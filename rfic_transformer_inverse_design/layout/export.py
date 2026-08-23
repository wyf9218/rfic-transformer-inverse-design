"""GDS export for the fixed v1 transformer template."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..sim.emx.layout_export import EMXLayoutManifest, EMXPort
from ..sim.emx.render import render_emx_layout_preview, render_emx_port_debug_panels

from ..core.topology import TransformerSpec
from ..core.types import InductorSpec, TransformerLayoutExport, TransformerRunConfig
from ..process import parse_proc_file
from .builders import (
    _build_inductor,
    _extend_terminal,
    _ordered_draw_layers,
    _pad_from_center,
    _polygons_on_layer,
    _signal_label_point,
)
from .shields import (
    _polygon_bbox,
    _rectangular_ring,
    _shield_inner_bbox,
)

logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

EDGE_PIN_THICKNESS_UM = 0.5
EDGE_PIN_INSET_UM = 0.0
POWER_LINE_8PORT_FIXED_WIDTH_UM = 10.0
POWER_LINE_8PORT_SHIELD_OPENING_CLEARANCE_UM = POWER_LINE_8PORT_FIXED_WIDTH_UM
POWER_LINE_8PORT_PORT_GROUND_OVERLAP_UM = 10.0
POWER_LINE_8PORT_DEFAULT_HEIGHT_RATIO = 1.5
POWER_LINE_8PORT_PLACEMENT_POLICY = "coil_opening_fixed_10um_port_ground_overlap"
SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME = "signal_shield_clearance_audit.json"
POWER_LINE_8PORT_GEOMETRY_AUDIT_FILENAME = "power_line_8port_geometry.json"


@dataclass(frozen=True)
class VddBarPlacement:
    name_prefix: str
    center_x_um: float
    center_y_um: float
    width_um: float
    route_width_um: float
    half_height_um: float
    bar_layer: int
    bar_datatype: int
    pin_layer: int | None
    pin_datatype: int
    top_port_label: str | None = None
    bottom_port_label: str | None = None
    top_ground_label: str | None = None
    bottom_ground_label: str | None = None

    def resolved_top_port_label(self) -> str:
        return self.top_port_label or f"{self.name_prefix}_TOP"

    def resolved_bottom_port_label(self) -> str:
        return self.bottom_port_label or f"{self.name_prefix}_BOT"

    def resolved_top_ground_label(self) -> str:
        return self.top_ground_label or f"{self.name_prefix}_TOP_G"

    def resolved_bottom_ground_label(self) -> str:
        return self.bottom_ground_label or f"{self.name_prefix}_BOT_G"


def _resolve_export_pair(
    *,
    proc_info,
    selected_layer: int | None,
    fallback_datatype: int,
    role: str,
) -> tuple[int | None, int]:
    if selected_layer is None:
        return None, int(fallback_datatype)
    pair = None
    if role == "pin":
        pair = proc_info.preferred_pin_pair_for_layer(int(selected_layer))
    else:
        pair = proc_info.preferred_draw_pair_for_layer(int(selected_layer))
    if pair is None:
        return int(selected_layer), int(fallback_datatype)
    return int(pair.layer), int(pair.datatype)


def _process_layer_record(
    *,
    proc_info,
    layer: int | None,
    datatype: int | None,
    semantic_role: str,
) -> dict[str, Any] | None:
    if layer is None or datatype is None:
        return None
    layer_int = int(layer)
    datatype_int = int(datatype)
    matching_pair = next(
        (
            pair
            for pair in proc_info.gds_pairs_for_layer(layer_int)
            if int(pair.layer) == layer_int and int(pair.datatype) == datatype_int
        ),
        None,
    )
    conductors = proc_info.conductors_for_gds_layer(layer_int)
    conductor = conductors[0] if conductors else None
    return {
        "semantic_role": semantic_role,
        "layer": layer_int,
        "datatype": datatype_int,
        "logical_name": None if matching_pair is None else str(matching_pair.logical_name),
        "proc_pair_role": None if matching_pair is None else str(matching_pair.role),
        "conductor_name": None if conductor is None else str(conductor.name),
        "conductor_thickness_um": None if conductor is None else float(conductor.thickness_um),
        "conductor_z_bottom_um": None if conductor is None else float(conductor.z_bottom_um),
        "conductor_z_top_um": None if conductor is None else float(conductor.z_top_um),
        "proc_summary": proc_info.summary_for_gds_layer(layer_int),
    }


def _process_layer_summary(
    *,
    proc_info,
    primary_draw_layer: int | None,
    primary_draw_datatype: int | None,
    primary_pin_layer: int | None,
    primary_pin_datatype: int | None,
    secondary_draw_layer: int | None,
    secondary_draw_datatype: int | None,
    secondary_pin_layer: int | None,
    secondary_pin_datatype: int | None,
    shield_draw_layer: int | None,
    shield_draw_datatype: int | None,
    shield_pin_layer: int | None,
    shield_pin_datatype: int | None,
) -> dict[str, Any]:
    records = {
        "primary_m10_draw": _process_layer_record(
            proc_info=proc_info,
            layer=primary_draw_layer,
            datatype=primary_draw_datatype,
            semantic_role="primary winding / M10 draw",
        ),
        "primary_m10_pin": _process_layer_record(
            proc_info=proc_info,
            layer=primary_pin_layer,
            datatype=primary_pin_datatype,
            semantic_role="primary winding / M10 pin",
        ),
        "secondary_m9_draw": _process_layer_record(
            proc_info=proc_info,
            layer=secondary_draw_layer,
            datatype=secondary_draw_datatype,
            semantic_role="secondary winding / M9 draw",
        ),
        "secondary_m9_pin": _process_layer_record(
            proc_info=proc_info,
            layer=secondary_pin_layer,
            datatype=secondary_pin_datatype,
            semantic_role="secondary winding / M9 pin",
        ),
        "shield_m5_draw": _process_layer_record(
            proc_info=proc_info,
            layer=shield_draw_layer,
            datatype=shield_draw_datatype,
            semantic_role="ground shield / M5 draw",
        ),
        "shield_m5_pin": _process_layer_record(
            proc_info=proc_info,
            layer=shield_pin_layer,
            datatype=shield_pin_datatype,
            semantic_role="ground shield / M5 pin",
        ),
    }
    return {
        "schema": "rfic_transformer_process_layer_summary.v1",
        "process_file": str(proc_info.path),
        "records": {key: value for key, value in records.items() if value is not None},
    }


def _proc_pair_dict(proc_info, *, layer: int, datatype: int, semantic_role: str) -> dict[str, Any]:
    record = _process_layer_record(
        proc_info=proc_info,
        layer=int(layer),
        datatype=int(datatype),
        semantic_role=semantic_role,
    )
    return {} if record is None else record


def _preferred_pair_for_layer(proc_info, *, layer: int, fallback_datatype: int, role: str) -> tuple[int, int]:
    if role == "pin":
        pair = proc_info.preferred_pin_pair_for_layer(int(layer))
    else:
        pair = proc_info.preferred_draw_pair_for_layer(int(layer))
    if pair is None:
        return int(layer), int(fallback_datatype)
    return int(pair.layer), int(pair.datatype)


def _add_port_pin(
    *,
    cell,
    center: tuple[float, float],
    width_um: float,
    height_um: float,
    layer: int | None,
    datatype: int,
    ) -> None:
    import gdstk

    if layer is None:
        return
    half_w = 0.5 * float(width_um)
    half_h = 0.5 * float(height_um)
    cell.add(
        gdstk.rectangle(
            (float(center[0]) - half_w, float(center[1]) - half_h),
            (float(center[0]) + half_w, float(center[1]) + half_h),
            layer=int(layer),
            datatype=int(datatype),
        )
    )


def _add_ground_stitch_rectangle(
    *,
    cell,
    center: tuple[float, float],
    width_um: float,
    height_um: float,
    layer: int,
    datatype: int,
) -> None:
    import gdstk

    half_w = 0.5 * float(width_um)
    half_h = 0.5 * float(height_um)
    cell.add(
        gdstk.rectangle(
            (float(center[0]) - half_w, float(center[1]) - half_h),
            (float(center[0]) + half_w, float(center[1]) + half_h),
            layer=int(layer),
            datatype=int(datatype),
        )
    )


def _add_power_line_ground_stitch_stack(
    *,
    cell,
    proc_info,
    label: str,
    ground_label: str,
    center: tuple[float, float],
    footprint_um: tuple[float, float],
    source_layer: int,
    source_datatype: int,
    target_ground_layer: int | None,
    fallback_datatype: int,
) -> dict[str, Any]:
    """Draw and record the local metal/via stack tying a power-line endpoint to M5."""

    source_metal = proc_info.metal_number_for_gds_layer(int(source_layer))
    target_layer = int(target_ground_layer) if target_ground_layer is not None else None
    target_metal = None if target_layer is None else proc_info.metal_number_for_gds_layer(target_layer)
    if source_metal is None:
        source_metal = 10 if int(source_layer) == 74 else 9 if int(source_layer) == 39 else None
    if target_metal is None:
        target_metal = 5
    if source_metal is None:
        raise ValueError(f"could not infer power-line source metal for layer {source_layer}")
    if int(source_metal) < int(target_metal):
        raise ValueError(f"power-line ground stitch source metal{source_metal} is below target metal{target_metal}")

    width_um, height_um = (float(footprint_um[0]), float(footprint_um[1]))
    metal_records: list[dict[str, Any]] = []
    via_records: list[dict[str, Any]] = []
    used_layers: list[int] = []
    for metal_number in range(int(target_metal), int(source_metal) + 1):
        metal_layer = proc_info.gds_layer_for_metal_number(metal_number)
        if metal_layer is None:
            continue
        layer, datatype = _preferred_pair_for_layer(
            proc_info,
            layer=int(metal_layer),
            fallback_datatype=int(fallback_datatype),
            role="drawing",
        )
        _add_ground_stitch_rectangle(
            cell=cell,
            center=center,
            width_um=width_um,
            height_um=height_um,
            layer=layer,
            datatype=datatype,
        )
        used_layers.append(int(layer))
        metal_records.append(
            {
                "metal_number": int(metal_number),
                "layer": int(layer),
                "datatype": int(datatype),
                "process": _proc_pair_dict(
                    proc_info,
                    layer=int(layer),
                    datatype=int(datatype),
                    semantic_role=f"{label} ground stitch metal{metal_number}",
                ),
            }
        )
    for via_number in range(int(target_metal), int(source_metal)):
        via_layer = proc_info.gds_layer_for_via_number(via_number)
        if via_layer is None:
            continue
        layer, datatype = _preferred_pair_for_layer(
            proc_info,
            layer=int(via_layer),
            fallback_datatype=int(fallback_datatype),
            role="drawing",
        )
        _add_ground_stitch_rectangle(
            cell=cell,
            center=center,
            width_um=width_um,
            height_um=height_um,
            layer=layer,
            datatype=datatype,
        )
        used_layers.append(int(layer))
        via_records.append(
            {
                "via_number": int(via_number),
                "layer": int(layer),
                "datatype": int(datatype),
                "connects_metal": [int(via_number), int(via_number) + 1],
                "process": _proc_pair_dict(
                    proc_info,
                    layer=int(layer),
                    datatype=int(datatype),
                    semantic_role=f"{label} ground stitch via{via_number}",
                ),
            }
        )
    return {
        "label": str(label),
        "ground_label": str(ground_label),
        "center_um": {"x_um": float(center[0]), "y_um": float(center[1])},
        "footprint_um": {"width_um": width_um, "height_um": height_um},
        "source_layer": int(source_layer),
        "source_datatype": int(source_datatype),
        "source_metal": f"metal{int(source_metal)}",
        "target_ground_layer": None if target_ground_layer is None else int(target_ground_layer),
        "target_ground_metal": f"metal{int(target_metal)}",
        "metal_stack": metal_records,
        "via_stack": via_records,
        "used_layers": sorted(dict.fromkeys(used_layers)),
    }


def _add_edge_port_pin(
    *,
    cell,
    edge_point: tuple[float, float],
    side: str,
    width_um: float,
    height_um: float,
    layer: int | None,
    datatype: int,
) -> None:
    import gdstk

    if layer is None:
        return
    x_um, y_um = float(edge_point[0]), float(edge_point[1])
    half_w = 0.5 * float(width_um)
    half_h = 0.5 * float(height_um)
    if side not in ("left", "right"):
        raise ValueError(f"Unsupported edge pin side: {side}")
    cell.add(
        gdstk.rectangle(
            (x_um - half_w, y_um - half_h),
            (x_um + half_w, y_um + half_h),
            layer=int(layer),
            datatype=int(datatype),
        )
    )


def _port_pin_dimensions(trace_width_um: float) -> tuple[float, float]:
    trace_width_um = float(trace_width_um)
    internal_side_um = max(4.0, trace_width_um * 0.6)
    return internal_side_um, internal_side_um


def _edge_signal_pin_dimensions(trace_width_um: float) -> tuple[float, float]:
    trace_width_um = float(trace_width_um)
    return 3.0, max(0.5, trace_width_um - 2.0)


def _edge_overlap_pin_dimensions(trace_width_um: float) -> tuple[float, float]:
    return EDGE_PIN_THICKNESS_UM, float(trace_width_um)


def _vdd_edge_pin_dimensions(trace_width_um: float) -> tuple[float, float]:
    return float(trace_width_um), EDGE_PIN_THICKNESS_UM


def _offset_pin_center_from_edge(
    *,
    edge_point: tuple[float, float],
    side: str,
    pin_width_um: float,
    pin_height_um: float,
    inset_um: float = EDGE_PIN_INSET_UM,
) -> tuple[float, float]:
    x_um, y_um = map(float, edge_point)
    pin_width_um = float(pin_width_um)
    pin_height_um = float(pin_height_um)
    inset_um = float(inset_um)
    if side == "left":
        return (x_um + inset_um + 0.5 * pin_width_um, y_um)
    if side == "right":
        return (x_um - inset_um - 0.5 * pin_width_um, y_um)
    if side == "top":
        return (x_um, y_um - inset_um - 0.5 * pin_height_um)
    if side == "bottom":
        return (x_um, y_um + inset_um + 0.5 * pin_height_um)
    raise ValueError(f"Unsupported edge side: {side}")


def _unique_layers(*layers: int | None) -> tuple[int, ...]:
    result: list[int] = []
    seen: set[int] = set()
    for layer in layers:
        if layer is None:
            continue
        layer_int = int(layer)
        if layer_int in seen:
            continue
        seen.add(layer_int)
        result.append(layer_int)
    return tuple(result)


def _unique_layer_datatypes(*pairs: tuple[int | None, int] | None) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    seen: set[int] = set()
    for pair in pairs:
        if pair is None:
            continue
        layer, datatype = pair
        if layer is None:
            continue
        layer_int = int(layer)
        if layer_int in seen:
            continue
        seen.add(layer_int)
        result.append((layer_int, int(datatype)))
    return tuple(result)


def _signal_shield_clearance_um(*trace_widths_um: float) -> float:
    trace_rule = max((max(0.5, min(2.0, 0.10 * float(width))) for width in trace_widths_um), default=0.5)
    return max(2.0, trace_rule)


def _cell_polygons_on_pair(cell, *, layer: int | None, datatype: int | None) -> tuple[object, ...]:
    if layer is None or datatype is None:
        return tuple()
    return tuple(
        poly
        for poly in cell.polygons
        if int(poly.layer) == int(layer) and int(poly.datatype) == int(datatype)
    )


def _shield_port_overlap_windows(
    *,
    inner_bbox: tuple[float, float, float, float],
    shield_width_um: float,
    side: str,
    terminals: tuple[tuple[float, float], ...],
    trace_width_um: float,
    layer: int,
    datatype: int,
) -> tuple[object, ...]:
    import gdstk

    inner_min_x, inner_min_y, inner_max_x, inner_max_y = inner_bbox
    shield_width_um = float(shield_width_um)
    trace_half_um = 0.5 * float(trace_width_um)
    windows = []
    if side == "left":
        x0, x1 = float(inner_min_x) - shield_width_um, float(inner_min_x)
        for _x, y in terminals:
            y = float(y)
            windows.append(gdstk.rectangle((x0, y - trace_half_um), (x1, y + trace_half_um), layer=layer, datatype=datatype))
    elif side == "right":
        x0, x1 = float(inner_max_x), float(inner_max_x) + shield_width_um
        for _x, y in terminals:
            y = float(y)
            windows.append(gdstk.rectangle((x0, y - trace_half_um), (x1, y + trace_half_um), layer=layer, datatype=datatype))
    elif side == "top":
        y0, y1 = float(inner_max_y), float(inner_max_y) + shield_width_um
        for x, _y in terminals:
            x = float(x)
            windows.append(gdstk.rectangle((x - trace_half_um, y0), (x + trace_half_um, y1), layer=layer, datatype=datatype))
    elif side == "bottom":
        y0, y1 = float(inner_min_y) - shield_width_um, float(inner_min_y)
        for x, _y in terminals:
            x = float(x)
            windows.append(gdstk.rectangle((x - trace_half_um, y0), (x + trace_half_um, y1), layer=layer, datatype=datatype))
    else:
        raise ValueError(f"Unsupported shield port side: {side}")
    return tuple(windows)


def _offset_polygons(polygons, *, margin_um: float, layer: int, datatype: int) -> tuple[object, ...]:
    import gdstk

    polygons = tuple(polygons)
    if not polygons:
        return tuple()
    if float(margin_um) <= 0.0:
        return polygons
    expanded = gdstk.offset(
        list(polygons),
        float(margin_um),
        join="miter",
        tolerance=2.0,
        precision=1.0e-4,
        use_union=True,
        layer=int(layer),
        datatype=int(datatype),
    )
    return tuple(expanded if expanded is not None else polygons)


def _boolean_polygons(left, right, operation: str, *, layer: int, datatype: int) -> tuple[object, ...]:
    import gdstk

    left = tuple(left)
    right = tuple(right)
    if not left:
        return tuple()
    result = gdstk.boolean(list(left), list(right), operation, layer=int(layer), datatype=int(datatype))
    return tuple(result if result is not None else tuple())


def _polygon_area_um2(polygons) -> float:
    return float(sum(abs(poly.area()) for poly in polygons))


def _polygon_bbox_summary(polygons, *, limit: int = 3) -> list[dict[str, float]]:
    bboxes: list[dict[str, float]] = []
    for poly in polygons:
        box = poly.bounding_box()
        if box is None:
            continue
        bboxes.append(
            {
                "min_x_um": float(box[0][0]),
                "min_y_um": float(box[0][1]),
                "max_x_um": float(box[1][0]),
                "max_y_um": float(box[1][1]),
            }
        )
        if len(bboxes) >= int(limit):
            break
    return bboxes


def _format_bbox_summary(bboxes: list[dict[str, float]]) -> str:
    return "; ".join(
        f"({box['min_x_um']:.3f},{box['min_y_um']:.3f})-({box['max_x_um']:.3f},{box['max_y_um']:.3f})"
        for box in bboxes[:3]
    )


def _signal_shield_clearance_report(
    *,
    cell,
    signal_name: str,
    signal_layer: int | None,
    signal_datatype: int | None,
    shield_layer: int | None,
    shield_datatype: int | None,
    allowed_overlap_windows: tuple[object, ...],
    trace_width_um: float,
) -> dict[str, Any]:
    signal_polygons = _cell_polygons_on_pair(cell, layer=signal_layer, datatype=signal_datatype)
    shield_polygons = _cell_polygons_on_pair(cell, layer=shield_layer, datatype=shield_datatype)
    clearance_um = _signal_shield_clearance_um(trace_width_um)
    report: dict[str, Any] = {
        "signal_name": signal_name,
        "signal_layer": None if signal_layer is None else int(signal_layer),
        "signal_datatype": None if signal_datatype is None else int(signal_datatype),
        "shield_layer": None if shield_layer is None else int(shield_layer),
        "shield_datatype": None if shield_datatype is None else int(shield_datatype),
        "trace_width_um": float(trace_width_um),
        "required_clearance_um": float(clearance_um),
        "signal_polygon_count": int(len(signal_polygons)),
        "shield_polygon_count": int(len(shield_polygons)),
        "allowed_overlap_window_count": int(len(allowed_overlap_windows)),
        "direct_signal_shield_overlap_area_um2": 0.0,
        "signal_shield_clearance_violation_area_um2": 0.0,
        "direct_signal_shield_overlap_bboxes": [],
        "signal_shield_clearance_violation_bboxes": [],
    }
    if not signal_polygons or not shield_polygons:
        report["status"] = "missing_signal_or_shield_polygons"
        return report

    check_layer = int(signal_layer if signal_layer is not None else 0)
    check_datatype = int(signal_datatype if signal_datatype is not None else 0)

    direct_overlap = _boolean_polygons(
        signal_polygons,
        shield_polygons,
        "and",
        layer=check_layer,
        datatype=check_datatype,
    )
    direct_illegal = (
        _boolean_polygons(direct_overlap, allowed_overlap_windows, "not", layer=check_layer, datatype=check_datatype)
        if allowed_overlap_windows
        else direct_overlap
    )
    direct_area = _polygon_area_um2(direct_illegal)
    direct_bboxes = _polygon_bbox_summary(direct_illegal)
    report["direct_signal_shield_overlap_area_um2"] = direct_area
    report["direct_signal_shield_overlap_bboxes"] = direct_bboxes

    expanded_signal = _offset_polygons(
        signal_polygons,
        margin_um=0.5 * clearance_um,
        layer=check_layer,
        datatype=check_datatype,
    )
    expanded_shield = _offset_polygons(
        shield_polygons,
        margin_um=0.5 * clearance_um,
        layer=check_layer,
        datatype=check_datatype,
    )
    overlap = _boolean_polygons(expanded_signal, expanded_shield, "and", layer=check_layer, datatype=check_datatype)
    allowed = _offset_polygons(
        allowed_overlap_windows,
        margin_um=clearance_um,
        layer=check_layer,
        datatype=check_datatype,
    )
    illegal = _boolean_polygons(overlap, allowed, "not", layer=check_layer, datatype=check_datatype) if allowed else overlap
    illegal_area = _polygon_area_um2(illegal)
    illegal_bboxes = _polygon_bbox_summary(illegal)
    report["signal_shield_clearance_violation_area_um2"] = illegal_area
    report["signal_shield_clearance_violation_bboxes"] = illegal_bboxes
    report["status"] = (
        "pass_signal_to_shield_clearance"
        if direct_area <= 1.0e-6 and illegal_area <= 1.0e-6
        else "reject_signal_to_shield_clearance"
    )
    return report


def _signal_shield_clearance_errors_from_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    signal_name = str(report.get("signal_name") or "signal")
    direct_area = float(report.get("direct_signal_shield_overlap_area_um2") or 0.0)
    violation_area = float(report.get("signal_shield_clearance_violation_area_um2") or 0.0)
    clearance_um = float(report.get("required_clearance_um") or 0.0)
    if direct_area > 1.0e-6:
        bbox_summary = _format_bbox_summary(list(report.get("direct_signal_shield_overlap_bboxes") or []))
        errors.append(
            "gdstk: "
            f"{signal_name} signal-to-shield direct overlap "
            f"(outside explicit port windows, illegal area {direct_area:.3f} um^2"
            + (f", bbox {bbox_summary}" if bbox_summary else "")
            + ")"
        )
    if violation_area > 1.0e-6:
        bbox_summary = _format_bbox_summary(list(report.get("signal_shield_clearance_violation_bboxes") or []))
        errors.append(
            "gdstk: "
            f"{signal_name} signal-to-shield clearance violation "
            f"(required >= {clearance_um:.3f} um outside explicit port windows, "
            f"illegal area {violation_area:.3f} um^2"
            + (f", bbox {bbox_summary}" if bbox_summary else "")
            + ")"
        )
    return errors


def _signal_shield_clearance_errors(**kwargs) -> list[str]:
    return _signal_shield_clearance_errors_from_report(_signal_shield_clearance_report(**kwargs))


def _build_signal_shield_clearance_audit(records: list[dict[str, Any]], *, enabled: bool, reason: str | None = None) -> dict[str, Any]:
    direct_area = float(sum(float(record.get("direct_signal_shield_overlap_area_um2") or 0.0) for record in records))
    violation_area = float(sum(float(record.get("signal_shield_clearance_violation_area_um2") or 0.0) for record in records))
    if not enabled:
        status = "not_applicable"
    elif any(record.get("status") == "missing_signal_or_shield_polygons" for record in records):
        status = "missing_signal_or_shield_polygons"
    elif direct_area <= 1.0e-6 and violation_area <= 1.0e-6:
        status = "pass_signal_to_shield_clearance"
    else:
        status = "reject_signal_to_shield_clearance"
    return {
        "schema": "rfic_transformer_signal_shield_clearance_audit.v1",
        "enabled": bool(enabled),
        "reason": reason,
        "status": status,
        "record_count": int(len(records)),
        "direct_signal_shield_overlap_area_um2": direct_area,
        "signal_shield_clearance_violation_area_um2": violation_area,
        "records": records,
    }


def _write_signal_shield_clearance_audit(out_dir: Path, audit: dict[str, Any]) -> Path:
    path = Path(out_dir) / SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME
    path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return path


def _power_line_8port_label_map(run_config: TransformerRunConfig) -> dict[str, str]:
    port_map = tuple(str(item) for item in run_config.emx.power_line_8port.port_map)
    signal_only_s4p = _power_line_signal_only_s4p(run_config)
    expected_port_count = 4 if signal_only_s4p else 8
    if len(port_map) != expected_port_count:
        raise ValueError(f"power_line_8port.port_map must contain exactly {expected_port_count} labels")
    role_labels = dict(run_config.emx.power_line_8port.role_labels)
    expected_roles = {
        "left_power_top",
        "left_power_bottom",
        "primary_top",
        "primary_bottom",
        "secondary_top",
        "secondary_bottom",
        "right_power_top",
        "right_power_bottom",
    }
    if not role_labels:
        raise ValueError("power_line_8port.role_labels must explicitly map every 8-port physical role")
    if set(role_labels) != expected_roles:
        raise ValueError("power_line_8port.role_labels must contain every 8-port physical role exactly once")
    if signal_only_s4p:
        signal_roles = {"primary_top", "primary_bottom", "secondary_top", "secondary_bottom"}
        if {role_labels[role] for role in signal_roles} != set(port_map):
            raise ValueError("power_line_8port signal role labels must match the 4 exported .s4p labels")
    elif set(role_labels.values()) != set(port_map):
        raise ValueError("power_line_8port.role_labels values must match port_map labels")
    return role_labels


def _power_line_signal_only_s4p(run_config: TransformerRunConfig) -> bool:
    spec = run_config.emx.power_line_8port
    return bool(spec.enabled and spec.touchstone_mode == "signal_4_grounded_aux")


def _validate_power_line_8port_layout_inputs(transformer, run_config: TransformerRunConfig) -> None:
    if not run_config.emx.power_line_8port.enabled:
        return
    errors: list[str] = []
    if not run_config.emx.uses_shield_as_port_ground():
        errors.append("power_line_8port requires single_ended_shield_grounded port mode")
    if not transformer.shield.enabled:
        errors.append("power_line_8port requires transformer.shield.enabled")
    expected_power_line_layers = {
        "primary": int(run_config.emx.ap_layer),
        "secondary": int(run_config.emx.m9_layer),
    }
    for role_name, inductor in (("primary", transformer.primary), ("secondary", transformer.secondary)):
        if not inductor.center_tap:
            errors.append(f"power_line_8port requires {role_name}_center_tap=true")
        if inductor.vdd_bar is None or not inductor.vdd_bar.enabled:
            errors.append(f"power_line_8port requires {role_name}_vdd_bar.enabled=true")
        elif inductor.vdd_bar.bar_layer is None:
            errors.append(f"power_line_8port requires {role_name}_vdd_bar.bar_layer")
        elif int(inductor.vdd_bar.bar_layer) != expected_power_line_layers[role_name]:
            errors.append(
                f"power_line_8port requires {role_name}_vdd_bar.bar_layer to match "
                f"{role_name} coil layer {expected_power_line_layers[role_name]}"
            )
    primary_width_um = float(transformer.primary.trace_width_um)
    secondary_width_um = float(transformer.secondary.trace_width_um)
    if abs(primary_width_um - secondary_width_um) > 1.0e-9:
        errors.append(
            "power_line_8port requires one synchronized line_width_um for M10/M9 coils "
            f"({primary_width_um} != {secondary_width_um})"
        )
    if errors:
        raise ValueError("power_line_8port layout requirements not met: " + "; ".join(errors))


def _with_synced_power_line_width(inductor: InductorSpec, *, enabled: bool) -> InductorSpec:
    if not enabled or inductor.vdd_bar is None or not inductor.vdd_bar.enabled:
        return inductor
    return replace(
        inductor,
        fixed=replace(
            inductor.fixed,
            vdd_bar=replace(inductor.vdd_bar, width_um=float(inductor.trace_width_um)),
        ),
    )


def _power_line_fixed_width_um() -> float:
    return float(POWER_LINE_8PORT_FIXED_WIDTH_UM)


def _power_line_shield_opening_clearance_um() -> float:
    return float(POWER_LINE_8PORT_SHIELD_OPENING_CLEARANCE_UM)


def _power_line_port_ground_overlap_um() -> float:
    return float(POWER_LINE_8PORT_PORT_GROUND_OVERLAP_UM)


def _shield_ground_frame_width_um(transformer, run_config: TransformerRunConfig) -> float:
    width_um = float(0.0 if transformer.shield.width_um is None else transformer.shield.width_um)
    if not run_config.emx.power_line_8port.enabled:
        return width_um
    margin_um = float(0.0 if transformer.shield.margin_um is None else transformer.shield.margin_um)
    return max(width_um, margin_um)


def _power_line_height_ratio(run_config: TransformerRunConfig) -> float:
    return float(run_config.emx.power_line_8port.vertical_length_diameter_ratio)


def _power_line_max_outer_height_um(transformer) -> float:
    return max(
        float(transformer.primary.outer_height_um),
        float(transformer.secondary.outer_height_um),
    )


def _power_line_vertical_length_um(transformer, run_config: TransformerRunConfig) -> float:
    return _power_line_max_outer_height_um(transformer) * _power_line_height_ratio(run_config)


def _write_power_line_8port_geometry_audit(
    out_dir: Path,
    *,
    enabled: bool,
    reason: str | None = None,
    touchstone_mode: str | None = None,
    placement_policy: str | None = None,
    labels: dict[str, str] | None = None,
    auxiliary_ground_reference_labels: tuple[str, ...] = tuple(),
    power_line_ground_stitches: tuple[dict[str, Any], ...] = tuple(),
    vertical_length_um: float | None = None,
    max_outer_height_um: float | None = None,
    vertical_length_diameter_ratio: float | None = None,
    line_width_um: float | None = None,
    bridge_width_um: float | None = None,
    primary_bar: VddBarPlacement | None = None,
    secondary_bar: VddBarPlacement | None = None,
    primary_terminals=None,
    secondary_terminals=None,
    primary_coil_center_x_um: float = 0.0,
    primary_coil_outer_width_um: float = 0.0,
    secondary_coil_center_x_um: float = 0.0,
    secondary_coil_outer_width_um: float = 0.0,
    signal_conductor_bbox_um: tuple[float, float, float, float] | None = None,
    grounded_conductor_bbox_um: tuple[float, float, float, float] | None = None,
    required_shield_inner_bbox_um: tuple[float, float, float, float] | None = None,
    shield_opening_clearance_um: float | None = None,
    port_ground_overlap_um: float | None = None,
    port_ground_overlap_evidence: dict[str, Any] | None = None,
    ground_frame_enclosure_clearance_um: float | None = None,
    shield_inner_bbox_um: tuple[float, float, float, float] | None = None,
    shield_outer_bbox_um: tuple[float, float, float, float] | None = None,
    ground_frame_width_um: float | None = None,
    ground_frame_policy: str | None = None,
    process_layer_summary: dict[str, Any] | None = None,
) -> Path:
    path = Path(out_dir) / POWER_LINE_8PORT_GEOMETRY_AUDIT_FILENAME
    payload: dict[str, Any] = {
        "schema": "rfic_transformer_power_line_8port_geometry.v1",
        "enabled": bool(enabled),
        "reason": reason,
        "touchstone_mode": touchstone_mode,
        "placement_policy": placement_policy,
        "labels": labels or {},
        "auxiliary_ground_reference_labels": list(auxiliary_ground_reference_labels),
        "power_line_ground_stitches": list(power_line_ground_stitches),
        "vertical_length_um": None if vertical_length_um is None else float(vertical_length_um),
        "max_outer_height_um": None if max_outer_height_um is None else float(max_outer_height_um),
        "vertical_length_diameter_ratio": None
        if vertical_length_diameter_ratio is None
        else float(vertical_length_diameter_ratio),
        "expected_vertical_length_um": (
            None
            if max_outer_height_um is None or vertical_length_diameter_ratio is None
            else float(max_outer_height_um) * float(vertical_length_diameter_ratio)
        ),
        "bridge_width_um": None if bridge_width_um is None else float(bridge_width_um),
        "line_width_um": None if line_width_um is None else float(line_width_um),
        "primary_power_line": _vdd_bar_audit_dict(primary_bar),
        "secondary_power_line": _vdd_bar_audit_dict(secondary_bar),
        "primary_power_line_clearance": _power_line_bar_clearance_audit_dict(
            primary_bar,
            own_coil_center_x_um=primary_coil_center_x_um,
            own_coil_outer_width_um=primary_coil_outer_width_um,
            other_coil_center_x_um=secondary_coil_center_x_um,
            other_coil_outer_width_um=secondary_coil_outer_width_um,
        ),
        "secondary_power_line_clearance": _power_line_bar_clearance_audit_dict(
            secondary_bar,
            own_coil_center_x_um=secondary_coil_center_x_um,
            own_coil_outer_width_um=secondary_coil_outer_width_um,
            other_coil_center_x_um=primary_coil_center_x_um,
            other_coil_outer_width_um=primary_coil_outer_width_um,
        ),
        "primary_bridge": _power_line_bridge_audit_dict(
            terminals=primary_terminals,
            power_line=primary_bar,
            bridge_width_um=bridge_width_um,
            coil_center_x_um=primary_coil_center_x_um,
            coil_outer_width_um=primary_coil_outer_width_um,
        ),
        "secondary_bridge": _power_line_bridge_audit_dict(
            terminals=secondary_terminals,
            power_line=secondary_bar,
            bridge_width_um=bridge_width_um,
            coil_center_x_um=secondary_coil_center_x_um,
            coil_outer_width_um=secondary_coil_outer_width_um,
        ),
        "signal_conductor_bbox_um": _bbox_audit_dict(signal_conductor_bbox_um),
        "grounded_conductor_bbox_um": _bbox_audit_dict(grounded_conductor_bbox_um),
        "required_shield_inner_bbox_um": _bbox_audit_dict(required_shield_inner_bbox_um),
        "shield_opening_clearance_um": (
            None if shield_opening_clearance_um is None else float(shield_opening_clearance_um)
        ),
        "port_ground_overlap_um": None if port_ground_overlap_um is None else float(port_ground_overlap_um),
        "port_ground_overlap_evidence": port_ground_overlap_evidence,
        "ground_frame_enclosure_clearance_um": (
            None if ground_frame_enclosure_clearance_um is None else float(ground_frame_enclosure_clearance_um)
        ),
        "shield_inner_bbox_um": _bbox_audit_dict(shield_inner_bbox_um),
        "shield_outer_bbox_um": _bbox_audit_dict(shield_outer_bbox_um),
        "ground_frame_width_um": None if ground_frame_width_um is None else float(ground_frame_width_um),
        "ground_frame_policy": ground_frame_policy,
        "process_layer_summary": process_layer_summary,
    }
    physical_left, physical_right, primary_is_left = _power_line_physical_left_right_audit(
        primary_bar,
        secondary_bar,
    )
    payload["physical_left_power_line"] = physical_left
    payload["physical_right_power_line"] = physical_right
    payload["primary_is_physical_left"] = primary_is_left
    payload["center_tap_topology"] = _power_line_center_tap_topology(primary_bar, secondary_bar)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _bbox_audit_dict(bbox: tuple[float, float, float, float] | None) -> dict[str, float] | None:
    if bbox is None:
        return None
    return {
        "min_x_um": float(bbox[0]),
        "min_y_um": float(bbox[1]),
        "max_x_um": float(bbox[2]),
        "max_y_um": float(bbox[3]),
    }


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    margin_um: float,
) -> tuple[float, float, float, float]:
    margin = max(0.0, float(margin_um))
    return (
        float(bbox[0]) - margin,
        float(bbox[1]) - margin,
        float(bbox[2]) + margin,
        float(bbox[3]) + margin,
    )


def _ground_frame_width_to_enclose_bbox(
    *,
    inner_bbox: tuple[float, float, float, float],
    required_bbox: tuple[float, float, float, float],
    base_width_um: float,
    clearance_um: float = EDGE_PIN_THICKNESS_UM,
) -> float:
    """Return a uniform ground-frame width that encloses every port conductor."""

    inner_min_x, inner_min_y, inner_max_x, inner_max_y = (float(value) for value in inner_bbox)
    req_min_x, req_min_y, req_max_x, req_max_y = (float(value) for value in required_bbox)
    clearance = max(0.0, float(clearance_um))
    required_width = max(
        float(base_width_um),
        inner_min_x - req_min_x + clearance,
        inner_min_y - req_min_y + clearance,
        req_max_x - inner_max_x + clearance,
        req_max_y - inner_max_y + clearance,
    )
    return max(0.0, float(required_width))


def _power_line_8port_opening_base_bbox(
    *,
    primary_center_x_um: float,
    primary_outer_width_um: float,
    primary_outer_height_um: float,
    secondary_center_x_um: float,
    secondary_outer_width_um: float,
    secondary_outer_height_um: float,
    primary_bar: VddBarPlacement | None,
    secondary_bar: VddBarPlacement | None,
    primary_terminals,
    secondary_terminals,
) -> tuple[float, float, float, float]:
    """Opening box for coupled coils plus center bridges, excluding power-port ends."""

    primary_half_width = 0.5 * float(primary_outer_width_um)
    secondary_half_width = 0.5 * float(secondary_outer_width_um)
    primary_half_height = 0.5 * float(primary_outer_height_um)
    secondary_half_height = 0.5 * float(secondary_outer_height_um)
    x_values = [
        float(primary_center_x_um) - primary_half_width,
        float(primary_center_x_um) + primary_half_width,
        float(secondary_center_x_um) - secondary_half_width,
        float(secondary_center_x_um) + secondary_half_width,
    ]
    y_values = [
        -primary_half_height,
        primary_half_height,
        -secondary_half_height,
        secondary_half_height,
    ]
    for placement in (primary_bar, secondary_bar):
        if placement is None:
            continue
        half_width = 0.5 * float(placement.width_um)
        x_values.extend([float(placement.center_x_um) - half_width, float(placement.center_x_um) + half_width])
    for terminals in (primary_terminals, secondary_terminals):
        if terminals is None:
            continue
        for point in (getattr(terminals, "center_tap_anchor", None), getattr(terminals, "center_tap", None)):
            if point is None:
                continue
            x_values.append(float(point[0]))
    return (min(x_values), min(y_values), max(x_values), max(y_values))


def _power_line_8port_opening_bbox_with_fixed_port_overlap(
    *,
    central_bbox: tuple[float, float, float, float],
    signal_terminals: dict[str, tuple[float, float]],
    primary_bar: VddBarPlacement | None,
    secondary_bar: VddBarPlacement | None,
    port_ground_overlap_um: float,
) -> tuple[float, float, float, float]:
    """White opening bbox with every port conductor protruding into ground by a fixed amount."""

    min_x, min_y, max_x, max_y = (float(value) for value in central_bbox)
    overlap = max(0.0, float(port_ground_overlap_um))
    left_signal_x = min(float(signal_terminals["P001"][0]), float(signal_terminals["P002"][0]))
    right_signal_x = max(float(signal_terminals["P003"][0]), float(signal_terminals["P004"][0]))
    min_x = min(min_x, left_signal_x + overlap)
    max_x = max(max_x, right_signal_x - overlap)
    for placement in (primary_bar, secondary_bar):
        if placement is None:
            continue
        top_y = float(placement.center_y_um) + float(placement.half_height_um)
        bottom_y = float(placement.center_y_um) - float(placement.half_height_um)
        min_y = min(min_y, bottom_y + overlap)
        max_y = max(max_y, top_y - overlap)
    return (min_x, min_y, max_x, max_y)


def _power_line_8port_grounded_conductor_bbox(
    *,
    signal_terminals: dict[str, tuple[float, float]],
    primary_bar: VddBarPlacement | None,
    secondary_bar: VddBarPlacement | None,
) -> tuple[float, float, float, float]:
    x_values = [float(point[0]) for point in signal_terminals.values()]
    y_values = [float(point[1]) for point in signal_terminals.values()]
    for placement in (primary_bar, secondary_bar):
        if placement is None:
            continue
        half_width = 0.5 * float(placement.width_um)
        top_y = float(placement.center_y_um) + float(placement.half_height_um)
        bottom_y = float(placement.center_y_um) - float(placement.half_height_um)
        x_values.extend([float(placement.center_x_um) - half_width, float(placement.center_x_um) + half_width])
        y_values.extend([bottom_y, top_y])
    return (min(x_values), min(y_values), max(x_values), max(y_values))


def _power_line_8port_port_overlap_evidence(
    *,
    signal_terminals: dict[str, tuple[float, float]],
    signal_external_labels: dict[str, str],
    primary_bar: VddBarPlacement | None,
    secondary_bar: VddBarPlacement | None,
    shield_inner_bbox: tuple[float, float, float, float] | None,
    expected_overlap_um: float,
) -> dict[str, Any] | None:
    if shield_inner_bbox is None:
        return None
    inner_min_x, inner_min_y, inner_max_x, inner_max_y = (float(value) for value in shield_inner_bbox)
    expected = float(expected_overlap_um)
    evidence: dict[str, Any] = {
        "expected_um": expected,
        "ports": {},
    }
    for port_name in ("P001", "P002"):
        point = signal_terminals[port_name]
        external_port_name = signal_external_labels.get(port_name, port_name)
        evidence["ports"][external_port_name] = {
            "internal_signal_label": port_name,
            "side": "left",
            "terminal_x_um": float(point[0]),
            "terminal_y_um": float(point[1]),
            "measured_overlap_um": inner_min_x - float(point[0]),
        }
    for port_name in ("P003", "P004"):
        point = signal_terminals[port_name]
        external_port_name = signal_external_labels.get(port_name, port_name)
        evidence["ports"][external_port_name] = {
            "internal_signal_label": port_name,
            "side": "right",
            "terminal_x_um": float(point[0]),
            "terminal_y_um": float(point[1]),
            "measured_overlap_um": float(point[0]) - inner_max_x,
        }
    for placement in (primary_bar, secondary_bar):
        if placement is None:
            continue
        top_label = placement.resolved_top_port_label()
        bottom_label = placement.resolved_bottom_port_label()
        top_y = float(placement.center_y_um) + float(placement.half_height_um)
        bottom_y = float(placement.center_y_um) - float(placement.half_height_um)
        evidence["ports"][top_label] = {
            "side": "top",
            "terminal_x_um": float(placement.center_x_um),
            "terminal_y_um": top_y,
            "measured_overlap_um": top_y - inner_max_y,
        }
        evidence["ports"][bottom_label] = {
            "side": "bottom",
            "terminal_x_um": float(placement.center_x_um),
            "terminal_y_um": bottom_y,
            "measured_overlap_um": inner_min_y - bottom_y,
        }
    return evidence


def _point_audit_dict(point: tuple[float, float] | None) -> dict[str, float] | None:
    if point is None:
        return None
    return {"x_um": float(point[0]), "y_um": float(point[1])}


def _power_line_bridge_audit_dict(
    *,
    terminals,
    power_line: VddBarPlacement | None,
    bridge_width_um: float | None,
    coil_center_x_um: float,
    coil_outer_width_um: float,
) -> dict[str, Any] | None:
    if terminals is None or power_line is None:
        return None
    coil_anchor = getattr(terminals, "center_tap_anchor", None)
    power_line_edge = getattr(terminals, "center_tap", None)
    if coil_anchor is None or power_line_edge is None:
        return None
    x0, y0 = map(float, coil_anchor)
    x1, y1 = map(float, power_line_edge)
    coil_center_x_um = float(coil_center_x_um)
    coil_outer_half_width_um = 0.5 * float(coil_outer_width_um)
    coil_left_edge_x_um = coil_center_x_um - coil_outer_half_width_um
    coil_right_edge_x_um = coil_center_x_um + coil_outer_half_width_um
    if x0 >= coil_center_x_um:
        extends_away = x1 >= x0 - 1.0e-12
    else:
        extends_away = x1 <= x0 + 1.0e-12
    half_width = 0.5 * float(power_line.width_um)
    left_edge = float(power_line.center_x_um) - half_width
    right_edge = float(power_line.center_x_um) + half_width
    nearest_edge = left_edge if abs(x1 - left_edge) <= abs(x1 - right_edge) else right_edge
    actual_bridge_width_um = float(power_line.route_width_um)
    return {
        "coil_anchor": _point_audit_dict(coil_anchor),
        "power_line_edge": _point_audit_dict(power_line_edge),
        "width_um": actual_bridge_width_um,
        "expected_width_um": None if bridge_width_um is None else float(bridge_width_um),
        "length_um": abs(x1 - x0),
        "delta_y_um": y1 - y0,
        "center_y_um": 0.5 * (y0 + y1),
        "power_line_center_y_um": float(power_line.center_y_um),
        "coil_center_x_um": coil_center_x_um,
        "coil_left_edge_x_um": coil_left_edge_x_um,
        "coil_right_edge_x_um": coil_right_edge_x_um,
        "extends_away_from_coil_interior": bool(extends_away),
        "power_line_left_edge_x_um": left_edge,
        "power_line_right_edge_x_um": right_edge,
        "nearest_power_line_edge_x_um": nearest_edge,
        "power_line_edge_alignment_error_um": abs(x1 - nearest_edge),
        "is_horizontal": abs(y1 - y0) <= 1.0e-12,
    }


def _power_line_bar_clearance_audit_dict(
    placement: VddBarPlacement | None,
    *,
    own_coil_center_x_um: float,
    own_coil_outer_width_um: float,
    other_coil_center_x_um: float,
    other_coil_outer_width_um: float,
) -> dict[str, Any] | None:
    if placement is None:
        return None
    bar_half_width_um = 0.5 * float(placement.width_um)
    bar_left_edge_x_um = float(placement.center_x_um) - bar_half_width_um
    bar_right_edge_x_um = float(placement.center_x_um) + bar_half_width_um
    own_half_width_um = 0.5 * float(own_coil_outer_width_um)
    other_half_width_um = 0.5 * float(other_coil_outer_width_um)
    own_left_edge_x_um = float(own_coil_center_x_um) - own_half_width_um
    own_right_edge_x_um = float(own_coil_center_x_um) + own_half_width_um
    other_left_edge_x_um = float(other_coil_center_x_um) - other_half_width_um
    other_right_edge_x_um = float(other_coil_center_x_um) + other_half_width_um
    combined_left_edge_x_um = min(own_left_edge_x_um, other_left_edge_x_um)
    combined_right_edge_x_um = max(own_right_edge_x_um, other_right_edge_x_um)

    if bar_right_edge_x_um <= combined_left_edge_x_um:
        placement_side = "left"
        combined_clearance_um = combined_left_edge_x_um - bar_right_edge_x_um
    elif bar_left_edge_x_um >= combined_right_edge_x_um:
        placement_side = "right"
        combined_clearance_um = bar_left_edge_x_um - combined_right_edge_x_um
    else:
        placement_side = "overlap"
        combined_clearance_um = -min(
            bar_right_edge_x_um - combined_left_edge_x_um,
            combined_right_edge_x_um - bar_left_edge_x_um,
        )

    own_clearance_um = (
        own_left_edge_x_um - bar_right_edge_x_um
        if float(placement.center_x_um) < float(own_coil_center_x_um)
        else bar_left_edge_x_um - own_right_edge_x_um
    )
    other_clearance_um = (
        other_left_edge_x_um - bar_right_edge_x_um
        if float(placement.center_x_um) < float(other_coil_center_x_um)
        else bar_left_edge_x_um - other_right_edge_x_um
    )

    return {
        "placement_side": placement_side,
        "bar_left_edge_x_um": bar_left_edge_x_um,
        "bar_right_edge_x_um": bar_right_edge_x_um,
        "own_coil_left_edge_x_um": own_left_edge_x_um,
        "own_coil_right_edge_x_um": own_right_edge_x_um,
        "other_coil_left_edge_x_um": other_left_edge_x_um,
        "other_coil_right_edge_x_um": other_right_edge_x_um,
        "combined_coil_left_edge_x_um": combined_left_edge_x_um,
        "combined_coil_right_edge_x_um": combined_right_edge_x_um,
        "own_coil_boundary_clearance_um": own_clearance_um,
        "other_coil_boundary_clearance_um": other_clearance_um,
        "combined_coil_boundary_clearance_um": combined_clearance_um,
        "outside_combined_coil_projection": combined_clearance_um >= -1.0e-12,
    }


def _vdd_bar_audit_dict(placement: VddBarPlacement | None) -> dict[str, Any] | None:
    if placement is None:
        return None
    return {
        "center_x_um": float(placement.center_x_um),
        "center_y_um": float(placement.center_y_um),
        "width_um": float(placement.width_um),
        "route_width_um": float(placement.route_width_um),
        "height_um": float(2.0 * placement.half_height_um),
        "bar_layer": int(placement.bar_layer),
        "bar_datatype": int(placement.bar_datatype),
        "pin_layer": None if placement.pin_layer is None else int(placement.pin_layer),
        "pin_datatype": int(placement.pin_datatype),
        "top_port_label": placement.resolved_top_port_label(),
        "bottom_port_label": placement.resolved_bottom_port_label(),
        "top_ground_label": placement.resolved_top_ground_label(),
        "bottom_ground_label": placement.resolved_bottom_ground_label(),
    }


def _power_line_physical_left_right_audit(
    primary: VddBarPlacement | None,
    secondary: VddBarPlacement | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool | None]:
    if primary is None or secondary is None:
        return None, None, None
    if float(primary.center_x_um) == float(secondary.center_x_um):
        return None, None, None
    if float(primary.center_x_um) < float(secondary.center_x_um):
        return _vdd_bar_audit_dict(primary), _vdd_bar_audit_dict(secondary), True
    return _vdd_bar_audit_dict(secondary), _vdd_bar_audit_dict(primary), False


def _power_line_center_tap_topology(
    primary: VddBarPlacement | None,
    secondary: VddBarPlacement | None,
) -> str | None:
    if primary is None or secondary is None:
        return None
    if float(primary.center_x_um) == float(secondary.center_x_um):
        return None
    if float(primary.center_x_um) > float(secondary.center_x_um):
        return "primary_right_secondary_left"
    return "primary_left_secondary_right"


def _shield_inner_feed_edges(
    *,
    signal_terminals: dict[str, tuple[float, float]],
    shield_port_extension_um: float,
    shield_grounded_ports: bool,
) -> tuple[float, float]:
    left_inner_x_um = min(float(signal_terminals["P001"][0]), float(signal_terminals["P002"][0]))
    right_inner_x_um = max(float(signal_terminals["P003"][0]), float(signal_terminals["P004"][0]))
    if shield_grounded_ports:
        left_inner_x_um += float(shield_port_extension_um)
        right_inner_x_um -= float(shield_port_extension_um)
    return left_inner_x_um, right_inner_x_um


def _vdd_bar_center_x_for_shield_band(
    *,
    center_tap_x_um: float,
    left_inner_x_um: float,
    right_inner_x_um: float,
    shield_width_um: float,
    bar_width_um: float,
    bar_offset_um: float,
) -> float:
    center_tap_x_um = float(center_tap_x_um)
    left_inner_x_um = float(left_inner_x_um)
    right_inner_x_um = float(right_inner_x_um)
    shield_width_um = float(shield_width_um)
    half_bar_width_um = 0.5 * float(bar_width_um)
    bar_offset_um = float(bar_offset_um)
    left_outer_x_um = left_inner_x_um - shield_width_um - bar_offset_um
    right_outer_x_um = right_inner_x_um + shield_width_um + bar_offset_um
    if center_tap_x_um <= left_inner_x_um:
        return left_outer_x_um - half_bar_width_um
    if center_tap_x_um >= right_inner_x_um:
        return right_outer_x_um + half_bar_width_um
    left_distance_um = abs(center_tap_x_um - left_inner_x_um)
    right_distance_um = abs(right_inner_x_um - center_tap_x_um)
    if left_distance_um <= right_distance_um:
        return left_outer_x_um - half_bar_width_um
    return right_outer_x_um + half_bar_width_um


def _vdd_bar_center_x_for_tap_side(
    *,
    tap_side: str,
    left_inner_x_um: float,
    right_inner_x_um: float,
    shield_width_um: float,
    bar_width_um: float,
    bar_offset_um: float,
    inside_shield: bool = False,
) -> float:
    half_bar_width_um = 0.5 * float(bar_width_um)
    bar_offset_um = float(bar_offset_um)
    if inside_shield:
        if tap_side == "left":
            return float(left_inner_x_um) + bar_offset_um + half_bar_width_um
        if tap_side == "right":
            return float(right_inner_x_um) - bar_offset_um - half_bar_width_um
        raise ValueError(f"Unsupported tap side: {tap_side}")

    shield_width_um = float(shield_width_um)
    left_outer_x_um = float(left_inner_x_um) - shield_width_um - bar_offset_um
    right_outer_x_um = float(right_inner_x_um) + shield_width_um + bar_offset_um
    if tap_side == "left":
        return left_outer_x_um - half_bar_width_um
    if tap_side == "right":
        return right_outer_x_um + half_bar_width_um
    raise ValueError(f"Unsupported tap side: {tap_side}")


def _vdd_bar_center_x_outside_coil_union(
    *,
    tap_side: str,
    coil_center_x_um: float,
    coil_outer_width_um: float,
    other_coil_center_x_um: float,
    other_coil_outer_width_um: float,
    bar_width_um: float,
    bar_offset_um: float,
) -> float:
    """Place a power-line outside both coil x-projections on the tap side.

    The S8P power-line bridge starts at the center-tap exit on the coil
    boundary and ends at the nearest vertical power-line edge, so the bridge
    does not extend into its own winding interior. The vertical line must also
    stay clear of the other winding's projected boundary.
    """

    coil_center_x_um = float(coil_center_x_um)
    half_coil_width_um = 0.5 * float(coil_outer_width_um)
    other_coil_center_x_um = float(other_coil_center_x_um)
    half_other_width_um = 0.5 * float(other_coil_outer_width_um)
    half_bar_width_um = 0.5 * float(bar_width_um)
    bar_offset_um = float(bar_offset_um)
    if tap_side == "left":
        left_edge = min(
            coil_center_x_um - half_coil_width_um,
            other_coil_center_x_um - half_other_width_um,
        )
        return left_edge - bar_offset_um - half_bar_width_um
    if tap_side == "right":
        right_edge = max(
            coil_center_x_um + half_coil_width_um,
            other_coil_center_x_um + half_other_width_um,
        )
        return right_edge + bar_offset_um + half_bar_width_um
    raise ValueError(f"Unsupported tap side: {tap_side}")


def _vdd_bar_tap_side_for_inductor(*, side: str, turns: int) -> str:
    side = str(side)
    turns = int(turns)
    if side not in ("left", "right"):
        raise ValueError(f"Unsupported inductor side: {side}")
    if turns > 1:
        return side
    return "right" if side == "left" else "left"


def _center_tap_target_x_for_bar(
    *,
    tap_side: str,
    bar_center_x_um: float,
    bar_width_um: float,
) -> float:
    bar_center_x_um = float(bar_center_x_um)
    half_bar_width_um = 0.5 * float(bar_width_um)
    if tap_side == "left":
        return bar_center_x_um + half_bar_width_um
    if tap_side == "right":
        return bar_center_x_um - half_bar_width_um
    raise ValueError(f"Unsupported tap side: {tap_side}")


def _normalize_inductor_for_export(
    *,
    proc_info,
    inductor: InductorSpec,
    fallback_datatype: int,
) -> InductorSpec:
    draw_bridge_layer, _draw_bridge_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    draw_bridge_lower_layer, _draw_bridge_lower_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_lower_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    vdd_bar = inductor.vdd_bar
    if vdd_bar is not None:
        vdd_bar_layer, _vdd_bar_datatype = _resolve_export_pair(
            proc_info=proc_info,
            selected_layer=vdd_bar.bar_layer,
            fallback_datatype=fallback_datatype,
            role="drawing",
        )
        vdd_route_layer, _vdd_route_datatype = _resolve_export_pair(
            proc_info=proc_info,
            selected_layer=vdd_bar.route_layer,
            fallback_datatype=fallback_datatype,
            role="drawing",
        )
        vdd_bar = replace(
            vdd_bar,
            bar_layer=vdd_bar_layer,
            route_layer=vdd_route_layer,
        )
    return replace(
        inductor,
        fixed=replace(
            inductor.fixed,
            bridge_layer=draw_bridge_layer,
            bridge_lower_layer=draw_bridge_lower_layer,
            vdd_bar=vdd_bar,
        ),
    )


def _inductor_export_layer_datatypes(
    *,
    proc_info,
    inductor: InductorSpec,
    coil_layer: int,
    coil_datatype: int,
    fallback_datatype: int,
) -> tuple[tuple[int, int], ...]:
    bridge_layer, bridge_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    bridge_via_layer, bridge_via_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_via_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    bridge_lower_layer, bridge_lower_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_lower_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    bridge_lower_via_layer, bridge_lower_via_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=inductor.bridge_lower_via_layer,
        fallback_datatype=fallback_datatype,
        role="drawing",
    )
    return _unique_layer_datatypes(
        (coil_layer, coil_datatype),
        (bridge_layer, bridge_datatype),
        (bridge_via_layer, bridge_via_datatype),
        (bridge_lower_layer, bridge_lower_datatype),
        (bridge_lower_via_layer, bridge_lower_via_datatype),
    )


def _add_vdd_bar(
    *,
    cell,
    label_prefix: str,
    pin_name_prefix: str,
    inductor,
    terminals,
    target_height_um: float,
    coil_layer: int,
    metal_datatype: int,
    pin_layer: int | None,
    pin_datatype: int,
    label_layer: int,
    label_datatype: int,
    target_center_x_um: float | None = None,
    top_port_label: str | None = None,
    bottom_port_label: str | None = None,
    top_ground_label: str | None = None,
    bottom_ground_label: str | None = None,
) -> VddBarPlacement | None:
    import gdstk

    if inductor.vdd_bar is None or not inductor.vdd_bar.enabled:
        return None
    if not inductor.center_tap:
        raise ValueError(f"{label_prefix.lower()}_vdd_bar requires a center tap on that inductor")
    if terminals.center_tap is None:
        raise ValueError(f"{label_prefix} center-tap terminal is missing for VDD bar export")
    if inductor.vdd_bar.bar_layer is None:
        raise ValueError(f"{label_prefix.lower()}_vdd_bar is enabled but no bar layer is configured")

    trace_width_um = float(inductor.trace_width_um)
    bar_width_um = trace_width_um if inductor.vdd_bar.width_um is None else float(inductor.vdd_bar.width_um)
    route_width_um = bar_width_um
    half_bar_height_um = 0.5 * float(target_height_um)
    feed_end_x_um, center_y_um = map(float, terminals.center_tap)
    center_x_um = float(feed_end_x_um if target_center_x_um is None else target_center_x_um)
    bar_layer = int(inductor.vdd_bar.bar_layer)
    bar_x0_um = center_x_um - 0.5 * bar_width_um
    bar_x1_um = center_x_um + 0.5 * bar_width_um
    if feed_end_x_um < bar_x0_um:
        route_x0_um = feed_end_x_um
        route_x1_um = bar_x0_um
    elif feed_end_x_um > bar_x1_um:
        route_x0_um = bar_x1_um
        route_x1_um = feed_end_x_um
    else:
        route_x0_um = route_x1_um = feed_end_x_um
    if route_x1_um - route_x0_um > 1.0e-9:
        route_layer = int(
            inductor.vdd_bar.route_layer
            if inductor.vdd_bar.route_layer is not None
            else bar_layer
        )
        cell.add(
            gdstk.rectangle(
                (route_x0_um, center_y_um - 0.5 * route_width_um),
                (route_x1_um, center_y_um + 0.5 * route_width_um),
                layer=route_layer,
                datatype=metal_datatype,
            )
        )
    bar = gdstk.rectangle(
        (center_x_um - 0.5 * bar_width_um, center_y_um - half_bar_height_um),
        (center_x_um + 0.5 * bar_width_um, center_y_um + half_bar_height_um),
        layer=bar_layer,
        datatype=metal_datatype,
    )
    cell.add(bar)

    needs_intermediate_route = bool(
        inductor.vdd_bar.bar_via_layer is not None
        and inductor.vdd_bar.route_layer is not None
        and int(inductor.vdd_bar.route_layer) != bar_layer
    )
    via_pad_layer = int(inductor.vdd_bar.route_layer) if needs_intermediate_route else bar_layer
    via_pad = _pad_from_center(
        center=(center_x_um, center_y_um),
        width_um=bar_width_um,
        height_um=bar_width_um,
        layer=via_pad_layer,
        datatype=metal_datatype,
    )
    if needs_intermediate_route:
        cell.add(via_pad)

    if inductor.vdd_bar.route_via_layer is not None:
        cell.add(
            *_polygons_on_layer(
                (via_pad,),
                int(inductor.vdd_bar.route_via_layer),
                datatype=metal_datatype,
            )
        )
    if inductor.vdd_bar.bar_via_layer is not None:
        cell.add(
            *_polygons_on_layer(
                (via_pad,),
                int(inductor.vdd_bar.bar_via_layer),
                datatype=metal_datatype,
            )
        )

    cell.add(
        gdstk.Label(
                f"{label_prefix}_VDD",
            (center_x_um, center_y_um + half_bar_height_um + 0.75 * bar_width_um),
            layer=label_layer,
            texttype=label_datatype,
        )
    )
    return VddBarPlacement(
        name_prefix=pin_name_prefix,
        center_x_um=float(center_x_um),
        center_y_um=float(center_y_um),
        width_um=float(bar_width_um),
        route_width_um=float(route_width_um),
        half_height_um=float(half_bar_height_um),
        bar_layer=int(bar_layer),
        bar_datatype=int(metal_datatype),
        pin_layer=None if pin_layer is None else int(pin_layer),
        pin_datatype=int(pin_datatype),
        top_port_label=top_port_label,
        bottom_port_label=bottom_port_label,
        top_ground_label=top_ground_label,
        bottom_ground_label=bottom_ground_label,
    )

def export_transformer_layout(
    geometry: TransformerSpec,
    run_config: TransformerRunConfig,
    out_dir: Path,
    *,
    validate_geometry: bool = True,
) -> TransformerLayoutExport:
    """Export the fixed octagonal transformer layout to GDS and an EMX manifest."""
    import gdstk

    transformer = geometry.transformer_spec()
    power_line_8port_enabled = bool(run_config.emx.power_line_8port.enabled)
    power_line_signal_only_s4p = _power_line_signal_only_s4p(run_config)
    if power_line_8port_enabled:
        transformer = transformer.with_shared_line_width(transformer.primary.trace_width_um)
    power_line_shared_line_width_um = (
        float(transformer.primary.trace_width_um) if power_line_8port_enabled else None
    )
    power_line_8port_placement_policy = POWER_LINE_8PORT_PLACEMENT_POLICY if power_line_8port_enabled else None
    power_line_labels = _power_line_8port_label_map(run_config) if power_line_8port_enabled else {}
    _validate_power_line_8port_layout_inputs(transformer, run_config)
    if validate_geometry:
        bounds_errors = list(run_config.bounds.validate(transformer))
        geometry_errors = [*bounds_errors, *transformer.validate()]
        if geometry_errors:
            raise ValueError("geometry validation failed: " + "; ".join(geometry_errors))
        from .checks import run_transformer_gdstk_checks

        gdstk_result = run_transformer_gdstk_checks(geometry=transformer, run_config=run_config)
        if gdstk_result.errors:
            raise ValueError("gdstk geometry check failed: " + "; ".join(gdstk_result.errors))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    top_cell = run_config.emx.top_cell_prefix
    gds_path = out_dir / "transformer_layout.gds"
    manifest_path = out_dir / "transformer_layout.layout.json"
    preview_path = out_dir / "transformer_layout_preview.png"
    debug_preview_path = out_dir / "transformer_port_debug.png"

    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    cell = lib.new_cell(top_cell)
    proc_info = parse_proc_file(run_config.emx.emx_process_file)
    signal_shield_clearance_audit = _build_signal_shield_clearance_audit(
        [],
        enabled=False,
        reason="shield disabled or signal-to-shield clearance audit not applicable",
    )

    primary_draw_layer, primary_draw_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.ap_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="drawing",
    )
    primary_pin_layer, primary_pin_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.ap_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="pin",
    )
    secondary_draw_layer, secondary_draw_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.m9_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="drawing",
    )
    secondary_pin_layer, secondary_pin_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.m9_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="pin",
    )
    shield_draw_layer, shield_draw_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.shield_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="drawing",
    )
    shield_pin_layer, shield_pin_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.shield_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="pin",
    )
    process_layer_summary = _process_layer_summary(
        proc_info=proc_info,
        primary_draw_layer=primary_draw_layer,
        primary_draw_datatype=primary_draw_datatype,
        primary_pin_layer=primary_pin_layer,
        primary_pin_datatype=primary_pin_datatype,
        secondary_draw_layer=secondary_draw_layer,
        secondary_draw_datatype=secondary_draw_datatype,
        secondary_pin_layer=secondary_pin_layer,
        secondary_pin_datatype=secondary_pin_datatype,
        shield_draw_layer=shield_draw_layer,
        shield_draw_datatype=shield_draw_datatype,
        shield_pin_layer=shield_pin_layer,
        shield_pin_datatype=shield_pin_datatype,
    )

    primary_geometry = _normalize_inductor_for_export(
        proc_info=proc_info,
        inductor=transformer.primary,
        fallback_datatype=run_config.emx.metal_datatype,
    )
    secondary_geometry = _normalize_inductor_for_export(
        proc_info=proc_info,
        inductor=transformer.secondary,
        fallback_datatype=run_config.emx.metal_datatype,
    )
    primary_geometry = _with_synced_power_line_width(primary_geometry, enabled=power_line_8port_enabled)
    secondary_geometry = _with_synced_power_line_width(secondary_geometry, enabled=power_line_8port_enabled)
    primary_layer_datatypes = _inductor_export_layer_datatypes(
        proc_info=proc_info,
        inductor=primary_geometry,
        coil_layer=int(primary_draw_layer if primary_draw_layer is not None else run_config.emx.ap_layer),
        coil_datatype=int(primary_draw_datatype),
        fallback_datatype=run_config.emx.metal_datatype,
    )
    secondary_layer_datatypes = _inductor_export_layer_datatypes(
        proc_info=proc_info,
        inductor=secondary_geometry,
        coil_layer=int(secondary_draw_layer if secondary_draw_layer is not None else run_config.emx.m9_layer),
        coil_datatype=int(secondary_draw_datatype),
        fallback_datatype=run_config.emx.metal_datatype,
    )
    if run_config.emx.uses_shield_as_port_ground() and transformer.shield.enabled:
        if power_line_8port_enabled:
            shield_port_extension_um = _power_line_port_ground_overlap_um()
        elif transformer.shield.width_um is not None:
            shield_port_extension_um = float(transformer.shield.width_um)
        else:
            shield_port_extension_um = 0.0
    else:
        shield_port_extension_um = 0.0
    shield_ground_frame_width_um = (
        _shield_ground_frame_width_um(transformer, run_config)
        if transformer.shield.enabled and transformer.shield.width_um is not None
        else 0.0
    )
    export_primary_geometry = (
        replace(
            primary_geometry,
            geometry=replace(
                primary_geometry.geometry,
                feed_extension_um=float(primary_geometry.feed_extension_um) + shield_port_extension_um,
            ),
        )
        if shield_port_extension_um > 0.0
        else primary_geometry
    )
    export_secondary_geometry = (
        replace(
            secondary_geometry,
            geometry=replace(
                secondary_geometry.geometry,
                feed_extension_um=float(secondary_geometry.feed_extension_um) + shield_port_extension_um,
            ),
        )
        if shield_port_extension_um > 0.0
        else secondary_geometry
    )
    primary_bridge_anchor_gap_cap_um = None
    if export_primary_geometry.bridge_section is not None:
        primary_bridge_anchor_gap_cap_um = max(
            1.0e-6,
            export_secondary_geometry.terminal_y_span_um - export_secondary_geometry.trace_width_um - 1.0e-6,
        )
    largest_coil_height_um = max(
        float(export_primary_geometry.outer_height_um),
        float(export_secondary_geometry.outer_height_um),
    )
    power_line_max_outer_height_um = (
        _power_line_max_outer_height_um(transformer) if power_line_8port_enabled else None
    )
    power_line_vertical_length_um = (
        _power_line_vertical_length_um(transformer, run_config) if power_line_8port_enabled else None
    )
    shield_height_basis_um = (
        float(power_line_vertical_length_um) if power_line_vertical_length_um is not None else float(largest_coil_height_um)
    )
    vertical_margin_um = (
        0.0
        if power_line_8port_enabled or transformer.shield.margin_um is None
        else float(transformer.shield.margin_um)
    )
    shared_vertical_target_height_um = (
        float(power_line_vertical_length_um)
        if power_line_vertical_length_um is not None
        else largest_coil_height_um + 2.0 * vertical_margin_um
    )
    left_inner_feed_x_um = -0.5 * float(export_primary_geometry.outer_width_um) - float(export_primary_geometry.feed_extension_um)
    right_inner_feed_x_um = float(transformer.offset_um) + 0.5 * float(export_secondary_geometry.outer_width_um) + float(export_secondary_geometry.feed_extension_um)
    if run_config.emx.uses_shield_as_port_ground():
        left_inner_feed_x_um += shield_port_extension_um
        right_inner_feed_x_um -= shield_port_extension_um

    primary_bar_width_um = None
    primary_vdd_bar_tap_side = None
    primary_bar_center_x_um = None
    primary_center_tap_target_x_um = None
    if (
        export_primary_geometry.turns == 1
        and export_primary_geometry.center_tap
        and export_primary_geometry.vdd_bar is not None
        and export_primary_geometry.vdd_bar.enabled
    ):
        primary_bar_width_um = (
            float(export_primary_geometry.trace_width_um)
            if export_primary_geometry.vdd_bar.width_um is None
            else float(export_primary_geometry.vdd_bar.width_um)
        )
        primary_vdd_bar_tap_side = _vdd_bar_tap_side_for_inductor(
            side="left",
            turns=export_primary_geometry.turns,
        )
        if power_line_8port_enabled:
            primary_bar_center_x_um = _vdd_bar_center_x_outside_coil_union(
                tap_side=primary_vdd_bar_tap_side,
                coil_center_x_um=0.0,
                coil_outer_width_um=float(export_primary_geometry.outer_width_um),
                other_coil_center_x_um=float(transformer.offset_um),
                other_coil_outer_width_um=float(export_secondary_geometry.outer_width_um),
                bar_width_um=primary_bar_width_um,
                bar_offset_um=float(export_primary_geometry.vdd_bar.offset_um),
            )
        else:
            primary_bar_center_x_um = _vdd_bar_center_x_for_tap_side(
                tap_side=primary_vdd_bar_tap_side,
                left_inner_x_um=left_inner_feed_x_um,
                right_inner_x_um=right_inner_feed_x_um,
                shield_width_um=float(0.0 if transformer.shield.width_um is None else transformer.shield.width_um),
                bar_width_um=primary_bar_width_um,
                bar_offset_um=float(export_primary_geometry.vdd_bar.offset_um),
                inside_shield=False,
            )
        primary_center_tap_target_x_um = _center_tap_target_x_for_bar(
            tap_side=primary_vdd_bar_tap_side,
            bar_center_x_um=primary_bar_center_x_um,
            bar_width_um=primary_bar_width_um,
        )

    secondary_bar_width_um = None
    secondary_vdd_bar_tap_side = None
    secondary_bar_center_x_um = None
    secondary_center_tap_target_x_um = None
    if (
        export_secondary_geometry.turns == 1
        and export_secondary_geometry.center_tap
        and export_secondary_geometry.vdd_bar is not None
        and export_secondary_geometry.vdd_bar.enabled
    ):
        secondary_bar_width_um = (
            float(export_secondary_geometry.trace_width_um)
            if export_secondary_geometry.vdd_bar.width_um is None
            else float(export_secondary_geometry.vdd_bar.width_um)
        )
        secondary_vdd_bar_tap_side = _vdd_bar_tap_side_for_inductor(
            side="right",
            turns=export_secondary_geometry.turns,
        )
        if power_line_8port_enabled:
            secondary_bar_center_x_um = _vdd_bar_center_x_outside_coil_union(
                tap_side=secondary_vdd_bar_tap_side,
                coil_center_x_um=float(transformer.offset_um),
                coil_outer_width_um=float(export_secondary_geometry.outer_width_um),
                other_coil_center_x_um=0.0,
                other_coil_outer_width_um=float(export_primary_geometry.outer_width_um),
                bar_width_um=secondary_bar_width_um,
                bar_offset_um=float(export_secondary_geometry.vdd_bar.offset_um),
            )
        else:
            secondary_bar_center_x_um = _vdd_bar_center_x_for_tap_side(
                tap_side=secondary_vdd_bar_tap_side,
                left_inner_x_um=left_inner_feed_x_um,
                right_inner_x_um=right_inner_feed_x_um,
                shield_width_um=float(0.0 if transformer.shield.width_um is None else transformer.shield.width_um),
                bar_width_um=secondary_bar_width_um,
                bar_offset_um=float(export_secondary_geometry.vdd_bar.offset_um),
                inside_shield=False,
            )
        secondary_center_tap_target_x_um = _center_tap_target_x_for_bar(
            tap_side=secondary_vdd_bar_tap_side,
            bar_center_x_um=secondary_bar_center_x_um,
            bar_width_um=secondary_bar_width_um,
        )

    primary_terminals = _build_inductor(
        cell=cell,
        side="left",
        inductor=export_primary_geometry,
        center_x_um=0.0,
        center_tap_target_x_um=primary_center_tap_target_x_um,
        bridge_anchor_gap_cap_um=primary_bridge_anchor_gap_cap_um,
        metal_layer=int(primary_draw_layer if primary_draw_layer is not None else run_config.emx.ap_layer),
        metal_datatype=int(primary_draw_datatype),
        layer_datatypes=primary_layer_datatypes,
        mirror_x=False,
        center_tap_width_um=power_line_shared_line_width_um if power_line_8port_enabled else None,
    )
    secondary_terminals = _build_inductor(
        cell=cell,
        side="right",
        inductor=export_secondary_geometry,
        center_x_um=transformer.offset_um,
        center_tap_target_x_um=secondary_center_tap_target_x_um,
        bridge_anchor_gap_cap_um=None,
        metal_layer=int(secondary_draw_layer if secondary_draw_layer is not None else run_config.emx.m9_layer),
        metal_datatype=int(secondary_draw_datatype),
        layer_datatypes=secondary_layer_datatypes,
        mirror_x=True,
        center_tap_width_um=power_line_shared_line_width_um if power_line_8port_enabled else None,
    )

    if primary_terminals.center_tap is not None:
        cell.add(
            gdstk.Label(
                "PRI_CT",
                primary_terminals.center_tap,
                layer=run_config.emx.label_layer,
                texttype=run_config.emx.label_datatype,
            )
        )
    if secondary_terminals.center_tap is not None:
        cell.add(
            gdstk.Label(
                "SEC_CT",
                secondary_terminals.center_tap,
                layer=run_config.emx.label_layer,
                texttype=run_config.emx.label_datatype,
            )
        )

    signal_terminals = {
        "P001": primary_terminals.top,
        "P002": primary_terminals.bottom,
        "P003": secondary_terminals.top,
        "P004": secondary_terminals.bottom,
    }
    signal_external_labels = (
        {
            "P001": power_line_labels["primary_top"],
            "P002": power_line_labels["primary_bottom"],
            "P003": power_line_labels["secondary_top"],
            "P004": power_line_labels["secondary_bottom"],
        }
        if power_line_8port_enabled
        else {"P001": "P001", "P002": "P002", "P003": "P003", "P004": "P004"}
    )
    left_inner_feed_x_um, right_inner_feed_x_um = _shield_inner_feed_edges(
        signal_terminals=signal_terminals,
        shield_port_extension_um=shield_port_extension_um,
        shield_grounded_ports=run_config.emx.uses_shield_as_port_ground(),
    )
    if (
        primary_terminals.center_tap is not None
        and export_primary_geometry.vdd_bar is not None
        and export_primary_geometry.vdd_bar.enabled
    ):
        primary_bar_width_um = (
            float(export_primary_geometry.trace_width_um)
            if export_primary_geometry.vdd_bar.width_um is None
            else float(export_primary_geometry.vdd_bar.width_um)
        )
        primary_vdd_bar_tap_side = _vdd_bar_tap_side_for_inductor(
            side="left",
            turns=export_primary_geometry.turns,
        )
        if power_line_8port_enabled:
            primary_bar_center_x_um = _vdd_bar_center_x_outside_coil_union(
                tap_side=primary_vdd_bar_tap_side,
                coil_center_x_um=0.0,
                coil_outer_width_um=float(export_primary_geometry.outer_width_um),
                other_coil_center_x_um=float(transformer.offset_um),
                other_coil_outer_width_um=float(export_secondary_geometry.outer_width_um),
                bar_width_um=primary_bar_width_um,
                bar_offset_um=float(export_primary_geometry.vdd_bar.offset_um),
            )
        else:
            primary_bar_center_x_um = _vdd_bar_center_x_for_tap_side(
                tap_side=primary_vdd_bar_tap_side,
                left_inner_x_um=left_inner_feed_x_um,
                right_inner_x_um=right_inner_feed_x_um,
                shield_width_um=float(0.0 if transformer.shield.width_um is None else transformer.shield.width_um),
                bar_width_um=primary_bar_width_um,
                bar_offset_um=float(export_primary_geometry.vdd_bar.offset_um),
                inside_shield=False,
            )
    if (
        secondary_terminals.center_tap is not None
        and export_secondary_geometry.vdd_bar is not None
        and export_secondary_geometry.vdd_bar.enabled
    ):
        secondary_bar_width_um = (
            float(export_secondary_geometry.trace_width_um)
            if export_secondary_geometry.vdd_bar.width_um is None
            else float(export_secondary_geometry.vdd_bar.width_um)
        )
        secondary_vdd_bar_tap_side = _vdd_bar_tap_side_for_inductor(
            side="right",
            turns=export_secondary_geometry.turns,
        )
        if power_line_8port_enabled:
            secondary_bar_center_x_um = _vdd_bar_center_x_outside_coil_union(
                tap_side=secondary_vdd_bar_tap_side,
                coil_center_x_um=float(transformer.offset_um),
                coil_outer_width_um=float(export_secondary_geometry.outer_width_um),
                other_coil_center_x_um=0.0,
                other_coil_outer_width_um=float(export_primary_geometry.outer_width_um),
                bar_width_um=secondary_bar_width_um,
                bar_offset_um=float(export_secondary_geometry.vdd_bar.offset_um),
            )
        else:
            secondary_bar_center_x_um = _vdd_bar_center_x_for_tap_side(
                tap_side=secondary_vdd_bar_tap_side,
                left_inner_x_um=left_inner_feed_x_um,
                right_inner_x_um=right_inner_feed_x_um,
                shield_width_um=float(0.0 if transformer.shield.width_um is None else transformer.shield.width_um),
                bar_width_um=secondary_bar_width_um,
                bar_offset_um=float(export_secondary_geometry.vdd_bar.offset_um),
                inside_shield=False,
            )

    primary_power_top_key = "left_power_top"
    primary_power_bottom_key = "left_power_bottom"
    secondary_power_top_key = "right_power_top"
    secondary_power_bottom_key = "right_power_bottom"
    if (
        power_line_8port_enabled
        and primary_bar_center_x_um is not None
        and secondary_bar_center_x_um is not None
        and float(primary_bar_center_x_um) > float(secondary_bar_center_x_um)
    ):
        primary_power_top_key = "right_power_top"
        primary_power_bottom_key = "right_power_bottom"
        secondary_power_top_key = "left_power_top"
        secondary_power_bottom_key = "left_power_bottom"

    primary_vdd_bar = None
    secondary_vdd_bar = None
    if primary_terminals.center_tap is not None and export_primary_geometry.vdd_bar is not None and export_primary_geometry.vdd_bar.enabled:
        primary_vdd_bar = _add_vdd_bar(
            cell=cell,
            label_prefix="PRI",
            pin_name_prefix="PVDD",
            inductor=export_primary_geometry,
            terminals=primary_terminals,
            target_height_um=shared_vertical_target_height_um,
            coil_layer=int(primary_draw_layer if primary_draw_layer is not None else run_config.emx.ap_layer),
            metal_datatype=int(primary_draw_datatype),
            pin_layer=primary_pin_layer,
            pin_datatype=primary_pin_datatype,
            label_layer=run_config.emx.label_layer,
            label_datatype=run_config.emx.label_datatype,
            top_port_label=power_line_labels.get(primary_power_top_key) if power_line_8port_enabled else None,
            bottom_port_label=power_line_labels.get(primary_power_bottom_key) if power_line_8port_enabled else None,
            top_ground_label=(
                (
                    power_line_labels[primary_power_top_key]
                    if power_line_signal_only_s4p
                    else f"{power_line_labels[primary_power_top_key]}_G"
                )
                if power_line_8port_enabled
                else None
            ),
            bottom_ground_label=(
                (
                    power_line_labels[primary_power_bottom_key]
                    if power_line_signal_only_s4p
                    else f"{power_line_labels[primary_power_bottom_key]}_G"
                )
                if power_line_8port_enabled
                else None
            ),
            target_center_x_um=primary_bar_center_x_um,
        )
    if secondary_terminals.center_tap is not None and export_secondary_geometry.vdd_bar is not None and export_secondary_geometry.vdd_bar.enabled:
        secondary_vdd_bar = _add_vdd_bar(
            cell=cell,
            label_prefix="SEC",
            pin_name_prefix="SVDD",
            inductor=export_secondary_geometry,
            terminals=secondary_terminals,
            target_height_um=shared_vertical_target_height_um,
            coil_layer=int(secondary_draw_layer if secondary_draw_layer is not None else run_config.emx.m9_layer),
            metal_datatype=int(secondary_draw_datatype),
            pin_layer=secondary_pin_layer,
            pin_datatype=secondary_pin_datatype,
            label_layer=run_config.emx.label_layer,
            label_datatype=run_config.emx.label_datatype,
            top_port_label=power_line_labels.get(secondary_power_top_key) if power_line_8port_enabled else None,
            bottom_port_label=power_line_labels.get(secondary_power_bottom_key) if power_line_8port_enabled else None,
            top_ground_label=(
                (
                    power_line_labels[secondary_power_top_key]
                    if power_line_signal_only_s4p
                    else f"{power_line_labels[secondary_power_top_key]}_G"
                )
                if power_line_8port_enabled
                else None
            ),
            bottom_ground_label=(
                (
                    power_line_labels[secondary_power_bottom_key]
                    if power_line_signal_only_s4p
                    else f"{power_line_labels[secondary_power_bottom_key]}_G"
                )
                if power_line_8port_enabled
                else None
            ),
            target_center_x_um=secondary_bar_center_x_um,
        )
    cadence_pin_labels = run_config.emx.uses_cadence_pins()
    signal_label_layers = {
        "P001": int(primary_pin_layer if cadence_pin_labels and primary_pin_layer is not None else run_config.emx.label_layer),
        "P002": int(primary_pin_layer if cadence_pin_labels and primary_pin_layer is not None else run_config.emx.label_layer),
        "P003": int(secondary_pin_layer if cadence_pin_labels and secondary_pin_layer is not None else run_config.emx.label_layer),
        "P004": int(secondary_pin_layer if cadence_pin_labels and secondary_pin_layer is not None else run_config.emx.label_layer),
    }
    signal_label_datatypes = {
        "P001": int(primary_pin_datatype if cadence_pin_labels and primary_pin_layer is not None else run_config.emx.label_datatype),
        "P002": int(primary_pin_datatype if cadence_pin_labels and primary_pin_layer is not None else run_config.emx.label_datatype),
        "P003": int(secondary_pin_datatype if cadence_pin_labels and secondary_pin_layer is not None else run_config.emx.label_datatype),
        "P004": int(secondary_pin_datatype if cadence_pin_labels and secondary_pin_layer is not None else run_config.emx.label_datatype),
    }
    signal_pin_centers = {
        "P001": _signal_label_point(
            terminal=signal_terminals["P001"],
            side="left",
            width_um=primary_geometry.trace_width_um,
            feed_extension_um=primary_geometry.feed_extension_um,
            edge_aligned=(
                run_config.emx.uses_differential_ports()
                or not run_config.emx.uses_shield_as_port_ground()
            ),
        ),
        "P002": _signal_label_point(
            terminal=signal_terminals["P002"],
            side="left",
            width_um=primary_geometry.trace_width_um,
            feed_extension_um=primary_geometry.feed_extension_um,
            edge_aligned=(
                run_config.emx.uses_differential_ports()
                or not run_config.emx.uses_shield_as_port_ground()
            ),
        ),
        "P003": _signal_label_point(
            terminal=signal_terminals["P003"],
            side="right",
            width_um=secondary_geometry.trace_width_um,
            feed_extension_um=secondary_geometry.feed_extension_um,
            edge_aligned=(
                run_config.emx.uses_differential_ports()
                or not run_config.emx.uses_shield_as_port_ground()
            ),
        ),
        "P004": _signal_label_point(
            terminal=signal_terminals["P004"],
            side="right",
            width_um=secondary_geometry.trace_width_um,
            feed_extension_um=secondary_geometry.feed_extension_um,
            edge_aligned=(
                run_config.emx.uses_differential_ports()
                or not run_config.emx.uses_shield_as_port_ground()
            ),
        ),
    }
    if power_line_8port_enabled and run_config.emx.uses_shield_as_port_ground():
        primary_internal = _edge_overlap_pin_dimensions(primary_geometry.trace_width_um)
        secondary_internal = _edge_overlap_pin_dimensions(secondary_geometry.trace_width_um)
        primary_signal_internal = primary_internal
        secondary_signal_internal = secondary_internal
    else:
        primary_internal = (max(4.0, primary_geometry.trace_width_um * 0.6),) * 2
        secondary_internal = (max(4.0, secondary_geometry.trace_width_um * 0.6),) * 2
        primary_signal_internal = _edge_signal_pin_dimensions(primary_geometry.trace_width_um)
        secondary_signal_internal = _edge_signal_pin_dimensions(secondary_geometry.trace_width_um)

    shield_ground_labels: dict[str, str] = {}
    extra_single_ended_ports: list[EMXPort] = []
    auxiliary_power_ground_reference_labels: list[str] = []
    power_line_ground_stitches_for_audit: list[dict[str, Any]] = []
    power_line_ground_stitch_layers_for_manifest: list[int] = []
    ground_label_points: dict[str, tuple[float, float]] = {}
    shield_inner_bbox_for_audit: tuple[float, float, float, float] | None = None
    shield_outer_bbox_for_audit: tuple[float, float, float, float] | None = None
    signal_conductor_bbox_for_audit: tuple[float, float, float, float] | None = None
    grounded_conductor_bbox_for_audit: tuple[float, float, float, float] | None = None
    required_shield_inner_bbox_for_audit: tuple[float, float, float, float] | None = None
    shield_opening_clearance_for_audit: float | None = None
    port_ground_overlap_for_audit: float | None = None
    port_ground_overlap_evidence_for_audit: dict[str, Any] | None = None
    if transformer.shield.enabled:
        if run_config.emx.shield_layer is None:
            raise ValueError("Shield is enabled but emx.shield_layer is not configured")
        if transformer.shield.kind != "ring":
            raise ValueError(f"Unsupported shield kind: {transformer.shield.kind}")
        if transformer.shield.width_um is None:
            raise ValueError("Shield is enabled but shield.width_um is missing")

        conductor_bbox = _polygon_bbox(cell.polygons)
        left_feed_x_um = min(float(signal_terminals["P001"][0]), float(signal_terminals["P002"][0]))
        right_feed_x_um = max(float(signal_terminals["P003"][0]), float(signal_terminals["P004"][0]))
        if run_config.emx.uses_shield_as_port_ground():
            left_feed_x_um += shield_port_extension_um
            right_feed_x_um -= shield_port_extension_um
        if power_line_8port_enabled:
            signal_conductor_bbox_for_audit = _power_line_8port_opening_base_bbox(
                primary_center_x_um=0.0,
                primary_outer_width_um=float(export_primary_geometry.outer_width_um),
                primary_outer_height_um=float(export_primary_geometry.outer_height_um),
                secondary_center_x_um=float(transformer.offset_um),
                secondary_outer_width_um=float(export_secondary_geometry.outer_width_um),
                secondary_outer_height_um=float(export_secondary_geometry.outer_height_um),
                primary_bar=primary_vdd_bar,
                secondary_bar=secondary_vdd_bar,
                primary_terminals=primary_terminals,
                secondary_terminals=secondary_terminals,
            )
            grounded_conductor_bbox_for_audit = _power_line_8port_grounded_conductor_bbox(
                signal_terminals=signal_terminals,
                primary_bar=primary_vdd_bar,
                secondary_bar=secondary_vdd_bar,
            )
            shield_opening_clearance_for_audit = _power_line_shield_opening_clearance_um()
            central_clearance_bbox = _expand_bbox(
                signal_conductor_bbox_for_audit,
                shield_opening_clearance_for_audit,
            )
            port_ground_overlap_for_audit = _power_line_port_ground_overlap_um()
            required_shield_inner_bbox_for_audit = _power_line_8port_opening_bbox_with_fixed_port_overlap(
                central_bbox=central_clearance_bbox,
                signal_terminals=signal_terminals,
                primary_bar=primary_vdd_bar,
                secondary_bar=secondary_vdd_bar,
                port_ground_overlap_um=port_ground_overlap_for_audit,
            )
            inner_bbox = required_shield_inner_bbox_for_audit
        else:
            inner_bbox = _shield_inner_bbox(
                conductor_bbox=conductor_bbox,
                left_feed_x_um=left_feed_x_um,
                right_feed_x_um=right_feed_x_um,
                largest_coil_height_um=shield_height_basis_um,
                margin_um=vertical_margin_um,
            )
        shield_ring = _rectangular_ring(
            inner_bbox=inner_bbox,
            width_um=float(shield_ground_frame_width_um),
            layer=int(shield_draw_layer if shield_draw_layer is not None else run_config.emx.shield_layer),
            datatype=int(shield_draw_datatype),
        )
        cell.add(*shield_ring)
        should_audit_signal_shield_clearance = (
            run_config.emx.uses_shield_as_port_ground()
            and primary_vdd_bar is None
            and secondary_vdd_bar is None
        )
        if should_audit_signal_shield_clearance:
            primary_allowed_windows = (
                _shield_port_overlap_windows(
                    inner_bbox=inner_bbox,
                    shield_width_um=float(shield_ground_frame_width_um),
                    side="left",
                    terminals=(signal_terminals["P001"], signal_terminals["P002"]),
                    trace_width_um=primary_geometry.trace_width_um,
                    layer=int(primary_draw_layer if primary_draw_layer is not None else run_config.emx.ap_layer),
                    datatype=int(primary_draw_datatype),
                )
                if run_config.emx.uses_shield_as_port_ground()
                else tuple()
            )
            secondary_allowed_windows = (
                _shield_port_overlap_windows(
                    inner_bbox=inner_bbox,
                    shield_width_um=float(shield_ground_frame_width_um),
                    side="right",
                    terminals=(signal_terminals["P003"], signal_terminals["P004"]),
                    trace_width_um=secondary_geometry.trace_width_um,
                    layer=int(secondary_draw_layer if secondary_draw_layer is not None else run_config.emx.m9_layer),
                    datatype=int(secondary_draw_datatype),
                )
                if run_config.emx.uses_shield_as_port_ground()
                else tuple()
            )
            shield_clearance_records = [
                _signal_shield_clearance_report(
                    cell=cell,
                    signal_name="primary",
                    signal_layer=int(primary_draw_layer if primary_draw_layer is not None else run_config.emx.ap_layer),
                    signal_datatype=int(primary_draw_datatype),
                    shield_layer=int(shield_draw_layer if shield_draw_layer is not None else run_config.emx.shield_layer),
                    shield_datatype=int(shield_draw_datatype),
                    allowed_overlap_windows=primary_allowed_windows,
                    trace_width_um=primary_geometry.trace_width_um,
                ),
                _signal_shield_clearance_report(
                    cell=cell,
                    signal_name="secondary",
                    signal_layer=int(secondary_draw_layer if secondary_draw_layer is not None else run_config.emx.m9_layer),
                    signal_datatype=int(secondary_draw_datatype),
                    shield_layer=int(shield_draw_layer if shield_draw_layer is not None else run_config.emx.shield_layer),
                    shield_datatype=int(shield_draw_datatype),
                    allowed_overlap_windows=secondary_allowed_windows,
                    trace_width_um=secondary_geometry.trace_width_um,
                ),
            ]
            signal_shield_clearance_audit = _build_signal_shield_clearance_audit(
                shield_clearance_records,
                enabled=True,
                reason=None,
            )
            _write_signal_shield_clearance_audit(out_dir, signal_shield_clearance_audit)
            shield_clearance_errors = [
                error
                for record in shield_clearance_records
                for error in _signal_shield_clearance_errors_from_report(record)
            ]
            if validate_geometry and shield_clearance_errors:
                raise ValueError("gdstk geometry check failed: " + "; ".join(shield_clearance_errors))
        else:
            reason = "EMX port mode does not use shield as port ground"
            if run_config.emx.uses_shield_as_port_ground() and (primary_vdd_bar is not None or secondary_vdd_bar is not None):
                reason = "VDD bar shield port geometry uses dedicated edge ground pins"
            signal_shield_clearance_audit = _build_signal_shield_clearance_audit(
                [],
                enabled=False,
                reason=reason,
            )
            _write_signal_shield_clearance_audit(out_dir, signal_shield_clearance_audit)
        shield_outer_min_x = float(inner_bbox[0]) - float(shield_ground_frame_width_um)
        shield_outer_max_x = float(inner_bbox[2]) + float(shield_ground_frame_width_um)
        shield_outer_min_y = float(inner_bbox[1]) - float(shield_ground_frame_width_um)
        shield_outer_max_y = float(inner_bbox[3]) + float(shield_ground_frame_width_um)
        shield_inner_bbox_for_audit = tuple(float(value) for value in inner_bbox)
        shield_outer_bbox_for_audit = (
            shield_outer_min_x,
            shield_outer_min_y,
            shield_outer_max_x,
            shield_outer_max_y,
        )
        if power_line_8port_enabled:
            port_ground_overlap_evidence_for_audit = _power_line_8port_port_overlap_evidence(
                signal_terminals=signal_terminals,
                signal_external_labels=signal_external_labels,
                primary_bar=primary_vdd_bar,
                secondary_bar=secondary_vdd_bar,
                shield_inner_bbox=shield_inner_bbox_for_audit,
                expected_overlap_um=_power_line_port_ground_overlap_um(),
            )

        if run_config.emx.uses_shield_as_port_ground():
            left_outer_x = min(float(signal_terminals["P001"][0]), float(signal_terminals["P002"][0]))
            right_outer_x = max(float(signal_terminals["P003"][0]), float(signal_terminals["P004"][0]))
            primary_pin_width_um, primary_pin_height_um = _edge_overlap_pin_dimensions(primary_geometry.trace_width_um)
            secondary_pin_width_um, secondary_pin_height_um = _edge_overlap_pin_dimensions(secondary_geometry.trace_width_um)
            signal_pin_centers = {
                "P001": _offset_pin_center_from_edge(
                    edge_point=(left_outer_x, float(signal_terminals["P001"][1])),
                    side="left",
                    pin_width_um=primary_pin_width_um,
                    pin_height_um=primary_pin_height_um,
                ),
                "P002": _offset_pin_center_from_edge(
                    edge_point=(left_outer_x, float(signal_terminals["P002"][1])),
                    side="left",
                    pin_width_um=primary_pin_width_um,
                    pin_height_um=primary_pin_height_um,
                ),
                "P003": _offset_pin_center_from_edge(
                    edge_point=(right_outer_x, float(signal_terminals["P003"][1])),
                    side="right",
                    pin_width_um=secondary_pin_width_um,
                    pin_height_um=secondary_pin_height_um,
                ),
                "P004": _offset_pin_center_from_edge(
                    edge_point=(right_outer_x, float(signal_terminals["P004"][1])),
                    side="right",
                    pin_width_um=secondary_pin_width_um,
                    pin_height_um=secondary_pin_height_um,
                ),
            }
            ground_label_points = dict(signal_pin_centers)
            for port_name, point in ground_label_points.items():
                external_port_name = signal_external_labels[port_name]
                ground_label = f"{external_port_name}_G"
                shield_ground_labels[external_port_name] = ground_label
                trace_width_um = (
                    primary_geometry.trace_width_um
                    if port_name in ("P001", "P002")
                    else secondary_geometry.trace_width_um
                )
                pin_width_um, pin_height_um = _edge_overlap_pin_dimensions(trace_width_um)
                _add_port_pin(
                    cell=cell,
                    center=point,
                    width_um=pin_width_um,
                    height_um=pin_height_um,
                    layer=shield_pin_layer,
                    datatype=shield_pin_datatype,
                )
                cell.add(
                    gdstk.Label(
                        ground_label,
                        point,
                        layer=int(shield_pin_layer if cadence_pin_labels and shield_pin_layer is not None else run_config.emx.label_layer),
                        texttype=(int(shield_pin_datatype) if cadence_pin_labels and shield_pin_layer is not None else run_config.emx.label_datatype),
                    )
                )

            for placement in (primary_vdd_bar, secondary_vdd_bar):
                if placement is None or placement.pin_layer is None:
                    continue
                pin_width_um, pin_height_um = _vdd_edge_pin_dimensions(placement.width_um)
                signal_internal = (pin_width_um, pin_height_um)
                ground_internal = (pin_width_um, pin_height_um)
                top_center = _offset_pin_center_from_edge(
                    edge_point=(placement.center_x_um, placement.center_y_um + placement.half_height_um),
                    side="top",
                    pin_width_um=pin_width_um,
                    pin_height_um=pin_height_um,
                )
                bottom_center = _offset_pin_center_from_edge(
                    edge_point=(placement.center_x_um, placement.center_y_um - placement.half_height_um),
                    side="bottom",
                    pin_width_um=pin_width_um,
                    pin_height_um=pin_height_um,
                )
                top_ground_center = _offset_pin_center_from_edge(
                    edge_point=(placement.center_x_um, placement.center_y_um + placement.half_height_um),
                    side="top",
                    pin_width_um=pin_width_um,
                    pin_height_um=pin_height_um,
                )
                bottom_ground_center = _offset_pin_center_from_edge(
                    edge_point=(placement.center_x_um, placement.center_y_um - placement.half_height_um),
                    side="bottom",
                    pin_width_um=pin_width_um,
                    pin_height_um=pin_height_um,
                )
                for name, point in (
                    (placement.resolved_top_port_label(), top_center),
                    (placement.resolved_bottom_port_label(), bottom_center),
                ):
                    if not power_line_signal_only_s4p:
                        _add_port_pin(
                            cell=cell,
                            center=point,
                            width_um=pin_width_um,
                            height_um=pin_height_um,
                            layer=placement.pin_layer,
                            datatype=placement.pin_datatype,
                        )
                        cell.add(
                            gdstk.Label(
                                name,
                                point,
                                layer=int(placement.pin_layer if cadence_pin_labels else run_config.emx.label_layer),
                                texttype=(
                                    int(placement.pin_datatype)
                                    if cadence_pin_labels
                                    else run_config.emx.label_datatype
                                ),
                            )
                        )
                for name, point in (
                    (placement.resolved_top_ground_label(), top_ground_center),
                    (placement.resolved_bottom_ground_label(), bottom_ground_center),
                ):
                    if shield_pin_layer is not None:
                        _add_port_pin(
                            cell=cell,
                            center=point,
                            width_um=pin_width_um,
                            height_um=pin_height_um,
                            layer=shield_pin_layer,
                            datatype=shield_pin_datatype,
                        )
                        cell.add(
                            gdstk.Label(
                                name,
                                point,
                                layer=int(shield_pin_layer if cadence_pin_labels else run_config.emx.label_layer),
                                texttype=(int(shield_pin_datatype) if cadence_pin_labels else run_config.emx.label_datatype),
                            )
                        )
                for name, ground_name, point in (
                    (placement.resolved_top_port_label(), placement.resolved_top_ground_label(), top_ground_center),
                    (
                        placement.resolved_bottom_port_label(),
                        placement.resolved_bottom_ground_label(),
                        bottom_ground_center,
                    ),
                ):
                    stitch = _add_power_line_ground_stitch_stack(
                        cell=cell,
                        proc_info=proc_info,
                        label=name,
                        ground_label=ground_name,
                        center=point,
                        footprint_um=(pin_width_um, pin_height_um),
                        source_layer=placement.bar_layer,
                        source_datatype=placement.bar_datatype,
                        target_ground_layer=shield_draw_layer,
                        fallback_datatype=run_config.emx.metal_datatype,
                    )
                    power_line_ground_stitches_for_audit.append(stitch)
                    power_line_ground_stitch_layers_for_manifest.extend(int(layer) for layer in stitch["used_layers"])
                if power_line_signal_only_s4p:
                    auxiliary_power_ground_reference_labels.extend(
                        (
                            placement.resolved_top_ground_label(),
                            placement.resolved_bottom_ground_label(),
                        )
                    )
                else:
                    extra_single_ended_ports.extend(
                        (
                            EMXPort(
                                placement.resolved_top_port_label(),
                                (placement.resolved_top_port_label(),),
                                (placement.resolved_top_ground_label(),),
                                signal_internal,
                                signal_internal_size_um=signal_internal,
                                ground_internal_size_um=ground_internal,
                                internal_signal_labels=True,
                                internal_ground_labels=True,
                            ),
                            EMXPort(
                                placement.resolved_bottom_port_label(),
                                (placement.resolved_bottom_port_label(),),
                                (placement.resolved_bottom_ground_label(),),
                                signal_internal,
                                signal_internal_size_um=signal_internal,
                                ground_internal_size_um=ground_internal,
                                internal_signal_labels=True,
                                internal_ground_labels=True,
                            ),
                        )
                    )

    if run_config.emx.uses_shield_as_port_ground() and not transformer.shield.enabled:
        _write_signal_shield_clearance_audit(out_dir, signal_shield_clearance_audit)
        raise ValueError("EMX port mode 'single_ended_shield_grounded' requires transformer.shield.enabled")

    if not transformer.shield.enabled:
        _write_signal_shield_clearance_audit(out_dir, signal_shield_clearance_audit)

    draw_signal_pin_boxes = run_config.emx.uses_shield_as_port_ground() or cadence_pin_labels
    for internal_name, point in signal_pin_centers.items():
        name = signal_external_labels[internal_name]
        if draw_signal_pin_boxes:
            if internal_name in ("P001", "P002"):
                if run_config.emx.uses_shield_as_port_ground():
                    pin_width_um, pin_height_um = _edge_overlap_pin_dimensions(primary_geometry.trace_width_um)
                else:
                    pin_width_um, pin_height_um = _port_pin_dimensions(primary_geometry.trace_width_um)
                _add_port_pin(
                    cell=cell,
                    center=point,
                    width_um=pin_width_um,
                    height_um=pin_height_um,
                    layer=primary_pin_layer,
                    datatype=primary_pin_datatype,
                )
            else:
                if run_config.emx.uses_shield_as_port_ground():
                    pin_width_um, pin_height_um = _edge_overlap_pin_dimensions(secondary_geometry.trace_width_um)
                else:
                    pin_width_um, pin_height_um = _port_pin_dimensions(secondary_geometry.trace_width_um)
                _add_port_pin(
                    cell=cell,
                    center=point,
                    width_um=pin_width_um,
                    height_um=pin_height_um,
                    layer=secondary_pin_layer,
                    datatype=secondary_pin_datatype,
                )
        cell.add(
            gdstk.Label(
                name,
                point,
                layer=signal_label_layers[internal_name],
                texttype=signal_label_datatypes[internal_name],
            )
        )

    lib.write_gds(str(gds_path))

    if power_line_8port_enabled:
        def _ground_labels_for_signal(label: str) -> tuple[str, ...]:
            labels: list[str] = []
            if label in shield_ground_labels:
                labels.append(shield_ground_labels[label])
            # In signal-only S4P mode the auxiliary power-line endpoints are
            # physically stitched to the shield/M5 ground conductor.  Reusing
            # those labels in every GSG port ground list makes EMX reject the
            # setup because a label cannot be part of multiple ground groups.
            return tuple(dict.fromkeys(labels))

        coil_port_by_label = {
            signal_external_labels["P001"]: EMXPort(
                signal_external_labels["P001"],
                (signal_external_labels["P001"],),
                _ground_labels_for_signal(signal_external_labels["P001"]),
                primary_internal,
                signal_internal_size_um=primary_signal_internal,
                ground_internal_size_um=primary_internal,
                internal_signal_labels=True,
                internal_ground_labels=True,
            ),
            signal_external_labels["P002"]: EMXPort(
                signal_external_labels["P002"],
                (signal_external_labels["P002"],),
                _ground_labels_for_signal(signal_external_labels["P002"]),
                primary_internal,
                signal_internal_size_um=primary_signal_internal,
                ground_internal_size_um=primary_internal,
                internal_signal_labels=True,
                internal_ground_labels=True,
            ),
            signal_external_labels["P003"]: EMXPort(
                signal_external_labels["P003"],
                (signal_external_labels["P003"],),
                _ground_labels_for_signal(signal_external_labels["P003"]),
                secondary_internal,
                signal_internal_size_um=secondary_signal_internal,
                ground_internal_size_um=secondary_internal,
                internal_signal_labels=True,
                internal_ground_labels=True,
            ),
            signal_external_labels["P004"]: EMXPort(
                signal_external_labels["P004"],
                (signal_external_labels["P004"],),
                _ground_labels_for_signal(signal_external_labels["P004"]),
                secondary_internal,
                signal_internal_size_um=secondary_signal_internal,
                ground_internal_size_um=secondary_internal,
                internal_signal_labels=True,
                internal_ground_labels=True,
            ),
        }
        port_by_label = {**coil_port_by_label, **{port.name: port for port in extra_single_ended_ports}}
        missing_power_line_ports = [label for label in run_config.emx.power_line_8port.port_map if label not in port_by_label]
        if missing_power_line_ports:
            raise ValueError(f"power_line_8port manifest is missing ports: {missing_power_line_ports}")
        ports = tuple(port_by_label[label] for label in run_config.emx.power_line_8port.port_map)
    elif run_config.emx.uses_differential_ports():
        ports = (
            EMXPort("PPRI", ("P001",), ("P002",), primary_internal),
            EMXPort("PSEC", ("P003",), ("P004",), secondary_internal),
        )
    else:
        ports = (
            EMXPort(
                "P001",
                ("P001",),
                (() if "P001" not in shield_ground_labels else (shield_ground_labels["P001"],)),
                primary_internal,
                signal_internal_size_um=primary_signal_internal,
                ground_internal_size_um=primary_internal,
                internal_signal_labels=run_config.emx.uses_shield_as_port_ground(),
                internal_ground_labels=True,
            ),
            EMXPort(
                "P002",
                ("P002",),
                (() if "P002" not in shield_ground_labels else (shield_ground_labels["P002"],)),
                primary_internal,
                signal_internal_size_um=primary_signal_internal,
                ground_internal_size_um=primary_internal,
                internal_signal_labels=run_config.emx.uses_shield_as_port_ground(),
                internal_ground_labels=True,
            ),
            EMXPort(
                "P003",
                ("P003",),
                (() if "P003" not in shield_ground_labels else (shield_ground_labels["P003"],)),
                secondary_internal,
                signal_internal_size_um=secondary_signal_internal,
                ground_internal_size_um=secondary_internal,
                internal_signal_labels=run_config.emx.uses_shield_as_port_ground(),
                internal_ground_labels=True,
            ),
            EMXPort(
                "P004",
                ("P004",),
                (() if "P004" not in shield_ground_labels else (shield_ground_labels["P004"],)),
                secondary_internal,
                signal_internal_size_um=secondary_signal_internal,
                ground_internal_size_um=secondary_internal,
                internal_signal_labels=run_config.emx.uses_shield_as_port_ground(),
                internal_ground_labels=True,
            ),
        ) + tuple(extra_single_ended_ports)
    manifest_kwargs = {
        "layout_path": str(gds_path),
        "top_cell": top_cell,
        "ports": ports,
        "metal_layer": int(primary_pin_layer if primary_pin_layer is not None else run_config.emx.ap_layer),
        "metal_datatype": int(primary_pin_datatype),
        "ground_layer": (
            shield_pin_layer
            if transformer.shield.enabled and run_config.emx.uses_shield_as_port_ground()
            else None
        ),
        "ground_datatype": (
            int(shield_pin_datatype)
            if transformer.shield.enabled and run_config.emx.uses_shield_as_port_ground()
            else None
        ),
        "label_layer": run_config.emx.label_layer,
        "label_datatype": run_config.emx.label_datatype,
        "cadence_pin_purpose": run_config.emx.cadence_pin_purpose,
        "layer_draw_order": _unique_layers(
            *(_ordered_draw_layers(
                run_config=run_config,
                primary_geometry=primary_geometry,
                secondary_geometry=secondary_geometry,
            )),
            primary_pin_layer,
            secondary_pin_layer,
            shield_pin_layer if transformer.shield.enabled else None,
            *power_line_ground_stitch_layers_for_manifest,
            run_config.emx.label_layer,
        ),
        "process_layer_summary": process_layer_summary,
    }
    try:
        manifest = EMXLayoutManifest(**manifest_kwargs)
    except TypeError:
        # Zeus can still have an older shared EMX manifest helper without
        # layer_draw_order; omit it so layout export remains functional.
        manifest_kwargs.pop("layer_draw_order", None)
        manifest_kwargs.pop("process_layer_summary", None)
        manifest = EMXLayoutManifest(**manifest_kwargs)
    manifest.to_json(manifest_path)

    (out_dir / "geometry.json").write_text(
        json.dumps(geometry.as_dict(), indent=2),
        encoding="utf-8",
    )
    _write_power_line_8port_geometry_audit(
        out_dir,
        enabled=power_line_8port_enabled,
        reason=None if power_line_8port_enabled else "power_line_8port disabled",
        touchstone_mode=(str(run_config.emx.power_line_8port.touchstone_mode) if power_line_8port_enabled else None),
        placement_policy=power_line_8port_placement_policy,
        labels=power_line_labels,
        auxiliary_ground_reference_labels=tuple(sorted(dict.fromkeys(auxiliary_power_ground_reference_labels))),
        power_line_ground_stitches=tuple(power_line_ground_stitches_for_audit),
        vertical_length_um=power_line_vertical_length_um,
        max_outer_height_um=power_line_max_outer_height_um,
        vertical_length_diameter_ratio=(
            run_config.emx.power_line_8port.vertical_length_diameter_ratio if power_line_8port_enabled else None
        ),
        line_width_um=power_line_shared_line_width_um if power_line_8port_enabled else None,
        bridge_width_um=power_line_shared_line_width_um if power_line_8port_enabled else None,
        primary_bar=primary_vdd_bar,
        secondary_bar=secondary_vdd_bar,
        primary_terminals=primary_terminals,
        secondary_terminals=secondary_terminals,
        primary_coil_center_x_um=0.0,
        primary_coil_outer_width_um=transformer.primary.outer_width_um,
        secondary_coil_center_x_um=transformer.offset_um,
        secondary_coil_outer_width_um=transformer.secondary.outer_width_um,
        signal_conductor_bbox_um=signal_conductor_bbox_for_audit,
        grounded_conductor_bbox_um=grounded_conductor_bbox_for_audit,
        required_shield_inner_bbox_um=required_shield_inner_bbox_for_audit,
        shield_opening_clearance_um=shield_opening_clearance_for_audit,
        port_ground_overlap_um=port_ground_overlap_for_audit,
        port_ground_overlap_evidence=port_ground_overlap_evidence_for_audit,
        shield_inner_bbox_um=shield_inner_bbox_for_audit,
        shield_outer_bbox_um=shield_outer_bbox_for_audit,
        ground_frame_width_um=shield_ground_frame_width_um if power_line_8port_enabled else None,
        ground_frame_policy=(
            "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
            if power_line_8port_enabled
            else None
        ),
        process_layer_summary=process_layer_summary,
    )

    if _env_flag("RFIC_SKIP_LAYOUT_PREVIEWS"):
        preview_path.write_text("preview rendering skipped by RFIC_SKIP_LAYOUT_PREVIEWS=1\n", encoding="utf-8")
        debug_preview_path.write_text("debug preview rendering skipped by RFIC_SKIP_LAYOUT_PREVIEWS=1\n", encoding="utf-8")
    else:
        try:
            render_emx_layout_preview(gds_path, preview_path, manifest_path=manifest_path)
            render_emx_port_debug_panels(gds_path, debug_preview_path, manifest_path=manifest_path)
        except Exception as exc:  # pragma: no cover - environment-dependent plotting stack
            logger.warning("Skipping EMX preview rendering for %s: %s", gds_path, exc)
            preview_path.write_text(f"preview render failed: {exc}\n", encoding="utf-8")
            debug_preview_path.write_text(f"debug preview render failed: {exc}\n", encoding="utf-8")
    return TransformerLayoutExport(
        gds_path=gds_path,
        manifest_path=manifest_path,
        preview_path=preview_path,
        debug_preview_path=debug_preview_path,
        top_cell=top_cell,
    )
