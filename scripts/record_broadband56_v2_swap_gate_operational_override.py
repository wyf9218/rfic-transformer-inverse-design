#!/usr/bin/env python3
"""Record the project-owner swap-gate override without launching simulators."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    validate_backend_identity_manifest,
)
from rfic_transformer_inverse_design.campaigns.broadband56_swap_override_policy import (  # noqa: E402
    MAX_IOWAIT_PERCENT_EXCLUSIVE,
    MIN_AVAILABLE_MEMORY_FRACTION,
    OVERRIDE_DECISION,
    OVERRIDE_RECEIPT_SCHEMA,
    SWAP_POLICY,
)


QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
CORRECTED_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
)
CORRECTED_APPROVAL_SCOPE = (
    "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
    "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
)
CORRECTED_APPROVAL_DECISION = "APPROVE_" + CORRECTED_APPROVAL_SCOPE
RECEIPT_NAME = "SWAP_GATE_OPERATIONAL_OVERRIDE_RECEIPT.json"


class OverrideReceiptError(RuntimeError):
    """Raised when an override receipt cannot be recorded fail closed."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = record_override(
            owner_instruction_path=Path(args.owner_instruction).expanduser().resolve(),
            frozen_contract_path=Path(args.frozen_contract).expanduser().resolve(),
            full_campaign_receipt_path=Path(args.full_campaign_receipt)
            .expanduser()
            .resolve(),
            corrected_backend_manifest_path=Path(args.corrected_backend_manifest)
            .expanduser()
            .resolve(),
            corrected_approval_receipt_path=Path(args.corrected_approval_receipt)
            .expanduser()
            .resolve(),
            queue_entry_path=Path(args.queue_entry).expanduser().resolve(),
            supervisor_identity_path=Path(args.supervisor_identity)
            .expanduser()
            .resolve(),
            approved_by=args.approved_by,
            approved_utc=args.approved_utc,
            out_dir=out_dir,
        )
    except OverrideReceiptError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={receipt['overall_status']}")
    print(f"swap_policy={receipt['swap_policy']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-instruction", required=True)
    parser.add_argument("--frozen-contract", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--corrected-backend-manifest", required=True)
    parser.add_argument("--corrected-approval-receipt", required=True)
    parser.add_argument("--queue-entry", required=True)
    parser.add_argument("--supervisor-identity", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-utc", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def record_override(
    *,
    owner_instruction_path: Path,
    frozen_contract_path: Path,
    full_campaign_receipt_path: Path,
    corrected_backend_manifest_path: Path,
    corrected_approval_receipt_path: Path,
    queue_entry_path: Path,
    supervisor_identity_path: Path,
    approved_by: str,
    approved_utc: str,
    out_dir: Path,
) -> dict[str, Any]:
    if out_dir.exists():
        raise OverrideReceiptError(f"no-clobber output exists: {out_dir}")
    approved_identity = approved_by.strip()
    if approved_identity != "Yufeng Wang, project owner and project leader":
        raise OverrideReceiptError("approved_by identity mismatch")
    approved_time = _parse_aware_utc(approved_utc)
    if approved_time is None:
        raise OverrideReceiptError("approved_utc must be timezone-aware UTC")

    owner_text = _read_text(owner_instruction_path, "owner instruction")
    contract = _read_json(frozen_contract_path, "frozen contract")
    full = _read_json(full_campaign_receipt_path, "FULL_CAMPAIGN receipt")
    backend = _read_json(corrected_backend_manifest_path, "corrected backend manifest")
    corrected = _read_json(
        corrected_approval_receipt_path, "corrected foundry-layout approval"
    )
    queue = _read_json(queue_entry_path, "queue entry")
    supervisor = _read_json(supervisor_identity_path, "supervisor identity")
    backend_sha = _sha256(corrected_backend_manifest_path)
    checks = [
        _check("owner_instruction_exact_scope", _owner_instruction_exact(owner_text)),
        _check(
            "frozen_contract_exact",
            not validate_contract(contract)
            and contract.get("campaign_id") == CAMPAIGN_ID
            and contract.get("contract_fingerprint_sha256")
            == SCIENTIFIC_CONTRACT_FINGERPRINT
            and contract_fingerprint(contract) == SCIENTIFIC_CONTRACT_FINGERPRINT,
        ),
        _check(
            "corrected_backend_exact",
            not validate_backend_identity_manifest(backend, verify_files=True)
            and backend.get("campaign_id") == CAMPAIGN_ID
            and backend.get("contract_fingerprint_sha256")
            == SCIENTIFIC_CONTRACT_FINGERPRINT,
        ),
        _check(
            "full_campaign_authorization_exact",
            full.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA
            and full.get("overall_status") == "PASS"
            and full.get("decision") == FULL_CAMPAIGN_PASS_DECISION
            and full.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
            and full.get("campaign_id") == CAMPAIGN_ID
            and full.get("contract_fingerprint_sha256")
            == SCIENTIFIC_CONTRACT_FINGERPRINT
            and full.get("backend_identity_manifest", {}).get("sha256")
            == backend_sha
            and full.get("campaign_200k_authorized") is True
            and full.get("nn_training_authorized") is False,
        ),
        _check(
            "corrected_foundry_layout_authorization_exact",
            corrected.get("schema") == CORRECTED_APPROVAL_SCHEMA
            and corrected.get("overall_status") == "PASS"
            and corrected.get("decision") == CORRECTED_APPROVAL_DECISION
            and corrected.get("authorization_scope") == CORRECTED_APPROVAL_SCOPE
            and corrected.get("reuse_existing_queue_only") is True
            and corrected.get("reuse_existing_authoritative_supervisor_only") is True
            and corrected.get("nn_training_authorized") is False,
        ),
        _check(
            "queue_identity_exact",
            queue.get("campaign_id") == CAMPAIGN_ID
            and queue.get("queue_id") == QUEUE_ID
            and queue.get("contract_fingerprint_sha256")
            == SCIENTIFIC_CONTRACT_FINGERPRINT
            and queue.get("backend_identity_manifest", {}).get("sha256")
            == backend_sha,
        ),
        _check(
            "logical_supervisor_identity_exact",
            supervisor.get("campaign_id") == CAMPAIGN_ID
            and supervisor.get("controller_id") == SUPERVISOR_ID,
        ),
    ]
    failed = [item["name"] for item in checks if not item["pass"]]
    if failed:
        raise OverrideReceiptError("evidence checks failed: " + ", ".join(failed))

    receipt = {
        "schema": OVERRIDE_RECEIPT_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": OVERRIDE_DECISION,
        "authorization_scope": "OPERATIONAL_SWAP_GATE_ONLY",
        "approved_by": approved_identity,
        "approved_utc": approved_time.isoformat(timespec="seconds"),
        "approval_reference": (
            "Current Codex-thread explicit project-owner operational override "
            "removing the strict-zero swap-in requirement"
        ),
        "campaign_id": CAMPAIGN_ID,
        "queue_id": QUEUE_ID,
        "supervisor_id": SUPERVISOR_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "base_resource_policy": RESOURCE_POLICY,
        "swap_policy": SWAP_POLICY,
        "swap_zero_requirement_removed": True,
        "nonzero_swap_in_alone_is_advisory": True,
        "active_swap_thrashing_requires_swap_and_resource_degradation": True,
        "hard_resource_gates": {
            "available_memory_fraction_minimum": MIN_AVAILABLE_MEMORY_FRACTION,
            "no_oom_event": True,
            "iowait_percent_maximum_exclusive": MAX_IOWAIT_PERCENT_EXCLUSIVE,
            "storage_gate": "PASS_REQUIRED",
            "cadence_license": "PASS_REQUIRED",
            "calibre_license": "PASS_REQUIRED",
            "emx_license": "PASS_REQUIRED",
            "isolation_gate": "PASS_REQUIRED",
            "corrected_backend_identity": "PASS_REQUIRED",
            "full_campaign_authorization": "PASS_REQUIRED",
            "authoritative_supervisor_count": 1,
            "duplicate_runner_count": 0,
            "duplicate_simulator_job_count": 0,
            "output_path_collision": False,
        },
        "execution_mode_on_pass": "HIGH_LOAD_OR_SWAP_RECOVERY_DEGRADED_MODE",
        "initial_concurrency": 1,
        "accepted_geometry_target": TARGET_ACCEPTED_GEOMETRIES,
        "automatic_post_golden_continuation_authorized": True,
        "scientific_contract_changed": False,
        "process_or_layout_contract_changed": False,
        "drc_contract_changed": False,
        "frequency_or_port_contract_changed": False,
        "accepted_data_definition_changed": False,
        "new_queue_or_campaign_authorized": False,
        "nn_training_authorized": False,
        "execution_effect": "NONE_RECORD_ONLY",
        "checks": checks,
        "evidence": {
            "owner_instruction": _file_record(owner_instruction_path),
            "frozen_contract": _file_record(frozen_contract_path),
            "full_campaign_receipt": _file_record(full_campaign_receipt_path),
            "corrected_backend_manifest": _file_record(
                corrected_backend_manifest_path
            ),
            "corrected_foundry_layout_approval": _file_record(
                corrected_approval_receipt_path
            ),
            "queue_entry": _file_record(queue_entry_path),
            "supervisor_identity": _file_record(supervisor_identity_path),
        },
    }
    _write_no_clobber(out_dir, receipt)
    return receipt


def _owner_instruction_exact(text: str) -> bool:
    required = (
        "PROJECT-OWNER OPERATIONAL OVERRIDE:",
        CAMPAIGN_ID,
        QUEUE_ID,
        SUPERVISOR_ID,
        "SWAP_POLICY=COMBINED_RESOURCE_DEGRADATION_ONLY",
        "available-memory fraction >= 0.40",
        "iowait < 5%",
        "NN_TRAINING_AUTHORIZED=no",
    )
    return all(value in text for value in required)


def _write_no_clobber(out_dir: Path, receipt: Mapping[str, Any]) -> None:
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise OverrideReceiptError(f"staging path exists: {staging}")
    staging.mkdir(parents=True)
    try:
        path = staging / RECEIPT_NAME
        _write_json(path, receipt)
        sums = staging / "SHA256SUMS.txt"
        sums.write_text(f"{_sha256(path)}  {RECEIPT_NAME}\n", encoding="utf-8")
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_text(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise OverrideReceiptError(f"{label} is missing, empty, or a symlink")
    return path.read_text(encoding="utf-8")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(_read_text(path, label))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OverrideReceiptError(f"failed to read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OverrideReceiptError(f"{label} root is not an object")
    return payload


def _parse_aware_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
