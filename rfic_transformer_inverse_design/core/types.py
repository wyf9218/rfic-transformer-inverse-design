"""Typed configuration and result models for transformer optimization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from ..paths import default_proc_path
from ..sim.base import SParameterResult


TopologyMode = Literal["1t1t", "1t2t", "2t1t", "2t2t"]
QTargetMode = Literal["max", "target"]
TransformerEmxPortMode = Literal[
    "single_ended_floating",
    "single_ended_shield_grounded",
    "differential_pairs",
]
TransformerEmxExecutionMode = Literal["local", "remote_ssh"]
ViaFamilyName = Literal["VIAx", "VIAy", "VIAz", "VIAr"]
PowerLineTouchstoneMode = Literal["all_ports_s8p", "signal_4_grounded_aux"]


@dataclass(frozen=True)
class BridgeSectionSpec:
    """Local bridge/crossover behavior attached to a single inductor."""

    containment_margin_ratio: float = 0.10
    pad_width_ratio: float = 0.70
    pad_height_ratio: float = 1.00
    via_size_ratio: float = 0.60
    via_width_ratio: float = 0.35
    via_spacing_ratio: float = 0.50

    def as_dict(self) -> dict[str, object]:
        return {
            "pad_width_ratio": float(self.pad_width_ratio),
            "pad_height_ratio": float(self.pad_height_ratio),
            "via_size_ratio": float(self.via_size_ratio),
            "via_width_ratio": float(self.via_width_ratio),
            "via_spacing_ratio": float(self.via_spacing_ratio),
        }


@dataclass(frozen=True)
class BridgeSectionConfig:
    """Fixed bridge/crossover behavior attached to one inductor."""

    containment_margin_ratio: float = 0.10
    pad_width_ratio: float = 0.70
    pad_height_ratio: float = 1.00
    via_size_ratio: float = 0.60
    via_width_ratio: float = 0.35
    via_spacing_ratio: float = 0.50

    def spec(self) -> BridgeSectionSpec:
        return BridgeSectionSpec(
            containment_margin_ratio=float(self.containment_margin_ratio),
            pad_width_ratio=float(self.pad_width_ratio),
            pad_height_ratio=float(self.pad_height_ratio),
            via_size_ratio=float(self.via_size_ratio),
            via_width_ratio=float(self.via_width_ratio),
            via_spacing_ratio=float(self.via_spacing_ratio),
        )


@dataclass(frozen=True)
class BridgeSectionBounds(BridgeSectionConfig):
    """Backward-compatible alias for fixed bridge/crossover settings."""


@dataclass(frozen=True)
class InductorGeometry:
    """Continuous geometry for one inductor inside the transformer."""

    outer_width_um: float
    outer_height_um: float
    trace_width_um: float
    spacing_um: float
    terminal_y_span_um: float
    feed_extension_um: float

    def active_flat_field_names(self, prefix: str, *, turns: int) -> tuple[str, ...]:
        names = [f"{prefix}_width_um"]
        if int(turns) > 1:
            names.append(f"{prefix}_spacing_um")
        names.extend(
            (
                f"{prefix}_terminal_y_span_um",
                f"{prefix}_feed_extension_um",
            )
        )
        return tuple(names)

    def as_dict(self) -> dict[str, object]:
        return {
            "outer_width_um": float(self.outer_width_um),
            "outer_height_um": float(self.outer_height_um),
            "trace_width_um": float(self.trace_width_um),
            "spacing_um": float(self.spacing_um),
            "terminal_y_span_um": float(self.terminal_y_span_um),
            "feed_extension_um": float(self.feed_extension_um),
        }


@dataclass(frozen=True)
class VddBarSpec:
    """Fixed VDD bar routing attached to one center-tapped inductor."""

    enabled: bool = False
    width_um: float | None = None
    offset_um: float = 0.0
    bar_layer: int | None = None
    route_layer: int | None = None
    route_via_layer: int | None = None
    bar_via_layer: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "width_um": None if self.width_um is None else float(self.width_um),
            "offset_um": float(self.offset_um),
            "bar_layer": None if self.bar_layer is None else int(self.bar_layer),
            "route_layer": None if self.route_layer is None else int(self.route_layer),
            "route_via_layer": None if self.route_via_layer is None else int(self.route_via_layer),
            "bar_via_layer": None if self.bar_via_layer is None else int(self.bar_via_layer),
        }


@dataclass(frozen=True)
class PowerLine8PortSpec:
    """Explicit topology intent for the vertical power-line structure."""

    enabled: bool = False
    touchstone_mode: PowerLineTouchstoneMode = "all_ports_s8p"
    bridge_width_um: float | None = None
    vertical_length_diameter_ratio: float = 1.5
    bridge_y_policy: Literal["center"] = "center"
    bridge_motion_axis: Literal["x_only"] = "x_only"
    port_ground_reference: Literal["shield"] = "shield"
    port_map: tuple[str, ...] = tuple()
    role_labels: tuple[tuple[str, str], ...] = tuple()

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "touchstone_mode": str(self.touchstone_mode),
            "bridge_width_um": None if self.bridge_width_um is None else float(self.bridge_width_um),
            "vertical_length_diameter_ratio": float(self.vertical_length_diameter_ratio),
            "bridge_y_policy": str(self.bridge_y_policy),
            "bridge_motion_axis": str(self.bridge_motion_axis),
            "port_ground_reference": str(self.port_ground_reference),
            "port_map": list(self.port_map),
            "role_labels": dict(self.role_labels),
        }


@dataclass(frozen=True)
class FoundryLayoutSpec:
    """Optional manufacturability corrections for foundry-bound closure layouts."""

    enabled: bool = False
    manufacturing_grid_um: float = 0.005
    power_line_stitch_pad_depth_um: float = 6.0
    shield_strap_width_um: float = 10.0
    shield_strap_pitch_um: float = 20.0

    def __post_init__(self) -> None:
        if float(self.manufacturing_grid_um) <= 0.0:
            raise ValueError("manufacturing_grid_um must be positive")
        if float(self.power_line_stitch_pad_depth_um) <= 0.0:
            raise ValueError("power_line_stitch_pad_depth_um must be positive")
        if float(self.shield_strap_width_um) <= 0.0:
            raise ValueError("shield_strap_width_um must be positive")
        if float(self.shield_strap_pitch_um) < float(self.shield_strap_width_um):
            raise ValueError("shield_strap_pitch_um must be at least shield_strap_width_um")

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "manufacturing_grid_um": float(self.manufacturing_grid_um),
            "power_line_stitch_pad_depth_um": float(self.power_line_stitch_pad_depth_um),
            "shield_strap_width_um": float(self.shield_strap_width_um),
            "shield_strap_pitch_um": float(self.shield_strap_pitch_um),
        }


@dataclass(frozen=True)
class InductorFixedSpec:
    """Fixed topology/process settings for one inductor."""

    turns: int
    center_tap: bool
    bridge_layer: int | None = None
    bridge_via_layer: int | None = None
    bridge_lower_layer: int | None = None
    bridge_lower_via_layer: int | None = None
    bridge_section: BridgeSectionSpec | None = None
    vdd_bar: VddBarSpec | None = None

    def uses_spacing(self) -> bool:
        return int(self.turns) > 1

    def uses_bridge_section(self) -> bool:
        return self.uses_spacing() and self.bridge_section is not None

    def uses_vdd_bar(self) -> bool:
        return bool(
            self.center_tap
            and self.vdd_bar is not None
            and self.vdd_bar.enabled
            and self.vdd_bar.bar_layer is not None
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "turns": int(self.turns),
            "center_tap": bool(self.center_tap),
            "bridge_layer": None if self.bridge_layer is None else int(self.bridge_layer),
            "bridge_via_layer": None if self.bridge_via_layer is None else int(self.bridge_via_layer),
            "bridge_lower_layer": None if self.bridge_lower_layer is None else int(self.bridge_lower_layer),
            "bridge_lower_via_layer": None if self.bridge_lower_via_layer is None else int(self.bridge_lower_via_layer),
            "bridge_section": None if self.bridge_section is None else self.bridge_section.as_dict(),
            "vdd_bar": None if self.vdd_bar is None else self.vdd_bar.as_dict(),
        }


@dataclass(frozen=True)
class InductorSpec:
    """Composed inductor model with continuous geometry and fixed settings."""

    geometry: InductorGeometry
    fixed: InductorFixedSpec

    def uses_spacing(self) -> bool:
        return self.fixed.uses_spacing()

    def uses_bridge_section(self) -> bool:
        return self.fixed.uses_bridge_section()

    def uses_vdd_bar(self) -> bool:
        return self.fixed.uses_vdd_bar()

    def active_flat_field_names(self, prefix: str) -> tuple[str, ...]:
        return self.geometry.active_flat_field_names(prefix, turns=self.fixed.turns)

    def as_dict(self) -> dict[str, object]:
        return {
            "geometry": self.geometry.as_dict(),
            "fixed": self.fixed.as_dict(),
        }

    @property
    def outer_width_um(self) -> float:
        return float(self.geometry.outer_width_um)

    @property
    def outer_height_um(self) -> float:
        return float(self.geometry.outer_height_um)

    @property
    def trace_width_um(self) -> float:
        return float(self.geometry.trace_width_um)

    @property
    def spacing_um(self) -> float:
        return float(self.geometry.spacing_um)

    @property
    def terminal_y_span_um(self) -> float:
        return float(self.geometry.terminal_y_span_um)

    @property
    def feed_extension_um(self) -> float:
        return float(self.geometry.feed_extension_um)

    @property
    def turns(self) -> int:
        return int(self.fixed.turns)

    @property
    def center_tap(self) -> bool:
        return bool(self.fixed.center_tap)

    @property
    def bridge_layer(self) -> int | None:
        return None if self.fixed.bridge_layer is None else int(self.fixed.bridge_layer)

    @property
    def bridge_via_layer(self) -> int | None:
        return None if self.fixed.bridge_via_layer is None else int(self.fixed.bridge_via_layer)

    @property
    def bridge_lower_layer(self) -> int | None:
        return None if self.fixed.bridge_lower_layer is None else int(self.fixed.bridge_lower_layer)

    @property
    def bridge_lower_via_layer(self) -> int | None:
        return None if self.fixed.bridge_lower_via_layer is None else int(self.fixed.bridge_lower_via_layer)

    @property
    def bridge_section(self) -> BridgeSectionSpec | None:
        return self.fixed.bridge_section

    @property
    def vdd_bar(self) -> VddBarSpec | None:
        return self.fixed.vdd_bar


@dataclass(frozen=True)
class ShieldSpec:
    """Transformer-level shield geometry intent."""

    enabled: bool = False
    kind: Literal["ring"] = "ring"
    margin_um: float | None = None
    width_um: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": bool(self.enabled),
            "kind": str(self.kind),
            "margin_um": None if self.margin_um is None else float(self.margin_um),
            "width_um": None if self.width_um is None else float(self.width_um),
        }


@dataclass(frozen=True)
class TransformerTargetSpec:
    """Electrical target specification for one transformer optimization run."""

    f0_hz: float
    lp_h: float
    ls_h: float
    k_target: float
    q_target_mode: QTargetMode = "max"
    q_primary_target: float | None = None
    q_secondary_target: float | None = None
    topology_mode: TopologyMode = "1t1t"
    differential_reference_impedance_ohm: float = 100.0
    band_points: int = 9
    fractional_bandwidth: float = 0.20
    frequency_start_hz: float | None = None
    frequency_stop_hz: float | None = None
    frequency_step_hz: float | None = None

    def __post_init__(self) -> None:
        if self.q_target_mode == "target":
            if self.q_primary_target is None or self.q_secondary_target is None:
                raise ValueError("q_target_mode='target' requires q_primary_target and q_secondary_target")
        if self.q_primary_target is not None and float(self.q_primary_target) <= 0.0:
            raise ValueError("q_primary_target must be positive when provided")
        if self.q_secondary_target is not None and float(self.q_secondary_target) <= 0.0:
            raise ValueError("q_secondary_target must be positive when provided")
        explicit = (self.frequency_start_hz, self.frequency_stop_hz, self.frequency_step_hz)
        if any(value is not None for value in explicit):
            if any(value is None for value in explicit):
                raise ValueError(
                    "frequency_start_hz, frequency_stop_hz, and frequency_step_hz must be provided together"
                )
            start = float(self.frequency_start_hz)
            stop = float(self.frequency_stop_hz)
            step = float(self.frequency_step_hz)
            if start <= 0.0 or stop <= 0.0 or step <= 0.0:
                raise ValueError("explicit frequency sweep values must be positive")
            if stop <= start:
                raise ValueError("frequency_stop_hz must be greater than frequency_start_hz")
            intervals = (stop - start) / step
            if not np.isclose(intervals, round(intervals), rtol=0.0, atol=1.0e-9):
                raise ValueError("frequency_step_hz must divide frequency_stop_hz - frequency_start_hz")
            expected_points = int(round(intervals)) + 1
            if int(self.band_points) != expected_points:
                raise ValueError(
                    "band_points must match explicit frequency sweep "
                    f"({expected_points} points from frequency_start_hz/frequency_stop_hz/frequency_step_hz)"
                )

    def band_edges_hz(self) -> tuple[float, float]:
        if self.frequency_start_hz is not None and self.frequency_stop_hz is not None:
            return float(self.frequency_start_hz), float(self.frequency_stop_hz)
        half_span = 0.5 * self.fractional_bandwidth * self.f0_hz
        return float(self.f0_hz - half_span), float(self.f0_hz + half_span)

    def frequency_points_hz(self) -> np.ndarray:
        if (
            self.frequency_start_hz is not None
            and self.frequency_stop_hz is not None
            and self.frequency_step_hz is not None
        ):
            start = float(self.frequency_start_hz)
            stop = float(self.frequency_stop_hz)
            step = float(self.frequency_step_hz)
            intervals = int(round((stop - start) / step))
            return start + step * np.arange(intervals + 1, dtype=float)
        return np.linspace(*self.band_edges_hz(), int(self.band_points))


@dataclass(frozen=True)
class ViaSpacingOption:
    """Minimum via-count/spacing option for redundancy checks."""

    min_via_count: int
    max_spacing_um: float


@dataclass(frozen=True)
class ViaWideMetalRequirement:
    """Redundant-via requirement triggered by large connected metals."""

    min_width_um: float
    min_length_um: float
    options: tuple[ViaSpacingOption, ...] = tuple()


@dataclass(frozen=True)
class ViaPlateThresholds:
    """Optional large-plate proximity thresholds for warning-only checks."""

    max_distance_um: float | None = None
    min_plate_width_um: float | None = None
    min_plate_height_um: float | None = None


@dataclass(frozen=True)
class ViaFamilyRule:
    """Rule deck entry for one via family."""

    size_um: float
    min_spacing_um: float
    legal_min_all_sides_um: tuple[float, ...] = tuple()
    legal_min_opposite_sides_um: tuple[float, ...] = tuple()
    recommended_min_all_sides_um: float | None = None
    recommended_line_end_enclosure_um: float | None = None
    wide_metal_requirements: tuple[ViaWideMetalRequirement, ...] = tuple()
    stacked_single_via_max_depth: int | None = None
    plate_thresholds: ViaPlateThresholds | None = None


@dataclass(frozen=True)
class ViaLayerRule:
    """Per-layer mapping from GDS via layers to via families and optional metals."""

    family: ViaFamilyName
    connected_metal_layers: tuple[int, int] | None = None


def _default_via_family_rules() -> dict[str, ViaFamilyRule]:
    return {
        "VIAx": ViaFamilyRule(
            size_um=0.10,
            min_spacing_um=0.10,
            legal_min_all_sides_um=(0.00, 0.03),
            legal_min_opposite_sides_um=(0.04,),
            recommended_min_all_sides_um=0.04,
            recommended_line_end_enclosure_um=0.07,
            wide_metal_requirements=(
                ViaWideMetalRequirement(
                    min_width_um=0.30,
                    min_length_um=0.30,
                    options=(
                        ViaSpacingOption(min_via_count=2, max_spacing_um=0.20),
                        ViaSpacingOption(min_via_count=4, max_spacing_um=0.25),
                    ),
                ),
                ViaWideMetalRequirement(
                    min_width_um=0.70,
                    min_length_um=0.70,
                    options=(
                        ViaSpacingOption(min_via_count=4, max_spacing_um=0.20),
                        ViaSpacingOption(min_via_count=9, max_spacing_um=0.35),
                    ),
                ),
            ),
            stacked_single_via_max_depth=4,
        ),
        "VIAy": ViaFamilyRule(
            size_um=0.20,
            min_spacing_um=0.20,
            legal_min_all_sides_um=(0.00,),
            legal_min_opposite_sides_um=(0.05,),
            recommended_min_all_sides_um=0.05,
            recommended_line_end_enclosure_um=0.08,
            wide_metal_requirements=(
                ViaWideMetalRequirement(
                    min_width_um=0.60,
                    min_length_um=0.60,
                    options=(
                        ViaSpacingOption(min_via_count=2, max_spacing_um=0.40),
                        ViaSpacingOption(min_via_count=4, max_spacing_um=0.50),
                    ),
                ),
                ViaWideMetalRequirement(
                    min_width_um=1.40,
                    min_length_um=1.40,
                    options=(ViaSpacingOption(min_via_count=4, max_spacing_um=0.40),),
                ),
            ),
        ),
        "VIAz": ViaFamilyRule(
            size_um=0.36,
            min_spacing_um=0.34,
            legal_min_all_sides_um=(0.02,),
            legal_min_opposite_sides_um=(0.08,),
            wide_metal_requirements=(
                ViaWideMetalRequirement(
                    min_width_um=1.80,
                    min_length_um=1.80,
                    options=(ViaSpacingOption(min_via_count=2, max_spacing_um=1.70),),
                ),
            ),
        ),
        "VIAr": ViaFamilyRule(
            size_um=0.46,
            min_spacing_um=0.44,
            legal_min_all_sides_um=(0.02,),
            legal_min_opposite_sides_um=(0.08,),
            wide_metal_requirements=(
                ViaWideMetalRequirement(
                    min_width_um=1.80,
                    min_length_um=1.80,
                    options=(ViaSpacingOption(min_via_count=2, max_spacing_um=1.70),),
                ),
            ),
        ),
    }


def _default_via_layer_rules() -> dict[int, ViaLayerRule]:
    return {
        84: ViaLayerRule(family="VIAy"),
        85: ViaLayerRule(family="VIAy"),
        87: ViaLayerRule(family="VIAy"),
        57: ViaLayerRule(family="VIAz"),
        58: ViaLayerRule(family="VIAz"),
    }


@dataclass(frozen=True)
class TransformerEmxConfig:
    """EMX and layer mapping settings for the transformer flow."""

    DEFAULT_CADENCE_LICENSE = None
    DEFAULT_CADENCE_INSTALL_ROOT = "/opt/cadence/IC"
    DEFAULT_CADENCE_PDK_CDS_LIB = "/path/to/pdk/cds.lib"
    DEFAULT_CADENCE_TECH_LIB = "exampleTechLib"
    DEFAULT_CADENCE_LAYER_MAP = "/path/to/pdk/layers.layermap"
    DEFAULT_REMOTE_WORK_ROOT = "/tmp/rfic_transformer_inverse_design_remote"

    emx_binary: str = "emx"
    emx_home: str | None = None
    emx_process_file: str = field(default_factory=lambda: str(default_proc_path()))
    top_cell_prefix: str = "TRANSFORMER"
    extra_args: tuple[str, ...] = ("--edge-width=1", "--accuracy=standard", "--verbose=2")
    use_cadence_license_env: bool = True
    license_file: str | None = DEFAULT_CADENCE_LICENSE
    cdslmd_license_file: str | None = DEFAULT_CADENCE_LICENSE
    skip_os_check: bool = True
    cadence_pin_purpose: int | None = 51
    cadence_install_root: str = DEFAULT_CADENCE_INSTALL_ROOT
    cadence_pdk_cds_lib: str = DEFAULT_CADENCE_PDK_CDS_LIB
    cadence_tech_lib: str = DEFAULT_CADENCE_TECH_LIB
    cadence_layer_map: str = DEFAULT_CADENCE_LAYER_MAP
    execution_mode: TransformerEmxExecutionMode = "local"
    remote_ssh_host: str | None = None
    remote_repo_root: str | None = None
    remote_work_root: str = DEFAULT_REMOTE_WORK_ROOT
    remote_python: str = "python"
    remote_venv_activate: str | None = None
    remote_emx_process_file: str | None = None
    remote_ssh_command: str = "ssh"
    remote_scp_command: str = "scp"
    port_mode: TransformerEmxPortMode = "single_ended_floating"
    differential_port_pairs: tuple[tuple[int, int], tuple[int, int]] | None = None
    ground_unused_s8p_ports: bool = False
    power_line_8port: PowerLine8PortSpec = field(default_factory=PowerLine8PortSpec)
    foundry_layout: FoundryLayoutSpec = field(default_factory=FoundryLayoutSpec)
    ap_layer: int = 74
    m9_layer: int = 39
    m5_layer: int = 35
    primary_bridge_layer: int = 39
    primary_bridge_via_layer: int = 85
    primary_bridge_lower_layer: int | None = 38
    primary_bridge_lower_via_layer: int | None = 58
    secondary_bridge_layer: int = 38
    secondary_bridge_via_layer: int = 58
    secondary_bridge_lower_layer: int | None = None
    secondary_bridge_lower_via_layer: int | None = None
    shield_layer: int | None = 35
    metal_datatype: int = 0
    label_layer: int = 135
    label_datatype: int = 0
    via_layer_rules: dict[int, ViaLayerRule] = field(default_factory=_default_via_layer_rules)
    via_family_rules: dict[str, ViaFamilyRule] = field(default_factory=_default_via_family_rules)
    enable_large_plate_warnings: bool = True

    def __post_init__(self) -> None:
        if self.execution_mode == "remote_ssh":
            missing: list[str] = []
            if self.remote_ssh_host is None or not str(self.remote_ssh_host).strip():
                missing.append("remote_ssh_host")
            if self.remote_repo_root is None or not str(self.remote_repo_root).strip():
                missing.append("remote_repo_root")
            if self.remote_work_root is None or not str(self.remote_work_root).strip():
                missing.append("remote_work_root")
            if missing:
                raise ValueError(
                    "execution_mode='remote_ssh' requires "
                    + ", ".join(missing)
                )

    def uses_differential_ports(self) -> bool:
        return self.port_mode == "differential_pairs"

    def uses_shield_as_port_ground(self) -> bool:
        return self.port_mode == "single_ended_shield_grounded"

    def uses_cadence_pins(self) -> bool:
        return self.cadence_pin_purpose is not None

    def uses_remote_ssh(self) -> bool:
        return self.execution_mode == "remote_ssh"

    @property
    def primary_coil_layer(self) -> int:
        """Canonical semantic name for the primary winding conductor layer."""

        return int(self.ap_layer)

    @property
    def secondary_coil_layer(self) -> int:
        """Canonical semantic name for the secondary winding conductor layer."""

        return int(self.m9_layer)


@dataclass(frozen=True)
class CMAESOptimizerConfig:
    """CMA-ES backend settings."""

    population_size: int | None = None
    sigma0: float | None = None
    verbose: int = -9


@dataclass(frozen=True)
class TuRBOOptimizerConfig:
    """TuRBO-1 backend settings."""

    initial_length: float = 0.8
    length_min: float = 0.5**7
    length_max: float = 1.6
    success_tolerance: int = 10
    num_restarts: int = 10
    raw_samples: int = 256
    n_candidates: int | None = None
    max_cholesky_size: float = float("inf")
    acquisition_function: Literal["ei"] = "ei"


@dataclass(frozen=True)
class TransformerOptimizerConfig:
    """Continuous optimizer settings with a selectable backend."""

    name: Literal["cma_es", "turbo"] = "cma_es"
    max_evaluations: int = 552
    warm_start_samples: int = 18
    warm_start_paths: tuple[str, ...] = tuple()
    seed: int = 1234
    resume_from_checkpoint: bool = False
    checkpoint_interval_evaluations: int = 1
    cma_es: CMAESOptimizerConfig = field(default_factory=CMAESOptimizerConfig)
    turbo: TuRBOOptimizerConfig = field(default_factory=TuRBOOptimizerConfig)


@dataclass(frozen=True)
class TransformerRunConfig:
    """Full configuration for one transformer optimization run."""

    target: TransformerTargetSpec
    bounds: TransformerSearchSpace
    emx: TransformerEmxConfig = field(default_factory=TransformerEmxConfig)
    optimizer: TransformerOptimizerConfig = field(default_factory=TransformerOptimizerConfig)


@dataclass(frozen=True)

class TransformerLayoutExport:
    """Artifacts produced by layout export."""

    gds_path: Path
    manifest_path: Path
    preview_path: Path
    debug_preview_path: Path
    top_cell: str


@dataclass(frozen=True)
class TransformerMetrics:
    """Electrical metrics extracted from the differential transformer network."""

    center_frequency_hz: float
    lp_h: float
    ls_h: float
    mutual_h: float
    k: float
    q_primary: float
    q_secondary: float
    real_z11_ohm: float
    real_z22_ohm: float
    z_diff_center: tuple[tuple[complex, complex], tuple[complex, complex]]

    def min_q(self) -> float:
        return float(min(self.q_primary, self.q_secondary))

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["z_diff_center"] = [[complex(value) for value in row] for row in self.z_diff_center]
        return payload


@dataclass(frozen=True)
class TransformerObjectiveBreakdown:
    """Objective terms for one evaluation."""

    lp_rel_error: float
    ls_rel_error: float
    k_rel_error: float
    primary_term: float
    q_reward: float
    total_cost: float
    q_target_term: float = 0.0
    q_primary_rel_error: float | None = None
    q_secondary_rel_error: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class TransformerEvalResult:
    """Complete result for one exported/evaluated transformer geometry."""

    cache_key: str
    geometry: TransformerSpec
    target: TransformerTargetSpec
    layout: TransformerLayoutExport | None
    metrics: TransformerMetrics | None
    objective: TransformerObjectiveBreakdown | None
    single_ended_sparams: SParameterResult | None
    differential_sparams: SParameterResult | None
    differential_z: np.ndarray | None
    work_dir: Path
    touchstone_path: Path | None
    command: list[str] | None
    geometry_check: dict[str, object] | None = None
    error: str | None = None

    def ok(self) -> bool:
        return self.error is None

    def summary_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "cache_key": self.cache_key,
            "geometry": self.geometry.flat_dict(),
            "target": asdict(self.target),
            "work_dir": str(self.work_dir),
            "touchstone_path": str(self.touchstone_path) if self.touchstone_path is not None else None,
            "command": self.command,
            "error": self.error,
            "ok": self.ok(),
        }
        if self.layout is not None:
            payload["artifacts"] = {
                "gds": str(self.layout.gds_path),
                "manifest": str(self.layout.manifest_path),
                "preview": str(self.layout.preview_path),
                "debug_preview": str(self.layout.debug_preview_path),
            }
        if self.metrics is not None:
            payload["metrics"] = self.metrics.as_dict()
        if self.objective is not None:
            payload["objective"] = self.objective.as_dict()
        if self.geometry_check is not None:
            payload["geometry_check"] = self.geometry_check
        if self.differential_sparams is not None:
            payload["num_freqs"] = int(self.differential_sparams.num_freqs)
        return payload

def topology_mode_from_turns(primary_turns: int, secondary_turns: int) -> TopologyMode:
    primary = int(primary_turns)
    secondary = int(secondary_turns)
    if primary == 1 and secondary == 1:
        return "1t1t"
    if primary == 1 and secondary == 2:
        return "1t2t"
    if primary == 2 and secondary == 1:
        return "2t1t"
    if primary == 2 and secondary == 2:
        return "2t2t"
    raise ValueError(f"Unsupported turn combination: primary={primary} secondary={secondary}")


def _coerce_topology_mode(value: object) -> TopologyMode:
    mode = str(value)
    if mode == "interweaved_bridge_2t2t":
        return "2t2t"
    if mode not in ("1t1t", "1t2t", "2t1t", "2t2t"):
        raise ValueError(f"Unsupported topology_mode: {mode}")
    return mode  # type: ignore[return-value]


def _coerce_emx_port_mode(value: object) -> TransformerEmxPortMode:
    mode = str(value)
    if mode not in ("single_ended_floating", "single_ended_shield_grounded", "differential_pairs"):
        raise ValueError(f"Unsupported EMX port_mode: {mode}")
    return mode  # type: ignore[return-value]
