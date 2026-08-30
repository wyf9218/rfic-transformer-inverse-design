from __future__ import annotations

import copy

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    SNAPSHOT_SCHEMA,
    CapacityPolicyError,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
    exact_completion,
    required_storage_bytes,
    stage_for_progress,
)


def _snapshot(*, logical_cpus: int = 256, load1: float = 225.55) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "captured_utc": "2026-08-30T18:00:00Z",
        "resources": {
            "logical_cpu_count": logical_cpus,
            "physical_cpu_count": logical_cpus // 2,
            "load_1m": load1,
            "load_5m": 220.0,
            "load_15m": 210.0,
            "cpu_total_utilization_percent": 70.0,
            "cpu_user_utilization_percent": 55.0,
            "cpu_system_utilization_percent": 10.0,
            "iowait_percent": 5.0,
            "runnable_process_count": 20,
            "blocked_process_count": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 300_000,
            "swap_total_bytes": 100_000,
            "swap_used_bytes": 10_000,
            "swap_sample_interval_seconds": 60.0,
            "swap_in_pages_delta": 0,
            "swap_out_pages_delta": 0,
            "active_swap_thrashing": False,
            "filesystem_free_bytes": 20 * 1024**3,
        },
        "licenses": {
            "cadence_available": True,
            "calibre_available": True,
            "emx_available": True,
            "simulator_license_capacity": 8,
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


def test_raw_load_above_40_can_pass_when_capacity_normalized() -> None:
    result = _evaluate(_snapshot(logical_cpus=256, load1=225.55))

    assert result["pass"] is True
    assert result["metrics"]["raw_load1"] == 225.55
    assert result["metrics"]["normalized_load1"] == pytest.approx(225.55 / 256)
    assert result["checks"]["normalized_load1_gate"] is True


def test_same_raw_load_fails_on_64_logical_cpus() -> None:
    result = _evaluate(_snapshot(logical_cpus=64, load1=225.55))

    assert result["pass"] is False
    assert result["metrics"]["normalized_load1"] == pytest.approx(225.55 / 64)
    assert result["checks"]["normalized_load1_gate"] is False


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    [
        (lambda item: item["resources"].update(memory_available_bytes=100_000), "memory_gate"),
        (lambda item: item["resources"].update(iowait_percent=10.1), "iowait_gate"),
        (
            lambda item: item["resources"].update(
                swap_in_pages_delta=1, active_swap_thrashing=True
            ),
            "swap_thrash_gate",
        ),
        (lambda item: item["licenses"].update(emx_available=False), "license_gate"),
        (
            lambda item: item["isolation"].update(
                authoritative_supervisor_count=2, duplicate_supervisor_count=1
            ),
            "isolation_gate",
        ),
        (lambda item: item["isolation"].update(duplicate_runner_count=1), "isolation_gate"),
    ],
)
def test_real_resource_failures_still_block(mutation, failed_check: str) -> None:
    snapshot = _snapshot()
    mutation(snapshot)

    result = _evaluate(snapshot)

    assert result["pass"] is False
    assert result["checks"][failed_check] is False


def test_reported_swap_state_must_match_measured_deltas() -> None:
    snapshot = _snapshot()
    snapshot["resources"]["swap_out_pages_delta"] = 1

    with pytest.raises(CapacityPolicyError, match="inconsistent"):
        _evaluate(snapshot)


def test_production_storage_uses_measured_pilot_and_safety_factor() -> None:
    required = required_storage_bytes(
        stage="PHASE_A",
        current_accepted=1_000,
        measured_pilot_bytes_per_geometry=2_000,
    )

    assert required == 2_000 * 199_000 * 1.25


def test_concurrency_is_capped_by_license_and_ten_percent_of_cpus() -> None:
    license_limited = adaptive_concurrency(
        stage="PILOT_1000",
        logical_cpu_count=256,
        simulator_license_capacity=7,
        current_concurrency=7,
        healthy_check_streak=10,
        normalized_load1=0.5,
        iowait_percent=1,
        available_memory_fraction=0.5,
        active_swap_thrashing=False,
        licenses_available=True,
    )
    cpu_limited = adaptive_concurrency(
        stage="PILOT_1000",
        logical_cpu_count=64,
        simulator_license_capacity=100,
        current_concurrency=6,
        healthy_check_streak=10,
        normalized_load1=0.5,
        iowait_percent=1,
        available_memory_fraction=0.5,
        active_swap_thrashing=False,
        licenses_available=True,
    )

    assert license_limited["hard_cap"] == 7
    assert license_limited["concurrency"] == 7
    assert cpu_limited["hard_cap"] == 6
    assert cpu_limited["concurrency"] == 6


def test_no_stage_can_request_200000_simultaneous_jobs() -> None:
    result = adaptive_concurrency(
        stage="PHASE_C",
        logical_cpu_count=10_000,
        simulator_license_capacity=200_000,
        current_concurrency=1_000,
        healthy_check_streak=10,
        normalized_load1=0.5,
        iowait_percent=1,
        available_memory_fraction=0.5,
        active_swap_thrashing=False,
        licenses_available=True,
        pilot_1000_safe_concurrency=1_000,
    )

    assert result["hard_cap"] == 1_000
    assert result["concurrency"] <= 1_000
    assert result["concurrency"] < 200_000


def test_concurrency_halves_and_pauses_at_frozen_thresholds() -> None:
    reduced = adaptive_concurrency(
        stage="PILOT_1000",
        logical_cpu_count=256,
        simulator_license_capacity=20,
        current_concurrency=8,
        healthy_check_streak=0,
        normalized_load1=1.01,
        iowait_percent=1,
        available_memory_fraction=0.5,
        active_swap_thrashing=False,
        licenses_available=True,
    )
    paused = adaptive_concurrency(
        stage="PILOT_1000",
        logical_cpu_count=256,
        simulator_license_capacity=20,
        current_concurrency=8,
        healthy_check_streak=0,
        normalized_load1=1.21,
        iowait_percent=1,
        available_memory_fraction=0.5,
        active_swap_thrashing=False,
        licenses_available=True,
    )

    assert reduced["action"] == "REDUCE_BY_HALF"
    assert reduced["concurrency"] == 4
    assert paused["action"] == "PAUSE_NEW_LAUNCHES"
    assert paused["concurrency"] == 0


def _receipt(stage: str, state: str, count: int, status: str = "PASS") -> dict:
    return {
        "stage": stage,
        "overall_status": status,
        "terminal_state": state,
        "accepted_unique_geometries": count,
    }


def test_stage_order_requires_exact_prior_pass_receipts() -> None:
    golden = _receipt("GOLDEN", "GOLDEN_COMPLETE", 1)
    pilot_32 = _receipt("PILOT_32", "PILOT_32_COMPLETE", 32)

    assert stage_for_progress(current_accepted=0, stage_receipts=[]) == "GOLDEN"
    assert stage_for_progress(current_accepted=1, stage_receipts=[golden]) == "PILOT_32"
    assert (
        stage_for_progress(current_accepted=32, stage_receipts=[golden, pilot_32])
        == "PILOT_1000"
    )

    with pytest.raises(CapacityPolicyError, match="GOLDEN"):
        stage_for_progress(current_accepted=32, stage_receipts=[pilot_32])


def test_golden_failure_prevents_32_pilot() -> None:
    failed_golden = _receipt("GOLDEN", "GOLDEN_COMPLETE", 1, status="FAIL")

    with pytest.raises(CapacityPolicyError, match="GOLDEN"):
        stage_for_progress(current_accepted=1, stage_receipts=[failed_golden])


def test_completion_requires_exactly_200000_unique_accepted() -> None:
    assert exact_completion(199_999) is False
    assert exact_completion(200_000) is True
    with pytest.raises(CapacityPolicyError, match="overshoots"):
        exact_completion(200_001)


def test_snapshot_input_is_not_mutated() -> None:
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)

    _evaluate(snapshot)

    assert snapshot == before
