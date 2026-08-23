#!/usr/bin/env python3
"""Audit whether current EMX/HFSS S4P metrics match the user-provided ADS reference photo."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from plot_emx_hfss_ads_style_metrics import DEFAULT_PACKAGE_DIR, _extract_metric_curves  # noqa: E402


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    expected: float
    unit: str
    max_percent_error: float


REFERENCE_METRICS = (
    # Values transcribed from the user-provided "correct ADS" photo at 15 GHz.
    MetricSpec("lp_nh", "Lp", 0.8843, "nH", 10.0),
    MetricSpec("ls_nh", "Ls", 0.8183, "nH", 10.0),
    MetricSpec("k", "K", -0.512, "", 10.0),
    MetricSpec("qp", "Qp", 16.113, "", 10.0),
    MetricSpec("qs", "Qs", 14.243, "", 10.0),
    MetricSpec("cm_single_primary_ff", "Cm single primary", 95.43, "fF", 10.0),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx-s4p", default=str(DEFAULT_PACKAGE_DIR / "ec6698dfc575950b_EMX_reference_NARROWBAND_13p5_16p5GHz.s4p"))
    parser.add_argument("--hfss-s4p", default=str(DEFAULT_PACKAGE_DIR / "ec6698dfc575950b_HFSS_WIDEBAND_0p1_50GHz_step0p1.s4p"))
    parser.add_argument("--out-dir", default=str(DEFAULT_PACKAGE_DIR / "ads_photo_reference_alignment_20260613"))
    parser.add_argument("--port-pairs", default="1,2:3,4")
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--max-percent-error", type=float, default=None, help="Override all per-metric percent-error tolerances")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    emx_path = Path(args.emx_s4p).expanduser().resolve()
    hfss_path = Path(args.hfss_s4p).expanduser().resolve()

    sources = [
        _source_record("EMX", emx_path, args),
        _source_record("HFSS", hfss_path, args),
    ]
    status_counts: dict[str, int] = {}
    for source in sources:
        for check in source["checks"]:
            status_counts[check["status"]] = status_counts.get(check["status"], 0) + 1
    overall_status = "PASS" if status_counts.get("FAIL", 0) == 0 else "FAIL"
    summary = {
        "overall_status": overall_status,
        "target_ghz": float(args.target_ghz),
        "port_pairs": args.port_pairs,
        "reference_source": "user-provided ADS correct-curve photo, values transcribed from visible 15 GHz markers",
        "limits": {
            "default_max_percent_error": float(args.max_percent_error) if args.max_percent_error is not None else None,
            "per_metric_defaults": {spec.key: spec.max_percent_error for spec in REFERENCE_METRICS},
        },
        "sources": sources,
        "status_counts": status_counts,
        "notes": [
            "This audit is a guardrail against accepting plots that visibly disagree with the user's ADS reference photo.",
            "It is not a replacement for ADS GUI simulation or the original ADS dataset file.",
            "A FAIL here means the current S4P source should not be used as the report's correct EMX/HFSS reference until the source mismatch is resolved.",
        ],
    }
    summary_path = out_dir / "ads_photo_reference_alignment_summary.json"
    report_path = out_dir / "ads_photo_reference_alignment_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for source in sources:
        failed = [check for check in source["checks"] if check["status"] == "FAIL"]
        print(f"{source['label']}: {len(failed)} FAIL / {len(source['checks'])} checks")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _source_record(label: str, path: Path, args: argparse.Namespace) -> dict[str, Any]:
    curves = _extract_metric_curves(label, path, args.port_pairs)
    freq_ghz = curves.freq_hz / 1.0e9
    idx = int(np.argmin(np.abs(freq_ghz - float(args.target_ghz))))
    actuals = {
        "lp_nh": float(curves.lp_nh[idx]),
        "ls_nh": float(curves.ls_nh[idx]),
        "k": float(curves.k[idx]),
        "qp": float(curves.qp[idx]),
        "qs": float(curves.qs[idx]),
        "cm_single_primary_ff": float(curves.cm_single_primary_ff[idx]),
    }
    checks = [_metric_check(spec, actuals[spec.key], args.max_percent_error) for spec in REFERENCE_METRICS]
    return {
        "label": label,
        "touchstone": str(path),
        "nearest_frequency_ghz": float(freq_ghz[idx]),
        "actuals": actuals,
        "checks": checks,
    }


def _metric_check(spec: MetricSpec, actual: float, override_limit: float | None) -> dict[str, Any]:
    limit = float(override_limit) if override_limit is not None else float(spec.max_percent_error)
    percent_error = _percent_error(spec.expected, actual)
    status = "PASS" if percent_error <= limit else "FAIL"
    return {
        "status": status,
        "metric": spec.key,
        "label": spec.label,
        "expected": float(spec.expected),
        "actual": float(actual),
        "unit": spec.unit,
        "abs_error": float(abs(actual - spec.expected)),
        "percent_error": float(percent_error),
        "max_percent_error": limit,
    }


def _percent_error(expected: float, actual: float) -> float:
    return abs(float(actual) - float(expected)) / max(abs(float(expected)), 1.0e-30) * 100.0


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# ADS Photo Reference Alignment Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Target frequency: `{summary['target_ghz']} GHz`",
        f"- Port pairs: `{summary['port_pairs']}`",
        f"- Reference source: {summary['reference_source']}",
        "",
        "## Checks",
        "",
    ]
    for source in summary["sources"]:
        lines.extend(
            [
                f"### {source['label']}",
                "",
                f"- Touchstone: `{source['touchstone']}`",
                f"- Nearest frequency: `{source['nearest_frequency_ghz']} GHz`",
                "",
                "| Status | Metric | Expected | Actual | Error | Limit |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for check in source["checks"]:
            unit = f" {check['unit']}" if check["unit"] else ""
            lines.append(
                f"| {check['status']} | {check['label']} | {check['expected']:.6g}{unit} | "
                f"{check['actual']:.6g}{unit} | {check['percent_error']:.2f}% | "
                f"{check['max_percent_error']:.2f}% |"
            )
        lines.append("")
    lines.extend(["## Boundary", ""])
    lines.extend(f"- {note}" for note in summary["notes"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
