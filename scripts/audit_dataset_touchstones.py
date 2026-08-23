#!/usr/bin/env python3
"""Run Touchstone transformer preflight on a dataset sample.

The script discovers Touchstone files from dataset_rows.csv or from the common
serial and parallel evaluation layouts, runs audit_touchstone_transformer.py
for each selected file, and writes a dataset-level JSON/CSV/Markdown summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_root = Path(args.dataset_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    discovered = _discover_touchstones(dataset_root)
    selected = _select_paths(discovered, sample_size=args.sample_size, seed=args.seed, audit_all=args.all)
    results = []
    for index, path in enumerate(selected, start=1):
        sample_out = out_dir / f"{index:04d}_{_safe_sample_name(path)}"
        result = _run_single_audit(path, sample_out, args, plot=args.plot or (args.plot_first and index == 1))
        results.append(result)

    summary = _build_summary(dataset_root, out_dir, discovered, selected, results, args)
    summary_path = out_dir / "dataset_touchstone_audit_summary.json"
    csv_path = out_dir / "dataset_touchstone_audit_rows.csv"
    report_path = out_dir / "dataset_touchstone_audit_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_rows_csv(csv_path, results)
    report_path.write_text(_render_report(summary, results), encoding="utf-8")

    print(f"overall_status={summary['overall_status']}")
    print(f"discovered_count={summary['discovered_count']}")
    print(f"audited_count={summary['audited_count']}")
    print(f"pass_count={summary['pass_count']}")
    print(f"fail_count={summary['fail_count']}")
    print(f"report={report_path}")
    print(f"summary={summary_path}")
    print(f"rows_csv={csv_path}")
    return 2 if summary["overall_status"] == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260613)
    parser.add_argument("--all", action="store_true", help="Audit every discovered Touchstone file")
    parser.add_argument("--plot", action="store_true", help="Write plots for every audited file")
    parser.add_argument("--plot-first", action="store_true", help="Write plots only for the first audited file")
    parser.add_argument("--expected-ports", type=int, default=4)
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--expected-frequency-start-ghz", type=float)
    parser.add_argument("--expected-frequency-stop-ghz", type=float)
    parser.add_argument("--expected-frequency-step-ghz", type=float)
    parser.add_argument("--expected-frequency-points", type=int)
    parser.add_argument("--required-sweep-start-ghz", type=float)
    parser.add_argument("--required-sweep-stop-ghz", type=float)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--max-reciprocity-error", type=float, default=1.0e-6)
    parser.add_argument("--max-passivity-sigma", type=float, default=1.001)
    parser.add_argument("--target-frequency-ghz", type=float)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float)
    parser.add_argument("--min-target-inductance-nh", type=float, default=0.0)
    parser.add_argument("--min-target-q", type=float, default=0.0)
    parser.add_argument("--max-target-abs-k", type=float, default=1.05)
    parser.add_argument("--positive-window-start-ghz", type=float)
    parser.add_argument("--positive-window-stop-ghz", type=float)
    parser.add_argument("--shape-window-start-ghz", type=float)
    parser.add_argument("--shape-window-stop-ghz", type=float)
    parser.add_argument("--max-shape-spike-ratio", type=float, default=8.0)
    parser.add_argument("--max-shape-relative-step", type=float, default=0.5)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _discover_touchstones(dataset_root: Path) -> list[Path]:
    paths: list[Path] = []
    rows_path = dataset_root / "dataset_rows.csv"
    if rows_path.exists():
        with rows_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if not _truthy(row.get("ok", "true")):
                    continue
                text = (row.get("touchstone_path") or row.get("sparameter_path") or "").strip()
                if text:
                    paths.append(_resolve(dataset_root, text))
    if not paths:
        paths.extend(dataset_root.glob("evaluations/*/emx/*.s*p"))
        paths.extend(dataset_root.glob("parallel_shards/shard_*/evaluations/*/emx/*.s*p"))
    if not paths:
        paths.extend(dataset_root.glob("*.s*p"))
    unique: dict[str, Path] = {}
    for path in paths:
        unique[str(path.expanduser().resolve())] = path.expanduser().resolve()
    return sorted(unique.values())


def _select_paths(paths: list[Path], *, sample_size: int, seed: int, audit_all: bool) -> list[Path]:
    if audit_all or sample_size >= len(paths):
        return list(paths)
    if sample_size <= 0:
        return []
    rng = random.Random(seed)
    return sorted(rng.sample(paths, sample_size))


def _run_single_audit(path: Path, out_dir: Path, args: argparse.Namespace, *, plot: bool) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "audit_touchstone_transformer.py"),
        str(path),
        "--out-dir",
        str(out_dir),
        "--expected-ports",
        str(args.expected_ports),
        "--port-pairs",
        args.port_pairs,
        "--frequency-tolerance-hz",
        str(args.frequency_tolerance_hz),
        "--max-reciprocity-error",
        str(args.max_reciprocity_error),
        "--max-passivity-sigma",
        str(args.max_passivity_sigma),
        "--min-target-inductance-nh",
        str(args.min_target_inductance_nh),
        "--min-target-q",
        str(args.min_target_q),
        "--max-target-abs-k",
        str(args.max_target_abs_k),
        "--no-fail-exit",
    ]
    _append_optional_float(cmd, "--expected-frequency-start-ghz", args.expected_frequency_start_ghz)
    _append_optional_float(cmd, "--expected-frequency-stop-ghz", args.expected_frequency_stop_ghz)
    _append_optional_float(cmd, "--expected-frequency-step-ghz", args.expected_frequency_step_ghz)
    _append_optional_int(cmd, "--expected-frequency-points", args.expected_frequency_points)
    _append_optional_float(cmd, "--required-sweep-start-ghz", args.required_sweep_start_ghz)
    _append_optional_float(cmd, "--required-sweep-stop-ghz", args.required_sweep_stop_ghz)
    _append_optional_float(cmd, "--target-frequency-ghz", args.target_frequency_ghz)
    _append_optional_float(cmd, "--target-frequency-tolerance-ghz", args.target_frequency_tolerance_ghz)
    _append_optional_float(cmd, "--positive-window-start-ghz", args.positive_window_start_ghz)
    _append_optional_float(cmd, "--positive-window-stop-ghz", args.positive_window_stop_ghz)
    _append_optional_float(cmd, "--shape-window-start-ghz", args.shape_window_start_ghz)
    _append_optional_float(cmd, "--shape-window-stop-ghz", args.shape_window_stop_ghz)
    cmd.extend(["--max-shape-spike-ratio", str(args.max_shape_spike_ratio)])
    cmd.extend(["--max-shape-relative-step", str(args.max_shape_relative_step)])
    if plot:
        cmd.append("--plot")
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    summary_path = out_dir / "touchstone_transformer_audit_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    fail_checks = [
        f"{item.get('name')}: {item.get('detail')}"
        for item in summary.get("checks", [])
        if item.get("status") == "FAIL"
    ]
    return {
        "touchstone_path": str(path),
        "out_dir": str(out_dir),
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "overall_status": summary.get("overall_status", "FAIL"),
        "report": str(out_dir / "touchstone_transformer_audit_report.md"),
        "summary": str(summary_path),
        "frequency": summary.get("frequency", {}),
        "port_count": summary.get("port_count"),
        "matrix_quality": summary.get("matrix_quality", {}),
        "target_point": summary.get("metric_summary", {}).get("target_point", {}),
        "fail_checks": fail_checks,
    }


def _build_summary(
    dataset_root: Path,
    out_dir: Path,
    discovered: list[Path],
    selected: list[Path],
    results: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    fail_count = sum(1 for item in results if item.get("overall_status") != "PASS")
    pass_count = len(results) - fail_count
    overall_status = "FAIL" if fail_count or not results else "PASS"
    failure_reason_counts = _failure_reason_counts(results)
    matrix_quality_summary = _matrix_quality_summary(results)
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_root": str(dataset_root),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "discovered_count": len(discovered),
        "audited_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "seed": args.seed,
        "sample_size": args.sample_size,
        "all": bool(args.all),
        "selected_paths": [str(path) for path in selected],
        "failure_reason_counts": failure_reason_counts,
        "matrix_quality_summary": matrix_quality_summary,
        "arguments": {
            "expected_ports": args.expected_ports,
            "expected_frequency_start_ghz": args.expected_frequency_start_ghz,
            "expected_frequency_stop_ghz": args.expected_frequency_stop_ghz,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
            "required_sweep_start_ghz": args.required_sweep_start_ghz,
            "required_sweep_stop_ghz": args.required_sweep_stop_ghz,
            "target_frequency_ghz": args.target_frequency_ghz,
            "positive_window_start_ghz": args.positive_window_start_ghz,
            "positive_window_stop_ghz": args.positive_window_stop_ghz,
            "shape_window_start_ghz": args.shape_window_start_ghz,
            "shape_window_stop_ghz": args.shape_window_stop_ghz,
            "max_shape_spike_ratio": args.max_shape_spike_ratio,
            "max_shape_relative_step": args.max_shape_relative_step,
            "max_reciprocity_error": args.max_reciprocity_error,
            "max_passivity_sigma": args.max_passivity_sigma,
        },
    }


def _write_rows_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "touchstone_path",
        "overall_status",
        "port_count",
        "freq_start_hz",
        "freq_stop_hz",
        "freq_step_hz",
        "freq_points",
        "target_freq_hz",
        "target_lp_nh",
        "target_ls_nh",
        "target_k",
        "target_qp",
        "target_qs",
        "reciprocity_error_abs_max",
        "passivity_sigma_max",
        "passivity_excess_max",
        "fail_checks",
        "report",
        "summary",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            freq = item.get("frequency", {})
            target = item.get("target_point", {})
            matrix_quality = item.get("matrix_quality", {})
            writer.writerow(
                {
                    "touchstone_path": item.get("touchstone_path"),
                    "overall_status": item.get("overall_status"),
                    "port_count": item.get("port_count"),
                    "freq_start_hz": freq.get("start_hz"),
                    "freq_stop_hz": freq.get("stop_hz"),
                    "freq_step_hz": freq.get("step_hz"),
                    "freq_points": freq.get("points"),
                    "target_freq_hz": target.get("freq_hz"),
                    "target_lp_nh": target.get("lp_nh"),
                    "target_ls_nh": target.get("ls_nh"),
                    "target_k": target.get("k"),
                    "target_qp": target.get("qp"),
                    "target_qs": target.get("qs"),
                    "reciprocity_error_abs_max": matrix_quality.get("reciprocity_error_abs_max"),
                    "passivity_sigma_max": matrix_quality.get("passivity_sigma_max"),
                    "passivity_excess_max": matrix_quality.get("passivity_excess_max"),
                    "fail_checks": " | ".join(item.get("fail_checks", [])),
                    "report": item.get("report"),
                    "summary": item.get("summary"),
                }
            )


def _render_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Dataset Touchstone Preflight Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset root: `{summary['dataset_root']}`",
        f"- Discovered Touchstone files: `{summary['discovered_count']}`",
        f"- Audited files: `{summary['audited_count']}`",
        f"- PASS/FAIL: `{summary['pass_count']}` / `{summary['fail_count']}`",
        "",
        "## Network Physical Sanity",
        "",
        f"- Matrix quality summary: `{summary.get('matrix_quality_summary', {})}`",
        f"- Failure reason counts: `{summary.get('failure_reason_counts', {})}`",
        "",
        "| Status | Touchstone | Frequency | Target metrics | Fail checks |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in results:
        freq = item.get("frequency", {})
        target = item.get("target_point", {})
        matrix_quality = item.get("matrix_quality", {})
        freq_text = f"{freq.get('start_hz')} - {freq.get('stop_hz')} Hz, points={freq.get('points')}"
        target_text = (
            f"Lp={target.get('lp_nh')}, Ls={target.get('ls_nh')}, "
            f"K={target.get('k')}, Qp={target.get('qp')}, Qs={target.get('qs')}; "
            f"rec={matrix_quality.get('reciprocity_error_abs_max')}, "
            f"sigma={matrix_quality.get('passivity_sigma_max')}"
        )
        fail_text = "<br>".join(item.get("fail_checks", [])) if item.get("fail_checks") else ""
        lines.append(
            f"| {item.get('overall_status')} | `{item.get('touchstone_path')}` | {freq_text} | {target_text} | {fail_text} |"
        )
    lines.extend(
        [
            "",
            "This is a sampled Touchstone preflight. It verifies file usability and ADS-style extraction assumptions, but it does not replace EMX-vs-HFSS or ADS-vs-EMX curve-error validation.",
        ]
    )
    return "\n".join(lines) + "\n"


def _failure_reason_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in results:
        for check in item.get("fail_checks", []):
            name = str(check).split(":", 1)[0].strip()
            if name:
                counter[name] += 1
    return dict(sorted(counter.items()))


def _matrix_quality_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality = [item.get("matrix_quality", {}) for item in results if item.get("matrix_quality")]
    if not quality:
        return {
            "audited_with_matrix_quality": 0,
            "reciprocity_error_abs_max": None,
            "passivity_sigma_max": None,
            "passivity_excess_max": None,
        }
    reciprocity = [_as_float(item.get("reciprocity_error_abs_max")) for item in quality]
    sigma = [_as_float(item.get("passivity_sigma_max")) for item in quality]
    excess = [_as_float(item.get("passivity_excess_max")) for item in quality]
    return {
        "audited_with_matrix_quality": len(quality),
        "reciprocity_error_abs_max": _max_finite(reciprocity),
        "passivity_sigma_max": _max_finite(sigma),
        "passivity_excess_max": _max_finite(excess),
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _max_finite(values: list[float | None]) -> float | None:
    finite = [value for value in values if value is not None]
    return max(finite) if finite else None


def _append_optional_float(cmd: list[str], flag: str, value: float | None) -> None:
    if value is not None:
        cmd.extend([flag, str(float(value))])


def _append_optional_int(cmd: list[str], flag: str, value: int | None) -> None:
    if value is not None:
        cmd.extend([flag, str(int(value))])


def _resolve(root: Path, text: str) -> Path:
    path = Path(text).expanduser()
    return path if path.is_absolute() else root / path


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "ok", ""}


def _safe_sample_name(path: Path) -> str:
    parts = [part for part in path.parts[-4:] if part not in {"/", ""}]
    return "_".join(parts).replace(".", "p").replace("/", "_")


if __name__ == "__main__":
    raise SystemExit(main())
