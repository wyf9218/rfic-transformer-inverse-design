#!/usr/bin/env python3
"""Attach EMX layout geometry metadata to extracted response features.

`extract_touchstone_response_features.py` can safely operate on a stable
Touchstone symlink index, but that index intentionally contains only S-parameter
files.  The inverse-design checkpoint also needs geometry targets.  This helper
uses each row's `touchstone_path` to walk back to the original evaluation folder
and read the real `layout/geometry.json` and
`layout/power_line_8port_geometry.json` files.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STANDARD_GEOMETRY_COLUMNS = [
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__primary_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__secondary_width_um",
    "geom__secondary_terminal_y_span_um",
    "geom__secondary_feed_extension_um",
    "geom__offset_um",
]

POWER_LINE_GEOMETRY_COLUMNS = [
    "geom__line_width_um",
    "geom__power_vertical_length_um",
    "geom__power_bridge_width_um",
    "geom__shield_opening_clearance_um",
    "geom__primary_power_line_clearance_um",
    "geom__secondary_power_line_clearance_um",
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    features_csv = Path(args.features_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(features_csv)
    enriched, rejects = _enrich_rows(rows, args)
    dataset_csv = out_dir / "dataset_rows.csv"
    manifest_path = out_dir / "geometry_enrichment_manifest.json"
    report_path = out_dir / "geometry_enrichment_report.md"
    _write_csv(dataset_csv, enriched)

    checks = [
        _check("features_csv_exists", features_csv.is_file(), str(features_csv)),
        _check("feature_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("enriched_rows_present", bool(enriched), f"rows={len(enriched)}"),
        _check("all_feature_rows_enriched", len(enriched) == len(rows), f"enriched={len(enriched)}, input={len(rows)}"),
        _check("q_center_available", all(_as_float(row.get("q_center")) is not None for row in enriched), "q_center"),
        _check("standard_geometry_available", all(_has_all(row, STANDARD_GEOMETRY_COLUMNS) for row in enriched), ",".join(STANDARD_GEOMETRY_COLUMNS)),
    ]
    if args.require_power_line_geometry:
        checks.append(
            _check(
                "power_line_geometry_available",
                all(_has_all(row, POWER_LINE_GEOMETRY_COLUMNS) for row in enriched),
                ",".join(POWER_LINE_GEOMETRY_COLUMNS),
            )
        )
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_FOR_INVERSE_TRAINING_TABLE" if status == "PASS" else "DO_NOT_USE_GEOMETRY_ENRICHED_ROWS",
        "features_csv": str(features_csv),
        "out_dir": str(out_dir),
        "dataset_rows_csv": str(dataset_csv),
        "input_row_count": len(rows),
        "enriched_row_count": len(enriched),
        "reject_summary": rejects,
        "standard_geometry_columns": STANDARD_GEOMETRY_COLUMNS,
        "power_line_geometry_columns": POWER_LINE_GEOMETRY_COLUMNS,
        "q_definition": args.q_definition,
        "checks": checks,
        "limitations": [
            "This script only joins existing extracted labels with existing layout JSON metadata.",
            "It does not run EMX, Cadence, ADS, or HFSS.",
            "For model training, pass this output directory to build_physical_feature_inverse_training_table.py.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(manifest), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"dataset_rows_csv={dataset_csv}")
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--q-definition", choices=["min", "mean", "primary", "secondary"], default="min")
    parser.add_argument("--require-power-line-geometry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _enrich_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    rejects = {
        "missing_touchstone_path": 0,
        "touchstone_path_not_evaluation": 0,
        "missing_geometry_json": 0,
        "missing_power_line_json": 0,
        "invalid_json": 0,
        "missing_required_geometry": 0,
    }
    for idx, row in enumerate(rows):
        touchstone_raw = (row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if not touchstone_raw:
            rejects["missing_touchstone_path"] += 1
            continue
        touchstone_path = Path(touchstone_raw).expanduser()
        eval_dir = _evaluation_dir_from_touchstone(touchstone_path)
        if eval_dir is None:
            rejects["touchstone_path_not_evaluation"] += 1
            continue
        geometry_path = eval_dir / "layout" / "geometry.json"
        power_path = eval_dir / "layout" / "power_line_8port_geometry.json"
        if not geometry_path.is_file():
            rejects["missing_geometry_json"] += 1
            continue
        if args.require_power_line_geometry and not power_path.is_file():
            rejects["missing_power_line_json"] += 1
            continue
        try:
            geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
            power = json.loads(power_path.read_text(encoding="utf-8")) if power_path.is_file() else {}
        except json.JSONDecodeError:
            rejects["invalid_json"] += 1
            continue
        record: dict[str, Any] = dict(row)
        record["source_evaluation_dir"] = str(eval_dir)
        record["geometry_json_path"] = str(geometry_path)
        record["power_line_8port_geometry_json_path"] = str(power_path) if power_path.is_file() else ""
        record["q_center"] = _scalar_q(row, args.q_definition)
        record.update(_flatten_standard_geometry(geometry))
        record.update(_flatten_power_geometry(power))
        if not _has_all(record, STANDARD_GEOMETRY_COLUMNS):
            rejects["missing_required_geometry"] += 1
            continue
        if args.require_power_line_geometry and not _has_all(record, POWER_LINE_GEOMETRY_COLUMNS):
            rejects["missing_required_geometry"] += 1
            continue
        record.setdefault("row_index", idx)
        out.append(record)
    return out, rejects


def _evaluation_dir_from_touchstone(path: Path) -> Path | None:
    # Expected original path: .../evaluations/<id>/emx/emx.s4p
    parts = path.parts
    if "evaluations" not in parts:
        return None
    try:
        eval_pos = len(parts) - 1 - list(reversed(parts)).index("evaluations")
    except ValueError:
        return None
    if eval_pos + 1 >= len(parts):
        return None
    return Path(*parts[: eval_pos + 2])


def _flatten_standard_geometry(data: dict[str, Any]) -> dict[str, Any]:
    primary = _as_dict(_as_dict(data.get("primary")).get("geometry"))
    secondary = _as_dict(_as_dict(data.get("secondary")).get("geometry"))
    return {
        "geom__primary_outer_width_um": primary.get("outer_width_um"),
        "geom__primary_outer_height_um": primary.get("outer_height_um"),
        "geom__primary_width_um": primary.get("trace_width_um"),
        "geom__primary_terminal_y_span_um": primary.get("terminal_y_span_um"),
        "geom__primary_feed_extension_um": primary.get("feed_extension_um"),
        "geom__secondary_outer_width_um": secondary.get("outer_width_um"),
        "geom__secondary_outer_height_um": secondary.get("outer_height_um"),
        "geom__secondary_width_um": secondary.get("trace_width_um"),
        "geom__secondary_terminal_y_span_um": secondary.get("terminal_y_span_um"),
        "geom__secondary_feed_extension_um": secondary.get("feed_extension_um"),
        "geom__offset_um": data.get("offset_um"),
    }


def _flatten_power_geometry(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "geom__line_width_um": data.get("line_width_um"),
        "geom__power_vertical_length_um": data.get("vertical_length_um"),
        "geom__power_bridge_width_um": data.get("bridge_width_um"),
        "geom__shield_opening_clearance_um": data.get("shield_opening_clearance_um"),
        "geom__primary_power_line_clearance_um": _clearance_value(data.get("primary_power_line_clearance")),
        "geom__secondary_power_line_clearance_um": _clearance_value(data.get("secondary_power_line_clearance")),
    }


def _clearance_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in (
            "combined_coil_boundary_clearance_um",
            "other_coil_boundary_clearance_um",
            "own_coil_boundary_clearance_um",
        ):
            parsed = _as_float(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _as_float(value)


def _scalar_q(row: dict[str, Any], definition: str) -> float | None:
    existing = _as_float(row.get("q_center"))
    if existing is not None:
        return existing
    qp = _as_float(row.get("qp_center"))
    qs = _as_float(row.get("qs_center"))
    if definition == "primary":
        return qp
    if definition == "secondary":
        return qs
    if qp is None or qs is None:
        return None
    if definition == "mean":
        return 0.5 * (qp + qs)
    return min(qp, qs)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _has_all(row: dict[str, Any], columns: list[str]) -> bool:
    return all(_as_float(row.get(column)) is not None for column in columns)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                keys.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Response Features Geometry Enrichment",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Input rows: `{summary['input_row_count']}`",
        f"- Enriched rows: `{summary['enriched_row_count']}`",
        f"- Q definition: `{summary['q_definition']}`",
        "",
        "## Checks",
        "",
    ]
    for check in summary["checks"]:
        lines.append(f"- {'PASS' if check['pass'] else 'FAIL'}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Reject Summary", ""])
    for key, value in summary["reject_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
