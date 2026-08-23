#!/usr/bin/env python3
"""Benchmark nested real-EMX sample efficiency on one fixed physical-cell OOD split.

This experiment answers how much training data is enough for the current
physical-feature tandem baseline without changing the million-sample campaign.
Validation and test cells are frozen from the complete checkpoint table. Train
rows are selected as deterministic, cell-balanced nested prefixes, and each
prefix is trained with multiple model-initialization seeds.

The result is advisory. A plateau in surrogate OOD error never replaces DRC,
real EMX closure, sampled HFSS correlation, ten formal 100k checkpoints, or the
final one-million-sample uniformity gate.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for import_path in (SCRIPT_DIR, PROJECT_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from rfic_transformer_inverse_design.model_splitting import (  # noqa: E402
    parse_optional_feature_bounds,
    split_physical_feature_indices,
)

import train_physical_feature_tandem_inverse as tandem  # noqa: E402


DEFAULT_INPUT_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"

SAMPLE_COUNT_REFERENCE_ANCHORS = [
    {
        "reference_id": "pulserf_4000_total_2400_train",
        "benchmark_count": 2400,
        "paper_total_samples": 4000,
        "paper_training_samples": 2400,
        "task_boundary": "PulseRF trains a layout-image to broadband S-parameter forward surrogate, not the present physical-feature inverse model.",
    },
    {
        "reference_id": "tmtt_2026_3216_total_2400_train",
        "benchmark_count": 3216,
        "paper_total_samples": 3216,
        "paper_training_samples": 2400,
        "task_boundary": "The TMTT proof uses four geometry variables, six physical/frequency inputs, 51 points over 0-100 GHz, and a different inverse contract.",
    },
    {
        "reference_id": "pulserf_4000_total_count_anchor",
        "benchmark_count": 4000,
        "paper_total_samples": 4000,
        "paper_training_samples": 2400,
        "task_boundary": "This 4000-row benchmark point is a numerical total-dataset anchor only; it does not assert task equivalence.",
    },
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / "physical_feature_sample_efficiency_summary.json",
        "report": out_dir / "physical_feature_sample_efficiency_report.md",
        "records": out_dir / "physical_feature_sample_efficiency_records.csv",
        "plot": out_dir / "physical_feature_sample_efficiency.png",
    }
    rows, fields = _read_rows(training_csv)
    input_columns = _split_csv(args.input_columns)
    split_reference_columns = _split_csv(args.split_reference_columns or args.input_columns)
    geometry_columns = _geometry_columns(fields, args.geometry_columns)
    matrix, valid_rows = _matrix(rows, input_columns, split_reference_columns)
    checks: dict[str, bool] = {
        "training_csv_exists": training_csv.is_file(),
        "input_columns_present": bool(input_columns),
        "split_reference_columns_present": bool(split_reference_columns),
        "finite_usable_rows_meet_minimum": len(valid_rows) >= int(args.minimum_full_rows),
    }
    base = _base_payload(
        args,
        training_csv,
        input_columns,
        split_reference_columns,
        geometry_columns,
        len(rows),
        len(valid_rows),
        checks,
        paths,
    )
    if not all(checks.values()):
        base.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_FIRST_100K_TRAINING_TABLE",
                "failure_reasons": [name for name, passed in checks.items() if not passed],
                "records": [],
            }
        )
        _write_outputs(base, paths, [])
        return _finish(base, paths, bool(args.no_fail_exit))

    geometry_contract = _geometry_contract(valid_rows, geometry_columns)
    checks.update(
        {
            "geometry_column_count_exact": len(geometry_columns)
            == int(args.expected_geometry_columns),
            "finite_geometry_rows_cover_usable_rows": geometry_contract["finite_row_count"]
            == len(valid_rows),
            "independent_geometry_vectors_unique": (
                not bool(args.require_unique_geometry)
                or geometry_contract["unique_vector_count"] == len(valid_rows)
            ),
            "expected_source_row_count_matches": (
                int(args.expected_source_rows) <= 0
                or len(rows) == int(args.expected_source_rows)
            ),
            "required_training_csv_sha256_matches": (
                not args.require_training_csv_sha256
                or _file_sha(training_csv) == str(args.require_training_csv_sha256).lower()
            ),
        }
    )
    if not all(checks.values()):
        base["checks"] = checks
        base["geometry_contract"] = geometry_contract
        base.update(
            {
                "overall_status": "FAIL",
                "decision": "FIX_FIRST_100K_TABLE_IDENTITY_BEFORE_SAMPLE_EFFICIENCY_BENCHMARK",
                "failure_reasons": [name for name, passed in checks.items() if not passed],
                "records": [],
            }
        )
        _write_outputs(base, paths, [])
        return _finish(base, paths, bool(args.no_fail_exit))

    lower, upper = parse_optional_feature_bounds(
        args.physical_cell_lower,
        args.physical_cell_upper,
        matrix.shape[1],
    )
    try:
        split, split_audit = split_physical_feature_indices(
            matrix,
            mode="physical_cell_grouped",
            seed=int(args.split_seed),
            validation_fraction=float(args.validation_fraction),
            test_fraction=float(args.test_fraction),
            physical_cell_bins=int(args.physical_cell_bins),
            physical_cell_lower=lower,
            physical_cell_upper=upper,
        )
    except ValueError as exc:
        base.update(
            {
                "overall_status": "FAIL",
                "decision": "FIX_PHYSICAL_CELL_SPLIT_BEFORE_SAMPLE_EFFICIENCY_BENCHMARK",
                "failure_reasons": [f"{type(exc).__name__}: {exc}"],
                "records": [],
            }
        )
        _write_outputs(base, paths, [])
        return _finish(base, paths, bool(args.no_fail_exit))

    cell_matrix = _cell_matrix(matrix, lower, upper, int(args.physical_cell_bins))
    train_order = _nested_balanced_order(
        np.asarray(split["train"], dtype=int),
        cell_matrix,
        valid_rows,
        int(args.selection_seed),
    )
    requested_counts = _training_counts(args.training_counts)
    effective_counts = sorted({count for count in requested_counts if count <= len(train_order)} | {len(train_order)})
    model_seeds = _integer_list(args.model_seeds)
    checks.update(
        {
            "physical_cell_split_grouped": split_audit.get("split_mode") == "physical_cell_grouped",
            "physical_cell_overlap_zero": int(split_audit.get("physical_cell_overlap_count") or 0) == 0,
            "stable_cell_partition": split_audit.get("physical_cell_partition_stable_for_existing_cells") is True,
            "at_least_two_training_sizes": len(effective_counts) >= 2,
            "model_seeds_present": bool(model_seeds),
        }
    )
    if not all(checks.values()):
        base["checks"] = checks
        base.update(
            {
                "overall_status": "FAIL",
                "decision": "FIX_NESTED_BENCHMARK_CONTRACT",
                "failure_reasons": [name for name, passed in checks.items() if not passed],
                "records": [],
                "split_audit": split_audit,
            }
        )
        _write_outputs(base, paths, [])
        return _finish(base, paths, bool(args.no_fail_exit))

    fixed_validation = np.asarray(split["validation"], dtype=int)
    fixed_test = np.asarray(split["test"], dtype=int)
    fixed_validation_hash = _row_identity_digest(valid_rows, fixed_validation)
    fixed_test_hash = _row_identity_digest(valid_rows, fixed_test)
    records: list[dict[str, Any]] = []
    run_failures: list[dict[str, Any]] = []
    previous_train_set: set[int] = set()
    nested_checks: list[dict[str, Any]] = []
    for training_count in effective_counts:
        selected_train = np.asarray(train_order[:training_count], dtype=int)
        selected_set = set(int(value) for value in selected_train)
        nested = previous_train_set.issubset(selected_set)
        nested_checks.append(
            {
                "training_count": int(training_count),
                "contains_previous_training_prefix": nested,
                "training_identity_sha256": _row_identity_digest(valid_rows, selected_train),
            }
        )
        previous_train_set = selected_set
        subset_indices = np.concatenate([selected_train, fixed_validation, fixed_test])
        subset_rows = [valid_rows[int(index)] for index in subset_indices]
        subset_dir = out_dir / "subsets" / f"train_{training_count:06d}"
        subset_dir.mkdir(parents=True, exist_ok=True)
        subset_csv = subset_dir / "training_table.csv"
        _write_csv(subset_csv, fields, subset_rows)
        subset_contract = {
            "schema": "physical_feature_nested_sample_efficiency_subset_v1",
            "source_training_csv": str(training_csv),
            "source_sha256": _file_sha(training_csv),
            "selected_training_count": int(training_count),
            "validation_count": int(len(fixed_validation)),
            "test_count": int(len(fixed_test)),
            "training_identity_sha256": _row_identity_digest(valid_rows, selected_train),
            "validation_identity_sha256": fixed_validation_hash,
            "test_identity_sha256": fixed_test_hash,
            "physical_cell_partition_fingerprint_sha256": split_audit.get(
                "physical_cell_partition_fingerprint_sha256"
            ),
            "selection_seed": int(args.selection_seed),
            "split_seed": int(args.split_seed),
            "geometry_columns": geometry_columns,
        }
        subset_contract_path = subset_dir / "subset_contract.json"
        subset_contract_path.write_text(
            json.dumps(subset_contract, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        for model_seed in model_seeds:
            run_dir = subset_dir / f"model_seed_{model_seed}"
            run_dir.mkdir(parents=True, exist_ok=True)
            trainer_summary = run_dir / "physical_feature_tandem_inverse_summary.json"
            run_manifest = run_dir / "sample_efficiency_run_manifest.json"
            run_contract = {
                **subset_contract,
                "model_seed": int(model_seed),
                "trainer_settings": _trainer_settings(args),
            }
            run_contract_sha = _json_sha(run_contract)
            reused = False
            existing = _read_json(run_manifest)
            if (
                bool(args.resume)
                and trainer_summary.is_file()
                and existing.get("run_contract_sha256") == run_contract_sha
                and existing.get("trainer_returncode") == 0
            ):
                trainer_returncode = 0
                reused = True
            else:
                trainer_returncode = tandem.main(
                    _trainer_argv(
                        args,
                        subset_csv,
                        run_dir,
                        input_columns,
                        split_reference_columns,
                        geometry_columns,
                        len(subset_rows),
                        int(model_seed),
                    )
                )
                run_manifest.write_text(
                    json.dumps(
                        {
                            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "run_contract_sha256": run_contract_sha,
                            "run_contract": run_contract,
                            "trainer_returncode": int(trainer_returncode),
                            "trainer_summary": str(trainer_summary),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            summary = _read_json(trainer_summary)
            record = _result_record(
                summary,
                training_count,
                model_seed,
                trainer_returncode,
                reused,
                subset_contract,
                trainer_summary,
            )
            records.append(record)
            if record["run_status"] != "PASS":
                run_failures.append(record)
            gc.collect()

    aggregates = _aggregate(records, float(args.sufficiency_relative_tolerance))
    sufficiency_claim_eligible = (
        len(valid_rows) >= int(args.minimum_rows_for_sufficiency_claim)
        and len(model_seeds) >= int(args.minimum_model_seeds_for_sufficiency_claim)
        and int(args.minimum_full_rows) >= int(args.minimum_rows_for_sufficiency_claim)
    )
    fixed_holdout = all(
        record.get("validation_identity_sha256") == fixed_validation_hash
        and record.get("test_identity_sha256") == fixed_test_hash
        for record in records
    )
    all_nested = all(item["contains_previous_training_prefix"] for item in nested_checks)
    checks.update(
        {
            "all_training_prefixes_nested": all_nested,
            "fixed_validation_and_test_identity": fixed_holdout,
            "all_model_runs_passed_contract": not run_failures,
        }
    )
    overall_status = "PASS" if all(checks.values()) else "FAIL"
    candidate_count = (
        aggregates.get("smallest_training_count_within_tolerance")
        if sufficiency_claim_eligible
        else None
    )
    decision = (
        (
            "REPORT_SAMPLE_EFFICIENCY_AND_CONTINUE_MILLION_CAMPAIGN"
            if sufficiency_claim_eligible
            else "INTERFACE_SMOKE_ONLY_NO_SAMPLE_SUFFICIENCY_CLAIM"
        )
        if overall_status == "PASS"
        else "FIX_SAMPLE_EFFICIENCY_BENCHMARK_BEFORE_INTERPRETATION"
    )
    payload = _base_payload(
        args,
        training_csv,
        input_columns,
        split_reference_columns,
        geometry_columns,
        len(rows),
        len(valid_rows),
        checks,
        paths,
    )
    payload.update(
        {
            "overall_status": overall_status,
            "decision": decision,
            "split_audit": split_audit,
            "requested_training_counts": requested_counts,
            "effective_training_counts": effective_counts,
            "full_available_training_count": len(train_order),
            "fixed_validation_count": int(len(fixed_validation)),
            "fixed_test_count": int(len(fixed_test)),
            "fixed_validation_identity_sha256": fixed_validation_hash,
            "fixed_test_identity_sha256": fixed_test_hash,
            "nested_training_prefixes": nested_checks,
            "model_seeds": model_seeds,
            "records": records,
            "run_failures": run_failures,
            "geometry_contract": geometry_contract,
            "aggregates": aggregates,
            "sufficiency_claim_eligible": sufficiency_claim_eligible,
            "minimum_rows_for_sufficiency_claim": int(
                args.minimum_rows_for_sufficiency_claim
            ),
            "minimum_model_seeds_for_sufficiency_claim": int(
                args.minimum_model_seeds_for_sufficiency_claim
            ),
            "candidate_sufficient_training_count_for_real_emx_review": candidate_count,
            "candidate_sufficient_total_evidence_rows_for_real_emx_review": (
                aggregates.get("smallest_total_evidence_rows_within_tolerance")
                if sufficiency_claim_eligible
                else None
            ),
        }
    )
    _write_outputs(payload, paths, records)
    return _finish(payload, paths, bool(args.no_fail_exit))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-columns", default=DEFAULT_INPUT_COLUMNS)
    parser.add_argument("--split-reference-columns")
    parser.add_argument("--geometry-columns")
    parser.add_argument("--expected-geometry-columns", type=int, default=10)
    parser.add_argument("--expected-source-rows", type=int, default=0)
    parser.add_argument("--require-training-csv-sha256")
    parser.add_argument(
        "--require-unique-geometry",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--training-counts", default="2400,3216,4000,8000,16000,32000,64000")
    parser.add_argument("--model-seeds", default="20260711,20260712,20260713")
    parser.add_argument("--selection-seed", type=int, default=20260711)
    parser.add_argument("--split-seed", type=int, default=20260626)
    parser.add_argument("--minimum-full-rows", type=int, default=100000)
    parser.add_argument("--minimum-rows-for-sufficiency-claim", type=int, default=100000)
    parser.add_argument("--minimum-model-seeds-for-sufficiency-claim", type=int, default=3)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--test-fraction", type=float, default=0.10)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower", default="0.5,0.5,5,0")
    parser.add_argument("--physical-cell-upper", default="3,3,25,0.8")
    parser.add_argument("--forward-depth", type=int, default=2)
    parser.add_argument("--forward-width", type=int, default=128)
    parser.add_argument("--inverse-depth", type=int, default=2)
    parser.add_argument("--inverse-width", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--forward-epochs", type=int, default=80)
    parser.add_argument("--inverse-epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-6)
    parser.add_argument("--sufficiency-relative-tolerance", type=float, default=0.05)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.minimum_full_rows) < 4:
        parser.error("--minimum-full-rows must be at least 4")
    if int(args.minimum_rows_for_sufficiency_claim) < 4:
        parser.error("--minimum-rows-for-sufficiency-claim must be at least 4")
    if int(args.minimum_model_seeds_for_sufficiency_claim) < 3:
        parser.error("--minimum-model-seeds-for-sufficiency-claim must be at least 3")
    if int(args.physical_cell_bins) < 2:
        parser.error("--physical-cell-bins must be at least 2")
    if int(args.expected_geometry_columns) < 1 or int(args.expected_source_rows) < 0:
        parser.error("expected geometry/source row counts are invalid")
    if args.require_training_csv_sha256 and not _valid_sha256(args.require_training_csv_sha256):
        parser.error("--require-training-csv-sha256 must contain 64 hexadecimal digits")
    if not 0.0 <= float(args.sufficiency_relative_tolerance) < 1.0:
        parser.error("--sufficiency-relative-tolerance must be in [0,1)")
    return args


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _matrix(
    rows: list[dict[str, str]],
    input_columns: list[str],
    split_reference_columns: list[str],
) -> tuple[np.ndarray, list[dict[str, str]]]:
    values: list[list[float]] = []
    valid_rows: list[dict[str, str]] = []
    required = list(dict.fromkeys([*input_columns, *split_reference_columns]))
    for row in rows:
        parsed = [_finite(row.get(column)) for column in required]
        if any(value is None for value in parsed):
            continue
        split_values = [_finite(row.get(column)) for column in split_reference_columns]
        if any(value is None for value in split_values):
            continue
        values.append([float(value) for value in split_values if value is not None])
        valid_rows.append(row)
    if values:
        return np.asarray(values, dtype=float), valid_rows
    return np.empty((0, len(split_reference_columns)), dtype=float), valid_rows


def _geometry_columns(fields: list[str], explicit: str | None) -> list[str]:
    if explicit:
        return _split_csv(explicit)
    return sorted(column for column in fields if column.startswith("geom__"))


def _geometry_contract(
    rows: list[dict[str, str]],
    geometry_columns: list[str],
) -> dict[str, int]:
    vectors: list[tuple[float, ...]] = []
    for row in rows:
        values = [_finite(row.get(column)) for column in geometry_columns]
        if len(values) != len(geometry_columns) or any(value is None for value in values):
            continue
        vectors.append(tuple(float(value) for value in values if value is not None))
    unique_count = len(set(vectors))
    return {
        "geometry_column_count": len(geometry_columns),
        "finite_row_count": len(vectors),
        "unique_vector_count": unique_count,
        "duplicate_vector_count": len(vectors) - unique_count,
    }


def _cell_matrix(
    matrix: np.ndarray,
    lower: np.ndarray | None,
    upper: np.ndarray | None,
    bins: int,
) -> np.ndarray:
    lower_values = np.min(matrix, axis=0) if lower is None else np.asarray(lower, dtype=float)
    upper_values = np.max(matrix, axis=0) if upper is None else np.asarray(upper, dtype=float)
    scaled = (matrix - lower_values[None, :]) / (upper_values - lower_values)[None, :]
    return np.clip(np.floor(scaled * int(bins)).astype(int), 0, int(bins) - 1)


def _nested_balanced_order(
    train_indices: np.ndarray,
    cells: np.ndarray,
    rows: list[dict[str, str]],
    seed: int,
) -> list[int]:
    grouped: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for raw_index in train_indices:
        index = int(raw_index)
        grouped[tuple(int(value) for value in cells[index])].append(index)
    for cell, indices in grouped.items():
        indices.sort(key=lambda index: _stable_score(seed, cell, _row_identity(rows[index])))
    cell_order = sorted(grouped, key=lambda cell: _stable_score(seed, cell, "cell"))
    positions = {cell: 0 for cell in cell_order}
    order: list[int] = []
    while True:
        progressed = False
        for cell in cell_order:
            position = positions[cell]
            if position >= len(grouped[cell]):
                continue
            order.append(grouped[cell][position])
            positions[cell] += 1
            progressed = True
        if not progressed:
            break
    return order


def _stable_score(seed: int, cell: tuple[int, ...], token: str) -> str:
    raw = f"{int(seed)}|{':'.join(str(value) for value in cell)}|{token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _trainer_argv(
    args: argparse.Namespace,
    subset_csv: Path,
    run_dir: Path,
    input_columns: list[str],
    split_reference_columns: list[str],
    geometry_columns: list[str],
    minimum_rows: int,
    model_seed: int,
) -> list[str]:
    values = [
        "--training-csv",
        str(subset_csv),
        "--out-dir",
        str(run_dir),
        "--input-columns",
        ",".join(input_columns),
        "--split-reference-columns",
        ",".join(split_reference_columns),
        "--min-training-rows",
        str(int(minimum_rows)),
        "--validation-fraction",
        str(float(args.validation_fraction)),
        "--test-fraction",
        str(float(args.test_fraction)),
        "--split-mode",
        "physical_cell_grouped",
        "--physical-cell-bins",
        str(int(args.physical_cell_bins)),
        "--physical-cell-lower",
        str(args.physical_cell_lower),
        "--physical-cell-upper",
        str(args.physical_cell_upper),
        "--seed",
        str(int(model_seed)),
        "--split-seed",
        str(int(args.split_seed)),
        "--forward-depth",
        str(int(args.forward_depth)),
        "--forward-width",
        str(int(args.forward_width)),
        "--inverse-depth",
        str(int(args.inverse_depth)),
        "--inverse-width",
        str(int(args.inverse_width)),
        "--batch-size",
        str(int(args.batch_size)),
        "--forward-epochs",
        str(int(args.forward_epochs)),
        "--inverse-epochs",
        str(int(args.inverse_epochs)),
        "--patience",
        str(int(args.patience)),
        "--learning-rate",
        str(float(args.learning_rate)),
        "--weight-decay",
        str(float(args.weight_decay)),
        "--max-prediction-rows",
        "1000",
        "--robustness-repeats",
        "1",
        "--robustness-max-rows",
        "512",
        "--response-loss-scaling",
        "declared_range",
        "--response-weight-schedule",
        "warmup_ramp_adaptive_ema",
        "--response-warmup-fraction",
        "0.05",
        "--response-ramp-fraction",
        "0.20",
        "--no-fail-exit",
    ]
    values.extend(["--geometry-columns", ",".join(geometry_columns)])
    return values


def _trainer_settings(args: argparse.Namespace) -> dict[str, Any]:
    return {
        key: getattr(args, key)
        for key in (
            "forward_depth",
            "forward_width",
            "inverse_depth",
            "inverse_width",
            "batch_size",
            "forward_epochs",
            "inverse_epochs",
            "patience",
            "learning_rate",
            "weight_decay",
            "physical_cell_bins",
            "physical_cell_lower",
            "physical_cell_upper",
            "validation_fraction",
            "test_fraction",
            "split_seed",
        )
    }


def _result_record(
    summary: dict[str, Any],
    training_count: int,
    model_seed: int,
    returncode: int,
    reused: bool,
    subset_contract: dict[str, Any],
    summary_path: Path,
) -> dict[str, Any]:
    metrics = summary.get("metrics") or {}
    forward = metrics.get("forward_proxy") or {}
    inverse = metrics.get("tandem_inverse") or {}
    split_audit = summary.get("split_audit") or {}
    forward_metric = _finite(forward.get("test_range_normalized_rmse"))
    tandem_metric = _finite(inverse.get("test_response_range_normalized_rmse"))
    actual_train = int((split_audit.get("row_counts") or {}).get("train") or 0)
    passed = (
        int(returncode) == 0
        and summary.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"}
        and actual_train == int(training_count)
        and int(split_audit.get("physical_cell_overlap_count") or 0) == 0
        and forward_metric is not None
        and tandem_metric is not None
    )
    return {
        "run_status": "PASS" if passed else "FAIL",
        "training_count": int(training_count),
        "model_seed": int(model_seed),
        "reused": bool(reused),
        "trainer_returncode": int(returncode),
        "trainer_overall_status": summary.get("overall_status"),
        "actual_train_count": actual_train,
        "validation_count": int((split_audit.get("row_counts") or {}).get("validation") or 0),
        "test_count": int((split_audit.get("row_counts") or {}).get("test") or 0),
        "forward_proxy_test_range_normalized_rmse": forward_metric,
        "tandem_test_response_range_normalized_rmse": tandem_metric,
        "training_identity_sha256": subset_contract["training_identity_sha256"],
        "validation_identity_sha256": subset_contract["validation_identity_sha256"],
        "test_identity_sha256": subset_contract["test_identity_sha256"],
        "physical_cell_partition_fingerprint_sha256": split_audit.get(
            "physical_cell_partition_fingerprint_sha256"
        ),
        "trainer_summary": str(summary_path),
    }


def _aggregate(records: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("run_status") == "PASS":
            grouped[int(record["training_count"])].append(record)
    rows = []
    for count in sorted(grouped):
        tandem_values = np.asarray(
            [record["tandem_test_response_range_normalized_rmse"] for record in grouped[count]],
            dtype=float,
        )
        forward_values = np.asarray(
            [record["forward_proxy_test_range_normalized_rmse"] for record in grouped[count]],
            dtype=float,
        )
        tandem_stats = _seed_stats(tandem_values)
        forward_stats = _seed_stats(forward_values)
        rows.append(
            {
                "training_count": count,
                "fixed_evaluation_count": int(grouped[count][0]["validation_count"])
                + int(grouped[count][0]["test_count"]),
                "total_evidence_rows": count
                + int(grouped[count][0]["validation_count"])
                + int(grouped[count][0]["test_count"]),
                "seed_count": len(grouped[count]),
                "tandem": tandem_stats,
                "forward": forward_stats,
            }
        )
    smallest = None
    smallest_total = None
    if rows:
        full = rows[-1]["tandem"]
        full_mean = float(full["mean"])
        full_interval = (float(full["ci95_low"]), float(full["ci95_high"]))
        for row in rows:
            mean = float(row["tandem"]["mean"])
            interval = (
                float(row["tandem"]["ci95_low"]),
                float(row["tandem"]["ci95_high"]),
            )
            relative_gap = (mean - full_mean) / max(full_mean, 1.0e-12)
            overlap = max(interval[0], full_interval[0]) <= min(interval[1], full_interval[1])
            row["relative_gap_vs_full_train"] = float(relative_gap)
            row["ci95_overlaps_full_train"] = bool(overlap)
            row["within_sufficiency_tolerance"] = relative_gap <= float(tolerance) and overlap
            if smallest is None and row["within_sufficiency_tolerance"]:
                smallest = int(row["training_count"])
                smallest_total = int(row["total_evidence_rows"])
    return {
        "by_training_count": rows,
        "sufficiency_relative_tolerance": float(tolerance),
        "smallest_training_count_within_tolerance": smallest,
        "smallest_total_evidence_rows_within_tolerance": smallest_total,
        "boundary": (
            "The candidate training count excludes the fixed validation/test evidence rows. It is based on model-seed OOD overlap only, is not a total-simulation count or stopping rule, and must be checked with DRC and real EMX inverse closure."
        ),
    }


def _seed_stats(values: np.ndarray) -> dict[str, float | int]:
    count = int(len(values))
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if count > 1 else 0.0
    critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}.get(count, 1.96 if count > 1 else 0.0)
    half = float(critical * std / math.sqrt(count)) if count > 1 else 0.0
    return {
        "count": count,
        "mean": mean,
        "std": std,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "ci95_low": mean - half,
        "ci95_high": mean + half,
    }


def _base_payload(
    args: argparse.Namespace,
    training_csv: Path,
    input_columns: list[str],
    split_reference_columns: list[str],
    geometry_columns: list[str],
    source_rows: int,
    usable_rows: int,
    checks: dict[str, bool],
    paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(training_csv),
        "training_csv_sha256": _file_sha(training_csv),
        "source_row_count": int(source_rows),
        "usable_row_count": int(usable_rows),
        "input_columns": input_columns,
        "split_reference_columns": split_reference_columns,
        "geometry_columns": geometry_columns,
        "checks": checks,
        "arguments": vars(args),
        "sample_count_reference_anchors": SAMPLE_COUNT_REFERENCE_ANCHORS,
        "artifacts": {name: str(path) for name, path in paths.items()},
        "scientific_boundary": (
            "This is a fixed-OOD, multi-initialization sample-efficiency benchmark inside an already generated checkpoint. The x-axis counts training rows only; fixed validation/test rows must be added for a total evidence budget. It cannot stop the one-million campaign, replace formal 100k tests, or turn surrogate metrics into EM validation."
        ),
    }


def _write_outputs(payload: dict[str, Any], paths: dict[str, Path], records: list[dict[str, Any]]) -> None:
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_records(paths["records"], records)
    plot_status = "SKIPPED"
    if records and not bool((payload.get("arguments") or {}).get("no_plots")):
        plot_status = _write_plot(paths["plot"], payload)
    payload["artifacts"]["plot_status"] = plot_status
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["report"].write_text(_render_report(payload), encoding="utf-8")


def _write_plot(path: Path, payload: dict[str, Any]) -> str:
    rows = (payload.get("aggregates") or {}).get("by_training_count") or []
    if not rows:
        return "WAITING"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    counts = np.asarray([row["training_count"] for row in rows], dtype=float)
    figure, axis = plt.subplots(figsize=(8.2, 4.8), dpi=180, facecolor="white")
    for key, label, color, marker in (
        ("tandem", "Tandem response OOD RMSE", "#c43c35", "o"),
        ("forward", "Forward proxy OOD RMSE", "#2268b2", "s"),
    ):
        means = np.asarray([row[key]["mean"] for row in rows], dtype=float)
        stds = np.asarray([row[key]["std"] for row in rows], dtype=float)
        axis.plot(counts, means, marker=marker, color=color, linewidth=2.0, label=label)
        axis.fill_between(counts, means - stds, means + stds, color=color, alpha=0.15)
    axis.set_xscale("log", base=2)
    axis.set_xticks(counts)
    axis.set_xticklabels([_format_count(value) for value in counts])
    axis.set_xlabel("Nested training rows (fixed validation/test cells)")
    axis.set_ylabel("Fixed-range normalized RMSE")
    axis.set_title("Physical-feature sample-efficiency benchmark")
    axis.grid(True, alpha=0.25)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return "PASS"


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Physical-feature sample-efficiency benchmark",
        "",
        f"- Status: **{payload.get('overall_status')}**",
        f"- Decision: **{payload.get('decision')}**",
        f"- Usable checkpoint rows: `{payload.get('usable_row_count')}`",
        f"- Fixed validation rows: `{payload.get('fixed_validation_count', 0)}`",
        f"- Fixed test rows: `{payload.get('fixed_test_count', 0)}`",
        "",
        "| Nested train rows | Total evidence rows | Seeds | Tandem OOD RMSE mean | Std | Gap vs full | CI overlaps full |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in (payload.get("aggregates") or {}).get("by_training_count", []):
        gap = row.get("relative_gap_vs_full_train")
        lines.append(
            "| {} | {} | {} | {:.6g} | {:.6g} | {} | {} |".format(
                row["training_count"],
                row["total_evidence_rows"],
                row["seed_count"],
                row["tandem"]["mean"],
                row["tandem"]["std"],
                "-" if gap is None else f"{100.0 * float(gap):.2f}%",
                row.get("ci95_overlaps_full_train"),
            )
        )
    lines.extend(
        [
            "",
            "## Literature count anchors",
            "",
        ]
    )
    for anchor in payload.get("sample_count_reference_anchors") or []:
        lines.append(
            "- `{}` benchmark rows: paper total `{}`, paper train `{}`. {}".format(
                anchor.get("benchmark_count"),
                anchor.get("paper_total_samples"),
                anchor.get("paper_training_samples"),
                anchor.get("task_boundary"),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            str(payload.get("scientific_boundary")),
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _row_identity(row: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _row_identity_digest(rows: list[dict[str, str]], indices: np.ndarray) -> str:
    values = sorted(_row_identity(rows[int(index)]) for index in indices)
    return hashlib.sha256(("\n".join(values) + "\n").encode("ascii")).hexdigest()


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _training_counts(raw: str) -> list[int]:
    return sorted({value for value in _integer_list(raw) if value > 0})


def _integer_list(raw: str) -> list[int]:
    values = []
    for token in str(raw).split(","):
        token = token.strip()
        if token:
            values.append(int(token))
    return values


def _valid_sha256(value: Any) -> bool:
    text = str(value).strip()
    return len(text) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in text
    )


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _format_count(value: float) -> str:
    number = int(round(float(value)))
    if number >= 1_000_000:
        return f"{number / 1_000_000:g}M"
    if number >= 1_000:
        return f"{number / 1_000:g}k"
    return str(number)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finish(payload: dict[str, Any], paths: dict[str, Path], no_fail_exit: bool) -> int:
    print(f"overall_status={payload.get('overall_status')}")
    print(f"decision={payload.get('decision')}")
    print(f"summary={paths['summary']}")
    print(f"report={paths['report']}")
    if payload.get("overall_status") in {"PASS", "WAITING"}:
        return 0 if payload.get("overall_status") == "PASS" or no_fail_exit else 2
    return 0 if no_fail_exit else 2


if __name__ == "__main__":
    raise SystemExit(main())
