#!/usr/bin/env python3
"""Recover the authorized Broadband56 supervisor after a recorded failure."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
FINGERPRINT = (
    "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
)
APPROVED_HOTFIX_RUNTIME_SHA256 = (
    "a48c97087f6853c70fc360f8db9111fafb1dc34e89b7bae2457b267946c51027"
)
APPROVED_HOTFIX_BACKEND_SHA256 = (
    "3f7c62d50c7bb707e1df55503a0950e180749cc7d09bf436dbfb453fcec2826c"
)
HOTFIX_SCOPE = "ISOLATION_GATE_AUTHORIZED_SUPERVISOR_ANCESTOR_IDENTITY_FIX"
RECOVERY_SCOPE = "SUPERVISOR_RECOVERY_AFTER_STAGE_PARENT_INITIALIZATION_FAILURE"
RECOVERY_CANDIDATE_SCHEMA = (
    "rfic_transformer.broadband56_v2_supervisor_recovery_candidate.v2"
)
RECOVERY_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_v2_supervisor_recovery_authorization.v2"
)
RECOVERY_APPROVAL_DECISION = "APPROVE_" + RECOVERY_SCOPE
RECOVERY_RUNTIME_SCHEMA = (
    "rfic_transformer.broadband56_v2_supervisor_recovery_runtime.v2"
)
RECOVERY_BACKEND_OVERLAY_SCHEMA = (
    "rfic_transformer.broadband56_v2_supervisor_recovery_backend_overlay.v2"
)
HANDOFF_SCHEMA = "rfic_transformer.broadband56_v2_swap_policy_supervisor_handoff.v1"
HANDOFF_DECISION = "HANDOFF_SAME_LOGICAL_SUPERVISOR_FOR_SWAP_POLICY_OVERLAY"
OVERLAY_SCHEMA = "rfic_transformer.broadband56_v2_operational_policy_overlay.v1"


class RecoveryError(RuntimeError):
    """Fail-closed supervisor recovery error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RecoveryError(f"{label} root is not an object")
    return value


def write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RecoveryError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bound_path(record: Any, label: str) -> Path:
    if not isinstance(record, Mapping):
        raise RecoveryError(f"{label} lacks a file identity")
    path = Path(str(record.get("path") or "")).expanduser().resolve()
    if not (
        path.is_file()
        and path.stat().st_size == record.get("size_bytes")
        and sha256(path) == record.get("sha256")
    ):
        raise RecoveryError(f"{label} file identity mismatch")
    return path


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def verify_recorded_supervisor_failure(
    *,
    prior_pid: int,
    recovery_generation: int,
    prior_lease_payload: Mapping[str, Any],
    failed_status: Mapping[str, Any],
    failure_log: str,
) -> None:
    """Verify the immutable failed run without requiring nonexistent wrapper text."""
    if not (
        prior_pid > 0
        and prior_lease_payload.get("lease_generation")
        == recovery_generation - 1
        and failed_status.get("status") == "BLOCKED"
        and failed_status.get("physical_supervisor_pid") == prior_pid
        and failed_status.get("logical_supervisor_id") == SUPERVISOR_ID
        and failed_status.get("queue_id") == QUEUE_ID
        and failed_status.get("simulator_action_taken") is False
        and failed_status.get("blocker")
        == "supervisor handoff receipt mismatch"
        and "supervisor handoff receipt mismatch" in failure_log
    ):
        raise RecoveryError("recorded supervisor failure evidence mismatch")


def verify_recovery_approval(
    candidate_path: Path,
    candidate_sha: str,
    approval_path: Path,
    approval_sha: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if sha256(candidate_path) != candidate_sha:
        raise RecoveryError("recovery candidate SHA-256 mismatch")
    if sha256(approval_path) != approval_sha:
        raise RecoveryError("recovery approval SHA-256 mismatch")
    candidate = read_json(candidate_path, "recovery candidate")
    approval = read_json(approval_path, "recovery approval")
    approved_utc = parse_utc(approval.get("approved_utc"))
    generated_utc = parse_utc(candidate.get("generated_utc"))
    if not (
        candidate.get("schema") == RECOVERY_CANDIDATE_SCHEMA
        and candidate.get("overall_status")
        == "PENDING_EXACT_SHA_PROJECT_OWNER_APPROVAL"
        and candidate.get("decision") == "STOP_AT_EXACT_SHA_APPROVAL_BOUNDARY"
        and candidate.get("authorization_scope") == RECOVERY_SCOPE
        and candidate.get("campaign_id") == CAMPAIGN_ID
        and candidate.get("queue_id") == QUEUE_ID
        and candidate.get("logical_supervisor_id") == SUPERVISOR_ID
        and candidate.get("scientific_contract_changed") is False
        and candidate.get("simulator_contract_changed") is False
        and candidate.get("automatic_command_authorized") is False
        and candidate.get("execution_authorized") is False
    ):
        raise RecoveryError("recovery candidate contract mismatch")
    if not (
        approval.get("schema") == RECOVERY_APPROVAL_SCHEMA
        and approval.get("overall_status") == "PASS"
        and approval.get("decision") == RECOVERY_APPROVAL_DECISION
        and approval.get("authorization_scope") == RECOVERY_SCOPE
        and approval.get("campaign_id") == CAMPAIGN_ID
        and approval.get("queue_id") == QUEUE_ID
        and approval.get("logical_supervisor_id") == SUPERVISOR_ID
        and approval.get("approved_candidate") == file_record(candidate_path)
        and approval.get("approved_by")
        == "Yufeng Wang, project owner and project leader"
        and approved_utc is not None
        and generated_utc is not None
        and approved_utc >= generated_utc
        and candidate_sha in str(approval.get("approval_reference") or "")
        and approval.get("one_supervisor_recovery_authorized") is True
        and approval.get("continue_existing_full_campaign_authorized") is True
        and approval.get("new_queue_or_campaign_authorized") is False
        and approval.get("scientific_contract_change_authorized") is False
        and approval.get("simulator_contract_change_authorized") is False
        and approval.get("nn_training_authorized") is False
        and approval.get("simulator_action_taken") is False
    ):
        raise RecoveryError("recovery approval is not exact PASS")
    for key, label in (
        ("runtime_manifest", "recovery runtime manifest"),
        ("backend_overlay_manifest", "recovery backend overlay"),
        ("base_hotfix_candidate", "base hotfix candidate"),
        ("base_hotfix_approval", "base hotfix approval"),
        ("approved_hotfix_runtime_manifest", "approved hotfix runtime manifest"),
        ("approved_hotfix_backend_manifest", "approved hotfix backend manifest"),
        ("prior_hotfix_handoff", "prior hotfix handoff"),
        ("prior_supervisor_lease", "prior supervisor lease"),
        ("failed_operation_status", "failed operation status"),
        ("failed_operation_log", "failed operation log"),
        ("stage_parent_repair_receipt", "stage parent repair receipt"),
        ("post_rebind_execution_gate", "post-rebind execution gate"),
        ("prior_recovery_candidate", "prior recovery candidate"),
        ("prior_recovery_approval", "prior recovery approval"),
        ("prior_recovery_binding", "prior recovery binding"),
        ("recovery_chain_failure_receipt", "recovery chain failure receipt"),
    ):
        bound_path(candidate.get(key), label)
    handoffs = candidate.get("prior_recovery_handoffs")
    if not isinstance(handoffs, list) or not handoffs:
        raise RecoveryError("recovery candidate lacks prior recovery handoff chain")
    for index, record in enumerate(handoffs, start=1):
        bound_path(record, f"prior recovery handoff {index}")
    if not (
        candidate.get("recovery_generation") == len(handoffs) + 3
        and candidate.get("failure_classification")
        == "FOURTH_HANDOFF_NOT_PROPAGATED_TO_BASE_REBOUND_VALIDATOR"
        and candidate.get("approved_hotfix_runtime_manifest", {}).get("sha256")
        == APPROVED_HOTFIX_RUNTIME_SHA256
        and candidate.get("approved_hotfix_backend_manifest", {}).get("sha256")
        == APPROVED_HOTFIX_BACKEND_SHA256
    ):
        raise RecoveryError("recovery generation or failure classification mismatch")
    return candidate, approval


def _project_counts(module: Any, processes: list[Mapping[str, Any]]) -> dict[str, Any]:
    supervisors: list[int] = []
    runners: list[int] = []
    simulators: list[int] = []
    for item in processes:
        command = str(item.get("command_text", ""))
        if not module._campaign_process(command):
            continue
        pid = int(item["pid"])
        if module._contains_marker(command, module.SUPERVISOR_MARKERS):
            supervisors.append(pid)
        elif module._contains_marker(command, module.RUNNER_MARKERS):
            runners.append(pid)
        if any(
            module._contains_marker(command, markers)
            for markers in (
                module.CADENCE_MARKERS,
                module.CALIBRE_MARKERS,
                module.EMX_MARKERS,
            )
        ):
            simulators.append(pid)
    return {
        "supervisor_pids": sorted(supervisors),
        "runner_pids": sorted(runners),
        "simulator_pids": sorted(simulators),
        "active_simulator_jobs": len(simulators),
    }


def _wait_for_detached_parent() -> None:
    parent = os.getppid()
    if parent <= 1:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if os.getppid() == 1 or not Path(f"/proc/{parent}").exists():
            return
        time.sleep(0.05)
    raise RecoveryError("launcher parent did not detach")


def _build_overlay(
    *,
    path: Path,
    bindings: Mapping[str, Any],
    runtime: Mapping[str, Any],
    operational_handoff: Path,
    hotfix_handoff: Path,
    recovery_handoffs: list[Path],
    failure_receipt: Path,
) -> None:
    write_json_new(
        path,
        {
            "schema": OVERLAY_SCHEMA,
            "generated_utc": utc_now(),
            "overall_status": "PASS",
            "decision": "BIND_OPERATIONAL_SWAP_POLICY_OVERLAY",
            "campaign_id": CAMPAIGN_ID,
            "queue_id": QUEUE_ID,
            "supervisor_id": SUPERVISOR_ID,
            "contract_fingerprint_sha256": FINGERPRINT,
            "swap_policy": "COMBINED_RESOURCE_DEGRADATION_ONLY",
            "new_backend_created": False,
            "new_queue_or_campaign_created": False,
            "scientific_contract_changed": False,
            "nn_training_authorized": False,
            "override_receipt": file_record(
                bound_path(bindings["swap_override_receipt"], "swap override")
            ),
            "previous_operational_handoff": file_record(operational_handoff),
            "isolation_hotfix_handoff": file_record(hotfix_handoff),
            "supervisor_recovery_handoffs": [
                file_record(item) for item in recovery_handoffs
            ],
            "corrected_backend_manifest": file_record(
                bound_path(
                    bindings["production_backend_manifest"],
                    "production backend",
                )
            ),
            "script_identities": {
                "queue_controller": file_record(
                    bound_path(runtime["queue_controller"], "queue controller")
                ),
                "rebound_helper": file_record(
                    bound_path(bindings["rebound_helper"], "rebound helper")
                ),
                "base_rebound_controller": file_record(
                    bound_path(
                        bindings["base_rebound_controller"],
                        "base rebound controller",
                    )
                ),
                "resource_gate_auditor": file_record(
                    bound_path(
                        runtime["resource_gate_auditor"],
                        "resource gate auditor",
                    )
                ),
                "base_resource_gate_auditor": file_record(
                    bound_path(
                        bindings["base_resource_gate_auditor"],
                        "base resource auditor",
                    )
                ),
                "isolation_identity_auditor": file_record(
                    bound_path(
                        runtime["isolation_identity_auditor"],
                        "isolation identity auditor",
                    )
                ),
                "isolation_identity_module": file_record(
                    bound_path(
                        runtime["isolation_identity_module"],
                        "isolation identity module",
                    )
                ),
            },
            "policy_module": file_record(
                bound_path(runtime["swap_policy_module"], "swap policy module")
            ),
            "hotfix_scope": HOTFIX_SCOPE,
            "recovery_scope": RECOVERY_SCOPE,
            "failure_receipt": file_record(failure_receipt),
            "simulator_action_taken": False,
            "campaign_data_modified": False,
        },
    )


def _controller_argv(
    *,
    bindings: Mapping[str, Any],
    runtime: Mapping[str, Any],
    overlay: Path,
    operational_handoff: Path,
    hotfix_handoff: Path,
    recovery_handoffs: list[Path],
    lease: Path,
    hotfix_old_pid: int,
    lease_generation: int,
) -> list[str]:
    def p(source: Mapping[str, Any], key: str, label: str) -> Path:
        return bound_path(source[key], label)

    post_gate = p(bindings, "post_rebind_execution_gate", "post-rebind gate")
    return [
        "--rebound-helper", str(p(bindings, "rebound_helper", "rebound helper")),
        "--rebound-helper-sha256", sha256(p(bindings, "rebound_helper", "rebound helper")),
        "--base-rebound-controller", str(p(bindings, "base_rebound_controller", "base rebound controller")),
        "--base-rebound-controller-sha256", sha256(p(bindings, "base_rebound_controller", "base rebound controller")),
        "--base-resource-gate-auditor", str(p(bindings, "base_resource_gate_auditor", "base resource auditor")),
        "--base-resource-gate-auditor-sha256", sha256(p(bindings, "base_resource_gate_auditor", "base resource auditor")),
        "--swap-override-receipt", str(p(bindings, "swap_override_receipt", "swap override")),
        "--swap-override-receipt-sha256", sha256(p(bindings, "swap_override_receipt", "swap override")),
        "--operational-overlay-manifest", str(overlay),
        "--operational-overlay-manifest-sha256", sha256(overlay),
        "--operational-handoff-receipt", str(operational_handoff),
        "--operational-handoff-receipt-sha256", sha256(operational_handoff),
        "--isolation-hotfix-handoff-receipt", str(hotfix_handoff),
        "--isolation-hotfix-handoff-receipt-sha256", sha256(hotfix_handoff),
        "--isolation-identity-auditor", str(p(runtime, "isolation_identity_auditor", "isolation auditor")),
        "--isolation-identity-auditor-sha256", sha256(p(runtime, "isolation_identity_auditor", "isolation auditor")),
        "--isolation-identity-module", str(p(runtime, "isolation_identity_module", "isolation module")),
        "--isolation-identity-module-sha256", sha256(p(runtime, "isolation_identity_module", "isolation module")),
        "--isolation-lease", str(lease),
        "--isolation-lease-generation", str(lease_generation),
        "--expected-handoff-old-pid", str(hotfix_old_pid),
        "--delegate-controller", str(p(bindings, "delegate_controller", "delegate controller")),
        "--delegate-controller-sha256", sha256(p(bindings, "delegate_controller", "delegate controller")),
        "--old-full-campaign-receipt", str(p(bindings, "old_full_campaign_receipt", "old FULL receipt")),
        "--old-full-campaign-receipt-sha256", sha256(p(bindings, "old_full_campaign_receipt", "old FULL receipt")),
        "--old-backend-manifest", str(p(bindings, "old_backend_manifest", "old backend")),
        "--old-backend-manifest-sha256", sha256(p(bindings, "old_backend_manifest", "old backend")),
        "--corrected-approval-receipt", str(p(bindings, "corrected_approval_receipt", "corrected approval")),
        "--corrected-approval-receipt-sha256", sha256(p(bindings, "corrected_approval_receipt", "corrected approval")),
        "--queue-rebind-receipt", str(p(bindings, "queue_rebind_receipt", "queue rebind receipt")),
        "--queue-rebind-receipt-sha256", sha256(p(bindings, "queue_rebind_receipt", "queue rebind receipt")),
        "--supervisor-handoff-receipt", str(p(bindings, "inner_supervisor_handoff", "inner supervisor handoff")),
        "--supervisor-handoff-receipt-sha256", sha256(p(bindings, "inner_supervisor_handoff", "inner supervisor handoff")),
        "--post-rebind-execution-gate", str(post_gate),
        "--post-rebind-execution-gate-sha256", sha256(post_gate),
        "--frozen-contract", str(p(bindings, "frozen_contract", "frozen contract")),
        "--preparation-receipt", str(p(bindings, "preparation_receipt", "preparation receipt")),
        "--policy-approval-receipt", str(p(bindings, "policy_approval_receipt", "policy approval receipt")),
        "--full-campaign-candidate", str(p(bindings, "full_campaign_candidate", "FULL candidate")),
        "--full-campaign-candidate-sha256", sha256(p(bindings, "full_campaign_candidate", "FULL candidate")),
        "--full-campaign-receipt", str(p(bindings, "composite_full_campaign_receipt", "composite FULL receipt")),
        "--backend-identity-manifest", str(p(bindings, "production_backend_manifest", "production backend")),
        "--backend-identity-verification-receipt", str(p(bindings, "backend_verification_receipt", "backend verification")),
        "--stage-launcher", str(p(bindings, "stage_launcher", "stage launcher")),
        "--probe-script", str(p(bindings, "resource_probe", "resource probe")),
        "--resource-gate-auditor", str(p(runtime, "resource_gate_auditor", "resource gate auditor")),
        "--python-bin", str(p(bindings, "private_python", "private Python")),
        "--campaign-root", str(Path(str(bindings["campaign_root"])).resolve()),
        "--lock-path", str(p(bindings, "campaign_lock", "campaign lock")),
        "--poll-seconds", "60",
        "--max-checks", "0",
        "--max-age-seconds", "300",
        "--resume",
    ] + [
        item
        for handoff in recovery_handoffs
        for item in (
            "--supervisor-recovery-handoff-receipt",
            str(handoff),
            "--supervisor-recovery-handoff-receipt-sha256",
            sha256(handoff),
        )
    ]


def run(args: argparse.Namespace) -> None:
    _wait_for_detached_parent()
    candidate_path = Path(args.recovery_candidate).expanduser().resolve()
    approval_path = Path(args.recovery_approval).expanduser().resolve()
    operation_root = Path(args.operation_root).expanduser().resolve()
    if operation_root.exists():
        raise RecoveryError(f"no-clobber operation root exists: {operation_root}")
    candidate, approval = verify_recovery_approval(
        candidate_path,
        args.recovery_candidate_sha256,
        approval_path,
        args.recovery_approval_sha256,
    )
    runtime_manifest_path = bound_path(candidate["runtime_manifest"], "runtime manifest")
    runtime_manifest = read_json(runtime_manifest_path, "runtime manifest")
    runtime = runtime_manifest.get("runtime")
    focused_tests = runtime_manifest.get("focused_tests")
    if not (
        runtime_manifest.get("schema") == RECOVERY_RUNTIME_SCHEMA
        and runtime_manifest.get("overall_status") == "PASS"
        and runtime_manifest.get("campaign_id") == CAMPAIGN_ID
        and runtime_manifest.get("queue_id") == QUEUE_ID
        and runtime_manifest.get("logical_supervisor_id") == SUPERVISOR_ID
        and runtime_manifest.get("new_queue_or_campaign_created") is False
        and runtime_manifest.get("scientific_contract_changed") is False
        and runtime_manifest.get("simulator_contract_changed") is False
        and runtime_manifest.get("simulator_action_taken") is False
        and runtime_manifest.get("nn_training_started") is False
        and isinstance(focused_tests, Mapping)
        and focused_tests.get("result") == "PASS"
        and focused_tests.get("passed") == 59
        and focused_tests.get("failed") == 0
        and isinstance(runtime, Mapping)
        and runtime.get("head_commit") == candidate.get("patch_commit")
    ):
        raise RecoveryError("runtime manifest contract mismatch")
    backend_overlay_path = bound_path(
        candidate["backend_overlay_manifest"], "recovery backend overlay"
    )
    backend_overlay = read_json(backend_overlay_path, "recovery backend overlay")
    if not (
        backend_overlay.get("schema") == RECOVERY_BACKEND_OVERLAY_SCHEMA
        and backend_overlay.get("overall_status") == "PASS"
        and backend_overlay.get("campaign_id") == CAMPAIGN_ID
        and backend_overlay.get("queue_id") == QUEUE_ID
        and backend_overlay.get("logical_supervisor_id") == SUPERVISOR_ID
        and backend_overlay.get("contract_fingerprint_sha256") == FINGERPRINT
        and backend_overlay.get("recovery_runtime_manifest")
        == file_record(runtime_manifest_path)
        and backend_overlay.get("approved_hotfix_runtime_manifest")
        == candidate["approved_hotfix_runtime_manifest"]
        and backend_overlay.get("approved_hotfix_backend_manifest")
        == candidate["approved_hotfix_backend_manifest"]
        and backend_overlay.get("prior_supervisor_lease")
        == candidate["prior_supervisor_lease"]
        and backend_overlay.get("recovery_chain_failure_receipt")
        == candidate["recovery_chain_failure_receipt"]
        and backend_overlay.get("backend_content_changed") is False
        and backend_overlay.get("queue_binding_changed") is False
        and backend_overlay.get("new_queue_or_campaign_created") is False
        and backend_overlay.get("scientific_contract_changed") is False
        and backend_overlay.get("simulator_contract_changed") is False
        and backend_overlay.get("simulator_action_taken") is False
        and backend_overlay.get("nn_training_started") is False
    ):
        raise RecoveryError("recovery backend overlay contract mismatch")
    if file_record(Path(__file__)) != runtime.get("recovery_launcher"):
        raise RecoveryError("recovery launcher identity mismatch")
    runtime_root = Path(str(runtime.get("runtime_root") or "")).resolve()
    sys.path[:] = [item for item in sys.path if item != str(runtime_root)]
    sys.path.insert(0, str(runtime_root))
    module = load_module(
        bound_path(runtime["isolation_identity_module"], "isolation module"),
        "b56_recovery_identity",
    )
    base_candidate_path = bound_path(
        candidate["base_hotfix_candidate"], "base hotfix candidate"
    )
    base_approval_path = bound_path(
        candidate["base_hotfix_approval"], "base hotfix approval"
    )
    base_candidate = read_json(base_candidate_path, "base hotfix candidate")
    base_bootstrap = load_module(
        bound_path(
            base_candidate["hotfix_runtime"]["handoff_bootstrap"],
            "base hotfix bootstrap",
        ),
        "b56_recovery_base_bootstrap",
    )
    base_candidate, _base_approval = base_bootstrap.verify_candidate_and_approval(
        base_candidate_path,
        candidate["base_hotfix_candidate"]["sha256"],
        base_approval_path,
        candidate["base_hotfix_approval"]["sha256"],
    )
    if not (
        base_candidate.get("new_runtime_manifest")
        == candidate["approved_hotfix_runtime_manifest"]
        and base_candidate.get("new_backend_manifest")
        == candidate["approved_hotfix_backend_manifest"]
    ):
        raise RecoveryError("approved hotfix runtime or backend identity mismatch")
    bindings = dict(base_candidate["evidence_bindings"])
    production_backend = bound_path(
        bindings["production_backend_manifest"], "production backend"
    )
    if backend_overlay.get("production_backend_manifest") != file_record(
        production_backend
    ):
        raise RecoveryError("recovery backend production identity mismatch")
    bindings["post_rebind_execution_gate"] = candidate[
        "post_rebind_execution_gate"
    ]
    campaign_root = Path(str(bindings["campaign_root"])).resolve()
    if campaign_root != Path(args.campaign_root).expanduser().resolve():
        raise RecoveryError("campaign root differs from recovery candidate")
    hotfix_handoff = bound_path(
        candidate["prior_hotfix_handoff"], "prior hotfix handoff"
    )
    operational_handoff = bound_path(
        bindings["previous_operational_handoff"], "previous operational handoff"
    )
    prior_recovery_handoffs = [
        bound_path(record, f"prior recovery handoff {index}")
        for index, record in enumerate(
            candidate["prior_recovery_handoffs"], start=1
        )
    ]
    prior_lease = bound_path(
        candidate["prior_supervisor_lease"], "prior supervisor lease"
    )
    prior_lease_payload = read_json(prior_lease, "prior supervisor lease")
    prior_process = prior_lease_payload.get("physical_process")
    prior_pid = (
        int(prior_process.get("pid", 0))
        if isinstance(prior_process, Mapping)
        else 0
    )
    failed_status_path = bound_path(
        candidate["failed_operation_status"], "failed operation status"
    )
    failed_log_path = bound_path(
        candidate["failed_operation_log"], "failed operation log"
    )
    failed_status = read_json(failed_status_path, "failed operation status")
    failure_log = failed_log_path.read_text(encoding="utf-8")
    recovery_generation = int(candidate["recovery_generation"])
    verify_recorded_supervisor_failure(
        prior_pid=prior_pid,
        recovery_generation=recovery_generation,
        prior_lease_payload=prior_lease_payload,
        failed_status=failed_status,
        failure_log=failure_log,
    )
    failure_receipt = bound_path(
        candidate["recovery_chain_failure_receipt"],
        "recovery chain failure receipt",
    )
    prior_recovery_candidate = bound_path(
        candidate["prior_recovery_candidate"], "prior recovery candidate"
    )
    prior_recovery_approval = bound_path(
        candidate["prior_recovery_approval"], "prior recovery approval"
    )
    prior_recovery_binding = bound_path(
        candidate["prior_recovery_binding"], "prior recovery binding"
    )
    failure = read_json(failure_receipt, "recovery chain failure receipt")
    failure_evidence = failure.get("evidence")
    if not (
        failure.get("overall_status") == "PASS"
        and failure.get("failure_classification")
        == candidate["failure_classification"]
        and failure.get("failed_physical_pid") == prior_pid
        and failure.get("failed_physical_pid_alive") is False
        and failure.get("active_simulator_jobs") == 0
        and failure.get("current_accepted") == 0
        and failure.get("current_feature_rows") == 0
        and failure.get("stage_entries") == 0
        and failure.get("simulator_action_taken") is False
        and isinstance(failure_evidence, Mapping)
        and failure_evidence.get("failed_runtime_status")
        == file_record(failed_status_path)
        and failure_evidence.get("failure_log") == file_record(failed_log_path)
        and failure_evidence.get("approved_recovery_candidate")
        == file_record(prior_recovery_candidate)
        and failure_evidence.get("approved_recovery_receipt")
        == file_record(prior_recovery_approval)
        and failure_evidence.get("recovery_binding")
        == file_record(prior_recovery_binding)
        and failure_evidence.get("generation_3_lease") == file_record(prior_lease)
        and failure_evidence.get("recovery_handoff")
        == file_record(prior_recovery_handoffs[-1])
    ):
        raise RecoveryError("recovery chain failure receipt mismatch")
    hotfix_payload = read_json(hotfix_handoff, "prior hotfix handoff")
    expected_old_pid = int(hotfix_payload.get("new_process_pid", 0))
    for index, handoff_path in enumerate(prior_recovery_handoffs, start=1):
        handoff = read_json(handoff_path, f"prior recovery handoff {index}")
        if not (
            handoff.get("schema") == HANDOFF_SCHEMA
            and handoff.get("overall_status") == "PASS"
            and handoff.get("decision") == HANDOFF_DECISION
            and handoff.get("campaign_id") == CAMPAIGN_ID
            and handoff.get("queue_id") == QUEUE_ID
            and handoff.get("supervisor_id") == SUPERVISOR_ID
            and handoff.get("contract_fingerprint_sha256") == FINGERPRINT
            and handoff.get("old_process_pid") == expected_old_pid
            and handoff.get("old_process_confirmed_exited") is True
            and handoff.get("recovery_scope") == RECOVERY_SCOPE
            and handoff.get("new_process_is_sole_authoritative_supervisor") is True
            and handoff.get("supervisor_count_after") == 1
            and handoff.get("overlap_seconds") == 0
            and handoff.get("new_queue_or_campaign_created") is False
            and handoff.get("nn_training_started") is False
        ):
            raise RecoveryError(f"prior recovery handoff {index} mismatch")
        expected_old_pid = int(handoff.get("new_process_pid", 0))
    if not (
        expected_old_pid == prior_pid
        and prior_lease_payload.get("operational_handoff_receipt")
        == file_record(prior_recovery_handoffs[-1])
    ):
        raise RecoveryError("prior recovery handoff-to-lease chain mismatch")
    stages = campaign_root / "stages"
    stage_parent_repair = bound_path(
        candidate["stage_parent_repair_receipt"], "stage parent repair receipt"
    )
    repair = read_json(stage_parent_repair, "stage parent repair receipt")
    repair_failure_status = bound_path(
        repair.get("failure_operation_status"), "stage-parent failure status"
    )
    repair_failure_log = bound_path(
        repair.get("failure_log"), "stage-parent failure log"
    )
    if not (
        repair.get("overall_status") == "PASS"
        and repair.get("failure_classification")
        == "STAGE_STDOUT_PARENT_DIRECTORY_MISSING_BEFORE_LAUNCH"
        and repair.get("failure_operation_status")
        == file_record(repair_failure_status)
        and repair.get("failure_log") == file_record(repair_failure_log)
        and repair.get("stage_parent_path") == str(stages)
        and repair.get("stage_parent_created") is True
        and stages.is_dir()
        and not any(stages.iterdir())
        and repair.get("active_simulator_jobs") == 0
        and repair.get("current_accepted") == 0
    ):
        raise RecoveryError("stage parent repair evidence mismatch")
    if not isinstance(prior_process, Mapping):
        raise RecoveryError("prior supervisor lease lacks physical identity")
    if module.read_process_identity(prior_pid) is not None:
        raise RecoveryError("failed predecessor PID is live or reused")
    current = module.read_process_identity(os.getpid())
    if current is None:
        raise RecoveryError("cannot read recovery supervisor identity")
    processes = module.enumerate_owner_processes(
        int(current["uid"]), probe_pid=0
    )
    counts = _project_counts(module, processes)
    if not (
        counts["supervisor_pids"] == [os.getpid()]
        and counts["runner_pids"] == []
        and counts["active_simulator_jobs"] == 0
    ):
        raise RecoveryError(f"pre-recovery process isolation mismatch: {counts}")
    lock_path = bound_path(bindings["campaign_lock"], "campaign lock")
    lock_fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(lock_fd)
        raise
    os.ftruncate(lock_fd, 0)
    os.write(lock_fd, f"{SUPERVISOR_ID}\n".encode("utf-8"))
    os.fsync(lock_fd)
    operation_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    log = (operation_root / "SUPERVISOR_RECOVERY.log").open(
        "x", encoding="utf-8", buffering=1
    )
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    restart_handoff = operation_root / "SUPERVISOR_RECOVERY_HANDOFF_RECEIPT.json"
    write_json_new(
        restart_handoff,
        {
            "schema": HANDOFF_SCHEMA,
            "generated_utc": utc_now(),
            "overall_status": "PASS",
            "decision": HANDOFF_DECISION,
            "campaign_id": CAMPAIGN_ID,
            "queue_id": QUEUE_ID,
            "supervisor_id": SUPERVISOR_ID,
            "contract_fingerprint_sha256": FINGERPRINT,
            "old_process_pid": prior_pid,
            "old_process_identity": dict(prior_process),
            "old_process_confirmed_exited": True,
            "new_process_pid": os.getpid(),
            "new_process_identity": module._public_process_record(current),
            "new_process_is_sole_authoritative_supervisor": True,
            "supervisor_count_after": 1,
            "overlap_seconds": 0,
            "new_queue_or_campaign_created": False,
            "nn_training_started": False,
            "handoff_scope": HOTFIX_SCOPE,
            "recovery_scope": RECOVERY_SCOPE,
            "prior_supervisor_lease": file_record(prior_lease),
            "restart_failure_receipt": file_record(failure_receipt),
            "recovery_candidate": file_record(candidate_path),
            "recovery_approval": file_record(approval_path),
            "active_simulator_jobs": 0,
            "simulator_action_taken": False,
            "campaign_data_modified": False,
        },
    )
    overlay = operation_root / "OPERATIONAL_POLICY_OVERLAY_RECOVERY.json"
    recovery_handoffs = [*prior_recovery_handoffs, restart_handoff]
    _build_overlay(
        path=overlay,
        bindings=bindings,
        runtime=runtime,
        operational_handoff=operational_handoff,
        hotfix_handoff=hotfix_handoff,
        recovery_handoffs=recovery_handoffs,
        failure_receipt=failure_receipt,
    )
    lease_dir = operation_root / "supervisor_leases"
    lease_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    lease_path = (
        lease_dir / f"SUPERVISOR_LEASE_GENERATION_{recovery_generation:04d}.json"
    )
    lease = module.build_restarted_supervisor_lease(
        physical_pid=os.getpid(),
        backend_identity_manifest=bound_path(
            bindings["production_backend_manifest"], "production backend"
        ),
        queue_entry=bound_path(bindings["queue_entry"], "queue entry"),
        supervisor_identity=bound_path(
            bindings["supervisor_identity"], "supervisor identity"
        ),
        operational_handoff_receipt=restart_handoff,
        prior_supervisor_lease=prior_lease,
        restart_failure_receipt=failure_receipt,
        campaign_lock=lock_path,
        lease_generation=recovery_generation,
        isolation_identity_auditor=bound_path(
            runtime["isolation_identity_auditor"], "isolation auditor"
        ),
    )
    module.write_json_exclusive(lease_path, lease)
    argv = _controller_argv(
        bindings=bindings,
        runtime=runtime,
        overlay=overlay,
        operational_handoff=operational_handoff,
        hotfix_handoff=hotfix_handoff,
        recovery_handoffs=recovery_handoffs,
        lease=lease_path,
        hotfix_old_pid=int(hotfix_payload["old_process_pid"]),
        lease_generation=recovery_generation,
    )
    write_json_new(
        operation_root / "SUPERVISOR_RECOVERY_BINDING.json",
        {
            "schema": "rfic_transformer.broadband56_v2_supervisor_recovery_binding.v2",
            "generated_utc": utc_now(),
            "overall_status": "PASS",
            "decision": "START_EXISTING_LOGICAL_SUPERVISOR_AFTER_RECORDED_FAILURE",
            "campaign_id": CAMPAIGN_ID,
            "queue_id": QUEUE_ID,
            "logical_supervisor_id": SUPERVISOR_ID,
            "physical_supervisor_pid": os.getpid(),
            "recovery_candidate": file_record(candidate_path),
            "recovery_approval": file_record(approval_path),
            "restart_handoff": file_record(restart_handoff),
            "prior_recovery_handoffs": [
                file_record(item) for item in prior_recovery_handoffs
            ],
            "supervisor_lease": file_record(lease_path),
            "stage_parent_repair_receipt": file_record(stage_parent_repair),
            "restart_failure_receipt": file_record(failure_receipt),
            "controller_argv_sha256": hashlib.sha256(
                json.dumps(argv, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "new_queue_or_campaign_created": False,
            "scientific_contract_changed": False,
            "simulator_contract_changed": False,
            "simulator_action_taken": False,
        },
    )
    write_json_atomic(
        operation_root / "SUPERVISOR_RECOVERY_STATUS.json",
        {
            "updated_utc": utc_now(),
            "status": "SUPERVISOR_RECOVERED_STARTING_CONTROLLER",
            "physical_supervisor_pid": os.getpid(),
            "logical_supervisor_id": SUPERVISOR_ID,
            "queue_id": QUEUE_ID,
            "active_simulator_jobs": 0,
            "simulator_action_taken": False,
        },
    )
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
    lock_fd = -1
    wrapper = load_module(
        bound_path(runtime["queue_controller"], "queue controller"),
        "b56_recovery_controller",
    )
    result = wrapper.main(argv)
    if result != 0:
        raise RecoveryError(f"queue controller returned {result}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-candidate", required=True)
    parser.add_argument("--recovery-candidate-sha256", required=True)
    parser.add_argument("--recovery-approval", required=True)
    parser.add_argument("--recovery-approval-sha256", required=True)
    parser.add_argument("--operation-root", required=True)
    parser.add_argument("--campaign-root", required=True)
    args = parser.parse_args(argv)
    try:
        run(args)
    except BaseException as exc:
        try:
            root = Path(args.operation_root).expanduser().resolve()
            if root.is_dir():
                write_json_atomic(
                    root / "SUPERVISOR_RECOVERY_STATUS.json",
                    {
                        "updated_utc": utc_now(),
                        "status": "BLOCKED",
                        "physical_supervisor_pid": os.getpid(),
                        "logical_supervisor_id": SUPERVISOR_ID,
                        "queue_id": QUEUE_ID,
                        "simulator_action_taken": False,
                        "blocker": str(exc),
                    },
                )
        except BaseException:
            pass
        print(f"overall_status=BLOCKED\nerror={exc}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
