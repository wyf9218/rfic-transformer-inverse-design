#!/usr/bin/env python3
"""Preflight a dataset-generation config before spending EM/cluster time."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.core import load_run_config


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


@dataclass(frozen=True)
class FrequencySpec:
    start_hz: float
    stop_hz: float
    step_hz: float
    points: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight a sample-dataset config for the grounded-shield wideband "
            "training workflow. This script is read-only: it loads a config and "
            "checks frequency grid, port mode, pin purpose, shield settings, and "
            "optionally required EMX/Cadence paths."
        )
    )
    parser.add_argument("config", help="Run-config YAML path")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument("--expected-port-mode", default="single_ended_shield_grounded")
    parser.add_argument("--expected-pin-purpose", type=int, default=51)
    parser.add_argument("--require-shield", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--check-emx-paths", action="store_true", help="Fail when required EMX/Cadence paths are placeholders or missing")
    parser.add_argument(
        "--forbid-dry-run-paths",
        action="store_true",
        help="Fail when EMX/Cadence fields contain local dry-run placeholders such as /usr/bin/true or /tmp/mars_dryrun.",
    )
    parser.add_argument("--report", default=None, help="Markdown report path")
    parser.add_argument("--summary", default=None, help="JSON summary path")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve() if args.report else config_path.with_suffix(".preflight_report.md")
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else config_path.with_suffix(".preflight_summary.json")

    checks: list[Check] = []
    config = None
    load_error = None
    try:
        config = load_run_config(config_path)
        checks.append(Check("PASS", "config loads", str(config_path)))
    except Exception as exc:  # noqa: BLE001 - report the config loader failure verbatim.
        load_error = f"{type(exc).__name__}: {exc}"
        checks.append(Check("FAIL", "config loads", load_error))

    if config is not None:
        expected = FrequencySpec(
            start_hz=args.expected_frequency_start_ghz * 1.0e9,
            stop_hz=args.expected_frequency_stop_ghz * 1.0e9,
            step_hz=args.expected_frequency_step_ghz * 1.0e9,
            points=int(args.expected_frequency_points),
        )
        _check_frequency_grid(checks, config, expected, float(args.frequency_tolerance_hz))
        _check_port_mode(checks, config, str(args.expected_port_mode))
        _check_pin_purpose(checks, config, int(args.expected_pin_purpose))
        _check_shield(checks, config, bool(args.require_shield))
        _check_emx_paths(checks, config, bool(args.check_emx_paths), bool(args.forbid_dry_run_paths))

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    summary = {
        "overall_status": overall_status,
        "config": str(config_path),
        "load_error": load_error,
        "expected_frequency": {
            "start_hz": args.expected_frequency_start_ghz * 1.0e9,
            "stop_hz": args.expected_frequency_stop_ghz * 1.0e9,
            "step_hz": args.expected_frequency_step_ghz * 1.0e9,
            "points": int(args.expected_frequency_points),
        },
        "checks": [check.as_dict() for check in checks],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    for check in checks:
        print(f"{check.status} {check.name}: {check.detail}")
    if overall_status == "FAIL" and not args.no_fail_exit:
        return 2
    return 0


def _check_frequency_grid(checks: list[Check], config: Any, expected: FrequencySpec, tolerance_hz: float) -> None:
    actual_points = config.target.frequency_points_hz()
    if len(actual_points) < 2:
        checks.append(Check("FAIL", "frequency grid", "Frequency grid has fewer than 2 points"))
        return
    actual = FrequencySpec(
        start_hz=float(actual_points[0]),
        stop_hz=float(actual_points[-1]),
        step_hz=float(actual_points[1] - actual_points[0]),
        points=int(len(actual_points)),
    )
    mismatch = _frequency_mismatch_detail(actual, expected, tolerance_hz)
    if mismatch:
        checks.append(Check("FAIL", "frequency grid", mismatch))
        return
    checks.append(
        Check(
            "PASS",
            "frequency grid",
            f"{actual.start_hz / 1.0e9:.12g}-{actual.stop_hz / 1.0e9:.12g} GHz, "
            f"step {actual.step_hz / 1.0e9:.12g} GHz, points={actual.points}",
        )
    )


def _check_port_mode(checks: list[Check], config: Any, expected: str) -> None:
    actual = str(config.emx.port_mode)
    if actual == expected:
        checks.append(Check("PASS", "port mode", actual))
    else:
        checks.append(Check("FAIL", "port mode", f"got {actual!r}, expected {expected!r}"))


def _check_pin_purpose(checks: list[Check], config: Any, expected: int) -> None:
    actual = int(config.emx.cadence_pin_purpose)
    if actual == expected:
        checks.append(Check("PASS", "cadence pin purpose", str(actual)))
    else:
        checks.append(Check("FAIL", "cadence pin purpose", f"got {actual}, expected {expected}"))


def _check_shield(checks: list[Check], config: Any, require_shield: bool) -> None:
    shield = config.bounds.shield
    if not require_shield:
        checks.append(Check("WARN", "shield", "Shield requirement disabled for this preflight"))
        return
    if shield.enabled and shield.kind == "ring" and shield.margin_um is not None and shield.width_um is not None:
        checks.append(
            Check(
                "PASS",
                "shield",
                f"enabled ring, margin_um={float(shield.margin_um):.6g}, width_um={float(shield.width_um):.6g}",
            )
        )
    else:
        checks.append(Check("FAIL", "shield", f"enabled={shield.enabled}, kind={shield.kind}, margin={shield.margin_um}, width={shield.width_um}"))


def _check_emx_paths(checks: list[Check], config: Any, check_paths: bool, forbid_dry_run_paths: bool) -> None:
    fields = {
        "emx_binary": config.emx.emx_binary,
        "emx_process_file": config.emx.emx_process_file,
        "cadence_install_root": config.emx.cadence_install_root,
        "cadence_pdk_cds_lib": config.emx.cadence_pdk_cds_lib,
        "cadence_layer_map": config.emx.cadence_layer_map,
    }
    bad: list[tuple[str, str]] = []
    placeholders: list[str] = []
    dry_run_paths: list[tuple[str, str]] = []
    for name, raw_value in fields.items():
        value = "" if raw_value is None else str(raw_value)
        if _looks_like_placeholder(value):
            placeholders.append(name)
            if check_paths:
                bad.append((name, "placeholder"))
            continue
        if _looks_like_dry_run_path(name, value):
            dry_run_paths.append((name, value))
            if forbid_dry_run_paths:
                bad.append((name, "dry-run-placeholder"))
            continue
        if check_paths and (not value or not Path(value).expanduser().exists()):
            bad.append((name, "missing"))
    if check_paths:
        cadence_root = fields.get("cadence_install_root")
        if cadence_root and not _looks_like_placeholder(str(cadence_root)) and not _looks_like_dry_run_path("cadence_install_root", str(cadence_root)):
            for tool in ("dbAccess", "strmin", "strmout"):
                tool_path = Path(str(cadence_root)).expanduser() / "bin" / tool
                if not (tool_path.exists() and os.access(tool_path, os.X_OK)):
                    bad.append((f"cadence_install_root/bin/{tool}", "missing-or-not-executable"))
    if bad:
        checks.append(Check("FAIL", "EMX/Cadence paths", f"Bad entries: {bad}"))
    elif dry_run_paths:
        checks.append(Check("WARN", "EMX/Cadence paths", f"Dry-run paths still present: {dry_run_paths}"))
    elif placeholders:
        checks.append(Check("WARN", "EMX/Cadence paths", f"Placeholders still present: {placeholders}"))
    else:
        checks.append(Check("PASS", "EMX/Cadence paths", "No placeholders detected" + (" and paths exist" if check_paths else "")))


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


def _looks_like_placeholder(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    upper = stripped.upper()
    return "REPLACE" in upper or stripped.startswith("/REPLACE/")


def _looks_like_dry_run_path(name: str, value: str) -> bool:
    text = str(value).strip()
    lowered = text.lower()
    if name == "emx_binary" and text == "/usr/bin/true":
        return True
    dry_run_markers = (
        "/tmp/mars_dryrun",
        "mars_dryrun",
        "/tmp/dryrun",
        "dry-run",
    )
    return any(marker in lowered for marker in dry_run_markers)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# RFIC Transformer Dataset Config Preflight",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Config: `{summary['config']}`",
        "",
        "## Expected Frequency",
        "```json",
        json.dumps(summary["expected_frequency"], indent=2),
        "```",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
