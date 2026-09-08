"""Synthetic dependency handoff only: no SSH, plotting, models or native tools."""
import base64
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from research.broadband56_nn import frequency_physical_reporting_handoff_once as handoff


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
    return handoff.once.pin(path)


def replace(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return handoff.once.pin(path)


@pytest.fixture
def frame(tmp_path, monkeypatch):
    old, once_out, native_out = [tmp_path / x for x in ("old", "once", "native/run")]
    for directory in (old, once_out, native_out):
        directory.mkdir(parents=True)
    state = SimpleNamespace(alive={123: False, 124: False}, observed=[], calls=[], preflights=[], sleeps=[])
    birth = lambda pid: dict(pid=pid, uid=os.getuid(), lstart="Tue Sep 8 10:00:00 2026", args_sha256="a" * 64)
    base = write(tmp_path / "BASE.json", dict(schema="frequency_incremental_statistics_consumer.v1",
                  output=str(old), deadline_utc="2026-09-08T18:15:00Z"))
    monkeypatch.setattr(handoff.reporter, "BASE_CONFIG_SHA", base["sha256"])
    snapshot = write(old / "SNAPSHOT_RECEIPT.json", {"synthetic": "not a real snapshot"})
    final_value = dict(status="PARTIAL", end_reason="DEADLINE_PARTIAL", error=None, configuration=base,
        N_original_requests=320, N_original_candidates=3520, N_accounted_requests=40, N_pending_requests=280,
        latest_snapshot=snapshot, remote_modified=False, simulation_or_training_started=False)
    final = write(old / "FINAL_RECEIPT.json", final_value)
    write(old / "LAUNCH.json", dict(pid=123, configuration=base))
    once_config = write(tmp_path / "ONCE_CONFIG.json", dict(schema="frequency_physical_export_once.v1",
        producer=birth(123), producer_configuration=base, final_receipt=final["path"], wait_output=str(once_out)))
    write(once_out / "WAIT_LAUNCH_RECEIPT.json", dict(pid=124, configuration=once_config))
    once_value = dict(status="FINITE_EXPORT_EXITED_ZERO_REVIEW_REQUIRED", configuration=once_config, final_receipt=final)
    write(once_out / "ONCE_RECEIPT.json", once_value)
    native_config = write(tmp_path / "native/WAIT_CONFIG.json", dict(schema="frequency_physical_resume_once.v1", out=str(native_out)))
    child_value = dict(schema="frequency_physical_resume_child_launch.v1", status="EXISTING_DISPATCHER_STARTED_NOT_COMPLETION",
                       waiter_configuration=native_config, pid=999, uid=1001, start_ticks=4000)
    child = write(native_out / "CHILD_LAUNCH_RECEIPT.json", child_value)
    sources = [handoff.once.pin(p) for p in (handoff.__file__, handoff.once.__file__, handoff.reporter.__file__,
               Path(handoff.__file__).parent / "io.py", Path(handoff.__file__).parent / "__init__.py")]
    config = dict(schema=handoff.SCHEMA, once_configuration=once_config, once_process=birth(124),
        nativewait_configuration=native_config, remote_child_receipt_path=child["path"],
        ssh_argv=["ssh", "-S", "/synthetic/socket", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                  "-o", "ConnectTimeout=10", "synthetic@example.invalid"], remote_python="/synthetic/python",
        source_pins=sources, python=sys.executable, cwd=str(Path(handoff.__file__).resolve().parents[2]),
        wait_output=str(tmp_path / "wait_output"), reporter_output=str(tmp_path / "reporter_output"),
        wait_deadline_utc="2026-09-10T19:00:00Z", report_deadline_utc="2026-09-10T20:00:00Z", poll_seconds=60)
    request = write(tmp_path / "REQUEST.json", config)
    moments = [datetime(2026, 9, 8, 18, 20, tzinfo=timezone.utc)]

    def same(process):
        state.observed.append(process["pid"])
        return state.alive[process["pid"]]
    monkeypatch.setattr(handoff.once, "same_producer", same)
    monkeypatch.setattr(handoff, "now", lambda: moments[0])

    def preflight(path):
        value = json.loads(Path(path).read_text())
        state.preflights.append(value)
        assert len(value) == 10 and value["schema"] == handoff.reporter.SCHEMA
        assert value["original_configuration"] == base
        assert value["original_final"] == handoff.once.pin(old / "FINAL_RECEIPT.json")
        assert handoff.once.checked(value["native_launch"]).read_bytes() == Path(child["path"]).read_bytes()
        assert not any(state.alive.values())
        return dict(status="SYNTHETIC_PREFLIGHT_NO_SCIENCE")
    def reporter_run(path):
        state.calls.append(str(path))
        out = Path(config["reporter_output"]); out.mkdir()
        result = dict(status="PARTIAL", N_accounted_requests=40, N_new_requests=0, baseline_rerender=False)
        write(out / "FINAL_RECEIPT.json", result)
        return result
    monkeypatch.setattr(handoff.reporter, "preflight", preflight)
    monkeypatch.setattr(handoff.reporter, "run", reporter_run)
    return SimpleNamespace(config=config, request=request, state=state, old=old, once_out=once_out,
        native_out=native_out, native_config=native_config, child=child, child_value=child_value,
        final_value=final_value, once_value=once_value, moments=moments, clock=lambda: moments[0])


def artifact(path):
    raw = Path(path).read_bytes()
    return dict(remote=handoff.once.pin(path), base64=base64.b64encode(raw).decode())


def child_reader(frame):
    return lambda config: dict(state="CHILD", artifacts=[artifact(frame.child["path"])])


def update_config(frame, key, value):
    frame.config[key] = value
    frame.request = replace(Path(frame.request["path"]), frame.config)


def literal_reader(frame):
    payload = {k: frame.config[k] for k in ("nativewait_configuration", "remote_child_receipt_path")}
    result = io.StringIO()
    with contextlib.redirect_stdout(result):
        prior = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            exec(handoff.REMOTE_READ, {"__name__": "synthetic_readonly_reader"})
        finally:
            sys.stdin = prior
    return json.loads(result.getvalue())


def test_check_is_local_no_outputs_or_reporter(frame):
    frame.state.alive[123] = True
    result = handoff.preflight(frame.request["path"])
    assert result["producer_alive"] == [True, False] and result["remote_calls"] == 0
    assert not Path(frame.config["wait_output"]).exists() and not frame.state.calls


def test_unique_resolved_request_exact_mirror_and_one_reporter_call(frame):
    result = handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert result["reporter_calls"] == 1 and len(frame.state.calls) == len(frame.state.preflights) == 1
    wait = Path(frame.config["wait_output"])
    assert (wait / "native/CHILD_LAUNCH_RECEIPT.json").read_bytes() == Path(frame.child["path"]).read_bytes()
    assert result["reporter_result"]["N_new_requests"] == 0 and result["visual_acceptance"] == "NOT_IMPLIED"
    before = {str(p): handoff.once.pin(p) for p in wait.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Fresh separate outputs"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert len(frame.state.calls) == 1
    assert before == {str(p): handoff.once.pin(p) for p in wait.rglob("*") if p.is_file()}


def test_waits_both_local_identities_before_reading_future_files(frame):
    (frame.old / "FINAL_RECEIPT.json").unlink()
    (frame.once_out / "ONCE_RECEIPT.json").unlink()
    frame.state.alive.update({123: True, 124: True})
    def sleep(seconds):
        frame.state.sleeps.append(seconds)
        if len(frame.state.sleeps) == 1:
            frame.state.alive[123] = False
        else:
            frame.state.alive[124] = False
            write(frame.old / "FINAL_RECEIPT.json", frame.final_value)
            write(frame.once_out / "ONCE_RECEIPT.json", frame.once_value)
    result = handoff.run(frame.request["path"], clock=frame.clock, sleep=sleep, reader=child_reader(frame))
    assert frame.state.sleeps == [60, 60] and result["reporter_calls"] == 1


def test_original_complete_does_not_fetch_or_launch_reporter(frame):
    frame.final_value.update(status="COMPLETE", end_reason="ALL320_ACCOUNTED", N_accounted_requests=320, N_pending_requests=0)
    changed = replace(frame.old / "FINAL_RECEIPT.json", frame.final_value)
    frame.once_value["final_receipt"] = changed
    replace(frame.once_out / "ONCE_RECEIPT.json", frame.once_value)
    def never(_):
        raise AssertionError("must not query native waiter")
    result = handoff.run(frame.request["path"], clock=frame.clock, reader=never)
    assert result["status"] == "NO_WORK_ALREADY_COMPLETE" and not frame.state.calls


def test_retained_once_failure_is_allowed_without_retrying_it(frame):
    (frame.once_out / "ONCE_RECEIPT.json").unlink()
    failure = dict(configuration=frame.config["once_configuration"], status="FAIL_PRESERVED_NO_RETRY")
    saved = write(frame.once_out / "FAILURE_RECEIPT.json", failure)
    result = handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert result["reporter_calls"] == 1 and handoff.once.pin(saved["path"]) == saved


def test_transport_and_pending_are_bounded_wait_not_native_terminal(frame):
    responses = iter([dict(state="TRANSPORT_UNAVAILABLE", error="synthetic offline"),
                      dict(state="PENDING", reason="CHILD_NOT_CLOSED"), child_reader(frame)(frame.config)])
    result = handoff.run(frame.request["path"], clock=frame.clock,
                         sleep=frame.state.sleeps.append, reader=lambda _: next(responses))
    assert frame.state.sleeps == [60, 60] and result["reporter_calls"] == 1
    assert not (Path(frame.config["wait_output"]) / "FAILURE_RECEIPT.json").exists()


def test_transport_deadline_retains_failure_no_reporter(frame):
    def sleep(_):
        frame.moments[0] = handoff.reporter.utc(frame.config["wait_deadline_utc"])
    with pytest.raises(ValueError, match="deadline ended"):
        handoff.run(frame.request["path"], clock=frame.clock, sleep=sleep,
                    reader=lambda _: dict(state="TRANSPORT_UNAVAILABLE"))
    failed = json.loads((Path(frame.config["wait_output"]) / "FAILURE_RECEIPT.json").read_text())
    assert failed["reporter_calls"] == 0 and not frame.state.calls


@pytest.mark.parametrize("name", ["FAILURE.json", "TERMINAL.json"])
def test_native_end_without_child_is_mirrored_failure(frame, name):
    terminal = write(frame.native_out / name, dict(config=frame.native_config, status="WAIT_DEADLINE_PARTIAL"))
    response = dict(state="NATIVE_TERMINAL_WITHOUT_CHILD", artifacts=[artifact(terminal["path"])])
    with pytest.raises(ValueError, match="without an actual child"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=lambda _: response)
    assert (Path(frame.config["wait_output"]) / "native" / name).read_bytes() == Path(terminal["path"]).read_bytes()
    assert not frame.state.calls


def test_wrong_native_waiter_binding_rejected(frame):
    value = dict(frame.child_value, waiter_configuration={**frame.native_config, "sha256": "0" * 64})
    replace(Path(frame.child["path"]), value)
    with pytest.raises(ValueError, match="another waiter"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert not frame.state.calls


def test_corrupt_mirror_bytes_rejected(frame):
    response = child_reader(frame)(frame.config)
    response["artifacts"][0]["base64"] = base64.b64encode(b"altered").decode()
    with pytest.raises(ValueError, match="byte identity"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=lambda _: response)


def test_reporter_failure_preserved_with_one_call(frame, monkeypatch):
    def fail(_):
        frame.state.calls.append("failed")
        raise RuntimeError("synthetic reporter partial output")
    monkeypatch.setattr(handoff.reporter, "run", fail)
    with pytest.raises(RuntimeError, match="synthetic reporter"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    failed = json.loads((Path(frame.config["wait_output"]) / "FAILURE_RECEIPT.json").read_text())
    assert failed["reporter_calls"] == 1 and len(frame.state.calls) == 1


def test_preflight_failure_never_invokes_reporter(frame, monkeypatch):
    def fail(_):
        raise ValueError("synthetic exact seed mismatch")
    monkeypatch.setattr(handoff.reporter, "preflight", fail)
    with pytest.raises(ValueError, match="seed mismatch"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert not frame.state.calls
    assert (Path(frame.config["wait_output"]) / "RESOLVED_REPORTING_REQUEST.json").is_file()


def test_source_change_during_wait_prevents_dispatch(frame):
    frame.state.alive[123] = True
    def sleep(_):
        Path(frame.request["path"]).write_text("configuration changed")
        frame.state.alive[123] = False
    with pytest.raises(ValueError, match="identity changed"):
        handoff.run(frame.request["path"], clock=frame.clock, sleep=sleep, reader=child_reader(frame))
    assert not frame.state.calls


def test_local_identity_mismatch_is_not_assumed_exit(frame, monkeypatch):
    def wrong(_):
        raise ValueError("producer identity changed")
    monkeypatch.setattr(handoff.once, "same_producer", wrong)
    with pytest.raises(ValueError, match="identity changed"):
        handoff.run(frame.request["path"], clock=frame.clock, reader=child_reader(frame))
    assert not frame.state.calls


@pytest.mark.parametrize("unsafe", [["ssh", "-o", "StrictHostKeyChecking=no", "host"],
                                    ["ssh", "-L", "123:other:456", "host"]])
def test_ssh_config_cannot_disable_strict_host_or_add_forwarding(frame, unsafe):
    update_config(frame, "ssh_argv", unsafe)
    with pytest.raises(ValueError):
        handoff.preflight(frame.request["path"])


def test_literal_reader_only_reads_pinned_config_and_child(frame):
    before = {str(p): handoff.once.pin(p) for p in frame.native_out.parent.rglob("*") if p.is_file()}
    result = literal_reader(frame)
    assert result["state"] == "CHILD"
    assert before == {str(p): handoff.once.pin(p) for p in frame.native_out.parent.rglob("*") if p.is_file()}


def test_literal_reader_no_child_and_no_end_is_pending(frame):
    Path(frame.child["path"]).unlink()
    assert literal_reader(frame)["state"] == "PENDING"


def test_literal_reader_complete_json_without_writer_newline_is_unpublished(frame):
    path = Path(frame.child["path"])
    complete = path.read_bytes()
    assert complete.endswith(b"\n")
    path.write_bytes(complete[:-1])
    assert json.loads(path.read_bytes()) == frame.child_value
    assert literal_reader(frame)["state"] == "PENDING"
    path.write_bytes(complete)
    result = literal_reader(frame)
    assert result["state"] == "CHILD"
    assert result["artifacts"][0]["remote"] == handoff.once.pin(path)


def test_literal_reader_retained_native_failure_is_not_pending(frame):
    Path(frame.child["path"]).unlink()
    write(frame.native_out / "FAILURE.json", dict(config=frame.native_config, status="WAIT_OR_DISPATCH_FAILED_NO_RETRY"))
    result = literal_reader(frame)
    assert result["state"] == "NATIVE_TERMINAL_WITHOUT_CHILD"
    assert len(result["artifacts"]) == 1


def test_literal_reader_config_hash_mismatch_is_evidence_error(frame):
    Path(frame.native_config["path"]).write_text('{"schema":"wrong"}')
    assert literal_reader(frame)["state"] == "EVIDENCE_ERROR"


def test_transport_ssh_failure_is_not_process_terminal(frame, monkeypatch):
    def fake(args, **kwargs):
        assert args[:-1] == frame.config["ssh_argv"] and " -B -c " in args[-1]
        assert kwargs["timeout"] == 55 and kwargs["check"] is False
        return SimpleNamespace(returncode=255, stdout="", stderr="synthetic unreachable")
    monkeypatch.setattr(handoff.subprocess, "run", fake)
    assert handoff.remote_read(frame.config)["state"] == "TRANSPORT_UNAVAILABLE"
