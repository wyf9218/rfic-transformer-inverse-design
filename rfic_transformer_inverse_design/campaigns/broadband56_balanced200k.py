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
SECONDARY_BINS_PER_FEATURE = 6
GEOMETRY_COVERAGE_BINS_PER_DIMENSION = 10
GEOMETRY_BOUNDARY_FRACTION_PER_SIDE = 0.10
REQUIRED_CHECKPOINT_COUNTS = (
    100,
    1_000,
    5_000,
    20_000,
    50_000,
    75_000,
    100_000,
    125_000,
    150_000,
    175_000,
    200_000,
)
ADAPTIVE_BATCH_SIZE = 5_000
ADAPTIVE_ROUND_START_COUNTS = tuple(range(50_000, 200_000, ADAPTIVE_BATCH_SIZE))
ADAPTIVE_ROUND_END_COUNTS = tuple(value + ADAPTIVE_BATCH_SIZE for value in ADAPTIVE_ROUND_START_COUNTS)
ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS = tuple(
    value for value in ADAPTIVE_ROUND_END_COUNTS if value not in REQUIRED_CHECKPOINT_COUNTS
)

SECONDARY_FEATURES = (
    "xp_ohm",
    "xs_ohm",
    "lp_nh",
    "ls_nh",
    "qp",
    "qs",
    "qmin",
    "k_abs",
    "ls_over_lp",
)
SECONDARY_PAIRWISE_FEATURES = (
    ("xp_ohm", "xs_ohm"),
    ("lp_nh", "ls_nh"),
    ("qp", "qs"),
    ("qmin", "k_abs"),
    ("ls_over_lp", "k_abs"),
)
COVERAGE_POPULATIONS = (
    "all_parseable_emx_records",
    "broadband_descriptor_valid",
    "strict_lumped_valid",
    "inside_broad_response_envelope",
    "inside_literature_practical_panel",
)
COVERAGE_PHASES = ("ALL", "PHASE_A", "PHASE_B", "PHASE_C")
ACQUISITION_SOURCES_BY_PHASE = {
    "PHASE_A": ("base_space_filling",),
    "PHASE_B": (
        "underfilled_response_repair",
        "ensemble_uncertainty",
        "maximin_geometry_exploration",
    ),
    "PHASE_C": (
        "rare_or_underfilled_response_repair",
        "ensemble_uncertainty",
        "maximin_geometry_exploration",
    ),
}

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


@dataclass(frozen=True)
class AdaptiveRoundSpec:
    """Frozen accepted-count and acquisition-quota contract for one round."""

    phase: str
    phase_round_index: int
    global_round_index: int
    accepted_start: int
    accepted_target: int
    batch_size: int
    source_quotas: tuple[tuple[str, int], ...]
    fallback_source_quotas: tuple[tuple[str, int], ...]

    @property
    def round_id(self) -> str:
        return f"{self.phase.lower()}_round_{self.phase_round_index:02d}_{self.accepted_start:06d}_{self.accepted_target:06d}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "phase": self.phase,
            "phase_round_index": self.phase_round_index,
            "global_round_index": self.global_round_index,
            "accepted_start": self.accepted_start,
            "accepted_target": self.accepted_target,
            "batch_size": self.batch_size,
            "source_quotas": dict(self.source_quotas),
            "fallback_source_quotas": dict(self.fallback_source_quotas),
        }

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


def canonical_geometry_bounds(adapter: Any) -> dict[str, tuple[float, float]]:
    """Project the runtime search space onto the frozen independent 10-D basis."""

    active = dict(zip(adapter.field_order(), adapter.search_space.to_scipy_bounds()))
    required = set(GEOMETRY_FIELDS) - {"line_width_um"}
    missing = required - set(active)
    if missing:
        raise ValueError(f"active search space lacks campaign geometry fields: {sorted(missing)}")
    primary_width = active["primary_width_um"]
    secondary_width = active["secondary_width_um"]
    shared = (
        max(float(primary_width[0]), float(secondary_width[0])),
        min(float(primary_width[1]), float(secondary_width[1])),
    )
    if shared[1] <= shared[0]:
        raise ValueError(f"primary/secondary line-width bounds do not overlap: {shared}")
    return {
        **{
            name: tuple(map(float, active[name]))
            for name in GEOMETRY_FIELDS
            if name != "line_width_um"
        },
        "line_width_um": shared,
    }


def frequency_grid_hz() -> tuple[int, ...]:
    return FREQUENCY_GRID_HZ


def primary_bin_edges() -> dict[str, tuple[float, ...]]:
    """Return the pre-result, fixed primary response-space edges."""

    reactance = tuple(10.0 * (25.0 ** (index / 6.0)) for index in range(7))
    qmin = tuple(2.0 + (35.0 - 2.0) * index / 6.0 for index in range(7))
    k_abs = tuple(0.05 + (0.85 - 0.05) * index / 6.0 for index in range(7))
    return {"xp_ohm": reactance, "xs_ohm": reactance, "qmin": qmin, "k_abs": k_abs}


def secondary_bin_edges() -> dict[str, tuple[float, ...]]:
    """Return fixed six-bin edges for secondary physical-coverage audits.

    Values below and above these edges are retained in explicit underflow and
    overflow categories by the coverage accumulator.  The edges therefore do
    not discard otherwise valid real-EMX records.
    """

    inductance = tuple(0.03 * ((8.0 / 0.03) ** (index / 6.0)) for index in range(7))
    reactance = tuple(10.0 * (25.0 ** (index / 6.0)) for index in range(7))
    quality = tuple(2.0 + (35.0 - 2.0) * index / 6.0 for index in range(7))
    coupling = tuple(0.05 + (0.85 - 0.05) * index / 6.0 for index in range(7))
    ratio = tuple(0.25 * (16.0 ** (index / 6.0)) for index in range(7))
    return {
        "xp_ohm": reactance,
        "xs_ohm": reactance,
        "lp_nh": inductance,
        "ls_nh": inductance,
        "qp": quality,
        "qs": quality,
        "qmin": quality,
        "k_abs": coupling,
        "ls_over_lp": ratio,
    }


def secondary_coverage_contract() -> dict[str, Any]:
    """Return the pre-production, target-independent secondary audit contract."""

    return {
        "bins_per_feature": SECONDARY_BINS_PER_FEATURE,
        "underflow_and_overflow_bins_retained": True,
        "feature_order": list(SECONDARY_FEATURES),
        "pairwise_feature_order": [list(pair) for pair in SECONDARY_PAIRWISE_FEATURES],
        "populations": list(COVERAGE_POPULATIONS),
        "campaign_phases": list(COVERAGE_PHASES),
        "counting_bases": ["record_weighted_coverage", "geometry_unique_anchor_coverage"],
        "bin_edges": {name: list(edges) for name, edges in secondary_bin_edges().items()},
    }


def geometry_coverage_contract() -> dict[str, Any]:
    """Return the frozen normalized 10-D geometry-space audit definition."""

    return {
        "field_order": list(GEOMETRY_FIELDS),
        "normalization": "exact affine normalization from frozen production bounds",
        "bins_per_dimension": GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
        "pairwise_cell_shape": [
            GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
            GEOMETRY_COVERAGE_BINS_PER_DIMENSION,
        ],
        "boundary_fraction_per_side": GEOMETRY_BOUNDARY_FRACTION_PER_SIDE,
        "nearest_neighbor_metric": "euclidean_distance_in_normalized_10d_space",
        "duplicate_identity": "canonical_ordered_10d_um_sha256_v2",
    }


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


def phase_for_accepted_sequence(accepted_sequence: int) -> str:
    """Map the one-based terminal acceptance sequence to its frozen phase."""

    sequence = int(accepted_sequence)
    if sequence < 1 or sequence > TARGET_ACCEPTED_GEOMETRIES:
        raise ValueError(f"accepted_sequence outside [1,{TARGET_ACCEPTED_GEOMETRIES}]: {sequence}")
    if sequence <= 50_000:
        return "PHASE_A"
    if sequence <= 150_000:
        return "PHASE_B"
    return "PHASE_C"


def adaptive_round_spec(accepted_start: int) -> AdaptiveRoundSpec:
    """Return the exact Phase-B/C 5k round beginning at ``accepted_start``."""

    start = int(accepted_start)
    if start not in ADAPTIVE_ROUND_START_COUNTS:
        raise ValueError(
            "adaptive rounds must start at a frozen 5k boundary in [50000,195000]; "
            f"got {start}"
        )
    if start < 150_000:
        phase = "PHASE_B"
        phase_round_index = (start - 50_000) // ADAPTIVE_BATCH_SIZE + 1
        quotas = (
            ("underfilled_response_repair", 3_000),
            ("ensemble_uncertainty", 1_000),
            ("maximin_geometry_exploration", 1_000),
        )
    else:
        phase = "PHASE_C"
        phase_round_index = (start - 150_000) // ADAPTIVE_BATCH_SIZE + 1
        quotas = (
            ("rare_or_underfilled_response_repair", 3_250),
            ("ensemble_uncertainty", 1_000),
            ("maximin_geometry_exploration", 750),
        )
    if sum(value for _, value in quotas) != ADAPTIVE_BATCH_SIZE:
        raise AssertionError(f"adaptive acquisition quotas do not sum to {ADAPTIVE_BATCH_SIZE}: {quotas}")
    return AdaptiveRoundSpec(
        phase=phase,
        phase_round_index=phase_round_index,
        global_round_index=(start - 50_000) // ADAPTIVE_BATCH_SIZE + 1,
        accepted_start=start,
        accepted_target=start + ADAPTIVE_BATCH_SIZE,
        batch_size=ADAPTIVE_BATCH_SIZE,
        source_quotas=quotas,
        fallback_source_quotas=(("maximin_geometry_exploration", ADAPTIVE_BATCH_SIZE),),
    )


def adaptive_round_for_current_accepted(
    current_accepted: int,
) -> tuple[AdaptiveRoundSpec, int]:
    """Return the frozen 5k round and exact remaining accepted count.

    A physical attempt may accept fewer rows than it submits.  Replenishment
    stays inside the original 5k round and must select only the number still
    required to reach that round's accepted-count boundary.
    """

    current = int(current_accepted)
    if not 50_000 <= current < TARGET_ACCEPTED_GEOMETRIES:
        raise ValueError(
            "adaptive current accepted count must be in [50000,200000); "
            f"got {current}"
        )
    start = 50_000 + ((current - 50_000) // ADAPTIVE_BATCH_SIZE) * ADAPTIVE_BATCH_SIZE
    spec = adaptive_round_spec(start)
    remaining = spec.accepted_target - current
    if not 1 <= remaining <= ADAPTIVE_BATCH_SIZE:
        raise AssertionError(
            f"adaptive round remaining count outside [1,{ADAPTIVE_BATCH_SIZE}]: {remaining}"
        )
    return spec, remaining


def prorate_adaptive_source_quotas(
    source_quotas: Mapping[str, int] | Iterable[tuple[str, int]],
    selected_count: int,
) -> tuple[tuple[str, int], ...]:
    """Scale frozen full-round quotas to one exact replenishment shard.

    Integer allocation uses deterministic largest remainders with the frozen
    source order as the tie-breaker.  Zero allocations are omitted because a
    selector source is executable only when it receives at least one row.
    """

    items = (
        tuple((str(name), int(value)) for name, value in source_quotas.items())
        if isinstance(source_quotas, Mapping)
        else tuple((str(name), int(value)) for name, value in source_quotas)
    )
    if not items or any(not name or value <= 0 for name, value in items):
        raise ValueError("source quotas must be non-empty positive integers")
    count = int(selected_count)
    total = sum(value for _, value in items)
    if not 1 <= count <= total:
        raise ValueError(f"selected_count must be in [1,{total}]; got {count}")

    floors = [value * count // total for _, value in items]
    remainder_count = count - sum(floors)
    order = sorted(
        range(len(items)),
        key=lambda index: (-(items[index][1] * count % total), index),
    )
    for index in order[:remainder_count]:
        floors[index] += 1
    result = tuple(
        (items[index][0], allocation)
        for index, allocation in enumerate(floors)
        if allocation > 0
    )
    if sum(value for _, value in result) != count:
        raise AssertionError("prorated source quotas do not close to selected_count")
    return result


def build_phase_plan() -> dict[str, Any]:
    """Return the deterministic accepted-count and acquisition-mixture plan."""

    return {
        "seed": 20260828,
        "checkpoints": list(REQUIRED_CHECKPOINT_COUNTS),
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

    require(contract.get("secondary_coverage") == secondary_coverage_contract(), "secondary coverage contract mismatch")
    require(contract.get("geometry_coverage") == geometry_coverage_contract(), "geometry coverage contract mismatch")

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
