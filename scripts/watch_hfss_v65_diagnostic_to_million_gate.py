#!/usr/bin/env python3
"""Watch HFSS V65 diagnostics and advance the gated million-sample flow.

This local watcher does not run HFSS, EMX, ADS, Guacamole, or SSH.  It repeatedly
executes the local postrun/promotion gates:

1. validate any exported V65 diagnostic `.s8p` files,
2. promote the best diagnostic PASS to a full 5-60 GHz HFSS run plan,
3. validate any exported full-frequency `.s8p` files,
4. only then ask the million-sample planner to unlock.

WAITING is an expected state while HFSS exports are absent.  PASS means the
validated full 5-60 GHz EMX/HFSS S8P gate is strong enough for the campaign
planner; it does not mean this watcher generated EMX data itself.
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
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v65_diagnostic_to_million_gate_watch_current"
DEFAULT_DIAG_POSTRUN = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "postrun_validate_hfss_lp_ls_reference_sweep.sh"
DEFAULT_DIAG_PLAN = PROJECT_ROOT / "outputs" / "hfss_lp_ls_reference_sweep_plan_current" / "hfss_lp_ls_reference_sweep_plan_summary.json"
DEFAULT_PROMOTION_OUT = PROJECT_ROOT / "outputs" / "hfss_lp_ls_full_sweep_promotion_current"
DEFAULT_CAMPAIGN_OUT = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_plan_current"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(args.timeout_seconds))
    attempts: list[dict[str, Any]] = []
    latest: dict[str, Any] = {}

    while True:
        latest = _run_once(args)
        attempts.append(
            {
                "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "overall_status": latest["overall_status"],
                "decision": latest["decision"],
                "promotion_status": latest.get("promotion_summary", {}).get("overall_status", ""),
                "full_postrun_status": latest.get("full_postrun_summary", {}).get("overall_status", ""),
                "campaign_status": latest.get("campaign_summary", {}).get("overall_status", ""),
            }
        )
        if latest["overall_status"] in {"PASS", "FAIL"}:
            break
        if float(args.timeout_seconds) <= 0.0 or time.monotonic() >= deadline:
            break
        time.sleep(max(1.0, float(args.poll_seconds)))

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": latest.get("overall_status", "WAITING"),
        "decision": latest.get("decision", "WAITING_FOR_HFSS_RESULTS"),
        "out_dir": str(out_dir),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "latest": latest,
        "arguments": {
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "diagnostic_postrun_script": str(Path(args.diagnostic_postrun_script).expanduser()),
            "diagnostic_plan_summary": str(Path(args.diagnostic_plan_summary).expanduser()),
            "promotion_out_dir": str(Path(args.promotion_out_dir).expanduser()),
            "campaign_out_dir": str(Path(args.campaign_out_dir).expanduser()),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "method_notes": [
            "This watcher is local-side orchestration only; HFSS exports must be produced by the Windows/HFSS runner.",
            "Diagnostic two-point PASS can only select a full-run variant; it cannot unlock the million-sample campaign.",
            "The million-sample planner is invoked only after a full 5-60 GHz postrun validation summary exists and passes.",
        ],
    }
    summary_path = out_dir / "hfss_v65_diagnostic_to_million_gate_watch_summary.json"
    report_path = out_dir / "HFSS_V65_DIAGNOSTIC_TO_MILLION_GATE_WATCH_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 2 if summary["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--diagnostic-postrun-script", default=str(DEFAULT_DIAG_POSTRUN))
    parser.add_argument("--diagnostic-plan-summary", default=str(DEFAULT_DIAG_PLAN))
    parser.add_argument("--promotion-script", default=str(SCRIPT_DIR / "promote_hfss_lp_ls_diagnostic_to_full_sweep.py"))
    parser.add_argument("--promotion-out-dir", default=str(DEFAULT_PROMOTION_OUT))
    parser.add_argument("--million-planner-script", default=str(SCRIPT_DIR / "run_gated_s8p_million_sample_campaign.py"))
    parser.add_argument("--campaign-out-dir", default=str(DEFAULT_CAMPAIGN_OUT))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_once(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_postrun = _run_diagnostic_postrun(args)
    promotion = _run_promotion(args)
    promotion_summary_path = Path(args.promotion_out_dir).expanduser().resolve() / "hfss_lp_ls_full_sweep_promotion_summary.json"
    promotion_summary = _read_json(promotion_summary_path)

    full_postrun_result: dict[str, Any] | None = None
    full_postrun_summary_path: Path | None = None
    full_postrun_summary: dict[str, Any] = {}
    if promotion_summary.get("overall_status") == "PASS":
        validator = str(promotion_summary.get("full_postrun_validator") or "")
        if validator:
            full_postrun_result = _run_command(["bash", validator], cwd=PROJECT_ROOT)
            full_postrun_summary_path = _infer_full_postrun_summary_path(promotion_summary)
            full_postrun_summary = _read_json(full_postrun_summary_path)

    campaign_result: dict[str, Any] | None = None
    campaign_summary_path = Path(args.campaign_out_dir).expanduser().resolve() / "s8p_million_sample_campaign_plan_summary.json"
    campaign_summary: dict[str, Any] = {}
    if full_postrun_summary.get("overall_status") == "PASS" and full_postrun_summary_path is not None:
        campaign_result = _run_million_planner(args, full_postrun_summary_path)
        campaign_summary = _read_json(campaign_summary_path)

    overall_status, decision = _decision(promotion_summary, full_postrun_summary, campaign_summary)
    return {
        "overall_status": overall_status,
        "decision": decision,
        "diagnostic_postrun_result": diagnostic_postrun,
        "promotion_result": promotion,
        "promotion_summary_path": str(promotion_summary_path),
        "promotion_summary": promotion_summary,
        "full_postrun_result": full_postrun_result,
        "full_postrun_summary_path": "" if full_postrun_summary_path is None else str(full_postrun_summary_path),
        "full_postrun_summary": full_postrun_summary,
        "campaign_result": campaign_result,
        "campaign_summary_path": str(campaign_summary_path),
        "campaign_summary": campaign_summary,
    }


def _run_diagnostic_postrun(args: argparse.Namespace) -> dict[str, Any]:
    script = Path(args.diagnostic_postrun_script).expanduser().resolve()
    if not script.is_file():
        return {"returncode": 2, "command": ["bash", str(script)], "stdout_tail": "", "stderr_tail": f"missing {script}"}
    return _run_command(["bash", str(script)], cwd=PROJECT_ROOT)


def _run_promotion(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.promotion_script).expanduser().resolve()),
        "--diagnostic-plan-summary",
        str(Path(args.diagnostic_plan_summary).expanduser().resolve()),
        "--out-dir",
        str(Path(args.promotion_out_dir).expanduser().resolve()),
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--no-fail-exit",
    ]
    return _run_command(command, cwd=REPO_ROOT)


def _run_million_planner(args: argparse.Namespace, validation_summary: Path) -> dict[str, Any]:
    command = [
        str(Path(args.python).expanduser()),
        str(Path(args.million_planner_script).expanduser().resolve()),
        "--validation-summary",
        str(validation_summary),
        "--out-dir",
        str(Path(args.campaign_out_dir).expanduser().resolve()),
        "--max-percent-error",
        f"{float(args.max_percent_error):g}",
        "--no-fail-exit",
    ]
    if args.allow_real_emx:
        command.append("--allow-real-emx")
    return _run_command(command, cwd=REPO_ROOT)


def _run_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": int(completed.returncode),
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
    }


def _infer_full_postrun_summary_path(promotion_summary: dict[str, Any]) -> Path:
    out_dir = Path(str(promotion_summary.get("out_dir") or "")).expanduser().resolve()
    return out_dir / "full_selected_variant_postrun_validation" / "s8p_hfss_postrun_validation_summary.json"


def _decision(
    promotion_summary: dict[str, Any],
    full_postrun_summary: dict[str, Any],
    campaign_summary: dict[str, Any],
) -> tuple[str, str]:
    campaign_status = str(campaign_summary.get("overall_status") or "")
    if campaign_status == "PASS":
        return "PASS", "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN"
    if campaign_status == "FAIL":
        return "FAIL", "MILLION_CAMPAIGN_GATE_FAILED_AFTER_FULL_POSTRUN"

    full_status = str(full_postrun_summary.get("overall_status") or "")
    if full_status == "PASS":
        return "WAITING_FOR_CAMPAIGN_PLANNER", "FULL_HFSS_POSTRUN_PASSED_RUN_MILLION_PLANNER"
    if full_status == "FAIL":
        return "FAIL", "FULL_HFSS_POSTRUN_FAILED"
    if full_status == "WAITING_FOR_HFSS":
        return "WAITING_FOR_FULL_HFSS", "WAIT_FOR_SELECTED_VARIANT_FULL_5_60_HFSS_S8P"

    promotion_status = str(promotion_summary.get("overall_status") or "")
    promotion_decision = str(promotion_summary.get("decision") or "")
    if promotion_status == "PASS":
        return "WAITING_FOR_FULL_HFSS", "RUN_SELECTED_VARIANT_FULL_5_60_HFSS_AND_WAIT_FOR_S8P"
    if promotion_status == "WAITING_FOR_DIAGNOSTIC_HFSS":
        return "WAITING_FOR_DIAGNOSTIC_HFSS", "WAIT_FOR_V65_DIAGNOSTIC_HFSS_S8P"
    if promotion_status == "FAIL":
        return "FAIL", promotion_decision or "DIAGNOSTIC_PROMOTION_FAILED"
    return "WAITING", "WAITING_FOR_HFSS_RESULTS"


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _render_report(summary: dict[str, Any]) -> str:
    latest = summary.get("latest") if isinstance(summary.get("latest"), dict) else {}
    lines = [
        "# HFSS V65 Diagnostic To Million Gate Watch",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Attempts: `{summary.get('attempt_count')}`",
        f"- Promotion summary: `{latest.get('promotion_summary_path', '')}`",
        f"- Full postrun summary: `{latest.get('full_postrun_summary_path', '')}`",
        f"- Campaign summary: `{latest.get('campaign_summary_path', '')}`",
        "",
        "## Current Meaning",
        "",
    ]
    status = summary["overall_status"]
    if status == "WAITING_FOR_DIAGNOSTIC_HFSS":
        lines.append("V65 两点诊断 `.s8p` 还没有通过；等待 HFSS 导出或修复。")
    elif status == "WAITING_FOR_FULL_HFSS":
        lines.append("已有可晋级诊断变体；等待该变体完整 5-60GHz HFSS `.s8p`。")
    elif status == "PASS":
        lines.append("完整 5-60GHz EMX/HFSS gate 已足以让百万级 campaign planner 解锁。")
    elif status == "FAIL":
        lines.append("当前 gate 失败；不能启动百万级 EMX。")
    else:
        lines.append("仍在等待上游 HFSS 或 planner 结果。")
    lines.extend(["", "## Attempts", "", "| # | UTC | Status | Decision |", "|---:|---|---|---|"])
    for index, attempt in enumerate(summary.get("attempts") or [], start=1):
        lines.append(f"| {index} | {attempt.get('utc', '')} | {attempt.get('overall_status', '')} | {attempt.get('decision', '')} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
