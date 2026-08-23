#!/usr/bin/env python3
"""Execute a gated 1M MARS56 grounded-S4P campaign from a chunk manifest.

The manifest-driven design keeps acquisition separate from production:
candidate queues must already be generated from the physical-feature balancing
workflow.  This executor simply runs each 100k queue through the S4P chunk
runner and stops after the first non-PASS checkpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RUNNER = SCRIPT_DIR / "run_mars56_s4p_100k_chunk_from_queue.sh"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest_csv = Path(args.manifest_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_manifest(manifest_csv)
    checks = _manifest_checks(manifest_csv, rows, args)
    executable = all(item["status"] == "PASS" for item in checks)
    dry_run = not bool(args.allow_real_emx)
    chunk_results: list[dict[str, Any]] = []
    if executable:
        for row in rows:
            result = _dry_run_chunk(row, args, out_dir) if dry_run else _run_chunk(row, args, out_dir)
            chunk_results.append(result)
            if result["overall_status"] != "PASS":
                break

    overall_status = _overall_status(executable, dry_run, chunk_results)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": _decision(overall_status, executable, dry_run),
        "manifest_csv": str(manifest_csv),
        "out_dir": str(out_dir),
        "allow_real_emx": bool(args.allow_real_emx),
        "dry_run": dry_run,
        "chunk_count": len(rows),
        "expected_total_count": int(args.expected_total_count),
        "chunk_size": int(args.chunk_size),
        "completed_chunk_count": sum(1 for item in chunk_results if item.get("overall_status") == "PASS"),
        "checks": checks,
        "chunk_results": chunk_results,
        "safety_notes": [
            "Real EMX execution requires --allow-real-emx.",
            "Each row must point to a prebuilt physical-feature targeted grounded-S4P queue.",
            "Execution stops after the first failed 100k checkpoint.",
        ],
    }
    summary_path = out_dir / "mars56_s4p_million_manifest_execution_summary.json"
    report_path = out_dir / "MARS56_S4P_MILLION_MANIFEST_EXECUTION_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--runner", default=str(DEFAULT_RUNNER))
    parser.add_argument("--expected-total-count", type=int, default=1_000_000)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--jobs", type=int, default=48)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _manifest_checks(path: Path, rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    expected_chunks = int(args.expected_total_count) // int(args.chunk_size)
    checks = [
        _check("manifest exists", path.is_file(), str(path)),
        _check("manifest rows present", bool(rows), f"rows={len(rows)}"),
        _check("chunk count matches expected total", len(rows) == expected_chunks, f"rows={len(rows)}, expected={expected_chunks}"),
        _check("expected total divisible by chunk size", int(args.expected_total_count) % int(args.chunk_size) == 0, f"total={args.expected_total_count}, chunk={args.chunk_size}"),
        _check("chunk runner exists", Path(args.runner).expanduser().is_file(), str(args.runner)),
    ]
    seen: set[int] = set()
    for pos, row in enumerate(rows, start=1):
        chunk_index = _as_int(row.get("chunk_index"), pos)
        candidate_csv = Path(row.get("candidate_csv", "")).expanduser()
        config = Path(row.get("config", "")).expanduser()
        checks.extend(
            [
                _check(f"chunk {pos} index positive", chunk_index > 0, str(chunk_index)),
                _check(f"chunk {pos} index unique", chunk_index not in seen, str(chunk_index)),
                _check(f"chunk {pos} candidate_csv exists", candidate_csv.is_file(), str(candidate_csv)),
                _check(f"chunk {pos} config exists", config.is_file(), str(config)),
            ]
        )
        seen.add(chunk_index)
    return checks


def _dry_run_chunk(row: dict[str, str], args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    command = _chunk_command(row, args, out_dir)
    return {
        "chunk_index": _as_int(row.get("chunk_index"), 0),
        "overall_status": "PASS",
        "dry_run": True,
        "command": command,
    }


def _run_chunk(row: dict[str, str], args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    command = _chunk_command(row, args, out_dir)
    completed = subprocess.run(command, text=True, capture_output=True)
    chunk_out = Path(_chunk_out_dir(row, out_dir))
    summary = _read_json(chunk_out / "mars56_s4p_100k_chunk_run_summary.json")
    status = "PASS" if completed.returncode == 0 and summary.get("overall_status") == "PASS" else "FAIL"
    return {
        "chunk_index": _as_int(row.get("chunk_index"), 0),
        "overall_status": status,
        "returncode": completed.returncode,
        "summary": str(chunk_out / "mars56_s4p_100k_chunk_run_summary.json"),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "command": command,
    }


def _chunk_command(row: dict[str, str], args: argparse.Namespace, out_dir: Path) -> list[str]:
    chunk_index = _as_int(row.get("chunk_index"), 0)
    command = [
        str(Path(args.runner).expanduser().resolve()),
        "--candidate-csv",
        str(Path(row["candidate_csv"]).expanduser().resolve()),
        "--config",
        str(Path(row["config"]).expanduser().resolve()),
        "--out-dir",
        _chunk_out_dir(row, out_dir),
        "--chunk-index",
        str(chunk_index),
        "--count",
        str(int(row.get("count") or args.chunk_size)),
        "--min-valid",
        str(int(row.get("min_valid") or row.get("count") or args.chunk_size)),
        "--jobs",
        str(int(row.get("jobs") or args.jobs)),
        "--python",
        str(args.python),
    ]
    if row.get("candidate_dir"):
        command.extend(["--candidate-dir", str(Path(row["candidate_dir"]).expanduser().resolve())])
    return command


def _chunk_out_dir(row: dict[str, str], out_dir: Path) -> str:
    explicit = row.get("out_dir", "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    chunk_index = _as_int(row.get("chunk_index"), 0)
    return str((out_dir / f"chunk_{chunk_index:02d}").resolve())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {}


def _overall_status(executable: bool, dry_run: bool, chunk_results: list[dict[str, Any]]) -> str:
    if not executable:
        return "FAIL"
    if dry_run:
        return "DRY_RUN"
    return "PASS" if chunk_results and all(item.get("overall_status") == "PASS" for item in chunk_results) else "FAIL"


def _decision(status: str, executable: bool, dry_run: bool) -> str:
    if not executable:
        return "DO_NOT_RUN_MANIFEST_UNTIL_INPUTS_PASS"
    if dry_run:
        return "DRY_RUN_ONLY_ADD_ALLOW_REAL_EMX_TO_EXECUTE"
    return "MILLION_CAMPAIGN_COMPLETE" if status == "PASS" else "STOPPED_AT_FAILED_100K_CHUNK"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 S4P Million Manifest Execution",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Chunks: `{summary['chunk_count']}`",
        f"- Completed chunks: `{summary['completed_chunk_count']}`",
        f"- Dry run: `{summary['dry_run']}`",
        "",
        "## Checks",
    ]
    lines.extend(f"- {item['status']}: {item['name']} - {item['detail']}" for item in summary["checks"])
    lines.extend(["", "## Chunk Results"])
    for item in summary["chunk_results"]:
        lines.append(f"- Chunk {item.get('chunk_index')}: `{item.get('overall_status')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
