"""Process-bound isolation evidence for the Broadband56 controller."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
QUEUE_ID = "b56-v2-queue-20260901T184307Z"
SUPERVISOR_ID = "b56-v2-controller-3184781-20260901T184307Z"
LEASE_SCHEMA = "rfic_transformer.broadband56_v2_supervisor_lease.v1"
AUDIT_SCHEMA = "rfic_transformer.broadband56_v2_isolation_identity_audit.v1"
LEASE_VALIDITY_MODEL = (
    "PID_UID_START_TICKS_BOOT_ID_COMMAND_BACKEND_AND_EXCLUSIVE_FLOCK"
)

SUPERVISOR_MARKERS = (
    "run_broadband56_v2_capacity_supervisor.py",
    "run_broadband56_v2_authorized_queue_controller.py",
    "run_broadband56_v2_rebound_queue_controller.py",
    "run_broadband56_v2_swap_override_queue_controller.py",
    "launch_repaired_backend",
    "launch_isolation_hotfix",
    "launch_broadband56_v2_supervisor_recovery",
)
RUNNER_MARKERS = (
    "run_broadband56_v2_stage_launcher.py",
    "run_broadband56_v2_production_stage_backend.py",
    "run_candidate_queue_dataset_parallel.py",
    "run_real_emx_accepted_increment_round",
)
CADENCE_MARKERS = ("virtuoso", "strmout")
CALIBRE_MARKERS = ("calibre",)
EMX_MARKERS = ("emx_cae_singularity", "/emx ", " emx ")
PROJECT_EXECUTION_MARKERS = (
    *SUPERVISOR_MARKERS,
    *RUNNER_MARKERS,
    *CADENCE_MARKERS,
    *CALIBRE_MARKERS,
    *EMX_MARKERS,
)


class IsolationIdentityError(RuntimeError):
    """Fail-closed isolation identity error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def file_record_matches(path: Path, record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    resolved = path.resolve()
    return (
        resolved.is_file()
        and Path(str(record.get("path") or "")).expanduser().resolve()
        == resolved
        and record.get("size_bytes") == resolved.stat().st_size
        and record.get("sha256") == sha256(resolved)
    )


def read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IsolationIdentityError(f"{label} root is not an object")
    return value


def write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _boot_id(proc_root: Path) -> str:
    path = proc_root / "sys/kernel/random/boot_id"
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise IsolationIdentityError("Linux boot ID is empty")
    return value


def read_process_identity(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    executable_hash_cache: dict[Path, str] | None = None,
    include_arguments: bool = False,
) -> dict[str, Any] | None:
    proc = proc_root / str(pid)
    try:
        stat_text = (proc / "stat").read_text(encoding="utf-8")
        stat_tail = stat_text.rsplit(")", 1)[1].split()
        start_ticks = int(stat_tail[19])
        parent_pid = int(stat_tail[1])
        state = stat_tail[0]
        uid = proc.stat().st_uid
        command_bytes = (proc / "cmdline").read_bytes()
        executable = (proc / "exe").resolve(strict=True)
    except (FileNotFoundError, PermissionError, OSError, IndexError, ValueError):
        return None
    if state == "Z" or not command_bytes:
        return None
    command_text = command_bytes.replace(b"\0", b" ").decode(
        "utf-8", "replace"
    ).strip()
    executable_sha = None
    if executable_hash_cache is not None:
        executable_sha = executable_hash_cache.get(executable)
    if executable_sha is None:
        executable_sha = sha256(executable)
        if executable_hash_cache is not None:
            executable_hash_cache[executable] = executable_sha
    result = {
        "pid": pid,
        "parent_pid": parent_pid,
        "uid": uid,
        "state": state,
        "start_ticks": start_ticks,
        "boot_id": _boot_id(proc_root),
        "command_line_sha256": hashlib.sha256(command_bytes).hexdigest(),
        "command_text": command_text,
        "executable_path": str(executable),
        "executable_sha256": executable_sha,
    }
    if include_arguments:
        result["command_argv"] = [part.decode("utf-8", "strict")
                                  for part in command_bytes.rstrip(b"\0").split(b"\0")]
    return result


def enumerate_owner_processes(
    uid: int,
    *,
    probe_pid: int,
    transient_helper_pids: Iterable[int] = (),
    proc_root: Path = Path("/proc"),
    include_arguments: bool = False,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    executable_hash_cache: dict[Path, str] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        record = read_process_identity(
            pid,
            proc_root=proc_root,
            executable_hash_cache=executable_hash_cache,
            include_arguments=include_arguments,
        )
        if record is None and include_arguments:
            record = read_container_transit_identity(pid, proc_root=proc_root)
            if record is not None and record["uid"] in {0, uid}:
                records.append(record)
            continue
        if record is not None and record["uid"] == uid:
            records.append(record)
    return filter_probe_helpers(
        records,
        probe_pid=probe_pid,
        transient_helper_pids=transient_helper_pids,
    )


def read_container_transit_identity(pid: int, *, proc_root: Path) -> dict[str, Any] | None:
    """Retain a protected Singularity parent edge, never solver authority."""
    proc = proc_root / str(pid)
    try:
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        command = (proc / "cmdline").read_bytes()
        if ((proc / "comm").read_text().strip() != "starter-suid"
                or command.replace(b"\0", b" ").strip() != b"Singularity runtime parent"
                or fields[0] in {"Z", "X"}):
            return None
        return {"pid": pid, "parent_pid": int(fields[1]), "uid": proc.stat().st_uid,
                "start_ticks": int(fields[19]), "state": fields[0], "boot_id": _boot_id(proc_root),
                "command_line_sha256": hashlib.sha256(command).hexdigest(),
                "command_text": "Singularity runtime parent", "protected_container_transit": True,
                "counted_as_native_solver": False}
    except (OSError, ValueError, IndexError):
        return None


def filter_probe_helpers(
    processes: Iterable[Mapping[str, Any]],
    *,
    probe_pid: int,
    transient_helper_pids: Iterable[int] = (),
) -> list[dict[str, Any]]:
    """Remove only the exact probe and its explicitly declared helpers."""
    excluded = {int(probe_pid), *(int(pid) for pid in transient_helper_pids)}
    return sorted(
        (dict(item) for item in processes if int(item["pid"]) not in excluded),
        key=lambda item: int(item["pid"]),
    )


def build_supervisor_lease(
    *,
    physical_pid: int,
    backend_identity_manifest: Path,
    queue_entry: Path,
    supervisor_identity: Path,
    operational_handoff_receipt: Path,
    campaign_lock: Path,
    lease_generation: int,
    isolation_identity_auditor: Path,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    if lease_generation < 1:
        raise IsolationIdentityError("lease generation must be positive")
    process = read_process_identity(physical_pid, proc_root=proc_root)
    if process is None:
        raise IsolationIdentityError("current supervisor process is unavailable")
    queue = read_json(queue_entry, "queue entry")
    supervisor = read_json(supervisor_identity, "supervisor identity")
    handoff = read_json(operational_handoff_receipt, "handoff receipt")
    backend_record = file_record(backend_identity_manifest)
    if not (
        _campaign_process(str(process.get("command_text", "")))
        and _contains_marker(
            str(process.get("command_text", "")), SUPERVISOR_MARKERS
        )
        and queue.get("campaign_id") == CAMPAIGN_ID
        and queue.get("queue_id") == QUEUE_ID
        and queue.get("one_authoritative_supervisor") == SUPERVISOR_ID
        and queue.get("backend_identity_manifest", {}).get("sha256")
        == backend_record["sha256"]
        and supervisor.get("campaign_id") == CAMPAIGN_ID
        and supervisor.get("controller_id") == SUPERVISOR_ID
        and supervisor.get("controller_pid")
        == handoff.get("old_process_pid")
        and supervisor.get("backend_identity_manifest", {}).get("sha256")
        == backend_record["sha256"]
        and handoff.get("campaign_id") == CAMPAIGN_ID
        and handoff.get("queue_id") == QUEUE_ID
        and handoff.get("supervisor_id") == SUPERVISOR_ID
        and handoff.get("new_process_pid") == physical_pid
        and isinstance(handoff.get("new_process_identity"), Mapping)
        and process_identity_matches(
            process, handoff["new_process_identity"]
        )
        and handoff.get("new_process_is_sole_authoritative_supervisor") is True
        and handoff.get("supervisor_count_after") == 1
        and handoff.get("overlap_seconds") == 0
    ):
        raise IsolationIdentityError("queue, supervisor, backend, or handoff mismatch")
    return {
        "schema": LEASE_SCHEMA,
        "generated_utc": utc_now(),
        "validity_state": "CURRENT",
        "expires_utc": None,
        "validity_model": LEASE_VALIDITY_MODEL,
        "lease_generation": lease_generation,
        "lease_nonce": uuid.uuid4().hex,
        "campaign_id": CAMPAIGN_ID,
        "queue_id": QUEUE_ID,
        "logical_supervisor_id": SUPERVISOR_ID,
        "physical_process": _public_process_record(process),
        "backend_identity_manifest": backend_record,
        "queue_entry": file_record(queue_entry),
        "supervisor_identity": file_record(supervisor_identity),
        "operational_handoff_receipt": file_record(
            operational_handoff_receipt
        ),
        "isolation_identity_module": file_record(Path(__file__)),
        "isolation_identity_auditor": file_record(
            isolation_identity_auditor
        ),
        "campaign_lock": {
            "path": str(campaign_lock.resolve()),
            "expected_contents": SUPERVISOR_ID,
            "exclusive_flock_required": True,
        },
        "pid_reuse_protection": {
            "uid_required": True,
            "start_ticks_required": True,
            "boot_id_required": True,
            "command_line_sha256_required": True,
            "executable_sha256_required": True,
        },
        "renewal_policy": "PROCESS_BOUND_NO_WALL_CLOCK_EXPIRY",
    }


def build_restarted_supervisor_lease(
    *,
    physical_pid: int,
    backend_identity_manifest: Path,
    queue_entry: Path,
    supervisor_identity: Path,
    operational_handoff_receipt: Path,
    prior_supervisor_lease: Path,
    restart_failure_receipt: Path,
    campaign_lock: Path,
    lease_generation: int,
    isolation_identity_auditor: Path,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Build a process-bound lease after a recorded supervisor failure.

    The immutable queue identity names the original physical controller. A
    durable restart therefore chains the new handoff to the immediately prior
    process-bound lease instead of rewriting that historical identity.
    """
    if lease_generation < 2:
        raise IsolationIdentityError(
            "restart lease generation must be at least two"
        )
    process = read_process_identity(physical_pid, proc_root=proc_root)
    if process is None:
        raise IsolationIdentityError("restart supervisor process is unavailable")
    queue = read_json(queue_entry, "queue entry")
    supervisor = read_json(supervisor_identity, "supervisor identity")
    handoff = read_json(operational_handoff_receipt, "restart handoff receipt")
    prior_lease = read_json(prior_supervisor_lease, "prior supervisor lease")
    failure = read_json(restart_failure_receipt, "restart failure receipt")
    backend_record = file_record(backend_identity_manifest)
    queue_record = file_record(queue_entry)
    supervisor_record = file_record(supervisor_identity)
    prior_process = prior_lease.get("physical_process")
    prior_pid = (
        int(prior_process.get("pid", 0))
        if isinstance(prior_process, Mapping)
        else 0
    )
    observed_prior = read_process_identity(prior_pid, proc_root=proc_root)
    prior_generation = prior_lease.get("lease_generation")
    if not (
        _campaign_process(str(process.get("command_text", "")))
        and _contains_marker(
            str(process.get("command_text", "")), SUPERVISOR_MARKERS
        )
        and prior_lease.get("schema") == LEASE_SCHEMA
        and prior_lease.get("validity_state") == "CURRENT"
        and prior_lease.get("campaign_id") == CAMPAIGN_ID
        and prior_lease.get("queue_id") == QUEUE_ID
        and prior_lease.get("logical_supervisor_id") == SUPERVISOR_ID
        and isinstance(prior_generation, int)
        and not isinstance(prior_generation, bool)
        and prior_generation == lease_generation - 1
        and isinstance(prior_process, Mapping)
        and prior_pid > 0
        and prior_lease.get("backend_identity_manifest", {}).get("sha256")
        == backend_record["sha256"]
        and prior_lease.get("queue_entry") == queue_record
        and prior_lease.get("supervisor_identity") == supervisor_record
        and observed_prior is None
        and queue.get("campaign_id") == CAMPAIGN_ID
        and queue.get("queue_id") == QUEUE_ID
        and queue.get("one_authoritative_supervisor") == SUPERVISOR_ID
        and queue.get("backend_identity_manifest", {}).get("sha256")
        == backend_record["sha256"]
        and supervisor.get("campaign_id") == CAMPAIGN_ID
        and supervisor.get("controller_id") == SUPERVISOR_ID
        and supervisor.get("backend_identity_manifest", {}).get("sha256")
        == backend_record["sha256"]
        and handoff.get("campaign_id") == CAMPAIGN_ID
        and handoff.get("queue_id") == QUEUE_ID
        and handoff.get("supervisor_id") == SUPERVISOR_ID
        and handoff.get("old_process_pid") == prior_pid
        and handoff.get("old_process_identity") == prior_process
        and handoff.get("old_process_confirmed_exited") is True
        and handoff.get("new_process_pid") == physical_pid
        and isinstance(handoff.get("new_process_identity"), Mapping)
        and process_identity_matches(process, handoff["new_process_identity"])
        and handoff.get("new_process_is_sole_authoritative_supervisor") is True
        and handoff.get("supervisor_count_after") == 1
        and handoff.get("overlap_seconds") == 0
        and handoff.get("new_queue_or_campaign_created") is False
        and handoff.get("nn_training_started") is False
        and failure.get("overall_status") == "PASS"
        and failure.get("failed_physical_pid") == prior_pid
        and failure.get("failed_physical_pid_alive") is False
        and failure.get("active_simulator_jobs") == 0
        and failure.get("current_accepted") == 0
        and failure.get("current_feature_rows") == 0
        and failure.get("simulator_action_taken") is False
    ):
        raise IsolationIdentityError(
            "queue, prior lease, failure, backend, or restart handoff mismatch"
        )
    return {
        "schema": LEASE_SCHEMA,
        "generated_utc": utc_now(),
        "validity_state": "CURRENT",
        "expires_utc": None,
        "validity_model": LEASE_VALIDITY_MODEL,
        "lease_generation": lease_generation,
        "lease_nonce": uuid.uuid4().hex,
        "campaign_id": CAMPAIGN_ID,
        "queue_id": QUEUE_ID,
        "logical_supervisor_id": SUPERVISOR_ID,
        "physical_process": _public_process_record(process),
        "backend_identity_manifest": backend_record,
        "queue_entry": queue_record,
        "supervisor_identity": supervisor_record,
        "operational_handoff_receipt": file_record(
            operational_handoff_receipt
        ),
        "prior_supervisor_lease": file_record(prior_supervisor_lease),
        "restart_failure_receipt": file_record(restart_failure_receipt),
        "restart_chain_validated": True,
        "isolation_identity_module": file_record(Path(__file__)),
        "isolation_identity_auditor": file_record(
            isolation_identity_auditor
        ),
        "campaign_lock": {
            "path": str(campaign_lock.resolve()),
            "expected_contents": SUPERVISOR_ID,
            "exclusive_flock_required": True,
        },
        "pid_reuse_protection": {
            "uid_required": True,
            "start_ticks_required": True,
            "boot_id_required": True,
            "command_line_sha256_required": True,
            "executable_sha256_required": True,
        },
        "renewal_policy": "PROCESS_BOUND_NO_WALL_CLOCK_EXPIRY",
    }


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _lease_is_current(lease: Mapping[str, Any], *, now: datetime) -> bool:
    if lease.get("validity_state") != "CURRENT":
        return False
    generated = _parse_utc(lease.get("generated_utc"))
    if generated is None or generated > now:
        return False
    expires = lease.get("expires_utc")
    if expires is None:
        return True
    parsed = _parse_utc(expires)
    return parsed is not None and parsed > now


def process_identity_matches(
    observed: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    fields = (
        "pid",
        "uid",
        "start_ticks",
        "boot_id",
        "command_line_sha256",
        "executable_path",
        "executable_sha256",
    )
    return all(observed.get(field) == expected.get(field) for field in fields)


def _contains_marker(command: str, markers: Iterable[str]) -> bool:
    lowered = command.lower()
    return any(marker.lower() in lowered for marker in markers)


def _campaign_process(command: str) -> bool:
    return CAMPAIGN_ID.lower() in command.lower()


def _project_execution_process(command: str) -> bool:
    return _campaign_process(command) and _contains_marker(
        command, PROJECT_EXECUTION_MARKERS
    )


def evaluate_process_isolation(
    *,
    lease: Mapping[str, Any],
    processes: Iterable[Mapping[str, Any]],
    backend_manifest_sha256: str,
    lock_held: bool,
    lock_contents: str,
    conflicting_lease_count: int = 0,
    now: datetime | None = None,
    running_stage_history: Path | None = None,
) -> dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    process_records = [dict(item) for item in processes]
    expected = lease.get("physical_process")
    expected_pid = expected.get("pid") if isinstance(expected, Mapping) else None
    by_pid = {item.get("pid"): item for item in process_records}
    observed_expected = by_pid.get(expected_pid)
    conflict_count_valid = (
        isinstance(conflicting_lease_count, int)
        and not isinstance(conflicting_lease_count, bool)
        and conflicting_lease_count >= 0
    )

    lease_identity_valid = (
        lease.get("schema") == LEASE_SCHEMA
        and lease.get("campaign_id") == CAMPAIGN_ID
        and lease.get("queue_id") == QUEUE_ID
        and lease.get("logical_supervisor_id") == SUPERVISOR_ID
        and lease.get("validity_model") == LEASE_VALIDITY_MODEL
        and isinstance(lease.get("lease_generation"), int)
        and not isinstance(lease.get("lease_generation"), bool)
        and int(lease.get("lease_generation", 0)) >= 1
        and isinstance(lease.get("lease_nonce"), str)
        and bool(lease.get("lease_nonce"))
        and lease.get("backend_identity_manifest", {}).get("sha256")
        == backend_manifest_sha256
        and lease.get("campaign_lock", {}).get("expected_contents")
        == SUPERVISOR_ID
        and lease.get("campaign_lock", {}).get("exclusive_flock_required")
        is True
        and _lease_is_current(lease, now=current_time)
        and conflict_count_valid
        and conflicting_lease_count == 0
    )
    expected_process_valid = (
        lease_identity_valid
        and isinstance(expected, Mapping)
        and observed_expected is not None
        and process_identity_matches(observed_expected, expected)
        and _campaign_process(str(observed_expected.get("command_text", "")))
        and _contains_marker(
            str(observed_expected.get("command_text", "")),
            SUPERVISOR_MARKERS,
        )
        and lock_held
        and lock_contents.strip() == SUPERVISOR_ID
    )

    owned_pids: set[int] = set()
    native_counts = dict.fromkeys(("cadence", "calibre", "emx"), 0)
    running_evidence = []
    running_error = None
    if running_stage_history is not None:
        try:
            if not expected_process_valid:
                raise IsolationIdentityError("running-stage supervisor identity is invalid")
            from .broadband56_running_isolation import running_stage_children

            running = running_stage_children(
                history_path=running_stage_history, lease=lease, processes=process_records)
            owned_pids = set(running["owned_pids"])
            native_counts.update(running["native_counts"])
            running_evidence = running["evidence"]
        except (OSError, ValueError, KeyError, TypeError, IsolationIdentityError) as exc:
            running_error = f"{type(exc).__name__}: {exc}"

    extra_supervisors = [
        item
        for item in process_records
        if item.get("pid") != expected_pid
        and _campaign_process(str(item.get("command_text", "")))
        and _contains_marker(
            str(item.get("command_text", "")), SUPERVISOR_MARKERS
        )
    ]
    supervisor_candidate_pids = {
        expected_pid,
        *(item.get("pid") for item in extra_supervisors),
        *owned_pids,
    }
    runners = [
        item
        for item in process_records
        if item.get("pid") not in supervisor_candidate_pids
        and _campaign_process(str(item.get("command_text", "")))
        and _contains_marker(str(item.get("command_text", "")), RUNNER_MARKERS)
    ]
    cadence = [
        item
        for item in process_records
        if item.get("pid") not in supervisor_candidate_pids
        and _campaign_process(str(item.get("command_text", "")))
        and _contains_marker(str(item.get("command_text", "")), CADENCE_MARKERS)
    ]
    calibre = [
        item
        for item in process_records
        if item.get("pid") not in supervisor_candidate_pids
        and _campaign_process(str(item.get("command_text", "")))
        and _contains_marker(str(item.get("command_text", "")), CALIBRE_MARKERS)
    ]
    emx = [
        item
        for item in process_records
        if item.get("pid") not in supervisor_candidate_pids
        and _campaign_process(str(item.get("command_text", "")))
        and _contains_marker(str(item.get("command_text", "")), EMX_MARKERS)
    ]
    known_pids = {
        expected_pid,
        *owned_pids,
        *(item.get("pid") for item in extra_supervisors),
        *(item.get("pid") for item in runners),
        *(item.get("pid") for item in cadence),
        *(item.get("pid") for item in calibre),
        *(item.get("pid") for item in emx),
    }
    unexpected = [
        item
        for item in process_records
        if item.get("pid") not in known_pids
        and _project_execution_process(str(item.get("command_text", "")))
    ]
    foreign_simulator_jobs = len(cadence) + len(calibre) + len(emx)
    active_simulator_jobs = foreign_simulator_jobs + sum(native_counts.values())
    isolation_gate_pass = (
        expected_process_valid
        and not extra_supervisors
        and not runners
        and foreign_simulator_jobs == 0
        and not unexpected
        and running_error is None
    )
    authoritative_supervisor_count = (
        (1 if expected_process_valid else 0) + len(extra_supervisors)
    )
    return {
        "authoritative_supervisor_count": authoritative_supervisor_count,
        "duplicate_supervisor_count": len(extra_supervisors),
        "duplicate_authoritative_supervisor_count": len(extra_supervisors),
        "duplicate_runner_count": len(runners),
        "runner_count": len(runners),
        "unexpected_project_child_count": len(unexpected) + (1 if running_error else 0),
        "project_owned_cadence_children": len(cadence) + native_counts["cadence"],
        "project_owned_calibre_children": len(calibre) + native_counts["calibre"],
        "project_owned_emx_children": len(emx) + native_counts["emx"],
        "active_simulator_jobs": active_simulator_jobs,
        "output_path_collision": False,
        "process_command_lines_persisted": False,
        "expected_supervisor_alive": observed_expected is not None,
        "expected_supervisor_identity_valid": expected_process_valid,
        "lease_identity_valid": lease_identity_valid,
        "lease_validity_state": lease.get("validity_state"),
        "lease_generation": lease.get("lease_generation"),
        "conflicting_lease_count": conflicting_lease_count,
        "lease_nonce_sha256": hashlib.sha256(
            str(lease.get("lease_nonce", "")).encode("utf-8")
        ).hexdigest(),
        "lock_held": lock_held,
        "lock_identity_valid": lock_contents.strip() == SUPERVISOR_ID,
        "expected_process": (
            _public_process_record(observed_expected)
            if observed_expected is not None
            else None
        ),
        "additional_supervisor_pids": [
            int(item["pid"]) for item in extra_supervisors
        ],
        "runner_pids": [int(item["pid"]) for item in runners],
        "simulator_pids": sorted(
            int(item["pid"]) for item in (*cadence, *calibre, *emx)
        ),
        "unexpected_project_pids": [
            int(item["pid"]) for item in unexpected
        ],
        "isolation_gate_pass": isolation_gate_pass,
        "isolation_scope": "RUNNING_BOUND_STAGE" if running_stage_history is not None else "IDLE_STARTUP",
        "bound_stage_process_pids": sorted(owned_pids),
        "bound_stage_native_counts": native_counts,
        "running_stage_evidence": running_evidence,
        "running_stage_error": running_error,
    }


def count_conflicting_current_leases(
    authoritative_lease: Path,
    *,
    registry_dir: Path,
) -> int:
    """Count other current leases for the exact logical supervisor."""
    authoritative = authoritative_lease.resolve()
    conflicts = 0
    for candidate in registry_dir.resolve().glob("SUPERVISOR_LEASE*.json"):
        resolved = candidate.resolve()
        if resolved == authoritative:
            continue
        try:
            payload = read_json(resolved, "supervisor lease candidate")
        except (OSError, json.JSONDecodeError, IsolationIdentityError):
            conflicts += 1
            continue
        if (
            payload.get("schema") == LEASE_SCHEMA
            and payload.get("campaign_id") == CAMPAIGN_ID
            and payload.get("queue_id") == QUEUE_ID
            and payload.get("logical_supervisor_id") == SUPERVISOR_ID
            and payload.get("validity_state") == "CURRENT"
        ):
            conflicts += 1
    return conflicts


def campaign_lock_state(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, ""
    contents = path.read_text(encoding="utf-8")
    fd = os.open(path, os.O_RDWR)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            held = True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return held, contents


def _public_process_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "pid",
            "parent_pid",
            "uid",
            "state",
            "start_ticks",
            "boot_id",
            "command_line_sha256",
            "executable_path",
            "executable_sha256",
        )
    }
