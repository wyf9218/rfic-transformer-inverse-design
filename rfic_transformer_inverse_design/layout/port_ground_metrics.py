"""Post-streamout measurements of the existing eight port/ground relationships."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .foundry_audit import (
    AREA_TOLERANCE_UM2,
    _actual_grid_audit,
    _boolean_area,
    _expected_stitch_polygons,
)

SCHEMA = "rfic_transformer.actual_gds_port_ground_metrics.v1"
FRAME = "GDS_TOP_CELL_LOCAL_XY_UM_AFTER_SNAP"
GRID_UM = 0.005
TOLERANCE_UM = 1.0e-9
COUNT = "power_line_8port_port_ground_overlap_verified_port_count"
ERROR = "power_line_8port_port_ground_overlap_max_abs_error_um"
EVIDENCE = "power_line_8port_port_ground_overlap_checks"
NOT_EVALUATED = "NOT_EVALUATED_DUE_TO_UPSTREAM_CONTRACT_FAILURE"

# Resolved by the approved exporter labels and physical-left/right bar contract.
# Auxiliary P005-P008 are grounded bar ends, not extra Touchstone ports.
PAIRS = {
    "P001": ("P001_G", "left", (74, 0), "primary_top"),
    "P002": ("P002_G", "left", (74, 0), "primary_bottom"),
    "P003": ("P003_G", "right", (39, 60), "secondary_top"),
    "P004": ("P004_G", "right", (39, 60), "secondary_bottom"),
    "P005": ("P005", "top", (39, 60), "left_power_top"),
    "P006": ("P006", "bottom", (39, 60), "left_power_bottom"),
    "P007": ("P007", "top", (74, 0), "right_power_top"),
    "P008": ("P008", "bottom", (74, 0), "right_power_bottom"),
}


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a geometric coordinate")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite geometry metric")
    return number


def _snap(value: Any) -> float:
    return round(_finite(value) / GRID_UM) * GRID_UM


def _bbox(polygons: list[Any]) -> list[float] | None:
    if not polygons:
        return None
    boxes = [p.bounding_box() for p in polygons]
    return [min(b[0][0] for b in boxes), min(b[0][1] for b in boxes),
            max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)]


def aggregate_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Missing measurements remain unknown, never an invented zero residual."""
    errors = [r.get("absolute_error_um") for r in checks]
    maximum = max(_finite(e) for e in errors) if errors and all(e is not None for e in errors) else None
    return {COUNT: sum(r.get("passed") is True for r in checks), ERROR: maximum}


def attach_actual_gds_metrics(
    geometry_check: dict[str, Any], *, gds_path: Path,
    power_line_audit: Mapping[str, Any], foundry_audit: Mapping[str, Any],
) -> dict[str, Any]:
    result = measure_port_ground_metrics(
        gds_path=gds_path, power_line_audit=power_line_audit, foundry_audit=foundry_audit,
    )
    geometry_check.setdefault("metrics", {}).update(result["metrics"])
    geometry_check["actual_gds_port_ground_metrics"] = result
    return result


def measure_port_ground_metrics(
    *, gds_path: Path, power_line_audit: Mapping[str, Any],
    foundry_audit: Mapping[str, Any], coordinate_frame: str = FRAME,
) -> dict[str, Any]:
    """Read geometry only. This function never writes GDS or starts a tool."""
    import gdstk

    gds_path = Path(gds_path)
    gds_sha = hashlib.sha256(gds_path.read_bytes()).hexdigest()
    library = gdstk.read_gds(str(gds_path))
    tops = library.top_level()
    top = tops[0] if len(tops) == 1 and tops[0].name == "TRANSFORMER" else None
    polygons = list(top.get_polygons(apply_repetitions=True, include_paths=True, depth=None)) if top else []
    labels = list(top.get_labels(apply_repetitions=True, depth=None)) if top else []
    actual: dict[tuple[int, int], list[Any]] = {}
    for polygon in polygons:
        actual.setdefault((polygon.layer, polygon.datatype), []).append(polygon)
    grid = _actual_grid_audit(polygons, labels=labels, grid_um=GRID_UM)
    grid_ok = grid["off_grid_vertex_count"] == 0 and grid["off_grid_label_count"] == 0
    frame = foundry_audit.get("ground_frame") or {}
    inner = frame.get("snapped_inner_bbox_um")
    nominal = (power_line_audit.get("port_ground_overlap_evidence") or {}).get("ports") or {}
    process = (power_line_audit.get("process_layer_summary") or {}).get("records") or {}
    declared_labels = power_line_audit.get("labels") or {}
    declared_ground = process.get("shield_m5_draw") or {}
    common_ok = (
        top is not None and coordinate_frame == FRAME and grid_ok
        and foundry_audit.get("gds_sha256") == gds_sha
        and power_line_audit.get("enabled") is True
        and power_line_audit.get("touchstone_mode") == "signal_4_grounded_aux"
        and power_line_audit.get("port_ground_overlap_um") == 10.0
        and frame.get("manufacturing_grid_um") == GRID_UM
        and isinstance(inner, list) and len(inner) == 4
        and (declared_ground.get("layer"), declared_ground.get("datatype")) == (35, 0)
        and set(nominal) == set(PAIRS)
    )
    checks = []
    for port, (ground_id, side, signal_pair, label_role) in PAIRS.items():
        source = nominal.get(port) or {}
        record: dict[str, Any] = {
            "port_id": port, "ground_id": ground_id, "side": side,
            "signal_layer": signal_pair[0], "signal_datatype": signal_pair[1],
            "ground_layer": 35, "ground_datatype": 0,
            "coordinate_frame": coordinate_frame, "grid_aligned": grid_ok,
            "expected_geometry": {}, "observed_geometry": {},
            "overlap_area_um2": 0.0, "absolute_error_um": None,
            "passed": False, "failure_reason": None,
        }
        failures = []
        if not common_ok:
            failures.append("source_layer_frame_grid_or_top_cell_contract_invalid")
        if declared_labels.get(label_role) != port or source.get("side") != side:
            failures.append("port_object_identity_mismatch")
        winding_role = "primary_m10_draw" if signal_pair == (74, 0) else "secondary_m9_draw"
        declared_signal = process.get(winding_role) or {}
        if (declared_signal.get("layer"), declared_signal.get("datatype")) != signal_pair:
            failures.append("signal_layer_identity_mismatch")
        if not any(l.text == ground_id and (l.layer, l.texttype) == (135, 0) for l in labels):
            failures.append("ground_reference_label_missing_or_wrong_layer")
        if port in {"P005", "P006", "P007", "P008"}:
            bar = power_line_audit.get("secondary_power_line" if port in {"P005", "P006"} else "primary_power_line") or {}
            end = "top" if side == "top" else "bottom"
            if not (bar.get(f"{end}_port_label") == port and bar.get(f"{end}_ground_label") == ground_id
                    and (bar.get("bar_layer"), bar.get("bar_datatype")) == signal_pair):
                failures.append("grounded_bar_object_identity_mismatch")
        else:
            pin_pair = (126, 0) if signal_pair == (74, 0) else (139, 0)
            if not any(l.text == port and (l.layer, l.texttype) == pin_pair for l in labels):
                failures.append("signal_pin_label_missing_or_wrong_layer")
        try:
            x, y = _snap(source["terminal_x_um"]), _snap(source["terminal_y_um"])
            horizontal = side in {"left", "right"}
            axis = 0 if horizontal else 1
            edge_index = {"left": 0, "right": 2, "top": 3, "bottom": 1}[side]
            expected_inner = _finite(inner[edge_index])
            expected_terminal = x if horizontal else y
            width = _finite(power_line_audit["line_width_um"])
            if width <= 0:
                raise ValueError("nonpositive line width")
            cross = y if horizontal else x
            lo, hi = _snap(cross - width / 2), _snap(cross + width / 2)
            # Probe both sides of the expected edge; observe the actual outward
            # signal edge and inward M5 edge, never the nominal measured_overlap.
            limits = (expected_terminal - 20, lo, expected_terminal + 20, hi) if horizontal else (lo, expected_terminal - 20, hi, expected_terminal + 20)
            probe = gdstk.rectangle(limits[:2], limits[2:])
            signal = gdstk.boolean(actual.get(signal_pair, []), [probe], "and", precision=1e-6)
            ground = gdstk.boolean(actual.get((35, 0), []), [probe], "and", precision=1e-6)
            sb, gb = _bbox(signal), _bbox(ground)
            record["expected_geometry"] = {
                "terminal_edge_um": expected_terminal, "ground_inner_edge_um": expected_inner,
                "overlap_um": 10.0, "probe_bbox_um": list(limits),
            }
            record["observed_geometry"] = {"signal_bbox_um": sb, "ground_bbox_um": gb}
            if sb is None or gb is None:
                failures.append("signal_or_ground_geometry_missing")
            else:
                outward_min = side in {"left", "bottom"}
                signal_edge = sb[axis if outward_min else axis + 2]
                ground_edge = gb[axis + 2 if outward_min else axis]
                observed = ground_edge - signal_edge if outward_min else signal_edge - ground_edge
                # Coordinates have already passed the grid audit. Integer grid
                # arithmetic removes binary roundoff, not a manufacturing step.
                delta = abs(round(observed / GRID_UM) - round(10.0 / GRID_UM)) * GRID_UM
                position_error = max(abs(round((signal_edge - expected_terminal) / GRID_UM)),
                                     abs(round((ground_edge - expected_inner) / GRID_UM))) * GRID_UM
                error = max(delta, position_error)
                intersection = gdstk.boolean(signal, ground, "and", precision=1e-6)
                area = sum(p.area() for p in intersection)
                record.update(overlap_area_um2=area, absolute_error_um=error)
                record["observed_geometry"].update(terminal_edge_um=signal_edge, ground_inner_edge_um=ground_edge,
                                                    overlap_um=round(observed / GRID_UM) * GRID_UM)
                if area <= AREA_TOLERANCE_UM2:
                    failures.append("no_actual_signal_ground_planar_overlap")
                if error > TOLERANCE_UM:
                    failures.append("post_snap_overlap_or_edge_position_mismatch")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            failures.append(f"incomplete_geometric_reference: {exc}")
        record["passed"] = not failures
        record["failure_reason"] = "; ".join(failures) if failures else None
        checks.append(record)
    metrics = aggregate_checks(checks)
    overlap_pass = all(r["passed"] for r in checks) and metrics[COUNT] == len(PAIRS) and metrics[ERROR] == 0.0
    via = measure_via_and_landing(actual, power_line_audit) if overlap_pass else {"overall_status": NOT_EVALUATED}
    return {
        "schema": SCHEMA, "coordinate_frame": coordinate_frame,
        "gds_sha256": gds_sha, "manufacturing_grid_um": GRID_UM,
        "tolerance_um": TOLERANCE_UM, "grid_audit": grid,
        "metrics": metrics, EVIDENCE: checks,
        "power_line_check": "PASS" if overlap_pass else "FAIL",
        "via_stack_check": via, "simulator_action_taken": False,
    }


def measure_via_and_landing(actual: Mapping[tuple[int, int], list[Any]], power_line_audit: Mapping[str, Any]) -> dict[str, Any]:
    """Independent Boolean tests of real via cuts and landing-pad polygons."""
    import gdstk

    metal, vias = _expected_stitch_polygons(power_line_audit, grid_um=GRID_UM)
    missing_metal = sum(_boolean_area(expected, actual.get(pair, []), "not", GRID_UM) for pair, expected in metal.items())
    missing_via = extra_via = 0.0
    for pair, groups in vias.items():
        for expected, footprint in groups:
            missing_via += _boolean_area(expected, actual.get(pair, []), "not", GRID_UM)
            local = gdstk.boolean(actual.get(pair, []), [footprint], "and", precision=1e-6)
            extra_via += _boolean_area(local, expected, "not", GRID_UM)
    passed = bool(metal) and bool(vias) and all(x <= AREA_TOLERANCE_UM2 for x in (missing_metal, missing_via, extra_via))
    return {"overall_status": "PASS" if passed else "FAIL", "metal_landing_missing_area_um2": missing_metal,
            "via_missing_area_um2": missing_via, "via_unexpected_area_um2": extra_via}


def validate_metric_evidence(geometry_check: Mapping[str, Any], *, gds_sha256: str) -> dict[str, Any]:
    """No fallback: missing aggregates, malformed or inconsistent evidence FAIL."""
    evidence = geometry_check.get("actual_gds_port_ground_metrics")
    try:
        if not isinstance(evidence, Mapping) or evidence.get("schema") != SCHEMA:
            raise ValueError("actual-GDS metric evidence missing")
        if evidence.get("coordinate_frame") != FRAME or evidence.get("gds_sha256") != gds_sha256:
            raise ValueError("actual-GDS metric identity mismatch")
        if evidence.get("manufacturing_grid_um") != GRID_UM or evidence.get("tolerance_um") != TOLERANCE_UM:
            raise ValueError("actual-GDS metric grid or tolerance drift")
        checks = evidence[EVIDENCE]
        if len(checks) != len(PAIRS) or [r["port_id"] for r in checks] != list(PAIRS):
            raise ValueError("exact ordered port evidence missing")
        for r in checks:
            ground, side, pair, _ = PAIRS[r["port_id"]]
            if (r["ground_id"], r["side"], r["signal_layer"], r["signal_datatype"], r["ground_layer"], r["ground_datatype"]) != (ground, side, *pair, 35, 0):
                raise ValueError("port/ground object or layer mismatch")
            if r["coordinate_frame"] != FRAME:
                raise ValueError("coordinate reference frame mismatch")
            observed, expected = r["observed_geometry"], r["expected_geometry"]
            if r["absolute_error_um"] is not None:
                if _finite(expected["overlap_um"]) != 10.0:
                    raise ValueError("expected overlap contract drift")
                errors = [abs(round((_finite(observed["overlap_um"]) - 10.0) / GRID_UM)) * GRID_UM]
                for key in ("terminal_edge_um", "ground_inner_edge_um"):
                    errors.append(abs(round((_finite(observed[key]) - _finite(expected[key])) / GRID_UM)) * GRID_UM)
                if _finite(r["absolute_error_um"]) != max(errors):
                    raise ValueError("residual contradicts observed coordinates")
            if r["passed"] is True and not (r["grid_aligned"] is True and r["failure_reason"] is None
                and _finite(r["absolute_error_um"]) <= TOLERANCE_UM and _finite(r["overlap_area_um2"]) > AREA_TOLERANCE_UM2):
                raise ValueError("passed flag contradicts geometric evidence")
        derived = aggregate_checks(checks)
        for key, value in derived.items():
            if key not in geometry_check["metrics"] or geometry_check["metrics"][key] != value or evidence["metrics"].get(key) != value:
                raise ValueError(f"aggregate/evidence mismatch: {key}")
        if derived[COUNT] != len(PAIRS) or derived[ERROR] != 0.0 or evidence.get("power_line_check") != "PASS":
            raise ValueError("actual-GDS overlap geometry FAIL")
        via = evidence["via_stack_check"]
        via_pass = via.get("overall_status") == "PASS" and all(
            0.0 <= _finite(via.get(k)) <= AREA_TOLERANCE_UM2 for k in (
                "metal_landing_missing_area_um2", "via_missing_area_um2", "via_unexpected_area_um2"))
        return {"power_line_check": "PASS", "via_stack_check": "PASS" if via_pass else "FAIL", "error": None}
    except (KeyError, TypeError, ValueError) as exc:
        return {"power_line_check": "FAIL", "via_stack_check": NOT_EVALUATED, "error": str(exc)}
