#!/usr/bin/env python3
"""Train a response-consistent multi-head inverse baseline with NumPy.

The model maps one [Lp, Ls, Q, |K|] target to several geometry candidates.
A frozen geometry-to-physical-feature proxy scores every head. Best-of-K
response loss, a small all-head response term, optional paired-geometry anchor,
and a smooth diversity repulsion are optimized jointly. The script is an
ablation baseline for one-to-many inverse design; its candidates still require
the production geometry audit, DRC, new EMX solves, and sampled HFSS closure.
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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import train_physical_feature_tandem_inverse as tandem  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "physical_feature_multihead_tandem_summary.json"
    history_path = out_dir / "physical_feature_multihead_tandem_history.csv"
    predictions_path = out_dir / "physical_feature_multihead_tandem_test_candidates.csv"
    weights_path = out_dir / "physical_feature_multihead_tandem_weights.npz"

    rows = tandem._read_rows(training_csv)
    input_columns = [item.strip() for item in args.input_columns.split(",") if item.strip()]
    preference_columns = [
        item.strip() for item in args.preference_columns.split(",") if item.strip()
    ]
    split_reference_columns = [
        item.strip()
        for item in (args.split_reference_columns or args.input_columns).split(",")
        if item.strip()
    ]
    geometry_columns = tandem._resolve_geometry_columns(rows, args.geometry_columns, args.geometry_prefix)
    topology_columns = tandem._topology_feasibility_column_contract(geometry_columns)
    matrix = tandem._build_matrix(rows, input_columns, geometry_columns, split_reference_columns)
    preference_values = _extract_preference_values(rows, matrix, preference_columns)
    preference_manifest_audit = _audit_preference_manifest(
        args,
        training_csv,
        preference_columns,
    )
    preference_qmin_consistency = _preference_qmin_consistency(
        matrix,
        input_columns,
        preference_values,
        float(args.preference_qmin_atol),
        float(args.preference_qmin_rtol),
    )
    preference_enabled = args.head_semantics == "qp_qs_preference"
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "usable_rows_meet_minimum": int(matrix["count"]) >= int(args.min_training_rows),
        "input_contract_is_lp_ls_q_absk": _input_contract_is_lp_ls_q_absk(input_columns),
        "split_reference_contract_is_lp_ls_q_absk": _input_contract_is_lp_ls_q_absk(
            split_reference_columns
        ),
        "geometry_column_count_exact": len(geometry_columns) == int(args.expected_geometry_columns),
        "head_count_at_least_two": int(args.head_count) >= 2,
        "preference_head_count_is_even_when_enabled": not preference_enabled
        or int(args.head_count) % 2 == 0,
        "preference_columns_are_exact_qp_qs_when_enabled": not preference_enabled
        or _preference_columns_are_qp_qs(preference_columns),
        "preference_values_complete_when_enabled": not preference_enabled
        or (
            preference_values.shape == (int(matrix["count"]), 2)
            and bool(preference_values.size)
            and bool(np.all(np.isfinite(preference_values)))
        ),
        "preference_qmin_matches_min_qp_qs_when_enabled": not preference_enabled
        or preference_qmin_consistency.get("overall_status") == "PASS",
        "preference_manifest_proves_same_real_s4p_rows_when_enabled": not preference_enabled
        or preference_manifest_audit.get("overall_status") == "PASS",
        "topology_columns_available_when_enabled": float(args.topology_feasibility_weight) == 0.0
        or topology_columns.get("available") is True,
    }
    if not all(checks.values()):
        status = "WAITING_FOR_COMPLETE_DATA" if not checks["usable_rows_meet_minimum"] else "FAIL"
        summary = _base_summary(
            args,
            training_csv,
            input_columns,
            geometry_columns,
            matrix,
            checks,
        )
        summary.update(
            {
                "overall_status": status,
                "execution_status": status,
                "quality_status": "NOT_RUN",
                "eligible_for_model_success_claim": False,
                "decision": "WAIT_FOR_FORMAL_REAL_EMX_TABLE"
                if status.startswith("WAITING")
                else "FIX_MULTIHEAD_INPUT_CONTRACT",
                "preference_manifest_audit": preference_manifest_audit,
                "preference_qmin_consistency": preference_qmin_consistency,
                "history_csv": "",
                "test_candidates_csv": "",
                "weights_npz": "",
            }
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        history_path.write_text("", encoding="utf-8")
        predictions_path.write_text("", encoding="utf-8")
        print(f"overall_status={status}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit or status.startswith("WAITING") else 2

    split, split_audit = tandem._split_indices(matrix, args)
    isolation = tandem._evaluation_isolation_contract(matrix, split)
    data = tandem._normalize(matrix, split, float(args.normalization_floor))
    data["split_audit"] = split_audit
    data["topology_feasibility_contract"] = tandem._configure_topology_feasibility(
        data["normalization"], geometry_columns, topology_columns, args
    )
    data["response_loss_contract"] = tandem._configure_response_loss(
        data,
        input_columns,
        split_reference_columns,
        split_audit,
        args,
    )
    sampler_state, sampler_contract = tandem._configure_training_batch_sampler(
        data,
        split_reference_columns,
        split_audit,
        args,
    )
    data["training_batch_sampler_state"] = sampler_state
    data["training_batch_sampler_contract"] = sampler_contract
    data["optimizer_budget_contract"] = tandem._configure_optimizer_budget_contract(
        sampler_contract,
        args,
    )
    preference_head_contract, preference_head_assignments = _configure_preference_heads(
        preference_values,
        split,
        matrix,
        preference_columns,
        int(args.head_count),
        preference_enabled,
        preference_manifest_audit,
        preference_qmin_consistency,
    )
    data["preference_head_contract"] = preference_head_contract
    data["preference_head_assignments"] = preference_head_assignments

    rng_forward = np.random.default_rng(int(args.seed))
    forward_hidden_widths = tandem._resolve_hidden_widths(
        None,
        depth=int(args.forward_depth),
        width=int(args.forward_width),
    )
    forward_weights, forward_biases = tandem._init_mlp(
        len(geometry_columns),
        len(input_columns),
        forward_hidden_widths,
        rng_forward,
    )
    forward_history, forward_best = tandem._train_forward(
        data, forward_weights, forward_biases, args
    )
    forward_weights = forward_best["weights"]
    forward_biases = forward_best["biases"]

    rng_inverse = np.random.default_rng(int(args.seed) + 1)
    inverse_hidden_widths = tandem._resolve_hidden_widths(
        None,
        depth=int(args.inverse_depth),
        width=int(args.inverse_width),
    )
    inverse_weights, inverse_biases = tandem._init_mlp(
        len(input_columns) + int(args.head_count),
        len(geometry_columns),
        inverse_hidden_widths,
        rng_inverse,
    )
    inverse_history, inverse_best = _train_multihead_inverse(
        data,
        inverse_weights,
        inverse_biases,
        forward_weights,
        forward_biases,
        args,
    )
    inverse_weights = inverse_best["weights"]
    inverse_biases = inverse_best["biases"]
    tandem._record_realized_training_sampler_budget(
        data["training_batch_sampler_contract"],
        forward_history,
        inverse_history,
    )
    tandem._record_optimizer_budget_realization(
        data["optimizer_budget_contract"],
        data["training_batch_sampler_contract"]["realized_training_budget"],
    )

    metrics, candidate_rows = _evaluate(
        data,
        input_columns,
        geometry_columns,
        inverse_weights,
        inverse_biases,
        forward_weights,
        forward_biases,
        args,
    )
    history_rows = [{"stage": "forward_proxy", **row} for row in forward_history] + [
        {"stage": "multihead_inverse", **row} for row in inverse_history
    ]
    _write_csv(history_path, history_rows)
    _write_csv(predictions_path, candidate_rows)
    _save_weights(
        weights_path,
        forward_weights,
        forward_biases,
        inverse_weights,
        inverse_biases,
        data,
        args,
    )

    formal_ood_scope = (
        split_audit.get("split_mode") == "physical_cell_grouped"
        and split_audit.get("physical_cell_range_source") == "explicit"
        and int(split_audit.get("physical_cell_overlap_count") or 0) == 0
        and isolation.get("overall_status") == "PASS"
    )
    summary = _base_summary(
        args,
        training_csv,
        input_columns,
        geometry_columns,
        matrix,
        checks,
    )
    summary.update(
        {
            "overall_status": "COMPLETE_REVIEW_REQUIRED"
            if isolation.get("overall_status") == "PASS"
            else "FAIL",
            "execution_status": "PASS",
            "quality_status": "REVIEW_REQUIRED_THRESHOLDS_NOT_PREREGISTERED",
            "eligible_for_checkpoint_model_acceptance": False,
            "eligible_for_model_success_claim": False,
            "decision": "COMPARE_MULTIHEAD_WITH_SINGLE_HEAD_ON_FIXED_OOD_AND_REAL_EMX",
            "method": {
                "family": (
                    "qp_qs_preference_semantic_multihead_tandem_best_of_k"
                    if preference_enabled
                    else "head_conditioned_multihead_tandem_best_of_k"
                ),
                "head_count": int(args.head_count),
                "forward_proxy": "geometry_to_Lp_Ls_Q_absK_frozen_before_inverse_training",
                "inverse_input": (
                    "Lp_Ls_Q_absK_plus_train_only_Qp_Qs_semantic_head_identity"
                    if preference_enabled
                    else "Lp_Ls_Q_absK_plus_one_hot_head_identity"
                ),
                "winner_selection": "minimum_frozen_forward_feature_balanced_response_error",
                "geometry_anchor_assignment": (
                    "same_real_S4P_Qp_Qs_semantic_head"
                    if preference_enabled
                    else "minimum_frozen_forward_response_error_head"
                ),
                "loss": "best_of_k_response_plus_all_head_response_plus_semantically_assigned_or_winner_paired_anchor_plus_smooth_diversity_repulsion_plus_topology_penalty",
                "paper_alignment": [
                    "Yuan et al., Optics and Laser Technology 176 (2024) 110997",
                    "Zhou et al., IEEE TMTT 73(11) (2025) 8690-8708",
                ],
            },
            "preference_manifest_audit": preference_manifest_audit,
            "preference_qmin_consistency": preference_qmin_consistency,
            "preference_head_contract": preference_head_contract,
            "training_batch_sampler_contract": data["training_batch_sampler_contract"],
            "optimizer_budget_contract": data["optimizer_budget_contract"],
            "evaluation_scope": {
                "formal_explicit_physical_cell_ood": formal_ood_scope,
                "test_set_frozen_until_post_training_evaluation": True,
                "boundary": "The fixed OOD test result is still a frozen-proxy ablation, not real EM validation.",
            },
            "evaluation_isolation": isolation,
            "split_audit": split_audit,
            "forward_proxy_best_epoch": int(forward_best["epoch"]),
            "multihead_inverse_best_epoch": int(inverse_best["epoch"]),
            "metrics": metrics,
            "history_csv": str(history_path),
            "history_csv_sha256": _sha256_file(history_path),
            "test_candidates_csv": str(predictions_path),
            "test_candidates_csv_sha256": _sha256_file(predictions_path),
            "weights_npz": str(weights_path),
            "weights_npz_sha256": _sha256_file(weights_path),
            "scientific_boundary": (
                "Multiple proxy-consistent candidates are not multiple valid transformers. Every selected geometry "
                "must pass the production geometry audit and foundry DRC, then obtain a new real EMX S4P; sampled "
                "candidates additionally require HFSS correlation. No 55-row smoke result may support an accuracy, "
                "sample-sufficiency, or model-promotion claim."
            ),
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={summary['overall_status']}")
    print(f"best_of_k_test_range_normalized_rmse={metrics['best_of_k']['response_range_normalized_rmse']}")
    print(f"mean_unique_candidate_fraction={metrics['diversity']['mean_unique_candidate_fraction']}")
    print(f"summary={summary_path}")
    return 0 if summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", default=tandem.DEFAULT_INPUT_COLUMNS)
    parser.add_argument("--split-reference-columns")
    parser.add_argument("--geometry-columns")
    parser.add_argument("--geometry-prefix", default="geom__")
    parser.add_argument("--expected-geometry-columns", type=int, default=10)
    parser.add_argument("--min-training-rows", type=int, default=100_000)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.10)
    parser.add_argument("--split-mode", choices=("random", "physical_cell_grouped"), default="physical_cell_grouped")
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower", default="0.5,0.5,5,0")
    parser.add_argument("--physical-cell-upper", default="3,3,25,0.8")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--split-seed", type=int, default=20260711)
    parser.add_argument("--head-count", type=int, default=4)
    parser.add_argument(
        "--head-semantics",
        choices=("arbitrary", "qp_qs_preference"),
        default="arbitrary",
    )
    parser.add_argument("--preference-columns", default="aux__qp_center,aux__qs_center")
    parser.add_argument("--training-manifest-json")
    parser.add_argument("--preference-qmin-atol", type=float, default=1.0e-6)
    parser.add_argument("--preference-qmin-rtol", type=float, default=1.0e-6)
    parser.add_argument("--forward-depth", type=int, default=3)
    parser.add_argument("--forward-width", type=int, default=256)
    parser.add_argument("--inverse-depth", type=int, default=3)
    parser.add_argument("--inverse-width", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument(
        "--training-batch-sampler",
        choices=("row_uniform", "joint_cell_balanced"),
        default="row_uniform",
    )
    parser.add_argument(
        "--exact-update-batch-mode",
        choices=("legacy_epoch_partial", "continuous_permutation_full_batch"),
        default="legacy_epoch_partial",
    )
    parser.add_argument("--forward-epochs", type=int, default=160)
    parser.add_argument("--inverse-epochs", type=int, default=180)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--forward-max-optimizer-updates", type=int, default=0)
    parser.add_argument("--inverse-max-optimizer-updates", type=int, default=0)
    parser.add_argument("--validation-every-optimizer-updates", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--winner-response-weight", type=float, default=1.0)
    parser.add_argument("--all-head-response-weight", type=float, default=0.10)
    parser.add_argument("--geometry-anchor-weight", type=float, default=0.01)
    parser.add_argument("--diversity-weight", type=float, default=0.01)
    parser.add_argument("--diversity-scale", type=float, default=0.50)
    parser.add_argument("--topology-feasibility-weight", type=float, default=0.01)
    parser.add_argument("--unique-distance-threshold", type=float, default=0.05)
    parser.add_argument("--response-loss-scaling", choices=("standardized", "declared_range"), default="declared_range")
    parser.add_argument("--response-loss-family", choices=("mse",), default="mse")
    parser.add_argument("--balanced-mse-temperature", type=float)
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument("--max-prediction-rows", type=int, default=2000)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    positive = {
        "expected_geometry_columns": args.expected_geometry_columns,
        "min_training_rows": args.min_training_rows,
        "head_count": args.head_count,
        "batch_size": args.batch_size,
        "forward_epochs": args.forward_epochs,
        "inverse_epochs": args.inverse_epochs,
        "patience": args.patience,
    }
    if any(int(value) < 1 for value in positive.values()):
        parser.error("count, epoch, batch, and patience arguments must be positive")
    if int(args.head_count) < 2:
        parser.error("--head-count must be at least 2")
    if args.head_semantics == "qp_qs_preference" and int(args.head_count) % 2:
        parser.error("--head-count must be even for qp_qs_preference semantics")
    update_budgets = (
        int(args.forward_max_optimizer_updates),
        int(args.inverse_max_optimizer_updates),
    )
    if any(value < 0 for value in update_budgets):
        parser.error("optimizer-update budgets must be nonnegative")
    exact_update_mode = any(value > 0 for value in update_budgets)
    if exact_update_mode and not all(value > 0 for value in update_budgets):
        parser.error("forward and inverse exact optimizer-update budgets must be enabled together")
    if int(args.validation_every_optimizer_updates) < 0:
        parser.error("--validation-every-optimizer-updates must be nonnegative")
    if exact_update_mode and int(args.validation_every_optimizer_updates) < 1:
        parser.error("exact optimizer-update budgets require a positive validation update interval")
    if args.exact_update_batch_mode == "continuous_permutation_full_batch":
        if not exact_update_mode:
            parser.error(
                "--exact-update-batch-mode continuous_permutation_full_batch requires exact optimizer-update budgets"
            )
        if args.training_batch_sampler != "row_uniform":
            parser.error(
                "continuous-permutation full batches require --training-batch-sampler row_uniform"
            )
    # The shared tandem budget/sampler implementation also records the PICC
    # scheduling namespace. Multi-head training uses fixed response weights,
    # so bind a zero-length linear schedule for an accurate compatibility
    # contract instead of inheriting private or implicit parser state.
    args.response_weight_schedule = "linear_ramp"
    args.response_schedule_domain = "optimizer_update" if exact_update_mode else "epoch"
    args.response_warmup_fraction = 0.0
    args.response_ramp_fraction = 0.0
    args.response_warmup_optimizer_updates = None
    args.response_ramp_optimizer_updates = None
    for name in (
        "learning_rate",
        "winner_response_weight",
        "diversity_scale",
        "unique_distance_threshold",
        "preference_qmin_atol",
        "preference_qmin_rtol",
    ):
        if not math.isfinite(float(getattr(args, name))) or float(getattr(args, name)) <= 0.0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    for name in (
        "weight_decay",
        "all_head_response_weight",
        "geometry_anchor_weight",
        "diversity_weight",
        "topology_feasibility_weight",
    ):
        if not math.isfinite(float(getattr(args, name))) or float(getattr(args, name)) < 0.0:
            parser.error(f"--{name.replace('_', '-')} must be finite and nonnegative")
    return args


def _extract_preference_values(
    rows: list[dict[str, str]],
    matrix: dict[str, Any],
    preference_columns: list[str],
) -> np.ndarray:
    if len(preference_columns) != 2:
        return np.empty((int(matrix.get("count") or 0), 0), dtype=float)
    values: list[list[float]] = []
    for source_index in matrix.get("source_indices") or []:
        row = rows[int(source_index)]
        parsed = [tandem._as_float(row.get(column)) for column in preference_columns]
        values.append(
            [float(item) if item is not None else float("nan") for item in parsed]
        )
    return np.asarray(values, dtype=float)


def _preference_columns_are_qp_qs(columns: list[str]) -> bool:
    names = [column.lower().removeprefix("aux__").removeprefix("input__") for column in columns]
    return (
        len(names) == 2
        and names[0].startswith("qp_")
        and names[1].startswith("qs_")
    )


def _preference_qmin_consistency(
    matrix: dict[str, Any],
    input_columns: list[str],
    preference_values: np.ndarray,
    atol: float,
    rtol: float,
) -> dict[str, Any]:
    q_indices = [
        index
        for index, column in enumerate(input_columns)
        if tandem._physical_feature_semantic(column) == "q"
    ]
    values = np.asarray(preference_values, dtype=float)
    checks = {
        "exactly_one_qmin_input": len(q_indices) == 1,
        "preference_shape_is_two_columns": values.ndim == 2 and values.shape[1:] == (2,),
        "all_values_finite": bool(values.size) and bool(np.all(np.isfinite(values))),
    }
    if not all(checks.values()):
        return {
            "overall_status": "FAIL",
            "checks": checks,
            "matching_row_fraction": 0.0,
            "max_absolute_difference": None,
            "atol": float(atol),
            "rtol": float(rtol),
        }
    qmin = np.asarray(matrix["x"], dtype=float)[:, q_indices[0]]
    derived = np.min(values, axis=1)
    matching = np.isclose(derived, qmin, atol=float(atol), rtol=float(rtol))
    checks["every_row_matches_qmin"] = bool(np.all(matching))
    return {
        "overall_status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "matching_row_fraction": float(np.mean(matching)),
        "max_absolute_difference": float(np.max(np.abs(derived - qmin))),
        "atol": float(atol),
        "rtol": float(rtol),
        "definition": "input Qmin must equal min(auxiliary Qp, auxiliary Qs) on the same real-S4P row",
    }


def _audit_preference_manifest(
    args: argparse.Namespace,
    training_csv: Path,
    preference_columns: list[str],
) -> dict[str, Any]:
    if args.head_semantics != "qp_qs_preference":
        return {
            "overall_status": "NOT_REQUIRED",
            "enabled": False,
            "automatic_model_or_production_promotion_authorized": False,
        }
    manifest_path = (
        Path(args.training_manifest_json).expanduser().resolve()
        if args.training_manifest_json
        else None
    )
    payload: dict[str, Any] = {}
    load_error = ""
    if manifest_path is not None and manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
            else:
                load_error = "manifest root is not a JSON object"
        except (OSError, json.JSONDecodeError) as exc:
            load_error = str(exc)
    else:
        load_error = "manifest path is missing or not a file"
    auxiliary_contract = payload.get("auxiliary_output_contract") or {}
    table_source = payload.get("training_table_source") or {}
    checks = {
        "manifest_exists_and_is_object": bool(payload) and not load_error,
        "manifest_overall_status_pass": payload.get("overall_status") == "PASS",
        "training_csv_sha_matches_manifest": training_csv.is_file()
        and str(table_source.get("sha256") or "").lower() == _sha256_file(training_csv),
        "preference_columns_are_declared_auxiliary_outputs": list(
            payload.get("auxiliary_output_columns") or []
        )
        == list(preference_columns),
        "same_real_simulator_row_required": auxiliary_contract.get(
            "same_real_simulator_row_required"
        )
        is True,
        "auxiliary_values_not_declared_inverse_inputs": auxiliary_contract.get(
            "included_in_inverse_model_inputs"
        )
        is False,
        "predicted_or_surrogate_values_forbidden": auxiliary_contract.get(
            "predicted_or_surrogate_values_allowed"
        )
        is False,
    }
    return {
        "overall_status": "PASS" if all(checks.values()) else "FAIL",
        "enabled": True,
        "manifest_json": str(manifest_path) if manifest_path is not None else "",
        "manifest_json_sha256": _sha256_file(manifest_path)
        if manifest_path is not None and manifest_path.is_file()
        else "",
        "training_csv_sha256": _sha256_file(training_csv) if training_csv.is_file() else "",
        "preference_columns": list(preference_columns),
        "checks": checks,
        "load_error": load_error,
        "automatic_model_or_production_promotion_authorized": False,
    }


def _configure_preference_heads(
    preference_values: np.ndarray,
    split: dict[str, np.ndarray],
    matrix: dict[str, Any],
    preference_columns: list[str],
    head_count: int,
    enabled: bool,
    manifest_audit: dict[str, Any],
    qmin_consistency: dict[str, Any],
) -> tuple[dict[str, Any], np.ndarray | None]:
    if not enabled:
        return (
            {
                "schema": "arbitrary_multihead_identity_v1",
                "enabled": False,
                "head_count": int(head_count),
                "anchor_assignment": "minimum_frozen_forward_response_error_head",
                "automatic_model_or_production_promotion_authorized": False,
            },
            None,
        )
    values = np.asarray(preference_values, dtype=float)
    train = np.asarray(split["train"], dtype=int)
    if values.shape != (int(matrix["count"]), 2) or not len(train):
        raise ValueError("Qp/Qs preference head construction requires complete rows and train split")
    branches_per_orientation = int(head_count) // 2
    qp = values[:, 0]
    qs = values[:, 1]
    orientation = np.where(qp <= qs, 0, 1).astype(int)
    delta = np.abs(qp - qs)
    assignments = np.zeros(len(values), dtype=int)
    semantics: list[dict[str, Any]] = []
    edge_records: dict[int, np.ndarray] = {}
    global_train_delta = delta[train]
    for orientation_index, orientation_name in (
        (0, "qp_is_qmin_qs_is_higher"),
        (1, "qs_is_qmin_qp_is_higher"),
    ):
        orientation_train = train[orientation[train] == orientation_index]
        source_delta = delta[orientation_train] if len(orientation_train) else global_train_delta
        edges = np.quantile(
            source_delta,
            np.linspace(0.0, 1.0, branches_per_orientation + 1),
        )
        edges = np.maximum.accumulate(np.asarray(edges, dtype=float))
        edge_records[orientation_index] = edges
        local_assignment = np.searchsorted(edges[1:-1], delta, side="right")
        mask = orientation == orientation_index
        assignments[mask] = orientation_index * branches_per_orientation + local_assignment[mask]
        for branch_index in range(branches_per_orientation):
            head_index = orientation_index * branches_per_orientation + branch_index
            in_bin = orientation_train[
                np.searchsorted(edges[1:-1], delta[orientation_train], side="right")
                == branch_index
            ]
            center = (
                float(np.median(delta[in_bin]))
                if len(in_bin)
                else float((edges[branch_index] + edges[branch_index + 1]) / 2.0)
            )
            semantics.append(
                {
                    "head_index": int(head_index),
                    "orientation": orientation_name,
                    "delta_q_train_bin_lower_inclusive": float(edges[branch_index]),
                    "delta_q_train_bin_upper_inclusive": float(edges[branch_index + 1]),
                    "delta_q_train_representative": center,
                    "train_anchor_row_count": int(len(in_bin)),
                    "candidate_preference_definition": (
                        "Qp=Qmin_target, Qs=Qmin_target+delta_q"
                        if orientation_index == 0
                        else "Qs=Qmin_target, Qp=Qmin_target+delta_q"
                    ),
                }
            )
    train_identity_payload = [
        str(matrix["source_geometry_identities"][int(index)]) for index in train
    ]
    contract = {
        "schema": "real_s4p_qp_qs_semantic_heads_v1",
        "enabled": True,
        "head_count": int(head_count),
        "branches_per_qmin_orientation": int(branches_per_orientation),
        "preference_columns": list(preference_columns),
        "head_semantics": semantics,
        "quantile_edges_fit_on_train_only": True,
        "validation_or_test_preferences_used_to_fit_edges": False,
        "test_preferences_used_for_candidate_selection": False,
        "candidate_selection": "minimum_frozen_forward_Lp_Ls_Qmin_absK_error_only",
        "geometry_anchor_assignment": "same_real_S4P_Qp_Qs_semantic_head",
        "same_real_s4p_manifest_status": manifest_audit.get("overall_status"),
        "qmin_consistency_status": qmin_consistency.get("overall_status"),
        "train_geometry_identity_sequence_sha256": hashlib.sha256(
            json.dumps(train_identity_payload, separators=(",", ":"), ensure_ascii=True).encode(
                "ascii"
            )
        ).hexdigest(),
        "automatic_model_or_production_promotion_authorized": False,
        "scientific_boundary": (
            "Qp/Qs labels assign train/validation geometry anchors to semantic heads. "
            "All candidates are selected by the frozen four-feature forward response; "
            "test Qp/Qs never select a head, model, threshold, or production policy."
        ),
    }
    contract["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    return contract, assignments


def _train_multihead_inverse(
    data: dict[str, Any],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    split = data["split"]
    rng = np.random.default_rng(int(args.seed) + 1)
    state = tandem._init_adam(inverse_weights, inverse_biases)
    best = {
        "loss": math.inf,
        "epoch": 0,
        "optimizer_updates": 0,
        "weights": [item.copy() for item in inverse_weights],
        "biases": [item.copy() for item in inverse_biases],
    }
    stale = 0
    history: list[dict[str, Any]] = []
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

    def record_validation(epoch: int) -> None:
        nonlocal best, stale, validation_event
        validation_event += 1
        validation = _candidate_metrics(
            data["x"][split["validation"]],
            data["y"][split["validation"]],
            data,
            inverse_weights,
            inverse_biases,
            forward_weights,
            forward_biases,
            args,
            split["validation"],
        )
        history.append(
            {
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "real_row_draws": int(row_draws),
                "validation_event": validation_event,
                "optimizer_budget_mode": str(budget["mode"]),
                "training_batch_sampler": str(
                    data["training_batch_sampler_contract"]["family"]
                ),
                "validation_objective": validation["objective"],
                "validation_best_of_k_response_rmse": math.sqrt(
                    validation["winner_response_mse"]
                ),
                "validation_all_head_response_rmse": math.sqrt(
                    validation["all_head_response_mse"]
                ),
                "validation_paired_geometry_anchor_rmse": math.sqrt(
                    validation["geometry_anchor_mse"]
                ),
                "validation_diversity_repulsion": validation["diversity_repulsion"],
                "validation_mean_pairwise_normalized_distance": validation[
                    "mean_pairwise_normalized_distance"
                ],
                "validation_head_utilization_entropy": validation[
                    "head_utilization_entropy"
                ],
            }
        )
        if float(validation["objective"]) + 1.0e-12 < float(best["loss"]):
            best = {
                "loss": float(validation["objective"]),
                "epoch": epoch,
                "optimizer_updates": int(state["step"]),
                "weights": [item.copy() for item in inverse_weights],
                "biases": [item.copy() for item in inverse_biases],
            }
            stale = 0
        else:
            stale += 1

    for epoch in range(1, max_epochs + 1):
        for batch in tandem._training_batches(data, int(args.batch_size), rng):
            if exact_update_mode and int(state["step"]) >= target_updates:
                break
            candidate_input = _head_conditioned_inputs(data["x"][batch], int(args.head_count))
            raw, inverse_activations, inverse_preactivations = tandem._forward_with_cache(
                candidate_input, inverse_weights, inverse_biases
            )
            lower = np.asarray(data["normalization"]["geometry_lower"], dtype=float)
            upper = np.asarray(data["normalization"]["geometry_upper"], dtype=float)
            geometry_flat, projection_derivative = tandem._project_geometry(raw, lower, upper)
            response_flat, forward_activations, forward_preactivations = tandem._forward_with_cache(
                geometry_flat, forward_weights, forward_biases
            )
            geometry = geometry_flat.reshape(len(batch), int(args.head_count), -1)
            response = response_flat.reshape(len(batch), int(args.head_count), -1)
            loss, grad_geometry, _diagnostics = _objective_and_gradient(
                geometry,
                response,
                data["x"][batch],
                data["y"][batch],
                data,
                forward_weights,
                forward_activations,
                forward_preactivations,
                args,
                _preference_anchor_heads(data, batch),
            )
            if not math.isfinite(loss):
                raise ValueError("multi-head training objective became non-finite")
            grad_raw = grad_geometry.reshape(geometry_flat.shape) * projection_derivative
            grad_weights, grad_biases, _ = tandem._backward(
                grad_raw,
                inverse_weights,
                inverse_activations,
                inverse_preactivations,
                float(args.weight_decay),
            )
            tandem._adam_step(
                inverse_weights,
                inverse_biases,
                grad_weights,
                grad_biases,
                state,
                float(args.learning_rate),
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
        raise RuntimeError("multi-head inverse training did not complete its exact optimizer-update budget")
    return history, best


def _objective_and_gradient(
    geometry: np.ndarray,
    response: np.ndarray,
    target_response: np.ndarray,
    paired_geometry: np.ndarray,
    data: dict[str, Any],
    forward_weights: list[np.ndarray],
    forward_activations: list[np.ndarray],
    forward_preactivations: list[np.ndarray],
    args: argparse.Namespace,
    anchor_head_indices: np.ndarray | None = None,
) -> tuple[float, np.ndarray, dict[str, Any]]:
    batch_size, head_count, geometry_dim = geometry.shape
    feature_dim = response.shape[2]
    dimension_weights = np.asarray(
        data["normalization"]["response_loss_dimension_weights"], dtype=float
    )
    error = response - target_response[:, None, :]
    candidate_mse = np.mean(error**2 * dimension_weights[None, None, :], axis=2)
    winners = np.argmin(candidate_mse, axis=1)
    winner_mse = float(np.mean(candidate_mse[np.arange(batch_size), winners]))
    all_head_mse = float(np.mean(candidate_mse))

    grad_response = (
        float(args.all_head_response_weight)
        * 2.0
        * error
        * dimension_weights[None, None, :]
        / float(batch_size * head_count * feature_dim)
    )
    for row_index, head_index in enumerate(winners):
        grad_response[row_index, head_index] += (
            float(args.winner_response_weight)
            * 2.0
            * error[row_index, head_index]
            * dimension_weights
            / float(batch_size * feature_dim)
        )
    _, _, grad_geometry_response = tandem._backward(
        grad_response.reshape(batch_size * head_count, feature_dim),
        forward_weights,
        forward_activations,
        forward_preactivations,
        0.0,
    )
    grad_geometry = grad_geometry_response.reshape(batch_size, head_count, geometry_dim)

    anchor_heads = (
        np.asarray(winners, dtype=int)
        if anchor_head_indices is None
        else np.asarray(anchor_head_indices, dtype=int)
    )
    if anchor_heads.shape != (batch_size,) or np.any(anchor_heads < 0) or np.any(
        anchor_heads >= head_count
    ):
        raise ValueError("geometry anchor head indices must select one valid head per row")
    anchor_geometry = geometry[np.arange(batch_size), anchor_heads]
    anchor_error = anchor_geometry - paired_geometry
    anchor_mse = float(np.mean(anchor_error**2))
    for row_index, head_index in enumerate(anchor_heads):
        grad_geometry[row_index, head_index] += (
            float(args.geometry_anchor_weight)
            * 2.0
            * anchor_error[row_index]
            / float(batch_size * geometry_dim)
        )

    diversity_repulsion, diversity_gradient, mean_distance = _diversity_repulsion_and_gradient(
        geometry, float(args.diversity_scale)
    )
    grad_geometry += float(args.diversity_weight) * diversity_gradient
    topology_penalty, topology_gradient, topology_diagnostics = (
        tandem._topology_feasibility_penalty_and_gradient(
            geometry.reshape(batch_size * head_count, geometry_dim),
            data["normalization"],
            data.get("topology_feasibility_contract") or {},
        )
    )
    grad_geometry += float(args.topology_feasibility_weight) * topology_gradient.reshape(
        batch_size, head_count, geometry_dim
    )
    objective = (
        float(args.winner_response_weight) * winner_mse
        + float(args.all_head_response_weight) * all_head_mse
        + float(args.geometry_anchor_weight) * anchor_mse
        + float(args.diversity_weight) * diversity_repulsion
        + float(args.topology_feasibility_weight) * topology_penalty
    )
    return objective, grad_geometry, {
        "winner_response_mse": winner_mse,
        "all_head_response_mse": all_head_mse,
        "geometry_anchor_mse": anchor_mse,
        "diversity_repulsion": diversity_repulsion,
        "mean_pairwise_normalized_distance": mean_distance,
        "topology_penalty": topology_penalty,
        "topology": topology_diagnostics,
        "winner_indices": winners,
        "anchor_head_indices": anchor_heads,
    }


def _diversity_repulsion_and_gradient(
    geometry: np.ndarray, scale: float
) -> tuple[float, np.ndarray, float]:
    values = np.asarray(geometry, dtype=float)
    batch_size, head_count, geometry_dim = values.shape
    pairs = head_count * (head_count - 1) // 2
    if batch_size < 1 or pairs < 1 or scale <= 0.0:
        raise ValueError("diversity loss requires non-empty rows, at least two heads, and positive scale")
    gradient = np.zeros_like(values)
    penalties: list[float] = []
    distances: list[float] = []
    denominator = float(batch_size * pairs)
    for left in range(head_count):
        for right in range(left + 1, head_count):
            delta = values[:, left] - values[:, right]
            mean_square = np.mean(delta**2, axis=1)
            penalty = np.exp(-mean_square / (2.0 * scale**2))
            pair_gradient = (
                -penalty[:, None]
                * delta
                / float(scale**2 * geometry_dim)
                / denominator
            )
            gradient[:, left] += pair_gradient
            gradient[:, right] -= pair_gradient
            penalties.extend(float(item) for item in penalty)
            distances.extend(float(item) for item in np.sqrt(mean_square))
    return float(np.mean(penalties)), gradient, float(np.mean(distances))


def _candidate_metrics(
    target_response: np.ndarray,
    paired_geometry: np.ndarray,
    data: dict[str, Any],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    args: argparse.Namespace,
    row_indices: np.ndarray,
) -> dict[str, Any]:
    geometry, response = _predict_candidates(
        target_response,
        data,
        inverse_weights,
        inverse_biases,
        forward_weights,
        forward_biases,
        int(args.head_count),
    )
    dimension_weights = np.asarray(
        data["normalization"]["response_loss_dimension_weights"], dtype=float
    )
    error = response - target_response[:, None, :]
    candidate_mse = np.mean(error**2 * dimension_weights[None, None, :], axis=2)
    winners = np.argmin(candidate_mse, axis=1)
    preference_anchor_heads = _preference_anchor_heads(data, row_indices)
    anchor_heads = (
        winners
        if preference_anchor_heads is None
        else np.asarray(preference_anchor_heads, dtype=int)
    )
    anchor_geometry = geometry[np.arange(len(geometry)), anchor_heads]
    winner_mse = float(np.mean(candidate_mse[np.arange(len(geometry)), winners]))
    all_head_mse = float(np.mean(candidate_mse))
    anchor_mse = float(np.mean((anchor_geometry - paired_geometry) ** 2))
    diversity_repulsion, _gradient, mean_distance = _diversity_repulsion_and_gradient(
        geometry, float(args.diversity_scale)
    )
    topology_penalty, _topology_gradient, topology = tandem._topology_feasibility_penalty_and_gradient(
        geometry.reshape(-1, geometry.shape[-1]),
        data["normalization"],
        data.get("topology_feasibility_contract") or {},
    )
    counts = np.bincount(winners, minlength=int(args.head_count))
    probabilities = counts / max(1, int(np.sum(counts)))
    positive = probabilities[probabilities > 0.0]
    entropy = float(-np.sum(positive * np.log(positive)) / math.log(int(args.head_count)))
    objective = (
        float(args.winner_response_weight) * winner_mse
        + float(args.all_head_response_weight) * all_head_mse
        + float(args.geometry_anchor_weight) * anchor_mse
        + float(args.diversity_weight) * diversity_repulsion
        + float(args.topology_feasibility_weight) * topology_penalty
    )
    return {
        "objective": objective,
        "winner_response_mse": winner_mse,
        "all_head_response_mse": all_head_mse,
        "geometry_anchor_mse": anchor_mse,
        "diversity_repulsion": diversity_repulsion,
        "mean_pairwise_normalized_distance": mean_distance,
        "head_utilization_counts": counts.astype(int).tolist(),
        "head_utilization_entropy": entropy,
        "topology_penalty": topology_penalty,
        "topology": topology,
        "winner_indices": winners,
        "anchor_head_indices": anchor_heads,
        "geometry": geometry,
        "response": response,
        "candidate_mse": candidate_mse,
    }


def _evaluate(
    data: dict[str, Any],
    input_columns: list[str],
    geometry_columns: list[str],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    test = np.asarray(data["split"]["test"], dtype=int)
    evaluated = _candidate_metrics(
        data["x"][test],
        data["y"][test],
        data,
        inverse_weights,
        inverse_biases,
        forward_weights,
        forward_biases,
        args,
        test,
    )
    geometry = np.asarray(evaluated.pop("geometry"), dtype=float)
    response = np.asarray(evaluated.pop("response"), dtype=float)
    candidate_mse = np.asarray(evaluated.pop("candidate_mse"), dtype=float)
    winners = np.asarray(evaluated.pop("winner_indices"), dtype=int)
    anchor_heads = np.asarray(evaluated.pop("anchor_head_indices"), dtype=int)
    norm = data["normalization"]
    response_physical = response * norm["x_scale"][None, None, :] + norm["x_mean"][None, None, :]
    target_physical = data["x"][test] * norm["x_scale"][None, :] + norm["x_mean"][None, :]
    geometry_physical = geometry * norm["y_scale"][None, None, :] + norm["y_mean"][None, None, :]
    paired_geometry_physical = data["y"][test] * norm["y_scale"][None, :] + norm["y_mean"][None, :]
    lower = np.asarray((data.get("split_audit") or {}).get("physical_cell_lower") or [], dtype=float)
    upper = np.asarray((data.get("split_audit") or {}).get("physical_cell_upper") or [], dtype=float)
    if lower.shape != (len(input_columns),) or upper.shape != lower.shape:
        raise ValueError("multi-head formal evaluation requires explicit physical feature bounds")
    spans = upper - lower
    best_response = response_physical[np.arange(len(test)), winners]
    best_range_error = (best_response - target_physical) / spans[None, :]
    head0_range_error = (response_physical[:, 0] - target_physical) / spans[None, :]
    all_range_error = (response_physical - target_physical[:, None, :]) / spans[None, None, :]
    unique_counts = [
        _greedy_unique_count(row, float(args.unique_distance_threshold)) for row in geometry
    ]
    per_feature_mae = np.mean(np.abs(best_response - target_physical), axis=0)
    metrics = {
        "test_row_count": int(len(test)),
        "candidate_count_per_target": int(args.head_count),
        "best_of_k": {
            "response_range_normalized_rmse": float(np.sqrt(np.mean(best_range_error**2))),
            "response_range_normalized_mae": float(np.mean(np.abs(best_range_error))),
            "per_feature_physical_mae": {
                column: float(per_feature_mae[index]) for index, column in enumerate(input_columns)
            },
        },
        "head0_internal_diagnostic": {
            "response_range_normalized_rmse": float(np.sqrt(np.mean(head0_range_error**2))),
            "boundary": "Head 0 is not a separately trained single-head baseline and cannot support promotion.",
        },
        "all_candidates": {
            "response_range_normalized_rmse": float(np.sqrt(np.mean(all_range_error**2))),
        },
        "diversity": {
            "mean_pairwise_normalized_distance": float(evaluated["mean_pairwise_normalized_distance"]),
            "minimum_unique_distance_threshold": float(args.unique_distance_threshold),
            "mean_unique_candidate_count": float(np.mean(unique_counts)),
            "mean_unique_candidate_fraction": float(np.mean(unique_counts) / int(args.head_count)),
            "all_targets_have_multiple_unique_candidates": all(count >= 2 for count in unique_counts),
            "head_utilization_counts": evaluated["head_utilization_counts"],
            "head_utilization_entropy": evaluated["head_utilization_entropy"],
        },
        "topology_feasibility": evaluated["topology"],
        "paired_geometry_diagnostic": {
            "best_response_head_physical_rmse": float(
                np.sqrt(
                    np.mean(
                        (
                            geometry_physical[np.arange(len(test)), winners]
                            - paired_geometry_physical
                        )
                        ** 2
                    )
                )
            ),
            "boundary": "Paired geometry is non-unique and is reported only as a diagnostic.",
            "anchor_head_assignment": (
                "same_real_S4P_Qp_Qs_semantic_head"
                if (data.get("preference_head_contract") or {}).get("enabled") is True
                else "minimum_frozen_forward_response_error_head"
            ),
            "semantic_anchor_head_counts": np.bincount(
                anchor_heads, minlength=int(args.head_count)
            )
            .astype(int)
            .tolist(),
        },
        "selection": "best candidate chosen only by frozen-forward feature-balanced response error",
    }

    candidate_rows: list[dict[str, Any]] = []
    head_semantics = {
        int(item["head_index"]): item
        for item in (data.get("preference_head_contract") or {}).get("head_semantics", [])
    }
    row_limit = min(len(test), max(0, int(args.max_prediction_rows)))
    for local_index in range(row_limit):
        matrix_index = int(test[local_index])
        for head_index in range(int(args.head_count)):
            row: dict[str, Any] = {
                "test_index": local_index,
                "matrix_index": matrix_index,
                "source_row_index": int(data["source_indices"][matrix_index]),
                "source_evaluation": str(data["source_evaluations"][matrix_index]),
                "source_geometry_identity_sha256": str(data["source_geometry_identities"][matrix_index]),
                "head_index": head_index,
                "selected_best_of_k": head_index == int(winners[local_index]),
                "candidate_feature_balanced_normalized_mse": float(candidate_mse[local_index, head_index]),
                "head_semantics": (
                    "qp_qs_preference" if head_index in head_semantics else "arbitrary"
                ),
            }
            if head_index in head_semantics:
                semantic = head_semantics[head_index]
                row.update(
                    {
                        "head_preference_orientation": semantic["orientation"],
                        "head_preference_delta_q_train_bin_lower": semantic[
                            "delta_q_train_bin_lower_inclusive"
                        ],
                        "head_preference_delta_q_train_bin_upper": semantic[
                            "delta_q_train_bin_upper_inclusive"
                        ],
                        "head_preference_delta_q_train_representative": semantic[
                            "delta_q_train_representative"
                        ],
                    }
                )
            for column_index, column in enumerate(input_columns):
                name = column.removeprefix("input__")
                row[f"target__{name}"] = float(target_physical[local_index, column_index])
                row[f"reconstructed__{name}"] = float(
                    response_physical[local_index, head_index, column_index]
                )
            for column_index, column in enumerate(geometry_columns):
                name = column.removeprefix("geom__")
                row[f"paired_geometry__{name}"] = float(
                    paired_geometry_physical[local_index, column_index]
                )
                row[f"predicted_geometry__{name}"] = float(
                    geometry_physical[local_index, head_index, column_index]
                )
            candidate_rows.append(row)
    return metrics, candidate_rows


def _preference_anchor_heads(
    data: dict[str, Any], row_indices: np.ndarray
) -> np.ndarray | None:
    contract = data.get("preference_head_contract") or {}
    assignments = data.get("preference_head_assignments")
    if contract.get("enabled") is not True or assignments is None:
        return None
    indices = np.asarray(row_indices, dtype=int)
    values = np.asarray(assignments, dtype=int)
    if np.any(indices < 0) or np.any(indices >= len(values)):
        raise ValueError("preference anchor row indices are outside the assignment table")
    return values[indices]


def _predict_candidates(
    target_response: np.ndarray,
    data: dict[str, Any],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    head_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    candidate_input = _head_conditioned_inputs(target_response, head_count)
    raw = tandem._predict(candidate_input, inverse_weights, inverse_biases)
    geometry_flat = tandem._project_geometry(
        raw,
        np.asarray(data["normalization"]["geometry_lower"], dtype=float),
        np.asarray(data["normalization"]["geometry_upper"], dtype=float),
    )[0]
    response_flat = tandem._predict(geometry_flat, forward_weights, forward_biases)
    return (
        geometry_flat.reshape(len(target_response), head_count, -1),
        response_flat.reshape(len(target_response), head_count, -1),
    )


def _head_conditioned_inputs(features: np.ndarray, head_count: int) -> np.ndarray:
    values = np.asarray(features, dtype=float)
    repeated = np.repeat(values, head_count, axis=0)
    head_identity = np.tile(np.eye(head_count, dtype=float), (len(values), 1))
    return np.concatenate([repeated, head_identity], axis=1)


def _greedy_unique_count(candidates: np.ndarray, threshold: float) -> int:
    selected: list[np.ndarray] = []
    for candidate in np.asarray(candidates, dtype=float):
        if not selected or all(
            float(np.sqrt(np.mean((candidate - existing) ** 2))) >= threshold
            for existing in selected
        ):
            selected.append(candidate)
    return len(selected)


def _input_contract_is_lp_ls_q_absk(columns: list[str]) -> bool:
    names = [column.lower().removeprefix("input__") for column in columns]
    return (
        len(names) == 4
        and "lp_nh_center" in names[0]
        and "ls_nh_center" in names[1]
        and "q_center" in names[2]
        and "k_abs_center" in names[3]
        and not any("zin" in name for name in names)
    )


def _base_summary(
    args: argparse.Namespace,
    training_csv: Path,
    input_columns: list[str],
    geometry_columns: list[str],
    matrix: dict[str, Any],
    checks: dict[str, bool],
) -> dict[str, Any]:
    contract = {
        "schema": "physical_feature_multihead_tandem_v1",
        "implementation_sha256": _sha256_file(Path(__file__).resolve()),
        "single_head_tandem_implementation_sha256": _sha256_file(
            Path(tandem.__file__).resolve()
        ),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "head_count": int(args.head_count),
        "head_semantics": str(args.head_semantics),
        "preference_columns": [
            item.strip() for item in args.preference_columns.split(",") if item.strip()
        ],
        "training_manifest_json": str(
            Path(args.training_manifest_json).expanduser().resolve()
        )
        if args.training_manifest_json
        else "",
        "training_manifest_json_sha256": _sha256_file(
            Path(args.training_manifest_json).expanduser().resolve()
        )
        if args.training_manifest_json
        and Path(args.training_manifest_json).expanduser().resolve().is_file()
        else "",
        "training_batch_sampler": str(args.training_batch_sampler),
        "optimizer_budget": {
            "forward_max_optimizer_updates": int(args.forward_max_optimizer_updates),
            "inverse_max_optimizer_updates": int(args.inverse_max_optimizer_updates),
            "validation_every_optimizer_updates": int(
                args.validation_every_optimizer_updates
            ),
        },
        "architecture": {
            "forward_depth": int(args.forward_depth),
            "forward_width": int(args.forward_width),
            "inverse_depth": int(args.inverse_depth),
            "inverse_width": int(args.inverse_width),
        },
        "loss_weights": {
            "winner_response": float(args.winner_response_weight),
            "all_head_response": float(args.all_head_response_weight),
            "geometry_anchor": float(args.geometry_anchor_weight),
            "diversity": float(args.diversity_weight),
            "topology": float(args.topology_feasibility_weight),
        },
    }
    contract["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "ascii"
        )
    ).hexdigest()
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "training_csv_sha256": _sha256_file(training_csv) if training_csv.is_file() else "",
        "training_count": int(matrix["count"]),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "checks": checks,
        "model_contract": contract,
        "arguments": vars(args),
    }


def _save_weights(
    path: Path,
    forward_weights: list[np.ndarray],
    forward_biases: list[np.ndarray],
    inverse_weights: list[np.ndarray],
    inverse_biases: list[np.ndarray],
    data: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    payload: dict[str, Any] = {
        "head_count": np.asarray([int(args.head_count)], dtype=int),
        "x_mean": np.asarray(data["normalization"]["x_mean"], dtype=float),
        "x_scale": np.asarray(data["normalization"]["x_scale"], dtype=float),
        "y_mean": np.asarray(data["normalization"]["y_mean"], dtype=float),
        "y_scale": np.asarray(data["normalization"]["y_scale"], dtype=float),
        "geometry_lower": np.asarray(data["normalization"]["geometry_lower"], dtype=float),
        "geometry_upper": np.asarray(data["normalization"]["geometry_upper"], dtype=float),
        "head_semantics": np.asarray([str(args.head_semantics)]),
        "preference_head_contract_json": np.asarray(
            [
                json.dumps(
                    data.get("preference_head_contract") or {},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            ]
        ),
        "preference_head_contract_fingerprint_sha256": np.asarray(
            [
                str(
                    (data.get("preference_head_contract") or {}).get(
                        "fingerprint_sha256"
                    )
                    or ""
                )
            ]
        ),
        "training_sampler_family": np.asarray(
            [str(data["training_batch_sampler_contract"]["family"])]
        ),
        "training_sampler_fingerprint_sha256": np.asarray(
            [str(data["training_batch_sampler_contract"]["fingerprint_sha256"])]
        ),
        "optimizer_budget_mode": np.asarray(
            [str(data["optimizer_budget_contract"]["mode"])]
        ),
        "optimizer_budget_fingerprint_sha256": np.asarray(
            [str(data["optimizer_budget_contract"]["fingerprint_sha256"])]
        ),
    }
    for index, value in enumerate(forward_weights):
        payload[f"forward_weight_{index}"] = value
    for index, value in enumerate(forward_biases):
        payload[f"forward_bias_{index}"] = value
    for index, value in enumerate(inverse_weights):
        payload[f"inverse_weight_{index}"] = value
    for index, value in enumerate(inverse_biases):
        payload[f"inverse_bias_{index}"] = value
    np.savez_compressed(path, **payload)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
