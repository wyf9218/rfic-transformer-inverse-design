from __future__ import annotations

import copy

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    CapacityPolicyError,
)
from rfic_transformer_inverse_design.campaigns.broadband56_swap_override_policy import (
    SNAPSHOT_SCHEMA,
    SWAP_POLICY,
    adaptive_concurrency,
    combined_swap_thrashing,
    evaluate_capacity_snapshot,
)


def _snapshot() -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "swap_policy": SWAP_POLICY,
        "captured_utc": "2026-09-02T18:00:00Z",
        "source_snapshot": {
            "path": "/private/resource_snapshot.json",
            "size_bytes": 123,
            "sha256": "a" * 64,
        },
        "resources": {
            "logical_cpu_count": 256,
            "physical_cpu_count": 128,
            "load_1m": 20.0,
            "load_5m": 18.0,
            "load_15m": 16.0,
            "cpu_total_utilization_percent": 8.0,
            "cpu_user_utilization_percent": 5.0,
            "cpu_system_utilization_percent": 2.0,
            "iowait_percent": 0.01,
            "runnable_process_count": 4,
            "blocked_process_count": 0,
            "blocked_process_count_delta": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 688_000,
            "swap_total_bytes": 100_000,
            "swap_used_bytes": 60_000,
            "swap_sample_interval_seconds": 60.0,
            "swap_in_pages_delta": 50,
            "swap_out_pages_delta": 0,
            "oom_kill_delta": 0,
            "active_swap_thrashing": False,
            "filesystem_free_bytes": 20 * 1024**3,
        },
        "licenses": {
            "cadence_available": True,
            "calibre_available": True,
            "emx_available": True,
            "simulator_license_capacity": 4,
        },
        "isolation": {
            "authoritative_supervisor_count": 1,
            "duplicate_supervisor_count": 0,
            "duplicate_runner_count": 0,
            "unexpected_project_child_count": 0,
            "project_owned_cadence_children": 0,
            "project_owned_calibre_children": 0,
            "project_owned_emx_children": 0,
            "output_path_collision": False,
        },
    }


def _evaluate(snapshot: dict) -> dict:
    return evaluate_capacity_snapshot(snapshot, stage="GOLDEN", current_accepted=0)


def test_small_swap_in_is_advisory_when_resources_are_healthy() -> None:
    result = _evaluate(_snapshot())

    assert result["pass"] is True
    assert result["metrics"]["advisory_nonzero_swap_in"] is True
    assert result["metrics"]["active_swap_thrashing"] is False
    assert result["checks"]["swap_thrash_gate"] is True


def test_negligible_swap_out_is_advisory_when_resources_are_healthy() -> None:
    snapshot = _snapshot()
    snapshot["resources"].update(
        swap_in_pages_delta=0,
        swap_out_pages_delta=1,
    )

    result = _evaluate(snapshot)

    assert result["pass"] is True
    assert result["metrics"]["active_swap_thrashing"] is False


def test_material_sustained_swap_out_is_active_thrashing() -> None:
    snapshot = _snapshot()
    snapshot["resources"].update(
        swap_in_pages_delta=0,
        swap_out_pages_delta=60,
        active_swap_thrashing=True,
    )

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["swap_thrash_gate"] is False


def test_swap_activity_and_low_memory_classifies_active_thrashing() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["memory_available_bytes"] = 399_000
    snapshot["resources"]["active_swap_thrashing"] = True

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["memory_gate"] is False
    assert result["checks"]["swap_thrash_gate"] is False


def test_memory_floor_is_hard_even_without_swap_activity() -> None:
    snapshot = _snapshot()
    snapshot["resources"].update(
        memory_available_bytes=399_000,
        swap_in_pages_delta=0,
    )

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["memory_gate"] is False
    assert result["metrics"]["active_swap_thrashing"] is False


def test_iowait_limit_is_strictly_below_five_percent() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["iowait_percent"] = 5.0
    snapshot["resources"]["active_swap_thrashing"] = True

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["iowait_gate"] is False
    assert result["checks"]["swap_thrash_gate"] is False


def test_oom_event_is_a_hard_failure() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["oom_kill_delta"] = 1
    snapshot["resources"]["active_swap_thrashing"] = True

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["no_oom_gate"] is False


def test_increasing_blocked_processes_with_swap_is_thrashing() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["blocked_process_count_delta"] = 1
    snapshot["resources"]["active_swap_thrashing"] = True

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"]["swap_thrash_gate"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item["licenses"].update(emx_available=False),
        lambda item: item["isolation"].update(authoritative_supervisor_count=0),
        lambda item: item["isolation"].update(duplicate_runner_count=1),
        lambda item: item["isolation"].update(output_path_collision=True),
    ],
)
def test_unchanged_hard_gates_remain_fail_closed(mutation) -> None:
    snapshot = _snapshot()
    mutation(snapshot)

    assert _evaluate(snapshot)["pass"] is False


def test_reported_combined_state_must_match_computed_state() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["active_swap_thrashing"] = True

    with pytest.raises(CapacityPolicyError, match="inconsistent"):
        _evaluate(snapshot)


def test_oom_evidence_is_required() -> None:
    snapshot = _snapshot()
    del snapshot["resources"]["oom_kill_delta"]

    with pytest.raises(CapacityPolicyError, match="oom_kill_delta"):
        _evaluate(snapshot)


def test_snapshot_is_not_mutated() -> None:
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)

    _evaluate(snapshot)

    assert snapshot == before


@pytest.fixture
def no_child_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Resource-policy tests must not launch child processes")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("os.system", forbidden)
    monkeypatch.setattr("os.fork", forbidden)
    monkeypatch.setattr("os.posix_spawn", forbidden)


def _cpu_snapshot(load1: float = 0.999006, load5: float = 0.992912) -> dict:
    snapshot = _snapshot()
    cpus = snapshot["resources"]["logical_cpu_count"]
    snapshot["resources"].update(load_1m=load1 * cpus, load_5m=load5 * cpus)
    return snapshot


@pytest.mark.parametrize("stage", ["GOLDEN", "PILOT_32"])
@pytest.mark.parametrize("load1,load5", [(0.999006, 0.992912), (1.10, 1.10)])
def test_initial_cpu_thresholds_pass_at_owner_values_and_boundary(
    stage, load1, load5, no_child_processes
) -> None:
    result = evaluate_capacity_snapshot(_cpu_snapshot(load1, load5), stage=stage, current_accepted=0)

    assert result["pass"] is True
    metrics = result["metrics"]
    concurrency = adaptive_concurrency(
        stage=stage,
        logical_cpu_count=metrics["logical_cpu_count"],
        simulator_license_capacity=metrics["simulator_license_capacity"],
        current_concurrency=None,
        healthy_check_streak=0,
        normalized_load1=metrics["normalized_load1"],
        iowait_percent=metrics["iowait_percent"],
        available_memory_fraction=metrics["available_memory_fraction"],
        active_swap_thrashing=metrics["active_swap_thrashing"],
        licenses_available=result["checks"]["license_gate"],
    )
    assert concurrency["concurrency"] == 1


@pytest.mark.parametrize("stage", ["GOLDEN", "PILOT_32"])
@pytest.mark.parametrize(
    "load1,load5,failed_check",
    [(1.100001, 0.992912, "normalized_load1_gate"), (0.999006, 1.100001, "normalized_load5_gate")],
)
def test_initial_cpu_thresholds_reject_either_value_above_boundary(
    stage, load1, load5, failed_check, no_child_processes
) -> None:
    result = evaluate_capacity_snapshot(_cpu_snapshot(load1, load5), stage=stage, current_accepted=0)

    assert result["pass"] is False
    assert result["failed_checks"] == [failed_check]


@pytest.mark.parametrize("stage", ["GOLDEN", "PILOT_32"])
@pytest.mark.parametrize(
    "section,field,value,failed_check",
    [
        ("resources", "memory_available_bytes", 399_000, "memory_gate"),
        ("resources", "oom_kill_delta", 1, "no_oom_gate"),
        ("resources", "swap_out_pages_delta", 60, "swap_thrash_gate"),
        ("resources", "iowait_percent", 5.0, "iowait_gate"),
        ("resources", "filesystem_free_bytes", 0, "storage_gate"),
        ("licenses", "cadence_available", False, "license_gate"),
        ("licenses", "calibre_available", False, "license_gate"),
        ("licenses", "emx_available", False, "license_gate"),
        ("isolation", "authoritative_supervisor_count", 0, "isolation_gate"),
        ("isolation", "authoritative_supervisor_count", 2, "isolation_gate"),
        ("isolation", "duplicate_supervisor_count", 1, "isolation_gate"),
        ("isolation", "duplicate_runner_count", 1, "isolation_gate"),
        ("isolation", "unexpected_project_child_count", 1, "isolation_gate"),
        ("isolation", "output_path_collision", True, "isolation_gate"),
    ],
)
def test_hard_gates_still_block_at_newly_admitted_cpu_load(
    stage, section, field, value, failed_check, no_child_processes
) -> None:
    snapshot = _cpu_snapshot()
    snapshot[section][field] = value
    snapshot["resources"]["active_swap_thrashing"] = combined_swap_thrashing(snapshot["resources"])["active"]

    result = evaluate_capacity_snapshot(snapshot, stage=stage, current_accepted=0)

    assert result["pass"] is False
    assert result["checks"][failed_check] is False


@pytest.mark.parametrize("stage", ["PILOT_1000", "PHASE_A", "PHASE_B", "PHASE_C"])
def test_later_stage_cpu_thresholds_are_unchanged(stage, no_child_processes) -> None:
    snapshot = _cpu_snapshot()
    snapshot["resources"]["filesystem_free_bytes"] = 10**15
    result = evaluate_capacity_snapshot(
        snapshot, stage=stage, current_accepted=0, measured_pilot_bytes_per_geometry=1024
    )

    assert result["pass"] is False
    assert result["checks"]["normalized_load1_gate"] is False
    assert result["checks"]["normalized_load5_gate"] is False
