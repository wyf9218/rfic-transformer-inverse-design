#!/usr/bin/env python3
"""Run the existing Broadband56 controller through an approved rebind overlay.

The existing full-campaign approval remains the scientific/stage authority.
The corrected foundry-layout approval and the no-clobber rebind receipt supply
the new backend/configuration binding.  This wrapper verifies that composition,
then delegates the controller loop without weakening its resource gates.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
CONTRACT_FINGERPRINT = (
    "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
)
CORRECTED_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
)
CORRECTED_APPROVAL_SCOPE = (
    "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
    "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
)
CORRECTED_APPROVAL_DECISION = "APPROVE_" + CORRECTED_APPROVAL_SCOPE
REBIND_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_queue_backend_rebind_receipt.v1"
REBIND_PLAN_SCHEMA = "rfic_transformer.broadband56_v2_queue_backend_rebind_plan.v1"
HANDOFF_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_supervisor_handoff_receipt.v1"
POST_REBIND_GATE_SCHEMA = "rfic_transformer.broadband56_v2_post_rebind_execution_gate.v1"
COMPOSITE_AUTHORIZATION_KIND = (
    "EXISTING_FULL_CAMPAIGN_PLUS_CORRECTED_LAYOUT_PLUS_BACKEND_REBIND"
)


class ReboundControllerError(RuntimeError):
    """Fail-closed error for the rebind-aware controller wrapper."""


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        private, delegated_argv = _parse_private_args(raw_argv)
        delegate_path = _regular_file(private.delegate_controller, "delegate controller")
        _require_sha(
            delegate_path,
            private.delegate_controller_sha256,
            "delegate controller",
        )
        controller = _load_delegate(delegate_path)
        args = controller._parse_args(delegated_argv)
        for name, value in vars(private).items():
            setattr(args, f"rebind_{name}", value)

        def validate(inputs: Mapping[str, Path], parsed: argparse.Namespace) -> dict[str, Any]:
            return _validate_control_evidence(
                controller,
                inputs=inputs,
                args=parsed,
                wrapper_path=Path(__file__).resolve(),
            )

        controller._validate_control_evidence = validate
        controller._interruptible_sleep = _safe_interruptible_sleep_factory(controller)
        campaign_root = Path(args.campaign_root).expanduser().resolve()
        try:
            state = controller.run_controller(args, campaign_root=campaign_root)
        except controller.ControllerError as exc:
            raise ReboundControllerError(str(exc)) from exc
        print(f"overall_status={state['overall_status']}")
        print(f"controller_id={state['authoritative_supervisor']}")
        print(f"campaign_status={campaign_root / controller.STATE_NAME}")
        return 0
    except (ReboundControllerError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=BLOCKED\nerror={exc}", file=sys.stderr)
        return 2


def _parse_private_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--delegate-controller", required=True)
    parser.add_argument("--delegate-controller-sha256", required=True)
    parser.add_argument("--old-full-campaign-receipt", required=True)
    parser.add_argument("--old-full-campaign-receipt-sha256", required=True)
    parser.add_argument("--old-backend-manifest", required=True)
    parser.add_argument("--old-backend-manifest-sha256", required=True)
    parser.add_argument("--corrected-approval-receipt", required=True)
    parser.add_argument("--corrected-approval-receipt-sha256", required=True)
    parser.add_argument("--queue-rebind-receipt", required=True)
    parser.add_argument("--queue-rebind-receipt-sha256", required=True)
    parser.add_argument("--supervisor-handoff-receipt", required=True)
    parser.add_argument("--supervisor-handoff-receipt-sha256", required=True)
    parser.add_argument("--post-rebind-execution-gate", required=True)
    parser.add_argument("--post-rebind-execution-gate-sha256", required=True)
    private, remaining = parser.parse_known_args(argv)
    for name, value in vars(private).items():
        if name.endswith("sha256") and not _is_sha256(value):
            raise ReboundControllerError(f"{name} is not a lowercase SHA-256 digest")
    return private, remaining


def _validate_control_evidence(
    controller: Any,
    *,
    inputs: Mapping[str, Path],
    args: argparse.Namespace,
    wrapper_path: Path,
) -> dict[str, Any]:
    contract = _read_json(inputs["frozen_contract"], "frozen contract")
    errors = controller.validate_contract(contract)
    fingerprint = contract.get("contract_fingerprint_sha256")
    if errors or not (
        contract.get("campaign_id") == CAMPAIGN_ID
        and fingerprint == CONTRACT_FINGERPRINT
        and controller.contract_fingerprint(contract) == fingerprint
    ):
        raise ReboundControllerError("frozen scientific contract identity mismatch")

    candidate = _read_json(inputs["full_campaign_candidate"], "old FULL candidate")
    candidate_sha = _sha256(inputs["full_campaign_candidate"])
    if controller.validate_full_campaign_candidate(candidate) or candidate_sha != str(
        args.full_campaign_candidate_sha256
    ).lower():
        raise ReboundControllerError("old FULL candidate identity mismatch")
    candidate_private = _mapping(
        candidate.get("private_preparation_evidence"), "candidate private preparation"
    )

    preparation = _read_json(inputs["preparation_receipt"], "preparation receipt")
    if not (
        preparation.get("overall_status") == "PASS"
        and preparation.get("decision") == "PREPARED_FOR_GOLDEN_GATE"
        and preparation.get("campaign_id") == CAMPAIGN_ID
        and preparation.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and _sha256(inputs["preparation_receipt"])
        == candidate_private.get("preparation_receipt_sha256")
        and _sha256(inputs["frozen_contract"])
        == candidate_private.get("campaign_contract_frozen_sha256")
    ):
        raise ReboundControllerError("preparation evidence identity mismatch")

    policy = _read_json(inputs["policy_approval_receipt"], "policy approval receipt")
    if not (
        policy.get("schema") == controller.POLICY_APPROVAL_SCHEMA
        and policy.get("overall_status") == "PASS"
        and policy.get("decision") == controller.POLICY_APPROVAL_SCOPE
        and policy.get("campaign_id") == CAMPAIGN_ID
        and policy.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and policy.get("resource_policy") == controller.RESOURCE_POLICY
        and policy.get("queue_authorized") is True
        and policy.get("supervisor_authorized") is True
        and _sha256(inputs["policy_approval_receipt"])
        == candidate_private.get("operational_policy_approval_receipt_sha256")
    ):
        raise ReboundControllerError("operational policy identity mismatch")

    old_manifest_path = _bound_private_file(
        args, "old_backend_manifest", "old_backend_manifest_sha256", "old backend"
    )
    old_full_path = _bound_private_file(
        args,
        "old_full_campaign_receipt",
        "old_full_campaign_receipt_sha256",
        "old FULL receipt",
    )
    old_manifest = _read_json(old_manifest_path, "old backend manifest")
    old_full = _read_json(old_full_path, "old FULL receipt")
    if not (
        old_full.get("schema") == controller.FULL_CAMPAIGN_APPROVAL_SCHEMA
        and old_full.get("overall_status") == "PASS"
        and old_full.get("decision") == controller.FULL_CAMPAIGN_PASS_DECISION
        and old_full.get("authorization_scope") == controller.FULL_CAMPAIGN_APPROVAL_SCOPE
        and old_full.get("campaign_id") == CAMPAIGN_ID
        and old_full.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and old_full.get("approved_candidate", {}).get("sha256") == candidate_sha
        and old_full.get("backend_identity_manifest", {}).get("sha256")
        == _sha256(old_manifest_path)
    ):
        raise ReboundControllerError("existing FULL_CAMPAIGN approval mismatch")
    if old_manifest.get("contract_fingerprint_sha256") != CONTRACT_FINGERPRINT:
        raise ReboundControllerError("old backend contract fingerprint mismatch")

    corrected_path = _bound_private_file(
        args,
        "corrected_approval_receipt",
        "corrected_approval_receipt_sha256",
        "corrected approval",
    )
    corrected = _read_json(corrected_path, "corrected approval")
    corrected_files = corrected.get("verified_bound_files")
    if not (
        corrected.get("schema") == CORRECTED_APPROVAL_SCHEMA
        and corrected.get("overall_status") == "PASS"
        and corrected.get("decision") == CORRECTED_APPROVAL_DECISION
        and corrected.get("authorization_scope") == CORRECTED_APPROVAL_SCOPE
        and corrected.get("restore_corrected_foundry_layout_contract_authorized") is True
        and corrected.get("one_corrected_rescue_golden_authorized") is True
        and corrected.get("automatic_post_golden_full_campaign_continuation_authorized")
        is True
        and corrected.get("reuse_existing_queue_only") is True
        and corrected.get("reuse_existing_authoritative_supervisor_only") is True
        and corrected.get("duplicate_queue_controller_supervisor_or_campaign_authorized")
        is False
        and corrected.get("nn_training_authorized") is False
        and isinstance(corrected_files, Mapping)
    ):
        raise ReboundControllerError("corrected foundry-layout approval mismatch")

    rebind_path = _bound_private_file(
        args,
        "queue_rebind_receipt",
        "queue_rebind_receipt_sha256",
        "queue rebind receipt",
    )
    handoff_path = _bound_private_file(
        args,
        "supervisor_handoff_receipt",
        "supervisor_handoff_receipt_sha256",
        "supervisor handoff receipt",
    )
    post_gate_path = _bound_private_file(
        args,
        "post_rebind_execution_gate",
        "post_rebind_execution_gate_sha256",
        "post-rebind execution gate",
    )
    rebind = _read_json(rebind_path, "queue rebind receipt")
    handoff = _read_json(handoff_path, "supervisor handoff receipt")
    post_gate = _read_json(post_gate_path, "post-rebind execution gate")

    backend = _read_json(inputs["backend_identity_manifest"], "corrected backend")
    backend_sha = _sha256(inputs["backend_identity_manifest"])
    backend_errors = controller.validate_backend_identity_manifest(backend, verify_files=True)
    if backend_errors:
        raise ReboundControllerError(
            "corrected backend manifest failed validation: " + "; ".join(backend_errors[:8])
        )
    if not (
        backend.get("campaign_id") == CAMPAIGN_ID
        and backend.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and backend.get("backend_id") == controller.PRODUCTION_BACKEND_ID
    ):
        raise ReboundControllerError("corrected backend identity mismatch")
    runtimes = _mapping(backend.get("runtime_identities"), "runtime identities")
    corrected_config_record = _mapping(
        corrected_files.get("corrected_private_configuration"),
        "approval-bound corrected config",
    )
    if runtimes.get("private_configuration") != corrected_config_record:
        raise ReboundControllerError(
            "backend private configuration differs from corrected approval"
        )

    plan = _mapping(backend.get("rebind_plan"), "backend rebind plan")
    plan_path = _regular_file(str(plan.get("path") or ""), "backend rebind plan")
    _require_identity(plan_path, plan, "backend rebind plan")
    plan_payload = _read_json(plan_path, "backend rebind plan")
    if not (
        plan_payload.get("schema") == REBIND_PLAN_SCHEMA
        and plan_payload.get("overall_status") == "PASS"
        and plan_payload.get("campaign_id") == CAMPAIGN_ID
        and plan_payload.get("queue_id") == QUEUE_ID
        and plan_payload.get("supervisor_id") == SUPERVISOR_ID
        and plan_payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and plan_payload.get("simulator_action_taken") is False
    ):
        raise ReboundControllerError("backend rebind plan mismatch")

    if not (
        rebind.get("schema") == REBIND_RECEIPT_SCHEMA
        and rebind.get("overall_status") == "PASS"
        and rebind.get("decision") == "REBIND_EXISTING_QUEUE_TO_CORRECTED_BACKEND"
        and rebind.get("campaign_id") == CAMPAIGN_ID
        and rebind.get("queue_id") == QUEUE_ID
        and rebind.get("supervisor_id") == SUPERVISOR_ID
        and rebind.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and rebind.get("old_backend_manifest", {}).get("sha256")
        == _sha256(old_manifest_path)
        and rebind.get("new_backend_manifest", {}).get("sha256") == backend_sha
        and rebind.get("rebind_plan", {}).get("sha256") == _sha256(plan_path)
        and rebind.get("corrected_approval_receipt", {}).get("sha256")
        == _sha256(corrected_path)
        and rebind.get("queue_id_unchanged") is True
        and rebind.get("supervisor_id_unchanged") is True
        and rebind.get("old_backend_preserved") is True
        and rebind.get("queue_rebind") == "PASS"
        and rebind.get("simulator_action_taken") is False
    ):
        raise ReboundControllerError("queue rebind receipt mismatch")
    if not (
        handoff.get("schema") == HANDOFF_RECEIPT_SCHEMA
        and handoff.get("overall_status") == "PASS"
        and handoff.get("campaign_id") == CAMPAIGN_ID
        and handoff.get("queue_id") == QUEUE_ID
        and handoff.get("supervisor_id") == SUPERVISOR_ID
        and handoff.get("old_process_confirmed_exited") is True
        and handoff.get("new_process_is_sole_authoritative_supervisor") is True
        and handoff.get("overlap_seconds") == 0
        and handoff.get("new_process_pid") == os.getpid()
        and handoff.get("supervisor_count_after") == 1
    ):
        raise ReboundControllerError("supervisor handoff receipt mismatch")
    if not (
        post_gate.get("schema") == POST_REBIND_GATE_SCHEMA
        and post_gate.get("overall_status") == "PASS"
        and post_gate.get("decision") == "START_CORRECTED_RESCUE_GOLDEN"
        and post_gate.get("campaign_id") == CAMPAIGN_ID
        and post_gate.get("queue_id") == QUEUE_ID
        and post_gate.get("supervisor_id") == SUPERVISOR_ID
        and post_gate.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and post_gate.get("queue_rebind") == "PASS"
        and post_gate.get("supervisor_rebind") == "PASS"
        and post_gate.get("supervisor_count") == 1
        and post_gate.get("current_accepted") == 0
        and post_gate.get("active_simulator_jobs") == 0
        and post_gate.get("resource_and_license_gate") == "PASS"
        and post_gate.get("simulator_action_taken") is False
        and post_gate.get("queue_rebind_receipt", {}).get("sha256")
        == _sha256(rebind_path)
        and post_gate.get("supervisor_handoff_receipt", {}).get("sha256")
        == _sha256(handoff_path)
        and post_gate.get("backend_identity_manifest", {}).get("sha256")
        == backend_sha
    ):
        raise ReboundControllerError("post-rebind execution gate mismatch")

    composite_path = inputs["full_campaign_receipt"]
    composite = _read_json(composite_path, "composite execution authorization")
    composition = _mapping(
        composite.get("authorization_composition"), "authorization composition"
    )
    required_true = (
        "queue_authorized",
        "supervisor_authorized",
        "automatic_capacity_wait_resume_authorized",
        "automatic_ordered_stage_execution_authorized",
        "cadence_authorized_within_current_stage",
        "calibre_authorized_within_current_stage",
        "emx_authorized_within_current_stage",
        "campaign_200k_authorized",
        "replenished_attempt_rounds_authorized",
    )
    if not (
        composite.get("schema") == controller.FULL_CAMPAIGN_APPROVAL_SCHEMA
        and composite.get("overall_status") == "PASS"
        and composite.get("decision") == controller.FULL_CAMPAIGN_PASS_DECISION
        and composite.get("authorization_scope") == controller.FULL_CAMPAIGN_APPROVAL_SCOPE
        and composite.get("campaign_id") == CAMPAIGN_ID
        and composite.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and composite.get("approved_candidate", {}).get("sha256") == candidate_sha
        and composite.get("backend_identity_manifest", {}).get("sha256") == backend_sha
        and composite.get("accepted_geometry_target")
        == controller.TARGET_ACCEPTED_GEOMETRIES
        and composite.get("attempt_replenishment_contract")
        == controller.ATTEMPT_REPLENISHMENT_CONTRACT
        and "simulator_geometry_limit" not in composite
        and all(composite.get(field) is True for field in required_true)
        and composition.get("kind") == COMPOSITE_AUTHORIZATION_KIND
        and composition.get("existing_full_campaign_receipt", {}).get("sha256")
        == _sha256(old_full_path)
        and composition.get("corrected_foundry_layout_approval_receipt", {}).get(
            "sha256"
        )
        == _sha256(corrected_path)
        and composition.get("queue_backend_rebind_receipt", {}).get("sha256")
        == _sha256(rebind_path)
        and composite.get("nn_training_authorized") is False
    ):
        raise ReboundControllerError("composite execution authorization mismatch")

    verification_path = inputs["backend_identity_verification_receipt"]
    verification = _read_json(verification_path, "backend verification receipt")
    if not (
        verification.get("schema") == controller.BACKEND_VERIFICATION_SCHEMA
        and verification.get("overall_status") == "PASS"
        and verification.get("decision")
        == controller.BACKEND_VERIFICATION_PASS_DECISION
        and verification.get("campaign_id") == CAMPAIGN_ID
        and verification.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and verification.get("checks") == controller.BACKEND_VERIFICATION_PASS_CHECKS
        and verification.get("errors") == []
        and verification.get("simulator_action_taken") is False
        and verification.get("authorization_effect")
        == "NONE_IDENTITY_VERIFICATION_ONLY"
        and verification.get("backend_identity_manifest", {}).get("sha256")
        == backend_sha
    ):
        raise ReboundControllerError("corrected backend verification mismatch")

    scripts = _mapping(backend.get("script_identities"), "script identities")
    _require_identity(wrapper_path, scripts.get("queue_controller"), "rebound controller")
    _require_identity(inputs["resource_gate_auditor"], scripts.get("resource_gate_auditor"), "resource gate auditor")
    _require_identity(inputs["stage_launcher"], scripts.get("stage_launcher"), "stage launcher")
    _require_identity(inputs["probe_script"], runtimes.get("resource_probe"), "resource probe")
    _require_identity(inputs["python_bin"], runtimes.get("python_executable"), "Python runtime")

    return {
        "contract": _file_record(inputs["frozen_contract"]),
        "preparation_receipt": _file_record(inputs["preparation_receipt"]),
        "policy_approval_receipt": _file_record(inputs["policy_approval_receipt"]),
        "candidate": _file_record(inputs["full_campaign_candidate"]),
        "backend_identity_manifest": _file_record(inputs["backend_identity_manifest"]),
        "backend_identity_verification_receipt": _file_record(verification_path),
        "stage_launcher": _file_record(inputs["stage_launcher"]),
        "frequency_contract": candidate["frequency_contract"],
        "port_and_grounding_contract": candidate["port_and_grounding_contract"],
        "unchanged_physical_contract_items": candidate[
            "unchanged_physical_contract_items"
        ],
        "backend_id": backend["backend_id"],
    }


def _safe_interruptible_sleep_factory(controller: Any) -> Any:
    def sleep(seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not controller.STOP_REQUESTED:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(1.0, max(0.0, remaining)))

    return sleep


def _load_delegate(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("broadband56_rebound_delegate", path)
    if spec is None or spec.loader is None:
        raise ReboundControllerError("cannot load delegate controller module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bound_private_file(
    args: argparse.Namespace,
    path_field: str,
    sha_field: str,
    label: str,
) -> Path:
    path = _regular_file(getattr(args, f"rebind_{path_field}"), label)
    _require_sha(path, getattr(args, f"rebind_{sha_field}"), label)
    return path


def _require_identity(path: Path, value: Any, label: str) -> None:
    record = _mapping(value, label)
    resolved = _regular_file(path, label)
    if not (
        Path(str(record.get("path") or "")).expanduser().resolve() == resolved
        and record.get("size_bytes") == resolved.stat().st_size
        and record.get("sha256") == _sha256(resolved)
    ):
        raise ReboundControllerError(f"{label} identity mismatch")


def _regular_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ReboundControllerError(f"{label} must not be a symlink")
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ReboundControllerError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReboundControllerError(f"{label} is not a JSON object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReboundControllerError(f"{label} is not an object")
    return value


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _require_sha(path: Path, expected: str, label: str) -> None:
    if _sha256(path) != expected:
        raise ReboundControllerError(f"{label} SHA-256 mismatch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


if __name__ == "__main__":
    raise SystemExit(main())
