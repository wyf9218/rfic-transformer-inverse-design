#!/usr/bin/env python3
"""Diagnose EMX-HFSS variant error patterns and propose the next HFSS experiment."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

METRIC_COLUMNS = {
    "Lp": "lp_nh_err_pct",
    "Ls": "ls_nh_err_pct",
    "Q": "q_err_pct",
    "K": "k_err_pct",
    "Kw": "kw_err_pct",
    "Qp": "qp_err_pct",
    "Qs": "qs_err_pct",
}

FINAL_GATE_METRICS = ("Lp", "Ls", "Qp", "Qs", "Kw")
BIAS_VALUE_COLUMNS = {
    "Lp": ("emx_lp_nh", "hfss_lp_nh"),
    "Ls": ("emx_ls_nh", "hfss_ls_nh"),
    "Q": ("emx_q", "hfss_q"),
    "K": ("emx_k", "hfss_k"),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scan_csv = Path(args.scan_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_rows(scan_csv)
    payload = diagnose(rows, target_percent=float(args.target_percent))
    (out_dir / "hfss_variant_error_pattern_diagnosis.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    (out_dir / "HFSS_VARIANT_ERROR_PATTERN_DIAGNOSIS_CN.md").write_text(
        render_markdown(payload), encoding="utf-8"
    )
    print(f"status={payload['overall_status']}")
    print(f"report={out_dir / 'HFSS_VARIANT_ERROR_PATTERN_DIAGNOSIS_CN.md'}")
    print(f"json={out_dir / 'hfss_variant_error_pattern_diagnosis.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-percent", type=float, default=10.0)
    return parser.parse_args(argv)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["target_max_pct_float"] = _float(row.get("target_max_pct"))
        row["core_sum_pct_float"] = _float(row.get("core_sum_pct"))
        for metric, column in METRIC_COLUMNS.items():
            row[f"{metric.lower()}_err_float"] = _float(row.get(column))
        for metric, (emx_column, hfss_column) in BIAS_VALUE_COLUMNS.items():
            row[f"{metric.lower()}_emx_float"] = _float(row.get(emx_column))
            row[f"{metric.lower()}_hfss_float"] = _float(row.get(hfss_column))
            row[f"{metric.lower()}_hfss_over_emx_float"] = _ratio(
                row[f"{metric.lower()}_hfss_float"],
                row[f"{metric.lower()}_emx_float"],
            )
    return rows


def diagnose(rows: list[dict[str, Any]], *, target_percent: float) -> dict[str, Any]:
    if not rows:
        raise ValueError("No rows to diagnose")
    sorted_rows = sorted(rows, key=lambda row: row["target_max_pct_float"])
    best = sorted_rows[0]
    metric_best = {
        metric: min(rows, key=lambda row, key=f"{metric.lower()}_err_float": row[key])
        for metric in METRIC_COLUMNS
    }
    gate_errors = {
        metric: _finite_values([row[f"{metric.lower()}_err_float"] for row in rows])
        for metric in FINAL_GATE_METRICS
    }
    keyword_groups = {
        "finite local M5 / keep frame": ["keep_frame", "local_air", "ground_unused"],
        "open unused ports": ["open_unused"],
        "all-M5 or connected-M5 global reference": ["allm5", "all-M5", "connected_m5", "connected-M5"],
        "terminal-reference payload": ["terminal_reference", "terminal-reference", "terminal_local"],
        "BEOL dielectric window": ["beol", "dielectric"],
        "no M5 frame": ["no_frame"],
        "modal ports": ["modal"],
    }
    groups = {
        label: summarize_group(rows, needles)
        for label, needles in keyword_groups.items()
    }
    recommendations = build_recommendations(best, metric_best, groups, target_percent)
    return {
        "overall_status": "PASS" if best["target_max_pct_float"] <= target_percent else "FAIL",
        "target_percent": target_percent,
        "variant_count": len(rows),
        "best_overall": _row_brief(best),
        "best_by_metric": {metric: _row_brief(row) for metric, row in metric_best.items()},
        "final_gate_metric_floor": {
            metric: {
                "min_error_pct": min(values) if values else float("inf"),
                "median_error_pct": _median(values),
                "any_variant_within_gate": bool(values and min(values) <= target_percent),
            }
            for metric, values in gate_errors.items()
        },
        "systematic_bias_hfss_over_emx": {
            metric: summarize_bias(rows, metric)
            for metric in BIAS_VALUE_COLUMNS
        },
        "groups": groups,
        "recommendations": recommendations,
    }


def summarize_group(rows: list[dict[str, Any]], needles: list[str]) -> dict[str, Any]:
    matched = [
        row for row in rows
        if any(needle.lower() in str(row.get("variant", "")).lower() for needle in needles)
    ]
    if not matched:
        return {"count": 0, "best": None}
    best = min(matched, key=lambda row: row["target_max_pct_float"])
    return {
        "count": len(matched),
        "best": _row_brief(best),
        "median_target_max_pct": _median([row["target_max_pct_float"] for row in matched]),
        "median_lp_pct": _median([row["lp_err_float"] for row in matched]),
        "median_ls_pct": _median([row["ls_err_float"] for row in matched]),
        "median_q_pct": _median([row["q_err_float"] for row in matched]),
        "median_k_pct": _median([row["k_err_float"] for row in matched]),
        "median_kw_pct": _median([row["kw_err_float"] for row in matched]),
        "median_qp_pct": _median([row["qp_err_float"] for row in matched]),
        "median_qs_pct": _median([row["qs_err_float"] for row in matched]),
    }


def summarize_bias(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    key = f"{metric.lower()}_hfss_over_emx_float"
    values = _finite_values([row.get(key, float("inf")) for row in rows])
    if not values:
        return {
            "count": 0,
            "median_hfss_over_emx": None,
            "min_hfss_over_emx": None,
            "max_hfss_over_emx": None,
        }
    return {
        "count": len(values),
        "median_hfss_over_emx": _median(values),
        "min_hfss_over_emx": min(values),
        "max_hfss_over_emx": max(values),
    }


def build_recommendations(
    best: dict[str, Any],
    metric_best: dict[str, dict[str, Any]],
    groups: dict[str, dict[str, Any]],
    target_percent: float,
) -> list[dict[str, str]]:
    recs: list[dict[str, str]] = []
    if best["target_max_pct_float"] > target_percent:
        recs.append(
            {
                "name": "Do not launch 1M EMX generation yet",
                "reason": f"Best existing target max error is {best['target_max_pct_float']:.2f}%, above the {target_percent:.2f}% gate.",
            }
        )
    global_m5 = groups.get("all-M5 or connected-M5 global reference", {})
    finite_local = groups.get("finite local M5 / keep frame", {})
    if global_m5.get("best") and finite_local.get("best"):
        if global_m5["best"]["target_max_pct"] > finite_local["best"]["target_max_pct"]:
            recs.append(
                {
                    "name": "Avoid full all-M5/global-reference for the full transformer",
                    "reason": "Straight-line M5-united can improve port continuity, but full-transformer all-M5/global-reference variants are much worse than finite local-M5 keep-frame variants.",
                }
            )
    if metric_best["Q"]["q_err_float"] <= target_percent and best["ls_err_float"] > target_percent:
        recs.append(
            {
                "name": "Keep the v48/v52 finite-M5 local-air family as the baseline",
                "reason": "This family already gets Q near or below 10% in some variants, while Lp/Ls remain systematically low; the next experiment should target magnetic/self-inductance reference conditions instead of changing ADS formulas.",
            }
        )
    if best["lp_err_float"] > target_percent or best["ls_err_float"] > target_percent:
        recs.append(
            {
                "name": "Run a local-reference sweep, not another port-order scan",
                "reason": "The dominant residual errors are Lp/Ls and are stable across port-order and terminal-reference variants. Sweep M5 frame distance/port ground overlap/deembed length on a 15/15.5 GHz two-point run before another 5-60 GHz full solve.",
            }
        )
    if metric_best["K"]["k_err_float"] <= target_percent and metric_best["Lp"]["lp_err_float"] > target_percent:
        recs.append(
            {
                "name": "Treat K-only agreement as insufficient evidence",
                "reason": "At least one variant can bring K close while Lp/Ls stay far outside the gate, so the next accepted result must pass Lp/Ls/Qp/Qs/Kw together from the same S8P pair.",
            }
        )
    recs.append(
        {
            "name": "Complete Stage-1 Cadence EMX .s2p import first",
            "reason": "The next full HFSS variant should be chosen after comparing M9/M10 straight-line EMX against baseline and M5-united HFSS, so the local-ground fix is evidence-backed.",
        }
    )
    return recs


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# HFSS Variant Error Pattern Diagnosis",
        "",
        f"- Overall status: **{payload['overall_status']}**",
        f"- Variants scanned: {payload['variant_count']}",
        f"- Gate: <= {payload['target_percent']:.2f}% for Lp/Ls/Qp/Qs/Kw at 15 GHz",
        "",
        "## Best Overall",
        "",
        _render_brief(payload["best_overall"]),
        "",
        "## Best By Metric",
        "",
        "| Metric | Best variant | Error | Target max |",
        "|---|---|---:|---:|",
    ]
    for metric, row in payload["best_by_metric"].items():
        lines.append(
            f"| {metric} | `{row['variant']}` | {row[metric.lower() + '_error_pct']:.2f}% | {row['target_max_pct']:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Final Gate Metric Floors",
            "",
            "| Metric | Best error found | Median error | Any variant within gate |",
            "|---|---:|---:|---|",
        ]
    )
    for metric, item in payload["final_gate_metric_floor"].items():
        lines.append(
            f"| {metric} | {item['min_error_pct']:.2f}% | {item['median_error_pct']:.2f}% | {item['any_variant_within_gate']} |"
        )
    lines.extend(
        [
            "",
            "## Systematic HFSS/EMX Bias At 15 GHz",
            "",
            "| Metric | Median HFSS/EMX | Min | Max |",
            "|---|---:|---:|---:|",
        ]
    )
    for metric, item in payload["systematic_bias_hfss_over_emx"].items():
        if item["count"] == 0:
            lines.append(f"| {metric} |  |  |  |")
            continue
        lines.append(
            f"| {metric} | {item['median_hfss_over_emx']:.3f} | {item['min_hfss_over_emx']:.3f} | {item['max_hfss_over_emx']:.3f} |"
        )
    lines.extend(["", "## Group Signals", "", "| Group | Count | Best target max | Median target max | Best variant |", "|---|---:|---:|---:|---|"])
    for label, group in payload["groups"].items():
        if not group.get("best"):
            lines.append(f"| {label} | 0 |  |  |  |")
            continue
        lines.append(
            f"| {label} | {group['count']} | {group['best']['target_max_pct']:.2f}% | {group['median_target_max_pct']:.2f}% | `{group['best']['variant']}` |"
        )
    lines.extend(["", "## Recommendations", ""])
    for index, item in enumerate(payload["recommendations"], start=1):
        lines.append(f"{index}. **{item['name']}**: {item['reason']}")
    lines.append("")
    return "\n".join(lines)


def _render_brief(row: dict[str, Any]) -> str:
    return (
        f"- Variant: `{row['variant']}`\n"
        f"- Target max error: {row['target_max_pct']:.2f}%\n"
        f"- Lp/Ls/Q/K/Kw/Qp/Qs errors: {row['lp_error_pct']:.2f}% / {row['ls_error_pct']:.2f}% / "
        f"{row['q_error_pct']:.2f}% / {row['k_error_pct']:.2f}% / {row['kw_error_pct']:.2f}% / "
        f"{row['qp_error_pct']:.2f}% / {row['qs_error_pct']:.2f}%"
    )


def _row_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": row["rank"],
        "variant": row.get("variant", ""),
        "status": row.get("status", ""),
        "target_max_pct": row["target_max_pct_float"],
        "core_sum_pct": row["core_sum_pct_float"],
        "lp_error_pct": row["lp_err_float"],
        "ls_error_pct": row["ls_err_float"],
        "q_error_pct": row["q_err_float"],
        "k_error_pct": row["k_err_float"],
        "kw_error_pct": row["kw_err_float"],
        "qp_error_pct": row["qp_err_float"],
        "qs_error_pct": row["qs_err_float"],
        "hfss_source": row.get("hfss_source", ""),
        "summary": row.get("summary", ""),
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _ratio(numerator: float, denominator: float) -> float:
    if numerator == float("inf") or denominator == float("inf") or numerator <= 0.0 or denominator <= 0.0:
        return float("inf")
    return numerator / denominator


def _finite_values(values: list[float]) -> list[float]:
    return [value for value in values if value != float("inf")]


def _median(values: list[float]) -> float:
    values = sorted(_finite_values(values))
    if not values:
        return float("inf")
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return 0.5 * (values[mid - 1] + values[mid])


if __name__ == "__main__":
    raise SystemExit(main())
