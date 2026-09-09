"""Synthetic metadata only: never load a checkpoint, NPZ, or real model."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from research.broadband56_nn import frequency_study as module
from tests.test_frequency_study import full_request, terminal_role


def save(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def shared_fixture(tmp_path):
    request_path, request = full_request(tmp_path)
    request["experiment_class"] = "DEVELOPMENT_CURRENT_SNAPSHOT"
    request["train"]["hidden_layers"] = [128, 128, 128]
    common_request = deepcopy(request)
    common_request["out"] = str(tmp_path / "common_baseline")
    common_request["train"]["hidden_layers"] = [256, 256, 256]
    common_path, common = terminal_role(tmp_path, common_request, "forward")
    Path(common["best_checkpoint"]).write_bytes(b"common-forward-inert-bytes-NOT-A-MODEL")
    common.update(best_sha256=module.sha256(common["best_checkpoint"]),
                  last_sha256=module.sha256(common["last_checkpoint"]), validation_selected_checkpoint=True)
    save(common_path, common)
    request["shared_forward_receipt"] = module.pin(common_path)
    save(request_path, request)
    return request_path, request, common_path, common


def completed_fixture(tmp_path):
    request_path, request, common_path, common = shared_fixture(tmp_path)
    roles = {}
    for role in ("forward", "inverse"):
        receipt_path, receipt = terminal_role(tmp_path, request, role)
        if role == "inverse":
            config_path = receipt_path.parent.parent / "config_0001.json"
            config = module.read_json(config_path)
            config["train"]["forward_checkpoint"] = common["best_checkpoint"]
            save(config_path, config)
        roles[role] = {"receipt": module.pin(receipt_path), "best": module.pin(receipt["best_checkpoint"]),
                       "last": module.pin(receipt["last_checkpoint"]), "status": receipt["status"]}
    pair = {"schema": "frequency_pair_receipt.v1", "status": "TRAINED_BUDGET_OR_EARLY_STOP",
            "roles": roles, "request": module.pin(request_path), "frequency_ghz": 15,
            "label_mode": "STRICT_LUMPED", "data_root": request["data_root"],
            "experiment_class": request["experiment_class"],
            "inverse_forward": module._inverse_forward(request, roles["forward"], request["dataset_sha256"])}
    return request_path, request, pair, common_path, common


def test_no_shared_request_remains_explicit_own_forward():
    own = {"receipt": {"path": "inert-own-receipt"}, "best": {"path": "inert-own-best"}}
    assert module._inverse_forward({}, own, "d" * 64) == {**own, "source": "OWN_FORWARD"}


def test_shared_receipt_exact_pin_allows_different_inverse_width(tmp_path):
    _, request, common_path, common = shared_fixture(tmp_path)
    own = {"receipt": {"path": "not-the-common-receipt"}, "best": {"path": "not-the-common-best"}}
    result = module._inverse_forward(request, own, request["dataset_sha256"])
    assert result == {"receipt": module.pin(common_path), "best": module.pin(common["best_checkpoint"]),
                      "source": "SHARED_FROZEN_FORWARD"}
    assert request["train"]["hidden_layers"] == [128, 128, 128]


@pytest.mark.parametrize("field,bad", [
    ("data_sha", "e" * 64), ("role", "inverse"), ("frequency_ghz", 20),
    ("label_mode", "POINTWISE_DESCRIPTOR_EXPERIMENTAL"), ("resume_probe", True),
    ("research_comparison_eligible", False), ("validation_selected_checkpoint", False),
])
def test_shared_receipt_wrong_qualification_rejected(tmp_path, field, bad):
    _, request, path, common = shared_fixture(tmp_path)
    common[field] = bad
    save(path, common)
    request["shared_forward_receipt"] = module.pin(path)
    with pytest.raises(ValueError, match="qualification mismatch"):
        module._inverse_forward(request, {}, request["dataset_sha256"])


def test_changed_shared_receipt_sha_rejected(tmp_path):
    _, request, path, common = shared_fixture(tmp_path)
    common["unexpected_change"] = True
    save(path, common)  # Keep the original request pin unchanged.
    with pytest.raises(ValueError):
        module._inverse_forward(request, {}, request["dataset_sha256"])


def test_changed_common_best_bytes_rejected(tmp_path):
    _, request, _, common = shared_fixture(tmp_path)
    Path(common["best_checkpoint"]).write_bytes(b"changed-inert-best")
    with pytest.raises(ValueError, match="best checkpoint changed"):
        module._inverse_forward(request, {}, request["dataset_sha256"])


@pytest.mark.parametrize("classification", ["FORMAL10K", "SYNTHETIC_DRIVER_TEST_NO_MODEL"])
def test_shared_extension_does_not_enter_other_experiment_classes(tmp_path, classification):
    _, request, _, _ = shared_fixture(tmp_path)
    request["experiment_class"] = classification
    with pytest.raises(ValueError, match="scoped to the development"):
        module._inverse_forward(request, {}, request["dataset_sha256"])


def test_completed_pair_keeps_own_forward_separate_and_reuses_without_training(tmp_path):
    path, request, pair, _, common = completed_fixture(tmp_path)
    assert pair["roles"]["forward"]["best"]["path"] != common["best_checkpoint"]
    assert pair["roles"]["forward"]["best"]["sha256"] != common["best_sha256"]
    assert pair["inverse_forward"]["best"]["path"] == common["best_checkpoint"]
    pair_path = Path(request["out"]) / "PAIR_RECEIPT.json"
    save(pair_path, pair)
    before = pair_path.read_bytes()
    with patch.object(module, "run_child") as child, patch.object(module, "_data") as data, \
            patch.object(module, "resource_snapshot") as resources:
        assert module.run(path, resume=True)["status"] == "ALREADY_TRAINED"
        child.assert_not_called()
        data.assert_not_called()
        resources.assert_not_called()
    assert pair_path.read_bytes() == before


def test_shared_pair_cannot_be_mislabeled_own_forward(tmp_path):
    _, request, pair, _, _ = completed_fixture(tmp_path)
    pair["inverse_forward"] = {"receipt": pair["roles"]["forward"]["receipt"],
                               "best": pair["roles"]["forward"]["best"], "source": "OWN_FORWARD"}
    with pytest.raises(ValueError, match="shared-forward identity"):
        module._verify_pair(pair, request, pair["request"])


def test_shared_pair_must_not_silently_omit_common_forward_identity(tmp_path):
    _, request, pair, _, _ = completed_fixture(tmp_path)
    del pair["inverse_forward"]
    with pytest.raises(ValueError, match="shared-forward.*identity"):
        module._verify_pair(pair, request, pair["request"])


def test_inverse_terminal_config_cannot_substitute_arms_own_forward(tmp_path):
    _, request, pair, _, _ = completed_fixture(tmp_path)
    inverse_receipt = Path(pair["roles"]["inverse"]["receipt"]["path"])
    config_path = inverse_receipt.parent.parent / "config_0001.json"
    config = module.read_json(config_path)
    config["train"]["forward_checkpoint"] = pair["roles"]["forward"]["best"]["path"]
    save(config_path, config)
    with pytest.raises(ValueError, match="training configuration differs"):
        module._verify_pair(pair, request, pair["request"])
