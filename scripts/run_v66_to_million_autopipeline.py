#!/usr/bin/env python3
"""Run the safe V66-to-million automation pipeline.

This is the top-level local orchestrator:

1. run the V66 EMX/HFSS watcher,
2. only if that watcher unlocks the gated million-sample plan, run the
   million campaign executor,
3. keep production EMX disabled unless --allow-real-emx is explicitly passed.

It does not run HFSS itself.  HFSS still has to be run by the Windows V66
runner, and the exported `.s8p` files still have to pass the 10% EMX/HFSS gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_WATCH_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_to_million_gate_watch_current"
DEFAULT_EXECUTOR_OUT = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_execution_current"
DEFAULT_RESILIENT_AUDIT_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_resilient_runner_audit_current"
DEFAULT_REPORT_PACKET_OUT = PROJECT_ROOT / "outputs" / "v66_validation_report_packet_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "v66_to_million_autopipeline_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    watcher_result = _run_watcher(args)
    watcher_summary_path = Path(args.watch_out_dir).expanduser().resolve() / "hfss_v66_calibration_to_million_gate_watch_summary.json"
    watcher_summary = _read_json(watcher_summary_path)

    executor_result: dict[str, Any] | None = None
    executor_summary: dict[str, Any] = {}
    if _watcher_unlocks_million(watcher_summary):
        executor_result = _run_executor(args)
        executor_summary_path = Path(args.executor_out_dir).expanduser().resolve() / "s8p_million_campaign_execution_summary.json"
        executor_summary = _read_json(executor_summary_path)

    resilient_audit_result = _run_resilient_audit(args)
    resilient_audit_summary_path = Path(args.resilient_audit_out_dir).expanduser().resolve() / "hfss_v66_resilient_runner_audit_summary.json"
    resilient_audit_summary = _read_json(resilient_audit_summary_path)

    report_packet_result = _run_report_packet(args, resilient_audit_summary_path)
    report_packet_summary_path = Path(args.report_packet_out_dir).expanduser().resolve() / "v66_validation_report_packet_summary.json"
    report_packet_summary = _read_json(report_packet_summary_path)

    overall_status, decision = _decision(watcher_summary, executor_summary, report_packet_summary)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "allow_real_emx": bool(args.allow_real_emx),
        "watcher_result": watcher_result,
        "watcher_summary_path": str(watcher_summary_path),
        "watcher_summary": _compact_summary(watcher_summary),
        "executor_result": executor_result,
        "executor_summary": _compact_summary(executor_summary),
        "resilient_audit_result": resilient_audit_result,
        "resilient_audit_summary_path": str(resilient_audit_summary_path),
        "resilient_audit_summary": _compact_summary(resilient_audit_summary),
        "report_packet_result": report_packet_result,
        "report_packet_summary_path": str(report_packet_summary_path),
        "report_packet_summary": _compact_summary(report_packet_summary),
        "arguments": {
            "watcher_script": str(Path(args.watcher_script).expanduser()),
            "executor_script": str(Path(args.executor_script).expanduser()),
            "resilient_audit_script": str(Path(args.resilient_audit_script).expanduser()),
            "report_packet_script": str(Path(args.report_packet_script).expanduser()),
            "watch_out_dir": str(Path(args.watch_out_dir).expanduser()),
            "executor_out_dir": str(Path(args.executor_out_dir).expanduser()),
            "resilient_audit_out_dir": str(Path(args.resilient_audit_out_dir).expanduser()),
            "report_packet_out_dir": str(Path(args.report_packet_out_dir).expanduser()),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "safety_notes": [
            "HFSS is not run by this local orchestrator; the Windows V66 runner must export .s8p files first.",
            "The resilient HFSS runner audit is regenerated before the validation report packet.",
            "Million execution only starts after the V66 watcher reports READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN.",
            "The V66 validation report packet is regenerated on every autopipeline run and remains WAITING until HFSS evidence exists.",
            "Production EMX requires --allow-real-emx here and in the generated million plan.",
        ],
    }
    summary_path = out_dir / "v66_to_million_autopipeline_summary.json"
    report_path = out_dir / "V66_TO_MILLION_AUTOPIPELINE_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watcher-script", default=str(SCRIPT_DIR / "watch_hfss_v66_calibration_to_million_gate.py"))
    parser.add_argument("--executor-script", default=str(SCRIPT_DIR / "run_s8p_million_campaign_from_plan.py"))
    parser.add_argument("--resilient-audit-script", default=str(SCRIPT_DIR / "audit_hfss_v66_resilient_runner_status.py"))
    parser.add_argument("--report-packet-script", default=str(SCRIPT_DIR / "build_v66_validation_report_packet.py"))
    parser.add_argument("--watch-out-dir", default=str(DEFAULT_WATCH_OUT))
    parser.add_argument("--executor-out-dir", default=str(DEFAULT_EXECUTOR_OUT))
    parser.add_argument("--resilient-audit-out-dir", default=str(DEFAULT_RESILIENT_AUDIT_OUT))
    parser.add_argument("--report-packet-out-dir", default=str(DEFAULT_REPORT_PACKET_OUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_watcher(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.watcher_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.watch_out_dir).expanduser().resolve()),
        "--timeout-seconds",
        f"{float(args.timeout_seconds):g}",
        "--poll-seconds",
        f"{float(args.poll_seconds):g}",
        "--no-fail-exit",
    ]
    if args.allow_real_emx:
        command.append("--allow-real-emx")
    return _run_command(command)


def _run_executor(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.executor_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.executor_out_dir).expanduser().resolve()),
        "--no-fail-exit",
    ]
    if args.allow_real_emx:
        command.append("--allow-real-emx")
    return _run_command(command)


def _run_resilient_audit(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.resilient_audit_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.resilient_audit_out_dir).expanduser().resolve()),
        "--no-fail-exit",
    ]
    return _run_command(command)


def _run_report_packet(args: argparse.Namespace, resilient_audit_summary_path: Path) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.report_packet_script).expanduser().resolve()),
        "--out-dir",
        str(Path(args.report_packet_out_dir).expanduser().resolve()),
        "--resilient-runner-summary",
        str(resilient_audit_summary_path),
        "--no-fail-exit",
    ]
    return _run_command(command)


def _run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
    }


def _watcher_unlocks_million(summary: dict[str, Any]) -> bool:
    return (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN"
    )


def _decision(watcher: dict[str, Any], executor: dict[str, Any], report_packet: dict[str, Any]) -> tuple[str, str]:
    if report_packet and report_packet.get("overall_status") == "FAIL":
        return "FAIL", "V66_VALIDATION_REPORT_PACKET_FAILED"
    watcher_status = str(watcher.get("overall_status") or "")
    watcher_decision = str(watcher.get("decision") or "")
    if watcher_status == "PASS" and watcher_decision == "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN":
        executor_status = str(executor.get("overall_status") or "")
        if executor_status == "PASS":
            return "PASS", "V66_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED"
        if executor_status == "DRY_RUN":
            return "DRY_RUN", "V66_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY"
        return "FAIL", "V66_GATE_PASSED_BUT_MILLION_EXECUTOR_FAILED"
    if watcher_status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_HFSS", "WAIT_FOR_V66_EXPORTED_HFSS_S8P_BEFORE_MILLION_EXECUTION"
    if watcher_status == "FAIL":
        return "FAIL", "V66_WATCHER_FAILED_DO_NOT_EXECUTE_MILLION"
    return "WAITING_FOR_HFSS", "WAIT_FOR_V66_GATE_DECISION"


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    audit = latest.get("execution_packet_audit_summary") if isinstance(latest.get("execution_packet_audit_summary"), dict) else {}
    compact = {
        "overall_status": summary.get("overall_status"),
        "decision": summary.get("decision"),
        "chunk_count": summary.get("chunk_count"),
        "selected_chunk_count": summary.get("selected_chunk_count"),
        "completed_chunk_count": summary.get("completed_chunk_count"),
        "variant_status_counts": latest.get("variant_status_counts"),
        "audit_status": audit.get("overall_status"),
        "audit_hfss_result_status": audit.get("hfss_result_status"),
        "audit_exported_s8p_count": audit.get("exported_s8p_count"),
        "resilient_runner_status": summary.get("overall_status") if "filesystem_exported_s8p_count" in summary else None,
        "resilient_runner_decision": summary.get("decision") if "filesystem_exported_s8p_count" in summary else None,
        "resilient_exported_s8p_count": summary.get("filesystem_exported_s8p_count"),
        "postrun_status": summary.get("postrun_status"),
        "report_packet_status": summary.get("overall_status") if "postrun_status" in summary else None,
        "report_packet_decision": summary.get("decision") if "postrun_status" in summary else None,
        "historical_recompare_candidate_count": summary.get("historical_recompare_candidate_count"),
        "historical_recompare_pass_count": summary.get("historical_recompare_pass_count"),
        "historical_recompare_best_target15_worst_percent_error": summary.get(
            "historical_recompare_best_target15_worst_percent_error"
        ),
    }
    return {key: value for key, value in compact.items() if value is not None}


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
        "# V66 To Million Autopipeline",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Allow real EMX: `{summary['allow_real_emx']}`",
        "",
        "## Watcher",
        "",
    ]
    for key, value in summary["watcher_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Executor", ""])
    for key, value in summary["executor_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Resilient HFSS Runner Audit", ""])
    for key, value in summary["resilient_audit_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Report Packet", ""])
    for key, value in summary["report_packet_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
