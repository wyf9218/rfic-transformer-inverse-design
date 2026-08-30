#!/usr/bin/env python3
"""Launch one authorized broadband56 stage through a private backend command.

This is the final control-plane gate before a simulator-capable backend.  It
requires an exact FULL_CAMPAIGN PASS receipt, an ordered stage boundary, a
fresh PASS capacity snapshot, and a hash-bound private backend manifest.  The
backend must write the exact stage receipt; this launcher does not invent one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
    stage_for_progress,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    ALLOWED_STAGE_PLACEHOLDERS,
    validate_backend_identity_manifest,
    validate_stage_receipt,
    validate_stage_receipt_chain,
)


LAUNCH_AUDIT_SCHEMA = "rfic_transformer.broadband56_v2_stage_launch_audit.v1"
ALLOWED_PLACEHOLDERS = set(ALLOWED_STAGE_PLACEHOLDERS)


class StageLauncherError(RuntimeError):
    """Fail-closed stage-launch error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = launch_stage(args, out_dir=out_dir)
    except StageLauncherError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"stage={result['stage']}")
    print(f"receipt={out_dir / 'STAGE_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--cumulative-target", type=int, required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--resource-snapshot", required=True)
    parser.add_argument("--max-concurrency", type=int, required=True)
    return parser.parse_args(argv)


def launch_stage(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).upper()
    if stage not in STAGE_BY_NAME:
        raise StageLauncherError(f"unknown stage: {stage}")
    spec = STAGE_BY_NAME[stage]
    if int(args.cumulative_target) != spec.cumulative_target:
        raise StageLauncherError("stage cumulative target mismatch")
    campaign_root = Path(args.campaign_root).expanduser().resolve()
    if not campaign_root.is_dir():
        raise StageLauncherError(f"campaign root is missing: {campaign_root}")
    receipt_path = Path(args.full_campaign_receipt).expanduser().resolve()
    backend_path = Path(args.backend_identity_manifest).expanduser().resolve()
    snapshot_path = Path(args.resource_snapshot).expanduser().resolve()
    receipt = _read_json(receipt_path, "full-campaign receipt")
    backend = _read_json(backend_path, "backend identity manifest")
    snapshot = _read_json(snapshot_path, "resource snapshot")
    _validate_receipt(receipt, backend_sha256=_sha256(backend_path))
    _validate_backend(backend, launcher_path=Path(__file__).resolve())

    stage_receipt_records = _ordered_stage_receipt_records(campaign_root)
    chain_errors = validate_stage_receipt_chain(
        stage_receipt_records,
        backend_manifest_sha256=_sha256(backend_path),
        authorization_receipt_sha256=_sha256(receipt_path),
        verify_artifacts=True,
    )
    if chain_errors:
        raise StageLauncherError(
            "prior stage receipt chain failed validation: "
            + "; ".join(chain_errors[:8])
        )
    stage_receipts = [value for _, value in stage_receipt_records]
    current_accepted = (
        int(stage_receipts[-1]["accepted_unique_geometries"])
        if stage_receipts
        else 0
    )
    next_stage = stage_for_progress(
        current_accepted=current_accepted,
        stage_receipts=stage_receipts,
    )
    if next_stage != stage:
        raise StageLauncherError(f"requested stage {stage} is out of order; next legal stage is {next_stage}")

    policy = evaluate_capacity_snapshot(
        snapshot,
        stage=stage,
        current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=_pilot_bytes_per_geometry(campaign_root),
    )
    if not policy["pass"]:
        raise StageLauncherError(
            "resource snapshot is WAIT: " + ",".join(policy["failed_checks"])
        )
    allowed = adaptive_concurrency(
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
    requested_concurrency = int(args.max_concurrency)
    if requested_concurrency < 1 or requested_concurrency > int(allowed["concurrency"]):
        raise StageLauncherError(
            f"requested concurrency {requested_concurrency} exceeds current allowed {allowed['concurrency']}"
        )

    template = _stage_command_template(backend, stage)
    backend_out_dir = out_dir / "backend"
    substitutions = {
        "{stage}": stage,
        "{cumulative_target}": str(spec.cumulative_target),
        "{campaign_root}": str(campaign_root),
        "{backend_out_dir}": str(backend_out_dir),
        "{full_campaign_receipt}": str(receipt_path),
        "{backend_identity_manifest}": str(backend_path),
        "{resource_snapshot}": str(snapshot_path),
        "{max_concurrency}": str(requested_concurrency),
    }
    command = [_substitute_argument(item, substitutions) for item in template]
    _validate_backend_command_identity(command, backend, stage)

    out_dir.mkdir(parents=True, mode=0o700)
    audit = {
        "schema": LAUNCH_AUDIT_SCHEMA,
        "overall_status": "PASS",
        "decision": "AUTHORIZED_TO_INVOKE_HASH_BOUND_BACKEND",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "cumulative_target": spec.cumulative_target,
        "current_accepted": current_accepted,
        "max_concurrency": requested_concurrency,
        "authorization_receipt": _file_record(receipt_path),
        "backend_identity_manifest": _file_record(backend_path),
        "resource_snapshot": _file_record(snapshot_path),
        "command_argv_sha256": hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "shell_used": False,
        "simulator_action_taken_by_audit_write": False,
    }
    _write_json(out_dir / "STAGE_LAUNCH_AUDIT.json", audit)
    backend_out_dir.parent.mkdir(parents=True, exist_ok=True)
    with (out_dir / "backend.stdout.log").open("w", encoding="utf-8") as stdout, (
        out_dir / "backend.stderr.log"
    ).open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    if result.returncode != 0:
        raise StageLauncherError(
            f"hash-bound backend exited with return code {result.returncode}"
        )
    backend_receipt_path = backend_out_dir / "STAGE_RECEIPT.json"
    backend_receipt = _read_json(backend_receipt_path, "backend stage receipt")
    receipt_errors = validate_stage_receipt(
        backend_receipt,
        stage=stage,
        cumulative_target=spec.cumulative_target,
        backend_manifest_sha256=_sha256(backend_path),
        authorization_receipt_sha256=_sha256(receipt_path),
        prior_stage_receipt_sha256=(
            _sha256(stage_receipt_records[-1][0]) if stage_receipt_records else None
        ),
        verify_artifacts=True,
        artifact_root=backend_out_dir,
    )
    if receipt_errors:
        raise StageLauncherError(
            "backend stage receipt failed the exact stage contract: "
            + "; ".join(receipt_errors[:8])
        )
    shutil.copyfile(backend_receipt_path, out_dir / "STAGE_RECEIPT.json")
    (out_dir / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{_sha256(out_dir / name)}  {name}"
            for name in ("STAGE_LAUNCH_AUDIT.json", "STAGE_RECEIPT.json")
        )
        + "\n",
        encoding="utf-8",
    )
    return backend_receipt


def _validate_receipt(receipt: Mapping[str, Any], *, backend_sha256: str) -> None:
    required_true = (
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
        and receipt.get("backend_identity_manifest", {}).get("sha256") == backend_sha256
        and receipt.get("simulator_geometry_limit") == TARGET_ACCEPTED_GEOMETRIES
        and all(receipt.get(field) is True for field in required_true)
    ):
        raise StageLauncherError("FULL_CAMPAIGN receipt identity or permission mismatch")


def _validate_backend(backend: Mapping[str, Any], *, launcher_path: Path) -> None:
    errors = validate_backend_identity_manifest(backend, verify_files=True)
    if errors:
        raise StageLauncherError(
            "backend identity manifest failed validation: " + "; ".join(errors[:8])
        )
    launcher_record = backend.get("script_identities", {}).get("stage_launcher", {})
    if not (
        backend.get("campaign_id") == CAMPAIGN_ID
        and backend.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT
        and backend.get("backend_id") == PRODUCTION_BACKEND_ID
        and launcher_record.get("sha256") == _sha256(launcher_path)
    ):
        raise StageLauncherError("backend manifest or stage-launcher identity mismatch")


def _stage_command_template(backend: Mapping[str, Any], stage: str) -> list[str]:
    commands = backend.get("stage_commands")
    if not isinstance(commands, Mapping):
        raise StageLauncherError("backend manifest lacks stage_commands")
    record = commands.get(stage)
    if not isinstance(record, Mapping):
        raise StageLauncherError(f"backend manifest lacks {stage} command")
    argv = record.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise StageLauncherError(f"backend {stage} argv is invalid")
    for item in argv:
        for token in re_placeholder_tokens(item):
            if token not in ALLOWED_PLACEHOLDERS:
                raise StageLauncherError(f"backend argv uses unknown placeholder: {token}")
    return list(argv)


def _validate_backend_command_identity(
    command: list[str], backend: Mapping[str, Any], stage: str
) -> None:
    record = backend.get("stage_commands", {}).get(stage, {})
    executable_index = int(record.get("identity_argv_index", 0))
    if executable_index < 0 or executable_index >= len(command):
        raise StageLauncherError("backend identity_argv_index is invalid")
    path = Path(command[executable_index]).expanduser().resolve()
    expected = record.get("identity_sha256")
    if not path.is_file() or _sha256(path) != expected:
        raise StageLauncherError(f"backend {stage} executable identity mismatch")


def _substitute_argument(value: str, substitutions: Mapping[str, str]) -> str:
    result = value
    for key, replacement in substitutions.items():
        result = result.replace(key, replacement)
    if re_placeholder_tokens(result):
        raise StageLauncherError(f"unresolved backend placeholder in argument: {result}")
    return result


def re_placeholder_tokens(value: str) -> list[str]:
    import re

    return re.findall(r"\{[A-Za-z0-9_]+\}", value)


def _ordered_stage_receipt_records(
    campaign_root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    receipts: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_RECEIPT.json")):
        value = _read_json(path, "prior stage receipt")
        if value.get("overall_status") == "PASS":
            receipts.append((path, value))
    return receipts


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


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageLauncherError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageLauncherError(f"{label} is not a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
