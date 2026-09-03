"""Post-Cadence audit for the frozen Broadband56 foundry-layout contract."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
)
from ..core.defaults import load_run_config
from ..process import parse_proc_file
from .export import _foundry_slotted_ground_frame, _resolve_export_pair


AUDIT_SCHEMA = "rfic_transformer_foundry_layout_audit.v1"
GRID_SCHEMA = "rfic_transformer_foundry_grid_canonicalization.v1"
FRAME_SCHEMA = "rfic_transformer_foundry_slotted_ground_frame.v1"
BRIDGE_SCHEMA = "rfic_transformer_foundry_bridge_connections.v1"
VIA_SCHEMA = "rfic_transformer_foundry_via_and_landing_audit.v1"
SOURCE_AUDIT_FILENAME = "foundry_layout_source_audit.json"
FINAL_AUDIT_FILENAME = "foundry_layout_audit.json"
CONTRACT_RELATIVE_PATH = Path("docs/research/FOUNDRY_LAYOUT_AUDIT_CONTRACT.json")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
AREA_TOLERANCE_UM2 = 1.0e-6
COORDINATE_TOLERANCE_GRID_UNITS = 1.0e-6


class FoundryLayoutAuditError(RuntimeError):
    """Raised when a foundry-layout artifact cannot be trusted."""


def produce_foundry_layout_audit(
    *,
    gds_path: Path,
    source_audit_path: Path,
    power_line_audit_path: Path,
    config_path: Path,
    contract_path: Path,
    candidate: Mapping[str, Any],
    stage_id: str,
    output_path: Path,
) -> dict[str, Any]:
    """Audit one actual Cadence GDS and atomically publish its sidecar."""

    gds_path = _regular_file(gds_path, "Cadence GDS")
    source_audit_path = _regular_file(source_audit_path, "source foundry-layout audit")
    power_line_audit_path = _regular_file(power_line_audit_path, "power-line audit")
    config_path = _regular_file(config_path, "private configuration")
    contract_path = _regular_file(contract_path, "foundry-layout audit contract")
    output_path = Path(output_path).expanduser().resolve()
    if output_path.exists():
        raise FoundryLayoutAuditError(
            f"no-clobber final foundry-layout audit already exists: {output_path}"
        )

    source_audit = _read_json(source_audit_path, "source foundry-layout audit")
    power_line_audit = _read_json(power_line_audit_path, "power-line audit")
    contract = _read_json(contract_path, "foundry-layout audit contract")
    run_config = load_run_config(config_path)
    geometry = _geometry_vector(candidate)
    geometry_sha = canonical_geometry_sha256(geometry)
    candidate_sha = _sha_value(
        candidate.get("candidate_id_sha256"), "candidate_id_sha256"
    )
    bound_geometry_sha = _sha_value(
        candidate.get("candidate_geometry_identity_sha256"),
        "candidate_geometry_identity_sha256",
    )
    recorded_geometry_sha = _sha_value(
        candidate.get("geometry_sha256"), "geometry_sha256"
    )
    geometry_identity_pass = (
        geometry_sha == bound_geometry_sha == recorded_geometry_sha == candidate_sha
    )

    _validate_contract(contract, run_config=run_config)
    _validate_source_audit(source_audit, run_config=run_config)
    _validate_power_line_audit_shape(power_line_audit)

    gds_result = _audit_actual_gds(
        gds_path=gds_path,
        source_audit=source_audit,
        power_line_audit=power_line_audit,
        run_config=run_config,
        contract=contract,
        expected_top_cell=str(contract["top_cell"]),
    )
    implementation_path = Path(__file__).resolve()
    checks = {
        **gds_result["checks"],
        "geometry_identity_recomputed": geometry_identity_pass,
        "private_configuration_sha256_bound": True,
        "candidate_and_stage_identity_bound": bool(
            stage_id and candidate.get("candidate_id") and candidate_sha
        ),
    }
    failure_reasons = [name for name, passed in checks.items() if passed is not True]
    overall_status = "PASS" if not failure_reasons else "FAIL"
    payload: dict[str, Any] = {
        "schema": AUDIT_SCHEMA,
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "audit_boundary": "POST_CADENCE_STREAMOUT_PRE_CADENCE_PASS_PARTITION",
        "campaign_id": CAMPAIGN_ID,
        "stage_id": str(stage_id),
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "candidate_id_sha256": candidate_sha,
        "geometry_vector": geometry,
        "geometry_sha256": geometry_sha,
        "candidate_geometry_identity_sha256": bound_geometry_sha,
        "private_configuration": _file_record(config_path),
        "foundry_layout_contract": _file_record(contract_path),
        "gds": _file_record(gds_path),
        "gds_path": str(gds_path),
        "gds_size_bytes": gds_path.stat().st_size,
        "gds_sha256": _sha256(gds_path),
        "gds_top_cell": gds_result["top_cell"],
        "audit_implementation": _file_record(implementation_path),
        "source_layout_audit": _file_record(source_audit_path),
        "source_power_line_audit": _file_record(power_line_audit_path),
        "enabled": True,
        "manufacturing_grid_um": float(
            run_config.emx.foundry_layout.manufacturing_grid_um
        ),
        "grid_canonicalization": gds_result["grid_canonicalization"],
        "ground_frame": gds_result["ground_frame"],
        "power_line_bridge_connections": gds_result[
            "power_line_bridge_connections"
        ],
        "via_and_landing": gds_result["via_and_landing"],
        "layer_audit": gds_result["layer_audit"],
        "checks": checks,
        "failure_reasons": failure_reasons,
        "overall_status": overall_status,
        "automatic_emx_execution_authorized": False,
        "foundry_drc_executed": False,
    }
    _atomic_write_audit(
        output_path,
        payload,
        expected={
            "stage_id": str(stage_id),
            "candidate_id_sha256": candidate_sha,
            "geometry_sha256": geometry_sha,
            "config_sha256": _sha256(config_path),
            "gds_sha256": _sha256(gds_path),
            "contract_sha256": _sha256(contract_path),
        },
        require_pass=False,
    )
    return payload


def validate_foundry_layout_audit(
    audit: Mapping[str, Any],
    *,
    expected_stage_id: str,
    expected_candidate_id_sha256: str,
    expected_geometry_sha256: str,
    expected_config_sha256: str,
    expected_gds_sha256: str,
    expected_contract_sha256: str,
    require_pass: bool = True,
    verify_files: bool = True,
) -> None:
    """Validate schema, checks, identities, and live file hashes."""

    errors: list[str] = []
    _expect(errors, "schema", audit.get("schema"), AUDIT_SCHEMA)
    _expect(errors, "schema_version", audit.get("schema_version"), 1)
    _expect(
        errors,
        "audit_boundary",
        audit.get("audit_boundary"),
        "POST_CADENCE_STREAMOUT_PRE_CADENCE_PASS_PARTITION",
    )
    _expect(errors, "campaign_id", audit.get("campaign_id"), CAMPAIGN_ID)
    _expect(errors, "stage_id", audit.get("stage_id"), expected_stage_id)
    if not str(audit.get("candidate_id") or "").strip():
        errors.append("candidate_id is empty")
    _expect(
        errors,
        "candidate_id_sha256",
        audit.get("candidate_id_sha256"),
        expected_candidate_id_sha256,
    )
    _expect(
        errors,
        "geometry_sha256",
        audit.get("geometry_sha256"),
        expected_geometry_sha256,
    )
    _expect(
        errors,
        "candidate_geometry_identity_sha256",
        audit.get("candidate_geometry_identity_sha256"),
        expected_geometry_sha256,
    )
    _expect(errors, "enabled", audit.get("enabled"), True)
    _expect_close(
        errors,
        "manufacturing_grid_um",
        audit.get("manufacturing_grid_um"),
        0.005,
    )
    generated_utc = audit.get("generated_utc")
    try:
        parsed_utc = datetime.fromisoformat(str(generated_utc))
    except ValueError:
        parsed_utc = None
    if parsed_utc is None or parsed_utc.tzinfo is None:
        errors.append("generated_utc is not timezone-aware ISO-8601")

    geometry = audit.get("geometry_vector")
    if not isinstance(geometry, Mapping) or set(geometry) != set(GEOMETRY_FIELDS):
        errors.append("geometry_vector fields mismatch")
    else:
        try:
            recomputed_geometry_sha = canonical_geometry_sha256(geometry)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"geometry_vector cannot be hashed: {exc}")
        else:
            _expect(
                errors,
                "geometry_vector recomputed SHA-256",
                recomputed_geometry_sha,
                expected_geometry_sha256,
            )

    config = _identity_record(
        audit.get("private_configuration"), "private_configuration", errors
    )
    gds = _identity_record(audit.get("gds"), "gds", errors)
    contract = _identity_record(
        audit.get("foundry_layout_contract"), "foundry_layout_contract", errors
    )
    implementation = _identity_record(
        audit.get("audit_implementation"), "audit_implementation", errors
    )
    source_layout = _identity_record(
        audit.get("source_layout_audit"), "source_layout_audit", errors
    )
    source_power_line = _identity_record(
        audit.get("source_power_line_audit"), "source_power_line_audit", errors
    )
    if config is not None:
        _expect(
            errors,
            "private_configuration.sha256",
            config.get("sha256"),
            expected_config_sha256,
        )
    if gds is not None:
        _expect(errors, "gds.sha256", gds.get("sha256"), expected_gds_sha256)
        _expect(errors, "gds_sha256", audit.get("gds_sha256"), expected_gds_sha256)
        _expect(errors, "gds_size_bytes", audit.get("gds_size_bytes"), gds.get("size_bytes"))
        _expect(errors, "gds_path", audit.get("gds_path"), gds.get("path"))
    _expect(errors, "gds_top_cell", audit.get("gds_top_cell"), "TRANSFORMER")
    if contract is not None:
        _expect(
            errors,
            "foundry_layout_contract.sha256",
            contract.get("sha256"),
            expected_contract_sha256,
        )

    grid = _mapping(audit.get("grid_canonicalization"), "grid_canonicalization", errors)
    frame = _mapping(audit.get("ground_frame"), "ground_frame", errors)
    bridges = _mapping(
        audit.get("power_line_bridge_connections"),
        "power_line_bridge_connections",
        errors,
    )
    if grid is not None:
        _expect(errors, "grid.schema", grid.get("schema"), GRID_SCHEMA)
        _expect_close(errors, "grid.grid_um", grid.get("grid_um"), 0.005)
    if frame is not None:
        _expect(errors, "frame.schema", frame.get("schema"), FRAME_SCHEMA)
        _expect_close(
            errors,
            "frame.manufacturing_grid_um",
            frame.get("manufacturing_grid_um"),
            0.005,
        )
        _expect_close(
            errors,
            "frame.strap_width_um",
            frame.get("strap_width_um"),
            10.0,
        )
        _expect_close(
            errors,
            "frame.strap_pitch_um",
            frame.get("strap_pitch_um"),
            20.0,
        )
    if bridges is not None:
        _expect(errors, "bridges.schema", bridges.get("schema"), BRIDGE_SCHEMA)

    checks = _mapping(audit.get("checks"), "checks", errors)
    if checks is not None:
        required = {
            "actual_gds_present_and_nonempty",
            "actual_gds_sha256_bound",
            "top_cell_exact",
            "all_polygon_vertices_on_manufacturing_grid",
            "all_polygon_edges_horizontal_vertical_or_45_degree",
            "slotted_ground_frame_matches_generated_geometry",
            "power_line_bridge_primary_connected",
            "power_line_bridge_secondary_connected",
            "via_arrays_match_generated_geometry",
            "metal_landing_pads_match_generated_geometry",
            "required_foundry_layers_present",
            "geometry_identity_recomputed",
            "private_configuration_sha256_bound",
            "candidate_and_stage_identity_bound",
        }
        missing = required - set(checks)
        if missing:
            errors.append(f"checks lacks required fields: {sorted(missing)}")
        if require_pass:
            failed = sorted(name for name in required if checks.get(name) is not True)
            if failed:
                errors.append(f"checks are not all PASS: {failed}")

    status = audit.get("overall_status")
    if status not in {"PASS", "FAIL"}:
        errors.append("overall_status must be PASS or FAIL")
    if require_pass and status != "PASS":
        errors.append("overall_status is not PASS")
    failures = audit.get("failure_reasons")
    if not isinstance(failures, list):
        errors.append("failure_reasons must be a list")
    elif require_pass and failures:
        errors.append("PASS audit contains failure reasons")
    if checks is not None and status in {"PASS", "FAIL"}:
        failed_checks = sorted(name for name, passed in checks.items() if passed is not True)
        if status == "PASS" and failed_checks:
            errors.append("overall_status PASS contradicts failed checks")
        if status == "FAIL" and not failed_checks:
            errors.append("overall_status FAIL lacks failed checks")
        if isinstance(failures, list) and failures != failed_checks:
            errors.append("failure_reasons do not exactly match failed checks")

    if verify_files:
        for label, record in (
            ("private_configuration", config),
            ("gds", gds),
            ("foundry_layout_contract", contract),
            ("audit_implementation", implementation),
            ("source_layout_audit", source_layout),
            ("source_power_line_audit", source_power_line),
        ):
            if record is None:
                continue
            path = Path(str(record["path"])).expanduser().resolve()
            if not path.is_file() or path.stat().st_size <= 0:
                errors.append(f"{label} file is missing or empty")
                continue
            if path.stat().st_size != record["size_bytes"]:
                errors.append(f"{label} size changed")
            if _sha256(path) != record["sha256"]:
                errors.append(f"{label} SHA-256 changed")

    if errors:
        raise FoundryLayoutAuditError("; ".join(errors))


def load_and_validate_foundry_layout_audit(
    audit_path: Path,
    *,
    expected_stage_id: str,
    expected_candidate_id_sha256: str,
    expected_geometry_sha256: str,
    expected_config_sha256: str,
    expected_gds_sha256: str,
    expected_contract_sha256: str,
    require_pass: bool = True,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Load a nonempty JSON sidecar and apply the complete consumer contract."""

    resolved = _regular_file(audit_path, "foundry-layout audit")
    audit = _read_json(resolved, "foundry-layout audit")
    validate_foundry_layout_audit(
        audit,
        expected_stage_id=expected_stage_id,
        expected_candidate_id_sha256=expected_candidate_id_sha256,
        expected_geometry_sha256=expected_geometry_sha256,
        expected_config_sha256=expected_config_sha256,
        expected_gds_sha256=expected_gds_sha256,
        expected_contract_sha256=expected_contract_sha256,
        require_pass=require_pass,
        verify_files=verify_files,
    )
    return audit


def _audit_actual_gds(
    *,
    gds_path: Path,
    source_audit: Mapping[str, Any],
    power_line_audit: Mapping[str, Any],
    run_config: Any,
    contract: Mapping[str, Any],
    expected_top_cell: str,
) -> dict[str, Any]:
    import gdstk

    library = gdstk.read_gds(str(gds_path))
    top_cells = library.top_level()
    top = top_cells[0] if len(top_cells) == 1 else None
    top_cell = top.name if top is not None else None
    polygons = (
        list(top.get_polygons(apply_repetitions=True, include_paths=True, depth=None))
        if top is not None
        else []
    )
    labels = list(top.get_labels(apply_repetitions=True, depth=None)) if top is not None else []
    grid_um = float(run_config.emx.foundry_layout.manufacturing_grid_um)
    grid_audit = _actual_grid_audit(polygons, labels=labels, grid_um=grid_um)

    proc_info = parse_proc_file(run_config.emx.emx_process_file)
    shield_layer, shield_datatype = _resolve_export_pair(
        proc_info=proc_info,
        selected_layer=run_config.emx.shield_layer,
        fallback_datatype=run_config.emx.metal_datatype,
        role="drawing",
    )
    if shield_layer is None:
        shield_layer = int(run_config.emx.shield_layer)
    source_frame = _required_mapping(source_audit, "ground_frame")
    frame_polygons, generated_frame = _foundry_slotted_ground_frame(
        inner_bbox=tuple(float(v) for v in source_frame["requested_inner_bbox_um"]),
        frame_width_um=float(source_frame["requested_frame_width_um"]),
        strap_width_um=float(run_config.emx.foundry_layout.shield_strap_width_um),
        strap_pitch_um=float(run_config.emx.foundry_layout.shield_strap_pitch_um),
        manufacturing_grid_um=grid_um,
        layer=int(shield_layer),
        datatype=int(shield_datatype),
    )
    expected_metal_polygons, expected_via_polygons = _expected_stitch_polygons(
        power_line_audit
    )
    actual_by_pair: dict[tuple[int, int], list[Any]] = {}
    for polygon in polygons:
        actual_by_pair.setdefault(
            (int(polygon.layer), int(polygon.datatype)), []
        ).append(polygon)

    expected_m5 = [
        *frame_polygons,
        *expected_metal_polygons.get((int(shield_layer), int(shield_datatype)), []),
    ]
    actual_m5 = actual_by_pair.get((int(shield_layer), int(shield_datatype)), [])
    frame_missing_area = _boolean_area(expected_m5, actual_m5, "not", grid_um)
    frame_extra_area = _boolean_area(actual_m5, expected_m5, "not", grid_um)
    frame_pass = (
        frame_missing_area <= AREA_TOLERANCE_UM2
        and frame_extra_area <= AREA_TOLERANCE_UM2
    )
    ground_frame = {
        **generated_frame,
        "overall_status": "PASS" if frame_pass else "FAIL",
        "actual_layer": int(shield_layer),
        "actual_datatype": int(shield_datatype),
        "actual_pair_polygon_count": len(actual_m5),
        "actual_missing_area_um2": frame_missing_area,
        "actual_unexpected_area_um2": frame_extra_area,
    }

    landing_missing_area = 0.0
    for pair, expected in expected_metal_polygons.items():
        landing_missing_area += _boolean_area(
            expected,
            actual_by_pair.get(pair, []),
            "not",
            grid_um,
        )
    via_missing_area = 0.0
    via_extra_area = 0.0
    for pair, groups in expected_via_polygons.items():
        actual = actual_by_pair.get(pair, [])
        for expected, footprint in groups:
            via_missing_area += _boolean_area(expected, actual, "not", grid_um)
            actual_local = gdstk.boolean(
                actual,
                [footprint],
                "and",
                precision=max(grid_um * 0.1, 1.0e-6),
                layer=pair[0],
                datatype=pair[1],
            )
            via_extra_area += _boolean_area(actual_local, expected, "not", grid_um)
    via_pass = (
        via_missing_area <= AREA_TOLERANCE_UM2
        and via_extra_area <= AREA_TOLERANCE_UM2
    )
    landing_pass = landing_missing_area <= AREA_TOLERANCE_UM2
    via_and_landing = {
        "schema": VIA_SCHEMA,
        "overall_status": "PASS" if via_pass and landing_pass else "FAIL",
        "expected_stitch_count": 4,
        "observed_stitch_count": len(power_line_audit.get("power_line_ground_stitches") or []),
        "via_missing_area_um2": via_missing_area,
        "via_unexpected_area_um2": via_extra_area,
        "metal_landing_missing_area_um2": landing_missing_area,
    }

    source_bridges = _required_mapping(source_audit, "power_line_bridge_connections")
    bridges = {
        "schema": BRIDGE_SCHEMA,
        "grid_um": grid_um,
        "primary_bridge": _actual_bridge_record(
            source_bridges.get("primary_bridge"), actual_by_pair, grid_um=grid_um
        ),
        "secondary_bridge": _actual_bridge_record(
            source_bridges.get("secondary_bridge"), actual_by_pair, grid_um=grid_um
        ),
    }
    bridges["overall_status"] = (
        "PASS"
        if bridges["primary_bridge"].get("overall_status") == "PASS"
        and bridges["secondary_bridge"].get("overall_status") == "PASS"
        else "FAIL"
    )

    required_pairs = {
        name: (int(record["layer"]), int(record["datatype"]))
        for name, record in _required_mapping(contract, "layer_map").items()
        if isinstance(record, Mapping)
    }
    pair_counts = {
        name: len(actual_by_pair.get(pair, [])) for name, pair in required_pairs.items()
    }
    layers_pass = all(count > 0 for count in pair_counts.values())
    layer_audit = {
        "schema": "rfic_transformer_foundry_layer_audit.v1",
        "overall_status": "PASS" if layers_pass else "FAIL",
        "required_pair_polygon_counts": pair_counts,
    }

    checks = {
        "actual_gds_present_and_nonempty": gds_path.stat().st_size > 0,
        "actual_gds_sha256_bound": bool(_sha256(gds_path)),
        "top_cell_exact": top_cell == expected_top_cell,
        "all_polygon_vertices_on_manufacturing_grid": (
            grid_audit["off_grid_vertex_count"] == 0
        ),
        "all_polygon_edges_horizontal_vertical_or_45_degree": (
            grid_audit["noncanonical_edge_count"] == 0
        ),
        "slotted_ground_frame_matches_generated_geometry": frame_pass,
        "power_line_bridge_primary_connected": (
            bridges["primary_bridge"].get("overall_status") == "PASS"
        ),
        "power_line_bridge_secondary_connected": (
            bridges["secondary_bridge"].get("overall_status") == "PASS"
        ),
        "via_arrays_match_generated_geometry": via_pass,
        "metal_landing_pads_match_generated_geometry": landing_pass,
        "required_foundry_layers_present": layers_pass,
    }
    return {
        "top_cell": top_cell,
        "grid_canonicalization": grid_audit,
        "ground_frame": ground_frame,
        "power_line_bridge_connections": bridges,
        "via_and_landing": via_and_landing,
        "layer_audit": layer_audit,
        "checks": checks,
    }


def _actual_grid_audit(
    polygons: Sequence[Any], *, labels: Sequence[Any], grid_um: float
) -> dict[str, Any]:
    off_grid_vertices = 0
    off_grid_labels = 0
    edge_kind_counts = {"H": 0, "V": 0, "D": 0, "OTHER": 0}
    max_grid_error_um = 0.0
    max_orientation_error_um = 0.0
    for polygon in polygons:
        points = [(float(point[0]), float(point[1])) for point in polygon.points]
        for x_um, y_um in points:
            for value in (x_um, y_um):
                error_um = abs(value - round(value / grid_um) * grid_um)
                max_grid_error_um = max(max_grid_error_um, error_um)
                if error_um / grid_um > COORDINATE_TOLERANCE_GRID_UNITS:
                    off_grid_vertices += 1
                    break
        for index, (x_um, y_um) in enumerate(points):
            next_x, next_y = points[(index + 1) % len(points)]
            dx = abs(next_x - x_um)
            dy = abs(next_y - y_um)
            tolerance = max(grid_um * COORDINATE_TOLERANCE_GRID_UNITS, 1.0e-9)
            if dy <= tolerance and dx > tolerance:
                edge_kind_counts["H"] += 1
            elif dx <= tolerance and dy > tolerance:
                edge_kind_counts["V"] += 1
            elif dx > tolerance and dy > tolerance and abs(dx - dy) <= tolerance:
                edge_kind_counts["D"] += 1
            else:
                edge_kind_counts["OTHER"] += 1
                max_orientation_error_um = max(
                    max_orientation_error_um, min(dx, dy, abs(dx - dy))
                )
    for label in labels:
        for value in (float(label.origin[0]), float(label.origin[1])):
            error_um = abs(value - round(value / grid_um) * grid_um)
            max_grid_error_um = max(max_grid_error_um, error_um)
            if error_um / grid_um > COORDINATE_TOLERANCE_GRID_UNITS:
                off_grid_labels += 1
                break
    passed = (
        bool(polygons)
        and off_grid_vertices == 0
        and off_grid_labels == 0
        and edge_kind_counts["OTHER"] == 0
    )
    return {
        "schema": GRID_SCHEMA,
        "overall_status": "PASS" if passed else "FAIL",
        "grid_um": grid_um,
        "polygon_count": len(polygons),
        "label_count": len(labels),
        "off_grid_vertex_count": off_grid_vertices,
        "off_grid_label_count": off_grid_labels,
        "noncanonical_edge_count": edge_kind_counts["OTHER"],
        "edge_kind_counts": edge_kind_counts,
        "max_grid_error_um": max_grid_error_um,
        "max_orientation_error_um": max_orientation_error_um,
    }


def _expected_stitch_polygons(
    power_line_audit: Mapping[str, Any],
) -> tuple[dict[tuple[int, int], list[Any]], dict[tuple[int, int], list[tuple[list[Any], Any]]]]:
    import gdstk

    metal: dict[tuple[int, int], list[Any]] = {}
    vias: dict[tuple[int, int], list[tuple[list[Any], Any]]] = {}
    stitches = power_line_audit.get("power_line_ground_stitches")
    if not isinstance(stitches, list) or len(stitches) != 4:
        return metal, vias
    for stitch in stitches:
        if not isinstance(stitch, Mapping):
            continue
        center = _required_mapping(stitch, "center_um")
        footprint = _required_mapping(stitch, "footprint_um")
        cx = float(center["x_um"])
        cy = float(center["y_um"])
        width = float(footprint["width_um"])
        height = float(footprint["height_um"])
        footprint_polygon = gdstk.rectangle(
            (cx - 0.5 * width, cy - 0.5 * height),
            (cx + 0.5 * width, cy + 0.5 * height),
        )
        for item in stitch.get("metal_stack") or []:
            pair = (int(item["layer"]), int(item["datatype"]))
            metal.setdefault(pair, []).append(
                gdstk.rectangle(
                    (cx - 0.5 * width, cy - 0.5 * height),
                    (cx + 0.5 * width, cy + 0.5 * height),
                    layer=pair[0],
                    datatype=pair[1],
                )
            )
        for item in stitch.get("via_stack") or []:
            pair = (int(item["layer"]), int(item["datatype"]))
            array = item.get("array")
            expected: list[Any] = []
            if isinstance(array, Mapping):
                size = float(array["size_um"])
                for point in array.get("cut_centers_um") or []:
                    x_um = float(point["x_um"])
                    y_um = float(point["y_um"])
                    expected.append(
                        gdstk.rectangle(
                            (x_um - 0.5 * size, y_um - 0.5 * size),
                            (x_um + 0.5 * size, y_um + 0.5 * size),
                            layer=pair[0],
                            datatype=pair[1],
                        )
                    )
            vias.setdefault(pair, []).append((expected, footprint_polygon))
    return metal, vias


def _actual_bridge_record(
    source: Any,
    actual_by_pair: Mapping[tuple[int, int], list[Any]],
    *,
    grid_um: float,
) -> dict[str, Any]:
    import gdstk

    if not isinstance(source, Mapping):
        return {"overall_status": "FAIL", "reason": "missing source bridge record"}
    try:
        pair = (int(source["layer"]), int(source["datatype"]))
        bridge = _required_mapping(source, "bridge_probe")
        bar = _required_mapping(source, "bar_probe")
        bridge_point = (float(bridge["x_um"]), float(bridge["y_um"]))
        bar_point = (float(bar["x_um"]), float(bar["y_um"]))
    except (KeyError, TypeError, ValueError, FoundryLayoutAuditError) as exc:
        return {"overall_status": "FAIL", "reason": f"invalid source bridge record: {exc}"}
    matching = actual_by_pair.get(pair, [])
    union = gdstk.boolean(
        matching,
        [],
        "or",
        precision=max(grid_um * 0.1, 1.0e-6),
        layer=pair[0],
        datatype=pair[1],
    )
    component = next(
        (
            index
            for index, polygon in enumerate(union)
            if polygon.contain(bridge_point) and polygon.contain(bar_point)
        ),
        None,
    )
    passed = component is not None
    return {
        **dict(source),
        "overall_status": "PASS" if passed else "FAIL",
        "reason": None if passed else "actual Cadence GDS bridge and bar are disconnected",
        "matching_polygon_count": len(matching),
        "union_component_count": len(union),
        "connected_component_index": component,
        "same_connected_component_after_grid_snap": passed,
    }


def _boolean_area(
    left: Sequence[Any],
    right: Sequence[Any],
    operation: str,
    grid_um: float,
) -> float:
    import gdstk

    if not left:
        return 0.0
    result = gdstk.boolean(
        list(left),
        list(right),
        operation,
        precision=max(grid_um * 0.1, 1.0e-6),
    )
    return float(sum(float(polygon.area()) for polygon in result))


def _geometry_vector(candidate: Mapping[str, Any]) -> dict[str, float]:
    geometry: dict[str, float] = {}
    for field in GEOMETRY_FIELDS:
        key = f"geom__{field}"
        try:
            value = float(candidate[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise FoundryLayoutAuditError(f"candidate lacks numeric {key}") from exc
        if not math.isfinite(value):
            raise FoundryLayoutAuditError(f"candidate {key} is not finite")
        geometry[field] = value
    return geometry


def _validate_contract(contract: Mapping[str, Any], *, run_config: Any) -> None:
    foundry = _required_mapping(contract, "foundry_layout")
    expected = {
        "enabled": True,
        "manufacturing_grid_um": 0.005,
        "power_line_stitch_pad_depth_um": 6.0,
        "shield_strap_width_um": 10.0,
        "shield_strap_pitch_um": 20.0,
    }
    observed = run_config.emx.foundry_layout.as_dict()
    if dict(foundry) != expected or observed != expected:
        raise FoundryLayoutAuditError("foundry-layout contract values changed")
    if contract.get("audit_schema") != AUDIT_SCHEMA:
        raise FoundryLayoutAuditError("foundry-layout audit schema changed")
    if contract.get("campaign_id") != CAMPAIGN_ID:
        raise FoundryLayoutAuditError("foundry-layout contract campaign changed")
    if contract.get("audit_boundary") != (
        "POST_CADENCE_STREAMOUT_PRE_CADENCE_PASS_PARTITION"
    ):
        raise FoundryLayoutAuditError("foundry-layout audit boundary changed")
    if contract.get("top_cell") != "TRANSFORMER":
        raise FoundryLayoutAuditError("foundry-layout top-cell contract changed")
    geometry_identity = _required_mapping(contract, "geometry_identity")
    if not (
        geometry_identity.get("schema") == "ordered_10d_um_sha256_v2"
        and geometry_identity.get("decimal_places") == 9
        and tuple(geometry_identity.get("fields") or ()) == GEOMETRY_FIELDS
    ):
        raise FoundryLayoutAuditError("foundry-layout geometry identity changed")
    expected_layers = {
        "m5": {"layer": 35, "datatype": 0},
        "m9": {"layer": 39, "datatype": 60},
        "m10": {"layer": 74, "datatype": 0},
        "via5": {"layer": 55, "datatype": 0},
        "via6": {"layer": 56, "datatype": 0},
        "via7": {"layer": 57, "datatype": 40},
        "via8": {"layer": 58, "datatype": 40},
        "via9": {"layer": 85, "datatype": 0},
    }
    if _required_mapping(contract, "layer_map") != expected_layers:
        raise FoundryLayoutAuditError("foundry-layout layer map changed")


def _validate_source_audit(source: Mapping[str, Any], *, run_config: Any) -> None:
    grid = _required_mapping(source, "grid_canonicalization")
    frame = _required_mapping(source, "ground_frame")
    bridges = _required_mapping(source, "power_line_bridge_connections")
    if not (
        source.get("schema") == AUDIT_SCHEMA
        and source.get("enabled") is True
        and source.get("overall_status") == "PASS"
        and source.get("audit_boundary") == "PRE_CADENCE_LAYOUT_CONSTRUCTION"
        and _close(source.get("manufacturing_grid_um"), 0.005)
        and grid.get("schema") == GRID_SCHEMA
        and grid.get("overall_status") == "PASS"
        and frame.get("schema") == FRAME_SCHEMA
        and bridges.get("schema") == BRIDGE_SCHEMA
        and bridges.get("overall_status") == "PASS"
        and _close(
            source.get("manufacturing_grid_um"),
            run_config.emx.foundry_layout.manufacturing_grid_um,
        )
    ):
        raise FoundryLayoutAuditError("source foundry-layout audit is not PASS")


def _validate_power_line_audit_shape(audit: Mapping[str, Any]) -> None:
    stitches = audit.get("power_line_ground_stitches")
    if not (
        audit.get("schema") == "rfic_transformer_power_line_8port_geometry.v1"
        and audit.get("enabled") is True
        and isinstance(stitches, list)
        and len(stitches) == 4
        and {str(item.get("label")) for item in stitches if isinstance(item, Mapping)}
        == {"P005", "P006", "P007", "P008"}
        and all(item.get("foundry_layout_enabled") is True for item in stitches)
        and all(item.get("via_stack") for item in stitches)
    ):
        raise FoundryLayoutAuditError("power-line audit lacks foundry via/landing evidence")


def _atomic_write_audit(
    output_path: Path,
    payload: Mapping[str, Any],
    *,
    expected: Mapping[str, str],
    require_pass: bool,
) -> None:
    parent = output_path.parent
    if not parent.is_dir():
        raise FoundryLayoutAuditError(f"audit output directory is missing: {parent}")
    temporary = parent / f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        reparsed = _read_json(temporary, "temporary foundry-layout audit")
        validate_foundry_layout_audit(
            reparsed,
            expected_stage_id=expected["stage_id"],
            expected_candidate_id_sha256=expected["candidate_id_sha256"],
            expected_geometry_sha256=expected["geometry_sha256"],
            expected_config_sha256=expected["config_sha256"],
            expected_gds_sha256=expected["gds_sha256"],
            expected_contract_sha256=expected["contract_sha256"],
            require_pass=require_pass,
            verify_files=True,
        )
        if output_path.exists():
            raise FoundryLayoutAuditError(
                f"no-clobber final foundry-layout audit appeared during write: {output_path}"
            )
        os.replace(temporary, output_path)
        try:
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _file_record(path: Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _identity_record(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} is not an object")
        return None
    if not isinstance(value.get("path"), str) or not Path(str(value.get("path"))).is_absolute():
        errors.append(f"{label}.path is not absolute")
    if not isinstance(value.get("size_bytes"), int) or value.get("size_bytes", 0) <= 0:
        errors.append(f"{label}.size_bytes is invalid")
    if not SHA256_PATTERN.fullmatch(str(value.get("sha256") or "")):
        errors.append(f"{label}.sha256 is invalid")
    return value


def _regular_file(path: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise FoundryLayoutAuditError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoundryLayoutAuditError(f"{label} cannot be parsed") from exc
    if not isinstance(value, dict):
        raise FoundryLayoutAuditError(f"{label} is not an object")
    return value


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise FoundryLayoutAuditError(f"{key} is not an object")
    return result


def _mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} is not an object")
        return None
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_value(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise FoundryLayoutAuditError(f"{label} is not lowercase SHA-256")
    return digest


def _expect(errors: list[str], label: str, observed: Any, expected: Any) -> None:
    if observed != expected:
        errors.append(f"{label} mismatch")


def _expect_close(errors: list[str], label: str, observed: Any, expected: float) -> None:
    if not _close(observed, expected):
        errors.append(f"{label} mismatch")


def _close(value: Any, expected: Any, tolerance: float = 1.0e-9) -> bool:
    try:
        return abs(float(value) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False
