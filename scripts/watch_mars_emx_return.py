#!/usr/bin/env python3
"""Watch for a MARS-returned target EMX S4P package.

This local-side watcher repeatedly runs discover_and_verify_mars_emx_return.py,
records a timestamped history, and stops when the returned EMX package is
accepted. It does not run EMX, HFSS, or ADS.
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
DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_SEARCH_ROOT = (
    DEFAULT_PROJECT_ROOT
    / "hfss_validation"
    / "final500_ec6698dfc575950b"
    / "target_emx_postrun_download_20260613"
)
DEFAULT_OUT_DIR = (
    DEFAULT_PROJECT_ROOT
    / "hfss_validation"
    / "final500_ec6698dfc575950b"
    / "mars_emx_return_watch_20260614"
)
DEFAULT_EXPECTED_SAMPLE_ID = "ec6698dfc575950b"
# Contract note: discover_and_verify_mars_emx_return.py records sample_status
# and uses _sample_id_failures to reject returned files that lack the expected sample id before watcher history can mark an EMX reference accepted.


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir = out_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    history_csv = out_dir / "mars_emx_return_watch_history.csv"
    history_jsonl = out_dir / "mars_emx_return_watch_history.jsonl"
    watch_summary_path = out_dir / "mars_emx_return_watch_summary.json"

    snapshots: list[dict[str, Any]] = []
    latest_snapshot: dict[str, Any] = {}
    stop_reason = "not_started"
    iteration = 0
    try:
        while True:
            iteration += 1
            discovery_out = out_dir / "latest_discovery"
            completed = _run_discovery(discovery_out, args)
            summary_path = discovery_out / "mars_emx_return_discovery_summary.json"
            summary = _read_json(summary_path)
            latest_snapshot = _snapshot(iteration, completed, summary)
            _copy_snapshot_files(discovery_out, snapshots_dir, iteration)
            _append_history_csv(history_csv, latest_snapshot)
            _append_history_jsonl(history_jsonl, latest_snapshot)
            snapshots.append(latest_snapshot)
            stop_reason = _stop_reason(latest_snapshot, iteration, args)
            _write_watch_summary(
                watch_summary_path,
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
    if args.stop_on_ready_to_verify and final_status == "READY_TO_VERIFY":
        return 0
    return 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", action="append", default=None, help="Directory to scan; may be supplied more than once")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--interval-sec", type=float, default=300.0)
    parser.add_argument("--max-iterations", type=int, default=0, help="0 means run until PASS or interruption")
    parser.add_argument("--stop-on-pass", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-on-ready-to-verify", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--tarball-pattern", action="append")
    parser.add_argument("--s4p-pattern", action="append")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=50.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.1)
    parser.add_argument("--expected-frequency-points", type=int, default=451)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--expected-sample-id", default=DEFAULT_EXPECTED_SAMPLE_ID)
    parser.add_argument("--python", default=sys.executable, help="Python executable used to run the discovery helper")
    parser.add_argument("--repo-root", default=str(SCRIPT_DIR.parents[0]))
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to the discovery helper")
    parser.add_argument("--skip-verifier", action="store_true", help="Pass --skip-verifier to the discovery helper")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _run_discovery(discovery_out: Path, args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    cmd = [
        args.python,
        str(SCRIPT_DIR / "discover_and_verify_mars_emx_return.py"),
        "--out-dir",
        str(discovery_out),
        "--expected-ports",
        str(args.expected_ports),
        "--expected-frequency-start-ghz",
        str(args.expected_frequency_start_ghz),
        "--expected-frequency-stop-ghz",
        str(args.expected_frequency_stop_ghz),
        "--expected-frequency-step-ghz",
        str(args.expected_frequency_step_ghz),
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--expected-sample-id",
        str(args.expected_sample_id),
        "--repo-root",
        str(args.repo_root),
        "--no-fail-exit",
    ]
    for root in args.search_root or [str(DEFAULT_SEARCH_ROOT)]:
        cmd.extend(["--search-root", str(root)])
    for pattern in args.tarball_pattern or []:
        cmd.extend(["--tarball-pattern", str(pattern)])
    for pattern in args.s4p_pattern or []:
        cmd.extend(["--s4p-pattern", str(pattern)])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.skip_verifier:
        cmd.append("--skip-verifier")
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


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
        if isinstance(item, dict) and item.get("status") == "FAIL"
    ]
    warning_checks = [
        str(item.get("name", "unknown"))
        for item in checks
        if isinstance(item, dict) and item.get("status") == "WARN"
    ]
    selected = summary.get("selected") if isinstance(summary.get("selected"), dict) else {}
    selected_tarball = selected.get("tarball") if isinstance(selected.get("tarball"), dict) else {}
    selected_s4p = selected.get("emx_s4p") if isinstance(selected.get("emx_s4p"), dict) else {}
    verifier_result = summary.get("verifier_result") if isinstance(summary.get("verifier_result"), dict) else {}
    verifier_summary = verifier_result.get("summary") if isinstance(verifier_result.get("summary"), dict) else {}
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iteration": iteration,
        "discovery_returncode": completed.returncode,
        "overall_status": summary.get("overall_status", "UNKNOWN"),
        "decision": summary.get("decision"),
        "selected_tarball": selected_tarball.get("path"),
        "selected_tarball_status": selected_tarball.get("status"),
        "selected_emx_s4p": selected_s4p.get("path"),
        "selected_emx_s4p_status": selected_s4p.get("status"),
        "tarball_candidate_count": len(summary.get("tarball_candidates", []) or []),
        "s4p_candidate_count": len(summary.get("s4p_candidates", []) or []),
        "verifier_returncode": verifier_result.get("returncode"),
        "verifier_decision": verifier_summary.get("decision"),
        "status_counts": summary.get("status_counts", {}),
        "failed_checks": failed_checks,
        "warning_checks": warning_checks,
    }


def _copy_snapshot_files(discovery_out: Path, snapshots_dir: Path, iteration: int) -> None:
    for name in ("mars_emx_return_discovery_summary.json", "MARS_EMX_RETURN_DISCOVERY_REPORT.md"):
        source = discovery_out / name
        if source.exists():
            target = snapshots_dir / f"iteration_{iteration:06d}_{name}"
            shutil.copyfile(source, target)
    verifier_summary = discovery_out / "target_emx_postrun_import" / "target_emx_postrun_import_summary.json"
    if verifier_summary.exists():
        target = snapshots_dir / f"iteration_{iteration:06d}_target_emx_postrun_import_summary.json"
        shutil.copyfile(verifier_summary, target)


def _append_history_csv(path: Path, snapshot: dict[str, Any]) -> None:
    fields = [
        "generated_utc",
        "iteration",
        "discovery_returncode",
        "overall_status",
        "decision",
        "selected_tarball",
        "selected_tarball_status",
        "selected_emx_s4p",
        "selected_emx_s4p_status",
        "tarball_candidate_count",
        "s4p_candidate_count",
        "verifier_returncode",
        "verifier_decision",
        "failed_checks",
        "warning_checks",
    ]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        row = dict(snapshot)
        row["failed_checks"] = " | ".join(snapshot.get("failed_checks", []))
        row["warning_checks"] = " | ".join(snapshot.get("warning_checks", []))
        writer.writerow({field: row.get(field, "") for field in fields})


def _append_history_jsonl(path: Path, snapshot: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True) + "\n")


def _stop_reason(snapshot: dict[str, Any], iteration: int, args: argparse.Namespace) -> str:
    if args.stop_on_pass and snapshot.get("overall_status") == "PASS":
        return "pass"
    if args.stop_on_ready_to_verify and snapshot.get("overall_status") == "READY_TO_VERIFY":
        return "ready_to_verify"
    if args.max_iterations and iteration >= args.max_iterations:
        return "max_iterations"
    return "continue"


def _write_watch_summary(
    path: Path,
    *,
    out_dir: Path,
    args: argparse.Namespace,
    snapshots: list[dict[str, Any]],
    latest_snapshot: dict[str, Any],
    stop_reason: str,
) -> None:
    overall_status = str(latest_snapshot.get("overall_status", "UNKNOWN"))
    decision = latest_snapshot.get("decision")
    accepted_emx_reference = overall_status == "PASS" and decision == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS"
    evidence_use = "ACCEPTED_EMX_REFERENCE_FOR_HFSS_INPUT" if accepted_emx_reference else "NOT_ACCEPTED_EMX_REFERENCE"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "search_roots": args.search_root or [str(DEFAULT_SEARCH_ROOT)],
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "decision": decision,
        "evidence_use": evidence_use,
        "accepted_emx_reference": accepted_emx_reference,
        "hfss_comparison_allowed": accepted_emx_reference,
        "s4p_candidate_count": int(latest_snapshot.get("s4p_candidate_count") or 0),
        "tarball_candidate_count": int(latest_snapshot.get("tarball_candidate_count") or 0),
        "selected_emx_s4p": latest_snapshot.get("selected_emx_s4p"),
        "selected_tarball": latest_snapshot.get("selected_tarball"),
        "verifier_decision": latest_snapshot.get("verifier_decision"),
        "next_required_action": (
            "RUN_ACCEPTED_EMX_HFSS_ADS_VALIDATION"
            if accepted_emx_reference
            else "WAIT_FOR_AND_IMPORT_MARS_WIDEBAND_EMX_RETURN"
        ),
        "stop_reason": stop_reason,
        "iteration_count": len(snapshots),
        "latest_snapshot": latest_snapshot,
        "arguments": {
            "interval_sec": args.interval_sec,
            "max_iterations": args.max_iterations,
            "stop_on_pass": args.stop_on_pass,
            "stop_on_ready_to_verify": args.stop_on_ready_to_verify,
            "expected_ports": args.expected_ports,
            "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
            "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
            "dry_run": args.dry_run,
            "skip_verifier": args.skip_verifier,
        },
        "history_csv": str(out_dir / "mars_emx_return_watch_history.csv"),
        "history_jsonl": str(out_dir / "mars_emx_return_watch_history.jsonl"),
        "latest_discovery_dir": str(out_dir / "latest_discovery"),
        "snapshots_dir": str(out_dir / "snapshots"),
        "limitations": [
            "This watcher repeatedly runs the local discovery/import gate only.",
            "Expected waiting decision is WAITING_FOR_MARS_RETURN until a returned S4P package is found.",
            "Accepted verifier decision is ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS.",
            "WAITING_FOR_MARS_RETURN is not an accepted EMX reference and must not enable HFSS comparison.",
            "PASS means the MARS-returned EMX package was accepted as a local golden EMX reference for HFSS comparison.",
            "PASS still does not prove HFSS-vs-EMX agreement; run run_accepted_emx_hfss_ads_validation.py next.",
            "Keep the watch history and snapshots with the project report for traceability.",
        ],
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _print_snapshot(snapshot: dict[str, Any], stop_reason: str) -> None:
    print(
        " ".join(
            [
                f"iteration={snapshot.get('iteration')}",
                f"status={snapshot.get('overall_status')}",
                f"decision={snapshot.get('decision')}",
                f"s4p_candidates={snapshot.get('s4p_candidate_count')}",
                f"tarballs={snapshot.get('tarball_candidate_count')}",
                f"verifier={snapshot.get('verifier_decision')}",
                f"failed={len(snapshot.get('failed_checks', []))}",
                f"warnings={len(snapshot.get('warning_checks', []))}",
                f"stop_reason={stop_reason}",
            ]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
