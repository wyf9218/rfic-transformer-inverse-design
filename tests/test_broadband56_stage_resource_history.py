import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_scheduling as scheduler
from tests.test_broadband56_fixed48_scheduling import fixed_samples
from tests.test_broadband56_scheduling import samples, write


def invoke(root, paths, *, probe, run, stage="PILOT_1000", index=40):
    return scheduler.run_stage_with_resource_history(
        controller=SimpleNamespace(_run_probe=probe), run_stage=run,
        inputs={"probe_script": root/"readonly_probe.sh"}, campaign_root=root,
        stage=stage, concurrency=48, snapshot_path=paths[-2], check_index=index)


def test_collects_during_blocking_stage_without_changing_its_result(tmp_path):
    paths, now = fixed_samples(tmp_path, count=6)
    stage_entered, sample_ready = threading.Event(), threading.Event()
    calls = []
    def probe(script, out, index):
        assert stage_entered.wait(3)
        calls.append((script, out, index))
        sample_ready.set()
        return paths[-1]
    result = {"decision": "CONTINUE_SAMPLING", "accepted_after": 777}
    def run(**kwargs):
        assert kwargs["concurrency"] == 48
        pointer = kwargs["inputs"]["stage_resource_history"]
        assert pointer == tmp_path/"scheduling_history/stage_000040/LATEST.json"
        assert pointer.is_file()
        stage_entered.set()
        assert sample_ready.wait(3)
        return result
    assert invoke(tmp_path, paths, probe=probe, run=run) is result
    assert len(calls) == 1 and calls[0][2] == 40_500_001
    root = tmp_path/"scheduling_history/stage_000040"
    receipt = json.loads((root/"HISTORY_SESSION_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "PASS_SAMPLING_ONLY"
    assert receipt["sampled_sources"] == [scheduler.file_record(paths[-1])]
    assert receipt["accepted_increment"] == 0 and receipt["stage_returned"]
    pointer = json.loads((root/"LATEST.json").read_text())
    path, state = scheduler._bound(pointer)
    assert path.name == "STATE_000002.json"
    assert state["latest_snapshot"] == scheduler.file_record(paths[-1])
    assert (root/"STATE_000001.json").is_file()
    assert not any(t.name == "b56-stage-readonly-resource-history" for t in threading.enumerate())
    history = scheduler.healthy_history(paths[-1], campaign_root=tmp_path,
        stage="PILOT_1000", current_accepted=777, now=now)
    assert history["healthy_check_streak"] == 6


@pytest.mark.parametrize("failure", ["exception", "duplicate", "lease_drift", "future"])
def test_sampler_errors_are_retained_without_discarding_completed_stage(tmp_path, failure):
    paths, _ = fixed_samples(tmp_path, count=6)
    entered, observed = threading.Event(), threading.Event()
    def probe(*_):
        assert entered.wait(3)
        observed.set()
        if failure == "exception":
            raise RuntimeError("read-only probe failed")
        if failure == "duplicate":
            return paths[-2]
        value = json.loads(paths[-1].read_text())
        if failure == "lease_drift":
            value["supervisor_lease"] = {"path": "/not-current", "sha256": "0" * 64}
        if failure == "future":
            value["captured_utc"] = "2099-01-01T00:00:00Z"
        write(paths[-1], value)
        return paths[-1]
    def run(**_):
        entered.set()
        assert observed.wait(3)
        return {"accepted_after": 123}
    assert invoke(tmp_path, paths, probe=probe, run=run) == {"accepted_after": 123}
    root = tmp_path/"scheduling_history/stage_000040"
    receipt = json.loads((root/"HISTORY_SESSION_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "FAIL" and len(receipt["errors"]) == 1
    assert receipt["stage_returned"] and receipt["sampled_sources"] == []
    _, state = scheduler._bound(json.loads((root/"LATEST.json").read_text()))
    assert state["overall_status"] == "FAIL" and state["latest_snapshot"] is None


def test_stage_exception_is_preserved_and_sampler_is_joined(tmp_path):
    paths, _ = fixed_samples(tmp_path, count=6)
    called = threading.Event()
    def probe(*_):
        called.set()
        return paths[-1]
    def run(**_):
        assert called.wait(3)
        raise RuntimeError("stage validation failed")
    with pytest.raises(RuntimeError, match="stage validation failed"):
        invoke(tmp_path, paths, probe=probe, run=run)
    receipt = json.loads((tmp_path/"scheduling_history/stage_000040/HISTORY_SESSION_RECEIPT.json").read_text())
    assert receipt["stage_returned"] is False
    assert not any(t.name == "b56-stage-readonly-resource-history" for t in threading.enumerate())


@pytest.mark.parametrize("stage, fixed", [("GOLDEN", True), ("PILOT_32", True), ("PILOT_1000", False)])
def test_legacy_and_golden_do_not_start_a_sampler(tmp_path, stage, fixed):
    paths, _ = (fixed_samples if fixed else samples)(tmp_path, count=6)
    def run(**kwargs):
        assert "stage_resource_history" not in kwargs["inputs"]
        return {"legacy": True}
    assert invoke(tmp_path, paths, probe=lambda *_: pytest.fail("unexpected probe"),
                  run=run, stage=stage) == {"legacy": True}
    assert not (tmp_path/"scheduling_history").exists()


def test_same_stage_session_cannot_overwrite_history(tmp_path):
    paths, _ = fixed_samples(tmp_path, count=6)
    root = tmp_path/"scheduling_history/stage_000040"
    root.mkdir(parents=True)
    write(root/"original.json", {"preserve": True})
    with pytest.raises(FileExistsError):
        invoke(tmp_path, paths, probe=lambda *_: pytest.fail("probe launched"),
               run=lambda **_: pytest.fail("stage launched"))
    assert json.loads((root/"original.json").read_text())["preserve"]
