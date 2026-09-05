"""Validation-only Golden evidence; historical geometry never becomes production."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .broadband56_golden_source import GoldenSourceError, _pin, validate_safe_anchor_qa_receipt


TERMINAL_MODE = "HISTORICAL_GOLDEN_VALIDATION_ONLY_ZERO_PRODUCTION"
FINALIZER_DECISION = "GOLDEN_VALIDATION_COMPLETE_ZERO_PRODUCTION"
ATTEMPT_SCHEMA = "rfic_transformer.broadband56_v2_golden_validation_attempt_products.v1"
ATTEMPT_DECISION = "USE_GOLDEN_VALIDATION_ONLY_TERMINAL_PRODUCTS"
ARTIFACT_FIELDS = (
    "golden_attempt_products_receipt", "stage_attempt_finalizer_receipt",
    "stage_execution_trace", "resource_summary", "stage_context",
    "accepted_geometry_index", "rejected_geometry_index", "validation_geometry",
)


def _load(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    value = json.loads(_pin(record, label).read_text())
    if not isinstance(value, dict):
        raise GoldenSourceError(f"{label} must be an object")
    return value


def _rows(record: Mapping[str, Any], label: str) -> list[dict[str, str]]:
    with _pin(record, label).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise GoldenSourceError(f"{label} lacks unique columns")
        return list(reader)


def _expect(value: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    for key, item in expected.items():
        if value.get(key) != item or (isinstance(item, bool) and value.get(key) is not item):
            raise GoldenSourceError(f"{label}.{key} mismatch")


def validate_attempt(
    record: Mapping[str, Any], *, backend_sha256: str, authorization_sha256: str,
) -> dict[str, Any]:
    """Verify a terminal aggregation and revalidate its candidate-level physics chain."""
    from .broadband56_balanced200k import CAMPAIGN_ID, FREQUENCY_GRID_HZ
    from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT
    from .broadband56_stage_progress import ATTEMPT_FAILURE_ACCOUNTING_FIELDS

    attempt = _load(record, "Golden attempt products")
    _expect(attempt, {
        "schema": ATTEMPT_SCHEMA, "overall_status": "PASS", "decision": ATTEMPT_DECISION,
        "stage": "GOLDEN", "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "current_accepted": 0, "accepted_count": 0, "rejected_count": 0,
        "raw_candidate_count": 1, "geometry_frequency_rows": 0,
        "production_accepted_count_delta": 0, "production_geometry_frequency_rows": 0,
        "golden_validation_status": "PASS", "validation_geometry_count": 1,
        "validation_feature_rows": 56, "terminal_partition_complete": True,
        "failed_or_duplicate_candidates_counted_as_accepted": False,
        "proxy_or_historical_labels_used": False, "simulator_action_taken": False,
    }, "Golden attempt")
    for name, digest in (("backend_identity_manifest", backend_sha256),
                         ("full_campaign_authorization_receipt", authorization_sha256)):
        _pin(attempt.get(name), name)
        if attempt[name]["sha256"] != digest:
            raise GoldenSourceError(f"Golden attempt {name} identity mismatch")
    roles = attempt.get("input_role_receipts")
    if not isinstance(roles, Mapping) or set(roles) != {
        "cadence", "gds", "calibre", "calibre_zero", "exact_emx", "exact56",
    }:
        raise GoldenSourceError("Golden input role set mismatch")
    role_values = {}
    for name, role_record in roles.items():
        role = _load(role_record, f"Golden {name} role")
        _expect(role, {"overall_status": "PASS", "stage": "GOLDEN", "campaign_id": CAMPAIGN_ID,
                      "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                      "backend_identity_manifest": attempt["backend_identity_manifest"],
                      "full_campaign_authorization_receipt": attempt["full_campaign_authorization_receipt"]},
                f"Golden {name} role")
        role_values[name] = role
    qa_role = role_values["exact56"]
    evidence = _rows(qa_role.get("evidence_index"), "Golden QA evidence index")
    if len(evidence) != 1:
        raise GoldenSourceError("Golden QA evidence must contain one candidate")
    qa_path = Path(evidence[0].get("qa_receipt_path", ""))
    qa_record = {"path": str(qa_path), "size_bytes": qa_path.stat().st_size,
                 "sha256": evidence[0].get("qa_receipt_sha256")}
    binding = attempt.get("golden_validation")
    if not isinstance(binding, Mapping):
        raise GoldenSourceError("Golden validation binding missing")
    verified = validate_safe_anchor_qa_receipt(
        attempt["golden_source_receipt"], qa_record,
        exact_emx_receipt_record=binding.get("exact_gds_emx_receipt"),
    )
    if binding != verified or qa_role.get("golden_validation") != verified:
        raise GoldenSourceError("Golden candidate binding differs from numerical QA")
    individual = _load(qa_record, "Golden numerical QA")
    products = attempt.get("validation_products")
    if not isinstance(products, Mapping) or set(products) != {
        "validation_geometry", "exact_gds_emx_receipt_index", "s4p_artifact_index", "long_features",
    }:
        raise GoldenSourceError("Golden validation products mismatch")
    for target, source in (("exact_gds_emx_receipt_index", "source_fresh_emx_receipt_index"),
                           ("s4p_artifact_index", "qa_index"), ("long_features", "broadband_features_long")):
        if _rows(products[target], target) != _rows(individual[source], source):
            raise GoldenSourceError(f"Golden {target} differs from numerical QA")
    features = _rows(products["long_features"], "Golden features")
    if len(features) != 56 or [float(row["frequency_hz"]) for row in features] != list(FREQUENCY_GRID_HZ):
        raise GoldenSourceError("Golden feature frequency grid mismatch")
    ledger = _rows(attempt.get("attempt_ledger"), "Golden ledger")
    if len(ledger) != 1 or ledger != _rows(products["validation_geometry"], "Golden geometry"):
        raise GoldenSourceError("Golden validation ledger mismatch")
    _expect(ledger[0], {"terminal_stage": "GOLDEN_VALIDATION_PASS", "accepted_sequence": "",
                       "duplicate_status": "HISTORICAL_NOT_PRODUCTION",
                       "geometry_sha256": verified["geometry_sha256"]}, "Golden ledger")
    for key in ("accepted_geometry_increment", "rejected_geometry_increment", "exact_gds_emx_receipt_index",
                "s4p_artifact_index", "long_features"):
        if _rows(attempt.get(key), key):
            raise GoldenSourceError(f"Golden production {key} must be empty")
    expected_funnel = {key: int(key == "raw_geometry_candidates") for key in ATTEMPT_FAILURE_ACCOUNTING_FIELDS}
    expected_funnel["golden_validation_geometries"] = 1
    funnel = _rows(attempt.get("failure_funnel"), "Golden funnel")
    if len(funnel) != len(expected_funnel) or {row["stage"]: int(row["count"]) for row in funnel} != expected_funnel:
        raise GoldenSourceError("Golden failure funnel mismatch")
    if attempt.get("failure_accounting") != expected_funnel:
        raise GoldenSourceError("Golden failure accounting mismatch")
    _pin(record, "Golden attempt products after validation")
    return attempt


def validate_finalizer(
    receipt: Mapping[str, Any], *, backend_sha256: str, authorization_sha256: str,
) -> dict[str, Any]:
    from .broadband56_balanced200k import CAMPAIGN_ID
    from .broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT
    from .broadband56_full_campaign_authorization import PRODUCTION_BACKEND_ID
    from .broadband56_stage_progress import STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA

    _expect(receipt, {
        "schema": STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA, "overall_status": "PASS",
        "decision": FINALIZER_DECISION, "stage": "GOLDEN", "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT, "backend_id": PRODUCTION_BACKEND_ID,
        "attempt_index": 1, "accepted_before": 0, "accepted_this_attempt": 0, "accepted_after": 0,
        "cumulative_target": 1, "raw_candidates_this_attempt": 1,
        "progress_receipt": None, "cumulative_stage_inputs": None,
        "simulator_invoked_by_finalizer": False, "golden_terminal_mode": TERMINAL_MODE,
        "production_accepted_count_delta": 0,
    }, "Golden finalizer")
    attempt = validate_attempt(receipt.get("golden_attempt_products_receipt"),
                               backend_sha256=backend_sha256, authorization_sha256=authorization_sha256)
    if receipt.get("golden_validation") != attempt["golden_validation"]:
        raise GoldenSourceError("Golden finalizer validation binding mismatch")
    return attempt


def validate_stage_evidence(receipt: Mapping[str, Any]) -> None:
    """Replay the explicit validation terminal and its hash-bound role prefix."""
    from .broadband56_stage_execution import expected_stage_role_order

    if "operational_progress_rebind" in receipt:
        _validate_operational_reuse(receipt)
        return

    _expect(receipt, {"stage": "GOLDEN", "golden_terminal_mode": TERMINAL_MODE,
                      "accepted_unique_geometries": 0, "validation_geometry_count": 1,
                      "validation_feature_rows": 56, "production_accepted_count_delta": 0}, "Golden stage")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(ARTIFACT_FIELDS):
        raise GoldenSourceError("Golden stage artifacts mismatch")
    finalizer = _load(artifacts["stage_attempt_finalizer_receipt"], "Golden finalizer")
    attempt = validate_finalizer(finalizer,
        backend_sha256=receipt["backend_identity_manifest_sha256"],
        authorization_sha256=receipt["full_campaign_authorization_receipt_sha256"])
    if artifacts["golden_attempt_products_receipt"] != finalizer["golden_attempt_products_receipt"]:
        raise GoldenSourceError("Golden stage/finalizer attempt binding mismatch")
    if receipt.get("golden_validation") != attempt["golden_validation"]:
        raise GoldenSourceError("Golden stage validation binding mismatch")
    if receipt.get("failure_accounting") != attempt["failure_accounting"]:
        raise GoldenSourceError("Golden stage failure accounting mismatch")
    for output, expected in (("accepted_geometry_index", attempt["accepted_geometry_increment"]),
                              ("rejected_geometry_index", attempt["rejected_geometry_increment"]),
                              ("validation_geometry", attempt["validation_products"]["validation_geometry"])):
        if artifacts[output] != expected:
            raise GoldenSourceError(f"Golden stage {output} binding mismatch")
    context = _load(artifacts["stage_context"], "Golden stage context")
    _expect(context, {"stage": "GOLDEN", "current_accepted": 0, "max_concurrency": 1,
                      "backend_identity_manifest": attempt["backend_identity_manifest"],
                      "full_campaign_authorization_receipt": attempt["full_campaign_authorization_receipt"],
                      "prior_stage_receipt": None, "shell_used": False}, "Golden context")
    profile = _load(context["stage_execution_profile"], "Golden execution profile")
    manifest = _load(attempt["backend_identity_manifest"], "Golden backend manifest")
    if manifest.get("runtime_identities", {}).get("stage_execution_profile") != context["stage_execution_profile"]:
        raise GoldenSourceError("Golden profile is not backend-bound")
    if profile.get("stages", {}).get("GOLDEN", {}).get("golden_terminal_mode") != TERMINAL_MODE:
        raise GoldenSourceError("Golden profile does not authorize validation-only terminal")
    trace = _load(artifacts["stage_execution_trace"], "Golden execution trace")
    order = expected_stage_role_order("GOLDEN")
    prefix = list(order[:order.index("stage_attempt_finalizer") + 1])
    _expect(trace, {"overall_status": "PASS", "decision": FINALIZER_DECISION, "stage": "GOLDEN",
                    "role_order": prefix, "expected_terminal_role_order": prefix,
                    "all_role_return_codes_zero": True, "all_role_receipts_pass": True,
                    "shell_used": False}, "Golden trace")
    roles = trace.get("roles")
    if not isinstance(roles, list) or len(roles) != len(prefix):
        raise GoldenSourceError("Golden trace role count mismatch")
    for role, expected_role in zip(roles, prefix):
        _expect(role, {"role": expected_role, "return_code": 0, "shell_used": False,
                       "script_identity": manifest["script_identities"].get(expected_role)}, "Golden trace role")
        for key in ("script_identity", "stdout", "stderr"):
            _pin(role.get(key), f"Golden trace {expected_role} {key}")
        role_receipt = _load(role.get("receipt"), f"Golden {expected_role} receipt")
        _expect(role_receipt, {"overall_status": "PASS", "stage": "GOLDEN"}, "Golden trace receipt")
        source_role = {"cadence_streamout_runner": "cadence", "gds_physical_identity_auditor": "gds",
                       "calibre_runner": "calibre", "calibre_zero_blocking_receipt_builder": "calibre_zero",
                       "exact_audited_gds_emx_runner": "exact_emx", "full_band_s4p_qa_builder": "exact56"}.get(expected_role)
        if source_role and role["receipt"] != attempt["input_role_receipts"][source_role]:
            raise GoldenSourceError("Golden trace/input role receipt mismatch")
    if roles[-1]["receipt"] != artifacts["stage_attempt_finalizer_receipt"]:
        raise GoldenSourceError("Golden trace/finalizer receipt mismatch")
    if roles[-2]["receipt"] != artifacts["golden_attempt_products_receipt"]:
        raise GoldenSourceError("Golden trace/aggregator receipt mismatch")
    resource = _load(artifacts["resource_summary"], "Golden resource summary")
    _expect(resource, {"overall_status": "PASS", "stage": "GOLDEN", "max_concurrency": 1,
                       "resource_snapshot": context["resource_snapshot"]}, "Golden resource summary")
    _pin(resource["resource_snapshot"], "Golden resource snapshot")


def _validate_operational_reuse(receipt: Mapping[str, Any]) -> None:
    """Validate original execution, never relabel it as a new Golden run."""
    binding = receipt["operational_progress_rebind"]
    if not isinstance(binding, Mapping):
        raise GoldenSourceError("Golden operational rebind must be an object")
    _expect(binding, {"kind": "REUSE_COMPLETED_STAGE_UNCHANGED_SCIENTIFIC_CONTRACT",
                      "new_simulator_execution": False, "accepted_count_increment": 0},
            "Golden operational rebind")
    original = _load(binding.get("original_stage_receipt"), "original Golden stage")
    if "operational_progress_rebind" in original:
        raise GoldenSourceError("Golden reuse must bind the original execution, not a nested reuse")
    validate_stage_evidence(original)
    mutable = {"backend_identity_manifest_sha256", "full_campaign_authorization_receipt_sha256",
               "artifacts", "operational_progress_rebind"}
    if ({k: v for k, v in receipt.items() if k not in mutable}
            != {k: v for k, v in original.items() if k not in mutable}):
        raise GoldenSourceError("Golden reuse changed scientific results or stage identity")
    old_attempt = _load(original["artifacts"]["golden_attempt_products_receipt"], "original Golden attempt")
    old_backend = _load(old_attempt["backend_identity_manifest"], "original Golden backend")
    target_pin = binding.get("target_backend_manifest")
    target = _load(target_pin, "Golden reuse target backend")
    if target_pin["sha256"] != receipt["backend_identity_manifest_sha256"]:
        raise GoldenSourceError("Golden reuse target backend SHA mismatch")
    if target.get("scientific_contract") != old_backend.get("scientific_contract"):
        raise GoldenSourceError("Golden reuse scientific contract changed")
    # All simulator entrypoints, extraction code, PDK, configuration and rules
    # must have the original bytes. Only their installation paths may differ.
    for group in ("script_identities", "runtime_identities"):
        if set(target.get(group, {})) != set(old_backend.get(group, {})):
            raise GoldenSourceError("Golden reuse backend role set changed")
        for name, old_pin in old_backend[group].items():
            new_pin = target[group][name]
            _pin(old_pin, "original " + name)
            _pin(new_pin, "rebound " + name)
            if any(new_pin.get(key) != old_pin.get(key) for key in ("sha256", "size_bytes")):
                raise GoldenSourceError("Golden reuse computational identity changed: " + name)
    if ("emx_python_runtime" in target) != ("emx_python_runtime" in old_backend):
        raise GoldenSourceError("Golden reuse EMX runtime binding changed")
    if "emx_python_runtime" in old_backend:
        old_runtime = _load(old_backend["emx_python_runtime"], "original EMX runtime")
        new_runtime = _load(target["emx_python_runtime"], "rebound EMX runtime")
        if set(old_runtime["modules"]) != set(new_runtime["modules"]):
            raise GoldenSourceError("Golden reuse EMX module set changed")
        for name, old_pin in old_runtime["modules"].items():
            new_pin = new_runtime["modules"][name]
            _pin(old_pin, "original EMX module " + name)
            _pin(new_pin, "rebound EMX module " + name)
            if any(new_pin.get(key) != old_pin.get(key) for key in ("sha256", "size_bytes")):
                raise GoldenSourceError("Golden reuse EMX dependency bytes changed: " + name)
        for name in ("python_launcher", "python_runtime", "environment", "dependency_roots"):
            if new_runtime.get(name) != old_runtime.get(name):
                raise GoldenSourceError("Golden reuse EMX environment changed: " + name)
    authorization_pin = binding.get("target_authorization")
    authorization = _load(authorization_pin, "Golden reuse target authorization")
    if not (authorization_pin["sha256"] == receipt["full_campaign_authorization_receipt_sha256"]
            and authorization.get("overall_status") == "PASS"
            and authorization.get("authorization_scope") == "FULL_CAMPAIGN"
            and authorization.get("backend_identity_manifest") == target_pin
            and authorization.get("campaign_id") == original["campaign_id"]
            and authorization.get("contract_fingerprint_sha256") == original["contract_fingerprint_sha256"]
            and authorization.get("nn_training_authorized") is False):
        raise GoldenSourceError("Golden reuse authorization mismatch")
    if set(receipt.get("artifacts", {})) != set(original["artifacts"]):
        raise GoldenSourceError("Golden reuse artifact set changed")
    for name, old_pin in original["artifacts"].items():
        new_pin = receipt["artifacts"][name]
        _pin(new_pin, "Golden reused artifact " + name)
        if any(new_pin.get(key) != old_pin.get(key) for key in ("sha256", "size_bytes")):
            raise GoldenSourceError("Golden reuse artifact bytes changed: " + name)
