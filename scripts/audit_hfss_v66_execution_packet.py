#!/usr/bin/env python3
"""Audit the HFSS V66 execution packet before/after Windows runs.

The audit checks that the V66 plan, Windows runner, postrun validator, per-
variant payloads, copied build/solve scripts, and EMX reference S8P files agree
on the final full-band contract. It does not run HFSS and does not claim
EMX/HFSS agreement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_WINDOWS = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "run_hfss_v66_calibration.windows.ps1"
DEFAULT_POSTRUN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "postrun_validate_hfss_v66_calibration.sh"
DEFAULT_WATCH = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_to_million_gate_watch_current" / "hfss_v66_calibration_to_million_gate_watch_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_execution_packet_audit_current"


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    windows_path = Path(args.windows_runner).expanduser().resolve()
    postrun_path = Path(args.postrun_script).expanduser().resolve()
    watch_path = Path(args.watch_summary).expanduser().resolve() if args.watch_summary else None
    plan = _read_json(plan_path)
    windows_text = _read_text(windows_path)
    postrun_text = _read_text(postrun_path)
    watch = _read_json(watch_path) if watch_path else {}
    contract = _expected_contract(args)

    variants = [
        _variant_record(item, windows_text, postrun_text, contract, args)
        for item in plan.get("variants") or []
        if isinstance(item, dict)
    ]
    checks = _global_checks(plan_path, plan, windows_path, windows_text, postrun_path, postrun_text, watch_path, watch, contract, args)
    checks.extend(check for variant in variants for check in variant.pop("_checks"))
    result_status = _result_status(variants)
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    decision = _decision(overall_status, result_status)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "hfss_result_status": result_status,
        "plan_summary": str(plan_path),
        "windows_runner": str(windows_path),
        "postrun_script": str(postrun_path),
        "watch_summary": "" if watch_path is None else str(watch_path),
        "out_dir": str(out_dir),
        "expected_contract": contract,
        "variant_count": len(variants),
        "exported_s8p_count": sum(len(item["exported_s8p"]) for item in variants),
        "export_manifest_count": sum(1 for item in variants if item["export_manifest_exists"]),
        "variants": variants,
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "PASS means the V66 execution packet is internally consistent, not that HFSS has passed EMX correlation.",
            "WAITING_FOR_HFSS_EXPORT is expected before Windows/HFSS writes `.s8p` files.",
            "Million-sample generation remains gated by V66 postrun plus the million planner checks.",
        ],
    }
    summary_path = out_dir / "hfss_v66_execution_packet_audit_summary.json"
    report_path = out_dir / "HFSS_V66_EXECUTION_PACKET_AUDIT_CN.md"
    checks_csv = out_dir / "hfss_v66_execution_packet_audit_checks.csv"
    variants_csv = out_dir / "hfss_v66_execution_packet_audit_variants.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(checks_csv, checks)
    _write_variants_csv(variants_csv, variants)

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"hfss_result_status={result_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--windows-runner", default=str(DEFAULT_WINDOWS))
    parser.add_argument("--postrun-script", default=str(DEFAULT_POSTRUN))
    parser.add_argument("--watch-summary", default=str(DEFAULT_WATCH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-variant-count", type=int, default=8)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _expected_contract(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "hfss_touchstone_suffix": ".s8p",
        "expected_ports": int(args.expected_ports),
        "compare_start_ghz": float(args.expected_frequency_start_ghz),
        "compare_stop_ghz": float(args.expected_frequency_stop_ghz),
        "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
        "expected_frequency_points": int(args.expected_frequency_points),
        "target_ghz": float(args.target_ghz),
        "max_percent_error": float(args.max_percent_error),
        "final_acceptance_candidate": True,
    }


def _global_checks(
    plan_path: Path,
    plan: dict[str, Any],
    windows_path: Path,
    windows_text: str,
    postrun_path: Path,
    postrun_text: str,
    watch_path: Path | None,
    watch: dict[str, Any],
    contract: dict[str, Any],
    args: argparse.Namespace,
) -> list[Check]:
    plan_contract = plan.get("postrun_validation_contract") if isinstance(plan.get("postrun_validation_contract"), dict) else {}
    checks = [
        _check("V66 plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("V66 plan status passed", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("V66 plan decision is diagnostic sweep", plan.get("decision") == "RUN_V66_HFSS_DIAGNOSTIC_SWEEP_BEFORE_FULL_VALIDATION", str(plan.get("decision"))),
        _check("V66 variant count is expected", len(plan.get("variants") or []) == int(args.expected_variant_count), f"variants={len(plan.get('variants') or [])}"),
        _check("V66 plan final acceptance candidate", bool(plan_contract.get("final_acceptance_candidate")), str(plan_contract.get("final_acceptance_candidate"))),
        _float_equals("V66 contract start GHz", plan_contract.get("compare_start_ghz"), contract["compare_start_ghz"], 1e-9),
        _float_equals("V66 contract stop GHz", plan_contract.get("compare_stop_ghz"), contract["compare_stop_ghz"], 1e-9),
        _float_equals("V66 contract step GHz", plan_contract.get("expected_frequency_step_ghz"), contract["expected_frequency_step_ghz"], 1e-9),
        _int_equals("V66 contract point count", plan_contract.get("expected_frequency_points"), contract["expected_frequency_points"]),
        _int_equals("V66 contract ports", plan_contract.get("expected_ports"), contract["expected_ports"]),
        _check("Windows runner exists", windows_path.is_file(), str(windows_path)),
        _check("Windows runner invokes build script", "build_hfss_s8p_from_payload.py" in windows_text, "build_hfss_s8p_from_payload.py"),
        _check("Windows runner invokes solve script", "solve_export_hfss_s8p.py" in windows_text, "solve_export_hfss_s8p.py"),
        _check("Windows runner uses variant payloads", "HFSS_S8P_PAYLOAD" in windows_text and "hfss_v66_calibration_plan_current" in windows_text, "HFSS_S8P_PAYLOAD"),
        _check("Windows runner does not use old source payload path", "hfss_aedt_v65_lp_ls_diag_15_15p5" not in windows_text, "old V65 source path absent"),
        _check("postrun script exists", postrun_path.is_file(), str(postrun_path)),
        _check("postrun script is executable", bool(postrun_path.exists() and os.access(postrun_path, os.X_OK)), oct(postrun_path.stat().st_mode & 0o777) if postrun_path.exists() else "missing"),
        _check("postrun uses final start GHz", f"--compare-start-ghz {contract['compare_start_ghz']:g}" in postrun_text, str(contract["compare_start_ghz"])),
        _check("postrun uses final stop GHz", f"--compare-stop-ghz {contract['compare_stop_ghz']:g}" in postrun_text, str(contract["compare_stop_ghz"])),
        _check("postrun uses final point count", f"--expected-frequency-points {contract['expected_frequency_points']}" in postrun_text, str(contract["expected_frequency_points"])),
    ]
    if watch_path is not None:
        checks.extend(
            [
                _check("V66-to-million watch summary exists", watch_path.is_file(), str(watch_path)),
                _check(
                    "V66-to-million watch status recognized",
                    str(watch.get("overall_status") or "") in {"WAITING_FOR_HFSS", "WAITING_FOR_CAMPAIGN_PLANNER", "PASS", "FAIL"},
                    str(watch.get("overall_status")),
                ),
            ]
        )
    return checks


def _variant_record(
    variant: dict[str, Any],
    windows_text: str,
    postrun_text: str,
    contract: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    name = str(variant.get("name") or "")
    variant_dir = Path(str(variant.get("variant_dir") or "")).expanduser()
    payload_path = Path(str(variant.get("payload_json") or "")).expanduser()
    build_script = Path(str(variant.get("build_script") or "")).expanduser()
    solve_script = Path(str(variant.get("solve_script") or "")).expanduser()
    packet_summary = Path(str(variant.get("single_variant_packet_summary") or "")).expanduser()
    hfss_results_dir = Path(str(variant.get("hfss_results_dir") or "")).expanduser()
    export_manifest = Path(str(variant.get("hfss_export_manifest") or "")).expanduser()
    postrun_out_dir = Path(str(variant.get("postrun_out_dir") or "")).expanduser()
    payload = _read_json(payload_path)
    packet = _read_json(packet_summary)
    grid = payload.get("frequency_grid") if isinstance(payload.get("frequency_grid"), dict) else {}
    source_files = payload.get("source_files") if isinstance(payload.get("source_files"), dict) else {}
    emx_s8p = Path(str(source_files.get("emx_s8p") or "")).expanduser()
    emx_audit = _inspect_s8p(emx_s8p, contract, args)
    exported_s8p = sorted(str(path) for path in variant_dir.rglob("*.s8p")) if variant_dir.exists() else []
    exported_audits = [_inspect_s8p(Path(path), contract, args) for path in exported_s8p]

    checks = [
        _check(f"{name} variant dir exists", variant_dir.is_dir(), str(variant_dir)),
        _check(f"{name} payload exists", payload_path.is_file(), str(payload_path)),
        _check(f"{name} build script exists", build_script.is_file(), str(build_script)),
        _check(f"{name} solve script exists", solve_script.is_file(), str(solve_script)),
        _check(f"{name} packet summary exists", packet_summary.is_file(), str(packet_summary)),
        _check(f"{name} Windows runner references payload", _windows_path(payload_path) in windows_text, _windows_path(payload_path)),
        _check(f"{name} Windows runner references build script", _windows_path(build_script) in windows_text, _windows_path(build_script)),
        _check(f"{name} Windows runner references solve script", _windows_path(solve_script) in windows_text, _windows_path(solve_script)),
        _check(f"{name} Windows runner references results dir", _windows_path(hfss_results_dir) in windows_text, _windows_path(hfss_results_dir)),
        _check(f"{name} Windows runner references export manifest", _windows_path(export_manifest) in windows_text, _windows_path(export_manifest)),
        _check(f"{name} postrun references packet summary", str(packet_summary) in postrun_text, str(packet_summary)),
        _check(f"{name} postrun references results dir", str(hfss_results_dir) in postrun_text, str(hfss_results_dir)),
        _float_equals(f"{name} payload start GHz", grid.get("start_ghz"), contract["compare_start_ghz"], 1e-9),
        _float_equals(f"{name} payload stop GHz", grid.get("stop_ghz"), contract["compare_stop_ghz"], 1e-9),
        _float_equals(f"{name} payload step GHz", grid.get("step_ghz"), contract["expected_frequency_step_ghz"], 1e-9),
        _int_equals(f"{name} payload point count", grid.get("points"), contract["expected_frequency_points"]),
        _check(f"{name} payload not old narrow V65 grid", not (float(grid.get("start_ghz") or 0) == 15.0 and float(grid.get("stop_ghz") or 0) == 15.5), str(grid)),
        _check(f"{name} payload EMX S8P exists", emx_s8p.is_file() and emx_s8p.suffix.lower() == ".s8p", str(emx_s8p)),
        _check(f"{name} EMX S8P contract pass", emx_audit.get("status") == "PASS", json.dumps(emx_audit, sort_keys=True)),
        _check(f"{name} packet sample uses variant payload", _packet_sample_value(packet, "payload_json") == str(payload_path), _packet_sample_value(packet, "payload_json")),
        _check(f"{name} packet sample uses variant build script", _packet_sample_value(packet, "build_script") == str(build_script), _packet_sample_value(packet, "build_script")),
        _check(f"{name} packet sample uses variant solve script", _packet_sample_value(packet, "solve_script") == str(solve_script), _packet_sample_value(packet, "solve_script")),
    ]
    for audit in exported_audits:
        checks.append(_check(f"{name} exported S8P contract {Path(audit['path']).name}", audit.get("status") == "PASS", json.dumps(audit, sort_keys=True)))

    return {
        "name": name,
        "variant_dir": str(variant_dir),
        "payload_json": str(payload_path),
        "build_script": str(build_script),
        "solve_script": str(solve_script),
        "packet_summary": str(packet_summary),
        "hfss_results_dir": str(hfss_results_dir),
        "hfss_export_manifest": str(export_manifest),
        "export_manifest_exists": export_manifest.is_file(),
        "postrun_out_dir": str(postrun_out_dir),
        "emx_s8p": str(emx_s8p),
        "emx_s8p_audit": emx_audit,
        "payload_frequency_grid": grid,
        "exported_s8p": exported_s8p,
        "exported_s8p_audits": exported_audits,
        "_checks": checks,
    }


def _inspect_s8p(path: Path, contract: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "suffix": path.suffix.lower(),
        "port_count": None,
        "frequency_point_count": 0,
        "start_ghz": None,
        "stop_ghz": None,
        "step_ghz": None,
        "status": "FAIL",
        "reasons": [],
    }
    if not path.is_file():
        result["reasons"].append("file_not_found")
        return result
    match = re.search(r"\.s(\d+)p$", path.name.lower())
    result["port_count"] = int(match.group(1)) if match else None
    if result["suffix"] != ".s8p":
        result["reasons"].append("suffix_not_s8p")
    if result["port_count"] != int(contract["expected_ports"]):
        result["reasons"].append("port_count_not_expected")
    freqs = _touchstone_freqs(path, int(contract["expected_ports"]))
    result["frequency_point_count"] = len(freqs)
    if freqs:
        result["start_ghz"] = freqs[0] / 1.0e9
        result["stop_ghz"] = freqs[-1] / 1.0e9
        result["step_ghz"] = None if len(freqs) < 2 else (freqs[1] - freqs[0]) / 1.0e9
    expected_start = float(contract["compare_start_ghz"]) * 1.0e9
    expected_stop = float(contract["compare_stop_ghz"]) * 1.0e9
    expected_step = float(contract["expected_frequency_step_ghz"]) * 1.0e9
    tol = float(args.frequency_tolerance_hz)
    if len(freqs) != int(contract["expected_frequency_points"]):
        result["reasons"].append("frequency_point_count_mismatch")
    if not freqs or abs(freqs[0] - expected_start) > tol:
        result["reasons"].append("frequency_start_mismatch")
    if not freqs or abs(freqs[-1] - expected_stop) > tol:
        result["reasons"].append("frequency_stop_mismatch")
    if len(freqs) >= 2 and any(abs((freqs[i + 1] - freqs[i]) - expected_step) > tol for i in range(len(freqs) - 1)):
        result["reasons"].append("frequency_step_mismatch")
    result["status"] = "PASS" if not result["reasons"] else "FAIL"
    return result


def _touchstone_freqs(path: Path, port_count: int) -> list[float]:
    scale = 1.0e9
    values: list[float] = []
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            tokens = line[1:].strip().lower().split()
            if tokens:
                scale = {"hz": 1.0, "khz": 1.0e3, "mhz": 1.0e6, "ghz": 1.0e9}.get(tokens[0], scale)
            continue
        if line.startswith("["):
            continue
        for token in line.replace("D", "E").replace("d", "e").split():
            try:
                values.append(float(token))
            except ValueError:
                pass
    block = 1 + 2 * port_count * port_count
    return [values[idx] * scale for idx in range(0, len(values) - block + 1, block)]


def _packet_sample_value(packet: dict[str, Any], key: str) -> str:
    samples = packet.get("sample_results") if isinstance(packet.get("sample_results"), list) else []
    if not samples or not isinstance(samples[0], dict):
        return ""
    return str(samples[0].get(key) or "")


def _result_status(variants: list[dict[str, Any]]) -> str:
    exported_count = sum(len(item.get("exported_s8p") or []) for item in variants)
    if exported_count == 0:
        return "WAITING_FOR_HFSS_EXPORT"
    failing = [
        audit
        for item in variants
        for audit in item.get("exported_s8p_audits", [])
        if audit.get("status") != "PASS"
    ]
    if failing:
        return "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES"
    if exported_count < len(variants):
        return "PARTIAL_HFSS_EXPORTS_FOUND"
    return "HFSS_EXPORTS_FOUND_RUN_POSTRUN"


def _decision(overall_status: str, result_status: str) -> str:
    if overall_status == "FAIL":
        return "FIX_V66_EXECUTION_PACKET_BEFORE_HFSS_RUN"
    if result_status == "WAITING_FOR_HFSS_EXPORT":
        return "HANDOFF_READY_WAITING_FOR_HFSS_EXPORT"
    if result_status == "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES":
        return "FIX_HFSS_EXPORTED_S8P_SPEC_BEFORE_POSTRUN"
    return "RUN_V66_POSTRUN_VALIDATION"


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _check(name: str, condition: bool, detail: Any) -> Check:
    return Check("PASS" if condition else "FAIL", name, _detail(detail))


def _float_equals(name: str, actual: Any, expected: float, tolerance: float) -> Check:
    value = _to_float(actual)
    ok = value is not None and math.isfinite(value) and abs(value - float(expected)) <= float(tolerance)
    return _check(name, ok, f"actual={actual!r}, expected={expected:g}, tolerance={tolerance:g}")


def _int_equals(name: str, actual: Any, expected: int) -> Check:
    try:
        value = int(actual)
    except (TypeError, ValueError):
        value = None
    return _check(name, value == int(expected), f"actual={actual!r}, expected={expected}")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _detail(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _windows_path(path: Path) -> str:
    text = str(path)
    if text.startswith("/home/researcher/"):
        return "\\\\Mac\\Home\\" + text[len("/home/researcher/") :].replace("/", "\\")
    return text.replace("/", "\\")


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.as_dict())


def _write_variants_csv(path: Path, variants: list[dict[str, Any]]) -> None:
    fieldnames = [
        "name",
        "payload_json",
        "emx_s8p",
        "emx_s8p_status",
        "exported_s8p_count",
        "export_manifest_exists",
        "hfss_results_dir",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for variant in variants:
            writer.writerow(
                {
                    "name": variant.get("name", ""),
                    "payload_json": variant.get("payload_json", ""),
                    "emx_s8p": variant.get("emx_s8p", ""),
                    "emx_s8p_status": (variant.get("emx_s8p_audit") or {}).get("status", ""),
                    "exported_s8p_count": len(variant.get("exported_s8p") or []),
                    "export_manifest_exists": variant.get("export_manifest_exists", False),
                    "hfss_results_dir": variant.get("hfss_results_dir", ""),
                }
            )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS V66 Execution Packet Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- HFSS result status: `{summary['hfss_result_status']}`",
        f"- Variant count: `{summary['variant_count']}`",
        f"- Exported S8P count: `{summary['exported_s8p_count']}`",
        f"- Export manifest count: `{summary['export_manifest_count']}`",
        "",
        "## Contract",
        "",
        f"- Suffix: `{summary['expected_contract']['hfss_touchstone_suffix']}`",
        f"- Ports: `{summary['expected_contract']['expected_ports']}`",
        f"- Frequency: `{summary['expected_contract']['compare_start_ghz']:g}-{summary['expected_contract']['compare_stop_ghz']:g} GHz`",
        f"- Step: `{summary['expected_contract']['expected_frequency_step_ghz']:g} GHz`",
        f"- Points: `{summary['expected_contract']['expected_frequency_points']}`",
        "",
        "## Interpretation",
        "",
    ]
    if summary["overall_status"] == "PASS" and summary["hfss_result_status"] == "WAITING_FOR_HFSS_EXPORT":
        lines.append("V66 execution packet is internally consistent and ready for Windows/HFSS export.")
    elif summary["overall_status"] == "PASS":
        lines.append("V66 execution packet is internally consistent; exported HFSS files have been detected and should be postrun-validated.")
    else:
        lines.append("V66 execution packet has blocking issues. Fix them before running HFSS.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
