#!/usr/bin/env python3
"""Monitor HFSS .s8p intake and start validation only when the gate is ready.

This is a thin safety wrapper around two existing tools:

* ``audit_hfss_s8p_global_intake.py`` finds visible HFSS .s8p files and
  classifies them as current-gate, historical/report, or user-drop evidence.
* ``monitor_s8p_validation_to_million_autopipeline.py`` performs the real
  EMX/HFSS validation-to-million gate.

The wrapper intentionally does not copy files, run HFSS, or bypass the 10%
physical-metric validation gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_INTAKE_SCRIPT = SCRIPT_DIR / "audit_hfss_s8p_global_intake.py"
DEFAULT_VALIDATION_MONITOR_SCRIPT = SCRIPT_DIR / "monitor_s8p_validation_to_million_autopipeline.py"
DEFAULT_INTAKE_OUT = PROJECT_ROOT / "outputs" / "hfss_s8p_global_intake_audit_current"
DEFAULT_VALIDATION_MONITOR_OUT = PROJECT_ROOT / "outputs" / "s8p_validation_to_million_autopipeline_monitor_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_s8p_intake_to_validation_monitor_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(args.timeout_seconds))
    attempts: list[dict[str, Any]] = []
    latest_intake_result: dict[str, Any] = {}
    latest_intake_summary: dict[str, Any] = {}
    validation_result: dict[str, Any] | None = None
    validation_summary: dict[str, Any] = {}

    while True:
        latest_intake_result = _run_intake(args)
        latest_intake_summary = _read_json(
            Path(args.intake_out_dir).expanduser().resolve() / "hfss_s8p_global_intake_audit_summary.json"
        )
        intake_status, intake_decision = _decision_from_intake(latest_intake_summary)
        attempts.append(_attempt_record(latest_intake_result, latest_intake_summary, intake_status, intake_decision))

        if intake_status == "CURRENT_GATE_READY":
            validation_result = _run_validation_monitor(args)
            validation_summary = _read_json(
                Path(args.validation_monitor_out_dir).expanduser().resolve()
                / "s8p_validation_to_million_autopipeline_monitor_summary.json"
            )
            break
        if intake_status in {"WAITING_FOR_STAGING", "FAIL"}:
            break
        if float(args.timeout_seconds) <= 0.0 or time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, float(args.poll_seconds)))

    overall_status, decision = _final_decision(latest_intake_summary, validation_summary)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "latest_intake_result": latest_intake_result,
        "latest_intake_summary": latest_intake_summary,
        "validation_result": validation_result,
        "latest_validation_monitor_summary": validation_summary,
        "recommended_next_action": _recommended_next_action(latest_intake_summary, validation_summary),
        "arguments": {
            "intake_script": str(Path(args.intake_script).expanduser()),
            "intake_out_dir": str(Path(args.intake_out_dir).expanduser()),
            "validation_monitor_script": str(Path(args.validation_monitor_script).expanduser()),
            "validation_monitor_out_dir": str(Path(args.validation_monitor_out_dir).expanduser()),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "validation_timeout_seconds": float(args.validation_timeout_seconds),
            "validation_poll_seconds": float(args.validation_poll_seconds),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "safety_notes": [
            "This monitor does not run HFSS and does not copy .s8p files.",
            "User-drop or historical/report .s8p files are intake evidence only; they do not unlock validation.",
            "Validation starts only when the intake audit finds a spec-pass current-gate HFSS .s8p.",
            "Million-sample generation remains locked until the downstream EMX/HFSS physical metric gate passes.",
        ],
    }
    summary_path = out_dir / "hfss_s8p_intake_to_validation_monitor_summary.json"
    report_path = out_dir / "HFSS_S8P_INTAKE_TO_VALIDATION_MONITOR_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"attempt_count={len(attempts)}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN", "WAITING_FOR_CURRENT_GATE_HFSS", "WAITING_FOR_STAGING", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake-script", default=str(DEFAULT_INTAKE_SCRIPT))
    parser.add_argument("--intake-out-dir", default=str(DEFAULT_INTAKE_OUT))
    parser.add_argument("--validation-monitor-script", default=str(DEFAULT_VALIDATION_MONITOR_SCRIPT))
    parser.add_argument("--validation-monitor-out-dir", default=str(DEFAULT_VALIDATION_MONITOR_OUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--search-root", action="append")
    parser.add_argument("--current-gate-root", action="append")
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--validation-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--validation-poll-seconds", type=float, default=300.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_intake(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.intake_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.intake_out_dir).expanduser().resolve()),
        "--no-fail-exit",
    ]
    for root in args.search_root or []:
        command.extend(["--search-root", str(Path(root).expanduser().resolve())])
    for root in args.current_gate_root or []:
        command.extend(["--current-gate-root", str(Path(root).expanduser().resolve())])
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
    }


def _run_validation_monitor(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.validation_monitor_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.validation_monitor_out_dir).expanduser().resolve()),
        "--timeout-seconds",
        str(float(args.validation_timeout_seconds)),
        "--poll-seconds",
        str(float(args.validation_poll_seconds)),
        "--no-fail-exit",
    ]
    if args.allow_real_emx:
        command.append("--allow-real-emx")
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
    }


def _decision_from_intake(summary: dict[str, Any]) -> tuple[str, str]:
    if not summary:
        return "FAIL", "INTAKE_SUMMARY_MISSING"
    if summary.get("_parse_error"):
        return "FAIL", "INTAKE_SUMMARY_PARSE_ERROR"
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    if int(counts.get("current_gate_spec_pass_count") or 0) > 0:
        return "CURRENT_GATE_READY", "CURRENT_GATE_HFSS_S8P_FOUND_RUN_VALIDATION_MONITOR"
    if int(counts.get("user_drop_spec_pass_count") or 0) > 0:
        return "WAITING_FOR_STAGING", "USER_DROP_S8P_FOUND_STAGE_TO_CURRENT_GATE_BEFORE_VALIDATION"
    decision = str(summary.get("decision") or "")
    if decision:
        return "WAITING_FOR_CURRENT_GATE_HFSS", decision
    return "WAITING_FOR_CURRENT_GATE_HFSS", "NO_CURRENT_GATE_HFSS_S8P"


def _final_decision(intake_summary: dict[str, Any], validation_summary: dict[str, Any]) -> tuple[str, str]:
    intake_status, intake_decision = _decision_from_intake(intake_summary)
    if intake_status == "FAIL":
        return "FAIL", intake_decision
    if intake_status == "WAITING_FOR_STAGING":
        return "WAITING_FOR_STAGING", intake_decision
    if intake_status != "CURRENT_GATE_READY":
        return "WAITING_FOR_CURRENT_GATE_HFSS", intake_decision
    if not validation_summary:
        return "FAIL", "CURRENT_GATE_READY_BUT_VALIDATION_MONITOR_SUMMARY_MISSING"
    if validation_summary.get("_parse_error"):
        return "FAIL", "CURRENT_GATE_READY_BUT_VALIDATION_MONITOR_SUMMARY_PARSE_ERROR"
    status = str(validation_summary.get("overall_status") or "")
    decision = str(validation_summary.get("decision") or "")
    if status == "PASS":
        return "PASS", "CURRENT_GATE_VALIDATION_MONITOR_COMPLETED"
    if status == "DRY_RUN":
        return "DRY_RUN", "CURRENT_GATE_VALIDATION_READY_DRY_RUN_COMPLETED"
    if status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_HFSS", "CURRENT_GATE_FOUND_BUT_DOWNSTREAM_VALIDATION_STILL_WAITING"
    if status == "FAIL":
        return "FAIL", decision or "CURRENT_GATE_VALIDATION_MONITOR_FAILED"
    return "FAIL", "VALIDATION_MONITOR_SUMMARY_UNKNOWN_STATUS"


def _attempt_record(
    result: dict[str, Any],
    summary: dict[str, Any],
    intake_status: str,
    intake_decision: str,
) -> dict[str, Any]:
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "returncode": result.get("returncode"),
        "intake_status": intake_status,
        "intake_decision": intake_decision,
        "counts": {
            "global_s8p_count": counts.get("global_s8p_count"),
            "current_gate_spec_pass_count": counts.get("current_gate_spec_pass_count"),
            "user_drop_spec_pass_count": counts.get("user_drop_spec_pass_count"),
            "historical_or_report_spec_pass_count": counts.get("historical_or_report_spec_pass_count"),
        },
    }


def _recommended_next_action(intake_summary: dict[str, Any], validation_summary: dict[str, Any]) -> str:
    status, decision = _final_decision(intake_summary, validation_summary)
    if status == "WAITING_FOR_STAGING":
        return "Use stage_hfss_s8p_manual_import_to_gate.py only for the matching HFSS export, then rerun this monitor."
    if status == "WAITING_FOR_CURRENT_GATE_HFSS":
        return "Export a matching HFSS .s8p into the V66/V67 current gate directory, then rerun this monitor."
    if status == "WAITING_FOR_HFSS":
        return "Inspect the current-gate sample mapping because intake found .s8p but downstream validation still cannot consume it."
    if status == "FAIL":
        return f"Fix the failing guard before continuing: {decision}."
    if status == "DRY_RUN":
        return "The gate is ready in dry-run mode; keep real EMX disabled unless intentionally starting production."
    return "Validation passed; proceed only through the gated million-sample executor."


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    intake = summary.get("latest_intake_summary") if isinstance(summary.get("latest_intake_summary"), dict) else {}
    validation = (
        summary.get("latest_validation_monitor_summary")
        if isinstance(summary.get("latest_validation_monitor_summary"), dict)
        else {}
    )
    counts = intake.get("counts") if isinstance(intake.get("counts"), dict) else {}
    lines = [
        "# HFSS S8P Intake To Validation Monitor",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Attempts: `{summary['attempt_count']}`",
        f"- Recommended next action: {summary['recommended_next_action']}",
        "",
        "## Intake",
        "",
        f"- Intake status: `{intake.get('overall_status')}`",
        f"- Intake decision: `{intake.get('decision')}`",
        f"- Current-gate spec-pass count: `{counts.get('current_gate_spec_pass_count')}`",
        f"- User-drop spec-pass count: `{counts.get('user_drop_spec_pass_count')}`",
        f"- Historical/report spec-pass count: `{counts.get('historical_or_report_spec_pass_count')}`",
        "",
        "## Validation Monitor",
        "",
        f"- Validation status: `{validation.get('overall_status')}`",
        f"- Validation decision: `{validation.get('decision')}`",
        f"- Validation attempts: `{validation.get('attempt_count')}`",
        "",
        "## Safety Notes",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
