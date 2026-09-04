"""Synthetic polygons for audit software tests, never foundry/EMX evidence."""

from pathlib import Path
import hashlib

import gdstk

from rfic_transformer_inverse_design.layout.foundry_audit import _expected_stitch_polygons
from rfic_transformer_inverse_design.layout.port_ground_metrics import PAIRS


def make_fixture(path: Path, mutate=None):
    cell = gdstk.Cell("TRANSFORMER")
    library = gdstk.Library(unit=1e-6, precision=1e-9)
    library.add(cell)
    terminals = {"P001": (-60, 20), "P002": (-60, -20), "P003": (60, 20), "P004": (60, -20),
                 "P005": (-20, 60), "P006": (-20, -60), "P007": (20, 60), "P008": (20, -60)}
    source = {
        "schema": "rfic_transformer_power_line_8port_geometry.v1",
        "enabled": True, "touchstone_mode": "signal_4_grounded_aux", "line_width_um": 5.0,
        "port_ground_overlap_um": 10.0, "labels": {},
        "port_ground_overlap_evidence": {"ports": {}},
        "process_layer_summary": {"records": {
            "primary_m10_draw": {"layer": 74, "datatype": 0},
            "secondary_m9_draw": {"layer": 39, "datatype": 60},
            "shield_m5_draw": {"layer": 35, "datatype": 0}}},
        "power_line_ground_stitches": [],
    }
    objects = {}
    for name, (ground, side, pair, role) in PAIRS.items():
        x, y = terminals[name]
        source["labels"][role] = name
        source["port_ground_overlap_evidence"]["ports"][name] = {
            "side": side, "terminal_x_um": x, "terminal_y_um": y,
            "measured_overlap_um": 12345,  # Deliberately false nominal value must be ignored.
        }
        if side in {"left", "right"}:
            p = gdstk.rectangle((min(x, 0), y-2.5), (max(x, 0), y+2.5), layer=pair[0], datatype=pair[1])
        else:
            p = gdstk.rectangle((x-2.5, min(y, 0)), (x+2.5, max(y, 0)), layer=pair[0], datatype=pair[1])
            key = "secondary_power_line" if x < 0 else "primary_power_line"
            source.setdefault(key, {"bar_layer": pair[0], "bar_datatype": pair[1]})
            end = "top" if y > 0 else "bottom"
            source[key].update({f"{end}_port_label": name, f"{end}_ground_label": ground})
            center_y = 55 if y > 0 else -55
            metal_pairs = [(35, 0), (36, 0), (37, 0), (38, 40), (39, 60)] + ([(74, 0)] if x > 0 else [])
            via_pairs = [(55, 0), (56, 0), (57, 40), (58, 40)] + ([(85, 0)] if x > 0 else [])
            source["power_line_ground_stitches"].append({
                "label": name, "ground_label": ground, "foundry_layout_enabled": True,
                "source_layer": pair[0], "target_ground_layer": 35, "landing_pad_expanded": x > 0,
                "center_um": {"x_um": x, "y_um": center_y},
                "footprint_um": {"width_um": 6.01 if x > 0 else 5.0, "height_um": 6.01 if x > 0 else 6.0},
                "metal_stack": [{"layer": a, "datatype": b} for a, b in metal_pairs],
                "via_stack": [{"layer": a, "datatype": b, "array": {
                    "size_um": 3.0 if a == 85 else 0.1, "cut_centers_um": [{"x_um": x, "y_um": center_y}]
                }} for a, b in via_pairs],
            })
        objects[name] = p
        cell.add(p, gdstk.Label(ground, (x, y), layer=135, texttype=0))
        if side in {"left", "right"}:
            cell.add(gdstk.Label(name, (x, y), layer=126 if pair[0] == 74 else 139, texttype=0))
    for a, b in [((-70,-70),(-50,70)), ((50,-70),(70,70)), ((-50,50),(50,70)), ((-50,-70),(50,-50))]:
        cell.add(gdstk.rectangle(a,b,layer=35))
    metals, vias = _expected_stitch_polygons(source, grid_um=.005)
    for polygons in metals.values(): cell.add(*polygons)
    for groups in vias.values():
        for polygons, _ in groups: cell.add(*polygons)
    if mutate: mutate(cell, objects, source)
    library.write_gds(path)
    return source, {"gds_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "ground_frame": {"manufacturing_grid_um": .005, "snapped_inner_bbox_um": [-50, -50, 50, 50]}}
