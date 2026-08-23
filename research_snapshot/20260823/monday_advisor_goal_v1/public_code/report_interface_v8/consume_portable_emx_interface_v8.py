#!/usr/bin/env python3
"""Fail-closed consumer for the portable Monday fresh-EMX interface v8.

The CLI prints only structural counts and hashes.  It never prints metric
values.  Report code may import :func:`load_portable_interface` after the
separate execution/result-access authority has supplied a local bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any, Mapping

import adapt_complete_emx_interface_v8 as adapter


REQUIRED_SOURCE_ROLES = {
    "producer_complete_interface_original",
    "terminal_normalized_gds_identity_receipt",
    "statistics_v2_preregistration",
    "statistics_v2_readme",
    "executed_statistics_copy",
    "producer__canonical_controller",
    "producer__canonical_stage07",
    "producer__canonical_stage08",
    "controller_resume_authorization",
    "controller_resume_preflight_terminal",
    "controller_resume_outcome",
    "controller_release_contract",
    "controller_bundle_manifest",
    "controller_independent_review_go",
    "controller_runtime_dependency_manifest",
    "controller_runtime_interpreter",
    "controller_watcher_launch_receipt",
    "controller_watcher_script",
    "controller_release_common",
    "controller_bound_launcher__build_complete_emx_interface_v5.py",
    "controller_bound_launcher__resume_exact_watcher_stage07_08_v5.py",
    "controller_bound_launcher__run_full_band_v3_after_stage08_v5.py",
    "controller_bound_launcher__run_three_chain_after_stage08_v5.py",
} | {f"producer__{role}" for role in adapter.PRODUCER_TERMINAL_ROLES} | set(
    adapter.NESTED_ARTIFACT_ROLES
)


def _read_bundle_regular(
    bundle_root: Path,
    relative: Any,
    expected_sha: Any,
    label: str,
    *,
    expected_size_bytes: Any | None = None,
    root_lease: adapter.HeldRootLease | None = None,
    held_snapshots: dict[tuple[str, str], adapter.HeldRegularSnapshot] | None = None,
) -> bytes:
    safe = adapter.safe_relative(relative, label)
    expected = adapter.require_sha(expected_sha, f"{label} SHA")
    if root_lease is None:
        raw = adapter._read_relative_regular_bytes(bundle_root, safe, label)
    else:
        key = (safe, expected)
        snapshot = held_snapshots.get(key) if held_snapshots is not None else None
        if snapshot is None:
            snapshot = root_lease.open_regular(safe, label)
            if held_snapshots is not None:
                held_snapshots[key] = snapshot
        snapshot.verify_named_continuity(verify_root=False)
        raw = snapshot.raw
    if adapter.sha_bytes(raw) != expected:
        raise adapter.GateError(f"{label} SHA mismatch")
    if expected_size_bytes is not None:
        if type(expected_size_bytes) is not int or expected_size_bytes <= 0:
            raise adapter.GateError(f"{label} public size_bytes must be a positive exact integer")
        if len(raw) != expected_size_bytes:
            raise adapter.GateError(
                f"{label} public size_bytes differs from held-FD bytes: "
                f"declared={expected_size_bytes}, actual={len(raw)}"
            )
    return raw


def _load_adapter_receipt(
    bundle_root: Path,
    interface_path: Path,
    expected_interface_sha256: str,
    adapter_receipt_path: Path,
    expected_adapter_receipt_sha256: str,
    *,
    root_lease: adapter.HeldRootLease | None = None,
    held_snapshots: dict[tuple[str, str], adapter.HeldRegularSnapshot] | None = None,
) -> Mapping[str, Any]:
    expected_interface = adapter.require_sha(expected_interface_sha256, "expected portable interface SHA")
    expected_receipt = adapter.require_sha(expected_adapter_receipt_sha256, "expected adapter receipt SHA")
    if interface_path.name != "COMPLETE_EMX_RESULT_INTERFACE_PORTABLE_V8.json":
        raise adapter.GateError("portable interface filename is not exact")
    if (
        adapter_receipt_path.name != "ADAPTER_RECEIPT.json"
        or adapter_receipt_path.parent.absolute() != bundle_root.absolute()
    ):
        raise adapter.GateError("ADAPTER_RECEIPT must be the exact direct sibling in the interface bundle")
    interface_raw = _read_bundle_regular(
        bundle_root, interface_path.name, expected_interface, "portable interface entry",
        root_lease=root_lease, held_snapshots=held_snapshots,
    )
    receipt_raw = _read_bundle_regular(
        bundle_root, adapter_receipt_path.name, expected_receipt, "ADAPTER_RECEIPT entry",
        root_lease=root_lease, held_snapshots=held_snapshots,
    )
    receipt = adapter.strict_json_bytes(receipt_raw, "ADAPTER_RECEIPT")
    receipt = adapter.require_exact_keys(
        receipt,
        {
            "schema", "status", "input_interface_sha256", "output_interface_path",
            "output_interface_sha256", "closure_manifest_path", "closure_manifest_sha256",
            "source_record_count", "discovered_control_source_record_count",
            "source_scientific_projection_sha256",
            "output_restored_scientific_projection_sha256", "remote_login_performed",
            "remote_generation_performed", "production_chain_executed",
        },
        "ADAPTER_RECEIPT",
    )
    if (
        receipt.get("schema") != "complete_emx_report_interface_adapter_v8_receipt"
        or receipt.get("status") != "PASS_AUTHOR_ADAPTER_EXECUTION"
        or receipt.get("output_interface_path") != interface_path.name
        or receipt.get("output_interface_sha256") != expected_interface
        or receipt.get("remote_login_performed") is not False
        or receipt.get("remote_generation_performed") is not False
        or receipt.get("production_chain_executed") is not False
    ):
        raise adapter.GateError("ADAPTER_RECEIPT schema/status/output/result-blind binding drift")
    return receipt


def _active_paths_are_nonabsolute(value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _active_paths_are_nonabsolute(child, child_key)
    elif isinstance(value, list):
        for child in value:
            _active_paths_are_nonabsolute(child, key)
    elif isinstance(value, str) and key is not None and (key == "path" or key.endswith("_path")):
        if Path(value).is_absolute() or "\\" in value or "\x00" in value:
            raise adapter.GateError(f"portable interface retains nonportable active path in {key}")


def _exact_row(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    adapter.require_exact_typed_value(
        dict(actual), dict(expected), f"{label} deterministic paired-row derivation"
    )


def _validate_science_and_aliases(payload: Mapping[str, Any], original: Mapping[str, Any]) -> None:
    source_projection = adapter._scientific_projection(original, output_form=False)
    output_projection = adapter._scientific_projection(payload, output_form=True)
    adapter.require_exact_typed_value(
        output_projection, source_projection,
        "portable restored producer scientific projection",
    )
    source_digest = adapter.digest_json(source_projection)
    mapping = payload.get("compatibility_adapter")
    mapping = adapter.require_exact_keys(
        mapping, adapter.COMPATIBILITY_ADAPTER_KEYS, "compatibility_adapter"
    )
    exact_mapping = {
        "schema": "monday_fresh_emx_report_interface_compatibility_v8",
        "status": "PASS_DECLARED_MAPPINGS_ONLY",
        "input_schema": adapter.INPUT_SCHEMA,
        "output_schema": adapter.OUTPUT_SCHEMA,
        "identity_status_mapping": "PASS=>PASS_7298_OF_7298_only_after_exact_counts_and_receipt_binding",
        "stage06_terminalization_mapping": "exact_known_stale_template=>terminal_stage07_stage08_identity_PASS",
        "reporting_label_mapping": f"{adapter.SOURCE_REPORTING_LABEL}=>{adapter.REPORT_REPORTING_LABEL}",
        "metric_version_mapping": f"{adapter.SOURCE_METRIC_VERSION}=>{adapter.REPORT_METRIC_VERSION}",
        "fixed_bin_variant_mapping": f"{adapter.PRIMARY_FIXED_BIN_VARIANT}=>{adapter.REPORT_PRIMARY_VARIANT}",
        "primary_joint_derivation": "paired_rows_fixed_spans_q_floor_numpy_linear_v1",
        "source_scientific_projection_sha256": source_digest,
        "output_restored_scientific_projection_sha256": source_digest,
        "numeric_value_or_denominator_mutation_allowed": False,
        "proxy_emx_target_chains_may_be_combined": False,
        "q_floor_is_primary_and_exact_q_is_secondary": True,
        "survivor_metrics_are_original_10000_unconditional": False,
    }
    for key, expected in exact_mapping.items():
        adapter.require_exact_typed_value(
            mapping.get(key), expected, f"compatibility mapping.{key}"
        )
    feature_rows = payload.get("comparison_feature_metrics")
    if not isinstance(feature_rows, list) or not feature_rows:
        raise adapter.GateError("portable feature metrics are absent")
    for index, row in enumerate(feature_rows):
        if (
            not isinstance(row, dict)
            or row.get("metric_contract_version") != adapter.REPORT_METRIC_VERSION
            or row.get("producer_metric_contract_version") != adapter.SOURCE_METRIC_VERSION
        ):
            raise adapter.GateError(f"portable metric version alias drift at row {index}")
    primary_rows, panel_values = adapter.derive_primary_engineering_joint(payload["paired_feature_rows"])
    actual_primary = [
        row
        for row in payload.get("joint_metrics", [])
        if isinstance(row, dict) and row.get("adapter_derivation") is not None
    ]
    if len(actual_primary) != len(primary_rows):
        raise adapter.GateError("portable primary engineering-joint rows are missing or duplicated")
    for index, (actual, expected) in enumerate(zip(actual_primary, primary_rows)):
        _exact_row(actual, expected, f"primary engineering-joint row {index}")
    expected_bins = adapter.validate_and_alias_fixed_bins(original["fixed_bin_metrics"], panel_values)
    adapter.require_exact_typed_value(
        payload.get("fixed_bin_metrics"), expected_bins,
        "portable fixed-bin alias/counts from paired-row recomputation",
    )
    required_scopes = {"target_vs_proxy", "target_vs_emx", "proxy_vs_emx"}
    feature_scopes = {row.get("comparison_scope") for row in feature_rows}
    joint_scopes = {
        row.get("comparison_scope")
        for row in payload.get("joint_metrics", [])
        if isinstance(row, dict)
    }
    if not required_scopes.issubset(feature_scopes) or not required_scopes.issubset(joint_scopes):
        raise adapter.GateError("three target/proxy/EMX chains are not separately represented")


def _validate_interface_semantics(payload: Mapping[str, Any]) -> None:
    adapter.require_exact_keys(payload, adapter.PORTABLE_ROOT_KEYS, "portable interface root")
    adapter.reject_authority_like_keys(payload, "portable interface")
    adapter.validate_exact_count_types(payload, "portable interface")
    if payload.get("schema") != adapter.OUTPUT_SCHEMA or payload.get("status") != "complete":
        raise adapter.GateError("unexpected portable interface schema/status")
    run = adapter.require_exact_keys(payload.get("run"), adapter.PORTABLE_RUN_KEYS, "portable run")
    exact_run = {
        "sampling_mode": "all_gate_pass",
        "planned_emx_count": 7298,
        "gds_pass_count": 7298,
        "calibre_pass_count": 7298,
        "calibre_blocking_fail_count": 75,
        "calibre_nonblocking_warning_count": 7373,
        "emx_complete_count": 7298,
        "emx_fail_count": 0,
        "terminal_status": "complete",
        "fresh_emx_reporting_label": adapter.REPORT_REPORTING_LABEL,
        "fresh_emx_evidence_scope_detail": adapter.SOURCE_REPORTING_LABEL,
        "selection_is_response_blind": True,
        "selection_strata": {"legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306},
        "selection_weights_path": None,
        "selection_weights_sha256": None,
        "survivor_scope": adapter.SURVIVOR_SCOPE,
    }
    for key, expected in exact_run.items():
        adapter.require_exact_typed_value(run[key], expected, f"portable run.{key}")
    adapter.require_exact_typed_value(
        run.get("survivor_conditioning_statement"),
        adapter.SURVIVOR_CONDITIONING_STATEMENT,
        "portable survivor conditioning statement",
    )
    identity = adapter.require_exact_keys(
        payload.get("terminal_normalized_gds_identity_audit"),
        adapter.PORTABLE_IDENTITY_KEYS,
        "portable normalized-GDS identity audit",
    )
    expected_identity = {
        "status": "PASS_7298_OF_7298",
        "producer_status": "PASS",
        "expected_candidate_count": 7298,
        "algorithm": adapter.IDENTITY_ALGORITHM,
        "terminal_match_count": 7298,
        "terminal_mismatch_count": 0,
        "result_publication_allowed": True,
    }
    for key, expected in expected_identity.items():
        adapter.require_exact_typed_value(identity.get(key), expected, f"portable identity.{key}")
    stage06 = adapter.require_exact_keys(
        payload.get("fresh_emx_stage06_running_state"),
        adapter.PORTABLE_STAGE06_KEYS,
        "portable Stage06/07/08 terminal state",
    )
    expected_stage06 = {
        "producer_template_status": "RUNNING_NO_RESULT_AVAILABLE",
        "status": "TERMINAL_PASS_7298_OF_7298_NO_PENDING_RESULTS",
        "identity_audit_gate_status": "PASS_7298_OF_7298_NORMALIZED_EXACT_LAYOUT_STREAM_IDENTITY",
        "full_7298_normalized_identity_terminal_audit_present": True,
        "stage07_result_present": True,
        "stage08_result_present": True,
        "numeric_fresh_emx_claim_allowed": True,
        "expected_candidate_count": 7298,
    }
    for key, expected in expected_stage06.items():
        adapter.require_exact_typed_value(stage06.get(key), expected, f"portable Stage06.{key}")
    metric = adapter.require_exact_keys(
        payload.get("fresh_metric_contract"),
        adapter.PORTABLE_METRIC_CONTRACT_KEYS,
        "portable fresh metric contract",
    )
    expected_metric = {
        "status": "frozen_before_results_stats_v2",
        "primary_error_representations": ["raw_absolute", "fixed_frame_normalized"],
        "k_fixed_frame_span": 1.0,
        "k_target_relative_percentage_primary_allowed": False,
        "k_target_relative_percentage_composite_gate_allowed": False,
        "q_floor_shortfall_required": True,
        "bins_frozen_before_results_required": True,
        "overflow_bin_required": True,
        "axis_limit_source": "preregistered_fixed_contract",
        "observed_p99_adaptive_axis_allowed": False,
        "statistics_v1_k_ape_p99_adaptive_primary_allowed": False,
        "producer_metric_contract_version": adapter.SOURCE_METRIC_VERSION,
        "report_interface_metric_contract_version": adapter.REPORT_METRIC_VERSION,
        "primary_joint_adapter_derivation": "paired_rows_fixed_spans_q_floor_numpy_linear_v1",
    }
    for key, expected in expected_metric.items():
        adapter.require_exact_typed_value(metric.get(key), expected, f"portable metric contract.{key}")
    release = payload.get("complete_emx_release_chain")
    if not isinstance(release, Mapping):
        raise adapter.GateError("portable rowwise/nonfiltering/Q-floor/RMSE-forbidden contract drift")
    for key, expected in adapter.RELEASE_FLAG_VALUES.items():
        adapter.require_exact_typed_value(
            release.get(key), expected, f"portable release flag.{key}"
        )
    for key in ("stage_counts", "comparison_feature_metrics", "joint_metrics", "q_metrics", "fixed_bin_metrics", "paired_feature_rows"):
        if not isinstance(payload.get(key), list) or not payload[key]:
            raise adapter.GateError(f"portable {key} is absent")
    adapter.validate_stage_funnel(payload["stage_counts"])
    adapter.validate_completion_contract(payload)
    if not any(row.get("is_overflow") is True for row in payload["fixed_bin_metrics"]):
        raise adapter.GateError("explicit fixed-bin overflow rows are absent")
    adapter.validate_table_cardinalities(payload, portable=True)


def _validate_portable_release_closure(
    payload: Mapping[str, Any],
    roles: Mapping[str, Mapping[str, Any]],
    source_raw_by_role: Mapping[str, bytes],
) -> None:
    release = adapter.require_exact_keys(
        payload.get("complete_emx_release_chain"),
        set(adapter.RELEASE_RECORD_BINDINGS.values())
        | {"launcher_preflight_terminals"}
        | set(adapter.RELEASE_FLAG_VALUES),
        "portable complete_emx_release_chain",
    )
    for source_role, release_key in adapter.RELEASE_RECORD_BINDINGS.items():
        record = roles[f"producer__{source_role}"]
        public_record = release.get(release_key)
        adapter.require_record(
            public_record, f"portable release {release_key}", public=True
        )
        adapter._same_file_identity(
            public_record,
            {"path": record.get("path"), "sha256": record.get("sha256")},
            f"portable release/source {release_key}",
        )
        if len(source_raw_by_role[f"producer__{source_role}"]) != public_record["size_bytes"]:
            raise adapter.GateError(
                f"portable release/source {release_key} public size_bytes drift"
            )
    launchers = adapter.require_exact_keys(
        release.get("launcher_preflight_terminals"),
        adapter.LAUNCHER_PREFLIGHT_KEYS,
        "portable launcher_preflight_terminals",
    )
    for key in sorted(adapter.LAUNCHER_PREFLIGHT_KEYS):
        record = roles[f"producer__canonical_{key}"]
        public_record = launchers.get(key)
        adapter.require_record(public_record, f"portable release launcher {key}", public=True)
        adapter._same_file_identity(
            public_record,
            {"path": record.get("path"), "sha256": record.get("sha256")},
            f"portable release/source launcher {key}",
        )
        if len(source_raw_by_role[f"producer__canonical_{key}"]) != public_record["size_bytes"]:
            raise adapter.GateError(
                f"portable release/source launcher {key} public size_bytes drift"
            )
    for key, expected in adapter.RELEASE_FLAG_VALUES.items():
        adapter.require_exact_typed_value(
            release.get(key), expected, f"portable complete release flag.{key}"
        )


def _load_portable_interface_held(
    interface_path: Path,
    expected_interface_sha256: str,
    adapter_receipt_path: Path,
    expected_adapter_receipt_sha256: str,
    bundle_lease: adapter.HeldRootLease,
    held_snapshots: dict[tuple[str, str], adapter.HeldRegularSnapshot],
) -> dict[str, Any]:
    bundle_root = interface_path.parent
    adapter_receipt = _load_adapter_receipt(
        bundle_root,
        interface_path,
        expected_interface_sha256,
        adapter_receipt_path,
        expected_adapter_receipt_sha256,
        root_lease=bundle_lease,
        held_snapshots=held_snapshots,
    )
    raw = _read_bundle_regular(
        bundle_root,
        interface_path.name,
        expected_interface_sha256,
        "portable interface entry",
        root_lease=bundle_lease,
        held_snapshots=held_snapshots,
    )
    payload = adapter.strict_json_bytes(raw, "portable interface")
    if not isinstance(payload, dict):
        raise adapter.GateError("portable interface root must be an object")
    _active_paths_are_nonabsolute(payload)
    _validate_interface_semantics(payload)
    source_files = payload.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise adapter.GateError("portable source_files closure is absent")
    roles: dict[str, Mapping[str, Any]] = {}
    source_raw_by_role: dict[str, bytes] = {}
    origin_raw: dict[tuple[str, str], bytes] = {}
    for position, record in enumerate(source_files):
        record = adapter.require_exact_keys(
            record,
            {
                "role", "path", "sha256", "size_bytes",
                "origin_path_sha256", "origin_path_was_absolute",
            },
            f"portable source record {position}",
        )
        if not isinstance(record.get("role"), str):
            raise adapter.GateError(f"portable source record {position} role is invalid")
        role = record["role"]
        if role in roles:
            raise adapter.GateError(f"portable source role is duplicated: {role}")
        if "original_path" in record:
            raise adapter.GateError("portable source record leaks an active original path")
        source_raw = _read_bundle_regular(
            bundle_root, record.get("path"), record.get("sha256"), f"source {role}",
            expected_size_bytes=record.get("size_bytes"),
            root_lease=bundle_lease,
            held_snapshots=held_snapshots,
        )
        origin_hash = adapter.require_sha(record.get("origin_path_sha256"), f"source {role} origin path hash")
        if type(record.get("origin_path_was_absolute")) is not bool:
            raise adapter.GateError(f"source {role} origin_path_was_absolute must be exact bool")
        origin_key = (origin_hash, adapter.require_sha(record.get("sha256"), f"source {role} SHA"))
        if origin_key in origin_raw and origin_raw[origin_key] != source_raw:
            raise adapter.GateError(f"portable origin reference has conflicting bytes: {role}")
        origin_raw[origin_key] = source_raw
        source_raw_by_role[role] = source_raw
        roles[role] = record
    if set(roles) != REQUIRED_SOURCE_ROLES:
        raise adapter.GateError(
            f"portable source role closure drift: missing={sorted(REQUIRED_SOURCE_ROLES-set(roles))}, "
            f"extra={sorted(set(roles)-REQUIRED_SOURCE_ROLES)}"
        )
    _validate_portable_release_closure(payload, roles, source_raw_by_role)
    closure = payload.get("portable_source_closure")
    closure = adapter.require_exact_keys(
        closure,
        {
            "status", "path_semantics", "manifest_path", "manifest_sha256",
            "source_record_count", "source_records_sha256",
            "absolute_active_source_dependency_count",
        },
        "portable_source_closure",
    )
    if closure.get("status") != "PASS_SELF_CONTAINED_INTERFACE_BUNDLE_RELATIVE":
        raise adapter.GateError("portable source closure status is not PASS")
    if closure.get("path_semantics") != "relative_to_interface_bundle_root" or closure.get("absolute_active_source_dependency_count") != 0:
        raise adapter.GateError("portable source path semantics drift")
    closure_raw = _read_bundle_regular(
        bundle_root,
        closure.get("manifest_path"),
        closure.get("manifest_sha256"),
        "portable closure manifest",
        root_lease=bundle_lease,
        held_snapshots=held_snapshots,
    )
    closure_manifest = adapter.strict_json_bytes(closure_raw, "portable closure manifest")
    if (
        set(closure_manifest) != {
            "schema", "status", "path_semantics", "producer_interface_sha256",
            "mirror_manifest_sha256", "source_record_count",
            "discovered_control_source_record_count", "source_records_sha256", "records",
        }
        or closure_manifest.get("schema") != "portable_complete_emx_source_closure_manifest_v1"
        or closure_manifest.get("status") != "PASS_SELF_CONTAINED_INTERFACE_BUNDLE_RELATIVE"
        or closure_manifest.get("path_semantics") != "relative_to_interface_bundle_root"
        or closure_manifest.get("records") != source_files
        or closure_manifest.get("source_records_sha256") != adapter.digest_json(source_files)
        or closure.get("source_records_sha256") != adapter.digest_json(source_files)
        or closure_manifest.get("source_record_count") != len(source_files)
        or closure.get("source_record_count") != len(source_files)
    ):
        raise adapter.GateError("portable source closure manifest does not exactly bind source_files")
    if (
        adapter_receipt.get("closure_manifest_path") != closure.get("manifest_path")
        or adapter_receipt.get("closure_manifest_sha256") != closure.get("manifest_sha256")
        or adapter_receipt.get("source_record_count") != len(source_files)
    ):
        raise adapter.GateError("ADAPTER_RECEIPT does not bind the active closure manifest/count")
    original_record = roles["producer_complete_interface_original"]
    original_raw = _read_bundle_regular(
        bundle_root,
        original_record["path"],
        original_record["sha256"],
        "original producer interface",
        expected_size_bytes=original_record.get("size_bytes"),
        root_lease=bundle_lease,
        held_snapshots=held_snapshots,
    )
    original = adapter.strict_json_bytes(original_raw, "original producer interface")
    adapter.validate_producer_interface(original)
    if closure_manifest.get("producer_interface_sha256") != adapter.sha_bytes(original_raw):
        raise adapter.GateError("closure manifest producer-interface SHA drift")
    if adapter_receipt.get("input_interface_sha256") != adapter.sha_bytes(original_raw):
        raise adapter.GateError("ADAPTER_RECEIPT input producer-interface SHA drift")

    def read_original_ref(original_path: str, expected_sha: str, label: str) -> bytes:
        key = (hashlib.sha256(original_path.encode("utf-8")).hexdigest(), expected_sha)
        try:
            return origin_raw[key]
        except KeyError as exc:
            raise adapter.GateError(f"{label} is absent from portable origin closure") from exc

    discovered_refs = adapter.validate_bound_source_documents(original, read_original_ref)
    expected_refs = adapter._all_source_refs(original, discovered_refs)
    for role, original_path, expected_sha in expected_refs:
        record = roles.get(role)
        if record is None:
            raise adapter.GateError(f"portable source role absent after source parsing: {role}")
        if (
            record.get("sha256") != expected_sha
            or record.get("origin_path_sha256") != hashlib.sha256(original_path.encode("utf-8")).hexdigest()
        ):
            raise adapter.GateError(f"portable source origin/SHA binding drift: {role}")
    if adapter_receipt.get("discovered_control_source_record_count") != len(discovered_refs):
        raise adapter.GateError("ADAPTER_RECEIPT discovered control-source count drift")
    if closure_manifest.get("discovered_control_source_record_count") != len(discovered_refs):
        raise adapter.GateError("closure manifest discovered control-source count drift")
    required_bindings = {
        "terminal_normalized_gds_identity_receipt": (
            payload["terminal_normalized_gds_identity_audit"]["receipt_path"],
            payload["terminal_normalized_gds_identity_audit"]["receipt_sha256"],
        ),
        "statistics_v2_preregistration": (
            payload["fresh_metric_contract"]["statistics_v2_manifest_path"],
            payload["fresh_metric_contract"]["statistics_v2_manifest_sha256"],
        ),
        "statistics_v2_readme": (
            payload["fresh_metric_contract"]["statistics_v2_readme_path"],
            payload["fresh_metric_contract"]["statistics_v2_readme_sha256"],
        ),
        "executed_statistics_copy": (
            payload["run"]["mars_executed_statistics_copy_path"],
            payload["run"]["mars_executed_statistics_copy_sha256"],
        ),
    }
    for role, (path, sha) in required_bindings.items():
        record = roles[role]
        if record.get("path") != path or record.get("sha256") != sha:
            raise adapter.GateError(f"active portable field is not bound to source role {role}")
    selection_path = payload["run"].get("selection_manifest_path")
    selection_sha = payload["run"].get("selection_manifest_sha256")
    if not any(record.get("path") == selection_path and record.get("sha256") == selection_sha for record in source_files):
        raise adapter.GateError("selection manifest is not in portable source closure")
    _validate_science_and_aliases(payload, original)
    mapping = payload["compatibility_adapter"]
    if (
        adapter_receipt.get("source_scientific_projection_sha256")
        != mapping.get("source_scientific_projection_sha256")
        or adapter_receipt.get("output_restored_scientific_projection_sha256")
        != mapping.get("output_restored_scientific_projection_sha256")
    ):
        raise adapter.GateError("ADAPTER_RECEIPT scientific projection binding drift")
    return payload


def load_portable_interface(
    interface_path: Path,
    expected_interface_sha256: str,
    adapter_receipt_path: Path,
    expected_adapter_receipt_sha256: str,
) -> dict[str, Any]:
    bundle_root = interface_path.parent
    bundle_lease = adapter.HeldRootLease(bundle_root, "portable interface bundle root")
    held_snapshots: dict[tuple[str, str], adapter.HeldRegularSnapshot] = {}
    try:
        payload = _load_portable_interface_held(
            interface_path,
            expected_interface_sha256,
            adapter_receipt_path,
            expected_adapter_receipt_sha256,
            bundle_lease,
            held_snapshots,
        )
        bundle_lease.verify_named_continuity()
        for snapshot in held_snapshots.values():
            snapshot.verify_named_continuity(verify_root=False)
        return payload
    finally:
        for snapshot in held_snapshots.values():
            snapshot.close()
        bundle_lease.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True, type=Path)
    parser.add_argument("--expected-interface-sha256", required=True)
    parser.add_argument("--adapter-receipt", required=True, type=Path)
    parser.add_argument("--expected-adapter-receipt-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_portable_interface(
        args.interface,
        args.expected_interface_sha256,
        args.adapter_receipt,
        args.expected_adapter_receipt_sha256,
    )
    receipt = {
        "schema": "portable_complete_emx_report_consumer_v8_receipt",
        "status": "PASS_PORTABLE_REPORT_INTERFACE_COMPATIBILITY_V8",
        "interface_sha256": adapter.require_sha(
            args.expected_interface_sha256, "expected portable interface SHA"
        ),
        "source_record_count": len(payload["source_files"]),
        "stage_count_row_count": len(payload["stage_counts"]),
        "comparison_scope_count": 3,
        "metric_values_printed": False,
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
