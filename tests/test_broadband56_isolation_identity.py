from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from rfic_transformer_inverse_design.campaigns import (
    broadband56_isolation_identity as identity,
)


BACKEND_SHA = "b" * 64
BOOT_ID = "11111111-2222-3333-4444-555555555555"


def _process(
    pid: int,
    *,
    parent_pid: int = 1,
    start_ticks: int | None = None,
    command: str | None = None,
) -> dict:
    command_text = command or (
        "python launch_isolation_hotfix.py "
        f"--campaign {identity.CAMPAIGN_ID} "
        f"--queue {identity.QUEUE_ID} "
        f"--supervisor {identity.SUPERVISOR_ID} "
        f"--backend-sha256 {BACKEND_SHA}"
    )
    return {
        "pid": pid,
        "parent_pid": parent_pid,
        "uid": 1001,
        "state": "S",
        "start_ticks": start_ticks if start_ticks is not None else 9000 + pid,
        "boot_id": BOOT_ID,
        "command_line_sha256": hashlib.sha256(command_text.encode()).hexdigest(),
        "command_text": command_text,
        "executable_path": "/usr/bin/python3",
        "executable_sha256": "c" * 64,
    }


def _lease(process: dict) -> dict:
    public_process = {
        key: value for key, value in process.items() if key != "command_text"
    }
    return {
        "schema": identity.LEASE_SCHEMA,
        "generated_utc": "2026-09-02T20:00:00+00:00",
        "validity_state": "CURRENT",
        "expires_utc": None,
        "validity_model": identity.LEASE_VALIDITY_MODEL,
        "lease_generation": 1,
        "lease_nonce": "lease-nonce-1",
        "campaign_id": identity.CAMPAIGN_ID,
        "queue_id": identity.QUEUE_ID,
        "logical_supervisor_id": identity.SUPERVISOR_ID,
        "physical_process": public_process,
        "backend_identity_manifest": {"sha256": BACKEND_SHA},
        "campaign_lock": {
            "expected_contents": identity.SUPERVISOR_ID,
            "exclusive_flock_required": True,
        },
    }


def _evaluate(lease: dict, processes: list[dict], **overrides) -> dict:
    arguments = {
        "lease": lease,
        "processes": processes,
        "backend_manifest_sha256": BACKEND_SHA,
        "lock_held": True,
        "lock_contents": identity.SUPERVISOR_ID,
        "conflicting_lease_count": 0,
        "now": datetime(2026, 9, 2, 20, 1, tzinfo=timezone.utc),
    }
    arguments.update(overrides)
    return identity.evaluate_process_isolation(**arguments)


def test_supervisor_ancestor_of_probe_is_still_counted() -> None:
    supervisor = _process(100)
    probe = _process(200, parent_pid=100)

    scanned = identity.filter_probe_helpers(
        [supervisor, probe], probe_pid=200
    )
    result = _evaluate(_lease(supervisor), scanned)

    assert [item["pid"] for item in scanned] == [100]
    assert result["authoritative_supervisor_count"] == 1
    assert result["isolation_gate_pass"] is True


def test_probe_pid_is_excluded_even_if_command_looks_like_supervisor() -> None:
    supervisor = _process(100)
    probe = _process(200, parent_pid=100)

    scanned = identity.filter_probe_helpers(
        [supervisor, probe], probe_pid=200
    )

    assert [item["pid"] for item in scanned] == [100]


def test_explicit_transient_helper_pid_is_excluded() -> None:
    supervisor = _process(100)
    helper = _process(201, parent_pid=200)

    scanned = identity.filter_probe_helpers(
        [supervisor, helper],
        probe_pid=200,
        transient_helper_pids=[201],
    )

    assert [item["pid"] for item in scanned] == [100]


def test_exactly_one_bound_supervisor_passes() -> None:
    supervisor = _process(100)

    result = _evaluate(_lease(supervisor), [supervisor])

    assert result["authoritative_supervisor_count"] == 1
    assert result["duplicate_authoritative_supervisor_count"] == 0
    assert result["runner_count"] == 0
    assert result["isolation_gate_pass"] is True


def test_second_campaign_supervisor_fails_closed() -> None:
    supervisor = _process(100)
    duplicate = _process(101)

    result = _evaluate(_lease(supervisor), [supervisor, duplicate])

    assert result["authoritative_supervisor_count"] == 2
    assert result["duplicate_authoritative_supervisor_count"] == 1
    assert result["isolation_gate_pass"] is False


def test_dead_expected_pid_fails_closed() -> None:
    supervisor = _process(100)

    result = _evaluate(_lease(supervisor), [])

    assert result["expected_supervisor_alive"] is False
    assert result["authoritative_supervisor_count"] == 0
    assert result["isolation_gate_pass"] is False


def test_pid_reuse_with_different_start_ticks_fails_closed() -> None:
    supervisor = _process(100)
    reused = deepcopy(supervisor)
    reused["start_ticks"] += 1

    result = _evaluate(_lease(supervisor), [reused])

    assert result["expected_supervisor_alive"] is True
    assert result["expected_supervisor_identity_valid"] is False
    assert result["isolation_gate_pass"] is False


def test_logical_supervisor_id_mismatch_fails_closed() -> None:
    supervisor = _process(100)
    lease = _lease(supervisor)
    lease["logical_supervisor_id"] = "wrong-supervisor"

    result = _evaluate(lease, [supervisor])

    assert result["lease_identity_valid"] is False
    assert result["isolation_gate_pass"] is False


def test_backend_sha_mismatch_fails_closed() -> None:
    supervisor = _process(100)

    result = _evaluate(
        _lease(supervisor),
        [supervisor],
        backend_manifest_sha256="d" * 64,
    )

    assert result["lease_identity_valid"] is False
    assert result["isolation_gate_pass"] is False


@pytest.mark.parametrize(
    ("validity_state", "conflict_count"),
    [("STALE", 0), ("CURRENT", 1)],
)
def test_stale_or_conflicting_lease_fails_closed(
    validity_state: str, conflict_count: int
) -> None:
    supervisor = _process(100)
    lease = _lease(supervisor)
    lease["validity_state"] = validity_state

    result = _evaluate(
        lease,
        [supervisor],
        conflicting_lease_count=conflict_count,
    )

    assert result["isolation_gate_pass"] is False


def test_unrelated_shell_and_ssh_processes_are_ignored() -> None:
    supervisor = _process(100)
    shell = _process(
        300,
        command=f"bash -c cat /tmp/{identity.CAMPAIGN_ID}/status.json",
    )
    ssh = _process(
        301,
        command=f"ssh mars-0002 echo {identity.CAMPAIGN_ID}",
    )

    result = _evaluate(_lease(supervisor), [supervisor, shell, ssh])

    assert result["unexpected_project_child_count"] == 0
    assert result["isolation_gate_pass"] is True


def test_no_simulator_before_golden_remains_zero() -> None:
    supervisor = _process(100)

    result = _evaluate(_lease(supervisor), [supervisor])

    assert result["project_owned_cadence_children"] == 0
    assert result["project_owned_calibre_children"] == 0
    assert result["project_owned_emx_children"] == 0
    assert result["active_simulator_jobs"] == 0


def test_unbound_runner_or_simulator_fails_closed() -> None:
    supervisor = _process(100)
    runner = _process(
        400,
        command=(
            "python run_broadband56_v2_stage_launcher.py "
            f"--campaign {identity.CAMPAIGN_ID}"
        ),
    )
    emx = _process(
        401,
        command=f"emx_cae_singularity --campaign {identity.CAMPAIGN_ID}",
    )

    result = _evaluate(_lease(supervisor), [supervisor, runner, emx])

    assert result["runner_count"] == 1
    assert result["active_simulator_jobs"] == 1
    assert result["isolation_gate_pass"] is False
