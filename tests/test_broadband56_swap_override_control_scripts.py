from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[1]
RECORDER = _load(
    ROOT / "scripts" / "record_broadband56_v2_swap_gate_operational_override.py",
    "swap_override_recorder",
)
AUDITOR = _load(
    ROOT / "scripts" / "audit_broadband56_v2_swap_override_resource_gate.py",
    "swap_override_auditor",
)
CONTROLLER = _load(
    ROOT / "scripts" / "run_broadband56_v2_swap_override_queue_controller.py",
    "swap_override_controller",
)


def test_owner_instruction_requires_exact_operational_boundaries() -> None:
    text = "\n".join(
        (
            "PROJECT-OWNER OPERATIONAL OVERRIDE:",
            RECORDER.CAMPAIGN_ID,
            RECORDER.QUEUE_ID,
            RECORDER.SUPERVISOR_ID,
            "SWAP_POLICY=COMBINED_RESOURCE_DEGRADATION_ONLY",
            "available-memory fraction >= 0.40",
            "iowait < 5%",
            "NN_TRAINING_AUTHORIZED=no",
        )
    )

    assert RECORDER._owner_instruction_exact(text) is True
    assert RECORDER._owner_instruction_exact(text.replace("0.40", "0.20")) is False


def test_approval_timestamp_must_be_timezone_aware_utc() -> None:
    assert RECORDER._parse_aware_utc("2026-09-02T17:41:45Z") is not None
    assert RECORDER._parse_aware_utc("2026-09-02T17:41:45") is None
    assert RECORDER._parse_aware_utc("2026-09-02T12:41:45-05:00") is None


def test_swap_controller_private_arguments_are_removed_before_rebound_parse() -> None:
    digest = "a" * 64
    values = []
    for name in (
        "rebound-helper",
        "base-rebound-controller",
        "base-resource-gate-auditor",
        "swap-override-receipt",
        "operational-overlay-manifest",
        "operational-handoff-receipt",
        "isolation-hotfix-handoff-receipt",
        "isolation-identity-auditor",
        "isolation-identity-module",
    ):
        values.extend((f"--{name}", f"/tmp/{name}", f"--{name}-sha256", digest))
    values.extend(
        (
            "--isolation-lease",
            "/tmp/SUPERVISOR_LEASE.json",
            "--isolation-lease-generation",
            "1",
            "--expected-handoff-old-pid",
            "2232746",
        )
    )
    values.extend(("--delegate-controller", "/tmp/delegate.py"))

    private, remaining = CONTROLLER._parse_private_args(values)

    assert private.swap_override_receipt == "/tmp/swap-override-receipt"
    assert remaining == ["--delegate-controller", "/tmp/delegate.py"]


def test_swap_controller_rejects_non_sha_identity() -> None:
    values = []
    for name in (
        "rebound-helper",
        "base-rebound-controller",
        "base-resource-gate-auditor",
        "swap-override-receipt",
        "operational-overlay-manifest",
        "operational-handoff-receipt",
        "isolation-hotfix-handoff-receipt",
        "isolation-identity-auditor",
        "isolation-identity-module",
    ):
        values.extend((f"--{name}", f"/tmp/{name}", f"--{name}-sha256", "bad"))
    values.extend(
        (
            "--isolation-lease",
            "/tmp/SUPERVISOR_LEASE.json",
            "--isolation-lease-generation",
            "1",
            "--expected-handoff-old-pid",
            "2232746",
        )
    )

    with pytest.raises(CONTROLLER.SwapOverrideControllerError, match="SHA-256"):
        CONTROLLER._parse_private_args(values)


def test_swap_controller_requires_paired_recovery_handoff_sha() -> None:
    digest = "a" * 64
    values = []
    for name in (
        "rebound-helper",
        "base-rebound-controller",
        "base-resource-gate-auditor",
        "swap-override-receipt",
        "operational-overlay-manifest",
        "operational-handoff-receipt",
        "isolation-hotfix-handoff-receipt",
        "isolation-identity-auditor",
        "isolation-identity-module",
    ):
        values.extend((f"--{name}", f"/tmp/{name}", f"--{name}-sha256", digest))
    values.extend(
        (
            "--supervisor-recovery-handoff-receipt",
            "/tmp/recovery-1.json",
            "--isolation-lease",
            "/tmp/SUPERVISOR_LEASE.json",
            "--isolation-lease-generation",
            "4",
            "--expected-handoff-old-pid",
            "2232746",
        )
    )

    with pytest.raises(CONTROLLER.SwapOverrideControllerError, match="paired SHA"):
        CONTROLLER._parse_private_args(values)


def test_auditor_environment_file_requires_exact_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "receipt.json"
    path.write_text("{}\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("TEST_PATH", str(path))
    monkeypatch.setenv("TEST_SHA", digest)

    assert AUDITOR._environment_file("TEST_PATH", "TEST_SHA") == path.resolve()
    monkeypatch.setenv("TEST_SHA", "0" * 64)
    with pytest.raises(AUDITOR.SwapOverrideGateError, match="identity mismatch"):
        AUDITOR._environment_file("TEST_PATH", "TEST_SHA")


def test_operational_handoff_is_bound_to_current_process() -> None:
    payload = {
        "schema": CONTROLLER.HANDOFF_SCHEMA,
        "overall_status": "PASS",
        "decision": CONTROLLER.HANDOFF_DECISION,
        "campaign_id": CONTROLLER.CAMPAIGN_ID,
        "queue_id": CONTROLLER.QUEUE_ID,
        "supervisor_id": CONTROLLER.SUPERVISOR_ID,
        "contract_fingerprint_sha256": CONTROLLER.CONTRACT_FINGERPRINT,
        "old_process_pid": 676436,
        "old_process_confirmed_exited": True,
        "new_process_pid": CONTROLLER.os.getpid(),
        "new_process_is_sole_authoritative_supervisor": True,
        "supervisor_count_after": 1,
        "overlap_seconds": 0,
        "new_queue_or_campaign_created": False,
        "nn_training_started": False,
    }

    assert (
        CONTROLLER._operational_handoff_exact(
            payload,
            expected_old_process_pid=676436,
            expected_new_process_pid=CONTROLLER.os.getpid(),
        )
        is True
    )
    payload["new_process_pid"] += 1
    assert (
        CONTROLLER._operational_handoff_exact(
            payload,
            expected_old_process_pid=676436,
            expected_new_process_pid=CONTROLLER.os.getpid(),
        )
        is False
    )


def test_swap_snapshot_writer_is_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    CONTROLLER._write_json_exclusive(path, {"pass": True})

    with pytest.raises(FileExistsError):
        CONTROLLER._write_json_exclusive(path, {"pass": False})
    assert json.loads(path.read_text()) == {"pass": True}


def test_operational_handoff_rejects_wrong_previous_process() -> None:
    payload = {
        "schema": CONTROLLER.HANDOFF_SCHEMA,
        "overall_status": "PASS",
        "decision": CONTROLLER.HANDOFF_DECISION,
        "campaign_id": CONTROLLER.CAMPAIGN_ID,
        "queue_id": CONTROLLER.QUEUE_ID,
        "supervisor_id": CONTROLLER.SUPERVISOR_ID,
        "contract_fingerprint_sha256": CONTROLLER.CONTRACT_FINGERPRINT,
        "old_process_pid": 2232746,
        "old_process_confirmed_exited": True,
        "new_process_pid": CONTROLLER.os.getpid(),
        "new_process_is_sole_authoritative_supervisor": True,
        "supervisor_count_after": 1,
        "overlap_seconds": 0,
        "new_queue_or_campaign_created": False,
        "nn_training_started": False,
    }

    assert CONTROLLER._operational_handoff_exact(
        payload,
        expected_old_process_pid=2232746,
        expected_new_process_pid=CONTROLLER.os.getpid(),
    )
    assert not CONTROLLER._operational_handoff_exact(
        payload,
        expected_old_process_pid=676436,
        expected_new_process_pid=CONTROLLER.os.getpid(),
    )


def test_hotfix_handoff_requires_live_start_bound_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = {
        "pid": CONTROLLER.os.getpid(),
        "parent_pid": 1,
        "uid": 1001,
        "state": "S",
        "start_ticks": 12345,
        "boot_id": "boot-id",
        "command_line_sha256": "a" * 64,
        "executable_path": "/usr/bin/python3",
        "executable_sha256": "b" * 64,
    }
    old_process = {**process, "pid": 2232746, "start_ticks": 12344}
    payload = {
        "schema": CONTROLLER.HANDOFF_SCHEMA,
        "overall_status": "PASS",
        "decision": CONTROLLER.HANDOFF_DECISION,
        "campaign_id": CONTROLLER.CAMPAIGN_ID,
        "queue_id": CONTROLLER.QUEUE_ID,
        "supervisor_id": CONTROLLER.SUPERVISOR_ID,
        "contract_fingerprint_sha256": CONTROLLER.CONTRACT_FINGERPRINT,
        "old_process_pid": 2232746,
        "old_process_identity": old_process,
        "old_process_confirmed_exited": True,
        "new_process_pid": CONTROLLER.os.getpid(),
        "new_process_identity": deepcopy(process),
        "new_process_is_sole_authoritative_supervisor": True,
        "supervisor_count_after": 1,
        "overlap_seconds": 0,
        "new_queue_or_campaign_created": False,
        "nn_training_started": False,
        "handoff_scope": (
            "ISOLATION_GATE_AUTHORIZED_SUPERVISOR_ANCESTOR_IDENTITY_FIX"
        ),
    }
    monkeypatch.setattr(
        CONTROLLER.isolation_identity,
        "read_process_identity",
        lambda _pid: deepcopy(process),
    )

    assert CONTROLLER._operational_handoff_exact(
        payload,
        expected_old_process_pid=2232746,
        expected_new_process_pid=CONTROLLER.os.getpid(),
        require_process_identities=True,
    )
    payload["new_process_identity"]["start_ticks"] += 1
    assert not CONTROLLER._operational_handoff_exact(
        payload,
        expected_old_process_pid=2232746,
        expected_new_process_pid=CONTROLLER.os.getpid(),
        require_process_identities=True,
    )


def test_handoff_chain_validates_all_recovery_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_pid = CONTROLLER.os.getpid()

    def process(pid: int, ticks: int) -> dict:
        return {
            "pid": pid,
            "parent_pid": 1,
            "uid": 1001,
            "state": "S",
            "start_ticks": ticks,
            "boot_id": "boot-id",
            "command_line_sha256": f"{ticks:064x}"[-64:],
            "executable_path": "/usr/bin/python3",
            "executable_sha256": "b" * 64,
        }

    def handoff(old_pid: int, new_pid: int, *, recovery: bool) -> dict:
        payload = {
            "schema": CONTROLLER.HANDOFF_SCHEMA,
            "overall_status": "PASS",
            "decision": CONTROLLER.HANDOFF_DECISION,
            "campaign_id": CONTROLLER.CAMPAIGN_ID,
            "queue_id": CONTROLLER.QUEUE_ID,
            "supervisor_id": CONTROLLER.SUPERVISOR_ID,
            "contract_fingerprint_sha256": CONTROLLER.CONTRACT_FINGERPRINT,
            "old_process_pid": old_pid,
            "old_process_confirmed_exited": True,
            "new_process_pid": new_pid,
            "new_process_is_sole_authoritative_supervisor": True,
            "supervisor_count_after": 1,
            "overlap_seconds": 0,
            "new_queue_or_campaign_created": False,
            "nn_training_started": False,
        }
        if old_pid == 676436:
            return payload
        payload.update(
            {
                "old_process_identity": process(old_pid, old_pid),
                "new_process_identity": process(new_pid, new_pid),
                "handoff_scope": (
                    "ISOLATION_GATE_AUTHORIZED_SUPERVISOR_ANCESTOR_IDENTITY_FIX"
                ),
            }
        )
        if recovery:
            payload["recovery_scope"] = CONTROLLER.RECOVERY_SCOPE
        return payload

    operational = handoff(676436, 2232746, recovery=False)
    hotfix = handoff(2232746, 526588, recovery=False)
    recovery_1 = handoff(526588, 510227, recovery=True)
    recovery_2 = handoff(510227, current_pid, recovery=True)
    monkeypatch.setattr(
        CONTROLLER.isolation_identity,
        "read_process_identity",
        lambda pid: process(pid, pid) if pid == current_pid else None,
    )

    assert CONTROLLER._validate_handoff_chain(
        operational_handoff=operational,
        hotfix_handoff=hotfix,
        recovery_handoffs=[recovery_1, recovery_2],
        expected_hotfix_old_pid=2232746,
        current_pid=current_pid,
    ) == 676436

    broken = deepcopy(recovery_1)
    broken["new_process_pid"] = 510228
    with pytest.raises(
        CONTROLLER.SwapOverrideControllerError,
        match="recovery handoff receipt 1 mismatch",
    ):
        CONTROLLER._validate_handoff_chain(
            operational_handoff=operational,
            hotfix_handoff=hotfix,
            recovery_handoffs=[broken, recovery_2],
            expected_hotfix_old_pid=2232746,
            current_pid=current_pid,
        )
