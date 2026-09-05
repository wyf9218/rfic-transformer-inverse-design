import json
import threading
from datetime import timedelta

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_dispatch as dispatch
from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduler
from tests.test_broadband56_fixed48_scheduling import fixed_samples
from tests.test_broadband56_scheduling import write


def read_receipt(root):
    return json.loads((root / "DISPATCH_RECEIPT.json").read_text())


def test_refills_while_first_candidate_is_still_running(tmp_path):
    later_started = threading.Event()
    order = []
    def run(index):
        order.append(index)
        if index == 0:
            assert later_started.wait(3), "wave barrier prevented refill"
        if index == 7:
            later_started.set()
        return index
    rows = list(dispatch.bounded_completed(12, run, max_workers=2,
                receipt_dir=tmp_path/"dispatch", poll_seconds=.01))
    assert sorted(f.result() for _, f in rows) == list(range(12))
    assert len(set(order)) == len(order) == 12
    receipt = read_receipt(tmp_path/"dispatch")
    assert receipt["peak_inflight_delegates"] == 2
    assert receipt["not_dispatched_indexes"] == []
    assert receipt["accepted_increment"] == 0 and not receipt["native_concurrency_proven"]


def test_capacity_can_expand_and_wait_without_recreating_pool(tmp_path):
    sequence = iter([0, 1, 4])
    values = []
    def gate():
        value = next(sequence, 4)
        values.append(value)
        return value
    result = list(dispatch.bounded_completed(12, lambda i: i, max_workers=4,
                admission=gate, receipt_dir=tmp_path/"dispatch", poll_seconds=.001))
    assert sorted(f.result() for _, f in result) == list(range(12))
    assert values[:3] == [0, 1, 4]
    assert read_receipt(tmp_path/"dispatch")["peak_inflight_delegates"] == 4


def test_gate_failure_drains_healthy_jobs_and_retains_not_dispatched(tmp_path):
    healthy_started, release, healthy_completed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def gate():
        calls.append(1)
        if len(calls) == 1:
            return 2
        assert healthy_started.wait(2)
        release.set()
        raise ValueError("sampler identity drift")
    def run(i):
        if i == 0:
            healthy_started.set()
            assert release.wait(3)
            healthy_completed.set()
        return i
    results = []
    with pytest.raises(scheduler.CapacityPolicyError, match="after drain"):
        for index, future in dispatch.bounded_completed(8, run, max_workers=4,
                admission=gate, receipt_dir=tmp_path/"dispatch", poll_seconds=.01):
            results.append((index, future.result()))
    assert healthy_completed.is_set()
    assert sorted(results) == [(0, 0), (1, 1)]
    receipt = read_receipt(tmp_path/"dispatch")
    assert receipt["submitted_count"] == 2 and receipt["not_dispatched_indexes"] == list(range(2, 8))
    assert receipt["overall_status"] == "INCOMPLETE_DISPATCH"


def test_candidate_error_is_yielded_not_hidden_or_global_stop(tmp_path):
    def run(i):
        if i == 3:
            raise RuntimeError("candidate failure")
        return i
    rows = list(dispatch.bounded_completed(7, run, max_workers=2, receipt_dir=tmp_path/"dispatch"))
    assert sorted(i for i, f in rows if f.exception()) == [3]
    assert read_receipt(tmp_path/"dispatch")["submitted_count"] == 7


@pytest.mark.parametrize("bad", [49, -1, True, None])
def test_invalid_admission_starts_nothing(tmp_path, bad):
    with pytest.raises(scheduler.CapacityPolicyError):
        list(dispatch.bounded_completed(10, lambda _: pytest.fail("dispatched"), max_workers=48,
             admission=lambda: bad, receipt_dir=tmp_path/"dispatch"))
    assert read_receipt(tmp_path/"dispatch")["submitted_count"] == 0


def history_fixture(root, monkeypatch, *, count=6, seats=12):
    paths, now = fixed_samples(root, count=count, seats=seats)
    initial = json.loads(paths[0].read_text())
    history = root / "scheduling_history/stage_000040"
    bindings = {key: initial[key] for key in ("campaign_id", "contract_fingerprint_sha256",
                  "supervisor_lease", "operational_overlay_manifest", "owner_swap_override_receipt")}
    initial_pin = scheduler.file_record(paths[0])
    def publish(path, *, seq=1, status="OBSERVED"):
        state = {"schema": "rfic_transformer.broadband56_stage_resource_history.v1",
                 "bindings": bindings, "stage": "PILOT_1000", "initial_snapshot": initial_pin,
                 "latest_snapshot": scheduler.file_record(path), "overall_status": status, "error": None}
        state_pin = scheduler.file_record(write(history/f"STATE_{seq:06d}.json", state))
        write(history/"LATEST.json", state_pin)
    publish(paths[-2])
    context = {**{k: initial[k] for k in ("campaign_id", "contract_fingerprint_sha256")},
               "schema": "rfic_transformer.broadband56_v2_stage_context.v2",
               "campaign_root": str(root), "stage": "PILOT_1000", "max_concurrency": 48,
               "backend_identity_manifest": scheduler._bound(initial["supervisor_lease"])[1]["backend_identity_manifest"],
               "current_accepted": 761, "stage_resource_history": str(history/"LATEST.json"),
               "initial_resource_snapshot": initial_pin,
               "scheduling_decision": {"fixed_generation_policy": scheduler.FIXED48_GENERATION_POLICY}}
    context_path = write(root/"STAGE_CONTEXT.json", context)
    monkeypatch.setenv("BROADBAND56_STAGE_CONTEXT", str(context_path))
    monkeypatch.setenv("BROADBAND56_STAGE_RESOURCE_HISTORY", str(history/"LATEST.json"))
    return paths, now, publish, context_path


def test_live_history_cached_per_observation_and_shared_policy(tmp_path, monkeypatch):
    paths, _, publish, _ = history_fixture(tmp_path, monkeypatch)
    original = scheduler.healthy_history
    calls = []
    def count(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(scheduler, "healthy_history", count)
    gate = dispatch.stage_admission(48)
    assert gate() == gate() == gate() == 12
    assert len(calls) == 1 and gate.last_decision["healthy_check_streak"] == 5
    publish(paths[-1], seq=2)
    assert gate() == 12 and len(calls) == 2
    assert gate.last_decision["healthy_check_streak"] == 6


def test_sampler_failure_is_not_an_old_cached_pass(tmp_path, monkeypatch):
    paths, _, publish, _ = history_fixture(tmp_path, monkeypatch)
    gate = dispatch.stage_admission(48)
    assert gate() == 12
    publish(paths[-1], seq=2, status="FAIL")
    with pytest.raises(scheduler.CapacityPolicyError, match="sampler failed"):
        gate()


def test_stale_snapshot_waits_without_rescanning_history(tmp_path, monkeypatch):
    _, now, _, _ = history_fixture(tmp_path, monkeypatch)
    gate = dispatch.stage_admission(48)
    assert gate() == 12
    class Clock:
        @staticmethod
        def now(_):
            return now + timedelta(seconds=301)
    monkeypatch.setattr(dispatch, "datetime", Clock)
    monkeypatch.setattr(scheduler, "healthy_history", lambda *a, **kw: pytest.fail("unnecessary scan"))
    assert gate() == 0


@pytest.mark.parametrize("change", ["missing", "wrong_capacity", "wrong_context", "source_drift", "backwards"])
def test_executor_identity_mismatch_fails_closed(tmp_path, monkeypatch, change):
    paths, _, publish, context = history_fixture(tmp_path, monkeypatch)
    if change == "missing":
        monkeypatch.delenv("BROADBAND56_STAGE_RESOURCE_HISTORY")
    elif change == "wrong_context":
        value = json.loads(context.read_text()); value["campaign_id"] = "other"
        write(context, value)
    with pytest.raises(scheduler.CapacityPolicyError):
        gate = dispatch.stage_admission(12 if change == "wrong_capacity" else 48)
        assert gate() == 12
        if change == "source_drift":
            value = json.loads(paths[-2].read_text()); value["current_stage"] = "drift"
            write(paths[-2], value)
        elif change == "backwards":
            publish(paths[-3], seq=2)
        gate()


def test_dispatch_receipts_are_no_clobber(tmp_path):
    root = tmp_path/"dispatch"
    list(dispatch.bounded_completed(0, lambda _: None, max_workers=48, receipt_dir=root))
    before = (root/"DISPATCH_RECEIPT.json").read_bytes()
    with pytest.raises(FileExistsError):
        list(dispatch.bounded_completed(1, lambda _: pytest.fail("launched"), max_workers=48, receipt_dir=root))
    assert (root/"DISPATCH_RECEIPT.json").read_bytes() == before


@pytest.mark.parametrize("admitted,requested,history,ok", [
    (12,48,True,True), (48,48,True,True), (1,48,True,True),
    (0,48,True,False), (12,12,True,False), (12,48,False,False), (48,49,True,False)])
def test_launcher_and_backend_share_pool_capacity_contract(admitted, requested, history, ok):
    decision = {"concurrency": admitted, "target_executor_capacity": 48,
                "fixed_generation_policy": scheduler.FIXED48_GENERATION_POLICY}
    if ok:
        scheduler.validate_executor_capacity(requested, decision, history_ready=history)
    else:
        with pytest.raises(scheduler.CapacityPolicyError):
            scheduler.validate_executor_capacity(requested, decision, history_ready=history)
