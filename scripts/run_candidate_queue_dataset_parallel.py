#!/usr/bin/env python3
"""Run a candidate-queue dataset through multiple independent worker shards.

This wrapper keeps the proven single-shard runner as the source of truth and
adds process-level parallelism around it. Each worker receives a CSV shard and a
separate output directory, then the wrapper merges shard CSV outputs and writes
an auditable summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.sim.touchstone import load_touchstone


@dataclass(frozen=True)
class ShardRun:
    index: int
    row_count: int
    csv_path: Path
    out_dir: Path
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    summary_path: Path
    summary: dict[str, Any] | None
    elapsed_seconds: float = 0.0
    reused_existing: bool = False


def main(argv: list[str] | None = None) -> int:
    started_at = time.perf_counter()
    args = _parse_args(argv)
    cadence_streamout_only = bool(args.cadence_streamout_only)
    run_emx = not bool(args.create_only or cadence_streamout_only)
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    if args.config is not None:
        args.config = str(Path(args.config).expanduser().resolve())
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_root = out_dir / "parallel_shards"
    shard_root.mkdir(parents=True, exist_ok=True)

    rows = _read_csv(candidate_csv)
    if args.max_count is not None:
        rows = rows[: max(0, int(args.max_count))]
    jobs = max(1, int(args.jobs))
    chunk_size = max(0, int(args.chunk_size or 0))
    scheduling_mode = "dynamic_chunk_shards" if chunk_size > 0 else "fixed_even_shards"
    shards = _chunk_rows(rows, chunk_size) if chunk_size > 0 else _split_rows(rows, jobs)
    shard_specs = _write_shard_inputs(shards, candidate_csv, shard_root)

    runs: list[ShardRun] = []
    pending_specs = list(shard_specs)
    if args.resume_completed:
        runs, pending_specs = _reuse_completed_shards(
            shard_specs,
            create_only=bool(args.create_only),
            cadence_streamout_only=cadence_streamout_only,
        )
    if pending_specs:
        from rfic_transformer_inverse_design.campaigns.broadband56_dispatch import bounded_completed, stage_admission

        admission = stage_admission(jobs)
        if admission is not None and (not cadence_streamout_only or chunk_size != 1):
            raise ValueError("fixed48 Cadence dispatch requires one-candidate streamout-only shards")
        dispatch_dir = out_dir / ("dispatch_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        for _, future in bounded_completed(
            len(pending_specs), lambda index: _run_shard(*pending_specs[index], args),
            max_workers=jobs, admission=admission, receipt_dir=dispatch_dir,
        ):
            runs.append(future.result())
    runs.sort(key=lambda item: item.index)

    merged_csv = out_dir / "dataset_rows.csv"
    merged_rows = _merge_dataset_rows(runs, merged_csv)
    pass_count = sum(1 for run in runs if (run.summary or {}).get("overall_status") == "PASS" and run.returncode == 0)
    fail_count = len(runs) - pass_count
    touchstone_contract = _touchstone_output_contract(
        out_dir=out_dir,
        merged_rows=merged_rows,
        create_only=bool(args.create_only),
        cadence_streamout_only=cadence_streamout_only,
        expected_extension=args.expected_touchstone_extension,
        expected_ports=args.expected_ports,
        expected_frequency_start_ghz=args.expected_frequency_start_ghz,
        expected_frequency_stop_ghz=args.expected_frequency_stop_ghz,
        expected_frequency_step_ghz=args.expected_frequency_step_ghz,
        expected_frequency_points=args.expected_frequency_points,
        frequency_tolerance_hz=args.frequency_tolerance_hz,
        max_touchstone_checks=args.max_touchstone_checks,
    )
    cadence_streamout_contract = _parallel_cadence_streamout_contract(
        runs=runs,
        enabled=cadence_streamout_only,
        expected_count=len(rows),
    )
    campaign_identity = _campaign_identity_summary(rows, merged_rows)
    checks = _parallel_checks(
        candidate_csv=candidate_csv,
        rows=rows,
        runs=runs,
        merged_rows=merged_rows,
        jobs=jobs,
        expected_shards=len(shard_specs),
        expected_count=args.expected_count,
        expected_jobs=args.expected_jobs,
        expected_campaign_contract_fingerprint=args.expected_campaign_contract_fingerprint,
        campaign_identity=campaign_identity,
        fail_on_error=bool(args.fail_on_error),
    ) + touchstone_contract["checks"] + cadence_streamout_contract["checks"]
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    elapsed_seconds = max(0.0, time.perf_counter() - started_at)
    active_elapsed_seconds = sum(float(run.elapsed_seconds) for run in runs if not run.reused_existing)
    seconds_per_row_effective = None if not merged_rows else elapsed_seconds / float(len(merged_rows))
    rows_per_second_effective = None if elapsed_seconds <= 0.0 else float(len(merged_rows)) / elapsed_seconds
    parallel_efficiency = (
        None
        if elapsed_seconds <= 0.0 or jobs <= 0
        else active_elapsed_seconds / (elapsed_seconds * float(jobs))
    )
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "PARALLEL_CANDIDATE_QUEUE_DATASET_READY" if status == "PASS" else "DO_NOT_USE_PARALLEL_DATASET",
        "candidate_csv": str(candidate_csv),
        "out_dir": str(out_dir),
        "jobs_requested": jobs,
        "scheduling_mode": scheduling_mode,
        "chunk_size": chunk_size,
        "input_row_count": len(rows),
        "expected_count": args.expected_count,
        "expected_jobs": args.expected_jobs,
        "shard_count": len(shard_specs),
        "merged_dataset_rows_csv": str(merged_csv),
        "merged_row_count": len(merged_rows),
        "pass_shard_count": pass_count,
        "fail_shard_count": fail_count,
        "resume_completed": bool(args.resume_completed),
        "reused_shard_count": sum(1 for run in runs if run.reused_existing),
        "pending_shard_count": len([run for run in runs if not run.reused_existing]),
        "elapsed_seconds": elapsed_seconds,
        "active_worker_elapsed_seconds_sum": active_elapsed_seconds,
        "parallel_efficiency": parallel_efficiency,
        "seconds_per_row_effective": seconds_per_row_effective,
        "rows_per_second_effective": rows_per_second_effective,
        "run_emx": run_emx,
        "create_only": bool(args.create_only),
        "cadence_streamout_only": cadence_streamout_only,
        "campaign_identity": campaign_identity,
        "touchstone_output_contract": touchstone_contract["summary"],
        "cadence_streamout_output_contract": cadence_streamout_contract["summary"],
        "shards": [_shard_summary(run) for run in runs],
        "checks": checks,
        "limitations": [
            "This wrapper provides process-level sample parallelism; each worker still uses the single-shard runner for Cadence/EMX correctness.",
            "EMX internal --parallel controls solver threading and is separate from this worker count.",
            "Final acceptance still requires dataset quality gates and random-sample EMX/HFSS physical-curve validation.",
        ],
    }
    summary_path = out_dir / "parallel_candidate_queue_dataset_summary.json"
    report_path = out_dir / "parallel_candidate_queue_dataset_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"dataset_rows={merged_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config")
    parser.add_argument("--max-count", type=int)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        help=(
            "Rows per dynamic scheduling shard. Use 0 to keep the legacy fixed mode "
            "with one even shard per requested job."
        ),
    )
    parser.add_argument("--expected-count", type=int, help="Fail unless the merged dataset has this many rows")
    parser.add_argument("--expected-jobs", type=int, help="Fail unless this worker count is requested")
    parser.add_argument(
        "--expected-campaign-contract-fingerprint",
        help=(
            "Fail unless both the queue and merged rows contain exactly this SHA-256 "
            "campaign contract fingerprint and geometry hashes remain unique"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--z-load-ohm", type=float, default=50.0)
    parser.add_argument("--uniformity-bins", type=int, default=10)
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument("--create-only", action="store_true")
    execution_mode.add_argument(
        "--cadence-streamout-only",
        action="store_true",
        help=(
            "Run candidate-bound Cadence streamout in each shard and stop "
            "before EMX."
        ),
    )
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--allow-outside-target-bin", action="store_true")
    parser.add_argument("--force-port-mode")
    parser.add_argument("--force-cadence-pin-purpose", type=int)
    parser.add_argument("--force-wideband-5-50-0p1", action="store_true")
    parser.add_argument("--force-wideband-5-60-0p5", action="store_true")
    parser.add_argument("--force-wideband-5-60-1p0", action="store_true")
    parser.add_argument("--expected-port-mode")
    parser.add_argument("--expected-pin-purpose", type=int)
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument(
        "--expected-touchstone-extension",
        default=".s8p",
        help="For non-create-only runs, fail unless successful rows point to this Touchstone extension.",
    )
    parser.add_argument(
        "--expected-ports",
        type=int,
        default=8,
        help="For non-create-only runs, parse sampled Touchstone files and fail unless this port count is found.",
    )
    parser.add_argument(
        "--max-touchstone-checks",
        type=int,
        default=500,
        help="Maximum number of successful Touchstone files to parse in the parallel-run output gate; default covers the full 500-row MARS run.",
    )
    parser.add_argument(
        "--resume-completed",
        action="store_true",
        help="Reuse a shard only when its existing summary is PASS and dataset_rows.csv still matches the current shard input",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _split_rows(rows: list[dict[str, str]], jobs: int) -> list[list[dict[str, str]]]:
    if not rows:
        return []
    shard_count = min(max(1, int(jobs)), len(rows))
    base_size, extra_rows = divmod(len(rows), shard_count)
    shards: list[list[dict[str, str]]] = []
    start = 0
    for index in range(shard_count):
        stop = start + base_size + (1 if index < extra_rows else 0)
        shards.append(rows[start:stop])
        start = stop
    return shards


def _chunk_rows(rows: list[dict[str, str]], chunk_size: int) -> list[list[dict[str, str]]]:
    if not rows:
        return []
    size = max(1, int(chunk_size))
    return [rows[start : start + size] for start in range(0, len(rows), size)]


def _write_shard_inputs(
    shards: list[list[dict[str, str]]],
    source_csv: Path,
    shard_root: Path,
) -> list[tuple[int, int, Path, Path]]:
    if not shards:
        return []
    source_rows = _read_csv(source_csv)
    fieldnames = list(source_rows[0].keys()) if source_rows else sorted({key for shard in shards for row in shard for key in row})
    specs = []
    for index, shard in enumerate(shards):
        shard_out = shard_root / f"shard_{index:03d}"
        shard_csv = shard_out / "candidate_queue_shard.csv"
        _write_csv(shard_csv, shard, fieldnames)
        specs.append((index, len(shard), shard_csv, shard_out))
    return specs


def _reuse_completed_shards(
    shard_specs: list[tuple[int, int, Path, Path]],
    *,
    create_only: bool,
    cadence_streamout_only: bool,
) -> tuple[list[ShardRun], list[tuple[int, int, Path, Path]]]:
    reused: list[ShardRun] = []
    pending: list[tuple[int, int, Path, Path]] = []
    for index, row_count, csv_path, shard_out in shard_specs:
        existing = _existing_shard_run(
            index,
            row_count,
            csv_path,
            shard_out,
            create_only=create_only,
            cadence_streamout_only=cadence_streamout_only,
        )
        if existing is None:
            pending.append((index, row_count, csv_path, shard_out))
        else:
            reused.append(existing)
    return reused, pending


def _existing_shard_run(
    index: int,
    row_count: int,
    csv_path: Path,
    out_dir: Path,
    *,
    create_only: bool,
    cadence_streamout_only: bool,
) -> ShardRun | None:
    summary_path = out_dir / "candidate_queue_dataset_summary.json"
    dataset_csv = out_dir / "dataset_rows.csv"
    if not summary_path.is_file() or not dataset_csv.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if summary.get("overall_status") != "PASS":
        return None
    expected_run_emx = not bool(create_only or cadence_streamout_only)
    if (
        summary.get("create_only") is not bool(create_only)
        or summary.get("cadence_streamout_only") is not bool(cadence_streamout_only)
        or summary.get("run_emx") is not expected_run_emx
    ):
        return None
    input_rows = _read_csv(csv_path)
    dataset_rows = _read_csv(dataset_csv)
    if len(input_rows) != int(row_count) or len(dataset_rows) != int(row_count):
        return None
    if not _candidate_ids_match(input_rows, dataset_rows):
        return None
    return ShardRun(
        index=index,
        row_count=row_count,
        csv_path=csv_path,
        out_dir=out_dir,
        command=["reused-existing-shard"],
        returncode=0,
        stdout="",
        stderr="",
        summary_path=summary_path,
        summary=summary,
        elapsed_seconds=0.0,
        reused_existing=True,
    )


def _candidate_ids_match(input_rows: list[dict[str, str]], dataset_rows: list[dict[str, str]]) -> bool:
    input_ids = [str(row.get("candidate_id", "")).strip() for row in input_rows]
    output_ids = [str(row.get("queue__candidate_id") or row.get("candidate_id") or "").strip() for row in dataset_rows]
    if all(input_ids) and all(output_ids):
        return input_ids == output_ids
    return True


def _run_shard(index: int, row_count: int, csv_path: Path, out_dir: Path, args: argparse.Namespace) -> ShardRun:
    started_at = time.perf_counter()
    runner = Path(__file__).with_name("run_candidate_queue_dataset.py")
    command = [
        sys.executable,
        str(runner),
        "--candidate-csv",
        str(csv_path),
        "--out-dir",
        str(out_dir),
        "--batch-size",
        str(int(args.batch_size)),
        "--z-load-ohm",
        str(float(args.z_load_ohm)),
        "--uniformity-bins",
        str(int(args.uniformity_bins)),
        "--no-fail-exit",
    ]
    _append_optional_arg(command, "--config", args.config)
    _append_flag(command, "--create-only", args.create_only)
    _append_flag(command, "--cadence-streamout-only", args.cadence_streamout_only)
    _append_flag(command, "--fail-on-error", args.fail_on_error)
    _append_flag(command, "--allow-outside-target-bin", args.allow_outside_target_bin)
    _append_flag(command, "--force-wideband-5-50-0p1", args.force_wideband_5_50_0p1)
    _append_flag(command, "--force-wideband-5-60-0p5", args.force_wideband_5_60_0p5)
    _append_flag(command, "--force-wideband-5-60-1p0", args.force_wideband_5_60_1p0)
    _append_optional_arg(command, "--force-port-mode", args.force_port_mode)
    _append_optional_arg(command, "--force-cadence-pin-purpose", args.force_cadence_pin_purpose)
    _append_optional_arg(command, "--expected-port-mode", args.expected_port_mode)
    _append_optional_arg(command, "--expected-pin-purpose", args.expected_pin_purpose)
    _append_optional_arg(command, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
    _append_optional_arg(command, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
    _append_optional_arg(command, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
    _append_optional_arg(command, "--expected-frequency-points", args.expected_frequency_points)
    _append_optional_arg(command, "--frequency-tolerance-hz", args.frequency_tolerance_hz)
    _append_optional_arg(command, "--expected-touchstone-extension", args.expected_touchstone_extension)
    _append_optional_arg(command, "--expected-ports", args.expected_ports)
    _append_optional_arg(command, "--max-touchstone-checks", args.max_touchstone_checks)

    env = dict(os.environ)
    existing_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    env["PYTHONPATH"] = str(REPO_ROOT) if not existing_pythonpath else f"{REPO_ROOT}:{existing_pythonpath}"
    completed = subprocess.run(command, capture_output=True, text=True, check=False, cwd=str(REPO_ROOT), env=env)
    elapsed_seconds = max(0.0, time.perf_counter() - started_at)
    (out_dir / "parallel_worker_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (out_dir / "parallel_worker_stderr.log").write_text(completed.stderr, encoding="utf-8")
    summary_path = out_dir / "candidate_queue_dataset_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    return ShardRun(
        index=index,
        row_count=row_count,
        csv_path=csv_path,
        out_dir=out_dir,
        command=command,
        returncode=int(completed.returncode),
        stdout=completed.stdout,
        stderr=completed.stderr,
        summary_path=summary_path,
        summary=summary,
        elapsed_seconds=elapsed_seconds,
    )


def _append_optional_arg(command: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    command.extend([flag, str(value)])


def _append_flag(command: list[str], flag: str, value: bool) -> None:
    if bool(value):
        command.append(flag)


def _merge_dataset_rows(runs: list[ShardRun], out_csv: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    for run in runs:
        dataset_csv = run.out_dir / "dataset_rows.csv"
        if not dataset_csv.is_file():
            continue
        with dataset_csv.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for field in reader.fieldnames or []:
                if field not in fieldnames:
                    fieldnames.append(field)
            for row in reader:
                merged = dict(row)
                merged["parallel_shard"] = f"{run.index:03d}"
                rows.append(merged)
    if rows:
        if "parallel_shard" not in fieldnames:
            fieldnames.append("parallel_shard")
        _write_csv(out_csv, rows, fieldnames)
    else:
        _write_csv(out_csv, [], ["parallel_shard"])
    return rows


def _parallel_checks(
    *,
    candidate_csv: Path,
    rows: list[dict[str, str]],
    runs: list[ShardRun],
    merged_rows: list[dict[str, Any]],
    jobs: int,
    expected_shards: int,
    expected_count: int | None,
    expected_jobs: int | None,
    fail_on_error: bool,
    expected_campaign_contract_fingerprint: str | None = None,
    campaign_identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    input_count = len(rows)
    merged_count = len(merged_rows)
    expected_shards = int(expected_shards)
    campaign_identity = campaign_identity or _campaign_identity_summary(rows, merged_rows)
    pass_shards = [run for run in runs if run.returncode == 0 and (run.summary or {}).get("overall_status") == "PASS"]
    checks = [
        _check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("input_rows_present", input_count > 0, f"input_rows={input_count}"),
        _check(
            "requested_jobs_match_expected",
            expected_jobs is None or int(jobs) == int(expected_jobs),
            f"jobs={jobs}, expected_jobs={expected_jobs}",
        ),
        _check(
            "input_count_matches_expected",
            expected_count is None or input_count == int(expected_count),
            f"input_rows={input_count}, expected_count={expected_count}",
        ),
        _check(
            "shard_count_matches_schedule",
            len(runs) == expected_shards,
            f"shards={len(runs)}, expected_shards={expected_shards}, jobs={jobs}, input_rows={input_count}",
        ),
        _check(
            "shard_row_accounting",
            sum(run.row_count for run in runs) == input_count,
            f"shard_rows={sum(run.row_count for run in runs)}, input_rows={input_count}",
        ),
        _check(
            "all_shards_passed",
            len(pass_shards) == len(runs) and bool(runs),
            f"pass_shards={len(pass_shards)}, total_shards={len(runs)}",
        ),
        _check(
            "merged_row_count_matches_input",
            merged_count == input_count,
            f"merged_rows={merged_count}, input_rows={input_count}",
        ),
        _check(
            "merged_count_matches_expected",
            expected_count is None or merged_count == int(expected_count),
            f"merged_rows={merged_count}, expected_count={expected_count}",
        ),
        _check(
            "merged_candidate_identity_matches_input",
            _candidate_ids_match(rows, merged_rows),
            "queue__candidate_id preserves input ordering and identity",
        ),
        _check(
            "all_merged_rows_ok_when_fail_on_error",
            (not fail_on_error) or (bool(merged_rows) and all(_truthy(row.get("ok")) for row in merged_rows)),
            f"fail_on_error={fail_on_error}, ok_rows={sum(_truthy(row.get('ok')) for row in merged_rows)}, merged_rows={merged_count}",
        ),
    ]
    if expected_campaign_contract_fingerprint is not None:
        expected = str(expected_campaign_contract_fingerprint).strip().lower()
        checks.extend(
            [
                _check(
                    "expected_campaign_contract_fingerprint_is_sha256",
                    _is_sha256(expected),
                    expected,
                ),
                _check(
                    "input_campaign_contract_fingerprint_matches_expected",
                    campaign_identity["input_campaign_contract_fingerprints"] == [expected],
                    campaign_identity["input_campaign_contract_fingerprints"],
                ),
                _check(
                    "merged_campaign_contract_fingerprint_matches_expected",
                    campaign_identity["merged_campaign_contract_fingerprints"] == [expected],
                    campaign_identity["merged_campaign_contract_fingerprints"],
                ),
                _check(
                    "input_geometry_hashes_are_complete_and_unique",
                    campaign_identity["input_geometry_sha256_present_count"] == input_count
                    and campaign_identity["input_geometry_sha256_unique_count"] == input_count,
                    campaign_identity,
                ),
                _check(
                    "merged_geometry_hashes_match_input",
                    campaign_identity["merged_geometry_sha256_present_count"] == merged_count
                    and campaign_identity["merged_geometry_sha256_unique_count"] == merged_count
                    and campaign_identity["geometry_sha256_sets_match"],
                    campaign_identity,
                ),
            ]
        )
    return checks


def _campaign_identity_summary(
    input_rows: list[dict[str, str]],
    merged_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    input_fingerprints = _unique_nonempty(input_rows, "campaign_contract_fingerprint")
    merged_fingerprints = _unique_nonempty(merged_rows, "queue__campaign_contract_fingerprint")
    input_hashes = _nonempty_values(input_rows, "geometry_sha256")
    merged_hashes = _nonempty_values(merged_rows, "queue__geometry_sha256")
    return {
        "input_campaign_contract_fingerprints": input_fingerprints,
        "merged_campaign_contract_fingerprints": merged_fingerprints,
        "input_geometry_sha256_present_count": len(input_hashes),
        "input_geometry_sha256_unique_count": len(set(input_hashes)),
        "merged_geometry_sha256_present_count": len(merged_hashes),
        "merged_geometry_sha256_unique_count": len(set(merged_hashes)),
        "geometry_sha256_sets_match": set(input_hashes) == set(merged_hashes),
    }


def _nonempty_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    return [str(row.get(key) or "").strip().lower() for row in rows if str(row.get(key) or "").strip()]


def _unique_nonempty(rows: list[dict[str, Any]], key: str) -> list[str]:
    return sorted(set(_nonempty_values(rows, key)))


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _parallel_cadence_streamout_contract(
    *,
    runs: list[ShardRun],
    enabled: bool,
    expected_count: int,
) -> dict[str, Any]:
    if not enabled:
        return {
            "summary": {
                "checked": False,
                "reason": "cadence_streamout_only_not_requested",
                "expected_count": int(expected_count),
                "shard_count": len(runs),
            },
            "checks": [],
        }

    shard_records: list[dict[str, Any]] = []
    for run in runs:
        summary = run.summary or {}
        contract = summary.get("cadence_streamout_output_contract") or {}
        shard_records.append(
            {
                "index": run.index,
                "run_emx": summary.get("run_emx"),
                "create_only": summary.get("create_only"),
                "cadence_streamout_only": summary.get("cadence_streamout_only"),
                "contract_checked": contract.get("checked") is True,
                "valid_candidate_bound_gds_count": int(
                    contract.get("valid_candidate_bound_gds_count") or 0
                ),
                "touchstone_file_count": int(contract.get("touchstone_file_count") or 0),
            }
        )
    valid_count = sum(
        record["valid_candidate_bound_gds_count"] for record in shard_records
    )
    touchstone_count = sum(record["touchstone_file_count"] for record in shard_records)
    checks = [
        _check(
            "all_shards_used_cadence_streamout_only_mode",
            bool(shard_records)
            and all(
                record["run_emx"] is False
                and record["create_only"] is False
                and record["cadence_streamout_only"] is True
                for record in shard_records
            ),
            f"shards={len(shard_records)}",
        ),
        _check(
            "all_shard_cadence_streamout_contracts_checked",
            bool(shard_records)
            and all(record["contract_checked"] for record in shard_records),
            f"unchecked={sum(not record['contract_checked'] for record in shard_records)}",
        ),
        _check(
            "candidate_bound_cadence_gds_count_matches_input",
            valid_count == int(expected_count) and int(expected_count) > 0,
            f"valid_gds={valid_count}, expected={expected_count}",
        ),
        _check(
            "cadence_streamout_shards_produced_no_touchstone",
            touchstone_count == 0,
            f"touchstone_files={touchstone_count}",
        ),
    ]
    return {
        "summary": {
            "checked": True,
            "expected_count": int(expected_count),
            "shard_count": len(shard_records),
            "valid_candidate_bound_gds_count": valid_count,
            "touchstone_file_count": touchstone_count,
            "shards": shard_records,
        },
        "checks": checks,
    }


def _touchstone_output_contract(
    *,
    out_dir: Path,
    merged_rows: list[dict[str, Any]],
    create_only: bool,
    cadence_streamout_only: bool = False,
    expected_extension: str,
    expected_ports: int,
    expected_frequency_start_ghz: float | None,
    expected_frequency_stop_ghz: float | None,
    expected_frequency_step_ghz: float | None,
    expected_frequency_points: int | None,
    frequency_tolerance_hz: float,
    max_touchstone_checks: int,
) -> dict[str, Any]:
    expected_extension = _normalise_suffix(expected_extension)
    if create_only or cadence_streamout_only:
        reason = (
            "cadence_streamout_only_has_no_emx_touchstone_output"
            if cadence_streamout_only
            else "create_only_run_has_no_emx_touchstone_output"
        )
        check_name = (
            "touchstone_output_contract_skipped_for_cadence_streamout_only"
            if cadence_streamout_only
            else "touchstone_output_contract_skipped_for_create_only"
        )
        summary = {
            "checked": False,
            "reason": reason,
            "expected_extension": expected_extension,
            "expected_ports": int(expected_ports),
            "sampled_count": 0,
        }
        return {
            "summary": summary,
            "checks": [
                _check(
                    check_name,
                    True,
                    f"layout-only modes intentionally do not require {expected_extension} output",
                )
            ],
        }

    ok_rows = [row for row in merged_rows if _truthy(row.get("ok"))]
    resolved: list[tuple[dict[str, Any], Path | None, str]] = []
    for row in ok_rows:
        raw, path = _resolve_touchstone_path(row, out_dir)
        resolved.append((row, path, raw))

    paths = [path for _, path, raw in resolved if path is not None and raw]
    missing_path_rows = [row for row, path, raw in resolved if path is None or not raw]
    existing_paths = [path for path in paths if path.is_file()]
    nonzero_paths = [path for path in existing_paths if path.stat().st_size > 0]
    extension_paths = [path for path in nonzero_paths if path.suffix.lower() == expected_extension]
    sample_paths = extension_paths[: max(0, int(max_touchstone_checks))]

    parse_errors: list[str] = []
    port_errors: list[str] = []
    frequency_errors: list[str] = []
    parsed_count = 0
    for path in sample_paths:
        try:
            result = load_touchstone(path)
            parsed_count += 1
        except Exception as exc:
            parse_errors.append(f"{path}: {exc}")
            continue
        if int(result.num_ports) != int(expected_ports):
            port_errors.append(f"{path}: ports={result.num_ports}, expected={expected_ports}")
        frequency_error = _frequency_grid_error(
            result.freqs_hz,
            expected_start_ghz=expected_frequency_start_ghz,
            expected_stop_ghz=expected_frequency_stop_ghz,
            expected_step_ghz=expected_frequency_step_ghz,
            expected_points=expected_frequency_points,
            tolerance_hz=frequency_tolerance_hz,
        )
        if frequency_error is not None:
            frequency_errors.append(f"{path}: {frequency_error}")

    missing_files = [str(path) for path in paths if not path.is_file()]
    zero_files = [str(path) for path in existing_paths if path.stat().st_size <= 0]
    wrong_extensions = [str(path) for path in nonzero_paths if path.suffix.lower() != expected_extension]
    frequency_check_requested = any(
        value is not None
        for value in (
            expected_frequency_start_ghz,
            expected_frequency_stop_ghz,
            expected_frequency_step_ghz,
            expected_frequency_points,
        )
    )
    summary = {
        "checked": True,
        "ok_row_count": len(ok_rows),
        "missing_touchstone_path_row_count": len(missing_path_rows),
        "resolved_path_count": len(paths),
        "existing_file_count": len(existing_paths),
        "nonzero_file_count": len(nonzero_paths),
        "expected_extension": expected_extension,
        "extension_match_count": len(extension_paths),
        "expected_ports": int(expected_ports),
        "sampled_count": len(sample_paths),
        "parsed_count": parsed_count,
        "parse_error_count": len(parse_errors),
        "port_error_count": len(port_errors),
        "frequency_error_count": len(frequency_errors),
        "expected_frequency": {
            "start_ghz": expected_frequency_start_ghz,
            "stop_ghz": expected_frequency_stop_ghz,
            "step_ghz": expected_frequency_step_ghz,
            "points": expected_frequency_points,
            "tolerance_hz": frequency_tolerance_hz,
        },
        "example_missing_files": missing_files[:5],
        "example_zero_files": zero_files[:5],
        "example_wrong_extensions": wrong_extensions[:5],
        "example_parse_errors": parse_errors[:5],
        "example_port_errors": port_errors[:5],
        "example_frequency_errors": frequency_errors[:5],
    }
    checks = [
        _check(
            "merged_success_rows_present_for_touchstone_check",
            len(ok_rows) > 0,
            f"ok_rows={len(ok_rows)}",
        ),
        _check(
            "merged_ok_rows_have_touchstone_paths",
            len(missing_path_rows) == 0 and len(paths) == len(ok_rows),
            f"resolved_paths={len(paths)}, ok_rows={len(ok_rows)}, missing_path_rows={len(missing_path_rows)}",
        ),
        _check(
            "merged_touchstone_files_exist",
            len(existing_paths) == len(paths) and len(paths) > 0,
            f"existing_files={len(existing_paths)}, resolved_paths={len(paths)}, examples={missing_files[:3]}",
        ),
        _check(
            "merged_touchstone_files_nonzero",
            len(nonzero_paths) == len(existing_paths) and len(existing_paths) > 0,
            f"nonzero_files={len(nonzero_paths)}, existing_files={len(existing_paths)}, examples={zero_files[:3]}",
        ),
        _check(
            "merged_touchstone_extensions_match_expected",
            len(extension_paths) == len(nonzero_paths) and len(nonzero_paths) > 0,
            f"extension={expected_extension}, matching={len(extension_paths)}, nonzero_files={len(nonzero_paths)}, examples={wrong_extensions[:3]}",
        ),
        _check(
            "sampled_touchstone_files_parse",
            len(sample_paths) > 0 and len(parse_errors) == 0 and parsed_count == len(sample_paths),
            f"sampled={len(sample_paths)}, parsed={parsed_count}, errors={parse_errors[:3]}",
        ),
        _check(
            "sampled_touchstone_ports_match_expected",
            len(sample_paths) > 0 and len(port_errors) == 0 and parsed_count == len(sample_paths),
            f"expected_ports={expected_ports}, sampled={len(sample_paths)}, errors={port_errors[:3]}",
        ),
        _check(
            "sampled_touchstone_frequency_grid_matches_expected",
            (not frequency_check_requested)
            or (len(sample_paths) > 0 and len(frequency_errors) == 0 and parsed_count == len(sample_paths)),
            (
                "frequency grid check not requested"
                if not frequency_check_requested
                else f"sampled={len(sample_paths)}, errors={frequency_errors[:3]}"
            ),
        ),
    ]
    return {"summary": summary, "checks": checks}


def _resolve_touchstone_path(row: dict[str, Any], out_dir: Path) -> tuple[str, Path | None]:
    raw = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
    if not raw or raw.lower() in {"none", "null", "nan"}:
        return raw, None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return raw, path
    candidates = [out_dir / path]
    shard = str(row.get("parallel_shard") or "").strip()
    if shard:
        candidates.append(out_dir / "parallel_shards" / f"shard_{int(shard):03d}" / path if shard.isdigit() else out_dir / "parallel_shards" / f"shard_{shard}" / path)
    for candidate in candidates:
        if candidate.exists():
            return raw, candidate
    return raw, candidates[-1] if candidates else path


def _normalise_suffix(value: str) -> str:
    suffix = str(value or "").strip().lower()
    if not suffix:
        return ".s8p"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "pass", "ok"}


def _frequency_grid_error(
    freqs_hz: Any,
    *,
    expected_start_ghz: float | None,
    expected_stop_ghz: float | None,
    expected_step_ghz: float | None,
    expected_points: int | None,
    tolerance_hz: float,
) -> str | None:
    freqs = [float(value) for value in freqs_hz]
    if expected_points is not None and len(freqs) != int(expected_points):
        return f"points={len(freqs)}, expected={expected_points}"
    if not freqs:
        return "no frequency points"
    tolerance = float(tolerance_hz)
    if expected_start_ghz is not None:
        expected = float(expected_start_ghz) * 1e9
        if abs(freqs[0] - expected) > tolerance:
            return f"start_hz={freqs[0]}, expected={expected}, tolerance={tolerance}"
    if expected_stop_ghz is not None:
        expected = float(expected_stop_ghz) * 1e9
        if abs(freqs[-1] - expected) > tolerance:
            return f"stop_hz={freqs[-1]}, expected={expected}, tolerance={tolerance}"
    if expected_step_ghz is not None and len(freqs) > 1:
        expected = float(expected_step_ghz) * 1e9
        max_error = max(abs((freqs[index] - freqs[index - 1]) - expected) for index in range(1, len(freqs)))
        if max_error > tolerance:
            return f"max_step_error_hz={max_error}, expected_step_hz={expected}, tolerance={tolerance}"
    return None


def _shard_summary(run: ShardRun) -> dict[str, Any]:
    status = None if run.summary is None else run.summary.get("overall_status")
    return {
        "index": run.index,
        "input_rows": run.row_count,
        "candidate_csv": str(run.csv_path),
        "out_dir": str(run.out_dir),
        "returncode": run.returncode,
        "overall_status": status,
        "dataset_rows_csv": str(run.out_dir / "dataset_rows.csv"),
        "summary_path": str(run.summary_path),
        "command": run.command,
        "elapsed_seconds": float(run.elapsed_seconds),
        "seconds_per_row": None if run.row_count <= 0 else float(run.elapsed_seconds) / float(run.row_count),
        "reused_existing": bool(run.reused_existing),
        "stdout_log": str(run.out_dir / "parallel_worker_stdout.log"),
        "stderr_log": str(run.out_dir / "parallel_worker_stderr.log"),
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Parallel Candidate Queue Dataset Report",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Jobs requested: `{summary['jobs_requested']}`",
        f"- Scheduling mode: `{summary.get('scheduling_mode')}`",
        f"- Chunk size: `{summary.get('chunk_size')}`",
        f"- Input rows: `{summary['input_row_count']}`",
        f"- Shards: `{summary['shard_count']}`",
        f"- Merged rows: `{summary['merged_row_count']}`",
        f"- Elapsed seconds: `{summary.get('elapsed_seconds')}`",
        f"- Parallel efficiency: `{summary.get('parallel_efficiency')}`",
        f"- Effective seconds per row: `{summary.get('seconds_per_row_effective')}`",
        f"- Merged CSV: `{summary['merged_dataset_rows_csv']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(
        [
            "",
            "## Shards",
        ]
    )
    for shard in summary["shards"]:
        lines.append(
            f"- shard {shard['index']:03d}: status={shard['overall_status']} "
            f"returncode={shard['returncode']} rows={shard['input_rows']} "
            f"elapsed_s={shard.get('elapsed_seconds')} "
            f"reused={shard['reused_existing']} out={shard['out_dir']}"
        )
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
