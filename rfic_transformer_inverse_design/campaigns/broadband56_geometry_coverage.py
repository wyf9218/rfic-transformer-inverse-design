"""Geometry-unique coverage audit for the frozen broadband56 10-D space."""

from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial import cKDTree

from .broadband56_balanced200k import (
    CAMPAIGN_ID,
    GEOMETRY_BOUNDARY_FRACTION_PER_SIDE,
    GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
    GEOMETRY_FIELDS,
    geometry_coverage_contract,
    occupancy_metrics,
)


def geometry_bounds_payload(
    *,
    bounds: Mapping[str, Sequence[float]],
    contract_fingerprint_sha256: str,
) -> dict[str, Any]:
    """Build the sanitized bounds artifact frozen by private preflight."""

    normalized = {
        name: [float(bounds[name][0]), float(bounds[name][1])]
        for name in GEOMETRY_FIELDS
    }
    return {
        "schema": "rfic_transformer.broadband56_geometry_bounds.v1",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": str(contract_fingerprint_sha256),
        "geometry_coverage_contract": geometry_coverage_contract(),
        "field_bounds_um": normalized,
        "contains_private_runtime_paths": False,
    }


def validate_geometry_bounds_payload(
    payload: Mapping[str, Any], *, contract_fingerprint_sha256: str
) -> list[str]:
    errors: list[str] = []
    if payload.get("campaign_id") != CAMPAIGN_ID:
        errors.append("campaign_id mismatch")
    if payload.get("contract_fingerprint_sha256") != str(contract_fingerprint_sha256):
        errors.append("contract fingerprint mismatch")
    if payload.get("geometry_coverage_contract") != geometry_coverage_contract():
        errors.append("geometry coverage contract mismatch")
    bounds = payload.get("field_bounds_um") or {}
    if tuple(bounds) != GEOMETRY_FIELDS:
        errors.append("geometry bounds field order mismatch")
    for name in GEOMETRY_FIELDS:
        value = bounds.get(name)
        try:
            low, high = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, IndexError):
            errors.append(f"invalid bounds for {name}")
            continue
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            errors.append(f"non-finite or non-increasing bounds for {name}")
    return errors


class GeometryCoverageAudit:
    """Compute fixed-bin occupancy and nearest-neighbor evidence once per geometry."""

    def __init__(
        self,
        *,
        matrix_um: np.ndarray,
        bounds: Mapping[str, Sequence[float]],
        geometry_hashes: Sequence[str],
    ) -> None:
        matrix = np.asarray(matrix_um, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(GEOMETRY_FIELDS):
            raise ValueError(
                f"expected geometry matrix with {len(GEOMETRY_FIELDS)} columns, got {matrix.shape}"
            )
        if not np.all(np.isfinite(matrix)):
            raise ValueError("geometry matrix contains non-finite values")
        if matrix.shape[0] != len(geometry_hashes):
            raise ValueError("geometry matrix and hash count differ")
        self.matrix_um = matrix
        self.geometry_hashes = tuple(str(value) for value in geometry_hashes)
        self.bounds = {
            name: (float(bounds[name][0]), float(bounds[name][1]))
            for name in GEOMETRY_FIELDS
        }
        lower = np.asarray([self.bounds[name][0] for name in GEOMETRY_FIELDS], dtype=float)
        upper = np.asarray([self.bounds[name][1] for name in GEOMETRY_FIELDS], dtype=float)
        if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(upper <= lower):
            raise ValueError("geometry bounds are non-finite or non-increasing")
        self.normalized = (matrix - lower) / (upper - lower)
        tolerance = 1.0e-12
        outside = np.argwhere((self.normalized < -tolerance) | (self.normalized > 1.0 + tolerance))
        self.outside_examples = [
            {
                "row_index": int(row),
                "field": GEOMETRY_FIELDS[int(column)],
                "normalized_value": float(self.normalized[row, column]),
            }
            for row, column in outside[:20]
        ]
        self.in_bounds = outside.size == 0
        self.normalized = np.clip(self.normalized, 0.0, 1.0)
        self.marginal_counts = np.stack(
            [
                np.histogram(
                    self.normalized[:, index],
                    bins=GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
                    range=(0.0, 1.0),
                )[0]
                for index in range(len(GEOMETRY_FIELDS))
            ]
        ).astype(np.int64)
        self.pairs = tuple(combinations(range(len(GEOMETRY_FIELDS)), 2))
        self.pairwise_counts = np.stack(
            [
                np.histogram2d(
                    self.normalized[:, left],
                    self.normalized[:, right],
                    bins=GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
                    range=((0.0, 1.0), (0.0, 1.0)),
                )[0]
                for left, right in self.pairs
            ]
        ).astype(np.int64)

    def internal_errors(self) -> list[str]:
        errors: list[str] = []
        count = self.matrix_um.shape[0]
        if not self.in_bounds:
            errors.append(f"geometry rows outside frozen bounds: {self.outside_examples}")
        if len(set(self.geometry_hashes)) != count:
            errors.append("geometry hashes are not unique")
        if not np.all(self.marginal_counts.sum(axis=1) == count):
            errors.append("geometry marginal counts do not sum to geometry count")
        if not np.all(self.pairwise_counts.sum(axis=(1, 2)) == count):
            errors.append("geometry pairwise counts do not sum to geometry count")
        return errors

    def marginal_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        count = int(self.matrix_um.shape[0])
        for index, field in enumerate(GEOMETRY_FIELDS):
            counts = self.marginal_counts[index]
            metrics = occupancy_metrics(counts, accepted_count=count)
            low, high = self.bounds[field]
            column = self.normalized[:, index]
            rows.append(
                {
                    "counting_basis": "geometry_unique",
                    "field": field,
                    "geometry_count": count,
                    "lower_bound_um": low,
                    "upper_bound_um": high,
                    "unit_bin_edges_json": _compact_json(
                        np.linspace(0.0, 1.0, GEOMETRY_COVERAGE_BINS_PER_DIMENSION + 1).tolist()
                    ),
                    "bin_counts_json": _compact_json(counts.astype(int).tolist()),
                    "lower_boundary_count": int(
                        np.sum(column <= GEOMETRY_BOUNDARY_FRACTION_PER_SIDE)
                    ),
                    "upper_boundary_count": int(
                        np.sum(column >= 1.0 - GEOMETRY_BOUNDARY_FRACTION_PER_SIDE)
                    ),
                    **_metric_columns(metrics),
                }
            )
        return rows

    def pairwise_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        count = int(self.matrix_um.shape[0])
        target = count / float(GEOMETRY_COVERAGE_BINS_PER_DIMENSION**2) if count else 0.0
        for pair_index, (left, right) in enumerate(self.pairs):
            counts = self.pairwise_counts[pair_index]
            metrics = occupancy_metrics(counts.reshape(-1), accepted_count=count)
            rows.append(
                {
                    "counting_basis": "geometry_unique",
                    "left_field": GEOMETRY_FIELDS[left],
                    "right_field": GEOMETRY_FIELDS[right],
                    "geometry_count": count,
                    "matrix_shape_json": _compact_json(list(counts.shape)),
                    "cell_counts_row_major_json": _compact_json(
                        counts.astype(int).reshape(-1).tolist()
                    ),
                    "sparse_cell_count": int(np.sum(counts == 0)),
                    "overrepresented_cell_count": int(np.sum(counts > target)) if count else 0,
                    **_metric_columns(metrics),
                }
            )
        return rows

    def summary(self) -> dict[str, Any]:
        count = int(self.matrix_um.shape[0])
        boundary = (self.normalized <= GEOMETRY_BOUNDARY_FRACTION_PER_SIDE) | (
            self.normalized >= 1.0 - GEOMETRY_BOUNDARY_FRACTION_PER_SIDE
        )
        marginal_metrics = {
            field: occupancy_metrics(self.marginal_counts[index], accepted_count=count)
            for index, field in enumerate(GEOMETRY_FIELDS)
        }
        pairwise_metrics = {
            f"{GEOMETRY_FIELDS[left]}__{GEOMETRY_FIELDS[right]}": occupancy_metrics(
                self.pairwise_counts[index].reshape(-1), accepted_count=count
            )
            for index, (left, right) in enumerate(self.pairs)
        }
        return {
            "counting_basis": "geometry_unique",
            "geometry_count": count,
            "canonical_hash_unique_count": len(set(self.geometry_hashes)),
            "in_frozen_bounds": self.in_bounds,
            "outside_frozen_bounds_examples": self.outside_examples,
            "field_order": list(GEOMETRY_FIELDS),
            "bins_per_dimension": GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
            "boundary_fraction_per_side": GEOMETRY_BOUNDARY_FRACTION_PER_SIDE,
            "geometries_on_any_boundary": int(np.sum(np.any(boundary, axis=1))) if count else 0,
            "geometries_on_any_boundary_fraction": (
                float(np.mean(np.any(boundary, axis=1))) if count else None
            ),
            "nearest_neighbor_distance": self._nearest_neighbor_summary(),
            "marginal_metrics": marginal_metrics,
            "pairwise_metrics": pairwise_metrics,
            "scientific_boundary": (
                "This is normalized geometry-space coverage, not physical-response uniformity "
                "and not Cadence, Calibre, or EMX evidence."
            ),
        }

    def _nearest_neighbor_summary(self) -> dict[str, Any]:
        count, dimensions = self.normalized.shape
        if count < 2:
            return {"status": "NOT_READY", "geometry_count": int(count)}
        tree = cKDTree(self.normalized)
        distances, _ = tree.query(self.normalized, k=2)
        nearest = np.asarray(distances[:, 1], dtype=float)
        finite = nearest[np.isfinite(nearest)]
        if finite.size != count:
            return {
                "status": "FAIL",
                "geometry_count": int(count),
                "finite_distance_count": int(finite.size),
            }
        expected_scale = float(count ** (-1.0 / dimensions))
        return {
            "status": "PASS",
            "geometry_count": int(count),
            "dimension_count": int(dimensions),
            "minimum": float(np.min(finite)),
            "p05": float(np.quantile(finite, 0.05)),
            "median": float(np.median(finite)),
            "mean": float(np.mean(finite)),
            "p95": float(np.quantile(finite, 0.95)),
            "maximum": float(np.max(finite)),
            "unit_cube_expected_scale": expected_scale,
            "median_to_expected_scale_ratio": float(np.median(finite) / expected_scale),
        }


def _metric_columns(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "total_bins_or_cells": metrics.get("total_cells"),
        "observed_bins_or_cells": metrics.get("observed_cells"),
        "observed_fraction": metrics.get("observed_cell_fraction"),
        "normalized_entropy": metrics.get("normalized_entropy"),
        "coefficient_of_variation": metrics.get("coefficient_of_variation"),
        "gini_coefficient": metrics.get("gini_coefficient"),
        "jensen_shannon_divergence_from_uniform": metrics.get(
            "jensen_shannon_divergence_from_uniform"
        ),
        "underfilled_bins_or_cells": metrics.get("underfilled_cells"),
    }


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
