"""Synthetic-only recovery reporting: no transport, statistics, models or plots."""
from copy import deepcopy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_reporting_recovery_once as recovery

# Reuse the existing synthetic fixture without collecting or running its tests.
_spec = importlib.util.spec_from_file_location("recovery_synthetic_prior_fixture",
    Path(__file__).with_name("test_frequency_physical_reporting_resume.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
original_frame = _fixture.frame
write, replace, remote_pin = _fixture.write, _fixture.replace, _fixture.remote_pin


@pytest.fixture
def frame(original_frame, tmp_path):
    base = original_frame
    old_native = recovery.read(base.native)
    captures = list(base.captures)
    values = {}
    for job in base.jobs:
        rid = job["request_id"]
        values[rid] = dict(summary=dict(job, N_original=11,
            receipt=remote_pin(f"/native/queue/requests/{rid}/REQUEST_RECEIPT.json", "b"),
            candidates=[dict(q=q, candidate_id=f"{rid}-q{q}") for q in range(10, 21)]), files=[])
    for i in range(2, 72):
        captures.append(write(base.paths.out / f"capture_{i}.json", values[base.jobs[i]["request_id"]]))
    publication = write(base.paths.out / "captures.json", dict(schema="frequency_physical_capture_publication.v1",
        status="PUBLISHED", captures=captures, source_receipts=[base.final, base.request]))
    old_stats = recovery.read(recovery.read(base.snapshot)["statistics"])
    stats = write(base.paths.out / "STATS_RECEIPT.json", dict(old_stats, N_accounted_requests=72))
    figures = write(base.paths.out / "FIGURES_RECEIPT.json", dict(recovery.read(base.figures), statistics=stats,
        N_accounted_requests=72, request_figures=[dict(request_id=j["request_id"], exports=[]) for j in base.jobs[:72]]))
    snapshot = write(base.paths.out / "snapshots/snapshot_0001/SNAPSHOT_RECEIPT.json", dict(
        status="PUBLISHED", remote_modified=False, publication=publication, statistics=stats, figures=figures))
    predecessor = write(base.paths.out / "FINAL_RECEIPT.json", dict(recovery.read(base.final),
        configuration=base.request, original_configuration=base.value["original_configuration"], original_final=base.final,
        native_launch=base.native, report_deadline_utc=base.value["report_deadline_utc"], N_accounted_requests=72,
        N_pending_requests=248, N_seeded_requests=2, latest_snapshot=snapshot))
    launch = write(base.paths.out / "LAUNCH.json", dict(configuration=base.request, pid=125))
    release = dict(code_root="/native/recovery", source_pins=[dict(p,
        path=p["path"].replace(old_native["release"]["code_root"], "/native/recovery"))
        for p in old_native["release"]["source_pins"]])
    release["source_pins"][3]["sha256"] = "c" * 64
    stage = {k: remote_pin("/native/failure/" + k) for k in
             ("request", "intent", "process", "log", "preflight", "old_wrapper")}
    manifest = write(tmp_path / "RECOVERY_MIRROR.json", dict(schema="frequency_physical_resource_wait_recovery.v1",
        base_config=old_native["base_config"], previous_operational_budget=old_native["operational_budget"],
        release=release, acknowledged_failures=[remote_pin("/native/FAILURE.json")], stages=[stage]))
    remote_manifest = dict(manifest, path="/native/RECOVERY_MANIFEST.json")
    native = write(tmp_path / "RECOVERY_LAUNCH.json", dict(schema="frequency_physical_resource_wait_recovery_launch.v1",
        pid=1000, uid=old_native["uid"], start_ticks=5000, base_config=old_native["base_config"],
        operational_budget=old_native["operational_budget"], resource_wait_recovery=remote_manifest,
        release=release, command=old_native["command"] + ["--resource-wait-recovery", remote_manifest["path"]]))
    value = dict(schema=recovery.SCHEMA, implementation=recovery.pin(recovery.__file__),
        original_configuration=base.value["original_configuration"], predecessor_final=predecessor,
        predecessor_launch=launch, native_launch=native, recovery_manifest=dict(local=manifest, remote=remote_manifest),
        report_deadline_utc=base.value["report_deadline_utc"], output=str(tmp_path / "recovery_report"))
    request = write(tmp_path / "RECOVERY_REPORT_REQUEST.json", value)
    state = SimpleNamespace(calls=[], produces=[], new_ids=[], alive=False, mutate=None)
    def remote_call(config, mode, *, fd):
        assert fd is not None and mode == "snapshot"
        assert config["queue_pid"] == 1000 and config["queue_start_ticks"] == 5000
        state.calls.append(mode)
        if state.mutate:
            state.mutate()
        closed = {j["request_id"] for j in base.jobs[:72]} | set(state.new_ids)
        return dict(queue_alive=state.alive, requests=[dict(request_id=j["request_id"],
            receipt=values[j["request_id"]]["summary"]["receipt"] if j["request_id"] in closed else None) for j in base.jobs])
    def collect(config, item, out, fd, caller):
        assert fd is not None and item["request_id"] not in {j["request_id"] for j in base.jobs[:72]}
        state.calls.append(item["request_id"])
        write(out / "completed" / item["request_id"] / "CAPTURE.json", values[item["request_id"]])
    base.original["observer"] = SimpleNamespace(remote_call=remote_call, collect=collect)
    consumer = recovery.prior.load_consumer(None)
    def produce(config, pub, out, index, previous):
        assert config is base.original and previous == figures
        assert config["config_pin"] == base.value["original_configuration"]
        rows = recovery.read(pub)["captures"]
        assert rows[:72] == captures
        state.produces.append((index, previous))
        f = write(out / "snapshots" / f"figures_{index}.json", {"stub": True})
        s = write(out / "snapshots" / f"snapshot_{index}.json", {"stub": True})
        return s, f
    consumer.produce = produce
    return SimpleNamespace(base=base, request=request, value=value, state=state, consumer=consumer, native=native,
        predecessor=predecessor, launch=launch, figures=figures, snapshot=snapshot, captures=captures,
        manifest=manifest, clock=base.clock, out=Path(value["output"]))


def update(frame, key, value):
    frame.value[key] = value
    frame.request = replace(Path(frame.request["path"]), frame.value)


def native_change(frame, callback):
    value = recovery.read(frame.native); callback(value)
    frame.native = replace(Path(frame.native["path"]), value)
    update(frame, "native_launch", frame.native)


def test_preflight_72_seed_metadata_only(frame):
    result = recovery.preflight(frame.request["path"], clock=frame.clock)
    assert result["status"] == "CANDIDATE_PREFLIGHT_PASS_NOT_INSTALLED"
    assert result["N_seeded_requests"] == 72 and result["previous_figures"] == frame.figures
    assert not frame.out.exists() and not frame.state.calls and not frame.state.produces


def test_72_baseline_no_recapture_no_recompute_no_redraw(frame):
    result = recovery.run(frame.request["path"], clock=frame.clock)
    assert result["N_accounted_requests"] == 72 and result["N_new_snapshots"] == 0
    assert result["latest_snapshot"] == frame.snapshot and result["status"] == "PARTIAL"
    assert result["end_reason"] == "PHYSICAL_QUEUE_ENDED_PARTIAL" and result["error"] is None
    assert frame.state.calls == ["snapshot"] and not frame.state.produces


def test_new_closed_request_only_original_science_called_once(frame):
    frame.state.new_ids = ["r072"]
    result = recovery.run(frame.request["path"], clock=frame.clock)
    assert result["N_accounted_requests"] == 73 and result["N_new_snapshots"] == 1
    assert frame.state.calls == ["snapshot", "r072"] and frame.state.produces == [(1, frame.figures)]
    assert result["N_original_candidates"] == 3520 and result["N_pending_requests"] == 247


@pytest.mark.parametrize("change", [
    lambda v: v["command"].extend(["--arbitrary", "yes"]),
    lambda v: v["command"].__setitem__(8, "--recovery"),
    lambda v: v["command"].__setitem__(9, "/native/other.json"),
    lambda v: v["command"].__setitem__(0, "/different/python"),
    lambda v: v.__setitem__("start_ticks", True),
    lambda v: v.__setitem__("uid", 700),
    lambda v: v.__setitem__("schema", "pretend_launch"),
])
def test_native_drift_or_extra_flags_rejected(frame, change):
    native_change(frame, change)
    with pytest.raises(ValueError):
        recovery.preflight(frame.request["path"], clock=frame.clock)
    assert not frame.state.calls and not frame.out.exists()


def test_same_original_reporting_lock_busy_does_not_create(frame):
    fd = os.open(frame.base.original["lock_path"], os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = recovery.run(frame.request["path"], clock=frame.clock)
        assert result["status"] == "ALREADY_RUNNING_NO_DUPLICATE" and not frame.out.exists()
    finally:
        os.close(fd)


def test_repeat_invocation_no_clobber(frame):
    recovery.run(frame.request["path"], clock=frame.clock)
    with pytest.raises(ValueError, match="no-clobber"):
        recovery.run(frame.request["path"], clock=frame.clock)
    assert frame.state.calls == ["snapshot"]


@pytest.mark.parametrize("pid", [123, 124, 125])
def test_any_predecessor_alive_rejected(frame, pid):
    frame.base.state.alive.add(pid)
    with pytest.raises(ValueError):
        recovery.preflight(frame.request["path"], clock=frame.clock)
    assert not frame.state.calls


@pytest.mark.parametrize("key", ["predecessor_final", "predecessor_launch", "native_launch"])
def test_pinned_predecessor_changed_rejected(frame, key):
    with Path(frame.value[key]["path"]).open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="Input changed|predecessor launch"):
        recovery.preflight(frame.request["path"], clock=frame.clock)


def test_missing_terminal_rejected(frame):
    Path(frame.predecessor["path"]).unlink()
    with pytest.raises(FileNotFoundError):
        recovery.preflight(frame.request["path"], clock=frame.clock)


def test_deadline_cannot_be_extended(frame):
    update(frame, "report_deadline_utc", "2026-09-20T20:00:00Z")
    with pytest.raises(ValueError, match="deadline"):
        recovery.preflight(frame.request["path"], clock=frame.clock)


def test_manifest_exact_remote_pin_required(frame):
    value = deepcopy(frame.value["recovery_manifest"])
    value["remote"]["sha256"] = "e" * 64
    update(frame, "recovery_manifest", value)
    with pytest.raises(ValueError, match="mirror"):
        recovery.preflight(frame.request["path"], clock=frame.clock)


def test_recursive_successor_72_keeps_original_publication_and_figures(frame):
    recovery.run(frame.request["path"], clock=frame.clock)
    value = deepcopy(frame.value)
    value["predecessor_final"] = recovery.pin(frame.out / "FINAL_RECEIPT.json")
    value["predecessor_launch"] = recovery.pin(frame.out / "LAUNCH.json")
    value["output"] = str(frame.out.with_name("second_recovery_report"))
    native = recovery.read(frame.native)
    native["pid"], native["start_ticks"] = 1001, 6000
    manifest = recovery.read(frame.manifest)
    manifest["release"]["code_root"] = "/native/recovery_two"
    for p in manifest["release"]["source_pins"]:
        p["path"] = p["path"].replace("/native/recovery/", "/native/recovery_two/")
    local = write(frame.out.parent / "RECOVERY_TWO_MANIFEST.json", manifest)
    remote = dict(local, path="/native/RECOVERY_TWO_MANIFEST.json")
    native.update(release=manifest["release"], resource_wait_recovery=remote)
    native["command"][-1] = remote["path"]
    value["native_launch"] = write(frame.out.parent / "RECOVERY_TWO_LAUNCH.json", native)
    value["recovery_manifest"] = dict(local=local, remote=remote)
    request = write(frame.out.parent / "RECOVERY_TWO_REPORT.json", value)
    result = recovery.preflight(request["path"], clock=frame.clock)
    assert result["N_seeded_requests"] == 72 and result["latest_snapshot"] == frame.snapshot
    assert result["previous_figures"] == frame.figures and frame.state.calls == ["snapshot"]
    assert not frame.state.produces and not Path(value["output"]).exists()


def test_source_drift_during_collection_preserved_partial_not_retry(frame):
    frame.state.new_ids = ["r072"]
    def mutate():
        with Path(frame.predecessor["path"]).open("a") as stream:
            stream.write(" ")
    frame.state.mutate = mutate
    result = recovery.run(frame.request["path"], clock=frame.clock)
    assert result["status"] == "PARTIAL" and result["end_reason"] == "STOPPED_WITH_EVIDENCE_ERROR"
    assert result["N_accounted_requests"] == 72 and not frame.state.produces
    assert Path(frame.out / "FINAL_RECEIPT.json").is_file()


def test_cli_delegates_preflight_only(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["report", "--request", "synthetic.json", "--preflight"])
    monkeypatch.setattr(recovery, "preflight", lambda p: {"path": p, "status": "NOT_INSTALLED"})
    monkeypatch.setattr(recovery, "run", lambda p: pytest.fail("unexpected report run"))
    recovery.main()
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_INSTALLED"
