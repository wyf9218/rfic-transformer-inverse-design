#!/usr/bin/env python3
"""Audit one accepted-geometry checkpoint for the broadband56 200k campaign.

The long feature CSV must be ordered by ``geometry_id,frequency_hz``.  This
allows exact 11.2M-row validation without keeping every record in memory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ACQUISITION_SOURCES_BY_PHASE,
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    TARGET_ACCEPTED_GEOMETRIES,
    canonical_geometry_sha256,
    contract_fingerprint,
    matrix_columns,
    occupancy_metrics,
    phase_for_accepted_sequence,
    primary_bin_edges,
    primary_cell_for_values,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_coverage import (  # noqa: E402
    StreamingPhysicalCoverage,
    population_memberships,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    GeometryCoverageAudit,
    validate_geometry_bounds_payload,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


CHECKPOINTS = (100, 1_000, 5_000, 20_000, 50_000, 75_000, 100_000, 125_000, 150_000, 175_000, 200_000)
PILOT_TARGETS = (32, 1_000)
AUDIT_MODES = ("golden", "pilot", "checkpoint")
ACCEPTANCE_STATUS_FIELDS = (
    "analytical_status",
    "topology_status",
    "cadence_gds_status",
    "calibre_status",
    "emx_status",
    "s4p_status",
    "feature_extraction_status",
)
FEATURE_COLUMNS = (
    "lp_nh",
    "ls_nh",
    "qp",
    "qs",
    "qmin",
    "mutual_inductance_h",
    "signed_k",
    "k_abs",
    "ls_over_lp",
    "xp_ohm",
    "xs_ohm",
)
VALIDITY_COLUMNS = (
    "broadband_descriptor_valid",
    "strict_lumped_valid",
    "srf_status",
    "passivity_status",
    "reciprocity_status",
    "inside_broad_response_envelope",
    "inside_literature_practical_panel",
    "outside_envelope_reason",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    accepted_path = Path(args.accepted_geometries).expanduser().resolve()
    geometry_bounds_path = Path(args.geometry_bounds).expanduser().resolve()
    features_path = Path(args.long_features).expanduser().resolve()
    artifact_path = Path(args.artifact_index).expanduser().resolve()
    funnel_path = Path(args.failure_funnel).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    _require_new_output_directory(out_dir)
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "contract")
    for error in validate_contract(contract):
        checks.append(_check(f"contract::{error}", False, error))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract))
    target_allowed, target_detail = _audit_target_allowed(
        str(args.audit_mode), int(args.expected_accepted)
    )
    checks.append(_check("audit_mode_and_target_are_frozen", target_allowed, target_detail))
    geometry_bounds_payload = _read_json(geometry_bounds_path, checks, "geometry_bounds")
    for error in validate_geometry_bounds_payload(
        geometry_bounds_payload,
        contract_fingerprint_sha256=fingerprint,
    ):
        checks.append(_check(f"geometry_bounds::{error}", False, error))

    accepted = _audit_accepted_geometries(
        accepted_path,
        fingerprint,
        int(args.expected_accepted),
        checks,
        geometry_bounds=geometry_bounds_payload.get("field_bounds_um") or {},
    )
    artifacts = _audit_artifact_index(
        artifact_path,
        accepted["geometry_ids"],
        fingerprint,
        int(args.expected_accepted),
        checks,
        verify_files=not bool(args.skip_s4p_file_hash_check),
    )
    coverage = _audit_long_features(
        features_path,
        accepted["geometry_ids"],
        accepted["geometry_hashes"],
        accepted["geometry_phases"],
        fingerprint,
        int(args.expected_accepted),
        checks,
        require_matrix_columns=not bool(args.allow_missing_matrix_columns),
    )
    _audit_failure_funnel(funnel_path, int(args.expected_accepted), checks)

    audit_pass = bool(checks) and all(item["pass"] for item in checks)
    execution_complete = bool(
        audit_pass
        and str(args.audit_mode) == "checkpoint"
        and int(args.expected_accepted) == TARGET_ACCEPTED_GEOMETRIES
        and coverage["feature_row_count"] == TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ)
        and artifacts["artifact_count"] == TARGET_ACCEPTED_GEOMETRIES
    )
    coverage_status = "COVERAGE_AUDIT_FAIL" if not audit_pass else "COVERAGE_PARTIAL"
    gate_evidence: dict[str, Any] | None = None
    if args.coverage_gate:
        gate_evidence, coverage_status = _apply_coverage_gate(
            Path(args.coverage_gate).expanduser().resolve(), coverage["overall_metrics"], audit_pass, checks
        )
        audit_pass = all(item["pass"] for item in checks)
        if not audit_pass:
            coverage_status = "COVERAGE_AUDIT_FAIL"

    checkpoint_state = _successful_audit_state(
        str(args.audit_mode), int(args.expected_accepted), execution_complete
    )
    if not audit_pass:
        checkpoint_state = "CHECKPOINT_AUDIT_FAIL"

    cells_path = out_dir / "physical_coverage_cells_by_anchor.csv"
    _write_cell_table(cells_path, coverage["anchor_counts"], int(args.expected_accepted))
    by_frequency_path = out_dir / "physical_coverage_by_frequency.csv"
    marginals_path = out_dir / "physical_coverage_marginals.csv"
    pairwise_path = out_dir / "physical_coverage_pairwise.csv"
    _write_csv_rows(by_frequency_path, coverage["secondary_coverage"].frequency_summary_rows())
    _write_csv_rows(marginals_path, coverage["secondary_coverage"].marginal_rows())
    _write_csv_rows(pairwise_path, coverage["secondary_coverage"].pairwise_rows())
    geometry_summary_path = out_dir / "GEOMETRY_COVERAGE_SUMMARY.json"
    geometry_marginals_path = out_dir / "geometry_coverage_marginals.csv"
    geometry_pairwise_path = out_dir / "geometry_coverage_pairwise.csv"
    geometry_coverage = accepted["geometry_coverage"]
    if geometry_coverage is None:
        geometry_summary = {
            "status": "FAIL",
            "reason": "geometry coverage could not be computed from frozen bounds",
        }
        _write_csv_rows(geometry_marginals_path, [])
        _write_csv_rows(geometry_pairwise_path, [])
    else:
        geometry_summary = {"status": "PASS", **geometry_coverage.summary()}
        _write_csv_rows(geometry_marginals_path, geometry_coverage.marginal_rows())
        _write_csv_rows(geometry_pairwise_path, geometry_coverage.pairwise_rows())
    geometry_summary_path.write_text(
        json.dumps(geometry_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    coverage_summary_path = out_dir / "COVERAGE_SUMMARY.json"
    coverage_summary = {
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "expected_accepted_geometries": int(args.expected_accepted),
        "geometry_unique_anchor_coverage": coverage["overall_metrics"],
        "by_anchor_ghz": coverage["anchor_metrics"],
        "geometry_unique_anchor_coverage_by_population_phase": coverage[
            "secondary_coverage"
        ].primary_summary(),
        "feature_row_count": coverage["feature_row_count"],
        "validity_counts": coverage["validity_counts"],
        "record_weighted_secondary_coverage": coverage["secondary_coverage"].summary(),
        "geometry_space_coverage": geometry_summary,
        "coverage_status": coverage_status,
        "coverage_gate": gate_evidence,
        "scientific_boundary": "Each geometry contributes at most one cell per anchor; no surrogate prediction is counted.",
    }
    coverage_summary_path.write_text(json.dumps(coverage_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    funnel_copy = out_dir / "FAILURE_FUNNEL.csv"
    if funnel_path.is_file():
        shutil.copyfile(funnel_path, funnel_copy)
    else:
        funnel_copy.write_text("stage,count\nmissing_failure_funnel,0\n", encoding="utf-8")

    status_path = out_dir / "CHECKPOINT_STATUS.json"
    status_payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "checkpoint_status": checkpoint_state,
        "audit_mode": str(args.audit_mode),
        "coverage_status": coverage_status,
        "accepted_geometries": len(accepted["geometry_ids"]),
        "s4p_artifacts": artifacts["artifact_count"],
        "geometry_frequency_rows": coverage["feature_row_count"],
    }
    status_path.write_text(json.dumps(status_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    receipt_path = out_dir / "CHECKPOINT_RECEIPT.json"
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if audit_pass else "FAIL",
        "decision": "USE_CHECKPOINT" if audit_pass else "DO_NOT_USE_CHECKPOINT",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "expected_accepted": int(args.expected_accepted),
        "audit_mode": str(args.audit_mode),
        "checks": checks,
        "inputs": {
            "contract": _file_evidence(contract_path),
            "geometry_bounds": _file_evidence(geometry_bounds_path),
            "accepted_geometries": _file_evidence(accepted_path),
            "long_features": _file_evidence(features_path),
            "artifact_index": _file_evidence(artifact_path),
            "failure_funnel": _file_evidence(funnel_path),
        },
        "outputs": {
            "coverage_cells": _file_evidence(cells_path),
            "coverage_by_frequency": _file_evidence(by_frequency_path),
            "coverage_marginals": _file_evidence(marginals_path),
            "coverage_pairwise": _file_evidence(pairwise_path),
            "geometry_coverage_summary": _file_evidence(geometry_summary_path),
            "geometry_coverage_marginals": _file_evidence(geometry_marginals_path),
            "geometry_coverage_pairwise": _file_evidence(geometry_pairwise_path),
            "coverage_summary": _file_evidence(coverage_summary_path),
            "checkpoint_status": _file_evidence(status_path),
            "failure_funnel": _file_evidence(funnel_copy),
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)

    print(f"overall_status={receipt['overall_status']}")
    print(f"checkpoint_status={checkpoint_state}")
    print(f"coverage_status={coverage_status}")
    print(f"accepted_geometries={len(accepted['geometry_ids'])}")
    print(f"feature_rows={coverage['feature_row_count']}")
    print(f"receipt={receipt_path}")
    return 0 if audit_pass or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--accepted-geometries", required=True)
    parser.add_argument("--geometry-bounds", required=True)
    parser.add_argument("--long-features", required=True)
    parser.add_argument("--artifact-index", required=True)
    parser.add_argument("--failure-funnel", required=True)
    parser.add_argument("--audit-mode", choices=AUDIT_MODES, default="checkpoint")
    parser.add_argument("--expected-accepted", required=True, type=int)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--coverage-gate", help="Optional frozen numeric coverage-gate JSON")
    parser.add_argument("--allow-missing-matrix-columns", action="store_true")
    parser.add_argument("--skip-s4p-file-hash-check", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _audit_target_allowed(mode: str, expected_accepted: int) -> tuple[bool, str]:
    normalized = str(mode)
    target = int(expected_accepted)
    allowed_by_mode = {
        "golden": (1,),
        "pilot": PILOT_TARGETS,
        "checkpoint": CHECKPOINTS,
    }
    allowed = allowed_by_mode.get(normalized, ())
    return target in allowed, f"mode={normalized}, target={target}, allowed={list(allowed)}"


def _successful_audit_state(mode: str, expected_accepted: int, execution_complete: bool) -> str:
    if execution_complete:
        return "COMPLETE_200K"
    normalized = str(mode)
    if normalized == "golden":
        return "GOLDEN_COMPLETE"
    if normalized == "pilot":
        return f"PILOT_{int(expected_accepted)}_COMPLETE"
    return "CHECKPOINT_COMPLETE"


def _audit_accepted_geometries(
    path: Path,
    fingerprint: str,
    expected_count: int,
    checks: list[dict[str, Any]],
    *,
    geometry_bounds: dict[str, Any],
) -> dict[str, Any]:
    rows, fieldnames = _read_csv(path, checks, "accepted_geometries")
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "accepted_sequence",
        "campaign_phase",
        "acquisition_source",
        "calibre_blocking_violations",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        *ACCEPTANCE_STATUS_FIELDS,
    }
    checks.append(_check("accepted_geometries_required_columns", required.issubset(fieldnames), sorted(required - fieldnames)))
    ids: set[str] = set()
    hashes: dict[str, str] = {}
    phases: dict[str, str] = {}
    acquisition_sources: dict[str, str] = {}
    sequences: list[int] = []
    geometry_vectors: list[list[float]] = []
    ordered_hashes: list[str] = []
    row_errors: list[str] = []
    for index, row in enumerate(rows, start=2):
        geometry_id = str(row.get("geometry_id") or "").strip()
        supplied_hash = str(row.get("geometry_sha256") or "").strip().lower()
        if not geometry_id or geometry_id in ids:
            row_errors.append(f"line {index}: missing or duplicate geometry_id={geometry_id!r}")
            continue
        ids.add(geometry_id)
        if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
            row_errors.append(f"line {index}: contract fingerprint mismatch")
        sequence = _as_int(row.get("accepted_sequence"))
        phase = str(row.get("campaign_phase") or "").strip()
        source = str(row.get("acquisition_source") or "").strip()
        if sequence is None:
            row_errors.append(f"line {index}: invalid accepted_sequence")
        else:
            sequences.append(sequence)
            try:
                expected_phase = phase_for_accepted_sequence(sequence)
            except ValueError as exc:
                row_errors.append(f"line {index}: {exc}")
            else:
                if phase != expected_phase:
                    row_errors.append(
                        f"line {index}: campaign_phase={phase!r}, expected={expected_phase!r}"
                    )
                elif source not in ACQUISITION_SOURCES_BY_PHASE[phase]:
                    row_errors.append(
                        f"line {index}: acquisition_source={source!r} is not allowed for {phase}"
                    )
        phases[geometry_id] = phase
        acquisition_sources[geometry_id] = source
        values = {name: row.get(f"geom__{name}") for name in GEOMETRY_FIELDS}
        try:
            actual_hash = canonical_geometry_sha256(values)
        except Exception as exc:  # noqa: BLE001
            row_errors.append(f"line {index}: geometry identity error: {exc}")
            continue
        if supplied_hash != actual_hash:
            row_errors.append(f"line {index}: geometry hash mismatch")
        hashes[geometry_id] = actual_hash
        try:
            geometry_vectors.append([float(values[name]) for name in GEOMETRY_FIELDS])
        except (TypeError, ValueError) as exc:
            row_errors.append(f"line {index}: non-numeric geometry vector: {exc}")
        else:
            ordered_hashes.append(actual_hash)
        for field in ACCEPTANCE_STATUS_FIELDS:
            if str(row.get(field) or "").upper() != "PASS":
                row_errors.append(f"line {index}: {field} is not PASS")
        if _as_int(row.get("calibre_blocking_violations")) != 0:
            row_errors.append(f"line {index}: Calibre blocking violations are not zero")
    geometry_coverage: GeometryCoverageAudit | None = None
    try:
        geometry_coverage = GeometryCoverageAudit(
            matrix_um=np.asarray(geometry_vectors, dtype=float),
            bounds=geometry_bounds,
            geometry_hashes=ordered_hashes,
        )
    except (KeyError, TypeError, ValueError) as exc:
        row_errors.append(f"geometry coverage initialization failed: {type(exc).__name__}: {exc}")
    else:
        row_errors.extend(geometry_coverage.internal_errors())
    checks.extend(
        [
            _check("accepted_geometry_count_exact", len(rows) == expected_count, f"actual={len(rows)}, expected={expected_count}"),
            _check("accepted_geometry_ids_unique", len(ids) == len(rows), f"unique={len(ids)}, rows={len(rows)}"),
            _check(
                "accepted_sequence_exact_contiguous_order",
                sequences == list(range(1, expected_count + 1)),
                f"sequence_count={len(sequences)}, expected={expected_count}",
            ),
            _check(
                "accepted_geometry_coverage_accounting",
                geometry_coverage is not None and not geometry_coverage.internal_errors(),
                "frozen-bounds marginals, pairwise cells, boundary and nearest-neighbor evidence",
            ),
            _check("accepted_geometry_contract_and_gates", not row_errors, row_errors[:20]),
        ]
    )
    return {
        "geometry_ids": ids,
        "geometry_hashes": hashes,
        "geometry_phases": phases,
        "acquisition_sources": acquisition_sources,
        "geometry_coverage": geometry_coverage,
        "row_count": len(rows),
    }


def _audit_artifact_index(
    path: Path,
    accepted_ids: set[str],
    fingerprint: str,
    expected_count: int,
    checks: list[dict[str, Any]],
    *,
    verify_files: bool,
) -> dict[str, Any]:
    rows, fieldnames = _read_csv(path, checks, "artifact_index")
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "s4p_path",
        "s4p_sha256",
        "frequency_points",
        "emx_status",
        "calibre_status",
        "calibre_blocking_violations",
    }
    checks.append(_check("artifact_index_required_columns", required.issubset(fieldnames), sorted(required - fieldnames)))
    ids: set[str] = set()
    errors: list[str] = []
    for index, row in enumerate(rows, start=2):
        geometry_id = str(row.get("geometry_id") or "").strip()
        if not geometry_id or geometry_id in ids:
            errors.append(f"line {index}: missing or duplicate geometry_id={geometry_id!r}")
            continue
        ids.add(geometry_id)
        if geometry_id not in accepted_ids:
            errors.append(f"line {index}: artifact geometry is not accepted")
        if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
            errors.append(f"line {index}: fingerprint mismatch")
        if str(row.get("emx_status") or "").upper() != "PASS":
            errors.append(f"line {index}: EMX status is not PASS")
        if str(row.get("calibre_status") or "").upper() != "PASS" or _as_int(row.get("calibre_blocking_violations")) != 0:
            errors.append(f"line {index}: Calibre status is not zero-blocking PASS")
        if _as_int(row.get("frequency_points")) != len(FREQUENCY_GRID_HZ):
            errors.append(f"line {index}: frequency_points is not 56")
        s4p = Path(str(row.get("s4p_path") or "")).expanduser()
        if s4p.suffix.lower() != ".s4p":
            errors.append(f"line {index}: not an .s4p path")
        if verify_files:
            if not s4p.is_file() or s4p.stat().st_size <= 0:
                errors.append(f"line {index}: missing or empty S4P")
            elif _sha256(s4p) != str(row.get("s4p_sha256") or "").lower():
                errors.append(f"line {index}: S4P SHA-256 mismatch")
            else:
                try:
                    touchstone = load_touchstone(s4p)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"line {index}: S4P parse failed: {type(exc).__name__}: {exc}")
                else:
                    grid = tuple(int(round(value)) for value in touchstone.freqs_hz)
                    if int(touchstone.num_ports) != 4:
                        errors.append(f"line {index}: S4P does not contain four ports")
                    if grid != FREQUENCY_GRID_HZ:
                        errors.append(f"line {index}: S4P frequency grid is not exact broadband56")
    checks.extend(
        [
            _check("artifact_count_exact", len(rows) == expected_count, f"actual={len(rows)}, expected={expected_count}"),
            _check("artifact_geometry_set_matches_accepted", ids == accepted_ids, f"artifact_ids={len(ids)}, accepted_ids={len(accepted_ids)}"),
            _check("artifact_contract_and_files", not errors, errors[:20]),
        ]
    )
    return {"artifact_count": len(rows), "geometry_ids": ids}


def _audit_long_features(
    path: Path,
    accepted_ids: set[str],
    geometry_hashes: dict[str, str],
    geometry_phases: dict[str, str],
    fingerprint: str,
    expected_accepted: int,
    checks: list[dict[str, Any]],
    *,
    require_matrix_columns: bool,
) -> dict[str, Any]:
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "frequency_hz",
        *FEATURE_COLUMNS,
        *VALIDITY_COLUMNS,
    }
    if require_matrix_columns:
        required.update(matrix_columns())
    anchor_counts = {anchor: np.zeros(PRIMARY_CELLS_PER_ANCHOR, dtype=np.int64) for anchor in ANCHOR_FREQUENCIES_GHZ}
    secondary_coverage = StreamingPhysicalCoverage()
    validity_counts = {
        "parseable_rows": 0,
        "broadband_descriptor_valid": 0,
        "strict_lumped_valid": 0,
        "inside_broad_response_envelope": 0,
        "inside_literature_practical_panel": 0,
    }
    errors: list[str] = []
    feature_rows = 0
    completed_geometries = 0
    current_id: str | None = None
    current_frequencies: list[int] = []
    seen_ids: set[str] = set()
    fieldnames: set[str] = set()
    if not path.is_file():
        checks.append(_check("long_features_exists", False, str(path)))
        return _empty_coverage(anchor_counts, validity_counts)
    checks.append(_check("long_features_exists", True, str(path)))
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            checks.append(_check("long_features_required_columns", required.issubset(fieldnames), sorted(required - fieldnames)))
            for line, row in enumerate(reader, start=2):
                feature_rows += 1
                geometry_id = str(row.get("geometry_id") or "").strip()
                if current_id is None:
                    current_id = geometry_id
                elif geometry_id != current_id:
                    _finish_geometry(current_id, current_frequencies, accepted_ids, seen_ids, errors)
                    completed_geometries += 1
                    current_id = geometry_id
                    current_frequencies = []
                frequency_hz = _as_int(row.get("frequency_hz"))
                if frequency_hz is None:
                    errors.append(f"line {line}: invalid frequency_hz")
                    continue
                if current_frequencies and frequency_hz <= current_frequencies[-1]:
                    errors.append(f"line {line}: feature rows are not strictly ordered by frequency")
                current_frequencies.append(frequency_hz)
                if geometry_id not in accepted_ids:
                    errors.append(f"line {line}: geometry_id is not accepted")
                if str(row.get("geometry_sha256") or "").lower() != geometry_hashes.get(geometry_id):
                    errors.append(f"line {line}: geometry SHA mismatch")
                if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
                    errors.append(f"line {line}: contract fingerprint mismatch")
                numeric_names = list(FEATURE_COLUMNS)
                if require_matrix_columns:
                    numeric_names.extend(matrix_columns())
                values = {name: _as_float(row.get(name)) for name in numeric_names}
                if any(value is None for value in values.values()):
                    errors.append(f"line {line}: non-finite feature or matrix value")
                    continue
                validity_counts["parseable_rows"] += 1
                broadband_valid = _truthy(row.get("broadband_descriptor_valid"))
                strict_valid = _truthy(row.get("strict_lumped_valid"))
                broad_inside = _truthy(row.get("inside_broad_response_envelope"))
                practical_inside = _truthy(row.get("inside_literature_practical_panel"))
                validity_counts["broadband_descriptor_valid"] += int(broadband_valid)
                validity_counts["strict_lumped_valid"] += int(strict_valid)
                validity_counts["inside_broad_response_envelope"] += int(broad_inside)
                validity_counts["inside_literature_practical_panel"] += int(practical_inside)
                _validate_feature_equations(values, frequency_hz, line, errors)
                phase = geometry_phases.get(geometry_id)
                if phase is None:
                    errors.append(f"line {line}: geometry has no accepted campaign phase")
                else:
                    try:
                        secondary_coverage.add_record(
                            frequency_hz=frequency_hz,
                            values={name: float(values[name]) for name in FEATURE_COLUMNS},
                            populations=population_memberships(
                                broadband_descriptor_valid=broadband_valid,
                                strict_lumped_valid=strict_valid,
                                inside_broad_response_envelope=broad_inside,
                                inside_literature_practical_panel=practical_inside,
                            ),
                            campaign_phase=phase,
                        )
                    except (KeyError, ValueError) as exc:
                        errors.append(f"line {line}: secondary coverage accounting failed: {exc}")
                anchor_ghz = frequency_hz // 1_000_000_000
                if broadband_valid and anchor_ghz in ANCHOR_FREQUENCIES_GHZ:
                    cell = primary_cell_for_values(
                        anchor_ghz=int(anchor_ghz),
                        xp_ohm=float(values["xp_ohm"]),
                        xs_ohm=float(values["xs_ohm"]),
                        qmin=float(values["qmin"]),
                        k_abs=float(values["k_abs"]),
                    )
                    if cell is not None:
                        anchor_counts[int(anchor_ghz)][cell.local_index] += 1
            if current_id is not None:
                _finish_geometry(current_id, current_frequencies, accepted_ids, seen_ids, errors)
                completed_geometries += 1
    except Exception as exc:  # noqa: BLE001
        errors.append(f"long feature parse failed: {type(exc).__name__}: {exc}")

    expected_rows = expected_accepted * len(FREQUENCY_GRID_HZ)
    errors.extend(secondary_coverage.internal_errors())
    principal_primary = secondary_coverage.primary_counts_for("broadband_descriptor_valid")
    for anchor in ANCHOR_FREQUENCIES_GHZ:
        if not np.array_equal(anchor_counts[anchor], principal_primary[anchor]):
            errors.append(f"primary coverage accumulator mismatch at {anchor} GHz")
    checks.extend(
        [
            _check("long_feature_row_count_exact", feature_rows == expected_rows, f"actual={feature_rows}, expected={expected_rows}"),
            _check("long_feature_geometry_count_exact", completed_geometries == expected_accepted, f"actual={completed_geometries}, expected={expected_accepted}"),
            _check("long_feature_geometry_set_matches_accepted", seen_ids == accepted_ids, f"feature_ids={len(seen_ids)}, accepted_ids={len(accepted_ids)}"),
            _check("long_feature_identity_grid_and_equations", not errors, errors[:20]),
        ]
    )
    flattened = np.concatenate([anchor_counts[anchor] for anchor in ANCHOR_FREQUENCIES_GHZ])
    return {
        "feature_row_count": feature_rows,
        "validity_counts": validity_counts,
        "anchor_counts": anchor_counts,
        "anchor_metrics": {str(anchor): occupancy_metrics(anchor_counts[anchor], accepted_count=expected_accepted) for anchor in ANCHOR_FREQUENCIES_GHZ},
        "overall_metrics": occupancy_metrics(flattened, accepted_count=expected_accepted * len(ANCHOR_FREQUENCIES_GHZ)),
        "secondary_coverage": secondary_coverage,
    }


def _finish_geometry(
    geometry_id: str,
    frequencies: list[int],
    accepted_ids: set[str],
    seen_ids: set[str],
    errors: list[str],
) -> None:
    if geometry_id in seen_ids:
        errors.append(f"geometry {geometry_id!r} appears in more than one non-contiguous block")
    seen_ids.add(geometry_id)
    if geometry_id not in accepted_ids:
        errors.append(f"geometry {geometry_id!r} is not accepted")
    if tuple(frequencies) != FREQUENCY_GRID_HZ:
        errors.append(f"geometry {geometry_id!r} does not have the exact 56-point grid")


def _validate_feature_equations(values: dict[str, float | None], frequency_hz: int, line: int, errors: list[str]) -> None:
    numeric = {name: float(value) for name, value in values.items() if value is not None}
    tolerance = 1.0e-6
    if not math.isclose(numeric["qmin"], min(numeric["qp"], numeric["qs"]), rel_tol=tolerance, abs_tol=tolerance):
        errors.append(f"line {line}: qmin != min(qp,qs)")
    if not math.isclose(numeric["k_abs"], abs(numeric["signed_k"]), rel_tol=tolerance, abs_tol=tolerance):
        errors.append(f"line {line}: k_abs != abs(signed_k)")
    if numeric["lp_nh"] <= 0.0 or numeric["ls_nh"] <= 0.0:
        return
    expected_ratio = numeric["ls_nh"] / numeric["lp_nh"]
    expected_xp = 2.0 * math.pi * float(frequency_hz) * numeric["lp_nh"] * 1.0e-9
    expected_xs = 2.0 * math.pi * float(frequency_hz) * numeric["ls_nh"] * 1.0e-9
    if not math.isclose(numeric["ls_over_lp"], expected_ratio, rel_tol=tolerance, abs_tol=tolerance):
        errors.append(f"line {line}: ls_over_lp equation mismatch")
    if not math.isclose(numeric["xp_ohm"], expected_xp, rel_tol=tolerance, abs_tol=tolerance):
        errors.append(f"line {line}: xp_ohm equation mismatch")
    if not math.isclose(numeric["xs_ohm"], expected_xs, rel_tol=tolerance, abs_tol=tolerance):
        errors.append(f"line {line}: xs_ohm equation mismatch")


def _write_cell_table(path: Path, anchor_counts: dict[int, np.ndarray], accepted_count: int) -> None:
    edges = primary_bin_edges()
    target = float(accepted_count) / PRIMARY_CELLS_PER_ANCHOR
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "anchor_ghz", "cell_id", "local_cell_index", "conditioned_cell_index",
            "xp_bin", "xs_bin", "qmin_bin", "k_abs_bin",
            "xp_low", "xp_high", "xs_low", "xs_high", "qmin_low", "qmin_high", "k_abs_low", "k_abs_high",
            "actual_count", "target_count", "deficit", "cell_status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
            counts = anchor_counts[anchor]
            for xp_bin in range(6):
                for xs_bin in range(6):
                    for qmin_bin in range(6):
                        for k_bin in range(6):
                            local = (((xp_bin * 6 + xs_bin) * 6 + qmin_bin) * 6 + k_bin)
                            count = int(counts[local])
                            status = "unobserved_under_current_geometry_contract" if count == 0 else ("underfilled" if count < target else "observed")
                            writer.writerow(
                                {
                                    "anchor_ghz": anchor,
                                    "cell_id": f"f{anchor:02d}_xp{xp_bin}_xs{xs_bin}_q{qmin_bin}_k{k_bin}",
                                    "local_cell_index": local,
                                    "conditioned_cell_index": anchor_index * PRIMARY_CELLS_PER_ANCHOR + local,
                                    "xp_bin": xp_bin,
                                    "xs_bin": xs_bin,
                                    "qmin_bin": qmin_bin,
                                    "k_abs_bin": k_bin,
                                    "xp_low": edges["xp_ohm"][xp_bin],
                                    "xp_high": edges["xp_ohm"][xp_bin + 1],
                                    "xs_low": edges["xs_ohm"][xs_bin],
                                    "xs_high": edges["xs_ohm"][xs_bin + 1],
                                    "qmin_low": edges["qmin"][qmin_bin],
                                    "qmin_high": edges["qmin"][qmin_bin + 1],
                                    "k_abs_low": edges["k_abs"][k_bin],
                                    "k_abs_high": edges["k_abs"][k_bin + 1],
                                    "actual_count": count,
                                    "target_count": target,
                                    "deficit": max(target - count, 0.0),
                                    "cell_status": status,
                                }
                            )


def _audit_failure_funnel(path: Path, accepted_count: int, checks: list[dict[str, Any]]) -> None:
    rows, fieldnames = _read_csv(path, checks, "failure_funnel")
    required_stages = {
        "raw_geometry_candidates", "analytical_failures", "topology_failures", "cadence_failures",
        "calibre_failures", "emx_failures", "incomplete_frequency_failures", "s4p_parsing_failures",
        "feature_extraction_failures", "accepted_geometries",
    }
    stages = {str(row.get("stage") or "") for row in rows}
    counts = {str(row.get("stage") or ""): _as_int(row.get("count")) for row in rows}
    checks.append(_check("failure_funnel_columns", {"stage", "count"}.issubset(fieldnames), sorted({"stage", "count"} - fieldnames)))
    checks.append(_check("failure_funnel_all_stages", required_stages.issubset(stages), sorted(required_stages - stages)))
    checks.append(_check("failure_funnel_accepted_count", counts.get("accepted_geometries") == accepted_count, counts.get("accepted_geometries")))
    raw = counts.get("raw_geometry_candidates")
    checks.append(_check("failure_funnel_raw_not_less_than_accepted", raw is not None and raw >= accepted_count, raw))


def _apply_coverage_gate(
    path: Path, metrics: dict[str, Any], audit_pass: bool, checks: list[dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    gate = _read_json(path, checks, "coverage_gate")
    required = {
        "minimum_observed_cell_fraction",
        "minimum_normalized_entropy",
        "maximum_coefficient_of_variation",
        "maximum_top_1pct_cell_concentration",
        "maximum_underfilled_cells",
    }
    checks.append(_check("coverage_gate_fields", required.issubset(gate), sorted(required - set(gate))))
    comparisons = {
        "observed_cell_fraction": float(metrics.get("observed_cell_fraction") or 0.0) >= float(gate.get("minimum_observed_cell_fraction") or math.inf),
        "normalized_entropy": float(metrics.get("normalized_entropy") or 0.0) >= float(gate.get("minimum_normalized_entropy") or math.inf),
        "coefficient_of_variation": float(metrics.get("coefficient_of_variation") or math.inf) <= float(gate.get("maximum_coefficient_of_variation") or -math.inf),
        "top_1pct_cell_concentration": float(metrics.get("top_1pct_cell_concentration") or math.inf) <= float(gate.get("maximum_top_1pct_cell_concentration") or -math.inf),
        "underfilled_cells": int(metrics.get("underfilled_cells") or 0) <= int(gate.get("maximum_underfilled_cells") or -1),
    }
    for name, passed in comparisons.items():
        checks.append(_check(f"coverage_gate::{name}", passed, f"metric={metrics.get(name)}"))
    status = "COVERAGE_PASS" if audit_pass and all(comparisons.values()) else "COVERAGE_PARTIAL"
    return {"path": str(path), "sha256": _sha256(path) if path.is_file() else None, "comparisons": comparisons}, status


def _empty_coverage(anchor_counts: dict[int, np.ndarray], validity_counts: dict[str, int]) -> dict[str, Any]:
    flattened = np.concatenate([anchor_counts[anchor] for anchor in ANCHOR_FREQUENCIES_GHZ])
    return {
        "feature_row_count": 0,
        "validity_counts": validity_counts,
        "anchor_counts": anchor_counts,
        "anchor_metrics": {str(anchor): occupancy_metrics(anchor_counts[anchor], accepted_count=1) for anchor in ANCHOR_FREQUENCIES_GHZ},
        "overall_metrics": occupancy_metrics(flattened, accepted_count=8),
        "secondary_coverage": StreamingPhysicalCoverage(),
    }


def _write_csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path, checks: list[dict[str, Any]], name: str) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return [], set()
    checks.append(_check(f"{name}_exists", True, str(path)))
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
            return rows, set(reader.fieldnames or [])
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return [], set()


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return {}
    checks.append(_check(f"{name}_exists", True, str(path)))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{name}_parses", isinstance(value, dict), type(value).__name__))
    return value if isinstance(value, dict) else {}


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"no-clobber output already exists: {path}")


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": str(detail)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {"path": str(path), "exists": True, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _write_sha256s(out_dir: Path) -> None:
    index = out_dir / "SHA256SUMS.txt"
    lines = []
    for path in sorted(item for item in out_dir.iterdir() if item.is_file() and item != index):
        lines.append(f"{_sha256(path)}  {path.name}")
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
