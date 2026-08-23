#!/usr/bin/env python3
"""Evaluate equal-budget acquisition policies from returned real EMX S4P rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


ARMS = (
    "random",
    "geometry_kmeanspp",
    "physical_deficit",
    "deficit_uncertainty",
    "deficit_diversity",
    "hierarchical_gap",
)
FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
FEATURE_RANGES = np.asarray(((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8)), dtype=float)
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    baseline_path = Path(args.baseline_accepted_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    result_paths = _parse_arm_results(args.arm_result)
    planned = _load_planned_ids(plan, plan_path)
    baseline = _load_feature_rows(
        baseline_path,
        require_candidate_id=False,
        require_qp_qs=False,
        check_touchstone=False,
    )
    results = {
        arm: _load_arm_results(
            result_paths.get(arm, Path("__missing__")),
            arm,
            planned.get(arm, set()),
            bool(args.check_touchstone_exists),
        )
        for arm in ARMS
    }
    analysis = _evaluate(baseline, results, args)
    checks = _checks(plan, baseline, planned, result_paths, results, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    metrics_csv = out_dir / "equal_budget_real_emx_policy_metrics.csv"
    figure_path = out_dir / "equal_budget_real_emx_coverage_comparison.png"
    summary_path = out_dir / "equal_budget_real_emx_benchmark_summary.json"
    report_path = out_dir / "equal_budget_real_emx_benchmark_report.md"
    _write_metrics_csv(metrics_csv, analysis)
    if analysis.get("available") is True:
        _plot(figure_path, analysis)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "REAL_EMX_COVERAGE_BENCHMARK_COMPLETE_RUN_FIXED_MODEL_ABLATION"
            if status == "PASS"
            else "DO_NOT_COMPARE_POLICIES_FIX_REAL_EMX_EVIDENCE"
        ),
        "outcome_status": "REAL_EMX_COVERAGE_COMPLETE_MODEL_ABLATION_PENDING" if status == "PASS" else "INCOMPLETE",
        "plan_summary": str(plan_path),
        "plan_summary_sha256": _sha256(plan_path),
        "baseline_accepted_csv": str(baseline_path),
        "baseline_accepted_csv_sha256": _sha256(baseline_path),
        "arm_result_paths": {arm: str(path) for arm, path in result_paths.items()},
        "arm_result_sha256": {arm: _sha256(path) for arm, path in result_paths.items()},
        "checks": checks,
        "baseline_evidence": _public_counts(baseline),
        "arm_evidence": {arm: _public_counts(item) for arm, item in results.items()},
        "analysis": analysis,
        "artifacts": {
            "metrics_csv": str(metrics_csv),
            "coverage_figure": str(figure_path),
            "report_md": str(report_path),
        },
        "scientific_boundary": (
            "PASS means all six equal-budget arms returned traceable real EMX attempts and enough valid S4P labels "
            "for a coverage comparison. The coverage ranking is advisory. A policy may replace the production "
            "selector only after baseline-plus-arm datasets are retrained with the same model seed, physical-cell "
            "OOD split, and fixed-range error metric."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"outcome_status={payload['outcome_status']}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", required=True)
    parser.add_argument("--baseline-accepted-csv", required=True)
    parser.add_argument(
        "--arm-result",
        action="append",
        default=[],
        metavar="ARM=CSV",
        help=(
            "Repeat for random, geometry_kmeanspp, physical_deficit, deficit_uncertainty, "
            "deficit_diversity, and hierarchical_gap."
        ),
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-arm-budget", type=int)
    parser.add_argument("--min-success-fraction", type=float, default=0.95)
    parser.add_argument("--marginal-bins", type=int, default=10)
    parser.add_argument("--pair-bins", type=int, default=10)
    parser.add_argument("--four-d-bins", type=int, default=4)
    parser.add_argument("--ranking-weight-grid-step", type=float, default=0.10)
    parser.add_argument("--bootstrap-replicates", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260712)
    parser.add_argument("--minimum-robust-top-fraction", type=float, default=0.60)
    parser.add_argument("--check-touchstone-exists", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 <= args.min_success_fraction <= 1.0:
        parser.error("--min-success-fraction must be in [0, 1]")
    if min(args.marginal_bins, args.pair_bins, args.four_d_bins) < 2:
        parser.error("all bin counts must be at least 2")
    reciprocal = 1.0 / float(args.ranking_weight_grid_step)
    if not 0.0 < args.ranking_weight_grid_step <= 1.0 or not math.isclose(
        reciprocal, round(reciprocal), rel_tol=0.0, abs_tol=1.0e-9
    ):
        parser.error("--ranking-weight-grid-step must be the reciprocal of a positive integer")
    if args.bootstrap_replicates < 1:
        parser.error("--bootstrap-replicates must be positive")
    if not 0.0 < args.minimum_robust_top_fraction <= 1.0:
        parser.error("--minimum-robust-top-fraction must be in (0,1]")
    return args


def _parse_arm_results(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        if "=" not in value:
            continue
        arm, raw_path = value.split("=", 1)
        arm = arm.strip()
        if arm in ARMS and arm not in result:
            result[arm] = Path(raw_path).expanduser().resolve()
    return result


def _load_planned_ids(plan: dict[str, Any], plan_path: Path) -> dict[str, set[str]]:
    paths = ((plan.get("artifacts") or {}).get("arm_candidate_csvs") or {})
    result = {}
    for arm in ARMS:
        path = Path(str(paths.get(arm) or "__missing__")).expanduser()
        if not path.is_absolute():
            path = (plan_path.parent / path).resolve()
        ids = set()
        if path.is_file():
            with path.open(newline="", encoding="utf-8-sig") as handle:
                ids = {str(row.get("candidate_id") or "") for row in csv.DictReader(handle) if row.get("candidate_id")}
        result[arm] = ids
    return result


def _load_feature_rows(
    path: Path,
    *,
    require_candidate_id: bool,
    require_qp_qs: bool,
    check_touchstone: bool,
) -> dict[str, Any]:
    result = {
        "row_count": 0,
        "attempted_count": 0,
        "ok_count": 0,
        "valid_feature_count": 0,
        "in_range_count": 0,
        "invalid_feature_count": 0,
        "touchstone_failure_count": 0,
        "duplicate_candidate_id_count": 0,
        "duplicate_geometry_count": 0,
        "features": np.empty((0, len(FEATURE_COLUMNS))),
        "predicted_features": np.empty((0, len(FEATURE_COLUMNS))),
        "geometry": np.empty((0, len(GEOMETRY_COLUMNS))),
        "candidate_ids": set(),
        "columns_present": False,
        "q_min_consistency_failure_count": 0,
        "missing_qp_qs_count": 0,
    }
    if not path.is_file():
        return result
    feature_rows = []
    predicted_rows = []
    geometry_rows = []
    geometry_digests: set[bytes] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(FEATURE_COLUMNS) | set(GEOMETRY_COLUMNS)
        if require_candidate_id:
            required.add("candidate_id")
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for row in reader:
            result["row_count"] += 1
            candidate_id = str(row.get("candidate_id") or "")
            if require_candidate_id:
                if candidate_id in result["candidate_ids"]:
                    result["duplicate_candidate_id_count"] += 1
                result["candidate_ids"].add(candidate_id)
            result["attempted_count"] += 1
            if not _truthy(row.get("ok", "true")):
                continue
            result["ok_count"] += 1
            geometry = _float_row(row, GEOMETRY_COLUMNS)
            features = _float_row(row, FEATURE_COLUMNS)
            if geometry is None or features is None:
                result["invalid_feature_count"] += 1
                continue
            qp = _finite(row.get("qp_center"))
            qs = _finite(row.get("qs_center"))
            if require_qp_qs and (qp is None or qs is None):
                result["missing_qp_qs_count"] += 1
                continue
            if qp is not None and qs is not None and not math.isclose(float(features[2]), min(qp, qs), rel_tol=1.0e-8, abs_tol=1.0e-8):
                result["q_min_consistency_failure_count"] += 1
                continue
            if check_touchstone and not _valid_touchstone(row, path.parent):
                result["touchstone_failure_count"] += 1
                continue
            digest = _vector_digest(geometry)
            if digest in geometry_digests:
                result["duplicate_geometry_count"] += 1
                continue
            geometry_digests.add(digest)
            predicted = _float_row(row, tuple(f"pred_{column}" for column in FEATURE_COLUMNS))
            if predicted is None:
                predicted = np.full(len(FEATURE_COLUMNS), np.nan)
            geometry_rows.append(geometry)
            feature_rows.append(features)
            predicted_rows.append(predicted)
            if _in_feature_range(features):
                result["in_range_count"] += 1
    if feature_rows:
        result["features"] = np.asarray(feature_rows, dtype=float)
        result["predicted_features"] = np.asarray(predicted_rows, dtype=float)
        result["geometry"] = np.asarray(geometry_rows, dtype=float)
    result["valid_feature_count"] = len(feature_rows)
    return result


def _load_arm_results(path: Path, arm: str, expected_ids: set[str], check_touchstone: bool) -> dict[str, Any]:
    result = _load_feature_rows(
        path,
        require_candidate_id=True,
        require_qp_qs=True,
        check_touchstone=check_touchstone,
    )
    actual_ids = result.get("candidate_ids") or set()
    result.update(
        {
            "arm": arm,
            "path": str(path),
            "path_exists": path.is_file(),
            "expected_candidate_count": len(expected_ids),
            "missing_candidate_ids": sorted(expected_ids - actual_ids)[:20],
            "missing_candidate_id_count": len(expected_ids - actual_ids),
            "unexpected_candidate_ids": sorted(actual_ids - expected_ids)[:20],
            "unexpected_candidate_id_count": len(actual_ids - expected_ids),
            "success_fraction": result["valid_feature_count"] / len(expected_ids) if expected_ids else 0.0,
        }
    )
    return result


def _evaluate(baseline: dict[str, Any], results: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    if baseline["valid_feature_count"] < 1 or any(item["valid_feature_count"] < 1 for item in results.values()):
        return {"available": False}
    baseline_in_range = baseline["features"][_in_range_mask(baseline["features"])]
    baseline_distribution = _distribution(baseline_in_range, args)
    arms = {}
    arm_in_range: dict[str, np.ndarray] = {}
    for arm, item in results.items():
        real = item["features"]
        in_range = real[_in_range_mask(real)]
        arm_in_range[arm] = in_range
        combined = np.vstack((baseline_in_range, in_range))
        distribution = _distribution(combined, args)
        predicted = item["predicted_features"]
        finite_prediction = np.isfinite(predicted).all(axis=1)
        calibration = _calibration(predicted[finite_prediction], real[finite_prediction]) if np.any(finite_prediction) else {}
        arms[arm] = {
            "attempted_count": item["attempted_count"],
            "valid_real_emx_count": item["valid_feature_count"],
            "real_emx_success_fraction": item["success_fraction"],
            "in_range_real_count": int(len(in_range)),
            "in_range_fraction_of_valid": float(len(in_range) / len(real)) if len(real) else 0.0,
            "distribution_after_adding_arm": distribution,
            "gain_vs_baseline": _distribution_gain(baseline_distribution, distribution),
            "proxy_calibration": calibration,
        }
        gain = arms[arm]["gain_vs_baseline"]
        arms[arm]["coverage_efficiency_score"] = float(
            0.45 * distribution["four_d"]["normalized_entropy"]
            + 0.35 * distribution["four_d"]["occupied_fraction"]
            + 0.20 * distribution["mean_pair_normalized_entropy"]
        )
        gain["coverage_efficiency_score_delta"] = arms[arm]["coverage_efficiency_score"] - float(
            0.45 * baseline_distribution["four_d"]["normalized_entropy"]
            + 0.35 * baseline_distribution["four_d"]["occupied_fraction"]
            + 0.20 * baseline_distribution["mean_pair_normalized_entropy"]
        )
    ranking = sorted(ARMS, key=lambda arm: (-arms[arm]["coverage_efficiency_score"], arm))
    weight_sensitivity = _weight_sensitivity(arms, float(args.ranking_weight_grid_step))
    bootstrap_sensitivity = _bootstrap_ranking_sensitivity(
        baseline_in_range,
        arm_in_range,
        args,
    )
    nominal_top = ranking[0]
    minimum_fraction = float(args.minimum_robust_top_fraction)
    nominal_top_robust = (
        float(weight_sensitivity["top_fraction"][nominal_top]) >= minimum_fraction
        and float(bootstrap_sensitivity["top_fraction"][nominal_top]) >= minimum_fraction
    )
    plausible = [
        arm
        for arm in ranking
        if float(weight_sensitivity["top_fraction"][arm]) > 0.0
        or float(bootstrap_sensitivity["top_fraction"][arm]) >= 0.10
    ]
    return {
        "available": True,
        "baseline_distribution": baseline_distribution,
        "arms": arms,
        "coverage_ranking": ranking,
        "nominal_advisory_policy": nominal_top,
        "advisory_policy_for_fixed_model_ablation": nominal_top if nominal_top_robust else None,
        "advisory_policies_for_fixed_model_ablation": plausible,
        "ranking_status": "ADVISORY_ONLY_MODEL_RETRAIN_REQUIRED",
        "ranking_robustness": {
            "minimum_top_fraction": minimum_fraction,
            "nominal_top_is_robust": nominal_top_robust,
            "decision": (
                "ONE_POLICY_MAY_ENTER_FIXED_MODEL_ABLATION"
                if nominal_top_robust
                else "NO_UNIQUE_POLICY_WINNER_RUN_PLAUSIBLE_POLICIES_IN_FIXED_MODEL_ABLATION"
            ),
            "weight_simplex_sensitivity": weight_sensitivity,
            "row_resampling_sensitivity": bootstrap_sensitivity,
            "scientific_boundary": (
                "Weight-simplex sensitivity tests arbitrary score weights. Row resampling is a heuristic "
                "stability analysis of the observed policy returns, not a formal confidence interval because "
                "actively selected candidates are not IID."
            ),
        },
    }


def _checks(
    plan: dict[str, Any],
    baseline: dict[str, Any],
    planned: dict[str, set[str]],
    paths: dict[str, Path],
    results: dict[str, dict[str, Any]],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    expected_budget = int(args.expected_arm_budget or plan.get("arm_budget") or 0)
    all_ids = [candidate_id for arm in ARMS for candidate_id in results[arm].get("candidate_ids", set())]
    geometry_digests = []
    for arm in ARMS:
        geometry_digests.extend(_vector_digest(row) for row in results[arm]["geometry"])
    return {
        "plan_is_ready_and_awaiting_real_emx": plan.get("overall_status") == "PASS"
        and plan.get("decision") == "RUN_EQUAL_BUDGET_REAL_EMX_BENCHMARK"
        and plan.get("outcome_status") == "AWAITING_REAL_EMX",
        "baseline_columns_present": baseline.get("columns_present") is True,
        "baseline_real_rows_present": int(baseline.get("valid_feature_count") or 0) > 0,
        "all_six_result_paths_present": set(paths) == set(ARMS) and all(path.is_file() for path in paths.values()),
        "all_planned_arm_ids_present": expected_budget > 0 and all(len(planned.get(arm, set())) == expected_budget for arm in ARMS),
        "all_result_columns_present": all(results[arm].get("columns_present") is True for arm in ARMS),
        "all_result_ids_exact": all(
            int(results[arm].get("missing_candidate_id_count") or 0) == 0
            and int(results[arm].get("unexpected_candidate_id_count") or 0) == 0
            and int(results[arm].get("duplicate_candidate_id_count") or 0) == 0
            for arm in ARMS
        ),
        "all_arms_meet_success_fraction": all(
            float(results[arm].get("success_fraction") or 0.0) >= float(args.min_success_fraction) for arm in ARMS
        ),
        "all_valid_rows_have_real_s4p": all(int(results[arm].get("touchstone_failure_count") or 0) == 0 for arm in ARMS),
        "q_min_semantics_consistent": all(
            int(results[arm].get("missing_qp_qs_count") or 0) == 0
            and int(results[arm].get("q_min_consistency_failure_count") or 0) == 0
            for arm in ARMS
        ),
        "candidate_ids_disjoint_across_arms": len(all_ids) == len(set(all_ids)),
        "geometry_disjoint_across_arms": len(geometry_digests) == len(set(geometry_digests)),
        "analysis_available": analysis.get("available") is True,
        "ranking_is_advisory": analysis.get("ranking_status") == "ADVISORY_ONLY_MODEL_RETRAIN_REQUIRED",
        "ranking_robustness_analyzed": bool(
            ((analysis.get("ranking_robustness") or {}).get("weight_simplex_sensitivity") or {}).get("grid_count")
        )
        and int(
            (((analysis.get("ranking_robustness") or {}).get("row_resampling_sensitivity") or {}).get("replicates") or 0)
        )
        == int(args.bootstrap_replicates),
    }


def _weight_sensitivity(arms: dict[str, dict[str, Any]], step: float) -> dict[str, Any]:
    denominator = int(round(1.0 / step))
    top_credit = {arm: 0.0 for arm in ARMS}
    rank_total = {arm: 0.0 for arm in ARMS}
    grid_count = 0
    for entropy_units in range(denominator + 1):
        for occupancy_units in range(denominator - entropy_units + 1):
            pair_units = denominator - entropy_units - occupancy_units
            weights = np.asarray(
                [entropy_units, occupancy_units, pair_units],
                dtype=float,
            ) / denominator
            scores = {
                arm: float(np.dot(weights, _coverage_components(item["distribution_after_adding_arm"])))
                for arm, item in arms.items()
            }
            _accumulate_ranking(scores, top_credit, rank_total)
            grid_count += 1
    return {
        "grid_step": step,
        "grid_count": grid_count,
        "top_fraction": {arm: float(top_credit[arm] / grid_count) for arm in ARMS},
        "mean_rank": {arm: float(rank_total[arm] / grid_count) for arm in ARMS},
        "tie_policy": "exact score ties split top credit and receive average ranks",
    }


def _bootstrap_ranking_sensitivity(
    baseline: np.ndarray,
    arm_rows: dict[str, np.ndarray],
    args: argparse.Namespace,
) -> dict[str, Any]:
    rng = np.random.default_rng(int(args.bootstrap_seed))
    top_credit = {arm: 0.0 for arm in ARMS}
    rank_total = {arm: 0.0 for arm in ARMS}
    weights = np.asarray([0.45, 0.35, 0.20], dtype=float)
    for _ in range(int(args.bootstrap_replicates)):
        scores = {}
        for arm in ARMS:
            values = arm_rows[arm]
            if len(values):
                sampled = values[rng.integers(0, len(values), size=len(values))]
                combined = np.vstack((baseline, sampled))
            else:
                combined = baseline
            distribution = _distribution(combined, args)
            scores[arm] = float(np.dot(weights, _coverage_components(distribution)))
        _accumulate_ranking(scores, top_credit, rank_total)
    replicates = int(args.bootstrap_replicates)
    return {
        "replicates": replicates,
        "seed": int(args.bootstrap_seed),
        "score_weights": {
            "four_d_normalized_entropy": 0.45,
            "four_d_occupied_fraction": 0.35,
            "mean_pair_normalized_entropy": 0.20,
        },
        "top_fraction": {arm: float(top_credit[arm] / replicates) for arm in ARMS},
        "mean_rank": {arm: float(rank_total[arm] / replicates) for arm in ARMS},
        "boundary": "Heuristic row-resampling sensitivity; selected candidates are not assumed IID.",
    }


def _coverage_components(distribution: dict[str, Any]) -> np.ndarray:
    return np.asarray(
        [
            distribution["four_d"]["normalized_entropy"],
            distribution["four_d"]["occupied_fraction"],
            distribution["mean_pair_normalized_entropy"],
        ],
        dtype=float,
    )


def _accumulate_ranking(
    scores: dict[str, float],
    top_credit: dict[str, float],
    rank_total: dict[str, float],
) -> None:
    values = np.asarray([scores[arm] for arm in ARMS], dtype=float)
    maximum = float(np.max(values))
    winners = [arm for arm in ARMS if math.isclose(scores[arm], maximum, rel_tol=0.0, abs_tol=1.0e-12)]
    for arm in winners:
        top_credit[arm] += 1.0 / len(winners)
    for arm in ARMS:
        greater = sum(scores[other] > scores[arm] + 1.0e-12 for other in ARMS)
        tied = sum(math.isclose(scores[other], scores[arm], rel_tol=0.0, abs_tol=1.0e-12) for other in ARMS)
        rank_total[arm] += 1.0 + greater + 0.5 * (tied - 1)


def _distribution(values: np.ndarray, args: argparse.Namespace) -> dict[str, Any]:
    one_d = {}
    for index, column in enumerate(FEATURE_COLUMNS):
        counts, _ = np.histogram(
            values[:, index],
            bins=int(args.marginal_bins),
            range=(float(FEATURE_RANGES[index, 0]), float(FEATURE_RANGES[index, 1])),
        )
        one_d[column] = _hist_metrics(counts)
    pairwise = {}
    for left, right in combinations(range(len(FEATURE_COLUMNS)), 2):
        counts, _, _ = np.histogram2d(
            values[:, left],
            values[:, right],
            bins=(int(args.pair_bins), int(args.pair_bins)),
            range=(FEATURE_RANGES[left].tolist(), FEATURE_RANGES[right].tolist()),
        )
        pairwise[f"{FEATURE_COLUMNS[left]}__{FEATURE_COLUMNS[right]}"] = _hist_metrics(counts.ravel())
    four_counts, _ = np.histogramdd(
        values,
        bins=[int(args.four_d_bins)] * len(FEATURE_COLUMNS),
        range=[item.tolist() for item in FEATURE_RANGES],
    )
    return {
        "row_count": int(len(values)),
        "one_d": one_d,
        "pairwise": pairwise,
        "four_d": {**_hist_metrics(four_counts.ravel()), "counts": four_counts.astype(int).ravel().tolist()},
        "mean_one_d_normalized_entropy": float(np.mean([item["normalized_entropy"] for item in one_d.values()])),
        "mean_pair_normalized_entropy": float(np.mean([item["normalized_entropy"] for item in pairwise.values()])),
    }


def _hist_metrics(counts: np.ndarray) -> dict[str, Any]:
    counts = np.asarray(counts, dtype=float)
    nonzero = counts[counts > 0.0]
    total = float(np.sum(counts))
    probabilities = nonzero / total if total > 0.0 else np.empty(0)
    entropy = float(-np.sum(probabilities * np.log(probabilities))) if probabilities.size else 0.0
    mean = float(np.mean(counts)) if len(counts) else 0.0
    return {
        "total_count": int(total),
        "occupied_fraction": float(len(nonzero) / len(counts)) if len(counts) else 0.0,
        "normalized_entropy": entropy / math.log(len(counts)) if len(counts) > 1 else 0.0,
        "coefficient_of_variation": float(np.std(counts) / mean) if mean > 0.0 else None,
        "max_to_min_nonzero_ratio": float(np.max(nonzero) / np.min(nonzero)) if nonzero.size else None,
    }


def _distribution_gain(baseline: dict[str, Any], updated: dict[str, Any]) -> dict[str, float]:
    return {
        "four_d_occupied_fraction_delta": float(updated["four_d"]["occupied_fraction"] - baseline["four_d"]["occupied_fraction"]),
        "four_d_normalized_entropy_delta": float(updated["four_d"]["normalized_entropy"] - baseline["four_d"]["normalized_entropy"]),
        "mean_one_d_normalized_entropy_delta": float(
            updated["mean_one_d_normalized_entropy"] - baseline["mean_one_d_normalized_entropy"]
        ),
        "mean_pair_normalized_entropy_delta": float(
            updated["mean_pair_normalized_entropy"] - baseline["mean_pair_normalized_entropy"]
        ),
    }


def _calibration(predicted: np.ndarray, real: np.ndarray) -> dict[str, Any]:
    spans = FEATURE_RANGES[:, 1] - FEATURE_RANGES[:, 0]
    absolute_error = np.abs(predicted - real)
    return {
        "row_count": int(len(real)),
        "range_normalized_mae": float(np.mean(absolute_error / spans[None, :])),
        "per_feature_range_normalized_mae": {
            column: float(np.mean(absolute_error[:, index]) / spans[index])
            for index, column in enumerate(FEATURE_COLUMNS)
        },
    }


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    baseline = np.asarray(analysis["baseline_distribution"]["four_d"]["counts"], dtype=float)
    order = np.argsort(baseline, kind="stable")
    fig, axes = plt.subplots(2, 3, figsize=(18, 9), sharex=True, sharey=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    for axis, arm in zip(axes.flat, ARMS):
        axis.set_facecolor("white")
        axis.tick_params(colors="#202020")
        axis.xaxis.label.set_color("#202020")
        axis.yaxis.label.set_color("#202020")
        axis.title.set_color("#202020")
        for spine in axis.spines.values():
            spine.set_color("#202020")
        arm_data = analysis["arms"][arm]
        counts = np.asarray(arm_data["distribution_after_adding_arm"]["four_d"]["counts"], dtype=float)
        positions = np.arange(len(order))
        baseline_sorted = baseline[order]
        counts_sorted = counts[order]
        axis.plot(positions, baseline_sorted, color="#777777", linewidth=1.4, linestyle="--", label="Real baseline")
        axis.plot(positions, counts_sorted, color="#167a65", linewidth=1.3, label="Baseline + real arm")
        axis.fill_between(positions, baseline_sorted, counts_sorted, color="#54a98d", alpha=0.28, label="Real added coverage")
        axis.set_title(
            f"{arm}\nvalid={arm_data['valid_real_emx_count']}, score={arm_data['coverage_efficiency_score']:.4f}"
        )
        axis.set_xlabel("4-D bin sorted by baseline count")
        axis.set_ylabel("Real rows per bin")
        legend = axis.legend(fontsize=8, facecolor="white", framealpha=1.0)
        for text in legend.get_texts():
            text.set_color("#202020")
    for axis in axes.flat[len(ARMS) :]:
        axis.set_visible(False)
    fig.suptitle("Equal-budget acquisition policy comparison from real EMX labels", fontsize=15, color="#202020")
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _write_metrics_csv(path: Path, analysis: dict[str, Any]) -> None:
    if analysis.get("available") is not True:
        path.write_text("", encoding="utf-8")
        return
    rows = []
    for rank, arm in enumerate(analysis["coverage_ranking"], start=1):
        item = analysis["arms"][arm]
        gain = item["gain_vs_baseline"]
        rows.append(
            {
                "coverage_rank": rank,
                "arm": arm,
                "attempted_count": item["attempted_count"],
                "valid_real_emx_count": item["valid_real_emx_count"],
                "real_emx_success_fraction": item["real_emx_success_fraction"],
                "in_range_real_count": item["in_range_real_count"],
                "in_range_fraction_of_valid": item["in_range_fraction_of_valid"],
                "coverage_efficiency_score": item["coverage_efficiency_score"],
                **gain,
                "proxy_range_normalized_mae": item["proxy_calibration"].get("range_normalized_mae"),
                "weight_grid_top_fraction": analysis["ranking_robustness"]["weight_simplex_sensitivity"]["top_fraction"][arm],
                "bootstrap_top_fraction": analysis["ranking_robustness"]["row_resampling_sensitivity"]["top_fraction"][arm],
                "nominal_top_is_robust": analysis["ranking_robustness"]["nominal_top_is_robust"],
                "decision_boundary": "ADVISORY_ONLY_MODEL_RETRAIN_REQUIRED",
            }
        )
    _write_csv(path, rows)


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    lines = [
        "# Equal-budget acquisition benchmark from real EMX",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Outcome status: **{data['outcome_status']}**",
        f"- Advisory coverage ranking: `{analysis.get('coverage_ranking')}`",
        f"- Nominal top policy: `{analysis.get('nominal_advisory_policy')}`",
        f"- Unique robust candidate for fixed-model ablation: `{analysis.get('advisory_policy_for_fixed_model_ablation')}`",
        f"- Plausible policies for fixed-model ablation: `{analysis.get('advisory_policies_for_fixed_model_ablation')}`",
        f"- Ranking robustness decision: `{(analysis.get('ranking_robustness') or {}).get('decision')}`",
        "",
        data["scientific_boundary"],
        "",
    ]
    return "\n".join(lines)


def _valid_touchstone(row: dict[str, str], base: Path) -> bool:
    raw = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
    path = Path(raw).expanduser()
    if raw and not path.is_absolute():
        path = (base / path).resolve()
    return bool(raw) and path.suffix.lower() == ".s4p" and path.is_file() and path.stat().st_size > 0


def _in_range_mask(values: np.ndarray) -> np.ndarray:
    return np.all((values >= FEATURE_RANGES[:, 0][None, :]) & (values <= FEATURE_RANGES[:, 1][None, :]), axis=1)


def _in_feature_range(values: np.ndarray) -> bool:
    return bool(np.all(values >= FEATURE_RANGES[:, 0]) and np.all(values <= FEATURE_RANGES[:, 1]))


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _float_row(row: dict[str, str], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vector_digest(values: np.ndarray) -> bytes:
    return hashlib.blake2b(np.asarray(values, dtype="<f8").tobytes(), digest_size=16).digest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_counts(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not isinstance(value, (np.ndarray, set, list))}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
