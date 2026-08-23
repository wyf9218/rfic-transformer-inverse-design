#!/usr/bin/env python3
"""Audit the new vertical-power-line 8-port contract before running EMX.

This script is intentionally read-only. It does not run Cadence, EMX, HFSS, or
ADS; it verifies that a run config has enough explicit information to be safe
to send to MARS for the new .s8p physical-feature data flow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402


BRIDGE_WIDTH_CONTRACT_BASIS = (
    "latest clarified geometry contract: bridge width must equal the vertical power-line width"
)
SUPERSEDED_LITERAL_10NM_BRIDGE_WIDTH_UM = 0.01
VERTICAL_LENGTH_REFERENCE_DIMENSION = "max(primary_outer_height_um, secondary_outer_height_um)"
VERTICAL_LENGTH_CONTRACT_BASIS = (
    "historical key vertical_length_diameter_ratio is kept for compatibility, "
    "but the current layout exporter applies it to the maximum coil vertical height"
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


@dataclass(frozen=True)
class AuditOptions:
    expected_bridge_width_um: float | None = None
    bridge_width_tolerance_um: float = 1.0e-12
    expected_ground_frame_width_um: float | None = 100.0
    ground_frame_width_tolerance_um: float = 1.0e-9
    expected_ground_frame_policy: str = "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
    expected_differential_port_pairs: str | None = "1,4:5,6"
    expected_frequency_start_ghz: float = 5.0
    expected_frequency_stop_ghz: float = 60.0
    expected_frequency_step_ghz: float = 0.5
    expected_frequency_points: int = 111
    frequency_tolerance_hz: float = 1.0
    reject_placeholders: bool = True


@dataclass(frozen=True)
class FrequencySpec:
    start_hz: float
    stop_hz: float
    step_hz: float
    points: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Final S8P run-config YAML path")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--expected-bridge-width-um",
        type=float,
        default=None,
        help=(
            "Optional legacy fixed bridge-width check. Omit for current shared-line-width runs; "
            "layout audits then verify each generated sample's bridge equals its recorded line_width_um."
        ),
    )
    parser.add_argument("--bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--expected-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--ground-frame-width-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument(
        "--expected-ground-frame-policy",
        default="power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame",
    )
    parser.add_argument(
        "--expected-differential-port-pairs",
        default="1,4:5,6",
        help="Expected one-based S8P physical-feature differential pairs. Use '' to disable this exact-match check.",
    )
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--allow-placeholders", action="store_true", help="Do not fail on TODO/REPLACE/TBD markers")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = AuditOptions(
        expected_bridge_width_um=None if args.expected_bridge_width_um is None else float(args.expected_bridge_width_um),
        bridge_width_tolerance_um=float(args.bridge_width_tolerance_um),
        expected_ground_frame_width_um=float(args.expected_ground_frame_width_um),
        ground_frame_width_tolerance_um=float(args.ground_frame_width_tolerance_um),
        expected_ground_frame_policy=str(args.expected_ground_frame_policy),
        expected_differential_port_pairs=str(args.expected_differential_port_pairs or "").strip() or None,
        expected_frequency_start_ghz=float(args.expected_frequency_start_ghz),
        expected_frequency_stop_ghz=float(args.expected_frequency_stop_ghz),
        expected_frequency_step_ghz=float(args.expected_frequency_step_ghz),
        expected_frequency_points=int(args.expected_frequency_points),
        frequency_tolerance_hz=float(args.frequency_tolerance_hz),
        reject_placeholders=not bool(args.allow_placeholders),
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = audit_config(Path(args.config).expanduser().resolve(), options)
    summary_path = out_dir / "power_line_8port_contract_audit_summary.json"
    report_path = out_dir / "power_line_8port_contract_audit_report.md"
    summary["summary_path"] = str(summary_path)
    summary["report_path"] = str(report_path)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if summary["overall_status"] != "PASS" and not args.no_fail_exit:
        return 2
    return 0


def audit_config(config_path: Path, options: AuditOptions | None = None) -> dict[str, Any]:
    opts = options or AuditOptions()
    checks: list[Check] = []
    config = None
    load_error = None
    raw_text = ""
    pair_label_map: list[dict[str, Any]] = []
    power_line_summary: dict[str, Any] = {}

    if config_path.is_file():
        checks.append(Check("PASS", "config file exists", str(config_path)))
        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw_text = config_path.read_text(encoding="utf-8", errors="replace")
    else:
        checks.append(Check("FAIL", "config file exists", str(config_path)))

    if opts.reject_placeholders and raw_text:
        placeholder_lines = _placeholder_lines(raw_text)
        if placeholder_lines:
            detail = "; ".join(f"L{line}: {text}" for line, text in placeholder_lines[:8])
            if len(placeholder_lines) > 8:
                detail += f"; ... {len(placeholder_lines) - 8} more"
            checks.append(Check("FAIL", "no unresolved config placeholders", detail))
        else:
            checks.append(Check("PASS", "no unresolved config placeholders", "No TODO/REPLACE/TBD markers"))

    if config_path.is_file():
        try:
            config = load_run_config(config_path)
            checks.append(Check("PASS", "config loads", str(config_path)))
        except Exception as exc:  # noqa: BLE001
            load_error = f"{type(exc).__name__}: {exc}"
            checks.append(Check("FAIL", "config loads", load_error))

    if config is not None:
        _check_port_mode(checks, config)
        pair_label_map = _check_differential_pairs(checks, config, opts)
        power_line_summary = _check_power_line_contract(checks, config, opts)
        _check_layout_topology_contract(checks, config)
        _check_frequency_grid(checks, config, opts)

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": "READY_FOR_8PORT_MARS_EMX_RUN" if overall_status == "PASS" else "DO_NOT_RUN_MARS_EMX_UNTIL_CONTRACT_PASSES",
        "config": str(config_path),
        "load_error": load_error,
        "expected": {
            "bridge_width_um": opts.expected_bridge_width_um,
            "bridge_width_tolerance_um": opts.bridge_width_tolerance_um,
            "bridge_width_contract_basis": BRIDGE_WIDTH_CONTRACT_BASIS,
            "superseded_literal_10nm_bridge_width_um": SUPERSEDED_LITERAL_10NM_BRIDGE_WIDTH_UM,
            "ground_frame_width_um": opts.expected_ground_frame_width_um,
            "ground_frame_width_tolerance_um": opts.ground_frame_width_tolerance_um,
            "ground_frame_policy": opts.expected_ground_frame_policy,
            "differential_port_pairs": opts.expected_differential_port_pairs,
            "vertical_length_reference_dimension": VERTICAL_LENGTH_REFERENCE_DIMENSION,
            "vertical_length_contract_basis": VERTICAL_LENGTH_CONTRACT_BASIS,
            "frequency_start_hz": opts.expected_frequency_start_ghz * 1.0e9,
            "frequency_stop_hz": opts.expected_frequency_stop_ghz * 1.0e9,
            "frequency_step_hz": opts.expected_frequency_step_ghz * 1.0e9,
            "frequency_points": int(opts.expected_frequency_points),
            "reject_placeholders": bool(opts.reject_placeholders),
        },
        "power_line_8port": power_line_summary,
        "differential_pair_label_map": pair_label_map,
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This audit only checks config-level topology requirements; it does not prove the generated GDS/SKILL geometry is physically correct.",
            "Bridge width follows the latest same-width power-line clarification; the earlier literal 10nm/0.01um bridge interpretation is intentionally rejected.",
            "The current vertical length check records the clarified reference dimension; layout-level audits must still verify vertical_length_um equals 1.5*max coil height for each generated sample.",
            "Final acceptance still requires EMX .s8p quality gates and EMX/HFSS/ADS Lp/Ls/Q/K/Kw curve comparison on a random sample.",
        ],
    }


def _check_port_mode(checks: list[Check], config: Any) -> None:
    actual = str(config.emx.port_mode)
    expected = "single_ended_shield_grounded"
    if actual == expected:
        checks.append(Check("PASS", "port mode", actual))
    else:
        checks.append(Check("FAIL", "port mode", f"got {actual!r}, expected {expected!r}"))


def _check_differential_pairs(checks: list[Check], config: Any, opts: AuditOptions) -> list[dict[str, Any]]:
    pairs = config.emx.differential_port_pairs
    port_map = list(config.emx.power_line_8port.port_map)
    if pairs is None:
        checks.append(Check("FAIL", "differential port pairs", "missing"))
        return []
    one_based_pairs = [[int(a) + 1, int(b) + 1] for a, b in pairs]
    flat = [port for pair in one_based_pairs for port in pair]
    errors = []
    if len(one_based_pairs) != 2 or any(len(pair) != 2 for pair in one_based_pairs):
        errors.append("must contain exactly two two-port pairs")
    if len(set(flat)) != 4:
        errors.append("must use four distinct ports")
    if any(port < 1 or port > 8 for port in flat):
        errors.append(f"all ports must be in 1..8, got {one_based_pairs}")
    if errors:
        checks.append(Check("FAIL", "differential port pairs", "; ".join(errors)))
    else:
        checks.append(Check("PASS", "differential port pairs", str(one_based_pairs)))
        expected_pairs = _parse_one_based_pair_text(opts.expected_differential_port_pairs)
        if expected_pairs is not None:
            if one_based_pairs == expected_pairs:
                checks.append(Check("PASS", "differential port pairs match expected physical-feature extraction path", str(one_based_pairs)))
            else:
                checks.append(
                    Check(
                        "FAIL",
                        "differential port pairs match expected physical-feature extraction path",
                        f"got {one_based_pairs}, expected {expected_pairs}",
                    )
                )

    mapping = []
    for index, pair in enumerate(one_based_pairs, start=1):
        labels = [port_map[port - 1] if 1 <= port <= len(port_map) else None for port in pair]
        role = "primary_response_pair" if index == 1 else "secondary_response_pair"
        mapping.append({"pair": index, "role": role, "ports": pair, "labels": labels})
    return mapping


def _parse_one_based_pair_text(text: str | None) -> list[list[int]] | None:
    if text is None:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None
    pairs: list[list[int]] = []
    for pair_text in stripped.split(":"):
        items = [item.strip() for item in pair_text.split(",") if item.strip()]
        if len(items) != 2:
            return []
        try:
            pairs.append([int(items[0]), int(items[1])])
        except ValueError:
            return []
    return pairs


def _check_power_line_contract(checks: list[Check], config: Any, opts: AuditOptions) -> dict[str, Any]:
    spec = config.emx.power_line_8port
    summary = {
        "enabled": bool(spec.enabled),
        "bridge_width_um": spec.bridge_width_um,
        "vertical_length_diameter_ratio": float(spec.vertical_length_diameter_ratio),
        "bridge_y_policy": spec.bridge_y_policy,
        "bridge_motion_axis": spec.bridge_motion_axis,
        "port_ground_reference": spec.port_ground_reference,
        "port_map": list(spec.port_map),
        "ground_frame_width_um": _ground_frame_width_um(config),
        "ground_frame_policy": opts.expected_ground_frame_policy,
        "shield_width_um": None if config.bounds.shield.width_um is None else float(config.bounds.shield.width_um),
        "shield_margin_um": None if config.bounds.shield.margin_um is None else float(config.bounds.shield.margin_um),
    }
    checks.append(Check("PASS" if spec.enabled else "FAIL", "power_line_8port enabled", str(bool(spec.enabled))))

    if spec.bridge_width_um is None:
        checks.append(Check("FAIL", "bridge width", "missing"))
    else:
        width = float(spec.bridge_width_um)
        if width <= 0.0:
            checks.append(Check("FAIL", "bridge width", f"must be positive, got {width} um"))
        elif opts.expected_bridge_width_um is not None and abs(width - float(opts.expected_bridge_width_um)) > opts.bridge_width_tolerance_um:
            checks.append(
                Check(
                    "FAIL",
                    "bridge width",
                    f"got {width:.12g} um, expected {float(opts.expected_bridge_width_um):.12g} um",
                )
            )
        else:
            checks.append(Check("PASS", "bridge width", f"{width:.12g} um"))
        if abs(width - SUPERSEDED_LITERAL_10NM_BRIDGE_WIDTH_UM) <= opts.bridge_width_tolerance_um:
            checks.append(
                Check(
                    "FAIL",
                    "literal 10nm bridge interpretation is rejected",
                    f"bridge_width_um={width:.12g} equals superseded 10nm/0.01um value",
                )
            )
        else:
            checks.append(
                Check(
                    "PASS",
                    "literal 10nm bridge interpretation is rejected",
                    f"bridge_width_um={width:.12g} follows same-width contract, not 0.01um",
                )
            )
    bridge_width = None if spec.bridge_width_um is None else float(spec.bridge_width_um)
    for role_name, inductor in (("primary", config.bounds.primary), ("secondary", config.bounds.secondary)):
        vdd = inductor.vdd_bar
        power_line_width = None if vdd is None else (float(inductor.trace_width_um[0]) if vdd.width_um is None else float(vdd.width_um))
        passed = bridge_width is not None and power_line_width is not None and abs(bridge_width - power_line_width) <= opts.bridge_width_tolerance_um
        checks.append(
            Check(
                "PASS" if passed else "FAIL",
                f"bridge width matches {role_name} vertical power-line width",
                f"bridge_width_um={bridge_width}, {role_name}_power_line_width_um={power_line_width}",
            )
        )

    _check_equal(checks, "vertical length height ratio", float(spec.vertical_length_diameter_ratio), 1.5, 1.0e-12)
    checks.append(
        Check(
            "PASS",
            "vertical length reference dimension",
            f"{VERTICAL_LENGTH_REFERENCE_DIMENSION}; {VERTICAL_LENGTH_CONTRACT_BASIS}",
        )
    )
    _check_string(checks, "bridge y policy", spec.bridge_y_policy, "center")
    _check_string(checks, "bridge motion axis", spec.bridge_motion_axis, "x_only")
    _check_string(checks, "port ground reference", spec.port_ground_reference, "shield")
    ground_frame_width = _ground_frame_width_um(config)
    if ground_frame_width is None:
        checks.append(Check("FAIL", "ground frame width", "missing shield width/margin evidence"))
    elif opts.expected_ground_frame_width_um is not None and abs(float(ground_frame_width) - float(opts.expected_ground_frame_width_um)) > opts.ground_frame_width_tolerance_um:
        checks.append(
            Check(
                "FAIL",
                "ground frame width",
                f"got {float(ground_frame_width):.12g} um, expected {float(opts.expected_ground_frame_width_um):.12g} um",
            )
        )
    else:
        checks.append(Check("PASS", "ground frame width", f"{float(ground_frame_width):.12g} um"))
    checks.append(Check("PASS", "ground frame policy", opts.expected_ground_frame_policy))

    port_map = list(spec.port_map)
    if len(port_map) != 8:
        checks.append(Check("FAIL", "port map", f"must list exactly 8 labels, got {len(port_map)}"))
    elif len(set(port_map)) != 8:
        checks.append(Check("FAIL", "port map", f"labels must be distinct: {port_map}"))
    else:
        checks.append(Check("PASS", "port map", ", ".join(port_map)))
    bad_labels = [label for label in port_map if _looks_like_placeholder(label)]
    if bad_labels:
        checks.append(Check("FAIL", "port map labels finalized", f"placeholder labels: {bad_labels}"))
    elif port_map:
        checks.append(Check("PASS", "port map labels finalized", "No placeholder labels"))
    return summary


def _ground_frame_width_um(config: Any) -> float | None:
    shield = config.bounds.shield
    if shield.width_um is None and shield.margin_um is None:
        return None
    width_um = 0.0 if shield.width_um is None else float(shield.width_um)
    margin_um = 0.0 if shield.margin_um is None else float(shield.margin_um)
    return max(width_um, margin_um)


def _check_layout_topology_contract(checks: list[Check], config: Any) -> None:
    expected_layers = {
        "primary": int(config.emx.ap_layer),
        "secondary": int(config.emx.m9_layer),
    }
    for role_name, inductor in (("primary", config.bounds.primary), ("secondary", config.bounds.secondary)):
        if bool(inductor.center_tap):
            checks.append(Check("PASS", f"{role_name} center tap", "enabled"))
        else:
            checks.append(Check("FAIL", f"{role_name} center tap", "power_line_8port requires center_tap=true"))
        vdd = inductor.vdd_bar
        if vdd is not None and bool(vdd.enabled) and vdd.bar_layer is not None:
            same_layer = int(vdd.bar_layer) == int(expected_layers[role_name])
            checks.append(
                Check(
                    "PASS" if same_layer else "FAIL",
                    f"{role_name} power line bar matches coil layer",
                    f"bar_layer={int(vdd.bar_layer)}, coil_layer={expected_layers[role_name]}, width_um={vdd.width_um}, offset_um={vdd.offset_um}",
                )
            )
        else:
            checks.append(Check("FAIL", f"{role_name} power line bar", "requires vdd_bar.enabled=true and bar_layer"))


def _check_frequency_grid(checks: list[Check], config: Any, opts: AuditOptions) -> None:
    actual_points = config.target.frequency_points_hz()
    if len(actual_points) < 2:
        checks.append(Check("FAIL", "frequency grid", "fewer than 2 points"))
        return
    actual = FrequencySpec(
        start_hz=float(actual_points[0]),
        stop_hz=float(actual_points[-1]),
        step_hz=float(actual_points[1] - actual_points[0]),
        points=int(len(actual_points)),
    )
    expected = FrequencySpec(
        start_hz=float(opts.expected_frequency_start_ghz) * 1.0e9,
        stop_hz=float(opts.expected_frequency_stop_ghz) * 1.0e9,
        step_hz=float(opts.expected_frequency_step_ghz) * 1.0e9,
        points=int(opts.expected_frequency_points),
    )
    mismatch = _frequency_mismatch_detail(actual, expected, float(opts.frequency_tolerance_hz))
    if mismatch:
        checks.append(Check("FAIL", "frequency grid", mismatch))
    else:
        checks.append(
            Check(
                "PASS",
                "frequency grid",
                f"{actual.start_hz / 1.0e9:.12g}-{actual.stop_hz / 1.0e9:.12g} GHz, "
                f"step {actual.step_hz / 1.0e9:.12g} GHz, points={actual.points}",
            )
        )


def _check_equal(checks: list[Check], name: str, actual: float, expected: float, tolerance: float) -> None:
    if abs(actual - expected) <= tolerance:
        checks.append(Check("PASS", name, f"{actual:.12g}"))
    else:
        checks.append(Check("FAIL", name, f"got {actual:.12g}, expected {expected:.12g}"))


def _check_string(checks: list[Check], name: str, actual: str, expected: str) -> None:
    if str(actual) == expected:
        checks.append(Check("PASS", name, str(actual)))
    else:
        checks.append(Check("FAIL", name, f"got {actual!r}, expected {expected!r}"))


def _frequency_mismatch_detail(actual: FrequencySpec, expected: FrequencySpec, tolerance_hz: float) -> str | None:
    comparisons = [
        ("start_hz", actual.start_hz, expected.start_hz),
        ("stop_hz", actual.stop_hz, expected.stop_hz),
        ("step_hz", actual.step_hz, expected.step_hz),
    ]
    for name, actual_value, expected_value in comparisons:
        if abs(float(actual_value) - float(expected_value)) > tolerance_hz:
            return f"{name} mismatch: actual={actual_value}, expected={expected_value}"
    if int(actual.points) != int(expected.points):
        return f"points mismatch: actual={actual.points}, expected={expected.points}"
    return None


def _placeholder_lines(raw_text: str) -> list[tuple[int, str]]:
    pattern = re.compile(r"(TODO|TBD|PLACEHOLDER|REPLACE)|/REPLACE/", re.IGNORECASE)
    lines = []
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if line and pattern.search(line):
            lines.append((line_number, line[:180]))
    return lines


def _looks_like_placeholder(value: str) -> bool:
    stripped = str(value).strip()
    if not stripped:
        return True
    upper = stripped.upper()
    return any(token in upper for token in ("TODO", "TBD", "PLACEHOLDER", "REPLACE", "CONFIRM")) or stripped.startswith("/REPLACE/")


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Power-Line 8-Port Contract Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Config: `{summary['config']}`",
        "",
        "## Power-Line Contract",
        "",
        "```json",
        json.dumps(summary.get("power_line_8port", {}), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Differential Pair Label Map",
        "",
        "```json",
        json.dumps(summary.get("differential_pair_label_map", []), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {_cell(check['name'])} | {_cell(check['detail'])} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
