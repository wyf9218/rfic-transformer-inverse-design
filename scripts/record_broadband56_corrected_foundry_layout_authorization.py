#!/usr/bin/env python3
"""Record exact-SHA approval of the corrected foundry-layout contract.

This command is deliberately execution-free. It verifies the complete
candidate and every file identity embedded in it, records the project-owner
approval in a new directory, and performs no signal, queue, Cadence, Calibre,
EMX, or neural-network action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_broadband56_corrected_foundry_layout_candidate import (  # noqa: E402
    CANDIDATE_SCHEMA,
    EXPECTED_CALIBRE_RULE_DECK_SHA256,
    EXPECTED_CONTROLLER_PID,
    EXACT_PUBLIC_CODE_COMMIT,
    REQUESTED_AUTHORIZATION_SCOPE,
    validate_candidate,
)


APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
)
APPROVAL_DECISION = (
    "APPROVE_RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_"
    "THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
)
FAIL_DECISION = "DO_NOT_RESTORE_OR_RUN_RESCUE_GOLDEN"
APPROVAL_SOURCE = "EXPLICIT_PROJECT_OWNER_INSTRUCTION"
EXPECTED_APPROVED_BY = "Yufeng Wang, project owner and project leader"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RECEIPT_NAME = "CORRECTED_FOUNDRY_LAYOUT_AUTHORIZATION_RECEIPT.json"


class RecorderError(RuntimeError):
    """Raised for errors that prevent even a complete audit receipt."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_path = Path(args.candidate).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2

    try:
        receipt = record_authorization(args, candidate_path=candidate_path)
        out_dir.mkdir(parents=True, mode=0o700)
        receipt_path = out_dir / RECEIPT_NAME
        _write_json(receipt_path, receipt)
        (out_dir / "SHA256SUMS.txt").write_text(
            f"{_sha256(receipt_path)}  {receipt_path.name}\n",
            encoding="utf-8",
        )
    except (OSError, RecorderError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2

    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"receipt={receipt_path}")
    print("simulator_action_taken=no")
    return 0 if receipt["overall_status"] == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-utc", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def record_authorization(
    args: argparse.Namespace,
    *,
    candidate_path: Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    candidate = _read_json(candidate_path, checks, "candidate")
    expected_sha = str(args.candidate_sha256).strip().lower()
    actual_sha = _sha256(candidate_path) if candidate_path.is_file() else None
    checks.extend(
        [
            _check(
                "candidate_sha256_argument",
                SHA256_PATTERN.fullmatch(expected_sha) is not None,
                expected_sha,
            ),
            _check(
                "candidate_sha256_exact_bytes",
                actual_sha == expected_sha,
                f"expected={expected_sha}, actual={actual_sha}",
            ),
            _check(
                "candidate_schema",
                candidate.get("schema") == CANDIDATE_SCHEMA,
                candidate.get("schema"),
            ),
            _check(
                "candidate_pending_exact_owner_approval",
                candidate.get("approval_status")
                == "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL",
                candidate.get("approval_status"),
            ),
            _check(
                "candidate_requested_scope",
                candidate.get("requested_authorization_scope")
                == REQUESTED_AUTHORIZATION_SCOPE,
                candidate.get("requested_authorization_scope"),
            ),
            _check(
                "candidate_static_contract",
                not validate_candidate(candidate),
                "; ".join(validate_candidate(candidate)) or "PASS",
            ),
            _check(
                "approved_by_exact_project_owner",
                str(args.approved_by).strip() == EXPECTED_APPROVED_BY,
                str(args.approved_by).strip(),
            ),
            _check(
                "approved_utc_timezone_aware",
                _is_timezone_aware(str(args.approved_utc).strip()),
                str(args.approved_utc).strip(),
            ),
            _check(
                "approval_reference_explicit",
                bool(str(args.approval_reference).strip()),
                str(args.approval_reference).strip() or "missing",
            ),
        ]
    )

    bound_records: dict[str, dict[str, Any]] = {}
    for label, value in _candidate_file_records(candidate):
        record, passed, detail = _verify_file_record(value)
        checks.append(_check(f"bound_file::{label}", passed, detail))
        if record is not None:
            bound_records[label] = record

    runtime_record = candidate.get("corrected_public_runtime")
    runtime_snapshot = _git_snapshot(
        Path(str(runtime_record.get("path") or "")).expanduser().resolve()
        if isinstance(runtime_record, Mapping)
        else Path()
    )
    checks.append(
        _check(
            "corrected_runtime_exact_identity",
            isinstance(runtime_record, Mapping)
            and runtime_snapshot == dict(runtime_record)
            and runtime_snapshot.get("head_commit") == EXACT_PUBLIC_CODE_COMMIT,
            json.dumps(runtime_snapshot, sort_keys=True),
        )
    )

    live = _live_controller_snapshot(EXPECTED_CONTROLLER_PID)
    checks.extend(
        [
            _check(
                "authoritative_controller_alive",
                live.get("authoritative_controller_alive") is True,
                live.get("authoritative_controller_alive"),
            ),
            _check(
                "one_controller_invariant",
                live.get("controller_count") == 1,
                live.get("controller_count"),
            ),
            _check(
                "zero_project_simulator_processes_before_transition",
                live.get("project_active_simulator_count") == 0,
                live.get("project_active_simulator_count"),
            ),
        ]
    )

    passed = bool(checks) and all(item["pass"] for item in checks)
    return {
        "schema": APPROVAL_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": APPROVAL_DECISION if passed else FAIL_DECISION,
        "authorization_scope": REQUESTED_AUTHORIZATION_SCOPE,
        "approved_by": str(args.approved_by).strip(),
        "approved_utc": str(args.approved_utc).strip(),
        "approval_source": APPROVAL_SOURCE,
        "approval_reference": str(args.approval_reference).strip(),
        "approved_candidate": {
            "path": str(candidate_path),
            "size_bytes": candidate_path.stat().st_size
            if candidate_path.is_file()
            else None,
            "sha256": actual_sha,
        },
        "verified_bound_files": bound_records,
        "corrected_public_runtime": runtime_snapshot,
        "controller_snapshot_at_recording": live,
        "restore_corrected_foundry_layout_contract_authorized": passed,
        "reuse_existing_queue_only": passed,
        "reuse_existing_authoritative_supervisor_only": passed,
        "duplicate_queue_controller_supervisor_or_campaign_authorized": False,
        "one_corrected_rescue_golden_authorized": passed,
        "cadence_authorized_for_rescue_golden": passed,
        "generated_layout_audit_required_before_calibre": True,
        "calibre_authorized_for_rescue_golden": passed,
        "zero_blocking_calibre_required_before_emx": True,
        "emx_authorized_only_after_zero_blocking_calibre": passed,
        "fresh_exact_56_point_four_port_s4p_required": True,
        "automatic_post_golden_full_campaign_continuation_authorized": passed,
        "accepted_unique_geometry_target": 200_000,
        "nn_training_authorized": False,
        "checks": checks,
        "execution_effect": "AUTHORIZATION_RECORDED_NO_SIMULATOR_ACTION",
        "simulator_action_taken_by_recorder": False,
        "authorization_boundary": (
            "PASS authorizes only the exact corrected foundry-layout restoration, one "
            "rescue Golden through fresh EMX after zero-blocking Calibre, and the "
            "pre-existing FULL_CAMPAIGN chain. It forbids duplicate control-plane "
            "processes, DRC waiver, historical S4P labels, manual GDS edits, geometry "
            "bound expansion, and neural-network training."
        ),
    }


def _candidate_file_records(
    candidate: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    records: list[tuple[str, Mapping[str, Any]]] = []
    direct = (
        "previous_private_configuration",
        "corrected_private_configuration",
        "configuration_diff",
        "calibre_rule_deck_identity",
        "existing_full_campaign_authorization",
    )
    for name in direct:
        value = candidate.get(name)
        if isinstance(value, Mapping):
            records.append((name, value))
    private = candidate.get("private_evidence")
    if isinstance(private, Mapping):
        for name, value in sorted(private.items()):
            if isinstance(value, Mapping):
                records.append((f"private_evidence.{name}", value))
    return records


def _verify_file_record(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, bool, str]:
    path_value = value.get("path")
    expected_sha = value.get("sha256")
    expected_size = value.get("size_bytes")
    if not isinstance(path_value, str) or not path_value:
        return None, False, "missing path"
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        return None, False, f"missing file: {path}"
    actual_sha = _sha256(path)
    actual_size = path.stat().st_size
    passed = (
        isinstance(expected_sha, str)
        and SHA256_PATTERN.fullmatch(expected_sha) is not None
        and actual_sha == expected_sha
        and actual_size == expected_size
    )
    record = {"path": str(path), "size_bytes": actual_size, "sha256": actual_sha}
    return (
        record,
        passed,
        f"expected_sha={expected_sha}, actual_sha={actual_sha}, "
        f"expected_size={expected_size}, actual_size={actual_size}",
    )


def _git_snapshot(repository: Path) -> dict[str, Any]:
    if not repository.is_dir():
        return {"path": str(repository), "error": "missing runtime repository"}
    try:
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"path": str(repository), "error": f"{type(exc).__name__}: {exc}"}
    return {
        "path": str(repository),
        "head_commit": head,
        "tree_sha1": tree,
        "working_tree_clean": not status,
    }


def _live_controller_snapshot(pid: int) -> dict[str, Any]:
    controller_count = 0
    simulator_count = 0
    authoritative_alive = False
    state = None
    for entry in Path("/proc").iterdir() if Path("/proc").is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        if "run_broadband56_v2_authorized_queue_controller.py" in cmdline:
            controller_count += 1
        if (
            "broadband56_real_emx_balanced200k_tsmc65_v2" in cmdline
            and any(token in cmdline.lower() for token in ("cadence", "calibre", "emx"))
            and "run_broadband56_v2_authorized_queue_controller.py" not in cmdline
        ):
            simulator_count += 1
        if int(entry.name) == pid:
            authoritative_alive = (
                "run_broadband56_v2_authorized_queue_controller.py" in cmdline
            )
            try:
                state = (entry / "stat").read_text(encoding="utf-8").split()[2]
            except (OSError, IndexError):
                state = None
    return {
        "authoritative_controller_pid": pid,
        "authoritative_controller_alive": authoritative_alive,
        "authoritative_controller_state": state,
        "controller_count": controller_count,
        "project_active_simulator_count": simulator_count,
    }


def _read_json(
    path: Path,
    checks: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    checks.append(_check(f"{label}_exists", path.is_file(), str(path)))
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        checks.append(_check(f"{label}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{label}_parses", isinstance(value, dict), type(value).__name__))
    return value if isinstance(value, dict) else {}


def _is_timezone_aware(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


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
