#!/usr/bin/env python3
"""Materialize a no-clobber, identity-bound 100k-vs-200k data-size experiment.

The source is two historical real-EMX tables from the same generation chain.
Rows are filtered only by the preregistered historical Lp/Ls/Q/|K| domain,
deduplicated by exact ordered float64 geometry and Touchstone content identity,
then split as follows:

* common validation/test: stable-hash selection within physical-cell x source
  strata, with exact global counts and no identity overlap;
* large arm: every remaining training row;
* five small arms: exact, independently hashed, stratified subsets of the fixed
  large-arm training pool.

The script never inspects model predictions or response errors. It does not
claim whole-cell OOD generalization; the common holdout is same-source,
stratified generalization evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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
PHYSICAL_LOWER = (0.5, 0.5, 5.0, 0.0)
PHYSICAL_UPPER = (3.0, 3.0, 25.0, 0.8)
GEOMETRY_LOWER = (160.0, 160.0, 160.0, 160.0, 3.0, 20.0, 20.0, -90.0, 100.0, 100.0)
GEOMETRY_UPPER = (520.0, 520.0, 520.0, 520.0, 12.0, 90.0, 90.0, 90.0, 320.0, 320.0)
OUTPUT_COLUMNS = (
    "controlled_source_batch",
    "controlled_source_row_number",
    "controlled_physical_cell_4d",
    "controlled_split_assignment",
    "canonical_geometry_identity_sha256",
    "evaluation",
    "touchstone_path",
    "touchstone_sha256",
    *INPUT_COLUMNS,
    *GEOMETRY_COLUMNS,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    try:
        return _run(args, out_dir)
    except Exception as exc:
        failure = {
            "schema": "controlled_data_scaling_materialization_failure_v1",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "FAIL",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        (out_dir / "materialization_FAIL.json").write_text(
            json.dumps(failure, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        raise


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-pool-csv", required=True)
    parser.add_argument("--base-pool-sha256", required=True)
    parser.add_argument("--increment-training-csv", required=True)
    parser.add_argument("--increment-training-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-base-rows", type=int, default=100_000)
    parser.add_argument("--expected-increment-rows", type=int, default=120_000)
    parser.add_argument("--expected-accepted-rows", type=int, default=218_192)
    parser.add_argument("--expected-increment-range-rejects", type=int, default=1_808)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--min-holdout-occupied-cells", type=int, default=100)
    parser.add_argument("--holdout-each", type=int, default=9_096)
    parser.add_argument("--large-train-count", type=int, default=200_000)
    parser.add_argument("--small-train-count", type=int, default=100_000)
    parser.add_argument("--split-seed", type=int, default=2026082201)
    parser.add_argument(
        "--subset-seeds",
        default="2026082211,2026082212,2026082213,2026082214,2026082215",
    )
    args = parser.parse_args(argv)
    for name in ("base_pool_sha256", "increment_training_sha256"):
        value = str(getattr(args, name)).strip().lower()
        if not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256")
        setattr(args, name, value)
    if args.physical_cell_bins < 2:
        parser.error("--physical-cell-bins must be at least 2")
    if not 1 <= args.min_holdout_occupied_cells <= args.physical_cell_bins**4:
        parser.error("--min-holdout-occupied-cells is outside the possible 4-D cell count")
    if min(args.holdout_each, args.large_train_count, args.small_train_count) < 1:
        parser.error("holdout and train counts must be positive")
    try:
        args.subset_seeds = [
            int(value.strip()) for value in str(args.subset_seeds).split(",") if value.strip()
        ]
    except ValueError as exc:
        parser.error(f"--subset-seeds must contain integers: {exc}")
    if len(args.subset_seeds) != 5 or len(set(args.subset_seeds)) != 5:
        parser.error("--subset-seeds must contain exactly five unique integers")
    return args


def _run(args: argparse.Namespace, out_dir: Path) -> int:
    base_path = Path(args.base_pool_csv).expanduser().resolve()
    increment_path = Path(args.increment_training_csv).expanduser().resolve()
    _require_source(base_path, args.base_pool_sha256)
    _require_source(increment_path, args.increment_training_sha256)

    rows: list[dict[str, str]] = []
    source_audits: list[dict[str, Any]] = []
    base_rows, base_audit = _load_source(
        base_path,
        source_batch="historical_base_100k",
        source_kind="base_pool",
        expected_rows=int(args.expected_base_rows),
        bins=int(args.physical_cell_bins),
    )
    increment_rows, increment_audit = _load_source(
        increment_path,
        source_batch="historical_increment_120k",
        source_kind="increment_training",
        expected_rows=int(args.expected_increment_rows),
        bins=int(args.physical_cell_bins),
    )
    rows.extend(base_rows)
    rows.extend(increment_rows)
    source_audits.extend([base_audit, increment_audit])

    if int(increment_audit["range_reject_count"]) != int(args.expected_increment_range_rejects):
        raise ValueError(
            "increment historical-range reject count mismatch: "
            f"expected={args.expected_increment_range_rejects} actual={increment_audit['range_reject_count']}"
        )
    if len(rows) != int(args.expected_accepted_rows):
        raise ValueError(
            f"accepted row count mismatch: expected={args.expected_accepted_rows} actual={len(rows)}"
        )
    expected_total = int(args.large_train_count) + 2 * int(args.holdout_each)
    if len(rows) != expected_total:
        raise ValueError(
            f"accepted rows must equal large_train+2*holdout: rows={len(rows)} expected={expected_total}"
        )
    _require_unique_identities(rows)

    by_stratum: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_stratum[(row["controlled_physical_cell_4d"], row["controlled_source_batch"])].append(row)
    stratum_sizes = {stratum: len(values) for stratum, values in by_stratum.items()}
    validation_counts = _allocate_stratified_counts(
        stratum_sizes,
        target=int(args.holdout_each),
        reserve_by_stratum={key: 2 if count >= 3 else 1 for key, count in stratum_sizes.items()},
        minimum_by_stratum={key: 1 if count >= 3 else 0 for key, count in stratum_sizes.items()},
    )
    remaining_sizes = {
        key: count - validation_counts[key] for key, count in stratum_sizes.items()
    }
    test_counts = _allocate_stratified_counts(
        remaining_sizes,
        target=int(args.holdout_each),
        reserve_by_stratum={key: 1 for key in remaining_sizes},
        minimum_by_stratum={key: 1 if count >= 2 else 0 for key, count in remaining_sizes.items()},
    )

    validation_ids: set[str] = set()
    test_ids: set[str] = set()
    for stratum, stratum_rows in sorted(by_stratum.items()):
        test_ranked = sorted(
            stratum_rows,
            key=lambda row: _stable_score(
                int(args.split_seed), "test", row["canonical_geometry_identity_sha256"]
            ),
        )
        selected_test = test_ranked[: test_counts[stratum]]
        selected_test_ids = {row["canonical_geometry_identity_sha256"] for row in selected_test}
        validation_ranked = sorted(
            (
                row
                for row in stratum_rows
                if row["canonical_geometry_identity_sha256"] not in selected_test_ids
            ),
            key=lambda row: _stable_score(
                int(args.split_seed), "validation", row["canonical_geometry_identity_sha256"]
            ),
        )
        selected_validation = validation_ranked[: validation_counts[stratum]]
        test_ids.update(selected_test_ids)
        validation_ids.update(
            row["canonical_geometry_identity_sha256"] for row in selected_validation
        )
    if len(validation_ids) != int(args.holdout_each) or len(test_ids) != int(args.holdout_each):
        raise RuntimeError("stable stratified holdout did not meet the exact global counts")
    if validation_ids & test_ids:
        raise RuntimeError("validation/test identities overlap")

    train_rows: list[dict[str, str]] = []
    validation_rows: list[dict[str, str]] = []
    test_rows: list[dict[str, str]] = []
    for row in rows:
        identity = row["canonical_geometry_identity_sha256"]
        if identity in validation_ids:
            row["controlled_split_assignment"] = "validation"
            validation_rows.append(row)
        elif identity in test_ids:
            row["controlled_split_assignment"] = "test"
            test_rows.append(row)
        else:
            row["controlled_split_assignment"] = "train"
            train_rows.append(row)
    if len(train_rows) != int(args.large_train_count):
        raise RuntimeError(
            f"large training pool is not exact: expected={args.large_train_count} actual={len(train_rows)}"
        )

    coverage = _coverage_audit(train_rows, validation_rows, test_rows)
    _require_coverage_quality(
        coverage, min_holdout_occupied_cells=int(args.min_holdout_occupied_cells)
    )
    common_holdout_path = out_dir / "fixed_common_holdout_manifest.json"
    holdout_fingerprint = _common_holdout_fingerprint(validation_ids, test_ids)
    holdout_manifest = {
        "schema": "fixed_common_holdout_geometry_identity_v1",
        "identity_kind": "canonical_geometry_sha256",
        "selection_method": "stable_hash_within_physical_cell_4d_x_source_batch",
        "selection_seed": int(args.split_seed),
        "selection_uses_model_results": False,
        "stratification": ["physical_cell_4d", "source_batch"],
        "physical_cell_bins": int(args.physical_cell_bins),
        "physical_lower": list(PHYSICAL_LOWER),
        "physical_upper": list(PHYSICAL_UPPER),
        "validation_count": len(validation_ids),
        "test_count": len(test_ids),
        "validation_geometry_identities": sorted(validation_ids),
        "test_geometry_identities": sorted(test_ids),
        "common_holdout_fingerprint_sha256": holdout_fingerprint,
        "coverage_audit": coverage,
        "interpretation_boundary": (
            "This is a same-source, identity-disjoint stratified holdout. It estimates interpolation/generalization "
            "within represented physical cells and source batches; it is not a whole-cell OOD test."
        ),
    }
    _write_json(common_holdout_path, holdout_manifest)

    large_path = out_dir / "arm_large_n200000_with_common_holdout.csv"
    _write_csv(large_path, rows)
    arm_records: list[dict[str, Any]] = [
        _arm_record(
            "large",
            None,
            large_path,
            train_rows,
            validation_rows,
            test_rows,
        )
    ]

    train_by_stratum: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in train_rows:
        train_by_stratum[(row["controlled_physical_cell_4d"], row["controlled_source_batch"])].append(row)
    small_counts = _allocate_stratified_counts(
        {key: len(values) for key, values in train_by_stratum.items()},
        target=int(args.small_train_count),
        reserve_by_stratum={key: 0 for key in train_by_stratum},
        minimum_by_stratum={key: 1 for key in train_by_stratum},
    )
    holdout_ids = validation_ids | test_ids
    for replicate, subset_seed in enumerate(args.subset_seeds, start=1):
        selected_train_ids: set[str] = set()
        for stratum, stratum_rows in sorted(train_by_stratum.items()):
            ranked = sorted(
                stratum_rows,
                key=lambda row: _stable_score(
                    int(subset_seed), "small_train", row["canonical_geometry_identity_sha256"]
                ),
            )
            selected_train_ids.update(
                row["canonical_geometry_identity_sha256"]
                for row in ranked[: small_counts[stratum]]
            )
        if len(selected_train_ids) != int(args.small_train_count):
            raise RuntimeError(f"small replicate {replicate} did not contain the exact training count")
        if selected_train_ids & holdout_ids:
            raise RuntimeError(f"small replicate {replicate} leaked common holdout identities")
        selected_loaded_ids = selected_train_ids | holdout_ids
        selected_rows = [
            row
            for row in rows
            if row["canonical_geometry_identity_sha256"] in selected_loaded_ids
        ]
        small_path = out_dir / f"arm_small_n100000_rep{replicate}_subsetseed{subset_seed}_with_common_holdout.csv"
        _write_csv(small_path, selected_rows)
        selected_training_rows = [
            row for row in selected_rows if row["controlled_split_assignment"] == "train"
        ]
        arm_records.append(
            _arm_record(
                "small",
                int(subset_seed),
                small_path,
                selected_training_rows,
                validation_rows,
                test_rows,
                replicate=replicate,
            )
        )

    holdout_sha = _sha256_file(common_holdout_path)
    summary = {
        "schema": "controlled_data_scaling_materialization_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": "USE_FOR_PREREGISTERED_100K_VS_200K_CONTROLLED_EXPERIMENT",
        "source_contract": {
            "same_historical_generation_chain": True,
            "current_contract_rows_spliced": False,
            "sources": source_audits,
            "accepted_row_count": len(rows),
            "unique_geometry_identity_count": len(
                {row["canonical_geometry_identity_sha256"] for row in rows}
            ),
            "unique_touchstone_sha256_count": len(
                {row["touchstone_sha256"] for row in rows}
            ),
        },
        "split_contract": {
            "large_train_count": len(train_rows),
            "validation_count": len(validation_rows),
            "test_count": len(test_rows),
            "common_holdout_manifest": str(common_holdout_path),
            "common_holdout_manifest_sha256": holdout_sha,
            "common_holdout_fingerprint_sha256": holdout_fingerprint,
            "coverage_audit": coverage,
            "minimum_holdout_occupied_cells_required": int(
                args.min_holdout_occupied_cells
            ),
            "whole_cell_ood_claim": False,
        },
        "paired_replicate_contract": {
            "replicate_count": len(args.subset_seeds),
            "subset_seeds": list(args.subset_seeds),
            "small_train_count_each": int(args.small_train_count),
            "large_train_pool_fixed_across_replicates": True,
            "stratification": ["physical_cell_4d", "source_batch"],
            "estimand": (
                "Average paired change from 100,000 to 200,000 inverse-training labels under five preregistered "
                "data-subset and initialization pairs, conditional on the historical source pool, common "
                "holdout, architecture, shared F_ref, optimizer-update budget, and fixed preprocessing."
            ),
        },
        "arms": arm_records,
        "limitations": [
            "The common real-EMX holdout is stratified same-source evidence, not whole-cell or deployment-distribution OOD evidence.",
            "The five small subsets quantify both subset-selection and initialization variability only when paired with distinct preregistered init seeds.",
            "The deterministic fixed10k centered-LHS frame is a finite coverage frame and must not be treated as an i.i.d. population sample.",
        ],
    }
    summary_path = out_dir / "controlled_data_scaling_materialization_summary.json"
    _write_json(summary_path, summary)
    sha_index_path = out_dir / "SHA256SUMS.txt"
    artifact_paths = [common_holdout_path, large_path]
    artifact_paths.extend(Path(record["csv_path"]) for record in arm_records if record["arm"] == "small")
    artifact_paths.append(summary_path)
    sha_index_path.write_text(
        "".join(f"{_sha256_file(path)}  {path.name}\n" for path in artifact_paths),
        encoding="utf-8",
    )
    print("overall_status=PASS")
    print(f"accepted_rows={len(rows)}")
    print(f"train_rows={len(train_rows)}")
    print(f"validation_rows={len(validation_rows)}")
    print(f"test_rows={len(test_rows)}")
    print(f"common_holdout_manifest={common_holdout_path}")
    print(f"common_holdout_manifest_sha256={holdout_sha}")
    print(f"summary={summary_path}")
    return 0


def _require_source(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"source table is missing: {path}")
    actual = _sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"source SHA-256 mismatch: path={path} expected={expected_sha256} actual={actual}")


def _load_source(
    path: Path,
    *,
    source_batch: str,
    source_kind: str,
    expected_rows: int,
    bins: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    output: list[dict[str, str]] = []
    range_reject_count = 0
    invalid_count = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        source_row_count = 0
        for source_row_count, row in enumerate(reader, start=1):
            normalized = _normalize_source_row(
                row,
                source_batch=source_batch,
                source_kind=source_kind,
                source_row_number=source_row_count,
                bins=bins,
            )
            if normalized is None:
                invalid_count += 1
                continue
            physical = [float(normalized[column]) for column in INPUT_COLUMNS]
            if not _inside(physical, PHYSICAL_LOWER, PHYSICAL_UPPER):
                range_reject_count += 1
                continue
            geometry = [float(normalized[column]) for column in GEOMETRY_COLUMNS]
            if not _inside(geometry, GEOMETRY_LOWER, GEOMETRY_UPPER):
                raise ValueError(
                    f"source geometry lies outside the declared generator bounds: {path}:{source_row_count}"
                )
            output.append(normalized)
    if source_row_count != expected_rows:
        raise ValueError(
            f"source row count mismatch: path={path} expected={expected_rows} actual={source_row_count}"
        )
    if invalid_count:
        raise ValueError(f"source contains {invalid_count} rows with invalid required fields: {path}")
    return output, {
        "kind": source_kind,
        "source_batch": source_batch,
        "path": str(path),
        "sha256": _sha256_file(path),
        "source_row_count": source_row_count,
        "accepted_historical_range_count": len(output),
        "range_reject_count": range_reject_count,
    }


def _normalize_source_row(
    row: dict[str, str],
    *,
    source_batch: str,
    source_kind: str,
    source_row_number: int,
    bins: int,
) -> dict[str, str] | None:
    if source_kind == "base_pool":
        feature_raw = (
            row.get("lp_nh_center"),
            row.get("ls_nh_center"),
            row.get("q_center"),
            row.get("k_abs_center") or row.get("k_center"),
        )
    elif source_kind == "increment_training":
        feature_raw = (
            row.get("input__lp_nh_center"),
            row.get("input__ls_nh_center"),
            row.get("input__q_center"),
            row.get("input__k_abs_center") or row.get("input__k_center"),
        )
    else:
        raise ValueError(f"unsupported source kind: {source_kind}")
    physical = [_as_float(value) for value in feature_raw]
    geometry = [_as_float(row.get(column)) for column in GEOMETRY_COLUMNS]
    touchstone_sha = str(row.get("touchstone_sha256") or "").strip().lower()
    touchstone_path = str(row.get("touchstone_path") or "").strip()
    if any(value is None for value in physical + geometry) or not _is_sha256(touchstone_sha) or not touchstone_path:
        return None
    physical_values = [float(value) for value in physical if value is not None]
    physical_values[3] = abs(physical_values[3])
    geometry_values = [float(value) for value in geometry if value is not None]
    identity = _geometry_identity_sha256(geometry_values)
    cell = _physical_cell(physical_values, bins)
    output = {
        "controlled_source_batch": source_batch,
        "controlled_source_row_number": str(source_row_number),
        "controlled_physical_cell_4d": ":".join(str(value) for value in cell),
        "controlled_split_assignment": "UNASSIGNED",
        "canonical_geometry_identity_sha256": identity,
        "evaluation": str(row.get("evaluation") or ""),
        "touchstone_path": touchstone_path,
        "touchstone_sha256": touchstone_sha,
    }
    for column, value in zip(INPUT_COLUMNS, physical_values):
        output[column] = format(value, ".17g")
    for column, value in zip(GEOMETRY_COLUMNS, geometry_values):
        output[column] = format(value, ".17g")
    return output


def _allocate_stratified_counts(
    sizes: dict[tuple[str, str], int],
    *,
    target: int,
    reserve_by_stratum: dict[tuple[str, str], int],
    minimum_by_stratum: dict[tuple[str, str], int],
) -> dict[tuple[str, str], int]:
    total = sum(sizes.values())
    if target < 0 or target >= total:
        raise ValueError("stratified target must be nonnegative and smaller than the source")
    allocation: dict[tuple[str, str], int] = {}
    ideal: dict[tuple[str, str], float] = {}
    capacity: dict[tuple[str, str], int] = {}
    for key, size in sizes.items():
        lower = int(minimum_by_stratum.get(key, 0))
        upper = int(size) - int(reserve_by_stratum.get(key, 0))
        if not 0 <= lower <= upper:
            raise ValueError(f"invalid stratified allocation bounds for {key}: lower={lower} upper={upper}")
        raw = float(size) * float(target) / float(total)
        ideal[key] = raw
        capacity[key] = upper
        allocation[key] = min(upper, max(lower, int(math.floor(raw))))
    current = sum(allocation.values())
    while current < target:
        candidates = [key for key in sizes if allocation[key] < capacity[key]]
        if not candidates:
            raise RuntimeError("stratified allocation lacks capacity for the exact target")
        candidates.sort(
            key=lambda key: (
                -(ideal[key] - allocation[key]),
                _stable_score(0, "allocation_add", "|".join(key)),
            )
        )
        for key in candidates:
            if current >= target:
                break
            allocation[key] += 1
            current += 1
    while current > target:
        candidates = [
            key for key in sizes if allocation[key] > int(minimum_by_stratum.get(key, 0))
        ]
        if not candidates:
            raise RuntimeError("stratified allocation cannot reduce to the exact target")
        candidates.sort(
            key=lambda key: (
                ideal[key] - allocation[key],
                _stable_score(0, "allocation_remove", "|".join(key)),
            )
        )
        for key in candidates:
            if current <= target:
                break
            allocation[key] -= 1
            current -= 1
    if sum(allocation.values()) != target:
        raise RuntimeError("stratified allocation did not meet its exact target")
    return allocation


def _coverage_audit(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    test_rows: list[dict[str, str]],
) -> dict[str, Any]:
    partitions = {"train": train_rows, "validation": validation_rows, "test": test_rows}
    all_cells = sorted(
        {row["controlled_physical_cell_4d"] for values in partitions.values() for row in values}
    )
    all_sources = sorted(
        {row["controlled_source_batch"] for values in partitions.values() for row in values}
    )
    per_partition: dict[str, Any] = {}
    for name, values in partitions.items():
        cell_counts = Counter(row["controlled_physical_cell_4d"] for row in values)
        source_counts = Counter(row["controlled_source_batch"] for row in values)
        stratum_counts = Counter(
            (row["controlled_physical_cell_4d"], row["controlled_source_batch"])
            for row in values
        )
        per_partition[name] = {
            "row_count": len(values),
            "occupied_cell_count": len(cell_counts),
            "all_pool_cells_covered": len(cell_counts) == len(all_cells),
            "source_counts": dict(sorted(source_counts.items())),
            "all_sources_covered": set(source_counts) == set(all_sources),
            "occupied_stratum_count": len(stratum_counts),
            "minimum_nonzero_cell_rows": min(cell_counts.values()) if cell_counts else 0,
            "minimum_nonzero_stratum_rows": min(stratum_counts.values()) if stratum_counts else 0,
        }
    return {
        "pool_occupied_cell_count": len(all_cells),
        "pool_source_batches": all_sources,
        "per_partition": per_partition,
        "geometry_identity_overlap_count": 0,
        "touchstone_identity_overlap_count": 0,
    }


def _require_coverage_quality(
    coverage: dict[str, Any],
    *,
    min_holdout_occupied_cells: int,
) -> None:
    partitions = coverage["per_partition"]
    checks = [
        partitions[name]["all_sources_covered"] for name in ("train", "validation", "test")
    ]
    checks.extend(
        partitions[name]["occupied_cell_count"] >= int(min_holdout_occupied_cells)
        for name in ("validation", "test")
    )
    checks.append(partitions["train"]["all_pool_cells_covered"])
    if not all(checks):
        raise ValueError(f"preregistered split coverage quality rules failed: {coverage}")


def _arm_record(
    arm: str,
    subset_seed: int | None,
    path: Path,
    training_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    test_rows: list[dict[str, str]],
    *,
    replicate: int | None = None,
) -> dict[str, Any]:
    training_ids = sorted(row["canonical_geometry_identity_sha256"] for row in training_rows)
    return {
        "arm": arm,
        "replicate": replicate,
        "subset_seed": subset_seed,
        "csv_path": str(path),
        "csv_sha256": _sha256_file(path),
        "loaded_row_count": len(training_rows) + len(validation_rows) + len(test_rows),
        "training_row_count": len(training_rows),
        "validation_row_count": len(validation_rows),
        "test_row_count": len(test_rows),
        "training_geometry_identity_set_sha256": hashlib.sha256(
            "".join(f"{identity}\n" for identity in training_ids).encode("ascii")
        ).hexdigest(),
        "source_batch_counts_training": dict(
            sorted(Counter(row["controlled_source_batch"] for row in training_rows).items())
        ),
        "physical_cell_count_training": len(
            {row["controlled_physical_cell_4d"] for row in training_rows}
        ),
    }


def _require_unique_identities(rows: list[dict[str, str]]) -> None:
    geometry = [row["canonical_geometry_identity_sha256"] for row in rows]
    touchstone = [row["touchstone_sha256"] for row in rows]
    duplicate_geometry = len(geometry) - len(set(geometry))
    duplicate_touchstone = len(touchstone) - len(set(touchstone))
    if duplicate_geometry or duplicate_touchstone:
        raise ValueError(
            "source pool identity uniqueness failed: "
            f"duplicate_geometry={duplicate_geometry} duplicate_touchstone={duplicate_touchstone}"
        )


def _geometry_identity_sha256(values: list[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_float64_v1",
        "columns": list(GEOMETRY_COLUMNS),
        "values": [format(float(value), ".17g") for value in values],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _physical_cell(values: list[float], bins: int) -> tuple[int, ...]:
    cell: list[int] = []
    for value, lower, upper in zip(values, PHYSICAL_LOWER, PHYSICAL_UPPER):
        scaled = (float(value) - lower) / (upper - lower)
        index = int(math.floor(scaled * int(bins)))
        cell.append(max(0, min(int(bins) - 1, index)))
    return tuple(cell)


def _inside(values: Iterable[float], lower: Iterable[float], upper: Iterable[float]) -> bool:
    return all(
        math.isfinite(float(value)) and float(lo) <= float(value) <= float(hi)
        for value, lo, hi in zip(values, lower, upper)
    )


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stable_score(seed: int, purpose: str, identity: str) -> str:
    return hashlib.sha256(f"{int(seed)}|{purpose}|{identity}".encode("ascii")).hexdigest()


def _common_holdout_fingerprint(validation: set[str], test: set[str]) -> str:
    payload = "".join(
        [f"validation\0{identity}\n" for identity in sorted(validation)]
        + [f"test\0{identity}\n" for identity in sorted(test)]
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
