#!/usr/bin/env python3
"""Audit a private resource/license snapshot for exactly one golden run.

This command consumes evidence that was collected elsewhere. It does not query
licenses, inspect processes, create a run root, connect to MARS, or invoke any
simulator. PASS authorizes only one exact-contract golden geometry; WAIT and
FAIL authorize no simulator action.
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
    contract_fingerprint,
    validate_contract,
)


SNAPSHOT_SCHEMA = "rfic_transformer.broadband56_v2_resource_license_snapshot.v1"
AUTHORIZATION_SCHEMA = "rfic_transformer.broadband56_v2_golden_authorization.v1"
GATE_SCHEMA = "rfic_transformer.broadband56_v2_resource_license_gate.v1"
AUTHORIZATION_DECISION = "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY"
PREPARATION_DECISION = "PREPARED_FOR_GOLDEN_GATE"
SHA256_LENGTH = 64


class ResourceGateError(RuntimeError):
    """Raised when resource/license evidence cannot be audited."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=output path already exists (no-clobber): {out_dir}", file=sys.stderr)
        return 2
    try:
        receipt = audit_resource_license_gate(
            frozen_contract_path=Path(args.frozen_contract).expanduser().resolve(),
            preparation_receipt_path=Path(args.preparation_receipt).expanduser().resolve(),
            authorization_receipt_path=Path(args.golden_authorization_receipt).expanduser().resolve(),
            snapshot_path=Path(args.resource_snapshot).expanduser().resolve(),
            out_dir=out_dir,
            max_age_seconds=int(args.max_age_seconds),
        )
    except ResourceGateError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"golden_launch_authorized={str(receipt['golden_launch_authorized']).lower()}")
    print(f"receipt={out_dir / 'RESOURCE_LICENSE_GATE.json'}")
    return 0 if receipt["overall_status"] in {"PASS", "WAIT"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-contract", required=True)
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--golden-authorization-receipt", required=True)
    parser.add_argument("--resource-snapshot", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def audit_resource_license_gate(
    *,
    frozen_contract_path: Path,
    preparation_receipt_path: Path,
    authorization_receipt_path: Path,
    snapshot_path: Path,
    out_dir: Path,
    max_age_seconds: int = 900,
    evaluated_utc: datetime | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        raise ResourceGateError(f"output path already exists (no-clobber): {out_dir}")
    if max_age_seconds <= 0:
        raise ResourceGateError("max_age_seconds must be positive")

    contract = _read_json(frozen_contract_path, "frozen contract")
    preparation = _read_json(preparation_receipt_path, "preparation receipt")
    authorization = _read_json(authorization_receipt_path, "golden authorization receipt")
    snapshot = _read_json(snapshot_path, "resource/license snapshot")
    now = evaluated_utc or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ResourceGateError("evaluated_utc must be timezone aware")

    fingerprint = str(contract.get("contract_fingerprint_sha256") or "")
    contract_errors = validate_contract(contract)
    control_checks = [
        _check("contract_validation", not contract_errors, contract_errors[:5]),
        _check("contract_campaign_id", contract.get("campaign_id") == CAMPAIGN_ID, contract.get("campaign_id")),
        _check(
            "contract_fingerprint",
            _is_sha256(fingerprint) and fingerprint == contract_fingerprint(contract),
            fingerprint,
        ),
        _check("contract_preparation_status", contract.get("preparation_status") == "PASS", contract.get("preparation_status")),
        _check(
            "preparation_receipt_identity",
            preparation.get("overall_status") == "PASS"
            and preparation.get("decision") == PREPARATION_DECISION
            and preparation.get("campaign_id") == CAMPAIGN_ID
            and preparation.get("contract_fingerprint_sha256") == fingerprint
            and _checks_are_exact_pass(preparation),
            preparation.get("decision"),
        ),
        _check(
            "golden_authorization_identity",
            authorization.get("schema") == AUTHORIZATION_SCHEMA
            and authorization.get("overall_status") == "PASS"
            and authorization.get("decision") == AUTHORIZATION_DECISION
            and authorization.get("approved_candidate", {}).get("campaign_id") == CAMPAIGN_ID
            and authorization.get("approved_candidate", {}).get("contract_fingerprint_sha256") == fingerprint
            and _is_sha256(str(authorization.get("approved_candidate", {}).get("sha256") or ""))
            and _checks_are_exact_pass(authorization),
            authorization.get("decision"),
        ),
        _check("authorization_one_golden", authorization.get("one_golden_authorized") is True, authorization.get("one_golden_authorized")),
        _check(
            "authorization_resource_gate",
            authorization.get("resource_and_license_gate_authorized") is True,
            authorization.get("resource_and_license_gate_authorized"),
        ),
        _check(
            "authorization_cadence_one_golden",
            authorization.get("cadence_authorized_for_one_golden") is True,
            authorization.get("cadence_authorized_for_one_golden"),
        ),
        _check(
            "authorization_calibre_one_golden",
            authorization.get("calibre_authorized_for_one_golden") is True,
            authorization.get("calibre_authorized_for_one_golden"),
        ),
        _check(
            "authorization_emx_one_golden",
            authorization.get("emx_authorized_for_one_golden") is True,
            authorization.get("emx_authorized_for_one_golden"),
        ),
        _check(
            "authorization_simulator_geometry_limit",
            authorization.get("simulator_geometry_limit") == 1,
            authorization.get("simulator_geometry_limit"),
        ),
        _check("authorization_pilot_32_forbidden", authorization.get("pilot_32_authorized") is False, authorization.get("pilot_32_authorized")),
        _check("authorization_pilot_1000_forbidden", authorization.get("pilot_1000_authorized") is False, authorization.get("pilot_1000_authorized")),
        _check("authorization_queue_forbidden", authorization.get("queue_authorized") is False, authorization.get("queue_authorized")),
        _check("authorization_supervisor_forbidden", authorization.get("supervisor_authorized") is False, authorization.get("supervisor_authorized")),
        _check("authorization_phase_a_forbidden", authorization.get("phase_a_authorized") is False, authorization.get("phase_a_authorized")),
        _check("authorization_phase_b_forbidden", authorization.get("phase_b_authorized") is False, authorization.get("phase_b_authorized")),
        _check("authorization_phase_c_forbidden", authorization.get("phase_c_authorized") is False, authorization.get("phase_c_authorized")),
        _check("authorization_campaign_forbidden", authorization.get("campaign_200k_authorized") is False, authorization.get("campaign_200k_authorized")),
        _check("authorization_record_only", authorization.get("execution_effect") == "NONE_RECORD_ONLY", authorization.get("execution_effect")),
        _check("snapshot_schema", snapshot.get("schema") == SNAPSHOT_SCHEMA, snapshot.get("schema")),
        _check("snapshot_campaign_id", snapshot.get("campaign_id") == CAMPAIGN_ID, snapshot.get("campaign_id")),
        _check("snapshot_contract_fingerprint", snapshot.get("contract_fingerprint_sha256") == fingerprint, snapshot.get("contract_fingerprint_sha256")),
    ]

    captured = _parse_datetime(snapshot.get("captured_utc"))
    age_seconds = (now.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() if captured else math.inf
    control_checks.extend(
        [
            _check("snapshot_timestamp_timezone_aware", captured is not None, snapshot.get("captured_utc")),
            _check(
                "snapshot_is_fresh",
                captured is not None and -30.0 <= age_seconds <= float(max_age_seconds),
                f"age_seconds={age_seconds}, max={max_age_seconds}",
            ),
        ]
    )

    consistency_checks, availability_checks = _availability_checks(snapshot)
    control_checks.extend(consistency_checks)
    control_pass = bool(control_checks) and all(item["pass"] for item in control_checks)
    availability_pass = bool(availability_checks) and all(item["pass"] for item in availability_checks)
    if not control_pass:
        status = "FAIL"
        decision = "DO_NOT_RUN_GOLDEN"
    elif availability_pass:
        status = "PASS"
        decision = "READY_FOR_ONE_GOLDEN_ONLY"
    else:
        status = "WAIT"
        decision = "PREPARED_WAITING_FOR_RESOURCE"

    golden_authorized = status == "PASS"
    valid_until = (
        captured.astimezone(timezone.utc) + timedelta(seconds=max_age_seconds)
        if captured is not None
        else now.astimezone(timezone.utc)
    )
    receipt = {
        "schema": GATE_SCHEMA,
        "generated_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "authorization_scope": "ONE_GOLDEN_ONLY",
        "resources_available": availability_pass,
        "load_gate_pass": _named_pass(availability_checks, "load_gate"),
        "memory_gate_pass": _named_pass(availability_checks, "memory_gate"),
        "storage_gate_pass": _named_pass(availability_checks, "storage_gate"),
        "license_gate_pass": _named_pass(availability_checks, "license_gate"),
        "isolation_gate_pass": _named_pass(availability_checks, "process_and_path_isolation_gate"),
        "golden_launch_authorized": golden_authorized,
        "pilot_32_launch_authorized": False,
        "pilot_1000_launch_authorized": False,
        "queue_launch_authorized": False,
        "supervisor_launch_authorized": False,
        "campaign_launch_authorized": False,
        "simulator_geometry_limit": 1 if golden_authorized else 0,
        "snapshot_age_seconds": age_seconds if math.isfinite(age_seconds) else None,
        "snapshot_captured_utc": (
            captured.astimezone(timezone.utc).isoformat(timespec="seconds")
            if captured is not None
            else None
        ),
        "max_snapshot_age_seconds": max_age_seconds,
        "valid_until_utc": valid_until.isoformat(timespec="seconds"),
        "checks": control_checks + availability_checks,
        "evidence": {
            "frozen_contract": _file_evidence(frozen_contract_path),
            "preparation_receipt": _file_evidence(preparation_receipt_path),
            "golden_authorization_receipt": _file_evidence(authorization_receipt_path),
            "resource_snapshot": _file_evidence(snapshot_path),
        },
        "execution_effect": "NONE_AUDIT_ONLY",
        "authorization_boundary": (
            "PASS permits at most one exact-contract golden geometry and requires a stop after its receipt. "
            "WAIT or FAIL permits no simulator action. Pilots, queues, supervisors, phases, and campaign "
            "launch remain unauthorized in every status."
        ),
    }
    _write_no_clobber(out_dir, receipt)
    return receipt


def _availability_checks(
    snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resources = snapshot.get("resources") if isinstance(snapshot.get("resources"), dict) else {}
    licenses = snapshot.get("licenses") if isinstance(snapshot.get("licenses"), dict) else {}
    isolation = snapshot.get("isolation") if isinstance(snapshot.get("isolation"), dict) else {}

    load_1m = _finite_nonnegative(resources.get("load_1m"))
    load_limit = _finite_positive(resources.get("load_limit"))
    available_memory = _integer_nonnegative(resources.get("available_memory_bytes"))
    minimum_memory = _integer_positive(resources.get("minimum_available_memory_bytes"))
    available_storage = _integer_nonnegative(resources.get("available_storage_bytes"))
    minimum_storage = _integer_positive(resources.get("minimum_available_storage_bytes"))
    duplicate_supervisors = _integer_nonnegative(isolation.get("duplicate_supervisor_count"))
    duplicate_queues = _integer_nonnegative(isolation.get("duplicate_queue_count"))
    conflicting_solvers = _integer_nonnegative(isolation.get("conflicting_solver_process_count"))

    computed_load = load_1m is not None and load_limit is not None and load_1m <= load_limit
    computed_memory = available_memory is not None and minimum_memory is not None and available_memory >= minimum_memory
    computed_storage = available_storage is not None and minimum_storage is not None and available_storage >= minimum_storage
    computed_license = all(
        licenses.get(key) is True
        for key in ("cadence_available", "calibre_available", "emx_available")
    )
    computed_isolation = (
        duplicate_supervisors == 0
        and duplicate_queues == 0
        and conflicting_solvers == 0
        and isolation.get("output_path_collision") is False
    )
    resources_available = computed_load and computed_memory and computed_storage
    consistency = [
        _check(
            "resource_numeric_fields_valid",
            all(
                value is not None
                for value in (
                    load_1m,
                    load_limit,
                    available_memory,
                    minimum_memory,
                    available_storage,
                    minimum_storage,
                )
            ),
            "load, memory, and storage fields",
        ),
        _check(
            "reported_load_gate_consistent",
            isinstance(resources.get("load_gate_pass"), bool)
            and resources.get("load_gate_pass") is computed_load,
            resources.get("load_gate_pass"),
        ),
        _check(
            "reported_memory_gate_consistent",
            isinstance(resources.get("memory_gate_pass"), bool)
            and resources.get("memory_gate_pass") is computed_memory,
            resources.get("memory_gate_pass"),
        ),
        _check(
            "reported_storage_gate_consistent",
            isinstance(resources.get("storage_gate_pass"), bool)
            and resources.get("storage_gate_pass") is computed_storage,
            resources.get("storage_gate_pass"),
        ),
        _check(
            "license_fields_valid",
            all(
                isinstance(licenses.get(key), bool)
                for key in (
                    "cadence_available",
                    "calibre_available",
                    "emx_available",
                    "license_gate_pass",
                )
            ),
            "cadence/calibre/emx and aggregate license booleans",
        ),
        _check(
            "reported_license_gate_consistent",
            isinstance(licenses.get("license_gate_pass"), bool)
            and licenses.get("license_gate_pass") is computed_license,
            licenses.get("license_gate_pass"),
        ),
        _check(
            "isolation_fields_valid",
            duplicate_supervisors is not None
            and duplicate_queues is not None
            and conflicting_solvers is not None
            and isinstance(isolation.get("output_path_collision"), bool)
            and isinstance(isolation.get("isolation_gate_pass"), bool),
            "process counts, collision, and aggregate isolation boolean",
        ),
        _check(
            "reported_isolation_gate_consistent",
            isinstance(isolation.get("isolation_gate_pass"), bool)
            and isolation.get("isolation_gate_pass") is computed_isolation,
            isolation.get("isolation_gate_pass"),
        ),
        _check(
            "snapshot_resources_available_consistent",
            isinstance(resources.get("resources_available"), bool)
            and resources.get("resources_available") is resources_available,
            resources.get("resources_available"),
        ),
    ]
    availability = [
        _check("load_gate", computed_load, f"load_1m={load_1m}, limit={load_limit}"),
        _check(
            "memory_gate",
            computed_memory,
            f"available={available_memory}, minimum={minimum_memory}",
        ),
        _check(
            "storage_gate",
            computed_storage,
            f"available={available_storage}, minimum={minimum_storage}",
        ),
        _check("license_gate", computed_license, "cadence/calibre/emx availability"),
        _check(
            "process_and_path_isolation_gate",
            computed_isolation,
            (
                f"supervisors={duplicate_supervisors}, queues={duplicate_queues}, "
                f"solvers={conflicting_solvers}, collision={isolation.get('output_path_collision')}"
            ),
        ),
    ]
    return consistency, availability


def _named_pass(checks: list[dict[str, Any]], name: str) -> bool:
    return next((bool(item["pass"]) for item in checks if item["name"] == name), False)


def _checks_are_exact_pass(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, dict) and item.get("pass") is True for item in checks
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ResourceGateError(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceGateError(f"{label} is not valid JSON: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResourceGateError(f"{label} must be a JSON object")
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _finite_positive(value: Any) -> float | None:
    result = _finite_nonnegative(value)
    return result if result is not None and result > 0 else None


def _integer_nonnegative(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _integer_positive(value: Any) -> int | None:
    result = _integer_nonnegative(value)
    return result if result is not None and result > 0 else None


def _is_sha256(value: str) -> bool:
    return len(value) == SHA256_LENGTH and all(char in "0123456789abcdef" for char in value)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _file_evidence(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_no_clobber(out_dir: Path, receipt: Mapping[str, Any]) -> None:
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise ResourceGateError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        receipt_path = staging / "RESOURCE_LICENSE_GATE.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (staging / "SHA256SUMS.txt").write_text(
            f"{_sha256(receipt_path)}  {receipt_path.name}\n",
            encoding="utf-8",
        )
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
