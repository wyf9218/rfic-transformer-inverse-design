#!/usr/bin/env python3
"""Diagnose whether S8P EMX/HFSS mismatch is explained by HFSS port pairing.

This diagnostic keeps the EMX port-pair convention fixed and sweeps possible
HFSS two-differential-pair choices from an 8-port Touchstone file. It is meant
to answer a narrow validation question: whether a simple HFSS port order or
polarity mistake can explain a large EMX/HFSS L/Q/K discrepancy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
for item in (REPO_ROOT, SCRIPT_DIR):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from compare_emx_hfss_ads import (  # noqa: E402
    METRICS,
    compare_curves,
    load_touchstone_curves,
    parse_port_pairs,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


TARGET_METRICS = ("lp_nh", "ls_nh", "q", "k", "kw")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    emx_path = Path(args.emx).expanduser().resolve()
    hfss_path = Path(args.hfss).expanduser().resolve()
    _check_touchstone_contract(emx_path, int(args.expected_ports))
    _check_touchstone_contract(hfss_path, int(args.expected_ports))

    emx_curves = load_touchstone_curves(
        emx_path,
        port_pairs=parse_port_pairs(args.emx_port_pairs),
        ground_unused_ports=bool(args.ground_unused_ports),
    )
    pair_specs = list(_all_pair_specs(int(args.expected_ports)))
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for hfss_pair_spec in pair_specs:
        try:
            hfss_curves = load_touchstone_curves(
                hfss_path,
                port_pairs=parse_port_pairs(hfss_pair_spec),
                ground_unused_ports=bool(args.ground_unused_ports),
            )
            result = compare_curves(
                emx_curves,
                hfss_curves,
                max_percent_error=float(args.max_percent_error),
                compare_start_hz=_ghz_to_hz(args.compare_start_ghz),
                compare_stop_hz=_ghz_to_hz(args.compare_stop_ghz),
                min_frequency_points=int(args.expected_frequency_points),
                expected_frequency_step_hz=_ghz_to_hz(args.expected_frequency_step_ghz),
                expected_frequency_points=int(args.expected_frequency_points),
                frequency_tolerance_hz=float(args.frequency_tolerance_hz),
                require_matching_frequency_grid=True,
                target_hz=_ghz_to_hz(args.target_ghz),
                target_frequency_tolerance_hz=_ghz_to_hz(args.target_frequency_tolerance_ghz),
            )
            rows.append(_row_from_result(hfss_pair_spec, result, args))
        except Exception as exc:  # noqa: BLE001 - exact candidate failure is evidence.
            errors.append({"hfss_port_pairs": hfss_pair_spec, "error": f"{type(exc).__name__}: {exc}"})
    rows.sort(key=lambda row: (row["target_target_metric_sum_percent_error"], row["full_window_target_metric_sum_percent_error"]))
    target_best = rows[0] if rows else None
    full_window_rows = sorted(
        rows,
        key=lambda row: (
            row["full_window_target_metric_sum_percent_error"],
            row["target_target_metric_sum_percent_error"],
        ),
    )
    full_window_best = full_window_rows[0] if full_window_rows else None
    target_pass = bool(target_best and target_best["target_all_target_metrics_pass"])
    full_window_pass = bool(full_window_best and full_window_best["full_window_all_target_metrics_pass"])
    if full_window_pass:
        overall_status = "PASS"
        decision = "PORT_PAIRING_CAN_EXPLAIN_FULL_WINDOW_MISMATCH"
    elif target_pass:
        overall_status = "TARGET_ONLY_PASS_FULL_WINDOW_FAIL"
        decision = "PORT_PAIRING_ONLY_MATCHES_TARGET_MARKER_NOT_FULL_CURVE"
    else:
        overall_status = "FAIL"
        decision = "PORT_PAIRING_DOES_NOT_EXPLAIN_MISMATCH"

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "emx": str(emx_path),
        "hfss": str(hfss_path),
        "emx_port_pairs": str(args.emx_port_pairs),
        "ground_unused_ports": bool(args.ground_unused_ports),
        "criterion": {
            "max_percent_error": float(args.max_percent_error),
            "target_ghz": float(args.target_ghz),
            "frequency_grid": {
                "start_ghz": float(args.compare_start_ghz),
                "stop_ghz": float(args.compare_stop_ghz),
                "step_ghz": float(args.expected_frequency_step_ghz),
                "points": int(args.expected_frequency_points),
            },
        },
        "candidate_count": len(pair_specs),
        "completed_count": len(rows),
        "error_count": len(errors),
        "best": target_best,
        "target_best": target_best,
        "full_window_best": full_window_best,
        "expected_hfss": next((row for row in rows if row["hfss_port_pairs"] == str(args.expected_hfss_port_pairs)), None),
        "rows": rows,
        "full_window_sorted_rows": full_window_rows,
        "errors": errors,
        "limitations": [
            "This sweeps HFSS port-pair/order choices only; it does not modify EMX, HFSS geometry, materials, ports, or solver setup.",
            "A target-only pass can be a local 15 GHz coincidence and is not valid evidence for 5-60 GHz curve agreement.",
            "A FAIL means the mismatch is not explained by a simple HFSS pair/order/polarity swap under the fixed EMX port-pair convention.",
            "Final EMX reliability still requires a real HFSS-exported .s8p that passes the full postrun validation gate.",
        ],
    }
    summary_path = out_dir / "s8p_emx_hfss_port_pairing_sensitivity_summary.json"
    csv_path = out_dir / "s8p_emx_hfss_port_pairing_sensitivity.csv"
    report_path = out_dir / "s8p_emx_hfss_port_pairing_sensitivity_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(csv_path, rows)
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={summary['overall_status']}")
    print(f"decision={summary['decision']}")
    if rows:
        best = rows[0]
        print(f"best_hfss_port_pairs={best['hfss_port_pairs']}")
        print(f"best_target_sum_percent_error={best['target_target_metric_sum_percent_error']:.6g}")
        print(f"best_target_max_percent_error={best['target_target_metric_max_percent_error']:.6g}")
    print(f"summary={summary_path}")
    print(f"csv={csv_path}")
    print(f"report={report_path}")
    return 2 if summary["overall_status"] != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emx", required=True)
    parser.add_argument("--hfss", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--emx-port-pairs", default="1,4:5,6")
    parser.add_argument("--expected-hfss-port-pairs", default="1,4:5,6")
    parser.add_argument("--expected-ports", type=int, default=8)
    parser.add_argument("--ground-unused-ports", action="store_true")
    parser.add_argument("--compare-start-ghz", type=float, default=5.0)
    parser.add_argument("--compare-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--target-ghz", type=float, default=15.0)
    parser.add_argument("--target-frequency-tolerance-ghz", type=float, default=0.05)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _all_pair_specs(n_ports: int) -> Iterable[str]:
    ports = tuple(range(1, n_ports + 1))
    specs: set[str] = set()
    for selected in combinations(ports, 4):
        selected_set = set(selected)
        for first in combinations(selected, 2):
            remaining = tuple(sorted(selected_set.difference(first)))
            second = (remaining[0], remaining[1])
            if min(first) > min(second):
                continue
            for oriented_first in _orientations(first):
                for oriented_second in _orientations(second):
                    specs.add(f"{oriented_first[0]},{oriented_first[1]}:{oriented_second[0]},{oriented_second[1]}")
                    specs.add(f"{oriented_second[0]},{oriented_second[1]}:{oriented_first[0]},{oriented_first[1]}")
    return sorted(specs)


def _orientations(pair: tuple[int, int]) -> Iterable[tuple[int, int]]:
    yield pair
    yield (pair[1], pair[0])


def _row_from_result(hfss_pair_spec: str, result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    metric_errors = {metric: float((result.get("metrics") or {}).get(metric, {}).get("max_percent_error", math.inf)) for metric in METRICS}
    target = result.get("target_marker") if isinstance(result.get("target_marker"), dict) else {}
    target_metrics = target.get("metrics") if isinstance(target.get("metrics"), dict) else {}
    target_errors = {
        metric: float(target_metrics.get(metric, {}).get("percent_error", math.inf))
        for metric in METRICS
    }
    target_pass = {
        metric: target_metrics.get(metric, {}).get("status") == "PASS" and target_errors[metric] <= float(args.max_percent_error)
        for metric in TARGET_METRICS
    }
    full_pass = {
        metric: (result.get("metrics") or {}).get(metric, {}).get("status") == "PASS" and metric_errors[metric] <= float(args.max_percent_error)
        for metric in TARGET_METRICS
    }
    return {
        "hfss_port_pairs": hfss_pair_spec,
        "overall_status": str(result.get("overall_status")),
        "target_status": str(target.get("status")),
        "target_all_target_metrics_pass": all(target_pass.values()),
        "full_window_all_target_metrics_pass": all(full_pass.values()),
        "target_target_metric_sum_percent_error": float(sum(target_errors[metric] for metric in TARGET_METRICS)),
        "target_target_metric_max_percent_error": float(max(target_errors[metric] for metric in TARGET_METRICS)),
        "full_window_target_metric_sum_percent_error": float(sum(metric_errors[metric] for metric in TARGET_METRICS)),
        "full_window_target_metric_max_percent_error": float(max(metric_errors[metric] for metric in TARGET_METRICS)),
        **{f"target_{metric}_percent_error": target_errors[metric] for metric in METRICS},
        **{f"full_{metric}_max_percent_error": metric_errors[metric] for metric in METRICS},
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    best = summary.get("target_best") or summary.get("best") or {}
    full_best = summary.get("full_window_best") or {}
    expected = summary.get("expected_hfss") or {}
    lines = [
        "# S8P EMX/HFSS Port-Pairing Sensitivity",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- EMX port pairs fixed as: `{summary['emx_port_pairs']}`",
        f"- Ground unused ports: `{summary['ground_unused_ports']}`",
        f"- Candidate HFSS pairings tested: `{summary['completed_count']}`",
        f"- Pass gate: `{summary['criterion']['max_percent_error']}%` at `{summary['criterion']['target_ghz']} GHz` for Lp/Ls/Q/K/Kw",
        "",
        "## Best Candidate",
        "",
        "This section is sorted by the target-frequency marker only; it is not sufficient for final validation unless the full-window result also passes.",
        "",
        f"- HFSS port pairs: `{best.get('hfss_port_pairs', '')}`",
        f"- Target max percent error: `{_fmt(best.get('target_target_metric_max_percent_error'))}%`",
        f"- Target sum percent error: `{_fmt(best.get('target_target_metric_sum_percent_error'))}%`",
        f"- Full-window max percent error: `{_fmt(best.get('full_window_target_metric_max_percent_error'))}%`",
        f"- Full-window all metrics pass: `{best.get('full_window_all_target_metrics_pass', '')}`",
        "",
        "## Best Full-Window Candidate",
        "",
        f"- HFSS port pairs: `{full_best.get('hfss_port_pairs', '')}`",
        f"- Full-window max percent error: `{_fmt(full_best.get('full_window_target_metric_max_percent_error'))}%`",
        f"- Full-window sum percent error: `{_fmt(full_best.get('full_window_target_metric_sum_percent_error'))}%`",
        f"- Target max percent error: `{_fmt(full_best.get('target_target_metric_max_percent_error'))}%`",
        f"- Full-window all metrics pass: `{full_best.get('full_window_all_target_metrics_pass', '')}`",
        "",
        "## Expected HFSS Pairing",
        "",
        f"- HFSS port pairs: `{expected.get('hfss_port_pairs', '')}`",
        f"- Target max percent error: `{_fmt(expected.get('target_target_metric_max_percent_error'))}%`",
        f"- Target sum percent error: `{_fmt(expected.get('target_target_metric_sum_percent_error'))}%`",
        "",
        "## Lowest-Error HFSS Pairings",
        "",
        "| Rank | HFSS pairs | Target pass | Target max % | Target sum % | Lp % | Ls % | Q % | K % | Kw % |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, row in enumerate((summary.get("rows") or [])[:20], start=1):
        lines.append(
            f"| {rank} | `{row['hfss_port_pairs']}` | {row['target_all_target_metrics_pass']} | "
            f"{row['target_target_metric_max_percent_error']:.4g} | {row['target_target_metric_sum_percent_error']:.4g} | "
            f"{row['target_lp_nh_percent_error']:.4g} | {row['target_ls_nh_percent_error']:.4g} | "
            f"{row['target_q_percent_error']:.4g} | {row['target_k_percent_error']:.4g} | {row['target_kw_percent_error']:.4g} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary.get("limitations") or [])
    return "\n".join(lines) + "\n"


def _check_touchstone_contract(path: Path, expected_ports: int) -> None:
    if path.suffix.lower() != ".s8p":
        raise ValueError(f"expected .s8p file, got {path}")
    touchstone = load_touchstone(path)
    if int(touchstone.num_ports) != int(expected_ports):
        raise ValueError(f"expected {expected_ports} ports, got {touchstone.num_ports}: {path}")


def _ghz_to_hz(value: float | None) -> float | None:
    return None if value is None else float(value) * 1.0e9


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
