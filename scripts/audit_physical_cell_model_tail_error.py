#!/usr/bin/env python3
"""Audit per-physical-cell inverse-model errors on the complete OOD test set."""

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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.model_summary).expanduser().resolve()
    predictions_path = Path(args.predictions_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output_summary = out_dir / "physical_cell_model_tail_error_summary.json"
    output_csv = out_dir / "physical_cell_model_tail_errors.csv"
    output_plot = out_dir / "physical_cell_model_tail_error_distribution.png"
    output_report = out_dir / "physical_cell_model_tail_error_report.md"

    model = _read_json(summary_path)
    rows = _read_csv(predictions_path)
    analysis = _analyze(model, rows, args)
    _write_csv(output_csv, analysis.get("cell_rows") or [])
    plot_status = _plot_cell_errors(analysis.get("cell_rows") or [], output_plot)
    checks = dict(analysis.get("checks") or {})
    checks["cell_error_plot_generated"] = plot_status == "PASS"
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "USE_PHYSICAL_CELL_TAIL_EVIDENCE_FOR_CHECKPOINT"
            if status == "PASS"
            else "FIX_INCOMPLETE_OR_INCONSISTENT_PHYSICAL_CELL_TEST_EVIDENCE"
        ),
        "checks": checks,
        "test_row_count": analysis.get("test_row_count"),
        "test_physical_cell_count": analysis.get("test_physical_cell_count"),
        "metrics": analysis.get("metrics") or {},
        "worst_cell": analysis.get("worst_cell") or {},
        "contract": analysis.get("contract") or {},
        "errors": analysis.get("errors") or [],
        "artifacts": {
            "model_summary": str(summary_path),
            "predictions_csv": str(predictions_path),
            "cell_metrics_csv": str(output_csv),
            "distribution_plot": str(output_plot),
            "report": str(output_report),
            "plot_status": plot_status,
        },
        "scientific_boundary": (
            "This audit exposes average, equal-cell, tail, and worst-cell errors for the fixed held-out split. "
            "It does not declare an accuracy PASS threshold, retrain the model, or replace DRC, real EMX closure, "
            "HFSS correlation, process-corner analysis, or measurement."
        ),
        "arguments": vars(args),
    }
    output_summary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_report.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"test_rows={payload['test_row_count']}")
    print(f"test_cells={payload['test_physical_cell_count']}")
    print(f"summary={output_summary}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-summary", required=True)
    parser.add_argument("--predictions-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-test-rows", type=int, default=1000)
    parser.add_argument("--minimum-test-cells", type=int, default=8)
    parser.add_argument("--metric-consistency-tolerance", type=float, default=1.0e-10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.minimum_test_rows) < 1 or int(args.minimum_test_cells) < 1:
        parser.error("minimum test rows/cells must be positive")
    if not math.isfinite(float(args.metric_consistency_tolerance)) or float(args.metric_consistency_tolerance) < 0.0:
        parser.error("metric consistency tolerance must be finite and nonnegative")
    return args


def _analyze(model: dict[str, Any], rows: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    metrics = model.get("metrics") if isinstance(model.get("metrics"), dict) else {}
    split = model.get("split_audit") if isinstance(model.get("split_audit"), dict) else {}
    input_columns = list(model.get("input_columns") or [])
    feature_names = [str(column).removeprefix("input__") for column in input_columns]
    complete_status = model.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"}
    expected_rows = _positive_int(metrics.get("test_row_count"))
    split_rows = _positive_int((split.get("row_counts") or {}).get("test"))
    bins = _positive_int(split.get("physical_cell_bins_per_dimension"))
    lower = _float_vector(split.get("physical_cell_lower"), len(feature_names))
    upper = _float_vector(split.get("physical_cell_upper"), len(feature_names))
    spans = _range_spans(model, input_columns)
    expected_cells = set(str(item) for item in ((split.get("cell_ids") or {}).get("test") or []))
    summary_rmse = _finite(((metrics.get("tandem_inverse") or {}).get("test_response_range_normalized_rmse")))

    if len(feature_names) != 4:
        errors.append(f"expected four physical features, got {feature_names}")
    if expected_rows is None or split_rows is None or expected_rows != split_rows:
        errors.append(f"summary/split test row counts disagree: metrics={expected_rows} split={split_rows}")
    if bins is None or bins < 2:
        errors.append(f"invalid physical-cell bins={bins}")
    if lower is None or upper is None or np.any(upper <= lower):
        errors.append("invalid physical-cell bounds")
    if spans is None:
        errors.append("missing declared range-normalization spans")
    if not expected_cells:
        errors.append("split audit does not declare held-out test cell IDs")

    by_id: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows):
        key = str(row.get("matrix_index") or "").strip()
        if not key:
            errors.append(f"missing matrix_index at prediction row={row_number}")
        elif key in by_id:
            errors.append(f"duplicate matrix_index={key}")
        else:
            by_id[key] = row
    if expected_rows is not None and len(by_id) != expected_rows:
        errors.append(f"prediction rows={len(by_id)} expected complete test rows={expected_rows}")
    if len(by_id) < int(args.minimum_test_rows):
        errors.append(f"prediction rows={len(by_id)} minimum={args.minimum_test_rows}")

    if errors:
        return _failed_analysis(model, rows, errors, complete_status, input_columns, expected_rows, split_rows)

    assert bins is not None and lower is not None and upper is not None and spans is not None
    target_values: list[list[float]] = []
    reconstructed_values: list[list[float]] = []
    forward_values: list[list[float]] = []
    matrix_ids: list[str] = []
    cell_ids: list[str] = []
    for matrix_id in sorted(by_id, key=_sortable_id):
        row = by_id[matrix_id]
        target = [_row_float(row, f"target__{name}") for name in feature_names]
        reconstructed = [_row_float(row, f"reconstructed__{name}") for name in feature_names]
        forward = [_row_float(row, f"forward__{name}") for name in feature_names]
        if any(value is None for value in (*target, *reconstructed, *forward)):
            errors.append(f"nonfinite target/reconstruction/forward at matrix_index={matrix_id}")
            continue
        target_array = np.asarray(target, dtype=float)
        normalized = (target_array - lower) / (upper - lower)
        if np.any((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12)):
            errors.append(f"target outside physical bounds at matrix_index={matrix_id}")
            continue
        cell = np.minimum(np.floor(np.clip(normalized, 0.0, 1.0) * bins).astype(int), bins - 1)
        cell_id = ":".join(str(int(value)) for value in cell)
        matrix_ids.append(matrix_id)
        cell_ids.append(cell_id)
        target_values.append([float(value) for value in target if value is not None])
        reconstructed_values.append([float(value) for value in reconstructed if value is not None])
        forward_values.append([float(value) for value in forward if value is not None])
    if len(target_values) != len(by_id):
        errors.append(f"valid prediction rows={len(target_values)} indexed rows={len(by_id)}")
    actual_cells = set(cell_ids)
    if actual_cells != expected_cells:
        errors.append(
            "prediction physical cells differ from split audit: "
            f"missing={sorted(expected_cells - actual_cells)} extra={sorted(actual_cells - expected_cells)}"
        )
    if len(actual_cells) < int(args.minimum_test_cells):
        errors.append(f"test physical cells={len(actual_cells)} minimum={args.minimum_test_cells}")
    if errors:
        return _failed_analysis(model, rows, errors, complete_status, input_columns, expected_rows, split_rows)

    target = np.asarray(target_values, dtype=float)
    reconstructed = np.asarray(reconstructed_values, dtype=float)
    forward = np.asarray(forward_values, dtype=float)
    range_error = (reconstructed - target) / spans[None, :]
    forward_range_error = (forward - target) / spans[None, :]
    row_weighted_rmse = float(np.sqrt(np.mean(range_error**2)))
    tolerance = float(args.metric_consistency_tolerance)
    metric_consistent = summary_rmse is not None and abs(row_weighted_rmse - summary_rmse) <= tolerance
    if not metric_consistent:
        errors.append(f"prediction RMSE={row_weighted_rmse} summary={summary_rmse} tolerance={tolerance}")

    cell_rows: list[dict[str, Any]] = []
    for cell_id in sorted(actual_cells, key=_sortable_cell):
        mask = np.asarray([value == cell_id for value in cell_ids], dtype=bool)
        cell_error = range_error[mask]
        cell_forward_error = forward_range_error[mask]
        cell_target = target[mask]
        item: dict[str, Any] = {
            "physical_cell_id": cell_id,
            "row_count": int(np.sum(mask)),
            "response_range_normalized_rmse": float(np.sqrt(np.mean(cell_error**2))),
            "response_range_normalized_mae": float(np.mean(np.abs(cell_error))),
            "forward_range_normalized_rmse": float(np.sqrt(np.mean(cell_forward_error**2))),
        }
        for index, feature in enumerate(feature_names):
            item[f"{feature}__response_range_normalized_rmse"] = float(
                np.sqrt(np.mean(cell_error[:, index] ** 2))
            )
            item[f"{feature}__response_range_normalized_mae"] = float(np.mean(np.abs(cell_error[:, index])))
            item[f"{feature}__target_min"] = float(np.min(cell_target[:, index]))
            item[f"{feature}__target_max"] = float(np.max(cell_target[:, index]))
        cell_rows.append(item)

    cell_rmse = np.asarray([float(item["response_range_normalized_rmse"]) for item in cell_rows])
    cell_counts = np.asarray([int(item["row_count"]) for item in cell_rows], dtype=float)
    equal_cell_rmse = float(np.sqrt(np.mean(cell_rmse**2)))
    percentiles = np.quantile(cell_rmse, [0.5, 0.9, 0.95])
    worst = max(cell_rows, key=lambda item: float(item["response_range_normalized_rmse"]))
    median = float(percentiles[0])
    tail_ratio = float(np.max(cell_rmse) / max(median, 1.0e-12))
    contract_payload = {
        "schema": "physical_cell_model_tail_error_v1",
        "split_fingerprint_sha256": split.get("split_fingerprint_sha256"),
        "cell_partition_fingerprint_sha256": split.get("physical_cell_partition_fingerprint_sha256"),
        "matrix_indices": matrix_ids,
        "test_cell_ids": sorted(actual_cells, key=_sortable_cell),
        "input_columns": input_columns,
        "spans": spans.tolist(),
    }
    contract_fingerprint = hashlib.sha256(
        json.dumps(contract_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    checks = {
        "model_summary_complete": complete_status,
        "physical_cell_grouped_split": split.get("split_mode") == "physical_cell_grouped",
        "four_physical_features": len(input_columns) == 4,
        "complete_test_prediction_rows": expected_rows == len(rows) == len(by_id),
        "unique_matrix_indices": len(by_id) == len(rows),
        "test_cells_match_split_audit": actual_cells == expected_cells,
        "minimum_test_rows": len(rows) >= int(args.minimum_test_rows),
        "minimum_test_cells": len(actual_cells) >= int(args.minimum_test_cells),
        "row_weighted_rmse_matches_model_summary": metric_consistent,
        "finite_cell_metrics": bool(cell_rows)
        and all(
            math.isfinite(float(item["response_range_normalized_rmse"]))
            and int(item["row_count"]) > 0
            for item in cell_rows
        ),
    }
    return {
        "checks": checks,
        "errors": errors,
        "test_row_count": len(rows),
        "test_physical_cell_count": len(actual_cells),
        "cell_rows": cell_rows,
        "metrics": {
            "row_weighted_response_range_normalized_rmse": row_weighted_rmse,
            "equal_cell_response_range_normalized_rmse": equal_cell_rmse,
            "cell_response_range_normalized_rmse_p50": median,
            "cell_response_range_normalized_rmse_p90": float(percentiles[1]),
            "cell_response_range_normalized_rmse_p95": float(percentiles[2]),
            "cell_response_range_normalized_rmse_max": float(np.max(cell_rmse)),
            "worst_to_median_cell_rmse_ratio": tail_ratio,
            "cell_row_count_min": int(np.min(cell_counts)),
            "cell_row_count_median": float(np.median(cell_counts)),
            "cell_row_count_max": int(np.max(cell_counts)),
            "cells_above_twice_median_rmse_fraction": float(np.mean(cell_rmse > 2.0 * median)),
        },
        "worst_cell": worst,
        "contract": {
            "schema": "physical_cell_model_tail_error_v1",
            "fingerprint_sha256": contract_fingerprint,
            "split_fingerprint_sha256": split.get("split_fingerprint_sha256"),
            "cell_partition_fingerprint_sha256": split.get("physical_cell_partition_fingerprint_sha256"),
            "input_columns": input_columns,
            "feature_spans": {column: float(spans[index]) for index, column in enumerate(input_columns)},
            "physical_cell_bins": bins,
            "physical_cell_lower": lower.tolist(),
            "physical_cell_upper": upper.tolist(),
            "expected_test_cell_ids": sorted(expected_cells, key=_sortable_cell),
        },
    }


def _failed_analysis(
    model: dict[str, Any],
    rows: list[dict[str, str]],
    errors: list[str],
    complete_status: bool,
    input_columns: list[str],
    expected_rows: int | None,
    split_rows: int | None,
) -> dict[str, Any]:
    return {
        "checks": {
            "model_summary_complete": complete_status,
            "four_physical_features": len(input_columns) == 4,
            "summary_test_row_count_consistent": expected_rows is not None and expected_rows == split_rows,
            "complete_test_prediction_rows": expected_rows is not None and len(rows) == expected_rows,
            "analysis_without_contract_errors": False,
        },
        "errors": errors[:50],
        "test_row_count": len(rows),
        "test_physical_cell_count": 0,
        "cell_rows": [],
        "metrics": {},
        "worst_cell": {},
        "contract": {},
    }


def _plot_cell_errors(rows: list[dict[str, Any]], path: Path) -> str:
    if not rows:
        return "FAIL"
    values = np.asarray([float(row["response_range_normalized_rmse"]) for row in rows], dtype=float)
    counts = np.asarray([int(row["row_count"]) for row in rows], dtype=float)
    order = np.argsort(values)[::-1]
    top = order[: min(10, len(order))]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    bin_count = min(20, max(5, len(values)))
    value_scale = max(float(np.max(np.abs(values))), 1.0)
    if float(np.ptp(values)) <= 16.0 * np.finfo(float).eps * value_scale:
        padding = max(value_scale * 1.0e-6, 1.0e-12)
        histogram_bins: int | np.ndarray = np.linspace(
            float(np.mean(values)) - padding,
            float(np.mean(values)) + padding,
            3,
        )
    else:
        histogram_bins = bin_count
    axes[0].hist(values, bins=histogram_bins, color="#2563eb", edgecolor="white")
    axes[0].set_title("Held-out cell RMSE distribution")
    axes[0].set_xlabel("Declared-range normalized RMSE")
    axes[0].set_ylabel("Cell count")
    axes[1].scatter(counts, values, s=30, color="#0f766e", alpha=0.8)
    axes[1].set_title("Cell population vs error")
    axes[1].set_xlabel("Rows in held-out cell")
    axes[1].set_ylabel("Cell RMSE")
    labels = [str(rows[index]["physical_cell_id"]) for index in top][::-1]
    top_values = values[top][::-1]
    axes[2].barh(labels, top_values, color="#dc2626")
    axes[2].set_title("Worst held-out cells")
    axes[2].set_xlabel("Cell RMSE")
    for axis in axes:
        axis.grid(alpha=0.2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return "PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not fields:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _range_spans(model: dict[str, Any], input_columns: list[str]) -> np.ndarray | None:
    spans = ((((model.get("metrics") or {}).get("range_normalization") or {}).get("feature_span")) or {})
    try:
        values = np.asarray([float(spans[column]) for column in input_columns], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    if values.shape != (len(input_columns),) or np.any(~np.isfinite(values)) or np.any(values <= 0.0):
        return None
    return values


def _float_vector(value: Any, expected: int) -> np.ndarray | None:
    try:
        result = np.asarray([float(item) for item in value], dtype=float)
    except (TypeError, ValueError):
        return None
    if result.shape != (expected,) or np.any(~np.isfinite(result)):
        return None
    return result


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _row_float(row: dict[str, str], field: str) -> float | None:
    return _finite(row.get(field))


def _sortable_id(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _sortable_cell(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item) for item in value.split(":"))
    except ValueError:
        return (2**31 - 1,)


def _render_report(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics") or {}
    worst = payload.get("worst_cell") or {}
    checks = payload.get("checks") or {}
    lines = [
        "# Physical-cell model tail-error audit",
        "",
        f"- Overall status: `{payload.get('overall_status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Complete test rows: `{payload.get('test_row_count')}`",
        f"- Held-out physical cells: `{payload.get('test_physical_cell_count')}`",
        f"- Row-weighted RMSE: `{metrics.get('row_weighted_response_range_normalized_rmse')}`",
        f"- Equal-cell RMSE: `{metrics.get('equal_cell_response_range_normalized_rmse')}`",
        f"- Cell RMSE p95/max: `{metrics.get('cell_response_range_normalized_rmse_p95')}` / `{metrics.get('cell_response_range_normalized_rmse_max')}`",
        f"- Worst cell: `{worst.get('physical_cell_id')}`",
        "",
        "## Contract checks",
        "",
    ]
    lines.extend(f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in checks.items())
    lines.extend(["", "## Scientific boundary", "", str(payload.get("scientific_boundary") or "")])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
