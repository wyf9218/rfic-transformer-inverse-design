"""Opt-in development feedline construction; not a change to frozen decoders.

Only the two feed extensions may increase. Every other coordinate, physical
target, Q selection and acceptance rule is outside this transform. Continuous
projection retains autograd; export rounding is deliberately nondifferentiable.
New geometry needs new proxy scoring / Q selection and GDS, DRC and fresh EMX.
Historical proxy scores and physical labels must never be copied onto it.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping

import numpy as np
import torch
from torch import Tensor

from .evaluation import _grid_from_contract, _grid_geometry
from .io import canonical_sha
from .physics import GeometryDecoder, _feed_requirements, geometry_feasibility


VERSION = "eucap15_feedline_projection_development_v1"
FEED_FIELDS = ("primary_feed_extension_um", "secondary_feed_extension_um")


class FeedlineProjection:
    """Minimum non-shortening extension satisfying the unchanged topology.

    This is an explicitly selected new candidate-construction intervention,
    not the historical model's output or a retrained / FINAL model. Reuse the
    existing contract validation and exact feed inequalities. An impossible
    feed upper bound is rejected, never expanded or clipped into acceptance.
    """

    def __init__(self, contract: Mapping, *, version: str):
        if version != VERSION:
            raise ValueError("Explicit development feedline version required")
        self.contract = copy.deepcopy(dict(contract))
        if self.contract.get("units") != "um":
            raise ValueError("Explicit um geometry units required")
        self.grid_um = _grid_from_contract(self.contract)
        topology = self.contract.get("topology_contract")
        if not topology:
            raise ValueError("Source-bound topology contract required")
        # Construct only for schema validation; no network inference or training.
        validator = GeometryDecoder(self.contract["field_names"], self.contract["lower"],
                                    self.contract["upper"], topology)
        self.fields = validator.field_names
        self.index = validator.index
        self.feed_indices = tuple(self.index[name] for name in FEED_FIELDS)
        self.topology = topology
        self.contract_sha256 = canonical_sha(self.contract)

    def _bounds(self, geometry: Tensor):
        if not geometry.is_floating_point() or geometry.ndim < 1 or geometry.shape[-1] != len(self.fields):
            raise ValueError("Floating geometry tensor with exact field dimension required")
        low = torch.as_tensor(self.contract["lower"], dtype=geometry.dtype, device=geometry.device)
        high = torch.as_tensor(self.contract["upper"], dtype=geometry.dtype, device=geometry.device)
        if not bool(torch.isfinite(geometry).all() and (geometry >= low).all() and (geometry <= high).all()):
            raise ValueError("Original geometry must be finite and within unchanged bounds")
        return low, high

    def continuous(self, geometry: Tensor) -> Tensor:
        """Differentiable feed-only max projection; no no_grad / detach / STE."""
        low, high = self._bounds(geometry)
        values = list(geometry.unbind(-1))
        requirements = _feed_requirements(values, self.index, low, self.topology)
        for role, required in requirements.items():
            index = self.index[role + "_feed_extension_um"]
            if bool((required > high[index]).any()):
                raise ValueError("Required feed exceeds unchanged upper bound: " + role)
            values[index] = torch.maximum(values[index], required)
        return torch.stack(values, -1)

    def construct(self, decoded_geometry) -> dict:
        """Construct one new candidate and return auditable intermediate values.

        A remaining non-feed or grid-envelope violation produces HOLD, not a
        fabricated PASS. No attempt to infer original neural logits is made.
        """
        raw = np.asarray(decoded_geometry, dtype=np.float64)
        if raw.shape != (len(self.fields),):
            raise ValueError("One exact ordered geometry vector required")
        tensor = torch.from_numpy(raw.copy())
        continuous = self.continuous(tensor).numpy()
        old_grid = _grid_geometry(raw, self.grid_um)
        nearest_grid = _grid_geometry(continuous, self.grid_um)
        grid = nearest_grid.copy()
        low, high = self._bounds(torch.from_numpy(grid))
        requirements = _feed_requirements(list(torch.from_numpy(grid).unbind(-1)),
                                           self.index, low, self.topology)
        minimum_grid = {}
        for role, required_tensor in requirements.items():
            index = self.index[role + "_feed_extension_um"]
            required = float(required_tensor)
            ticks = math.ceil(required / self.grid_um)
            # Float multiply can be below the strict threshold by one ULP.
            # Move up a complete lattice tick; never relax the old inequality.
            minimum = ticks * self.grid_um
            if minimum < required:
                minimum = (ticks + 1) * self.grid_um
            grid[index] = max(grid[index], minimum)
            minimum_grid[role] = minimum
        status = geometry_feasibility(torch.from_numpy(grid), self.fields,
            self.contract["lower"], self.contract["upper"], self.topology)
        new_geometry_sha = canonical_sha({"fields": list(self.fields), "units": "um",
            "values": np.round(grid, 12).tolist(),
            "identity_scope": "parameter_vector_only_not_actual_GDS"})
        changed = [name for i, name in enumerate(self.fields) if grid[i] != old_grid[i]]
        if not set(changed) <= set(FEED_FIELDS):
            raise AssertionError("Feed-only construction altered another grid coordinate")
        return dict(schema=VERSION, development_scope="NEW_METHOD_REPLAY_NOT_HISTORICAL_OUTPUT",
            status="ANALYTIC_ONLY_PASS" if bool(status["analytical_pass"]) else "HOLD_ANALYTIC_FAIL",
            contract_sha256=self.contract_sha256, geometry_fields=list(self.fields), units="um",
            source_decoded_geometry_um=raw.tolist(), source_nearest_grid_um=old_grid.tolist(),
            new_continuous_geometry_um=continuous.tolist(),
            new_nearest_grid_before_feed_ceiling_um=nearest_grid.tolist(),
            new_grid_geometry_um=grid.tolist(), changed_grid_fields=changed,
            continuous_delta_um=(continuous - raw).tolist(), grid_delta_um=(grid - old_grid).tolist(),
            required_grid_feed_lower_bound_um=minimum_grid,
            analytical_pass=bool(status["analytical_pass"]), envelope_pass=bool(status["envelope_pass"]),
            new_parameter_geometry_sha256=new_geometry_sha,
            new_proxy=None, new_q_proxy=None, proxy_and_q_rescoring="REQUIRED_BEFORE_NEW_PHYSICAL_SELECTION",
            actual_gds_geometry=None, gds_sha256=None, calibre_status="NOT_RUN",
            REAL_EMX_VALIDATION="NOT_RUN", manufacturability="NOT_PROVEN")
