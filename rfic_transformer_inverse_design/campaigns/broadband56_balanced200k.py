"""Frozen contract helpers for the broadband56 balanced 200k campaign.

This module contains no simulator or surrogate implementation.  It owns the
public, non-sensitive scientific contract and the deterministic accounting
functions used before and after private Cadence/Calibre/EMX execution.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np


CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
TARGET_ACCEPTED_GEOMETRIES = 200_000
FREQUENCY_POINTS = 56
EXPECTED_FEATURE_ROWS = TARGET_ACCEPTED_GEOMETRIES * FREQUENCY_POINTS
FREQUENCY_GRID_HZ = tuple(int(value * 1_000_000_000) for value in range(5, 61))
ANCHOR_FREQUENCIES_GHZ = (8, 15, 22, 29, 36, 43, 50, 57)
AUDIT_EDGE_FREQUENCIES_GHZ = (5, 60)
PRIMARY_BINS_PER_DIMENSION = 6
PRIMARY_CELLS_PER_ANCHOR = PRIMARY_BINS_PER_DIMENSION**4
PRIMARY_FREQUENCY_CONDITIONED_CELLS = len(ANCHOR_FREQUENCIES_GHZ) * PRIMARY_CELLS_PER_ANCHOR

GEOMETRY_FIELDS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)

S_MATRIX_COLUMNS = tuple(
    f"s{row}{col}_{component}"
    for row in range(1, 5)
    for col in range(1, 5)
    for component in ("re", "im")
)
Z_MATRIX_COLUMNS = tuple(
    f"z{row}{col}_{component}"
    for row in range(1, 5)
    for col in range(1, 5)
    for component in ("re", "im")
)


@dataclass(frozen=True)
class PrimaryCell:
    """One fixed 4-D response cell at one acquisition anchor."""

    anchor_ghz: int
    xp_bin: int
    xs_bin: int
    qmin_bin: int
    k_abs_bin: int

    @property
    def local_index(self) -> int:
        return (
            ((self.xp_bin * PRIMARY_BINS_PER_DIMENSION + self.xs_bin) * PRIMARY_BINS_PER_DIMENSION
             + self.qmin_bin)
            * PRIMARY_BINS_PER_DIMENSION
            + self.k_abs_bin
        )

    @property
    def conditioned_index(self) -> int:
        anchor_index = ANCHOR_FREQUENCIES_GHZ.index(int(self.anchor_ghz))
        return anchor_index * PRIMARY_CELLS_PER_ANCHOR + self.local_index

    @property
    def cell_id(self) -> str:
        return (
            f"f{self.anchor_ghz:02d}_xp{self.xp_bin}_xs{self.xs_bin}_"
            f"q{self.qmin_bin}_k{self.k_abs_bin}"
        )


def canonical_json_bytes(payload: Any) -> bytes:
    """Return stable UTF-8 bytes for hashing a JSON-compatible value."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def contract_fingerprint(contract: Mapping[str, Any]) -> str:
    """Hash the complete frozen contract, excluding a self-referential field."""

    normalized = dict(contract)
    normalized.pop("contract_fingerprint_sha256", None)
    return sha256_bytes(canonical_json_bytes(normalized))


def canonical_geometry_sha256(
    values: Mapping[str, Any],
    *,
    fields: Sequence[str] = GEOMETRY_FIELDS,
    decimal_places: int = 9,
) -> str:
    """Create the campaign's geometry identity from ordered, quantized values."""

    ordered: list[tuple[str, str]] = []
    for field in fields:
        if field not in values:
            raise KeyError(f"missing geometry field: {field}")
        value = float(values[field])
        if not math.isfinite(value):
            raise ValueError(f"non-finite geometry field {field}: {value}")
        ordered.append((field, f"{value:.{int(decimal_places)}f}"))
    return sha256_bytes(canonical_json_bytes(ordered))


def frequency_grid_hz() -> tuple[int, ...]:
    return FREQUENCY_GRID_HZ


def primary_bin_edges() -> dict[str, tuple[float, ...]]:
    """Return the pre-result, fixed primary response-space edges."""

    reactance = tuple(10.0 * (25.0 ** (index / 6.0)) for index in range(7))
    qmin = tuple(2.0 + (35.0 - 2.0) * index / 6.0 for index in range(7))
    k_abs = tuple(0.05 + (0.85 - 0.05) * index / 6.0 for index in range(7))
    return {"xp_ohm": reactance, "xs_ohm": reactance, "qmin": qmin, "k_abs": k_abs}


def phase_for_accepted_count(accepted_count: int) -> str:
    count = int(accepted_count)
    if count < 0 or count > TARGET_ACCEPTED_GEOMETRIES:
        raise ValueError(f"accepted_count outside [0,{TARGET_ACCEPTED_GEOMETRIES}]: {count}")
    if count < 50_000:
        return "PHASE_A"
    if count < 150_000:
        return "PHASE_B"
    if count < 200_000:
        return "PHASE_C"
    return "COMPLETE_200K"


def build_phase_plan() -> dict[str, Any]:
    """Return the deterministic accepted-count and acquisition-mixture plan."""

    return {
        "seed": 20260828,
        "checkpoints": [100, 1_000, 5_000, 20_000, 50_000, 75_000, 100_000, 125_000, 150_000, 175_000, 200_000],
        "phase_a": {
            "accepted_start": 0,
            "accepted_target": 50_000,
            "sampler_preference": ["lhs_optimized", "sobol"],
            "targeted_acquisition_allowed": False,
        },
        "phase_b": {
            "accepted_start": 50_000,
            "accepted_target": 150_000,
            "accepted_batch_size": 5_000,
            "round_count": 20,
            "mixture_fractions": {
                "underfilled_response_repair": 0.60,
                "ensemble_uncertainty": 0.20,
                "maximin_geometry_exploration": 0.20,
            },
        },
        "phase_c": {
            "accepted_start": 150_000,
            "accepted_target": 200_000,
            "accepted_batch_size": 5_000,
            "round_count": 10,
            "mixture_fractions": {
                "rare_or_underfilled_response_repair": 0.65,
                "ensemble_uncertainty": 0.20,
                "maximin_geometry_exploration": 0.15,
            },
        },
    }


def validate_contract(contract: Mapping[str, Any]) -> list[str]:
    """Return every contract violation; an empty list means static PASS."""

    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(contract.get("campaign_id") == CAMPAIGN_ID, "campaign_id mismatch")
    target = contract.get("terminal_goal") or {}
    require(int(target.get("accepted_geometries") or 0) == TARGET_ACCEPTED_GEOMETRIES, "accepted target must be 200000")
    require(int(target.get("s4p_artifacts") or 0) == TARGET_ACCEPTED_GEOMETRIES, "S4P target must be 200000")
    require(int(target.get("geometry_frequency_rows") or 0) == EXPECTED_FEATURE_ROWS, "feature-row target must be 11200000")

    grid = contract.get("frequency_grid") or {}
    require(float(grid.get("start_ghz") or 0.0) == 5.0, "frequency start must be 5 GHz")
    require(float(grid.get("stop_ghz") or 0.0) == 60.0, "frequency stop must be 60 GHz")
    require(float(grid.get("step_ghz") or 0.0) == 1.0, "frequency step must be 1 GHz")
    require(int(grid.get("points") or 0) == FREQUENCY_POINTS, "frequency points must be 56")
    require(tuple(int(value) for value in grid.get("exact_hz", [])) == FREQUENCY_GRID_HZ, "exact frequency vector mismatch")

    uniformity = contract.get("primary_uniformity") or {}
    require(tuple(uniformity.get("anchors_ghz") or ()) == ANCHOR_FREQUENCIES_GHZ, "anchor frequencies mismatch")
    require(tuple(uniformity.get("audit_edges_ghz") or ()) == AUDIT_EDGE_FREQUENCIES_GHZ, "audit edges mismatch")
    require(int(uniformity.get("bins_per_dimension") or 0) == PRIMARY_BINS_PER_DIMENSION, "primary bins must be six per dimension")
    require(int(uniformity.get("cells_per_anchor") or 0) == PRIMARY_CELLS_PER_ANCHOR, "cells per anchor must be 1296")
    require(int(uniformity.get("frequency_conditioned_cells") or 0) == PRIMARY_FREQUENCY_CONDITIONED_CELLS, "total primary cells must be 10368")

    declared_edges = uniformity.get("bin_edges") or {}
    expected_edges = primary_bin_edges()
    for name, expected in expected_edges.items():
        actual = declared_edges.get(name) or []
        require(
            len(actual) == len(expected)
            and all(math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1.0e-12) for left, right in zip(actual, expected)),
            f"{name} bin edges mismatch",
        )

    phase_plan = contract.get("phase_plan") or {}
    require(phase_plan == build_phase_plan(), "phase plan or acquisition mixture mismatch")
    require(contract.get("label_source") == "FRESH_REAL_EMX_ONLY", "label source must be fresh real EMX only")
    require(contract.get("touchstone_extension") == ".s4p", "Touchstone extension must be .s4p")
    require(int(contract.get("touchstone_ports") or 0) == 4, "Touchstone port count must be four")
    geometry_identity = contract.get("geometry_identity") or {}
    require(tuple(geometry_identity.get("fields") or ()) == GEOMETRY_FIELDS, "canonical 10-D geometry field order mismatch")
    require(int(geometry_identity.get("decimal_places") or -1) == 9, "geometry identity precision must be nine decimal places")
    require(contract.get("completion_status") == "COMPLETE_200K", "completion status vocabulary mismatch")
    require(
        tuple(contract.get("coverage_status_vocabulary") or ())
        == ("COVERAGE_PASS", "COVERAGE_PARTIAL", "COVERAGE_PHYSICALLY_LIMITED", "COVERAGE_AUDIT_FAIL"),
        "coverage status vocabulary mismatch",
    )

    inherited = contract.get("inherited_contract") or {}
    require(bool(inherited.get("required")), "previous broadband56 contract is required")
    require(bool(inherited.get("sha256_required")), "previous broadband56 SHA-256 must be verified")
    return errors


def _bin_index(value: float, edges: Sequence[float]) -> int | None:
    number = float(value)
    if not math.isfinite(number) or number < float(edges[0]) or number > float(edges[-1]):
        return None
    if math.isclose(number, float(edges[-1]), rel_tol=0.0, abs_tol=1.0e-12):
        return len(edges) - 2
    index = int(np.searchsorted(np.asarray(edges, dtype=float), number, side="right") - 1)
    return index if 0 <= index < len(edges) - 1 else None


def primary_cell_for_values(
    *, anchor_ghz: int, xp_ohm: float, xs_ohm: float, qmin: float, k_abs: float
) -> PrimaryCell | None:
    if int(anchor_ghz) not in ANCHOR_FREQUENCIES_GHZ:
        raise ValueError(f"not a primary anchor: {anchor_ghz}")
    edges = primary_bin_edges()
    indices = (
        _bin_index(xp_ohm, edges["xp_ohm"]),
        _bin_index(xs_ohm, edges["xs_ohm"]),
        _bin_index(qmin, edges["qmin"]),
        _bin_index(k_abs, edges["k_abs"]),
    )
    if any(index is None for index in indices):
        return None
    xp_bin, xs_bin, qmin_bin, k_abs_bin = (int(index) for index in indices)
    return PrimaryCell(int(anchor_ghz), xp_bin, xs_bin, qmin_bin, k_abs_bin)


def normalized_entropy(counts: Sequence[int | float]) -> float | None:
    values = np.asarray(counts, dtype=float)
    total = float(values.sum())
    if total <= 0.0 or values.size <= 1:
        return None
    probabilities = values[values > 0.0] / total
    return float(-np.sum(probabilities * np.log(probabilities)) / math.log(values.size))


def coefficient_of_variation(counts: Sequence[int | float]) -> float | None:
    values = np.asarray(counts, dtype=float)
    mean = float(values.mean()) if values.size else 0.0
    return None if mean <= 0.0 else float(values.std(ddof=0) / mean)


def gini_coefficient(counts: Sequence[int | float]) -> float | None:
    values = np.asarray(counts, dtype=float)
    if not values.size or float(values.sum()) <= 0.0:
        return None
    ordered = np.sort(values)
    n = ordered.size
    return float((2.0 * np.sum(np.arange(1, n + 1) * ordered) / (n * ordered.sum())) - (n + 1.0) / n)


def jensen_shannon_from_uniform(counts: Sequence[int | float]) -> float | None:
    values = np.asarray(counts, dtype=float)
    total = float(values.sum())
    if total <= 0.0 or values.size == 0:
        return None
    p = values / total
    q = np.full(values.size, 1.0 / values.size, dtype=float)
    m = 0.5 * (p + q)

    def kl(left: np.ndarray, right: np.ndarray) -> float:
        mask = left > 0.0
        return float(np.sum(left[mask] * np.log2(left[mask] / right[mask])))

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def occupancy_metrics(counts: Sequence[int | float], *, accepted_count: int) -> dict[str, Any]:
    values = np.asarray(counts, dtype=float)
    if values.size == 0:
        raise ValueError("counts must not be empty")
    observed = values[values > 0.0]
    target = float(accepted_count) / float(values.size)
    quantiles = np.quantile(values, [0.10, 0.50, 0.90])
    top_count = max(1, int(math.ceil(0.01 * values.size)))
    top_concentration = None if values.sum() <= 0.0 else float(np.sort(values)[-top_count:].sum() / values.sum())
    p10 = float(quantiles[0])
    p90_p10 = None if p10 <= 0.0 else float(quantiles[2] / p10)
    return {
        "total_cells": int(values.size),
        "observed_cells": int(observed.size),
        "observed_cell_fraction": float(observed.size / values.size),
        "normalized_entropy": normalized_entropy(values),
        "coefficient_of_variation": coefficient_of_variation(values),
        "gini_coefficient": gini_coefficient(values),
        "jensen_shannon_divergence_from_uniform": jensen_shannon_from_uniform(values),
        "p10_cell_occupancy": p10,
        "p50_cell_occupancy": float(quantiles[1]),
        "p90_cell_occupancy": float(quantiles[2]),
        "p90_p10_ratio": p90_p10,
        "top_1pct_cell_concentration": top_concentration,
        "equal_allocation_target_count": target,
        "underfilled_cells": int(np.sum(values < target)),
        "count_min": float(values.min()),
        "count_max": float(values.max()),
    }


def matrix_columns() -> tuple[str, ...]:
    return S_MATRIX_COLUMNS + Z_MATRIX_COLUMNS


def all_finite(values: Iterable[Any]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False
