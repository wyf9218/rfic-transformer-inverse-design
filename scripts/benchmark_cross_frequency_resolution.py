#!/usr/bin/env python3
"""Benchmark sparse-frequency training against a full-grid reference.

Both arms use the same real Touchstone rows, physical-cell OOD split,
coordinate-conditioned architecture, initial weights, physical-row batches,
batch size, and optimizer-update count. The full-grid arm can sample every
frequency, while the sparse arm can sample only an interleaved subset. Both
are evaluated on the complete grid, including frequencies never shown to the
sparse arm.

This is a cross-resolution baseline. It is not presented as a neural operator
and cannot replace DRC, real EMX closure, or sampled HFSS correlation.
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
REPO_ROOT = SCRIPT_DIR.parent
for candidate in (SCRIPT_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import benchmark_frequency_domain_self_transfer as transfer  # noqa: E402
import train_broadband_sparameter_pca_surrogate as broadband  # noqa: E402
from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    training_manifest = Path(args.training_manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "cross_frequency_resolution_summary.json"
    frequency_csv = out_dir / "cross_frequency_resolution_frequency_errors.csv"
    history_csv = out_dir / "cross_frequency_resolution_history.csv"
    plot_path = out_dir / "cross_frequency_resolution_frequency_errors.png"
    weights_path = out_dir / "cross_frequency_resolution_weights.npz"
    report_path = out_dir / "cross_frequency_resolution_report.md"

    manifest = _read_json(training_manifest)
    rows = broadband._read_rows(training_csv)
    predictor_columns = broadband._geometry_columns(rows, args.geometry_columns)
    split_columns = broadband._split_columns(args.split_reference_columns)
    selected_rows = broadband._deterministic_spread_sample(rows, int(args.max_rows))
    dataset, rejects = broadband._load_dataset(
        selected_rows,
        predictor_columns,
        split_columns,
        args,
    )
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "training_manifest_exists": training_manifest.is_file(),
        "training_manifest_pass": manifest.get("overall_status") == "PASS",
        "training_manifest_matches_csv": _same_path(manifest.get("training_csv"), training_csv),
        "predictor_count_is_ten": len(predictor_columns) == 10,
        "split_reference_count_is_four": len(split_columns) == 4,
        "usable_rows_meet_minimum": int(dataset.get("count") or 0) >= int(args.min_rows),
        "all_selected_rows_loaded": int(dataset.get("count") or 0) == len(selected_rows),
        "frequency_grid_consistent": bool(dataset.get("frequency_grid_consistent")),
        "touchstone_content_hash_present": bool(dataset.get("touchstone_content_sha256")),
    }
    input_s4p_quality = _input_s4p_quality(dataset, args)
    checks.update(
        {
            "raw_input_s4p_quality_present": input_s4p_quality["row_count"]
            == int(dataset.get("count") or 0)
            and input_s4p_quality["row_count"] > 0,
            "raw_input_reciprocity_threshold_pass": input_s4p_quality["reciprocity"][
                "hard_threshold_pass"
            ],
            "raw_input_passivity_threshold_pass": input_s4p_quality["passivity"][
                "hard_threshold_pass"
            ],
        }
    )
    if not all(checks.values()):
        status = (
            "WAITING_FOR_COMPLETE_BROADBAND_DATA"
            if not checks["usable_rows_meet_minimum"]
            else "FAIL"
        )
        payload = _base_summary(
            args,
            training_csv,
            training_manifest,
            out_dir,
            predictor_columns,
            split_columns,
            dataset,
            rejects,
        )
        payload.update(
            {
                "overall_status": status,
                "decision": (
                    "WAIT_FOR_REAL_S4P_ROWS"
                    if status == "WAITING_FOR_COMPLETE_BROADBAND_DATA"
                    else "FIX_CROSS_FREQUENCY_RESOLUTION_CONTRACT"
                ),
                "checks": checks,
                "input_s4p_quality": input_s4p_quality,
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

    result = _run_benchmark(dataset, args)
    checks.update(result["checks"])
    comparable = all(checks.values())
    comparison = result["comparison"]
    decision = (
        _decision(comparison, args)
        if comparable
        else "FIX_CROSS_FREQUENCY_RESOLUTION_CONTRACT"
    )
    status = "COMPLETE_REVIEW_REQUIRED" if comparable else "FAIL"

    _write_csv(frequency_csv, result["frequency_rows"])
    _write_csv(history_csv, result["history_rows"])
    plot_status = _write_plot(
        plot_path,
        result["frequency_rows"],
        result["frequency_partition"]["held_out_indices"],
        float(args.target_frequency_ghz),
    )
    np.savez_compressed(weights_path, **result["weights"])

    payload = _base_summary(
        args,
        training_csv,
        training_manifest,
        out_dir,
        predictor_columns,
        split_columns,
        dataset,
        rejects,
    )
    payload.update(
        {
            "overall_status": status,
            "decision": decision,
            "checks": checks,
            "input_s4p_quality": input_s4p_quality,
            "split_audit": result["split_audit"],
            "frequency_partition": result["frequency_partition"],
            "architecture": result["architecture"],
            "equal_budget_contract": result["equal_budget_contract"],
            "metrics": result["metrics"],
            "comparison": comparison,
            "artifacts": {
                "summary": str(summary_path),
                "frequency_errors": str(frequency_csv),
                "history": str(history_csv),
                "plot": str(plot_path) if plot_status == "PASS" else "",
                "plot_status": plot_status,
                "weights": str(weights_path),
                "report": str(report_path),
            },
            "scientific_boundary": (
                "This is a same-real-S4P, same-physical-cell-split, equal-update cross-resolution "
                "ablation. The sparse arm is tested on frequency coordinates excluded from its "
                "training set. It is a coordinate-conditioned MLP baseline, not proof of a neural "
                "operator or discretization invariance. A favorable result does not change the "
                "production inverse model, 0.5 GHz EMX grid, or queue; adoption still requires "
                "predeclared inverse retraining, DRC, real EMX closure, and sampled HFSS correlation."
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
        "test_held_out_relative_degradation="
        f"{comparison['test_held_out_relative_degradation']}"
    )
    print(f"summary={summary_path}")
    return 0 if status == "COMPLETE_REVIEW_REQUIRED" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--training-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--geometry-columns")
    parser.add_argument(
        "--split-reference-columns",
        default=broadband.DEFAULT_INPUT_COLUMNS,
    )
    parser.add_argument("--min-rows", type=int, default=5000)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--sparse-frequency-stride", type=int, default=2)
    parser.add_argument("--optimizer-updates", type=int, default=512)
    parser.add_argument("--hidden-depth", type=int, default=2)
    parser.add_argument("--hidden-width", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--split-seed", type=int, default=20260711)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--max-input-reciprocity-error", type=float, default=0.02)
    parser.add_argument("--max-input-passivity-excess", type=float, default=0.05)
    parser.add_argument("--maximum-held-out-relative-degradation", type=float, default=0.15)
    parser.add_argument("--max-held-out-regression-fraction", type=float, default=0.25)
    parser.add_argument("--frequency-regression-tolerance", type=float, default=0.05)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 10 <= args.min_rows <= args.max_rows:
        parser.error("require 10 <= --min-rows <= --max-rows")
    if not 2 <= args.sparse_frequency_stride < args.expected_frequency_points:
        parser.error("--sparse-frequency-stride must leave observed and held-out points")
    if args.optimizer_updates < 1:
        parser.error("--optimizer-updates must be positive")
    if args.hidden_depth < 1 or args.hidden_width < 4 or args.batch_size < 1:
        parser.error("invalid MLP architecture or batch size")
    if args.learning_rate <= 0.0 or args.weight_decay < 0.0:
        parser.error("learning rate must be positive and weight decay nonnegative")
    if args.physical_cell_bins < 2:
        parser.error("--physical-cell-bins must be at least two")
    if not 0.0 <= args.maximum_held_out_relative_degradation:
        parser.error("--maximum-held-out-relative-degradation must be nonnegative")
    if not 0.0 <= args.max_held_out_regression_fraction <= 1.0:
        parser.error("--max-held-out-regression-fraction must be in [0,1]")
    if args.frequency_regression_tolerance < 0.0:
        parser.error("--frequency-regression-tolerance must be nonnegative")
    if args.max_input_reciprocity_error < 0.0 or args.max_input_passivity_excess < 0.0:
        parser.error("raw input S4P thresholds must be nonnegative")
    _validate_target_frequency(args)
    return args


def _validate_target_frequency(args: argparse.Namespace) -> None:
    start = float(args.expected_frequency_start_ghz)
    stop = float(args.expected_frequency_stop_ghz)
    step = float(args.expected_frequency_step_ghz)
    target = float(args.target_frequency_ghz)
    if not start <= target <= stop:
        raise SystemExit("target frequency lies outside the expected grid")
    nearest = start + round((target - start) / step) * step
    if abs(target - nearest) * 1.0e9 > float(args.frequency_tolerance_hz):
        raise SystemExit("target frequency does not align with the expected grid")


def _run_benchmark(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    geometry = np.asarray(dataset["geometry"], dtype=float)
    split_x = np.asarray(dataset["split_x"], dtype=float)
    frequencies = np.asarray(dataset["frequencies_hz"], dtype=float)
    ports = int(args.expected_ports)
    frequency_points = int(args.expected_frequency_points)
    matrices = broadband._decode_reciprocal_s(
        np.asarray(dataset["spectra"], dtype=float),
        frequency_points,
        ports,
    )
    upper_rows, upper_columns = np.triu_indices(ports)
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
    geometry_mean = np.mean(geometry[train], axis=0)
    geometry_scale = np.maximum(np.std(geometry[train], axis=0), 1.0e-12)
    geometry_normalized = (geometry - geometry_mean[None, :]) / geometry_scale[None, :]
    frequency_normalized = 2.0 * (frequencies - frequencies[0]) / max(
        frequencies[-1] - frequencies[0],
        1.0,
    ) - 1.0
    observed_indices, held_out_indices = _frequency_partition(
        frequency_points,
        int(args.sparse_frequency_stride),
    )

    shared_output_train = output[train][:, observed_indices, :].reshape(-1, output.shape[2])
    output_mean = np.mean(shared_output_train, axis=0)
    output_scale = np.maximum(np.std(shared_output_train, axis=0), 1.0e-6)
    output_normalized = (output - output_mean[None, None, :]) / output_scale[None, None, :]

    rng = np.random.default_rng(_seed(int(args.seed), "initial_weights"))
    initial_model = transfer._init_mlp(
        geometry.shape[1] + 1,
        output.shape[2],
        int(args.hidden_depth),
        int(args.hidden_width),
        rng,
    )
    dense_model = transfer._copy_model(initial_model)
    sparse_model = transfer._copy_model(initial_model)
    initial_weights_sha256 = _model_sha256(initial_model)
    dense_initial_weights_sha256 = _model_sha256(dense_model)
    sparse_initial_weights_sha256 = _model_sha256(sparse_model)
    same_initial_weights = (
        dense_initial_weights_sha256
        == sparse_initial_weights_sha256
        == initial_weights_sha256
    )

    schedule_rng = np.random.default_rng(_seed(int(args.seed), "paired_training_schedule"))
    update_count = int(args.optimizer_updates)
    batch_size = int(args.batch_size)
    physical_row_offsets = schedule_rng.integers(
        0,
        len(train),
        size=(update_count, batch_size),
        endpoint=False,
    )
    frequency_quantiles = schedule_rng.random((update_count, batch_size))
    dense_frequency_draws = np.minimum(
        (frequency_quantiles * frequency_points).astype(int),
        frequency_points - 1,
    )
    sparse_frequency_draws = observed_indices[
        np.minimum(
            (frequency_quantiles * len(observed_indices)).astype(int),
            len(observed_indices) - 1,
        )
    ]
    physical_rows = train[physical_row_offsets]
    dense_state = transfer._init_adam(dense_model)
    sparse_state = transfer._init_adam(sparse_model)
    history_rows: list[dict[str, Any]] = []
    history_interval = max(1, update_count // 10)
    for update_index in range(update_count):
        row_batch = physical_rows[update_index]
        dense_frequency_batch = dense_frequency_draws[update_index]
        sparse_frequency_batch = sparse_frequency_draws[update_index]
        dense_x, dense_y = _training_batch(
            geometry_normalized,
            frequency_normalized,
            output_normalized,
            row_batch,
            dense_frequency_batch,
        )
        sparse_x, sparse_y = _training_batch(
            geometry_normalized,
            frequency_normalized,
            output_normalized,
            row_batch,
            sparse_frequency_batch,
        )
        dense_gradients = transfer._gradients(
            dense_x,
            dense_y,
            dense_model,
            float(args.weight_decay),
        )
        sparse_gradients = transfer._gradients(
            sparse_x,
            sparse_y,
            sparse_model,
            float(args.weight_decay),
        )
        transfer._adam_step(
            dense_model,
            dense_gradients[0],
            dense_gradients[1],
            dense_state,
            float(args.learning_rate),
        )
        transfer._adam_step(
            sparse_model,
            sparse_gradients[0],
            sparse_gradients[1],
            sparse_state,
            float(args.learning_rate),
        )
        completed = update_index + 1
        if completed == 1 or completed == update_count or completed % history_interval == 0:
            validation_dense = _evaluate_model(
                dense_model,
                split["validation"],
                geometry_normalized,
                frequency_normalized,
                output_mean,
                output_scale,
                matrices,
                frequencies,
                observed_indices,
                held_out_indices,
                args,
            )
            validation_sparse = _evaluate_model(
                sparse_model,
                split["validation"],
                geometry_normalized,
                frequency_normalized,
                output_mean,
                output_scale,
                matrices,
                frequencies,
                observed_indices,
                held_out_indices,
                args,
            )
            history_rows.append(
                {
                    "optimizer_updates": completed,
                    "dense_validation_full_grid_complex_rmse": validation_dense["metrics"]["full_grid"]["raw_complex_rmse"],
                    "sparse_validation_full_grid_complex_rmse": validation_sparse["metrics"]["full_grid"]["raw_complex_rmse"],
                    "dense_validation_held_out_complex_rmse": validation_dense["metrics"]["held_out"]["raw_complex_rmse"],
                    "sparse_validation_held_out_complex_rmse": validation_sparse["metrics"]["held_out"]["raw_complex_rmse"],
                }
            )

    validation_dense = _evaluate_model(
        dense_model,
        split["validation"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        observed_indices,
        held_out_indices,
        args,
    )
    validation_sparse = _evaluate_model(
        sparse_model,
        split["validation"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        observed_indices,
        held_out_indices,
        args,
    )
    test_dense = _evaluate_model(
        dense_model,
        split["test"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        observed_indices,
        held_out_indices,
        args,
    )
    test_sparse = _evaluate_model(
        sparse_model,
        split["test"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        observed_indices,
        held_out_indices,
        args,
    )
    comparison = _compare(validation_dense, validation_sparse, test_dense, test_sparse, args)
    frequency_rows = _frequency_comparison_rows(
        test_dense["frequency_rows"],
        test_sparse["frequency_rows"],
        set(int(value) for value in observed_indices),
    )

    dense_updates = int(dense_state["t"])
    sparse_updates = int(sparse_state["t"])
    observed_set = set(int(value) for value in observed_indices)
    held_out_set = set(int(value) for value in held_out_indices)
    checks = {
        "physical_cell_ood_split": split_audit.get("split_mode") == "physical_cell_grouped",
        "physical_cell_overlap_zero": int(split_audit.get("physical_cell_overlap_count") or 0) == 0,
        "all_rows_assigned_once": split_audit.get("all_rows_assigned_once") is True,
        "frequency_partition_complete": observed_set | held_out_set == set(range(frequency_points)),
        "frequency_partition_disjoint": not bool(observed_set & held_out_set),
        "held_out_frequency_count_positive": len(held_out_indices) > 0,
        "sparse_training_excludes_all_held_out_frequencies": not bool(
            set(int(value) for value in np.unique(sparse_frequency_draws)) & held_out_set
        ),
        "dense_training_uses_at_least_one_held_out_frequency": bool(
            set(int(value) for value in np.unique(dense_frequency_draws)) & held_out_set
        ),
        "same_initial_weights": same_initial_weights,
        "same_parameter_count": transfer._parameter_count(dense_model)
        == transfer._parameter_count(sparse_model),
        "equal_optimizer_updates": dense_updates == sparse_updates == update_count,
        "same_physical_row_schedule": True,
        "same_frequency_quantile_schedule": True,
        "finite_validation_metrics": _finite_evaluations(validation_dense, validation_sparse),
        "finite_test_metrics": _finite_evaluations(test_dense, test_sparse),
    }
    weights = {
        "geometry_mean": geometry_mean,
        "geometry_scale": geometry_scale,
        "output_mean": output_mean,
        "output_scale": output_scale,
        "frequencies_hz": frequencies,
        "observed_frequency_indices": observed_indices,
        "held_out_frequency_indices": held_out_indices,
        "dense_optimizer_updates": np.asarray([dense_updates], dtype=np.int64),
        "sparse_optimizer_updates": np.asarray([sparse_updates], dtype=np.int64),
    }
    for arm, model in (("dense", dense_model), ("sparse", sparse_model)):
        for layer, weight in enumerate(model["weights"]):
            weights[f"{arm}_weight_{layer}"] = weight
        for layer, bias in enumerate(model["biases"]):
            weights[f"{arm}_bias_{layer}"] = bias
    return {
        "checks": checks,
        "split_audit": split_audit,
        "frequency_partition": {
            "full_grid_point_count": frequency_points,
            "sparse_frequency_stride": int(args.sparse_frequency_stride),
            "observed_point_count": int(len(observed_indices)),
            "held_out_point_count": int(len(held_out_indices)),
            "observed_indices": [int(value) for value in observed_indices],
            "held_out_indices": [int(value) for value in held_out_indices],
            "observed_frequencies_ghz": [float(frequencies[value] / 1.0e9) for value in observed_indices],
            "held_out_frequencies_ghz": [float(frequencies[value] / 1.0e9) for value in held_out_indices],
            "held_out_definition": "full-grid coordinates excluded from sparse-arm training",
        },
        "architecture": {
            "model_class": "coordinate-conditioned MLP baseline",
            "input": "10 normalized geometry variables plus continuous normalized frequency",
            "output": f"{output.shape[2]} real values for reciprocal S upper triangle at one frequency",
            "hidden_depth": int(args.hidden_depth),
            "hidden_width": int(args.hidden_width),
            "activation": "GELU",
            "parameter_count_per_arm": transfer._parameter_count(dense_model),
            "is_neural_operator": False,
        },
        "equal_budget_contract": {
            "same_real_touchstone_rows": True,
            "same_physical_cell_ood_split": True,
            "same_initial_weights": same_initial_weights,
            "initial_weights_sha256": initial_weights_sha256,
            "same_architecture": True,
            "same_parameter_count": True,
            "same_batch_size": True,
            "batch_size": batch_size,
            "same_physical_row_schedule": True,
            "physical_row_schedule_sha256": _array_sha256(physical_rows),
            "same_frequency_quantile_schedule": True,
            "frequency_quantile_schedule_sha256": _array_sha256(frequency_quantiles),
            "dense_optimizer_updates": dense_updates,
            "sparse_optimizer_updates": sparse_updates,
            "equal_optimizer_updates": dense_updates == sparse_updates,
            "only_arm_difference": (
                "dense arm may sample all grid frequencies; sparse arm maps the identical quantile "
                "schedule only onto the predeclared observed-frequency subset"
            ),
            "shared_output_normalization_source": "OOD-train rows at sparse observed frequencies only",
        },
        "metrics": {
            "validation": {
                "dense_full_grid_reference": validation_dense["metrics"],
                "interleaved_sparse_grid": validation_sparse["metrics"],
            },
            "test": {
                "dense_full_grid_reference": test_dense["metrics"],
                "interleaved_sparse_grid": test_sparse["metrics"],
            },
        },
        "comparison": comparison,
        "frequency_rows": frequency_rows,
        "history_rows": history_rows,
        "weights": weights,
    }


def _frequency_partition(point_count: int, stride: int) -> tuple[np.ndarray, np.ndarray]:
    observed = np.arange(0, int(point_count), int(stride), dtype=int)
    if not len(observed) or observed[-1] != int(point_count) - 1:
        observed = np.append(observed, int(point_count) - 1)
    observed = np.unique(observed)
    held_out = np.setdiff1d(np.arange(int(point_count), dtype=int), observed)
    return observed, held_out


def _training_batch(
    geometry: np.ndarray,
    frequency: np.ndarray,
    output: np.ndarray,
    row_indices: np.ndarray,
    frequency_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    rows = np.asarray(row_indices, dtype=int)
    frequencies = np.asarray(frequency_indices, dtype=int)
    x = np.column_stack((geometry[rows], frequency[frequencies]))
    y = output[rows, frequencies]
    return x, y


def _evaluate_model(
    model: dict[str, list[np.ndarray]],
    rows: np.ndarray,
    geometry: np.ndarray,
    frequency: np.ndarray,
    output_mean: np.ndarray,
    output_scale: np.ndarray,
    truth: np.ndarray,
    frequencies_hz: np.ndarray,
    observed_indices: np.ndarray,
    held_out_indices: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    row_indices = np.asarray(rows, dtype=int)
    frequency_indices = np.arange(len(frequency), dtype=int)
    pair_x = transfer._pair_inputs(geometry, frequency, row_indices, frequency_indices)
    predicted_components = transfer._predict(model, pair_x)
    predicted_components = (
        predicted_components * output_scale[None, :] + output_mean[None, :]
    ).reshape(len(row_indices), len(frequency), len(output_mean))
    ports = int(args.expected_ports)
    upper_rows, upper_columns = np.triu_indices(ports)
    upper_count = len(upper_rows)
    predicted_upper = (
        predicted_components[:, :, :upper_count]
        + 1j * predicted_components[:, :, upper_count:]
    )
    prediction = np.zeros(
        (len(row_indices), len(frequency), ports, ports),
        dtype=np.complex128,
    )
    prediction[:, :, upper_rows, upper_columns] = predicted_upper
    prediction[:, :, upper_columns, upper_rows] = predicted_upper
    target = truth[row_indices]
    error = prediction - target
    projected, correction = transfer._passivity_project_vectorized(prediction)
    projected_error = projected - target
    raw_singular = np.linalg.svd(prediction, compute_uv=False)
    projected_singular = np.linalg.svd(projected, compute_uv=False)
    target_requested_hz = float(args.target_frequency_ghz) * 1.0e9
    target_index = int(np.argmin(np.abs(frequencies_hz - target_requested_hz)))
    metrics = {
        "row_count": int(len(row_indices)),
        "full_grid": _error_metrics(error, np.arange(len(frequency), dtype=int)),
        "observed": _error_metrics(error, observed_indices),
        "held_out": _error_metrics(error, held_out_indices),
        "target_frequency_ghz": float(frequencies_hz[target_index] / 1.0e9),
        "target_frequency_was_observed_by_sparse_arm": bool(target_index in set(observed_indices)),
        "target_raw_complex_rmse": float(
            np.sqrt(np.mean(np.abs(error[:, target_index]) ** 2))
        ),
        "target_raw_complex_mae": float(np.mean(np.abs(error[:, target_index]))),
        "projected_full_grid_complex_rmse": float(
            np.sqrt(np.mean(np.abs(projected_error) ** 2))
        ),
        "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular) - 1.0)),
        "projected_max_passivity_excess": float(
            max(0.0, np.max(projected_singular) - 1.0)
        ),
        "passivity_projection_complex_rmse": float(
            np.sqrt(np.mean(np.abs(correction) ** 2))
        ),
        "reciprocity_error": float(
            np.max(np.abs(prediction - np.swapaxes(prediction, 2, 3)))
        ),
    }
    frequency_rows = []
    for frequency_index, frequency_hz in enumerate(frequencies_hz):
        frequency_rows.append(
            {
                "frequency_index": frequency_index,
                "frequency_ghz": float(frequency_hz / 1.0e9),
                "raw_complex_rmse": float(
                    np.sqrt(np.mean(np.abs(error[:, frequency_index]) ** 2))
                ),
                "raw_complex_mae": float(np.mean(np.abs(error[:, frequency_index]))),
            }
        )
    return {"metrics": metrics, "frequency_rows": frequency_rows}


def _error_metrics(error: np.ndarray, frequency_indices: np.ndarray) -> dict[str, Any]:
    selected = error[:, np.asarray(frequency_indices, dtype=int)]
    return {
        "frequency_point_count": int(len(frequency_indices)),
        "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(selected) ** 2))),
        "raw_complex_mae": float(np.mean(np.abs(selected))),
    }


def _compare(
    validation_dense: dict[str, Any],
    validation_sparse: dict[str, Any],
    test_dense: dict[str, Any],
    test_sparse: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    def degradation(reference: float, sparse: float) -> float:
        return (sparse - reference) / max(reference, 1.0e-12)

    validation_reference = validation_dense["metrics"]
    validation_candidate = validation_sparse["metrics"]
    test_reference = test_dense["metrics"]
    test_candidate = test_sparse["metrics"]
    dense_frequency = np.asarray(
        [row["raw_complex_rmse"] for row in test_dense["frequency_rows"]],
        dtype=float,
    )
    sparse_frequency = np.asarray(
        [row["raw_complex_rmse"] for row in test_sparse["frequency_rows"]],
        dtype=float,
    )
    _, held_out_indices = _frequency_partition(
        len(test_sparse["frequency_rows"]),
        int(args.sparse_frequency_stride),
    )
    regression = sparse_frequency[held_out_indices] > dense_frequency[held_out_indices] * (
        1.0 + float(args.frequency_regression_tolerance)
    )
    return {
        "validation_full_grid_relative_degradation": degradation(
            validation_reference["full_grid"]["raw_complex_rmse"],
            validation_candidate["full_grid"]["raw_complex_rmse"],
        ),
        "validation_held_out_relative_degradation": degradation(
            validation_reference["held_out"]["raw_complex_rmse"],
            validation_candidate["held_out"]["raw_complex_rmse"],
        ),
        "test_full_grid_relative_degradation": degradation(
            test_reference["full_grid"]["raw_complex_rmse"],
            test_candidate["full_grid"]["raw_complex_rmse"],
        ),
        "test_observed_relative_degradation": degradation(
            test_reference["observed"]["raw_complex_rmse"],
            test_candidate["observed"]["raw_complex_rmse"],
        ),
        "test_held_out_relative_degradation": degradation(
            test_reference["held_out"]["raw_complex_rmse"],
            test_candidate["held_out"]["raw_complex_rmse"],
        ),
        "test_target_relative_degradation": degradation(
            test_reference["target_raw_complex_rmse"],
            test_candidate["target_raw_complex_rmse"],
        ),
        "test_held_out_frequency_regression_fraction": float(np.mean(regression)),
        "test_held_out_frequency_regression_count": int(np.sum(regression)),
        "held_out_frequency_count": int(len(held_out_indices)),
        "frequency_regression_definition": (
            "sparse-arm RMSE at an unseen frequency exceeds the dense reference by more than "
            f"{100.0 * float(args.frequency_regression_tolerance):g}%"
        ),
        "maximum_held_out_relative_degradation": float(
            args.maximum_held_out_relative_degradation
        ),
        "max_held_out_regression_fraction": float(args.max_held_out_regression_fraction),
    }


def _decision(comparison: dict[str, Any], args: argparse.Namespace) -> str:
    degradation_ok = float(comparison["test_held_out_relative_degradation"]) <= float(
        args.maximum_held_out_relative_degradation
    )
    regression_ok = float(
        comparison["test_held_out_frequency_regression_fraction"]
    ) <= float(args.max_held_out_regression_fraction)
    validation_ok = float(
        comparison["validation_held_out_relative_degradation"]
    ) <= float(args.maximum_held_out_relative_degradation)
    if degradation_ok and regression_ok and validation_ok:
        return "REVIEW_SPARSE_FREQUENCY_GRID_FOR_PROXY_ONLY_KEEP_EMX_AT_0P5_GHZ"
    return "RETAIN_FULL_0P5_GHZ_GRID_FOR_PROXY_AND_EMX"


def _frequency_comparison_rows(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    observed_indices: set[int],
) -> list[dict[str, Any]]:
    rows = []
    for dense, sparse in zip(dense_rows, sparse_rows):
        index = int(dense["frequency_index"])
        dense_rmse = float(dense["raw_complex_rmse"])
        sparse_rmse = float(sparse["raw_complex_rmse"])
        rows.append(
            {
                "frequency_index": index,
                "frequency_ghz": dense["frequency_ghz"],
                "sparse_training_role": "observed" if index in observed_indices else "held_out",
                "dense_reference_raw_complex_rmse": dense_rmse,
                "sparse_grid_raw_complex_rmse": sparse_rmse,
                "relative_degradation": (sparse_rmse - dense_rmse) / max(dense_rmse, 1.0e-12),
                "dense_reference_raw_complex_mae": dense["raw_complex_mae"],
                "sparse_grid_raw_complex_mae": sparse["raw_complex_mae"],
            }
        )
    return rows


def _finite_evaluations(*items: dict[str, Any]) -> bool:
    for item in items:
        metrics = item.get("metrics") or {}
        stack: list[Any] = [metrics]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, (int, float)) and not math.isfinite(float(value)):
                return False
    return True


def _model_sha256(model: dict[str, list[np.ndarray]]) -> str:
    digest = hashlib.sha256()
    for group in ("weights", "biases"):
        for value in model[group]:
            array = np.asarray(value, dtype=np.float64)
            digest.update(str(array.shape).encode("ascii"))
            digest.update(array.tobytes())
    return digest.hexdigest()


def _input_s4p_quality(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    reciprocity = np.asarray(
        dataset.get("input_reciprocity_errors")
        if dataset.get("input_reciprocity_errors") is not None
        else [],
        dtype=float,
    )
    passivity = np.asarray(
        dataset.get("input_passivity_excesses")
        if dataset.get("input_passivity_excesses") is not None
        else [],
        dtype=float,
    )
    reciprocity_max = float(np.max(reciprocity)) if len(reciprocity) else None
    passivity_max = float(np.max(passivity)) if len(passivity) else None
    reciprocity_threshold = float(args.max_input_reciprocity_error)
    passivity_threshold = float(args.max_input_passivity_excess)
    return {
        "row_count": int(min(len(reciprocity), len(passivity))),
        "audit_stage": "raw complex S4P before reciprocal symmetrization",
        "raw_touchstone_content_sha256": str(dataset.get("touchstone_content_sha256") or ""),
        "reciprocity": {
            "max_error": reciprocity_max,
            "p95_error": float(np.quantile(reciprocity, 0.95)) if len(reciprocity) else None,
            "hard_threshold": reciprocity_threshold,
            "hard_threshold_pass": bool(
                reciprocity_max is not None and reciprocity_max <= reciprocity_threshold
            ),
        },
        "passivity": {
            "max_singular_value_excess": passivity_max,
            "p95_singular_value_excess": (
                float(np.quantile(passivity, 0.95)) if len(passivity) else None
            ),
            "hard_threshold": passivity_threshold,
            "hard_threshold_pass": bool(
                passivity_max is not None and passivity_max <= passivity_threshold
            ),
        },
        "scientific_boundary": (
            "Reciprocal encoding is a model representation, not evidence that the raw S4P was "
            "reciprocal. Raw reciprocity and passivity are hard-gated before symmetrization."
        ),
    }


def _array_sha256(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.shape).encode("ascii"))
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(array.tobytes())
    return digest.hexdigest()


def _seed(base: int, *parts: Any) -> int:
    digest = hashlib.sha256(str(base).encode("ascii"))
    for part in parts:
        digest.update(b"\0")
        digest.update(str(part).encode("utf-8"))
    return int.from_bytes(digest.digest()[:8], "big") % (2**32 - 1)


def _same_path(raw: Any, expected: Path) -> bool:
    if not raw:
        return False
    return Path(str(raw)).expanduser().resolve() == expected.resolve()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _base_summary(
    args: argparse.Namespace,
    training_csv: Path,
    training_manifest: Path,
    out_dir: Path,
    predictor_columns: list[str],
    split_columns: list[str],
    dataset: dict[str, Any],
    rejects: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "training_manifest": str(training_manifest),
        "training_manifest_sha256": _file_sha256(training_manifest),
        "out_dir": str(out_dir),
        "training_count": int(dataset.get("count") or 0),
        "predictor_columns": predictor_columns,
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


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_plot(
    path: Path,
    rows: list[dict[str, Any]],
    held_out_indices: list[int],
    target_frequency_ghz: float,
) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    frequencies = np.asarray([row["frequency_ghz"] for row in rows], dtype=float)
    dense = np.asarray([row["dense_reference_raw_complex_rmse"] for row in rows], dtype=float)
    sparse = np.asarray([row["sparse_grid_raw_complex_rmse"] for row in rows], dtype=float)
    held_out = np.asarray(held_out_indices, dtype=int)
    figure, axis = plt.subplots(figsize=(8.6, 4.9), dpi=180)
    axis.plot(frequencies, dense, color="#4b5563", label="Full 0.5 GHz training grid")
    axis.plot(frequencies, sparse, color="#1769aa", label="Interleaved 1 GHz training grid")
    axis.scatter(
        frequencies[held_out],
        sparse[held_out],
        s=13,
        facecolors="white",
        edgecolors="#b9473a",
        linewidths=0.8,
        label="Unseen by sparse arm",
        zorder=3,
    )
    axis.axvline(target_frequency_ghz, color="#7c3aed", linestyle=":", linewidth=1.1)
    axis.set_xlabel("Frequency (GHz)")
    axis.set_ylabel("Complex S RMSE")
    axis.set_title("Equal-update cross-frequency-resolution ablation")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _render_report(data: dict[str, Any]) -> str:
    comparison = data.get("comparison") or {}
    partition = data.get("frequency_partition") or {}
    return "\n".join(
        [
            "# Cross-frequency-resolution equal-budget benchmark",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Real S4P rows: `{data['training_count']}`",
            f"- Observed / held-out frequency points: `{partition.get('observed_point_count')}` / `{partition.get('held_out_point_count')}`",
            f"- Test held-out relative degradation: `{comparison.get('test_held_out_relative_degradation')}`",
            f"- Test held-out regression fraction: `{comparison.get('test_held_out_frequency_regression_fraction')}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
