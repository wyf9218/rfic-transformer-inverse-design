"""Differentiable research adaptation of the production Broadband56 extractor.

Parity sources (the data manifest must bind their actual SHA-256 identities):
``network_analysis.s_to_z``, ``analysis.extraction.single_ended_to_differential_z``
and ``campaigns.broadband56_s4p_qa.audit_exact56_s4p``.  The latter, rather
than the historical 15 GHz metric extractor, defines the broadband ratios.

S channels are row-major, interleaved real/imaginary: s11_re, s11_im, ...,
s44_re, s44_im. No reference impedance or geometry bounds are inferred.
Neither physical validity nor analytical geometry checks prove manufacturability.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import torch
from torch import Tensor, nn


S_CHANNEL_ORDER = tuple(
    f"s{row}{column}_{part}"
    for row in range(1, 5) for column in range(1, 5) for part in ("re", "im")
)
Y_NAMES = ("Lp_nH", "Ls_nH", "Q_scalar", "K_abs")
RATIO_DENOMINATOR_MIN = 1.0e-18
INDUCTANCE_PRODUCT_MIN = 1.0e-30


def _port_parameters(contract: Mapping[str, Any]) -> tuple[Any, float]:
    if int(contract.get("ports", 4)) != 4:
        raise ValueError("Broadband56 requires exactly four signal ports")
    if tuple(contract.get("port_order", ())) != ("P001", "P002", "P003", "P004"):
        raise ValueError("An explicit production external P001..P004 port order is required")
    if tuple(contract.get("internal_permutation", (0, 1, 3, 2))) != (0, 1, 3, 2):
        raise ValueError("internal port permutation differs from the production extractor")
    mode = contract.get("mode", contract.get("port_mode", "single_ended_shield_grounded"))
    if mode != "single_ended_shield_grounded":
        raise ValueError("unsupported physical port/grounding convention")
    if contract.get("channel_order", list(S_CHANNEL_ORDER)) != list(S_CHANNEL_ORDER):
        if tuple(contract.get("channel_order", ())) != S_CHANNEL_ORDER:
            raise ValueError("S channels must be explicitly converted to row-major interleaved RI")
    z0 = contract.get("reference_impedance_ohm")
    if z0 is None:
        raise ValueError("reference_impedance_ohm must come from the accepted source contract")
    floor = float(contract.get("solve_min_singular_value", 1.0e-12))
    if not math.isfinite(floor) or floor <= 0:
        raise ValueError("solve_min_singular_value must be positive and finite")
    return z0, floor


def _ratio(numerator: Tensor, denominator: Tensor) -> tuple[Tensor, Tensor]:
    valid = (denominator.abs() > RATIO_DENOMINATOR_MIN)
    valid = valid & torch.isfinite(numerator) & torch.isfinite(denominator)
    safe = numerator / torch.where(valid, denominator, torch.ones_like(denominator))
    valid = valid & torch.isfinite(safe)
    return torch.where(valid, safe, torch.full_like(safe, float("nan"))), valid


def _below_half_srf(frequencies: Tensor, reactance: Tensor) -> Tensor:
    """Exact production first positive-to-nonpositive crossing/censor rule.

    This diagnostic mask is intentionally not a train-time request mask.
    """
    crossing = (reactance[:, :-1] > 0) & (reactance[:, 1:] <= 0)
    has_crossing = crossing.any(dim=1)
    index = crossing.to(torch.int64).argmax(dim=1)
    previous = reactance.gather(1, index[:, None]).squeeze(1)
    following = reactance.gather(1, (index + 1)[:, None]).squeeze(1)
    delta = following - previous
    denominator = torch.where(delta != 0, delta, torch.ones_like(delta))
    f0, f1 = frequencies[index], frequencies[index + 1]
    estimate = f0 - previous * (f1 - f0) / denominator
    estimate = torch.minimum(torch.maximum(estimate, f0), f1)
    bracketed = 2 * frequencies[None, :] < estimate[:, None]
    censored = 2 * frequencies[None, :] <= frequencies[-1]
    return ((reactance[:, :1] > 0) &
            torch.where(has_crossing[:, None], bracketed, censored))


def extract_physical(
    s_real_imag: Tensor, frequency_hz: Tensor, port_contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Extract production-defined physical descriptors without detaching gradients.

    The exact unregularized solve is used for nonsingular predictions. Unsolvable
    matrices receive an identity *substitute for numerical safety only*, explicit
    invalid status, NaN raw descriptors and a nonzero penalty; they cannot be
    scored as exact physical results. Ill-conditioned inputs below the recorded
    solve threshold are conservatively rejected, not silently regularized.

    Complex128 operations run on CPU for MPS inputs (differentiable device copies).
    The return tensors are on the input device. Raw ``y`` preserves undefined
    descriptors as NaN; ``y_safe`` is a declared zero placeholder accompanied by
    ``valid``. Use :func:`requested_physical_loss`, never a prediction-derived
    denominator, for training physical requests.
    """
    if s_real_imag.ndim != 3 or s_real_imag.shape[-1] != 32:
        raise ValueError("S must have shape [B,F,32]")
    if not s_real_imag.is_floating_point():
        raise ValueError("S real/imag channels must be floating point")
    z0_value, solve_floor = _port_parameters(port_contract)
    source_device = s_real_imag.device
    solve_device = torch.device("cpu") if source_device.type == "mps" else source_device
    # Torch 2.8 MPS can attempt the dtype conversion before the device copy;
    # separate both differentiable operations to avoid unsupported MPS float64.
    s_ri = s_real_imag.to(device=solve_device).to(dtype=torch.float64)
    f = torch.as_tensor(frequency_hz).to(device=solve_device).to(dtype=torch.float64)
    if f.ndim != 1 or len(f) != s_ri.shape[1] or len(f) < 2:
        raise ValueError("frequency_hz must be a shared one-dimensional frequency grid")
    if not bool(torch.isfinite(f).all() and (f > 0).all() and (f[1:] > f[:-1]).all()):
        raise ValueError("frequencies must be finite, positive and strictly increasing")
    z0 = torch.as_tensor(z0_value, device=solve_device, dtype=torch.float64)
    if z0.ndim == 0:
        z0 = z0.repeat(4)
    if z0.shape != (4,) or not bool(torch.isfinite(z0).all() and (z0 > 0).all()):
        raise ValueError("reference impedance must be positive scalar or four-vector")
    source_finite = torch.isfinite(s_ri).all(dim=-1)
    # Sanitization is explicitly invalidated below; it does not repair labels.
    finite_ri = torch.where(torch.isfinite(s_ri), s_ri, torch.zeros_like(s_ri))
    shaped = finite_ri.reshape(*s_ri.shape[:2], 4, 4, 2)
    s = torch.complex(shaped[..., 0], shaped[..., 1])
    eye = torch.eye(4, dtype=torch.complex128, device=solve_device)
    denominator = eye - s
    singular_values = torch.linalg.svdvals(denominator)
    minimum_sv = singular_values[..., -1]
    solve_valid = source_finite & (minimum_sv > solve_floor)
    safe_denominator = torch.where(solve_valid[..., None, None], denominator, eye)
    # A B^{-1} = solve(B^T,A^T)^T, matching the production multiplication order.
    ratio_matrix = torch.linalg.solve(
        safe_denominator.transpose(-2, -1), (eye + s).transpose(-2, -1)
    ).transpose(-2, -1)
    root_z0 = z0.sqrt()
    z = root_z0[:, None] * ratio_matrix * root_z0[None, :]
    # Equivalent to the production (0,1,3,2) reorder and Av @ Z @ inv(Ai).
    transform = torch.tensor([[1, 0], [-1, 0], [0, -1], [0, 1]],
                             dtype=torch.complex128, device=solve_device)
    z_diff = transform.T @ z @ transform
    z11, z22, z21 = z_diff[..., 0, 0], z_diff[..., 1, 1], z_diff[..., 1, 0]
    omega = 2 * math.pi * f[None, :]
    lp, ls, mutual = z11.imag / omega, z22.imag / omega, z21.imag / omega
    qp, qp_valid = _ratio(z11.imag, z11.real)
    qs, qs_valid = _ratio(z22.imag, z22.real)
    qmin = torch.minimum(qp, qs)
    product = (lp * ls).abs()
    k_valid = product > INDUCTANCE_PRODUCT_MIN
    k = mutual / torch.where(k_valid, product, torch.ones_like(product)).sqrt()
    k = torch.where(k_valid, k, torch.full_like(k, float("nan")))
    ls_over_lp, lp_ratio_valid = _ratio(ls, lp)
    y = torch.stack((lp * 1e9, ls * 1e9, qmin, k.abs()), dim=-1)
    numeric_valid = (solve_valid & torch.isfinite(z.real).all(dim=(-2, -1)) &
                     torch.isfinite(z.imag).all(dim=(-2, -1)))
    finite_features = (torch.isfinite(y).all(dim=-1) & qp_valid & qs_valid &
                       k_valid & lp_ratio_valid & torch.isfinite(ls_over_lp))
    descriptor_valid = (numeric_valid & finite_features & (z11.real > 0) &
                        (z22.real > 0) & (z11.imag > 0) & (z22.imag > 0))
    strict_valid = (descriptor_valid & _below_half_srf(f, z11.imag) &
                    _below_half_srf(f, z22.imag))
    y = torch.where(numeric_valid[..., None], y, torch.full_like(y, float("nan")))
    valid = descriptor_valid[..., None].expand_as(y)
    # A fixed failure cost cannot disappear by masking out a difficult prediction.
    # Extra smooth violations are dimensionless using explicit 50-ohm scale only
    # as a penalty scale, not as a substituted reference impedance.
    violation = (torch.relu(-z11.real) + torch.relu(-z22.real) +
                 torch.relu(-z11.imag) + torch.relu(-z22.imag)) / 50.0
    singular_cost = torch.relu(1.0 - minimum_sv / solve_floor).square()
    invalid_penalty = (~descriptor_valid).to(torch.float64) + violation + singular_cost
    invalid_penalty = invalid_penalty + (~solve_valid).to(torch.float64) * finite_ri.square().mean(-1)

    def back(value: Tensor) -> Tensor:
        # MPS has no float64 support. Device casting remains connected to autograd.
        dtype = s_real_imag.dtype if source_device.type == "mps" and value.is_floating_point() else value.dtype
        return value.to(dtype=dtype).to(device=source_device)

    return {
        "y": back(y), "y_safe": back(torch.where(torch.isfinite(y), y, torch.zeros_like(y))),
        "qp": back(torch.where(numeric_valid, qp, torch.full_like(qp, float("nan")))),
        "qs": back(torch.where(numeric_valid, qs, torch.full_like(qs, float("nan")))),
        "k_signed": back(torch.where(numeric_valid, k, torch.full_like(k, float("nan")))),
        "valid": back(valid), "valid_broadband": back(valid),
        "valid_strict": back(strict_valid[..., None].expand_as(y)),
        "broadband_descriptor_valid": back(descriptor_valid),
        "strict_lumped_valid": back(strict_valid), "s_parameter_valid": back(numeric_valid),
        "invalid_penalty": back(invalid_penalty),
        "diagnostics": {
            "minimum_solve_singular_value": back(minimum_sv),
            "solve_valid": back(solve_valid), "nonfinite_input": back(~source_finite),
            "solve_min_singular_value": solve_floor, "solve_dtype": "complex128",
            "solve_device": str(solve_device), "regularized_solve_used": False,
            "invalid_solver_substitution_is_not_physical_result": True,
            "physical_names": Y_NAMES, "real_emx_validation": "NOT_RUN",
        },
    }


def requested_physical_loss(
    prediction: Mapping[str, Tensor], target: Tensor, condition_mask: Tensor,
    scale: Tensor, tolerance: Tensor | None = None, relation: Tensor | None = None,
) -> Tensor:
    """Per-geometry mean request violation; invalid requests cost at least one.

    Relation values are 0=EQ, 1=LOWER, 2=UPPER. Only caller-frozen condition
    masks set the denominator. The caller separately intersects training
    requests with *source-label* validity before invoking this function.
    """
    if condition_mask.dtype != torch.bool:
        raise ValueError("condition_mask must be boolean, not numeric weights")
    mask = condition_mask
    if mask.shape != target.shape or target.shape != prediction["y_safe"].shape:
        raise ValueError("physical request, prediction and condition mask shapes must match")
    if not bool(mask.flatten(1).any(dim=1).all()):
        raise ValueError("every geometry requires at least one physical condition")
    if not bool(torch.isfinite(target[mask]).all()):
        raise ValueError("requested target labels must be finite")
    scale = torch.as_tensor(scale, device=target.device, dtype=target.dtype)
    if scale.shape != (target.shape[-1],):
        raise ValueError("scale must be one train-frozen value per physical feature")
    if not bool(torch.isfinite(scale).all() and (scale > 0).all()):
        raise ValueError("scales must be positive, finite and train-frozen")
    sanitized_target = torch.where(mask, target, torch.zeros_like(target))
    residual = (prediction["y_safe"] - sanitized_target) / scale
    relations = torch.zeros_like(target, dtype=torch.long) if relation is None else relation
    if relations.shape != target.shape or relations.dtype not in (torch.int8, torch.int16, torch.int32, torch.int64, torch.uint8):
        raise ValueError("relation must be an integer tensor matching physical targets")
    if bool(((relations < 0) | (relations > 2))[mask].any()):
        raise ValueError("relation must be 0=EQ, 1=LOWER or 2=UPPER")
    signed = torch.where(relations == 1, -residual, residual)
    violation = torch.where(relations == 0, residual.abs(), signed)
    if tolerance is not None and tolerance.shape != target.shape:
        raise ValueError("tolerance must match physical target shape")
    allowed = torch.zeros_like(target) if tolerance is None else torch.where(mask, tolerance, torch.zeros_like(target)) / scale
    if not bool(torch.isfinite(allowed[mask]).all() and (allowed[mask] >= 0).all()):
        raise ValueError("requested tolerances must be finite and nonnegative")
    cost = torch.relu(violation - allowed).square()
    invalid = ~prediction["valid"]
    cost = cost + invalid.to(cost.dtype) * (1.0 + prediction["invalid_penalty"][..., None])
    cost = torch.where(mask, cost, torch.zeros_like(cost))
    return (cost.flatten(1).sum(1) / mask.flatten(1).sum(1)).mean()


class GeometryDecoder(nn.Module):
    """Common continuous decoder with explicit externally sourced geometry bounds.

    When available, ``topology_contract`` is the saved production trainer's
    power_line_port_ground_overlap contract. The hard mapping is the autograd
    adaptation of ``_project_geometry_hard_feasible_topology``. Without it the
    mode is explicitly envelope-only, never renamed hard-feasible.
    """

    def __init__(self, field_names: Sequence[str], lower: Sequence[float],
                 upper: Sequence[float], topology_contract: Mapping[str, Any] | None = None):
        super().__init__()
        self.field_names = tuple(field_names)
        if len(set(self.field_names)) != len(self.field_names):
            raise ValueError("geometry field names must be unique")
        low, high = torch.as_tensor(lower, dtype=torch.float32), torch.as_tensor(upper, dtype=torch.float32)
        if low.shape != (len(self.field_names),) or high.shape != low.shape:
            raise ValueError("geometry order and bounds must have identical dimensions")
        if not bool(torch.isfinite(low).all() and torch.isfinite(high).all() and (high > low).all()):
            raise ValueError("geometry bounds must be finite and increasing")
        self.register_buffer("lower", low)
        self.register_buffer("upper", high)
        self.topology_contract = dict(topology_contract or {})
        self.mode = "hard_feasible_topology_v1" if self.topology_contract else "independent_sigmoid_envelope_only"
        self.index = {name: index for index, name in enumerate(self.field_names)}
        if self.topology_contract:
            provided_index = self.topology_contract.get("index_by_semantic", {})
            if any(name not in self.index or self.index[name] != value for name, value in provided_index.items()):
                raise ValueError("topology semantic index differs from geometry field order")
            if not self.topology_contract.get("available"):
                raise ValueError("topology contract is not available")
            power = self.topology_contract.get("power_line_port_ground_overlap", {})
            if not power.get("enabled"):
                raise ValueError("hard topology requires the recorded power-line ground-overlap contract")
            for name in ("bar_offset_um", "shield_opening_clearance_um"):
                if name not in power or not math.isfinite(float(power[name])):
                    raise ValueError(f"topology contract missing finite {name}")
            for role in ("primary", "secondary"):
                for suffix in ("outer_width_um", "outer_height_um", "terminal_y_span_um", "feed_extension_um"):
                    if f"{role}_{suffix}" not in self.index:
                        raise ValueError("hard topology requires the exact production semantic columns")
            if not {"line_width_um", "offset_um"}.issubset(self.index):
                raise ValueError("hard topology requires width and offset columns")

    def forward(self, logits: Tensor) -> Tensor:
        if logits.shape[-1] != len(self.field_names):
            raise ValueError("logit dimension differs from frozen geometry contract")
        if not bool(torch.isfinite(logits).all()):
            raise ValueError("geometry logits must be finite")
        low, high = self.lower.to(logits), self.upper.to(logits)
        sigmoid = torch.sigmoid(logits.clamp(-40, 40))
        values = list((low + (high - low) * sigmoid).unbind(-1))
        if not self.topology_contract:
            return torch.stack(values, dim=-1)
        idx = self.index
        for role in ("primary", "secondary"):
            width = values[idx[f"{role}_outer_width_um"]]
            height = values[idx[f"{role}_outer_height_um"]]
            terminal = idx[f"{role}_terminal_y_span_um"]
            straight_cap = height - (math.sqrt(2) - 1) * torch.minimum(width, height)
            cap = torch.minimum(torch.minimum(width, height), straight_cap)
            cap = torch.minimum(cap, high[terminal])
            if bool((cap < low[terminal] - 1e-9).any()):
                raise ValueError("geometry envelope cannot satisfy terminal topology")
            cap = torch.maximum(cap, low[terminal])
            values[terminal] = low[terminal] + sigmoid[..., terminal] * (cap - low[terminal])
        requirements = _feed_requirements(values, idx, low, self.topology_contract)
        for role, required in requirements.items():
            feed = idx[f"{role}_feed_extension_um"]
            if bool((required > high[feed] + 1e-9).any()):
                raise ValueError("geometry envelope cannot satisfy feed topology")
            required = torch.minimum(required, high[feed])
            values[feed] = required + sigmoid[..., feed] * (high[feed] - required)
        return torch.stack(values, dim=-1)

    def feasibility(self, geometry: Tensor) -> dict[str, Any]:
        return geometry_feasibility(geometry, self.field_names, self.lower, self.upper, self.topology_contract)


def _feed_requirements(values: Sequence[Tensor], idx: Mapping[str, int], low: Tensor,
                       topology: Mapping[str, Any]) -> dict[str, Tensor]:
    power = topology["power_line_port_ground_overlap"]
    margin = (float(power["bar_offset_um"]) + float(power["shield_opening_clearance_um"]) +
              float(power.get("training_safety_margin_um") or 0))
    pw, sw = values[idx["primary_outer_width_um"]], values[idx["secondary_outer_width_um"]]
    offset, line = values[idx["offset_um"]], values[idx["line_width_um"]]
    left, other_left = -.5 * pw, offset - .5 * sw
    right, other_right = offset + .5 * sw, .5 * pw
    primary = left - torch.minimum(left, other_left) + margin + line
    secondary = torch.maximum(right, other_right) - right + margin + line
    return {
        "primary": torch.maximum(torch.maximum(primary, -offset), low[idx["primary_feed_extension_um"]]),
        "secondary": torch.maximum(torch.maximum(secondary, offset), low[idx["secondary_feed_extension_um"]]),
    }


def geometry_feasibility(
    geometry: Tensor, field_names: Sequence[str], lower: Sequence[float] | Tensor,
    upper: Sequence[float] | Tensor, topology_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analytical envelope/topology penalty; never a Cadence/Calibre certificate."""
    low, high = torch.as_tensor(lower, device=geometry.device, dtype=geometry.dtype), torch.as_tensor(upper, device=geometry.device, dtype=geometry.dtype)
    if geometry.shape[-1] != len(field_names) or low.shape != high.shape or low.shape != (len(field_names),):
        raise ValueError("geometry dimensions do not match frozen contract")
    finite = torch.isfinite(geometry).all(-1)
    safe = torch.where(torch.isfinite(geometry), geometry, low)
    span = high - low
    if not bool(torch.isfinite(span).all() and (span > 0).all()):
        raise ValueError("bounds must have finite positive spans")
    penalty = ((torch.relu(low - safe) + torch.relu(safe - high)) / span).square().mean(-1)
    envelope_pass = finite & (safe >= low).all(-1) & (safe <= high).all(-1)
    topology_pass = torch.ones_like(envelope_pass)
    index = {name: i for i, name in enumerate(field_names)}
    violations: list[Tensor] = []
    # These octagonal terminal constraints are independent of private PDK values.
    for role in ("primary", "secondary"):
        names = [f"{role}_{suffix}" for suffix in ("outer_width_um", "outer_height_um", "terminal_y_span_um")]
        if all(name in index for name in names):
            width, height, terminal = (safe[..., index[name]] for name in names)
            cap = torch.minimum(torch.minimum(width, height), height - (math.sqrt(2) - 1) * torch.minimum(width, height))
            violations.append(torch.relu(terminal - cap) / span[index[names[2]]])
    if topology_contract:
        requirements = _feed_requirements(list(safe.unbind(-1)), index, low, topology_contract)
        for role, required in requirements.items():
            feed = index[f"{role}_feed_extension_um"]
            violations.append(torch.relu(required - safe[..., feed]) / span[feed])
    if violations:
        stacked = torch.stack(violations, -1)
        penalty = penalty + stacked.square().mean(-1)
        topology_pass = (stacked == 0).all(-1)
    penalty = penalty + (~finite).to(geometry.dtype)
    return {"penalty": penalty, "envelope_pass": envelope_pass,
            "analytical_pass": envelope_pass & topology_pass,
            "power_line_topology_checked": bool(topology_contract),
            "manufacturability": "NOT_PROVEN", "grid_applied": False}
