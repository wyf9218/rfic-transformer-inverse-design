"""Analytical feasibility checks for parameterized transformer geometries."""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..core.adapter import TransformerOptimizationAdapter
from .drc_rules import audit_tsmc65_top_metal_geometry
from .export import (
    VddBarPlacement,
    _expand_bbox,
    _power_line_8port_label_map,
    _power_line_8port_opening_base_bbox,
    _power_line_8port_opening_bbox_with_fixed_port_overlap,
    _power_line_8port_port_overlap_evidence,
    _power_line_height_ratio,
    _power_line_max_outer_height_um,
    _power_line_port_ground_overlap_um,
    _power_line_shield_opening_clearance_um,
    _vdd_bar_center_x_outside_coil_union,
    _vdd_bar_tap_side_for_inductor,
)


PARAMETERIZED_GEOMETRY_NAMES = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)


def audit_parameterized_transformer_geometry(
    values_by_name: Mapping[str, Any],
    run_config: Any,
    *,
    adapter: TransformerOptimizationAdapter | None = None,
) -> dict[str, Any]:
    """Rebuild and audit one independent 10-D geometry vector.

    The production power-line flow represents the M9 and M10 trace widths with
    one shared ``line_width_um`` variable.  The optimizer adapter still exposes
    primary and secondary widths separately, so this function owns that mapping
    and keeps optimization-time and post-hoc feasibility checks identical.
    """

    resolved_adapter = adapter or TransformerOptimizationAdapter(run_config.bounds)
    sync_line_width = bool(run_config.emx.power_line_8port.enabled)
    numeric_values = {
        str(name): _finite_float(value)
        for name, value in values_by_name.items()
    }
    missing_fields = [
        name for name in PARAMETERIZED_GEOMETRY_NAMES if numeric_values.get(name) is None
    ]
    shared_width = numeric_values.get("line_width_um")
    vector: list[float] = []
    adapter_missing: list[str] = []
    for field in resolved_adapter.field_order():
        if sync_line_width and field in {"primary_width_um", "secondary_width_um"}:
            value = shared_width
        else:
            value = numeric_values.get(field)
        if value is None:
            adapter_missing.append(str(field))
        else:
            vector.append(float(value))
    missing_fields = list(dict.fromkeys([*missing_fields, *adapter_missing]))

    bounds_errors: list[str] = []
    topology_errors: list[str] = []
    drc_errors: list[str] = []
    port_ground_overlap_audit: dict[str, Any] | None = None
    build_error = ""
    if not missing_fields:
        try:
            geometry = resolved_adapter.from_vector(vector)
            if sync_line_width:
                if shared_width is None:
                    raise ValueError("power_line_8port geometry is missing line_width_um")
                geometry = geometry.with_shared_line_width(shared_width)
            bounds_errors = list(resolved_adapter.search_space.validate(geometry))
            topology_errors = list(geometry.validate())
            drc = audit_tsmc65_top_metal_geometry(geometry, run_config)
            drc_errors = [str(item) for item in drc.get("errors") or []]
            port_ground_overlap_audit = audit_power_line_8port_port_ground_overlap(
                geometry,
                run_config,
            )
        except Exception as exc:  # noqa: BLE001 - exact failure is audit evidence.
            build_error = f"{type(exc).__name__}: {exc}"

    categories: list[str] = []
    if missing_fields:
        categories.append("missing_fields")
    if build_error:
        categories.append("geometry_build")
    if bounds_errors:
        categories.append("configured_bounds")
    if topology_errors:
        categories.append("coupled_topology")
    if drc_errors:
        categories.append("tsmc65_top_metal")
    if (
        port_ground_overlap_audit is not None
        and port_ground_overlap_audit.get("status") == "FAIL"
    ):
        categories.append("power_line_8port_port_ground_overlap")
    errors = [build_error] if build_error else []
    errors.extend(bounds_errors)
    errors.extend(topology_errors)
    errors.extend(drc_errors)
    if (
        port_ground_overlap_audit is not None
        and port_ground_overlap_audit.get("status") == "FAIL"
    ):
        errors.append(
            "power_line_8port port-ground overlap mismatch: "
            + ", ".join(port_ground_overlap_audit.get("failure_labels") or [])
        )
    return {
        "status": "PASS" if not categories else "FAIL",
        "failure_categories": categories,
        "missing_fields": missing_fields,
        "build_error": build_error,
        "bounds_errors": bounds_errors,
        "topology_errors": topology_errors,
        "drc_errors": drc_errors,
        "port_ground_overlap_audit": port_ground_overlap_audit,
        "bounds_error_count": len(bounds_errors),
        "topology_error_count": len(topology_errors),
        "drc_error_count": len(drc_errors),
        "error_count": len(errors) + len(missing_fields),
        "errors": errors,
        "field_order": list(resolved_adapter.field_order()),
        "shared_line_width_mapping_enabled": sync_line_width,
    }


def audit_power_line_8port_port_ground_overlap(
    geometry: Any,
    run_config: Any,
    *,
    absolute_tolerance_um: float = 1.0e-6,
) -> dict[str, Any]:
    """Plan the power-line opening and verify all eight 10 um overlaps.

    This is the geometry-only counterpart of the evidence written by
    ``export_transformer_layout``.  It deliberately reuses the exporter's
    placement and opening helpers so candidate screening and generated GDS use
    one definition of the port-ground contract.
    """

    if not bool(run_config.emx.power_line_8port.enabled):
        return {
            "status": "NOT_APPLICABLE",
            "enabled": False,
            "expected_overlap_um": None,
            "absolute_tolerance_um": float(absolute_tolerance_um),
            "ports": {},
            "failure_labels": [],
        }

    transformer = geometry.transformer_spec().with_shared_line_width(
        geometry.transformer_spec().primary.trace_width_um
    )
    labels = _power_line_8port_label_map(run_config)
    expected = _power_line_port_ground_overlap_um()
    tolerance = float(absolute_tolerance_um)
    line_width_um = float(transformer.primary.trace_width_um)
    signal_extension_um = (
        expected
        if run_config.emx.uses_shield_as_port_ground() and transformer.shield.enabled
        else 0.0
    )

    primary_signal_x = (
        -0.5 * float(transformer.primary.outer_width_um)
        - float(transformer.primary.feed_extension_um)
        - signal_extension_um
    )
    secondary_signal_x = (
        float(transformer.offset_um)
        + 0.5 * float(transformer.secondary.outer_width_um)
        + float(transformer.secondary.feed_extension_um)
        + signal_extension_um
    )
    signal_terminals = {
        "P001": (
            primary_signal_x,
            0.5 * float(transformer.primary.terminal_y_span_um),
        ),
        "P002": (
            primary_signal_x,
            -0.5 * float(transformer.primary.terminal_y_span_um),
        ),
        "P003": (
            secondary_signal_x,
            0.5 * float(transformer.secondary.terminal_y_span_um),
        ),
        "P004": (
            secondary_signal_x,
            -0.5 * float(transformer.secondary.terminal_y_span_um),
        ),
    }
    signal_external_labels = {
        "P001": labels["primary_top"],
        "P002": labels["primary_bottom"],
        "P003": labels["secondary_top"],
        "P004": labels["secondary_bottom"],
    }

    primary_tap_side = _vdd_bar_tap_side_for_inductor(
        side="left",
        turns=int(transformer.primary.turns),
    )
    secondary_tap_side = _vdd_bar_tap_side_for_inductor(
        side="right",
        turns=int(transformer.secondary.turns),
    )
    primary_bar = transformer.primary.vdd_bar
    secondary_bar = transformer.secondary.vdd_bar
    if primary_bar is None or secondary_bar is None:
        return {
            "status": "FAIL",
            "enabled": True,
            "expected_overlap_um": expected,
            "absolute_tolerance_um": tolerance,
            "ports": {},
            "failure_labels": ["missing_vdd_bar"],
        }

    primary_bar_width_um = (
        line_width_um if primary_bar.width_um is None else float(primary_bar.width_um)
    )
    secondary_bar_width_um = (
        line_width_um if secondary_bar.width_um is None else float(secondary_bar.width_um)
    )
    primary_center_x_um = _vdd_bar_center_x_outside_coil_union(
        tap_side=primary_tap_side,
        coil_center_x_um=0.0,
        coil_outer_width_um=float(transformer.primary.outer_width_um),
        other_coil_center_x_um=float(transformer.offset_um),
        other_coil_outer_width_um=float(transformer.secondary.outer_width_um),
        bar_width_um=primary_bar_width_um,
        bar_offset_um=float(primary_bar.offset_um),
    )
    secondary_center_x_um = _vdd_bar_center_x_outside_coil_union(
        tap_side=secondary_tap_side,
        coil_center_x_um=float(transformer.offset_um),
        coil_outer_width_um=float(transformer.secondary.outer_width_um),
        other_coil_center_x_um=0.0,
        other_coil_outer_width_um=float(transformer.primary.outer_width_um),
        bar_width_um=secondary_bar_width_um,
        bar_offset_um=float(secondary_bar.offset_um),
    )
    primary_power_top_key = "left_power_top"
    primary_power_bottom_key = "left_power_bottom"
    secondary_power_top_key = "right_power_top"
    secondary_power_bottom_key = "right_power_bottom"
    if primary_center_x_um > secondary_center_x_um:
        primary_power_top_key = "right_power_top"
        primary_power_bottom_key = "right_power_bottom"
        secondary_power_top_key = "left_power_top"
        secondary_power_bottom_key = "left_power_bottom"

    target_height_um = (
        _power_line_height_ratio(run_config)
        * _power_line_max_outer_height_um(transformer)
    )

    def _placement(
        *,
        name_prefix: str,
        center_x_um: float,
        width_um: float,
        top_key: str,
        bottom_key: str,
    ) -> VddBarPlacement:
        return VddBarPlacement(
            name_prefix=name_prefix,
            center_x_um=float(center_x_um),
            center_y_um=0.0,
            width_um=float(width_um),
            route_width_um=float(width_um),
            half_height_um=0.5 * float(target_height_um),
            bar_layer=0,
            bar_datatype=0,
            pin_layer=None,
            pin_datatype=0,
            top_port_label=labels[top_key],
            bottom_port_label=labels[bottom_key],
        )

    primary_placement = _placement(
        name_prefix="PVDD",
        center_x_um=primary_center_x_um,
        width_um=primary_bar_width_um,
        top_key=primary_power_top_key,
        bottom_key=primary_power_bottom_key,
    )
    secondary_placement = _placement(
        name_prefix="SVDD",
        center_x_um=secondary_center_x_um,
        width_um=secondary_bar_width_um,
        top_key=secondary_power_top_key,
        bottom_key=secondary_power_bottom_key,
    )
    central_bbox = _power_line_8port_opening_base_bbox(
        primary_center_x_um=0.0,
        primary_outer_width_um=float(transformer.primary.outer_width_um),
        primary_outer_height_um=float(transformer.primary.outer_height_um),
        secondary_center_x_um=float(transformer.offset_um),
        secondary_outer_width_um=float(transformer.secondary.outer_width_um),
        secondary_outer_height_um=float(transformer.secondary.outer_height_um),
        primary_bar=primary_placement,
        secondary_bar=secondary_placement,
        primary_terminals=None,
        secondary_terminals=None,
    )
    clearance_bbox = _expand_bbox(
        central_bbox,
        _power_line_shield_opening_clearance_um(),
    )
    shield_inner_bbox = _power_line_8port_opening_bbox_with_fixed_port_overlap(
        central_bbox=clearance_bbox,
        signal_terminals=signal_terminals,
        primary_bar=primary_placement,
        secondary_bar=secondary_placement,
        port_ground_overlap_um=expected,
    )
    evidence = _power_line_8port_port_overlap_evidence(
        signal_terminals=signal_terminals,
        signal_external_labels=signal_external_labels,
        primary_bar=primary_placement,
        secondary_bar=secondary_placement,
        shield_inner_bbox=shield_inner_bbox,
        expected_overlap_um=expected,
    )
    ports = {} if evidence is None else dict(evidence.get("ports") or {})
    failure_labels = [
        label
        for label, record in ports.items()
        if not math.isclose(
            float(record.get("measured_overlap_um") or 0.0),
            expected,
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    ]
    return {
        "status": "PASS" if len(ports) == 8 and not failure_labels else "FAIL",
        "enabled": True,
        "expected_overlap_um": expected,
        "absolute_tolerance_um": tolerance,
        "shield_inner_bbox_um": {
            "min_x_um": float(shield_inner_bbox[0]),
            "min_y_um": float(shield_inner_bbox[1]),
            "max_x_um": float(shield_inner_bbox[2]),
            "max_y_um": float(shield_inner_bbox[3]),
        },
        "ports": ports,
        "failure_labels": failure_labels,
    }


def project_power_line_8port_port_ground_overlap(
    values_by_name: Mapping[str, Any],
    run_config: Any,
    *,
    adapter: TransformerOptimizationAdapter | None = None,
    maximum_iterations: int = 2,
    safety_margin_um: float = 1.0e-8,
) -> dict[str, Any]:
    """Increase only the feed that prevents a fixed 10 um ground overlap.

    The measured deficit comes from the same exporter helpers used by the GDS
    path.  No response label is read and no other geometry coordinate is
    changed.  Bounds and every other topology/DRC condition remain fail-closed
    in the final full geometry audit.
    """

    if maximum_iterations < 1:
        raise ValueError("maximum_iterations must be positive")
    if not math.isfinite(float(safety_margin_um)) or float(safety_margin_um) < 0.0:
        raise ValueError("safety_margin_um must be finite and nonnegative")

    projected_values = {
        name: _finite_float(values_by_name.get(name))
        for name in PARAMETERIZED_GEOMETRY_NAMES
    }
    before = audit_parameterized_transformer_geometry(
        projected_values,
        run_config,
        adapter=adapter,
    )
    current = before
    iterations: list[dict[str, Any]] = []
    primary_total_delta = 0.0
    secondary_total_delta = 0.0

    for iteration in range(1, maximum_iterations + 1):
        categories = list(current.get("failure_categories") or [])
        if current.get("status") == "PASS":
            break
        if categories != ["power_line_8port_port_ground_overlap"]:
            break
        overlap = current.get("port_ground_overlap_audit") or {}
        ports = overlap.get("ports") or {}
        expected = _finite_float(overlap.get("expected_overlap_um"))
        if expected is None or not ports:
            break

        primary_deficit = 0.0
        secondary_deficit = 0.0
        for record in ports.values():
            internal_label = str(record.get("internal_signal_label") or "")
            measured = _finite_float(record.get("measured_overlap_um"))
            if measured is None:
                continue
            deficit = max(0.0, expected - measured)
            if internal_label in {"P001", "P002"}:
                primary_deficit = max(primary_deficit, deficit)
            elif internal_label in {"P003", "P004"}:
                secondary_deficit = max(secondary_deficit, deficit)

        primary_increment = primary_deficit + (
            float(safety_margin_um) if primary_deficit > 0.0 else 0.0
        )
        secondary_increment = secondary_deficit + (
            float(safety_margin_um) if secondary_deficit > 0.0 else 0.0
        )
        if primary_increment <= 0.0 and secondary_increment <= 0.0:
            break

        if primary_increment > 0.0:
            value = projected_values.get("primary_feed_extension_um")
            if value is None:
                break
            projected_values["primary_feed_extension_um"] = value + primary_increment
            primary_total_delta += primary_increment
        if secondary_increment > 0.0:
            value = projected_values.get("secondary_feed_extension_um")
            if value is None:
                break
            projected_values["secondary_feed_extension_um"] = value + secondary_increment
            secondary_total_delta += secondary_increment

        current = audit_parameterized_transformer_geometry(
            projected_values,
            run_config,
            adapter=adapter,
        )
        iterations.append(
            {
                "iteration": iteration,
                "primary_feed_increment_um": primary_increment,
                "secondary_feed_increment_um": secondary_increment,
                "status_after_increment": current.get("status"),
                "failure_categories_after_increment": list(
                    current.get("failure_categories") or []
                ),
            }
        )

    return {
        "status": current.get("status"),
        "repair_applied": bool(primary_total_delta > 0.0 or secondary_total_delta > 0.0),
        "repair_scope": "feed_extension_only_for_exact_port_ground_overlap",
        "projected_values": projected_values,
        "primary_feed_total_delta_um": primary_total_delta,
        "secondary_feed_total_delta_um": secondary_total_delta,
        "iteration_count": len(iterations),
        "iterations": iterations,
        "before_audit": before,
        "after_audit": current,
        "scientific_boundary": (
            "This deterministic label-free projection enforces only the exporter's fixed port-ground overlap. "
            "A PASS is not foundry sign-off DRC, EMX/HFSS agreement, or measurement evidence."
        ),
    }


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
