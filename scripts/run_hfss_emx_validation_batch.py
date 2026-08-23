#!/usr/bin/env python3
"""Plan or run sampled HFSS-vs-EMX comparisons.

This script consumes the CSV produced by `select_hfss_validation_samples.py`.
It does not build HFSS models. It either:

- writes a traceable checklist and compare commands while HFSS exports are still
  missing, or
- runs `compare_emx_hfss_ads.py` for every selected sample whose HFSS/ADS file
  is available.

Use it after sampled HFSS rebuilds/exported .s4p files exist. A PASS from this
script means every requested sampled comparison passed the configured curve gate;
it is still scoped to the selected samples, not the whole dataset.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMPARE_DEFAULTS = {
    "compare_start_ghz": 5.0,
    "compare_stop_ghz": 60.0,
    "min_frequency_points": 111,
    "expected_frequency_step_ghz": 0.5,
    "expected_frequency_points": 111,
    "frequency_tolerance_hz": 1.0e5,
    "max_percent_error": 5.0,
}

TOUCHSTONE_OR_CSV_SUFFIXES = {".csv", ".s2p", ".s4p", ".s8p"}


@dataclass(frozen=True)
class Sample:
    rank: int
    evaluation: str
    emx_path: Path | None
    reasons: str


@dataclass(frozen=True)
class CompareRecord:
    rank: int
    evaluation: str
    status: str
    emx_path: str
    hfss_path: str
    compare_out_dir: str
    summary_path: str
    returncode: int | None
    worst_metric: str
    worst_percent_error: float | None
    no_extrapolation_status: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "evaluation": self.evaluation,
            "status": self.status,
            "emx_path": self.emx_path,
            "hfss_path": self.hfss_path,
            "compare_out_dir": self.compare_out_dir,
            "summary_path": self.summary_path,
            "returncode": self.returncode,
            "worst_metric": self.worst_metric,
            "worst_percent_error": self.worst_percent_error,
            "no_extrapolation_status": self.no_extrapolation_status,
            "detail": self.detail,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selection_csv = Path(args.selection_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    compare_script = _default_compare_script(args.compare_script)
    samples = _read_samples(selection_csv)
    hfss_map = _read_hfss_map(Path(args.hfss_map_csv).expanduser().resolve()) if args.hfss_map_csv else {}
    hfss_dir = Path(args.hfss_dir).expanduser().resolve() if args.hfss_dir else None
    records: list[CompareRecord] = []

    for sample in samples:
        emx_path = sample.emx_path
        hfss_path = _resolve_hfss_path(sample, hfss_map, hfss_dir)
        sample_out = out_dir / "comparisons" / f"{sample.rank:02d}_{_slug(sample.evaluation)}"
        if emx_path is None or not emx_path.exists():
            records.append(
                _record(
                    sample,
                    "MISSING_EMX",
                    emx_path,
                    hfss_path,
                    sample_out,
                    detail="EMX/reference Touchstone or metric CSV is missing",
                )
            )
            continue
        if hfss_path is None or not hfss_path.exists():
            records.append(
                _record(
                    sample,
                    "MISSING_HFSS",
                    emx_path,
                    hfss_path,
                    sample_out,
                    detail="HFSS/ADS Touchstone or metric CSV is missing",
                )
            )
            continue
        if not args.run_available:
            records.append(_record(sample, "READY", emx_path, hfss_path, sample_out, detail="ready to compare"))
            continue
        records.append(_run_one_compare(sample, emx_path, hfss_path, sample_out, compare_script, args))

    status = _overall_status(records, args)
    summary = _build_summary(selection_csv, out_dir, compare_script, records, status, args)
    summary_path = out_dir / "hfss_emx_validation_batch_summary.json"
    report_path = out_dir / "hfss_emx_validation_batch_report.md"
    results_csv = out_dir / "hfss_emx_validation_batch_results.csv"
    missing_csv = out_dir / "hfss_emx_validation_missing_files.csv"
    commands_path = out_dir / "hfss_emx_validation_batch_commands.sh"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_records_csv(results_csv, records)
    _write_missing_csv(missing_csv, records)
    _write_commands(commands_path, records, compare_script, args)

    print(f"overall_status={status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"results_csv={results_csv}")
    print(f"missing_csv={missing_csv}")
    print(f"commands={commands_path}")
    print(f"sample_count={len(samples)}")
    print(f"ready_or_pass_count={sum(1 for item in records if item.status in {'READY', 'PASS'})}")
    return 2 if status in {"FAIL", "NOT_READY"} and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-csv", required=True, help="CSV from select_hfss_validation_samples.py")
    parser.add_argument("--hfss-dir", help="Directory containing sampled HFSS/ADS .s4p/.s2p or metric CSV files")
    parser.add_argument("--hfss-map-csv", help="Optional CSV with columns evaluation,hfss_path")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-script", help="Path to compare_emx_hfss_ads.py; defaults to this script's directory")
    parser.add_argument("--run-available", action="store_true", help="Run compare_emx_hfss_ads.py for samples with both files")
    parser.add_argument("--require-all-present", action="store_true", help="FAIL if any selected EMX or HFSS file is missing")
    parser.add_argument("--require-all-pass", action="store_true", help="FAIL unless all selected samples were compared and passed")
    parser.add_argument("--emx-port-pairs", default="1,4:5,6")
    parser.add_argument("--hfss-port-pairs", default="1,4:5,6")
    parser.add_argument("--compare-start-ghz", type=float, default=COMPARE_DEFAULTS["compare_start_ghz"])
    parser.add_argument("--compare-stop-ghz", type=float, default=COMPARE_DEFAULTS["compare_stop_ghz"])
    parser.add_argument("--min-frequency-points", type=int, default=COMPARE_DEFAULTS["min_frequency_points"])
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=COMPARE_DEFAULTS["expected_frequency_step_ghz"])
    parser.add_argument("--expected-frequency-points", type=int, default=COMPARE_DEFAULTS["expected_frequency_points"])
    parser.add_argument("--frequency-tolerance-hz", type=float, default=COMPARE_DEFAULTS["frequency_tolerance_hz"])
    parser.add_argument("--max-percent-error", type=float, default=COMPARE_DEFAULTS["max_percent_error"])
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _default_compare_script(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().with_name("compare_emx_hfss_ads.py")


def _read_samples(path: Path) -> list[Sample]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    samples: list[Sample] = []
    for index, row in enumerate(rows, start=1):
        evaluation = row.get("evaluation") or row.get("sample_id") or f"sample_{index:03d}"
        rank = _as_int(row.get("rank"), default=index)
        emx_raw = row.get("touchstone_path") or row.get("emx_path") or row.get("emx_touchstone_path")
        emx_path = _resolve_existing_or_candidate(emx_raw, base_dirs=[path.parent, Path.cwd()]) if emx_raw else None
        samples.append(
            Sample(
                rank=rank,
                evaluation=str(evaluation),
                emx_path=emx_path,
                reasons=row.get("selection_reasons") or row.get("reasons") or "",
            )
        )
    return sorted(samples, key=lambda item: (item.rank, item.evaluation))


def _read_hfss_map(path: Path) -> dict[str, Path]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, Path] = {}
    for row in rows:
        evaluation = row.get("evaluation") or row.get("sample_id") or row.get("id")
        raw_path = row.get("hfss_path") or row.get("hfss_touchstone_path") or row.get("ads_metrics_path")
        if evaluation and raw_path:
            result[str(evaluation)] = _resolve_existing_or_candidate(raw_path, base_dirs=[path.parent, Path.cwd()])
    return result


def _resolve_hfss_path(sample: Sample, hfss_map: dict[str, Path], hfss_dir: Path | None) -> Path | None:
    mapped = hfss_map.get(sample.evaluation)
    if mapped is not None:
        return mapped
    if hfss_dir is None or not hfss_dir.exists():
        return None

    slug = _slug(sample.evaluation)
    direct_names = [
        f"{sample.evaluation}.s4p",
        f"{sample.evaluation}.s2p",
        f"{sample.evaluation}.csv",
        f"{slug}.s4p",
        f"{slug}.s2p",
        f"{slug}.csv",
        f"{sample.evaluation}_hfss.s4p",
        f"{sample.evaluation}_HFSS.s4p",
        f"{sample.rank:02d}_{slug}.s4p",
        f"{sample.rank:02d}_{slug}.csv",
    ]
    for name in direct_names:
        candidate = hfss_dir / name
        if candidate.exists():
            return candidate.resolve()

    tokens = {sample.evaluation.lower(), slug.lower()}
    matches = []
    for candidate in hfss_dir.rglob("*"):
        if candidate.is_file() and _is_curve_file(candidate):
            name = candidate.name.lower()
            if any(token and token in name for token in tokens):
                matches.append(candidate)
    return sorted(matches)[0].resolve() if matches else None


def _resolve_existing_or_candidate(raw: str | Path, *, base_dirs: list[Path]) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    for base_dir in base_dirs:
        candidate = (base_dir / path).resolve()
        if candidate.exists():
            return candidate
    return (base_dirs[0] / path).resolve()


def _is_curve_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in TOUCHSTONE_OR_CSV_SUFFIXES or bool(re.search(r"\.s\d+p$", path.name.lower()))


def _run_one_compare(
    sample: Sample,
    emx_path: Path,
    hfss_path: Path,
    sample_out: Path,
    compare_script: Path,
    args: argparse.Namespace,
) -> CompareRecord:
    sample_out.mkdir(parents=True, exist_ok=True)
    cmd = _compare_command(compare_script, emx_path, hfss_path, sample_out, args)
    completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    summary_path = sample_out / "emx_hfss_ads_comparison_summary.json"
    if not summary_path.exists():
        detail = (completed.stderr or completed.stdout or f"returncode={completed.returncode}").strip()
        return _record(
            sample,
            "FAIL",
            emx_path,
            hfss_path,
            sample_out,
            summary_path=summary_path,
            returncode=completed.returncode,
            detail=detail[:500],
        )
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _record(
            sample,
            "FAIL",
            emx_path,
            hfss_path,
            sample_out,
            summary_path=summary_path,
            returncode=completed.returncode,
            detail=f"could not parse compare summary: {type(exc).__name__}: {exc}",
        )
    worst_metric, worst_error = _worst_metric(summary)
    no_extrapolation_status = _no_extrapolation_status(summary)
    summary_failures = _compare_summary_failures(sample, emx_path, hfss_path, summary, args)
    if completed.returncode != 0:
        summary_failures.append(f"returncode={completed.returncode}")
    status = "PASS" if not summary_failures else "FAIL"
    return _record(
        sample,
        status,
        emx_path,
        hfss_path,
        sample_out,
        summary_path=summary_path,
        returncode=completed.returncode,
        worst_metric=worst_metric,
        worst_percent_error=worst_error,
        no_extrapolation_status=no_extrapolation_status,
        detail=(
            f"compare overall_status={summary.get('overall_status')}; ADS no-extrapolation coverage={no_extrapolation_status}"
            if not summary_failures
            else "; ".join(summary_failures[:10])
        ),
    )


def _compare_command(compare_script: Path, emx_path: Path, hfss_path: Path, out_dir: Path, args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(compare_script),
        "--emx",
        str(emx_path),
        "--hfss",
        str(hfss_path),
        "--out-dir",
        str(out_dir),
        "--emx-port-pairs",
        args.emx_port_pairs,
        "--hfss-port-pairs",
        args.hfss_port_pairs,
        "--compare-start-ghz",
        f"{args.compare_start_ghz:g}",
        "--compare-stop-ghz",
        f"{args.compare_stop_ghz:g}",
        "--min-frequency-points",
        str(args.min_frequency_points),
        "--expected-frequency-step-ghz",
        f"{args.expected_frequency_step_ghz:g}",
        "--expected-frequency-points",
        str(args.expected_frequency_points),
        "--frequency-tolerance-hz",
        f"{args.frequency_tolerance_hz:g}",
        "--require-matching-frequency-grid",
        "--max-percent-error",
        f"{args.max_percent_error:g}",
        "--no-fail-exit",
    ]
    if args.plot:
        cmd.append("--plot")
    return cmd


def _record(
    sample: Sample,
    status: str,
    emx_path: Path | None,
    hfss_path: Path | None,
    sample_out: Path,
    *,
    summary_path: Path | None = None,
    returncode: int | None = None,
    worst_metric: str = "",
    worst_percent_error: float | None = None,
    no_extrapolation_status: str = "",
    detail: str,
) -> CompareRecord:
    return CompareRecord(
        rank=sample.rank,
        evaluation=sample.evaluation,
        status=status,
        emx_path=str(emx_path) if emx_path is not None else "",
        hfss_path=str(hfss_path) if hfss_path is not None else "",
        compare_out_dir=str(sample_out),
        summary_path=str(summary_path or (sample_out / "emx_hfss_ads_comparison_summary.json")),
        returncode=returncode,
        worst_metric=worst_metric,
        worst_percent_error=worst_percent_error,
        no_extrapolation_status=no_extrapolation_status,
        detail=detail,
    )


def _worst_metric(summary: dict[str, Any]) -> tuple[str, float | None]:
    worst_metric = ""
    worst_error: float | None = None
    for metric, item in (summary.get("metrics") or {}).items():
        error = item.get("max_percent_error")
        if isinstance(error, (int, float)) and (worst_error is None or float(error) > worst_error):
            worst_metric = str(metric)
            worst_error = float(error)
    return worst_metric, worst_error


def _no_extrapolation_status(summary: dict[str, Any]) -> str:
    item = (summary.get("frequency_grid_checks") or {}).get("ADS no-extrapolation coverage") or {}
    return str(item.get("status") or "MISSING")


def _compare_summary_failures(
    sample: Sample,
    emx_path: Path,
    hfss_path: Path,
    summary: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    prefix = sample.evaluation
    failures: list[str] = []
    if summary.get("overall_status") != "PASS":
        failures.append(f"{prefix}: compare_overall_status={summary.get('overall_status')}")
    failures.extend(_compare_source_failures(prefix, emx_path, hfss_path, summary))
    criterion = summary.get("criterion", {}) if isinstance(summary.get("criterion"), dict) else {}
    criterion_max = _as_float_or_none(criterion.get("max_percent_error"))
    if criterion_max is None or criterion_max > float(args.max_percent_error):
        failures.append(f"{prefix}: criterion_max_percent_error={criterion.get('max_percent_error')}")
    freq = summary.get("frequency_window_hz", {}) if isinstance(summary.get("frequency_window_hz"), dict) else {}
    expected_start = float(args.compare_start_ghz) * 1.0e9
    expected_stop = float(args.compare_stop_ghz) * 1.0e9
    tolerance_hz = float(args.frequency_tolerance_hz)
    start = _as_float_or_none(freq.get("min"))
    stop = _as_float_or_none(freq.get("max"))
    count = _as_int(freq.get("count"), default=-1)
    if start is None or abs(start - expected_start) > tolerance_hz:
        failures.append(f"{prefix}: compare_window_start={freq.get('min')}")
    if stop is None or abs(stop - expected_stop) > tolerance_hz:
        failures.append(f"{prefix}: compare_window_stop={freq.get('max')}")
    if count != int(args.expected_frequency_points):
        failures.append(f"{prefix}: compare_window_count={freq.get('count')}")
    grid_checks = summary.get("frequency_grid_checks", {}) if isinstance(summary.get("frequency_grid_checks"), dict) else {}
    for name in (
        "ADS no-extrapolation coverage",
        "expected frequency points",
        "expected frequency step",
        "matching HFSS/ADS frequency grid",
    ):
        status = (grid_checks.get(name) or {}).get("status") if isinstance(grid_checks.get(name), dict) else None
        if status != "PASS":
            failures.append(f"{prefix}: {name}={status}")
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    for name in ("k", "qp", "qs", "lp_nh", "ls_nh"):
        item = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
        status = item.get("status")
        if status != "PASS":
            failures.append(f"{prefix}: metric_{name}={status}")
        max_percent_error = _as_float_or_none(item.get("max_percent_error"))
        if max_percent_error is None or max_percent_error > float(args.max_percent_error):
            failures.append(f"{prefix}: metric_{name}_max_percent_error={item.get('max_percent_error')}")
    return failures


def _compare_source_failures(prefix: str, emx_path: Path, hfss_path: Path, summary: dict[str, Any]) -> list[str]:
    checks = (
        ("emx", emx_path, summary.get("emx_source")),
        ("hfss", hfss_path, summary.get("hfss_ads_source")),
    )
    failures: list[str] = []
    for label, expected_path, summary_path in checks:
        if not summary_path:
            failures.append(f"{prefix}: summary_{label}_source_missing")
            continue
        if _normalized_path_text(expected_path) != _normalized_path_text(summary_path):
            failures.append(
                f"{prefix}: {label}_source_mismatch="
                f"expected:{_normalized_path_text(expected_path)} summary:{_normalized_path_text(summary_path)}"
            )
    return failures


def _normalized_path_text(raw_path: object) -> str:
    return str(Path(str(raw_path)).expanduser().resolve())


def _as_float_or_none(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _overall_status(records: list[CompareRecord], args: argparse.Namespace) -> str:
    if not records:
        return "NOT_READY"
    missing = [item for item in records if item.status in {"MISSING_EMX", "MISSING_HFSS"}]
    failed = [item for item in records if item.status == "FAIL"]
    passed = [item for item in records if item.status == "PASS"]
    ready = [item for item in records if item.status == "READY"]

    if args.require_all_present and missing:
        return "FAIL"
    if args.require_all_pass and (missing or failed or ready or not passed):
        return "FAIL"
    if failed:
        return "FAIL"
    if args.run_available:
        if len(passed) == len(records):
            return "PASS"
        if passed:
            return "PARTIAL"
        return "WAITING_FOR_HFSS" if missing else "READY"
    return "READY" if len(ready) == len(records) else "WAITING_FOR_HFSS"


def _build_summary(
    selection_csv: Path,
    out_dir: Path,
    compare_script: Path,
    records: list[CompareRecord],
    status: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "selection_csv": str(selection_csv),
        "out_dir": str(out_dir),
        "compare_script": str(compare_script),
        "sample_count": len(records),
        "status_counts": dict(sorted(counts.items())),
        "arguments": {
            "run_available": args.run_available,
            "require_all_present": args.require_all_present,
            "require_all_pass": args.require_all_pass,
            "emx_port_pairs": args.emx_port_pairs,
            "hfss_port_pairs": args.hfss_port_pairs,
            "compare_start_ghz": args.compare_start_ghz,
            "compare_stop_ghz": args.compare_stop_ghz,
            "min_frequency_points": args.min_frequency_points,
            "expected_frequency_step_ghz": args.expected_frequency_step_ghz,
            "expected_frequency_points": args.expected_frequency_points,
            "frequency_tolerance_hz": args.frequency_tolerance_hz,
            "max_percent_error": args.max_percent_error,
            "plot": args.plot,
        },
        "records": [record.as_dict() for record in records],
        "limitations": [
            "This batch runner does not generate HFSS models or ADS plots.",
            "WAITING_FOR_HFSS only means selected EMX samples were found but matching HFSS/ADS files are missing.",
            "A PASS is scoped to the selected samples and the configured metrics/frequency grid.",
            "Every PASS record must include ADS no-extrapolation coverage = PASS from compare_emx_hfss_ads.py.",
        ],
    }


def _write_records_csv(path: Path, records: list[CompareRecord]) -> None:
    fields = list(CompareRecord.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_dict())


def _write_missing_csv(path: Path, records: list[CompareRecord]) -> None:
    missing = [record for record in records if record.status in {"MISSING_EMX", "MISSING_HFSS"}]
    fields = ["rank", "evaluation", "status", "emx_path", "hfss_path", "detail"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in missing:
            writer.writerow({field: record.as_dict().get(field, "") for field in fields})


def _write_commands(path: Path, records: list[CompareRecord], compare_script: Path, args: argparse.Namespace) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated commands for sampled HFSS/EMX validation.",
        "# Fill missing HFSS paths first; do not use this file as proof until commands run and pass.",
        "",
    ]
    for record in records:
        lines.extend([f"# {record.rank}. {record.evaluation} [{record.status}]", ""])
        if not record.emx_path or not record.hfss_path:
            lines.append(f"# Missing input: {record.detail}")
            lines.append("")
            continue
        cmd = _compare_command(compare_script, Path(record.emx_path), Path(record.hfss_path), Path(record.compare_out_dir), args)
        lines.append(_shell_join(cmd[:-1] if cmd[-1] == "--no-fail-exit" else cmd))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o755)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# HFSS/EMX Validation Batch",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Selection CSV: `{summary['selection_csv']}`",
        f"- Sample count: `{summary['sample_count']}`",
        f"- Status counts: `{summary['status_counts']}`",
        "",
        "| Rank | Evaluation | Status | No extrapolation | Worst metric | Worst percent error | EMX | HFSS/ADS | Detail |",
        "| ---: | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for record in summary["records"]:
        worst = "" if record["worst_percent_error"] is None else f"{record['worst_percent_error']:.6g}"
        lines.append(
            f"| {record['rank']} | {record['evaluation']} | {record['status']} | {record.get('no_extrapolation_status', '')} | "
            f"{record['worst_metric']} | {worst} | `{record['emx_path']}` | `{record['hfss_path']}` | {record['detail']} |"
        )
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _shell_join(items: list[str]) -> str:
    return " \\\n  ".join(_quote(item) for item in items)


def _quote(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=,+-]+", text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _as_int(value: object, *, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _slug(text: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())
    return result.strip("._") or "sample"


if __name__ == "__main__":
    raise SystemExit(main())
