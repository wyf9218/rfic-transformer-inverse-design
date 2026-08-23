#!/usr/bin/env python3
"""Audit whether Lp/Ls/Q/K labels can support inverse geometry prediction.

The script performs leave-one-out cross validation on a completed
``physical_feature_inverse_training_table.csv``:

    input__Lp/Ls/Q/K -> geom__*

It is intentionally a transparent KNN/IDW baseline, not a final neural model.
The goal is to provide hard evidence that the generated EMX labels contain a
usable inverse-design signal before the table is used for downstream training.
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
    training = _training_matrix(rows, input_columns, geometry_columns)
    feature_contract = _input_feature_contract(input_columns, args)
    feature_checks = _feature_contract_checks(input_columns, args)
    checks = [
        _check("training_csv_exists", training_csv.is_file(), str(training_csv)),
        _check("training_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("training_rows_meet_minimum", training["count"] >= int(args.min_training_rows), f"usable_rows={training['count']}, minimum={args.min_training_rows}"),
        _check("input_columns_present", bool(input_columns), ",".join(input_columns)),
        _check("geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("training_matrix_present", training["count"] > 1, f"usable_rows={training['count']}"),
        *feature_checks,
    ]

    cv_predictions: list[dict[str, Any]] = []
    geometry_errors: list[dict[str, Any]] = []
    quality_summary: dict[str, Any] = {}
    if all(item["pass"] for item in checks):
        cv_predictions, geometry_errors, quality_summary = _leave_one_out_quality(training, rows, input_columns, geometry_columns, args)
        checks.extend(
            [
                _check("cv_predictions_present", bool(cv_predictions), f"rows={len(cv_predictions)}"),
                _check(
                    "inverse_model_normalized_mae_within_limit",
                    quality_summary.get("max_normalized_mae", math.inf) <= float(args.max_normalized_mae),
                    f"max={quality_summary.get('max_normalized_mae')} limit={args.max_normalized_mae}",
                ),
                _check(
                    "inverse_model_normalized_rmse_within_limit",
                    quality_summary.get("max_normalized_rmse", math.inf) <= float(args.max_normalized_rmse),
                    f"max={quality_summary.get('max_normalized_rmse')} limit={args.max_normalized_rmse}",
                ),
                _check(
                    "inverse_model_normalized_max_error_within_limit",
                    quality_summary.get("max_normalized_max_abs_error", math.inf) <= float(args.max_normalized_max_abs_error),
                    f"max={quality_summary.get('max_normalized_max_abs_error')} limit={args.max_normalized_max_abs_error}",
                ),
            ]
        )

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    cv_predictions_csv = out_dir / "physical_feature_inverse_model_cv_predictions.csv"
    geometry_errors_csv = out_dir / "physical_feature_inverse_model_geometry_errors.csv"
    summary_path = out_dir / "physical_feature_inverse_model_quality_summary.json"
    report_path = out_dir / "physical_feature_inverse_model_quality_report.md"
    _write_csv(cv_predictions_csv, cv_predictions)
    _write_csv(geometry_errors_csv, geometry_errors)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_BASELINE_INVERSE_MODEL_QUALITY_EVIDENCE" if status == "PASS" else "DO_NOT_CLAIM_INVERSE_MODEL_QUALITY_YET",
        "training_csv": str(training_csv),
        "out_dir": str(out_dir),
        "cv_predictions_csv": str(cv_predictions_csv),
        "geometry_errors_csv": str(geometry_errors_csv),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "input_feature_contract": feature_contract,
        "training_count": training["count"],
        "quality_summary": quality_summary,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This audit uses leave-one-out KNN/IDW as a baseline diagnostic, not the final neural inverse-design model.",
            "Low cross-validation error supports using the EMX labels for inverse training; it does not replace EMX/HFSS validation of generated candidates.",
            "The quality is measured over the training table distribution, so future targets outside this feature envelope still require acquisition or extrapolation checks.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"cv_predictions={cv_predictions_csv}")
    print(f"geometry_errors={geometry_errors_csv}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", help="Comma-separated input columns; defaults to input__* columns")
    parser.add_argument("--geometry-columns", help="Comma-separated geometry columns; defaults to geom__* columns")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--k-neighbors", type=int, default=8)
    parser.add_argument("--distance-power", type=float, default=2.0)
    parser.add_argument("--min-training-rows", type=int, default=8)
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument("--max-normalized-mae", type=float, default=0.25)
    parser.add_argument("--max-normalized-rmse", type=float, default=0.35)
    parser.add_argument("--max-normalized-max-abs-error", type=float, default=0.80)
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


def _training_matrix(rows: list[dict[str, str]], input_columns: list[str], geometry_columns: list[str]) -> dict[str, Any]:
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


def _leave_one_out_quality(
    training: dict[str, Any],
    source_rows: list[dict[str, str]],
    input_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    x = np.asarray(training["x"], dtype=float)
    y = np.asarray(training["y"], dtype=float)
    lows = np.min(x, axis=0)
    highs = np.max(x, axis=0)
    denom = np.maximum(highs - lows, float(args.normalization_floor))
    x_norm = (x - lows) / denom
    k = max(1, min(int(args.k_neighbors), x.shape[0] - 1))

    predictions = np.zeros_like(y, dtype=float)
    mean_distances: list[float] = []
    neighbor_records: list[list[int]] = []
    for idx in range(x.shape[0]):
        mask = np.ones(x.shape[0], dtype=bool)
        mask[idx] = False
        candidate_indices = np.nonzero(mask)[0]
        distances = np.linalg.norm(x_norm[candidate_indices] - x_norm[idx][None, :], axis=1)
        order_local = np.argsort(distances)
        neighbors_local = order_local[:k]
        neighbors = [int(candidate_indices[item]) for item in neighbors_local]
        full_distances = np.full(x.shape[0], np.inf, dtype=float)
        full_distances[candidate_indices] = distances
        pred, _uncertainty, mean_distance = _predict_from_neighbors(y, full_distances, neighbors, float(args.distance_power))
        predictions[idx] = pred
        mean_distances.append(float(mean_distance))
        neighbor_records.append([int(training["source_indices"][item]) for item in neighbors])

    spans = np.maximum(np.max(y, axis=0) - np.min(y, axis=0), float(args.normalization_floor))
    errors = predictions - y
    abs_errors = np.abs(errors)
    normalized_abs_errors = abs_errors / spans[None, :]

    prediction_rows: list[dict[str, Any]] = []
    for idx in range(x.shape[0]):
        source_idx = int(training["source_indices"][idx])
        source = source_rows[source_idx] if 0 <= source_idx < len(source_rows) else {}
        row: dict[str, Any] = {
            "cv_row_index": idx,
            "source_row_index": source_idx,
            "evaluation": source.get("evaluation", ""),
            "mean_neighbor_distance": mean_distances[idx],
            "neighbor_source_indices": ";".join(str(item) for item in neighbor_records[idx]),
        }
        for col_idx, column in enumerate(input_columns):
            row[column] = float(x[idx, col_idx])
        for col_idx, column in enumerate(geometry_columns):
            suffix = column.removeprefix(str(args.geom_prefix))
            row[f"true__{suffix}"] = float(y[idx, col_idx])
            row[f"pred__{suffix}"] = float(predictions[idx, col_idx])
            row[f"abs_error__{suffix}"] = float(abs_errors[idx, col_idx])
            row[f"normalized_abs_error__{suffix}"] = float(normalized_abs_errors[idx, col_idx])
        prediction_rows.append(row)

    geometry_error_rows: list[dict[str, Any]] = []
    for col_idx, column in enumerate(geometry_columns):
        err = errors[:, col_idx]
        abs_err = abs_errors[:, col_idx]
        norm_abs = normalized_abs_errors[:, col_idx]
        geometry_error_rows.append(
            {
                "geometry_column": column,
                "span": float(spans[col_idx]),
                "mae": float(np.mean(abs_err)),
                "rmse": float(np.sqrt(np.mean(err * err))),
                "max_abs_error": float(np.max(abs_err)),
                "normalized_mae": float(np.mean(norm_abs)),
                "normalized_rmse": float(np.sqrt(np.mean(norm_abs * norm_abs))),
                "normalized_max_abs_error": float(np.max(norm_abs)),
            }
        )

    quality_summary = {
        "method": "leave_one_out_knn_idw",
        "normalization": "min_max_over_full_training_table_inputs",
        "training_count": int(x.shape[0]),
        "input_count": int(x.shape[1]),
        "geometry_count": int(y.shape[1]),
        "k_neighbors": int(k),
        "distance_power": float(args.distance_power),
        "max_normalized_mae": float(max(row["normalized_mae"] for row in geometry_error_rows)),
        "max_normalized_rmse": float(max(row["normalized_rmse"] for row in geometry_error_rows)),
        "max_normalized_max_abs_error": float(max(row["normalized_max_abs_error"] for row in geometry_error_rows)),
        "mean_neighbor_distance": float(np.mean(mean_distances)) if mean_distances else None,
        "worst_normalized_rmse_geometry": max(geometry_error_rows, key=lambda row: float(row["normalized_rmse"]))["geometry_column"],
        "worst_normalized_max_error_geometry": max(geometry_error_rows, key=lambda row: float(row["normalized_max_abs_error"]))["geometry_column"],
        "per_geometry": {str(row["geometry_column"]): row for row in geometry_error_rows},
    }
    return prediction_rows, geometry_error_rows, quality_summary


def _predict_from_neighbors(
    y: np.ndarray,
    distances: np.ndarray,
    neighbors: list[int],
    distance_power: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    if not neighbors:
        raise ValueError("No neighbors available for inverse quality audit")
    neighbor_y = y[neighbors]
    neighbor_distances = distances[neighbors]
    zero_mask = neighbor_distances <= 1.0e-12
    if np.any(zero_mask):
        weights = zero_mask.astype(float)
    else:
        weights = 1.0 / np.maximum(neighbor_distances, 1.0e-12) ** max(float(distance_power), 1.0e-12)
    weights = weights / np.sum(weights)
    pred = np.sum(neighbor_y * weights[:, None], axis=0)
    uncertainty = np.sqrt(np.sum(((neighbor_y - pred[None, :]) ** 2) * weights[:, None], axis=0))
    return pred, uncertainty, float(np.mean(neighbor_distances))


def _feature_contract_checks(input_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    contract = _input_feature_contract(input_columns, args)
    required = {
        "lp": bool(contract["lp_columns"]),
        "ls": bool(contract["ls_columns"]),
        "q": bool(contract["q_columns"]),
        "k": bool(contract["k_columns"]),
    }
    return [
        _check("inverse_inputs_do_not_use_zin", not bool(args.forbid_zin_inputs) or not contract["zin_columns"], f"zin_columns={contract['zin_columns']}"),
        _check("inverse_inputs_include_lp_ls_q_k", not bool(args.require_physical_feature_inputs) or all(required.values()), f"required={required}, input_columns={input_columns}"),
    ]


def _input_feature_contract(input_columns: list[str], args: argparse.Namespace) -> dict[str, Any]:
    tokens = _physical_feature_tokens(input_columns)
    return {
        "forbid_zin_inputs": bool(args.forbid_zin_inputs),
        "require_physical_feature_inputs": bool(args.require_physical_feature_inputs),
        "zin_columns": [column for column in input_columns if _is_zin_column(column)],
        "lp_columns": tokens["lp"],
        "ls_columns": tokens["ls"],
        "q_columns": tokens["q"],
        "k_columns": tokens["k"],
        "input_columns": list(input_columns),
    }


def _physical_feature_tokens(columns: list[str]) -> dict[str, list[str]]:
    return {
        "lp": [column for column in columns if _normalized_feature_name(column).startswith("lp")],
        "ls": [column for column in columns if _normalized_feature_name(column).startswith("ls")],
        "q": [column for column in columns if _normalized_feature_name(column).startswith(("q", "qp", "qs"))],
        "k": [column for column in columns if _normalized_feature_name(column).startswith(("k", "kw"))],
    }


def _is_zin_column(column: str) -> bool:
    name = _normalized_feature_name(column)
    return "zin" in name or name.startswith(("re_z", "im_z", "z_real", "z_imag"))


def _normalized_feature_name(column: str) -> str:
    text = str(column).strip().lower()
    for prefix in ("input__", "target__", "pred_", "candidate__"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature Inverse Model Quality Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Training CSV: `{summary['training_csv']}`",
        f"- Training rows: `{summary['training_count']}`",
        "",
        "## Contract",
        "",
        f"- Inputs: `{summary['input_columns']}`",
        f"- Geometry outputs: `{summary['geometry_columns']}`",
        f"- Zin inputs: `{summary['input_feature_contract']['zin_columns']}`",
        "",
        "## Quality",
        "",
    ]
    quality = summary.get("quality_summary") or {}
    if quality:
        lines.extend(
            [
                f"- Method: `{quality.get('method')}`",
                f"- K neighbors: `{quality.get('k_neighbors')}`",
                f"- Max normalized MAE: `{quality.get('max_normalized_mae')}`",
                f"- Max normalized RMSE: `{quality.get('max_normalized_rmse')}`",
                f"- Max normalized max abs error: `{quality.get('max_normalized_max_abs_error')}`",
                f"- Worst RMSE geometry: `{quality.get('worst_normalized_rmse_geometry')}`",
                "",
                "| Geometry | norm MAE | norm RMSE | norm max err | MAE | RMSE |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, item in (quality.get("per_geometry") or {}).items():
            lines.append(
                f"| {_cell(name)} | {float(item['normalized_mae']):.6g} | {float(item['normalized_rmse']):.6g} | "
                f"{float(item['normalized_max_abs_error']):.6g} | {float(item['mae']):.6g} | {float(item['rmse']):.6g} |"
            )
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(str(check['name']))} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
