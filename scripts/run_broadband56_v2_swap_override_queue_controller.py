#!/usr/bin/env python3
"""Resume the existing Broadband56 queue under the owner swap-gate overlay.

The queue, campaign root, corrected backend, scientific contract, and logical
supervisor identity remain unchanged.  This wrapper adds only the explicit
operational swap policy and a no-overlap process handoff receipt.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns import (  # noqa: E402
    broadband56_capacity_policy as capacity_policy,
    broadband56_capacity_snapshot_adapter as capacity_adapter,
    broadband56_isolation_identity as isolation_identity,
    broadband56_swap_override_policy as swap_policy,
)


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
CONTRACT_FINGERPRINT = (
    "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
)
OVERLAY_SCHEMA = "rfic_transformer.broadband56_v2_operational_policy_overlay.v1"
HANDOFF_SCHEMA = (
    "rfic_transformer.broadband56_v2_swap_policy_supervisor_handoff.v1"
)
HANDOFF_DECISION = "HANDOFF_SAME_LOGICAL_SUPERVISOR_FOR_SWAP_POLICY_OVERLAY"
RECOVERY_SCOPE = "SUPERVISOR_RECOVERY_AFTER_STAGE_PARENT_INITIALIZATION_FAILURE"
OVERRIDE_PATH_ENV = "B56_SWAP_OVERRIDE_RECEIPT"
OVERRIDE_SHA_ENV = "B56_SWAP_OVERRIDE_RECEIPT_SHA256"
OVERLAY_PATH_ENV = "B56_SWAP_OPERATIONAL_OVERLAY"
OVERLAY_SHA_ENV = "B56_SWAP_OPERATIONAL_OVERLAY_SHA256"


class SwapOverrideControllerError(RuntimeError):
    """Fail-closed error for the operational queue wrapper."""


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        private, rebound_argv = _parse_private_args(raw_argv)
        rebound_path = _bound_file(
            private.rebound_helper,
            private.rebound_helper_sha256,
            "rebound helper",
        )
        rebound = _load_module(rebound_path, "broadband56_swap_rebound_helper")
        rebind_private, delegated_argv = rebound._parse_private_args(rebound_argv)
        delegate_path = _bound_file(
            rebind_private.delegate_controller,
            rebind_private.delegate_controller_sha256,
            "delegate controller",
        )
        controller = rebound._load_delegate(delegate_path)
        args = controller._parse_args(delegated_argv)
        for name, value in vars(rebind_private).items():
            setattr(args, f"rebind_{name}", value)
        for name, value in vars(private).items():
            setattr(args, f"swap_override_{name}", value)

        override_path = _bound_file(
            private.swap_override_receipt,
            private.swap_override_receipt_sha256,
            "swap override receipt",
        )
        overlay_path = _bound_file(
            private.operational_overlay_manifest,
            private.operational_overlay_manifest_sha256,
            "operational overlay manifest",
        )
        operational_handoff_path = _bound_file(
            private.operational_handoff_receipt,
            private.operational_handoff_receipt_sha256,
            "operational handoff receipt",
        )
        isolation_hotfix_handoff_path = _bound_file(
            private.isolation_hotfix_handoff_receipt,
            private.isolation_hotfix_handoff_receipt_sha256,
            "isolation hotfix handoff receipt",
        )
        recovery_handoff_paths = [
            _bound_file(path, digest, f"supervisor recovery handoff receipt {index}")
            for index, (path, digest) in enumerate(
                zip(
                    private.supervisor_recovery_handoff_receipt,
                    private.supervisor_recovery_handoff_receipt_sha256,
                    strict=True,
                ),
                start=1,
            )
        ]
        isolation_auditor_path = _bound_file(
            private.isolation_identity_auditor,
            private.isolation_identity_auditor_sha256,
            "isolation identity auditor",
        )
        isolation_module_path = _bound_file(
            private.isolation_identity_module,
            private.isolation_identity_module_sha256,
            "isolation identity module",
        )
        if Path(isolation_identity.__file__).resolve() != isolation_module_path:
            raise SwapOverrideControllerError(
                "loaded isolation identity module does not match bound path"
            )

        os.environ[OVERRIDE_PATH_ENV] = str(override_path)
        os.environ[OVERRIDE_SHA_ENV] = private.swap_override_receipt_sha256
        os.environ[OVERLAY_PATH_ENV] = str(overlay_path)
        os.environ[OVERLAY_SHA_ENV] = private.operational_overlay_manifest_sha256

        original_run_probe = controller._run_probe
        original_run_stage_launcher = controller._run_stage_launcher

        def validate(
            inputs: Mapping[str, Path], parsed: argparse.Namespace
        ) -> dict[str, Any]:
            return _validate_control_evidence(
                rebound,
                controller,
                inputs=inputs,
                args=parsed,
                current_wrapper_path=Path(__file__).resolve(),
            )

        controller._validate_control_evidence = validate
        controller._interruptible_sleep = rebound._safe_interruptible_sleep_factory(
            controller
        )
        controller.evaluate_capacity_snapshot = swap_policy.evaluate_capacity_snapshot
        controller.adaptive_concurrency = swap_policy.adaptive_concurrency
        campaign_root = Path(args.campaign_root).expanduser().resolve()
        campaign_lock = (
            Path(args.lock_path).expanduser().resolve()
            if args.lock_path
            else campaign_root.parent / controller.LOCK_NAME
        )
        controller._run_probe = _swap_override_probe_factory(
            controller,
            original_run_probe=original_run_probe,
            override_receipt_path=override_path,
            overlay_manifest_path=overlay_path,
            operational_handoff_path=operational_handoff_path,
            isolation_hotfix_handoff_path=isolation_hotfix_handoff_path,
            supervisor_recovery_handoff_paths=recovery_handoff_paths,
            isolation_auditor_path=isolation_auditor_path,
            isolation_module_path=isolation_module_path,
            isolation_lease_path=Path(private.isolation_lease)
            .expanduser()
            .resolve(),
            isolation_lease_generation=private.isolation_lease_generation,
            backend_manifest_path=Path(args.backend_identity_manifest)
            .expanduser()
            .resolve(),
            campaign_root=campaign_root,
            campaign_lock=campaign_lock,
            python_bin=Path(args.python_bin).expanduser().resolve(),
        )
        controller._run_stage_launcher = _capacity_adapter_stage_launcher_factory(
            controller,
            original_run_stage_launcher=original_run_stage_launcher,
            poll_seconds=int(args.poll_seconds),
        )
        try:
            state = controller.run_controller(args, campaign_root=campaign_root)
        except controller.ControllerError as exc:
            raise SwapOverrideControllerError(str(exc)) from exc
        print(f"overall_status={state['overall_status']}")
        print(f"controller_id={state['authoritative_supervisor']}")
        print(f"campaign_status={campaign_root / controller.STATE_NAME}")
        return 0
    except (SwapOverrideControllerError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=BLOCKED\nerror={exc}", file=sys.stderr)
        return 2


def _parse_private_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--rebound-helper", required=True)
    parser.add_argument("--rebound-helper-sha256", required=True)
    parser.add_argument("--base-rebound-controller", required=True)
    parser.add_argument("--base-rebound-controller-sha256", required=True)
    parser.add_argument("--base-resource-gate-auditor", required=True)
    parser.add_argument("--base-resource-gate-auditor-sha256", required=True)
    parser.add_argument("--swap-override-receipt", required=True)
    parser.add_argument("--swap-override-receipt-sha256", required=True)
    parser.add_argument("--operational-overlay-manifest", required=True)
    parser.add_argument("--operational-overlay-manifest-sha256", required=True)
    parser.add_argument("--operational-handoff-receipt", required=True)
    parser.add_argument("--operational-handoff-receipt-sha256", required=True)
    parser.add_argument("--isolation-hotfix-handoff-receipt", required=True)
    parser.add_argument(
        "--isolation-hotfix-handoff-receipt-sha256", required=True
    )
    parser.add_argument("--supervisor-recovery-handoff-receipt", action="append", default=[])
    parser.add_argument(
        "--supervisor-recovery-handoff-receipt-sha256", action="append", default=[]
    )
    parser.add_argument("--isolation-identity-auditor", required=True)
    parser.add_argument("--isolation-identity-auditor-sha256", required=True)
    parser.add_argument("--isolation-identity-module", required=True)
    parser.add_argument("--isolation-identity-module-sha256", required=True)
    parser.add_argument("--isolation-lease", required=True)
    parser.add_argument("--isolation-lease-generation", type=int, required=True)
    parser.add_argument("--expected-handoff-old-pid", type=int, required=True)
    private, remaining = parser.parse_known_args(argv)
    if private.isolation_lease_generation < 1:
        raise SwapOverrideControllerError(
            "isolation lease generation must be positive"
        )
    if private.expected_handoff_old_pid < 1:
        raise SwapOverrideControllerError(
            "expected handoff old PID must be positive"
        )
    if len(private.supervisor_recovery_handoff_receipt) != len(
        private.supervisor_recovery_handoff_receipt_sha256
    ):
        raise SwapOverrideControllerError(
            "each supervisor recovery handoff path must have one paired SHA"
        )
    for name, value in vars(private).items():
        if not name.endswith("sha256"):
            continue
        digests = value if isinstance(value, list) else [value]
        if any(not _is_sha256(digest) for digest in digests):
            raise SwapOverrideControllerError(
                f"{name} is not a lowercase SHA-256 digest"
            )
    return private, remaining


def _validate_control_evidence(
    rebound: Any,
    controller: Any,
    *,
    inputs: Mapping[str, Path],
    args: argparse.Namespace,
    current_wrapper_path: Path,
) -> dict[str, Any]:
    base_wrapper = _bound_argument_file(
        args,
        "base_rebound_controller",
        "base_rebound_controller_sha256",
        "base rebound controller",
    )
    base_auditor = _bound_argument_file(
        args,
        "base_resource_gate_auditor",
        "base_resource_gate_auditor_sha256",
        "base resource gate auditor",
    )
    operational_handoff_path = _bound_argument_file(
        args,
        "operational_handoff_receipt",
        "operational_handoff_receipt_sha256",
        "operational handoff receipt",
    )
    operational_handoff = _read_json(
        operational_handoff_path, "operational handoff receipt"
    )
    hotfix_handoff_path = _bound_argument_file(
        args,
        "isolation_hotfix_handoff_receipt",
        "isolation_hotfix_handoff_receipt_sha256",
        "isolation hotfix handoff receipt",
    )
    hotfix_handoff = _read_json(
        hotfix_handoff_path, "isolation hotfix handoff receipt"
    )
    recovery_handoff_paths = [
        _bound_file(path, digest, f"supervisor recovery handoff receipt {index}")
        for index, (path, digest) in enumerate(
            zip(
                args.swap_override_supervisor_recovery_handoff_receipt,
                args.swap_override_supervisor_recovery_handoff_receipt_sha256,
                strict=True,
            ),
            start=1,
        )
    ]
    recovery_handoffs = [
        _read_json(path, f"supervisor recovery handoff receipt {index}")
        for index, path in enumerate(recovery_handoff_paths, start=1)
    ]
    base_handoff_pid = _validate_handoff_chain(
        operational_handoff=operational_handoff,
        hotfix_handoff=hotfix_handoff,
        recovery_handoffs=recovery_handoffs,
        expected_hotfix_old_pid=args.swap_override_expected_handoff_old_pid,
        current_pid=os.getpid(),
    )

    base_inputs = dict(inputs)
    base_inputs["resource_gate_auditor"] = base_auditor
    evidence = rebound._validate_control_evidence(
        controller,
        inputs=base_inputs,
        args=args,
        wrapper_path=base_wrapper,
        expected_handoff_pid=base_handoff_pid,
    )

    override_path = _bound_argument_file(
        args,
        "swap_override_receipt",
        "swap_override_receipt_sha256",
        "swap override receipt",
    )
    overlay_path = _bound_argument_file(
        args,
        "operational_overlay_manifest",
        "operational_overlay_manifest_sha256",
        "operational overlay manifest",
    )
    rebound_helper = _bound_argument_file(
        args,
        "rebound_helper",
        "rebound_helper_sha256",
        "rebound helper",
    )
    isolation_auditor = _bound_argument_file(
        args,
        "isolation_identity_auditor",
        "isolation_identity_auditor_sha256",
        "isolation identity auditor",
    )
    isolation_module = _bound_argument_file(
        args,
        "isolation_identity_module",
        "isolation_identity_module_sha256",
        "isolation identity module",
    )
    override = _read_json(override_path, "swap override receipt")
    overlay = _read_json(overlay_path, "operational overlay manifest")
    if not _override_exact(override):
        raise SwapOverrideControllerError("swap override receipt mismatch")
    if not _overlay_exact(
        overlay,
        inputs=inputs,
        current_wrapper_path=current_wrapper_path,
        rebound_helper_path=rebound_helper,
        base_wrapper_path=base_wrapper,
        base_auditor_path=base_auditor,
        override_receipt_path=override_path,
        isolation_auditor_path=isolation_auditor,
        isolation_module_path=isolation_module,
        previous_operational_handoff_path=operational_handoff_path,
        isolation_hotfix_handoff_path=hotfix_handoff_path,
        supervisor_recovery_handoff_paths=recovery_handoff_paths,
    ):
        raise SwapOverrideControllerError("operational overlay manifest mismatch")

    evidence["owner_swap_override_receipt"] = _file_record(override_path)
    evidence["operational_overlay_manifest"] = _file_record(overlay_path)
    evidence["operational_handoff_receipt"] = _file_record(
        operational_handoff_path
    )
    evidence["isolation_hotfix_handoff_receipt"] = _file_record(
        hotfix_handoff_path
    )
    evidence["supervisor_recovery_handoff_receipts"] = [
        _file_record(path) for path in recovery_handoff_paths
    ]
    evidence["isolation_identity_auditor"] = _file_record(isolation_auditor)
    evidence["isolation_identity_module"] = _file_record(isolation_module)
    evidence["swap_policy"] = swap_policy.SWAP_POLICY
    evidence["capacity_schema_adapter"] = _file_record(
        Path(capacity_adapter.__file__).resolve()
    )
    evidence["capacity_policy_module"] = _file_record(
        Path(capacity_policy.__file__).resolve()
    )
    evidence["capacity_schema_adapter_profile"] = capacity_adapter.ADAPTER_PROFILE
    return evidence


def _swap_override_probe_factory(
    controller: Any,
    *,
    original_run_probe: Any,
    override_receipt_path: Path,
    overlay_manifest_path: Path,
    operational_handoff_path: Path,
    isolation_hotfix_handoff_path: Path,
    supervisor_recovery_handoff_paths: list[Path],
    isolation_auditor_path: Path,
    isolation_module_path: Path,
    isolation_lease_path: Path,
    isolation_lease_generation: int,
    backend_manifest_path: Path,
    campaign_root: Path,
    campaign_lock: Path,
    python_bin: Path,
) -> Any:
    def run_probe(probe_script: Path, out_dir: Path, check_index: int) -> Path:
        oom_before = _proc_counter(Path("/proc/vmstat"), "oom_kill")
        blocked_before = _proc_counter(Path("/proc/stat"), "procs_blocked")
        source_path = original_run_probe(probe_script, out_dir, check_index)
        oom_after = _proc_counter(Path("/proc/vmstat"), "oom_kill")
        blocked_after = _proc_counter(Path("/proc/stat"), "procs_blocked")
        payload = copy.deepcopy(controller._read_json(source_path, "base resource snapshot"))
        resources = payload.get("resources")
        if not isinstance(resources, dict):
            raise controller.ControllerError("base resource snapshot lacks resources")
        resources["legacy_reported_active_swap_thrashing"] = resources.get(
            "active_swap_thrashing"
        )
        resources["oom_kill_delta"] = max(0, oom_after - oom_before)
        resources["blocked_process_count_delta"] = max(
            0, blocked_after - blocked_before
        )
        resources["active_swap_thrashing"] = swap_policy.combined_swap_thrashing(
            resources
        )["active"]
        base_isolation = payload.get("isolation")
        if not isinstance(base_isolation, dict):
            raise controller.ControllerError(
                "base resource snapshot lacks isolation"
            )
        if not isolation_lease_path.exists():
            lock_held, lock_contents = isolation_identity.campaign_lock_state(
                campaign_lock
            )
            if not lock_held or lock_contents.strip() != SUPERVISOR_ID:
                raise controller.ControllerError(
                    "cannot create supervisor lease without the bound flock"
                )
            authoritative_handoff = (
                supervisor_recovery_handoff_paths[-1]
                if supervisor_recovery_handoff_paths
                else isolation_hotfix_handoff_path
            )
            lease = isolation_identity.build_supervisor_lease(
                physical_pid=os.getpid(),
                backend_identity_manifest=backend_manifest_path,
                queue_entry=campaign_root / "MARS_QUEUE_ENTRY.json",
                supervisor_identity=campaign_root / "SUPERVISOR_IDENTITY.json",
                operational_handoff_receipt=authoritative_handoff,
                campaign_lock=campaign_lock,
                lease_generation=isolation_lease_generation,
                isolation_identity_auditor=isolation_auditor_path,
            )
            isolation_identity.write_json_exclusive(
                isolation_lease_path, lease
            )
        audit_path = _run_isolation_identity_audit(
            controller=controller,
            python_bin=python_bin,
            auditor_path=isolation_auditor_path,
            lease_path=isolation_lease_path,
            backend_manifest_path=backend_manifest_path,
            campaign_lock=campaign_lock,
            out_dir=out_dir,
            check_index=check_index,
        )
        audit = controller._read_json(audit_path, "isolation identity audit")
        if not (
            audit.get("schema") == isolation_identity.AUDIT_SCHEMA
            and audit.get("campaign_id") == CAMPAIGN_ID
            and audit.get("queue_id") == QUEUE_ID
            and audit.get("logical_supervisor_id") == SUPERVISOR_ID
            and isinstance(audit.get("isolation"), dict)
            and audit.get("simulator_action_taken") is False
            and audit.get("campaign_data_modified") is False
        ):
            raise controller.ControllerError(
                "isolation identity audit contract mismatch"
            )
        corrected_isolation = copy.deepcopy(audit["isolation"])
        corrected_isolation["output_path_collision"] = base_isolation.get(
            "output_path_collision"
        )
        payload["base_probe_isolation"] = copy.deepcopy(base_isolation)
        payload["isolation"] = corrected_isolation
        payload["schema"] = swap_policy.SNAPSHOT_SCHEMA
        payload["swap_policy"] = swap_policy.SWAP_POLICY
        payload["source_snapshot"] = _file_record(source_path)
        payload["owner_swap_override_receipt"] = _file_record(
            override_receipt_path
        )
        payload["operational_overlay_manifest"] = _file_record(
            overlay_manifest_path
        )
        payload["isolation_identity_module"] = _file_record(
            isolation_module_path
        )
        payload["isolation_identity_audit"] = _file_record(audit_path)
        payload["supervisor_lease"] = _file_record(isolation_lease_path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = out_dir / f"swap_override_{check_index:06d}_{timestamp}.json"
        _write_json_exclusive(path, payload)
        return path

    return run_probe


def _capacity_adapter_stage_launcher_factory(
    controller: Any,
    *,
    original_run_stage_launcher: Any,
    poll_seconds: int,
) -> Any:
    """Adapt only at the final controller-to-stage-launcher boundary."""

    def run_stage_launcher(
        *,
        inputs: Mapping[str, Path],
        campaign_root: Path,
        stage: str,
        concurrency: int,
        snapshot_path: Path,
        check_index: int,
    ) -> dict[str, Any]:
        source_path = snapshot_path.expanduser().resolve()
        source_check_index = int(check_index)
        refresh_attempt = 0
        gate_path = _resource_gate_for_snapshot(
            controller,
            campaign_root=campaign_root,
            snapshot_path=source_path,
        )
        initial_snapshot = True
        while True:
            gate = controller._read_json(gate_path, "source resource gate")
            gate_status = gate.get("overall_status")
            if gate_status not in {"PASS", "WAIT"}:
                raise controller.ControllerError(
                    "source resource gate has an invalid terminal status"
                )
            gate_passed = gate_status == "PASS"
            if initial_snapshot and not gate_passed:
                raise controller.ControllerError(
                    "stage launch requested from a non-PASS resource gate"
                )
            if gate_passed:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                adapter_out = (
                    campaign_root
                    / "resource_snapshot_adapters"
                    / f"{source_check_index:09d}_{timestamp}"
                )
                try:
                    adapted = (
                        capacity_adapter.normalize_capacity_snapshot_for_stage_launcher(
                            source_path,
                            gate_path,
                            adapter_out,
                        )
                    )
                    adapted_path = adapted["adapted_snapshot_path"]
                    fresh_at_child_launch = capacity_adapter.adapted_snapshot_is_fresh(
                        adapted_path
                    )
                    adapted_payload = controller._read_json(
                        adapted_path,
                        "adapted capacity snapshot",
                    )
                    policy = None
                    if fresh_at_child_launch:
                        policy = capacity_adapter.evaluate_adapted_capacity_snapshot(
                            adapted_payload,
                            stage=stage,
                            current_accepted=int(gate["current_accepted"]),
                            measured_pilot_bytes_per_geometry=(
                                controller._pilot_bytes_per_geometry(campaign_root)
                            ),
                        )
                except (
                    capacity_adapter.CapacitySnapshotAdapterError,
                    swap_policy.CapacityPolicyError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise controller.ControllerError(str(exc)) from exc
                if fresh_at_child_launch:
                    if policy is None:
                        raise controller.ControllerError(
                            "fresh adapted snapshot lacks a policy decision"
                        )
                    allowed = swap_policy.adaptive_concurrency(
                        stage=stage,
                        logical_cpu_count=policy["metrics"]["logical_cpu_count"],
                        simulator_license_capacity=policy["metrics"][
                            "simulator_license_capacity"
                        ],
                        current_concurrency=None,
                        healthy_check_streak=0,
                        normalized_load1=policy["metrics"]["normalized_load1"],
                        iowait_percent=policy["metrics"]["iowait_percent"],
                        available_memory_fraction=policy["metrics"][
                            "available_memory_fraction"
                        ],
                        active_swap_thrashing=policy["metrics"][
                            "active_swap_thrashing"
                        ],
                        licenses_available=policy["checks"]["license_gate"],
                        pilot_1000_safe_concurrency=(
                            controller._pilot_safe_concurrency(campaign_root)
                        ),
                    )
                    launch_concurrency = min(
                        int(concurrency),
                        int(allowed["concurrency"]),
                    )
                    if launch_concurrency >= 1:
                        return original_run_stage_launcher(
                            inputs=inputs,
                            campaign_root=campaign_root,
                            stage=stage,
                            concurrency=launch_concurrency,
                            snapshot_path=adapted_path,
                            check_index=check_index,
                        )

            refresh_attempt += 1
            if controller.STOP_REQUESTED:
                raise controller.ControllerError(
                    "controller stop requested while refreshing a stale snapshot"
                )
            if not gate_passed:
                controller._interruptible_sleep(poll_seconds)
                if controller.STOP_REQUESTED:
                    raise controller.ControllerError(
                        "controller stop requested while waiting for a fresh PASS gate"
                    )
            refresh_index = int(check_index) * 1_000_000 + refresh_attempt
            source_path = controller._run_probe(
                inputs["probe_script"],
                campaign_root / "resource_snapshots",
                refresh_index,
            )
            source_check_index = refresh_index
            gate_path = controller._write_resource_gate(
                inputs=inputs,
                snapshot_path=source_path,
                campaign_root=campaign_root,
                check_index=refresh_index,
                stage=stage,
                current_accepted=int(gate["current_accepted"]),
            )
            initial_snapshot = False

    return run_stage_launcher


def _resource_gate_for_snapshot(
    controller: Any,
    *,
    campaign_root: Path,
    snapshot_path: Path,
) -> Path:
    expected = _file_record(snapshot_path)
    matches: list[Path] = []
    for path in sorted(
        (campaign_root / "resource_gates").glob("*/CAPACITY_RESOURCE_GATE.json")
    ):
        try:
            payload = controller._read_json(path, "capacity resource gate")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        evidence = payload.get("evidence")
        if isinstance(evidence, Mapping) and evidence.get("resource_snapshot") == expected:
            matches.append(path.resolve())
    if len(matches) != 1:
        raise controller.ControllerError(
            "expected exactly one resource gate bound to the source snapshot"
        )
    return matches[0]


def _run_isolation_identity_audit(
    *,
    controller: Any,
    python_bin: Path,
    auditor_path: Path,
    lease_path: Path,
    backend_manifest_path: Path,
    campaign_lock: Path,
    out_dir: Path,
    check_index: int,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    audit_dir = (
        out_dir
        / f"isolation_identity_{check_index:06d}_{timestamp}"
    )
    log_path = out_dir / f"isolation_identity_{check_index:06d}_{timestamp}.log"
    command = [
        str(python_bin),
        str(auditor_path),
        "--lease",
        str(lease_path),
        "--backend-identity-manifest",
        str(backend_manifest_path),
        "--campaign-lock",
        str(campaign_lock),
        "--lease-registry-dir",
        str(lease_path.parent),
        "--out-dir",
        str(audit_dir),
    ]
    with log_path.open("x", encoding="utf-8") as log_handle:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=60,
        )
    audit_path = audit_dir / "ISOLATION_IDENTITY_AUDIT.json"
    if result.returncode != 0 or not audit_path.is_file():
        raise controller.ControllerError(
            "isolation identity auditor failed closed; "
            f"see {log_path}"
        )
    return audit_path


def _override_exact(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("schema") == swap_policy.OVERRIDE_RECEIPT_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == swap_policy.OVERRIDE_DECISION
        and payload.get("authorization_scope") == "OPERATIONAL_SWAP_GATE_ONLY"
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and payload.get("swap_policy") == swap_policy.SWAP_POLICY
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
    inputs: Mapping[str, Path],
    current_wrapper_path: Path,
    rebound_helper_path: Path,
    base_wrapper_path: Path,
    base_auditor_path: Path,
    override_receipt_path: Path,
    isolation_auditor_path: Path,
    isolation_module_path: Path,
    previous_operational_handoff_path: Path,
    isolation_hotfix_handoff_path: Path,
    supervisor_recovery_handoff_paths: list[Path] | None = None,
) -> bool:
    scripts = payload.get("script_identities")
    recovery_paths = supervisor_recovery_handoff_paths or []
    return (
        payload.get("schema") == OVERLAY_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == "BIND_OPERATIONAL_SWAP_POLICY_OVERLAY"
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and payload.get("swap_policy") == swap_policy.SWAP_POLICY
        and payload.get("capacity_schema_adapter_profile")
        == capacity_adapter.ADAPTER_PROFILE
        and payload.get("capacity_schema_source_schema")
        == capacity_adapter.SOURCE_SNAPSHOT_SCHEMA
        and payload.get("capacity_schema_target_schema")
        == capacity_adapter.TARGET_SNAPSHOT_SCHEMA
        and payload.get("capacity_snapshot_maximum_launch_age_seconds")
        == capacity_adapter.MAX_LAUNCH_AGE_SECONDS
        and payload.get("new_backend_created") is False
        and payload.get("new_queue_or_campaign_created") is False
        and payload.get("scientific_contract_changed") is False
        and payload.get("nn_training_authorized") is False
        and _identity_exact(override_receipt_path, payload.get("override_receipt"))
        and _identity_exact(
            previous_operational_handoff_path,
            payload.get("previous_operational_handoff"),
        )
        and _identity_exact(
            isolation_hotfix_handoff_path,
            payload.get("isolation_hotfix_handoff"),
        )
        and _recovery_handoffs_exact(payload, recovery_paths)
        and _identity_exact(
            inputs["backend_identity_manifest"],
            payload.get("corrected_backend_manifest"),
        )
        and isinstance(scripts, Mapping)
        and _identity_exact(current_wrapper_path, scripts.get("queue_controller"))
        and _identity_exact(rebound_helper_path, scripts.get("rebound_helper"))
        and _identity_exact(base_wrapper_path, scripts.get("base_rebound_controller"))
        and _identity_exact(
            inputs["resource_gate_auditor"], scripts.get("resource_gate_auditor")
        )
        and _identity_exact(
            base_auditor_path, scripts.get("base_resource_gate_auditor")
        )
        and _identity_exact(
            isolation_auditor_path,
            scripts.get("isolation_identity_auditor"),
        )
        and _identity_exact(
            isolation_module_path,
            scripts.get("isolation_identity_module"),
        )
        and _identity_exact(
            Path(swap_policy.__file__).resolve(), payload.get("policy_module")
        )
        and _identity_exact(
            Path(capacity_policy.__file__).resolve(),
            scripts.get("capacity_policy_module"),
        )
        and _identity_exact(
            Path(capacity_adapter.__file__).resolve(),
            scripts.get("capacity_schema_adapter"),
        )
    )


def _operational_handoff_exact(
    payload: Mapping[str, Any],
    *,
    expected_old_process_pid: int,
    expected_new_process_pid: int,
    require_process_identities: bool = False,
    require_new_process_live: bool = True,
) -> bool:
    base_valid = (
        payload.get("schema") == HANDOFF_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == HANDOFF_DECISION
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and payload.get("old_process_pid") == expected_old_process_pid
        and payload.get("old_process_confirmed_exited") is True
        and payload.get("new_process_pid") == expected_new_process_pid
        and payload.get("new_process_is_sole_authoritative_supervisor") is True
        and payload.get("supervisor_count_after") == 1
        and payload.get("overlap_seconds") == 0
        and payload.get("new_queue_or_campaign_created") is False
        and payload.get("nn_training_started") is False
    )
    if not base_valid or not require_process_identities:
        return base_valid
    old_identity = payload.get("old_process_identity")
    new_identity = payload.get("new_process_identity")
    identities_valid = (
        isinstance(old_identity, Mapping)
        and old_identity.get("pid") == expected_old_process_pid
        and _complete_process_identity(old_identity)
        and isinstance(new_identity, Mapping)
        and new_identity.get("pid") == expected_new_process_pid
        and _complete_process_identity(new_identity)
        and payload.get("handoff_scope")
        == "ISOLATION_GATE_AUTHORIZED_SUPERVISOR_ANCESTOR_IDENTITY_FIX"
    )
    if not identities_valid or not require_new_process_live:
        return identities_valid
    observed_new = isolation_identity.read_process_identity(
        expected_new_process_pid
    )
    return observed_new is not None and isolation_identity.process_identity_matches(
        observed_new, new_identity
    )


def _validate_handoff_chain(
    *,
    operational_handoff: Mapping[str, Any],
    hotfix_handoff: Mapping[str, Any],
    recovery_handoffs: list[Mapping[str, Any]],
    expected_hotfix_old_pid: int,
    current_pid: int,
) -> int:
    """Validate every physical PID transition and return the base handoff PID."""
    base_handoff_pid = int(operational_handoff.get("old_process_pid", 0))
    if not _operational_handoff_exact(
        operational_handoff,
        expected_old_process_pid=base_handoff_pid,
        expected_new_process_pid=expected_hotfix_old_pid,
    ):
        raise SwapOverrideControllerError(
            "previous operational handoff receipt mismatch"
        )
    hotfix_new_pid = (
        int(recovery_handoffs[0].get("old_process_pid", 0))
        if recovery_handoffs
        else current_pid
    )
    if not _operational_handoff_exact(
        hotfix_handoff,
        expected_old_process_pid=expected_hotfix_old_pid,
        expected_new_process_pid=hotfix_new_pid,
        require_process_identities=True,
        require_new_process_live=not recovery_handoffs,
    ):
        raise SwapOverrideControllerError(
            "isolation hotfix handoff receipt mismatch"
        )
    for index, recovery_handoff in enumerate(recovery_handoffs):
        expected_old_pid = (
            hotfix_new_pid
            if index == 0
            else int(recovery_handoffs[index - 1].get("new_process_pid", 0))
        )
        expected_new_pid = (
            int(recovery_handoffs[index + 1].get("old_process_pid", 0))
            if index + 1 < len(recovery_handoffs)
            else current_pid
        )
        if not (
            recovery_handoff.get("recovery_scope") == RECOVERY_SCOPE
            and _operational_handoff_exact(
                recovery_handoff,
                expected_old_process_pid=expected_old_pid,
                expected_new_process_pid=expected_new_pid,
                require_process_identities=True,
                require_new_process_live=index + 1 == len(recovery_handoffs),
            )
        ):
            raise SwapOverrideControllerError(
                f"supervisor recovery handoff receipt {index + 1} mismatch"
            )
    return base_handoff_pid


def _recovery_handoffs_exact(
    payload: Mapping[str, Any], recovery_paths: list[Path]
) -> bool:
    records = payload.get("supervisor_recovery_handoffs")
    if not recovery_paths:
        return records in (None, []) and payload.get("supervisor_recovery_handoff") is None
    if isinstance(records, list):
        return len(records) == len(recovery_paths) and all(
            _identity_exact(path, record)
            for path, record in zip(recovery_paths, records, strict=True)
        )
    return len(recovery_paths) == 1 and _identity_exact(
        recovery_paths[0], payload.get("supervisor_recovery_handoff")
    )


def _complete_process_identity(value: Mapping[str, Any]) -> bool:
    integer_fields = ("pid", "parent_pid", "uid", "start_ticks")
    text_fields = (
        "boot_id",
        "command_line_sha256",
        "executable_path",
        "executable_sha256",
    )
    return (
        all(
            isinstance(value.get(field), int)
            and not isinstance(value.get(field), bool)
            and int(value[field]) >= 0
            for field in integer_fields
        )
        and all(
            isinstance(value.get(field), str) and bool(value[field])
            for field in text_fields
        )
        and _is_sha256(value.get("command_line_sha256"))
        and _is_sha256(value.get("executable_sha256"))
    )


def _bound_argument_file(
    args: argparse.Namespace,
    path_field: str,
    sha_field: str,
    label: str,
) -> Path:
    return _bound_file(
        getattr(args, f"swap_override_{path_field}"),
        getattr(args, f"swap_override_{sha_field}"),
        label,
    )


def _bound_file(value: str | Path, expected_sha: str, label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise SwapOverrideControllerError(f"{label} must not be a symlink")
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise SwapOverrideControllerError(f"{label} is missing or empty: {resolved}")
    if _sha256(resolved) != expected_sha:
        raise SwapOverrideControllerError(f"{label} SHA-256 mismatch")
    return resolved


def _proc_counter(path: Path, key: str) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == key:
                value = int(fields[1])
                if value < 0:
                    break
                return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise SwapOverrideControllerError(
            f"cannot read {key} from {path}: {exc}"
        ) from exc
    raise SwapOverrideControllerError(f"missing nonnegative {key} in {path}")


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SwapOverrideControllerError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SwapOverrideControllerError(f"failed to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SwapOverrideControllerError(f"{label} root is not an object")
    return payload


def _identity_exact(path: Path, value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    resolved = path.expanduser().resolve()
    return (
        resolved.is_file()
        and Path(str(value.get("path") or "")).expanduser().resolve() == resolved
        and value.get("size_bytes") == resolved.stat().st_size
        and value.get("sha256") == _sha256(resolved)
    )


def _all_checks_pass(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, Mapping) and item.get("pass") is True for item in checks
    )


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


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
