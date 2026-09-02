#!/usr/bin/env python3
"""Audit a Broadband56 resource snapshot under the owner swap override."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import audit_broadband56_v2_capacity_resource_gate as legacy  # noqa: E402

from rfic_transformer_inverse_design.campaigns import (  # noqa: E402
    broadband56_swap_override_policy as swap_policy_module,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
)
from rfic_transformer_inverse_design.campaigns.broadband56_swap_override_policy import (  # noqa: E402
    GATE_SCHEMA,
    OVERRIDE_DECISION,
    OVERRIDE_RECEIPT_SCHEMA,
    SWAP_POLICY,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
)


OVERLAY_SCHEMA = "rfic_transformer.broadband56_v2_operational_policy_overlay.v1"
QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
OVERRIDE_PATH_ENV = "B56_SWAP_OVERRIDE_RECEIPT"
OVERRIDE_SHA_ENV = "B56_SWAP_OVERRIDE_RECEIPT_SHA256"
OVERLAY_PATH_ENV = "B56_SWAP_OPERATIONAL_OVERLAY"
OVERLAY_SHA_ENV = "B56_SWAP_OPERATIONAL_OVERLAY_SHA256"


class SwapOverrideGateError(RuntimeError):
    """Fail-closed error for the operational resource-gate overlay."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = audit_swap_override_gate(
            frozen_contract_path=Path(args.frozen_contract).expanduser().resolve(),
            preparation_receipt_path=Path(args.preparation_receipt)
            .expanduser()
            .resolve(),
            policy_approval_receipt_path=Path(args.policy_approval_receipt)
            .expanduser()
            .resolve(),
            snapshot_path=Path(args.resource_snapshot).expanduser().resolve(),
            override_receipt_path=_environment_file(OVERRIDE_PATH_ENV, OVERRIDE_SHA_ENV),
            overlay_manifest_path=_environment_file(OVERLAY_PATH_ENV, OVERLAY_SHA_ENV),
            out_dir=out_dir,
            stage=args.stage,
            current_accepted=args.current_accepted,
            measured_pilot_bytes_per_geometry=args.measured_pilot_bytes_per_geometry,
            current_concurrency=args.current_concurrency,
            healthy_check_streak=args.healthy_check_streak,
            pilot_1000_safe_concurrency=args.pilot_1000_safe_concurrency,
            max_age_seconds=args.max_age_seconds,
        )
    except SwapOverrideGateError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"swap_policy={receipt['swap_policy']}")
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


def audit_swap_override_gate(
    *,
    frozen_contract_path: Path,
    preparation_receipt_path: Path,
    policy_approval_receipt_path: Path,
    snapshot_path: Path,
    override_receipt_path: Path,
    overlay_manifest_path: Path,
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
        raise SwapOverrideGateError(f"no-clobber output exists: {out_dir}")
    if max_age_seconds <= 0:
        raise SwapOverrideGateError("max_age_seconds must be positive")

    contract = _read_json(frozen_contract_path, "frozen contract")
    preparation = _read_json(preparation_receipt_path, "preparation receipt")
    approval = _read_json(policy_approval_receipt_path, "policy approval receipt")
    snapshot = _read_json(snapshot_path, "swap-override resource snapshot")
    override = _read_json(override_receipt_path, "swap override receipt")
    overlay = _read_json(overlay_manifest_path, "operational overlay manifest")
    now = evaluated_utc or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise SwapOverrideGateError("evaluated_utc must be timezone aware")

    fingerprint = str(contract.get("contract_fingerprint_sha256") or "")
    checks = [
        _check(
            "contract_validation",
            not validate_contract(contract),
            validate_contract(contract)[:5],
        ),
        _check(
            "contract_identity_exact",
            contract.get("campaign_id") == CAMPAIGN_ID
            and fingerprint == SCIENTIFIC_CONTRACT_FINGERPRINT
            and fingerprint == contract_fingerprint(contract),
            fingerprint,
        ),
        _check(
            "preparation_receipt_exact",
            legacy._preparation_exact(preparation, fingerprint),
            preparation.get("decision"),
        ),
        _check(
            "base_policy_approval_exact",
            legacy._approval_exact(approval, fingerprint),
            approval.get("decision"),
        ),
        _check(
            "base_policy_candidate_evidence_exact",
            legacy._approved_candidate_exact(approval),
            approval.get("approved_candidate"),
        ),
        _check(
            "owner_swap_override_exact",
            _override_exact(override),
            override.get("decision"),
        ),
        _check(
            "operational_overlay_exact",
            _overlay_exact(
                overlay,
                overlay_manifest_path=overlay_manifest_path,
                override_receipt_path=override_receipt_path,
            ),
            overlay.get("decision"),
        ),
    ]
    captured = _parse_datetime(snapshot.get("captured_utc"))
    age_seconds = (
        (now.astimezone(timezone.utc) - captured).total_seconds()
        if captured is not None
        else math.inf
    )
    checks.extend(
        [
            _check(
                "snapshot_timestamp_timezone_aware",
                captured is not None,
                snapshot.get("captured_utc"),
            ),
            _check(
                "snapshot_fresh",
                captured is not None
                and -30.0 <= age_seconds <= max_age_seconds,
                f"age_seconds={age_seconds}",
            ),
        ]
    )
    failed_control = [item["name"] for item in checks if not item["pass"]]
    if failed_control:
        raise SwapOverrideGateError(
            "control evidence failed: " + ", ".join(failed_control)
        )

    try:
        decision = evaluate_capacity_snapshot(
            snapshot,
            stage=stage,
            current_accepted=current_accepted,
            measured_pilot_bytes_per_geometry=measured_pilot_bytes_per_geometry,
        )
        metrics = decision["metrics"]
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
        raise SwapOverrideGateError(str(exc)) from exc

    status = "PASS" if decision["pass"] and concurrency["concurrency"] >= 1 else "WAIT"
    permissions = {
        gate_field: bool(status == "PASS" and approval.get(approval_field) is True)
        for gate_field, approval_field in legacy.PERMISSION_FIELDS.items()
    }
    stage_name = str(stage).upper()
    stage_target = STAGE_BY_NAME[stage_name].cumulative_target
    valid_until = captured + timedelta(seconds=max_age_seconds)
    receipt = {
        "schema": GATE_SCHEMA,
        "generated_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "READY_FOR_CURRENT_STAGE"
            if status == "PASS"
            else "WAITING_FOR_CAPACITY"
        ),
        "campaign_id": CAMPAIGN_ID,
        "queue_id": QUEUE_ID,
        "supervisor_id": SUPERVISOR_ID,
        "contract_fingerprint_sha256": fingerprint,
        "resource_policy": RESOURCE_POLICY,
        "swap_policy": SWAP_POLICY,
        "swap_zero_requirement_removed": True,
        "authorization_scope": "FULL_CAMPAIGN_PLUS_OPERATIONAL_SWAP_OVERRIDE",
        "execution_mode": "HIGH_LOAD_OR_SWAP_RECOVERY_DEGRADED_MODE",
        "current_stage": stage_name,
        "current_accepted": int(current_accepted),
        "resources_available": status == "PASS",
        "load_gate_pass": decision["checks"]["normalized_load1_gate"]
        and decision["checks"]["normalized_load5_gate"],
        "memory_gate_pass": decision["checks"]["memory_gate"],
        "no_oom_gate_pass": decision["checks"]["no_oom_gate"],
        "iowait_gate_pass": decision["checks"]["iowait_gate"],
        "swap_gate_pass": decision["checks"]["swap_sample_interval_gate"]
        and decision["checks"]["swap_thrash_gate"],
        "storage_gate_pass": decision["checks"]["storage_gate"],
        "license_gate_pass": decision["checks"]["license_gate"],
        "isolation_gate_pass": decision["checks"]["isolation_gate"],
        "raw_load1": metrics["raw_load1"],
        "logical_cpu_count": metrics["logical_cpu_count"],
        "normalized_load1": metrics["normalized_load1"],
        "normalized_load5": metrics["normalized_load5"],
        "normalized_load15": metrics["normalized_load15"],
        "available_memory_fraction": metrics["available_memory_fraction"],
        "swap_in_60s_pages": metrics["swap_in_pages_delta"],
        "swap_out_60s_pages": metrics["swap_out_pages_delta"],
        "iowait_percent": metrics["iowait_percent"],
        "oom_kill_delta": metrics["oom_kill_delta"],
        "blocked_process_count_delta": metrics["blocked_process_count_delta"],
        "active_swap_thrashing": metrics["active_swap_thrashing"],
        "advisory_nonzero_swap_in": metrics["advisory_nonzero_swap_in"],
        "active_simulator_jobs": metrics["active_simulator_jobs"],
        "required_storage_bytes": metrics["required_storage_bytes"],
        "filesystem_free_bytes": metrics["filesystem_free_bytes"],
        "max_allowed_concurrency": (
            concurrency["concurrency"] if status == "PASS" else 0
        ),
        "concurrency_hard_cap": concurrency["hard_cap"],
        "concurrency_action": concurrency["action"],
        "concurrency_reasons": concurrency["reasons"],
        **permissions,
        "accepted_geometry_target": TARGET_ACCEPTED_GEOMETRIES,
        "stage_remaining_accepted": stage_target - int(current_accepted),
        "max_new_candidate_attempts": (
            stage_target - int(current_accepted) if status == "PASS" else 0
        ),
        "snapshot_age_seconds": age_seconds,
        "snapshot_captured_utc": captured.isoformat(timespec="seconds"),
        "max_snapshot_age_seconds": max_age_seconds,
        "valid_until_utc": valid_until.isoformat(timespec="seconds"),
        "checks": checks
        + [
            _check(name, passed, passed)
            for name, passed in decision["checks"].items()
        ],
        "evidence": {
            "frozen_contract": _file_record(frozen_contract_path),
            "preparation_receipt": _file_record(preparation_receipt_path),
            "base_policy_approval_receipt": _file_record(
                policy_approval_receipt_path
            ),
            "owner_swap_override_receipt": _file_record(override_receipt_path),
            "operational_overlay_manifest": _file_record(overlay_manifest_path),
            "resource_snapshot": _file_record(snapshot_path),
        },
        "execution_effect": "NONE_AUDIT_ONLY",
        "authorization_boundary": (
            "Nonzero swap-in alone is advisory. PASS authorizes only the current "
            "ordered stage at the reported concurrency; all scientific and physical "
            "contracts remain unchanged."
        ),
    }
    _write_no_clobber(out_dir, receipt)
    return receipt


def _override_exact(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("schema") == OVERRIDE_RECEIPT_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == OVERRIDE_DECISION
        and payload.get("authorization_scope") == "OPERATIONAL_SWAP_GATE_ONLY"
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and payload.get("swap_policy") == SWAP_POLICY
        and payload.get("swap_zero_requirement_removed") is True
        and payload.get("nonzero_swap_in_alone_is_advisory") is True
        and payload.get("scientific_contract_changed") is False
        and payload.get("new_queue_or_campaign_authorized") is False
        and payload.get("nn_training_authorized") is False
        and payload.get("execution_effect") == "NONE_RECORD_ONLY"
        and _all_checks_pass(payload)
    )


def _overlay_exact(
    payload: Mapping[str, Any],
    *,
    overlay_manifest_path: Path,
    override_receipt_path: Path,
) -> bool:
    scripts = payload.get("script_identities")
    policy = payload.get("policy_module")
    return (
        payload.get("schema") == OVERLAY_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == "BIND_OPERATIONAL_SWAP_POLICY_OVERLAY"
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and payload.get("swap_policy") == SWAP_POLICY
        and payload.get("new_backend_created") is False
        and payload.get("new_queue_or_campaign_created") is False
        and payload.get("nn_training_authorized") is False
        and _identity_exact(override_receipt_path, payload.get("override_receipt"))
        and isinstance(scripts, Mapping)
        and _identity_exact(Path(__file__).resolve(), scripts.get("resource_gate_auditor"))
        and isinstance(policy, Mapping)
        and _identity_exact(
            Path(swap_policy_module.__file__).resolve(), policy
        )
        and _file_record(overlay_manifest_path)["sha256"]
        == os.environ.get(OVERLAY_SHA_ENV)
    )


def _environment_file(path_name: str, sha_name: str) -> Path:
    path_text = os.environ.get(path_name, "")
    expected = os.environ.get(sha_name, "")
    path = Path(path_text).expanduser().resolve()
    if not path_text or not path.is_file() or _sha256(path) != expected:
        raise SwapOverrideGateError(f"{path_name} identity mismatch")
    return path


def _identity_exact(path: Path, value: Any) -> bool:
    if not isinstance(value, Mapping) or not path.is_file():
        return False
    return (
        Path(str(value.get("path") or "")).expanduser().resolve() == path.resolve()
        and value.get("size_bytes") == path.stat().st_size
        and value.get("sha256") == _sha256(path)
    )


def _all_checks_pass(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, Mapping) and item.get("pass") is True for item in checks
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SwapOverrideGateError(f"failed to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SwapOverrideGateError(f"{label} root is not an object")
    return payload


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


def _write_no_clobber(out_dir: Path, receipt: Mapping[str, Any]) -> None:
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise SwapOverrideGateError(f"staging path exists: {staging}")
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


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
