#!/usr/bin/env python3
"""Materialize the five authoritative raw products for broadband56 V2.

The input attempt ledger is the boundary between the private production
runner and the public campaign tooling.  This script never launches Cadence,
Calibre, or EMX.  It accepts a geometry only when every frozen gate is PASS,
the evidence files and hashes exist, the S4P is a fresh four-port exact-grid
artifact, and every long-table S/Z/physical value agrees with that S4P.

Validation is completed in a staging directory.  A failed validation leaves
no official output directory behind.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.analysis.extraction import (  # noqa: E402
    single_ended_to_differential_z,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ACQUISITION_SOURCES_BY_PHASE,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    TARGET_ACCEPTED_GEOMETRIES,
    canonical_geometry_sha256,
    contract_fingerprint,
    matrix_columns,
    phase_for_accepted_sequence,
    validate_contract,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


ACCEPTANCE_STATUS_FIELDS = (
    "duplicate_status",
    "geometry_bounds_status",
    "analytical_status",
    "topology_status",
    "cadence_gds_status",
    "calibre_status",
    "emx_status",
    "s4p_status",
    "s_to_z_status",
    "feature_extraction_status",
)
FEATURE_COLUMNS = (
    "lp_h",
    "ls_h",
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
    "finite_values",
    "positive_primary_resistance",
    "positive_secondary_resistance",
    "positive_primary_inductive_reactance",
    "positive_secondary_inductive_reactance",
    "extraction_continuity_status",
    "below_half_srf",
    "broadband_descriptor_valid",
    "strict_lumped_valid",
    "srf_status",
    "passivity_status",
    "reciprocity_status",
    "inside_broad_response_envelope",
    "inside_literature_practical_panel",
    "outside_envelope_reason",
)
PATH_HASH_FIELDS = (
    ("candidate_source_path", "candidate_source_sha256"),
    ("gds_path", "gds_sha256"),
    ("calibre_report_path", "calibre_report_sha256"),
    ("emx_log_path", "emx_log_sha256"),
    ("s4p_path", "s4p_sha256"),
)
TERMINAL_STAGES = (
    "DUPLICATE_CANDIDATE",
    "GEOMETRY_BOUND_FAILURE",
    "ANALYTICAL_FAILURE",
    "TOPOLOGY_FAILURE",
    "CADENCE_FAILURE",
    "CALIBRE_FAILURE",
    "EMX_FAILURE",
    "INCOMPLETE_FREQUENCY_FAILURE",
    "S4P_PARSING_FAILURE",
    "S_TO_Z_FAILURE",
    "FEATURE_EXTRACTION_FAILURE",
    "ACCEPTED",
)
FUNNEL_STAGE_BY_TERMINAL = {
    "DUPLICATE_CANDIDATE": "duplicate_candidates",
    "GEOMETRY_BOUND_FAILURE": "geometry_bound_failures",
    "ANALYTICAL_FAILURE": "analytical_failures",
    "TOPOLOGY_FAILURE": "topology_failures",
    "CADENCE_FAILURE": "cadence_failures",
    "CALIBRE_FAILURE": "calibre_blocking_failures",
    "EMX_FAILURE": "emx_failures",
    "INCOMPLETE_FREQUENCY_FAILURE": "incomplete_frequency_failures",
    "S4P_PARSING_FAILURE": "s4p_parsing_failures",
    "S_TO_Z_FAILURE": "s_to_z_failures",
    "FEATURE_EXTRACTION_FAILURE": "feature_extraction_failures",
    "ACCEPTED": "accepted_geometries",
}
FAILURE_FUNNEL_ORDER = (
    "raw_geometry_candidates",
    "duplicate_candidates",
    "geometry_bound_failures",
    "analytical_failures",
    "topology_failures",
    "cadence_failures",
    "calibre_blocking_failures",
    "emx_failures",
    "incomplete_frequency_failures",
    "s4p_parsing_failures",
    "s_to_z_failures",
    "feature_extraction_failures",
    "accepted_geometries",
)
STATUS_PATTERN_BY_TERMINAL = {
    "DUPLICATE_CANDIDATE": ("FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "GEOMETRY_BOUND_FAILURE": ("PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "ANALYTICAL_FAILURE": ("PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "TOPOLOGY_FAILURE": ("PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "CADENCE_FAILURE": ("PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "CALIBRE_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "EMX_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "INCOMPLETE_FREQUENCY_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN"),
    "S4P_PARSING_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN"),
    "S_TO_Z_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN"),
    "FEATURE_EXTRACTION_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL"),
    "ACCEPTED": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"),
}


class RawProductFinalizationError(RuntimeError):
    """Raised when private execution evidence cannot support raw products."""


@dataclass(frozen=True)
class AcceptedAttempt:
    row: Mapping[str, str]
    geometry_id: str
    geometry_sha256: str
    accepted_sequence: int
    s4p_path: Path
    s4p_sha256: str


@dataclass(frozen=True)
class AttemptLedgerAudit:
    accepted: tuple[AcceptedAttempt, ...]
    rejected: tuple[Mapping[str, str], ...]
    funnel_counts: Mapping[str, int]
    attempt_count: int
    unique_geometry_count: int
    retry_count: int


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"output path already exists (no-clobber): {out_dir}")
    try:
        result = finalize_raw_products(
            contract_path=Path(args.contract).expanduser().resolve(),
            production_config_path=Path(args.production_config).expanduser().resolve(),
            attempt_ledger_path=Path(args.attempt_ledger).expanduser().resolve(),
            long_features_path=Path(args.long_features).expanduser().resolve(),
            out_dir=out_dir,
            expected_accepted=int(args.expected_accepted),
        )
    except RawProductFinalizationError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"accepted_geometries={result['accepted_geometries']}")
    print(f"geometry_frequency_rows={result['geometry_frequency_rows']}")
    print(f"receipt={out_dir / 'RAW_PRODUCTS_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--production-config", required=True)
    parser.add_argument("--attempt-ledger", required=True)
    parser.add_argument("--long-features", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-accepted", type=int, default=TARGET_ACCEPTED_GEOMETRIES)
    args = parser.parse_args(argv)
    if not 1 <= int(args.expected_accepted) <= TARGET_ACCEPTED_GEOMETRIES:
        parser.error(f"--expected-accepted must be in [1,{TARGET_ACCEPTED_GEOMETRIES}]")
    return args


def finalize_raw_products(
    *,
    contract_path: Path,
    production_config_path: Path,
    attempt_ledger_path: Path,
    long_features_path: Path,
    out_dir: Path,
    expected_accepted: int,
) -> dict[str, int]:
    if out_dir.exists():
        raise RawProductFinalizationError(f"output path already exists: {out_dir}")
    contract, fingerprint, production_config_sha256 = _validate_contract_and_config(
        contract_path,
        production_config_path,
    )
    attempt_audit = _audit_attempt_ledger(
        attempt_ledger_path,
        fingerprint=fingerprint,
        expected_accepted=expected_accepted,
    )
    accepted = attempt_audit.accepted
    funnel_counts = attempt_audit.funnel_counts

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise RawProductFinalizationError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        accepted_path = staging / "accepted_geometry_200k.csv"
        rejected_path = staging / "rejected_geometry_index.csv"
        artifact_path = staging / "sparameter_artifact_index_200k.csv"
        provenance_path = staging / "geometry_provenance_200k.csv"
        feature_path = staging / "broadband_features_11p2m_long.csv"
        feature_manifest_path = staging / "broadband_features_manifest.json"
        funnel_path = staging / "failure_funnel.csv"

        _write_accepted_ledger(accepted_path, accepted)
        _write_rejected_ledger(rejected_path, attempt_audit.rejected)
        _write_artifact_index(artifact_path, accepted)
        _write_geometry_provenance(
            provenance_path,
            accepted,
            production_config_path=production_config_path,
            production_config_sha256=production_config_sha256,
        )
        feature_count, feature_fields = _validate_and_write_long_features(
            source_path=long_features_path,
            destination_path=feature_path,
            accepted=accepted,
            fingerprint=fingerprint,
        )
        _write_feature_manifest(
            feature_manifest_path,
            feature_path=feature_path,
            recorded_feature_path=out_dir / feature_path.name,
            fieldnames=feature_fields,
            row_count=feature_count,
            accepted_count=len(accepted),
            fingerprint=fingerprint,
        )
        _write_failure_funnel(funnel_path, funnel_counts)

        expected_feature_rows = expected_accepted * len(FREQUENCY_GRID_HZ)
        if feature_count != expected_feature_rows:
            raise RawProductFinalizationError(
                f"long feature row count mismatch: actual={feature_count}, expected={expected_feature_rows}"
            )
        receipt_path = staging / "RAW_PRODUCTS_RECEIPT.json"
        checks = {
            "contract_static_validation_pass": True,
            "production_config_hash_matches_frozen_contract": True,
            "attempt_ledger_terminal_partition_exact": sum(funnel_counts.values())
            == attempt_audit.attempt_count,
            "accepted_count_exact": len(accepted) == expected_accepted,
            "accepted_geometry_identity_unique": len({item.geometry_sha256 for item in accepted})
            == expected_accepted,
            "accepted_sequence_exact": [item.accepted_sequence for item in accepted]
            == list(range(1, expected_accepted + 1)),
            "all_accepted_evidence_files_hash_match": True,
            "all_accepted_s4p_are_fresh_exact_56_point_four_port": True,
            "long_features_bound_to_exact_s4p_s_and_z": True,
            "long_physical_features_recomputed_from_exact_s4p": True,
            "long_feature_schema_complete": True,
            "long_validity_flags_recomputed_or_fail_closed": True,
            "failure_accounting_matches_frozen_funnel": set(funnel_counts)
            == set(FAILURE_FUNNEL_ORDER) - {"raw_geometry_candidates"},
            "proxy_values_excluded_from_labels": True,
            "simulators_not_run_by_finalizer": True,
            "output_is_no_clobber": True,
        }
        if not all(value is True for value in checks.values()):
            raise RawProductFinalizationError(f"raw-product checks failed: {checks}")
        receipt = {
            "schema": "broadband56_raw_products_receipt_v1",
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "counts": {
                "raw_attempts": attempt_audit.attempt_count,
                "unique_geometry_candidates": attempt_audit.unique_geometry_count,
                "retry_attempts": attempt_audit.retry_count,
                "accepted_geometries": len(accepted),
                "accepted_s4p_geometries": len(accepted),
                "accepted_feature_complete_geometries": len(accepted),
                "s4p_artifacts": len(accepted),
                "geometry_frequency_rows": feature_count,
                "independent_designs": len(accepted),
            },
            "failure_funnel": {
                "raw_geometry_candidates": attempt_audit.attempt_count,
                **funnel_counts,
            },
            "checks": checks,
            "numeric_binding_tolerances": {
                "relative": 1.0e-8,
                "absolute": 1.0e-10,
            },
            "inputs": {
                "contract": _file_evidence(contract_path),
                "production_config": _file_evidence(production_config_path),
                "attempt_ledger": _file_evidence(attempt_ledger_path),
                "long_features": _file_evidence(long_features_path),
            },
            "outputs": {
                "accepted_geometries": _file_evidence(
                    accepted_path, recorded_path=out_dir / accepted_path.name
                ),
                "rejected_geometries": _file_evidence(
                    rejected_path, recorded_path=out_dir / rejected_path.name
                ),
                "long_features": _file_evidence(
                    feature_path, recorded_path=out_dir / feature_path.name
                ),
                "long_features_manifest": _file_evidence(
                    feature_manifest_path,
                    recorded_path=out_dir / feature_manifest_path.name,
                ),
                "artifact_index": _file_evidence(
                    artifact_path, recorded_path=out_dir / artifact_path.name
                ),
                "geometry_provenance": _file_evidence(
                    provenance_path, recorded_path=out_dir / provenance_path.name
                ),
                "failure_funnel": _file_evidence(
                    funnel_path, recorded_path=out_dir / funnel_path.name
                ),
            },
            "scientific_boundary": (
                "Each geometry contributes one independent design and 56 correlated frequency "
                "records. Every S/Z/physical row is numerically rebound to the exact fresh-real-EMX "
                "S4P named by the accepted attempt. Proxy predictions are not accepted labels."
            ),
            "contract_snapshot": {
                "label_source": contract.get("label_source"),
                "frequency_points": len(FREQUENCY_GRID_HZ),
                "touchstone_ports": contract.get("touchstone_ports"),
            },
        }
        _write_json(receipt_path, receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "accepted_geometries": len(accepted),
        "geometry_frequency_rows": feature_count,
    }


def _validate_contract_and_config(
    contract_path: Path,
    production_config_path: Path,
) -> tuple[dict[str, Any], str, str]:
    contract = _read_json(contract_path, "contract")
    errors = validate_contract(contract)
    if errors:
        raise RawProductFinalizationError(f"contract static validation failed: {errors}")
    fingerprint = str(contract.get("contract_fingerprint_sha256") or "").lower()
    if not _is_sha256(fingerprint) or fingerprint != contract_fingerprint(contract):
        raise RawProductFinalizationError("contract fingerprint is missing or does not match contract bytes")
    if str(contract.get("preparation_status") or "").upper() != "PASS":
        raise RawProductFinalizationError("frozen contract preparation_status is not PASS")
    inherited = contract.get("inherited_contract_evidence") or {}
    expected_config_sha = str(inherited.get("production_config_sha256") or "").lower()
    if not _is_sha256(expected_config_sha):
        raise RawProductFinalizationError("frozen contract lacks production_config_sha256 evidence")
    if not production_config_path.is_file() or production_config_path.stat().st_size <= 0:
        raise RawProductFinalizationError(f"production config is missing or empty: {production_config_path}")
    actual_config_sha = _sha256(production_config_path)
    if actual_config_sha != expected_config_sha:
        raise RawProductFinalizationError(
            "production config SHA-256 does not match frozen inherited-contract evidence"
        )
    return contract, fingerprint, actual_config_sha


def _audit_attempt_ledger(
    path: Path,
    *,
    fingerprint: str,
    expected_accepted: int,
) -> AttemptLedgerAudit:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RawProductFinalizationError(f"attempt ledger is missing or empty: {path}")
    required = {
        "attempt_id",
        "retry_of_attempt_id",
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "accepted_sequence",
        "campaign_phase",
        "acquisition_source",
        "terminal_stage",
        "calibre_blocking_violations",
        "frequency_points",
        "fresh_real_emx",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        *ACCEPTANCE_STATUS_FIELDS,
        *(name for pair in PATH_HASH_FIELDS for name in pair),
    }
    accepted: list[AcceptedAttempt] = []
    rejected: list[Mapping[str, str]] = []
    attempt_count = 0
    retry_count = 0
    attempt_ids: set[str] = set()
    attempt_id_to_geometry_sha: dict[str, str] = {}
    accepted_geometry_ids: set[str] = set()
    accepted_geometry_hashes: set[str] = set()
    geometry_id_to_sha: dict[str, str] = {}
    geometry_sha_to_id: dict[str, str] = {}
    funnel = Counter({value: 0 for value in FUNNEL_STAGE_BY_TERMINAL.values()})
    hash_cache: dict[tuple[str, str], bool] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not required.issubset(fields):
            raise RawProductFinalizationError(
                f"attempt ledger lacks columns: {sorted(required - fields)}"
            )
        for line, raw in enumerate(reader, start=2):
            row = {key: str(value or "").strip() for key, value in raw.items()}
            attempt_id = row["attempt_id"]
            geometry_id = row["geometry_id"]
            if not attempt_id or attempt_id in attempt_ids:
                raise RawProductFinalizationError(
                    f"attempt ledger has missing/duplicate attempt_id at line {line}"
                )
            if not geometry_id:
                raise RawProductFinalizationError(f"attempt ledger has empty geometry_id at line {line}")
            if row["campaign_contract_fingerprint"] != fingerprint:
                raise RawProductFinalizationError(f"contract fingerprint mismatch at line {line}")
            geometry_values = {name: row[f"geom__{name}"] for name in GEOMETRY_FIELDS}
            try:
                geometry_sha = canonical_geometry_sha256(geometry_values)
            except Exception as exc:  # noqa: BLE001
                raise RawProductFinalizationError(
                    f"canonical geometry failed at line {line}: {type(exc).__name__}: {exc}"
                ) from exc
            if row["geometry_sha256"].lower() != geometry_sha:
                raise RawProductFinalizationError(f"geometry SHA-256 mismatch at line {line}")
            retry_of = row["retry_of_attempt_id"]
            if retry_of:
                if retry_of not in attempt_id_to_geometry_sha:
                    raise RawProductFinalizationError(
                        f"retry_of_attempt_id does not reference an earlier attempt at line {line}"
                    )
                if attempt_id_to_geometry_sha[retry_of] != geometry_sha:
                    raise RawProductFinalizationError(
                        f"retry_of_attempt_id references a different geometry at line {line}"
                    )
                retry_count += 1
            seen_geometry = geometry_sha in geometry_sha_to_id
            prior_sha = geometry_id_to_sha.setdefault(geometry_id, geometry_sha)
            prior_id = geometry_sha_to_id.setdefault(geometry_sha, geometry_id)
            if prior_sha != geometry_sha or prior_id != geometry_id:
                raise RawProductFinalizationError(
                    f"geometry_id/geometry_sha256 mapping is not stable at line {line}"
                )
            terminal = row["terminal_stage"].upper()
            if terminal not in TERMINAL_STAGES:
                raise RawProductFinalizationError(
                    f"unsupported terminal_stage={terminal!r} at line {line}"
                )
            if terminal == "DUPLICATE_CANDIDATE":
                if retry_of or not seen_geometry:
                    raise RawProductFinalizationError(
                        f"duplicate-candidate terminal identity is inconsistent at line {line}"
                    )
            elif seen_geometry and not retry_of:
                raise RawProductFinalizationError(
                    f"repeated canonical geometry lacks retry_of_attempt_id at line {line}"
                )
            phase = row["campaign_phase"]
            source = row["acquisition_source"]
            if phase not in ACQUISITION_SOURCES_BY_PHASE:
                raise RawProductFinalizationError(f"campaign phase is invalid at line {line}")
            if source not in ACQUISITION_SOURCES_BY_PHASE[phase]:
                raise RawProductFinalizationError(f"acquisition source is invalid at line {line}")
            statuses = tuple(row[field].upper() for field in ACCEPTANCE_STATUS_FIELDS)
            if statuses != STATUS_PATTERN_BY_TERMINAL[terminal]:
                raise RawProductFinalizationError(
                    f"status-chain/terminal-stage mismatch at line {line}: {statuses}"
                )
            blocking = _as_int(row["calibre_blocking_violations"], f"line {line} blocking")
            if blocking < 0:
                raise RawProductFinalizationError(f"negative Calibre blocking count at line {line}")
            if terminal == "CALIBRE_FAILURE" and blocking == 0:
                raise RawProductFinalizationError(
                    f"CALIBRE_FAILURE has zero blocking violations at line {line}"
                )
            if terminal not in {
                "CALIBRE_FAILURE",
                "ANALYTICAL_FAILURE",
                "TOPOLOGY_FAILURE",
                "CADENCE_FAILURE",
            } and blocking != 0:
                raise RawProductFinalizationError(
                    f"post-Calibre attempt is not zero-blocking at line {line}"
                )
            _audit_conditional_evidence(row, terminal=terminal, line=line, hash_cache=hash_cache)
            _audit_terminal_s4p(row, terminal=terminal, line=line)
            if terminal not in {
                "INCOMPLETE_FREQUENCY_FAILURE",
                "S4P_PARSING_FAILURE",
                "S_TO_Z_FAILURE",
                "FEATURE_EXTRACTION_FAILURE",
                "ACCEPTED",
            } and _truthy(row["fresh_real_emx"]):
                raise RawProductFinalizationError(
                    f"pre-S4P terminal stage is incorrectly marked fresh_real_emx at line {line}"
                )
            attempt_count += 1
            attempt_ids.add(attempt_id)
            attempt_id_to_geometry_sha[attempt_id] = geometry_sha
            funnel[FUNNEL_STAGE_BY_TERMINAL[terminal]] += 1

            if terminal != "ACCEPTED":
                if row["accepted_sequence"]:
                    raise RawProductFinalizationError(
                        f"failed attempt has accepted_sequence at line {line}"
                    )
                rejected.append(row)
                continue
            if not _truthy(row["fresh_real_emx"]):
                raise RawProductFinalizationError(f"accepted attempt is not fresh real EMX at line {line}")
            if _as_int(row["frequency_points"], f"line {line} frequency_points") != len(FREQUENCY_GRID_HZ):
                raise RawProductFinalizationError(f"accepted attempt is not exact 56-point at line {line}")
            sequence = _as_int(row["accepted_sequence"], f"line {line} accepted_sequence")
            try:
                expected_phase = phase_for_accepted_sequence(sequence)
            except ValueError as exc:
                raise RawProductFinalizationError(f"invalid accepted sequence at line {line}: {exc}") from exc
            if phase != expected_phase:
                raise RawProductFinalizationError(f"campaign phase mismatch at line {line}")
            if geometry_id in accepted_geometry_ids or geometry_sha in accepted_geometry_hashes:
                raise RawProductFinalizationError(f"duplicate accepted canonical geometry at line {line}")
            accepted_geometry_ids.add(geometry_id)
            accepted_geometry_hashes.add(geometry_sha)
            accepted.append(
                AcceptedAttempt(
                    row=row,
                    geometry_id=geometry_id,
                    geometry_sha256=geometry_sha,
                    accepted_sequence=sequence,
                    s4p_path=Path(row["s4p_path"]).expanduser().resolve(),
                    s4p_sha256=row["s4p_sha256"].lower(),
                )
            )
    accepted.sort(key=lambda item: item.accepted_sequence)
    if len(accepted) != expected_accepted:
        raise RawProductFinalizationError(
            f"accepted attempt count mismatch: actual={len(accepted)}, expected={expected_accepted}"
        )
    if [item.accepted_sequence for item in accepted] != list(range(1, expected_accepted + 1)):
        raise RawProductFinalizationError("accepted_sequence is not exact contiguous 1..N")
    if sum(funnel.values()) != attempt_count:
        raise RawProductFinalizationError("attempt ledger terminal partition does not close")
    return AttemptLedgerAudit(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        funnel_counts=dict(funnel),
        attempt_count=attempt_count,
        unique_geometry_count=len(geometry_sha_to_id),
        retry_count=retry_count,
    )


def _audit_conditional_evidence(
    row: Mapping[str, str],
    *,
    terminal: str,
    line: int,
    hash_cache: dict[tuple[str, str], bool],
) -> None:
    terminal_index = TERMINAL_STAGES.index(terminal)
    required_paths = {"candidate_source_path"}
    if terminal_index >= TERMINAL_STAGES.index("CALIBRE_FAILURE"):
        required_paths.add("gds_path")
    if terminal_index >= TERMINAL_STAGES.index("CALIBRE_FAILURE"):
        required_paths.add("calibre_report_path")
    if terminal_index >= TERMINAL_STAGES.index("EMX_FAILURE"):
        required_paths.add("emx_log_path")
    if terminal in {
        "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURE",
        "S_TO_Z_FAILURE",
        "FEATURE_EXTRACTION_FAILURE",
        "ACCEPTED",
    }:
        required_paths.add("s4p_path")
    for path_field, hash_field in PATH_HASH_FIELDS:
        path_text = str(row.get(path_field) or "").strip()
        digest = str(row.get(hash_field) or "").strip().lower()
        required = path_field in required_paths
        if not path_text and not digest and not required:
            continue
        if not path_text or not _is_sha256(digest):
            raise RawProductFinalizationError(
                f"incomplete {path_field}/{hash_field} evidence at line {line}"
            )
        evidence_path = Path(path_text).expanduser().resolve()
        cache_key = (str(evidence_path), digest)
        if cache_key not in hash_cache:
            hash_cache[cache_key] = bool(
                evidence_path.is_file()
                and evidence_path.stat().st_size > 0
                and _sha256(evidence_path) == digest
            )
        if not hash_cache[cache_key]:
            raise RawProductFinalizationError(
                f"missing/empty/hash-mismatched {path_field} evidence at line {line}"
            )
    if terminal in {
        "CALIBRE_FAILURE",
        "EMX_FAILURE",
        "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURE",
        "S_TO_Z_FAILURE",
        "FEATURE_EXTRACTION_FAILURE",
        "ACCEPTED",
    }:
        gds = Path(row["gds_path"])
        if gds.suffix.lower() != ".gds":
            raise RawProductFinalizationError(f"GDS evidence has wrong suffix at line {line}")
    if "s4p_path" in required_paths and Path(row["s4p_path"]).suffix.lower() != ".s4p":
        raise RawProductFinalizationError(f"S4P evidence has wrong suffix at line {line}")


def _audit_terminal_s4p(row: Mapping[str, str], *, terminal: str, line: int) -> None:
    if terminal not in {
        "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURE",
        "S_TO_Z_FAILURE",
        "FEATURE_EXTRACTION_FAILURE",
        "ACCEPTED",
    }:
        return
    # Accepted files are parsed exactly once while their 56-row feature block
    # is rebound below.  Their path/hash/fresh flag are still checked here by
    # the attempt-ledger evidence gate and the accepted-row checks.
    if terminal == "ACCEPTED":
        return
    path = Path(row["s4p_path"]).expanduser().resolve()
    try:
        touchstone = load_touchstone(path)
    except Exception as exc:  # noqa: BLE001
        if terminal == "S4P_PARSING_FAILURE":
            return
        raise RawProductFinalizationError(
            f"terminal stage {terminal} contradicts S4P parse failure at line {line}: {exc}"
        ) from exc
    grid = tuple(int(round(value)) for value in touchstone.freqs_hz)
    ports_exact = int(touchstone.num_ports) == 4
    exact = ports_exact and grid == FREQUENCY_GRID_HZ
    if terminal == "S4P_PARSING_FAILURE":
        if ports_exact:
            raise RawProductFinalizationError(
                f"S4P_PARSING_FAILURE artifact is parseable with four ports at line {line}"
            )
        return
    if terminal == "INCOMPLETE_FREQUENCY_FAILURE":
        if not ports_exact or exact:
            raise RawProductFinalizationError(
                f"INCOMPLETE_FREQUENCY_FAILURE artifact does not isolate a four-port grid mismatch at line {line}"
            )
        if _as_int(row["frequency_points"], f"line {line} frequency_points") != len(grid):
            raise RawProductFinalizationError(
                f"INCOMPLETE_FREQUENCY_FAILURE frequency_points mismatch at line {line}"
            )
        return
    if not exact:
        raise RawProductFinalizationError(
            f"terminal stage {terminal} lacks exact four-port broadband56 S4P at line {line}"
        )
    if not (
        np.isfinite(touchstone.s_matrix.real).all()
        and np.isfinite(touchstone.s_matrix.imag).all()
    ):
        raise RawProductFinalizationError(f"S4P contains NaN/Inf at line {line}")
    if _as_int(row["frequency_points"], f"line {line} frequency_points") != len(grid):
        raise RawProductFinalizationError(f"frequency_points disagrees with S4P at line {line}")
    if not _truthy(row["fresh_real_emx"]):
        raise RawProductFinalizationError(
            f"post-EMX S4P terminal stage is not marked fresh_real_emx at line {line}"
        )
    try:
        z_matrix = touchstone.to_z_parameters()
    except Exception as exc:  # noqa: BLE001
        if terminal == "S_TO_Z_FAILURE":
            return
        raise RawProductFinalizationError(
            f"terminal stage {terminal} contradicts S-to-Z failure at line {line}: {exc}"
        ) from exc
    z_finite = bool(
        np.isfinite(z_matrix.real).all() and np.isfinite(z_matrix.imag).all()
    )
    if terminal == "S_TO_Z_FAILURE":
        if z_finite:
            raise RawProductFinalizationError(
                f"S_TO_Z_FAILURE artifact yields finite Z parameters at line {line}"
            )
        return
    if not z_finite:
        raise RawProductFinalizationError(
            f"terminal stage {terminal} has non-finite Z parameters at line {line}"
        )


def _write_accepted_ledger(path: Path, accepted: Sequence[AcceptedAttempt]) -> None:
    fields = [
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "accepted_sequence",
        "campaign_phase",
        "acquisition_source",
        "calibre_blocking_violations",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        *ACCEPTANCE_STATUS_FIELDS,
    ]
    _write_csv(
        path,
        fields,
        ({field: item.row[field] for field in fields} for item in accepted),
    )


def _write_rejected_ledger(
    path: Path, rejected: Sequence[Mapping[str, str]]
) -> None:
    fields = [
        "attempt_id",
        "retry_of_attempt_id",
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "campaign_phase",
        "acquisition_source",
        "terminal_stage",
        "calibre_blocking_violations",
        "frequency_points",
        "fresh_real_emx",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        *ACCEPTANCE_STATUS_FIELDS,
        *(name for pair in PATH_HASH_FIELDS for name in pair),
    ]
    _write_csv(
        path,
        fields,
        ({field: row.get(field, "") for field in fields} for row in rejected),
    )


def _write_artifact_index(path: Path, accepted: Sequence[AcceptedAttempt]) -> None:
    fields = [
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "s4p_path",
        "s4p_sha256",
        "s4p_size_bytes",
        "port_count",
        "frequency_points",
        "first_frequency_hz",
        "last_frequency_hz",
        "frequency_step_hz",
        "emx_status",
        "calibre_status",
        "calibre_blocking_violations",
    ]
    def rows():
        for item in accepted:
            row = {field: item.row.get(field, "") for field in fields}
            row["s4p_path"] = str(item.s4p_path)
            row["s4p_sha256"] = item.s4p_sha256
            row["s4p_size_bytes"] = item.s4p_path.stat().st_size
            row["port_count"] = 4
            row["first_frequency_hz"] = FREQUENCY_GRID_HZ[0]
            row["last_frequency_hz"] = FREQUENCY_GRID_HZ[-1]
            row["frequency_step_hz"] = FREQUENCY_GRID_HZ[1] - FREQUENCY_GRID_HZ[0]
            yield row

    _write_csv(path, fields, rows())


def _write_geometry_provenance(
    path: Path,
    accepted: Sequence[AcceptedAttempt],
    *,
    production_config_path: Path,
    production_config_sha256: str,
) -> None:
    fields = [
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "production_config_sha256",
        "production_config_path",
        *(name for pair in PATH_HASH_FIELDS for name in pair),
        "frequency_points",
        "calibre_status",
        "calibre_blocking_violations",
        "emx_status",
        "fresh_real_emx",
    ]

    def rows():
        for item in accepted:
            row = {field: item.row.get(field, "") for field in fields}
            row["production_config_path"] = str(production_config_path)
            row["production_config_sha256"] = production_config_sha256
            for path_field, _ in PATH_HASH_FIELDS:
                row[path_field] = str(Path(str(item.row[path_field])).expanduser().resolve())
            yield row

    _write_csv(path, fields, rows())


def _validate_and_write_long_features(
    *,
    source_path: Path,
    destination_path: Path,
    accepted: Sequence[AcceptedAttempt],
    fingerprint: str,
) -> tuple[int, tuple[str, ...]]:
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise RawProductFinalizationError(f"long feature source is missing or empty: {source_path}")
    required = {
        "accepted_sequence",
        "geometry_id",
        "geometry_sha256",
        "campaign_phase",
        "acquisition_source",
        "campaign_contract_fingerprint",
        "s4p_sha256",
        "frequency_hz",
        *FEATURE_COLUMNS,
        *VALIDITY_COLUMNS,
        *matrix_columns(),
    }
    feature_count = 0
    expected_geometry_index = 0
    frequency_index = 0
    touchstone = None
    z_single = None
    z_diff = None
    with source_path.open(newline="", encoding="utf-8-sig") as source, destination_path.open(
        "w", newline="", encoding="utf-8"
    ) as destination:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        fields = set(fieldnames)
        if not required.issubset(fields):
            raise RawProductFinalizationError(
                f"long feature source lacks columns: {sorted(required - fields)}"
            )
        writer = csv.DictWriter(destination, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for line, raw in enumerate(reader, start=2):
            if expected_geometry_index >= len(accepted):
                raise RawProductFinalizationError(f"long feature source has extra rows at line {line}")
            item = accepted[expected_geometry_index]
            row = {key: str(value or "").strip() for key, value in raw.items()}
            if _as_int(row["accepted_sequence"], f"line {line} accepted_sequence") != item.accepted_sequence:
                raise RawProductFinalizationError(f"feature accepted_sequence order mismatch at line {line}")
            if (
                row["geometry_id"] != item.geometry_id
                or row["geometry_sha256"].lower() != item.geometry_sha256
                or row["campaign_phase"] != item.row["campaign_phase"]
                or row["acquisition_source"] != item.row["acquisition_source"]
                or row["campaign_contract_fingerprint"] != fingerprint
                or row["s4p_sha256"].lower() != item.s4p_sha256
            ):
                raise RawProductFinalizationError(f"feature identity/fingerprint mismatch at line {line}")
            expected_frequency = FREQUENCY_GRID_HZ[frequency_index]
            if _as_int(row["frequency_hz"], f"line {line} frequency_hz") != expected_frequency:
                raise RawProductFinalizationError(f"feature frequency order mismatch at line {line}")
            if frequency_index == 0:
                touchstone = load_touchstone(item.s4p_path)
                actual_grid = tuple(int(round(value)) for value in touchstone.freqs_hz)
                if touchstone.num_ports != 4 or actual_grid != FREQUENCY_GRID_HZ:
                    raise RawProductFinalizationError(
                        f"accepted S4P changed after attempt audit: {item.geometry_id}"
                    )
                if _sha256(item.s4p_path) != item.s4p_sha256:
                    raise RawProductFinalizationError(
                        f"accepted S4P hash changed during feature binding: {item.geometry_id}"
                    )
                try:
                    z_single = touchstone.to_z_parameters()
                except Exception as exc:  # noqa: BLE001
                    raise RawProductFinalizationError(
                        f"accepted S4P fails S-to-Z conversion: {item.geometry_id}: {exc}"
                    ) from exc
                z_diff = single_ended_to_differential_z(z_single)
                if not (
                    np.isfinite(z_single.real).all()
                    and np.isfinite(z_single.imag).all()
                    and np.isfinite(z_diff.real).all()
                    and np.isfinite(z_diff.imag).all()
                ):
                    raise RawProductFinalizationError(
                        f"accepted S4P yields non-finite Z data: {item.geometry_id}"
                    )
            assert touchstone is not None and z_single is not None and z_diff is not None
            _audit_bound_feature_row(
                row,
                s_matrix=touchstone.s_matrix[frequency_index],
                z_matrix=z_single[frequency_index],
                z_diff=z_diff[frequency_index],
                frequency_hz=expected_frequency,
                line=line,
            )
            writer.writerow(raw)
            feature_count += 1
            frequency_index += 1
            if frequency_index == len(FREQUENCY_GRID_HZ):
                frequency_index = 0
                expected_geometry_index += 1
                touchstone = None
                z_single = None
                z_diff = None
    if frequency_index != 0:
        raise RawProductFinalizationError("long feature source ends inside a geometry block")
    if expected_geometry_index != len(accepted):
        raise RawProductFinalizationError(
            f"long feature geometry count mismatch: actual={expected_geometry_index}, expected={len(accepted)}"
        )
    return feature_count, tuple(fieldnames)


def _write_feature_manifest(
    path: Path,
    *,
    feature_path: Path,
    recorded_feature_path: Path,
    fieldnames: Sequence[str],
    row_count: int,
    accepted_count: int,
    fingerprint: str,
) -> None:
    integer_columns = {"accepted_sequence", "frequency_hz"}
    floating_columns = set(FEATURE_COLUMNS) | set(matrix_columns())
    boolean_columns = {
        "finite_values",
        "positive_primary_resistance",
        "positive_secondary_resistance",
        "positive_primary_inductive_reactance",
        "positive_secondary_inductive_reactance",
        "below_half_srf",
        "broadband_descriptor_valid",
        "strict_lumped_valid",
        "inside_broad_response_envelope",
        "inside_literature_practical_panel",
    }

    def logical_type(name: str) -> str:
        if name in integer_columns:
            return "integer"
        if name in floating_columns:
            return "float64"
        if name in boolean_columns:
            return "boolean"
        return "string"

    _write_json(
        path,
        {
            "schema": "broadband56_long_feature_manifest_v1",
            "generated_utc": _utc_now(),
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "format": "csv",
            "partition_count": 1,
            "geometry_count": accepted_count,
            "frequency_points_per_geometry": len(FREQUENCY_GRID_HZ),
            "total_row_count": row_count,
            "columns": [
                {"name": name, "logical_type": logical_type(name)} for name in fieldnames
            ],
            "partitions": [
                {
                    **_file_evidence(
                        feature_path,
                        recorded_path=recorded_feature_path,
                    ),
                    "row_count": row_count,
                }
            ],
        },
    )


def _audit_bound_feature_row(
    row: Mapping[str, str],
    *,
    s_matrix: np.ndarray,
    z_matrix: np.ndarray,
    z_diff: np.ndarray,
    frequency_hz: int,
    line: int,
) -> None:
    expected_matrix: dict[str, float] = {}
    for matrix_name, matrix in (("s", s_matrix), ("z", z_matrix)):
        for row_index in range(4):
            for col_index in range(4):
                value = complex(matrix[row_index, col_index])
                expected_matrix[f"{matrix_name}{row_index + 1}{col_index + 1}_re"] = float(value.real)
                expected_matrix[f"{matrix_name}{row_index + 1}{col_index + 1}_im"] = float(value.imag)
    for name, expected in expected_matrix.items():
        actual = _as_float(row.get(name), f"line {line} {name}")
        _require_close(actual, expected, line=line, name=name)

    omega = 2.0 * math.pi * float(frequency_hz)
    z11 = complex(z_diff[0, 0])
    z22 = complex(z_diff[1, 1])
    z21 = complex(z_diff[1, 0])
    lp_h = float(z11.imag / omega)
    ls_h = float(z22.imag / omega)
    mutual_h = float(z21.imag / omega)
    inductance_product = abs(lp_h * ls_h)
    signed_k = (
        mutual_h / math.sqrt(inductance_product)
        if inductance_product > 1.0e-30
        else math.nan
    )
    qp = _derived_ratio(z11.imag, z11.real)
    qs = _derived_ratio(z22.imag, z22.real)
    qmin = min(qp, qs) if math.isfinite(qp) and math.isfinite(qs) else math.nan
    ls_over_lp = _derived_ratio(ls_h, lp_h)
    expected_features = {
        "lp_h": lp_h,
        "ls_h": ls_h,
        "lp_nh": lp_h * 1.0e9,
        "ls_nh": ls_h * 1.0e9,
        "qp": qp,
        "qs": qs,
        "qmin": qmin,
        "mutual_inductance_h": mutual_h,
        "signed_k": signed_k,
        "k_abs": abs(signed_k),
        "ls_over_lp": ls_over_lp,
        "xp_ohm": omega * lp_h,
        "xs_ohm": omega * ls_h,
    }
    actual_features = {
        name: _as_float_allow_nonfinite(row.get(name), f"line {line} {name}")
        for name in FEATURE_COLUMNS
    }
    for name, expected in expected_features.items():
        _require_close(actual_features[name], expected, line=line, name=name)

    finite_expected = bool(
        np.isfinite(s_matrix.real).all()
        and np.isfinite(s_matrix.imag).all()
        and np.isfinite(z_matrix.real).all()
        and np.isfinite(z_matrix.imag).all()
        and all(math.isfinite(value) for value in expected_features.values())
    )
    expected_flags = {
        "finite_values": finite_expected,
        "positive_primary_resistance": z11.real > 0.0,
        "positive_secondary_resistance": z22.real > 0.0,
        "positive_primary_inductive_reactance": z11.imag > 0.0,
        "positive_secondary_inductive_reactance": z22.imag > 0.0,
    }
    for name, expected in expected_flags.items():
        actual = _as_bool(row.get(name), f"line {line} {name}")
        if actual is not expected:
            raise RawProductFinalizationError(
                f"{name} contradicts S/Z values at line {line}"
            )

    broadband = _as_bool(
        row.get("broadband_descriptor_valid"), f"line {line} broadband validity"
    )
    strict = _as_bool(row.get("strict_lumped_valid"), f"line {line} strict validity")
    below_half_srf = _as_bool(row.get("below_half_srf"), f"line {line} below-half-SRF")
    broad_inside = _as_bool(
        row.get("inside_broad_response_envelope"), f"line {line} broad envelope"
    )
    practical_inside = _as_bool(
        row.get("inside_literature_practical_panel"), f"line {line} practical panel"
    )
    terminal_statuses: dict[str, str] = {}
    for label, value in (
        ("SRF", row.get("srf_status")),
        ("passivity", row.get("passivity_status")),
        ("reciprocity", row.get("reciprocity_status")),
        ("extraction continuity", row.get("extraction_continuity_status")),
    ):
        normalized = str(value or "").strip().upper()
        if normalized in {"", "NOT_RUN", "UNKNOWN", "PENDING"}:
            raise RawProductFinalizationError(
                f"{label} audit status is not terminal at line {line}"
            )
        terminal_statuses[label] = normalized
    broadband_expected = bool(
        all(expected_flags.values())
        and terminal_statuses["extraction continuity"] == "PASS"
    )
    if broadband is not broadband_expected:
        raise RawProductFinalizationError(
            f"broadband_descriptor_valid contradicts audited validity flags at line {line}"
        )
    strict_expected = broadband_expected and below_half_srf
    if strict is not strict_expected:
        raise RawProductFinalizationError(
            f"strict_lumped_valid contradicts broadband/below-half-SRF status at line {line}"
        )

    broad_expected = (
        0.03 <= expected_features["lp_nh"] <= 8.0
        and 0.03 <= expected_features["ls_nh"] <= 8.0
        and 10.0 <= expected_features["xp_ohm"] <= 250.0
        and 10.0 <= expected_features["xs_ohm"] <= 250.0
        and 2.0 <= qp <= 35.0
        and 2.0 <= qs <= 35.0
        and 0.05 <= abs(signed_k) <= 0.85
        and 0.25 <= expected_features["ls_over_lp"] <= 4.0
    )
    practical_expected = (
        0.10 <= abs(signed_k) <= 0.85
        and 0.50 <= expected_features["ls_over_lp"] <= 2.0
    )
    if broad_inside != broad_expected:
        raise RawProductFinalizationError(f"broad-envelope classification mismatch at line {line}")
    if practical_inside != practical_expected:
        raise RawProductFinalizationError(f"literature-panel classification mismatch at line {line}")
    outside_reason = str(row.get("outside_envelope_reason") or "").strip()
    if broad_inside and outside_reason:
        raise RawProductFinalizationError(
            f"inside-envelope row has outside_envelope_reason at line {line}"
        )
    if not broad_inside and not outside_reason:
        raise RawProductFinalizationError(
            f"outside-envelope row lacks outside_envelope_reason at line {line}"
        )


def _write_failure_funnel(path: Path, counts: Mapping[str, int]) -> None:
    rows = []
    raw_count = sum(int(value) for value in counts.values())
    for stage in FAILURE_FUNNEL_ORDER:
        count = raw_count if stage == "raw_geometry_candidates" else int(counts.get(stage, 0))
        rows.append({"stage": stage, "count": count})
    _write_csv(path, ["stage", "count"], rows)


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]] | Any,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RawProductFinalizationError(f"{label} is missing or empty: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RawProductFinalizationError(
            f"{label} JSON parse failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RawProductFinalizationError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_evidence(path: Path, *, recorded_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(recorded_path if recorded_path is not None else path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _as_int(value: Any, label: str) -> int:
    text = str(value).strip()
    try:
        number = int(text)
    except (TypeError, ValueError) as exc:
        raise RawProductFinalizationError(f"{label} is not an integer: {value!r}") from exc
    if str(number) != text and not (text.startswith("+") and str(number) == text[1:]):
        raise RawProductFinalizationError(f"{label} is not a canonical integer: {value!r}")
    return number


def _as_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RawProductFinalizationError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise RawProductFinalizationError(f"{label} is NaN/Inf")
    return number


def _as_float_allow_nonfinite(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RawProductFinalizationError(f"{label} is not numeric: {value!r}") from exc


def _as_bool(value: Any, label: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise RawProductFinalizationError(f"{label} is not a canonical boolean: {value!r}")


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _derived_ratio(numerator: float, denominator: float) -> float:
    if abs(float(denominator)) <= 1.0e-18:
        return math.nan
    value = float(numerator) / float(denominator)
    return value if math.isfinite(value) else math.nan


def _require_close(actual: float, expected: float, *, line: int, name: str) -> None:
    if math.isnan(expected):
        if math.isnan(actual):
            return
    elif math.isinf(expected):
        if actual == expected:
            return
    elif math.isclose(actual, expected, rel_tol=1.0e-8, abs_tol=1.0e-10):
        return
    raise RawProductFinalizationError(
        f"S4P-bound value mismatch at line {line}: {name}, actual={actual:.17g}, expected={expected:.17g}"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
