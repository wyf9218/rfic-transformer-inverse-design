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


def completed_trial(root, *, index=1, before=98, level=2, observed=None, prior=None):
    """Synthetic software fixture, not native execution or physical acceptance."""
    from tests.test_broadband56_stage_progress import _receipt
    from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import expected_stage_role_order

    observed = level if observed is None else observed
    attempt = root / "stages" / f"{index:06d}_pilot"
    path, progress = _receipt(attempt, attempt_index=index, before=before, accepted=level,
                              raw=level, prior_sha=prior, stage="PILOT_1000", cumulative_target=1000)
    identity = {key: progress[key] for key in ("campaign_id", "contract_fingerprint_sha256", "stage")}
    backend = scheduler.file_record(write(root / f"backend{index}.json", identity))
    progress["backend_identity_manifest_sha256"] = backend["sha256"]
    if before + level == 100:
        progress["round_cumulative_inputs"] = copy.deepcopy(progress["artifacts"])
    write(path, progress)
    final_progress = scheduler.file_record(write(attempt / "backend/final_progress.json", progress))
    context = scheduler.file_record(write(attempt / "backend/STAGE_CONTEXT.json", {
        **identity, "schema": "rfic_transformer.broadband56_v2_stage_context.v2",
        "backend_identity_manifest": backend, "current_accepted": before, "max_concurrency": level,
        "full_campaign_authorization_receipt": {"sha256": progress["full_campaign_authorization_receipt_sha256"]},
    }))
    sample_path = attempt / "backend/PROCESS_SAMPLES.jsonl"
    write(sample_path, {
        "captured_utc": "2026-09-05T09:00:01Z", "verified_utc": "2026-09-05T09:00:02Z",
        "native_processes": [{"pid": i + 2, "uid": 42, "boot_id": "boot", "start_ticks": 2,
                              "tool": "emx"} for i in range(observed)],
        "counts": {"cadence": 0, "calibre": 0, "emx": observed},
    })
    native = scheduler.file_record(write(attempt / "backend/NATIVE_ROLE_OBSERVATION.json", {
        "schema": "rfic_transformer.broadband56_native_role_observation.v1",
        "role": "exact_audited_gds_emx_runner", "bindings": {"stage_context": context, "backend_manifest": backend},
        "command_argv_sha256": "c" * 64, "observation_status": "RECORDED",
        "backend_admitted_max_concurrency": level, "executor_argv_max_concurrency": level,
        "sampled_peak_native_concurrency": {"cadence": 0, "calibre": 0, "emx": observed},
        "sample_count": 1, "samples": scheduler.file_record(sample_path),
        "started_utc": "2026-09-05T09:00:00Z", "finished_utc": "2026-09-05T09:01:00Z",
        "root_process": {"pid": 1, "uid": 42, "boot_id": "boot", "start_ticks": 1},
    }))
    expected = list(expected_stage_role_order("PILOT_1000"))
    roles = []
    for role in expected[:expected.index("stage_attempt_finalizer") + 1]:
        receipt = {**identity, "overall_status": "PASS"}
        if role == "stage_attempt_finalizer":
            receipt.update(accepted_before=before, accepted_after=before + level,
                           decision=progress["decision"], progress_receipt=final_progress,
                           simulator_invoked_by_finalizer=False)
        if role == "exact_audited_gds_emx_runner":
            receipt.update(max_concurrency=level, emx_pass_count=level, emx_fail_count=0)
        if role == "full_band_s4p_qa_builder":
            receipt.update(qa_pass_count=level, qa_fail_count=0)
        roles.append({"role": role, "return_code": 0, "command_argv_sha256": "c" * 64,
                      "receipt": scheduler.file_record(write(attempt / f"backend/{role}.json", receipt))})
        if role == "exact_audited_gds_emx_runner":
            roles[-1]["native_observation"] = {"receipt": native}
    trace_path = write(attempt / "backend/STAGE_EXECUTION_TRACE.json", {
        **identity, "schema": "rfic_transformer.broadband56_v2_stage_execution_trace.v1",
        "overall_status": "INCOMPLETE", "decision": progress["decision"],
        "all_role_return_codes_zero": True, "all_role_receipts_pass": True,
        "expected_terminal_role_order": expected, "role_order": [r["role"] for r in roles], "roles": roles,
    })
    return path, trace_path


def mutate_native(trace_path, mutate):
    trace = json.loads(trace_path.read_text())
    emx = next(r for r in trace["roles"] if r["role"] == "exact_audited_gds_emx_runner")
    native_path = Path(emx["native_observation"]["receipt"]["path"])
    native = json.loads(native_path.read_text())
    mutate(native)
    emx["native_observation"]["receipt"] = scheduler.file_record(write(native_path, native))
    write(trace_path, trace)


def test_completed_two_worker_attempt_enables_four_not_claimed_benchmark(tmp_path):
    completed_trial(tmp_path)
    paths, now = samples(tmp_path)
    result = decision(tmp_path, paths[-1], now)
    assert result["concurrency"] == result["requested_concurrency"] == 4
    assert result["proven_pilot_limit"] == 2
    assert result["benchmark_levels_completed"] == []
    assert result["production_optimum_proven"] is False


def test_real_observation_of_only_one_does_not_prove_requested_two(tmp_path):
    completed_trial(tmp_path, observed=1)
    paths, now = samples(tmp_path)
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 2


def test_uncommitted_backend_progress_is_not_an_expansion_receipt(tmp_path):
    path, _ = completed_trial(tmp_path)
    path.unlink()
    assert scheduler.completed_pilot_execution(tmp_path, current_accepted=100)["proven_pilot_limit"] == 1


def test_completed_two_does_not_override_fresh_health_or_hard_gates(tmp_path):
    completed_trial(tmp_path)
    paths, now = samples(tmp_path, mutations={4: lambda s: s["resources"].update(load_5m=256)})
    assert decision(tmp_path, paths[-1], now)["concurrency"] == 1


def test_per_tool_cap_three_must_not_silently_launch_unrequested_level_three(tmp_path):
    completed_trial(tmp_path)
    paths, now = samples(tmp_path)
    payload = json.loads(paths[-1].read_text())
    proof_path = Path(payload["per_tool_capacity_evidence"]["path"])
    proof = json.loads(proof_path.read_text())
    item = proof["tools"]["emx"]
    item["observed"]["free_seats"] = 3
    source = Path(item["measurement_source"]["path"])
    item["measurement_source"] = scheduler.file_record(write(source, {"tool": "emx", "observed": item["observed"]}))
    payload["per_tool_capacity_evidence"] = scheduler.file_record(write(proof_path, proof))
    write(paths[-1], payload)
    result = decision(tmp_path, paths[-1], now)
    assert result["requested_concurrency"] == 4
    assert result["hard_cap"] == 3 and result["concurrency"] == 2


@pytest.mark.parametrize("change", [
    lambda n: n.update(observation_status="PARTIAL", sampled_peak_native_concurrency=None),
    lambda n: n.update(executor_argv_max_concurrency=1),
])
def test_partial_or_different_executor_does_not_prove_two(tmp_path, change):
    _, trace = completed_trial(tmp_path)
    mutate_native(trace, change)
    assert scheduler.completed_pilot_execution(tmp_path, current_accepted=100)["proven_pilot_limit"] == 1


def test_future_acceptance_cannot_authorize_expansion(tmp_path):
    completed_trial(tmp_path, before=100)
    with pytest.raises(CapacityPolicyError, match="committed valid progress"):
        scheduler.completed_pilot_execution(tmp_path, current_accepted=100)


def test_four_requires_preceding_two_and_does_not_authorize_eight(tmp_path):
    path, _ = completed_trial(tmp_path)
    completed_trial(tmp_path, index=2, before=100, level=4, prior=scheduler.file_record(path)["sha256"])
    proof = scheduler.completed_pilot_execution(tmp_path, current_accepted=104)
    assert proof["proven_pilot_limit"] == 4
    assert proof["benchmark_levels_completed"] == []


def test_unproven_four_trial_rejected(tmp_path):
    completed_trial(tmp_path, level=4, before=96)
    with pytest.raises(CapacityPolicyError, match="prior completed two"):
        scheduler.completed_pilot_execution(tmp_path, current_accepted=100)


@pytest.mark.parametrize("change", [
    lambda t: t["roles"].reverse(),
    lambda t: t.update(role_order=[]),
    lambda t: t["roles"][0].update(return_code=1),
    lambda t: t.update(decision="FAIL"),
])
def test_incomplete_or_wrong_order_trace_rejected(tmp_path, change):
    _, trace_path = completed_trial(tmp_path)
    trace = json.loads(trace_path.read_text())
    change(trace)
    write(trace_path, trace)
    with pytest.raises(CapacityPolicyError, match="completed role chain"):
        scheduler.completed_pilot_execution(tmp_path, current_accepted=100)


def test_failed_role_cannot_hide_behind_all_receipts_pass_flag(tmp_path):
    _, trace_path = completed_trial(tmp_path)
    trace = json.loads(trace_path.read_text())
    entry = next(r for r in trace["roles"] if r["role"] == "calibre_runner")
    receipt_path = Path(entry["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text())
    receipt["overall_status"] = "FAIL"
    entry["receipt"] = scheduler.file_record(write(receipt_path, receipt))
    write(trace_path, trace)
    with pytest.raises(CapacityPolicyError, match="physical role receipt"):
        scheduler.completed_pilot_execution(tmp_path, current_accepted=100)


@pytest.mark.parametrize("role,field", [
    ("exact_audited_gds_emx_runner", "emx_fail_count"), ("full_band_s4p_qa_builder", "qa_fail_count"),
])
def test_partition_pass_with_tool_failures_does_not_authorize_expansion(tmp_path, role, field):
    _, trace_path = completed_trial(tmp_path)
    trace = json.loads(trace_path.read_text())
    entry = next(r for r in trace["roles"] if r["role"] == role)
    path = Path(entry["receipt"]["path"])
    receipt = json.loads(path.read_text())
    receipt[field] = 1
    entry["receipt"] = scheduler.file_record(write(path, receipt))
    write(trace_path, trace)
    assert scheduler.completed_pilot_execution(tmp_path, current_accepted=100)["proven_pilot_limit"] == 1


@pytest.mark.parametrize("change", [
    lambda s: s["native_processes"][1].update(pid=2),
    lambda s: s["native_processes"][1].update(uid=43),
    lambda s: s["native_processes"][1].update(start_ticks=0),
    lambda s: s.update(verified_utc="2026-09-05T08:59:00Z"),
    lambda s: s["counts"].update(emx=1),
])
def test_native_counts_require_distinct_simultaneous_identity_records(tmp_path, change):
    _, trace_path = completed_trial(tmp_path)

    def mutate(native):
        path = Path(native["samples"]["path"])
        sample = json.loads(path.read_text())
        change(sample)
        native["samples"] = scheduler.file_record(write(path, sample))

    mutate_native(trace_path, mutate)
    with pytest.raises(CapacityPolicyError, match="simultaneous distinct"):
        scheduler.completed_pilot_execution(tmp_path, current_accepted=100)


def test_observation_publication_exposes_only_complete_json(tmp_path, monkeypatch):
    from tests.test_broadband56_capacity_snapshot_adapter import SWAP_CONTROLLER

    target = tmp_path / "swap_override_complete.json"
    link = SWAP_CONTROLLER.os.link

    def publish(source, destination):
        assert not list(tmp_path.glob("swap_override_*.json"))
        assert json.loads(Path(source).read_text()) == {"pass": True}
        link(source, destination)

    monkeypatch.setattr(SWAP_CONTROLLER.os, "link", publish)
    SWAP_CONTROLLER._write_json_exclusive(target, {"pass": True})
    assert json.loads(target.read_text()) == {"pass": True}
    assert not list(tmp_path.glob("*.pending"))


def test_observation_publication_collision_preserves_both_evidence_files(tmp_path):
    from tests.test_broadband56_capacity_snapshot_adapter import SWAP_CONTROLLER

    target = write(tmp_path / "swap_override_existing.json", {"original": True})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        SWAP_CONTROLLER._write_json_exclusive(target, {"new": True})
    assert target.read_bytes() == before
    pending, = tmp_path.glob("*.pending")
    assert json.loads(pending.read_text()) == {"new": True}


def test_incomplete_observation_write_is_not_visible_as_a_health_sample(tmp_path):
    from tests.test_broadband56_capacity_snapshot_adapter import SWAP_CONTROLLER

    target = tmp_path / "swap_override_invalid.json"
    with pytest.raises(TypeError):
        SWAP_CONTROLLER._write_json_exclusive(target, {"invalid": object()})
    assert not target.exists()
    assert len(list(tmp_path.glob("*.pending"))) == 1
