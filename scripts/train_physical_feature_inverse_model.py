#!/usr/bin/env python3
"""Train a saved baseline inverse model: Lp/Ls/Q/K -> transformer geometry.

This script turns ``physical_feature_inverse_training_table.csv`` into a
traceable model artifact.  It intentionally uses only NumPy: standardized
physical-feature inputs, polynomial terms, and multi-output ridge regression.
The saved JSON contains every coefficient needed to reproduce predictions.

The model is a baseline for the next S8P workflow, not a replacement for
layout checks, EMX labels, or EMX/HFSS validation.
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

from rfic_transformer_inverse_design.api import TransformerOptimizationAdapter, load_run_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    input_columns = _resolve_columns(rows, args.input_prefix, args.input_columns)
    geometry_columns = _resolve_columns(rows, args.geom_prefix, args.geometry_columns)
    training = _training_matrix(rows, input_columns, geometry_columns)
    targets, target_errors = _load_targets(args, input_columns, args.input_prefix)
    feature_contract = _input_feature_contract(input_columns, args)
    feature_checks = _feature_contract_checks(input_columns, args)
    geometry_contract, geometry_contract_checks = _geometry_contract(geometry_columns, args)
    input_domain = _input_domain_summary(training, input_columns)

    checks = [
        _check("training_csv_exists", training_csv.is_file(), str(training_csv)),
        _check("training_rows_present", bool(rows), f"rows={len(rows)}"),
        _check(
            "training_rows_meet_minimum",
            training["count"] >= int(args.min_training_rows),
            f"usable_rows={training['count']}, minimum={args.min_training_rows}",
        ),
        _check("input_columns_present", bool(input_columns), ",".join(input_columns)),
        _check("geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("training_matrix_present", training["count"] > 0, f"usable_rows={training['count']}"),
        _check("target_features_parse", not target_errors, "; ".join(target_errors)),
        *feature_checks,
        *geometry_contract_checks,
    ]

    model: dict[str, Any] = {}
    quality_summary: dict[str, Any] = {}
    cv_predictions: list[dict[str, Any]] = []
    geometry_errors: list[dict[str, Any]] = []
    target_prediction_rows: list[dict[str, Any]] = []
    target_prediction_contract: dict[str, Any] = {}
    target_feature_envelope: dict[str, Any] = {}

    if all(item["pass"] for item in checks):
        model = _fit_model(training, input_columns, geometry_columns, args)
        model["input_domain"] = input_domain
        cv_predictions, geometry_errors, quality_summary = _leave_one_out_quality(
            training,
            rows,
            input_columns,
            geometry_columns,
            args,
        )
        checks.extend(
            [
                _check("model_coefficients_present", bool(model.get("coefficients")), f"terms={len(model.get('terms') or [])}"),
                _check("cv_predictions_present", bool(cv_predictions), f"rows={len(cv_predictions)}"),
                _check(
                    "baseline_model_normalized_mae_within_limit",
                    quality_summary.get("max_normalized_mae", math.inf) <= float(args.max_normalized_mae),
                    f"max={quality_summary.get('max_normalized_mae')} limit={args.max_normalized_mae}",
                ),
                _check(
                    "baseline_model_normalized_rmse_within_limit",
                    quality_summary.get("max_normalized_rmse", math.inf) <= float(args.max_normalized_rmse),
                    f"max={quality_summary.get('max_normalized_rmse')} limit={args.max_normalized_rmse}",
                ),
                _check(
                    "baseline_model_normalized_max_error_within_limit",
                    quality_summary.get("max_normalized_max_abs_error", math.inf) <= float(args.max_normalized_max_abs_error),
                    f"max={quality_summary.get('max_normalized_max_abs_error')} limit={args.max_normalized_max_abs_error}",
                ),
            ]
        )
        if targets:
            target_feature_checks, target_feature_envelope = _target_feature_envelope_checks(
                targets,
                input_columns,
                input_domain,
                allow_extrapolation=bool(args.allow_target_extrapolation),
            )
            checks.extend(target_feature_checks)
            if all(item["pass"] for item in target_feature_checks):
                target_prediction_rows = _predict_targets(model, targets, input_columns, geometry_columns)
                checks.append(_check("target_predictions_present", bool(target_prediction_rows), f"rows={len(target_prediction_rows)}"))
                if args.config:
                    target_checks, target_prediction_contract = _target_prediction_geometry_contract(
                        target_prediction_rows,
                        Path(args.config).expanduser().resolve(),
                        args.geom_prefix,
                    )
                    checks.extend(target_checks)
            else:
                target_prediction_contract = {
                    "decision": "SKIP_TARGET_GEOMETRY_PREDICTION_OUTSIDE_TRAINING_ENVELOPE",
                    "candidate_count": 0,
                    "reason": "target physical features are outside the training input domain",
                }

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    model_path = out_dir / "physical_feature_inverse_model.json"
    summary_path = out_dir / "physical_feature_inverse_model_training_summary.json"
    report_path = out_dir / "physical_feature_inverse_model_training_report.md"
    cv_predictions_csv = out_dir / "physical_feature_inverse_model_training_cv_predictions.csv"
    geometry_errors_csv = out_dir / "physical_feature_inverse_model_training_geometry_errors.csv"
    target_predictions_csv = out_dir / "physical_feature_inverse_model_target_predictions.csv"

    if model:
        model["model_source"] = _file_source(training_csv)
        model["model_path"] = str(model_path)
        model["training_summary_path"] = str(summary_path)
        model["input_feature_contract"] = feature_contract
        model["geometry_contract"] = geometry_contract
        model_path.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        model_path.write_text("", encoding="utf-8")
    _write_csv(cv_predictions_csv, cv_predictions)
    _write_csv(geometry_errors_csv, geometry_errors)
    _write_csv(target_predictions_csv, target_prediction_rows)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_SAVED_BASELINE_INVERSE_MODEL_FOR_GEOMETRY_CANDIDATES" if status == "PASS" else "DO_NOT_USE_SAVED_INVERSE_MODEL_YET",
        "training_csv": str(training_csv),
        "training_source": _file_source(training_csv),
        "out_dir": str(out_dir),
        "model_json": str(model_path),
        "report": str(report_path),
        "cv_predictions_csv": str(cv_predictions_csv),
        "geometry_errors_csv": str(geometry_errors_csv),
        "target_predictions_csv": str(target_predictions_csv),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "input_feature_contract": feature_contract,
        "input_domain": input_domain,
        "target_feature_envelope": target_feature_envelope,
        "geometry_contract": geometry_contract,
        "target_prediction_geometry_contract": target_prediction_contract,
        "training_count": training["count"],
        "target_count": len(targets),
        "target_prediction_count": len(target_prediction_rows),
        "method": model.get("method") if model else "not_trained",
        "quality_summary": quality_summary,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This is a saved deterministic baseline model, not the final neural inverse-design model.",
            "Model predictions are candidate geometries; they must still pass layout/DRC, EMX .s8p generation, and EMX/HFSS/ADS Lp/Ls/Q/K/Kw validation.",
            "The model is valid only within the physical-feature envelope represented by the real simulator labels used for training.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"model={model_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", help="Comma-separated input columns; defaults to input__* columns")
    parser.add_argument("--geometry-columns", help="Comma-separated geometry columns; defaults to geom__* columns")
    parser.add_argument("--config", help="Optional run config used to verify geometry field order and target prediction bounds")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--degree", type=int, choices=(1, 2), default=2)
    parser.add_argument("--ridge-alpha", type=float, default=1.0e-6)
    parser.add_argument("--min-training-rows", type=int, default=8)
    parser.add_argument("--normalization-floor", type=float, default=1.0e-12)
    parser.add_argument("--target", action="append", default=[], help="Target feature as name=value, repeat for every input feature")
    parser.add_argument("--target-json", help="JSON dict or list of dicts with target physical features")
    parser.add_argument("--max-normalized-mae", type=float, default=0.35)
    parser.add_argument("--max-normalized-rmse", type=float, default=0.50)
    parser.add_argument("--max-normalized-max-abs-error", type=float, default=1.25)
    parser.add_argument(
        "--allow-target-extrapolation",
        action="store_true",
        help="Allow target Lp/Ls/Q/K values outside the training feature envelope. Default is to fail instead of extrapolating.",
    )
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
        return {"count": 0, "x": np.empty((0, len(input_columns))), "y": np.empty((0, len(geometry_columns))), "source_indices": []}
    return {"count": len(x), "x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float), "source_indices": source_indices}


def _fit_model(training: dict[str, Any], input_columns: list[str], geometry_columns: list[str], args: argparse.Namespace) -> dict[str, Any]:
    x = np.asarray(training["x"], dtype=float)
    y = np.asarray(training["y"], dtype=float)
    x_mean = np.mean(x, axis=0)
    x_scale = np.maximum(np.std(x, axis=0), float(args.normalization_floor))
    terms = _polynomial_terms(len(input_columns), int(args.degree))
    phi = _design_matrix((x - x_mean[None, :]) / x_scale[None, :], terms)
    coefficients = _ridge_fit(phi, y, float(args.ridge_alpha))
    predictions = phi @ coefficients
    residual = predictions - y
    return {
        "method": "standardized_polynomial_ridge_regression",
        "degree": int(args.degree),
        "ridge_alpha": float(args.ridge_alpha),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "training_count": int(x.shape[0]),
        "input_mean": [float(value) for value in x_mean],
        "input_scale": [float(value) for value in x_scale],
        "terms": terms,
        "coefficients": coefficients.tolist(),
        "training_residual_summary": {
            "mae": [float(value) for value in np.mean(np.abs(residual), axis=0)],
            "rmse": [float(value) for value in np.sqrt(np.mean(residual * residual, axis=0))],
        },
    }


def _input_domain_summary(training: dict[str, Any], input_columns: list[str]) -> dict[str, Any]:
    x = np.asarray(training.get("x", np.empty((0, len(input_columns)))), dtype=float)
    out: dict[str, Any] = {
        "training_count": int(x.shape[0]) if x.ndim == 2 else 0,
        "columns": list(input_columns),
        "per_feature": {},
    }
    if x.ndim != 2 or x.shape[0] == 0:
        return out
    for idx, column in enumerate(input_columns):
        values = x[:, idx]
        out["per_feature"][column] = {
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "span": float(np.max(values) - np.min(values)),
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
        }
    return out


def _target_feature_envelope_checks(
    targets: list[dict[str, float]],
    input_columns: list[str],
    input_domain: dict[str, Any],
    *,
    allow_extrapolation: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    per_feature = input_domain.get("per_feature") if isinstance(input_domain, dict) else {}
    missing_domain = [column for column in input_columns if not isinstance(per_feature, dict) or column not in per_feature]
    out_of_range: list[dict[str, Any]] = []
    for target_idx, target in enumerate(targets):
        for column in input_columns:
            domain = per_feature.get(column, {}) if isinstance(per_feature, dict) else {}
            value = target.get(column)
            min_value = _as_float(domain.get("min"))
            max_value = _as_float(domain.get("max"))
            if value is None or min_value is None or max_value is None:
                continue
            if float(value) < min_value or float(value) > max_value:
                out_of_range.append(
                    {
                        "target_index": target_idx,
                        "feature": column,
                        "value": float(value),
                        "min": float(min_value),
                        "max": float(max_value),
                    }
                )
    envelope = {
        "allow_target_extrapolation": bool(allow_extrapolation),
        "target_count": len(targets),
        "input_domain": input_domain,
        "missing_domain_columns": missing_domain,
        "out_of_range": out_of_range,
    }
    checks = [
        _check("target_feature_training_envelope_present", not missing_domain, f"missing_domain_columns={missing_domain}"),
        _check(
            "target_features_inside_training_envelope",
            bool(allow_extrapolation) or not out_of_range,
            f"allow_target_extrapolation={allow_extrapolation}, out_of_range={out_of_range}",
        ),
    ]
    return checks, envelope


def _ridge_fit(phi: np.ndarray, y: np.ndarray, ridge_alpha: float) -> np.ndarray:
    regularizer = np.eye(phi.shape[1], dtype=float) * max(float(ridge_alpha), 0.0)
    if regularizer.shape[0]:
        regularizer[0, 0] = 0.0
    lhs = phi.T @ phi + regularizer
    rhs = phi.T @ y
    try:
        return np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(lhs, rhs, rcond=None)[0]


def _polynomial_terms(feature_count: int, degree: int) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = [{"name": "constant", "powers": [0] * feature_count}]
    for idx in range(feature_count):
        powers = [0] * feature_count
        powers[idx] = 1
        terms.append({"name": f"x{idx}", "powers": powers})
    if degree >= 2:
        for first in range(feature_count):
            for second in range(first, feature_count):
                powers = [0] * feature_count
                powers[first] += 1
                powers[second] += 1
                name = f"x{first}^2" if first == second else f"x{first}:x{second}"
                terms.append({"name": name, "powers": powers})
    return terms


def _design_matrix(x_norm: np.ndarray, terms: list[dict[str, Any]]) -> np.ndarray:
    if x_norm.size == 0:
        return np.empty((0, len(terms)))
    columns = []
    for term in terms:
        powers = np.asarray(term["powers"], dtype=int)
        values = np.ones(x_norm.shape[0], dtype=float)
        for idx, power in enumerate(powers):
            if power:
                values *= x_norm[:, idx] ** int(power)
        columns.append(values)
    return np.column_stack(columns)


def _predict_model(model: dict[str, Any], target: dict[str, float], input_columns: list[str]) -> np.ndarray:
    x = np.asarray([[float(target[column]) for column in input_columns]], dtype=float)
    mean = np.asarray(model["input_mean"], dtype=float)
    scale = np.asarray(model["input_scale"], dtype=float)
    terms = model["terms"]
    coefficients = np.asarray(model["coefficients"], dtype=float)
    phi = _design_matrix((x - mean[None, :]) / scale[None, :], terms)
    return (phi @ coefficients)[0]


def _leave_one_out_quality(
    training: dict[str, Any],
    source_rows: list[dict[str, str]],
    input_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    x = np.asarray(training["x"], dtype=float)
    y = np.asarray(training["y"], dtype=float)
    if x.shape[0] < 2:
        return [], [], {}
    predictions = np.zeros_like(y, dtype=float)
    for idx in range(x.shape[0]):
        mask = np.ones(x.shape[0], dtype=bool)
        mask[idx] = False
        fold_training = {
            "count": int(np.sum(mask)),
            "x": x[mask],
            "y": y[mask],
            "source_indices": [int(item) for row_idx, item in enumerate(training["source_indices"]) if mask[row_idx]],
        }
        fold_model = _fit_model(fold_training, input_columns, geometry_columns, args)
        target = {column: float(x[idx, col_idx]) for col_idx, column in enumerate(input_columns)}
        predictions[idx] = _predict_model(fold_model, target, input_columns)

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
        "method": "leave_one_out_polynomial_ridge",
        "training_count": int(x.shape[0]),
        "input_count": int(x.shape[1]),
        "geometry_count": int(y.shape[1]),
        "degree": int(args.degree),
        "ridge_alpha": float(args.ridge_alpha),
        "max_normalized_mae": float(max(row["normalized_mae"] for row in geometry_error_rows)),
        "max_normalized_rmse": float(max(row["normalized_rmse"] for row in geometry_error_rows)),
        "max_normalized_max_abs_error": float(max(row["normalized_max_abs_error"] for row in geometry_error_rows)),
        "worst_normalized_rmse_geometry": max(geometry_error_rows, key=lambda row: float(row["normalized_rmse"]))["geometry_column"],
        "worst_normalized_max_error_geometry": max(geometry_error_rows, key=lambda row: float(row["normalized_max_abs_error"]))["geometry_column"],
        "per_geometry": {str(row["geometry_column"]): row for row in geometry_error_rows},
    }
    return prediction_rows, geometry_error_rows, quality_summary


def _load_targets(args: argparse.Namespace, input_columns: list[str], input_prefix: str) -> tuple[list[dict[str, float]], list[str]]:
    errors: list[str] = []
    targets: list[dict[str, float]] = []
    if args.target_json:
        path = Path(args.target_json).expanduser().resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_targets = data if isinstance(data, list) else [data]
            if not all(isinstance(item, dict) for item in raw_targets):
                errors.append("--target-json must contain a dict or list of dicts")
            else:
                for target_idx, item in enumerate(raw_targets):
                    _append_target_schema_errors(item, target_idx, errors)
                    targets.append(_coerce_target_dict(item, input_columns, input_prefix, errors))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"target json error: {type(exc).__name__}: {exc}")
    if args.target:
        raw: dict[str, Any] = {}
        for item in args.target:
            if "=" not in item:
                errors.append(f"target must be name=value, got {item!r}")
                continue
            key, value = item.split("=", 1)
            raw[key.strip()] = value.strip()
        _append_target_schema_errors(raw, 0, errors)
        targets.append(_coerce_target_dict(raw, input_columns, input_prefix, errors))
    return [target for target in targets if target], errors


def _append_target_schema_errors(raw: dict[str, Any], target_idx: int, errors: list[str]) -> None:
    zin_keys = sorted(str(key) for key in raw if _is_zin_column(str(key)))
    if zin_keys:
        errors.append(f"target {target_idx} must use physical features only; remove Zin fields {zin_keys}")


def _coerce_target_dict(raw: dict[str, Any], input_columns: list[str], input_prefix: str, errors: list[str]) -> dict[str, float]:
    target: dict[str, float] = {}
    for column in input_columns:
        aliases = (column, column.removeprefix(input_prefix))
        value = None
        for alias in aliases:
            if alias in raw:
                value = _as_float(raw.get(alias))
                break
        if value is None:
            errors.append(f"missing target feature {column}")
            return {}
        target[column] = float(value)
    return target


def _predict_targets(
    model: dict[str, Any],
    targets: list[dict[str, float]],
    input_columns: list[str],
    geometry_columns: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for idx, target in enumerate(targets):
        geometry = _predict_model(model, target, input_columns)
        row: dict[str, Any] = {
            "candidate_id": f"saved_inverse_target_{idx:03d}_candidate_001",
            "target_index": idx,
            "candidate_rank": 1,
            "inverse_prediction_source": "saved_polynomial_ridge_baseline",
        }
        for key, value in target.items():
            row[f"target__{key}"] = float(value)
        for col_idx, column in enumerate(geometry_columns):
            row[column] = float(geometry[col_idx])
        rows.append(row)
    return rows


def _geometry_contract(geometry_columns: list[str], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract: dict[str, Any] = {
        "source": "geometry_columns",
        "config": str(Path(args.config).expanduser().resolve()) if args.config else "",
        "field_order": [],
        "expected_geometry_columns": [],
    }
    if not args.config:
        return contract, []
    config_path = Path(args.config).expanduser().resolve()
    checks = [_check("inverse_model_geometry_config_exists", config_path.is_file(), str(config_path))]
    if not config_path.is_file():
        return contract, checks
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("inverse_model_geometry_config_loads", False, f"{type(exc).__name__}: {exc}"))
        return contract, checks
    field_order = list(adapter.field_order())
    expected_columns = [f"{args.geom_prefix}{field}" for field in field_order]
    missing = [column for column in expected_columns if column not in geometry_columns]
    contract.update(
        {
            "source": "config_adapter_field_order",
            "field_order": field_order,
            "expected_geometry_columns": expected_columns,
        }
    )
    checks.append(_check("inverse_model_geometry_config_loads", True, str(config_path)))
    checks.append(
        _check(
            "inverse_model_geometry_columns_cover_config_field_order",
            not missing,
            f"missing={missing}, expected_columns={expected_columns}",
        )
    )
    return contract, checks


def _target_prediction_geometry_contract(
    target_rows: list[dict[str, Any]],
    config_path: Path,
    geom_prefix: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    contract: dict[str, Any] = {
        "config": str(config_path),
        "candidate_count": len(target_rows),
        "valid_candidate_count": 0,
        "invalid_candidate_rows": [],
    }
    if not config_path.is_file():
        return [_check("inverse_model_target_config_exists", False, str(config_path))], contract
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
    except Exception as exc:  # noqa: BLE001
        return [_check("inverse_model_target_config_loads", False, f"{type(exc).__name__}: {exc}")], contract
    field_order = list(adapter.field_order())
    expected_columns = [f"{geom_prefix}{field}" for field in field_order]
    invalid_rows = []
    valid_count = 0
    for row_idx, row in enumerate(target_rows):
        values = [_as_float(row.get(column)) for column in expected_columns]
        if any(value is None for value in values):
            invalid_rows.append({"candidate_row": row_idx, "missing_or_invalid": expected_columns})
            continue
        try:
            geometry = adapter.from_vector([float(value) for value in values if value is not None])
            bound_errors = adapter.search_space.validate(geometry)
        except Exception as exc:  # noqa: BLE001
            invalid_rows.append({"candidate_row": row_idx, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if bound_errors:
            invalid_rows.append({"candidate_row": row_idx, "errors": bound_errors})
            continue
        valid_count += 1
    contract["valid_candidate_count"] = valid_count
    contract["invalid_candidate_rows"] = invalid_rows[:20]
    checks.append(
        _check(
            "inverse_model_target_predictions_rebuild_from_config",
            bool(target_rows) and not invalid_rows and valid_count == len(target_rows),
            f"valid={valid_count}, candidates={len(target_rows)}, invalid_rows={invalid_rows[:20]}",
        )
    )
    return checks, contract


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


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    quality = summary.get("quality_summary") or {}
    lines = [
        "# Saved Physical-Feature Inverse Model",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Method: `{summary['method']}`",
        f"- Training rows: `{summary['training_count']}`",
        f"- Model JSON: `{summary['model_json']}`",
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
    if quality:
        lines.extend(
            [
                f"- Method: `{quality.get('method')}`",
                f"- Degree: `{quality.get('degree')}`",
                f"- Ridge alpha: `{quality.get('ridge_alpha')}`",
                f"- Max normalized MAE: `{quality.get('max_normalized_mae')}`",
                f"- Max normalized RMSE: `{quality.get('max_normalized_rmse')}`",
                f"- Max normalized max abs error: `{quality.get('max_normalized_max_abs_error')}`",
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


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
