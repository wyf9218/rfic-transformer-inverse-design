#!/usr/bin/env python3
"""Run the unified S8P validation-to-million automation pipeline.

This orchestrator does not run HFSS. It runs available local validation
watchers in priority order and starts the million-sample executor only after a
watcher reports a gated EMX/HFSS pass.
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
DEFAULT_V66_WATCH_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_to_million_gate_watch_current"
DEFAULT_V67_WATCH_OUT = PROJECT_ROOT / "outputs" / "hfss_v67_material_mesh_to_million_gate_watch_current"
DEFAULT_EXECUTOR_OUT = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_execution_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "s8p_validation_to_million_autopipeline_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    watcher_results: list[dict[str, Any]] = []
    selected_source = ""
    selected_summary: dict[str, Any] = {}

    if not args.skip_v66:
        v66 = _run_watcher(
            label="v66",
            script=Path(args.v66_watcher_script).expanduser().resolve(),
            out_dir=Path(args.v66_watch_out_dir).expanduser().resolve(),
            summary_name="hfss_v66_calibration_to_million_gate_watch_summary.json",
            args=args,
        )
        watcher_results.append(v66)
        if _watcher_unlocks_million(v66["summary"]):
            selected_source = "v66"
            selected_summary = v66["summary"]

    if not selected_source and not args.skip_v67:
        v67 = _run_watcher(
            label="v67",
            script=Path(args.v67_watcher_script).expanduser().resolve(),
            out_dir=Path(args.v67_watch_out_dir).expanduser().resolve(),
            summary_name="hfss_v67_material_mesh_to_million_gate_watch_summary.json",
            args=args,
        )
        watcher_results.append(v67)
        if _watcher_unlocks_million(v67["summary"]):
            selected_source = "v67"
            selected_summary = v67["summary"]

    executor_result: dict[str, Any] | None = None
    executor_summary: dict[str, Any] = {}
    if selected_source:
        executor_result = _run_executor(args)
        executor_summary_path = Path(args.executor_out_dir).expanduser().resolve() / "s8p_million_campaign_execution_summary.json"
        executor_summary = _read_json(executor_summary_path)

    overall_status, decision = _decision(watcher_results, selected_source, executor_summary)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "allow_real_emx": bool(args.allow_real_emx),
        "selected_source": selected_source,
        "selected_watcher_summary": _compact_watcher_summary(selected_summary),
        "watchers": [
            {
                "label": item["label"],
                "result": item["result"],
                "summary_path": item["summary_path"],
                "summary": _compact_watcher_summary(item["summary"]),
            }
            for item in watcher_results
        ],
        "executor_result": executor_result,
        "executor_summary": _compact_executor_summary(executor_summary),
        "arguments": {
            "v66_watcher_script": str(Path(args.v66_watcher_script).expanduser()),
            "v67_watcher_script": str(Path(args.v67_watcher_script).expanduser()),
            "executor_script": str(Path(args.executor_script).expanduser()),
            "v66_watch_out_dir": str(Path(args.v66_watch_out_dir).expanduser()),
            "v67_watch_out_dir": str(Path(args.v67_watch_out_dir).expanduser()),
            "executor_out_dir": str(Path(args.executor_out_dir).expanduser()),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "allow_real_emx": bool(args.allow_real_emx),
            "skip_v66": bool(args.skip_v66),
            "skip_v67": bool(args.skip_v67),
        },
        "safety_notes": [
            "HFSS is not run by this local orchestrator.",
            "Million execution only starts after one validation watcher reports READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN.",
            "V67 diagnostic-only skip-pin passes are rejected by the V67 watcher and cannot unlock production.",
            "Production EMX requires --allow-real-emx here and in the generated million plan.",
        ],
    }
    summary_path = out_dir / "s8p_validation_to_million_autopipeline_summary.json"
    report_path = out_dir / "S8P_VALIDATION_TO_MILLION_AUTOPIPELINE_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN", "WAITING_FOR_HFSS"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v66-watcher-script", default=str(SCRIPT_DIR / "watch_hfss_v66_calibration_to_million_gate.py"))
    parser.add_argument("--v67-watcher-script", default=str(SCRIPT_DIR / "watch_hfss_v67_material_mesh_to_million_gate.py"))
    parser.add_argument("--executor-script", default=str(SCRIPT_DIR / "run_s8p_million_campaign_from_plan.py"))
    parser.add_argument("--v66-watch-out-dir", default=str(DEFAULT_V66_WATCH_OUT))
    parser.add_argument("--v67-watch-out-dir", default=str(DEFAULT_V67_WATCH_OUT))
    parser.add_argument("--executor-out-dir", default=str(DEFAULT_EXECUTOR_OUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--skip-v66", action="store_true")
    parser.add_argument("--skip-v67", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_watcher(
    *,
    label: str,
    script: Path,
    out_dir: Path,
    summary_name: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(script),
        "--out-dir",
        str(out_dir),
        "--timeout-seconds",
        f"{float(args.timeout_seconds):g}",
        "--poll-seconds",
        f"{float(args.poll_seconds):g}",
        "--no-fail-exit",
    ]
    if args.allow_real_emx:
        command.append("--allow-real-emx")
    result = _run_command(command)
    summary_path = out_dir / summary_name
    return {
        "label": label,
        "result": result,
        "summary_path": str(summary_path),
        "summary": _read_json(summary_path),
    }


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


def _decision(
    watchers: list[dict[str, Any]],
    selected_source: str,
    executor_summary: dict[str, Any],
) -> tuple[str, str]:
    if selected_source:
        executor_status = str(executor_summary.get("overall_status") or "")
        if executor_status == "PASS":
            return "PASS", "S8P_GATE_PASSED_AND_MILLION_EXECUTION_COMPLETED"
        if executor_status == "DRY_RUN":
            return "DRY_RUN", "S8P_GATE_PASSED_MILLION_EXECUTOR_DRY_RUN_READY"
        return "FAIL", "S8P_GATE_PASSED_BUT_MILLION_EXECUTOR_FAILED"
    statuses = [str(item.get("summary", {}).get("overall_status") or "") for item in watchers]
    if any(status == "WAITING_FOR_HFSS" for status in statuses):
        return "WAITING_FOR_HFSS", "WAIT_FOR_ANY_S8P_VALIDATION_BRANCH_HFSS_EXPORT"
    if statuses and all(status == "FAIL" for status in statuses):
        return "FAIL", "ALL_S8P_VALIDATION_BRANCHES_FAILED"
    return "WAITING_FOR_HFSS", "WAIT_FOR_ANY_S8P_VALIDATION_BRANCH_DECISION"


def _compact_watcher_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    selected = latest.get("selected_variant") if isinstance(latest.get("selected_variant"), dict) else {}
    return {
        "overall_status": summary.get("overall_status"),
        "decision": summary.get("decision"),
        "variant_status_counts": latest.get("variant_status_counts"),
        "selected_variant": selected.get("name", ""),
        "campaign_status": latest.get("campaign_summary", {}).get("overall_status")
        if isinstance(latest.get("campaign_summary"), dict)
        else None,
    }


def _compact_executor_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    return {
        "overall_status": summary.get("overall_status"),
        "decision": summary.get("decision"),
        "allow_real_emx": summary.get("allow_real_emx"),
        "selected_chunk_count": summary.get("selected_chunk_count"),
        "completed_chunk_count": summary.get("completed_chunk_count"),
    }


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
        "# S8P Validation To Million Autopipeline",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Selected source: `{summary['selected_source']}`",
        f"- Allow real EMX: `{summary['allow_real_emx']}`",
        "",
        "## Watchers",
        "",
    ]
    for watcher in summary["watchers"]:
        lines.append(f"### {watcher['label']}")
        for key, value in watcher["summary"].items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Executor", ""])
    for key, value in summary["executor_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
