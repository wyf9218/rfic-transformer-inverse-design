"""Small DRC gates for the TSMC65 top-metal transformer layouts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class Tsmc65TopMetalDrcRules:
    """Top-metal rules extracted from TN65CLDR001_2_6_2 for this flow."""

    rule_source: str = "TN65CLDR001_2_6_2_Designrule_manual.pdf"
    mu_layer: int = 39
    ap_md_layer: int = 74
    min_mu_width_um: float = 2.0
    min_mu_spacing_um: float = 2.0
    max_mu_width_without_inddmy_um: float = 12.0
    min_ap_md_width_um: float = 3.0
    min_ap_md_spacing_um: float = 2.0
    max_ap_md_width_um: float = 35.0

    @property
    def shared_line_min_width_um(self) -> float:
        return max(float(self.min_mu_width_um), float(self.min_ap_md_width_um))

    @property
    def shared_line_max_width_um(self) -> float:
        return min(float(self.max_mu_width_without_inddmy_um), float(self.max_ap_md_width_um))


TSMC65_TOP_METAL_DRC = Tsmc65TopMetalDrcRules()


def audit_tsmc65_top_metal_geometry(
    geometry: Any,
    run_config: Any | None = None,
    *,
    rules: Tsmc65TopMetalDrcRules = TSMC65_TOP_METAL_DRC,
) -> dict[str, Any]:
    """Audit one synchronized M10/M9 power-line transformer geometry."""

    flat = geometry.flat_dict()
    checks: list[dict[str, Any]] = []

    primary_layer = _primary_layer(flat, run_config, rules)
    secondary_layer = _secondary_layer(flat, run_config, rules)
    primary_width = _as_float(flat.get("primary_width_um"))
    secondary_width = _as_float(flat.get("secondary_width_um"))
    line_width = _as_float(flat.get("line_width_um"))
    primary_spacing = _as_float(flat.get("primary_spacing_um"))
    secondary_spacing = _as_float(flat.get("secondary_spacing_um"))
    primary_bar_width = _as_float(flat.get("primary_vdd_bar_width_um"))
    secondary_bar_width = _as_float(flat.get("secondary_vdd_bar_width_um"))

    checks.append(_check("shared_line_width_present", line_width is not None, f"line_width_um={line_width}"))
    if primary_width is not None and secondary_width is not None:
        checks.append(
            _check(
                "primary_secondary_widths_are_synchronized",
                _same(primary_width, secondary_width),
                f"primary={primary_width}, secondary={secondary_width}",
            )
        )
    if line_width is not None and primary_width is not None:
        checks.append(_check("line_width_matches_primary", _same(line_width, primary_width), f"line={line_width}, primary={primary_width}"))
    if line_width is not None and secondary_width is not None:
        checks.append(
            _check("line_width_matches_secondary", _same(line_width, secondary_width), f"line={line_width}, secondary={secondary_width}")
        )

    checks.extend(_width_checks("primary", primary_layer, primary_width, rules))
    checks.extend(_width_checks("secondary", secondary_layer, secondary_width, rules))
    checks.extend(_spacing_checks("primary", primary_layer, primary_spacing, rules))
    checks.extend(_spacing_checks("secondary", secondary_layer, secondary_spacing, rules))

    if _power_line_enabled(run_config):
        checks.append(
            _check(
                "primary_vertical_power_line_same_width",
                primary_bar_width is not None and primary_width is not None and _same(primary_bar_width, primary_width),
                f"primary_vdd_bar_width_um={primary_bar_width}, primary_width_um={primary_width}",
            )
        )
        checks.append(
            _check(
                "secondary_vertical_power_line_same_width",
                secondary_bar_width is not None and secondary_width is not None and _same(secondary_bar_width, secondary_width),
                f"secondary_vdd_bar_width_um={secondary_bar_width}, secondary_width_um={secondary_width}",
            )
        )

    errors = [str(item["detail"]) for item in checks if not item["pass"]]
    return {
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "rule_source": rules.rule_source,
        "rules": asdict(rules),
        "primary_layer": primary_layer,
        "secondary_layer": secondary_layer,
        "checks": checks,
        "errors": errors,
    }


def audit_tsmc65_top_metal_search_space(
    run_config: Any,
    *,
    rules: Tsmc65TopMetalDrcRules = TSMC65_TOP_METAL_DRC,
) -> dict[str, Any]:
    """Audit search-space bounds before expensive EMX generation."""

    bounds = run_config.bounds
    checks: list[dict[str, Any]] = []
    primary_width_bounds = tuple(map(float, bounds.primary_width_um))
    secondary_width_bounds = tuple(map(float, bounds.secondary_width_um))
    primary_spacing_bounds = tuple(map(float, bounds.primary_spacing_um))
    secondary_spacing_bounds = tuple(map(float, bounds.secondary_spacing_um))
    min_width = rules.shared_line_min_width_um
    max_width = rules.shared_line_max_width_um
    min_spacing = max(float(rules.min_mu_spacing_um), float(rules.min_ap_md_spacing_um))

    checks.append(
        _check(
            "primary_trace_width_bounds_drc_safe",
            primary_width_bounds[0] >= min_width - 1.0e-12 and primary_width_bounds[1] <= max_width + 1.0e-12,
            f"primary_trace_width_um={primary_width_bounds}, required=[{min_width}, {max_width}]",
        )
    )
    checks.append(
        _check(
            "secondary_trace_width_bounds_drc_safe",
            secondary_width_bounds[0] >= min_width - 1.0e-12 and secondary_width_bounds[1] <= max_width + 1.0e-12,
            f"secondary_trace_width_um={secondary_width_bounds}, required=[{min_width}, {max_width}]",
        )
    )
    checks.append(
        _check(
            "primary_spacing_bounds_drc_safe",
            primary_spacing_bounds[0] >= min_spacing - 1.0e-12,
            f"primary_spacing_um={primary_spacing_bounds}, min={min_spacing}",
        )
    )
    checks.append(
        _check(
            "secondary_spacing_bounds_drc_safe",
            secondary_spacing_bounds[0] >= min_spacing - 1.0e-12,
            f"secondary_spacing_um={secondary_spacing_bounds}, min={min_spacing}",
        )
    )

    errors = [str(item["detail"]) for item in checks if not item["pass"]]
    return {
        "status": "PASS" if not errors else "FAIL",
        "ok": not errors,
        "rule_source": rules.rule_source,
        "rules": asdict(rules),
        "checks": checks,
        "errors": errors,
    }


def _width_checks(prefix: str, layer: int, width_um: float | None, rules: Tsmc65TopMetalDrcRules) -> list[dict[str, Any]]:
    if width_um is None:
        return [_check(f"{prefix}_width_present", False, f"{prefix}_width_um=None")]
    if int(layer) == int(rules.ap_md_layer):
        return [
            _check(f"{prefix}_ap_md_min_width", width_um >= rules.min_ap_md_width_um - 1.0e-12, f"{width_um} >= {rules.min_ap_md_width_um}"),
            _check(f"{prefix}_ap_md_max_width", width_um <= rules.max_ap_md_width_um + 1.0e-12, f"{width_um} <= {rules.max_ap_md_width_um}"),
        ]
    if int(layer) == int(rules.mu_layer):
        return [
            _check(f"{prefix}_mu_min_width", width_um >= rules.min_mu_width_um - 1.0e-12, f"{width_um} >= {rules.min_mu_width_um}"),
            _check(
                f"{prefix}_mu_conservative_max_width",
                width_um <= rules.max_mu_width_without_inddmy_um + 1.0e-12,
                f"{width_um} <= {rules.max_mu_width_without_inddmy_um} without explicit INDDMY exception",
            ),
        ]
    return [_check(f"{prefix}_known_top_metal_layer", False, f"layer={layer}")]


def _spacing_checks(prefix: str, layer: int, spacing_um: float | None, rules: Tsmc65TopMetalDrcRules) -> list[dict[str, Any]]:
    if spacing_um is None:
        return [_check(f"{prefix}_spacing_present", False, f"{prefix}_spacing_um=None")]
    minimum = rules.min_ap_md_spacing_um if int(layer) == int(rules.ap_md_layer) else rules.min_mu_spacing_um
    return [_check(f"{prefix}_same_layer_min_spacing", spacing_um >= minimum - 1.0e-12, f"{spacing_um} >= {minimum}")]


def _primary_layer(flat: dict[str, Any], run_config: Any | None, rules: Tsmc65TopMetalDrcRules) -> int:
    if run_config is not None:
        return int(run_config.emx.ap_layer)
    return int(flat.get("primary_vdd_bar_layer") or rules.ap_md_layer)


def _secondary_layer(flat: dict[str, Any], run_config: Any | None, rules: Tsmc65TopMetalDrcRules) -> int:
    if run_config is not None:
        return int(run_config.emx.m9_layer)
    return int(flat.get("secondary_vdd_bar_layer") or rules.mu_layer)


def _power_line_enabled(run_config: Any | None) -> bool:
    return bool(run_config is not None and run_config.emx.power_line_8port.enabled)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _same(left: float, right: float, *, tol: float = 1.0e-9) -> bool:
    return abs(float(left) - float(right)) <= float(tol)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}
