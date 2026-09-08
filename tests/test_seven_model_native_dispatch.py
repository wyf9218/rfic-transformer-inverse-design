"""Synthetic dispatch tests: never import a trainer or contact MARS."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "tools/seven_model_native_dispatch.py"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    return path


def pin(path):
    return {"path": str(path), "sha256": digest(path)}


@pytest.fixture
def dispatch_module():
    spec = importlib.util.spec_from_file_location("native_dispatch_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_event(root, status):
    events = sorted((root / "events").glob("event_*.json"))
    event = {"schema": "bb_study_event.v1", "sequence": len(events) + 1,
             "prior_event_sha256": digest(events[-1]) if events else None,
             "created_utc": "2026-09-08T00:00:00+00:00", "uid": 501,
             "pid": 123, "status": status, "detail": {"synthetic": True}}
    write_json(root / "events" / f"event_{len(events) + 1:06d}.json", event)
    write_json(root / "run_status.json", event)


@pytest.fixture
def contract(tmp_path):
    identity = {"campaign_id": "synthetic-no-remote", "milestone": 10000,
                "suite_version": "seven-model-10k-v3"}
    key = "10k-" + hashlib.sha256(json.dumps(identity, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()[:24]
    root = tmp_path / "studies" / key
    root.mkdir(parents=True)
    (root / "events").mkdir()
    (root / "study.lock").touch()
    request = write_json(tmp_path / "request.json", {
        "schema": "bb_seven_study_request.v1", "study_key": root.name,
        "control_root": str(root.parent), "campaign_id": "synthetic-no-remote",
        "suite_version": "seven-model-10k-v3", "milestone_geometries": 10000,
        "environment": {"python": sys.executable}})
    plan = write_json(root / "experiment_plan.json", json.loads(request.read_text()))
    marker = write_json(root / "ACTIVE_RUNTIME_REVISION.json", {
        "schema": "bb_active_runtime_revision.v1", "status": "ACTIVE",
        "study_root": str(root), "candidate_request": pin(request),
        "base_request": pin(plan)})
    access = write_json(tmp_path / "access.json", {"synthetic": True})
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexit 75\n")
    append_event(root, "WAITING_FOR_10K")
    config = write_json(tmp_path / "config.json", {
        "schema": "bb_native_dispatch.v1", "study_root": str(root),
        "study_key": root.name, "state_root": str(tmp_path / "dispatch_state"),
        "wrapper": pin(wrapper), "request": pin(request), "access": pin(access),
        "active_marker": pin(marker)})
    qa = write_json(tmp_path / "qa.json", {"status": "GO_FOR_NATIVE_DISPATCH",
                    "config_sha256": digest(config), "dispatcher_sha256": digest(SOURCE)})
    return SimpleNamespace(root=root, config=config, qa=qa,
                           state=tmp_path / "dispatch_state", wrapper=wrapper,
                           args=lambda: (config, digest(config), qa, digest(qa)))


def mock_run(monkeypatch, module, contract, *, code=75, result=None,
             study_status=None, started=False, crash=False):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv == ["/bin/bash", str(contract.wrapper)]
        assert not kwargs.get("shell")
        assert "timeout" not in kwargs
        assert len(kwargs["pass_fds"]) == 1
        assert "BASH_ENV" not in kwargs.get("env", {})
        assert "PYTHONPATH" not in kwargs.get("env", {})
        if crash:
            raise SystemExit("synthetic parent interruption, no real process")
        if study_status:
            append_event(contract.root, study_status)
        if started:
            write_json(contract.root / "TRAINING_BUDGET.json", {"synthetic": True})
        value = result if result is not None else (
            json.loads((contract.root / "run_status.json").read_text()) if study_status
            else {"entry_preflight": {"status": "WAITING_RESOURCE"}})
        kwargs["stdout"].write((json.dumps(value) + "\n").encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_config_check_and_status_do_not_create_dispatch_state(dispatch_module, contract, monkeypatch):
    monkeypatch.setattr(dispatch_module.subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
    dispatch_module.load_config(*contract.args())
    dispatch_module.status(*contract.args())
    assert not contract.state.exists()


def test_verified_resource_wait_can_be_checked_again(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract)
    first = dispatch_module.tick(*contract.args())
    second = dispatch_module.tick(*contract.args())
    assert first["status"] == second["status"] == "WAITING_RESOURCE"
    assert len(calls) == 2
    assert len(list(contract.state.glob("attempt_*/INTENT.json"))) == 2
    assert len(list(contract.state.glob("attempt_*/COMPLETION.json"))) == 2
    assert not (contract.root / "TRAINING_BUDGET.json").exists()


def test_verified_below_threshold_wait_can_be_checked_again(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract, code=0,
                     study_status="WAITING_FOR_10K")
    assert dispatch_module.tick(*contract.args())["status"] == "WAITING_FOR_10K"
    assert dispatch_module.tick(*contract.args())["status"] == "WAITING_FOR_10K"
    assert len(calls) == 2


@pytest.mark.parametrize("terminal,code", [("FAILED", 1), ("PARTIAL", 0)])
def test_terminal_result_is_not_automatically_retried(dispatch_module, contract, monkeypatch, terminal, code):
    calls = mock_run(monkeypatch, dispatch_module, contract, code=code,
                     result={"status": terminal}, study_status=terminal, started=True)
    first = dispatch_module.tick(*contract.args())
    assert first["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K")
    dispatch_module.tick(*contract.args())
    assert len(calls) == 1


def test_resource_wait_after_budget_started_is_not_retried(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract, code=0,
                     result={"status": "WAITING_RESOURCE"}, study_status="WAITING_RESOURCE", started=True)
    first = dispatch_module.tick(*contract.args())
    assert first["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K")
    dispatch_module.tick(*contract.args())
    assert len(calls) == 1


def test_unfinished_intent_never_resubmits_on_parent_restart(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract, crash=True)
    with pytest.raises(SystemExit):
        dispatch_module.tick(*contract.args())
    assert len(list(contract.state.glob("attempt_*/INTENT.json"))) == 1
    assert not list(contract.state.glob("attempt_*/COMPLETION.json"))
    result = dispatch_module.tick(*contract.args())
    assert result["status"] == "NEEDS_RECONCILIATION"
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["FAILED", "PARTIAL", "TRAINING"])
def test_existing_nonwaiting_study_is_not_submitted(dispatch_module, contract, monkeypatch, status):
    append_event(contract.root, status)
    calls = mock_run(monkeypatch, dispatch_module, contract)
    result = dispatch_module.tick(*contract.args())
    assert result["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K")
    assert calls == []


@pytest.mark.parametrize("name", ["TRAINING_BUDGET.json", "STUDY_MODELS.json", "stages"])
def test_existing_started_artifact_blocks_first_submission(dispatch_module, contract, monkeypatch, name):
    path = contract.root / name
    path.mkdir() if name == "stages" else write_json(path, {"synthetic": True})
    calls = mock_run(monkeypatch, dispatch_module, contract)
    result = dispatch_module.tick(*contract.args())
    assert result["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K")
    assert calls == []


@pytest.mark.parametrize("result,code", [({}, 0), ({"status": "WAITING_FOR_10K"}, 75),
                                        ({"entry_preflight": {"status": "PASS"}}, 75),
                                        ({"status": "TRAINED_AND_EVALUATED"}, 0)])
def test_unknown_or_contradictory_result_does_not_reauthorize(dispatch_module, contract, monkeypatch, result, code):
    calls = mock_run(monkeypatch, dispatch_module, contract, code=code, result=result)
    outcome = dispatch_module.tick(*contract.args())
    assert outcome["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K", "COMPLETE", "TRAINED_AND_EVALUATED")
    dispatch_module.tick(*contract.args())
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["wrapper", "request", "access", "active_marker"])
def test_pinned_input_drift_rejected_before_state_write(dispatch_module, contract, monkeypatch, field):
    config = json.loads(contract.config.read_text())
    Path(config[field]["path"]).write_text("tampered\n")
    calls = mock_run(monkeypatch, dispatch_module, contract)
    with pytest.raises((ValueError, RuntimeError)):
        dispatch_module.tick(*contract.args())
    assert not contract.state.exists()
    assert calls == []


@pytest.mark.parametrize("field", ["status", "config_sha256", "dispatcher_sha256"])
def test_exact_qa_required(dispatch_module, contract, monkeypatch, field):
    qa = json.loads(contract.qa.read_text())
    qa[field] = "NO_GO" if field == "status" else "0" * 64
    write_json(contract.qa, qa)
    calls = mock_run(monkeypatch, dispatch_module, contract)
    with pytest.raises((ValueError, RuntimeError)):
        dispatch_module.tick(*contract.args())
    assert not contract.state.exists()
    assert calls == []


def test_changed_journal_cache_is_not_trusted(dispatch_module, contract, monkeypatch):
    write_json(contract.root / "run_status.json", {"status": "WAITING_FOR_10K"})
    calls = mock_run(monkeypatch, dispatch_module, contract)
    with pytest.raises((ValueError, RuntimeError)):
        dispatch_module.tick(*contract.args())
    assert calls == []


def test_changed_prior_event_chain_is_rejected(dispatch_module, contract, monkeypatch):
    append_event(contract.root, "WAITING_FOR_10K")
    path = contract.root / "events/event_000001.json"
    value = json.loads(path.read_text())
    value["status"] = "FAILED"
    write_json(path, value)
    calls = mock_run(monkeypatch, dispatch_module, contract)
    with pytest.raises((ValueError, RuntimeError)):
        dispatch_module.tick(*contract.args())
    assert calls == []


def repin_config(contract, value):
    write_json(contract.config, value)
    qa = json.loads(contract.qa.read_text())
    qa["config_sha256"] = digest(contract.config)
    write_json(contract.qa, qa)


@pytest.mark.parametrize("where", ["inside", "contains", "same"])
def test_state_cannot_be_inside_or_contain_study(dispatch_module, contract, monkeypatch, where):
    config = json.loads(contract.config.read_text())
    config["state_root"] = str({"inside": contract.root / "nested",
                                "contains": contract.root.parent, "same": contract.root}[where])
    repin_config(contract, config)
    calls = mock_run(monkeypatch, dispatch_module, contract)
    with pytest.raises(ValueError):
        dispatch_module.tick(*contract.args())
    assert calls == []


def test_lock_contention_does_not_spawn(dispatch_module, contract, monkeypatch):
    import fcntl
    contract.state.mkdir()
    with (contract.state / "dispatch.lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls = mock_run(monkeypatch, dispatch_module, contract)
        assert dispatch_module.tick(*contract.args())["status"] == "DISPATCH_LOCK_BUSY"
        assert calls == []


def test_render_launchd_does_not_install_or_spawn(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract)
    raw = dispatch_module.render_launchd(*contract.args(), python=sys.executable,
                                          label="test.synthetic.seven", interval_seconds=300)
    config = plistlib.loads(raw)
    assert config["StartInterval"] == 300
    assert config["RunAtLoad"] is False
    assert config["KeepAlive"] is False
    assert config["AbandonProcessGroup"] is True
    assert config["ProgramArguments"][0] == sys.executable
    assert config["ProgramArguments"][1:4] == ["-I", "-B", "-S"]
    assert "tick" in config["ProgramArguments"]
    assert not contract.state.exists()
    assert calls == []


@pytest.mark.parametrize("failed", ["FAILED", "PARTIAL"])
def test_historical_failure_is_not_erased_by_new_wait_event(dispatch_module, contract, monkeypatch, failed):
    append_event(contract.root, failed)
    append_event(contract.root, "WAITING_FOR_10K")
    calls = mock_run(monkeypatch, dispatch_module, contract)
    assert dispatch_module.tick(*contract.args())["status"] not in ("WAITING_RESOURCE", "WAITING_FOR_10K")
    assert calls == []


def test_last_completion_status_tamper_cannot_enable_retry(dispatch_module, contract, monkeypatch):
    calls = mock_run(monkeypatch, dispatch_module, contract, code=1, result={"status": "FAILED"})
    dispatch_module.tick(*contract.args())
    completion = next(contract.state.glob("attempt_*/COMPLETION.json"))
    value = json.loads(completion.read_text())
    value["status"] = "WAITING_RESOURCE"
    value["automatic_retry_allowed"] = True
    write_json(completion, value)
    with pytest.raises((ValueError, RuntimeError)):
        dispatch_module.tick(*contract.args())
    assert len(calls) == 1


def test_real_isolated_cli_status_and_resource_wait(contract):
    # Synthetic shell only: no research import, data, optimizer, SSH or simulator.
    contract.wrapper.write_text('#!/bin/bash\nprintf \'{"entry_preflight":{"status":"WAITING_RESOURCE"}}\\n\'\nexit 75\n')
    value = json.loads(contract.config.read_text())
    value["wrapper"] = pin(contract.wrapper)
    repin_config(contract, value)
    args = ["--config", str(contract.config), "--config-sha256", digest(contract.config),
            "--qa-receipt", str(contract.qa), "--qa-sha256", digest(contract.qa)]
    for action, expected in (("check-config", "CONFIG_VALID"), ("status", "READY_FOR_TICK"),
                             ("tick", "WAITING_RESOURCE"), ("tick", "WAITING_RESOURCE")):
        result = subprocess.run([sys.executable, "-I", "-B", "-S", str(SOURCE), action, *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, check=False, timeout=10)
        assert result.returncode == 0, result.stderr.decode() + result.stdout.decode()
        assert json.loads(result.stdout)["status"] == expected
    assert len(list(contract.state.glob("attempt_*/COMPLETION.json"))) == 2


def test_real_child_keeps_dispatch_lock_after_parent_unwinds(dispatch_module, contract, monkeypatch):
    children = []
    def interrupted_parent(argv, **kwargs):
        fd = kwargs["pass_fds"][0]
        child = subprocess.Popen([sys.executable, "-I", "-B", "-S", "-c",
            "import os,sys; os.fstat(int(sys.argv[1])); print('READY',flush=True); sys.stdin.buffer.read(1)",
            str(fd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(fd,))
        children.append(child)
        assert child.stdout.readline() == b"READY\n"
        raise SystemExit("synthetic parent unwind while ordinary child retains fd")
    monkeypatch.setattr(dispatch_module.subprocess, "run", interrupted_parent)
    try:
        with pytest.raises(SystemExit):
            dispatch_module.tick(*contract.args())
        assert dispatch_module.tick(*contract.args())["status"] == "DISPATCH_LOCK_BUSY"
        assert len(children) == 1
    finally:
        for child in children:
            # Protocol EOF lets only this test-owned child exit normally; no kill/signal.
            _, stderr = child.communicate(input=b"x", timeout=10)
            assert child.returncode == 0, stderr
    assert dispatch_module.tick(*contract.args())["status"] == "NEEDS_RECONCILIATION"
    assert len(children) == 1
