"""Synthetic orchestration only: no real model, optimizer, data or subprocess."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from research.broadband56_nn import bb00_delivery as bb00
from research.broadband56_nn import delivery
from research.broadband56_nn import seven_suite as suite
from research.broadband56_nn.io import read_json, save_json, sha256

FUTURE = "2100-01-01T00:30:00+00:00"
PAST = "2000-01-01T00:30:00+00:00"


def forbidden(*args, **kwargs):
    pytest.fail("real model/data/training/subprocess entry is forbidden in budget tests")


@pytest.fixture(autouse=True)
def forbid_real_execution(monkeypatch):
    from research.broadband56_nn import training
    monkeypatch.setattr(delivery.subprocess, "Popen", forbidden)
    monkeypatch.setattr(delivery.subprocess, "run", forbidden)
    monkeypatch.setattr(delivery, "Bundle", forbidden)
    monkeypatch.setattr(delivery, "load_checkpoint", forbidden)
    monkeypatch.setattr(delivery, "_output_arrays", forbidden)
    monkeypatch.setattr(bb00, "Bundle", forbidden)
    monkeypatch.setattr(bb00, "load_checkpoint", forbidden)
    monkeypatch.setattr(bb00, "load_bb00", forbidden)
    monkeypatch.setattr(bb00, "train_bb00", forbidden)
    monkeypatch.setattr(training, "train", forbidden)


@pytest.mark.parametrize("value", ["", "nonsense", "2100-01-01", "2100-01-01T00:00:00", 12, False, []])
def test_invalid_or_naive_deadline_rejected(value):
    with pytest.raises(delivery.ResumeDeadlineError):
        delivery.validate_resume_deadline(value)


def test_none_is_only_standalone_compatibility_and_expired_can_be_read():
    assert delivery.validate_resume_deadline(None) is None
    assert delivery.validate_resume_deadline(FUTURE).isoformat() == FUTURE
    with pytest.raises(TimeoutError):
        delivery.validate_resume_deadline(PAST)
    assert delivery.validate_resume_deadline(PAST, allow_expired=True).year == 2000


def test_six_command_propagates_exact_timestamp_and_lease(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(delivery, "_command", lambda *a, **kw: calls.append((a, kw)) or {"returncode": 0})
    argv = ["python", "-m", "research.broadband56_nn", "resume", "--steps", "1"]
    delivery._resume_command(argv, tmp_path / "mock.log", deadline_utc=FUTURE, lock_fds=(9, 10))
    command, kwargs = calls[0]
    assert command[0] == argv + ["--deadline-utc", FUTURE]
    assert kwargs == {"lock_fds": (9, 10)}
    assert "--deadline-utc" not in argv


@pytest.mark.parametrize("deadline", [PAST, "bad", "2100-01-01T00:00:00"])
def test_six_command_rejects_before_subprocess(tmp_path, deadline):
    with pytest.raises((TimeoutError, delivery.ResumeDeadlineError)):
        delivery._resume_command(["must-not-execute"], tmp_path / "never.log", deadline_utc=deadline)
    assert not (tmp_path / "never.log").exists()


@pytest.mark.parametrize("deadline", [PAST, "bad"])
def test_six_verifier_keeps_failed_receipt_without_data_or_optimizer(tmp_path, monkeypatch, deadline):
    monkeypatch.setattr(delivery, "_campaign_terminal", lambda root: {})
    out = tmp_path / "proof"
    result = delivery.verify_load_resume(tmp_path / "no-data", tmp_path / "runs", out,
                                        deadline_utc=deadline, training_budget_sha256="clock")
    assert result["status"] == "FAIL"
    assert result["results"] == {}
    assert result["effective_deadline_utc"] == deadline
    assert result["remaining_probes"] == "NOT_RUN"
    assert read_json(out / "LOAD_RESUME_RECEIPT.json") == result
    with pytest.raises(FileExistsError):
        delivery.verify_load_resume(tmp_path / "no-data", tmp_path / "runs", out, deadline_utc=deadline)


@pytest.mark.parametrize("deadline", [PAST, "bad"])
def test_bb00_verifier_keeps_failed_receipt_without_data_or_optimizer(tmp_path, deadline):
    out = tmp_path / "proof"
    with pytest.raises((TimeoutError, delivery.ResumeDeadlineError)):
        bb00.verify_bb00_load_resume("unused", "unused", "unused", out, "unused", "unused", "sha",
                                    deadline_utc=deadline, training_budget_sha256="clock")
    proof = read_json(out / "BB00_LOAD_RESUME_RECEIPT.json")
    assert proof["status"] == "FAIL" and proof["results"] == {}
    assert proof["effective_deadline_utc"] == deadline
    assert proof["remaining_probes"] == "NOT_RUN"


def worker_request(tmp_path, deadline):
    path = tmp_path / "request.json"
    save_json(path, {"action": "resume_one", "effective_deadline_utc": deadline,
                    "training_budget_sha256": "clock", "out_receipt": str(tmp_path / "receipt.json"),
                    "last_checkpoint": "mock-last", "best_checkpoint": "mock-best", "best_sha256": "best-sha",
                    "device": "cpu", "data_root": "unused", "resume_out": "unused",
                    "contract_path": "unused", "legacy_replay_receipt": "unused", "expected_legacy_sha": "sha"})
    return path


@pytest.mark.parametrize("deadline", [PAST, "bad"])
def test_bb00_child_checks_before_checkpoint_or_optimizer(tmp_path, deadline):
    request = worker_request(tmp_path, deadline)
    with pytest.raises((TimeoutError, delivery.ResumeDeadlineError)):
        bb00._worker(request)
    proof = read_json(tmp_path / "receipt.json")
    assert proof["status"] == "FAIL" and proof["optimizer_called"] is False
    assert proof["effective_deadline_utc"] == deadline


def test_bb00_worker_passes_exact_deadline_into_mock_trainer(tmp_path, monkeypatch):
    class ReachedMockTrainer(Exception):
        pass
    calls = []
    monkeypatch.setattr(bb00.torch, "set_num_threads", lambda _: None)
    config = asdict(bb00.BB00Config(role="forward", device="cpu", deadline_utc=PAST))
    monkeypatch.setattr(bb00, "load_checkpoint", lambda _: {"train_config": config})
    def mock_trainer(*args, **kwargs):
        calls.append((args, kwargs))
        raise ReachedMockTrainer
    monkeypatch.setattr(bb00, "train_bb00", mock_trainer)
    with pytest.raises(ReachedMockTrainer):
        bb00._worker(worker_request(tmp_path, FUTURE))
    actual = calls[0][0][2]
    assert actual.deadline_utc == FUTURE and actual.steps == 1
    assert calls[0][1]["resume_probe"] is True
    assert config["deadline_utc"] == PAST


@pytest.mark.parametrize("deadline", [PAST, "bad"])
def test_bb00_launcher_rejects_before_child(tmp_path, deadline):
    request = worker_request(tmp_path, deadline)
    with pytest.raises((TimeoutError, delivery.ResumeDeadlineError)):
        bb00._launch(request, tmp_path / "receipt.json", (9,))
    assert not (tmp_path / "receipt.json.process.json").exists()


def budget_fixture(tmp_path, *, past=False, clock_overrides=None):
    root = tmp_path / "study"
    root.mkdir()
    year = "2000" if past else "2100"
    clock = {"started_utc": year + "-01-01T00:00:00+00:00",
             "deadline_utc": year + "-01-01T00:30:00+00:00", "seconds": 1800}
    clock.update(clock_overrides or {})
    save_json(root / "TRAINING_BUDGET.json", clock)
    request = {"wall_budget_seconds": 1800, "device": "cpu", "runtime_contract": {"path": "unused"},
               "legacy_replay": {"path": "unused", "sha256": "legacy"}}
    return root, request


def proof_value(labels, deadline, budget_sha):
    binding = {"effective_deadline_utc": deadline, "training_budget_sha256": budget_sha}
    return {"status": "PASS", **binding, "results": {
        label: {"status": "PASS", **binding, "checks": {"effective_deadline_bound": True}} for label in labels}}


def write_proofs(root, deadline, budget_pin):
    save_json(root / "load_resume_six/LOAD_RESUME_RECEIPT.json",
              proof_value(delivery.RUN_LABELS, deadline, budget_pin["sha256"]))
    save_json(root / "load_resume_bb00/BB00_LOAD_RESUME_RECEIPT.json",
              proof_value(("forward", "inverse"), deadline, budget_pin["sha256"]))


def test_packaging_never_creates_missing_budget(tmp_path):
    with pytest.raises(FileNotFoundError):
        suite._packaging_budget(tmp_path, {"wall_budget_seconds": 1800})
    assert not (tmp_path / "TRAINING_BUDGET.json").exists()


@pytest.mark.parametrize("field,value", [("seconds", 1801), ("seconds", True), ("started_utc", None),
    ("deadline_utc", None), ("deadline_utc", "2100-01-01T00:31:00+00:00"),
    ("deadline_utc", "2100-01-01T00:30:00"), ("started_utc", "bad")])
def test_packaging_rejects_corrupt_or_renewed_clock(tmp_path, field, value):
    root, request = budget_fixture(tmp_path, clock_overrides={field: value})
    path = root / "TRAINING_BUDGET.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        suite._packaging_budget(root, request)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["unbound", "other_clock", "other_deadline", "missing_role", "missing_check", "fail"])
def test_packaging_rejects_old_or_foreign_proof(tmp_path, mutation):
    root, request = budget_fixture(tmp_path)
    deadline, clock = suite._packaging_budget(root, request)
    proof = proof_value(("forward", "inverse"), deadline, clock["sha256"])
    if mutation == "unbound":
        proof.pop("effective_deadline_utc")
    elif mutation == "other_clock":
        proof["training_budget_sha256"] = "other"
    elif mutation == "other_deadline":
        proof["effective_deadline_utc"] = PAST
    elif mutation == "missing_role":
        proof["results"].pop("inverse")
    elif mutation == "missing_check":
        proof["results"]["inverse"]["checks"] = {}
    else:
        proof["results"]["inverse"]["status"] = "FAIL"
    path = root / "proof.json"
    save_json(path, proof)
    with pytest.raises(ValueError):
        suite._require_packaging_proof(path, deadline, clock, ("forward", "inverse"))


def test_expired_budget_without_proofs_cannot_launch(tmp_path, monkeypatch):
    root, request = budget_fixture(tmp_path, past=True)
    monkeypatch.setattr(delivery, "verify_load_resume", forbidden)
    monkeypatch.setattr(bb00, "verify_bb00_load_resume", forbidden)
    before = (root / "TRAINING_BUDGET.json").read_bytes()
    with pytest.raises(TimeoutError):
        suite.package_completed(root, request)
    assert (root / "TRAINING_BUDGET.json").read_bytes() == before
    assert not (root / "load_resume_six").exists()


def test_completed_bound_proofs_reusable_after_expiry_without_new_probes(tmp_path, monkeypatch):
    class ReachedHashPackaging(Exception):
        pass
    root, request = budget_fixture(tmp_path, past=True)
    deadline, clock = suite._packaging_budget(root, request)
    write_proofs(root, deadline, clock)
    save_json(root / "STUDY_MODELS.json", {"records": {}})
    monkeypatch.setattr(suite, "_finished_evaluation", lambda _: True)
    monkeypatch.setattr(delivery, "verify_load_resume", forbidden)
    monkeypatch.setattr(bb00, "verify_bb00_load_resume", forbidden)
    def hash_only(*a, **kw):
        raise ReachedHashPackaging
    monkeypatch.setattr(delivery, "package", hash_only)
    with pytest.raises(ReachedHashPackaging):
        suite.package_completed(root, request)


@pytest.mark.parametrize("six_fails", [False, True])
def test_seven_suite_propagates_one_clock_and_stops_on_failed_proof(tmp_path, monkeypatch, six_fails):
    class ReachedHashPackaging(Exception):
        pass
    root, request = budget_fixture(tmp_path)
    deadline, clock = suite._packaging_budget(root, request)
    save_json(root / "STUDY_MODELS.json", {"records": {
        label: {"receipt": {"path": "unused"}} for label in ("BB00_FORWARD", "BB00")}})
    monkeypatch.setattr(suite, "_finished_evaluation", lambda _: True)
    calls = []
    def six(*args, **kwargs):
        calls.append(("six", kwargs))
        value = proof_value(delivery.RUN_LABELS, kwargs["deadline_utc"], kwargs["training_budget_sha256"])
        if six_fails:
            value["status"] = "FAIL"
        save_json(Path(args[2]) / "LOAD_RESUME_RECEIPT.json", value)
    def baseline(*args, **kwargs):
        calls.append(("bb00", kwargs))
        save_json(Path(args[3]) / "BB00_LOAD_RESUME_RECEIPT.json",
                  proof_value(("forward", "inverse"), kwargs["deadline_utc"], kwargs["training_budget_sha256"]))
    def hash_only(*a, **kw):
        raise ReachedHashPackaging
    monkeypatch.setattr(delivery, "verify_load_resume", six)
    monkeypatch.setattr(bb00, "verify_bb00_load_resume", baseline)
    monkeypatch.setattr(delivery, "package", hash_only)
    with pytest.raises(ValueError if six_fails else ReachedHashPackaging):
        suite.package_completed(root, request, (9, 10))
    assert [name for name, _ in calls] == (["six"] if six_fails else ["six", "bb00"])
    assert all(kw == {"lock_fds": (9, 10), "deadline_utc": deadline,
                     "training_budget_sha256": clock["sha256"]} for _, kw in calls)


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
@pytest.mark.parametrize("stage", ["fresh_load", "resume"])
def test_six_loop_stops_remaining_resume_probes_after_child_failure(tmp_path, monkeypatch, failure, stage):
    """Exercise the real loop using metadata/array stubs, not model inference."""
    root, out = tmp_path / "runs", tmp_path / "proof"
    root.mkdir()
    save_json(root / "CAMPAIGN_RECEIPT.json", {"synthetic": True})
    original = {"best_checkpoint": "best", "last_checkpoint": "last", "best_sha256": "b", "last_sha256": "l"}
    monkeypatch.setattr(delivery, "_campaign_terminal", lambda _: {"results": {"F1": original}})
    monkeypatch.setattr(delivery, "Bundle", lambda _: SimpleNamespace(data_sha="data", norm_sha="norm"))
    monkeypatch.setattr(delivery, "_checkpoint_pair", lambda *a: (original, {"best": {}, "last": {}}))
    monkeypatch.setattr(delivery, "_require_forward_reference", lambda *a: None)
    arrays = {"training_indices": np.array([0])}
    monkeypatch.setattr(delivery, "_output_arrays", lambda *a: arrays)
    monkeypatch.setattr(delivery, "_resume_contract", lambda *a: (tmp_path / "contract", None))
    def mock_load(command, log_path, **kw):
        if stage == "fresh_load":
            raise failure("synthetic fresh-load failure; no model executed")
        target = Path(command[command.index("--out") + 1])
        target.mkdir(parents=True)
        artifacts = {}
        for name in ("best", "last"):
            path = target / (name + "_outputs.npz")
            np.savez(path, **arrays)
            artifacts[name] = {"checkpoint_sha256": original[name + "_sha256"], "output_sha256": sha256(path)}
        save_json(target / "FRESH_PROCESS_RECEIPT.json", {"status": "PASS", "pid": 123,
                  "data_sha": "data", "normalizer_sha": "norm", "artifacts": artifacts})
        return {"pid": 123, "returncode": 0}
    calls = []
    def mock_resume(*a, **kw):
        calls.append(kw)
        raise failure("synthetic child failure; no optimizer executed")
    monkeypatch.setattr(delivery, "_command", mock_load)
    monkeypatch.setattr(delivery, "_resume_command", mock_resume)
    result = delivery.verify_load_resume("unused", root, out, "cpu", deadline_utc=FUTURE,
                                        training_budget_sha256="clock", lock_fds=(9,))
    assert result["status"] == "FAIL" and set(result["results"]) == {"F1"}
    assert result["not_run_labels"] == [label for label in delivery.RUN_LABELS if label != "F1"]
    assert calls == ([{"deadline_utc": FUTURE, "lock_fds": (9,)}] if stage == "resume" else [])
    assert not (out / "F2").exists()
