#!/usr/bin/env python3
"""Predict candidate geometry from a saved Lp/Ls/Q/K inverse-model JSON.

This is the inference companion to ``train_physical_feature_inverse_model.py``.
It loads the saved polynomial-ridge baseline, accepts target physical features,
and writes a candidate CSV with ``geom__`` columns that can feed the existing
create-only layout smoke or the EMX candidate queue.

It does not run Cadence, EMX, HFSS, or ADS.
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


SUPPORTED_METHOD = "standardized_polynomial_ridge_regression"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    model_path = Path(args.model_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    model, model_error = _read_model(model_path)
    input_columns = list(model.get("input_columns") or []) if isinstance(model, dict) else []
    geometry_columns = list(model.get("geometry_columns") or []) if isinstance(model, dict) else []
    targets, target_errors = _load_targets(args, input_columns)
    feature_contract = _input_feature_contract(input_columns, args)
    feature_checks = _feature_contract_checks(input_columns, args)
    target_feature_checks, target_feature_envelope = _target_feature_envelope_checks(
        targets,
        input_columns,
        model.get("input_domain") if isinstance(model, dict) else {},
        allow_extrapolation=bool(args.allow_target_extrapolation),
    )

    checks = [
        _check("model_json_exists", model_path.is_file(), str(model_path)),
        _check("model_json_loads", model_error is None, model_error or str(model_path)),
        _check("model_method_supported", model.get("method") == SUPPORTED_METHOD if isinstance(model, dict) else False, model.get("method") if isinstance(model, dict) else None),
        _check("model_input_columns_present", bool(input_columns), ",".join(input_columns)),
        _check("model_geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("model_coefficients_present", bool(model.get("coefficients")) if isinstance(model, dict) else False, "coefficients"),
        _check("model_terms_present", bool(model.get("terms")) if isinstance(model, dict) else False, "terms"),
        _check("target_features_present", bool(targets), f"targets={len(targets)}"),
        _check("target_features_parse", not target_errors, "; ".join(target_errors)),
        *feature_checks,
        *target_feature_checks,
    ]

    candidate_rows: list[dict[str, Any]] = []
    geometry_contract: dict[str, Any] = {}
    if all(item["pass"] for item in checks):
        candidate_rows = _predict_candidates(model, targets, input_columns, geometry_columns)
        checks.append(_check("candidate_rows_present", bool(candidate_rows), f"rows={len(candidate_rows)}"))
    if args.config:
        contract_checks, geometry_contract = _candidate_geometry_contract(
            candidate_rows,
            Path(args.config).expanduser().resolve(),
            str(args.geom_prefix),
            allow_out_of_bounds=bool(args.allow_out_of_bounds),
        )
        checks.extend(contract_checks)

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    candidate_csv = out_dir / "physical_feature_saved_inverse_geometry_candidates.csv"
    summary_path = out_dir / "physical_feature_saved_inverse_prediction_summary.json"
    report_path = out_dir / "physical_feature_saved_inverse_prediction_report.md"
    _write_csv(candidate_csv, candidate_rows)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_CANDIDATE_GEOMETRIES_FOR_LAYOUT_OR_EMX_QUEUE" if status == "PASS" else "DO_NOT_USE_SAVED_MODEL_PREDICTIONS",
        "model_json": str(model_path),
        "model_source": _file_source(model_path),
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "input_feature_contract": feature_contract,
        "target_feature_envelope": target_feature_envelope,
        "candidate_geometry_contract": geometry_contract,
        "target_count": len(targets),
        "candidate_count": len(candidate_rows),
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "Predicted rows are candidate geometries, not simulator labels.",
            "Every candidate must still pass layout/DRC checks and real EMX/HFSS/ADS validation.",
            "The saved model should only be trusted inside the physical-feature envelope covered by real EMX training labels.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"candidate_csv={candidate_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target", action="append", default=[], help="Target feature as name=value, repeat for every model input")
    parser.add_argument("--target-json", help="JSON dict or list of dicts with target physical features")
    parser.add_argument("--config", help="Optional run config used to prove predicted geom__ fields rebuild within bounds")
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--allow-out-of-bounds", action="store_true")
    parser.add_argument(
        "--allow-target-extrapolation",
        action="store_true",
        help="Allow target Lp/Ls/Q/K values outside the saved model training envelope. Default is to fail instead of extrapolating.",
    )
    parser.add_argument("--forbid-zin-inputs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-physical-feature-inputs", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_model(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {}, f"missing {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return {}, "model JSON must be an object"
    return data, None


def _load_targets(args: argparse.Namespace, input_columns: list[str]) -> tuple[list[dict[str, float]], list[str]]:
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
                    targets.append(_coerce_target_dict(item, input_columns, str(args.input_prefix), errors))
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
        targets.append(_coerce_target_dict(raw, input_columns, str(args.input_prefix), errors))
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


def _predict_candidates(
    model: dict[str, Any],
    targets: list[dict[str, float]],
    input_columns: list[str],
    geometry_columns: list[str],
) -> list[dict[str, Any]]:
    rows = []
    for target_idx, target in enumerate(targets):
        geometry = _predict_model(model, target, input_columns)
        row: dict[str, Any] = {
            "candidate_id": f"saved_inverse_target_{target_idx:03d}_candidate_001",
            "target_index": int(target_idx),
            "candidate_rank": 1,
            "inverse_prediction_source": "saved_polynomial_ridge_baseline",
            "inverse_model_json": str(model.get("model_path") or ""),
        }
        for key, value in target.items():
            row[f"target__{key}"] = float(value)
        for idx, column in enumerate(geometry_columns):
            row[column] = float(geometry[idx])
        rows.append(row)
    return rows


def _predict_model(model: dict[str, Any], target: dict[str, float], input_columns: list[str]) -> np.ndarray:
    x = np.asarray([[float(target[column]) for column in input_columns]], dtype=float)
    mean = np.asarray(model["input_mean"], dtype=float)
    scale = np.asarray(model["input_scale"], dtype=float)
    terms = model["terms"]
    coefficients = np.asarray(model["coefficients"], dtype=float)
    phi = _design_matrix((x - mean[None, :]) / scale[None, :], terms)
    return (phi @ coefficients)[0]


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


def _candidate_geometry_contract(
    candidate_rows: list[dict[str, Any]],
    config_path: Path,
    geom_prefix: str,
    *,
    allow_out_of_bounds: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    contract: dict[str, Any] = {
        "config": str(config_path),
        "config_exists": config_path.is_file(),
        "candidate_count": len(candidate_rows),
        "field_order": [],
        "expected_geometry_columns": [],
        "valid_candidate_count": 0,
        "missing_field_rows": [],
        "invalid_candidate_rows": [],
        "allow_out_of_bounds": bool(allow_out_of_bounds),
    }
    checks.append(_check("inverse_model_geometry_config_exists", config_path.is_file(), str(config_path)))
    if not config_path.is_file():
        return checks, contract
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("inverse_model_geometry_config_loads", False, f"{type(exc).__name__}: {exc}"))
        return checks, contract
    checks.append(_check("inverse_model_geometry_config_loads", True, str(config_path)))
    field_order = list(adapter.field_order())
    expected_columns = [f"{geom_prefix}{field}" for field in field_order]
    contract["field_order"] = field_order
    contract["expected_geometry_columns"] = expected_columns

    missing_field_rows = []
    invalid_rows = []
    valid_count = 0
    for row_index, row in enumerate(candidate_rows):
        missing = [column for column in expected_columns if _as_float(row.get(column)) is None]
        if missing:
            missing_field_rows.append({"candidate_row": row_index, "missing": missing})
            continue
        values = [float(row[column]) for column in expected_columns]
        try:
            geometry = adapter.from_vector(values)
            bound_errors = adapter.search_space.validate(geometry)
            topology_errors = geometry.validate()
        except Exception as exc:  # noqa: BLE001
            invalid_rows.append({"candidate_row": row_index, "error": f"{type(exc).__name__}: {exc}"})
            continue
        geometry_errors = [*bound_errors, *topology_errors]
        if (bound_errors and not allow_out_of_bounds) or topology_errors:
            invalid_rows.append(
                {
                    "candidate_row": row_index,
                    "bounds_errors": bound_errors,
                    "topology_errors": topology_errors,
                    "errors": geometry_errors,
                }
            )
            continue
        valid_count += 1

    contract["valid_candidate_count"] = valid_count
    contract["missing_field_rows"] = missing_field_rows[:20]
    contract["invalid_candidate_rows"] = invalid_rows[:20]
    checks.append(
        _check(
            "saved_inverse_candidate_fields_match_config",
            bool(candidate_rows) and not missing_field_rows,
            f"expected_columns={expected_columns}, missing_rows={missing_field_rows[:20]}",
        )
    )
    checks.append(
        _check(
            "saved_inverse_candidates_rebuild_from_config",
            bool(candidate_rows) and not invalid_rows and valid_count == len(candidate_rows),
            f"valid={valid_count}, candidates={len(candidate_rows)}, invalid_rows={invalid_rows[:20]}",
        )
    )
    return checks, contract


def _target_feature_envelope_checks(
    targets: list[dict[str, float]],
    input_columns: list[str],
    input_domain: Any,
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
        "input_domain": input_domain if isinstance(input_domain, dict) else {},
        "missing_domain_columns": missing_domain,
        "out_of_range": out_of_range,
    }
    checks = [
        _check("saved_model_target_feature_training_envelope_present", not missing_domain, f"missing_domain_columns={missing_domain}"),
        _check(
            "saved_model_target_features_inside_training_envelope",
            bool(allow_extrapolation) or not out_of_range,
            f"allow_target_extrapolation={allow_extrapolation}, out_of_range={out_of_range}",
        ),
    ]
    return checks, envelope


def _feature_contract_checks(input_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    contract = _input_feature_contract(input_columns, args)
    required = {
        "lp": bool(contract["lp_columns"]),
        "ls": bool(contract["ls_columns"]),
        "q": bool(contract["q_columns"]),
        "k": bool(contract["k_columns"]),
    }
    return [
        _check("saved_model_inputs_do_not_use_zin", not bool(args.forbid_zin_inputs) or not contract["zin_columns"], f"zin_columns={contract['zin_columns']}"),
        _check("saved_model_inputs_include_lp_ls_q_k", not bool(args.require_physical_feature_inputs) or all(required.values()), f"required={required}, input_columns={input_columns}"),
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
    lines = [
        "# Saved Physical-Feature Inverse Prediction",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Model JSON: `{summary['model_json']}`",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        f"- Target count: `{summary['target_count']}`",
        f"- Candidate count: `{summary['candidate_count']}`",
        "",
        "## Contract",
        "",
        f"- Inputs: `{summary['input_columns']}`",
        f"- Geometry outputs: `{summary['geometry_columns']}`",
        f"- Zin inputs: `{summary['input_feature_contract']['zin_columns']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(str(check['name']))} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
