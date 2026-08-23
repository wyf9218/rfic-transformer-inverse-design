#!/usr/bin/env python3
"""Audit the V65 HFSS execution handoff before/after Windows runs.

The audit checks that the generated Windows HFSS runner, postrun validator, and
watcher agree on paths and frequency scope.  It does not run HFSS and does not
claim EMX/HFSS validation.  Missing exported `.s8p` files are reported as
WAITING when the handoff itself is consistent.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402

DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "hfss_lp_ls_reference_sweep_plan_summary.json"
DEFAULT_WINDOWS = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "run_hfss_lp_ls_reference_sweep.windows.ps1"
DEFAULT_POSTRUN = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "postrun_validate_hfss_lp_ls_reference_sweep.sh"
DEFAULT_WATCH = PROJECT_ROOT / "outputs" / "hfss_v65_diagnostic_to_million_gate_watch_current" / "hfss_v65_diagnostic_to_million_gate_watch_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v65_execution_handoff_audit_current"


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

    grid = plan.get("diagnostic_frequency_grid") if isinstance(plan.get("diagnostic_frequency_grid"), dict) else {}
    variant_records = [
        _variant_record(item, windows_text, postrun_text, grid, args)
        for item in plan.get("variants") or []
        if isinstance(item, dict)
    ]
    checks = _global_checks(plan_path, plan, windows_path, windows_text, postrun_path, postrun_text, watch_path, watch, args)
    checks.extend(check for record in variant_records for check in record.pop("_checks"))
    result_status = _result_status(variant_records)
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
        "variant_count": len(variant_records),
        "exported_s8p_count": sum(len(record["exported_s8p"]) for record in variant_records),
        "exported_s8p_audit_count": sum(len(record["exported_s8p_audits"]) for record in variant_records),
        "export_manifest_count": sum(1 for record in variant_records if record["export_manifest_exists"]),
        "variants": variant_records,
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "PASS means the HFSS execution handoff is internally consistent, not that EMX/HFSS data agree.",
            "WAITING_FOR_HFSS_EXPORT is expected before Windows/HFSS writes the diagnostic `.s8p` files.",
            "Final dataset generation remains gated by full 5-60 GHz EMX/HFSS postrun validation.",
        ],
    }
    summary_path = out_dir / "hfss_v65_execution_handoff_audit_summary.json"
    report_path = out_dir / "HFSS_V65_EXECUTION_HANDOFF_AUDIT_CN.md"
    checks_csv = out_dir / "hfss_v65_execution_handoff_audit_checks.csv"
    variants_csv = out_dir / "hfss_v65_execution_handoff_audit_variants.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_checks_csv(checks_csv, checks)
    _write_variants_csv(variants_csv, variant_records)

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
    parser.add_argument("--expected-variant-count", type=int, default=10)
    parser.add_argument("--expected-diagnostic-points", type=int, default=2)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _global_checks(
    plan_path: Path,
    plan: dict[str, Any],
    windows_path: Path,
    windows_text: str,
    postrun_path: Path,
    postrun_text: str,
    watch_path: Path | None,
    watch: dict[str, Any],
    args: argparse.Namespace,
) -> list[Check]:
    grid = plan.get("diagnostic_frequency_grid") if isinstance(plan.get("diagnostic_frequency_grid"), dict) else {}
    checks = [
        _check("plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("plan summary passed", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("plan decision ready", plan.get("decision") == "READY_TO_RUN_HFSS_LP_LS_DIAGNOSTIC_SWEEP", str(plan.get("decision"))),
        _check("variant count is expected", len(plan.get("variants") or []) == int(args.expected_variant_count), f"variants={len(plan.get('variants') or [])}"),
        _check("diagnostic grid has expected point count", int(grid.get("points") or -1) == int(args.expected_diagnostic_points), str(grid)),
        _check("Windows runner exists", windows_path.is_file(), str(windows_path)),
        _check("Windows runner contains HFSS solve export", "solve_export_hfss_s8p.py" in windows_text, "solve_export_hfss_s8p.py"),
        _check("postrun script exists", postrun_path.is_file(), str(postrun_path)),
        _check("postrun script is executable", bool(postrun_path.exists() and os.access(postrun_path, os.X_OK)), oct(postrun_path.stat().st_mode & 0o777) if postrun_path.exists() else "missing"),
        _check("postrun uses diagnostic point count", f"--expected-frequency-points {int(args.expected_diagnostic_points)}" in postrun_text, f"expected={args.expected_diagnostic_points}"),
    ]
    if watch_path is not None:
        checks.extend(
            [
                _check("watch summary exists", watch_path.is_file(), str(watch_path)),
                _check(
                    "watch summary status is recognized",
                    str(watch.get("overall_status") or "") in {
                        "WAITING_FOR_DIAGNOSTIC_HFSS",
                        "WAITING_FOR_FULL_HFSS",
                        "WAITING_FOR_CAMPAIGN_PLANNER",
                        "PASS",
                        "FAIL",
                    },
                    str(watch.get("overall_status")),
                ),
            ]
        )
    return checks


def _variant_record(
    variant: dict[str, Any],
    windows_text: str,
    postrun_text: str,
    grid: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    name = str(variant.get("name") or "")
    variant_dir = Path(str(variant.get("variant_dir") or "")).expanduser()
    postrun_out_dir = Path(str(variant.get("postrun_out_dir") or "")).expanduser()
    steps = variant.get("windows_steps") if isinstance(variant.get("windows_steps"), list) else []
    exported_s8p = sorted(str(path) for path in variant_dir.rglob("*.s8p")) if variant_dir.exists() else []
    export_manifest_paths = [
        Path(str(step.get("hfss_export_manifest") or "")).expanduser()
        for step in steps
        if isinstance(step, dict)
    ]
    build_scripts = [
        Path(str(step.get("build_script") or "")).expanduser()
        for step in steps
        if isinstance(step, dict)
    ]
    solve_scripts = [
        Path(str(step.get("solve_script") or "")).expanduser()
        for step in steps
        if isinstance(step, dict)
    ]
    hfss_results_dirs = [
        Path(str(step.get("hfss_results_dir") or "")).expanduser()
        for step in steps
        if isinstance(step, dict)
    ]
    s8p_audits: list[dict[str, Any]] = []
    s8p_checks: list[Check] = []
    for path_text in exported_s8p:
        audit, audit_checks = _audit_exported_s8p(Path(path_text), name, grid, args)
        s8p_audits.append(audit)
        s8p_checks.extend(audit_checks)

    checks = [
        _check(f"{name} has one Windows step", len(steps) == 1, f"steps={len(steps)}"),
        _check(f"{name} variant dir path is present", bool(str(variant_dir)), str(variant_dir)),
        _check(f"{name} postrun dir path is present", bool(str(postrun_out_dir)), str(postrun_out_dir)),
        _check(f"{name} Windows runner names variant", name in windows_text, name),
        _check(f"{name} postrun scans variant dir", str(variant_dir) in postrun_text, str(variant_dir)),
    ]
    for index, path in enumerate(build_scripts, start=1):
        checks.append(_check(f"{name} build script {index} exists", path.is_file(), str(path)))
    for index, path in enumerate(solve_scripts, start=1):
        checks.append(_check(f"{name} solve script {index} exists", path.is_file(), str(path)))
    for index, path in enumerate(hfss_results_dirs, start=1):
        checks.append(_check(f"{name} HFSS results dir nested under variant", _is_relative_to(path, variant_dir), f"results={path}; variant={variant_dir}"))
        checks.append(_check(f"{name} Windows runner sets HFSS results dir {index}", _windows_path(path) in windows_text, _windows_path(path)))
    for index, path in enumerate(export_manifest_paths, start=1):
        checks.append(_check(f"{name} Windows runner sets export manifest {index}", _windows_path(path) in windows_text, _windows_path(path)))
    checks.extend(s8p_checks)
    return {
        "name": name,
        "variant_dir": str(variant_dir),
        "postrun_out_dir": str(postrun_out_dir),
        "windows_step_count": len(steps),
        "hfss_results_dirs": [str(path) for path in hfss_results_dirs],
        "export_manifests": [str(path) for path in export_manifest_paths],
        "export_manifest_exists": any(path.is_file() for path in export_manifest_paths),
        "exported_s8p": exported_s8p,
        "exported_s8p_count": len(exported_s8p),
        "exported_s8p_audits": s8p_audits,
        "_checks": checks,
    }


def _result_status(records: list[dict[str, Any]]) -> str:
    count = sum(len(record.get("exported_s8p") or []) for record in records)
    if count == 0:
        return "WAITING_FOR_HFSS_EXPORT"
    invalid = [
        audit
        for record in records
        for audit in record.get("exported_s8p_audits", [])
        if audit.get("status") != "PASS"
    ]
    if invalid:
        return "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES"
    if count < len(records):
        return "PARTIAL_HFSS_EXPORTS_FOUND"
    return "HFSS_EXPORTS_FOUND_RUN_POSTRUN"


def _decision(overall_status: str, result_status: str) -> str:
    if result_status == "HFSS_EXPORTS_FOUND_WITH_SPEC_FAILURES":
        return "FIX_HFSS_EXPORTED_S8P_SPEC_BEFORE_POSTRUN"
    if overall_status == "FAIL":
        return "FIX_HFSS_V65_EXECUTION_HANDOFF"
    if result_status == "WAITING_FOR_HFSS_EXPORT":
        return "HANDOFF_READY_WAITING_FOR_HFSS_EXPORT"
    if result_status == "PARTIAL_HFSS_EXPORTS_FOUND":
        return "RUN_OR_COMPLETE_V65_HFSS_POSTRUN"
    return "RUN_V65_HFSS_POSTRUN_AND_PROMOTION_WATCHER"


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS V65 Execution Handoff Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- HFSS result status: `{summary['hfss_result_status']}`",
        f"- Variants: `{summary['variant_count']}`",
        f"- Exported S8P count: `{summary['exported_s8p_count']}`",
        f"- Exported S8P audits: `{summary['exported_s8p_audit_count']}`",
        "",
        "## Variants",
        "",
        "| Variant | S8P count | S8P spec | Export manifest | Results dir |",
        "|---|---:|---|---|---|",
    ]
    for record in summary["variants"]:
        result_dirs = "; ".join(record.get("hfss_results_dirs") or [])
        spec_status = _joined_s8p_status(record.get("exported_s8p_audits") or [])
        lines.append(f"| `{record['name']}` | {record['exported_s8p_count']} | `{spec_status}` | {record['export_manifest_exists']} | `{result_dirs}` |")
    lines.extend(["", "## Checks", "", "| Status | Check | Detail |", "|---|---|---|"])
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | `{check['detail']}` |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _write_checks_csv(path: Path, checks: list[Check]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "name", "detail"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.as_dict())


def _write_variants_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "name",
        "variant_dir",
        "postrun_out_dir",
        "windows_step_count",
        "export_manifest_exists",
        "exported_s8p_count",
        "exported_s8p_spec_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {field: record.get(field, "") for field in fields}
            row["exported_s8p_spec_status"] = _joined_s8p_status(record.get("exported_s8p_audits") or [])
            writer.writerow(row)


def _audit_exported_s8p(path: Path, variant_name: str, grid: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], list[Check]]:
    audit: dict[str, Any] = {
        "path": str(path),
        "status": "FAIL",
        "num_ports": None,
        "frequency_points": None,
        "start_ghz": None,
        "stop_ghz": None,
        "step_ghz": None,
        "parse_error": "",
    }
    checks = [_check(f"{variant_name} exported Touchstone suffix is .s8p", path.suffix.lower() == ".s8p", str(path))]
    try:
        result = load_touchstone(path)
    except Exception as exc:  # noqa: BLE001 - report parser failures as audit evidence.
        audit["parse_error"] = str(exc)
        checks.append(_check(f"{variant_name} exported S8P parses as Touchstone", False, exc))
        return audit, checks

    freqs = result.freqs_hz
    audit.update(
        {
            "num_ports": int(result.num_ports),
            "frequency_points": int(result.num_freqs),
            "start_ghz": float(freqs[0]) / 1.0e9 if len(freqs) else None,
            "stop_ghz": float(freqs[-1]) / 1.0e9 if len(freqs) else None,
            "step_ghz": _nominal_step_ghz(freqs),
        }
    )
    expected_points = int(grid.get("points") or args.expected_diagnostic_points)
    expected_start_hz = _optional_ghz_to_hz(grid.get("start_ghz"))
    expected_stop_hz = _optional_ghz_to_hz(grid.get("stop_ghz"))
    expected_step_hz = _optional_ghz_to_hz(grid.get("step_ghz"))
    checks.extend(
        [
            _check(f"{variant_name} exported S8P parses as Touchstone", True, str(path)),
            _check(f"{variant_name} exported S8P has {int(args.expected_ports)} ports", int(result.num_ports) == int(args.expected_ports), f"ports={result.num_ports}"),
            _check(f"{variant_name} exported S8P has expected frequency point count", int(result.num_freqs) == expected_points, f"expected={expected_points}; actual={result.num_freqs}"),
        ]
    )
    if expected_start_hz is not None and len(freqs):
        checks.append(
            _check(
                f"{variant_name} exported S8P starts at planned frequency",
                abs(float(freqs[0]) - expected_start_hz) <= float(args.frequency_tolerance_hz),
                f"expected={expected_start_hz / 1e9:g}GHz; actual={float(freqs[0]) / 1e9:g}GHz",
            )
        )
    if expected_stop_hz is not None and len(freqs):
        checks.append(
            _check(
                f"{variant_name} exported S8P stops at planned frequency",
                abs(float(freqs[-1]) - expected_stop_hz) <= float(args.frequency_tolerance_hz),
                f"expected={expected_stop_hz / 1e9:g}GHz; actual={float(freqs[-1]) / 1e9:g}GHz",
            )
        )
    if expected_step_hz is not None and len(freqs) > 1:
        diffs = [float(value) for value in (freqs[1:] - freqs[:-1])]
        max_error = max(abs(value - expected_step_hz) for value in diffs)
        checks.append(
            _check(
                f"{variant_name} exported S8P uses planned frequency step",
                max_error <= float(args.frequency_tolerance_hz),
                f"expected={expected_step_hz / 1e9:g}GHz; max_error_hz={max_error:g}",
            )
        )
    audit["status"] = "PASS" if all(check.status == "PASS" for check in checks) else "FAIL"
    return audit, checks


def _optional_ghz_to_hz(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * 1.0e9
    except (TypeError, ValueError):
        return None


def _nominal_step_ghz(freqs: Any) -> float | None:
    if len(freqs) < 2:
        return None
    return float(freqs[1] - freqs[0]) / 1.0e9


def _joined_s8p_status(audits: list[dict[str, Any]]) -> str:
    if not audits:
        return "WAITING"
    statuses = [str(audit.get("status") or "UNKNOWN") for audit in audits]
    return ",".join(statuses)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _check(name: str, passed: bool, detail: Any) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _windows_path(path: Path) -> str:
    text = str(path)
    prefix = "/home/researcher/"
    if text.startswith(prefix):
        return "\\\\Mac\\Home\\" + text[len(prefix):].replace("/", "\\")
    return text.replace("/", "\\")


if __name__ == "__main__":
    raise SystemExit(main())
