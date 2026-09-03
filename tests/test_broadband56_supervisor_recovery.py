from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import (
    broadband56_isolation_identity as identity,
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = _load(
    ROOT / "scripts" / "launch_broadband56_v2_supervisor_recovery.py",
    "broadband56_supervisor_recovery",
)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _record(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_recovery_approval_requires_exact_candidate_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = []
    for index in range(17):
        path = tmp_path / f"evidence-{index}.json"
        path.write_text("{}\n", encoding="utf-8")
        evidence.append(path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    candidate_path = tmp_path / "candidate.json"
    candidate = {
        "schema": RECOVERY.RECOVERY_CANDIDATE_SCHEMA,
        "generated_utc": now.isoformat(),
        "overall_status": "PENDING_EXACT_SHA_PROJECT_OWNER_APPROVAL",
        "decision": "STOP_AT_EXACT_SHA_APPROVAL_BOUNDARY",
        "authorization_scope": RECOVERY.RECOVERY_SCOPE,
        "campaign_id": RECOVERY.CAMPAIGN_ID,
        "queue_id": RECOVERY.QUEUE_ID,
        "logical_supervisor_id": RECOVERY.SUPERVISOR_ID,
        "scientific_contract_changed": False,
        "simulator_contract_changed": False,
        "automatic_command_authorized": False,
        "execution_authorized": False,
    }
    keys = (
        "runtime_manifest",
        "backend_overlay_manifest",
        "base_hotfix_candidate",
        "base_hotfix_approval",
        "approved_hotfix_runtime_manifest",
        "approved_hotfix_backend_manifest",
        "prior_hotfix_handoff",
        "prior_supervisor_lease",
        "failed_operation_status",
        "failed_operation_log",
        "stage_parent_repair_receipt",
        "post_rebind_execution_gate",
        "prior_recovery_candidate",
        "prior_recovery_approval",
        "prior_recovery_binding",
        "recovery_chain_failure_receipt",
    )
    assert len(keys) + 1 == len(evidence)
    candidate.update({key: _record(path) for key, path in zip(keys, evidence)})
    candidate["prior_recovery_handoffs"] = [_record(evidence[-1])]
    candidate["recovery_generation"] = 4
    candidate["failure_classification"] = (
        "FOURTH_HANDOFF_NOT_PROPAGATED_TO_BASE_REBOUND_VALIDATOR"
    )
    monkeypatch.setattr(
        RECOVERY,
        "APPROVED_HOTFIX_RUNTIME_SHA256",
        candidate["approved_hotfix_runtime_manifest"]["sha256"],
    )
    monkeypatch.setattr(
        RECOVERY,
        "APPROVED_HOTFIX_BACKEND_SHA256",
        candidate["approved_hotfix_backend_manifest"]["sha256"],
    )
    _write_json(candidate_path, candidate)
    candidate_sha = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    approval_path = tmp_path / "approval.json"
    approval = {
        "schema": RECOVERY.RECOVERY_APPROVAL_SCHEMA,
        "approved_utc": (now + timedelta(seconds=1)).isoformat(),
        "overall_status": "PASS",
        "decision": RECOVERY.RECOVERY_APPROVAL_DECISION,
        "authorization_scope": RECOVERY.RECOVERY_SCOPE,
        "campaign_id": RECOVERY.CAMPAIGN_ID,
        "queue_id": RECOVERY.QUEUE_ID,
        "logical_supervisor_id": RECOVERY.SUPERVISOR_ID,
        "approved_candidate": _record(candidate_path),
        "approved_by": "Yufeng Wang, project owner and project leader",
        "approval_reference": f"exact candidate SHA-256 {candidate_sha}",
        "one_supervisor_recovery_authorized": True,
        "continue_existing_full_campaign_authorized": True,
        "new_queue_or_campaign_authorized": False,
        "scientific_contract_change_authorized": False,
        "simulator_contract_change_authorized": False,
        "nn_training_authorized": False,
        "simulator_action_taken": False,
    }
    _write_json(approval_path, approval)
    approval_sha = hashlib.sha256(approval_path.read_bytes()).hexdigest()

    parsed_candidate, parsed_approval = RECOVERY.verify_recovery_approval(
        candidate_path,
        candidate_sha,
        approval_path,
        approval_sha,
    )

    assert parsed_candidate["authorization_scope"] == RECOVERY.RECOVERY_SCOPE
    assert parsed_approval["overall_status"] == "PASS"
    approval["one_supervisor_recovery_authorized"] = False
    _write_json(approval_path, approval)
    approval_sha = hashlib.sha256(approval_path.read_bytes()).hexdigest()
    with pytest.raises(RECOVERY.RecoveryError, match="not exact PASS"):
        RECOVERY.verify_recovery_approval(
            candidate_path,
            candidate_sha,
            approval_path,
            approval_sha,
        )


def test_recovery_process_count_detects_duplicate_supervisor() -> None:
    command = (
        "python launch_broadband56_v2_supervisor_recovery.py "
        f"--campaign-root /tmp/{RECOVERY.CAMPAIGN_ID}"
    )
    result = RECOVERY._project_counts(
        identity,
        [
            {"pid": 10, "command_text": command},
            {"pid": 11, "command_text": command},
        ],
    )

    assert result["supervisor_pids"] == [10, 11]
    assert result["runner_pids"] == []
    assert result["active_simulator_jobs"] == 0


def test_recorded_failure_accepts_exact_immutable_log_without_wrapper_text() -> None:
    prior_pid = 510227
    RECOVERY.verify_recorded_supervisor_failure(
        prior_pid=prior_pid,
        recovery_generation=4,
        prior_lease_payload={"lease_generation": 3},
        failed_status={
            "status": "BLOCKED",
            "physical_supervisor_pid": prior_pid,
            "logical_supervisor_id": RECOVERY.SUPERVISOR_ID,
            "queue_id": RECOVERY.QUEUE_ID,
            "simulator_action_taken": False,
            "blocker": "supervisor handoff receipt mismatch",
        },
        failure_log=(
            "overall_status=BLOCKED\n"
            "error=supervisor handoff receipt mismatch\n"
        ),
    )


def test_recorded_failure_rejects_log_without_handoff_mismatch() -> None:
    prior_pid = 510227
    with pytest.raises(RECOVERY.RecoveryError, match="failure evidence mismatch"):
        RECOVERY.verify_recorded_supervisor_failure(
            prior_pid=prior_pid,
            recovery_generation=4,
            prior_lease_payload={"lease_generation": 3},
            failed_status={
                "status": "BLOCKED",
                "physical_supervisor_pid": prior_pid,
                "logical_supervisor_id": RECOVERY.SUPERVISOR_ID,
                "queue_id": RECOVERY.QUEUE_ID,
                "simulator_action_taken": False,
                "blocker": "supervisor handoff receipt mismatch",
            },
            failure_log="overall_status=BLOCKED\nerror=some other failure\n",
        )


def test_recovery_controller_argv_binds_immediate_predecessor(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_text("bound\n", encoding="utf-8")
    record = _record(artifact)
    campaign_root = tmp_path / RECOVERY.CAMPAIGN_ID
    campaign_root.mkdir()
    binding_keys = (
        "post_rebind_execution_gate",
        "rebound_helper",
        "base_rebound_controller",
        "base_resource_gate_auditor",
        "swap_override_receipt",
        "delegate_controller",
        "old_full_campaign_receipt",
        "old_backend_manifest",
        "corrected_approval_receipt",
        "queue_rebind_receipt",
        "inner_supervisor_handoff",
        "frozen_contract",
        "preparation_receipt",
        "policy_approval_receipt",
        "full_campaign_candidate",
        "composite_full_campaign_receipt",
        "production_backend_manifest",
        "backend_verification_receipt",
        "stage_launcher",
        "resource_probe",
        "private_python",
        "campaign_lock",
        "previous_operational_handoff",
    )
    bindings = {key: record for key in binding_keys}
    bindings["campaign_root"] = str(campaign_root)
    runtime = {
        "isolation_identity_auditor": record,
        "isolation_identity_module": record,
        "resource_gate_auditor": record,
    }
    operational_handoff = tmp_path / "operational-handoff.json"
    hotfix_handoff = tmp_path / "hotfix-handoff.json"
    recovery_1 = tmp_path / "recovery-1.json"
    recovery_2 = tmp_path / "recovery-2.json"
    lease = tmp_path / "lease.json"
    overlay = tmp_path / "overlay.json"
    for path in (
        operational_handoff,
        hotfix_handoff,
        recovery_1,
        recovery_2,
        lease,
        overlay,
    ):
        path.write_text("{}\n", encoding="utf-8")

    argv = RECOVERY._controller_argv(
        bindings=bindings,
        runtime=runtime,
        overlay=overlay,
        operational_handoff=operational_handoff,
        hotfix_handoff=hotfix_handoff,
        recovery_handoffs=[recovery_1, recovery_2],
        lease=lease,
        hotfix_old_pid=2232746,
        lease_generation=4,
    )

    assert argv[argv.index("--expected-handoff-old-pid") + 1] == "2232746"
    assert argv[argv.index("--isolation-lease-generation") + 1] == "4"
    assert argv[argv.index("--operational-handoff-receipt") + 1] == str(
        operational_handoff
    )
    assert argv[argv.index("--isolation-hotfix-handoff-receipt") + 1] == str(
        hotfix_handoff
    )
    recovery_indices = [
        index
        for index, value in enumerate(argv)
        if value == "--supervisor-recovery-handoff-receipt"
    ]
    assert [argv[index + 1] for index in recovery_indices] == [
        str(recovery_1),
        str(recovery_2),
    ]
