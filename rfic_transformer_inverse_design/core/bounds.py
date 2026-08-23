"""Search-space models for transformer optimization."""

from __future__ import annotations

from dataclasses import dataclass, field

from .topology import TransformerSpec
from .types import (
    BridgeSectionConfig,
    BridgeSectionSpec,
    InductorFixedSpec,
    ShieldSpec,
    TopologyMode,
    VddBarSpec,
)

@dataclass(frozen=True)
class InductorBounds:
    """Continuous search bounds plus fixed discrete topology for one inductor."""

    outer_width_um: tuple[float, float]
    outer_height_um: tuple[float, float]
    trace_width_um: tuple[float, float]
    spacing_um: tuple[float, float]
    terminal_y_span_um: tuple[float, float]
    feed_extension_um: tuple[float, float]
    turns: int
    center_tap: bool
    bridge_layer: int | None = None
    bridge_via_layer: int | None = None
    bridge_lower_layer: int | None = None
    bridge_lower_via_layer: int | None = None
    bridge_section: BridgeSectionConfig | None = None
    vdd_bar: VddBarSpec | None = None

    def uses_spacing(self) -> bool:
        return int(self.turns) > 1

    def uses_bridge_section(self) -> bool:
        return self.uses_spacing() and self.bridge_section is not None

    def bridge_section_spec(self) -> BridgeSectionSpec | None:
        if not self.uses_bridge_section():
            return None
        return self.bridge_section.spec()

    def uses_vdd_bar(self) -> bool:
        return bool(
            self.center_tap
            and self.vdd_bar is not None
            and self.vdd_bar.enabled
            and self.vdd_bar.bar_layer is not None
        )

    def active_flat_field_names(self, prefix: str) -> tuple[str, ...]:
        names = [f"{prefix}_width_um"]
        if self.uses_spacing():
            names.append(f"{prefix}_spacing_um")
        names.extend(
            (
                f"{prefix}_terminal_y_span_um",
                f"{prefix}_feed_extension_um",
            )
        )
        return tuple(names)


@dataclass(frozen=True)
class TransformerSearchSpace:
    """Search bounds for a transformer expressed with primary and secondary inductors."""

    primary: InductorBounds
    secondary: InductorBounds
    offset_um: tuple[float, float]
    topology_mode: TopologyMode = "1t1t"
    shield: ShieldSpec = field(default_factory=ShieldSpec)

    def names(self) -> tuple[str, ...]:
        return TransformerSpec.flat_field_order_for_topology(
            primary_turns=self.primary.turns,
            secondary_turns=self.secondary.turns,
        )

    def bounds_by_name(self) -> dict[str, tuple[float, float]]:
        return {
            "primary_outer_width_um": tuple(map(float, self.primary.outer_width_um)),
            "primary_outer_height_um": tuple(map(float, self.primary.outer_height_um)),
            "secondary_outer_width_um": tuple(map(float, self.secondary.outer_width_um)),
            "secondary_outer_height_um": tuple(map(float, self.secondary.outer_height_um)),
            "primary_width_um": tuple(map(float, self.primary.trace_width_um)),
            "secondary_width_um": tuple(map(float, self.secondary.trace_width_um)),
            "primary_spacing_um": tuple(map(float, self.primary.spacing_um)),
            "secondary_spacing_um": tuple(map(float, self.secondary.spacing_um)),
            "primary_terminal_y_span_um": tuple(map(float, self.primary.terminal_y_span_um)),
            "secondary_terminal_y_span_um": tuple(map(float, self.secondary.terminal_y_span_um)),
            "offset_um": tuple(map(float, self.offset_um)),
            "primary_feed_extension_um": tuple(map(float, self.primary.feed_extension_um)),
            "secondary_feed_extension_um": tuple(map(float, self.secondary.feed_extension_um)),
        }

    @staticmethod
    def _range_is_optimizable(bounds: tuple[float, float]) -> bool:
        lo, hi = map(float, bounds)
        return (hi - lo) > 1.0e-12

    def optimizable_names(self) -> tuple[str, ...]:
        bounds_by_name = self.bounds_by_name()
        return tuple(name for name in self.names() if self._range_is_optimizable(bounds_by_name[name]))

    @property
    def primary_outer_width_um(self) -> tuple[float, float]:
        return self.primary.outer_width_um

    @property
    def primary_outer_height_um(self) -> tuple[float, float]:
        return self.primary.outer_height_um

    @property
    def secondary_outer_width_um(self) -> tuple[float, float]:
        return self.secondary.outer_width_um

    @property
    def secondary_outer_height_um(self) -> tuple[float, float]:
        return self.secondary.outer_height_um

    @property
    def primary_width_um(self) -> tuple[float, float]:
        return self.primary.trace_width_um

    @property
    def secondary_width_um(self) -> tuple[float, float]:
        return self.secondary.trace_width_um

    @property
    def primary_spacing_um(self) -> tuple[float, float]:
        return self.primary.spacing_um

    @property
    def secondary_spacing_um(self) -> tuple[float, float]:
        return self.secondary.spacing_um

    @property
    def primary_terminal_y_span_um(self) -> tuple[float, float]:
        return self.primary.terminal_y_span_um

    @property
    def secondary_terminal_y_span_um(self) -> tuple[float, float]:
        return self.secondary.terminal_y_span_um

    @property
    def primary_feed_extension_um(self) -> tuple[float, float]:
        return self.primary.feed_extension_um

    @property
    def secondary_feed_extension_um(self) -> tuple[float, float]:
        return self.secondary.feed_extension_um

    @property
    def primary_turns(self) -> int:
        return int(self.primary.turns)

    @property
    def secondary_turns(self) -> int:
        return int(self.secondary.turns)

    @property
    def primary_center_tap(self) -> bool:
        return bool(self.primary.center_tap)

    @property
    def secondary_center_tap(self) -> bool:
        return bool(self.secondary.center_tap)

    def to_scipy_bounds(self) -> list[tuple[float, float]]:
        bounds_by_name = self.bounds_by_name()
        return [bounds_by_name[name] for name in self.optimizable_names()]

    def validate(self, spec: TransformerSpec) -> list[str]:
        errors: list[str] = []
        flat = spec.active_flat_dict()
        bounds_by_name = self.bounds_by_name()
        for name in self.names():
            if name not in flat:
                continue
            lower, upper = map(float, bounds_by_name[name])
            value = float(flat[name])
            if value < lower - 1.0e-9 or value > upper + 1.0e-9:
                errors.append(
                    f"{name} ({value:.3f}) is outside configured bounds [{lower:.3f}, {upper:.3f}]"
                )
        return errors

    def midpoint(self) -> TransformerSpec:
        midpoint_values = {
            "primary_outer_width_um": 0.5 * (self.primary.outer_width_um[0] + self.primary.outer_width_um[1]),
            "primary_outer_height_um": 0.5 * (self.primary.outer_height_um[0] + self.primary.outer_height_um[1]),
            "secondary_outer_width_um": 0.5 * (self.secondary.outer_width_um[0] + self.secondary.outer_width_um[1]),
            "secondary_outer_height_um": 0.5 * (self.secondary.outer_height_um[0] + self.secondary.outer_height_um[1]),
            "primary_width_um": 0.5 * (self.primary.trace_width_um[0] + self.primary.trace_width_um[1]),
            "secondary_width_um": 0.5 * (self.secondary.trace_width_um[0] + self.secondary.trace_width_um[1]),
            "primary_spacing_um": 0.5 * (self.primary.spacing_um[0] + self.primary.spacing_um[1]),
            "secondary_spacing_um": 0.5 * (self.secondary.spacing_um[0] + self.secondary.spacing_um[1]),
            "primary_terminal_y_span_um": 0.5 * (self.primary.terminal_y_span_um[0] + self.primary.terminal_y_span_um[1]),
            "secondary_terminal_y_span_um": 0.5 * (self.secondary.terminal_y_span_um[0] + self.secondary.terminal_y_span_um[1]),
            "offset_um": 0.5 * (self.offset_um[0] + self.offset_um[1]),
            "primary_feed_extension_um": 0.5 * (self.primary.feed_extension_um[0] + self.primary.feed_extension_um[1]),
            "secondary_feed_extension_um": 0.5 * (self.secondary.feed_extension_um[0] + self.secondary.feed_extension_um[1]),
        }
        return TransformerSpec.from_flat_dict(
            midpoint_values,
            primary_turns=self.primary.turns,
            secondary_turns=self.secondary.turns,
            primary_center_tap=self.primary.center_tap,
            secondary_center_tap=self.secondary.center_tap,
            primary_bridge_layer=self.primary.bridge_layer,
            secondary_bridge_layer=self.secondary.bridge_layer,
            primary_bridge_via_layer=self.primary.bridge_via_layer,
            secondary_bridge_via_layer=self.secondary.bridge_via_layer,
            primary_bridge_lower_layer=self.primary.bridge_lower_layer,
            secondary_bridge_lower_layer=self.secondary.bridge_lower_layer,
            primary_bridge_lower_via_layer=self.primary.bridge_lower_via_layer,
            secondary_bridge_lower_via_layer=self.secondary.bridge_lower_via_layer,
            primary_bridge_section=self.primary.bridge_section_spec(),
            secondary_bridge_section=self.secondary.bridge_section_spec(),
            primary_vdd_bar=self.primary.vdd_bar,
            secondary_vdd_bar=self.secondary.vdd_bar,
            topology_mode=self.topology_mode,
            shield=self.shield,
        )
