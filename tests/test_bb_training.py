"""Synthetic integration tests; these are not research pretraining results."""
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn.io import save_json, load_checkpoint, sha256
from research.broadband56_nn.training import TrainConfig, train


def fixture_data(tmp_path):
    root = tmp_path / "synthetic_data"
    root.mkdir()
    rng = np.random.default_rng(7)
    np.savez(root / "dataset.npz", geometry_ids=np.arange(8).astype(str),
             geometry_sha256=np.arange(8).astype(str),
             geometry=rng.uniform(size=(8, 2)), frequency_hz=np.arange(5, 61, dtype=float)*1e9,
             s=rng.normal(size=(8, 56, 32)), y=np.ones((8, 56, 4)),
             y_valid=np.ones((8, 56, 4), bool), split=np.array([0]*6+[1]*2))
    norm = {"field_names": ["a", "b"], "g_min": [0, 0], "g_max": [1, 1],
            "s_mean": [0]*32, "s_scale": [1]*32, "y_mean": [0]*4, "y_scale": [1]*4}
    save_json(root / "normalizer.json", norm)
    save_json(root / "splits.json", {"evidence": "SYNTHETIC_TEST_ONLY"})
    save_json(root / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS",
              "evidence": "SYNTHETIC_TEST_ONLY", "artifacts": {
                  name: {"path": name, "sha256": sha256(root / name)}
                  for name in ("dataset.npz", "normalizer.json", "splits.json")}})
    contract = tmp_path / "contract.json"
    save_json(contract, {"field_names": ["a", "b"], "lower": [0, 0], "upper": [1, 1]})
    return root, contract


def test_real_optimizer_checkpoint_resume_and_no_clobber(tmp_path):
    data, contract = fixture_data(tmp_path)
    cfg = TrainConfig("forward", "F1", steps=1, device="cpu", validation_interval=1)
    first = train(data, tmp_path / "first", cfg, contract)
    state = load_checkpoint(first["last_checkpoint"])
    assert state["step"] == 1 and state["optimizer_state"]["state"]
    second = train(data, tmp_path / "second", cfg, contract, resume_checkpoint=first["last_checkpoint"])
    assert second["started_step"] == 1 and second["completed_step"] == 2
    assert second["trainable_weights_changed"]
    with pytest.raises(FileExistsError):
        train(data, tmp_path / "first", cfg, contract)


def test_resume_data_identity_change_rejected(tmp_path):
    data, contract = fixture_data(tmp_path)
    cfg = TrainConfig("forward", "F1", steps=1, device="cpu", validation_interval=1)
    receipt = train(data, tmp_path / "first", cfg, contract)
    with open(data / "dataset.npz", "ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        train(data, tmp_path / "second", cfg, contract, resume_checkpoint=receipt["last_checkpoint"])
