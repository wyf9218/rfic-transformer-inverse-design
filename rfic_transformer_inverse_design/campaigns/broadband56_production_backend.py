"""Fail-closed identity and receipt contracts for the broadband56 backend.

The private production backend is the only component allowed to invoke
Cadence, Calibre, and EMX after an exact FULL_CAMPAIGN approval.  This module
contains validation only: it never starts a process and never writes a file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .broadband56_balanced200k import CAMPAIGN_ID
from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT, STAGES
from .broadband56_full_campaign_authorization import (
    PORT_AND_GROUNDING_CONTRACT,
    PRODUCTION_BACKEND_ID,
    expected_frequency_contract,
    expected_geometry_contract,
    expected_stage_contract,
    expected_terminal_contract,
)
from .broadband56_stage_execution import (
    StageExecutionProfileError,
    read_execution_profile,
    validate_execution_profile,
)


BACKEND_MANIFEST_SCHEMA = "rfic_transformer.broadband56_v2_private_backend_identity.v6"
BACKEND_VERIFICATION_SCHEMA = (
    "rfic_transformer.broadband56_v2_private_backend_identity_verification.v2"
)
BACKEND_VERIFICATION_PASS_DECISION = "USE_HASH_BOUND_PRODUCTION_BACKEND"
BACKEND_MANIFEST_EFFECT = "IDENTITY_ONLY_NO_EXECUTION"
STAGE_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_stage_receipt.v2"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

BACKEND_VERIFICATION_PASS_CHECKS = {
    "manifest_parsed": True,
    "manifest_contract_complete": True,
    "all_named_files_exist": True,
    "all_named_file_sizes_match": True,
    "all_named_file_sha256_values_match": True,
    "stage_execution_profile_reparsed_and_validated": True,
    "all_required_executables_are_executable": True,
    "all_stage_commands_hash_bound": True,
    "all_stage_commands_shell_free": True,
    "all_ordered_stages_present": True,
    "simulator_action_taken": False,
}

ALLOWED_STAGE_PLACEHOLDERS = (
    "{stage}",
    "{cumulative_target}",
    "{campaign_root}",
    "{backend_out_dir}",
    "{full_campaign_receipt}",
    "{backend_identity_manifest}",
    "{resource_snapshot}",
    "{max_concurrency}",
)

STAGE_COMMAND_ARGUMENTS = (
    ("--stage", "{stage}"),
    ("--cumulative-target", "{cumulative_target}"),
    ("--campaign-root", "{campaign_root}"),
    ("--backend-out-dir", "{backend_out_dir}"),
    ("--full-campaign-receipt", "{full_campaign_receipt}"),
    ("--backend-identity-manifest", "{backend_identity_manifest}"),
    ("--resource-snapshot", "{resource_snapshot}"),
    ("--max-concurrency", "{max_concurrency}"),
)

PRODUCTION_CHAIN = (
    "deterministic_geometry_acquisition",
    "analytical_geometry_validation",
    "topology_validation",
    "cadence_gds_generation",
    "gds_identity_audit",
    "calibre_zero_blocking_drc",
    "calibre_to_emx_exact_gds_identity_binding",
    "fresh_real_emx",
    "exact_four_port_s4p_qa",
    "s_to_z_conversion",
    "broadband_feature_extraction",
    "attempt_ledger_finalization",
    "checkpoint_audit",
)

REQUIRED_SCRIPT_ROLES = (
    "queue_controller",
    "stage_launcher",
    "production_stage_backend",
    "phase_a_queue_builder",
    "adaptive_candidate_pool_builder",
    "acquisition_ensemble_trainer",
    "acquisition_predictor",
    "adaptive_candidate_selector",
    "adaptive_round_stager",
    "cadence_streamout_runner",
    "candidate_gds_index_builder",
    "gds_physical_identity_auditor",
    "gds_physical_identity_module",
    "calibre_runner",
    "calibre_zero_blocking_receipt_builder",
    "exact_audited_gds_emx_runner",
    "exact_audited_gds_emx_module",
    "full_band_s4p_qa_builder",
    "full_band_s4p_qa_module",
    "raw_products_finalizer",
    "checkpoint_auditor",
    "campaign_histories_finalizer",
    "training_readiness_finalizer",
    "checkpoint_figure_renderer",
    "final_delivery_auditor",
)

REQUIRED_RUNTIME_ROLES = (
    "private_configuration",
    "stage_execution_profile",
    "emx_wrapper",
    "emx_process_file",
    "cadence_layout_generator",
    "calibre_rule_deck",
)

PREPARATION_BINDING_FIELDS = (
    "preparation_receipt_sha256",
    "private_configuration_sha256",
    "historical_configuration_sha256",
    "operational_policy_approval_receipt_sha256",
)

LABEL_CONTRACT = {
    "final_label_source": "FRESH_REAL_EMX_ONLY",
    "proxy_may_rank_candidates_only": True,
    "proxy_as_label_forbidden": True,
    "historical_label_reuse_forbidden": True,
    "frequency_interpolation_forbidden": True,
    "failed_or_duplicate_geometry_counted_as_accepted": False,
    "cadence_required": True,
    "zero_blocking_calibre_required": True,
    "geometry_to_s4p_hash_chain_required": True,
    "calibre_audited_gds_must_equal_emx_input_bytes": True,
    "cadence_or_gds_regeneration_after_calibre_forbidden": True,
    "exact_audited_gds_emx_receipt_required": True,
}

STAGE_GATE_FIELDS = (
    "analytical_geometry_gate_complete",
    "topology_gate_complete",
    "cadence_gds_gate_complete",
    "gds_identity_gate_complete",
    "calibre_zero_blocking_gate_complete",
    "calibre_to_emx_exact_gds_identity_gate_complete",
    "fresh_real_emx_gate_complete",
    "exact_four_port_s4p_gate_complete",
    "s_to_z_gate_complete",
    "broadband_feature_extraction_gate_complete",
    "provenance_hash_chain_gate_complete",
    "proxy_labels_excluded",
)

STAGE_ARTIFACT_FIELDS = (
    "stage_execution_trace",
    "attempt_ledger",
    "accepted_geometry_index",
    "rejected_geometry_index",
    "s4p_artifact_index",
    "broadband_features_manifest",
    "failure_funnel",
    "exact_gds_emx_receipt_index",
    "raw_products_receipt",
    "checkpoint_receipt",
    "checkpoint_sha256s",
    "checkpoint_status",
    "coverage_summary",
    "resource_summary",
)

TERMINAL_STAGE_ARTIFACT_FIELDS = (
    "campaign_history_receipt",
    "training_readiness_receipt",
    "checkpoint_figure_receipt",
    "final_delivery_receipt",
)

FAILURE_ACCOUNTING_FIELDS = (
    "raw_geometry_candidates",
    "duplicate_candidates",
    "geometry_bound_failures",
    "analytical_failures",
    "topology_failures",
    "cadence_failures",
    "calibre_blocking_failures",
    "emx_failures",
    "incomplete_frequency_failures",
    "s4p_parsing_failures",
    "s_to_z_failures",
    "feature_extraction_failures",
    "accepted_geometries",
)


def validate_backend_identity_manifest(
    manifest: Mapping[str, Any],
    *,
    verify_files: bool,
) -> list[str]:
    """Return every private-backend manifest violation."""

    errors: list[str] = []
    _require_equal(errors, "schema", manifest.get("schema"), BACKEND_MANIFEST_SCHEMA)
    _require_equal(errors, "campaign_id", manifest.get("campaign_id"), CAMPAIGN_ID)
    _require_equal(
        errors,
        "contract_fingerprint_sha256",
        manifest.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _require_equal(
        errors,
        "backend_id",
        manifest.get("backend_id"),
        PRODUCTION_BACKEND_ID,
    )
    _require_equal(
        errors,
        "manifest_effect",
        manifest.get("manifest_effect"),
        BACKEND_MANIFEST_EFFECT,
    )
    _require_equal(
        errors,
        "simulator_action_taken",
        manifest.get("simulator_action_taken"),
        False,
    )
    _require_equal(
        errors,
        "private_paths_published",
        manifest.get("private_paths_published"),
        False,
    )
    _require_equal(
        errors,
        "no_clobber_required",
        manifest.get("no_clobber_required"),
        True,
    )
    _require_equal(
        errors,
        "execution_chain",
        manifest.get("execution_chain"),
        list(PRODUCTION_CHAIN),
    )

    scientific = manifest.get("scientific_contract")
    if not isinstance(scientific, Mapping):
        errors.append("scientific_contract must be an object")
    else:
        _require_equal(
            errors,
            "scientific_contract.frequency_contract",
            scientific.get("frequency_contract"),
            expected_frequency_contract(),
        )
        _require_equal(
            errors,
            "scientific_contract.geometry_contract",
            scientific.get("geometry_contract"),
            expected_geometry_contract(),
        )
        _require_equal(
            errors,
            "scientific_contract.port_and_grounding_contract",
            scientific.get("port_and_grounding_contract"),
            PORT_AND_GROUNDING_CONTRACT,
        )
        _require_equal(
            errors,
            "scientific_contract.label_contract",
            scientific.get("label_contract"),
            LABEL_CONTRACT,
        )
        _require_equal(
            errors,
            "scientific_contract.terminal_contract",
            scientific.get("terminal_contract"),
            expected_terminal_contract(),
        )
        _require_equal(
            errors,
            "scientific_contract.ordered_stages",
            scientific.get("ordered_stages"),
            expected_stage_contract(),
        )

    preparation = manifest.get("preparation_bindings")
    if not isinstance(preparation, Mapping):
        errors.append("preparation_bindings must be an object")
    else:
        for field in PREPARATION_BINDING_FIELDS:
            if not _is_sha256(preparation.get(field)):
                errors.append(f"preparation_bindings.{field} is not SHA-256")

    scripts = manifest.get("script_identities")
    _validate_identity_records(
        errors,
        scripts,
        required_roles=REQUIRED_SCRIPT_ROLES,
        label="script_identities",
        verify_files=verify_files,
    )
    runtimes = manifest.get("runtime_identities")
    _validate_identity_records(
        errors,
        runtimes,
        required_roles=REQUIRED_RUNTIME_ROLES,
        label="runtime_identities",
        verify_files=verify_files,
    )
    _validate_stage_execution_profile_identity(
        errors,
        manifest=manifest,
        runtimes=runtimes,
        verify_file=verify_files,
    )

    _validate_stage_commands(errors, manifest.get("stage_commands"), scripts=scripts)
    _validate_historical_receipts(
        errors,
        manifest,
        verify_files=verify_files,
    )
    return errors


def validate_stage_receipt(
    receipt: Mapping[str, Any],
    *,
    stage: str,
    cumulative_target: int,
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
    prior_stage_receipt_sha256: str | None,
    verify_artifacts: bool,
    artifact_root: Path | None = None,
) -> list[str]:
    """Return every terminal-stage receipt violation."""

    errors: list[str] = []
    stage_name = str(stage).upper()
    specs = {item.name: item for item in STAGES}
    spec = specs.get(stage_name)
    if spec is None:
        return [f"unknown stage: {stage_name}"]
    _require_equal(errors, "schema", receipt.get("schema"), STAGE_RECEIPT_SCHEMA)
    _require_equal(errors, "overall_status", receipt.get("overall_status"), "PASS")
    _require_equal(errors, "decision", receipt.get("decision"), "ACCEPT_STAGE")
    _require_equal(errors, "campaign_id", receipt.get("campaign_id"), CAMPAIGN_ID)
    _require_equal(
        errors,
        "contract_fingerprint_sha256",
        receipt.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _require_equal(
        errors,
        "backend_id",
        receipt.get("backend_id"),
        PRODUCTION_BACKEND_ID,
    )
    _require_equal(errors, "stage", receipt.get("stage"), stage_name)
    _require_equal(
        errors,
        "terminal_state",
        receipt.get("terminal_state"),
        spec.receipt_status,
    )
    _require_equal(
        errors,
        "cumulative_target",
        receipt.get("cumulative_target"),
        cumulative_target,
    )
    _require_equal(
        errors,
        "accepted_unique_geometries",
        receipt.get("accepted_unique_geometries"),
        cumulative_target,
    )
    _require_equal(
        errors,
        "backend_identity_manifest_sha256",
        receipt.get("backend_identity_manifest_sha256"),
        backend_manifest_sha256,
    )
    _require_equal(
        errors,
        "full_campaign_authorization_receipt_sha256",
        receipt.get("full_campaign_authorization_receipt_sha256"),
        authorization_receipt_sha256,
    )
    _require_equal(
        errors,
        "prior_stage_receipt_sha256",
        receipt.get("prior_stage_receipt_sha256"),
        prior_stage_receipt_sha256,
    )
    _require_equal(
        errors,
        "frequency_contract",
        receipt.get("frequency_contract"),
        expected_frequency_contract(),
    )
    _require_equal(
        errors,
        "port_and_grounding_contract",
        receipt.get("port_and_grounding_contract"),
        PORT_AND_GROUNDING_CONTRACT,
    )
    _require_equal(
        errors,
        "label_source",
        receipt.get("label_source"),
        "FRESH_REAL_EMX_ONLY",
    )

    counts = receipt.get("counts")
    expected_rows = int(cumulative_target) * 56
    expected_counts = {
        "accepted_unique_geometries": int(cumulative_target),
        "valid_s4p_geometries": int(cumulative_target),
        "feature_complete_geometries": int(cumulative_target),
        "s4p_artifacts": int(cumulative_target),
        "independent_designs": int(cumulative_target),
        "geometry_frequency_rows": expected_rows,
    }
    if not isinstance(counts, Mapping):
        errors.append("counts must be an object")
    else:
        for field, expected in expected_counts.items():
            _require_equal(errors, f"counts.{field}", counts.get(field), expected)
        broad_rows = _nonnegative_int(
            errors,
            counts.get("broadband_descriptor_valid_rows"),
            "counts.broadband_descriptor_valid_rows",
        )
        strict_rows = _nonnegative_int(
            errors,
            counts.get("strict_lumped_valid_rows"),
            "counts.strict_lumped_valid_rows",
        )
        if broad_rows is not None and broad_rows > expected_rows:
            errors.append(
                "counts.broadband_descriptor_valid_rows exceeds geometry-frequency rows"
            )
        if strict_rows is not None and broad_rows is not None and strict_rows > broad_rows:
            errors.append(
                "counts.strict_lumped_valid_rows exceeds broadband-descriptor rows"
            )

    safeguards = receipt.get("safeguards")
    expected_safeguards = {
        "proxy_label_count": 0,
        "historical_label_count": 0,
        "interpolated_frequency_record_count": 0,
        "accepted_duplicate_geometry_count": 0,
        "accepted_blocking_calibre_count": 0,
        "manual_gds_modification_count": 0,
        "mixed_contract_fingerprint_count": 0,
    }
    _require_equal(errors, "safeguards", safeguards, expected_safeguards)

    gates = receipt.get("gates")
    if not isinstance(gates, Mapping):
        errors.append("gates must be an object")
    else:
        for field in STAGE_GATE_FIELDS:
            if gates.get(field) is not True:
                errors.append(f"gates.{field} must be true")

    _validate_failure_accounting(errors, receipt.get("failure_accounting"), cumulative_target)
    artifacts = receipt.get("artifacts")
    _validate_identity_records(
        errors,
        artifacts,
        required_roles=stage_artifact_fields(stage_name),
        label="artifacts",
        verify_files=verify_artifacts,
    )
    _validate_artifact_root(
        errors,
        artifacts,
        artifact_root=artifact_root,
    )
    if isinstance(artifacts, Mapping):
        raw_record = artifacts.get("raw_products_receipt")
        if isinstance(raw_record, Mapping):
            _validate_raw_products_receipt(
                errors,
                raw_record,
                cumulative_target=cumulative_target,
                verify_file=verify_artifacts,
            )
        checkpoint_record = artifacts.get("checkpoint_receipt")
        if isinstance(checkpoint_record, Mapping):
            _validate_checkpoint_receipt(
                errors,
                checkpoint_record,
                cumulative_target=cumulative_target,
                verify_file=verify_artifacts,
            )
        if stage_name == "PHASE_C":
            _validate_terminal_receipts(
                errors,
                artifacts,
                verify_files=verify_artifacts,
            )
    return errors


def stage_artifact_fields(stage: str) -> tuple[str, ...]:
    """Return the exact artifact-role set for one completed stage."""

    stage_name = str(stage).upper()
    if stage_name not in {item.name for item in STAGES}:
        raise ValueError(f"unknown stage: {stage_name}")
    if stage_name == "PHASE_C":
        return (*STAGE_ARTIFACT_FIELDS, *TERMINAL_STAGE_ARTIFACT_FIELDS)
    return STAGE_ARTIFACT_FIELDS


def validate_stage_receipt_chain(
    receipt_records: Sequence[tuple[Path, Mapping[str, Any]]],
    *,
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
    verify_artifacts: bool,
) -> list[str]:
    """Validate every completed stage in exact order and exact-SHA linkage."""

    errors: list[str] = []
    if len(receipt_records) > len(STAGES):
        return ["stage receipt chain contains too many records"]
    prior_sha256: str | None = None
    for index, (receipt_path, receipt) in enumerate(receipt_records):
        expected = STAGES[index]
        path = Path(receipt_path).expanduser().resolve()
        if not path.is_file():
            errors.append(f"stage_chain.{expected.name}.receipt_path is missing")
            prior_sha256 = None
        stage_errors = validate_stage_receipt(
            receipt,
            stage=expected.name,
            cumulative_target=expected.cumulative_target,
            backend_manifest_sha256=backend_manifest_sha256,
            authorization_receipt_sha256=authorization_receipt_sha256,
            prior_stage_receipt_sha256=prior_sha256,
            verify_artifacts=verify_artifacts,
            artifact_root=path.parent / "backend",
        )
        errors.extend(
            f"stage_chain.{expected.name}: {error}" for error in stage_errors
        )
        prior_sha256 = _sha256(path) if path.is_file() else None
    return errors


def _validate_stage_commands(
    errors: list[str],
    value: Any,
    *,
    scripts: Any,
) -> None:
    if not isinstance(value, Mapping):
        errors.append("stage_commands must be an object")
        return
    expected_stages = {item.name for item in STAGES}
    if set(value) != expected_stages:
        errors.append("stage_commands keys do not exactly match the ordered stages")
    script_records = scripts if isinstance(scripts, Mapping) else {}
    for stage in sorted(expected_stages):
        record = value.get(stage)
        if not isinstance(record, Mapping):
            errors.append(f"stage_commands.{stage} must be an object")
            continue
        argv = record.get("argv")
        if not (
            isinstance(argv, list)
            and argv
            and all(isinstance(item, str) and item for item in argv)
        ):
            errors.append(f"stage_commands.{stage}.argv is invalid")
            continue
        _require_equal(
            errors,
            f"stage_commands.{stage}.shell_used",
            record.get("shell_used"),
            False,
        )
        _require_equal(
            errors,
            f"stage_commands.{stage}.identity_role",
            record.get("identity_role"),
            "production_stage_backend",
        )
        placeholders = sorted(set(_placeholder_tokens("\n".join(argv))))
        if placeholders != sorted(ALLOWED_STAGE_PLACEHOLDERS):
            errors.append(f"stage_commands.{stage} placeholder contract mismatch")
        index = record.get("identity_argv_index")
        if index != 0:
            errors.append(
                f"stage_commands.{stage}.identity_argv_index must be zero"
            )
            continue
        identity = script_records.get("production_stage_backend")
        if not isinstance(identity, Mapping):
            continue
        if argv[index] != identity.get("path"):
            errors.append(f"stage_commands.{stage} identity path mismatch")
        if record.get("identity_sha256") != identity.get("sha256"):
            errors.append(f"stage_commands.{stage} identity SHA-256 mismatch")
        expected_argv = [
            str(identity.get("path")),
            *[
                item
                for flag, placeholder in STAGE_COMMAND_ARGUMENTS
                for item in (flag, placeholder)
            ],
        ]
        if argv != expected_argv:
            errors.append(f"stage_commands.{stage} argv interface mismatch")


def _validate_identity_records(
    errors: list[str],
    value: Any,
    *,
    required_roles: Sequence[str],
    label: str,
    verify_files: bool,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return
    missing = set(required_roles) - set(value)
    if missing:
        errors.append(f"{label} lacks roles: {sorted(missing)}")
    unexpected = set(value) - set(required_roles)
    if unexpected:
        errors.append(f"{label} has unexpected roles: {sorted(unexpected)}")
    for role in required_roles:
        record = value.get(role)
        if not isinstance(record, Mapping):
            continue
        path_text = record.get("path")
        digest = record.get("sha256")
        size = record.get("size_bytes")
        if (
            not isinstance(path_text, str)
            or not path_text
            or not Path(path_text).is_absolute()
        ):
            errors.append(f"{label}.{role}.path must be absolute")
            continue
        if not _is_sha256(digest):
            errors.append(f"{label}.{role}.sha256 is not SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append(f"{label}.{role}.size_bytes is invalid")
        must_be_executable = (
            label == "script_identities" and role == "production_stage_backend"
        ) or (label == "runtime_identities" and role == "emx_wrapper")
        if must_be_executable and record.get("executable") is not True:
            errors.append(f"{label}.{role}.executable must be true")
        if not verify_files:
            continue
        path = Path(path_text).expanduser().resolve()
        if not path.is_file():
            errors.append(f"{label}.{role}.path is missing")
            continue
        if path.stat().st_size != size:
            errors.append(f"{label}.{role}.size_bytes mismatches file")
        if _is_sha256(digest) and _sha256(path) != digest:
            errors.append(f"{label}.{role}.sha256 mismatches file")
        if must_be_executable and not os.access(path, os.X_OK):
            errors.append(f"{label}.{role}.path is not executable")


def _validate_stage_execution_profile_identity(
    errors: list[str],
    *,
    manifest: Mapping[str, Any],
    runtimes: Any,
    verify_file: bool,
) -> None:
    if not verify_file or not isinstance(runtimes, Mapping):
        return
    record = runtimes.get("stage_execution_profile")
    if not isinstance(record, Mapping):
        return
    path_text = record.get("path")
    digest = record.get("sha256")
    size = record.get("size_bytes")
    if (
        not isinstance(path_text, str)
        or not path_text
        or not Path(path_text).is_absolute()
        or not _is_sha256(digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        return
    path = Path(path_text).expanduser().resolve()
    if not path.is_file() or path.stat().st_size != size or _sha256(path) != digest:
        return
    before = path.stat()
    try:
        profile = read_execution_profile(path)
    except StageExecutionProfileError as exc:
        errors.append(f"runtime_identities.stage_execution_profile is invalid: {exc}")
        return
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or _sha256(path) != digest:
        errors.append("runtime_identities.stage_execution_profile changed while validating")
        return
    profile_errors = validate_execution_profile(
        profile,
        backend_manifest=manifest,
    )
    errors.extend(
        f"runtime_identities.stage_execution_profile: {error}"
        for error in profile_errors
    )


def _validate_artifact_root(
    errors: list[str],
    artifacts: Any,
    *,
    artifact_root: Path | None,
) -> None:
    if artifact_root is None:
        return
    root = Path(artifact_root).expanduser().resolve()
    if not root.is_dir():
        errors.append(f"artifact_root is missing: {root}")
        return
    if not isinstance(artifacts, Mapping):
        return
    expected_roles = set(STAGE_ARTIFACT_FIELDS) | set(TERMINAL_STAGE_ARTIFACT_FIELDS)
    for role in expected_roles:
        record = artifacts.get(role)
        if not isinstance(record, Mapping):
            continue
        path_text = record.get("path")
        if not isinstance(path_text, str) or not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"artifacts.{role}.path escapes the stage artifact root")


def _validate_historical_receipts(
    errors: list[str],
    manifest: Mapping[str, Any],
    *,
    verify_files: bool,
) -> None:
    receipts = manifest.get("historical_backend_pass_receipts")
    if not isinstance(receipts, list) or len(receipts) < 2:
        errors.append("historical_backend_pass_receipts must contain at least two records")
    else:
        receipt_paths: list[str] = []
        receipt_hashes: list[str] = []
        for index, record in enumerate(receipts):
            if not isinstance(record, Mapping):
                errors.append(f"historical backend receipt {index} is not an object")
                continue
            label = f"historical_backend_pass_receipts.{index}"
            _validate_pass_receipt_identity_record(
                errors,
                record,
                label=label,
                verify_file=verify_files,
            )
            path_text = record.get("path")
            digest = record.get("sha256")
            if isinstance(path_text, str):
                receipt_paths.append(path_text)
            if isinstance(digest, str):
                receipt_hashes.append(digest)
        if len(receipt_paths) == len(receipts) and len(set(receipt_paths)) != len(
            receipt_paths
        ):
            errors.append("historical backend receipt paths must be distinct")
        if len(receipt_hashes) == len(receipts) and len(set(receipt_hashes)) != len(
            receipt_hashes
        ):
            errors.append("historical backend receipt bytes must be distinct")
    gds = manifest.get("historical_gds_identity_pass_receipt")
    if not isinstance(gds, Mapping):
        errors.append("historical_gds_identity_pass_receipt must be an object")
    else:
        _validate_pass_receipt_identity_record(
            errors,
            gds,
            label="historical_gds_identity_pass_receipt",
            verify_file=verify_files,
        )


def _validate_pass_receipt_identity_record(
    errors: list[str],
    record: Mapping[str, Any],
    *,
    label: str,
    verify_file: bool,
) -> None:
    path_text = record.get("path")
    digest = record.get("sha256")
    size = record.get("size_bytes")
    if record.get("overall_status") != "PASS":
        errors.append(f"{label}.overall_status is not PASS")
    if (
        not isinstance(path_text, str)
        or not path_text
        or not Path(path_text).is_absolute()
    ):
        errors.append(f"{label}.path must be absolute")
        return
    if not _is_sha256(digest):
        errors.append(f"{label}.sha256 is not SHA-256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        errors.append(f"{label}.size_bytes is invalid")
    if not verify_file:
        return
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        errors.append(f"{label}.path is missing")
        return
    if path.stat().st_size != size:
        errors.append(f"{label}.size_bytes mismatches file")
    if _is_sha256(digest) and _sha256(path) != digest:
        errors.append(f"{label}.sha256 mismatches file")
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{label}.file is not valid UTF-8 JSON")
        return
    if not isinstance(value, Mapping) or value.get("overall_status") != "PASS":
        errors.append(f"{label}.file is not top-level PASS")


def _validate_failure_accounting(
    errors: list[str], value: Any, cumulative_target: int
) -> None:
    if not isinstance(value, Mapping):
        errors.append("failure_accounting must be an object")
        return
    if set(value) != set(FAILURE_ACCOUNTING_FIELDS):
        errors.append("failure_accounting fields do not exactly match the frozen funnel")
        return
    counts: dict[str, int] = {}
    for field in FAILURE_ACCOUNTING_FIELDS:
        parsed = _nonnegative_int(
            errors,
            value.get(field),
            f"failure_accounting.{field}",
        )
        if parsed is not None:
            counts[field] = parsed
    if len(counts) != len(FAILURE_ACCOUNTING_FIELDS):
        return
    if counts["accepted_geometries"] != int(cumulative_target):
        errors.append("failure_accounting.accepted_geometries mismatch")
    terminal_total = sum(
        value for field, value in counts.items() if field != "raw_geometry_candidates"
    )
    if counts["raw_geometry_candidates"] != terminal_total:
        errors.append("failure_accounting does not partition raw geometry candidates")


def _validate_raw_products_receipt(
    errors: list[str],
    record: Mapping[str, Any],
    *,
    cumulative_target: int,
    verify_file: bool,
) -> None:
    path_text = record.get("path")
    if not verify_file or not isinstance(path_text, str):
        return
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        return
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append("artifacts.raw_products_receipt is not valid JSON")
        return
    if not isinstance(receipt, Mapping):
        errors.append("artifacts.raw_products_receipt is not an object")
        return
    _require_equal(
        errors,
        "raw_products.schema",
        receipt.get("schema"),
        "broadband56_raw_products_receipt_v1",
    )
    _require_equal(
        errors,
        "raw_products.overall_status",
        receipt.get("overall_status"),
        "PASS",
    )
    _require_equal(
        errors,
        "raw_products.decision",
        receipt.get("decision"),
        "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
    )
    _require_equal(
        errors,
        "raw_products.campaign_id",
        receipt.get("campaign_id"),
        CAMPAIGN_ID,
    )
    _require_equal(
        errors,
        "raw_products.contract_fingerprint_sha256",
        receipt.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    counts = receipt.get("counts")
    if not isinstance(counts, Mapping):
        errors.append("raw_products.counts must be an object")
        return
    _require_equal(
        errors,
        "raw_products.counts.accepted_geometries",
        counts.get("accepted_geometries"),
        cumulative_target,
    )
    _require_equal(
        errors,
        "raw_products.counts.s4p_artifacts",
        counts.get("s4p_artifacts"),
        cumulative_target,
    )
    _require_equal(
        errors,
        "raw_products.counts.geometry_frequency_rows",
        counts.get("geometry_frequency_rows"),
        cumulative_target * 56,
    )
    required_checks = (
        "all_accepted_s4p_are_fresh_exact_56_point_four_port",
        "long_features_bound_to_exact_s4p_s_and_z",
        "long_physical_features_recomputed_from_exact_s4p",
        "proxy_values_excluded_from_labels",
    )
    checks = receipt.get("checks")
    if not isinstance(checks, Mapping):
        errors.append("raw_products.checks must be an object")
    else:
        for field in required_checks:
            if checks.get(field) is not True:
                errors.append(f"raw_products.checks.{field} must be true")


def _validate_checkpoint_receipt(
    errors: list[str],
    record: Mapping[str, Any],
    *,
    cumulative_target: int,
    verify_file: bool,
) -> None:
    receipt = _read_artifact_json(
        errors,
        record,
        label="artifacts.checkpoint_receipt",
        verify_file=verify_file,
    )
    if receipt is None:
        return
    _require_equal(
        errors,
        "checkpoint.overall_status",
        receipt.get("overall_status"),
        "PASS",
    )
    _require_equal(
        errors,
        "checkpoint.decision",
        receipt.get("decision"),
        "USE_CHECKPOINT",
    )
    _require_equal(
        errors,
        "checkpoint.campaign_id",
        receipt.get("campaign_id"),
        CAMPAIGN_ID,
    )
    _require_equal(
        errors,
        "checkpoint.contract_fingerprint_sha256",
        receipt.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _require_equal(
        errors,
        "checkpoint.expected_accepted",
        receipt.get("expected_accepted"),
        cumulative_target,
    )
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checkpoint.checks must be a nonempty list")
    else:
        for index, check in enumerate(checks):
            if not isinstance(check, Mapping) or check.get("pass") is not True:
                errors.append(f"checkpoint.checks.{index} is not PASS")


def _validate_terminal_receipts(
    errors: list[str],
    artifacts: Mapping[str, Any],
    *,
    verify_files: bool,
) -> None:
    expectations = {
        "campaign_history_receipt": "USE_AS_AUDITED_CAMPAIGN_HISTORY",
        "training_readiness_receipt": (
            "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY"
        ),
        "checkpoint_figure_receipt": "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES",
        "final_delivery_receipt": (
            "REPORT_COMPLETE_200K_WITH_SEPARATE_COVERAGE_STATUS"
        ),
    }
    for role, decision in expectations.items():
        record = artifacts.get(role)
        if not isinstance(record, Mapping):
            continue
        receipt = _read_artifact_json(
            errors,
            record,
            label=f"artifacts.{role}",
            verify_file=verify_files,
        )
        if receipt is None:
            continue
        _require_equal(
            errors,
            f"{role}.overall_status",
            receipt.get("overall_status"),
            "PASS",
        )
        _require_equal(
            errors,
            f"{role}.decision",
            receipt.get("decision"),
            decision,
        )
        _require_equal(
            errors,
            f"{role}.campaign_id",
            receipt.get("campaign_id"),
            CAMPAIGN_ID,
        )
        _require_equal(
            errors,
            f"{role}.contract_fingerprint_sha256",
            receipt.get("contract_fingerprint_sha256"),
            SCIENTIFIC_CONTRACT_FINGERPRINT,
        )
        if role == "final_delivery_receipt":
            _require_equal(
                errors,
                "final_delivery_receipt.execution_completion",
                receipt.get("execution_completion"),
                "COMPLETE_200K",
            )
            counts = receipt.get("terminal_counts")
            if not isinstance(counts, Mapping):
                errors.append("final_delivery_receipt.terminal_counts must be an object")
            else:
                _require_equal(
                    errors,
                    "final_delivery_receipt.terminal_counts.accepted_geometries",
                    counts.get("accepted_geometries"),
                    200_000,
                )
                _require_equal(
                    errors,
                    "final_delivery_receipt.terminal_counts.s4p_artifacts",
                    counts.get("s4p_artifacts"),
                    200_000,
                )
                _require_equal(
                    errors,
                    "final_delivery_receipt.terminal_counts.geometry_frequency_rows",
                    counts.get("geometry_frequency_rows"),
                    11_200_000,
                )


def _read_artifact_json(
    errors: list[str],
    record: Mapping[str, Any],
    *,
    label: str,
    verify_file: bool,
) -> Mapping[str, Any] | None:
    if not verify_file:
        return None
    path_text = record.get("path")
    if not isinstance(path_text, str) or not path_text:
        return None
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{label} is not valid UTF-8 JSON")
        return None
    if not isinstance(value, Mapping):
        errors.append(f"{label} is not an object")
        return None
    return value


def _placeholder_tokens(value: str) -> list[str]:
    return re.findall(r"\{[A-Za-z0-9_]+\}", value)


def _nonnegative_int(errors: list[str], value: Any, label: str) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{label} must be a nonnegative integer")
        return None
    return value


def _require_equal(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label} mismatch")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value.lower()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ALLOWED_STAGE_PLACEHOLDERS",
    "BACKEND_MANIFEST_EFFECT",
    "BACKEND_MANIFEST_SCHEMA",
    "BACKEND_VERIFICATION_PASS_DECISION",
    "BACKEND_VERIFICATION_PASS_CHECKS",
    "BACKEND_VERIFICATION_SCHEMA",
    "FAILURE_ACCOUNTING_FIELDS",
    "LABEL_CONTRACT",
    "PREPARATION_BINDING_FIELDS",
    "PRODUCTION_CHAIN",
    "REQUIRED_RUNTIME_ROLES",
    "REQUIRED_SCRIPT_ROLES",
    "STAGE_ARTIFACT_FIELDS",
    "STAGE_COMMAND_ARGUMENTS",
    "STAGE_GATE_FIELDS",
    "STAGE_RECEIPT_SCHEMA",
    "TERMINAL_STAGE_ARTIFACT_FIELDS",
    "stage_artifact_fields",
    "validate_backend_identity_manifest",
    "validate_stage_receipt",
    "validate_stage_receipt_chain",
]
