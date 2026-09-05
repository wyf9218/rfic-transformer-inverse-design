"""Fixed worker-limit fixtures; none is a live 48-worker benchmark."""
import copy
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduler
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import CapacityPolicyError
from tests.test_broadband56_scheduling import samples, write, decision


def fixed_samples(root, *, count=5, seats=100, measured=True, mutation=None, each_mutation=None):
    paths, now = samples(root, count=count, measured=measured)
    first = json.loads(paths[0].read_text())
    identity = {k: first[k] for k in ("campaign_id", "contract_fingerprint_sha256")}
    backend = scheduler.file_record(write(root / "fixed_backend.json", identity))
    lease_path = Path(first["supervisor_lease"]["path"])
    lease = scheduler.file_record(write(lease_path, {
        **identity, "queue_id": "queue", "logical_supervisor_id": "owner",
        "backend_identity_manifest": backend,
    }))
    overlay = scheduler.file_record(write(root / "fixed_overlay.json", {
        **identity, "schema": "rfic_transformer.broadband56_v2_operational_policy_overlay.v1",
        "overall_status": "PASS", "queue_id": "queue", "supervisor_id": "owner",
        "scientific_contract_changed": False, "nn_training_authorized": False,
        "corrected_backend_manifest": backend,
        "fixed_generation_policy": copy.deepcopy(scheduler.FIXED48_GENERATION_POLICY),
    }))
    for path in paths:
        item = json.loads(path.read_text())
        item.update(supervisor_lease=lease, operational_overlay_manifest=overlay)
        item["resources"].update(logical_cpu_count=192, physical_cpu_count=96,
                                 memory_total_bytes=100 * 1024**3,
                                 memory_available_bytes=80 * 1024**3)
        if each_mutation:
            each_mutation(item)
        if mutation and path == paths[-1]:
            mutation(item)
        item["resources"]["active_swap_thrashing"] = scheduler.swap.combined_swap_thrashing(item["resources"])["active"]
        raw = copy.deepcopy(item)
        raw.pop("source_snapshot")
        raw["schema"] = scheduler.RAW_SNAPSHOT_SCHEMA
        item["source_snapshot"] = scheduler.file_record(write(Path(item["source_snapshot"]["path"]), raw))
        if measured:
            proof_path = Path(item["per_tool_capacity_evidence"]["path"])
            proof = json.loads(proof_path.read_text())
            proof["supervisor_lease"] = lease
            for tool, record in proof["tools"].items():
                record["observed"].update(free_seats=seats, threads_per_job=9,
                                          peak_rss_bytes_per_job=100 * 1024**2)
                record["measurement_source"] = scheduler.file_record(write(
                    Path(record["measurement_source"]["path"]),
                    {"tool": tool, "observed": record["observed"]}))
            item["per_tool_capacity_evidence"] = scheduler.file_record(write(proof_path, proof))
        write(path, item)
    return paths, now


def test_direct_48_uses_actual_seats_without_running_comparison(tmp_path, monkeypatch):
    paths, now = fixed_samples(tmp_path)
    monkeypatch.setattr(scheduler, "completed_pilot_execution",
                        lambda *a, **kw: pytest.fail("fixed generation must not read a trial ladder"))
    monkeypatch.setattr(scheduler, "base_concurrency",
                        lambda **kw: pytest.fail("legacy 10-percent cap must not reset fixed generation"))
    result = decision(tmp_path, paths[-1], now)
    assert result["requested_concurrency"] == result["concurrency"] == 48
    assert result["hard_cap"] == 48
    assert result["benchmark_required"] is False
    assert result["benchmark_levels_completed"] == []
    assert result["production_optimum_proven"] is False
    # Four is a legacy aggregate, not the exact per-tool capacity in this fixture.
    assert json.loads(paths[-1].read_text())["licenses"]["simulator_license_capacity"] == 4


@pytest.mark.parametrize("count,expected", [(1, 0), (4, 0), (5, 48)])
def test_health_checks_are_not_concurrency_benchmarks(tmp_path, count, expected):
    paths, now = fixed_samples(tmp_path, count=count)
    assert decision(tmp_path, paths[-1], now)["concurrency"] == expected


@pytest.mark.parametrize("seats", [0, 1, 4, 47])
def test_available_capacity_runs_without_claiming_48_native_jobs(tmp_path, seats):
    paths, now = fixed_samples(tmp_path, seats=seats)
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == result["admitted_concurrency"] == seats
    assert result["hard_cap"] == seats
    assert result["requested_concurrency"] == 48
    assert result["target_executor_capacity"] == 48
    assert result["production_optimum_proven"] is False


def test_missing_measured_capacity_never_silently_falls_back(tmp_path):
    paths, now = fixed_samples(tmp_path, measured=False)
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 0


@pytest.mark.parametrize("mutate", [
    lambda s: s["resources"].update(memory_available_bytes=19 * 1024**3),
    lambda s: s["resources"].update(iowait_percent=5),
    lambda s: s["resources"].update(oom_kill_delta=1),
    lambda s: s["resources"].update(filesystem_free_bytes=0),
    lambda s: s["licenses"].update(emx_available=False),
    lambda s: s["licenses"].update(calibre_available=False),
    lambda s: s["licenses"].update(cadence_available=False),
    lambda s: s["isolation"].update(duplicate_runner_count=1),
    lambda s: s["isolation"].update(authoritative_supervisor_count=2),
    lambda s: s["isolation"].update(output_path_collision=True),
    lambda s: s["resources"].update(load_1m=192 * 1.1001),
    lambda s: s["resources"].update(load_5m=192 * 1.1001),
])
def test_fixed_target_never_bypasses_live_protections(tmp_path, mutate):
    paths, now = fixed_samples(tmp_path, mutation=mutate)
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 0


@pytest.mark.parametrize("load1,load5", [(0.91, 0.96), (1.0, 1.05), (1.10, 1.10)])
def test_five_checks_use_approved_1p10_window(tmp_path, load1, load5):
    paths, now = fixed_samples(tmp_path, each_mutation=lambda s: s["resources"].update(
        load_1m=192 * load1, load_5m=192 * load5))
    result = decision(tmp_path, paths[-1], now)
    assert result["healthy_check_streak"] == 5
    assert result["healthy_load1_max"] == result["healthy_load5_max"] == 1.10
    assert result["concurrency"] == 48


@pytest.mark.parametrize("stage", ["PHASE_A", "PHASE_B", "PHASE_C"])
def test_production_gate_keeps_same_fixed48_cpu_window(tmp_path, stage):
    paths, now = fixed_samples(tmp_path, each_mutation=lambda s: s["resources"].update(
        load_1m=192 * 1.10, load_5m=192 * 1.10))
    payload = json.loads(paths[-1].read_text())
    policy = scheduler.swap.evaluate_capacity_snapshot(
        payload, stage=stage, current_accepted=1000, measured_pilot_bytes_per_geometry=1000)
    assert policy["pass"]
    result = scheduler.concurrency_for_snapshot(
        snapshot_path=paths[-1], campaign_root=tmp_path, stage=stage, current_accepted=1000,
        policy=policy, legacy_policy=lambda **kw: pytest.fail("legacy CPU reset"),
        measured_pilot_bytes_per_geometry=1000, now=now)
    assert result["concurrency"] == 48 and result["healthy_check_streak"] == 5


def test_partial_memory_capacity_is_used_with_20pct_reserve(tmp_path):
    paths, now = fixed_samples(tmp_path, mutation=lambda s: s["resources"].update(
        memory_available_bytes=21 * 1024**3))
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == result["hard_cap"] == 10
    assert result["reasons"] == ["FIXED_48_ADMITTING_AVAILABLE_PER_TOOL_CAPACITY"]


def test_same_lease_owned_children_do_not_erase_health(tmp_path):
    paths, now = fixed_samples(tmp_path, each_mutation=lambda s: s["isolation"].update(
        project_owned_emx_children=3))
    result = scheduler.healthy_history(paths[-1], campaign_root=tmp_path,
                                       stage="PILOT_1000", current_accepted=100, now=now)
    assert result["healthy_check_streak"] == 5
    # Re-reading is idempotent, not a sixth sample or a reset at a batch boundary.
    assert scheduler.healthy_history(paths[-1], campaign_root=tmp_path,
        stage="PILOT_1000", current_accepted=110, now=now) == result


def test_duplicate_fixed48_sample_cannot_fabricate_history(tmp_path):
    paths, now = fixed_samples(tmp_path)
    write(paths[-1].with_name("swap_override_duplicate.json"), json.loads(paths[-1].read_text()))
    with pytest.raises(CapacityPolicyError, match="duplicate"):
        decision(tmp_path, paths[-1], now)


@pytest.mark.parametrize("field,value", [
    ("requested_concurrency", 49), ("requested_concurrency", 48.0),
    ("run_concurrency_benchmarks", True), ("run_concurrency_benchmarks", 0),
    ("cpu_admission", "UNLIMITED"), ("extra_field", True),
])
def test_fixed_overlay_rejects_scope_expansion(tmp_path, field, value):
    paths, now = fixed_samples(tmp_path)
    data = json.loads(paths[-1].read_text())
    overlay_path = Path(data["operational_overlay_manifest"]["path"])
    overlay = json.loads(overlay_path.read_text())
    overlay["fixed_generation_policy"][field] = value
    data["operational_overlay_manifest"] = scheduler.file_record(write(overlay_path, overlay))
    write(paths[-1], data)
    with pytest.raises(CapacityPolicyError):
        decision(tmp_path, paths[-1], now)


@pytest.mark.parametrize("field,value", [
    ("queue_id", "other"), ("supervisor_id", "other"), ("campaign_id", "other"),
    ("scientific_contract_changed", True), ("nn_training_authorized", True),
    ("corrected_backend_manifest", {}),
])
def test_fixed_overlay_must_bind_original_owner_and_backend(tmp_path, field, value):
    paths, now = fixed_samples(tmp_path)
    data = json.loads(paths[-1].read_text())
    overlay_path = Path(data["operational_overlay_manifest"]["path"])
    overlay = json.loads(overlay_path.read_text())
    overlay[field] = value
    data["operational_overlay_manifest"] = scheduler.file_record(write(overlay_path, overlay))
    write(paths[-1], data)
    with pytest.raises(CapacityPolicyError):
        decision(tmp_path, paths[-1], now)


def test_fixed_mode_preserves_golden_single_worker(tmp_path):
    paths, now = fixed_samples(tmp_path)
    assert decision(tmp_path, paths[-1], now, stage="GOLDEN")["concurrency"] == 1


def test_fixed_overlay_byte_drift_is_not_accepted(tmp_path):
    paths, now = fixed_samples(tmp_path)
    data = json.loads(paths[-1].read_text())
    Path(data["operational_overlay_manifest"]["path"]).write_text("{}")
    with pytest.raises(CapacityPolicyError):
        decision(tmp_path, paths[-1], now)


def test_stale_snapshot_does_not_authorize_fixed48(tmp_path):
    from datetime import timedelta

    paths, now = fixed_samples(tmp_path)
    with pytest.raises(CapacityPolicyError):
        decision(tmp_path, paths[-1], now + timedelta(seconds=301))
