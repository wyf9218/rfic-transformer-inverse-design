#!/usr/bin/env python3
"""Estimate large S4P EMX campaign runtime from parallel-run summaries."""

from __future__ import annotations

import argparse
import glob
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summaries = _summary_paths(args)
    records = [_record_from_summary(path, args) for path in summaries]
    usable = [record for record in records if record["usable"]]
    best = max(usable, key=lambda item: item["rows_per_second_effective"]) if usable else None
    target_count = int(args.target_count)
    estimate = _estimate(best, target_count)
    checks = _checks(records, usable, best, args)
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_BEST_PARALLEL_SETTING_FOR_MILLION_RUN" if status == "PASS" else "DO_NOT_SCALE_FROM_THESE_TIMINGS",
        "target_count": target_count,
        "require_real_emx": not bool(args.allow_create_only),
        "input_summary_count": len(records),
        "usable_summary_count": len(usable),
        "best_record": best,
        "estimate": estimate,
        "records": records,
        "checks": checks,
        "limitations": [
            "Use real EMX summaries for final production estimates; create-only timing only measures Python/layout overhead.",
            "Recheck the estimate after the first production chunk because EMX license contention and filesystem load can change throughput.",
            "Do not start the million-sample campaign unless the DRC gate and random EMX/HFSS validation gate both remain acceptable.",
        ],
    }
    summary_path = out_dir / "mars_s4p_runtime_estimate_summary.json"
    report_path = out_dir / "mars_s4p_runtime_estimate_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    if best is not None:
        print(f"best_jobs={best['jobs_requested']}")
        print(f"best_rows_per_second={best['rows_per_second_effective']}")
        print(f"target_days={estimate['target_days']}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="*", help="parallel_candidate_queue_dataset_summary.json files")
    parser.add_argument("--summary-glob", action="append", default=[], help="Glob for summary JSON files")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-count", type=int, default=1_000_000)
    parser.add_argument("--min-usable-runs", type=int, default=1)
    parser.add_argument("--allow-create-only", action="store_true", help="Allow create-only timing for smoke testing the estimator")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _summary_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for raw in args.summaries:
        paths.append(Path(raw).expanduser().resolve())
    for pattern in args.summary_glob:
        for raw in glob.glob(str(Path(pattern).expanduser()), recursive=True):
            paths.append(Path(raw).expanduser().resolve())
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def _record_from_summary(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    record: dict[str, Any] = {
        "summary_path": str(path),
        "exists": path.is_file(),
        "usable": False,
        "reject_reasons": [],
    }
    if not path.is_file():
        record["reject_reasons"].append("summary file does not exist")
        return record
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        record["reject_reasons"].append(f"summary JSON parse failed: {exc}")
        return record
    jobs = _as_int(raw.get("jobs_requested"))
    rows = _as_int(raw.get("merged_row_count"))
    elapsed = _as_float(raw.get("elapsed_seconds"))
    rows_per_second = _as_float(raw.get("rows_per_second_effective"))
    seconds_per_row = _as_float(raw.get("seconds_per_row_effective"))
    if rows_per_second is None and rows is not None and elapsed and elapsed > 0.0:
        rows_per_second = float(rows) / float(elapsed)
    if seconds_per_row is None and rows is not None and elapsed is not None and rows > 0:
        seconds_per_row = float(elapsed) / float(rows)
    touchstone = raw.get("touchstone_output_contract") if isinstance(raw.get("touchstone_output_contract"), dict) else {}
    expected_frequency = touchstone.get("expected_frequency") if isinstance(touchstone.get("expected_frequency"), dict) else {}
    record.update(
        {
            "overall_status": raw.get("overall_status"),
            "decision": raw.get("decision"),
            "run_emx": bool(raw.get("run_emx")),
            "create_only": bool(raw.get("create_only")),
            "jobs_requested": jobs,
            "shard_count": _as_int(raw.get("shard_count")),
            "merged_row_count": rows,
            "elapsed_seconds": elapsed,
            "seconds_per_row_effective": seconds_per_row,
            "rows_per_second_effective": rows_per_second,
            "touchstone_expected_extension": touchstone.get("expected_extension"),
            "touchstone_expected_ports": touchstone.get("expected_ports"),
            "frequency_start_ghz": expected_frequency.get("start_ghz"),
            "frequency_stop_ghz": expected_frequency.get("stop_ghz"),
            "frequency_step_ghz": expected_frequency.get("step_ghz"),
            "frequency_points": expected_frequency.get("points"),
        }
    )
    if raw.get("overall_status") != "PASS":
        record["reject_reasons"].append(f"overall_status={raw.get('overall_status')}")
    if not bool(args.allow_create_only) and not bool(raw.get("run_emx")):
        record["reject_reasons"].append("not a real EMX run")
    if rows is None or rows <= 0:
        record["reject_reasons"].append(f"merged_row_count={rows}")
    if elapsed is None or elapsed <= 0.0:
        record["reject_reasons"].append(f"elapsed_seconds={elapsed}")
    if rows_per_second is None or rows_per_second <= 0.0:
        record["reject_reasons"].append(f"rows_per_second_effective={rows_per_second}")
    if not record["reject_reasons"]:
        record["usable"] = True
    return record


def _estimate(best: dict[str, Any] | None, target_count: int) -> dict[str, Any]:
    if best is None:
        return {
            "available": False,
            "target_count": target_count,
            "reason": "no usable timing record",
        }
    rows_per_second = float(best["rows_per_second_effective"])
    seconds = float(target_count) / rows_per_second
    return {
        "available": True,
        "target_count": target_count,
        "best_jobs_requested": best["jobs_requested"],
        "best_rows_per_second": rows_per_second,
        "best_seconds_per_row": best["seconds_per_row_effective"],
        "target_seconds": seconds,
        "target_hours": seconds / 3600.0,
        "target_days": seconds / 86400.0,
    }


def _checks(records: list[dict[str, Any]], usable: list[dict[str, Any]], best: dict[str, Any] | None, args: argparse.Namespace) -> list[dict[str, Any]]:
    return [
        _check("summary_inputs_present", bool(records), f"input_summary_count={len(records)}"),
        _check(
            "minimum_usable_runs_met",
            len(usable) >= int(args.min_usable_runs),
            f"usable={len(usable)}, required={args.min_usable_runs}",
        ),
        _check("best_record_selected", best is not None, "best timing record exists"),
    ]


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS S4P Runtime Estimate",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Target count: `{summary['target_count']}`",
        f"- Require real EMX: `{summary['require_real_emx']}`",
        "",
    ]
    estimate = summary["estimate"]
    if estimate.get("available"):
        lines.extend(
            [
                "## Best Timing",
                "",
                f"- Jobs: `{estimate['best_jobs_requested']}`",
                f"- Rows/s: `{estimate['best_rows_per_second']:.6g}`",
                f"- Seconds/row: `{estimate['best_seconds_per_row']:.6g}`",
                f"- Estimated hours for target: `{estimate['target_hours']:.3f}`",
                f"- Estimated days for target: `{estimate['target_days']:.3f}`",
                "",
            ]
        )
    else:
        lines.extend(["## Best Timing", "", f"- Not available: {estimate.get('reason')}", ""])
    lines.extend(
        [
            "## Timing Records",
            "",
            "| Summary | Usable | Jobs | Rows | Elapsed s | Rows/s | Reject reasons |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for record in summary["records"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(Path(record["summary_path"]).name),
                    str(record.get("usable")),
                    _fmt(record.get("jobs_requested")),
                    _fmt(record.get("merged_row_count")),
                    _fmt(record.get("elapsed_seconds")),
                    _fmt(record.get("rows_per_second_effective")),
                    _cell("; ".join(record.get("reject_reasons") or [])),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return "" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
