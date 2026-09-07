"""Synthetic common-token no-leakage and requested-response tests."""

from copy import deepcopy

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from research.broadband56_nn.models import build_inverse  # noqa: E402
from research.broadband56_nn.specs import (  # noqa: E402
    TOKEN_DIM, forward_loss, make_spec, spectrum_loss, tokenize,
)


@pytest.fixture(autouse=True)
def bounded_threads_and_seed():
    previous = torch.get_num_threads()
    torch.set_num_threads(min(previous, 2))
    torch.manual_seed(17)
    yield
    torch.set_num_threads(previous)


def example(task="SPECTRUM", mode="single"):
    s = torch.randn(2, 56, 32)
    y = torch.rand(2, 56, 4)
    frequency = torch.arange(5.0, 61.0) * 1e9
    spec = make_spec(s, y, torch.ones_like(y, dtype=torch.bool), frequency,
                     np.random.default_rng(17), task=task, mode=mode)
    normalizer = {"s_mean": np.zeros(32), "s_scale": np.ones(32),
                  "y_mean": np.zeros(4), "y_scale": np.ones(4)}
    return spec, normalizer


@pytest.mark.parametrize("task", ["SPECTRUM", "PHYSICAL"])
def test_hidden_labels_tolerance_relation_cannot_change_tokens(task):
    spec, normalizer = example(task)
    tokens, condition = tokenize(spec, normalizer)
    changed = deepcopy(spec)
    for prefix in ("s", "y"):
        mask = changed[f"{prefix}_mask"]
        changed[f"{prefix}_target"][~mask] = float("nan")
        changed[f"{prefix}_tolerance"][~mask] = float("nan")
    changed["y_relation"][~changed["y_mask"]] = 999
    altered, altered_condition = tokenize(changed, normalizer)
    assert tokens.shape == (2, 56, TOKEN_DIM)
    assert torch.isfinite(altered).all()
    assert torch.equal(tokens, altered)
    assert torch.equal(condition, altered_condition)
    if task == "PHYSICAL":
        assert not spec["s_mask"].any()
        assert torch.count_nonzero(tokens[..., 17:113]) == 0


@pytest.mark.parametrize("kind", ["I1", "I2", "I3", "I4"])
def test_hidden_full_s_never_enters_physical_inverse(kind):
    spec, normalizer = example("PHYSICAL", "single")
    changed = deepcopy(spec)
    changed["s_target"].fill_(1e9)
    changed["y_target"][~changed["y_mask"]] = -1e9
    tokens, condition = tokenize(spec, normalizer)
    changed_tokens, changed_condition = tokenize(changed, normalizer)
    model = build_inverse(kind, 11).eval()
    with torch.no_grad():
        torch.testing.assert_close(model(tokens, condition), model(changed_tokens, changed_condition), rtol=0, atol=0)


@pytest.mark.parametrize("mode", ["full", "band", "multi", "single"])
def test_task_sampling_is_fixed_seed_and_single_geometry_response(mode):
    first, _ = example("SPECTRUM", mode)
    s, y = first["s_target"], first["y_target"]
    second = make_spec(s, y, torch.ones_like(y, dtype=torch.bool), first["frequency_hz"],
                       np.random.default_rng(17), task="SPECTRUM", mode=mode)
    assert first["description"] == second["description"]
    assert torch.equal(first["s_mask"], second["s_mask"])
    assert first["s_target"] is s  # No geometries are spliced into one target.
    counts = first["s_mask"].any(-1).sum(-1)
    if mode == "full":
        assert (counts == 56).all()
    elif mode == "single":
        assert (counts == 1).all()
    elif mode == "multi":
        assert (counts == 4).all()
    else:
        for description in first["description"]:
            chosen = description["frequency_indices"]
            assert chosen == list(range(chosen[0], chosen[-1] + 1))


def test_empty_and_nonfinite_requested_conditions_fail():
    spec, normalizer = example()
    spec["s_mask"].zero_()
    spec["y_mask"].zero_()
    with pytest.raises(ValueError, match="all-empty"):
        tokenize(spec, normalizer)
    spec["s_mask"][0, 0, 0] = True
    spec["s_mask"][1, 0, 0] = True
    spec["s_target"][0, 0, 0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite requested target"):
        tokenize(spec, normalizer)


def test_forward_difference_loss_matches_response_difference_not_flattening():
    target = torch.arange(56.0).view(1, 56, 1).expand(2, -1, 32)
    exact_loss, exact_parts = forward_loss(target, target, torch.ones(32))
    assert exact_loss == 0
    assert exact_parts["difference_mse"] == 0
    offset_loss, offset_parts = forward_loss(target + 1, target, torch.ones(32))
    assert offset_loss == 1
    assert offset_parts["difference_mse"] == 0
    _, flat_parts = forward_loss(torch.zeros_like(target), target, torch.ones(32))
    assert flat_parts["difference_mse"] == 1


def test_spectrum_loss_uses_requested_channels_only():
    spec, _ = example()
    prediction = spec["s_target"].clone()
    prediction[~spec["s_mask"]] = 12345.0
    assert torch.equal(spectrum_loss(prediction, spec, torch.ones(32)), torch.zeros(2))
    prediction[spec["s_mask"]] += 2.0
    torch.testing.assert_close(spectrum_loss(prediction, spec, torch.ones(32)), torch.full((2,), 4.0))
