#!/usr/bin/env python3
"""Benchmark adjacent-band self-transfer against independent band models.

Both arms use the same real Touchstone rows, physical-cell OOD split,
frequency bands, architecture, initial weights, mini-batches, and optimizer
updates. The only difference is whether a band model receives the neighboring
band's weights before each forward/backward training session.
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

import extract_touchstone_response_features as response  # noqa: E402
import train_broadband_sparameter_pca_surrogate as broadband  # noqa: E402
from compare_emx_hfss_ads import parse_port_pairs  # noqa: E402
from rfic_transformer_inverse_design.analysis import multiport_s_to_grounded_differential_z  # noqa: E402
from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    training_manifest = Path(args.training_manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "frequency_self_transfer_benchmark_summary.json"
    frequency_csv = out_dir / "frequency_self_transfer_frequency_errors.csv"
    history_csv = out_dir / "frequency_self_transfer_history.csv"
    plot_path = out_dir / "frequency_self_transfer_frequency_errors.png"
    weights_path = out_dir / "frequency_self_transfer_weights.npz"
    report_path = out_dir / "frequency_self_transfer_benchmark_report.md"

    manifest = _read_json(training_manifest)
    rows = broadband._read_rows(training_csv)
    predictor_columns = broadband._geometry_columns(rows, args.geometry_columns)
    split_columns = broadband._split_columns(args.split_reference_columns)
    selected_rows = broadband._deterministic_spread_sample(rows, int(args.max_rows))
    dataset, rejects = broadband._load_dataset(selected_rows, predictor_columns, split_columns, args)
    input_s4p_quality: dict[str, Any] = {}
    if int(dataset.get("count") or 0) > 0:
        input_s4p_quality = broadband._summarize_input_quality(dataset, args)
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
        "raw_input_s4p_quality_present": bool(input_s4p_quality)
        and int(input_s4p_quality.get("row_count") or 0) == int(dataset.get("count") or 0),
        "raw_input_reciprocity_threshold_pass": bool(
            (input_s4p_quality.get("reciprocity") or {}).get("hard_threshold_pass")
        ),
        "raw_input_passivity_threshold_pass": bool(
            (input_s4p_quality.get("passivity") or {}).get("hard_threshold_pass")
        ),
    }
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
                    else "FIX_SELF_TRANSFER_BENCHMARK_CONTRACT"
                ),
                "checks": checks,
                "input_s4p_quality": input_s4p_quality,
                "artifacts": {"summary": str(summary_path)},
            }
        )
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"overall_status={status}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    result = _run_benchmark(dataset, args)
    checks.update(result["checks"])
    comparable = all(checks.values())
    comparison = result["comparison"]
    decision = _decision(comparison, args) if comparable else "FIX_SELF_TRANSFER_BENCHMARK_CONTRACT"
    status = "COMPLETE_REVIEW_REQUIRED" if comparable else "FAIL"

    _write_csv(frequency_csv, result["frequency_rows"])
    _write_csv(history_csv, result["history_rows"])
    plot_status = _write_plot(plot_path, result["frequency_rows"], float(args.target_frequency_ghz))
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
            "frequency_bands": result["frequency_bands"],
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
                "This is a predeclared same-data forward-surrogate ablation. A favorable result does not alter "
                "the active inverse model or EMX queue. Adoption still requires inverse retraining, DRC, real "
                "EMX closure, and sampled HFSS correlation. The paper's ten-band optimum is not assumed universal."
            ),
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"test_full_band_relative_improvement={comparison['test_full_band_relative_improvement']}")
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
    parser.add_argument("--band-count", type=int, default=10)
    parser.add_argument("--transfer-iterations", type=int, default=2)
    parser.add_argument("--epochs-per-session", type=int, default=2)
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
    parser.add_argument("--input-reciprocity-audit-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--input-passivity-audit-tolerance", type=float, default=1.0e-3)
    parser.add_argument("--max-input-reciprocity-error", type=float, default=0.02)
    parser.add_argument("--max-input-passivity-excess", type=float, default=0.05)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
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
    if not 1 <= args.band_count <= args.expected_frequency_points:
        parser.error("--band-count must be within the expected frequency-point count")
    if args.transfer_iterations < 1 or args.epochs_per_session < 1:
        parser.error("transfer iterations and epochs per session must be positive")
    if args.hidden_depth < 1 or args.hidden_width < 4 or args.batch_size < 1:
        parser.error("invalid MLP architecture or batch size")
    if not 0.0 <= args.minimum_material_improvement < 1.0:
        parser.error("--minimum-material-improvement must be in [0,1)")
    if not 0.0 <= args.max_frequency_regression_fraction <= 1.0:
        parser.error("--max-frequency-regression-fraction must be in [0,1]")
    if args.frequency_regression_tolerance < 0.0:
        parser.error("--frequency-regression-tolerance must be nonnegative")
    if args.minimum_physical_improvement < 0.0:
        parser.error("--minimum-physical-improvement must be nonnegative")
    if (
        args.input_reciprocity_audit_tolerance < 0.0
        or args.input_passivity_audit_tolerance < 0.0
        or args.max_input_reciprocity_error < 0.0
        or args.max_input_passivity_excess < 0.0
        or args.max_passivity_correction_increase < 0.0
        or args.max_candidate_test_complex_rmse < 0.0
        or args.max_candidate_raw_passivity_excess < 0.0
    ):
        parser.error("S-parameter quality and adoption thresholds must be nonnegative")
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
    output_train = output[train].reshape(-1, output.shape[2])
    output_mean = np.mean(output_train, axis=0)
    output_scale = np.maximum(np.std(output_train, axis=0), 1.0e-6)
    output_normalized = (output - output_mean[None, None, :]) / output_scale[None, None, :]
    frequency_normalized = 2.0 * (frequencies - frequencies[0]) / max(
        frequencies[-1] - frequencies[0], 1.0
    ) - 1.0
    bands = _contiguous_frequency_bands(frequency_points, int(args.band_count))
    band_audit = _audit_frequency_bands(bands, frequency_points, int(args.band_count))
    band_pairs = [
        _band_pairs(geometry_normalized, frequency_normalized, output_normalized, train, indices)
        for indices in bands
    ]

    baseline_models: list[dict[str, list[np.ndarray]]] = []
    transfer_models: list[dict[str, list[np.ndarray]]] = []
    initialization_rows: list[dict[str, Any]] = []
    for band_index in range(len(bands)):
        rng = np.random.default_rng(_seed(int(args.seed), "init", band_index))
        model = _init_mlp(
            geometry.shape[1] + 1,
            output.shape[2],
            int(args.hidden_depth),
            int(args.hidden_width),
            rng,
        )
        baseline_model = _copy_model(model)
        transfer_model = _copy_model(model)
        baseline_models.append(baseline_model)
        transfer_models.append(transfer_model)
        initialization_rows.append(
            {
                "band_index": band_index,
                "source_initial_weights_sha256": _model_sha256(model),
                "baseline_initial_weights_sha256": _model_sha256(baseline_model),
                "transfer_initial_weights_sha256": _model_sha256(transfer_model),
            }
        )

    baseline_updates = np.zeros(len(bands), dtype=np.int64)
    transfer_updates = np.zeros(len(bands), dtype=np.int64)
    history_rows: list[dict[str, Any]] = []
    for iteration in range(1, int(args.transfer_iterations) + 1):
        for phase, order in (
            ("forward", list(range(len(bands)))),
            ("backward", list(reversed(range(len(bands))))),
        ):
            for position, band_index in enumerate(order):
                if phase == "forward" and position > 0:
                    transfer_models[band_index] = _copy_model(transfer_models[order[position - 1]])
                if phase == "backward" and position > 0:
                    transfer_models[band_index] = _copy_model(transfer_models[order[position - 1]])
                session_seed = _seed(int(args.seed), "session", iteration, phase, band_index)
                pair_x, pair_y = band_pairs[band_index]
                baseline_updates[band_index] += _train_session(
                    baseline_models[band_index], pair_x, pair_y, args, session_seed
                )
                transfer_updates[band_index] += _train_session(
                    transfer_models[band_index], pair_x, pair_y, args, session_seed
                )
        validation_baseline = _evaluate(
            baseline_models,
            bands,
            split["validation"],
            geometry_normalized,
            frequency_normalized,
            output_mean,
            output_scale,
            matrices,
            frequencies,
            args,
        )
        validation_transfer = _evaluate(
            transfer_models,
            bands,
            split["validation"],
            geometry_normalized,
            frequency_normalized,
            output_mean,
            output_scale,
            matrices,
            frequencies,
            args,
        )
        history_rows.append(
            {
                "iteration": iteration,
                "baseline_validation_full_band_complex_rmse": validation_baseline["metrics"]["raw_complex_rmse"],
                "transfer_validation_full_band_complex_rmse": validation_transfer["metrics"]["raw_complex_rmse"],
                "baseline_validation_target_complex_rmse": validation_baseline["metrics"]["target_raw_complex_rmse"],
                "transfer_validation_target_complex_rmse": validation_transfer["metrics"]["target_raw_complex_rmse"],
            }
        )

    validation_baseline = _evaluate(
        baseline_models,
        bands,
        split["validation"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        args,
    )
    validation_transfer = _evaluate(
        transfer_models,
        bands,
        split["validation"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        args,
    )
    test_baseline = _evaluate(
        baseline_models,
        bands,
        split["test"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        args,
    )
    test_transfer = _evaluate(
        transfer_models,
        bands,
        split["test"],
        geometry_normalized,
        frequency_normalized,
        output_mean,
        output_scale,
        matrices,
        frequencies,
        args,
    )
    comparison = _compare(
        validation_baseline,
        validation_transfer,
        test_baseline,
        test_transfer,
        args,
    )
    frequency_rows = []
    for baseline_row, transfer_row in zip(test_baseline["frequency_rows"], test_transfer["frequency_rows"]):
        baseline_rmse = float(baseline_row["raw_complex_rmse"])
        transfer_rmse = float(transfer_row["raw_complex_rmse"])
        frequency_rows.append(
            {
                "frequency_ghz": baseline_row["frequency_ghz"],
                "baseline_raw_complex_rmse": baseline_rmse,
                "transfer_raw_complex_rmse": transfer_rmse,
                "relative_improvement": (baseline_rmse - transfer_rmse) / max(baseline_rmse, 1.0e-12),
                "baseline_raw_complex_mae": baseline_row["raw_complex_mae"],
                "transfer_raw_complex_mae": transfer_row["raw_complex_mae"],
            }
        )

    equal_updates = _equal_optimizer_updates(baseline_updates, transfer_updates)
    band_coverage = np.concatenate(bands) if bands else np.empty(0, dtype=int)
    initialization_equal = all(
        row["source_initial_weights_sha256"]
        == row["baseline_initial_weights_sha256"]
        == row["transfer_initial_weights_sha256"]
        for row in initialization_rows
    )
    train = np.asarray(split["train"], dtype=int)
    validation = np.asarray(split["validation"], dtype=int)
    test = np.asarray(split["test"], dtype=int)
    split_rows_disjoint = _rows_are_pairwise_disjoint(train, validation, test)
    checks = {
        "physical_cell_ood_split": split_audit.get("split_mode") == "physical_cell_grouped",
        "physical_cell_overlap_zero": int(split_audit.get("physical_cell_overlap_count") or 0) == 0,
        "all_rows_assigned_once": split_audit.get("all_rows_assigned_once") is True,
        "all_frequency_points_assigned_once": np.array_equal(
            np.sort(band_coverage), np.arange(frequency_points)
        ),
        "all_bands_nonempty": all(len(item) > 0 for item in bands),
        "frequency_bands_are_monotonic_contiguous": band_audit["monotonic_contiguous"],
        "frequency_band_boundaries_are_contiguous": band_audit["boundaries_contiguous"],
        "frequency_band_count_matches_contract": band_audit["band_count_matches"],
        "train_validation_test_rows_disjoint": split_rows_disjoint,
        "same_initial_weights_per_band": initialization_equal,
        "equal_optimizer_updates_per_band": equal_updates,
        "finite_validation_metrics": _finite_metrics(validation_baseline, validation_transfer),
        "finite_test_metrics": _finite_metrics(test_baseline, test_transfer),
        "physical_metric_extraction_available": all(
            float(item["metrics"].get("target_physical_valid_fraction") or 0.0) > 0.99
            for item in (
                validation_baseline,
                validation_transfer,
                test_baseline,
                test_transfer,
            )
        ),
    }
    weights = {
        "geometry_mean": geometry_mean,
        "geometry_scale": geometry_scale,
        "output_mean": output_mean,
        "output_scale": output_scale,
        "frequencies_hz": frequencies,
        "train_row_indices": train,
        "validation_row_indices": validation,
        "test_row_indices": test,
        "baseline_updates_per_band": baseline_updates,
        "transfer_updates_per_band": transfer_updates,
    }
    for arm, models in (("baseline", baseline_models), ("transfer", transfer_models)):
        for band_index, model in enumerate(models):
            for layer, weight in enumerate(model["weights"]):
                weights[f"{arm}_band_{band_index:02d}_weight_{layer}"] = weight
            for layer, bias in enumerate(model["biases"]):
                weights[f"{arm}_band_{band_index:02d}_bias_{layer}"] = bias
    return {
        "checks": checks,
        "split_audit": split_audit,
        "frequency_bands": [
            {
                "band_index": index,
                "point_count": int(len(points)),
                "start_ghz": float(frequencies[points[0]] / 1.0e9),
                "stop_ghz": float(frequencies[points[-1]] / 1.0e9),
                "frequency_indices": [int(value) for value in points],
            }
            for index, points in enumerate(bands)
        ],
        "architecture": {
            "input": "10 normalized geometry variables plus normalized frequency",
            "output": f"{output.shape[2]} real values for reciprocal S upper triangle at one frequency",
            "hidden_depth": int(args.hidden_depth),
            "hidden_width": int(args.hidden_width),
            "activation": "GELU",
            "parameter_count_per_band": _parameter_count(baseline_models[0]),
        },
        "equal_budget_contract": {
            "same_real_touchstone_rows": True,
            "same_physical_cell_ood_split": True,
            "same_architecture": True,
            "same_parameter_count": True,
            "same_initial_weights_per_band": initialization_equal,
            "initialization_rows": initialization_rows,
            "same_minibatch_schedule_per_band_session": True,
            "sessions_per_band": 2 * int(args.transfer_iterations),
            "epochs_per_session": int(args.epochs_per_session),
            "baseline_updates_per_band": [int(value) for value in baseline_updates],
            "transfer_updates_per_band": [int(value) for value in transfer_updates],
            "equal_updates_per_band": equal_updates,
            "train_row_indices_sha256": _array_sha256(train),
            "validation_row_indices_sha256": _array_sha256(validation),
            "test_row_indices_sha256": _array_sha256(test),
            "validation_set_used_for_training": False,
            "test_set_used_for_training": False,
            "fixed_iteration_count_no_early_stopping": True,
            "validation_set_used_for_checkpoint_selection": False,
            "test_set_used_for_checkpoint_selection": False,
            "only_arm_difference": "neighboring-band weight copy before each transfer-arm session",
        },
        "metrics": {
            "validation": {
                "independent_band": validation_baseline["metrics"],
                "self_transfer": validation_transfer["metrics"],
            },
            "test": {
                "independent_band": test_baseline["metrics"],
                "self_transfer": test_transfer["metrics"],
            },
        },
        "comparison": comparison,
        "frequency_rows": frequency_rows,
        "history_rows": history_rows,
        "weights": weights,
    }


def _band_pairs(
    geometry: np.ndarray,
    frequency: np.ndarray,
    output: np.ndarray,
    rows: np.ndarray,
    frequency_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    pair_input = _pair_inputs(geometry, frequency, rows, frequency_indices)
    pair_output = output[np.asarray(rows, dtype=int)][:, frequency_indices, :].reshape(
        -1, output.shape[2]
    )
    return pair_input, pair_output


def _contiguous_frequency_bands(point_count: int, band_count: int) -> list[np.ndarray]:
    if not 1 <= int(band_count) <= int(point_count):
        raise ValueError("band_count must be within the frequency-point count")
    return [
        np.asarray(item, dtype=int)
        for item in np.array_split(np.arange(int(point_count), dtype=int), int(band_count))
    ]


def _audit_frequency_bands(
    bands: list[np.ndarray],
    point_count: int,
    expected_band_count: int,
) -> dict[str, bool]:
    normalized = [np.asarray(item, dtype=int).reshape(-1) for item in bands]
    nonempty = bool(normalized) and all(len(item) > 0 for item in normalized)
    monotonic_contiguous = nonempty and all(
        len(item) == 1 or np.all(np.diff(item) == 1) for item in normalized
    )
    boundaries_contiguous = nonempty and int(normalized[0][0]) == 0
    if boundaries_contiguous:
        boundaries_contiguous = all(
            int(previous[-1]) + 1 == int(current[0])
            for previous, current in zip(normalized[:-1], normalized[1:])
        ) and int(normalized[-1][-1]) == int(point_count) - 1
    coverage = np.concatenate(normalized) if nonempty else np.empty(0, dtype=int)
    assigned_once = np.array_equal(coverage, np.arange(int(point_count), dtype=int))
    return {
        "band_count_matches": len(normalized) == int(expected_band_count),
        "all_bands_nonempty": nonempty,
        "monotonic_contiguous": bool(monotonic_contiguous),
        "boundaries_contiguous": bool(boundaries_contiguous),
        "all_frequency_points_assigned_once_in_order": bool(assigned_once),
    }


def _equal_optimizer_updates(baseline: np.ndarray, transfer: np.ndarray) -> bool:
    baseline_values = np.asarray(baseline, dtype=np.int64)
    transfer_values = np.asarray(transfer, dtype=np.int64)
    return bool(
        baseline_values.shape == transfer_values.shape
        and baseline_values.size > 0
        and np.all(baseline_values > 0)
        and np.array_equal(baseline_values, transfer_values)
    )


def _rows_are_pairwise_disjoint(*groups: np.ndarray) -> bool:
    sets = [set(int(value) for value in np.asarray(group, dtype=int)) for group in groups]
    return all(not (left & right) for index, left in enumerate(sets) for right in sets[index + 1 :])


def _pair_inputs(
    geometry: np.ndarray,
    frequency: np.ndarray,
    rows: np.ndarray,
    frequency_indices: np.ndarray,
) -> np.ndarray:
    selected_geometry = geometry[np.asarray(rows, dtype=int)]
    selected_frequency = frequency[np.asarray(frequency_indices, dtype=int)]
    pair_geometry = np.repeat(selected_geometry, len(selected_frequency), axis=0)
    pair_frequency = np.tile(selected_frequency, len(selected_geometry))[:, None]
    return np.column_stack((pair_geometry, pair_frequency))


def _init_mlp(
    input_dim: int,
    output_dim: int,
    depth: int,
    width: int,
    rng: np.random.Generator,
) -> dict[str, list[np.ndarray]]:
    sizes = [input_dim] + [width] * depth + [output_dim]
    weights = []
    biases = []
    for fan_in, fan_out in zip(sizes[:-1], sizes[1:]):
        weights.append(rng.normal(0.0, math.sqrt(2.0 / max(1, fan_in)), size=(fan_in, fan_out)))
        biases.append(np.zeros(fan_out, dtype=float))
    return {"weights": weights, "biases": biases}


def _copy_model(model: dict[str, list[np.ndarray]]) -> dict[str, list[np.ndarray]]:
    return {
        "weights": [np.array(value, copy=True) for value in model["weights"]],
        "biases": [np.array(value, copy=True) for value in model["biases"]],
    }


def _train_session(
    model: dict[str, list[np.ndarray]],
    x: np.ndarray,
    y: np.ndarray,
    args: argparse.Namespace,
    seed: int,
) -> int:
    rng = np.random.default_rng(seed)
    state = _init_adam(model)
    updates = 0
    for _ in range(int(args.epochs_per_session)):
        for start in range(0, len(x), int(args.batch_size)):
            if start == 0:
                order = rng.permutation(len(x))
            indices = order[start : start + int(args.batch_size)]
            grad_weights, grad_biases = _gradients(
                x[indices],
                y[indices],
                model,
                float(args.weight_decay),
            )
            _adam_step(model, grad_weights, grad_biases, state, float(args.learning_rate))
            updates += 1
    return updates


def _gradients(
    x: np.ndarray,
    y: np.ndarray,
    model: dict[str, list[np.ndarray]],
    weight_decay: float,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    weights = model["weights"]
    biases = model["biases"]
    activations = [x]
    preactivations: list[np.ndarray] = []
    value = x
    for layer, (weight, bias) in enumerate(zip(weights, biases)):
        preactivation = value @ weight + bias
        preactivations.append(preactivation)
        value = preactivation if layer == len(weights) - 1 else _gelu(preactivation)
        activations.append(value)
    delta = (2.0 / max(1, len(x))) * (activations[-1] - y)
    grad_weights = [np.zeros_like(value) for value in weights]
    grad_biases = [np.zeros_like(value) for value in biases]
    for layer in reversed(range(len(weights))):
        grad_weights[layer] = activations[layer].T @ delta + weight_decay * weights[layer]
        grad_biases[layer] = np.sum(delta, axis=0)
        if layer > 0:
            delta = (delta @ weights[layer].T) * _gelu_derivative(preactivations[layer - 1])
    return grad_weights, grad_biases


def _predict(model: dict[str, list[np.ndarray]], x: np.ndarray, batch_size: int = 8192) -> np.ndarray:
    outputs = []
    for start in range(0, len(x), batch_size):
        value = x[start : start + batch_size]
        for layer, (weight, bias) in enumerate(zip(model["weights"], model["biases"])):
            value = value @ weight + bias
            if layer < len(model["weights"]) - 1:
                value = _gelu(value)
        outputs.append(value)
    return np.vstack(outputs) if outputs else np.empty((0, model["biases"][-1].shape[0]))


def _init_adam(model: dict[str, list[np.ndarray]]) -> dict[str, Any]:
    return {
        "t": 0,
        "mw": [np.zeros_like(value) for value in model["weights"]],
        "vw": [np.zeros_like(value) for value in model["weights"]],
        "mb": [np.zeros_like(value) for value in model["biases"]],
        "vb": [np.zeros_like(value) for value in model["biases"]],
    }


def _adam_step(
    model: dict[str, list[np.ndarray]],
    grad_weights: list[np.ndarray],
    grad_biases: list[np.ndarray],
    state: dict[str, Any],
    learning_rate: float,
) -> None:
    beta1, beta2, epsilon = 0.9, 0.999, 1.0e-8
    state["t"] += 1
    time = int(state["t"])
    for layer in range(len(model["weights"])):
        state["mw"][layer] = beta1 * state["mw"][layer] + (1.0 - beta1) * grad_weights[layer]
        state["vw"][layer] = beta2 * state["vw"][layer] + (1.0 - beta2) * grad_weights[layer] ** 2
        state["mb"][layer] = beta1 * state["mb"][layer] + (1.0 - beta1) * grad_biases[layer]
        state["vb"][layer] = beta2 * state["vb"][layer] + (1.0 - beta2) * grad_biases[layer] ** 2
        model["weights"][layer] -= learning_rate * (state["mw"][layer] / (1.0 - beta1**time)) / (
            np.sqrt(state["vw"][layer] / (1.0 - beta2**time)) + epsilon
        )
        model["biases"][layer] -= learning_rate * (state["mb"][layer] / (1.0 - beta1**time)) / (
            np.sqrt(state["vb"][layer] / (1.0 - beta2**time)) + epsilon
        )


def _evaluate(
    models: list[dict[str, list[np.ndarray]]],
    bands: list[np.ndarray],
    rows: np.ndarray,
    geometry: np.ndarray,
    frequency: np.ndarray,
    output_mean: np.ndarray,
    output_scale: np.ndarray,
    truth: np.ndarray,
    frequencies_hz: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    row_indices = np.asarray(rows, dtype=int)
    component_count = len(output_mean)
    predicted_components = np.empty((len(row_indices), len(frequency), component_count), dtype=float)
    for model, band in zip(models, bands):
        pair_x = _pair_inputs(
            geometry,
            frequency,
            row_indices,
            band,
        )
        prediction = _predict(model, pair_x)
        prediction = prediction * output_scale[None, :] + output_mean[None, :]
        predicted_components[:, band, :] = prediction.reshape(len(row_indices), len(band), component_count)
    ports = int(args.expected_ports)
    upper_rows, upper_columns = np.triu_indices(ports)
    upper_count = len(upper_rows)
    predicted_upper = (
        predicted_components[:, :, :upper_count]
        + 1j * predicted_components[:, :, upper_count:]
    )
    prediction = np.zeros((len(row_indices), len(frequency), ports, ports), dtype=np.complex128)
    prediction[:, :, upper_rows, upper_columns] = predicted_upper
    prediction[:, :, upper_columns, upper_rows] = predicted_upper
    target = truth[row_indices]
    error = prediction - target
    projected, correction = _passivity_project_vectorized(prediction)
    projected_error = projected - target
    raw_singular = np.linalg.svd(prediction, compute_uv=False)
    projected_singular = np.linalg.svd(projected, compute_uv=False)
    requested_hz = float(args.target_frequency_ghz) * 1.0e9
    target_index = int(np.argmin(np.abs(frequencies_hz - requested_hz)))
    physical = _target_physical_metrics(prediction, target, frequencies_hz, target_index)
    frequency_rows = []
    for frequency_index, frequency_hz in enumerate(frequencies_hz):
        frequency_rows.append(
            {
                "frequency_ghz": float(frequency_hz / 1.0e9),
                "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error[:, frequency_index]) ** 2))),
                "raw_complex_mae": float(np.mean(np.abs(error[:, frequency_index]))),
            }
        )
    return {
        "metrics": {
            "row_count": int(len(row_indices)),
            "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error) ** 2))),
            "raw_complex_mae": float(np.mean(np.abs(error))),
            "projected_complex_rmse": float(np.sqrt(np.mean(np.abs(projected_error) ** 2))),
            "target_frequency_ghz": float(frequencies_hz[target_index] / 1.0e9),
            "target_raw_complex_rmse": float(np.sqrt(np.mean(np.abs(error[:, target_index]) ** 2))),
            "target_raw_complex_mae": float(np.mean(np.abs(error[:, target_index]))),
            "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular) - 1.0)),
            "projected_max_passivity_excess": float(max(0.0, np.max(projected_singular) - 1.0)),
            "passivity_projection_complex_rmse": float(np.sqrt(np.mean(np.abs(correction) ** 2))),
            "reciprocity_error": float(np.max(np.abs(prediction - np.swapaxes(prediction, 2, 3)))),
            **physical,
        },
        "frequency_rows": frequency_rows,
    }


def _passivity_project_vectorized(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u, singular_values, vh = np.linalg.svd(matrix, full_matrices=False)
    clipped = np.minimum(singular_values, 1.0)
    projected = np.einsum("...ij,...j,...jk->...ik", u, clipped, vh, optimize=True)
    return projected, projected - matrix


def _target_physical_metrics(
    prediction: np.ndarray,
    target: np.ndarray,
    frequencies: np.ndarray,
    target_index: int,
) -> dict[str, float]:
    predicted_rows: list[list[float]] = []
    target_rows: list[list[float]] = []
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
    finite = np.all(np.isfinite(predicted_values), axis=1) & np.all(
        np.isfinite(target_values), axis=1
    )
    result: dict[str, float] = {
        "target_physical_valid_fraction": float(np.mean(finite)) if len(finite) else 0.0
    }
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


def _compare(
    validation_baseline: dict[str, Any],
    validation_transfer: dict[str, Any],
    test_baseline: dict[str, Any],
    test_transfer: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    def improvement(baseline: float, transfer: float) -> float:
        return (baseline - transfer) / max(baseline, 1.0e-12)

    validation_base = validation_baseline["metrics"]
    validation_new = validation_transfer["metrics"]
    test_base = test_baseline["metrics"]
    test_new = test_transfer["metrics"]
    baseline_frequency = np.asarray(
        [row["raw_complex_rmse"] for row in test_baseline["frequency_rows"]], dtype=float
    )
    transfer_frequency = np.asarray(
        [row["raw_complex_rmse"] for row in test_transfer["frequency_rows"]], dtype=float
    )
    regression = transfer_frequency > baseline_frequency * (
        1.0 + float(args.frequency_regression_tolerance)
    )
    comparison = {
        "validation_full_band_relative_improvement": improvement(
            validation_base["raw_complex_rmse"], validation_new["raw_complex_rmse"]
        ),
        "validation_target_relative_improvement": improvement(
            validation_base["target_raw_complex_rmse"], validation_new["target_raw_complex_rmse"]
        ),
        "test_full_band_relative_improvement": improvement(
            test_base["raw_complex_rmse"], test_new["raw_complex_rmse"]
        ),
        "test_target_relative_improvement": improvement(
            test_base["target_raw_complex_rmse"], test_new["target_raw_complex_rmse"]
        ),
        "test_frequency_regression_fraction": float(np.mean(regression)),
        "test_frequency_regression_count": int(np.sum(regression)),
        "frequency_regression_definition": (
            f"self-transfer RMSE exceeds independent-band RMSE by more than "
            f"{100.0 * float(args.frequency_regression_tolerance):g}%"
        ),
        "minimum_material_improvement": float(args.minimum_material_improvement),
        "max_frequency_regression_fraction": float(args.max_frequency_regression_fraction),
        "test_passivity_projection_correction_increase": float(
            test_new["passivity_projection_complex_rmse"]
            - test_base["passivity_projection_complex_rmse"]
        ),
        "candidate_test_raw_complex_rmse": float(test_new["raw_complex_rmse"]),
        "candidate_test_raw_max_passivity_excess": float(
            test_new["raw_max_passivity_excess"]
        ),
        "candidate_test_target_physical_valid_fraction": float(
            test_new["target_physical_valid_fraction"]
        ),
    }
    for split_name, baseline_metrics, transfer_metrics in (
        ("validation", validation_base, validation_new),
        ("test", test_base, test_new),
    ):
        for name in ("lp_nh", "ls_nh", "q", "k_abs"):
            comparison[f"{split_name}_target_{name}_mae_relative_improvement"] = improvement(
                float(baseline_metrics[f"target_{name}_mae"]),
                float(transfer_metrics[f"target_{name}_mae"]),
            )
    return comparison


def _decision(comparison: dict[str, Any], args: argparse.Namespace) -> str:
    threshold = float(args.minimum_material_improvement)
    complex_improvements = [
        float(comparison["validation_full_band_relative_improvement"]),
        float(comparison["validation_target_relative_improvement"]),
        float(comparison["test_full_band_relative_improvement"]),
        float(comparison["test_target_relative_improvement"]),
    ]
    physical_threshold = float(args.minimum_physical_improvement)
    physical_improvements = [
        float(comparison[f"{split_name}_target_{name}_mae_relative_improvement"])
        for split_name in ("validation", "test")
        for name in ("lp_nh", "ls_nh", "q", "k_abs")
    ]
    regression_ok = float(comparison["test_frequency_regression_fraction"]) <= float(
        args.max_frequency_regression_fraction
    )
    absolute_quality_ok = (
        float(comparison["candidate_test_raw_complex_rmse"])
        <= float(args.max_candidate_test_complex_rmse)
        and float(comparison["candidate_test_raw_max_passivity_excess"])
        <= float(args.max_candidate_raw_passivity_excess)
        and float(comparison["test_passivity_projection_correction_increase"])
        <= float(args.max_passivity_correction_increase)
        and float(comparison["candidate_test_target_physical_valid_fraction"]) > 0.99
    )
    if (
        all(value >= threshold for value in complex_improvements)
        and all(value >= physical_threshold for value in physical_improvements)
        and regression_ok
        and absolute_quality_ok
    ):
        return "REVIEW_SELF_TRANSFER_FOR_BROADBAND_PROXY_WITH_INVERSE_EMX_CLOSURE"
    if all(value <= -threshold for value in complex_improvements):
        return "RETAIN_INDEPENDENT_BAND_BASELINE_SELF_TRANSFER_IS_MATERIALLY_WORSE"
    return "RETAIN_INDEPENDENT_BAND_BASELINE_MIXED_SELF_TRANSFER_EVIDENCE"


def _finite_metrics(*items: dict[str, Any]) -> bool:
    for item in items:
        for value in (item.get("metrics") or {}).values():
            if isinstance(value, (int, float)) and not math.isfinite(float(value)):
                return False
    return True


def _parameter_count(model: dict[str, list[np.ndarray]]) -> int:
    return int(sum(value.size for value in model["weights"] + model["biases"]))


def _model_sha256(model: dict[str, list[np.ndarray]]) -> str:
    digest = hashlib.sha256()
    for kind in ("weights", "biases"):
        for value in model[kind]:
            array = np.asarray(value, dtype=np.float64)
            digest.update(kind.encode("ascii"))
            digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
            digest.update(array.tobytes())
    return digest.hexdigest()


def _array_sha256(values: np.ndarray) -> str:
    array = np.asarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
    digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _gelu(value: np.ndarray) -> np.ndarray:
    coefficient = math.sqrt(2.0 / math.pi)
    return 0.5 * value * (
        1.0 + np.tanh(coefficient * (value + 0.044715 * value**3))
    )


def _gelu_derivative(value: np.ndarray) -> np.ndarray:
    coefficient = math.sqrt(2.0 / math.pi)
    transformed = coefficient * (value + 0.044715 * value**3)
    tanh_value = np.tanh(transformed)
    return 0.5 * (1.0 + tanh_value) + 0.5 * value * (1.0 - tanh_value**2) * coefficient * (
        1.0 + 3.0 * 0.044715 * value**2
    )


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
        [row["baseline_raw_complex_rmse"] for row in rows],
        color="#4b5563",
        label="Independent bands",
    )
    axis.plot(
        frequencies,
        [row["transfer_raw_complex_rmse"] for row in rows],
        color="#1769aa",
        label="Adjacent-band self-transfer",
    )
    axis.axvline(target_frequency_ghz, color="#b9473a", linestyle=":", linewidth=1.1)
    axis.set_xlabel("Frequency (GHz)")
    axis.set_ylabel("Complex S RMSE")
    axis.set_title("Equal-budget frequency-domain self-transfer ablation")
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
            "# Frequency-domain self-transfer benchmark",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Touchstone rows: `{data['training_count']}`",
            f"- Bands: `{len(data.get('frequency_bands') or [])}`",
            f"- Test full-band relative improvement: `{comparison.get('test_full_band_relative_improvement')}`",
            f"- Test target-frequency relative improvement: `{comparison.get('test_target_relative_improvement')}`",
            f"- Test frequency regression fraction: `{comparison.get('test_frequency_regression_fraction')}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
