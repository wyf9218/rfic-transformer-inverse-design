"""Topology-specific transformer geometry models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import math

import numpy as np

from .types import (
    BridgeSectionSpec,
    InductorGeometry,
    InductorFixedSpec,
    InductorSpec,
    ShieldSpec,
    TopologyMode,
    VddBarSpec,
    _coerce_topology_mode,
    topology_mode_from_turns,
)

@dataclass(frozen=True)
class TransformerSpec:
    """Full transformer description assembled from two inductors and their relative offset."""

    primary: InductorSpec
    secondary: InductorSpec
    offset_um: float
    topology_mode: TopologyMode = "1t1t"
    shield: ShieldSpec = field(default_factory=ShieldSpec)

    VECTOR_FIELD_ORDER = (
        "primary.outer_width_um",
        "primary.outer_height_um",
        "secondary.outer_width_um",
        "secondary.outer_height_um",
        "primary.trace_width_um",
        "secondary.trace_width_um",
        "primary.spacing_um",
        "secondary.spacing_um",
        "primary.terminal_y_span_um",
        "secondary.terminal_y_span_um",
        "offset_um",
        "primary.feed_extension_um",
        "secondary.feed_extension_um",
    )

    FLAT_FIELD_ORDER = (
        "primary_outer_width_um",
        "primary_outer_height_um",
        "secondary_outer_width_um",
        "secondary_outer_height_um",
        "primary_width_um",
        "secondary_width_um",
        "primary_spacing_um",
        "secondary_spacing_um",
        "primary_terminal_y_span_um",
        "secondary_terminal_y_span_um",
        "offset_um",
        "primary_feed_extension_um",
        "secondary_feed_extension_um",
    )

    @classmethod
    def flat_field_order_for_topology(cls, *, primary_turns: int, secondary_turns: int) -> tuple[str, ...]:
        names = [
            "primary_outer_width_um",
            "primary_outer_height_um",
            "secondary_outer_width_um",
            "secondary_outer_height_um",
            "primary_width_um",
        ]
        if int(primary_turns) > 1:
            names.append("primary_spacing_um")
        names.append("secondary_width_um")
        if int(secondary_turns) > 1:
            names.append("secondary_spacing_um")
        names.extend(
            (
                "primary_terminal_y_span_um",
                "secondary_terminal_y_span_um",
                "offset_um",
                "primary_feed_extension_um",
                "secondary_feed_extension_um",
            )
        )
        return tuple(names)

    def active_flat_field_order(self) -> tuple[str, ...]:
        return self.flat_field_order_for_topology(
            primary_turns=self.primary.turns,
            secondary_turns=self.secondary.turns,
        )

    def active_flat_dict(self) -> dict[str, object]:
        flat = self.flat_dict()
        return {name: flat[name] for name in self.active_flat_field_order()}

    def to_vector(self) -> np.ndarray:
        active_flat = self.active_flat_dict()
        return np.array([float(active_flat[name]) for name in self.active_flat_field_order()], dtype=float)

    @classmethod
    def from_vector(
        cls,
        values: np.ndarray | list[float] | tuple[float, ...],
        *,
        topology_mode: TopologyMode = "1t1t",
        primary_turns: int,
        secondary_turns: int,
        primary_center_tap: bool,
        secondary_center_tap: bool,
        primary_spacing_um: float | None = None,
    secondary_spacing_um: float | None = None,
    primary_bridge_layer: int | None = None,
    secondary_bridge_layer: int | None = None,
    primary_bridge_via_layer: int | None = None,
    secondary_bridge_via_layer: int | None = None,
    primary_bridge_lower_layer: int | None = None,
    secondary_bridge_lower_layer: int | None = None,
    primary_bridge_lower_via_layer: int | None = None,
    secondary_bridge_lower_via_layer: int | None = None,
        primary_bridge_section: BridgeSectionSpec | None = None,
        secondary_bridge_section: BridgeSectionSpec | None = None,
        primary_vdd_bar: VddBarSpec | None = None,
        secondary_vdd_bar: VddBarSpec | None = None,
        shield: ShieldSpec | None = None,
    ) -> "TransformerSpec":
        arr = np.asarray(values, dtype=float)
        field_order = cls.flat_field_order_for_topology(
            primary_turns=primary_turns,
            secondary_turns=secondary_turns,
        )
        if arr.shape != (len(field_order),):
            raise ValueError(f"Expected {len(field_order)} geometry values, got shape {arr.shape}")
        values_by_name = {name: float(value) for name, value in zip(field_order, arr)}
        if "primary_spacing_um" not in values_by_name:
            if primary_spacing_um is None:
                raise ValueError("primary_spacing_um must be provided when omitted from the topology vector")
            values_by_name["primary_spacing_um"] = float(primary_spacing_um)
        if "secondary_spacing_um" not in values_by_name:
            if secondary_spacing_um is None:
                raise ValueError("secondary_spacing_um must be provided when omitted from the topology vector")
            values_by_name["secondary_spacing_um"] = float(secondary_spacing_um)
        return cls.from_flat_dict(
            values_by_name,
            topology_mode=topology_mode,
            primary_turns=primary_turns,
            secondary_turns=secondary_turns,
            primary_center_tap=primary_center_tap,
            secondary_center_tap=secondary_center_tap,
            primary_bridge_layer=primary_bridge_layer,
            secondary_bridge_layer=secondary_bridge_layer,
            primary_bridge_via_layer=primary_bridge_via_layer,
            secondary_bridge_via_layer=secondary_bridge_via_layer,
            primary_bridge_lower_layer=primary_bridge_lower_layer,
            secondary_bridge_lower_layer=secondary_bridge_lower_layer,
            primary_bridge_lower_via_layer=primary_bridge_lower_via_layer,
            secondary_bridge_lower_via_layer=secondary_bridge_lower_via_layer,
            primary_bridge_section=primary_bridge_section,
            secondary_bridge_section=secondary_bridge_section,
            primary_vdd_bar=primary_vdd_bar,
            secondary_vdd_bar=secondary_vdd_bar,
            shield=shield,
        )

    @classmethod
    def from_flat_dict(
        cls,
        values: dict[str, object],
        *,
        topology_mode: TopologyMode = "1t1t",
        primary_turns: int,
        secondary_turns: int,
        primary_center_tap: bool,
        secondary_center_tap: bool,
        primary_spacing_um: float | None = None,
    secondary_spacing_um: float | None = None,
    primary_bridge_layer: int | None = None,
    secondary_bridge_layer: int | None = None,
    primary_bridge_via_layer: int | None = None,
    secondary_bridge_via_layer: int | None = None,
    primary_bridge_lower_layer: int | None = None,
    secondary_bridge_lower_layer: int | None = None,
    primary_bridge_lower_via_layer: int | None = None,
    secondary_bridge_lower_via_layer: int | None = None,
        primary_bridge_section: BridgeSectionSpec | None = None,
        secondary_bridge_section: BridgeSectionSpec | None = None,
        primary_vdd_bar: VddBarSpec | None = None,
        secondary_vdd_bar: VddBarSpec | None = None,
        shield: ShieldSpec | None = None,
    ) -> "TransformerSpec":
        resolved_topology_mode = _coerce_topology_mode(topology_mode)
        use_interweaved_bridge_constraint = resolved_topology_mode != "1t1t"
        resolved_values = dict(values)
        shared_outer_width_um = resolved_values.get("outer_width_um")
        shared_outer_height_um = resolved_values.get("outer_height_um")
        if "primary_outer_width_um" not in resolved_values and shared_outer_width_um is not None:
            resolved_values["primary_outer_width_um"] = float(shared_outer_width_um)
        if "secondary_outer_width_um" not in resolved_values and shared_outer_width_um is not None:
            resolved_values["secondary_outer_width_um"] = float(shared_outer_width_um)
        if "primary_outer_height_um" not in resolved_values and shared_outer_height_um is not None:
            resolved_values["primary_outer_height_um"] = float(shared_outer_height_um)
        if "secondary_outer_height_um" not in resolved_values and shared_outer_height_um is not None:
            resolved_values["secondary_outer_height_um"] = float(shared_outer_height_um)
        if "primary_spacing_um" not in resolved_values:
            if primary_spacing_um is None:
                raise ValueError("primary_spacing_um must be provided when omitted from the topology dictionary")
            resolved_values["primary_spacing_um"] = float(primary_spacing_um)
        if "secondary_spacing_um" not in resolved_values:
            if secondary_spacing_um is None:
                raise ValueError("secondary_spacing_um must be provided when omitted from the topology dictionary")
            resolved_values["secondary_spacing_um"] = float(secondary_spacing_um)
        return cls(
            primary=InductorSpec(
                geometry=InductorGeometry(
                    outer_width_um=float(resolved_values["primary_outer_width_um"]),
                    outer_height_um=float(resolved_values["primary_outer_height_um"]),
                    trace_width_um=float(resolved_values["primary_width_um"]),
                    spacing_um=float(resolved_values["primary_spacing_um"]),
                    terminal_y_span_um=float(resolved_values["primary_terminal_y_span_um"]),
                    feed_extension_um=float(resolved_values["primary_feed_extension_um"]),
                ),
                fixed=InductorFixedSpec(
                    turns=int(primary_turns),
                    center_tap=bool(primary_center_tap),
                    bridge_layer=primary_bridge_layer,
                    bridge_via_layer=primary_bridge_via_layer,
                    bridge_lower_layer=primary_bridge_lower_layer,
                    bridge_lower_via_layer=primary_bridge_lower_via_layer,
                    vdd_bar=primary_vdd_bar,
                    bridge_section=(
                        None
                        if int(primary_turns) <= 1
                        else (
                            BridgeSectionSpec()
                            if use_interweaved_bridge_constraint and primary_bridge_section is None
                            else primary_bridge_section
                        )
                    ),
                ),
            ),
            secondary=InductorSpec(
                geometry=InductorGeometry(
                    outer_width_um=float(resolved_values["secondary_outer_width_um"]),
                    outer_height_um=float(resolved_values["secondary_outer_height_um"]),
                    trace_width_um=float(resolved_values["secondary_width_um"]),
                    spacing_um=float(resolved_values["secondary_spacing_um"]),
                    terminal_y_span_um=float(resolved_values["secondary_terminal_y_span_um"]),
                    feed_extension_um=float(resolved_values["secondary_feed_extension_um"]),
                ),
                fixed=InductorFixedSpec(
                    turns=int(secondary_turns),
                    center_tap=bool(secondary_center_tap),
                    bridge_layer=secondary_bridge_layer,
                    bridge_via_layer=secondary_bridge_via_layer,
                    bridge_lower_layer=secondary_bridge_lower_layer,
                    bridge_lower_via_layer=secondary_bridge_lower_via_layer,
                    vdd_bar=secondary_vdd_bar,
                    bridge_section=(
                        None
                        if int(secondary_turns) <= 1
                        else (
                            BridgeSectionSpec()
                            if use_interweaved_bridge_constraint and secondary_bridge_section is None
                            else secondary_bridge_section
                        )
                    ),
                ),
            ),
            offset_um=float(resolved_values["offset_um"]),
            topology_mode=resolved_topology_mode,
            shield=ShieldSpec() if shield is None else shield,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary.as_dict(),
            "secondary": self.secondary.as_dict(),
            "offset_um": float(self.offset_um),
            "topology_mode": self.topology_mode,
            "shield": self.shield.as_dict(),
        }

    def flat_dict(self) -> dict[str, object]:
        return {
            "primary_outer_width_um": float(self.primary.outer_width_um),
            "primary_outer_height_um": float(self.primary.outer_height_um),
            "secondary_outer_width_um": float(self.secondary.outer_width_um),
            "secondary_outer_height_um": float(self.secondary.outer_height_um),
            "primary_width_um": float(self.primary.trace_width_um),
            "secondary_width_um": float(self.secondary.trace_width_um),
            "line_width_um": (
                float(self.primary.trace_width_um)
                if abs(float(self.primary.trace_width_um) - float(self.secondary.trace_width_um)) <= 1.0e-12
                else None
            ),
            "primary_spacing_um": float(self.primary.spacing_um),
            "secondary_spacing_um": float(self.secondary.spacing_um),
            "primary_terminal_y_span_um": float(self.primary.terminal_y_span_um),
            "secondary_terminal_y_span_um": float(self.secondary.terminal_y_span_um),
            "offset_um": float(self.offset_um),
            "primary_feed_extension_um": float(self.primary.feed_extension_um),
            "secondary_feed_extension_um": float(self.secondary.feed_extension_um),
            "primary_turns": int(self.primary.turns),
            "secondary_turns": int(self.secondary.turns),
            "primary_center_tap": bool(self.primary.center_tap),
            "secondary_center_tap": bool(self.secondary.center_tap),
            "primary_bridge_layer": None if self.primary.bridge_layer is None else int(self.primary.bridge_layer),
            "secondary_bridge_layer": None if self.secondary.bridge_layer is None else int(self.secondary.bridge_layer),
            "primary_bridge_via_layer": None if self.primary.bridge_via_layer is None else int(self.primary.bridge_via_layer),
            "secondary_bridge_via_layer": None if self.secondary.bridge_via_layer is None else int(self.secondary.bridge_via_layer),
            "primary_bridge_lower_layer": (
                None if self.primary.bridge_lower_layer is None else int(self.primary.bridge_lower_layer)
            ),
            "secondary_bridge_lower_layer": (
                None if self.secondary.bridge_lower_layer is None else int(self.secondary.bridge_lower_layer)
            ),
            "primary_bridge_lower_via_layer": (
                None if self.primary.bridge_lower_via_layer is None else int(self.primary.bridge_lower_via_layer)
            ),
            "secondary_bridge_lower_via_layer": (
                None if self.secondary.bridge_lower_via_layer is None else int(self.secondary.bridge_lower_via_layer)
            ),
            "primary_bridge_section_pad_width_ratio": (
                None
                if self.primary.bridge_section is None
                else float(self.primary.bridge_section.pad_width_ratio)
            ),
            "primary_bridge_section_pad_height_ratio": (
                None
                if self.primary.bridge_section is None
                else float(self.primary.bridge_section.pad_height_ratio)
            ),
            "primary_bridge_section_via_size_ratio": (
                None
                if self.primary.bridge_section is None
                else float(self.primary.bridge_section.via_size_ratio)
            ),
            "primary_bridge_section_via_width_ratio": (
                None
                if self.primary.bridge_section is None
                else float(self.primary.bridge_section.via_width_ratio)
            ),
            "primary_bridge_section_via_spacing_ratio": (
                None
                if self.primary.bridge_section is None
                else float(self.primary.bridge_section.via_spacing_ratio)
            ),
            "primary_vdd_bar_enabled": bool(self.primary.vdd_bar.enabled) if self.primary.vdd_bar is not None else False,
            "primary_vdd_bar_width_um": (
                None if self.primary.vdd_bar is None or self.primary.vdd_bar.width_um is None else float(self.primary.vdd_bar.width_um)
            ),
            "primary_vdd_bar_offset_um": (
                None if self.primary.vdd_bar is None else float(self.primary.vdd_bar.offset_um)
            ),
            "primary_vdd_bar_layer": (
                None if self.primary.vdd_bar is None or self.primary.vdd_bar.bar_layer is None else int(self.primary.vdd_bar.bar_layer)
            ),
            "secondary_bridge_section_pad_width_ratio": (
                None
                if self.secondary.bridge_section is None
                else float(self.secondary.bridge_section.pad_width_ratio)
            ),
            "secondary_bridge_section_pad_height_ratio": (
                None
                if self.secondary.bridge_section is None
                else float(self.secondary.bridge_section.pad_height_ratio)
            ),
            "secondary_bridge_section_via_size_ratio": (
                None
                if self.secondary.bridge_section is None
                else float(self.secondary.bridge_section.via_size_ratio)
            ),
            "secondary_bridge_section_via_width_ratio": (
                None
                if self.secondary.bridge_section is None
                else float(self.secondary.bridge_section.via_width_ratio)
            ),
            "secondary_bridge_section_via_spacing_ratio": (
                None
                if self.secondary.bridge_section is None
                else float(self.secondary.bridge_section.via_spacing_ratio)
            ),
            "secondary_vdd_bar_enabled": bool(self.secondary.vdd_bar.enabled) if self.secondary.vdd_bar is not None else False,
            "secondary_vdd_bar_width_um": (
                None if self.secondary.vdd_bar is None or self.secondary.vdd_bar.width_um is None else float(self.secondary.vdd_bar.width_um)
            ),
            "secondary_vdd_bar_offset_um": (
                None if self.secondary.vdd_bar is None else float(self.secondary.vdd_bar.offset_um)
            ),
            "secondary_vdd_bar_layer": (
                None if self.secondary.vdd_bar is None or self.secondary.vdd_bar.bar_layer is None else int(self.secondary.vdd_bar.bar_layer)
            ),
            "shield_enabled": bool(self.shield.enabled),
            "shield_kind": str(self.shield.kind),
            "shield_margin_um": None if self.shield.margin_um is None else float(self.shield.margin_um),
            "shield_width_um": None if self.shield.width_um is None else float(self.shield.width_um),
        }

    @property
    def outer_width_um(self) -> float:
        return float(self.primary.outer_width_um)

    @property
    def outer_height_um(self) -> float:
        return float(self.primary.outer_height_um)

    @property
    def primary_width_um(self) -> float:
        return float(self.primary.trace_width_um)

    @property
    def secondary_width_um(self) -> float:
        return float(self.secondary.trace_width_um)

    @property
    def primary_spacing_um(self) -> float:
        return float(self.primary.spacing_um)

    @property
    def secondary_spacing_um(self) -> float:
        return float(self.secondary.spacing_um)

    @property
    def primary_terminal_y_span_um(self) -> float:
        return float(self.primary.terminal_y_span_um)

    @property
    def secondary_terminal_y_span_um(self) -> float:
        return float(self.secondary.terminal_y_span_um)

    @property
    def primary_feed_extension_um(self) -> float:
        return float(self.primary.feed_extension_um)

    @property
    def secondary_feed_extension_um(self) -> float:
        return float(self.secondary.feed_extension_um)

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

    def primary_inductor_spec(self) -> InductorSpec:
        return self.primary

    def secondary_inductor_spec(self) -> InductorSpec:
        return self.secondary

    def transformer_spec(self) -> "TransformerSpec":
        return self

    def with_shared_line_width(self, line_width_um: float) -> "TransformerSpec":
        """Return a copy whose M9/M10 coils and VDD bars use one synchronized line width."""

        width = float(line_width_um)
        if not math.isfinite(width) or width <= 0.0:
            raise ValueError(f"line_width_um must be positive and finite, got {line_width_um!r}")

        def _sync(inductor: InductorSpec) -> InductorSpec:
            vdd_bar = inductor.vdd_bar
            fixed = inductor.fixed
            if vdd_bar is not None:
                fixed = replace(fixed, vdd_bar=replace(vdd_bar, width_um=width))
            return replace(
                inductor,
                geometry=replace(inductor.geometry, trace_width_um=width),
                fixed=fixed,
            )

        return replace(
            self,
            primary=_sync(self.primary),
            secondary=_sync(self.secondary),
        )

    def with_topology(
        self,
        *,
        primary_turns: int | None = None,
        secondary_turns: int | None = None,
        primary_center_tap: bool | None = None,
        secondary_center_tap: bool | None = None,
    ) -> "TransformerSpec":
        resolved_primary_turns = int(self.primary.turns if primary_turns is None else primary_turns)
        resolved_secondary_turns = int(self.secondary.turns if secondary_turns is None else secondary_turns)
        resolved_topology_mode = topology_mode_from_turns(resolved_primary_turns, resolved_secondary_turns)
        use_interweaved_bridge_constraint = resolved_topology_mode != "1t1t"
        return TransformerSpec(
            primary=InductorSpec(
                geometry=self.primary.geometry,
                fixed=InductorFixedSpec(
                    turns=resolved_primary_turns,
                    center_tap=bool(self.primary.center_tap if primary_center_tap is None else primary_center_tap),
                    bridge_layer=self.primary.bridge_layer,
                    bridge_via_layer=self.primary.bridge_via_layer,
                    bridge_lower_layer=self.primary.bridge_lower_layer,
                    bridge_lower_via_layer=self.primary.bridge_lower_via_layer,
                    vdd_bar=self.primary.vdd_bar,
                    bridge_section=(
                        None
                        if resolved_primary_turns <= 1
                        else (
                            self.primary.bridge_section
                            if self.primary.bridge_section is not None or not use_interweaved_bridge_constraint
                            else BridgeSectionSpec()
                        )
                    ),
                ),
            ),
            secondary=InductorSpec(
                geometry=self.secondary.geometry,
                fixed=InductorFixedSpec(
                    turns=resolved_secondary_turns,
                    center_tap=bool(self.secondary.center_tap if secondary_center_tap is None else secondary_center_tap),
                    bridge_layer=self.secondary.bridge_layer,
                    bridge_via_layer=self.secondary.bridge_via_layer,
                    bridge_lower_layer=self.secondary.bridge_lower_layer,
                    bridge_lower_via_layer=self.secondary.bridge_lower_via_layer,
                    vdd_bar=self.secondary.vdd_bar,
                    bridge_section=(
                        None
                        if resolved_secondary_turns <= 1
                        else (
                            self.secondary.bridge_section
                            if self.secondary.bridge_section is not None or not use_interweaved_bridge_constraint
                            else BridgeSectionSpec()
                        )
                    ),
                ),
            ),
            offset_um=self.offset_um,
            topology_mode=resolved_topology_mode,
            shield=self.shield,
        )

    @staticmethod
    def _min_spacing_for_crossover(width_um: float) -> float:
        return max(2.0, 0.35 * width_um)

    @staticmethod
    def _max_width_for_outline(outer_width_um: float, outer_height_um: float) -> float:
        return 0.18 * min(outer_width_um, outer_height_um)

    @staticmethod
    def _min_terminal_span(trace_width_um: float, center_tap: bool) -> float:
        multiplier = 2.0 if center_tap else 1.0
        return multiplier * trace_width_um

    @staticmethod
    def _max_terminal_span_for_feed_side(outer_width_um: float, outer_height_um: float) -> float:
        """Maximum terminal span that fits on the straight feed-side wall."""
        half_width_um = 0.5 * float(outer_width_um)
        half_height_um = 0.5 * float(outer_height_um)
        chamfer_um = min(half_width_um, half_height_um) * (math.sqrt(2.0) - 1.0)
        return 2.0 * (half_height_um - chamfer_um)

    @staticmethod
    def _feed_clear_opening(terminal_y_span_um: float, trace_width_um: float) -> float:
        return terminal_y_span_um - trace_width_um

    @staticmethod
    def _default_bridge_anchor_gap_height(trace_width_um: float) -> float:
        return 1.2 * trace_width_um

    @staticmethod
    def _min_interweaved_bridge_anchor_gap_height(trace_width_um: float) -> float:
        return 0.9 * trace_width_um

    def _primary_uses_bridge_section(self) -> bool:
        return self.primary.uses_bridge_section()

    def _resolved_primary_bridge_anchor_gap_height(self) -> float:
        base_gap_height = self._default_bridge_anchor_gap_height(self.primary.trace_width_um)
        if not self._primary_uses_bridge_section():
            return base_gap_height
        secondary_opening = self._feed_clear_opening(
            self.secondary.terminal_y_span_um,
            self.secondary.trace_width_um,
        )
        return max(1.0e-6, min(base_gap_height, secondary_opening - 1.0e-6))

    @staticmethod
    def _primary_bridge_anchor_intervals(
        trace_width_um: float,
        spacing_um: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        connector_len_um = 1.05 * trace_width_um
        connector_overlap_um = 0.25 * trace_width_um
        bridge_outer_y_um = 0.5 * trace_width_um
        bridge_inner_y_um = -0.5 * trace_width_um - spacing_um
        upper_interval = (
            bridge_outer_y_um - connector_overlap_um,
            bridge_outer_y_um + connector_len_um + connector_overlap_um,
        )
        lower_interval = (
            bridge_inner_y_um - connector_len_um - connector_overlap_um,
            bridge_inner_y_um + connector_overlap_um,
        )
        return upper_interval, lower_interval

    @staticmethod
    def _secondary_terminal_window_intervals(
        terminal_y_span_um: float,
        trace_width_um: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        half_span_um = 0.5 * terminal_y_span_um
        half_width_um = 0.5 * trace_width_um
        upper_window = (half_width_um, half_span_um - half_width_um)
        lower_window = (-half_span_um + half_width_um, -half_width_um)
        return upper_window, lower_window

    def _required_primary_width_for_upper_terminal_window(self) -> float:
        return 2.0 * self.secondary.trace_width_um

    def _required_primary_spacing_for_lower_terminal_window(self, primary_width_um: float) -> float:
        return max(0.0, 0.5 * self.secondary.trace_width_um - 0.25 * primary_width_um)

    def _required_secondary_terminal_span_for_interweaved(self) -> float:
        upper_anchor, lower_anchor = self._primary_bridge_anchor_intervals(
            self.primary.trace_width_um,
            self.primary.spacing_um,
        )
        secondary_width_um = self.secondary.trace_width_um
        y_margin_um = self._interweaved_feed_y_margin_um(self.primary.trace_width_um)
        return max(
            2.0 * (upper_anchor[1] + y_margin_um + 0.5 * secondary_width_um),
            2.0 * (0.5 * secondary_width_um + y_margin_um - lower_anchor[0]),
        )

    def _interweaved_feed_x_margin_um(self, primary_width_um: float) -> float:
        if self.primary.bridge_section is None:
            return 0.0
        return self.primary.bridge_section.containment_margin_ratio * primary_width_um

    def _interweaved_feed_y_margin_um(self, primary_width_um: float) -> float:
        _ = primary_width_um
        return 0.0

    def _offset_feed_support_bounds(self) -> tuple[float, float]:
        return (-float(self.primary.feed_extension_um), float(self.secondary.feed_extension_um))

    def _interweaved_offset_x_bounds(self) -> tuple[float, float]:
        x_margin_um = self._interweaved_feed_x_margin_um(self.primary.trace_width_um)
        primary_outer_anchor_x, primary_inner_anchor_x = self._primary_bridge_anchor_x_intervals()
        secondary_outer_half_width_um = 0.5 * self.secondary.outer_width_um
        secondary_inner_feed_start_base_um = (
            secondary_outer_half_width_um - self.secondary.trace_width_um - self.secondary.spacing_um
        )
        secondary_feed_end_base_um = secondary_outer_half_width_um + self.secondary.feed_extension_um
        lower_bound_um = max(
            primary_outer_anchor_x[1] + x_margin_um - secondary_feed_end_base_um,
            primary_inner_anchor_x[1] + x_margin_um - secondary_feed_end_base_um,
        )
        upper_bound_um = min(
            primary_outer_anchor_x[0] - secondary_outer_half_width_um - x_margin_um,
            primary_inner_anchor_x[0] - secondary_inner_feed_start_base_um - x_margin_um,
        )
        feed_support_min_um, feed_support_max_um = self._offset_feed_support_bounds()
        return (max(lower_bound_um, feed_support_min_um), min(upper_bound_um, feed_support_max_um))

    def _required_secondary_feed_extension_for_interweaved(self, offset_um: float) -> float:
        x_margin_um = self._interweaved_feed_x_margin_um(self.primary.trace_width_um)
        primary_outer_anchor_x, primary_inner_anchor_x = self._primary_bridge_anchor_x_intervals()
        secondary_outer_half_width_um = 0.5 * self.secondary.outer_width_um
        return max(
            0.0,
            primary_outer_anchor_x[1] + x_margin_um - (offset_um + secondary_outer_half_width_um),
            primary_inner_anchor_x[1] + x_margin_um - (offset_um + secondary_outer_half_width_um),
        )

    def _primary_bridge_anchor_x_intervals(self) -> tuple[tuple[float, float], tuple[float, float]]:
        outer_half_width_um = 0.5 * self.primary.outer_width_um
        primary_width_um = self.primary.trace_width_um
        primary_spacing_um = self.primary.spacing_um
        outer_interval = (
            outer_half_width_um - primary_width_um,
            outer_half_width_um,
        )
        inner_interval = (
            outer_half_width_um - 2.0 * primary_width_um - primary_spacing_um,
            outer_half_width_um - primary_width_um - primary_spacing_um,
        )
        return outer_interval, inner_interval

    def _secondary_feed_section_x_intervals(self) -> tuple[tuple[float, float], tuple[float, float]]:
        outer_half_width_um = 0.5 * self.secondary.outer_width_um
        secondary_width_um = self.secondary.trace_width_um
        secondary_spacing_um = self.secondary.spacing_um
        feed_extension_um = self.secondary.feed_extension_um
        outer_interval = (
            self.offset_um + outer_half_width_um,
            self.offset_um + outer_half_width_um + feed_extension_um,
        )
        inner_interval = (
            self.offset_um + outer_half_width_um - secondary_width_um - secondary_spacing_um,
            self.offset_um + outer_half_width_um + feed_extension_um,
        )
        return outer_interval, inner_interval

    def constraint_report(self) -> dict[str, float]:
        p_pitch = self.primary.trace_width_um + self.primary.spacing_um
        s_pitch = self.secondary.trace_width_um + self.secondary.spacing_um
        p_outer_half_width = self.primary.outer_width_um * 0.5
        p_outer_half_height = self.primary.outer_height_um * 0.5
        s_outer_half_width = self.secondary.outer_width_um * 0.5
        s_outer_half_height = self.secondary.outer_height_um * 0.5
        p_inner_half_width = p_outer_half_width - max(0, self.primary.turns - 1) * p_pitch - self.primary.trace_width_um
        p_inner_half_height = (
            p_outer_half_height - max(0, self.primary.turns - 1) * p_pitch - self.primary.trace_width_um
        )
        s_inner_half_width = (
            s_outer_half_width - max(0, self.secondary.turns - 1) * s_pitch - self.secondary.trace_width_um
        )
        s_inner_half_height = (
            s_outer_half_height - max(0, self.secondary.turns - 1) * s_pitch - self.secondary.trace_width_um
        )
        secondary_feed_clear_opening_um = self._feed_clear_opening(
            self.secondary.terminal_y_span_um,
            self.secondary.trace_width_um,
        )
        primary_upper_anchor_interval, primary_lower_anchor_interval = self._primary_bridge_anchor_intervals(
            self.primary.trace_width_um,
            self.primary.spacing_um,
        )
        secondary_upper_window, secondary_lower_window = self._secondary_terminal_window_intervals(
            self.secondary.terminal_y_span_um,
            self.secondary.trace_width_um,
        )
        primary_outer_anchor_x, primary_inner_anchor_x = self._primary_bridge_anchor_x_intervals()
        secondary_outer_feed_x, secondary_inner_feed_x = self._secondary_feed_section_x_intervals()
        interweaved_feed_x_margin_um = self._interweaved_feed_x_margin_um(self.primary.trace_width_um)
        interweaved_feed_y_margin_um = self._interweaved_feed_y_margin_um(self.primary.trace_width_um)
        return {
            "primary_turns": float(self.primary.turns),
            "secondary_turns": float(self.secondary.turns),
            "primary_pitch_um": float(p_pitch),
            "secondary_pitch_um": float(s_pitch),
            "primary_min_spacing_um": float(self._min_spacing_for_crossover(self.primary.trace_width_um)),
            "secondary_min_spacing_um": float(self._min_spacing_for_crossover(self.secondary.trace_width_um)),
            "primary_min_terminal_span_um": float(
                self._min_terminal_span(self.primary.trace_width_um, self.primary.center_tap)
            ),
            "secondary_min_terminal_span_um": float(
                self._min_terminal_span(self.secondary.trace_width_um, self.secondary.center_tap)
            ),
            "primary_max_terminal_span_um": float(
                self._max_terminal_span_for_feed_side(self.primary.outer_width_um, self.primary.outer_height_um)
            ),
            "secondary_max_terminal_span_um": float(
                self._max_terminal_span_for_feed_side(self.secondary.outer_width_um, self.secondary.outer_height_um)
            ),
            "primary_max_trace_width_um": float(
                self._max_width_for_outline(self.primary.outer_width_um, self.primary.outer_height_um)
            ),
            "secondary_max_trace_width_um": float(
                self._max_width_for_outline(self.secondary.outer_width_um, self.secondary.outer_height_um)
            ),
            "primary_inner_width_um": float(2.0 * p_inner_half_width),
            "primary_inner_height_um": float(2.0 * p_inner_half_height),
            "secondary_inner_width_um": float(2.0 * s_inner_half_width),
            "secondary_inner_height_um": float(2.0 * s_inner_half_height),
            "primary_bridge_offset_y_um": float(-0.5 * self.primary.spacing_um),
            "secondary_bridge_offset_y_um": float(-0.5 * self.secondary.spacing_um),
            "primary_bridge_anchor_gap_height_um": float(self._resolved_primary_bridge_anchor_gap_height()),
            "secondary_feed_clear_opening_um": float(secondary_feed_clear_opening_um),
            "primary_required_width_for_secondary_window_um": float(
                self._required_primary_width_for_upper_terminal_window()
            ),
            "primary_required_spacing_for_secondary_window_um": float(
                self._required_primary_spacing_for_lower_terminal_window(self.primary.trace_width_um)
            ),
            "required_secondary_terminal_span_for_interweaved_um": float(
                self._required_secondary_terminal_span_for_interweaved()
            ),
            "primary_upper_anchor_y0_um": float(primary_upper_anchor_interval[0]),
            "primary_upper_anchor_y1_um": float(primary_upper_anchor_interval[1]),
            "primary_lower_anchor_y0_um": float(primary_lower_anchor_interval[0]),
            "primary_lower_anchor_y1_um": float(primary_lower_anchor_interval[1]),
            "secondary_upper_window_y0_um": float(secondary_upper_window[0]),
            "secondary_upper_window_y1_um": float(secondary_upper_window[1]),
            "secondary_lower_window_y0_um": float(secondary_lower_window[0]),
            "secondary_lower_window_y1_um": float(secondary_lower_window[1]),
            "primary_outer_anchor_x0_um": float(primary_outer_anchor_x[0]),
            "primary_outer_anchor_x1_um": float(primary_outer_anchor_x[1]),
            "primary_inner_anchor_x0_um": float(primary_inner_anchor_x[0]),
            "primary_inner_anchor_x1_um": float(primary_inner_anchor_x[1]),
            "secondary_outer_feed_x0_um": float(secondary_outer_feed_x[0]),
            "secondary_outer_feed_x1_um": float(secondary_outer_feed_x[1]),
            "secondary_inner_feed_x0_um": float(secondary_inner_feed_x[0]),
            "secondary_inner_feed_x1_um": float(secondary_inner_feed_x[1]),
            "interweaved_feed_x_margin_um": float(interweaved_feed_x_margin_um),
            "interweaved_feed_y_margin_um": float(interweaved_feed_y_margin_um),
            "offset_feed_support_min_um": float(self._offset_feed_support_bounds()[0]),
            "offset_feed_support_max_um": float(self._offset_feed_support_bounds()[1]),
            "interweaved_offset_x_min_um": float(self._interweaved_offset_x_bounds()[0]),
            "interweaved_offset_x_max_um": float(self._interweaved_offset_x_bounds()[1]),
            "required_secondary_feed_extension_for_interweaved_um": float(
                self._required_secondary_feed_extension_for_interweaved(self.offset_um)
            ),
        }

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.primary.terminal_y_span_um > self.primary.outer_width_um + 1.0e-9:
            errors.append(
                "primary terminal_y_span_um "
                f"({self.primary.terminal_y_span_um:.3f}) exceeds primary outer_width_um "
                f"({self.primary.outer_width_um:.3f})"
            )
        if self.primary.terminal_y_span_um > self.primary.outer_height_um + 1.0e-9:
            errors.append(
                "primary terminal_y_span_um "
                f"({self.primary.terminal_y_span_um:.3f}) exceeds primary outer_height_um "
                f"({self.primary.outer_height_um:.3f})"
            )
        if self.secondary.terminal_y_span_um > self.secondary.outer_width_um + 1.0e-9:
            errors.append(
                "secondary terminal_y_span_um "
                f"({self.secondary.terminal_y_span_um:.3f}) exceeds secondary outer_width_um "
                f"({self.secondary.outer_width_um:.3f})"
            )
        if self.secondary.terminal_y_span_um > self.secondary.outer_height_um + 1.0e-9:
            errors.append(
                "secondary terminal_y_span_um "
                f"({self.secondary.terminal_y_span_um:.3f}) exceeds secondary outer_height_um "
                f"({self.secondary.outer_height_um:.3f})"
            )
        primary_max_terminal_span_um = self._max_terminal_span_for_feed_side(
            self.primary.outer_width_um,
            self.primary.outer_height_um,
        )
        secondary_max_terminal_span_um = self._max_terminal_span_for_feed_side(
            self.secondary.outer_width_um,
            self.secondary.outer_height_um,
        )
        if self.primary.terminal_y_span_um > primary_max_terminal_span_um + 1.0e-9:
            errors.append(
                "primary terminal_y_span_um "
                f"({self.primary.terminal_y_span_um:.3f}) exceeds the feed-side straight-section limit "
                f"({primary_max_terminal_span_um:.3f})"
            )
        if self.secondary.terminal_y_span_um > secondary_max_terminal_span_um + 1.0e-9:
            errors.append(
                "secondary terminal_y_span_um "
                f"({self.secondary.terminal_y_span_um:.3f}) exceeds the feed-side straight-section limit "
                f"({secondary_max_terminal_span_um:.3f})"
            )
        offset_feed_support_min_um, offset_feed_support_max_um = self._offset_feed_support_bounds()
        if self.offset_um < offset_feed_support_min_um - 1.0e-9:
            errors.append(
                "offset_um "
                f"({self.offset_um:.3f}) exceeds the primary-side feed support "
                f"({offset_feed_support_min_um:.3f})"
            )
        if self.offset_um > offset_feed_support_max_um + 1.0e-9:
            errors.append(
                "offset_um "
                f"({self.offset_um:.3f}) exceeds the secondary-side feed support "
                f"({offset_feed_support_max_um:.3f})"
            )
        return errors

def default_topology_fields(topology_mode: TopologyMode) -> dict[str, object]:
    if topology_mode == "1t2t":
        return {
            "primary_turns": 1,
            "secondary_turns": 2,
            "primary_center_tap": False,
            "secondary_center_tap": True,
        }
    if topology_mode == "2t1t":
        return {
            "primary_turns": 2,
            "secondary_turns": 1,
            "primary_center_tap": True,
            "secondary_center_tap": False,
        }
    if topology_mode == "2t2t":
        return {
            "primary_turns": 2,
            "secondary_turns": 2,
            "primary_center_tap": True,
            "secondary_center_tap": True,
        }
    return {
        "primary_turns": 1,
        "secondary_turns": 1,
        "primary_center_tap": False,
        "secondary_center_tap": False,
    }
