from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduler
from rfic_transformer_inverse_design.campaigns import broadband56_swap_override_policy as swap
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import CapacityPolicyError
from tests.test_broadband56_swap_override_policy import _snapshot
from tests.test_broadband56_stage_execution import _profile
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import validate_execution_profile


def write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def samples(root, *, count=5, step=60, mutations=None, measured=True):
    now = datetime.now(timezone.utc)
    lease = scheduler.file_record(write(root / "lease.json", {"generation": 28}))
    paths = []
    for index in range(count):
        item = _snapshot()
        item["captured_utc"] = (now - timedelta(seconds=(count - 1 - index) * step)).isoformat()
        item["supervisor_lease"] = lease
        if mutations and index in mutations:
            mutations[index](item)
        raw = copy.deepcopy(item)
        raw.pop("source_snapshot")
        raw["schema"] = scheduler.RAW_SNAPSHOT_SCHEMA
        item["source_snapshot"] = scheduler.file_record(write(root / f"raw/{index}.json", raw))
        if measured:
            tools = {}
            for tool in scheduler.TOOL_NAMES:
                observed = {"free_seats": 4, "threads_per_job": 2, "peak_rss_bytes_per_job": 100_000}
                source = write(root / f"tools/{index}_{tool}.json", {"tool": tool, "observed": observed})
                tools[tool] = {"measurement_source": scheduler.file_record(source), "observed": observed}
            proof = {"schema": "rfic_transformer.broadband56_tool_capacity.v1",
                     "campaign_id": item["campaign_id"],
                     "contract_fingerprint_sha256": item["contract_fingerprint_sha256"],
                     "captured_utc": item["captured_utc"], "supervisor_lease": lease, "tools": tools}
            item["per_tool_capacity_evidence"] = scheduler.file_record(write(root / f"capacity/{index}.json", proof))
        paths.append(write(root / f"resource_snapshots/swap_override_{index:06d}.json", item))
    return paths, now


def decision(root, path, now, *, stage="PILOT_1000"):
    data = json.loads(path.read_text())
    return scheduler.concurrency_for_snapshot(
        snapshot_path=path, campaign_root=root, stage=stage,
        current_accepted=100 if stage == "PILOT_1000" else 0,
        policy=swap.evaluate_capacity_snapshot(data, stage=stage, current_accepted=100),
        legacy_policy=lambda **kw: pytest.fail("overlay must not use the legacy single-worker clamp"),
        now=now,
    )


@pytest.mark.parametrize("count,expected", [(1, 0), (4, 0), (5, 2), (10, 2)])
def test_five_distinct_checks_enable_only_first_two_worker_trial(tmp_path, count, expected):
    paths, now = samples(tmp_path, count=count)
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == expected
    assert result["healthy_check_streak"] == count
    assert result["effective_healthy_streak_requirement"] == 5
    assert len(result["evidence"]) == count


def test_boolean_license_availability_is_not_parallel_capacity(tmp_path):
    paths, now = samples(tmp_path, measured=False)
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == 1
    assert result["reasons"] == ["PER_TOOL_SEATS_THREADS_PEAK_RSS_NOT_MEASURED"]


@pytest.mark.parametrize("name,value", [("load_1m", 256 * .91), ("load_5m", 256 * .96)])
def test_single_worker_window_is_not_expansion_window(tmp_path, name, value):
    paths, now = samples(tmp_path, mutations={4: lambda s: s["resources"].update({name: value})})
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == 1
    assert result["healthy_check_streak"] == 0


@pytest.mark.parametrize("mutation", [
    lambda s: s["resources"].update(memory_available_bytes=199_000),
    lambda s: s["resources"].update(iowait_percent=5),
    lambda s: s["resources"].update(filesystem_free_bytes=0),
    lambda s: s["licenses"].update(emx_available=False),
    lambda s: s["isolation"].update(duplicate_runner_count=1),
    lambda s: s["isolation"].update(output_path_collision=True),
])
def test_hard_gate_failure_prevents_launch(tmp_path, mutation):
    def mutate(snapshot):
        mutation(snapshot)
        snapshot["resources"]["swap_in_pages_delta"] = 0
    paths, now = samples(tmp_path, mutations={4: mutate})
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 0


def test_unhealthy_middle_sample_resets_streak(tmp_path):
    paths, now = samples(tmp_path, mutations={2: lambda s: s["resources"].update(load_5m=256)})
    assert decision(tmp_path, paths[-1], now)["healthy_check_streak"] == 2


def test_gaps_do_not_accumulate_old_health(tmp_path):
    paths, now = samples(tmp_path, step=181)
    assert decision(tmp_path, paths[-1], now)["healthy_check_streak"] == 1


@pytest.mark.parametrize("age", [301, -1])
def test_stale_and_future_latest_observation_fail_closed(tmp_path, age):
    paths, now = samples(tmp_path)
    with pytest.raises(CapacityPolicyError, match="stale or in the future"):
        decision(tmp_path, paths[-1], now + timedelta(seconds=age))


def test_overlapping_probe_intervals_rejected(tmp_path):
    paths, now = samples(tmp_path, step=59)
    with pytest.raises(CapacityPolicyError, match="overlapping"):
        decision(tmp_path, paths[-1], now)


def test_repeated_snapshot_cannot_count_twice(tmp_path):
    paths, now = samples(tmp_path)
    write(paths[-1].with_name("swap_override_duplicate.json"), json.loads(paths[-1].read_text()))
    with pytest.raises(CapacityPolicyError, match="duplicate"):
        decision(tmp_path, paths[-1], now)


def test_raw_source_hash_drift_rejected(tmp_path):
    paths, now = samples(tmp_path)
    write(tmp_path / "raw/0.json", {"changed": True})
    with pytest.raises(CapacityPolicyError, match="identity mismatch"):
        decision(tmp_path, paths[-1], now)


def test_overlay_must_not_change_measured_values(tmp_path):
    paths, now = samples(tmp_path)
    value = json.loads(paths[-1].read_text())
    value["resources"]["load_5m"] += 1
    write(paths[-1], value)
    with pytest.raises(CapacityPolicyError, match="changed an observed"):
        decision(tmp_path, paths[-1], now)


def test_golden_remains_one_even_with_five_checks(tmp_path):
    paths, now = samples(tmp_path)
    assert decision(tmp_path, paths[-1], now, stage="GOLDEN")["concurrency"] == 1


def test_cpu_threads_and_memory_reservation_bound_slots(tmp_path):
    paths, now = samples(tmp_path)
    value = json.loads(paths[-1].read_text())
    proof_path = Path(value["per_tool_capacity_evidence"]["path"])
    proof = json.loads(proof_path.read_text())
    tool = proof["tools"]["emx"]
    tool["observed"]["threads_per_job"] = 16  # floor(256 * .10) / 16 = one.
    source = Path(tool["measurement_source"]["path"])
    write(source, {"tool": "emx", "observed": tool["observed"]})
    tool["measurement_source"] = scheduler.file_record(source)
    write(proof_path, proof)
    value["per_tool_capacity_evidence"] = scheduler.file_record(proof_path)
    write(paths[-1], value)
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 1


@pytest.mark.parametrize("limit", [1, 16, 32])
def test_attempt_profile_requires_matching_queue_limit(limit):
    profile, manifest = _profile()
    pilot = profile["stages"]["PILOT_1000"]
    pilot["max_candidates_per_attempt"] = limit
    queue = next(c for c in pilot["commands"] if c["role"] == "phase_a_queue_builder")
    queue["argv"].extend(["--attempt-candidate-limit", str(limit)])
    queue["argv"].extend(["--frozen-queue-receipt", "/private/source.json",
                          "--frozen-queue-receipt-sha256", "a" * 64])
    assert not validate_execution_profile(profile, backend_manifest=manifest)
    queue["argv"][queue["argv"].index("--attempt-candidate-limit") + 1] = str(limit + 1)
    assert validate_execution_profile(profile, backend_manifest=manifest)


@pytest.mark.parametrize("limit", [0, -1, 33, True, 32.0, "32"])
def test_invalid_attempt_limits_rejected(limit):
    profile, manifest = _profile()
    pilot = profile["stages"]["PILOT_1000"]
    pilot["max_candidates_per_attempt"] = limit
    assert validate_execution_profile(profile, backend_manifest=manifest)


def test_final_launcher_and_backend_read_identical_five_check_decision(tmp_path):
    from tests.test_broadband56_capacity_snapshot_adapter import SWAP_CONTROLLER
    from rfic_transformer_inverse_design.campaigns import broadband56_capacity_snapshot_adapter as adapter

    paths, now = samples(tmp_path)
    source = json.loads(paths[-1].read_text())
    policy = swap.evaluate_capacity_snapshot(source, stage="PILOT_1000", current_accepted=100)
    gate = {
        "schema": swap.GATE_SCHEMA, "overall_status": "PASS", "decision": "READY_FOR_CURRENT_STAGE",
        "campaign_id": source["campaign_id"], "contract_fingerprint_sha256": source["contract_fingerprint_sha256"],
        "resource_policy": source["resource_policy"], "swap_policy": swap.SWAP_POLICY,
        "current_stage": "PILOT_1000", "current_accepted": 100,
        "snapshot_captured_utc": now.isoformat(timespec="seconds"),
        "checks": [{"name": k, "pass": v, "detail": str(v)} for k, v in policy["checks"].items()],
        "evidence": {"resource_snapshot": scheduler.file_record(paths[-1])},
    }
    write(tmp_path / "resource_gates/001/CAPACITY_RESOURCE_GATE.json", gate)
    calls = []
    controller = SimpleNamespace(
        ControllerError=RuntimeError, STOP_REQUESTED=False,
        _read_json=lambda path, label: json.loads(path.read_text()),
        _pilot_bytes_per_geometry=lambda root: None,
        _pilot_safe_concurrency=lambda root: None,
    )

    def backend_admission(**kwargs):
        calls.append(kwargs)
        payload = json.loads(kwargs["snapshot_path"].read_text())
        checked = adapter.evaluate_adapted_capacity_snapshot(payload, stage="PILOT_1000", current_accepted=100)
        final = scheduler.concurrency_for_snapshot(
            snapshot_path=kwargs["snapshot_path"], campaign_root=tmp_path,
            stage="PILOT_1000", current_accepted=100, policy=checked,
            legacy_policy=lambda **kw: pytest.fail("legacy reset reached"),
        )
        assert final["healthy_check_streak"] == 5
        assert final["concurrency"] == kwargs["concurrency"] == 2
        assert final["latest_snapshot"] == scheduler.file_record(paths[-1])
        return final

    launch = SWAP_CONTROLLER._capacity_adapter_stage_launcher_factory(
        controller, original_run_stage_launcher=backend_admission, poll_seconds=60)
    result = launch(inputs={}, campaign_root=tmp_path, stage="PILOT_1000", concurrency=2,
                    snapshot_path=paths[-1], check_index=1)
    assert result["concurrency"] == 2 and len(calls) == 1


@pytest.mark.parametrize("accepted,limit,requested,expected", [
    (100, None, 900, True), (100, None, 32, False),
    (100, 32, 32, True), (100, 32, 900, False),
    (990, 32, 10, True), (990, 32, 32, False),
])
def test_queue_attempt_count_does_not_change_stage_target(tmp_path, accepted, limit, requested, expected):
    from scripts.build_broadband56_phase_a_queue import _campaign_exclusion_paths

    checks = []
    _campaign_exclusion_paths(tmp_path, stage="PILOT_1000", current_accepted=accepted,
                              requested_count=requested, checks=checks, attempt_candidate_limit=limit)
    count_check = next(c for c in checks if c["name"] == "requested_count_matches_next_frozen_checkpoint")
    assert count_check["pass"] is expected
