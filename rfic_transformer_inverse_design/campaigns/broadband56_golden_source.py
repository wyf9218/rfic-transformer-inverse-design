"""Canonical source identity for a historical, validation-only Rescue Golden.

This source is intentionally NOT added to production acquisition enums. A
source binding authorizes validation, not acceptance or a physical PASS.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .broadband56_balanced200k import CAMPAIGN_ID, GEOMETRY_FIELDS, canonical_geometry_sha256


SAFE_ANCHOR_SOURCE = "historical_zero_blocking_safe_anchor_layout_drc_only"
SOURCE_SCHEMA = "rfic_transformer.broadband56_v2_safe_anchor_source.v1"
QUEUE_SCHEMA = "rfic_transformer.broadband56_v2_bound_safe_anchor_queue.v1"
SOURCE_CONTRACT = {
    "schema": "rfic_transformer.broadband56_golden_source_contract.v1",
    "acquisition_source": SAFE_ANCHOR_SOURCE,
    "geometry_selection": "HISTORICAL_ZERO_BLOCKING_GEOMETRY_ONLY",
    "allowed_stage": "GOLDEN",
    "stage_gate_validation_authorized": True,
    "global_geometry_uniqueness": "HISTORICALLY_REUSED",
    "production_dataset_accepted_eligible": False,
    "production_accepted_count_delta": 0,
    "golden_pass_requires_fresh_emx_and_exact56_qa": True,
    "historical_gds_or_labels_reusable": False,
}


class GoldenSourceError(RuntimeError):
    """A source must fail closed rather than be relabeled as production DOE."""


def _pin(record: Mapping[str, Any], label: str) -> Path:
    if not isinstance(record, Mapping):
        raise GoldenSourceError(f"{label}: binding is absent")
    path = Path(str(record.get("path", "")))
    if not path.is_absolute() or not path.is_file() or any(
        item.is_symlink() for item in (path, *path.parents)
    ):
        raise GoldenSourceError(f"{label}: regular absolute file required")
    if path.stat().st_size != record.get("size_bytes"):
        raise GoldenSourceError(f"{label}: size mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
        raise GoldenSourceError(f"{label}: SHA-256 mismatch")
    return path


def validate_safe_anchor_source(
    queue_receipt_record: Mapping[str, Any], *, stage: str,
    geometry_sha256: str, config_sha256: str, contract_fingerprint: str,
) -> dict[str, Any]:
    """Validate exact source/queue/config bindings without claiming Golden PASS."""
    summary_path = _pin(queue_receipt_record, "bound Golden queue receipt")
    summary = json.loads(summary_path.read_text())
    if not (
        stage == "GOLDEN"
        and summary.get("schema") == QUEUE_SCHEMA
        and summary.get("overall_status") == "PASS"
        and summary.get("decision") == "USE_EXACT_BOUND_SAFE_ANCHOR_FOR_RESCUE_GOLDEN"
        and summary.get("campaign_id") == CAMPAIGN_ID
        and summary.get("contract_fingerprint_sha256") == contract_fingerprint
        and summary.get("stage") == stage
        and summary.get("queue_count") == 1
        and summary.get("safe_anchor_geometry_sha256") == geometry_sha256
        and summary.get("source_contract") == SOURCE_CONTRACT
        and summary.get("geometry_only_source_reused") is True
        and summary.get("historical_gds_reused") is False
        and summary.get("historical_s4p_reused") is False
        and summary.get("proxy_or_physical_labels_present") is False
    ):
        raise GoldenSourceError("Golden queue/source contract mismatch")
    source_path = _pin(summary.get("safe_anchor_source_receipt"), "source receipt")
    source = json.loads(source_path.read_text())
    if not (
        source.get("schema") == SOURCE_SCHEMA
        and source.get("overall_status") == "PASS"
        and source.get("decision") == "USE_GEOMETRY_PARAMETERS_ONLY_REGENERATE_WITH_CURRENT_FROZEN_GENERATOR"
        and source.get("campaign_id") == CAMPAIGN_ID
        and source.get("historical_candidate_id") == summary.get("safe_anchor_id")
        and bool(source.get("historical_candidate_id"))
        and source.get("current_canonical_geometry_sha256") == geometry_sha256
        and source.get("geometry_vector_order") == list(GEOMETRY_FIELDS)
        and source.get("analytical_gate", {}).get("status") == "PASS"
        and source.get("analytical_gate", {}).get("topology_mode") == "1t1t"
        and source.get("historical_gds_reused") is False
        and source.get("historical_labels_reused") is False
    ):
        raise GoldenSourceError("historical safe-anchor source identity mismatch")
    if canonical_geometry_sha256(source.get("geometry", {})) != geometry_sha256:
        raise GoldenSourceError("source geometry canonical SHA mismatch")
    config = _pin(summary.get("corrected_private_configuration"), "corrected config")
    if summary["corrected_private_configuration"]["sha256"] != config_sha256:
        raise GoldenSourceError("current configuration differs from source binding")
    approval_path = _pin(summary.get("corrected_foundry_layout_approval_receipt"), "corrected approval")
    approval = json.loads(approval_path.read_text())
    scope = "RESTORE_FOUNDRY_LAYOUT_CONTRACT_AND_RERUN_ONE_RESCUE_GOLDEN_THEN_AUTO_CONTINUE_FULL_CAMPAIGN"
    if not (
        approval.get("schema") == "rfic_transformer.broadband56_corrected_foundry_layout_authorization.v1"
        and approval.get("overall_status") == "PASS"
        and approval.get("authorization_scope") == scope
        and approval.get("decision") == "APPROVE_" + scope
        and approval.get("restore_corrected_foundry_layout_contract_authorized") is True
        and approval.get("one_corrected_rescue_golden_authorized") is True
        and approval.get("nn_training_authorized") is False
        and approval.get("verified_bound_files", {}).get("corrected_private_configuration")
        == summary["corrected_private_configuration"]
    ):
        raise GoldenSourceError("corrected foundry-layout approval mismatch")
    queue_path = _pin(summary.get("candidate_queue"), "exact safe-anchor queue")
    with queue_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise GoldenSourceError("safe-anchor queue must have exactly one row")
    row = rows[0]
    if not (
        row.get("acquisition_source") == SAFE_ANCHOR_SOURCE
        and row.get("campaign_phase") == "PHASE_A"
        and row.get("campaign_id") == CAMPAIGN_ID
        and row.get("campaign_contract_fingerprint") == contract_fingerprint
        and all(row.get(field) == geometry_sha256 for field in (
            "candidate_id_sha256", "candidate_geometry_identity_sha256",
            "geometry_id", "geometry_sha256", "geometry_fingerprint_sha256",
        ))
        and canonical_geometry_sha256({name: row.get(f"geom__{name}") for name in GEOMETRY_FIELDS}) == geometry_sha256
    ):
        raise GoldenSourceError("safe-anchor row source or geometry mismatch")
    return {
        "source_contract": dict(SOURCE_CONTRACT),
        "source_binding": dict(queue_receipt_record),
        "geometry_sha256": geometry_sha256,
        "config_sha256": config_sha256,
        "golden_stage_gate_eligible": False,
        "eligibility_status": "PENDING_FRESH_EMX_AND_EXACT56_QA",
        "production_dataset_accepted_eligible": False,
        "production_accepted_count_delta": 0,
    }


def validate_safe_anchor_emx_binding(
    queue_receipt_record: Mapping[str, Any], *, stage: str,
    emx_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Recheck the source-to-Calibre chain; exact56 QA must still run next."""
    from . import broadband56_exact_gds_emx as exact

    geometry = emx_receipt.get("geometry_identity_sha256")
    config_record = emx_receipt.get("private_configuration", {})
    binding = validate_safe_anchor_source(
        queue_receipt_record, stage=stage, geometry_sha256=geometry,
        config_sha256=config_record.get("sha256"),
        contract_fingerprint=emx_receipt.get("contract_fingerprint_sha256"),
    )
    if emx_receipt.get("candidate_id_sha256") != geometry:
        raise GoldenSourceError("safe-anchor EMX candidate/geometry mismatch")
    pins = {}
    for key in (
        "private_configuration", "source_exact_gds", "source_layout_manifest",
        "source_calibre_zero_blocking_receipt", "source_calibre_report",
        "full_campaign_authorization_receipt",
    ):
        record = emx_receipt.get(key)
        path = _pin(record, key)
        pins[key] = exact._pin_regular_file(
            path, expected_sha256=record["sha256"],
            expected_size=record["size_bytes"], label=key,
        )[0]
    zero_path = Path(pins["source_calibre_zero_blocking_receipt"].path)
    zero = json.loads(zero_path.read_text())
    report_pin = exact._validate_calibre_receipt(
        zero, receipt_path=zero_path, candidate_id=geometry, geometry_id=geometry,
        config_pin=pins["private_configuration"], gds_pin=pins["source_exact_gds"],
        manifest_pin=pins["source_layout_manifest"], top_cell=emx_receipt.get("top_cell"),
    )
    if report_pin.public_record() != pins["source_calibre_report"].public_record():
        raise GoldenSourceError("safe-anchor EMX/Calibre report binding differs")
    exact._validate_full_campaign_receipt(json.loads(
        Path(pins["full_campaign_authorization_receipt"].path).read_text()
    ))
    audit_record = zero.get("source_geometry_audit")
    audit_path = _pin(audit_record, "current-contract geometry audit")
    audit = json.loads(audit_path.read_text())
    if not (
        zero.get("source_files_unchanged") is True
        and audit.get("schema") == "rfic_transformer.broadband56_v2_current_contract_calibre_delegate_geometry_audit.v2"
        and audit.get("overall_status") == "PASS"
        and audit.get("decision") == "CURRENT_CONTRACT_GDS_READY_FOR_FOUNDRY_CALIBRE"
        and audit.get("candidate_id_sha256") == geometry
        and audit.get("candidate_geometry_identity_sha256") == geometry
        and audit.get("gds_path") == pins["source_exact_gds"].path
        and audit.get("gds_sha256") == pins["source_exact_gds"].sha256
        and audit.get("gds_top_cell") == emx_receipt.get("top_cell")
        and audit.get("geometry_metric_states", {}).get("power_line_check") == "PASS"
        and audit.get("geometry_metric_states", {}).get("via_stack_check") == "PASS"
        and audit.get("checks", {}).get("foundry_layout_audit_pass") is True
    ):
        raise GoldenSourceError("safe-anchor geometry audit/GDS binding mismatch")
    evidence = audit.get("source_evidence", {})
    for key in ("evaluation_summary", "foundry_layout_audit", "gds_physical_identity_audit", "source_geometry_audit", "emx_process_file"):
        _pin(evidence.get(key), f"geometry audit {key}")
    foundry = json.loads(Path(evidence["foundry_layout_audit"]["path"]).read_text())
    if foundry.get("overall_status") != "PASS":
        raise GoldenSourceError("safe-anchor foundry-layout audit is not PASS")
    _pin(zero.get("source_calibre_summary"), "Calibre summary")
    exact._reverify_pins(list(pins.values()))
    return {
        **binding,
        "source_geometry_audit": dict(audit_record),
        "source_foundry_layout_audit": dict(evidence["foundry_layout_audit"]),
        "source_calibre_zero_blocking_receipt": dict(emx_receipt["source_calibre_zero_blocking_receipt"]),
        "source_exact_gds": dict(emx_receipt["source_exact_gds"]),
    }


def validate_safe_anchor_qa_receipt(
    queue_receipt_record: Mapping[str, Any], qa_receipt_record: Mapping[str, Any],
    *, exact_emx_receipt_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Consume only hash-bound numerical QA, never source eligibility alone."""
    from . import broadband56_s4p_qa as qa

    qa_path = _pin(qa_receipt_record, "Golden exact56 QA receipt")
    receipt = json.loads(qa_path.read_text())
    emx_path = _pin(exact_emx_receipt_record, "Golden fresh-EMX receipt")
    emx = json.loads(emx_path.read_text())
    geometry = emx.get("geometry_identity_sha256")
    qa._validate_exact_gds_emx_receipt(
        emx, candidate_sha256=geometry, geometry_sha256=geometry, line_number=2,
    )
    expected = validate_safe_anchor_emx_binding(queue_receipt_record, stage="GOLDEN", emx_receipt=emx)
    output = emx["emx_output"]
    s4p_record = {
        "path": output["touchstone_path"], "size_bytes": output["touchstone_size_bytes"],
        "sha256": output["touchstone_sha256"],
    }
    _pin(s4p_record, "Golden S4P")
    expected.update({
        "golden_stage_gate_eligible": True,
        "eligibility_status": "FRESH_EMX_AND_EXACT56_QA_PASS",
        "exact_gds_emx_receipt": dict(exact_emx_receipt_record), "s4p": s4p_record,
        "validation_geometry_count": 1, "validation_feature_rows": 56,
    })
    if not (
        receipt.get("schema") == qa.QA_RECEIPT_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == qa.VALIDATION_QA_PASS_DECISION
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256") == emx.get("contract_fingerprint_sha256")
        and receipt.get("golden_validation") == expected
        and receipt.get("geometry_count") == 1
        and receipt.get("geometry_unique_count") == 1
        and receipt.get("candidate_unique_count") == 1
        and receipt.get("geometry_frequency_rows") == 56
        and receipt.get("production_accepted_count_delta") == 0
        and receipt.get("production_geometry_frequency_rows") == 0
        and receipt.get("proxy_or_historical_labels_used") is False
        and receipt.get("simulator_action_taken") is False
    ):
        raise GoldenSourceError("Golden QA eligibility or production-count contract mismatch")
    for key in ("source_fresh_emx_receipt_index", "qa_index", "broadband_features_long", "broadband_features_manifest"):
        _pin(receipt.get(key), f"Golden QA {key}")
    _pin(queue_receipt_record, "Golden queue receipt after QA validation")
    _pin(qa_receipt_record, "Golden QA receipt after validation")
    return expected
