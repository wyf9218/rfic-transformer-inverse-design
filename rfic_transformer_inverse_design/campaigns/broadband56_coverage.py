"""Streaming secondary coverage accounting for broadband56 balanced-200k.

The accumulator stores only fixed-size histograms and running moments.  Its
memory use is independent of the number of accepted real-EMX records.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Mapping

import numpy as np

from .broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    COVERAGE_PHASES,
    COVERAGE_POPULATIONS,
    FREQUENCY_GRID_HZ,
    PRIMARY_CELLS_PER_ANCHOR,
    SECONDARY_BINS_PER_FEATURE,
    SECONDARY_FEATURES,
    SECONDARY_PAIRWISE_FEATURES,
    occupancy_metrics,
    primary_cell_for_values,
    secondary_bin_edges,
)


BIN_CLASS_COUNT = SECONDARY_BINS_PER_FEATURE + 2
_FREQUENCY_INDEX = {frequency_hz: index for index, frequency_hz in enumerate(FREQUENCY_GRID_HZ)}
_ANCHOR_INDEX = {anchor: index for index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ)}
_FEATURE_INDEX = {name: index for index, name in enumerate(SECONDARY_FEATURES)}
_GROUP_KEYS = tuple((population, phase) for population in COVERAGE_POPULATIONS for phase in COVERAGE_PHASES)
_GROUP_INDEX = {key: index for index, key in enumerate(_GROUP_KEYS)}
_SECONDARY_EDGES = {
    name: np.asarray(edges, dtype=float) for name, edges in secondary_bin_edges().items()
}


def population_memberships(
    *,
    broadband_descriptor_valid: bool,
    strict_lumped_valid: bool,
    inside_broad_response_envelope: bool,
    inside_literature_practical_panel: bool,
) -> tuple[str, ...]:
    """Return all target-independent coverage populations for one record."""

    memberships = ["all_parseable_emx_records"]
    if broadband_descriptor_valid:
        memberships.append("broadband_descriptor_valid")
    if strict_lumped_valid:
        memberships.append("strict_lumped_valid")
    if inside_broad_response_envelope:
        memberships.append("inside_broad_response_envelope")
    if inside_literature_practical_panel:
        memberships.append("inside_literature_practical_panel")
    return tuple(memberships)


def extended_bin_index(value: float, edges: Iterable[float]) -> int:
    """Return an explicit underflow/in-range/overflow bin index."""

    number = float(value)
    edge_array = np.asarray(tuple(edges), dtype=float)
    if not math.isfinite(number):
        raise ValueError(f"non-finite value: {number}")
    if edge_array.size != SECONDARY_BINS_PER_FEATURE + 1:
        raise ValueError(f"expected {SECONDARY_BINS_PER_FEATURE + 1} edges, got {edge_array.size}")
    if number < float(edge_array[0]):
        return 0
    if number > float(edge_array[-1]):
        return BIN_CLASS_COUNT - 1
    if math.isclose(number, float(edge_array[-1]), rel_tol=0.0, abs_tol=1.0e-12):
        return SECONDARY_BINS_PER_FEATURE
    interior = int(np.searchsorted(edge_array, number, side="right") - 1)
    if not 0 <= interior < SECONDARY_BINS_PER_FEATURE:
        raise ValueError(f"failed to bin value={number}")
    return interior + 1


def bin_class_labels() -> tuple[str, ...]:
    return ("underflow", *(f"in_range_{index}" for index in range(SECONDARY_BINS_PER_FEATURE)), "overflow")


class StreamingPhysicalCoverage:
    """Accumulate fixed physical marginals and pairwise occupancy by phase."""

    def __init__(self) -> None:
        groups = len(_GROUP_KEYS)
        frequencies = len(FREQUENCY_GRID_HZ)
        features = len(SECONDARY_FEATURES)
        pairs = len(SECONDARY_PAIRWISE_FEATURES)
        self.marginal_counts = np.zeros(
            (groups, frequencies, features, BIN_CLASS_COUNT), dtype=np.int64
        )
        self.pairwise_counts = np.zeros(
            (groups, frequencies, pairs, BIN_CLASS_COUNT, BIN_CLASS_COUNT), dtype=np.int64
        )
        self.primary_counts = np.zeros(
            (groups, len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR), dtype=np.int64
        )
        shape = (groups, frequencies, features)
        self.stat_count = np.zeros(shape, dtype=np.int64)
        self.stat_sum = np.zeros(shape, dtype=np.float64)
        self.stat_sum_squares = np.zeros(shape, dtype=np.float64)
        self.stat_min = np.full(shape, np.inf, dtype=np.float64)
        self.stat_max = np.full(shape, -np.inf, dtype=np.float64)

    def add_record(
        self,
        *,
        frequency_hz: int,
        values: Mapping[str, float],
        populations: Iterable[str],
        campaign_phase: str,
    ) -> None:
        frequency = int(frequency_hz)
        if frequency not in _FREQUENCY_INDEX:
            raise ValueError(f"frequency is not on exact broadband56 grid: {frequency}")
        phase = str(campaign_phase)
        if phase not in COVERAGE_PHASES[1:]:
            raise ValueError(f"invalid campaign phase for record: {phase!r}")
        numeric = np.asarray([float(values[name]) for name in SECONDARY_FEATURES], dtype=float)
        if not np.all(np.isfinite(numeric)):
            raise ValueError("secondary physical feature vector is not finite")
        bin_indices = np.asarray(
            [
                extended_bin_index(value, _SECONDARY_EDGES[name])
                for name, value in zip(SECONDARY_FEATURES, numeric)
            ],
            dtype=np.int64,
        )
        frequency_index = _FREQUENCY_INDEX[frequency]
        feature_indices = np.arange(len(SECONDARY_FEATURES), dtype=np.int64)
        memberships = tuple(dict.fromkeys(str(value) for value in populations))
        unknown = set(memberships) - set(COVERAGE_POPULATIONS)
        if unknown:
            raise ValueError(f"unknown coverage population(s): {sorted(unknown)}")
        anchor_ghz = frequency // 1_000_000_000
        primary_cell = None
        if anchor_ghz in _ANCHOR_INDEX:
            primary_cell = primary_cell_for_values(
                anchor_ghz=anchor_ghz,
                xp_ohm=float(values["xp_ohm"]),
                xs_ohm=float(values["xs_ohm"]),
                qmin=float(values["qmin"]),
                k_abs=float(values["k_abs"]),
            )

        for population in memberships:
            for phase_scope in ("ALL", phase):
                group_index = _GROUP_INDEX[(population, phase_scope)]
                self.marginal_counts[group_index, frequency_index, feature_indices, bin_indices] += 1
                self.stat_count[group_index, frequency_index, :] += 1
                self.stat_sum[group_index, frequency_index, :] += numeric
                self.stat_sum_squares[group_index, frequency_index, :] += numeric * numeric
                self.stat_min[group_index, frequency_index, :] = np.minimum(
                    self.stat_min[group_index, frequency_index, :], numeric
                )
                self.stat_max[group_index, frequency_index, :] = np.maximum(
                    self.stat_max[group_index, frequency_index, :], numeric
                )
                for pair_index, (left, right) in enumerate(SECONDARY_PAIRWISE_FEATURES):
                    left_bin = int(bin_indices[_FEATURE_INDEX[left]])
                    right_bin = int(bin_indices[_FEATURE_INDEX[right]])
                    self.pairwise_counts[
                        group_index, frequency_index, pair_index, left_bin, right_bin
                    ] += 1
                if primary_cell is not None:
                    self.primary_counts[
                        group_index,
                        _ANCHOR_INDEX[anchor_ghz],
                        primary_cell.local_index,
                    ] += 1

    def internal_errors(self) -> list[str]:
        """Return accounting inconsistencies without weakening any input gate."""

        errors: list[str] = []
        reference = self.stat_count[:, :, 0]
        for feature_index, feature in enumerate(SECONDARY_FEATURES):
            marginal_total = self.marginal_counts[:, :, feature_index, :].sum(axis=-1)
            if not np.array_equal(marginal_total, reference):
                errors.append(f"marginal total mismatch for {feature}")
            if not np.array_equal(self.stat_count[:, :, feature_index], reference):
                errors.append(f"running-stat count mismatch for {feature}")
        for pair_index, pair in enumerate(SECONDARY_PAIRWISE_FEATURES):
            pair_total = self.pairwise_counts[:, :, pair_index, :, :].sum(axis=(-1, -2))
            if not np.array_equal(pair_total, reference):
                errors.append(f"pairwise total mismatch for {pair[0]}__{pair[1]}")
        for population in COVERAGE_POPULATIONS:
            overall = self.marginal_counts[_GROUP_INDEX[(population, "ALL")]]
            phases = sum(
                (self.marginal_counts[_GROUP_INDEX[(population, phase)]] for phase in COVERAGE_PHASES[1:]),
                np.zeros_like(overall),
            )
            if not np.array_equal(overall, phases):
                errors.append(f"phase marginals do not sum to ALL for {population}")
            overall_primary = self.primary_counts[_GROUP_INDEX[(population, "ALL")]]
            phase_primary = sum(
                (self.primary_counts[_GROUP_INDEX[(population, phase)]] for phase in COVERAGE_PHASES[1:]),
                np.zeros_like(overall_primary),
            )
            if not np.array_equal(overall_primary, phase_primary):
                errors.append(f"phase primary counts do not sum to ALL for {population}")
        return errors

    def primary_counts_for(self, population: str, phase: str = "ALL") -> dict[int, np.ndarray]:
        group_index = _GROUP_INDEX[(str(population), str(phase))]
        return {
            anchor: self.primary_counts[group_index, anchor_index].copy()
            for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ)
        }

    def primary_summary(self) -> dict[str, Any]:
        groups: list[dict[str, Any]] = []
        for group_index, (population, phase) in enumerate(_GROUP_KEYS):
            anchor_metrics: dict[str, Any] = {}
            flattened_counts = self.primary_counts[group_index].reshape(-1)
            total_anchor_records = 0
            for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
                frequency_index = _FREQUENCY_INDEX[anchor * 1_000_000_000]
                anchor_records = int(self.stat_count[group_index, frequency_index, 0])
                total_anchor_records += anchor_records
                counts = self.primary_counts[group_index, anchor_index]
                anchor_metrics[str(anchor)] = {
                    "anchor_record_count": anchor_records,
                    "in_primary_cells": int(counts.sum()),
                    "outside_primary_cells": anchor_records - int(counts.sum()),
                    **occupancy_metrics(counts, accepted_count=anchor_records),
                }
            groups.append(
                {
                    "population": population,
                    "campaign_phase": phase,
                    "anchor_record_count": total_anchor_records,
                    "in_primary_cells": int(flattened_counts.sum()),
                    "outside_primary_cells": total_anchor_records - int(flattened_counts.sum()),
                    "combined_anchor_metrics": occupancy_metrics(
                        flattened_counts, accepted_count=total_anchor_records
                    ),
                    "by_anchor_ghz": anchor_metrics,
                }
            )
        return {
            "counting_basis": "geometry_unique_anchor_coverage",
            "maximum_contributions_per_geometry": len(ANCHOR_FREQUENCIES_GHZ),
            "groups": groups,
            "scientific_boundary": (
                "Each geometry contributes at most one actual real-EMX response cell per anchor. "
                "Records outside the frozen primary edges remain visible as outside_primary_cells."
            ),
        }

    def frequency_summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for group_index, (population, phase) in enumerate(_GROUP_KEYS):
            for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
                for feature_index, feature in enumerate(SECONDARY_FEATURES):
                    counts = self.marginal_counts[group_index, frequency_index, feature_index]
                    total = int(counts.sum())
                    metrics = occupancy_metrics(counts, accepted_count=total)
                    count = int(self.stat_count[group_index, frequency_index, feature_index])
                    mean, std, minimum, maximum = self._moments(group_index, frequency_index, feature_index)
                    rows.append(
                        {
                            "counting_basis": "record_weighted_coverage",
                            "population": population,
                            "campaign_phase": phase,
                            "frequency_hz": frequency_hz,
                            "feature": feature,
                            "record_count": count,
                            "mean": mean,
                            "std": std,
                            "min": minimum,
                            "max": maximum,
                            "underflow_count": int(counts[0]),
                            "in_range_count": int(counts[1:-1].sum()),
                            "overflow_count": int(counts[-1]),
                            **_metric_columns(metrics),
                        }
                    )
        return rows

    def marginal_rows(self) -> list[dict[str, Any]]:
        labels = bin_class_labels()
        edges = secondary_bin_edges()
        rows: list[dict[str, Any]] = []
        for group_index, (population, phase) in enumerate(_GROUP_KEYS):
            for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
                for feature_index, feature in enumerate(SECONDARY_FEATURES):
                    counts = self.marginal_counts[group_index, frequency_index, feature_index]
                    total = int(counts.sum())
                    metrics = occupancy_metrics(counts, accepted_count=total)
                    rows.append(
                        {
                            "counting_basis": "record_weighted_coverage",
                            "population": population,
                            "campaign_phase": phase,
                            "frequency_hz": frequency_hz,
                            "feature": feature,
                            "record_count": total,
                            "bin_edges_json": _compact_json(edges[feature]),
                            "bin_class_labels_json": _compact_json(labels),
                            "bin_counts_json": _compact_json(counts.astype(int).tolist()),
                            **_metric_columns(metrics),
                        }
                    )
        return rows

    def pairwise_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        edges = secondary_bin_edges()
        labels = bin_class_labels()
        for group_index, (population, phase) in enumerate(_GROUP_KEYS):
            for frequency_index, frequency_hz in enumerate(FREQUENCY_GRID_HZ):
                for pair_index, (left, right) in enumerate(SECONDARY_PAIRWISE_FEATURES):
                    counts = self.pairwise_counts[group_index, frequency_index, pair_index]
                    rows.append(
                        _pair_row(
                            scope="feature_pair_at_exact_frequency",
                            population=population,
                            phase=phase,
                            frequency_hz=frequency_hz,
                            left=left,
                            right=right,
                            left_bins=edges[left],
                            right_bins=edges[right],
                            left_labels=labels,
                            right_labels=labels,
                            counts=counts,
                        )
                    )
            for feature_index, feature in enumerate(SECONDARY_FEATURES):
                counts = self.marginal_counts[group_index, :, feature_index, :]
                rows.append(
                    _pair_row(
                        scope="frequency_vs_feature",
                        population=population,
                        phase=phase,
                        frequency_hz=None,
                        left="frequency_hz",
                        right=feature,
                        left_bins=FREQUENCY_GRID_HZ,
                        right_bins=edges[feature],
                        left_labels=tuple(str(value) for value in FREQUENCY_GRID_HZ),
                        right_labels=labels,
                        counts=counts,
                    )
                )
        return rows

    def summary(self) -> dict[str, Any]:
        groups = []
        for group_index, (population, phase) in enumerate(_GROUP_KEYS):
            by_frequency = self.stat_count[group_index, :, 0]
            groups.append(
                {
                    "population": population,
                    "campaign_phase": phase,
                    "geometry_frequency_records": int(by_frequency.sum()),
                    "frequencies_with_records": int(np.sum(by_frequency > 0)),
                    "minimum_records_at_any_frequency": int(by_frequency.min()),
                    "maximum_records_at_any_frequency": int(by_frequency.max()),
                }
            )
        return {
            "counting_basis": "record_weighted_coverage",
            "fixed_frequency_points": len(FREQUENCY_GRID_HZ),
            "fixed_bin_classes_per_feature": BIN_CLASS_COUNT,
            "underflow_and_overflow_retained": True,
            "feature_order": list(SECONDARY_FEATURES),
            "pairwise_feature_order": [list(pair) for pair in SECONDARY_PAIRWISE_FEATURES],
            "groups": groups,
            "scientific_boundary": (
                "Frequency records from one geometry are correlated. These secondary tables are "
                "record-weighted and do not replace geometry-unique anchor coverage."
            ),
        }

    def _moments(self, group_index: int, frequency_index: int, feature_index: int) -> tuple[Any, ...]:
        count = int(self.stat_count[group_index, frequency_index, feature_index])
        if count <= 0:
            return None, None, None, None
        total = float(self.stat_sum[group_index, frequency_index, feature_index])
        squares = float(self.stat_sum_squares[group_index, frequency_index, feature_index])
        mean = total / count
        variance = max(squares / count - mean * mean, 0.0)
        return (
            mean,
            math.sqrt(variance),
            float(self.stat_min[group_index, frequency_index, feature_index]),
            float(self.stat_max[group_index, frequency_index, feature_index]),
        )


def _pair_row(
    *,
    scope: str,
    population: str,
    phase: str,
    frequency_hz: int | None,
    left: str,
    right: str,
    left_bins: Iterable[Any],
    right_bins: Iterable[Any],
    left_labels: Iterable[str],
    right_labels: Iterable[str],
    counts: np.ndarray,
) -> dict[str, Any]:
    total = int(counts.sum())
    metrics = occupancy_metrics(counts.reshape(-1), accepted_count=total)
    target = float(total) / float(counts.size) if counts.size else 0.0
    return {
        "coverage_scope": scope,
        "counting_basis": "record_weighted_coverage",
        "population": population,
        "campaign_phase": phase,
        "frequency_hz": frequency_hz,
        "left_feature": left,
        "right_feature": right,
        "record_count": total,
        "matrix_shape_json": _compact_json(list(counts.shape)),
        "left_bins_json": _compact_json(tuple(left_bins)),
        "right_bins_json": _compact_json(tuple(right_bins)),
        "left_bin_labels_json": _compact_json(tuple(left_labels)),
        "right_bin_labels_json": _compact_json(tuple(right_labels)),
        "cell_counts_row_major_json": _compact_json(counts.astype(int).reshape(-1).tolist()),
        "sparse_cell_count": int(np.sum(counts == 0)),
        "overrepresented_cell_count": int(np.sum(counts > target)) if total > 0 else 0,
        **_metric_columns(metrics),
    }


def _metric_columns(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "total_bins_or_cells": metrics.get("total_cells"),
        "observed_bins_or_cells": metrics.get("observed_cells"),
        "observed_fraction": metrics.get("observed_cell_fraction"),
        "normalized_entropy": metrics.get("normalized_entropy"),
        "coefficient_of_variation": metrics.get("coefficient_of_variation"),
        "gini_coefficient": metrics.get("gini_coefficient"),
        "jensen_shannon_divergence_from_uniform": metrics.get("jensen_shannon_divergence_from_uniform"),
        "underfilled_bins_or_cells": metrics.get("underfilled_cells"),
    }


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
