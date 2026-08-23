"""gdstk-based geometry checks for transformer layouts."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.topology import TransformerSpec
from ..core.types import TransformerRunConfig, ViaFamilyRule, ViaLayerRule
from .builders import (
    CenterTappedInductorGeometry,
    _build_gdstk_check_bundle,
    _build_winding,
    _octagon_vertices,
    _resolved_terminal_half_span,
)

@dataclass(frozen=True)
class TransformerGdstkCheckResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, float | int | bool | str]


def run_transformer_gdstk_checks(
    geometry: TransformerSpec,
    run_config: TransformerRunConfig,
) -> TransformerGdstkCheckResult:
    start = time.perf_counter()
    transformer = geometry.transformer_spec()
    metrics: dict[str, float | int | bool | str] = {
        "topology_mode": transformer.topology_mode,
    }
    errors: list[str] = []
    warnings: list[str] = []

    winding_angle_errors, winding_angle_metrics = _winding_centerline_angle_checks(transformer)
    errors.extend(winding_angle_errors)
    metrics.update(winding_angle_metrics)

    primary_bundle = _build_gdstk_check_bundle(
        side="left",
        inductor=transformer.primary,
        center_x_um=0.0,
        bridge_anchor_gap_cap_um=(
            max(1.0e-6, transformer.secondary.terminal_y_span_um - transformer.secondary.trace_width_um - 1.0e-6)
            if transformer.primary.bridge_section is not None
            else None
        ),
        metal_layer=run_config.emx.ap_layer,
        mirror_x=False,
    )
    secondary_bundle = _build_gdstk_check_bundle(
        side="right",
        inductor=transformer.secondary,
        center_x_um=transformer.offset_um,
        bridge_anchor_gap_cap_um=None,
        metal_layer=run_config.emx.m9_layer,
        mirror_x=True,
    )

    if primary_bundle is None or secondary_bundle is None:
        metrics["skipped"] = True
        metrics["elapsed_ms"] = (time.perf_counter() - start) * 1000.0
        return TransformerGdstkCheckResult(errors=tuple(errors), warnings=tuple(warnings), metrics=metrics)

    metrics["checker_primary_coil_layer"] = int(run_config.emx.ap_layer)
    metrics["checker_secondary_coil_layer"] = int(run_config.emx.m9_layer)
    metrics["checker_primary_bridge_route_layers"] = (
        f"via1={run_config.emx.primary_bridge_via_layer}, "
        f"metal1={run_config.emx.primary_bridge_layer}, "
        f"via2={run_config.emx.primary_bridge_lower_via_layer}, "
        f"metal2={run_config.emx.primary_bridge_lower_layer}"
    )
    metrics["checker_secondary_bridge_route_layers"] = (
        f"via1={run_config.emx.secondary_bridge_via_layer}, "
        f"metal1={run_config.emx.secondary_bridge_layer}, "
        f"via2={run_config.emx.secondary_bridge_lower_via_layer}, "
        f"metal2={run_config.emx.secondary_bridge_lower_layer}"
    )
    metrics["checker_primary_bridge_stack_layers"] = _format_bridge_stack_layers(primary_bundle)
    metrics["checker_secondary_bridge_stack_layers"] = _format_bridge_stack_layers(secondary_bundle)
    metrics["checker_secondary_coil_group_layers"] = _format_layer_groups(secondary_bundle.coil_groups)
    metrics["checker_secondary_feed_group_layers"] = _format_layer_groups(secondary_bundle.feed_groups)

    primary_components = _conductive_component_count(
        primary_bundle.coil_polygons,
        primary_bundle.bridge_polygons,
        primary_bundle.via_polygons,
    )
    secondary_components = _conductive_component_count(
        secondary_bundle.coil_polygons,
        secondary_bundle.bridge_polygons,
        secondary_bundle.via_polygons,
    )
    metrics["primary_conductive_components"] = primary_components
    metrics["secondary_conductive_components"] = secondary_components
    if primary_components != 1:
        errors.append(f"gdstk: primary conductive geometry splits into {primary_components} components")
    if secondary_components != 1:
        errors.append(f"gdstk: secondary conductive geometry splits into {secondary_components} components")

    primary_feed_errors, primary_feed_metrics = _feed_clearance_checks(
        bundle=primary_bundle,
        trace_width_um=transformer.primary.trace_width_um,
        prefix="primary",
    )
    secondary_feed_errors, secondary_feed_metrics = _feed_clearance_checks(
        bundle=secondary_bundle,
        trace_width_um=transformer.secondary.trace_width_um,
        prefix="secondary",
    )
    errors.extend(primary_feed_errors)
    errors.extend(secondary_feed_errors)
    metrics.update(primary_feed_metrics)
    metrics.update(secondary_feed_metrics)

    same_layer_errors, same_layer_metrics = _generic_same_layer_spacing_checks(
        primary_bundle=primary_bundle,
        primary_trace_width_um=transformer.primary.trace_width_um,
        secondary_bundle=secondary_bundle,
        secondary_trace_width_um=transformer.secondary.trace_width_um,
    )
    errors.extend(same_layer_errors)
    metrics.update(same_layer_metrics)

    bridge_feed_errors, bridge_feed_metrics = _bridge_pad_feed_clearance_checks(
        source_bundle=primary_bundle,
        source_trace_width_um=transformer.primary.trace_width_um,
        target_bundle=secondary_bundle,
        target_trace_width_um=transformer.secondary.trace_width_um,
        prefix="primary_to_secondary",
    )
    errors.extend(bridge_feed_errors)
    metrics.update(bridge_feed_metrics)

    manual_bridge_pad_errors, manual_bridge_pad_metrics = _primary_intermediate_bridge_pad_clearance_checks(
        source_bundle=primary_bundle,
        target_bundle=secondary_bundle,
        margin_um=1.0,
    )
    errors.extend(manual_bridge_pad_errors)
    metrics.update(manual_bridge_pad_metrics)

    primary_via_errors, primary_via_warnings, primary_via_metrics = _bundle_via_rule_checks(
        bundle=primary_bundle,
        run_config=run_config,
        prefix="primary",
    )
    secondary_via_errors, secondary_via_warnings, secondary_via_metrics = _bundle_via_rule_checks(
        bundle=secondary_bundle,
        run_config=run_config,
        prefix="secondary",
    )
    errors.extend(primary_via_errors)
    errors.extend(secondary_via_errors)
    warnings.extend(primary_via_warnings)
    warnings.extend(secondary_via_warnings)
    metrics.update(primary_via_metrics)
    metrics.update(secondary_via_metrics)
    metrics["warning_count"] = len(warnings)

    metrics["elapsed_ms"] = (time.perf_counter() - start) * 1000.0
    return TransformerGdstkCheckResult(errors=tuple(errors), warnings=tuple(warnings), metrics=metrics)


def _winding_centerline_angle_checks(transformer: TransformerSpec) -> tuple[list[str], dict[str, float | int | bool | str]]:
    errors: list[str] = []
    metrics: dict[str, float | int | bool | str] = {}
    checks = (
        ("primary", "left", transformer.primary, 0.0),
        ("secondary", "right", transformer.secondary, transformer.offset_um),
    )
    for prefix, side, inductor, center_x_um in checks:
        if int(inductor.turns) == 1:
            points, _terminals = _build_winding(side=side, inductor=inductor, center_x_um=float(center_x_um))
            prefix_errors, prefix_metrics = _single_turn_winding_centerline_angle_checks(
                prefix=prefix,
                points=points,
            )
        else:
            prefix_errors, prefix_metrics = _multi_turn_template_angle_checks(
                prefix=prefix,
                inductor=inductor,
                center_x_um=float(center_x_um),
            )
        errors.extend(prefix_errors)
        metrics.update(prefix_metrics)
    return errors, metrics


def _single_turn_winding_centerline_angle_checks(
    *,
    prefix: str,
    points: list[tuple[float, float]],
) -> tuple[list[str], dict[str, float | int | bool | str]]:
    errors: list[str] = []
    metrics: dict[str, float | int | bool | str] = {}
    if len(points) < 4:
        return [f"gdstk: {prefix} winding centerline has too few points for angle validation"], metrics

    terminal_indices = {1, len(points) - 2}
    internal_angles: list[float] = []
    terminal_angles: list[float] = []
    diagonal_segment_count = 0
    for idx in range(1, len(points) - 1):
        angle = _centerline_interior_angle_degrees(points[idx - 1], points[idx], points[idx + 1])
        if angle is None:
            continue
        if idx in terminal_indices:
            terminal_angles.append(angle)
            if not (_angle_close(angle, 90.0) or _angle_close(angle, 135.0)):
                errors.append(f"gdstk: {prefix} winding lead interface angle is {angle:.6g} deg, expected 90 or 135 deg")
            continue
        internal_angles.append(angle)
        if not _angle_close(angle, 135.0):
            errors.append(f"gdstk: {prefix} winding octagon internal angle is {angle:.6g} deg, expected 135 deg")

    for start, end in zip(points[1:-1], points[2:]):
        dx = abs(float(end[0]) - float(start[0]))
        dy = abs(float(end[1]) - float(start[1]))
        if dx <= 1.0e-9 or dy <= 1.0e-9:
            continue
        diagonal_segment_count += 1
        if abs(dx - dy) > 1.0e-6:
            errors.append(
                f"gdstk: {prefix} winding diagonal segment has dx={dx:.6g} um and dy={dy:.6g} um, expected a 45 deg segment"
            )

    metrics[f"{prefix}_winding_centerline_point_count"] = len(points)
    metrics[f"{prefix}_winding_centerline_internal_turn_count"] = len(internal_angles)
    metrics[f"{prefix}_winding_centerline_terminal_interface_count"] = len(terminal_angles)
    metrics[f"{prefix}_winding_centerline_diagonal_segment_count"] = diagonal_segment_count
    if internal_angles:
        metrics[f"{prefix}_winding_centerline_min_internal_angle_deg"] = min(internal_angles)
        metrics[f"{prefix}_winding_centerline_max_internal_angle_deg"] = max(internal_angles)
    if terminal_angles:
        metrics[f"{prefix}_winding_centerline_min_terminal_angle_deg"] = min(terminal_angles)
        metrics[f"{prefix}_winding_centerline_max_terminal_angle_deg"] = max(terminal_angles)
    return errors, metrics


def _multi_turn_template_angle_checks(
    *,
    prefix: str,
    inductor,
    center_x_um: float,
) -> tuple[list[str], dict[str, float | int | bool | str]]:
    errors: list[str] = []
    metrics: dict[str, float | int | bool | str] = {}
    turns = int(inductor.turns)
    if turns != 2:
        return [f"gdstk: {prefix} winding angle validation supports 1-turn and 2-turn templates, got {turns}"], metrics

    width_um = float(inductor.trace_width_um)
    pitch_um = width_um + float(inductor.spacing_um)
    outer_half_width_um = float(inductor.outer_width_um) * 0.5
    outer_half_height_um = float(inductor.outer_height_um) * 0.5
    internal_angles: list[float] = []
    terminal_angles: list[float] = []
    route_angles: list[float] = []
    diagonal_segment_count = 0
    route_diagonal_segment_count = 0

    for turn_idx in range(turns):
        half_width_um = outer_half_width_um - turn_idx * pitch_um
        half_height_um = outer_half_height_um - turn_idx * pitch_um
        if half_width_um <= width_um or half_height_um <= width_um:
            errors.append(f"gdstk: {prefix} turn {turn_idx + 1} size collapsed before angle validation")
            continue
        vertices = _octagon_vertices(half_width_um, half_height_um, center_x_um=center_x_um, center_y_um=0.0)
        for idx, point in enumerate(vertices):
            angle = _centerline_interior_angle_degrees(vertices[idx - 1], point, vertices[(idx + 1) % len(vertices)])
            if angle is None:
                continue
            internal_angles.append(angle)
            if not _angle_close(angle, 135.0):
                errors.append(
                    f"gdstk: {prefix} turn {turn_idx + 1} octagon internal angle is {angle:.6g} deg, expected 135 deg"
                )
        diag_count, diag_errors = _diagonal_45_segment_checks(
            prefix=f"{prefix} turn {turn_idx + 1} octagon",
            points=vertices + [vertices[0]],
        )
        diagonal_segment_count += diag_count
        errors.extend(diag_errors)

    try:
        _resolved_terminal_half_span(outer_half_width_um, outer_half_height_um, float(inductor.terminal_y_span_um))
        terminal_angles.extend([90.0, 90.0])
        if bool(getattr(inductor, "center_tap", False)):
            terminal_angles.append(90.0)
    except ValueError as exc:
        errors.append(f"gdstk: {prefix} terminal interface angle validation failed: {exc}")

    route_points = _two_turn_45_route_centerlines(inductor)
    for route_name, points in route_points:
        diag_count, diag_errors = _diagonal_45_segment_checks(prefix=f"{prefix} {route_name}", points=points)
        route_diagonal_segment_count += diag_count
        errors.extend(diag_errors)
        for idx in range(1, len(points) - 1):
            angle = _centerline_interior_angle_degrees(points[idx - 1], points[idx], points[idx + 1])
            if angle is None:
                continue
            route_angles.append(angle)
            if not _angle_close(angle, 135.0):
                errors.append(
                    f"gdstk: {prefix} {route_name} centerline bend angle is {angle:.6g} deg, expected 135 deg"
                )

    metrics[f"{prefix}_winding_centerline_point_count"] = int(turns * 8)
    metrics[f"{prefix}_winding_centerline_internal_turn_count"] = len(internal_angles)
    metrics[f"{prefix}_winding_centerline_terminal_interface_count"] = len(terminal_angles)
    metrics[f"{prefix}_winding_centerline_diagonal_segment_count"] = diagonal_segment_count
    metrics[f"{prefix}_winding_centerline_template"] = "two_turn_octagon_rings_plus_45_routes"
    metrics[f"{prefix}_bridge_route_count"] = len(route_points)
    metrics[f"{prefix}_bridge_route_diagonal_segment_count"] = route_diagonal_segment_count
    if internal_angles:
        metrics[f"{prefix}_winding_centerline_min_internal_angle_deg"] = min(internal_angles)
        metrics[f"{prefix}_winding_centerline_max_internal_angle_deg"] = max(internal_angles)
    if terminal_angles:
        metrics[f"{prefix}_winding_centerline_min_terminal_angle_deg"] = min(terminal_angles)
        metrics[f"{prefix}_winding_centerline_max_terminal_angle_deg"] = max(terminal_angles)
    if route_angles:
        metrics[f"{prefix}_bridge_route_min_bend_angle_deg"] = min(route_angles)
        metrics[f"{prefix}_bridge_route_max_bend_angle_deg"] = max(route_angles)
    return errors, metrics


def _two_turn_45_route_centerlines(inductor) -> list[tuple[str, list[tuple[float, float]]]]:
    width_um = float(inductor.trace_width_um)
    pitch_um = width_um + float(inductor.spacing_um)
    outer_half_width_um = float(inductor.outer_width_um) * 0.5
    inner_half_width_um = outer_half_width_um - pitch_um
    outer_center_x = outer_half_width_um - width_um * 0.5
    inner_center_x = inner_half_width_um - width_um * 0.5
    half_crossover_dy = 0.5 * (outer_center_x - inner_center_x)
    upper_site_y = half_crossover_dy
    lower_site_y = -half_crossover_dy
    pad_height_um = max(1.0e-6, 0.5 * width_um * _bridge_section_pad_height_ratio_for_inductor(inductor))
    site_gap_height_um = max(1.0e-6, pad_height_um + 0.1 * width_um)
    half_site_span_um = 0.5 * (site_gap_height_um + pad_height_um)
    underpass_start = (outer_center_x, lower_site_y - half_site_span_um)
    underpass_end = (inner_center_x, upper_site_y + half_site_span_um)
    bridge_start = (inner_center_x, lower_site_y - half_site_span_um)
    bridge_end = (outer_center_x, upper_site_y + half_site_span_um)
    return [
        ("underpass_45_route", _vertical_45_route_centerline_points(underpass_start, underpass_end)),
        ("bridge_45_route", _vertical_45_route_centerline_points(bridge_start, bridge_end)),
    ]


def _bridge_section_pad_height_ratio_for_inductor(inductor) -> float:
    section = getattr(inductor, "bridge_section", None)
    if section is None:
        return 1.0
    return min(1.0, max(0.05, float(section.pad_height_ratio)))


def _vertical_45_route_centerline_points(
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    dx_um = float(end[0]) - float(start[0])
    dy_um = float(end[1]) - float(start[1])
    abs_dx_um = abs(dx_um)
    abs_dy_um = abs(dy_um)
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
    return points


def _diagonal_45_segment_checks(
    *,
    prefix: str,
    points: list[tuple[float, float]],
) -> tuple[int, list[str]]:
    errors: list[str] = []
    diagonal_segment_count = 0
    for start, end in zip(points[:-1], points[1:]):
        dx = abs(float(end[0]) - float(start[0]))
        dy = abs(float(end[1]) - float(start[1]))
        if dx <= 1.0e-9 or dy <= 1.0e-9:
            continue
        diagonal_segment_count += 1
        if abs(dx - dy) > 1.0e-6:
            errors.append(f"gdstk: {prefix} diagonal segment has dx={dx:.6g} um and dy={dy:.6g} um, expected a 45 deg segment")
    return diagonal_segment_count, errors


def _centerline_interior_angle_degrees(
    prev_point: tuple[float, float],
    point: tuple[float, float],
    next_point: tuple[float, float],
) -> float | None:
    import math

    v1 = (float(prev_point[0]) - float(point[0]), float(prev_point[1]) - float(point[1]))
    v2 = (float(next_point[0]) - float(point[0]), float(next_point[1]) - float(point[1]))
    len1 = math.hypot(v1[0], v1[1])
    len2 = math.hypot(v2[0], v2[1])
    if len1 <= 1.0e-9 or len2 <= 1.0e-9:
        return None
    dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (len1 * len2)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def _angle_close(value: float, expected: float, *, tol_deg: float = 1.0e-6) -> bool:
    return abs(float(value) - float(expected)) <= float(tol_deg)

def _bbox_overlaps(a, b) -> bool:
    (ax0, ay0), (ax1, ay1) = a.bounding_box()
    (bx0, by0), (bx1, by1) = b.bounding_box()
    return ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1


def _polygons_overlap(polygons_a, polygons_b) -> bool:
    import gdstk

    for poly_a in polygons_a:
        for poly_b in polygons_b:
            if not _bbox_overlaps(poly_a, poly_b):
                continue
            intersection = gdstk.boolean([poly_a], [poly_b], "and", layer=poly_a.layer, datatype=0)
            if intersection:
                return True
    return False


def _polygons_fully_contained(subject_polygons, container_polygons) -> bool:
    import gdstk

    if not subject_polygons:
        return True
    if not container_polygons:
        return False
    difference = gdstk.boolean(
        list(subject_polygons),
        list(container_polygons),
        "not",
        layer=subject_polygons[0].layer,
        datatype=0,
    )
    return not difference


def _conductive_component_count(coil_polygons, bridge_polygons, via_polygons) -> int:
    groups = [
        tuple(coil_polygons),
        tuple(bridge_polygons),
        tuple(via_polygons),
    ]
    active = [idx for idx, polys in enumerate(groups) if polys]
    if not active:
        return 0
    adjacency = {idx: set() for idx in active}
    for i_idx, i in enumerate(active):
        for j in active[i_idx + 1 :]:
            if _polygons_overlap(groups[i], groups[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)
    seen: set[int] = set()
    components = 0
    for node in active:
        if node in seen:
            continue
        components += 1
        stack = [node]
        seen.add(node)
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
    return components


def _generic_same_layer_spacing_checks(
    *,
    primary_bundle: CenterTappedInductorGeometry,
    primary_trace_width_um: float,
    secondary_bundle: CenterTappedInductorGeometry,
    secondary_trace_width_um: float,
) -> tuple[list[str], dict[str, float | int]]:
    primary_layer_polygons: dict[int, list[object]] = {}
    secondary_layer_polygons: dict[int, list[object]] = {}
    layer_required_spacing_um: dict[int, float] = {}

    def register(layer_polygons: dict[int, list[object]], polygons, trace_width_um: float) -> None:
        spacing_um = _generic_same_layer_spacing_margin_um(trace_width_um)
        for polygon in polygons:
            layer = int(polygon.layer)
            layer_polygons.setdefault(layer, []).append(polygon)
            layer_required_spacing_um[layer] = max(layer_required_spacing_um.get(layer, 0.0), spacing_um)

    register(primary_layer_polygons, primary_bundle.coil_polygons, primary_trace_width_um)
    register(primary_layer_polygons, primary_bundle.bridge_polygons, primary_trace_width_um)
    register(primary_layer_polygons, primary_bundle.via_polygons, primary_trace_width_um)
    register(secondary_layer_polygons, secondary_bundle.coil_polygons, secondary_trace_width_um)
    register(secondary_layer_polygons, secondary_bundle.bridge_polygons, secondary_trace_width_um)
    register(secondary_layer_polygons, secondary_bundle.via_polygons, secondary_trace_width_um)

    errors: list[str] = []
    pair_count = 0
    violation_count = 0
    layers_with_multiple_regions = 0
    for layer in sorted(set(primary_layer_polygons) | set(secondary_layer_polygons)):
        primary_merged = _merge_same_layer_polygons(primary_layer_polygons.get(layer, ()), layer=layer)
        secondary_merged = _merge_same_layer_polygons(secondary_layer_polygons.get(layer, ()), layer=layer)
        if not primary_merged or not secondary_merged:
            continue
        layers_with_multiple_regions += 1
        required_spacing_um = layer_required_spacing_um[layer]
        expanded_primary_regions = tuple(
            _expand_polygon_region(region, margin_um=0.5 * required_spacing_um) for region in primary_merged
        )
        expanded_secondary_regions = tuple(
            _expand_polygon_region(region, margin_um=0.5 * required_spacing_um) for region in secondary_merged
        )
        for region_a in expanded_primary_regions:
            for region_b in expanded_secondary_regions:
                pair_count += 1
                if _polygons_overlap(region_a, region_b):
                    violation_count += 1
                    errors.append(
                        "gdstk: layer "
                        f"{layer} has a same-layer spacing/intersection violation "
                        f"(required >= {required_spacing_um:.3f} um)"
                    )

    return errors, {
        "same_layer_region_pairs_checked": pair_count,
        "same_layer_spacing_violations": violation_count,
        "same_layer_layers_with_multiple_regions": layers_with_multiple_regions,
    }


def _feed_clearance_checks(
    *,
    bundle: CenterTappedInductorGeometry,
    trace_width_um: float,
    prefix: str,
) -> tuple[list[str], dict[str, float | int]]:
    required_spacing_um = _generic_same_layer_spacing_margin_um(trace_width_um)
    feed_pairs: list[tuple[str, tuple[object, ...], str, tuple[object, ...]]] = []
    if bundle.center_feed_polygons:
        feed_pairs.append(("top", bundle.top_feed_polygons, "center", bundle.center_feed_polygons))
        feed_pairs.append(("center", bundle.center_feed_polygons, "bottom", bundle.bottom_feed_polygons))
    else:
        feed_pairs.append(("top", bundle.top_feed_polygons, "bottom", bundle.bottom_feed_polygons))

    violations = 0
    errors: list[str] = []
    for left_name, left_polygons, right_name, right_polygons in feed_pairs:
        expanded_left = _expand_polygon_region(left_polygons, margin_um=0.5 * required_spacing_um)
        expanded_right = _expand_polygon_region(right_polygons, margin_um=0.5 * required_spacing_um)
        if _polygons_overlap(expanded_left, expanded_right):
            violations += 1
            errors.append(
                "gdstk: "
                f"{prefix} {left_name}-feed to {right_name}-feed clearance violation "
                f"(required >= {required_spacing_um:.3f} um)"
            )
    return errors, {
        f"{prefix}_feed_pair_checks": len(feed_pairs),
        f"{prefix}_feed_clearance_violations": violations,
    }


def _bridge_pad_feed_clearance_checks(
    *,
    source_bundle: CenterTappedInductorGeometry,
    source_trace_width_um: float,
    target_bundle: CenterTappedInductorGeometry,
    target_trace_width_um: float,
    prefix: str,
) -> tuple[list[str], dict[str, float | int]]:
    required_spacing_um = max(
        _generic_same_layer_spacing_margin_um(source_trace_width_um),
        _generic_same_layer_spacing_margin_um(target_trace_width_um),
    )
    target_feeds = {
        "top": target_bundle.top_feed_polygons,
        "bottom": target_bundle.bottom_feed_polygons,
    }
    source_pads = {
        "outer_anchor": source_bundle.outer_anchor_pad,
        "inner_anchor": source_bundle.inner_anchor_pad,
    }
    pair_count = 0
    violations = 0
    errors: list[str] = []
    for pad_name, pad_polygons in source_pads.items():
        if not pad_polygons:
            continue
        for feed_name, feed_polygons in target_feeds.items():
            if not feed_polygons:
                continue
            shared_layers = {int(poly.layer) for poly in pad_polygons}.intersection(int(poly.layer) for poly in feed_polygons)
            if not shared_layers:
                continue
            pair_count += 1
            pad_layer_polygons = tuple(poly for poly in pad_polygons if int(poly.layer) in shared_layers)
            feed_layer_polygons = tuple(poly for poly in feed_polygons if int(poly.layer) in shared_layers)
            expanded_pad = _expand_polygon_region(pad_layer_polygons, margin_um=0.5 * required_spacing_um)
            expanded_feed = _expand_polygon_region(feed_layer_polygons, margin_um=0.5 * required_spacing_um)
            if _polygons_overlap(expanded_pad, expanded_feed):
                violations += 1
                errors.append(
                    "gdstk: "
                    f"{prefix} {pad_name} to {feed_name}-feed clearance violation "
                    f"(required >= {required_spacing_um:.3f} um)"
                )
    return errors, {
        f"{prefix}_bridge_feed_pair_checks": pair_count,
        f"{prefix}_bridge_feed_clearance_violations": violations,
    }


def _primary_intermediate_bridge_pad_clearance_checks(
    *,
    source_bundle: CenterTappedInductorGeometry,
    target_bundle: CenterTappedInductorGeometry,
    intermediate_layer: int | None = None,
    margin_um: float,
) -> tuple[list[str], dict[str, float | int]]:
    staged_source_groups: list[tuple[str, int, tuple[object, ...]]] = [
        (
            f"{stack.name}:{stage.name}",
            int(stage.layer),
            tuple(stage.polygons),
        )
        for stack in source_bundle.bridge_endpoint_stacks
        for stage in stack.stages
        if stage.name != "coil_pad"
    ]
    intermediate_stage_groups = [
        group for group in staged_source_groups if group[0].endswith(":intermediate_pad")
    ]
    if intermediate_layer is not None:
        staged_source_groups = [group for group in staged_source_groups if group[1] == int(intermediate_layer)]
        intermediate_stage_groups = [group for group in intermediate_stage_groups if group[1] == int(intermediate_layer)]
    if not staged_source_groups:
        legacy_groups: list[tuple[str, int, tuple[object, ...]]] = []
        grouped: dict[int, list[object]] = {}
        for poly in source_bundle.intermediate_bridge_pad_polygons:
            grouped.setdefault(int(getattr(poly, "layer", -1)), []).append(poly)
        for layer, polys in grouped.items():
            legacy_groups.append(("legacy:intermediate_pad", int(layer), tuple(polys)))
        staged_source_groups = legacy_groups
        intermediate_stage_groups = legacy_groups

    if not staged_source_groups:
        return [], {
            "primary_intermediate_bridge_pad_count": 0,
            "primary_intermediate_bridge_pad_same_layer_checks": 0,
            "primary_intermediate_bridge_pad_target_polygons": 0,
            "primary_intermediate_bridge_pad_clearance_violations": 0,
            "primary_intermediate_bridge_pad_checked_stage_layers": "",
            "primary_intermediate_bridge_pad_target_layer_counts": "",
        }

    errors: list[str] = []
    violations = 0
    same_layer_checks = 0
    target_polygon_count = 0
    target_layer_counts: dict[int, int] = {}

    for stage_name, layer, stage_polygons in staged_source_groups:
        target_layer_polygons = tuple(target_bundle.forbidden_geometry_on_layer(layer))
        target_polygon_count += len(target_layer_polygons)
        target_layer_counts[int(layer)] = target_layer_counts.get(int(layer), 0) + len(target_layer_polygons)
        if not stage_polygons or not target_layer_polygons:
            continue
        expanded_stage = _expand_polygon_region(stage_polygons, margin_um=0.5 * margin_um)
        expanded_target = _expand_polygon_region(target_layer_polygons, margin_um=0.5 * margin_um)
        same_layer_checks += len(stage_polygons) * len(target_layer_polygons)
        if _polygons_overlap(expanded_stage, expanded_target):
            violations += 1
            errors.append(
                "gdstk: primary intermediate bridge pad overlaps secondary geometry; "
                f"stage {stage_name} on layer {layer} "
                f"or violates {margin_um:.3f} um clearance"
            )

    return errors, {
        "primary_intermediate_bridge_pad_count": len(intermediate_stage_groups),
        "primary_intermediate_bridge_pad_same_layer_checks": same_layer_checks,
        "primary_intermediate_bridge_pad_bbox_checks": same_layer_checks,
        "primary_intermediate_bridge_pad_target_polygons": target_polygon_count,
        "primary_intermediate_bridge_pad_clearance_violations": violations,
        "primary_intermediate_bridge_pad_checked_stage_layers": ", ".join(
            f"{stage_name}@{layer}" for stage_name, layer, _ in staged_source_groups
        ),
        "primary_intermediate_bridge_pad_target_layer_counts": ", ".join(
            f"{layer}:{count}" for layer, count in sorted(target_layer_counts.items())
        ),
    }


def _bundle_via_rule_checks(
    *,
    bundle: CenterTappedInductorGeometry,
    run_config: TransformerRunConfig,
    prefix: str,
) -> tuple[list[str], list[str], dict[str, float | int | str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, float | int | str] = {
        f"{prefix}_via_checked": 0,
        f"{prefix}_via_shape_violations": 0,
        f"{prefix}_via_coverage_violations": 0,
        f"{prefix}_via_size_violations": 0,
        f"{prefix}_via_spacing_violations": 0,
        f"{prefix}_via_enclosure_violations": 0,
        f"{prefix}_via_recommended_enclosure_warnings": 0,
        f"{prefix}_via_redundancy_violations": 0,
        f"{prefix}_via_redundancy_warnings": 0,
        f"{prefix}_via_stacked_depth_warnings": 0,
        f"{prefix}_via_large_plate_warnings": 0,
    }
    if not bundle.bridge_endpoint_stacks:
        return errors, warnings, metrics

    family_by_layer = {
        int(layer): rule
        for layer, rule in run_config.emx.via_layer_rules.items()
    }
    family_rules = dict(run_config.emx.via_family_rules)
    connected_summary: list[str] = []

    stack_depth_by_layer: dict[int, int] = {}
    for stack in bundle.bridge_endpoint_stacks:
        stage_by_name = {stage.name: stage for stage in stack.stages}
        if "upper_via_pad" in stage_by_name:
            _check_stack_via_stage(
                stack_name=stack.name,
                stage_name="upper_via_pad",
                via_stage=stage_by_name["upper_via_pad"],
                lower_stage=stage_by_name.get("coil_pad"),
                upper_stage=stage_by_name.get("intermediate_pad"),
                run_config=run_config,
                family_by_layer=family_by_layer,
                family_rules=family_rules,
                prefix=prefix,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
                connected_summary=connected_summary,
                stack_depth_by_layer=stack_depth_by_layer,
            )
        if "lower_via_pad" in stage_by_name:
            _check_stack_via_stage(
                stack_name=stack.name,
                stage_name="lower_via_pad",
                via_stage=stage_by_name["lower_via_pad"],
                lower_stage=stage_by_name.get("intermediate_pad"),
                upper_stage=stage_by_name.get("bridge_pad"),
                run_config=run_config,
                family_by_layer=family_by_layer,
                family_rules=family_rules,
                prefix=prefix,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
                connected_summary=connected_summary,
                stack_depth_by_layer=stack_depth_by_layer,
            )
    metrics[f"{prefix}_via_connections"] = ", ".join(connected_summary)
    return errors, warnings, metrics


def _check_stack_via_stage(
    *,
    stack_name: str,
    stage_name: str,
    via_stage,
    lower_stage,
    upper_stage,
    run_config: TransformerRunConfig,
    family_by_layer: dict[int, ViaLayerRule],
    family_rules: dict[str, ViaFamilyRule],
    prefix: str,
    errors: list[str],
    warnings: list[str],
    metrics: dict[str, float | int | str],
    connected_summary: list[str],
    stack_depth_by_layer: dict[int, int],
) -> None:
    if lower_stage is None or upper_stage is None or not via_stage.polygons:
        return
    via_layer = int(via_stage.layer)
    layer_rule = family_by_layer.get(via_layer)
    if layer_rule is None:
        warnings.append(f"gdstk: {prefix} {stack_name}:{stage_name} on layer {via_layer} has no via-family mapping")
        return
    family_rule = family_rules.get(layer_rule.family)
    if family_rule is None:
        warnings.append(
            f"gdstk: {prefix} {stack_name}:{stage_name} on layer {via_layer} maps to {layer_rule.family} but has no family rule"
        )
        return
    lower_polygons = tuple(lower_stage.polygons)
    upper_polygons = tuple(upper_stage.polygons)
    connected_summary.append(
        f"{stack_name}:{stage_name}@{via_layer}->{int(lower_stage.layer)}/{int(upper_stage.layer)}:{layer_rule.family}"
    )

    metrics[f"{prefix}_via_checked"] = int(metrics[f"{prefix}_via_checked"]) + len(via_stage.polygons)
    single_via_stage = len(via_stage.polygons) == 1
    stage_depth = stack_depth_by_layer.get(via_layer, 0) + (1 if single_via_stage else 0)
    stack_depth_by_layer[via_layer] = stage_depth if single_via_stage else 0

    via_rects = [_axis_aligned_rect_bbox(polygon) for polygon in via_stage.polygons]
    proxy_geometry = any(
        rect is not None
        and max(rect[1][0] - rect[0][0], rect[1][1] - rect[0][1]) > (1.5 * family_rule.size_um)
        for rect in via_rects
    )

    stage_spacing = _min_via_spacing_um(via_stage.polygons)
    max_stage_spacing = _max_via_spacing_um(via_stage.polygons)
    if stage_spacing is not None and stage_spacing + 1.0e-9 < family_rule.min_spacing_um:
        metrics[f"{prefix}_via_spacing_violations"] = int(metrics[f"{prefix}_via_spacing_violations"]) + 1
        target = warnings if proxy_geometry else errors
        target.append(
            f"gdstk: {prefix} {stack_name}:{stage_name} via spacing {stage_spacing:.3f} um is below "
            f"{family_rule.min_spacing_um:.3f} um for {layer_rule.family}"
        )

    stage_bbox = _combined_bbox(via_stage.polygons)
    metal_width_um = min(stage_bbox[1][0] - stage_bbox[0][0], stage_bbox[1][1] - stage_bbox[0][1])
    metal_length_um = max(stage_bbox[1][0] - stage_bbox[0][0], stage_bbox[1][1] - stage_bbox[0][1])
    if not _meets_redundancy_rule(
        via_count=len(via_stage.polygons),
        max_spacing_um=max_stage_spacing,
        metal_width_um=metal_width_um,
        metal_length_um=metal_length_um,
        family_rule=family_rule,
    ):
        if proxy_geometry:
            metrics[f"{prefix}_via_redundancy_warnings"] = int(metrics[f"{prefix}_via_redundancy_warnings"]) + 1
            warnings.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} on layer {via_layer} exceeds {layer_rule.family} "
                f"redundancy spacing/count guidance for {metal_width_um:.3f}x{metal_length_um:.3f} um proxy geometry"
            )
        else:
            metrics[f"{prefix}_via_redundancy_violations"] = int(metrics[f"{prefix}_via_redundancy_violations"]) + 1
            errors.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} on layer {via_layer} needs redundant {layer_rule.family} vias "
                f"for {metal_width_um:.3f}x{metal_length_um:.3f} um metal coverage"
            )
    elif len(via_stage.polygons) == 1:
        metrics[f"{prefix}_via_redundancy_warnings"] = int(metrics[f"{prefix}_via_redundancy_warnings"]) + 1
        warnings.append(
            f"gdstk: {prefix} {stack_name}:{stage_name} uses a single {layer_rule.family} via; redundant vias are preferred"
        )

    if family_rule.stacked_single_via_max_depth is not None and stage_depth > family_rule.stacked_single_via_max_depth:
        metrics[f"{prefix}_via_stacked_depth_warnings"] = int(metrics[f"{prefix}_via_stacked_depth_warnings"]) + 1
        warnings.append(
            f"gdstk: {prefix} {stack_name}:{stage_name} exceeds {layer_rule.family} single-via stacked depth "
            f"limit {family_rule.stacked_single_via_max_depth}"
        )

    for index, (polygon, rect) in enumerate(zip(via_stage.polygons, via_rects), start=1):
        if rect is None:
            metrics[f"{prefix}_via_shape_violations"] = int(metrics[f"{prefix}_via_shape_violations"]) + 1
            errors.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} via {index} on layer {via_layer} is not an axis-aligned rectangle"
            )
            continue
        width_um = rect[1][0] - rect[0][0]
        height_um = rect[1][1] - rect[0][1]
        if (
            abs(width_um - family_rule.size_um) > 1.0e-3
            or abs(height_um - family_rule.size_um) > 1.0e-3
        ):
            metrics[f"{prefix}_via_size_violations"] = int(metrics[f"{prefix}_via_size_violations"]) + 1
            target = warnings if proxy_geometry else errors
            target.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} via {index} size {width_um:.3f}x{height_um:.3f} um "
                f"does not match {layer_rule.family} {family_rule.size_um:.3f} um"
            )

        enclosure = _enclosure_against_metals(rect, lower_polygons, upper_polygons)
        if enclosure is None:
            metrics[f"{prefix}_via_coverage_violations"] = int(metrics[f"{prefix}_via_coverage_violations"]) + 1
            errors.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} via {index} is not fully covered by both connected metals"
            )
            continue
        if not _enclosure_is_legal(enclosure, family_rule):
            metrics[f"{prefix}_via_enclosure_violations"] = int(metrics[f"{prefix}_via_enclosure_violations"]) + 1
            errors.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} via {index} enclosure "
                f"({min(enclosure):.3f} um min, opposite={_max_opposite_pair(enclosure):.3f} um) "
                f"is illegal for {layer_rule.family}"
            )
        elif (
            family_rule.recommended_min_all_sides_um is not None
            and min(enclosure) + 1.0e-9 < family_rule.recommended_min_all_sides_um
        ):
            metrics[f"{prefix}_via_recommended_enclosure_warnings"] = (
                int(metrics[f"{prefix}_via_recommended_enclosure_warnings"]) + 1
            )
            warnings.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} via {index} enclosure {min(enclosure):.3f} um is below "
                f"the recommended {family_rule.recommended_min_all_sides_um:.3f} um for {layer_rule.family}"
            )

    if run_config.emx.enable_large_plate_warnings:
        thresholds = family_rule.plate_thresholds
        if thresholds is None or (
            thresholds.max_distance_um is None
            and thresholds.min_plate_width_um is None
            and thresholds.min_plate_height_um is None
        ):
            metrics[f"{prefix}_via_large_plate_warnings"] = int(metrics[f"{prefix}_via_large_plate_warnings"]) + 1
            warnings.append(
                f"gdstk: {prefix} {stack_name}:{stage_name} large-plate proximity thresholds are not configured for {layer_rule.family}"
            )


def _axis_aligned_rect_bbox(polygon) -> tuple[tuple[float, float], tuple[float, float]] | None:
    bbox = polygon.bounding_box()
    if bbox is None:
        return None
    (min_x, min_y), (max_x, max_y) = bbox
    points = {
        (round(float(point[0]), 6), round(float(point[1]), 6))
        for point in polygon.points
    }
    expected = {
        (round(float(min_x), 6), round(float(min_y), 6)),
        (round(float(min_x), 6), round(float(max_y), 6)),
        (round(float(max_x), 6), round(float(min_y), 6)),
        (round(float(max_x), 6), round(float(max_y), 6)),
    }
    if len(points) != 4 or points != expected:
        return None
    return ((float(min_x), float(min_y)), (float(max_x), float(max_y)))


def _min_via_spacing_um(polygons) -> float | None:
    if len(polygons) < 2:
        return None
    min_spacing = None
    for index, left in enumerate(polygons):
        left_bbox = left.bounding_box()
        for right in polygons[index + 1 :]:
            right_bbox = right.bounding_box()
            spacing = _bbox_edge_spacing_um(left_bbox, right_bbox)
            if min_spacing is None or spacing < min_spacing:
                min_spacing = spacing
    return min_spacing


def _max_via_spacing_um(polygons) -> float | None:
    if len(polygons) < 2:
        return None
    max_spacing = 0.0
    for index, left in enumerate(polygons):
        left_bbox = left.bounding_box()
        for right in polygons[index + 1 :]:
            right_bbox = right.bounding_box()
            max_spacing = max(max_spacing, _bbox_edge_spacing_um(left_bbox, right_bbox))
    return max_spacing


def _bbox_edge_spacing_um(left_bbox, right_bbox) -> float:
    dx = max(0.0, float(right_bbox[0][0]) - float(left_bbox[1][0]), float(left_bbox[0][0]) - float(right_bbox[1][0]))
    dy = max(0.0, float(right_bbox[0][1]) - float(left_bbox[1][1]), float(left_bbox[0][1]) - float(right_bbox[1][1]))
    return max(dx, dy) if dx == 0.0 or dy == 0.0 else (dx * dx + dy * dy) ** 0.5


def _combined_bbox(polygons) -> tuple[tuple[float, float], tuple[float, float]]:
    bboxes = [polygon.bounding_box() for polygon in polygons if polygon.bounding_box() is not None]
    min_x = min(float(bbox[0][0]) for bbox in bboxes)
    min_y = min(float(bbox[0][1]) for bbox in bboxes)
    max_x = max(float(bbox[1][0]) for bbox in bboxes)
    max_y = max(float(bbox[1][1]) for bbox in bboxes)
    return ((min_x, min_y), (max_x, max_y))


def _enclosure_against_metals(via_bbox, lower_polygons, upper_polygons) -> tuple[float, float, float, float] | None:
    lower_bbox = _containing_bbox(via_bbox, lower_polygons)
    upper_bbox = _containing_bbox(via_bbox, upper_polygons)
    if lower_bbox is None or upper_bbox is None:
        return None
    return (
        min(via_bbox[0][0] - lower_bbox[0][0], via_bbox[0][0] - upper_bbox[0][0]),
        min(lower_bbox[1][0] - via_bbox[1][0], upper_bbox[1][0] - via_bbox[1][0]),
        min(via_bbox[0][1] - lower_bbox[0][1], via_bbox[0][1] - upper_bbox[0][1]),
        min(lower_bbox[1][1] - via_bbox[1][1], upper_bbox[1][1] - via_bbox[1][1]),
    )


def _containing_bbox(via_bbox, polygons):
    for polygon in polygons:
        bbox = polygon.bounding_box()
        if bbox is None:
            continue
        if (
            float(bbox[0][0]) - 1.0e-9 <= via_bbox[0][0] <= via_bbox[1][0] <= float(bbox[1][0]) + 1.0e-9
            and float(bbox[0][1]) - 1.0e-9 <= via_bbox[0][1] <= via_bbox[1][1] <= float(bbox[1][1]) + 1.0e-9
        ):
            return ((float(bbox[0][0]), float(bbox[0][1])), (float(bbox[1][0]), float(bbox[1][1])))
    return None


def _max_opposite_pair(enclosure: tuple[float, float, float, float]) -> float:
    left, right, bottom, top = enclosure
    return max(min(left, right), min(bottom, top))


def _enclosure_is_legal(enclosure: tuple[float, float, float, float], rule: ViaFamilyRule) -> bool:
    min_all_sides = min(enclosure)
    if any(min_all_sides + 1.0e-9 >= value for value in rule.legal_min_all_sides_um):
        return True
    opposite = _max_opposite_pair(enclosure)
    if any(opposite + 1.0e-9 >= value for value in rule.legal_min_opposite_sides_um):
        return True
    return False


def _meets_redundancy_rule(
    *,
    via_count: int,
    max_spacing_um: float | None,
    metal_width_um: float,
    metal_length_um: float,
    family_rule: ViaFamilyRule,
) -> bool:
    for requirement in family_rule.wide_metal_requirements:
        if metal_width_um + 1.0e-9 < requirement.min_width_um or metal_length_um + 1.0e-9 < requirement.min_length_um:
            continue
        if not requirement.options:
            return via_count >= 2
        if max_spacing_um is None:
            return False
        if not any(
            via_count >= option.min_via_count and max_spacing_um <= option.max_spacing_um + 1.0e-9
            for option in requirement.options
        ):
            return False
    return True


def _format_bridge_stack_layers(bundle: CenterTappedInductorGeometry) -> str:
    parts: list[str] = []
    for stack in bundle.bridge_endpoint_stacks:
        for stage in stack.stages:
            parts.append(f"{stack.name}:{stage.name}@{int(stage.layer)}")
    return ", ".join(parts)


def _format_layer_groups(groups) -> str:
    return ", ".join(f"{group.role}@{int(group.layer)}[{len(group.polygons)}]" for group in groups)

def _bbox_overlaps_with_margin(a, b, margin_um: float) -> bool:
    (ax0, ay0), (ax1, ay1) = a.bounding_box()
    (bx0, by0), (bx1, by1) = b.bounding_box()
    return (ax0 - margin_um) <= bx1 and bx0 <= (ax1 + margin_um) and (ay0 - margin_um) <= by1 and by0 <= (ay1 + margin_um)


def _merge_same_layer_polygons(polygons, *, layer: int):
    import gdstk

    if not polygons:
        return tuple()
    merged = gdstk.boolean(list(polygons), [], "or", layer=layer, datatype=0)
    return tuple(merged if merged is not None else polygons)


def _generic_same_layer_spacing_margin_um(trace_width_um: float) -> float:
    return max(0.5, min(2.0, 0.10 * float(trace_width_um)))


def _expand_polygon_region(polygons, *, margin_um: float):
    import gdstk

    if isinstance(polygons, gdstk.Polygon):
        polygons = (polygons,)
    if not polygons or margin_um <= 0.0:
        return tuple(polygons)
    expanded = gdstk.offset(
        list(polygons),
        margin_um,
        join="miter",
        tolerance=2.0,
        precision=1.0e-4,
        use_union=True,
        layer=int(polygons[0].layer),
        datatype=int(polygons[0].datatype),
    )
    return tuple(expanded if expanded is not None else polygons)
