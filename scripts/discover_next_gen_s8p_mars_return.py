#!/usr/bin/env python3
"""Discover a returned next-generation S8P MARS run and verify its status.

This local helper is intentionally read-only. It scans candidate directories
for the new 8-port ``dataset_rows.csv`` / ``dataset_manifest.json`` layout,
selects the strongest candidate, then dispatches
``summarize_next_gen_s8p_mars_run.py`` so the same 500-row, 8-worker, EMX,
manifest, and postrun evidence gates are used everywhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_ROOT = Path("/home/researcher/Documents/模拟变压器AI反向建模")
DEFAULT_OUT_DIR = DEFAULT_PROJECT_ROOT / "outputs" / "next_gen_s8p_mars_return_discovery_current"
DEFAULT_SEARCH_ROOTS = (
    DEFAULT_PROJECT_ROOT / "outputs",
    DEFAULT_PROJECT_ROOT / "hfss_validation",
    DEFAULT_PROJECT_ROOT / "rfic-transformer-inverse-design",
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    roots = _search_roots(args.search_root)
    candidates = _discover_run_candidates(roots, args)
    selected = _select_candidate(candidates)
    checks = _root_checks(roots)
    checks.append(_selection_check(selected, candidates))

    run_status_result: dict[str, Any] | None = None
    if selected:
        if args.dry_run:
            checks.append(Check("WARN", "strict S8P run summarizer", "not run because --dry-run was supplied"))
        else:
            run_status_result = _run_summarizer(Path(str(selected["run_dir"])), out_dir, args)
            checks.append(_summarizer_check(run_status_result))
    else:
        checks.append(Check("WARN", "strict S8P run summarizer", "not run because no candidate run directory was selected"))

    overall_status, decision = _overall_decision(selected, run_status_result, args)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "search_roots": [str(root) for root in roots],
        "selected_candidate": selected,
        "run_status_result": run_status_result,
        "candidate_count": len(candidates),
        "candidates": candidates[: int(args.max_candidates_reported)],
        "checks": [check.as_dict() for check in checks],
        "status_counts": _status_counts(checks),
        "requirements": {
            "expected_count": int(args.expected_count),
            "expected_jobs": int(args.expected_jobs),
            "expected_ports": int(args.expected_ports),
            "expected_frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "expected_frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "expected_frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "expected_frequency_points": int(args.expected_frequency_points),
        },
        "method_notes": [
            "This script never runs Cadence, EMX, HFSS, ADS, or a browser session.",
            "A discovered directory is not accepted directly; it is passed through summarize_next_gen_s8p_mars_run.py.",
            "The strict summary must prove .s8p extension, 8 ports, 5-60 GHz / 1.0 GHz / 56-point grid, EMX provenance, and the approved S8P topology manifest.",
            "HFSS comparison and final report figures remain separate gates after the real EMX dataset is accepted.",
        ],
    }
    summary_path = out_dir / "next_gen_s8p_mars_return_discovery_summary.json"
    report_path = out_dir / "NEXT_GEN_S8P_MARS_RETURN_DISCOVERY_REPORT.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:7s} {check.name}: {check.detail}")
    if run_status_result and run_status_result.get("summary_path"):
        print(f"run_status_summary={run_status_result.get('summary_path')}")
    return 0 if overall_status in {"PASS", "WAITING_FOR_RETURN", "READY_FOR_NEXT_GATES"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-root", action="append", help="Directory to scan; may be supplied more than once")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--expected-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-touchstone-checks", type=int, default=500)
    parser.add_argument("--max-candidates-reported", type=int, default=25)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _search_roots(raw_roots: list[str] | None) -> list[Path]:
    roots = raw_roots or [str(root) for root in DEFAULT_SEARCH_ROOTS]
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw in roots:
        path = Path(raw).expanduser().resolve()
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    return resolved


def _root_checks(roots: list[Path]) -> list[Check]:
    return [
        Check("PASS" if root.is_dir() else "WARN", "search root", str(root) if root.is_dir() else f"missing: {root}")
        for root in roots
    ]


def _discover_run_candidates(roots: list[Path], args: argparse.Namespace) -> list[dict[str, Any]]:
    candidates: dict[Path, dict[str, Any]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for rows_path in root.rglob("dataset_rows.csv"):
            if not rows_path.is_file():
                continue
            run_dir = rows_path.parent.resolve()
            candidates[run_dir] = _candidate_record(run_dir, args)
    return sorted(
        candidates.values(),
        key=lambda item: (
            item["status"] != "PASS",
            -int(item.get("ok_count") or 0),
            -int(item.get("s8p_count") or 0),
            -float(item.get("mtime") or 0.0),
            str(item.get("run_dir") or ""),
        ),
    )


def _candidate_record(run_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    rows = _read_rows(run_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    manifest = run_dir / "dataset_manifest.json"
    parallel_summary = run_dir / "parallel_candidate_queue_dataset_summary.json"
    s8p_paths = [path for path in run_dir.rglob("*.s8p") if path.is_file()]
    reasons = []
    if len(rows) != int(args.expected_count):
        reasons.append(f"row_count expected {args.expected_count}, got {len(rows)}")
    if len(ok_rows) != int(args.expected_count):
        reasons.append(f"ok_count expected {args.expected_count}, got {len(ok_rows)}")
    if len(s8p_paths) < int(args.expected_count):
        reasons.append(f"s8p_count expected at least {args.expected_count}, got {len(s8p_paths)}")
    if not manifest.is_file():
        reasons.append("dataset_manifest.json missing")
    if not parallel_summary.is_file():
        reasons.append("parallel_candidate_queue_dataset_summary.json missing")
    return {
        "run_dir": str(run_dir),
        "status": "PASS" if not reasons else "FAIL",
        "row_count": len(rows),
        "ok_count": len(ok_rows),
        "s8p_count": len(s8p_paths),
        "dataset_manifest": str(manifest) if manifest.is_file() else None,
        "parallel_summary": str(parallel_summary) if parallel_summary.is_file() else None,
        "mtime": _candidate_mtime(run_dir, [run_dir / "dataset_rows.csv", manifest, parallel_summary, *s8p_paths[:20]]),
        "reasons": reasons,
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:  # noqa: BLE001 - candidate details are recorded by the strict summarizer later.
        return []


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "nan"}


def _candidate_mtime(run_dir: Path, paths: list[Path]) -> float:
    mtimes = [run_dir.stat().st_mtime] if run_dir.exists() else [0.0]
    for path in paths:
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
    return max(mtimes)


def _select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [item for item in candidates if item.get("status") == "PASS"]
    return passing[0] if passing else (candidates[0] if candidates else None)


def _selection_check(selected: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> Check:
    if not selected:
        return Check("WARN", "selected S8P MARS run", "no dataset_rows.csv candidate found")
    status = "PASS" if selected.get("status") == "PASS" else "WARN"
    return Check(
        status,
        "selected S8P MARS run",
        f"{selected.get('run_dir')} status={selected.get('status')} reasons={selected.get('reasons')} candidates={len(candidates)}",
    )


def _run_summarizer(run_dir: Path, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    quality_dir = run_dir / "dataset_quality_gates_s8p_physical_feature"
    status_dir = out_dir / "selected_run_status"
    command = [
        str(Path(args.python).expanduser()),
        str(SCRIPT_DIR / "summarize_next_gen_s8p_mars_run.py"),
        "--run-dir",
        str(run_dir),
        "--quality-dir",
        str(quality_dir),
        "--out-dir",
        str(status_dir),
        "--expected-count",
        str(args.expected_count),
        "--expected-jobs",
        str(args.expected_jobs),
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
        "--max-touchstone-checks",
        str(args.max_touchstone_checks),
        "--no-fail-exit",
    ]
    completed = subprocess.run(command, cwd=SCRIPT_DIR.parents[0], text=True, capture_output=True, check=False)
    summary_path = status_dir / "next_gen_s8p_mars_run_status_summary.json"
    summary = _read_json(summary_path)
    return {
        "returncode": completed.returncode,
        "command": command,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_json_error": True}
    return payload if isinstance(payload, dict) else {}


def _summarizer_check(result: dict[str, Any]) -> Check:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    status = summary.get("overall_status")
    decision = summary.get("decision")
    if status and status != "NOT_READY":
        return Check("PASS", "strict S8P run summarizer", f"overall_status={status}, decision={decision}")
    return Check(
        "FAIL",
        "strict S8P run summarizer",
        f"returncode={result.get('returncode')}, overall_status={status}, decision={decision}, summary={result.get('summary_path')}",
    )


def _overall_decision(
    selected: dict[str, Any] | None,
    run_status_result: dict[str, Any] | None,
    args: argparse.Namespace,
) -> tuple[str, str]:
    if not selected:
        return "WAITING_FOR_RETURN", "WAIT_FOR_NEXT_GEN_S8P_MARS_RETURN"
    if args.dry_run:
        return "READY_TO_VERIFY", "RUN_STRICT_S8P_MARS_RUN_SUMMARY"
    summary = (run_status_result or {}).get("summary") if isinstance((run_status_result or {}).get("summary"), dict) else {}
    status = str(summary.get("overall_status") or "")
    if status == "PASS":
        return "PASS", "ACCEPT_VERIFIED_NEXT_GEN_S8P_RUN"
    if status and status != "NOT_READY":
        return "READY_FOR_NEXT_GATES", str(summary.get("decision") or "CONTINUE_NEXT_GEN_S8P_GATES")
    if selected.get("status") == "PASS":
        return "FAIL", "FIX_STRICT_S8P_RUN_SUMMARY_FAILURE"
    return "WAITING_FOR_RETURN", "WAIT_FOR_COMPLETE_NEXT_GEN_S8P_MARS_RETURN"


def _status_counts(checks: list[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return dict(sorted(counts.items()))


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P MARS Return Discovery",
        "",
        f"- overall_status: `{summary['overall_status']}`",
        f"- decision: `{summary['decision']}`",
        f"- candidate_count: `{summary['candidate_count']}`",
        "",
        "## Selected Candidate",
        "",
    ]
    selected = summary.get("selected_candidate") or {}
    if selected:
        for key in ("run_dir", "status", "row_count", "ok_count", "s8p_count", "dataset_manifest", "parallel_summary", "reasons"):
            lines.append(f"- {key}: `{selected.get(key)}`")
    else:
        lines.append("- NOT_SELECTED")
    result = summary.get("run_status_result") or {}
    run_status = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if run_status:
        lines.extend(
            [
                "",
                "## Strict Run Status",
                "",
                f"- summary: `{result.get('summary_path')}`",
                f"- overall_status: `{run_status.get('overall_status')}`",
                f"- decision: `{run_status.get('decision')}`",
            ]
        )
    lines.extend(["", "## Checks", ""])
    for check in summary.get("checks", []):
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Method Notes", ""])
    for note in summary.get("method_notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
