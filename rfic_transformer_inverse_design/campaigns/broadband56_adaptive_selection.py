"""Deterministic candidate-priority policy for broadband56 adaptive rounds.

The functions in this module rank unevaluated geometries only.  Predicted
responses and ensemble uncertainty never become accepted rows or labels.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial import cKDTree

from .broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    GEOMETRY_BOUNDARY_FRACTION_PER_SIDE,
    PRIMARY_BINS_PER_DIMENSION,
    PRIMARY_CELLS_PER_ANCHOR,
    primary_bin_edges,
)


SELECTION_POLICY_SCHEMA = "broadband56_adaptive_candidate_selection_v1"
MINIMUM_CANDIDATE_POOL_FACTOR = 4
BATCH_DIVERSITY_WEIGHT = 0.15
DIVERSITY_BLOCK_SIZE = 64
DIVERSITY_LOCAL_WINDOW = 512
STATIC_PREFILTER_FACTOR = 6

PREDICTION_FEATURES = (
    "xp_ohm",
    "xs_ohm",
    "qp",
    "qs",
    "qmin",
    "k_abs",
    "feature_validity_probability",
)
UNCERTAINTY_NORMALIZERS = {
    "xp_ohm": 240.0,
    "xs_ohm": 240.0,
    "qp": 33.0,
    "qs": 33.0,
    "qmin": 33.0,
    "k_abs": 0.8,
    "feature_validity_probability": 1.0,
}
SOURCE_STATIC_WEIGHTS: dict[str, dict[str, float]] = {
    "underfilled_response_repair": {
        "deficit_gain": 0.55,
        "uncertainty": 0.10,
        "geometry_novelty": 0.10,
        "boundary_coverage": 0.10,
        "feature_validity": 0.15,
    },
    "rare_or_underfilled_response_repair": {
        "deficit_gain": 0.45,
        "uncertainty": 0.10,
        "geometry_novelty": 0.10,
        "boundary_coverage": 0.20,
        "feature_validity": 0.15,
    },
    "ensemble_uncertainty": {
        "deficit_gain": 0.10,
        "uncertainty": 0.55,
        "geometry_novelty": 0.15,
        "boundary_coverage": 0.05,
        "feature_validity": 0.15,
    },
}


def selection_policy_contract() -> dict[str, Any]:
    """Return the exact pre-EMX candidate-priority policy."""

    return {
        "schema": SELECTION_POLICY_SCHEMA,
        "minimum_candidate_pool_factor": MINIMUM_CANDIDATE_POOL_FACTOR,
        "prediction_features": list(PREDICTION_FEATURES),
        "prediction_column_pattern": "pred__<feature>__<anchor>ghz",
        "uncertainty_column_pattern": "unc__<feature>__<anchor>ghz",
        "qmin_consistency": "derive min(qp,qs) and require declared qmin absolute error <=1e-6",
        "deficit_gain": "mean normalized real-EMX cell deficit across all eight anchors",
        "uncertainty_normalizers": dict(UNCERTAINTY_NORMALIZERS),
        "geometry_novelty": "nearest accepted geometry distance in frozen normalized 10-D space",
        "boundary_coverage": (
            "accepted-count deficit across both frozen 10-percent boundary slabs of each normalized geometry dimension"
        ),
        "feature_validity": "minimum predicted validity probability across all eight anchors",
        "source_static_weights": SOURCE_STATIC_WEIGHTS,
        "batch_diversity": {
            "method": "deterministic block-greedy minimum-distance update",
            "dynamic_weight": BATCH_DIVERSITY_WEIGHT,
            "block_size": DIVERSITY_BLOCK_SIZE,
            "local_window": DIVERSITY_LOCAL_WINDOW,
            "static_prefilter_factor": STATIC_PREFILTER_FACTOR,
            "maximin_source_uses_only_geometry_distance": True,
        },
        "candidate_predictions_are_labels": False,
        "selected_label_status": "AWAITING_FRESH_REAL_EMX",
    }


def prediction_column(feature: str, anchor_ghz: int) -> str:
    return f"pred__{feature}__{int(anchor_ghz)}ghz"


def uncertainty_column(feature: str, anchor_ghz: int) -> str:
    return f"unc__{feature}__{int(anchor_ghz)}ghz"


def required_prediction_columns() -> tuple[str, ...]:
    columns: list[str] = []
    for anchor in ANCHOR_FREQUENCIES_GHZ:
        columns.extend(prediction_column(feature, anchor) for feature in PREDICTION_FEATURES)
        columns.extend(uncertainty_column(feature, anchor) for feature in PREDICTION_FEATURES)
    return tuple(columns)


def compute_candidate_components(
    *,
    candidate_geometry_normalized: np.ndarray,
    accepted_geometry_normalized: np.ndarray,
    predictions: Mapping[str, np.ndarray],
    uncertainties: Mapping[str, np.ndarray],
    coverage_deficits: np.ndarray,
    coverage_targets: np.ndarray,
) -> dict[str, np.ndarray]:
    """Compute traceable priority components without creating labels."""

    candidate = _matrix(candidate_geometry_normalized, "candidate_geometry_normalized")
    accepted = _matrix(accepted_geometry_normalized, "accepted_geometry_normalized")
    if candidate.shape[1] != accepted.shape[1]:
        raise ValueError("candidate and accepted geometry dimensions differ")
    if accepted.shape[0] < 1:
        raise ValueError("accepted geometry matrix must not be empty")
    count = candidate.shape[0]
    predicted = {name: _anchor_matrix(predictions.get(name), count, f"predictions[{name}]") for name in PREDICTION_FEATURES}
    uncertainty = {
        name: _anchor_matrix(uncertainties.get(name), count, f"uncertainties[{name}]")
        for name in PREDICTION_FEATURES
    }
    if np.any(predicted["feature_validity_probability"] < 0.0) or np.any(
        predicted["feature_validity_probability"] > 1.0
    ):
        raise ValueError("feature validity probabilities must lie in [0,1]")
    if any(np.any(values < 0.0) for values in uncertainty.values()):
        raise ValueError("ensemble uncertainties must be nonnegative")

    derived_qmin = np.minimum(predicted["qp"], predicted["qs"])
    if not np.allclose(predicted["qmin"], derived_qmin, rtol=0.0, atol=1.0e-6):
        raise ValueError("declared qmin is inconsistent with min(qp,qs)")
    predicted["qmin"] = derived_qmin

    deficits = np.asarray(coverage_deficits, dtype=float)
    targets = np.asarray(coverage_targets, dtype=float)
    expected_shape = (len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR)
    if deficits.shape != expected_shape or targets.shape != expected_shape:
        raise ValueError(f"coverage deficit/target arrays must have shape {expected_shape}")
    if not np.all(np.isfinite(deficits)) or not np.all(np.isfinite(targets)):
        raise ValueError("coverage deficit/target arrays contain non-finite values")
    if np.any(deficits < 0.0) or np.any(targets <= 0.0):
        raise ValueError("coverage deficits must be nonnegative and targets positive")

    edges = primary_bin_edges()
    gain = np.zeros((count, len(ANCHOR_FREQUENCIES_GHZ)), dtype=float)
    predicted_cells = np.full((count, len(ANCHOR_FREQUENCIES_GHZ)), -1, dtype=np.int64)
    for anchor_index in range(len(ANCHOR_FREQUENCIES_GHZ)):
        xp = _bin_indices(predicted["xp_ohm"][:, anchor_index], edges["xp_ohm"])
        xs = _bin_indices(predicted["xs_ohm"][:, anchor_index], edges["xs_ohm"])
        qmin = _bin_indices(predicted["qmin"][:, anchor_index], edges["qmin"])
        coupling = _bin_indices(predicted["k_abs"][:, anchor_index], edges["k_abs"])
        valid = (xp >= 0) & (xs >= 0) & (qmin >= 0) & (coupling >= 0)
        local = (((xp * PRIMARY_BINS_PER_DIMENSION + xs) * PRIMARY_BINS_PER_DIMENSION + qmin)
                 * PRIMARY_BINS_PER_DIMENSION + coupling)
        predicted_cells[valid, anchor_index] = local[valid]
        gain[valid, anchor_index] = (
            deficits[anchor_index, local[valid]] / targets[anchor_index, local[valid]]
        )
    deficit_gain = np.mean(gain, axis=1)

    normalized_uncertainties = np.stack(
        [uncertainty[name] / float(UNCERTAINTY_NORMALIZERS[name]) for name in PREDICTION_FEATURES],
        axis=2,
    )
    uncertainty_rms = np.sqrt(np.mean(normalized_uncertainties**2, axis=(1, 2)))

    accepted_tree = cKDTree(accepted)
    geometry_novelty = np.asarray(accepted_tree.query(candidate, k=1)[0], dtype=float)
    boundary_coverage = _boundary_coverage_score(candidate, accepted)
    feature_validity = np.min(predicted["feature_validity_probability"], axis=1)

    raw = {
        "deficit_gain": deficit_gain,
        "uncertainty": uncertainty_rms,
        "geometry_novelty": geometry_novelty,
        "boundary_coverage": boundary_coverage,
        "feature_validity": feature_validity,
    }
    ranked = {f"{name}_rank": percentile_rank(values) for name, values in raw.items()}
    return {**raw, **ranked, "predicted_local_cells": predicted_cells}


def source_static_scores(components: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Combine frozen percentile components for each ensemble acquisition source."""

    scores: dict[str, np.ndarray] = {}
    for source, weights in SOURCE_STATIC_WEIGHTS.items():
        score = None
        for component, weight in weights.items():
            values = np.asarray(components[f"{component}_rank"], dtype=float)
            score = values * float(weight) if score is None else score + values * float(weight)
        scores[source] = np.asarray(score, dtype=float)
    return scores


def select_source_quotas(
    *,
    candidate_geometry_normalized: np.ndarray,
    accepted_geometry_normalized: np.ndarray,
    geometry_hashes: Sequence[str],
    source_quotas: Mapping[str, int],
    static_scores: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    """Select exact disjoint source quotas with deterministic batch diversity."""

    candidate = _matrix(candidate_geometry_normalized, "candidate_geometry_normalized")
    accepted = _matrix(accepted_geometry_normalized, "accepted_geometry_normalized")
    hashes = tuple(str(value) for value in geometry_hashes)
    if len(hashes) != len(candidate) or len(set(hashes)) != len(hashes):
        raise ValueError("candidate geometry hashes must be present and unique")
    quotas = {str(source): int(value) for source, value in source_quotas.items()}
    if not quotas or any(value <= 0 for value in quotas.values()):
        raise ValueError("source quotas must be positive")
    if sum(quotas.values()) > len(candidate):
        raise ValueError("candidate pool is smaller than the requested quotas")

    accepted_novelty = np.asarray(cKDTree(accepted).query(candidate, k=1)[0], dtype=float)
    tie_break = np.asarray([int(value[:16], 16) / float(16**16 - 1) for value in hashes], dtype=float)
    claimed: set[int] = set()
    assignments: dict[int, dict[str, Any]] = {}
    for source, quota in quotas.items():
        available = np.asarray([index for index in range(len(candidate)) if index not in claimed], dtype=np.int64)
        if len(available) < quota:
            raise ValueError(f"insufficient disjoint candidates for {source}")
        if source == "maximin_geometry_exploration":
            base_score = np.zeros(len(candidate), dtype=float)
            pool = available
            diversity_weight = 1.0
        else:
            if source not in static_scores:
                raise ValueError(f"missing static score for acquisition source {source}")
            base_score = np.asarray(static_scores[source], dtype=float)
            if base_score.shape != (len(candidate),) or not np.all(np.isfinite(base_score)):
                raise ValueError(f"invalid static score for acquisition source {source}")
            prefilter_count = min(len(available), max(quota, STATIC_PREFILTER_FACTOR * quota))
            order = np.lexsort((tie_break[available], -base_score[available]))
            pool = available[order[:prefilter_count]]
            diversity_weight = BATCH_DIVERSITY_WEIGHT
        prior = np.asarray(sorted(claimed), dtype=np.int64)
        selected, dynamic_scores, dynamic_distances = _block_greedy_select(
            candidate=candidate,
            pool_indices=pool,
            initial_novelty=accepted_novelty,
            prior_selected_indices=prior,
            base_score=base_score,
            tie_break=tie_break,
            quota=quota,
            diversity_weight=diversity_weight,
        )
        if len(selected) != quota:
            raise ValueError(f"selector returned {len(selected)} candidates for {source}, expected {quota}")
        for source_rank, index in enumerate(selected, start=1):
            claimed.add(index)
            assignments[index] = {
                "acquisition_source": source,
                "source_selection_rank": source_rank,
                "selection_score": float(dynamic_scores[index]),
                "batch_diversity_distance": float(dynamic_distances[index]),
            }
    return {
        "assignments": assignments,
        "selected_count": len(assignments),
        "selected_counts_by_source": {
            source: sum(1 for value in assignments.values() if value["acquisition_source"] == source)
            for source in quotas
        },
        "selected_indices": tuple(assignments),
    }


def percentile_rank(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("percentile-rank input must be one-dimensional and finite")
    if len(values) <= 1:
        return np.ones_like(values)
    result = np.empty_like(values)
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and sorted_values[stop] == sorted_values[start]:
            stop += 1
        result[order[start:stop]] = (0.5 * (start + stop - 1)) / (len(values) - 1)
        start = stop
    return result


def _block_greedy_select(
    *,
    candidate: np.ndarray,
    pool_indices: np.ndarray,
    initial_novelty: np.ndarray,
    prior_selected_indices: np.ndarray,
    base_score: np.ndarray,
    tie_break: np.ndarray,
    quota: int,
    diversity_weight: float,
) -> tuple[list[int], np.ndarray, np.ndarray]:
    pool = np.asarray(pool_indices, dtype=np.int64)
    if len(pool) < quota:
        raise ValueError("diversity pool is smaller than quota")
    minimum_distance = np.asarray(initial_novelty[pool], dtype=float).copy()
    if prior_selected_indices.size:
        distance = cKDTree(candidate[prior_selected_indices]).query(candidate[pool], k=1)[0]
        minimum_distance = np.minimum(minimum_distance, np.asarray(distance, dtype=float))
    active = np.ones(len(pool), dtype=bool)
    selected: list[int] = []
    selected_scores = np.full(len(candidate), np.nan, dtype=float)
    selected_distances = np.full(len(candidate), np.nan, dtype=float)
    max_distance = math.sqrt(candidate.shape[1])
    while len(selected) < quota:
        normalized_distance = np.clip(minimum_distance / max_distance, 0.0, 1.0)
        dynamic = (1.0 - diversity_weight) * base_score[pool] + diversity_weight * normalized_distance
        dynamic[~active] = -np.inf
        order = np.lexsort((tie_break[pool], -dynamic))
        window = [int(position) for position in order if active[int(position)]][:DIVERSITY_LOCAL_WINDOW]
        if not window:
            break
        block_target = min(DIVERSITY_BLOCK_SIZE, quota - len(selected), len(window))
        local_active = np.ones(len(window), dtype=bool)
        local_distance = minimum_distance[np.asarray(window, dtype=np.int64)].copy()
        new_indices: list[int] = []
        for _ in range(block_target):
            local_dynamic = (
                (1.0 - diversity_weight) * base_score[pool[np.asarray(window, dtype=np.int64)]]
                + diversity_weight * np.clip(local_distance / max_distance, 0.0, 1.0)
            )
            local_dynamic[~local_active] = -np.inf
            local_ties = tie_break[pool[np.asarray(window, dtype=np.int64)]]
            local_order = np.lexsort((local_ties, -local_dynamic))
            chosen_local = next((int(value) for value in local_order if local_active[int(value)]), -1)
            if chosen_local < 0:
                break
            pool_position = window[chosen_local]
            candidate_index = int(pool[pool_position])
            local_active[chosen_local] = False
            active[pool_position] = False
            selected.append(candidate_index)
            new_indices.append(candidate_index)
            selected_scores[candidate_index] = float(local_dynamic[chosen_local])
            selected_distances[candidate_index] = float(local_distance[chosen_local])
            distance = np.sqrt(
                np.sum((candidate[pool[np.asarray(window, dtype=np.int64)]] - candidate[candidate_index]) ** 2, axis=1)
            )
            local_distance = np.minimum(local_distance, distance)
        if not new_indices:
            break
        global_distance = cKDTree(candidate[np.asarray(new_indices, dtype=np.int64)]).query(candidate[pool], k=1)[0]
        minimum_distance = np.minimum(minimum_distance, np.asarray(global_distance, dtype=float))
    return selected, selected_scores, selected_distances


def _boundary_coverage_score(candidate: np.ndarray, accepted: np.ndarray) -> np.ndarray:
    fraction = float(GEOMETRY_BOUNDARY_FRACTION_PER_SIDE)
    target = max(1.0, accepted.shape[0] * fraction)
    low_deficit = np.maximum(target - np.sum(accepted <= fraction, axis=0), 0.0) / target
    high_deficit = np.maximum(target - np.sum(accepted >= 1.0 - fraction, axis=0), 0.0) / target
    low_membership = np.clip((fraction - candidate) / fraction, 0.0, 1.0)
    high_membership = np.clip((candidate - (1.0 - fraction)) / fraction, 0.0, 1.0)
    return np.mean(low_membership * low_deficit[None, :] + high_membership * high_deficit[None, :], axis=1)


def _bin_indices(values: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    edge_array = np.asarray(edges, dtype=float)
    result = np.searchsorted(edge_array, array, side="right") - 1
    result[np.isclose(array, edge_array[-1], rtol=0.0, atol=1.0e-12)] = len(edge_array) - 2
    result[(array < edge_array[0]) | (array > edge_array[-1])] = -1
    return result.astype(np.int64)


def _matrix(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[0] < 1 or array.shape[1] < 1 or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a nonempty finite matrix")
    if np.any(array < 0.0) or np.any(array > 1.0):
        raise ValueError(f"{name} must lie inside frozen normalized [0,1] bounds")
    return array


def _anchor_matrix(value: Any, count: int, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    expected = (count, len(ANCHOR_FREQUENCIES_GHZ))
    if array.shape != expected or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite matrix with shape {expected}")
    return array
