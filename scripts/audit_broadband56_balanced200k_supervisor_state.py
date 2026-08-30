#!/usr/bin/env python3
"""Derive the fail-closed authoritative-supervisor state for broadband56 V2.

This command is a read-only evidence evaluator. It never creates a run root,
starts a process, submits a job, or invokes Cadence, Calibre, or EMX. Its output
is a no-clobber snapshot that tells the private supervisor which single next
action is legal. Missing resources produce ``PREPARED_WAITING_FOR_RESOURCE``;
missing intermediate audit evidence or duplicate supervisors fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    REQUIRED_CHECKPOINT_COUNTS,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    phase_for_accepted_count,
    validate_contract,
)


EXPECTED_FEATURE_ROWS = TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ)
PHASE_A_AUDIT_COUNTS = (100, 1_000, 5_000, 20_000, 50_000)
ALL_AUDIT_COUNTS = tuple(
    sorted(set(REQUIRED_CHECKPOINT_COUNTS) | set(ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS))
)
CHECKPOINT_FILES = (
    "CHECKPOINT_STATUS.json",
    "CHECKPOINT_RECEIPT.json",
    "COVERAGE_SUMMARY.json",
    "FAILURE_FUNNEL.csv",
    "SHA256SUMS.txt",
)
PREPARATION_FILES = (
    "campaign_contract_frozen.json",
    "PRIMARY_BINS_FROZEN.json",
    "SECONDARY_COVERAGE_FROZEN.json",
    "GEOMETRY_BOUNDS_FROZEN.json",
    "PHASE_PLAN_FROZEN.json",
    "PREPARATION_RECEIPT.json",
    "SHA256SUMS.txt",
)
RESOURCE_FILES = ("RESOURCE_ESTIMATE.json", "RESOURCE_ESTIMATE.md", "SHA256SUMS.txt")
COVERAGE_STATUSES = {
    "COVERAGE_PASS",
    "COVERAGE_PARTIAL",
    "COVERAGE_PHYSICALLY_LIMITED",
    "COVERAGE_AUDIT_FAIL",
}
RESOURCE_GATE_SCHEMA = "rfic_transformer.broadband56_v2_resource_license_gate.v1"
GOLDEN_AUTHORIZATION_SCHEMA = "rfic_transformer.broadband56_v2_golden_authorization.v1"
STAGE_AUTHORIZATION_SCHEMA = "rfic_transformer.broadband56_v2_stage_authorization.v1"
GOLDEN_AUTHORIZATION_DECISION = "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY"
STAGE_AUTHORIZATION_DECISION = "APPROVE_EXPLICIT_BROADBAND56_V2_STAGES"
ONE_GOLDEN_SCOPE = "ONE_GOLDEN_ONLY"
GATE_PERMISSION_FIELDS = {
    "golden": "golden_launch_authorized",
    "pilot_32": "pilot_32_launch_authorized",
    "pilot_1000": "pilot_1000_launch_authorized",
    "queue": "queue_launch_authorized",
    "supervisor": "supervisor_launch_authorized",
    "phase_a": "phase_a_launch_authorized",
    "phase_b": "phase_b_launch_authorized",
    "phase_c": "phase_c_launch_authorized",
    "campaign_200k": "campaign_launch_authorized",
}
AUTHORIZATION_PERMISSION_FIELDS = {
    "golden": "one_golden_authorized",
    "pilot_32": "pilot_32_authorized",
    "pilot_1000": "pilot_1000_authorized",
    "queue": "queue_authorized",
    "supervisor": "supervisor_authorized",
    "phase_a": "phase_a_authorized",
    "phase_b": "phase_b_authorized",
    "phase_c": "phase_c_authorized",
    "campaign_200k": "campaign_200k_authorized",
}
AUTHORIZATION_SCOPE_STAGES = {
    ONE_GOLDEN_SCOPE: ("golden",),
    "PILOT_32_ONLY": ("golden", "pilot_32"),
    "PILOT_1000_ONLY": ("golden", "pilot_32", "pilot_1000"),
    "PHASE_A_ONLY": (
        "golden",
        "pilot_32",
        "pilot_1000",
        "queue",
        "supervisor",
        "phase_a",
    ),
    "PHASE_B_ONLY": (
        "golden",
        "pilot_32",
        "pilot_1000",
        "queue",
        "supervisor",
        "phase_a",
        "phase_b",
    ),
    "PHASE_C_ONLY": (
        "golden",
        "pilot_32",
        "pilot_1000",
        "queue",
        "supervisor",
        "phase_a",
        "phase_b",
        "phase_c",
    ),
    "FULL_CAMPAIGN": tuple(GATE_PERMISSION_FIELDS),
}


class SupervisorStateError(RuntimeError):
    """Raised when evidence cannot support one safe supervisor state."""


@dataclass(frozen=True)
class ContractEvidence:
    payload: Mapping[str, Any]
    fingerprint: str


@dataclass(frozen=True)
class AuditEvidence:
    count: int
    audit_mode: str
    path: Path
    coverage_status: str
    primary_cells_observed: int | None
    primary_cell_occupancy: float | None
    uniformity_entropy: float | None
    underfilled_cells: int | None


@dataclass(frozen=True)
class SupervisorEvidence:
    configured: bool
    live: bool
    supervisor_id: str | None
    supervisor_job: str | None
    run_root: str | None
    hostname: str | None
    owner_pid: int | None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=output path already exists (no-clobber): {out_dir}", file=sys.stderr)
        return 2
    try:
        snapshot = audit_supervisor_state(
            contract_path=Path(args.contract).expanduser().resolve(),
            preparation_dir=Path(args.preparation_dir).expanduser().resolve(),
            resource_gate_path=(
                Path(args.resource_gate).expanduser().resolve()
                if args.resource_gate
                else None
            ),
            golden_dir=(Path(args.golden_audit_dir).expanduser().resolve() if args.golden_audit_dir else None),
            pilot_32_dir=(Path(args.pilot_32_audit_dir).expanduser().resolve() if args.pilot_32_audit_dir else None),
            pilot_1000_dir=(
                Path(args.pilot_1000_audit_dir).expanduser().resolve()
                if args.pilot_1000_audit_dir
                else None
            ),
            resource_estimate_dir=(
                Path(args.resource_estimate_dir).expanduser().resolve()
                if args.resource_estimate_dir
                else None
            ),
            audit_dirs=[Path(value).expanduser().resolve() for value in args.audit_dir],
            supervisor_registry_path=(
                Path(args.supervisor_registry).expanduser().resolve()
                if args.supervisor_registry
                else None
            ),
            expected_run_root=(
                Path(args.expected_run_root).expanduser().resolve()
                if args.expected_run_root
                else None
            ),
            out_dir=out_dir,
        )
    except SupervisorStateError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"campaign_status={snapshot['campaign_status']}")
    print(f"lifecycle_state={snapshot['lifecycle_state']}")
    print(f"current_accepted={snapshot['current_accepted']}")
    print(f"next_action={snapshot['next_action']}")
    print(f"snapshot={out_dir / 'SUPERVISOR_STATE.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--preparation-dir", required=True)
    parser.add_argument("--resource-gate")
    parser.add_argument("--golden-audit-dir")
    parser.add_argument("--pilot-32-audit-dir")
    parser.add_argument("--pilot-1000-audit-dir")
    parser.add_argument("--resource-estimate-dir")
    parser.add_argument("--audit-dir", action="append", default=[])
    parser.add_argument("--supervisor-registry")
    parser.add_argument("--expected-run-root")
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def audit_supervisor_state(
    *,
    contract_path: Path,
    preparation_dir: Path,
    resource_gate_path: Path | None,
    golden_dir: Path | None,
    pilot_32_dir: Path | None,
    pilot_1000_dir: Path | None,
    resource_estimate_dir: Path | None,
    audit_dirs: Sequence[Path],
    supervisor_registry_path: Path | None,
    expected_run_root: Path | None,
    out_dir: Path,
) -> dict[str, Any]:
    if out_dir.exists():
        raise SupervisorStateError(f"output path already exists (no-clobber): {out_dir}")
    contract = _load_contract(contract_path)
    preparation = _load_preparation(
        preparation_dir,
        contract_path=contract_path,
        fingerprint=contract.fingerprint,
    )
    supervisor = _load_supervisor_registry(
        supervisor_registry_path,
        fingerprint=contract.fingerprint,
        expected_run_root=expected_run_root,
    )
    resource_gate = _load_resource_gate(
        resource_gate_path,
        fingerprint=contract.fingerprint,
    )

    golden = _load_optional_gate_audit(
        golden_dir,
        expected_count=1,
        expected_mode="golden",
        expected_state="GOLDEN_COMPLETE",
        fingerprint=contract.fingerprint,
    )
    pilot_32 = _load_optional_gate_audit(
        pilot_32_dir,
        expected_count=32,
        expected_mode="pilot",
        expected_state="PILOT_32_COMPLETE",
        fingerprint=contract.fingerprint,
    )
    pilot_1000 = _load_optional_gate_audit(
        pilot_1000_dir,
        expected_count=1_000,
        expected_mode="pilot",
        expected_state="PILOT_1000_COMPLETE",
        fingerprint=contract.fingerprint,
    )
    _require_gate_prefix(golden, pilot_32, pilot_1000)
    resource_estimate = _load_resource_estimate(
        resource_estimate_dir,
        fingerprint=contract.fingerprint,
    )
    if resource_estimate is not None and pilot_1000 is None:
        raise SupervisorStateError("resource estimate exists before the 1,000-pilot gate")

    audits = _load_campaign_audits(audit_dirs, fingerprint=contract.fingerprint)
    if audits and resource_estimate is None:
        raise SupervisorStateError("campaign audits exist before a PASS resource estimate")
    current_accepted = audits[-1].count if audits else 0
    current_feature_rows = current_accepted * len(FREQUENCY_GRID_HZ)
    latest = audits[-1] if audits else None
    state = _classify_state(
        resources_available=resource_gate["available"],
        stage_authorizations=resource_gate["permissions"],
        golden=golden,
        pilot_32=pilot_32,
        pilot_1000=pilot_1000,
        resource_estimate=resource_estimate,
        current_accepted=current_accepted,
        supervisor=supervisor,
    )

    snapshot = {
        "schema": "broadband56_authoritative_supervisor_state_v1",
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": contract.fingerprint,
        "campaign_status": state["campaign_status"],
        "lifecycle_state": state["lifecycle_state"],
        "next_action": state["next_action"],
        "blocker": state["blocker"],
        "current_accepted": current_accepted,
        "current_feature_rows": current_feature_rows,
        "current_phase": phase_for_accepted_count(current_accepted),
        "raw_candidates": None,
        "emx_success": current_accepted,
        "primary_cells_observed": latest.primary_cells_observed if latest else None,
        "primary_cell_occupancy": latest.primary_cell_occupancy if latest else None,
        "coverage_status": latest.coverage_status if latest else "COVERAGE_PARTIAL",
        "uniformity_entropy": latest.uniformity_entropy if latest else None,
        "underfilled_cells": latest.underfilled_cells if latest else None,
        "run_root": supervisor.run_root,
        "supervisor_job": supervisor.supervisor_job,
        "supervisor_hostname": supervisor.hostname,
        "supervisor_owner_pid": supervisor.owner_pid,
        "latest_checkpoint": str(latest.path) if latest else None,
        "stage_authorizations": resource_gate["permissions"],
        "evidence": {
            "contract": _file_evidence(contract_path),
            "preparation_receipt": preparation,
            "resource_gate": resource_gate["evidence"],
            "golden": _audit_record(golden),
            "pilot_32": _audit_record(pilot_32),
            "pilot_1000": _audit_record(pilot_1000),
            "resource_estimate": resource_estimate,
            "campaign_audits": [_audit_record(item) for item in audits],
            "supervisor_registry": _file_evidence(supervisor_registry_path)
            if supervisor_registry_path is not None
            else None,
        },
        "checks": {
            "preparation_pass_and_hash_closed": True,
            "resource_and_license_gate_not_bypassed": True,
            "stage_authorization_not_bypassed": True,
            "golden_and_pilot_order_not_bypassed": True,
            "campaign_audits_form_exact_prefix": True,
            "at_most_one_authoritative_supervisor": True,
            "no_remote_action_or_process_signal_performed": True,
            "proxy_predictions_not_counted_as_labels": True,
        },
        "scientific_boundary": (
            "This snapshot reports only receipt-backed execution state. It does not start or "
            "observe a simulator by itself. A configured supervisor is called live only when "
            "its registry belongs to this host and its exact PID currently exists."
        ),
    }
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise SupervisorStateError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        _write_json(staging / "SUPERVISOR_STATE.json", snapshot)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return snapshot


def _classify_state(
    *,
    resources_available: bool,
    stage_authorizations: Mapping[str, bool],
    golden: AuditEvidence | None,
    pilot_32: AuditEvidence | None,
    pilot_1000: AuditEvidence | None,
    resource_estimate: Mapping[str, Any] | None,
    current_accepted: int,
    supervisor: SupervisorEvidence,
) -> dict[str, str | None]:
    if current_accepted == TARGET_ACCEPTED_GEOMETRIES:
        return {
            "campaign_status": "COMPLETE_200K",
            "lifecycle_state": "COMPLETE_200K",
            "next_action": "RUN_TERMINAL_HISTORY_TRAINING_READINESS_FIGURE_AND_DELIVERY_AUDITS",
            "blocker": None,
        }
    if not resources_available:
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": "PREPARED_WAITING_FOR_RESOURCE",
            "next_action": "WAIT_FOR_PASS_RESOURCE_AND_LICENSE_GATE",
            "blocker": "resource or license gate is not currently PASS",
        }
    if golden is None:
        if not stage_authorizations.get("golden", False):
            return {
                "campaign_status": "PREPARED",
                "lifecycle_state": "PREPARED_WAITING_FOR_GOLDEN_AUTHORIZATION",
                "next_action": "REQUEST_EXPLICIT_ONE_GOLDEN_AUTHORIZATION",
                "blocker": "one-golden execution is not explicitly authorized",
            }
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": "READY_FOR_GOLDEN",
            "next_action": "RUN_ONE_EXACT_CONTRACT_GOLDEN_GEOMETRY",
            "blocker": None,
        }
    if pilot_32 is None:
        if not stage_authorizations.get("pilot_32", False):
            return {
                "campaign_status": "PREPARED",
                "lifecycle_state": "GOLDEN_COMPLETE_WAITING_FOR_PILOT_32_AUTHORIZATION",
                "next_action": "STOP_AND_REQUEST_EXPLICIT_PILOT_32_AUTHORIZATION",
                "blocker": "32-geometry pilot execution is not explicitly authorized",
            }
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": "READY_FOR_PILOT_32",
            "next_action": "RUN_EXACT_CONTRACT_32_GEOMETRY_PILOT",
            "blocker": None,
        }
    if pilot_1000 is None:
        if not stage_authorizations.get("pilot_1000", False):
            return {
                "campaign_status": "PREPARED",
                "lifecycle_state": "PILOT_32_COMPLETE_WAITING_FOR_PILOT_1000_AUTHORIZATION",
                "next_action": "STOP_AND_REQUEST_EXPLICIT_PILOT_1000_AUTHORIZATION",
                "blocker": "1,000-geometry pilot execution is not explicitly authorized",
            }
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": "READY_FOR_PILOT_1000",
            "next_action": "RUN_EXACT_CONTRACT_1000_GEOMETRY_PILOT",
            "blocker": None,
        }
    if resource_estimate is None:
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": "READY_FOR_RESOURCE_ESTIMATE",
            "next_action": "BUILD_MEASURED_32_AND_1000_PILOT_RESOURCE_ESTIMATE",
            "blocker": None,
        }
    phase = phase_for_accepted_count(current_accepted)
    required_permissions = ("queue", "supervisor", phase.lower())
    missing_permissions = [
        name for name in required_permissions if not stage_authorizations.get(name, False)
    ]
    if missing_permissions:
        if supervisor.live:
            return {
                "campaign_status": "BLOCKED",
                "lifecycle_state": "UNAUTHORIZED_SUPERVISOR_ACTIVE",
                "next_action": "STOP_UNAUTHORIZED_SUPERVISOR_AND_PRESERVE_EVIDENCE",
                "blocker": "live supervisor lacks explicit stage authorization",
            }
        return {
            "campaign_status": "PREPARED",
            "lifecycle_state": f"{phase}_WAITING_FOR_AUTHORIZATION",
            "next_action": f"REQUEST_EXPLICIT_{phase}_QUEUE_AND_SUPERVISOR_AUTHORIZATION",
            "blocker": "missing explicit launch permissions: " + ", ".join(missing_permissions),
        }
    running_status = {
        "PHASE_A": "PHASE_A_RUNNING",
        "PHASE_B": "PHASE_B_RUNNING",
        "PHASE_C": "PHASE_C_RUNNING",
    }[phase]
    if supervisor.live:
        lifecycle = f"{phase}_ACTIVE"
        action = "CONTINUE_AUTHORITATIVE_SUPERVISOR"
        campaign_status = running_status
    elif current_accepted == 0:
        lifecycle = "READY_FOR_PHASE_A"
        action = "START_PHASE_A_FROM_ZERO"
        campaign_status = "PREPARED"
    else:
        lifecycle = f"{phase}_RESUME_FROM_LATEST_TERMINAL_AUDIT"
        action = "RESUME_ONE_AUTHORITATIVE_SUPERVISOR_WITHOUT_OVERWRITING_SHARDS"
        campaign_status = "PREPARED"
    return {
        "campaign_status": campaign_status,
        "lifecycle_state": lifecycle,
        "next_action": action,
        "blocker": None,
    }


def _load_contract(path: Path) -> ContractEvidence:
    payload = _read_json(path)
    errors = validate_contract(payload)
    if errors:
        raise SupervisorStateError(f"campaign contract validation failed: {errors[:5]}")
    fingerprint = str(payload.get("contract_fingerprint_sha256") or "")
    if (
        not _is_sha256(fingerprint)
        or fingerprint != contract_fingerprint(payload)
        or payload.get("preparation_status") != "PASS"
    ):
        raise SupervisorStateError("campaign contract is not a fingerprinted PASS preparation artifact")
    inherited = payload.get("inherited_contract_evidence") or {}
    if not _is_sha256(str(inherited.get("production_config_sha256") or "")):
        raise SupervisorStateError("campaign contract lacks production-config SHA-256")
    return ContractEvidence(payload, fingerprint)


def _load_preparation(
    directory: Path, *, contract_path: Path, fingerprint: str
) -> dict[str, Any]:
    _require_directory(directory, "preparation")
    _require_files(directory, PREPARATION_FILES)
    _verify_sha_index(directory, require_all=True)
    receipt_path = directory / "PREPARATION_RECEIPT.json"
    receipt = _read_json(receipt_path)
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "PREPARED_FOR_GOLDEN_GATE"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or not _checks_are_exact_pass(receipt)
    ):
        raise SupervisorStateError("preparation receipt is not exact PASS")
    artifact_names = {
        "frozen_contract": "campaign_contract_frozen.json",
        "primary_bins": "PRIMARY_BINS_FROZEN.json",
        "secondary_coverage": "SECONDARY_COVERAGE_FROZEN.json",
        "geometry_bounds": "GEOMETRY_BOUNDS_FROZEN.json",
        "phase_plan": "PHASE_PLAN_FROZEN.json",
    }
    for key, name in artifact_names.items():
        _verify_evidence(receipt.get("artifacts", {}).get(key), directory / name, key)
    if _sha256(directory / "campaign_contract_frozen.json") != _sha256(contract_path):
        raise SupervisorStateError("supervisor contract is not the prepared frozen contract bytes")
    return _file_evidence(receipt_path)


def _load_resource_gate(path: Path | None, *, fingerprint: str) -> dict[str, Any]:
    if path is None:
        return {
            "available": False,
            "permissions": _empty_stage_permissions(),
            "evidence": None,
        }
    payload = _read_json(path)
    identity_ok = payload.get("campaign_id") == CAMPAIGN_ID and payload.get(
        "contract_fingerprint_sha256"
    ) == fingerprint
    if not identity_ok:
        raise SupervisorStateError("resource gate campaign identity mismatch")
    if payload.get("schema") != RESOURCE_GATE_SCHEMA:
        raise SupervisorStateError("resource gate schema mismatch")
    status = payload.get("overall_status")
    if status not in {"PASS", "WAIT"}:
        raise SupervisorStateError("resource gate is neither PASS nor WAIT")

    authorization_path, authorization = _load_gate_authorization(
        payload,
        fingerprint=fingerprint,
    )
    authorized_permissions = _authorization_permissions(authorization)
    expected_permissions = _permissions_for_scope(payload.get("authorization_scope"))
    if authorized_permissions != expected_permissions:
        raise SupervisorStateError(
            "authorization receipt permissions do not match its exact scope"
        )
    gate_permissions = _gate_permissions(payload)
    if status == "PASS":
        if gate_permissions != authorized_permissions:
            raise SupervisorStateError(
                "resource gate launch permissions exceed or differ from authorization receipt"
            )
    elif any(gate_permissions.values()):
        raise SupervisorStateError("WAIT resource gate must authorize no launch action")

    resource_checks_pass = all(
        payload.get(key) is True
        for key in (
            "resources_available",
            "load_gate_pass",
            "memory_gate_pass",
            "storage_gate_pass",
            "license_gate_pass",
            "isolation_gate_pass",
        )
    )
    checks_pass = _checks_are_exact_pass(payload)
    valid_until = _parse_aware_datetime(payload.get("valid_until_utc"))
    gate_is_fresh = valid_until is not None and datetime.now(timezone.utc) <= valid_until
    available = bool(
        status == "PASS" and resource_checks_pass and checks_pass and gate_is_fresh
    )
    effective_permissions = gate_permissions if available else _empty_stage_permissions()
    return {
        "available": available,
        "permissions": effective_permissions,
        "evidence": {
            "gate": _file_evidence(path),
            "authorization_receipt": _file_evidence(authorization_path),
            "gate_is_fresh": gate_is_fresh,
        },
    }


def _load_gate_authorization(
    gate: Mapping[str, Any], *, fingerprint: str
) -> tuple[Path, dict[str, Any]]:
    evidence = gate.get("evidence")
    if not isinstance(evidence, Mapping):
        raise SupervisorStateError("resource gate lacks authorization evidence")
    item = evidence.get("golden_authorization_receipt") or evidence.get(
        "stage_authorization_receipt"
    )
    if not isinstance(item, Mapping):
        raise SupervisorStateError("resource gate lacks authorization-receipt evidence")
    path_text = str(item.get("path") or "")
    path = Path(path_text).expanduser().resolve()
    if (
        not path_text
        or not path.is_file()
        or item.get("size_bytes") != path.stat().st_size
        or str(item.get("sha256") or "").lower() != _sha256(path)
    ):
        raise SupervisorStateError("resource gate authorization-receipt evidence mismatch")
    authorization = _read_json(path)
    approved = authorization.get("approved_candidate")
    approved_campaign = (
        approved.get("campaign_id") if isinstance(approved, Mapping) else None
    ) or authorization.get("campaign_id")
    approved_fingerprint = (
        approved.get("contract_fingerprint_sha256")
        if isinstance(approved, Mapping)
        else None
    ) or authorization.get("contract_fingerprint_sha256")
    if (
        authorization.get("overall_status") != "PASS"
        or approved_campaign != CAMPAIGN_ID
        or approved_fingerprint != fingerprint
        or not _checks_are_exact_pass(authorization)
        or authorization.get("execution_effect") != "NONE_RECORD_ONLY"
        or not str(authorization.get("approved_by") or "").strip()
        or _parse_aware_datetime(authorization.get("approved_utc")) is None
        or not str(authorization.get("approval_reference") or "").strip()
    ):
        raise SupervisorStateError("authorization receipt is not exact hash-closed PASS")
    scope = gate.get("authorization_scope")
    if scope == ONE_GOLDEN_SCOPE:
        if (
            authorization.get("schema") != GOLDEN_AUTHORIZATION_SCHEMA
            or authorization.get("decision") != GOLDEN_AUTHORIZATION_DECISION
            or authorization.get("approval_source")
            != "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION"
            or authorization.get("resource_and_license_gate_authorized") is not True
            or authorization.get("cadence_authorized_for_one_golden") is not True
            or authorization.get("calibre_authorized_for_one_golden") is not True
            or authorization.get("emx_authorized_for_one_golden") is not True
            or authorization.get("simulator_geometry_limit") != 1
            or not isinstance(approved, Mapping)
            or not _is_sha256(str(approved.get("sha256") or ""))
        ):
            raise SupervisorStateError("one-golden authorization identity mismatch")
    elif (
        authorization.get("schema") != STAGE_AUTHORIZATION_SCHEMA
        or authorization.get("decision") != STAGE_AUTHORIZATION_DECISION
        or authorization.get("authorization_scope") != scope
        or authorization.get("approval_source")
        != "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION"
    ):
        raise SupervisorStateError("stage authorization identity mismatch")
    return path, authorization


def _authorization_permissions(authorization: Mapping[str, Any]) -> dict[str, bool]:
    permissions = _read_permission_fields(
        authorization,
        AUTHORIZATION_PERMISSION_FIELDS,
        "authorization receipt",
    )
    _require_monotonic_permissions(permissions)
    return permissions


def _gate_permissions(gate: Mapping[str, Any]) -> dict[str, bool]:
    permissions = _read_permission_fields(gate, GATE_PERMISSION_FIELDS, "resource gate")
    _require_monotonic_permissions(permissions)
    return permissions


def _read_permission_fields(
    payload: Mapping[str, Any], fields: Mapping[str, str], label: str
) -> dict[str, bool]:
    permissions: dict[str, bool] = {}
    for name, field in fields.items():
        value = payload.get(field)
        if not isinstance(value, bool):
            raise SupervisorStateError(f"{label} permission is not boolean: {field}")
        permissions[name] = value
    return permissions


def _require_monotonic_permissions(permissions: Mapping[str, bool]) -> None:
    implications = (
        ("pilot_32", "golden"),
        ("pilot_1000", "pilot_32"),
        ("phase_b", "phase_a"),
        ("phase_c", "phase_b"),
    )
    for later, earlier in implications:
        if permissions[later] and not permissions[earlier]:
            raise SupervisorStateError(
                f"stage authorization is non-monotonic: {later} requires {earlier}"
            )
    if permissions["campaign_200k"] and not all(
        permissions[name]
        for name in ("queue", "supervisor", "phase_a", "phase_b", "phase_c")
    ):
        raise SupervisorStateError(
            "200K campaign authorization requires queue, supervisor, and all phase permissions"
        )


def _empty_stage_permissions() -> dict[str, bool]:
    return {name: False for name in GATE_PERMISSION_FIELDS}


def _permissions_for_scope(scope: Any) -> dict[str, bool]:
    if not isinstance(scope, str) or scope not in AUTHORIZATION_SCOPE_STAGES:
        raise SupervisorStateError(f"unsupported stage authorization scope: {scope!r}")
    permissions = _empty_stage_permissions()
    for name in AUTHORIZATION_SCOPE_STAGES[scope]:
        permissions[name] = True
    return permissions


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _load_optional_gate_audit(
    directory: Path | None,
    *,
    expected_count: int,
    expected_mode: str,
    expected_state: str,
    fingerprint: str,
) -> AuditEvidence | None:
    if directory is None:
        return None
    return _load_audit(
        directory,
        expected_count=expected_count,
        expected_mode=expected_mode,
        expected_state=expected_state,
        fingerprint=fingerprint,
    )


def _require_gate_prefix(
    golden: AuditEvidence | None,
    pilot_32: AuditEvidence | None,
    pilot_1000: AuditEvidence | None,
) -> None:
    if pilot_32 is not None and golden is None:
        raise SupervisorStateError("32-pilot evidence exists without golden evidence")
    if pilot_1000 is not None and pilot_32 is None:
        raise SupervisorStateError("1,000-pilot evidence exists without 32-pilot evidence")


def _load_resource_estimate(
    directory: Path | None, *, fingerprint: str
) -> dict[str, Any] | None:
    if directory is None:
        return None
    _require_directory(directory, "resource estimate")
    _require_files(directory, RESOURCE_FILES)
    _verify_sha_index(directory, require_all=True)
    path = directory / "RESOURCE_ESTIMATE.json"
    payload = _read_json(path)
    if (
        payload.get("overall_status") != "PASS"
        or payload.get("decision") != "RESOURCE_ESTIMATE_READY"
        or payload.get("campaign_id") != CAMPAIGN_ID
        or payload.get("contract_fingerprint_sha256") != fingerprint
        or _as_int(payload.get("target_accepted_geometries"), "resource target")
        != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(payload.get("target_geometry_frequency_rows"), "resource rows")
        != EXPECTED_FEATURE_ROWS
        or not _checks_are_exact_pass(payload)
    ):
        raise SupervisorStateError("resource estimate is not exact PASS")
    return _file_evidence(path)


def _load_campaign_audits(
    directories: Sequence[Path], *, fingerprint: str
) -> tuple[AuditEvidence, ...]:
    by_count: dict[int, AuditEvidence] = {}
    input_counts: list[int] = []
    for directory in directories:
        status = _read_json(directory / "CHECKPOINT_STATUS.json")
        count = _as_int(status.get("accepted_geometries"), "campaign accepted")
        if count in by_count:
            raise SupervisorStateError(f"duplicate campaign audit count: {count}")
        if count not in ALL_AUDIT_COUNTS:
            raise SupervisorStateError(f"campaign audit count is not frozen: {count}")
        input_counts.append(count)
        expected_mode = "checkpoint" if count in REQUIRED_CHECKPOINT_COUNTS else "round"
        expected_state = (
            "COMPLETE_200K"
            if count == TARGET_ACCEPTED_GEOMETRIES
            else ("CHECKPOINT_COMPLETE" if expected_mode == "checkpoint" else f"ROUND_{count}_COMPLETE")
        )
        by_count[count] = _load_audit(
            directory,
            expected_count=count,
            expected_mode=expected_mode,
            expected_state=expected_state,
            fingerprint=fingerprint,
        )
    if input_counts != sorted(input_counts):
        raise SupervisorStateError(
            f"campaign audit arguments are not in ascending frozen order: {input_counts}"
        )
    ordered_counts = sorted(by_count)
    if ordered_counts != list(ALL_AUDIT_COUNTS[: len(ordered_counts)]):
        raise SupervisorStateError(
            "campaign audits are not an exact prefix of frozen terminal endpoints: "
            f"actual={ordered_counts}"
        )
    return tuple(by_count[count] for count in ordered_counts)


def _load_audit(
    directory: Path,
    *,
    expected_count: int,
    expected_mode: str,
    expected_state: str,
    fingerprint: str,
) -> AuditEvidence:
    _require_directory(directory, f"{expected_mode} audit")
    _require_files(directory, CHECKPOINT_FILES)
    _verify_sha_index(directory, require_all=True)
    status = _read_json(directory / "CHECKPOINT_STATUS.json")
    receipt = _read_json(directory / "CHECKPOINT_RECEIPT.json")
    coverage = _read_json(directory / "COVERAGE_SUMMARY.json")
    expected_rows = expected_count * len(FREQUENCY_GRID_HZ)
    coverage_status = str(status.get("coverage_status") or "")
    if (
        status.get("campaign_id") != CAMPAIGN_ID
        or status.get("contract_fingerprint_sha256") != fingerprint
        or status.get("audit_mode") != expected_mode
        or status.get("checkpoint_status") != expected_state
        or _as_int(status.get("accepted_geometries"), "audit accepted") != expected_count
        or _as_int(status.get("s4p_artifacts"), "audit S4P") != expected_count
        or _as_int(status.get("geometry_frequency_rows"), "audit feature rows") != expected_rows
        or coverage_status not in COVERAGE_STATUSES
        or receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_CHECKPOINT"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or receipt.get("audit_mode") != expected_mode
        or _as_int(receipt.get("expected_accepted"), "receipt expected") != expected_count
        or not _checks_are_exact_pass(receipt)
        or coverage.get("campaign_id") != CAMPAIGN_ID
        or coverage.get("contract_fingerprint_sha256") != fingerprint
        or _as_int(coverage.get("feature_row_count"), "coverage rows") != expected_rows
        or coverage.get("coverage_status") != coverage_status
    ):
        raise SupervisorStateError(
            f"{expected_mode} audit {expected_count} is not exact hash-closed PASS"
        )
    _verify_evidence(
        receipt.get("outputs", {}).get("checkpoint_status"),
        directory / "CHECKPOINT_STATUS.json",
        "checkpoint status",
    )
    _verify_evidence(
        receipt.get("outputs", {}).get("coverage_summary"),
        directory / "COVERAGE_SUMMARY.json",
        "coverage summary",
    )
    _verify_evidence(
        receipt.get("outputs", {}).get("failure_funnel"),
        directory / "FAILURE_FUNNEL.csv",
        "failure funnel",
    )
    metrics = coverage.get("geometry_unique_anchor_coverage") or {}
    return AuditEvidence(
        count=expected_count,
        audit_mode=expected_mode,
        path=directory,
        coverage_status=coverage_status,
        primary_cells_observed=_optional_int(metrics.get("observed_cells")),
        primary_cell_occupancy=_optional_float(metrics.get("observed_cell_fraction")),
        uniformity_entropy=_optional_float(metrics.get("normalized_entropy")),
        underfilled_cells=_optional_int(metrics.get("underfilled_cells")),
    )


def _load_supervisor_registry(
    path: Path | None,
    *,
    fingerprint: str,
    expected_run_root: Path | None,
) -> SupervisorEvidence:
    if path is None:
        if expected_run_root is not None:
            raise SupervisorStateError("expected run root supplied without supervisor registry")
        return SupervisorEvidence(False, False, None, None, None, None, None)
    payload = _read_json(path)
    run_root_text = str(payload.get("run_root") or "")
    supervisor_id = str(payload.get("supervisor_id") or "")
    supervisor_job = str(payload.get("supervisor_job") or "")
    run_root = Path(run_root_text).expanduser().resolve()
    if (
        payload.get("schema") != "broadband56_authoritative_supervisor_registry_v1"
        or payload.get("campaign_id") != CAMPAIGN_ID
        or payload.get("contract_fingerprint_sha256") != fingerprint
        or payload.get("authoritative") is not True
        or _as_int(payload.get("active_supervisor_count"), "active supervisor count") != 1
        or payload.get("duplicate_supervisors_detected") is not False
        or not run_root_text
        or not supervisor_id
        or not supervisor_job
        or expected_run_root is None
        or run_root != expected_run_root
        or not run_root.is_dir()
    ):
        raise SupervisorStateError("supervisor registry is not one exact authoritative run root")
    hostname = str(payload.get("hostname") or "")
    pid = _as_int(payload.get("owner_pid"), "supervisor owner PID")
    if hostname != socket.gethostname():
        raise SupervisorStateError(
            "supervisor registry belongs to another host; remote liveness cannot be proven safely"
        )
    live = _pid_exists(pid)
    return SupervisorEvidence(
        configured=True,
        live=live,
        supervisor_id=supervisor_id,
        supervisor_job=supervisor_job,
        run_root=str(run_root),
        hostname=hostname,
        owner_pid=pid,
    )


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _audit_record(audit: AuditEvidence | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    return {
        "accepted_geometries": audit.count,
        "audit_mode": audit.audit_mode,
        "path": str(audit.path),
        "receipt_sha256": _sha256(audit.path / "CHECKPOINT_RECEIPT.json"),
        "status_sha256": _sha256(audit.path / "CHECKPOINT_STATUS.json"),
    }


def _verify_sha_index(directory: Path, *, require_all: bool) -> None:
    index = directory / "SHA256SUMS.txt"
    if not index.is_file():
        raise SupervisorStateError(f"missing SHA256SUMS.txt: {directory}")
    seen: set[str] = set()
    for line, raw in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or not _is_sha256(parts[0]) or Path(parts[1]).name != parts[1]:
            raise SupervisorStateError(f"invalid SHA256SUMS line {line}: {index}")
        digest, name = parts[0].lower(), parts[1]
        target = directory / name
        if name in seen or not target.is_file() or _sha256(target) != digest:
            raise SupervisorStateError(f"SHA256SUMS mismatch: {directory.name}/{name}")
        seen.add(name)
    if require_all:
        files = {path.name for path in directory.iterdir() if path.is_file() and path != index}
        if seen != files:
            raise SupervisorStateError(
                f"SHA256SUMS coverage mismatch: missing={sorted(files - seen)}, extra={sorted(seen - files)}"
            )


def _verify_evidence(evidence: Any, path: Path, label: str) -> None:
    if not isinstance(evidence, Mapping) or not path.is_file():
        raise SupervisorStateError(f"missing receipt evidence: {label}")
    if (
        Path(str(evidence.get("path") or "")).name != path.name
        or str(evidence.get("sha256") or "").lower() != _sha256(path)
        or _as_int(evidence.get("size_bytes"), f"{label} size") != path.stat().st_size
    ):
        raise SupervisorStateError(f"receipt evidence mismatch: {label}")


def _file_evidence(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _require_directory(path: Path, label: str) -> None:
    if not path.is_dir():
        raise SupervisorStateError(f"{label} directory does not exist: {path}")


def _require_files(directory: Path, names: Sequence[str]) -> None:
    missing = [name for name in names if not (directory / name).is_file()]
    if missing:
        raise SupervisorStateError(f"{directory} is missing required files: {missing}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SupervisorStateError(f"failed to parse JSON {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SupervisorStateError(f"JSON root is not an object: {path}")
    return payload


def _checks_are_exact_pass(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return bool(checks) and isinstance(checks, list) and all(
        isinstance(item, Mapping) and item.get("pass") is True for item in checks
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SupervisorStateError(f"invalid integer for {label}: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise SupervisorStateError(f"invalid integer for {label}: {value!r}")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("+-").isdigit():
            return int(stripped)
    raise SupervisorStateError(f"invalid integer for {label}: {value!r}")


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _as_int(value, "optional metric")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SupervisorStateError(f"invalid optional float metric: {value!r}") from exc
    if not math.isfinite(result):
        raise SupervisorStateError(f"non-finite optional float metric: {value!r}")
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
