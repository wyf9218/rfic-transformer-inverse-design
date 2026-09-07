"""Synthetic CPU lifecycle tests; never consume private research snapshots."""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn.io import load_checkpoint, save_json, sha256
from research.broadband56_nn.training import TrainConfig, train
from tests.test_bb_training import fixture_data


FINGERPRINT = "b" * 64


def _refresh_manifest(root: Path, fingerprint=FINGERPRINT):
    manifest = json.loads((root / "data_manifest.json").read_text())
    manifest["contract_fingerprint_sha256"] = fingerprint
    manifest["artifacts"] = {
        name: {"path": name, "sha256": sha256(root / name)}
        for name in ("dataset.npz", "normalizer.json", "splits.json")
    }
    (root / "data_manifest.json").write_text(json.dumps(manifest))


def _cfg(role="forward", kind="F1", steps=1, forward=None):
    return TrainConfig(role, kind, steps=steps, effective_batch=32, micro_batch=8,
                       device="cpu", threads=2, validation_interval=1,
                       physical_ready=False, forward_checkpoint=forward)


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory):
    root = tmp_path_factory.mktemp("bb_finetune_synthetic")
    data, contract = fixture_data(root)
    _refresh_manifest(data)
    forward = train(data, root / "old_forward", _cfg(steps=2), contract)
    inverse = train(data, root / "old_inverse", _cfg("inverse", "I1", forward=forward["last_checkpoint"]), contract)
    return root, data, contract, forward, inverse


def _new_snapshot(old: Path, destination: Path, *, remap=False, fingerprint=FINGERPRINT):
    destination.mkdir()
    with np.load(old / "dataset.npz", allow_pickle=False) as loaded:
        arrays = {key: loaded[key] for key in loaded.files}
    for name in ("geometry", "s", "y", "y_valid"):
        arrays[name] = np.concatenate([arrays[name], arrays[name][:1]], axis=0)
    arrays["geometry"][-1] = [0.12, 0.87]
    arrays["s"][-1] = arrays["s"][-1] + 0.01
    for name in ("geometry_ids", "geometry_sha256"):
        arrays[name] = np.concatenate([arrays[name], ["8"]])
    arrays["split"] = np.concatenate([arrays["split"], [0]])
    if remap:
        arrays["split"][0] = 1
    np.savez(destination / "dataset.npz", **arrays)
    norm = json.loads((old / "normalizer.json").read_text())
    norm["s_mean"] = [9.0] * 32  # A new fit is intentionally different.
    save_json(destination / "normalizer.json", norm)
    save_json(destination / "splits.json", {"evidence": "SYNTHETIC_GROWTH_ONLY"})
    save_json(destination / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS", "artifacts": {}})
    _refresh_manifest(destination, fingerprint)
    return destination


def _optimizer_steps(checkpoint):
    return {int(value["step"].item()) for value in checkpoint["optimizer_state"]["state"].values()}


def test_same_snapshot_resume_reproduces_exact_next_step(lifecycle, tmp_path):
    _, data, contract, first, _ = lifecycle
    resumed = train(data, tmp_path / "resumed", _cfg(), contract,
                    resume_checkpoint=first["last_checkpoint"])
    uninterrupted = train(data, tmp_path / "uninterrupted", _cfg(steps=3), contract)
    resumed_state = load_checkpoint(resumed["last_checkpoint"])
    whole_state = load_checkpoint(uninterrupted["last_checkpoint"])
    assert resumed["started_step"] == 2 and resumed["completed_step"] == 3
    assert _optimizer_steps(resumed_state) == {3}
    assert resumed_state["model_sha"] == whole_state["model_sha"]
    for name in resumed_state["model_state"]:
        assert torch.equal(resumed_state["model_state"][name], whole_state["model_state"][name])
    for key in resumed_state["optimizer_state"]["state"]:
        for name, value in resumed_state["optimizer_state"]["state"][key].items():
            assert torch.equal(value, whole_state["optimizer_state"]["state"][key][name])


def test_finetune_preserves_explicit_old_normalizer_and_resets_optimizer(lifecycle, tmp_path):
    _, old_data, contract, old_forward, _ = lifecycle
    data = _new_snapshot(old_data, tmp_path / "grown")
    old_state = load_checkpoint(old_forward["last_checkpoint"])
    result = train(data, tmp_path / "new_forward", _cfg(), contract,
                   finetune_checkpoint=old_forward["last_checkpoint"], preserve_normalizer=True)
    new_state = load_checkpoint(result["last_checkpoint"])
    config = json.loads((tmp_path / "new_forward/config.json").read_text())
    assert result["started_step"] == 0 and result["completed_step"] == 1
    assert _optimizer_steps(old_state) == {2} and _optimizer_steps(new_state) == {1}
    assert new_state["data_sha"] != old_state["data_sha"]
    assert new_state["normalizer"] == old_state["normalizer"]
    assert new_state["normalizer_sha"] == old_state["normalizer_sha"]
    assert new_state["normalizer"]["s_mean"] != json.loads((data / "normalizer.json").read_text())["s_mean"]
    assert config["normalizer_policy"] == "preserved_prior"
    assert config["optimizer_policy"] == "fresh"
    assert result["trainable_weights_changed"]


def test_finetune_requires_explicit_preservation(lifecycle, tmp_path):
    _, old, contract, forward, _ = lifecycle
    data = _new_snapshot(old, tmp_path / "grown")
    with pytest.raises(ValueError, match="explicit preserve_normalizer"):
        train(data, tmp_path / "denied", _cfg(), contract, finetune_checkpoint=forward["last_checkpoint"])
    assert (tmp_path / "denied/TRAIN_REQUEST.json").exists()
    assert not list((tmp_path / "denied").glob("checkpoint*"))


@pytest.mark.parametrize("violation", ["split_remap", "scientific_contract"])
def test_new_data_cannot_change_old_split_or_science(lifecycle, tmp_path, violation):
    _, old, contract, forward, _ = lifecycle
    data = _new_snapshot(old, tmp_path / "grown", remap=violation == "split_remap",
                         fingerprint="c" * 64 if violation == "scientific_contract" else FINGERPRINT)
    expected = "assignment changed" if violation == "split_remap" else "scientific contract fingerprint differs"
    with pytest.raises(ValueError, match=expected):
        train(data, tmp_path / "denied", _cfg(), contract,
              finetune_checkpoint=forward["last_checkpoint"], preserve_normalizer=True)


def test_resume_rejects_new_snapshot_and_finetune_rejects_old_snapshot(lifecycle, tmp_path):
    _, old, contract, forward, _ = lifecycle
    data = _new_snapshot(old, tmp_path / "grown")
    with pytest.raises(ValueError, match="same snapshot and normalizer"):
        train(data, tmp_path / "bad_resume", _cfg(), contract, resume_checkpoint=forward["last_checkpoint"])
    with pytest.raises(ValueError, match="same snapshot requires resume"):
        train(old, tmp_path / "bad_finetune", _cfg(), contract,
              finetune_checkpoint=forward["last_checkpoint"], preserve_normalizer=True)


def test_inverse_finetune_requires_new_snapshot_forward_first(lifecycle, tmp_path):
    _, old, contract, old_forward, old_inverse = lifecycle
    data = _new_snapshot(old, tmp_path / "grown")
    with pytest.raises(ValueError, match="forward role/snapshot mismatch"):
        train(data, tmp_path / "bad_inverse", _cfg("inverse", "I1", forward=old_forward["last_checkpoint"]),
              contract, finetune_checkpoint=old_inverse["last_checkpoint"], preserve_normalizer=True)
    new_forward = train(data, tmp_path / "new_forward", _cfg(), contract,
                        finetune_checkpoint=old_forward["last_checkpoint"], preserve_normalizer=True)
    inverse = train(data, tmp_path / "new_inverse", _cfg("inverse", "I1", forward=new_forward["last_checkpoint"]),
                    contract, finetune_checkpoint=old_inverse["last_checkpoint"], preserve_normalizer=True)
    inverse_state = load_checkpoint(inverse["last_checkpoint"])
    forward_state = load_checkpoint(new_forward["last_checkpoint"])
    assert inverse_state["data_sha"] == forward_state["data_sha"]
    assert inverse_state["normalizer_sha"] == forward_state["normalizer_sha"]
    assert inverse_state["forward_model_sha"] == forward_state["model_sha"]
    assert inverse["frozen_forward_unchanged"] is True
    assert inverse["trainable_weights_changed"] is True
    assert _optimizer_steps(inverse_state) == {1}
