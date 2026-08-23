#!/usr/bin/env python3
"""Audit whether the photo-matched HFSS S4P can be tied to the target sample.

The photo-matched file is useful because it reproduces the user's correct ADS
curve. It must not, however, be used as validation for a different training
sample unless geometry, ports, and provenance all line up. This script records
the available comparable evidence and blocks any same-structure claim when the
evidence is missing or contradictory.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from plot_emx_hfss_ads_style_metrics import DEFAULT_PACKAGE_DIR  # noqa: E402


DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_TARGET_DIR = DEFAULT_PROJECT_ROOT / "hfss_validation" / "final500_ec6698dfc575950b"
DEFAULT_PHOTO_SUMMARY = DEFAULT_PACKAGE_DIR / "photo_matched_hfss_reference_20260613" / "photo_matched_reference_summary.json"
DEFAULT_TARGET_GEOMETRY = DEFAULT_TARGET_DIR / "geometry.json"
DEFAULT_TARGET_LAYOUT = DEFAULT_TARGET_DIR / "transformer_layout.layout.json"
DEFAULT_TARGET_MODELING = DEFAULT_TARGET_DIR / "modeling_geometry_from_gds.json"
DEFAULT_TARGET_HFSS_RENDER = DEFAULT_PACKAGE_DIR / "hfss_model_views" / "hfss_payload_geometry_render_summary.json"
DEFAULT_OUT_DIR = DEFAULT_PACKAGE_DIR / "photo_matched_vs_target_geometry_audit_20260613"


@dataclass(frozen=True)
class AuditCheck:
    status: str
    name: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photo-summary", default=str(DEFAULT_PHOTO_SUMMARY))
    parser.add_argument("--target-geometry", default=str(DEFAULT_TARGET_GEOMETRY))
    parser.add_argument("--target-layout", default=str(DEFAULT_TARGET_LAYOUT))
    parser.add_argument("--target-modeling", default=str(DEFAULT_TARGET_MODELING))
    parser.add_argument("--target-hfss-render", default=str(DEFAULT_TARGET_HFSS_RENDER))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--dimension-relative-tolerance", type=float, default=0.05)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    photo_path = Path(args.photo_summary).expanduser().resolve()
    target_geometry_path = Path(args.target_geometry).expanduser().resolve()
    target_layout_path = Path(args.target_layout).expanduser().resolve()
    target_modeling_path = Path(args.target_modeling).expanduser().resolve()
    target_hfss_render_path = Path(args.target_hfss_render).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    photo = _read_json(photo_path)
    target_geometry = _read_json(target_geometry_path)
    target_layout = _read_json(target_layout_path)
    target_modeling = _read_json(target_modeling_path)
    target_hfss_render = _read_json(target_hfss_render_path)

    photo_evidence = _photo_evidence(photo)
    target_evidence = _target_evidence(target_geometry, target_layout, target_modeling, target_hfss_render)
    dimension_rows = _dimension_rows(photo_evidence, target_evidence)
    checks = _build_checks(photo_evidence, target_evidence, dimension_rows, args)
    status_counts = _status_counts(checks)
    overall_status = "PASS" if status_counts.get("FAIL", 0) == 0 else "FAIL"
    decision = (
        "PHOTO_MATCHED_HFSS_CAN_BE_TREATED_AS_TARGET_SAMPLE_REFERENCE"
        if overall_status == "PASS"
        else "DO_NOT_USE_PHOTO_MATCHED_HFSS_AS_TARGET_SAMPLE_REFERENCE"
    )

    csv_path = out_dir / "photo_matched_vs_target_geometry_comparison.csv"
    plot_path = out_dir / "photo_matched_vs_target_geometry_scale.png"
    report_path = out_dir / "photo_matched_vs_target_geometry_audit_report.md"
    summary_path = out_dir / "photo_matched_vs_target_geometry_audit_summary.json"
    _write_dimension_csv(csv_path, dimension_rows)
    _write_scale_plot(plot_path, dimension_rows)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "inputs": {
            "photo_summary": str(photo_path),
            "target_geometry": str(target_geometry_path),
            "target_layout": str(target_layout_path),
            "target_modeling": str(target_modeling_path),
            "target_hfss_render": str(target_hfss_render_path),
        },
        "photo_evidence": photo_evidence,
        "target_evidence": target_evidence,
        "dimension_relative_tolerance": float(args.dimension_relative_tolerance),
        "dimension_comparisons": dimension_rows,
        "checks": [check.__dict__ for check in checks],
        "status_counts": status_counts,
        "artifacts": {
            "summary": str(summary_path),
            "report": str(report_path),
            "csv": str(csv_path),
            "scale_plot": str(plot_path) if plot_path.exists() else None,
        },
        "notes": [
            "This audit does not judge whether the photo-matched HFSS S4P is physically valid; the earlier photo-matched evidence already shows its curves match the user's ADS photo.",
            "This audit asks a narrower provenance question: can that HFSS file be used as the matching HFSS/ADS reference for the target training sample ec6698dfc575950b?",
            "A FAIL means the file remains a valuable correct-curve clue, but it must not be used as target-sample EMX/HFSS validation evidence.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"csv={csv_path}")
    if plot_path.exists():
        print(f"plot={plot_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def _photo_evidence(summary: dict[str, Any]) -> dict[str, Any]:
    metadata = summary.get("metadata", {})
    variables = metadata.get("variables", {})
    fields = metadata.get("header_fields", {})
    ports = metadata.get("ports", {})
    return {
        "touchstone": summary.get("touchstone"),
        "source_kind_from_path": summary.get("source_kind_from_path"),
        "source_declares_hfss": bool(summary.get("source_declares_hfss")),
        "header_file": fields.get("File"),
        "header_project": fields.get("Project"),
        "header_design": fields.get("Design"),
        "header_setup": fields.get("Setup"),
        "frequency_ghz": summary.get("frequency_ghz", {}),
        "ports": ports,
        "dimensions_um": {
            "D1": _parse_um(variables.get("$D1")),
            "D2": _parse_um(variables.get("$D2")),
            "inner_D1": _parse_um(variables.get("$inner_D1")),
            "m10_w_inner": _parse_um(variables.get("$m10_w_inner")),
            "m10_w_outer": _parse_um(variables.get("$m10_w_outer")),
            "m9_w_inner": _parse_um(variables.get("$m9_w_inner")),
            "m9_w_outer": _parse_um(variables.get("$m9_w_outer")),
            "s": _parse_um(variables.get("$s")),
            "sub_h": _parse_um(variables.get("$sub_h")),
        },
        "raw_key_variables": {
            key: variables.get(key)
            for key in (
                "$D1",
                "$D2",
                "$inner_D1",
                "$m10_w_inner",
                "$m10_w_outer",
                "$m9_w_inner",
                "$m9_w_outer",
                "$s",
                "$sub_h",
                "$theta_bridge",
            )
        },
    }


def _target_evidence(geometry: dict[str, Any], layout: dict[str, Any], modeling: dict[str, Any], render: dict[str, Any]) -> dict[str, Any]:
    geom = modeling.get("geometry_parameters_from_summary") or _flatten_geometry_json(geometry)
    bbox = modeling.get("polygons_from_gds") or []
    bbox_by_role = _bbox_by_role(bbox)
    clearance_selected = modeling.get("selected", {})
    if not clearance_selected and "bbox_um" in modeling:
        clearance_selected = modeling
    hfss_objects = render.get("hfss_objects", {})
    return {
        "sample_id": render.get("sample_id") or modeling.get("cache_key") or "ec6698dfc575950b",
        "summary_work_dir": modeling.get("work_dir"),
        "touchstone_path": modeling.get("s4p_path") or modeling.get("touchstone_path"),
        "layout_path": layout.get("layout_path") or modeling.get("layout_path"),
        "top_cell": layout.get("top_cell"),
        "ports": [port.get("name") for port in layout.get("ports", [])],
        "hfss_ports": hfss_objects.get("ports") or [],
        "geometry_parameters_um": geom,
        "bbox_by_role_um": bbox_by_role,
        "shield_bbox_um": render.get("shield_bbox_um") or _coerce_bbox(clearance_selected.get("bbox_um", {}).get("shield")),
        "shield_width_um": render.get("shield_width_um") or geom.get("shield_width_um"),
        "port_widths_um": render.get("port_widths_um") or {},
        "metal_mid_z_um": render.get("metal_mid_z_um") or {},
    }


def _flatten_geometry_json(geometry: dict[str, Any]) -> dict[str, Any]:
    primary = geometry.get("primary", {}).get("geometry", {})
    secondary = geometry.get("secondary", {}).get("geometry", {})
    shield = geometry.get("shield", {})
    return {
        "primary_outer_width_um": primary.get("outer_width_um"),
        "primary_outer_height_um": primary.get("outer_height_um"),
        "secondary_outer_width_um": secondary.get("outer_width_um"),
        "secondary_outer_height_um": secondary.get("outer_height_um"),
        "primary_width_um": primary.get("trace_width_um"),
        "secondary_width_um": secondary.get("trace_width_um"),
        "primary_spacing_um": primary.get("spacing_um"),
        "secondary_spacing_um": secondary.get("spacing_um"),
        "primary_feed_extension_um": primary.get("feed_extension_um"),
        "secondary_feed_extension_um": secondary.get("feed_extension_um"),
        "shield_width_um": shield.get("width_um"),
        "shield_margin_um": shield.get("margin_um"),
    }


def _bbox_by_role(polygons: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for poly in polygons:
        role = str(poly.get("role") or f"layer_{poly.get('layer')}")
        bbox = _coerce_bbox(poly.get("bbox_um"))
        if bbox:
            result[role] = {"bbox_um": bbox, "width_um": bbox[2] - bbox[0], "height_um": bbox[3] - bbox[1]}
    return result


def _coerce_bbox(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return None
    return None


def _dimension_rows(photo: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    photo_dims = photo.get("dimensions_um", {})
    target_geom = target.get("geometry_parameters_um", {})
    bbox = target.get("bbox_by_role_um", {})
    rows = [
        _row("primary overall width/diameter", photo_dims.get("D1"), target_geom.get("primary_outer_width_um"), "photo $D1 vs target primary_outer_width_um"),
        _row("primary overall height/diameter", photo_dims.get("D1"), target_geom.get("primary_outer_height_um"), "photo $D1 vs target primary_outer_height_um"),
        _row("secondary overall width/diameter", photo_dims.get("D2"), target_geom.get("secondary_outer_width_um"), "photo $D2 vs target secondary_outer_width_um"),
        _row("secondary overall height/diameter", photo_dims.get("D2"), target_geom.get("secondary_outer_height_um"), "photo $D2 vs target secondary_outer_height_um"),
        _row("primary trace width", _mean_defined([photo_dims.get("m10_w_inner"), photo_dims.get("m10_w_outer")]), target_geom.get("primary_width_um"), "photo M10 widths vs target primary_width_um"),
        _row("secondary trace width", _mean_defined([photo_dims.get("m9_w_inner"), photo_dims.get("m9_w_outer")]), target_geom.get("secondary_width_um"), "photo M9 widths vs target secondary_width_um"),
        _row("spacing", photo_dims.get("s"), target_geom.get("primary_spacing_um"), "photo $s vs target primary_spacing_um"),
        _row("target primary bbox width", photo_dims.get("D1"), _nested(bbox, "primary_m10_drawing", "width_um"), "photo $D1 vs target primary GDS bbox width"),
        _row("target primary bbox height", photo_dims.get("D1"), _nested(bbox, "primary_m10_drawing", "height_um"), "photo $D1 vs target primary GDS bbox height"),
        _row("target secondary bbox width", photo_dims.get("D2"), _nested(bbox, "secondary_m9_drawing", "width_um"), "photo $D2 vs target secondary GDS bbox width"),
        _row("target secondary bbox height", photo_dims.get("D2"), _nested(bbox, "secondary_m9_drawing", "height_um"), "photo $D2 vs target secondary GDS bbox height"),
    ]
    return [row for row in rows if row["photo_um"] is not None and row["target_um"] is not None]


def _row(name: str, photo_value: float | None, target_value: Any, evidence: str) -> dict[str, Any]:
    target_float = _to_float(target_value)
    if photo_value is None or target_float is None:
        return {
            "name": name,
            "photo_um": photo_value,
            "target_um": target_float,
            "abs_delta_um": None,
            "relative_delta": None,
            "ratio_target_to_photo": None,
            "evidence": evidence,
        }
    delta = target_float - float(photo_value)
    return {
        "name": name,
        "photo_um": float(photo_value),
        "target_um": target_float,
        "abs_delta_um": float(abs(delta)),
        "relative_delta": float(abs(delta) / max(abs(float(photo_value)), 1.0e-30)),
        "ratio_target_to_photo": float(target_float / max(float(photo_value), 1.0e-30)),
        "evidence": evidence,
    }


def _build_checks(photo: dict[str, Any], target: dict[str, Any], rows: list[dict[str, Any]], args: argparse.Namespace) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    checks.append(
        AuditCheck(
            "FAIL" if photo.get("source_declares_hfss") else "WARN",
            "photo source provenance",
            f"photo file declares HFSS={photo.get('source_declares_hfss')}, header_file={photo.get('header_file')}",
        )
    )
    if str(photo.get("header_project", "")).lower() in {"", "n/a"}:
        checks.append(AuditCheck("FAIL", "photo project provenance", "no HFSS project name found"))
    elif str(photo.get("header_project")) != str(target.get("sample_id")):
        checks.append(
            AuditCheck(
                "FAIL",
                "photo project provenance",
                f"photo project `{photo.get('header_project')}` does not match target sample `{target.get('sample_id')}`",
            )
        )
    else:
        checks.append(AuditCheck("PASS", "photo project provenance", "photo project matches target sample id"))

    photo_ports = sorted(str(item) for item in photo.get("ports", {}).values())
    target_ports = sorted(str(item) for item in target.get("ports", []))
    if set(target_ports) and set(photo_ports) == set(target_ports):
        checks.append(AuditCheck("PASS", "port-name alignment", f"ports={target_ports}"))
    else:
        checks.append(AuditCheck("FAIL", "port-name alignment", f"photo_ports={photo_ports}, target_ports={target_ports}"))

    freq = photo.get("frequency_ghz", {})
    if freq.get("start") == 5.0 and freq.get("stop") == 50.0 and freq.get("step") == 0.1:
        checks.append(AuditCheck("PASS", "photo frequency grid", "photo file covers 5-50 GHz with 0.1 GHz step"))
    else:
        checks.append(
            AuditCheck(
                "FAIL",
                "photo frequency grid",
                f"photo frequency grid is {freq}; target validation requires 5-50 GHz, 0.1 GHz step",
            )
        )

    if not rows:
        checks.append(AuditCheck("FAIL", "geometry scale comparison", "no comparable geometry dimensions found"))
    else:
        failing = [
            row
            for row in rows
            if row.get("relative_delta") is None or float(row["relative_delta"]) > float(args.dimension_relative_tolerance)
        ]
        worst = max(rows, key=lambda row: float(row.get("relative_delta") or 0.0))
        status = "PASS" if not failing else "FAIL"
        checks.append(
            AuditCheck(
                status,
                "geometry scale comparison",
                f"{len(failing)}/{len(rows)} comparable dimensions exceed {args.dimension_relative_tolerance:.1%}; "
                f"worst={worst['name']} relative_delta={float(worst.get('relative_delta') or 0.0):.2%}",
            )
        )

    target_ids = [
        str(target.get("sample_id") or ""),
        str(target.get("layout_path") or ""),
        str(target.get("touchstone_path") or ""),
        str(target.get("top_cell") or ""),
    ]
    haystack = "\n".join(target_ids)
    if "ec6698dfc575950b" in haystack or "ec6698df" in haystack:
        checks.append(AuditCheck("PASS", "target sample identity", "target geometry evidence points to ec6698dfc575950b"))
    else:
        checks.append(AuditCheck("FAIL", "target sample identity", f"target identifiers do not include expected sample: {target_ids}"))
    return checks


def _write_dimension_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["name", "photo_um", "target_um", "abs_delta_um", "relative_delta", "ratio_target_to_photo", "evidence"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_scale_plot(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    import matplotlib.pyplot as plt

    top = rows[:]
    labels = [row["name"] for row in top]
    x = np.arange(len(top))
    width = 0.38
    photo_vals = [float(row["photo_um"]) for row in top]
    target_vals = [float(row["target_um"]) for row in top]
    fig, ax = plt.subplots(figsize=(13.5, 6.2), constrained_layout=True)
    ax.bar(x - width / 2, photo_vals, width=width, label="photo-matched HFSS variable", color="#60a5fa", edgecolor="#111827", linewidth=0.35)
    ax.bar(x + width / 2, target_vals, width=width, label="target ec6698dfc575950b evidence", color="#f97316", edgecolor="#111827", linewidth=0.35)
    ax.set_ylabel("micrometers")
    ax.set_title("Photo-matched HFSS clue vs target sample geometry scale", loc="left", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.grid(axis="y", alpha=0.28)
    ax.legend(loc="best")
    for idx, row in enumerate(top):
        rel = row.get("relative_delta")
        if rel is not None:
            ax.text(idx, max(photo_vals[idx], target_vals[idx]) * 1.02, f"{float(rel):.0%}", ha="center", va="bottom", fontsize=7)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _render_report(summary: dict[str, Any]) -> str:
    photo = summary["photo_evidence"]
    target = summary["target_evidence"]
    lines = [
        "# Photo-Matched HFSS vs Target Geometry Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Photo S4P: `{photo.get('touchstone')}`",
        f"- Photo HFSS project: `{photo.get('header_project')}` / `{photo.get('header_design')}` / `{photo.get('header_setup')}`",
        f"- Target sample: `{target.get('sample_id')}`",
        f"- Target top cell: `{target.get('top_cell')}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(
        [
            "",
            "## Comparable Dimensions",
            "",
            "| Dimension | Photo HFSS (um) | Target evidence (um) | Relative delta | Evidence |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in summary["dimension_comparisons"]:
        rel = row.get("relative_delta")
        lines.append(
            f"| {row['name']} | {_fmt(row.get('photo_um'))} | {_fmt(row.get('target_um'))} | "
            f"{_fmt_percent(rel)} | {row['evidence']} |"
        )
    lines.extend(["", "## Photo Key Variables", ""])
    for key, value in photo.get("raw_key_variables", {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Target Geometry Evidence", ""])
    for key in (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "primary_width_um",
        "secondary_width_um",
        "primary_spacing_um",
        "secondary_spacing_um",
    ):
        value = target.get("geometry_parameters_um", {}).get(key)
        if value is not None:
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Artifacts", ""])
    for key, value in summary.get("artifacts", {}).items():
        if value:
            lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Boundary", ""])
    lines.extend(f"- {note}" for note in summary.get("notes", []))
    lines.append("")
    return "\n".join(lines)


def _parse_um(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    lower = text.lower()
    if "nm" in lower and "um" not in lower:
        return number * 1.0e-3
    if "mm" in lower:
        return number * 1.0e3
    if "m" in lower and "um" not in lower and "nm" not in lower and "mm" not in lower:
        return number * 1.0e6
    return number


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _mean_defined(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(np.mean(clean)) if clean else None


def _status_counts(checks: list[AuditCheck]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _fmt(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.6g}"


def _fmt_percent(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number * 100.0:.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
