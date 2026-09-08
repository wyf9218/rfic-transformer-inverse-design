import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from research.broadband56_nn import frequency_study as module
from research.broadband56_nn.study_once import lease


def request(tmp_path):
    value = {"schema": "frequency_study_request.v1", "out": str(tmp_path / "study"),
             "device_lock": str(tmp_path / "device.lock"), "dataset_sha256": "d" * 64,
             "train": {"device": "cpu", "deadline_utc": "2099-01-01T00:00:00Z"},
             "resources": {"min_available_bytes": 1, "min_disk_bytes": 1}}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(value))
    return path, value


def test_device_lock_prevents_submission(tmp_path):
    path, value = request(tmp_path)
    with lease(value["device_lock"]), patch.object(module, "run_child") as child:
        assert module.run(path)["status"] == "BUSY"
        child.assert_not_called()


def test_resource_wait_does_not_read_data_or_submit(tmp_path):
    path, _ = request(tmp_path)
    with patch.object(module, "resource_snapshot", return_value={"status": "WAITING_RESOURCE"}), \
            patch.object(module, "_data") as data, patch.object(module, "run_child") as child:
        assert module.run(path)["status"] == "WAITING_RESOURCE"
        assert module.run(path)["status"] == "WAITING_RESOURCE"
        data.assert_not_called()
        child.assert_not_called()


def test_changed_request_fails_closed(tmp_path):
    path, value = request(tmp_path)
    with patch.object(module, "resource_snapshot", return_value={"status": "WAITING_RESOURCE"}):
        module.run(path)
    value["train"]["device"] = "mps"
    path.write_text(json.dumps(value))
    import pytest
    with pytest.raises(ValueError, match="request changed"):
        module.run(path)


def test_existing_incomplete_output_is_not_duplicated(tmp_path):
    path, value = request(tmp_path)
    old = tmp_path / "study" / "forward" / "attempt_0001"
    old.mkdir(parents=True)
    with patch.object(module, "resource_snapshot", return_value={"status": "PASS"}), \
            patch.object(module, "_data", return_value="/unused"), patch.object(module, "run_child") as child:
        assert module.run(path)["status"] == "NEEDS_RESUME"
        assert module.run(path)["status"] == "NEEDS_RESUME"
        child.assert_not_called()


def test_deadline_blocks_before_data_or_optimizer(tmp_path):
    path, value = request(tmp_path)
    value["train"]["deadline_utc"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(value))
    with patch.object(module, "_data") as data, patch.object(module, "run_child") as child:
        assert module.run(path)["status"] == "PARTIAL_BUDGET_EXHAUSTED"
        data.assert_not_called()
        child.assert_not_called()


def full_request(tmp_path):
    path, value = request(tmp_path)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"synthetic_contract": True}))
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({"synthetic_replay": True}))
    value.update(contract_path=str(contract), legacy_replay_receipt=str(replay),
                 legacy_replay_sha256=module.sha256(replay),
                 experiment_class="SYNTHETIC_DRIVER_TEST_NO_MODEL",
                 data_root=str(tmp_path / "prepared"), dataset_sha256="d" * 64)
    value["train"].update(frequency_ghz=15, label_mode="STRICT_LUMPED",
                          steps=10, schedule_total_steps=10)
    path.write_text(json.dumps(value))
    return path, value


def terminal_role(tmp_path, request_value, role, *, number=1):
    """Plain inert files and metadata only; never serialize or load a model."""
    from dataclasses import asdict
    from research.broadband56_nn.bb00 import BB00Config
    root = Path(request_value["out"]) / role
    attempt = root / f"attempt_{number:04d}"
    attempt.mkdir(parents=True)
    checkpoint = attempt / "checkpoint_step_000010.pt"
    checkpoint.write_bytes(b"synthetic-inert-not-a-model")
    (root / f"attempt_{number:04d}.log").write_text("synthetic terminal log\n")
    train = asdict(BB00Config(**dict(request_value["train"], role=role, log_progress=True)))
    if role == "inverse":
        train["forward_checkpoint"] = str(Path(request_value["out"]) / "forward" / "attempt_0001" / "checkpoint_step_000010.pt")
    config = {"schema": "frequency_tandem_train.v1", "train": train,
              "data_root": request_value["data_root"],
              **{name: request_value[name] for name in ("contract_path", "legacy_replay_receipt", "legacy_replay_sha256")}}
    (root / f"config_{number:04d}.json").write_text(json.dumps(config))
    receipt = {"schema": "bb00_training_receipt.v1", "status": "PARTIAL",
               "role": role, "kind": "BB00", "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
               "data_sha": request_value["dataset_sha256"],
               "stop_reason": "UPDATE_BUDGET_COMPLETE", "completed_step": 10,
               "started_step": 0, "updates_this_run": 10,
               "best_checkpoint": str(checkpoint), "last_checkpoint": str(checkpoint),
               "best_sha256": module.sha256(checkpoint), "last_sha256": module.sha256(checkpoint),
               "research_comparison_eligible": True, "resume_probe": False,
               "trainable_weights_changed": True, "frozen_forward_unchanged": True}
    receipt_path = attempt / "TRAINING_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt))
    return receipt_path, receipt


def assert_rejected_without_child(path):
    """Accept explicit refusal status or validation error, never a successful pair."""
    with patch.object(module, "resource_snapshot", return_value={"status": "PASS"}), \
            patch.object(module, "_data", return_value=json.loads(path.read_text())["data_root"]), \
            patch.object(module, "run_child") as child:
        try:
            result = module.run(path)
        except ValueError:
            pass
        else:
            assert result["status"] in {"BLOCKED", "NEEDS_RESUME", "NEEDS_RECONCILIATION", "FAILED", "NO_GO"}
        child.assert_not_called()


def test_matching_completed_pair_is_no_training_or_data_probe(tmp_path):
    path, value = full_request(tmp_path)
    roles = {}
    for role in ("forward", "inverse"):
        receipt, document = terminal_role(tmp_path, value, role)
        roles[role] = {"receipt": module.pin(receipt), "best": module.pin(document["best_checkpoint"]),
                       "last": module.pin(document["last_checkpoint"]), "status": document["status"]}
    pair = {"schema": "frequency_pair_receipt.v1", "status": "TRAINED_BUDGET_OR_EARLY_STOP",
            "roles": roles, "request": module.pin(path), "frequency_ghz": 15,
            "label_mode": "STRICT_LUMPED", "data_root": value["data_root"],
            "experiment_class": value["experiment_class"]}
    pair_path = Path(value["out"]) / "PAIR_RECEIPT.json"
    pair_path.write_text(json.dumps(pair))
    before = pair_path.read_bytes()
    with patch.object(module, "resource_snapshot") as resources, patch.object(module, "_data") as data, \
            patch.object(module, "run_child") as child:
        assert module.run(path)["status"] == "ALREADY_TRAINED"
        assert module.run(path)["status"] == "ALREADY_TRAINED"
        resources.assert_not_called()
        data.assert_not_called()
        child.assert_not_called()
    assert pair_path.read_bytes() == before


def test_empty_pair_receipt_is_not_vacuously_complete(tmp_path):
    path, value = full_request(tmp_path)
    root = Path(value["out"])
    root.mkdir()
    (root / "PAIR_RECEIPT.json").write_text(json.dumps({
        "schema": "frequency_pair_receipt.v1", "roles": {},
        "frequency_ghz": 15, "label_mode": "STRICT_LUMPED", "request": module.pin(path)}))
    assert_rejected_without_child(path)


@pytest.mark.parametrize("field,bad", [
    ("frequency_ghz", 16), ("label_mode", "POINTWISE_DESCRIPTOR_EXPERIMENTAL"),
    ("request", {"path": "/unrelated/request.json", "sha256": "0" * 64})])
def test_foreign_pair_identity_is_not_reused(tmp_path, field, bad):
    path, value = full_request(tmp_path)
    roles = {}
    for role in ("forward", "inverse"):
        receipt, document = terminal_role(tmp_path, value, role)
        roles[role] = {"receipt": module.pin(receipt), "best": module.pin(document["best_checkpoint"]),
                       "last": module.pin(document["last_checkpoint"]), "status": document["status"]}
    pair = {"schema": "frequency_pair_receipt.v1", "status": "TRAINED_BUDGET_OR_EARLY_STOP",
            "roles": roles, "request": module.pin(path), "frequency_ghz": 15,
            "label_mode": "STRICT_LUMPED", "data_root": value["data_root"],
            "experiment_class": value["experiment_class"]}
    pair[field] = bad
    (Path(value["out"]) / "PAIR_RECEIPT.json").write_text(json.dumps(pair))
    assert_rejected_without_child(path)


@pytest.mark.parametrize("field,bad", [
    ("role", "inverse"), ("frequency_ghz", 16),
    ("label_mode", "POINTWISE_DESCRIPTOR_EXPERIMENTAL"), ("data_sha", "f" * 64),
    ("resume_probe", True), ("research_comparison_eligible", False)])
def test_terminal_role_must_match_current_route_data_and_comparison(tmp_path, field, bad):
    path, value = full_request(tmp_path)
    receipt, document = terminal_role(tmp_path, value, "forward")
    document[field] = bad
    receipt.write_text(json.dumps(document))
    assert_rejected_without_child(path)


def test_newer_incomplete_attempt_not_hidden_by_old_complete_receipt(tmp_path):
    path, value = full_request(tmp_path)
    terminal_role(tmp_path, value, "forward")
    later = Path(value["out"]) / "forward" / "attempt_0002"
    later.mkdir()
    (later / "PARTIAL_EVIDENCE.json").write_text("{}")
    assert_rejected_without_child(path)
    assert (later / "PARTIAL_EVIDENCE.json").read_text() == "{}"


def test_resume_selects_checkpoint_directory_not_sibling_log(tmp_path):
    path, value = full_request(tmp_path)
    root = Path(value["out"]) / "forward"
    attempt = root / "attempt_0001"
    attempt.mkdir(parents=True)
    checkpoint = attempt / "checkpoint_step_000005.pt"
    checkpoint.write_bytes(b"synthetic-inert-checkpoint")
    log = root / "attempt_0001.log"
    log.write_text("old incomplete log; must remain unchanged\n")
    calls = []
    def fake_child(command, cwd, new_log, fds):
        calls.append((command, new_log))
        assert command[command.index("--resume") + 1] == str(checkpoint)
        assert Path(command[command.index("--out") + 1]).name == "attempt_0002"
        assert new_log.name == "attempt_0002.log"
        assert len(fds) == 2
        for fd in fds:
            os.fstat(fd)
        return {"returncode": 1, "synthetic": True}
    with patch.object(module, "resource_snapshot", return_value={"status": "PASS"}), \
            patch.object(module, "_data", return_value=value["data_root"]), \
            patch.object(module, "load_checkpoint", return_value={"schema": "bb00_training_state.v1", "role": "forward", "step": 5}) as load, \
            patch.object(module, "run_child", side_effect=fake_child):
        result = module.run(path, resume=True)
    assert len(calls) == 1, result
    load.assert_called_with(checkpoint)
    assert result["status"] == "FAILED"
    assert log.read_text() == "old incomplete log; must remain unchanged\n"


def test_parent_interrupt_retains_attempt_and_never_auto_resubmits(tmp_path):
    path, value = full_request(tmp_path)
    calls = []
    def interrupted(command, cwd, log_path, fds):
        calls.append(command)
        attempt = Path(command[command.index("--out") + 1])
        attempt.mkdir(parents=True)
        (attempt / "PARTIAL_EVIDENCE.json").write_text("{}")
        log_path.write_text("interrupted synthetic child\n")
        raise SystemExit("synthetic parent interruption; no process spawned")
    with patch.object(module, "resource_snapshot", return_value={"status": "PASS"}), \
            patch.object(module, "_data", return_value=value["data_root"]), \
            patch.object(module, "run_child", side_effect=interrupted):
        with pytest.raises(SystemExit):
            module.run(path)
        result = module.run(path)
    assert result["status"] == "NEEDS_RESUME"
    assert len(calls) == 1
    assert not (Path(value["out"]) / "forward" / "attempt_0002").exists()


def test_prewriting_config_interruption_does_not_submit_new_child(tmp_path):
    path, value = full_request(tmp_path)
    role = Path(value["out"]) / "forward"
    role.mkdir(parents=True)
    marker = role / "config_0001.json"
    marker.write_text("{\"preserved_interrupted_prelaunch_config\": true}")
    before = marker.read_bytes()
    with patch.object(module, "resource_snapshot", return_value={"status": "PASS"}), \
            patch.object(module, "_data", return_value=value["data_root"]), \
            patch.object(module, "run_child") as child:
        try:
            result = module.run(path)
        except (ValueError, FileExistsError):
            pass
        else:
            assert result["status"] in {"NEEDS_RESUME", "NEEDS_RECONCILIATION", "BLOCKED"}
        child.assert_not_called()
    assert marker.read_bytes() == before


def test_data_wait_does_not_freeze_or_materialize(tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    req = {"source_access": {"synthetic": True}}
    with patch("research.broadband56_nn.research_data_access.probe_and_localize",
               return_value={"status": "WAITING_FOR_10K", "committed_accepted": 9999}) as probe, \
            patch("research.broadband56_nn.snapshot_10k.freeze_selection") as freeze, \
            patch("research.broadband56_nn.snapshot_10k.prepare_selection") as prepare:
        assert module._data(req, root) is None
        probe.assert_called_once_with(synthetic=True)
        freeze.assert_not_called()
        prepare.assert_not_called()
    assert json.loads((root / "RUN_STATE.json").read_text())["committed_accepted"] == 9999
    assert not (root / "selection").exists()
    assert not (root / "data").exists()


def test_formal_data_routes_to_existing_fixed10k_and_shared_split(tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    selection = root / "selection" / "SELECTION_MANIFEST.json"
    old_splits = tmp_path / "fixed_global_splits.json"
    old_splits.write_text("{}")
    req = {"source_access": {"synthetic": True}, "previous_splits": str(old_splits)}
    def freeze(source, out, *, previous_splits, seed):
        assert source == "/synthetic/committed/source_manifest.json"
        assert out == selection.parent
        assert previous_splits == str(old_splits)
        assert seed == 17
        out.mkdir()
        selection.write_text('{"status":"READY_FOR_10K","selected_geometries":10000}')
        return {"status": "READY_FOR_10K"}
    with patch("research.broadband56_nn.research_data_access.probe_and_localize",
               return_value={"status": "SOURCE_TRANSPORT_VERIFIED_PENDING_SELECTION",
                             "source_manifest": {"path": "/synthetic/committed/source_manifest.json"}}), \
            patch("research.broadband56_nn.snapshot_10k.freeze_selection", side_effect=freeze) as freeze_mock, \
            patch("research.broadband56_nn.snapshot_10k.prepare_selection") as prepare:
        assert module._data(req, root) == str(root / "data")
        freeze_mock.assert_called_once()
        prepare.assert_called_once_with(selection, root / "data", module.sha256(selection))
