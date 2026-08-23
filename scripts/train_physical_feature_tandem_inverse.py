#!/usr/bin/env python3
"""Train a traceable physical-feature tandem inverse model with NumPy.

The forward proxy learns geometry -> [Lp, Ls, Q, |K|]. The inverse network
then learns target physical features -> geometry through the frozen forward
proxy. By default, geometry outputs are projected into the per-dimension
envelope observed in the training split. Controlled comparisons can instead
bind a shared external declared-domain normalization and projection contract.
Either projection is an extrapolation guard, not a DRC certificate; generated
layouts still require the project's geometry checks and real EMX validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.model_splitting import (  # noqa: E402
    parse_optional_feature_bounds,
    split_physical_feature_indices,
)


DEFAULT_INPUT_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"
PROJECT_FEATURE_BOUNDS = {
    "lp": (0.5, 3.0),
    "ls": (0.5, 3.0),
    "q": (5.0, 25.0),
    "k": (0.0, 0.8),
}

TOPOLOGY_GEOMETRY_SEMANTICS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)
POWER_LINE_PORT_GROUND_OVERLAP_SEMANTICS = ("line_width_um",)
INVERSE_GEOMETRY_PROJECTION_MODES = (
    "independent_sigmoid",
    "hard_feasible_topology_v1",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    input_columns = [item.strip() for item in args.input_columns.split(",") if item.strip()]
    split_reference_columns = [
        item.strip()
        for item in (args.split_reference_columns or args.input_columns).split(",")
        if item.strip()
    ]
    geometry_columns = _resolve_geometry_columns(rows, args.geometry_columns, args.geometry_prefix)
    topology_column_contract = _topology_feasibility_column_contract(
        geometry_columns,
        enforce_power_line_port_ground_overlap=bool(
            args.enforce_power_line_port_ground_overlap
        ),
    )
    matrix = _build_matrix(rows, input_columns, geometry_columns, split_reference_columns)
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "usable_rows_meet_minimum": matrix["count"] >= int(args.min_training_rows),
        "input_is_physical_features_not_zin": bool(input_columns) and not any("zin" in item.lower() for item in input_columns),
        "split_reference_is_physical_features_not_zin": bool(split_reference_columns)
        and not any("zin" in item.lower() for item in split_reference_columns),
        "geometry_columns_present": bool(geometry_columns),
        "topology_feasibility_columns_present": (
            (
                float(args.topology_feasibility_weight) == 0.0
                and args.inverse_geometry_projection == "independent_sigmoid"
            )
            or topology_column_contract["available"]
        ),
    }
    ready = all(checks.values())

    summary_path = out_dir / "physical_feature_tandem_inverse_summary.json"
    history_path = out_dir / "physical_feature_tandem_inverse_history.csv"
    validation_predictions_path = out_dir / "physical_feature_tandem_inverse_validation_predictions.csv"
    predictions_path = out_dir / "physical_feature_tandem_inverse_test_predictions.csv"
    weights_path = out_dir / "physical_feature_tandem_inverse_weights.npz"

    if not ready:
        status = "WAITING_FOR_COMPLETE_DATA" if not checks["usable_rows_meet_minimum"] else "FAIL"
        summary = _base_summary(args, training_csv, out_dir, input_columns, geometry_columns, matrix["count"], checks)
        summary.update(
            {
                "overall_status": status,
                "execution_status": status,
                "quality_status": "NOT_RUN",
                "eligible_for_checkpoint_model_acceptance": False,
                "eligible_for_model_success_claim": False,
                "decision": "WAIT_FOR_REAL_PHYSICAL_FEATURE_ROWS" if status.startswith("WAITING") else "FIX_TANDEM_INPUT_CONTRACT",
                "history_csv": "",
                "validation_predictions_csv": "",
                "test_predictions_csv": "",
                "weights_npz": "",
            }
        )
        _write_strict_json(summary_path, summary)
        history_path.write_text("", encoding="utf-8")
        validation_predictions_path.write_text("", encoding="utf-8")
        predictions_path.write_text("", encoding="utf-8")
        print(f"overall_status={status}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    split, split_audit = _split_indices(matrix, args)
    evaluation_isolation = _evaluation_isolation_contract(
        matrix,
        split,
        evaluation_mode=str(args.evaluation_mode),
    )
    data = _normalize(
        matrix,
        split,
        float(args.normalization_floor),
        fixed_contract_path=args.fixed_normalization_contract_json,
        expected_fixed_contract_sha256=args.fixed_normalization_contract_sha256,
        input_columns=input_columns,
        geometry_columns=geometry_columns,
    )
    data["split_audit"] = split_audit
    data["topology_feasibility_contract"] = _configure_topology_feasibility(
        data["normalization"],
        geometry_columns,
        topology_column_contract,
        args,
    )
    data["inverse_geometry_projection_mode"] = str(
        args.inverse_geometry_projection
    )
    data["response_loss_contract"] = _configure_response_loss(
        data,
        input_columns,
        split_reference_columns,
        split_audit,
        args,
    )
    sampler_state, sampler_contract = _configure_training_batch_sampler(
        data,
        split_reference_columns,
        split_audit,
        args,
    )
    data["training_batch_sampler_state"] = sampler_state
    data["training_batch_sampler_contract"] = sampler_contract
    data["optimizer_budget_contract"] = _configure_optimizer_budget_contract(
        sampler_contract,
        args,
    )
    forward_hidden_widths = _resolve_hidden_widths(
        args.forward_hidden_widths,
        depth=int(args.forward_depth),
        width=int(args.forward_width),
    )
    inverse_hidden_widths = _resolve_hidden_widths(
        args.inverse_hidden_widths,
        depth=int(args.inverse_depth),
        width=int(args.inverse_width),
    )
    (
        forward_initial_weights,
        forward_initial_biases,
        forward_initialization_contract,
    ) = _configure_forward_initialization(
        args=args,
        input_columns=input_columns,
        geometry_columns=geometry_columns,
        forward_hidden_widths=forward_hidden_widths,
        target_normalization=data["normalization"],
    )
    data["forward_initialization_contract"] = forward_initialization_contract
    (
        inverse_initial_weights,
        inverse_initial_biases,
        inverse_initialization_contract,
    ) = _configure_inverse_initialization(
        args=args,
        input_columns=input_columns,
        geometry_columns=geometry_columns,
        inverse_hidden_widths=inverse_hidden_widths,
        target_normalization=data["normalization"],
    )
    data["inverse_initialization_contract"] = inverse_initialization_contract
    stage_checkpoint_root = out_dir / "stage_checkpoints"
    stage_checkpoint_contract: dict[str, Any] = {}
    stage_checkpoint_contract_record: dict[str, Any] = {}
    stage_checkpoint_records: dict[str, dict[str, Any]] = {}
    resumed_stage_names: list[str] = []
    if args.stage_checkpoint_mode == "resume_exact":
        stage_checkpoint_contract = _tandem_stage_checkpoint_contract(
            training_csv=training_csv,
            out_dir=out_dir,
            input_columns=input_columns,
            split_reference_columns=split_reference_columns,
            geometry_columns=geometry_columns,
            split_audit=split_audit,
            data=data,
            args=args,
        )
        stage_checkpoint_contract_record = _prepare_tandem_stage_checkpoint_contract(
            stage_checkpoint_root,
            stage_checkpoint_contract,
        )

    loaded_forward = (
        _load_tandem_stage_checkpoint(
            stage_checkpoint_root,
            stage="forward_proxy",
            contract_fingerprint=str(stage_checkpoint_contract["fingerprint_sha256"]),
            dependency_weights_sha256="",
            expected_input_dim=len(geometry_columns),
            expected_output_dim=len(input_columns),
        )
        if args.stage_checkpoint_mode == "resume_exact"
        else None
    )
    if loaded_forward is None:
        if forward_initial_weights is None or forward_initial_biases is None:
            rng_forward = np.random.default_rng(int(args.seed))
            forward_weights, forward_biases = _init_mlp(
                len(geometry_columns),
                len(input_columns),
                forward_hidden_widths,
                rng_forward,
            )
        else:
            forward_weights = [value.copy() for value in forward_initial_weights]
            forward_biases = [value.copy() for value in forward_initial_biases]
        if bool(args.freeze_transported_forward):
            forward_history, forward_best = _evaluate_frozen_forward(
                data,
                forward_weights,
                forward_biases,
            )
        else:
            forward_history, forward_best = _train_forward(data, forward_weights, forward_biases, args)
        forward_weights = forward_best["weights"]
        forward_biases = forward_best["biases"]
        if args.stage_checkpoint_mode == "resume_exact":
            stage_checkpoint_records["forward_proxy"] = _save_tandem_stage_checkpoint(
                stage_checkpoint_root,
                stage="forward_proxy",
                contract_fingerprint=str(stage_checkpoint_contract["fingerprint_sha256"]),
                dependency_weights_sha256="",
                history=forward_history,
                best=forward_best,
                expected_input_dim=len(geometry_columns),
                expected_output_dim=len(input_columns),
            )
    else:
        forward_history, forward_best, forward_record = loaded_forward
        forward_weights = forward_best["weights"]
        forward_biases = forward_best["biases"]
        stage_checkpoint_records["forward_proxy"] = forward_record
        resumed_stage_names.append("forward_proxy")

    forward_stage_weights_sha256 = str(
        (stage_checkpoint_records.get("forward_proxy") or {}).get("weights_sha256") or ""
    )
    loaded_inverse = (
        _load_tandem_stage_checkpoint(
            stage_checkpoint_root,
            stage="tandem_inverse",
            contract_fingerprint=str(stage_checkpoint_contract["fingerprint_sha256"]),
            dependency_weights_sha256=forward_stage_weights_sha256,
            expected_input_dim=len(input_columns),
            expected_output_dim=len(geometry_columns),
        )
        if args.stage_checkpoint_mode == "resume_exact"
        else None
    )
    if loaded_inverse is None:
        if inverse_initial_weights is None or inverse_initial_biases is None:
            rng_inverse = np.random.default_rng(int(args.seed) + 1)
            inverse_weights, inverse_biases = _init_mlp(
                len(input_columns),
                len(geometry_columns),
                inverse_hidden_widths,
                rng_inverse,
            )
        else:
            inverse_weights = [value.copy() for value in inverse_initial_weights]
            inverse_biases = [value.copy() for value in inverse_initial_biases]
        inverse_history, inverse_best = _train_inverse(
            data,
            inverse_weights,
            inverse_biases,
            forward_weights,
            forward_biases,
            args,
        )
        inverse_weights = inverse_best["weights"]
        inverse_biases = inverse_best["biases"]
        if args.stage_checkpoint_mode == "resume_exact":
            stage_checkpoint_records["tandem_inverse"] = _save_tandem_stage_checkpoint(
                stage_checkpoint_root,
                stage="tandem_inverse",
                contract_fingerprint=str(stage_checkpoint_contract["fingerprint_sha256"]),
                dependency_weights_sha256=forward_stage_weights_sha256,
                history=inverse_history,
                best=inverse_best,
                expected_input_dim=len(input_columns),
                expected_output_dim=len(geometry_columns),
            )
    else:
        inverse_history, inverse_best, inverse_record = loaded_inverse
        inverse_weights = inverse_best["weights"]
        inverse_biases = inverse_best["biases"]
        stage_checkpoint_records["tandem_inverse"] = inverse_record
        resumed_stage_names.append("tandem_inverse")
    _record_realized_training_sampler_budget(
        data["training_batch_sampler_contract"],
        forward_history,
        inverse_history,
    )
    _record_optimizer_budget_realization(
        data["optimizer_budget_contract"],
        data["training_batch_sampler_contract"]["realized_training_budget"],
    )

    validation_metrics, validation_prediction_rows = _evaluate(
        data,
        input_columns,
        geometry_columns,
        forward_weights,
        forward_biases,
        inverse_weights,
        inverse_biases,
        len(data["split"]["validation"]),
        args,
        evaluation_split="validation",
    )
    test_accessed = args.evaluation_mode == "validation_then_test"
    if test_accessed:
        metrics, prediction_rows = _evaluate(
            data,
            input_columns,
            geometry_columns,
            forward_weights,
            forward_biases,
            inverse_weights,
            inverse_biases,
            int(args.max_prediction_rows),
            args,
            evaluation_split="test",
        )
    else:
        metrics, prediction_rows = {}, []
    thresholds_configured = math.isfinite(float(args.max_forward_test_rmse)) and math.isfinite(
        float(args.max_tandem_response_test_rmse)
    )
    thresholds_pass = False
    if not test_accessed:
        status = "COMPLETE_REVIEW_REQUIRED"
    elif thresholds_configured:
        thresholds_pass = metrics["forward_proxy"]["test_normalized_rmse"] <= float(
            args.max_forward_test_rmse
        )
        if metrics["tandem_inverse"]["test_response_normalized_rmse"] > float(args.max_tandem_response_test_rmse):
            thresholds_pass = False
        status = "PASS" if thresholds_pass else "FAIL"
    else:
        status = "COMPLETE_REVIEW_REQUIRED"
    isolation_pass = evaluation_isolation.get("overall_status") == "PASS"
    if not isolation_pass:
        status = "FAIL"
    validation_ood_scope = (
        split_audit.get("split_mode") == "physical_cell_grouped"
        and split_audit.get("physical_cell_range_source") == "explicit"
        and int(split_audit.get("physical_cell_overlap_count") or 0) == 0
        and isolation_pass
    )
    formal_ood_scope = validation_ood_scope and test_accessed
    if not isolation_pass:
        quality_status = "FAIL"
    elif not test_accessed:
        quality_status = "REVIEW_REQUIRED_VALIDATION_ONLY"
    elif thresholds_configured and thresholds_pass:
        quality_status = "PASS"
    elif thresholds_configured:
        quality_status = "FAIL"
    else:
        quality_status = "REVIEW_REQUIRED_THRESHOLDS_NOT_CONFIGURED"
    checkpoint_acceptance_eligible = bool(quality_status == "PASS" and formal_ood_scope)

    history_rows = [
        {"stage": "forward_proxy", **item} for item in forward_history
    ] + [
        {"stage": "tandem_inverse", **item} for item in inverse_history
    ]
    _write_csv(history_path, history_rows)
    _write_csv(validation_predictions_path, validation_prediction_rows)
    _write_csv(predictions_path, prediction_rows)
    _save_weights(
        weights_path,
        forward_weights,
        forward_biases,
        inverse_weights,
        inverse_biases,
        data,
    )

    summary = _base_summary(args, training_csv, out_dir, input_columns, geometry_columns, matrix["count"], checks)
    summary.update(
        {
            "overall_status": status,
            "execution_status": "PASS",
            "quality_status": quality_status,
            "eligible_for_checkpoint_model_acceptance": checkpoint_acceptance_eligible,
            "eligible_for_model_success_claim": False,
            "evaluation_mode": str(args.evaluation_mode),
            "test_access_contract": {
                "test_access_event_count": 1 if test_accessed else 0,
                "test_access_timing": (
                    "after_training_and_validation_checkpoint_selection"
                    if test_accessed
                    else "not_accessed"
                ),
                "test_used_for_training": False,
                "test_used_for_early_stopping": False,
                "test_used_for_model_or_hyperparameter_selection": False,
                "test_used_for_acceptance_threshold_tuning": False,
                "test_evaluator_called": bool(test_accessed),
            },
            "evaluation_scope": {
                "validation_explicit_physical_cell_ood": validation_ood_scope,
                "formal_explicit_physical_cell_ood": formal_ood_scope,
                "split_mode": split_audit.get("split_mode"),
                "physical_cell_range_source": split_audit.get("physical_cell_range_source"),
                "boundary": (
                    "Checkpoint acceptance requires explicit non-overlapping physical-cell OOD and finite preregistered thresholds. "
                    "Final model-success claims additionally require real EMX closure and sampled HFSS comparison."
                ),
            },
            "evaluation_isolation": evaluation_isolation,
            "decision": (
                "CHECKPOINT_ABLATION_ACCEPTABLE_REAL_EMX_VERIFY_REQUIRED"
                if checkpoint_acceptance_eligible
                else (
                    (
                        "REVIEW_VALIDATION_ONLY_SAMPLER_ABLATION_WITHOUT_TEST_ACCESS"
                        if not test_accessed
                        else "COMPARE_WITH_DIRECT_BASELINE_AND_EMX_VERIFY"
                    )
                    if status in {"PASS", "COMPLETE_REVIEW_REQUIRED"}
                    else "DO_NOT_USE_FIX_TANDEM_MODEL"
                )
            ),
            "method": {
                "forward_proxy": "geometry_to_Lp_Ls_Q_absK",
                "forward_proxy_initialization": data["forward_initialization_contract"],
                "inverse": "physical_features_to_geometry_through_frozen_forward_proxy",
                "inverse_initialization": data["inverse_initialization_contract"],
                "inverse_loss": (
                    _inverse_loss_name(args)
                ),
                "geometry_anchor_weight": float(args.geometry_anchor_weight),
                "geometry_label_used_in_inverse_objective": float(args.geometry_anchor_weight) > 0.0,
                "topology_feasibility_weight": float(args.topology_feasibility_weight),
                "topology_feasibility_is_label_free": True,
                "topology_feasibility_contract": data["topology_feasibility_contract"],
                "geometry_output_constraint": str(args.inverse_geometry_projection),
                "geometry_output_constraint_is_single_pass": True,
                "geometry_output_constraint_is_posthoc_repair": False,
                "inverse_checkpoint_selection": str(args.inverse_checkpoint_selection),
                "inverse_checkpoint_selection_uses_validation_only": True,
                "inverse_checkpoint_exact_relative_error_threshold": float(
                    args.inverse_checkpoint_exact_relative_error_threshold
                ),
                "local_refinement": (
                    f"multi_start_{args.local_refinement_optimizer}_on_frozen_forward"
                    if int(args.local_refinement_steps) > 0
                    else "disabled"
                ),
                "local_refinement_lr_schedule": args.local_refinement_lr_schedule,
                "local_refinement_final_lr_fraction": float(
                    args.local_refinement_final_lr_fraction
                ),
                "robustness_evaluation": "relative_Gaussian_input_noise_with_training_envelope_clipping",
                "split_reference_columns": split_reference_columns,
                "response_loss_family": args.response_loss_family,
                "response_loss_scaling": args.response_loss_scaling,
                "response_weight_schedule": args.response_weight_schedule,
                "response_schedule_domain": args.response_schedule_domain,
                "response_schedule_warmup_optimizer_updates": (
                    args.response_warmup_optimizer_updates
                ),
                "response_schedule_ramp_optimizer_updates": (
                    args.response_ramp_optimizer_updates
                ),
                "training_batch_sampler": args.training_batch_sampler,
                "exact_update_batch_mode": args.exact_update_batch_mode,
            },
            "normalization_contract": data["normalization_contract"],
            "response_loss_contract": data["response_loss_contract"],
            "training_batch_sampler_contract": data["training_batch_sampler_contract"],
            "optimizer_budget_contract": data["optimizer_budget_contract"],
            "split_audit": split_audit,
            "validation_metrics": validation_metrics,
            "metrics": metrics,
            "acceptance_thresholds": {
                "configured": thresholds_configured,
                "max_forward_test_normalized_rmse": None
                if not math.isfinite(float(args.max_forward_test_rmse))
                else float(args.max_forward_test_rmse),
                "max_tandem_response_test_normalized_rmse": None
                if not math.isfinite(float(args.max_tandem_response_test_rmse))
                else float(args.max_tandem_response_test_rmse),
                "boundary": (
                    "Test thresholds are disabled in validation-only mode. PASS is emitted only in "
                    "validation-then-test mode when both thresholds are explicitly finite and satisfied."
                ),
                "checkpoint_acceptance_additional_requirement": "explicit_nonoverlapping_physical_cell_OOD",
            },
            "best_epochs": {
                "forward_proxy": int(forward_best["epoch"]),
                "tandem_inverse": int(inverse_best["epoch"]),
            },
            "best_optimizer_updates": {
                "forward_proxy": int(forward_best.get("optimizer_updates") or 0),
                "tandem_inverse": int(inverse_best.get("optimizer_updates") or 0),
            },
            "stage_checkpoint_resume": {
                "mode": str(args.stage_checkpoint_mode),
                "enabled": args.stage_checkpoint_mode == "resume_exact",
                "checkpoint_root": (
                    str(stage_checkpoint_root)
                    if args.stage_checkpoint_mode == "resume_exact"
                    else ""
                ),
                "contract_fingerprint_sha256": str(
                    stage_checkpoint_contract.get("fingerprint_sha256") or ""
                ),
                "contract_path": str(stage_checkpoint_contract_record.get("path") or ""),
                "contract_file_sha256": str(
                    stage_checkpoint_contract_record.get("sha256") or ""
                ),
                "resumed_stage_names": resumed_stage_names,
                "resumed_stage_count": len(resumed_stage_names),
                "stage_records": stage_checkpoint_records,
                "scientific_boundary": (
                    "Exact stage reuse is allowed only when the real-data, trainer, split, normalization, sampler, "
                    "optimizer-budget, and argument contract fingerprint matches. A resumed stage does not add "
                    "training data or constitute model-quality evidence."
                ),
            },
            "history_csv": str(history_path),
            "history_csv_sha256": _sha256_file(history_path),
            "validation_predictions_csv": str(validation_predictions_path),
            "validation_predictions_csv_sha256": _sha256_file(validation_predictions_path),
            "test_predictions_csv": str(predictions_path),
            "test_predictions_csv_sha256": _sha256_file(predictions_path),
            "weights_npz": str(weights_path),
            "weights_npz_sha256": _sha256_file(weights_path),
            "limitations": [
                "The frozen proxy reconstructs four center-frequency physical features, not the complete broadband S-parameter tensor.",
                "The output projection and differentiable topology penalty cover only encoded envelope/topology constraints and are not a DRC certificate.",
                "Surrogate response consistency is not final validation; selected outputs require DRC, real EMX, and sampled HFSS comparison.",
                "A single inverse output does not represent the complete one-to-many design distribution; a later conditional generative model should produce top-k alternatives.",
                "Local refinement, when enabled, selects candidates only through the frozen proxy and therefore cannot be counted as real-EM improvement without new EMX labels.",
            ]
            + (
                [
                    "The joint 4D physical-cell BNI loss is a project-specific extension of bin-based numerical integration; it remains an ablation until it improves preregistered held-out-cell and tail metrics against MSE."
                ]
                if args.response_loss_family == "balanced_mse_bni"
                else []
            )
            + (
                [
                    "Physical relative MSE aligns training with percentage-error acceptance, but fixed denominator floors remain a preregistered modeling choice and require held-out-cell plus real-EM validation."
                ]
                if args.response_loss_family == "relative_mse"
                else []
            )
            + (
                [
                    "The joint-cell-balanced sampler only resamples real train rows with replacement. It is an ablation until equal-budget held-out-cell, tail, DRC, and real-EM evidence improve over row-uniform batches."
                ]
                if args.training_batch_sampler == "joint_cell_balanced"
                else []
            )
            + (
                [
                    "Transported source-domain weights are only an initialization ablation. They do not transfer labels, alter the held-out split, or establish a current-foundry accuracy claim without current-foundry real-EM evaluation."
                ]
                if (
                    args.forward_initialization_mode == "transported_source_finetune"
                    or args.inverse_initialization_mode
                    == "transported_source_finetune"
                )
                else []
            ),
        }
    )
    _write_strict_json(summary_path, summary)
    print(f"overall_status={status}")
    if test_accessed:
        print(f"forward_test_normalized_rmse={metrics['forward_proxy']['test_normalized_rmse']}")
        print(f"tandem_test_response_normalized_rmse={metrics['tandem_inverse']['test_response_normalized_rmse']}")
        print(
            "tandem_test_response_range_normalized_rmse="
            f"{metrics['tandem_inverse']['test_response_range_normalized_rmse']}"
        )
    else:
        print("test_access_event_count=0")
        print(
            "tandem_validation_response_range_normalized_rmse="
            f"{validation_metrics['tandem_inverse']['validation_response_range_normalized_rmse']}"
        )
    print(f"summary={summary_path}")
    return 0 if status in {"PASS", "COMPLETE_REVIEW_REQUIRED"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", default=DEFAULT_INPUT_COLUMNS)
    parser.add_argument(
        "--split-reference-columns",
        help="Optional physical columns used only to create the shared OOD split. Enables fair Q versus Qp/Qs ablations.",
    )
    parser.add_argument("--geometry-columns")
    parser.add_argument("--geometry-prefix", default="geom__")
    parser.add_argument("--min-training-rows", type=int, default=100_000)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.10)
    parser.add_argument(
        "--split-mode",
        choices=("random", "physical_cell_grouped", "fixed_common_holdout_manifest"),
        default="random",
        help=(
            "Random row split, a stricter holdout of complete physical-feature cells, or an external shared "
            "validation/test identity manifest. The manifest mode assigns every loaded identity not listed in "
            "the common holdout to training."
        ),
    )
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument(
        "--physical-cell-lower",
        help="Optional comma-separated lower bounds for grouped physical cells, in input-column order.",
    )
    parser.add_argument(
        "--physical-cell-upper",
        help="Optional comma-separated upper bounds for grouped physical cells, in input-column order.",
    )
    parser.add_argument(
        "--fixed-common-holdout-manifest-json",
        help=(
            "External fixed validation/test manifest keyed by unique geometry identity. Required only with "
            "--split-mode fixed_common_holdout_manifest."
        ),
    )
    parser.add_argument(
        "--fixed-common-holdout-manifest-sha256",
        help="Required lowercase SHA-256 for --fixed-common-holdout-manifest-json.",
    )
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument(
        "--split-seed",
        type=int,
        help="Independent seed for train/validation/test cells. Defaults to --seed for compatibility.",
    )
    parser.add_argument("--forward-depth", type=int, default=3)
    parser.add_argument("--forward-width", type=int, default=256)
    parser.add_argument(
        "--forward-hidden-widths",
        help=(
            "Optional comma-separated hidden widths, for example 200,100,50. "
            "When omitted, --forward-depth copies --forward-width at every hidden layer."
        ),
    )
    parser.add_argument(
        "--forward-initialization-mode",
        choices=("random", "transported_source_finetune"),
        default="random",
        help=(
            "Randomly initialize the forward proxy, or transport a source-domain forward checkpoint into "
            "the current train-split normalization before fine-tuning. Transport requires exact source "
            "weights/summary paths and expected SHA-256 values."
        ),
    )
    parser.add_argument("--forward-initial-weights")
    parser.add_argument("--forward-initial-weights-sha256")
    parser.add_argument("--forward-initial-summary")
    parser.add_argument("--forward-initial-summary-sha256")
    parser.add_argument(
        "--freeze-transported-forward",
        action="store_true",
        help=(
            "Keep an exactly transported forward checkpoint fixed and train only the inverse network. "
            "This requires transported_source_finetune initialization, zero forward optimizer updates, "
            "and a positive exact inverse optimizer-update budget."
        ),
    )
    parser.add_argument("--inverse-depth", type=int, default=3)
    parser.add_argument("--inverse-width", type=int, default=256)
    parser.add_argument(
        "--inverse-hidden-widths",
        help=(
            "Optional comma-separated hidden widths, for example 200,100,50. "
            "When omitted, --inverse-depth copies --inverse-width at every hidden layer."
        ),
    )
    parser.add_argument(
        "--inverse-geometry-projection",
        choices=INVERSE_GEOMETRY_PROJECTION_MODES,
        default="independent_sigmoid",
        help=(
            "Project inverse outputs independently into the observed geometry envelope, or use the "
            "single-pass differentiable current-topology decoder that also enforces all encoded "
            "terminal, feed-support, and signal-port ground-reachability inequalities by construction."
        ),
    )
    parser.add_argument(
        "--inverse-checkpoint-selection",
        choices=(
            "training_objective",
            "worst_dimension_relative_mae",
            "strict_row_failure_rate",
            "physical_cell_tail_strict_pass_rate",
        ),
        default="training_objective",
        help=(
            "Select inverse checkpoints with the historical aggregate training objective, or minimize the "
            "worst validation-set semantic relative MAE across physical response dimensions, or minimize the "
            "fraction of validation rows that violate any exact-response relative-error threshold or the Q "
            "floor. The physical-cell-tail mode first maximizes the lower-decile and worst held-out-cell pass "
            "rates, then the equal-cell and row pass rates. Acceptance-aligned modes require the relative-MSE, "
            "minimum-Q, hard-feasible one-shot contract."
        ),
    )
    parser.add_argument(
        "--inverse-checkpoint-exact-relative-error-threshold",
        type=float,
        default=0.10,
        help=(
            "Exact-response relative-error ceiling used only by strict_row_failure_rate checkpoint selection. "
            "Q remains a one-sided minimum requirement."
        ),
    )
    parser.add_argument(
        "--inverse-initialization-mode",
        choices=("random", "transported_source_finetune"),
        default="random",
        help=(
            "Randomly initialize the inverse network, or transport a source inverse checkpoint into the "
            "current response/geometry normalization before fine-tuning on current-foundry real EMX."
        ),
    )
    parser.add_argument("--inverse-initial-weights")
    parser.add_argument("--inverse-initial-weights-sha256")
    parser.add_argument("--inverse-initial-summary")
    parser.add_argument("--inverse-initial-summary-sha256")
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument(
        "--training-batch-sampler",
        choices=("row_uniform", "joint_cell_balanced"),
        default="row_uniform",
        help=(
            "Draw each train row once per epoch in random order, or draw the same number of real train rows "
            "with replacement while balancing occupied joint physical cells. The latter requires an explicit "
            "physical-cell grouped split and never creates synthetic labels."
        ),
    )
    parser.add_argument(
        "--exact-update-batch-mode",
        choices=("legacy_epoch_partial", "continuous_permutation_full_batch"),
        default="legacy_epoch_partial",
        help=(
            "Batch construction used only with exact optimizer-update budgets. The legacy mode emits one "
            "possibly short final batch per dataset permutation. The continuous-permutation mode concatenates "
            "successive random permutations and makes every optimizer update contain exactly --batch-size real "
            "train rows, so sample-size arms have identical row-draw and batch-size budgets."
        ),
    )
    parser.add_argument("--forward-epochs", type=int, default=160)
    parser.add_argument("--inverse-epochs", type=int, default=180)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument(
        "--forward-max-optimizer-updates",
        type=int,
        default=0,
        help=(
            "Optional exact forward optimizer-update budget. A positive value disables patience-based "
            "termination and cycles real training rows until this many updates have completed."
        ),
    )
    parser.add_argument(
        "--inverse-max-optimizer-updates",
        type=int,
        default=0,
        help=(
            "Optional exact inverse optimizer-update budget. A positive value disables patience-based "
            "termination and cycles real training rows until this many updates have completed."
        ),
    )
    parser.add_argument(
        "--validation-every-optimizer-updates",
        type=int,
        default=0,
        help=(
            "Validation interval for exact-update training. It must be positive when exact update budgets "
            "are enabled so all sample-size arms can use the same checkpoint-selection cadence."
        ),
    )
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument(
        "--training-learning-rate-schedule",
        choices=("constant", "cosine_decay"),
        default="constant",
        help=(
            "Optimizer learning-rate schedule shared by the trainable forward and inverse stages. "
            "Non-constant schedules require exact optimizer-update budgets so data-size and budget "
            "comparisons remain reproducible."
        ),
    )
    parser.add_argument(
        "--training-final-learning-rate-fraction",
        type=float,
        default=0.1,
        help=(
            "Final/base learning-rate ratio for cosine-decay training. The constant schedule records "
            "this value but does not apply it."
        ),
    )
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--response-weight", type=float, default=1.0)
    parser.add_argument("--geometry-anchor-weight", type=float, default=0.01)
    parser.add_argument(
        "--topology-feasibility-weight",
        type=float,
        default=0.0,
        help=(
            "Weight for a label-free differentiable penalty on terminal-span and feed-support constraints. "
            "Use the production hard geometry audit after training regardless of this value."
        ),
    )
    parser.add_argument(
        "--enforce-power-line-port-ground-overlap",
        action="store_true",
        help=(
            "Add exact label-free feed-extension constraints for the current power-line topology so all "
            "signal ports can protrude into the shield ground by the declared overlap. This requires the "
            "shared line-width geometry column."
        ),
    )
    parser.add_argument("--power-line-bar-offset-um", type=float, default=12.0)
    parser.add_argument(
        "--power-line-shield-opening-clearance-um",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--power-line-port-ground-overlap-um",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--power-line-feed-training-safety-margin-um",
        type=float,
        default=0.0,
        help=(
            "Optional conservative margin added only to the differentiable minimum feed-length constraint. "
            "The generated geometry is still audited against the unmodified foundry/export contract."
        ),
    )
    parser.add_argument("--response-ramp-fraction", type=float, default=0.25)
    parser.add_argument(
        "--response-loss-scaling",
        choices=("standardized", "declared_range"),
        default="declared_range",
        help="Balance response dimensions in standardized space or by the declared physical Lp/Ls/Q/K spans.",
    )
    parser.add_argument(
        "--response-loss-family",
        choices=("mse", "relative_mse", "balanced_mse_bni"),
        default="mse",
        help=(
            "Use feature-balanced MSE, physical relative-error MSE, or an explicit joint-cell "
            "Balanced-MSE BNI ablation. BNI requires a grouped physical-cell split with declared "
            "bounds and an explicit temperature."
        ),
    )
    parser.add_argument(
        "--relative-error-floors",
        default="0.5,0.5,5.0,0.05",
        help=(
            "Comma-separated positive physical denominator floors in input-column order for "
            "--response-loss-family relative_mse. The default matches Lp/Ls/Q/|K| project semantics."
        ),
    )
    parser.add_argument(
        "--response-semantic-loss-weights",
        default="",
        help=(
            "Optional comma-separated positive fixed loss weights in input-column order. "
            "Weights are mean-normalized to one; an empty value preserves equal weighting."
        ),
    )
    parser.add_argument(
        "--q-target-semantics",
        choices=("exact", "minimum"),
        default="exact",
        help=(
            "Treat Q as an exact regression target, or as a minimum engineering requirement in the inverse "
            "response-consistency loss. The forward surrogate always learns the exact simulated Q."
        ),
    )
    parser.add_argument(
        "--q-minimum-margin-physical",
        type=float,
        default=0.0,
        help=(
            "Fixed global physical-unit guardband added to every inverse-training Q minimum. "
            "This is a training-time contract, not a target-specific or post-hoc adjustment."
        ),
    )
    parser.add_argument(
        "--balanced-mse-temperature",
        type=float,
        help=(
            "Positive tau=2*sigma^2 in feature-balanced standardized-distance units. "
            "Required only for --response-loss-family balanced_mse_bni; select it on validation data only."
        ),
    )
    parser.add_argument(
        "--response-weight-schedule",
        choices=("linear_ramp", "warmup_ramp_adaptive_ema"),
        default="warmup_ramp_adaptive_ema",
        help="PICC response-loss schedule. The adaptive option follows warm-up, ramp-up, then EMA balancing.",
    )
    parser.add_argument(
        "--response-schedule-domain",
        choices=("epoch", "optimizer_update"),
        default="epoch",
        help=(
            "Advance PICC warm-up/ramp by epochs or optimizer updates. Exact-update comparisons require "
            "optimizer_update so the schedule is identical across dataset sizes."
        ),
    )
    parser.add_argument("--response-warmup-fraction", type=float, default=0.05)
    parser.add_argument(
        "--response-warmup-optimizer-updates",
        type=int,
        default=None,
        help=(
            "Optional absolute PICC warm-up length for optimizer-update scheduling. "
            "Specify this together with --response-ramp-optimizer-updates to compare "
            "different optimizer budgets without stretching the loss schedule."
        ),
    )
    parser.add_argument(
        "--response-ramp-optimizer-updates",
        type=int,
        default=None,
        help=(
            "Optional absolute PICC ramp length for optimizer-update scheduling. "
            "Specify this together with --response-warmup-optimizer-updates."
        ),
    )
    parser.add_argument("--response-adaptive-ema-decay", type=float, default=0.95)
    parser.add_argument("--response-adaptive-min-multiplier", type=float, default=0.25)
    parser.add_argument("--response-adaptive-max-multiplier", type=float, default=4.0)
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument(
        "--fixed-normalization-contract-json",
        help=(
            "Optional external declared-domain normalization contract shared across comparison arms. The JSON "
            "must bind exact input/geometry column order plus finite lower/upper vectors; midpoint and half-range "
            "are used for scaling and the declared geometry envelope is used by the inverse projection."
        ),
    )
    parser.add_argument(
        "--fixed-normalization-contract-sha256",
        help="Required lowercase SHA-256 for --fixed-normalization-contract-json.",
    )
    parser.add_argument("--max-forward-test-rmse", type=float, default=math.inf)
    parser.add_argument("--max-tandem-response-test-rmse", type=float, default=math.inf)
    parser.add_argument(
        "--evaluation-mode",
        choices=("validation_only", "validation_then_test"),
        default="validation_then_test",
        help=(
            "Keep the test split completely sealed for model selection, or evaluate it once after training. "
            "Validation-only mode emits an empty test-prediction artifact and cannot pass checkpoint gates."
        ),
    )
    parser.add_argument(
        "--local-refinement-steps",
        type=int,
        default=0,
        help=(
            "Optional projected gradient steps on frozen-forward response error after inverse prediction. "
            "This is a proxy-only inference ablation and does not replace real EMX closure."
        ),
    )
    parser.add_argument(
        "--local-refinement-starts",
        type=int,
        default=1,
        help="Number of projected refinement starts per target; the unrefined inverse output is always retained.",
    )
    parser.add_argument("--local-refinement-learning-rate", type=float, default=0.05)
    parser.add_argument(
        "--local-refinement-optimizer",
        choices=("projected_gradient", "projected_adam"),
        default="projected_gradient",
        help="Optimizer used only for frozen-forward local geometry refinement.",
    )
    parser.add_argument(
        "--local-refinement-lr-schedule",
        choices=("constant", "cosine_decay"),
        default="constant",
    )
    parser.add_argument(
        "--local-refinement-final-lr-fraction",
        type=float,
        default=0.1,
        help="Final/base learning-rate ratio for cosine_decay; ignored for constant.",
    )
    parser.add_argument(
        "--local-refinement-jitter",
        type=float,
        default=0.05,
        help="Gaussian start jitter as a fraction of each normalized geometry-envelope span.",
    )
    parser.add_argument("--local-refinement-seed", type=int, default=20260712)
    parser.add_argument("--max-prediction-rows", type=int, default=2000)
    parser.add_argument(
        "--robustness-noise-levels",
        default="0.01,0.03,0.05,0.10",
        help="Comma-separated relative Gaussian input-noise standard deviations used only for post-training audit.",
    )
    parser.add_argument("--robustness-repeats", type=int, default=3)
    parser.add_argument("--robustness-max-rows", type=int, default=4096)
    parser.add_argument("--robustness-seed", type=int, default=20260711)
    parser.add_argument(
        "--stage-checkpoint-mode",
        choices=("off", "resume_exact"),
        default="off",
        help=(
            "Persist and reuse completed forward/inverse training stages only under an exact data/code/split/"
            "normalization/sampler/optimizer contract. The default keeps legacy behavior unchanged."
        ),
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.forward_epochs) < 1 or int(args.inverse_epochs) < 1:
        parser.error("forward/inverse epochs must both be positive")
    if int(args.forward_depth) < 0 or int(args.inverse_depth) < 0:
        parser.error("forward/inverse depth must be nonnegative")
    if int(args.forward_width) < 1 or int(args.inverse_width) < 1:
        parser.error("forward/inverse width must be positive")
    for option_name, specification, depth, width in (
        ("--forward-hidden-widths", args.forward_hidden_widths, args.forward_depth, args.forward_width),
        ("--inverse-hidden-widths", args.inverse_hidden_widths, args.inverse_depth, args.inverse_width),
    ):
        try:
            _resolve_hidden_widths(specification, depth=int(depth), width=int(width))
        except ValueError as exc:
            parser.error(f"{option_name}: {exc}")
    initialization_values = {
        "--forward-initial-weights": args.forward_initial_weights,
        "--forward-initial-weights-sha256": args.forward_initial_weights_sha256,
        "--forward-initial-summary": args.forward_initial_summary,
        "--forward-initial-summary-sha256": args.forward_initial_summary_sha256,
    }
    if args.forward_initialization_mode == "transported_source_finetune":
        missing = [name for name, value in initialization_values.items() if not str(value or "").strip()]
        if missing:
            parser.error(
                "transported_source_finetune requires " + ", ".join(missing)
            )
        for name in (
            "--forward-initial-weights-sha256",
            "--forward-initial-summary-sha256",
        ):
            value = str(initialization_values[name]).strip().lower()
            if not _is_sha256(value):
                parser.error(f"{name} must be a lowercase SHA-256 digest")
    elif any(str(value or "").strip() for value in initialization_values.values()):
        parser.error(
            "forward initialization artifacts are only valid with "
            "--forward-initialization-mode transported_source_finetune"
        )
    if bool(args.freeze_transported_forward) and args.forward_initialization_mode != "transported_source_finetune":
        parser.error(
            "--freeze-transported-forward requires "
            "--forward-initialization-mode transported_source_finetune"
        )
    inverse_initialization_values = {
        "--inverse-initial-weights": args.inverse_initial_weights,
        "--inverse-initial-weights-sha256": args.inverse_initial_weights_sha256,
        "--inverse-initial-summary": args.inverse_initial_summary,
        "--inverse-initial-summary-sha256": args.inverse_initial_summary_sha256,
    }
    if args.inverse_initialization_mode == "transported_source_finetune":
        missing = [
            name
            for name, value in inverse_initialization_values.items()
            if not str(value or "").strip()
        ]
        if missing:
            parser.error(
                "transported inverse source initialization requires "
                + ", ".join(missing)
            )
        for name in (
            "--inverse-initial-weights-sha256",
            "--inverse-initial-summary-sha256",
        ):
            value = str(inverse_initialization_values[name]).strip().lower()
            if not _is_sha256(value):
                parser.error(f"{name} must be a lowercase SHA-256 digest")
    elif any(
        str(value or "").strip() for value in inverse_initialization_values.values()
    ):
        parser.error(
            "inverse initialization artifacts are only valid with "
            "--inverse-initialization-mode transported_source_finetune"
        )
    if int(args.patience) < 1:
        parser.error("--patience must be positive")
    if int(args.batch_size) < 1:
        parser.error("--batch-size must be positive")
    fixed_holdout_values = (
        args.fixed_common_holdout_manifest_json,
        args.fixed_common_holdout_manifest_sha256,
    )
    if args.split_mode == "fixed_common_holdout_manifest":
        if not all(str(value or "").strip() for value in fixed_holdout_values):
            parser.error(
                "fixed common holdout splitting requires both --fixed-common-holdout-manifest-json and "
                "--fixed-common-holdout-manifest-sha256"
            )
        if not _is_sha256(str(args.fixed_common_holdout_manifest_sha256).strip().lower()):
            parser.error("--fixed-common-holdout-manifest-sha256 must be a lowercase SHA-256 digest")
    elif any(str(value or "").strip() for value in fixed_holdout_values):
        parser.error(
            "fixed common holdout manifest arguments require --split-mode fixed_common_holdout_manifest"
        )
    if args.evaluation_mode == "validation_only" and any(
        math.isfinite(float(value))
        for value in (
            args.max_forward_test_rmse,
            args.max_tandem_response_test_rmse,
        )
    ):
        parser.error("validation-only evaluation cannot configure test thresholds")
    update_budgets = (
        int(args.forward_max_optimizer_updates),
        int(args.inverse_max_optimizer_updates),
    )
    if any(value < 0 for value in update_budgets):
        parser.error("optimizer-update budgets must be nonnegative")
    freeze_forward = bool(args.freeze_transported_forward)
    exact_update_mode = any(value > 0 for value in update_budgets)
    if freeze_forward:
        if int(args.forward_max_optimizer_updates) != 0:
            parser.error("a frozen transported forward requires zero forward optimizer updates")
        if int(args.inverse_max_optimizer_updates) < 1:
            parser.error("a frozen transported forward requires a positive exact inverse update budget")
        exact_update_mode = True
    elif exact_update_mode and not all(value > 0 for value in update_budgets):
        parser.error("forward and inverse exact optimizer-update budgets must be enabled together")
    if int(args.validation_every_optimizer_updates) < 0:
        parser.error("--validation-every-optimizer-updates must be nonnegative")
    if exact_update_mode and int(args.validation_every_optimizer_updates) < 1:
        parser.error("exact optimizer-update budgets require a positive validation update interval")
    if exact_update_mode and args.response_schedule_domain != "optimizer_update":
        parser.error("exact optimizer-update budgets require --response-schedule-domain optimizer_update")
    if not exact_update_mode and args.response_schedule_domain != "epoch":
        parser.error("optimizer_update response scheduling requires exact optimizer-update budgets")
    if args.exact_update_batch_mode == "continuous_permutation_full_batch":
        if not exact_update_mode:
            parser.error(
                "--exact-update-batch-mode continuous_permutation_full_batch requires exact optimizer-update budgets"
            )
        if args.training_batch_sampler != "row_uniform":
            parser.error(
                "continuous-permutation full batches currently require --training-batch-sampler row_uniform"
            )
    fixed_normalization_values = (
        args.fixed_normalization_contract_json,
        args.fixed_normalization_contract_sha256,
    )
    if any(str(value or "").strip() for value in fixed_normalization_values):
        if not all(str(value or "").strip() for value in fixed_normalization_values):
            parser.error(
                "fixed normalization requires both --fixed-normalization-contract-json and "
                "--fixed-normalization-contract-sha256"
            )
        if not _is_sha256(str(args.fixed_normalization_contract_sha256).strip().lower()):
            parser.error("--fixed-normalization-contract-sha256 must be a lowercase SHA-256 digest")
    if not math.isfinite(float(args.learning_rate)) or float(args.learning_rate) <= 0.0:
        parser.error("--learning-rate must be finite and positive")
    if not math.isfinite(float(args.training_final_learning_rate_fraction)) or not (
        0.0 < float(args.training_final_learning_rate_fraction) <= 1.0
    ):
        parser.error(
            "--training-final-learning-rate-fraction must be finite and in (0, 1]"
        )
    if args.training_learning_rate_schedule != "constant" and not exact_update_mode:
        parser.error(
            "non-constant training learning-rate schedules require exact optimizer-update budgets"
        )
    if not 0.0 <= float(args.response_ramp_fraction) <= 1.0:
        parser.error("--response-ramp-fraction must be in [0, 1]")
    if not math.isfinite(float(args.geometry_anchor_weight)) or float(args.geometry_anchor_weight) < 0.0:
        parser.error("--geometry-anchor-weight must be finite and nonnegative")
    if not math.isfinite(float(args.topology_feasibility_weight)) or float(args.topology_feasibility_weight) < 0.0:
        parser.error("--topology-feasibility-weight must be finite and nonnegative")
    if bool(args.enforce_power_line_port_ground_overlap) and float(args.topology_feasibility_weight) <= 0.0:
        parser.error(
            "--enforce-power-line-port-ground-overlap requires a positive "
            "--topology-feasibility-weight"
        )
    if args.inverse_geometry_projection == "hard_feasible_topology_v1":
        if not bool(args.enforce_power_line_port_ground_overlap):
            parser.error(
                "--inverse-geometry-projection hard_feasible_topology_v1 requires "
                "--enforce-power-line-port-ground-overlap"
            )
        if int(args.local_refinement_steps) > 0:
            parser.error(
                "--inverse-geometry-projection hard_feasible_topology_v1 requires "
                "--local-refinement-steps 0 so inference remains one-shot and feasible by construction"
            )
    for option, value in (
        ("--power-line-bar-offset-um", args.power_line_bar_offset_um),
        (
            "--power-line-shield-opening-clearance-um",
            args.power_line_shield_opening_clearance_um,
        ),
        ("--power-line-port-ground-overlap-um", args.power_line_port_ground_overlap_um),
        (
            "--power-line-feed-training-safety-margin-um",
            args.power_line_feed_training_safety_margin_um,
        ),
    ):
        if not math.isfinite(float(value)) or float(value) < 0.0:
            parser.error(f"{option} must be finite and nonnegative")
    if not 0.0 <= float(args.response_warmup_fraction) < 1.0:
        parser.error("--response-warmup-fraction must be in [0, 1)")
    absolute_schedule_values = (
        args.response_warmup_optimizer_updates,
        args.response_ramp_optimizer_updates,
    )
    absolute_schedule_enabled = any(value is not None for value in absolute_schedule_values)
    if absolute_schedule_enabled:
        if not all(value is not None for value in absolute_schedule_values):
            parser.error(
                "absolute response scheduling requires both "
                "--response-warmup-optimizer-updates and --response-ramp-optimizer-updates"
            )
        if args.response_schedule_domain != "optimizer_update":
            parser.error("absolute response scheduling requires --response-schedule-domain optimizer_update")
        if any(int(value) < 0 for value in absolute_schedule_values):
            parser.error("absolute response warm-up and ramp optimizer updates must be nonnegative")
        if (
            args.response_weight_schedule == "warmup_ramp_adaptive_ema"
            and sum(int(value) for value in absolute_schedule_values)
            >= int(args.inverse_max_optimizer_updates)
        ):
            parser.error(
                "absolute adaptive PICC warm-up plus ramp updates must be less than "
                "the inverse optimizer-update budget"
            )
        effective_warmup_units = int(args.response_warmup_optimizer_updates)
    else:
        if args.response_weight_schedule == "warmup_ramp_adaptive_ema" and (
            float(args.response_warmup_fraction) + float(args.response_ramp_fraction) >= 1.0
        ):
            parser.error("adaptive PICC warm-up plus ramp fractions must be less than 1")
        effective_warmup_units = float(args.response_warmup_fraction)
    if float(args.geometry_anchor_weight) == 0.0 and effective_warmup_units > 0:
        parser.error("response warm-up must be 0 when --geometry-anchor-weight is 0")
    if not 0.0 <= float(args.response_adaptive_ema_decay) < 1.0:
        parser.error("--response-adaptive-ema-decay must be in [0, 1)")
    if not 0.0 < float(args.response_adaptive_min_multiplier) <= float(args.response_adaptive_max_multiplier):
        parser.error("adaptive response multipliers must satisfy 0 < min <= max")
    if args.response_loss_family == "balanced_mse_bni":
        if args.balanced_mse_temperature is None:
            parser.error("--balanced-mse-temperature is required for balanced_mse_bni")
        if not math.isfinite(float(args.balanced_mse_temperature)) or float(args.balanced_mse_temperature) <= 0.0:
            parser.error("--balanced-mse-temperature must be finite and positive")
    elif args.balanced_mse_temperature is not None:
        parser.error("--balanced-mse-temperature is only valid with balanced_mse_bni")
    try:
        relative_error_floors = _parse_positive_float_list(args.relative_error_floors)
    except ValueError as exc:
        parser.error(f"--relative-error-floors: {exc}")
    semantic_loss_weights: list[float] = []
    if str(args.response_semantic_loss_weights).strip():
        try:
            semantic_loss_weights = _parse_positive_float_list(
                args.response_semantic_loss_weights
            )
        except ValueError as exc:
            parser.error(f"--response-semantic-loss-weights: {exc}")
    if args.response_loss_family == "relative_mse" and not relative_error_floors:
        parser.error("--response-loss-family relative_mse requires positive denominator floors")
    if semantic_loss_weights and args.response_loss_family != "relative_mse":
        parser.error(
            "--response-semantic-loss-weights currently requires "
            "--response-loss-family relative_mse"
        )
    if args.q_target_semantics == "minimum" and args.response_loss_family != "relative_mse":
        parser.error("--q-target-semantics minimum currently requires --response-loss-family relative_mse")
    if (
        not math.isfinite(float(args.q_minimum_margin_physical))
        or float(args.q_minimum_margin_physical) < 0.0
    ):
        parser.error("--q-minimum-margin-physical must be finite and nonnegative")
    if float(args.q_minimum_margin_physical) > 0.0:
        if args.response_loss_family != "relative_mse":
            parser.error(
                "--q-minimum-margin-physical requires "
                "--response-loss-family relative_mse"
            )
        if args.q_target_semantics != "minimum":
            parser.error(
                "--q-minimum-margin-physical requires "
                "--q-target-semantics minimum"
            )
    if args.inverse_checkpoint_selection != "training_objective":
        if args.response_loss_family != "relative_mse":
            parser.error(
                "acceptance-aligned inverse checkpoint selection requires "
                "--response-loss-family relative_mse"
            )
        if args.q_target_semantics != "minimum":
            parser.error(
                "acceptance-aligned inverse checkpoint selection requires "
                "--q-target-semantics minimum"
            )
        if args.inverse_geometry_projection != "hard_feasible_topology_v1":
            parser.error(
                "acceptance-aligned inverse checkpoint selection requires "
                "--inverse-geometry-projection hard_feasible_topology_v1"
            )
    if (
        not math.isfinite(float(args.inverse_checkpoint_exact_relative_error_threshold))
        or float(args.inverse_checkpoint_exact_relative_error_threshold) <= 0.0
    ):
        parser.error(
            "--inverse-checkpoint-exact-relative-error-threshold must be finite and positive"
        )
    if int(args.local_refinement_steps) < 0:
        parser.error("--local-refinement-steps must be nonnegative")
    if int(args.local_refinement_starts) < 1:
        parser.error("--local-refinement-starts must be at least 1")
    if not math.isfinite(float(args.local_refinement_learning_rate)) or float(
        args.local_refinement_learning_rate
    ) <= 0.0:
        parser.error("--local-refinement-learning-rate must be finite and positive")
    if not math.isfinite(float(args.local_refinement_final_lr_fraction)) or not (
        0.0 <= float(args.local_refinement_final_lr_fraction) <= 1.0
    ):
        parser.error("--local-refinement-final-lr-fraction must be finite and in [0,1]")
    if not math.isfinite(float(args.local_refinement_jitter)) or float(args.local_refinement_jitter) < 0.0:
        parser.error("--local-refinement-jitter must be finite and nonnegative")
    return args


def _base_summary(
    args: argparse.Namespace,
    training_csv: Path,
    out_dir: Path,
    input_columns: list[str],
    geometry_columns: list[str],
    count: int,
    checks: dict[str, bool],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "training_csv_sha256": _sha256_file(training_csv) if training_csv.is_file() else "",
        "out_dir": str(out_dir),
        "training_count": int(count),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "model_comparison_contract": _model_comparison_contract(args, input_columns, geometry_columns),
        "checks": checks,
        "arguments": _json_safe(vars(args)),
    }


def _json_safe(value: Any) -> Any:
    """Return a JSON-portable value while preserving explicit non-finite defaults."""
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _write_strict_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_strict_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    _write_strict_json(temporary, payload)
    temporary.replace(path)


def _model_comparison_contract(
    args: argparse.Namespace,
    input_columns: list[str],
    geometry_columns: list[str],
) -> dict[str, Any]:
    implementation_path = Path(__file__).resolve()
    contract = {
        "schema": "physical_feature_tandem_cross_checkpoint_v1",
        "trainer_implementation_sha256": _sha256_file(implementation_path),
        "input_columns": list(input_columns),
        "geometry_columns": list(geometry_columns),
        "architecture": {
            "forward_depth": int(args.forward_depth),
            "forward_width": int(args.forward_width),
            "forward_hidden_widths": _resolve_hidden_widths(
                args.forward_hidden_widths,
                depth=int(args.forward_depth),
                width=int(args.forward_width),
            ),
            "forward_hidden_widths_source": (
                "explicit_per_layer" if args.forward_hidden_widths else "legacy_uniform_depth_width"
            ),
            "inverse_depth": int(args.inverse_depth),
            "inverse_width": int(args.inverse_width),
            "inverse_hidden_widths": _resolve_hidden_widths(
                args.inverse_hidden_widths,
                depth=int(args.inverse_depth),
                width=int(args.inverse_width),
            ),
            "inverse_hidden_widths_source": (
                "explicit_per_layer" if args.inverse_hidden_widths else "legacy_uniform_depth_width"
            ),
            "inverse_geometry_projection": str(args.inverse_geometry_projection),
        },
        "optimization": {
            "batch_size": int(args.batch_size),
            "training_batch_sampler": str(args.training_batch_sampler),
            "exact_update_batch_mode": str(args.exact_update_batch_mode),
            "forward_initialization": {
                "mode": str(args.forward_initialization_mode),
                "source_weights_sha256": str(args.forward_initial_weights_sha256 or ""),
                "source_summary_sha256": str(args.forward_initial_summary_sha256 or ""),
            },
            "inverse_initialization": {
                "mode": str(args.inverse_initialization_mode),
                "source_weights_sha256": str(args.inverse_initial_weights_sha256 or ""),
                "source_summary_sha256": str(args.inverse_initial_summary_sha256 or ""),
            },
            "forward_epochs": int(args.forward_epochs),
            "inverse_epochs": int(args.inverse_epochs),
            "patience": int(args.patience),
            "forward_max_optimizer_updates": int(args.forward_max_optimizer_updates),
            "inverse_max_optimizer_updates": int(args.inverse_max_optimizer_updates),
            "validation_every_optimizer_updates": int(
                args.validation_every_optimizer_updates
            ),
            "learning_rate": float(args.learning_rate),
            "training_learning_rate_schedule": str(
                args.training_learning_rate_schedule
            ),
            "training_final_learning_rate_fraction": float(
                args.training_final_learning_rate_fraction
            ),
            "weight_decay": float(args.weight_decay),
        },
        "normalization": {
            "mode": (
                "external_declared_midpoint_half_range"
                if args.fixed_normalization_contract_json
                else "legacy_train_arm_empirical"
            ),
            "fixed_contract_sha256": str(
                args.fixed_normalization_contract_sha256 or ""
            ),
        },
        "evaluation": {
            "mode": str(args.evaluation_mode),
            "test_access_allowed": args.evaluation_mode == "validation_then_test",
        },
        "loss": {
            "response_weight": float(args.response_weight),
            "geometry_anchor_weight": float(args.geometry_anchor_weight),
            "topology_feasibility_weight": float(args.topology_feasibility_weight),
            "enforce_power_line_port_ground_overlap": bool(
                args.enforce_power_line_port_ground_overlap
            ),
            "power_line_bar_offset_um": float(args.power_line_bar_offset_um),
            "power_line_shield_opening_clearance_um": float(
                args.power_line_shield_opening_clearance_um
            ),
            "power_line_port_ground_overlap_um": float(
                args.power_line_port_ground_overlap_um
            ),
            "power_line_feed_training_safety_margin_um": float(
                args.power_line_feed_training_safety_margin_um
            ),
            "response_ramp_fraction": float(args.response_ramp_fraction),
            "response_loss_scaling": str(args.response_loss_scaling),
            "response_loss_family": str(args.response_loss_family),
            "q_target_semantics": str(args.q_target_semantics),
            "q_minimum_margin_physical": float(args.q_minimum_margin_physical),
            "relative_error_floors": str(args.relative_error_floors),
            "response_semantic_loss_weights": str(
                args.response_semantic_loss_weights
            ),
            "response_weight_schedule": str(args.response_weight_schedule),
            "response_schedule_domain": str(args.response_schedule_domain),
            "response_warmup_fraction": float(args.response_warmup_fraction),
            "response_warmup_optimizer_updates": args.response_warmup_optimizer_updates,
            "response_ramp_optimizer_updates": args.response_ramp_optimizer_updates,
            "response_adaptive_ema_decay": float(args.response_adaptive_ema_decay),
            "response_adaptive_min_multiplier": float(args.response_adaptive_min_multiplier),
            "response_adaptive_max_multiplier": float(args.response_adaptive_max_multiplier),
        },
        "split": {
            "mode": str(args.split_mode),
            "fixed_common_holdout_manifest_sha256": str(
                args.fixed_common_holdout_manifest_sha256 or ""
            ),
            "validation_fraction": float(args.validation_fraction),
            "test_fraction": float(args.test_fraction),
            "physical_cell_bins": int(args.physical_cell_bins),
            "physical_cell_lower": str(args.physical_cell_lower or ""),
            "physical_cell_upper": str(args.physical_cell_upper or ""),
        },
    }
    contract["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return contract


def _evaluation_isolation_contract(
    matrix: dict[str, Any],
    split: dict[str, np.ndarray],
    evaluation_mode: str = "validation_then_test",
) -> dict[str, Any]:
    identities = [str(item).strip().lower() for item in matrix.get("source_geometry_identities") or []]
    expected_indices = set(range(len(identities)))
    split_indices = {
        name: [int(item) for item in np.asarray(split.get(name, []), dtype=int)]
        for name in ("train", "validation", "test")
    }
    split_sets = {name: set(values) for name, values in split_indices.items()}
    assigned = split_sets["train"] | split_sets["validation"] | split_sets["test"]
    overlap_count = sum(
        len(split_sets[left] & split_sets[right])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    )
    identity_sets = {
        name: {identities[index] for index in values if 0 <= index < len(identities)}
        for name, values in split_indices.items()
    }
    identity_overlap_count = sum(
        len(identity_sets[left] & identity_sets[right])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    )

    def identity_fingerprint(name: str) -> str:
        payload = "".join(f"{identity}\n" for identity in sorted(identity_sets[name]))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()

    checks = {
        "all_geometry_identities_are_sha256": len(identities) == len(expected_indices)
        and all(_is_sha256(identity) for identity in identities),
        "all_geometry_identities_unique": len(set(identities)) == len(identities),
        "all_rows_assigned_once": assigned == expected_indices and overlap_count == 0,
        "no_geometry_identity_overlap_across_splits": identity_overlap_count == 0,
        "all_splits_nonempty": all(bool(values) for values in split_indices.values()),
    }
    test_accessed = evaluation_mode == "validation_then_test"
    return {
        "overall_status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "row_counts": {name: len(values) for name, values in split_indices.items()},
        "index_overlap_count": int(overlap_count),
        "geometry_identity_overlap_count": int(identity_overlap_count),
        "geometry_identity_set_sha256": {
            name: identity_fingerprint(name) for name in ("train", "validation", "test")
        },
        "test_set_used_for_gradient_updates": False,
        "test_set_used_for_early_stopping": False,
        "test_set_used_for_model_or_hyperparameter_selection": False,
        "test_set_used_for_acceptance_threshold_tuning": False,
        "evaluation_mode": evaluation_mode,
        "test_set_not_accessed": not test_accessed,
        "test_set_used_only_for_post_training_evaluation": test_accessed,
        "selection_contract": (
            "training rows update weights; validation rows select epochs; test rows remain sealed"
            if not test_accessed
            else "training rows update weights; validation rows select epochs; test rows are evaluated once after both models are frozen"
        ),
    }


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tandem_stage_checkpoint_contract(
    *,
    training_csv: Path,
    out_dir: Path,
    input_columns: list[str],
    split_reference_columns: list[str],
    geometry_columns: list[str],
    split_audit: dict[str, Any],
    data: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    arguments = dict(vars(args))
    arguments["training_csv"] = str(training_csv)
    arguments["out_dir"] = str(out_dir)
    payload = _json_safe(
        {
            "schema": "physical_feature_tandem_stage_checkpoint_contract_v1",
            "trainer_implementation_sha256": _sha256_file(Path(__file__).resolve()),
            "numpy_version": np.__version__,
            "training_csv": str(training_csv),
            "training_csv_sha256": _sha256_file(training_csv),
            "out_dir": str(out_dir),
            "input_columns": list(input_columns),
            "split_reference_columns": list(split_reference_columns),
            "geometry_columns": list(geometry_columns),
            "split_audit": split_audit,
            "normalization": data["normalization"],
            "normalization_contract": data["normalization_contract"],
            "topology_feasibility_contract": data["topology_feasibility_contract"],
            "response_loss_contract": data["response_loss_contract"],
            "forward_initialization_contract": data["forward_initialization_contract"],
            "inverse_initialization_contract": data["inverse_initialization_contract"],
            "training_batch_sampler_contract": data["training_batch_sampler_contract"],
            "optimizer_budget_contract": data["optimizer_budget_contract"],
            "arguments": arguments,
        }
    )
    if not isinstance(payload, dict):
        raise RuntimeError("tandem stage checkpoint contract is not a JSON object")
    return {**payload, "fingerprint_sha256": _json_fingerprint(payload)}


def _prepare_tandem_stage_checkpoint_contract(
    root: Path,
    contract: dict[str, Any],
) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    contract_path = root / "stage_checkpoint_contract.json"
    expected_fingerprint = str(contract.get("fingerprint_sha256") or "")
    if not _is_sha256(expected_fingerprint):
        raise RuntimeError("tandem stage checkpoint contract fingerprint is invalid")
    if contract_path.is_file():
        try:
            existing = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing tandem stage checkpoint contract is unreadable") from exc
        existing_payload = dict(existing)
        recorded_fingerprint = str(existing_payload.pop("fingerprint_sha256", ""))
        recalculated_fingerprint = _json_fingerprint(existing_payload)
        if (
            recorded_fingerprint != expected_fingerprint
            or recalculated_fingerprint != expected_fingerprint
            or existing != contract
        ):
            raise RuntimeError("existing tandem stage checkpoint contract does not match the exact run contract")
    else:
        if any(root.glob("*_stage.complete.json")):
            raise RuntimeError("tandem stage checkpoint markers exist without their exact contract")
        _write_strict_json_atomic(contract_path, contract)
    return {
        "path": str(contract_path),
        "sha256": _sha256_file(contract_path),
    }


def _save_tandem_stage_checkpoint(
    root: Path,
    *,
    stage: str,
    contract_fingerprint: str,
    dependency_weights_sha256: str,
    history: list[dict[str, Any]],
    best: dict[str, Any],
    expected_input_dim: int,
    expected_output_dim: int,
) -> dict[str, Any]:
    weights = [np.asarray(value, dtype=float).copy() for value in best.get("weights") or []]
    biases = [np.asarray(value, dtype=float).copy() for value in best.get("biases") or []]
    _validate_tandem_stage_network(
        weights,
        biases,
        expected_input_dim=expected_input_dim,
        expected_output_dim=expected_output_dim,
    )
    if not history:
        raise RuntimeError(f"cannot checkpoint empty {stage} training history")
    loss = float(best.get("loss", math.inf))
    if not math.isfinite(loss):
        raise RuntimeError(f"cannot checkpoint non-finite {stage} best loss")

    weights_path = root / f"{stage}_stage_weights.npz"
    metadata_path = root / f"{stage}_stage.json"
    marker_path = root / f"{stage}_stage.complete.json"
    weights_temporary = weights_path.with_name(weights_path.stem + ".tmp.npz")
    arrays: dict[str, np.ndarray] = {}
    for index, value in enumerate(weights):
        arrays[f"weight_{index}"] = value
    for index, value in enumerate(biases):
        arrays[f"bias_{index}"] = value
    np.savez_compressed(weights_temporary, **arrays)
    weights_temporary.replace(weights_path)
    weights_sha256 = _sha256_file(weights_path)

    metadata = {
        "schema": "physical_feature_tandem_stage_checkpoint_v1",
        "stage": stage,
        "contract_fingerprint_sha256": contract_fingerprint,
        "dependency_weights_sha256": dependency_weights_sha256,
        "expected_input_dim": int(expected_input_dim),
        "expected_output_dim": int(expected_output_dim),
        "weights_sha256": weights_sha256,
        "best": {
            "loss": loss,
            "epoch": int(best.get("epoch") or 0),
            "optimizer_updates": int(best.get("optimizer_updates") or 0),
        },
        "history": history,
    }
    _write_strict_json_atomic(metadata_path, metadata)
    marker = {
        "schema": "physical_feature_tandem_stage_checkpoint_marker_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS",
        "stage": stage,
        "contract_fingerprint_sha256": contract_fingerprint,
        "dependency_weights_sha256": dependency_weights_sha256,
        "weights_path": str(weights_path),
        "weights_sha256": weights_sha256,
        "metadata_path": str(metadata_path),
        "metadata_sha256": _sha256_file(metadata_path),
    }
    _write_strict_json_atomic(marker_path, marker)
    return marker


def _load_tandem_stage_checkpoint(
    root: Path,
    *,
    stage: str,
    contract_fingerprint: str,
    dependency_weights_sha256: str,
    expected_input_dim: int,
    expected_output_dim: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None:
    marker_path = root / f"{stage}_stage.complete.json"
    if not marker_path.is_file():
        return None
    weights_path = root / f"{stage}_stage_weights.npz"
    metadata_path = root / f"{stage}_stage.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("schema") != "physical_feature_tandem_stage_checkpoint_marker_v1":
            raise ValueError("unexpected marker schema")
        if marker.get("status") != "PASS" or marker.get("stage") != stage:
            raise ValueError("marker does not record a complete matching stage")
        if marker.get("contract_fingerprint_sha256") != contract_fingerprint:
            raise ValueError("stage contract fingerprint mismatch")
        if str(marker.get("dependency_weights_sha256") or "") != dependency_weights_sha256:
            raise ValueError("stage dependency fingerprint mismatch")
        if marker.get("weights_path") != str(weights_path) or marker.get("metadata_path") != str(metadata_path):
            raise ValueError("stage artifact path mismatch")
        if not weights_path.is_file() or not metadata_path.is_file():
            raise ValueError("stage artifact is missing")
        if _sha256_file(weights_path) != marker.get("weights_sha256"):
            raise ValueError("stage weights SHA-256 mismatch")
        if _sha256_file(metadata_path) != marker.get("metadata_sha256"):
            raise ValueError("stage metadata SHA-256 mismatch")

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("schema") != "physical_feature_tandem_stage_checkpoint_v1":
            raise ValueError("unexpected stage metadata schema")
        if metadata.get("stage") != stage:
            raise ValueError("stage metadata identity mismatch")
        if metadata.get("contract_fingerprint_sha256") != contract_fingerprint:
            raise ValueError("stage metadata contract mismatch")
        if str(metadata.get("dependency_weights_sha256") or "") != dependency_weights_sha256:
            raise ValueError("stage metadata dependency mismatch")
        if metadata.get("weights_sha256") != marker.get("weights_sha256"):
            raise ValueError("stage metadata weights identity mismatch")
        if int(metadata.get("expected_input_dim") or 0) != int(expected_input_dim):
            raise ValueError("stage input dimension mismatch")
        if int(metadata.get("expected_output_dim") or 0) != int(expected_output_dim):
            raise ValueError("stage output dimension mismatch")

        with np.load(weights_path, allow_pickle=False) as arrays:
            weight_keys = sorted(
                (key for key in arrays.files if key.startswith("weight_")),
                key=lambda key: int(key.removeprefix("weight_")),
            )
            bias_keys = sorted(
                (key for key in arrays.files if key.startswith("bias_")),
                key=lambda key: int(key.removeprefix("bias_")),
            )
            weights = [np.asarray(arrays[key], dtype=float).copy() for key in weight_keys]
            biases = [np.asarray(arrays[key], dtype=float).copy() for key in bias_keys]
        _validate_tandem_stage_network(
            weights,
            biases,
            expected_input_dim=expected_input_dim,
            expected_output_dim=expected_output_dim,
        )
        history = metadata.get("history")
        best_scalar = metadata.get("best")
        if not isinstance(history, list) or not history or not all(isinstance(item, dict) for item in history):
            raise ValueError("stage history is missing or invalid")
        if not isinstance(best_scalar, dict):
            raise ValueError("stage best-checkpoint metadata is missing")
        loss = float(best_scalar.get("loss", math.inf))
        if not math.isfinite(loss):
            raise ValueError("stage best loss is non-finite")
        best = {
            "loss": loss,
            "epoch": int(best_scalar.get("epoch") or 0),
            "optimizer_updates": int(best_scalar.get("optimizer_updates") or 0),
            "weights": weights,
            "biases": biases,
        }
        return [dict(item) for item in history], best, marker
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid exact tandem stage checkpoint for {stage}: {exc}") from exc


def _validate_tandem_stage_network(
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    *,
    expected_input_dim: int,
    expected_output_dim: int,
) -> None:
    if not weights or len(weights) != len(biases):
        raise ValueError("stage checkpoint network has incomplete layers")
    previous_width = int(expected_input_dim)
    for weight, bias in zip(weights, biases):
        if weight.ndim != 2 or bias.ndim != 1:
            raise ValueError("stage checkpoint network has invalid array rank")
        if weight.shape[0] != previous_width or weight.shape[1] != bias.shape[0]:
            raise ValueError("stage checkpoint network has incompatible layer shapes")
        if np.any(~np.isfinite(weight)) or np.any(~np.isfinite(bias)):
            raise ValueError("stage checkpoint network contains non-finite parameters")
        previous_width = int(bias.shape[0])
    if previous_width != int(expected_output_dim):
        raise ValueError("stage checkpoint network output dimension is incorrect")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_geometry_columns(rows: list[dict[str, str]], explicit: str | None, prefix: str) -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    return sorted(column for column in rows[0] if column.startswith(prefix)) if rows else []


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _geometry_identity_sha256(columns: list[str], values: list[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_float64_v1",
        "columns": list(columns),
        "values": [format(float(value), ".17g") for value in values],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _build_matrix(
    rows: list[dict[str, str]],
    input_columns: list[str],
    geometry_columns: list[str],
    split_reference_columns: list[str],
) -> dict[str, Any]:
    x_rows: list[list[float]] = []
    y_rows: list[list[float]] = []
    split_rows: list[list[float]] = []
    source_indices: list[int] = []
    source_evaluations: list[str] = []
    source_geometry_identities: list[str] = []
    for source_index, row in enumerate(rows):
        x = [_as_float(row.get(column)) for column in input_columns]
        y = [_as_float(row.get(column)) for column in geometry_columns]
        split_values = [_as_float(row.get(column)) for column in split_reference_columns]
        if any(item is None for item in x + y + split_values):
            continue
        x_values = [float(item) for item in x if item is not None]
        y_values = [float(item) for item in y if item is not None]
        x_rows.append(x_values)
        y_rows.append(y_values)
        split_rows.append([float(item) for item in split_values if item is not None])
        source_indices.append(source_index)
        source_evaluations.append(str(row.get("evaluation") or row.get("sample_id") or ""))
        source_geometry_identities.append(_geometry_identity_sha256(geometry_columns, y_values))
    return {
        "count": len(x_rows),
        "x": np.asarray(x_rows, dtype=float) if x_rows else np.empty((0, len(input_columns))),
        "y": np.asarray(y_rows, dtype=float) if y_rows else np.empty((0, len(geometry_columns))),
        "split_x": np.asarray(split_rows, dtype=float)
        if split_rows
        else np.empty((0, len(split_reference_columns))),
        "split_reference_columns": split_reference_columns,
        "source_indices": source_indices,
        "source_evaluations": source_evaluations,
        "source_geometry_identities": source_geometry_identities,
    }


def _split_indices(matrix: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if str(args.split_mode) == "fixed_common_holdout_manifest":
        return _split_indices_from_common_holdout_manifest(matrix, args)
    x = np.asarray(matrix["split_x"], dtype=float)
    lower, upper = parse_optional_feature_bounds(args.physical_cell_lower, args.physical_cell_upper, x.shape[1])
    return split_physical_feature_indices(
        x,
        mode=str(args.split_mode),
        seed=int(args.seed if args.split_seed is None else args.split_seed),
        validation_fraction=float(args.validation_fraction),
        test_fraction=float(args.test_fraction),
        physical_cell_bins=int(args.physical_cell_bins),
        physical_cell_lower=lower,
        physical_cell_upper=upper,
    )


def _split_indices_from_common_holdout_manifest(
    matrix: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    path = Path(str(args.fixed_common_holdout_manifest_json)).expanduser().resolve()
    expected = str(args.fixed_common_holdout_manifest_sha256).strip().lower()
    if not path.is_file():
        raise FileNotFoundError(f"fixed common holdout manifest is missing: {path}")
    if not _is_sha256(expected):
        raise ValueError("fixed common holdout manifest requires a lowercase expected SHA-256")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"fixed common holdout manifest SHA-256 mismatch: expected={expected} actual={actual}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"fixed common holdout manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "fixed_common_holdout_geometry_identity_v1":
        raise ValueError("unsupported fixed common holdout manifest schema")
    if payload.get("identity_kind") != "canonical_geometry_sha256":
        raise ValueError("fixed common holdout manifest identity kind must be canonical_geometry_sha256")

    def identity_list(key: str) -> list[str]:
        raw = payload.get(key)
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"fixed common holdout manifest {key} must be a non-empty list")
        values = [str(value).strip().lower() for value in raw]
        if any(not _is_sha256(value) for value in values) or len(set(values)) != len(values):
            raise ValueError(f"fixed common holdout manifest {key} must contain unique SHA-256 identities")
        return values

    validation_identities = identity_list("validation_geometry_identities")
    test_identities = identity_list("test_geometry_identities")
    validation_set = set(validation_identities)
    test_set = set(test_identities)
    if validation_set & test_set:
        raise ValueError("fixed common validation/test geometry identities overlap")

    loaded = [str(value).strip().lower() for value in matrix.get("source_geometry_identities") or []]
    if len(loaded) != int(matrix.get("count") or 0):
        raise ValueError("loaded geometry identity count does not match the model matrix")
    if any(not _is_sha256(value) for value in loaded) or len(set(loaded)) != len(loaded):
        raise ValueError("fixed common holdout mode requires unique SHA-256 geometry identities in the table")
    loaded_index = {identity: index for index, identity in enumerate(loaded)}
    missing_validation = sorted(validation_set - set(loaded_index))
    missing_test = sorted(test_set - set(loaded_index))
    if missing_validation or missing_test:
        raise ValueError(
            "fixed common holdout identities are absent from the loaded table: "
            f"validation_missing={len(missing_validation)} test_missing={len(missing_test)}"
        )
    validation_indices = np.asarray(
        sorted(loaded_index[identity] for identity in validation_identities), dtype=int
    )
    test_indices = np.asarray(
        sorted(loaded_index[identity] for identity in test_identities), dtype=int
    )
    holdout = validation_set | test_set
    train_indices = np.asarray(
        [index for index, identity in enumerate(loaded) if identity not in holdout], dtype=int
    )
    if min(len(train_indices), len(validation_indices), len(test_indices)) < 1:
        raise ValueError("fixed common holdout manifest produced an empty train/validation/test split")
    split = {
        "train": train_indices,
        "validation": validation_indices,
        "test": test_indices,
    }
    assigned = np.concatenate([split[name] for name in ("train", "validation", "test")])
    if len(assigned) != len(loaded) or len(np.unique(assigned)) != len(loaded):
        raise RuntimeError("fixed common holdout split did not assign each loaded row exactly once")
    split_hashes: dict[str, str] = {}
    combined = hashlib.sha256()
    for name in ("train", "validation", "test"):
        indices = np.asarray(split[name], dtype=np.int64)
        split_hashes[name] = hashlib.sha256(indices.tobytes()).hexdigest()
        combined.update(name.encode("ascii"))
        combined.update(b"\0")
        combined.update(indices.tobytes())
    holdout_fingerprint = hashlib.sha256(
        "".join(
            [f"validation\0{identity}\n" for identity in sorted(validation_set)]
            + [f"test\0{identity}\n" for identity in sorted(test_set)]
        ).encode("ascii")
    ).hexdigest()
    declared_fingerprint = str(payload.get("common_holdout_fingerprint_sha256") or "")
    if declared_fingerprint and declared_fingerprint != holdout_fingerprint:
        raise ValueError("fixed common holdout manifest fingerprint does not match its identity lists")
    audit = {
        "split_mode": "fixed_common_holdout_manifest",
        "physical_cell_grouped": False,
        "physical_cell_overlap_count": None,
        "all_rows_assigned_once": True,
        "row_counts": {name: int(len(indices)) for name, indices in split.items()},
        "fixed_common_holdout_manifest": {
            "path": str(path),
            "sha256": actual,
            "schema": str(payload["schema"]),
            "identity_kind": str(payload["identity_kind"]),
            "common_holdout_fingerprint_sha256": holdout_fingerprint,
            "selection_method": payload.get("selection_method"),
            "stratification": payload.get("stratification"),
            "coverage_audit": payload.get("coverage_audit"),
        },
        "split_index_sha256": split_hashes,
        "split_fingerprint_sha256": combined.hexdigest(),
        "boundary": (
            "Validation/test are a preregistered, unique-identity, source-batch-by-physical-cell stratified "
            "same-distribution holdout shared byte-for-byte across data-size arms. This is not a whole-cell OOD "
            "split."
        ),
    }
    return split, audit


def _normalize(
    matrix: dict[str, Any],
    split: dict[str, np.ndarray],
    floor: float,
    *,
    fixed_contract_path: str | None = None,
    expected_fixed_contract_sha256: str | None = None,
    input_columns: list[str] | None = None,
    geometry_columns: list[str] | None = None,
) -> dict[str, Any]:
    x = np.asarray(matrix["x"], dtype=float)
    y = np.asarray(matrix["y"], dtype=float)
    split_x_physical = np.asarray(matrix["split_x"], dtype=float)
    train = split["train"]
    fixed_contract: dict[str, Any] | None = None
    if fixed_contract_path:
        fixed_contract = _load_fixed_normalization_contract(
            fixed_contract_path,
            str(expected_fixed_contract_sha256 or ""),
            input_columns=list(input_columns or []),
            geometry_columns=list(geometry_columns or []),
        )
        x_lower = np.asarray(fixed_contract["input_lower"], dtype=float)
        x_upper = np.asarray(fixed_contract["input_upper"], dtype=float)
        y_lower = np.asarray(fixed_contract["geometry_lower_physical"], dtype=float)
        y_upper = np.asarray(fixed_contract["geometry_upper_physical"], dtype=float)
        _require_rows_inside_declared_bounds(x, x_lower, x_upper, "physical input")
        _require_rows_inside_declared_bounds(y, y_lower, y_upper, "geometry")
        x_mean = 0.5 * (x_lower + x_upper)
        x_scale = 0.5 * (x_upper - x_lower)
        y_mean = 0.5 * (y_lower + y_upper)
        y_scale = 0.5 * (y_upper - y_lower)
        feature_lower = x_lower
        feature_upper = x_upper
        normalized_geometry_lower = (y_lower - y_mean) / y_scale
        normalized_geometry_upper = (y_upper - y_mean) / y_scale
        normalization_contract = {
            "mode": "external_declared_midpoint_half_range",
            "schema": str(fixed_contract["schema"]),
            "path": str(fixed_contract["path"]),
            "sha256": str(fixed_contract["sha256"]),
            "input_columns": list(fixed_contract["input_columns"]),
            "geometry_columns": list(fixed_contract["geometry_columns"]),
            "large_arm_empirical_statistics_used": False,
            "train_arm_specific_statistics_used": False,
            "all_loaded_rows_required_inside_declared_bounds": True,
            "interpretation_boundary": (
                "Both comparison arms use the same result-independent declared physical and generator geometry "
                "bounds. No large-arm empirical mean, variance, minimum, or maximum is transferred to the "
                "small arm."
            ),
        }
    else:
        x_mean = np.mean(x[train], axis=0)
        x_scale = np.maximum(np.std(x[train], axis=0), floor)
        y_mean = np.mean(y[train], axis=0)
        y_scale = np.maximum(np.std(y[train], axis=0), floor)
        feature_lower = np.min(x[train], axis=0)
        feature_upper = np.max(x[train], axis=0)
        normalized_geometry_lower = np.min((y[train] - y_mean[None, :]) / y_scale[None, :], axis=0)
        normalized_geometry_upper = np.max((y[train] - y_mean[None, :]) / y_scale[None, :], axis=0)
        normalization_contract = {
            "mode": "legacy_train_arm_empirical",
            "schema": "legacy_train_split_mean_std_and_observed_envelope_v1",
            "path": "",
            "sha256": "",
            "large_arm_empirical_statistics_used": None,
            "train_arm_specific_statistics_used": True,
            "all_loaded_rows_required_inside_declared_bounds": False,
            "interpretation_boundary": (
                "Legacy behavior recomputes scaling and the decoder envelope from each run's own train split; "
                "it is not suitable for a data-size-only controlled comparison unless the resulting arrays are "
                "proven identical."
            ),
        }
    if np.any(~np.isfinite(x_scale)) or np.any(x_scale <= floor):
        raise ValueError("normalization input half-ranges/scales must be finite and exceed the floor")
    if np.any(~np.isfinite(y_scale)) or np.any(y_scale <= floor):
        raise ValueError("normalization geometry half-ranges/scales must be finite and exceed the floor")
    x_norm = (x - x_mean[None, :]) / x_scale[None, :]
    y_norm = (y - y_mean[None, :]) / y_scale[None, :]
    return {
        "x": x_norm,
        "y": y_norm,
        "split_x_physical": split_x_physical,
        "split": split,
        "source_indices": matrix["source_indices"],
        "source_evaluations": matrix["source_evaluations"],
        "source_geometry_identities": matrix["source_geometry_identities"],
        "normalization_contract": normalization_contract,
        "normalization": {
            "x_mean": x_mean,
            "x_scale": x_scale,
            "feature_lower": feature_lower,
            "feature_upper": feature_upper,
            "y_mean": y_mean,
            "y_scale": y_scale,
            "geometry_lower": normalized_geometry_lower,
            "geometry_upper": normalized_geometry_upper,
        },
    }


def _load_fixed_normalization_contract(
    raw_path: str,
    expected_sha256: str,
    *,
    input_columns: list[str],
    geometry_columns: list[str],
) -> dict[str, Any]:
    path = Path(raw_path).expanduser().resolve()
    expected = str(expected_sha256).strip().lower()
    if not path.is_file():
        raise FileNotFoundError(f"fixed normalization contract is missing: {path}")
    if not _is_sha256(expected):
        raise ValueError("fixed normalization contract requires a lowercase expected SHA-256")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"fixed normalization contract SHA-256 mismatch: expected={expected} actual={actual}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"fixed normalization contract is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("fixed normalization contract must be a JSON object")
    if payload.get("schema") != "declared_midpoint_half_range_normalization_v1":
        raise ValueError("unsupported fixed normalization contract schema")
    if payload.get("input_columns") != input_columns:
        raise ValueError("fixed normalization input-column order does not match the trainer")
    if payload.get("geometry_columns") != geometry_columns:
        raise ValueError("fixed normalization geometry-column order does not match the trainer")

    def vector(key: str, expected_length: int) -> list[float]:
        try:
            values = np.asarray(payload[key], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"fixed normalization contract has invalid {key}") from exc
        if values.shape != (expected_length,) or np.any(~np.isfinite(values)):
            raise ValueError(
                f"fixed normalization contract {key} must contain {expected_length} finite values"
            )
        return [float(value) for value in values]

    input_lower = vector("input_lower", len(input_columns))
    input_upper = vector("input_upper", len(input_columns))
    geometry_lower = vector("geometry_lower", len(geometry_columns))
    geometry_upper = vector("geometry_upper", len(geometry_columns))
    if np.any(np.asarray(input_upper) <= np.asarray(input_lower)):
        raise ValueError("fixed normalization input upper bounds must exceed lower bounds")
    if np.any(np.asarray(geometry_upper) <= np.asarray(geometry_lower)):
        raise ValueError("fixed normalization geometry upper bounds must exceed lower bounds")
    return {
        "schema": payload["schema"],
        "path": str(path),
        "sha256": actual,
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "input_lower": input_lower,
        "input_upper": input_upper,
        "geometry_lower_physical": geometry_lower,
        "geometry_upper_physical": geometry_upper,
    }


def _require_rows_inside_declared_bounds(
    values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    label: str,
) -> None:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or lower.shape != (matrix.shape[1],) or upper.shape != (matrix.shape[1],):
        raise ValueError(f"{label} declared-bound dimensionality mismatch")
    finite = np.all(np.isfinite(matrix), axis=1)
    inside = finite & np.all(matrix >= lower[None, :], axis=1) & np.all(
        matrix <= upper[None, :], axis=1
    )
    if not np.all(inside):
        bad = np.flatnonzero(~inside)
        raise ValueError(
            f"{label} rows outside the fixed declared normalization bounds: count={len(bad)} "
            f"first_indices={bad[:10].tolist()}"
        )


def _configure_response_loss(
    data: dict[str, Any],
    input_columns: list[str],
    split_reference_columns: list[str],
    split_audit: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Configure dimension scaling and the optional train-only BNI prior."""

    x_scale = np.asarray(data["normalization"]["x_scale"], dtype=float)
    if x_scale.shape != (len(input_columns),):
        raise ValueError("response-loss scaling requires one finite scale per input feature")
    if args.response_loss_scaling == "standardized":
        spans = np.full(len(input_columns), np.nan, dtype=float)
        weights = np.ones(len(input_columns), dtype=float)
        range_source = "not_used_standardized_equal_weight"
    else:
        reference_lower = np.asarray(split_audit.get("physical_cell_lower") or [], dtype=float)
        reference_upper = np.asarray(split_audit.get("physical_cell_upper") or [], dtype=float)
        explicit_spans: dict[str, float] = {}
        if reference_lower.shape == (len(split_reference_columns),) and reference_upper.shape == reference_lower.shape:
            for column, lower, upper in zip(split_reference_columns, reference_lower, reference_upper):
                span = float(upper - lower)
                if math.isfinite(span) and span > 0.0:
                    explicit_spans[column] = span
        spans_list = []
        span_sources = []
        for column in input_columns:
            semantic = _physical_feature_semantic(column)
            exact = explicit_spans.get(column)
            semantic_explicit = next(
                (
                    span
                    for reference_column, span in explicit_spans.items()
                    if _physical_feature_semantic(reference_column) == semantic
                ),
                None,
            )
            if exact is not None:
                spans_list.append(exact)
                span_sources.append("explicit_split_bound")
            elif semantic_explicit is not None:
                spans_list.append(semantic_explicit)
                span_sources.append("semantic_explicit_split_bound")
            elif semantic in PROJECT_FEATURE_BOUNDS:
                lower, upper = PROJECT_FEATURE_BOUNDS[semantic]
                spans_list.append(float(upper - lower))
                span_sources.append("project_declared_bound")
            else:
                raise ValueError(f"no declared physical response span is available for {column}")
        spans = np.asarray(spans_list, dtype=float)
        if np.any(~np.isfinite(spans)) or np.any(spans <= 0.0):
            raise ValueError("declared response spans must be finite and positive")
        raw_weights = (x_scale / spans) ** 2
        mean_weight = float(np.mean(raw_weights))
        if not math.isfinite(mean_weight) or mean_weight <= 0.0:
            raise ValueError("response dimension weights are not finite and positive")
        weights = raw_weights / mean_weight
        range_source = ",".join(span_sources)
    data["normalization"]["response_loss_dimension_weights"] = weights
    data["normalization"]["response_loss_physical_spans"] = spans
    family = str(getattr(args, "response_loss_family", "mse"))
    q_target_semantics = str(getattr(args, "q_target_semantics", "exact"))
    minimum_target_indices = (
        [
            index
            for index, column in enumerate(input_columns)
            if _physical_feature_semantic(column) == "q"
        ]
        if q_target_semantics == "minimum"
        else []
    )
    if q_target_semantics == "minimum" and len(minimum_target_indices) != 1:
        raise ValueError(
            "minimum Q target semantics require exactly one Q or Qmin response feature"
        )
    bni_contract: dict[str, Any] | None = None
    relative_contract: dict[str, Any] | None = None
    data["balanced_mse_bni"] = None
    data["response_loss_state"] = None
    if family == "balanced_mse_bni":
        bni_state, bni_contract = _build_balanced_mse_bni_state(
            data,
            input_columns,
            split_reference_columns,
            split_audit,
            float(args.balanced_mse_temperature),
        )
        data["balanced_mse_bni"] = bni_state
        data["response_loss_state"] = bni_state
    elif family == "relative_mse":
        floors = np.asarray(
            _parse_positive_float_list(args.relative_error_floors),
            dtype=float,
        )
        if floors.shape != (len(input_columns),):
            raise ValueError(
                "relative-error floors must contain exactly one value per input column"
            )
        x_mean = np.asarray(data["normalization"]["x_mean"], dtype=float)
        x_scale = np.asarray(data["normalization"]["x_scale"], dtype=float)
        if (
            x_mean.shape != floors.shape
            or x_scale.shape != floors.shape
            or np.any(~np.isfinite(x_mean))
            or np.any(~np.isfinite(x_scale))
            or np.any(x_scale <= 0.0)
        ):
            raise ValueError("relative MSE received invalid physical normalization")
        semantic_weights_raw = (
            np.asarray(
                _parse_positive_float_list(args.response_semantic_loss_weights),
                dtype=float,
            )
            if str(args.response_semantic_loss_weights).strip()
            else np.ones(len(input_columns), dtype=float)
        )
        if semantic_weights_raw.shape != (len(input_columns),):
            raise ValueError(
                "response semantic loss weights must contain exactly one value "
                "per input column"
            )
        semantic_weight_mean = float(np.mean(semantic_weights_raw))
        if (
            np.any(~np.isfinite(semantic_weights_raw))
            or np.any(semantic_weights_raw <= 0.0)
            or not math.isfinite(semantic_weight_mean)
            or semantic_weight_mean <= 0.0
        ):
            raise ValueError(
                "response semantic loss weights must be finite and positive"
            )
        semantic_weights = semantic_weights_raw / semantic_weight_mean
        q_minimum_margin_physical = float(args.q_minimum_margin_physical)
        relative_state = {
            "family": "relative_mse",
            "x_mean": x_mean,
            "x_scale": x_scale,
            "denominator_floors_physical": floors,
            "minimum_target_indices": np.asarray(minimum_target_indices, dtype=int),
            "semantic_loss_weights": semantic_weights,
            "q_minimum_margin_physical": q_minimum_margin_physical,
        }
        data["response_loss_state"] = relative_state
        relative_contract = {
            "enabled": True,
            "denominator_floors_physical": {
                column: float(floors[index])
                for index, column in enumerate(input_columns)
            },
            "semantic_loss_weights_raw": {
                column: float(semantic_weights_raw[index])
                for index, column in enumerate(input_columns)
            },
            "semantic_loss_weights_mean_normalized": {
                column: float(semantic_weights[index])
                for index, column in enumerate(input_columns)
            },
            "q_minimum_margin_physical": q_minimum_margin_physical,
            "formula": (
                "mean(weight*(semantic_error_physical/max(abs(truth_physical),floor))^2), where "
                "semantic_error_Q=min(prediction_Q-(target_Qmin+fixed_margin),0) for minimum-Q targets"
                if minimum_target_indices
                else "mean(weight*((prediction_physical-truth_physical)/max(abs(truth_physical),floor))^2)"
            ),
            "selection_metric": (
                "validation_qmin_aware_relative_physical_rmse"
                if minimum_target_indices
                else "validation_relative_physical_rmse"
            ),
            "scientific_boundary": (
                "Floors are fixed before model selection and apply to all train/validation/test rows; "
                "Q minimum semantics apply globally to every inverse-training row and are not tuned on teacher targets. "
                "The Q guardband and semantic weights are fixed globally before validation. "
                "The forward surrogate remains an exact simulated-response regressor."
            ),
        }
    elif family != "mse":
        raise ValueError(f"unsupported response loss family: {family}")
    return {
        "family": family,
        "scaling": str(args.response_loss_scaling),
        "input_columns": list(input_columns),
        "physical_spans": {
            column: None if not math.isfinite(float(spans[index])) else float(spans[index])
            for index, column in enumerate(input_columns)
        },
        "standardized_dimension_weights": {
            column: float(weights[index]) for index, column in enumerate(input_columns)
        },
        "dimension_weight_mean": float(np.mean(weights)),
        "range_source": range_source,
        "balanced_mse_bni": bni_contract,
        "relative_mse": relative_contract,
        "q_target_semantics": q_target_semantics,
        "target_semantics": {
            column: (
                "minimum"
                if index in minimum_target_indices
                else "exact"
            )
            for index, column in enumerate(input_columns)
        },
        "interpretation": (
            "The inverse objective measures physical relative error with fixed denominator floors; Q is a "
            "one-sided minimum requirement while Lp, Ls, and |K| remain exact targets."
            if minimum_target_indices
            else "The objective measures physical relative error with fixed denominator floors so training and "
            "checkpoint selection align with percentage-based engineering acceptance."
            if family == "relative_mse"
            else (
                "Weights convert standardized errors into equalized declared-range errors before a mean-one rescale. "
                "This follows dimension-balanced physical regression; it does not change the declared feature ranges."
                if args.response_loss_scaling == "declared_range"
                else "Every standardized response dimension has equal loss weight."
            )
        ),
    }


def _build_balanced_mse_bni_state(
    data: dict[str, Any],
    input_columns: list[str],
    split_reference_columns: list[str],
    split_audit: dict[str, Any],
    temperature: float,
) -> tuple[dict[str, np.ndarray | float | str], dict[str, Any]]:
    """Build an equal-volume joint-cell quadrature from training rows only."""

    if not math.isfinite(float(temperature)) or float(temperature) <= 0.0:
        raise ValueError("Balanced-MSE BNI temperature must be finite and positive")
    if input_columns != split_reference_columns:
        raise ValueError(
            "Balanced-MSE BNI requires input columns to exactly match split-reference columns; "
            "use a separate preregistered loss contract for Q versus Qp/Qs ablations"
        )
    semantics = [_physical_feature_semantic(column) for column in input_columns]
    if semantics != ["lp", "ls", "q", "k"]:
        raise ValueError("Balanced-MSE BNI requires ordered Lp,Ls,Q,|K| input columns")
    if split_audit.get("split_mode") != "physical_cell_grouped":
        raise ValueError("Balanced-MSE BNI requires --split-mode physical_cell_grouped")
    if split_audit.get("physical_cell_range_source") != "explicit":
        raise ValueError("Balanced-MSE BNI requires explicit preregistered physical-cell bounds")

    bins = int(split_audit.get("physical_cell_bins_per_dimension", 0))
    lower = np.asarray(split_audit.get("physical_cell_lower") or [], dtype=float)
    upper = np.asarray(split_audit.get("physical_cell_upper") or [], dtype=float)
    feature_count = len(input_columns)
    if bins < 2 or lower.shape != (feature_count,) or upper.shape != (feature_count,):
        raise ValueError("Balanced-MSE BNI received an incomplete physical-cell split contract")
    if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(upper <= lower):
        raise ValueError("Balanced-MSE BNI physical-cell bounds must be finite with upper > lower")

    split_x = np.asarray(data.get("split_x_physical"), dtype=float)
    train_indices = np.asarray(data["split"]["train"], dtype=int)
    if split_x.ndim != 2 or split_x.shape[1] != feature_count or train_indices.size == 0:
        raise ValueError("Balanced-MSE BNI requires non-empty physical training rows")
    train_values = split_x[train_indices]
    scaled = (train_values - lower[None, :]) / (upper - lower)[None, :]
    if np.any(~np.isfinite(scaled)) or np.any((scaled < 0.0) | (scaled > 1.0)):
        raise ValueError("Balanced-MSE BNI training rows fall outside the declared physical-cell range")
    cell_matrix = np.clip(np.floor(scaled * bins).astype(int), 0, bins - 1)
    occupied_cells, counts = np.unique(cell_matrix, axis=0, return_counts=True)
    if occupied_cells.shape[0] < 2:
        raise ValueError("Balanced-MSE BNI requires at least two occupied training cells")

    centers_physical = lower[None, :] + (occupied_cells + 0.5) * (
        (upper - lower)[None, :] / float(bins)
    )
    x_mean = np.asarray(data["normalization"]["x_mean"], dtype=float)
    x_scale = np.asarray(data["normalization"]["x_scale"], dtype=float)
    if x_mean.shape != (feature_count,) or x_scale.shape != (feature_count,):
        raise ValueError("Balanced-MSE BNI normalization dimensionality is inconsistent")
    if np.any(~np.isfinite(x_scale)) or np.any(x_scale <= 0.0):
        raise ValueError("Balanced-MSE BNI normalization scales must be finite and positive")
    centers_normalized = (centers_physical - x_mean[None, :]) / x_scale[None, :]
    priors = counts.astype(float) / float(np.sum(counts))

    fingerprint_payload = {
        "version": "joint_physical_cell_bni_v1",
        "input_columns": list(input_columns),
        "bins_per_dimension": bins,
        "physical_cell_lower": [float(value) for value in lower],
        "physical_cell_upper": [float(value) for value in upper],
        "occupied_train_cells": occupied_cells.astype(int).tolist(),
        "train_cell_row_counts": counts.astype(int).tolist(),
        "train_split_index_sha256": (split_audit.get("split_index_sha256") or {}).get("train"),
        "temperature": float(temperature),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    state: dict[str, np.ndarray | float | str] = {
        "family": "balanced_mse_bni",
        "temperature": float(temperature),
        "centers_normalized": centers_normalized,
        "centers_physical": centers_physical,
        "priors": priors,
        "cell_indices": occupied_cells.astype(int),
        "cell_row_counts": counts.astype(int),
        "fingerprint_sha256": fingerprint,
    }
    contract = {
        "enabled": True,
        "implementation": "joint_4d_equal_volume_physical_cell_numerical_integration",
        "paper_basis": "Ren et al., Balanced MSE for Imbalanced Visual Regression, CVPR 2022, Eq. 3.16",
        "adaptation_boundary": (
            "The paper presents BNI mainly for one-dimensional labels; this project extends the same "
            "equal-volume numerical-integration idea to the declared joint Lp/Ls/Q/|K| grid."
        ),
        "temperature_tau": float(temperature),
        "temperature_interpretation": "tau=2*sigma^2 in feature-balanced standardized-distance units",
        "prior_source": "empirical_mass_of_training_rows_only_in_equal_volume_declared_cells",
        "center_source": "declared_physical_cell_bin_centers",
        "validation_or_test_rows_used_in_prior": False,
        "input_columns_exactly_match_split_reference_columns": True,
        "bins_per_dimension": bins,
        "occupied_training_cell_count": int(len(counts)),
        "training_row_count": int(np.sum(counts)),
        "training_cell_row_count_min": int(np.min(counts)),
        "training_cell_row_count_median": float(np.median(counts)),
        "training_cell_row_count_max": int(np.max(counts)),
        "prior_sum": float(np.sum(priors)),
        "fingerprint_sha256": fingerprint,
        "selection_boundary": (
            "Temperature must be selected with validation cells only. Test cells remain untouched until the "
            "single preregistered MSE-versus-BNI comparison."
        ),
    }
    return state, contract


def _configure_training_batch_sampler(
    data: dict[str, Any],
    split_reference_columns: list[str],
    split_audit: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an auditable train-only batch-sampling contract."""

    family = str(getattr(args, "training_batch_sampler", "row_uniform"))
    train_indices = np.asarray(data["split"]["train"], dtype=int)
    validation_indices = np.asarray(data["split"]["validation"], dtype=int)
    test_indices = np.asarray(data["split"]["test"], dtype=int)
    row_count = int(np.asarray(data["x"]).shape[0])
    if train_indices.ndim != 1 or train_indices.size == 0:
        raise ValueError("training batch sampler requires a non-empty one-dimensional train split")
    if np.any(train_indices < 0) or np.any(train_indices >= row_count):
        raise ValueError("training batch sampler received out-of-range train indices")
    if np.unique(train_indices).size != train_indices.size:
        raise ValueError("training batch sampler requires unique train-split indices")
    if np.intersect1d(train_indices, validation_indices).size or np.intersect1d(train_indices, test_indices).size:
        raise ValueError("training batch sampler detected validation/test leakage into the train split")

    recorded_train_sha = str((split_audit.get("split_index_sha256") or {}).get("train") or "")
    computed_train_sha = hashlib.sha256(np.asarray(train_indices, dtype=np.int64).tobytes()).hexdigest()
    if recorded_train_sha and recorded_train_sha != computed_train_sha:
        raise ValueError("training batch sampler train-split SHA does not match the split audit")

    batch_size = max(1, int(args.batch_size))
    draws_per_epoch = int(train_indices.size)
    optimizer_updates_per_epoch = int(math.ceil(draws_per_epoch / float(batch_size)))
    common_payload: dict[str, Any] = {
        "version": "train_only_physical_cell_sampler_v2",
        "family": family,
        "exact_update_batch_mode": str(args.exact_update_batch_mode),
        "training_row_count": draws_per_epoch,
        "draws_per_epoch": draws_per_epoch,
        "batch_size": batch_size,
        "optimizer_updates_per_epoch": optimizer_updates_per_epoch,
        "train_split_index_sha256": computed_train_sha,
        "model_seed": int(args.seed),
        "forward_sampler_seed": int(args.seed),
        "inverse_sampler_seed": int(args.seed) + 1,
    }

    if family == "row_uniform":
        fingerprint = _json_fingerprint(common_payload)
        state = {
            "family": family,
            "train_indices": train_indices.copy(),
            "draws_per_epoch": draws_per_epoch,
            "optimizer_updates_per_epoch": optimizer_updates_per_epoch,
            "fingerprint_sha256": fingerprint,
        }
        contract = {
            **common_payload,
            "enabled": False,
            "implementation": (
                "continuous_random_permutations_with_fixed_full_batches"
                if args.exact_update_batch_mode == "continuous_permutation_full_batch"
                else "one_random_permutation_of_every_real_train_row_per_epoch"
            ),
            "train_only": True,
            "validation_or_test_rows_eligible_for_sampling": False,
            "synthetic_rows_created": False,
            "sampling_with_replacement": False,
            "all_exact_update_batches_have_configured_size": (
                args.exact_update_batch_mode == "continuous_permutation_full_batch"
            ),
            "permutation_boundary_may_occur_inside_batch": (
                args.exact_update_batch_mode == "continuous_permutation_full_batch"
            ),
            "occupied_training_cell_count": None,
            "target_cell_draw_count_difference_max_per_epoch": None,
            "fingerprint_sha256": fingerprint,
            "interpretation_boundary": (
                "Continuous mode concatenates complete random permutations and therefore gives every row one "
                "exposure per completed permutation plus at most one exposure in the active prefix. It balances "
                "neither occupied physical cells nor the full declared 4-D grid."
                if args.exact_update_batch_mode == "continuous_permutation_full_batch"
                else "This is the unchanged row-uniform baseline. It balances neither occupied physical cells nor "
                "the full declared 4-D grid."
            ),
        }
        return state, contract

    if family != "joint_cell_balanced":
        raise ValueError(f"unsupported training batch sampler: {family}")
    if split_audit.get("split_mode") != "physical_cell_grouped":
        raise ValueError("joint-cell-balanced sampling requires --split-mode physical_cell_grouped")
    if split_audit.get("physical_cell_range_source") != "explicit":
        raise ValueError("joint-cell-balanced sampling requires explicit preregistered physical-cell bounds")
    semantics = [_physical_feature_semantic(column) for column in split_reference_columns]
    if semantics != ["lp", "ls", "q", "k"]:
        raise ValueError("joint-cell-balanced sampling requires ordered Lp,Ls,Q,|K| split-reference columns")

    bins = int(split_audit.get("physical_cell_bins_per_dimension", 0))
    lower = np.asarray(split_audit.get("physical_cell_lower") or [], dtype=float)
    upper = np.asarray(split_audit.get("physical_cell_upper") or [], dtype=float)
    feature_count = len(split_reference_columns)
    if bins < 2 or lower.shape != (feature_count,) or upper.shape != (feature_count,):
        raise ValueError("joint-cell-balanced sampling received an incomplete physical-cell split contract")
    if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(upper <= lower):
        raise ValueError("joint-cell-balanced sampling bounds must be finite with upper > lower")

    split_x = np.asarray(data.get("split_x_physical"), dtype=float)
    if split_x.ndim != 2 or split_x.shape != (row_count, feature_count):
        raise ValueError("joint-cell-balanced sampling physical rows do not match the split contract")
    train_values = split_x[train_indices]
    scaled = (train_values - lower[None, :]) / (upper - lower)[None, :]
    if np.any(~np.isfinite(scaled)) or np.any((scaled < 0.0) | (scaled > 1.0)):
        raise ValueError("joint-cell-balanced sampling found train rows outside the declared physical range")
    cell_matrix = np.clip(np.floor(scaled * bins).astype(int), 0, bins - 1)
    occupied_cells, inverse_cell, cell_row_counts = np.unique(
        cell_matrix,
        axis=0,
        return_inverse=True,
        return_counts=True,
    )
    if occupied_cells.shape[0] < 2:
        raise ValueError("joint-cell-balanced sampling requires at least two occupied training cells")
    row_indices_by_cell = tuple(
        train_indices[np.flatnonzero(inverse_cell == cell_index)].copy()
        for cell_index in range(occupied_cells.shape[0])
    )
    if sum(len(indices) for indices in row_indices_by_cell) != draws_per_epoch:
        raise ValueError("joint-cell-balanced sampling did not partition every train row exactly once")

    sampler_payload = {
        **common_payload,
        "physical_cell_bins_per_dimension": bins,
        "physical_cell_lower": [float(value) for value in lower],
        "physical_cell_upper": [float(value) for value in upper],
        "occupied_training_cells": occupied_cells.astype(int).tolist(),
        "training_cell_row_counts": cell_row_counts.astype(int).tolist(),
        "target_cell_draw_count_difference_max_per_epoch": 1,
    }
    fingerprint = _json_fingerprint(sampler_payload)
    state = {
        "family": family,
        "train_indices": train_indices.copy(),
        "occupied_cells": occupied_cells.astype(int),
        "cell_row_counts": cell_row_counts.astype(int),
        "row_indices_by_cell": row_indices_by_cell,
        "draws_per_epoch": draws_per_epoch,
        "optimizer_updates_per_epoch": optimizer_updates_per_epoch,
        "fingerprint_sha256": fingerprint,
    }
    contract = {
        **sampler_payload,
        "enabled": True,
        "implementation": "equal_draws_across_occupied_joint_4d_train_cells_with_real_row_replacement",
        "train_only": True,
        "validation_or_test_rows_eligible_for_sampling": False,
        "synthetic_rows_created": False,
        "sampling_with_replacement": True,
        "occupied_training_cell_count": int(occupied_cells.shape[0]),
        "training_cell_row_count_min": int(np.min(cell_row_counts)),
        "training_cell_row_count_median": float(np.median(cell_row_counts)),
        "training_cell_row_count_max": int(np.max(cell_row_counts)),
        "fingerprint_sha256": fingerprint,
        "interpretation_boundary": (
            "Each epoch draws the same total number of real train rows and optimizer updates as the row-uniform "
            "baseline, while equalizing draws only across occupied train cells. Empty declared cells remain empty, "
            "and validation/test cells are never sampled."
        ),
    }
    return state, contract


def _json_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _configure_optimizer_budget_contract(
    sampler_contract: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    updates_per_epoch = int(sampler_contract["optimizer_updates_per_epoch"])
    if updates_per_epoch < 1:
        raise ValueError("optimizer budget requires a positive updates-per-epoch contract")
    forward_target = int(getattr(args, "forward_max_optimizer_updates", 0))
    inverse_target = int(getattr(args, "inverse_max_optimizer_updates", 0))
    freeze_forward = bool(getattr(args, "freeze_transported_forward", False))
    exact_update_mode = freeze_forward or forward_target > 0 or inverse_target > 0
    if exact_update_mode:
        if freeze_forward:
            if forward_target != 0 or inverse_target < 1:
                raise ValueError(
                    "a frozen transported forward requires zero forward updates and positive inverse updates"
                )
        elif forward_target < 1 or inverse_target < 1:
            raise ValueError("forward and inverse exact optimizer-update budgets must be positive")
        validation_interval = int(args.validation_every_optimizer_updates)
        if validation_interval < 1:
            raise ValueError("exact optimizer-update budgets require a positive validation interval")
        mode = "fixed_optimizer_updates"
        early_stopping_enabled = False
        response_schedule_domain = "optimizer_update"
    else:
        validation_interval = updates_per_epoch
        mode = "fixed_epoch_protocol_with_validation_patience"
        early_stopping_enabled = True
        response_schedule_domain = "epoch"
        forward_target = int(args.forward_epochs) * updates_per_epoch
        inverse_target = int(args.inverse_epochs) * updates_per_epoch
    response_schedule = _init_response_schedule_state(args)
    payload = {
        "schema": "physical_feature_optimizer_budget_v2",
        "mode": mode,
        "exact_update_batch_mode": str(args.exact_update_batch_mode),
        "updates_per_full_training_epoch": updates_per_epoch,
        "validation_every_optimizer_updates": validation_interval,
        "early_stopping_enabled": early_stopping_enabled,
        "response_schedule_domain": response_schedule_domain,
        "response_schedule": {
            "weight_schedule": str(args.response_weight_schedule),
            "unit_source": str(response_schedule["unit_source"]),
            "total_units": int(response_schedule["total_units"]),
            "warmup_units": int(response_schedule["warmup_units"]),
            "ramp_units": int(response_schedule["ramp_units"]),
        },
        "forward": {
            "configured_epoch_limit": int(args.forward_epochs),
            "target_optimizer_updates": forward_target,
            "target_real_row_draws": (
                forward_target * int(args.batch_size)
                if exact_update_mode
                and args.exact_update_batch_mode == "continuous_permutation_full_batch"
                else None
            ),
            "frozen_transported_checkpoint": freeze_forward,
        },
        "inverse": {
            "configured_epoch_limit": int(args.inverse_epochs),
            "target_optimizer_updates": inverse_target,
            "target_real_row_draws": (
                inverse_target * int(args.batch_size)
                if exact_update_mode
                and args.exact_update_batch_mode == "continuous_permutation_full_batch"
                else None
            ),
        },
        "interpretation_boundary": (
            "fixed_optimizer_updates cycles only real train rows, validates at a fixed update cadence, and "
            "selects the best checkpoint using validation data after completing the exact budget. In continuous "
            "full-batch mode, every update also has the identical configured row-draw count across arms; repeated "
            "row exposure is not additional independent data."
            if exact_update_mode
            else "fixed epochs preserve the engineering training protocol, but realized optimizer updates may "
            "differ across dataset sizes and may stop early through validation patience."
        ),
    }
    payload["fingerprint_sha256"] = _json_fingerprint(payload)
    return payload


def _record_realized_training_sampler_budget(
    contract: dict[str, Any],
    forward_history: list[dict[str, Any]],
    inverse_history: list[dict[str, Any]],
) -> None:
    updates_per_epoch = int(contract["optimizer_updates_per_epoch"])
    draws_per_epoch = int(contract["draws_per_epoch"])
    forward_epochs = int(forward_history[-1]["epoch"]) if forward_history else 0
    inverse_epochs = int(inverse_history[-1]["epoch"]) if inverse_history else 0
    forward_draws = (
        int(forward_history[-1]["real_row_draws"])
        if forward_history and "real_row_draws" in forward_history[-1]
        else forward_epochs * draws_per_epoch
    )
    inverse_draws = (
        int(inverse_history[-1]["real_row_draws"])
        if inverse_history and "real_row_draws" in inverse_history[-1]
        else inverse_epochs * draws_per_epoch
    )
    forward_updates = (
        int(forward_history[-1]["optimizer_updates"])
        if forward_history and "optimizer_updates" in forward_history[-1]
        else forward_epochs * updates_per_epoch
    )
    inverse_updates = (
        int(inverse_history[-1]["optimizer_updates"])
        if inverse_history and "optimizer_updates" in inverse_history[-1]
        else inverse_epochs * updates_per_epoch
    )
    training_row_count = int(contract.get("training_row_count") or draws_per_epoch)
    batch_size = int(contract.get("batch_size") or 0)
    continuous_full_batch = (
        contract.get("exact_update_batch_mode") == "continuous_permutation_full_batch"
    )

    def exposure_audit(real_draws: int, updates: int) -> dict[str, Any]:
        complete, prefix = divmod(int(real_draws), training_row_count)
        return {
            "complete_random_permutations": int(complete),
            "active_permutation_prefix_rows": int(prefix),
            "minimum_row_exposure": int(complete),
            "maximum_row_exposure": int(complete + (1 if prefix else 0)),
            "maximum_minus_minimum_row_exposure": int(1 if prefix else 0),
            "every_optimizer_batch_has_configured_size": (
                int(real_draws) == int(updates) * batch_size
                if continuous_full_batch
                else None
            ),
        }

    contract["realized_training_budget"] = {
        "forward_epochs": forward_epochs,
        "forward_real_row_draws": forward_draws,
        "forward_optimizer_updates": forward_updates,
        "forward_continuous_permutation_exposure": exposure_audit(
            forward_draws, forward_updates
        )
        if continuous_full_batch
        else None,
        "inverse_epochs": inverse_epochs,
        "inverse_real_row_draws": inverse_draws,
        "inverse_optimizer_updates": inverse_updates,
        "inverse_continuous_permutation_exposure": exposure_audit(
            inverse_draws, inverse_updates
        )
        if continuous_full_batch
        else None,
        "total_real_row_draws": forward_draws + inverse_draws,
        "total_optimizer_updates": forward_updates + inverse_updates,
        "continuous_full_batch_contract": continuous_full_batch,
    }


def _record_optimizer_budget_realization(
    contract: dict[str, Any],
    realized: dict[str, Any],
) -> None:
    forward_realized = int(realized.get("forward_optimizer_updates") or 0)
    inverse_realized = int(realized.get("inverse_optimizer_updates") or 0)
    forward_target = int((contract.get("forward") or {}).get("target_optimizer_updates") or 0)
    inverse_target = int((contract.get("inverse") or {}).get("target_optimizer_updates") or 0)
    exact_mode = contract.get("mode") == "fixed_optimizer_updates"
    contract["realized"] = {
        "forward_optimizer_updates": forward_realized,
        "inverse_optimizer_updates": inverse_realized,
        "forward_target_exactly_met": forward_realized == forward_target,
        "inverse_target_exactly_met": inverse_realized == inverse_target,
        "exact_update_budget_pass": (
            forward_realized == forward_target and inverse_realized == inverse_target
            if exact_mode
            else None
        ),
    }


def _strip_physical_feature_provenance(column: str) -> str:
    name = str(column).strip().lower()
    for prefix in ("input__", "phys__", "aux__", "aux_"):
        name = name.removeprefix(prefix)
    return name


def _physical_feature_semantic(column: str) -> str:
    name = _strip_physical_feature_provenance(column)
    if name.startswith("lp"):
        return "lp"
    if name.startswith("ls"):
        return "ls"
    if name.startswith(("q", "qp", "qs")):
        return "q"
    if name.startswith("k") or "kw" in name:
        return "k"
    return ""


def _inverse_loss_name(args: argparse.Namespace) -> str:
    family = getattr(args, "response_loss_family", "mse")
    if family == "balanced_mse_bni":
        response_component = "joint_physical_cell_balanced_mse_bni_response_consistency"
    elif family == "relative_mse":
        response_component = (
            "physical_relative_mse_q_minimum_response_consistency"
            if getattr(args, "q_target_semantics", "exact") == "minimum"
            else "physical_relative_mse_response_consistency"
        )
    else:
        response_component = "feature_balanced_response_consistency"
    components = [response_component]
    if float(args.geometry_anchor_weight) > 0.0:
        components.append("geometry_anchor")
    if float(args.topology_feasibility_weight) > 0.0:
        components.append("label_free_topology_feasibility")
    return "feature_balanced_response_consistency_only" if len(components) == 1 else "_plus_".join(components)


def _topology_feasibility_column_contract(
    geometry_columns: list[str],
    *,
    enforce_power_line_port_ground_overlap: bool = False,
) -> dict[str, Any]:
    required_semantics = list(TOPOLOGY_GEOMETRY_SEMANTICS)
    if enforce_power_line_port_ground_overlap:
        required_semantics.extend(POWER_LINE_PORT_GROUND_OVERLAP_SEMANTICS)
    required_semantic_set = set(required_semantics)
    index_by_semantic: dict[str, int] = {}
    source_by_semantic: dict[str, str] = {}
    for index, column in enumerate(geometry_columns):
        semantic = column.lower().split("__")[-1]
        if semantic in required_semantic_set and semantic not in index_by_semantic:
            index_by_semantic[semantic] = int(index)
            source_by_semantic[semantic] = column
    missing = [name for name in required_semantics if name not in index_by_semantic]
    return {
        "available": not missing,
        "required_semantics": required_semantics,
        "missing_semantics": missing,
        "index_by_semantic": index_by_semantic,
        "source_by_semantic": source_by_semantic,
    }


def _configure_topology_feasibility(
    normalization: dict[str, Any],
    geometry_columns: list[str],
    column_contract: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    y_scale = np.asarray(normalization["y_scale"], dtype=float)
    lower = np.asarray(normalization["geometry_lower"], dtype=float)
    upper = np.asarray(normalization["geometry_upper"], dtype=float)
    physical_spans = np.maximum(np.abs(upper - lower) * np.abs(y_scale), 1.0e-6)
    index_by_semantic = {
        str(name): int(index) for name, index in (column_contract.get("index_by_semantic") or {}).items()
    }
    scale_by_semantic = {
        name: float(max(physical_spans[index], 1.0))
        for name, index in index_by_semantic.items()
    }
    enforce_power_line_port_ground_overlap = bool(
        getattr(args, "enforce_power_line_port_ground_overlap", False)
    )
    constraint_names = [
        "primary_terminal_within_outer_width",
        "primary_terminal_within_outer_height",
        "primary_terminal_within_feed_side_straight_section",
        "secondary_terminal_within_outer_width",
        "secondary_terminal_within_outer_height",
        "secondary_terminal_within_feed_side_straight_section",
        "offset_within_primary_feed_support",
        "offset_within_secondary_feed_support",
    ]
    if enforce_power_line_port_ground_overlap:
        constraint_names.extend(
            [
                "primary_signal_port_ground_overlap_reachable",
                "secondary_signal_port_ground_overlap_reachable",
            ]
        )
    return {
        "enabled": float(args.topology_feasibility_weight) > 0.0,
        "weight": float(args.topology_feasibility_weight),
        "available": bool(column_contract.get("available")),
        "geometry_columns": list(geometry_columns),
        "required_semantics": list(column_contract.get("required_semantics") or []),
        "missing_semantics": list(column_contract.get("missing_semantics") or []),
        "index_by_semantic": index_by_semantic,
        "source_by_semantic": dict(column_contract.get("source_by_semantic") or {}),
        "scale_by_semantic_um": scale_by_semantic,
        "constraint_names": constraint_names,
        "power_line_port_ground_overlap": {
            "enabled": enforce_power_line_port_ground_overlap,
            "bar_offset_um": float(
                getattr(args, "power_line_bar_offset_um", 12.0)
            ),
            "shield_opening_clearance_um": float(
                getattr(args, "power_line_shield_opening_clearance_um", 10.0)
            ),
            "expected_overlap_um": float(
                getattr(args, "power_line_port_ground_overlap_um", 10.0)
            ),
            "training_safety_margin_um": float(
                getattr(args, "power_line_feed_training_safety_margin_um", 0.0)
            ),
            "expected_overlap_cancels_from_minimum_feed_extension": True,
            "constraint_scope": (
                "one_turn_shared_line_width_signal_ports_with_shield_reference"
            ),
        },
        "scientific_boundary": (
            "This differentiable penalty is label-free and mirrors the coupled terminal-span/feed-support checks in "
            "TransformerSpec.validate(). When explicitly enabled, it also mirrors the analytical minimum feed length "
            "needed for the one-turn shared-width signal ports to reach the shield opening with the declared ground "
            "overlap. It does not cover the full TSMC65 geometry audit, foundry signoff DRC, EMX, HFSS, ADS, or "
            "measurement."
        ),
    }


def _topology_feasibility_penalty_and_gradient(
    geometry_normalized: np.ndarray,
    normalization: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[float, np.ndarray, dict[str, Any]]:
    geometry = np.asarray(geometry_normalized, dtype=float)
    gradient = np.zeros_like(geometry)
    constraint_names = list(contract.get("constraint_names") or [])
    if geometry.ndim != 2:
        raise ValueError("topology feasibility expects a 2-D geometry matrix")
    if geometry.shape[0] == 0 or not contract.get("available"):
        return 0.0, gradient, {
            "available": bool(contract.get("available")),
            "row_count": int(geometry.shape[0]),
            "constraint_count": len(constraint_names),
            "penalty_mse": 0.0,
            "violation_fraction": 0.0,
            "max_normalized_violation": 0.0,
            "per_constraint": {},
        }

    y_mean = np.asarray(normalization["y_mean"], dtype=float)
    y_scale = np.asarray(normalization["y_scale"], dtype=float)
    if geometry.shape[1] != y_mean.size or y_mean.shape != y_scale.shape:
        raise ValueError("topology feasibility normalization shape mismatch")
    physical = geometry * y_scale[None, :] + y_mean[None, :]
    index = {str(name): int(value) for name, value in (contract.get("index_by_semantic") or {}).items()}
    scales = {str(name): float(value) for name, value in (contract.get("scale_by_semantic_um") or {}).items()}
    constraints: list[tuple[str, np.ndarray, float, dict[int, np.ndarray]]] = []

    def add_constraint(
        name: str,
        residual: np.ndarray,
        scale_semantic: str,
        derivatives: dict[int, np.ndarray],
    ) -> None:
        constraints.append((name, residual, max(scales.get(scale_semantic, 1.0), 1.0e-6), derivatives))

    chamfer_factor = math.sqrt(2.0) - 1.0
    for role in ("primary", "secondary"):
        width_index = index[f"{role}_outer_width_um"]
        height_index = index[f"{role}_outer_height_um"]
        terminal_index = index[f"{role}_terminal_y_span_um"]
        width = physical[:, width_index]
        height = physical[:, height_index]
        terminal = physical[:, terminal_index]
        ones = np.ones_like(terminal)
        add_constraint(
            f"{role}_terminal_within_outer_width",
            terminal - width,
            f"{role}_terminal_y_span_um",
            {terminal_index: ones, width_index: -ones},
        )
        add_constraint(
            f"{role}_terminal_within_outer_height",
            terminal - height,
            f"{role}_terminal_y_span_um",
            {terminal_index: ones, height_index: -ones},
        )
        width_is_minimum = width <= height
        max_terminal_span = height - chamfer_factor * np.minimum(width, height)
        d_residual_d_width = np.where(width_is_minimum, chamfer_factor, 0.0)
        d_residual_d_height = np.where(width_is_minimum, -1.0, chamfer_factor - 1.0)
        add_constraint(
            f"{role}_terminal_within_feed_side_straight_section",
            terminal - max_terminal_span,
            f"{role}_terminal_y_span_um",
            {
                terminal_index: ones,
                width_index: d_residual_d_width,
                height_index: d_residual_d_height,
            },
        )

    offset_index = index["offset_um"]
    primary_feed_index = index["primary_feed_extension_um"]
    secondary_feed_index = index["secondary_feed_extension_um"]
    offset = physical[:, offset_index]
    primary_feed = physical[:, primary_feed_index]
    secondary_feed = physical[:, secondary_feed_index]
    ones = np.ones_like(offset)
    add_constraint(
        "offset_within_primary_feed_support",
        -primary_feed - offset,
        "offset_um",
        {primary_feed_index: -ones, offset_index: -ones},
    )
    add_constraint(
        "offset_within_secondary_feed_support",
        offset - secondary_feed,
        "offset_um",
        {offset_index: ones, secondary_feed_index: -ones},
    )

    power_line_contract = contract.get("power_line_port_ground_overlap") or {}
    if bool(power_line_contract.get("enabled")):
        primary_width_index = index["primary_outer_width_um"]
        secondary_width_index = index["secondary_outer_width_um"]
        line_width_index = index["line_width_um"]
        primary_width = physical[:, primary_width_index]
        secondary_width = physical[:, secondary_width_index]
        line_width = physical[:, line_width_index]
        bar_offset = float(power_line_contract["bar_offset_um"])
        opening_clearance = float(
            power_line_contract["shield_opening_clearance_um"]
        )
        training_safety_margin = float(
            power_line_contract.get("training_safety_margin_um") or 0.0
        )
        fixed_margin = (
            bar_offset
            + opening_clearance
            + training_safety_margin
        )

        primary_own_left = -0.5 * primary_width
        secondary_left = offset - 0.5 * secondary_width
        secondary_defines_left_union = secondary_left < primary_own_left
        left_union = np.minimum(primary_own_left, secondary_left)
        add_constraint(
            "primary_signal_port_ground_overlap_reachable",
            (
                primary_own_left
                - left_union
                + fixed_margin
                + line_width
                - primary_feed
            ),
            "primary_feed_extension_um",
            {
                primary_width_index: np.where(
                    secondary_defines_left_union,
                    -0.5,
                    0.0,
                ),
                secondary_width_index: np.where(
                    secondary_defines_left_union,
                    0.5,
                    0.0,
                ),
                offset_index: np.where(
                    secondary_defines_left_union,
                    -1.0,
                    0.0,
                ),
                line_width_index: ones,
                primary_feed_index: -ones,
            },
        )

        secondary_own_right = offset + 0.5 * secondary_width
        primary_right = 0.5 * primary_width
        primary_defines_right_union = primary_right > secondary_own_right
        right_union = np.maximum(primary_right, secondary_own_right)
        add_constraint(
            "secondary_signal_port_ground_overlap_reachable",
            (
                right_union
                - secondary_own_right
                + fixed_margin
                + line_width
                - secondary_feed
            ),
            "secondary_feed_extension_um",
            {
                primary_width_index: np.where(
                    primary_defines_right_union,
                    0.5,
                    0.0,
                ),
                secondary_width_index: np.where(
                    primary_defines_right_union,
                    -0.5,
                    0.0,
                ),
                offset_index: np.where(
                    primary_defines_right_union,
                    -1.0,
                    0.0,
                ),
                line_width_index: ones,
                secondary_feed_index: -ones,
            },
        )

    denominator = max(1, geometry.shape[0] * len(constraints))
    loss_sum = 0.0
    active_count = 0
    max_normalized_violation = 0.0
    per_constraint: dict[str, Any] = {}
    gradient_physical = np.zeros_like(physical)
    for name, residual, scale, derivatives in constraints:
        positive = np.maximum(residual, 0.0)
        normalized = positive / scale
        active = positive > 0.0
        loss_sum += float(np.sum(normalized**2))
        active_count += int(np.count_nonzero(active))
        max_normalized_violation = max(max_normalized_violation, float(np.max(normalized)))
        coefficient = 2.0 * positive / (scale * scale * denominator)
        for column_index, derivative in derivatives.items():
            gradient_physical[:, column_index] += coefficient * derivative
        per_constraint[name] = {
            "scale_um": float(scale),
            "violation_count": int(np.count_nonzero(active)),
            "violation_fraction": float(np.mean(active)),
            "max_violation_um": float(np.max(positive)),
            "max_normalized_violation": float(np.max(normalized)),
        }
    penalty = loss_sum / denominator
    gradient = gradient_physical * y_scale[None, :]
    return penalty, gradient, {
        "available": True,
        "row_count": int(geometry.shape[0]),
        "constraint_count": len(constraints),
        "penalty_mse": float(penalty),
        "violation_count": int(active_count),
        "violation_fraction": float(active_count / denominator),
        "max_normalized_violation": float(max_normalized_violation),
        "per_constraint": per_constraint,
    }


def _resolve_hidden_widths(
    specification: str | None,
    *,
    depth: int,
    width: int,
) -> list[int]:
    if specification is None or not str(specification).strip():
        if depth < 0:
            raise ValueError("depth must be nonnegative")
        if width < 1:
            raise ValueError("uniform width must be positive")
        return [int(width)] * int(depth)
    pieces = [item.strip() for item in str(specification).split(",")]
    if not pieces or any(not item for item in pieces):
        raise ValueError("expected a nonempty comma-separated integer list")
    try:
        values = [int(item) for item in pieces]
    except ValueError as exc:
        raise ValueError("all hidden widths must be integers") from exc
    if any(value < 1 for value in values):
        raise ValueError("all hidden widths must be positive")
    return values


def _configure_forward_initialization(
    *,
    args: argparse.Namespace,
    input_columns: list[str],
    geometry_columns: list[str],
    forward_hidden_widths: list[int],
    target_normalization: dict[str, Any],
) -> tuple[list[np.ndarray] | None, list[np.ndarray] | None, dict[str, Any]]:
    mode = str(args.forward_initialization_mode)
    if mode == "random":
        return None, None, {
            "schema": "forward_proxy_initialization_v1",
            "overall_status": "PASS",
            "mode": "random",
            "source_artifact_used": False,
            "normalization_transport_applied": False,
            "physical_prediction_equivalence_checked": False,
            "scientific_boundary": (
                "Random initialization is the control arm for equal-data, equal-split, equal-budget "
                "comparison with transported source initialization."
            ),
        }

    weights_path = Path(str(args.forward_initial_weights)).expanduser().resolve()
    summary_path = Path(str(args.forward_initial_summary)).expanduser().resolve()
    expected_weights_sha256 = str(args.forward_initial_weights_sha256).strip().lower()
    expected_summary_sha256 = str(args.forward_initial_summary_sha256).strip().lower()
    if not weights_path.is_file():
        raise RuntimeError(f"source forward weights do not exist: {weights_path}")
    if not summary_path.is_file():
        raise RuntimeError(f"source forward summary does not exist: {summary_path}")
    actual_weights_sha256 = _sha256_file(weights_path)
    actual_summary_sha256 = _sha256_file(summary_path)
    if actual_weights_sha256 != expected_weights_sha256:
        raise RuntimeError("source forward weights SHA-256 mismatch")
    if actual_summary_sha256 != expected_summary_sha256:
        raise RuntimeError("source forward summary SHA-256 mismatch")

    try:
        source_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("source forward summary is unreadable") from exc
    if not isinstance(source_summary, dict):
        raise RuntimeError("source forward summary must be a JSON object")
    if str(source_summary.get("weights_npz_sha256") or "") != actual_weights_sha256:
        raise RuntimeError("source summary does not bind the supplied weights SHA-256")

    source_input_columns = [str(value) for value in source_summary.get("input_columns") or []]
    source_geometry_columns = [str(value) for value in source_summary.get("geometry_columns") or []]
    source_input_semantics = [
        _strip_physical_feature_provenance(value) for value in source_input_columns
    ]
    target_input_semantics = [
        _strip_physical_feature_provenance(value) for value in input_columns
    ]
    if source_input_semantics != target_input_semantics:
        raise RuntimeError("source and target physical-feature column order does not match")
    if source_geometry_columns != list(geometry_columns):
        raise RuntimeError("source and target geometry column order does not match")

    expected_sizes = [
        len(geometry_columns),
        *[int(value) for value in forward_hidden_widths],
        len(input_columns),
    ]
    source_architecture = (
        source_summary.get("model_comparison_contract", {}).get("architecture", {})
    )
    if list(source_architecture.get("forward_hidden_widths") or []) != [
        int(value) for value in forward_hidden_widths
    ]:
        raise RuntimeError("source and target forward hidden-width contracts do not match")

    required_normalization_keys = ("x_mean", "x_scale", "y_mean", "y_scale")
    try:
        with np.load(weights_path, allow_pickle=False) as arrays:
            weight_keys = sorted(
                (key for key in arrays.files if key.startswith("forward_weight_")),
                key=lambda key: int(key.removeprefix("forward_weight_")),
            )
            bias_keys = sorted(
                (key for key in arrays.files if key.startswith("forward_bias_")),
                key=lambda key: int(key.removeprefix("forward_bias_")),
            )
            source_weights = [
                np.asarray(arrays[key], dtype=float).copy() for key in weight_keys
            ]
            source_biases = [
                np.asarray(arrays[key], dtype=float).copy() for key in bias_keys
            ]
            source_normalization = {
                key: np.asarray(arrays[f"normalization__{key}"], dtype=float).copy()
                for key in required_normalization_keys
            }
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError("source forward weights artifact is incomplete or unreadable") from exc

    _validate_tandem_stage_network(
        source_weights,
        source_biases,
        expected_input_dim=len(geometry_columns),
        expected_output_dim=len(input_columns),
    )
    actual_sizes = [source_weights[0].shape[0]] + [
        weight.shape[1] for weight in source_weights
    ]
    if actual_sizes != expected_sizes:
        raise RuntimeError("source forward network layer shapes do not match the target architecture")

    target_norm = {
        key: np.asarray(target_normalization[key], dtype=float).copy()
        for key in required_normalization_keys
    }
    expected_shapes = {
        "x_mean": (len(input_columns),),
        "x_scale": (len(input_columns),),
        "y_mean": (len(geometry_columns),),
        "y_scale": (len(geometry_columns),),
    }
    for label, normalization in (
        ("source", source_normalization),
        ("target", target_norm),
    ):
        for key, expected_shape in expected_shapes.items():
            value = normalization[key]
            if value.shape != expected_shape or np.any(~np.isfinite(value)):
                raise RuntimeError(
                    f"{label} normalization {key} is non-finite or has the wrong shape"
                )
        if np.any(normalization["x_scale"] <= 0.0) or np.any(
            normalization["y_scale"] <= 0.0
        ):
            raise RuntimeError(f"{label} normalization scales must be positive")

    transported_weights, transported_biases, transport = (
        _transport_forward_network_normalization(
            source_weights=source_weights,
            source_biases=source_biases,
            source_normalization=source_normalization,
            target_normalization=target_norm,
        )
    )
    _validate_tandem_stage_network(
        transported_weights,
        transported_biases,
        expected_input_dim=len(geometry_columns),
        expected_output_dim=len(input_columns),
    )
    return transported_weights, transported_biases, {
        "schema": "forward_proxy_initialization_v1",
        "overall_status": "PASS",
        "mode": "transported_source_finetune",
        "source_artifact_used": True,
        "source_weights": str(weights_path),
        "source_weights_sha256": actual_weights_sha256,
        "source_summary": str(summary_path),
        "source_summary_sha256": actual_summary_sha256,
        "source_input_columns": source_input_columns,
        "target_input_columns": list(input_columns),
        "input_semantic_order": target_input_semantics,
        "geometry_columns": list(geometry_columns),
        "forward_layer_sizes": actual_sizes,
        "normalization_transport_applied": True,
        "physical_prediction_equivalence_checked": True,
        "normalization_transport": transport,
        "scientific_boundary": (
            "Only the source forward weights are transported into the current train-split normalization. "
            "All fine-tuning rows, validation/test cells, labels, and acceptance evidence remain current-"
            "foundry real EMX."
        ),
    }


def _transport_forward_network_normalization(
    *,
    source_weights: list[np.ndarray],
    source_biases: list[np.ndarray],
    source_normalization: dict[str, np.ndarray],
    target_normalization: dict[str, np.ndarray],
) -> tuple[list[np.ndarray], list[np.ndarray], dict[str, Any]]:
    source_x_mean = np.asarray(source_normalization["x_mean"], dtype=float)
    source_x_scale = np.asarray(source_normalization["x_scale"], dtype=float)
    source_y_mean = np.asarray(source_normalization["y_mean"], dtype=float)
    source_y_scale = np.asarray(source_normalization["y_scale"], dtype=float)
    target_x_mean = np.asarray(target_normalization["x_mean"], dtype=float)
    target_x_scale = np.asarray(target_normalization["x_scale"], dtype=float)
    target_y_mean = np.asarray(target_normalization["y_mean"], dtype=float)
    target_y_scale = np.asarray(target_normalization["y_scale"], dtype=float)

    geometry_scale = target_y_scale / source_y_scale
    geometry_shift = (target_y_mean - source_y_mean) / source_y_scale
    response_scale = source_x_scale / target_x_scale
    response_shift = (source_x_mean - target_x_mean) / target_x_scale

    weights = [np.asarray(value, dtype=float).copy() for value in source_weights]
    biases = [np.asarray(value, dtype=float).copy() for value in source_biases]
    if len(weights) == 1:
        source_weight = weights[0].copy()
        source_bias = biases[0].copy()
        weights[0] = (
            geometry_scale[:, None]
            * source_weight
            * response_scale[None, :]
        )
        biases[0] = (
            (geometry_shift @ source_weight + source_bias) * response_scale
            + response_shift
        )
    else:
        weights[0] = geometry_scale[:, None] * weights[0]
        biases[0] = geometry_shift @ source_weights[0] + source_biases[0]
        weights[-1] = weights[-1] * response_scale[None, :]
        biases[-1] = biases[-1] * response_scale + response_shift

    probe_rng = np.random.default_rng(20260728)
    target_geometry_normalized = np.vstack(
        [
            np.zeros((1, source_y_mean.size), dtype=float),
            np.eye(source_y_mean.size, dtype=float),
            -np.eye(source_y_mean.size, dtype=float),
            probe_rng.normal(0.0, 1.0, size=(32, source_y_mean.size)),
        ]
    )
    source_geometry_normalized = (
        target_geometry_normalized * geometry_scale[None, :]
        + geometry_shift[None, :]
    )
    source_response_normalized = _predict(
        source_geometry_normalized,
        source_weights,
        source_biases,
    )
    expected_target_response_normalized = (
        source_response_normalized * response_scale[None, :]
        + response_shift[None, :]
    )
    transported_target_response_normalized = _predict(
        target_geometry_normalized,
        weights,
        biases,
    )
    source_response_physical = (
        source_response_normalized * source_x_scale[None, :]
        + source_x_mean[None, :]
    )
    transported_response_physical = (
        transported_target_response_normalized * target_x_scale[None, :]
        + target_x_mean[None, :]
    )
    normalized_max_abs_error = float(
        np.max(
            np.abs(
                expected_target_response_normalized
                - transported_target_response_normalized
            )
        )
    )
    physical_max_abs_error = float(
        np.max(np.abs(source_response_physical - transported_response_physical))
    )
    equivalence_pass = bool(
        np.allclose(
            expected_target_response_normalized,
            transported_target_response_normalized,
            rtol=1.0e-10,
            atol=1.0e-10,
        )
        and np.allclose(
            source_response_physical,
            transported_response_physical,
            rtol=1.0e-10,
            atol=1.0e-10,
        )
    )
    if not equivalence_pass:
        raise RuntimeError("transported forward initialization failed prediction equivalence")
    return weights, biases, {
        "schema": "forward_normalization_transport_v1",
        "probe_row_count": int(target_geometry_normalized.shape[0]),
        "normalized_prediction_max_abs_error": normalized_max_abs_error,
        "physical_prediction_max_abs_error": physical_max_abs_error,
        "rtol": 1.0e-10,
        "atol": 1.0e-10,
        "equivalence_status": "PASS",
        "geometry_scale": geometry_scale,
        "geometry_shift": geometry_shift,
        "response_scale": response_scale,
        "response_shift": response_shift,
    }


def _configure_inverse_initialization(
    *,
    args: argparse.Namespace,
    input_columns: list[str],
    geometry_columns: list[str],
    inverse_hidden_widths: list[int],
    target_normalization: dict[str, Any],
) -> tuple[list[np.ndarray] | None, list[np.ndarray] | None, dict[str, Any]]:
    mode = str(args.inverse_initialization_mode)
    if mode == "random":
        return None, None, {
            "schema": "inverse_initialization_v1",
            "overall_status": "PASS",
            "mode": "random",
            "source_artifact_used": False,
            "normalization_transport_applied": False,
            "physical_geometry_equivalence_checked": False,
        }

    weights_path = Path(str(args.inverse_initial_weights)).expanduser().resolve()
    summary_path = Path(str(args.inverse_initial_summary)).expanduser().resolve()
    expected_weights_sha256 = str(args.inverse_initial_weights_sha256).strip().lower()
    expected_summary_sha256 = str(args.inverse_initial_summary_sha256).strip().lower()
    if not weights_path.is_file() or not summary_path.is_file():
        raise RuntimeError("source inverse weights or summary do not exist")
    actual_weights_sha256 = _sha256_file(weights_path)
    actual_summary_sha256 = _sha256_file(summary_path)
    if actual_weights_sha256 != expected_weights_sha256:
        raise RuntimeError("source inverse weights SHA-256 mismatch")
    if actual_summary_sha256 != expected_summary_sha256:
        raise RuntimeError("source inverse summary SHA-256 mismatch")
    try:
        source_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("source inverse summary is unreadable") from exc
    if str(source_summary.get("weights_npz_sha256") or "") != actual_weights_sha256:
        raise RuntimeError("source inverse summary does not bind the supplied weights SHA-256")

    source_input_columns = [str(value) for value in source_summary.get("input_columns") or []]
    source_geometry_columns = [str(value) for value in source_summary.get("geometry_columns") or []]
    if [
        _strip_physical_feature_provenance(value) for value in source_input_columns
    ] != [
        _strip_physical_feature_provenance(value) for value in input_columns
    ]:
        raise RuntimeError("source and target inverse physical-feature order does not match")
    if source_geometry_columns != list(geometry_columns):
        raise RuntimeError("source and target inverse geometry order does not match")
    source_architecture = (
        source_summary.get("model_comparison_contract", {}).get("architecture", {})
    )
    if list(source_architecture.get("inverse_hidden_widths") or []) != [
        int(value) for value in inverse_hidden_widths
    ]:
        raise RuntimeError("source and target inverse hidden-width contracts do not match")

    required_normalization_keys = ("x_mean", "x_scale", "y_mean", "y_scale")
    try:
        with np.load(weights_path, allow_pickle=False) as arrays:
            weight_keys = sorted(
                (key for key in arrays.files if key.startswith("inverse_weight_")),
                key=lambda key: int(key.removeprefix("inverse_weight_")),
            )
            bias_keys = sorted(
                (key for key in arrays.files if key.startswith("inverse_bias_")),
                key=lambda key: int(key.removeprefix("inverse_bias_")),
            )
            source_weights = [
                np.asarray(arrays[key], dtype=float).copy() for key in weight_keys
            ]
            source_biases = [
                np.asarray(arrays[key], dtype=float).copy() for key in bias_keys
            ]
            source_normalization = {
                key: np.asarray(arrays[f"normalization__{key}"], dtype=float).copy()
                for key in required_normalization_keys
            }
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError("source inverse weights artifact is incomplete or unreadable") from exc

    _validate_tandem_stage_network(
        source_weights,
        source_biases,
        expected_input_dim=len(input_columns),
        expected_output_dim=len(geometry_columns),
    )
    expected_sizes = [
        len(input_columns),
        *[int(value) for value in inverse_hidden_widths],
        len(geometry_columns),
    ]
    actual_sizes = [source_weights[0].shape[0]] + [
        weight.shape[1] for weight in source_weights
    ]
    if actual_sizes != expected_sizes:
        raise RuntimeError("source inverse network layer shapes do not match target architecture")
    target_norm = {
        key: np.asarray(target_normalization[key], dtype=float).copy()
        for key in required_normalization_keys
    }
    transported_weights, transported_biases, transport = (
        _transport_inverse_network_normalization(
            source_weights=source_weights,
            source_biases=source_biases,
            source_normalization=source_normalization,
            target_normalization=target_norm,
        )
    )
    return transported_weights, transported_biases, {
        "schema": "inverse_initialization_v1",
        "overall_status": "PASS",
        "mode": "transported_source_finetune",
        "source_artifact_used": True,
        "source_weights": str(weights_path),
        "source_weights_sha256": actual_weights_sha256,
        "source_summary": str(summary_path),
        "source_summary_sha256": actual_summary_sha256,
        "source_input_columns": source_input_columns,
        "target_input_columns": list(input_columns),
        "geometry_columns": list(geometry_columns),
        "inverse_layer_sizes": actual_sizes,
        "normalization_transport_applied": True,
        "physical_geometry_equivalence_checked": True,
        "normalization_transport": transport,
        "scientific_boundary": (
            "Only source inverse weights are transported. Fine-tuning, held-out selection, and all "
            "accuracy evidence use current-foundry real EMX; source labels are not merged."
        ),
    }


def _transport_inverse_network_normalization(
    *,
    source_weights: list[np.ndarray],
    source_biases: list[np.ndarray],
    source_normalization: dict[str, np.ndarray],
    target_normalization: dict[str, np.ndarray],
) -> tuple[list[np.ndarray], list[np.ndarray], dict[str, Any]]:
    source_x_mean = np.asarray(source_normalization["x_mean"], dtype=float)
    source_x_scale = np.asarray(source_normalization["x_scale"], dtype=float)
    source_y_mean = np.asarray(source_normalization["y_mean"], dtype=float)
    source_y_scale = np.asarray(source_normalization["y_scale"], dtype=float)
    target_x_mean = np.asarray(target_normalization["x_mean"], dtype=float)
    target_x_scale = np.asarray(target_normalization["x_scale"], dtype=float)
    target_y_mean = np.asarray(target_normalization["y_mean"], dtype=float)
    target_y_scale = np.asarray(target_normalization["y_scale"], dtype=float)
    for value in (
        source_x_scale,
        source_y_scale,
        target_x_scale,
        target_y_scale,
    ):
        if np.any(~np.isfinite(value)) or np.any(value <= 0.0):
            raise RuntimeError("inverse normalization transport requires positive finite scales")

    input_scale = target_x_scale / source_x_scale
    input_shift = (target_x_mean - source_x_mean) / source_x_scale
    output_scale = source_y_scale / target_y_scale
    output_shift = (source_y_mean - target_y_mean) / target_y_scale
    weights = [np.asarray(value, dtype=float).copy() for value in source_weights]
    biases = [np.asarray(value, dtype=float).copy() for value in source_biases]
    if len(weights) == 1:
        source_weight = weights[0].copy()
        source_bias = biases[0].copy()
        weights[0] = input_scale[:, None] * source_weight * output_scale[None, :]
        biases[0] = (
            (input_shift @ source_weight + source_bias) * output_scale
            + output_shift
        )
    else:
        weights[0] = input_scale[:, None] * weights[0]
        biases[0] = input_shift @ source_weights[0] + source_biases[0]
        weights[-1] = weights[-1] * output_scale[None, :]
        biases[-1] = biases[-1] * output_scale + output_shift

    probe_rng = np.random.default_rng(20260728)
    target_response_normalized = np.vstack(
        [
            np.zeros((1, source_x_mean.size), dtype=float),
            np.eye(source_x_mean.size, dtype=float),
            -np.eye(source_x_mean.size, dtype=float),
            probe_rng.normal(0.0, 1.0, size=(32, source_x_mean.size)),
        ]
    )
    source_response_normalized = (
        target_response_normalized * input_scale[None, :]
        + input_shift[None, :]
    )
    source_geometry_normalized = _predict(
        source_response_normalized,
        source_weights,
        source_biases,
    )
    expected_target_geometry_normalized = (
        source_geometry_normalized * output_scale[None, :]
        + output_shift[None, :]
    )
    transported_target_geometry_normalized = _predict(
        target_response_normalized,
        weights,
        biases,
    )
    source_geometry_physical = (
        source_geometry_normalized * source_y_scale[None, :]
        + source_y_mean[None, :]
    )
    transported_geometry_physical = (
        transported_target_geometry_normalized * target_y_scale[None, :]
        + target_y_mean[None, :]
    )
    normalized_max_abs_error = float(
        np.max(
            np.abs(
                expected_target_geometry_normalized
                - transported_target_geometry_normalized
            )
        )
    )
    physical_max_abs_error = float(
        np.max(np.abs(source_geometry_physical - transported_geometry_physical))
    )
    if not (
        np.allclose(
            expected_target_geometry_normalized,
            transported_target_geometry_normalized,
            rtol=1.0e-10,
            atol=1.0e-10,
        )
        and np.allclose(
            source_geometry_physical,
            transported_geometry_physical,
            rtol=1.0e-10,
            atol=1.0e-10,
        )
    ):
        raise RuntimeError("transported inverse initialization failed geometry equivalence")
    return weights, biases, {
        "schema": "inverse_normalization_transport_v1",
        "probe_row_count": int(target_response_normalized.shape[0]),
        "normalized_geometry_max_abs_error": normalized_max_abs_error,
        "physical_geometry_max_abs_error_um": physical_max_abs_error,
        "rtol": 1.0e-10,
        "atol": 1.0e-10,
        "equivalence_status": "PASS",
        "input_scale": input_scale,
        "input_shift": input_shift,
        "output_scale": output_scale,
        "output_shift": output_shift,
    }


def _init_mlp(
    input_dim: int,
    output_dim: int,
    hidden_widths: list[int],
    rng: np.random.Generator,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    sizes = [input_dim] + [int(value) for value in hidden_widths] + [output_dim]
    weights: list[np.ndarray] = []
    biases: list[np.ndarray] = []
    for fan_in, fan_out in zip(sizes[:-1], sizes[1:]):
        weights.append(rng.normal(0.0, math.sqrt(2.0 / max(1, fan_in)), size=(fan_in, fan_out)))
        biases.append(np.zeros(fan_out, dtype=float))
    return weights, biases


def _forward_with_cache(x: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> tuple[np.ndarray, list[np.ndarray], list[np.ndarray]]:
    activations = [x]
    preactivations: list[np.ndarray] = []
    value = x
    for layer, (weight, bias) in enumerate(zip(weights, biases)):
        z = value @ weight + bias
        preactivations.append(z)
        value = z if layer == len(weights) - 1 else _gelu(z)
        activations.append(value)
    return value, activations, preactivations


def _backward(
    grad_output: np.ndarray,
    weights: list[np.ndarray],
    activations: list[np.ndarray],
    preactivations: list[np.ndarray],
    weight_decay: float,
) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    grad = grad_output
    grad_weights = [np.zeros_like(item) for item in weights]
    grad_biases = [np.zeros(weight.shape[1], dtype=float) for weight in weights]
    for layer in reversed(range(len(weights))):
        grad_weights[layer] = activations[layer].T @ grad + weight_decay * weights[layer]
        grad_biases[layer] = np.sum(grad, axis=0)
        grad = grad @ weights[layer].T
        if layer > 0:
            grad = grad * _gelu_derivative(preactivations[layer - 1])
    return grad_weights, grad_biases, grad


def _backward_input_gradient(
    grad_output: np.ndarray,
    weights: list[np.ndarray],
    preactivations: list[np.ndarray],
) -> np.ndarray:
    """Backpropagate only to the input of a frozen network."""
    grad = np.asarray(grad_output, dtype=float)
    for layer in reversed(range(len(weights))):
        grad = grad @ weights[layer].T
        if layer > 0:
            grad = grad * _gelu_derivative(preactivations[layer - 1])
    return grad


def _predict(x: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    return _forward_with_cache(x, weights, biases)[0]


def _project_geometry(raw: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    clipped = np.clip(raw, -40.0, 40.0)
    sigmoid = 1.0 / (1.0 + np.exp(-clipped))
    geometry = lower[None, :] + (upper - lower)[None, :] * sigmoid
    derivative = (upper - lower)[None, :] * sigmoid * (1.0 - sigmoid)
    return geometry, derivative


def _project_geometry_hard_feasible_topology(
    raw: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    normalization: dict[str, Any],
    topology_contract: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    raw_matrix = np.asarray(raw, dtype=float)
    lower_array = np.asarray(lower, dtype=float)
    upper_array = np.asarray(upper, dtype=float)
    if raw_matrix.ndim != 2:
        raise ValueError("hard-feasible geometry projection expects a 2-D raw matrix")
    if lower_array.shape != upper_array.shape or lower_array.shape != (raw_matrix.shape[1],):
        raise ValueError("hard-feasible geometry projection envelope shape mismatch")
    if not topology_contract.get("available"):
        raise ValueError("hard-feasible geometry projection requires all topology semantics")
    power_line_contract = topology_contract.get("power_line_port_ground_overlap") or {}
    if not bool(power_line_contract.get("enabled")):
        raise ValueError("hard-feasible geometry projection requires the power-line ground-overlap contract")

    y_mean = np.asarray(normalization["y_mean"], dtype=float)
    y_scale = np.asarray(normalization["y_scale"], dtype=float)
    if y_mean.shape != lower_array.shape or y_scale.shape != lower_array.shape:
        raise ValueError("hard-feasible geometry projection normalization shape mismatch")
    if np.any(~np.isfinite(y_scale)) or np.any(y_scale <= 0.0):
        raise ValueError("hard-feasible geometry projection requires positive finite geometry scales")

    clipped = np.clip(raw_matrix, -40.0, 40.0)
    sigmoid = 1.0 / (1.0 + np.exp(-clipped))
    sigmoid_derivative = sigmoid * (1.0 - sigmoid)
    physical_lower = lower_array * y_scale + y_mean
    physical_upper = upper_array * y_scale + y_mean
    physical_span = physical_upper - physical_lower
    if np.any(~np.isfinite(physical_span)) or np.any(physical_span <= 0.0):
        raise ValueError("hard-feasible geometry projection requires positive physical envelope spans")

    physical = physical_lower[None, :] + physical_span[None, :] * sigmoid
    row_count, dimension_count = physical.shape
    physical_jacobian = np.zeros(
        (row_count, dimension_count, dimension_count),
        dtype=float,
    )
    independent_derivative = physical_span[None, :] * sigmoid_derivative
    diagonal = np.arange(dimension_count)
    physical_jacobian[:, diagonal, diagonal] = independent_derivative

    index = {
        str(name): int(value)
        for name, value in (topology_contract.get("index_by_semantic") or {}).items()
    }
    chamfer_factor = math.sqrt(2.0) - 1.0

    for role in ("primary", "secondary"):
        width_index = index[f"{role}_outer_width_um"]
        height_index = index[f"{role}_outer_height_um"]
        terminal_index = index[f"{role}_terminal_y_span_um"]
        width = physical[:, width_index]
        height = physical[:, height_index]
        width_is_minimum = width <= height
        straight_cap = height - chamfer_factor * np.minimum(width, height)
        cap_candidates = np.column_stack(
            (
                np.full(row_count, physical_upper[terminal_index], dtype=float),
                width,
                height,
                straight_cap,
            )
        )
        selected_cap = np.argmin(cap_candidates, axis=1)
        cap = cap_candidates[np.arange(row_count), selected_cap]
        terminal_lower = float(physical_lower[terminal_index])
        if np.any(cap < terminal_lower - 1.0e-9):
            raise RuntimeError(
                f"hard-feasible projection envelope cannot satisfy {role} terminal constraints"
            )
        cap = np.maximum(cap, terminal_lower)

        d_cap_d_width = np.zeros(row_count, dtype=float)
        d_cap_d_height = np.zeros(row_count, dtype=float)
        d_cap_d_width[selected_cap == 1] = 1.0
        d_cap_d_height[selected_cap == 2] = 1.0
        straight_selected = selected_cap == 3
        d_cap_d_width[straight_selected & width_is_minimum] = -chamfer_factor
        d_cap_d_height[straight_selected & width_is_minimum] = 1.0
        d_cap_d_height[straight_selected & ~width_is_minimum] = 1.0 - chamfer_factor

        terminal_sigmoid = sigmoid[:, terminal_index]
        physical[:, terminal_index] = terminal_lower + terminal_sigmoid * (
            cap - terminal_lower
        )
        physical_jacobian[:, terminal_index, :] = 0.0
        physical_jacobian[:, terminal_index, width_index] = (
            terminal_sigmoid
            * d_cap_d_width
            * independent_derivative[:, width_index]
        )
        physical_jacobian[:, terminal_index, height_index] = (
            terminal_sigmoid
            * d_cap_d_height
            * independent_derivative[:, height_index]
        )
        physical_jacobian[:, terminal_index, terminal_index] = (
            sigmoid_derivative[:, terminal_index] * (cap - terminal_lower)
        )

    primary_width_index = index["primary_outer_width_um"]
    secondary_width_index = index["secondary_outer_width_um"]
    line_width_index = index["line_width_um"]
    offset_index = index["offset_um"]
    primary_feed_index = index["primary_feed_extension_um"]
    secondary_feed_index = index["secondary_feed_extension_um"]

    primary_width = physical[:, primary_width_index]
    secondary_width = physical[:, secondary_width_index]
    line_width = physical[:, line_width_index]
    offset = physical[:, offset_index]
    fixed_margin = (
        float(power_line_contract["bar_offset_um"])
        + float(power_line_contract["shield_opening_clearance_um"])
        + float(power_line_contract.get("training_safety_margin_um") or 0.0)
    )

    primary_own_left = -0.5 * primary_width
    secondary_left = offset - 0.5 * secondary_width
    secondary_defines_left_union = secondary_left < primary_own_left
    primary_port_requirement = (
        primary_own_left
        - np.minimum(primary_own_left, secondary_left)
        + fixed_margin
        + line_width
    )
    primary_requirements = np.column_stack(
        (
            np.full(row_count, physical_lower[primary_feed_index], dtype=float),
            -offset,
            primary_port_requirement,
        )
    )
    primary_selected = np.argmax(primary_requirements, axis=1)
    primary_required = primary_requirements[np.arange(row_count), primary_selected]
    primary_requirement_gradient = np.zeros_like(physical)
    primary_requirement_gradient[primary_selected == 1, offset_index] = -1.0
    primary_port_selected = primary_selected == 2
    primary_requirement_gradient[
        primary_port_selected & secondary_defines_left_union,
        primary_width_index,
    ] = -0.5
    primary_requirement_gradient[
        primary_port_selected & secondary_defines_left_union,
        secondary_width_index,
    ] = 0.5
    primary_requirement_gradient[
        primary_port_selected & secondary_defines_left_union,
        offset_index,
    ] = -1.0
    primary_requirement_gradient[primary_port_selected, line_width_index] = 1.0

    secondary_own_right = offset + 0.5 * secondary_width
    primary_right = 0.5 * primary_width
    primary_defines_right_union = primary_right > secondary_own_right
    secondary_port_requirement = (
        np.maximum(primary_right, secondary_own_right)
        - secondary_own_right
        + fixed_margin
        + line_width
    )
    secondary_requirements = np.column_stack(
        (
            np.full(row_count, physical_lower[secondary_feed_index], dtype=float),
            offset,
            secondary_port_requirement,
        )
    )
    secondary_selected = np.argmax(secondary_requirements, axis=1)
    secondary_required = secondary_requirements[
        np.arange(row_count),
        secondary_selected,
    ]
    secondary_requirement_gradient = np.zeros_like(physical)
    secondary_requirement_gradient[secondary_selected == 1, offset_index] = 1.0
    secondary_port_selected = secondary_selected == 2
    secondary_requirement_gradient[
        secondary_port_selected & primary_defines_right_union,
        primary_width_index,
    ] = 0.5
    secondary_requirement_gradient[
        secondary_port_selected & primary_defines_right_union,
        secondary_width_index,
    ] = -0.5
    secondary_requirement_gradient[
        secondary_port_selected & primary_defines_right_union,
        offset_index,
    ] = -1.0
    secondary_requirement_gradient[secondary_port_selected, line_width_index] = 1.0

    for feed_index, required, requirement_gradient, label in (
        (
            primary_feed_index,
            primary_required,
            primary_requirement_gradient,
            "primary",
        ),
        (
            secondary_feed_index,
            secondary_required,
            secondary_requirement_gradient,
            "secondary",
        ),
    ):
        feed_upper = float(physical_upper[feed_index])
        if np.any(required > feed_upper + 1.0e-9):
            raise RuntimeError(
                f"hard-feasible projection envelope cannot satisfy {label} feed constraints"
            )
        required = np.minimum(required, feed_upper)
        feed_sigmoid = sigmoid[:, feed_index]
        physical[:, feed_index] = required + feed_sigmoid * (
            feed_upper - required
        )
        physical_jacobian[:, feed_index, :] = (
            (1.0 - feed_sigmoid)[:, None]
            * requirement_gradient
            * independent_derivative
        )
        physical_jacobian[:, feed_index, feed_index] = (
            sigmoid_derivative[:, feed_index] * (feed_upper - required)
        )

    geometry = (physical - y_mean[None, :]) / y_scale[None, :]
    normalized_jacobian = physical_jacobian / y_scale[None, :, None]
    return geometry, normalized_jacobian


def _project_inverse_geometry(
    raw: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    mode: str = "independent_sigmoid",
    normalization: dict[str, Any] | None = None,
    topology_contract: dict[str, Any] | None = None,
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    if mode == "independent_sigmoid":
        geometry, derivative = _project_geometry(raw, lower, upper)
        return geometry, derivative, None
    if mode == "hard_feasible_topology_v1":
        if normalization is None or topology_contract is None:
            raise ValueError("hard-feasible projection requires normalization and topology contract")
        geometry, jacobian = _project_geometry_hard_feasible_topology(
            raw,
            lower,
            upper,
            normalization,
            topology_contract,
        )
        return geometry, None, jacobian
    raise ValueError(f"unsupported inverse geometry projection mode: {mode}")


def _predict_inverse(
    x: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    projection_mode: str = "independent_sigmoid",
    normalization: dict[str, Any] | None = None,
    topology_contract: dict[str, Any] | None = None,
) -> np.ndarray:
    raw = _predict(x, weights, biases)
    return _project_inverse_geometry(
        raw,
        lower,
        upper,
        mode=projection_mode,
        normalization=normalization,
        topology_contract=topology_contract,
    )[0]


def _init_adam(weights: list[np.ndarray], biases: list[np.ndarray]) -> dict[str, Any]:
    return {
        "step": 0,
        "mw": [np.zeros_like(item) for item in weights],
        "vw": [np.zeros_like(item) for item in weights],
        "mb": [np.zeros_like(item) for item in biases],
        "vb": [np.zeros_like(item) for item in biases],
    }


def _training_learning_rate(
    args: argparse.Namespace,
    optimizer_update: int,
    target_updates: int,
) -> float:
    """Return the auditable optimizer rate for a one-based update index."""

    base = float(args.learning_rate)
    schedule = str(getattr(args, "training_learning_rate_schedule", "constant"))
    if schedule == "constant" or int(target_updates) <= 1:
        return base
    if schedule != "cosine_decay":
        raise ValueError(f"unsupported training learning-rate schedule: {schedule}")
    final_fraction = float(
        getattr(args, "training_final_learning_rate_fraction", 0.1)
    )
    progress = (int(optimizer_update) - 1) / float(int(target_updates) - 1)
    progress = min(1.0, max(0.0, progress))
    multiplier = final_fraction + 0.5 * (1.0 - final_fraction) * (
        1.0 + math.cos(math.pi * progress)
    )
    return base * multiplier


def _adam_step(
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    grad_weights: list[np.ndarray],
    grad_biases: list[np.ndarray],
    state: dict[str, Any],
    learning_rate: float,
) -> None:
    beta1, beta2, epsilon = 0.9, 0.999, 1.0e-8
    state["step"] += 1
    step = int(state["step"])
    for index in range(len(weights)):
        state["mw"][index] = beta1 * state["mw"][index] + (1.0 - beta1) * grad_weights[index]
        state["vw"][index] = beta2 * state["vw"][index] + (1.0 - beta2) * grad_weights[index] ** 2
        state["mb"][index] = beta1 * state["mb"][index] + (1.0 - beta1) * grad_biases[index]
        state["vb"][index] = beta2 * state["vb"][index] + (1.0 - beta2) * grad_biases[index] ** 2
        mw = state["mw"][index] / (1.0 - beta1**step)
        vw = state["vw"][index] / (1.0 - beta2**step)
        mb = state["mb"][index] / (1.0 - beta1**step)
        vb = state["vb"][index] / (1.0 - beta2**step)
        weights[index] -= learning_rate * mw / (np.sqrt(vw) + epsilon)
        biases[index] -= learning_rate * mb / (np.sqrt(vb) + epsilon)


def _evaluate_frozen_forward(
    data: dict[str, Any],
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Record the transported forward checkpoint without applying optimizer updates."""

    x, y, split = data["x"], data["y"], data["split"]
    dimension_weights = np.asarray(data["normalization"]["response_loss_dimension_weights"], dtype=float)
    response_loss_state = data.get("response_loss_state")
    train_probe = split["train"][: min(4096, len(split["train"]))]
    train_prediction = _predict(y[train_probe], weights, biases)
    validation_prediction = _predict(y[split["validation"]], weights, biases)
    train_rmse = _rmse(train_prediction, x[train_probe])
    validation_rmse = _rmse(validation_prediction, x[split["validation"]])
    train_balanced_rmse = _weighted_rmse(train_prediction, x[train_probe], dimension_weights)
    validation_balanced_rmse = _weighted_rmse(
        validation_prediction,
        x[split["validation"]],
        dimension_weights,
    )
    train_objective_rmse = _response_objective_rmse(
        train_prediction,
        x[train_probe],
        dimension_weights,
        response_loss_state,
    )
    validation_objective_rmse = _response_objective_rmse(
        validation_prediction,
        x[split["validation"]],
        dimension_weights,
        response_loss_state,
    )
    selection_rmse = (
        validation_objective_rmse
        if data["response_loss_contract"]["family"] == "relative_mse"
        else validation_balanced_rmse
    )
    history = [
        {
            "epoch": 0,
            "optimizer_updates": 0,
            "real_row_draws": 0,
            "validation_event": 1,
            "optimizer_budget_mode": str(data["optimizer_budget_contract"]["mode"]),
            "train_normalized_rmse": train_rmse,
            "validation_normalized_rmse": validation_rmse,
            "train_feature_balanced_normalized_rmse": train_balanced_rmse,
            "validation_feature_balanced_normalized_rmse": validation_balanced_rmse,
            "train_response_objective_rmse": train_objective_rmse,
            "validation_response_objective_rmse": validation_objective_rmse,
            "response_loss_family": str(data["response_loss_contract"]["family"]),
            "training_batch_sampler": str(data["training_batch_sampler_contract"]["family"]),
            "frozen_transported_checkpoint": True,
        }
    ]
    best = {
        "loss": selection_rmse,
        "epoch": 0,
        "optimizer_updates": 0,
        "weights": [item.copy() for item in weights],
        "biases": [item.copy() for item in biases],
    }
    return history, best


def _train_forward(
    data: dict[str, Any],
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x, y, split = data["x"], data["y"], data["split"]
    dimension_weights = np.asarray(data["normalization"]["response_loss_dimension_weights"], dtype=float)
    response_loss_state = data.get("response_loss_state")
    rng = np.random.default_rng(int(args.seed))
    state = _init_adam(weights, biases)
    best = {
        "loss": math.inf,
        "epoch": 0,
        "optimizer_updates": 0,
        "weights": [item.copy() for item in weights],
        "biases": [item.copy() for item in biases],
    }
    stale = 0
    history: list[dict[str, Any]] = []
    row_draws = 0
    validation_event = 0
    budget = data["optimizer_budget_contract"]
    exact_update_mode = budget["mode"] == "fixed_optimizer_updates"
    target_updates = int(budget["forward"]["target_optimizer_updates"])
    updates_per_epoch = int(budget["updates_per_full_training_epoch"])
    validation_interval = int(budget["validation_every_optimizer_updates"])
    max_epochs = (
        int(math.ceil(target_updates / float(updates_per_epoch)))
        if exact_update_mode
        else int(args.forward_epochs)
    )
    continuous_batch_state = (
        _init_continuous_permutation_batch_state(data)
        if exact_update_mode
        and args.exact_update_batch_mode == "continuous_permutation_full_batch"
        else None
    )
    last_learning_rate = float(args.learning_rate)

    def record_validation(epoch: int) -> None:
        nonlocal best, stale, validation_event
        validation_event += 1
        train_probe = split["train"][: min(4096, len(split["train"]))]
        train_prediction = _predict(y[train_probe], weights, biases)
        validation_prediction = _predict(y[split["validation"]], weights, biases)
        train_rmse = _rmse(train_prediction, x[train_probe])
        validation_rmse = _rmse(validation_prediction, x[split["validation"]])
        train_balanced_rmse = _weighted_rmse(train_prediction, x[train_probe], dimension_weights)
        validation_balanced_rmse = _weighted_rmse(
            validation_prediction,
            x[split["validation"]],
            dimension_weights,
        )
        train_objective_rmse = _response_objective_rmse(
            train_prediction,
            x[train_probe],
            dimension_weights,
            response_loss_state,
        )
        validation_objective_rmse = _response_objective_rmse(
            validation_prediction,
            x[split["validation"]],
            dimension_weights,
            response_loss_state,
        )
        history.append(
            {
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "real_row_draws": int(row_draws),
                "validation_event": validation_event,
                "optimizer_budget_mode": str(budget["mode"]),
                "train_normalized_rmse": train_rmse,
                "validation_normalized_rmse": validation_rmse,
                "train_feature_balanced_normalized_rmse": train_balanced_rmse,
                "validation_feature_balanced_normalized_rmse": validation_balanced_rmse,
                "train_response_objective_rmse": train_objective_rmse,
                "validation_response_objective_rmse": validation_objective_rmse,
                "response_loss_family": str(data["response_loss_contract"]["family"]),
                "training_batch_sampler": str(data["training_batch_sampler_contract"]["family"]),
                "training_learning_rate": float(last_learning_rate),
            }
        )
        selection_rmse = (
            validation_objective_rmse
            if data["response_loss_contract"]["family"] == "relative_mse"
            else validation_balanced_rmse
        )
        if selection_rmse + 1.0e-12 < float(best["loss"]):
            best = {
                "loss": selection_rmse,
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "weights": [item.copy() for item in weights],
                "biases": [item.copy() for item in biases],
            }
            stale = 0
        else:
            stale += 1

    for epoch in range(1, max_epochs + 1):
        exact_batch_count = (
            min(updates_per_epoch, target_updates - int(state["step"]))
            if continuous_batch_state is not None
            else None
        )
        for batch in _training_batches(
            data,
            int(args.batch_size),
            rng,
            continuous_state=continuous_batch_state,
            exact_batch_count=exact_batch_count,
        ):
            if exact_update_mode and int(state["step"]) >= target_updates:
                break
            prediction, activations, preactivations = _forward_with_cache(y[batch], weights, biases)
            _response_loss, grad_output = _response_loss_and_gradient(
                prediction,
                x[batch],
                dimension_weights,
                response_loss_state,
            )
            grad_weights, grad_biases, _ = _backward(grad_output, weights, activations, preactivations, float(args.weight_decay))
            last_learning_rate = _training_learning_rate(
                args,
                int(state["step"]) + 1,
                target_updates,
            )
            _adam_step(
                weights,
                biases,
                grad_weights,
                grad_biases,
                state,
                last_learning_rate,
            )
            row_draws += int(len(batch))
            if exact_update_mode and (
                int(state["step"]) % validation_interval == 0
                or int(state["step"]) == target_updates
            ):
                record_validation(epoch)
        if exact_update_mode:
            if int(state["step"]) >= target_updates:
                break
        else:
            record_validation(epoch)
            if stale >= int(args.patience):
                break
    if exact_update_mode and int(state["step"]) != target_updates:
        raise RuntimeError("forward training did not complete its exact optimizer-update budget")
    return history, best


def _physical_cell_strict_pass_summary(
    target_physical: np.ndarray,
    strict_pass: np.ndarray,
    split_audit: dict[str, Any],
    *,
    partition: str = "validation",
) -> dict[str, Any]:
    """Summarize strict acceptance with equal weight per held-out physical cell."""

    target = np.asarray(target_physical, dtype=float)
    passed = np.asarray(strict_pass, dtype=bool)
    if target.ndim != 2 or target.shape[1] != 4:
        raise ValueError("physical-cell checkpoint selection requires four response dimensions")
    if passed.shape != (target.shape[0],) or target.shape[0] == 0:
        raise ValueError("physical-cell checkpoint selection requires one pass flag per row")
    if np.any(~np.isfinite(target)):
        raise ValueError("physical-cell checkpoint targets contain nonfinite values")
    if split_audit.get("split_mode") != "physical_cell_grouped":
        raise ValueError("physical-cell checkpoint selection requires a grouped physical-cell split")
    if split_audit.get("physical_cell_range_source") != "explicit":
        raise ValueError("physical-cell checkpoint selection requires explicit physical bounds")

    bins = int(split_audit.get("physical_cell_bins_per_dimension") or 0)
    lower = np.asarray(split_audit.get("physical_cell_lower") or [], dtype=float)
    upper = np.asarray(split_audit.get("physical_cell_upper") or [], dtype=float)
    if bins < 2 or lower.shape != (4,) or upper.shape != (4,):
        raise ValueError("physical-cell checkpoint split contract is incomplete")
    if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(upper <= lower):
        raise ValueError("physical-cell checkpoint bounds are invalid")

    normalized = (target - lower[None, :]) / (upper - lower)[None, :]
    if np.any(normalized < -1.0e-12) or np.any(normalized > 1.0 + 1.0e-12):
        raise ValueError("physical-cell checkpoint targets fall outside declared bounds")
    cells = np.clip(np.floor(np.clip(normalized, 0.0, 1.0) * bins).astype(int), 0, bins - 1)
    cell_ids = np.asarray(
        [":".join(str(int(value)) for value in row) for row in cells],
        dtype=object,
    )
    actual_ids = set(str(value) for value in cell_ids.tolist())
    expected_ids = {
        str(value)
        for value in ((split_audit.get("cell_ids") or {}).get(partition) or [])
    }
    if expected_ids and actual_ids != expected_ids:
        raise ValueError(
            "physical-cell checkpoint rows differ from the immutable split audit: "
            f"missing={sorted(expected_ids - actual_ids)} extra={sorted(actual_ids - expected_ids)}"
        )

    rates = [
        float(np.mean(passed[cell_ids == cell_id]))
        for cell_id in sorted(actual_ids)
    ]
    ordered = sorted(rates)
    p10_index = int(math.floor((len(ordered) - 1) * 0.10))
    return {
        "physical_cell_count": len(rates),
        "p10_physical_cell_strict_pass_rate": float(ordered[p10_index]),
        "minimum_physical_cell_strict_pass_rate": float(ordered[0]),
        "equal_cell_strict_pass_rate": float(np.mean(rates)),
        "row_strict_pass_rate": float(np.mean(passed)),
    }


def _train_inverse(
    data: dict[str, Any],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x, y, split = data["x"], data["y"], data["split"]
    lower = data["normalization"]["geometry_lower"]
    upper = data["normalization"]["geometry_upper"]
    dimension_weights = np.asarray(data["normalization"]["response_loss_dimension_weights"], dtype=float)
    response_loss_state = data.get("response_loss_state")
    topology_contract = data.get("topology_feasibility_contract") or {}
    topology_weight = float(args.topology_feasibility_weight)
    projection_mode = str(args.inverse_geometry_projection)
    rng = np.random.default_rng(int(args.seed) + 1)
    state = _init_adam(inverse_weights, inverse_biases)
    best = {
        "loss": math.inf,
        "selection_key": (math.inf,),
        "epoch": 0,
        "optimizer_updates": 0,
        "weights": [item.copy() for item in inverse_weights],
        "biases": [item.copy() for item in inverse_biases],
    }
    stale = 0
    history: list[dict[str, Any]] = []
    schedule_state = _init_response_schedule_state(args)
    row_draws = 0
    validation_event = 0
    budget = data["optimizer_budget_contract"]
    exact_update_mode = budget["mode"] == "fixed_optimizer_updates"
    target_updates = int(budget["inverse"]["target_optimizer_updates"])
    updates_per_epoch = int(budget["updates_per_full_training_epoch"])
    validation_interval = int(budget["validation_every_optimizer_updates"])
    max_epochs = (
        int(math.ceil(target_updates / float(updates_per_epoch)))
        if exact_update_mode
        else int(args.inverse_epochs)
    )
    continuous_batch_state = (
        _init_continuous_permutation_batch_state(data)
        if exact_update_mode
        and args.exact_update_batch_mode == "continuous_permutation_full_batch"
        else None
    )
    last_schedule = (0.0, "not_started", 0.0)
    last_learning_rate = float(args.learning_rate)

    def record_validation(epoch: int, schedule_progress: int) -> None:
        nonlocal best, stale, validation_event
        validation_event += 1
        response_weight, schedule_phase, adaptive_multiplier = last_schedule
        validation_geometry = _predict_inverse(
            x[split["validation"]],
            inverse_weights,
            inverse_biases,
            lower,
            upper,
            projection_mode=projection_mode,
            normalization=data["normalization"],
            topology_contract=topology_contract,
        )
        validation_response = _predict(validation_geometry, forward_weights, forward_biases)
        response_rmse = _rmse(validation_response, x[split["validation"]])
        balanced_response_mse = _weighted_mse(
            validation_response,
            x[split["validation"]],
            dimension_weights,
        )
        balanced_response_rmse = math.sqrt(balanced_response_mse)
        response_objective_rmse = _response_objective_rmse(
            validation_response,
            x[split["validation"]],
            dimension_weights,
            response_loss_state,
            apply_target_semantics=True,
        )
        geometry_rmse = _rmse(validation_geometry, y[split["validation"]])
        geometry_mse = float(np.mean((validation_geometry - y[split["validation"]]) ** 2))
        topology_penalty, _topology_gradient, topology_diagnostics = _topology_feasibility_penalty_and_gradient(
            validation_geometry,
            data["normalization"],
            topology_contract,
        )
        _update_response_schedule_state(
            schedule_state,
            balanced_response_mse,
            geometry_mse,
            schedule_progress,
            args,
        )
        objective = (
            (
                response_objective_rmse
                if data["response_loss_contract"]["family"] == "relative_mse"
                else balanced_response_rmse
            )
            + float(args.geometry_anchor_weight) * geometry_rmse
            + topology_weight * topology_penalty
        )
        checkpoint_selection_score = objective
        checkpoint_selection_key = (float(checkpoint_selection_score),)
        semantic_relative_mae: dict[str, float] | None = None
        strict_row_pass_rate: float | None = None
        physical_cell_checkpoint: dict[str, Any] | None = None
        if args.inverse_checkpoint_selection != "training_objective":
            if response_loss_state is None or response_loss_state.get("family") != "relative_mse":
                raise RuntimeError(
                    "acceptance-aligned inverse checkpoint selection requires relative-MSE state"
                )
            x_mean = np.asarray(response_loss_state["x_mean"], dtype=float)
            x_scale = np.asarray(response_loss_state["x_scale"], dtype=float)
            floors = np.asarray(
                response_loss_state["denominator_floors_physical"],
                dtype=float,
            )
            validation_truth = x[split["validation"]]
            response_physical = validation_response * x_scale[None, :] + x_mean[None, :]
            truth_physical = validation_truth * x_scale[None, :] + x_mean[None, :]
            denominator = np.maximum(np.abs(truth_physical), floors[None, :])
            relative_error = (response_physical - truth_physical) / denominator
            minimum_target_indices = np.asarray(
                response_loss_state.get("minimum_target_indices", []),
                dtype=int,
            )
            exact_target_indices = np.asarray(
                [
                    index
                    for index in range(relative_error.shape[1])
                    if index not in set(int(value) for value in minimum_target_indices)
                ],
                dtype=int,
            )
            minimum_target_pass = np.all(
                response_physical[:, minimum_target_indices]
                >= truth_physical[:, minimum_target_indices],
                axis=1,
            )
            relative_error[:, minimum_target_indices] = np.minimum(
                relative_error[:, minimum_target_indices],
                0.0,
            )
            per_dimension_mae = np.mean(np.abs(relative_error), axis=0)
            input_names = list(data["response_loss_contract"]["input_columns"])
            semantic_relative_mae = {
                input_names[index]: float(per_dimension_mae[index])
                for index in range(len(input_names))
            }
            worst_dimension_mae = float(np.max(per_dimension_mae))
            if args.inverse_checkpoint_selection == "worst_dimension_relative_mae":
                checkpoint_selection_score = worst_dimension_mae
                checkpoint_selection_key = (float(checkpoint_selection_score),)
            else:
                exact_target_pass = np.all(
                    np.abs(relative_error[:, exact_target_indices])
                    <= float(args.inverse_checkpoint_exact_relative_error_threshold),
                    axis=1,
                )
                strict_row_pass_rate = float(
                    np.mean(exact_target_pass & minimum_target_pass)
                )
                failure_rate = 1.0 - strict_row_pass_rate
                checkpoint_selection_score = failure_rate + 1.0e-6 * worst_dimension_mae
                checkpoint_selection_key = (float(checkpoint_selection_score),)
                if args.inverse_checkpoint_selection == "physical_cell_tail_strict_pass_rate":
                    physical_cell_checkpoint = _physical_cell_strict_pass_summary(
                        truth_physical,
                        exact_target_pass & minimum_target_pass,
                        data.get("split_audit") or {},
                    )
                    checkpoint_selection_key = (
                        1.0
                        - float(
                            physical_cell_checkpoint[
                                "p10_physical_cell_strict_pass_rate"
                            ]
                        ),
                        1.0
                        - float(
                            physical_cell_checkpoint[
                                "minimum_physical_cell_strict_pass_rate"
                            ]
                        ),
                        1.0
                        - float(
                            physical_cell_checkpoint[
                                "equal_cell_strict_pass_rate"
                            ]
                        ),
                        1.0 - strict_row_pass_rate,
                        worst_dimension_mae,
                    )
                    checkpoint_selection_score = float(checkpoint_selection_key[0])
        history.append(
            {
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "real_row_draws": int(row_draws),
                "validation_event": validation_event,
                "optimizer_budget_mode": str(budget["mode"]),
                "response_schedule_domain": str(args.response_schedule_domain),
                "response_schedule_progress": int(schedule_progress),
                "response_weight": response_weight,
                "response_weight_schedule_phase": schedule_phase,
                "response_weight_adaptive_multiplier": adaptive_multiplier,
                "response_loss_ema": schedule_state["ema_response_mse"],
                "geometry_loss_ema": schedule_state["ema_geometry_mse"],
                "response_loss_family": str(data["response_loss_contract"]["family"]),
                "training_batch_sampler": str(data["training_batch_sampler_contract"]["family"]),
                "training_learning_rate": float(last_learning_rate),
                "validation_response_normalized_rmse": response_rmse,
                "validation_feature_balanced_response_normalized_rmse": balanced_response_rmse,
                "validation_response_objective_rmse": response_objective_rmse,
                "validation_geometry_normalized_rmse": geometry_rmse,
                "validation_topology_feasibility_penalty": topology_penalty,
                "validation_topology_violation_fraction": topology_diagnostics["violation_fraction"],
                "validation_topology_max_normalized_violation": topology_diagnostics[
                    "max_normalized_violation"
                ],
                "validation_objective": objective,
                "checkpoint_selection_metric": str(args.inverse_checkpoint_selection),
                "checkpoint_selection_score": checkpoint_selection_score,
                "checkpoint_selection_key_json": json.dumps(
                    [float(value) for value in checkpoint_selection_key],
                    separators=(",", ":"),
                ),
                "validation_semantic_relative_mae": semantic_relative_mae,
                "validation_strict_row_pass_rate": strict_row_pass_rate,
                "validation_physical_cell_checkpoint": physical_cell_checkpoint,
                "validation_strict_exact_relative_error_threshold": float(
                    args.inverse_checkpoint_exact_relative_error_threshold
                ),
            }
        )
        if tuple(checkpoint_selection_key) < tuple(best["selection_key"]):
            best = {
                "loss": checkpoint_selection_score,
                "selection_key": tuple(checkpoint_selection_key),
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "weights": [item.copy() for item in inverse_weights],
                "biases": [item.copy() for item in inverse_biases],
            }
            stale = 0
        else:
            stale += 1

    for epoch in range(1, max_epochs + 1):
        if not exact_update_mode:
            last_schedule = _response_weight_for_epoch(
                epoch,
                args,
                schedule_state,
            )
        exact_batch_count = (
            min(updates_per_epoch, target_updates - int(state["step"]))
            if continuous_batch_state is not None
            else None
        )
        for batch in _training_batches(
            data,
            int(args.batch_size),
            rng,
            continuous_state=continuous_batch_state,
            exact_batch_count=exact_batch_count,
        ):
            if exact_update_mode and int(state["step"]) >= target_updates:
                break
            schedule_progress = int(state["step"]) + 1 if exact_update_mode else epoch
            if exact_update_mode:
                last_schedule = _response_weight_for_progress(
                    schedule_progress,
                    args,
                    schedule_state,
                )
            response_weight, _schedule_phase, _adaptive_multiplier = last_schedule
            raw, inverse_activations, inverse_preactivations = _forward_with_cache(x[batch], inverse_weights, inverse_biases)
            geometry, projection_derivative, projection_jacobian = _project_inverse_geometry(
                raw,
                lower,
                upper,
                mode=projection_mode,
                normalization=data["normalization"],
                topology_contract=topology_contract,
            )
            reconstructed, _forward_activations, forward_preactivations = _forward_with_cache(
                geometry,
                forward_weights,
                forward_biases,
            )
            _response_loss, unscaled_response_grad = _response_loss_and_gradient(
                reconstructed,
                x[batch],
                dimension_weights,
                response_loss_state,
                apply_target_semantics=True,
            )
            response_grad = response_weight * unscaled_response_grad
            grad_geometry_from_response = _backward_input_gradient(
                response_grad,
                forward_weights,
                forward_preactivations,
            )
            anchor_grad = float(args.geometry_anchor_weight) * 2.0 * (geometry - y[batch]) / max(1, geometry.size)
            _topology_penalty, topology_grad, _topology_diagnostics = _topology_feasibility_penalty_and_gradient(
                geometry,
                data["normalization"],
                topology_contract,
            )
            grad_geometry = (
                grad_geometry_from_response
                + anchor_grad
                + topology_weight * topology_grad
            )
            if projection_jacobian is None:
                if projection_derivative is None:
                    raise RuntimeError("independent inverse projection derivative is missing")
                grad_raw = grad_geometry * projection_derivative
            else:
                grad_raw = np.einsum(
                    "bi,bij->bj",
                    grad_geometry,
                    projection_jacobian,
                    optimize=True,
                )
            grad_weights, grad_biases, _ = _backward(
                grad_raw,
                inverse_weights,
                inverse_activations,
                inverse_preactivations,
                float(args.weight_decay),
            )
            last_learning_rate = _training_learning_rate(
                args,
                int(state["step"]) + 1,
                target_updates,
            )
            _adam_step(
                inverse_weights,
                inverse_biases,
                grad_weights,
                grad_biases,
                state,
                last_learning_rate,
            )
            row_draws += int(len(batch))
            if exact_update_mode and (
                int(state["step"]) % validation_interval == 0
                or int(state["step"]) == target_updates
            ):
                record_validation(epoch, int(state["step"]))
        if exact_update_mode:
            if int(state["step"]) >= target_updates:
                break
        else:
            record_validation(epoch, epoch)
            if stale >= int(args.patience):
                break
    if exact_update_mode and int(state["step"]) != target_updates:
        raise RuntimeError("inverse training did not complete its exact optimizer-update budget")
    return history, best


def _init_response_schedule_state(args: argparse.Namespace) -> dict[str, Any]:
    domain = str(getattr(args, "response_schedule_domain", "epoch"))
    if domain == "optimizer_update":
        total_units = max(1, int(getattr(args, "inverse_max_optimizer_updates", 0)))
    else:
        total_units = max(1, int(args.inverse_epochs))
    warmup_fraction = float(args.response_warmup_fraction)
    ramp_fraction = float(args.response_ramp_fraction)
    absolute_warmup = getattr(args, "response_warmup_optimizer_updates", None)
    absolute_ramp = getattr(args, "response_ramp_optimizer_updates", None)
    absolute_schedule = domain == "optimizer_update" and absolute_warmup is not None and absolute_ramp is not None
    if absolute_schedule:
        warmup_units = int(absolute_warmup)
        ramp_units = int(absolute_ramp)
        unit_source = "absolute_optimizer_updates"
    else:
        warmup_units = max(1, int(round(total_units * warmup_fraction))) if warmup_fraction > 0.0 else 0
        ramp_units = max(1, int(round(total_units * ramp_fraction))) if ramp_fraction > 0.0 else 0
        unit_source = "fraction_of_training_budget"
    if args.response_weight_schedule == "warmup_ramp_adaptive_ema" and warmup_units + ramp_units >= total_units:
        ramp_units = max(0, total_units - warmup_units - 1)
    return {
        "domain": domain,
        "total_units": total_units,
        "unit_source": unit_source,
        "warmup_units": warmup_units,
        "ramp_units": ramp_units,
        "warmup_epochs": warmup_units if domain == "epoch" else None,
        "ramp_epochs": ramp_units if domain == "epoch" else None,
        "warmup_optimizer_updates": warmup_units if domain == "optimizer_update" else None,
        "ramp_optimizer_updates": ramp_units if domain == "optimizer_update" else None,
        "ema_response_mse": None,
        "ema_geometry_mse": None,
        "reference_loss_ratio": None,
    }


def _response_weight_for_epoch(
    epoch: int,
    args: argparse.Namespace,
    state: dict[str, Any],
) -> tuple[float, str, float]:
    return _response_weight_for_progress(epoch, args, state)


def _response_weight_for_progress(
    progress: int,
    args: argparse.Namespace,
    state: dict[str, Any],
) -> tuple[float, str, float]:
    base = float(args.response_weight)
    if args.response_weight_schedule == "linear_ramp":
        ramp_units = max(1, int(state["ramp_units"]))
        multiplier = min(1.0, progress / ramp_units)
        return base * multiplier, "ramp" if multiplier < 1.0 else "fixed", multiplier
    warmup_units = int(state["warmup_units"])
    ramp_units = int(state["ramp_units"])
    if progress <= warmup_units:
        return 0.0, "warmup", 0.0
    if ramp_units > 0 and progress <= warmup_units + ramp_units:
        multiplier = (progress - warmup_units) / ramp_units
        return base * multiplier, "ramp", multiplier
    if float(getattr(args, "geometry_anchor_weight", 0.01)) == 0.0:
        return base, "response_only_fixed", 1.0
    response_mse = state.get("ema_response_mse")
    geometry_mse = state.get("ema_geometry_mse")
    reference = state.get("reference_loss_ratio")
    if response_mse is None or geometry_mse is None or reference is None or float(reference) <= 0.0:
        return base, "adaptive_ema", 1.0
    ratio = float(geometry_mse) / max(float(response_mse), 1.0e-18)
    multiplier = ratio / float(reference)
    multiplier = float(
        np.clip(
            multiplier,
            float(args.response_adaptive_min_multiplier),
            float(args.response_adaptive_max_multiplier),
        )
    )
    return base * multiplier, "adaptive_ema", multiplier


def _update_response_schedule_state(
    state: dict[str, Any],
    response_mse: float,
    geometry_mse: float,
    progress: int,
    args: argparse.Namespace,
) -> None:
    decay = float(args.response_adaptive_ema_decay)
    if state["ema_response_mse"] is None:
        state["ema_response_mse"] = float(response_mse)
        state["ema_geometry_mse"] = float(geometry_mse)
    else:
        state["ema_response_mse"] = decay * float(state["ema_response_mse"]) + (1.0 - decay) * float(response_mse)
        state["ema_geometry_mse"] = decay * float(state["ema_geometry_mse"]) + (1.0 - decay) * float(geometry_mse)
    adaptive_start = int(state["warmup_units"]) + int(state["ramp_units"])
    if (
        args.response_weight_schedule == "warmup_ramp_adaptive_ema"
        and progress >= adaptive_start
        and state["reference_loss_ratio"] is None
    ):
        state["reference_loss_ratio"] = float(state["ema_geometry_mse"]) / max(
            float(state["ema_response_mse"]),
            1.0e-18,
        )


def _refine_geometry_candidates(
    targets: np.ndarray,
    initial_geometry: np.ndarray,
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
    dimension_weights: np.ndarray,
    args: argparse.Namespace,
) -> tuple[np.ndarray, dict[str, Any]]:
    targets = np.asarray(targets, dtype=float)
    initial = np.asarray(initial_geometry, dtype=float)
    steps = int(args.local_refinement_steps)
    starts = int(args.local_refinement_starts)
    learning_rate = float(args.local_refinement_learning_rate)
    jitter_fraction = float(args.local_refinement_jitter)
    optimizer = str(getattr(args, "local_refinement_optimizer", "projected_gradient"))
    lr_schedule = str(getattr(args, "local_refinement_lr_schedule", "constant"))
    final_lr_fraction = float(
        getattr(args, "local_refinement_final_lr_fraction", 0.1)
    )
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        initial_response = _predict(initial, forward_weights, forward_biases)
        initial_scores = np.mean(
            (initial_response - targets) ** 2 * dimension_weights[None, :],
            axis=1,
        )
    initial_nonfinite_response = ~np.all(np.isfinite(initial_response), axis=1)
    initial_nonfinite_scores = ~np.isfinite(initial_scores)
    if steps == 0 or initial.shape[0] == 0:
        return initial.copy(), {
            "enabled": False,
            "method": "disabled",
            "steps": steps,
            "starts": starts,
            "learning_rate": learning_rate,
            "optimizer": optimizer,
            "learning_rate_schedule": lr_schedule,
            "final_learning_rate_fraction": final_lr_fraction,
            "jitter_fraction_of_geometry_span": jitter_fraction,
            "baseline_feature_balanced_normalized_rmse": float(np.sqrt(np.mean(initial_scores)))
            if initial_scores.size
            else 0.0,
            "selected_feature_balanced_normalized_rmse": float(np.sqrt(np.mean(initial_scores)))
            if initial_scores.size
            else 0.0,
            "improved_row_fraction": 0.0,
            "selection_nonworsening_by_construction": True,
            "initial_nonfinite_response_row_count": int(np.count_nonzero(initial_nonfinite_response)),
            "initial_nonfinite_score_row_count": int(np.count_nonzero(initial_nonfinite_scores)),
            "nonfinite_forward_event_count": 0,
            "nonfinite_gradient_event_count": 0,
            "nonfinite_proposal_event_count": 0,
            "rejected_nonfinite_update_count": 0,
            "nonfinite_refined_score_count": 0,
        }

    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    span = np.maximum(upper - lower, 1.0e-12)
    rng = np.random.default_rng(int(args.local_refinement_seed))
    candidates = np.repeat(initial[:, None, :], starts, axis=1)
    if starts > 1 and jitter_fraction > 0.0:
        candidates[:, 1:, :] += rng.normal(
            0.0,
            jitter_fraction,
            size=candidates[:, 1:, :].shape,
        ) * span[None, None, :]
    candidates = np.clip(candidates, lower[None, None, :], upper[None, None, :])
    flat = candidates.reshape(-1, candidates.shape[-1])
    repeated_targets = np.repeat(targets, starts, axis=0)
    nonfinite_forward_events = 0
    nonfinite_gradient_events = 0
    nonfinite_proposal_events = 0
    rejected_nonfinite_updates = 0
    affected_candidate_indices: set[int] = set()
    adam_first_moment = np.zeros_like(flat) if optimizer == "projected_adam" else None
    adam_second_moment = np.zeros_like(flat) if optimizer == "projected_adam" else None
    adam_beta1 = 0.9
    adam_beta2 = 0.999
    adam_epsilon = 1.0e-8

    for step_index in range(steps):
        if lr_schedule == "cosine_decay" and steps > 1:
            progress = step_index / float(steps - 1)
            learning_rate_multiplier = final_lr_fraction + 0.5 * (
                1.0 - final_lr_fraction
            ) * (1.0 + math.cos(math.pi * progress))
        else:
            learning_rate_multiplier = 1.0
        step_learning_rate = learning_rate * learning_rate_multiplier
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            prediction, _, preactivations = _forward_with_cache(
                flat,
                forward_weights,
                forward_biases,
            )
            grad_output = (
                2.0
                * (prediction - repeated_targets)
                * dimension_weights[None, :]
                / max(1, prediction.shape[1])
            )
            grad_geometry = _backward_input_gradient(
                grad_output,
                forward_weights,
                preactivations,
            )
            if optimizer == "projected_adam":
                finite_gradient_for_moments = np.all(np.isfinite(grad_geometry), axis=1)
                proposed_first_moment = adam_first_moment.copy()
                proposed_second_moment = adam_second_moment.copy()
                proposed_first_moment[finite_gradient_for_moments] = (
                    adam_beta1 * adam_first_moment[finite_gradient_for_moments]
                    + (1.0 - adam_beta1) * grad_geometry[finite_gradient_for_moments]
                )
                proposed_second_moment[finite_gradient_for_moments] = (
                    adam_beta2 * adam_second_moment[finite_gradient_for_moments]
                    + (1.0 - adam_beta2)
                    * grad_geometry[finite_gradient_for_moments] ** 2
                )
                bias_corrected_first = proposed_first_moment / (
                    1.0 - adam_beta1 ** (step_index + 1)
                )
                bias_corrected_second = proposed_second_moment / (
                    1.0 - adam_beta2 ** (step_index + 1)
                )
                update_direction = bias_corrected_first / (
                    np.sqrt(bias_corrected_second) + adam_epsilon
                )
                proposal = flat - step_learning_rate * update_direction
            else:
                proposal = flat - step_learning_rate * grad_geometry

        finite_forward = np.all(np.isfinite(prediction), axis=1)
        finite_gradient = np.all(np.isfinite(grad_geometry), axis=1)
        finite_proposal = np.all(np.isfinite(proposal), axis=1)
        valid_update = finite_forward & finite_gradient & finite_proposal
        nonfinite_forward_events += int(np.count_nonzero(~finite_forward))
        nonfinite_gradient_events += int(np.count_nonzero(~finite_gradient))
        nonfinite_proposal_events += int(np.count_nonzero(~finite_proposal))
        rejected_nonfinite_updates += int(np.count_nonzero(~valid_update))
        affected_candidate_indices.update(np.flatnonzero(~valid_update).tolist())
        if np.any(valid_update):
            flat[valid_update] = np.clip(
                proposal[valid_update],
                lower[None, :],
                upper[None, :],
            )
            if optimizer == "projected_adam":
                adam_first_moment[valid_update] = proposed_first_moment[valid_update]
                adam_second_moment[valid_update] = proposed_second_moment[valid_update]

    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        refined_response = _predict(flat, forward_weights, forward_biases)
        refined_scores_flat = np.mean(
            (refined_response - repeated_targets) ** 2 * dimension_weights[None, :],
            axis=1,
        )
    nonfinite_refined_scores = ~np.isfinite(refined_scores_flat)
    refined_scores_flat = np.where(nonfinite_refined_scores, np.inf, refined_scores_flat)
    refined_scores = refined_scores_flat.reshape(initial.shape[0], starts)
    candidate_geometry = flat.reshape(initial.shape[0], starts, initial.shape[1])
    selection_initial_scores = np.where(np.isfinite(initial_scores), initial_scores, np.inf)
    all_scores = np.concatenate([selection_initial_scores[:, None], refined_scores], axis=1)
    all_geometry = np.concatenate([initial[:, None, :], candidate_geometry], axis=1)
    selected_indices = np.argmin(all_scores, axis=1)
    selected = all_geometry[np.arange(initial.shape[0]), selected_indices]
    selected_scores = all_scores[np.arange(initial.shape[0]), selected_indices]
    improved = selected_scores + 1.0e-15 < initial_scores
    finite_reduction = np.isfinite(initial_scores) & np.isfinite(selected_scores)
    mean_reduction = (
        float(np.mean(initial_scores[finite_reduction] - selected_scores[finite_reduction]))
        if np.any(finite_reduction)
        else 0.0
    )
    return selected, {
        "enabled": True,
        "method": (
            "multi_start_projected_gradient_on_frozen_forward_feature_balanced_mse"
            if optimizer == "projected_gradient" and lr_schedule == "constant"
            else f"multi_start_{optimizer}_{lr_schedule}_on_frozen_forward_feature_balanced_mse"
        ),
        "steps": steps,
        "starts": starts,
        "learning_rate": learning_rate,
        "optimizer": optimizer,
        "learning_rate_schedule": lr_schedule,
        "final_learning_rate_fraction": final_lr_fraction,
        "final_learning_rate": (
            learning_rate * final_lr_fraction
            if lr_schedule == "cosine_decay" and steps > 1
            else learning_rate
        ),
        "adam_beta1": adam_beta1 if optimizer == "projected_adam" else None,
        "adam_beta2": adam_beta2 if optimizer == "projected_adam" else None,
        "adam_epsilon": adam_epsilon if optimizer == "projected_adam" else None,
        "jitter_fraction_of_geometry_span": jitter_fraction,
        "seed": int(args.local_refinement_seed),
        "baseline_feature_balanced_normalized_rmse": float(np.sqrt(np.mean(initial_scores))),
        "selected_feature_balanced_normalized_rmse": float(np.sqrt(np.mean(selected_scores))),
        "mean_feature_balanced_mse_reduction": mean_reduction,
        "improved_row_fraction": float(np.mean(improved)),
        "selected_unrefined_row_fraction": float(np.mean(selected_indices == 0)),
        "selection_nonworsening_by_construction": bool(np.all(selected_scores <= initial_scores + 1.0e-12)),
        "selection_basis": "frozen_forward_proxy_only",
        "initial_nonfinite_response_row_count": int(np.count_nonzero(initial_nonfinite_response)),
        "initial_nonfinite_score_row_count": int(np.count_nonzero(initial_nonfinite_scores)),
        "nonfinite_forward_event_count": int(nonfinite_forward_events),
        "nonfinite_gradient_event_count": int(nonfinite_gradient_events),
        "nonfinite_proposal_event_count": int(nonfinite_proposal_events),
        "rejected_nonfinite_update_count": int(rejected_nonfinite_updates),
        "affected_candidate_count": int(len(affected_candidate_indices)),
        "affected_source_row_count": int(
            len({index // starts for index in affected_candidate_indices})
        ),
        "nonfinite_refined_score_count": int(np.count_nonzero(nonfinite_refined_scores)),
        "nonfinite_update_policy": "retain_last_finite_candidate_and_record_diagnostics",
        "boundary": "Local refinement optimizes proxy response consistency only; DRC and real EMX closure remain mandatory.",
    }


def _evaluate(
    data: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    max_prediction_rows: int,
    args: argparse.Namespace,
    *,
    evaluation_split: str = "test",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    x, y, split = data["x"], data["y"], data["split"]
    if evaluation_split not in {"validation", "test"}:
        raise ValueError(f"unsupported evaluation split: {evaluation_split}")
    norm = data["normalization"]
    dimension_weights = np.asarray(norm["response_loss_dimension_weights"], dtype=float)
    lower, upper = norm["geometry_lower"], norm["geometry_upper"]
    evaluation_indices = np.asarray(split[evaluation_split], dtype=int)
    forward_prediction = _predict(y[evaluation_indices], forward_weights, forward_biases)
    initial_predicted_geometry = _predict_inverse(
        x[evaluation_indices],
        inverse_weights,
        inverse_biases,
        lower,
        upper,
        projection_mode=str(args.inverse_geometry_projection),
        normalization=norm,
        topology_contract=data.get("topology_feasibility_contract") or {},
    )
    predicted_geometry, local_refinement = _refine_geometry_candidates(
        x[evaluation_indices],
        initial_predicted_geometry,
        forward_weights,
        forward_biases,
        lower,
        upper,
        dimension_weights,
        args,
    )
    reconstructed_features = _predict(predicted_geometry, forward_weights, forward_biases)
    violations = (predicted_geometry < lower[None, :]) | (predicted_geometry > upper[None, :])

    target_physical = x[evaluation_indices] * norm["x_scale"][None, :] + norm["x_mean"][None, :]
    forward_physical = forward_prediction * norm["x_scale"][None, :] + norm["x_mean"][None, :]
    reconstructed_physical = reconstructed_features * norm["x_scale"][None, :] + norm["x_mean"][None, :]
    truth_geometry_physical = y[evaluation_indices] * norm["y_scale"][None, :] + norm["y_mean"][None, :]
    predicted_geometry_physical = predicted_geometry * norm["y_scale"][None, :] + norm["y_mean"][None, :]
    feature_mae = np.mean(np.abs(reconstructed_physical - target_physical), axis=0)
    forward_feature_mae = np.mean(np.abs(forward_physical - target_physical), axis=0)
    geometry_mae = np.mean(np.abs(predicted_geometry_physical - truth_geometry_physical), axis=0)
    split_audit = data.get("split_audit") or {}
    declared_lower = np.asarray(split_audit.get("physical_cell_lower") or [], dtype=float)
    declared_upper = np.asarray(split_audit.get("physical_cell_upper") or [], dtype=float)
    if declared_lower.shape == (len(input_columns),) and declared_upper.shape == (len(input_columns),):
        feature_range = np.maximum(declared_upper - declared_lower, 1.0e-12)
        feature_range_source = "declared_physical_cell_range"
    else:
        all_physical = x * norm["x_scale"][None, :] + norm["x_mean"][None, :]
        feature_range = np.maximum(np.max(all_physical, axis=0) - np.min(all_physical, axis=0), 1.0e-12)
        feature_range_source = "observed_full_dataset_range"
    forward_range_error = (forward_physical - target_physical) / feature_range[None, :]
    tandem_range_error = (reconstructed_physical - target_physical) / feature_range[None, :]
    response_loss_state = data.get("response_loss_state")
    forward_objective_rmse = _response_objective_rmse(
        forward_prediction,
        x[evaluation_indices],
        dimension_weights,
        response_loss_state,
    )
    tandem_objective_rmse = _response_objective_rmse(
        reconstructed_features,
        x[evaluation_indices],
        dimension_weights,
        response_loss_state,
        apply_target_semantics=True,
    )
    relative_state = (
        response_loss_state
        if response_loss_state is not None
        and response_loss_state.get("family") == "relative_mse"
        else None
    )
    if relative_state is not None:
        relative_floors = np.asarray(
            relative_state["denominator_floors_physical"],
            dtype=float,
        )
    else:
        relative_floors = np.maximum(np.abs(target_physical).min(axis=0), 1.0e-12)
    relative_denominator = np.maximum(
        np.abs(target_physical),
        relative_floors[None, :],
    )
    tandem_exact_relative_error = (
        reconstructed_physical - target_physical
    ) / relative_denominator
    tandem_relative_error = tandem_exact_relative_error.copy()
    minimum_target_indices = (
        np.asarray(relative_state.get("minimum_target_indices", []), dtype=int)
        if relative_state is not None
        else np.asarray([], dtype=int)
    )
    if minimum_target_indices.size:
        tandem_relative_error[:, minimum_target_indices] = np.minimum(
            tandem_relative_error[:, minimum_target_indices],
            0.0,
        )
    common_contract = _common_physical_contract_metrics(
        input_columns,
        target_physical,
        reconstructed_physical,
        evaluation_split=evaluation_split,
        q_target_semantics=str(
            (data.get("response_loss_contract") or {}).get("q_target_semantics")
            or "exact"
        ),
    )
    topology_penalty, _topology_gradient, topology_diagnostics = _topology_feasibility_penalty_and_gradient(
        predicted_geometry,
        norm,
        data.get("topology_feasibility_contract") or {},
    )

    metrics = {
        "forward_proxy": {
            f"{evaluation_split}_normalized_rmse": _rmse(forward_prediction, x[evaluation_indices]),
            f"{evaluation_split}_feature_balanced_normalized_rmse": _weighted_rmse(
                forward_prediction,
                x[evaluation_indices],
                dimension_weights,
            ),
            f"{evaluation_split}_normalized_mae": float(
                np.mean(np.abs(forward_prediction - x[evaluation_indices]))
            ),
            f"{evaluation_split}_normalized_r2": _r2(forward_prediction, x[evaluation_indices]),
            f"{evaluation_split}_range_normalized_rmse": float(np.sqrt(np.mean(forward_range_error**2))),
            f"{evaluation_split}_range_normalized_mae": float(np.mean(np.abs(forward_range_error))),
            f"{evaluation_split}_response_objective_rmse": forward_objective_rmse,
        },
        "tandem_inverse": {
            f"{evaluation_split}_response_normalized_rmse": _rmse(
                reconstructed_features,
                x[evaluation_indices],
            ),
            f"{evaluation_split}_feature_balanced_response_normalized_rmse": _weighted_rmse(
                reconstructed_features,
                x[evaluation_indices],
                dimension_weights,
            ),
            f"{evaluation_split}_response_normalized_mae": float(
                np.mean(np.abs(reconstructed_features - x[evaluation_indices]))
            ),
            f"{evaluation_split}_response_normalized_r2": _r2(
                reconstructed_features,
                x[evaluation_indices],
            ),
            f"{evaluation_split}_response_range_normalized_rmse": float(
                np.sqrt(np.mean(tandem_range_error**2))
            ),
            f"{evaluation_split}_response_range_normalized_mae": float(
                np.mean(np.abs(tandem_range_error))
            ),
            f"{evaluation_split}_response_objective_rmse": tandem_objective_rmse,
            f"{evaluation_split}_response_relative_physical_rmse": float(
                np.sqrt(np.mean(tandem_relative_error**2))
            ),
            f"{evaluation_split}_response_relative_physical_mae": float(
                np.mean(np.abs(tandem_relative_error))
            ),
            f"{evaluation_split}_response_exact_relative_physical_rmse": float(
                np.sqrt(np.mean(tandem_exact_relative_error**2))
            ),
            f"{evaluation_split}_response_exact_relative_physical_mae": float(
                np.mean(np.abs(tandem_exact_relative_error))
            ),
            f"{evaluation_split}_geometry_anchor_normalized_rmse": _rmse(
                predicted_geometry,
                y[evaluation_indices],
            ),
            "geometry_envelope_sample_violation_rate": float(np.mean(np.any(violations, axis=1))),
            "geometry_envelope_element_violation_rate": float(np.mean(violations)),
            "geometry_envelope_definition": "observed_training_min_max_per_dimension_not_drc",
            "topology_feasibility_penalty": float(topology_penalty),
            "topology_feasibility": topology_diagnostics,
            "local_refinement": local_refinement,
        },
        "input_noise_robustness": (
            _input_noise_robustness(
                data,
                forward_weights,
                forward_biases,
                inverse_weights,
                inverse_biases,
                args,
            )
            if evaluation_split == "test"
            else {
                "status": "NOT_RUN_ON_VALIDATION_SELECTION_SPLIT",
                "boundary": "Robustness is reported once on the frozen final test evaluation, not used to select architecture.",
            }
        ),
        "per_feature_forward_proxy_physical_mae": {
            column: float(forward_feature_mae[index]) for index, column in enumerate(input_columns)
        },
        "per_feature_physical_mae": {column: float(feature_mae[index]) for index, column in enumerate(input_columns)},
        "per_feature_range_normalized_mae": {
            column: float(feature_mae[index] / feature_range[index]) for index, column in enumerate(input_columns)
        },
        "per_feature_relative_physical_mae": {
            column: float(np.mean(np.abs(tandem_relative_error[:, index])))
            for index, column in enumerate(input_columns)
        },
        "per_feature_exact_relative_physical_mae": {
            column: float(np.mean(np.abs(tandem_exact_relative_error[:, index])))
            for index, column in enumerate(input_columns)
        },
        "range_normalization": {
            "source": feature_range_source,
            "feature_span": {column: float(feature_range[index]) for index, column in enumerate(input_columns)},
            "boundary": "Range-normalized metrics use a fixed declared physical span when available and are the preferred cross-checkpoint learning-curve metrics.",
        },
        "common_lp_ls_qmin_absk_contract": common_contract,
        "per_geometry_anchor_mae": {column: float(geometry_mae[index]) for index, column in enumerate(geometry_columns)},
        f"{evaluation_split}_row_count": int(len(evaluation_indices)),
    }

    rows: list[dict[str, Any]] = []
    for local_index, matrix_index in enumerate(evaluation_indices[: max(0, max_prediction_rows)]):
        row: dict[str, Any] = {
            f"{evaluation_split}_index": local_index,
            "evaluation_split": evaluation_split,
            "matrix_index": int(matrix_index),
            "source_row_index": int(data["source_indices"][int(matrix_index)]),
            "source_evaluation": str(data["source_evaluations"][int(matrix_index)]),
            "source_geometry_identity_sha256": str(
                data["source_geometry_identities"][int(matrix_index)]
            ),
        }
        for column_index, column in enumerate(input_columns):
            row[f"target__{column.removeprefix('input__')}"] = float(target_physical[local_index, column_index])
            row[f"forward__{column.removeprefix('input__')}"] = float(forward_physical[local_index, column_index])
            row[f"reconstructed__{column.removeprefix('input__')}"] = float(reconstructed_physical[local_index, column_index])
        for column_index, column in enumerate(geometry_columns):
            name = column.removeprefix("geom__")
            row[f"paired_geometry__{name}"] = float(truth_geometry_physical[local_index, column_index])
            row[f"predicted_geometry__{name}"] = float(predicted_geometry_physical[local_index, column_index])
        rows.append(row)
    return metrics, rows


def _common_physical_contract_metrics(
    input_columns: list[str],
    target_physical: np.ndarray,
    reconstructed_physical: np.ndarray,
    *,
    evaluation_split: str = "test",
    q_target_semantics: str = "exact",
) -> dict[str, Any]:
    """Reduce one same-frequency response group to Lp/Ls/Qmin/|K|."""

    normalized_names = [_strip_physical_feature_provenance(column) for column in input_columns]
    groups: dict[str, dict[str, int]] = {}
    prefixes = (
        ("lp_nh_", "lp"),
        ("ls_nh_", "ls"),
        ("k_abs_", "k"),
        ("qp_", "qp"),
        ("qs_", "qs"),
        ("q_", "q"),
        ("k_", "k"),
    )
    for index, name in enumerate(normalized_names):
        for prefix, semantic in prefixes:
            if name.startswith(prefix):
                frequency_tag = name[len(prefix) :]
                if frequency_tag:
                    groups.setdefault(frequency_tag, {})[semantic] = index
                break
    complete_groups = {
        tag: indices
        for tag, indices in groups.items()
        if {"lp", "ls", "k"}.issubset(indices)
        and ("q" in indices or {"qp", "qs"}.issubset(indices))
    }
    if not complete_groups:
        return {
            "status": "UNAVAILABLE",
            "input_columns": input_columns,
            "reason": "A same-frequency Lp/Ls/K group and either Q or both Qp/Qs are required.",
        }

    preferred_tags = ("center", "f015ghz")
    selected_tag = next((tag for tag in preferred_tags if tag in complete_groups), sorted(complete_groups)[0])
    indices = complete_groups[selected_tag]
    lp_index = indices["lp"]
    ls_index = indices["ls"]
    k_index = indices["k"]
    q_index = indices.get("q")
    qp_index = indices.get("qp")
    qs_index = indices.get("qs")
    if q_index is not None:
        target_q = target_physical[:, q_index]
        reconstructed_q = reconstructed_physical[:, q_index]
        q_representation = "Q_scalar"
    else:
        assert qp_index is not None and qs_index is not None
        target_q = np.minimum(target_physical[:, qp_index], target_physical[:, qs_index])
        reconstructed_q = np.minimum(reconstructed_physical[:, qp_index], reconstructed_physical[:, qs_index])
        q_representation = "min_Qp_Qs"

    target = np.column_stack(
        [target_physical[:, lp_index], target_physical[:, ls_index], target_q, target_physical[:, k_index]]
    )
    reconstructed = np.column_stack(
        [
            reconstructed_physical[:, lp_index],
            reconstructed_physical[:, ls_index],
            reconstructed_q,
            reconstructed_physical[:, k_index],
        ]
    )
    spans = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)
    errors = reconstructed - target
    exact_errors = errors.copy()
    if q_target_semantics == "minimum":
        errors[:, 2] = np.minimum(errors[:, 2], 0.0)
    elif q_target_semantics != "exact":
        raise ValueError(f"unsupported Q target semantics: {q_target_semantics}")
    range_errors = errors / spans[None, :]
    names = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")
    return {
        "status": "PASS",
        "q_representation": q_representation,
        "q_target_semantics": q_target_semantics,
        "selected_frequency_tag": selected_tag,
        "available_frequency_groups": sorted(complete_groups),
        "feature_order": list(names),
        "fixed_feature_spans": {name: float(spans[index]) for index, name in enumerate(names)},
        f"{evaluation_split}_range_normalized_rmse": float(np.sqrt(np.mean(range_errors**2))),
        f"{evaluation_split}_range_normalized_mae": float(np.mean(np.abs(range_errors))),
        "per_feature_physical_mae": {
            name: float(np.mean(np.abs(errors[:, index]))) for index, name in enumerate(names)
        },
        "per_feature_exact_physical_mae": {
            name: float(np.mean(np.abs(exact_errors[:, index]))) for index, name in enumerate(names)
        },
        "boundary": (
            "Q error is one-sided only when q_target_semantics=minimum; Q above the requested floor is not a failure. "
            "This metric is valid for Q versus Qp/Qs comparison only when both models use the same frequency tag. "
            "Cross-frequency input representations require one shared canonical evaluator and new real-EM closure."
        ),
    }


def _input_noise_robustness(
    data: dict[str, Any],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    args: argparse.Namespace,
) -> dict[str, Any]:
    x = np.asarray(data["x"], dtype=float)
    norm = data["normalization"]
    lower = np.asarray(norm["geometry_lower"], dtype=float)
    upper = np.asarray(norm["geometry_upper"], dtype=float)
    test = np.asarray(data["split"]["test"], dtype=int)
    row_limit = max(1, int(args.robustness_max_rows))
    audit_indices = test[: min(len(test), row_limit)]
    clean_normalized = x[audit_indices]
    clean_physical = clean_normalized * norm["x_scale"][None, :] + norm["x_mean"][None, :]
    clean_initial_geometry = _predict_inverse(
        clean_normalized,
        inverse_weights,
        inverse_biases,
        lower,
        upper,
        projection_mode=str(args.inverse_geometry_projection),
        normalization=norm,
        topology_contract=data.get("topology_feasibility_contract") or {},
    )
    clean_geometry, _clean_refinement = _refine_geometry_candidates(
        clean_normalized,
        clean_initial_geometry,
        forward_weights,
        forward_biases,
        lower,
        upper,
        np.asarray(norm["response_loss_dimension_weights"], dtype=float),
        args,
    )
    feature_lower = np.asarray(norm["feature_lower"], dtype=float)
    feature_upper = np.asarray(norm["feature_upper"], dtype=float)
    noise_levels = _parse_noise_levels(args.robustness_noise_levels)
    repeats = max(1, int(args.robustness_repeats))
    rng = np.random.default_rng(int(args.robustness_seed))
    records: list[dict[str, Any]] = []
    for level in noise_levels:
        clean_response_rmse: list[float] = []
        noisy_response_rmse: list[float] = []
        geometry_shift_rmse: list[float] = []
        clipped_fraction: list[float] = []
        for _ in range(repeats):
            perturbation = rng.normal(0.0, level, size=clean_physical.shape)
            noisy_unclipped = clean_physical * (1.0 + perturbation)
            noisy_physical = np.clip(noisy_unclipped, feature_lower[None, :], feature_upper[None, :])
            clipped_fraction.append(float(np.mean(noisy_physical != noisy_unclipped)))
            noisy_normalized = (noisy_physical - norm["x_mean"][None, :]) / norm["x_scale"][None, :]
            noisy_initial_geometry = _predict_inverse(
                noisy_normalized,
                inverse_weights,
                inverse_biases,
                lower,
                upper,
                projection_mode=str(args.inverse_geometry_projection),
                normalization=norm,
                topology_contract=data.get("topology_feasibility_contract") or {},
            )
            noisy_geometry, _noisy_refinement = _refine_geometry_candidates(
                noisy_normalized,
                noisy_initial_geometry,
                forward_weights,
                forward_biases,
                lower,
                upper,
                np.asarray(norm["response_loss_dimension_weights"], dtype=float),
                args,
            )
            reconstructed = _predict(noisy_geometry, forward_weights, forward_biases)
            clean_response_rmse.append(_rmse(reconstructed, clean_normalized))
            noisy_response_rmse.append(_rmse(reconstructed, noisy_normalized))
            geometry_shift_rmse.append(_rmse(noisy_geometry, clean_geometry))
        records.append(
            {
                "relative_noise_std": float(level),
                "clean_target_response_normalized_rmse_mean": float(np.mean(clean_response_rmse)),
                "clean_target_response_normalized_rmse_std": float(np.std(clean_response_rmse)),
                "noisy_target_response_normalized_rmse_mean": float(np.mean(noisy_response_rmse)),
                "noisy_target_response_normalized_rmse_std": float(np.std(noisy_response_rmse)),
                "predicted_geometry_shift_normalized_rmse_mean": float(np.mean(geometry_shift_rmse)),
                "predicted_geometry_shift_normalized_rmse_std": float(np.std(geometry_shift_rmse)),
                "input_element_clipped_fraction_mean": float(np.mean(clipped_fraction)),
            }
        )
    return {
        "status": "AUDIT_ONLY_NO_PASS_GATE",
        "method": "relative_Gaussian_noise_on_physical_targets_then_clip_to_observed_training_feature_envelope",
        "reference": "TMTT-2026-02-0420_Proof_hi.pdf, PDF page 13 robustness experiment",
        "inverse_inference": "with_local_refinement_when_enabled",
        "audit_row_count": int(len(audit_indices)),
        "repeats_per_level": repeats,
        "seed": int(args.robustness_seed),
        "levels": records,
        "interpretation_boundary": "This measures frozen-proxy stability only; it is not real EMX or HFSS validation.",
    }


def _parse_noise_levels(raw: str) -> list[float]:
    levels = [float(item.strip()) for item in str(raw).split(",") if item.strip()]
    if not levels:
        raise ValueError("robustness noise levels must not be empty")
    if any(not math.isfinite(level) or level < 0.0 or level > 1.0 for level in levels):
        raise ValueError("robustness noise levels must be finite fractions in [0, 1]")
    return levels


def _parse_positive_float_list(raw: str) -> list[float]:
    values = [float(item.strip()) for item in str(raw).split(",") if item.strip()]
    if not values:
        raise ValueError("values must not be empty")
    if any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise ValueError("values must be finite and positive")
    return values


def _batches(indices: np.ndarray, batch_size: int, rng: np.random.Generator) -> list[np.ndarray]:
    shuffled = rng.permutation(np.asarray(indices, dtype=int))
    return [shuffled[start : start + max(1, batch_size)] for start in range(0, len(shuffled), max(1, batch_size))]


def _draw_joint_cell_balanced_epoch_indices(
    sampler_state: dict[str, Any],
    rng: np.random.Generator,
) -> np.ndarray:
    if sampler_state.get("family") != "joint_cell_balanced":
        raise ValueError("balanced epoch drawing requires joint_cell_balanced sampler state")
    rows_by_cell = tuple(sampler_state.get("row_indices_by_cell") or ())
    draw_count = int(sampler_state.get("draws_per_epoch", 0))
    cell_count = len(rows_by_cell)
    if draw_count < 1 or cell_count < 2 or any(len(indices) == 0 for indices in rows_by_cell):
        raise ValueError("joint-cell-balanced sampler state is incomplete")
    base_draws, remainder = divmod(draw_count, cell_count)
    draws_by_cell = np.full(cell_count, base_draws, dtype=int)
    if remainder:
        draws_by_cell[rng.permutation(cell_count)[:remainder]] += 1
    selected: list[np.ndarray] = []
    for candidates, count in zip(rows_by_cell, draws_by_cell):
        candidate_indices = np.asarray(candidates, dtype=int)
        selected.append(rng.choice(candidate_indices, size=int(count), replace=True))
    epoch_indices = np.concatenate(selected).astype(int, copy=False)
    if epoch_indices.size != draw_count or int(np.max(draws_by_cell) - np.min(draws_by_cell)) > 1:
        raise ValueError("joint-cell-balanced sampler violated its equal-budget draw contract")
    return rng.permutation(epoch_indices)


def _init_continuous_permutation_batch_state(data: dict[str, Any]) -> dict[str, Any]:
    sampler_state = data.get("training_batch_sampler_state") or {}
    if sampler_state.get("family") != "row_uniform":
        raise ValueError("continuous-permutation batches require the row-uniform sampler")
    train_indices = np.asarray(sampler_state.get("train_indices"), dtype=int)
    if train_indices.ndim != 1 or train_indices.size == 0:
        raise ValueError("continuous-permutation batches require non-empty train indices")
    return {
        "train_indices": train_indices.copy(),
        "permutation": np.asarray([], dtype=int),
        "cursor": 0,
        "completed_permutations": 0,
        "emitted_row_draws": 0,
    }


def _draw_continuous_permutation_full_batch(
    state: dict[str, Any],
    batch_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    size = max(1, int(batch_size))
    train_indices = np.asarray(state.get("train_indices"), dtype=int)
    if train_indices.ndim != 1 or train_indices.size == 0:
        raise ValueError("continuous-permutation state has no train rows")
    parts: list[np.ndarray] = []
    remaining = size
    while remaining:
        permutation = np.asarray(state.get("permutation"), dtype=int)
        cursor = int(state.get("cursor", 0))
        if permutation.shape != train_indices.shape or cursor >= permutation.size:
            if permutation.size and cursor >= permutation.size:
                state["completed_permutations"] = int(
                    state.get("completed_permutations", 0)
                ) + 1
            permutation = rng.permutation(train_indices)
            cursor = 0
            state["permutation"] = permutation
        take = min(remaining, int(permutation.size) - cursor)
        if take < 1:
            raise RuntimeError("continuous-permutation sampler could not fill a batch")
        parts.append(permutation[cursor : cursor + take])
        cursor += take
        remaining -= take
        state["cursor"] = cursor
    batch = np.concatenate(parts).astype(int, copy=False)
    if batch.shape != (size,) or np.any(~np.isin(batch, train_indices)):
        raise RuntimeError("continuous-permutation sampler violated its fixed full-batch contract")
    state["emitted_row_draws"] = int(state.get("emitted_row_draws", 0)) + size
    return batch


def _training_batches(
    data: dict[str, Any],
    batch_size: int,
    rng: np.random.Generator,
    *,
    continuous_state: dict[str, Any] | None = None,
    exact_batch_count: int | None = None,
) -> list[np.ndarray]:
    if continuous_state is not None:
        if exact_batch_count is None or int(exact_batch_count) < 0:
            raise ValueError("continuous-permutation batches require a nonnegative exact batch count")
        return [
            _draw_continuous_permutation_full_batch(
                continuous_state,
                int(batch_size),
                rng,
            )
            for _ in range(int(exact_batch_count))
        ]
    if exact_batch_count is not None:
        raise ValueError("exact batch count is only valid with continuous-permutation state")
    sampler_state = data.get("training_batch_sampler_state") or {}
    family = sampler_state.get("family")
    if family == "row_uniform":
        epoch_indices = rng.permutation(np.asarray(sampler_state["train_indices"], dtype=int))
    elif family == "joint_cell_balanced":
        epoch_indices = _draw_joint_cell_balanced_epoch_indices(sampler_state, rng)
    else:
        raise ValueError(f"unsupported or missing training batch sampler state: {family}")
    draw_count = int(sampler_state.get("draws_per_epoch", 0))
    train_indices = np.asarray(sampler_state.get("train_indices"), dtype=int)
    if epoch_indices.size != draw_count or np.any(~np.isin(epoch_indices, train_indices)):
        raise ValueError("training batch sampler emitted rows outside its train-only budget")
    size = max(1, int(batch_size))
    return [epoch_indices[start : start + size] for start in range(0, len(epoch_indices), size)]


def _rmse(prediction: np.ndarray, truth: np.ndarray) -> float:
    return float(np.sqrt(np.mean((prediction - truth) ** 2)))


def _response_loss_and_gradient(
    prediction: np.ndarray,
    truth: np.ndarray,
    dimension_weights: np.ndarray,
    response_loss_state: dict[str, Any] | None,
    *,
    apply_target_semantics: bool = False,
) -> tuple[float, np.ndarray]:
    """Return the configured response objective and its mean-batch gradient."""

    prediction_values = np.asarray(prediction, dtype=float)
    truth_values = np.asarray(truth, dtype=float)
    weights = np.asarray(dimension_weights, dtype=float)
    if (
        prediction_values.shape != truth_values.shape
        or prediction_values.ndim != 2
        or weights.shape != (prediction_values.shape[1],)
    ):
        raise ValueError("response loss received incompatible shapes")
    if prediction_values.shape[0] == 0:
        raise ValueError("response loss requires at least one row")
    if np.any(~np.isfinite(prediction_values)) or np.any(~np.isfinite(truth_values)):
        raise ValueError("response loss inputs must be finite")
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0.0):
        raise ValueError("response loss dimension weights must be finite and positive")

    if response_loss_state is None:
        loss = float(np.mean((prediction_values - truth_values) ** 2 * weights[None, :]))
        gradient = (
            2.0
            * (prediction_values - truth_values)
            * weights[None, :]
            / float(prediction_values.size)
        )
        return loss, gradient

    family = str(response_loss_state.get("family") or "")
    if family == "relative_mse":
        x_mean = np.asarray(response_loss_state["x_mean"], dtype=float)
        x_scale = np.asarray(response_loss_state["x_scale"], dtype=float)
        floors = np.asarray(
            response_loss_state["denominator_floors_physical"],
            dtype=float,
        )
        expected_shape = (prediction_values.shape[1],)
        if (
            x_mean.shape != expected_shape
            or x_scale.shape != expected_shape
            or floors.shape != expected_shape
            or np.any(~np.isfinite(x_mean))
            or np.any(~np.isfinite(x_scale))
            or np.any(~np.isfinite(floors))
            or np.any(x_scale <= 0.0)
            or np.any(floors <= 0.0)
        ):
            raise ValueError("relative MSE response-loss state is invalid")
        prediction_physical = prediction_values * x_scale[None, :] + x_mean[None, :]
        truth_physical = truth_values * x_scale[None, :] + x_mean[None, :]
        denominator = np.maximum(np.abs(truth_physical), floors[None, :])
        relative_error = (prediction_physical - truth_physical) / denominator
        semantic_loss_weights = np.asarray(
            response_loss_state.get(
                "semantic_loss_weights",
                np.ones(prediction_values.shape[1], dtype=float),
            ),
            dtype=float,
        )
        if (
            semantic_loss_weights.shape != expected_shape
            or np.any(~np.isfinite(semantic_loss_weights))
            or np.any(semantic_loss_weights <= 0.0)
        ):
            raise ValueError(
                "relative MSE semantic loss weights are invalid"
            )
        if apply_target_semantics:
            minimum_target_indices = np.asarray(
                response_loss_state.get("minimum_target_indices", []),
                dtype=int,
            )
            if (
                minimum_target_indices.ndim != 1
                or len(set(int(index) for index in minimum_target_indices))
                != minimum_target_indices.size
                or np.any(minimum_target_indices < 0)
                or np.any(minimum_target_indices >= prediction_values.shape[1])
            ):
                raise ValueError("relative MSE minimum-target indices are invalid")
            q_minimum_margin_physical = float(
                response_loss_state.get("q_minimum_margin_physical", 0.0)
            )
            if (
                not math.isfinite(q_minimum_margin_physical)
                or q_minimum_margin_physical < 0.0
            ):
                raise ValueError(
                    "relative MSE Q minimum margin is invalid"
                )
            relative_error[:, minimum_target_indices] = (
                prediction_physical[:, minimum_target_indices]
                - truth_physical[:, minimum_target_indices]
                - q_minimum_margin_physical
            ) / denominator[:, minimum_target_indices]
            relative_error[:, minimum_target_indices] = np.minimum(
                relative_error[:, minimum_target_indices],
                0.0,
            )
        loss = float(
            np.mean(relative_error**2 * semantic_loss_weights[None, :])
        )
        gradient = (
            2.0
            * relative_error
            * semantic_loss_weights[None, :]
            * x_scale[None, :]
            / denominator
            / float(prediction_values.size)
        )
        if not math.isfinite(loss) or np.any(~np.isfinite(gradient)):
            raise ValueError("relative MSE produced a non-finite objective or gradient")
        return loss, gradient

    if family != "balanced_mse_bni":
        raise ValueError(f"unsupported response loss state: {family}")
    temperature = float(response_loss_state["temperature"])
    centers = np.asarray(response_loss_state["centers_normalized"], dtype=float)
    priors = np.asarray(response_loss_state["priors"], dtype=float)
    feature_count = prediction_values.shape[1]
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("Balanced-MSE BNI temperature must be finite and positive")
    if centers.ndim != 2 or centers.shape[1] != feature_count or centers.shape[0] < 2:
        raise ValueError("Balanced-MSE BNI centers have incompatible dimensions")
    if priors.shape != (centers.shape[0],) or np.any(~np.isfinite(priors)) or np.any(priors <= 0.0):
        raise ValueError("Balanced-MSE BNI priors must be finite and positive")
    if not math.isclose(float(np.sum(priors)), 1.0, rel_tol=1.0e-10, abs_tol=1.0e-12):
        raise ValueError("Balanced-MSE BNI priors must sum to one")

    weighted_prediction = prediction_values * weights[None, :]
    prediction_square = np.sum(weighted_prediction * prediction_values, axis=1)[:, None]
    center_square = np.sum((centers**2) * weights[None, :], axis=1)[None, :]
    cross = weighted_prediction @ centers.T
    distances = (prediction_square + center_square - 2.0 * cross) / float(feature_count)
    positive_distance = np.mean(
        (prediction_values - truth_values) ** 2 * weights[None, :],
        axis=1,
    )
    logits = np.log(priors)[None, :] - distances / temperature
    row_max = np.max(logits, axis=1, keepdims=True)
    exp_shifted = np.exp(logits - row_max)
    normalizer = np.sum(exp_shifted, axis=1, keepdims=True)
    probabilities = exp_shifted / normalizer
    log_integral = row_max[:, 0] + np.log(normalizer[:, 0])
    loss = float(np.mean(positive_distance / temperature + log_integral))

    posterior_center = probabilities @ centers
    gradient = (
        2.0
        * weights[None, :]
        * (
            (prediction_values - truth_values)
            - (prediction_values - posterior_center)
        )
        / (float(prediction_values.shape[0] * feature_count) * temperature)
    )
    if not math.isfinite(loss) or np.any(~np.isfinite(gradient)):
        raise ValueError("Balanced-MSE BNI produced a non-finite objective or gradient")
    return loss, gradient


def _response_objective_rmse(
    prediction: np.ndarray,
    truth: np.ndarray,
    dimension_weights: np.ndarray,
    response_loss_state: dict[str, Any] | None,
    *,
    apply_target_semantics: bool = False,
) -> float:
    if response_loss_state is not None and response_loss_state.get("family") == "balanced_mse_bni":
        return _weighted_rmse(prediction, truth, dimension_weights)
    loss, _gradient = _response_loss_and_gradient(
        prediction,
        truth,
        dimension_weights,
        response_loss_state,
        apply_target_semantics=apply_target_semantics,
    )
    if not math.isfinite(loss) or loss < 0.0:
        raise ValueError("response objective RMSE received a non-finite or negative loss")
    return float(math.sqrt(loss))


def _weighted_mse(prediction: np.ndarray, truth: np.ndarray, dimension_weights: np.ndarray) -> float:
    weights = np.asarray(dimension_weights, dtype=float)
    if prediction.shape != truth.shape or prediction.ndim != 2 or weights.shape != (prediction.shape[1],):
        raise ValueError("feature-balanced response loss received incompatible shapes")
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0.0):
        raise ValueError("feature-balanced response weights must be finite and positive")
    return float(np.mean((prediction - truth) ** 2 * weights[None, :]))


def _weighted_rmse(prediction: np.ndarray, truth: np.ndarray, dimension_weights: np.ndarray) -> float:
    return float(math.sqrt(_weighted_mse(prediction, truth, dimension_weights)))


def _r2(prediction: np.ndarray, truth: np.ndarray) -> float:
    residual = float(np.sum((prediction - truth) ** 2))
    centered = truth - np.mean(truth, axis=0, keepdims=True)
    total = float(np.sum(centered**2))
    return float(1.0 - residual / total) if total > 0.0 else float("nan")


def _gelu(value: np.ndarray) -> np.ndarray:
    constant = math.sqrt(2.0 / math.pi)
    return 0.5 * value * (1.0 + np.tanh(constant * (value + 0.044715 * value**3)))


def _gelu_derivative(value: np.ndarray) -> np.ndarray:
    constant = math.sqrt(2.0 / math.pi)
    inner = constant * (value + 0.044715 * value**3)
    tanh_inner = np.tanh(inner)
    return 0.5 * (1.0 + tanh_inner) + 0.5 * value * (1.0 - tanh_inner**2) * constant * (1.0 + 3.0 * 0.044715 * value**2)


def _save_weights(
    path: Path,
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    data: dict[str, Any],
) -> None:
    arrays: dict[str, np.ndarray] = {}
    for index, value in enumerate(forward_weights):
        arrays[f"forward_weight_{index}"] = value
    for index, value in enumerate(forward_biases):
        arrays[f"forward_bias_{index}"] = value
    for index, value in enumerate(inverse_weights):
        arrays[f"inverse_weight_{index}"] = value
    for index, value in enumerate(inverse_biases):
        arrays[f"inverse_bias_{index}"] = value
    for key, value in data["normalization"].items():
        arrays[f"normalization__{key}"] = np.asarray(value, dtype=float)
    normalization_contract = data.get("normalization_contract") or {}
    arrays["normalization_contract__mode"] = np.asarray(
        [str(normalization_contract.get("mode") or "")]
    )
    arrays["normalization_contract__sha256"] = np.asarray(
        [str(normalization_contract.get("sha256") or "")]
    )
    response_loss_state = data.get("response_loss_state")
    bni_state = (
        response_loss_state
        if response_loss_state is not None
        and response_loss_state.get("family") == "balanced_mse_bni"
        else None
    )
    if bni_state is not None:
        arrays["response_loss__bni_temperature"] = np.asarray(
            [float(bni_state["temperature"])],
            dtype=float,
        )
        for key in (
            "centers_normalized",
            "centers_physical",
            "priors",
            "cell_indices",
            "cell_row_counts",
        ):
            arrays[f"response_loss__bni_{key}"] = np.asarray(bni_state[key])
    relative_state = (
        response_loss_state
        if response_loss_state is not None
        and response_loss_state.get("family") == "relative_mse"
        else None
    )
    if relative_state is not None:
        arrays["response_loss__relative_error_floors_physical"] = np.asarray(
            relative_state["denominator_floors_physical"],
            dtype=float,
        )
        arrays["response_loss__minimum_target_indices"] = np.asarray(
            relative_state.get("minimum_target_indices", []),
            dtype=np.int64,
        )
        arrays["response_loss__semantic_loss_weights"] = np.asarray(
            relative_state.get("semantic_loss_weights", []),
            dtype=float,
        )
        arrays["response_loss__q_minimum_margin_physical"] = np.asarray(
            [float(relative_state.get("q_minimum_margin_physical", 0.0))],
            dtype=float,
        )
    sampler_contract = data.get("training_batch_sampler_contract") or {}
    arrays["training_sampler__family"] = np.asarray([str(sampler_contract.get("family") or "")])
    arrays["training_sampler__fingerprint_sha256"] = np.asarray(
        [str(sampler_contract.get("fingerprint_sha256") or "")]
    )
    arrays["training_sampler__draws_per_epoch"] = np.asarray(
        [int(sampler_contract.get("draws_per_epoch") or 0)],
        dtype=np.int64,
    )
    arrays["training_sampler__optimizer_updates_per_epoch"] = np.asarray(
        [int(sampler_contract.get("optimizer_updates_per_epoch") or 0)],
        dtype=np.int64,
    )
    optimizer_budget = data.get("optimizer_budget_contract") or {}
    arrays["optimizer_budget__mode"] = np.asarray(
        [str(optimizer_budget.get("mode") or "")]
    )
    arrays["optimizer_budget__fingerprint_sha256"] = np.asarray(
        [str(optimizer_budget.get("fingerprint_sha256") or "")]
    )
    arrays["optimizer_budget__forward_target_updates"] = np.asarray(
        [int((optimizer_budget.get("forward") or {}).get("target_optimizer_updates") or 0)],
        dtype=np.int64,
    )
    arrays["optimizer_budget__inverse_target_updates"] = np.asarray(
        [int((optimizer_budget.get("inverse") or {}).get("target_optimizer_updates") or 0)],
        dtype=np.int64,
    )
    arrays["inverse_geometry_projection__mode"] = np.asarray(
        [str(data.get("inverse_geometry_projection_mode") or "independent_sigmoid")]
    )
    topology_contract_json = json.dumps(
        _json_safe(data.get("topology_feasibility_contract") or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    arrays["inverse_geometry_projection__topology_contract_json"] = np.asarray(
        [topology_contract_json]
    )
    sampler_state = data.get("training_batch_sampler_state") or {}
    if sampler_state.get("family") == "joint_cell_balanced":
        arrays["training_sampler__occupied_cells"] = np.asarray(sampler_state["occupied_cells"], dtype=np.int64)
        arrays["training_sampler__cell_row_counts"] = np.asarray(sampler_state["cell_row_counts"], dtype=np.int64)
    np.savez_compressed(path, **arrays)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
