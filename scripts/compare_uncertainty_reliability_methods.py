#!/usr/bin/env python3
"""Compare KNN and deep-ensemble uncertainty on identical real EMX holdout.

This gate can approve an equal-budget acquisition ablation. It cannot switch
production, create labels, or claim that either uncertainty method improves
the final inverse model.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


READY = "ENSEMBLE_READY_FOR_EQUAL_BUDGET_REAL_EMX_ABLATION_ONLY"
REVIEW = "KEEP_KNN_BASELINE_AND_REVIEW_ENSEMBLE"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / "uncertainty_method_comparison_summary.json",
        "report": out_dir / "uncertainty_method_comparison_report.md",
    }
    knn_path = Path(args.knn_summary).expanduser().resolve()
    ensemble_path = Path(args.ensemble_summary).expanduser().resolve()
    knn = _load(knn_path)
    ensemble = _load(ensemble_path)
    ksplit = knn.get("split") if isinstance(knn.get("split"), dict) else {}
    esplit = ensemble.get("split") if isinstance(ensemble.get("split"), dict) else {}
    checks = {
        "summaries_exist": knn_path.is_file() and ensemble_path.is_file(),
        "summaries_complete": knn.get("overall_status") == "PASS"
        and ensemble.get("overall_status") == "PASS",
        "feature_contract_match": knn.get("feature_columns") == ensemble.get("feature_columns")
        and knn.get("feature_ranges") == ensemble.get("feature_ranges"),
        "train_geometry_match": bool(ksplit.get("train_group_sha256"))
        and ksplit.get("train_group_sha256") == esplit.get("train_group_sha256"),
        "train_real_target_match": bool(ksplit.get("train_real_target_sha256"))
        and ksplit.get("train_real_target_sha256") == esplit.get("train_real_target_sha256"),
        "train_count_match": int(ksplit.get("train_geometry_count") or 0) > 0
        and ksplit.get("train_geometry_count") == esplit.get("train_geometry_count"),
        "holdout_geometry_match": bool(ksplit.get("holdout_group_sha256"))
        and ksplit.get("holdout_group_sha256") == esplit.get("holdout_group_sha256"),
        "holdout_real_target_match": bool(ksplit.get("holdout_real_target_sha256"))
        and ksplit.get("holdout_real_target_sha256") == esplit.get("holdout_real_target_sha256"),
        "holdout_count_match": int(ksplit.get("holdout_geometry_count") or 0) > 0
        and ksplit.get("holdout_geometry_count") == esplit.get("holdout_geometry_count"),
    }
    km = knn.get("holdout_metrics") if isinstance(knn.get("holdout_metrics"), dict) else {}
    em = ensemble.get("holdout_metrics") if isinstance(ensemble.get("holdout_metrics"), dict) else {}
    required_metrics = (
        "aggregate_spearman_uncertainty_vs_error",
        "high_vs_low_uncertainty_error_ratio",
        "low_minus_high_uncertainty_mean_1d_bin_accuracy",
        "mean_range_normalized_absolute_error",
        "scaled_interval_empirical_coverage",
    )
    checks["required_metrics_finite"] = all(
        math.isfinite(_metric(metrics, key))
        for metrics in (km, em)
        for key in required_metrics
    )
    comparable = all(checks.values())
    deltas: dict[str, float] = {}
    adoption_checks: dict[str, bool] = {}
    if comparable:
        deltas = {
            "aggregate_spearman": _metric(em, "aggregate_spearman_uncertainty_vs_error")
            - _metric(km, "aggregate_spearman_uncertainty_vs_error"),
            "high_low_error_ratio": _metric(em, "high_vs_low_uncertainty_error_ratio")
            - _metric(km, "high_vs_low_uncertainty_error_ratio"),
            "low_high_bin_accuracy_delta": _metric(
                em,
                "low_minus_high_uncertainty_mean_1d_bin_accuracy",
            )
            - _metric(km, "low_minus_high_uncertainty_mean_1d_bin_accuracy"),
            "mean_range_normalized_error": _metric(em, "mean_range_normalized_absolute_error")
            - _metric(km, "mean_range_normalized_absolute_error"),
        }
        ensemble_error = _metric(em, "mean_range_normalized_absolute_error")
        knn_error = _metric(km, "mean_range_normalized_absolute_error")
        adoption_checks = {
            "ensemble_reliability_gate_passed": bool(
                ensemble.get("eligible_for_acquisition_ablation")
            ),
            "spearman_improves": deltas["aggregate_spearman"] >= float(args.min_spearman_delta),
            "high_low_ratio_not_worse": deltas["high_low_error_ratio"]
            >= -float(args.max_high_low_ratio_regression),
            "bin_accuracy_delta_not_worse": deltas["low_high_bin_accuracy_delta"]
            >= -float(args.max_bin_accuracy_delta_regression),
            "point_error_not_worse": ensemble_error
            <= knn_error * (1.0 + float(args.max_point_error_relative_regression)),
            "interval_coverage_adequate": _metric(em, "scaled_interval_empirical_coverage")
            >= float(args.min_interval_coverage),
        }
    ready = comparable and all(adoption_checks.values())
    overall = "PASS" if comparable else "WAITING"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": READY if ready else REVIEW,
        "eligible_for_equal_budget_real_emx_ablation": ready,
        "inputs": {"knn": str(knn_path), "deep_ensemble": str(ensemble_path)},
        "checks": checks,
        "adoption_checks": adoption_checks,
        "metric_deltas_ensemble_minus_knn": deltas,
        "holdout_geometry_count": ksplit.get("holdout_geometry_count") if comparable else 0,
        "holdout_group_sha256": ksplit.get("holdout_group_sha256") if comparable else "",
        "holdout_real_target_sha256": ksplit.get("holdout_real_target_sha256") if comparable else "",
        "thresholds": {
            "min_spearman_delta": float(args.min_spearman_delta),
            "max_high_low_ratio_regression": float(args.max_high_low_ratio_regression),
            "max_bin_accuracy_delta_regression": float(args.max_bin_accuracy_delta_regression),
            "max_point_error_relative_regression": float(args.max_point_error_relative_regression),
            "min_interval_coverage": float(args.min_interval_coverage),
        },
        "scientific_boundary": (
            "PASS only makes deep-ensemble uncertainty eligible for a future equal-budget real-EMX acquisition ablation. "
            "It does not authorize a production switch or count proxy predictions as labels."
        ),
    }
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["report"].write_text(_report(payload), encoding="utf-8")
    print(f"overall_status={overall}")
    print(f"decision={payload['decision']}")
    print(f"summary={paths['summary']}")
    return 0 if overall == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knn-summary", required=True)
    parser.add_argument("--ensemble-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-spearman-delta", type=float, default=0.05)
    parser.add_argument("--max-high-low-ratio-regression", type=float, default=0.10)
    parser.add_argument("--max-bin-accuracy-delta-regression", type=float, default=0.02)
    parser.add_argument("--max-point-error-relative-regression", type=float, default=0.05)
    parser.add_argument("--min-interval-coverage", type=float, default=0.82)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _metric(metrics: dict[str, Any], key: str) -> float:
    try:
        value = float(metrics.get(key))
    except (TypeError, ValueError):
        return math.nan
    return value


def _report(payload: dict[str, Any]) -> str:
    lines = [
        "# KNN vs Deep-Ensemble Uncertainty Reliability",
        "",
        f"- Status: `{payload['overall_status']}`",
        f"- Decision: `{payload['decision']}`",
        f"- Same real holdout rows: `{payload['checks'].get('holdout_real_target_match')}`",
        "",
        "| Metric delta | Ensemble - KNN |",
        "| --- | ---: |",
    ]
    for name, value in payload["metric_deltas_ensemble_minus_knn"].items():
        lines.append(f"| {name} | {value:.6g} |")
    lines.extend(["", "## Boundary", "", payload["scientific_boundary"], ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
