"""Optimizer-facing vector adapters for transformer geometry."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bounds import TransformerSearchSpace
from .topology import TransformerSpec

@dataclass(frozen=True)
class TransformerOptimizationAdapter:
    """Own optimizer-facing flat/vector mapping for transformer geometry."""

    search_space: TransformerSearchSpace

    def field_order(self) -> tuple[str, ...]:
        # Keep the optimizer dimension set identical to the active search space,
        # minus any variables whose bounds collapse to a single fixed value.
        # In particular, 1-turn inductors do not expose spacing as an optimizable
        # parameter even though the geometry still carries a concrete spacing value.
        return self.search_space.optimizable_names()

    def flat_dict(self, spec: TransformerSpec) -> dict[str, object]:
        flat = spec.flat_dict()
        return {name: flat[name] for name in self.field_order()}

    def to_vector(self, spec: TransformerSpec) -> np.ndarray:
        flat = self.flat_dict(spec)
        return np.array([float(flat[name]) for name in self.field_order()], dtype=float)

    def from_vector(self, values: np.ndarray | list[float] | tuple[float, ...]) -> TransformerSpec:
        arr = np.asarray(values, dtype=float)
        field_order = self.field_order()
        if arr.shape != (len(field_order),):
            raise ValueError(f"Expected {len(field_order)} geometry values, got shape {arr.shape}")
        midpoint = self.search_space.midpoint()
        values_by_name = midpoint.flat_dict()
        values_by_name.update({name: float(value) for name, value in zip(field_order, arr)})
        return TransformerSpec.from_flat_dict(
            values_by_name,
            topology_mode=self.search_space.topology_mode,
            primary_turns=self.search_space.primary_turns,
            secondary_turns=self.search_space.secondary_turns,
            primary_center_tap=self.search_space.primary_center_tap,
            secondary_center_tap=self.search_space.secondary_center_tap,
            primary_spacing_um=midpoint.primary_spacing_um,
            secondary_spacing_um=midpoint.secondary_spacing_um,
            primary_bridge_layer=self.search_space.primary.bridge_layer,
            secondary_bridge_layer=self.search_space.secondary.bridge_layer,
            primary_bridge_via_layer=self.search_space.primary.bridge_via_layer,
            secondary_bridge_via_layer=self.search_space.secondary.bridge_via_layer,
            primary_bridge_lower_layer=self.search_space.primary.bridge_lower_layer,
            secondary_bridge_lower_layer=self.search_space.secondary.bridge_lower_layer,
            primary_bridge_lower_via_layer=self.search_space.primary.bridge_lower_via_layer,
            secondary_bridge_lower_via_layer=self.search_space.secondary.bridge_lower_via_layer,
            primary_bridge_section=self.search_space.primary.bridge_section_spec(),
            secondary_bridge_section=self.search_space.secondary.bridge_section_spec(),
            primary_vdd_bar=self.search_space.primary.vdd_bar,
            secondary_vdd_bar=self.search_space.secondary.vdd_bar,
            shield=self.search_space.shield,
        )
