#!/usr/bin/env python3
"""Merge accepted physical-feature rows and re-audit Lp/Ls/Q/|K| coverage.

This utility closes the adaptive-acquisition loop:

1. start with an existing accepted pool built from real EMX labels;
2. add rows from a completed adaptive EMX checkpoint training table;
3. normalize columns back to the ``dataset_rows.csv`` shape expected by the
   next sparse-bin planner;
4. filter to explicit Lp/Ls/Q/|K| ranges;
5. optionally run the uniformity audit on the merged pool.

It does not use surrogate predictions as labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
INDEPENDENT_GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)
CANONICAL_GEOMETRY_FIELDS = tuple(column.removeprefix("geom__") for column in INDEPENDENT_GEOMETRY_COLUMNS)
GEOMETRY_FINGERPRINT_SCHEMA = "mars56_grounded_s4p_geometry_v1"
GEOMETRY_FINGERPRINT_QUANTIZATION_UM = 1.0e-6


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, Any]] = []
    merged_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    reject_summary = {
        "duplicate": 0,
        "not_ok": 0,
        "missing_feature": 0,
        "outside_range": 0,
        "missing_geometry": 0,
        "geometry_identity_mismatch": 0,
    }

    for pool_dir_raw in args.base_pool_dir:
        pool_dir = Path(pool_dir_raw).expanduser().resolve()
        rows = _read_rows(pool_dir / "dataset_rows.csv")
        source = _source_record("base_pool", pool_dir / "dataset_rows.csv", len(rows))
        accepted, rejects = _normalize_dataset_rows(rows, source["label"], args)
        _merge_rows(accepted, merged_rows, seen_keys, reject_summary)
        _add_rejects(reject_summary, rejects)
        source["accepted_after_source_filter"] = len(accepted)
        source["reject_summary"] = rejects
        sources.append(source)

    for training_csv_raw in args.training_csv:
        training_csv = Path(training_csv_raw).expanduser().resolve()
        rows = _read_rows(training_csv)
        source = _source_record("training_csv", training_csv, len(rows))
        accepted, rejects = _normalize_training_rows(rows, source["label"], args)
        _merge_rows(accepted, merged_rows, seen_keys, reject_summary)
        _add_rejects(reject_summary, rejects)
        source["accepted_after_source_filter"] = len(accepted)
        source["reject_summary"] = rejects
        sources.append(source)

    dataset_csv = out_dir / "dataset_rows.csv"
    summary_path = out_dir / "accepted_pool_merge_summary.json"
    report_path = out_dir / "accepted_pool_merge_report.md"
    _write_csv(dataset_csv, merged_rows)

    uniformity_summary: dict[str, Any] = {"status": "NOT_RUN"}
    if args.run_uniformity:
        uniformity_summary = _run_uniformity(dataset_csv, out_dir / "physical_feature_uniformity", args)

    checks = [
        _check("merged_rows_present", len(merged_rows) > 0, f"rows={len(merged_rows)}"),
        _check("min_row_count", len(merged_rows) >= args.min_row_count, f"rows={len(merged_rows)} min={args.min_row_count}"),
        _check("has_geometry_columns", bool(_geometry_columns(merged_rows)), f"columns={len(_geometry_columns(merged_rows))}"),
        _check(
            "all_rows_have_canonical_geometry_identity",
            bool(merged_rows)
            and all(
                str(row.get("canonical_geometry_fingerprint_sha256") or "")
                and row.get("canonical_geometry_fingerprint_schema") == GEOMETRY_FINGERPRINT_SCHEMA
                and _as_float(row.get("canonical_geometry_fingerprint_quantization_um"))
                == GEOMETRY_FINGERPRINT_QUANTIZATION_UM
                for row in merged_rows
            ),
            f"rows={len(merged_rows)}",
        ),
        _check(
            "no_declared_geometry_identity_mismatch",
            int(reject_summary["geometry_identity_mismatch"]) == 0,
            f"mismatches={reject_summary['geometry_identity_mismatch']}",
        ),
    ]
    if args.run_uniformity:
        checks.append(
            _check(
                "uniformity_audit_ran",
                uniformity_summary.get("returncode") == 0 or args.no_fail_exit,
                f"returncode={uniformity_summary.get('returncode')}",
            )
        )
        if args.require_four_d_gate:
            checks.append(
                _check(
                    "uniformity_audit_passed_required_four_d_balance_gate",
                    uniformity_summary.get("overall_status") == "PASS",
                    (
                        f"status={uniformity_summary.get('overall_status')} "
                        f"occupied={uniformity_summary.get('four_d_occupied_fraction')} "
                        f"entropy={uniformity_summary.get('four_d_normalized_entropy')} "
                        f"imbalance={uniformity_summary.get('four_d_nonzero_bin_imbalance')}"
                    ),
                )
            )

    status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_NEXT_ACCEPTED_POOL_FOR_ADAPTIVE_PLANNING" if status == "PASS" else "DO_NOT_USE_ACCEPTED_POOL",
        "out_dir": str(out_dir),
        "dataset_rows_csv": str(dataset_csv),
        "row_count": len(merged_rows),
        "feature_columns_for_next_planner": list(DEFAULT_FEATURE_COLUMNS),
        "k_axis_policy": "Pool rows keep signed k_center when available and always include k_abs_center=abs(k_center) for acquisition.",
        "ranges": _range_config(args),
        "sources": sources,
        "reject_summary": reject_summary,
        "geometry_columns": _geometry_columns(merged_rows),
        "dedupe_policy": {
            "primary_key": "canonical 10-variable independent geometry vector",
            "geometry_columns": list(INDEPENDENT_GEOMETRY_COLUMNS),
            "canonical_fields": list(CANONICAL_GEOMETRY_FIELDS),
            "fingerprint_schema": GEOMETRY_FINGERPRINT_SCHEMA,
            "fingerprint_quantization_um": GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
            "fallback": "touchstone path, evaluation, sample ID, cache key, then full geometry-feature hash",
            "reason": "Repeated simulations of the same geometry are not independent training samples.",
        },
        "feature_summary": _feature_summary(merged_rows),
        "uniformity": uniformity_summary,
        "checks": checks,
        "limitations": [
            "Only real simulator-derived dataset_rows/training-table rows should be supplied.",
            "Surrogate candidate predictions are intentionally not accepted as labels.",
            "Uniformity PASS here proves this merged pool is suitable as an acquisition source, not that the final 1M campaign is complete.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"dataset_rows_csv={dataset_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if args.run_uniformity:
        print(f"uniformity_summary={uniformity_summary.get('summary')}")
        print(f"uniformity_status={uniformity_summary.get('overall_status')}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-pool-dir", action="append", default=[], help="Directory containing accepted-pool dataset_rows.csv")
    parser.add_argument("--training-csv", action="append", default=[], help="Completed EMX checkpoint physical_feature_inverse_training_table.csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-row-count", type=int, default=1)
    parser.add_argument("--lp-min-nh", type=float, default=0.5)
    parser.add_argument("--lp-max-nh", type=float, default=3.0)
    parser.add_argument("--ls-min-nh", type=float, default=0.5)
    parser.add_argument("--ls-max-nh", type=float, default=3.0)
    parser.add_argument("--q-min", type=float, default=5.0)
    parser.add_argument("--q-max", type=float, default=25.0)
    parser.add_argument("--k-min", type=float, default=0.0)
    parser.add_argument("--k-max", type=float, default=0.8)
    parser.add_argument("--uniformity-min-valid-count", type=int, default=1)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--pair-bins", type=int, default=10)
    parser.add_argument("--four-d-bins", type=int, default=4)
    parser.add_argument("--min-four-d-occupied-frac", type=float, default=0.50)
    parser.add_argument("--min-four-d-entropy-frac", type=float, default=0.80)
    parser.add_argument("--max-four-d-bin-imbalance", type=float, default=4.0)
    parser.add_argument("--run-uniformity", action="store_true")
    parser.add_argument("--require-four-d-gate", action="store_true")
    parser.add_argument("--require-plots", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not args.base_pool_dir and not args.training_csv:
        parser.error("at least one --base-pool-dir or --training-csv is required")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _normalize_dataset_rows(rows: list[dict[str, str]], source_label: str, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    rejects = {
        "not_ok": 0,
        "missing_feature": 0,
        "outside_range": 0,
        "missing_geometry": 0,
        "geometry_identity_mismatch": 0,
    }
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            rejects["not_ok"] += 1
            continue
        normalized = dict(row)
        normalized.setdefault("ok", "true")
        normalized.setdefault("evaluation", row.get("evaluation") or row.get("sample_id") or f"{source_label}_row_{idx}")
        if not _copy_physical_features(normalized):
            rejects["missing_feature"] += 1
            continue
        if not _inside_ranges(normalized, args):
            rejects["outside_range"] += 1
            continue
        if not _geometry_columns([normalized]):
            rejects["missing_geometry"] += 1
            continue
        if not _attach_canonical_geometry_identity(normalized):
            rejects["geometry_identity_mismatch"] += 1
            continue
        normalized["merge_source"] = source_label
        accepted.append(normalized)
    return accepted, rejects


def _normalize_training_rows(rows: list[dict[str, str]], source_label: str, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    rejects = {
        "not_ok": 0,
        "missing_feature": 0,
        "outside_range": 0,
        "missing_geometry": 0,
        "geometry_identity_mismatch": 0,
    }
    for idx, row in enumerate(rows):
        normalized = dict(row)
        normalized["ok"] = "true"
        normalized.setdefault("evaluation", row.get("evaluation") or f"{source_label}_row_{idx}")
        for feature in ("lp_nh_center", "ls_nh_center", "q_center", "k_center"):
            value = _first_float(row, (feature, f"input__{feature}", f"phys__{feature}"))
            if value is not None:
                normalized[feature] = value
        if not _copy_physical_features(normalized):
            rejects["missing_feature"] += 1
            continue
        if not _inside_ranges(normalized, args):
            rejects["outside_range"] += 1
            continue
        if not _geometry_columns([normalized]):
            rejects["missing_geometry"] += 1
            continue
        if not _attach_canonical_geometry_identity(normalized):
            rejects["geometry_identity_mismatch"] += 1
            continue
        normalized["merge_source"] = source_label
        accepted.append(normalized)
    return accepted, rejects


def _copy_physical_features(row: dict[str, Any]) -> bool:
    lp = _first_float(row, ("lp_nh_center", "input__lp_nh_center", "phys__lp_nh_center"))
    ls = _first_float(row, ("ls_nh_center", "input__ls_nh_center", "phys__ls_nh_center"))
    q = _first_float(row, ("q_center", "input__q_center", "phys__q_center", "q", "q_min"))
    k_signed = _first_float(row, ("k_center", "input__k_center", "phys__k_center", "k", "kw_center", "kw"))
    k_abs = _first_float(row, ("k_abs_center", "input__k_abs_center", "phys__k_abs_center", "abs_k", "k_abs"))
    if q is None:
        qp = _first_float(row, ("qp_center", "input__qp_center", "phys__qp_center", "qp"))
        qs = _first_float(row, ("qs_center", "input__qs_center", "phys__qs_center", "qs"))
        if qp is not None and qs is not None:
            q = min(qp, qs)
    if k_abs is None and k_signed is not None:
        k_abs = abs(k_signed)
    if k_signed is None and k_abs is not None:
        k_signed = k_abs
    if any(value is None for value in (lp, ls, q, k_signed, k_abs)):
        return False
    row["lp_nh_center"] = float(lp)
    row["ls_nh_center"] = float(ls)
    row["q_center"] = float(q)
    row["k_center"] = float(k_signed)
    row["k_abs_center"] = float(k_abs)
    return True


def _inside_ranges(row: dict[str, Any], args: argparse.Namespace) -> bool:
    lp = _as_float(row.get("lp_nh_center"))
    ls = _as_float(row.get("ls_nh_center"))
    q = _as_float(row.get("q_center"))
    k_abs = _as_float(row.get("k_abs_center"))
    if any(value is None or not math.isfinite(float(value)) for value in (lp, ls, q, k_abs)):
        return False
    return (
        args.lp_min_nh <= float(lp) <= args.lp_max_nh
        and args.ls_min_nh <= float(ls) <= args.ls_max_nh
        and args.q_min <= float(q) <= args.q_max
        and args.k_min <= float(k_abs) <= args.k_max
    )


def _merge_rows(rows: list[dict[str, Any]], merged: list[dict[str, Any]], seen: set[str], rejects: dict[str, int]) -> None:
    for row in rows:
        key = _dedupe_key(row)
        if key in seen:
            rejects["duplicate"] += 1
            continue
        seen.add(key)
        merged.append(row)


def _dedupe_key(row: dict[str, Any]) -> str:
    fingerprint = _canonical_geometry_fingerprint(row)
    if fingerprint is not None:
        return "geometry:" + fingerprint
    for field in ("touchstone_path", "raw_touchstone_path", "evaluation", "sample_id", "cache_key"):
        value = str(row.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    geom = "|".join(f"{key}={row.get(key)}" for key in sorted(row) if key.startswith("geom__"))
    features = "|".join(f"{key}={row.get(key)}" for key in DEFAULT_FEATURE_COLUMNS)
    return "hash:" + hashlib.sha256((geom + "|" + features).encode("utf-8")).hexdigest()


def _attach_canonical_geometry_identity(row: dict[str, Any]) -> bool:
    fingerprint = _canonical_geometry_fingerprint(row)
    if fingerprint is None or not _shared_width_aliases_valid(row):
        return False
    for field in ("queue__geometry_fingerprint_sha256", "geometry_fingerprint_sha256"):
        declared = str(row.get(field) or "").strip()
        if declared and declared != fingerprint:
            return False
    for field in ("queue__geometry_fingerprint_schema", "geometry_fingerprint_schema"):
        declared = str(row.get(field) or "").strip()
        if declared and declared != GEOMETRY_FINGERPRINT_SCHEMA:
            return False
    for field in (
        "queue__geometry_fingerprint_quantization_um",
        "geometry_fingerprint_quantization_um",
    ):
        declared = _as_float(row.get(field))
        if declared is not None and not math.isclose(
            declared,
            GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            return False
    row["canonical_geometry_fingerprint_sha256"] = fingerprint
    row["canonical_geometry_fingerprint_schema"] = GEOMETRY_FINGERPRINT_SCHEMA
    row["canonical_geometry_fingerprint_quantization_um"] = GEOMETRY_FINGERPRINT_QUANTIZATION_UM
    return True


def _canonical_geometry_fingerprint(row: dict[str, Any]) -> str | None:
    quantum = Decimal(str(GEOMETRY_FINGERPRINT_QUANTIZATION_UM))
    quantized = []
    for column in INDEPENDENT_GEOMETRY_COLUMNS:
        value = _as_float(row.get(column))
        if value is None or not math.isfinite(value):
            return None
        integer = (Decimal(str(value)) / quantum).to_integral_value(rounding=ROUND_HALF_UP)
        quantized.append(int(integer))
    payload = {
        "schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "quantization_um": format(quantum, "f"),
        "fields": list(CANONICAL_GEOMETRY_FIELDS),
        "quantized_values": quantized,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _shared_width_aliases_valid(row: dict[str, Any]) -> bool:
    line_width = _as_float(row.get("geom__line_width_um"))
    if line_width is None:
        return False
    for alias in ("geom__primary_width_um", "geom__secondary_width_um"):
        value = _as_float(row.get(alias))
        if value is not None and not math.isclose(value, line_width, rel_tol=0.0, abs_tol=1.0e-12):
            return False
    return True


def _add_rejects(total: dict[str, int], item: dict[str, int]) -> None:
    for key, value in item.items():
        total[key] = int(total.get(key, 0)) + int(value)


def _geometry_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    return sorted(key for key in rows[0] if key.startswith("geom__"))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for preferred in (
        "ok",
        "evaluation",
        "touchstone_path",
        "raw_touchstone_path",
        "merge_source",
        "lp_nh_center",
        "ls_nh_center",
        "q_center",
        "k_center",
        "k_abs_center",
    ):
        if any(preferred in row for row in rows):
            fieldnames.append(preferred)
    remaining = sorted({key for row in rows for key in row}.difference(fieldnames))
    fieldnames.extend(remaining)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run_uniformity(dataset_csv: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    script = Path(__file__).resolve().with_name("audit_physical_feature_uniformity.py")
    cmd = [
        args.python,
        str(script),
        "--training-csv",
        str(dataset_csv),
        "--out-dir",
        str(out_dir),
        "--min-valid-count",
        str(args.uniformity_min_valid_count),
        "--bins",
        str(args.bins),
        "--pair-bins",
        str(args.pair_bins),
        "--four-d-bins",
        str(args.four_d_bins),
        "--min-four-d-occupied-frac",
        str(args.min_four_d_occupied_frac),
        "--min-four-d-entropy-frac",
        str(args.min_four_d_entropy_frac),
        "--max-four-d-bin-imbalance",
        str(args.max_four_d_bin_imbalance),
        "--k-mode",
        "magnitude",
        "--lp-min-nh",
        str(args.lp_min_nh),
        "--lp-max-nh",
        str(args.lp_max_nh),
        "--ls-min-nh",
        str(args.ls_min_nh),
        "--ls-max-nh",
        str(args.ls_max_nh),
        "--q-min",
        str(args.q_min),
        "--q-max",
        str(args.q_max),
        "--k-min",
        str(args.k_min),
        "--k-max",
        str(args.k_max),
        "--require-explicit-ranges",
        "--no-fail-exit",
    ]
    if args.require_four_d_gate:
        cmd.append("--require-four-d-gate")
    if args.require_plots:
        cmd.append("--require-plots")
    completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    summary_path = out_dir / "physical_feature_uniformity_summary.json"
    status = "MISSING"
    valid_count = None
    four_d_occupied = None
    four_d_entropy = None
    four_d_imbalance = None
    if summary_path.is_file():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        status = str(data.get("overall_status", "MISSING"))
        valid_count = data.get("valid_feature_count")
        four_d = data.get("four_dimensional_uniformity") or {}
        four_d_occupied = four_d.get("occupied_fraction")
        four_d_entropy = four_d.get("normalized_entropy")
        four_d_imbalance = four_d.get("max_to_min_nonzero_ratio")
    return {
        "returncode": completed.returncode,
        "overall_status": status,
        "valid_feature_count": valid_count,
        "four_d_occupied_fraction": four_d_occupied,
        "four_d_normalized_entropy": four_d_entropy,
        "four_d_nonzero_bin_imbalance": four_d_imbalance,
        "summary": str(summary_path),
        "stdout_tail": "\n".join((completed.stdout or "").splitlines()[-20:]),
        "stderr_tail": "\n".join((completed.stderr or "").splitlines()[-20:]),
    }


def _source_record(kind: str, path: Path, row_count: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": f"{kind}:{path.parent.name}",
        "path": str(path),
        "exists": path.is_file(),
        "row_count": row_count,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feature_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    summary: dict[str, dict[str, float | None]] = {}
    for column in ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"):
        values = [float(v) for row in rows if (v := _as_float(row.get(column))) is not None]
        if not values:
            summary[column] = {"min": None, "max": None, "mean": None}
            continue
        summary[column] = {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
    return summary


def _range_config(args: argparse.Namespace) -> dict[str, list[float]]:
    return {
        "lp_nh_center": [args.lp_min_nh, args.lp_max_nh],
        "ls_nh_center": [args.ls_min_nh, args.ls_max_nh],
        "q_center": [args.q_min, args.q_max],
        "k_abs_center": [args.k_min, args.k_max],
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Accepted Physical-Feature Pool Merge",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Rows: `{summary['row_count']}`",
        f"- Dataset CSV: `{summary['dataset_rows_csv']}`",
        f"- Uniformity status: `{summary['uniformity'].get('overall_status')}`",
        f"- 4D occupied fraction: `{summary['uniformity'].get('four_d_occupied_fraction')}`",
        f"- 4D normalized entropy: `{summary['uniformity'].get('four_d_normalized_entropy')}`",
        f"- 4D nonzero-bin max/min ratio: `{summary['uniformity'].get('four_d_nonzero_bin_imbalance')}`",
        "",
        "## Sources",
    ]
    for source in summary["sources"]:
        lines.append(
            f"- `{source['kind']}` rows={source['row_count']} accepted={source.get('accepted_after_source_filter')} path={source['path']}"
        )
    lines.extend(["", "## Boundary", "This merge uses simulator-derived rows only; it is not final 1M completion evidence."])
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "fail", "failed"}


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


if __name__ == "__main__":
    raise SystemExit(main())
