#!/usr/bin/env python3
"""Build one hash-bound private broadband56 backend identity manifest.

This command is identity-only. It reads and hashes explicitly named files,
validates the complete manifest in memory, and writes one no-clobber private
manifest. It never invokes Cadence, Calibre, EMX, a queue, or a supervisor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    PORT_AND_GROUNDING_CONTRACT,
    PRODUCTION_BACKEND_ID,
    expected_frequency_contract,
    expected_geometry_contract,
    expected_stage_contract,
    expected_terminal_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    BACKEND_MANIFEST_EFFECT,
    BACKEND_MANIFEST_SCHEMA,
    LABEL_CONTRACT,
    PRODUCTION_CHAIN,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    STAGE_COMMAND_ARGUMENTS,
    validate_backend_identity_manifest,
)


class ManifestBuildError(RuntimeError):
    """Raised when a private identity cannot be proved without execution."""


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
        manifest = build_manifest(args)
        errors = validate_backend_identity_manifest(manifest, verify_files=True)
        if errors:
            raise ManifestBuildError(
                "constructed manifest failed validation: " + "; ".join(errors[:12])
            )
        out_dir.mkdir(parents=True, mode=0o700)
        manifest_path = out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
        _write_json(manifest_path, manifest)
        reparsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        errors = validate_backend_identity_manifest(reparsed, verify_files=True)
        if errors:
            raise ManifestBuildError(
                "written manifest failed validation: " + "; ".join(errors[:12])
            )
        (out_dir / "SHA256SUMS.txt").write_text(
            f"{_sha256(manifest_path)}  {manifest_path.name}\n",
            encoding="utf-8",
        )
    except (ManifestBuildError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2

    print("overall_status=PASS")
    print("decision=USE_AS_PRIVATE_BACKEND_IDENTITY_MANIFEST")
    print(f"manifest={manifest_path}")
    print("simulator_action_taken=false")
    return 0


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    """Construct and self-validate a manifest without writing or executing."""

    preparation = {
        "preparation_receipt_sha256": args.preparation_receipt_sha256,
        "private_configuration_sha256": args.private_configuration_sha256,
        "historical_configuration_sha256": args.historical_configuration_sha256,
        "operational_policy_approval_receipt_sha256": (
            args.operational_policy_approval_receipt_sha256
        ),
    }
    for field, digest in preparation.items():
        if not _is_sha256(digest):
            raise ManifestBuildError(f"{field} is not lowercase SHA-256")

    scripts = {
        role: _identity_record(
            Path(getattr(args, f"script_{role}")),
            executable=role == "production_stage_backend",
        )
        for role in REQUIRED_SCRIPT_ROLES
    }
    runtimes = {
        role: _identity_record(
            Path(getattr(args, f"runtime_{role}")),
            executable=role in {"python_executable", "emx_wrapper"},
        )
        for role in REQUIRED_RUNTIME_ROLES
    }

    historical_receipts = [
        _pass_receipt_record(Path(path), label="historical backend PASS receipt")
        for path in args.historical_backend_pass_receipt
    ]
    if len(historical_receipts) < 2:
        raise ManifestBuildError(
            "at least two historical backend PASS receipts are required"
        )
    receipt_paths = [str(record["path"]) for record in historical_receipts]
    receipt_hashes = [str(record["sha256"]) for record in historical_receipts]
    if len(set(receipt_paths)) != len(receipt_paths):
        raise ManifestBuildError("historical backend receipt paths must be distinct")
    if len(set(receipt_hashes)) != len(receipt_hashes):
        raise ManifestBuildError("historical backend receipt bytes must be distinct")
    gds_receipt = _pass_receipt_record(
        Path(args.historical_gds_identity_pass_receipt),
        label="historical GDS identity PASS receipt",
    )

    backend = scripts["production_stage_backend"]
    stage_argv = [
        str(backend["path"]),
        *[
            item
            for flag, placeholder in STAGE_COMMAND_ARGUMENTS
            for item in (flag, placeholder)
        ],
    ]
    return {
        "schema": BACKEND_MANIFEST_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "manifest_effect": BACKEND_MANIFEST_EFFECT,
        "simulator_action_taken": False,
        "private_paths_published": False,
        "no_clobber_required": True,
        "execution_chain": list(PRODUCTION_CHAIN),
        "scientific_contract": {
            "frequency_contract": expected_frequency_contract(),
            "geometry_contract": expected_geometry_contract(),
            "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
            "label_contract": LABEL_CONTRACT,
            "terminal_contract": expected_terminal_contract(),
            "ordered_stages": expected_stage_contract(),
        },
        "preparation_bindings": preparation,
        "script_identities": scripts,
        "runtime_identities": runtimes,
        "stage_commands": {
            stage.name: {
                "argv": list(stage_argv),
                "identity_argv_index": 0,
                "identity_role": "production_stage_backend",
                "identity_sha256": backend["sha256"],
                "shell_used": False,
            }
            for stage in STAGES
        },
        "historical_backend_pass_receipts": historical_receipts,
        "historical_gds_identity_pass_receipt": gds_receipt,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--preparation-receipt-sha256", required=True)
    parser.add_argument("--private-configuration-sha256", required=True)
    parser.add_argument("--historical-configuration-sha256", required=True)
    parser.add_argument(
        "--operational-policy-approval-receipt-sha256",
        required=True,
    )
    for role in REQUIRED_SCRIPT_ROLES:
        parser.add_argument(
            f"--script-{role.replace('_', '-')}",
            dest=f"script_{role}",
            required=True,
        )
    for role in REQUIRED_RUNTIME_ROLES:
        parser.add_argument(
            f"--runtime-{role.replace('_', '-')}",
            dest=f"runtime_{role}",
            required=True,
        )
    parser.add_argument(
        "--historical-backend-pass-receipt",
        action="append",
        required=True,
    )
    parser.add_argument("--historical-gds-identity-pass-receipt", required=True)
    return parser.parse_args(argv)


def _identity_record(path: Path, *, executable: bool) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ManifestBuildError(f"identity file is missing: {resolved}")
    before = resolved.stat()
    digest = _sha256(resolved)
    after = resolved.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise ManifestBuildError(f"identity file changed while hashing: {resolved}")
    if executable and not os.access(resolved, os.X_OK):
        raise ManifestBuildError(f"required executable is not executable: {resolved}")
    record: dict[str, Any] = {
        "path": str(resolved),
        "size_bytes": after.st_size,
        "sha256": digest,
    }
    if executable:
        record["executable"] = True
    return record


def _pass_receipt_record(path: Path, *, label: str) -> dict[str, Any]:
    record = _identity_record(path, executable=False)
    resolved = Path(str(record["path"]))
    before = resolved.stat()
    payload = resolved.read_bytes()
    after = resolved.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise ManifestBuildError(f"{label} changed while reading: {resolved}")
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise ManifestBuildError(f"{label} changed after identity hashing: {resolved}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBuildError(f"{label} is not valid UTF-8 JSON: {resolved}") from exc
    if not isinstance(value, Mapping) or value.get("overall_status") != "PASS":
        raise ManifestBuildError(f"{label} is not top-level PASS: {resolved}")
    return {**record, "overall_status": "PASS"}


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
