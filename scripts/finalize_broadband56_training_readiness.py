#!/usr/bin/env python3
"""Create training-readiness products from a terminal broadband56 checkpoint.

This finalizer is intentionally downstream of the real-EMX checkpoint audit.
It refuses to run unless the checkpoint is ``COMPLETE_200K`` and all bound
input hashes still match.  It does not train a model and never treats proxy
predictions as physical evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_BINS_PER_DIMENSION,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    TARGET_ACCEPTED_GEOMETRIES,
    canonical_geometry_sha256,
    contract_fingerprint,
    primary_cell_for_values,
    validate_contract,
)


WEIGHT_CLIP_LOW = 0.25
WEIGHT_CLIP_HIGH = 4.0
BALANCED_SUBSET_SEED = 20260828
FUTURE_SPLIT_SALT = "broadband56_future_geometry_split_v1_20260828"
FUTURE_SPLIT_RATIOS = (("train", 0.80), ("validation", 0.10), ("test", 0.10))
REQUIRED_CHECKPOINT_FILES = (
    "CHECKPOINT_STATUS.json",
    "CHECKPOINT_RECEIPT.json",
    "COVERAGE_SUMMARY.json",
    "physical_coverage_cells_by_anchor.csv",
    "SHA256SUMS.txt",
)


class FinalizationError(RuntimeError):
    """Raised when terminal evidence or derived-product accounting fails."""


@dataclass(frozen=True)
class AcceptedIdentity:
    geometry_id: str
    geometry_sha256: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()
    accepted_path = Path(args.accepted_geometries).expanduser().resolve()
    features_path = Path(args.long_features).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if out_dir.exists():
        raise SystemExit(f"output path already exists (no-clobber): {out_dir}")

    try:
        result = finalize_training_readiness(
            contract_path=contract_path,
            checkpoint_dir=checkpoint_dir,
            accepted_path=accepted_path,
            features_path=features_path,
            out_dir=out_dir,
        )
    except FinalizationError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2

    print("overall_status=PASS")
    print(f"training_weight_rows={result['training_weight_rows']}")
    print(f"maximal_balanced_subset_rows={result['balanced_subset_rows']}")
    print(f"future_split_rows={result['future_split_rows']}")
    print(f"receipt={out_dir / 'TRAINING_READINESS_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--accepted-geometries", required=True)
    parser.add_argument("--long-features", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def finalize_training_readiness(
    *,
    contract_path: Path,
    checkpoint_dir: Path,
    accepted_path: Path,
    features_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Validate terminal evidence and atomically write all derived products."""

    contract, fingerprint, receipt = _validate_terminal_checkpoint(
        contract_path=contract_path,
        checkpoint_dir=checkpoint_dir,
        accepted_path=accepted_path,
        features_path=features_path,
    )
    identities, accepted_fieldnames = _read_accepted_identities(
        accepted_path,
        fingerprint=fingerprint,
        expected_count=TARGET_ACCEPTED_GEOMETRIES,
    )
    memberships, occupancies, feature_rows = _collect_actual_anchor_memberships(
        features_path,
        identities=identities,
        fingerprint=fingerprint,
    )
    expected_feature_rows = TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ)
    if feature_rows != expected_feature_rows:
        raise FinalizationError(
            f"long-feature row count mismatch: actual={feature_rows}, expected={expected_feature_rows}"
        )

    audited_counts = _read_audited_cell_counts(
        checkpoint_dir / "physical_coverage_cells_by_anchor.csv"
    )
    complete_counts = Counter({index: int(occupancies.get(index, 0)) for index in range(
        PRIMARY_FREQUENCY_CONDITIONED_CELLS
    )})
    if dict(audited_counts) != dict(complete_counts):
        mismatches = [
            (index, audited_counts.get(index), complete_counts.get(index))
            for index in range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)
            if audited_counts.get(index) != complete_counts.get(index)
        ]
        raise FinalizationError(
            f"recomputed actual anchor-cell counts disagree with checkpoint audit: {mismatches[:5]}"
        )

    weight_rows, weight_summary = _coverage_weight_rows(
        identities=identities,
        memberships=memberships,
        occupancies=occupancies,
    )
    subset_ids, subset_metadata, subset_summary = _maximal_balanced_subset(
        identities=identities,
        memberships=memberships,
        occupancies=occupancies,
        seed=BALANCED_SUBSET_SEED,
    )
    split_assignments, split_summary = _future_split_assignments(identities)

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise FinalizationError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        weights_path = staging / "full_200k_training_weights.csv"
        _write_dict_rows(weights_path, weight_rows)

        subset_path = staging / "maximal_balanced_subset.csv"
        subset_count = _write_balanced_subset(
            source=accepted_path,
            output=subset_path,
            source_fieldnames=accepted_fieldnames,
            selected_ids=subset_ids,
            metadata=subset_metadata,
        )
        if subset_count != len(subset_ids):
            raise FinalizationError(
                f"balanced-subset write count mismatch: actual={subset_count}, expected={len(subset_ids)}"
            )

        assignments_path = staging / "future_split_assignments.csv"
        _write_split_assignments(assignments_path, identities, split_assignments)
        split_manifest_path = staging / "future_split_manifest.json"
        split_manifest = {
            "schema": "broadband56_future_geometry_split_manifest_v1",
            "generated_utc": _utc_now(),
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "split_unit": "canonical_geometry_identity",
            "all_56_frequency_rows_from_one_geometry_remain_in_one_split": True,
            "algorithm": {
                "name": "salted_sha256_order_with_exact_largest_remainder_counts",
                "salt": FUTURE_SPLIT_SALT,
                "ratios": {name: ratio for name, ratio in FUTURE_SPLIT_RATIOS},
                "source_identity": "geometry_sha256",
            },
            **split_summary,
            "assignment_artifact": _file_evidence(
                assignments_path,
                recorded_path=out_dir / assignments_path.name,
            ),
            "scientific_boundary": (
                "This manifest freezes a future geometry-identity split only. It does not train or evaluate a model."
            ),
        }
        _write_json(split_manifest_path, split_manifest)

        receipt_path = staging / "TRAINING_READINESS_RECEIPT.json"
        checks = {
            "terminal_checkpoint_complete_200k": True,
            "training_weight_count_exact": len(weight_rows) == TARGET_ACCEPTED_GEOMETRIES,
            "training_weights_finite_and_clipped": weight_summary["all_finite_and_within_clip"],
            "balanced_subset_geometry_unique": subset_count == len(subset_ids),
            "balanced_subset_not_labeled_as_full_dataset": True,
            "future_split_count_exact": len(split_assignments) == TARGET_ACCEPTED_GEOMETRIES,
            "future_split_geometry_unique": len(set(split_assignments)) == TARGET_ACCEPTED_GEOMETRIES,
            "future_split_all_rows_grouped_by_geometry": True,
            "no_final_model_training_performed": True,
        }
        if not all(checks.values()):
            raise FinalizationError(f"derived-product checks failed: {checks}")
        readiness_receipt = {
            "schema": "broadband56_training_readiness_receipt_v1",
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "terminal_checkpoint": {
                "directory": str(checkpoint_dir),
                "receipt_sha256": _sha256(checkpoint_dir / "CHECKPOINT_RECEIPT.json"),
                "status_sha256": _sha256(checkpoint_dir / "CHECKPOINT_STATUS.json"),
                "coverage_summary_sha256": _sha256(checkpoint_dir / "COVERAGE_SUMMARY.json"),
                "checkpoint_receipt_decision": receipt.get("decision"),
            },
            "inputs": {
                "contract": _file_evidence(contract_path),
                "accepted_geometries": _file_evidence(accepted_path),
                "long_features": _file_evidence(features_path),
                "audited_anchor_cells": _file_evidence(
                    checkpoint_dir / "physical_coverage_cells_by_anchor.csv"
                ),
            },
            "counts": {
                "accepted_geometries": len(identities),
                "geometry_frequency_rows": feature_rows,
                "actual_anchor_cell_contributions": sum(occupancies.values()),
                "observed_frequency_conditioned_cells": sum(
                    1 for value in occupancies.values() if value > 0
                ),
            },
            "training_weights": weight_summary,
            "maximal_balanced_subset": subset_summary,
            "future_split": split_summary,
            "checks": checks,
            "outputs": {
                "full_200k_training_weights": _file_evidence(
                    weights_path,
                    recorded_path=out_dir / weights_path.name,
                ),
                "maximal_balanced_subset": _file_evidence(
                    subset_path,
                    recorded_path=out_dir / subset_path.name,
                ),
                "future_split_manifest": _file_evidence(
                    split_manifest_path,
                    recorded_path=out_dir / split_manifest_path.name,
                ),
                "future_split_assignments": _file_evidence(
                    assignments_path,
                    recorded_path=out_dir / assignments_path.name,
                ),
            },
            "scientific_boundary": (
                "Weights and subset membership are derived only from actual fresh-real-EMX anchor cells. "
                "They are training-preparation metadata, not new labels, proxy evidence, or model results. "
                "The balanced subset is the largest exact equal-quota subset under the documented "
                "rarest-actual-cell ownership partition; it does not claim exact simultaneous uniformity "
                "of every one of the eight anchor marginals."
            ),
            "contract_snapshot": {
                "target_accepted_geometries": contract.get("terminal_goal", {}).get(
                    "accepted_geometries"
                ),
                "frequency_points": len(FREQUENCY_GRID_HZ),
            },
        }
        _write_json(receipt_path, readiness_receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "training_weight_rows": len(weight_rows),
        "balanced_subset_rows": len(subset_ids),
        "future_split_rows": len(split_assignments),
    }


def _validate_terminal_checkpoint(
    *,
    contract_path: Path,
    checkpoint_dir: Path,
    accepted_path: Path,
    features_path: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    for path in (contract_path, accepted_path, features_path):
        if not path.is_file():
            raise FinalizationError(f"required input does not exist: {path}")
    if not checkpoint_dir.is_dir():
        raise FinalizationError(f"checkpoint directory does not exist: {checkpoint_dir}")
    for name in REQUIRED_CHECKPOINT_FILES:
        if not (checkpoint_dir / name).is_file():
            raise FinalizationError(f"checkpoint is missing required file: {name}")
    _verify_sha256s(checkpoint_dir)

    contract = _read_json(contract_path)
    errors = validate_contract(contract)
    if errors:
        raise FinalizationError(f"contract validation failed: {errors[:5]}")
    fingerprint = str(
        contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract)
    )
    if fingerprint != contract_fingerprint(contract):
        raise FinalizationError("contract self-declared fingerprint does not match its content")

    status = _read_json(checkpoint_dir / "CHECKPOINT_STATUS.json")
    receipt = _read_json(checkpoint_dir / "CHECKPOINT_RECEIPT.json")
    coverage = _read_json(checkpoint_dir / "COVERAGE_SUMMARY.json")
    if status.get("checkpoint_status") != "COMPLETE_200K":
        raise FinalizationError(
            f"checkpoint status is not COMPLETE_200K: {status.get('checkpoint_status')!r}"
        )
    if status.get("audit_mode") != "checkpoint":
        raise FinalizationError("checkpoint status audit_mode is not checkpoint")
    exact_status = {
        "accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
        "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
        "geometry_frequency_rows": TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ),
    }
    for field, expected in exact_status.items():
        if _as_int(status.get(field)) != expected:
            raise FinalizationError(
                f"checkpoint status {field} mismatch: actual={status.get(field)!r}, expected={expected}"
            )
    if status.get("campaign_id") != CAMPAIGN_ID or status.get(
        "contract_fingerprint_sha256"
    ) != fingerprint:
        raise FinalizationError("checkpoint status campaign identity mismatch")
    if receipt.get("overall_status") != "PASS" or receipt.get("decision") != "USE_CHECKPOINT":
        raise FinalizationError("checkpoint receipt is not PASS/USE_CHECKPOINT")
    if receipt.get("audit_mode") != "checkpoint" or _as_int(
        receipt.get("expected_accepted")
    ) != TARGET_ACCEPTED_GEOMETRIES:
        raise FinalizationError("checkpoint receipt is not the exact terminal checkpoint audit")
    if receipt.get("campaign_id") != CAMPAIGN_ID or receipt.get(
        "contract_fingerprint_sha256"
    ) != fingerprint:
        raise FinalizationError("checkpoint receipt campaign identity mismatch")
    receipt_checks = receipt.get("checks")
    if not isinstance(receipt_checks, list) or not receipt_checks:
        raise FinalizationError("checkpoint receipt has no audit checks")
    failed_checks = [
        item.get("name") if isinstance(item, Mapping) else "<malformed-check>"
        for item in receipt_checks
        if not isinstance(item, Mapping) or not bool(item.get("pass"))
    ]
    if failed_checks:
        raise FinalizationError(f"checkpoint receipt contains failed checks: {failed_checks[:5]}")
    if coverage.get("campaign_id") != CAMPAIGN_ID or coverage.get(
        "contract_fingerprint_sha256"
    ) != fingerprint:
        raise FinalizationError("coverage summary campaign identity mismatch")
    if _as_int(coverage.get("expected_accepted_geometries")) != TARGET_ACCEPTED_GEOMETRIES:
        raise FinalizationError("coverage summary accepted count mismatch")
    if _as_int(coverage.get("feature_row_count")) != TARGET_ACCEPTED_GEOMETRIES * len(
        FREQUENCY_GRID_HZ
    ):
        raise FinalizationError("coverage summary feature-row count mismatch")

    evidence_bindings = (
        (receipt.get("inputs", {}).get("contract"), contract_path, "contract"),
        (
            receipt.get("inputs", {}).get("accepted_geometries"),
            accepted_path,
            "accepted_geometries",
        ),
        (
            receipt.get("inputs", {}).get("long_features"),
            features_path,
            "long_features",
        ),
        (
            receipt.get("outputs", {}).get("coverage_cells"),
            checkpoint_dir / "physical_coverage_cells_by_anchor.csv",
            "coverage_cells",
        ),
        (
            receipt.get("outputs", {}).get("checkpoint_status"),
            checkpoint_dir / "CHECKPOINT_STATUS.json",
            "checkpoint_status",
        ),
        (
            receipt.get("outputs", {}).get("coverage_summary"),
            checkpoint_dir / "COVERAGE_SUMMARY.json",
            "coverage_summary",
        ),
    )
    for evidence, path, name in evidence_bindings:
        _verify_evidence(evidence, path, name)
    return contract, fingerprint, receipt


def _read_accepted_identities(
    path: Path, *, fingerprint: str, expected_count: int
) -> tuple[list[AcceptedIdentity], list[str]]:
    identities: list[AcceptedIdentity] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        required = {
            "geometry_id",
            "geometry_sha256",
            "campaign_contract_fingerprint",
            *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        }
        missing = required - set(fieldnames)
        if missing:
            raise FinalizationError(f"accepted ledger is missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            geometry_id = str(row.get("geometry_id") or "").strip()
            supplied_hash = str(row.get("geometry_sha256") or "").strip().lower()
            if not geometry_id or geometry_id in seen_ids:
                raise FinalizationError(
                    f"accepted ledger line {line} has missing/duplicate geometry_id"
                )
            values = {name: row.get(f"geom__{name}") for name in GEOMETRY_FIELDS}
            try:
                actual_hash = canonical_geometry_sha256(values)
            except (KeyError, TypeError, ValueError) as exc:
                raise FinalizationError(
                    f"accepted ledger line {line} geometry identity failed: {exc}"
                ) from exc
            if supplied_hash != actual_hash or supplied_hash in seen_hashes:
                raise FinalizationError(
                    f"accepted ledger line {line} geometry hash is mismatched or duplicated"
                )
            if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
                raise FinalizationError(
                    f"accepted ledger line {line} contract fingerprint mismatch"
                )
            seen_ids.add(geometry_id)
            seen_hashes.add(supplied_hash)
            identities.append(AcceptedIdentity(geometry_id, supplied_hash))
    if len(identities) != expected_count:
        raise FinalizationError(
            f"accepted ledger count mismatch: actual={len(identities)}, expected={expected_count}"
        )
    return identities, fieldnames


def _collect_actual_anchor_memberships(
    path: Path,
    *,
    identities: Sequence[AcceptedIdentity],
    fingerprint: str,
) -> tuple[dict[str, tuple[int, ...]], Counter[int], int]:
    identity_by_id = {item.geometry_id: item.geometry_sha256 for item in identities}
    memberships: dict[str, tuple[int, ...]] = {}
    occupancies: Counter[int] = Counter()
    current_id: str | None = None
    current_frequencies: list[int] = []
    current_memberships: list[int] = []
    row_count = 0

    def finish_current() -> None:
        nonlocal current_id, current_frequencies, current_memberships
        if current_id is None:
            return
        if current_id in memberships:
            raise FinalizationError(
                f"long features contain non-contiguous duplicate geometry block: {current_id}"
            )
        if tuple(current_frequencies) != FREQUENCY_GRID_HZ:
            raise FinalizationError(
                f"long features geometry {current_id!r} does not have the exact 56-point grid"
            )
        unique = tuple(sorted(set(current_memberships)))
        if len(unique) != len(current_memberships):
            raise FinalizationError(
                f"long features geometry {current_id!r} has duplicate anchor-cell contributions"
            )
        memberships[current_id] = unique
        occupancies.update(unique)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "geometry_id",
            "geometry_sha256",
            "campaign_contract_fingerprint",
            "frequency_hz",
            "broadband_descriptor_valid",
            "xp_ohm",
            "xs_ohm",
            "qmin",
            "k_abs",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise FinalizationError(f"long features are missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            row_count += 1
            geometry_id = str(row.get("geometry_id") or "").strip()
            if geometry_id != current_id:
                finish_current()
                current_id = geometry_id
                current_frequencies = []
                current_memberships = []
            expected_hash = identity_by_id.get(geometry_id)
            if expected_hash is None:
                raise FinalizationError(
                    f"long features line {line} references a non-accepted geometry"
                )
            if str(row.get("geometry_sha256") or "").strip().lower() != expected_hash:
                raise FinalizationError(f"long features line {line} geometry SHA mismatch")
            if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
                raise FinalizationError(f"long features line {line} contract fingerprint mismatch")
            frequency_hz = _as_int(row.get("frequency_hz"))
            if frequency_hz is None:
                raise FinalizationError(f"long features line {line} has invalid frequency")
            current_frequencies.append(frequency_hz)
            anchor_ghz = frequency_hz // 1_000_000_000
            if anchor_ghz not in ANCHOR_FREQUENCIES_GHZ or not _truthy(
                row.get("broadband_descriptor_valid")
            ):
                continue
            values = [_as_float(row.get(name)) for name in ("xp_ohm", "xs_ohm", "qmin", "k_abs")]
            if any(value is None for value in values):
                raise FinalizationError(
                    f"long features line {line} has non-finite primary-cell values"
                )
            cell = primary_cell_for_values(
                anchor_ghz=int(anchor_ghz),
                xp_ohm=float(values[0]),
                xs_ohm=float(values[1]),
                qmin=float(values[2]),
                k_abs=float(values[3]),
            )
            if cell is not None:
                anchor_index = ANCHOR_FREQUENCIES_GHZ.index(int(anchor_ghz))
                current_memberships.append(
                    anchor_index * PRIMARY_CELLS_PER_ANCHOR + cell.local_index
                )
    finish_current()
    if set(memberships) != set(identity_by_id):
        missing_ids = sorted(set(identity_by_id) - set(memberships))
        extra_ids = sorted(set(memberships) - set(identity_by_id))
        raise FinalizationError(
            f"long-feature geometry set mismatch: missing={missing_ids[:3]}, extra={extra_ids[:3]}"
        )
    return memberships, occupancies, row_count


def _read_audited_cell_counts(path: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"conditioned_cell_index", "actual_count"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise FinalizationError(f"anchor-cell audit is missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            index = _as_int(row.get("conditioned_cell_index"))
            count = _as_int(row.get("actual_count"))
            if index is None or not 0 <= index < PRIMARY_FREQUENCY_CONDITIONED_CELLS:
                raise FinalizationError(f"anchor-cell audit line {line} has invalid cell index")
            if count is None or count < 0 or index in counts:
                raise FinalizationError(f"anchor-cell audit line {line} has invalid/duplicate count")
            counts[index] = count
    expected = set(range(PRIMARY_FREQUENCY_CONDITIONED_CELLS))
    if set(counts) != expected:
        raise FinalizationError(
            f"anchor-cell audit does not enumerate all fixed cells: actual={len(counts)}, expected={len(expected)}"
        )
    return counts


def _coverage_weight_rows(
    *,
    identities: Sequence[AcceptedIdentity],
    memberships: Mapping[str, Sequence[int]],
    occupancies: Mapping[int, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_scores: list[float | None] = []
    for item in identities:
        cells = memberships[item.geometry_id]
        if not cells:
            raw_scores.append(None)
            continue
        raw_scores.append(sum(1.0 / float(occupancies[cell]) for cell in cells) / len(cells))
    active = [value for value in raw_scores if value is not None]
    if not active:
        raise FinalizationError("no geometry has an actual in-range broadband-valid anchor cell")
    active_mean = sum(active) / len(active)
    normalized = [1.0 if value is None else float(value) / active_mean for value in raw_scores]
    final_weights = _mean_one_clipped_weights(
        normalized, low=WEIGHT_CLIP_LOW, high=WEIGHT_CLIP_HIGH
    )
    rows: list[dict[str, Any]] = []
    for item, raw, unbounded, weight in zip(
        identities, raw_scores, normalized, final_weights
    ):
        cells = tuple(memberships[item.geometry_id])
        rows.append(
            {
                "geometry_id": item.geometry_id,
                "geometry_sha256": item.geometry_sha256,
                "actual_anchor_cell_count": len(cells),
                "actual_anchor_conditioned_cell_indices": ";".join(map(str, cells)),
                "actual_anchor_cell_ids": ";".join(_cell_id(index) for index in cells),
                "actual_anchor_cell_occupancies": ";".join(
                    str(int(occupancies[index])) for index in cells
                ),
                "raw_mean_inverse_occupancy": "" if raw is None else f"{raw:.17g}",
                "normalized_unclipped_weight": f"{unbounded:.17g}",
                "training_weight": f"{weight:.17g}",
                "weight_clip_low": f"{WEIGHT_CLIP_LOW:.17g}",
                "weight_clip_high": f"{WEIGHT_CLIP_HIGH:.17g}",
                "zero_primary_cell_policy": "neutral_weight_1_before_global_clipped_normalization",
                "evidence_class": "derived_from_actual_fresh_real_emx_anchor_cells",
            }
        )
    finite_and_clipped = all(
        math.isfinite(value) and WEIGHT_CLIP_LOW - 1.0e-12 <= value <= WEIGHT_CLIP_HIGH + 1.0e-12
        for value in final_weights
    )
    summary = {
        "algorithm": "mean_inverse_actual_anchor_cell_occupancy_then_global_mean_one_clipping",
        "weight_clip": [WEIGHT_CLIP_LOW, WEIGHT_CLIP_HIGH],
        "geometry_count": len(rows),
        "geometry_with_primary_cells": len(active),
        "geometry_without_primary_cells": len(rows) - len(active),
        "final_weight_min": min(final_weights),
        "final_weight_max": max(final_weights),
        "final_weight_mean": sum(final_weights) / len(final_weights),
        "all_finite_and_within_clip": finite_and_clipped,
        "no_sample_duplication": True,
        "zero_primary_cell_policy": "neutral_weight_1_before_global_clipped_normalization",
    }
    return rows, summary


def _mean_one_clipped_weights(
    values: Sequence[float], *, low: float, high: float
) -> list[float]:
    if not values or not (0.0 < low <= 1.0 <= high):
        raise FinalizationError("invalid weight normalization inputs")
    if any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise FinalizationError("coverage weights must be finite and positive before clipping")

    def mean_for(scale: float) -> float:
        return sum(min(high, max(low, scale * value)) for value in values) / len(values)

    left, right = 0.0, 1.0
    while mean_for(right) < 1.0:
        right *= 2.0
        if right > 1.0e18:
            raise FinalizationError("could not normalize clipped weights to mean one")
    for _ in range(100):
        middle = (left + right) / 2.0
        if mean_for(middle) < 1.0:
            left = middle
        else:
            right = middle
    scale = (left + right) / 2.0
    return [min(high, max(low, scale * value)) for value in values]


def _maximal_balanced_subset(
    *,
    identities: Sequence[AcceptedIdentity],
    memberships: Mapping[str, Sequence[int]],
    occupancies: Mapping[int, int],
    seed: int,
) -> tuple[set[str], dict[str, dict[str, Any]], dict[str, Any]]:
    """Return the strict equal-quota subset under rarest-cell ownership.

    Multi-anchor geometries belong to as many as eight physical cells.  To
    retain geometry uniqueness, each geometry is deterministically assigned
    to its least-populated actual cell (cell index breaks ties).  Water filling
    then raises every non-empty owner stratum to the largest common integer
    quota supported by all strata.  This is exactly maximal under that frozen
    partition, without claiming a global hypergraph optimum.
    """

    identity_by_id = {item.geometry_id: item for item in identities}
    strata: dict[int, list[str]] = defaultdict(list)
    owner_by_id: dict[str, int] = {}
    no_cell_count = 0
    for item in identities:
        cells = tuple(memberships[item.geometry_id])
        if not cells:
            no_cell_count += 1
            continue
        owner = min(cells, key=lambda cell: (int(occupancies[cell]), int(cell)))
        owner_by_id[item.geometry_id] = owner
        strata[owner].append(item.geometry_id)
    if not strata:
        raise FinalizationError("cannot create a balanced subset without observed owner strata")
    quota = min(len(values) for values in strata.values())
    if quota <= 0:
        raise FinalizationError("capacity-aware water-fill quota is not positive")

    selected: set[str] = set()
    metadata: dict[str, dict[str, Any]] = {}
    for owner in sorted(strata):
        ordered = sorted(
            strata[owner],
            key=lambda geometry_id: hashlib.sha256(
                f"{seed}|{owner}|{identity_by_id[geometry_id].geometry_sha256}".encode("ascii")
            ).hexdigest(),
        )
        for rank, geometry_id in enumerate(ordered[:quota], start=1):
            selected.add(geometry_id)
            metadata[geometry_id] = {
                "balanced_subset_owner_conditioned_cell_index": owner,
                "balanced_subset_owner_cell_id": _cell_id(owner),
                "balanced_subset_owner_source_capacity": len(strata[owner]),
                "balanced_subset_equal_quota": quota,
                "balanced_subset_selection_rank_within_owner": rank,
                "balanced_subset_seed": seed,
            }
    expected_count = quota * len(strata)
    if len(selected) != expected_count:
        raise FinalizationError(
            f"balanced subset count mismatch: actual={len(selected)}, expected={expected_count}"
        )
    selected_owner_counts = Counter(owner_by_id[geometry_id] for geometry_id in selected)
    if set(selected_owner_counts.values()) != {quota}:
        raise FinalizationError("balanced subset owner strata are not at the exact common quota")
    return selected, metadata, {
        "algorithm": "capacity_aware_strict_equal_water_fill_v1",
        "geometry_partition": "least_populated_actual_anchor_cell_then_conditioned_index_tie_break",
        "maximality_scope": (
            "largest exact equal-quota geometry-unique subset under the frozen rarest-actual-cell ownership partition"
        ),
        "does_not_claim_global_multi_anchor_hypergraph_optimum": True,
        "seed": seed,
        "observed_owner_strata": len(strata),
        "minimum_owner_stratum_capacity": quota,
        "equal_quota_per_owner_stratum": quota,
        "subset_geometry_count": len(selected),
        "excluded_geometry_without_primary_cell": no_cell_count,
        "source_geometry_count": len(identities),
        "present_as_full_200k_dataset": False,
    }


def _future_split_assignments(
    identities: Sequence[AcceptedIdentity],
) -> tuple[dict[str, str], dict[str, Any]]:
    counts = _largest_remainder_counts(len(identities), FUTURE_SPLIT_RATIOS)
    ordered = sorted(
        identities,
        key=lambda item: hashlib.sha256(
            f"{FUTURE_SPLIT_SALT}|{item.geometry_sha256}".encode("ascii")
        ).hexdigest(),
    )
    assignments: dict[str, str] = {}
    cursor = 0
    identity_digests: dict[str, str] = {}
    for split_name, _ in FUTURE_SPLIT_RATIOS:
        split_items = ordered[cursor : cursor + counts[split_name]]
        cursor += counts[split_name]
        for item in split_items:
            assignments[item.geometry_id] = split_name
        identity_digests[split_name] = hashlib.sha256(
            "\n".join(sorted(item.geometry_sha256 for item in split_items)).encode("ascii")
        ).hexdigest()
    if cursor != len(identities) or len(assignments) != len(identities):
        raise FinalizationError("future split assignment count mismatch")
    return assignments, {
        "geometry_count": len(identities),
        "split_counts": counts,
        "split_geometry_identity_set_sha256": identity_digests,
    }


def _largest_remainder_counts(
    total: int, ratios: Sequence[tuple[str, float]]
) -> dict[str, int]:
    if total < 0 or not ratios or not math.isclose(
        sum(ratio for _, ratio in ratios), 1.0, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise FinalizationError("invalid future split ratios")
    exact = {name: total * ratio for name, ratio in ratios}
    counts = {name: int(math.floor(value)) for name, value in exact.items()}
    remaining = total - sum(counts.values())
    order = sorted(
        (name for name, _ in ratios),
        key=lambda name: (-(exact[name] - counts[name]), [item[0] for item in ratios].index(name)),
    )
    for name in order[:remaining]:
        counts[name] += 1
    return counts


def _write_balanced_subset(
    *,
    source: Path,
    output: Path,
    source_fieldnames: Sequence[str],
    selected_ids: set[str],
    metadata: Mapping[str, Mapping[str, Any]],
) -> int:
    extra_fields = [
        "balanced_subset_owner_conditioned_cell_index",
        "balanced_subset_owner_cell_id",
        "balanced_subset_owner_source_capacity",
        "balanced_subset_equal_quota",
        "balanced_subset_selection_rank_within_owner",
        "balanced_subset_seed",
    ]
    collisions = set(source_fieldnames) & set(extra_fields)
    if collisions:
        raise FinalizationError(f"accepted ledger already uses subset metadata fields: {sorted(collisions)}")
    count = 0
    with source.open(newline="", encoding="utf-8-sig") as src, output.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=list(source_fieldnames) + extra_fields)
        writer.writeheader()
        for row in reader:
            geometry_id = str(row.get("geometry_id") or "").strip()
            if geometry_id not in selected_ids:
                continue
            writer.writerow({**row, **metadata[geometry_id]})
            count += 1
    return count


def _write_split_assignments(
    path: Path,
    identities: Sequence[AcceptedIdentity],
    assignments: Mapping[str, str],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "geometry_id",
                "geometry_sha256",
                "future_split",
                "split_unit",
                "frequency_rows_kept_together",
            ],
        )
        writer.writeheader()
        for item in identities:
            writer.writerow(
                {
                    "geometry_id": item.geometry_id,
                    "geometry_sha256": item.geometry_sha256,
                    "future_split": assignments[item.geometry_id],
                    "split_unit": "canonical_geometry_identity",
                    "frequency_rows_kept_together": len(FREQUENCY_GRID_HZ),
                }
            )


def _write_dict_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise FinalizationError(f"refusing to write empty CSV: {path.name}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _cell_id(conditioned_index: int) -> str:
    anchor_index, local = divmod(int(conditioned_index), PRIMARY_CELLS_PER_ANCHOR)
    if not 0 <= anchor_index < len(ANCHOR_FREQUENCIES_GHZ):
        raise FinalizationError(f"invalid conditioned cell index: {conditioned_index}")
    k_bin = local % PRIMARY_BINS_PER_DIMENSION
    local //= PRIMARY_BINS_PER_DIMENSION
    qmin_bin = local % PRIMARY_BINS_PER_DIMENSION
    local //= PRIMARY_BINS_PER_DIMENSION
    xs_bin = local % PRIMARY_BINS_PER_DIMENSION
    xp_bin = local // PRIMARY_BINS_PER_DIMENSION
    anchor = ANCHOR_FREQUENCIES_GHZ[anchor_index]
    return f"f{anchor:02d}_xp{xp_bin}_xs{xs_bin}_q{qmin_bin}_k{k_bin}"


def _verify_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    seen: set[str] = set()
    for line_number, raw in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise FinalizationError(f"invalid SHA256SUMS line {line_number}")
        expected, name = parts
        path = directory / name
        if Path(name).name != name or name in seen or not path.is_file():
            raise FinalizationError(f"invalid/missing SHA256SUMS target: {name!r}")
        seen.add(name)
        if _sha256(path) != expected.lower():
            raise FinalizationError(f"checkpoint SHA256SUMS mismatch: {name}")
    required_indexed = set(REQUIRED_CHECKPOINT_FILES) - {"SHA256SUMS.txt"}
    if not required_indexed.issubset(seen):
        raise FinalizationError(
            f"SHA256SUMS does not bind required checkpoint files: {sorted(required_indexed - seen)}"
        )


def _verify_evidence(evidence: Any, path: Path, name: str) -> None:
    if not isinstance(evidence, Mapping):
        raise FinalizationError(f"checkpoint receipt lacks {name} evidence")
    expected = str(evidence.get("sha256") or "").lower()
    if len(expected) != 64 or _sha256(path) != expected:
        raise FinalizationError(f"checkpoint receipt {name} SHA-256 mismatch")


def _file_evidence(path: Path, *, recorded_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(recorded_path if recorded_path is not None else path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise FinalizationError(f"failed to parse JSON {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FinalizationError(f"JSON root is not an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not parsed.is_integer():
        return None
    return int(parsed)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
