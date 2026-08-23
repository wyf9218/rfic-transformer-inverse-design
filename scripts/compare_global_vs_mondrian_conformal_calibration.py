#!/usr/bin/env python3
"""Compare global and fixed-cell Mondrian split-conformal intervals."""

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
PROJECT_LOWER = np.asarray((0.5, 0.5, 5.0, 0.0), dtype=float)
PROJECT_UPPER = np.asarray((3.0, 3.0, 25.0, 0.8), dtype=float)
FEATURE_SPANS = PROJECT_UPPER - PROJECT_LOWER
METHODS = {"forward_proxy": "forward", "tandem_inverse": "reconstructed"}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tandem_path = Path(args.tandem_summary).expanduser().resolve()
    global_path = Path(args.global_summary).expanduser().resolve()
    tandem = _read_json(tandem_path)
    global_summary = _read_json(global_path)
    predictions_path = _resolve_path(
        args.predictions_csv or tandem.get("test_predictions_csv"), tandem_path.parent
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_predictions(predictions_path, args)
    analysis = _compare(rows, args)
    checks = _checks(tandem, global_summary, rows, analysis, predictions_path, tandem_path, args)
    status = "PASS" if all(checks.values()) else "FAIL"
    recommendation = _recommendation(analysis, args) if status == "PASS" else {
        "decision": "DO_NOT_USE_MONDRIAN_COMPARISON",
        "criteria_pass": False,
        "criteria": {},
    }

    metrics_csv = out_dir / "physical_feature_mondrian_conformal_comparison_metrics.csv"
    cell_metrics_csv = out_dir / "physical_feature_mondrian_conformal_cell_metrics.csv"
    figure_path = out_dir / "physical_feature_mondrian_conformal_comparison.png"
    summary_path = out_dir / "physical_feature_mondrian_conformal_comparison_summary.json"
    report_path = out_dir / "physical_feature_mondrian_conformal_comparison_report.md"
    _write_csv(metrics_csv, analysis.get("records") or [])
    _write_csv(cell_metrics_csv, analysis.get("cell_records") or [])
    if analysis.get("available") is True:
        _plot(figure_path, analysis)

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": recommendation["decision"],
        "recommendation": recommendation,
        "tandem_summary": str(tandem_path),
        "tandem_summary_sha256": _sha256(tandem_path),
        "global_summary": str(global_path),
        "global_summary_sha256": _sha256(global_path),
        "predictions_csv": str(predictions_path),
        "predictions_csv_sha256": _sha256(predictions_path),
        "input_columns": list(INPUT_COLUMNS),
        "physical_cell_contract": {
            "bins_per_dimension": int(args.physical_cell_bins),
            "lower": [float(value) for value in args._physical_cell_lower],
            "upper": [float(value) for value in args._physical_cell_upper],
            "minimum_calibration_rows_per_supported_cell": int(args.min_cell_calibration_rows),
            "minimum_evaluation_rows_per_supported_cell": int(args.min_cell_evaluation_rows),
            "minimum_supported_cells": int(args.min_supported_cells),
            "minimum_supported_evaluation_cell_fraction": float(args.min_supported_cell_fraction),
            "minimum_supported_evaluation_row_fraction": float(args.min_supported_row_fraction),
        },
        "checks": checks,
        "prediction_evidence": {
            key: value
            for key, value in rows.items()
            if key not in {
                "target",
                "forward",
                "reconstructed",
                "source_indices",
                "calibration_mask",
                "cell_ids",
                "cell_coordinates",
            }
        },
        "analysis": {
            key: value for key, value in analysis.items() if key not in {"records", "cell_records"}
        },
        "artifacts": {
            "metrics_csv": str(metrics_csv),
            "cell_metrics_csv": str(cell_metrics_csv),
            "figure_png": str(figure_path),
            "report_md": str(report_path),
        },
        "literature_basis": (
            "The comparison follows Mondrian conformal prediction with categories fixed before evaluation. "
            "It targets finite-sample group coverage for the declared Lp/Ls/Q/|K| cells under within-cell "
            "exchangeability; it does not claim exact pointwise conditional coverage."
        ),
        "scientific_boundary": (
            "PASS means the global-versus-Mondrian comparison is traceable, uses the identical calibration and "
            "evaluation rows, and has adequate supported-cell evidence. It does not mean Mondrian won. Unsupported "
            "cells receive no group-conditional claim, and neither interval is sample-wise epistemic uncertainty or "
            "a substitute for DRC, real EMX closure, HFSS correlation, process corners, or measurement."
        ),
        "arguments": _jsonable_arguments(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--global-summary", required=True)
    parser.add_argument("--predictions-csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-source-rows", type=int, default=600_000)
    parser.add_argument("--min-prediction-rows", type=int, default=5_000)
    parser.add_argument("--min-calibration-rows", type=int, default=2_000)
    parser.add_argument("--min-evaluation-rows", type=int, default=2_000)
    parser.add_argument("--calibration-fraction", type=float, default=0.5)
    parser.add_argument("--coverage-levels", default="0.90,0.95")
    parser.add_argument("--coverage-tolerance", type=float, default=0.03)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower", default="0.5,0.5,5,0")
    parser.add_argument("--physical-cell-upper", default="3,3,25,0.8")
    parser.add_argument("--min-cell-calibration-rows", type=int, default=30)
    parser.add_argument("--min-cell-evaluation-rows", type=int, default=30)
    parser.add_argument("--min-supported-cells", type=int, default=8)
    parser.add_argument("--min-supported-cell-fraction", type=float, default=0.80)
    parser.add_argument("--min-supported-row-fraction", type=float, default=0.80)
    parser.add_argument("--max-mean-width-inflation", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    args._coverage_levels = _levels(args.coverage_levels)
    args._physical_cell_lower = _vector(args.physical_cell_lower)
    args._physical_cell_upper = _vector(args.physical_cell_upper)
    if not args._coverage_levels or any(not 0.0 < value < 1.0 for value in args._coverage_levels):
        parser.error("--coverage-levels must contain comma-separated values in (0, 1)")
    if args._physical_cell_lower.shape != (len(FEATURE_NAMES),) or args._physical_cell_upper.shape != (
        len(FEATURE_NAMES),
    ):
        parser.error("physical-cell bounds must contain four values")
    if np.any(~np.isfinite(args._physical_cell_lower)) or np.any(~np.isfinite(args._physical_cell_upper)):
        parser.error("physical-cell bounds must be finite")
    if np.any(args._physical_cell_upper <= args._physical_cell_lower):
        parser.error("physical-cell upper bounds must exceed lower bounds")
    positive = (
        args.min_source_rows,
        args.min_prediction_rows,
        args.min_calibration_rows,
        args.min_evaluation_rows,
        args.physical_cell_bins,
        args.min_cell_calibration_rows,
        args.min_cell_evaluation_rows,
        args.min_supported_cells,
    )
    if min(positive) < 1 or args.physical_cell_bins < 2:
        parser.error("row, cell, and bin minimums must be positive; bins must be at least two")
    fractions = (
        args.calibration_fraction,
        args.min_supported_cell_fraction,
        args.min_supported_row_fraction,
    )
    if not 0.0 < args.calibration_fraction < 1.0 or any(not 0.0 <= value <= 1.0 for value in fractions[1:]):
        parser.error("calibration fraction must be in (0,1); support fractions must be in [0,1]")
    if not 0.0 <= args.coverage_tolerance < 1.0 or args.max_mean_width_inflation < 0.0:
        parser.error("coverage tolerance and maximum width inflation must be nonnegative")
    return args


def _load_predictions(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_source_index_count": 0,
        "columns_present": False,
        "target_rows_inside_declared_range": False,
        "calibration_count": 0,
        "evaluation_count": 0,
        "target": np.empty((0, len(FEATURE_NAMES))),
        "forward": np.empty((0, len(FEATURE_NAMES))),
        "reconstructed": np.empty((0, len(FEATURE_NAMES))),
        "source_indices": np.empty(0, dtype=np.int64),
        "calibration_mask": np.empty(0, dtype=bool),
        "cell_ids": np.empty(0, dtype=np.int64),
        "cell_coordinates": np.empty((0, len(FEATURE_NAMES)), dtype=np.int64),
    }
    if not path.is_file():
        return result
    target_columns = tuple(f"target__{name}" for name in FEATURE_NAMES)
    forward_columns = tuple(f"forward__{name}" for name in FEATURE_NAMES)
    reconstructed_columns = tuple(f"reconstructed__{name}" for name in FEATURE_NAMES)
    targets: list[np.ndarray] = []
    forwards: list[np.ndarray] = []
    reconstructed: list[np.ndarray] = []
    source_indices: list[int] = []
    seen: set[int] = set()
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
            inverse = _float_row(row, reconstructed_columns)
            source_index = _integer(row.get("source_row_index"))
            if target is None or forward is None or inverse is None or source_index is None:
                result["invalid_count"] += 1
                continue
            if source_index in seen:
                result["duplicate_source_index_count"] += 1
                continue
            seen.add(source_index)
            targets.append(target)
            forwards.append(forward)
            reconstructed.append(inverse)
            source_indices.append(source_index)
    if targets:
        result["target"] = np.asarray(targets, dtype=float)
        result["forward"] = np.asarray(forwards, dtype=float)
        result["reconstructed"] = np.asarray(reconstructed, dtype=float)
        result["source_indices"] = np.asarray(source_indices, dtype=np.int64)
        result["calibration_mask"] = np.asarray(
            [_calibration_member(index, int(args.seed), float(args.calibration_fraction)) for index in source_indices],
            dtype=bool,
        )
        inside = np.all(
            (result["target"] >= args._physical_cell_lower[None, :])
            & (result["target"] <= args._physical_cell_upper[None, :])
        )
        result["target_rows_inside_declared_range"] = bool(inside)
        if inside:
            coordinates, cell_ids = _physical_cells(
                result["target"], args._physical_cell_lower, args._physical_cell_upper, int(args.physical_cell_bins)
            )
            result["cell_coordinates"] = coordinates
            result["cell_ids"] = cell_ids
    result["valid_count"] = len(targets)
    result["calibration_count"] = int(np.sum(result["calibration_mask"]))
    result["evaluation_count"] = int(len(targets) - result["calibration_count"])
    return result


def _compare(rows: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if rows.get("valid_count", 0) < 1 or rows.get("target_rows_inside_declared_range") is not True:
        return {"available": False, "records": [], "cell_records": []}
    calibration = np.asarray(rows["calibration_mask"], dtype=bool)
    evaluation = ~calibration
    cell_ids = np.asarray(rows["cell_ids"], dtype=np.int64)
    unique_evaluation_cells = np.unique(cell_ids[evaluation])
    calibration_counts = {int(cell): int(np.sum(calibration & (cell_ids == cell))) for cell in np.unique(cell_ids)}
    evaluation_counts = {int(cell): int(np.sum(evaluation & (cell_ids == cell))) for cell in unique_evaluation_cells}
    supported_cells = sorted(
        cell
        for cell in unique_evaluation_cells.tolist()
        if calibration_counts.get(int(cell), 0) >= int(args.min_cell_calibration_rows)
        and evaluation_counts.get(int(cell), 0) >= int(args.min_cell_evaluation_rows)
    )
    supported_set = set(int(cell) for cell in supported_cells)
    supported_evaluation = evaluation & np.asarray([int(cell) in supported_set for cell in cell_ids], dtype=bool)
    supported_evaluation_count = int(np.sum(supported_evaluation))
    evaluation_count = int(np.sum(evaluation))
    support = {
        "evaluation_occupied_cell_count": int(len(unique_evaluation_cells)),
        "supported_evaluation_cell_count": int(len(supported_cells)),
        "supported_evaluation_cell_fraction": (
            float(len(supported_cells) / len(unique_evaluation_cells)) if len(unique_evaluation_cells) else 0.0
        ),
        "supported_evaluation_row_count": supported_evaluation_count,
        "supported_evaluation_row_fraction": (
            float(supported_evaluation_count / evaluation_count) if evaluation_count else 0.0
        ),
        "unsupported_evaluation_cells": [
            int(cell) for cell in unique_evaluation_cells.tolist() if int(cell) not in supported_set
        ],
    }

    base_analysis = {
        "calibration_count": int(np.sum(calibration)),
        "evaluation_count": int(np.sum(evaluation)),
        "split_fingerprint_sha256": _split_fingerprint(rows["source_indices"], calibration),
        "cell_assignment_fingerprint_sha256": _cell_assignment_fingerprint(
            rows["source_indices"], cell_ids, calibration
        ),
        "support": support,
    }
    if not supported_cells:
        return {
            "available": False,
            **base_analysis,
            "methods": {},
            "records": [],
            "cell_records": [],
        }

    records: list[dict[str, Any]] = []
    cell_records: list[dict[str, Any]] = []
    method_summary: dict[str, Any] = {}
    for method, key in METHODS.items():
        residual = np.abs(np.asarray(rows[key], dtype=float) - np.asarray(rows["target"], dtype=float))
        method_records = []
        for feature_index, feature in enumerate(FEATURE_NAMES):
            for level in args._coverage_levels:
                global_quantile = _conformal_quantile(residual[calibration, feature_index], float(level))
                per_cell: list[dict[str, Any]] = []
                for cell in unique_evaluation_cells.tolist():
                    cell = int(cell)
                    cal_mask = calibration & (cell_ids == cell)
                    eval_mask = evaluation & (cell_ids == cell)
                    cal_count = int(np.sum(cal_mask))
                    eval_count = int(np.sum(eval_mask))
                    supported = cell in supported_set
                    mondrian_quantile = (
                        _conformal_quantile(residual[cal_mask, feature_index], float(level)) if supported else None
                    )
                    eval_residual = residual[eval_mask, feature_index]
                    global_coverage = float(np.mean(eval_residual <= global_quantile)) if eval_count else None
                    mondrian_coverage = (
                        float(np.mean(eval_residual <= float(mondrian_quantile)))
                        if supported and eval_count and mondrian_quantile is not None
                        else None
                    )
                    coordinates = np.unravel_index(cell, (int(args.physical_cell_bins),) * len(FEATURE_NAMES))
                    cell_record = {
                        "method": method,
                        "feature": feature,
                        "nominal_coverage": float(level),
                        "cell_id": cell,
                        "cell_coordinates": ":".join(str(int(value)) for value in coordinates),
                        "calibration_count": cal_count,
                        "evaluation_count": eval_count,
                        "supported": supported,
                        "global_half_width_physical": float(global_quantile),
                        "mondrian_half_width_physical": (
                            None if mondrian_quantile is None else float(mondrian_quantile)
                        ),
                        "global_empirical_coverage": global_coverage,
                        "mondrian_empirical_coverage": mondrian_coverage,
                    }
                    cell_records.append(cell_record)
                    if supported:
                        per_cell.append(cell_record)
                global_cell_coverages = np.asarray(
                    [float(item["global_empirical_coverage"]) for item in per_cell], dtype=float
                )
                mondrian_cell_coverages = np.asarray(
                    [float(item["mondrian_empirical_coverage"]) for item in per_cell], dtype=float
                )
                supported_residual = residual[supported_evaluation, feature_index]
                mondrian_limits = np.asarray(
                    [
                        _conformal_quantile(
                            residual[calibration & (cell_ids == int(cell)), feature_index], float(level)
                        )
                        for cell in cell_ids[supported_evaluation]
                    ],
                    dtype=float,
                )
                global_micro = (
                    float(np.mean(supported_residual <= global_quantile)) if supported_residual.size else None
                )
                mondrian_micro = (
                    float(np.mean(supported_residual <= mondrian_limits)) if supported_residual.size else None
                )
                global_width = float(global_quantile / FEATURE_SPANS[feature_index])
                mondrian_width = (
                    float(np.mean(mondrian_limits) / FEATURE_SPANS[feature_index])
                    if mondrian_limits.size
                    else None
                )
                width_inflation = _relative_inflation(mondrian_width, global_width)
                record = {
                    "method": method,
                    "feature": feature,
                    "nominal_coverage": float(level),
                    "supported_cell_count": int(len(per_cell)),
                    "supported_evaluation_row_count": int(supported_residual.size),
                    "global_half_width_physical": float(global_quantile),
                    "global_mean_half_width_range_normalized": global_width,
                    "mondrian_mean_half_width_range_normalized": mondrian_width,
                    "mondrian_mean_width_inflation": width_inflation,
                    "global_micro_coverage": global_micro,
                    "mondrian_micro_coverage": mondrian_micro,
                    "global_macro_equal_cell_coverage": _mean_or_none(global_cell_coverages),
                    "mondrian_macro_equal_cell_coverage": _mean_or_none(mondrian_cell_coverages),
                    "global_p10_cell_coverage": _quantile_or_none(global_cell_coverages, 0.10),
                    "mondrian_p10_cell_coverage": _quantile_or_none(mondrian_cell_coverages, 0.10),
                    "global_worst_cell_coverage": _min_or_none(global_cell_coverages),
                    "mondrian_worst_cell_coverage": _min_or_none(mondrian_cell_coverages),
                }
                record["p10_cell_coverage_improvement"] = _difference(
                    record["mondrian_p10_cell_coverage"], record["global_p10_cell_coverage"]
                )
                record["worst_cell_coverage_improvement"] = _difference(
                    record["mondrian_worst_cell_coverage"], record["global_worst_cell_coverage"]
                )
                record["mondrian_micro_coverage_pass"] = (
                    mondrian_micro is not None
                    and mondrian_micro >= float(level) - float(args.coverage_tolerance)
                )
                record["mondrian_macro_coverage_pass"] = (
                    record["mondrian_macro_equal_cell_coverage"] is not None
                    and float(record["mondrian_macro_equal_cell_coverage"])
                    >= float(level) - float(args.coverage_tolerance)
                )
                records.append(record)
                method_records.append(record)
        method_summary[method] = _summarize_records(method_records)

    return {
        "available": True,
        **base_analysis,
        "methods": method_summary,
        "records": records,
        "cell_records": cell_records,
    }


def _checks(
    tandem: dict[str, Any],
    global_summary: dict[str, Any],
    rows: dict[str, Any],
    analysis: dict[str, Any],
    predictions_path: Path,
    tandem_path: Path,
    args: argparse.Namespace,
) -> dict[str, bool]:
    split = tandem.get("split_audit") or {}
    global_args = global_summary.get("arguments") or {}
    global_analysis = global_summary.get("analysis") or {}
    records = analysis.get("records") or []
    support = analysis.get("support") or {}
    checks = {
        "tandem_summary_reviewable": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "formal_input_contract": tuple(tandem.get("input_columns") or []) == INPUT_COLUMNS,
        "source_rows_meet_600k_stage_minimum": int(tandem.get("training_count") or 0)
        >= int(args.min_source_rows),
        "physical_cell_ood_split": split.get("split_mode") == "physical_cell_grouped"
        and int(split.get("physical_cell_overlap_count") or 0) == 0,
        "declared_physical_range_matches_project_contract": bool(
            np.array_equal(args._physical_cell_lower, PROJECT_LOWER)
            and np.array_equal(args._physical_cell_upper, PROJECT_UPPER)
        ),
        "global_summary_pass": global_summary.get("overall_status") == "PASS",
        "global_tandem_summary_sha_matches": global_summary.get("tandem_summary_sha256") == _sha256(tandem_path),
        "global_predictions_sha_matches": global_summary.get("predictions_csv_sha256")
        == _sha256(predictions_path),
        "global_input_contract_matches": tuple(global_summary.get("input_columns") or []) == INPUT_COLUMNS,
        "global_seed_matches": _integer(global_args.get("seed")) == int(args.seed),
        "global_calibration_fraction_matches": _close(
            global_args.get("calibration_fraction"), args.calibration_fraction
        ),
        "global_coverage_levels_match": _levels(global_args.get("coverage_levels"))
        == list(args._coverage_levels),
        "global_split_fingerprint_matches": global_analysis.get("split_fingerprint_sha256")
        == analysis.get("split_fingerprint_sha256"),
        "prediction_columns_present": rows.get("columns_present") is True,
        "prediction_rows_meet_minimum": int(rows.get("valid_count") or 0) >= int(args.min_prediction_rows),
        "prediction_rows_finite_unique": int(rows.get("invalid_count") or 0) == 0
        and int(rows.get("duplicate_source_index_count") or 0) == 0,
        "targets_inside_declared_physical_range": rows.get("target_rows_inside_declared_range") is True,
        "calibration_rows_meet_minimum": int(rows.get("calibration_count") or 0)
        >= int(args.min_calibration_rows),
        "evaluation_rows_meet_minimum": int(rows.get("evaluation_count") or 0)
        >= int(args.min_evaluation_rows),
        "supported_cells_meet_minimum": int(support.get("supported_evaluation_cell_count") or 0)
        >= int(args.min_supported_cells),
        "supported_cell_fraction_meets_minimum": float(
            support.get("supported_evaluation_cell_fraction") or 0.0
        )
        >= float(args.min_supported_cell_fraction),
        "supported_row_fraction_meets_minimum": float(
            support.get("supported_evaluation_row_fraction") or 0.0
        )
        >= float(args.min_supported_row_fraction),
        "analysis_available": analysis.get("available") is True,
        "all_declared_method_feature_levels_present": len(records)
        == len(METHODS) * len(FEATURE_NAMES) * len(args._coverage_levels),
        "all_comparison_metrics_finite": bool(records) and all(_record_finite(item) for item in records),
    }
    return {key: bool(value) for key, value in checks.items()}


def _recommendation(analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    records = analysis.get("records") or []
    if not records:
        return {"decision": "DO_NOT_USE_MONDRIAN_COMPARISON", "criteria_pass": False, "criteria": {}}
    p10 = np.asarray([item["p10_cell_coverage_improvement"] for item in records], dtype=float)
    worst = np.asarray([item["worst_cell_coverage_improvement"] for item in records], dtype=float)
    inflation = np.asarray([item["mondrian_mean_width_inflation"] for item in records], dtype=float)
    criteria = {
        "all_mondrian_micro_coverages_meet_nominal_minus_tolerance": all(
            item.get("mondrian_micro_coverage_pass") is True for item in records
        ),
        "all_mondrian_macro_coverages_meet_nominal_minus_tolerance": all(
            item.get("mondrian_macro_coverage_pass") is True for item in records
        ),
        "no_p10_cell_coverage_regression": bool(np.all(p10 >= 0.0)),
        "positive_mean_p10_cell_coverage_improvement": float(np.mean(p10)) > 0.0,
        "no_worst_cell_coverage_regression": bool(np.all(worst >= 0.0)),
        "positive_mean_worst_cell_coverage_improvement": float(np.mean(worst)) > 0.0,
        "maximum_mean_width_inflation_within_limit": float(np.max(inflation))
        <= float(args.max_mean_width_inflation),
    }
    criteria_pass = all(criteria.values())
    return {
        "decision": (
            "ADOPT_MONDRIAN_FOR_GROUP_REPORTED_INTERVALS"
            if criteria_pass
            else "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS"
        ),
        "criteria_pass": criteria_pass,
        "criteria": criteria,
        "aggregate_metrics": {
            "minimum_p10_cell_coverage_improvement": float(np.min(p10)),
            "mean_p10_cell_coverage_improvement": float(np.mean(p10)),
            "minimum_worst_cell_coverage_improvement": float(np.min(worst)),
            "mean_worst_cell_coverage_improvement": float(np.mean(worst)),
            "maximum_mean_width_inflation": float(np.max(inflation)),
            "maximum_allowed_mean_width_inflation": float(args.max_mean_width_inflation),
        },
        "interpretation": (
            "This rule governs reporting only. An adoption recommendation cannot change data generation, active "
            "acquisition, the inverse model, or any EMX/HFSS contract without a separate preregistered experiment."
        ),
    }


def _physical_cells(
    values: np.ndarray, lower: np.ndarray, upper: np.ndarray, bins: int
) -> tuple[np.ndarray, np.ndarray]:
    scaled = (np.asarray(values, dtype=float) - lower[None, :]) / (upper - lower)[None, :]
    coordinates = np.clip(np.floor(scaled * bins).astype(np.int64), 0, bins - 1)
    ids = np.ravel_multi_index(coordinates.T, (bins,) * values.shape[1]).astype(np.int64)
    return coordinates, ids


def _conformal_quantile(values: np.ndarray, coverage: float) -> float:
    sorted_values = np.sort(np.asarray(values, dtype=float))
    if sorted_values.size == 0:
        return math.nan
    rank = int(math.ceil((len(sorted_values) + 1) * coverage))
    rank = min(max(rank, 1), len(sorted_values))
    return float(sorted_values[rank - 1])


def _calibration_member(source_index: int, seed: int, fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}|{source_index}".encode("ascii")).digest()
    value = int.from_bytes(digest[:8], "big") / float(2**64)
    return value < fraction


def _split_fingerprint(indices: np.ndarray, mask: np.ndarray) -> str:
    digest = hashlib.sha256()
    for index, calibration in sorted(zip(indices.tolist(), mask.tolist())):
        digest.update(f"{int(index)}:{'C' if calibration else 'E'}\n".encode("ascii"))
    return digest.hexdigest()


def _cell_assignment_fingerprint(indices: np.ndarray, cells: np.ndarray, mask: np.ndarray) -> str:
    digest = hashlib.sha256()
    for index, cell, calibration in sorted(zip(indices.tolist(), cells.tolist(), mask.tolist())):
        digest.update(f"{int(index)}:{int(cell)}:{'C' if calibration else 'E'}\n".encode("ascii"))
    return digest.hexdigest()


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    return {
        "all_mondrian_micro_coverages_pass": all(
            item.get("mondrian_micro_coverage_pass") is True for item in records
        ),
        "all_mondrian_macro_coverages_pass": all(
            item.get("mondrian_macro_coverage_pass") is True for item in records
        ),
        "mean_p10_cell_coverage_improvement": float(
            np.mean([item["p10_cell_coverage_improvement"] for item in records])
        ),
        "mean_worst_cell_coverage_improvement": float(
            np.mean([item["worst_cell_coverage_improvement"] for item in records])
        ),
        "maximum_mean_width_inflation": float(
            np.max([item["mondrian_mean_width_inflation"] for item in records])
        ),
    }


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    records = analysis["records"]
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), sharey=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    for axis, feature in zip(axes.flat, FEATURE_NAMES):
        subset = [item for item in records if item["feature"] == feature]
        labels = [f"{item['method'].replace('_proxy', '').replace('_inverse', '')}\n{item['nominal_coverage']:.0%}" for item in subset]
        positions = np.arange(len(subset), dtype=float)
        width = 0.34
        axis.bar(
            positions - width / 2,
            [item["global_macro_equal_cell_coverage"] for item in subset],
            width=width,
            color="#4c78a8",
            label="global macro",
        )
        axis.bar(
            positions + width / 2,
            [item["mondrian_macro_equal_cell_coverage"] for item in subset],
            width=width,
            color="#e45756",
            label="Mondrian macro",
        )
        axis.scatter(
            positions - width / 2,
            [item["global_p10_cell_coverage"] for item in subset],
            marker="v",
            color="#1f3f63",
            label="global cell p10",
            zorder=3,
        )
        axis.scatter(
            positions + width / 2,
            [item["mondrian_p10_cell_coverage"] for item in subset],
            marker="v",
            color="#8f1d1d",
            label="Mondrian cell p10",
            zorder=3,
        )
        axis.scatter(
            positions,
            [item["nominal_coverage"] for item in subset],
            marker="_",
            s=500,
            linewidths=2.0,
            color="#202020",
            label="nominal",
            zorder=4,
        )
        axis.set_xticks(positions, labels)
        axis.set_ylim(0.0, 1.01)
        axis.set_title(feature)
        axis.set_ylabel("Supported-cell evaluation coverage")
        axis.grid(axis="y", color="#dddddd", linewidth=0.8)
        axis.legend(facecolor="white", framealpha=1.0, fontsize=8, ncol=2)
    support = analysis.get("support") or {}
    fig.suptitle(
        "Global vs fixed-4D-cell Mondrian conformal coverage "
        f"({support.get('supported_evaluation_cell_count')}/{support.get('evaluation_occupied_cell_count')} cells supported)",
        fontsize=15,
        color="#202020",
    )
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    support = analysis.get("support") or {}
    recommendation = data.get("recommendation") or {}
    lines = [
        "# Global versus Mondrian conformal comparison",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Calibration rows: `{analysis.get('calibration_count')}`",
        f"- Independent evaluation rows: `{analysis.get('evaluation_count')}`",
        f"- Supported evaluation cells: `{support.get('supported_evaluation_cell_count')}` / `{support.get('evaluation_occupied_cell_count')}`",
        f"- Supported evaluation row fraction: `{support.get('supported_evaluation_row_fraction')}`",
        "",
        "## Predeclared reporting rule",
        "",
    ]
    for name, value in (recommendation.get("criteria") or {}).items():
        lines.append(f"- `{name}`: `{value}`")
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _record_finite(item: dict[str, Any]) -> bool:
    keys = (
        "global_half_width_physical",
        "global_mean_half_width_range_normalized",
        "mondrian_mean_half_width_range_normalized",
        "mondrian_mean_width_inflation",
        "global_micro_coverage",
        "mondrian_micro_coverage",
        "global_macro_equal_cell_coverage",
        "mondrian_macro_equal_cell_coverage",
        "global_p10_cell_coverage",
        "mondrian_p10_cell_coverage",
        "global_worst_cell_coverage",
        "mondrian_worst_cell_coverage",
        "p10_cell_coverage_improvement",
        "worst_cell_coverage_improvement",
    )
    return all(_finite(item.get(key)) is not None for key in keys)


def _mean_or_none(values: np.ndarray) -> float | None:
    return float(np.mean(values)) if values.size else None


def _quantile_or_none(values: np.ndarray, quantile: float) -> float | None:
    return float(np.quantile(values, quantile)) if values.size else None


def _min_or_none(values: np.ndarray) -> float | None:
    return float(np.min(values)) if values.size else None


def _difference(left: Any, right: Any) -> float | None:
    left_value = _finite(left)
    right_value = _finite(right)
    return None if left_value is None or right_value is None else float(left_value - right_value)


def _relative_inflation(value: Any, baseline: Any) -> float | None:
    value_number = _finite(value)
    baseline_number = _finite(baseline)
    if value_number is None or baseline_number is None:
        return None
    if baseline_number == 0.0:
        return 0.0 if value_number == 0.0 else math.inf
    return float(value_number / baseline_number - 1.0)


def _levels(raw: Any) -> list[float]:
    try:
        return sorted(set(float(item.strip()) for item in str(raw).split(",") if item.strip()))
    except (TypeError, ValueError):
        return []


def _vector(raw: Any) -> np.ndarray:
    try:
        return np.asarray([float(item.strip()) for item in str(raw).split(",") if item.strip()], dtype=float)
    except (TypeError, ValueError):
        return np.empty(0, dtype=float)


def _resolve_path(raw: Any, base: Path) -> Path:
    if not raw:
        return base / "__missing__"
    path = Path(str(raw)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _float_row(row: dict[str, str], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close(left: Any, right: Any) -> bool:
    left_value = _finite(left)
    right_value = _finite(right)
    return left_value is not None and right_value is not None and math.isclose(
        left_value, right_value, rel_tol=0.0, abs_tol=1.0e-12
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {key: value for key, value in vars(args).items() if not key.startswith("_")}


if __name__ == "__main__":
    raise SystemExit(main())
