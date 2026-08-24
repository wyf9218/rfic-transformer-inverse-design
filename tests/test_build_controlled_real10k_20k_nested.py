from __future__ import annotations

import csv
import hashlib
import importlib.util
import inspect
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from rfic_transformer_inverse_design.model_splitting import (
    split_physical_feature_indices,
)
import rfic_transformer_inverse_design.controlled_real10k_20k_contract as SHARED_CONTRACT
import rfic_transformer_inverse_design.model_splitting as MODEL_SPLITTING


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_controlled_real10k_20k_nested.py"
)
SPEC = importlib.util.spec_from_file_location("controlled_real10k_20k_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_entry(path: Path) -> dict[str, object]:
    logical = path.resolve()
    payload = logical.read_bytes()
    metadata = logical.stat()
    return {
        "logical_path": str(logical),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "mode_octal": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "nlink": metadata.st_nlink,
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "bytes": payload,
    }


def _geometry_identity(values: list[float]) -> str:
    payload = {
        "schema": "ordered_inverse_geometry_float64_v1",
        "columns": list(BUILDER.GEOMETRY_COLUMNS),
        "values": [format(float(value), ".17g") for value in values],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "row_index",
        "evaluation",
        "touchstone_path",
        "touchstone_sha256",
        *BUILDER.INPUT_COLUMNS,
        *BUILDER.GEOMETRY_COLUMNS,
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def test_shared_contract_freezes_colon_cell_encoding_bounds_and_seeds() -> None:
    assert SHARED_CONTRACT.canonical_physical_cell_id([0.5, 0.5, 5.0, 0.0]) == "0:0:0:0"
    assert SHARED_CONTRACT.canonical_physical_cell_id([1.75, 1.75, 15.0, 0.4]) == "2:2:2:2"
    assert SHARED_CONTRACT.canonical_physical_cell_id([3.0, 3.0, 25.0, 0.8]) == "3:3:3:3"
    assert SHARED_CONTRACT.PHYSICAL_CELL_ENCODING == (
        "colon_separated_zero_based_bin_indices_v1"
    )
    assert SHARED_CONTRACT.EXACT_PAIRED_SEEDS == (20260711, 20260712, 20260713)
    assert SHARED_CONTRACT.EXACT_EXTRA_SELECTION_SEED == 20260824
    assert BUILDER.OUTPUT_COLUMNS == SHARED_CONTRACT.OUTPUT_COLUMNS
    with pytest.raises(ValueError, match="outside"):
        SHARED_CONTRACT.canonical_physical_cell_id([0.49, 0.5, 5.0, 0.0])
    with pytest.raises(ValueError, match="bin count"):
        SHARED_CONTRACT.canonical_physical_cell_id([0.5, 0.5, 5.0, 0.0], bins=5)


def test_builder_strict_json_rejects_duplicate_nonfinite_and_bool_int_alias() -> None:
    with pytest.raises(ValueError, match="strict JSON"):
        BUILDER._strict_json_object(b'{"a":1,"a":2}', "duplicate fixture")
    with pytest.raises(ValueError, match="strict JSON"):
        BUILDER._strict_json_object(b'{"a":NaN}', "nonfinite fixture")
    with pytest.raises(ValueError, match="exact JSON integer"):
        BUILDER._exact_json_int(False, "bool alias")


def _fixture(tmp_path: Path) -> dict[str, Any]:
    historical_path = tmp_path / "historical10k.csv"
    authoritative_path = tmp_path / "authoritative100k.csv"
    summary_path = tmp_path / "historical_summary.json"
    cell_coordinates = [
        (0, 0, 0, 0),
        (0, 1, 1, 1),
        (0, 2, 2, 2),
        (0, 3, 3, 3),
        (1, 0, 1, 2),
        (1, 1, 2, 3),
        (1, 2, 3, 0),
        (2, 0, 2, 1),
        (2, 1, 3, 2),
        (2, 2, 0, 3),
        (3, 0, 3, 1),
        (3, 3, 1, 2),
    ]

    def feature_values(cell: tuple[int, int, int, int]) -> list[float]:
        return [
            lower + (index + 0.5) * (upper - lower) / 4.0
            for index, lower, upper in zip(
                cell, BUILDER.INPUT_LOWER, BUILDER.INPUT_UPPER
            )
        ]

    def geometry_values(global_index: int) -> list[float]:
        return [
            160.0 + 0.5 * global_index,
            170.0 + 0.5 * global_index,
            180.0 + 0.5 * global_index,
            190.0 + 0.5 * global_index,
            3.0 + 0.05 * (global_index % 160),
            20.0 + 0.5 * (global_index % 120),
            21.0 + 0.5 * (global_index % 120),
            -90.0 + float(global_index % 180),
            100.0 + float(global_index % 220),
            101.0 + float(global_index % 219),
        ]

    authoritative_rows: list[dict[str, Any]] = []
    historical_rows: list[dict[str, Any]] = []
    for cell_index, cell in enumerate(cell_coordinates):
        inputs = feature_values(cell)
        for within_cell in range(10):
            global_index = cell_index * 10 + within_cell
            geometry = geometry_values(global_index)
            row: dict[str, Any] = {
                "row_index": global_index,
                "evaluation": f"real-emx-{global_index:05d}",
                "touchstone_path": f"/mars/real-emx-{global_index:05d}/emx.s4p",
                "touchstone_sha256": hashlib.sha256(
                    f"touchstone-{global_index}".encode("ascii")
                ).hexdigest(),
            }
            row.update(dict(zip(BUILDER.INPUT_COLUMNS, inputs)))
            row.update(dict(zip(BUILDER.GEOMETRY_COLUMNS, geometry)))
            authoritative_rows.append(row)
            if within_cell < 2:
                historical_rows.append(dict(row))
    _write_csv(authoritative_path, authoritative_rows)
    _write_csv(historical_path, historical_rows)

    split_seed = 20260711
    validation_fraction = 0.20
    test_fraction = 0.20
    x = np.asarray(
        [[float(row[column]) for column in BUILDER.INPUT_COLUMNS] for row in historical_rows]
    )
    split, split_audit = split_physical_feature_indices(
        x,
        mode="physical_cell_grouped",
        seed=split_seed,
        validation_fraction=validation_fraction,
        test_fraction=test_fraction,
        physical_cell_bins=4,
        physical_cell_lower=np.asarray(BUILDER.INPUT_LOWER),
        physical_cell_upper=np.asarray(BUILDER.INPUT_UPPER),
    )
    identity_sets: dict[str, str] = {}
    identities = [
        _geometry_identity(
            [float(row[column]) for column in BUILDER.GEOMETRY_COLUMNS]
        )
        for row in historical_rows
    ]
    for name in ("train", "validation", "test"):
        payload = "".join(
            f"{identity}\n" for identity in sorted(identities[int(index)] for index in split[name])
        )
        identity_sets[name] = hashlib.sha256(payload.encode("ascii")).hexdigest()
    summary = {
        "training_csv_sha256": _sha256(historical_path),
        "training_count": len(historical_rows),
        "input_columns": list(BUILDER.INPUT_COLUMNS),
        "geometry_columns": list(BUILDER.GEOMETRY_COLUMNS),
        "model_comparison_contract": {
            "trainer_implementation_sha256": "1" * 64,
            "fingerprint_sha256": "2" * 64,
            "architecture": json.loads(
                json.dumps(BUILDER.FROZEN_HISTORICAL_ARCHITECTURE)
            ),
            "split": {
                "mode": "physical_cell_grouped",
                "validation_fraction": validation_fraction,
                "test_fraction": test_fraction,
                "physical_cell_bins": 4,
                "physical_cell_lower": "0.5,0.5,5.0,0.0",
                "physical_cell_upper": "3.0,3.0,25.0,0.8",
            },
        },
        "arguments": {
            "seed": split_seed,
            "split_seed": split_seed,
            "validation_fraction": validation_fraction,
            "test_fraction": test_fraction,
            "physical_cell_bins": 4,
            "forward_max_optimizer_updates": 1200,
            "inverse_max_optimizer_updates": 1200,
            "batch_size": 1024,
        },
        "method": {
            "geometry_output_constraint": "sigmoid_projection_to_observed_training_envelope"
        },
        "evaluation_mode": "validation_only",
        "test_access_contract": {"test_access_event_count": 0},
        "split_audit": split_audit,
        "evaluation_isolation": {"geometry_identity_set_sha256": identity_sets},
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "historical_path": historical_path,
        "authoritative_path": authoritative_path,
        "summary_path": summary_path,
        "historical_rows": historical_rows,
        "authoritative_rows": authoritative_rows,
        "split": split,
        "split_audit": split_audit,
        "extra_count": 12,
    }


def _arguments(fixture: dict[str, Any], out_dir: Path) -> list[str]:
    split = fixture["split"]
    return [
        "--historical-10k-csv",
        str(fixture["historical_path"]),
        "--historical-10k-sha256",
        _sha256(fixture["historical_path"]),
        "--authoritative-100k-csv",
        str(fixture["authoritative_path"]),
        "--authoritative-100k-sha256",
        _sha256(fixture["authoritative_path"]),
        "--historical-model-summary-json",
        str(fixture["summary_path"]),
        "--historical-model-summary-sha256",
        _sha256(fixture["summary_path"]),
        "--out-dir",
        str(out_dir),
        "--extra-count",
        str(fixture["extra_count"]),
        "--selection-seed",
        "2026082401",
        "--expected-historical-rows",
        str(len(fixture["historical_rows"])),
        "--expected-authoritative-rows",
        str(len(fixture["authoritative_rows"])),
        "--expected-train-rows",
        str(len(split["train"])),
        "--expected-validation-rows",
        str(len(split["validation"])),
        "--expected-test-rows",
        str(len(split["test"])),
    ]


def test_historical_summary_proves_architecture_and_records_decoder_boundary(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    summary = BUILDER._load_json_object(fixture["summary_path"])
    contract = BUILDER._validate_summary_contract(
        summary,
        historical_sha=_sha256(fixture["historical_path"]),
        expected_rows=len(fixture["historical_rows"]),
    )
    assert contract["architecture"] == BUILDER.FROZEN_HISTORICAL_ARCHITECTURE
    assert contract["historical_network_layer_architecture_reused_exactly"] is True
    assert contract["historical_decoder_reused_exactly"] is False
    assert contract["historical_geometry_output_constraint"] == (
        "sigmoid_projection_to_observed_training_envelope"
    )
    assert contract["controlled_pair_geometry_output_constraint"] == (
        "independent_sigmoid_with_one_shared_declared_domain_envelope"
    )

    summary["model_comparison_contract"]["architecture"]["inverse_width"] = 128
    with pytest.raises(ValueError, match="3x256 architecture"):
        BUILDER._validate_summary_contract(
            summary,
            historical_sha=_sha256(fixture["historical_path"]),
            expected_rows=len(fixture["historical_rows"]),
        )


def test_builds_deterministic_exact_nested_arms_and_frozen_contracts(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    assert BUILDER.main(_arguments(fixture, first_dir)) == 0
    assert BUILDER.main(_arguments(fixture, second_dir)) == 0

    arm10 = _read_csv(first_dir / "arm_source_n10000.csv")
    arm20 = _read_csv(first_dir / "arm_source_n20000.csv")
    assert arm20[: len(arm10)] == arm10
    assert len(arm20) == len(arm10) + fixture["extra_count"]
    assert _sha256(first_dir / "arm_source_n10000.csv") == _sha256(
        second_dir / "arm_source_n10000.csv"
    )
    assert _sha256(first_dir / "arm_source_n20000.csv") == _sha256(
        second_dir / "arm_source_n20000.csv"
    )

    extras = arm20[len(arm10) :]
    train_cells = {
        row["controlled_physical_cell_4d"]
        for row in arm10
        if row["controlled_split_assignment"] == "train"
    }
    assert {row["controlled_physical_cell_4d"] for row in extras} <= train_cells
    assert {row["controlled_split_assignment"] for row in extras} == {"train"}
    for identity_column in (
        "canonical_geometry_identity_sha256",
        "portable_geometry_decimal12_sha256",
        "touchstone_sha256",
    ):
        historical_ids = {row[identity_column] for row in arm10}
        extra_ids = {row[identity_column] for row in extras}
        assert historical_ids.isdisjoint(extra_ids)
        assert len(extra_ids) == len(extras)

    summary = json.loads(
        (first_dir / "controlled_real10k_20k_nested_summary.json").read_text()
    )
    assert summary["schema"] == "controlled_real10k_20k_nested_materialization_v2"
    assert summary["status"] == "PASS"
    assert summary["decision"] == "TEST_OR_NONPRODUCTION_FIXTURE_ONLY"
    assert summary["arm_counts"]["n10000"]["gradient_train_rows"] == len(
        fixture["split"]["train"]
    )
    assert summary["arm_counts"]["n20000"]["gradient_train_rows"] == len(
        fixture["split"]["train"]
    ) + fixture["extra_count"]
    assert summary["nested_identity_contract"][
        "arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000"
    ] is True
    assert sum(summary["selection_contract"]["extra_cell_quotas"].values()) == fixture[
        "extra_count"
    ]
    assert summary["selection_contract"]["extra_cell_quotas"] == summary[
        "selection_contract"
    ]["candidate_audit"]["selected_by_train_cell"]
    assert summary["shared_contract"] == {
        "physical_cell_encoding": "colon_separated_zero_based_bin_indices_v1",
        "physical_cell_bins": 4,
        "extra_selection_seed": 20260824,
        "paired_seeds": [20260711, 20260712, 20260713],
    }
    # The fixture deliberately uses 2026082401.  A non-preregistered selection
    # seed may exercise mechanics, but it can never receive production status.
    assert summary["selection_contract"]["selection_seed"] == 2026082401
    assert summary["production_exact_checks"]["selection_seed_exact_20260824"] is False
    assert summary["decision"] == "TEST_OR_NONPRODUCTION_FIXTURE_ONLY"
    expected_implementation_paths = {
        "builder": SCRIPT.resolve(),
        "shared_contract": Path(inspect.getsourcefile(SHARED_CONTRACT) or "").resolve(),
        "splitter_source": Path(inspect.getsourcefile(MODEL_SPLITTING) or "").resolve(),
    }
    for role, expected_path in expected_implementation_paths.items():
        record = summary["implementation_identities"][role]
        assert Path(record["path"]) == expected_path
        assert record["sha256"] == _sha256(expected_path)
        assert record["size_bytes"] == expected_path.stat().st_size

    holdout = json.loads((first_dir / "fixed_common_holdout_manifest.json").read_text())
    assert holdout["schema"] == "fixed_common_holdout_geometry_identity_v1"
    assert holdout["historical_model_summary_sha256"] == _sha256(fixture["summary_path"])
    assert holdout["shared_contract_sha256"] == _sha256(
        expected_implementation_paths["shared_contract"]
    )
    assert holdout["selection_uses_model_results"] is False
    assert holdout["physical_cell_encoding"] == "colon_separated_zero_based_bin_indices_v1"
    assert holdout["physical_cell_bins"] == 4
    assert holdout["physical_lower"] == list(BUILDER.INPUT_LOWER)
    assert holdout["physical_upper"] == list(BUILDER.INPUT_UPPER)
    assert holdout["validation_count"] == len(fixture["split"]["validation"])
    assert holdout["test_count"] == len(fixture["split"]["test"])
    validation_ids = {
        row["canonical_geometry_identity_sha256"]
        for row in arm10
        if row["controlled_split_assignment"] == "validation"
    }
    test_ids = {
        row["canonical_geometry_identity_sha256"]
        for row in arm10
        if row["controlled_split_assignment"] == "test"
    }
    assert set(holdout["validation_geometry_identities"]) == validation_ids
    assert set(holdout["test_geometry_identities"]) == test_ids
    assert holdout["train_cell_ids"] == fixture["split_audit"]["cell_ids"]["train"]
    assert holdout["validation_cell_ids"] == fixture["split_audit"]["cell_ids"][
        "validation"
    ]
    assert holdout["test_cell_ids"] == fixture["split_audit"]["cell_ids"]["test"]
    assert holdout["physical_cell_partition_fingerprint_sha256"] == fixture[
        "split_audit"
    ]["physical_cell_partition_fingerprint_sha256"]
    assert holdout["complete_cell_isolation"] == {
        "all_historical_rows_assigned_once": True,
        "every_cell_assigned_to_exactly_one_split": True,
        "train_validation_test_cell_overlap_count": 0,
        "appended_rows_restricted_to_train_cells": True,
        "appended_train_row_count": fixture["extra_count"],
        "appended_occupied_train_cell_count": len(
            {row["controlled_physical_cell_4d"] for row in extras}
        ),
        "train_cell_count": len(fixture["split_audit"]["cell_ids"]["train"]),
        "validation_cell_count": len(fixture["split_audit"]["cell_ids"]["validation"]),
        "test_cell_count": len(fixture["split_audit"]["cell_ids"]["test"]),
    }
    assert len(holdout["common_holdout_fingerprint_sha256"]) == 64

    normalization = json.loads(
        (first_dir / "declared_midpoint_half_range_normalization_contract.json").read_text()
    )
    assert normalization["schema"] == "declared_midpoint_half_range_normalization_v1"
    assert normalization["input_lower"] == list(BUILDER.INPUT_LOWER)
    assert normalization["input_upper"] == list(BUILDER.INPUT_UPPER)
    assert normalization["geometry_lower"] == list(BUILDER.GEOMETRY_LOWER)
    assert normalization["geometry_upper"] == list(BUILDER.GEOMETRY_UPPER)
    assert normalization["train_arm_specific_statistics_used"] is False
    assert normalization["large_arm_empirical_statistics_used"] is False
    assert normalization["input_midpoint"] == [
        0.5 * (lower + upper)
        for lower, upper in zip(BUILDER.INPUT_LOWER, BUILDER.INPUT_UPPER)
    ]
    assert normalization["input_half_range"] == [
        0.5 * (upper - lower)
        for lower, upper in zip(BUILDER.INPUT_LOWER, BUILDER.INPUT_UPPER)
    ]
    assert normalization["geometry_midpoint"] == [
        0.5 * (lower + upper)
        for lower, upper in zip(BUILDER.GEOMETRY_LOWER, BUILDER.GEOMETRY_UPPER)
    ]
    assert normalization["geometry_half_range"] == [
        0.5 * (upper - lower)
        for lower, upper in zip(BUILDER.GEOMETRY_LOWER, BUILDER.GEOMETRY_UPPER)
    ]
    assert set(normalization) == {
        "schema",
        "input_columns",
        "geometry_columns",
        "input_lower",
        "input_upper",
        "geometry_lower",
        "geometry_upper",
        "input_midpoint",
        "input_half_range",
        "geometry_midpoint",
        "geometry_half_range",
        "train_arm_specific_statistics_used",
        "large_arm_empirical_statistics_used",
        "all_loaded_rows_required_inside_declared_bounds",
        "boundary",
    }

    qa_required_path = first_dir / "INDEPENDENT_QA_REQUIRED.json"
    qa_required = json.loads(qa_required_path.read_text())
    assert qa_required["status"] == "INDEPENDENT_QA_REQUIRED"
    assert qa_required["verdict"] == "NO_GO_PENDING_FRESH_INDEPENDENT_QA"
    assert qa_required["training_authorized"] is False
    assert qa_required["result_access_authorized"] is False
    assert qa_required["fresh_emx_authorized"] is False
    assert qa_required["frozen_scientific_contract"]["extra_selection_seed"] == 20260824
    assert qa_required["verdict"] == "NO_GO_PENDING_FRESH_INDEPENDENT_QA"
    assert qa_required["materialization_summary"]["sha256"] == _sha256(
        first_dir / "controlled_real10k_20k_nested_summary.json"
    )
    assert qa_required["implementation_identities"] == summary["implementation_identities"]
    assert set(qa_required["frozen_artifacts"]) == {
        "arm_source_n10000.csv",
        "arm_source_n20000.csv",
        "fixed_common_holdout_manifest.json",
        "declared_midpoint_half_range_normalization_contract.json",
    }
    for name, record in qa_required["frozen_artifacts"].items():
        assert record["sha256"] == _sha256(first_dir / name)

    receipt = json.loads(
        (first_dir / "controlled_real10k_20k_nested_receipt.json").read_text()
    )
    assert receipt["schema"] == "controlled_real10k_20k_nested_materialization_receipt_v2"
    assert receipt["status"] == "PASS"
    assert receipt["training_launch_authorized"] is False
    assert receipt["independent_qa_required"] is True
    assert receipt["implementation_identities"] == summary["implementation_identities"]
    assert receipt["independent_qa_required_record"]["sha256"] == _sha256(qa_required_path)
    assert set(receipt["artifact_identities"]) == {
        "arm_source_n10000.csv",
        "arm_source_n20000.csv",
        "fixed_common_holdout_manifest.json",
        "declared_midpoint_half_range_normalization_contract.json",
        "controlled_real10k_20k_nested_summary.json",
        "INDEPENDENT_QA_REQUIRED.json",
    }
    for name, record in receipt["artifact_identities"].items():
        assert record["sha256"] == _sha256(first_dir / name)
    expected_index_names = [
        "arm_source_n10000.csv",
        "arm_source_n20000.csv",
        "fixed_common_holdout_manifest.json",
        "declared_midpoint_half_range_normalization_contract.json",
        "controlled_real10k_20k_nested_summary.json",
        "INDEPENDENT_QA_REQUIRED.json",
        "controlled_real10k_20k_nested_receipt.json",
    ]
    assert receipt["sha256_closure_contract"]["exact_filenames_in_order"] == expected_index_names
    assert receipt["sha256_closure_contract"]["exact_entry_count"] == 7
    indexed = (first_dir / "SHA256SUMS.txt").read_text().splitlines()
    assert len(indexed) == 7
    assert [line.split("  ", 1)[1] for line in indexed] == expected_index_names
    for line in indexed:
        expected, name = line.split("  ", 1)
        assert _sha256(first_dir / name) == expected
    assert sorted(path.name for path in first_dir.iterdir()) == sorted(
        [*expected_index_names, "SHA256SUMS.txt"]
    )


def test_verified_context_consumes_frozen_source_bytes_after_path_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    baseline = tmp_path / "baseline"
    assert BUILDER.main(_arguments(fixture, baseline)) == 0

    shared_path = Path(inspect.getsourcefile(SHARED_CONTRACT) or "").resolve()
    splitter_path = Path(inspect.getsourcefile(MODEL_SPLITTING) or "").resolve()
    entries = {
        "materialization_builder_code": _verified_entry(SCRIPT),
        "shared_contract_code": _verified_entry(shared_path),
        "splitter_code": _verified_entry(splitter_path),
        "historical_10k_csv": _verified_entry(fixture["historical_path"]),
        "authoritative_100k_csv": _verified_entry(fixture["authoritative_path"]),
        "historical_model_summary_json": _verified_entry(fixture["summary_path"]),
    }
    context = {
        "schema": BUILDER.VERIFIED_CONTEXT_SCHEMA,
        "entries": entries,
    }
    verified_out = tmp_path / "verified"
    verified_argv = _arguments(fixture, verified_out)
    monkeypatch.setitem(sys.modules, BUILDER.__name__, BUILDER)
    for module, role in (
        (BUILDER, "materialization_builder_code"),
        (SHARED_CONTRACT, "shared_contract_code"),
        (MODEL_SPLITTING, "splitter_code"),
    ):
        monkeypatch.setattr(
            module,
            "__verified_snapshot_sha256__",
            entries[role]["sha256"],
            raising=False,
        )
        monkeypatch.setattr(
            module,
            "__verified_snapshot_logical_path__",
            entries[role]["logical_path"],
            raising=False,
        )

    backups: list[tuple[Path, Path]] = []
    for role, fixture_key in (
        ("historical_10k_csv", "historical_path"),
        ("authoritative_100k_csv", "authoritative_path"),
        ("historical_model_summary_json", "summary_path"),
    ):
        path = Path(fixture[fixture_key])
        backup = path.with_suffix(path.suffix + ".held-original")
        os.replace(path, backup)
        path.write_bytes(b"SUBSTITUTED_UNVERIFIED_SOURCE_BYTES\n")
        backups.append((path, backup))
        assert _sha256(path) != entries[role]["sha256"]

    try:
        assert (
            BUILDER.main(
                verified_argv,
                verified_context=context,
            )
            == 0
        )
    finally:
        for path, backup in backups:
            path.unlink()
            os.replace(backup, path)

    for name in ("arm_source_n10000.csv", "arm_source_n20000.csv"):
        assert (verified_out / name).read_bytes() == (baseline / name).read_bytes()
    summary = json.loads(
        (verified_out / "controlled_real10k_20k_nested_summary.json").read_text()
    )
    assert summary["verified_input_consumption"] == {
        "mode": "GATE_VERIFIED_HELD_BYTES_ONLY",
        "verified_context_schema": BUILDER.VERIFIED_CONTEXT_SCHEMA,
        "exact_role_order": list(BUILDER.VERIFIED_CONTEXT_ROLES),
        "role_sha256": {
            role: entries[role]["sha256"] for role in BUILDER.VERIFIED_CONTEXT_ROLES
        },
        "path_reopen_for_consumed_inputs": False,
    }

    malformed = {
        "schema": BUILDER.VERIFIED_CONTEXT_SCHEMA,
        "entries": dict(entries),
    }
    malformed["entries"].pop("historical_model_summary_json")
    rejected_out = tmp_path / "rejected_missing_context_role"
    with pytest.raises(ValueError, match="role keyset is not exact"):
        BUILDER.main(
            _arguments(fixture, rejected_out),
            verified_context=malformed,
        )
    assert not rejected_out.exists()


def test_duplicate_authoritative_match_fails_closed_and_preserves_fail_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    duplicate = dict(fixture["authoritative_rows"][0])
    duplicate["row_index"] = len(fixture["authoritative_rows"])
    duplicate["touchstone_path"] = "/mars/duplicate-path/emx.s4p"
    fixture["authoritative_rows"].append(duplicate)
    _write_csv(fixture["authoritative_path"], fixture["authoritative_rows"])
    out_dir = tmp_path / "duplicate_match_failure"
    with pytest.raises(ValueError, match="exactly one authoritative match"):
        BUILDER.main(_arguments(fixture, out_dir))
    failure = json.loads((out_dir / "BUILD_FAIL.json").read_text())
    assert failure["status"] == "FAIL"
    assert failure["training_launch_authorized"] is False
    assert failure["partial_outputs_preserved"] is True
    assert not (out_dir / "controlled_real10k_20k_nested_receipt.json").exists()


def test_existing_output_directory_is_never_modified(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    sentinel = out_dir / "sentinel.txt"
    sentinel.write_text("do-not-modify\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="no-clobber"):
        BUILDER.main(_arguments(fixture, out_dir))
    assert sentinel.read_text(encoding="utf-8") == "do-not-modify\n"
    assert sorted(path.name for path in out_dir.iterdir()) == ["sentinel.txt"]
