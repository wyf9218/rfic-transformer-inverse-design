from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.broadband56_nn import frequency_physical_export_once as once


def save(path, value):
    path.write_text(json.dumps(value))
    return once.pin(path)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    producer_output = tmp_path / "producer"
    producer_output.mkdir()
    producer_config = save(tmp_path / "producer.json",
                           {"schema": "frequency_incremental_statistics_consumer.v1",
                            "output": str(producer_output)})
    source = tmp_path / "source.py"
    source.write_text("# no model or simulation\n")
    identity = {"pid": 12345, "uid": os.getuid(), "lstart": "Tue Sep 8 04:56:33 2026",
                "args_sha256": hashlib.sha256(b"producer").hexdigest()}
    monkeypatch.setattr(once, "process_identity", lambda pid: identity)
    import sys
    config = {"schema": once.SCHEMA, "producer": identity, "producer_configuration": producer_config,
              "final_receipt": str(producer_output / "FINAL_RECEIPT.json"),
              "python": sys.executable, "cwd": str(tmp_path),
              "wait_output": str(tmp_path / "wait"), "batch_output": str(tmp_path / "batch"),
              "wait_deadline_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
              "poll_seconds": 30, "source_pins": [once.pin(source)], "aggregates": ["selection"]}
    path = tmp_path / "request.json"
    save(path, config)
    return path, config, identity


def terminal(config, status="PARTIAL"):
    return save(Path(config["final_receipt"]),
                {"configuration": config["producer_configuration"], "status": status})


def test_readonly_preflight_no_outputs(setup):
    path, config, _ = setup
    result = once.preflight(path)
    assert result["status"] == "READY_TO_WAIT_READONLY"
    assert not Path(config["wait_output"]).exists()
    assert not Path(config["batch_output"]).exists()
    assert once.BATCH_MODULE in result["command"]


def test_wait_for_exit_then_single_finite_dispatch(setup, monkeypatch):
    path, config, identity = setup
    terminal(config)
    observations = iter([identity, None])
    monkeypatch.setattr(once, "process_identity", lambda pid: next(observations))
    calls = []
    result = once.run(path, sleeper=lambda n: None,
                      runner=lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=0))
    assert len(calls) == 1
    assert result["producer_terminal_status"] == "PARTIAL"
    assert result["visual_acceptance"] == "NOT_IMPLIED"
    assert Path(config["wait_output"], "DISPATCH_INTENT.json").exists()
    with pytest.raises(ValueError, match="already installed"):
        once.run(path)
    assert len(calls) == 1


def test_exited_without_terminal_is_sticky_failure(setup, monkeypatch):
    path, config, _ = setup
    monkeypatch.setattr(once, "process_identity", lambda pid: None)
    with pytest.raises(FileNotFoundError):
        once.run(path)
    assert Path(config["wait_output"], "FAILURE_RECEIPT.json").exists()
    assert not Path(config["wait_output"], "DISPATCH_INTENT.json").exists()


def test_parent_identity_change_not_mistaken_for_exit(setup, monkeypatch):
    path, config, identity = setup
    terminal(config)
    monkeypatch.setattr(once, "process_identity", lambda pid: {**identity, "args_sha256": "changed"})
    with pytest.raises(ValueError, match="identity changed"):
        once.run(path)
    assert not Path(config["wait_output"], "DISPATCH_INTENT.json").exists()


def test_reused_pid_never_signalled(setup, monkeypatch):
    path, config, identity = setup
    terminal(config, "COMPLETE")
    monkeypatch.setattr(once, "process_identity", lambda pid: {**identity, "lstart": "different start"})
    result = once.run(path, runner=lambda *a, **k: SimpleNamespace(returncode=0))
    assert result["producer_signals"] == 0


def test_budget_end_never_dispatches(setup):
    path, config, _ = setup
    with pytest.raises(ValueError, match="budget ended"):
        once.run(path, now=lambda: datetime.now(timezone.utc) + timedelta(days=1))
    assert not Path(config["wait_output"], "DISPATCH_INTENT.json").exists()


def test_terminal_wrong_producer_refused(setup, monkeypatch):
    path, config, _ = setup
    save(Path(config["final_receipt"]), {"configuration": {}, "status": "PARTIAL"})
    monkeypatch.setattr(once, "process_identity", lambda pid: None)
    with pytest.raises(ValueError, match="another producer"):
        once.run(path)


def test_changed_code_hash_refused_before_output(setup):
    path, config, _ = setup
    Path(config["source_pins"][0]["path"]).write_text("# changed\n")
    with pytest.raises(ValueError, match="identity changed"):
        once.preflight(path)
    assert not Path(config["wait_output"]).exists()


def test_export_failure_is_not_retried(setup, monkeypatch):
    path, config, _ = setup
    terminal(config)
    monkeypatch.setattr(once, "process_identity", lambda pid: None)
    with pytest.raises(ValueError, match="no automatic retry"):
        once.run(path, runner=lambda *a, **k: SimpleNamespace(returncode=3))
    assert Path(config["wait_output"], "FAILURE_RECEIPT.json").exists()
    assert not Path(config["wait_output"], "ONCE_RECEIPT.json").exists()


def test_unrelated_final_path_refused(setup):
    path, config, _ = setup
    config["final_receipt"] += ".other"
    save(path, config)
    with pytest.raises(ValueError, match="producer-owned"):
        once.preflight(path)


def test_ps_error_is_not_exit(monkeypatch):
    monkeypatch.setattr(once.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="error"))
    with pytest.raises(ValueError, match="not evidence of exit"):
        once.process_identity(12345)

