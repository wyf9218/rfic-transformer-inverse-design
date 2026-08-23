#!/usr/bin/env python3
"""Build GDS/manifest inputs for EMX-vs-HFSS calibration structures."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gdstk

M5_DRAW = (35, 0)
M5_PIN = (135, 0)
M9_DRAW = (39, 60)
M9_PIN = (139, 0)
M10_DRAW = (74, 0)
M10_PIN = (126, 0)
DRAW_LAYER_FOR_PIN = {
    M5_PIN: M5_DRAW,
    M9_PIN: M9_DRAW,
    M10_PIN: M10_DRAW,
}


@dataclass(frozen=True)
class Port:
    name: str
    signal_xy_um: tuple[float, float]
    ground_xy_um: tuple[float, float]
    signal_layer: tuple[int, int]
    ground_layer: tuple[int, int] = M5_PIN


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    structures = [
        _straight_line("m9_straight_line", "metal9", M9_DRAW, M9_PIN, args),
        _straight_line("m10_straight_line", "metal10", M10_DRAW, M10_PIN, args),
        _single_inductor("m9_single_inductor", "metal9", M9_DRAW, M9_PIN, args),
        _single_inductor("m10_single_inductor", "metal10", M10_DRAW, M10_PIN, args),
        _two_port_transformer(args),
    ]

    manifest_entries = []
    for spec in structures:
        entry = _write_structure(out_dir, spec, args)
        manifest_entries.append(entry)

    manifest = {
        "schema": "rfic_transformer_emx_hfss_calibration_structures.v1",
        "purpose": "Isolate EMX/HFSS differences before returning to the full 8-port transformer.",
        "frequency_grid": {
            "start_ghz": args.start_ghz,
            "stop_ghz": args.stop_ghz,
            "step_ghz": args.step_ghz,
            "target_ghz": args.target_ghz,
        },
        "gate": {
            "target_percent_error": args.max_percent_error,
            "rule": "Each calibration stage must pass before the full 8-port transformer can be trusted.",
        },
        "layer_map": {
            "metal5_draw": {"layer": M5_DRAW[0], "datatype": M5_DRAW[1]},
            "metal5_pin": {"layer": M5_PIN[0], "datatype": M5_PIN[1]},
            "metal9_draw": {"layer": M9_DRAW[0], "datatype": M9_DRAW[1]},
            "metal9_pin": {"layer": M9_PIN[0], "datatype": M9_PIN[1]},
            "metal10_draw": {"layer": M10_DRAW[0], "datatype": M10_DRAW[1]},
            "metal10_pin": {"layer": M10_PIN[0], "datatype": M10_PIN[1]},
        },
        "structures": manifest_entries,
        "next_decision": "Run Stage 1 straight-line EMX/HFSS first; do not relaunch 8-port full-frequency validation until the calibration stages pass.",
    }
    manifest_path = out_dir / "calibration_structures_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_index_csv(out_dir / "calibration_structures_index.csv", manifest_entries)
    _write_report(out_dir / "CALIBRATION_STRUCTURES_README_CN.md", manifest)

    print(f"manifest={manifest_path}")
    print(f"structures={len(manifest_entries)}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--line-length-um", type=float, default=600.0)
    parser.add_argument("--line-width-um", type=float, default=4.0)
    parser.add_argument("--inductor-outer-width-um", type=float, default=260.0)
    parser.add_argument("--inductor-outer-height-um", type=float, default=220.0)
    parser.add_argument("--shield-margin-um", type=float, default=90.0)
    parser.add_argument("--start-ghz", type=float, default=5.0)
    parser.add_argument("--stop-ghz", type=float, default=60.0)
    parser.add_argument("--step-ghz", type=float, default=1.0)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    return parser.parse_args(argv)


def _straight_line(
    name: str,
    metal: str,
    draw_layer: tuple[int, int],
    pin_layer: tuple[int, int],
    args: argparse.Namespace,
) -> dict[str, Any]:
    length = float(args.line_length_um)
    width = float(args.line_width_um)
    half_l = length / 2.0
    half_w = width / 2.0
    signal = [gdstk.rectangle((-half_l, -half_w), (half_l, half_w), layer=draw_layer[0], datatype=draw_layer[1])]
    ports = [
        Port("P001", (-half_l, 0.0), (-half_l, 0.0), pin_layer),
        Port("P002", (half_l, 0.0), (half_l, 0.0), pin_layer),
    ]
    bbox = (-half_l - args.shield_margin_um, -args.shield_margin_um, half_l + args.shield_margin_um, args.shield_margin_um)
    return {
        "name": name,
        "stage": "stage1_straight_line",
        "metal": metal,
        "top_cell": name.upper(),
        "purpose": f"Calibrate {metal} metal, port, and local-ground return path.",
        "polygons": signal,
        "ports": ports,
        "bbox": bbox,
        "expected_metrics": ["r", "l", "c"],
    }


def _single_inductor(
    name: str,
    metal: str,
    draw_layer: tuple[int, int],
    pin_layer: tuple[int, int],
    args: argparse.Namespace,
) -> dict[str, Any]:
    half_w = args.inductor_outer_width_um / 2.0
    half_h = args.inductor_outer_height_um / 2.0
    chamfer = min(half_w, half_h) * 0.34
    gap = args.line_width_um * 3.0
    points = [
        (-half_w, gap),
        (-half_w, half_h - chamfer),
        (-half_w + chamfer, half_h),
        (half_w - chamfer, half_h),
        (half_w, half_h - chamfer),
        (half_w, -half_h + chamfer),
        (half_w - chamfer, -half_h),
        (-half_w + chamfer, -half_h),
        (-half_w, -half_h + chamfer),
        (-half_w, -gap),
    ]
    path = gdstk.FlexPath(points, args.line_width_um, layer=draw_layer[0], datatype=draw_layer[1], ends="flush")
    ports = [
        Port("P001", (-half_w, gap), (-half_w, gap), pin_layer),
        Port("P002", (-half_w, -gap), (-half_w, -gap), pin_layer),
    ]
    bbox = (
        -half_w - args.shield_margin_um,
        -half_h - args.shield_margin_um,
        half_w + args.shield_margin_um,
        half_h + args.shield_margin_um,
    )
    return {
        "name": name,
        "stage": "stage2_single_inductor",
        "metal": metal,
        "top_cell": name.upper(),
        "purpose": f"Calibrate isolated {metal} octagonal inductor L/Q without transformer coupling.",
        "polygons": list(path.to_polygons()),
        "ports": ports,
        "bbox": bbox,
        "expected_metrics": ["l", "q"],
    }


def _two_port_transformer(args: argparse.Namespace) -> dict[str, Any]:
    outer_m10 = _single_inductor("tmp_m10", "metal10", M10_DRAW, M10_PIN, args)
    inner_args = argparse.Namespace(**vars(args))
    inner_args.inductor_outer_width_um = args.inductor_outer_width_um * 0.72
    inner_args.inductor_outer_height_um = args.inductor_outer_height_um * 0.72
    inner_m9 = _single_inductor("tmp_m9", "metal9", M9_DRAW, M9_PIN, inner_args)
    ports = [
        Port("P001", outer_m10["ports"][0].signal_xy_um, outer_m10["ports"][0].ground_xy_um, M10_PIN),
        Port("P002", outer_m10["ports"][1].signal_xy_um, outer_m10["ports"][1].ground_xy_um, M10_PIN),
        Port("P003", inner_m9["ports"][0].signal_xy_um, inner_m9["ports"][0].ground_xy_um, M9_PIN),
        Port("P004", inner_m9["ports"][1].signal_xy_um, inner_m9["ports"][1].ground_xy_um, M9_PIN),
    ]
    bbox = outer_m10["bbox"]
    return {
        "name": "simple_2port_transformer",
        "stage": "stage3_simple_transformer",
        "metal": "metal10_vs_metal9",
        "top_cell": "SIMPLE_2PORT_TRANSFORMER",
        "purpose": "Keep M9/M10 coupling but remove 8-port power-line complexity.",
        "polygons": [*outer_m10["polygons"], *inner_m9["polygons"]],
        "ports": ports,
        "bbox": bbox,
        "expected_metrics": ["lp", "ls", "q", "kw"],
    }


def _write_structure(out_dir: Path, spec: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    struct_dir = out_dir / spec["name"]
    struct_dir.mkdir(parents=True, exist_ok=True)
    gds_path = struct_dir / f"{spec['name']}.gds"
    preview_path = struct_dir / f"{spec['name']}_preview.png"
    port_map_path = struct_dir / f"{spec['name']}_ports.json"

    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    cell = lib.new_cell(spec["top_cell"])
    for polygon in spec["polygons"]:
        cell.add(polygon)
    _add_ground_frame(cell, spec["bbox"])
    _add_ports(cell, spec["ports"], args.line_width_um, spec["bbox"])
    lib.write_gds(str(gds_path))
    _write_preview(preview_path, spec)

    port_map = {
        "top_cell": spec["top_cell"],
        "ports": [
            {
                "name": port.name,
                "ground": f"{port.name}_G",
                "signal_xy_um": list(port.signal_xy_um),
                "ground_xy_um": list(port.ground_xy_um),
                "signal_layer": {"layer": port.signal_layer[0], "datatype": port.signal_layer[1]},
                "ground_layer": {"layer": port.ground_layer[0], "datatype": port.ground_layer[1]},
                "emx_port_argument": f"--port={port.name}={port.name}:{port.name}_G",
            }
            for port in spec["ports"]
        ],
    }
    port_map_path.write_text(json.dumps(port_map, indent=2), encoding="utf-8")

    return {
        "name": spec["name"],
        "stage": spec["stage"],
        "top_cell": spec["top_cell"],
        "purpose": spec["purpose"],
        "metal": spec["metal"],
        "port_count": len(spec["ports"]),
        "expected_metrics": spec["expected_metrics"],
        "gds": str(gds_path),
        "preview": str(preview_path),
        "port_map": str(port_map_path),
        "recommended_first_run": {
            "frequency_ghz": [args.start_ghz, args.stop_ghz],
            "max_percent_error": args.max_percent_error,
        },
    }


def _add_ground_frame(cell: gdstk.Cell, bbox: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = bbox
    width = 12.0
    layer, datatype = M5_DRAW
    cell.add(gdstk.rectangle((x0, y0), (x0 + width, y1), layer=layer, datatype=datatype))
    cell.add(gdstk.rectangle((x1 - width, y0), (x1, y1), layer=layer, datatype=datatype))
    cell.add(gdstk.rectangle((x0 + width, y0), (x1 - width, y0 + width), layer=layer, datatype=datatype))
    cell.add(gdstk.rectangle((x0 + width, y1 - width), (x1 - width, y1), layer=layer, datatype=datatype))


def _add_ports(
    cell: gdstk.Cell,
    ports: list[Port],
    line_width_um: float,
    bbox: tuple[float, float, float, float],
) -> None:
    pad = max(6.0, line_width_um * 1.5)
    for port in ports:
        sx, sy = port.signal_xy_um
        gx, gy = port.ground_xy_um
        sig_layer, sig_dt = port.signal_layer
        gnd_layer, gnd_dt = port.ground_layer
        sig_draw_layer, sig_draw_dt = DRAW_LAYER_FOR_PIN.get(port.signal_layer, port.signal_layer)
        gnd_draw_layer, gnd_draw_dt = DRAW_LAYER_FOR_PIN.get(port.ground_layer, port.ground_layer)
        cell.add(
            gdstk.rectangle(
                (sx - pad / 2.0, sy - pad / 2.0),
                (sx + pad / 2.0, sy + pad / 2.0),
                layer=sig_draw_layer,
                datatype=sig_draw_dt,
            )
        )
        cell.add(
            gdstk.rectangle(
                (gx - pad / 2.0, gy - pad / 2.0),
                (gx + pad / 2.0, gy + pad / 2.0),
                layer=gnd_draw_layer,
                datatype=gnd_draw_dt,
            )
        )
        _add_ground_tie_to_frame(cell, (gx, gy), pad, bbox)
        cell.add(gdstk.rectangle((sx - pad / 2.0, sy - pad / 2.0), (sx + pad / 2.0, sy + pad / 2.0), layer=sig_layer, datatype=sig_dt))
        cell.add(gdstk.rectangle((gx - pad / 2.0, gy - pad / 2.0), (gx + pad / 2.0, gy + pad / 2.0), layer=gnd_layer, datatype=gnd_dt))
        _add_cadence_pin_labels(cell, port.name, (sx, sy), sig_layer, sig_dt)
        _add_cadence_pin_labels(cell, f"{port.name}_G", (gx, gy), gnd_layer, gnd_dt)


def _add_cadence_pin_labels(
    cell: gdstk.Cell,
    text: str,
    xy_um: tuple[float, float],
    layer: int,
    texttype: int,
) -> None:
    cell.add(gdstk.Label(text, xy_um, layer=layer, texttype=texttype))
    cell.add(gdstk.Label(text, xy_um, layer=layer, texttype=texttype, magnification=0.5))


def _add_ground_tie_to_frame(
    cell: gdstk.Cell,
    ground_xy_um: tuple[float, float],
    width_um: float,
    bbox: tuple[float, float, float, float],
) -> None:
    gx, gy = ground_xy_um
    x0, y0, x1, y1 = bbox
    distances = {
        "left": abs(gx - x0),
        "right": abs(x1 - gx),
        "bottom": abs(gy - y0),
        "top": abs(y1 - gy),
    }
    side = min(distances, key=distances.get)
    half = width_um / 2.0
    if side == "left":
        p0 = (min(x0, gx) - half, gy - half)
        p1 = (max(x0, gx) + half, gy + half)
    elif side == "right":
        p0 = (min(gx, x1) - half, gy - half)
        p1 = (max(gx, x1) + half, gy + half)
    elif side == "bottom":
        p0 = (gx - half, min(y0, gy) - half)
        p1 = (gx + half, max(y0, gy) + half)
    else:
        p0 = (gx - half, min(gy, y1) - half)
        p1 = (gx + half, max(gy, y1) + half)
    cell.add(gdstk.rectangle(p0, p1, layer=M5_DRAW[0], datatype=M5_DRAW[1]))


def _write_preview(path: Path, spec: dict[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    colors = {
        M5_DRAW: "#6b7280",
        M9_DRAW: "#dc2626",
        M10_DRAW: "#2563eb",
    }
    fig, ax = plt.subplots(figsize=(7, 5), dpi=180)
    x0, y0, x1, y1 = spec["bbox"]
    ax.set_xlim(x0 - 20, x1 + 20)
    ax.set_ylim(y0 - 20, y1 + 20)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(spec["name"])
    for polygon in spec["polygons"]:
        pts = polygon.points
        color = colors.get((polygon.layer, polygon.datatype), "#111827")
        ax.add_patch(MplPolygon(pts, closed=True, facecolor=color, edgecolor="black", alpha=0.65, linewidth=0.6))
    gx = [x0, x1, x1, x0, x0]
    gy = [y0, y0, y1, y1, y0]
    ax.plot(gx, gy, color="#6b7280", linewidth=1.0, linestyle="--")
    for port in spec["ports"]:
        ax.scatter([port.signal_xy_um[0]], [port.signal_xy_um[1]], color="black", s=12)
        ax.scatter([port.ground_xy_um[0]], [port.ground_xy_um[1]], color="#6b7280", s=12)
        ax.text(port.signal_xy_um[0], port.signal_xy_um[1], port.name, fontsize=7)
    ax.grid(True, linewidth=0.3, alpha=0.4)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _write_index_csv(path: Path, entries: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "stage", "metal", "port_count", "gds", "preview", "port_map"])
        writer.writeheader()
        for entry in entries:
            writer.writerow({key: entry[key] for key in writer.fieldnames})


def _write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# EMX/HFSS 校准结构包",
        "",
        "目的：在完整 8-port transformer 之前，先拆开校准金属、端口、地回流和介质/衬底。",
        "",
        f"- 频点：{manifest['frequency_grid']['start_ghz']} 到 {manifest['frequency_grid']['stop_ghz']} GHz，step {manifest['frequency_grid']['step_ghz']} GHz",
        f"- Gate：目标误差 <= {manifest['gate']['target_percent_error']}%",
        "",
        "| Structure | Stage | Metal | Ports | Purpose |",
        "|---|---|---|---:|---|",
    ]
    for item in manifest["structures"]:
        lines.append(f"| `{item['name']}` | {item['stage']} | {item['metal']} | {item['port_count']} | {item['purpose']} |")
    lines.extend([
        "",
        "执行顺序：先跑 M9/M10 straight line；直线通过后再跑 single inductor；再跑 simple 2-port transformer；最后才回完整 8-port。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
