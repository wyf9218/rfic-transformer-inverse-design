#!/usr/bin/env python3
"""Synthetic, result-blind hostile tests for report-interface compatibility v8."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping

import adapt_complete_emx_interface_v8 as adapter
import consume_portable_emx_interface_v8 as consumer


WORKSPACE = Path(__file__).resolve().parents[3]
PHYSICAL_EVIDENCE_ROOT = (
    WORKSPACE / "reports/historical_200k_fixed10k_mars_physical_20260822"
)


FEATURE_FIELDS = [
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "feature_key", "feature", "unit", "row_count", "metric_contract_version",
    "bias_physical", "mae_physical", "rmse_physical", "bias_fixed_frame_fraction",
    "mae_fixed_frame_fraction", "rmse_fixed_frame_range_fraction", "p50_physical",
    "p90_physical", "p95_physical", "p99_physical", "maximum_physical",
    "p50_fixed_frame_fraction", "p90_fixed_frame_fraction", "p95_fixed_frame_fraction",
    "p99_fixed_frame_fraction", "maximum_fixed_frame_fraction",
]
JOINT_FIELDS = [
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "metric_variant", "row_count", "joint_mae_fraction", "joint_rmse_fraction",
    "p50_fraction", "p90_fraction", "p95_fraction", "p99_fraction", "maximum_fraction",
]
Q_FIELDS = [
    "panel_order", "panel_key", "panel", "row_count", "q_target_met_fraction",
    "q_shortfall_mae", "q_shortfall_rmse", "q_shortfall_p50", "q_shortfall_p90",
    "q_shortfall_p95", "q_shortfall_p99", "q_shortfall_max",
]
BIN_FIELDS = [
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "metric_variant", "bin_order", "bin_label", "lower_fraction", "upper_fraction_or_inf",
    "is_overflow", "count", "denominator", "fraction",
]
STAGE_FIELDS = ["stage_order", "stage", "eligible", "completed", "status", "denominator_note"]

HOSTILE_GATE_GROUPS = {
    "test_02_source_terminals_exact_json_schema_pass_7298_and_false_rows": [
        "H01_STAGE07_FAIL", "H02_STAGE08_7297", "H03_CONTROLLER_ROWS_EMBEDDED",
        "H04_PREFLIGHT_SCHEMA", "H05_CONTROLLER_NON_JSON",
    ],
    "test_03_identity_receipt_exact_result_blind_semantics": [
        "H06_IDENTITY_FAIL", "H07_IDENTITY_RESULT_ACCESS",
    ],
    "test_04_false_authority_and_outcome_binding": [
        "H08_TRANSPORT_AUTHORITY", "H09_SCIENCE_AUTHORITY", "H10_PIDFD_FALSE",
    ],
    "test_05_consumer_requires_expected_interface_and_receipt_sha": [
        "H11_INTERFACE_SHA", "H12_RECEIPT_SHA",
    ],
    "test_06_scientific_contract_fields_fail_closed": [
        "H13_RESPONSE_BLIND", "H14_SELECTION_STRATA", "H15_K_SPAN", "H16_Q_FLOOR",
        "H17_METRIC_VERSION", "H18_GEOMETRY_ROWS_BOUND", "H19_THREE_VALUES_BOUND",
        "H20_RMSE_ADDITION", "H21_FULL_BAND_FILTER",
    ],
    "test_07_table_cardinalities_and_panels_are_hard_gates": [
        "H22_FEATURE_35", "H23_EXACT_JOINT_8", "H24_Q_2", "H25_PAIRED_29191",
        "H26_PANEL_IDENTITY_DUPLICATE",
    ],
    "test_08_fixed_bin_label_scope_and_count_are_exact": [
        "H27_BIN_LABEL", "H28_BIN_SCOPE", "H29_BIN_SCOPE_LABEL", "H30_BIN_COUNT",
    ],
    "test_09_internal_bundle_source_ancestor_symlink_is_rejected": ["H31_BUNDLE_ANCESTOR_SYMLINK"],
    "test_10_internal_mirror_ancestor_symlink_is_rejected": ["H32_MIRROR_ANCESTOR_SYMLINK"],
    "test_11_missing_or_tampered_source_closure_is_rejected": [
        "H33_MISSING_IDENTITY_SOURCE", "H34_SOURCE_BYTE_TAMPER",
    ],
    "test_12_no_clobber_output_is_rejected": ["H35_OUTPUT_CLOBBER"],
    "test_13_contract_has_no_execution_result_or_go_authority": ["H36_NO_EXECUTION_AUTHORITY"],
    "test_15_strict_json_rejects_nonfinite_constants_and_duplicate_keys": [
        "H37_JSON_NAN", "H38_JSON_INFINITY", "H39_JSON_NEG_INFINITY",
        "H40_JSON_DUPLICATE_KEY", "H41_CANONICAL_NAN",
    ],
    "test_16_complete_release_chain_roles_records_and_cross_bindings_are_exact": [
        "H42_SOURCE_ROLE_MISSING", "H43_SOURCE_RECORD_EXTRA_KEY",
        "H44_THREE_TERMINAL_MISSING", "H45_FULL_MANIFEST_RECORD_MISMATCH",
    ],
    "test_17_three_full_manifests_internal_and_launcher_semantics_are_parsed": [
        "H46_THREE_TERMINAL_FAIL", "H47_FULL_TERMINAL_CHECK_FALSE",
        "H48_THREE_MANIFEST_FAIL", "H49_FULL_MANIFEST_SCHEMA",
        "H50_FULL_INTERNAL_7297", "H51_LAUNCHER_CHECK_FALSE",
    ],
    "test_18_portable_release_and_source_records_remain_fail_closed_if_caller_rebinds_sha": [
        "H52_PORTABLE_RELEASE_REBIND", "H53_PORTABLE_SOURCE_RECORD_REBIND",
    ],
    "test_19_preflight_and_launcher_evidence_are_exact_and_parent_bound": [
        "D01_RESUME_EVIDENCE_EMPTY", "D02_THREE_PREFLIGHT_EVIDENCE_EMPTY",
        "D03_FULL_PREFLIGHT_EVIDENCE_EMPTY", "D04_LAUNCHER_EVIDENCE_EMPTY",
        "D05_LAUNCHER_EVIDENCE_EMPTY", "D06_LAUNCHER_EVIDENCE_EMPTY",
        "D07_LAUNCHER_EVIDENCE_EMPTY", "H54_THREE_PARENT_MISMATCH",
        "H55_FULL_PARENT_MISMATCH", "H56_RESUME_SOURCE_ROLE_EXTRA",
    ],
    "test_20_identity_terminal_manifest_and_internal_records_are_cross_bound": [
        "X01_IDENTITY_GATE_CROSS_MISMATCH", "X02_STAGE07_MANIFEST_MISMATCH",
        "X03_STAGE08_MANIFEST_MISMATCH", "X04_THREE_MANIFEST_MISMATCH",
        "X05_FULL_MANIFEST_MISMATCH", "X06_FULL_INTERNAL_MISMATCH",
        "X07_FULL_MANIFEST_EXTRA_INPUT_SOURCE", "H57_FULL_RUNTIME_ROLE_EXTRA",
    ],
    "test_21_frozen_v8_authorization_process_go_and_runtime_semantics": [
        "A01_AUTH_LAUNCHER_MISMATCH", "A02_PROCESS_ALGORITHM", "A03_PROCESS_DELTA",
        "A04_PROCESS_ARGV", "H58_PROCESS_CMDLINE_SHA", "H59_GO_CONTRACT_SHA",
        "H60_GO_RUNTIME_ROOT", "H61_RUNTIME_MANIFEST_ROOT", "H62_CONTRACT_ALGORITHM",
        "H63_PROCESS_DELTA_ZERO_PASS", "H64_PROCESS_DELTA_120S_PASS",
        "H77_LAUNCH_RECEIPT_SCHEMA", "H78_LAUNCH_RECEIPT_TIME_DELTA",
        "H79_LAUNCH_RECEIPT_SOURCE_SHA", "H80_LAUNCH_RECEIPT_SCRIPT",
        "H81_LAUNCH_RECEIPT_NO_CLOBBER",
    ],
    "test_22_scientific_tables_panel_and_funnel_are_recomputed_exactly": [
        "R01_FEATURE_SUMMARY_TAMPER", "R02_FEATURE_TYPE_TAMPER",
        "R03_EXACT_JOINT_TAMPER", "R04_Q_SUMMARY_TAMPER",
        "R05_PANEL_K_CLASSIFICATION", "R06_STAGE_STATUS_LABEL",
        "R07_END_TO_END_AGGREGATE_AND_PANEL_TAMPER", "H65_BOOL_AS_NUMBER",
        "H66_NONFINITE_PAIRED_VALUE", "H67_K_EXACT_0P8_PASS",
    ],
    "test_23_root_ancestors_and_portable_semantic_keysets_are_fail_closed": [
        "P11_MIRROR_ROOT_ANCESTOR_SYMLINK", "P13_CALLER_REHASH_TOP_LEVEL_EXTRA",
        "P14_CALLER_REHASH_MAPPING_EXTRA", "H68_BUNDLE_ROOT_ANCESTOR_SYMLINK",
        "H69_PORTABLE_ROOT_MISSING", "H70_MAPPING_MISSING",
    ],
    "test_24_production_record_shapes_and_full_manifest_roles_are_exact": [
        "H71_PUBLIC_RECORD_SIZE_MISSING", "H72_PUBLIC_RECORD_SIZE_TAMPER",
        "H73_RESUME_SOURCE_ROLE_MISSING", "H74_FULL_INPUT_ROLE_MISSING",
        "H75_FULL_RUNTIME_ROLE_MISSING", "H76_LAUNCHER_CONTROLLER_BASENAME",
    ],
    "test_25_formal_v5_contract_and_run_authority_failures_are_closed": [
        "F03_CONTRACT_MISSING_EXECUTION_UNITS",
        "F04_CONTRACT_EXTRA_AUTHORITY_DIRECT",
        "F05_CONTRACT_EXTRA_AUTHORITY_E2E",
        "F06_RUN_EXTRA_AUTHORITY_E2E",
        "V7A01_CONTRACT_NESTED_EXTRA",
        "V7A02_CONTRACT_AUTHORITY_TYPE_CONFUSION",
        "V7A03_RUN_KEYSET_MISSING",
    ],
    "test_26_formal_v5_public_size_failures_are_closed": [
        "F07_GLOBAL_FALSE_SIZE_DIRECT",
        "F08_GLOBAL_FALSE_SIZE_E2E",
        "V7S01_LAUNCHER_COMMON_FALSE_SIZE",
        "V7S02_LAUNCHER_CONTROLLER_FALSE_SIZE",
        "V7S03_PORTABLE_RELEASE_FALSE_SIZE_CONSUMER",
    ],
    "test_27_formal_v5_count_type_failures_are_closed": [
        "F01_FUNNEL_BOOL_FLOAT_DIRECT",
        "F02_FUNNEL_BOOL_FLOAT_E2E",
        "V7C01_RUN_BOOL_COUNT",
        "V7C02_RUN_INTEGRAL_FLOAT_COUNT",
        "V7C03_IDENTITY_BOOL_COUNT",
        "V7C04_TABLE_INTEGRAL_FLOAT_COUNT",
        "V7C05_CONTRACT_INTEGRAL_FLOAT_COUNT",
        "V7C06_SOURCE_ROW_INTEGRAL_FLOAT_COUNT",
        "V7C07_PORTABLE_RUN_FLOAT_CONSUMER",
    ],
    "test_28_combined_authority_size_and_count_attack_is_closed": [
        "V7M01_COMBINED_DIRECT",
        "V7M02_COMBINED_E2E",
    ],
    "test_29_formal_v6_nested_exact_schema_and_authority_injection_are_closed": [
        "H04_NESTED_AUTHORITY_INJECTION",
    ],
    "test_30_formal_v6_consumer_primitive_aliases_are_closed": [
        "B02_PORTABLE_BOOL_FIELD_AS_INT",
        "B04_PORTABLE_CONTINUOUS_AS_BOOL",
        "H04_MAPPING_FALSE_AS_INT",
        "F03_RELEASE_FALSE_AS_INT",
    ],
    "test_31_formal_v6_hash_parse_and_held_source_continuity_are_closed": [
        "G03_CONSUMER_HASH_PARSE_SAME_BYTES",
        "V7B01_ADAPTER_SOURCE_NAMED_HELD_CONTINUITY",
    ],
    "test_32_formal_v6_output_path_and_single_link_gates_are_closed": [
        "G01_OUTPUT_ANCESTOR_SYMLINK",
        "G03_OUTPUT_ROOT_SWAP_TOCTOU",
        "G02_HARDLINK_SOURCE",
    ],
    "test_33_formal_v6_nested_artifact_bytes_are_in_exact_closure": [
        "D06_NESTED_ARTIFACT_BYTES_CLOSURE",
    ],
    "test_34_formal_v6_funnel_and_survivor_semantics_are_closed": [
        "D04_GDS_COUNT_CROSS_BIND",
        "H06_SURVIVOR_CONDITIONAL_NARRATIVE",
    ],
    "test_35_formal_v6_no_go_is_exactly_hash_bound": [
        "V7F01_FORMAL_V6_NEGATIVE_EVIDENCE_BINDING",
    ],
    "test_36_combined_formal_v6_attacks_fail_closed": [
        "V7X01_COMBINED_NESTED_TYPE_SURVIVOR_CONSUMER",
        "V7X02_COMBINED_PHANTOM_HARDLINK_OUTPUT_SCOPE_ADAPTER",
    ],
    "test_37_formal_v7_output_file_version_replacement_is_rejected": [
        "V8F01_OUTPUT_FILE_VERSION_CONTINUITY",
    ],
    "test_38_formal_v7_nested_roles_require_unique_artifact_identities": [
        "V8F02_NESTED_ROLE_IDENTITY_ONE_TO_ONE",
    ],
    "test_39_formal_v7_survivor_statement_is_exact_bound": [
        "V8F03_SURVIVOR_STATEMENT_EXACT_BINDING",
    ],
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(adapter.canonical_json_bytes(value))


def public_record(path: str, raw: bytes) -> dict[str, Any]:
    return {"path": path, "sha256": sha(raw), "size_bytes": len(raw)}


def terminal(schema: str, stage: str, checks: dict[str, bool], evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": schema,
        "overall_status": "PASS",
        "stage": stage,
        "finished_utc": "2026-08-22T00:00:00+00:00",
        "checks": checks,
        "evidence": evidence,
        "error": None,
        "result_rows_embedded": False,
    }


def paired_rows() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for prefix, panel, count in (
        ("legacy", "legacy_k_le_0p8", 5992),
        ("extension", "extension_k_gt_0p8", 1306),
    ):
        panel_label = "Legacy |K|≤0.8" if panel == "legacy_k_le_0p8" else "Extension |K|>0.8"
        for item in range(count):
            delta = (item % 17) / 1000.0
            targets = (
                1.0 + delta,
                1.5 + delta,
                12.0 + (item % 5) / 10.0,
                (0.4 + delta) if panel == "legacy_k_le_0p8" else (0.9 + delta / 4.0),
            )
            emx_values = (
                targets[0] + 0.05,
                targets[1] - 0.05,
                targets[2] - (1.0 if item % 2 == 0 else -0.5),
                targets[3] - 0.02,
            )
            for index, feature in enumerate(adapter.FEATURES):
                target = targets[index]
                emx = emx_values[index]
                proxy = (target + emx) / 2.0
                span = adapter.SPANS[feature]
                result.append(
                    {
                        "target_id": f"synthetic_{prefix}_{item:05d}",
                        "panel_key": panel,
                        "panel": panel_label,
                        "feature_key": feature,
                        "feature": adapter.FEATURE_LABELS[feature],
                        "unit": adapter.UNITS[feature],
                        "target_value": target,
                        "proxy_value": proxy,
                        "emx_value": emx,
                        "target_normalized_fraction": target / span,
                        "proxy_normalized_fraction": proxy / span,
                        "emx_normalized_fraction": emx / span,
                        "proxy_minus_emx_range_fraction": (proxy - emx) / span,
                    }
                )
    return result


def feature_metrics() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    counts = {"overall": 7298, "legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306}
    for panel_order, panel_key, panel_label, _ in adapter.PANELS:
        for scope, scope_label in adapter.CHAINS:
            for feature in adapter.FEATURES:
                result.append(
                    {
                        "panel_order": panel_order, "panel_key": panel_key, "panel": panel_label,
                        "comparison_scope": scope, "comparison_scope_label": scope_label,
                        "feature_key": feature, "feature": adapter.FEATURE_LABELS[feature],
                        "unit": adapter.UNITS[feature], "row_count": counts[panel_key],
                        "metric_contract_version": adapter.SOURCE_METRIC_VERSION,
                        "bias_physical": 0.0, "mae_physical": 0.01, "rmse_physical": 0.02,
                        "bias_fixed_frame_fraction": 0.0, "mae_fixed_frame_fraction": 0.004,
                        "rmse_fixed_frame_range_fraction": 0.008, "p50_physical": 0.01,
                        "p90_physical": 0.02, "p95_physical": 0.02, "p99_physical": 0.02,
                        "maximum_physical": 0.02, "p50_fixed_frame_fraction": 0.004,
                        "p90_fixed_frame_fraction": 0.008, "p95_fixed_frame_fraction": 0.008,
                        "p99_fixed_frame_fraction": 0.008, "maximum_fixed_frame_fraction": 0.008,
                    }
                )
    return result


def joint_metrics() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    counts = {"overall": 7298, "legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306}
    for panel_order, panel_key, panel_label, _ in adapter.PANELS:
        for scope, scope_label in adapter.CHAINS:
            result.append(
                {
                    "panel_order": panel_order, "panel_key": panel_key, "panel": panel_label,
                    "comparison_scope": scope, "comparison_scope_label": scope_label,
                    "metric_variant": adapter.EXACT_Q_VARIANT, "row_count": counts[panel_key],
                    "joint_mae_fraction": 0.01, "joint_rmse_fraction": 0.02,
                    "p50_fraction": 0.01, "p90_fraction": 0.02, "p95_fraction": 0.02,
                    "p99_fraction": 0.02, "maximum_fraction": 0.02,
                }
            )
    return result


def q_metrics() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    counts = {"overall": 7298, "legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306}
    for panel_order, panel_key, panel_label, _ in adapter.PANELS:
        result.append(
            {
                "panel_order": panel_order, "panel_key": panel_key, "panel": panel_label,
                "row_count": counts[panel_key], "q_target_met_fraction": 0.5,
                "q_shortfall_mae": 0.75, "q_shortfall_rmse": 1.0, "q_shortfall_p50": 0.5,
                "q_shortfall_p90": 1.8, "q_shortfall_p95": 1.9,
                "q_shortfall_p99": 1.98, "q_shortfall_max": 2.0,
            }
        )
    return result


def fixed_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _, values_by_panel = adapter.derive_primary_engineering_joint(rows)
    result: list[dict[str, Any]] = []
    for panel_order, panel_key, panel_label, _ in adapter.PANELS:
        values = values_by_panel[panel_key]
        for bin_order, (lower, upper) in enumerate(zip(adapter.FIXED_BINS[:-1], adapter.FIXED_BINS[1:])):
            overflow = upper == float("inf")
            count = sum(value >= lower and (value < upper or overflow) for value in values)
            result.append(
                {
                    "panel_order": panel_order, "panel_key": panel_key, "panel": panel_label,
                    "comparison_scope": "target_vs_emx",
                    "comparison_scope_label": "Target vs fresh EMX",
                    "metric_variant": adapter.PRIMARY_FIXED_BIN_VARIANT,
                    "bin_order": bin_order,
                    "bin_label": f"[{lower:g},{'inf' if overflow else format(upper, 'g')})",
                    "lower_fraction": lower,
                    "upper_fraction_or_inf": "inf" if overflow else upper,
                    "is_overflow": overflow, "count": count, "denominator": len(values),
                    "fraction": count / len(values),
                }
            )
    return result


def preregistration() -> dict[str, Any]:
    return {
        "schema": adapter.PREREG_SCHEMA,
        "status": "FROZEN_BEFORE_ANY_FRESH_EMX_RESULT",
        "feature_order": list(adapter.FEATURES),
        "fixed_frame_spans": dict(adapter.SPANS),
        "fixed_histogram_bins_fraction_of_full_scale": [0.0, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, "INF"],
        "denominators": {
            "original_target_count": 10000, "analytical_pass_count": 7926,
            "analytical_fail_count": 2074, "cadence_pass_count": 7373,
            "cadence_fail_count": 553,
        },
        "row_metrics": {
            "q_floor_shortfall": "max(target_qmin - achieved_qmin, 0)",
            "q_floor_shortfall_normalized": "q_floor_shortfall / 20",
            "q_floor_pass": "achieved_qmin >= target_qmin",
            "joint_engineering_fixed_frame_error": "sqrt(mean([r_lp^2, r_ls^2, r_q_shortfall^2, r_k^2]))",
        },
        "inference_boundary": "Survivor results are conditional descriptive statistics; no deployment-population CI is created.",
    }


Mutator = Callable[[dict[str, Any]], None]


def build_control_sources(
    mutators: dict[str, Mutator] | None = None,
    raw_overrides: dict[str, bytes] | None = None,
) -> tuple[dict[str, tuple[str, bytes]], dict[str, tuple[str, str, int]]]:
    mutators = mutators or {}
    raw_overrides = raw_overrides or {}
    paths: dict[str, str] = {}
    raws: dict[str, bytes] = {}

    def seal(key: str, value: dict[str, Any], *, basename: str | None = None) -> dict[str, Any]:
        paths[key] = f"/volumes/synthetic-only/{basename or key + '.json'}"
        if key in mutators:
            mutators[key](value)
        raw = raw_overrides.get(key, adapter.canonical_json_bytes(value))
        raws[key] = raw
        return public_record(paths[key], raw)

    def seal_bytes(key: str, raw: bytes, basename: str) -> dict[str, Any]:
        paths[key] = f"/volumes/synthetic-only/{basename}"
        raws[key] = raw_overrides.get(key, raw)
        return public_record(paths[key], raws[key])

    def record_source_key(name: str) -> str:
        return f"artifact_{sha(name.encode())[:20]}"

    def record(name: str, *, row_count: int | None = None) -> dict[str, Any]:
        raw = ("synthetic:" + name).encode()
        key = record_source_key(name)
        paths[key] = f"/synthetic/{name}"
        raws[key] = raw
        output = public_record(paths[key], raw)
        if row_count is not None:
            output["row_count"] = row_count
        return output

    def minimal(value: dict[str, Any]) -> dict[str, Any]:
        output = {"path": value["path"], "sha256": value["sha256"]}
        if "row_count" in value:
            output["row_count"] = value["row_count"]
        return output

    def stage_artifact(value: dict[str, Any], *, row_count: int | None = None) -> dict[str, Any]:
        output = {
            "exists": True, "path": value["path"], "sha256": value["sha256"],
            "size_bytes": value["size_bytes"],
        }
        if row_count is not None:
            output["row_count"] = row_count
        return output

    identity_summary = record("identity_summary.json")
    identity_rows = record("identity_rows.csv", row_count=7298)
    identity_ref = seal(
        "identity",
        {
            "finished_utc": "2026-08-22T00:00:00+00:00", "overall_status": "PASS",
            "physical_error_metrics_accessed": False, "raw_byte_equality_is_not_a_gate": True,
            "rows": minimal(identity_rows), "schema": adapter.IDENTITY_RECEIPT_SCHEMA,
            "summary": minimal(identity_summary),
            "timestamp_normalized_identity_is_primary_fail_closed_gate": True,
        },
    )
    prereg_ref = seal("prereg", preregistration(), basename="stage08_preregistration.json")
    identity_auditor = record("identity_auditor.py")
    identity_gate = {
        "receipt": identity_ref, "summary": identity_summary, "rows": identity_rows,
        "auditor": identity_auditor, "target_id_set_sha256": "4" * 64,
        "candidate_id_set_sha256": "5" * 64,
    }

    stage07_generator = record("stage07_generator.py")
    stage08_generator = record("stage08_generator.py")
    three_generator = record("three_generator.py")
    full_source_bindings = {
        key: record(f"full_{key}.bin") for key in sorted(adapter.FULL_SOURCE_BINDING_KEYS)
    }
    stage07_summary = record("stage07_summary.json")
    stage07_rows = record("stage07_rows.csv", row_count=7298)
    stage07_artifacts = {
        key: stage_artifact(record(f"stage07_{key}"))
        for key in sorted(adapter.MANIFEST_ARTIFACT_KEYS["stage07_manifest"])
    }
    stage07_artifacts["historical_200k_fresh_emx_statistics_summary.json"] = stage_artifact(stage07_summary)
    stage07_artifacts["historical_200k_fresh_emx_evaluated_rows.csv"] = stage_artifact(stage07_rows)
    stage07_manifest_ref = seal(
        "stage07_manifest",
        {"schema": adapter.STAGE07_MANIFEST_SCHEMA,
         "generated_utc": "2026-08-22T00:00:00+00:00",
         "script": stage_artifact(stage07_generator), "artifacts": stage07_artifacts},
    )
    stage07_ref = seal(
        "stage07",
        terminal(adapter.STAGE07_TERMINAL_SCHEMA, "Stage07", {"canonical_validation_passed": True},
                 {"summary": stage07_summary, "rows": stage07_rows, "manifest": stage07_manifest_ref}),
    )

    stage08_summary = record("stage08_summary.json")
    stage08_rows = record("stage08_rows.csv", row_count=7298)
    stage08_artifacts = {
        key: stage_artifact(record(f"stage08_{key}"))
        for key in sorted(adapter.MANIFEST_ARTIFACT_KEYS["stage08_manifest"])
    }
    stage08_artifacts["summary"] = stage_artifact(stage08_summary)
    stage08_artifacts["v2_rows"] = stage_artifact(stage08_rows, row_count=7298)
    for key in ("fixed_histogram_counts", "fixed_secondary_percent_histogram_counts"):
        base = record(f"stage08_{key}")
        stage08_artifacts[key] = stage_artifact(base, row_count=60)
    stage08_manifest_ref = seal(
        "stage08_manifest",
        {"schema": adapter.STAGE08_MANIFEST_SCHEMA, "artifacts": stage08_artifacts},
    )
    legacy_stage08 = record("legacy_stage08_terminal.json")
    stage08_ref = seal(
        "stage08",
        terminal(
            adapter.STAGE08_TERMINAL_SCHEMA, "Stage08",
            {"canonical_validation_passed": True, "upstream_stage07_pass": True},
            {"summary": stage08_summary, "rows": stage08_rows, "manifest": stage08_manifest_ref,
             "generator": stage08_generator, "preregistration": prereg_ref,
             "legacy_watch_terminal": legacy_stage08,
             "upstream_stage07_terminal": stage07_ref, "identity_gate": identity_gate},
        ),
    )

    watcher_script = seal_bytes("watcher_script", b"#!/bin/bash\n# synthetic result blind\n", "watch_stage07_08.sh")
    watcher_launch_ref = seal(
        "watcher_launch",
        {
            "schema": "synthetic_watcher_launch_v1",
            "status_at_receipt": "WAITING_FOR_STAGE06",
            "started_utc": "1970-01-01T00:00:02+00:00",
            "host": "synthetic.local",
            "pid": 7298,
            "stage06_supervisor_pid": 7297,
            "watcher_script": {
                "path": watcher_script["path"], "sha256": watcher_script["sha256"]
            },
            "physical_statistics_script_sha256": stage07_generator["sha256"],
            "v2_statistics_script_sha256": stage08_generator["sha256"],
            "preregistration_sha256": prereg_ref["sha256"],
            "expected_physical_count": 7298,
            "v2_is_primary_for_final_report": True,
            "v1_preserved_as_superseded_physical_evidence": True,
            "no_clobber": True,
        },
        basename="watcher_launch_receipt.json",
    )
    python_ref = seal_bytes("runtime_python", b"synthetic-python-binary\n", "python")
    common_ref = seal_bytes("common", b"synthetic release common\n", "release_chain_common_v5.py")
    controller_refs: dict[str, dict[str, Any]] = {}
    for basename in (
        "build_complete_emx_interface_v5.py", "resume_exact_watcher_stage07_08_v5.py",
        "run_full_band_v3_after_stage08_v5.py", "run_three_chain_after_stage08_v5.py",
    ):
        controller_refs[basename] = seal_bytes(
            f"controller_{basename}", f"synthetic {basename}\n".encode(), basename
        )

    full_spec = {
        "bundle_runtime_dir": "/synthetic/full_runtime",
        "generator_name": Path(full_source_bindings["v3_generator"]["path"]).name,
        "generator_sha256": full_source_bindings["v3_generator"]["sha256"],
        "base_generator_path": full_source_bindings["superseded_base_generator"]["path"],
        "base_generator_sha256": full_source_bindings["superseded_base_generator"]["sha256"],
        "panel_addendum_name": Path(full_source_bindings["panel_schema_addendum"]["path"]).name,
        "panel_addendum_sha256": full_source_bindings["panel_schema_addendum"]["sha256"],
        "method_preregistration_path": full_source_bindings["unchanged_method_preregistration"]["path"],
        "method_preregistration_sha256": full_source_bindings["unchanged_method_preregistration"]["sha256"],
        "stage06_config_path": full_source_bindings["unchanged_stage06_config"]["path"],
        "stage06_config_sha256": full_source_bindings["unchanged_stage06_config"]["sha256"],
    }
    # Make the bundle-derived paths agree exactly with the frozen full-band spec.
    full_source_bindings["panel_schema_addendum"]["path"] = str(
        Path(full_spec["bundle_runtime_dir"]) / full_spec["panel_addendum_name"]
    )
    full_source_bindings["v3_generator"]["path"] = str(
        Path(full_spec["bundle_runtime_dir"]) / full_spec["generator_name"]
    )
    paths[record_source_key("full_panel_schema_addendum.bin")] = full_source_bindings[
        "panel_schema_addendum"
    ]["path"]
    paths[record_source_key("full_v3_generator.bin")] = full_source_bindings[
        "v3_generator"
    ]["path"]
    contract = adapter.frozen_release_contract_reference()
    contract["active_watcher"].update(
        {
            "expected_host": "synthetic.local",
            "expected_pid": 7298,
            "expected_stage06_supervisor_pid": 7297,
            "expected_script_path": watcher_script["path"],
            "expected_script_sha256": watcher_script["sha256"],
            "launch_receipt_path": watcher_launch_ref["path"],
            "launch_receipt_sha256": watcher_launch_ref["sha256"],
            "launch_receipt_schema": "synthetic_watcher_launch_v1",
        }
    )
    contract["runtime"].update(
        {
            "python_path": python_ref["path"],
            "dependency_manifest_path": "/volumes/synthetic-only/runtime_manifest.json",
            "private_site_packages_root": "/volumes/synthetic-only/private_site_packages",
        }
    )
    contract["stage07"].update(
        {"generator_path": stage07_generator["path"], "generator_sha256": stage07_generator["sha256"]}
    )
    contract["stage08"].update(
        {
            "generator_path": stage08_generator["path"],
            "generator_sha256": stage08_generator["sha256"],
            "preregistration_path": prereg_ref["path"],
            "preregistration_sha256": prereg_ref["sha256"],
        }
    )
    contract["three_chain"].update(
        {"generator_path": three_generator["path"], "generator_sha256": three_generator["sha256"]}
    )
    contract["full_band_v3"].update(full_spec)
    contract_ref = seal("contract", contract, basename="RELEASE_CHAIN_V5_CONTRACT.json")
    bundle_files = []
    for basename, ref in {
        "RELEASE_CHAIN_V5_CONTRACT.json": contract_ref,
        "release_chain_common_v5.py": common_ref,
        **controller_refs,
    }.items():
        bundle_files.append({"relative_path": basename, "role": f"synthetic_{basename}",
                             "sha256": ref["sha256"], "size_bytes": ref["size_bytes"]})
    bundle_ref = seal(
        "bundle_manifest",
        {"schema": adapter.BUNDLE_MANIFEST_SCHEMA, "status": adapter.PREPARED_RESULT_FREE_STATUS,
         "bundle_role": "RESULT_FREE_PREPARED_CANDIDATE_NOT_LAUNCH_AUTHORITY",
         "created_utc": "2026-08-22T00:00:00+00:00", "execution_authorized": False,
         "file_count": len(bundle_files), "files": bundle_files, "manifest_self_inclusion": False,
         "unhashed_closure_files": ["BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
                                    "PREPARED_VALIDATION_OUTPUT.json", "SHA256SUMS"]},
        basename="BUNDLE_MANIFEST.json",
    )
    runtime_files = [
        {"relative_path": "matplotlib/__init__.py", "sha256": sha(b"mpl"), "size_bytes": 3, "mode": "0444"},
        {"relative_path": "matplotlib-1.dist-info/METADATA", "sha256": sha(b"mplmeta"), "size_bytes": 7, "mode": "0444"},
        {"relative_path": "numpy/__init__.py", "sha256": sha(b"np"), "size_bytes": 2, "mode": "0444"},
        {"relative_path": "numpy-1.dist-info/METADATA", "sha256": sha(b"npmeta"), "size_bytes": 6, "mode": "0444"},
    ]
    digest_lines = [f"{row['relative_path']}\0{row['sha256']}\0{row['size_bytes']}\0{row['mode']}\n" for row in runtime_files]
    runtime_root_digest = sha("".join(sorted(digest_lines)).encode())
    runtime_ref = seal(
        "runtime_manifest",
        {"schema": adapter.RUNTIME_MANIFEST_SCHEMA, "status": "FROZEN_RESULT_FREE_RUNTIME_IDENTITY",
         "site_packages_root": contract["runtime"]["private_site_packages_root"],
         "exact_file_set": True, "files": runtime_files, "root_digest": runtime_root_digest,
         "distributions": {
             "matplotlib": {"version": "1", "import_relative_path": "matplotlib/__init__.py",
                            "distribution_record_relative_path": "matplotlib-1.dist-info/METADATA"},
             "numpy": {"version": "1", "import_relative_path": "numpy/__init__.py",
                       "distribution_record_relative_path": "numpy-1.dist-info/METADATA"}}},
        basename="runtime_manifest.json",
    )
    argv = ["/bin/bash", watcher_script["path"]]
    process_identity = {
        "algorithm": adapter.WATCHER_PROCESS_IDENTITY_ALGORITHM,
        "boot_id": "synthetic-result-blind-boot-id",
        "cmdline_sha256": sha(json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode()),
        "exe_device": 1, "exe_inode": 2, "exe_realpath": "/bin/bash",
        "exe_sha256": sha(b"synthetic-bash"), "exe_size_bytes": 3,
        "launch_receipt_sha256": watcher_launch_ref["sha256"],
        "launch_receipt_after_proc_start_ns": 1_000_000_000, "pid": 7298, "ppid": 1,
        "proc_starttime_ticks": 100, "proc_start_unix_ns": 1_000_000_000,
        "script_sha256": watcher_script["sha256"], "stage06_supervisor_pid": 7297,
        "uid": 0, "argv": argv,
    }
    go_ref = seal(
        "independent_go",
        {"schema": adapter.INDEPENDENT_GO_SCHEMA, "status": adapter.INDEPENDENT_GO_STATUS,
         "bundle_manifest_sha256": bundle_ref["sha256"], "contract_sha256": contract_ref["sha256"],
         "exact_resume_only": True, "reviewed_utc": "2026-08-22T00:00:00+00:00",
         "reviewer": "synthetic-independent-reviewer",
         "runtime_dependency_manifest_path": runtime_ref["path"],
         "runtime_dependency_manifest_sha256": runtime_ref["sha256"],
         "runtime_dependency_root_digest": runtime_root_digest,
         "runtime_python_path": python_ref["path"], "runtime_python_sha256": python_ref["sha256"],
         "runtime_site_packages_root": contract["runtime"]["private_site_packages_root"],
         "scientific_release_authorized": False, "training_authorized": False,
         "transport_authorized": False, "watcher_process_identity": process_identity},
        basename="INDEPENDENT_REVIEW_GO.json",
    )
    launcher_refs: dict[str, dict[str, Any]] = {}
    for launcher_key in sorted(adapter.LAUNCHER_PREFLIGHT_KEYS):
        basename = launcher_key.removeprefix("launcher_preflight__")
        launcher_refs[launcher_key] = seal(
            launcher_key,
            terminal(
                adapter.LAUNCHER_PREFLIGHT_SCHEMA, "bound_launcher_authentication",
                {key: True for key in adapter.TERMINAL_SPECS["launcher_preflight"][2]},
                {"bundle_manifest": bundle_ref, "common": common_ref, "contract": contract_ref,
                 "controller": controller_refs[basename], "independent_review_go": go_ref,
                 "runtime_dependency_manifest": runtime_ref},
            ),
        )

    resume_launcher_key = "launcher_preflight__resume_exact_watcher_stage07_08_v5.py"
    preflight_ref = seal(
        "preflight",
        terminal(
            adapter.PREFLIGHT_TERMINAL_SCHEMA, "resume_stage07_08_preflight",
            {key: True for key in adapter.TERMINAL_SPECS["preflight"][2]},
            {"bundle_manifest": bundle_ref, "contract": contract_ref, "identity_gate": identity_gate,
             "launcher_authentication_terminal": launcher_refs[resume_launcher_key], "review_go": go_ref,
             "runtime_dependency_manifest": runtime_ref, "runtime_interpreter": python_ref,
             "sources": {"stage07_generator": stage07_generator, "stage08_generator": stage08_generator,
                         "stage08_preregistration": prereg_ref},
             "watcher_launch_receipt": watcher_launch_ref,
             "watcher_process_identity": process_identity, "watcher_script": watcher_script},
        ),
    )
    three_launcher_key = "launcher_preflight__run_three_chain_after_stage08_v5.py"
    three_chain_preflight_ref = seal(
        "three_chain_preflight",
        terminal(
            adapter.THREE_CHAIN_PREFLIGHT_SCHEMA, "three_chain_preflight",
            {key: True for key in adapter.TERMINAL_SPECS["three_chain_preflight"][2]},
            {"generator": three_generator,
             "launcher_authentication_terminal": launcher_refs[three_launcher_key],
             "runtime_dependency_manifest": runtime_ref, "stage08_terminal": stage08_ref},
        ),
    )
    full_launcher_key = "launcher_preflight__run_full_band_v3_after_stage08_v5.py"
    full_band_preflight_ref = seal(
        "full_band_v3_preflight",
        terminal(
            adapter.FULL_BAND_PREFLIGHT_SCHEMA, "full_band_v3_preflight",
            {key: True for key in adapter.TERMINAL_SPECS["full_band_v3_preflight"][2]},
            {"runtime_dependency_manifest": runtime_ref,
             "launcher_authentication_terminal": launcher_refs[full_launcher_key],
             "source_bindings": copy.deepcopy(full_source_bindings), "stage08_terminal": stage08_ref},
        ),
    )
    authorization_ref = seal(
        "authorization",
        {"schema": adapter.AUTHORIZATION_SCHEMA, "status": "AUTHORIZED_NOT_YET_RESUMED",
         "authorized_utc": "2026-08-22T00:00:00+00:00", "contract": contract_ref,
         "bundle_manifest": bundle_ref, "independent_review_go": go_ref,
         "runtime_dependency_manifest": runtime_ref, "runtime_dependency_root_digest": runtime_root_digest,
         "runtime_interpreter": python_ref, "isolated_flags": ["-I", "-B", "-S"],
         "launcher_authentication_terminal": launcher_refs[resume_launcher_key],
         "preflight_terminal": preflight_ref, "watcher_launch_receipt": watcher_launch_ref,
         "watcher_script": watcher_script, "watcher_process_start_identity": process_identity,
         "identity_gate": identity_gate, "same_prelaunched_watcher_only": True,
         "transport_authorized": False, "scientific_release_authorized": False},
    )
    outcome_ref = seal(
        "outcome",
        {"schema": adapter.OUTCOME_SCHEMA, "overall_status": "PASS",
         "resumed_utc": "2026-08-22T00:00:01+00:00", "watcher_pid": 7298,
         "authorization": authorization_ref, "identity_revalidated_same_snapshots": True,
         "pidfd_signal_used": True, "watcher_process_start_identity": process_identity,
         "live_proc_identity_revalidated_before_pidfd_signal": True},
    )

    three_summary = record("three_summary.json")
    three_rows = record("three_rows.csv", row_count=7298)
    three_artifacts = {
        key: minimal(record(f"three_{key}"))
        for key in sorted(adapter.MANIFEST_ARTIFACT_KEYS["three_chain_manifest"])
    }
    three_artifacts["summary"] = minimal(three_summary)
    three_artifacts["rows"] = {"path": three_rows["path"], "sha256": three_rows["sha256"]}
    three_chain_manifest_ref = seal(
        "three_chain_manifest",
        {"schema": adapter.THREE_CHAIN_MANIFEST_SCHEMA, "overall_status": "PASS",
         "artifacts": three_artifacts},
    )
    three_chain_ref = seal(
        "three_chain",
        terminal(
            adapter.THREE_CHAIN_TERMINAL_SCHEMA, "matched_survivor_three_chain",
            {key: True for key in adapter.TERMINAL_SPECS["three_chain"][2]},
            {"canonical_stage08_terminal": stage08_ref, "preflight_terminal": three_chain_preflight_ref,
             "manifest": three_chain_manifest_ref, "rows": three_rows, "summary": three_summary},
        ),
    )

    full_summary = record("full_summary.json")
    full_rows = record("full_rows.csv", row_count=7298)
    full_fixed = record("full_fixed_histograms.csv")
    full_plot = record("full_plot.png")
    full_inputs = {key: record(f"full_input_{key}.bin") for key in sorted(adapter.FULL_MANIFEST_INPUT_KEYS)}
    full_inputs["auditor_script"] = full_source_bindings["v3_generator"]
    full_inputs["method_preregistration"] = full_source_bindings["unchanged_method_preregistration"]
    full_inputs["stage06_config"] = full_source_bindings["unchanged_stage06_config"]
    full_inputs["stage08_terminal_receipt"] = record("full_stage08_terminal_receipt.json")
    full_runtime = {key: record(f"runtime_{key.replace('/', '_')}") for key in sorted(adapter.FULL_MANIFEST_RUNTIME_KEYS)}
    full_outputs = {"rows": {key: full_rows[key] for key in ("path", "sha256", "size_bytes")},
                    "fixed_histograms": full_fixed,
                    "summary": full_summary, "plot": full_plot}
    full_band_manifest_ref = seal(
        "full_band_v3_manifest",
        {"schema": adapter.FULL_BAND_MANIFEST_SCHEMA,
         "generated_utc": "2026-08-22T00:00:00+00:00", "inputs": full_inputs,
         "runtime_sources": full_runtime, "outputs": full_outputs},
    )
    full_band_internal_ref = seal(
        "full_band_v3_internal_terminal",
        {"schema": adapter.FULL_BAND_INTERNAL_SCHEMA, "overall_status": "PASS",
         "finished_utc": "2026-08-22T00:00:00+00:00", "expected_count": 7298,
         "audited_count": 7298, "structural_execution_pass_count": 7298,
         "diagnostic_flag_candidate_count_nonfiltering": 0,
         "method_preregistration": full_inputs["method_preregistration"],
         "identity_terminal_receipt": identity_ref,
         "stage06_terminal_receipt": full_inputs["stage06_terminal_receipt"],
         "stage08_terminal_receipt": full_inputs["stage08_terminal_receipt"],
         "summary": full_summary, "rows": full_rows, "fixed_histograms": full_fixed,
         "plot": full_plot, "manifest": full_band_manifest_ref,
         "candidate_inclusion_changed": False,
         "stage07_08_primary_15ghz_statistics_changed": False,
         "diagnostic_only_nonfiltering": True, "panel_schema_only_remediation": True},
    )
    full_band_ref = seal(
        "full_band_v3",
        terminal(
            adapter.FULL_BAND_TERMINAL_SCHEMA, "full_band_s4p_qa_v3",
            {key: True for key in adapter.TERMINAL_SPECS["full_band_v3"][2]},
            {"canonical_stage08_terminal": stage08_ref,
             "preflight_terminal": full_band_preflight_ref,
             "source_bindings": copy.deepcopy(full_source_bindings),
             "manifest": full_band_manifest_ref, "rows": full_rows, "summary": full_summary,
             "internal_terminal": full_band_internal_ref},
        ),
    )
    seal(
        "controller",
        terminal(
            adapter.CONTROLLER_TERMINAL_SCHEMA, "Stage07_08_controller",
            {"stage07_pass": True, "stage08_pass": True},
            {"authorization": authorization_ref, "preflight_terminal": preflight_ref,
             "resume_outcome": outcome_ref, "stage07_terminal": stage07_ref,
             "stage08_terminal": stage08_ref},
        ),
    )
    paths["readme"] = "reports/synthetic-only/stats_v2_README.md"
    raws["readme"] = b"synthetic result-blind preregistration note\n"
    sources = {key: (paths[key], raw) for key, raw in raws.items()}
    refs = {key: (paths[key], sha(raw), len(raw)) for key, raw in raws.items()}
    return sources, refs


def make_payload(paths: dict[str, tuple[str, str, int]]) -> dict[str, Any]:
    paired = paired_rows()
    recomputed_feature, recomputed_joint, recomputed_q = adapter.derive_source_science_tables(paired)
    role_to_key = {
        "canonical_controller": "controller",
        "canonical_stage07": "stage07",
        "canonical_stage08": "stage08",
        "canonical_stage07_manifest": "stage07_manifest",
        "canonical_stage08_manifest": "stage08_manifest",
        "canonical_three_chain": "three_chain",
        "canonical_three_chain_manifest": "three_chain_manifest",
        "canonical_full_band_v3": "full_band_v3",
        "canonical_full_band_v3_manifest": "full_band_v3_manifest",
        "canonical_full_band_v3_internal_terminal": "full_band_v3_internal_terminal",
        "canonical_resume_preflight_terminal": "preflight",
        "canonical_three_chain_preflight_terminal": "three_chain_preflight",
        "canonical_full_band_v3_preflight_terminal": "full_band_v3_preflight",
        **{
            f"canonical_{key}": key
            for key in adapter.LAUNCHER_PREFLIGHT_KEYS
        },
    }
    source_files = [
        {"role": role, "path": paths[key][0], "sha256": paths[key][1]}
        for role, key in sorted(role_to_key.items())
    ]
    source_by_role = {row["role"]: row for row in source_files}

    def source_record(role: str) -> dict[str, str]:
        row = source_by_role[role]
        key = role_to_key[role]
        return {"path": row["path"], "sha256": row["sha256"], "size_bytes": paths[key][2]}

    stages = [
        {"stage_order": 0, "stage": "Frozen targets", "eligible": 10000, "completed": 10000, "status": "complete", "denominator_note": "original fixed denominator"},
        {"stage_order": 1, "stage": "Analytical gate", "eligible": 10000, "completed": 7926, "status": "complete", "denominator_note": "2074 FAIL retained"},
        {"stage_order": 2, "stage": "Cadence streamout", "eligible": 7926, "completed": 7373, "status": "complete", "denominator_note": "553 FAIL retained"},
        {"stage_order": 3, "stage": "Zero-blocking Calibre", "eligible": 7373, "completed": 7298, "status": "complete", "denominator_note": "75 blocking FAIL retained"},
        {"stage_order": 4, "stage": "Fresh real EMX", "eligible": 7298, "completed": 7298, "status": "complete", "denominator_note": "survivor-conditioned numeric denominator"},
        {"stage_order": 5, "stage": "Full-band S4P QA v3", "eligible": 7298, "completed": 7298, "status": "complete", "denominator_note": "diagnostic flags are non-filtering"},
    ]
    return {
        "schema": adapter.INPUT_SCHEMA, "status": "complete",
        "run": {
            "run_id": "synthetic_result_blind_fixture", "sampling_mode": "all_gate_pass",
            "planned_emx_count": 7298, "selection_manifest_path": paths["stage08"][0],
            "selection_manifest_sha256": paths["stage08"][1], "selection_is_response_blind": True,
            "selection_strata": {"legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306},
            "selection_weights_path": None, "selection_weights_sha256": None,
            "gds_pass_count": 7298, "calibre_pass_count": 7298,
            "calibre_blocking_fail_count": 75, "calibre_nonblocking_warning_count": 7373,
            "emx_complete_count": 7298, "emx_fail_count": 0, "wall_time_seconds": None,
            "concurrency": 48, "mars_executed_statistics_copy_path": paths["stage08"][0],
            "mars_executed_statistics_copy_sha256": paths["stage08"][1],
            "survivor_conditioning_statement": adapter.PRODUCER_SURVIVOR_CONDITIONING_STATEMENT,
            "fresh_emx_reporting_label": adapter.SOURCE_REPORTING_LABEL, "terminal_status": "complete",
        },
        "fresh_emx_stage06_running_state": {
            "status": "RUNNING_NO_RESULT_AVAILABLE", "expected_candidate_count": 7298,
            "identity_audit_gate_status": "IDENTITY_AUDIT_GATE_PENDING",
            "full_7298_normalized_identity_terminal_audit_present": False,
            "stage07_result_present": False, "stage08_result_present": False,
            "numeric_fresh_emx_claim_allowed": False,
        },
        "terminal_normalized_gds_identity_audit": {
            "status": "PASS", "expected_candidate_count": 7298, "algorithm": adapter.IDENTITY_ALGORITHM,
            "terminal_match_count": 7298, "terminal_mismatch_count": 0,
            "receipt_path": paths["identity"][0], "receipt_sha256": paths["identity"][1],
            "result_publication_allowed": True,
        },
        "fresh_metric_contract": {
            "status": "frozen_before_results_stats_v2",
            "primary_error_representations": ["raw_absolute", "fixed_frame_normalized"],
            "k_fixed_frame_span": 1.0, "k_target_relative_percentage_primary_allowed": False,
            "k_target_relative_percentage_composite_gate_allowed": False,
            "q_floor_shortfall_required": True, "bins_frozen_before_results_required": True,
            "overflow_bin_required": True, "axis_limit_source": "preregistered_fixed_contract",
            "observed_p99_adaptive_axis_allowed": False,
            "statistics_v1_k_ape_p99_adaptive_primary_allowed": False,
            "statistics_v2_manifest_path": paths["prereg"][0],
            "statistics_v2_manifest_sha256": paths["prereg"][1],
            "statistics_v2_readme_path": paths["readme"][0],
            "statistics_v2_readme_sha256": paths["readme"][1],
        },
        "source_files": source_files,
        "stage_counts": stages, "comparison_feature_metrics": recomputed_feature,
        "joint_metrics": recomputed_joint, "q_metrics": recomputed_q,
        "fixed_bin_metrics": fixed_bins(paired), "paired_feature_rows": paired,
        "completion_contract": {
            "comparison_feature_metric_fields": FEATURE_FIELDS, "joint_metric_fields": JOINT_FIELDS,
            "q_metric_fields": Q_FIELDS, "fixed_bin_metric_fields": BIN_FIELDS,
            "stage_count_fields": STAGE_FIELDS,
            "paired_feature_row_fields": list(adapter.PAIRED_FEATURE_ROW_FIELDS),
        },
        "complete_emx_release_chain": {
            **{
                release_key: source_record(source_role)
                for source_role, release_key in adapter.RELEASE_RECORD_BINDINGS.items()
            },
            "launcher_preflight_terminals": {
                key: source_record(f"canonical_{key}")
                for key in sorted(adapter.LAUNCHER_PREFLIGHT_KEYS)
            },
            **adapter.RELEASE_FLAG_VALUES,
        },
    }


class CompatibilityV8Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="report_interface_v8_")
        # macOS exposes /var as a symlink to /private/var.  The v5 nofollow gate
        # correctly rejects that alias, so the positive fixture uses the physical
        # component chain while dedicated hostile tests retain the symlink alias.
        self.root = Path(os.path.realpath(self.temp.name))
        self.mirror = self.root / "mirror"
        self.mirror.mkdir()
        self.install_sources()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install_sources(
        self,
        mutators: dict[str, Mutator] | None = None,
        raw_overrides: dict[str, bytes] | None = None,
    ) -> None:
        sources, self.paths = build_control_sources(mutators, raw_overrides)
        self.raw_by_ref: dict[tuple[str, str], bytes] = {}
        self.mirror_entries: list[dict[str, Any]] = []
        for index, (key, (original, raw)) in enumerate(sources.items()):
            relative = f"files/{index:02d}_{key}.bin"
            target = self.mirror / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            digest = sha(raw)
            self.raw_by_ref[(original, digest)] = raw
            self.mirror_entries.append({"original_path": original, "sha256": digest, "mirror_relative_path": relative})
        self.payload = make_payload(self.paths)

    def read_ref(self, path: str, expected: str, label: str) -> bytes:
        try:
            return self.raw_by_ref[(path, expected)]
        except KeyError as exc:
            raise adapter.GateError(f"missing synthetic ref {label}") from exc

    def write_case(
        self,
        payload: dict[str, Any] | None = None,
        entries: list[dict[str, Any]] | None = None,
    ) -> tuple[Path, Path]:
        interface = self.root / f"input_{len(list(self.root.glob('input_*.json'))):02d}.json"
        write_json(interface, self.payload if payload is None else payload)
        manifest = self.root / f"mirror_{len(list(self.root.glob('mirror_*.json'))):02d}.json"
        write_json(
            manifest,
            {
                "schema": adapter.MIRROR_SCHEMA, "status": "PASS_LOCAL_NO_CLOBBER_MIRROR",
                "remote_generation_performed": False,
                "input_interface_sha256": sha(interface.read_bytes()),
                "files": self.mirror_entries if entries is None else entries,
            },
        )
        return interface, manifest

    def adapt(self, name: str = "bundle") -> Path:
        interface, manifest = self.write_case()
        output = self.root / name
        adapter.adapt_interface(interface, manifest, self.mirror, output)
        return output / "COMPLETE_EMX_RESULT_INTERFACE_PORTABLE_V8.json"

    def consume(self, interface: Path) -> dict[str, Any]:
        receipt = interface.parent / "ADAPTER_RECEIPT.json"
        return consumer.load_portable_interface(
            interface, sha(interface.read_bytes()), receipt, sha(receipt.read_bytes())
        )

    def rehash_portable_and_consume(
        self, interface: Path, mutate: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        receipt_path = interface.parent / "ADAPTER_RECEIPT.json"
        interface.parent.chmod(0o755)
        interface.chmod(0o644)
        receipt_path.chmod(0o644)
        payload = json.loads(interface.read_text(encoding="utf-8"))
        mutate(payload)
        write_json(interface, payload)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["output_interface_sha256"] = sha(interface.read_bytes())
        write_json(receipt_path, receipt)
        return consumer.load_portable_interface(
            interface,
            sha(interface.read_bytes()),
            receipt_path,
            sha(receipt_path.read_bytes()),
        )

    def test_01_positive_full_chain_and_consumer_pass(self) -> None:
        payload = self.consume(self.adapt())
        self.assertEqual(payload["terminal_normalized_gds_identity_audit"]["status"], "PASS_7298_OF_7298")
        self.assertTrue(payload["fresh_emx_stage06_running_state"]["stage08_result_present"])
        self.assertEqual(payload["run"]["fresh_emx_reporting_label"], adapter.REPORT_REPORTING_LABEL)
        self.assertEqual(
            (len(payload["comparison_feature_metrics"]), len(payload["joint_metrics"]),
             len(payload["q_metrics"]), len(payload["fixed_bin_metrics"]),
             len(payload["paired_feature_rows"])),
            (36, 12, 3, 30, 29192),
        )
        roles = {row["role"] for row in payload["source_files"]}
        self.assertTrue(adapter.NESTED_ARTIFACT_ROLES.issubset(roles))
        self.assertEqual(payload["run"]["survivor_scope"], adapter.SURVIVOR_SCOPE)
        self.assertEqual(
            payload["run"]["survivor_conditioning_statement"],
            adapter.SURVIVOR_CONDITIONING_STATEMENT,
        )

    def test_02_source_terminals_exact_json_schema_pass_7298_and_false_rows(self) -> None:
        hostile = [
            ("stage07", lambda value: value.__setitem__("overall_status", "FAIL")),
            ("stage08", lambda value: value["evidence"]["rows"].__setitem__("row_count", 7297)),
            ("controller", lambda value: value.__setitem__("result_rows_embedded", True)),
            ("preflight", lambda value: value.__setitem__("schema", "wrong")),
        ]
        for key, mutate in hostile:
            with self.subTest(key=key):
                self.install_sources({key: mutate})
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)
        self.install_sources(raw_overrides={"controller": b"not-json\n"})
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_03_identity_receipt_exact_result_blind_semantics(self) -> None:
        for key, value in (("overall_status", "FAIL"), ("physical_error_metrics_accessed", True)):
            with self.subTest(key=key):
                self.install_sources({"identity": lambda row, k=key, v=value: row.__setitem__(k, v)})
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_04_false_authority_and_outcome_binding(self) -> None:
        hostile = [
            ("authorization", lambda value: value.__setitem__("transport_authorized", True)),
            ("authorization", lambda value: value.__setitem__("scientific_release_authorized", True)),
            ("outcome", lambda value: value.__setitem__("pidfd_signal_used", False)),
        ]
        for key, mutate in hostile:
            with self.subTest(key=key):
                self.install_sources({key: mutate})
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_05_consumer_requires_expected_interface_and_receipt_sha(self) -> None:
        interface = self.adapt()
        receipt = interface.parent / "ADAPTER_RECEIPT.json"
        with self.assertRaises(adapter.GateError):
            consumer.load_portable_interface(interface, "0" * 64, receipt, sha(receipt.read_bytes()))
        with self.assertRaises(adapter.GateError):
            consumer.load_portable_interface(interface, sha(interface.read_bytes()), receipt, "0" * 64)

    def test_06_scientific_contract_fields_fail_closed(self) -> None:
        mutations = [
            lambda row: row["run"].__setitem__("selection_is_response_blind", False),
            lambda row: row["run"].__setitem__("selection_strata", {"legacy_k_le_0p8": 0, "extension_k_gt_0p8": 7298}),
            lambda row: row["fresh_metric_contract"].__setitem__("k_fixed_frame_span", 0.8),
            lambda row: row["fresh_metric_contract"].__setitem__("q_floor_shortfall_required", False),
            lambda row: row["comparison_feature_metrics"][0].__setitem__("metric_contract_version", "stats_v1"),
            lambda row: row["complete_emx_release_chain"].__setitem__("all_target_candidate_panel_geometry_gds_touchstone_rows_bound", False),
            lambda row: row["complete_emx_release_chain"].__setitem__("stage08_three_target_proxy_emx_values_bound_rowwise", False),
            lambda row: row["complete_emx_release_chain"].__setitem__("rmse_addition_allowed", True),
            lambda row: row["complete_emx_release_chain"].__setitem__("full_band_diagnostic_flags_filter_candidates", True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                payload = copy.deepcopy(self.payload)
                mutate(payload)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_producer_interface(payload)

    def test_07_table_cardinalities_and_panels_are_hard_gates(self) -> None:
        mutations = [
            lambda row: row["comparison_feature_metrics"].pop(),
            lambda row: row["joint_metrics"].pop(),
            lambda row: row["q_metrics"].pop(),
            lambda row: row["paired_feature_rows"].pop(),
            lambda row: row["comparison_feature_metrics"][0].__setitem__("panel_key", "extension_k_gt_0p8"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                payload = copy.deepcopy(self.payload)
                mutate(payload)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_producer_interface(payload)

    def test_08_fixed_bin_label_scope_and_count_are_exact(self) -> None:
        mutations = [
            lambda row: row["fixed_bin_metrics"][0].__setitem__("bin_label", "0-2.5%"),
            lambda row: row["fixed_bin_metrics"][0].__setitem__("comparison_scope", "proxy_vs_emx"),
            lambda row: row["fixed_bin_metrics"][0].__setitem__("comparison_scope_label", "Proxy vs EMX"),
            lambda row: row["fixed_bin_metrics"][0].__setitem__("count", row["fixed_bin_metrics"][0]["count"] + 1),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                payload = copy.deepcopy(self.payload)
                mutate(payload)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_producer_interface(payload)

    def test_09_internal_bundle_source_ancestor_symlink_is_rejected(self) -> None:
        interface = self.adapt()
        bundle = interface.parent
        bundle.chmod(0o755)
        source_dir = bundle / "portable_sources"
        source_dir.chmod(0o755)
        real_dir = bundle / "portable_sources_real"
        os.rename(source_dir, real_dir)
        source_dir.symlink_to(real_dir.name, target_is_directory=True)
        with self.assertRaises(adapter.GateError):
            self.consume(interface)

    def test_10_internal_mirror_ancestor_symlink_is_rejected(self) -> None:
        real_dir = self.mirror / "real_files"
        os.rename(self.mirror / "files", real_dir)
        (self.mirror / "files").symlink_to(real_dir.name, target_is_directory=True)
        interface, manifest = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(interface, manifest, self.mirror, self.root / "bad_mirror_symlink")

    def test_11_missing_or_tampered_source_closure_is_rejected(self) -> None:
        entries = [row for row in self.mirror_entries if row["original_path"] != self.paths["identity"][0]]
        interface, manifest = self.write_case(entries=entries)
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(interface, manifest, self.mirror, self.root / "missing_source")
        portable = self.adapt(name="tampered_bundle")
        output = json.loads(portable.read_text(encoding="utf-8"))
        victim = portable.parent / output["source_files"][0]["path"]
        victim.chmod(0o644)
        victim.write_bytes(victim.read_bytes() + b"tamper")
        with self.assertRaises(adapter.GateError):
            self.consume(portable)

    def test_12_no_clobber_output_is_rejected(self) -> None:
        interface, manifest = self.write_case()
        output = self.root / "no_clobber"
        adapter.adapt_interface(interface, manifest, self.mirror, output)
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(interface, manifest, self.mirror, output)

    def test_13_contract_has_no_execution_result_or_go_authority(self) -> None:
        package_root = Path(__file__).resolve().parent
        contract = json.loads((package_root / "REPORT_INTERFACE_COMPATIBILITY_CONTRACT_V8.json").read_text(encoding="utf-8"))
        boundaries = contract["execution_boundaries"]
        expected_boundary_keys = {
            "mars_login_authorized", "mars_result_read_authorized",
            "actual_complete_interface_or_result_read_authorized",
            "production_chain_execution_authorized", "watcher_resume_or_signal_authorized",
            "stage07_or_stage08_execution_authorized",
            "existing_v8_or_v4_package_mutation_authorized",
            "current_engineering_memory_mutation_authorized",
            "prepared_package_is_independent_go", "actual_complete_interface_adaptation_performed",
        }
        self.assertEqual(set(boundaries), expected_boundary_keys)
        for key in expected_boundary_keys:
            self.assertIs(boundaries[key], False)
        readme_title = (package_root / "README_CN.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(
            readme_title,
            "# 周一报告 complete-EMX 接口兼容层 v8 AWAITING_FRESH_INDEPENDENT_QA（结果盲）",
        )
        for source_name in ("adapt_complete_emx_interface_v8.py", "consume_portable_emx_interface_v8.py"):
            source = (package_root / source_name).read_text(encoding="utf-8")
            for forbidden in ("import subprocess", "import socket", "import paramiko", "os.kill(", "SIGCONT", "${MARS_HOST_PREFIX}"):
                self.assertNotIn(forbidden, source)

    def test_14_formal_v5_no_go_audit_is_fully_hash_bound_without_modification(self) -> None:
        package_root = Path(__file__).resolve().parent
        binding = json.loads((package_root / "FORMAL_V5_NO_GO_BINDING.json").read_text(encoding="utf-8"))
        result = binding["independent_result"]
        self.assertEqual(result["finding_counts"], {"P0": 0, "P1": 3, "P2": 0, "P3": 0})
        self.assertEqual(result["gate_counts"], {"total": 120, "pass": 112, "fail": 8})
        self.assertEqual(len(binding["formal_v5_finding_set_exact"]), 3)
        self.assertEqual(len(result["failed_gate_ids"]), 8)
        audit_root = PHYSICAL_EVIDENCE_ROOT / "independent_report_interface_compatibility_v5_audit_20260822T212307Z"
        self.assertEqual(len(binding["audit_artifacts_sha256"]), 11)
        for name, expected in binding["audit_artifacts_sha256"].items():
            self.assertEqual(sha(adapter._read_regular_bytes(audit_root / name)), expected)
        candidate_root = PHYSICAL_EVIDENCE_ROOT / "report_interface_compatibility_v5_prepared_20260822T210920Z"
        for name, expected in binding["candidate"]["indexed_files"].items():
            self.assertEqual(sha(adapter._read_regular_bytes(candidate_root / name)), expected)
        self.assertIs(binding["boundaries"]["candidate_modified"], False)
        self.assertIs(binding["boundaries"]["actual_complete_interface_or_results_read"], False)

    def test_15_strict_json_rejects_nonfinite_constants_and_duplicate_keys(self) -> None:
        hostile = (
            b'{"value":NaN}\n',
            b'{"value":Infinity}\n',
            b'{"value":-Infinity}\n',
            b'{"same":1,"same":2}\n',
        )
        for raw in hostile:
            with self.subTest(raw=raw):
                with self.assertRaises(adapter.GateError):
                    adapter.strict_json_bytes(raw, "synthetic hostile JSON")
        with self.assertRaises(adapter.GateError):
            adapter.canonical_json_bytes({"value": float("nan")})

    def test_16_complete_release_chain_roles_records_and_cross_bindings_are_exact(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["source_files"].pop()
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(payload)

        payload = copy.deepcopy(self.payload)
        payload["source_files"][0]["unexpected"] = True
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(payload)

        payload = copy.deepcopy(self.payload)
        payload["complete_emx_release_chain"].pop("three_chain_terminal")
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(payload)

        payload = copy.deepcopy(self.payload)
        payload["complete_emx_release_chain"]["full_band_v3_manifest"] = {
            "path": "/synthetic/wrong.json", "sha256": "0" * 64
        }
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(payload)

    def test_17_three_full_manifests_internal_and_launcher_semantics_are_parsed(self) -> None:
        hostile = [
            ("three_chain", lambda value: value.__setitem__("overall_status", "FAIL")),
            ("full_band_v3", lambda value: value["checks"].__setitem__("diagnostic_flags_nonfiltering", False)),
            ("three_chain_manifest", lambda value: value.__setitem__("overall_status", "FAIL")),
            ("full_band_v3_manifest", lambda value: value.__setitem__("schema", "wrong")),
            ("full_band_v3_internal_terminal", lambda value: value.__setitem__("audited_count", 7297)),
            (
                "launcher_preflight__run_three_chain_after_stage08_v5.py",
                lambda value: value["checks"].__setitem__("interpreter_identity_pass", False),
            ),
        ]
        for key, mutate in hostile:
            with self.subTest(key=key):
                self.install_sources({key: mutate})
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_18_portable_release_and_source_records_remain_fail_closed_if_caller_rebinds_sha(self) -> None:
        for case in ("release_record", "source_extra_key"):
            with self.subTest(case=case):
                interface = self.adapt(name=f"portable_tamper_{case}")
                bundle = interface.parent
                receipt_path = bundle / "ADAPTER_RECEIPT.json"
                bundle.chmod(0o755)
                interface.chmod(0o644)
                receipt_path.chmod(0o644)
                payload = json.loads(interface.read_text(encoding="utf-8"))
                if case == "release_record":
                    payload["complete_emx_release_chain"]["three_chain_terminal"] = {
                        "path": payload["complete_emx_release_chain"]["stage07_terminal"]["path"],
                        "sha256": payload["complete_emx_release_chain"]["stage07_terminal"]["sha256"],
                    }
                else:
                    payload["source_files"][0]["unexpected"] = True
                write_json(interface, payload)
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt["output_interface_sha256"] = sha(interface.read_bytes())
                write_json(receipt_path, receipt)
                with self.assertRaises(adapter.GateError):
                    consumer.load_portable_interface(
                        interface,
                        sha(interface.read_bytes()),
                        receipt_path,
                        sha(receipt_path.read_bytes()),
                    )

    def test_19_preflight_and_launcher_evidence_are_exact_and_parent_bound(self) -> None:
        cases: list[tuple[str, dict[str, Mutator]]] = [
            ("resume_empty", {"preflight": lambda value: value.__setitem__("evidence", {})}),
            ("three_empty", {"three_chain_preflight": lambda value: value.__setitem__("evidence", {})}),
            ("full_empty", {"full_band_v3_preflight": lambda value: value.__setitem__("evidence", {})}),
        ]
        for launcher_key in sorted(adapter.LAUNCHER_PREFLIGHT_KEYS):
            cases.append(
                (launcher_key, {launcher_key: lambda value: value.__setitem__("evidence", {})})
            )

        def wrong_stage08(value: dict[str, Any]) -> None:
            value["evidence"]["stage08_terminal"]["path"] += ".wrong"

        def extra_resume_source(value: dict[str, Any]) -> None:
            source = copy.deepcopy(value["evidence"]["sources"]["stage07_generator"])
            value["evidence"]["sources"]["undeclared"] = source

        cases.extend(
            [
                ("three_parent", {"three_chain_preflight": wrong_stage08}),
                ("full_parent", {"full_band_v3_preflight": wrong_stage08}),
                ("resume_source_extra", {"preflight": extra_resume_source}),
            ]
        )
        for name, mutators in cases:
            with self.subTest(name=name):
                self.install_sources(mutators)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_20_identity_terminal_manifest_and_internal_records_are_cross_bound(self) -> None:
        def drift_path(container: dict[str, Any], *keys: str) -> None:
            current: dict[str, Any] = container
            for key in keys[:-1]:
                current = current[key]
            current[keys[-1]]["path"] += ".different"

        cases: list[tuple[str, dict[str, Mutator]]] = [
            ("identity", {"stage08": lambda value: drift_path(value, "evidence", "identity_gate", "summary")}),
            ("stage07", {"stage07_manifest": lambda value: drift_path(value, "artifacts", "historical_200k_fresh_emx_statistics_summary.json")}),
            ("stage08", {"stage08_manifest": lambda value: drift_path(value, "artifacts", "summary")}),
            ("three", {"three_chain_manifest": lambda value: drift_path(value, "artifacts", "summary")}),
            # Mutate the terminal after the manifest has already been serialized.
            # Mutating the manifest-side dict earlier would also mutate the shared
            # fixture record object before terminal serialization and would not
            # create an actual cross-document mismatch.
            ("full", {"full_band_v3": lambda value: drift_path(value, "evidence", "summary")}),
            ("internal", {"full_band_v3_internal_terminal": lambda value: drift_path(value, "summary")}),
        ]

        def extra_full_input(value: dict[str, Any]) -> None:
            value["inputs"]["undeclared"] = copy.deepcopy(value["inputs"]["identity_receipt"])

        def extra_full_runtime(value: dict[str, Any]) -> None:
            first = next(iter(value["runtime_sources"].values()))
            value["runtime_sources"]["undeclared.py"] = copy.deepcopy(first)

        cases.extend(
            [
                ("full_input_extra", {"full_band_v3_manifest": extra_full_input}),
                ("full_runtime_extra", {"full_band_v3_manifest": extra_full_runtime}),
            ]
        )
        for name, mutators in cases:
            with self.subTest(name=name):
                self.install_sources(mutators)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_21_frozen_v8_authorization_process_go_and_runtime_semantics(self) -> None:
        resume_launcher = "launcher_preflight__resume_exact_watcher_stage07_08_v5.py"

        def wrong_launcher(value: dict[str, Any]) -> None:
            value["launcher_authentication_terminal"]["path"] += ".unrelated"

        def process_change(key: str, changed: Any) -> Mutator:
            return lambda value: value["watcher_process_start_identity"].__setitem__(key, changed)

        cases: list[tuple[str, dict[str, Mutator]]] = [
            ("launcher", {"authorization": wrong_launcher}),
            ("algorithm", {"authorization": process_change("algorithm", "wrong")}),
            ("delta", {"authorization": process_change("launch_receipt_after_proc_start_ns", 120_000_000_001)}),
            ("argv", {"authorization": process_change("argv", ["/tmp/not-bash", "/tmp/not-watcher"])}),
            ("cmdline", {"authorization": process_change("cmdline_sha256", "f" * 64)}),
            ("go_contract", {"independent_go": lambda value: value.__setitem__("contract_sha256", "f" * 64)}),
            ("go_runtime", {"independent_go": lambda value: value.__setitem__("runtime_dependency_root_digest", "f" * 64)}),
            ("runtime_root", {"runtime_manifest": lambda value: value.__setitem__("root_digest", "f" * 64)}),
            ("contract_algorithm", {"contract": lambda value: value["active_watcher"].__setitem__("process_identity_algorithm", "wrong")}),
            ("launch_schema", {"watcher_launch": lambda value: value.__setitem__("schema", "wrong")}),
            ("launch_delta", {"watcher_launch": lambda value: value.__setitem__("started_utc", "1970-01-01T00:00:03+00:00")}),
            ("launch_source", {"watcher_launch": lambda value: value.__setitem__("physical_statistics_script_sha256", "f" * 64)}),
            ("launch_script", {"watcher_launch": lambda value: value["watcher_script"].__setitem__("path", "/synthetic/wrong.sh")}),
            ("launch_no_clobber", {"watcher_launch": lambda value: value.__setitem__("no_clobber", False)}),
        ]
        for name, mutators in cases:
            with self.subTest(name=name):
                self.install_sources(mutators)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

        self.install_sources()
        authorization = adapter.strict_json_bytes(
            self.raw_by_ref[(self.paths["authorization"][0], self.paths["authorization"][1])],
            "authorization boundary fixture",
        )
        contract = adapter.strict_json_bytes(
            self.raw_by_ref[(self.paths["contract"][0], self.paths["contract"][1])],
            "contract boundary fixture",
        )
        for boundary in (0, 120_000_000_000):
            with self.subTest(delta_boundary=boundary):
                process = copy.deepcopy(authorization["watcher_process_start_identity"])
                process["launch_receipt_after_proc_start_ns"] = boundary
                validated = adapter.validate_process_identity(process, authorization, contract)
                self.assertEqual(validated["launch_receipt_after_proc_start_ns"], boundary)
        self.assertIn(resume_launcher, adapter.LAUNCHER_PREFLIGHT_KEYS)

    def test_22_scientific_tables_panel_and_funnel_are_recomputed_exactly(self) -> None:
        mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            ("feature_value", lambda row: row["comparison_feature_metrics"][0].__setitem__("mae_physical", 999.0)),
            ("feature_type", lambda row: row["comparison_feature_metrics"][0].__setitem__("rmse_physical", "not-a-number")),
            ("joint", lambda row: row["joint_metrics"][0].__setitem__("joint_rmse_fraction", 999.0)),
            ("q", lambda row: row["q_metrics"][0].__setitem__("q_target_met_fraction", 2.0)),
            ("funnel", lambda row: row["stage_counts"][0].update({"status": "FAIL", "stage": "fabricated"})),
            ("bool_number", lambda row: row["paired_feature_rows"][0].__setitem__("target_value", True)),
            ("nonfinite", lambda row: row["paired_feature_rows"][0].__setitem__("target_value", float("nan"))),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                hostile = copy.deepcopy(self.payload)
                mutate(hostile)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_producer_interface(hostile)

        swapped = copy.deepcopy(self.payload)
        extension_start = 5992 * len(adapter.FEATURES)
        for index in range(len(adapter.FEATURES)):
            swapped["paired_feature_rows"][index]["panel_key"] = "extension_k_gt_0p8"
            swapped["paired_feature_rows"][index]["panel"] = "Extension |K|>0.8"
            swapped["paired_feature_rows"][extension_start + index]["panel_key"] = "legacy_k_le_0p8"
            swapped["paired_feature_rows"][extension_start + index]["panel"] = "Legacy |K|≤0.8"
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(swapped)

        combined = copy.deepcopy(swapped)
        combined["comparison_feature_metrics"][0]["mae_physical"] = 999.0
        combined["joint_metrics"][0]["joint_rmse_fraction"] = 999.0
        combined["q_metrics"][0]["q_target_met_fraction"] = 2.0
        combined["stage_counts"][0]["status"] = "FAIL"
        interface, manifest = self.write_case(combined)
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(interface, manifest, self.mirror, self.root / "combined_fail")

        exact_boundary = copy.deepcopy(self.payload)
        k_row = next(
            row for row in exact_boundary["paired_feature_rows"]
            if row["panel_key"] == "legacy_k_le_0p8" and row["feature_key"] == "k_abs"
        )
        k_row["target_value"] = 0.8
        k_row["target_normalized_fraction"] = 0.8
        feature_rows, joint_rows, q_rows = adapter.derive_source_science_tables(
            exact_boundary["paired_feature_rows"]
        )
        exact_boundary["comparison_feature_metrics"] = feature_rows
        exact_boundary["joint_metrics"] = joint_rows
        exact_boundary["q_metrics"] = q_rows
        exact_boundary["fixed_bin_metrics"] = fixed_bins(exact_boundary["paired_feature_rows"])
        adapter.validate_producer_interface(exact_boundary)

    def test_23_root_ancestors_and_portable_semantic_keysets_are_fail_closed(self) -> None:
        real_parent = self.root / "real_parent"
        real_root = real_parent / "root"
        real_root.mkdir(parents=True)
        (real_root / "item.bin").write_bytes(b"ancestor probe\n")
        alias_parent = self.root / "alias_parent"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(adapter.GateError):
            adapter._read_relative_regular_bytes(alias_parent / "root", "item.bin", "ancestor")

        def rehash_after(interface: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
            receipt_path = interface.parent / "ADAPTER_RECEIPT.json"
            interface.parent.chmod(0o755)
            interface.chmod(0o644)
            receipt_path.chmod(0o644)
            portable = json.loads(interface.read_text(encoding="utf-8"))
            mutate(portable)
            write_json(interface, portable)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["output_interface_sha256"] = sha(interface.read_bytes())
            write_json(receipt_path, receipt)
            with self.assertRaises(adapter.GateError):
                consumer.load_portable_interface(
                    interface, sha(interface.read_bytes()), receipt_path, sha(receipt_path.read_bytes())
                )

        rehash_after(self.adapt("top_extra"), lambda row: row.__setitem__("unconditional_accuracy_claim", True))
        rehash_after(self.adapt("mapping_extra"), lambda row: row["compatibility_adapter"].__setitem__("unconditional_accuracy_claim", True))
        rehash_after(self.adapt("top_missing"), lambda row: row.pop("stage_counts"))
        rehash_after(self.adapt("mapping_missing"), lambda row: row["compatibility_adapter"].pop("input_schema"))

        interface = self.adapt("bundle_ancestor")
        alias_workspace = self.root / "bundle_alias_workspace"
        alias_workspace.symlink_to(self.root, target_is_directory=True)
        aliased_interface = alias_workspace / interface.parent.name / interface.name
        aliased_receipt = aliased_interface.parent / "ADAPTER_RECEIPT.json"
        with self.assertRaises(adapter.GateError):
            consumer.load_portable_interface(
                aliased_interface, sha(interface.read_bytes()), aliased_receipt,
                sha((interface.parent / "ADAPTER_RECEIPT.json").read_bytes()),
            )

    def test_24_production_record_shapes_and_full_manifest_roles_are_exact(self) -> None:
        def remove_size(value: dict[str, Any]) -> None:
            value["evidence"]["bundle_manifest"].pop("size_bytes")

        def alter_size(value: dict[str, Any]) -> None:
            value["evidence"]["bundle_manifest"]["size_bytes"] += 1

        def missing_resume_source(value: dict[str, Any]) -> None:
            value["evidence"]["sources"].pop("stage07_generator")

        def missing_full_input(value: dict[str, Any]) -> None:
            value["inputs"].pop("identity_receipt")

        def missing_full_runtime(value: dict[str, Any]) -> None:
            value["runtime_sources"].pop(next(iter(value["runtime_sources"])))

        def wrong_controller(value: dict[str, Any]) -> None:
            value["evidence"]["controller"]["path"] = "/synthetic/wrong_controller.py"

        first_launcher = sorted(adapter.LAUNCHER_PREFLIGHT_KEYS)[0]
        cases = [
            ("size_missing", {"preflight": remove_size}),
            ("size_tamper", {"preflight": alter_size}),
            ("resume_missing", {"preflight": missing_resume_source}),
            ("full_input_missing", {"full_band_v3_manifest": missing_full_input}),
            ("full_runtime_missing", {"full_band_v3_manifest": missing_full_runtime}),
            ("controller_basename", {first_launcher: wrong_controller}),
        ]
        for name, mutators in cases:
            with self.subTest(name=name):
                self.install_sources(mutators)
                with self.assertRaises(adapter.GateError):
                    adapter.validate_bound_source_documents(self.payload, self.read_ref)

    def test_25_formal_v5_contract_and_run_authority_failures_are_closed(self) -> None:
        reference = adapter.frozen_release_contract_reference()
        self.assertEqual(len(reference), 24)
        self.assertIs(adapter.validate_release_contract_payload(reference), reference)

        missing_execution_units = copy.deepcopy(reference)
        missing_execution_units.pop("execution_units")
        with self.assertRaises(adapter.GateError):
            adapter.validate_release_contract_payload(missing_execution_units)

        extra_authority = copy.deepcopy(reference)
        extra_authority["result_access_authorized"] = True
        extra_authority["execution_units"]["shell_launcher_authorized"] = True
        with self.assertRaises(adapter.GateError):
            adapter.validate_release_contract_payload(extra_authority)

        nested_extra = copy.deepcopy(reference)
        nested_extra["execution_units"]["result_access_authorized"] = True
        with self.assertRaises(adapter.GateError):
            adapter.validate_release_contract_payload(nested_extra)

        authority_type_confusion = copy.deepcopy(reference)
        authority_type_confusion["execution_authorized"] = 0
        with self.assertRaises(adapter.GateError):
            adapter.validate_release_contract_payload(authority_type_confusion)

        self.install_sources(
            {"contract": lambda value: value.__setitem__("result_access_authorized", True)}
        )
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path, manifest_path, self.mirror, self.root / "authority_contract_e2e"
            )

        self.install_sources()
        run_authority = copy.deepcopy(self.payload)
        run_authority["run"]["execution_authorized"] = True
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(run_authority)
        self.payload = run_authority
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path, manifest_path, self.mirror, self.root / "run_authority_e2e"
            )

        self.install_sources()
        run_missing = copy.deepcopy(self.payload)
        run_missing["run"].pop("concurrency")
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(run_missing)

        portable = self.adapt("portable_run_authority")
        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(
                portable,
                lambda value: value["run"].__setitem__("execution_authorized", True),
            )

    def test_26_formal_v5_public_size_failures_are_closed(self) -> None:
        fake_size = 999999

        def mutate_bundle_contract(value: dict[str, Any]) -> None:
            next(
                row
                for row in value["files"]
                if row["relative_path"] == "RELEASE_CHAIN_V5_CONTRACT.json"
            )["size_bytes"] = fake_size

        def mutate_launcher_contract(value: dict[str, Any]) -> None:
            value["evidence"]["contract"]["size_bytes"] = fake_size

        global_size_mutators: dict[str, Mutator] = {
            "bundle_manifest": mutate_bundle_contract,
            "preflight": lambda value: value["evidence"]["contract"].__setitem__(
                "size_bytes", fake_size
            ),
            "authorization": lambda value: value["contract"].__setitem__(
                "size_bytes", fake_size
            ),
        }
        for launcher_key in adapter.LAUNCHER_PREFLIGHT_KEYS:
            global_size_mutators[launcher_key] = mutate_launcher_contract
        self.install_sources(global_size_mutators)
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path, manifest_path, self.mirror, self.root / "global_size_e2e"
            )

        def mutate_bundle_shared(value: dict[str, Any], suffix: str) -> None:
            for row in value["files"]:
                if row["relative_path"].endswith(suffix):
                    row["size_bytes"] = fake_size

        common_mutators: dict[str, Mutator] = {
            "bundle_manifest": lambda value: mutate_bundle_shared(
                value, "release_chain_common_v5.py"
            )
        }
        for launcher_key in adapter.LAUNCHER_PREFLIGHT_KEYS:
            common_mutators[launcher_key] = (
                lambda value: value["evidence"]["common"].__setitem__("size_bytes", fake_size)
            )
        self.install_sources(common_mutators)
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)

        controller_mutators: dict[str, Mutator] = {
            "bundle_manifest": lambda value: [
                row.__setitem__("size_bytes", fake_size)
                for row in value["files"]
                if row["relative_path"].endswith(".py")
                and row["relative_path"] != "release_chain_common_v5.py"
            ]
        }
        for launcher_key in adapter.LAUNCHER_PREFLIGHT_KEYS:
            controller_mutators[launcher_key] = (
                lambda value: value["evidence"]["controller"].__setitem__(
                    "size_bytes", fake_size
                )
            )
        self.install_sources(controller_mutators)
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)

        self.install_sources()
        portable = self.adapt("portable_false_size_consumer")
        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(
                portable,
                lambda value: value["complete_emx_release_chain"]["stage07_terminal"].__setitem__(
                    "size_bytes", fake_size
                ),
            )

    def test_27_formal_v5_count_type_failures_are_closed(self) -> None:
        typed_funnel = copy.deepcopy(self.payload)
        typed_funnel["stage_counts"][0]["stage_order"] = False
        typed_funnel["stage_counts"][0]["eligible"] = 10000.0
        typed_funnel["stage_counts"][0]["completed"] = 10000.0
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(typed_funnel)
        self.payload = typed_funnel
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path, manifest_path, self.mirror, self.root / "typed_funnel_e2e"
            )

        self.install_sources()
        for label, mutate in (
            ("run_bool", lambda value: value["run"].__setitem__("planned_emx_count", True)),
            ("run_float", lambda value: value["run"].__setitem__("concurrency", 48.0)),
            (
                "identity_bool",
                lambda value: value["terminal_normalized_gds_identity_audit"].__setitem__(
                    "expected_candidate_count", True
                ),
            ),
            (
                "table_float",
                lambda value: value["comparison_feature_metrics"][0].__setitem__(
                    "row_count", 7298.0
                ),
            ),
        ):
            hostile = copy.deepcopy(self.payload)
            mutate(hostile)
            with self.subTest(label=label), self.assertRaises(adapter.GateError):
                adapter.validate_producer_interface(hostile)

        self.install_sources(
            {"contract": lambda value: value.__setitem__("expected_count", 7298.0)}
        )
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)

        self.install_sources(
            {
                "stage08": lambda value: value["evidence"]["rows"].__setitem__(
                    "row_count", 7298.0
                )
            }
        )
        with self.assertRaises(adapter.GateError):
            adapter.validate_bound_source_documents(self.payload, self.read_ref)

        self.install_sources()
        portable_funnel = self.adapt("portable_typed_funnel_consumer")
        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(
                portable_funnel,
                lambda value: value["stage_counts"][0].update(
                    {"stage_order": False, "eligible": 10000.0, "completed": 10000.0}
                ),
            )
        portable_run = self.adapt("portable_run_float_consumer")
        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(
                portable_run,
                lambda value: value["run"].__setitem__("planned_emx_count", 7298.0),
            )

    def test_28_combined_authority_size_and_count_attack_is_closed(self) -> None:
        fake_size = 999999

        def contract_attack(value: dict[str, Any]) -> None:
            value["result_access_authorized"] = True
            value["execution_units"]["shell_launcher_authorized"] = True

        def bundle_attack(value: dict[str, Any]) -> None:
            next(
                row
                for row in value["files"]
                if row["relative_path"] == "RELEASE_CHAIN_V5_CONTRACT.json"
            )["size_bytes"] = fake_size

        def launcher_attack(value: dict[str, Any]) -> None:
            value["evidence"]["contract"]["size_bytes"] = fake_size

        mutators: dict[str, Mutator] = {
            "contract": contract_attack,
            "bundle_manifest": bundle_attack,
            "preflight": lambda value: value["evidence"]["contract"].__setitem__(
                "size_bytes", fake_size
            ),
            "authorization": lambda value: value["contract"].__setitem__(
                "size_bytes", fake_size
            ),
        }
        for launcher_key in adapter.LAUNCHER_PREFLIGHT_KEYS:
            mutators[launcher_key] = launcher_attack
        self.install_sources(mutators)
        self.payload["run"]["execution_authorized"] = True
        self.payload["stage_counts"][0].update(
            {"stage_order": False, "eligible": 10000.0, "completed": 10000.0}
        )
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(self.payload)
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path,
                manifest_path,
                self.mirror,
                self.root / "combined_authority_size_count_e2e",
            )

    def test_29_formal_v6_nested_exact_schema_and_authority_injection_are_closed(self) -> None:
        authority_payload = copy.deepcopy(self.payload)
        authority_payload["terminal_normalized_gds_identity_audit"][
            "result_access_authorized"
        ] = True
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(authority_payload)
        self.payload = authority_payload
        interface_path, manifest_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                interface_path,
                manifest_path,
                self.mirror,
                self.root / "formal_v6_nested_authority_e2e",
            )

        self.install_sources()
        nested_extra_cases = (
            (
                "run",
                lambda value: value["run"].__setitem__("undeclared_schema_extension", False),
            ),
            (
                "identity",
                lambda value: value["terminal_normalized_gds_identity_audit"].__setitem__(
                    "undeclared_schema_extension", False
                ),
            ),
            (
                "stage06",
                lambda value: value["fresh_emx_stage06_running_state"].__setitem__(
                    "undeclared_schema_extension", False
                ),
            ),
            (
                "metric",
                lambda value: value["fresh_metric_contract"].__setitem__(
                    "undeclared_schema_extension", False
                ),
            ),
            (
                "completion",
                lambda value: value["completion_contract"].__setitem__(
                    "undeclared_schema_extension", []
                ),
            ),
        )
        for label, mutate in nested_extra_cases:
            hostile = copy.deepcopy(self.payload)
            mutate(hostile)
            with self.subTest(label=label), self.assertRaises(adapter.GateError):
                adapter.validate_producer_interface(hostile)

        portable = self.adapt("formal_v6_portable_nested_authority")
        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(
                portable,
                lambda value: value["terminal_normalized_gds_identity_audit"].__setitem__(
                    "result_access_authorized", True
                ),
            )

    def test_30_formal_v6_consumer_primitive_aliases_are_closed(self) -> None:
        interface = self.adapt("formal_v6_primitive_aliases")
        receipt_path = interface.parent / "ADAPTER_RECEIPT.json"
        interface.parent.chmod(0o755)
        interface.chmod(0o644)
        receipt_path.chmod(0o644)
        baseline_interface = json.loads(interface.read_text(encoding="utf-8"))
        baseline_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            (
                "bool_as_int",
                lambda value: value["fixed_bin_metrics"][0].__setitem__("is_overflow", 0),
            ),
            (
                "continuous_as_bool",
                lambda value: value["fixed_bin_metrics"][0].__setitem__(
                    "lower_fraction", False
                ),
            ),
            (
                "mapping_false_as_int",
                lambda value: value["compatibility_adapter"].__setitem__(
                    "numeric_value_or_denominator_mutation_allowed", 0
                ),
            ),
            (
                "release_false_as_int",
                lambda value: value["complete_emx_release_chain"].__setitem__(
                    "rmse_addition_allowed", 0
                ),
            ),
        )
        for label, mutate in mutations:
            hostile = copy.deepcopy(baseline_interface)
            mutate(hostile)
            write_json(interface, hostile)
            receipt = copy.deepcopy(baseline_receipt)
            receipt["output_interface_sha256"] = sha(interface.read_bytes())
            write_json(receipt_path, receipt)
            with self.subTest(label=label), self.assertRaises(adapter.GateError):
                consumer.load_portable_interface(
                    interface,
                    sha(interface.read_bytes()),
                    receipt_path,
                    sha(receipt_path.read_bytes()),
                )

    def test_31_formal_v6_hash_parse_and_held_source_continuity_are_closed(self) -> None:
        interface = self.adapt("formal_v6_consumer_same_bytes")
        receipt_path = interface.parent / "ADAPTER_RECEIPT.json"
        interface.parent.chmod(0o755)
        interface.chmod(0o644)
        original_raw = interface.read_bytes()
        alternate = json.loads(original_raw.decode("utf-8"))
        alternate["run"]["run_id"] = "synthetic_second_unhashed_payload"
        alternate_raw = adapter.canonical_json_bytes(alternate)
        expected_interface_sha = sha(original_raw)
        expected_receipt_sha = sha(receipt_path.read_bytes())
        original_loader = consumer._load_adapter_receipt
        replacement_done = False

        def replace_after_first_held_read(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
            nonlocal replacement_done
            receipt = original_loader(*args, **kwargs)
            if not replacement_done:
                parked = interface.with_name("INTERFACE_FIRST_HELD_BYTES.json")
                os.rename(interface, parked)
                interface.write_bytes(alternate_raw)
                replacement_done = True
            return receipt

        consumer._load_adapter_receipt = replace_after_first_held_read
        try:
            with self.assertRaises(adapter.GateError):
                consumer.load_portable_interface(
                    interface,
                    expected_interface_sha,
                    receipt_path,
                    expected_receipt_sha,
                )
        finally:
            consumer._load_adapter_receipt = original_loader
        self.assertTrue(replacement_done)

        self.install_sources()
        original_validator = adapter.validate_bound_source_documents
        source_replaced = False
        victim_relative = self.mirror_entries[0]["mirror_relative_path"]
        victim = self.mirror / victim_relative
        victim_raw = victim.read_bytes()

        def replace_source_after_validation(
            payload: Mapping[str, Any], read_ref: Any,
        ) -> list[tuple[str, str, str]]:
            nonlocal source_replaced
            discovered = original_validator(payload, read_ref)
            parked = victim.with_name(victim.name + ".first-held")
            os.rename(victim, parked)
            victim.write_bytes(victim_raw)
            source_replaced = True
            return discovered

        adapter.validate_bound_source_documents = replace_source_after_validation
        try:
            producer_path, mirror_path = self.write_case()
            with self.assertRaises(adapter.GateError):
                adapter.adapt_interface(
                    producer_path,
                    mirror_path,
                    self.mirror,
                    self.root / "formal_v6_adapter_source_continuity",
                )
        finally:
            adapter.validate_bound_source_documents = original_validator
        self.assertTrue(source_replaced)

    def test_32_formal_v6_output_path_and_single_link_gates_are_closed(self) -> None:
        producer_path, mirror_path = self.write_case()
        real_parent = self.root / "real_output_parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked_output_parent"
        linked_parent.symlink_to(real_parent.name, target_is_directory=True)
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                producer_path,
                mirror_path,
                self.mirror,
                linked_parent / "redirected_bundle",
            )
        self.assertFalse((real_parent / "redirected_bundle").exists())

        stage07_path, stage07_sha, _ = self.paths["stage07_manifest"]
        stage07_manifest = json.loads(self.raw_by_ref[(stage07_path, stage07_sha)])
        nested_record = stage07_manifest["artifacts"]["end_to_end_funnel_counts.png"]
        nested_entry = next(
            row
            for row in self.mirror_entries
            if row["original_path"] == nested_record["path"]
            and row["sha256"] == nested_record["sha256"]
        )
        nested_source = self.mirror / nested_entry["mirror_relative_path"]
        hardlink = nested_source.with_name(nested_source.name + ".hardlink")
        os.link(nested_source, hardlink)
        try:
            producer_path, mirror_path = self.write_case()
            with self.assertRaises(adapter.GateError):
                adapter.adapt_interface(
                    producer_path,
                    mirror_path,
                    self.mirror,
                    self.root / "formal_v6_hardlink_source",
                )
        finally:
            hardlink.unlink()

        producer_path, mirror_path = self.write_case()
        output = self.root / "formal_v6_output_root_swap"
        parked = self.root / "formal_v6_output_root_first_held"
        redirected = self.root / "formal_v6_output_root_redirected"
        original_write = adapter._write_exclusive_at
        root_swapped = False

        def swap_root_during_first_write(
            directory_fd: int, name: str, raw: bytes, mode: int = 0o600,
        ) -> None:
            nonlocal root_swapped
            if not root_swapped:
                os.rename(output, parked)
                redirected.mkdir()
                (redirected / "portable_sources").mkdir()
                output.symlink_to(redirected.name, target_is_directory=True)
                root_swapped = True
            original_write(directory_fd, name, raw, mode)

        adapter._write_exclusive_at = swap_root_during_first_write
        try:
            with self.assertRaises(adapter.GateError):
                adapter.adapt_interface(
                    producer_path, mirror_path, self.mirror, output
                )
        finally:
            adapter._write_exclusive_at = original_write
        self.assertTrue(root_swapped)
        self.assertTrue((parked / "ADAPTER_FAIL_NO_GO.txt").is_file())
        self.assertFalse(
            (redirected / "COMPLETE_EMX_RESULT_INTERFACE_PORTABLE_V8.json").exists()
        )
        self.assertFalse((redirected / "ADAPTER_RECEIPT.json").exists())

    def test_33_formal_v6_nested_artifact_bytes_are_in_exact_closure(self) -> None:
        stage07_path, stage07_sha, _ = self.paths["stage07_manifest"]
        stage07_manifest = json.loads(self.raw_by_ref[(stage07_path, stage07_sha)])
        artifact = stage07_manifest["artifacts"]["end_to_end_funnel_counts.png"]
        matching = [
            row
            for row in self.mirror_entries
            if row["original_path"] == artifact["path"]
            and row["sha256"] == artifact["sha256"]
        ]
        self.assertEqual(len(matching), 1)
        phantom_entries = [row for row in self.mirror_entries if row is not matching[0]]
        producer_path, mirror_path = self.write_case(entries=phantom_entries)
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                producer_path,
                mirror_path,
                self.mirror,
                self.root / "formal_v6_phantom_nested_artifact",
            )

    def test_34_formal_v6_funnel_and_survivor_semantics_are_closed(self) -> None:
        count_attack = copy.deepcopy(self.payload)
        count_attack["run"]["gds_pass_count"] = 1
        unconditional_attack = copy.deepcopy(self.payload)
        unconditional_attack["run"]["survivor_conditioning_statement"] = (
            "Unconditional original 10,000 accuracy is proven by 7,298 survivors; "
            "2,074 analytical, 553 Cadence, and 75 Calibre failures are listed."
        )
        for label, hostile in (
            ("gds_count", count_attack),
            ("unconditional_narrative", unconditional_attack),
        ):
            with self.subTest(label=label), self.assertRaises(adapter.GateError):
                adapter.validate_producer_interface(hostile)
            self.payload = hostile
            producer_path, mirror_path = self.write_case()
            with self.subTest(label=f"{label}_e2e"), self.assertRaises(adapter.GateError):
                adapter.adapt_interface(
                    producer_path,
                    mirror_path,
                    self.mirror,
                    self.root / f"formal_v6_{label}_e2e",
                )

    def test_35_formal_v6_no_go_is_exactly_hash_bound(self) -> None:
        package_root = Path(__file__).resolve().parent
        binding = json.loads(
            (package_root / "FORMAL_V6_NO_GO_BINDING.json").read_text(encoding="utf-8")
        )
        formal = binding["formal_audit"]
        self.assertEqual(formal["verdict"], "NO_GO_FOR_PORTABLE_MONDAY_REPORT_INTERFACE_V6")
        self.assertEqual(formal["finding_counts"], {"P0": 0, "P1": 6, "P2": 0, "P3": 0})
        self.assertEqual(formal["gate_counts"], {"total": 32, "pass": 20, "fail": 12})
        self.assertEqual(formal["finding_ids"], [f"P1-{index:02d}" for index in range(1, 7)])
        expected_failed_gate_ids = [
            "H04_NESTED_AUTHORITY_INJECTION",
            "B02_PORTABLE_BOOL_FIELD_AS_INT",
            "B04_PORTABLE_CONTINUOUS_AS_BOOL",
            "H04_MAPPING_FALSE_AS_INT",
            "F03_RELEASE_FALSE_AS_INT",
            "G03_CONSUMER_HASH_PARSE_SAME_BYTES",
            "G01_OUTPUT_ANCESTOR_SYMLINK",
            "G03_OUTPUT_ROOT_SWAP_TOCTOU",
            "G02_HARDLINK_SOURCE",
            "D04_GDS_COUNT_CROSS_BIND",
            "H06_SURVIVOR_CONDITIONAL_NARRATIVE",
            "D06_NESTED_ARTIFACT_BYTES_CLOSURE",
        ]
        self.assertEqual(formal["failed_gate_ids"], expected_failed_gate_ids)
        audit_root = PHYSICAL_EVIDENCE_ROOT / Path(formal["root"]).name
        artifact_names = {
            "sha256sums_sha256": "SHA256SUMS",
            "receipt_sha256": "INDEPENDENT_AUDIT_RECEIPT.json",
            "report_sha256": "INDEPENDENT_AUDIT_REPORT_CN.md",
            "findings_sha256": "INDEPENDENT_FINDINGS.json",
            "hostile_output_sha256": "INDEPENDENT_HOSTILE_OUTPUT.json",
            "harness_sha256": "independent_hostile_audit_v6.py",
            "audit_manifest_sha256": "AUDIT_MANIFEST.json",
            "test_matrix_sha256": "TEST_MATRIX_RESULT_BLIND_V6_PRE_CANDIDATE_FROZEN.md",
        }
        for field, name in artifact_names.items():
            self.assertEqual(sha(adapter._read_regular_bytes(audit_root / name)), formal[field])
        hostile_output = json.loads(
            adapter._read_regular_bytes(audit_root / "INDEPENDENT_HOSTILE_OUTPUT.json")
        )
        self.assertEqual(
            [row["gate_id"] for row in hostile_output["failed_gates"]],
            expected_failed_gate_ids,
        )
        candidate = binding["candidate"]
        candidate_root = PHYSICAL_EVIDENCE_ROOT / Path(candidate["root"]).name
        candidate_names = {
            "sha256sums_sha256": "SHA256SUMS",
            "adapter_sha256": "adapt_complete_emx_interface_v6.py",
            "consumer_sha256": "consume_portable_emx_interface_v6.py",
            "contract_sha256": "REPORT_INTERFACE_COMPATIBILITY_CONTRACT_V6.json",
        }
        for field, name in candidate_names.items():
            self.assertEqual(
                sha(adapter._read_regular_bytes(candidate_root / name)), candidate[field]
            )
        self.assertIs(candidate["candidate_modified"], False)
        self.assertTrue(all(value is False for value in binding["boundaries"].values() if value is not True))
        self.assertIs(binding["boundaries"]["local_synthetic_only"], True)

    def test_36_combined_formal_v6_attacks_fail_closed(self) -> None:
        portable = self.adapt("formal_v6_combined_portable")

        def mutate_portable(value: dict[str, Any]) -> None:
            value["terminal_normalized_gds_identity_audit"][
                "result_access_authorized"
            ] = False
            value["fixed_bin_metrics"][0]["is_overflow"] = 0
            value["compatibility_adapter"][
                "numeric_value_or_denominator_mutation_allowed"
            ] = 0
            value["complete_emx_release_chain"]["rmse_addition_allowed"] = 0
            value["run"]["survivor_scope"]["original_target_count"] = 7298

        with self.assertRaises(adapter.GateError):
            self.rehash_portable_and_consume(portable, mutate_portable)

        self.install_sources()
        stage07_path, stage07_sha, _ = self.paths["stage07_manifest"]
        stage07_manifest = json.loads(self.raw_by_ref[(stage07_path, stage07_sha)])
        artifact = stage07_manifest["artifacts"]["end_to_end_funnel_counts.png"]
        phantom_entries = [
            row
            for row in self.mirror_entries
            if not (
                row["original_path"] == artifact["path"]
                and row["sha256"] == artifact["sha256"]
            )
        ]
        hardlink_victim = self.mirror / phantom_entries[0]["mirror_relative_path"]
        hardlink = hardlink_victim.with_name(hardlink_victim.name + ".combined-hardlink")
        os.link(hardlink_victim, hardlink)
        real_parent = self.root / "combined_real_output_parent"
        real_parent.mkdir()
        linked_parent = self.root / "combined_linked_output_parent"
        linked_parent.symlink_to(real_parent.name, target_is_directory=True)
        try:
            producer_path, mirror_path = self.write_case(entries=phantom_entries)
            with self.assertRaises(adapter.GateError):
                adapter.adapt_interface(
                    producer_path,
                    mirror_path,
                    self.mirror,
                    linked_parent / "combined_bad_bundle",
                )
        finally:
            hardlink.unlink()
        self.assertFalse((real_parent / "combined_bad_bundle").exists())

    def test_37_formal_v7_output_file_version_replacement_is_rejected(self) -> None:
        producer_path, mirror_path = self.write_case()
        output = self.root / "formal_v7_output_version_replacement"
        replacement = self.root / "formal_v7_replacement.tmp"
        replacement.write_bytes(b'{"schema":"different-output-version"}\n')
        original_write = adapter._write_exclusive_at
        replaced = False

        def replace_after_interface_write(
            directory_fd: int, name: str, raw: bytes, mode: int = 0o600,
        ) -> None:
            nonlocal replaced
            original_write(directory_fd, name, raw, mode)
            if name == "COMPLETE_EMX_RESULT_INTERFACE_PORTABLE_V8.json":
                os.replace(replacement, output / name)
                replaced = True

        adapter._write_exclusive_at = replace_after_interface_write
        try:
            with self.assertRaises(adapter.GateError):
                adapter.adapt_interface(producer_path, mirror_path, self.mirror, output)
        finally:
            adapter._write_exclusive_at = original_write
        self.assertTrue(replaced)
        self.assertTrue((output / "ADAPTER_FAIL_NO_GO.txt").is_file())
        self.assertFalse((output / "ADAPTER_RECEIPT.json").exists())
        self.assertFalse((output / "SHA256SUMS").exists())

    def test_38_formal_v7_nested_roles_require_unique_artifact_identities(self) -> None:
        def alias_two_roles(value: dict[str, Any]) -> None:
            artifacts = value["artifacts"]
            artifacts["fresh_emx_error_distributions.png"] = copy.deepcopy(
                artifacts["end_to_end_funnel_counts.png"]
            )

        self.install_sources({"stage07_manifest": alias_two_roles})
        producer_path, mirror_path = self.write_case()
        with self.assertRaises(adapter.GateError):
            adapter.adapt_interface(
                producer_path,
                mirror_path,
                self.mirror,
                self.root / "formal_v7_nested_role_alias",
            )

    def test_39_formal_v7_survivor_statement_is_exact_bound(self) -> None:
        adapter.validate_producer_survivor_statement(
            adapter.PRODUCER_SURVIVOR_CONDITIONING_STATEMENT
        )
        hostile_statements = (
            "Accuracy over all original 10,000 targets is 99%; the evaluated evidence "
            "contains 7,298 survivors after 2,074 analytical, 553 Cadence and 75 Calibre failures.",
            "The original 10,000-target model accuracy is 99%; 7,298 survived after "
            "2,074 analytical, 553 Cadence and 75 Calibre failures.",
            "原始10,000个目标准确率为99%；7,298个存活，另有2,074、553和75个失败。",
            adapter.PRODUCER_SURVIVOR_CONDITIONING_STATEMENT + " Accuracy is 99%.",
        )
        for position, statement in enumerate(hostile_statements):
            with self.subTest(position=position), self.assertRaises(adapter.GateError):
                adapter.validate_producer_survivor_statement(statement)

        payload = copy.deepcopy(self.payload)
        payload["run"]["survivor_conditioning_statement"] = hostile_statements[0]
        with self.assertRaises(adapter.GateError):
            adapter.validate_producer_interface(payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
