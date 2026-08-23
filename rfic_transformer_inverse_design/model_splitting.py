"""Deterministic train/validation/test splits for physical-feature models."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np


def split_physical_feature_indices(
    x: np.ndarray,
    *,
    mode: str,
    seed: int,
    validation_fraction: float,
    test_fraction: float,
    physical_cell_bins: int = 4,
    physical_cell_lower: np.ndarray | None = None,
    physical_cell_upper: np.ndarray | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Split rows randomly or by complete joint physical-feature cells.

    Grouped mode is intended for a stricter inverse-design test: every row in a
    held-out Lp/Ls/Q/|K| cell stays out of training. The returned fingerprint
    allows independent model implementations to prove they used identical row
    assignments.
    """

    values = np.asarray(x, dtype=float)
    if values.ndim != 2 or values.shape[0] < 4 or values.shape[1] < 1:
        raise ValueError("at least four finite physical-feature rows are required for splitting")
    if not np.all(np.isfinite(values)):
        raise ValueError("physical-feature split input contains non-finite values")
    _validate_fractions(validation_fraction, test_fraction)
    if mode == "random":
        return _random_split(values.shape[0], seed, validation_fraction, test_fraction)
    if mode == "physical_cell_grouped":
        return _physical_cell_grouped_split(
            values,
            seed=seed,
            validation_fraction=validation_fraction,
            test_fraction=test_fraction,
            bins=physical_cell_bins,
            lower=physical_cell_lower,
            upper=physical_cell_upper,
        )
    raise ValueError(f"unsupported physical-feature split mode: {mode}")


def parse_optional_feature_bounds(
    lower_text: str | None,
    upper_text: str | None,
    feature_count: int,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if bool(lower_text) != bool(upper_text):
        raise ValueError("--physical-cell-lower and --physical-cell-upper must be supplied together")
    if not lower_text:
        return None, None
    lower = _float_vector(str(lower_text), feature_count, "--physical-cell-lower")
    upper = _float_vector(str(upper_text), feature_count, "--physical-cell-upper")
    return lower, upper


def _validate_fractions(validation_fraction: float, test_fraction: float) -> None:
    validation = float(validation_fraction)
    test = float(test_fraction)
    if not 0.0 < validation < 1.0 or not 0.0 < test < 1.0:
        raise ValueError("validation and test fractions must each be between zero and one")
    if validation + test >= 1.0:
        raise ValueError("validation_fraction + test_fraction must be less than one")


def _random_split(
    count: int,
    seed: int,
    validation_fraction: float,
    test_fraction: float,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    rng = np.random.default_rng(int(seed))
    permutation = rng.permutation(int(count))
    test_count = max(1, int(round(count * float(test_fraction))))
    validation_count = max(1, int(round(count * float(validation_fraction))))
    train_count = count - test_count - validation_count
    if train_count < 2:
        raise ValueError("not enough rows for train/validation/test split")
    split = {
        "train": permutation[:train_count],
        "validation": permutation[train_count : train_count + validation_count],
        "test": permutation[train_count + validation_count :],
    }
    audit = {
        "split_mode": "random",
        "row_counts": {name: int(len(indices)) for name, indices in split.items()},
        "physical_cell_grouped": False,
        "physical_cell_overlap_count": None,
        "all_rows_assigned_once": _all_rows_assigned_once(split, count),
        "boundary": "Random row splitting does not test holdout generalization to unseen joint physical-feature cells.",
    }
    audit.update(_split_fingerprints(split))
    return split, audit


def _physical_cell_grouped_split(
    x: np.ndarray,
    *,
    seed: int,
    validation_fraction: float,
    test_fraction: float,
    bins: int,
    lower: np.ndarray | None,
    upper: np.ndarray | None,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    bins = int(bins)
    if bins < 2:
        raise ValueError("--physical-cell-bins must be at least 2")
    lower_values, upper_values, range_source = _physical_cell_ranges(x, lower, upper)
    scaled = (x - lower_values[None, :]) / (upper_values - lower_values)[None, :]
    out_of_range = np.any((scaled < 0.0) | (scaled > 1.0), axis=1)
    if np.any(out_of_range):
        raise ValueError(
            "physical_cell_grouped split found rows outside the declared physical-feature range; "
            "filter the accepted dataset or correct the explicit range before evaluating OOD generalization"
        )
    cell_matrix = np.floor(scaled * bins).astype(int)
    cell_matrix = np.clip(cell_matrix, 0, bins - 1)

    groups: dict[tuple[int, ...], list[int]] = {}
    for row_index, cell_values in enumerate(cell_matrix):
        groups.setdefault(tuple(int(value) for value in cell_values), []).append(row_index)
    if len(groups) < 3:
        raise ValueError("physical_cell_grouped split requires at least three occupied cells")

    train_cells, validation_cells, test_cells, stable_partition = _stable_cell_partition(
        sorted(groups),
        seed=int(seed),
        validation_fraction=float(validation_fraction),
        test_fraction=float(test_fraction),
    )
    if not train_cells or not validation_cells or not test_cells:
        raise ValueError("physical_cell_grouped split could not allocate non-empty train/validation/test cell sets")

    def indices_for(selected_cells: list[tuple[int, ...]]) -> np.ndarray:
        values = [index for cell in selected_cells for index in groups[cell]]
        return np.asarray(sorted(values), dtype=int)

    split = {
        "train": indices_for(train_cells),
        "validation": indices_for(validation_cells),
        "test": indices_for(test_cells),
    }
    if not _all_rows_assigned_once(split, x.shape[0]):
        raise ValueError("physical_cell_grouped split did not assign each row exactly once")

    cell_sets = {
        "train": set(train_cells),
        "validation": set(validation_cells),
        "test": set(test_cells),
    }
    overlap = (
        (cell_sets["train"] & cell_sets["validation"])
        | (cell_sets["train"] & cell_sets["test"])
        | (cell_sets["validation"] & cell_sets["test"])
    )
    encode_cell = lambda cell: ":".join(str(value) for value in cell)
    audit = {
        "split_mode": "physical_cell_grouped",
        "physical_cell_grouped": True,
        "physical_cell_partition_method": "seeded_sha256_threshold_by_cell_id",
        "physical_cell_partition_seed": int(seed),
        "physical_cell_partition_stable_for_existing_cells": stable_partition,
        "physical_cell_bins_per_dimension": bins,
        "physical_cell_range_source": range_source,
        "physical_cell_lower": [float(value) for value in lower_values],
        "physical_cell_upper": [float(value) for value in upper_values],
        "occupied_cell_count": int(len(groups)),
        "cell_counts": {name: int(len(values)) for name, values in cell_sets.items()},
        "row_counts": {name: int(len(indices)) for name, indices in split.items()},
        "physical_cell_overlap_count": int(len(overlap)),
        "all_rows_assigned_once": True,
        "out_of_range_row_count_before_clipping": int(np.sum(out_of_range)),
        "cell_ids": {name: sorted(encode_cell(cell) for cell in values) for name, values in cell_sets.items()},
        "boundary": "Validation and test rows occupy complete joint physical-feature cells absent from training.",
    }
    audit["physical_cell_partition_fingerprint_sha256"] = _cell_partition_fingerprint(cell_sets)
    audit.update(_split_fingerprints(split))
    return split, audit


def _stable_cell_partition(
    cells: list[tuple[int, ...]],
    *,
    seed: int,
    validation_fraction: float,
    test_fraction: float,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], list[tuple[int, ...]], bool]:
    """Assign a physical cell independently of row counts or input order.

    A cumulative learning curve is only interpretable when a cell does not
    silently move between train and test as more rows arrive. Hash thresholds
    make the assignment stable for every already-observed cell. The fallback
    is used only for tiny data sets where thresholding leaves a split empty.
    """

    scored = sorted((_cell_score(cell, seed), cell) for cell in cells)
    test_cells = [cell for score, cell in scored if score < test_fraction]
    validation_cells = [
        cell
        for score, cell in scored
        if test_fraction <= score < test_fraction + validation_fraction
    ]
    train_cells = [cell for score, cell in scored if score >= test_fraction + validation_fraction]
    stable = bool(train_cells and validation_cells and test_cells)
    if stable:
        return train_cells, validation_cells, test_cells, True

    if len(cells) < 3:
        raise ValueError("physical_cell_grouped split requires at least three occupied cells")
    test_cells = [scored[0][1]]
    validation_cells = [scored[1][1]]
    train_cells = [cell for _, cell in scored[2:]]
    return train_cells, validation_cells, test_cells, False


def _cell_score(cell: tuple[int, ...], seed: int) -> float:
    payload = f"{int(seed)}|" + ":".join(str(value) for value in cell)
    integer = int.from_bytes(hashlib.sha256(payload.encode("ascii")).digest()[:8], "big")
    return integer / float(1 << 64)


def _cell_partition_fingerprint(cell_sets: dict[str, set[tuple[int, ...]]]) -> str:
    digest = hashlib.sha256()
    for name in ("train", "validation", "test"):
        for cell in sorted(cell_sets[name]):
            digest.update(name.encode("ascii"))
            digest.update(b"\0")
            digest.update(":".join(str(value) for value in cell).encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def _physical_cell_ranges(
    x: np.ndarray,
    lower: np.ndarray | None,
    upper: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, str]:
    if (lower is None) != (upper is None):
        raise ValueError("physical-cell lower and upper bounds must be supplied together")
    if lower is None:
        lower_values = np.min(x, axis=0)
        upper_values = np.max(x, axis=0)
        source = "observed_full_dataset_min_max"
    else:
        lower_values = np.asarray(lower, dtype=float)
        upper_values = np.asarray(upper, dtype=float)
        source = "explicit"
    if lower_values.shape != (x.shape[1],) or upper_values.shape != (x.shape[1],):
        raise ValueError("physical-cell range dimensionality does not match the model inputs")
    if np.any(~np.isfinite(lower_values)) or np.any(~np.isfinite(upper_values)) or np.any(upper_values <= lower_values):
        raise ValueError("physical-cell ranges must be finite with upper > lower in every dimension")
    return lower_values, upper_values, source


def _float_vector(text: str, expected: int, label: str) -> np.ndarray:
    try:
        values = np.asarray([float(item.strip()) for item in text.split(",") if item.strip()], dtype=float)
    except ValueError as exc:
        raise ValueError(f"{label} must contain only finite numbers") from exc
    if len(values) != expected:
        raise ValueError(f"{label} has {len(values)} values; expected {expected}")
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{label} must contain only finite numbers")
    return values


def _all_rows_assigned_once(split: dict[str, np.ndarray], count: int) -> bool:
    assigned = np.concatenate([np.asarray(split[name], dtype=int) for name in ("train", "validation", "test")])
    return len(assigned) == int(count) and len(np.unique(assigned)) == int(count)


def _split_fingerprints(split: dict[str, np.ndarray]) -> dict[str, Any]:
    per_split: dict[str, str] = {}
    combined = hashlib.sha256()
    for name in ("train", "validation", "test"):
        indices = np.asarray(split[name], dtype=np.int64)
        digest = hashlib.sha256(indices.tobytes()).hexdigest()
        per_split[name] = digest
        combined.update(name.encode("ascii"))
        combined.update(b"\0")
        combined.update(indices.tobytes())
    return {
        "split_index_sha256": per_split,
        "split_fingerprint_sha256": combined.hexdigest(),
    }
