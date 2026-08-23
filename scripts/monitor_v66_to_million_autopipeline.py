#!/usr/bin/env python3
"""Poll the V66-to-million autopipeline until it can advance or times out.

Use this when HFSS may finish later and you want a local watchdog to keep
checking.  The monitor only calls ``run_v66_to_million_autopipeline.py``; all
validation and production-safety decisions remain inside the underlying gate
scripts.  By default it performs one check.  For an overnight monitor, pass a
positive ``--timeout-seconds`` and ``--poll-seconds``.
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
DEFAULT_AUTOPIPELINE = SCRIPT_DIR / "run_v66_to_million_autopipeline.py"
DEFAULT_AUTOPIPELINE_OUT = PROJECT_ROOT / "outputs" / "v66_to_million_autopipeline_current"
DEFAULT_VISIBLE_RUNNER_AUDIT = SCRIPT_DIR / "audit_hfss_v66_visible_runner_status.py"
DEFAULT_VISIBLE_RUNNER_AUDIT_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_visible_runner_audit_current"
DEFAULT_RESILIENT_RUNNER_AUDIT = SCRIPT_DIR / "audit_hfss_v66_resilient_runner_status.py"
DEFAULT_RESILIENT_RUNNER_AUDIT_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_resilient_runner_audit_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "v66_to_million_autopipeline_monitor_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(args.timeout_seconds))
    attempts: list[dict[str, Any]] = []
    latest_summary: dict[str, Any] = {}
    latest_result: dict[str, Any] = {}
    latest_visible_result: dict[str, Any] = {}
    latest_visible_summary: dict[str, Any] = {}
    latest_resilient_result: dict[str, Any] = {}
    latest_resilient_summary: dict[str, Any] = {}

    while True:
        latest_visible_result = _run_visible_runner_audit(args)
        latest_visible_summary = _read_json(Path(args.visible_runner_audit_out_dir).expanduser().resolve() / "hfss_v66_visible_runner_audit_summary.json")
        latest_resilient_result = _run_resilient_runner_audit(args)
        latest_resilient_summary = _read_json(Path(args.resilient_runner_audit_out_dir).expanduser().resolve() / "hfss_v66_resilient_runner_audit_summary.json")
        if _runner_audits_block(args, latest_visible_summary, latest_resilient_summary):
            latest_result = {}
            latest_summary = {}
        else:
            latest_result = _run_autopipeline(args)
            latest_summary = _read_json(Path(args.autopipeline_out_dir).expanduser().resolve() / "v66_to_million_autopipeline_summary.json")
        attempts.append(
            {
                "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "visible_runner_status": latest_visible_summary.get("overall_status"),
                "visible_runner_decision": latest_visible_summary.get("decision"),
                "visible_runner_exported_s8p_count": latest_visible_summary.get("exported_s8p_count"),
                "resilient_runner_status": latest_resilient_summary.get("overall_status"),
                "resilient_runner_decision": latest_resilient_summary.get("decision"),
                "resilient_runner_exported_s8p_count": latest_resilient_summary.get("filesystem_exported_s8p_count"),
                "returncode": latest_result.get("returncode"),
                "overall_status": latest_summary.get("overall_status"),
                "decision": latest_summary.get("decision"),
                "watcher_summary": latest_summary.get("watcher_summary", {}),
                "executor_summary": latest_summary.get("executor_summary", {}),
                "resilient_audit_summary": latest_summary.get("resilient_audit_summary", {}),
                "report_packet_summary": latest_summary.get("report_packet_summary", {}),
            }
        )
        if _runner_audits_block(args, latest_visible_summary, latest_resilient_summary):
            break
        if str(latest_summary.get("overall_status") or "") in {"PASS", "DRY_RUN", "FAIL"}:
            break
        if float(args.timeout_seconds) <= 0.0 or time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, float(args.poll_seconds)))

    overall_status, decision = _decision(latest_summary, latest_visible_summary, latest_resilient_summary, args)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "latest_result": latest_result,
        "latest_visible_runner_result": latest_visible_result,
        "latest_resilient_runner_result": latest_resilient_result,
        "latest_visible_runner_summary": latest_visible_summary,
        "latest_resilient_runner_summary": latest_resilient_summary,
        "latest_autopipeline_summary": latest_summary,
        "arguments": {
            "autopipeline_script": str(Path(args.autopipeline_script).expanduser()),
            "autopipeline_out_dir": str(Path(args.autopipeline_out_dir).expanduser()),
            "visible_runner_audit_script": str(Path(args.visible_runner_audit_script).expanduser()),
            "visible_runner_audit_out_dir": str(Path(args.visible_runner_audit_out_dir).expanduser()),
            "skip_visible_runner_audit": bool(args.skip_visible_runner_audit),
            "resilient_runner_audit_script": str(Path(args.resilient_runner_audit_script).expanduser()),
            "resilient_runner_audit_out_dir": str(Path(args.resilient_runner_audit_out_dir).expanduser()),
            "skip_resilient_runner_audit": bool(args.skip_resilient_runner_audit),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "safety_notes": [
            "This monitor does not run HFSS; it waits for HFSS exports to appear in the V66 postrun paths.",
            "The visible and resilient runner audits are checked before each autopipeline attempt unless explicitly skipped.",
            "A failed visible runner audit does not block the monitor when the resilient runner audit is still usable.",
            "The autopipeline summary is expected to include the resilient runner audit status generated before the report packet.",
            "This monitor does not bypass the 10% EMX/HFSS gate.",
            "Production EMX still requires --allow-real-emx and a gate-passing million plan.",
        ],
    }
    summary_path = out_dir / "v66_to_million_autopipeline_monitor_summary.json"
    report_path = out_dir / "V66_TO_MILLION_AUTOPIPELINE_MONITOR_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"attempt_count={len(attempts)}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autopipeline-script", default=str(DEFAULT_AUTOPIPELINE))
    parser.add_argument("--autopipeline-out-dir", default=str(DEFAULT_AUTOPIPELINE_OUT))
    parser.add_argument("--visible-runner-audit-script", default=str(DEFAULT_VISIBLE_RUNNER_AUDIT))
    parser.add_argument("--visible-runner-audit-out-dir", default=str(DEFAULT_VISIBLE_RUNNER_AUDIT_OUT))
    parser.add_argument("--skip-visible-runner-audit", action="store_true")
    parser.add_argument("--resilient-runner-audit-script", default=str(DEFAULT_RESILIENT_RUNNER_AUDIT))
    parser.add_argument("--resilient-runner-audit-out-dir", default=str(DEFAULT_RESILIENT_RUNNER_AUDIT_OUT))
    parser.add_argument("--skip-resilient-runner-audit", action="store_true")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_visible_runner_audit(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_visible_runner_audit:
        return {
            "command": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "skipped": True,
        }
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.visible_runner_audit_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.visible_runner_audit_out_dir).expanduser().resolve()),
        "--no-fail-exit",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
        "skipped": False,
    }


def _run_resilient_runner_audit(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_resilient_runner_audit:
        return {
            "command": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "skipped": True,
        }
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.resilient_runner_audit_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.resilient_runner_audit_out_dir).expanduser().resolve()),
        "--no-fail-exit",
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
        "skipped": False,
    }


def _run_autopipeline(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.autopipeline_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.autopipeline_out_dir).expanduser().resolve()),
        "--timeout-seconds",
        "0",
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


def _runner_audits_block(args: argparse.Namespace, visible: dict[str, Any], resilient: dict[str, Any]) -> bool:
    statuses: list[str] = []
    if not args.skip_visible_runner_audit:
        statuses.append(str(visible.get("overall_status") or ""))
    if not args.skip_resilient_runner_audit:
        statuses.append(str(resilient.get("overall_status") or ""))
    return bool(statuses) and all(status == "FAIL" for status in statuses)


def _decision(
    latest_summary: dict[str, Any],
    latest_visible_summary: dict[str, Any],
    latest_resilient_summary: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str]:
    if _runner_audits_block(args, latest_visible_summary, latest_resilient_summary):
        visible_decision = str(latest_visible_summary.get("decision") or "")
        resilient_decision = str(latest_resilient_summary.get("decision") or "")
        return "FAIL", f"HFSS_RUNNER_AUDITS_FAILED visible={visible_decision} resilient={resilient_decision}".strip()
    status = str(latest_summary.get("overall_status") or "")
    decision = str(latest_summary.get("decision") or "")
    if status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_HFSS", "MONITOR_TIMEOUT_OR_SINGLE_CHECK_WAITING_FOR_HFSS"
    if status == "DRY_RUN":
        return "DRY_RUN", "AUTOPIPELINE_READY_DRY_RUN_COMPLETED"
    if status == "PASS":
        return "PASS", "AUTOPIPELINE_COMPLETED"
    if status == "FAIL":
        return "FAIL", decision or "AUTOPIPELINE_FAILED"
    return "FAIL", "AUTOPIPELINE_SUMMARY_MISSING_OR_UNKNOWN"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# V66 To Million Autopipeline Monitor",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Attempts: `{summary['attempt_count']}`",
        f"- Allow real EMX: `{summary['arguments']['allow_real_emx']}`",
        "",
        "## Visible Runner",
        "",
    ]
    visible = summary.get("latest_visible_runner_summary") if isinstance(summary.get("latest_visible_runner_summary"), dict) else {}
    for key in ("overall_status", "decision", "exported_s8p_count", "export_manifest_count"):
        lines.append(f"- `{key}`: `{visible.get(key)}`")
    lines.extend([
        "",
        "## Resilient Runner",
        "",
    ])
    latest = summary.get("latest_autopipeline_summary") if isinstance(summary.get("latest_autopipeline_summary"), dict) else {}
    resilient = summary.get("latest_resilient_runner_summary") if isinstance(summary.get("latest_resilient_runner_summary"), dict) else {}
    for key in ("overall_status", "decision", "expected_variant_count", "filesystem_exported_s8p_count", "filesystem_export_manifest_count"):
        lines.append(f"- `{key}`: `{resilient.get(key)}`")
    lines.extend([
        "",
        "## Latest Autopipeline",
        "",
    ])
    for key in ("overall_status", "decision", "allow_real_emx"):
        lines.append(f"- `{key}`: `{latest.get(key)}`")
    lines.extend([
        "",
        "## Report Packet",
        "",
    ])
    report_packet = latest.get("report_packet_summary") if isinstance(latest.get("report_packet_summary"), dict) else {}
    for key in ("overall_status", "decision", "postrun_status", "report_packet_status", "report_packet_decision"):
        lines.append(f"- `{key}`: `{report_packet.get(key)}`")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
