from __future__ import annotations

import hashlib
import importlib.util
import json
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
    ):
        values.extend((f"--{name}", f"/tmp/{name}", f"--{name}-sha256", digest))
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
    ):
        values.extend((f"--{name}", f"/tmp/{name}", f"--{name}-sha256", "bad"))

    with pytest.raises(CONTROLLER.SwapOverrideControllerError, match="SHA-256"):
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

    assert CONTROLLER._operational_handoff_exact(payload) is True
    payload["new_process_pid"] += 1
    assert CONTROLLER._operational_handoff_exact(payload) is False


def test_swap_snapshot_writer_is_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    CONTROLLER._write_json_exclusive(path, {"pass": True})

    with pytest.raises(FileExistsError):
        CONTROLLER._write_json_exclusive(path, {"pass": False})
    assert json.loads(path.read_text()) == {"pass": True}
