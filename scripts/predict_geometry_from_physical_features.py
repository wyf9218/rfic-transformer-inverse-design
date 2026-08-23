#!/usr/bin/env python3
"""Predict candidate transformer geometry from target physical features.

This is a transparent baseline inverse model for the new workflow:

    Lp/Ls/Q/K -> candidate geom__ fields

It trains a KNN inverse mapping from a table created by
``build_physical_feature_inverse_training_table.py``. The output CSV preserves
``geom__`` columns so it can feed ``run_candidate_queue_dataset.py`` or the
8-worker parallel runner. Candidate rows are not accepted designs until they
pass layout checks and EMX/HFSS validation.
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
    input_columns = _resolve_input_columns(rows, args.input_prefix, args.input_columns)
    geometry_columns = _resolve_geometry_columns(rows, args.geom_prefix)
    targets, target_errors = _load_targets(args, input_columns, args.input_prefix)
    training = _training_matrix(rows, input_columns, geometry_columns)
    feature_contract_checks = _feature_contract_checks(input_columns, args)

    checks = [
        _check("training_csv_exists", training_csv.is_file(), str(training_csv)),
        _check("training_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("input_columns_present", bool(input_columns), ",".join(input_columns)),
        _check("geometry_columns_present", bool(geometry_columns), f"columns={len(geometry_columns)}"),
        _check("target_features_present", bool(targets), f"targets={len(targets)}"),
        _check("target_features_parse", not target_errors, "; ".join(target_errors)),
        _check("training_matrix_present", training["count"] > 0, f"rows={training['count']}"),
        *feature_contract_checks,
    ]

    candidate_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    candidate_geometry_contract: dict[str, Any] = {}
    if all(item["pass"] for item in checks):
        candidate_rows, diagnostics = _predict_candidates(training, targets, input_columns, geometry_columns, args)
        checks.append(_check("candidate_rows_present", bool(candidate_rows), f"rows={len(candidate_rows)}"))
    if args.config:
        contract_checks, candidate_geometry_contract = _candidate_geometry_contract(
            candidate_rows=candidate_rows,
            config_path=Path(args.config).expanduser().resolve(),
            geom_prefix=args.geom_prefix,
        )
        checks.extend(contract_checks)

    candidate_csv = out_dir / "physical_feature_inverse_geometry_candidates.csv"
    summary_path = out_dir / "physical_feature_inverse_prediction_summary.json"
    report_path = out_dir / "physical_feature_inverse_prediction_report.md"
    _write_csv(candidate_csv, candidate_rows)
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_CANDIDATE_GEOMETRIES_FOR_EMX_QUEUE" if status == "PASS" else "DO_NOT_USE_INVERSE_PREDICTIONS",
        "training_csv": str(training_csv),
        "training_source": _file_source(training_csv),
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "input_columns": input_columns,
        "input_feature_contract": _input_feature_contract(input_columns, args),
        "geometry_columns": geometry_columns,
        "training_count": training["count"],
        "target_count": len(targets),
        "candidate_count": len(candidate_rows),
        "candidate_geometry_contract": candidate_geometry_contract,
        "target_errors": target_errors,
        "diagnostics": diagnostics,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This is a KNN baseline inverse model, not a final neural inverse-design model.",
            "Output rows are candidate geometries only; they must pass geometry checks and EMX/HFSS/ADS validation.",
            "The model uses the physical-feature columns present in the training table; the project default is one explicit scalar q_center for Q.",
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
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target", action="append", default=[], help="Target feature as name=value, repeat for every input feature")
    parser.add_argument("--target-json", help="JSON dict or list of dicts with target physical features")
    parser.add_argument("--input-columns", help="Comma-separated input columns; defaults to input__* columns in the training table")
    parser.add_argument(
        "--config",
        help="Optional TransformerRunConfig YAML. When supplied, every predicted candidate must rebuild and validate against this config before use.",
    )
    parser.add_argument("--input-prefix", default="input__")
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--candidate-count", type=int, default=1)
    parser.add_argument("--k-neighbors", type=int, default=8)
    parser.add_argument("--distance-power", type=float, default=2.0)
    parser.add_argument("--include-nearest-neighbor-candidates", action=argparse.BooleanOptionalAction, default=True)
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


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_input_columns(rows: list[dict[str, str]], prefix: str, explicit: str | None) -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    if not rows:
        return []
    return sorted(key for key in rows[0] if key.startswith(prefix))


def _resolve_geometry_columns(rows: list[dict[str, str]], prefix: str) -> list[str]:
    if not rows:
        return []
    return sorted(key for key in rows[0] if key.startswith(prefix))


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
                for item in raw_targets:
                    targets.append(_coerce_target_dict(item, input_columns, input_prefix, errors))
        except Exception as exc:  # noqa: BLE001 - target parsing needs a direct report.
            errors.append(f"target json error: {type(exc).__name__}: {exc}")
    if args.target:
        raw: dict[str, Any] = {}
        for item in args.target:
            if "=" not in item:
                errors.append(f"target must be name=value, got {item!r}")
                continue
            key, value = item.split("=", 1)
            raw[key.strip()] = value.strip()
        targets.append(_coerce_target_dict(raw, input_columns, input_prefix, errors))
    targets = [target for target in targets if target]
    return targets, errors


def _coerce_target_dict(raw: dict[str, Any], input_columns: list[str], input_prefix: str, errors: list[str]) -> dict[str, float]:
    target = {}
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


def _training_matrix(rows: list[dict[str, str]], input_columns: list[str], geometry_columns: list[str]) -> dict[str, Any]:
    x = []
    y = []
    source = []
    for idx, row in enumerate(rows):
        inputs = [_as_float(row.get(column)) for column in input_columns]
        geoms = [_as_float(row.get(column)) for column in geometry_columns]
        if any(value is None for value in inputs) or any(value is None for value in geoms):
            continue
        x.append([float(value) for value in inputs if value is not None])
        y.append([float(value) for value in geoms if value is not None])
        source.append(idx)
    if not x:
        return {"count": 0, "x": np.empty((0, len(input_columns))), "y": np.empty((0, len(geometry_columns))), "source_indices": []}
    return {"count": len(x), "x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float), "source_indices": source}


def _predict_candidates(
    training: dict[str, Any],
    targets: list[dict[str, float]],
    input_columns: list[str],
    geometry_columns: list[str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    x = np.asarray(training["x"], dtype=float)
    y = np.asarray(training["y"], dtype=float)
    lows = np.min(x, axis=0)
    highs = np.max(x, axis=0)
    denom = np.maximum(highs - lows, 1e-12)
    x_norm = (x - lows) / denom
    k = max(1, min(int(args.k_neighbors), x.shape[0]))
    requested = max(1, int(args.candidate_count))
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for target_idx, target in enumerate(targets):
        target_vec = np.asarray([float(target[column]) for column in input_columns], dtype=float)
        target_norm = (target_vec - lows) / denom
        distances = np.linalg.norm(x_norm - target_norm[None, :], axis=1)
        order = np.argsort(distances)
        neighbors = [int(item) for item in order[:k]]
        pred, uncertainty, mean_distance = _predict_from_neighbors(y, distances, neighbors, float(args.distance_power))
        target_diag = {
            "target_index": target_idx,
            "neighbor_indices": [int(training["source_indices"][idx]) for idx in neighbors],
            "neighbor_distances": [float(distances[idx]) for idx in neighbors],
            "mean_neighbor_distance": mean_distance,
        }
        diagnostics.append(target_diag)
        rows.append(
            _candidate_row(
                target_idx=target_idx,
                candidate_rank=1,
                source="knn_idw_weighted_inverse_prediction",
                target=target,
                geometry=pred,
                uncertainty=uncertainty,
                mean_distance=mean_distance,
                geometry_columns=geometry_columns,
            )
        )
        if args.include_nearest_neighbor_candidates and requested > 1:
            for rank, neighbor_idx in enumerate(order[: requested - 1], start=2):
                rows.append(
                    _candidate_row(
                        target_idx=target_idx,
                        candidate_rank=rank,
                        source=f"nearest_training_geometry_row_{int(training['source_indices'][int(neighbor_idx)])}",
                        target=target,
                        geometry=y[int(neighbor_idx)],
                        uncertainty=np.zeros(len(geometry_columns), dtype=float),
                        mean_distance=float(distances[int(neighbor_idx)]),
                        geometry_columns=geometry_columns,
                    )
                )
    return rows, diagnostics


def _candidate_geometry_contract(
    *,
    candidate_rows: list[dict[str, Any]],
    config_path: Path,
    geom_prefix: str,
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
    }
    checks.append(_check("inverse_geometry_config_exists", config_path.is_file(), str(config_path)))
    if not config_path.is_file():
        return checks, contract
    try:
        cfg = load_run_config(config_path)
        adapter = TransformerOptimizationAdapter(cfg.bounds)
    except Exception as exc:  # noqa: BLE001 - exact config error is evidence.
        checks.append(_check("inverse_geometry_config_loads", False, f"{type(exc).__name__}: {exc}"))
        return checks, contract

    checks.append(_check("inverse_geometry_config_loads", True, str(config_path)))
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
        if geometry_errors:
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
            "inverse_geometry_candidate_fields_match_config",
            bool(candidate_rows) and not missing_field_rows,
            f"expected_columns={expected_columns}, missing_rows={missing_field_rows[:20]}",
        )
    )
    checks.append(
        _check(
            "inverse_geometry_candidates_rebuild_from_config",
            bool(candidate_rows) and not invalid_rows and valid_count == len(candidate_rows),
            f"valid={valid_count}, candidates={len(candidate_rows)}, invalid_rows={invalid_rows[:20]}",
        )
    )
    return checks, contract


def _predict_from_neighbors(
    y: np.ndarray,
    distances: np.ndarray,
    neighbors: list[int],
    power: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    neighbor_distances = np.asarray([float(distances[idx]) for idx in neighbors], dtype=float)
    neighbor_y = y[np.asarray(neighbors, dtype=int), :]
    if np.any(neighbor_distances < 1e-12):
        pred = neighbor_y[int(np.argmin(neighbor_distances))]
        uncertainty = np.zeros(y.shape[1], dtype=float)
    else:
        weights = 1.0 / np.maximum(neighbor_distances, 1e-12) ** float(power)
        weights = weights / np.sum(weights)
        pred = np.sum(neighbor_y * weights[:, None], axis=0)
        uncertainty = np.sqrt(np.sum(((neighbor_y - pred) ** 2) * weights[:, None], axis=0))
    return pred, uncertainty, float(np.mean(neighbor_distances))


def _candidate_row(
    *,
    target_idx: int,
    candidate_rank: int,
    source: str,
    target: dict[str, float],
    geometry: np.ndarray,
    uncertainty: np.ndarray,
    mean_distance: float,
    geometry_columns: list[str],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": f"inverse_target_{target_idx:03d}_candidate_{candidate_rank:03d}",
        "target_index": int(target_idx),
        "candidate_rank": int(candidate_rank),
        "inverse_prediction_source": source,
        "inverse_neighbor_mean_distance": float(mean_distance),
    }
    for key, value in target.items():
        row[f"target__{key}"] = float(value)
    for idx, column in enumerate(geometry_columns):
        row[column] = float(geometry[idx])
        row[f"inverse_uncertainty__{column}"] = float(uncertainty[idx])
    return row


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


def _feature_contract_checks(input_columns: list[str], args: argparse.Namespace) -> list[dict[str, Any]]:
    zin_columns = [column for column in input_columns if _is_zin_column(column)]
    tokens = _physical_feature_tokens(input_columns)
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
            f"required={required}, input_columns={input_columns}",
        ),
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
        "# Physical-Feature Inverse Geometry Prediction",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Training rows: `{summary['training_count']}`",
        f"Targets: `{summary['target_count']}`",
        f"Candidate rows: `{summary['candidate_count']}`",
        f"Candidate CSV: `{summary['candidate_csv']}`",
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
