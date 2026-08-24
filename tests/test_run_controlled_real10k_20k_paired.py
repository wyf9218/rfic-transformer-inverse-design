from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_controlled_real10k_20k_paired.py"
TEST_COUNTS = {
    "small": {"source_rows": 8, "gradient_train": 4, "validation": 2, "test": 2},
    "large": {"source_rows": 12, "gradient_train": 8, "validation": 2, "test": 2},
}
_ISOLATED_NUMPY_PYTHON: Path | None = None


def _isolated_numpy_python() -> Path:
    global _ISOLATED_NUMPY_PYTHON
    if _ISOLATED_NUMPY_PYTHON is not None:
        return _ISOLATED_NUMPY_PYTHON
    candidates = (
        Path(sys.executable),
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-I", "-B", "-c", "import numpy"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            _ISOLATED_NUMPY_PYTHON = candidate.resolve()
            return _ISOLATED_NUMPY_PYTHON
    raise AssertionError("tests require a Python whose isolated mode can import NumPy")


def _load_module():
    spec = importlib.util.spec_from_file_location("controlled_real10k_20k_runner_v2_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.EXPECTED_COUNTS = {arm: dict(values) for arm, values in TEST_COUNTS.items()}
    module._load1 = lambda: 0.25
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _input_values(index: int) -> tuple[float, float, float, float]:
    if index < 2:
        base = (0.65, 0.65, 6.0, 0.05)
    elif index < 4:
        base = (1.25, 1.25, 11.0, 0.25)
    else:
        base = (1.90, 1.90, 16.0, 0.45)
    offset = 0.001 * (index % 2)
    return base[0] + offset, base[1] + offset, base[2] + offset, base[3] + 0.0001 * index


def _geometry_values(index: int) -> tuple[float, ...]:
    return (
        180.0 + index,
        190.0 + index,
        200.0 + index,
        210.0 + index,
        4.0 + 0.01 * index,
        30.0 + index,
        31.0 + index,
        -20.0 + index,
        140.0 + index,
        141.0 + index,
    )


def _exact_geometry(module, values: tuple[float, ...]) -> str:
    return _canonical_sha(
        {
            "schema": "ordered_inverse_geometry_float64_v1",
            "columns": list(module.GEOMETRY_COLUMNS),
            "values": [format(value, ".17g") for value in values],
        }
    )


def _portable_geometry(module, values: tuple[float, ...]) -> str:
    return _canonical_sha(
        {
            "schema": "ordered_inverse_geometry_decimal12_v1",
            "columns": list(module.GEOMETRY_COLUMNS),
            "values": [format(value, ".12f") for value in values],
        }
    )


def _row(module, index: int) -> dict[str, str]:
    inputs = _input_values(index)
    geometry = _geometry_values(index)
    split = "validation" if index < 2 else "test" if index < 4 else "train"
    row = {
        "controlled_source_row_number": str(index + 1),
        "controlled_origin": (
            "historical10k_exact_authoritative_match"
            if index < 8
            else "authoritative100k_train_cell_extra"
        ),
        "controlled_physical_cell_4d": module.canonical_physical_cell_id(inputs),
        "controlled_split_assignment": split,
        "canonical_geometry_identity_sha256": _exact_geometry(module, geometry),
        "portable_geometry_decimal12_sha256": _portable_geometry(module, geometry),
        "evaluation": f"real-emx-{index}",
        "touchstone_path": f"/frozen/real-emx-{index}.s4p",
        "touchstone_sha256": hashlib.sha256(f"touchstone-{index}".encode()).hexdigest(),
    }
    row.update({key: format(value, ".17g") for key, value in zip(module.INPUT_COLUMNS, inputs)})
    row.update({key: format(value, ".17g") for key, value in zip(module.GEOMETRY_COLUMNS, geometry)})
    return row


def _write_table(module, path: Path, count: int) -> list[dict[str, str]]:
    rows = [_row(module, index) for index in range(count)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(module.OUTPUT_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _artifact(path: Path) -> dict[str, object]:
    return {"path": str(path), "sha256": _sha(path), "size_bytes": path.stat().st_size}


def _normalization(module, *, hostile: bool = False) -> dict[str, object]:
    input_midpoint = [(low + high) / 2 for low, high in zip(module.INPUT_LOWER, module.INPUT_UPPER)]
    input_half = [(high - low) / 2 for low, high in zip(module.INPUT_LOWER, module.INPUT_UPPER)]
    geometry_midpoint = [
        (low + high) / 2 for low, high in zip(module.GEOMETRY_LOWER, module.GEOMETRY_UPPER)
    ]
    geometry_half = [
        (high - low) / 2 for low, high in zip(module.GEOMETRY_LOWER, module.GEOMETRY_UPPER)
    ]
    return {
        "schema": module.NORMALIZATION_SCHEMA,
        "input_columns": list(module.INPUT_COLUMNS),
        "geometry_columns": list(module.GEOMETRY_COLUMNS),
        "input_lower": list(module.INPUT_LOWER),
        "input_upper": list(module.INPUT_UPPER),
        "geometry_lower": list(module.GEOMETRY_LOWER),
        "geometry_upper": list(module.GEOMETRY_UPPER),
        "input_midpoint": input_midpoint,
        "input_half_range": input_half,
        "geometry_midpoint": geometry_midpoint,
        "geometry_half_range": geometry_half,
        "train_arm_specific_statistics_used": hostile,
        "large_arm_empirical_statistics_used": False,
        "all_loaded_rows_required_inside_declared_bounds": True,
    }


def _write_material(
    module, root: Path, *, hostile_normalization: bool = False
) -> tuple[Path, Path]:
    root.mkdir()
    authority_root = root.parent / f"{root.name}_outer_authority"
    authority_root.mkdir()
    source_root = authority_root / "payload"
    source_root.mkdir()
    small_path, large_path = root / "arm_source_n10000.csv", root / "arm_source_n20000.csv"
    small_rows = _write_table(module, small_path, 8)
    _write_table(module, large_path, 12)
    historical_source = source_root / "historical_10k.csv"
    authoritative_source = source_root / "authoritative_100k.csv"
    historical_source.write_bytes(small_path.read_bytes())
    authoritative_source.write_bytes(large_path.read_bytes())
    historical = source_root / "historical_model_summary.json"
    _write_json(historical, {"model_id": "historical-real10k", "frozen": True})
    builder_source = source_root / "build_controlled_real10k_20k_nested.py"
    splitter_source = source_root / "model_splitting.py"
    wrapper_source = source_root / "run_controlled_real10k_20k_materialization.py"
    builder_source.write_text("# exact synthetic builder identity\n", encoding="utf-8")
    splitter_source.write_text("# exact synthetic splitter identity\n", encoding="utf-8")
    wrapper_source.write_text("# exact synthetic outer gate identity\n", encoding="utf-8")
    module.FROZEN_HISTORICAL_10K_CSV_SHA256 = _sha(historical_source)
    module.FROZEN_AUTHORITATIVE_100K_CSV_SHA256 = _sha(authoritative_source)
    module.FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256 = _sha(historical)
    module.FROZEN_AUTHORITATIVE_SOURCE_ROWS = 12
    shared_path = Path(module.shared_contract.__file__).resolve()
    validation = [row["canonical_geometry_identity_sha256"] for row in small_rows if row["controlled_split_assignment"] == "validation"]
    test = [row["canonical_geometry_identity_sha256"] for row in small_rows if row["controlled_split_assignment"] == "test"]
    partition_fingerprint = "b" * 64
    holdout = root / "fixed_common_holdout_manifest.json"
    fingerprint = hashlib.sha256(
        "".join(
            [f"validation\0{value}\n" for value in sorted(validation)]
            + [f"test\0{value}\n" for value in sorted(test)]
        ).encode("ascii")
    ).hexdigest()
    _write_json(
        holdout,
        {
            "schema": module.HOLDOUT_SCHEMA,
            "identity_kind": "canonical_geometry_sha256",
            "historical_model_summary_sha256": _sha(historical),
            "shared_contract_sha256": _sha(shared_path),
            "selection_method": "exact_historical_physical_cell_grouped_split_reconstruction",
            "selection_uses_model_results": False,
            "stratification": ["physical_cell_4d"],
            "physical_cell_encoding": module.PHYSICAL_CELL_ENCODING,
            "physical_cell_bins": module.PHYSICAL_CELL_BINS,
            "physical_lower": list(module.INPUT_LOWER),
            "physical_upper": list(module.INPUT_UPPER),
            "validation_count": 2,
            "test_count": 2,
            "validation_geometry_identities": validation,
            "test_geometry_identities": test,
            "validation_portable_decimal12_geometry_identities": sorted(
                small_rows[i]["portable_geometry_decimal12_sha256"] for i in (0, 1)
            ),
            "test_portable_decimal12_geometry_identities": sorted(
                small_rows[i]["portable_geometry_decimal12_sha256"] for i in (2, 3)
            ),
            "train_cell_ids": [small_rows[4]["controlled_physical_cell_4d"]],
            "validation_cell_ids": [small_rows[0]["controlled_physical_cell_4d"]],
            "test_cell_ids": [small_rows[2]["controlled_physical_cell_4d"]],
            "physical_cell_partition_fingerprint_sha256": partition_fingerprint,
            "complete_cell_isolation": {
                "all_historical_rows_assigned_once": True,
                "every_cell_assigned_to_exactly_one_split": True,
                "train_validation_test_cell_overlap_count": 0,
                "train_cell_count": 1,
                "validation_cell_count": 1,
                "test_cell_count": 1,
            },
            "common_holdout_fingerprint_sha256": fingerprint,
        },
    )
    normalization = root / "declared_midpoint_half_range_normalization_contract.json"
    _write_json(normalization, _normalization(module, hostile=hostile_normalization))
    summary_path = root / module.MATERIAL_SUMMARY_NAME
    artifacts = {
        small_path.name: _artifact(small_path),
        large_path.name: _artifact(large_path),
        holdout.name: _artifact(holdout),
        normalization.name: _artifact(normalization),
    }
    implementation = {
        "builder": _artifact(builder_source),
        "shared_contract": _artifact(shared_path),
        "splitter_source": _artifact(splitter_source),
    }
    sources = {
        "historical_10k_csv": {
            "path": str(historical_source),
            "sha256": _sha(historical_source),
            "rows": 8,
        },
        "authoritative_100k_csv": {
            "path": str(authoritative_source),
            "sha256": _sha(authoritative_source),
            "rows": 12,
        },
        "historical_model_summary_json": {
            "path": str(historical),
            "sha256": _sha(historical),
        },
    }
    production = {key: True for key in module.MATERIALIZATION_PRODUCTION_EXACT_CHECK_KEYS}
    summary = {
        "schema": module.MATERIAL_SCHEMA,
        "status": "PASS",
        "decision": "PREPARED_FOR_INDEPENDENT_QA",
        "result_accessed": False,
        "model_training_performed": False,
        "emx_performed": False,
        "implementation_identities": implementation,
        "shared_contract": {
            "physical_cell_encoding": module.PHYSICAL_CELL_ENCODING,
            "physical_cell_bins": module.PHYSICAL_CELL_BINS,
            "extra_selection_seed": module.EXACT_EXTRA_SELECTION_SEED,
            "paired_seeds": list(module.EXACT_PAIRED_SEEDS),
        },
        "source_identities": sources,
        "selection_contract": {
            "method": "stable_sha256_rank_within_proportional_historical_train_cell_quotas_v1",
            "selection_seed": module.EXACT_EXTRA_SELECTION_SEED,
            "selection_uses_model_results": False,
            "historical_geometry_excluded": True,
            "historical_touchstone_content_excluded": True,
            "extra_rows_restricted_to_historical_train_cells": True,
        },
        "arm_counts": {
            "n10000": {"source_table_rows": 8, "gradient_train_rows": 4, "validation_rows": 2, "test_rows": 2},
            "n20000": {"source_table_rows": 12, "gradient_train_rows": 8, "validation_rows": 2, "test_rows": 2},
        },
        "nested_identity_contract": {
            "arm_n10000_is_exact_ordered_prefix_and_row_subset_of_arm_n20000": True,
            "common_output_schema": list(module.OUTPUT_COLUMNS),
            "common_validation_and_test_unchanged": True,
            "geometry_identity_overlap_historical_vs_extra": 0,
            "touchstone_identity_overlap_historical_vs_extra": 0,
        },
        "fixed_contracts": {
            "common_holdout": {"path": str(holdout), "sha256": _sha(holdout)},
            "declared_midpoint_half_range_normalization": {"path": str(normalization), "sha256": _sha(normalization)},
        },
        "split_reconstruction": {
            "exact_match_to_historical_summary": True,
            "physical_cell_partition_fingerprint_sha256": partition_fingerprint,
        },
        "production_exact_checks": production,
        "artifacts": artifacts,
        "training_launch_authorized": False,
        "independent_qa_required": True,
    }
    _write_json(summary_path, summary)
    qa_required = root / module.MATERIAL_QA_REQUIRED_NAME
    _write_json(
        qa_required,
        {
            "schema": module.MATERIAL_QA_REQUIRED_SCHEMA,
            "status": "INDEPENDENT_QA_REQUIRED",
            "verdict": "NO_GO_PENDING_FRESH_INDEPENDENT_QA",
            "materialization_summary": {"path": str(summary_path), "sha256": _sha(summary_path)},
            "implementation_identities": implementation,
            "frozen_artifacts": artifacts,
            "frozen_scientific_contract": {
                "physical_cell_encoding": module.PHYSICAL_CELL_ENCODING,
                "physical_cell_bins": module.PHYSICAL_CELL_BINS,
                "extra_selection_seed": module.EXACT_EXTRA_SELECTION_SEED,
                "paired_seeds": list(module.EXACT_PAIRED_SEEDS),
            },
            "training_authorized": False,
            "result_access_authorized": False,
            "fresh_emx_authorized": False,
            "next_legal_gate": {
                "required_receipt_schema": module.GO_SCHEMA,
                "required_status": module.GO_STATUS,
                "must_bind_all_bytes_in_this_record": True,
                "must_report_zero_p0_and_zero_p1_findings": True,
            },
        },
    )
    receipt_path = root / module.MATERIAL_RECEIPT_NAME
    receipt_artifacts = {
        **artifacts,
        summary_path.name: _artifact(summary_path),
        qa_required.name: _artifact(qa_required),
    }
    _write_json(
        receipt_path,
        {
            "schema": module.MATERIAL_RECEIPT_SCHEMA,
            "status": "PASS",
            "verdict": "PREPARED_FOR_INDEPENDENT_QA",
            "checks": {"all_contracts_exact": True, "no_results_accessed": True},
            "artifact_identities": receipt_artifacts,
            "production_exact_checks": production,
            "training_launch_authorized": False,
            "independent_qa_required": True,
            "independent_qa_required_record": {
                "path": str(qa_required),
                "sha256": _sha(qa_required),
            },
            "next_legal_gate": "FRESH_INDEPENDENT_RESULT_BLIND_QA_EXACT_GO",
            "sha256_closure_contract": {
                "index_filename": module.MATERIAL_SHA_INDEX_NAME,
                "index_self_hash_included": False,
                "exact_entry_count": 7,
                "exact_filenames_in_order": [
                    small_path.name,
                    large_path.name,
                    holdout.name,
                    normalization.name,
                    summary_path.name,
                    qa_required.name,
                    receipt_path.name,
                ],
            },
        },
    )
    index = root / module.MATERIAL_SHA_INDEX_NAME
    with index.open("w", encoding="utf-8") as handle:
        for path in (
            small_path,
            large_path,
            holdout,
            normalization,
            summary_path,
            qa_required,
            receipt_path,
        ):
            handle.write(f"{_sha(path)}  {path.name}\n")

    def bound_record(role: str, path: Path) -> dict[str, object]:
        metadata = path.lstat()
        return {
            "role": role,
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "size_bytes": metadata.st_size,
            "mode_octal": f"{module.stat.S_IMODE(metadata.st_mode):04o}",
            "nlink": metadata.st_nlink,
            "st_dev": metadata.st_dev,
            "st_ino": metadata.st_ino,
        }

    protocol_paths = {
        "preregistration_v1": source_root / "prereg_v1.json",
        "preregistration_addendum_v1_1": source_root / "prereg_v1_1.json",
        "preregistration_addendum_v1_2": source_root / "prereg_v1_2.json",
        "mars_preflight_prepared": source_root / "mars_preflight_prepared.json",
        "mars_preflight_execution_qa_required": source_root / "mars_preflight_execution_qa_required.json",
        "mars_preflight_prepare_sha_index": source_root / "mars_preflight_prepare_sha_index.txt",
        "mars_preflight_receipt_body": source_root / "mars_preflight_receipt_body.json",
        "mars_preflight_sha_index": source_root / "mars_preflight_sha_index.txt",
        "mars_preflight_committed": source_root / "mars_preflight_committed.json",
        "mars_preflight_consumed_lease": source_root / "mars_preflight_consumed_lease.json",
    }
    for role, path in protocol_paths.items():
        _write_json(path, {"role": role, "synthetic_exact_fixture": True})

    package_root = source_root / "package_v5"
    package_contract_root = package_root / "runtime" / "contracts"
    package_contract_root.mkdir(parents=True)
    package_singleton_lock = package_root / "CONTROLLED_SINGLETON.lock"
    package_singleton_lock.write_bytes(b"")
    package_singleton_contract = package_contract_root / "PROCESS_SINGLETON_CONTRACT.json"
    _write_json(
        package_singleton_contract,
        {
            "schema": "controlled_real10k_20k_process_singleton_contract_v1",
            "lock": {
                "relative_path": "CONTROLLED_SINGLETON.lock",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "operation": "LOCK_EX|LOCK_NB",
            },
            "protected_entrypoints": [],
            "proc_audit": {},
            "lifetime": {"full_lifetime_required": True},
            "conflict_policy": {"controlled_process_start_authorized": False},
        },
    )
    package_manifest = package_root / "MANIFEST.json"
    package_receipt = package_root / "RECEIPT.json"
    package_qa = package_root / "INDEPENDENT_QA_REQUIRED.json"
    package_index = package_root / "SHA256SUMS.txt"
    package_commit = package_root / module.PACKAGE_COMMIT_NAME
    _write_json(
        package_manifest,
        {"schema": module.PACKAGE_MANIFEST_SCHEMA, "package_version": module.PACKAGE_VERSION},
    )
    _write_json(
        package_receipt,
        {"schema": module.PACKAGE_RECEIPT_SCHEMA, "package_version": module.PACKAGE_VERSION},
    )
    _write_json(package_qa, {"schema": module.PACKAGE_QA_REQUIRED_SCHEMA})
    package_index.write_text(
        "".join(
            f"{_sha(path)}  {path.name}\n"
            for path in (package_manifest, package_receipt, package_qa)
        ),
        encoding="ascii",
    )

    attempt_parent = source_root / "package_attempts"
    attempt_root = attempt_parent / "attempt_0001"
    attempt_root.mkdir(parents=True)
    attempt_body = attempt_root / module.PACKAGE_BUILD_ATTEMPT_BODY_NAME
    attempt_committed = attempt_root / module.PACKAGE_BUILD_ATTEMPT_COMMITTED_NAME
    package_commit_payload = {
        "schema": module.PACKAGE_COMMIT_SCHEMA,
        "status": module.PACKAGE_COMMIT_STATUS,
        "package_version": module.PACKAGE_VERSION,
        "manifest": {"path": package_manifest.name, "sha256": _sha(package_manifest)},
        "receipt": {"path": package_receipt.name, "sha256": _sha(package_receipt)},
        "independent_qa_required": {"path": package_qa.name, "sha256": _sha(package_qa)},
        "sha256sums": {"path": package_index.name, "sha256": _sha(package_index)},
        "required_external_pass_attempt": {
            "body": {
                "path": str(attempt_body),
                "schema": module.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
                "status": module.PACKAGE_BUILD_ATTEMPT_BODY_STATUS,
            },
            "committed": {
                "path": str(attempt_committed),
                "schema": module.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
                "status": module.PACKAGE_BUILD_ATTEMPT_COMMITTED_STATUS,
            },
        },
        "creation_order_contract": {
            "this_member_created_last": True,
            "post_commit_package_file_creation_permitted": False,
        },
        "authorities": dict(module.PACKAGE_AUTHORITIES),
        "execution_authorized": False,
    }
    _write_json(package_commit, package_commit_payload)
    for path in (
        package_singleton_lock,
        package_singleton_contract,
        package_manifest,
        package_receipt,
        package_qa,
        package_index,
        package_commit,
    ):
        path.chmod(0o444)
    package_contract_root.chmod(0o555)
    package_contract_root.parent.chmod(0o555)
    package_root.chmod(0o555)
    package_metadata = package_root.lstat()
    attempt_body_payload = {
        "schema": module.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
        "status": module.PACKAGE_BUILD_ATTEMPT_BODY_STATUS,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "invocation": {
            "argv": ["synthetic-package-v5-builder"],
            "cwd": {},
            "output_dir": str(package_root),
            "failure_receipt_dir": str(attempt_root),
            "package_spec": {},
            "builder": {},
            "python": {},
            "runtime": {},
            "environment": {},
        },
        "observed_identity": {
            "package_spec_sha256": "a" * 64,
            "builder_sha256": "b" * 64,
            "package_output_device": package_metadata.st_dev,
            "package_output_inode": package_metadata.st_ino,
        },
        "package": {
            "path": str(package_root),
            "manifest_sha256": _sha(package_manifest),
            "receipt_sha256": _sha(package_receipt),
            "independent_qa_required_sha256": _sha(package_qa),
            "sha256sums_sha256": _sha(package_index),
            "package_commit_sha256": _sha(package_commit),
            "file_count": 7,
        },
        "partial_output_preserved": False,
        "authorities": dict(module.PACKAGE_AUTHORITIES),
        "execution_authorized": False,
    }
    _write_json(attempt_body, attempt_body_payload)
    attempt_root_metadata = attempt_root.lstat()
    attempt_parent_metadata = attempt_parent.lstat()
    attempt_committed_payload = {
        "schema": module.PACKAGE_BUILD_ATTEMPT_COMMITTED_SCHEMA,
        "status": module.PACKAGE_BUILD_ATTEMPT_COMMITTED_STATUS,
        "committed_utc": datetime.now(timezone.utc).isoformat(),
        "body": {
            "path": str(attempt_body),
            "sha256": _sha(attempt_body),
            "schema": module.PACKAGE_BUILD_ATTEMPT_BODY_SCHEMA,
            "status": module.PACKAGE_BUILD_ATTEMPT_BODY_STATUS,
        },
        "package_commit": {
            "path": str(package_commit),
            "sha256": _sha(package_commit),
            "schema": module.PACKAGE_COMMIT_SCHEMA,
            "status": module.PACKAGE_COMMIT_STATUS,
        },
        "package_root": {
            "path": str(package_root),
            "st_dev": package_metadata.st_dev,
            "st_ino": package_metadata.st_ino,
            "mode_octal": "0555",
        },
        "attempt_root": {
            "path": str(attempt_root),
            "st_dev": attempt_root_metadata.st_dev,
            "st_ino": attempt_root_metadata.st_ino,
            "mode_octal": "0555",
        },
        "attempt_parent": {
            "path": str(attempt_parent),
            "st_dev": attempt_parent_metadata.st_dev,
            "st_ino": attempt_parent_metadata.st_ino,
            "mode_octal": f"{module.stat.S_IMODE(attempt_parent_metadata.st_mode):04o}",
        },
        "publication": dict(module.PACKAGE_ATTEMPT_PUBLICATION),
        "authorities": dict(module.PACKAGE_AUTHORITIES),
        "execution_authorized": False,
    }
    _write_json(attempt_committed, attempt_committed_payload)
    attempt_body.chmod(0o444)
    attempt_committed.chmod(0o444)
    attempt_root.chmod(0o555)

    role_paths = {
        "wrapper_code": wrapper_source,
        "materialization_builder_code": builder_source,
        "shared_contract_code": shared_path,
        "splitter_code": splitter_source,
        "package_build_attempt_body": attempt_body,
        "package_build_attempt_committed": attempt_committed,
        **protocol_paths,
        "package_process_singleton_contract": package_singleton_contract,
        "package_singleton_lock": package_singleton_lock,
        "historical_10k_csv": historical_source,
        "authoritative_100k_csv": authoritative_source,
        "historical_model_summary_json": historical,
    }
    bindings = {
        role: bound_record(role, role_paths[role])
        for role in module.MATERIALIZATION_BOUND_ROLE_ORDER
    }
    candidate = authority_root / "candidate"
    execution_receipt = authority_root / "execution_receipt"
    candidate.mkdir()
    challenge_nonce = "1" * 32
    runtime_identity = {"identity_sha256": "2" * 64}
    host_identity = {"identity_sha256": "3" * 64}
    runtime_manifest_sha = hashlib.sha256(b"{}\n").hexdigest()
    sealed_runtime = {
        "expected_runtime_closure_json_sha256": runtime_manifest_sha,
        "attestation": {
            "schema": module.runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "materialization",
            "manifest_sha256": runtime_manifest_sha,
            "pure_archive_sha256": "d" * 64,
            "bootstrap_sha256": _sha(Path(module.runtime_bootstrap.__file__).resolve()),
        },
        "runtime_manifest_role_identity": {
            "kind": "file",
            "path": "runtime/contracts/RUNTIME_CLOSURE.json",
            "sha256": runtime_manifest_sha,
        },
        "runtime_tree_role_identity": {
            "kind": "tree",
            "path": "runtime/dependencies",
            "sha256": "9" * 64,
        },
        "required_external_entrypoint": "materialization",
        "raw_runtime_fallback_authorized": False,
    }
    materialization_contract = {"builder_argv": ["--out-dir", str(root)]}
    candidate_manifest_payload = {
        "schema": module.MATERIALIZATION_CANDIDATE_MANIFEST_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PREPARED_IMMUTABLE_RESULT_BLIND_NO_AUTHORITY",
        "result_blind": True,
        "candidate_dir": str(candidate),
        "challenge_nonce": challenge_nonce,
        "bindings": bindings,
        "bound_role_order": list(module.MATERIALIZATION_BOUND_ROLE_ORDER),
        "materialization_contract": materialization_contract,
        "materialization_contract_sha256": _canonical_sha(materialization_contract),
        "runtime_identity": runtime_identity,
        "host_identity": host_identity,
        "sealed_runtime": sealed_runtime,
        "host_constraints_asserted": {},
        "future_paths": {
            "materialization_out_dir": str(root),
            "execution_receipt_dir": str(execution_receipt),
        },
        "authorities": dict(module.MATERIALIZATION_CANDIDATE_AUTHORITIES),
        "result_or_row_access": {
            "csv_rows_read": False,
            "model_summary_json_parsed": False,
            "numerical_model_results_accessed": False,
            "scientific_source_files_sha256_and_stat_only": True,
            "protocol_and_provenance_json_parsed": True,
            "descriptor_sealed_runtime_imports_executed": True,
        },
        "next_legal_gate": module.MATERIALIZATION_GO_SCHEMA,
    }
    candidate_manifest = candidate / "MANIFEST.json"
    candidate_qa = candidate / "INDEPENDENT_QA_REQUIRED.json"
    candidate_prepared = candidate / "PREPARED_RECEIPT.json"
    _write_json(candidate_manifest, candidate_manifest_payload)
    _write_json(candidate_qa, {"status": "INDEPENDENT_QA_REQUIRED"})
    _write_json(candidate_prepared, {"status": "PASS_PREPARED_AWAITING_EXTERNAL_EXACT_GO"})
    candidate_index = candidate / "SHA256SUMS.txt"
    candidate_index.write_text(
        "".join(
            f"{_sha(path)}  {path.name}\n"
            for path in (candidate_manifest, candidate_qa, candidate_prepared)
        ),
        encoding="ascii",
    )

    execution_receipt.mkdir()
    material_go_payload = {
        "schema": module.MATERIALIZATION_GO_SCHEMA,
        "status": "GO",
        "scope": module.MATERIALIZATION_GO_SCOPE,
        "issued_utc": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "expires_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "challenge_nonce": challenge_nonce,
        "reviewer": {
            "reviewer_id": "synthetic-independent-reviewer",
            "independent": True,
            "result_blind": True,
            "reviewed_without_numerical_results": True,
        },
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "bindings": {
            "candidate_manifest_sha256": _sha(candidate_manifest),
            "candidate_sha256sums_sha256": _sha(candidate_index),
            "challenge_nonce": challenge_nonce,
            "artifact_sha256": {
                role: bindings[role]["sha256"]
                for role in module.MATERIALIZATION_BOUND_ROLE_ORDER
            },
            "materialization_out_dir": str(root),
            "execution_receipt_dir": str(execution_receipt),
            "runtime_identity_sha256": runtime_identity["identity_sha256"],
            "host_identity_sha256": host_identity["identity_sha256"],
            "materialization_contract_sha256": _canonical_sha(materialization_contract),
            "sealed_runtime": sealed_runtime,
        },
        "authorities": dict(module.MATERIALIZATION_GO_AUTHORITIES),
    }
    go_copy = execution_receipt / "GO_AUTHORITY.json"
    intent = execution_receipt / "INTENT.json"
    running = execution_receipt / "RUNNING.json"
    _write_json(go_copy, material_go_payload)
    _write_json(intent, {"status": "INTENT_RESULT_BLIND_MATERIALIZATION_ONLY"})
    _write_json(running, {"status": "RUNNING_IN_PROCESS_RESULT_BLIND_MATERIALIZATION"})
    output_closure = module._artifact_snapshot(root)
    validation = {
        "status": "PASS_MATERIALIZATION_DEEP_VALIDATED_RESULT_BLIND",
        "root": str(root),
        "arm_rows": {"n10000": 8, "n20000": 12},
        "gradient_train_rows": {"n10000": 4, "n20000": 8},
        "validation_rows_common": 2,
        "test_rows_common": 2,
        "artifact_closure": output_closure,
        "sha256sums_sha256": _sha(index),
        "training_authorized": False,
        "evaluation_authorized": False,
        "fresh_emx_authorized": False,
    }
    precursor_closure = module._artifact_snapshot(execution_receipt)
    complete = execution_receipt / "COMPLETE.json"
    complete_payload = {
        "schema": module.MATERIALIZATION_COMPLETE_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETE_RESULT_BLIND_MATERIALIZATION_DEEP_VALIDATED",
        "candidate_manifest_sha256": _sha(candidate_manifest),
        "candidate_sha256sums_sha256": _sha(candidate_index),
        "go_sha256": _sha(go_copy),
        "challenge_nonce": challenge_nonce,
        "candidate_manifest": {"path": str(candidate_manifest), "sha256": _sha(candidate_manifest)},
        "candidate_sha_index": {"path": str(candidate_index), "sha256": _sha(candidate_index)},
        "materialization_go_authority": {"path": str(go_copy), "sha256": _sha(go_copy)},
        "materialization_output": {
            "path": str(root),
            "sha256sums": {"path": str(index), "sha256": _sha(index)},
            "artifact_closure": output_closure,
        },
        "materialization_validation": validation,
        "frozen_closure_after_materialization": {
            "candidate_manifest_sha256": _sha(candidate_manifest),
            "candidate_sha256sums_sha256": _sha(candidate_index),
            "artifact_sha256": {
                role: bindings[role]["sha256"]
                for role in module.MATERIALIZATION_BOUND_ROLE_ORDER
            },
            "go_sha256": _sha(go_copy),
            "held_snapshot_consumption": True,
            "path_reopen_for_consumed_inputs": False,
        },
        "sealed_runtime": sealed_runtime,
        "execution_precursor_closure": precursor_closure,
        "retry_authorized": False,
        "training_authorized": False,
        "evaluation_authorized": False,
        "common_test_access_authorized": False,
        "numerical_metric_access_authorized": False,
        "fresh_emx_authorized": False,
        "emx_generation_authorized": False,
        "process_signal_sent": False,
        "subprocess_spawned": False,
        "next_legal_gate": "FRESH_INDEPENDENT_QA_OF_MATERIALIZED_DATA_AND_TRAINING_CONTRACT",
    }
    _write_json(complete, complete_payload)
    return summary_path, complete


def _write_fake_trainer(path: Path) -> None:
    path.write_text(
        r'''#!/usr/bin/env python3
import csv, hashlib, json, math, os, pathlib, sys
import numpy as np

tokens = sys.argv[1:]
flags = {}
for index in range(0, len(tokens), 2):
    if index + 1 >= len(tokens) or not tokens[index].startswith('--'):
        raise SystemExit(70)
    flags[tokens[index]] = tokens[index + 1]
required = {
    '--split-reference-columns': 'input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center',
    '--split-mode': 'fixed_common_holdout_manifest',
    '--forward-initialization-mode': 'random',
    '--inverse-initialization-mode': 'random',
    '--forward-hidden-widths': '256,256,256',
    '--inverse-hidden-widths': '256,256,256',
    '--inverse-geometry-projection': 'independent_sigmoid',
    '--inverse-checkpoint-selection': 'training_objective',
    '--batch-size': '1024',
    '--training-batch-sampler': 'row_uniform',
    '--exact-update-batch-mode': 'continuous_permutation_full_batch',
    '--forward-max-optimizer-updates': '1200',
    '--inverse-max-optimizer-updates': '1200',
    '--validation-every-optimizer-updates': '20',
    '--learning-rate': '0.001',
    '--training-learning-rate-schedule': 'constant',
    '--weight-decay': '0.000001',
    '--response-loss-family': 'mse',
    '--response-loss-scaling': 'declared_range',
    '--response-weight': '1.0',
    '--geometry-anchor-weight': '0.01',
    '--topology-feasibility-weight': '0.0',
    '--q-target-semantics': 'exact',
    '--response-weight-schedule': 'warmup_ramp_adaptive_ema',
    '--response-schedule-domain': 'optimizer_update',
    '--response-warmup-fraction': '0.05',
    '--response-ramp-fraction': '0.25',
    '--response-warmup-optimizer-updates': '60',
    '--response-ramp-optimizer-updates': '300',
    '--response-adaptive-ema-decay': '0.95',
    '--response-adaptive-min-multiplier': '0.25',
    '--response-adaptive-max-multiplier': '4.0',
    '--max-forward-test-rmse': 'inf',
    '--max-tandem-response-test-rmse': 'inf',
    '--evaluation-mode': 'validation_only',
    '--local-refinement-steps': '0',
    '--max-prediction-rows': '20000',
    '--stage-checkpoint-mode': 'resume_exact',
}
if any(flags.get(key) != value for key, value in required.items()):
    raise SystemExit(71)
if flags['--seed'] != flags['--split-seed']:
    raise SystemExit(72)
training_csv = pathlib.Path(flags['--training-csv']).resolve()
out = pathlib.Path(flags['--out-dir']).resolve()
out.mkdir(parents=True, exist_ok=True)
arm = out.name
fixture_root = out.parents[3]
with (fixture_root/'invocations.csv').open('a', encoding='utf-8') as handle:
    handle.write(f"{flags['--seed']},{arm},{flags['--evaluation-mode']}\n")
with (fixture_root/'observed_child_environments.jsonl').open('a', encoding='utf-8') as handle:
    handle.write(json.dumps({
        'seed': int(flags['--seed']),
        'arm': arm,
        'environment': dict(os.environ),
        'isolated': int(sys.flags.isolated),
        'dont_write_bytecode': bool(sys.dont_write_bytecode),
        'argv': list(sys.argv),
    }, sort_keys=True)+'\n')
if (fixture_root/f'fail_{arm}.marker').is_file():
    raise SystemExit(9)
with training_csv.open(newline='', encoding='utf-8') as handle:
    rows = list(csv.DictReader(handle))
holdout = json.loads(pathlib.Path(flags['--fixed-common-holdout-manifest-json']).read_text())
validation_count = len(holdout['validation_geometry_identities'])
test_count = len(holdout['test_geometry_identities'])
train_count = len(rows) - validation_count - test_count

history_path = out/'physical_feature_tandem_inverse_history.csv'
with history_path.open('w', newline='', encoding='utf-8') as handle:
    writer = csv.DictWriter(handle, fieldnames=['stage', 'optimizer_updates'])
    writer.writeheader()
    for stage in ('forward_proxy', 'tandem_inverse'):
        for update in range(20, 1201, 20):
            writer.writerow({'stage': stage, 'optimizer_updates': update})
test_predictions = out/'physical_feature_tandem_inverse_test_predictions.csv'
test_predictions.write_bytes(b'')
validation_predictions = out/'physical_feature_tandem_inverse_validation_predictions.csv'
validation_predictions.write_text('validation_only_no_test_release\n', encoding='utf-8')

arrays = {}
shapes = {
    'forward_weight_0': (10,256), 'forward_weight_1': (256,256),
    'forward_weight_2': (256,256), 'forward_weight_3': (256,4),
    'forward_bias_0': (256,), 'forward_bias_1': (256,),
    'forward_bias_2': (256,), 'forward_bias_3': (4,),
    'inverse_weight_0': (4,256), 'inverse_weight_1': (256,256),
    'inverse_weight_2': (256,256), 'inverse_weight_3': (256,10),
    'inverse_bias_0': (256,), 'inverse_bias_1': (256,),
    'inverse_bias_2': (256,), 'inverse_bias_3': (10,),
}
for key, shape in shapes.items(): arrays[key] = np.zeros(shape, dtype=float)
input_lower = np.asarray([0.5,0.5,5.0,0.0])
input_upper = np.asarray([3.0,3.0,25.0,0.8])
geometry_lower = np.asarray([160,160,160,160,3,20,20,-90,100,100], dtype=float)
geometry_upper = np.asarray([520,520,520,520,12,90,90,90,320,320], dtype=float)
arrays.update({
    'normalization__x_mean': (input_lower+input_upper)/2,
    'normalization__x_scale': (input_upper-input_lower)/2,
    'normalization__feature_lower': input_lower,
    'normalization__feature_upper': input_upper,
    'normalization__y_mean': (geometry_lower+geometry_upper)/2,
    'normalization__y_scale': (geometry_upper-geometry_lower)/2,
    'normalization__geometry_lower': -np.ones(10),
    'normalization__geometry_upper': np.ones(10),
    'normalization__response_loss_dimension_weights': np.ones(4),
    'normalization__response_loss_physical_spans': input_upper-input_lower,
    'normalization_contract__mode': np.asarray(['external_declared_midpoint_half_range']),
    'normalization_contract__sha256': np.asarray([flags['--fixed-normalization-contract-sha256']]),
    'training_sampler__family': np.asarray(['row_uniform']),
    'training_sampler__fingerprint_sha256': np.asarray(['a'*64]),
    'training_sampler__draws_per_epoch': np.asarray([train_count], dtype=np.int64),
    'training_sampler__optimizer_updates_per_epoch': np.asarray([math.ceil(train_count/1024)], dtype=np.int64),
    'optimizer_budget__mode': np.asarray(['fixed_optimizer_updates']),
    'optimizer_budget__fingerprint_sha256': np.asarray(['b'*64]),
    'optimizer_budget__forward_target_updates': np.asarray([1200], dtype=np.int64),
    'optimizer_budget__inverse_target_updates': np.asarray([1200], dtype=np.int64),
    'inverse_geometry_projection__mode': np.asarray(['independent_sigmoid']),
    'inverse_geometry_projection__topology_contract_json': np.asarray([json.dumps({
        'enabled':False,'weight':0.0,
        'geometry_columns':flags['--geometry-columns'].split(','),
        'power_line_port_ground_overlap':{'enabled':False},
    }, sort_keys=True, separators=(',',':'))]),
})
weights_path = out/'physical_feature_tandem_inverse_weights.npz'
np.savez_compressed(weights_path, **arrays)
def sha(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
trainer_sha = sha(pathlib.Path(__file__).resolve())
int_flags = {
    '--min-training-rows','--physical-cell-bins','--seed','--split-seed','--forward-depth',
    '--forward-width','--inverse-depth','--inverse-width','--batch-size','--forward-epochs',
    '--inverse-epochs','--patience','--forward-max-optimizer-updates','--inverse-max-optimizer-updates',
    '--validation-every-optimizer-updates','--response-warmup-optimizer-updates',
    '--response-ramp-optimizer-updates','--local-refinement-steps','--local-refinement-starts',
    '--local-refinement-seed','--max-prediction-rows','--robustness-repeats',
    '--robustness-max-rows','--robustness-seed',
}
float_flags = {
    '--validation-fraction','--test-fraction','--inverse-checkpoint-exact-relative-error-threshold',
    '--learning-rate','--training-final-learning-rate-fraction','--weight-decay','--response-weight',
    '--geometry-anchor-weight','--topology-feasibility-weight','--response-ramp-fraction',
    '--q-minimum-margin-physical','--response-warmup-fraction','--response-adaptive-ema-decay',
    '--response-adaptive-min-multiplier','--response-adaptive-max-multiplier','--normalization-floor',
    '--local-refinement-learning-rate','--local-refinement-final-lr-fraction','--local-refinement-jitter',
}
arguments = {}
for flag, value in flags.items():
    key = flag[2:].replace('-', '_')
    if flag in int_flags: arguments[key] = int(value)
    elif flag in float_flags: arguments[key] = float(value)
    elif value == 'inf': arguments[key] = None
    else: arguments[key] = value
summary = {
    'overall_status': 'COMPLETE_REVIEW_REQUIRED',
    'execution_status': 'PASS',
    'quality_status': 'REVIEW_REQUIRED_VALIDATION_ONLY',
    'eligible_for_checkpoint_model_acceptance': False,
    'eligible_for_model_success_claim': False,
    'evaluation_mode': 'validation_only',
    'training_csv': str(training_csv),
    'training_csv_sha256': sha(training_csv),
    'training_count': len(rows),
    'arguments': arguments,
    'model_comparison_contract': {
        'trainer_implementation_sha256': trainer_sha,
        'input_columns': flags['--input-columns'].split(','),
        'geometry_columns': flags['--geometry-columns'].split(','),
        'architecture': {
            'forward_hidden_widths': [256,256,256],
            'inverse_hidden_widths': [256,256,256],
            'inverse_geometry_projection': 'independent_sigmoid',
        },
        'optimization': {
            'batch_size': 1024, 'training_batch_sampler': 'row_uniform',
            'exact_update_batch_mode': 'continuous_permutation_full_batch',
            'forward_initialization': {'mode':'random','source_weights_sha256':'','source_summary_sha256':''},
            'inverse_initialization': {'mode':'random','source_weights_sha256':'','source_summary_sha256':''},
            'forward_epochs':160,'inverse_epochs':180,'patience':20,
            'forward_max_optimizer_updates': 1200, 'inverse_max_optimizer_updates': 1200,
            'validation_every_optimizer_updates': 20, 'learning_rate': 0.001,
            'training_learning_rate_schedule': 'constant',
            'training_final_learning_rate_fraction':0.1,'weight_decay': 0.000001,
        },
        'normalization': {
            'mode':'external_declared_midpoint_half_range',
            'fixed_contract_sha256':flags['--fixed-normalization-contract-sha256'],
        },
        'evaluation': {'mode':'validation_only','test_access_allowed':False},
        'loss': {
            'response_loss_family':'mse','response_loss_scaling':'declared_range',
            'response_weight':1.0,'geometry_anchor_weight':0.01,'topology_feasibility_weight':0.0,
            'enforce_power_line_port_ground_overlap':False,
            'power_line_bar_offset_um':12.0,'power_line_shield_opening_clearance_um':10.0,
            'power_line_port_ground_overlap_um':10.0,'power_line_feed_training_safety_margin_um':0.0,
            'q_target_semantics':'exact','q_minimum_margin_physical':0.0,
            'relative_error_floors':'0.5,0.5,5.0,0.05','response_semantic_loss_weights':'',
            'response_weight_schedule':'warmup_ramp_adaptive_ema',
            'response_schedule_domain':'optimizer_update','response_warmup_fraction':0.05,
            'response_ramp_fraction':0.25,'response_warmup_optimizer_updates':60,
            'response_ramp_optimizer_updates':300,'response_adaptive_ema_decay':0.95,
            'response_adaptive_min_multiplier':0.25,'response_adaptive_max_multiplier':4.0,
        },
        'split': {
            'mode':'fixed_common_holdout_manifest',
            'fixed_common_holdout_manifest_sha256':flags['--fixed-common-holdout-manifest-sha256'],
            'validation_fraction':0.15,'test_fraction':0.10,
            'physical_cell_bins':4,'physical_cell_lower':flags['--physical-cell-lower'],
            'physical_cell_upper':flags['--physical-cell-upper'],
        },
    },
    'method': {
        'forward_proxy_initialization': {'mode':'random'},
        'inverse_initialization': {'mode':'random'},
        'inverse_checkpoint_selection_uses_validation_only': True,
        'topology_feasibility_contract': {
            'enabled':False,'weight':0.0,
            'geometry_columns':flags['--geometry-columns'].split(','),
            'power_line_port_ground_overlap':{'enabled':False},
        },
    },
    'split_audit': {
        'row_counts': {'train':train_count,'validation':validation_count,'test':test_count},
        'fixed_common_holdout_manifest': {'sha256':flags['--fixed-common-holdout-manifest-sha256']},
    },
    'normalization_contract': {
        'mode':'external_declared_midpoint_half_range',
        'schema':'declared_midpoint_half_range_normalization_v1',
        'sha256':flags['--fixed-normalization-contract-sha256'],
        'train_arm_specific_statistics_used':False,'large_arm_empirical_statistics_used':False,
    },
    'response_loss_contract': {
        'family':'mse','scaling':'declared_range','input_columns':flags['--input-columns'].split(','),
        'physical_spans':dict(zip(flags['--input-columns'].split(','),[2.5,2.5,20.0,0.8])),
        'standardized_dimension_weights':dict.fromkeys(flags['--input-columns'].split(','),1.0),
        'dimension_weight_mean':1.0,'balanced_mse_bni':None,'relative_mse':None,
        'q_target_semantics':'exact','target_semantics':dict.fromkeys(flags['--input-columns'].split(','),'exact'),
    },
    'optimizer_budget_contract': {
        'mode':'fixed_optimizer_updates','fingerprint_sha256':'b'*64,
        'exact_update_batch_mode':'continuous_permutation_full_batch',
        'validation_every_optimizer_updates':20,'early_stopping_enabled':False,
        'response_schedule_domain':'optimizer_update',
        'response_schedule': {
            'weight_schedule':'warmup_ramp_adaptive_ema','unit_source':'absolute_optimizer_updates',
            'total_units':1200,'warmup_units':60,'ramp_units':300,
        },
        'forward': {'target_optimizer_updates':1200,'target_real_row_draws':1200*1024},
        'inverse': {'target_optimizer_updates':1200,'target_real_row_draws':1200*1024},
        'realized': {'forward_optimizer_updates':1200,'inverse_optimizer_updates':1200,'exact_update_budget_pass':True},
    },
    'training_batch_sampler_contract': {
        'family':'row_uniform','fingerprint_sha256':'a'*64,
        'exact_update_batch_mode':'continuous_permutation_full_batch',
        'training_row_count':train_count,'draws_per_epoch':train_count,'batch_size':1024,
        'optimizer_updates_per_epoch':math.ceil(train_count/1024),
        'model_seed':int(flags['--seed']),'forward_sampler_seed':int(flags['--seed']),
        'inverse_sampler_seed':int(flags['--seed'])+1,'enabled':False,'train_only':True,
        'validation_or_test_rows_eligible_for_sampling':False,'synthetic_rows_created':False,
        'sampling_with_replacement':False,'all_exact_update_batches_have_configured_size':True,
        'realized_training_budget': {
            'forward_optimizer_updates':1200,'inverse_optimizer_updates':1200,
            'forward_real_row_draws':1200*1024,'inverse_real_row_draws':1200*1024,
        },
    },
    'best_optimizer_updates': {'forward_proxy':1200,'tandem_inverse':1200},
    'test_access_contract': {
        'test_access_event_count':0,'test_access_timing':'not_accessed',
        'test_used_for_training':False,'test_used_for_early_stopping':False,
        'test_used_for_model_or_hyperparameter_selection':False,
        'test_used_for_acceptance_threshold_tuning':False,'test_evaluator_called':False,
    },
    'evaluation_isolation': {
        'test_set_not_accessed':True,'test_set_used_only_for_post_training_evaluation':False,
    },
    'metrics': {},
    'acceptance_thresholds': {
        'configured':False,'max_forward_test_normalized_rmse':None,
        'max_tandem_response_test_normalized_rmse':None,
    },
    'stage_checkpoint_resume': {'mode':'resume_exact','enabled':True,'resumed_stage_count':0},
    'history_csv': str(history_path),
    'history_csv_sha256': sha(history_path),
    'validation_predictions_csv':str(validation_predictions),
    'validation_predictions_csv_sha256':sha(validation_predictions),
    'test_predictions_csv': str(test_predictions),
    'test_predictions_csv_sha256': sha(test_predictions),
    'weights_npz': str(weights_path),
    'weights_npz_sha256': sha(weights_path),
}
(out/'physical_feature_tandem_inverse_summary.json').write_text(
    json.dumps(summary, indent=2, sort_keys=True)+'\n', encoding='utf-8'
)
''',
        encoding="utf-8",
    )


def _case(tmp_path: Path, monkeypatch, *, hostile_normalization: bool = False):
    module = _load_module()
    material, material_complete = _write_material(
        module,
        tmp_path / "material",
        hostile_normalization=hostile_normalization,
    )
    trainer = tmp_path / "fake_trainer.py"
    _write_fake_trainer(trainer)
    test_python = _isolated_numpy_python()
    module._python_executable = lambda raw: Path(raw).expanduser().resolve()
    module.PRODUCTION_TRAINER_SHA256 = _sha(trainer)
    module.PRODUCTION_PYTHON_SHA256 = _sha(test_python)
    module.PRODUCTION_PYTHON_VERSION = module.platform.python_version()
    module.PRODUCTION_NUMPY_VERSION = module.np.__version__
    runtime_root = tmp_path / "runtime_fixture"
    runtime_root.mkdir()
    runtime_tree = runtime_root / "dependencies"
    runtime_tree.mkdir()
    runtime_manifest = runtime_root / "RUNTIME_CLOSURE.json"
    runtime_manifest.write_text("{}\n", encoding="ascii")
    runtime_bootstrap = Path(module.runtime_bootstrap.__file__).resolve()
    outer_complete = json.loads(material_complete.read_text(encoding="utf-8"))
    candidate_manifest = json.loads(
        Path(outer_complete["candidate_manifest"]["path"]).read_text(encoding="utf-8")
    )
    singleton_lock = Path(
        candidate_manifest["bindings"]["package_singleton_lock"]["path"]
    )
    runner_sha = _sha(SCRIPT)
    shared_sha = _sha(Path(module.shared_contract.__file__).resolve())
    closure = {
        "schema": module.runtime_bootstrap.RUNTIME_CLOSURE_SCHEMA,
        "manifest": {
            "path": str(runtime_manifest),
            "sha256": _sha(runtime_manifest),
            "size_bytes": runtime_manifest.stat().st_size,
        },
        "tree_root": str(runtime_tree),
        "bootstrap": {
            "path": str(runtime_bootstrap),
            "sha256": _sha(runtime_bootstrap),
            "size_bytes": runtime_bootstrap.stat().st_size,
        },
        "pure_archive": {
            "path": "pure/RUNTIME_PURE.zip",
            "sha256": "d" * 64,
            "size_bytes": 1,
            "format": "zip",
            "compression": "ZIP_STORED",
        },
        "member_count": 11,
        "native_extension_count": 0,
        "native_library_count": 0,
        "native_extensions": [],
        "native_libraries": [],
        "system_library_allowlist": list(
            module.runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST
        ),
        "python": {
            "implementation": "CPython",
            "version": module.platform.python_version(),
            "abi_tag": "synthetic-test-abi",
            "platform": "synthetic-test-platform",
            "executable_sha256": _sha(test_python),
        },
        "numpy": {"version": module.np.__version__},
        "entrypoints": {
            role: {
                "member": f"controlled_entrypoints/{role}.py",
                "sha256": _sha(trainer) if role == "trainer" else "e" * 64,
                "display_path": f"runtime/project/scripts/{role}.py",
                "role": module.runtime_bootstrap.ENTRYPOINT_ROLES[role],
            }
            for role in module.runtime_bootstrap.ENTRYPOINT_ROLES
        },
        "role_bindings": {
            "package_init_code": {"member": "rfic_transformer_inverse_design/__init__.py", "sha256": hashlib.sha256(b"").hexdigest(), "size_bytes": 0},
            "runtime_bootstrap_code": {"member": "rfic_transformer_inverse_design/controlled_real10k_20k_runtime_bootstrap.py", "sha256": _sha(runtime_bootstrap), "size_bytes": runtime_bootstrap.stat().st_size},
            "shared_contract_code": {"member": "rfic_transformer_inverse_design/controlled_real10k_20k_contract.py", "sha256": shared_sha, "size_bytes": Path(module.shared_contract.__file__).stat().st_size},
            "splitter_code": {"member": "rfic_transformer_inverse_design/model_splitting.py", "sha256": "f" * 64, "size_bytes": 1},
            "runner_code": {"member": "controlled_entrypoints/runner.py", "sha256": runner_sha, "size_bytes": SCRIPT.stat().st_size},
            "trainer_code": {"member": "controlled_entrypoints/trainer.py", "sha256": _sha(trainer), "size_bytes": trainer.stat().st_size},
            "materialization_gate_code": {"member": "controlled_entrypoints/materialization.py", "sha256": "1" * 64, "size_bytes": 1},
            "materialization_builder_code": {"member": "controlled_entrypoints/builder.py", "sha256": "2" * 64, "size_bytes": 1},
            "evaluator_code": {"member": "controlled_entrypoints/evaluator.py", "sha256": "3" * 64, "size_bytes": 1},
            "native_smoke_test": {"member": "controlled_entrypoints/smoke.py", "sha256": "4" * 64, "size_bytes": 1},
        },
        "zero_path_fallback": True,
    }

    def fake_runtime_identity(
        python, python_descriptor_identity, closure_json, expected_closure_sha,
        closure_tree, bootstrap_path, expected_bootstrap_sha,
    ):
        assert Path(python) == test_python
        assert expected_closure_sha == _sha(runtime_manifest)
        assert expected_bootstrap_sha == _sha(runtime_bootstrap)
        return {
            "python": dict(python_descriptor_identity),
            "numpy_version": module.np.__version__,
            "bootstrap": dict(closure["bootstrap"]),
            "descriptor_closure": closure,
        }

    monkeypatch.setattr(module, "_runtime_identity", fake_runtime_identity)
    monkeypatch.setattr(module, "_require_active_runtime", lambda expected: {
        "schema": module.runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
        "entrypoint": "runner",
        "manifest_sha256": expected,
        "pure_archive_sha256": closure["pure_archive"]["sha256"],
        "bootstrap_sha256": closure["bootstrap"]["sha256"],
    })
    monkeypatch.setattr(
        module.runtime_bootstrap,
        "audit_runtime_closure_paths",
        lambda *args, **kwargs: closure,
    )
    launch_state = {}

    class FakeLaunch:
        process_argv_suffix = [
            f"/proc/self/fd/{module.runtime_bootstrap.BOOTSTRAP_FD}",
            "--request-fd", str(module.runtime_bootstrap.REQUEST_FD),
            "--entrypoint", "trainer",
        ]
        pass_fds = ()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_prepare(**kwargs):
        launch_state["entrypoint_argv"] = list(kwargs["entrypoint_argv"])
        required_modules = {
            "numpy": "a" * 64,
            "rfic_transformer_inverse_design": hashlib.sha256(b"").hexdigest(),
            "rfic_transformer_inverse_design.controlled_real10k_20k_contract": shared_sha,
            "rfic_transformer_inverse_design.model_splitting": "f" * 64,
            module.runtime_bootstrap.BOOTSTRAP_MODULE: _sha(runtime_bootstrap),
        }
        module_origins = {
            name: {
                "kind": "sealed_pure_zip",
                "origin": f"descriptor-zip:/proc/self/fd/203!/{index}.py",
                "sha256": digest,
            }
            for index, (name, digest) in enumerate(required_modules.items())
        }
        common = {
            "schema": module.runtime_bootstrap.RUNTIME_ATTESTATION_SCHEMA,
            "entrypoint": "trainer",
            "manifest_sha256": closure["manifest"]["sha256"],
            "pure_archive_sha256": closure["pure_archive"]["sha256"],
            "bootstrap_sha256": closure["bootstrap"]["sha256"],
        }
        startup = {
            **common,
            "status": "PASS_DESCRIPTOR_CLOSED_STARTUP",
            "entrypoint_sha256": _sha(trainer),
            "python": {"implementation": "CPython", "version": module.platform.python_version(), "abi_tag": "test", "platform": "test"},
            "python_flags": {"isolated": 1, "no_site": 1, "dont_write_bytecode": True},
            "numpy_version": module.np.__version__,
            "module_origins": module_origins,
            "native_library_sha256": {},
            "native_extension_sha256": {},
            "system_library_allowlist": list(module.runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
            "site_initialization_disabled": True,
            "external_package_fallback_allowed": False,
        }
        terminal = {
            **common,
            "status": "PASS_DESCRIPTOR_CLOSED_TERMINAL",
            "exit_code": 0,
            "module_origins": module_origins,
            "system_library_allowlist": list(module.runtime_bootstrap.TRUSTED_SYSTEM_LIBRARY_ALLOWLIST),
            "external_package_fallback_allowed": False,
        }
        os.write(
            kwargs["attestation_output_fd"],
            (json.dumps(startup, sort_keys=True) + "\n" + json.dumps(terminal, sort_keys=True) + "\n").encode("ascii"),
        )
        return FakeLaunch()

    original_popen = module.subprocess.Popen

    def fake_popen(args, **kwargs):
        kwargs.pop("executable", None)
        kwargs.pop("pass_fds", None)
        return original_popen(
            [str(test_python), "-I", "-B", *launch_state["entrypoint_argv"]],
            **kwargs,
        )

    monkeypatch.setattr(module.runtime_bootstrap, "prepare_sealed_runtime_launch", fake_prepare)
    monkeypatch.setattr(
        module,
        "subprocess",
        types.SimpleNamespace(Popen=fake_popen, DEVNULL=subprocess.DEVNULL),
    )
    module._process_exclusivity_audit = lambda: {
        "schema": module.PROCESS_AUDIT_SCHEMA,
        "status": "PASS",
        "audit_mode": "linux_procfs_current_uid_exact",
        "uid": module.os.getuid(),
        "controller_pid": module.os.getpid(),
        "boot_id": "synthetic-test-boot-id",
        "scanned_same_uid_pid_count": 1,
        "matched_controlled_processes": [
            {
                "pid": module.os.getpid(),
                "roles": ["run_controlled_real10k_20k_paired.py"],
                "cmdline_sha256": "c" * 64,
            }
        ],
        "duplicates": [],
    }
    out_dir = tmp_path / "paired_run"
    invocations = tmp_path / "invocations.csv"
    argv = [
        "--materialization-summary", str(material),
        "--expected-materialization-summary-sha256", _sha(material),
        "--materialization-complete-receipt", str(material_complete),
        "--expected-materialization-complete-receipt-sha256", _sha(material_complete),
        "--trainer", str(trainer),
        "--expected-trainer-sha256", _sha(trainer),
        "--python-executable", str(test_python),
        "--runtime-bootstrap", str(runtime_bootstrap),
        "--expected-runtime-bootstrap-sha256", _sha(runtime_bootstrap),
        "--runtime-closure-json", str(runtime_manifest),
        "--expected-runtime-closure-json-sha256", _sha(runtime_manifest),
        "--runtime-closure-tree", str(runtime_tree),
        "--controlled-singleton-lock", str(singleton_lock),
        "--expected-controlled-singleton-lock-sha256", _sha(singleton_lock),
        "--out-dir", str(out_dir),
    ]
    return module, argv, out_dir, invocations, material


def _make_go(module, argv: list[str], out_dir: Path, tmp_path: Path, *, mutate=None, expired=False):
    contract = json.loads((out_dir / "run_contract.json").read_text(encoding="utf-8"))
    package = module._prepare_or_verify(out_dir, contract)
    now = datetime.now(timezone.utc)
    receipt = {
        "schema": module.GO_SCHEMA,
        "status": module.GO_STATUS,
        "verdict": module.GO_VERDICT,
        "scope": module.GO_SCOPE,
        "nonce": contract["qa_challenge_nonce"],
        "issued_utc": (now - (timedelta(hours=2) if expired else timedelta(minutes=1))).isoformat(),
        "expires_utc": (now - timedelta(hours=1) if expired else now + timedelta(hours=1)).isoformat(),
        "reviewer": {
            "role": "independent_qa",
            "identity": "independent-test-reviewer",
            "independent_of_builder_and_execution": True,
        },
        "findings": {"p0": 0, "p1": 0},
        "checks": {
            "result_blind_review": True,
            "materialization_closure_exact": True,
            "training_contract_exact": True,
            "validation_only_test_sealed": True,
            "six_arm_scope_only": True,
            "no_fresh_emx_authority": True,
        },
        "bindings": module._expected_go_bindings(contract, package, out_dir),
    }
    if mutate:
        mutate(receipt)
    go = tmp_path / f"external_go_{len(list(tmp_path.glob('external_go_*.json')))}.json"
    _write_json(go, receipt)
    execute = [*argv, "--phase", "execute", "--independent-qa-go-receipt", str(go), "--expected-independent-qa-go-receipt-sha256", _sha(go)]
    return execute, go


def test_prepare_freezes_package_and_never_spawns(tmp_path: Path, monkeypatch) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")))
    assert module.main(argv) == 0
    assert not invocations.exists()
    assert (out_dir / "run_contract.json").is_file()
    assert (out_dir / "INDEPENDENT_QA_REQUIRED.json").is_file()
    assert (out_dir / "receipts" / "PREPARED_RECEIPT.json").is_file()
    assert len(list((out_dir / "commands").glob("*.json"))) == 6
    assert not (out_dir / "runs").exists()


@pytest.mark.parametrize(
    "hostile_name",
    (
        "PACKAGE_BUILD_ATTEMPT_FAILED.json",
        "PACKAGE_BUILD_ATTEMPT_AMBIGUOUS.json",
        "UNBOUND_EXTRA.json",
    ),
)
def test_package_attempt_root_requires_exact_body_committed_closure(
    tmp_path: Path, hostile_name: str
) -> None:
    module = _load_module()
    _material, complete_path = _write_material(module, tmp_path / "material")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        Path(complete["candidate_manifest"]["path"]).read_text(encoding="utf-8")
    )
    bindings = manifest["bindings"]
    attempt_root = Path(bindings["package_build_attempt_body"]["path"]).parent
    attempt_root.chmod(0o755)
    hostile = attempt_root / hostile_name
    hostile.write_text("{}\n", encoding="ascii")
    hostile.chmod(0o444)
    attempt_root.chmod(0o555)
    try:
        with pytest.raises(module.ControllerError, match="root closure"):
            module._audit_package_build_attempt(bindings)
    finally:
        attempt_root.chmod(0o755)
        hostile.unlink()
        attempt_root.chmod(0o555)


def test_singleton_never_locks_symlink_and_detects_path_swap(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "singleton-target.lock"
    target.write_bytes(b"")
    linked = tmp_path / "singleton-linked.lock"
    linked.symlink_to(target)
    with pytest.raises(module.ControllerError, match="non-symlink"):
        module._open_singleton_lock(linked, _sha(target))

    descriptor, identity = module._open_singleton_lock(target, _sha(target))
    try:
        moved = tmp_path / "singleton-old.lock"
        target.rename(moved)
        target.write_bytes(b"")
        with pytest.raises(module.ControllerError, match="identity changed"):
            module._verify_singleton_lock_descriptor(descriptor, identity)
    finally:
        os.close(descriptor)


def test_python_descriptor_detects_lexical_target_swap(tmp_path: Path) -> None:
    module = _load_module()
    executable = tmp_path / "python-fixture"
    executable.write_bytes(b"first")
    executable.chmod(0o755)
    descriptor, identity = module._open_python_executable_descriptor(executable)
    try:
        executable.rename(tmp_path / "python-fixture-old")
        executable.write_bytes(b"second")
        executable.chmod(0o755)
        module._verify_python_executable_descriptor(descriptor, identity)
        with pytest.raises(module.ControllerError, match="lexical path changed"):
            module._verify_python_path_binding(identity)
    finally:
        os.close(descriptor)


def test_prepare_freezes_isolated_argv_exact_allowlisted_environment_and_go_binding(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    contract = json.loads((out_dir / "run_contract.json").read_text(encoding="utf-8"))
    expected_environment = module._effective_child_environment()
    launch = contract["process_contract"]["trainer_launch"]
    assert launch == module._trainer_launch_contract()
    assert launch["effective_environment"] == expected_environment
    assert launch["effective_environment_sha256"] == module._child_environment_sha256(
        expected_environment
    )
    assert launch["parent_environment_inherited"] is False
    assert launch["python_prefixed_environment_keys"] == []
    assert not any(key.startswith("PYTHON") for key in expected_environment)
    for command_path in sorted((out_dir / "commands").glob("*.json")):
        command = json.loads(command_path.read_text(encoding="utf-8"))
        assert command["argv"][1:4] == ["-I", "-B", "-S"]
        assert command["argv"][4] == f"/proc/self/fd/{module.runtime_bootstrap.BOOTSTRAP_FD}"
        assert command["entrypoint_argv"][0] == contract["trainer"]["path"]
        assert command["python_isolation_flags"] == ["-I", "-B", "-S"]
        assert command["effective_environment"] == expected_environment
        assert command["effective_environment_sha256"] == launch[
            "effective_environment_sha256"
        ]
    _execute, go_path = _make_go(module, argv, out_dir, tmp_path)
    go = json.loads(go_path.read_text(encoding="utf-8"))
    assert go["bindings"]["trainer_launch_contract"] == launch
    assert not invocations.exists()


def test_launch_record_rejects_missing_isolation_parent_env_and_blas_drift(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    contract = json.loads((out_dir / "run_contract.json").read_text(encoding="utf-8"))
    package = module._prepare_or_verify(out_dir, contract)
    frozen = package["commands"][0]
    seed, arm = frozen["seed"], frozen["arm"]

    missing_isolation = json.loads(json.dumps(frozen))
    missing_isolation["argv"].remove("-I")
    with pytest.raises(module.ControllerError):
        module._require_exact_launch_record(
            missing_isolation, contract, out_dir, seed, arm
        )

    python_customized = json.loads(json.dumps(frozen))
    python_customized["effective_environment"]["PYTHONPATH"] = "/hostile"
    with pytest.raises(module.ControllerError, match="environment"):
        module._require_exact_launch_record(
            python_customized, contract, out_dir, seed, arm
        )

    blas_drift = json.loads(json.dumps(frozen))
    blas_drift["effective_environment"]["OPENBLAS_NUM_THREADS"] = "99"
    with pytest.raises(module.ControllerError, match="environment"):
        module._require_exact_launch_record(blas_drift, contract, out_dir, seed, arm)
    assert not invocations.exists()


@pytest.mark.parametrize(
    ("constant", "wrong_value"),
    [
        ("PRODUCTION_TRAINER_SHA256", "0" * 64),
        ("PRODUCTION_PYTHON_SHA256", "1" * 64),
        ("PRODUCTION_PYTHON_VERSION", "0.0.0"),
        ("PRODUCTION_NUMPY_VERSION", "0.0.0"),
    ],
)
def test_wrong_production_trainer_or_runtime_identity_is_rejected_before_prepare(
    tmp_path: Path, monkeypatch, constant: str, wrong_value: str
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    setattr(module, constant, wrong_value)
    with pytest.raises(module.ControllerError, match="hard identity mismatch"):
        module.main(argv)
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()
    assert not invocations.exists()


@pytest.mark.parametrize("seeds", ["20260711", "20260712,20260711,20260713"])
def test_any_nonexact_or_reordered_seed_list_is_rejected_before_prepare(
    tmp_path: Path, monkeypatch, seeds: str
) -> None:
    module, argv, out_dir, _, _ = _case(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        module.main([*argv, "--seeds", seeds])
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()


def test_execute_requires_go_and_wrong_or_expired_go_never_spawns(tmp_path: Path, monkeypatch) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    with pytest.raises(SystemExit):
        module.main([*argv, "--phase", "execute"])
    wrong, _ = _make_go(
        module,
        argv,
        out_dir,
        tmp_path,
        mutate=lambda receipt: receipt["bindings"]["trainer"].update({"sha256": "0" * 64}),
    )
    with pytest.raises(module.ControllerError, match="bindings are not exact"):
        module.main(wrong)
    expired, _ = _make_go(module, argv, out_dir, tmp_path, expired=True)
    with pytest.raises(module.ControllerError, match="expired"):
        module.main(expired)
    assert not invocations.exists()


def test_go_extra_top_level_key_or_future_issue_time_never_spawns(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    extra, _ = _make_go(
        module,
        argv,
        out_dir,
        tmp_path,
        mutate=lambda receipt: receipt.update({"unexpected_authority": True}),
    )
    with pytest.raises(module.ControllerError, match="top-level keyset"):
        module.main(extra)

    def future_issue(receipt) -> None:
        now = datetime.now(timezone.utc)
        receipt["issued_utc"] = (now + timedelta(minutes=1)).isoformat()
        receipt["expires_utc"] = (now + timedelta(hours=1)).isoformat()

    future, _ = _make_go(module, argv, out_dir, tmp_path, mutate=future_issue)
    with pytest.raises(module.ControllerError, match="future-dated"):
        module.main(future)
    assert not invocations.exists()


def test_duplicate_same_uid_controlled_process_fails_before_attempt_and_spawn(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    safe_audit = module._process_exclusivity_audit
    audit_calls = 0
    duplicate = {
        "pid": module.os.getpid() + 1000,
        "roles": ["train_physical_feature_tandem_inverse.py"],
        "cmdline_sha256": "d" * 64,
    }
    def audit_with_late_duplicate():
        nonlocal audit_calls
        audit_calls += 1
        if audit_calls == 1:
            return safe_audit()
        return {
            "schema": module.PROCESS_AUDIT_SCHEMA,
            "status": "FAIL_DUPLICATE_CONTROLLED_PROCESS",
            "audit_mode": "linux_procfs_current_uid_exact",
            "uid": module.os.getuid(),
            "controller_pid": module.os.getpid(),
            "boot_id": "synthetic-test-boot-id",
            "scanned_same_uid_pid_count": 2,
            "matched_controlled_processes": [
                {
                    "pid": module.os.getpid(),
                    "roles": ["run_controlled_real10k_20k_paired.py"],
                    "cmdline_sha256": "c" * 64,
                },
                duplicate,
            ],
            "duplicates": [duplicate],
        }

    module._process_exclusivity_audit = audit_with_late_duplicate
    with pytest.raises(module.ControllerError, match="duplicate controlled process"):
        module.main(execute)
    assert not invocations.exists()
    assert audit_calls == 2
    assert not (out_dir / "receipts" / "seed_20260711_small" / "attempt_0001").exists()
    assert list(
        (out_dir / "receipts" / "events").glob("DUPLICATE_CONTROLLED_PROCESS_FAIL_*.json")
    )


def test_process_role_detection_uses_executed_script_not_data_arguments() -> None:
    module = _load_module()
    assert module._controlled_process_roles(
        ["/mars/python3.12", "-I", "-B", "-S", "/tool/run_controlled_real10k_20k_paired.py"]
    ) == ["run_controlled_real10k_20k_paired.py"]
    assert module._controlled_process_roles(
        [
            "/mars/python3.12",
            "/tool/run_controlled_real10k_20k_paired.py",
            "--trainer",
            "/tool/train_physical_feature_tandem_inverse.py",
        ]
    ) == ["run_controlled_real10k_20k_paired.py"]
    assert module._controlled_process_roles(
        ["shasum", "/tool/train_physical_feature_tandem_inverse.py"]
    ) == []


def test_builder_colon_cells_integrate_and_hostile_normalization_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    good_root = tmp_path / "good"
    good_root.mkdir()
    module, argv, out_dir, _, _ = _case(good_root, monkeypatch)
    assert module.main(argv) == 0
    with Path(json.loads((out_dir / "run_contract.json").read_text())["materialization"]["artifacts"]["small_csv"]["path"]).open(newline="", encoding="utf-8") as handle:
        cells = {row["controlled_physical_cell_4d"] for row in csv.DictReader(handle)}
    assert all(cell.count(":") == 3 for cell in cells)
    bad_root = tmp_path / "bad"
    bad_root.mkdir()
    bad_module, bad_argv, _, bad_invocations, _ = _case(
        bad_root, monkeypatch, hostile_normalization=True
    )
    with pytest.raises(bad_module.ControllerError, match="result-blind flags"):
        bad_module.main(bad_argv)
    assert not bad_invocations.exists()


def test_actual_builder_v2_nonproduction_fixture_cannot_bypass_runner_gate(
    tmp_path: Path, monkeypatch
) -> None:
    from tests import test_build_controlled_real10k_20k_nested as builder_test

    fixture_root = tmp_path / "builder_fixture"
    fixture_root.mkdir()
    fixture = builder_test._fixture(fixture_root)
    builder = builder_test.BUILDER
    monkeypatch.setattr(builder, "FROZEN_HISTORICAL_10K_CSV_SHA256", _sha(fixture["historical_path"]))
    monkeypatch.setattr(builder, "FROZEN_AUTHORITATIVE_100K_CSV_SHA256", _sha(fixture["authoritative_path"]))
    monkeypatch.setattr(builder, "FROZEN_HISTORICAL_MODEL_SUMMARY_SHA256", _sha(fixture["summary_path"]))
    monkeypatch.setattr(
        builder,
        "PRODUCTION_COUNTS",
        {
            "historical": len(fixture["historical_rows"]),
            "authoritative": len(fixture["authoritative_rows"]),
            "train": len(fixture["split"]["train"]),
            "validation": len(fixture["split"]["validation"]),
            "test": len(fixture["split"]["test"]),
            "extra": fixture["extra_count"],
        },
    )
    material_root = tmp_path / "builder_output"
    builder_argv = builder_test._arguments(fixture, material_root)
    builder_argv[builder_argv.index("--selection-seed") + 1] = str(
        builder.EXACT_EXTRA_SELECTION_SEED
    )
    assert builder.main(builder_argv) == 0
    module = _load_module()
    material = material_root / module.MATERIAL_SUMMARY_NAME
    summary = json.loads(material.read_text(encoding="utf-8"))
    module.EXPECTED_COUNTS = {
        "small": {
            "source_rows": summary["arm_counts"]["n10000"]["source_table_rows"],
            "gradient_train": summary["arm_counts"]["n10000"]["gradient_train_rows"],
            "validation": summary["arm_counts"]["n10000"]["validation_rows"],
            "test": summary["arm_counts"]["n10000"]["test_rows"],
        },
        "large": {
            "source_rows": summary["arm_counts"]["n20000"]["source_table_rows"],
            "gradient_train": summary["arm_counts"]["n20000"]["gradient_train_rows"],
            "validation": summary["arm_counts"]["n20000"]["validation_rows"],
            "test": summary["arm_counts"]["n20000"]["test_rows"],
        },
    }
    trainer = tmp_path / "actual_builder_fake_trainer.py"
    _write_fake_trainer(trainer)
    module.PRODUCTION_TRAINER_SHA256 = _sha(trainer)
    module.PRODUCTION_PYTHON_SHA256 = _sha(Path(sys.executable))
    module.PRODUCTION_PYTHON_VERSION = module.platform.python_version()
    module.PRODUCTION_NUMPY_VERSION = module.np.__version__
    out_dir = tmp_path / "actual_builder_prepare"
    dummy_complete = tmp_path / "actual_builder_no_outer_complete.json"
    _write_json(dummy_complete, {"status": "ABSENT_PRODUCTION_OUTER_AUTHORITY"})
    monkeypatch.setenv("PAIRED_FAKE_INVOCATIONS", str(tmp_path / "actual_builder_invocations"))
    with pytest.raises(module.ControllerError, match="production QA candidate"):
        module._audit_material(
            material,
            _sha(material),
            dummy_complete,
            _sha(dummy_complete),
            _sha(Path(module.shared_contract.__file__).resolve()),
        )
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").is_file()


def test_launch_only_attempt_is_ambiguous_and_never_duplicated(tmp_path: Path, monkeypatch) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    attempt = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001"
    attempt.mkdir(parents=True)
    _write_json(attempt / "INTENT_RECEIPT.json", {"schema": "hostile_launch_only"})
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    with pytest.raises(module.ControllerError, match="ambiguous existing attempt"):
        module.main(execute)
    assert not invocations.exists()
    assert list((out_dir / "receipts" / "events").glob("AMBIGUOUS_ATTEMPT_FAIL_*.json"))


def test_input_mutation_is_rejected_before_spawn(tmp_path: Path, monkeypatch) -> None:
    module, argv, out_dir, invocations, material = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    summary = json.loads(material.read_text(encoding="utf-8"))
    small = Path(summary["artifacts"]["arm_source_n10000.csv"]["path"])
    small.write_text(small.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(module.ControllerError, match="SHA-256 mismatch"):
        module.main(execute)
    assert not invocations.exists()


def test_execute_six_validation_only_arms_and_complete_resume_is_deep_noop(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    sitecustomize_dir = tmp_path / "hostile_pythonpath"
    sitecustomize_dir.mkdir()
    sitecustomize_marker = tmp_path / "sitecustomize_executed.marker"
    (sitecustomize_dir / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sitecustomize_marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    hostile_parent = {
        "PYTHONPATH": str(sitecustomize_dir),
        "PYTHONWARNINGS": "error",
        "PYTHONSTARTUP": str(sitecustomize_dir / "sitecustomize.py"),
        "PYTHONINSPECT": "1",
        "PYTHONDONTWRITEBYTECODE": "0",
        "PYTHONHASHSEED": "0",
        "PYTEST_ADDOPTS": "--collect-only",
        "PYTEST_PLUGINS": "hostile_unreviewed_plugin",
        "OMP_NUM_THREADS": "99",
        "OPENBLAS_NUM_THREADS": "99",
        "GOTO_NUM_THREADS": "99",
        "MKL_NUM_THREADS": "99",
        "NUMEXPR_NUM_THREADS": "99",
        "BLIS_NUM_THREADS": "99",
        "VECLIB_MAXIMUM_THREADS": "99",
        "OMP_DYNAMIC": "TRUE",
        "MKL_DYNAMIC": "TRUE",
    }
    for key, value in hostile_parent.items():
        monkeypatch.setenv(key, value)
    synthetic_audit = module._process_exclusivity_audit
    audit_checkpoints: list[int] = []

    def counted_audit():
        audit_checkpoints.append(len(audit_checkpoints))
        return synthetic_audit()

    module._process_exclusivity_audit = counted_audit
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    assert module.main(execute) == 0
    lines = invocations.read_text(encoding="utf-8").splitlines()
    assert lines == [
        f"{seed},{arm},validation_only"
        for seed in module.EXACT_PAIRED_SEEDS
        for arm in module.ARM_ORDER
    ]
    assert len(audit_checkpoints) == 12  # preflight + immediate pre-intent for all six arms
    final = json.loads((out_dir / "receipts" / "COMPLETE_RECEIPT.json").read_text())
    assert final["status"] == module.FINAL_STATUS
    assert final["test_access_event_count"] == 0
    assert final["one_time_common_test_evaluation_performed"] is False
    assert not sitecustomize_marker.exists()
    observed_path = tmp_path / "observed_child_environments.jsonl"
    observed = [json.loads(line) for line in observed_path.read_text().splitlines()]
    expected_environment = module._effective_child_environment()
    assert len(observed) == 6
    for record in observed:
        assert {
            key: record["environment"][key] for key in expected_environment
        } == expected_environment
        os_injected = set(record["environment"]) - set(expected_environment)
        assert os_injected <= ({"__CF_USER_TEXT_ENCODING"} if sys.platform == "darwin" else set())
        assert record["isolated"] == 1
        assert record["dont_write_bytecode"] is True
        assert not any(key.startswith("PYTHON") for key in record["environment"])
        assert not {"PYTEST_ADDOPTS", "PYTEST_PLUGINS"} & set(record["environment"])
        attempt = (
            out_dir
            / "receipts"
            / f"seed_{record['seed']}_{record['arm']}"
            / "attempt_0001"
        )
        intent = json.loads((attempt / "INTENT_RECEIPT.json").read_text())
        running = json.loads((attempt / "RUNNING_RECEIPT.json").read_text())
        complete = json.loads((attempt / "COMPLETE_RECEIPT.json").read_text())
        for receipt in (intent, running, complete):
            assert receipt["python_isolation_flags"] == ["-I", "-B", "-S"]
            assert receipt["effective_environment"] == expected_environment
            assert receipt["effective_environment_sha256"] == module._child_environment_sha256(
                expected_environment
            )
    for command in (out_dir / "commands").glob("*.json"):
        assert json.loads(command.read_text())["evaluation_mode"] == "validation_only"
    assert module.main(execute) == 0
    assert invocations.read_text(encoding="utf-8").splitlines() == lines
    assert len(audit_checkpoints) == 12  # completed deep verification never creates a new launch window


def test_trainer_summary_rejects_json_string_bool_and_numeric_type_aliases(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, _, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    assert module.main(execute) == 0
    seed = module.EXACT_PAIRED_SEEDS[0]
    arm = "small"
    contract = json.loads((out_dir / "run_contract.json").read_text(encoding="utf-8"))
    command = json.loads(
        (out_dir / "commands" / f"seed_{seed}_{arm}.json").read_text(encoding="utf-8")
    )
    summary_path = out_dir / "runs" / f"seed_{seed}" / arm / module.SUMMARY_NAME
    original = json.loads(summary_path.read_text(encoding="utf-8"))
    assert all(module._summary_checks(original, contract, command, arm, seed).values())

    hostile_cases = [
        (
            lambda value: value.update(
                {"training_count": str(value["training_count"])}
            ),
            "source_count_exact",
        ),
        (lambda value: value["arguments"].update({"seed": str(seed)}), "seed_exact"),
        (
            lambda value: value["optimizer_budget_contract"]["realized"].update(
                {"forward_optimizer_updates": "1200"}
            ),
            "realized_budget_exact",
        ),
        (
            lambda value: value["test_access_contract"].update(
                {"test_access_event_count": False}
            ),
            "validation_only_exact",
        ),
        (
            lambda value: value["model_comparison_contract"]["evaluation"].update(
                {"test_access_allowed": 0}
            ),
            "comparison_split_normalization_evaluation_exact",
        ),
        (
            lambda value: value["response_loss_contract"][
                "standardized_dimension_weights"
            ].update({module.INPUT_COLUMNS[0]: True}),
            "response_loss_contract_exact",
        ),
        (
            lambda value: value["model_comparison_contract"]["loss"].update(
                {"topology_feasibility_weight": "0.0"}
            ),
            "loss_exact",
        ),
        (
            lambda value: value["stage_checkpoint_resume"].update(
                {"resumed_stage_count": False}
            ),
            "resume_contract_first_launch",
        ),
    ]
    for mutate, failed_check in hostile_cases:
        candidate = json.loads(json.dumps(original))
        mutate(candidate)
        checks = module._summary_checks(candidate, contract, command, arm, seed)
        assert checks[failed_check] is False


def test_effective_environment_is_rebuilt_and_rejected_at_spawn_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    real_environment = module._effective_child_environment
    drift_at_spawn = False

    def environment_with_one_spawn_drift():
        environment = real_environment()
        if drift_at_spawn:
            environment["OPENBLAS_NUM_THREADS"] = "99"
        return environment

    # Remove unrelated closure re-materialization calls so the switch made
    # after INTENT is first consumed by the independent Popen-boundary rebuild.
    monkeypatch.setattr(module, "_verify_closure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_effective_child_environment", environment_with_one_spawn_drift)
    real_write_json_x = module._write_json_x

    def enable_drift_after_intent(path: Path, value) -> None:
        nonlocal drift_at_spawn
        real_write_json_x(path, value)
        if path.name == "INTENT_RECEIPT.json":
            drift_at_spawn = True

    monkeypatch.setattr(module, "_write_json_x", enable_drift_after_intent)
    spawn_called = False

    def forbidden_spawn(*_args, **_kwargs):
        nonlocal spawn_called
        spawn_called = True
        raise AssertionError("drifted environment must never be spawned")

    monkeypatch.setattr(module.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(module.ControllerError, match="environment"):
        module.main(execute)
    attempt = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001"
    assert (attempt / "INTENT_RECEIPT.json").is_file()
    assert (attempt / "FAIL_RECEIPT.json").is_file()
    assert spawn_called is False
    assert not invocations.exists()


def test_trainer_failure_receipt_blocks_retry_without_duplication(tmp_path: Path, monkeypatch) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    failure_marker = tmp_path / "fail_small.marker"
    failure_marker.write_text("synthetic failure\n", encoding="utf-8")
    with pytest.raises(module.ControllerError, match="returncode 9"):
        module.main(execute)
    first = invocations.read_text(encoding="utf-8").splitlines()
    assert first == ["20260711,small,validation_only"]
    failure = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001" / "FAIL_RECEIPT.json"
    assert json.loads(failure.read_text())["status"] == "FAIL"
    failure_marker.unlink()
    with pytest.raises(module.ControllerError, match="terminal attempt"):
        module.main(execute)
    assert invocations.read_text(encoding="utf-8").splitlines() == first


def test_complete_resume_rechecks_weights_and_rejects_unindexed_tamper(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    assert module.main(execute) == 0
    before = invocations.read_text(encoding="utf-8")
    weights = out_dir / "runs" / "seed_20260711" / "small" / module.WEIGHTS_NAME
    weights.write_bytes(weights.read_bytes() + b"tamper")
    with pytest.raises(module.ControllerError):
        module.main(execute)
    assert invocations.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "mutation",
    ["missing_builder", "extra_source", "wrong_source_sha", "arbitrary_production_check"],
)
def test_material_self_assertions_cannot_replace_exact_production_provenance(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    module, argv, out_dir, invocations, material = _case(tmp_path, monkeypatch)
    payload = json.loads(material.read_text(encoding="utf-8"))
    if mutation == "missing_builder":
        payload["implementation_identities"].pop("builder")
    elif mutation == "extra_source":
        payload["source_identities"]["caller_selected_source"] = {
            "path": payload["source_identities"]["historical_10k_csv"]["path"],
            "sha256": payload["source_identities"]["historical_10k_csv"]["sha256"],
        }
    elif mutation == "wrong_source_sha":
        payload["source_identities"]["authoritative_100k_csv"]["sha256"] = "0" * 64
    else:
        payload["production_exact_checks"] = {"caller_claims_everything_exact": True}
    _write_json(material, payload)
    sha_index = argv.index("--expected-materialization-summary-sha256") + 1
    argv[sha_index] = _sha(material)
    with pytest.raises(module.ControllerError):
        module.main(argv)
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()
    assert not invocations.exists()


@pytest.mark.parametrize(
    "mutation",
    ["candidate_manifest_binding", "output_closure", "frozen_candidate_go_closure"],
)
def test_outer_materialization_complete_must_bind_candidate_go_and_output_closure(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    complete_index = argv.index("--materialization-complete-receipt") + 1
    complete = Path(argv[complete_index])
    payload = json.loads(complete.read_text(encoding="utf-8"))
    if mutation == "candidate_manifest_binding":
        payload["candidate_manifest"]["sha256"] = "0" * 64
        payload["candidate_manifest_sha256"] = "0" * 64
    elif mutation == "output_closure":
        payload["materialization_output"]["artifact_closure"] = {}
    else:
        payload["frozen_closure_after_materialization"]["go_sha256"] = "0" * 64
    _write_json(complete, payload)
    argv[argv.index("--expected-materialization-complete-receipt-sha256") + 1] = _sha(
        complete
    )
    with pytest.raises(module.ControllerError):
        module.main(argv)
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()
    assert not invocations.exists()


def test_material_candidate_sha_index_raw_byte_drift_is_rejected_before_prepare(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    complete = json.loads(
        Path(argv[argv.index("--materialization-complete-receipt") + 1]).read_text(
            encoding="utf-8"
        )
    )
    index = Path(complete["candidate_sha_index"]["path"])
    index.write_text(index.read_text(encoding="ascii").upper(), encoding="ascii")
    with pytest.raises(module.ControllerError, match="candidate SHA index"):
        module.main(argv)
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()
    assert not invocations.exists()


def test_outer_materialization_go_requires_independent_zero_p0_p1(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    complete = Path(argv[argv.index("--materialization-complete-receipt") + 1])
    complete_payload = json.loads(complete.read_text(encoding="utf-8"))
    go_path = Path(complete_payload["materialization_go_authority"]["path"])
    go_payload = json.loads(go_path.read_text(encoding="utf-8"))
    go_payload["findings"]["p1"] = 1
    _write_json(go_path, go_payload)
    new_go_sha = _sha(go_path)
    complete_payload["go_sha256"] = new_go_sha
    complete_payload["materialization_go_authority"]["sha256"] = new_go_sha
    complete_payload["frozen_closure_after_materialization"]["go_sha256"] = new_go_sha
    complete_payload["execution_precursor_closure"] = {
        key: value
        for key, value in module._artifact_snapshot(complete.parent).items()
        if key != complete.name
    }
    _write_json(complete, complete_payload)
    argv[argv.index("--expected-materialization-complete-receipt-sha256") + 1] = _sha(
        complete
    )
    with pytest.raises(module.ControllerError, match="zero-finding"):
        module.main(argv)
    assert not (out_dir / "receipts" / "PREPARED_RECEIPT.json").exists()
    assert not invocations.exists()


@pytest.mark.parametrize("mutation", ["missing", "p1_nonzero", "extra_finding"])
def test_training_go_requires_exact_zero_p0_p1_findings(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0

    def mutate(receipt) -> None:
        if mutation == "missing":
            receipt.pop("findings")
        elif mutation == "p1_nonzero":
            receipt["findings"]["p1"] = 1
        else:
            receipt["findings"]["p2"] = 0

    execute, _ = _make_go(module, argv, out_dir, tmp_path, mutate=mutate)
    with pytest.raises(module.ControllerError):
        module.main(execute)
    assert not invocations.exists()


def test_training_go_exactly_binds_material_qa_required_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0

    def mutate(receipt) -> None:
        receipt["bindings"]["materialization_independent_qa_required"]["sha256"] = "0" * 64

    execute, _ = _make_go(module, argv, out_dir, tmp_path, mutate=mutate)
    with pytest.raises(module.ControllerError, match="bindings are not exact"):
        module.main(execute)
    assert not invocations.exists()


def test_package_sha_index_raw_bytes_are_rechecked_before_every_arm_spawn(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    original_popen = module.subprocess.Popen
    spawn_count = 0

    def popen_then_drift_index(*args, **kwargs):
        nonlocal spawn_count
        process = original_popen(*args, **kwargs)
        spawn_count += 1
        if spawn_count == 1:
            index = out_dir / "SHA256SUMS.txt"
            lines = []
            for line in index.read_text(encoding="ascii").splitlines():
                digest, relative = line.split("  ", 1)
                lines.append(f"{digest.upper()}  {relative}\n")
            index.write_text("".join(lines), encoding="ascii")
        return process

    monkeypatch.setattr(module.subprocess, "Popen", popen_then_drift_index)
    with pytest.raises(
        module.ControllerError,
        match="GO bindings are not exact|not a lowercase SHA-256",
    ):
        module.main(execute)
    assert spawn_count == 1
    assert invocations.read_text(encoding="utf-8").splitlines() == [
        "20260711,small,validation_only"
    ]


def test_package_index_drift_after_intent_is_caught_at_exact_spawn_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    real_write_json_x = module._write_json_x

    def write_then_drift(path: Path, value) -> None:
        real_write_json_x(path, value)
        if path.name == "INTENT_RECEIPT.json":
            index = out_dir / "SHA256SUMS.txt"
            lines = []
            for line in index.read_text(encoding="ascii").splitlines():
                digest, relative = line.split("  ", 1)
                lines.append(f"{digest.upper()}  {relative}\n")
            index.write_text("".join(lines), encoding="ascii")

    monkeypatch.setattr(module, "_write_json_x", write_then_drift)
    spawn_called = False

    def forbidden_spawn(*_args, **_kwargs):
        nonlocal spawn_called
        spawn_called = True
        raise AssertionError("spawn must remain sealed")

    monkeypatch.setattr(module.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(module.ControllerError):
        module.main(execute)
    attempt = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001"
    assert (attempt / "INTENT_RECEIPT.json").is_file()
    assert (attempt / "FAIL_RECEIPT.json").is_file()
    assert spawn_called is False
    assert not invocations.exists()


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(subprocess.SubprocessError("synthetic Popen failure"), id="SubprocessError"),
        pytest.param(SystemExit(73), id="SystemExit"),
    ],
)
def test_every_catchable_baseexception_after_intent_writes_durable_fail(
    tmp_path: Path, monkeypatch, raised: BaseException
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    fsynced: list[Path] = []
    real_fsync_dir = module._fsync_dir

    def recording_fsync(path: Path) -> None:
        fsynced.append(Path(path))
        real_fsync_dir(Path(path))

    monkeypatch.setattr(module, "_fsync_dir", recording_fsync)

    def hostile_popen(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(module.subprocess, "Popen", hostile_popen)
    with pytest.raises(module.ControllerError):
        module.main(execute)
    attempt = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001"
    failure = attempt / "FAIL_RECEIPT.json"
    payload = json.loads(failure.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["details"]["exception_type"] == type(raised).__name__
    assert attempt in fsynced
    assert attempt.parent in fsynced
    assert not invocations.exists()


def test_post_intent_failure_never_overwrites_an_existing_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    module, argv, out_dir, invocations, _ = _case(tmp_path, monkeypatch)
    assert module.main(argv) == 0
    execute, _ = _make_go(module, argv, out_dir, tmp_path)
    terminal_bytes = b'{"preexisting_terminal":true}\n'

    def hostile_popen(*_args, **_kwargs):
        attempt = out_dir / "receipts" / "seed_20260711_small" / "attempt_0001"
        (attempt / "FAIL_RECEIPT.json").write_bytes(terminal_bytes)
        raise subprocess.SubprocessError("do not overwrite terminal")

    monkeypatch.setattr(module.subprocess, "Popen", hostile_popen)
    with pytest.raises(module.ControllerError):
        module.main(execute)
    failure = (
        out_dir
        / "receipts"
        / "seed_20260711_small"
        / "attempt_0001"
        / "FAIL_RECEIPT.json"
    )
    assert failure.read_bytes() == terminal_bytes
    assert not invocations.exists()
