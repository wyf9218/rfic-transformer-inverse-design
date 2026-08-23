#!/usr/bin/env python3
"""Watch a MARS dataset run by repeatedly running audit_mars_run_progress.py.

This is a non-simulator monitor for long EMX/Cadence dataset jobs. It records a
timestamped audit history and stops when the run reaches PASS, or when a maximum
iteration count is reached. It does not run EMX, HFSS, or ADS.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir = Path(args.run_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else run_dir / "mars_run_progress_watch"
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = out_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    history_csv = out_dir / "mars_run_progress_watch_history.csv"
    history_jsonl = out_dir / "mars_run_progress_watch_history.jsonl"
    watch_summary_path = out_dir / "mars_run_progress_watch_summary.json"

    snapshots: list[dict[str, Any]] = []
    iteration = 0
    stop_reason = "not_started"
    latest_snapshot: dict[str, Any] = {}
    try:
        while True:
            iteration += 1
            audit_out = out_dir / "latest_audit"
            completed = _run_audit(run_dir, audit_out, args)
            summary_path = audit_out / "mars_run_progress_summary.json"
            summary = _read_json(summary_path)
            latest_snapshot = _snapshot(iteration, completed, summary)
            _copy_snapshot_files(audit_out, snapshots_dir, iteration)
            _append_history_csv(history_csv, latest_snapshot)
            _append_history_jsonl(history_jsonl, latest_snapshot)
            snapshots.append(latest_snapshot)
            stop_reason = _stop_reason(latest_snapshot, iteration, args)
            _write_watch_summary(
                watch_summary_path,
                run_dir=run_dir,
                out_dir=out_dir,
                args=args,
                snapshots=snapshots,
                latest_snapshot=latest_snapshot,
                stop_reason=stop_reason,
            )
            _print_snapshot(latest_snapshot, stop_reason)
            if stop_reason != "continue":
                break
            if args.interval_sec > 0:
                time.sleep(float(args.interval_sec))
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        _write_watch_summary(
            watch_summary_path,
            run_dir=run_dir,
            out_dir=out_dir,
            args=args,
            snapshots=snapshots,
            latest_snapshot=latest_snapshot,
            stop_reason=stop_reason,
        )
        print("watch interrupted by user")
        return 130 if not args.no_fail_exit else 0

    final_status = latest_snapshot.get("overall_status")
    if final_status == "PASS" or args.no_fail_exit:
        return 0
    return 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--interval-sec", type=float, default=300.0)
    parser.add_argument("--max-iterations", type=int, default=0, help="0 means run until PASS or interruption")
    parser.add_argument("--stop-on-pass", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-frequency-checks", type=int, default=25)
    parser.add_argument("--touchstone-seed", type=int, default=20260613)
    parser.add_argument("--require-emx-command", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--expected-port-mode")
    parser.add_argument("--expected-pin-purpose", type=int)
    parser.add_argument("--require-gds", action="store_true")
    parser.add_argument("--require-layout-preview", action="store_true")
    parser.add_argument("--require-clearance-audit", action="store_true")
    parser.add_argument("--require-geometry-quality", action="store_true")
    parser.add_argument("--internal-angle-deg", type=float, default=135.0)
    parser.add_argument("--terminal-angle-deg", type=float, default=90.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_audit(run_dir: Path, audit_out: Path, args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "audit_mars_run_progress.py"),
        str(run_dir),
        "--out-dir",
        str(audit_out),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-touchstone-frequency-checks",
        str(args.max_touchstone_frequency_checks),
        "--touchstone-seed",
        str(args.touchstone_seed),
        "--no-fail-exit",
    ]
    _append_optional(cmd, "--expected-count", args.expected_count)
    _append_optional(cmd, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
    _append_optional(cmd, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
    _append_optional(cmd, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
    _append_optional(cmd, "--expected-frequency-points", args.expected_frequency_points)
    _append_optional(cmd, "--expected-port-mode", args.expected_port_mode)
    _append_optional(cmd, "--expected-pin-purpose", args.expected_pin_purpose)
    if args.require_emx_command:
        cmd.append("--require-emx-command")
    if args.require_gds:
        cmd.append("--require-gds")
    if args.require_layout_preview:
        cmd.append("--require-layout-preview")
    if args.require_clearance_audit:
        cmd.append("--require-clearance-audit")
    if args.require_geometry_quality:
        cmd.extend(
            [
                "--require-geometry-quality",
                "--internal-angle-deg",
                str(args.internal_angle_deg),
                "--terminal-angle-deg",
                str(args.terminal_angle_deg),
            ]
        )
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def _append_optional(cmd: list[str], flag: str, value: Any) -> None:
    if value is not None:
        cmd.extend([flag, str(value)])


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _snapshot(iteration: int, completed: subprocess.CompletedProcess[str], summary: dict[str, Any]) -> dict[str, Any]:
    checks = summary.get("checks") if isinstance(summary.get("checks"), list) else []
    failed_checks = [
        str(item.get("name", "unknown"))
        for item in checks
        if isinstance(item, dict) and item.get("status") != "PASS"
    ]
    rows = summary.get("rows") if isinstance(summary.get("rows"), dict) else {}
    evaluations = summary.get("evaluations") if isinstance(summary.get("evaluations"), dict) else {}
    touchstone = summary.get("touchstone_frequency_checks") if isinstance(summary.get("touchstone_frequency_checks"), dict) else {}
    emx_command = summary.get("emx_command_checks") if isinstance(summary.get("emx_command_checks"), dict) else {}
    clearance = summary.get("clearance_audit") if isinstance(summary.get("clearance_audit"), dict) else {}
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iteration": iteration,
        "audit_returncode": completed.returncode,
        "overall_status": summary.get("overall_status", "UNKNOWN"),
        "row_count": rows.get("row_count"),
        "ok_count": rows.get("ok_count"),
        "fail_count": rows.get("fail_count"),
        "required_count": evaluations.get("required_count"),
        "summary_ok_count": evaluations.get("summary_ok_count"),
        "touchstone_file_count": evaluations.get("touchstone_file_count"),
        "emx_command_file_count": evaluations.get("emx_command_file_count"),
        "layout_json_file_count": evaluations.get("layout_json_file_count"),
        "touchstone_checked_count": touchstone.get("checked_count"),
        "touchstone_fail_count": touchstone.get("fail_count"),
        "emx_command_checked_count": emx_command.get("checked_count"),
        "emx_command_fail_count": emx_command.get("fail_count"),
        "clearance_candidate_count": clearance.get("candidate_count"),
        "clearance_pass_count": clearance.get("pass_count"),
        "clearance_reject_count": clearance.get("reject_count"),
        "clearance_missing_or_other_count": clearance.get("missing_or_other_count"),
        "failed_checks": failed_checks,
    }


def _copy_snapshot_files(audit_out: Path, snapshots_dir: Path, iteration: int) -> None:
    for name in ("mars_run_progress_summary.json", "mars_run_progress_report.md", "mars_run_progress_rows.csv"):
        source = audit_out / name
        if source.exists():
            target = snapshots_dir / f"iteration_{iteration:06d}_{name}"
            shutil.copyfile(source, target)


def _append_history_csv(path: Path, snapshot: dict[str, Any]) -> None:
    fields = [
        "generated_utc",
        "iteration",
        "audit_returncode",
        "overall_status",
        "row_count",
        "ok_count",
        "fail_count",
        "required_count",
        "summary_ok_count",
        "touchstone_file_count",
        "emx_command_file_count",
        "layout_json_file_count",
        "touchstone_checked_count",
        "touchstone_fail_count",
        "emx_command_checked_count",
        "emx_command_fail_count",
        "clearance_candidate_count",
        "clearance_pass_count",
        "clearance_reject_count",
        "clearance_missing_or_other_count",
        "failed_checks",
    ]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        row = dict(snapshot)
        row["failed_checks"] = " | ".join(snapshot.get("failed_checks", []))
        writer.writerow({field: row.get(field, "") for field in fields})


def _append_history_jsonl(path: Path, snapshot: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True) + "\n")


def _stop_reason(snapshot: dict[str, Any], iteration: int, args: argparse.Namespace) -> str:
    if args.stop_on_pass and snapshot.get("overall_status") == "PASS":
        return "pass"
    if args.max_iterations and iteration >= args.max_iterations:
        return "max_iterations"
    return "continue"


def _write_watch_summary(
    path: Path,
    *,
    run_dir: Path,
    out_dir: Path,
    args: argparse.Namespace,
    snapshots: list[dict[str, Any]],
    latest_snapshot: dict[str, Any],
    stop_reason: str,
) -> None:
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "overall_status": latest_snapshot.get("overall_status", "UNKNOWN"),
        "stop_reason": stop_reason,
        "iteration_count": len(snapshots),
        "latest_snapshot": latest_snapshot,
        "arguments": {
            "interval_sec": args.interval_sec,
            "max_iterations": args.max_iterations,
            "stop_on_pass": args.stop_on_pass,
            "expected_count": args.expected_count,
            "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
            "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
            "require_emx_command": args.require_emx_command,
            "expected_port_mode": args.expected_port_mode,
            "expected_pin_purpose": args.expected_pin_purpose,
            "require_clearance_audit": args.require_clearance_audit,
        },
        "history_csv": str(out_dir / "mars_run_progress_watch_history.csv"),
        "history_jsonl": str(out_dir / "mars_run_progress_watch_history.jsonl"),
        "latest_audit_dir": str(out_dir / "latest_audit"),
        "snapshots_dir": str(out_dir / "snapshots"),
        "limitations": [
            "This watcher repeatedly audits filesystem evidence only.",
            "PASS does not prove EMX/HFSS/ADS physical agreement.",
            "Keep the watch history with the dataset package for progress traceability.",
        ],
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _print_snapshot(snapshot: dict[str, Any], stop_reason: str) -> None:
    print(
        " ".join(
            [
                f"iteration={snapshot.get('iteration')}",
                f"status={snapshot.get('overall_status')}",
                f"ok={snapshot.get('ok_count')}",
                f"touchstone={snapshot.get('touchstone_file_count')}",
                f"emx_command={snapshot.get('emx_command_file_count')}",
                f"clearance={snapshot.get('clearance_candidate_count')}",
                f"failed={len(snapshot.get('failed_checks', []))}",
                f"stop_reason={stop_reason}",
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
