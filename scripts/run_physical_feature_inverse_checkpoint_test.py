#!/usr/bin/env python3
"""Scalable checkpoint test for physical-feature inverse modeling.

This is the production checkpoint counterpart to the small-data leave-one-out
audits.  It trains a transparent ridge-regression baseline from
Lp/Ls/Q/K -> geometry and evaluates it on a deterministic holdout split, so it
can run after every 100k EMX chunk without O(N^2) cross-validation cost.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    input_columns = _resolve_columns(rows, args.input_prefix, args.input_columns)
    geometry_columns = _resolve_columns(rows, args.geom_prefix, args.geometry_columns)
    matrix = _matrix(rows, input_columns, geometry_columns)
    split = _split_indices(matrix["count"], args)

    checks = [
        _check("training_csv_exists", training_csv.is_file(), str(training_csv)),
        _check("rows_present", bool(rows), f"rows={len(rows)}"),
        _check("input_columns_present", bool(input_columns), ",".join(input_columns)),
        _check("geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("usable_rows_meet_minimum", matrix["count"] >= int(args.min_training_rows), f"usable={matrix['count']}, minimum={args.min_training_rows}"),
        _check("train_rows_present", len(split["train"]) > 0, f"train={len(split['train'])}"),
        _check("test_rows_present", len(split["test"]) > 0, f"test={len(split['test'])}"),
        *_feature_contract_checks(input_columns, args),
    ]

    model: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    prediction_rows: list[dict[str, Any]] = []
    geometry_error_rows: list[dict[str, Any]] = []
    if all(item["pass"] for item in checks):
        model = _fit_ridge(matrix, split, input_columns, geometry_columns, args)
        metrics, prediction_rows, geometry_error_rows = _evaluate(model, matrix, split, input_columns, geometry_columns, rows, args)
        checks.extend(
            [
                _check("model_coefficients_present", bool(model.get("coefficients")), f"terms={len(model.get('terms') or [])}"),
                _check("holdout_predictions_present", bool(prediction_rows), f"rows={len(prediction_rows)}"),
                _check(
                    "holdout_normalized_mae_within_warning_limit",
                    metrics.get("max_normalized_mae", math.inf) <= float(args.warn_max_normalized_mae),
                    f"max={metrics.get('max_normalized_mae')}, warning_limit={args.warn_max_normalized_mae}",
                ),
                _check(
                    "holdout_normalized_rmse_within_warning_limit",
                    metrics.get("max_normalized_rmse", math.inf) <= float(args.warn_max_normalized_rmse),
                    f"max={metrics.get('max_normalized_rmse')}, warning_limit={args.warn_max_normalized_rmse}",
                ),
            ]
        )

    required_checks = [item for item in checks if not item["name"].endswith("_within_warning_limit")]
    execution_status = "PASS" if all(item["pass"] for item in required_checks) else "FAIL"
    quality_status = "PASS" if all(item["pass"] for item in checks) else "WARN"
    overall_status = execution_status
    if execution_status != "PASS":
        decision = "DO_NOT_USE_MODEL_CHECKPOINT"
    elif quality_status == "PASS":
        decision = "BASELINE_CHECKPOINT_EXECUTED_WARNING_LIMITS_PASS_NOT_MODEL_SUCCESS_GATE"
    else:
        decision = "BASELINE_CHECKPOINT_EXECUTED_WITH_QUALITY_WARNING_NOT_MODEL_SUCCESS_GATE"

    model_path = out_dir / "physical_feature_inverse_checkpoint_model.json"
    summary_path = out_dir / "physical_feature_inverse_checkpoint_test_summary.json"
    report_path = out_dir / "physical_feature_inverse_checkpoint_test_report.md"
    predictions_csv = out_dir / "physical_feature_inverse_checkpoint_holdout_predictions.csv"
    errors_csv = out_dir / "physical_feature_inverse_checkpoint_geometry_errors.csv"

    model_path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(predictions_csv, prediction_rows)
    _write_csv(errors_csv, geometry_error_rows)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "execution_status": execution_status,
        "quality_status": quality_status,
        "evidence_scope": "DETERMINISTIC_RANDOM_HOLDOUT_RIDGE_BASELINE",
        "formal_model_gate_status": "NOT_EVALUATED_REQUIRES_PHYSICAL_CELL_OOD_AND_REAL_EM_CLOSURE",
        "eligible_for_model_success_claim": False,
        "decision": decision,
        "training_csv": str(training_csv),
        "out_dir": str(out_dir),
        "model_json": str(model_path),
        "holdout_predictions_csv": str(predictions_csv),
        "geometry_errors_csv": str(errors_csv),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "usable_row_count": int(matrix["count"]),
        "train_row_count": int(len(split["train"])),
        "test_row_count": int(len(split["test"])),
        "method": "polynomial_ridge_holdout",
        "degree": int(args.degree),
        "ridge_alpha": float(args.ridge_alpha),
        "metrics": metrics,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This checkpoint is a scalable baseline model test, not the final neural architecture search.",
            "overall_status=PASS means only that the random-holdout ridge baseline executed on real EMX-derived labels.",
            "quality_status compares only against warning limits; it is not an engineering-accuracy or formal model-success gate.",
            "A formal model claim requires a geometry-disjoint physical-cell OOD split plus DRC and real inverse-EMX closure.",
            "Predicted geometry still requires DRC/layout checks and EMX/HFSS validation before tapeout-facing use.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"execution_status={execution_status}")
    print(f"quality_status={quality_status}")
    print("eligible_for_model_success_claim=false")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", help="Comma-separated input columns; defaults to input__* columns")
    parser.add_argument("--geometry-columns", help="Comma-separated geometry columns; defaults to geom__* columns")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--min-training-rows", type=int, default=100_000)
    parser.add_argument("--max-train-rows", type=int, default=80_000)
    parser.add_argument("--max-test-rows", type=int, default=20_000)
    parser.add_argument("--test-fraction", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=20260705)
    parser.add_argument("--degree", type=int, choices=(1, 2), default=2)
    parser.add_argument("--ridge-alpha", type=float, default=1.0e-6)
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument("--max-prediction-rows", type=int, default=2000)
    parser.add_argument("--warn-max-normalized-mae", type=float, default=0.35)
    parser.add_argument("--warn-max-normalized-rmse", type=float, default=0.50)
    parser.add_argument("--forbid-zin-inputs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-physical-feature-inputs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_columns(rows: list[dict[str, str]], prefix: str, explicit: str | None) -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    if not rows:
        return []
    return sorted(key for key in rows[0] if key.startswith(prefix))


def _matrix(rows: list[dict[str, str]], input_columns: list[str], geometry_columns: list[str]) -> dict[str, Any]:
    x: list[list[float]] = []
    y: list[list[float]] = []
    source_indices: list[int] = []
    for idx, row in enumerate(rows):
        inputs = [_as_float(row.get(column)) for column in input_columns]
        geoms = [_as_float(row.get(column)) for column in geometry_columns]
        if any(value is None for value in inputs) or any(value is None for value in geoms):
            continue
        x.append([float(value) for value in inputs if value is not None])
        y.append([float(value) for value in geoms if value is not None])
        source_indices.append(idx)
    if not x:
        return {
            "count": 0,
            "x": np.empty((0, len(input_columns))),
            "y": np.empty((0, len(geometry_columns))),
            "source_indices": [],
        }
    return {"count": len(x), "x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float), "source_indices": source_indices}


def _split_indices(count: int, args: argparse.Namespace) -> dict[str, np.ndarray]:
    if count <= 1:
        return {"train": np.asarray([], dtype=int), "test": np.asarray([], dtype=int)}
    rng = np.random.default_rng(int(args.seed))
    order = np.arange(count, dtype=int)
    rng.shuffle(order)
    requested_test = max(1, int(round(count * float(args.test_fraction))))
    test_count = min(int(args.max_test_rows), requested_test, count - 1)
    train_count = min(int(args.max_train_rows), count - test_count)
    test = order[:test_count]
    train = order[test_count : test_count + train_count]
    return {"train": np.sort(train), "test": np.sort(test)}


def _fit_ridge(
    matrix: dict[str, Any],
    split: dict[str, np.ndarray],
    input_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    x = np.asarray(matrix["x"], dtype=float)
    y = np.asarray(matrix["y"], dtype=float)
    train = split["train"]
    x_train = x[train]
    y_train = y[train]
    x_mean = np.mean(x_train, axis=0)
    x_std = np.maximum(np.std(x_train, axis=0), float(args.normalization_floor))
    y_mean = np.mean(y_train, axis=0)
    y_std = np.maximum(np.std(y_train, axis=0), float(args.normalization_floor))
    design, terms = _design((x_train - x_mean) / x_std, int(args.degree))
    target = (y_train - y_mean) / y_std
    reg = float(args.ridge_alpha) * np.eye(design.shape[1])
    reg[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + reg, design.T @ target)
    return {
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "input_mean": x_mean.tolist(),
        "input_std": x_std.tolist(),
        "geometry_mean": y_mean.tolist(),
        "geometry_std": y_std.tolist(),
        "terms": terms,
        "coefficients": coefficients.tolist(),
    }


def _evaluate(
    model: dict[str, Any],
    matrix: dict[str, Any],
    split: dict[str, np.ndarray],
    input_columns: list[str],
    geometry_columns: list[str],
    rows: list[dict[str, str]],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    x = np.asarray(matrix["x"], dtype=float)
    y = np.asarray(matrix["y"], dtype=float)
    test = split["test"]
    pred = _predict(model, x[test], int(args.degree))
    truth = y[test]
    spans = np.maximum(np.max(y, axis=0) - np.min(y, axis=0), float(args.normalization_floor))
    errors = pred - truth
    abs_errors = np.abs(errors)
    norm_abs = abs_errors / spans[None, :]

    geometry_error_rows = []
    for col_idx, column in enumerate(geometry_columns):
        err = errors[:, col_idx]
        abs_err = abs_errors[:, col_idx]
        norm = norm_abs[:, col_idx]
        geometry_error_rows.append(
            {
                "geometry_column": column,
                "span": float(spans[col_idx]),
                "mae": float(np.mean(abs_err)),
                "rmse": float(np.sqrt(np.mean(err * err))),
                "max_abs_error": float(np.max(abs_err)),
                "normalized_mae": float(np.mean(norm)),
                "normalized_rmse": float(np.sqrt(np.mean(norm * norm))),
                "normalized_max_abs_error": float(np.max(norm)),
            }
        )

    prediction_rows = []
    max_rows = min(int(args.max_prediction_rows), len(test))
    for local_idx, row_idx in enumerate(test[:max_rows]):
        source_idx = int(matrix["source_indices"][int(row_idx)])
        source = rows[source_idx] if 0 <= source_idx < len(rows) else {}
        row: dict[str, Any] = {
            "holdout_index": local_idx,
            "source_row_index": source_idx,
            "evaluation": source.get("evaluation", ""),
        }
        for col_idx, column in enumerate(input_columns):
            row[column] = float(x[row_idx, col_idx])
        for col_idx, column in enumerate(geometry_columns):
            suffix = column.removeprefix(str(args.geom_prefix))
            row[f"true__{suffix}"] = float(truth[local_idx, col_idx])
            row[f"pred__{suffix}"] = float(pred[local_idx, col_idx])
            row[f"abs_error__{suffix}"] = float(abs_errors[local_idx, col_idx])
            row[f"normalized_abs_error__{suffix}"] = float(norm_abs[local_idx, col_idx])
        prediction_rows.append(row)

    metrics = {
        "test_count": int(len(test)),
        "geometry_count": int(len(geometry_columns)),
        "max_normalized_mae": float(max(row["normalized_mae"] for row in geometry_error_rows)),
        "max_normalized_rmse": float(max(row["normalized_rmse"] for row in geometry_error_rows)),
        "max_normalized_max_abs_error": float(max(row["normalized_max_abs_error"] for row in geometry_error_rows)),
        "mean_normalized_mae": float(np.mean([row["normalized_mae"] for row in geometry_error_rows])),
        "mean_normalized_rmse": float(np.mean([row["normalized_rmse"] for row in geometry_error_rows])),
    }
    return metrics, prediction_rows, geometry_error_rows


def _predict(model: dict[str, Any], x: np.ndarray, degree: int) -> np.ndarray:
    x_mean = np.asarray(model["input_mean"], dtype=float)
    x_std = np.asarray(model["input_std"], dtype=float)
    y_mean = np.asarray(model["geometry_mean"], dtype=float)
    y_std = np.asarray(model["geometry_std"], dtype=float)
    coeff = np.asarray(model["coefficients"], dtype=float)
    design, _terms = _design((x - x_mean) / x_std, degree)
    return design @ coeff * y_std + y_mean


def _design(x: np.ndarray, degree: int) -> tuple[np.ndarray, list[str]]:
    cols = [np.ones((x.shape[0], 1), dtype=float)]
    terms = ["1"]
    for idx in range(x.shape[1]):
        cols.append(x[:, idx : idx + 1])
        terms.append(f"x{idx}")
    if degree >= 2:
        for left in range(x.shape[1]):
            for right in range(left, x.shape[1]):
                cols.append((x[:, left] * x[:, right])[:, None])
                terms.append(f"x{left}*x{right}")
    return np.hstack(cols), terms


def _feature_contract_checks(input_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    checks = []
    if args.forbid_zin_inputs:
        zin_columns = [column for column in input_columns if "zin" in column.lower()]
        checks.append(_check("no_zin_inputs", not zin_columns, f"zin_columns={zin_columns}"))
    if args.require_physical_feature_inputs:
        lower = [column.lower() for column in input_columns]
        required = {
            "lp": any("lp" in column for column in lower),
            "ls": any("ls" in column for column in lower),
            "q": any("q" in column for column in lower),
            "k": any("k" in column for column in lower),
        }
        checks.append(_check("physical_feature_inputs_include_lp_ls_q_k", all(required.values()), required))
    return checks


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical Feature Inverse Checkpoint Test",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Quality status: `{summary['quality_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Usable rows: `{summary['usable_row_count']}`",
        f"- Train rows: `{summary['train_row_count']}`",
        f"- Test rows: `{summary['test_row_count']}`",
        f"- Method: `{summary['method']}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in (summary.get("metrics") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Checks", ""])
    for item in summary["checks"]:
        mark = "PASS" if item["pass"] else "FAIL"
        lines.append(f"- {mark}: {item['name']} - {item['detail']}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
