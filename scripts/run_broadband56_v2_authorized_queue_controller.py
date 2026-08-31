#!/usr/bin/env python3
"""Run the single durable broadband56 V2 authorization/capacity queue.

The controller registers a no-clobber private queue immediately and keeps
Cadence, Calibre, and EMX at zero until an exact PASS FULL_CAMPAIGN receipt is
present.  After approval it enforces fresh capacity gates and ordered stage
receipts before invoking one hash-bound stage launcher.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    EXPECTED_FEATURE_ROWS,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    POLICY_APPROVAL_SCHEMA,
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
    stage_for_progress,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_CANDIDATE_SCHEMA,
    FULL_CAMPAIGN_PASS_DECISION,
    PRODUCTION_BACKEND_ID,
    validate_full_campaign_candidate,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    BACKEND_VERIFICATION_PASS_CHECKS,
    BACKEND_VERIFICATION_PASS_DECISION,
    BACKEND_VERIFICATION_SCHEMA,
    validate_backend_identity_manifest,
    validate_stage_receipt,
)


QUEUE_SCHEMA = "rfic_transformer.broadband56_v2_mars_queue_entry.v1"
QUEUE_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_mars_queue_receipt.v1"
SUPERVISOR_SCHEMA = "rfic_transformer.broadband56_v2_authoritative_supervisor.v1"
STATUS_SCHEMA = "rfic_transformer.broadband56_v2_campaign_status.v1"
STATE_NAME = "CAMPAIGN_STATUS.json"
LOCK_NAME = ".broadband56_v2_authoritative_controller.lock"
SNAPSHOT_LINE = re.compile(r"^SNAPSHOT=(.+)$", re.MULTILINE)
STOP_REQUESTED = False

IMMUTABLE_ARTIFACTS = (
    "CAMPAIGN_CONTRACT.json",
    "SCIENTIFIC_CONTRACT_IDENTITY.json",
    "OPERATIONAL_POLICY_IDENTITY.json",
    "FREQUENCY_CONTRACT.json",
    "PORT_AND_GROUNDING_CONTRACT.json",
    "DRC_AND_LAYOUT_CONTRACT.json",
    "MARS_QUEUE_ENTRY.json",
    "MARS_QUEUE_RECEIPT.json",
    "SUPERVISOR_IDENTITY.json",
    "CAMPAIGN_LOCK.json",
)


class ControllerError(RuntimeError):
    """Fail-closed queue/controller error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    campaign_root = Path(args.campaign_root).expanduser().resolve()
    try:
        state = run_controller(args, campaign_root=campaign_root)
    except ControllerError as exc:
        print(f"overall_status=BLOCKED\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={state['overall_status']}")
    print(f"controller_id={state['authoritative_supervisor']}")
    print(f"campaign_status={campaign_root / STATE_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-contract", required=True)
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--policy-approval-receipt", required=True)
    parser.add_argument("--full-campaign-candidate", required=True)
    parser.add_argument("--full-campaign-candidate-sha256", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--backend-identity-verification-receipt", required=True)
    parser.add_argument("--stage-launcher", required=True)
    parser.add_argument("--probe-script", required=True)
    parser.add_argument("--resource-gate-auditor", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--lock-path")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-checks", type=int, default=0)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not 30 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be between 30 and 300")
    if args.max_checks < 0:
        parser.error("--max-checks must be nonnegative; zero means unlimited")
    return args


def run_controller(args: argparse.Namespace, *, campaign_root: Path) -> dict[str, Any]:
    inputs = _resolve_inputs(args)
    evidence = _validate_control_evidence(inputs, args)
    lock_path = (
        Path(args.lock_path).expanduser().resolve()
        if args.lock_path
        else campaign_root.parent / LOCK_NAME
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        raise ControllerError("another authoritative broadband56 controller owns the campaign lock") from exc

    controller_id = f"b56-v2-controller-{os.getpid()}-{_stamp()}"
    queue_id = f"b56-v2-queue-{_stamp()}"
    try:
        if campaign_root.exists():
            if not args.resume:
                raise ControllerError(f"no-clobber campaign root already exists: {campaign_root}")
            queue_id, controller_id = _validate_resume_root(
                campaign_root,
                evidence=evidence,
            )
        else:
            _register_queue(
                campaign_root=campaign_root,
                lock_path=lock_path,
                controller_id=controller_id,
                queue_id=queue_id,
                evidence=evidence,
                poll_seconds=int(args.poll_seconds),
            )
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"{controller_id}\n".encode("utf-8"))
        os.fsync(lock_fd)
        _install_signal_handlers()

        check_index = 0
        latest: dict[str, Any] = {}
        while not STOP_REQUESTED:
            check_index += 1
            snapshot_path = _run_probe(
                inputs["probe_script"],
                campaign_root / "resource_snapshots",
                check_index,
            )
            snapshot = _read_json(snapshot_path, "capacity snapshot")
            stage_receipts = _ordered_stage_receipts(campaign_root)
            current_accepted = (
                int(stage_receipts[-1]["accepted_unique_geometries"])
                if stage_receipts
                else 0
            )
            stage = stage_for_progress(
                current_accepted=current_accepted,
                stage_receipts=stage_receipts,
            )
            active_jobs = _active_simulator_jobs(snapshot)
            receipt = _load_full_campaign_receipt(
                inputs["full_campaign_receipt"],
                candidate_sha256=evidence["candidate"]["sha256"],
                backend_manifest_sha256=evidence["backend_identity_manifest"]["sha256"],
            )

            resource_gate = "NOT_RUN"
            failed_checks: list[str] = []
            concurrency = 0
            launch_taken = False
            latest_gate_path: Path | None = None
            if receipt is None:
                lifecycle = "QUEUED_WAITING_FOR_AUTHORIZATION"
            elif stage == "COMPLETE":
                lifecycle = "COMPLETE_200K"
                resource_gate = "PASS"
            else:
                policy = evaluate_capacity_snapshot(
                    snapshot,
                    stage=stage,
                    current_accepted=current_accepted,
                    measured_pilot_bytes_per_geometry=_pilot_bytes_per_geometry(campaign_root),
                )
                failed_checks = list(policy["failed_checks"])
                resource_gate = "PASS" if policy["pass"] else "WAIT"
                concurrency_result = adaptive_concurrency(
                    stage=stage,
                    logical_cpu_count=policy["metrics"]["logical_cpu_count"],
                    simulator_license_capacity=policy["metrics"]["simulator_license_capacity"],
                    current_concurrency=None,
                    healthy_check_streak=0,
                    normalized_load1=policy["metrics"]["normalized_load1"],
                    iowait_percent=policy["metrics"]["iowait_percent"],
                    available_memory_fraction=policy["metrics"]["available_memory_fraction"],
                    active_swap_thrashing=policy["metrics"]["active_swap_thrashing"],
                    licenses_available=policy["checks"]["license_gate"],
                    pilot_1000_safe_concurrency=_pilot_safe_concurrency(campaign_root),
                )
                concurrency = int(concurrency_result["concurrency"])
                latest_gate_path = _write_resource_gate(
                    inputs=inputs,
                    snapshot_path=snapshot_path,
                    campaign_root=campaign_root,
                    check_index=check_index,
                    stage=stage,
                    current_accepted=current_accepted,
                )
                if not policy["pass"] or concurrency < 1:
                    lifecycle = "QUEUED_WAITING_FOR_CAPACITY"
                elif active_jobs != 0:
                    raise ControllerError(
                        "capacity snapshot reports project simulator children before a controller launch"
                    )
                else:
                    lifecycle = f"{stage}_RUNNING"
                    latest = _state_payload(
                        lifecycle=lifecycle,
                        controller_id=controller_id,
                        queue_id=queue_id,
                        stage=stage,
                        current_accepted=current_accepted,
                        active_jobs=0,
                        concurrency=concurrency,
                        resource_gate=resource_gate,
                        failed_checks=failed_checks,
                        snapshot_path=snapshot_path,
                        gate_path=latest_gate_path,
                        check_index=check_index,
                        launch_taken=False,
                    )
                    _write_json_atomic(campaign_root / STATE_NAME, latest)
                    _materialize_authorization_receipt(
                        source=inputs["full_campaign_receipt"],
                        campaign_root=campaign_root,
                    )
                    _run_stage_launcher(
                        inputs=inputs,
                        campaign_root=campaign_root,
                        stage=stage,
                        concurrency=concurrency,
                        snapshot_path=snapshot_path,
                        check_index=check_index,
                    )
                    launch_taken = True

            latest = _state_payload(
                lifecycle=lifecycle,
                controller_id=controller_id,
                queue_id=queue_id,
                stage="COMPLETE" if stage == "COMPLETE" else stage,
                current_accepted=current_accepted,
                active_jobs=active_jobs,
                concurrency=concurrency,
                resource_gate=resource_gate,
                failed_checks=failed_checks,
                snapshot_path=snapshot_path,
                gate_path=latest_gate_path,
                check_index=check_index,
                launch_taken=launch_taken,
            )
            _write_json_atomic(campaign_root / STATE_NAME, latest)
            if lifecycle == "COMPLETE_200K":
                break
            if args.max_checks and check_index >= args.max_checks:
                break
            _interruptible_sleep(int(args.poll_seconds))
        return latest
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _resolve_inputs(args: argparse.Namespace) -> dict[str, Path]:
    names = (
        "frozen_contract",
        "preparation_receipt",
        "policy_approval_receipt",
        "full_campaign_candidate",
        "full_campaign_receipt",
        "backend_identity_manifest",
        "backend_identity_verification_receipt",
        "stage_launcher",
        "probe_script",
        "resource_gate_auditor",
        "python_bin",
    )
    values = {
        name: Path(os.path.abspath(os.path.expanduser(str(getattr(args, name)))))
        for name in names
    }
    for name, path in values.items():
        if name == "full_campaign_receipt":
            continue
        if not path.is_file():
            raise ControllerError(f"missing {name}: {path}")
    return values


def _validate_control_evidence(
    inputs: Mapping[str, Path], args: argparse.Namespace
) -> dict[str, Any]:
    contract = _read_json(inputs["frozen_contract"], "frozen contract")
    errors = validate_contract(contract)
    if errors:
        raise ControllerError("frozen contract failed validation: " + "; ".join(errors[:5]))
    fingerprint = contract.get("contract_fingerprint_sha256")
    if (
        contract.get("campaign_id") != CAMPAIGN_ID
        or fingerprint != SCIENTIFIC_CONTRACT_FINGERPRINT
        or contract_fingerprint(contract) != fingerprint
    ):
        raise ControllerError("frozen contract identity mismatch")

    candidate = _read_json(inputs["full_campaign_candidate"], "full-campaign candidate")
    candidate_errors = validate_full_campaign_candidate(candidate)
    actual_candidate_sha = _sha256(inputs["full_campaign_candidate"])
    if candidate_errors:
        raise ControllerError("full-campaign candidate invalid: " + "; ".join(candidate_errors[:5]))
    if actual_candidate_sha != str(args.full_campaign_candidate_sha256).lower():
        raise ControllerError("full-campaign candidate SHA-256 mismatch")
    candidate_private = candidate.get("private_preparation_evidence")
    candidate_runtime = candidate.get("runtime_and_backend_identity")
    if not isinstance(candidate_private, Mapping) or not isinstance(
        candidate_runtime, Mapping
    ):
        raise ControllerError("full-campaign candidate lacks private runtime bindings")

    preparation = _read_json(inputs["preparation_receipt"], "preparation receipt")
    if not (
        preparation.get("overall_status") == "PASS"
        and preparation.get("decision") == "PREPARED_FOR_GOLDEN_GATE"
        and preparation.get("campaign_id") == CAMPAIGN_ID
        and preparation.get("contract_fingerprint_sha256") == fingerprint
    ):
        raise ControllerError("preparation receipt identity mismatch")
    if (
        _sha256(inputs["preparation_receipt"])
        != candidate_private.get("preparation_receipt_sha256")
        or _sha256(inputs["frozen_contract"])
        != candidate_private.get("campaign_contract_frozen_sha256")
    ):
        raise ControllerError("candidate preparation or frozen-contract SHA-256 mismatch")

    policy = _read_json(inputs["policy_approval_receipt"], "policy approval receipt")
    if not (
        policy.get("schema") == POLICY_APPROVAL_SCHEMA
        and policy.get("overall_status") == "PASS"
        and policy.get("decision") == POLICY_APPROVAL_SCOPE
        and policy.get("campaign_id") == CAMPAIGN_ID
        and policy.get("contract_fingerprint_sha256") == fingerprint
        and policy.get("resource_policy") == RESOURCE_POLICY
        and policy.get("queue_authorized") is True
        and policy.get("supervisor_authorized") is True
    ):
        raise ControllerError("operational policy approval identity mismatch")
    if (
        _sha256(inputs["policy_approval_receipt"])
        != candidate_private.get("operational_policy_approval_receipt_sha256")
    ):
        raise ControllerError("candidate operational-policy SHA-256 mismatch")

    backend = _read_json(inputs["backend_identity_manifest"], "backend identity manifest")
    runtime = candidate.get("runtime_and_backend_identity") or {}
    backend_errors = validate_backend_identity_manifest(backend, verify_files=True)
    if backend_errors:
        raise ControllerError(
            "backend identity manifest failed validation: "
            + "; ".join(backend_errors[:8])
        )
    if not (
        backend.get("campaign_id") == CAMPAIGN_ID
        and backend.get("contract_fingerprint_sha256") == fingerprint
        and backend.get("backend_id") == PRODUCTION_BACKEND_ID
        and _sha256(inputs["backend_identity_manifest"])
        == runtime.get("backend_identity_manifest_sha256")
    ):
        raise ControllerError("backend identity manifest mismatch")
    verification = _read_json(
        inputs["backend_identity_verification_receipt"],
        "backend identity verification receipt",
    )
    _validate_backend_verification_receipt(
        verification,
        receipt_path=inputs["backend_identity_verification_receipt"],
        manifest_path=inputs["backend_identity_manifest"],
        candidate_runtime=candidate_runtime,
    )
    scripts = backend.get("script_identities")
    runtimes = backend.get("runtime_identities")
    if not isinstance(scripts, Mapping) or not isinstance(runtimes, Mapping):
        raise ControllerError("backend manifest lacks identity maps")
    _require_input_identity(
        Path(__file__).resolve(),
        scripts.get("queue_controller"),
        candidate_runtime.get("queue_controller_sha256"),
        label="queue controller",
    )
    _require_input_identity(
        inputs["resource_gate_auditor"],
        scripts.get("resource_gate_auditor"),
        candidate_runtime.get("resource_gate_auditor_sha256"),
        label="resource-gate auditor",
    )
    _require_input_identity(
        inputs["stage_launcher"],
        scripts.get("stage_launcher"),
        candidate_runtime.get("stage_launcher_sha256"),
        label="stage launcher",
    )
    _require_input_identity(
        inputs["probe_script"],
        runtimes.get("resource_probe"),
        candidate_runtime.get("resource_probe_sha256"),
        label="resource probe",
    )
    _require_input_identity(
        inputs["python_bin"],
        runtimes.get("python_executable"),
        candidate_runtime.get("python_executable_sha256"),
        label="Python executable",
    )

    return {
        "contract": _file_record(inputs["frozen_contract"]),
        "preparation_receipt": _file_record(inputs["preparation_receipt"]),
        "policy_approval_receipt": _file_record(inputs["policy_approval_receipt"]),
        "candidate": _file_record(inputs["full_campaign_candidate"]),
        "backend_identity_manifest": _file_record(inputs["backend_identity_manifest"]),
        "backend_identity_verification_receipt": _file_record(
            inputs["backend_identity_verification_receipt"]
        ),
        "stage_launcher": _file_record(inputs["stage_launcher"]),
        "frequency_contract": candidate["frequency_contract"],
        "port_and_grounding_contract": candidate["port_and_grounding_contract"],
        "unchanged_physical_contract_items": candidate["unchanged_physical_contract_items"],
        "backend_id": backend["backend_id"],
    }


def _validate_backend_verification_receipt(
    receipt: Mapping[str, Any],
    *,
    receipt_path: Path,
    manifest_path: Path,
    candidate_runtime: Mapping[str, Any],
) -> None:
    if not (
        receipt.get("schema") == BACKEND_VERIFICATION_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == BACKEND_VERIFICATION_PASS_DECISION
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and receipt.get("checks") == BACKEND_VERIFICATION_PASS_CHECKS
        and receipt.get("errors") == []
        and receipt.get("simulator_action_taken") is False
        and receipt.get("authorization_effect")
        == "NONE_IDENTITY_VERIFICATION_ONLY"
    ):
        raise ControllerError("backend identity verification receipt is not exact PASS")
    if (
        _sha256(receipt_path)
        != candidate_runtime.get("backend_identity_verification_receipt_sha256")
    ):
        raise ControllerError("backend verification receipt SHA-256 mismatch")
    manifest_record = receipt.get("backend_identity_manifest")
    if not isinstance(manifest_record, Mapping):
        raise ControllerError("backend verification receipt lacks manifest identity")
    _require_input_identity(
        manifest_path,
        manifest_record,
        manifest_record.get("sha256"),
        label="verified backend manifest",
    )


def _require_input_identity(
    path: Path,
    record: Any,
    candidate_sha256: Any,
    *,
    label: str,
) -> None:
    if not isinstance(record, Mapping):
        raise ControllerError(f"{label} lacks a manifest identity")
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ControllerError(f"{label} is missing")
    before = resolved.stat()
    digest = _sha256(resolved)
    after = resolved.stat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise ControllerError(f"{label} changed while hashing")
    record_path = record.get("path")
    if not isinstance(record_path, str) or Path(record_path).expanduser().resolve() != resolved:
        raise ControllerError(f"{label} path differs from the approved manifest")
    if record.get("size_bytes") != after.st_size or record.get("sha256") != digest:
        raise ControllerError(f"{label} file identity differs from the approved manifest")
    if candidate_sha256 != digest:
        raise ControllerError(f"{label} SHA-256 differs from the approved candidate")


def _register_queue(
    *,
    campaign_root: Path,
    lock_path: Path,
    controller_id: str,
    queue_id: str,
    evidence: Mapping[str, Any],
    poll_seconds: int,
) -> None:
    campaign_root.mkdir(parents=True, mode=0o700)
    (campaign_root / "resource_snapshots").mkdir(mode=0o700)
    (campaign_root / "resource_gates").mkdir(mode=0o700)
    (campaign_root / "stages").mkdir(mode=0o700)
    shutil.copyfile(
        evidence["contract"]["path"],
        campaign_root / "CAMPAIGN_CONTRACT.json",
    )
    generated = _utc_now()
    _write_json(
        campaign_root / "SCIENTIFIC_CONTRACT_IDENTITY.json",
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "contract": evidence["contract"],
            "target_accepted_unique_geometries": TARGET_ACCEPTED_GEOMETRIES,
            "expected_geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
        },
    )
    _write_json(
        campaign_root / "OPERATIONAL_POLICY_IDENTITY.json",
        {
            "resource_policy": RESOURCE_POLICY,
            "policy_approval_receipt": evidence["policy_approval_receipt"],
            "poll_seconds": poll_seconds,
        },
    )
    _write_json(campaign_root / "FREQUENCY_CONTRACT.json", evidence["frequency_contract"])
    _write_json(
        campaign_root / "PORT_AND_GROUNDING_CONTRACT.json",
        evidence["port_and_grounding_contract"],
    )
    _write_json(
        campaign_root / "DRC_AND_LAYOUT_CONTRACT.json",
        {
            "unchanged_physical_contract_items": evidence["unchanged_physical_contract_items"],
            "zero_blocking_calibre_required": True,
            "manual_gds_modification_allowed": False,
        },
    )
    queue_entry = {
        "schema": QUEUE_SCHEMA,
        "generated_utc": generated,
        "queue_id": queue_id,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "queue_state": "QUEUED_WAITING_FOR_AUTHORIZATION",
        "backend_id": evidence["backend_id"],
        "candidate": evidence["candidate"],
        "backend_identity_manifest": evidence["backend_identity_manifest"],
        "backend_identity_verification_receipt": evidence[
            "backend_identity_verification_receipt"
        ],
        "stage_launcher": evidence["stage_launcher"],
        "one_authoritative_supervisor": controller_id,
        "bounded_pending_work_window_required": True,
        "simulator_jobs_active": 0,
        "simulator_action_taken": False,
    }
    _write_json(campaign_root / "MARS_QUEUE_ENTRY.json", queue_entry)
    _write_json(
        campaign_root / "MARS_QUEUE_RECEIPT.json",
        {
            "schema": QUEUE_RECEIPT_SCHEMA,
            "generated_utc": generated,
            "overall_status": "PASS",
            "decision": "QUEUE_REGISTERED_WAITING_FOR_AUTHORIZATION",
            "queue_id": queue_id,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "queue_entry": _file_record(campaign_root / "MARS_QUEUE_ENTRY.json"),
            "simulator_jobs_active": 0,
            "simulator_action_taken": False,
        },
    )
    _write_json(
        campaign_root / "SUPERVISOR_IDENTITY.json",
        {
            "schema": SUPERVISOR_SCHEMA,
            "generated_utc": generated,
            "campaign_id": CAMPAIGN_ID,
            "controller_id": controller_id,
            "controller_pid": os.getpid(),
            "poll_seconds": poll_seconds,
            "persistent_detached_execution_required": True,
            "one_authoritative_supervisor": True,
        },
    )
    _write_json(
        campaign_root / "CAMPAIGN_LOCK.json",
        {
            "generated_utc": generated,
            "campaign_id": CAMPAIGN_ID,
            "controller_id": controller_id,
            "lock_path": str(lock_path),
            "exclusive_flock_required": True,
        },
    )
    _write_immutable_sums(campaign_root)
    _write_json_atomic(
        campaign_root / STATE_NAME,
        _state_payload(
            lifecycle="QUEUED_WAITING_FOR_AUTHORIZATION",
            controller_id=controller_id,
            queue_id=queue_id,
            stage="GOLDEN",
            current_accepted=0,
            active_jobs=0,
            concurrency=0,
            resource_gate="NOT_RUN",
            failed_checks=[],
            snapshot_path=None,
            gate_path=None,
            check_index=0,
            launch_taken=False,
        ),
    )


def _validate_resume_root(
    campaign_root: Path,
    *,
    evidence: Mapping[str, Any],
) -> tuple[str, str]:
    sums = campaign_root / "SHA256SUMS.txt"
    if not sums.is_file():
        raise ControllerError("resume root lacks SHA256SUMS.txt")
    expected: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            expected[parts[1].strip()] = parts[0].strip()
    for name in IMMUTABLE_ARTIFACTS:
        path = campaign_root / name
        if not path.is_file() or expected.get(name) != _sha256(path):
            raise ControllerError(f"resume immutable artifact mismatch: {name}")
    entry = _read_json(campaign_root / "MARS_QUEUE_ENTRY.json", "queue entry")
    supervisor = _read_json(campaign_root / "SUPERVISOR_IDENTITY.json", "supervisor identity")
    if (
        entry.get("campaign_id") != CAMPAIGN_ID
        or entry.get("contract_fingerprint_sha256") != SCIENTIFIC_CONTRACT_FINGERPRINT
        or entry.get("candidate", {}).get("sha256") != evidence["candidate"]["sha256"]
        or entry.get("backend_identity_manifest", {}).get("sha256")
        != evidence["backend_identity_manifest"]["sha256"]
        or entry.get("backend_identity_verification_receipt", {}).get("sha256")
        != evidence["backend_identity_verification_receipt"]["sha256"]
    ):
        raise ControllerError("resume queue identity mismatch")
    return str(entry["queue_id"]), str(supervisor["controller_id"])


def _run_probe(probe_script: Path, out_dir: Path, check_index: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"probe_{check_index:06d}.log"
    result = subprocess.run(
        ["nice", "-n", "19", "bash", str(probe_script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise ControllerError(f"read-only probe failed with return code {result.returncode}")
    match = SNAPSHOT_LINE.search(result.stdout)
    if match is None:
        raise ControllerError("read-only probe did not report SNAPSHOT path")
    path = Path(match.group(1).strip()).expanduser().resolve()
    if not path.is_file():
        raise ControllerError(f"read-only probe snapshot is missing: {path}")
    return path


def _load_full_campaign_receipt(
    path: Path,
    *,
    candidate_sha256: str,
    backend_manifest_sha256: str,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    receipt = _read_json(path, "full-campaign receipt")
    required_true = (
        "queue_authorized",
        "supervisor_authorized",
        "automatic_capacity_wait_resume_authorized",
        "automatic_ordered_stage_execution_authorized",
        "cadence_authorized_within_current_stage",
        "calibre_authorized_within_current_stage",
        "emx_authorized_within_current_stage",
        "campaign_200k_authorized",
    )
    if not (
        receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION
        and receipt.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
        and receipt.get("approved_candidate", {}).get("sha256") == candidate_sha256
        and receipt.get("backend_identity_manifest", {}).get("sha256")
        == backend_manifest_sha256
        and receipt.get("simulator_geometry_limit") == TARGET_ACCEPTED_GEOMETRIES
        and all(receipt.get(field) is True for field in required_true)
    ):
        raise ControllerError("full-campaign receipt is present but invalid")
    return receipt


def _write_resource_gate(
    *,
    inputs: Mapping[str, Path],
    snapshot_path: Path,
    campaign_root: Path,
    check_index: int,
    stage: str,
    current_accepted: int,
) -> Path:
    out_dir = campaign_root / "resource_gates" / f"{check_index:06d}_{_stamp()}"
    command = [
        str(inputs["python_bin"]),
        str(inputs["resource_gate_auditor"]),
        "--frozen-contract",
        str(inputs["frozen_contract"]),
        "--preparation-receipt",
        str(inputs["preparation_receipt"]),
        "--policy-approval-receipt",
        str(inputs["policy_approval_receipt"]),
        "--resource-snapshot",
        str(snapshot_path),
        "--stage",
        stage,
        "--current-accepted",
        str(current_accepted),
        "--out-dir",
        str(out_dir),
    ]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ControllerError("resource-gate audit failed")
    path = out_dir / "CAPACITY_RESOURCE_GATE.json"
    if not path.is_file():
        raise ControllerError("resource-gate receipt is missing")
    return path


def _run_stage_launcher(
    *,
    inputs: Mapping[str, Path],
    campaign_root: Path,
    stage: str,
    concurrency: int,
    snapshot_path: Path,
    check_index: int,
) -> None:
    stage_spec = STAGE_BY_NAME[stage]
    prior_records = _ordered_stage_receipt_records(campaign_root)
    out_dir = campaign_root / "stages" / f"{check_index:06d}_{stage.lower()}_{_stamp()}"
    command = [
        str(inputs["python_bin"]),
        str(inputs["stage_launcher"]),
        "--stage",
        stage,
        "--cumulative-target",
        str(stage_spec.cumulative_target),
        "--campaign-root",
        str(campaign_root),
        "--out-dir",
        str(out_dir),
        "--full-campaign-receipt",
        str(inputs["full_campaign_receipt"]),
        "--backend-identity-manifest",
        str(inputs["backend_identity_manifest"]),
        "--resource-snapshot",
        str(snapshot_path),
        "--max-concurrency",
        str(concurrency),
    ]
    with out_dir.with_suffix(".stdout.log").open("w", encoding="utf-8") as stdout, (
        out_dir.with_suffix(".stderr.log")
    ).open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    if result.returncode != 0:
        raise ControllerError(f"{stage} launcher exited with return code {result.returncode}")
    receipt_path = out_dir / "STAGE_RECEIPT.json"
    receipt = _read_json(receipt_path, f"{stage} stage receipt")
    receipt_errors = validate_stage_receipt(
        receipt,
        stage=stage,
        cumulative_target=stage_spec.cumulative_target,
        backend_manifest_sha256=_sha256(inputs["backend_identity_manifest"]),
        authorization_receipt_sha256=_sha256(inputs["full_campaign_receipt"]),
        prior_stage_receipt_sha256=(
            _sha256(prior_records[-1][0]) if prior_records else None
        ),
        verify_artifacts=True,
        artifact_root=receipt_path.parent / "backend",
    )
    if receipt_errors:
        raise ControllerError(
            f"{stage} launcher did not produce the exact PASS stage receipt: "
            + "; ".join(receipt_errors[:8])
        )


def _ordered_stage_receipts(campaign_root: Path) -> list[dict[str, Any]]:
    return [value for _, value in _ordered_stage_receipt_records(campaign_root)]


def _ordered_stage_receipt_records(
    campaign_root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_RECEIPT.json")):
        value = _read_json(path, "stage receipt")
        if value.get("overall_status") == "PASS":
            records.append((path, value))
    return records


def _materialize_authorization_receipt(*, source: Path, campaign_root: Path) -> None:
    destination = campaign_root / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    if destination.exists():
        if _sha256(destination) != _sha256(source):
            raise ControllerError("materialized full-campaign receipt differs from approved source")
        return
    destination.write_bytes(source.read_bytes())


def _active_simulator_jobs(snapshot: Mapping[str, Any]) -> int:
    isolation = snapshot.get("isolation")
    if not isinstance(isolation, Mapping):
        raise ControllerError("capacity snapshot lacks isolation object")
    return sum(
        int(isolation.get(name, 0))
        for name in (
            "project_owned_cadence_children",
            "project_owned_calibre_children",
            "project_owned_emx_children",
        )
    )


def _pilot_bytes_per_geometry(campaign_root: Path) -> float | None:
    path = campaign_root / "PILOT_1000_RESOURCE_SUMMARY.json"
    if not path.is_file():
        return None
    value = _read_json(path, "pilot resource summary").get("bytes_per_geometry")
    return float(value) if value is not None else None


def _pilot_safe_concurrency(campaign_root: Path) -> int | None:
    path = campaign_root / "PILOT_1000_RESOURCE_SUMMARY.json"
    if not path.is_file():
        return None
    value = _read_json(path, "pilot resource summary").get("safe_concurrency")
    return int(value) if value is not None else None


def _state_payload(
    *,
    lifecycle: str,
    controller_id: str,
    queue_id: str,
    stage: str,
    current_accepted: int,
    active_jobs: int,
    concurrency: int,
    resource_gate: str,
    failed_checks: list[str],
    snapshot_path: Path | None,
    gate_path: Path | None,
    check_index: int,
    launch_taken: bool,
) -> dict[str, Any]:
    return {
        "schema": STATUS_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": lifecycle,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "queue_id": queue_id,
        "authoritative_supervisor": controller_id,
        "supervisor_count": 1,
        "check_index": check_index,
        "current_stage": stage,
        "current_accepted": current_accepted,
        "target_accepted": TARGET_ACCEPTED_GEOMETRIES,
        "feature_rows": current_accepted * 56,
        "active_simulator_jobs": active_jobs,
        "current_concurrency": concurrency,
        "resource_gate": resource_gate,
        "failed_resource_checks": failed_checks,
        "latest_resource_snapshot": _file_record(snapshot_path) if snapshot_path else None,
        "latest_resource_gate": _file_record(gate_path) if gate_path else None,
        "simulator_action_taken_on_this_iteration": launch_taken,
    }


def _write_immutable_sums(campaign_root: Path) -> None:
    lines = []
    for name in IMMUTABLE_ARTIFACTS:
        path = campaign_root / name
        if not path.is_file():
            raise ControllerError(f"queue registration omitted immutable artifact: {name}")
        lines.append(f"{_sha256(path)}  {name}")
    (campaign_root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _file_record(path: Path) -> dict[str, Any]:
    path = Path(path)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControllerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ControllerError(f"{label} is not a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    _write_json(temp, value)
    os.replace(temp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_signal_handlers() -> None:
    def stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def _interruptible_sleep(seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while not STOP_REQUESTED and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
