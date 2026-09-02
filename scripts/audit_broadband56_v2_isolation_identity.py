#!/usr/bin/env python3
"""Audit one process-bound Broadband56 supervisor lease."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns import (  # noqa: E402
    broadband56_isolation_identity as isolation_identity,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--campaign-lock", required=True)
    parser.add_argument("--lease-registry-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--transient-helper-pid", action="append", type=int, default=[]
    )
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (
        OSError,
        json.JSONDecodeError,
        isolation_identity.IsolationIdentityError,
    ) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print(f"overall_status={result['overall_status']}")
    print(f"audit={result['path']}")
    return 0


def run(args: argparse.Namespace) -> dict[str, object]:
    lease_path = Path(args.lease).expanduser().resolve()
    backend_path = Path(args.backend_identity_manifest).expanduser().resolve()
    lock_path = Path(args.campaign_lock).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    registry_dir = Path(args.lease_registry_dir).expanduser().resolve()
    if out_dir.exists():
        raise isolation_identity.IsolationIdentityError(
            f"no-clobber output exists: {out_dir}"
        )
    lease = isolation_identity.read_json(lease_path, "supervisor lease")
    if not isolation_identity.file_record_matches(
        Path(isolation_identity.__file__).resolve(),
        lease.get("isolation_identity_module"),
    ):
        raise isolation_identity.IsolationIdentityError(
            "lease isolation-identity module binding mismatch"
        )
    if not isolation_identity.file_record_matches(
        Path(__file__).resolve(), lease.get("isolation_identity_auditor")
    ):
        raise isolation_identity.IsolationIdentityError(
            "lease isolation-identity auditor binding mismatch"
        )
    if not isolation_identity.file_record_matches(
        backend_path, lease.get("backend_identity_manifest")
    ):
        raise isolation_identity.IsolationIdentityError(
            "lease backend-manifest binding mismatch"
        )
    if (
        Path(str(lease.get("campaign_lock", {}).get("path") or ""))
        .expanduser()
        .resolve()
        != lock_path
    ):
        raise isolation_identity.IsolationIdentityError(
            "lease campaign-lock path mismatch"
        )
    backend_sha = isolation_identity.sha256(backend_path)
    expected_uid = lease.get("physical_process", {}).get("uid")
    if not isinstance(expected_uid, int) or isinstance(expected_uid, bool):
        raise isolation_identity.IsolationIdentityError(
            "lease physical-process UID is invalid"
        )
    processes = isolation_identity.enumerate_owner_processes(
        expected_uid,
        probe_pid=os.getpid(),
        transient_helper_pids=args.transient_helper_pid,
    )
    lock_held, lock_contents = isolation_identity.campaign_lock_state(lock_path)
    conflicting_lease_count = isolation_identity.count_conflicting_current_leases(
        lease_path,
        registry_dir=registry_dir,
    )
    isolation = isolation_identity.evaluate_process_isolation(
        lease=lease,
        processes=processes,
        backend_manifest_sha256=backend_sha,
        lock_held=lock_held,
        lock_contents=lock_contents,
        conflicting_lease_count=conflicting_lease_count,
    )
    payload = {
        "schema": isolation_identity.AUDIT_SCHEMA,
        "generated_utc": isolation_identity.utc_now(),
        "overall_status": (
            "PASS" if isolation["isolation_gate_pass"] else "FAIL"
        ),
        "decision": (
            "EXACTLY_ONE_BOUND_SUPERVISOR"
            if isolation["isolation_gate_pass"]
            else "FAIL_CLOSED_PROCESS_ISOLATION"
        ),
        "campaign_id": isolation_identity.CAMPAIGN_ID,
        "queue_id": isolation_identity.QUEUE_ID,
        "logical_supervisor_id": isolation_identity.SUPERVISOR_ID,
        "probe_pid": os.getpid(),
        "probe_uid": os.getuid(),
        "excluded_transient_helper_pids": sorted(
            set(args.transient_helper_pid)
        ),
        "owner_processes_scanned": len(processes),
        "process_command_lines_persisted": False,
        "lease": isolation_identity.file_record(lease_path),
        "backend_identity_manifest": isolation_identity.file_record(
            backend_path
        ),
        "campaign_lock_path": str(lock_path),
        "lease_registry_dir": str(registry_dir),
        "isolation": isolation,
        "simulator_action_taken": False,
        "campaign_data_modified": False,
    }
    out_dir.mkdir(parents=True, exist_ok=False)
    path = out_dir / "ISOLATION_IDENTITY_AUDIT.json"
    isolation_identity.write_json_exclusive(path, payload)
    return {"overall_status": payload["overall_status"], "path": path}


if __name__ == "__main__":
    raise SystemExit(main())
