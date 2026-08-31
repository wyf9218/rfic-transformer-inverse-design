#!/usr/bin/env python3
"""Verify one private broadband56 production-backend identity manifest.

This command is execution-free.  It recomputes every file identity named by
the private manifest and writes a no-clobber PASS/FAIL receipt.  It never
invokes Cadence, Calibre, EMX, a queue, or a supervisor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    BACKEND_VERIFICATION_PASS_DECISION,
    BACKEND_VERIFICATION_SCHEMA,
    validate_backend_identity_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = Path(args.backend_identity_manifest).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2

    manifest, read_error = _read_json(manifest_path)
    errors = ([read_error] if read_error else []) + validate_backend_identity_manifest(
        manifest,
        verify_files=True,
    )
    passed = not errors

    def lacks_errors(*fragments: str) -> bool:
        return read_error is None and not any(
            any(fragment in error for fragment in fragments) for error in errors
        )

    runtime_identities = manifest.get("runtime_identities")
    stage_profile_present = isinstance(runtime_identities, Mapping) and isinstance(
        runtime_identities.get("stage_execution_profile"), Mapping
    )

    receipt = {
        "schema": BACKEND_VERIFICATION_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": (
            BACKEND_VERIFICATION_PASS_DECISION
            if passed
            else "DO_NOT_USE_PRIVATE_PRODUCTION_BACKEND"
        ),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_identity_manifest": _file_record(manifest_path),
        "checks": {
            "manifest_parsed": read_error is None,
            "manifest_contract_complete": passed,
            "all_named_files_exist": lacks_errors(
                ".path is missing",
                ".path must be absolute",
            ),
            "all_named_file_sizes_match": lacks_errors(".size_bytes"),
            "all_named_file_sha256_values_match": lacks_errors(
                ".sha256 mismatches file",
                ".sha256 is not SHA-256",
            ),
            "stage_execution_profile_reparsed_and_validated": (
                stage_profile_present
                and lacks_errors("runtime_identities.stage_execution_profile")
            ),
            "all_required_executables_are_executable": lacks_errors(
                ".executable must be true",
                ".path is not executable",
            ),
            "all_stage_commands_hash_bound": lacks_errors(
                " identity path mismatch",
                " identity SHA-256 mismatch",
                "identity_argv_index must be zero",
                "argv interface mismatch",
            ),
            "all_stage_commands_shell_free": lacks_errors(".shell_used mismatch"),
            "all_ordered_stages_present": read_error is None
            and not any(
                error == "stage_commands keys do not exactly match the ordered stages"
                or (
                    error.startswith("stage_commands.")
                    and error.endswith("must be an object")
                )
                for error in errors
            ),
            "simulator_action_taken": False,
        },
        "errors": errors,
        "simulator_action_taken": False,
        "authorization_effect": "NONE_IDENTITY_VERIFICATION_ONLY",
    }
    out_dir.mkdir(parents=True, mode=0o700)
    receipt_path = out_dir / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json"
    _write_json(receipt_path, receipt)
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{_sha256(receipt_path)}  {receipt_path.name}\n",
        encoding="utf-8",
    )
    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"receipt={receipt_path}")
    return 0 if passed or args.no_fail_exit else 2


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.is_file():
        return {}, f"backend identity manifest is missing: {path}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"backend identity manifest cannot be read: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return {}, "backend identity manifest is not a JSON object"
    return value, None


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


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
