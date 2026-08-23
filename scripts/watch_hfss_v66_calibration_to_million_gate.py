#!/usr/bin/env python3
"""Watch HFSS V66 calibration results and advance the million-sample gate.

This local watcher does not run HFSS, EMX, ADS, Guacamole, or SSH. It runs the
local V66 postrun validator, reads the eight variant validation summaries, and
only invokes the million-sample planner after at least one full 5-60 GHz HFSS
`.s8p` passes the EMX/HFSS Lp/Ls/Q/K/Kw gate.
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
DEFAULT_V66_PLAN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "hfss_v66_calibration_plan_summary.json"
DEFAULT_V66_POSTRUN = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "postrun_validate_hfss_v66_calibration.sh"
DEFAULT_V66_WINDOWS_RUNNER = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_plan_current" / "run_hfss_v66_calibration.windows.ps1"
DEFAULT_EXECUTION_PACKET_AUDIT_OUT = PROJECT_ROOT / "outputs" / "hfss_v66_execution_packet_audit_current"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "hfss_v66_calibration_to_million_gate_watch_current"
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
                "variant_status_counts": latest.get("variant_status_counts", {}),
                "selected_variant": latest.get("selected_variant", {}).get("name", ""),
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
        "overall_status": latest.get("overall_status", "WAITING_FOR_HFSS"),
        "decision": latest.get("decision", "WAIT_FOR_V66_HFSS_S8P"),
        "out_dir": str(out_dir),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "latest": latest,
        "arguments": {
            "v66_plan_summary": str(Path(args.v66_plan_summary).expanduser()),
            "v66_postrun_script": str(Path(args.v66_postrun_script).expanduser()),
            "v66_windows_runner": str(Path(args.v66_windows_runner).expanduser()),
            "execution_packet_audit_script": str(Path(args.execution_packet_audit_script).expanduser()),
            "execution_packet_audit_out_dir": str(Path(args.execution_packet_audit_out_dir).expanduser()),
            "skip_execution_packet_audit": bool(args.skip_execution_packet_audit),
            "campaign_out_dir": str(Path(args.campaign_out_dir).expanduser()),
            "timeout_seconds": float(args.timeout_seconds),
            "poll_seconds": float(args.poll_seconds),
            "max_percent_error": float(args.max_percent_error),
            "allow_real_emx": bool(args.allow_real_emx),
        },
        "method_notes": [
            "HFSS must be run by the Windows/HFSS V66 runner before this watcher can pass.",
            "The V66 execution packet audit is a preflight gate before postrun and million planning.",
            "A WAITING_FOR_HFSS state is expected when exported HFSS `.s8p` files are absent.",
            "The million planner is invoked only after a V66 full-band postrun summary passes.",
            "This watcher does not fabricate curves, labels, S-parameters, or HFSS results.",
        ],
    }
    summary_path = out_dir / "hfss_v66_calibration_to_million_gate_watch_summary.json"
    report_path = out_dir / "HFSS_V66_CALIBRATION_TO_MILLION_GATE_WATCH_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 2 if summary["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v66-plan-summary", default=str(DEFAULT_V66_PLAN))
    parser.add_argument("--v66-postrun-script", default=str(DEFAULT_V66_POSTRUN))
    parser.add_argument("--v66-windows-runner", default=str(DEFAULT_V66_WINDOWS_RUNNER))
    parser.add_argument("--execution-packet-audit-script", default=str(SCRIPT_DIR / "audit_hfss_v66_execution_packet.py"))
    parser.add_argument("--execution-packet-audit-out-dir", default=str(DEFAULT_EXECUTION_PACKET_AUDIT_OUT))
    parser.add_argument("--skip-execution-packet-audit", action="store_true")
    parser.add_argument("--million-planner-script", default=str(SCRIPT_DIR / "run_gated_s8p_million_sample_campaign.py"))
    parser.add_argument("--campaign-out-dir", default=str(DEFAULT_CAMPAIGN_OUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_once(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = Path(args.v66_plan_summary).expanduser().resolve()
    postrun_path = Path(args.v66_postrun_script).expanduser().resolve()
    plan = _read_json(plan_path)
    audit_result = _run_execution_packet_audit(args)
    audit_summary = _read_json(Path(str(audit_result.get("summary_path") or "")))
    if not args.skip_execution_packet_audit and str(audit_summary.get("overall_status") or "") == "FAIL":
        return {
            "overall_status": "FAIL",
            "decision": "V66_EXECUTION_PACKET_AUDIT_FAILED",
            "v66_plan_summary": str(plan_path),
            "v66_postrun_script": str(postrun_path),
            "execution_packet_audit_result": audit_result,
            "execution_packet_audit_summary": audit_summary,
            "postrun_result": {},
            "variant_count": 0,
            "variant_status_counts": {},
            "variants": [],
            "selected_variant": {},
            "campaign_result": None,
            "campaign_summary_path": str(Path(args.campaign_out_dir).expanduser().resolve() / "s8p_million_sample_campaign_plan_summary.json"),
            "campaign_summary": {},
        }
    postrun_result = _run_postrun(postrun_path)
    variants = _variant_records(plan)
    variant_summaries = [_read_variant_summary(variant) for variant in variants]
    status_counts = _status_counts(variant_summaries)
    selected = _select_passing_variant(variant_summaries)

    campaign_result: dict[str, Any] | None = None
    campaign_summary_path = Path(args.campaign_out_dir).expanduser().resolve() / "s8p_million_sample_campaign_plan_summary.json"
    campaign_summary: dict[str, Any] = {}
    if selected:
        campaign_result = _run_million_planner(args, Path(selected["summary_path"]))
        campaign_summary = _read_json(campaign_summary_path)

    overall_status, decision = _decision(plan, variant_summaries, selected, campaign_summary)
    return {
        "overall_status": overall_status,
        "decision": decision,
        "v66_plan_summary": str(plan_path),
        "v66_postrun_script": str(postrun_path),
        "execution_packet_audit_result": audit_result,
        "execution_packet_audit_summary": audit_summary,
        "postrun_result": postrun_result,
        "variant_count": len(variant_summaries),
        "variant_status_counts": status_counts,
        "variants": variant_summaries,
        "selected_variant": selected or {},
        "campaign_result": campaign_result,
        "campaign_summary_path": str(campaign_summary_path),
        "campaign_summary": campaign_summary,
    }


def _run_execution_packet_audit(args: argparse.Namespace) -> dict[str, Any]:
    audit_out_dir = Path(args.execution_packet_audit_out_dir).expanduser().resolve()
    summary_path = audit_out_dir / "hfss_v66_execution_packet_audit_summary.json"
    if args.skip_execution_packet_audit:
        return {
            "returncode": 0,
            "command": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "summary_path": str(summary_path),
            "skipped": True,
        }
    script = Path(args.execution_packet_audit_script).expanduser().resolve()
    if not script.is_file():
        return {
            "returncode": 2,
            "command": [str(script)],
            "stdout_tail": "",
            "stderr_tail": f"missing {script}",
            "summary_path": str(summary_path),
            "skipped": False,
        }
    command = [
        str(Path(args.python).expanduser()),
        str(script),
        "--plan-summary",
        str(Path(args.v66_plan_summary).expanduser().resolve()),
        "--windows-runner",
        str(Path(args.v66_windows_runner).expanduser().resolve()),
        "--postrun-script",
        str(Path(args.v66_postrun_script).expanduser().resolve()),
        "--out-dir",
        str(audit_out_dir),
        "--no-fail-exit",
    ]
    result = _run_command(command, cwd=REPO_ROOT)
    result["summary_path"] = str(summary_path)
    result["skipped"] = False
    return result


def _run_postrun(postrun_path: Path) -> dict[str, Any]:
    if not postrun_path.is_file():
        return {"returncode": 2, "command": ["bash", str(postrun_path)], "stdout_tail": "", "stderr_tail": f"missing {postrun_path}"}
    return _run_command(["bash", str(postrun_path)], cwd=PROJECT_ROOT)


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


def _variant_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in plan.get("variants") or []:
        if not isinstance(item, dict):
            continue
        out_dir = Path(str(item.get("postrun_out_dir") or "")).expanduser()
        records.append(
            {
                "name": str(item.get("name") or ""),
                "postrun_out_dir": str(out_dir),
                "summary_path": str(out_dir / "s8p_hfss_postrun_validation_summary.json"),
            }
        )
    return records


def _read_variant_summary(variant: dict[str, Any]) -> dict[str, Any]:
    summary_path = Path(str(variant["summary_path"])).expanduser()
    summary = _read_json(summary_path)
    records = summary.get("records") if isinstance(summary.get("records"), list) else []
    worst = _worst_record_error(records)
    return {
        "name": variant["name"],
        "postrun_out_dir": variant["postrun_out_dir"],
        "summary_path": str(summary_path),
        "summary_exists": summary_path.is_file(),
        "overall_status": str(summary.get("overall_status") or "MISSING"),
        "decision": str(summary.get("decision") or ""),
        "frequency_grid_mode": str(summary.get("frequency_grid_mode") or ""),
        "final_acceptance_candidate": bool(summary.get("final_acceptance_candidate")),
        "sample_count": int(summary.get("sample_count") or len(records)),
        "worst_percent_error": worst,
    }


def _select_passing_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [
        item
        for item in variants
        if item.get("overall_status") == "PASS"
        and item.get("final_acceptance_candidate") is True
        and str(item.get("frequency_grid_mode")) == "final_5_60_0p5_111"
    ]
    if not passing:
        return None
    return sorted(passing, key=lambda item: (_sort_error(item.get("worst_percent_error")), str(item.get("name"))))[0]


def _decision(
    plan: dict[str, Any],
    variants: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    campaign_summary: dict[str, Any],
) -> tuple[str, str]:
    if str(plan.get("overall_status") or "") not in {"PASS", ""}:
        return "FAIL", "V66_PLAN_NOT_READY"
    campaign_status = str(campaign_summary.get("overall_status") or "")
    if campaign_status == "PASS":
        return "PASS", "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN"
    if campaign_status == "FAIL":
        return "FAIL", "MILLION_CAMPAIGN_GATE_FAILED_AFTER_V66_PASS"
    if selected:
        return "WAITING_FOR_CAMPAIGN_PLANNER", "V66_HFSS_GATE_PASSED_RUN_MILLION_PLANNER"
    if not variants:
        return "FAIL", "NO_V66_VARIANTS_FOUND"
    statuses = {str(item.get("overall_status")) for item in variants}
    if "WAITING_FOR_HFSS" in statuses or "MISSING" in statuses:
        return "WAITING_FOR_HFSS", "WAIT_FOR_V66_EXPORTED_HFSS_S8P"
    if statuses and statuses <= {"FAIL"}:
        return "FAIL", "ALL_V66_VARIANTS_FAILED_EMX_HFSS_GATE"
    return "WAITING_FOR_HFSS", "WAIT_FOR_V66_EXPORTED_HFSS_S8P"


def _status_counts(variants: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in variants:
        key = str(item.get("overall_status") or "MISSING")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _worst_record_error(records: Any) -> float | None:
    values = []
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        value = _as_float(record.get("worst_percent_error"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return max(values)


def _sort_error(value: Any) -> float:
    number = _as_float(value)
    return float("inf") if number is None else number


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


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
    selected = latest.get("selected_variant") if isinstance(latest.get("selected_variant"), dict) else {}
    lines = [
        "# HFSS V66 Calibration To Million Gate Watch",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Attempts: `{summary.get('attempt_count')}`",
        f"- V66 variants: `{latest.get('variant_count', 0)}`",
        f"- Variant status counts: `{latest.get('variant_status_counts', {})}`",
        f"- Selected variant: `{selected.get('name', '')}`",
        f"- Selected validation summary: `{selected.get('summary_path', '')}`",
        f"- Campaign summary: `{latest.get('campaign_summary_path', '')}`",
        "",
        "## Interpretation",
        "",
    ]
    if summary["overall_status"] == "PASS":
        lines.append("V66 full-band EMX/HFSS gate passed and the million-sample campaign planner is ready.")
    elif summary["overall_status"] == "FAIL":
        lines.append("V66 did not produce an acceptable full-band EMX/HFSS gate result. Do not start production EMX generation.")
    else:
        lines.append("Waiting for exported HFSS `.s8p` files or a passing V66 postrun result. Million-sample generation remains locked.")
    lines.extend(
        [
            "",
            "Acceptance remains unchanged: EMX/HFSS `.s8p` comparison must pass Lp/Ls/Q/K/Kw <= 10% before the 1,000,000-sample campaign can start.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
