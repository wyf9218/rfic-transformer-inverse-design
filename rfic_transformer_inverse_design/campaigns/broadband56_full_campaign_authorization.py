"""Exact authorization contract for the broadband56 V2 full campaign.

This module is control-plane only.  It defines and validates the single
project-owner authorization candidate that may unlock the ordered
Cadence/Calibre/EMX campaign.  It never reads private simulator files and has
no execution capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .broadband56_balanced200k import (
    CAMPAIGN_ID,
    EXPECTED_FEATURE_ROWS,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    TARGET_ACCEPTED_GEOMETRIES,
)
from .broadband56_capacity_policy import (
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)


FULL_CAMPAIGN_CANDIDATE_SCHEMA = (
    "rfic_transformer.broadband56_v2_full_campaign_authorization_candidate.v11"
)
FULL_CAMPAIGN_APPROVAL_SCHEMA = (
    "rfic_transformer.broadband56_v2_full_campaign_authorization_approval.v11"
)
FULL_CAMPAIGN_APPROVAL_SCOPE = "FULL_CAMPAIGN"
FULL_CAMPAIGN_PENDING_STATUS = "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL"
FULL_CAMPAIGN_PASS_DECISION = "APPROVE_FULL_CAMPAIGN"
FULL_CAMPAIGN_FAIL_DECISION = "DO_NOT_AUTHORIZE_FULL_CAMPAIGN"
FULL_CAMPAIGN_CANDIDATE_EFFECT = "NONE_REQUEST_ONLY"
FULL_CAMPAIGN_RECEIPT_EFFECT = "NONE_RECORD_ONLY"
PRODUCTION_BACKEND_ID = "MARS_CADENCE_GDS_IDENTITY_CALIBRE_EMX_S4P_QA_V11"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

UNCHANGED_PHYSICAL_CONTRACT_ITEMS = (
    "foundry_process",
    "proc_file",
    "pdk",
    "metal_and_via_stack",
    "m10_m9_winding_implementation",
    "m4_cutout_and_keepout",
    "topology",
    "ten_geometry_variables",
    "geometry_bounds",
    "port_mode",
    "pin_purposes",
    "grounding",
    "gds_generation",
    "analytical_gates",
    "cadence",
    "calibre_drc",
    "emx_except_frequency_grid",
    "s_z_conversion",
    "physical_feature_equations",
    "provenance_and_no_clobber",
)

GEOMETRY_BOUNDS_UM = {
    "primary_outer_width_um": (160.0, 520.0),
    "primary_outer_height_um": (160.0, 520.0),
    "secondary_outer_width_um": (160.0, 520.0),
    "secondary_outer_height_um": (160.0, 520.0),
    "line_width_um": (3.0, 12.0),
    "primary_terminal_y_span_um": (20.0, 90.0),
    "secondary_terminal_y_span_um": (20.0, 90.0),
    "offset_um": (-90.0, 90.0),
    "primary_feed_extension_um": (100.0, 320.0),
    "secondary_feed_extension_um": (100.0, 320.0),
}

PORT_AND_GROUNDING_CONTRACT = {
    "port_mode": "single_ended_shield_grounded",
    "signal_port_count": 4,
    "touchstone_extension": ".s4p",
    "touchstone_mode": "signal_4_grounded_aux",
    "cadence_pin_purpose": 51,
    "port_order": ["P001", "P002", "P003", "P004"],
    "port_ground_reference": "shield",
    "ground_unused_s8p_ports": False,
}

ATTEMPT_REPLENISHMENT_CONTRACT = {
    "submitted_count_is_not_accepted_count": True,
    "failed_or_duplicate_attempts_do_not_consume_accepted_target": True,
    "continue_sampling_until_exact_accepted_target": True,
    "bounded_replenished_shards_required": True,
    "bounded_pending_work_window_required": True,
    "resource_gate_before_every_attempt_shard": True,
    "no_clobber_attempt_paths_required": True,
    "retry_failed_shards_only": True,
    "accepted_count_overshoot_forbidden": True,
}

STAGE_ORDER = tuple(stage.name for stage in STAGES)
STAGE_TARGETS = tuple(stage.cumulative_target for stage in STAGES)
STAGE_TERMINAL_STATES = tuple(stage.receipt_status for stage in STAGES)

PUBLIC_EVIDENCE_FIELDS = {
    "frozen_contract": "configs/broadband56_real_emx_balanced200k_tsmc65_v2.json",
    "sanitized_private_template": (
        "configs/mars_s4p_grounded_powerline_broadband56_balanced200k_v2_template.yaml"
    ),
    "r2_candidate": (
        "docs/research/BROADBAND56_RECONSTRUCTED_BASELINE_V1_CANDIDATE_R2_20260829.json"
    ),
    "r2_approval_receipt": (
        "reports/broadband56_reconstructed_baseline_r2_approval_20260830T143319Z/"
        "RECONSTRUCTED_BASELINE_APPROVAL_RECEIPT.json"
    ),
    "public_preparation_status": (
        "reports/broadband56_v2_preparation_preflight_r2_20260830T145300Z/"
        "PUBLIC_SAFE_PREPARATION_STATUS.json"
    ),
    "operational_policy_candidate": (
        "docs/research/BROADBAND56_V2_CAPACITY_POLICY_AMENDMENT_CANDIDATE_20260830.json"
    ),
}

PRIVATE_PREPARATION_SHA_FIELDS = (
    "preparation_receipt_sha256",
    "private_configuration_sha256",
    "historical_configuration_sha256",
    "campaign_contract_frozen_sha256",
    "primary_bins_frozen_sha256",
    "secondary_coverage_frozen_sha256",
    "geometry_bounds_frozen_sha256",
    "phase_plan_frozen_sha256",
    "operational_policy_approval_receipt_sha256",
)

BACKEND_SHA_FIELDS = (
    "backend_identity_manifest_sha256",
    "backend_identity_verification_receipt_sha256",
    "queue_controller_sha256",
    "resource_gate_auditor_sha256",
    "stage_launcher_sha256",
    "production_stage_backend_sha256",
    "phase_a_queue_builder_sha256",
    "adaptive_checkpoint_materializer_sha256",
    "adaptive_candidate_pool_builder_sha256",
    "acquisition_ensemble_trainer_sha256",
    "acquisition_predictor_sha256",
    "adaptive_candidate_selector_sha256",
    "adaptive_round_stager_sha256",
    "cadence_streamout_runner_sha256",
    "cadence_streamout_delegate_sha256",
    "candidate_gds_index_builder_sha256",
    "gds_physical_identity_auditor_sha256",
    "gds_physical_identity_delegate_sha256",
    "gds_physical_identity_module_sha256",
    "calibre_runner_sha256",
    "calibre_batch_delegate_sha256",
    "calibre_zero_blocking_receipt_builder_sha256",
    "calibre_zero_blocking_single_receipt_builder_sha256",
    "exact_audited_gds_emx_runner_sha256",
    "exact_audited_gds_emx_single_runner_sha256",
    "exact_audited_gds_emx_module_sha256",
    "full_band_s4p_qa_builder_sha256",
    "full_band_s4p_qa_module_sha256",
    "stage_attempt_product_builder_sha256",
    "stage_attempt_finalizer_sha256",
    "raw_products_finalizer_sha256",
    "checkpoint_auditor_sha256",
    "campaign_histories_finalizer_sha256",
    "training_readiness_finalizer_sha256",
    "checkpoint_figure_renderer_sha256",
    "final_delivery_auditor_sha256",
    "resource_probe_sha256",
    "python_executable_sha256",
    "historical_gds_identity_pass_receipt_sha256",
)


def expected_frequency_contract() -> dict[str, Any]:
    return {
        "start_hz": FREQUENCY_GRID_HZ[0],
        "stop_hz": FREQUENCY_GRID_HZ[-1],
        "step_hz": 1_000_000_000,
        "points": len(FREQUENCY_GRID_HZ),
        "exact_hz": list(FREQUENCY_GRID_HZ),
        "strictly_increasing": True,
        "interpolation_allowed": False,
        "ports": 4,
        "touchstone_extension": ".s4p",
    }


def expected_terminal_contract() -> dict[str, Any]:
    return {
        "accepted_geometry_unique_s4p_geometries": TARGET_ACCEPTED_GEOMETRIES,
        "feature_complete_geometries": TARGET_ACCEPTED_GEOMETRIES,
        "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
        "geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
        "label_source": "FRESH_REAL_EMX_ONLY",
        "frequency_rows_are_not_independent_geometries": True,
    }


def expected_geometry_contract() -> dict[str, Any]:
    return {
        "field_order": list(GEOMETRY_FIELDS),
        "field_bounds_um": {
            field: list(GEOMETRY_BOUNDS_UM[field]) for field in GEOMETRY_FIELDS
        },
        "geometry_identity": "canonical_ordered_10d_um_sha256_v2",
        "duplicate_geometries_accepted": False,
        "manual_gds_modification_allowed": False,
    }


def expected_stage_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": stage.name,
            "cumulative_accepted_unique_geometries": stage.cumulative_target,
            "required_terminal_state": stage.receipt_status,
            "prior_exact_pass_receipt_required": index > 0,
        }
        for index, stage in enumerate(STAGES)
    ]


def validate_full_campaign_candidate(
    candidate: Mapping[str, Any],
    *,
    repository_root: Path | None = None,
) -> list[str]:
    """Return every candidate violation; an empty list means static PASS."""

    errors: list[str] = []
    _require_equal(errors, "schema", candidate.get("schema"), FULL_CAMPAIGN_CANDIDATE_SCHEMA)
    _require_equal(errors, "campaign_id", candidate.get("campaign_id"), CAMPAIGN_ID)
    _require_equal(
        errors,
        "scientific_contract_fingerprint_sha256",
        candidate.get("scientific_contract_fingerprint_sha256"),
        SCIENTIFIC_CONTRACT_FINGERPRINT,
    )
    _require_equal(
        errors,
        "approval_status",
        candidate.get("approval_status"),
        FULL_CAMPAIGN_PENDING_STATUS,
    )
    _require_equal(
        errors,
        "authorization_scope",
        candidate.get("authorization_scope"),
        FULL_CAMPAIGN_APPROVAL_SCOPE,
    )
    _require_equal(
        errors,
        "execution_effect_of_candidate_file",
        candidate.get("execution_effect_of_candidate_file"),
        FULL_CAMPAIGN_CANDIDATE_EFFECT,
    )
    _require_equal(
        errors,
        "automatic_campaign_execution_authorized",
        candidate.get("automatic_campaign_execution_authorized"),
        False,
    )
    _require_equal(
        errors,
        "frequency_contract",
        candidate.get("frequency_contract"),
        expected_frequency_contract(),
    )
    _require_equal(
        errors,
        "terminal_contract",
        candidate.get("terminal_contract"),
        expected_terminal_contract(),
    )
    _require_equal(
        errors,
        "geometry_contract",
        candidate.get("geometry_contract"),
        expected_geometry_contract(),
    )
    _require_equal(
        errors,
        "port_and_grounding_contract",
        candidate.get("port_and_grounding_contract"),
        PORT_AND_GROUNDING_CONTRACT,
    )
    _require_equal(
        errors,
        "attempt_replenishment_contract",
        candidate.get("attempt_replenishment_contract"),
        ATTEMPT_REPLENISHMENT_CONTRACT,
    )
    _require_equal(
        errors,
        "unchanged_physical_contract_items",
        candidate.get("unchanged_physical_contract_items"),
        list(UNCHANGED_PHYSICAL_CONTRACT_ITEMS),
    )
    _require_equal(
        errors,
        "ordered_stages",
        candidate.get("ordered_stages"),
        expected_stage_contract(),
    )
    _validate_stage_transition(errors, candidate.get("stage_transition_contract"))
    _validate_queue_contract(errors, candidate.get("queue_contract"))
    _validate_label_contract(errors, candidate.get("label_contract"))
    _validate_private_preparation(errors, candidate.get("private_preparation_evidence"))
    _validate_runtime_backend(errors, candidate.get("runtime_and_backend_identity"))
    _validate_public_evidence(
        errors,
        candidate.get("public_evidence"),
        repository_root=repository_root,
    )
    _validate_public_safety(errors, candidate)
    return errors


def _validate_stage_transition(errors: list[str], value: Any) -> None:
    expected = {
        "prior_stage_exact_pass_receipt_required": True,
        "no_additional_human_approval_after_full_campaign_pass": True,
        "golden_failure_blocks_later_stages": True,
        "bounded_pending_work_window_required": True,
        "no_clobber_shards_required": True,
        "retry_failed_shards_only": True,
        "exact_200000_completion_required": True,
    }
    _require_equal(errors, "stage_transition_contract", value, expected)


def _validate_queue_contract(errors: list[str], value: Any) -> None:
    expected = {
        "registration_authorized_before_full_campaign_approval": True,
        "zero_simulator_before_exact_pass_receipt": True,
        "one_authoritative_supervisor": True,
        "survives_terminal_and_browser_disconnect": True,
        "persistent_no_clobber_private_root": True,
        "poll_seconds": 60,
        "resource_shortage_state": "QUEUED_WAITING_FOR_CAPACITY",
        "resource_shortage_is_terminal_blocker": False,
    }
    _require_equal(errors, "queue_contract", value, expected)


def _validate_label_contract(errors: list[str], value: Any) -> None:
    expected = {
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
    _require_equal(errors, "label_contract", value, expected)


def _validate_private_preparation(errors: list[str], value: Any) -> None:
    if not isinstance(value, Mapping):
        errors.append("private_preparation_evidence must be an object")
        return
    for field in PRIVATE_PREPARATION_SHA_FIELDS:
        if not _is_sha256(value.get(field)):
            errors.append(f"private_preparation_evidence.{field} is not SHA-256")
    expected_scalars = {
        "preparation_receipt_size_bytes": 10439,
        "preparation_overall_status": "PASS",
        "preparation_decision": "PREPARED_FOR_GOLDEN_GATE",
        "preparation_check_count": 40,
        "preparation_pass_count": 40,
        "preparation_fail_count": 0,
        "private_paths_published": False,
    }
    for field, expected in expected_scalars.items():
        _require_equal(
            errors,
            f"private_preparation_evidence.{field}",
            value.get(field),
            expected,
        )


def _validate_runtime_backend(errors: list[str], value: Any) -> None:
    if not isinstance(value, Mapping):
        errors.append("runtime_and_backend_identity must be an object")
        return
    _require_equal(errors, "runtime_backend.backend_id", value.get("backend_id"), PRODUCTION_BACKEND_ID)
    _require_equal(
        errors,
        "runtime_backend.resource_policy",
        value.get("resource_policy"),
        RESOURCE_POLICY,
    )
    _require_equal(
        errors,
        "runtime_backend.operational_policy_approval_scope",
        value.get("operational_policy_approval_scope"),
        POLICY_APPROVAL_SCOPE,
    )
    for field in BACKEND_SHA_FIELDS:
        if not _is_sha256(value.get(field)):
            errors.append(f"runtime_and_backend_identity.{field} is not SHA-256")
    pass_receipts = value.get("historical_backend_pass_receipts")
    if not isinstance(pass_receipts, Sequence) or isinstance(pass_receipts, (str, bytes)):
        errors.append("runtime_and_backend_identity.historical_backend_pass_receipts must be a list")
    else:
        if len(pass_receipts) < 2:
            errors.append("at least two historical backend PASS receipts are required")
        for index, receipt in enumerate(pass_receipts):
            if not isinstance(receipt, Mapping):
                errors.append(f"historical backend receipt {index} is not an object")
                continue
            if receipt.get("overall_status") != "PASS":
                errors.append(f"historical backend receipt {index} is not PASS")
            if not _is_sha256(receipt.get("sha256")):
                errors.append(f"historical backend receipt {index} lacks SHA-256")
            if not isinstance(receipt.get("size_bytes"), int) or receipt.get("size_bytes", 0) <= 0:
                errors.append(f"historical backend receipt {index} has invalid size")
    required_true = (
        "cadence_identity_reverified",
        "calibre_zero_blocking_gate_required",
        "emx_wrapper_identity_reverified",
        "emx_process_identity_reverified",
        "full_band_s4p_qa_required",
        "private_paths_published",
    )
    for field in required_true[:-1]:
        if value.get(field) is not True:
            errors.append(f"runtime_and_backend_identity.{field} must be true")
    if value.get("private_paths_published") is not False:
        errors.append("runtime_and_backend_identity.private_paths_published must be false")


def _validate_public_evidence(
    errors: list[str],
    value: Any,
    *,
    repository_root: Path | None,
) -> None:
    if not isinstance(value, Mapping):
        errors.append("public_evidence must be an object")
        return
    for label, required_path in PUBLIC_EVIDENCE_FIELDS.items():
        record = value.get(label)
        if not isinstance(record, Mapping):
            errors.append(f"public_evidence.{label} must be an object")
            continue
        _require_equal(errors, f"public_evidence.{label}.path", record.get("path"), required_path)
        expected_sha = record.get("sha256")
        if not _is_sha256(expected_sha):
            errors.append(f"public_evidence.{label}.sha256 is not SHA-256")
            continue
        if repository_root is None:
            continue
        root = repository_root.expanduser().resolve()
        path = (root / required_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"public_evidence.{label}.path escapes repository")
            continue
        if not path.is_file():
            errors.append(f"public_evidence.{label}.path is missing")
        elif _sha256(path) != expected_sha:
            errors.append(f"public_evidence.{label}.sha256 mismatches repository bytes")


def _validate_public_safety(errors: list[str], candidate: Mapping[str, Any]) -> None:
    serialized = json.dumps(candidate, sort_keys=True)
    forbidden = ("/volumes/", "/cae/", "license_file", "cdslmd_license_file")
    for token in forbidden:
        if token in serialized:
            errors.append(f"candidate publishes forbidden private token: {token}")


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
    "BACKEND_SHA_FIELDS",
    "FULL_CAMPAIGN_APPROVAL_SCHEMA",
    "FULL_CAMPAIGN_APPROVAL_SCOPE",
    "FULL_CAMPAIGN_CANDIDATE_EFFECT",
    "FULL_CAMPAIGN_CANDIDATE_SCHEMA",
    "FULL_CAMPAIGN_FAIL_DECISION",
    "FULL_CAMPAIGN_PASS_DECISION",
    "FULL_CAMPAIGN_PENDING_STATUS",
    "FULL_CAMPAIGN_RECEIPT_EFFECT",
    "GEOMETRY_BOUNDS_UM",
    "PORT_AND_GROUNDING_CONTRACT",
    "PRIVATE_PREPARATION_SHA_FIELDS",
    "PRODUCTION_BACKEND_ID",
    "PUBLIC_EVIDENCE_FIELDS",
    "UNCHANGED_PHYSICAL_CONTRACT_ITEMS",
    "expected_frequency_contract",
    "expected_geometry_contract",
    "expected_stage_contract",
    "expected_terminal_contract",
    "validate_full_campaign_candidate",
]
