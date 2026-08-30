#!/usr/bin/env python3
"""Audit a fresh capacity-normalized resource snapshot for broadband56 V2.

This command is audit-only.  It consumes snapshot evidence collected by a
separate read-only probe and writes a no-clobber PASS or WAIT receipt.  It never
queries MARS, licenses, processes, or simulators itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    GATE_SCHEMA,
    POLICY_APPROVAL_SCHEMA,
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
)


PREPARATION_DECISION = "PREPARED_FOR_GOLDEN_GATE"
PERMISSION_FIELDS = {
    "golden_launch_authorized": "one_golden_authorized",
    "pilot_32_launch_authorized": "pilot_32_authorized",
    "pilot_1000_launch_authorized": "pilot_1000_authorized",
    "queue_launch_authorized": "queue_authorized",
    "supervisor_launch_authorized": "supervisor_authorized",
    "phase_a_launch_authorized": "phase_a_authorized",
    "phase_b_launch_authorized": "phase_b_authorized",
    "phase_c_launch_authorized": "phase_c_authorized",
    "campaign_launch_authorized": "campaign_200k_authorized",
}


class CapacityGateError(RuntimeError):
    """Raised when control-plane evidence cannot support a gate receipt."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        receipt = audit_capacity_resource_gate(
            frozen_contract_path=Path(args.frozen_contract).expanduser().resolve(),
            preparation_receipt_path=Path(args.preparation_receipt).expanduser().resolve(),
            policy_approval_receipt_path=Path(args.policy_approval_receipt).expanduser().resolve(),
            snapshot_path=Path(args.resource_snapshot).expanduser().resolve(),
            out_dir=out_dir,
            stage=args.stage,
            current_accepted=args.current_accepted,
            measured_pilot_bytes_per_geometry=args.measured_pilot_bytes_per_geometry,
            current_concurrency=args.current_concurrency,
            healthy_check_streak=args.healthy_check_streak,
            pilot_1000_safe_concurrency=args.pilot_1000_safe_concurrency,
            max_age_seconds=args.max_age_seconds,
        )
    except CapacityGateError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"resource_policy={receipt['resource_policy']}")
    print(f"max_allowed_concurrency={receipt['max_allowed_concurrency']}")
    print(f"receipt={out_dir / 'CAPACITY_RESOURCE_GATE.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-contract", required=True)
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--policy-approval-receipt", required=True)
    parser.add_argument("--resource-snapshot", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--current-accepted", type=int, required=True)
    parser.add_argument("--measured-pilot-bytes-per-geometry", type=float)
    parser.add_argument("--current-concurrency", type=int)
    parser.add_argument("--healthy-check-streak", type=int, default=0)
    parser.add_argument("--pilot-1000-safe-concurrency", type=int)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def audit_capacity_resource_gate(
    *,
    frozen_contract_path: Path,
    preparation_receipt_path: Path,
    policy_approval_receipt_path: Path,
    snapshot_path: Path,
    out_dir: Path,
    stage: str,
    current_accepted: int,
    measured_pilot_bytes_per_geometry: float | None = None,
    current_concurrency: int | None = None,
    healthy_check_streak: int = 0,
    pilot_1000_safe_concurrency: int | None = None,
    max_age_seconds: int = 900,
    evaluated_utc: datetime | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        raise CapacityGateError(f"no-clobber output exists: {out_dir}")
    if max_age_seconds <= 0:
        raise CapacityGateError("max_age_seconds must be positive")
    contract = _read_json(frozen_contract_path, "frozen contract")
    preparation = _read_json(preparation_receipt_path, "preparation receipt")
    approval = _read_json(policy_approval_receipt_path, "policy approval receipt")
    snapshot = _read_json(snapshot_path, "capacity snapshot")
    now = evaluated_utc or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CapacityGateError("evaluated_utc must be timezone aware")

    fingerprint = str(contract.get("contract_fingerprint_sha256") or "")
    control_checks = [
        _check("contract_validation", not validate_contract(contract), validate_contract(contract)[:5]),
        _check("contract_campaign", contract.get("campaign_id") == CAMPAIGN_ID, contract.get("campaign_id")),
        _check("contract_fingerprint_exact", fingerprint == SCIENTIFIC_CONTRACT_FINGERPRINT and fingerprint == contract_fingerprint(contract), fingerprint),
        _check("contract_prepared", contract.get("preparation_status") == "PASS", contract.get("preparation_status")),
        _check("preparation_receipt_exact", _preparation_exact(preparation, fingerprint), preparation.get("decision")),
        _check("policy_approval_exact", _approval_exact(approval, fingerprint), approval.get("decision")),
        _check("policy_candidate_evidence_exact", _approved_candidate_exact(approval), approval.get("approved_candidate")),
    ]
    captured = _parse_datetime(snapshot.get("captured_utc"))
    age_seconds = (
        (now.astimezone(timezone.utc) - captured).total_seconds()
        if captured is not None
        else math.inf
    )
    control_checks.extend(
        [
            _check("snapshot_timestamp_timezone_aware", captured is not None, snapshot.get("captured_utc")),
            _check("snapshot_fresh", captured is not None and -30.0 <= age_seconds <= max_age_seconds, f"age_seconds={age_seconds}"),
        ]
    )
    if not all(item["pass"] for item in control_checks):
        failed = [item["name"] for item in control_checks if not item["pass"]]
        raise CapacityGateError("control evidence failed: " + ", ".join(failed))

    try:
        decision = evaluate_capacity_snapshot(
            snapshot,
            stage=stage,
            current_accepted=current_accepted,
            measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
        )
    except (TypeError, ValueError) as exc:
        raise CapacityGateError(str(exc)) from exc
    metrics = decision["metrics"]
    try:
        concurrency = adaptive_concurrency(
            stage=stage,
            logical_cpu_count=metrics["logical_cpu_count"],
            simulator_license_capacity=metrics["simulator_license_capacity"],
            current_concurrency=current_concurrency,
            healthy_check_streak=healthy_check_streak,
            normalized_load1=metrics["normalized_load1"],
            iowait_percent=metrics["iowait_percent"],
            available_memory_fraction=metrics["available_memory_fraction"],
            active_swap_thrashing=metrics["active_swap_thrashing"],
            licenses_available=decision["checks"]["license_gate"],
            pilot_1000_safe_concurrency=pilot_1000_safe_concurrency,
        )
    except (TypeError, ValueError) as exc:
        raise CapacityGateError(str(exc)) from exc
    status = "PASS" if decision["pass"] and concurrency["concurrency"] >= 1 else "WAIT"
    permissions = {
        gate_field: bool(status == "PASS" and approval.get(approval_field) is True)
        for gate_field, approval_field in PERMISSION_FIELDS.items()
    }
    resource_checks = [
        _check(name, passed, metrics if name.startswith("normalized") else passed)
        for name, passed in decision["checks"].items()
    ]
    valid_until = captured + timedelta(seconds=max_age_seconds) if captured else now
    receipt = {
        "schema": GATE_SCHEMA,
        "generated_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "READY_FOR_CURRENT_STAGE" if status == "PASS" else "WAITING_FOR_CAPACITY",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "resource_policy": RESOURCE_POLICY,
        "authorization_scope": "FULL_CAMPAIGN",
        "current_stage": str(stage).upper(),
        "current_accepted": int(current_accepted),
        "resources_available": status == "PASS",
        "load_gate_pass": decision["checks"]["normalized_load1_gate"] and decision["checks"]["normalized_load5_gate"],
        "memory_gate_pass": decision["checks"]["memory_gate"],
        "iowait_gate_pass": decision["checks"]["iowait_gate"],
        "swap_gate_pass": decision["checks"]["swap_sample_interval_gate"] and decision["checks"]["swap_thrash_gate"],
        "storage_gate_pass": decision["checks"]["storage_gate"],
        "license_gate_pass": decision["checks"]["license_gate"],
        "isolation_gate_pass": decision["checks"]["isolation_gate"],
        "raw_load1": metrics["raw_load1"],
        "logical_cpu_count": metrics["logical_cpu_count"],
        "normalized_load1": metrics["normalized_load1"],
        "normalized_load5": metrics["normalized_load5"],
        "normalized_load15": metrics["normalized_load15"],
        "available_memory_fraction": metrics["available_memory_fraction"],
        "iowait_percent": metrics["iowait_percent"],
        "active_swap_thrashing": metrics["active_swap_thrashing"],
        "required_storage_bytes": metrics["required_storage_bytes"],
        "filesystem_free_bytes": metrics["filesystem_free_bytes"],
        "max_allowed_concurrency": concurrency["concurrency"] if status == "PASS" else 0,
        "concurrency_hard_cap": concurrency["hard_cap"],
        "concurrency_action": concurrency["action"],
        "concurrency_reasons": concurrency["reasons"],
        **permissions,
        "simulator_geometry_limit": TARGET_ACCEPTED_GEOMETRIES if status == "PASS" else 0,
        "snapshot_age_seconds": age_seconds,
        "snapshot_captured_utc": captured.isoformat(timespec="seconds") if captured else None,
        "max_snapshot_age_seconds": max_age_seconds,
        "valid_until_utc": valid_until.isoformat(timespec="seconds"),
        "checks": control_checks + resource_checks,
        "evidence": {
            "frozen_contract": _file_evidence(frozen_contract_path),
            "preparation_receipt": _file_evidence(preparation_receipt_path),
            "operational_policy_approval_receipt": _file_evidence(policy_approval_receipt_path),
            "resource_snapshot": _file_evidence(snapshot_path),
        },
        "execution_effect": "NONE_AUDIT_ONLY",
        "authorization_boundary": (
            "PASS allows only the current ordered stage and its adaptive worker limit. "
            "WAIT launches no new simulator child. Every later stage still requires the "
            "preceding exact PASS receipt; raw load1 alone is not a blocker."
        ),
    }
    _write_no_clobber(out_dir, receipt)
    return receipt


def _preparation_exact(payload: Mapping[str, Any], fingerprint: str) -> bool:
    return (
        payload.get("overall_status") == "PASS"
        and payload.get("decision") == PREPARATION_DECISION
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("contract_fingerprint_sha256") == fingerprint
        and _checks_exact_pass(payload)
    )


def _approval_exact(payload: Mapping[str, Any], fingerprint: str) -> bool:
    return (
        payload.get("schema") == POLICY_APPROVAL_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == POLICY_APPROVAL_SCOPE
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("contract_fingerprint_sha256") == fingerprint
        and payload.get("resource_policy") == RESOURCE_POLICY
        and payload.get("authorization_scope") == "FULL_CAMPAIGN"
        and payload.get("execution_effect") == "NONE_RECORD_ONLY"
        and all(payload.get(field) is True for field in PERMISSION_FIELDS.values())
        and _checks_exact_pass(payload)
        and bool(str(payload.get("approved_by") or "").strip())
        and _parse_datetime(payload.get("approved_utc")) is not None
        and bool(str(payload.get("approval_reference") or "").strip())
    )


def _approved_candidate_exact(approval: Mapping[str, Any]) -> bool:
    item = approval.get("approved_candidate")
    if not isinstance(item, Mapping):
        return False
    path_text = str(item.get("path") or "")
    path = Path(path_text).expanduser().resolve()
    return (
        bool(path_text)
        and path.is_file()
        and item.get("size_bytes") == path.stat().st_size
        and str(item.get("sha256") or "").lower() == _sha256(path)
        and item.get("campaign_id") == CAMPAIGN_ID
        and item.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
        and item.get("resource_policy") == RESOURCE_POLICY
    )


def _write_no_clobber(out_dir: Path, receipt: Mapping[str, Any]) -> None:
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise CapacityGateError(f"staging path exists: {staging}")
    staging.mkdir(parents=True)
    try:
        path = staging / "CAPACITY_RESOURCE_GATE.json"
        _write_json(path, receipt)
        (staging / "SHA256SUMS.txt").write_text(
            f"{_sha256(path)}  {path.name}\n", encoding="utf-8"
        )
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CapacityGateError(f"failed to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CapacityGateError(f"{label} root is not an object")
    return payload


def _checks_exact_pass(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, Mapping) and item.get("pass") is True for item in checks
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _file_evidence(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
