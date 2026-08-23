#!/usr/bin/env python3
"""Train a traceable low-rank broadband complex-S baseline.

By default the model maps 10 geometry variables to a reciprocal S4P spectrum.
With ``--predictor-role physical_spec`` it instead provides the paper-motivated
Lp/Ls/Q/|K| -> broadband-spectrum spectral-expander baseline. A randomized PCA
basis compresses the spectrum and a ridge model predicts its coefficients.
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

from rfic_transformer_inverse_design.model_splitting import split_physical_feature_indices  # noqa: E402
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


DEFAULT_INPUT_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_rows(training_csv)
    geometry_columns = _geometry_columns(rows, args.geometry_columns)
    split_columns = _split_columns(args.split_reference_columns)
    selected_rows = _deterministic_spread_sample(rows, int(args.max_rows))
    dataset, rejects = _load_dataset(selected_rows, geometry_columns, split_columns, args)

    summary_path = out_dir / "broadband_sparameter_pca_surrogate_summary.json"
    frequency_csv = out_dir / "broadband_sparameter_pca_frequency_errors.csv"
    weights_path = out_dir / "broadband_sparameter_pca_surrogate_weights.npz"
    plot_path = out_dir / "broadband_sparameter_pca_frequency_errors.png"
    report_path = out_dir / "broadband_sparameter_pca_surrogate_report.md"
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "predictor_column_count": len(geometry_columns) == int(args.expected_predictor_count),
        "predictor_role_contract": (
            args.predictor_role == "geometry" and len(geometry_columns) == 10
        )
        or (
            args.predictor_role == "physical_spec"
            and _physical_spec_contract(geometry_columns)
        ),
        "four_split_reference_columns": len(split_columns) == 4,
        "usable_rows_meet_minimum": int(dataset["count"]) >= int(args.min_rows),
        "all_selected_rows_loaded": int(dataset["count"]) == len(selected_rows),
        "frequency_grid_consistent": bool(dataset.get("frequency_grid_consistent")),
    }
    if not all(checks.values()):
        payload = _base_summary(args, training_csv, out_dir, geometry_columns, split_columns, dataset, rejects)
        payload.update(
            {
                "overall_status": "WAITING_FOR_COMPLETE_BROADBAND_DATA" if not checks["usable_rows_meet_minimum"] else "FAIL",
                "decision": "WAIT_FOR_REAL_S4P_ROWS" if not checks["usable_rows_meet_minimum"] else "FIX_BROADBAND_TRAINING_CONTRACT",
                "checks": checks,
                "artifacts": {"summary": str(summary_path), "weights": "", "frequency_errors": "", "plot": ""},
            }
        )
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"overall_status={payload['overall_status']}")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    result = _train_and_evaluate(dataset, args)
    input_quality = _summarize_input_quality(dataset, args)
    thresholds_configured = math.isfinite(float(args.max_test_complex_rmse))
    input_quality_thresholds_configured = bool(
        math.isfinite(float(args.max_input_reciprocity_error))
        or math.isfinite(float(args.max_input_passivity_excess))
    )
    input_quality_pass = bool(
        input_quality["reciprocity"]["hard_threshold_pass"]
        and input_quality["passivity"]["hard_threshold_pass"]
    )
    checks["input_reciprocity_threshold_pass"] = bool(
        input_quality["reciprocity"]["hard_threshold_pass"]
    )
    checks["input_passivity_threshold_pass"] = bool(
        input_quality["passivity"]["hard_threshold_pass"]
    )
    if not input_quality_pass:
        status = "FAIL"
    elif thresholds_configured:
        status = "PASS" if result["metrics"]["test_raw_complex_rmse"] <= float(args.max_test_complex_rmse) else "FAIL"
    else:
        status = "COMPLETE_REVIEW_REQUIRED"
    _write_csv(frequency_csv, result["frequency_rows"])
    plot_status = _write_plot(plot_path, result["frequency_rows"])
    np.savez_compressed(weights_path, **result["weights"])
    payload = _base_summary(args, training_csv, out_dir, geometry_columns, split_columns, dataset, rejects)
    payload.update(
        {
            "overall_status": status,
            "decision": (
                "REJECT_INPUT_S4P_FIX_PORT_OR_SOLVER_CONTRACT"
                if not input_quality_pass
                else (
                    "COMPARE_WITH_NEURAL_SPECTRAL_EXPANDER"
                    if status == "COMPLETE_REVIEW_REQUIRED" and args.predictor_role == "physical_spec"
                    else (
                        "COMPARE_WITH_FREQUENCY_CONDITIONED_FORWARD_MODEL"
                        if status == "COMPLETE_REVIEW_REQUIRED"
                        else ("USE_AS_BROADBAND_BASELINE" if status == "PASS" else "DO_NOT_USE_BROADBAND_BASELINE")
                    )
                )
            ),
            "checks": checks,
            "representation": {
                "input": (
                    "10 normalized geometry variables"
                    if args.predictor_role == "geometry"
                    else _physical_spec_description(geometry_columns)
                ),
                "output": "upper-triangular reciprocal complex S4P over the full grid",
                "input_audit_order": "raw complex S4P reciprocity/passivity/content SHA before reciprocal symmetrization",
                "spectral_compression": "randomized PCA trained on OOD-train rows only",
                "regressor": f"ridge {args.predictor_role}-to-PCA-coefficients",
                "physical_projection": "reciprocity by construction, then per-frequency singular-value passivity projection",
            },
            "split_audit": result["split_audit"],
            "input_s4p_quality": input_quality,
            "metrics": result["metrics"],
            "acceptance_thresholds": {
                "configured": thresholds_configured,
                "input_quality_configured": input_quality_thresholds_configured,
                "max_test_complex_rmse": None if not thresholds_configured else float(args.max_test_complex_rmse),
                "max_input_reciprocity_error": (
                    None
                    if not math.isfinite(float(args.max_input_reciprocity_error))
                    else float(args.max_input_reciprocity_error)
                ),
                "max_input_passivity_excess": (
                    None
                    if not math.isfinite(float(args.max_input_passivity_excess))
                    else float(args.max_input_passivity_excess)
                ),
                "boundary": (
                    "Configured raw-input reciprocity/passivity thresholds reject the evidence before a model conclusion. "
                    "Without a predeclared finite model-error threshold the baseline remains COMPLETE_REVIEW_REQUIRED."
                ),
            },
            "artifacts": {
                "summary": str(summary_path),
                "weights": str(weights_path),
                "frequency_errors": str(frequency_csv),
                "plot": str(plot_path) if plot_status == "PASS" else "",
                "plot_status": plot_status,
                "report": str(report_path),
            },
            "limitations": [
                "This low-rank ridge surrogate is a baseline, not the final broadband neural architecture.",
                (
                    "The physical-spec mode is a spectral-expander baseline; it does not prove that one center-frequency descriptor uniquely determines post-resonance behavior."
                    if args.predictor_role == "physical_spec"
                    else "The geometry mode is a forward baseline and is not an inverse synthesis result."
                ),
                "Passivity projection is applied after prediction and its correction size is reported; it does not prove causality.",
                "Raw input reciprocity and passivity are audited before symmetrization; symmetrization cannot erase the recorded diagnostics or raw-content SHA.",
                "Inverse candidates still require DRC and real EMX validation, followed by sampled HFSS comparison.",
            ],
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"test_raw_complex_rmse={result['metrics']['test_raw_complex_rmse']}")
    print(f"summary={summary_path}")
    return 0 if status in {"PASS", "COMPLETE_REVIEW_REQUIRED"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--geometry-columns")
    parser.add_argument("--predictor-role", choices=("geometry", "physical_spec"), default="geometry")
    parser.add_argument("--expected-predictor-count", type=int)
    parser.add_argument("--split-reference-columns", default=DEFAULT_INPUT_COLUMNS)
    parser.add_argument("--min-rows", type=int, default=5000)
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--pca-rank", type=int, default=32)
    parser.add_argument("--pca-oversample", type=int, default=8)
    parser.add_argument("--pca-power-iterations", type=int, default=1)
    parser.add_argument("--ridge-alpha", type=float, default=1.0e-3)
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
    parser.add_argument("--max-input-reciprocity-error", type=float, default=math.inf)
    parser.add_argument("--max-input-passivity-excess", type=float, default=math.inf)
    parser.add_argument("--max-test-complex-rmse", type=float, default=math.inf)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_rows < 10 or args.max_rows < args.min_rows:
        parser.error("require 10 <= --min-rows <= --max-rows")
    if args.pca_rank < 1 or args.pca_oversample < 1 or args.pca_power_iterations < 0:
        parser.error("PCA dimensions and power iterations are invalid")
    if args.expected_predictor_count is None:
        args.expected_predictor_count = 10 if args.predictor_role == "geometry" else 4
    if args.expected_predictor_count < 1:
        parser.error("expected predictor count must be positive")
    if float(args.input_reciprocity_audit_tolerance) < 0.0 or float(args.input_passivity_audit_tolerance) < 0.0:
        parser.error("input S4P audit tolerances must be nonnegative")
    if float(args.max_input_reciprocity_error) < 0.0 or float(args.max_input_passivity_excess) < 0.0:
        parser.error("input S4P hard thresholds must be nonnegative")
    expected_start = float(args.expected_frequency_start_ghz)
    expected_stop = float(args.expected_frequency_stop_ghz)
    expected_step = float(args.expected_frequency_step_ghz)
    target = float(args.target_frequency_ghz)
    if not expected_start <= target <= expected_stop:
        parser.error("--target-frequency-ghz must lie inside the expected frequency grid")
    nearest_step = round((target - expected_start) / expected_step)
    aligned_target = expected_start + nearest_step * expected_step
    if abs(target - aligned_target) * 1.0e9 > float(args.frequency_tolerance_hz):
        parser.error("--target-frequency-ghz must align with the expected frequency grid")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _split_columns(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _physical_spec_contract(columns: list[str]) -> bool:
    names = [column.lower().removeprefix("input__") for column in columns]
    if not names or any("zin" in name for name in names):
        return False
    has_lp = any(name.startswith("lp_") or name == "lp" for name in names)
    has_ls = any(name.startswith("ls_") or name == "ls" for name in names)
    has_k = any("k_abs" in name or name.startswith("k_") or name == "k" for name in names)
    has_q_scalar = any(name.startswith("q_") and not name.startswith(("qp_", "qs_")) for name in names)
    has_q_pair = any(name.startswith("qp_") for name in names) and any(
        name.startswith("qs_") for name in names
    )
    return has_lp and has_ls and has_k and (has_q_scalar or has_q_pair)


def _physical_spec_description(columns: list[str]) -> str:
    names = [column.lower().removeprefix("input__") for column in columns]
    q_mode = "Qp/Qs" if any(name.startswith("qp_") for name in names) else "Q=min(Qp,Qs)"
    return f"{len(columns)} normalized physical specifications including Lp/Ls/{q_mode}/|K|"


def _geometry_columns(rows: list[dict[str, str]], explicit: str | None) -> list[str]:
    if explicit:
        return _split_columns(explicit)
    return sorted(column for column in (rows[0] if rows else {}) if column.startswith("geom__"))


def _deterministic_spread_sample(rows: list[dict[str, str]], maximum: int) -> list[dict[str, str]]:
    if len(rows) <= maximum:
        return list(rows)
    indices = np.linspace(0, len(rows) - 1, maximum, dtype=int)
    return [rows[int(index)] for index in indices]


def _load_dataset(
    rows: list[dict[str, str]],
    geometry_columns: list[str],
    split_columns: list[str],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    geometry_rows: list[list[float]] = []
    split_rows: list[list[float]] = []
    spectra: list[np.ndarray] = []
    row_identities: list[str] = []
    touchstone_content_digest = hashlib.sha256()
    reciprocal_training_content_digest = hashlib.sha256()
    input_reciprocity_errors: list[float] = []
    input_passivity_excesses: list[float] = []
    input_nonpassive_frequency_fractions: list[float] = []
    rejects: list[dict[str, str]] = []
    frequency_reference: np.ndarray | None = None
    for row_index, row in enumerate(rows):
        try:
            geometry = [_finite_float(row.get(column)) for column in geometry_columns]
            split_values = [_finite_float(row.get(column)) for column in split_columns]
            if any(value is None for value in geometry + split_values):
                raise ValueError("missing geometry or physical split values")
            path = Path(str(row.get("touchstone_path") or "")).expanduser()
            if not path.is_file():
                raise FileNotFoundError(path)
            touchstone = load_touchstone(path)
            s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
            frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
            _validate_touchstone(s_matrix, frequencies, args)
            if frequency_reference is None:
                frequency_reference = frequencies
            elif np.max(np.abs(frequencies - frequency_reference)) > float(args.frequency_tolerance_hz):
                raise ValueError("frequency grid differs from earlier training rows")
            reciprocity_error = float(np.max(np.abs(s_matrix - np.swapaxes(s_matrix, 1, 2))))
            raw_singular_values = np.linalg.svd(s_matrix, compute_uv=False)
            per_frequency_passivity_excess = np.maximum(
                np.max(raw_singular_values, axis=-1) - 1.0,
                0.0,
            )
            passivity_excess = float(np.max(per_frequency_passivity_excess))
            nonpassive_fraction = float(
                np.mean(
                    per_frequency_passivity_excess
                    > float(getattr(args, "input_passivity_audit_tolerance", 1.0e-3))
                )
            )
            reciprocal = 0.5 * (s_matrix + np.swapaxes(s_matrix, 1, 2))
            encoded = _encode_reciprocal_s(reciprocal)
            spectra.append(encoded)
            geometry_rows.append([float(value) for value in geometry if value is not None])
            split_rows.append([float(value) for value in split_values if value is not None])
            resolved_path = str(path.resolve())
            row_identities.append(resolved_path)
            touchstone_content_digest.update(resolved_path.encode("utf-8"))
            touchstone_content_digest.update(b"\0")
            touchstone_content_digest.update(np.asarray(frequencies, dtype=np.float64).tobytes())
            touchstone_content_digest.update(np.asarray(s_matrix.real, dtype=np.float64).tobytes())
            touchstone_content_digest.update(np.asarray(s_matrix.imag, dtype=np.float64).tobytes())
            reciprocal_training_content_digest.update(resolved_path.encode("utf-8"))
            reciprocal_training_content_digest.update(b"\0")
            reciprocal_training_content_digest.update(np.asarray(frequencies, dtype=np.float64).tobytes())
            reciprocal_training_content_digest.update(np.asarray(encoded, dtype=np.float32).tobytes())
            input_reciprocity_errors.append(reciprocity_error)
            input_passivity_excesses.append(passivity_excess)
            input_nonpassive_frequency_fractions.append(nonpassive_fraction)
        except Exception as exc:  # noqa: BLE001
            rejects.append({"row_index": str(row_index), "reason": f"{type(exc).__name__}: {exc}"})
    count = len(spectra)
    return {
        "count": count,
        "geometry": np.asarray(geometry_rows, dtype=float) if geometry_rows else np.empty((0, len(geometry_columns))),
        "split_x": np.asarray(split_rows, dtype=float) if split_rows else np.empty((0, len(split_columns))),
        "spectra": np.asarray(spectra, dtype=np.float32) if spectra else np.empty((0, 0), dtype=np.float32),
        "frequencies_hz": frequency_reference if frequency_reference is not None else np.empty(0),
        "frequency_grid_consistent": bool(count) and frequency_reference is not None,
        "row_identity_sha256": _string_sequence_sha256(row_identities),
        "touchstone_content_sha256": touchstone_content_digest.hexdigest() if count else "",
        "reciprocal_training_content_sha256": (
            reciprocal_training_content_digest.hexdigest() if count else ""
        ),
        "input_reciprocity_errors": np.asarray(input_reciprocity_errors, dtype=float),
        "input_passivity_excesses": np.asarray(input_passivity_excesses, dtype=float),
        "input_nonpassive_frequency_fractions": np.asarray(
            input_nonpassive_frequency_fractions,
            dtype=float,
        ),
        "frequency_grid_sha256": _array_sha256(
            frequency_reference if frequency_reference is not None else np.empty(0, dtype=float)
        ),
    }, rejects


def _string_sequence_sha256(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest() if values else ""


def _array_sha256(values: np.ndarray) -> str:
    array = np.asarray(values, dtype=np.float64)
    return hashlib.sha256(array.tobytes()).hexdigest() if array.size else ""


def _summarize_input_quality(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    reciprocity_source = dataset.get("input_reciprocity_errors")
    passivity_source = dataset.get("input_passivity_excesses")
    nonpassive_source = dataset.get("input_nonpassive_frequency_fractions")
    reciprocity = np.asarray(
        [] if reciprocity_source is None else reciprocity_source,
        dtype=float,
    )
    passivity = np.asarray(
        [] if passivity_source is None else passivity_source,
        dtype=float,
    )
    nonpassive_fractions = np.asarray(
        [] if nonpassive_source is None else nonpassive_source,
        dtype=float,
    )
    if not len(reciprocity) or len(reciprocity) != len(passivity):
        raise ValueError("raw input S4P quality diagnostics are incomplete")
    reciprocity_tolerance = float(args.input_reciprocity_audit_tolerance)
    passivity_tolerance = float(args.input_passivity_audit_tolerance)
    reciprocity_threshold = float(args.max_input_reciprocity_error)
    passivity_threshold = float(args.max_input_passivity_excess)
    reciprocity_max = float(np.max(reciprocity))
    passivity_max = float(np.max(passivity))
    return {
        "row_count": int(len(reciprocity)),
        "audit_stage": "raw complex S4P before reciprocal symmetrization",
        "raw_touchstone_content_sha256": str(dataset.get("touchstone_content_sha256") or ""),
        "reciprocal_training_content_sha256": str(
            dataset.get("reciprocal_training_content_sha256") or ""
        ),
        "reciprocity": {
            "audit_tolerance": reciprocity_tolerance,
            "max_error": reciprocity_max,
            "p95_error": float(np.quantile(reciprocity, 0.95)),
            "mean_error": float(np.mean(reciprocity)),
            "row_fraction_above_audit_tolerance": float(
                np.mean(reciprocity > reciprocity_tolerance)
            ),
            "hard_threshold": (
                None if not math.isfinite(reciprocity_threshold) else reciprocity_threshold
            ),
            "hard_threshold_pass": bool(
                not math.isfinite(reciprocity_threshold)
                or reciprocity_max <= reciprocity_threshold
            ),
        },
        "passivity": {
            "audit_tolerance": passivity_tolerance,
            "max_singular_value_excess": passivity_max,
            "p95_singular_value_excess": float(np.quantile(passivity, 0.95)),
            "mean_singular_value_excess": float(np.mean(passivity)),
            "row_fraction_above_audit_tolerance": float(
                np.mean(passivity > passivity_tolerance)
            ),
            "mean_nonpassive_frequency_fraction": float(
                np.mean(nonpassive_fractions)
            ),
            "p95_nonpassive_frequency_fraction": float(
                np.quantile(nonpassive_fractions, 0.95)
            ),
            "hard_threshold": (
                None if not math.isfinite(passivity_threshold) else passivity_threshold
            ),
            "hard_threshold_pass": bool(
                not math.isfinite(passivity_threshold)
                or passivity_max <= passivity_threshold
            ),
        },
        "scientific_boundary": (
            "Reciprocal encoding is a model representation, not evidence that the raw S4P was reciprocal. "
            "Raw diagnostics and raw-content SHA are recorded before symmetrization."
        ),
    }


def _validate_touchstone(s_matrix: np.ndarray, frequencies: np.ndarray, args: argparse.Namespace) -> None:
    expected_shape = (int(args.expected_frequency_points), int(args.expected_ports), int(args.expected_ports))
    if s_matrix.shape != expected_shape:
        raise ValueError(f"S shape {s_matrix.shape}, expected {expected_shape}")
    if not np.all(np.isfinite(s_matrix.real)) or not np.all(np.isfinite(s_matrix.imag)):
        raise ValueError("non-finite S matrix")
    expected = np.linspace(
        float(args.expected_frequency_start_ghz) * 1.0e9,
        float(args.expected_frequency_stop_ghz) * 1.0e9,
        int(args.expected_frequency_points),
    )
    if frequencies.shape != expected.shape or np.max(np.abs(frequencies - expected)) > float(args.frequency_tolerance_hz):
        raise ValueError("frequency grid mismatch")
    if abs(float(np.median(np.diff(frequencies))) - float(args.expected_frequency_step_ghz) * 1.0e9) > float(args.frequency_tolerance_hz):
        raise ValueError("frequency step mismatch")


def _encode_reciprocal_s(s_matrix: np.ndarray) -> np.ndarray:
    ports = s_matrix.shape[1]
    row_indices, column_indices = np.triu_indices(ports)
    upper = s_matrix[:, row_indices, column_indices]
    return np.concatenate([upper.real.reshape(-1), upper.imag.reshape(-1)]).astype(np.float32)


def _decode_reciprocal_s(encoded: np.ndarray, frequency_points: int, ports: int) -> np.ndarray:
    values = np.asarray(encoded, dtype=float)
    row_indices, column_indices = np.triu_indices(ports)
    upper_count = len(row_indices)
    half = frequency_points * upper_count
    real = values[:, :half].reshape(-1, frequency_points, upper_count)
    imag = values[:, half:].reshape(-1, frequency_points, upper_count)
    upper = real + 1j * imag
    matrices = np.zeros((len(values), frequency_points, ports, ports), dtype=np.complex128)
    matrices[:, :, row_indices, column_indices] = upper
    matrices[:, :, column_indices, row_indices] = upper
    return matrices


def _train_and_evaluate(dataset: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    geometry = np.asarray(dataset["geometry"], dtype=float)
    split_x = np.asarray(dataset["split_x"], dtype=float)
    spectra = np.asarray(dataset["spectra"], dtype=float)
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
    train = split["train"]
    test = split["test"]
    geometry_mean = np.mean(geometry[train], axis=0)
    geometry_scale = np.maximum(np.std(geometry[train], axis=0), 1.0e-12)
    x = (geometry - geometry_mean[None, :]) / geometry_scale[None, :]
    x_augmented = np.column_stack([np.ones(len(x)), x])

    spectrum_mean = np.mean(spectra[train], axis=0)
    spectrum_scale = np.maximum(np.std(spectra[train], axis=0), 1.0e-6)
    y = (spectra - spectrum_mean[None, :]) / spectrum_scale[None, :]
    components, singular_values = _randomized_components(
        y[train],
        rank=min(int(args.pca_rank), len(train) - 1, y.shape[1]),
        oversample=int(args.pca_oversample),
        power_iterations=int(args.pca_power_iterations),
        seed=int(args.seed),
    )
    train_coefficients = y[train] @ components.T
    gram = x_augmented[train].T @ x_augmented[train]
    penalty = np.eye(gram.shape[0]) * float(args.ridge_alpha)
    penalty[0, 0] = 0.0
    ridge_weights = np.linalg.solve(
        gram + penalty,
        x_augmented[train].T @ train_coefficients,
    )
    predicted_coefficients = x_augmented[test] @ ridge_weights
    predicted_normalized = predicted_coefficients @ components
    predicted_encoded = predicted_normalized * spectrum_scale[None, :] + spectrum_mean[None, :]
    truth_encoded = spectra[test]
    pca_floor_normalized = (y[test] @ components.T) @ components
    pca_floor_encoded = pca_floor_normalized * spectrum_scale[None, :] + spectrum_mean[None, :]
    ports = int(args.expected_ports)
    frequency_points = int(args.expected_frequency_points)
    prediction = _decode_reciprocal_s(predicted_encoded, frequency_points, ports)
    truth = _decode_reciprocal_s(truth_encoded, frequency_points, ports)
    pca_floor = _decode_reciprocal_s(pca_floor_encoded, frequency_points, ports)
    projected, correction = _passivity_project(prediction)
    raw_error = prediction - truth
    projected_error = projected - truth
    pca_error = pca_floor - truth
    raw_singular = np.linalg.svd(prediction, compute_uv=False)
    projected_singular = np.linalg.svd(projected, compute_uv=False)
    frequency_rows = []
    frequencies = np.asarray(dataset["frequencies_hz"], dtype=float)
    for index, frequency in enumerate(frequencies):
        frequency_rows.append(
            {
                "frequency_ghz": float(frequency / 1.0e9),
                "raw_complex_rmse": float(np.sqrt(np.mean(np.abs(raw_error[:, index]) ** 2))),
                "projected_complex_rmse": float(np.sqrt(np.mean(np.abs(projected_error[:, index]) ** 2))),
                "pca_floor_complex_rmse": float(np.sqrt(np.mean(np.abs(pca_error[:, index]) ** 2))),
                "raw_complex_mae": float(np.mean(np.abs(raw_error[:, index]))),
                "projected_complex_mae": float(np.mean(np.abs(projected_error[:, index]))),
                "pca_floor_complex_mae": float(np.mean(np.abs(pca_error[:, index]))),
                "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular[:, index]) - 1.0)),
            }
        )
    target_requested_hz = float(args.target_frequency_ghz) * 1.0e9
    target_index = int(np.argmin(np.abs(frequencies - target_requested_hz)))
    target_used_hz = float(frequencies[target_index])
    target_error_hz = abs(target_used_hz - target_requested_hz)
    if target_error_hz > float(args.frequency_tolerance_hz):
        raise ValueError(
            f"target frequency grid error {target_error_hz:g} Hz exceeds tolerance"
        )
    explained_energy = float(np.sum(singular_values**2) / max(np.sum(y[train] ** 2), 1.0e-12))
    metrics = {
        "test_row_count": int(len(test)),
        "pca_rank": int(len(components)),
        "pca_train_explained_energy_fraction": explained_energy,
        "test_pca_floor_complex_rmse": float(np.sqrt(np.mean(np.abs(pca_error) ** 2))),
        "test_raw_complex_rmse": float(np.sqrt(np.mean(np.abs(raw_error) ** 2))),
        "test_projected_complex_rmse": float(np.sqrt(np.mean(np.abs(projected_error) ** 2))),
        "test_raw_complex_mae": float(np.mean(np.abs(raw_error))),
        "test_projected_complex_mae": float(np.mean(np.abs(projected_error))),
        "target_frequency_requested_ghz": float(args.target_frequency_ghz),
        "target_frequency_used_ghz": target_used_hz / 1.0e9,
        "target_frequency_grid_error_hz": target_error_hz,
        "target_test_pca_floor_complex_rmse": float(
            np.sqrt(np.mean(np.abs(pca_error[:, target_index]) ** 2))
        ),
        "target_test_raw_complex_rmse": float(
            np.sqrt(np.mean(np.abs(raw_error[:, target_index]) ** 2))
        ),
        "target_test_projected_complex_rmse": float(
            np.sqrt(np.mean(np.abs(projected_error[:, target_index]) ** 2))
        ),
        "target_test_raw_complex_mae": float(np.mean(np.abs(raw_error[:, target_index]))),
        "target_test_projected_complex_mae": float(
            np.mean(np.abs(projected_error[:, target_index]))
        ),
        "raw_max_passivity_excess": float(max(0.0, np.max(raw_singular) - 1.0)),
        "projected_max_passivity_excess": float(max(0.0, np.max(projected_singular) - 1.0)),
        "passivity_projection_complex_rmse": float(np.sqrt(np.mean(np.abs(correction) ** 2))),
        "raw_reciprocity_error": float(np.max(np.abs(prediction - np.swapaxes(prediction, 2, 3)))),
        "boundary": (
            "Full-band and target-frequency complex-S errors, PCA compression floor, "
            "and physical-projection correction are reported separately."
        ),
    }
    weights = {
        "geometry_mean": geometry_mean,
        "geometry_scale": geometry_scale,
        "predictor_mean": geometry_mean,
        "predictor_scale": geometry_scale,
        "spectrum_mean": spectrum_mean,
        "spectrum_scale": spectrum_scale,
        "pca_components": components,
        "pca_singular_values": singular_values,
        "ridge_weights": ridge_weights,
        "frequencies_hz": frequencies,
    }
    return {"metrics": metrics, "frequency_rows": frequency_rows, "weights": weights, "split_audit": split_audit}


def _randomized_components(
    matrix: np.ndarray,
    *,
    rank: int,
    oversample: int,
    power_iterations: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    if rank < 1:
        raise ValueError("PCA rank must be positive after data-size clipping")
    rng = np.random.default_rng(seed)
    width = min(matrix.shape[1], rank + oversample)
    projection = rng.normal(size=(matrix.shape[1], width))
    sample = matrix @ projection
    for _ in range(power_iterations):
        basis, _ = np.linalg.qr(sample, mode="reduced")
        sample = matrix @ (matrix.T @ basis)
    basis, _ = np.linalg.qr(sample, mode="reduced")
    reduced = basis.T @ matrix
    _, singular_values, right = np.linalg.svd(reduced, full_matrices=False)
    return right[:rank], singular_values[:rank]


def _passivity_project(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    projected = np.empty_like(matrix)
    for row_index in range(matrix.shape[0]):
        for frequency_index in range(matrix.shape[1]):
            u, singular_values, vh = np.linalg.svd(matrix[row_index, frequency_index], full_matrices=False)
            projected[row_index, frequency_index] = (u * np.minimum(singular_values, 1.0)[None, :]) @ vh
    return projected, projected - matrix


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _base_summary(
    args: argparse.Namespace,
    training_csv: Path,
    out_dir: Path,
    geometry_columns: list[str],
    split_columns: list[str],
    dataset: dict[str, Any],
    rejects: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "out_dir": str(out_dir),
        "training_count": int(dataset.get("count") or 0),
        "geometry_columns": geometry_columns,
        "predictor_columns": geometry_columns,
        "predictor_role": args.predictor_role,
        "split_reference_columns": split_columns,
        "row_identity_sha256": str(dataset.get("row_identity_sha256") or ""),
        "touchstone_content_sha256": str(dataset.get("touchstone_content_sha256") or ""),
        "reciprocal_training_content_sha256": str(
            dataset.get("reciprocal_training_content_sha256") or ""
        ),
        "frequency_grid_sha256": str(dataset.get("frequency_grid_sha256") or ""),
        "rejected_rows": rejects[:100],
        "rejected_row_count": len(rejects),
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


def _write_plot(path: Path, rows: list[dict[str, Any]]) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    frequencies = [row["frequency_ghz"] for row in rows]
    figure, axis = plt.subplots(figsize=(8.2, 4.8), dpi=180)
    axis.plot(frequencies, [row["raw_complex_rmse"] for row in rows], label="Raw ridge/PCA", color="#c43c35")
    axis.plot(frequencies, [row["projected_complex_rmse"] for row in rows], label="After passivity projection", color="#2268b2")
    axis.plot(frequencies, [row["pca_floor_complex_rmse"] for row in rows], label="PCA representation floor", color="#16836b", linestyle="--")
    axis.set_xlabel("Frequency (GHz)")
    axis.set_ylabel("Complex S RMSE")
    axis.set_title("Broadband reciprocal S4P surrogate baseline")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _render_report(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or {}
    return "\n".join(
        [
            "# Broadband reciprocal S4P PCA/ridge baseline",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Real S4P rows used: `{data['training_count']}`",
            f"- Test raw complex-S RMSE: `{metrics.get('test_raw_complex_rmse')}`",
            f"- Test projected complex-S RMSE: `{metrics.get('test_projected_complex_rmse')}`",
            f"- PCA representation floor: `{metrics.get('test_pca_floor_complex_rmse')}`",
            f"- Target frequency: `{metrics.get('target_frequency_used_ghz')}` GHz",
            f"- Target raw complex-S RMSE: `{metrics.get('target_test_raw_complex_rmse')}`",
            f"- Target raw complex-S MAE: `{metrics.get('target_test_raw_complex_mae')}`",
            f"- Input raw reciprocity max error: `{(data.get('input_s4p_quality') or {}).get('reciprocity', {}).get('max_error')}`",
            f"- Input raw passivity max excess: `{(data.get('input_s4p_quality') or {}).get('passivity', {}).get('max_singular_value_excess')}`",
            "",
            data["limitations"][0],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
