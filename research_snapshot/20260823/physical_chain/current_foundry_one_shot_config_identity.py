#!/usr/bin/env python3
"""Audit the exact foundry/topology contract used by one-shot MLP validation."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml


SCHEMA = "current_foundry_one_shot_config_identity_v1"
HFSS_PORT_SCHEMA = "current_foundry_hfss_port_contract_v1"
EXPECTED_CONFIG_SHA256 = (
    "996ebad95e407b5f959b3807e1a2b90fad5599c54a7b6745fcd24ce82c4708e0"
)
EXPECTED_SIGNAL_PORTS = ("P001", "P002", "P003", "P004")
EXPECTED_AUXILIARY_PORTS = ("P005", "P006", "P007", "P008")
EXPECTED_DIFFERENTIAL_PORT_PAIRS_ONE_BASED = ((1, 2), (3, 4))
EXPECTED_DIFFERENTIAL_PORT_PAIRS_ZERO_BASED = ((0, 1), (2, 3))
EXPECTED_DIFFERENTIAL_PAIR_LABELS = (
    ("P001", "P002"),
    ("P003", "P004"),
)
EXPECTED_DIFFERENTIAL_PAIR_ROLES = (
    ("primary_top", "primary_bottom"),
    ("secondary_top", "secondary_bottom"),
)
EXPECTED_ROLE_LABELS = {
    "primary_top": "P001",
    "primary_bottom": "P002",
    "secondary_top": "P003",
    "secondary_bottom": "P004",
    "left_power_top": "P005",
    "left_power_bottom": "P006",
    "right_power_top": "P007",
    "right_power_bottom": "P008",
}


def audit_hfss_port_contract(
    config_path: Path,
    *,
    expected_config_sha256: str = EXPECTED_CONFIG_SHA256,
) -> dict[str, Any]:
    """Bind HFSS/Touchstone ordering to the frozen foundry configuration."""

    config_identity = audit_config_identity(
        config_path,
        expected_config_sha256=expected_config_sha256,
    )
    checks: dict[str, bool] = {
        "config_identity_pass": config_identity.get("overall_status") == "PASS",
        "config_identity_checks_all_pass": bool(
            config_identity.get("checks")
        )
        and all(
            value is True
            for value in config_identity.get("checks", {}).values()
        ),
    }
    errors: list[str] = []
    raw_pair_text = ""
    one_based_pairs: tuple[tuple[int, int], ...] = ()
    zero_based_pairs: tuple[tuple[int, int], ...] = ()
    signal_ports: tuple[str, ...] = ()
    pair_labels: tuple[tuple[str, str], ...] = ()
    pair_roles: tuple[tuple[str, str], ...] = ()
    role_labels: dict[str, str] = {}
    try:
        if not checks["config_identity_pass"]:
            raise ValueError("frozen current-foundry config identity failed")
        config = yaml.safe_load(
            config_path.expanduser().resolve().read_text(encoding="utf-8")
        )
        if not isinstance(config, dict):
            raise TypeError("configuration root must be a mapping")
        emx = _mapping(config.get("emx"))
        power = _mapping(emx.get("power_line_8port"))
        raw_roles = _mapping(power.get("role_labels"))
        role_labels = {
            key: str(raw_roles.get(key) or "")
            for key in EXPECTED_ROLE_LABELS
        }
        signal_ports = tuple(
            str(value) for value in power.get("port_map") or ()
        )
        raw_pair_text = str(emx.get("differential_port_pairs") or "")
        one_based_pairs = _parse_differential_port_pairs(raw_pair_text)
        zero_based_pairs = tuple(
            (positive - 1, negative - 1)
            for positive, negative in one_based_pairs
        )
        if len(signal_ports) == 4:
            pair_labels = tuple(
                (signal_ports[positive - 1], signal_ports[negative - 1])
                for positive, negative in one_based_pairs
            )
        pair_roles = tuple(
            (role_labels[positive], role_labels[negative])
            for positive, negative in EXPECTED_DIFFERENTIAL_PAIR_ROLES
        )
        checks.update(
            {
                "touchstone_signal_port_order_exact": (
                    signal_ports == EXPECTED_SIGNAL_PORTS
                ),
                "differential_port_pairs_exact": (
                    one_based_pairs
                    == EXPECTED_DIFFERENTIAL_PORT_PAIRS_ONE_BASED
                    and zero_based_pairs
                    == EXPECTED_DIFFERENTIAL_PORT_PAIRS_ZERO_BASED
                ),
                "differential_pair_labels_exact": (
                    pair_labels == EXPECTED_DIFFERENTIAL_PAIR_LABELS
                ),
                "differential_pair_roles_exact": (
                    pair_roles == EXPECTED_DIFFERENTIAL_PAIR_LABELS
                ),
                "auxiliary_ports_grounded_to_shield": (
                    tuple(
                        role_labels[name]
                        for name in (
                            "left_power_top",
                            "left_power_bottom",
                            "right_power_top",
                            "right_power_bottom",
                        )
                    )
                    == EXPECTED_AUXILIARY_PORTS
                    and str(power.get("port_ground_reference") or "")
                    == "shield"
                ),
                "touchstone_is_four_port_s4p": (
                    str(power.get("touchstone_mode") or "")
                    == "signal_4_grounded_aux"
                    and len(signal_ports) == 4
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - exact failure is audit evidence.
        errors.append(f"{type(exc).__name__}: {exc}")

    status = "PASS" if checks and all(checks.values()) and not errors else "FAIL"
    return {
        "schema": HFSS_PORT_SCHEMA,
        "overall_status": status,
        "config_identity": config_identity,
        "touchstone_extension": ".s4p",
        "touchstone_port_count": 4,
        "touchstone_port_order": list(signal_ports),
        "differential_port_pairs_source": raw_pair_text,
        "differential_port_pairs_one_based": [
            list(pair) for pair in one_based_pairs
        ],
        "differential_port_pairs_zero_based": [
            list(pair) for pair in zero_based_pairs
        ],
        "differential_pair_labels": [
            list(pair) for pair in pair_labels
        ],
        "differential_pair_roles": [
            list(pair) for pair in EXPECTED_DIFFERENTIAL_PAIR_ROLES
        ],
        "auxiliary_ports": list(EXPECTED_AUXILIARY_PORTS),
        "auxiliary_port_treatment": "grounded_to_shield_not_exported",
        "checks": checks,
        "errors": errors,
    }


def audit_config_identity(
    config_path: Path,
    *,
    expected_config_sha256: str = EXPECTED_CONFIG_SHA256,
) -> dict[str, Any]:
    """Return a fail-closed identity audit without resolving remote PDK files."""

    config_path = config_path.expanduser().resolve()
    expected_sha = str(expected_config_sha256 or "").strip().lower()
    checks: dict[str, bool] = {
        "config_exists_nonzero": (
            config_path.is_file() and config_path.stat().st_size > 0
        ),
        "expected_config_sha256_is_valid": _is_sha256(expected_sha),
    }
    errors: list[str] = []
    raw: dict[str, Any] = {}
    actual_sha = ""
    identity: dict[str, Any] = {}
    try:
        if not checks["config_exists_nonzero"]:
            raise FileNotFoundError(config_path)
        actual_sha = _sha256(config_path)
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("configuration root must be a mapping")
        raw = loaded

        target = _mapping(raw.get("target"))
        topology = _mapping(raw.get("topology"))
        primary = _mapping(topology.get("primary"))
        secondary = _mapping(topology.get("secondary"))
        primary_bar = _mapping(primary.get("vdd_bar"))
        secondary_bar = _mapping(secondary.get("vdd_bar"))
        transformer = _mapping(raw.get("transformer"))
        shield = _mapping(transformer.get("shield"))
        emx = _mapping(raw.get("emx"))
        power = _mapping(emx.get("power_line_8port"))
        role_labels = _mapping(power.get("role_labels"))
        foundry_layout = _mapping(emx.get("foundry_layout"))
        signal_ports = tuple(str(value) for value in power.get("port_map") or ())
        auxiliary_ports = tuple(
            str(role_labels.get(name) or "")
            for name in (
                "left_power_top",
                "left_power_bottom",
                "right_power_top",
                "right_power_bottom",
            )
        )
        frequency_start_hz = _finite(target.get("frequency_start_hz"))
        frequency_stop_hz = _finite(target.get("frequency_stop_hz"))
        frequency_step_hz = _finite(target.get("frequency_step_hz"))
        band_points = _integer(target.get("band_points"))

        identity = {
            "topology_mode": str(target.get("topology_mode") or ""),
            "primary_turns": _integer(primary.get("turns")),
            "secondary_turns": _integer(secondary.get("turns")),
            "primary_center_tap": primary.get("center_tap") is True,
            "secondary_center_tap": secondary.get("center_tap") is True,
            "primary_center_tap_bar": {
                "enabled": primary_bar.get("enabled") is True,
                "layer": _integer(primary_bar.get("bar_layer")),
            },
            "secondary_center_tap_bar": {
                "enabled": secondary_bar.get("enabled") is True,
                "layer": _integer(secondary_bar.get("bar_layer")),
            },
            "shield": {
                "enabled": shield.get("enabled") is True,
                "kind": str(shield.get("kind") or ""),
            },
            "process_token_present": (
                "/TSMC65_05_12_26/"
                in str(emx.get("emx_process_file") or "")
                and "/TSMC65_05_12_26/"
                in str(emx.get("cadence_pdk_cds_lib") or "")
            ),
            "foundry_layout_enabled": foundry_layout.get("enabled") is True,
            "manufacturing_grid_um": _finite(
                foundry_layout.get("manufacturing_grid_um")
            ),
            "port_mode": str(emx.get("port_mode") or ""),
            "cadence_pin_purpose": _integer(
                emx.get("cadence_pin_purpose")
            ),
            "touchstone_mode": str(power.get("touchstone_mode") or ""),
            "touchstone_extension": ".s4p",
            "signal_port_count": len(signal_ports),
            "signal_ports": list(signal_ports),
            "physical_auxiliary_ports": list(auxiliary_ports),
            "auxiliary_ports_grounded_to": str(
                power.get("port_ground_reference") or ""
            ),
            "role_labels": {
                key: str(role_labels.get(key) or "")
                for key in EXPECTED_ROLE_LABELS
            },
            "ground_unused_s8p_ports": emx.get(
                "ground_unused_s8p_ports"
            ),
            "frequency_start_hz": frequency_start_hz,
            "frequency_stop_hz": frequency_stop_hz,
            "frequency_step_hz": frequency_step_hz,
            "frequency_points": band_points,
        }
        checks.update(
            {
                "config_sha256_exact": bool(actual_sha)
                and actual_sha == expected_sha,
                "topology_is_1t1t": identity["topology_mode"] == "1t1t",
                "primary_is_one_turn_center_tapped": (
                    identity["primary_turns"] == 1
                    and identity["primary_center_tap"] is True
                    and identity["primary_center_tap_bar"]
                    == {"enabled": True, "layer": 74}
                ),
                "secondary_is_one_turn_center_tapped": (
                    identity["secondary_turns"] == 1
                    and identity["secondary_center_tap"] is True
                    and identity["secondary_center_tap_bar"]
                    == {"enabled": True, "layer": 39}
                ),
                "shield_ground_reference_present": (
                    identity["shield"] == {"enabled": True, "kind": "ring"}
                ),
                "tsmc65_process_identity_present": (
                    identity["process_token_present"] is True
                ),
                "foundry_layout_and_grid_exact": (
                    identity["foundry_layout_enabled"] is True
                    and _same_float(
                        identity["manufacturing_grid_um"],
                        0.005,
                    )
                ),
                "port_mode_exact": identity["port_mode"]
                == "single_ended_shield_grounded",
                "cadence_pin_purpose_exact": (
                    identity["cadence_pin_purpose"] == 51
                ),
                "touchstone_is_four_signal_port_s4p": (
                    power.get("enabled") is True
                    and identity["touchstone_mode"]
                    == "signal_4_grounded_aux"
                    and identity["signal_port_count"] == 4
                    and tuple(identity["signal_ports"])
                    == EXPECTED_SIGNAL_PORTS
                    and identity["touchstone_extension"] == ".s4p"
                ),
                "auxiliary_center_tap_ports_are_grounded_to_shield": (
                    tuple(identity["physical_auxiliary_ports"])
                    == EXPECTED_AUXILIARY_PORTS
                    and identity["auxiliary_ports_grounded_to"] == "shield"
                    and identity["role_labels"] == EXPECTED_ROLE_LABELS
                    and identity["ground_unused_s8p_ports"] is False
                ),
                "frequency_contract_is_5_60_0p5_111": (
                    _same_float(frequency_start_hz, 5.0e9)
                    and _same_float(frequency_stop_hz, 60.0e9)
                    and _same_float(frequency_step_hz, 0.5e9)
                    and band_points == 111
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - exact failure is audit evidence.
        errors.append(f"{type(exc).__name__}: {exc}")

    identity_sha = (
        hashlib.sha256(
            json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if identity
        else ""
    )
    status = "PASS" if checks and all(checks.values()) and not errors else "FAIL"
    return {
        "schema": SCHEMA,
        "overall_status": status,
        "config": {
            "path": str(config_path),
            "exists": config_path.is_file(),
            "size_bytes": (
                config_path.stat().st_size if config_path.is_file() else 0
            ),
            "sha256": actual_sha,
        },
        "expected_config_sha256": expected_sha,
        "identity": identity,
        "identity_sha256": identity_sha,
        "checks": checks,
        "errors": errors,
    }


def _parse_differential_port_pairs(
    value: Any,
) -> tuple[tuple[int, int], ...]:
    text = str(value or "").strip()
    groups = text.split(":")
    if len(groups) != 2:
        raise ValueError(
            "differential_port_pairs must contain exactly two pairs"
        )
    pairs: list[tuple[int, int]] = []
    for group in groups:
        tokens = [token.strip() for token in group.split(",")]
        if len(tokens) != 2 or any(not token.isdigit() for token in tokens):
            raise ValueError(
                "each differential port pair must contain two integers"
            )
        pairs.append((int(tokens[0]), int(tokens[1])))
    flattened = [port for pair in pairs for port in pair]
    if sorted(flattened) != [1, 2, 3, 4]:
        raise ValueError(
            "differential port pairs must use signal ports 1-4 exactly once"
        )
    return tuple(pairs)


def records_match(left: Any, right: Any) -> bool:
    """Return True when two file records bind the same resolved path and SHA."""

    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_path = Path(str(left.get("path") or "")).expanduser()
    right_path = Path(str(right.get("path") or "")).expanduser()
    return bool(
        left_path.resolve() == right_path.resolve()
        and _is_sha256(left.get("sha256"))
        and str(left.get("sha256") or "").lower()
        == str(right.get("sha256") or "").lower()
    )


def identity_matches(left: Any, right: Any) -> bool:
    """Compare two emitted foundry identity audits by schema and fingerprints."""

    return bool(
        isinstance(left, dict)
        and isinstance(right, dict)
        and left.get("schema") == SCHEMA
        and right.get("schema") == SCHEMA
        and left.get("overall_status") == "PASS"
        and right.get("overall_status") == "PASS"
        and _is_sha256(left.get("identity_sha256"))
        and left.get("identity_sha256") == right.get("identity_sha256")
        and records_match(left.get("config"), right.get("config"))
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"non-finite numeric value: {value!r}")
    return numeric


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer contract value")
    numeric = int(value)
    if float(value) != float(numeric):
        raise ValueError(f"non-integral value: {value!r}")
    return numeric


def _same_float(first: Any, second: Any) -> bool:
    try:
        return math.isclose(
            _finite(first),
            _finite(second),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    except (TypeError, ValueError):
        return False


def _is_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
