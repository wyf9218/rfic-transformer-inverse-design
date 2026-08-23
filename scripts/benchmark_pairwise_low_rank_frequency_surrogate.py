#!/usr/bin/env python3
"""Compare a pointwise MLP with a pairwise low-rank EM surrogate.

Both arms use the same real S4P rows, physical-cell OOD split, reciprocal
complex-S labels, physical-row batches, optimizer updates, and model seeds.
The candidate represents coordinate-wise scalar embeddings and their pairwise
interactions through a compact latent rank. It is an auditable PLRNet-inspired
ablation, not a claim of reproducing the 2026 preprint implementation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for candidate in (SCRIPT_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import benchmark_frequency_domain_self_transfer as pointwise  # noqa: E402
import benchmark_frequency_sequence_architectures as sequence  # noqa: E402
import train_broadband_sparameter_pca_surrogate as broadband  # noqa: E402
from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices  # noqa: E402


ARMS = ("mlp", "pairwise_low_rank")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    training_manifest = Path(args.training_manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "pairwise_low_rank_frequency_surrogate_summary.json"
    frequency_csv = out_dir / "pairwise_low_rank_frequency_errors.csv"
    seed_csv = out_dir / "pairwise_low_rank_seed_metrics.csv"
    history_csv = out_dir / "pairwise_low_rank_history.csv"
    plot_path = out_dir / "pairwise_low_rank_frequency_errors.png"
    weights_path = out_dir / "pairwise_low_rank_weights.npz"
    report_path = out_dir / "pairwise_low_rank_frequency_surrogate_report.md"

    manifest = _read_json(training_manifest)
    rows = broadband._read_rows(training_csv)
    geometry_columns = broadband._geometry_columns(rows, args.geometry_columns)
    split_columns = broadband._split_columns(args.split_reference_columns)
    selected_rows = broadband._deterministic_spread_sample(rows, int(args.max_rows))
    dataset, rejects = broadband._load_dataset(
        selected_rows,
        geometry_columns,
        split_columns,
        args,
    )
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "training_manifest_exists": training_manifest.is_file(),
        "training_manifest_pass": manifest.get("overall_status") == "PASS",
        "training_manifest_matches_csv": _same_path(manifest.get("training_csv"), training_csv),
        "predictor_count_is_ten": len(geometry_columns) == 10,
        "split_reference_count_is_four": len(split_columns) == 4,
        "usable_rows_meet_minimum": int(dataset.get("count") or 0) >= int(args.min_rows),
        "all_selected_rows_loaded": int(dataset.get("count") or 0) == len(selected_rows),
        "frequency_grid_consistent": bool(dataset.get("frequency_grid_consistent")),
        "touchstone_content_hash_present": bool(dataset.get("touchstone_content_sha256")),
    }
    payload = _base_summary(
        args,
        training_csv,
        training_manifest,
        geometry_columns,
        split_columns,
        dataset,
        rejects,
    )
    if not all(checks.values()):
        status = (
            "WAITING_FOR_COMPLETE_BROADBAND_DATA"
            if not checks["usable_rows_meet_minimum"]
            else "FAIL"
        )
        payload.update(
            {
                "overall_status": status,
                "decision": (
                    "WAIT_FOR_REAL_S4P_ROWS"
                    if status.startswith("WAITING")
                    else "FIX_PAIRWISE_LOW_RANK_BENCHMARK_CONTRACT"
                ),
                "checks": checks,
                "eligible_for_model_success_claim": False,
                "artifacts": {"summary": str(summary_path)},
            }
        )
        summary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"overall_status={status}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    input_quality = broadband._summarize_input_quality(dataset, args)
    checks.update(
        {
            "raw_input_reciprocity_threshold_pass": bool(
                input_quality["reciprocity"]["hard_threshold_pass"]
            ),
            "raw_input_passivity_threshold_pass": bool(
                input_quality["passivity"]["hard_threshold_pass"]
            ),
        }
    )
    try:
        result = _run_benchmark(dataset, args)
    except (ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
        checks["benchmark_runtime_contract"] = False
        payload.update(
            {
                "overall_status": "FAIL",
                "decision": "FIX_PAIRWISE_LOW_RANK_BENCHMARK_INPUT_CONTRACT",
                "checks": checks,
                "raw_input_s4p_quality": input_quality,
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "eligible_for_model_success_claim": False,
                "artifacts": {"summary": str(summary_path)},
            }
        )
        summary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("overall_status=FAIL")
        print(f"failure={type(exc).__name__}: {exc}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2
    checks.update(result["checks"])
    status = "COMPLETE_REVIEW_REQUIRED" if all(checks.values()) else "FAIL"
    decision = (
        _decision(result["comparison"], args)
        if status != "FAIL"
        else "FIX_PAIRWISE_LOW_RANK_BENCHMARK_CONTRACT"
    )

    _write_csv(frequency_csv, result["frequency_rows"])
    _write_csv(seed_csv, result["seed_rows"])
    _write_csv(history_csv, result["history_rows"])
    plot_status = _write_plot(plot_path, result["frequency_rows"], float(args.target_frequency_ghz))
    np.savez_compressed(weights_path, **result["weights"])
    payload.update(
        {
            "overall_status": status,
            "decision": decision,
            "checks": checks,
            "raw_input_s4p_quality": input_quality,
            "split_audit": result["split_audit"],
            "architecture": result["architecture"],
            "equal_budget_contract": result["equal_budget_contract"],
            "resonance_challenge_definition": result["resonance_challenge_definition"],
            "metrics": result["metrics"],
            "comparison": result["comparison"],
            "eligible_for_model_success_claim": False,
            "artifacts": {
                "summary": str(summary_path),
                "report": str(report_path),
                "frequency_errors": str(frequency_csv),
                "seed_metrics": str(seed_csv),
                "history": str(history_csv),
                "plot": str(plot_path) if plot_status == "PASS" else "",
                "plot_status": plot_status,
                "weights": str(weights_path),
            },
            "scientific_boundary": (
                "This is a PLRNet-inspired pairwise low-rank ablation, not a reproduction claim. "
                "A favorable frozen-forward result cannot alter the inverse model or EMX queue. "
                "Adoption still requires inverse retraining, DRC, new real EMX closure, and sampled "
                "HFSS correlation. Synthetic fixtures and proxy predictions never count as real labels."
            ),
        }
    )
    summary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(
        "test_full_band_relative_improvement="
        f"{result['comparison']['test_full_band_relative_improvement']}"
    )
    print(f"summary={summary_path}")
    return 0 if status == "COMPLETE_REVIEW_REQUIRED" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--training-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--geometry-columns")
    parser.add_argument("--split-reference-columns", default=broadband.DEFAULT_INPUT_COLUMNS)
    parser.add_argument("--min-rows", type=int, default=5000)
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--pairwise-rank", type=int, default=8)
    parser.add_argument("--mlp-hidden-depth", type=int, default=2)
    parser.add_argument("--mlp-hidden-width", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--gradient-clip", type=float, default=5.0)
    parser.add_argument("--model-seeds", default="20260711,20260712,20260713")
    parser.add_argument("--split-seed", type=int, default=20260711)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--resonance-variation-quantile", type=float, default=0.90)
    parser.add_argument("--input-reciprocity-audit-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--input-passivity-audit-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--max-input-reciprocity-error", type=float, default=0.02)
    parser.add_argument("--max-input-passivity-excess", type=float, default=0.05)
    parser.add_argument("--max-parameter-count-ratio", type=float, default=1.10)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.03)
    parser.add_argument("--minimum-physical-improvement", type=float, default=0.0)
    parser.add_argument("--max-frequency-regression-fraction", type=float, default=0.20)
    parser.add_argument("--frequency-regression-tolerance", type=float, default=0.05)
    parser.add_argument("--max-passivity-correction-increase", type=float, default=0.01)
    parser.add_argument("--max-candidate-test-complex-rmse", type=float, default=0.05)
    parser.add_argument("--max-candidate-raw-passivity-excess", type=float, default=0.05)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 10 <= args.min_rows <= args.max_rows:
        parser.error("require 10 <= --min-rows <= --max-rows")
    if args.epochs < 1 or args.batch_size < 1 or args.pairwise_rank < 1:
        parser.error("epochs, batch size, and pairwise rank must be positive")
    if args.mlp_hidden_depth < 1 or args.mlp_hidden_width < 0:
        parser.error("invalid MLP architecture")
    if args.learning_rate <= 0.0 or args.weight_decay < 0.0 or args.gradient_clip <= 0.0:
        parser.error("invalid optimizer settings")
    if args.expected_ports != 4:
        parser.error("this audited benchmark currently requires the production S4P contract")
    if not 0.0 < args.resonance_variation_quantile < 1.0:
        parser.error("--resonance-variation-quantile must be in (0,1)")
    if args.max_parameter_count_ratio < 1.0:
        parser.error("--max-parameter-count-ratio must be >= 1")
    for name in (
        "minimum_material_improvement",
        "minimum_physical_improvement",
        "max_frequency_regression_fraction",
        "frequency_regression_tolerance",
        "max_passivity_correction_increase",
        "max_candidate_test_complex_rmse",
        "max_candidate_raw_passivity_excess",
        "max_input_reciprocity_error",
        "max_input_passivity_excess",
    ):
        if float(getattr(args, name)) < 0.0:
            parser.error(f"--{name.replace('_', '-')} must be nonnegative")
    if args.max_frequency_regression_fraction > 1.0:
        parser.error("--max-frequency-regression-fraction must be in [0,1]")
    args.model_seeds = sequence._parse_seeds(args.model_seeds)
    if not args.model_seeds:
        parser.error("at least one model seed is required")
    sequence._validate_target_grid(args)
    return args


def _run_benchmark(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    geometry = np.asarray(dataset["geometry"], dtype=float)
    split_x = np.asarray(dataset["split_x"], dtype=float)
    frequencies = np.asarray(dataset["frequencies_hz"], dtype=float)
    matrices = broadband._decode_reciprocal_s(
        np.asarray(dataset["spectra"], dtype=float),
        int(args.expected_frequency_points),
        int(args.expected_ports),
    )
    upper_rows, upper_columns = np.triu_indices(int(args.expected_ports))
    upper = matrices[:, :, upper_rows, upper_columns]
    output = np.concatenate((upper.real, upper.imag), axis=2)
    split, split_audit = split_physical_feature_indices(
        split_x,
        mode="physical_cell_grouped",
        seed=int(args.split_seed),
        validation_fraction=0.15,
        test_fraction=0.10,
        physical_cell_bins=int(args.physical_cell_bins),
        physical_cell_lower=np.asarray([0.5, 0.5, 5.0, 0.0]),
        physical_cell_upper=np.asarray([3.0, 3.0, 25.0, 0.8]),
    )
    train = np.asarray(split["train"], dtype=int)
    validation = np.asarray(split["validation"], dtype=int)
    test = np.asarray(split["test"], dtype=int)
    geometry_mean = np.mean(geometry[train], axis=0)
    geometry_scale = np.maximum(np.std(geometry[train], axis=0), 1.0e-12)
    geometry_normalized = (geometry - geometry_mean[None, :]) / geometry_scale[None, :]
    output_train = output[train].reshape(-1, output.shape[2])
    output_mean = np.mean(output_train, axis=0)
    output_scale = np.maximum(np.std(output_train, axis=0), 1.0e-6)
    output_normalized = (output - output_mean[None, None, :]) / output_scale[None, None, :]
    frequency_normalized = 2.0 * (frequencies - frequencies[0]) / max(
        frequencies[-1] - frequencies[0], 1.0
    ) - 1.0
    sequence_x = sequence._sequence_inputs(geometry_normalized, frequency_normalized)
    resonance_indices, resonance_scores = sequence._resonance_challenge_indices(
        matrices[train],
        float(args.resonance_variation_quantile),
    )

    input_dim = sequence_x.shape[2]
    output_dim = output.shape[2]
    rank = int(args.pairwise_rank)
    pairwise_parameter_count = _pairwise_parameter_count(input_dim, rank, output_dim)
    mlp_width = int(args.mlp_hidden_width) or sequence._closest_mlp_width(
        input_dim,
        output_dim,
        int(args.mlp_hidden_depth),
        pairwise_parameter_count,
    )
    mlp_parameter_count = sequence._mlp_parameter_count(
        input_dim,
        output_dim,
        int(args.mlp_hidden_depth),
        mlp_width,
    )
    parameter_ratio = max(mlp_parameter_count, pairwise_parameter_count) / max(
        1,
        min(mlp_parameter_count, pairwise_parameter_count),
    )

    seed_results: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    seed_rows: list[dict[str, Any]] = []
    weights: dict[str, np.ndarray] = {
        "geometry_mean": geometry_mean,
        "geometry_scale": geometry_scale,
        "output_mean": output_mean,
        "output_scale": output_scale,
        "frequencies_hz": frequencies,
        "train_row_indices": train,
        "validation_row_indices": validation,
        "test_row_indices": test,
        "resonance_indices": resonance_indices,
        "resonance_scores": resonance_scores,
    }
    for seed in args.model_seeds:
        result = _train_seed(
            int(seed),
            train,
            validation,
            test,
            split_x,
            sequence_x,
            output_normalized,
            output_mean,
            output_scale,
            matrices,
            frequencies,
            resonance_indices,
            mlp_width,
            args,
        )
        seed_results.append(result)
        history_rows.extend(result["history_rows"])
        seed_rows.extend(_seed_metric_rows(int(seed), result))
        for index, value in enumerate(result["mlp_model"]["weights"]):
            weights[f"seed_{seed}_mlp_weight_{index}"] = value
        for index, value in enumerate(result["mlp_model"]["biases"]):
            weights[f"seed_{seed}_mlp_bias_{index}"] = value
        for name, value in result["pairwise_low_rank_model"].items():
            weights[f"seed_{seed}_pairwise_{name}"] = value

    aggregate = _aggregate_seed_results(seed_results, frequencies, args)
    equal_updates = all(
        item["mlp_updates"] == item["pairwise_low_rank_updates"] for item in seed_results
    )
    checks = {
        "physical_cell_ood_split": split_audit.get("split_mode") == "physical_cell_grouped",
        "physical_cell_overlap_zero": int(split_audit.get("physical_cell_overlap_count") or 0) == 0,
        "all_rows_assigned_once": split_audit.get("all_rows_assigned_once") is True,
        "train_validation_test_rows_disjoint": pointwise._rows_are_pairwise_disjoint(
            train, validation, test
        ),
        "train_validation_test_nonempty": min(len(train), len(validation), len(test)) > 0,
        "full_frequency_grid_used": sequence_x.shape[1] == int(args.expected_frequency_points),
        "train_only_resonance_challenge_nonempty": len(resonance_indices) > 0,
        "equal_optimizer_updates_per_seed": equal_updates,
        "parameter_budget_ratio_within_limit": parameter_ratio
        <= float(args.max_parameter_count_ratio),
        "finite_validation_metrics": _all_finite(seed_results, "validation"),
        "finite_test_metrics": _all_finite(seed_results, "test"),
        "physical_metric_extraction_available": all(
            item["test"][arm]["metrics"]["target_physical_valid_fraction"] > 0.99
            for item in seed_results
            for arm in ARMS
        ),
    }
    return {
        "checks": checks,
        "split_audit": split_audit,
        "architecture": {
            "input": "10 normalized geometry variables plus normalized frequency",
            "output": f"{output_dim} real values for reciprocal S4P upper triangle per frequency",
            "mlp": {
                "kind": "frequency-conditioned pointwise MLP",
                "hidden_depth": int(args.mlp_hidden_depth),
                "hidden_width": mlp_width,
                "activation": "GELU",
                "parameter_count": mlp_parameter_count,
            },
            "pairwise_low_rank": {
                "kind": "PLRNet-inspired coordinate embedding with low-rank pairwise aggregation",
                "rank": rank,
                "embedding": "tanh(a*x + b*x^2 + c) per coordinate",
                "pairwise_feature": "0.5*((sum_i e_i)^2-sum_i(e_i^2))",
                "parameter_count": pairwise_parameter_count,
                "is_exact_plrnet_reproduction": False,
            },
            "parameter_count_ratio": parameter_ratio,
        },
        "equal_budget_contract": {
            "same_real_s4p_rows": True,
            "same_physical_cell_ood_split": True,
            "same_row_order_and_batches": True,
            "same_epochs": int(args.epochs),
            "same_optimizer": "Adam",
            "same_learning_rate": float(args.learning_rate),
            "same_weight_decay": float(args.weight_decay),
            "same_gradient_clip": float(args.gradient_clip),
            "equal_optimizer_updates_per_seed": equal_updates,
            "parameter_count_ratio": parameter_ratio,
            "validation_only_checkpoint_selection": True,
            "validation_set_used_for_training": False,
            "test_set_used_for_training": False,
            "test_set_used_for_checkpoint_selection": False,
            "test_set_role": "single frozen-model final assessment and predeclared adoption gate",
            "train_row_indices_sha256": pointwise._array_sha256(train),
            "validation_row_indices_sha256": pointwise._array_sha256(validation),
            "test_row_indices_sha256": pointwise._array_sha256(test),
            "only_model_difference": "dense hidden layers versus low-rank coordinate-pair interaction features",
        },
        "resonance_challenge_definition": {
            "source": "training rows only",
            "score": "mean absolute adjacent-frequency change over full reciprocal S matrix",
            "quantile": float(args.resonance_variation_quantile),
            "frequency_indices": [int(value) for value in resonance_indices],
            "frequencies_ghz": [float(frequencies[value] / 1.0e9) for value in resonance_indices],
            "boundary": "A response-variation challenge set, not proof of physical resonance at every point.",
        },
        "metrics": aggregate["metrics"],
        "comparison": aggregate["comparison"],
        "frequency_rows": aggregate["frequency_rows"],
        "seed_rows": seed_rows,
        "history_rows": history_rows,
        "weights": weights,
    }


def _train_seed(
    seed: int,
    train: np.ndarray,
    validation: np.ndarray,
    test: np.ndarray,
    physical_features: np.ndarray,
    sequence_x: np.ndarray,
    output_normalized: np.ndarray,
    output_mean: np.ndarray,
    output_scale: np.ndarray,
    truth: np.ndarray,
    frequencies: np.ndarray,
    resonance_indices: np.ndarray,
    mlp_width: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    input_dim = sequence_x.shape[2]
    output_dim = output_normalized.shape[2]
    mlp = pointwise._init_mlp(
        input_dim,
        output_dim,
        int(args.mlp_hidden_depth),
        int(mlp_width),
        np.random.default_rng(sequence._seed(seed, "mlp-init")),
    )
    pairwise_model = _init_pairwise_model(
        input_dim,
        int(args.pairwise_rank),
        output_dim,
        np.random.default_rng(sequence._seed(seed, "pairwise-init")),
    )
    mlp_state = pointwise._init_adam(mlp)
    pairwise_state = sequence._init_flat_adam(pairwise_model)
    order_rng = np.random.default_rng(sequence._seed(seed, "shared-batches"))
    mlp_updates = 0
    pairwise_updates = 0
    best_mlp = pointwise._copy_model(mlp)
    best_pairwise = _copy_pairwise_model(pairwise_model)
    best_mlp_epoch = 0
    best_pairwise_epoch = 0
    best_mlp_validation = math.inf
    best_pairwise_validation = math.inf
    mlp_train_seconds = 0.0
    pairwise_train_seconds = 0.0
    history_rows: list[dict[str, Any]] = []

    for epoch in range(1, int(args.epochs) + 1):
        order = order_rng.permutation(train)
        for start in range(0, len(order), int(args.batch_size)):
            indices = order[start : start + int(args.batch_size)]
            batch_x = sequence_x[indices].reshape(-1, input_dim)
            batch_y = output_normalized[indices].reshape(-1, output_dim)

            started = time.perf_counter()
            grad_weights, grad_biases = pointwise._gradients(
                batch_x,
                batch_y,
                mlp,
                float(args.weight_decay),
            )
            sequence._clip_list_gradients(
                grad_weights + grad_biases,
                float(args.gradient_clip),
            )
            pointwise._adam_step(
                mlp,
                grad_weights,
                grad_biases,
                mlp_state,
                float(args.learning_rate),
            )
            mlp_train_seconds += time.perf_counter() - started
            mlp_updates += 1

            started = time.perf_counter()
            pairwise_gradients = _pairwise_gradients(
                batch_x,
                batch_y,
                pairwise_model,
                float(args.weight_decay),
            )
            sequence._clip_list_gradients(
                list(pairwise_gradients.values()),
                float(args.gradient_clip),
            )
            sequence._adam_flat_step(
                pairwise_model,
                pairwise_gradients,
                pairwise_state,
                float(args.learning_rate),
            )
            pairwise_train_seconds += time.perf_counter() - started
            pairwise_updates += 1

        validation_mlp = _evaluate_model(
            "mlp",
            mlp,
            validation,
            physical_features,
            sequence_x,
            output_mean,
            output_scale,
            truth,
            frequencies,
            resonance_indices,
            args,
        )
        validation_pairwise = _evaluate_model(
            "pairwise_low_rank",
            pairwise_model,
            validation,
            physical_features,
            sequence_x,
            output_mean,
            output_scale,
            truth,
            frequencies,
            resonance_indices,
            args,
        )
        mlp_score = float(validation_mlp["metrics"]["raw_complex_rmse"])
        pairwise_score = float(validation_pairwise["metrics"]["raw_complex_rmse"])
        if mlp_score < best_mlp_validation:
            best_mlp_validation = mlp_score
            best_mlp = pointwise._copy_model(mlp)
            best_mlp_epoch = epoch
        if pairwise_score < best_pairwise_validation:
            best_pairwise_validation = pairwise_score
            best_pairwise = _copy_pairwise_model(pairwise_model)
            best_pairwise_epoch = epoch
        history_rows.append(
            {
                "seed": seed,
                "epoch": epoch,
                "mlp_validation_raw_complex_rmse": mlp_score,
                "pairwise_low_rank_validation_raw_complex_rmse": pairwise_score,
                "mlp_updates": mlp_updates,
                "pairwise_low_rank_updates": pairwise_updates,
            }
        )

    validation_mlp = _evaluate_model(
        "mlp",
        best_mlp,
        validation,
        physical_features,
        sequence_x,
        output_mean,
        output_scale,
        truth,
        frequencies,
        resonance_indices,
        args,
    )
    validation_pairwise = _evaluate_model(
        "pairwise_low_rank",
        best_pairwise,
        validation,
        physical_features,
        sequence_x,
        output_mean,
        output_scale,
        truth,
        frequencies,
        resonance_indices,
        args,
    )
    started = time.perf_counter()
    test_mlp = _evaluate_model(
        "mlp",
        best_mlp,
        test,
        physical_features,
        sequence_x,
        output_mean,
        output_scale,
        truth,
        frequencies,
        resonance_indices,
        args,
    )
    mlp_inference_seconds = time.perf_counter() - started
    started = time.perf_counter()
    test_pairwise = _evaluate_model(
        "pairwise_low_rank",
        best_pairwise,
        test,
        physical_features,
        sequence_x,
        output_mean,
        output_scale,
        truth,
        frequencies,
        resonance_indices,
        args,
    )
    pairwise_inference_seconds = time.perf_counter() - started
    return {
        "seed": seed,
        "mlp_updates": mlp_updates,
        "pairwise_low_rank_updates": pairwise_updates,
        "mlp_best_epoch": best_mlp_epoch,
        "pairwise_low_rank_best_epoch": best_pairwise_epoch,
        "mlp_train_seconds": mlp_train_seconds,
        "pairwise_low_rank_train_seconds": pairwise_train_seconds,
        "mlp_inference_seconds": mlp_inference_seconds,
        "pairwise_low_rank_inference_seconds": pairwise_inference_seconds,
        "validation": {"mlp": validation_mlp, "pairwise_low_rank": validation_pairwise},
        "test": {"mlp": test_mlp, "pairwise_low_rank": test_pairwise},
        "history_rows": history_rows,
        "mlp_model": best_mlp,
        "pairwise_low_rank_model": best_pairwise,
    }


def _init_pairwise_model(
    input_dim: int,
    rank: int,
    output_dim: int,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    scale = 1.0 / math.sqrt(max(1, input_dim))
    feature_dim = 2 * input_dim + 2 * rank
    return {
        "linear_embedding": rng.normal(0.0, scale, size=(input_dim, rank)),
        "quadratic_embedding": rng.normal(0.0, scale, size=(input_dim, rank)),
        "embedding_bias": np.zeros((input_dim, rank), dtype=float),
        "output_weight": rng.normal(
            0.0,
            math.sqrt(2.0 / max(1, feature_dim)),
            size=(feature_dim, output_dim),
        ),
        "output_bias": np.zeros(output_dim, dtype=float),
    }


def _copy_pairwise_model(model: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {name: np.array(value, copy=True) for name, value in model.items()}


def _pairwise_forward(
    model: dict[str, np.ndarray],
    x: np.ndarray,
    *,
    return_cache: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray] | None]:
    squared = x**2
    preactivation = (
        x[:, :, None] * model["linear_embedding"][None, :, :]
        + squared[:, :, None] * model["quadratic_embedding"][None, :, :]
        + model["embedding_bias"][None, :, :]
    )
    embedding = np.tanh(preactivation)
    pooled = np.sum(embedding, axis=1)
    pairwise = 0.5 * (pooled**2 - np.sum(embedding**2, axis=1))
    features = np.concatenate((x, squared, pooled, pairwise), axis=1)
    prediction = features @ model["output_weight"] + model["output_bias"]
    if not return_cache:
        return prediction, None
    return prediction, {
        "x": x,
        "squared": squared,
        "embedding": embedding,
        "pooled": pooled,
        "features": features,
    }


def _pairwise_gradients(
    x: np.ndarray,
    y: np.ndarray,
    model: dict[str, np.ndarray],
    weight_decay: float,
) -> dict[str, np.ndarray]:
    prediction, cache = _pairwise_forward(model, x, return_cache=True)
    assert cache is not None
    delta = (2.0 / max(1, len(x))) * (prediction - y)
    output_weight_gradient = cache["features"].T @ delta + weight_decay * model[
        "output_weight"
    ]
    output_bias_gradient = np.sum(delta, axis=0)
    feature_gradient = delta @ model["output_weight"].T
    input_dim = x.shape[1]
    rank = model["linear_embedding"].shape[1]
    pooled_gradient = feature_gradient[:, 2 * input_dim : 2 * input_dim + rank]
    pairwise_gradient = feature_gradient[:, 2 * input_dim + rank :]
    embedding_gradient = pooled_gradient[:, None, :] + pairwise_gradient[:, None, :] * (
        cache["pooled"][:, None, :] - cache["embedding"]
    )
    preactivation_gradient = embedding_gradient * (1.0 - cache["embedding"] ** 2)
    linear_gradient = np.sum(
        preactivation_gradient * cache["x"][:, :, None],
        axis=0,
    ) + weight_decay * model["linear_embedding"]
    quadratic_gradient = np.sum(
        preactivation_gradient * cache["squared"][:, :, None],
        axis=0,
    ) + weight_decay * model["quadratic_embedding"]
    embedding_bias_gradient = np.sum(preactivation_gradient, axis=0)
    return {
        "linear_embedding": linear_gradient,
        "quadratic_embedding": quadratic_gradient,
        "embedding_bias": embedding_bias_gradient,
        "output_weight": output_weight_gradient,
        "output_bias": output_bias_gradient,
    }


def _pairwise_predict(
    model: dict[str, np.ndarray],
    x: np.ndarray,
    batch_size: int = 8192,
) -> np.ndarray:
    outputs = []
    for start in range(0, len(x), int(batch_size)):
        prediction, _ = _pairwise_forward(
            model,
            x[start : start + int(batch_size)],
            return_cache=False,
        )
        outputs.append(prediction)
    return np.concatenate(outputs, axis=0) if outputs else np.empty((0, model["output_bias"].shape[0]))


def _pairwise_parameter_count(input_dim: int, rank: int, output_dim: int) -> int:
    feature_dim = 2 * input_dim + 2 * rank
    return int(3 * input_dim * rank + feature_dim * output_dim + output_dim)


def _evaluate_model(
    kind: str,
    model: dict[str, Any],
    rows: np.ndarray,
    physical_features: np.ndarray,
    sequence_x: np.ndarray,
    output_mean: np.ndarray,
    output_scale: np.ndarray,
    truth: np.ndarray,
    frequencies: np.ndarray,
    resonance_indices: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    indices = np.asarray(rows, dtype=int)
    x = sequence_x[indices]
    pair_x = x.reshape(-1, x.shape[2])
    if kind == "mlp":
        normalized = pointwise._predict(model, pair_x).reshape(len(indices), x.shape[1], -1)
    elif kind == "pairwise_low_rank":
        normalized = _pairwise_predict(model, pair_x).reshape(len(indices), x.shape[1], -1)
    else:
        raise ValueError(f"unknown model kind {kind}")
    components = normalized * output_scale[None, None, :] + output_mean[None, None, :]
    prediction = sequence._components_to_matrix(components, int(args.expected_ports))
    target = truth[indices]
    evaluation = sequence._matrix_metrics(
        prediction,
        target,
        frequencies,
        resonance_indices,
        args,
    )
    evaluation["metrics"].update(
        _physical_cell_complex_s_tail_metrics(
            prediction,
            target,
            np.asarray(physical_features, dtype=float)[indices],
            int(args.physical_cell_bins),
        )
    )
    return evaluation


def _physical_cell_complex_s_tail_metrics(
    prediction: np.ndarray,
    target: np.ndarray,
    physical_features: np.ndarray,
    bins: int,
) -> dict[str, float | int]:
    lower = np.asarray([0.5, 0.5, 5.0, 0.0], dtype=float)
    upper = np.asarray([3.0, 3.0, 25.0, 0.8], dtype=float)
    normalized = (np.asarray(physical_features, dtype=float) - lower[None, :]) / (
        upper - lower
    )[None, :]
    if np.any((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12)):
        raise ValueError("physical-cell tail metrics received out-of-range rows")
    cells = np.minimum(
        np.floor(np.clip(normalized, 0.0, 1.0) * int(bins)).astype(int),
        int(bins) - 1,
    )
    labels = [":".join(str(int(value)) for value in row) for row in cells]
    error = prediction - target
    row_rmse = np.sqrt(np.mean(np.abs(error) ** 2, axis=(1, 2, 3)))
    cell_rmse = []
    for label in sorted(set(labels)):
        mask = np.asarray([value == label for value in labels], dtype=bool)
        cell_rmse.append(float(np.sqrt(np.mean(np.abs(error[mask]) ** 2))))
    values = np.asarray(cell_rmse, dtype=float)
    return {
        "physical_cell_count": int(len(values)),
        "row_complex_s_rmse_p95": float(np.quantile(row_rmse, 0.95)),
        "equal_cell_complex_s_rmse": float(np.sqrt(np.mean(values**2))),
        "physical_cell_complex_s_rmse_p95": float(np.quantile(values, 0.95)),
        "physical_cell_complex_s_rmse_max": float(np.max(values)),
    }


def _aggregate_seed_results(
    seed_results: list[dict[str, Any]],
    frequencies: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"validation": {}, "test": {}, "runtime": {}}
    for split_name in ("validation", "test"):
        for arm in ARMS:
            rows = [item[split_name][arm]["metrics"] for item in seed_results]
            metrics[split_name][arm] = sequence._aggregate_numeric_dicts(rows)
    for arm in ARMS:
        train_values = np.asarray(
            [item[f"{arm}_train_seconds"] for item in seed_results],
            dtype=float,
        )
        inference_values = np.asarray(
            [item[f"{arm}_inference_seconds"] for item in seed_results],
            dtype=float,
        )
        metrics["runtime"][arm] = {
            "train_seconds_mean": float(np.mean(train_values)),
            "train_seconds_std": float(np.std(train_values)),
            "test_inference_seconds_mean": float(np.mean(inference_values)),
            "test_inference_seconds_std": float(np.std(inference_values)),
        }
    mlp_frequency = np.asarray(
        [
            [row["raw_complex_rmse"] for row in item["test"]["mlp"]["frequency_rows"]]
            for item in seed_results
        ],
        dtype=float,
    )
    pairwise_frequency = np.asarray(
        [
            [
                row["raw_complex_rmse"]
                for row in item["test"]["pairwise_low_rank"]["frequency_rows"]
            ]
            for item in seed_results
        ],
        dtype=float,
    )
    frequency_rows = []
    for index, frequency in enumerate(frequencies):
        baseline = float(np.mean(mlp_frequency[:, index]))
        candidate = float(np.mean(pairwise_frequency[:, index]))
        frequency_rows.append(
            {
                "frequency_ghz": float(frequency / 1.0e9),
                "mlp_raw_complex_rmse_mean": baseline,
                "mlp_raw_complex_rmse_std": float(np.std(mlp_frequency[:, index])),
                "pairwise_low_rank_raw_complex_rmse_mean": candidate,
                "pairwise_low_rank_raw_complex_rmse_std": float(
                    np.std(pairwise_frequency[:, index])
                ),
                "pairwise_low_rank_relative_improvement": sequence._improvement(
                    baseline,
                    candidate,
                ),
            }
        )
    validation_mlp = metrics["validation"]["mlp"]
    validation_candidate = metrics["validation"]["pairwise_low_rank"]
    test_mlp = metrics["test"]["mlp"]
    test_candidate = metrics["test"]["pairwise_low_rank"]
    regression = np.mean(pairwise_frequency, axis=0) > np.mean(mlp_frequency, axis=0) * (
        1.0 + float(args.frequency_regression_tolerance)
    )
    comparison = {
        "model_seed_count": len(seed_results),
        "validation_full_band_relative_improvement": sequence._improvement(
            validation_mlp["raw_complex_rmse"]["mean"],
            validation_candidate["raw_complex_rmse"]["mean"],
        ),
        "validation_target_relative_improvement": sequence._improvement(
            validation_mlp["target_raw_complex_rmse"]["mean"],
            validation_candidate["target_raw_complex_rmse"]["mean"],
        ),
        "test_full_band_relative_improvement": sequence._improvement(
            test_mlp["raw_complex_rmse"]["mean"],
            test_candidate["raw_complex_rmse"]["mean"],
        ),
        "test_target_relative_improvement": sequence._improvement(
            test_mlp["target_raw_complex_rmse"]["mean"],
            test_candidate["target_raw_complex_rmse"]["mean"],
        ),
        "test_resonance_relative_improvement": sequence._improvement(
            test_mlp["resonance_raw_complex_rmse"]["mean"],
            test_candidate["resonance_raw_complex_rmse"]["mean"],
        ),
        "test_equal_cell_relative_improvement": sequence._improvement(
            test_mlp["equal_cell_complex_s_rmse"]["mean"],
            test_candidate["equal_cell_complex_s_rmse"]["mean"],
        ),
        "test_physical_cell_p95_relative_improvement": sequence._improvement(
            test_mlp["physical_cell_complex_s_rmse_p95"]["mean"],
            test_candidate["physical_cell_complex_s_rmse_p95"]["mean"],
        ),
        "test_frequency_regression_fraction": float(np.mean(regression)),
        "test_frequency_regression_count": int(np.sum(regression)),
        "test_passivity_projection_correction_increase": float(
            test_candidate["passivity_projection_complex_rmse"]["mean"]
            - test_mlp["passivity_projection_complex_rmse"]["mean"]
        ),
        "candidate_test_raw_complex_rmse": float(
            test_candidate["raw_complex_rmse"]["mean"]
        ),
        "candidate_test_raw_max_passivity_excess": float(
            test_candidate["raw_max_passivity_excess"]["mean"]
        ),
        "candidate_test_target_physical_valid_fraction": float(
            test_candidate["target_physical_valid_fraction"]["mean"]
        ),
    }
    for name in ("lp_nh", "ls_nh", "q", "k_abs"):
        comparison[f"test_target_{name}_mae_relative_improvement"] = sequence._improvement(
            test_mlp[f"target_{name}_mae"]["mean"],
            test_candidate[f"target_{name}_mae"]["mean"],
        )
    comparison.update(
        {
            "minimum_material_improvement": float(args.minimum_material_improvement),
            "minimum_physical_improvement": float(args.minimum_physical_improvement),
            "max_frequency_regression_fraction": float(args.max_frequency_regression_fraction),
            "max_passivity_correction_increase": float(args.max_passivity_correction_increase),
            "max_candidate_test_complex_rmse": float(args.max_candidate_test_complex_rmse),
            "max_candidate_raw_passivity_excess": float(
                args.max_candidate_raw_passivity_excess
            ),
        }
    )
    return {"metrics": metrics, "comparison": comparison, "frequency_rows": frequency_rows}


def _decision(comparison: dict[str, Any], args: argparse.Namespace) -> str:
    material = float(args.minimum_material_improvement)
    physical = float(args.minimum_physical_improvement)
    response_improvements = (
        float(comparison["validation_full_band_relative_improvement"]),
        float(comparison["validation_target_relative_improvement"]),
        float(comparison["test_full_band_relative_improvement"]),
        float(comparison["test_target_relative_improvement"]),
        float(comparison["test_resonance_relative_improvement"]),
        float(comparison["test_equal_cell_relative_improvement"]),
        float(comparison["test_physical_cell_p95_relative_improvement"]),
    )
    physical_improvements = tuple(
        float(comparison[f"test_target_{name}_mae_relative_improvement"])
        for name in ("lp_nh", "ls_nh", "q", "k_abs")
    )
    quality_ok = (
        float(comparison["test_frequency_regression_fraction"])
        <= float(args.max_frequency_regression_fraction)
        and float(comparison["test_passivity_projection_correction_increase"])
        <= float(args.max_passivity_correction_increase)
        and float(comparison["candidate_test_raw_complex_rmse"])
        <= float(args.max_candidate_test_complex_rmse)
        and float(comparison["candidate_test_raw_max_passivity_excess"])
        <= float(args.max_candidate_raw_passivity_excess)
        and float(comparison["candidate_test_target_physical_valid_fraction"]) > 0.99
    )
    if (
        all(value >= material for value in response_improvements)
        and all(value >= physical for value in physical_improvements)
        and quality_ok
    ):
        return "REVIEW_PAIRWISE_LOW_RANK_FOR_FROZEN_FORWARD_INVERSE_ABLATION"
    if all(value <= -material for value in response_improvements):
        return "RETAIN_POINTWISE_MLP_PAIRWISE_LOW_RANK_IS_MATERIALLY_WORSE"
    return "RETAIN_POINTWISE_MLP_MIXED_PAIRWISE_LOW_RANK_EVIDENCE"


def _all_finite(seed_results: list[dict[str, Any]], split_name: str) -> bool:
    for item in seed_results:
        for arm in ARMS:
            for value in item[split_name][arm]["metrics"].values():
                if isinstance(value, (int, float)) and not math.isfinite(float(value)):
                    return False
    return True


def _seed_metric_rows(seed: int, result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for split_name in ("validation", "test"):
        for arm in ARMS:
            rows.append(
                {
                    "seed": seed,
                    "split": split_name,
                    "arm": arm,
                    "best_epoch": result[f"{arm}_best_epoch"],
                    "optimizer_updates": result[f"{arm}_updates"],
                    "train_seconds": result[f"{arm}_train_seconds"],
                    "test_inference_seconds": (
                        result[f"{arm}_inference_seconds"] if split_name == "test" else ""
                    ),
                    **result[split_name][arm]["metrics"],
                }
            )
    return rows


def _base_summary(
    args: argparse.Namespace,
    training_csv: Path,
    training_manifest: Path,
    geometry_columns: list[str],
    split_columns: list[str],
    dataset: dict[str, Any],
    rejects: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "training_manifest": str(training_manifest),
        "training_manifest_sha256": pointwise._file_sha256(training_manifest),
        "training_count": int(dataset.get("count") or 0),
        "geometry_columns": geometry_columns,
        "split_reference_columns": split_columns,
        "row_identity_sha256": str(dataset.get("row_identity_sha256") or ""),
        "touchstone_content_sha256": str(dataset.get("touchstone_content_sha256") or ""),
        "frequency_grid_sha256": str(dataset.get("frequency_grid_sha256") or ""),
        "rejected_row_count": len(rejects),
        "rejected_rows": rejects[:100],
        "arguments": vars(args),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_plot(path: Path, rows: list[dict[str, Any]], target_frequency_ghz: float) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    frequencies = [row["frequency_ghz"] for row in rows]
    figure, axis = plt.subplots(figsize=(8.4, 4.8), dpi=180)
    axis.plot(
        frequencies,
        [row["mlp_raw_complex_rmse_mean"] for row in rows],
        color="#4b5563",
        label="Pointwise MLP",
    )
    axis.plot(
        frequencies,
        [row["pairwise_low_rank_raw_complex_rmse_mean"] for row in rows],
        color="#0f766e",
        label="Pairwise low-rank",
    )
    axis.axvline(target_frequency_ghz, color="#b9473a", linestyle=":", linewidth=1.1)
    axis.set_xlabel("Frequency (GHz)")
    axis.set_ylabel("Complex S RMSE")
    axis.set_title("Equal-budget pairwise low-rank forward-surrogate ablation")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _render_report(data: dict[str, Any]) -> str:
    comparison = data.get("comparison") or {}
    return "\n".join(
        [
            "# Pairwise low-rank frequency surrogate benchmark",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Real Touchstone rows: `{data['training_count']}`",
            f"- Test full-band relative improvement: `{comparison.get('test_full_band_relative_improvement')}`",
            f"- Test 15 GHz relative improvement: `{comparison.get('test_target_relative_improvement')}`",
            f"- Test frequency regression fraction: `{comparison.get('test_frequency_regression_fraction')}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


def _same_path(raw: Any, expected: Path) -> bool:
    if not raw:
        return False
    try:
        return Path(str(raw)).expanduser().resolve() == expected.resolve()
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
