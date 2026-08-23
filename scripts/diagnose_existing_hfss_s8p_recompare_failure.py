#!/usr/bin/env python3
"""Diagnose why existing historical HFSS .s8p files failed the EMX gate.

This script reads the strict recompare output already produced by
``compare_emx_hfss_ads.py`` runs.  It does not re-run ADS, EMX, or HFSS.  The
goal is to turn many failing comparison folders into a compact engineering
diagnosis that points to likely next HFSS fixes: port polarity/order,
ground/reference setup, geometry scale, or material/layer setup.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_STRICT_RECOMPARE_SUMMARY = (
    PROJECT_ROOT
    / "outputs"
    / "existing_hfss_s8p_strict_recompare_current"
    / "existing_hfss_s8p_strict_recompare_summary.json"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "existing_hfss_s8p_failure_diagnosis_current"
CORE_TARGET_METRICS = ("lp_nh", "ls_nh", "q", "k", "kw")
INDUCTANCE_METRICS = ("lp_nh", "ls_nh")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    strict_summary_path = Path(args.strict_recompare_summary).expanduser().resolve()
    strict_summary = _read_json(strict_summary_path)
    records = _diagnose_records(strict_summary, args)
    summary = _build_summary(strict_summary_path, strict_summary, records, args, out_dir)

    summary_path = out_dir / "existing_hfss_s8p_failure_diagnosis_summary.json"
    report_path = out_dir / "EXISTING_HFSS_S8P_FAILURE_DIAGNOSIS_CN.md"
    csv_path = out_dir / "existing_hfss_s8p_failure_diagnosis_records.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_records_csv(csv_path, records)

    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    print(f"candidate_count={summary['candidate_count']}")
    print(f"pass_count={summary['pass_count']}")
    print(f"dominant_failure_modes={','.join(summary['dominant_failure_modes'])}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"records_csv={csv_path}")
    return 0 if summary["overall_status"] in {"PASS", "DIAGNOSIS_READY"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-recompare-summary", default=str(DEFAULT_STRICT_RECOMPARE_SUMMARY))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--small-inductance-ratio-threshold", type=float, default=0.25)
    parser.add_argument("--large-inductance-ratio-threshold", type=float, default=4.0)
    parser.add_argument("--max-records-in-report", type=int, default=10)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _diagnose_records(strict_summary: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    by_hfss: dict[str, dict[str, Any]] = {}
    for record in strict_summary.get("records") or []:
        if isinstance(record, dict) and record.get("hfss_s8p"):
            by_hfss[str(record["hfss_s8p"])] = dict(record)
    for record in strict_summary.get("target15_records") or []:
        if isinstance(record, dict) and record.get("hfss_s8p"):
            merged = by_hfss.setdefault(str(record["hfss_s8p"]), {})
            merged.update(record)

    diagnosed: list[dict[str, Any]] = []
    for index, record in enumerate(by_hfss.values(), start=1):
        target_rows = _read_target_marker_rows(Path(str(record.get("out_dir", ""))))
        target = _target_values(target_rows)
        ratios = _hfss_to_emx_ratios(target)
        sign_mismatches = _sign_mismatches(target)
        modes = _failure_modes(target, ratios, sign_mismatches, record, args)
        diagnosed.append(
            {
                "rank": index,
                "hfss_s8p": record.get("hfss_s8p", ""),
                "out_dir": record.get("out_dir", ""),
                "overall_status": record.get("overall_status", ""),
                "worst_metric": record.get("worst_metric", ""),
                "worst_percent_error": _number(record.get("worst_percent_error")),
                "target_worst_metric": record.get("target15_worst_metric", ""),
                "target_worst_percent_error": _number(record.get("target15_worst_percent_error")),
                "target_core_percent_errors": record.get("target15_core_percent_errors") or {},
                "target_values": target,
                "hfss_to_emx_ratios": ratios,
                "sign_mismatches": sign_mismatches,
                "failure_modes": modes,
                "primary_failure_mode": modes[0] if modes else "UNCLASSIFIED_FAILURE",
            }
        )
    return sorted(
        diagnosed,
        key=lambda item: (
            float("inf") if item.get("target_worst_percent_error") is None else float(item["target_worst_percent_error"]),
            float("inf") if item.get("worst_percent_error") is None else float(item["worst_percent_error"]),
            str(item.get("hfss_s8p", "")),
        ),
    )


def _read_target_marker_rows(out_dir: Path) -> list[dict[str, str]]:
    path = out_dir / "emx_hfss_ads_target_marker_metrics.csv"
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _target_values(rows: list[dict[str, str]]) -> dict[str, dict[str, float | str | None]]:
    result: dict[str, dict[str, float | str | None]] = {}
    for row in rows:
        metric = str(row.get("metric") or "")
        if not metric:
            continue
        result[metric] = {
            "status": row.get("metric_status") or row.get("status") or "",
            "emx": _number(row.get("emx")),
            "hfss": _number(row.get("hfss_ads")),
            "abs_error": _number(row.get("abs_error")),
            "percent_error": _number(row.get("percent_error")),
        }
    return result


def _hfss_to_emx_ratios(target: dict[str, dict[str, float | str | None]]) -> dict[str, float | None]:
    ratios: dict[str, float | None] = {}
    for metric in CORE_TARGET_METRICS:
        emx = _number((target.get(metric) or {}).get("emx"))
        hfss = _number((target.get(metric) or {}).get("hfss"))
        ratios[metric] = None if emx is None or abs(emx) < 1.0e-15 or hfss is None else abs(hfss) / abs(emx)
    return ratios


def _sign_mismatches(target: dict[str, dict[str, float | str | None]]) -> dict[str, bool]:
    mismatches: dict[str, bool] = {}
    for metric in CORE_TARGET_METRICS:
        emx = _number((target.get(metric) or {}).get("emx"))
        hfss = _number((target.get(metric) or {}).get("hfss"))
        mismatches[metric] = bool(emx is not None and hfss is not None and abs(emx) > 1.0e-15 and abs(hfss) > 1.0e-15 and emx * hfss < 0)
    return mismatches


def _failure_modes(
    target: dict[str, dict[str, float | str | None]],
    ratios: dict[str, float | None],
    sign_mismatches: dict[str, bool],
    record: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    modes: list[str] = []
    lp_ratio = ratios.get("lp_nh")
    ls_ratio = ratios.get("ls_nh")
    if _below(lp_ratio, args.small_inductance_ratio_threshold) and _below(ls_ratio, args.small_inductance_ratio_threshold):
        modes.append("HFSS_INDUCTANCE_SCALE_TOO_SMALL_CHECK_GEOMETRY_UNITS_OR_METAL_STACK")
    elif _above(lp_ratio, args.large_inductance_ratio_threshold) or _above(ls_ratio, args.large_inductance_ratio_threshold):
        modes.append("HFSS_INDUCTANCE_SCALE_TOO_LARGE_CHECK_GEOMETRY_UNITS_OR_AIRBOX")
    if sign_mismatches.get("k") or sign_mismatches.get("kw"):
        modes.append("COUPLING_SIGN_MISMATCH_CHECK_PORT_ORDER_POLARITY_WINDING_DIRECTION")
    q_hfss = _number((target.get("q") or {}).get("hfss"))
    qp_hfss = _number((target.get("qp") or {}).get("hfss"))
    qs_hfss = _number((target.get("qs") or {}).get("hfss"))
    if any(value is not None and value <= 0 for value in (q_hfss, qp_hfss, qs_hfss)):
        modes.append("NON_POSITIVE_Q_CHECK_LOSS_MODEL_TERMINAL_REFERENCE_OR_GROUND")
    target_errors = record.get("target15_core_percent_errors") if isinstance(record.get("target15_core_percent_errors"), dict) else {}
    if target_errors and all(_number(target_errors.get(metric)) is not None and float(target_errors[metric]) > float(args.max_percent_error) for metric in ("lp_nh", "ls_nh", "k")):
        modes.append("CORE_LP_LS_K_ALL_FAIL_STRUCTURE_OR_PORT_MAPPING_NOT_EQUIVALENT")
    if not modes:
        modes.append("UNCLASSIFIED_FAILURE_REVIEW_CURVES_AND_PORT_DEFINITION")
    return modes


def _build_summary(
    strict_summary_path: Path,
    strict_summary: dict[str, Any],
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    out_dir: Path,
) -> dict[str, Any]:
    pass_count = int(strict_summary.get("pass_count") or 0)
    mode_counts = _mode_counts(records)
    target_errors_by_metric = _target_error_statistics(records)
    ratio_stats = _ratio_statistics(records)
    sign_counts = _sign_mismatch_counts(records)
    overall_status = "PASS" if pass_count > 0 else "DIAGNOSIS_READY"
    decision = "HISTORICAL_HFSS_RECOMPARE_HAS_PASSING_CANDIDATE" if pass_count > 0 else "HISTORICAL_HFSS_RECOMPARE_FAILURE_DIAGNOSED_DO_NOT_UNLOCK_MILLION"
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "out_dir": str(out_dir),
        "strict_recompare_summary": str(strict_summary_path),
        "candidate_count": int(strict_summary.get("candidate_count") or len(records)),
        "diagnosed_record_count": len(records),
        "pass_count": pass_count,
        "max_percent_error_limit": float(strict_summary.get("max_percent_error_limit") or args.max_percent_error),
        "target_frequency_ghz": float(args.target_frequency_ghz),
        "best_full_band": strict_summary.get("best") or {},
        "best_target_marker": strict_summary.get("target15_best") or {},
        "dominant_failure_modes": [mode for mode, _ in sorted(mode_counts.items(), key=lambda item: (-item[1], item[0]))[:5]],
        "failure_mode_counts": mode_counts,
        "target_error_statistics": target_errors_by_metric,
        "hfss_to_emx_ratio_statistics": ratio_stats,
        "sign_mismatch_counts": sign_counts,
        "top_records": records[: max(0, int(args.max_records_in_report))],
        "diagnosis_notes": _diagnosis_notes(mode_counts, ratio_stats, sign_counts),
    }


def _mode_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for mode in record.get("failure_modes") or []:
            counts[str(mode)] = counts.get(str(mode), 0) + 1
    return counts


def _target_error_statistics(records: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    for metric in CORE_TARGET_METRICS:
        values = [
            _number((record.get("target_core_percent_errors") or {}).get(metric))
            for record in records
            if isinstance(record.get("target_core_percent_errors"), dict)
        ]
        result[metric] = _stats([value for value in values if value is not None])
    return result


def _ratio_statistics(records: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    for metric in CORE_TARGET_METRICS:
        values = [
            _number((record.get("hfss_to_emx_ratios") or {}).get(metric))
            for record in records
            if isinstance(record.get("hfss_to_emx_ratios"), dict)
        ]
        result[metric] = _stats([value for value in values if value is not None])
    return result


def _sign_mismatch_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {metric: 0 for metric in CORE_TARGET_METRICS}
    for record in records:
        mismatches = record.get("sign_mismatches") if isinstance(record.get("sign_mismatches"), dict) else {}
        for metric in CORE_TARGET_METRICS:
            if mismatches.get(metric):
                result[metric] += 1
    return result


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return {"min": None, "median": None, "max": None}
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "max": ordered[-1],
    }


def _diagnosis_notes(
    mode_counts: dict[str, int],
    ratio_stats: dict[str, dict[str, float | None]],
    sign_counts: dict[str, int],
) -> list[str]:
    notes: list[str] = []
    lp_median = _number((ratio_stats.get("lp_nh") or {}).get("median"))
    ls_median = _number((ratio_stats.get("ls_nh") or {}).get("median"))
    if lp_median is not None and ls_median is not None and lp_median < 0.25 and ls_median < 0.25:
        notes.append("At the target marker, historical HFSS inductance magnitudes are systematically far smaller than EMX, which points first to HFSS geometry units, missing metal stack thickness, or unintended short/reference geometry.")
    if sign_counts.get("k", 0) > 0 or sign_counts.get("kw", 0) > 0:
        notes.append("Coupling sign mismatch appears in at least one candidate, so port order, winding direction, and differential pair polarity must be checked before trusting any curve comparison.")
    if mode_counts.get("NON_POSITIVE_Q_CHECK_LOSS_MODEL_TERMINAL_REFERENCE_OR_GROUND", 0) > 0:
        notes.append("Non-positive Q appears in the HFSS-derived metrics; this is a red flag for terminal/reference/ground setup or an invalid lossy port extraction rather than a normal transformer response.")
    notes.append("These diagnostics are evidence only; a new current-gate HFSS .s8p must still pass the EMX/HFSS <=10% postrun validator before million-sample generation is unlocked.")
    return notes


def _write_records_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "hfss_s8p",
        "overall_status",
        "worst_metric",
        "worst_percent_error",
        "target_worst_metric",
        "target_worst_percent_error",
        "primary_failure_mode",
        "failure_modes",
        "lp_ratio",
        "ls_ratio",
        "k_ratio",
        "q_ratio",
        "lp_percent_error",
        "ls_percent_error",
        "k_percent_error",
        "q_percent_error",
        "k_sign_mismatch",
        "q_sign_mismatch",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            ratios = record.get("hfss_to_emx_ratios") if isinstance(record.get("hfss_to_emx_ratios"), dict) else {}
            errors = record.get("target_core_percent_errors") if isinstance(record.get("target_core_percent_errors"), dict) else {}
            signs = record.get("sign_mismatches") if isinstance(record.get("sign_mismatches"), dict) else {}
            writer.writerow(
                {
                    "rank": record.get("rank"),
                    "hfss_s8p": record.get("hfss_s8p"),
                    "overall_status": record.get("overall_status"),
                    "worst_metric": record.get("worst_metric"),
                    "worst_percent_error": record.get("worst_percent_error"),
                    "target_worst_metric": record.get("target_worst_metric"),
                    "target_worst_percent_error": record.get("target_worst_percent_error"),
                    "primary_failure_mode": record.get("primary_failure_mode"),
                    "failure_modes": ";".join(record.get("failure_modes") or []),
                    "lp_ratio": ratios.get("lp_nh"),
                    "ls_ratio": ratios.get("ls_nh"),
                    "k_ratio": ratios.get("k"),
                    "q_ratio": ratios.get("q"),
                    "lp_percent_error": errors.get("lp_nh"),
                    "ls_percent_error": errors.get("ls_nh"),
                    "k_percent_error": errors.get("k"),
                    "q_percent_error": errors.get("q"),
                    "k_sign_mismatch": signs.get("k"),
                    "q_sign_mismatch": signs.get("q"),
                }
            )


def _render_report(summary: dict[str, Any]) -> str:
    best = summary.get("best_target_marker") if isinstance(summary.get("best_target_marker"), dict) else {}
    lines = [
        "# Existing HFSS S8P Failure Diagnosis",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Candidate count: `{summary['candidate_count']}`",
        f"- Diagnosed records: `{summary['diagnosed_record_count']}`",
        f"- Historical pass count: `{summary['pass_count']}`",
        f"- Target frequency: `{summary['target_frequency_ghz']}` GHz",
        f"- Best target-marker worst error: `{best.get('target15_worst_percent_error')}` %",
        f"- Best target-marker worst metric: `{best.get('target15_worst_metric')}`",
        "",
        "## Dominant Failure Modes",
        "",
    ]
    for mode, count in sorted(summary["failure_mode_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{mode}`: `{count}`")
    lines.extend(["", "## Target-Marker Error Statistics (%)", ""])
    for metric, stats in summary["target_error_statistics"].items():
        lines.append(f"- `{metric}`: min `{stats['min']}`, median `{stats['median']}`, max `{stats['max']}`")
    lines.extend(["", "## HFSS / EMX Magnitude Ratio At Target", ""])
    for metric, stats in summary["hfss_to_emx_ratio_statistics"].items():
        lines.append(f"- `{metric}`: min `{stats['min']}`, median `{stats['median']}`, max `{stats['max']}`")
    lines.extend(["", "## Sign Mismatch Counts", ""])
    for metric, count in summary["sign_mismatch_counts"].items():
        lines.append(f"- `{metric}`: `{count}`")
    lines.extend(["", "## Best Historical Candidates By 15GHz Error", ""])
    for record in summary["top_records"]:
        lines.append(
            f"- `{Path(str(record.get('hfss_s8p', ''))).name}`: target worst `{record.get('target_worst_percent_error')}` %, mode `{record.get('primary_failure_mode')}`"
        )
    lines.extend(["", "## Diagnosis Notes", ""])
    lines.extend(f"- {item}" for item in summary["diagnosis_notes"])
    return "\n".join(lines) + "\n"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _below(value: float | None, threshold: float) -> bool:
    return value is not None and value < float(threshold)


def _above(value: float | None, threshold: float) -> bool:
    return value is not None and value > float(threshold)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


if __name__ == "__main__":
    raise SystemExit(main())
