"""Synthetic parity with the actual production NumPy implementation, not a copy."""

from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn.physics import (
    GeometryDecoder, S_CHANNEL_ORDER, extract_physical, geometry_feasibility,
    requested_physical_loss,
)
from rfic_transformer_inverse_design.analysis.extraction import differential_2port_to_4port_z
from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as production
from rfic_transformer_inverse_design.network_analysis import z_to_s
from rfic_transformer_inverse_design.sim.base import SParameterResult


PORTS = {"ports": 4, "port_order": ["P001", "P002", "P003", "P004"],
         "reference_impedance_ohm": 50.0, "channel_order": list(S_CHANNEL_ORDER)}
FIELDS = ("primary_outer_width_um", "primary_outer_height_um", "secondary_outer_width_um",
          "secondary_outer_height_um", "line_width_um", "primary_terminal_y_span_um",
          "secondary_terminal_y_span_um", "offset_um", "primary_feed_extension_um",
          "secondary_feed_extension_um")
# Arbitrary synthetic bounds/topology numbers, not a foundry contract.
LOW = [200., 200., 200., 200., 4., 10., 10., -20., 100., 100.]
HIGH = [300., 300., 300., 300., 8., 60., 60., 20., 250., 250.]
TOPOLOGY = {"available": True, "index_by_semantic": {n: i for i, n in enumerate(FIELDS)},
            "power_line_port_ground_overlap": {"enabled": True, "bar_offset_um": 12.,
                "shield_opening_clearance_um": 9., "training_safety_margin_um": 0.}}


def synthetic_s(crossing=False, z0=50.):
    f = np.arange(5, 61, dtype=float) * 1e9
    omega = 2 * np.pi * f
    zd = np.zeros((56, 2, 2), dtype=np.complex128)
    reactance_factor = 1 - (f / 42.3e9) ** 2 if crossing else np.ones(56)
    zd[:, 0, 0] = 5. + 1j * omega * .8e-9 * reactance_factor
    zd[:, 1, 1] = 7. + 1j * omega * 1.1e-9 * reactance_factor
    zd[:, 0, 1] = zd[:, 1, 0] = 1j * omega * .35e-9
    z = differential_2port_to_4port_z(zd, common_mode_impedance_ohm=75.)
    s = z_to_s(z, z0)
    ri = np.stack((s.real, s.imag), axis=-1).reshape(1, 56, 32)
    return f, s, torch.tensor(ri, dtype=torch.float64)


@pytest.mark.parametrize("crossing", [False, True])
@pytest.mark.parametrize("z0", [50., [40., 45., 50., 55.]])
def test_full_broadband_extractor_numeric_and_masks_parity(monkeypatch, crossing, z0):
    f, s, ri = synthetic_s(crossing, z0)
    monkeypatch.setattr(production, "load_touchstone", lambda _: SParameterResult(f, s, z0))
    reference = production.audit_exact56_s4p(Path("synthetic_not_read.s4p"))
    expected = np.array([[r[k] for k in ("lp_nh", "ls_nh", "qmin", "k_abs")] for r in reference.rows])
    result = extract_physical(ri, torch.tensor(f), {**PORTS, "reference_impedance_ohm": z0})
    np.testing.assert_allclose(result["y"][0].numpy(), expected, rtol=2e-10, atol=2e-11, equal_nan=True)
    for actual, name in (("qp", "qp"), ("qs", "qs"), ("k_signed", "signed_k")):
        np.testing.assert_allclose(result[actual][0].numpy(), [r[name] for r in reference.rows], rtol=2e-10, atol=2e-11)
    for key in ("broadband_descriptor_valid", "strict_lumped_valid"):
        np.testing.assert_array_equal(result[key][0].numpy(), [r[key] == "true" for r in reference.rows])


def test_physical_gradients_finite_nonzero_and_request_relation():
    f, _, ri = synthetic_s()
    ri.requires_grad_(True)
    result = extract_physical(ri, torch.tensor(f), PORTS)
    target = result["y"].detach() * 1.03
    mask = torch.ones_like(target, dtype=torch.bool)
    loss = requested_physical_loss(result, target, mask, torch.tensor([1., 1., 10., 1.]))
    loss.backward()
    assert torch.isfinite(ri.grad).all() and ri.grad.abs().sum() > 0
    same = result["y"].detach()
    assert requested_physical_loss(result, same, mask, torch.ones(4)).item() == 0
    lower = torch.ones_like(same, dtype=torch.long)
    upper = torch.full_like(lower, 2)
    assert requested_physical_loss(result, same * .9, mask, torch.ones(4), relation=lower).item() == 0
    assert requested_physical_loss(result, same * 1.1, mask, torch.ones(4), relation=upper).item() == 0


@pytest.mark.parametrize("invalid_kind", ["singular", "nonfinite", "negative_reactance"])
def test_invalid_predictions_cannot_remove_requests_or_nan_loss(invalid_kind):
    f, _, ri = synthetic_s()
    if invalid_kind == "singular":
        ri.zero_()
        for i in range(4):
            ri[..., 2 * (i * 4 + i)] = 1
    elif invalid_kind == "nonfinite":
        ri[..., 0] = float("nan")
    else:
        ri[..., 1::2] *= -1
    ri.requires_grad_(True)
    result = extract_physical(ri, torch.tensor(f), PORTS)
    assert not result["valid"].any()
    target = torch.ones((1, 56, 4), dtype=torch.float64)
    loss = requested_physical_loss(result, target, torch.ones_like(target, dtype=torch.bool), torch.ones(4))
    assert torch.isfinite(loss) and loss >= 1
    loss.backward()
    assert torch.isfinite(ri.grad).all()


def test_ratio_invalid_boundary_matches_production():
    # Exact zero S means real Z but zero inductances: k and Ls/Lp are undefined.
    result = extract_physical(torch.zeros(1, 56, 32, dtype=torch.float64), torch.arange(5, 61) * 1e9, PORTS)
    assert not result["valid"].any()
    assert result["s_parameter_valid"].all()
    assert torch.isnan(result["k_signed"]).all()
    assert production._derived_ratio(1., 1e-18) != production._derived_ratio(1., 1e-18)


def test_missing_contract_and_empty_spec_rejected():
    f, _, ri = synthetic_s()
    with pytest.raises(ValueError, match="reference_impedance"):
        extract_physical(ri, torch.tensor(f), {"port_order": PORTS["port_order"]})
    result = extract_physical(ri, torch.tensor(f), PORTS)
    with pytest.raises(ValueError, match="at least one"):
        requested_physical_loss(result, result["y"], torch.zeros_like(result["y"], dtype=torch.bool), torch.ones(4))


def test_hidden_labels_cannot_poison_physical_loss():
    f, _, ri = synthetic_s()
    result = extract_physical(ri, torch.tensor(f), PORTS)
    target = result["y"].clone()
    mask = torch.zeros_like(target, dtype=torch.bool)
    mask[:, 7] = True
    target[~mask] = float("nan")
    assert requested_physical_loss(result, target, mask, torch.ones(4)).item() == 0


def test_physical_request_schema_rejects_implicit_weight_and_relation_casts():
    f, _, ri = synthetic_s()
    result = extract_physical(ri, torch.tensor(f), PORTS)
    target = result["y"]
    mask = torch.ones_like(target, dtype=torch.bool)
    with pytest.raises(ValueError, match="boolean"):
        requested_physical_loss(result, target, mask.float(), torch.ones(4))
    with pytest.raises(ValueError, match="per physical feature"):
        requested_physical_loss(result, target, mask, torch.ones(56))
    with pytest.raises(ValueError, match="integer tensor"):
        requested_physical_loss(result, target, mask, torch.ones(4), relation=torch.zeros_like(target))


def test_geometry_hard_map_values_and_jacobian_against_actual_numpy_trainer():
    from scripts.train_physical_feature_tandem_inverse import _project_geometry_hard_feasible_topology
    decoder = GeometryDecoder(FIELDS, LOW, HIGH, TOPOLOGY).double()
    logits = torch.tensor(np.random.default_rng(17).normal(size=(3, 10)), dtype=torch.float64, requires_grad=True)
    actual = decoder(logits)
    norm = {"y_mean": np.zeros(10), "y_scale": np.ones(10)}
    expected, jac = _project_geometry_hard_feasible_topology(logits.detach().numpy(), np.array(LOW), np.array(HIGH), norm, TOPOLOGY)
    np.testing.assert_allclose(actual.detach().numpy(), expected, rtol=1e-12, atol=1e-11)
    gradient = torch.autograd.functional.jacobian(decoder, logits)
    for b in range(3):
        np.testing.assert_allclose(gradient[b, :, b, :].numpy(), jac[b], rtol=1e-10, atol=1e-10)
    feasibility = decoder.feasibility(actual)
    assert feasibility["analytical_pass"].all()
    assert feasibility["manufacturability"] == "NOT_PROVEN"


def test_geometry_envelope_only_explicit_and_violations_not_hidden():
    decoder = GeometryDecoder(FIELDS, LOW, HIGH)
    geometry = decoder(torch.zeros(2, 10, requires_grad=True))
    assert decoder.mode == "independent_sigmoid_envelope_only"
    assert not decoder.feasibility(geometry)["power_line_topology_checked"]
    geometry = geometry.clone()
    geometry[0, 5] = 1000
    result = geometry_feasibility(geometry, FIELDS, LOW, HIGH)
    assert result["penalty"][0] > 0 and not result["analytical_pass"][0]


@pytest.mark.skipif(not torch.backends.mps.is_available(), reason="MPS unavailable")
def test_mps_physics_cpu_complex128_roundtrip_retains_gradient():
    f, _, ri = synthetic_s()
    ri = ri.float().to("mps").requires_grad_(True)
    result = extract_physical(ri, torch.tensor(f), PORTS)
    assert result["y"].device.type == "mps"
    assert result["diagnostics"]["solve_device"] == "cpu"
    result["y_safe"].square().mean().backward()
    assert torch.isfinite(ri.grad).all() and ri.grad.abs().sum() > 0
    decoder = GeometryDecoder(FIELDS, LOW, HIGH).to("mps")
    assert decoder(torch.zeros(1, 10, device="mps")).device.type == "mps"
