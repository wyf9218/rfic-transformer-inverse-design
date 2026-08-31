#!/usr/bin/env python3
"""Build one no-clobber private broadband56 stage-execution profile.

The input command plan may contain private runtime arguments and must remain
private.  This builder validates exact role order, argument placeholders,
result paths, and the shell-free contract, then writes a canonical profile and
a hash-bound build receipt.  It never launches a process or simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    REQUIRED_SCRIPT_ROLES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (  # noqa: E402
    StageExecutionProfileError,
    profile_from_command_plan,
    validate_execution_profile,
)


BUILD_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_stage_execution_profile_build_receipt.v1"
)
BUILD_DECISION = "USE_AS_PRIVATE_STAGE_EXECUTION_PROFILE"


class ProfileBuildError(RuntimeError):
    """Raised when a private execution profile cannot be built safely."""


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
        plan_path = Path(args.command_plan).expanduser()
        plan, plan_identity = _read_stable_json(plan_path)
        profile = profile_from_command_plan(plan)
        errors = validate_execution_profile(
            profile,
            backend_manifest={
                "script_identities": {
                    role: {"role": role} for role in REQUIRED_SCRIPT_ROLES
                }
            },
        )
        if errors:
            raise ProfileBuildError(
                "command plan failed execution-profile validation: "
                + "; ".join(errors[:16])
            )

        out_dir.mkdir(parents=True, mode=0o700)
        profile_path = out_dir / "STAGE_EXECUTION_PROFILE.json"
        _write_json(profile_path, profile)
        profile_identity = _identity_record(profile_path)
        receipt_path = out_dir / "STAGE_EXECUTION_PROFILE_BUILD_RECEIPT.json"
        receipt = {
            "schema": BUILD_RECEIPT_SCHEMA,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall_status": "PASS",
            "decision": BUILD_DECISION,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_id": PRODUCTION_BACKEND_ID,
            "command_plan": plan_identity,
            "stage_execution_profile": profile_identity,
            "checks": {
                "command_plan_stable_while_reading": True,
                "exact_campaign_and_contract_identity": True,
                "all_stage_role_orders_exact": True,
                "all_placeholders_authorized": True,
                "all_result_and_receipt_paths_safe_relative": True,
                "all_commands_shell_free": True,
                "profile_reparsed_and_validated": True,
                "simulator_action_taken": False,
            },
            "simulator_action_taken": False,
            "private_artifact_do_not_publish": True,
        }
        _write_json(receipt_path, receipt)
        reparsed = json.loads(profile_path.read_text(encoding="utf-8"))
        errors = validate_execution_profile(
            reparsed,
            backend_manifest={
                "script_identities": {
                    role: {"role": role} for role in REQUIRED_SCRIPT_ROLES
                }
            },
        )
        if errors:
            raise ProfileBuildError(
                "written profile failed validation: " + "; ".join(errors[:16])
            )
        (out_dir / "SHA256SUMS.txt").write_text(
            "\n".join(
                f"{_sha256(path)}  {path.name}"
                for path in (profile_path, receipt_path)
            )
            + "\n",
            encoding="utf-8",
        )
    except (
        OSError,
        ProfileBuildError,
        StageExecutionProfileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        if out_dir.is_dir():
            _remove_partial_output(out_dir)
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2

    print("overall_status=PASS")
    print(f"decision={BUILD_DECISION}")
    print(f"profile={profile_path}")
    print(f"receipt={receipt_path}")
    print("simulator_action_taken=false")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command-plan", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _read_stable_json(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise ProfileBuildError(f"command plan is missing, empty, or a symlink: {path}")
    resolved = path.resolve()
    before = resolved.stat()
    payload = resolved.read_bytes()
    after = resolved.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise ProfileBuildError("command plan changed while reading")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ProfileBuildError("command plan is not a JSON object")
    return value, {
        "path": str(resolved),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _identity_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    before = resolved.stat()
    digest = _sha256(resolved)
    after = resolved.stat()
    if _stat_identity(before) != _stat_identity(after):
        raise ProfileBuildError(f"output changed while hashing: {resolved.name}")
    return {
        "path": str(resolved),
        "size_bytes": after.st_size,
        "sha256": digest,
    }


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _remove_partial_output(out_dir: Path) -> None:
    for path in sorted(out_dir.iterdir(), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            _remove_partial_output(path)
    out_dir.rmdir()


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
