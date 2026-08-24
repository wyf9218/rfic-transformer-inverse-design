#!/usr/bin/env python3
"""Build the result-blind, exact-nested real-EMX 10K/20K source tables.

The historical 10K rows and their physical-cell split are treated as frozen
evidence.  Every historical row must match exactly one row in the authoritative
100K source.  The 20K arm appends only result-blind, identity-disjoint rows from
the historical training cells, in quotas proportional to the historical
training-cell distribution.  Validation and test identities therefore remain
byte-for-byte common across the two arms.

This script materializes data contracts only.  It never reads model metrics,
trains a model, evaluates a model, or authorizes a launch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import inspect
import json
import math
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rfic_transformer_inverse_design.controlled_real10k_20k_contract as controlled_contract  # noqa: E402
import rfic_transformer_inverse_design.model_splitting as model_splitting  # noqa: E402
from rfic_transformer_inverse_design.controlled_real10k_20k_contract import (  # noqa: E402
    EXACT_EXTRA_SELECTION_SEED,
    EXACT_PAIRED_SEEDS,
    GEOMETRY_COLUMNS,
    GEOMETRY_LOWER,
    GEOMETRY_UPPER,
    INPUT_COLUMNS,
    INPUT_LOWER,
    INPUT_UPPER,
    OUTPUT_COLUMNS,
    PHYSICAL_CELL_BINS,
    PHYSICAL_CELL_ENCODING,
    canonical_physical_cell_id,
)
from rfic_transformer_inverse_design.model_splitting import (  # noqa: E402
    split_physical_feature_indices,
)

PRODUCTION_COUNTS = {
    "historical": 10_000,
    "authoritative": 100_000,
    "train": 7_871,
    "validation": 1_227,
    "test": 902,
    "extra": 10_000,
}
FROZEN_HISTORICAL_10K_CSV_SHA256 = (
    "3027290eb1b4c229a23f0676f970ff9d13762677a897a0d7e2aed959075c85c8"
)
FROZEN_AUTHORITATIVE_100K_CSV_SHA256 = (
    "68468eb2d3678aa0793157c1c647e975f60e8ec1673c259050ababe9fd1ff08a"
)
FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256 = (
    "90a81532f7342ae7348248ea45889368fbb13ed2b6ee8f89e789f80f6811a3fa"
)
FROZEN_HISTORICAL_ARCHITECTURE = {
    "forward_depth": 3,
    "forward_width": 256,
    "forward_hidden_widths": [256, 256, 256],
    "forward_hidden_widths_source": "legacy_uniform_depth_width",
    "inverse_depth": 3,
    "inverse_width": 256,
    "inverse_hidden_widths": [256, 256, 256],
    "inverse_hidden_widths_source": "legacy_uniform_depth_width",
}
FROZEN_HISTORICAL_GEOMETRY_OUTPUT_CONSTRAINT = (
    "sigmoid_projection_to_observed_training_envelope"
)

VERIFIED_CONTEXT_SCHEMA = "controlled_real10k_20k_verified_materialization_inputs_v1"
VERIFIED_CONTEXT_ROLES = (
    "materialization_builder_code",
    "shared_contract_code",
    "splitter_code",
    "historical_10k_csv",
    "authoritative_100k_csv",
    "historical_model_summary_json",
)
VERIFIED_ENTRY_KEYS = {
    "logical_path",
    "sha256",
    "size_bytes",
    "mode_octal",
    "nlink",
    "st_dev",
    "st_ino",
    "bytes",
}


def main(
    argv: list[str] | None = None,
    *,
    verified_context: Mapping[str, Any] | None = None,
) -> int:
    args = _parse_args(argv)
    verified = (
        _validate_verified_context(args, verified_context)
        if verified_context is not None
        else None
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    try:
        return _run(args, out_dir, verified_context=verified)
    except Exception as exc:
        failure = {
            "schema": "controlled_real10k_20k_nested_build_failure_v2",
            "generated_utc": _utc_now(),
            "status": "FAIL",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
            "partial_outputs_preserved": True,
            "training_launch_authorized": False,
        }
        failure_path = out_dir / "BUILD_FAIL.json"
        if not failure_path.exists():
            _write_json_exclusive(failure_path, failure)
        print("status=FAIL", file=sys.stderr)
        print(f"failure_receipt={failure_path}", file=sys.stderr)
        raise


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-10k-csv", required=True)
    parser.add_argument("--historical-10k-sha256", required=True)
    parser.add_argument("--authoritative-100k-csv", required=True)
    parser.add_argument("--authoritative-100k-sha256", required=True)
    parser.add_argument("--historical-model-summary-json", required=True)
    parser.add_argument("--historical-model-summary-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--extra-count", type=int, default=10_000)
    parser.add_argument("--selection-seed", type=int, required=True)
    # Count overrides make compact, explicitly non-production fixtures possible.
    # Production eligibility below remains fail-closed unless all defaults hold.
    parser.add_argument("--expected-historical-rows", type=int, default=10_000)
    parser.add_argument("--expected-authoritative-rows", type=int, default=100_000)
    parser.add_argument("--expected-train-rows", type=int, default=7_871)
    parser.add_argument("--expected-validation-rows", type=int, default=1_227)
    parser.add_argument("--expected-test-rows", type=int, default=902)
    args = parser.parse_args(argv)
    for name in (
        "historical_10k_sha256",
        "authoritative_100k_sha256",
        "historical_model_summary_sha256",
    ):
        value = str(getattr(args, name)).strip().lower()
        if not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256")
        setattr(args, name, value)
    for name in (
        "extra_count",
        "expected_historical_rows",
        "expected_authoritative_rows",
        "expected_train_rows",
        "expected_validation_rows",
        "expected_test_rows",
    ):
        if int(getattr(args, name)) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if (
        int(args.expected_train_rows)
        + int(args.expected_validation_rows)
        + int(args.expected_test_rows)
        != int(args.expected_historical_rows)
    ):
        parser.error("expected train+validation+test rows must equal expected historical rows")
    return args


def _run(
    args: argparse.Namespace,
    out_dir: Path,
    *,
    verified_context: dict[str, dict[str, Any]] | None,
) -> int:
    if verified_context is None:
        historical_path = Path(args.historical_10k_csv).expanduser().resolve()
        authoritative_path = Path(args.authoritative_100k_csv).expanduser().resolve()
        summary_path = Path(args.historical_model_summary_json).expanduser().resolve()
    else:
        historical_path = Path(
            verified_context["historical_10k_csv"]["logical_path"]
        )
        authoritative_path = Path(
            verified_context["authoritative_100k_csv"]["logical_path"]
        )
        summary_path = Path(
            verified_context["historical_model_summary_json"]["logical_path"]
        )
    implementation_identities = _implementation_identities(verified_context)
    if verified_context is None:
        source_identities = {
            "historical_10k_csv": _require_sha(
                historical_path, args.historical_10k_sha256, "historical 10K CSV"
            ),
            "authoritative_100k_csv": _require_sha(
                authoritative_path,
                args.authoritative_100k_sha256,
                "authoritative 100K CSV",
            ),
            "historical_model_summary_json": _require_sha(
                summary_path,
                args.historical_model_summary_sha256,
                "historical model summary",
            ),
        }
        summary = _load_json_object(summary_path)
        historical_source: Path | bytes = historical_path
        authoritative_source: Path | bytes = authoritative_path
    else:
        source_identities = {
            role: verified_context[role]["sha256"]
            for role in (
                "historical_10k_csv",
                "authoritative_100k_csv",
                "historical_model_summary_json",
            )
        }
        summary = _load_json_object_bytes(
            verified_context["historical_model_summary_json"]["bytes"],
            summary_path,
        )
        historical_source = verified_context["historical_10k_csv"]["bytes"]
        authoritative_source = verified_context["authoritative_100k_csv"]["bytes"]
    historical_rows = _load_table(
        historical_source,
        logical_path=historical_path,
        expected_rows=int(args.expected_historical_rows),
        source_label="historical_10k",
    )
    authoritative_rows = _load_table(
        authoritative_source,
        logical_path=authoritative_path,
        expected_rows=int(args.expected_authoritative_rows),
        source_label="authoritative_100k",
    )
    summary_contract = _validate_summary_contract(
        summary,
        historical_sha=args.historical_10k_sha256,
        expected_rows=int(args.expected_historical_rows),
    )

    historical_to_source = _match_historical_rows_once(
        historical_rows, authoritative_rows
    )
    arm10_rows = [
        _output_row(
            authoritative_rows[source_index],
            origin="historical10k_exact_authoritative_match",
        )
        for source_index in historical_to_source
    ]
    split, split_audit = _rebuild_and_verify_historical_split(
        arm10_rows,
        summary,
        expected_counts={
            "train": int(args.expected_train_rows),
            "validation": int(args.expected_validation_rows),
            "test": int(args.expected_test_rows),
        },
    )
    _assign_split(arm10_rows, split)
    _verify_historical_identity_sets(arm10_rows, split, summary)

    historical_exact_geometry = {
        row["canonical_geometry_identity_sha256"] for row in arm10_rows
    }
    historical_portable_geometry = {
        row["portable_geometry_decimal12_sha256"] for row in arm10_rows
    }
    historical_touchstone = {row["touchstone_sha256"] for row in arm10_rows}
    if len(historical_exact_geometry) != len(arm10_rows):
        raise ValueError("historical 10K contains duplicate exact geometry identities")
    if len(historical_portable_geometry) != len(arm10_rows):
        raise ValueError("historical 10K contains duplicate decimal12 geometry identities")
    if len(historical_touchstone) != len(arm10_rows):
        raise ValueError("historical 10K contains duplicate Touchstone content identities")

    train_indices = {int(value) for value in split["train"]}
    historical_train_cell_counts = Counter(
        arm10_rows[index]["controlled_physical_cell_4d"] for index in train_indices
    )
    train_cells = set(historical_train_cell_counts)
    quotas = _proportional_cell_quotas(
        historical_train_cell_counts,
        total=int(args.extra_count),
        seed=int(args.selection_seed),
    )
    selected_extra, candidate_audit = _select_extra_rows(
        authoritative_rows,
        matched_source_indices=set(historical_to_source),
        train_cells=train_cells,
        quotas=quotas,
        selection_seed=int(args.selection_seed),
        excluded_exact_geometry=historical_exact_geometry,
        excluded_portable_geometry=historical_portable_geometry,
        excluded_touchstone=historical_touchstone,
    )
    extra_rows = [
        _output_row(row, origin="authoritative100k_train_cell_extra")
        for row in selected_extra
    ]
    for row in extra_rows:
        row["controlled_split_assignment"] = "train"
    arm20_rows = [dict(row) for row in arm10_rows] + extra_rows
    _verify_nested_arms(
        arm10_rows,
        arm20_rows,
        extra_count=int(args.extra_count),
        expected_train20=int(args.expected_train_rows) + int(args.extra_count),
        expected_validation=int(args.expected_validation_rows),
        expected_test=int(args.expected_test_rows),
    )

    arm10_path = out_dir / "arm_source_n10000.csv"
    arm20_path = out_dir / "arm_source_n20000.csv"
    holdout_path = out_dir / "fixed_common_holdout_manifest.json"
    normalization_path = out_dir / "declared_midpoint_half_range_normalization_contract.json"
    materialization_summary_path = out_dir / "controlled_real10k_20k_nested_summary.json"
    qa_required_path = out_dir / "INDEPENDENT_QA_REQUIRED.json"
    receipt_path = out_dir / "controlled_real10k_20k_nested_receipt.json"
    sha_index_path = out_dir / "SHA256SUMS.txt"

    _write_csv_exclusive(arm10_path, arm10_rows)
    _write_csv_exclusive(arm20_path, arm20_rows)
    holdout = _build_holdout_manifest(
        arm10_rows,
        arm20_rows,
        split,
        split_audit,
        historical_summary_sha=args.historical_model_summary_sha256,
        shared_contract_sha=implementation_identities["shared_contract"]["sha256"],
    )
    _write_json_exclusive(holdout_path, holdout)
    normalization = _normalization_contract()
    _write_json_exclusive(normalization_path, normalization)

    production_exact = {
        "selection_seed_exact_20260824": int(args.selection_seed)
        == EXACT_EXTRA_SELECTION_SEED,
        "historical_10k_csv_identity_exact": args.historical_10k_sha256
        == FROZEN_HISTORICAL_10K_CSV_SHA256,
        "authoritative_100k_csv_identity_exact": args.authoritative_100k_sha256
        == FROZEN_AUTHORITATIVE_100K_CSV_SHA256,
        "historical_model_summary_identity_exact": args.historical_model_summary_sha256
        == FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256,
        "historical_source_rows_exact_10000": len(arm10_rows)
        == PRODUCTION_COUNTS["historical"],
        "authoritative_source_rows_exact_100000": len(authoritative_rows)
        == PRODUCTION_COUNTS["authoritative"],
        "historical_gradient_train_rows_exact_7871": len(split["train"])
        == PRODUCTION_COUNTS["train"],
        "historical_validation_rows_exact_1227": len(split["validation"])
        == PRODUCTION_COUNTS["validation"],
        "historical_test_rows_exact_902": len(split["test"])
        == PRODUCTION_COUNTS["test"],
        "extra_rows_exact_10000": len(extra_rows) == PRODUCTION_COUNTS["extra"],
        "new_gradient_train_rows_exact_17871": (
            len(split["train"]) + len(extra_rows) == 17_871
        ),
    }
    artifacts_before_summary = _artifact_map(
        [arm10_path, arm20_path, holdout_path, normalization_path]
    )
    materialization_summary = {
        "schema": "controlled_real10k_20k_nested_materialization_v2",
        "generated_utc": _utc_now(),
        "status": "PASS",
        "decision": (
            "PREPARED_FOR_INDEPENDENT_QA"
            if all(production_exact.values())
            else "TEST_OR_NONPRODUCTION_FIXTURE_ONLY"
        ),
        "result_accessed": False,
        "model_training_performed": False,
        "emx_performed": False,
        "implementation_identities": implementation_identities,
        "verified_input_consumption": _verified_consumption_record(verified_context),
        "shared_contract": {
            "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
            "physical_cell_bins": PHYSICAL_CELL_BINS,
            "extra_selection_seed": EXACT_EXTRA_SELECTION_SEED,
            "paired_seeds": list(EXACT_PAIRED_SEEDS),
        },
        "source_identities": {
            "historical_10k_csv": {
                "path": str(historical_path),
                "sha256": source_identities["historical_10k_csv"],
                "rows": len(historical_rows),
            },
            "authoritative_100k_csv": {
                "path": str(authoritative_path),
                "sha256": source_identities["authoritative_100k_csv"],
                "rows": len(authoritative_rows),
            },
            "historical_model_summary_json": {
                "path": str(summary_path),
                "sha256": source_identities["historical_model_summary_json"],
            },
        },
        "historical_model_contract": summary_contract,
        "historical_to_authoritative_match": {
            "historical_rows": len(historical_rows),
            "unique_exact_matches": len(historical_to_source),
            "unmatched_rows": 0,
            "multiply_matched_rows": 0,
            "source_row_number_set_sha256": _line_set_sha(
                str(authoritative_rows[index]["source_row_number"])
                for index in historical_to_source
            ),
        },
        "split_reconstruction": {
            "exact_match_to_historical_summary": True,
            "row_counts": {
                name: int(len(split[name]))
                for name in ("train", "validation", "test")
            },
            "cell_counts": split_audit["cell_counts"],
            "cell_ids": split_audit["cell_ids"],
            "physical_cell_partition_fingerprint_sha256": split_audit[
                "physical_cell_partition_fingerprint_sha256"
            ],
            "split_index_sha256": split_audit["split_index_sha256"],
            "split_fingerprint_sha256": split_audit["split_fingerprint_sha256"],
        },
        "selection_contract": {
            "method": "stable_sha256_rank_within_proportional_historical_train_cell_quotas_v1",
            "selection_seed": int(args.selection_seed),
            "selection_uses_model_results": False,
            "extra_count": len(extra_rows),
            "historical_train_cell_row_counts": dict(
                sorted(historical_train_cell_counts.items())
            ),
            "extra_cell_quotas": dict(sorted(quotas.items())),
            "candidate_audit": candidate_audit,
            "historical_geometry_excluded": True,
            "historical_touchstone_content_excluded": True,
            "extra_rows_restricted_to_historical_train_cells": True,
        },
        "arm_counts": {
            "n10000": {
                "source_table_rows": len(arm10_rows),
                "gradient_train_rows": len(split["train"]),
                "validation_rows": len(split["validation"]),
                "test_rows": len(split["test"]),
            },
            "n20000": {
                "source_table_rows": len(arm20_rows),
                "gradient_train_rows": len(split["train"]) + len(extra_rows),
                "validation_rows": len(split["validation"]),
                "test_rows": len(split["test"]),
            },
        },
        "nested_identity_contract": {
            "arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000": True,
            "common_output_schema": list(OUTPUT_COLUMNS),
            "historical_row_record_set_sha256": _row_record_set_sha(arm10_rows),
            "extra_row_record_set_sha256": _row_record_set_sha(extra_rows),
            "common_validation_and_test_unchanged": True,
            "geometry_identity_overlap_historical_vs_extra": 0,
            "touchstone_identity_overlap_historical_vs_extra": 0,
        },
        "fixed_contracts": {
            "common_holdout": {
                "path": str(holdout_path),
                "sha256": artifacts_before_summary[holdout_path.name]["sha256"],
            },
            "declared_midpoint_half_range_normalization": {
                "path": str(normalization_path),
                "sha256": artifacts_before_summary[normalization_path.name]["sha256"],
                "train_arm_specific_statistics_used": False,
                "large_arm_empirical_statistics_used": False,
            },
        },
        "production_exact_checks": production_exact,
        "artifacts": artifacts_before_summary,
        "release_boundary": (
            "Data materialization is result-blind and complete. Training remains unauthorized until a fresh "
            "independent QA receipt gives exact GO for these frozen bytes."
        ),
        "training_launch_authorized": False,
        "independent_qa_required": True,
    }
    _write_json_exclusive(materialization_summary_path, materialization_summary)
    qa_bound_artifacts = _artifact_map(
        [arm10_path, arm20_path, holdout_path, normalization_path]
    )
    qa_required = {
        "schema": "controlled_real10k_20k_independent_qa_required_v2",
        "generated_utc": _utc_now(),
        "status": "INDEPENDENT_QA_REQUIRED",
        "verdict": "NO_GO_PENDING_FRESH_INDEPENDENT_QA",
        "materialization_summary": {
            "path": str(materialization_summary_path),
            "sha256": _sha256_file(materialization_summary_path),
        },
        "implementation_identities": implementation_identities,
        "frozen_artifacts": qa_bound_artifacts,
        "frozen_scientific_contract": {
            "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
            "physical_cell_bins": PHYSICAL_CELL_BINS,
            "extra_selection_seed": EXACT_EXTRA_SELECTION_SEED,
            "paired_seeds": list(EXACT_PAIRED_SEEDS),
            "source_table_rows": {"n10000": len(arm10_rows), "n20000": len(arm20_rows)},
            "gradient_train_rows": {
                "n10000": len(split["train"]),
                "n20000": len(split["train"]) + len(extra_rows),
            },
            "validation_rows_common": len(split["validation"]),
            "test_rows_common": len(split["test"]),
        },
        "training_authorized": False,
        "result_access_authorized": False,
        "fresh_emx_authorized": False,
        "next_legal_gate": {
            "required_receipt_schema": "controlled_real10k_20k_independent_qa_exact_go_v2",
            "required_status": "GO",
            "must_bind_all_bytes_in_this_record": True,
            "must_report_zero_p0_and_zero_p1_findings": True,
        },
    }
    _write_json_exclusive(qa_required_path, qa_required)
    artifacts_before_receipt = _artifact_map(
        [
            arm10_path,
            arm20_path,
            holdout_path,
            normalization_path,
            materialization_summary_path,
            qa_required_path,
        ]
    )
    receipt = {
        "schema": "controlled_real10k_20k_nested_materialization_receipt_v2",
        "generated_utc": _utc_now(),
        "status": "PASS",
        "verdict": (
            "PREPARED_FOR_INDEPENDENT_QA"
            if all(production_exact.values())
            else "TEST_OR_NONPRODUCTION_FIXTURE_ONLY"
        ),
        "checks": {
            "all_historical_rows_match_exactly_once": True,
            "historical_split_exactly_reconstructed": True,
            "historical_10k_is_exact_subset_of_20k": True,
            "extra_rows_only_in_historical_train_cells": True,
            "historical_geometry_and_touchstone_excluded_from_extra": True,
            "common_holdout_frozen": True,
            "declared_normalization_frozen": True,
            "no_model_results_accessed": True,
            "no_training_or_emx_performed": True,
        },
        "source_sha256": {
            "historical_10k_csv": args.historical_10k_sha256,
            "authoritative_100k_csv": args.authoritative_100k_sha256,
            "historical_model_summary_json": args.historical_model_summary_sha256,
        },
        "implementation_identities": implementation_identities,
        "artifact_identities": artifacts_before_receipt,
        "arm_source_rows": {"n10000": len(arm10_rows), "n20000": len(arm20_rows)},
        "gradient_train_rows": {
            "n10000": len(split["train"]),
            "n20000": len(split["train"]) + len(extra_rows),
        },
        "validation_rows_common": len(split["validation"]),
        "test_rows_common": len(split["test"]),
        "production_exact_checks": production_exact,
        "training_launch_authorized": False,
        "independent_qa_required": True,
        "independent_qa_required_record": {
            "path": str(qa_required_path),
            "sha256": artifacts_before_receipt[qa_required_path.name]["sha256"],
        },
        "next_legal_gate": "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO",
        "sha256_closure_contract": {
            "index_filename": "SHA256SUMS.txt",
            "index_self_hash_included": False,
            "exact_entry_count": 7,
            "exact_filenames_in_order": [
                arm10_path.name,
                arm20_path.name,
                holdout_path.name,
                normalization_path.name,
                materialization_summary_path.name,
                qa_required_path.name,
                receipt_path.name,
            ],
        },
    }
    _write_json_exclusive(receipt_path, receipt)
    indexed_paths = [
        arm10_path,
        arm20_path,
        holdout_path,
        normalization_path,
        materialization_summary_path,
        qa_required_path,
        receipt_path,
    ]
    _write_sha_index_exclusive(sha_index_path, indexed_paths)

    print("status=PASS")
    print(f"arm_n10000_source_rows={len(arm10_rows)}")
    print(f"arm_n20000_source_rows={len(arm20_rows)}")
    print(f"arm_n10000_gradient_train_rows={len(split['train'])}")
    print(f"arm_n20000_gradient_train_rows={len(split['train']) + len(extra_rows)}")
    print(f"validation_rows_common={len(split['validation'])}")
    print(f"test_rows_common={len(split['test'])}")
    print(f"independent_qa_required={qa_required_path}")
    print(f"receipt={receipt_path}")
    return 0


def _load_table(
    source: Path | bytes,
    *,
    logical_path: Path,
    expected_rows: int,
    source_label: str,
) -> list[dict[str, Any]]:
    required = {
        "evaluation",
        "touchstone_path",
        "touchstone_sha256",
        *INPUT_COLUMNS,
        *GEOMETRY_COLUMNS,
    }
    rows: list[dict[str, Any]] = []
    if isinstance(source, bytes):
        try:
            handle = io.StringIO(source.decode("utf-8-sig"), newline="")
        except UnicodeError as exc:
            raise ValueError(f"{source_label} table is not valid UTF-8: {logical_path}") from exc
    else:
        handle = source.open(newline="", encoding="utf-8-sig")
    with handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{source_label} table lacks required columns: {missing}")
        for source_row_number, raw in enumerate(reader, start=1):
            evaluation = str(raw.get("evaluation") or "").strip()
            touchstone_path = str(raw.get("touchstone_path") or "").strip()
            touchstone_sha = str(raw.get("touchstone_sha256") or "").strip().lower()
            if not evaluation or not touchstone_path or not _is_sha256(touchstone_sha):
                raise ValueError(
                    f"{source_label} row {source_row_number} has invalid evaluation/path/Touchstone SHA"
                )
            inputs = _finite_vector(raw, INPUT_COLUMNS, source_label, source_row_number)
            geometry = _finite_vector(raw, GEOMETRY_COLUMNS, source_label, source_row_number)
            _require_inside(inputs, INPUT_LOWER, INPUT_UPPER, source_label, source_row_number, "input")
            _require_inside(
                geometry,
                GEOMETRY_LOWER,
                GEOMETRY_UPPER,
                source_label,
                source_row_number,
                "geometry",
            )
            cell = canonical_physical_cell_id(inputs, bins=PHYSICAL_CELL_BINS)
            normalized: dict[str, Any] = {
                "source_row_number": source_row_number,
                "evaluation": evaluation,
                "touchstone_path": touchstone_path,
                "touchstone_sha256": touchstone_sha,
                "inputs": inputs,
                "geometry": geometry,
                "cell": cell,
                "canonical_geometry_identity_sha256": _exact_geometry_identity(geometry),
                "portable_geometry_decimal12_sha256": _portable_geometry_identity(geometry),
            }
            normalized["scientific_match_key"] = _scientific_match_key(normalized)
            rows.append(normalized)
    if len(rows) != int(expected_rows):
        raise ValueError(
            f"{source_label} row count mismatch: expected={expected_rows} actual={len(rows)}"
        )
    return rows


def _exact_json_int(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an exact JSON integer")
    return value


def _exact_json_number(value: Any, label: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite JSON number, not a boolean")
    return float(value)


def _validate_summary_contract(
    summary: dict[str, Any], *, historical_sha: str, expected_rows: int
) -> dict[str, Any]:
    if str(summary.get("training_csv_sha256") or "").lower() != historical_sha:
        raise ValueError("historical model summary does not bind the supplied historical 10K CSV SHA")
    if _exact_json_int(summary.get("training_count"), "historical training_count") != expected_rows:
        raise ValueError("historical model summary training_count mismatch")
    if tuple(summary.get("input_columns") or []) != INPUT_COLUMNS:
        raise ValueError("historical model summary input-column contract mismatch")
    if tuple(summary.get("geometry_columns") or []) != GEOMETRY_COLUMNS:
        raise ValueError("historical model summary geometry-column contract mismatch")
    comparison = summary.get("model_comparison_contract") or {}
    split_contract = comparison.get("split") or {}
    arguments = summary.get("arguments") or {}
    expected = {
        "split_mode": "physical_cell_grouped",
        "bins": 4,
        "lower": list(INPUT_LOWER),
        "upper": list(INPUT_UPPER),
    }
    try:
        lower = [float(value) for value in str(split_contract["physical_cell_lower"]).split(",")]
        upper = [float(value) for value in str(split_contract["physical_cell_upper"]).split(",")]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("historical model summary has invalid physical-cell bounds") from exc
    bins = _exact_json_int(
        split_contract.get("physical_cell_bins"),
        "historical physical_cell_bins",
    )
    if (
        split_contract.get("mode") != expected["split_mode"]
        or bins != expected["bins"]
        or lower != expected["lower"]
        or upper != expected["upper"]
    ):
        raise ValueError("historical model summary physical-cell split contract is not the frozen 4-D contract")
    split_seed = _exact_json_int(arguments.get("split_seed"), "historical split_seed")
    partition_seed = _exact_json_int(
        (summary.get("split_audit") or {}).get("physical_cell_partition_seed"),
        "historical physical_cell_partition_seed",
    )
    if split_seed != partition_seed:
        raise ValueError("historical model summary split seed is internally inconsistent")
    for key in ("validation_fraction", "test_fraction"):
        try:
            comparison_value = _exact_json_number(
                split_contract[key], f"historical split {key}"
            )
            argument_value = _exact_json_number(
                arguments[key], f"historical argument {key}"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"historical model summary has invalid {key}") from exc
        if comparison_value != argument_value:
            raise ValueError(f"historical model summary {key} is internally inconsistent")
    architecture = comparison.get("architecture") or {}
    if type(architecture) is not dict or architecture != FROZEN_HISTORICAL_ARCHITECTURE:
        raise ValueError(
            "historical model summary does not prove the frozen 3x256 architecture identity"
        )
    historical_output_constraint = str(
        (summary.get("method") or {}).get("geometry_output_constraint") or ""
    )
    if historical_output_constraint != FROZEN_HISTORICAL_GEOMETRY_OUTPUT_CONSTRAINT:
        raise ValueError(
            "historical model summary geometry-output constraint identity mismatch"
        )
    seed = _exact_json_int(arguments.get("seed"), "historical seed")
    forward_updates = _exact_json_int(
        arguments.get("forward_max_optimizer_updates"),
        "historical forward_max_optimizer_updates",
    )
    inverse_updates = _exact_json_int(
        arguments.get("inverse_max_optimizer_updates"),
        "historical inverse_max_optimizer_updates",
    )
    batch_size = _exact_json_int(arguments.get("batch_size"), "historical batch_size")
    test_access_event_count = _exact_json_int(
        (summary.get("test_access_contract") or {}).get("test_access_event_count"),
        "historical test_access_event_count",
    )
    return {
        "trainer_implementation_sha256": str(
            comparison.get("trainer_implementation_sha256") or ""
        ),
        "model_comparison_contract_fingerprint_sha256": str(
            comparison.get("fingerprint_sha256") or ""
        ),
        "architecture": architecture,
        "historical_geometry_output_constraint": historical_output_constraint,
        "controlled_pair_geometry_output_constraint": (
            "independent_sigmoid_with_one_shared_declared_domain_envelope"
        ),
        "historical_decoder_reused_exactly": False,
        "historical_network_layer_architecture_reused_exactly": True,
        "seed": seed,
        "split_seed": split_seed,
        "forward_max_optimizer_updates": forward_updates,
        "inverse_max_optimizer_updates": inverse_updates,
        "batch_size": batch_size,
        "evaluation_mode": str(summary.get("evaluation_mode") or ""),
        "test_access_event_count": test_access_event_count,
    }


def _match_historical_rows_once(
    historical: list[dict[str, Any]], authoritative: list[dict[str, Any]]
) -> list[int]:
    by_key: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(authoritative):
        by_key[str(row["scientific_match_key"])].append(index)
    matched: list[int] = []
    used: set[int] = set()
    for historical_index, row in enumerate(historical):
        candidates = [
            index
            for index in by_key.get(str(row["scientific_match_key"]), [])
            if authoritative[index]["touchstone_sha256"] == row["touchstone_sha256"]
            and authoritative[index]["inputs"] == row["inputs"]
            and authoritative[index]["geometry"] == row["geometry"]
        ]
        if len(candidates) != 1:
            raise ValueError(
                "historical row does not have exactly one authoritative match: "
                f"historical_row={historical_index + 1} exact_matches={len(candidates)}"
            )
        if candidates[0] in used:
            raise ValueError("two historical rows resolve to the same authoritative source row")
        used.add(candidates[0])
        matched.append(candidates[0])
    return matched


def _rebuild_and_verify_historical_split(
    rows: list[dict[str, str]],
    summary: dict[str, Any],
    *,
    expected_counts: dict[str, int],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    arguments = summary.get("arguments") or {}
    expected_audit = summary.get("split_audit") or {}
    matrix = np.asarray(
        [[float(row[column]) for column in INPUT_COLUMNS] for row in rows], dtype=float
    )
    split, actual = split_physical_feature_indices(
        matrix,
        mode="physical_cell_grouped",
        seed=int(arguments.get("split_seed")),
        validation_fraction=float(arguments.get("validation_fraction")),
        test_fraction=float(arguments.get("test_fraction")),
        physical_cell_bins=int(arguments.get("physical_cell_bins")),
        physical_cell_lower=np.asarray(INPUT_LOWER, dtype=float),
        physical_cell_upper=np.asarray(INPUT_UPPER, dtype=float),
    )
    if actual["row_counts"] != expected_counts:
        raise ValueError(
            f"historical split row counts mismatch: expected={expected_counts} actual={actual['row_counts']}"
        )
    exact_keys = (
        "split_mode",
        "physical_cell_grouped",
        "physical_cell_partition_method",
        "physical_cell_partition_seed",
        "physical_cell_partition_stable_for_existing_cells",
        "physical_cell_bins_per_dimension",
        "physical_cell_range_source",
        "physical_cell_lower",
        "physical_cell_upper",
        "occupied_cell_count",
        "cell_counts",
        "row_counts",
        "physical_cell_overlap_count",
        "all_rows_assigned_once",
        "out_of_range_row_count_before_clipping",
        "cell_ids",
        "physical_cell_partition_fingerprint_sha256",
        "split_index_sha256",
        "split_fingerprint_sha256",
    )
    mismatches = [key for key in exact_keys if actual.get(key) != expected_audit.get(key)]
    if mismatches:
        raise ValueError(f"historical 4-D split does not exactly reproduce summary keys: {mismatches}")
    return split, actual


def _verify_historical_identity_sets(
    rows: list[dict[str, str]],
    split: dict[str, np.ndarray],
    summary: dict[str, Any],
) -> None:
    expected = (
        (summary.get("evaluation_isolation") or {}).get("geometry_identity_set_sha256")
        or {}
    )
    actual: dict[str, str] = {}
    for name in ("train", "validation", "test"):
        identities = sorted(
            rows[int(index)]["canonical_geometry_identity_sha256"]
            for index in split[name]
        )
        actual[name] = hashlib.sha256(
            "".join(f"{identity}\n" for identity in identities).encode("ascii")
        ).hexdigest()
    if actual != expected:
        raise ValueError(
            "historical geometry identity sets do not reproduce the model summary: "
            f"expected={expected} actual={actual}"
        )


def _assign_split(rows: list[dict[str, str]], split: dict[str, np.ndarray]) -> None:
    assigned: set[int] = set()
    for name in ("train", "validation", "test"):
        for raw_index in split[name]:
            index = int(raw_index)
            if index in assigned:
                raise RuntimeError("split assigns a historical row more than once")
            assigned.add(index)
            rows[index]["controlled_split_assignment"] = name
    if assigned != set(range(len(rows))):
        raise RuntimeError("split does not assign every historical row exactly once")


def _proportional_cell_quotas(
    counts: Counter[str], *, total: int, seed: int
) -> dict[str, int]:
    denominator = sum(counts.values())
    if denominator < 1 or total < 1:
        raise ValueError("proportional quota requires positive historical rows and total")
    ideal = {cell: total * count / denominator for cell, count in counts.items()}
    quotas = {cell: int(math.floor(value)) for cell, value in ideal.items()}
    remaining = total - sum(quotas.values())
    ranked = sorted(
        counts,
        key=lambda cell: (
            -(ideal[cell] - quotas[cell]),
            _stable_score(seed, "quota_tie", cell),
            cell,
        ),
    )
    for cell in ranked[:remaining]:
        quotas[cell] += 1
    if sum(quotas.values()) != total or set(quotas) != set(counts):
        raise RuntimeError("proportional cell quota allocation failed")
    return quotas


def _select_extra_rows(
    authoritative: list[dict[str, Any]],
    *,
    matched_source_indices: set[int],
    train_cells: set[str],
    quotas: dict[str, int],
    selection_seed: int,
    excluded_exact_geometry: set[str],
    excluded_portable_geometry: set[str],
    excluded_touchstone: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    exclusion_counts: Counter[str] = Counter()
    ranked: list[tuple[str, int, dict[str, Any]]] = []
    raw_candidates_by_cell: Counter[str] = Counter()
    for index, row in enumerate(authoritative):
        cell = str(row["cell"])
        if cell not in train_cells:
            exclusion_counts["not_historical_train_cell"] += 1
            continue
        if index in matched_source_indices:
            exclusion_counts["matched_historical_source_row"] += 1
            continue
        if row["canonical_geometry_identity_sha256"] in excluded_exact_geometry:
            exclusion_counts["historical_exact_geometry"] += 1
            continue
        if row["portable_geometry_decimal12_sha256"] in excluded_portable_geometry:
            exclusion_counts["historical_decimal12_geometry"] += 1
            continue
        if row["touchstone_sha256"] in excluded_touchstone:
            exclusion_counts["historical_touchstone"] += 1
            continue
        raw_candidates_by_cell[cell] += 1
        identity = (
            f"{cell}|{row['portable_geometry_decimal12_sha256']}|"
            f"{row['touchstone_sha256']}|{row['source_row_number']}"
        )
        ranked.append(
            (
                _stable_score(selection_seed, "extra_row", identity),
                int(row["source_row_number"]),
                row,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    selected: list[dict[str, Any]] = []
    selected_by_cell: Counter[str] = Counter()
    used_exact = set(excluded_exact_geometry)
    used_portable = set(excluded_portable_geometry)
    used_touchstone = set(excluded_touchstone)
    for _, _, row in ranked:
        cell = str(row["cell"])
        if selected_by_cell[cell] >= quotas[cell]:
            exclusion_counts["cell_quota_already_full"] += 1
            continue
        exact_identity = str(row["canonical_geometry_identity_sha256"])
        portable_identity = str(row["portable_geometry_decimal12_sha256"])
        touchstone_identity = str(row["touchstone_sha256"])
        if exact_identity in used_exact:
            exclusion_counts["duplicate_exact_geometry_candidate"] += 1
            continue
        if portable_identity in used_portable:
            exclusion_counts["duplicate_decimal12_geometry_candidate"] += 1
            continue
        if touchstone_identity in used_touchstone:
            exclusion_counts["duplicate_touchstone_candidate"] += 1
            continue
        used_exact.add(exact_identity)
        used_portable.add(portable_identity)
        used_touchstone.add(touchstone_identity)
        selected_by_cell[cell] += 1
        selected.append(row)
    shortfall = {
        cell: quotas[cell] - selected_by_cell[cell]
        for cell in sorted(quotas)
        if selected_by_cell[cell] != quotas[cell]
    }
    if shortfall:
        raise ValueError(
            "authoritative source lacks unique train-cell capacity for frozen quotas: "
            f"shortfall={shortfall} raw_candidates={dict(sorted(raw_candidates_by_cell.items()))}"
        )
    return selected, {
        "authoritative_rows_scanned": len(authoritative),
        "raw_candidates_by_train_cell": dict(sorted(raw_candidates_by_cell.items())),
        "selected_by_train_cell": dict(sorted(selected_by_cell.items())),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "selected_source_row_number_set_sha256": _line_set_sha(
            str(row["source_row_number"]) for row in selected
        ),
        "selected_geometry_identity_set_sha256": _line_set_sha(
            str(row["canonical_geometry_identity_sha256"]) for row in selected
        ),
        "selected_touchstone_identity_set_sha256": _line_set_sha(
            str(row["touchstone_sha256"]) for row in selected
        ),
    }


def _verify_nested_arms(
    arm10: list[dict[str, str]],
    arm20: list[dict[str, str]],
    *,
    extra_count: int,
    expected_train20: int,
    expected_validation: int,
    expected_test: int,
) -> None:
    if len(arm20) != len(arm10) + extra_count:
        raise RuntimeError("20K arm row count is not historical+extra")
    if arm20[: len(arm10)] != arm10:
        raise RuntimeError("historical arm is not an exact ordered row subset/prefix of the large arm")
    counts = Counter(row["controlled_split_assignment"] for row in arm20)
    if counts != {
        "train": expected_train20,
        "validation": expected_validation,
        "test": expected_test,
    }:
        raise RuntimeError(f"large arm split counts mismatch: {dict(counts)}")
    extra = arm20[len(arm10) :]
    if any(row["controlled_split_assignment"] != "train" for row in extra):
        raise RuntimeError("an appended row is not assigned to training")
    for identity_column in (
        "canonical_geometry_identity_sha256",
        "portable_geometry_decimal12_sha256",
        "touchstone_sha256",
    ):
        small_ids = {row[identity_column] for row in arm10}
        extra_ids = {row[identity_column] for row in extra}
        if small_ids & extra_ids or len(extra_ids) != len(extra):
            raise RuntimeError(f"nested arm identity isolation failed for {identity_column}")


def _build_holdout_manifest(
    rows: list[dict[str, str]],
    large_rows: list[dict[str, str]],
    split: dict[str, np.ndarray],
    split_audit: dict[str, Any],
    *,
    historical_summary_sha: str,
    shared_contract_sha: str,
) -> dict[str, Any]:
    identities = {
        name: sorted(
            rows[int(index)]["canonical_geometry_identity_sha256"]
            for index in split[name]
        )
        for name in ("validation", "test")
    }
    portable = {
        name: sorted(
            rows[int(index)]["portable_geometry_decimal12_sha256"]
            for index in split[name]
        )
        for name in ("validation", "test")
    }
    fingerprint_payload = "".join(
        [f"validation\0{value}\n" for value in identities["validation"]]
        + [f"test\0{value}\n" for value in identities["test"]]
    )
    cells = {
        name: sorted(
            {rows[int(index)]["controlled_physical_cell_4d"] for index in split[name]}
        )
        for name in ("train", "validation", "test")
    }
    expected_cells = {
        name: sorted(str(value) for value in split_audit["cell_ids"][name])
        for name in ("train", "validation", "test")
    }
    if cells != expected_cells:
        raise RuntimeError(
            f"holdout cell identities do not match the reconstructed split: {cells} != {expected_cells}"
        )
    cell_sets = {name: set(values) for name, values in cells.items()}
    overlap = (
        (cell_sets["train"] & cell_sets["validation"])
        | (cell_sets["train"] & cell_sets["test"])
        | (cell_sets["validation"] & cell_sets["test"])
    )
    if overlap:
        raise RuntimeError(f"historical complete-cell partitions overlap: {sorted(overlap)}")
    row_cell_split = {
        rows[int(index)]["controlled_physical_cell_4d"]: name
        for name in ("train", "validation", "test")
        for index in split[name]
    }
    if len(row_cell_split) != sum(len(values) for values in cell_sets.values()):
        raise RuntimeError("a historical physical cell is assigned to more than one split")
    if large_rows[: len(rows)] != rows:
        raise RuntimeError("large arm does not preserve the historical rows before holdout freezing")
    appended_rows = large_rows[len(rows) :]
    appended_cells = {
        row["controlled_physical_cell_4d"] for row in appended_rows
    }
    if not appended_cells <= cell_sets["train"]:
        raise RuntimeError("an appended row occupies a frozen validation or test cell")
    if any(row["controlled_split_assignment"] != "train" for row in appended_rows):
        raise RuntimeError("an appended row is not assigned to the frozen training partition")
    return {
        "schema": "fixed_common_holdout_geometry_identity_v1",
        "identity_kind": "canonical_geometry_sha256",
        "historical_model_summary_sha256": historical_summary_sha,
        "shared_contract_sha256": shared_contract_sha,
        "selection_method": "exact_historical_physical_cell_grouped_split_reconstruction",
        "selection_uses_model_results": False,
        "stratification": ["physical_cell_4d"],
        "physical_cell_encoding": PHYSICAL_CELL_ENCODING,
        "physical_cell_bins": PHYSICAL_CELL_BINS,
        "physical_lower": list(INPUT_LOWER),
        "physical_upper": list(INPUT_UPPER),
        "validation_count": len(identities["validation"]),
        "test_count": len(identities["test"]),
        "validation_geometry_identities": identities["validation"],
        "test_geometry_identities": identities["test"],
        "validation_portable_decimal12_geometry_identities": portable["validation"],
        "test_portable_decimal12_geometry_identities": portable["test"],
        "train_cell_ids": cells["train"],
        "validation_cell_ids": cells["validation"],
        "test_cell_ids": cells["test"],
        "physical_cell_partition_fingerprint_sha256": split_audit[
            "physical_cell_partition_fingerprint_sha256"
        ],
        "complete_cell_isolation": {
            "all_historical_rows_assigned_once": True,
            "every_cell_assigned_to_exactly_one_split": True,
            "train_validation_test_cell_overlap_count": 0,
            "appended_rows_restricted_to_train_cells": True,
            "appended_train_row_count": len(appended_rows),
            "appended_occupied_train_cell_count": len(appended_cells),
            "train_cell_count": len(cells["train"]),
            "validation_cell_count": len(cells["validation"]),
            "test_cell_count": len(cells["test"]),
        },
        "common_holdout_fingerprint_sha256": hashlib.sha256(
            fingerprint_payload.encode("ascii")
        ).hexdigest(),
        "boundary": (
            "This is the exact historical complete-cell validation/test holdout. Both nested arms use the "
            "same identities; every appended row is restricted to the historical train-cell list."
        ),
    }


def _normalization_contract() -> dict[str, Any]:
    return {
        "schema": "declared_midpoint_half_range_normalization_v1",
        "input_columns": list(INPUT_COLUMNS),
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "input_lower": list(INPUT_LOWER),
        "input_upper": list(INPUT_UPPER),
        "geometry_lower": list(GEOMETRY_LOWER),
        "geometry_upper": list(GEOMETRY_UPPER),
        "input_midpoint": [
            0.5 * (lower + upper) for lower, upper in zip(INPUT_LOWER, INPUT_UPPER)
        ],
        "input_half_range": [
            0.5 * (upper - lower) for lower, upper in zip(INPUT_LOWER, INPUT_UPPER)
        ],
        "geometry_midpoint": [
            0.5 * (lower + upper)
            for lower, upper in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)
        ],
        "geometry_half_range": [
            0.5 * (upper - lower)
            for lower, upper in zip(GEOMETRY_LOWER, GEOMETRY_UPPER)
        ],
        "train_arm_specific_statistics_used": False,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
        "boundary": (
            "Both arms use identical declared midpoint/half-range arrays and the identical sigmoid decoder "
            "envelope. No arm-specific empirical mean, variance, minimum, or maximum is used."
        ),
    }


def _output_row(row: dict[str, Any], *, origin: str) -> dict[str, str]:
    output = {
        "controlled_source_row_number": str(row["source_row_number"]),
        "controlled_origin": origin,
        "controlled_physical_cell_4d": str(row["cell"]),
        "controlled_split_assignment": "UNASSIGNED",
        "canonical_geometry_identity_sha256": str(
            row["canonical_geometry_identity_sha256"]
        ),
        "portable_geometry_decimal12_sha256": str(
            row["portable_geometry_decimal12_sha256"]
        ),
        "evaluation": str(row["evaluation"]),
        "touchstone_path": str(row["touchstone_path"]),
        "touchstone_sha256": str(row["touchstone_sha256"]),
    }
    for column, value in zip(INPUT_COLUMNS, row["inputs"]):
        output[column] = format(float(value), ".17g")
    for column, value in zip(GEOMETRY_COLUMNS, row["geometry"]):
        output[column] = format(float(value), ".17g")
    return output


def _finite_vector(
    row: dict[str, str],
    columns: Iterable[str],
    source_label: str,
    row_number: int,
) -> tuple[float, ...]:
    values: list[float] = []
    for column in columns:
        try:
            value = float(row.get(column, ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{source_label} row {row_number} has invalid numeric column {column}"
            ) from exc
        if not math.isfinite(value):
            raise ValueError(
                f"{source_label} row {row_number} has non-finite numeric column {column}"
            )
        values.append(value)
    return tuple(values)


def _require_inside(
    values: Iterable[float],
    lower: Iterable[float],
    upper: Iterable[float],
    source_label: str,
    row_number: int,
    kind: str,
) -> None:
    if not all(lo <= value <= hi for value, lo, hi in zip(values, lower, upper)):
        raise ValueError(
            f"{source_label} row {row_number} lies outside declared {kind} bounds"
        )


def _physical_cell(values: Iterable[float], bins: int) -> str:
    """Compatibility wrapper around the shared canonical cell encoder."""

    return canonical_physical_cell_id(values, bins=bins)


def _exact_geometry_identity(values: Iterable[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_float64_v1",
        "columns": list(GEOMETRY_COLUMNS),
        "values": [format(float(value), ".17g") for value in values],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _decimal12(value: float) -> str:
    token = format(float(value), ".12f")
    return "0.000000000000" if token == "-0.000000000000" else token


def _portable_geometry_identity(values: Iterable[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_decimal12_v1",
        "columns": list(GEOMETRY_COLUMNS),
        "values": [_decimal12(value) for value in values],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _scientific_match_key(row: dict[str, Any]) -> str:
    payload = {
        "schema": "historical_to_authoritative_real_emx_row_match_v1",
        "evaluation": str(row["evaluation"]),
        "input_columns": list(INPUT_COLUMNS),
        "inputs_decimal12": [_decimal12(value) for value in row["inputs"]],
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "geometry_decimal12": [_decimal12(value) for value in row["geometry"]],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _stable_score(seed: int, purpose: str, identity: str) -> str:
    return hashlib.sha256(
        f"{int(seed)}|{purpose}|{identity}".encode("ascii")
    ).hexdigest()


def _row_record_set_sha(rows: list[dict[str, str]]) -> str:
    records = [
        json.dumps(
            {column: row[column] for column in OUTPUT_COLUMNS},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        for row in rows
    ]
    return _line_set_sha(records)


def _line_set_sha(values: Iterable[str]) -> str:
    payload = "".join(f"{value}\n" for value in sorted(str(item) for item in values))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _artifact_map(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    return {
        path.name: {
            "path": str(path),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in paths
    }


def _implementation_identities(
    verified_context: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if verified_context is not None:
        return {
            output_role: {
                "path": verified_context[context_role]["logical_path"],
                "sha256": verified_context[context_role]["sha256"],
                "size_bytes": verified_context[context_role]["size_bytes"],
            }
            for output_role, context_role in {
                "builder": "materialization_builder_code",
                "shared_contract": "shared_contract_code",
                "splitter_source": "splitter_code",
            }.items()
        }
    builder_path = Path(__file__).resolve()
    shared_source = inspect.getsourcefile(controlled_contract)
    splitter_source = inspect.getsourcefile(model_splitting)
    if not shared_source or not splitter_source:
        raise RuntimeError("cannot resolve shared-contract or splitter source identity")
    paths = {
        "builder": builder_path,
        "shared_contract": Path(shared_source).resolve(),
        "splitter_source": Path(splitter_source).resolve(),
    }
    identities: dict[str, dict[str, Any]] = {}
    for role, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"implementation source is missing for {role}: {path}")
        identities[role] = {
            "path": str(path),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return identities


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError(f"duplicate/non-string JSON key: {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid strict JSON object: {label}") from exc
    if type(value) is not dict:
        raise ValueError(f"expected an exact JSON object: {label}")
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"invalid JSON object: {path}") from exc
    return _strict_json_object(raw, str(path))


def _load_json_object_bytes(raw: bytes, logical_path: Path) -> dict[str, Any]:
    return _strict_json_object(raw, str(logical_path))


def _validate_verified_context(
    args: argparse.Namespace, context: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Validate the gate-supplied immutable byte closure without reopening paths."""

    if not isinstance(context, Mapping) or set(context) != {"schema", "entries"}:
        raise ValueError("verified_context top-level keyset is not exact")
    if context.get("schema") != VERIFIED_CONTEXT_SCHEMA:
        raise ValueError("verified_context schema is invalid")
    raw_entries = context.get("entries")
    if not isinstance(raw_entries, Mapping) or set(raw_entries) != set(
        VERIFIED_CONTEXT_ROLES
    ):
        raise ValueError("verified_context role keyset is not exact")
    entries: dict[str, dict[str, Any]] = {}
    for role in VERIFIED_CONTEXT_ROLES:
        raw_record = raw_entries[role]
        if not isinstance(raw_record, Mapping) or set(raw_record) != VERIFIED_ENTRY_KEYS:
            raise ValueError(f"verified_context entry keyset is invalid: {role}")
        logical_path = raw_record.get("logical_path")
        payload = raw_record.get("bytes")
        digest = raw_record.get("sha256")
        mode = raw_record.get("mode_octal")
        if (
            not isinstance(logical_path, str)
            or not logical_path
            or "\x00" in logical_path
            or not Path(logical_path).is_absolute()
            or ".." in Path(logical_path).parts
            or type(payload) is not bytes
            or not isinstance(digest, str)
            or not _is_sha256(digest)
            or hashlib.sha256(payload).hexdigest() != digest
            or type(raw_record.get("size_bytes")) is not int
            or raw_record.get("size_bytes") != len(payload)
            or not isinstance(mode, str)
            or len(mode) != 4
            or any(character not in "01234567" for character in mode)
            or type(raw_record.get("nlink")) is not int
            or raw_record.get("nlink") != 1
            or type(raw_record.get("st_dev")) is not int
            or raw_record.get("st_dev") < 0
            or type(raw_record.get("st_ino")) is not int
            or raw_record.get("st_ino") <= 0
        ):
            raise ValueError(f"verified_context byte/stat identity is invalid: {role}")
        entries[role] = dict(raw_record)

    arg_bindings = {
        "historical_10k_csv": (
            args.historical_10k_csv,
            args.historical_10k_sha256,
        ),
        "authoritative_100k_csv": (
            args.authoritative_100k_csv,
            args.authoritative_100k_sha256,
        ),
        "historical_model_summary_json": (
            args.historical_model_summary_json,
            args.historical_model_summary_sha256,
        ),
    }
    for role, (logical_path, digest) in arg_bindings.items():
        if (
            entries[role]["logical_path"] != str(Path(logical_path).expanduser())
            or entries[role]["sha256"] != digest
        ):
            raise ValueError(f"verified_context does not exactly bind builder argv: {role}")

    module_bindings = (
        (
            "materialization_builder_code",
            sys.modules[__name__],
        ),
        ("shared_contract_code", controlled_contract),
        ("splitter_code", model_splitting),
    )
    for role, module in module_bindings:
        if (
            getattr(module, "__verified_snapshot_sha256__", None)
            != entries[role]["sha256"]
            or getattr(module, "__verified_snapshot_logical_path__", None)
            != entries[role]["logical_path"]
        ):
            raise ValueError(f"executed module is not the verified snapshot: {role}")
    if (
        canonical_physical_cell_id is not controlled_contract.canonical_physical_cell_id
        or split_physical_feature_indices
        is not model_splitting.split_physical_feature_indices
    ):
        raise ValueError("builder callable imports do not reference verified module objects")
    return entries


def _verified_consumption_record(
    verified_context: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if verified_context is None:
        return {
            "mode": "INDEPENDENT_CLI_PATH_MODE",
            "verified_context_schema": None,
            "exact_role_order": [],
            "role_sha256": {},
            "path_reopen_for_consumed_inputs": True,
        }
    return {
        "mode": "GATE_VERIFIED_HELD_BYTES_ONLY",
        "verified_context_schema": VERIFIED_CONTEXT_SCHEMA,
        "exact_role_order": list(VERIFIED_CONTEXT_ROLES),
        "role_sha256": {
            role: verified_context[role]["sha256"] for role in VERIFIED_CONTEXT_ROLES
        },
        "path_reopen_for_consumed_inputs": False,
    }


def _require_sha(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"{label} SHA-256 mismatch: expected={expected} actual={actual} path={path}"
        )
    return actual


def _write_csv_exclusive(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(OUTPUT_COLUMNS), extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        handle.write("\n")


def _write_sha_index_exclusive(path: Path, indexed_paths: list[Path]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for artifact in indexed_paths:
            handle.write(f"{_sha256_file(artifact)}  {artifact.name}\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
