"""Synthetic-only successor reporting tests; no transport, science or renderer."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_reporting_resume as resume


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True)
    return resume.pin(path)


def replace(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    return resume.pin(path)


def remote_pin(path, key="a"):
    return dict(path=path, sha256=key * 64, bytes=17)


@pytest.fixture
def frame(tmp_path, monkeypatch):
    paths = SimpleNamespace(old=tmp_path / "old", once=tmp_path / "once", out=tmp_path / "successor")
    paths.old.mkdir(); paths.once.mkdir()
    state = SimpleNamespace(calls=[], produces=[], publications=[], alive=set(), load_calls=[],
                            failures=None, new_ids=[], queue_alive=False)
    jobs = [dict(request_id=f"r{i:03d}", dispatch_order=i, frequency_ghz=5 + i % 16,
                 model_id=f"f{5+i%16}-formal", dataset_scope="FORMAL_10K", q_proxy=14) for i in range(320)]
    originals = {j["request_id"]: [dict(q_target=q, candidate_id=f'{j["request_id"]}-q{q}')
                                  for q in range(10, 21)] for j in jobs}
    physical = remote_pin("/native/original/CONFIG.json")
    source = write(tmp_path / "consumer.py", {"synthetic": True})
    reader = write(tmp_path / "reader.py", {"synthetic": "read-only"})
    base_value = dict(schema="frequency_incremental_statistics_consumer.v1", consumer_source=source,
                      reporting_remote_reader=reader, deadline_utc="2026-09-08T18:15:00Z",
                      output=str(paths.old), lock_path=str(tmp_path / "original.lock"), source_pins=[source, reader])
    base_pin = write(tmp_path / "BASE_CONFIG.json", base_value)
    Path(base_value["lock_path"]).touch()
    monkeypatch.setattr(resume, "BASE_CONFIG_SHA", base_pin["sha256"])
    monkeypatch.setattr(resume, "CONSUMER_SHA", source["sha256"])
    monkeypatch.setattr(resume, "READER_SHA", reader["sha256"])

    def capture_value(job):
        rid = job["request_id"]
        expected = remote_pin(f"/native/queue/requests/{rid}/REQUEST_RECEIPT.json", "b")
        summary = dict(job, N_original=11, receipt=expected,
                       candidates=[dict(q=q, candidate_id=f"{rid}-q{q}") for q in range(10, 21)])
        return dict(summary=summary, files=[], verified_remote=[])

    capture_values = {j["request_id"]: capture_value(j) for j in jobs}
    captures = [write(paths.old / f"capture_{i}.json", capture_values[jobs[i]["request_id"]]) for i in range(2)]
    publication = write(paths.old / "captures.json", dict(schema="frequency_physical_capture_publication.v1",
                        status="PUBLISHED", captures=captures, source_receipts=[]))
    summary = write(paths.old / "SUMMARY.json", {"N_accounted_requests": 2})
    manifest = write(paths.old / "MANIFEST.json", dict(schema="frequency_physical_statistics_manifest.v1",
                                                     artifacts=[summary]))
    sums = write(paths.old / "SHA256SUMS", {"stub": True})
    stats = write(paths.old / "STATS_RECEIPT.json", dict(schema="frequency_physical_statistics_receipt.v1",
                  status="PUBLISHED", N_accounted_requests=2, N_original_candidates=3520,
                  summary=summary, manifest=manifest, sha256sums=sums))
    export = write(paths.old / "old.png", {"not": "real graphics"})
    figures = write(paths.old / "FIGURES_RECEIPT.json", dict(schema="frequency_physical_statistics_figures_receipt.v1",
                    status="COMPLETE", statistics=stats, N_accounted_requests=2,
                    N_original_candidates=3520, N_original_requests=320,
                    request_figures=[dict(request_id=j["request_id"], exports=[export]) for j in jobs[:2]]))
    snapshot = write(paths.old / "SNAPSHOT_RECEIPT.json", dict(status="PUBLISHED", remote_modified=False,
                     publication=publication, statistics=stats, figures=figures))
    final = write(paths.old / "FINAL_RECEIPT.json", dict(status="PARTIAL", end_reason="DEADLINE_PARTIAL", error=None,
                  N_accounted_requests=2, N_original_requests=320, N_original_candidates=3520, N_pending_requests=318,
                  configuration=base_pin, latest_snapshot=snapshot, remote_modified=False, simulation_or_training_started=False))
    launch = write(paths.old / "LAUNCH.json", dict(configuration=base_pin, pid=123))
    once_config = write(paths.once / "CONFIG.json", dict(schema="frequency_physical_export_once.v1",
                        producer_configuration=base_pin, producer=dict(pid=123), final_receipt=final["path"],
                        wait_output=str(paths.once)))
    once_launch = write(paths.once / "WAIT_LAUNCH_RECEIPT.json", dict(configuration=once_config, pid=124))
    once = write(paths.once / "ONCE_RECEIPT.json", dict(status="FINITE_EXPORT_EXITED_ZERO_REVIEW_REQUIRED",
                 configuration=once_config, final_receipt=final))
    code_root = "/native/new-release"
    names = ["research/__init__.py", "research/broadband56_nn/__init__.py",
             *["research/broadband56_nn/" + x for x in ("io.py", "frequency_physical_dispatch.py",
                "frequency_research_emx.py", "frequency_research_gds_audit.py", "frequency_research_calibre.py")]]
    budget = remote_pin("/native/new-release/BUDGET.json")
    native = write(tmp_path / "CHILD_LAUNCH_RECEIPT.json", dict(
        schema="frequency_physical_resume_child_launch.v1", status="EXISTING_DISPATCHER_STARTED_NOT_COMPLETION",
        pid=999, uid=1001, start_ticks=4000, base_config=physical, operational_budget=budget,
        release=dict(code_root=code_root, source_pins=[remote_pin(code_root + "/" + x) for x in names]),
        command=["/native/python", "-B", "-m", "research.broadband56_nn.frequency_physical_dispatch",
                 "--config", physical["path"], "--operational-budget", budget["path"]],
        waiter_configuration=remote_pin("/native/new-release/WAIT_CONFIG.json"),
        dispatch_intent=remote_pin("/native/new-release/DISPATCH_INTENT.json")))
    request_value = dict(schema=resume.SCHEMA, implementation=resume.pin(resume.__file__),
        original_configuration=base_pin, original_final=final, original_launch=launch,
        reportonce_terminal=once, reportonce_launch=once_launch, native_launch=native,
        report_deadline_utc="2026-09-09T06:00:00Z", output=str(paths.out))
    request = write(tmp_path / "RESUME.json", request_value)

    def remote_call(config, mode, *, fd=None, **kwargs):
        assert fd is not None
        assert config["remote_config"] == physical
        assert config["queue_pid"] == 999 and config["queue_start_ticks"] == 4000
        state.calls.append((mode, kwargs.get("request_id")))
        if mode == "snapshot":
            ids = {j["request_id"] for j in jobs[:2]} | set(state.new_ids)
            return dict(queue_alive=state.queue_alive, requests=[dict(request_id=j["request_id"],
                receipt=capture_values[j["request_id"]]["summary"]["receipt"] if j["request_id"] in ids else None)
                for j in jobs])
        return capture_values[kwargs["request_id"]]

    def collect(config, item, out, fd, caller):
        value = caller(config, "capture", fd=fd, request_id=item["request_id"], expected=item["receipt"])
        dest = out / "completed" / item["request_id"] / "CAPTURE.json"
        write(dest, value)
        if state.failures == "collect":
            raise RuntimeError("synthetic partial capture")

    def publish(out, entries, sources, index):
        state.publications.append(list(entries))
        return write(out / "publications" / f"captures_{index:04d}.json",
                     dict(captures=entries, source_receipts=sources))

    def produce(config, publication_pin, out, index, previous):
        assert config is original
        assert config["deadline_utc"] == base_value["deadline_utc"]
        assert config["remote"]["queue_pid"] == 111
        state.produces.append((index, previous))
        if state.failures == "produce":
            write(out / "snapshots" / f"partial_{index}.json", {"failed": True})
            raise RuntimeError("synthetic figure failure")
        f = write(out / "snapshots" / f"figures_{index}.json", {"synthetic": "no real rendering"})
        s = write(out / "snapshots" / f"snapshot_{index}.json", {"figures": f, "publication": publication_pin})
        return s, f

    original = dict(base_value, config_pin=base_pin, jobs=jobs,
                    remote=dict(remote_config=physical, queue_pid=111, queue_start_ticks=222,
                                originals=originals, remote_reader_source=Path(reader["path"]).read_text()),
                    observer=SimpleNamespace(remote_call=remote_call, collect=collect))

    def load_original(path):
        state.load_calls.append(resume.pin(path))
        assert json.loads(Path(path).read_text()) == base_value
        return original

    consumer = SimpleNamespace(load_config=load_original,
        current_process=lambda pid: "still alive" if pid in state.alive else None,
        save=write, publish_capture_set=publish, produce=produce)
    monkeypatch.setattr(resume, "load_consumer", lambda item: consumer)
    return SimpleNamespace(paths=paths, state=state, jobs=jobs, request=request, value=request_value,
                           original=original, figures=figures, snapshot=snapshot, captures=captures,
                           final=final, native=native, publication=publication,
                           clock=lambda: datetime(2026, 9, 8, 18, 20, tzinfo=timezone.utc))


def update_request(frame, key, value):
    frame.value[key] = value
    frame.request = replace(Path(frame.request["path"]), frame.value)


def test_preflight_uses_original_validator_no_output_or_remote(frame):
    result = resume.preflight(frame.request["path"], clock=frame.clock)
    assert result["N_seeded_requests"] == 2 and result["previous_figures"] == frame.figures
    assert not frame.paths.out.exists() and not frame.state.calls and not frame.state.produces
    assert frame.state.load_calls == [frame.value["original_configuration"]]


def test_no_new_request_no_recollect_or_baseline(frame):
    result = resume.run(frame.request["path"], clock=frame.clock)
    assert result["latest_snapshot"] == frame.snapshot and result["N_new_snapshots"] == 0
    assert result["status"] == "PARTIAL" and result["end_reason"] == "PHYSICAL_QUEUE_ENDED_PARTIAL"
    assert frame.state.calls == [("snapshot", None)] and not frame.state.produces


def test_each_new_request_one_snapshot_reuses_previous(frame):
    frame.state.new_ids = ["r002", "r003"]
    result = resume.run(frame.request["path"], clock=frame.clock)
    assert result["N_accounted_requests"] == 4 and result["N_new_snapshots"] == 2
    assert frame.state.calls == [("snapshot", None), ("capture", "r002"), ("capture", "r003")]
    assert frame.state.produces[0][1] == frame.figures
    assert frame.state.produces[1][1] == resume.pin(frame.paths.out / "snapshots/figures_1.json")
    assert [len(p) for p in frame.state.publications] == [3, 4]
    assert frame.state.publications[0][:2] == frame.captures


@pytest.mark.parametrize("pid", [123, 124])
def test_original_consumer_and_once_must_exit(frame, pid):
    frame.state.alive.add(pid)
    with pytest.raises(ValueError, match="has not exited"):
        resume.preflight(frame.request["path"], clock=frame.clock)
    assert not frame.paths.out.exists() and not frame.state.calls


def test_failed_once_is_preserved_not_a_new_visual_gate(frame):
    old = resume.read(frame.value["reportonce_terminal"])
    failed = write(frame.paths.once / "FAILURE_RECEIPT.json", dict(configuration=old["configuration"],
                   status="FAIL_PRESERVED_NO_RETRY", error="original export failed"))
    update_request(frame, "reportonce_terminal", failed)
    result = resume.preflight(frame.request["path"], clock=frame.clock)
    assert result["reportonce_status"] == "FAIL_PRESERVED_NO_RETRY"
    assert (frame.paths.once / "ONCE_RECEIPT.json").exists()


@pytest.mark.parametrize("mutation", ["error", "complete", "count"])
def test_bad_original_terminal_never_seeds(frame, mutation):
    final = resume.read(frame.final)
    if mutation == "error":
        final.update(error="source changed", end_reason="STOPPED_WITH_EVIDENCE_ERROR")
    elif mutation == "complete":
        final["status"] = "COMPLETE"
    else:
        final["N_original_candidates"] = 22
    new = replace(Path(frame.final["path"]), final)
    once = resume.read(frame.value["reportonce_terminal"]); once["final_receipt"] = new
    update_request(frame, "reportonce_terminal", replace(Path(frame.value["reportonce_terminal"]["path"]), once))
    update_request(frame, "original_final", new)
    with pytest.raises(ValueError):
        resume.preflight(frame.request["path"], clock=frame.clock)
    assert not frame.state.calls


def test_seed_capture_hash_change_is_sticky_input_failure(frame):
    Path(frame.captures[0]["path"]).write_text("changed")
    with pytest.raises(ValueError, match="Input changed"):
        resume.preflight(frame.request["path"], clock=frame.clock)


@pytest.mark.parametrize("field,value", [("pid", None), ("start_ticks", None), ("uid", None),
                                        ("base_config", remote_pin("/native/another/CONFIG.json"))])
def test_native_identity_must_be_actual_and_same_base(frame, field, value):
    native = resume.read(frame.native); native[field] = value
    update_request(frame, "native_launch", replace(Path(frame.native["path"]), native))
    with pytest.raises(ValueError):
        resume.preflight(frame.request["path"], clock=frame.clock)


def test_new_release_not_old_wrapper_whitelist(frame):
    config = resume.load_config(frame.request["path"])
    assert config["native"]["release"]["code_root"] == "/native/new-release"
    assert config["remote"]["remote_reader_source"] == frame.original["remote"]["remote_reader_source"]
    assert config["remote"]["queue_pid"] == 999 and frame.original["remote"]["queue_pid"] == 111


def test_wrong_native_command_rejected(frame):
    native = resume.read(frame.native); native["command"][3] = "unrelated.module"
    update_request(frame, "native_launch", replace(Path(frame.native["path"]), native))
    with pytest.raises(ValueError, match="existing dispatcher"):
        resume.preflight(frame.request["path"], clock=frame.clock)


def test_new_deadline_separate_and_later(frame):
    update_request(frame, "report_deadline_utc", "2026-09-08T18:15:00Z")
    with pytest.raises(ValueError, match="separately later"):
        resume.preflight(frame.request["path"], clock=frame.clock)


def test_original_lock_no_duplicate(frame):
    fd = os.open(frame.original["lock_path"], os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = resume.run(frame.request["path"], clock=frame.clock)
        assert result["status"] == "ALREADY_RUNNING_NO_DUPLICATE"
        assert not frame.paths.out.exists() and not frame.state.calls
    finally:
        os.close(fd)


@pytest.mark.parametrize("failure", ["collect", "produce"])
def test_partial_output_preserved_and_never_retried(frame, failure):
    frame.state.new_ids = ["r002"]
    frame.state.failures = failure
    result = resume.run(frame.request["path"], clock=frame.clock)
    assert result["status"] == "PARTIAL" and result["end_reason"] == "STOPPED_WITH_EVIDENCE_ERROR"
    assert result["N_accounted_requests"] == 2 and result["latest_snapshot"] == frame.snapshot
    pins = {str(p): resume.pin(p) for p in frame.paths.out.rglob("*") if p.is_file()}
    before_calls = list(frame.state.calls)
    with pytest.raises(ValueError, match="Fresh output"):
        resume.run(frame.request["path"], clock=frame.clock)
    assert before_calls == frame.state.calls
    assert pins == {str(p): resume.pin(p) for p in frame.paths.out.rglob("*") if p.is_file()}


def test_ordinary_loop_only_120_seconds_no_unchanged_render(frame):
    frame.state.queue_alive = True
    moments = [frame.clock()]
    sleeps = []
    def sleeper(seconds):
        sleeps.append(seconds)
        frame.state.queue_alive = False
    result = resume.run(frame.request["path"], clock=lambda: moments[0], sleep=sleeper)
    assert sleeps == [120] and result["N_new_snapshots"] == 0
    assert frame.state.calls == [("snapshot", None), ("snapshot", None)]


def test_seed_remote_receipt_change_stops_without_recollection(frame):
    old = frame.original["observer"].remote_call
    def changed(config, mode, **kwargs):
        value = old(config, mode, **kwargs)
        value["requests"][0]["receipt"] = None
        return value
    frame.original["observer"].remote_call = changed
    result = resume.run(frame.request["path"], clock=frame.clock)
    assert result["end_reason"] == "STOPPED_WITH_EVIDENCE_ERROR" and "terminal changed" in result["error"]
    assert len(frame.state.calls) == 1 and not frame.state.produces


def test_source_mutation_before_produce_is_not_published(frame):
    frame.state.new_ids = ["r002"]
    old = frame.original["observer"].collect
    def changed(*args, **kwargs):
        old(*args, **kwargs)
        Path(frame.value["native_launch"]["path"]).write_text("source changed")
    frame.original["observer"].collect = changed
    result = resume.run(frame.request["path"], clock=frame.clock)
    assert result["end_reason"] == "STOPPED_WITH_EVIDENCE_ERROR"
    assert result["N_new_requests"] == 0
    assert not frame.state.produces
