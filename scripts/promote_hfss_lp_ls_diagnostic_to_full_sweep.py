#!/usr/bin/env python3
"""Promote a passing V65 HFSS diagnostic sweep to a full 5-60 GHz run plan.

This script does not run HFSS.  It reads the V65 two-point diagnostic postrun
summaries, selects the best diagnostic PASS variant, and prepares the exact
full-frequency HFSS/PyAEDT run and postrun-validation scripts for that variant.

If no diagnostic variant has passed yet, it records WAITING/FAIL evidence and
does not create a full-run command that could be mistaken for accepted data.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "hfss_lp_ls_reference_sweep_plan_summary.json"
FINAL_GRID = {
    "compare_start_ghz": 5.0,
    "compare_stop_ghz": 60.0,
    "expected_frequency_step_ghz": 0.5,
    "expected_frequency_points": 111,
    "target_ghz": 15.0,
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = Path(args.diagnostic_plan_summary).expanduser().resolve()
    plan = _read_json(plan_path)
    aedt_packet_path = Path(str(plan.get("aedt_packet_summary") or "")).expanduser().resolve()
    diagnostic_aedt = _read_json(aedt_packet_path)

    checks = [
        _check("diagnostic plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("diagnostic plan passed", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("diagnostic AEDT packet exists", aedt_packet_path.is_file(), str(aedt_packet_path)),
        _check("diagnostic AEDT packet is diagnostic", diagnostic_aedt.get("frequency_grid_purpose") == "diagnostic", str(diagnostic_aedt.get("frequency_grid_purpose"))),
    ]
    variants = [_variant_status(item) for item in plan.get("variants") or [] if isinstance(item, dict)]
    pass_variants = [item for item in variants if item["status"] == "PASS"]
    waiting_variants = [item for item in variants if item["status"] == "WAITING_FOR_HFSS"]
    fail_variants = [item for item in variants if item["status"] == "FAIL"]
    selected = _select_best_variant(pass_variants)

    full_packet: dict[str, Any] = {}
    full_packet_summary_path: Path | None = None
    builder_record: dict[str, Any] | None = None
    full_windows_script = out_dir / "run_hfss_full_sweep_selected_variant.windows.ps1"
    full_postrun_script = out_dir / "postrun_validate_hfss_full_sweep_selected_variant.sh"

    if selected is not None:
        full_packet_summary_path, full_packet, builder_record = _ensure_full_aedt_packet(
            diagnostic_aedt=diagnostic_aedt,
            out_dir=out_dir,
            args=args,
        )
        checks.extend(_full_packet_checks(full_packet_summary_path, full_packet))
        if full_packet.get("overall_status") == "PASS":
            full_windows_script.write_text(_render_windows_script(selected, full_packet, args), encoding="utf-8")
            full_postrun_script.write_text(_render_postrun_script(selected, full_packet_summary_path, out_dir, args), encoding="utf-8")
            full_postrun_script.chmod(0o755)
        else:
            full_windows_script.write_text("Write-Error 'Full AEDT packet did not pass; refusing HFSS full sweep.'\nexit 2\n", encoding="utf-8")
            full_postrun_script.write_text("#!/usr/bin/env bash\necho 'Full AEDT packet did not pass.' >&2\nexit 2\n", encoding="utf-8")
            full_postrun_script.chmod(0o755)

    overall_status, decision = _overall_status_and_decision(
        checks=checks,
        selected=selected,
        waiting_count=len(waiting_variants),
        pass_count=len(pass_variants),
        fail_count=len(fail_variants),
        full_packet=full_packet,
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "diagnostic_plan_summary": str(plan_path),
        "diagnostic_aedt_packet_summary": str(aedt_packet_path),
        "out_dir": str(out_dir),
        "diagnostic_variant_counts": {
            "PASS": len(pass_variants),
            "WAITING_FOR_HFSS": len(waiting_variants),
            "FAIL": len(fail_variants),
            "OTHER": len(variants) - len(pass_variants) - len(waiting_variants) - len(fail_variants),
        },
        "selected_variant": selected or {},
        "variant_records": variants,
        "full_frequency_grid": {
            "start_ghz": FINAL_GRID["compare_start_ghz"],
            "stop_ghz": FINAL_GRID["compare_stop_ghz"],
            "step_ghz": FINAL_GRID["expected_frequency_step_ghz"],
            "points": FINAL_GRID["expected_frequency_points"],
            "target_ghz": FINAL_GRID["target_ghz"],
        },
        "full_aedt_packet_summary": "" if full_packet_summary_path is None else str(full_packet_summary_path),
        "full_windows_runner": "" if selected is None else str(full_windows_script),
        "full_postrun_validator": "" if selected is None else str(full_postrun_script),
        "builder_record": builder_record or {},
        "checks": checks,
        "limitations": [
            "This script does not run HFSS and does not prove EMX/HFSS agreement.",
            "A two-point diagnostic PASS only chooses a candidate HFSS reference/environment variant.",
            "Final acceptance still requires the generated full 5-60 GHz `.s8p` to pass Lp/Ls/Qp/Qs/Kw <= 10% against EMX.",
        ],
    }
    summary_path = out_dir / "hfss_lp_ls_full_sweep_promotion_summary.json"
    report_path = out_dir / "HFSS_LP_LS_FULL_SWEEP_PROMOTION_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if selected is not None:
        print(f"windows_script={full_windows_script}")
        print(f"postrun_script={full_postrun_script}")
    return 2 if overall_status in {"FAIL", "WAITING_FOR_DIAGNOSTIC_HFSS"} and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "outputs" / "hfss_lp_ls_full_sweep_promotion_current"))
    parser.add_argument("--builder-script", default=str(REPO_ROOT / "scripts" / "build_s8p_hfss_aedt_scripts_from_handoff.py"))
    parser.add_argument("--full-aedt-packet-summary", help="Existing production-grid AEDT packet summary; skips builder execution")
    parser.add_argument("--python-command", default=sys.executable)
    parser.add_argument("--windows-python-command", default="python")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _variant_status(variant: dict[str, Any]) -> dict[str, Any]:
    postrun_out = Path(str(variant.get("postrun_out_dir") or "")).expanduser()
    summary_path = postrun_out / "s8p_hfss_postrun_validation_summary.json"
    summary = _read_json(summary_path)
    records = summary.get("records") if isinstance(summary.get("records"), list) else []
    worst_errors = [
        float(record["worst_percent_error"])
        for record in records
        if isinstance(record, dict) and isinstance(record.get("worst_percent_error"), (int, float))
    ]
    record_statuses = [str(record.get("status")) for record in records if isinstance(record, dict)]
    status = str(summary.get("overall_status") or "MISSING")
    diagnostic_pass = (
        status == "PASS"
        and summary.get("decision") == "ACCEPT_DIAGNOSTIC_S8P_EMX_HFSS_SCREENING_ONLY_NOT_FINAL"
        and summary.get("frequency_grid_mode") == "diagnostic_screening_only"
        and summary.get("final_acceptance_candidate") is False
    )
    if diagnostic_pass:
        normalized = "PASS"
    elif status == "WAITING_FOR_HFSS":
        normalized = "WAITING_FOR_HFSS"
    elif status in {"FAIL", "NOT_READY", "PARTIAL"}:
        normalized = "FAIL"
    else:
        normalized = status
    return {
        "name": str(variant.get("name") or ""),
        "status": normalized,
        "raw_status": status,
        "decision": str(summary.get("decision") or ""),
        "frequency_grid_mode": str(summary.get("frequency_grid_mode") or ""),
        "final_acceptance_candidate": bool(summary.get("final_acceptance_candidate")),
        "postrun_summary": str(summary_path),
        "postrun_out_dir": str(postrun_out),
        "worst_percent_error": None if not worst_errors else max(worst_errors),
        "record_statuses": record_statuses,
        "env": variant.get("env") if isinstance(variant.get("env"), dict) else {},
    }


def _select_best_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not variants:
        return None
    return sorted(
        variants,
        key=lambda item: (
            float("inf") if item.get("worst_percent_error") is None else float(item["worst_percent_error"]),
            str(item.get("name", "")),
        ),
    )[0]


def _ensure_full_aedt_packet(
    *,
    diagnostic_aedt: dict[str, Any],
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    if args.full_aedt_packet_summary:
        summary_path = Path(args.full_aedt_packet_summary).expanduser().resolve()
        return summary_path, _read_json(summary_path), None

    handoff_summary = Path(str(diagnostic_aedt.get("handoff_summary") or "")).expanduser().resolve()
    proc_file = Path(str(diagnostic_aedt.get("proc_file") or "")).expanduser().resolve()
    full_packet_dir = out_dir / "full_5_60_aedt_packet"
    builder_script = Path(args.builder_script).expanduser().resolve()
    command = [
        str(args.python_command),
        str(builder_script),
        "--handoff-summary",
        str(handoff_summary),
        "--out-dir",
        str(full_packet_dir),
        "--frequency-start-ghz",
        str(FINAL_GRID["compare_start_ghz"]),
        "--frequency-stop-ghz",
        str(FINAL_GRID["compare_stop_ghz"]),
        "--frequency-step-ghz",
        str(FINAL_GRID["expected_frequency_step_ghz"]),
        "--expected-frequency-points",
        str(FINAL_GRID["expected_frequency_points"]),
    ]
    if proc_file.is_file():
        command.extend(["--proc-file", str(proc_file)])
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    summary_path = full_packet_dir / "hfss_s8p_aedt_script_packet_summary.json"
    record = {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
    }
    return summary_path, _read_json(summary_path), record


def _full_packet_checks(summary_path: Path, packet: dict[str, Any]) -> list[dict[str, str]]:
    grid = packet.get("frequency_grid") if isinstance(packet.get("frequency_grid"), dict) else {}
    return [
        _check("full AEDT packet summary exists", summary_path.is_file(), str(summary_path)),
        _check("full AEDT packet passed", packet.get("overall_status") == "PASS", str(packet.get("overall_status"))),
        _check("full AEDT packet is production grid", packet.get("frequency_grid_purpose") == "production", str(packet.get("frequency_grid_purpose"))),
        _check("full AEDT grid starts at 5 GHz", _float_eq(grid.get("start_ghz"), 5.0), str(grid.get("start_ghz"))),
        _check("full AEDT grid stops at 60 GHz", _float_eq(grid.get("stop_ghz"), 60.0), str(grid.get("stop_ghz"))),
        _check("full AEDT grid step is 1.0 GHz", _float_eq(grid.get("step_ghz"), 0.5), str(grid.get("step_ghz"))),
        _check("full AEDT grid has 56 points", int(grid.get("points") or -1) == 111, str(grid.get("points"))),
    ]


def _overall_status_and_decision(
    *,
    checks: list[dict[str, str]],
    selected: dict[str, Any] | None,
    waiting_count: int,
    pass_count: int,
    fail_count: int,
    full_packet: dict[str, Any],
) -> tuple[str, str]:
    if any(check["status"] == "FAIL" for check in checks[:4]):
        return "FAIL", "FIX_DIAGNOSTIC_PROMOTION_INPUTS"
    if selected is None:
        if waiting_count:
            return "WAITING_FOR_DIAGNOSTIC_HFSS", "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P"
        return "FAIL", "NO_DIAGNOSTIC_VARIANT_PASSED_DO_NOT_RUN_FULL_SWEEP"
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL", "FULL_SWEEP_PACKET_NOT_READY"
    if pass_count >= 1 and full_packet.get("overall_status") == "PASS":
        return "PASS", "READY_TO_RUN_SELECTED_VARIANT_FULL_5_60_HFSS_SWEEP"
    if fail_count and not waiting_count:
        return "FAIL", "DIAGNOSTIC_VARIANTS_FAILED"
    return "WAITING_FOR_DIAGNOSTIC_HFSS", "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P"


def _render_windows_script(selected: dict[str, Any], packet: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "# Auto-generated full 5-60 GHz HFSS sweep for the selected diagnostic variant.",
        "$ErrorActionPreference = 'Stop'",
        f"Write-Host '== selected variant: {selected['name']} ==' ",
    ]
    for key, value in (selected.get("env") or {}).items():
        lines.append(f"$env:{key} = {_ps_quote(value)}")
    sample_results = [sample for sample in packet.get("sample_results") or [] if isinstance(sample, dict) and sample.get("overall_status") == "PASS"]
    for sample in sample_results:
        evaluation = str(sample.get("evaluation") or "sample")
        script_dir = Path(str(sample.get("script_dir") or "")).expanduser()
        build_script = Path(str(sample.get("build_script") or script_dir / "build_hfss_s8p_from_payload.py")).expanduser()
        solve_script = Path(str(sample.get("solve_script") or script_dir / "solve_export_hfss_s8p.py")).expanduser()
        variant_dir = Path(packet.get("out_dir") or script_dir).expanduser().resolve() / "full_selected_variant" / str(selected["name"]) / evaluation
        lines.extend(
            [
                f"New-Item -ItemType Directory -Force -Path {_ps_quote(_windows_path(variant_dir))} | Out-Null",
                f"$env:HFSS_SAVE_PATH = {_ps_quote(_windows_path(variant_dir / (evaluation + '_' + selected['name'] + '_full.aedt')))}",
                f"$env:HFSS_SOLVE_PROJECT = {_ps_quote(_windows_path(variant_dir / (evaluation + '_' + selected['name'] + '_full_solve.aedt')))}",
                f"$env:HFSS_SOLVE_RESULTS_DIR = {_ps_quote(_windows_path(variant_dir / 'hfss_solve_export_results'))}",
                f"$env:HFSS_BUILD_LOG = {_ps_quote(_windows_path(variant_dir / 'hfss_s8p_build.log'))}",
                f"$env:HFSS_PORT_MANIFEST = {_ps_quote(_windows_path(variant_dir / 'hfss_s8p_build_port_manifest.json'))}",
                f"$env:HFSS_EXPORT_MANIFEST = {_ps_quote(_windows_path(variant_dir / 'hfss_s8p_export_manifest.json'))}",
                f"& {_ps_quote(args.windows_python_command)} {_ps_quote(_windows_path(build_script))}",
                f"& {_ps_quote(args.windows_python_command)} {_ps_quote(_windows_path(solve_script))}",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_postrun_script(selected: dict[str, Any], full_packet_summary: Path, out_dir: Path, args: argparse.Namespace) -> str:
    variant_results_dir = full_packet_summary.parent / "full_selected_variant" / str(selected["name"])
    postrun_dir = out_dir / "full_selected_variant_postrun_validation"
    command = [
        str(args.python_command),
        "scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py",
        "--aedt-packet-summary",
        str(full_packet_summary),
        "--hfss-results-dir",
        str(variant_results_dir),
        "--out-dir",
        str(postrun_dir),
        "--compare-start-ghz",
        str(FINAL_GRID["compare_start_ghz"]),
        "--compare-stop-ghz",
        str(FINAL_GRID["compare_stop_ghz"]),
        "--expected-frequency-step-ghz",
        str(FINAL_GRID["expected_frequency_step_ghz"]),
        "--expected-frequency-points",
        str(FINAL_GRID["expected_frequency_points"]),
        "--target-ghz",
        f"{float(args.target_ghz):g}",
        "--target-frequency-tolerance-ghz",
        "0.05",
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--ground-unused-ports",
        "--no-fail-exit",
    ]
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "cd " + shlex.quote(str(REPO_ROOT)),
            _shell_join(command),
            "",
        ]
    )


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS Lp/Ls Diagnostic-To-Full Promotion",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Diagnostic plan: `{summary['diagnostic_plan_summary']}`",
        f"- Selected variant: `{(summary.get('selected_variant') or {}).get('name', '')}`",
        f"- Full AEDT packet: `{summary.get('full_aedt_packet_summary', '')}`",
        f"- Full Windows runner: `{summary.get('full_windows_runner', '')}`",
        f"- Full postrun validator: `{summary.get('full_postrun_validator', '')}`",
        "",
        "## Diagnostic Variants",
        "",
        "| Variant | Status | Worst % | Decision |",
        "|---|---|---:|---|",
    ]
    for item in summary.get("variant_records") or []:
        worst = "" if item.get("worst_percent_error") is None else f"{float(item['worst_percent_error']):.4g}"
        lines.append(f"| `{item.get('name', '')}` | {item.get('status', '')} | {worst} | `{item.get('decision', '')}` |")
    lines.extend(["", "## Checks", "", "| Status | Check | Detail |", "|---|---|---|"])
    for check in summary.get("checks") or []:
        lines.append(f"| {check['status']} | {check['name']} | `{check['detail']}` |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary.get("limitations") or [])
    lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _float_eq(value: Any, expected: float, *, tol: float = 1.0e-12) -> bool:
    try:
        return abs(float(value) - float(expected)) <= tol
    except (TypeError, ValueError):
        return False


def _windows_path(path: Any) -> str:
    text = str(path)
    prefix = "/home/researcher/"
    if text.startswith(prefix):
        return "\\\\Mac\\Home\\" + text[len(prefix):].replace("/", "\\")
    return text.replace("/", "\\")


def _ps_quote(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in command)


if __name__ == "__main__":
    raise SystemExit(main())
