"""Configuration helpers for rfic-transformer-inverse-design."""

from __future__ import annotations

from pathlib import Path

import yaml

from .bounds import InductorBounds, TransformerSearchSpace
from .topology import default_topology_fields
from .types import (
    BridgeSectionConfig,
    CMAESOptimizerConfig,
    ShieldSpec,
    TopologyMode,
    TransformerEmxConfig,
    PowerLine8PortSpec,
    TransformerOptimizerConfig,
    TransformerRunConfig,
    TransformerTargetSpec,
    TuRBOOptimizerConfig,
    ViaFamilyRule,
    ViaLayerRule,
    ViaPlateThresholds,
    ViaSpacingOption,
    ViaWideMetalRequirement,
    VddBarSpec,
    _coerce_topology_mode,
    _coerce_emx_port_mode,
    topology_mode_from_turns,
)
from ..process.proc_parser import parse_proc_file
from ..process.stackup import infer_bridge_route_layers


def _coerce_q_target_mode(value: object) -> str:
    mode = str(value).strip().lower()
    if mode not in {"max", "target"}:
        raise ValueError(f"Unsupported q_target_mode {value!r}; expected 'max' or 'target'")
    return mode


def _coerce_emx_execution_mode(value: object) -> str:
    mode = str(value).strip().lower()
    if mode not in {"local", "remote_ssh"}:
        raise ValueError(f"Unsupported EMX execution_mode {value!r}; expected 'local' or 'remote_ssh'")
    return mode


def _default_primary_bridge_section(topology_mode: TopologyMode, *, turns: int) -> BridgeSectionConfig | None:
    if int(turns) <= 1:
        return None
    if topology_mode == "1t1t":
        return None
    return BridgeSectionConfig()


def _default_secondary_bridge_section(topology_mode: TopologyMode, *, turns: int) -> BridgeSectionConfig | None:
    if int(turns) <= 1:
        return None
    if topology_mode == "1t1t":
        return None
    return BridgeSectionConfig()


def _coerce_via_family_name(value: object) -> str:
    family = str(value).strip()
    if family not in {"VIAx", "VIAy", "VIAz", "VIAr"}:
        raise ValueError(f"Unsupported via family {value!r}")
    return family


def _load_via_spacing_options(raw: object) -> tuple[ViaSpacingOption, ...]:
    if raw in (None, ""):
        return tuple()
    options: list[ViaSpacingOption] = []
    for item in raw:
        entry = dict(item or {})
        options.append(
            ViaSpacingOption(
                min_via_count=int(entry["min_via_count"]),
                max_spacing_um=float(entry["max_spacing_um"]),
            )
        )
    return tuple(options)


def _load_wide_metal_requirements(raw: object) -> tuple[ViaWideMetalRequirement, ...]:
    if raw in (None, ""):
        return tuple()
    requirements: list[ViaWideMetalRequirement] = []
    for item in raw:
        entry = dict(item or {})
        requirements.append(
            ViaWideMetalRequirement(
                min_width_um=float(entry["min_width_um"]),
                min_length_um=float(entry["min_length_um"]),
                options=_load_via_spacing_options(entry.get("options")),
            )
        )
    return tuple(requirements)


def _load_plate_thresholds(raw: object) -> ViaPlateThresholds | None:
    if raw in (None, ""):
        return None
    entry = dict(raw or {})
    return ViaPlateThresholds(
        max_distance_um=None if entry.get("max_distance_um") is None else float(entry.get("max_distance_um")),
        min_plate_width_um=None if entry.get("min_plate_width_um") is None else float(entry.get("min_plate_width_um")),
        min_plate_height_um=None if entry.get("min_plate_height_um") is None else float(entry.get("min_plate_height_um")),
    )


def _load_via_family_rules(raw: object, defaults: dict[str, ViaFamilyRule]) -> dict[str, ViaFamilyRule]:
    if raw in (None, ""):
        return dict(defaults)
    resolved = dict(defaults)
    for family_name, item in dict(raw or {}).items():
        entry = dict(item or {})
        family = _coerce_via_family_name(family_name)
        base = defaults.get(family, ViaFamilyRule(size_um=0.1, min_spacing_um=0.1))
        resolved[family] = ViaFamilyRule(
            size_um=float(entry.get("size_um", base.size_um)),
            min_spacing_um=float(entry.get("min_spacing_um", base.min_spacing_um)),
            legal_min_all_sides_um=tuple(
                float(value) for value in entry.get("legal_min_all_sides_um", base.legal_min_all_sides_um)
            ),
            legal_min_opposite_sides_um=tuple(
                float(value) for value in entry.get("legal_min_opposite_sides_um", base.legal_min_opposite_sides_um)
            ),
            recommended_min_all_sides_um=(
                base.recommended_min_all_sides_um
                if entry.get("recommended_min_all_sides_um") is None
                else float(entry.get("recommended_min_all_sides_um"))
            ),
            recommended_line_end_enclosure_um=(
                base.recommended_line_end_enclosure_um
                if entry.get("recommended_line_end_enclosure_um") is None
                else float(entry.get("recommended_line_end_enclosure_um"))
            ),
            wide_metal_requirements=_load_wide_metal_requirements(
                entry.get("wide_metal_requirements", base.wide_metal_requirements)
            ),
            stacked_single_via_max_depth=(
                base.stacked_single_via_max_depth
                if entry.get("stacked_single_via_max_depth") is None
                else int(entry.get("stacked_single_via_max_depth"))
            ),
            plate_thresholds=_load_plate_thresholds(
                entry.get("plate_thresholds", base.plate_thresholds)
            ),
        )
    return resolved


def _load_via_layer_rules(raw: object, defaults: dict[int, ViaLayerRule]) -> dict[int, ViaLayerRule]:
    if raw in (None, ""):
        return dict(defaults)
    resolved = dict(defaults)
    for layer_name, item in dict(raw or {}).items():
        layer = int(layer_name)
        entry = dict(item or {})
        base = defaults.get(layer, ViaLayerRule(family="VIAy"))
        connected = entry.get("connected_metal_layers", base.connected_metal_layers)
        resolved[layer] = ViaLayerRule(
            family=_coerce_via_family_name(entry.get("family", base.family)),
            connected_metal_layers=(
                None if connected is None else (int(connected[0]), int(connected[1]))
            ),
        )
    return resolved


def default_target_spec(topology_mode: TopologyMode = "1t1t") -> TransformerTargetSpec:
    return TransformerTargetSpec(
        f0_hz=15.0e9,
        lp_h=1.155247e-9,
        ls_h=1.39977e-9,
        k_target=0.759752,
        q_target_mode="max",
        topology_mode=topology_mode,
        differential_reference_impedance_ohm=100.0,
        band_points=9,
    )


def default_bounds(
    topology_mode: TopologyMode = "1t1t",
    *,
    shield: ShieldSpec | None = None,
    primary_bridge_layer: int | None = None,
    primary_bridge_via_layer: int | None = None,
    primary_bridge_lower_layer: int | None = None,
    primary_bridge_lower_via_layer: int | None = None,
    secondary_bridge_layer: int | None = None,
    secondary_bridge_via_layer: int | None = None,
) -> TransformerSearchSpace:
    topology = default_topology_fields(topology_mode)
    resolved_shield = ShieldSpec() if shield is None else shield
    return TransformerSearchSpace(
        primary=InductorBounds(
            outer_width_um=(20.0, 500.0),
            outer_height_um=(20.0, 500.0),
            trace_width_um=(1.0, 12.0),
            spacing_um=(2.0, 14.0),
            terminal_y_span_um=(20.0, 180.0),
            feed_extension_um=(10.0, 300.0),
            turns=int(topology["primary_turns"]),
            center_tap=bool(topology["primary_center_tap"]),
            bridge_layer=(None if int(topology["primary_turns"]) <= 1 else primary_bridge_layer),
            bridge_via_layer=(None if int(topology["primary_turns"]) <= 1 else primary_bridge_via_layer),
            bridge_lower_layer=(None if int(topology["primary_turns"]) <= 1 else primary_bridge_lower_layer),
            bridge_lower_via_layer=(
                None if int(topology["primary_turns"]) <= 1 else primary_bridge_lower_via_layer
            ),
            bridge_section=_default_primary_bridge_section(topology_mode, turns=int(topology["primary_turns"])),
            vdd_bar=VddBarSpec(bar_layer=74, width_um=10.0, offset_um=0.0),
        ),
        secondary=InductorBounds(
            outer_width_um=(20.0, 500.0),
            outer_height_um=(20.0, 500.0),
            trace_width_um=(1.0, 12.0),
            spacing_um=(2.0, 14.0),
            terminal_y_span_um=(20.0, 180.0),
            feed_extension_um=(10.0, 300.0),
            turns=int(topology["secondary_turns"]),
            center_tap=bool(topology["secondary_center_tap"]),
            bridge_layer=(None if int(topology["secondary_turns"]) <= 1 else secondary_bridge_layer),
            bridge_via_layer=(None if int(topology["secondary_turns"]) <= 1 else secondary_bridge_via_layer),
            bridge_lower_layer=None,
            bridge_lower_via_layer=None,
            bridge_section=_default_secondary_bridge_section(
                topology_mode,
                turns=int(topology["secondary_turns"]),
            ),
            vdd_bar=VddBarSpec(bar_layer=74, width_um=10.0, offset_um=0.0),
        ),
        offset_um=(-100.0, 100.0),
        topology_mode=topology_mode,
        shield=resolved_shield,
    )


def default_run_config(topology_mode: TopologyMode = "1t1t") -> TransformerRunConfig:
    emx = TransformerEmxConfig()
    shield = ShieldSpec(enabled=True, kind="ring", margin_um=100.0, width_um=10.0)
    return TransformerRunConfig(
        target=default_target_spec(topology_mode=topology_mode),
        bounds=default_bounds(
            topology_mode=topology_mode,
            shield=shield,
            primary_bridge_layer=emx.primary_bridge_layer,
            primary_bridge_via_layer=emx.primary_bridge_via_layer,
            primary_bridge_lower_layer=emx.primary_bridge_lower_layer,
            primary_bridge_lower_via_layer=emx.primary_bridge_lower_via_layer,
            secondary_bridge_layer=emx.secondary_bridge_layer,
            secondary_bridge_via_layer=emx.secondary_bridge_via_layer,
        ),
        emx=emx,
        optimizer=TransformerOptimizerConfig(),
    )


def _resolve_route_fields(
    *,
    proc_path: str,
    coil_layer: int,
    target_layer: int | None,
    explicit_layer: int | None,
    explicit_via_layer: int | None,
    explicit_lower_layer: int | None,
    explicit_lower_via_layer: int | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    if target_layer is None and explicit_layer is None:
        return None, None, None, None
    if (
        explicit_layer is not None
        or explicit_via_layer is not None
        or explicit_lower_layer is not None
        or explicit_lower_via_layer is not None
    ):
        return (
            None if explicit_layer is None else int(explicit_layer),
            None if explicit_via_layer is None else int(explicit_via_layer),
            None if explicit_lower_layer is None else int(explicit_lower_layer),
            None if explicit_lower_via_layer is None else int(explicit_lower_via_layer),
        )
    if target_layer is None:
        return None, None, None, None
    if int(target_layer) == int(coil_layer):
        return int(target_layer), None, None, None
    proc_info = parse_proc_file(proc_path)
    route = infer_bridge_route_layers(proc_info, coil_layer=int(coil_layer), bridge_layer=int(target_layer))
    return (
        int(route.bridge_layer),
        int(route.bridge_via_layer),
        None if route.bridge_lower_layer is None else int(route.bridge_lower_layer),
        None if route.bridge_lower_via_layer is None else int(route.bridge_lower_via_layer),
    )


def _optional_int_field(mapping: dict[str, object], key: str) -> int | None:
    if key not in mapping:
        return None
    value = mapping[key]
    if value is None:
        return None
    return int(value)


def _normalize_to_preferred_draw_layer(*, proc_path: str, layer: int) -> int:
    try:
        proc_info = parse_proc_file(proc_path)
    except FileNotFoundError:
        return int(layer)
    preferred = proc_info.preferred_draw_pair_for_layer(int(layer))
    if preferred is None:
        return int(layer)
    return int(preferred.layer)


def load_run_config_from_raw(raw: dict[str, object] | None = None) -> TransformerRunConfig:
    if raw is None:
        return default_run_config(topology_mode="1t1t")

    raw = dict(raw or {})
    target_raw = dict(raw.get("target", {}) or {})
    bounds_raw = dict(raw.get("bounds", {}) or {})
    topology_raw = dict(raw.get("topology", {}) or {})
    topology_primary_raw = dict(topology_raw.get("primary", {}) or {})
    topology_secondary_raw = dict(topology_raw.get("secondary", {}) or {})
    topology_shield_raw = dict(topology_raw.get("shield", {}) or {})
    primary_bounds_raw = dict(bounds_raw.get("primary", {}) or {})
    secondary_bounds_raw = dict(bounds_raw.get("secondary", {}) or {})
    chosen_topology_raw = target_raw.get("topology_mode", bounds_raw.get("topology_mode"))
    primary_turns_hint = topology_primary_raw.get(
        "turns",
        topology_raw.get("primary_turns", primary_bounds_raw.get("turns")),
    )
    secondary_turns_hint = topology_secondary_raw.get(
        "turns",
        topology_raw.get("secondary_turns", secondary_bounds_raw.get("turns")),
    )
    if primary_turns_hint is not None or secondary_turns_hint is not None:
        fallback_topology = _coerce_topology_mode(
            "1t1t" if chosen_topology_raw is None else chosen_topology_raw
        )
        fallback_fields = default_topology_fields(fallback_topology)
        chosen_topology = topology_mode_from_turns(
            int(
                fallback_fields["primary_turns"]
                if primary_turns_hint is None
                else primary_turns_hint
            ),
            int(
                fallback_fields["secondary_turns"]
                if secondary_turns_hint is None
                else secondary_turns_hint
            ),
        )
    elif chosen_topology_raw is not None:
        chosen_topology = _coerce_topology_mode(chosen_topology_raw)
    else:
        chosen_topology = "1t1t"
    cfg = default_run_config(topology_mode=chosen_topology)

    q_target_mode_raw = target_raw.get("q_target_mode", target_raw.get("q_mode"))
    q_primary_target_raw = target_raw.get("q_primary_target", target_raw.get("q_primary"))
    q_secondary_target_raw = target_raw.get("q_secondary_target", target_raw.get("q_secondary"))
    if q_target_mode_raw is None:
        q_target_mode = "target" if q_primary_target_raw is not None or q_secondary_target_raw is not None else cfg.target.q_target_mode
    else:
        q_target_mode = _coerce_q_target_mode(q_target_mode_raw)
    frequency_start_raw = target_raw.get("frequency_start_hz", target_raw.get("freq_start_hz"))
    frequency_stop_raw = target_raw.get("frequency_stop_hz", target_raw.get("freq_stop_hz"))
    frequency_step_raw = target_raw.get("frequency_step_hz", target_raw.get("freq_step_hz"))
    explicit_frequency_raw = (frequency_start_raw, frequency_stop_raw, frequency_step_raw)
    explicit_band_points = cfg.target.band_points
    if all(value is not None for value in explicit_frequency_raw):
        explicit_band_points = int(round((float(frequency_stop_raw) - float(frequency_start_raw)) / float(frequency_step_raw))) + 1

    target = TransformerTargetSpec(
        f0_hz=float(target_raw.get("f0_hz", cfg.target.f0_hz)),
        lp_h=float(target_raw.get("lp_h", cfg.target.lp_h)),
        ls_h=float(target_raw.get("ls_h", cfg.target.ls_h)),
        k_target=float(target_raw.get("k_target", cfg.target.k_target)),
        q_target_mode=q_target_mode,
        q_primary_target=(
            None
            if q_primary_target_raw is None
            else float(q_primary_target_raw)
        ),
        q_secondary_target=(
            None
            if q_secondary_target_raw is None
            else float(q_secondary_target_raw)
        ),
        topology_mode=chosen_topology,
        differential_reference_impedance_ohm=float(
            target_raw.get(
                "differential_reference_impedance_ohm",
                cfg.target.differential_reference_impedance_ohm,
            )
        ),
        band_points=int(target_raw.get("band_points", explicit_band_points)),
        fractional_bandwidth=float(target_raw.get("fractional_bandwidth", cfg.target.fractional_bandwidth)),
        frequency_start_hz=None if frequency_start_raw is None else float(frequency_start_raw),
        frequency_stop_hz=None if frequency_stop_raw is None else float(frequency_stop_raw),
        frequency_step_hz=None if frequency_step_raw is None else float(frequency_step_raw),
    )

    legacy_transformer_shield_raw = dict(raw.get("transformer", {}).get("shield", {}) or {})
    shield_raw = {
        **legacy_transformer_shield_raw,
        **dict(bounds_raw.get("shield", {}) or {}),
        **topology_shield_raw,
    }
    legacy_shield_margin = emx_raw_margin = raw.get("emx", {}).get("ground_ring_margin_um")
    legacy_shield_width = emx_raw_width = raw.get("emx", {}).get("ground_ring_width_um")
    shield = ShieldSpec(
        enabled=bool(shield_raw.get("enabled", cfg.bounds.shield.enabled)),
        kind=str(shield_raw.get("kind", cfg.bounds.shield.kind)),
        margin_um=(
            None
            if shield_raw.get("margin_um", legacy_shield_margin if legacy_shield_margin is not None else cfg.bounds.shield.margin_um) is None
            else float(
                shield_raw.get(
                    "margin_um",
                    legacy_shield_margin if legacy_shield_margin is not None else cfg.bounds.shield.margin_um,
                )
            )
        ),
        width_um=(
            None
            if shield_raw.get("width_um", legacy_shield_width if legacy_shield_width is not None else cfg.bounds.shield.width_um) is None
            else float(
                shield_raw.get(
                    "width_um",
                    legacy_shield_width if legacy_shield_width is not None else cfg.bounds.shield.width_um,
                )
            )
        ),
    )

    emx_raw = raw.get("emx", {})
    shared_bridge_layer = emx_raw.get("bridge_layer")
    shared_bridge_via_layer = emx_raw.get("bridge_via_layer")
    resolved_process_file = str(emx_raw.get("emx_process_file", cfg.emx.emx_process_file))
    primary_coil_layer = int(
        emx_raw.get("primary_coil_layer", emx_raw.get("ap_layer", cfg.emx.primary_coil_layer))
    )
    secondary_coil_layer = int(
        emx_raw.get("secondary_coil_layer", emx_raw.get("m9_layer", cfg.emx.secondary_coil_layer))
    )
    primary_coil_layer = _normalize_to_preferred_draw_layer(
        proc_path=resolved_process_file,
        layer=primary_coil_layer,
    )
    secondary_coil_layer = _normalize_to_preferred_draw_layer(
        proc_path=resolved_process_file,
        layer=secondary_coil_layer,
    )
    primary_bridge_target_layer_raw = emx_raw.get(
        "primary_bridge_target_layer",
        emx_raw.get(
            "primary_bridge_layer",
            shared_bridge_layer if shared_bridge_layer is not None else cfg.emx.primary_bridge_lower_layer or cfg.emx.primary_bridge_layer,
        ),
    )
    secondary_bridge_target_layer_raw = emx_raw.get(
        "secondary_bridge_target_layer",
        emx_raw.get(
            "secondary_bridge_layer",
            shared_bridge_layer if shared_bridge_layer is not None else cfg.emx.secondary_bridge_lower_layer or cfg.emx.secondary_bridge_layer,
        ),
    )
    primary_bridge_layer, primary_bridge_via_layer, primary_bridge_lower_layer, primary_bridge_lower_via_layer = _resolve_route_fields(
        proc_path=resolved_process_file,
        coil_layer=primary_coil_layer,
        target_layer=None if primary_bridge_target_layer_raw is None else int(primary_bridge_target_layer_raw),
        explicit_layer=_optional_int_field(emx_raw, "primary_bridge_layer"),
        explicit_via_layer=(
            _optional_int_field(emx_raw, "primary_bridge_via_layer")
            if "primary_bridge_via_layer" in emx_raw
            else _optional_int_field(emx_raw, "bridge_via_layer")
        ),
        explicit_lower_layer=_optional_int_field(emx_raw, "primary_bridge_lower_layer"),
        explicit_lower_via_layer=_optional_int_field(emx_raw, "primary_bridge_lower_via_layer"),
    )
    secondary_bridge_layer, secondary_bridge_via_layer, secondary_bridge_lower_layer, secondary_bridge_lower_via_layer = _resolve_route_fields(
        proc_path=resolved_process_file,
        coil_layer=secondary_coil_layer,
        target_layer=None if secondary_bridge_target_layer_raw is None else int(secondary_bridge_target_layer_raw),
        explicit_layer=_optional_int_field(emx_raw, "secondary_bridge_layer"),
        explicit_via_layer=(
            _optional_int_field(emx_raw, "secondary_bridge_via_layer")
            if "secondary_bridge_via_layer" in emx_raw
            else _optional_int_field(emx_raw, "bridge_via_layer")
        ),
        explicit_lower_layer=_optional_int_field(emx_raw, "secondary_bridge_lower_layer"),
        explicit_lower_via_layer=_optional_int_field(emx_raw, "secondary_bridge_lower_via_layer"),
    )

    primary_center_tap = bool(
        primary_bounds_raw.get(
            "center_tap",
            topology_primary_raw.get(
                "center_tap",
                topology_raw.get("primary_center_tap", cfg.bounds.primary_center_tap),
            ),
        )
    )
    secondary_center_tap = bool(
        secondary_bounds_raw.get(
            "center_tap",
            topology_secondary_raw.get(
                "center_tap",
                topology_raw.get("secondary_center_tap", cfg.bounds.secondary_center_tap),
            ),
        )
    )
    primary_bounds_vdd_raw = dict(primary_bounds_raw.get("vdd_bar", {}) or {})
    secondary_bounds_vdd_raw = dict(secondary_bounds_raw.get("vdd_bar", {}) or {})
    topology_primary_vdd_raw = dict(topology_primary_raw.get("vdd_bar", {}) or {})
    topology_secondary_vdd_raw = dict(topology_secondary_raw.get("vdd_bar", {}) or {})
    primary_vdd_enabled = bool(
        primary_bounds_vdd_raw.get(
            "enabled",
            topology_primary_vdd_raw.get(
                "enabled",
                topology_raw.get(
                    "primary_vdd_bar_enabled",
                    False if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.enabled,
                ),
            ),
        )
    )
    secondary_vdd_enabled = bool(
        secondary_bounds_vdd_raw.get(
            "enabled",
            topology_secondary_vdd_raw.get(
                "enabled",
                topology_raw.get(
                    "secondary_vdd_bar_enabled",
                    False if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.enabled,
                ),
            ),
        )
    )
    if primary_vdd_enabled and not primary_center_tap:
        raise ValueError("primary_vdd_bar requires primary_center_tap to be enabled")
    if secondary_vdd_enabled and not secondary_center_tap:
        raise ValueError("secondary_vdd_bar requires secondary_center_tap to be enabled")
    primary_vdd_width_um = primary_bounds_vdd_raw.get(
        "width_um",
        topology_primary_vdd_raw.get(
            "width_um",
            topology_raw.get(
                "primary_vdd_bar_width_um",
                None if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.width_um,
            ),
        ),
    )
    secondary_vdd_width_um = secondary_bounds_vdd_raw.get(
        "width_um",
        topology_secondary_vdd_raw.get(
            "width_um",
            topology_raw.get(
                "secondary_vdd_bar_width_um",
                None if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.width_um,
            ),
        ),
    )
    primary_vdd_offset_um = primary_bounds_vdd_raw.get(
        "offset_um",
        topology_primary_vdd_raw.get(
            "offset_um",
            topology_raw.get(
                "primary_vdd_bar_offset_um",
                0.0 if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.offset_um,
            ),
        ),
    )
    secondary_vdd_offset_um = secondary_bounds_vdd_raw.get(
        "offset_um",
        topology_secondary_vdd_raw.get(
            "offset_um",
            topology_raw.get(
                "secondary_vdd_bar_offset_um",
                0.0 if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.offset_um,
            ),
        ),
    )

    primary_vdd_target_layer_raw = primary_bounds_vdd_raw.get(
        "bar_layer",
        topology_primary_vdd_raw.get(
            "bar_layer",
            topology_raw.get(
                "primary_vdd_bar_layer",
                None if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.bar_layer,
            ),
        ),
    )
    secondary_vdd_target_layer_raw = secondary_bounds_vdd_raw.get(
        "bar_layer",
        topology_secondary_vdd_raw.get(
            "bar_layer",
            topology_raw.get(
                "secondary_vdd_bar_layer",
                None if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.bar_layer,
            ),
        ),
    )
    primary_vdd_route_layer, primary_vdd_route_via_layer, primary_vdd_intermediate_layer, primary_vdd_bar_via_layer = _resolve_route_fields(
        proc_path=resolved_process_file,
        coil_layer=primary_coil_layer,
        target_layer=(
            None
            if not primary_vdd_enabled or primary_vdd_target_layer_raw is None
            else int(primary_vdd_target_layer_raw)
        ),
        explicit_layer=None,
        explicit_via_layer=None,
        explicit_lower_layer=None,
        explicit_lower_via_layer=None,
    )
    secondary_vdd_route_layer, secondary_vdd_route_via_layer, secondary_vdd_intermediate_layer, secondary_vdd_bar_via_layer = _resolve_route_fields(
        proc_path=resolved_process_file,
        coil_layer=secondary_coil_layer,
        target_layer=(
            None
            if not secondary_vdd_enabled or secondary_vdd_target_layer_raw is None
            else int(secondary_vdd_target_layer_raw)
        ),
        explicit_layer=None,
        explicit_via_layer=None,
        explicit_lower_layer=None,
        explicit_lower_via_layer=None,
    )
    via_layer_rules = _load_via_layer_rules(
        emx_raw.get("via_layer_rules"),
        cfg.emx.via_layer_rules,
    )
    via_family_rules = _load_via_family_rules(
        emx_raw.get("via_family_rules"),
        cfg.emx.via_family_rules,
    )
    emx = TransformerEmxConfig(
        emx_binary=str(emx_raw.get("emx_binary", cfg.emx.emx_binary)),
        emx_home=emx_raw.get("emx_home", cfg.emx.emx_home),
        emx_process_file=resolved_process_file,
        top_cell_prefix=str(emx_raw.get("top_cell_prefix", cfg.emx.top_cell_prefix)),
        extra_args=tuple(emx_raw.get("extra_args", cfg.emx.extra_args)),
        use_cadence_license_env=bool(
            emx_raw.get("use_cadence_license_env", cfg.emx.use_cadence_license_env)
        ),
        license_file=emx_raw.get("license_file", cfg.emx.license_file),
        cdslmd_license_file=emx_raw.get("cdslmd_license_file", cfg.emx.cdslmd_license_file),
        skip_os_check=bool(emx_raw.get("skip_os_check", cfg.emx.skip_os_check)),
        cadence_pin_purpose=(
            None
            if emx_raw.get("cadence_pin_purpose", cfg.emx.cadence_pin_purpose) is None
            else int(emx_raw.get("cadence_pin_purpose", cfg.emx.cadence_pin_purpose))
        ),
        cadence_install_root=str(
            emx_raw.get("cadence_install_root", cfg.emx.cadence_install_root)
        ),
        cadence_pdk_cds_lib=str(
            emx_raw.get("cadence_pdk_cds_lib", cfg.emx.cadence_pdk_cds_lib)
        ),
        cadence_tech_lib=str(
            emx_raw.get("cadence_tech_lib", cfg.emx.cadence_tech_lib)
        ),
        cadence_layer_map=str(
            emx_raw.get("cadence_layer_map", cfg.emx.cadence_layer_map)
        ),
        execution_mode=_coerce_emx_execution_mode(
            emx_raw.get("execution_mode", cfg.emx.execution_mode)
        ),
        remote_ssh_host=(
            None
            if emx_raw.get("remote_ssh_host", cfg.emx.remote_ssh_host) in (None, "")
            else str(emx_raw.get("remote_ssh_host", cfg.emx.remote_ssh_host))
        ),
        remote_repo_root=(
            None
            if emx_raw.get("remote_repo_root", cfg.emx.remote_repo_root) in (None, "")
            else str(emx_raw.get("remote_repo_root", cfg.emx.remote_repo_root))
        ),
        remote_work_root=str(
            emx_raw.get("remote_work_root", cfg.emx.remote_work_root)
        ),
        remote_python=str(
            emx_raw.get("remote_python", cfg.emx.remote_python)
        ),
        remote_ssh_command=str(
            emx_raw.get("remote_ssh_command", cfg.emx.remote_ssh_command)
        ),
        remote_scp_command=str(
            emx_raw.get("remote_scp_command", cfg.emx.remote_scp_command)
        ),
        remote_venv_activate=(
            None
            if emx_raw.get("remote_venv_activate", cfg.emx.remote_venv_activate) in (None, "")
            else str(emx_raw.get("remote_venv_activate", cfg.emx.remote_venv_activate))
        ),
        remote_emx_process_file=(
            None
            if emx_raw.get("remote_emx_process_file", cfg.emx.remote_emx_process_file) in (None, "")
            else str(emx_raw.get("remote_emx_process_file", cfg.emx.remote_emx_process_file))
        ),
        port_mode=_coerce_emx_port_mode(emx_raw.get("port_mode", cfg.emx.port_mode)),
        differential_port_pairs=_parse_differential_port_pairs(
            emx_raw.get("differential_port_pairs", cfg.emx.differential_port_pairs)
        ),
        ground_unused_s8p_ports=bool(
            emx_raw.get("ground_unused_s8p_ports", cfg.emx.ground_unused_s8p_ports)
        ),
        power_line_8port=_load_power_line_8port_spec(
            emx_raw.get("power_line_8port", cfg.emx.power_line_8port)
        ),
        ap_layer=primary_coil_layer,
        m9_layer=secondary_coil_layer,
        m5_layer=int(emx_raw.get("m5_layer", cfg.emx.m5_layer)),
        primary_bridge_layer=int(primary_bridge_layer if primary_bridge_layer is not None else cfg.emx.primary_bridge_layer),
        primary_bridge_via_layer=int(primary_bridge_via_layer if primary_bridge_via_layer is not None else cfg.emx.primary_bridge_via_layer),
        primary_bridge_lower_layer=primary_bridge_lower_layer,
        primary_bridge_lower_via_layer=primary_bridge_lower_via_layer,
        secondary_bridge_layer=int(secondary_bridge_layer if secondary_bridge_layer is not None else cfg.emx.secondary_bridge_layer),
        secondary_bridge_via_layer=int(secondary_bridge_via_layer if secondary_bridge_via_layer is not None else cfg.emx.secondary_bridge_via_layer),
        secondary_bridge_lower_layer=secondary_bridge_lower_layer,
        secondary_bridge_lower_via_layer=secondary_bridge_lower_via_layer,
        shield_layer=(
            None
            if emx_raw.get("shield_layer", emx_raw.get("shielding_layer", cfg.emx.shield_layer)) is None
            else int(emx_raw.get("shield_layer", emx_raw.get("shielding_layer", cfg.emx.shield_layer)))
        ),
        metal_datatype=int(emx_raw.get("metal_datatype", cfg.emx.metal_datatype)),
        label_layer=int(emx_raw.get("label_layer", cfg.emx.label_layer)),
        label_datatype=int(emx_raw.get("label_datatype", cfg.emx.label_datatype)),
        via_layer_rules=via_layer_rules,
        via_family_rules=via_family_rules,
        enable_large_plate_warnings=bool(
            emx_raw.get("enable_large_plate_warnings", cfg.emx.enable_large_plate_warnings)
        ),
    )
    _validate_power_line_8port_config(emx)

    legacy_bounds = {
        "primary_outer_width_um": primary_bounds_raw.get("outer_width_um", bounds_raw.get("primary_outer_width_um", bounds_raw.get("outer_width_um", bounds_raw.get("outer_diameter_um")))),
        "primary_outer_height_um": primary_bounds_raw.get("outer_height_um", bounds_raw.get("primary_outer_height_um", bounds_raw.get("outer_height_um", bounds_raw.get("outer_diameter_um")))),
        "secondary_outer_width_um": secondary_bounds_raw.get("outer_width_um", bounds_raw.get("secondary_outer_width_um", bounds_raw.get("outer_width_um", bounds_raw.get("outer_diameter_um")))),
        "secondary_outer_height_um": secondary_bounds_raw.get("outer_height_um", bounds_raw.get("secondary_outer_height_um", bounds_raw.get("outer_height_um", bounds_raw.get("outer_diameter_um")))),
        "offset_um": bounds_raw.get("offset_um", bounds_raw.get("secondary_offset_um")),
        "primary_feed_extension_um": primary_bounds_raw.get("feed_extension_um", bounds_raw.get("primary_feed_extension_um", bounds_raw.get("feed_extension_um"))),
        "secondary_feed_extension_um": secondary_bounds_raw.get("feed_extension_um", bounds_raw.get("secondary_feed_extension_um", bounds_raw.get("feed_extension_um"))),
        "primary_width_um": primary_bounds_raw.get("trace_width_um", bounds_raw.get("primary_width_um")),
        "primary_spacing_um": primary_bounds_raw.get("spacing_um", bounds_raw.get("primary_spacing_um")),
        "primary_terminal_y_span_um": primary_bounds_raw.get("terminal_y_span_um", bounds_raw.get("primary_terminal_y_span_um")),
        "secondary_width_um": secondary_bounds_raw.get("trace_width_um", bounds_raw.get("secondary_width_um")),
        "secondary_spacing_um": secondary_bounds_raw.get("spacing_um", bounds_raw.get("secondary_spacing_um")),
        "secondary_terminal_y_span_um": secondary_bounds_raw.get("terminal_y_span_um", bounds_raw.get("secondary_terminal_y_span_um")),
    }

    def _resolve_bound(name: str) -> tuple[float, float]:
        value = legacy_bounds.get(name)
        if value is None:
            value = bounds_raw.get(name, getattr(cfg.bounds, name))
        return tuple(map(float, value))

    primary_turns = int(
        primary_bounds_raw.get(
            "turns",
            topology_primary_raw.get(
                "turns",
                topology_raw.get("primary_turns", cfg.bounds.primary_turns),
            ),
        )
    )
    secondary_turns = int(
        secondary_bounds_raw.get(
            "turns",
            topology_secondary_raw.get(
                "turns",
                topology_raw.get("secondary_turns", cfg.bounds.secondary_turns),
            ),
        )
    )
    default_primary_bridge_section = _default_primary_bridge_section(chosen_topology, turns=primary_turns)
    default_secondary_bridge_section = _default_secondary_bridge_section(chosen_topology, turns=secondary_turns)
    current_primary_bridge_section = cfg.bounds.primary.bridge_section or default_primary_bridge_section
    current_secondary_bridge_section = cfg.bounds.secondary.bridge_section or default_secondary_bridge_section

    if primary_turns <= 1:
        primary_bridge_section_pad_width_ratio = None
        primary_bridge_section_pad_height_ratio = None
        primary_bridge_section_via_size_ratio = None
        primary_bridge_section_via_width_ratio = None
        primary_bridge_section_via_spacing_ratio = None
    else:
        primary_bridge_section_pad_width_ratio_raw = bounds_raw.get(
            "primary_bridge_section_pad_width_ratio",
            (primary_bounds_raw.get("bridge_section", {}) or {}).get(
                "pad_width_ratio",
                topology_raw.get(
                    "primary_bridge_section_pad_width_ratio",
                    None
                    if current_primary_bridge_section is None
                    else current_primary_bridge_section.pad_width_ratio,
                ),
            ),
        )
        primary_bridge_section_pad_width_ratio = (
            None
            if primary_bridge_section_pad_width_ratio_raw is None
            else float(primary_bridge_section_pad_width_ratio_raw)
        )
        primary_bridge_section_pad_height_ratio_raw = bounds_raw.get(
            "primary_bridge_section_pad_height_ratio",
            (primary_bounds_raw.get("bridge_section", {}) or {}).get(
                "pad_height_ratio",
                topology_raw.get(
                "primary_bridge_section_pad_height_ratio",
                None
                if current_primary_bridge_section is None
                else current_primary_bridge_section.pad_height_ratio,
                ),
            ),
        )
        primary_bridge_section_pad_height_ratio = (
            None
            if primary_bridge_section_pad_height_ratio_raw is None
            else float(primary_bridge_section_pad_height_ratio_raw)
        )
        primary_bridge_section_via_size_ratio_raw = bounds_raw.get(
            "primary_bridge_section_via_size_ratio",
            (primary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_size_ratio",
                topology_raw.get(
                    "primary_bridge_section_via_size_ratio",
                    None
                    if current_primary_bridge_section is None
                    else current_primary_bridge_section.via_size_ratio,
                ),
            ),
        )
        primary_bridge_section_via_size_ratio = (
            None
            if primary_bridge_section_via_size_ratio_raw is None
            else float(primary_bridge_section_via_size_ratio_raw)
        )
        primary_bridge_section_via_width_ratio_raw = bounds_raw.get(
            "primary_bridge_section_via_width_ratio",
            (primary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_width_ratio",
                topology_raw.get(
                    "primary_bridge_section_via_width_ratio",
                    None
                    if current_primary_bridge_section is None
                    else current_primary_bridge_section.via_width_ratio,
                ),
            ),
        )
        primary_bridge_section_via_width_ratio = (
            None
            if primary_bridge_section_via_width_ratio_raw is None
            else float(primary_bridge_section_via_width_ratio_raw)
        )
        primary_bridge_section_via_spacing_ratio_raw = bounds_raw.get(
            "primary_bridge_section_via_spacing_ratio",
            (primary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_spacing_ratio",
                topology_raw.get(
                    "primary_bridge_section_via_spacing_ratio",
                    None
                    if current_primary_bridge_section is None
                    else current_primary_bridge_section.via_spacing_ratio,
                ),
            ),
        )
        primary_bridge_section_via_spacing_ratio = (
            None
            if primary_bridge_section_via_spacing_ratio_raw is None
            else float(primary_bridge_section_via_spacing_ratio_raw)
        )

    if secondary_turns <= 1:
        secondary_bridge_section_pad_width_ratio = None
        secondary_bridge_section_pad_height_ratio = None
        secondary_bridge_section_via_size_ratio = None
        secondary_bridge_section_via_width_ratio = None
        secondary_bridge_section_via_spacing_ratio = None
    else:
        secondary_bridge_section_pad_width_ratio_raw = bounds_raw.get(
            "secondary_bridge_section_pad_width_ratio",
            (secondary_bounds_raw.get("bridge_section", {}) or {}).get(
                "pad_width_ratio",
                topology_raw.get(
                    "secondary_bridge_section_pad_width_ratio",
                    None
                    if current_secondary_bridge_section is None
                    else current_secondary_bridge_section.pad_width_ratio,
                ),
            ),
        )
        secondary_bridge_section_pad_width_ratio = (
            None
            if secondary_bridge_section_pad_width_ratio_raw is None
            else float(secondary_bridge_section_pad_width_ratio_raw)
        )
        secondary_bridge_section_pad_height_ratio_raw = bounds_raw.get(
            "secondary_bridge_section_pad_height_ratio",
            (secondary_bounds_raw.get("bridge_section", {}) or {}).get(
                "pad_height_ratio",
                topology_raw.get(
                "secondary_bridge_section_pad_height_ratio",
                None
                if current_secondary_bridge_section is None
                else current_secondary_bridge_section.pad_height_ratio,
                ),
            ),
        )
        secondary_bridge_section_pad_height_ratio = (
            None
            if secondary_bridge_section_pad_height_ratio_raw is None
            else float(secondary_bridge_section_pad_height_ratio_raw)
        )
        secondary_bridge_section_via_size_ratio_raw = bounds_raw.get(
            "secondary_bridge_section_via_size_ratio",
            (secondary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_size_ratio",
                topology_raw.get(
                    "secondary_bridge_section_via_size_ratio",
                    None
                    if current_secondary_bridge_section is None
                    else current_secondary_bridge_section.via_size_ratio,
                ),
            ),
        )
        secondary_bridge_section_via_size_ratio = (
            None
            if secondary_bridge_section_via_size_ratio_raw is None
            else float(secondary_bridge_section_via_size_ratio_raw)
        )
        secondary_bridge_section_via_width_ratio_raw = bounds_raw.get(
            "secondary_bridge_section_via_width_ratio",
            (secondary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_width_ratio",
                topology_raw.get(
                    "secondary_bridge_section_via_width_ratio",
                    None
                    if current_secondary_bridge_section is None
                    else current_secondary_bridge_section.via_width_ratio,
                ),
            ),
        )
        secondary_bridge_section_via_width_ratio = (
            None
            if secondary_bridge_section_via_width_ratio_raw is None
            else float(secondary_bridge_section_via_width_ratio_raw)
        )
        secondary_bridge_section_via_spacing_ratio_raw = bounds_raw.get(
            "secondary_bridge_section_via_spacing_ratio",
            (secondary_bounds_raw.get("bridge_section", {}) or {}).get(
                "via_spacing_ratio",
                topology_raw.get(
                    "secondary_bridge_section_via_spacing_ratio",
                    None
                    if current_secondary_bridge_section is None
                    else current_secondary_bridge_section.via_spacing_ratio,
                ),
            ),
        )
        secondary_bridge_section_via_spacing_ratio = (
            None
            if secondary_bridge_section_via_spacing_ratio_raw is None
            else float(secondary_bridge_section_via_spacing_ratio_raw)
        )
    bounds = TransformerSearchSpace(
        primary=InductorBounds(
            outer_width_um=_resolve_bound("primary_outer_width_um"),
            outer_height_um=_resolve_bound("primary_outer_height_um"),
            trace_width_um=_resolve_bound("primary_width_um"),
            spacing_um=_resolve_bound("primary_spacing_um"),
            terminal_y_span_um=_resolve_bound("primary_terminal_y_span_um"),
            feed_extension_um=_resolve_bound("primary_feed_extension_um"),
            turns=primary_turns,
            center_tap=primary_center_tap,
            bridge_layer=emx.primary_bridge_layer if primary_turns > 1 else None,
            bridge_via_layer=emx.primary_bridge_via_layer if primary_turns > 1 else None,
            bridge_lower_layer=emx.primary_bridge_lower_layer if primary_turns > 1 else None,
            bridge_lower_via_layer=emx.primary_bridge_lower_via_layer if primary_turns > 1 else None,
            vdd_bar=VddBarSpec(
                enabled=primary_vdd_enabled,
                width_um=None if primary_vdd_width_um is None else float(primary_vdd_width_um),
                offset_um=float(primary_vdd_offset_um),
                bar_layer=(
                    None
                    if primary_vdd_target_layer_raw is None
                    else int(primary_vdd_target_layer_raw)
                ),
                route_layer=primary_vdd_route_layer,
                route_via_layer=primary_vdd_route_via_layer,
                bar_via_layer=primary_vdd_bar_via_layer,
            ),
            bridge_section=(
                None
                if primary_turns <= 1
                else BridgeSectionConfig(
                    pad_width_ratio=(
                        current_primary_bridge_section.pad_width_ratio
                        if primary_bridge_section_pad_width_ratio is None and current_primary_bridge_section is not None
                        else (0.70 if primary_bridge_section_pad_width_ratio is None else primary_bridge_section_pad_width_ratio)
                    ),
                    pad_height_ratio=(
                        current_primary_bridge_section.pad_height_ratio
                        if primary_bridge_section_pad_height_ratio is None and current_primary_bridge_section is not None
                        else (
                            1.00 if primary_bridge_section_pad_height_ratio is None else primary_bridge_section_pad_height_ratio
                        )
                    ),
                    via_size_ratio=(
                        current_primary_bridge_section.via_size_ratio
                        if primary_bridge_section_via_size_ratio is None and current_primary_bridge_section is not None
                        else (0.60 if primary_bridge_section_via_size_ratio is None else primary_bridge_section_via_size_ratio)
                    ),
                    via_width_ratio=(
                        current_primary_bridge_section.via_width_ratio
                        if primary_bridge_section_via_width_ratio is None and current_primary_bridge_section is not None
                        else (0.35 if primary_bridge_section_via_width_ratio is None else primary_bridge_section_via_width_ratio)
                    ),
                    via_spacing_ratio=(
                        current_primary_bridge_section.via_spacing_ratio
                        if primary_bridge_section_via_spacing_ratio is None and current_primary_bridge_section is not None
                        else (
                            0.50
                            if primary_bridge_section_via_spacing_ratio is None
                            else primary_bridge_section_via_spacing_ratio
                        )
                    ),
                )
            ),
        ),
        secondary=InductorBounds(
            outer_width_um=_resolve_bound("secondary_outer_width_um"),
            outer_height_um=_resolve_bound("secondary_outer_height_um"),
            trace_width_um=_resolve_bound("secondary_width_um"),
            spacing_um=_resolve_bound("secondary_spacing_um"),
            terminal_y_span_um=_resolve_bound("secondary_terminal_y_span_um"),
            feed_extension_um=_resolve_bound("secondary_feed_extension_um"),
            turns=secondary_turns,
            center_tap=secondary_center_tap,
            bridge_layer=emx.secondary_bridge_layer if secondary_turns > 1 else None,
            bridge_via_layer=emx.secondary_bridge_via_layer if secondary_turns > 1 else None,
            bridge_lower_layer=emx.secondary_bridge_lower_layer if secondary_turns > 1 else None,
            bridge_lower_via_layer=emx.secondary_bridge_lower_via_layer if secondary_turns > 1 else None,
            vdd_bar=VddBarSpec(
                enabled=secondary_vdd_enabled,
                width_um=None if secondary_vdd_width_um is None else float(secondary_vdd_width_um),
                offset_um=float(secondary_vdd_offset_um),
                bar_layer=(
                    None
                    if secondary_vdd_target_layer_raw is None
                    else int(secondary_vdd_target_layer_raw)
                ),
                route_layer=secondary_vdd_route_layer,
                route_via_layer=secondary_vdd_route_via_layer,
                bar_via_layer=secondary_vdd_bar_via_layer,
            ),
            bridge_section=(
                None
                if secondary_turns <= 1
                else BridgeSectionConfig(
                    pad_width_ratio=(
                        current_secondary_bridge_section.pad_width_ratio
                        if secondary_bridge_section_pad_width_ratio is None and current_secondary_bridge_section is not None
                        else (
                            0.70 if secondary_bridge_section_pad_width_ratio is None else secondary_bridge_section_pad_width_ratio
                        )
                    ),
                    pad_height_ratio=(
                        current_secondary_bridge_section.pad_height_ratio
                        if secondary_bridge_section_pad_height_ratio is None and current_secondary_bridge_section is not None
                        else (
                            1.00 if secondary_bridge_section_pad_height_ratio is None else secondary_bridge_section_pad_height_ratio
                        )
                    ),
                    via_size_ratio=(
                        current_secondary_bridge_section.via_size_ratio
                        if secondary_bridge_section_via_size_ratio is None and current_secondary_bridge_section is not None
                        else (
                            0.60 if secondary_bridge_section_via_size_ratio is None else secondary_bridge_section_via_size_ratio
                        )
                    ),
                    via_width_ratio=(
                        current_secondary_bridge_section.via_width_ratio
                        if secondary_bridge_section_via_width_ratio is None and current_secondary_bridge_section is not None
                        else (
                            0.35 if secondary_bridge_section_via_width_ratio is None else secondary_bridge_section_via_width_ratio
                        )
                    ),
                    via_spacing_ratio=(
                        current_secondary_bridge_section.via_spacing_ratio
                        if secondary_bridge_section_via_spacing_ratio is None and current_secondary_bridge_section is not None
                        else (
                            0.50
                            if secondary_bridge_section_via_spacing_ratio is None
                            else secondary_bridge_section_via_spacing_ratio
                        )
                    ),
                )
            ),
        ),
        offset_um=_resolve_bound("offset_um"),
        topology_mode=chosen_topology,
        shield=shield,
    )

    optimizer = _load_optimizer_config(raw.get("optimizer", {}), cfg.optimizer)

    return TransformerRunConfig(target=target, bounds=bounds, emx=emx, optimizer=optimizer)


def load_run_config(path: str | Path | None = None) -> TransformerRunConfig:
    if path is None:
        return default_run_config(topology_mode="1t1t")

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return load_run_config_from_raw(raw)


def _parse_differential_port_pairs(value: object) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str):
            first, second = value.split(":", 1)
            pairs = (
                tuple(int(item.strip()) for item in first.split(",", 1)),
                tuple(int(item.strip()) for item in second.split(",", 1)),
            )
        else:
            raw_pairs = list(value)  # type: ignore[arg-type]
            pairs = tuple(tuple(int(item) for item in pair) for pair in raw_pairs)
    except Exception as exc:
        raise ValueError(
            "emx.differential_port_pairs must be written as '1,2:7,8' or [[1, 2], [7, 8]]"
        ) from exc
    if len(pairs) != 2 or any(len(pair) != 2 for pair in pairs):
        raise ValueError("emx.differential_port_pairs must contain exactly two two-port pairs")
    flat = [port for pair in pairs for port in pair]
    if len(set(flat)) != 4:
        raise ValueError("emx.differential_port_pairs must use four distinct 1-based port numbers")
    if any(port <= 0 for port in flat):
        raise ValueError("emx.differential_port_pairs must use positive 1-based port numbers")
    return tuple(tuple(port - 1 for port in pair) for pair in pairs)  # type: ignore[return-value]


def _load_power_line_8port_spec(value: object) -> PowerLine8PortSpec:
    if isinstance(value, PowerLine8PortSpec):
        return value
    raw = dict(value or {})
    port_map_raw = raw.get("port_map", ())
    if isinstance(port_map_raw, str):
        port_map = tuple(item.strip() for item in port_map_raw.split(",") if item.strip())
    else:
        port_map = tuple(str(item) for item in port_map_raw)
    role_labels_raw = raw.get("role_labels", raw.get("role_port_map", ()))
    if isinstance(role_labels_raw, dict):
        role_labels = tuple((str(key), str(value)) for key, value in role_labels_raw.items())
    else:
        role_labels = tuple((str(item[0]), str(item[1])) for item in role_labels_raw or ())
    return PowerLine8PortSpec(
        enabled=bool(raw.get("enabled", False)),
        touchstone_mode=str(raw.get("touchstone_mode", "all_ports_s8p")),  # type: ignore[arg-type]
        bridge_width_um=(
            None
            if raw.get("bridge_width_um", raw.get("bridge_width")) is None
            else float(raw.get("bridge_width_um", raw.get("bridge_width")))
        ),
        vertical_length_diameter_ratio=float(raw.get("vertical_length_diameter_ratio", 1.5)),
        bridge_y_policy=str(raw.get("bridge_y_policy", "center")),  # type: ignore[arg-type]
        bridge_motion_axis=str(raw.get("bridge_motion_axis", "x_only")),  # type: ignore[arg-type]
        port_ground_reference=str(raw.get("port_ground_reference", "shield")),  # type: ignore[arg-type]
        port_map=port_map,
        role_labels=role_labels,
    )


def _validate_power_line_8port_config(emx: TransformerEmxConfig) -> None:
    spec = emx.power_line_8port
    if not spec.enabled:
        return
    errors: list[str] = []
    if emx.port_mode != "single_ended_shield_grounded":
        errors.append("emx.port_mode must be single_ended_shield_grounded")
    signal_only_s4p = spec.touchstone_mode == "signal_4_grounded_aux"
    if spec.touchstone_mode not in ("all_ports_s8p", "signal_4_grounded_aux"):
        errors.append("emx.power_line_8port.touchstone_mode must be all_ports_s8p or signal_4_grounded_aux")
    if emx.differential_port_pairs is None and not signal_only_s4p:
        errors.append("emx.differential_port_pairs must be provided for .s8p physical feature extraction")
    if spec.bridge_width_um is None or float(spec.bridge_width_um) <= 0.0:
        errors.append("emx.power_line_8port.bridge_width_um must be explicit and positive")
    if abs(float(spec.vertical_length_diameter_ratio) - 1.5) > 1.0e-12:
        errors.append("emx.power_line_8port.vertical_length_diameter_ratio must remain 1.5")
    if spec.bridge_y_policy != "center":
        errors.append("emx.power_line_8port.bridge_y_policy must be center")
    if spec.bridge_motion_axis != "x_only":
        errors.append("emx.power_line_8port.bridge_motion_axis must be x_only")
    if spec.port_ground_reference != "shield":
        errors.append("emx.power_line_8port.port_ground_reference must be shield")
    expected_port_count = 4 if signal_only_s4p else 8
    if len(spec.port_map) != expected_port_count:
        errors.append(
            f"emx.power_line_8port.port_map must list exactly {expected_port_count} "
            + ("exported signal port labels" if signal_only_s4p else "physical port labels")
        )
    elif len(set(spec.port_map)) != expected_port_count:
        errors.append(f"emx.power_line_8port.port_map must contain {expected_port_count} distinct labels")
    expected_roles = {
        "left_power_top",
        "left_power_bottom",
        "primary_top",
        "primary_bottom",
        "secondary_top",
        "secondary_bottom",
        "right_power_top",
        "right_power_bottom",
    }
    if not spec.role_labels:
        errors.append("emx.power_line_8port.role_labels must explicitly map every physical role to the approved P001-P008 order")
    else:
        role_map = dict(spec.role_labels)
        if set(role_map) != expected_roles:
            errors.append(
                "emx.power_line_8port.role_labels must define exactly: "
                + ", ".join(sorted(expected_roles))
            )
        elif len(set(role_map.values())) != 8:
            errors.append("emx.power_line_8port.role_labels must contain 8 distinct labels")
        elif signal_only_s4p:
            signal_roles = {"primary_top", "primary_bottom", "secondary_top", "secondary_bottom"}
            if {role_map[role] for role in signal_roles} != set(spec.port_map):
                errors.append("emx.power_line_8port signal role labels must match the 4 exported .s4p port_map labels")
            auxiliary_roles = expected_roles - signal_roles
            if set(role_map[role] for role in auxiliary_roles) & set(spec.port_map):
                errors.append("emx.power_line_8port auxiliary power-line role labels must not be exported .s4p ports")
        elif set(role_map.values()) != set(spec.port_map):
            errors.append("emx.power_line_8port.role_labels values must match port_map labels")
    if errors:
        raise ValueError("power_line_8port configuration is incomplete: " + "; ".join(errors))


def _load_optimizer_config(raw: dict[str, object], defaults: TransformerOptimizerConfig) -> TransformerOptimizerConfig:
    opt_raw = dict(raw or {})
    removed_names = {"bads", "nomad", "legacy_de_powell"}
    removed_sections = removed_names | {"de_popsize", "de_maxiter", "polish_maxfev"}
    if unsupported := sorted(field for field in removed_sections if field in opt_raw):
        raise ValueError(
            "Unsupported transformer optimizer configuration fields: "
            + ", ".join(unsupported)
            + ". Only 'cma_es' and 'turbo' are supported."
        )

    name = str(opt_raw.get("name", defaults.name))
    if name not in {"cma_es", "turbo"}:
        raise ValueError(f"Unsupported transformer optimizer backend '{name}'. Only 'cma_es' and 'turbo' are supported.")
    cma_raw = dict(opt_raw.get("cma_es", {}) or {})
    turbo_raw = dict(opt_raw.get("turbo", {}) or {})

    warm_start_paths_raw = opt_raw.get("warm_start_paths", defaults.warm_start_paths)
    if warm_start_paths_raw in (None, ""):
        warm_start_paths = tuple()
    elif isinstance(warm_start_paths_raw, str):
        warm_start_paths = (str(warm_start_paths_raw),)
    else:
        warm_start_paths = tuple(str(path) for path in warm_start_paths_raw)

    return TransformerOptimizerConfig(
        name=name,
        max_evaluations=int(opt_raw.get("max_evaluations", defaults.max_evaluations)),
        warm_start_samples=int(opt_raw.get("warm_start_samples", defaults.warm_start_samples)),
        warm_start_paths=warm_start_paths,
        seed=int(opt_raw.get("seed", defaults.seed)),
        resume_from_checkpoint=bool(opt_raw.get("resume_from_checkpoint", defaults.resume_from_checkpoint)),
        checkpoint_interval_evaluations=int(
            opt_raw.get("checkpoint_interval_evaluations", defaults.checkpoint_interval_evaluations)
        ),
        cma_es=CMAESOptimizerConfig(
            population_size=(
                None
                if cma_raw.get("population_size", defaults.cma_es.population_size) is None
                else int(cma_raw.get("population_size", defaults.cma_es.population_size))
            ),
            sigma0=(
                None
                if cma_raw.get("sigma0", defaults.cma_es.sigma0) is None
                else float(cma_raw.get("sigma0", defaults.cma_es.sigma0))
            ),
            verbose=int(cma_raw.get("verbose", defaults.cma_es.verbose)),
        ),
        turbo=TuRBOOptimizerConfig(
            initial_length=float(turbo_raw.get("initial_length", defaults.turbo.initial_length)),
            length_min=float(turbo_raw.get("length_min", defaults.turbo.length_min)),
            length_max=float(turbo_raw.get("length_max", defaults.turbo.length_max)),
            success_tolerance=int(turbo_raw.get("success_tolerance", defaults.turbo.success_tolerance)),
            num_restarts=int(turbo_raw.get("num_restarts", defaults.turbo.num_restarts)),
            raw_samples=int(turbo_raw.get("raw_samples", defaults.turbo.raw_samples)),
            n_candidates=(
                None
                if turbo_raw.get("n_candidates", defaults.turbo.n_candidates) is None
                else int(turbo_raw.get("n_candidates", defaults.turbo.n_candidates))
            ),
            max_cholesky_size=float(turbo_raw.get("max_cholesky_size", defaults.turbo.max_cholesky_size)),
            acquisition_function=str(turbo_raw.get("acquisition_function", defaults.turbo.acquisition_function)),
        ),
    )
