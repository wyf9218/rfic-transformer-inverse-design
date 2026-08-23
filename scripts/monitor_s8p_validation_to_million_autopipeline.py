#!/usr/bin/env python3
"""Poll the unified S8P validation-to-million autopipeline.

Use this local watchdog after HFSS is running elsewhere.  It repeatedly calls
``run_s8p_validation_to_million_autopipeline.py`` until V66 or V67 unlocks the
million-sample executor, the validation branches fail, or the timeout expires.
It does not run HFSS and it does not bypass the EMX/HFSS 10% validation gate.
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
DEFAULT_AUTOPIPELINE = SCRIPT_DIR / "run_s8p_validation_to_million_autopipeline.py"
DEFAULT_AUTOPIPELINE_OUT = PROJECT_ROOT / "outputs" / "s8p_validation_to_million_autopipeline_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "s8p_validation_to_million_autopipeline_monitor_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(args.timeout_seconds))
    attempts: list[dict[str, Any]] = []
    latest_result: dict[str, Any] = {}
    latest_summary: dict[str, Any] = {}

    while True:
        latest_result = _run_autopipeline(args)
        latest_summary = _read_json(Path(args.autopipeline_out_dir).expanduser().resolve() / "s8p_validation_to_million_autopipeline_summary.json")
        attempts.append(_attempt_record(latest_result, latest_summary))
        status = str(latest_summary.get("overall_status") or "")
        if status in {"PASS", "DRY_RUN", "FAIL"}:
            break
        if float(args.timeout_seconds) <= 0.0 or time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, float(args.poll_seconds)))

    overall_status, decision = _decision(latest_summary)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "latest_result": latest_result,
        "latest_autopipeline_summary": latest_summary,
        "arguments": {
            "autopipeline_script": str(Path(args.autopipeline_script).expanduser()),
            "autopipeline_out_dir": str(Path(args.autopipeline_out_dir).expanduser()),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "safety_notes": [
            "This monitor does not run HFSS; it waits for V66/V67 HFSS .s8p exports through the unified autopipeline.",
            "Million execution only starts after a watcher reports READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN.",
            "The million executor still requires the generated plan and this monitor to both allow real EMX.",
            "V67 diagnostic-only skip-pin passes cannot unlock production because the V67 watcher rejects them.",
            "A WAITING_FOR_HFSS status means the first-stage EMX/HFSS <=10% evidence is still missing.",
        ],
    }
    summary_path = out_dir / "s8p_validation_to_million_autopipeline_monitor_summary.json"
    report_path = out_dir / "S8P_VALIDATION_TO_MILLION_AUTOPIPELINE_MONITOR_CN.md"
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
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


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


def _attempt_record(result: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    watchers = summary.get("watchers") if isinstance(summary.get("watchers"), list) else []
    return {
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "returncode": result.get("returncode"),
        "overall_status": summary.get("overall_status"),
        "decision": summary.get("decision"),
        "selected_source": summary.get("selected_source", ""),
        "allow_real_emx": summary.get("allow_real_emx"),
        "watchers": [
            {
                "label": item.get("label"),
                "overall_status": (item.get("summary") or {}).get("overall_status") if isinstance(item, dict) else None,
                "decision": (item.get("summary") or {}).get("decision") if isinstance(item, dict) else None,
                "selected_variant": (item.get("summary") or {}).get("selected_variant") if isinstance(item, dict) else None,
            }
            for item in watchers
            if isinstance(item, dict)
        ],
        "executor_summary": summary.get("executor_summary") if isinstance(summary.get("executor_summary"), dict) else {},
    }


def _decision(summary: dict[str, Any]) -> tuple[str, str]:
    status = str(summary.get("overall_status") or "")
    decision = str(summary.get("decision") or "")
    if status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_HFSS", "MONITOR_TIMEOUT_OR_SINGLE_CHECK_WAITING_FOR_HFSS"
    if status == "DRY_RUN":
        return "DRY_RUN", "UNIFIED_AUTOPIPELINE_READY_DRY_RUN_COMPLETED"
    if status == "PASS":
        return "PASS", "UNIFIED_AUTOPIPELINE_COMPLETED"
    if status == "FAIL":
        return "FAIL", decision or "UNIFIED_AUTOPIPELINE_FAILED"
    return "FAIL", "UNIFIED_AUTOPIPELINE_SUMMARY_MISSING_OR_UNKNOWN"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    latest = summary.get("latest_autopipeline_summary") if isinstance(summary.get("latest_autopipeline_summary"), dict) else {}
    lines = [
        "# S8P Validation To Million Autopipeline Monitor",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Attempts: `{summary['attempt_count']}`",
        f"- Allow real EMX: `{summary['arguments']['allow_real_emx']}`",
        "",
        "## Latest Unified Autopipeline",
        "",
    ]
    for key in ("overall_status", "decision", "selected_source", "allow_real_emx"):
        lines.append(f"- `{key}`: `{latest.get(key)}`")
    lines.extend(["", "## Watchers", ""])
    watchers = latest.get("watchers") if isinstance(latest.get("watchers"), list) else []
    if watchers:
        for watcher in watchers:
            watcher_summary = watcher.get("summary") if isinstance(watcher.get("summary"), dict) else {}
            lines.append(
                f"- `{watcher.get('label')}`: `{watcher_summary.get('overall_status')}` / `{watcher_summary.get('decision')}`"
            )
    else:
        lines.append("- No watcher summaries were recorded.")
    lines.extend(["", "## Executor", ""])
    executor = latest.get("executor_summary") if isinstance(latest.get("executor_summary"), dict) else {}
    for key in ("overall_status", "decision", "selected_chunk_count", "completed_chunk_count"):
        lines.append(f"- `{key}`: `{executor.get(key)}`")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
