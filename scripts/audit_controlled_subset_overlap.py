#!/usr/bin/env python3
"""Audit dependence among preregistered training subsets without model results.

The input CSVs are expected to contain the materializer's canonical geometry
identity and split-assignment columns.  Only rows assigned to ``train`` enter
the overlap calculation; the shared validation/test rows are deliberately
excluded.  The output is created no-clobber so the audit remains an immutable
pre-result receipt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path


IDENTITY_COLUMN = "canonical_geometry_identity_sha256"
SPLIT_COLUMN = "controlled_split_assignment"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _training_identities(path: Path) -> set[str]:
    identities: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {IDENTITY_COLUMN, SPLIT_COLUMN}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            if row[SPLIT_COLUMN] != "train":
                continue
            identity = row[IDENTITY_COLUMN].strip().lower()
            if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
                raise ValueError(f"{path}: invalid geometry identity {identity!r}")
            if identity in identities:
                raise ValueError(f"{path}: duplicate training identity {identity}")
            identities.add(identity)
    return identities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset-csv", action="append", required=True)
    parser.add_argument("--expected-subsets", type=int, default=5)
    parser.add_argument("--expected-training-rows", type=int, default=100_000)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    paths = [Path(value).resolve() for value in args.subset_csv]
    if len(paths) != args.expected_subsets:
        raise ValueError(
            f"expected {args.expected_subsets} subsets, received {len(paths)}"
        )
    output_path = Path(args.output_json).resolve()
    if output_path.exists():
        raise FileExistsError(f"no-clobber output already exists: {output_path}")

    names = [path.stem for path in paths]
    if len(names) != len(set(names)):
        raise ValueError("subset CSV basenames must be unique")
    identity_sets = [_training_identities(path) for path in paths]
    row_counts = [len(values) for values in identity_sets]
    if any(count != args.expected_training_rows for count in row_counts):
        raise ValueError(
            f"training counts {row_counts} do not all equal {args.expected_training_rows}"
        )

    intersection_matrix: list[list[int]] = []
    jaccard_matrix: list[list[float]] = []
    for left in identity_sets:
        overlap_row: list[int] = []
        jaccard_row: list[float] = []
        for right in identity_sets:
            intersection = len(left.intersection(right))
            union = len(left.union(right))
            overlap_row.append(intersection)
            jaccard_row.append(intersection / union)
        intersection_matrix.append(overlap_row)
        jaccard_matrix.append(jaccard_row)

    off_diagonal_intersections = [
        len(identity_sets[i].intersection(identity_sets[j]))
        for i, j in combinations(range(len(identity_sets)), 2)
    ]
    off_diagonal_jaccards = [
        len(identity_sets[i].intersection(identity_sets[j]))
        / len(identity_sets[i].union(identity_sets[j]))
        for i, j in combinations(range(len(identity_sets)), 2)
    ]
    payload = {
        "schema": "controlled_small_subset_overlap_audit_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "inputs": [
            {
                "label": name,
                "csv_path": str(path),
                "csv_sha256": _sha256(path),
                "training_identity_count": len(identities),
            }
            for name, path, identities in zip(names, paths, identity_sets)
        ],
        "matrix_labels": names,
        "pairwise_training_identity_intersection_count": intersection_matrix,
        "pairwise_training_identity_jaccard": jaccard_matrix,
        "off_diagonal_summary": {
            "pair_count": len(off_diagonal_intersections),
            "intersection_count_min": min(off_diagonal_intersections),
            "intersection_count_mean": sum(off_diagonal_intersections)
            / len(off_diagonal_intersections),
            "intersection_count_max": max(off_diagonal_intersections),
            "jaccard_min": min(off_diagonal_jaccards),
            "jaccard_mean": sum(off_diagonal_jaccards) / len(off_diagonal_jaccards),
            "jaccard_max": max(off_diagonal_jaccards),
        },
        "dependence_boundary": (
            "All five 100k subsets are sampled from the same fixed 200k training pool. "
            "Their paired-replicate interval is conditional on that historical pool and must not "
            "be described as an independent-sample population confidence interval."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"overall_status={payload['overall_status']}")
    print(f"output_json={output_path}")
    print(f"output_sha256={_sha256(output_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
