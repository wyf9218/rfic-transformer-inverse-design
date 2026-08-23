#!/usr/bin/env python3
"""Audit the resilient Windows runner for the HFSS V66 sweep."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current"
DEFAULT_PACKET_SUMMARY = DEFAULT_PLAN_DIR / "hfss_v66_resilient_runner_packet_summary.json"
DEFAULT_RUNNER = DEFAULT_PLAN_DIR / "run_hfss_v66_calibration_resilient.windows.ps1"
DEFAULT_CMD_LAUNCHER = DEFAULT_PLAN_DIR / "run_hfss_v66_calibration_resilient.windows.cmd"
DEFAULT_STATUS = DEFAULT_PLAN_DIR / "resilient_run_status" / "hfss_v66_resilient_run_status.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_resilient_runner_audit_current"
DEFAULT_EXPECTED_PORTS = 8
DEFAULT_EXPECTED_START_GHZ = 5.0
DEFAULT_EXPECTED_STOP_GHZ = 60.0
DEFAULT_EXPECTED_STEP_GHZ = 1.0
DEFAULT_EXPECTED_POINTS = 56


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_summary_path = Path(args.packet_summary).expanduser().resolve()
    runner = Path(args.runner).expanduser().resolve()
    cmd_launcher = Path(args.cmd_launcher).expanduser().resolve()
    status_path = Path(args.status_json).expanduser().resolve()
    packet = _read_json(packet_summary_path)
    status = _read_json(status_path)
    exports = _filesystem_exports(runner.parent)
    checks = _checks(packet_summary_path, packet, runner, cmd_launcher, status_path, status, exports, args)
    overall_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    decision = _decision(overall_status, status_path, status)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "packet_summary": str(packet_summary_path),
        "runner": str(runner),
        "cmd_launcher": str(cmd_launcher),
        "status_json": str(status_path),
        "status_json_exists": status_path.is_file(),
        "packet_status": packet,
        "resilient_status": status,
        "expected_variant_count": _expected_variant_count(packet, status),
        "pass_count": _int_value(status.get("pass_count")) if isinstance(status, dict) else 0,
        "fail_count": _int_value(status.get("fail_count")) if isinstance(status, dict) else 0,
        "filesystem_exported_s8p_count": len(exports["s8p_files"]),
        "filesystem_export_manifest_count": len(exports["manifest_files"]),
        "touchstone_contract": {
            "expected_ports": int(args.expected_ports),
            "expected_frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "expected_frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
            "frequency_tolerance_hz": float(args.frequency_tolerance_hz),
        },
        "checks": checks,
        "method_notes": [
            "PASS without a resilient status JSON means the resilient runner packet is ready but has not been run in Windows yet.",
            "A completed resilient PASS requires at least one variant-level .s8p and export manifest.",
            "Any exported .s8p found under variants/ is parsed locally and must match the 8-port, 5-60 GHz, 1.0 GHz, 56-point contract.",
            "This audit does not replace the EMX/HFSS 10% postrun validation gate.",
        ],
    }
    summary_path = out_dir / "hfss_v66_resilient_runner_audit_summary.json"
    report_path = out_dir / "HFSS_V66_RESILIENT_RUNNER_AUDIT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"exported_s8p_count={summary['filesystem_exported_s8p_count']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-summary", default=str(DEFAULT_PACKET_SUMMARY))
    parser.add_argument("--runner", default=str(DEFAULT_RUNNER))
    parser.add_argument("--cmd-launcher", default=str(DEFAULT_CMD_LAUNCHER))
    parser.add_argument("--status-json", default=str(DEFAULT_STATUS))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-ports", type=int, default=DEFAULT_EXPECTED_PORTS)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=DEFAULT_EXPECTED_START_GHZ)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=DEFAULT_EXPECTED_STOP_GHZ)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=DEFAULT_EXPECTED_STEP_GHZ)
    parser.add_argument("--expected-frequency-points", type=int, default=DEFAULT_EXPECTED_POINTS)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _checks(
    packet_summary_path: Path,
    packet: dict[str, Any],
    runner: Path,
    cmd_launcher: Path,
    status_path: Path,
    status: dict[str, Any],
    exports: dict[str, list[Path]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    runner_text = runner.read_text(encoding="utf-8", errors="replace") if runner.is_file() else ""
    cmd_text = cmd_launcher.read_text(encoding="utf-8", errors="replace") if cmd_launcher.is_file() else ""
    expected_variant_count = _expected_variant_count(packet, status)
    checks = [
        _check("resilient packet summary exists", packet_summary_path.is_file(), str(packet_summary_path)),
        _check("resilient packet summary PASS", packet.get("overall_status") == "PASS", str(packet.get("overall_status"))),
        _check("resilient runner exists", runner.is_file(), str(runner)),
        _check("cmd launcher exists", cmd_launcher.is_file(), str(cmd_launcher)),
        _check("cmd launcher calls PowerShell", "powershell" in cmd_text.lower(), "powershell.exe"),
        _check("cmd launcher bypasses execution policy", "ExecutionPolicy Bypass" in cmd_text, "ExecutionPolicy Bypass"),
        _check("cmd launcher calls resilient runner", "run_hfss_v66_calibration_resilient.windows.ps1" in cmd_text, "resilient runner"),
        _check("resilient runner starts transcript", "Start-Transcript" in runner_text, "Start-Transcript"),
        _check("resilient runner writes JSON status", "hfss_v66_resilient_run_status.json" in runner_text and "ConvertTo-Json" in runner_text, "status JSON"),
        _check("resilient runner has per-variant function", "function Run-V66Variant" in runner_text, "Run-V66Variant"),
        _check(
            "resilient runner call count matches variants",
            runner_text.count("$VariantResults += Run-V66Variant") == expected_variant_count,
            f"calls={runner_text.count('$VariantResults += Run-V66Variant')}, expected={expected_variant_count}",
        ),
        _check("resilient runner uses PowerShell backtick continuation", "Run-V66Variant `" in runner_text, "Run-V66Variant `"),
        _check("resilient runner has no shell backslash continuation", "Run-V66Variant \\" not in runner_text, "no Run-V66Variant \\"),
        _check("resilient runner requires exports", "Variant completed but did not produce both .s8p and export manifest." in runner_text, "export guard"),
    ]
    if status_path.is_file():
        checks.append(_check("status JSON parses", "_parse_error" not in status, status.get("_parse_error", "JSON object")))
        checks.append(_check("status has status", str(status.get("overall_status") or "") in {"PASS", "FAIL"}, str(status.get("overall_status", ""))))
        if str(status.get("overall_status") or "") == "PASS":
            pass_count = _int_value(status.get("pass_count"))
            checks.append(_check("completed resilient PASS has passing variant", pass_count >= 1, f"pass_count={pass_count}"))
            checks.append(
                _check(
                    "completed resilient PASS has filesystem S8P",
                    len(exports["s8p_files"]) >= pass_count,
                    f"filesystem={len(exports['s8p_files'])}, pass_count={pass_count}",
                )
            )
            checks.append(
                _check(
                    "completed resilient PASS has export manifest",
                    len(exports["manifest_files"]) >= pass_count,
                    f"filesystem={len(exports['manifest_files'])}, pass_count={pass_count}",
                )
            )
            for touchstone_path in exports["s8p_files"]:
                checks.extend(_touchstone_contract_checks(touchstone_path, args))
        elif str(status.get("overall_status") or "") == "FAIL":
            checks.append(_check("completed resilient run produced at least one passing variant", False, str(status.get("decision") or "")))
    return checks


def _decision(overall_status: str, status_path: Path, status: dict[str, Any]) -> str:
    if overall_status == "FAIL":
        if str(status.get("overall_status") or "") == "PASS":
            return "FIX_RESILIENT_HFSS_RUNNER_COMPLETED_WITH_INVALID_EXPORTS"
        if str(status.get("overall_status") or "") == "FAIL":
            return "FIX_RESILIENT_HFSS_RUNNER_WINDOWS_FAILURES"
        return "FIX_RESILIENT_HFSS_RUNNER_PACKET"
    if not status_path.is_file():
        return "RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN"
    if str(status.get("overall_status") or "") == "PASS":
        return "RESILIENT_HFSS_RUNNER_COMPLETED_RUN_POSTRUN_MONITOR"
    return "RESILIENT_HFSS_RUNNER_READY"


def _filesystem_exports(plan_dir: Path) -> dict[str, list[Path]]:
    variants_dir = plan_dir / "variants"
    return {
        "s8p_files": sorted(variants_dir.rglob("*.s8p")) if variants_dir.is_dir() else [],
        "manifest_files": sorted(variants_dir.rglob("*export*manifest*.json")) if variants_dir.is_dir() else [],
    }


def _expected_variant_count(packet: dict[str, Any], status: dict[str, Any]) -> int:
    for value in (status.get("expected_variant_count"), packet.get("variant_count")):
        count = _int_value(value)
        if count >= 1:
            return count
    return 1


def _touchstone_contract_checks(path: Path, args: argparse.Namespace) -> list[dict[str, str]]:
    label = path.name
    try:
        data = load_touchstone(path)
    except Exception as exc:
        return [_check(f"Touchstone parses: {label}", False, f"{type(exc).__name__}: {exc}")]
    freqs = data.freqs_hz
    ports = int(data.s_matrix.shape[1]) if data.s_matrix.ndim == 3 else 0
    start_hz = float(args.expected_frequency_start_ghz) * 1.0e9
    stop_hz = float(args.expected_frequency_stop_ghz) * 1.0e9
    step_hz = float(args.expected_frequency_step_ghz) * 1.0e9
    tolerance_hz = float(args.frequency_tolerance_hz)
    checks = [
        _check(f"Touchstone suffix is .s8p: {label}", path.suffix.lower() == ".s8p", str(path)),
        _check(f"Touchstone port count: {label}", ports == int(args.expected_ports), f"ports={ports}"),
        _check(f"Touchstone frequency point count: {label}", len(freqs) == int(args.expected_frequency_points), f"points={len(freqs)}"),
        _check(
            f"Touchstone frequency start: {label}",
            bool(len(freqs)) and math.isclose(float(freqs[0]), start_hz, abs_tol=tolerance_hz),
            f"start_hz={float(freqs[0]) if len(freqs) else 'missing'}",
        ),
        _check(
            f"Touchstone frequency stop: {label}",
            bool(len(freqs)) and math.isclose(float(freqs[-1]), stop_hz, abs_tol=tolerance_hz),
            f"stop_hz={float(freqs[-1]) if len(freqs) else 'missing'}",
        ),
    ]
    if len(freqs) >= 2:
        diffs = [float(freqs[index + 1] - freqs[index]) for index in range(len(freqs) - 1)]
        max_step_error = max(abs(item - step_hz) for item in diffs)
        checks.append(
            _check(
                f"Touchstone frequency step: {label}",
                max_step_error <= tolerance_hz,
                f"expected_hz={step_hz}, max_step_error_hz={max_step_error}",
            )
        )
    else:
        checks.append(_check(f"Touchstone frequency step: {label}", False, "not enough points"))
    return checks


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _check(name: str, condition: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if condition else "FAIL", "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS V66 Resilient Runner Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Expected variants: `{summary['expected_variant_count']}`",
        f"- Exported `.s8p` files: `{summary['filesystem_exported_s8p_count']}`",
        f"- Export manifests: `{summary['filesystem_export_manifest_count']}`",
        f"- Status JSON exists: `{summary['status_json_exists']}`",
        "",
        "## Checks",
        "",
    ]
    for item in summary.get("checks") or []:
        lines.append(f"- {item['status']}: {item['name']} - `{item['detail']}`")
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            "- If decision is `RESILIENT_HFSS_RUNNER_READY_NOT_YET_RUN`, run the `.cmd` launcher in Windows/HFSS.",
            "- If decision is `RESILIENT_HFSS_RUNNER_COMPLETED_RUN_POSTRUN_MONITOR`, run the V66 postrun/monitor gate to compare EMX and HFSS physical curves.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
