"""Synthetic orchestration tests. These do not constitute seven-model training."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.broadband56_nn import seven_suite as suite
from research.broadband56_nn import study_once as once


def test_study_identity_excludes_parent_checkpoint_and_time():
    assert once.study_key("campaign") == once.study_key("campaign")
    assert once.study_key("campaign") != once.study_key("another")
    assert once.study_key("campaign", "v4") != once.study_key("campaign")


def test_atomic_json_never_clobbers_immutable(tmp_path):
    path = tmp_path / "receipt.json"
    once.atomic_json(path, {"a": 1}, immutable=True)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        once.atomic_json(path, {"a": 2}, immutable=True)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".unpublished-*"))


def test_journal_recovers_from_missing_mutable_cache_and_rejects_tamper(tmp_path):
    journal = once.Journal(tmp_path)
    journal.append("WAITING_FOR_10K", {"count": 5000})
    journal.append("WAITING_FOR_10K", {"count": 6000})
    (tmp_path / "run_status.json").unlink()
    assert journal.load()["detail"]["count"] == 6000
    first = tmp_path / "events/event_000001.json"
    first.write_text(first.read_text().replace("5000", "5001"))
    with pytest.raises(ValueError, match="hash chain"):
        journal.load()


def test_permanent_lock_blocks_duplicate_and_releases(tmp_path):
    lock = tmp_path / "study.lock"
    with once.lease(lock):
        with pytest.raises(once.BusyStudy):
            with once.lease(lock):
                pass
    assert lock.exists()
    with once.lease(lock):
        pass


def test_child_inherits_lock_after_parent_closes_fd(tmp_path):
    lock = tmp_path / "study.lock"
    with once.lease(lock) as fd:
        child = subprocess.Popen([sys.executable, "-c", "import time;print('READY',flush=True);time.sleep(.25)"],
                                 stdout=subprocess.PIPE, text=True, pass_fds=(fd,))
        assert child.stdout.readline().strip() == "READY"
    with pytest.raises(once.BusyStudy):
        with once.lease(lock):
            pass
    assert child.wait(timeout=5) == 0
    with once.lease(lock):
        pass


def _waiting_fixture(tmp_path, monkeypatch, count=9999):
    request = {"study_key": once.study_key("synthetic"), "device": "cpu", "campaign_id": "synthetic",
               "control_root": str(tmp_path / "control")}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"overall_status": "PASS", "decision": "USE_CHECKPOINT",
                                    "contract_fingerprint_sha256": "science", "expected_accepted": count}))
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"schema": "bb_source_manifest.v1", "campaign_id": "synthetic", "contract_fingerprint_sha256": "science",
                                 "files": {"checkpoint_receipt": once.pin(checkpoint)}}))
    monkeypatch.setattr(suite, "validate_request", lambda _: None)
    return path, source, tmp_path / "control"


def test_below_10k_does_not_import_training_or_create_snapshot(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(suite, "_train_stage", lambda *a: pytest.fail("training called below10K"))
    a = suite.check_once(request, control, source)
    b = suite.check_once(request, control, source)
    assert a["status"] == b["status"] == "WAITING_FOR_10K"
    assert b["sequence"] == 2
    root = control / once.study_key("synthetic")
    assert not (root / "selection").exists()
    assert not (root / "stages").exists()


def test_check_is_same_study_when_source_count_grows(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch, 5000)
    suite.check_once(request, control, source)
    checkpoint = tmp_path / "checkpoint_next.json"
    checkpoint.write_text(json.dumps({"overall_status": "PASS", "decision": "USE_CHECKPOINT",
        "contract_fingerprint_sha256": "science", "expected_accepted": 9000}))
    metadata = json.loads(source.read_text())
    metadata["files"]["checkpoint_receipt"] = once.pin(checkpoint)
    source.write_text(json.dumps(metadata))
    second = suite.check_once(request, control, source)
    assert second["detail"]["committed_accepted"] == 9000
    assert len(list(control.glob("10k-*"))) == 1


def test_running_check_cannot_submit_a_second_worker(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    root = control / once.study_key("synthetic")
    with once.lease(root / "study.lock"):
        result = suite.check_once(request, control, source)
    assert result["status"] == "ALREADY_RUNNING"
    assert not (root / "events").exists()


def test_foreign_or_changed_checkpoint_cannot_supply_count(tmp_path, monkeypatch):
    _, source, _ = _waiting_fixture(tmp_path, monkeypatch)
    (tmp_path / "checkpoint.json").write_text("{}")
    with pytest.raises(ValueError, match="identity changed"):
        suite.committed_count(source)


def test_scientific_stage_budgets_and_shared_F2_mapping():
    request = {"forward_steps": 256, "inverse_steps": 128}
    assert len(suite.ORDER) == 12
    assert len(suite.MAPPING) + 1 == 7
    assert suite._stage_spec("BB00_FORWARD", request)["budget"] == 256
    assert suite._stage_spec("BB00", request)["budget"] == 128
    assert suite._stage_spec("FREF", request)["seed"] == 29
    assert {suite.MAPPING[k][0] for k in ("BB02", "BB04", "BB05", "BB06")} == {"F2"}


def test_recovery_refuses_no_committed_checkpoint(tmp_path):
    (tmp_path / "attempt_0001").mkdir()
    (tmp_path / "attempt_0001/checkpoint_step_000032.pt").write_bytes(b"partial")
    with pytest.raises(ValueError, match="no committed checkpoint"):
        suite._resume_candidate(tmp_path, {"role": "forward", "kind": "F1"}, "data")


def test_failed_stage_or_changed_request_does_not_get_overwritten(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    suite.check_once(request, control, source)
    initial = (control / once.study_key("synthetic") / "experiment_plan.json").read_bytes()
    request.write_text(json.dumps({"study_key": once.study_key("synthetic"), "device": "cpu", "changed": True,
                                  "control_root": str(control)}))
    with pytest.raises(ValueError, match="immutable registry copy differs"):
        suite.check_once(request, control, source)
    assert (control / once.study_key("synthetic") / "experiment_plan.json").read_bytes() == initial


@pytest.mark.parametrize("phase", ["evaluate", "package", "resume"])
def test_noninitial_commands_never_launch_new_training(tmp_path, monkeypatch, phase):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(suite, "_train_stage", lambda *a: pytest.fail("must not train"))
    with pytest.raises(ValueError, match="requires"):
        suite.check_once(request, control, source, phase=phase)
    assert not (control / once.study_key("synthetic") / "stages").exists()


def test_terminal_receipt_adoption_ignores_adjacent_attempt_logs(tmp_path, monkeypatch):
    root = tmp_path / "study"
    data = tmp_path / "data"
    data.mkdir()
    once.atomic_json(data / "data_manifest.json", {"artifacts": {"dataset.npz": {"sha256": "test"}}})
    stage = root / "stages/F1"
    attempt = stage / "attempt_0001"
    attempt.mkdir(parents=True)
    receipt = attempt / "TRAINING_RECEIPT.json"
    once.atomic_json(receipt, {"status": "SYNTHETIC"})
    for suffix in (".log", "_PROCESS.json", "_REQUEST.json"):
        (stage / ("attempt_0001" + suffix)).write_text("SYNTHETIC")
    monkeypatch.setattr(suite, "_qualified_receipt", lambda *a: ({"ok": True}, True))
    monkeypatch.setattr(suite, "run_child", lambda *a: pytest.fail("terminal stage duplicated"))
    path, result = suite._train_stage(root, "F1", {"forward_steps": 32, "inverse_steps": 32},
                                     data, tmp_path / "parity", "2000-01-01T00:00:00+00:00", None, ())
    assert path == receipt and result["ok"]
    assert (stage / "STAGE_COMPLETE.json").is_file()


def test_foreign_campaign_is_not_accepted_for_stable_study(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    metadata = json.loads(source.read_text())
    metadata["campaign_id"] = "foreign"
    source.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="source campaign"):
        suite.check_once(request, control, source)


def test_delivery_child_receives_live_lease(tmp_path):
    from research.broadband56_nn.delivery import _command
    with once.lease(tmp_path / "study.lock") as fd:
        command = [sys.executable, "-c", "import os,sys;os.fstat(int(sys.argv[1]));print('LEASE_INHERITED')", str(fd)]
        result = _command(command, tmp_path / "child.log", lock_fds=(fd,))
    assert result["returncode"] == 0
    assert "LEASE_INHERITED" in (tmp_path / "child.log").read_text()


def test_same_request_cannot_silently_move_control_roots(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="control root differs"):
        suite.check_once(request, tmp_path / "another_control", source)
    assert not (tmp_path / "another_control").exists()


def test_package_completion_requires_exact_nested_files_and_active_study(tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    once.atomic_json(root / "experiment_plan.json", {"synthetic": True, "wall_budget_seconds": 1800})
    once.atomic_json(root / "STUDY_MODELS.json", {"synthetic": True})
    # Hash-only package validation remains valid after the immutable deadline.
    deadline = "2000-01-01T00:30:00+00:00"
    once.atomic_json(root / "TRAINING_BUDGET.json", {
        "started_utc": "2000-01-01T00:00:00+00:00", "deadline_utc": deadline, "seconds": 1800})
    budget = once.pin(root / "TRAINING_BUDGET.json")
    binding = {"effective_deadline_utc": deadline, "training_budget_sha256": budget["sha256"]}
    proofs = {}
    for name, labels in (("six_resume_proof", ("F1", "F2", "F3", "FREF", *suite.MAPPING)),
                         ("bb00_resume_proof", ("forward", "inverse"))):
        path = root / (name + ".json")
        once.atomic_json(path, {"status": "PASS", **binding, "results": {
            label: {"status": "PASS", **binding, "checks": {"effective_deadline_bound": True}}
            for label in labels}})
        proofs[name] = once.pin(path)
    output = root / "packages"
    output.mkdir()
    nested = output / "model"
    nested.mkdir()
    (nested / "SHA256SUMS.txt").write_text("SYNTHETIC_NESTED_INDEX")
    once.atomic_json(output / "SEVEN_PACKAGE.json", {"status": "TRAINED_AND_EVALUATED",
        "study": str(root), "study_plan": once.pin(root / "experiment_plan.json"),
        "models": once.pin(root / "STUDY_MODELS.json"), "training_budget": budget,
        "effective_resume_deadline_utc": deadline, **proofs})
    once.atomic_json(output / "SEVEN_MANIFEST.json", {"files": suite._package_files(output)})
    index = "".join(p["sha256"] + "  " + p["path"] + "\n"
                    for p in suite._package_files(output, exclude_root=("SHA256SUMS.txt",)))
    (output / "SHA256SUMS.txt").write_text(index)
    suite._verify_seven_package(output)
    (nested / "SHA256SUMS.txt").write_text("CHANGED")
    with pytest.raises(ValueError, match="artifact set differs"):
        suite._verify_seven_package(output)


def test_partial_package_marker_is_not_declared_complete(tmp_path, monkeypatch):
    request, source, control = _waiting_fixture(tmp_path, monkeypatch)
    package = control / once.study_key("synthetic") / "packages/SEVEN_PACKAGE.json"
    once.atomic_json(package, {"status": "SYNTHETIC_INTERRUPTED_ASSEMBLY"})
    result = suite.check_once(request, control, source)
    assert result["status"] == "WAITING_FOR_10K"
    assert package.exists()
