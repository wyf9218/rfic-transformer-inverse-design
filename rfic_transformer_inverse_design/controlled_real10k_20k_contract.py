"""Frozen constants shared by the controlled real-EMX 10K/20K experiment.

This module deliberately contains no result access, data selection, training,
or launch authority.  It is the single source of truth for column order,
declared normalization bounds, physical-cell encoding, and paired seeds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
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
OUTPUT_COLUMNS = (
    "controlled_source_row_number",
    "controlled_origin",
    "controlled_physical_cell_4d",
    "controlled_split_assignment",
    "canonical_geometry_identity_sha256",
    "portable_geometry_decimal12_sha256",
    "evaluation",
    "touchstone_path",
    "touchstone_sha256",
    *INPUT_COLUMNS,
    *GEOMETRY_COLUMNS,
)

INPUT_LOWER = (0.5, 0.5, 5.0, 0.0)
INPUT_UPPER = (3.0, 3.0, 25.0, 0.8)
GEOMETRY_LOWER = (
    160.0,
    160.0,
    160.0,
    160.0,
    3.0,
    20.0,
    20.0,
    -90.0,
    100.0,
    100.0,
)
GEOMETRY_UPPER = (
    520.0,
    520.0,
    520.0,
    520.0,
    12.0,
    90.0,
    90.0,
    90.0,
    320.0,
    320.0,
)

PHYSICAL_CELL_BINS = 4
PHYSICAL_CELL_ENCODING = "colon_separated_zero_based_bin_indices_v1"
EXACT_PAIRED_SEEDS = (20260711, 20260712, 20260713)
EXACT_EXTRA_SELECTION_SEED = 20260824


def canonical_physical_cell_id(
    values: Iterable[float], *, bins: int = PHYSICAL_CELL_BINS
) -> str:
    """Return the frozen ``0:1:2:3`` 4-D cell identity.

    Values must be finite and within the declared physical domain.  The upper
    endpoint is assigned to the last bin, matching the historical splitter.
    """

    numeric = tuple(float(value) for value in values)
    if len(numeric) != len(INPUT_COLUMNS):
        raise ValueError(
            f"physical-cell input has {len(numeric)} dimensions; expected {len(INPUT_COLUMNS)}"
        )
    if int(bins) != PHYSICAL_CELL_BINS:
        raise ValueError(
            f"physical-cell bin count must remain frozen at {PHYSICAL_CELL_BINS}"
        )
    if any(not math.isfinite(value) for value in numeric):
        raise ValueError("physical-cell input contains non-finite values")
    if any(
        value < lower or value > upper
        for value, lower, upper in zip(numeric, INPUT_LOWER, INPUT_UPPER)
    ):
        raise ValueError("physical-cell input lies outside the frozen declared bounds")
    cell: list[int] = []
    for value, lower, upper in zip(numeric, INPUT_LOWER, INPUT_UPPER):
        scaled = (value - lower) / (upper - lower)
        index = int(math.floor(scaled * PHYSICAL_CELL_BINS))
        cell.append(max(0, min(PHYSICAL_CELL_BINS - 1, index)))
    return ":".join(str(value) for value in cell)


__all__ = [
    "EXACT_PAIRED_SEEDS",
    "EXACT_EXTRA_SELECTION_SEED",
    "GEOMETRY_COLUMNS",
    "GEOMETRY_LOWER",
    "GEOMETRY_UPPER",
    "INPUT_COLUMNS",
    "INPUT_LOWER",
    "INPUT_UPPER",
    "OUTPUT_COLUMNS",
    "PHYSICAL_CELL_BINS",
    "PHYSICAL_CELL_ENCODING",
    "canonical_physical_cell_id",
]
