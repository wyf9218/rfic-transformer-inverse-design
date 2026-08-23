#!/usr/bin/env python3
"""Build an inverse-design training table: physical features -> geometry.

The new inverse-design objective is to use Lp/Ls/Q/K as model inputs and output
transformer geometry. This script converts completed simulator rows into an
auditable ML-ready table with input physical-feature columns and output
``geom__`` columns.

It does not train a model and it does not create labels.
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


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    feature_columns = _split_columns(args.feature_columns)
    geometry_columns, geometry_contract, geometry_contract_checks = _geometry_columns(rows, args)
    training_rows, reject_summary = _training_rows(rows, dataset_dir, feature_columns, geometry_columns, args)
    feature_contract_checks = _feature_contract_checks(feature_columns, args)

    training_csv = out_dir / "physical_feature_inverse_training_table.csv"
    manifest_path = out_dir / "physical_feature_inverse_training_manifest.json"
    report_path = out_dir / "physical_feature_inverse_training_report.md"
    _write_csv(training_csv, training_rows)
    checks = [
        _check("dataset_rows_csv_exists", dataset_csv.is_file(), str(dataset_csv)),
        _check("dataset_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("feature_columns_present", bool(feature_columns), ",".join(feature_columns)),
        _check("geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("training_rows_present", bool(training_rows), f"rows={len(training_rows)}"),
        *geometry_contract_checks,
        *feature_contract_checks,
    ]
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_FOR_PHYSICAL_FEATURE_INVERSE_MODEL_TRAINING" if status == "PASS" else "DO_NOT_USE_INVERSE_TRAINING_TABLE",
        "dataset_dir": str(dataset_dir),
        "dataset_source": _file_source(dataset_csv),
        "out_dir": str(out_dir),
        "training_csv": str(training_csv),
        "feature_columns": feature_columns,
        "input_columns": [f"{args.input_prefix}{column}" for column in feature_columns],
        "input_feature_contract": _input_feature_contract(feature_columns, args),
        "geometry_contract": geometry_contract,
        "geometry_columns": geometry_columns,
        "row_count": len(rows),
        "training_count": len(training_rows),
        "reject_summary": reject_summary,
        "feature_summary": _range_summary(training_rows, [f"{args.input_prefix}{column}" for column in feature_columns]),
        "geometry_summary": _range_summary(training_rows, geometry_columns),
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This table is only as reliable as the simulator-derived labels in dataset_rows.csv.",
            "Predicted geometry from any downstream model must still pass layout/DRC checks and EMX/HFSS validation.",
            "The default Q representation is one explicit scalar q_center; if Qp/Qs must be kept separately for an experiment, pass those columns through --feature-columns.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(manifest), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={manifest['decision']}")
    print(f"training_csv={training_csv}")
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument(
        "--geometry-columns",
        help="Comma-separated independent geometry columns. Overrides span inference and --config field order.",
    )
    parser.add_argument(
        "--config",
        help="Optional TransformerRunConfig YAML. When supplied, geometry columns are forced to the config adapter field order so candidate CSVs can feed EMX.",
    )
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--min-geometry-span", type=float, default=1e-12)
    parser.add_argument("--require-touchstone-path", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-touchstone-exists", action="store_true")
    parser.add_argument(
        "--forbid-zin-inputs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail if any inverse-model input column is a Zin column.",
    )
    parser.add_argument(
        "--require-physical-feature-inputs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require inverse-model input columns to include Lp, Ls, Q/Qp/Qs, and K evidence.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _split_columns(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _infer_geometry_columns(rows: list[dict[str, str]], prefix: str, min_span: float) -> list[str]:
    if not rows:
        return []
    candidates = sorted(key for key in rows[0] if key.startswith(prefix))
    selected = []
    for key in candidates:
        values = [_as_float(row.get(key)) for row in rows]
        finite = [value for value in values if value is not None]
        if len(finite) < 2:
            continue
        if max(finite) - min(finite) <= float(min_span):
            continue
        selected.append(key)
    return selected


def _geometry_columns(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    if args.geometry_columns:
        columns = _split_columns(args.geometry_columns)
        available = set().union(*(row.keys() for row in rows)) if rows else set()
        missing = [column for column in columns if column not in available]
        contract = {
            "source": "explicit_geometry_columns",
            "field_order": [column.removeprefix(args.geom_prefix) for column in columns],
            "geometry_columns": columns,
        }
        checks = [
            _check(
                "explicit_inverse_geometry_columns_present",
                not missing,
                f"missing={missing}, columns={columns}",
            )
        ]
        return columns, contract, checks
    if not args.config:
        columns = _infer_geometry_columns(rows, args.geom_prefix, args.min_geometry_span)
        return columns, {"source": "span_inferred", "field_order": [], "geometry_columns": columns}, []
    config_path = Path(args.config).expanduser().resolve()
    contract: dict[str, Any] = {
        "source": "config_adapter_field_order",
        "config": str(config_path),
        "config_exists": config_path.is_file(),
        "field_order": [],
        "geometry_columns": [],
    }
    checks = [_check("inverse_geometry_config_exists", config_path.is_file(), str(config_path))]
    if not config_path.is_file():
        return [], contract, checks
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
    except Exception as exc:  # noqa: BLE001 - exact config error is evidence.
        checks.append(_check("inverse_geometry_config_loads", False, f"{type(exc).__name__}: {exc}"))
        return [], contract, checks
    field_order = list(adapter.field_order())
    columns = [f"{args.geom_prefix}{field}" for field in field_order]
    contract["field_order"] = field_order
    contract["geometry_columns"] = columns
    checks.append(_check("inverse_geometry_config_loads", True, str(config_path)))
    if rows:
        missing = [column for column in columns if column not in rows[0]]
        checks.append(
            _check(
                "inverse_geometry_columns_cover_config_field_order",
                not missing,
                f"missing={missing}, columns={columns}",
            )
        )
    else:
        checks.append(_check("inverse_geometry_columns_cover_config_field_order", False, "dataset has no rows"))
    return columns, contract, checks


def _training_rows(
    rows: list[dict[str, str]],
    dataset_dir: Path,
    feature_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out = []
    rejects = {
        "not_ok": 0,
        "missing_feature": 0,
        "missing_geometry": 0,
        "missing_touchstone_path": 0,
        "missing_touchstone_file": 0,
    }
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            rejects["not_ok"] += 1
            continue
        touchstone_raw = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if args.require_touchstone_path and not touchstone_raw:
            rejects["missing_touchstone_path"] += 1
            continue
        touchstone_path = _resolve(dataset_dir, touchstone_raw) if touchstone_raw else None
        if args.check_touchstone_exists and (touchstone_path is None or not touchstone_path.is_file()):
            rejects["missing_touchstone_file"] += 1
            continue
        feature_values = {column: _as_float(row.get(column)) for column in feature_columns}
        if any(value is None for value in feature_values.values()):
            rejects["missing_feature"] += 1
            continue
        geometry_values = {column: _as_float(row.get(column)) for column in geometry_columns}
        if any(value is None for value in geometry_values.values()):
            rejects["missing_geometry"] += 1
            continue
        record: dict[str, Any] = {
            "row_index": idx,
            "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or f"row_{idx}",
            "touchstone_path": "" if touchstone_path is None else str(touchstone_path),
        }
        for column, value in feature_values.items():
            record[f"{args.input_prefix}{column}"] = float(value)
        for column, value in geometry_values.items():
            record[column] = float(value)
        out.append(record)
    return out, rejects


def _range_summary(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, dict[str, float | None]]:
    summary = {}
    for column in columns:
        values = np.asarray([float(row[column]) for row in rows if _as_float(row.get(column)) is not None], dtype=float)
        if values.size == 0:
            summary[column] = {"min": None, "max": None, "mean": None, "std": None}
        else:
            summary[column] = {
                "min": float(np.min(values)),
                "max": float(np.max(values)),
                "mean": float(np.mean(values)),
                "std": float(np.std(values)),
            }
    return summary


def _feature_contract_checks(feature_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    zin_columns = [column for column in feature_columns if _is_zin_column(column)]
    tokens = _physical_feature_tokens(feature_columns)
    required = {
        "lp": bool(tokens["lp"]),
        "ls": bool(tokens["ls"]),
        "q": bool(tokens["q"]),
        "k": bool(tokens["k"]),
    }
    return [
        _check(
            "inverse_inputs_do_not_use_zin",
            not bool(args.forbid_zin_inputs) or not zin_columns,
            f"zin_columns={zin_columns}",
        ),
        _check(
            "inverse_inputs_include_lp_ls_q_k",
            not bool(args.require_physical_feature_inputs) or all(required.values()),
            f"required={required}, feature_columns={feature_columns}",
        ),
    ]


def _input_feature_contract(feature_columns: list[str], args: argparse.Namespace) -> dict[str, Any]:
    tokens = _physical_feature_tokens(feature_columns)
    return {
        "forbid_zin_inputs": bool(args.forbid_zin_inputs),
        "require_physical_feature_inputs": bool(args.require_physical_feature_inputs),
        "zin_columns": [column for column in feature_columns if _is_zin_column(column)],
        "lp_columns": tokens["lp"],
        "ls_columns": tokens["ls"],
        "q_columns": tokens["q"],
        "k_columns": tokens["k"],
        "feature_columns": list(feature_columns),
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


def _resolve(dataset_dir: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else dataset_dir / path


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
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
        "# Physical-Feature Inverse Training Table",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Training rows: `{summary['training_count']}`",
        f"Input columns: `{', '.join(summary['input_columns'])}`",
        f"Geometry columns: `{len(summary['geometry_columns'])}`",
        f"Training CSV: `{summary['training_csv']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
