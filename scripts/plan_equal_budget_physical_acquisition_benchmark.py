#!/usr/bin/env python3
"""Plan a disjoint, equal-budget physical-feature acquisition benchmark.

Six candidate-priority policies are compared from the same proxy-filtered
candidate pool: random, geometry K-means++, 4-D deficit, 4-D deficit plus
surrogate uncertainty, 4-D deficit plus sequential geometry diversity, and a
hierarchical 1-D/2-D/4-D response-gap policy. Every selected candidate remains
unlabeled until a real EMX solve returns; predicted physical features are never
training labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402


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
HIERARCHICAL_PAIR_INDICES = ((0, 2), (1, 2), (2, 3))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    accepted_csv = Path(args.accepted_csv).expanduser().resolve()
    candidate_summary_path = Path(args.candidate_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_summary = _read_json(candidate_summary_path)
    accepted = _load_accepted(accepted_csv)
    geometry_bounds = _geometry_bounds(candidate_summary, accepted)
    candidates = _load_candidates(candidate_csv, accepted["geometry_digests"])
    analysis = _plan(accepted, candidates, geometry_bounds, args)
    checks = _checks(accepted, candidates, candidate_summary, geometry_bounds, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    combined_csv = out_dir / "equal_budget_acquisition_assignments.csv"
    arm_paths = {arm: out_dir / f"arm_{arm}_candidates.csv" for arm in ARMS}
    figure_path = out_dir / "equal_budget_predicted_coverage_comparison.png"
    summary_path = out_dir / "equal_budget_acquisition_benchmark_plan_summary.json"
    report_path = out_dir / "equal_budget_acquisition_benchmark_plan_report.md"
    selected_rows = _materialize_rows(candidate_csv, analysis.get("assignments") or {})
    _write_csv(combined_csv, selected_rows)
    for arm, path in arm_paths.items():
        _write_csv(path, [row for row in selected_rows if row.get("benchmark_arm") == arm])
    if analysis.get("available") is True:
        _plot(figure_path, analysis)

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "RUN_EQUAL_BUDGET_REAL_EMX_BENCHMARK" if status == "PASS" else "DO_NOT_RUN_BENCHMARK_FIX_PLAN",
        "outcome_status": "AWAITING_REAL_EMX",
        "candidate_csv": str(candidate_csv),
        "candidate_csv_sha256": _sha256(candidate_csv),
        "candidate_summary": str(candidate_summary_path),
        "candidate_summary_sha256": _sha256(candidate_summary_path),
        "accepted_csv": str(accepted_csv),
        "accepted_csv_sha256": _sha256(accepted_csv),
        "arms": list(ARMS),
        "arm_budget": int(args.arm_budget),
        "total_selected_budget": int(args.arm_budget) * len(ARMS),
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_ranges": {
            column: {"min": float(FEATURE_RANGES[index, 0]), "max": float(FEATURE_RANGES[index, 1])}
            for index, column in enumerate(FEATURE_COLUMNS)
        },
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "geometry_bounds": geometry_bounds,
        "accepted_evidence": _public_counts(accepted),
        "candidate_evidence": _public_counts(candidates),
        "analysis": {key: value for key, value in analysis.items() if key != "assignments"},
        "checks": checks,
        "artifacts": {
            "combined_assignments_csv": str(combined_csv),
            "arm_candidate_csvs": {arm: str(path) for arm, path in arm_paths.items()},
            "predicted_coverage_figure": str(figure_path),
            "report_md": str(report_path),
        },
        "literature_basis": [
            {
                "source": "Accelerating Surrogate Modeling for Electromagnetic Device Using Active Learning, IEEE Transactions on Magnetics 2025",
                "url": "https://doi.org/10.1109/TMAG.2025.3647963",
                "adaptation": "Compare active learning, K-means++ novelty, and random selection under equal real-simulation budgets.",
            },
            {
                "source": "On-the-fly closed-loop materials discovery via Bayesian active learning, npj Computational Materials 2020",
                "url": "https://www.nature.com/articles/s41524-020-00431-2",
                "adaptation": "Keep exploration and uncertainty in candidate priority while retaining expensive simulation as the only label source.",
            },
            {
                "source": "AutoML based workflow for design of experiments selection and benchmarking data acquisition strategies with simulation models, Scientific Reports 2024",
                "url": "https://www.nature.com/articles/s41598-024-83581-3",
                "adaptation": "Use equal real-simulation budgets and fixed downstream evaluation because active learning does not always beat space-filling DOE.",
            },
            {
                "source": "Active learning accelerated exploration of single-atom local environments in multimetallic systems for oxygen electrocatalysis, npj Computational Materials 2024",
                "url": "https://www.nature.com/articles/s41524-024-01432-1",
                "adaptation": "Add a diversity-aware arm to test whether acquisition quality and batch diversity are complementary.",
            },
            {
                "source": "Bias free multiobjective active learning for materials design and discovery, Nature Communications 2021",
                "url": "https://www.nature.com/articles/s41467-021-22437-0",
                "adaptation": "Use greedy farthest-point ordering as a traceable geometry-diversity baseline.",
            },
        ],
        "scientific_boundary": (
            "PASS validates only a disjoint equal-budget candidate plan. All pred_* and pred_uncertainty_* values are "
            "proxy outputs for priority, not labels. Policy quality can be ranked only after every arm receives real "
            "EMX S4P results and the same fixed downstream model evaluation."
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
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--candidate-summary", required=True)
    parser.add_argument("--accepted-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--arm-budget", type=int, default=2_000)
    parser.add_argument("--min-accepted-rows", type=int, default=400_000)
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--kmeans-prefilter-factor", type=int, default=8)
    parser.add_argument("--kmeans-ranking-factor", type=float, default=1.5)
    parser.add_argument("--diversity-prefilter-factor", type=int, default=8)
    parser.add_argument("--diversity-ranking-factor", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--deficit-weight", type=float, default=0.65)
    parser.add_argument("--uncertainty-weight", type=float, default=0.25)
    parser.add_argument("--novelty-weight", type=float, default=0.10)
    parser.add_argument("--diversity-deficit-weight", type=float, default=0.65)
    parser.add_argument("--diversity-geometry-weight", type=float, default=0.35)
    parser.add_argument("--hierarchical-marginal-bins", type=int, default=10)
    parser.add_argument("--hierarchical-pair-bins", type=int, default=10)
    parser.add_argument("--hierarchical-marginal-weight", type=float, default=0.25)
    parser.add_argument("--hierarchical-pair-weight", type=float, default=0.45)
    parser.add_argument("--hierarchical-four-d-weight", type=float, default=0.20)
    parser.add_argument("--hierarchical-novelty-weight", type=float, default=0.10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.arm_budget < 1 or args.min_accepted_rows < 1 or args.bins < 2:
        parser.error("budgets, accepted rows, and bins must be positive; bins must be at least 2")
    if args.kmeans_prefilter_factor < 1 or args.kmeans_ranking_factor < 1.0:
        parser.error("K-means++ factors must be at least 1")
    if args.diversity_prefilter_factor < 1 or args.diversity_ranking_factor < 1.0:
        parser.error("deficit-diversity factors must be at least 1")
    if min(args.hierarchical_marginal_bins, args.hierarchical_pair_bins) < 2:
        parser.error("hierarchical marginal and pair bin counts must be at least 2")
    weights = (args.deficit_weight, args.uncertainty_weight, args.novelty_weight)
    if any(value < 0.0 for value in weights) or not math.isclose(sum(weights), 1.0, abs_tol=1.0e-9):
        parser.error("deficit, uncertainty, and novelty weights must be nonnegative and sum to 1")
    diversity_weights = (args.diversity_deficit_weight, args.diversity_geometry_weight)
    if any(value < 0.0 for value in diversity_weights) or not math.isclose(
        sum(diversity_weights), 1.0, abs_tol=1.0e-9
    ):
        parser.error("diversity deficit and geometry weights must be nonnegative and sum to 1")
    hierarchical_weights = (
        args.hierarchical_marginal_weight,
        args.hierarchical_pair_weight,
        args.hierarchical_four_d_weight,
        args.hierarchical_novelty_weight,
    )
    if any(value < 0.0 for value in hierarchical_weights) or not math.isclose(
        sum(hierarchical_weights), 1.0, abs_tol=1.0e-9
    ):
        parser.error("hierarchical marginal, pair, 4-D, and novelty weights must be nonnegative and sum to 1")
    return args


def _load_accepted(path: Path) -> dict[str, Any]:
    result = {
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "geometry": np.empty((0, len(GEOMETRY_COLUMNS))),
        "features": np.empty((0, len(FEATURE_COLUMNS))),
        "geometry_digests": set(),
        "duplicate_geometry_count": 0,
        "columns_present": False,
    }
    if not path.is_file():
        return result
    geometry_rows = []
    feature_rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(GEOMETRY_COLUMNS) | set(FEATURE_COLUMNS)
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for row in reader:
            result["row_count"] += 1
            geometry = _float_row(row, GEOMETRY_COLUMNS)
            features = _float_row(row, FEATURE_COLUMNS)
            if geometry is None or features is None:
                result["invalid_count"] += 1
                continue
            digest = _vector_digest(geometry)
            if digest in result["geometry_digests"]:
                result["duplicate_geometry_count"] += 1
                continue
            result["geometry_digests"].add(digest)
            geometry_rows.append(geometry)
            feature_rows.append(features)
    if geometry_rows:
        result["geometry"] = np.asarray(geometry_rows, dtype=float)
        result["features"] = np.asarray(feature_rows, dtype=float)
    result["valid_count"] = len(geometry_rows)
    return result


def _load_candidates(path: Path, accepted_digests: set[bytes]) -> dict[str, Any]:
    result = {
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "outside_predicted_range_count": 0,
        "accepted_overlap_count": 0,
        "duplicate_geometry_count": 0,
        "geometry": np.empty((0, len(GEOMETRY_COLUMNS))),
        "predicted_features": np.empty((0, len(FEATURE_COLUMNS))),
        "uncertainty": np.empty((0, len(FEATURE_COLUMNS))),
        "source_row_indices": np.empty(0, dtype=np.int64),
        "candidate_ids": [],
        "columns_present": False,
    }
    if not path.is_file():
        return result
    predicted_columns = tuple(f"pred_{column}" for column in FEATURE_COLUMNS)
    uncertainty_columns = tuple(f"pred_uncertainty_{column}" for column in FEATURE_COLUMNS)
    geometry_rows = []
    feature_rows = []
    uncertainty_rows = []
    source_indices = []
    candidate_ids = []
    seen: set[bytes] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(GEOMETRY_COLUMNS) | set(predicted_columns) | set(uncertainty_columns) | {"candidate_id"}
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for source_index, row in enumerate(reader):
            result["row_count"] += 1
            geometry = _float_row(row, GEOMETRY_COLUMNS)
            features = _float_row(row, predicted_columns)
            uncertainty = _float_row(row, uncertainty_columns)
            if geometry is None or features is None or uncertainty is None or np.any(uncertainty < 0.0):
                result["invalid_count"] += 1
                continue
            if not _in_feature_range(features):
                result["outside_predicted_range_count"] += 1
                continue
            digest = _vector_digest(geometry)
            if digest in accepted_digests:
                result["accepted_overlap_count"] += 1
                continue
            if digest in seen:
                result["duplicate_geometry_count"] += 1
                continue
            seen.add(digest)
            geometry_rows.append(geometry)
            feature_rows.append(features)
            uncertainty_rows.append(uncertainty)
            source_indices.append(source_index)
            candidate_ids.append(str(row.get("candidate_id") or f"candidate_{source_index}"))
    if geometry_rows:
        result["geometry"] = np.asarray(geometry_rows, dtype=float)
        result["predicted_features"] = np.asarray(feature_rows, dtype=float)
        result["uncertainty"] = np.asarray(uncertainty_rows, dtype=float)
        result["source_row_indices"] = np.asarray(source_indices, dtype=np.int64)
        result["candidate_ids"] = candidate_ids
    result["valid_count"] = len(geometry_rows)
    return result


def _geometry_bounds(candidate_summary: dict[str, Any], accepted: dict[str, Any]) -> dict[str, Any]:
    raw_bounds = candidate_summary.get("bounds") or {}
    lower = []
    upper = []
    source = "candidate_prediction_summary"
    for index, column in enumerate(GEOMETRY_COLUMNS):
        item = raw_bounds.get(column) or {}
        lo = _finite(item.get("min"))
        hi = _finite(item.get("max"))
        if lo is None or hi is None or hi <= lo:
            source = "accepted_observed_fallback"
            if accepted["valid_count"]:
                lo = float(np.min(accepted["geometry"][:, index]))
                hi = float(np.max(accepted["geometry"][:, index]))
        lower.append(lo)
        upper.append(hi)
    valid = all(lo is not None and hi is not None and hi > lo for lo, hi in zip(lower, upper))
    return {"source": source, "valid": valid, "lower": lower, "upper": upper}


def _plan(
    accepted: dict[str, Any],
    candidates: dict[str, Any],
    bounds: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if accepted["valid_count"] < 1 or candidates["valid_count"] < 1 or not bounds.get("valid"):
        return {"available": False, "assignments": {}}
    total_needed = int(args.arm_budget) * len(ARMS)
    if candidates["valid_count"] < total_needed:
        return {"available": False, "assignments": {}}
    lower = np.asarray(bounds["lower"], dtype=float)
    upper = np.asarray(bounds["upper"], dtype=float)
    span = upper - lower
    accepted_geometry = (accepted["geometry"] - lower[None, :]) / span[None, :]
    candidate_geometry = (candidates["geometry"] - lower[None, :]) / span[None, :]
    tree = cKDTree(accepted_geometry)
    novelty = np.empty(candidates["valid_count"], dtype=float)
    batch_size = 50_000
    for start in range(0, len(candidate_geometry), batch_size):
        novelty[start : start + batch_size] = tree.query(candidate_geometry[start : start + batch_size], k=1)[0]

    bins = int(args.bins)
    baseline_bins = _feature_bin_indices(accepted["features"], bins)
    baseline_valid = np.all(baseline_bins >= 0, axis=1)
    baseline_flat = _flatten_bins(baseline_bins[baseline_valid], bins)
    bin_count = bins ** len(FEATURE_COLUMNS)
    current_counts = np.bincount(baseline_flat, minlength=bin_count)
    target_per_bin = int(math.ceil((len(baseline_flat) + int(args.arm_budget)) / bin_count))
    deficits = np.maximum(target_per_bin - current_counts, 0)
    candidate_bins = _feature_bin_indices(candidates["predicted_features"], bins)
    candidate_flat = _flatten_bins(candidate_bins, bins)
    deficit_score = deficits[candidate_flat] / max(1, target_per_bin)
    response_spans = FEATURE_RANGES[:, 1] - FEATURE_RANGES[:, 0]
    uncertainty = np.sqrt(np.mean((candidates["uncertainty"] / response_spans[None, :]) ** 2, axis=1))

    rng = np.random.default_rng(int(args.seed))
    random_order = rng.permutation(candidates["valid_count"])
    kmeans_order = _kmeanspp_rank(candidate_geometry, novelty, int(args.arm_budget), args)
    jitter = np.random.default_rng(int(args.seed) + 17).random(candidates["valid_count"])
    deficit_order = np.lexsort((jitter, -novelty, -deficit_score))
    combined_score = (
        float(args.deficit_weight) * _percentile_rank(deficit_score)
        + float(args.uncertainty_weight) * _percentile_rank(uncertainty)
        + float(args.novelty_weight) * _percentile_rank(novelty)
    )
    combined_order = np.lexsort((jitter, -combined_score))
    diversity_order, diversity_score = _deficit_diversity_rank(
        candidate_geometry,
        deficit_score,
        novelty,
        int(args.arm_budget),
        args,
    )
    hierarchical = _hierarchical_gap_scores(
        accepted["features"],
        candidates["predicted_features"],
        deficit_score,
        novelty,
        args,
    )
    hierarchical_order = np.lexsort((jitter, -hierarchical["combined_score"]))
    rankings = {
        "random": random_order,
        "geometry_kmeanspp": kmeans_order,
        "physical_deficit": deficit_order,
        "deficit_uncertainty": combined_order,
        "deficit_diversity": diversity_order,
        "hierarchical_gap": hierarchical_order,
    }
    selected = _assign_disjoint(rankings, int(args.arm_budget))
    assignments: dict[int, dict[str, Any]] = {}
    projected = {}
    for arm, indices in selected.items():
        arm_flat = candidate_flat[np.asarray(indices, dtype=int)]
        projected_counts = current_counts + np.bincount(arm_flat, minlength=bin_count)
        projected[arm] = _uniformity_metrics(projected_counts)
        for rank, index in enumerate(indices, start=1):
            assignments[int(candidates["source_row_indices"][index])] = {
                "benchmark_arm": arm,
                "arm_rank": rank,
                "label_status": "AWAITING_REAL_EMX",
                "predicted_bin_key": "|".join(str(int(value)) for value in candidate_bins[index]),
                "baseline_bin_count": int(current_counts[candidate_flat[index]]),
                "target_count_per_bin": target_per_bin,
                "predicted_bin_deficit": int(deficits[candidate_flat[index]]),
                "geometry_novelty_distance": float(novelty[index]),
                "normalized_surrogate_uncertainty": float(uncertainty[index]),
                "deficit_uncertainty_score": float(combined_score[index]),
                "deficit_diversity_score": float(diversity_score[index]),
                "hierarchical_gap_score": float(hierarchical["combined_score"][index]),
                "hierarchical_marginal_deficit_score": float(hierarchical["marginal_score"][index]),
                "hierarchical_pair_deficit_score": float(hierarchical["pair_score"][index]),
            }
    return {
        "available": True,
        "baseline_in_range_count": int(len(baseline_flat)),
        "bin_count": bin_count,
        "target_count_per_bin": target_per_bin,
        "baseline_uniformity": _uniformity_metrics(current_counts),
        "predicted_projected_uniformity": projected,
        "selected_counts": {arm: len(indices) for arm, indices in selected.items()},
        "selected_candidate_id_sha256": {
            arm: _string_list_digest([candidates["candidate_ids"][index] for index in indices])
            for arm, indices in selected.items()
        },
        "selected_index_overlap_count": sum(
            len(set(selected[left]) & set(selected[right]))
            for arm_index, left in enumerate(ARMS)
            for right in ARMS[arm_index + 1 :]
        ),
        "ranking_contract": {
            "random": "seeded random order after common predicted-in-range proxy filter",
            "geometry_kmeanspp": "K-means++ D2 sampling initialized by distance to accepted real geometry",
            "physical_deficit": "descending fixed-range 4-D bin deficit with novelty and seeded tie breaks",
            "deficit_uncertainty": (
                f"{float(args.deficit_weight):.2f} deficit percentile + "
                f"{float(args.uncertainty_weight):.2f} normalized KNN uncertainty percentile + "
                f"{float(args.novelty_weight):.2f} novelty percentile"
            ),
            "deficit_diversity": (
                "precomputed sequential farthest-first ranking: "
                f"{float(args.diversity_deficit_weight):.2f} fixed-range 4-D deficit percentile + "
                f"{float(args.diversity_geometry_weight):.2f} minimum normalized geometry distance to accepted "
                "real geometries and earlier ranked candidates"
            ),
            "hierarchical_gap": (
                f"{float(args.hierarchical_marginal_weight):.2f} marginal-deficit percentile + "
                f"{float(args.hierarchical_pair_weight):.2f} Lp-Q/Ls-Q/Q-|K| pair-deficit percentile + "
                f"{float(args.hierarchical_four_d_weight):.2f} fixed-range 4-D deficit percentile + "
                f"{float(args.hierarchical_novelty_weight):.2f} geometry-novelty percentile"
            ),
            "disjoint_assignment": "round-robin conflict resolution with rotating arm order",
        },
        "deficit_diversity_configuration": {
            "deficit_weight": float(args.diversity_deficit_weight),
            "geometry_diversity_weight": float(args.diversity_geometry_weight),
            "prefilter_factor": int(args.diversity_prefilter_factor),
            "ranking_factor": float(args.diversity_ranking_factor),
            "distance_normalization": "Euclidean distance in 10-D geometry normalized by traceable bounds, clipped by sqrt(10)",
            "production_policy_changed": False,
        },
        "hierarchical_gap_configuration": {
            "marginal_bins": int(args.hierarchical_marginal_bins),
            "pair_bins": int(args.hierarchical_pair_bins),
            "pair_features": [
                [FEATURE_COLUMNS[left], FEATURE_COLUMNS[right]]
                for left, right in HIERARCHICAL_PAIR_INDICES
            ],
            "marginal_weight": float(args.hierarchical_marginal_weight),
            "pair_weight": float(args.hierarchical_pair_weight),
            "four_d_weight": float(args.hierarchical_four_d_weight),
            "geometry_novelty_weight": float(args.hierarchical_novelty_weight),
            "labels_for_priority": "accepted real EMX only",
            "candidate_response_values": "proxy ranking only",
            "production_policy_changed": False,
        },
        "assignments": assignments,
    }


def _hierarchical_gap_scores(
    accepted_features: np.ndarray,
    candidate_features: np.ndarray,
    four_d_deficit_score: np.ndarray,
    novelty: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, np.ndarray]:
    """Score fixed-range marginal and pair gaps without creating labels."""
    marginal_bins = int(args.hierarchical_marginal_bins)
    pair_bins = int(args.hierarchical_pair_bins)
    marginal_components = []
    for axis in range(len(FEATURE_COLUMNS)):
        accepted_index = _single_feature_bin_indices(accepted_features[:, axis], axis, marginal_bins)
        candidate_index = _single_feature_bin_indices(candidate_features[:, axis], axis, marginal_bins)
        counts = np.bincount(accepted_index, minlength=marginal_bins)
        target = int(math.ceil((len(accepted_index) + int(args.arm_budget)) / marginal_bins))
        deficits = np.maximum(target - counts, 0)
        marginal_components.append(deficits[candidate_index] / max(1, target))
    marginal_score = np.mean(np.vstack(marginal_components), axis=0)

    pair_components = []
    for left, right in HIERARCHICAL_PAIR_INDICES:
        accepted_left = _single_feature_bin_indices(accepted_features[:, left], left, pair_bins)
        accepted_right = _single_feature_bin_indices(accepted_features[:, right], right, pair_bins)
        candidate_left = _single_feature_bin_indices(candidate_features[:, left], left, pair_bins)
        candidate_right = _single_feature_bin_indices(candidate_features[:, right], right, pair_bins)
        accepted_flat = accepted_left * pair_bins + accepted_right
        candidate_flat = candidate_left * pair_bins + candidate_right
        counts = np.bincount(accepted_flat, minlength=pair_bins**2)
        target = int(math.ceil((len(accepted_flat) + int(args.arm_budget)) / (pair_bins**2)))
        deficits = np.maximum(target - counts, 0)
        pair_components.append(deficits[candidate_flat] / max(1, target))
    pair_score = np.mean(np.vstack(pair_components), axis=0)

    combined_score = (
        float(args.hierarchical_marginal_weight) * _percentile_rank(marginal_score)
        + float(args.hierarchical_pair_weight) * _percentile_rank(pair_score)
        + float(args.hierarchical_four_d_weight) * _percentile_rank(four_d_deficit_score)
        + float(args.hierarchical_novelty_weight) * _percentile_rank(novelty)
    )
    return {
        "marginal_score": marginal_score,
        "pair_score": pair_score,
        "combined_score": combined_score,
    }


def _single_feature_bin_indices(values: np.ndarray, axis: int, bins: int) -> np.ndarray:
    lo, hi = FEATURE_RANGES[axis]
    normalized = (np.asarray(values, dtype=float) - lo) / (hi - lo)
    result = np.floor(normalized * bins).astype(int)
    result[np.isclose(values, hi, rtol=0.0, atol=1.0e-12)] = bins - 1
    if np.any(result < 0) or np.any(result >= bins):
        raise ValueError("hierarchical-gap scoring received an out-of-range feature value")
    return result


def _kmeanspp_rank(geometry: np.ndarray, novelty: np.ndarray, budget: int, args: argparse.Namespace) -> np.ndarray:
    prefilter_count = min(len(geometry), max(budget, int(args.kmeans_prefilter_factor) * budget))
    prefilter = np.argsort(novelty, kind="stable")[::-1][:prefilter_count]
    desired = min(prefilter_count, max(budget, int(math.ceil(float(args.kmeans_ranking_factor) * budget))))
    pool = geometry[prefilter]
    distance_squared = novelty[prefilter] ** 2
    rng = np.random.default_rng(int(args.seed) + 29)
    selected_positions = []
    for _ in range(desired):
        total = float(np.sum(distance_squared))
        if total <= 0.0:
            remaining = np.flatnonzero(distance_squared >= 0.0)
            remaining = [int(index) for index in remaining if index not in set(selected_positions)]
            if not remaining:
                break
            chosen = remaining[0]
        else:
            chosen = int(rng.choice(len(pool), p=distance_squared / total))
        if chosen in selected_positions:
            distance_squared[chosen] = 0.0
            continue
        selected_positions.append(chosen)
        new_distance = np.sum((pool - pool[chosen]) ** 2, axis=1)
        distance_squared = np.minimum(distance_squared, new_distance)
        distance_squared[np.asarray(selected_positions, dtype=int)] = 0.0
    selected = prefilter[np.asarray(selected_positions, dtype=int)]
    selected_set = set(int(index) for index in selected)
    remainder = [int(index) for index in np.argsort(novelty, kind="stable")[::-1] if int(index) not in selected_set]
    return np.asarray([int(index) for index in selected] + remainder, dtype=int)


def _deficit_diversity_rank(
    geometry: np.ndarray,
    deficit_score: np.ndarray,
    accepted_novelty: np.ndarray,
    budget: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a traceable quality-diversity ranking without using EMX labels."""
    deficit_rank = _percentile_rank(deficit_score)
    max_distance = math.sqrt(geometry.shape[1])
    accepted_diversity = np.clip(accepted_novelty / max_distance, 0.0, 1.0)
    static_score = (
        float(args.diversity_deficit_weight) * deficit_rank
        + float(args.diversity_geometry_weight) * accepted_diversity
    )
    prefilter_count = min(
        len(geometry),
        max(budget, int(args.diversity_prefilter_factor) * budget),
    )
    prefilter = np.argsort(static_score, kind="stable")[::-1][:prefilter_count]
    desired = min(
        prefilter_count,
        max(budget, int(math.ceil(float(args.diversity_ranking_factor) * budget))),
    )
    pool = geometry[prefilter]
    minimum_distance = np.asarray(accepted_novelty[prefilter], dtype=float).copy()
    selected_mask = np.zeros(prefilter_count, dtype=bool)
    selected_positions: list[int] = []
    scores = np.asarray(static_score, dtype=float).copy()
    jitter = np.random.default_rng(int(args.seed) + 43).random(prefilter_count) * 1.0e-12
    for _ in range(desired):
        diversity = np.clip(minimum_distance / max_distance, 0.0, 1.0)
        step_score = (
            float(args.diversity_deficit_weight) * deficit_rank[prefilter]
            + float(args.diversity_geometry_weight) * diversity
        )
        step_score[selected_mask] = -np.inf
        chosen = int(np.argmax(step_score + jitter))
        if selected_mask[chosen] or not math.isfinite(float(step_score[chosen])):
            break
        selected_mask[chosen] = True
        selected_positions.append(chosen)
        source_index = int(prefilter[chosen])
        scores[source_index] = float(step_score[chosen])
        distance = np.sqrt(np.sum((pool - pool[chosen]) ** 2, axis=1))
        minimum_distance = np.minimum(minimum_distance, distance)

    selected = prefilter[np.asarray(selected_positions, dtype=int)]
    selected_set = set(int(index) for index in selected)
    remainder = [
        int(index)
        for index in np.argsort(static_score, kind="stable")[::-1]
        if int(index) not in selected_set
    ]
    order = np.asarray([int(index) for index in selected] + remainder, dtype=int)
    return order, scores


def _assign_disjoint(rankings: dict[str, np.ndarray], budget: int) -> dict[str, list[int]]:
    selected = {arm: [] for arm in ARMS}
    pointers = {arm: 0 for arm in ARMS}
    claimed: set[int] = set()
    round_index = 0
    while any(len(selected[arm]) < budget for arm in ARMS):
        progress = False
        rotation = round_index % len(ARMS)
        for arm in ARMS[rotation:] + ARMS[:rotation]:
            if len(selected[arm]) >= budget:
                continue
            ranking = rankings[arm]
            while pointers[arm] < len(ranking) and int(ranking[pointers[arm]]) in claimed:
                pointers[arm] += 1
            if pointers[arm] >= len(ranking):
                continue
            index = int(ranking[pointers[arm]])
            pointers[arm] += 1
            claimed.add(index)
            selected[arm].append(index)
            progress = True
        if not progress:
            break
        round_index += 1
    return selected


def _checks(
    accepted: dict[str, Any],
    candidates: dict[str, Any],
    candidate_summary: dict[str, Any],
    bounds: dict[str, Any],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    selected_counts = analysis.get("selected_counts") or {}
    return {
        "accepted_columns_present": accepted.get("columns_present") is True,
        "accepted_rows_meet_400k_stage_minimum": int(accepted.get("valid_count") or 0) >= int(args.min_accepted_rows),
        "accepted_geometry_unique": int(accepted.get("duplicate_geometry_count") or 0) == 0,
        "accepted_rows_finite": int(accepted.get("invalid_count") or 0) == 0,
        "candidate_summary_pass": candidate_summary.get("overall_status") == "PASS"
        and candidate_summary.get("decision") == "USE_AS_CANDIDATE_PREDICTIONS_ONLY",
        "candidate_columns_present": candidates.get("columns_present") is True,
        "candidate_pool_large_enough": int(candidates.get("valid_count") or 0) >= int(args.arm_budget) * len(ARMS),
        "candidate_geometry_unique": int(candidates.get("duplicate_geometry_count") or 0) == 0,
        "candidate_does_not_repeat_accepted_geometry": int(candidates.get("accepted_overlap_count") or 0) == 0,
        "candidate_values_finite": int(candidates.get("invalid_count") or 0) == 0,
        "geometry_bounds_traceable": bounds.get("valid") is True and bounds.get("source") == "candidate_prediction_summary",
        "plan_available": analysis.get("available") is True,
        "all_six_arms_exact_budget": all(int(selected_counts.get(arm) or 0) == int(args.arm_budget) for arm in ARMS),
        "selected_arms_disjoint": int(analysis.get("selected_index_overlap_count") or 0) == 0,
        "all_selected_rows_await_real_emx": bool(analysis.get("assignments"))
        and all(item.get("label_status") == "AWAITING_REAL_EMX" for item in analysis["assignments"].values()),
    }


def _feature_bin_indices(values: np.ndarray, bins: int) -> np.ndarray:
    normalized = (values - FEATURE_RANGES[:, 0][None, :]) / (FEATURE_RANGES[:, 1] - FEATURE_RANGES[:, 0])[None, :]
    result = np.floor(normalized * bins).astype(int)
    exact_upper = np.isclose(values, FEATURE_RANGES[:, 1][None, :], rtol=0.0, atol=1.0e-12)
    result[exact_upper] = bins - 1
    invalid = np.any((values < FEATURE_RANGES[:, 0][None, :]) | (values > FEATURE_RANGES[:, 1][None, :]), axis=1)
    result[invalid] = -1
    return result


def _flatten_bins(indices: np.ndarray, bins: int) -> np.ndarray:
    if len(indices) == 0:
        return np.empty(0, dtype=int)
    multipliers = bins ** np.arange(indices.shape[1] - 1, -1, -1)
    return np.sum(indices * multipliers[None, :], axis=1).astype(int)


def _uniformity_metrics(counts: np.ndarray) -> dict[str, Any]:
    counts = np.asarray(counts, dtype=float)
    nonzero = counts[counts > 0.0]
    total = float(np.sum(counts))
    probabilities = nonzero / total if total > 0.0 else np.empty(0)
    entropy = float(-np.sum(probabilities * np.log(probabilities))) if probabilities.size else 0.0
    normalized_entropy = entropy / math.log(len(counts)) if len(counts) > 1 else 0.0
    mean = float(np.mean(counts)) if len(counts) else 0.0
    return {
        "total_count": int(total),
        "occupied_bin_count": int(len(nonzero)),
        "occupied_fraction": float(len(nonzero) / len(counts)) if len(counts) else 0.0,
        "normalized_entropy": normalized_entropy,
        "coefficient_of_variation": float(np.std(counts) / mean) if mean > 0.0 else None,
        "max_to_min_nonzero_ratio": float(np.max(nonzero) / np.min(nonzero)) if nonzero.size else None,
        "counts": counts.astype(int).tolist(),
    }


def _percentile_rank(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return np.empty_like(values)
    if len(values) == 1:
        return np.ones_like(values)
    result = np.empty_like(values)
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and sorted_values[stop] == sorted_values[start]:
            stop += 1
        average_position = 0.5 * (start + stop - 1)
        result[order[start:stop]] = average_position / (len(values) - 1)
        start = stop
    return result


def _materialize_rows(path: Path, assignments: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    if not path.is_file() or not assignments:
        return rows
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for source_index, row in enumerate(csv.DictReader(handle)):
            assignment = assignments.get(source_index)
            if assignment is not None:
                rows.append({**assignment, **row})
    rows.sort(key=lambda item: (ARMS.index(str(item["benchmark_arm"])), int(item["arm_rank"])))
    return rows


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    baseline = np.asarray(analysis["baseline_uniformity"]["counts"], dtype=float)
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
        projected = np.asarray(analysis["predicted_projected_uniformity"][arm]["counts"], dtype=float)
        positions = np.arange(len(order))
        baseline_sorted = baseline[order]
        projected_sorted = projected[order]
        axis.plot(positions, baseline_sorted, color="#777777", linewidth=1.4, linestyle="--", label="Real baseline")
        axis.plot(positions, projected_sorted, color="#2068b4", linewidth=1.3, label="Predicted projection")
        axis.fill_between(
            positions,
            baseline_sorted,
            projected_sorted,
            color="#74a8df",
            alpha=0.28,
            label="Predicted added coverage",
        )
        metrics = analysis["predicted_projected_uniformity"][arm]
        axis.set_title(
            f"{arm}\noccupied={metrics['occupied_fraction']:.3f}, entropy={metrics['normalized_entropy']:.3f}"
        )
        axis.set_xlabel("4-D bin sorted by baseline count")
        axis.set_ylabel("Rows per bin")
        legend = axis.legend(fontsize=8, facecolor="white", framealpha=1.0)
        for text in legend.get_texts():
            text.set_color("#202020")
    for axis in axes.flat[len(ARMS) :]:
        axis.set_visible(False)
    fig.suptitle("Equal-budget acquisition policies: predicted coverage only, awaiting real EMX", fontsize=15, color="#202020")
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _render_report(data: dict[str, Any]) -> str:
    lines = [
        "# Equal-budget physical acquisition benchmark plan",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Outcome status: **{data['outcome_status']}**",
        f"- Real accepted baseline rows: `{data['accepted_evidence']['valid_count']}`",
        f"- Proxy-filtered candidate rows: `{data['candidate_evidence']['valid_count']}`",
        f"- Budget per arm: `{data['arm_budget']}`",
        "",
        "## Arms",
        "",
    ]
    lines.extend(f"- `{arm}`" for arm in data["arms"])
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


def _public_counts(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not isinstance(value, (np.ndarray, set, list))}


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


def _in_feature_range(values: np.ndarray) -> bool:
    return bool(np.all(values >= FEATURE_RANGES[:, 0]) and np.all(values <= FEATURE_RANGES[:, 1]))


def _vector_digest(values: np.ndarray) -> bytes:
    return hashlib.blake2b(np.asarray(values, dtype="<f8").tobytes(), digest_size=16).digest()


def _string_list_digest(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


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
