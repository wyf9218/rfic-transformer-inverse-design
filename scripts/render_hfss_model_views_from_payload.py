#!/usr/bin/env python3
"""Render traceable HFSS geometry views from an S8P build payload.

The next-generation flow writes `hfss_s8p_build_payload.json` before any HFSS
solve. This script renders that exact payload so the report has geometry
evidence before and after the Windows/HFSS run:

* 8 signal ports and 8 shield-ground labels.
* M5 shield and higher-metal signal polygons.
* Same-width centered power-line bridges from `power_line_8port_geometry`.
* Basic angle, bridge, port, and signal-to-shield projection checks.

It does not run HFSS and it does not prove EM correctness.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


PORT_NAMES = tuple(f"P{idx:03d}" for idx in range(1, 9))
COLORS = {
    "metal10": "#2563EB",
    "metal9": "#DC2626",
    "metal8": "#B45309",
    "metal7": "#7C3AED",
    "metal6": "#0891B2",
    "metal5": "#5C6470",
    "port": "#111827",
    "ground_port": "#6B7280",
    "bridge": "#F59E0B",
    "dielectric": "#D8E5F7",
    "ground_frame_inner": "#111827",
    "ground_frame_outer": "#374151",
}
POWER_LINE_EXPECTED_GROUND_FRAME_POLICY = (
    "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
)


@dataclass(frozen=True)
class RenderJob:
    payload_path: Path
    out_dir: Path
    sample_id: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    jobs = _render_jobs(args)
    summaries = []
    for job in jobs:
        job.out_dir.mkdir(parents=True, exist_ok=True)
        payload = _normalise_payload(_read_payload(job.payload_path), job.payload_path)
        checks = geometry_quality_checks(payload)
        image_paths = []
        image_paths.append(draw_top(payload, job.out_dir))
        image_paths.extend(draw_3d(payload, job.out_dir))
        image_paths.append(draw_layer_stack_closeup(payload, job.out_dir))
        image_paths.append(draw_quality_checks(payload, checks, job.out_dir))
        checks_path = job.out_dir / "hfss_payload_geometry_quality_checks.json"
        checks_path.write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
        summaries.append(write_summary(payload, job.out_dir, image_paths, args.step_path, checks, checks_path))
    aggregate = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if summaries else "FAIL",
        "rendered_count": len(summaries),
        "summary_paths": [str(path) for path in summaries],
    }
    if args.summary_path:
        summary_path = Path(args.summary_path).expanduser().resolve()
    else:
        summary_path = Path(args.out_dir).expanduser().resolve() / "hfss_payload_geometry_render_batch_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"overall_status={aggregate['overall_status']}")
    print(f"rendered_count={len(summaries)}")
    print(f"summary={summary_path}")
    for path in summaries:
        print(path)
    return 0 if summaries or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload-json", type=Path, help="Path to hfss_s8p_build_payload.json")
    source.add_argument("--build-script", type=Path, help="Legacy build script or sibling of hfss_s8p_build_payload.json")
    source.add_argument("--aedt-packet-summary", type=Path, help="hfss_s8p_aedt_script_packet_summary.json")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--step-path", type=Path)
    parser.add_argument("--summary-path", type=Path)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _render_jobs(args: argparse.Namespace) -> list[RenderJob]:
    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.payload_json:
        payload_path = Path(args.payload_json).expanduser().resolve()
        sample_id = _safe_sample_id(_read_payload(payload_path).get("sample_id") or payload_path.parent.name)
        return [RenderJob(payload_path, out_dir, sample_id)]
    if args.build_script:
        payload_path = _payload_from_build_script(Path(args.build_script).expanduser().resolve())
        sample_id = _safe_sample_id(_read_payload(payload_path).get("sample_id") or payload_path.parent.name)
        return [RenderJob(payload_path, out_dir, sample_id)]
    packet_path = Path(args.aedt_packet_summary).expanduser().resolve()
    packet = _read_json(packet_path)
    jobs = []
    for index, sample in enumerate(packet.get("sample_results") or [], start=1):
        if sample.get("overall_status") not in {None, "PASS"}:
            continue
        payload_path = Path(str(sample.get("payload_json") or "")).expanduser()
        if not payload_path.is_absolute():
            payload_path = (packet_path.parent / payload_path).resolve()
        if not payload_path.is_file():
            continue
        sample_id = _safe_sample_id(str(sample.get("evaluation") or _read_payload(payload_path).get("sample_id") or f"sample_{index:02d}"))
        jobs.append(RenderJob(payload_path, out_dir / f"{index:02d}_{sample_id}", sample_id))
    return jobs


def _read_payload(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    if not data:
        raise ValueError(f"empty or unreadable payload: {path}")
    return data


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _payload_from_build_script(script_path: Path) -> Path:
    sibling = script_path.parent / "hfss_s8p_build_payload.json"
    if sibling.is_file():
        return sibling.resolve()
    module = ast.parse(script_path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PAYLOAD":
                    value = ast.literal_eval(node.value)
                    temp = script_path.parent / "hfss_s8p_build_payload_from_literal.json"
                    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
                    return temp.resolve()
    raise ValueError(f"cannot resolve payload JSON from {script_path}")


def _normalise_payload(raw: dict[str, Any], payload_path: Path) -> dict[str, Any]:
    if "conductor_polygons" in raw:
        polygons = []
        for item in raw.get("conductor_polygons") or []:
            metal = str(item.get("metal") or "")
            index = int(item.get("index") or len(polygons))
            polygons.append(
                {
                    "role": str(item.get("role") or f"{metal}_poly_{index:03d}"),
                    "metal": metal,
                    "index": index,
                    "points_um": _points(item.get("points_um") or []),
                    "bbox_um": [float(v) for v in item.get("bbox_um") or _bbox(_points(item.get("points_um") or []))],
                }
            )
        return {
            "schema": str(raw.get("schema") or "rfic_transformer_hfss_s8p_build_payload.v1"),
            "sample_id": str(raw.get("sample_id") or payload_path.parent.name),
            "payload_path": str(payload_path),
            "source_files": raw.get("source_files") or {},
            "stack": raw.get("stack") or {},
            "bbox_um": [float(v) for v in raw.get("bbox_um") or _combined_bbox([item["bbox_um"] for item in polygons])],
            "polygons": polygons,
            "labels": raw.get("labels") or {},
            "ports": raw.get("ports") or [],
            "power_line_8port_geometry": raw.get("power_line_8port_geometry") or {},
        }

    old_polygons = []
    for index, item in enumerate(raw.get("polygons") or []):
        metal = "metal10" if str(item.get("role", "")).startswith("primary") else "metal9"
        points = _points(item.get("points_um") or [])
        old_polygons.append(
            {
                "role": str(item.get("role") or f"{metal}_poly_{index:03d}"),
                "metal": metal,
                "index": index,
                "points_um": points,
                "bbox_um": [float(v) for v in item.get("bbox_um") or _bbox(points)],
            }
        )
    for index, (name, rect) in enumerate(_legacy_shield_rectangles(raw), start=len(old_polygons)):
        points = _rect_points(rect)
        old_polygons.append({"role": name, "metal": "metal5", "index": index, "points_um": points, "bbox_um": list(rect)})
    return {
        "schema": str(raw.get("schema") or "legacy_hfss_payload"),
        "sample_id": str(raw.get("sample_id") or payload_path.parent.name),
        "payload_path": str(payload_path),
        "source_files": {},
        "stack": raw.get("stack") or {},
        "bbox_um": _combined_bbox([item["bbox_um"] for item in old_polygons]),
        "polygons": old_polygons,
        "labels": raw.get("labels") or {},
        "ports": _legacy_ports(raw),
        "power_line_8port_geometry": {},
    }


def _points(raw: list[Any]) -> list[list[float]]:
    return [[float(item[0]), float(item[1])] for item in raw if isinstance(item, (list, tuple)) and len(item) >= 2]


def _bbox(points: list[list[float]]) -> list[float]:
    if not points:
        return [0.0, 0.0, 0.0, 0.0]
    xs = [float(item[0]) for item in points]
    ys = [float(item[1]) for item in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _combined_bbox(boxes: list[list[float]]) -> list[float]:
    clean = [box for box in boxes if len(box) == 4]
    if not clean:
        return [0.0, 0.0, 0.0, 0.0]
    return [min(box[0] for box in clean), min(box[1] for box in clean), max(box[2] for box in clean), max(box[3] for box in clean)]


def _rect_points(rect: tuple[float, float, float, float]) -> list[list[float]]:
    x0, y0, x1, y1 = rect
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _legacy_shield_rectangles(payload: dict[str, Any]) -> list[tuple[str, tuple[float, float, float, float]]]:
    if "shield_bbox_um" not in payload:
        return []
    sx0, sy0, sx1, sy1 = [float(v) for v in payload["shield_bbox_um"]]
    width = float(payload.get("shield_width_um", 0.0))
    return [
        ("shield_m5_top", (sx0, sy1 - width, sx1, sy1)),
        ("shield_m5_bottom", (sx0, sy0, sx1, sy0 + width)),
        ("shield_m5_left", (sx0, sy0 + width, sx0 + width, sy1 - width)),
        ("shield_m5_right", (sx1 - width, sy0 + width, sx1, sy1 - width)),
    ]


def _legacy_ports(payload: dict[str, Any]) -> list[dict[str, Any]]:
    labels = payload.get("labels") or {}
    ports = []
    for name in ("P001", "P002", "P003", "P004"):
        if name in labels:
            ports.append({"port_name": name, "role": "", "signal_label": labels.get(name, {}), "ground_label": labels.get(f"{name}_G", {})})
    return ports


def conductor_mid_z(conductor: dict[str, Any]) -> float:
    return 0.5 * (float(conductor.get("z_bottom_um", 0.0)) + float(conductor.get("z_top_um", 0.0)))


def _conductor(payload: dict[str, Any], metal: str) -> dict[str, Any]:
    conductors = (payload.get("stack") or {}).get("conductors") or {}
    if metal in conductors:
        return conductors[metal]
    fallback_z = {"metal5": 706.0, "metal9": 711.0, "metal10": 713.0}.get(metal, 710.0)
    return {"name": metal, "z_bottom_um": fallback_z, "z_top_um": fallback_z + 0.1, "thickness_um": 0.1}


def _dielectric_box(payload: dict[str, Any]) -> dict[str, float]:
    x0, y0, x1, y1 = [float(v) for v in payload["bbox_um"]]
    margin = 0.18 * max(x1 - x0, y1 - y0, 100.0)
    conductors = (payload.get("stack") or {}).get("conductors") or {}
    z_values = []
    for item in conductors.values():
        z_values.extend([float(item.get("z_bottom_um", 700.0)), float(item.get("z_top_um", 713.0))])
    z0 = min(z_values) - 5.0 if z_values else 700.0
    z1 = max(z_values) + 5.0 if z_values else 718.0
    return {"x0": x0 - margin, "y0": y0 - margin, "x1": x1 + margin, "y1": y1 + margin, "z0": z0, "z1": z1}


def polygon_at_z(points: list[list[float]], z: float) -> list[tuple[float, float, float]]:
    return [(float(x), float(y), z) for x, y in points]


def box_faces(x0: float, y0: float, x1: float, y1: float, z0: float, z1: float) -> list[list[tuple[float, float, float]]]:
    p000, p100, p110, p010 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
    p001, p101, p111, p011 = (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
    return [[p000, p100, p110, p010], [p001, p101, p111, p011], [p000, p100, p101, p001], [p100, p110, p111, p101], [p110, p010, p011, p111], [p010, p000, p001, p011]]


def polygon_area(points: list[tuple[float, float]] | list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for p0, p1 in zip(points, points[1:] + points[:1]):
        area += float(p0[0]) * float(p1[1]) - float(p1[0]) * float(p0[1])
    return 0.5 * abs(area)


def _clip_with_boundary(points: list[tuple[float, float]], *, axis: int, value: float, keep_greater: bool) -> list[tuple[float, float]]:
    if not points:
        return []

    def inside(point: tuple[float, float]) -> bool:
        return point[axis] >= value - 1.0e-12 if keep_greater else point[axis] <= value + 1.0e-12

    def intersect(p0: tuple[float, float], p1: tuple[float, float]) -> tuple[float, float]:
        denom = p1[axis] - p0[axis]
        if abs(denom) <= 1.0e-15:
            return p1
        t = (value - p0[axis]) / denom
        return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))

    output = []
    prev = points[-1]
    prev_inside = inside(prev)
    for curr in points:
        curr_inside = inside(curr)
        if curr_inside:
            if not prev_inside:
                output.append(intersect(prev, curr))
            output.append(curr)
        elif prev_inside:
            output.append(intersect(prev, curr))
        prev = curr
        prev_inside = curr_inside
    return output


def polygon_rect_overlap_area(points: list[list[float]], rect: list[float]) -> float:
    x0, y0, x1, y1 = [float(v) for v in rect]
    clipped = [(float(x), float(y)) for x, y in points]
    clipped = _clip_with_boundary(clipped, axis=0, value=x0, keep_greater=True)
    clipped = _clip_with_boundary(clipped, axis=0, value=x1, keep_greater=False)
    clipped = _clip_with_boundary(clipped, axis=1, value=y0, keep_greater=True)
    clipped = _clip_with_boundary(clipped, axis=1, value=y1, keep_greater=False)
    return polygon_area(clipped)


def bbox_gap(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = [float(v) for v in a]
    bx0, by0, bx1, by1 = [float(v) for v in b]
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    return math.hypot(dx, dy)


def edge_angle_summary(points: list[list[float]]) -> dict[str, Any]:
    allowed = (0.0, 45.0, 90.0, 135.0)
    tolerance_deg = 1.0e-3
    counts = {str(int(angle)): 0 for angle in allowed}
    off_grid = []
    for p0, p1 in zip(points, points[1:] + points[:1]):
        dx = float(p1[0]) - float(p0[0])
        dy = float(p1[1]) - float(p0[1])
        if math.hypot(dx, dy) <= 1.0e-9:
            continue
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        nearest = min(allowed, key=lambda item: abs(item - angle))
        if abs(nearest - angle) <= tolerance_deg:
            counts[str(int(nearest))] += 1
        else:
            off_grid.append(float(angle))
    return {"allowed_edge_angle_counts": counts, "angle_tolerance_deg": tolerance_deg, "off_grid_edge_angles_deg": off_grid}


def internal_angle_values(points: list[list[float]]) -> list[float]:
    values = []
    pts = [(float(x), float(y)) for x, y in points]
    for prev, point, nxt in zip(pts[-1:] + pts[:-1], pts, pts[1:] + pts[:1]):
        v1 = (prev[0] - point[0], prev[1] - point[1])
        v2 = (nxt[0] - point[0], nxt[1] - point[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 <= 1.0e-9 or l2 <= 1.0e-9:
            continue
        dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
        values.append(round(math.degrees(math.acos(dot)), 9))
    return values


def geometry_quality_checks(payload: dict[str, Any]) -> dict[str, Any]:
    labels = payload.get("labels") or {}
    ports = payload.get("ports") or []
    shield_polygons = [poly for poly in payload["polygons"] if poly["metal"] == "metal5"]
    signal_polygons = [poly for poly in payload["polygons"] if poly["metal"] != "metal5"]
    polygon_checks = {}
    signal_overlap = {}
    signal_clearance = {}
    for poly in payload["polygons"]:
        angles = internal_angle_values(poly["points_um"])
        polygon_checks[poly["role"]] = {
            **edge_angle_summary(poly["points_um"]),
            "unique_internal_angles_deg": sorted({round(value, 6) for value in angles}),
            "internal_angle_count": len(angles),
            "metal": poly["metal"],
        }
    for poly in signal_polygons:
        overlaps = {shield["role"]: polygon_rect_overlap_area(poly["points_um"], shield["bbox_um"]) for shield in shield_polygons}
        gaps = [bbox_gap(poly["bbox_um"], shield["bbox_um"]) for shield in shield_polygons]
        signal_overlap[poly["role"]] = {"total_overlap_um2": float(sum(overlaps.values())), "by_shield_polygon_um2": overlaps}
        signal_clearance[poly["role"]] = {"min_bbox_clearance_um": None if not gaps else float(min(gaps))}

    power_line = payload.get("power_line_8port_geometry") or {}
    bridge_checks = {}
    for name in ("primary_bridge", "secondary_bridge"):
        bridge = power_line.get(name) or {}
        width = _as_float(bridge.get("width_um"))
        left_edge = _as_float(bridge.get("power_line_left_edge_x_um"))
        right_edge = _as_float(bridge.get("power_line_right_edge_x_um"))
        edge_width = None if left_edge is None or right_edge is None else abs(right_edge - left_edge)
        bridge_checks[name] = {
            "width_um": width,
            "power_line_width_from_edges_um": edge_width,
            "width_matches_power_line": width is not None and edge_width is not None and abs(width - edge_width) <= 1.0e-12,
            "is_horizontal": bool(bridge.get("is_horizontal")),
            "center_y_um": _as_float(bridge.get("center_y_um")),
            "delta_y_um": _as_float(bridge.get("delta_y_um")),
            "centered_y_pass": _as_float(bridge.get("delta_y_um")) in (None, 0.0),
            "extends_away_from_coil_interior": bridge.get("extends_away_from_coil_interior") is True,
        }
    ground_frame_checks = ground_frame_quality_checks(power_line)

    return {
        "schema": "hfss_payload_geometry_quality_checks.v2",
        "port_checks": {
            "port_count": len(ports),
            "all_p001_p008_labels_present": all(name in labels for name in PORT_NAMES),
            "all_p001_p008_ground_labels_present": all(f"{name}_G" in labels for name in PORT_NAMES),
            "payload_has_eight_ports": len(ports) == 8,
        },
        "power_line_bridge_checks": bridge_checks,
        "power_line_ground_frame_checks": ground_frame_checks,
        "polygon_checks": polygon_checks,
        "signal_to_shield_projection_overlap_um2": signal_overlap,
        "signal_to_shield_bbox_clearance_um": signal_clearance,
        "summary_flags": {
            "all_edge_angles_on_0_45_90_135_grid": all(not item["off_grid_edge_angles_deg"] for item in polygon_checks.values()),
            "signal_shield_projection_overlap_zero": all(item["total_overlap_um2"] <= 1.0e-9 for item in signal_overlap.values()),
            "bridges_match_power_line_width_horizontal_centered": all(
                item["width_matches_power_line"] and item["is_horizontal"] and item["centered_y_pass"]
                for item in bridge_checks.values()
            ),
            "bridges_stay_outside_coil_interior": all(
                item["extends_away_from_coil_interior"] for item in bridge_checks.values()
            ),
            "ground_frame_bbox_matches_recorded_width": bool(ground_frame_checks.get("bbox_expands_by_ground_frame_width")),
            "ground_frame_policy_is_rectangular": bool(ground_frame_checks.get("policy_is_rectangular_ground_frame")),
        },
        "interpretation": "These are layout-provenance checks from the HFSS build payload, not EM accuracy checks.",
    }


def ground_frame_quality_checks(power_line: dict[str, Any]) -> dict[str, Any]:
    width = _as_float(power_line.get("ground_frame_width_um")) if isinstance(power_line, dict) else None
    policy = str(power_line.get("ground_frame_policy", "")) if isinstance(power_line, dict) else ""
    inner = _bbox_dict(power_line.get("shield_inner_bbox_um")) if isinstance(power_line, dict) else None
    outer = _bbox_dict(power_line.get("shield_outer_bbox_um")) if isinstance(power_line, dict) else None
    edges = _ground_frame_edges(inner, outer)
    expands = (
        width is not None
        and bool(edges)
        and all(value is not None and abs(float(value) - float(width)) <= 1.0e-9 for value in edges.values())
    )
    return {
        "ground_frame_width_um": width,
        "ground_frame_policy": policy,
        "policy_is_rectangular_ground_frame": policy == POWER_LINE_EXPECTED_GROUND_FRAME_POLICY,
        "shield_inner_bbox_um": inner,
        "shield_outer_bbox_um": outer,
        "frame_edges_um": edges,
        "bbox_expands_by_ground_frame_width": expands,
    }


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _bbox_dict(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key in ("min_x_um", "min_y_um", "max_x_um", "max_y_um"):
        value = _as_float(raw.get(key))
        if value is None:
            return None
        out[key] = float(value)
    return out


def _bbox_rect_points(bbox: dict[str, float]) -> list[list[float]]:
    return _rect_points((bbox["min_x_um"], bbox["min_y_um"], bbox["max_x_um"], bbox["max_y_um"]))


def _ground_frame_edges(inner: dict[str, float] | None, outer: dict[str, float] | None) -> dict[str, float] | None:
    if inner is None or outer is None:
        return None
    return {
        "left_um": float(inner["min_x_um"]) - float(outer["min_x_um"]),
        "right_um": float(outer["max_x_um"]) - float(inner["max_x_um"]),
        "bottom_um": float(inner["min_y_um"]) - float(outer["min_y_um"]),
        "top_um": float(outer["max_y_um"]) - float(inner["max_y_um"]),
    }


def draw_top(payload: dict[str, Any], outdir: Path) -> Path:
    diel = _dielectric_box(payload)
    fig, ax = plt.subplots(figsize=(12, 10), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.set_title(
        f"HFSS S8P payload top view - {payload['sample_id']}\n"
        "Rendered from hfss_s8p_build_payload.json before HFSS solve",
        fontsize=13,
        color="#111827",
        pad=14,
    )
    ax.add_patch(
        MplPolygon(
            _rect_points((diel["x0"], diel["y0"], diel["x1"], diel["y1"])),
            closed=True,
            facecolor=COLORS["dielectric"],
            edgecolor="#94A3B8",
            linewidth=0.9,
            alpha=0.16,
            label="HFSS dielectric/air footprint",
        )
    )
    for poly in payload["polygons"]:
        color = COLORS.get(poly["metal"], "#9CA3AF")
        label = poly["metal"] if poly["metal"] not in ax.get_legend_handles_labels()[1] else None
        ax.add_patch(MplPolygon(poly["points_um"], closed=True, facecolor=color, edgecolor="#111827", linewidth=0.9, alpha=0.72, label=label))
        cx = 0.5 * (poly["bbox_um"][0] + poly["bbox_um"][2])
        cy = 0.5 * (poly["bbox_um"][1] + poly["bbox_um"][3])
        ax.text(cx, cy, poly["role"], fontsize=6.5, color="#111827", ha="center", va="center")

    ground_frame = ground_frame_quality_checks(payload.get("power_line_8port_geometry") or {})
    outer_bbox = ground_frame.get("shield_outer_bbox_um")
    inner_bbox = ground_frame.get("shield_inner_bbox_um")
    if isinstance(outer_bbox, dict):
        ax.add_patch(
            MplPolygon(
                _bbox_rect_points(outer_bbox),
                closed=True,
                facecolor="none",
                edgecolor=COLORS["ground_frame_outer"],
                linewidth=1.8,
                linestyle="--",
                label="recorded M5 ground-frame outer bbox",
            )
        )
    if isinstance(inner_bbox, dict):
        ax.add_patch(
            MplPolygon(
                _bbox_rect_points(inner_bbox),
                closed=True,
                facecolor="none",
                edgecolor=COLORS["ground_frame_inner"],
                linewidth=1.8,
                linestyle=":",
                label="recorded white inner window",
            )
        )

    labels = payload.get("labels") or {}
    for port in payload.get("ports") or []:
        pname = str(port.get("port_name") or "")
        gname = str(port.get("ground_name") or f"{pname}_G")
        signal = labels.get(pname) or port.get("signal_label") or {}
        ground = labels.get(gname) or port.get("ground_label") or {}
        if not signal.get("origin_um") or not ground.get("origin_um"):
            continue
        x, y = [float(v) for v in signal["origin_um"]]
        gx, gy = [float(v) for v in ground["origin_um"]]
        ax.plot([x, gx], [y, gy], color=COLORS["port"], linewidth=1.0, alpha=0.75)
        ax.scatter([x], [y], color=COLORS["port"], s=28, zorder=5)
        ax.scatter([gx], [gy], color=COLORS["ground_port"], s=22, zorder=5)
        ax.text(x + 1.8, y + 1.8, pname, fontsize=8, color=COLORS["port"])

    for name, bridge in (payload.get("power_line_8port_geometry") or {}).items():
        if not isinstance(bridge, dict) or not name.endswith("_bridge"):
            continue
        start = bridge.get("coil_anchor") or {}
        end = bridge.get("power_line_edge") or {}
        if {"x_um", "y_um"} <= set(start) and {"x_um", "y_um"} <= set(end):
            ax.plot([float(start["x_um"]), float(end["x_um"])], [float(start["y_um"]), float(end["y_um"])], color=COLORS["bridge"], linewidth=2.0, label="same-width bridge" if name == "primary_bridge" else None)

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(diel["x0"], diel["x1"])
    ax.set_ylim(diel["y0"], diel["y1"])
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.grid(True, linestyle=":", linewidth=0.8, color="#D1D5DB")
    _dedupe_legend(ax)
    path = outdir / "hfss_payload_geometry_top_annotated.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_3d(payload: dict[str, Any], outdir: Path) -> list[Path]:
    diel = _dielectric_box(payload)
    paths = []
    for name, elev, azim in [("isometric", 27, -55), ("top_3d", 90, -90), ("side_xz", 8, -90), ("side_yz", 8, 0)]:
        fig = plt.figure(figsize=(12, 9), facecolor="#FCFCFD")
        ax = fig.add_subplot(111, projection="3d")
        ax.set_title(f"HFSS S8P payload geometry - {payload['sample_id']} ({name})", fontsize=13, color="#111827", pad=18)
        ax.add_collection3d(
            Poly3DCollection(
                box_faces(diel["x0"], diel["y0"], diel["x1"], diel["y1"], diel["z0"], diel["z1"]),
                facecolors=COLORS["dielectric"],
                alpha=0.10,
                linewidths=0.25,
                edgecolors="#94A3B8",
            )
        )
        for poly in payload["polygons"]:
            conductor = _conductor(payload, poly["metal"])
            z = conductor_mid_z(conductor)
            verts = polygon_at_z(poly["points_um"], z)
            ax.add_collection3d(
                Poly3DCollection([verts], facecolors=COLORS.get(poly["metal"], "#9CA3AF"), edgecolors="#111827", linewidths=0.65, alpha=0.82)
            )
            if poly["index"] % 6 == 0:
                ax.text(verts[0][0], verts[0][1], z + 0.4, poly["role"], color="#111827", fontsize=6)
        for port in payload.get("ports") or []:
            signal = port.get("signal_label") or {}
            if not signal.get("origin_um"):
                continue
            metal = str(port.get("signal_metal") or "")
            z = conductor_mid_z(_conductor(payload, metal))
            x, y = [float(v) for v in signal["origin_um"]]
            ax.scatter([x], [y], [z + 0.8], color=COLORS["port"], s=22, depthshade=False)
            ax.text(x, y, z + 1.5, str(port.get("port_name", "")), color=COLORS["port"], fontsize=7)
        ax.set_xlim(diel["x0"], diel["x1"])
        ax.set_ylim(diel["y0"], diel["y1"])
        ax.set_zlim(diel["z0"], diel["z1"])
        ax.set_box_aspect((diel["x1"] - diel["x0"], diel["y1"] - diel["y0"], max(diel["z1"] - diel["z0"], 20.0)))
        ax.set_xlabel("x (um)")
        ax.set_ylabel("y (um)")
        ax.set_zlabel("z (um)")
        ax.view_init(elev=elev, azim=azim)
        ax.grid(True, alpha=0.25)
        path = outdir / f"hfss_payload_geometry_{name}.png"
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def draw_layer_stack_closeup(payload: dict[str, Any], outdir: Path) -> Path:
    conductors = (payload.get("stack") or {}).get("conductors") or {}
    used_metals = sorted({poly["metal"] for poly in payload["polygons"]}, key=lambda metal: conductor_mid_z(_conductor(payload, metal)))
    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.set_title(f"HFSS S8P metal-layer z close-up - {payload['sample_id']}", fontsize=13, color="#111827", pad=14)
    for idx, metal in enumerate(used_metals):
        conductor = conductors.get(metal) or _conductor(payload, metal)
        z = conductor_mid_z(conductor)
        ax.hlines(z, 0.12, 0.85, color=COLORS.get(metal, "#9CA3AF"), linewidth=6)
        ax.scatter([0.5], [z], color=COLORS.get(metal, "#9CA3AF"), s=70, zorder=4)
        ax.text(0.89, z, f"{metal}: z={z:.3f} um", va="center", fontsize=10, color="#111827")
    z_values = [conductor_mid_z(_conductor(payload, metal)) for metal in used_metals] or [700.0, 713.0]
    ax.set_xlim(0, 1.35)
    ax.set_ylim(min(z_values) - 2.0, max(z_values) + 2.0)
    ax.set_xticks([])
    ax.set_ylabel("z (um)")
    ax.grid(True, axis="y", linestyle=":", linewidth=0.8, color="#D1D5DB")
    path = outdir / "hfss_payload_geometry_layer_stack_closeup.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_quality_checks(payload: dict[str, Any], checks: dict[str, Any], outdir: Path) -> Path:
    fig = plt.figure(figsize=(13, 9), facecolor="#FCFCFD")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.1])
    fig.suptitle(f"HFSS S8P payload geometry checks - {payload['sample_id']}", fontsize=15, fontweight="bold", color="#111827", y=0.98)
    fig.text(0.5, 0.945, "Ports, same-width bridges, angle grid, and signal-to-shield projection checks from the HFSS build payload.", ha="center", color="#6B7280", fontsize=10)

    ax_angles = fig.add_subplot(gs[0, 0])
    roles = list(checks["polygon_checks"].keys())
    off_counts = [len(checks["polygon_checks"][role]["off_grid_edge_angles_deg"]) for role in roles]
    ax_angles.bar(range(len(roles)), off_counts, color="#64748B", alpha=0.82)
    ax_angles.set_xticks(range(len(roles)), [role[:18] for role in roles], rotation=50, ha="right", fontsize=7)
    ax_angles.set_ylabel("off-grid edge count")
    ax_angles.set_title("0/45/90/135 degree edge-grid audit")
    ax_angles.grid(True, axis="y", linestyle=":", color="#D1D5DB")

    ax_overlap = fig.add_subplot(gs[0, 1])
    overlap_items = checks["signal_to_shield_projection_overlap_um2"]
    labels = list(overlap_items.keys())
    values = [overlap_items[key]["total_overlap_um2"] for key in labels]
    ax_overlap.barh([label[:22] for label in labels], values, color="#DC2626", alpha=0.72)
    ax_overlap.set_xlabel("signal to M5 shield projected overlap (um^2)")
    ax_overlap.set_title("Signal metal must not press into grounded shield")
    ax_overlap.grid(True, axis="x", linestyle=":", color="#D1D5DB")

    ax_text = fig.add_subplot(gs[1, :])
    ax_text.axis("off")
    port = checks["port_checks"]
    flags = checks["summary_flags"]
    bridge = checks["power_line_bridge_checks"]
    ground_frame = checks["power_line_ground_frame_checks"]
    lines = [
        f"port_count = {port['port_count']}; P001-P008 labels = {port['all_p001_p008_labels_present']}; P001_G-P008_G labels = {port['all_p001_p008_ground_labels_present']}",
        f"all_edge_angles_on_0_45_90_135_grid = {flags['all_edge_angles_on_0_45_90_135_grid']}",
        f"signal_shield_projection_overlap_zero = {flags['signal_shield_projection_overlap_zero']}",
        f"bridges_match_power_line_width_horizontal_centered = {flags['bridges_match_power_line_width_horizontal_centered']}",
        f"ground_frame_bbox_matches_recorded_width = {flags['ground_frame_bbox_matches_recorded_width']}; "
        f"ground_frame_width_um = {ground_frame['ground_frame_width_um']}; edges_um = {ground_frame['frame_edges_um']}",
        "",
        "Bridge checks:",
    ]
    for name, item in bridge.items():
        lines.append(
            f"  {name}: width_um={item['width_um']}; power_line_width_from_edges_um={item['power_line_width_from_edges_um']}; "
            f"horizontal={item['is_horizontal']}; delta_y_um={item['delta_y_um']}; "
            f"same_width={item['width_matches_power_line']}; outside_coil_interior={item['extends_away_from_coil_interior']}"
        )
    lines.append("")
    lines.append("Interpretation: these checks guard the layout contract before EMX/HFSS physics validation.")
    ax_text.text(0.01, 0.98, "\n".join(lines), va="top", ha="left", family="monospace", fontsize=10, color="#111827")

    path = outdir / "hfss_payload_geometry_quality_checks.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return path


def write_summary(payload: dict[str, Any], outdir: Path, image_paths: list[Path], step_path: Path | None, checks: dict[str, Any], checks_path: Path) -> Path:
    conductors = (payload.get("stack") or {}).get("conductors") or {}
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "sample_id": payload["sample_id"],
        "payload_path": payload["payload_path"],
        "source_files": payload.get("source_files", {}),
        "source": "Rendered from the same hfss_s8p_build_payload.json used by build_hfss_s8p_from_payload.py.",
        "hfss_objects": {
            "conductor_polygon_count": len(payload["polygons"]),
            "ports": [str(port.get("port_name", "")) for port in payload.get("ports") or []],
            "labels": sorted((payload.get("labels") or {}).keys()),
        },
        "metal_mid_z_um": {metal: conductor_mid_z(item) for metal, item in conductors.items()},
        "bbox_um": payload["bbox_um"],
        "image_paths": [str(path) for path in image_paths],
        "quality_checks_json": str(checks_path),
        "step_path": str(step_path) if step_path else None,
        "quality_checks": checks,
    }
    path = outdir / "hfss_payload_geometry_render_summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _dedupe_legend(ax: Any) -> None:
    handles, labels = ax.get_legend_handles_labels()
    unique = {}
    for handle, label in zip(handles, labels):
        if label and label not in unique:
            unique[label] = handle
    if unique:
        ax.legend(unique.values(), unique.keys(), loc="upper right", frameon=False, fontsize=8)


def _safe_sample_id(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value)).strip("_")
    return text[:80] or "sample"


if __name__ == "__main__":
    raise SystemExit(main())
