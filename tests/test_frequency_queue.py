"""Finite-pool tests using temporary metadata/stub processes, never training."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import threading

import pytest

from research.broadband56_nn import frequency_queue as queue
from research.broadband56_nn.io import read_json, save_json


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / "queue"
    config = {"schema": "frequency_finite_queue.v1", "workers": 2,
        "out": str(root), "deadline_utc": "2099-01-01T00:00:00Z",
        "global_device_lock": str(tmp_path / "global.lock"),
        "slot_locks": {str(i): str(tmp_path / f"slot{i}.lock") for i in range(2)}, "jobs": []}
    for index, frequency in enumerate(queue.ORDER):
        path = tmp_path / "requests" / f"f{frequency:02d}.json"
        request = {"schema": "frequency_study_request.v1", "out": str(tmp_path / f"study{frequency:02d}"),
            "dataset_sha256": "a" * 64, "data_root": str(tmp_path / "data"),
            "device_lock": config["slot_locks"][str(index % 2)],
            "train": {"frequency_ghz": frequency, "device": "cpu", "threads": 2,
                "label_mode": "STRICT_LUMPED", "deadline_utc": config["deadline_utc"]}}
        save_json(path, request)
        config["jobs"].append({"frequency_ghz": frequency, "request": queue.pin(path), "on_ready_commands": []})
    path = tmp_path / "queue.json"
    save_json(path, config)
    return path, config


def rewrite_request(config, index, change):
    path = Path(config["jobs"][index]["request"]["path"])
    request = read_json(path)
    change(request)
    path.write_text(json.dumps(request))
    config["jobs"][index]["request"] = queue.pin(path)


def commit_config(path, config):
    path.write_text(json.dumps(config))


def job_receipt(config, index):
    job = config["jobs"][index]
    root = Path(config["out"]) / "jobs" / f"f{job['frequency_ghz']:02d}"
    root.mkdir(parents=True, exist_ok=True)
    pair = root / "synthetic_pair.json"
    save_json(pair, {"synthetic": True, "frequency_ghz": job["frequency_ghz"]})
    value = {"schema": "frequency_queue_job.v1", "status": "PAIR_READY",
        "request": job["request"], "frequency_ghz": job["frequency_ghz"],
        "pair": queue.pin(pair), "callbacks": []}
    save_json(root / "JOB_RECEIPT.json", value)
    return root / "JOB_RECEIPT.json"


def test_exact_sixteen_order_and_two_fixed_slots(configured):
    _, config = configured
    assert queue.validate(config) == config
    assert queue.ORDER[:2] == [5, 10]
    assert sorted(queue.ORDER) == list(range(5, 21))


@pytest.mark.parametrize("fault", ["duplicate_frequency", "duplicate_output", "dataset", "slot", "threads", "device"])
def test_contract_mutations_rejected(configured, fault):
    _, config = configured
    if fault == "duplicate_frequency":
        config["jobs"][2]["frequency_ghz"] = config["jobs"][0]["frequency_ghz"]
    elif fault == "duplicate_output":
        first = read_json(config["jobs"][0]["request"]["path"])["out"]
        rewrite_request(config, 2, lambda r: r.update(out=first))
    elif fault == "dataset":
        rewrite_request(config, 2, lambda r: r.update(dataset_sha256="b" * 64))
    elif fault == "slot":
        rewrite_request(config, 2, lambda r: r.update(device_lock=config["slot_locks"]["1"]))
    else:
        rewrite_request(config, 2, lambda r: r["train"].update({fault: 4 if fault == "threads" else "mps"}))
    with pytest.raises(ValueError):
        queue.validate(config)


def test_historical_reuse_keeps_its_original_deadline(configured):
    _, config = configured
    index = queue.ORDER.index(15)
    config["jobs"][index]["reuse_existing"] = True
    rewrite_request(config, index, lambda r: r["train"].update(deadline_utc="2026-09-08T07:30:00Z"))
    queue.validate(config)


def test_output_path_alias_is_duplicate(configured):
    _, config = configured
    first = read_json(config["jobs"][0]["request"]["path"])["out"]
    alias = str(Path(first).parent / "unused" / ".." / Path(first).name)
    rewrite_request(config, 2, lambda r: r.update(out=alias))
    with pytest.raises(ValueError, match="duplicate"):
        queue.validate(config)


def test_no_resources_means_no_child(configured, monkeypatch):
    path, _ = configured
    monkeypatch.setattr(queue, "resource_snapshot", lambda *a, **k: {"status": "WAITING_RESOURCE", "cpu_logical": 10})
    monkeypatch.setattr(queue.subprocess, "Popen", lambda *a, **k: pytest.fail("must not submit"))
    assert queue.run(path)["status"] == "WAITING_RESOURCE"


def test_synthetic_pool_never_exceeds_two_children_and_reuses_completed_jobs(configured, monkeypatch):
    path, config = configured
    monkeypatch.setattr(queue, "resource_snapshot", lambda *a, **k: {"status": "PASS", "cpu_logical": 10})
    guard = threading.Lock()
    both_lanes = threading.Event()
    observed = {"active": 0, "peak": 0, "starts": [], "fds": []}
    class StubProcess:
        def __init__(self, argv, **kwargs):
            self.index = int(argv[argv.index("--index") + 1])
            assert argv[:4] == [sys.executable, "-B", "-m", "research.broadband56_nn.frequency_queue"]
            fds = kwargs["pass_fds"]
            assert len(fds) == 2 and all(os.fstat(fd).st_ino for fd in fds)
            assert kwargs["env"]["OMP_NUM_THREADS"] == "2"
            with guard:
                observed["active"] += 1
                observed["peak"] = max(observed["peak"], observed["active"])
                observed["starts"].append(self.index)
                observed["fds"].append(tuple(fds))
                self.pid = 100000 + self.index
                if observed["active"] == 2:
                    both_lanes.set()
        def wait(self):
            assert both_lanes.wait(timeout=2)
            job_receipt(config, self.index)
            with guard:
                observed["active"] -= 1
            return 0
    monkeypatch.setattr(queue.subprocess, "Popen", StubProcess)
    result = queue.run(path)
    assert result["all_pairs_ready"] is True
    assert observed["peak"] == 2 and observed["active"] == 0
    assert sorted(observed["starts"]) == list(range(16))
    assert len(set(observed["fds"])) == 1
    monkeypatch.setattr(queue.subprocess, "Popen", lambda *a, **k: pytest.fail("completed jobs must not resubmit"))
    repeated = queue.run(path)
    assert repeated["all_pairs_ready"] is True
    assert all(j["status"] == "ALREADY_COMPLETE" for j in repeated["jobs"])


def test_completed_job_with_foreign_request_is_not_accepted(configured, monkeypatch):
    path, config = configured
    proof = job_receipt(config, 0)
    value = read_json(proof)
    value["request"] = config["jobs"][1]["request"]
    proof.write_text(json.dumps(value))
    monkeypatch.setattr(queue, "resource_snapshot", lambda *a, **k: {"status": "PASS", "cpu_logical": 10})
    started = []
    class UnrelatedStub:
        def __init__(self, argv, **kwargs):
            self.pid = 999999
            started.append(int(argv[argv.index("--index") + 1]))
        def wait(self):
            return 1  # Only a stub; an unrelated lane may continue independently.
    monkeypatch.setattr(queue.subprocess, "Popen", UnrelatedStub)
    with pytest.raises(ValueError):
        queue.run(path)
    assert 0 not in started


def test_worker_requires_parent_reservations(configured):
    path, _ = configured
    with pytest.raises(ValueError):
        queue.worker(path, 0, [])


def test_worker_propagates_parent_fds_and_restores_wrapped_functions(configured, monkeypatch):
    from research.broadband56_nn import frequency_study as study
    path, config = configured
    observed = []
    def run_child(argv, cwd, log, lock_fds):
        observed.append(("child", set(lock_fds)))
    def finish(request, root, lock_fds):
        observed.append(("finish", set(lock_fds)))
    monkeypatch.setattr(study, "run_child", run_child)
    monkeypatch.setattr(study, "_finish", finish)
    monkeypatch.setattr(queue, "_completed_pair", lambda *a: None)
    def no_training(*a, **k):
        study.run_child([], ".", "unused", (100, 101))
        study._finish({}, ".", (100, 101))
        return {"status": "SYNTHETIC_NO_TRAINING"}
    monkeypatch.setattr(study, "run", no_training)
    with ExitStack() as stack:
        fds = [stack.enter_context(queue.lease(Path(config["out"]) / "queue.lock")),
               stack.enter_context(queue.lease(config["global_device_lock"]))]
        result = queue.worker(path, 0, fds)
        assert result["status"] == "SYNTHETIC_NO_TRAINING"
        assert observed == [("child", {100, 101, *fds}), ("finish", {100, 101, *fds})]
    assert study.run_child is run_child and study._finish is finish


def test_worker_completed_pair_never_retrains(configured, monkeypatch):
    from research.broadband56_nn import frequency_study as study
    path, config = configured
    pair = path.parent / "pair.json"
    save_json(pair, {"synthetic": True})
    monkeypatch.setattr(queue, "_completed_pair", lambda *a: queue.pin(pair))
    monkeypatch.setattr(study, "run", lambda *a, **k: pytest.fail("must reuse pair"))
    with ExitStack() as stack:
        fds = [stack.enter_context(queue.lease(Path(config["out"]) / "queue.lock")),
               stack.enter_context(queue.lease(config["global_device_lock"]))]
        value = queue.worker(path, 0, fds)
        assert value["training_result"] == "REUSED_COMMITTED_PAIR"
        assert queue.worker(path, 0, fds) == value


@pytest.mark.parametrize("fault", ["missing", "changed_receipt", "changed_log", "failed"])
def test_completed_callback_evidence_is_revalidated(configured, fault):
    path, config = configured
    job = config["jobs"][0]
    command = {"argv": [sys.executable, "-B", "synthetic_only.py"], "pins": [], "cwd": str(path.parent)}
    job["on_ready_commands"] = [command]
    proof = job_receipt(config, 0)
    log = proof.parent / "callback.log"
    log.write_text("synthetic result")
    callback = proof.parent / "callback.json"
    save_json(callback, {"command": command, "returncode": 1 if fault == "failed" else 0, "log": queue.pin(log)})
    value = read_json(proof)
    value["callbacks"] = [] if fault == "missing" else [queue.pin(callback)]
    if fault == "changed_receipt":
        callback.write_text("changed")
    if fault == "changed_log":
        log.write_text("changed")
    with pytest.raises(ValueError):
        queue._check_job(value, job)
