#!/usr/bin/env python3
"""Audit transformer geometry quality evidence for report readiness.

The script reads dataset_manifest.json and/or final500_ground_clearance_audit.json
and turns geometry evidence into explicit PASS/FAIL checks. It does not infer
geometry from missing files and it does not validate EM labels.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = _resolve_manifest(source, args.manifest)
    clearance_path = _resolve_optional(source, args.clearance_audit, names=("final500_ground_clearance_audit.json",))
    layout_path = _resolve_optional(source, args.layout_json, names=("transformer_layout.layout.json",))
    summary_json_path = _resolve_optional(source, args.summary_json, names=("summary.json",))
    power_line_geometry_path = _resolve_optional(source, args.power_line_8port_geometry_json, names=("power_line_8port_geometry.json",))

    checks: list[Check] = []
    manifest: dict[str, Any] | None = None
    clearance: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    target_summary: dict[str, Any] | None = None
    power_line_geometry: dict[str, Any] | None = None

    if manifest_path is None and clearance_path is None and layout_path is None and summary_json_path is None and power_line_geometry_path is None:
        checks.append(
            Check(
                "FAIL",
                "geometry evidence source",
                "No raw dataset manifest, clearance audit, layout JSON, or target summary JSON was supplied or discovered",
            )
        )

    if manifest_path is not None:
        manifest = _read_json(manifest_path)
        checks.extend(_audit_manifest(manifest, args))
    else:
        checks.append(Check("WARN", "dataset manifest", "No dataset_manifest.json supplied or discovered"))

    if clearance_path is not None:
        clearance = _read_json(clearance_path)
        checks.extend(_audit_clearance(clearance, args))
    else:
        status = "FAIL" if args.require_clearance_audit else "WARN"
        checks.append(Check(status, "clearance audit", "No final500_ground_clearance_audit.json supplied or discovered"))

    if layout_path is not None:
        layout = _read_json(layout_path)
        checks.extend(_audit_layout(layout, args))
    else:
        checks.append(Check("WARN", "layout manifest", "No transformer_layout.layout.json supplied or discovered"))

    if power_line_geometry_path is not None:
        power_line_geometry = _read_json(power_line_geometry_path)
        checks.extend(_audit_power_line_8port_geometry(power_line_geometry, args))
    else:
        checks.append(
            Check(
                "FAIL" if args.require_power_line_8port_geometry else "WARN",
                "power_line_8port geometry",
                "No power_line_8port_geometry.json supplied or discovered",
            )
        )

    if summary_json_path is not None:
        target_summary = _read_json(summary_json_path)
        checks.extend(_audit_target_summary_geometry(target_summary, args))
    else:
        checks.append(Check("WARN", "target summary geometry", "No summary.json supplied or discovered"))

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "overall_status": overall_status,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "clearance_audit_path": str(clearance_path) if clearance_path else None,
        "layout_json_path": str(layout_path) if layout_path else None,
        "summary_json_path": str(summary_json_path) if summary_json_path else None,
        "power_line_8port_geometry_path": str(power_line_geometry_path) if power_line_geometry_path else None,
        "checks": [check.__dict__ for check in checks],
        "manifest_counts": _manifest_counts(manifest),
        "clearance_counts": _clearance_counts(clearance),
        "layout_counts": _layout_counts(layout),
        "target_summary_counts": _target_summary_counts(target_summary),
        "power_line_8port_counts": _power_line_8port_counts(power_line_geometry),
        "limitations": [
            "This audit checks geometry metadata and clearance evidence only.",
            "It does not validate S-parameters, Zin coverage, HFSS-vs-EMX correlation, or ADS formulas.",
        ],
    }

    summary_path = out_dir / "geometry_quality_audit_summary.json"
    report_path = out_dir / "geometry_quality_audit_report.md"
    rows_path = out_dir / "geometry_quality_audit_checks.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(rows_path, checks)

    print(f"overall_status={overall_status}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    print(f"checks_csv={rows_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Dataset directory, manifest JSON, clearance audit JSON, layout JSON, or target summary JSON")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--manifest", help="Optional dataset_manifest.json path")
    parser.add_argument("--clearance-audit", help="Optional final500_ground_clearance_audit.json path")
    parser.add_argument("--layout-json", help="Optional transformer_layout.layout.json path")
    parser.add_argument("--summary-json", help="Optional target sample summary.json path containing geometry_check metrics")
    parser.add_argument("--power-line-8port-geometry-json", help="Optional power_line_8port_geometry.json path")
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--expected-port-names", default="P001,P002,P003,P004")
    parser.add_argument(
        "--expected-power-line-bridge-width-um",
        type=float,
        default=None,
        help="Optional legacy fixed bridge width check. Omit for shared variable line_width_um runs.",
    )
    parser.add_argument("--expected-primary-power-line-layer", type=int, default=74)
    parser.add_argument("--expected-primary-power-line-datatype", type=int, default=0)
    parser.add_argument("--expected-secondary-power-line-layer", type=int, default=39)
    parser.add_argument("--expected-secondary-power-line-datatype", type=int, default=60)
    parser.add_argument("--expected-power-line-vertical-length-diameter-ratio", type=float, default=1.5)
    parser.add_argument(
        "--min-power-line-other-coil-clearance-um",
        type=float,
        default=1.0e-6,
        help="Minimum positive x-clearance from each vertical power-line edge to the opposite coil boundary.",
    )
    parser.add_argument(
        "--expected-power-line-center-tap-topology",
        default="primary_right_secondary_left",
        choices=("primary_right_secondary_left", "primary_left_secondary_right", "any"),
        help=(
            "Expected S8P center-tap power-line placement. The photo-matched 1t1t topology is "
            "primary_right_secondary_left: L1 signal feeds on the physical left, but its center "
            "power-line is on the physical right; L2 is the opposite."
        ),
    )
    parser.add_argument("--power-line-tolerance-um", type=float, default=1.0e-9)
    parser.add_argument("--require-power-line-8port-geometry", action="store_true")
    parser.add_argument("--require-shield-enabled", action="store_true", default=True)
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--angle-tolerance-deg", type=float, default=1.0e-3)
    parser.add_argument("--min-geometry-pass-fraction", type=float, default=1.0)
    parser.add_argument("--max-clearance-overlap-area-um2", type=float, default=1.0e-3)
    parser.add_argument("--max-clearance-violation-area-um2", type=float, default=1.0e-3)
    parser.add_argument("--min-clearance-pass-fraction", type=float, default=0.0)
    parser.add_argument("--require-clearance-audit", action="store_true")
    parser.add_argument("--require-selected-clearance-pass", action="store_true", default=True)
    parser.add_argument("--allow-clearance-missing", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_manifest(source: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if source.is_dir() and (source / "dataset_manifest.json").exists():
        return source / "dataset_manifest.json"
    if source.is_file() and source.name == "dataset_manifest.json":
        return source
    return None


def _resolve_optional(source: Path, explicit: str | None, *, names: tuple[str, ...]) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if source.is_dir():
        for name in names:
            candidate = source / name
            if candidate.exists():
                return candidate
    if source.is_file() and source.name in names:
        return source
    return None


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    expected_port_mode = str(args.expected_port_mode)
    port_mode = manifest.get("port_mode")
    if port_mode == expected_port_mode:
        checks.append(Check("PASS", "port mode", f"port_mode={port_mode}"))
    else:
        checks.append(Check("FAIL", "port mode", f"expected={expected_port_mode}, actual={port_mode}"))

    pin = _to_int(manifest.get("cadence_pin_purpose"))
    if pin == int(args.expected_pin_purpose):
        checks.append(Check("PASS", "cadence pin purpose", f"pin_purpose={pin}"))
    else:
        checks.append(Check("FAIL", "cadence pin purpose", f"expected={args.expected_pin_purpose}, actual={pin}"))

    if bool(manifest.get("shield_enabled")) or not args.require_shield_enabled:
        checks.append(Check("PASS", "shield enabled", f"shield_enabled={manifest.get('shield_enabled')}"))
    else:
        checks.append(Check("FAIL", "shield enabled", f"shield_enabled={manifest.get('shield_enabled')}"))

    geometry = manifest.get("geometry_quality") or {}
    count = _to_int(geometry.get("geometry_check_count"))
    ok_count = _to_int(geometry.get("geometry_check_ok_count"))
    if count is None or ok_count is None or count <= 0:
        checks.append(Check("FAIL", "geometry check count", f"geometry_quality counts missing or zero: count={count}, ok={ok_count}"))
    else:
        fraction = ok_count / count
        status = "PASS" if fraction + 1.0e-12 >= float(args.min_geometry_pass_fraction) else "FAIL"
        checks.append(
            Check(
                status,
                "geometry check pass fraction",
                f"ok={ok_count}, count={count}, fraction={fraction:.6g}, required={args.min_geometry_pass_fraction:.6g}",
            )
        )

    angle_count = _to_int(geometry.get("angle_checked_count"))
    if angle_count is None or angle_count <= 0:
        checks.append(Check("FAIL", "angle checked count", f"angle_checked_count={angle_count}"))
    else:
        checks.append(Check("PASS", "angle checked count", f"angle_checked_count={angle_count}"))

    for key in ("primary_internal_angle_deg", "secondary_internal_angle_deg"):
        checks.append(_angle_check(geometry.get(key), key, float(args.internal_angle_deg), float(args.angle_tolerance_deg)))
    for key in ("primary_terminal_interface_angle_deg", "secondary_terminal_interface_angle_deg"):
        checks.append(_angle_check(geometry.get(key), key, float(args.terminal_angle_deg), float(args.angle_tolerance_deg)))
    return checks


def _angle_check(section: Any, name: str, expected: float, tolerance: float) -> Check:
    values = _collect_angle_numbers(section)
    if not values:
        return Check("FAIL", name, "no numeric angle evidence")
    low = min(values)
    high = max(values)
    if all(abs(value - expected) <= tolerance for value in values):
        return Check("PASS", name, f"range={low:.12g}-{high:.12g} deg, expected={expected:g} +/- {tolerance:g}")
    return Check("FAIL", name, f"range={low:.12g}-{high:.12g} deg, expected={expected:g} +/- {tolerance:g}")


def _audit_clearance(clearance: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    records = list(clearance.get("records") or [])
    count = _to_int(clearance.get("candidate_count")) or len(records)
    pass_count = _to_int(clearance.get("pass_count")) or 0
    reject_count = _to_int(clearance.get("reject_count")) or 0
    missing = _to_int(clearance.get("missing_or_other_count")) or 0
    if count <= 0 or not records:
        return [Check("FAIL", "clearance records", f"candidate_count={count}, records={len(records)}")]
    if pass_count + reject_count + missing == count:
        checks.append(Check("PASS", "clearance count accounting", f"pass={pass_count}, reject={reject_count}, missing={missing}, count={count}"))
    else:
        checks.append(Check("FAIL", "clearance count accounting", f"pass+reject+missing={pass_count + reject_count + missing}, count={count}"))

    fraction = pass_count / count
    status = "PASS" if fraction + 1.0e-12 >= float(args.min_clearance_pass_fraction) else "FAIL"
    checks.append(
        Check(
            status,
            "clearance pass fraction",
            f"pass={pass_count}, count={count}, fraction={fraction:.6g}, required={args.min_clearance_pass_fraction:.6g}",
        )
    )
    if missing == 0 or args.allow_clearance_missing:
        checks.append(Check("PASS", "clearance missing/other count", f"missing_or_other_count={missing}"))
    else:
        checks.append(Check("FAIL", "clearance missing/other count", f"missing_or_other_count={missing}"))

    selected = clearance.get("selected") or {}
    selected_status = selected.get("status")
    if not args.require_selected_clearance_pass:
        checks.append(Check("WARN", "selected clearance sample", "selected pass requirement disabled"))
    elif selected_status == "pass_signal_to_shield_clearance":
        checks.append(Check("PASS", "selected clearance sample", f"cache_key={selected.get('cache_key')} status={selected_status}"))
    else:
        checks.append(Check("FAIL", "selected clearance sample", f"cache_key={selected.get('cache_key')} status={selected_status}"))

    passing_records = [record for record in records if record.get("status") == "pass_signal_to_shield_clearance"]
    max_direct = max((_to_float(record.get("direct_signal_shield_overlap_area_um2")) or 0.0) for record in passing_records) if passing_records else 0.0
    max_violation = max((_to_float(record.get("signal_shield_clearance_violation_area_um2")) or 0.0) for record in passing_records) if passing_records else 0.0
    if max_direct <= float(args.max_clearance_overlap_area_um2):
        checks.append(Check("PASS", "passing-record direct overlap", f"max={max_direct:.6g} um^2"))
    else:
        checks.append(
            Check(
                "FAIL",
                "passing-record direct overlap",
                f"max={max_direct:.6g} um^2 exceeds {args.max_clearance_overlap_area_um2:.6g}",
            )
        )
    if max_violation <= float(args.max_clearance_violation_area_um2):
        checks.append(Check("PASS", "passing-record clearance violation", f"max={max_violation:.6g} um^2"))
    else:
        checks.append(
            Check(
                "FAIL",
                "passing-record clearance violation",
                f"max={max_violation:.6g} um^2 exceeds {args.max_clearance_violation_area_um2:.6g}",
            )
        )
    return checks


def _audit_layout(layout: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    ports = list(layout.get("ports") or [])
    expected_names = _expected_port_names(args)
    by_name = {str(port.get("name")): port for port in ports if port.get("name")}
    expected_count = len(expected_names)
    if len(ports) == expected_count:
        checks.append(Check("PASS", "layout port count", f"ports={len(ports)}"))
    else:
        checks.append(Check("FAIL", "layout port count", f"expected={expected_count}, actual={len(ports)}"))
    actual_names = [str(port.get("name")) for port in ports]
    if actual_names == expected_names:
        checks.append(Check("PASS", "layout port names", f"ports={actual_names}"))
    else:
        checks.append(Check("FAIL", "layout port names", f"expected={expected_names}, actual={actual_names}"))
    if _to_int(layout.get("cadence_pin_purpose")) == int(args.expected_pin_purpose):
        checks.append(Check("PASS", "layout pin purpose", f"pin_purpose={layout.get('cadence_pin_purpose')}"))
    else:
        checks.append(Check("FAIL", "layout pin purpose", f"expected={args.expected_pin_purpose}, actual={layout.get('cadence_pin_purpose')}"))
    missing_signal_labels = [
        name for name in expected_names if name not in {str(item) for item in (by_name.get(name, {}).get("signal_labels") or [])}
    ]
    missing_ground_labels = [
        name for name in expected_names if f"{name}_G" not in {str(item) for item in (by_name.get(name, {}).get("ground_labels") or [])}
    ]
    if not missing_signal_labels:
        checks.append(Check("PASS", "layout signal labels", f"ports={expected_names}"))
    else:
        checks.append(Check("FAIL", "layout signal labels", f"missing signal labels for ports={missing_signal_labels}"))
    if not missing_ground_labels:
        checks.append(Check("PASS", "layout grounded labels", f"ports={expected_names}"))
    else:
        checks.append(Check("FAIL", "layout grounded labels", f"missing ground labels for ports={missing_ground_labels}"))
    missing_internal_signal = [
        name for name in expected_names if by_name.get(name, {}).get("internal_signal_labels") is not True
    ]
    missing_internal_ground = [
        name for name in expected_names if by_name.get(name, {}).get("internal_ground_labels") is not True
    ]
    if not missing_internal_signal and not missing_internal_ground:
        checks.append(Check("PASS", "layout internal pin labels", f"ports={expected_names}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "layout internal pin labels",
                f"missing_internal_signal={missing_internal_signal}, missing_internal_ground={missing_internal_ground}",
            )
        )
    return checks


def _audit_power_line_8port_geometry(power_line: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    expected_names = _expected_port_names(args)
    expected_roles = (
        "left_power_top",
        "left_power_bottom",
        "primary_top",
        "primary_bottom",
        "secondary_top",
        "secondary_bottom",
        "right_power_top",
        "right_power_bottom",
    )
    if bool(power_line.get("enabled")):
        checks.append(Check("PASS", "power_line_8port geometry enabled", "enabled=true"))
    else:
        checks.append(Check("FAIL", "power_line_8port geometry enabled", f"enabled={power_line.get('enabled')}"))

    placement_policy = power_line.get("placement_policy")
    accepted_policies = {"coil_opening_fixed_10um_port_ground_overlap"}
    if placement_policy in accepted_policies:
        checks.append(Check("PASS", "power_line_8port placement policy", str(placement_policy)))
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port placement policy",
                f"expected one of {sorted(accepted_policies)}, actual={placement_policy}",
            )
        )

    bridge_width = _to_float(power_line.get("bridge_width_um"))
    line_width = _to_float(power_line.get("line_width_um"))
    expected_bridge = _to_float(args.expected_power_line_bridge_width_um)
    tol = float(args.power_line_tolerance_um)
    if bridge_width is not None and bridge_width > 0.0 and (
        expected_bridge is None or abs(bridge_width - expected_bridge) <= tol
    ):
        detail = f"{bridge_width:.12g} um"
        if expected_bridge is not None:
            detail += f", expected={expected_bridge:.12g} um"
        checks.append(Check("PASS", "power_line_8port bridge width", detail))
    else:
        checks.append(Check("FAIL", "power_line_8port bridge width", f"expected={expected_bridge} um, actual={bridge_width}"))

    vertical_length = _to_float(power_line.get("vertical_length_um"))
    max_outer_height = _to_float(power_line.get("max_outer_height_um"))
    if max_outer_height is None:
        max_outer_height = _to_float(power_line.get("max_outer_diameter_um"))
    ratio = _to_float(power_line.get("vertical_length_diameter_ratio"))
    expected_vertical_length = _to_float(power_line.get("expected_vertical_length_um"))
    expected_ratio = float(args.expected_power_line_vertical_length_diameter_ratio)
    primary = power_line.get("primary_power_line") or {}
    secondary = power_line.get("secondary_power_line") or {}
    primary_width = _to_float(primary.get("width_um") if isinstance(primary, dict) else None)
    secondary_width = _to_float(secondary.get("width_um") if isinstance(secondary, dict) else None)
    primary_height = _to_float(primary.get("height_um") if isinstance(primary, dict) else None)
    secondary_height = _to_float(secondary.get("height_um") if isinstance(secondary, dict) else None)
    for role_name, section, expected_layer, expected_datatype in (
        (
            "primary",
            primary,
            int(args.expected_primary_power_line_layer),
            int(args.expected_primary_power_line_datatype),
        ),
        (
            "secondary",
            secondary,
            int(args.expected_secondary_power_line_layer),
            int(args.expected_secondary_power_line_datatype),
        ),
    ):
        actual_layer = _to_float(section.get("bar_layer") if isinstance(section, dict) else None)
        actual_datatype = _to_float(section.get("bar_datatype") if isinstance(section, dict) else None)
        if actual_layer is not None and int(actual_layer) == expected_layer:
            checks.append(
                Check(
                    "PASS",
                    f"power_line_8port {role_name} vertical power-line layer matches coil layer",
                    f"bar_layer={int(actual_layer)}",
                )
            )
        else:
            checks.append(
                Check(
                    "FAIL",
                    f"power_line_8port {role_name} vertical power-line layer matches coil layer",
                    f"expected={expected_layer}, actual={actual_layer}",
                )
            )
        if actual_datatype is not None and int(actual_datatype) == expected_datatype:
            checks.append(
                Check(
                    "PASS",
                    f"power_line_8port {role_name} vertical power-line datatype matches proc draw pair",
                    f"bar_datatype={int(actual_datatype)}",
                )
            )
        else:
            checks.append(
                Check(
                    "FAIL",
                    f"power_line_8port {role_name} vertical power-line datatype matches proc draw pair",
                    f"expected={expected_datatype}, actual={actual_datatype}",
                )
            )
    checks.extend(_audit_power_line_8port_process_layers(power_line))
    if (
        line_width is not None
        and bridge_width is not None
        and primary_width is not None
        and secondary_width is not None
        and abs(line_width - bridge_width) <= tol
        and abs(line_width - primary_width) <= tol
        and abs(line_width - secondary_width) <= tol
    ):
        checks.append(
            Check(
                "PASS",
                "power_line_8port shared line width",
                (
                    f"line_width={line_width:.12g}, bridge={bridge_width:.12g}, "
                    f"primary={primary_width:.12g}, secondary={secondary_width:.12g} um"
                ),
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port shared line width",
                f"line_width={line_width}, bridge={bridge_width}, primary={primary_width}, secondary={secondary_width}",
            )
        )
    if vertical_length is not None and vertical_length > 0.0:
        checks.append(Check("PASS", "power_line_8port vertical length positive", f"{vertical_length:.12g} um"))
    else:
        checks.append(Check("FAIL", "power_line_8port vertical length positive", f"vertical_length_um={vertical_length}"))
    if (
        vertical_length is not None
        and primary_height is not None
        and secondary_height is not None
        and abs(primary_height - vertical_length) <= tol
        and abs(secondary_height - vertical_length) <= tol
        and abs(primary_height - secondary_height) <= tol
    ):
        checks.append(
            Check(
                "PASS",
                "power_line_8port equal vertical heights",
                f"vertical={vertical_length:.12g}, primary={primary_height:.12g}, secondary={secondary_height:.12g} um",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port equal vertical heights",
                f"vertical={vertical_length}, primary={primary_height}, secondary={secondary_height}",
            )
        )
    if max_outer_height is not None and max_outer_height > 0.0:
        checks.append(Check("PASS", "power_line_8port max outer height positive", f"{max_outer_height:.12g} um"))
    else:
        checks.append(
            Check("FAIL", "power_line_8port max outer height positive", f"max_outer_height_um={max_outer_height}")
        )
    if ratio is not None and abs(ratio - expected_ratio) <= 1.0e-12:
        checks.append(Check("PASS", "power_line_8port vertical length ratio", f"{ratio:.12g}"))
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port vertical length ratio",
                f"expected={expected_ratio:.12g}, actual={ratio}",
            )
        )
    if max_outer_height is not None and vertical_length is not None:
        computed_vertical_length = max_outer_height * expected_ratio
        if abs(vertical_length - computed_vertical_length) <= tol:
            checks.append(
                Check(
                    "PASS",
                    "power_line_8port vertical length equals 1.5*max coil height",
                    f"vertical={vertical_length:.12g}, height={max_outer_height:.12g}, expected={computed_vertical_length:.12g} um",
                )
            )
        else:
            checks.append(
                Check(
                    "FAIL",
                    "power_line_8port vertical length equals 1.5*max coil height",
                    f"vertical={vertical_length}, height={max_outer_height}, expected={computed_vertical_length}",
                )
            )
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port vertical length equals 1.5*max coil height",
                f"vertical_length_um={vertical_length}, max_outer_height_um={max_outer_height}",
            )
        )
    if expected_vertical_length is not None and vertical_length is not None and abs(expected_vertical_length - vertical_length) <= tol:
        checks.append(
            Check(
                "PASS",
                "power_line_8port stored expected vertical length",
                f"expected_vertical_length_um={expected_vertical_length:.12g}",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port stored expected vertical length",
                f"expected_vertical_length_um={expected_vertical_length}, vertical_length_um={vertical_length}",
            )
        )

    labels = power_line.get("labels") or {}
    if isinstance(labels, dict) and len(expected_names) == 8:
        actual_by_role = [str(labels.get(role)) for role in expected_roles]
        if sorted(actual_by_role) == sorted(expected_names) and len(set(actual_by_role)) == 8:
            checks.append(
                Check(
                    "PASS",
                    "power_line_8port role label map",
                    f"role_labels={actual_by_role}; labels cover expected ports without assuming semantic order",
                )
            )
        else:
            checks.append(Check("FAIL", "power_line_8port role label map", f"expected labels={expected_names}, actual role labels={actual_by_role}"))
    elif isinstance(labels, dict):
        checks.append(Check("WARN", "power_line_8port role label map", f"expected port count is {len(expected_names)}, labels={labels}"))
    else:
        checks.append(Check("FAIL", "power_line_8port role label map", "labels block missing or not a dict"))

    primary_center_x = _to_float(primary.get("center_x_um") if isinstance(primary, dict) else None)
    secondary_center_x = _to_float(secondary.get("center_x_um") if isinstance(secondary, dict) else None)
    actual_center_tap_topology = _power_line_center_tap_topology(primary, secondary)
    recorded_center_tap_topology = power_line.get("center_tap_topology")
    if recorded_center_tap_topology is None:
        recorded_center_tap_topology = actual_center_tap_topology
    expected_center_tap_topology = getattr(args, "expected_power_line_center_tap_topology", "primary_right_secondary_left")
    if expected_center_tap_topology == "any":
        if actual_center_tap_topology is not None:
            checks.append(
                Check(
                    "PASS",
                    "power_line_8port center-tap topology",
                    f"actual={actual_center_tap_topology}, recorded={recorded_center_tap_topology}",
                )
            )
        else:
            checks.append(Check("FAIL", "power_line_8port center-tap topology", "missing primary/secondary x evidence"))
    elif actual_center_tap_topology == expected_center_tap_topology and recorded_center_tap_topology == expected_center_tap_topology:
        checks.append(
            Check(
                "PASS",
                "power_line_8port center-tap topology",
                f"expected={expected_center_tap_topology}, actual={actual_center_tap_topology}",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                "power_line_8port center-tap topology",
                f"expected={expected_center_tap_topology}, actual={actual_center_tap_topology}, recorded={recorded_center_tap_topology}",
            )
        )
    if primary_center_x is not None and secondary_center_x is not None and primary_center_x != secondary_center_x:
        primary_is_left = primary_center_x < secondary_center_x
        left_section = primary if primary_is_left else secondary
        right_section = secondary if primary_is_left else primary
        endpoint_checks = {
            "physical left top port label": (left_section, "top_port_label", labels.get("left_power_top") if isinstance(labels, dict) else None),
            "physical left bottom port label": (left_section, "bottom_port_label", labels.get("left_power_bottom") if isinstance(labels, dict) else None),
            "physical right top port label": (right_section, "top_port_label", labels.get("right_power_top") if isinstance(labels, dict) else None),
            "physical right bottom port label": (right_section, "bottom_port_label", labels.get("right_power_bottom") if isinstance(labels, dict) else None),
            "physical left top ground label": (
                left_section,
                "top_ground_label",
                f"{labels.get('left_power_top')}_G" if isinstance(labels, dict) and labels.get("left_power_top") else None,
            ),
            "physical left bottom ground label": (
                left_section,
                "bottom_ground_label",
                f"{labels.get('left_power_bottom')}_G" if isinstance(labels, dict) and labels.get("left_power_bottom") else None,
            ),
            "physical right top ground label": (
                right_section,
                "top_ground_label",
                f"{labels.get('right_power_top')}_G" if isinstance(labels, dict) and labels.get("right_power_top") else None,
            ),
            "physical right bottom ground label": (
                right_section,
                "bottom_ground_label",
                f"{labels.get('right_power_bottom')}_G" if isinstance(labels, dict) and labels.get("right_power_bottom") else None,
            ),
        }
        checks.append(
            Check(
                "PASS",
                "power_line_8port physical left/right order",
                f"primary_x={primary_center_x:.12g}, secondary_x={secondary_center_x:.12g}, primary_is_left={primary_is_left}",
            )
        )
    else:
        endpoint_checks = {}
        checks.append(
            Check(
                "FAIL",
                "power_line_8port physical left/right order",
                f"primary_x={primary_center_x}, secondary_x={secondary_center_x}",
            )
        )
    for name, (section, key, expected) in endpoint_checks.items():
        actual = section.get(key) if isinstance(section, dict) else None
        if expected is not None and actual == expected:
            checks.append(Check("PASS", f"power_line_8port {name}", f"{actual}"))
        else:
            checks.append(Check("FAIL", f"power_line_8port {name}", f"expected={expected}, actual={actual}"))
    checks.extend(
        _audit_power_line_8port_bridge(
            power_line.get("primary_bridge") if isinstance(power_line, dict) else None,
            "primary",
            expected_bridge,
            tol,
        )
    )
    checks.extend(
        _audit_power_line_8port_bridge(
            power_line.get("secondary_bridge") if isinstance(power_line, dict) else None,
            "secondary",
            expected_bridge,
            tol,
        )
    )
    min_other_clearance = float(getattr(args, "min_power_line_other_coil_clearance_um", 1.0e-6))
    checks.extend(
        _audit_power_line_8port_clearance(
            power_line.get("primary_power_line_clearance") if isinstance(power_line, dict) else None,
            "primary",
            min_other_clearance,
            tol,
        )
    )
    checks.extend(
        _audit_power_line_8port_clearance(
            power_line.get("secondary_power_line_clearance") if isinstance(power_line, dict) else None,
            "secondary",
            min_other_clearance,
            tol,
        )
    )
    checks.extend(_audit_power_line_8port_inside_shield(power_line, tol))
    return checks


def _audit_power_line_8port_clearance(
    clearance: Any,
    side: str,
    min_other_clearance_um: float,
    tol: float,
) -> list[Check]:
    if not isinstance(clearance, dict):
        return [Check("FAIL", f"power_line_8port {side} power-line clearance evidence", "missing clearance evidence")]
    checks: list[Check] = []
    combined_clearance = _to_float(clearance.get("combined_coil_boundary_clearance_um"))
    other_clearance = _to_float(clearance.get("other_coil_boundary_clearance_um"))
    own_clearance = _to_float(clearance.get("own_coil_boundary_clearance_um"))
    outside_combined = clearance.get("outside_combined_coil_projection") is True
    if outside_combined and combined_clearance is not None and combined_clearance >= -tol:
        checks.append(
            Check(
                "PASS",
                f"power_line_8port {side} power-line outside combined coil projection",
                f"combined_clearance={combined_clearance:.12g} um",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                f"power_line_8port {side} power-line outside combined coil projection",
                f"outside={clearance.get('outside_combined_coil_projection')}, combined_clearance={combined_clearance}",
            )
        )
    if other_clearance is not None and other_clearance + tol >= float(min_other_clearance_um):
        checks.append(
            Check(
                "PASS",
                f"power_line_8port {side} power-line clears other coil boundary",
                f"other_clearance={other_clearance:.12g} um >= {float(min_other_clearance_um):.12g} um",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                f"power_line_8port {side} power-line clears other coil boundary",
                f"other_clearance={other_clearance}, min={float(min_other_clearance_um):.12g}",
            )
        )
    if own_clearance is not None and own_clearance + tol >= 0.0:
        checks.append(
            Check(
                "PASS",
                f"power_line_8port {side} power-line clears own coil boundary",
                f"own_clearance={own_clearance:.12g} um",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                f"power_line_8port {side} power-line clears own coil boundary",
                f"own_clearance={own_clearance}",
            )
        )
    return checks


def _audit_power_line_8port_process_layers(power_line: dict[str, Any]) -> list[Check]:
    summary = power_line.get("process_layer_summary") if isinstance(power_line, dict) else None
    if not isinstance(summary, dict):
        return [Check("FAIL", "power_line_8port process layer summary", "missing process_layer_summary")]
    records = summary.get("records")
    if not isinstance(records, dict):
        return [Check("FAIL", "power_line_8port process layer summary", "missing records")]
    checks: list[Check] = []
    expected = {
        "primary_m10_draw": ("metal10", 74, 0, 2.8),
        "primary_m10_pin": ("metal10", 126, 0, 2.8),
        "secondary_m9_draw": ("metal9", 39, 60, 3.4),
        "secondary_m9_pin": ("metal9", 139, 0, 3.4),
    }
    for key, (conductor_name, layer, datatype, thickness_um) in expected.items():
        record = records.get(key)
        if not isinstance(record, dict):
            checks.append(Check("FAIL", f"process layer {key}", "missing"))
            continue
        actual = (
            str(record.get("conductor_name")),
            _to_int(record.get("layer")),
            _to_int(record.get("datatype")),
        )
        expected_tuple = (conductor_name, layer, datatype)
        thickness = _to_float(record.get("conductor_thickness_um"))
        if actual == expected_tuple and thickness is not None and abs(thickness - thickness_um) <= 1.0e-9:
            checks.append(
                Check(
                    "PASS",
                    f"process layer {key}",
                    f"{conductor_name} layer={layer} datatype={datatype} thickness={thickness:.12g} um",
                )
            )
        else:
            checks.append(
                Check(
                    "FAIL",
                    f"process layer {key}",
                    f"expected={expected_tuple}, thickness={thickness_um}; actual={actual}, thickness={thickness}",
                )
            )
    return checks


def _audit_power_line_8port_bridge(bridge: Any, side: str, expected_bridge: float | None, tol: float) -> list[Check]:
    if not isinstance(bridge, dict):
        return [Check("FAIL", f"power_line_8port {side} bridge evidence", "missing bridge coordinate evidence")]
    checks: list[Check] = []
    width = _to_float(bridge.get("width_um"))
    if width is not None and width > 0.0 and (
        expected_bridge is None or abs(width - expected_bridge) <= tol
    ):
        detail = f"{width:.12g} um"
        if expected_bridge is not None:
            detail += f", expected={expected_bridge:.12g}"
        checks.append(Check("PASS", f"power_line_8port {side} bridge width", detail))
    else:
        checks.append(Check("FAIL", f"power_line_8port {side} bridge width", f"expected={expected_bridge}, actual={width}"))
    left_edge = _to_float(bridge.get("power_line_left_edge_x_um"))
    right_edge = _to_float(bridge.get("power_line_right_edge_x_um"))
    edge_width = None if left_edge is None or right_edge is None else abs(right_edge - left_edge)
    if width is not None and edge_width is not None and abs(width - edge_width) <= tol:
        checks.append(
            Check(
                "PASS",
                f"power_line_8port {side} bridge width matches power-line edge width",
                f"bridge={width:.12g}, edge_width={edge_width:.12g} um",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                f"power_line_8port {side} bridge width matches power-line edge width",
                f"bridge={width}, edge_width={edge_width}",
            )
        )

    if bridge.get("extends_away_from_coil_interior") is True:
        checks.append(
            Check(
                "PASS",
                f"power_line_8port {side} bridge stays outside coil interior",
                "extends_away_from_coil_interior=true",
            )
        )
    else:
        checks.append(
            Check(
                "FAIL",
                f"power_line_8port {side} bridge stays outside coil interior",
                f"extends_away_from_coil_interior={bridge.get('extends_away_from_coil_interior')}",
            )
        )

    coil = bridge.get("coil_anchor") if isinstance(bridge.get("coil_anchor"), dict) else {}
    edge = bridge.get("power_line_edge") if isinstance(bridge.get("power_line_edge"), dict) else {}
    y_values = [
        _to_float(coil.get("y_um") if isinstance(coil, dict) else None),
        _to_float(edge.get("y_um") if isinstance(edge, dict) else None),
        _to_float(bridge.get("center_y_um")),
        _to_float(bridge.get("power_line_center_y_um")),
    ]
    missing_y = [value for value in y_values if value is None]
    max_abs_y = max((abs(float(value)) for value in y_values if value is not None), default=float("inf"))
    if not missing_y and max_abs_y <= tol:
        checks.append(Check("PASS", f"power_line_8port {side} bridge centered y=0", f"max_abs_y={max_abs_y:.12g} um"))
    else:
        checks.append(Check("FAIL", f"power_line_8port {side} bridge centered y=0", f"y_values={y_values}, max_abs_y={max_abs_y}"))

    delta_y = _to_float(bridge.get("delta_y_um"))
    if delta_y is not None and abs(delta_y) <= tol and bridge.get("is_horizontal") is True:
        checks.append(Check("PASS", f"power_line_8port {side} bridge horizontal", f"delta_y={delta_y:.12g} um"))
    else:
        checks.append(Check("FAIL", f"power_line_8port {side} bridge horizontal", f"delta_y={delta_y}, is_horizontal={bridge.get('is_horizontal')}"))

    length = _to_float(bridge.get("length_um"))
    if length is not None and length > 0.0:
        checks.append(Check("PASS", f"power_line_8port {side} bridge positive length", f"{length:.12g} um"))
    else:
        checks.append(Check("FAIL", f"power_line_8port {side} bridge positive length", f"length_um={length}"))

    edge_error = _to_float(bridge.get("power_line_edge_alignment_error_um"))
    if edge_error is not None and edge_error <= tol:
        checks.append(Check("PASS", f"power_line_8port {side} bridge touches power-line edge", f"edge_error={edge_error:.12g} um"))
    else:
        checks.append(Check("FAIL", f"power_line_8port {side} bridge touches power-line edge", f"edge_error={edge_error}"))
    return checks


def _audit_power_line_8port_inside_shield(power_line: dict[str, Any], tol: float) -> list[Check]:
    checks: list[Check] = []
    shield_inner = _bbox_from_dict(power_line.get("shield_inner_bbox_um"))
    shield_outer = _bbox_from_dict(power_line.get("shield_outer_bbox_um"))
    if shield_inner is None:
        checks.append(Check("FAIL", "power_line_8port shield inner bbox", "missing shield_inner_bbox_um evidence"))
        return checks
    checks.append(Check("PASS", "power_line_8port shield inner bbox", _format_bbox_tuple(shield_inner)))
    if shield_outer is None:
        checks.append(Check("FAIL", "power_line_8port shield outer bbox", "missing shield_outer_bbox_um evidence"))
    else:
        checks.append(Check("PASS", "power_line_8port shield outer bbox", _format_bbox_tuple(shield_outer)))
        frame_width = _to_float(power_line.get("ground_frame_width_um"))
        if frame_width is None:
            checks.append(Check("FAIL", "power_line_8port ground frame width", "missing ground_frame_width_um evidence"))
        else:
            actual_widths = (
                shield_inner[0] - shield_outer[0],
                shield_inner[1] - shield_outer[1],
                shield_outer[2] - shield_inner[2],
                shield_outer[3] - shield_inner[3],
            )
            max_error = max(abs(float(width) - float(frame_width)) for width in actual_widths)
            detail = f"expected={frame_width:.12g} um actual={tuple(round(float(width), 12) for width in actual_widths)}"
            checks.append(
                Check(
                    "PASS" if max_error <= tol else "FAIL",
                    "power_line_8port ground frame width",
                    detail,
                )
            )

    bar_failures: list[str] = []
    for name in ("primary_power_line", "secondary_power_line"):
        bar_bbox = _power_line_bar_bbox(power_line.get(name))
        if bar_bbox is None:
            bar_failures.append(f"{name}=missing")
            continue
        if shield_outer is not None and not _bbox_inside(bar_bbox, shield_outer, tol=tol):
            bar_failures.append(f"{name}={_format_bbox_tuple(bar_bbox)} outside outer={_format_bbox_tuple(shield_outer)}")
            continue
        x_inside_opening = bar_bbox[0] + tol >= shield_inner[0] and bar_bbox[2] - tol <= shield_inner[2]
        y_crosses_opening = bar_bbox[1] <= shield_inner[1] + tol and bar_bbox[3] >= shield_inner[3] - tol
        y_reaches_ground = bar_bbox[1] < shield_inner[1] - tol and bar_bbox[3] > shield_inner[3] + tol
        if not x_inside_opening or not y_crosses_opening or not y_reaches_ground:
            bar_failures.append(
                f"{name}={_format_bbox_tuple(bar_bbox)} must x-fit opening, cross opening y, and extend into ground frame "
                f"inner={_format_bbox_tuple(shield_inner)}"
            )
    if not bar_failures:
        checks.append(Check("PASS", "power_line_8port bars cross opening and reach ground frame", _format_bbox_tuple(shield_inner)))
    else:
        checks.append(Check("FAIL", "power_line_8port bars cross opening and reach ground frame", "; ".join(bar_failures)))

    bridge_failures: list[str] = []
    for name in ("primary_bridge", "secondary_bridge"):
        bridge_bbox = _power_line_bridge_bbox(power_line.get(name))
        if bridge_bbox is None:
            bridge_failures.append(f"{name}=missing")
            continue
        if not _bbox_inside(bridge_bbox, shield_inner, tol=tol):
            bridge_failures.append(f"{name}={_format_bbox_tuple(bridge_bbox)} outside inner={_format_bbox_tuple(shield_inner)}")
    if not bridge_failures:
        checks.append(Check("PASS", "power_line_8port bridges inside shield opening", _format_bbox_tuple(shield_inner)))
    else:
        checks.append(Check("FAIL", "power_line_8port bridges inside shield opening", "; ".join(bridge_failures)))

    expected_overlap = _to_float(power_line.get("port_ground_overlap_um"))
    overlap_evidence = power_line.get("port_ground_overlap_evidence")
    overlap_ports = overlap_evidence.get("ports") if isinstance(overlap_evidence, dict) else None
    if expected_overlap is None:
        checks.append(Check("FAIL", "power_line_8port fixed port-ground overlap", "missing port_ground_overlap_um evidence"))
    elif not isinstance(overlap_ports, dict) or not overlap_ports:
        checks.append(Check("FAIL", "power_line_8port fixed port-ground overlap", "missing per-port overlap evidence"))
    else:
        overlap_failures: list[str] = []
        for port_name, record in sorted(overlap_ports.items()):
            if not isinstance(record, dict):
                overlap_failures.append(f"{port_name}=invalid")
                continue
            measured = _to_float(record.get("measured_overlap_um"))
            if measured is None:
                overlap_failures.append(f"{port_name}=missing")
                continue
            if abs(float(measured) - float(expected_overlap)) > tol:
                overlap_failures.append(f"{port_name}={measured:.12g}um expected={expected_overlap:.12g}um")
        if overlap_failures:
            checks.append(Check("FAIL", "power_line_8port fixed port-ground overlap", "; ".join(overlap_failures)))
        else:
            checks.append(
                Check(
                    "PASS",
                    "power_line_8port fixed port-ground overlap",
                    f"{len(overlap_ports)} ports at {expected_overlap:.12g} um",
                )
            )
    return checks


def _bbox_from_dict(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, dict):
        return None
    values = (
        _to_float(raw.get("min_x_um")),
        _to_float(raw.get("min_y_um")),
        _to_float(raw.get("max_x_um")),
        _to_float(raw.get("max_y_um")),
    )
    if any(value is None for value in values):
        return None
    min_x, min_y, max_x, max_y = (float(value) for value in values if value is not None)
    if min_x > max_x or min_y > max_y:
        return None
    return (min_x, min_y, max_x, max_y)


def _power_line_center_tap_topology(primary: Any, secondary: Any) -> str | None:
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        return None
    primary_center_x = _to_float(primary.get("center_x_um"))
    secondary_center_x = _to_float(secondary.get("center_x_um"))
    if primary_center_x is None or secondary_center_x is None or primary_center_x == secondary_center_x:
        return None
    if primary_center_x > secondary_center_x:
        return "primary_right_secondary_left"
    return "primary_left_secondary_right"


def _power_line_bar_bbox(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, dict):
        return None
    center_x = _to_float(raw.get("center_x_um"))
    center_y = _to_float(raw.get("center_y_um"))
    width = _to_float(raw.get("width_um"))
    height = _to_float(raw.get("height_um"))
    if center_x is None or center_y is None or width is None or height is None:
        return None
    half_w = 0.5 * float(width)
    half_h = 0.5 * float(height)
    return (float(center_x) - half_w, float(center_y) - half_h, float(center_x) + half_w, float(center_y) + half_h)


def _power_line_bridge_bbox(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, dict):
        return None
    coil = raw.get("coil_anchor") if isinstance(raw.get("coil_anchor"), dict) else {}
    edge = raw.get("power_line_edge") if isinstance(raw.get("power_line_edge"), dict) else {}
    x0 = _to_float(coil.get("x_um") if isinstance(coil, dict) else None)
    y0 = _to_float(coil.get("y_um") if isinstance(coil, dict) else None)
    x1 = _to_float(edge.get("x_um") if isinstance(edge, dict) else None)
    y1 = _to_float(edge.get("y_um") if isinstance(edge, dict) else None)
    width = _to_float(raw.get("width_um"))
    if x0 is None or y0 is None or x1 is None or y1 is None or width is None:
        return None
    half_w = 0.5 * float(width)
    return (min(float(x0), float(x1)), min(float(y0), float(y1)) - half_w, max(float(x0), float(x1)), max(float(y0), float(y1)) + half_w)


def _bbox_inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], *, tol: float) -> bool:
    return (
        inner[0] + float(tol) >= outer[0]
        and inner[1] + float(tol) >= outer[1]
        and inner[2] - float(tol) <= outer[2]
        and inner[3] - float(tol) <= outer[3]
    )


def _format_bbox_tuple(bbox: tuple[float, float, float, float]) -> str:
    return f"({bbox[0]:.6g},{bbox[1]:.6g})-({bbox[2]:.6g},{bbox[3]:.6g})"


def _audit_target_summary_geometry(summary: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    geometry_check = summary.get("geometry_check") or {}
    if not geometry_check:
        return [Check("FAIL", "target summary geometry_check", "summary.json has no geometry_check block")]
    errors = list(geometry_check.get("errors") or [])
    if not errors:
        checks.append(Check("PASS", "target summary geometry errors", "errors=[]"))
    else:
        checks.append(Check("FAIL", "target summary geometry errors", f"errors={errors}"))
    metrics = geometry_check.get("metrics") or {}
    checks.extend(_summary_winding_angle_checks(metrics, args))
    if metrics.get("skipped") is True:
        checks.append(
            Check(
                "WARN",
                "target summary advanced geometry checks",
                "summary geometry_check was marked skipped=true; use raw layout/clearance audit for clearance and port evidence",
            )
        )
    return checks


def _summary_winding_angle_checks(metrics: dict[str, Any], args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    for side in ("primary", "secondary"):
        internal_values = [
            metrics.get(f"{side}_winding_centerline_min_internal_angle_deg"),
            metrics.get(f"{side}_winding_centerline_max_internal_angle_deg"),
        ]
        terminal_values = [
            metrics.get(f"{side}_winding_centerline_min_terminal_angle_deg"),
            metrics.get(f"{side}_winding_centerline_max_terminal_angle_deg"),
        ]
        checks.append(
            _angle_check(
                internal_values,
                f"target summary {side} internal winding angles",
                float(args.internal_angle_deg),
                float(args.angle_tolerance_deg),
            )
        )
        checks.append(
            _angle_check(
                terminal_values,
                f"target summary {side} terminal interface angles",
                float(args.terminal_angle_deg),
                float(args.angle_tolerance_deg),
            )
        )
        diagonal_count = _to_int(metrics.get(f"{side}_winding_centerline_diagonal_segment_count"))
        if diagonal_count is not None and diagonal_count > 0:
            checks.append(Check("PASS", f"target summary {side} diagonal segment count", f"count={diagonal_count}"))
        else:
            checks.append(Check("FAIL", f"target summary {side} diagonal segment count", f"count={diagonal_count}"))
    return checks


def _manifest_counts(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest:
        return {}
    geometry = manifest.get("geometry_quality") or {}
    return {
        "ok_count": manifest.get("ok_count"),
        "fail_count": manifest.get("fail_count"),
        "geometry_check_count": geometry.get("geometry_check_count"),
        "geometry_check_ok_count": geometry.get("geometry_check_ok_count"),
        "angle_checked_count": geometry.get("angle_checked_count"),
    }


def _clearance_counts(clearance: dict[str, Any] | None) -> dict[str, Any]:
    if not clearance:
        return {}
    return {
        "candidate_count": clearance.get("candidate_count"),
        "pass_count": clearance.get("pass_count"),
        "reject_count": clearance.get("reject_count"),
        "missing_or_other_count": clearance.get("missing_or_other_count"),
        "selected_cache_key": (clearance.get("selected") or {}).get("cache_key"),
        "selected_status": (clearance.get("selected") or {}).get("status"),
    }


def _layout_counts(layout: dict[str, Any] | None) -> dict[str, Any]:
    if not layout:
        return {}
    ports = list(layout.get("ports") or [])
    return {
        "port_count": len(ports),
        "cadence_pin_purpose": layout.get("cadence_pin_purpose"),
        "grounded_port_count": sum(1 for port in ports if port.get("ground_labels")),
        "signal_labeled_port_count": sum(1 for port in ports if port.get("signal_labels")),
        "internal_signal_labeled_port_count": sum(1 for port in ports if port.get("internal_signal_labels") is True),
        "internal_ground_labeled_port_count": sum(1 for port in ports if port.get("internal_ground_labels") is True),
    }


def _target_summary_counts(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    geometry_check = summary.get("geometry_check") or {}
    metrics = geometry_check.get("metrics") or {}
    return {
        "geometry_error_count": len(geometry_check.get("errors") or []),
        "primary_internal_angle_min": metrics.get("primary_winding_centerline_min_internal_angle_deg"),
        "primary_internal_angle_max": metrics.get("primary_winding_centerline_max_internal_angle_deg"),
        "secondary_internal_angle_min": metrics.get("secondary_winding_centerline_min_internal_angle_deg"),
        "secondary_internal_angle_max": metrics.get("secondary_winding_centerline_max_internal_angle_deg"),
        "primary_terminal_angle_min": metrics.get("primary_winding_centerline_min_terminal_angle_deg"),
        "primary_terminal_angle_max": metrics.get("primary_winding_centerline_max_terminal_angle_deg"),
        "secondary_terminal_angle_min": metrics.get("secondary_winding_centerline_min_terminal_angle_deg"),
        "secondary_terminal_angle_max": metrics.get("secondary_winding_centerline_max_terminal_angle_deg"),
        "advanced_geometry_skipped": metrics.get("skipped"),
        "power_line_8port_geometry_audit_enabled": metrics.get("power_line_8port_geometry_audit_enabled"),
        "power_line_8port_bridge_width_um": metrics.get("power_line_8port_bridge_width_um"),
        "power_line_8port_vertical_length_um": metrics.get("power_line_8port_vertical_length_um"),
    }


def _power_line_8port_counts(power_line: dict[str, Any] | None) -> dict[str, Any]:
    if not power_line:
        return {}
    primary = power_line.get("primary_power_line") or {}
    secondary = power_line.get("secondary_power_line") or {}
    return {
        "enabled": power_line.get("enabled"),
        "placement_policy": power_line.get("placement_policy"),
        "line_width_um": power_line.get("line_width_um"),
        "bridge_width_um": power_line.get("bridge_width_um"),
        "vertical_length_um": power_line.get("vertical_length_um"),
        "max_outer_height_um": power_line.get("max_outer_height_um"),
        "vertical_length_diameter_ratio": power_line.get("vertical_length_diameter_ratio"),
        "expected_vertical_length_um": power_line.get("expected_vertical_length_um"),
        "ground_frame_width_um": power_line.get("ground_frame_width_um"),
        "ground_frame_policy": power_line.get("ground_frame_policy"),
        "port_ground_overlap_um": power_line.get("port_ground_overlap_um"),
        "center_tap_topology": power_line.get("center_tap_topology")
        or _power_line_center_tap_topology(primary, secondary),
        "primary_is_physical_left": (
            None
            if not isinstance(primary, dict)
            or not isinstance(secondary, dict)
            or _to_float(primary.get("center_x_um")) is None
            or _to_float(secondary.get("center_x_um")) is None
            else _to_float(primary.get("center_x_um")) < _to_float(secondary.get("center_x_um"))
        ),
        "has_shield_inner_bbox": isinstance(power_line.get("shield_inner_bbox_um"), dict),
        "has_shield_outer_bbox": isinstance(power_line.get("shield_outer_bbox_um"), dict),
        "primary_height_um": primary.get("height_um") if isinstance(primary, dict) else None,
        "secondary_height_um": secondary.get("height_um") if isinstance(secondary, dict) else None,
        "label_count": len(power_line.get("labels") or {}) if isinstance(power_line.get("labels"), dict) else None,
        "primary_bridge_width_um": (power_line.get("primary_bridge") or {}).get("width_um") if isinstance(power_line.get("primary_bridge"), dict) else None,
        "secondary_bridge_width_um": (power_line.get("secondary_bridge") or {}).get("width_um") if isinstance(power_line.get("secondary_bridge"), dict) else None,
    }


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.__dict__)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Geometry Quality Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Source: `{summary['source']}`",
        f"- Manifest: `{summary['manifest_path']}`",
        f"- Clearance audit: `{summary['clearance_audit_path']}`",
        f"- Layout JSON: `{summary['layout_json_path']}`",
        f"- Target summary JSON: `{summary['summary_json_path']}`",
        f"- Power-line 8-port geometry JSON: `{summary['power_line_8port_geometry_path']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- Manifest counts: `{summary['manifest_counts']}`",
            f"- Clearance counts: `{summary['clearance_counts']}`",
            f"- Layout counts: `{summary['layout_counts']}`",
            f"- Target summary counts: `{summary['target_summary_counts']}`",
            f"- Power-line 8-port counts: `{summary['power_line_8port_counts']}`",
            "",
            "This audit checks geometry metadata and signal-to-shield clearance evidence only. It does not prove S-parameter or Zin coverage.",
        ]
    )
    return "\n".join(lines) + "\n"


def _collect_angle_numbers(value: Any) -> list[float]:
    return _collect_nested_numbers(value, skip_keys={"std"})


def _expected_port_names(args: argparse.Namespace) -> list[str]:
    return [item.strip() for item in str(args.expected_port_names).split(",") if item.strip()]


def _collect_nested_numbers(value: Any, *, skip_keys: set[str] | None = None) -> list[float]:
    numbers: list[float] = []
    skip = skip_keys or set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in skip:
                continue
            numbers.extend(_collect_nested_numbers(child, skip_keys=skip))
    elif isinstance(value, (list, tuple)):
        for child in value:
            numbers.extend(_collect_nested_numbers(child, skip_keys=skip))
    else:
        num = _to_float(value)
        if num is not None:
            numbers.append(num)
    return numbers


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
