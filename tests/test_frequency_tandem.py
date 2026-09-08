"""Synthetic-only frequency routing, actual optimizer and exact resume checks."""
from dataclasses import replace
import json

import numpy as np
import pytest
import torch

from research.broadband56_nn.bb00 import BB00Config, load_bb00, prepare_bb00, train_bb00
from research.broadband56_nn.frequency_tandem import infer, load_frequency_pair, training_config
from research.broadband56_nn.io import load_checkpoint, sha256
from research.broadband56_nn.training import Bundle, model_digest
from tests.test_bb00 import inputs


def fixture(tmp_path):
    data, contract, recipe = inputs(tmp_path)
    with np.load(data / "dataset.npz") as archive:
        arrays = {k: archive[k] for k in archive.files}
    arrays["strict_lumped_valid"] = arrays["y_valid"].all(-1)
    arrays["strict_lumped_valid"][:, 26:] = False
    arrays["y_valid"][:, 26:] = False
    arrays["broadband_descriptor_valid"] = np.ones(arrays["y"].shape[:2], dtype=bool)
    arrays["y"][:, 15] *= np.array([2., 3., 1.5, .8])
    np.savez(data / "dataset.npz", **arrays)
    manifest = json.loads((data / "data_manifest.json").read_text())
    manifest["artifacts"]["dataset.npz"]["sha256"] = sha256(data / "dataset.npz")
    (data / "data_manifest.json").write_text(json.dumps(manifest))
    return data, contract, recipe


@pytest.mark.parametrize("frequency", [4, 61, 15.5, True, "15"])
def test_reject_non_exact_frequency_routes(frequency):
    with pytest.raises(ValueError, match="integer"):
        training_config({"schema": "frequency_tandem_train.v1", "train": {
            "role": "forward", "frequency_ghz": frequency, "label_mode": "STRICT_LUMPED"}})


def test_strict_and_descriptor_domains_remain_separate(tmp_path):
    data, contract, _ = fixture(tmp_path)
    bundle = Bundle(data)
    original_split = bundle.arrays["split"].copy()
    contract = json.loads(contract.read_text())
    with pytest.raises(ValueError, match="NO_STRICT_LABELS"):
        prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8), True, frequency_ghz=31)
    norm, train, val, frequency, _ = prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8), True,
        frequency_ghz=31, label_mode="POINTWISE_DESCRIPTOR_EXPERIMENTAL")
    assert frequency == 26 and norm["label_mode"] == "POINTWISE_DESCRIPTOR_EXPERIMENTAL"
    assert len(train) == 6 and len(val) == 2
    assert not bundle.arrays["strict_lumped_valid"][:, frequency].any()
    assert np.array_equal(bundle.arrays["split"], original_split)


def test_each_frequency_fits_train_only_normalizer(tmp_path):
    data, contract, _ = fixture(tmp_path)
    bundle, contract = Bundle(data), json.loads(contract.read_text())
    norm15, *_ = prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8), True)
    norm20, *_ = prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8), True, frequency_ghz=20)
    assert norm15["y_mean"] != norm20["y_mean"]
    bundle.arrays["y"][bundle.arrays["split"] != 0] = 1e6
    unchanged, *_ = prepare_bb00(bundle, contract, (2.5, 2.5, 20., .8), True, frequency_ghz=20)
    assert norm20 == unchanged


def test_real_updates_frozen_forward_sparse_checkpoints_and_resume(tmp_path):
    torch.set_num_threads(2)
    data, contract, recipe = fixture(tmp_path)
    cfg = BB00Config("forward", steps=3, schedule_total_steps=4, device="cpu", validation_interval=100,
        checkpoint_interval=100, allow_io_adaptation=True, frequency_ghz=20)
    forward = train_bb00(data, tmp_path / "forward", cfg, contract, recipe, sha256(recipe))
    assert forward["updates_this_run"] == 3
    assert len(list((tmp_path / "forward").glob("checkpoint*.pt"))) == 2
    f, state = load_bb00(forward["best_checkpoint"])
    assert f.architecture["frequency_ghz"] == 20.0 and f.layers[0].in_features == 2
    frozen_digest = model_digest(f)
    icfg = replace(cfg, role="inverse", forward_checkpoint=forward["best_checkpoint"])
    inverse = train_bb00(data, tmp_path / "inverse", icfg, contract, recipe, sha256(recipe))
    assert inverse["trainable_weights_changed"] and inverse["frozen_forward_unchanged"]
    model, _ = load_bb00(inverse["last_checkpoint"])
    f.requires_grad_(False)
    target = torch.tensor([state["normalizer"]["y_mean"]], dtype=torch.float32)
    geometry = model(target)
    f(geometry).sum().backward()
    gradient = sum(float(p.grad.abs().sum()) for p in model.parameters() if p.grad is not None)
    assert np.isfinite(gradient) and gradient > 0
    assert all(p.grad is None for p in f.parameters()) and model_digest(f) == frozen_digest
    resumed = train_bb00(data, tmp_path / "resume", replace(icfg, steps=1), contract, recipe,
        sha256(recipe), resume_checkpoint=inverse["last_checkpoint"])
    whole = train_bb00(data, tmp_path / "whole", replace(icfg, steps=4), contract, recipe, sha256(recipe))
    assert resumed["started_step"] == 3 and resumed["completed_step"] == 4
    assert load_checkpoint(resumed["last_checkpoint"])["model_sha"] == load_checkpoint(whole["last_checkpoint"])["model_sha"]
    prediction = infer(forward["best_checkpoint"], inverse["last_checkpoint"], target.numpy(),
        frequency_ghz=20, label_mode="STRICT_LUMPED")
    assert prediction["validation_source"] == "SELF_PROXY"
    assert prediction["REAL_EMX_VALIDATION"] == "NOT_RUN"
    with pytest.raises(ValueError, match="route mismatch"):
        load_frequency_pair(forward["best_checkpoint"], inverse["last_checkpoint"],
            frequency_ghz=15, label_mode="STRICT_LUMPED")
    with pytest.raises(ValueError, match="OUTSIDE_TRAIN_SUPPORT"):
        infer(forward["best_checkpoint"], inverse["last_checkpoint"], [[1e9]*4],
            frequency_ghz=20, label_mode="STRICT_LUMPED")
