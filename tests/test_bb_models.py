"""Synthetic architecture tests; these do not constitute research pretraining."""

import pytest

torch = pytest.importorskip("torch")

from research.broadband56_nn.models import (  # noqa: E402
    FiLMResidual,
    architecture_config,
    build_forward,
    build_inverse,
    frequency_encoding,
    masked_pooling,
    parameter_counts,
)


@pytest.fixture(autouse=True)
def bounded_threads_and_seed():
    previous = torch.get_num_threads()
    torch.set_num_threads(min(previous, 2))
    torch.manual_seed(17)
    yield
    torch.set_num_threads(previous)


def grid():
    return torch.arange(5.0, 61.0) * 1e9


def test_frequency_encoding_exact_order_and_endpoints():
    encoded = frequency_encoding(torch.tensor([5e9, 60e9], dtype=torch.float64))
    assert encoded.shape == (2, 17)
    torch.testing.assert_close(encoded[:, 0], torch.tensor([-1.0, 1.0], dtype=torch.float64))
    torch.testing.assert_close(encoded[:, 1::2], torch.zeros((2, 8), dtype=torch.float64), atol=1e-13, rtol=0)
    torch.testing.assert_close(encoded[:, 2::2], torch.ones((2, 8), dtype=torch.float64))
    encoded_middle = frequency_encoding(torch.tensor([18.75e9], dtype=torch.float64))[0]
    for k in range(1, 9):
        phase = torch.tensor(2 * torch.pi * k * 0.25, dtype=torch.float64)
        torch.testing.assert_close(encoded_middle[2 * k - 1], phase.sin())
        torch.testing.assert_close(encoded_middle[2 * k], phase.cos())


@pytest.mark.parametrize("kind", ["F1", "F2", "F3"])
def test_forward_shapes_batched_frequencies_and_input_gradient(kind):
    model = build_forward(kind, geometry_dim=11).eval()
    geometry = torch.randn(2, 11, requires_grad=True)
    output = model(geometry, grid())
    assert output.shape == (2, 56, 32)
    torch.testing.assert_close(output, model(geometry, grid().expand(2, -1)))
    # F2 starts at exactly identity FiLM and is initially geometry-independent.
    # A synthetic nonzero FiLM coefficient verifies its connected geometry path.
    if kind == "F2":
        with torch.no_grad():
            model.decoder_blocks[0].film.weight.fill_(0.001)
        output = model(geometry, grid())
    model.requires_grad_(False)
    output = model(geometry, grid())
    output.square().mean().backward()
    assert geometry.grad is not None
    assert torch.isfinite(geometry.grad).all()
    assert geometry.grad.abs().sum() > 0
    assert all(parameter.grad is None for parameter in model.parameters())
    config = architecture_config(model)
    assert config["kind"] == kind and config["geometry_dim"] == 11
    assert config["parameter_counts"]["total"] > 0


def test_film_initialization_is_identity_modulation():
    block = FiLMResidual()
    hidden = torch.randn(2, 56, 256)
    geometry = torch.randn(2, 512)
    assert torch.count_nonzero(block.film.weight) == 0
    assert torch.count_nonzero(block.film.bias) == 0
    expected = hidden + block.linear2(block.activation(block.linear1(block.norm(hidden))))
    torch.testing.assert_close(block(hidden, geometry), expected)


def test_film_geometry_gradient_opens_after_one_synthetic_forward_update():
    """Software-only gradient regression, not a research-training checkpoint."""
    model = build_forward("F2", geometry_dim=11)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
    geometry = torch.randn(2, 11, requires_grad=True)
    model(geometry, grid()).square().mean().backward()
    assert torch.count_nonzero(geometry.grad) == 0  # Exact initial identity modulation.
    assert model.decoder_blocks[0].film.weight.grad.abs().sum() > 0
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    model.eval().requires_grad_(False)
    geometry_after = geometry.detach().clone().requires_grad_(True)
    model(geometry_after, grid()).square().mean().backward()
    assert torch.isfinite(geometry_after.grad).all()
    assert geometry_after.grad.abs().sum() > 0


@pytest.mark.parametrize("kind", ["I1", "I2", "I3", "I4"])
def test_inverse_shapes_single_point_and_empty_rejection(kind):
    model = build_inverse(kind, geometry_dim=11).eval()
    tokens = torch.randn(2, 56, 137)
    requested = torch.zeros(2, 56, dtype=torch.bool)
    requested[:, 10] = True
    with torch.no_grad():
        output = model(tokens, requested)
    assert output.shape == (2, 11)
    assert torch.isfinite(output).all()
    with pytest.raises(ValueError, match="All-empty"):
        model(tokens, torch.zeros_like(requested))
    config = architecture_config(model)
    assert config["kind"] == kind
    assert config["token_dim"] == 137
    assert parameter_counts(model)["total"] == parameter_counts(model)["trainable"]


def test_masked_pool_excludes_unrequested_nan_and_rejects_empty():
    hidden = torch.tensor([[[2.0, 4.0], [float("nan"), float("nan")], [6.0, 8.0]]])
    mask = torch.tensor([[True, False, True]])
    torch.testing.assert_close(masked_pooling(hidden, mask), torch.tensor([[4.0, 6.0]]))
    with pytest.raises(ValueError, match="All-empty"):
        masked_pooling(hidden, torch.zeros_like(mask))


def test_frozen_forward_keeps_inverse_gradient_and_weights_unchanged():
    forward = build_forward("F1", 11).eval().requires_grad_(False)
    inverse = build_inverse("I1", 11)
    forward_before = {name: tensor.clone() for name, tensor in forward.state_dict().items()}
    inverse_before = {name: tensor.clone() for name, tensor in inverse.state_dict().items()}
    optimizer = torch.optim.AdamW(inverse.parameters(), lr=3e-4)
    tokens = torch.randn(2, 56, 137)
    geometry = torch.sigmoid(inverse(tokens, torch.ones(2, 56, dtype=torch.bool)))
    loss = (forward(geometry, grid()) - 0.2).square().mean()
    loss.backward()
    gradients = [parameter.grad for parameter in inverse.parameters()]
    assert all(gradient is not None and torch.isfinite(gradient).all() for gradient in gradients)
    assert sum(gradient.abs().sum() for gradient in gradients) > 0
    optimizer.step()
    assert any(not torch.equal(tensor, inverse_before[name]) for name, tensor in inverse.state_dict().items())
    assert all(torch.equal(tensor, forward_before[name]) for name, tensor in forward.state_dict().items())


def test_large_is_not_silently_reduced_to_base():
    base = build_inverse("I3", 11)
    large = build_inverse("I4", 11)
    assert len(base.transformer.layers) == 4
    assert len(large.transformer.layers) == 6
    assert large.transformer.layers[0].self_attn.embed_dim == 384
    assert large.transformer.layers[0].linear1.out_features == 1536
    assert large.transformer.layers[0].norm_first is True
    assert parameter_counts(large)["total"] > parameter_counts(base)["total"]
    assert not torch.equal(base.transformer.layers[0].linear1.weight, base.transformer.layers[1].linear1.weight)


def test_invalid_kinds_and_shapes_are_not_silently_coerced():
    with pytest.raises(ValueError):
        build_forward("F4", 11)
    with pytest.raises(ValueError):
        build_inverse("I5", 11)
    with pytest.raises(ValueError):
        build_forward("F1", 11)(torch.randn(2, 10), grid())
    with pytest.raises(ValueError):
        build_inverse("I1", 11)(torch.randn(2, 56, 137), torch.ones(2, 56))
