#!/usr/bin/env python3
"""Audit split-conformal coverage for forward and tandem OOD predictions."""

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
FEATURE_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)
METHODS = {"forward_proxy": "forward", "tandem_inverse": "reconstructed"}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tandem_path = Path(args.tandem_summary).expanduser().resolve()
    tandem = _read_json(tandem_path)
    predictions_path = _resolve_path(args.predictions_csv or tandem.get("test_predictions_csv"), tandem_path.parent)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_predictions(predictions_path, args)
    analysis = _calibrate(rows, args)
    checks = _checks(tandem, rows, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    metrics_csv = out_dir / "physical_feature_conformal_calibration_metrics.csv"
    figure_path = out_dir / "physical_feature_conformal_calibration.png"
    summary_path = out_dir / "physical_feature_conformal_calibration_summary.json"
    report_path = out_dir / "physical_feature_conformal_calibration_report.md"
    _write_csv(metrics_csv, analysis.get("records") or [])
    if analysis.get("available") is True:
        _plot(figure_path, analysis, args)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "GLOBAL_CONFORMAL_INTERVALS_CALIBRATED" if status == "PASS" else "DO_NOT_USE_CONFORMAL_INTERVALS",
        "tandem_summary": str(tandem_path),
        "tandem_summary_sha256": _sha256(tandem_path),
        "predictions_csv": str(predictions_path),
        "predictions_csv_sha256": _sha256(predictions_path),
        "input_columns": list(INPUT_COLUMNS),
        "feature_spans": {name: float(FEATURE_SPANS[index]) for index, name in enumerate(FEATURE_NAMES)},
        "checks": checks,
        "prediction_evidence": {
            key: value
            for key, value in rows.items()
            if key not in {"target", "forward", "reconstructed", "source_indices", "calibration_mask"}
        },
        "analysis": {key: value for key, value in analysis.items() if key != "records"},
        "artifacts": {
            "metrics_csv": str(metrics_csv),
            "figure_png": str(figure_path),
            "report_md": str(report_path),
        },
        "literature_basis": (
            "Split conformal prediction supplies finite-sample marginal coverage under exchangeability. This audit "
            "uses the already held-out physical-cell OOD predictions and a second deterministic calibration/evaluation "
            "split; it does not reuse calibration residuals as evaluation evidence."
        ),
        "scientific_boundary": (
            "PASS validates global per-feature error intervals for this fixed model and data distribution. The intervals "
            "are not sample-wise epistemic uncertainty and must not directly drive active acquisition. Distribution "
            "shift, process migration, or a changed geometry contract requires recalibration."
        ),
        "arguments": vars(args),
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
    parser.add_argument("--predictions-csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-source-rows", type=int, default=600_000)
    parser.add_argument("--min-prediction-rows", type=int, default=5_000)
    parser.add_argument("--min-calibration-rows", type=int, default=2_000)
    parser.add_argument("--min-evaluation-rows", type=int, default=2_000)
    parser.add_argument("--calibration-fraction", type=float, default=0.5)
    parser.add_argument("--coverage-levels", default="0.90,0.95")
    parser.add_argument("--coverage-tolerance", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    levels = _levels(args.coverage_levels)
    if not levels or any(not 0.0 < value < 1.0 for value in levels):
        parser.error("--coverage-levels must contain comma-separated values in (0, 1)")
    if args.min_source_rows < 1 or min(args.min_prediction_rows, args.min_calibration_rows, args.min_evaluation_rows) < 1:
        parser.error("row minimums must be positive")
    if not 0.0 < args.calibration_fraction < 1.0 or not 0.0 <= args.coverage_tolerance < 1.0:
        parser.error("calibration fraction must be in (0,1) and tolerance in [0,1)")
    args._coverage_levels = levels
    return args


def _load_predictions(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = {
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_source_index_count": 0,
        "columns_present": False,
        "calibration_count": 0,
        "evaluation_count": 0,
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
    targets = []
    forwards = []
    reconstructed = []
    source_indices = []
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
            [
                _calibration_member(index, int(args.seed), float(args.calibration_fraction))
                for index in source_indices
            ],
            dtype=bool,
        )
    result["valid_count"] = len(targets)
    result["calibration_count"] = int(np.sum(result["calibration_mask"]))
    result["evaluation_count"] = int(len(targets) - result["calibration_count"])
    return result


def _calibrate(rows: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if rows.get("valid_count", 0) < 1:
        return {"available": False, "records": []}
    calibration = rows["calibration_mask"]
    evaluation = ~calibration
    records = []
    summary = {}
    for method, key in METHODS.items():
        prediction = rows[key]
        residual = np.abs(prediction - rows["target"])
        method_records = []
        for feature_index, feature in enumerate(FEATURE_NAMES):
            calibration_residual = residual[calibration, feature_index]
            evaluation_residual = residual[evaluation, feature_index]
            for level in args._coverage_levels:
                quantile = _conformal_quantile(calibration_residual, float(level))
                coverage = float(np.mean(evaluation_residual <= quantile)) if len(evaluation_residual) else None
                record = {
                    "method": method,
                    "feature": feature,
                    "nominal_coverage": float(level),
                    "calibration_count": int(len(calibration_residual)),
                    "evaluation_count": int(len(evaluation_residual)),
                    "half_width_physical": quantile,
                    "full_width_physical": 2.0 * quantile,
                    "half_width_range_normalized": float(quantile / FEATURE_SPANS[feature_index]),
                    "empirical_coverage": coverage,
                    "coverage_gap": None if coverage is None else float(coverage - level),
                    "coverage_pass": coverage is not None and coverage >= float(level) - float(args.coverage_tolerance),
                }
                records.append(record)
                method_records.append(record)
        summary[method] = {
            "all_coverages_pass": all(item["coverage_pass"] for item in method_records),
            "mean_half_width_range_normalized": float(
                np.mean([item["half_width_range_normalized"] for item in method_records])
            ),
            "minimum_empirical_coverage_gap": float(min(item["coverage_gap"] for item in method_records)),
        }
    return {
        "available": True,
        "calibration_count": int(np.sum(calibration)),
        "evaluation_count": int(np.sum(evaluation)),
        "split_fingerprint_sha256": _split_fingerprint(rows["source_indices"], calibration),
        "methods": summary,
        "records": records,
    }


def _checks(tandem: dict[str, Any], rows: dict[str, Any], analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    split = tandem.get("split_audit") or {}
    records = analysis.get("records") or []
    checks = {
        "tandem_summary_reviewable": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "formal_input_contract": tuple(tandem.get("input_columns") or []) == INPUT_COLUMNS,
        "source_rows_meet_600k_stage_minimum": int(tandem.get("training_count") or 0) >= int(args.min_source_rows),
        "physical_cell_ood_split": split.get("split_mode") == "physical_cell_grouped"
        and int(split.get("physical_cell_overlap_count") or 0) == 0,
        "prediction_columns_present": rows.get("columns_present") is True,
        "prediction_rows_meet_minimum": int(rows.get("valid_count") or 0) >= int(args.min_prediction_rows),
        "prediction_rows_finite_unique": int(rows.get("invalid_count") or 0) == 0
        and int(rows.get("duplicate_source_index_count") or 0) == 0,
        "calibration_rows_meet_minimum": int(rows.get("calibration_count") or 0) >= int(args.min_calibration_rows),
        "evaluation_rows_meet_minimum": int(rows.get("evaluation_count") or 0) >= int(args.min_evaluation_rows),
        "analysis_available": analysis.get("available") is True,
        "all_declared_method_feature_levels_present": len(records) == len(METHODS) * len(FEATURE_NAMES) * len(args._coverage_levels),
        "all_empirical_coverages_pass": bool(records) and all(item.get("coverage_pass") is True for item in records),
        "all_interval_widths_finite": bool(records)
        and all(_finite(item.get("half_width_physical")) is not None for item in records),
    }
    return {key: bool(value) for key, value in checks.items()}


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


def _plot(path: Path, analysis: dict[str, Any], args: argparse.Namespace) -> None:
    records = analysis["records"]
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), sharey=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    colors = {"forward_proxy": "#2c6db2", "tandem_inverse": "#c54a3f"}
    width = 0.18
    levels = list(args._coverage_levels)
    for feature_index, (axis, feature) in enumerate(zip(axes.flat, FEATURE_NAMES)):
        axis.set_facecolor("white")
        axis.tick_params(colors="#202020")
        axis.xaxis.label.set_color("#202020")
        axis.yaxis.label.set_color("#202020")
        axis.title.set_color("#202020")
        positions = np.arange(len(levels), dtype=float)
        for method_index, method in enumerate(METHODS):
            subset = [item for item in records if item["method"] == method and item["feature"] == feature]
            subset.sort(key=lambda item: item["nominal_coverage"])
            offset = (method_index - 0.5) * width
            axis.bar(
                positions + offset,
                [item["empirical_coverage"] for item in subset],
                width=width,
                color=colors[method],
                label=method,
            )
        axis.scatter(positions, levels, marker="_", s=500, linewidths=2.0, color="#202020", label="nominal")
        axis.set_xticks(positions, [f"{level:.0%}" for level in levels])
        axis.set_ylim(max(0.0, min(levels) - 0.10), 1.01)
        axis.set_title(feature)
        axis.set_xlabel("Nominal coverage")
        axis.set_ylabel("Evaluation coverage")
        axis.legend(facecolor="white", framealpha=1.0, fontsize=8)
    fig.suptitle("Split-conformal coverage on held-out physical-cell OOD predictions", fontsize=15, color="#202020")
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    lines = [
        "# Physical-feature split-conformal calibration audit",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Calibration rows: `{analysis.get('calibration_count')}`",
        f"- Independent evaluation rows: `{analysis.get('evaluation_count')}`",
        "",
    ]
    for method, item in (analysis.get("methods") or {}).items():
        lines.append(
            f"- `{method}`: coverage pass `{item.get('all_coverages_pass')}`, mean normalized half-width `{item.get('mean_half_width_range_normalized')}`"
        )
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


def _levels(raw: str) -> list[float]:
    try:
        values = sorted(set(float(item.strip()) for item in str(raw).split(",") if item.strip()))
    except ValueError:
        return []
    return values


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
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
