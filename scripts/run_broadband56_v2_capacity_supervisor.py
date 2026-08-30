#!/usr/bin/env python3
"""Keep one low-frequency broadband56 capacity supervisor alive.

This controller is deliberately simulator-free.  It consumes one approved
initial snapshot, then executes a private read-only probe at most once per
``--poll-seconds``.  Unchanged checks update one atomic state file; a new
no-clobber resource-gate receipt is written only on a material state
transition.  A PASS is reported as ready for the separately audited golden
launcher, but this waiting controller never starts Cadence, Calibre, or EMX.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
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
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    POLICY_APPROVAL_SCHEMA,
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
)


STATE_NAME = "CAPACITY_SUPERVISOR_STATE.json"
LOCK_NAME = ".broadband56_v2_capacity_supervisor.lock"
PREPARATION_DECISION = "PREPARED_FOR_GOLDEN_GATE"
SNAPSHOT_LINE = re.compile(r"^SNAPSHOT=(.+)$", re.MULTILINE)
STOP_REQUESTED = False


class SupervisorError(RuntimeError):
    """Fail-closed controller error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = run_supervisor(args, out_dir=out_dir)
    except SupervisorError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={result['overall_status']}")
    print(f"supervisor_id={result['supervisor_id']}")
    print(f"state={out_dir / STATE_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-contract", required=True)
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--policy-approval-receipt", required=True)
    parser.add_argument("--probe-script", required=True)
    parser.add_argument("--resource-gate-auditor", required=True)
    parser.add_argument("--initial-snapshot", required=True)
    parser.add_argument("--initial-resource-gate", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--max-checks", type=int, default=0)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    if args.poll_seconds < 300:
        parser.error("--poll-seconds must be at least 300")
    if args.max_checks < 0:
        parser.error("--max-checks must be nonnegative; zero means unlimited")
    return args


def run_supervisor(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    inputs = {
        "frozen_contract": Path(args.frozen_contract).expanduser().resolve(),
        "preparation_receipt": Path(args.preparation_receipt).expanduser().resolve(),
        "policy_approval_receipt": Path(args.policy_approval_receipt).expanduser().resolve(),
        "probe_script": Path(args.probe_script).expanduser().resolve(),
        "resource_gate_auditor": Path(args.resource_gate_auditor).expanduser().resolve(),
        "initial_snapshot": Path(args.initial_snapshot).expanduser().resolve(),
        "initial_resource_gate": Path(args.initial_resource_gate).expanduser().resolve(),
        "python_bin": Path(os.path.abspath(os.path.expanduser(args.python_bin))),
    }
    for label, path in inputs.items():
        if not path.is_file():
            raise SupervisorError(f"missing {label}: {path}")
    _validate_control_evidence(inputs)

    lock_path = out_dir.parent / LOCK_NAME
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        raise SupervisorError("another authoritative capacity supervisor owns the lock") from exc

    out_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    transitions_dir = out_dir / "transitions"
    transitions_dir.mkdir(mode=0o700)
    supervisor_id = f"b56-v2-capacity-{os.getpid()}-{_stamp()}"
    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, f"{supervisor_id}\n".encode())
    os.fsync(lock_fd)
    _install_signal_handlers()

    previous_decision: str | None = None
    check_index = 0
    latest_state: dict[str, Any] = {}
    try:
        while not STOP_REQUESTED:
            check_index += 1
            if check_index == 1:
                snapshot_path = inputs["initial_snapshot"]
                gate_path = inputs["initial_resource_gate"]
                source = "approved_initial_snapshot"
            else:
                snapshot_path = _run_probe(inputs["probe_script"], out_dir, check_index)
                gate_path = None
                source = "private_read_only_probe"

            snapshot = _read_json(snapshot_path, "capacity snapshot")
            decision = evaluate_capacity_snapshot(snapshot, stage="GOLDEN", current_accepted=0)
            metrics = decision["metrics"]
            concurrency = adaptive_concurrency(
                stage="GOLDEN",
                logical_cpu_count=metrics["logical_cpu_count"],
                simulator_license_capacity=metrics["simulator_license_capacity"],
                current_concurrency=0,
                healthy_check_streak=0,
                normalized_load1=metrics["normalized_load1"],
                iowait_percent=metrics["iowait_percent"],
                available_memory_fraction=metrics["available_memory_fraction"],
                active_swap_thrashing=metrics["active_swap_thrashing"],
                licenses_available=decision["checks"]["license_gate"],
            )
            status = "READY_FOR_GOLDEN" if decision["pass"] and concurrency["concurrency"] >= 1 else "WAITING_FOR_CAPACITY"

            if gate_path is not None:
                gate = _read_json(gate_path, "initial resource gate")
                expected = "PASS" if status == "READY_FOR_GOLDEN" else "WAIT"
                if gate.get("overall_status") != expected:
                    raise SupervisorError("initial resource-gate decision mismatches the snapshot")
            elif status != previous_decision:
                gate_path = _write_transition_gate(
                    inputs=inputs,
                    snapshot_path=snapshot_path,
                    transitions_dir=transitions_dir,
                    check_index=check_index,
                )

            isolation = snapshot.get("isolation") or {}
            active_jobs = sum(
                int(isolation.get(name, 0))
                for name in (
                    "project_owned_cadence_children",
                    "project_owned_calibre_children",
                    "project_owned_emx_children",
                )
            )
            latest_state = {
                "schema": "rfic_transformer.broadband56_v2_capacity_supervisor_state.v1",
                "generated_utc": _utc_now(),
                "overall_status": status,
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "resource_policy": RESOURCE_POLICY,
                "supervisor_id": supervisor_id,
                "supervisor_pid": os.getpid(),
                "check_index": check_index,
                "poll_seconds": int(args.poll_seconds),
                "current_stage": "GOLDEN",
                "current_accepted": 0,
                "emx_success": 0,
                "active_simulator_jobs": active_jobs,
                "max_allowed_concurrency": concurrency["concurrency"] if status == "READY_FOR_GOLDEN" else 0,
                "raw_load1": metrics["raw_load1"],
                "logical_cpu_count": metrics["logical_cpu_count"],
                "normalized_load1": metrics["normalized_load1"],
                "normalized_load5": metrics["normalized_load5"],
                "available_memory_fraction": metrics["available_memory_fraction"],
                "iowait_percent": metrics["iowait_percent"],
                "active_swap_thrashing": metrics["active_swap_thrashing"],
                "license_gate": "PASS" if decision["checks"]["license_gate"] else "WAIT",
                "resource_gate": "PASS" if status == "READY_FOR_GOLDEN" else "WAIT",
                "failed_checks": decision["failed_checks"],
                "snapshot_source": source,
                "snapshot": _file_evidence(snapshot_path),
                "latest_transition_gate": _file_evidence(gate_path) if gate_path else None,
                "simulator_action_taken": False,
                "launch_boundary": "This waiting controller never starts Cadence, Calibre, or EMX.",
            }
            _write_json_atomic(out_dir / STATE_NAME, latest_state)
            previous_decision = status
            if args.max_checks and check_index >= args.max_checks:
                break
            _interruptible_sleep(int(args.poll_seconds))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return latest_state


def _validate_control_evidence(inputs: Mapping[str, Path]) -> None:
    contract = _read_json(inputs["frozen_contract"], "frozen contract")
    errors = validate_contract(contract)
    if errors:
        raise SupervisorError("frozen contract failed validation: " + "; ".join(errors[:5]))
    fingerprint = contract.get("contract_fingerprint_sha256")
    if (
        contract.get("campaign_id") != CAMPAIGN_ID
        or fingerprint != SCIENTIFIC_CONTRACT_FINGERPRINT
        or contract_fingerprint(contract) != fingerprint
    ):
        raise SupervisorError("frozen contract identity mismatch")
    preparation = _read_json(inputs["preparation_receipt"], "preparation receipt")
    if not (
        preparation.get("overall_status") == "PASS"
        and preparation.get("decision") == PREPARATION_DECISION
        and preparation.get("campaign_id") == CAMPAIGN_ID
        and preparation.get("contract_fingerprint_sha256") == fingerprint
    ):
        raise SupervisorError("preparation receipt identity mismatch")
    approval = _read_json(inputs["policy_approval_receipt"], "policy approval receipt")
    required = (
        "one_golden_authorized",
        "supervisor_authorized",
        "automatic_capacity_wait_resume_authorized",
    )
    if not (
        approval.get("schema") == POLICY_APPROVAL_SCHEMA
        and approval.get("overall_status") == "PASS"
        and approval.get("decision") == POLICY_APPROVAL_SCOPE
        and approval.get("campaign_id") == CAMPAIGN_ID
        and approval.get("contract_fingerprint_sha256") == fingerprint
        and approval.get("resource_policy") == RESOURCE_POLICY
        and all(approval.get(name) is True for name in required)
    ):
        raise SupervisorError("operational-policy approval identity mismatch")


def _run_probe(probe_script: Path, out_dir: Path, check_index: int) -> Path:
    log_path = out_dir / f"probe_{check_index:06d}.log"
    result = subprocess.run(
        ["nice", "-n", "19", "bash", str(probe_script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.write_text(result.stdout)
    if result.returncode != 0:
        raise SupervisorError(f"read-only probe failed with return code {result.returncode}")
    match = SNAPSHOT_LINE.search(result.stdout)
    if match is None:
        raise SupervisorError("read-only probe did not report SNAPSHOT path")
    path = Path(match.group(1).strip()).expanduser().resolve()
    if not path.is_file():
        raise SupervisorError(f"read-only probe snapshot is missing: {path}")
    return path


def _write_transition_gate(
    *,
    inputs: Mapping[str, Path],
    snapshot_path: Path,
    transitions_dir: Path,
    check_index: int,
) -> Path:
    out_dir = transitions_dir / f"{check_index:06d}_{_stamp()}"
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
        "GOLDEN",
        "--current-accepted",
        "0",
        "--out-dir",
        str(out_dir),
    ]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SupervisorError("resource-gate transition audit failed")
    receipt = out_dir / "CAPACITY_RESOURCE_GATE.json"
    if not receipt.is_file():
        raise SupervisorError("resource-gate transition receipt is missing")
    return receipt


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise SupervisorError(f"{label} is not a JSON object")
    return payload


def _file_evidence(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def _install_signal_handlers() -> None:
    def request_stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


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
