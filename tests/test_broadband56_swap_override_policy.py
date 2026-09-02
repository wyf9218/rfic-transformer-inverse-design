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
