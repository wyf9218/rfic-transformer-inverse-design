"""Synthetic-only immutable best-reference relocation checks."""
from pathlib import Path
import shutil

import pytest

from research.broadband56_nn import training
from research.broadband56_nn.io import load_checkpoint, sha256
from tests.test_bb_training import fixture_data


@pytest.fixture
def prepared(tmp_path):
    data, contract = fixture_data(tmp_path)
    cfg = training.TrainConfig("forward", "F1", steps=2, device="cpu",
                               threads=2, validation_interval=1)
    receipt = training.train(data, tmp_path / "original", cfg, contract)
    original = Path(receipt["best_checkpoint"])
    relocated = tmp_path / "relocated_best.pt"
    shutil.copyfile(original, relocated)
    shutil.copyfile(str(original) + ".identity.json", str(relocated) + ".identity.json")
    return data, contract, cfg, receipt, relocated


def test_resume_relocated_best_does_not_open_original_best(prepared, tmp_path, monkeypatch):
    data, contract, cfg, receipt, relocated = prepared
    original_best = Path(receipt["best_checkpoint"]).resolve()
    original_loader = training.load_checkpoint
    # Copy last as well so neither old checkpoint path needs to be opened.
    last = tmp_path / "relocated_last.pt"
    shutil.copyfile(receipt["last_checkpoint"], last)
    shutil.copyfile(receipt["last_checkpoint"] + ".identity.json", str(last) + ".identity.json")
    def checked_loader(path):
        assert Path(path).resolve() != original_best
        return original_loader(path)
    monkeypatch.setattr(training, "load_checkpoint", checked_loader)
    monkeypatch.setattr(training, "validation_loss", lambda *args: receipt["best_validation"] + 1)
    cfg.steps = 1
    result = training.train(data, tmp_path / "resumed", cfg, contract,
                            resume_checkpoint=last, resume_best_checkpoint=relocated,
                            resume_best_checkpoint_sha256=receipt["best_sha256"])
    assert result["started_step"] == 2 and result["completed_step"] == 3
    assert result["trainable_weights_changed"]
    # A carried best remains resumable again after relocation, without opening
    # its old location or changing any historical checkpoint bytes.
    second = training.train(data, tmp_path / "resumed_again", cfg, contract,
                            resume_checkpoint=result["last_checkpoint"])
    assert second["started_step"] == 3 and second["completed_step"] == 4


def test_relocated_best_requires_original_sha(prepared):
    _, _, _, receipt, relocated = prepared
    state = load_checkpoint(receipt["last_checkpoint"])
    with pytest.raises(ValueError, match="path and original SHA"):
        training.resume_best_reference(state, relocated)
    with pytest.raises(ValueError, match="SHA differs"):
        training.resume_best_reference(state, relocated, "0" * 64)


@pytest.mark.parametrize("key,value", [("normalizer_sha", "wrong"),
                                       ("best_reference_original", "/wrong/best.pt"),
                                       ("best_validation", -1)])
def test_relocated_best_must_match_previous_identity(prepared, key, value):
    _, _, _, receipt, relocated = prepared
    state = load_checkpoint(receipt["last_checkpoint"])
    state[key] = value
    with pytest.raises(ValueError, match="differs"):
        training.resume_best_reference(state, relocated, sha256(relocated))


def test_legacy_checkpoint_original_reference_is_still_checked(prepared):
    _, _, _, receipt, relocated = prepared
    state = load_checkpoint(receipt["last_checkpoint"])
    state.pop("best_reference_original")
    state["best_checkpoint"] = "/wrong/legacy_best.pt"
    with pytest.raises(ValueError, match="original reference differs"):
        training.resume_best_reference(state, relocated, sha256(relocated))
