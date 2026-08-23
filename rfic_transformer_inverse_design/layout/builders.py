"""Geometry construction helpers for transformer layout generation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.types import InductorSpec

@dataclass(frozen=True)
class InductorLayoutSpec:
    """Placement, layer, and mirroring info for one inductor instance."""

    geometry: InductorSpec
    center_x_um: float
    center_y_um: float
    bridge_offset_y_um: float
    bridge_anchor_gap_cap_um: float | None
    metal_layer: int
    bridge_layer: int
    bridge_via_layer: int | None = None
    bridge_lower_layer: int | None = None
    bridge_lower_via_layer: int | None = None
    layer_datatypes: tuple[tuple[int, int], ...] = tuple()
    mirror_x: bool = False

    @property
    def outer_half_width_um(self) -> float:
        return self.geometry.outer_width_um * 0.5

    @property
    def outer_half_height_um(self) -> float:
        return self.geometry.outer_height_um * 0.5

    @property
    def trace_width_um(self) -> float:
        return self.geometry.trace_width_um

    @property
    def spacing_um(self) -> float:
        return self.geometry.spacing_um

    @property
    def turns(self) -> int:
        return self.geometry.turns

    @property
    def feed_extension_um(self) -> float:
        return self.geometry.feed_extension_um

    @property
    def terminal_y_span_um(self) -> float:
        return self.geometry.terminal_y_span_um

    @property
    def pitch_um(self) -> float:
        return self.trace_width_um + self.spacing_um

    @property
    def inner_half_width_um(self) -> float:
        return self.outer_half_width_um - (self.turns - 1) * self.pitch_um

    @property
    def inner_half_height_um(self) -> float:
        return self.outer_half_height_um - (self.turns - 1) * self.pitch_um


@dataclass(frozen=True)
class InductorTerminals:
    top: tuple[float, float]
    bottom: tuple[float, float]
    center_tap: tuple[float, float] | None = None
    center_tap_anchor: tuple[float, float] | None = None


@dataclass(frozen=True)
class LayerPolygonGroup:
    layer: int
    polygons: tuple[object, ...]
    role: str


@dataclass(frozen=True)
class BridgePadStage:
    name: str
    layer: int
    polygons: tuple[object, ...]


@dataclass(frozen=True)
class BridgeEndpointStack:
    name: str
    stages: tuple[BridgePadStage, ...]

    def polygons_on_layer(self, layer: int) -> tuple[object, ...]:
        result: list[object] = []
        for stage in self.stages:
            if int(stage.layer) == int(layer):
                result.extend(stage.polygons)
        return tuple(result)


@dataclass(frozen=True)
class CenterTappedInductorGeometry:
    coil_polygons: tuple[object, ...]
    bridge_polygons: tuple[object, ...]
    via_polygons: tuple[object, ...]
    intermediate_bridge_pad_polygons: tuple[object, ...]
    top_feed_polygons: tuple[object, ...]
    center_feed_polygons: tuple[object, ...]
    bottom_feed_polygons: tuple[object, ...]
    outer_anchor_pad: tuple[object, ...]
    inner_anchor_pad: tuple[object, ...]
    top_terminal_window: tuple[object, ...]
    bottom_terminal_window: tuple[object, ...]
    terminals: InductorTerminals
    coil_groups: tuple[LayerPolygonGroup, ...] = tuple()
    feed_groups: tuple[LayerPolygonGroup, ...] = tuple()
    crossing_groups: tuple[LayerPolygonGroup, ...] = tuple()
    crossing_pad_groups: tuple[LayerPolygonGroup, ...] = tuple()
    bridge_groups: tuple[LayerPolygonGroup, ...] = tuple()
    via_groups: tuple[LayerPolygonGroup, ...] = tuple()
    bridge_endpoint_stacks: tuple[BridgeEndpointStack, ...] = tuple()

    def polygons_for_layer(self, groups: tuple[LayerPolygonGroup, ...], layer: int) -> tuple[object, ...]:
        result: list[object] = []
        for group in groups:
            if int(group.layer) == int(layer):
                result.extend(group.polygons)
        return tuple(result)

    def _legacy_polygons_for_layer(self, polygons: tuple[object, ...], layer: int) -> tuple[object, ...]:
        return tuple(poly for poly in polygons if int(getattr(poly, "layer", -1)) == int(layer))

    def coil_on_layer(self, layer: int) -> tuple[object, ...]:
        if self.coil_groups:
            return self.polygons_for_layer(self.coil_groups, layer)
        return self._legacy_polygons_for_layer(self.coil_polygons, layer)

    def feeds_on_layer(self, layer: int) -> tuple[object, ...]:
        if self.feed_groups:
            return self.polygons_for_layer(self.feed_groups, layer)
        return self._legacy_polygons_for_layer(
            self.top_feed_polygons + self.center_feed_polygons + self.bottom_feed_polygons,
            layer,
        )

    def crossing_on_layer(self, layer: int) -> tuple[object, ...]:
        return self.polygons_for_layer(self.crossing_groups, layer)

    def crossing_pads_on_layer(self, layer: int) -> tuple[object, ...]:
        return self.polygons_for_layer(self.crossing_pad_groups, layer)

    def bridge_on_layer(self, layer: int) -> tuple[object, ...]:
        if self.bridge_groups:
            return self.polygons_for_layer(self.bridge_groups, layer)
        return self._legacy_polygons_for_layer(
            self.bridge_polygons + self.intermediate_bridge_pad_polygons,
            layer,
        )

    def vias_on_layer(self, layer: int) -> tuple[object, ...]:
        if self.via_groups:
            return self.polygons_for_layer(self.via_groups, layer)
        return self._legacy_polygons_for_layer(self.via_polygons, layer)

    def forbidden_geometry_on_layer(self, layer: int) -> tuple[object, ...]:
        return (
            self.coil_on_layer(layer)
            + self.feeds_on_layer(layer)
            + self.crossing_on_layer(layer)
            + self.crossing_pads_on_layer(layer)
            + self.bridge_on_layer(layer)
            + self.vias_on_layer(layer)
        )


@dataclass(frozen=True)
class BridgeCornerAnchors:
    """Corner anchors for a 2-point diagonal bridge rectangle."""

    start_upper: tuple[float, float]
    start_lower: tuple[float, float]
    end_upper: tuple[float, float]
    end_lower: tuple[float, float]


def _bridge_section_pad_height_ratio(spec: InductorLayoutSpec) -> float:
    section = spec.geometry.bridge_section
    if section is None:
        return 1.0
    return min(1.0, max(0.05, float(section.pad_height_ratio)))


def _bridge_section_pad_width_ratio(spec: InductorLayoutSpec) -> float:
    section = spec.geometry.bridge_section
    if section is None:
        return 0.70
    return min(1.0, max(0.05, float(section.pad_width_ratio)))


def _bridge_section_via_size_ratio(spec: InductorLayoutSpec) -> float:
    section = spec.geometry.bridge_section
    if section is None:
        return 0.60
    return min(1.0, max(0.05, float(section.via_size_ratio)))


def _bridge_section_via_width_ratio(spec: InductorLayoutSpec) -> float:
    section = spec.geometry.bridge_section
    if section is None:
        return 0.35
    return min(1.0, max(0.05, float(section.via_width_ratio)))


def _bridge_section_via_spacing_ratio(spec: InductorLayoutSpec) -> float:
    section = spec.geometry.bridge_section
    if section is None:
        return 0.50
    return max(0.05, float(section.via_spacing_ratio))


def _build_winding(
    side: str,
    inductor: InductorSpec,
    center_x_um: float,
) -> tuple[list[tuple[float, float]], tuple[tuple[float, float], tuple[float, float]]]:
    turns = inductor.turns
    outer_half_width_um = inductor.outer_width_um * 0.5
    outer_half_height_um = inductor.outer_height_um * 0.5
    width_um = inductor.trace_width_um
    spacing_um = inductor.spacing_um
    feed_extension_um = inductor.feed_extension_um
    terminal_y_span_um = inductor.terminal_y_span_um
    if turns > 1:
        _resolved_terminal_half_span(outer_half_width_um, outer_half_height_um, terminal_y_span_um)
    pitch = width_um + spacing_um
    points: list[tuple[float, float]] = []
    start_label: tuple[float, float] | None = None
    end_label: tuple[float, float] | None = None
    direction = "down" if side == "left" else "down"

    for turn_idx in range(turns):
        half_width_um = outer_half_width_um - turn_idx * pitch
        half_height_um = outer_half_height_um - turn_idx * pitch
        if half_width_um <= width_um or half_height_um <= width_um:
            raise ValueError("turn size collapsed")
        if turn_idx == 0:
            start_point = _terminal_point(
                side,
                half_width_um,
                half_height_um,
                direction,
                center_x_um,
                terminal_y_span_um=terminal_y_span_um,
            )
            lead_start = _extend_terminal(side, start_point, feed_extension_um)
            points.extend([lead_start, start_point])
            start_label = lead_start
        path_points = _open_octagon_path(
            side,
            half_width_um,
            half_height_um,
            direction,
            center_x_um,
            terminal_y_span_um=terminal_y_span_um if turn_idx == 0 and turns == 1 else None,
        )
        points.extend(path_points[1:] if points else path_points)
        end_point = path_points[-1]
        if turn_idx == turns - 1:
            lead_end = _extend_terminal(side, end_point, feed_extension_um)
            points.append(lead_end)
            end_label = lead_end
        else:
            # Keep the same circulation sense across turns; alternating the
            # open-path direction makes adjacent turns cancel magnetically.
            bridge_target = _terminal_point(
                side,
                half_width_um - pitch,
                half_height_um - pitch,
                direction,
                center_x_um,
            )
            points.append(bridge_target)

    assert start_label is not None and end_label is not None
    return points, (start_label, end_label)


def _build_inductor(
    *,
    cell,
    side: str,
    inductor: InductorSpec,
    center_x_um: float,
    center_tap_target_x_um: float | None,
    bridge_anchor_gap_cap_um: float | None,
    metal_layer: int,
    metal_datatype: int,
    layer_datatypes: tuple[tuple[int, int], ...] = tuple(),
    mirror_x: bool,
    center_tap_width_um: float | None = None,
) -> InductorTerminals:
    import gdstk

    if inductor.turns not in (1, 2):
        raise ValueError("Only 1-turn and 2-turn inductors are currently supported")

    if inductor.turns == 2:
        layout_spec = _build_inductor_layout_spec(
            geometry=inductor,
            center_x_um=center_x_um,
            center_y_um=0.0,
            bridge_anchor_gap_cap_um=bridge_anchor_gap_cap_um,
            metal_layer=metal_layer,
            metal_datatype=metal_datatype,
            layer_datatypes=layer_datatypes,
            mirror_x=mirror_x,
        )
        return _build_center_tapped_inductor(
            cell,
            layout_spec,
            include_center_tap_feed=inductor.center_tap,
            center_tap_target_x_um=center_tap_target_x_um,
            center_tap_width_um=center_tap_width_um,
        )

    points, terminals = _build_winding(
        side=side,
        inductor=inductor,
        center_x_um=center_x_um,
    )
    path = gdstk.FlexPath(
        points,
        inductor.trace_width_um,
        layer=metal_layer,
        datatype=metal_datatype,
        joins="miter",
        ends="flush",
    )
    cell.add(*path.to_polygons())

    center_tap_point = None
    center_tap_anchor = None
    if inductor.center_tap:
        tap_side = "right" if side == "left" else "left"
        center_tap_anchor = (
            center_x_um + (0.5 * inductor.outer_width_um if tap_side == "right" else -0.5 * inductor.outer_width_um),
            0.0,
        )
        center_tap_point = _add_single_turn_center_tap(
            cell=cell,
            side=side,
            inductor=inductor,
            center_x_um=center_x_um,
            target_x_um=center_tap_target_x_um,
            metal_layer=metal_layer,
            metal_datatype=metal_datatype,
            center_tap_width_um=center_tap_width_um,
        )
    return InductorTerminals(
        top=terminals[0],
        bottom=terminals[1],
        center_tap=center_tap_point,
        center_tap_anchor=center_tap_anchor,
    )


def _build_inductor_layout_spec(
    *,
    geometry: InductorSpec,
    center_x_um: float,
    center_y_um: float,
    bridge_anchor_gap_cap_um: float | None,
    metal_layer: int,
    metal_datatype: int = 0,
    layer_datatypes: tuple[tuple[int, int], ...] = tuple(),
    mirror_x: bool,
) -> InductorLayoutSpec:
    if geometry.turns != 2:
        raise ValueError("The center-tapped inductor template is only defined for 2-turn windings")
    if geometry.bridge_layer is None:
        raise ValueError("Two-turn inductors require bridge_layer to be set in the inductor spec")
    # Center the crossover on the straight side opposite the ports.
    bridge_offset_y_um = 0.0
    return InductorLayoutSpec(
        geometry=geometry,
        center_x_um=center_x_um,
        center_y_um=center_y_um,
        bridge_offset_y_um=bridge_offset_y_um,
        bridge_anchor_gap_cap_um=bridge_anchor_gap_cap_um,
        metal_layer=metal_layer,
        layer_datatypes=(
            ((int(metal_layer), int(metal_datatype)),)
            if not layer_datatypes
            else tuple((int(layer), int(datatype)) for layer, datatype in layer_datatypes)
        ),
        bridge_layer=int(geometry.bridge_layer),
        bridge_via_layer=geometry.bridge_via_layer,
        bridge_lower_layer=geometry.bridge_lower_layer,
        bridge_lower_via_layer=geometry.bridge_lower_via_layer,
        mirror_x=mirror_x,
    )


def _ordered_draw_layers(
    *,
    run_config: TransformerRunConfig,
    primary_geometry: InductorSpec,
    secondary_geometry: InductorSpec,
) -> tuple[int, ...]:
    ordered = [
        run_config.emx.shield_layer,
        run_config.emx.m5_layer,
        None if primary_geometry.vdd_bar is None else primary_geometry.vdd_bar.bar_via_layer,
        None if primary_geometry.vdd_bar is None else primary_geometry.vdd_bar.bar_layer,
        None if primary_geometry.vdd_bar is None else primary_geometry.vdd_bar.route_via_layer,
        None if primary_geometry.vdd_bar is None else primary_geometry.vdd_bar.route_layer,
        None if secondary_geometry.vdd_bar is None else secondary_geometry.vdd_bar.bar_via_layer,
        None if secondary_geometry.vdd_bar is None else secondary_geometry.vdd_bar.bar_layer,
        None if secondary_geometry.vdd_bar is None else secondary_geometry.vdd_bar.route_via_layer,
        None if secondary_geometry.vdd_bar is None else secondary_geometry.vdd_bar.route_layer,
        secondary_geometry.bridge_via_layer,
        secondary_geometry.bridge_layer,
        primary_geometry.bridge_lower_via_layer,
        primary_geometry.bridge_lower_layer,
        run_config.emx.m9_layer,
        primary_geometry.bridge_via_layer,
        primary_geometry.bridge_layer,
        run_config.emx.ap_layer,
    ]
    # Keep the last occurrence of each layer so a layer reused for multiple
    # roles (for example VDD bars on AP/M10) preserves its highest intended
    # draw rank in the composite preview.
    result_reversed: list[int] = []
    seen: set[int] = set()
    for layer in reversed(ordered):
        if layer is None:
            continue
        layer_int = int(layer)
        if layer_int in seen:
            continue
        seen.add(layer_int)
        result_reversed.append(layer_int)
    return tuple(reversed(result_reversed))

def _add_single_turn_center_tap(
    *,
    cell,
    side: str,
    inductor: InductorSpec,
    center_x_um: float,
    target_x_um: float | None,
    metal_layer: int,
    metal_datatype: int,
    center_tap_width_um: float | None = None,
) -> tuple[float, float]:
    tap_side = "right" if side == "left" else "left"
    x_edge_um = center_x_um + (0.5 * inductor.outer_width_um if tap_side == "right" else -0.5 * inductor.outer_width_um)
    target_x_um = float(
        x_edge_um + (inductor.feed_extension_um if tap_side == "right" else -inductor.feed_extension_um)
        if target_x_um is None
        else target_x_um
    )
    if tap_side == "right":
        target_x_um = max(target_x_um, x_edge_um)
    else:
        target_x_um = min(target_x_um, x_edge_um)
    tap_width_um = inductor.trace_width_um if center_tap_width_um is None else float(center_tap_width_um)
    stub = _side_stub(
        tap_side,
        x_edge_um,
        0.0,
        abs(target_x_um - x_edge_um),
        tap_width_um,
        metal_layer,
    )
    stub.datatype = metal_datatype
    cell.add(stub)
    return (target_x_um, 0.0)


def _build_center_tapped_inductor(
    cell,
    spec: InductorLayoutSpec,
    *,
    include_center_tap_feed: bool = True,
    center_tap_target_x_um: float | None = None,
    center_tap_width_um: float | None = None,
) -> InductorTerminals:
    bundle = _build_center_tapped_inductor_geometry(
        spec,
        include_center_tap_feed=include_center_tap_feed,
        center_tap_target_x_um=center_tap_target_x_um,
        center_tap_width_um=center_tap_width_um,
    )
    for polygon in _normalize_polygons_for_export(bundle.coil_polygons, spec.layer_datatypes):
        cell.add(polygon)
    for polygon in _normalize_polygons_for_export(bundle.bridge_polygons, spec.layer_datatypes):
        cell.add(polygon)
    for polygon in _normalize_polygons_for_export(bundle.via_polygons, spec.layer_datatypes):
        cell.add(polygon)
    return bundle.terminals


def _build_gdstk_check_bundle(
    *,
    side: str,
    inductor: InductorSpec,
    center_x_um: float,
    bridge_anchor_gap_cap_um: float | None,
    metal_layer: int,
    mirror_x: bool,
) -> CenterTappedInductorGeometry | None:
    if inductor.turns != 2:
        return None
    layout_spec = _build_inductor_layout_spec(
        geometry=inductor,
        center_x_um=center_x_um,
        center_y_um=0.0,
        bridge_anchor_gap_cap_um=bridge_anchor_gap_cap_um,
        metal_layer=metal_layer,
        mirror_x=mirror_x,
    )
    return _build_center_tapped_inductor_geometry(
        layout_spec,
        include_center_tap_feed=inductor.center_tap,
    )


def _build_center_tapped_inductor_geometry(
    spec: InductorLayoutSpec,
    *,
    include_center_tap_feed: bool = True,
    center_tap_target_x_um: float | None = None,
    center_tap_width_um: float | None = None,
) -> CenterTappedInductorGeometry:
    import gdstk

    outer_edge_x = spec.outer_half_width_um
    inner_edge_x = spec.inner_half_width_um
    outer_center_x = outer_edge_x - spec.trace_width_um * 0.5
    inner_center_x = inner_edge_x - spec.trace_width_um * 0.5
    crossover_dx = outer_center_x - inner_center_x
    half_crossover_dy = 0.5 * crossover_dx
    connector_len = 1.05 * spec.trace_width_um
    half_terminal_span = _resolved_terminal_half_span(
        spec.outer_half_width_um,
        spec.outer_half_height_um,
        spec.terminal_y_span_um,
    )

    upper_site_y = spec.bridge_offset_y_um + half_crossover_dy
    lower_site_y = spec.bridge_offset_y_um - half_crossover_dy
    pad_height_um = max(
        1.0e-6,
        0.5 * spec.trace_width_um * _bridge_section_pad_height_ratio(spec),
    )
    site_gap_height_um = max(1.0e-6, pad_height_um + 0.1 * spec.trace_width_um)
    half_site_span_um = 0.5 * (site_gap_height_um + pad_height_um)
    pad_width_um = max(
        1.0e-6,
        spec.trace_width_um * _bridge_section_pad_width_ratio(spec),
    )
    interface_pad_width_um = max(spec.trace_width_um, pad_width_um)
    via_size_ratio = _bridge_section_via_size_ratio(spec)
    outer_bridge_pad_center = (
        outer_center_x,
        upper_site_y + half_site_span_um,
    )
    inner_bridge_pad_center = (
        inner_center_x,
        lower_site_y - half_site_span_um,
    )
    underpass_start_pad_center = (
        outer_center_x,
        lower_site_y - half_site_span_um,
    )
    underpass_end_pad_center = (
        inner_center_x,
        upper_site_y + half_site_span_um,
    )
    crossing_pad_height_um = _required_equal_pad_height_for_45_degree(
        start_center=underpass_start_pad_center,
        end_center=underpass_end_pad_center,
        minimum_height_um=pad_height_um,
    )
    center_gap_lower_y = max(
        underpass_start_pad_center[1] + 0.5 * crossing_pad_height_um,
        inner_bridge_pad_center[1] + 0.5 * pad_height_um,
    )
    center_gap_upper_y = min(
        outer_bridge_pad_center[1] - 0.5 * pad_height_um,
        underpass_end_pad_center[1] - 0.5 * crossing_pad_height_um,
    )
    center_gap_height_um = max(1.0e-6, center_gap_upper_y - center_gap_lower_y)
    center_gap_center_y = 0.5 * (center_gap_lower_y + center_gap_upper_y)

    coil_polygons: list[object] = []
    crossing_pad_polygons: list[object] = []
    crossing_polygons: list[object] = []
    bridge_polygons: list[object] = []
    bridge_overlap_pads: list[object] = []

    primary_outer = _octagon_ring(
        spec.outer_half_width_um,
        spec.outer_half_height_um,
        spec.trace_width_um,
        spec.metal_layer,
    )
    primary_outer = _cut_side_gap(
        primary_outer,
        x_edge_um=-spec.outer_half_width_um,
        center_y_um=0.0,
        gap_height_um=_mid_gap_height(half_terminal_span, -half_terminal_span, spec.trace_width_um),
        gap_width_um=1.35 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    primary_outer = _cut_side_gap(
        primary_outer,
        x_edge_um=spec.outer_half_width_um,
        center_y_um=upper_site_y,
        gap_height_um=site_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    primary_outer = _cut_side_gap(
        primary_outer,
        x_edge_um=spec.outer_half_width_um,
        center_y_um=lower_site_y,
        gap_height_um=site_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    primary_outer = _cut_side_gap(
        primary_outer,
        x_edge_um=spec.outer_half_width_um,
        center_y_um=center_gap_center_y,
        gap_height_um=center_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    coil_polygons.extend(primary_outer)

    primary_inner = _octagon_ring(
        spec.inner_half_width_um,
        spec.inner_half_height_um,
        spec.trace_width_um,
        spec.metal_layer,
    )
    primary_inner = _cut_side_gap(
        primary_inner,
        x_edge_um=spec.inner_half_width_um,
        center_y_um=upper_site_y,
        gap_height_um=site_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    primary_inner = _cut_side_gap(
        primary_inner,
        x_edge_um=spec.inner_half_width_um,
        center_y_um=lower_site_y,
        gap_height_um=site_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    primary_inner = _cut_side_gap(
        primary_inner,
        x_edge_um=spec.inner_half_width_um,
        center_y_um=center_gap_center_y,
        gap_height_um=center_gap_height_um,
        gap_width_um=0.9 * spec.trace_width_um,
        layer=spec.metal_layer,
    )
    coil_polygons.extend(primary_inner)

    left_pin_start_x = -spec.outer_half_width_um - spec.feed_extension_um
    local_center_tap_target_x_um = (
        left_pin_start_x
        if center_tap_target_x_um is None
        else _inverse_transform_x(center_tap_target_x_um, spec)
    )
    local_center_tap_target_x_um = min(local_center_tap_target_x_um, -spec.inner_half_width_um)
    bottom_feed_start_x = -spec.inner_half_width_um if spec.turns > 1 else -spec.outer_half_width_um
    primary_stubs = [
        _side_stub(
            "left",
            -spec.outer_half_width_um,
            half_terminal_span,
            spec.feed_extension_um,
            spec.trace_width_um,
            spec.metal_layer,
        ),
        _side_stub(
            "left",
            -spec.outer_half_width_um,
            -half_terminal_span,
            spec.feed_extension_um,
            spec.trace_width_um,
            spec.metal_layer,
        ),
    ]
    top_stub = primary_stubs[0]
    bottom_stub = primary_stubs[-1]
    center_tap_stub = None
    if include_center_tap_feed:
        resolved_center_tap_width_um = (
            spec.trace_width_um if center_tap_width_um is None else float(center_tap_width_um)
        )
        center_tap_stub = _stub_between_x(
            local_center_tap_target_x_um,
            -spec.inner_half_width_um,
            0.0,
            resolved_center_tap_width_um,
            spec.metal_layer,
        )
        primary_stubs.insert(1, center_tap_stub)
    coil_polygons.extend(primary_stubs)

    bridge_start = (inner_center_x, lower_site_y)
    bridge_end = (outer_center_x, upper_site_y)
    crossover_layer = int(spec.bridge_lower_layer) if spec.bridge_lower_layer is not None else int(spec.bridge_layer)
    anchor_layer = int(spec.bridge_layer)
    underpass_start_route_center = underpass_start_pad_center
    underpass_end_route_center = underpass_end_pad_center
    underpass_outer_anchor_pad = _pad_from_center(
        center=underpass_start_pad_center,
        width_um=interface_pad_width_um,
        height_um=crossing_pad_height_um,
        layer=spec.metal_layer,
        datatype=9,
    )
    underpass_inner_anchor_pad = _pad_from_center(
        center=underpass_end_pad_center,
        width_um=interface_pad_width_um,
        height_um=crossing_pad_height_um,
        layer=spec.metal_layer,
        datatype=9,
    )
    crossing_pad_polygons.extend([underpass_outer_anchor_pad, underpass_inner_anchor_pad])
    crossing_polygons.extend(
        _vertical_45_polyline_route(
            start=underpass_start_route_center,
            end=underpass_end_route_center,
            width_um=spec.trace_width_um,
            layer=spec.metal_layer,
            datatype=8,
        )
    )

    start_pad_center = inner_bridge_pad_center
    end_pad_center = outer_bridge_pad_center
    bridge_pad_height_um = _required_equal_pad_height_for_45_degree(
        start_center=start_pad_center,
        end_center=end_pad_center,
        minimum_height_um=pad_height_um,
    )
    via_pad_width_um = max(1.0e-6, min(pad_width_um, pad_width_um * via_size_ratio))
    via_pad_height_um = max(
        1.0e-6,
        min(bridge_pad_height_um, bridge_pad_height_um * via_size_ratio),
    )
    bridge_route_start = start_pad_center
    bridge_route_end = end_pad_center
    bridge_path_polygons = _vertical_45_polyline_route(
        start=bridge_route_start,
        end=bridge_route_end,
        width_um=spec.trace_width_um,
        layer=crossover_layer,
    )
    bridge_polygons.extend(bridge_path_polygons)
    inner_overlap_pad = _pad_from_center(
        center=start_pad_center,
        width_um=interface_pad_width_um,
        height_um=bridge_pad_height_um,
        layer=crossover_layer,
        datatype=10,
    )
    bridge_polygons.append(inner_overlap_pad)
    bridge_overlap_pads.append(inner_overlap_pad)
    outer_overlap_pad = _pad_from_center(
        center=end_pad_center,
        width_um=interface_pad_width_um,
        height_um=bridge_pad_height_um,
        layer=crossover_layer,
        datatype=10,
    )
    bridge_polygons.append(outer_overlap_pad)
    bridge_overlap_pads.append(outer_overlap_pad)

    merged_coil = gdstk.boolean(coil_polygons, [], "or", layer=spec.metal_layer, datatype=0)
    transformed_coil = (
        tuple(_transformed_polygons(merged_coil if merged_coil is not None else coil_polygons, spec))
        + tuple(_transformed_polygons(crossing_pad_polygons, spec))
        + tuple(_transformed_polygons(crossing_polygons, spec))
    )
    merged_bridge = gdstk.boolean(bridge_polygons, [], "or", layer=crossover_layer, datatype=0)
    all_bridge_polygons = list(merged_bridge if merged_bridge is not None else bridge_polygons)
    via_polygon_groups: list[object] = []
    inner_upper_vias: tuple[object, ...] = tuple()
    outer_upper_vias: tuple[object, ...] = tuple()
    inner_lower_vias: tuple[object, ...] = tuple()
    outer_lower_vias: tuple[object, ...] = tuple()
    inner_intermediate_stage: tuple[object, ...] = tuple()
    outer_intermediate_stage: tuple[object, ...] = tuple()
    inner_bridge_stage: tuple[object, ...] = (inner_overlap_pad,)
    outer_bridge_stage: tuple[object, ...] = (outer_overlap_pad,)

    if spec.bridge_lower_layer is not None:
        anchor_inner_overlap_pad = _pad_from_center(
            center=start_pad_center,
            width_um=interface_pad_width_um,
            height_um=bridge_pad_height_um,
            layer=anchor_layer,
            datatype=11,
        )
        anchor_outer_overlap_pad = _pad_from_center(
            center=end_pad_center,
            width_um=interface_pad_width_um,
            height_um=bridge_pad_height_um,
            layer=anchor_layer,
            datatype=11,
        )
        anchor_bridge_polygons = [anchor_inner_overlap_pad, anchor_outer_overlap_pad]
        inner_intermediate_stage = (anchor_inner_overlap_pad,)
        outer_intermediate_stage = (anchor_outer_overlap_pad,)
        merged_anchor_bridge = gdstk.boolean(
            anchor_bridge_polygons,
            [],
            "or",
            layer=anchor_layer,
            datatype=0,
        )
        all_bridge_polygons.extend(merged_anchor_bridge if merged_anchor_bridge is not None else anchor_bridge_polygons)

        if spec.bridge_via_layer is not None:
            inner_upper_vias = tuple(
                _via_grid_polygons(
                    spec=spec,
                    center=start_pad_center,
                    width_um=via_pad_width_um,
                    height_um=via_pad_height_um,
                    layer=int(spec.bridge_via_layer),
                    datatype=12,
                )
            )
            outer_upper_vias = tuple(
                _via_grid_polygons(
                    spec=spec,
                    center=end_pad_center,
                    width_um=via_pad_width_um,
                    height_um=via_pad_height_um,
                    layer=int(spec.bridge_via_layer),
                    datatype=12,
                )
            )
            via_polygon_groups.extend(inner_upper_vias)
            via_polygon_groups.extend(outer_upper_vias)
        if spec.bridge_lower_via_layer is not None:
            inner_lower_vias = tuple(
                _via_grid_polygons(
                    spec=spec,
                    center=start_pad_center,
                    width_um=via_pad_width_um,
                    height_um=via_pad_height_um,
                    layer=int(spec.bridge_lower_via_layer),
                    datatype=13,
                )
            )
            outer_lower_vias = tuple(
                _via_grid_polygons(
                    spec=spec,
                    center=end_pad_center,
                    width_um=via_pad_width_um,
                    height_um=via_pad_height_um,
                    layer=int(spec.bridge_lower_via_layer),
                    datatype=13,
                )
            )
            via_polygon_groups.extend(inner_lower_vias)
            via_polygon_groups.extend(outer_lower_vias)
    elif spec.bridge_via_layer is not None:
        inner_intermediate_stage = (inner_overlap_pad,)
        outer_intermediate_stage = (outer_overlap_pad,)
        inner_upper_vias = tuple(
            _via_grid_polygons(
                spec=spec,
                center=start_pad_center,
                width_um=via_pad_width_um,
                height_um=via_pad_height_um,
                layer=int(spec.bridge_via_layer),
                datatype=12,
            )
        )
        outer_upper_vias = tuple(
            _via_grid_polygons(
                spec=spec,
                center=end_pad_center,
                width_um=via_pad_width_um,
                height_um=via_pad_height_um,
                layer=int(spec.bridge_via_layer),
                datatype=12,
            )
        )
        via_polygon_groups.extend(inner_upper_vias)
        via_polygon_groups.extend(outer_upper_vias)
    else:
        inner_intermediate_stage = (inner_overlap_pad,)
        outer_intermediate_stage = (outer_overlap_pad,)

    transformed_bridge = tuple(_transformed_polygons(all_bridge_polygons, spec))
    transformed_via = tuple(_transformed_polygons(via_polygon_groups, spec))
    transformed_intermediate_bridge_pads = tuple(
        _transformed_polygons(
            tuple(poly for poly in all_bridge_polygons if int(poly.layer) == int(anchor_layer)),
            spec,
        )
    )
    transformed_top_feed = tuple(_transformed_polygons((top_stub,), spec))
    transformed_center_feed = (
        tuple(_transformed_polygons((center_tap_stub,), spec)) if center_tap_stub is not None else tuple()
    )
    transformed_bottom_feed = tuple(_transformed_polygons((bottom_stub,), spec))
    transformed_crossing_pads = tuple(_transformed_polygons(crossing_pad_polygons, spec))
    transformed_crossing = tuple(_transformed_polygons(crossing_polygons, spec))

    coil_groups = tuple(
        LayerPolygonGroup(layer=int(layer), polygons=polys, role="coil")
        for layer, polys in _group_polygons_by_layer(transformed_coil).items()
    )
    feed_groups = tuple(
        group
        for group in (
            LayerPolygonGroup(layer=int(spec.metal_layer), polygons=transformed_top_feed, role="top_feed") if transformed_top_feed else None,
            LayerPolygonGroup(layer=int(spec.metal_layer), polygons=transformed_center_feed, role="center_feed") if transformed_center_feed else None,
            LayerPolygonGroup(layer=int(spec.metal_layer), polygons=transformed_bottom_feed, role="bottom_feed") if transformed_bottom_feed else None,
        )
        if group is not None
    )
    crossing_groups = tuple(
        LayerPolygonGroup(layer=int(layer), polygons=polys, role="crossing")
        for layer, polys in _group_polygons_by_layer(transformed_crossing).items()
    )
    crossing_pad_groups = tuple(
        LayerPolygonGroup(layer=int(layer), polygons=polys, role="crossing_pad")
        for layer, polys in _group_polygons_by_layer(transformed_crossing_pads).items()
    )
    bridge_groups = tuple(
        LayerPolygonGroup(layer=int(layer), polygons=polys, role="bridge")
        for layer, polys in _group_polygons_by_layer(transformed_bridge).items()
    )
    via_groups = tuple(
        LayerPolygonGroup(layer=int(layer), polygons=polys, role="via")
        for layer, polys in _group_polygons_by_layer(transformed_via).items()
    )
    explicit_stage_polygons = {
        ("inner", "intermediate_pad"): tuple(_transformed_polygons(inner_intermediate_stage, spec)),
        ("outer", "intermediate_pad"): tuple(_transformed_polygons(outer_intermediate_stage, spec)),
        ("inner", "bridge_pad"): tuple(_transformed_polygons(inner_bridge_stage, spec)),
        ("outer", "bridge_pad"): tuple(_transformed_polygons(outer_bridge_stage, spec)),
        ("inner", "upper_via_pad"): tuple(_transformed_polygons(inner_upper_vias, spec)),
        ("outer", "upper_via_pad"): tuple(_transformed_polygons(outer_upper_vias, spec)),
        ("inner", "lower_via_pad"): tuple(_transformed_polygons(inner_lower_vias, spec)),
        ("outer", "lower_via_pad"): tuple(_transformed_polygons(outer_lower_vias, spec)),
    }
    bridge_endpoint_stacks = _build_bridge_endpoint_stacks(
        spec=spec,
        start_pad_center=start_pad_center,
        end_pad_center=end_pad_center,
        bridge_pad_width_um=pad_width_um,
        bridge_pad_height_um=bridge_pad_height_um,
        via_pad_width_um=via_pad_width_um,
        via_pad_height_um=via_pad_height_um,
        explicit_stage_polygons=explicit_stage_polygons,
    )

    top_window = _shrink_rectangles(
        (
            _transform_polygon(
                gdstk.rectangle(
                    (-spec.outer_half_width_um, 0.5 * spec.trace_width_um),
                    (-spec.outer_half_width_um - spec.feed_extension_um, half_terminal_span - 0.5 * spec.trace_width_um),
                    layer=spec.metal_layer,
                    datatype=0,
                ),
                spec,
            ),
        ),
        0.0,
        0.0,
    )
    bottom_window = _shrink_rectangles(
        (
            _transform_polygon(
                gdstk.rectangle(
                    (bottom_feed_start_x, -half_terminal_span + 0.5 * spec.trace_width_um),
                    (-spec.outer_half_width_um - spec.feed_extension_um, -0.5 * spec.trace_width_um),
                    layer=spec.metal_layer,
                    datatype=0,
                ),
                spec,
            ),
        ),
        0.0,
        0.0,
    )

    return CenterTappedInductorGeometry(
        coil_polygons=transformed_coil,
        bridge_polygons=transformed_bridge,
        via_polygons=transformed_via,
        intermediate_bridge_pad_polygons=transformed_intermediate_bridge_pads,
        top_feed_polygons=transformed_top_feed,
        center_feed_polygons=transformed_center_feed,
        bottom_feed_polygons=transformed_bottom_feed,
        outer_anchor_pad=tuple(_transformed_polygons((outer_overlap_pad,), spec)),
        inner_anchor_pad=tuple(_transformed_polygons((inner_overlap_pad,), spec)),
        top_terminal_window=tuple(top_window),
        bottom_terminal_window=tuple(bottom_window),
        terminals=InductorTerminals(
            top=_transform_point((-spec.outer_half_width_um - spec.feed_extension_um, half_terminal_span), spec),
            bottom=_transform_point((-spec.outer_half_width_um - spec.feed_extension_um, -half_terminal_span), spec),
            center_tap=_transform_point((local_center_tap_target_x_um, 0.0), spec) if include_center_tap_feed else None,
            center_tap_anchor=_transform_point((-spec.inner_half_width_um, 0.0), spec) if include_center_tap_feed else None,
        ),
        coil_groups=coil_groups,
        feed_groups=feed_groups,
        crossing_groups=crossing_groups,
        crossing_pad_groups=crossing_pad_groups,
        bridge_groups=bridge_groups,
        via_groups=via_groups,
        bridge_endpoint_stacks=bridge_endpoint_stacks,
    )


def _octagon_vertices(
    half_width_um: float,
    half_height_um: float,
    center_x_um: float = 0.0,
    center_y_um: float = 0.0,
) -> list[tuple[float, float]]:
    chamfer = _octagon_chamfer_um(half_width_um, half_height_um)
    return [
        (center_x_um - half_width_um + chamfer, center_y_um + half_height_um),
        (center_x_um + half_width_um - chamfer, center_y_um + half_height_um),
        (center_x_um + half_width_um, center_y_um + half_height_um - chamfer),
        (center_x_um + half_width_um, center_y_um - half_height_um + chamfer),
        (center_x_um + half_width_um - chamfer, center_y_um - half_height_um),
        (center_x_um - half_width_um + chamfer, center_y_um - half_height_um),
        (center_x_um - half_width_um, center_y_um - half_height_um + chamfer),
        (center_x_um - half_width_um, center_y_um + half_height_um - chamfer),
    ]


def _octagon_chamfer_um(half_width_um: float, half_height_um: float) -> float:
    return min(float(half_width_um), float(half_height_um)) * (math.sqrt(2.0) - 1.0)


def _octagon_ring(half_width_um: float, half_height_um: float, width_um: float, layer: int):
    import gdstk

    outer = gdstk.Polygon(_octagon_vertices(half_width_um, half_height_um), layer=layer, datatype=0)
    inner = gdstk.Polygon(
        _octagon_vertices(half_width_um - width_um, half_height_um - width_um),
        layer=layer,
        datatype=0,
    )
    result = gdstk.boolean([outer], [inner], "not", layer=layer, datatype=0)
    return result if result is not None else [outer]


def _cut_side_gap(polygons, x_edge_um: float, center_y_um: float, gap_height_um: float, gap_width_um: float, layer: int):
    import gdstk

    x_margin = 0.5 * gap_width_um
    cut = gdstk.rectangle(
        (x_edge_um - gap_width_um - x_margin, center_y_um - gap_height_um * 0.5),
        (x_edge_um + gap_width_um + x_margin, center_y_um + gap_height_um * 0.5),
        layer=layer,
        datatype=0,
    )
    result = gdstk.boolean(polygons, [cut], "not", layer=layer, datatype=0)
    return result if result is not None else polygons


def _mid_gap_height(feed_top_y_um: float, feed_bot_y_um: float, feed_width_um: float) -> float:
    separation_um = abs(feed_top_y_um - feed_bot_y_um)
    return max(feed_width_um * 0.25, separation_um - feed_width_um)


def _anchor_gap_bounds(
    bridge_y_um: float,
    underpass_y_um: float,
    trace_width_um: float,
    gap_height_cap_um: float | None = None,
) -> tuple[float, float]:
    bridge_margin_um = 0.6 * trace_width_um
    underpass_margin_um = -0.2 * trace_width_um
    lower_y = min(bridge_y_um - bridge_margin_um, underpass_y_um - underpass_margin_um)
    upper_y = max(bridge_y_um + bridge_margin_um, underpass_y_um + underpass_margin_um)
    if gap_height_cap_um is not None:
        current_height_um = upper_y - lower_y
        capped_height_um = max(1.0e-6, min(current_height_um, gap_height_cap_um))
        center_y_um = 0.5 * (lower_y + upper_y)
        half_height_um = 0.5 * capped_height_um
        lower_y = center_y_um - half_height_um
        upper_y = center_y_um + half_height_um
    return lower_y, upper_y


def _side_stub(side: str, x_edge_um: float, y_center_um: float, length_um: float, width_um: float, layer: int):
    import gdstk

    if side == "left":
        return gdstk.rectangle(
            (x_edge_um - length_um, y_center_um - width_um * 0.5),
            (x_edge_um, y_center_um + width_um * 0.5),
            layer=layer,
            datatype=0,
        )
    return gdstk.rectangle(
        (x_edge_um, y_center_um - width_um * 0.5),
        (x_edge_um + length_um, y_center_um + width_um * 0.5),
        layer=layer,
        datatype=0,
    )


def _stub_between_x(x_start_um: float, x_end_um: float, y_center_um: float, width_um: float, layer: int):
    import gdstk

    return gdstk.rectangle(
        (min(x_start_um, x_end_um), y_center_um - width_um * 0.5),
        (max(x_start_um, x_end_um), y_center_um + width_um * 0.5),
        layer=layer,
        datatype=0,
    )


def _overlap_pad_vertical(x_center_um: float, y0_um: float, y1_um: float, width_um: float, layer: int):
    import gdstk

    half_width_um = 0.5 * width_um
    return gdstk.rectangle(
        (x_center_um - half_width_um, min(y0_um, y1_um)),
        (x_center_um + half_width_um, max(y0_um, y1_um)),
        layer=layer,
        datatype=0,
    )


def _flush_anchor_pad(
    *,
    x_center_um: float,
    y_center_um: float,
    width_um: float,
    height_ratio: float,
    corner: str,
    layer: int,
):
    import gdstk

    half_width_um = 0.5 * width_um
    half_height_um = 0.5 * max(1.0e-6, float(height_ratio) * width_um)
    chamfer_um = min(half_width_um, half_height_um)
    x0 = x_center_um - half_width_um
    x1 = x_center_um + half_width_um
    y0 = y_center_um - half_height_um
    y1 = y_center_um + half_height_um

    if corner == "lower_left":
        points = [
            (x0, y0 + chamfer_um),
            (x0 + chamfer_um, y0),
            (x1, y0),
            (x1, y1),
            (x0, y1),
        ]
    elif corner == "upper_right":
        points = [
            (x0, y0),
            (x1, y0),
            (x1, y1 - chamfer_um),
            (x1 - chamfer_um, y1),
            (x0, y1),
        ]
    else:
        raise ValueError(f"Unsupported flush-anchor corner: {corner}")

    return gdstk.Polygon(points, layer=layer, datatype=0)


def _pad_bounds_from_center(
    *,
    center: tuple[float, float],
    width_um: float,
    height_um: float,
) -> tuple[float, float, float, float]:
    half_width_um = 0.5 * width_um
    half_height_um = 0.5 * height_um
    x_mid_um, y_mid_um = center
    return (
        x_mid_um - half_width_um,
        y_mid_um - half_height_um,
        x_mid_um + half_width_um,
        y_mid_um + half_height_um,
    )


def _pad_from_center(
    *,
    center: tuple[float, float],
    width_um: float,
    height_um: float,
    layer: int,
    datatype: int = 0,
):
    import gdstk

    x0, y0, x1, y1 = _pad_bounds_from_center(
        center=center,
        width_um=width_um,
        height_um=height_um,
    )
    return gdstk.rectangle((x0, y0), (x1, y1), layer=layer, datatype=datatype)


def _pad_side_anchors(
    center: tuple[float, float],
    *,
    side: str,
    width_um: float,
    height_um: float,
    left_to_right: bool = True,
) -> tuple[tuple[float, float], tuple[float, float]]:
    x0, y0, x1, y1 = _pad_bounds_from_center(
        center=center,
        width_um=width_um,
        height_um=height_um,
    )
    if side == "top":
        anchors = ((x0, y1), (x1, y1))
    elif side == "bottom":
        anchors = ((x0, y0), (x1, y0))
    elif side == "left":
        anchors = ((x0, y1), (x0, y0))
    elif side == "right":
        anchors = ((x1, y1), (x1, y0))
    else:
        raise ValueError(f"Unsupported pad-side anchor request: side={side}")
    if left_to_right:
        return anchors
    return (anchors[1], anchors[0])


def _segment_unit_vector(
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0.0:
        raise ValueError("Unit vector requires distinct segment endpoints")
    return (dx / length, dy / length)


def _anchor_edge_pad(
    *,
    upper_anchor: tuple[float, float],
    lower_anchor: tuple[float, float],
    extension_vector: tuple[float, float],
    layer: int,
):
    import gdstk

    points = [
        upper_anchor,
        lower_anchor,
        (lower_anchor[0] + extension_vector[0], lower_anchor[1] + extension_vector[1]),
        (upper_anchor[0] + extension_vector[0], upper_anchor[1] + extension_vector[1]),
    ]
    return gdstk.Polygon(points, layer=layer, datatype=0)


def _bridge_polygon_from_corner_anchors(
    anchors: BridgeCornerAnchors,
    layer: int,
    datatype: int = 0,
):
    import gdstk

    return [
        gdstk.Polygon(
            [
                anchors.start_upper,
                anchors.end_upper,
                anchors.end_lower,
                anchors.start_lower,
            ],
            layer=layer,
            datatype=datatype,
        )
    ]


def _reference_routing_geometric_45_points(
    *,
    width_um: float,
    spacing_um: float,
    center_x_um: float,
    center_y_um: float,
    extend_um: float,
    flip_x: bool = False,
    flip_y: bool = False,
):
    g_um = (math.sqrt(2.0) - 1.0) * spacing_um
    d_um = (math.sqrt(2.0) - 1.0) * width_um
    h_um = width_um + spacing_um + (math.sqrt(2.0) - 1.0) * (2.0 * spacing_um + width_um)

    x_upper = [-0.5 * h_um, -0.5 * h_um + g_um, 0.5 * h_um - g_um - d_um, 0.5 * h_um]
    y_upper = [-0.5 * spacing_um, -0.5 * spacing_um, 0.5 * spacing_um + width_um, 0.5 * spacing_um + width_um]
    x_lower = [-0.5 * h_um, -0.5 * h_um + g_um + d_um, 0.5 * h_um - g_um, 0.5 * h_um]
    y_lower = [-0.5 * spacing_um - width_um, -0.5 * spacing_um - width_um, 0.5 * spacing_um, 0.5 * spacing_um]

    if extend_um > 0.0:
        x_upper = [-0.5 * h_um - extend_um] + x_upper + [0.5 * h_um + extend_um]
        y_upper = [-0.5 * spacing_um] + y_upper + [0.5 * spacing_um + width_um]
        x_lower = [-0.5 * h_um - extend_um] + x_lower + [0.5 * h_um + extend_um]
        y_lower = [-0.5 * spacing_um - width_um] + y_lower + [0.5 * spacing_um]

    points = list(zip(x_upper + x_lower[::-1], y_upper + y_lower[::-1]))
    if flip_x:
        points = [(-x, y) for x, y in points]
    if flip_y:
        points = [(x, -y) for x, y in points]
    return [(x + center_x_um, y + center_y_um) for x, y in points]


def _reference_routing_geometric_45(
    *,
    width_um: float,
    spacing_um: float,
    center_x_um: float,
    center_y_um: float,
    extend_um: float,
    layer: int,
    datatype: int = 0,
    flip_x: bool = False,
    flip_y: bool = False,
):
    import gdstk

    points = _reference_routing_geometric_45_points(
        width_um=width_um,
        spacing_um=spacing_um,
        center_x_um=center_x_um,
        center_y_um=center_y_um,
        extend_um=extend_um,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    return [gdstk.Polygon(points, layer=layer, datatype=datatype)]


def _routing_geometric_45_from_horizontal_anchors(
    *,
    start_left: tuple[float, float],
    start_right: tuple[float, float],
    end_left: tuple[float, float],
    end_right: tuple[float, float],
    layer: int,
    datatype: int = 0,
):
    width_um = max(1.0e-6, abs(start_right[0] - start_left[0]))
    start_center = (0.5 * (start_left[0] + start_right[0]), 0.5 * (start_left[1] + start_right[1]))
    end_center = (0.5 * (end_left[0] + end_right[0]), 0.5 * (end_left[1] + end_right[1]))
    dx_um = end_center[0] - start_center[0]
    dy_um = end_center[1] - start_center[1]
    spacing_um = max(0.0, abs(dy_um) - width_um)
    base_span_um = width_um + spacing_um + (math.sqrt(2.0) - 1.0) * (2.0 * spacing_um + width_um)
    extend_um = max(0.0, 0.5 * (abs(dx_um) - base_span_um))
    return _reference_routing_geometric_45(
        width_um=width_um,
        spacing_um=spacing_um,
        center_x_um=0.5 * (start_center[0] + end_center[0]),
        center_y_um=0.5 * (start_center[1] + end_center[1]),
        extend_um=extend_um,
        layer=layer,
        datatype=datatype,
        flip_x=dx_um < 0.0,
        flip_y=dy_um < 0.0,
    )


def _routing_geometric_45_from_vertical_anchors(
    *,
    start_upper: tuple[float, float],
    start_lower: tuple[float, float],
    end_upper: tuple[float, float],
    end_lower: tuple[float, float],
    layer: int,
    datatype: int = 0,
):
    import gdstk

    width_um = max(1.0e-6, abs(start_upper[1] - start_lower[1]))
    start_center = (0.5 * (start_upper[0] + start_lower[0]), 0.5 * (start_upper[1] + start_lower[1]))
    end_center = (0.5 * (end_upper[0] + end_lower[0]), 0.5 * (end_upper[1] + end_lower[1]))
    dx_um = end_center[0] - start_center[0]
    dy_um = end_center[1] - start_center[1]
    spacing_um = max(0.0, abs(dx_um) - width_um)
    base_span_um = width_um + spacing_um + (math.sqrt(2.0) - 1.0) * (2.0 * spacing_um + width_um)
    extend_um = max(0.0, 0.5 * (abs(dy_um) - base_span_um))
    rotated_points = _reference_routing_geometric_45_points(
        width_um=width_um,
        spacing_um=spacing_um,
        center_x_um=0.5 * (start_center[1] + end_center[1]),
        center_y_um=0.5 * (start_center[0] + end_center[0]),
        extend_um=extend_um,
        flip_x=dy_um < 0.0,
        flip_y=dx_um < 0.0,
    )
    return [gdstk.Polygon([(y, x) for x, y in rotated_points], layer=layer, datatype=datatype)]


def _vertical_45_polyline_route(
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    width_um: float,
    layer: int,
    datatype: int = 0,
):
    dx_um = end[0] - start[0]
    dy_um = end[1] - start[1]
    abs_dx_um = abs(dx_um)
    abs_dy_um = abs(dy_um)
    if abs_dx_um <= 1.0e-9 and abs_dy_um <= 1.0e-9:
        raise ValueError("45-degree route requires distinct start/end points")

    points = [start]
    if abs_dy_um >= abs_dx_um:
        sign_y = 1.0 if dy_um >= 0.0 else -1.0
        excess_um = abs_dy_um - abs_dx_um
        first_y = start[1] + sign_y * 0.5 * excess_um
        second_y = end[1] - sign_y * 0.5 * excess_um
        if abs(first_y - start[1]) > 1.0e-9:
            points.append((start[0], first_y))
        diagonal_end = (end[0], second_y)
        if abs(diagonal_end[0] - points[-1][0]) > 1.0e-9 or abs(diagonal_end[1] - points[-1][1]) > 1.0e-9:
            points.append(diagonal_end)
    else:
        sign_x = 1.0 if dx_um >= 0.0 else -1.0
        excess_um = abs_dx_um - abs_dy_um
        first_x = start[0] + sign_x * 0.5 * excess_um
        second_x = end[0] - sign_x * 0.5 * excess_um
        if abs(first_x - start[0]) > 1.0e-9:
            points.append((first_x, start[1]))
        diagonal_end = (second_x, end[1])
        if abs(diagonal_end[0] - points[-1][0]) > 1.0e-9 or abs(diagonal_end[1] - points[-1][1]) > 1.0e-9:
            points.append(diagonal_end)
    if abs(end[0] - points[-1][0]) > 1.0e-9 or abs(end[1] - points[-1][1]) > 1.0e-9:
        points.append(end)
    return _bridge_path(points, width_um, layer)


def _via_grid_polygons(
    *,
    spec: InductorLayoutSpec,
    center: tuple[float, float],
    width_um: float,
    height_um: float,
    layer: int,
    datatype: int = 0,
):
    import gdstk

    min_extent_um = max(1.0e-6, min(width_um, height_um))
    via_width_um = max(0.20, min(min_extent_um, _bridge_section_via_width_ratio(spec) * min_extent_um))
    via_spacing_um = max(0.15, _bridge_section_via_spacing_ratio(spec) * via_width_um)
    nx = max(1, int((width_um + via_spacing_um) / (via_width_um + via_spacing_um)))
    ny = max(1, int((height_um + via_spacing_um) / (via_width_um + via_spacing_um)))
    diff_x_um = width_um - nx * via_width_um - (nx - 1) * via_spacing_um
    diff_y_um = height_um - ny * via_width_um - (ny - 1) * via_spacing_um

    x_origin_um = center[0] - 0.5 * width_um + 0.5 * diff_x_um
    y_origin_um = center[1] - 0.5 * height_um + 0.5 * diff_y_um
    polygons: list[object] = []
    for ix in range(nx):
        x0_um = x_origin_um + ix * (via_width_um + via_spacing_um)
        for iy in range(ny):
            y0_um = y_origin_um + iy * (via_width_um + via_spacing_um)
            polygons.append(
                gdstk.rectangle(
                    (x0_um, y0_um),
                    (x0_um + via_width_um, y0_um + via_width_um),
                    layer=layer,
                    datatype=datatype,
                )
            )
    return polygons


def _polygons_on_layer(polygons, layer: int, datatype: int | None = None):
    import gdstk

    copied = []
    for polygon in polygons:
        copied.append(
            gdstk.Polygon(
                polygon.points.copy(),
                layer=layer,
                datatype=int(getattr(polygon, "datatype", 0) if datatype is None else datatype),
            )
        )
    return copied


def _group_polygons_by_layer(polygons) -> dict[int, tuple[object, ...]]:
    grouped: dict[int, list[object]] = {}
    for polygon in polygons:
        grouped.setdefault(int(polygon.layer), []).append(polygon)
    return {layer: tuple(items) for layer, items in grouped.items()}


def _build_bridge_endpoint_stacks(
    *,
    spec: InductorLayoutSpec,
    start_pad_center: tuple[float, float],
    end_pad_center: tuple[float, float],
    bridge_pad_width_um: float,
    bridge_pad_height_um: float,
    via_pad_width_um: float,
    via_pad_height_um: float,
    explicit_stage_polygons: dict[tuple[str, str], tuple[object, ...]] | None = None,
) -> tuple[BridgeEndpointStack, ...]:
    def _stage_polys(stack_name: str, center: tuple[float, float], layer: int | None, role: str) -> BridgePadStage | None:
        if layer is None:
            return None
        if explicit_stage_polygons is not None:
            polygons = explicit_stage_polygons.get((stack_name, role))
            if polygons:
                return BridgePadStage(name=role, layer=int(layer), polygons=tuple(polygons))
        width_um = float(bridge_pad_width_um)
        height_um = float(bridge_pad_height_um)
        if "via" in role:
            width_um = float(via_pad_width_um)
            height_um = float(via_pad_height_um)
        polygon = _transform_polygon(
            _pad_from_center(
                center=center,
                width_um=width_um,
                height_um=height_um,
                layer=int(layer),
                datatype=0,
            ),
            spec,
        )
        return BridgePadStage(name=role, layer=int(layer), polygons=(polygon,))

    inner_stages = tuple(
        stage
        for stage in (
            _stage_polys("inner", start_pad_center, spec.metal_layer, "coil_pad"),
            _stage_polys("inner", start_pad_center, spec.bridge_via_layer, "upper_via_pad"),
            _stage_polys("inner", start_pad_center, spec.bridge_layer, "intermediate_pad"),
            _stage_polys("inner", start_pad_center, spec.bridge_lower_via_layer, "lower_via_pad"),
            _stage_polys("inner", start_pad_center, spec.bridge_lower_layer, "bridge_pad"),
        )
        if stage is not None
    )
    outer_stages = tuple(
        stage
        for stage in (
            _stage_polys("outer", end_pad_center, spec.metal_layer, "coil_pad"),
            _stage_polys("outer", end_pad_center, spec.bridge_via_layer, "upper_via_pad"),
            _stage_polys("outer", end_pad_center, spec.bridge_layer, "intermediate_pad"),
            _stage_polys("outer", end_pad_center, spec.bridge_lower_via_layer, "lower_via_pad"),
            _stage_polys("outer", end_pad_center, spec.bridge_lower_layer, "bridge_pad"),
        )
        if stage is not None
    )
    return (
        BridgeEndpointStack(name="inner", stages=inner_stages),
        BridgeEndpointStack(name="outer", stages=outer_stages),
    )


def _bridge_path(points: list[tuple[float, float]], width_um: float, layer: int):
    import gdstk

    path = gdstk.FlexPath(
        points,
        width_um,
        layer=layer,
        datatype=0,
        joins="bevel",
        ends="flush",
    )
    return path.to_polygons()


def _diagonal_bridge_corner_anchors(
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    width_um: float,
) -> BridgeCornerAnchors:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0.0:
        raise ValueError("Diagonal bridge anchors require distinct start/end points")

    ux = dx / length
    uy = dy / length
    px = -uy
    py = ux
    half_width_um = 0.5 * width_um

    start_a = (start[0] + px * half_width_um, start[1] + py * half_width_um)
    start_b = (start[0] - px * half_width_um, start[1] - py * half_width_um)
    end_a = (end[0] + px * half_width_um, end[1] + py * half_width_um)
    end_b = (end[0] - px * half_width_um, end[1] - py * half_width_um)

    start_upper, start_lower = sorted((start_a, start_b), key=lambda point: point[1], reverse=True)
    end_upper, end_lower = sorted((end_a, end_b), key=lambda point: point[1], reverse=True)
    return BridgeCornerAnchors(
        start_upper=start_upper,
        start_lower=start_lower,
        end_upper=end_upper,
        end_lower=end_lower,
    )


def _required_equal_pad_height_for_45_degree(
    *,
    start_center: tuple[float, float],
    end_center: tuple[float, float],
    minimum_height_um: float,
) -> float:
    dx_um = abs(end_center[0] - start_center[0])
    dy_um = abs(end_center[1] - start_center[1])
    # For anchors taken from the top edge of the lower pad and the bottom edge
    # of the upper pad, a true 45-degree edge requires:
    #   abs(center_dy) - pad_height == abs(center_dx)
    required_height_um = max(0.0, dy_um - dx_um)
    return max(float(minimum_height_um), required_height_um)


def _extend_segment_endpoints(
    start: tuple[float, float],
    end: tuple[float, float],
    extension_um: float,
) -> list[tuple[float, float]]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0.0 or extension_um == 0.0:
        return [start, end]
    ux = dx / length
    uy = dy / length
    return [
        (start[0] - ux * extension_um, start[1] - uy * extension_um),
        (end[0] + ux * extension_um, end[1] + uy * extension_um),
    ]


def _transform_point(point: tuple[float, float], spec: InductorLayoutSpec) -> tuple[float, float]:
    x_um, y_um = point
    if spec.mirror_x:
        x_um = -x_um
    return (x_um + spec.center_x_um, y_um + spec.center_y_um)


def _inverse_transform_x(x_um: float, spec: InductorLayoutSpec) -> float:
    x_um = float(x_um) - spec.center_x_um
    if spec.mirror_x:
        x_um = -x_um
    return x_um


def _transform_polygon(polygon, spec: InductorLayoutSpec):
    import gdstk

    points = polygon.points.copy()
    if spec.mirror_x:
        points[:, 0] *= -1.0
    points[:, 0] += spec.center_x_um
    points[:, 1] += spec.center_y_um
    return gdstk.Polygon(points, layer=polygon.layer, datatype=polygon.datatype)


def _transformed_polygons(polygons, spec: InductorLayoutSpec):
    return [_transform_polygon(polygon, spec) for polygon in polygons]


def _add_transformed_polygons(cell, polygons, spec: InductorLayoutSpec) -> None:
    for polygon in _transformed_polygons(polygons, spec):
        cell.add(polygon)


def _normalize_polygons_for_export(polygons, layer_datatypes: tuple[tuple[int, int], ...]):
    import gdstk

    if not layer_datatypes:
        return tuple(polygons)
    datatype_by_layer = {int(layer): int(datatype) for layer, datatype in layer_datatypes}
    normalized = []
    for polygon in polygons:
        target_datatype = datatype_by_layer.get(int(polygon.layer))
        if target_datatype is None or int(polygon.datatype) == target_datatype:
            normalized.append(polygon)
            continue
        normalized.append(
            gdstk.Polygon(
                polygon.points.copy(),
                layer=int(polygon.layer),
                datatype=int(target_datatype),
            )
        )
    return tuple(normalized)

def _shrink_rectangles(polygons, dx_um: float, dy_um: float):
    import gdstk

    result = []
    for polygon in polygons:
        (x0, y0), (x1, y1) = polygon.bounding_box()
        new_x0 = x0 + dx_um
        new_x1 = x1 - dx_um
        new_y0 = y0 + dy_um
        new_y1 = y1 - dy_um
        if new_x1 <= new_x0 or new_y1 <= new_y0:
            continue
        result.append(gdstk.rectangle((new_x0, new_y0), (new_x1, new_y1), layer=polygon.layer, datatype=polygon.datatype))
    return tuple(result)


def _signal_label_point(
    terminal: tuple[float, float],
    side: str,
    width_um: float,
    feed_extension_um: float,
    edge_aligned: bool = False,
) -> tuple[float, float]:
    if edge_aligned:
        inset_um = 0.0
    else:
        inset_um = min(feed_extension_um * 0.45, max(1.0, width_um * 0.45))
    sign = 1.0 if side == "left" else -1.0
    return (terminal[0] + sign * inset_um, terminal[1])


def _open_octagon_path(
    side: str,
    half_width_um: float,
    half_height_um: float,
    direction: str,
    center_x_um: float,
    terminal_y_span_um: float | None = None,
) -> list[tuple[float, float]]:
    chamfer = _octagon_chamfer_um(half_width_um, half_height_um)
    side_half_span_um = _resolved_terminal_half_span(half_width_um, half_height_um, terminal_y_span_um)
    left_upper = (center_x_um - half_width_um, half_height_um - chamfer)
    left_lower = (center_x_um - half_width_um, -half_height_um + chamfer)
    right_upper = (center_x_um + half_width_um, half_height_um - chamfer)
    right_lower = (center_x_um + half_width_um, -half_height_um + chamfer)
    left_top_gap = (center_x_um - half_width_um, side_half_span_um)
    left_bottom_gap = (center_x_um - half_width_um, -side_half_span_um)
    right_top_gap = (center_x_um + half_width_um, side_half_span_um)
    right_bottom_gap = (center_x_um + half_width_um, -side_half_span_um)

    if side == "left" and direction == "down":
        return _dedupe_sequential_points(
            [
            left_top_gap,
            left_upper,
            (center_x_um - half_width_um + chamfer, half_height_um),
            (center_x_um + half_width_um - chamfer, half_height_um),
            right_upper,
            right_lower,
            (center_x_um + half_width_um - chamfer, -half_height_um),
            (center_x_um - half_width_um + chamfer, -half_height_um),
            left_lower,
            left_bottom_gap,
        ]
        )
    if side == "left":
        return _dedupe_sequential_points(
            [
            left_bottom_gap,
            left_lower,
            (center_x_um - half_width_um + chamfer, -half_height_um),
            (center_x_um + half_width_um - chamfer, -half_height_um),
            right_lower,
            right_upper,
            (center_x_um + half_width_um - chamfer, half_height_um),
            (center_x_um - half_width_um + chamfer, half_height_um),
            left_upper,
            left_top_gap,
        ]
        )
    if direction == "down":
        return _dedupe_sequential_points(
            [
            right_top_gap,
            right_upper,
            (center_x_um + half_width_um - chamfer, half_height_um),
            (center_x_um - half_width_um + chamfer, half_height_um),
            left_upper,
            left_lower,
            (center_x_um - half_width_um + chamfer, -half_height_um),
            (center_x_um + half_width_um - chamfer, -half_height_um),
            right_lower,
            right_bottom_gap,
        ]
        )
    return _dedupe_sequential_points(
        [
        right_bottom_gap,
        right_lower,
        (center_x_um + half_width_um - chamfer, -half_height_um),
        (center_x_um - half_width_um + chamfer, -half_height_um),
        left_lower,
        left_upper,
        (center_x_um - half_width_um + chamfer, half_height_um),
        (center_x_um + half_width_um - chamfer, half_height_um),
        right_upper,
        right_top_gap,
    ]
    )


def _terminal_point(
    side: str,
    half_width_um: float,
    half_height_um: float,
    direction: str,
    center_x_um: float,
    terminal_y_span_um: float | None = None,
) -> tuple[float, float]:
    return _open_octagon_path(
        side,
        half_width_um,
        half_height_um,
        direction,
        center_x_um,
        terminal_y_span_um=terminal_y_span_um,
    )[0]


def _resolved_terminal_half_span(half_width_um: float, half_height_um: float, terminal_y_span_um: float | None) -> float:
    max_half_span_um = float(half_height_um) - _octagon_chamfer_um(half_width_um, half_height_um)
    if terminal_y_span_um is None:
        return max_half_span_um
    half_terminal_span_um = 0.5 * terminal_y_span_um
    if half_terminal_span_um > max_half_span_um:
        raise ValueError(
            f"terminal_y_span_um={terminal_y_span_um:.3f} exceeds the straight-side span limit of {2.0 * max_half_span_um:.3f} um"
        )
    return half_terminal_span_um


def _dedupe_sequential_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if deduped and deduped[-1] == point:
            continue
        deduped.append(point)
    return deduped


def _extend_terminal(side: str, terminal_point: tuple[float, float], feed_extension_um: float) -> tuple[float, float]:
    sign = -1.0 if side == "left" else 1.0
    return (terminal_point[0] + sign * feed_extension_um, terminal_point[1])
