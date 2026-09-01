"""Fail-closed progress receipts for replenished broadband56 stage attempts.

Progress receipts are nonterminal evidence.  They preserve one bounded shard
whose accepted count is still below the current stage target.  Only a later
terminal stage receipt may advance the ordered campaign stage.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .broadband56_balanced200k import (
    CAMPAIGN_ID,
    FROZEN_INTERMEDIATE_ACCEPTED_BOUNDARIES,
)
from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT, STAGE_BY_NAME
from .broadband56_full_campaign_authorization import PRODUCTION_BACKEND_ID


STAGE_PROGRESS_SCHEMA = "rfic_transformer.broadband56_v2_stage_progress_receipt.v3"
STAGE_PROGRESS_STATUS = "INCOMPLETE"
STAGE_PROGRESS_DECISION = "CONTINUE_SAMPLING"
STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_stage_attempt_finalizer.v1"
)
STAGE_ATTEMPT_TARGET_REACHED_DECISION = "STAGE_TARGET_REACHED"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

ATTEMPT_FAILURE_ACCOUNTING_FIELDS = (
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

STAGE_PROGRESS_ARTIFACT_FIELDS = (
    "attempt_ledger",
    "accepted_geometry_increment",
    "rejected_geometry_increment",
    "exact_gds_emx_receipt_index",
    "s4p_artifact_index",
    "long_features",
    "failure_funnel",
)

STAGE_PROGRESS_SAFEGUARDS = {
    "proxy_label_count": 0,
    "historical_label_count": 0,
    "interpolated_frequency_record_count": 0,
    "accepted_duplicate_geometry_count": 0,
    "accepted_blocking_calibre_count": 0,
    "manual_gds_modification_count": 0,
    "mixed_contract_fingerprint_count": 0,
}


def validate_stage_progress_receipt(
    receipt: Mapping[str, Any],
    *,
    stage: str,
    attempt_index: int,
    accepted_before: int,
    prior_progress_receipt_sha256: str | None,
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
    verify_artifacts: bool,
    artifact_root: Path | None = None,
) -> list[str]:
    """Return every violation in one nonterminal replenishment receipt."""

    errors: list[str] = []
    stage_name = str(stage).upper()
    spec = STAGE_BY_NAME.get(stage_name)
    if spec is None:
        return [f"unknown stage: {stage_name}"]
    _equal(errors, "schema", receipt.get("schema"), STAGE_PROGRESS_SCHEMA)
    _equal(errors, "overall_status", receipt.get("overall_status"), STAGE_PROGRESS_STATUS)
    _equal(errors, "decision", receipt.get("decision"), STAGE_PROGRESS_DECISION)
    _equal(errors, "campaign_id", receipt.get("campaign_id"), CAMPAIGN_ID)
    _equal(
        errors,
        "contract_fingerprint_sha256",
        receipt.get("contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _equal(errors, "backend_id", receipt.get("backend_id"), PRODUCTION_BACKEND_ID)
    _equal(errors, "stage", receipt.get("stage"), stage_name)
    _equal(errors, "attempt_index", receipt.get("attempt_index"), attempt_index)
    _equal(errors, "cumulative_target", receipt.get("cumulative_target"), spec.cumulative_target)
    _equal(errors, "accepted_before", receipt.get("accepted_before"), accepted_before)
    _equal(
        errors,
        "prior_progress_receipt_sha256",
        receipt.get("prior_progress_receipt_sha256"),
        prior_progress_receipt_sha256,
    )
    _equal(
        errors,
        "backend_identity_manifest_sha256",
        receipt.get("backend_identity_manifest_sha256"),
        backend_manifest_sha256,
    )
    _equal(
        errors,
        "full_campaign_authorization_receipt_sha256",
        receipt.get("full_campaign_authorization_receipt_sha256"),
        authorization_receipt_sha256,
    )

    accepted_this = _nonnegative_int(
        errors, receipt.get("accepted_this_attempt"), "accepted_this_attempt"
    )
    accepted_after = _nonnegative_int(
        errors, receipt.get("accepted_after"), "accepted_after"
    )
    remaining_after = _nonnegative_int(
        errors, receipt.get("remaining_after"), "remaining_after"
    )
    raw_candidates = _positive_int(
        errors, receipt.get("raw_candidates_this_attempt"), "raw_candidates_this_attempt"
    )
    terminal_attempts = _positive_int(
        errors, receipt.get("terminal_attempts_this_attempt"), "terminal_attempts_this_attempt"
    )
    if accepted_this is not None and accepted_after is not None:
        if accepted_after != accepted_before + accepted_this:
            errors.append("accepted_after does not equal accepted_before plus accepted_this_attempt")
        if accepted_after >= spec.cumulative_target:
            errors.append("progress receipt must remain strictly below the stage target")
    if accepted_after is not None and remaining_after is not None:
        if remaining_after != spec.cumulative_target - accepted_after:
            errors.append("remaining_after does not close to the stage target")
    if raw_candidates is not None and accepted_this is not None and accepted_this > raw_candidates:
        errors.append("accepted_this_attempt exceeds raw_candidates_this_attempt")
    if raw_candidates is not None and terminal_attempts != raw_candidates:
        errors.append("terminal attempts do not partition the raw candidate shard")

    _equal(errors, "safeguards", receipt.get("safeguards"), STAGE_PROGRESS_SAFEGUARDS)
    _validate_failure_accounting(
        errors,
        receipt.get("failure_accounting"),
        raw_candidates=raw_candidates,
        accepted_this=accepted_this,
    )
    _validate_artifacts(
        errors,
        receipt.get("artifacts"),
        verify_files=verify_artifacts,
        artifact_root=artifact_root,
        label="artifacts",
    )
    round_inputs = receipt.get("round_cumulative_inputs")
    materialization_boundary = accepted_after in set(
        FROZEN_INTERMEDIATE_ACCEPTED_BOUNDARIES
    )
    if materialization_boundary:
        _validate_artifacts(
            errors,
            round_inputs,
            verify_files=verify_artifacts,
            artifact_root=artifact_root,
            label="round_cumulative_inputs",
        )
    elif round_inputs is not None:
        errors.append(
            "round_cumulative_inputs is allowed only at a frozen intermediate accepted boundary"
        )
    if not isinstance(receipt.get("simulator_action_taken"), bool):
        errors.append("simulator_action_taken must be boolean")
    if receipt.get("stage_pass_receipt_created") is not False:
        errors.append("stage_pass_receipt_created must be false")
    if receipt.get("evidence_preserved") is not True:
        errors.append("evidence_preserved must be true")
    return errors


def validate_stage_progress_chain(
    receipt_records: Sequence[tuple[Path, Mapping[str, Any]]],
    *,
    stage: str,
    base_accepted: int,
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
    verify_artifacts: bool,
) -> list[str]:
    """Validate one stage's ordered, SHA-linked nonterminal progress chain."""

    errors: list[str] = []
    accepted_before = int(base_accepted)
    prior_sha256: str | None = None
    for attempt_index, (raw_path, receipt) in enumerate(receipt_records, start=1):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            errors.append(f"progress_chain.{attempt_index}.receipt_path is missing")
        item_errors = validate_stage_progress_receipt(
            receipt,
            stage=stage,
            attempt_index=attempt_index,
            accepted_before=accepted_before,
            prior_progress_receipt_sha256=prior_sha256,
            backend_manifest_sha256=backend_manifest_sha256,
            authorization_receipt_sha256=authorization_receipt_sha256,
            verify_artifacts=verify_artifacts,
            artifact_root=path.parent if path.is_file() else None,
        )
        errors.extend(f"progress_chain.{attempt_index}: {error}" for error in item_errors)
        value = receipt.get("accepted_after")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            accepted_before = value
        prior_sha256 = _sha256(path) if path.is_file() else None
    return errors


def accepted_after_progress(
    receipt_records: Sequence[tuple[Path, Mapping[str, Any]]],
    *,
    base_accepted: int,
) -> int:
    """Return the current accepted count after a separately validated chain."""

    if not receipt_records:
        return int(base_accepted)
    value = receipt_records[-1][1].get("accepted_after")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("latest progress receipt has invalid accepted_after")
    return value


def _validate_failure_accounting(
    errors: list[str],
    value: Any,
    *,
    raw_candidates: int | None,
    accepted_this: int | None,
) -> None:
    if not isinstance(value, Mapping):
        errors.append("failure_accounting must be an object")
        return
    if set(value) != set(ATTEMPT_FAILURE_ACCOUNTING_FIELDS):
        errors.append("failure_accounting fields do not exactly match the attempt funnel")
        return
    counts: dict[str, int] = {}
    for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS:
        parsed = _nonnegative_int(errors, value.get(field), f"failure_accounting.{field}")
        if parsed is not None:
            counts[field] = parsed
    if len(counts) != len(ATTEMPT_FAILURE_ACCOUNTING_FIELDS):
        return
    if raw_candidates is not None and counts["raw_geometry_candidates"] != raw_candidates:
        errors.append("failure_accounting.raw_geometry_candidates mismatch")
    if accepted_this is not None and counts["accepted_geometries"] != accepted_this:
        errors.append("failure_accounting.accepted_geometries mismatch")
    partition = sum(
        count for field, count in counts.items() if field != "raw_geometry_candidates"
    )
    if partition != counts["raw_geometry_candidates"]:
        errors.append("failure_accounting does not partition raw_geometry_candidates")


def _validate_artifacts(
    errors: list[str],
    value: Any,
    *,
    verify_files: bool,
    artifact_root: Path | None,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return
    if set(value) != set(STAGE_PROGRESS_ARTIFACT_FIELDS):
        errors.append(f"{label} fields do not exactly match the progress contract")
        return
    root = Path(artifact_root).expanduser().resolve() if artifact_root is not None else None
    for role in STAGE_PROGRESS_ARTIFACT_FIELDS:
        record = value.get(role)
        if not isinstance(record, Mapping):
            errors.append(f"{label}.{role} must be an identity record")
            continue
        raw_path = record.get("path")
        digest = record.get("sha256")
        size = record.get("size_bytes")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{label}.{role}.path is invalid")
            continue
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            errors.append(f"{label}.{role}.sha256 is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append(f"{label}.{role}.size_bytes is invalid")
        if not verify_files:
            continue
        path = Path(raw_path).expanduser().resolve()
        if root is None:
            errors.append(f"{label}.{role} cannot be verified without artifact_root")
            continue
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"{label}.{role}.path escapes the progress output root")
            continue
        if not path.is_file():
            errors.append(f"{label}.{role}.path is missing")
            continue
        if path.stat().st_size != size:
            errors.append(f"{label}.{role}.size_bytes mismatches file")
        if _sha256(path) != digest:
            errors.append(f"{label}.{role}.sha256 mismatches file")


def _equal(errors: list[str], name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{name} mismatch")


def _nonnegative_int(errors: list[str], value: Any, name: str) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{name} must be a nonnegative integer")
        return None
    return value


def _positive_int(errors: list[str], value: Any, name: str) -> int | None:
    parsed = _nonnegative_int(errors, value, name)
    if parsed is not None and parsed == 0:
        errors.append(f"{name} must be positive")
        return None
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ATTEMPT_FAILURE_ACCOUNTING_FIELDS",
    "STAGE_PROGRESS_ARTIFACT_FIELDS",
    "STAGE_PROGRESS_DECISION",
    "STAGE_PROGRESS_SCHEMA",
    "STAGE_PROGRESS_SAFEGUARDS",
    "STAGE_PROGRESS_STATUS",
    "STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA",
    "STAGE_ATTEMPT_TARGET_REACHED_DECISION",
    "accepted_after_progress",
    "validate_stage_progress_chain",
    "validate_stage_progress_receipt",
]
