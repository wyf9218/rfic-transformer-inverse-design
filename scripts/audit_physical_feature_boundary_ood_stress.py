#!/usr/bin/env python3
"""Audit boundary OOD error, conditional coverage, and specification stress.

The audit uses physical-cell-held-out tandem predictions and the saved frozen
tandem weights. It compares declared-range errors near physical boundaries
with interior targets, diagnoses conditional coverage of global conformal
intervals, and moves targets 1-10% toward their nearest declared boundary.
All stress results are proxy diagnostics, not real EMX labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
FEATURE_NAMES = tuple(column.removeprefix("input__") for column in INPUT_COLUMNS)
FEATURE_LOWER = np.asarray((0.5, 0.5, 5.0, 0.0), dtype=float)
FEATURE_UPPER = np.asarray((3.0, 3.0, 25.0, 0.8), dtype=float)
FEATURE_SPANS = FEATURE_UPPER - FEATURE_LOWER
METHODS = {"forward_proxy": "forward", "tandem_inverse": "reconstructed"}
EXPECTED_GEOMETRY_COUNT = 10


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tandem_path = Path(args.tandem_summary).expanduser().resolve()
    tandem = _read_json(tandem_path)
    predictions_path = _resolve_path(args.predictions_csv or tandem.get("test_predictions_csv"), tandem_path.parent)
    weights_path = _resolve_path(args.weights_npz or tandem.get("weights_npz"), tandem_path.parent)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    predictions = _load_predictions(predictions_path, args)
    model = _load_model(weights_path)
    analysis = _analyze(predictions, model, tandem, args)
    checks = _checks(predictions, model, tandem, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    group_metrics_path = out_dir / "boundary_ood_group_metrics.csv"
    coverage_path = out_dir / "boundary_ood_conditional_coverage.csv"
    stress_path = out_dir / "boundary_ood_specification_stress.csv"
    figure_path = out_dir / "boundary_ood_stress_audit.png"
    summary_path = out_dir / "boundary_ood_stress_summary.json"
    report_path = out_dir / "boundary_ood_stress_report.md"
    _write_csv(group_metrics_path, analysis.get("group_metric_rows") or [])
    _write_csv(coverage_path, analysis.get("coverage_rows") or [])
    _write_csv(stress_path, analysis.get("stress_rows") or [])
    if analysis.get("available") is True:
        _plot(figure_path, analysis)

    public_analysis = {
        key: value
        for key, value in analysis.items()
        if key not in {"group_metric_rows", "coverage_rows", "stress_rows"}
    }
    recommendation = _recommendation(analysis, args) if status == "PASS" else {
        "decision": "DO_NOT_INTERPRET_FIX_BOUNDARY_AUDIT_INPUTS",
        "all_predeclared_robustness_gates_pass": False,
    }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "BOUNDARY_OOD_STRESS_EVIDENCE_READY_REVIEW_METRICS"
            if status == "PASS"
            else "DO_NOT_INTERPRET_BOUNDARY_OOD_STRESS"
        ),
        "tandem_summary": str(tandem_path),
        "tandem_summary_sha256": _sha256(tandem_path),
        "predictions_csv": str(predictions_path),
        "predictions_csv_sha256": _sha256(predictions_path),
        "weights_npz": str(weights_path),
        "weights_npz_sha256": _sha256(weights_path),
        "input_columns": list(INPUT_COLUMNS),
        "feature_ranges": {
            name: {"min": float(FEATURE_LOWER[index]), "max": float(FEATURE_UPPER[index])}
            for index, name in enumerate(FEATURE_NAMES)
        },
        "partition_contract": {
            "boundary_fraction": float(args.boundary_fraction),
            "interior_fraction": float(args.interior_fraction),
            "boundary_definition": "minimum normalized distance to any declared physical bound <= boundary_fraction",
            "interior_definition": "minimum normalized distance to every declared physical bound >= interior_fraction",
            "middle_rows_are_reported_but_not_used_for_boundary_vs_interior_ratio": True,
        },
        "checks": checks,
        "analysis": public_analysis,
        "recommendation": recommendation,
        "artifacts": {
            "group_metrics_csv": str(group_metrics_path),
            "conditional_coverage_csv": str(coverage_path),
            "specification_stress_csv": str(stress_path),
            "figure_png": str(figure_path),
            "report_md": str(report_path),
        },
        "local_literature_basis": [
            {
                "source": "TMTT-2026-02-0420_Proof_hi.pdf, robustness experiment",
                "adaptation": "Stress inverse synthesis under 1-10% input uncertainty while preserving the frozen forward-consistency contract.",
            },
            {
                "source": "Tandem_Neural_Network_Based_Design_of_Multiband_Antennas.pdf",
                "adaptation": "Report response robustness separately from manufacturability constraints.",
            },
            {
                "source": "5.Integrated_Transformers_Basic_concepts_design_intuition_and_practical_considerations.pdf",
                "adaptation": "Treat physical-range edges and resonance-adjacent behavior as a separate reliability region.",
            },
        ],
        "scientific_boundary": (
            "PASS means the predeclared boundary/interior partitions, global-to-conditional coverage diagnostics, "
            "and frozen-proxy stress calculations completed on unique physical-cell OOD rows. Conditional coverage "
            "has no automatic conformal guarantee. Stress predictions are not real EMX or HFSS validation, and this "
            "audit cannot change the production geometry, port, process, frequency, or target-range contracts."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"boundary_rows={public_analysis.get('partition_counts', {}).get('boundary', 0)}")
    print(f"interior_rows={public_analysis.get('partition_counts', {}).get('interior', 0)}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--predictions-csv")
    parser.add_argument("--weights-npz")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-source-rows", type=int, default=900_000)
    parser.add_argument("--min-prediction-rows", type=int, default=5_000)
    parser.add_argument("--min-boundary-rows", type=int, default=256)
    parser.add_argument("--min-interior-rows", type=int, default=256)
    parser.add_argument("--min-group-evaluation-rows", type=int, default=128)
    parser.add_argument("--boundary-fraction", type=float, default=0.10)
    parser.add_argument("--interior-fraction", type=float, default=0.20)
    parser.add_argument("--calibration-fraction", type=float, default=0.50)
    parser.add_argument("--coverage-levels", default="0.90,0.95")
    parser.add_argument("--stress-levels", default="0.01,0.03,0.05,0.10")
    parser.add_argument("--max-stress-rows", type=int, default=2048)
    parser.add_argument("--geometry-saturation-fraction", type=float, default=0.01)
    parser.add_argument("--max-boundary-interior-rmse-ratio", type=float, default=1.50)
    parser.add_argument("--max-conditional-coverage-shortfall", type=float, default=0.10)
    parser.add_argument("--max-final-stress-range-rmse", type=float, default=0.10)
    parser.add_argument("--max-final-saturation-sample-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    args._coverage_levels = _parse_levels(args.coverage_levels, upper=1.0, allow_zero=False)
    args._stress_levels = _parse_levels(args.stress_levels, upper=0.5, allow_zero=True)
    if args.min_source_rows < 1 or min(
        args.min_prediction_rows,
        args.min_boundary_rows,
        args.min_interior_rows,
        args.min_group_evaluation_rows,
    ) < 1:
        parser.error("row minimums must be positive")
    if not 0.0 < args.boundary_fraction < args.interior_fraction < 0.5:
        parser.error("fractions must satisfy 0 < boundary < interior < 0.5")
    if not 0.0 < args.calibration_fraction < 1.0:
        parser.error("calibration fraction must be in (0,1)")
    if args.max_stress_rows < 1 or not 0.0 <= args.geometry_saturation_fraction < 0.5:
        parser.error("stress rows and geometry saturation fraction are invalid")
    thresholds = (
        args.max_boundary_interior_rmse_ratio,
        args.max_conditional_coverage_shortfall,
        args.max_final_stress_range_rmse,
        args.max_final_saturation_sample_fraction,
    )
    if any(not math.isfinite(value) or value < 0.0 for value in thresholds):
        parser.error("robustness thresholds must be finite and nonnegative")
    return args


def _parse_levels(raw: str, *, upper: float, allow_zero: bool) -> list[float]:
    values = [float(item.strip()) for item in str(raw).split(",") if item.strip()]
    lower_ok = (lambda value: value >= 0.0) if allow_zero else (lambda value: value > 0.0)
    if not values or any(not math.isfinite(value) or not lower_ok(value) or value >= upper for value in values):
        raise argparse.ArgumentTypeError(f"levels must be finite and within the allowed range below {upper}")
    return sorted(set(values))


def _load_predictions(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path_exists": path.is_file(),
        "columns_present": False,
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "out_of_declared_range_count": 0,
        "duplicate_source_index_count": 0,
        "target": np.empty((0, len(FEATURE_NAMES))),
        "forward": np.empty((0, len(FEATURE_NAMES))),
        "reconstructed": np.empty((0, len(FEATURE_NAMES))),
        "source_indices": np.empty(0, dtype=np.int64),
        "calibration_mask": np.empty(0, dtype=bool),
    }
    if not path.is_file():
        return result
    target_columns = tuple(f"target__{name}" for name in FEATURE_NAMES)
    forward_columns = tuple(f"forward__{name}" for name in FEATURE_NAMES)
    reconstructed_columns = tuple(f"reconstructed__{name}" for name in FEATURE_NAMES)
    targets, forwards, reconstructions, source_indices = [], [], [], []
    seen = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(target_columns) | set(forward_columns) | set(reconstructed_columns) | {"source_row_index"}
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for row in reader:
            result["row_count"] += 1
            target = _float_row(row, target_columns)
            forward = _float_row(row, forward_columns)
            reconstructed = _float_row(row, reconstructed_columns)
            source_index = _integer(row.get("source_row_index"))
            if target is None or forward is None or reconstructed is None or source_index is None:
                result["invalid_count"] += 1
                continue
            if np.any(target < FEATURE_LOWER - 1.0e-12) or np.any(target > FEATURE_UPPER + 1.0e-12):
                result["out_of_declared_range_count"] += 1
                continue
            if source_index in seen:
                result["duplicate_source_index_count"] += 1
                continue
            seen.add(source_index)
            targets.append(target)
            forwards.append(forward)
            reconstructions.append(reconstructed)
            source_indices.append(source_index)
    if targets:
        result["target"] = np.asarray(targets, dtype=float)
        result["forward"] = np.asarray(forwards, dtype=float)
        result["reconstructed"] = np.asarray(reconstructions, dtype=float)
        result["source_indices"] = np.asarray(source_indices, dtype=np.int64)
        result["calibration_mask"] = np.asarray(
            [_calibration_member(index, int(args.seed), float(args.calibration_fraction)) for index in source_indices],
            dtype=bool,
        )
    result["valid_count"] = len(targets)
    return result


def _load_model(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "error": "", "path": str(path)}
    if not path.is_file():
        result["error"] = "weights NPZ is missing"
        return result
    try:
        with np.load(path, allow_pickle=False) as archive:
            forward_weights = _numbered_arrays(archive, "forward_weight_")
            forward_biases = _numbered_arrays(archive, "forward_bias_")
            inverse_weights = _numbered_arrays(archive, "inverse_weight_")
            inverse_biases = _numbered_arrays(archive, "inverse_bias_")
            normalization = {
                key: np.asarray(archive[f"normalization__{key}"], dtype=float)
                for key in ("x_mean", "x_scale", "y_mean", "y_scale", "geometry_lower", "geometry_upper")
                if f"normalization__{key}" in archive
            }
        _validate_layers(forward_weights, forward_biases, "forward")
        _validate_layers(inverse_weights, inverse_biases, "inverse")
        result.update(
            {
                "available": True,
                "forward_weights": forward_weights,
                "forward_biases": forward_biases,
                "inverse_weights": inverse_weights,
                "inverse_biases": inverse_biases,
                "normalization": normalization,
            }
        )
    except Exception as exc:  # noqa: BLE001 - exact evidence error is recorded.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _validate_layers(weights: list[np.ndarray], biases: list[np.ndarray], name: str) -> None:
    if not weights or len(weights) != len(biases):
        raise ValueError(f"{name} weights and biases are incomplete")
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        if weight.ndim != 2 or bias.shape != (weight.shape[1],):
            raise ValueError(f"invalid {name} layer {index}")
        if index and weights[index - 1].shape[1] != weight.shape[0]:
            raise ValueError(f"{name} layer width mismatch")
        if not np.isfinite(weight).all() or not np.isfinite(bias).all():
            raise ValueError(f"non-finite {name} value")


def _numbered_arrays(archive: Any, prefix: str) -> list[np.ndarray]:
    result = []
    index = 0
    while f"{prefix}{index}" in archive:
        result.append(np.asarray(archive[f"{prefix}{index}"], dtype=float))
        index += 1
    return result


def _analyze(
    rows: dict[str, Any],
    model: dict[str, Any],
    tandem: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if rows.get("valid_count", 0) < 1:
        return {"available": False, "group_metric_rows": [], "coverage_rows": [], "stress_rows": []}
    target = rows["target"]
    normalized_target = (target - FEATURE_LOWER[None, :]) / FEATURE_SPANS[None, :]
    edge_distance = np.min(np.minimum(normalized_target, 1.0 - normalized_target), axis=1)
    boundary_mask = edge_distance <= float(args.boundary_fraction) + 1.0e-12
    interior_mask = edge_distance >= float(args.interior_fraction) - 1.0e-12
    middle_mask = ~(boundary_mask | interior_mask)
    evaluation_mask = ~rows["calibration_mask"]
    groups = {"all": np.ones(len(target), dtype=bool), "boundary": boundary_mask, "interior": interior_mask}

    group_rows = []
    group_summary: dict[str, Any] = {}
    for method, key in METHODS.items():
        prediction = rows[key]
        error = prediction - target
        group_summary[method] = {}
        for group, mask in groups.items():
            group_error = error[mask]
            range_error = group_error / FEATURE_SPANS[None, :]
            metrics = {
                "row_count": int(np.sum(mask)),
                "range_rmse": float(np.sqrt(np.mean(range_error**2))) if len(group_error) else None,
                "range_mae": float(np.mean(np.abs(range_error))) if len(group_error) else None,
                "per_feature_physical_mae": {
                    feature: float(np.mean(np.abs(group_error[:, index]))) if len(group_error) else None
                    for index, feature in enumerate(FEATURE_NAMES)
                },
            }
            group_summary[method][group] = metrics
            group_rows.append({"method": method, "group": group, **{k: v for k, v in metrics.items() if k != "per_feature_physical_mae"}})
        boundary_rmse = group_summary[method]["boundary"]["range_rmse"]
        interior_rmse = group_summary[method]["interior"]["range_rmse"]
        group_summary[method]["boundary_to_interior_rmse_ratio"] = (
            float(boundary_rmse / max(interior_rmse, 1.0e-18))
            if boundary_rmse is not None and interior_rmse is not None
            else None
        )

    coverage_rows = []
    coverage_summary: dict[str, Any] = {}
    for method, key in METHODS.items():
        residual = np.abs(rows[key] - target)
        method_records = []
        for feature_index, feature in enumerate(FEATURE_NAMES):
            calibration_residual = residual[rows["calibration_mask"], feature_index]
            for level in args._coverage_levels:
                quantile = _conformal_quantile(calibration_residual, float(level))
                for group, group_mask in groups.items():
                    mask = evaluation_mask & group_mask
                    empirical = float(np.mean(residual[mask, feature_index] <= quantile)) if np.any(mask) else None
                    record = {
                        "method": method,
                        "feature": feature,
                        "nominal_coverage": float(level),
                        "group": group,
                        "calibration_count": int(np.sum(rows["calibration_mask"])),
                        "evaluation_count": int(np.sum(mask)),
                        "half_width_physical": float(quantile),
                        "empirical_coverage": empirical,
                        "coverage_gap": None if empirical is None else float(empirical - level),
                    }
                    coverage_rows.append(record)
                    method_records.append(record)
        boundary_gaps = [
            item["coverage_gap"] for item in method_records if item["group"] == "boundary" and item["coverage_gap"] is not None
        ]
        interior_gaps = [
            item["coverage_gap"] for item in method_records if item["group"] == "interior" and item["coverage_gap"] is not None
        ]
        coverage_summary[method] = {
            "minimum_boundary_coverage_gap": float(min(boundary_gaps)) if boundary_gaps else None,
            "minimum_interior_coverage_gap": float(min(interior_gaps)) if interior_gaps else None,
            "maximum_boundary_shortfall": float(max(0.0, -min(boundary_gaps))) if boundary_gaps else None,
            "maximum_interior_shortfall": float(max(0.0, -min(interior_gaps))) if interior_gaps else None,
        }

    stress = _stress(model, rows, normalized_target, evaluation_mask, args)
    return {
        "available": True,
        "partition_counts": {
            "all": int(len(target)),
            "boundary": int(np.sum(boundary_mask)),
            "interior": int(np.sum(interior_mask)),
            "middle": int(np.sum(middle_mask)),
            "evaluation_boundary": int(np.sum(evaluation_mask & boundary_mask)),
            "evaluation_interior": int(np.sum(evaluation_mask & interior_mask)),
        },
        "partition_fingerprint_sha256": _partition_fingerprint(rows["source_indices"], boundary_mask, interior_mask),
        "group_metrics": group_summary,
        "conditional_coverage": coverage_summary,
        "stress": {
            **{key: value for key, value in stress.items() if key != "rows"},
            "levels": stress.get("rows") or [],
        },
        "group_metric_rows": group_rows,
        "coverage_rows": coverage_rows,
        "stress_rows": stress.get("rows") or [],
    }


def _stress(
    model: dict[str, Any],
    rows: dict[str, Any],
    physical_normalized: np.ndarray,
    evaluation_mask: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if model.get("available") is not True:
        return {"available": False, "rows": []}
    norm = model["normalization"]
    geometry_count = int(model["inverse_weights"][-1].shape[1])
    required_shapes = {
        "x_mean": (len(INPUT_COLUMNS),),
        "x_scale": (len(INPUT_COLUMNS),),
        "y_mean": (geometry_count,),
        "y_scale": (geometry_count,),
        "geometry_lower": (geometry_count,),
        "geometry_upper": (geometry_count,),
    }
    if any(np.asarray(norm.get(key, [])).shape != shape for key, shape in required_shapes.items()):
        return {"available": False, "rows": [], "error": "normalization shape mismatch"}
    if model["forward_weights"][0].shape[0] != geometry_count or model["forward_weights"][-1].shape[1] != len(INPUT_COLUMNS):
        return {"available": False, "rows": [], "error": "model dimension mismatch"}
    if model["inverse_weights"][0].shape[0] != len(INPUT_COLUMNS) or model["inverse_weights"][-1].shape[1] != geometry_count:
        return {"available": False, "rows": [], "error": "inverse dimension mismatch"}
    if np.any(norm["x_scale"] <= 0.0) or np.any(norm["y_scale"] <= 0.0):
        return {"available": False, "rows": [], "error": "nonpositive normalization scale"}
    lower, upper = norm["geometry_lower"], norm["geometry_upper"]
    geometry_span = upper - lower
    if np.any(geometry_span <= 0.0):
        return {"available": False, "rows": [], "error": "nonpositive geometry span"}

    candidates = np.flatnonzero(evaluation_mask)
    if len(candidates) > int(args.max_stress_rows):
        order = np.argsort(rows["source_indices"][candidates], kind="stable")
        candidates = candidates[order[np.linspace(0, len(order) - 1, int(args.max_stress_rows), dtype=int)]]
    clean_physical = rows["target"][candidates]
    clean_x = (clean_physical - norm["x_mean"][None, :]) / norm["x_scale"][None, :]
    clean_geometry = _predict_inverse(
        clean_x,
        model["inverse_weights"],
        model["inverse_biases"],
        lower,
        upper,
    )
    direction = np.where(physical_normalized[candidates] < 0.5, -1.0, 1.0)
    records = []
    saturation_fraction = float(args.geometry_saturation_fraction)
    for level in args._stress_levels:
        stressed_unit = np.clip(physical_normalized[candidates] + direction * float(level), 0.0, 1.0)
        stressed_physical = FEATURE_LOWER[None, :] + stressed_unit * FEATURE_SPANS[None, :]
        stressed_x = (stressed_physical - norm["x_mean"][None, :]) / norm["x_scale"][None, :]
        geometry = _predict_inverse(
            stressed_x,
            model["inverse_weights"],
            model["inverse_biases"],
            lower,
            upper,
        )
        reconstructed_x = _predict(geometry, model["forward_weights"], model["forward_biases"])
        reconstructed_physical = reconstructed_x * norm["x_scale"][None, :] + norm["x_mean"][None, :]
        range_error = (reconstructed_physical - stressed_physical) / FEATURE_SPANS[None, :]
        geometry_drift = (geometry - clean_geometry) / geometry_span[None, :]
        unit_geometry = (geometry - lower[None, :]) / geometry_span[None, :]
        saturation = (unit_geometry <= saturation_fraction) | (unit_geometry >= 1.0 - saturation_fraction)
        records.append(
            {
                "outward_stress_fraction_of_declared_span": float(level),
                "audit_row_count": int(len(candidates)),
                "reconstructed_target_range_rmse": float(np.sqrt(np.mean(range_error**2))),
                "reconstructed_target_range_mae": float(np.mean(np.abs(range_error))),
                "geometry_drift_normalized_rmse": float(np.sqrt(np.mean(geometry_drift**2))),
                "geometry_saturation_element_fraction": float(np.mean(saturation)),
                "geometry_saturation_sample_fraction": float(np.mean(np.any(saturation, axis=1))),
                "target_element_at_declared_bound_fraction": float(np.mean((stressed_unit <= 0.0) | (stressed_unit >= 1.0))),
            }
        )
    return {
        "available": bool(records),
        "audit_row_count": int(len(candidates)),
        "selection": "deterministic_even_source_index_subset_of_held_out_evaluation_rows",
        "direction": "outward_toward_nearest_declared_physical_bound_per_feature",
        "geometry_saturation_fraction": saturation_fraction,
        "rows": records,
    }


def _checks(
    rows: dict[str, Any],
    model: dict[str, Any],
    tandem: dict[str, Any],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    split = tandem.get("split_audit") or {}
    partition = analysis.get("partition_counts") or {}
    stress = analysis.get("stress") or {}
    coverage_rows = analysis.get("coverage_rows") or []
    return {
        "tandem_summary_reviewable": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "formal_input_contract": tuple(tandem.get("input_columns") or ()) == INPUT_COLUMNS,
        "ten_geometry_outputs": len(tandem.get("geometry_columns") or []) == EXPECTED_GEOMETRY_COUNT,
        "source_rows_meet_900k_stage_minimum": int(tandem.get("training_count") or 0) >= int(args.min_source_rows),
        "physical_cell_ood_split": split.get("split_mode") == "physical_cell_grouped"
        and int(split.get("physical_cell_overlap_count") or 0) == 0,
        "prediction_columns_present": rows.get("columns_present") is True,
        "prediction_rows_meet_minimum": int(rows.get("valid_count") or 0) >= int(args.min_prediction_rows),
        "prediction_rows_finite_unique_in_range": int(rows.get("invalid_count") or 0) == 0
        and int(rows.get("out_of_declared_range_count") or 0) == 0
        and int(rows.get("duplicate_source_index_count") or 0) == 0,
        "saved_tandem_model_available": model.get("available") is True,
        "analysis_available": analysis.get("available") is True,
        "boundary_rows_meet_minimum": int(partition.get("boundary") or 0) >= int(args.min_boundary_rows),
        "interior_rows_meet_minimum": int(partition.get("interior") or 0) >= int(args.min_interior_rows),
        "boundary_interior_disjoint": int(partition.get("boundary") or 0)
        + int(partition.get("interior") or 0)
        + int(partition.get("middle") or 0)
        == int(partition.get("all") or -1),
        "boundary_evaluation_rows_meet_minimum": int(partition.get("evaluation_boundary") or 0)
        >= int(args.min_group_evaluation_rows),
        "interior_evaluation_rows_meet_minimum": int(partition.get("evaluation_interior") or 0)
        >= int(args.min_group_evaluation_rows),
        "all_conditional_coverage_records_present": len(coverage_rows)
        == len(METHODS) * len(FEATURE_NAMES) * len(args._coverage_levels) * 3,
        "stress_levels_complete": stress.get("available") is True
        and len(analysis.get("stress_rows") or []) == len(args._stress_levels),
    }


def _recommendation(analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    tandem_metrics = (analysis.get("group_metrics") or {}).get("tandem_inverse") or {}
    ratio = tandem_metrics.get("boundary_to_interior_rmse_ratio")
    coverage = (analysis.get("conditional_coverage") or {}).get("tandem_inverse") or {}
    shortfall = coverage.get("maximum_boundary_shortfall")
    stress_rows = analysis.get("stress_rows") or []
    final_stress = stress_rows[-1] if stress_rows else {}
    gates = {
        "boundary_to_interior_rmse_ratio": ratio is not None
        and float(ratio) <= float(args.max_boundary_interior_rmse_ratio),
        "boundary_conditional_coverage_shortfall": shortfall is not None
        and float(shortfall) <= float(args.max_conditional_coverage_shortfall),
        "final_stress_range_rmse": _finite(final_stress.get("reconstructed_target_range_rmse")) is not None
        and float(final_stress["reconstructed_target_range_rmse"]) <= float(args.max_final_stress_range_rmse),
        "final_geometry_saturation_sample_fraction": _finite(
            final_stress.get("geometry_saturation_sample_fraction")
        )
        is not None
        and float(final_stress["geometry_saturation_sample_fraction"])
        <= float(args.max_final_saturation_sample_fraction),
    }
    all_pass = all(gates.values())
    return {
        "decision": (
            "BOUNDARY_PROXY_ROBUSTNESS_GATES_PASS_STILL_REQUIRE_REAL_EMX"
            if all_pass
            else "BOUNDARY_WEAKNESS_DETECTED_PRIORITIZE_REAL_EMX_EDGE_VALIDATION"
        ),
        "all_predeclared_robustness_gates_pass": all_pass,
        "gates": gates,
        "observed": {
            "boundary_to_interior_rmse_ratio": ratio,
            "maximum_boundary_coverage_shortfall": shortfall,
            "final_stress_range_rmse": final_stress.get("reconstructed_target_range_rmse"),
            "final_geometry_saturation_sample_fraction": final_stress.get(
                "geometry_saturation_sample_fraction"
            ),
        },
        "boundary": "This recommendation is proxy/OOD diagnostic evidence only and cannot replace DRC or real EMX edge-target validation.",
    }


def _predict_inverse(
    values: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    raw = _predict(values, weights, biases)
    clipped = np.clip(raw, -40.0, 40.0)
    sigmoid = 1.0 / (1.0 + np.exp(-clipped))
    return lower[None, :] + (upper - lower)[None, :] * sigmoid


def _predict(values: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    activation = np.asarray(values, dtype=float)
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        activation = activation @ weight + bias[None, :]
        if index < len(weights) - 1:
            activation = _gelu(activation)
    return activation


def _gelu(value: np.ndarray) -> np.ndarray:
    return 0.5 * value * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) * (value + 0.044715 * value**3)))


def _conformal_quantile(values: np.ndarray, coverage: float) -> float:
    sorted_values = np.sort(np.asarray(values, dtype=float))
    if not len(sorted_values):
        return math.nan
    rank = min(max(int(math.ceil((len(sorted_values) + 1) * coverage)), 1), len(sorted_values))
    return float(sorted_values[rank - 1])


def _calibration_member(source_index: int, seed: int, fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}|{source_index}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64) < fraction


def _partition_fingerprint(indices: np.ndarray, boundary: np.ndarray, interior: np.ndarray) -> str:
    digest = hashlib.sha256()
    for source_index, is_boundary, is_interior in sorted(zip(indices.tolist(), boundary.tolist(), interior.tolist())):
        group = "B" if is_boundary else ("I" if is_interior else "M")
        digest.update(f"{int(source_index)}:{group}\n".encode("ascii"))
    return digest.hexdigest()


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(13.0, 8.0), constrained_layout=True)
    figure.patch.set_facecolor("white")
    methods = list(METHODS)
    colors = {"forward_proxy": "#1769aa", "tandem_inverse": "#cf5c36"}
    x = np.arange(2)
    width = 0.34
    for method_index, method in enumerate(methods):
        group = analysis["group_metrics"][method]
        values = [group["boundary"]["range_rmse"], group["interior"]["range_rmse"]]
        axes[0, 0].bar(x + (method_index - 0.5) * width, values, width=width, label=method, color=colors[method])
    axes[0, 0].set_xticks(x, ["Boundary", "Interior"])
    axes[0, 0].set_ylabel("Fixed-range RMSE")
    axes[0, 0].set_title("Held-out OOD error")
    axes[0, 0].legend(frameon=False)

    coverage_rows = [
        row for row in analysis["coverage_rows"] if row["method"] == "tandem_inverse" and row["group"] in {"boundary", "interior"}
    ]
    labels = [f"{row['feature']} {int(100 * row['nominal_coverage'])}%" for row in coverage_rows if row["group"] == "boundary"]
    boundary = [row["coverage_gap"] for row in coverage_rows if row["group"] == "boundary"]
    interior = [row["coverage_gap"] for row in coverage_rows if row["group"] == "interior"]
    positions = np.arange(len(labels))
    axes[0, 1].bar(positions - 0.18, boundary, width=0.36, label="Boundary", color="#7b4ab5")
    axes[0, 1].bar(positions + 0.18, interior, width=0.36, label="Interior", color="#2a9d8f")
    axes[0, 1].axhline(0.0, color="#333333", linewidth=1.0)
    axes[0, 1].set_xticks(positions, labels, rotation=35, ha="right")
    axes[0, 1].set_ylabel("Empirical - nominal coverage")
    axes[0, 1].set_title("Global interval conditional coverage")
    axes[0, 1].legend(frameon=False)

    stress = analysis["stress_rows"]
    levels = [100.0 * row["outward_stress_fraction_of_declared_span"] for row in stress]
    axes[1, 0].plot(levels, [row["reconstructed_target_range_rmse"] for row in stress], marker="o", color="#d1495b")
    axes[1, 0].set_xlabel("Outward target stress (% declared span)")
    axes[1, 0].set_ylabel("Frozen-proxy range RMSE")
    axes[1, 0].set_title("Specification reconstruction stress")

    axes[1, 1].plot(levels, [row["geometry_drift_normalized_rmse"] for row in stress], marker="o", label="Geometry drift", color="#1769aa")
    axes[1, 1].plot(levels, [row["geometry_saturation_sample_fraction"] for row in stress], marker="s", label="Saturated samples", color="#cf5c36")
    axes[1, 1].set_xlabel("Outward target stress (% declared span)")
    axes[1, 1].set_ylabel("Fraction / normalized RMSE")
    axes[1, 1].set_title("Geometry stability")
    axes[1, 1].legend(frameon=False)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.22)
        axis.set_facecolor("white")
    figure.suptitle("900k boundary OOD and specification-stress audit", fontsize=15, fontweight="bold")
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    recommendation = payload.get("recommendation") or {}
    return "\n".join(
        [
            "# 900k boundary OOD and specification-stress audit",
            "",
            f"- Overall status: `{payload['overall_status']}`",
            f"- Decision: `{payload['decision']}`",
            f"- Partition counts: `{analysis.get('partition_counts')}`",
            f"- Recommendation: `{recommendation.get('decision')}`",
            "",
            "Global conformal coverage is marginal, not conditional. Boundary and stress diagnostics remain frozen-proxy evidence until edge targets pass DRC and real EMX validation.",
            "",
        ]
    )


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


def _float_row(row: dict[str, Any], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    return None if any(value is None for value in values) else np.asarray(values, dtype=float)


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None and float(number).is_integer() else None


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _resolve_path(raw: Any, base: Path) -> Path:
    if not raw:
        return base / "__missing__"
    path = Path(str(raw)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
