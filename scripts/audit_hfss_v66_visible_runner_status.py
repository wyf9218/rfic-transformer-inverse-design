#!/usr/bin/env python3
"""Audit the visible Windows wrapper for the HFSS V66 run."""

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
DEFAULT_WRAPPER = DEFAULT_PLAN_DIR / "run_hfss_v66_calibration_visible.windows.ps1"
DEFAULT_CMD_LAUNCHER = DEFAULT_PLAN_DIR / "run_hfss_v66_calibration_visible.windows.cmd"
DEFAULT_RUNNER = DEFAULT_PLAN_DIR / "run_hfss_v66_calibration.windows.ps1"
DEFAULT_STATUS = DEFAULT_PLAN_DIR / "visible_run_status" / "hfss_v66_visible_run_status.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_visible_runner_audit_current"
DEFAULT_EXPECTED_PORTS = 8
DEFAULT_EXPECTED_START_GHZ = 5.0
DEFAULT_EXPECTED_STOP_GHZ = 60.0
DEFAULT_EXPECTED_STEP_GHZ = 1.0
DEFAULT_EXPECTED_POINTS = 56


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wrapper = Path(args.visible_wrapper).expanduser().resolve()
    cmd_launcher = Path(args.cmd_launcher).expanduser().resolve()
    runner = Path(args.base_runner).expanduser().resolve()
    status_path = Path(args.status_json).expanduser().resolve()
    status = _read_json(status_path)
    checks = _checks(wrapper, cmd_launcher, runner, status_path, status, args)
    overall_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"
    decision = _decision(overall_status, status_path, status)
    filesystem_exports = _filesystem_exports(wrapper.parent)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "visible_wrapper": str(wrapper),
        "cmd_launcher": str(cmd_launcher),
        "base_runner": str(runner),
        "status_json": str(status_path),
        "status_json_exists": status_path.is_file(),
        "visible_status": status,
        "exported_s8p_count": int(status.get("exported_s8p_count") or 0) if isinstance(status, dict) else 0,
        "export_manifest_count": int(status.get("export_manifest_count") or 0) if isinstance(status, dict) else 0,
        "expected_variant_count": _expected_variant_count(wrapper.parent, status),
        "filesystem_exported_s8p_count": len(filesystem_exports["s8p_files"]),
        "filesystem_export_manifest_count": len(filesystem_exports["manifest_files"]),
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
            "PASS without a status JSON means the wrapper is ready but has not been run in Windows yet.",
            "A completed PASS state is valid only when every V66 variant has an HFSS .s8p and export manifest.",
            "Completed HFSS .s8p files are parsed locally and must match the 8-port, 5-60 GHz, 1.0 GHz, 56-point contract.",
            "The visible wrapper does not replace the EMX/HFSS 10% validation gate.",
            "After the wrapper runs, use the V66-to-million monitor to run postrun validation and gates.",
        ],
    }
    summary_path = out_dir / "hfss_v66_visible_runner_audit_summary.json"
    report_path = out_dir / "HFSS_V66_VISIBLE_RUNNER_AUDIT_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"exported_s8p_count={summary['exported_s8p_count']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-wrapper", default=str(DEFAULT_WRAPPER))
    parser.add_argument("--cmd-launcher", default=str(DEFAULT_CMD_LAUNCHER))
    parser.add_argument("--base-runner", default=str(DEFAULT_RUNNER))
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
    wrapper: Path,
    cmd_launcher: Path,
    runner: Path,
    status_path: Path,
    status: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    wrapper_text = wrapper.read_text(encoding="utf-8", errors="replace") if wrapper.is_file() else ""
    cmd_text = cmd_launcher.read_text(encoding="utf-8", errors="replace") if cmd_launcher.is_file() else ""
    exports = _filesystem_exports(wrapper.parent)
    expected_variant_count = _expected_variant_count(wrapper.parent, status)
    checks = [
        _check("visible wrapper exists", wrapper.is_file(), str(wrapper)),
        _check("cmd launcher exists", cmd_launcher.is_file(), str(cmd_launcher)),
        _check("base runner exists", runner.is_file(), str(runner)),
        _check("cmd launcher calls PowerShell", "powershell" in cmd_text.lower(), "powershell.exe"),
        _check("cmd launcher bypasses execution policy", "ExecutionPolicy Bypass" in cmd_text, "ExecutionPolicy Bypass"),
        _check("cmd launcher calls visible wrapper", "run_hfss_v66_calibration_visible.windows.ps1" in cmd_text, "visible wrapper"),
        _check("visible wrapper starts transcript", "Start-Transcript" in wrapper_text, "Start-Transcript"),
        _check("visible wrapper writes JSON status", "hfss_v66_visible_run_status.json" in wrapper_text and "ConvertTo-Json" in wrapper_text, "status JSON"),
        _check("visible wrapper invokes base runner", "run_hfss_v66_calibration.windows.ps1" in wrapper_text, "base runner"),
        _check("visible wrapper rejects completed runs without exports", "completed_hfss_v66_runner_no_exports" in wrapper_text, "no-export guard"),
        _check("visible wrapper requires per-variant exports", "ExpectedVariantCount" in wrapper_text, "expected variant count"),
    ]
    if status_path.is_file():
        checks.append(_check("status JSON parses", "_parse_error" not in status, status.get("_parse_error", "JSON object")))
        checks.append(_check("status has phase", bool(status.get("phase")), str(status.get("phase", ""))))
        checks.append(_check("status has status", str(status.get("status") or "") in {"RUNNING", "PASS", "FAIL"}, str(status.get("status", ""))))
        checks.append(_check("status not failed", str(status.get("status") or "") != "FAIL", str(status.get("error", ""))))
        if str(status.get("status") or "") == "PASS":
            checks.append(
                _check(
                    "completed PASS has exported S8P",
                    _int_value(status.get("exported_s8p_count")) >= expected_variant_count,
                    f"status={status.get('exported_s8p_count', '')}, expected>={expected_variant_count}",
                )
            )
            checks.append(
                _check(
                    "completed PASS has export manifest",
                    _int_value(status.get("export_manifest_count")) >= expected_variant_count,
                    f"status={status.get('export_manifest_count', '')}, expected>={expected_variant_count}",
                )
            )
            checks.append(
                _check(
                    "completed PASS filesystem S8P count covers variants",
                    len(exports["s8p_files"]) >= expected_variant_count,
                    f"filesystem={len(exports['s8p_files'])}, expected>={expected_variant_count}",
                )
            )
            checks.append(
                _check(
                    "completed PASS filesystem manifest count covers variants",
                    len(exports["manifest_files"]) >= expected_variant_count,
                    f"filesystem={len(exports['manifest_files'])}, expected>={expected_variant_count}",
                )
            )
            for touchstone_path in exports["s8p_files"]:
                checks.extend(_touchstone_contract_checks(touchstone_path, args))
    return checks


def _decision(overall_status: str, status_path: Path, status: dict[str, Any]) -> str:
    if overall_status == "FAIL":
        if str(status.get("status") or "") == "PASS" and (
            _int_value(status.get("exported_s8p_count")) < 1 or _int_value(status.get("export_manifest_count")) < 1
        ):
            return "FIX_VISIBLE_HFSS_RUNNER_COMPLETED_WITHOUT_EXPORTS"
        if str(status.get("status") or "") == "PASS":
            return "FIX_VISIBLE_HFSS_RUNNER_COMPLETED_WITH_INVALID_EXPORTS"
        return "FIX_VISIBLE_HFSS_RUNNER_OR_FAILED_WINDOWS_RUN"
    if not status_path.is_file():
        return "VISIBLE_HFSS_RUNNER_READY_NOT_YET_RUN"
    visible_status = str(status.get("status") or "")
    if visible_status == "RUNNING":
        return "VISIBLE_HFSS_RUNNER_RUNNING_WAIT_FOR_EXPORTS"
    if visible_status == "PASS":
        return "VISIBLE_HFSS_RUNNER_COMPLETED_RUN_POSTRUN_MONITOR"
    return "VISIBLE_HFSS_RUNNER_READY"


def _filesystem_exports(plan_dir: Path) -> dict[str, list[Path]]:
    variants_dir = plan_dir / "variants"
    return {
        "s8p_files": sorted(variants_dir.rglob("*.s8p")) if variants_dir.is_dir() else [],
        "manifest_files": sorted(variants_dir.rglob("*export*manifest*.json")) if variants_dir.is_dir() else [],
    }


def _expected_variant_count(plan_dir: Path, status: dict[str, Any]) -> int:
    status_count = _int_value(status.get("expected_variant_count")) if isinstance(status, dict) else 0
    if status_count >= 1:
        return status_count
    variants_dir = plan_dir / "variants"
    if not variants_dir.is_dir():
        return 1
    count = len([item for item in variants_dir.iterdir() if item.is_dir()])
    return max(count, 1)


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
        _check(
            f"Touchstone frequency point count: {label}",
            len(freqs) == int(args.expected_frequency_points),
            f"points={len(freqs)}",
        ),
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


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS V66 Visible Runner Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Exported S8P count: `{summary['exported_s8p_count']}`",
        f"- Export manifest count: `{summary['export_manifest_count']}`",
        f"- Expected variant count: `{summary['expected_variant_count']}`",
        f"- Filesystem S8P count: `{summary['filesystem_exported_s8p_count']}`",
        f"- Filesystem manifest count: `{summary['filesystem_export_manifest_count']}`",
        f"- Status JSON exists: `{summary['status_json_exists']}`",
        "",
        "## Touchstone Contract",
        "",
        f"- Ports: `{summary['touchstone_contract']['expected_ports']}`",
        (
            "- Frequency: "
            f"`{summary['touchstone_contract']['expected_frequency_start_ghz']}-"
            f"{summary['touchstone_contract']['expected_frequency_stop_ghz']} GHz`, "
            f"step `{summary['touchstone_contract']['expected_frequency_step_ghz']} GHz`, "
            f"points `{summary['touchstone_contract']['expected_frequency_points']}`"
        ),
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {item['status']}: {item['name']} - {item['detail']}" for item in summary["checks"])
    lines.extend(["", "## Method Notes", ""])
    lines.extend(f"- {item}" for item in summary["method_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
