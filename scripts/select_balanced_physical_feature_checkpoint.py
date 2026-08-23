#!/usr/bin/env python3
"""Select an exact, geometry-unique physical-feature checkpoint dataset.

The input must already contain real simulator labels.  Selection is performed
only inside explicit Lp/Ls/Q/|K| ranges.  Rows are grouped into a four-
dimensional grid and allocated with capacity-aware water filling so occupied
cells contribute as evenly as their realized EMX populations permit.

This script never promotes surrogate predictions to labels.  A downstream
``audit_physical_feature_uniformity.py`` run remains the formal marginal,
pairwise, and four-dimensional uniformity gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


FEATURE_COLUMNS = (
    "lp_nh_center",
    "ls_nh_center",
    "q_center",
    "k_abs_center",
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


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    source = Path(args.input_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output_csv = out_dir / "dataset_rows.csv"
    summary_path = out_dir / "balanced_physical_feature_checkpoint_summary.json"

    if not source.is_file():
        raise SystemExit("input CSV does not exist: {}".format(source))

    ranges = _ranges(args)
    first = _count_unique_cells(
        source,
        ranges=ranges,
        four_d_bins=args.four_d_bins,
        check_touchstone_exists=args.check_touchstone_exists,
    )
    capacities = first["cell_counts"]
    valid_unique_count = int(first["valid_unique_count"])
    target_count = int(args.target_count)
    quotas = _balanced_quotas(capacities, target_count, args.seed) if valid_unique_count >= target_count else {}
    selectors = _build_position_selectors(capacities, quotas, args.seed)

    second = _write_selected_rows(
        source,
        output_csv,
        ranges=ranges,
        four_d_bins=args.four_d_bins,
        selectors=selectors,
        check_touchstone_exists=args.check_touchstone_exists,
    ) if quotas else {
        "selected_count": 0,
        "selected_cell_counts": Counter(),
        "selected_geometry_count": 0,
    }

    selected_count = int(second["selected_count"])
    occupied_total = int(args.four_d_bins) ** 4
    occupied_before = len(capacities)
    occupied_after = len(second["selected_cell_counts"])
    checks = {
        "input_header_complete": not first["missing_columns"],
        "valid_unique_count_at_least_target": valid_unique_count >= target_count,
        "quota_count_exact": sum(quotas.values()) == target_count,
        "selected_count_exact": selected_count == target_count,
        "selected_geometry_unique": int(second["selected_geometry_count"]) == selected_count,
        "selected_four_d_occupied_fraction": (
            occupied_after / float(occupied_total) >= float(args.min_four_d_occupied_fraction)
        ),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "USE_REAL_EMX_BALANCED_CHECKPOINT_FOR_FORMAL_UNIFORMITY_AUDIT"
            if status == "PASS"
            else "DO_NOT_USE_CHECKPOINT"
        ),
        "input_csv": _file_record(source, args.compute_input_sha256),
        "output_csv": _file_record(output_csv, True) if output_csv.is_file() else {"path": str(output_csv), "exists": False},
        "target_count": target_count,
        "input_row_count": int(first["input_row_count"]),
        "valid_unique_count": valid_unique_count,
        "selected_count": selected_count,
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "feature_columns": list(FEATURE_COLUMNS),
        "ranges": {key: list(value) for key, value in ranges.items()},
        "four_d_bins": int(args.four_d_bins),
        "four_d_total_cells": occupied_total,
        "occupied_cells_before": occupied_before,
        "occupied_fraction_before": occupied_before / float(occupied_total),
        "occupied_cells_after": occupied_after,
        "occupied_fraction_after": occupied_after / float(occupied_total),
        "min_four_d_occupied_fraction": float(args.min_four_d_occupied_fraction),
        "cell_capacity_summary": _count_summary(capacities.values()),
        "cell_quota_summary": _count_summary(quotas.values()),
        "cell_selected_summary": _count_summary(second["selected_cell_counts"].values()),
        "reject_summary": first["reject_summary"],
        "missing_columns": first["missing_columns"],
        "selection_method": {
            "name": "four_dimensional_capacity_aware_water_fill",
            "seed": int(args.seed),
            "position_sampling": "deterministic random sample within each occupied cell",
            "row_order": "source order after deterministic selection",
        },
        "checks": checks,
        "scientific_boundary": (
            "All selected rows retain real simulator labels. This selector balances realized occupied cells; "
            "it does not prove that unreachable or empty physical bins are populated. Formal marginal, pairwise, "
            "four-dimensional, traceability, and model gates must still run on the output."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("overall_status={}".format(status))
    print("valid_unique_count={}".format(valid_unique_count))
    print("selected_count={}".format(selected_count))
    print("occupied_fraction_after={:.6f}".format(summary["occupied_fraction_after"]))
    print("dataset_rows_csv={}".format(output_csv))
    print("summary={}".format(summary_path))
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--target-count", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--four-d-bins", type=int, default=4)
    parser.add_argument("--min-four-d-occupied-fraction", type=float, default=0.50)
    parser.add_argument("--lp-min-nh", type=float, default=0.5)
    parser.add_argument("--lp-max-nh", type=float, default=3.0)
    parser.add_argument("--ls-min-nh", type=float, default=0.5)
    parser.add_argument("--ls-max-nh", type=float, default=3.0)
    parser.add_argument("--q-min", type=float, default=5.0)
    parser.add_argument("--q-max", type=float, default=25.0)
    parser.add_argument("--k-min", type=float, default=0.0)
    parser.add_argument("--k-max", type=float, default=0.8)
    parser.add_argument("--check-touchstone-exists", action="store_true")
    parser.add_argument("--compute-input-sha256", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.target_count <= 0:
        parser.error("--target-count must be positive")
    if args.four_d_bins <= 0:
        parser.error("--four-d-bins must be positive")
    if not 0.0 <= args.min_four_d_occupied_fraction <= 1.0:
        parser.error("--min-four-d-occupied-fraction must be between 0 and 1")
    for low_name, high_name in (
        ("lp_min_nh", "lp_max_nh"),
        ("ls_min_nh", "ls_max_nh"),
        ("q_min", "q_max"),
        ("k_min", "k_max"),
    ):
        if not getattr(args, low_name) < getattr(args, high_name):
            parser.error("{} must be less than {}".format(low_name, high_name))
    return args


def _ranges(args: argparse.Namespace) -> Dict[str, Tuple[float, float]]:
    return {
        "lp_nh_center": (float(args.lp_min_nh), float(args.lp_max_nh)),
        "ls_nh_center": (float(args.ls_min_nh), float(args.ls_max_nh)),
        "q_center": (float(args.q_min), float(args.q_max)),
        "k_abs_center": (float(args.k_min), float(args.k_max)),
    }


def _count_unique_cells(
    source: Path,
    ranges: Dict[str, Tuple[float, float]],
    four_d_bins: int,
    check_touchstone_exists: bool,
) -> Dict[str, Any]:
    seen_geometry: Set[str] = set()
    cell_counts: Counter = Counter()
    rejects: Counter = Counter()
    input_count = 0
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = [column for column in FEATURE_COLUMNS + GEOMETRY_COLUMNS if column not in fieldnames]
        for row in reader:
            input_count += 1
            accepted = _accepted_row(row, ranges, four_d_bins, check_touchstone_exists)
            if accepted[0] is None:
                rejects[str(accepted[1])] += 1
                continue
            geometry_key, cell = accepted[0]
            if geometry_key in seen_geometry:
                rejects["duplicate_geometry"] += 1
                continue
            seen_geometry.add(geometry_key)
            cell_counts[cell] += 1
    return {
        "input_row_count": input_count,
        "valid_unique_count": sum(cell_counts.values()),
        "cell_counts": cell_counts,
        "reject_summary": dict(sorted(rejects.items())),
        "missing_columns": missing,
    }


def _accepted_row(
    row: Dict[str, str],
    ranges: Dict[str, Tuple[float, float]],
    four_d_bins: int,
    check_touchstone_exists: bool,
) -> Tuple[Optional[Tuple[str, Tuple[int, int, int, int]]], str]:
    if str(row.get("ok", "true")).strip().lower() in {"", "0", "false", "no", "fail", "failed"}:
        return None, "not_ok"
    features: List[float] = []
    for column in FEATURE_COLUMNS:
        value = _finite_float(row.get(column))
        if value is None:
            return None, "missing_feature"
        low, high = ranges[column]
        if value < low or value > high:
            return None, "outside_range"
        features.append(value)
    geometry_values: List[float] = []
    for column in GEOMETRY_COLUMNS:
        value = _finite_float(row.get(column))
        if value is None:
            return None, "missing_geometry"
        geometry_values.append(value)
    if check_touchstone_exists:
        raw_path = str(row.get("touchstone_path") or row.get("raw_touchstone_path") or "").strip()
        if not raw_path or not Path(raw_path).is_file():
            return None, "missing_touchstone"
    geometry_key = _geometry_key(geometry_values)
    cell = tuple(
        _bin_index(value, ranges[column], four_d_bins)
        for column, value in zip(FEATURE_COLUMNS, features)
    )
    return (geometry_key, cell), "accepted"


def _geometry_key(values: Sequence[float]) -> str:
    canonical = "|".join("{:.12g}".format(value) for value in values)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _bin_index(value: float, bounds: Tuple[float, float], bins: int) -> int:
    low, high = bounds
    if value >= high:
        return bins - 1
    index = int((value - low) * bins / (high - low))
    return max(0, min(bins - 1, index))


def _balanced_quotas(capacities: Counter, target: int, seed: int) -> Dict[Tuple[int, int, int, int], int]:
    if target < 0 or target > sum(capacities.values()):
        return {}
    cells = sorted(capacities)
    if target == sum(capacities.values()):
        return {cell: int(capacities[cell]) for cell in cells}
    low, high = 0, max(capacities.values()) if capacities else 0
    while low < high:
        mid = (low + high + 1) // 2
        if sum(min(int(capacities[cell]), mid) for cell in cells) <= target:
            low = mid
        else:
            high = mid - 1
    quotas = {cell: min(int(capacities[cell]), low) for cell in cells}
    remaining = target - sum(quotas.values())
    eligible = [cell for cell in cells if quotas[cell] < int(capacities[cell])]
    random.Random(seed).shuffle(eligible)
    for cell in eligible[:remaining]:
        quotas[cell] += 1
    return quotas


def _build_position_selectors(
    capacities: Counter,
    quotas: Dict[Tuple[int, int, int, int], int],
    seed: int,
) -> Dict[Tuple[int, int, int, int], Tuple[str, Set[int]]]:
    selectors: Dict[Tuple[int, int, int, int], Tuple[str, Set[int]]] = {}
    for ordinal, cell in enumerate(sorted(quotas)):
        capacity = int(capacities[cell])
        quota = int(quotas[cell])
        rng = random.Random(seed + 104729 * (ordinal + 1))
        if quota <= capacity - quota:
            selectors[cell] = ("include", set(rng.sample(range(capacity), quota)))
        else:
            selectors[cell] = ("exclude", set(rng.sample(range(capacity), capacity - quota)))
    return selectors


def _write_selected_rows(
    source: Path,
    output: Path,
    ranges: Dict[str, Tuple[float, float]],
    four_d_bins: int,
    selectors: Dict[Tuple[int, int, int, int], Tuple[str, Set[int]]],
    check_touchstone_exists: bool,
) -> Dict[str, Any]:
    seen_geometry: Set[str] = set()
    selected_geometry: Set[str] = set()
    positions: Counter = Counter()
    selected_cells: Counter = Counter()
    selected_count = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open(newline="", encoding="utf-8-sig") as src, output.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=list(reader.fieldnames or []))
        writer.writeheader()
        for row in reader:
            accepted = _accepted_row(row, ranges, four_d_bins, check_touchstone_exists)
            if accepted[0] is None:
                continue
            geometry_key, cell = accepted[0]
            if geometry_key in seen_geometry:
                continue
            seen_geometry.add(geometry_key)
            position = int(positions[cell])
            positions[cell] += 1
            mode, selected_positions = selectors.get(cell, ("include", set()))
            choose = position in selected_positions if mode == "include" else position not in selected_positions
            if not choose:
                continue
            writer.writerow(row)
            selected_count += 1
            selected_cells[cell] += 1
            selected_geometry.add(geometry_key)
    return {
        "selected_count": selected_count,
        "selected_cell_counts": selected_cells,
        "selected_geometry_count": len(selected_geometry),
    }


def _finite_float(raw: Any) -> Optional[float]:
    try:
        if raw is None or str(raw).strip() == "":
            return None
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _count_summary(values: Iterable[int]) -> Dict[str, Any]:
    numbers = [int(value) for value in values]
    if not numbers:
        return {"count": 0, "min": None, "max": None, "mean": None, "sum": 0}
    return {
        "count": len(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "mean": sum(numbers) / float(len(numbers)),
        "sum": sum(numbers),
    }


def _file_record(path: Path, include_sha256: bool) -> Dict[str, Any]:
    record: Dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return record
    record["size_bytes"] = path.stat().st_size
    if include_sha256:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        record["sha256"] = digest.hexdigest()
    return record


if __name__ == "__main__":
    raise SystemExit(main())
