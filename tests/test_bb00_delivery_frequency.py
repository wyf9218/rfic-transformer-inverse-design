"""Exact route acceptance tests with synthetic arrays and no optimizer/model."""
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.broadband56_nn import bb00_delivery as delivery
from research.broadband56_nn.io import read_json, save_json


def bundle():
    y = np.ones((5, 56, 4), dtype=float)
    y[:, :, 0] = np.arange(5)[:, None] + 1
    y[:, :, 2] = np.arange(5, 61)[None, :]
    return SimpleNamespace(train=np.asarray([0, 1, 2, 3]), data_sha="synthetic",
        arrays={"frequency_hz": np.arange(5, 61) * 1e9, "y": y,
            "y_valid": np.ones_like(y, dtype=bool),
            "strict_lumped_valid": np.ones((5, 56), dtype=bool),
            "geometry": np.ones((5, 10))})


@pytest.mark.parametrize("frequency", range(5, 21))
def test_fixed_inputs_use_requested_frequency_strict_and_finite_train_only(frequency):
    data = bundle()
    f = frequency - 5
    data.arrays["strict_lumped_valid"][0, f] = False
    data.arrays["y"][1, f, 2] = np.nan
    indices, observed = delivery._inputs(data, frequency)
    assert observed == f
    assert indices.tolist() == [2, 3]  # Row 4 is not train and cannot enter.
    assert np.all(data.arrays["y"][indices, observed, 2] == frequency)


@pytest.mark.parametrize("frequency", [True, 4, 21, 15.5])
def test_acceptance_route_rejects_nonqualified_frequencies(frequency):
    with pytest.raises(ValueError, match="exact integer frequency"):
        delivery._inputs(bundle(), frequency)


def test_legacy_missing_checkpoint_route_is_15_only():
    state = {"normalizer": {}, "train_config": {}}
    delivery._state_route(state, 15, "STRICT_LUMPED")
    with pytest.raises(ValueError, match="checkpoint frequency/label route differs"):
        delivery._state_route(state, 5, "STRICT_LUMPED")


@pytest.mark.parametrize("action", ["load", "resume_one"])
@pytest.mark.parametrize("frequency", [5, 10, 20])
def test_child_request_preserves_frequency_and_original_recipe(tmp_path, monkeypatch, action, frequency):
    data = bundle()
    config = delivery.BB00Config("inverse", frequency_ghz=frequency, device="cpu",
        deadline_utc="2099-01-01T00:00:00Z", steps=12000, schedule_total_steps=12000)
    state = {"data_sha": data.data_sha, "role": "inverse", "normalizer_sha": "normalizer",
        "contract_sha": "contract", "model_sha": "model", "step": 12000,
        "normalizer": {"frequency_ghz": frequency, "label_mode": "STRICT_LUMPED"},
        "train_config": asdict(config)}
    # No actual MLP or optimizer is constructed in this synthetic worker test.
    class SyntheticOutput:
        def __call__(self, values):
            assert torch.all(values[:, 2] == frequency)
            return values
    monkeypatch.setattr(delivery, "Bundle", lambda *_: data)
    monkeypatch.setattr(delivery.torch, "set_num_threads", lambda *_: None)
    monkeypatch.setattr(delivery, "load_bb00", lambda *a, **k: (SyntheticOutput(), state))
    monkeypatch.setattr(delivery, "load_checkpoint", lambda *a, **k: state)
    calls = []
    def no_training(*args, **kwargs):
        calls.append((args, kwargs))
        assert args[2].frequency_ghz == frequency
        assert args[2].steps == 1 and args[2].schedule_total_steps == 12000
        assert args[2].deadline_utc == "2099-01-01T00:00:00Z"
        assert kwargs["resume_probe"] is True
        return {"last_checkpoint": "synthetic_only", "updates_this_run": 1}
    monkeypatch.setattr(delivery, "train_bb00", no_training)
    request = {"action": action, "data_root": "synthetic", "frequency_ghz": frequency,
        "label_mode": "STRICT_LUMPED", "role": "inverse", "device": "cpu",
        "effective_deadline_utc": "2099-01-01T00:00:00Z", "training_budget_sha256": "f" * 64,
        "best_checkpoint": "synthetic_best", "best_sha256": "b" * 64,
        "last_checkpoint": "synthetic_last", "last_sha256": "a" * 64,
        "out_receipt": str(tmp_path / "child.json"), "out_arrays": str(tmp_path / "child.npz"),
        "resume_out": str(tmp_path / "resume"), "contract_path": "synthetic",
        "legacy_replay_receipt": "synthetic", "expected_legacy_sha": "c" * 64,
        "forward_checkpoint": "same_frequency_frozen_forward"}
    save_json(tmp_path / "request.json", request)
    delivery._worker(tmp_path / "request.json")
    result = read_json(tmp_path / "child.json")
    assert result["frequency_ghz"] == frequency and result["label_mode"] == "STRICT_LUMPED"
    assert result["status"] == "PASS"
    assert len(calls) == (1 if action == "resume_one" else 0)


def test_wrong_frequency_checkpoint_never_reaches_resume_optimizer(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery.torch, "set_num_threads", lambda *_: None)
    monkeypatch.setattr(delivery, "load_checkpoint", lambda *_: {
        "normalizer": {"frequency_ghz": 15}, "train_config": {"frequency_ghz": 15}})
    monkeypatch.setattr(delivery, "train_bb00", lambda *a, **k: pytest.fail("must not train"))
    save_json(tmp_path / "request.json", {"action": "resume_one", "frequency_ghz": 5,
        "label_mode": "STRICT_LUMPED", "last_checkpoint": "synthetic",
        "effective_deadline_utc": "2099-01-01T00:00:00Z"})
    with pytest.raises(ValueError, match="checkpoint frequency/label route differs"):
        delivery._worker(tmp_path / "request.json")
