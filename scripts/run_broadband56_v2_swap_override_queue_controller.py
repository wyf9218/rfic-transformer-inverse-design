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
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns import (  # noqa: E402
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

        os.environ[OVERRIDE_PATH_ENV] = str(override_path)
        os.environ[OVERRIDE_SHA_ENV] = private.swap_override_receipt_sha256
        os.environ[OVERLAY_PATH_ENV] = str(overlay_path)
        os.environ[OVERLAY_SHA_ENV] = private.operational_overlay_manifest_sha256

        original_run_probe = controller._run_probe

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
        controller._run_probe = _swap_override_probe_factory(
            controller,
            original_run_probe=original_run_probe,
            override_receipt_path=override_path,
            overlay_manifest_path=overlay_path,
            operational_handoff_path=operational_handoff_path,
        )
        campaign_root = Path(args.campaign_root).expanduser().resolve()
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
    private, remaining = parser.parse_known_args(argv)
    for name, value in vars(private).items():
        if name.endswith("sha256") and not _is_sha256(value):
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
    if not _operational_handoff_exact(operational_handoff):
        raise SwapOverrideControllerError("operational handoff receipt mismatch")

    base_inputs = dict(inputs)
    base_inputs["resource_gate_auditor"] = base_auditor
    evidence = rebound._validate_control_evidence(
        controller,
        inputs=base_inputs,
        args=args,
        wrapper_path=base_wrapper,
        expected_handoff_pid=int(operational_handoff["old_process_pid"]),
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
    ):
        raise SwapOverrideControllerError("operational overlay manifest mismatch")

    evidence["owner_swap_override_receipt"] = _file_record(override_path)
    evidence["operational_overlay_manifest"] = _file_record(overlay_path)
    evidence["operational_handoff_receipt"] = _file_record(
        operational_handoff_path
    )
    evidence["swap_policy"] = swap_policy.SWAP_POLICY
    return evidence


def _swap_override_probe_factory(
    controller: Any,
    *,
    original_run_probe: Any,
    override_receipt_path: Path,
    overlay_manifest_path: Path,
    operational_handoff_path: Path,
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
        payload = _include_verified_current_supervisor(
            payload,
            controller=controller,
            operational_handoff_path=operational_handoff_path,
        )
        payload["schema"] = swap_policy.SNAPSHOT_SCHEMA
        payload["swap_policy"] = swap_policy.SWAP_POLICY
        payload["source_snapshot"] = _file_record(source_path)
        payload["owner_swap_override_receipt"] = _file_record(
            override_receipt_path
        )
        payload["operational_overlay_manifest"] = _file_record(
            overlay_manifest_path
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = out_dir / f"swap_override_{check_index:06d}_{timestamp}.json"
        _write_json_exclusive(path, payload)
        return path

    return run_probe


def _include_verified_current_supervisor(
    payload: Mapping[str, Any],
    *,
    controller: Any,
    operational_handoff_path: Path,
) -> dict[str, Any]:
    """Restore the controller omitted by the ancestor-filtering base probe."""
    updated = copy.deepcopy(payload)
    isolation = updated.get("isolation")
    if not isinstance(isolation, dict):
        raise controller.ControllerError("base resource snapshot lacks isolation")
    handoff = controller._read_json(
        operational_handoff_path,
        "operational handoff receipt",
    )
    if not _operational_handoff_exact(handoff):
        raise controller.ControllerError(
            "current supervisor is not bound by the operational handoff receipt"
        )

    external_count = isolation.get("authoritative_supervisor_count")
    external_duplicates = isolation.get("duplicate_supervisor_count")
    if (
        not isinstance(external_count, int)
        or isinstance(external_count, bool)
        or external_count < 0
        or not isinstance(external_duplicates, int)
        or isinstance(external_duplicates, bool)
        or external_duplicates < 0
        or external_duplicates != max(0, external_count - 1)
    ):
        raise controller.ControllerError(
            "base resource snapshot has inconsistent supervisor counts"
        )
    if "supervisor_self_inclusion" in isolation:
        raise controller.ControllerError(
            "base resource snapshot already contains supervisor self-inclusion"
        )

    total_count = external_count + 1
    isolation["authoritative_supervisor_count"] = total_count
    isolation["duplicate_supervisor_count"] = max(0, total_count - 1)
    isolation["supervisor_self_inclusion"] = {
        "reason": "BASE_PROBE_EXCLUDES_ANCESTOR_CONTROLLER",
        "external_supervisor_count": external_count,
        "current_supervisor_pid": os.getpid(),
        "logical_supervisor_id": SUPERVISOR_ID,
        "operational_handoff_receipt": _file_record(
            operational_handoff_path
        ),
    }
    return updated


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
) -> bool:
    scripts = payload.get("script_identities")
    return (
        payload.get("schema") == OVERLAY_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == "BIND_OPERATIONAL_SWAP_POLICY_OVERLAY"
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and payload.get("swap_policy") == swap_policy.SWAP_POLICY
        and payload.get("new_backend_created") is False
        and payload.get("new_queue_or_campaign_created") is False
        and payload.get("scientific_contract_changed") is False
        and payload.get("nn_training_authorized") is False
        and _identity_exact(override_receipt_path, payload.get("override_receipt"))
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
            Path(swap_policy.__file__).resolve(), payload.get("policy_module")
        )
    )


def _operational_handoff_exact(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("schema") == HANDOFF_SCHEMA
        and payload.get("overall_status") == "PASS"
        and payload.get("decision") == HANDOFF_DECISION
        and payload.get("campaign_id") == CAMPAIGN_ID
        and payload.get("queue_id") == QUEUE_ID
        and payload.get("supervisor_id") == SUPERVISOR_ID
        and payload.get("contract_fingerprint_sha256") == CONTRACT_FINGERPRINT
        and payload.get("old_process_pid") == 676436
        and payload.get("old_process_confirmed_exited") is True
        and payload.get("new_process_pid") == os.getpid()
        and payload.get("new_process_is_sole_authoritative_supervisor") is True
        and payload.get("supervisor_count_after") == 1
        and payload.get("overlap_seconds") == 0
        and payload.get("new_queue_or_campaign_created") is False
        and payload.get("nn_training_started") is False
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
