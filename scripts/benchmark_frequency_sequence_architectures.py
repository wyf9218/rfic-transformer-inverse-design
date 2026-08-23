#!/usr/bin/env python3
"""Benchmark a pointwise MLP against a lightweight GRU on real S4P rows.

The two arms use the same reciprocal complex-S representation, physical-cell
OOD split, row batches, epoch count, optimizer updates, and model seeds.  The
only architectural difference is whether frequency points are predicted
independently or through a recurrent hidden state.  This is an isolated
forward-surrogate ablation; it does not alter an EMX queue or inverse model.
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

import extract_touchstone_response_features as response  # noqa: E402
import train_broadband_sparameter_pca_surrogate as broadband  # noqa: E402
import benchmark_frequency_domain_self_transfer as pointwise  # noqa: E402
from compare_emx_hfss_ads import parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    training_manifest = Path(args.training_manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "frequency_sequence_architecture_summary.json"
    report_path = out_dir / "frequency_sequence_architecture_report.md"
    frequency_csv = out_dir / "frequency_sequence_frequency_errors.csv"
    seed_csv = out_dir / "frequency_sequence_seed_metrics.csv"
    history_csv = out_dir / "frequency_sequence_history.csv"
    plot_path = out_dir / "frequency_sequence_frequency_errors.png"
    weights_path = out_dir / "frequency_sequence_weights.npz"

    manifest = _read_json(training_manifest)
    rows = broadband._read_rows(training_csv)
    geometry_columns = broadband._geometry_columns(rows, args.geometry_columns)
    split_columns = broadband._split_columns(args.split_reference_columns)
    selected_rows = broadband._deterministic_spread_sample(rows, int(args.max_rows))
    dataset, rejects = broadband._load_dataset(selected_rows, geometry_columns, split_columns, args)
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "training_manifest_exists": training_manifest.is_file(),
        "training_manifest_pass": manifest.get("overall_status") == "PASS",
        "training_manifest_matches_csv": _same_path(manifest.get("training_csv"), training_csv),
        "geometry_predictor_count_is_ten": len(geometry_columns) == 10,
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
        status = "WAITING_FOR_COMPLETE_BROADBAND_DATA" if not checks["usable_rows_meet_minimum"] else "FAIL"
        payload.update(
            {
                "overall_status": status,
                "decision": "WAIT_FOR_REAL_S4P_ROWS" if status.startswith("WAITING") else "FIX_SEQUENCE_BENCHMARK_CONTRACT",
                "checks": checks,
                "artifacts": {"summary": str(summary_path)},
            }
        )
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"overall_status={status}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    input_quality = broadband._summarize_input_quality(dataset, args)
    checks.update(
        {
            "raw_input_reciprocity_threshold_pass": bool(input_quality["reciprocity"]["hard_threshold_pass"]),
            "raw_input_passivity_threshold_pass": bool(input_quality["passivity"]["hard_threshold_pass"]),
        }
    )
    result = _run_benchmark(dataset, args)
    checks.update(result["checks"])
    status = "COMPLETE_REVIEW_REQUIRED" if all(checks.values()) else "FAIL"
    decision = _decision(result["comparison"], args) if status != "FAIL" else "FIX_SEQUENCE_BENCHMARK_CONTRACT"

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
                "This benchmark can nominate a frequency-sequential forward surrogate only. It does not prove "
                "inverse-design accuracy, full-band causality, data sufficiency, or EMX/HFSS agreement. Adoption "
                "still requires frozen-forward inverse retraining, DRC, real EMX closure, and sampled HFSS correlation."
            ),
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"test_full_band_relative_improvement={result['comparison']['test_full_band_relative_improvement']}")
    print(f"summary={summary_path}")
    return 0 if status == "COMPLETE_REVIEW_REQUIRED" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--training-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--geometry-columns")
    parser.add_argument("--split-reference-columns", default=broadband.DEFAULT_INPUT_COLUMNS)
    parser.add_argument("--min-rows", type=int, default=10000)
    parser.add_argument("--max-rows", type=int, default=20000)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--gru-hidden-width", type=int, default=32)
    parser.add_argument("--mlp-hidden-depth", type=int, default=2)
    parser.add_argument("--mlp-hidden-width", type=int, default=0, help="0 chooses the closest GRU parameter budget")
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--gradient-clip", type=float, default=5.0)
    parser.add_argument("--model-seeds", default="20260711,20260712,20260713,20260714,20260715")
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
    parser.add_argument("--max-frequency-regression-fraction", type=float, default=0.20)
    parser.add_argument("--frequency-regression-tolerance", type=float, default=0.05)
    parser.add_argument("--max-passivity-correction-increase", type=float, default=0.01)
    parser.add_argument("--max-candidate-test-complex-rmse", type=float, default=0.05)
    parser.add_argument("--max-candidate-raw-passivity-excess", type=float, default=0.05)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 10 <= args.min_rows <= args.max_rows:
        parser.error("require 10 <= --min-rows <= --max-rows")
    if args.epochs < 1 or args.batch_size < 1 or args.gru_hidden_width < 2:
        parser.error("epochs, batch size, and GRU width must be positive")
    if args.mlp_hidden_depth < 1 or args.mlp_hidden_width < 0:
        parser.error("invalid MLP architecture")
    if args.learning_rate <= 0.0 or args.weight_decay < 0.0 or args.gradient_clip <= 0.0:
        parser.error("invalid optimizer settings")
    if not 0.0 < args.resonance_variation_quantile < 1.0:
        parser.error("--resonance-variation-quantile must be in (0,1)")
    if args.expected_ports != 4:
        parser.error("this audited benchmark currently requires the production S4P contract")
    if args.expected_frequency_points < 2:
        parser.error("at least two frequency points are required")
    if args.max_parameter_count_ratio < 1.0:
        parser.error("--max-parameter-count-ratio must be >= 1")
    if not 0.0 <= args.minimum_material_improvement < 1.0:
        parser.error("--minimum-material-improvement must be in [0,1)")
    if not 0.0 <= args.max_frequency_regression_fraction <= 1.0:
        parser.error("--max-frequency-regression-fraction must be in [0,1]")
    if args.max_candidate_test_complex_rmse < 0.0 or args.max_candidate_raw_passivity_excess < 0.0:
        parser.error("candidate absolute-quality thresholds must be nonnegative")
    args.model_seeds = _parse_seeds(args.model_seeds)
    if not args.model_seeds:
        parser.error("at least one model seed is required")
    _validate_target_grid(args)
    return args


def _validate_target_grid(args: argparse.Namespace) -> None:
    start = float(args.expected_frequency_start_ghz)
    stop = float(args.expected_frequency_stop_ghz)
    step = float(args.expected_frequency_step_ghz)
    target = float(args.target_frequency_ghz)
    if not start <= target <= stop:
        raise SystemExit("target frequency lies outside the expected grid")
    nearest = start + round((target - start) / step) * step
    if abs(nearest - target) * 1.0e9 > float(args.frequency_tolerance_hz):
        raise SystemExit("target frequency does not align with the expected grid")


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
    frequency_normalized = 2.0 * (frequencies - frequencies[0]) / max(frequencies[-1] - frequencies[0], 1.0) - 1.0
    sequence_x = _sequence_inputs(geometry_normalized, frequency_normalized)
    resonance_indices, resonance_scores = _resonance_challenge_indices(
        matrices[train], float(args.resonance_variation_quantile)
    )

    input_dim = sequence_x.shape[2]
    output_dim = output.shape[2]
    gru_width = int(args.gru_hidden_width)
    gru_parameter_count = _gru_parameter_count(input_dim, gru_width, output_dim)
    mlp_width = int(args.mlp_hidden_width) or _closest_mlp_width(
        input_dim, output_dim, int(args.mlp_hidden_depth), gru_parameter_count
    )
    mlp_parameter_count = _mlp_parameter_count(input_dim, output_dim, int(args.mlp_hidden_depth), mlp_width)
    parameter_ratio = max(mlp_parameter_count, gru_parameter_count) / max(1, min(mlp_parameter_count, gru_parameter_count))

    seed_rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    seed_results: list[dict[str, Any]] = []
    weights: dict[str, np.ndarray] = {
        "geometry_mean": geometry_mean,
        "geometry_scale": geometry_scale,
        "output_mean": output_mean,
        "output_scale": output_scale,
        "frequencies_hz": frequencies,
        "resonance_indices": resonance_indices,
        "resonance_scores": resonance_scores,
    }
    for seed in args.model_seeds:
        result = _train_seed(
            seed,
            train,
            validation,
            test,
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
        seed_rows.extend(_seed_metric_rows(seed, result))
        for arm, model in (("mlp", result["mlp_model"]), ("gru", result["gru_model"])):
            for name, value in _model_arrays(arm, model).items():
                weights[f"seed_{seed}_{name}"] = value

    aggregate = _aggregate_seed_results(seed_results, frequencies, args)
    equal_updates = all(item["mlp_updates"] == item["gru_updates"] for item in seed_results)
    checks = {
        "physical_cell_ood_split": split_audit.get("split_mode") == "physical_cell_grouped",
        "physical_cell_overlap_zero": int(split_audit.get("physical_cell_overlap_count") or 0) == 0,
        "all_rows_assigned_once": split_audit.get("all_rows_assigned_once") is True,
        "train_validation_test_nonempty": min(len(train), len(validation), len(test)) > 0,
        "full_frequency_sequence_used": sequence_x.shape[1] == int(args.expected_frequency_points),
        "train_only_resonance_challenge_nonempty": len(resonance_indices) > 0,
        "equal_optimizer_updates_per_seed": equal_updates,
        "parameter_budget_ratio_within_limit": parameter_ratio <= float(args.max_parameter_count_ratio),
        "finite_validation_metrics": _all_finite(seed_results, "validation"),
        "finite_test_metrics": _all_finite(seed_results, "test"),
        "physical_metric_extraction_available": all(
            item["test"][arm]["metrics"]["target_physical_valid_fraction"] > 0.99
            for item in seed_results
            for arm in ("mlp", "gru")
        ),
    }
    return {
        "checks": checks,
        "split_audit": split_audit,
        "architecture": {
            "input": "10 normalized geometry variables plus normalized frequency at each of 111 sequence steps",
            "output": f"{output_dim} real values for the reciprocal S upper triangle per frequency",
            "mlp": {
                "kind": "frequency-conditioned pointwise MLP",
                "hidden_depth": int(args.mlp_hidden_depth),
                "hidden_width": mlp_width,
                "activation": "GELU",
                "parameter_count": mlp_parameter_count,
            },
            "gru": {
                "kind": "unidirectional GRU with a linear per-frequency head",
                "hidden_width": gru_width,
                "parameter_count": gru_parameter_count,
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
            "validation_only_checkpoint_selection": True,
            "test_set_used_for_selection": False,
            "only_model_difference": "pointwise feed-forward hidden state versus recurrent GRU hidden state",
        },
        "resonance_challenge_definition": {
            "source": "training rows only",
            "score": "mean absolute adjacent-frequency change over the full reciprocal S matrix",
            "quantile": float(args.resonance_variation_quantile),
            "frequency_indices": [int(value) for value in resonance_indices],
            "frequencies_ghz": [float(frequencies[value] / 1.0e9) for value in resonance_indices],
            "boundary": "This is a response-variation challenge set, not proof that every selected point is a physical resonance.",
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
        mlp_width,
        np.random.default_rng(_seed(seed, "mlp-init")),
    )
    gru = _init_gru(input_dim, int(args.gru_hidden_width), output_dim, np.random.default_rng(_seed(seed, "gru-init")))
    mlp_state = pointwise._init_adam(mlp)
    gru_state = _init_flat_adam(gru)
    order_rng = np.random.default_rng(_seed(seed, "shared-batches"))
    mlp_updates = 0
    gru_updates = 0
    best_mlp = pointwise._copy_model(mlp)
    best_gru = _copy_gru(gru)
    best_mlp_epoch = 0
    best_gru_epoch = 0
    best_mlp_validation = math.inf
    best_gru_validation = math.inf
    history_rows: list[dict[str, Any]] = []
    mlp_train_seconds = 0.0
    gru_train_seconds = 0.0

    for epoch in range(1, int(args.epochs) + 1):
        order = order_rng.permutation(train)
        for start in range(0, len(order), int(args.batch_size)):
            indices = order[start : start + int(args.batch_size)]
            batch_x = sequence_x[indices]
            batch_y = output_normalized[indices]
            pair_x = batch_x.reshape(-1, input_dim)
            pair_y = batch_y.reshape(-1, output_dim)

            started = time.perf_counter()
            grad_weights, grad_biases = pointwise._gradients(
                pair_x, pair_y, mlp, float(args.weight_decay)
            )
            _clip_list_gradients(grad_weights + grad_biases, float(args.gradient_clip))
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
            gru_gradients = _gru_gradients(
                batch_x,
                batch_y,
                gru,
                float(args.weight_decay),
                float(args.gradient_clip),
            )
            _adam_flat_step(gru, gru_gradients, gru_state, float(args.learning_rate))
            gru_train_seconds += time.perf_counter() - started
            gru_updates += 1

        validation_mlp = _evaluate_model(
            "mlp", mlp, validation, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
        )
        validation_gru = _evaluate_model(
            "gru", gru, validation, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
        )
        mlp_score = float(validation_mlp["metrics"]["raw_complex_rmse"])
        gru_score = float(validation_gru["metrics"]["raw_complex_rmse"])
        if mlp_score < best_mlp_validation:
            best_mlp_validation = mlp_score
            best_mlp = pointwise._copy_model(mlp)
            best_mlp_epoch = epoch
        if gru_score < best_gru_validation:
            best_gru_validation = gru_score
            best_gru = _copy_gru(gru)
            best_gru_epoch = epoch
        history_rows.append(
            {
                "seed": seed,
                "epoch": epoch,
                "mlp_validation_raw_complex_rmse": mlp_score,
                "gru_validation_raw_complex_rmse": gru_score,
                "mlp_updates": mlp_updates,
                "gru_updates": gru_updates,
            }
        )

    validation_mlp = _evaluate_model(
        "mlp", best_mlp, validation, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
    )
    validation_gru = _evaluate_model(
        "gru", best_gru, validation, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
    )
    started = time.perf_counter()
    test_mlp = _evaluate_model(
        "mlp", best_mlp, test, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
    )
    mlp_inference_seconds = time.perf_counter() - started
    started = time.perf_counter()
    test_gru = _evaluate_model(
        "gru", best_gru, test, sequence_x, output_mean, output_scale, truth, frequencies, resonance_indices, args
    )
    gru_inference_seconds = time.perf_counter() - started
    return {
        "seed": seed,
        "mlp_updates": mlp_updates,
        "gru_updates": gru_updates,
        "mlp_best_epoch": best_mlp_epoch,
        "gru_best_epoch": best_gru_epoch,
        "mlp_train_seconds": mlp_train_seconds,
        "gru_train_seconds": gru_train_seconds,
        "mlp_inference_seconds": mlp_inference_seconds,
        "gru_inference_seconds": gru_inference_seconds,
        "validation": {"mlp": validation_mlp, "gru": validation_gru},
        "test": {"mlp": test_mlp, "gru": test_gru},
        "history_rows": history_rows,
        "mlp_model": best_mlp,
        "gru_model": best_gru,
    }


def _sequence_inputs(geometry: np.ndarray, frequency: np.ndarray) -> np.ndarray:
    repeated_geometry = np.repeat(geometry[:, None, :], len(frequency), axis=1)
    repeated_frequency = np.broadcast_to(frequency[None, :, None], (len(geometry), len(frequency), 1))
    return np.concatenate((repeated_geometry, repeated_frequency), axis=2)


def _resonance_challenge_indices(matrix: np.ndarray, quantile: float) -> tuple[np.ndarray, np.ndarray]:
    adjacent = np.mean(np.abs(np.diff(matrix, axis=1)), axis=(0, 2, 3))
    scores = np.empty(matrix.shape[1], dtype=float)
    scores[0] = adjacent[0]
    scores[-1] = adjacent[-1]
    if len(scores) > 2:
        scores[1:-1] = np.maximum(adjacent[:-1], adjacent[1:])
    threshold = float(np.quantile(scores, quantile))
    indices = np.flatnonzero(scores >= threshold)
    if not len(indices):
        indices = np.asarray([int(np.argmax(scores))], dtype=int)
    return indices.astype(int), scores


def _evaluate_model(
    kind: str,
    model: dict[str, Any],
    rows: np.ndarray,
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
    if kind == "mlp":
        normalized = pointwise._predict(model, x.reshape(-1, x.shape[2])).reshape(
            len(indices), x.shape[1], -1
        )
    elif kind == "gru":
        normalized = _gru_predict(model, x, batch_size=max(1, int(args.batch_size)))
    else:
        raise ValueError(f"unknown model kind {kind}")
    components = normalized * output_scale[None, None, :] + output_mean[None, None, :]
    prediction = _components_to_matrix(components, int(args.expected_ports))
    target = truth[indices]
    return _matrix_metrics(prediction, target, frequencies, resonance_indices, args)


def _components_to_matrix(components: np.ndarray, ports: int) -> np.ndarray:
    upper_rows, upper_columns = np.triu_indices(ports)
    upper_count = len(upper_rows)
    upper = components[:, :, :upper_count] + 1j * components[:, :, upper_count:]
    matrix = np.zeros((len(components), components.shape[1], ports, ports), dtype=np.complex128)
    matrix[:, :, upper_rows, upper_columns] = upper
    matrix[:, :, upper_columns, upper_rows] = upper
    return matrix


def _matrix_metrics(
    prediction: np.ndarray,
    target: np.ndarray,
    frequencies: np.ndarray,
    resonance_indices: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    error = prediction - target
    projected, correction = pointwise._passivity_project_vectorized(prediction)
    projected_error = projected - target
    raw_singular = np.linalg.svd(prediction, compute_uv=False)
    projected_singular = np.linalg.svd(projected, compute_uv=False)
    requested_hz = float(args.target_frequency_ghz) * 1.0e9
    target_index = int(np.argmin(np.abs(frequencies - requested_hz)))
    physical = _target_physical_metrics(prediction, target, frequencies, target_index)
    frequency_rows = []
    for index, frequency in enumerate(frequencies):
        frequency_rows.append(
            {
                "frequency_ghz": float(frequency / 1.0e9),
                "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error[:, index]) ** 2))),
                "raw_complex_mae": float(np.mean(np.abs(error[:, index]))),
                "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular[:, index]) - 1.0)),
            }
        )
    frequency_rmse = np.asarray([row["raw_complex_rmse"] for row in frequency_rows], dtype=float)
    metrics = {
        "row_count": int(len(prediction)),
        "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error) ** 2))),
        "raw_complex_mae": float(np.mean(np.abs(error))),
        "projected_complex_rmse": float(np.sqrt(np.mean(np.abs(projected_error) ** 2))),
        "frequency_rmse_p95": float(np.quantile(frequency_rmse, 0.95)),
        "target_frequency_ghz": float(frequencies[target_index] / 1.0e9),
        "target_raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error[:, target_index]) ** 2))),
        "target_raw_complex_mae": float(np.mean(np.abs(error[:, target_index]))),
        "resonance_raw_complex_rmse": float(
            np.sqrt(np.mean(np.abs(error[:, np.asarray(resonance_indices, dtype=int)]) ** 2))
        ),
        "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular) - 1.0)),
        "projected_max_passivity_excess": float(max(0.0, np.max(projected_singular) - 1.0)),
        "passivity_projection_complex_rmse": float(np.sqrt(np.mean(np.abs(correction) ** 2))),
        "reciprocity_error": float(np.max(np.abs(prediction - np.swapaxes(prediction, 2, 3)))),
        **physical,
    }
    return {"metrics": metrics, "frequency_rows": frequency_rows}


def _target_physical_metrics(
    prediction: np.ndarray,
    target: np.ndarray,
    frequencies: np.ndarray,
    target_index: int,
) -> dict[str, float]:
    predicted_rows = []
    target_rows = []
    pairs = parse_port_pairs("1,2:3,4")
    for predicted_matrix, target_matrix in zip(prediction, target):
        try:
            predicted_z = multiport_s_to_grounded_differential_z(predicted_matrix, 50.0, pairs)
            target_z = multiport_s_to_grounded_differential_z(target_matrix, 50.0, pairs)
            predicted_curves = response._response_curves(predicted_z, frequencies, load_ohm=50.0)
            target_curves = response._response_curves(target_z, frequencies, load_ohm=50.0)
            predicted_rows.append(_physical_row(predicted_curves, target_index))
            target_rows.append(_physical_row(target_curves, target_index))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            predicted_rows.append([math.nan] * 4)
            target_rows.append([math.nan] * 4)
    predicted_values = np.asarray(predicted_rows, dtype=float)
    target_values = np.asarray(target_rows, dtype=float)
    finite = np.all(np.isfinite(predicted_values), axis=1) & np.all(np.isfinite(target_values), axis=1)
    result: dict[str, float] = {"target_physical_valid_fraction": float(np.mean(finite)) if len(finite) else 0.0}
    names = ("lp_nh", "ls_nh", "q", "k_abs")
    if not np.any(finite):
        for name in names:
            result[f"target_{name}_mae"] = math.nan
            result[f"target_{name}_rmse"] = math.nan
            result[f"target_{name}_relative_mae"] = math.nan
        return result
    difference = predicted_values[finite] - target_values[finite]
    for column, name in enumerate(names):
        values = difference[:, column]
        truth = target_values[finite, column]
        result[f"target_{name}_mae"] = float(np.mean(np.abs(values)))
        result[f"target_{name}_rmse"] = float(np.sqrt(np.mean(values**2)))
        result[f"target_{name}_relative_mae"] = float(
            np.mean(np.abs(values) / np.maximum(np.abs(truth), 1.0e-9))
        )
    return result


def _physical_row(curves: dict[str, np.ndarray], index: int) -> list[float]:
    qp = float(curves["qp"][index])
    qs = float(curves["qs"][index])
    return [
        float(curves["lp_nh"][index]),
        float(curves["ls_nh"][index]),
        min(qp, qs),
        abs(float(curves["k"][index])),
    ]


def _init_gru(
    input_dim: int,
    hidden_dim: int,
    output_dim: int,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    model: dict[str, np.ndarray] = {}
    for gate in ("z", "r", "h"):
        model[f"W{gate}"] = rng.normal(0.0, 1.0 / math.sqrt(input_dim), size=(input_dim, hidden_dim))
        model[f"U{gate}"] = rng.normal(0.0, 1.0 / math.sqrt(hidden_dim), size=(hidden_dim, hidden_dim))
        model[f"b{gate}"] = np.zeros(hidden_dim, dtype=float)
    model["Wo"] = rng.normal(0.0, 1.0 / math.sqrt(hidden_dim), size=(hidden_dim, output_dim))
    model["bo"] = np.zeros(output_dim, dtype=float)
    return model


def _copy_gru(model: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {name: np.array(value, copy=True) for name, value in model.items()}


def _gru_forward(
    model: dict[str, np.ndarray],
    x: np.ndarray,
    *,
    return_cache: bool,
) -> tuple[np.ndarray, list[tuple[np.ndarray, ...]] | None]:
    batch, steps, _ = x.shape
    hidden = np.zeros((batch, model["bz"].shape[0]), dtype=float)
    output = np.empty((batch, steps, model["bo"].shape[0]), dtype=float)
    cache: list[tuple[np.ndarray, ...]] = []
    for step in range(steps):
        value = x[:, step]
        previous = hidden
        update = _sigmoid(value @ model["Wz"] + previous @ model["Uz"] + model["bz"])
        reset = _sigmoid(value @ model["Wr"] + previous @ model["Ur"] + model["br"])
        candidate = np.tanh(value @ model["Wh"] + (reset * previous) @ model["Uh"] + model["bh"])
        hidden = (1.0 - update) * candidate + update * previous
        output[:, step] = hidden @ model["Wo"] + model["bo"]
        if return_cache:
            cache.append((value, previous, update, reset, candidate, hidden))
    return output, cache if return_cache else None


def _gru_gradients(
    x: np.ndarray,
    y: np.ndarray,
    model: dict[str, np.ndarray],
    weight_decay: float,
    gradient_clip: float,
) -> dict[str, np.ndarray]:
    prediction, cache = _gru_forward(model, x, return_cache=True)
    assert cache is not None
    gradients = {name: np.zeros_like(value) for name, value in model.items()}
    hidden_gradient = np.zeros((len(x), model["bz"].shape[0]), dtype=float)
    scale = 2.0 / max(1, x.shape[0] * x.shape[1])
    for step in reversed(range(x.shape[1])):
        value, previous, update, reset, candidate, hidden = cache[step]
        output_gradient = scale * (prediction[:, step] - y[:, step])
        gradients["Wo"] += hidden.T @ output_gradient
        gradients["bo"] += np.sum(output_gradient, axis=0)
        hidden_total = output_gradient @ model["Wo"].T + hidden_gradient

        candidate_gradient = hidden_total * (1.0 - update)
        update_gradient = hidden_total * (previous - candidate)
        previous_gradient = hidden_total * update

        candidate_pre = candidate_gradient * (1.0 - candidate**2)
        gradients["Wh"] += value.T @ candidate_pre
        gradients["Uh"] += (reset * previous).T @ candidate_pre
        gradients["bh"] += np.sum(candidate_pre, axis=0)
        reset_previous_gradient = candidate_pre @ model["Uh"].T
        reset_gradient = reset_previous_gradient * previous
        previous_gradient += reset_previous_gradient * reset

        reset_pre = reset_gradient * reset * (1.0 - reset)
        gradients["Wr"] += value.T @ reset_pre
        gradients["Ur"] += previous.T @ reset_pre
        gradients["br"] += np.sum(reset_pre, axis=0)
        previous_gradient += reset_pre @ model["Ur"].T

        update_pre = update_gradient * update * (1.0 - update)
        gradients["Wz"] += value.T @ update_pre
        gradients["Uz"] += previous.T @ update_pre
        gradients["bz"] += np.sum(update_pre, axis=0)
        previous_gradient += update_pre @ model["Uz"].T
        hidden_gradient = previous_gradient

    for name in ("Wz", "Uz", "Wr", "Ur", "Wh", "Uh", "Wo"):
        gradients[name] += weight_decay * model[name]
    _clip_list_gradients(list(gradients.values()), gradient_clip)
    return gradients


def _gru_predict(model: dict[str, np.ndarray], x: np.ndarray, batch_size: int) -> np.ndarray:
    outputs = []
    for start in range(0, len(x), batch_size):
        prediction, _ = _gru_forward(model, x[start : start + batch_size], return_cache=False)
        outputs.append(prediction)
    return np.concatenate(outputs, axis=0) if outputs else np.empty((0, x.shape[1], model["bo"].shape[0]))


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _init_flat_adam(model: dict[str, np.ndarray]) -> dict[str, Any]:
    return {
        "t": 0,
        "m": {name: np.zeros_like(value) for name, value in model.items()},
        "v": {name: np.zeros_like(value) for name, value in model.items()},
    }


def _adam_flat_step(
    model: dict[str, np.ndarray],
    gradients: dict[str, np.ndarray],
    state: dict[str, Any],
    learning_rate: float,
) -> None:
    beta1, beta2, epsilon = 0.9, 0.999, 1.0e-8
    state["t"] += 1
    step = int(state["t"])
    for name in model:
        state["m"][name] = beta1 * state["m"][name] + (1.0 - beta1) * gradients[name]
        state["v"][name] = beta2 * state["v"][name] + (1.0 - beta2) * gradients[name] ** 2
        first = state["m"][name] / (1.0 - beta1**step)
        second = state["v"][name] / (1.0 - beta2**step)
        model[name] -= learning_rate * first / (np.sqrt(second) + epsilon)


def _clip_list_gradients(gradients: list[np.ndarray], maximum: float) -> None:
    norm = math.sqrt(sum(float(np.sum(value**2)) for value in gradients))
    if norm > maximum:
        scale = maximum / max(norm, 1.0e-12)
        for value in gradients:
            value *= scale


def _gru_parameter_count(input_dim: int, hidden_dim: int, output_dim: int) -> int:
    return 3 * (input_dim * hidden_dim + hidden_dim * hidden_dim + hidden_dim) + hidden_dim * output_dim + output_dim


def _mlp_parameter_count(input_dim: int, output_dim: int, depth: int, width: int) -> int:
    sizes = [input_dim] + [width] * depth + [output_dim]
    return int(sum(fan_in * fan_out + fan_out for fan_in, fan_out in zip(sizes[:-1], sizes[1:])))


def _closest_mlp_width(input_dim: int, output_dim: int, depth: int, target: int) -> int:
    candidates = range(4, 1025)
    return min(candidates, key=lambda width: abs(_mlp_parameter_count(input_dim, output_dim, depth, width) - target))


def _aggregate_seed_results(
    seed_results: list[dict[str, Any]],
    frequencies: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"validation": {}, "test": {}, "runtime": {}}
    for split_name in ("validation", "test"):
        for arm in ("mlp", "gru"):
            rows = [item[split_name][arm]["metrics"] for item in seed_results]
            metrics[split_name][arm] = _aggregate_numeric_dicts(rows)
    for arm in ("mlp", "gru"):
        train_values = np.asarray([item[f"{arm}_train_seconds"] for item in seed_results], dtype=float)
        inference_values = np.asarray([item[f"{arm}_inference_seconds"] for item in seed_results], dtype=float)
        metrics["runtime"][arm] = {
            "train_seconds_mean": float(np.mean(train_values)),
            "train_seconds_std": float(np.std(train_values)),
            "test_inference_seconds_mean": float(np.mean(inference_values)),
            "test_inference_seconds_std": float(np.std(inference_values)),
        }
    frequency_rows = []
    mlp_frequency = np.asarray(
        [[row["raw_complex_rmse"] for row in item["test"]["mlp"]["frequency_rows"]] for item in seed_results]
    )
    gru_frequency = np.asarray(
        [[row["raw_complex_rmse"] for row in item["test"]["gru"]["frequency_rows"]] for item in seed_results]
    )
    for index, frequency in enumerate(frequencies):
        mlp_mean = float(np.mean(mlp_frequency[:, index]))
        gru_mean = float(np.mean(gru_frequency[:, index]))
        frequency_rows.append(
            {
                "frequency_ghz": float(frequency / 1.0e9),
                "mlp_raw_complex_rmse_mean": mlp_mean,
                "mlp_raw_complex_rmse_std": float(np.std(mlp_frequency[:, index])),
                "gru_raw_complex_rmse_mean": gru_mean,
                "gru_raw_complex_rmse_std": float(np.std(gru_frequency[:, index])),
                "gru_relative_improvement": _improvement(mlp_mean, gru_mean),
            }
        )
    test_mlp = metrics["test"]["mlp"]
    test_gru = metrics["test"]["gru"]
    validation_mlp = metrics["validation"]["mlp"]
    validation_gru = metrics["validation"]["gru"]
    regression = np.mean(gru_frequency, axis=0) > np.mean(mlp_frequency, axis=0) * (
        1.0 + float(args.frequency_regression_tolerance)
    )
    comparison = {
        "model_seed_count": len(seed_results),
        "validation_full_band_relative_improvement": _improvement(
            validation_mlp["raw_complex_rmse"]["mean"], validation_gru["raw_complex_rmse"]["mean"]
        ),
        "validation_target_relative_improvement": _improvement(
            validation_mlp["target_raw_complex_rmse"]["mean"], validation_gru["target_raw_complex_rmse"]["mean"]
        ),
        "test_full_band_relative_improvement": _improvement(
            test_mlp["raw_complex_rmse"]["mean"], test_gru["raw_complex_rmse"]["mean"]
        ),
        "test_target_relative_improvement": _improvement(
            test_mlp["target_raw_complex_rmse"]["mean"], test_gru["target_raw_complex_rmse"]["mean"]
        ),
        "test_resonance_relative_improvement": _improvement(
            test_mlp["resonance_raw_complex_rmse"]["mean"], test_gru["resonance_raw_complex_rmse"]["mean"]
        ),
        "test_frequency_regression_fraction": float(np.mean(regression)),
        "test_frequency_regression_count": int(np.sum(regression)),
        "test_passivity_projection_correction_increase": (
            test_gru["passivity_projection_complex_rmse"]["mean"]
            - test_mlp["passivity_projection_complex_rmse"]["mean"]
        ),
        "test_target_lp_nh_mae_relative_improvement": _improvement(
            test_mlp["target_lp_nh_mae"]["mean"], test_gru["target_lp_nh_mae"]["mean"]
        ),
        "test_target_ls_nh_mae_relative_improvement": _improvement(
            test_mlp["target_ls_nh_mae"]["mean"], test_gru["target_ls_nh_mae"]["mean"]
        ),
        "test_target_q_mae_relative_improvement": _improvement(
            test_mlp["target_q_mae"]["mean"], test_gru["target_q_mae"]["mean"]
        ),
        "test_target_k_abs_mae_relative_improvement": _improvement(
            test_mlp["target_k_abs_mae"]["mean"], test_gru["target_k_abs_mae"]["mean"]
        ),
        "candidate_test_raw_complex_rmse": test_gru["raw_complex_rmse"]["mean"],
        "candidate_test_raw_max_passivity_excess": test_gru["raw_max_passivity_excess"]["mean"],
        "minimum_material_improvement": float(args.minimum_material_improvement),
        "max_frequency_regression_fraction": float(args.max_frequency_regression_fraction),
        "max_passivity_correction_increase": float(args.max_passivity_correction_increase),
        "max_candidate_test_complex_rmse": float(args.max_candidate_test_complex_rmse),
        "max_candidate_raw_passivity_excess": float(args.max_candidate_raw_passivity_excess),
    }
    return {"metrics": metrics, "comparison": comparison, "frequency_rows": frequency_rows}


def _aggregate_numeric_dicts(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for key in rows[0]:
        values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float)) and math.isfinite(float(row[key]))]
        if values:
            array = np.asarray(values, dtype=float)
            result[key] = {
                "mean": float(np.mean(array)),
                "std": float(np.std(array)),
                "min": float(np.min(array)),
                "max": float(np.max(array)),
            }
    return result


def _seed_metric_rows(seed: int, result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for split_name in ("validation", "test"):
        for arm in ("mlp", "gru"):
            rows.append(
                {
                    "seed": seed,
                    "split": split_name,
                    "arm": arm,
                    "best_epoch": result[f"{arm}_best_epoch"],
                    "optimizer_updates": result[f"{arm}_updates"],
                    "train_seconds": result[f"{arm}_train_seconds"],
                    "test_inference_seconds": result[f"{arm}_inference_seconds"] if split_name == "test" else "",
                    **result[split_name][arm]["metrics"],
                }
            )
    return rows


def _decision(comparison: dict[str, Any], args: argparse.Namespace) -> str:
    threshold = float(args.minimum_material_improvement)
    improvements = (
        float(comparison["validation_full_band_relative_improvement"]),
        float(comparison["validation_target_relative_improvement"]),
        float(comparison["test_full_band_relative_improvement"]),
        float(comparison["test_target_relative_improvement"]),
        float(comparison["test_resonance_relative_improvement"]),
    )
    physical_improvements = (
        float(comparison["test_target_lp_nh_mae_relative_improvement"]),
        float(comparison["test_target_ls_nh_mae_relative_improvement"]),
        float(comparison["test_target_q_mae_relative_improvement"]),
        float(comparison["test_target_k_abs_mae_relative_improvement"]),
    )
    regression_ok = float(comparison["test_frequency_regression_fraction"]) <= float(
        args.max_frequency_regression_fraction
    )
    passivity_ok = float(comparison["test_passivity_projection_correction_increase"]) <= float(
        args.max_passivity_correction_increase
    )
    absolute_quality_ok = (
        float(comparison["candidate_test_raw_complex_rmse"])
        <= float(args.max_candidate_test_complex_rmse)
        and float(comparison["candidate_test_raw_max_passivity_excess"])
        <= float(args.max_candidate_raw_passivity_excess)
    )
    if (
        all(value >= threshold for value in improvements)
        and all(value >= threshold for value in physical_improvements)
        and regression_ok
        and passivity_ok
        and absolute_quality_ok
    ):
        return "REVIEW_GRU_FOR_FROZEN_FORWARD_INVERSE_ABLATION"
    if all(value <= -threshold for value in improvements) and all(
        value <= -threshold for value in physical_improvements
    ):
        return "RETAIN_POINTWISE_MLP_GRU_IS_MATERIALLY_WORSE"
    return "RETAIN_POINTWISE_MLP_MIXED_SEQUENCE_EVIDENCE"


def _all_finite(seed_results: list[dict[str, Any]], split_name: str) -> bool:
    for item in seed_results:
        for arm in ("mlp", "gru"):
            for value in item[split_name][arm]["metrics"].values():
                if isinstance(value, (int, float)) and not math.isfinite(float(value)):
                    return False
    return True


def _model_arrays(arm: str, model: dict[str, Any]) -> dict[str, np.ndarray]:
    if arm == "gru":
        return {f"gru_{name}": value for name, value in model.items()}
    arrays = {}
    for index, value in enumerate(model["weights"]):
        arrays[f"mlp_weight_{index}"] = value
    for index, value in enumerate(model["biases"]):
        arrays[f"mlp_bias_{index}"] = value
    return arrays


def _parse_seeds(raw: str | list[int]) -> list[int]:
    if isinstance(raw, list):
        return [int(value) for value in raw]
    return [int(item.strip()) for item in str(raw).split(",") if item.strip()]


def _seed(seed: int, *parts: object) -> int:
    digest = hashlib.sha256("|".join([str(seed), *[str(part) for part in parts]]).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % (2**32 - 1)


def _improvement(baseline: float, candidate: float) -> float:
    return (float(baseline) - float(candidate)) / max(abs(float(baseline)), 1.0e-12)


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
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
        "training_manifest_sha256": _sha256(training_manifest),
        "training_count": int(dataset.get("count") or 0),
        "geometry_columns": geometry_columns,
        "split_reference_columns": split_columns,
        "row_identity_sha256": str(dataset.get("row_identity_sha256") or ""),
        "touchstone_content_sha256": str(dataset.get("touchstone_content_sha256") or ""),
        "reciprocal_training_content_sha256": str(dataset.get("reciprocal_training_content_sha256") or ""),
        "frequency_grid_sha256": str(dataset.get("frequency_grid_sha256") or ""),
        "rejected_row_count": len(rejects),
        "rejected_rows": rejects[:100],
        "arguments": vars(args),
    }


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_plot(path: Path, rows: list[dict[str, Any]], target_ghz: float) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return "SKIPPED_MATPLOTLIB_UNAVAILABLE"
    frequency = np.asarray([row["frequency_ghz"] for row in rows], dtype=float)
    mlp = np.asarray([row["mlp_raw_complex_rmse_mean"] for row in rows], dtype=float)
    gru = np.asarray([row["gru_raw_complex_rmse_mean"] for row in rows], dtype=float)
    improvement = np.asarray([row["gru_relative_improvement"] for row in rows], dtype=float)
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), constrained_layout=True)
    axes[0].plot(frequency, mlp, label="Pointwise MLP", color="#155fa0", linewidth=2)
    axes[0].plot(frequency, gru, label="GRU", color="#c43c2b", linewidth=2)
    axes[0].axvline(target_ghz, color="#333333", linestyle=":", linewidth=1)
    axes[0].set_xlabel("Frequency (GHz)")
    axes[0].set_ylabel("Test complex-S RMSE")
    axes[0].grid(alpha=0.25)
    axes[0].legend()
    axes[1].plot(frequency, 100.0 * improvement, color="#167a68", linewidth=2)
    axes[1].axhline(0.0, color="#333333", linewidth=1)
    axes[1].axvline(target_ghz, color="#333333", linestyle=":", linewidth=1)
    axes[1].set_xlabel("Frequency (GHz)")
    axes[1].set_ylabel("GRU relative improvement (%)")
    axes[1].grid(alpha=0.25)
    figure.savefig(path, dpi=180)
    plt.close(figure)
    return "PASS"


def _render_report(payload: dict[str, Any]) -> str:
    comparison = payload.get("comparison") or {}
    architecture = payload.get("architecture") or {}
    return "\n".join(
        [
            "# Frequency-Sequence Architecture Benchmark",
            "",
            f"- Overall status: **{payload.get('overall_status')}**",
            f"- Decision: **{payload.get('decision')}**",
            f"- Real S4P rows: `{payload.get('training_count')}`",
            f"- Model seeds: `{comparison.get('model_seed_count')}`",
            f"- MLP parameters: `{(architecture.get('mlp') or {}).get('parameter_count')}`",
            f"- GRU parameters: `{(architecture.get('gru') or {}).get('parameter_count')}`",
            f"- Test full-band relative improvement: `{comparison.get('test_full_band_relative_improvement')}`",
            f"- Test 15 GHz relative improvement: `{comparison.get('test_target_relative_improvement')}`",
            f"- Test resonance-band relative improvement: `{comparison.get('test_resonance_relative_improvement')}`",
            f"- Frequency regression fraction: `{comparison.get('test_frequency_regression_fraction')}`",
            "",
            "## Scientific boundary",
            "",
            str(payload.get("scientific_boundary") or ""),
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
