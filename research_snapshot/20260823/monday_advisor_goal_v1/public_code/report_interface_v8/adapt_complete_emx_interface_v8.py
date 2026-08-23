#!/usr/bin/env python3
"""Build a self-contained, portable Monday-report EMX interface.

This program is intentionally local-only.  It has no remote/MARS transport or
execution code.  It accepts a *completed local mirror* of the producer
interface and an explicit mirror manifest, validates the frozen scientific
semantics, copies every active source into a no-clobber bundle, and applies only
the enumerated report-interface compatibility mappings.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


INPUT_SCHEMA = "monday_fresh_emx_report_interface_v1"
OUTPUT_SCHEMA = "monday_fresh_emx_report_interface_portable_v8"
FROZEN_RELEASE_CONTRACT_REFERENCE_NAME = "FROZEN_V8_RELEASE_CONTRACT_REFERENCE.json"
FROZEN_RELEASE_CONTRACT_REFERENCE_SHA256 = (
    "42387f9f3372d019eed9d964a4bc9832a52c2c00cd645c02edca6309914c9dd6"
)
MIRROR_SCHEMA = "local_complete_emx_source_mirror_manifest_v1"
SOURCE_METRIC_VERSION = "stats_v2_fixed_frame_three_chain_v1"
REPORT_METRIC_VERSION = "stats_v2"
SOURCE_REPORTING_LABEL = (
    "historical seed20260711 old-checkpoint, unchanged one-shot geometries, "
    "fresh real-EMX survivor evidence"
)
REPORT_REPORTING_LABEL = "conditional_on_analytical_cadence_calibre_pass"
IDENTITY_ALGORITHM = "gdsii-record-sha256-zero-bgnlib-bgnstr-timestamps-v1"
WATCHER_PROCESS_IDENTITY_ALGORITHM = (
    "linux-boot-id-proc-starttime-pidfd-exe-cmdline-script-sha256-v1"
)
MAXIMUM_LAUNCH_RECEIPT_AFTER_PROC_START_NS = 120 * 1_000_000_000
INDEPENDENT_GO_SCHEMA = (
    "historical_200k_fixed10k_post_stage06_release_chain_v5_independent_review_go_v1"
)
INDEPENDENT_GO_STATUS = "GO_FOR_RESULT_FREE_DEPLOYMENT_AND_EXACT_RESUME_ONLY"
RELEASE_CONTRACT_SCHEMA = "historical_200k_fixed10k_post_stage06_release_chain_contract_v5"
BUNDLE_MANIFEST_SCHEMA = "historical_200k_fixed10k_post_stage06_release_chain_bundle_manifest_v5"
RUNTIME_MANIFEST_SCHEMA = (
    "historical_200k_fixed10k_post_stage06_runtime_dependency_identity_manifest_v1"
)
PREPARED_RESULT_FREE_STATUS = (
    "PREPARED_ONLY_RESULT_FREE_NOT_DEPLOYED_AWAITING_INDEPENDENT_REVIEW"
)
PRIMARY_FIXED_BIN_VARIANT = "stats_v2_joint_engineering_primary_q_floor_fixed_bins"
REPORT_PRIMARY_VARIANT = "joint_engineering_fixed_frame_error"
FEATURES = ("lp_nh", "ls_nh", "qmin", "k_abs")
SPANS = {"lp_nh": 2.5, "ls_nh": 2.5, "qmin": 20.0, "k_abs": 1.0}
UNITS = {"lp_nh": "nH", "ls_nh": "nH", "qmin": "dimensionless", "k_abs": "dimensionless"}
FEATURE_LABELS = {"lp_nh": "Lp", "ls_nh": "Ls", "qmin": "Qmin", "k_abs": "|K|"}
PANELS = (
    (0, "overall", "All physical survivors", None),
    (1, "legacy_k_le_0p8", "Legacy |K|≤0.8", "legacy_k_le_0p8"),
    (2, "extension_k_gt_0p8", "Extension |K|>0.8", "extension_k_gt_0p8"),
)
FIXED_BINS = (0.0, 0.025, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0, math.inf)
CHAINS = (
    ("target_vs_proxy", "Target vs frozen proxy"),
    ("target_vs_emx", "Target vs fresh EMX"),
    ("proxy_vs_emx", "Frozen proxy vs fresh EMX"),
)
EXACT_Q_VARIANT = "fixed_span_exact_q_secondary_three_chain"
IDENTITY_RECEIPT_SCHEMA = "historical_200k_fixed10k_stage05_stage06_gds_identity_terminal_receipt_v1"
PREREG_SCHEMA = "historical_200k_fixed10k_fresh_emx_statistics_preregistration_v2"
STAGE07_TERMINAL_SCHEMA = "historical_200k_fixed10k_stage07_canonical_terminal_v5"
STAGE08_TERMINAL_SCHEMA = "historical_200k_fixed10k_stage08_canonical_terminal_v5"
CONTROLLER_TERMINAL_SCHEMA = "historical_200k_fixed10k_stage07_08_controller_terminal_v5"
PREFLIGHT_TERMINAL_SCHEMA = "historical_200k_fixed10k_resume_stage07_08_preflight_terminal_v5"
AUTHORIZATION_SCHEMA = "historical_200k_fixed10k_exact_watcher_resume_authorization_v5"
OUTCOME_SCHEMA = "historical_200k_fixed10k_exact_watcher_resume_outcome_v5"
THREE_CHAIN_TERMINAL_SCHEMA = "historical_200k_fixed10k_matched_survivor_three_chain_canonical_terminal_v5"
FULL_BAND_TERMINAL_SCHEMA = "historical_200k_fixed10k_full_band_s4p_qa_v3_canonical_terminal_v5"
THREE_CHAIN_PREFLIGHT_SCHEMA = "historical_200k_fixed10k_three_chain_preflight_terminal_v5"
FULL_BAND_PREFLIGHT_SCHEMA = "historical_200k_fixed10k_full_band_v3_preflight_terminal_v5"
LAUNCHER_PREFLIGHT_SCHEMA = "historical_200k_fixed10k_bound_launcher_authentication_terminal_v5"
STAGE07_MANIFEST_SCHEMA = "historical_200k_fixed10k_statistics_artifact_manifest_v1"
STAGE08_MANIFEST_SCHEMA = "historical_200k_fixed10k_statistics_v2_artifact_manifest_v1"
THREE_CHAIN_MANIFEST_SCHEMA = "historical_200k_fixed10k_matched_survivor_three_chain_manifest_v1"
FULL_BAND_MANIFEST_SCHEMA = "historical_200k_fixed10k_full_band_s4p_qa_artifact_manifest_v3"
FULL_BAND_INTERNAL_SCHEMA = "historical_200k_fixed10k_full_band_s4p_nonfiltering_qa_terminal_receipt_v3"
TERMINAL_TOP_LEVEL_KEYS = {
    "checks", "error", "evidence", "finished_utc", "overall_status",
    "result_rows_embedded", "schema", "stage",
}
TERMINAL_SPECS = {
    "stage07": (STAGE07_TERMINAL_SCHEMA, "Stage07", {"canonical_validation_passed"}),
    "stage08": (
        STAGE08_TERMINAL_SCHEMA,
        "Stage08",
        {"canonical_validation_passed", "upstream_stage07_pass"},
    ),
    "controller": (
        CONTROLLER_TERMINAL_SCHEMA,
        "Stage07_08_controller",
        {"stage07_pass", "stage08_pass"},
    ),
    "preflight": (
        PREFLIGHT_TERMINAL_SCHEMA,
        "resume_stage07_08_preflight",
        {
            "authenticated_same_fd_context_pass", "frozen_sources_pass",
            "normalized_gds_identity_7298_pass", "process_start_identity_matches_go",
            "watcher_stopped_pidfd_bound",
        },
    ),
    "three_chain": (
        THREE_CHAIN_TERMINAL_SCHEMA,
        "matched_survivor_three_chain",
        {
            "artifact_manifest_exact_closure_pass", "canonical_stage08_terminal_pass",
            "generator_returncode_zero", "rowwise_target_candidate_panel_binding_pass",
        },
    ),
    "full_band_v3": (
        FULL_BAND_TERMINAL_SCHEMA,
        "full_band_s4p_qa_v3",
        {
            "artifact_manifest_and_internal_terminal_exact_closure_pass",
            "canonical_stage08_terminal_pass", "diagnostic_flags_nonfiltering",
            "panel_counts_5992_1306_exact",
            "rowwise_target_candidate_panel_geometry_touchstone_binding_pass",
            "stage08_15ghz_primary_unchanged",
        },
    ),
    "three_chain_preflight": (
        THREE_CHAIN_PREFLIGHT_SCHEMA,
        "three_chain_preflight",
        {
            "authenticated_same_fd_context_pass", "canonical_stage08_terminal_pass",
            "exact_stage08_manifest_pass", "generator_same_fd_bound",
            "runtime_dependency_identity_pass",
        },
    ),
    "full_band_v3_preflight": (
        FULL_BAND_PREFLIGHT_SCHEMA,
        "full_band_v3_preflight",
        {
            "authenticated_same_fd_context_pass", "canonical_stage08_terminal_pass",
            "exact_stage08_manifest_pass", "full_band_v3_generator_same_fd_bound",
            "panel_schema_addendum_and_unchanged_method_bound",
            "runtime_dependency_identity_pass",
        },
    ),
    "launcher_preflight": (
        LAUNCHER_PREFLIGHT_SCHEMA,
        "bound_launcher_authentication",
        {
            "bundle_exact_file_set_pass", "common_and_controller_same_fd_bound",
            "contract_and_go_same_fd_bound", "interpreter_identity_pass",
            "runtime_dependency_exact_tree_and_root_lease_pass",
        },
    ),
}
PRODUCER_TERMINAL_ROLES = {
    "canonical_controller",
    "canonical_stage07",
    "canonical_stage08",
    "canonical_stage07_manifest",
    "canonical_stage08_manifest",
    "canonical_three_chain",
    "canonical_three_chain_manifest",
    "canonical_full_band_v3",
    "canonical_full_band_v3_manifest",
    "canonical_full_band_v3_internal_terminal",
    "canonical_resume_preflight_terminal",
    "canonical_three_chain_preflight_terminal",
    "canonical_full_band_v3_preflight_terminal",
    "canonical_launcher_preflight__build_complete_emx_interface_v5.py",
    "canonical_launcher_preflight__resume_exact_watcher_stage07_08_v5.py",
    "canonical_launcher_preflight__run_full_band_v3_after_stage08_v5.py",
    "canonical_launcher_preflight__run_three_chain_after_stage08_v5.py",
}
RELEASE_RECORD_BINDINGS = {
    "canonical_controller": "controller_terminal",
    "canonical_stage07": "stage07_terminal",
    "canonical_stage08": "stage08_terminal",
    "canonical_stage07_manifest": "stage07_manifest",
    "canonical_stage08_manifest": "stage08_manifest",
    "canonical_three_chain": "three_chain_terminal",
    "canonical_three_chain_manifest": "three_chain_manifest",
    "canonical_full_band_v3": "full_band_v3_terminal",
    "canonical_full_band_v3_manifest": "full_band_v3_manifest",
    "canonical_full_band_v3_internal_terminal": "full_band_v3_internal_terminal",
    "canonical_resume_preflight_terminal": "resume_preflight_terminal",
    "canonical_three_chain_preflight_terminal": "three_chain_preflight_terminal",
    "canonical_full_band_v3_preflight_terminal": "full_band_v3_preflight_terminal",
}
LAUNCHER_PREFLIGHT_KEYS = {
    "launcher_preflight__build_complete_emx_interface_v5.py",
    "launcher_preflight__resume_exact_watcher_stage07_08_v5.py",
    "launcher_preflight__run_full_band_v3_after_stage08_v5.py",
    "launcher_preflight__run_three_chain_after_stage08_v5.py",
}
RELEASE_FLAG_VALUES = {
    "all_target_candidate_panel_geometry_gds_touchstone_rows_bound": True,
    "stage08_three_target_proxy_emx_values_bound_rowwise": True,
    "full_band_diagnostic_flags_filter_candidates": False,
    "full_band_changes_stage07_stage08_15ghz_primary": False,
    "superseded_7a0af63c_terminal_accepted": False,
    "rmse_addition_allowed": False,
    "q_primary_semantics": "target floor pass and one-sided shortfall",
    "k_target_relative_percentage_primary_allowed": False,
}
PROCESS_IDENTITY_KEYS = {
    "algorithm", "boot_id", "cmdline_sha256", "exe_device", "exe_inode",
    "exe_realpath", "exe_sha256", "exe_size_bytes", "launch_receipt_sha256",
    "launch_receipt_after_proc_start_ns", "pid", "ppid", "proc_starttime_ticks",
    "proc_start_unix_ns", "script_sha256", "stage06_supervisor_pid", "uid", "argv",
}
MANIFEST_ARTIFACT_KEYS = {
    "stage07_manifest": {
        "historical_200k_end_to_end_10000_status_rows.csv", "end_to_end_funnel_counts.png",
        "fresh_emx_error_distributions.png", "historical_200k_fresh_emx_evaluated_rows.csv",
        "historical_200k_fresh_emx_statistics_summary.json", "panel_error_percentiles.png",
    },
    "stage08_manifest": {
        "end_to_end_10000_funnel", "feature_error_primary_summary", "fixed_histogram_counts",
        "fixed_secondary_percent_histogram_counts", "joint_engineering_fixed_histogram",
        "legacy_extension_fixed_frame_percentiles", "proxy_fixed_histograms",
        "proxy_vs_emx_identity_and_residuals", "summary", "target_fixed_histograms", "v2_rows",
    },
    "three_chain_manifest": {"chart", "metrics_table", "rows", "summary", "target_floor_metrics_table"},
    "full_band_v3_manifest": {"fixed_histograms", "plot", "rows", "summary"},
}


def nested_artifact_role(kind: str, section: str, key: str) -> str:
    normalized = key.replace("/", "__")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", normalized):
        raise GateError(f"nested artifact role key is not canonical: {kind}/{section}/{key}")
    return f"nested__{kind}__{section}__{normalized}"


FEATURE_METRIC_FIELDS = (
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "feature_key", "feature", "unit", "row_count", "metric_contract_version",
    "bias_physical", "mae_physical", "rmse_physical", "bias_fixed_frame_fraction",
    "mae_fixed_frame_fraction", "rmse_fixed_frame_range_fraction", "p50_physical",
    "p90_physical", "p95_physical", "p99_physical", "maximum_physical",
    "p50_fixed_frame_fraction", "p90_fixed_frame_fraction", "p95_fixed_frame_fraction",
    "p99_fixed_frame_fraction", "maximum_fixed_frame_fraction",
)
JOINT_METRIC_FIELDS = (
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "metric_variant", "row_count", "joint_mae_fraction", "joint_rmse_fraction",
    "p50_fraction", "p90_fraction", "p95_fraction", "p99_fraction", "maximum_fraction",
)
Q_METRIC_FIELDS = (
    "panel_order", "panel_key", "panel", "row_count", "q_target_met_fraction",
    "q_shortfall_mae", "q_shortfall_rmse", "q_shortfall_p50", "q_shortfall_p90",
    "q_shortfall_p95", "q_shortfall_p99", "q_shortfall_max",
)
FIXED_BIN_METRIC_FIELDS = (
    "panel_order", "panel_key", "panel", "comparison_scope", "comparison_scope_label",
    "metric_variant", "bin_order", "bin_label", "lower_fraction",
    "upper_fraction_or_inf", "is_overflow", "count", "denominator", "fraction",
)
STAGE_COUNT_FIELDS = (
    "stage_order", "stage", "eligible", "completed", "status", "denominator_note",
)
STAGE_COUNT_ROWS = (
    {
        "stage_order": 0, "stage": "Frozen targets", "eligible": 10000,
        "completed": 10000, "status": "complete",
        "denominator_note": "original fixed denominator",
    },
    {
        "stage_order": 1, "stage": "Analytical gate", "eligible": 10000,
        "completed": 7926, "status": "complete",
        "denominator_note": "2074 FAIL retained",
    },
    {
        "stage_order": 2, "stage": "Cadence streamout", "eligible": 7926,
        "completed": 7373, "status": "complete",
        "denominator_note": "553 FAIL retained",
    },
    {
        "stage_order": 3, "stage": "Zero-blocking Calibre", "eligible": 7373,
        "completed": 7298, "status": "complete",
        "denominator_note": "75 blocking FAIL retained",
    },
    {
        "stage_order": 4, "stage": "Fresh real EMX", "eligible": 7298,
        "completed": 7298, "status": "complete",
        "denominator_note": "survivor-conditioned numeric denominator",
    },
    {
        "stage_order": 5, "stage": "Full-band S4P QA v3", "eligible": 7298,
        "completed": 7298, "status": "complete",
        "denominator_note": "diagnostic flags are non-filtering",
    },
)
PAIRED_FEATURE_ROW_FIELDS = (
    "target_id", "panel_key", "panel", "feature_key", "feature", "unit",
    "target_value", "proxy_value", "emx_value", "target_normalized_fraction",
    "proxy_normalized_fraction", "emx_normalized_fraction",
    "proxy_minus_emx_range_fraction",
)
PRODUCER_ROOT_KEYS = {
    "schema", "status", "run", "fresh_emx_stage06_running_state",
    "terminal_normalized_gds_identity_audit", "fresh_metric_contract", "source_files",
    "stage_counts", "comparison_feature_metrics", "joint_metrics", "q_metrics",
    "fixed_bin_metrics", "paired_feature_rows", "completion_contract",
    "complete_emx_release_chain",
}
PORTABLE_ROOT_KEYS = PRODUCER_ROOT_KEYS | {"compatibility_adapter", "portable_source_closure"}
PRODUCER_RUN_KEYS = {
    "run_id", "sampling_mode", "planned_emx_count", "selection_manifest_path",
    "selection_manifest_sha256", "selection_is_response_blind", "selection_strata",
    "selection_weights_path", "selection_weights_sha256", "gds_pass_count",
    "calibre_pass_count", "calibre_blocking_fail_count",
    "calibre_nonblocking_warning_count", "emx_complete_count", "emx_fail_count",
    "wall_time_seconds", "concurrency", "mars_executed_statistics_copy_path",
    "mars_executed_statistics_copy_sha256", "survivor_conditioning_statement",
    "fresh_emx_reporting_label", "terminal_status",
}
PORTABLE_RUN_KEYS = PRODUCER_RUN_KEYS | {
    "fresh_emx_evidence_scope_detail", "survivor_scope",
}
PRODUCER_IDENTITY_KEYS = {
    "status", "expected_candidate_count", "algorithm", "terminal_match_count",
    "terminal_mismatch_count", "receipt_path", "receipt_sha256",
    "result_publication_allowed",
}
PORTABLE_IDENTITY_KEYS = PRODUCER_IDENTITY_KEYS | {"producer_status"}
PRODUCER_STAGE06_KEYS = {
    "status", "expected_candidate_count", "identity_audit_gate_status",
    "full_7298_normalized_identity_terminal_audit_present",
    "stage07_result_present", "stage08_result_present",
    "numeric_fresh_emx_claim_allowed",
}
PORTABLE_STAGE06_KEYS = PRODUCER_STAGE06_KEYS | {"producer_template_status"}
PRODUCER_METRIC_CONTRACT_KEYS = {
    "status", "primary_error_representations", "k_fixed_frame_span",
    "k_target_relative_percentage_primary_allowed",
    "k_target_relative_percentage_composite_gate_allowed",
    "q_floor_shortfall_required", "bins_frozen_before_results_required",
    "overflow_bin_required", "axis_limit_source",
    "observed_p99_adaptive_axis_allowed",
    "statistics_v1_k_ape_p99_adaptive_primary_allowed",
    "statistics_v2_manifest_path", "statistics_v2_manifest_sha256",
    "statistics_v2_readme_path", "statistics_v2_readme_sha256",
}
PORTABLE_METRIC_CONTRACT_KEYS = PRODUCER_METRIC_CONTRACT_KEYS | {
    "producer_metric_contract_version", "report_interface_metric_contract_version",
    "primary_joint_adapter_derivation",
}
COMPLETION_CONTRACT_KEYS = {
    "comparison_feature_metric_fields", "joint_metric_fields", "q_metric_fields",
    "fixed_bin_metric_fields", "stage_count_fields", "paired_feature_row_fields",
}
SURVIVOR_CONDITIONING_STATEMENT = (
    "Survivor-conditional descriptive statistics only: the numeric EMX denominator is "
    "7,298 after 2,074 analytical, 553 Cadence, and 75 Calibre blocking failures "
    "from the original 10,000 fixed targets; this is not an unconditional original-10,000 "
    "accuracy estimate."
)
PRODUCER_SURVIVOR_CONDITIONING_STATEMENT = (
    "Numeric metrics are conditional on the 7,298 analytical+Cadence+zero-blocking-Calibre survivors; "
    "2,074 analytical, 553 Cadence and 75 blocking-Calibre failures remain in the 10,000 denominator."
)
SURVIVOR_SCOPE = {
    "original_target_count": 10000,
    "analytical_pass_count": 7926,
    "cadence_pass_count": 7373,
    "calibre_gds_pass_count": 7298,
    "fresh_emx_numeric_count": 7298,
    "legacy_survivor_count": 5992,
    "extension_survivor_count": 1306,
    "statistics_conditioning": "survivor_conditional_not_original_10000_unconditional_accuracy",
}
AUTHORITY_LIKE_KEY = re.compile(
    r"(?:^|_)(?:authority|authorized|authorization|permission|result_access|"
    r"execution_authority|deployment_authority|mars_access|watcher_signal)(?:$|_)"
)
RELEASE_CONTRACT_ROOT_KEYS = {
    "schema", "status", "execution_authorized", "deployment_authorized",
    "independent_review_required", "expected_count", "expected_original_denominator",
    "expected_panel_counts", "active_watcher", "identity_gate", "stage07", "stage08",
    "canonical_control", "three_chain", "full_band_superseded_v2", "full_band_v3",
    "runtime", "execution_units", "preflight_attempts", "final_interface",
    "deployment_bootstrap", "terminal_check_contracts", "semantic_validation_contract",
    "independent_go",
}
# Synthetic result-blind fixtures replace only identities that are independently
# cross-bound elsewhere.  Every policy, authority flag, key set, list order, and
# all other values remain byte-for-byte semantic projections of frozen-v8.
RELEASE_CONTRACT_DYNAMIC_FIELDS = {
    ("active_watcher", "expected_host"),
    ("active_watcher", "expected_pid"),
    ("active_watcher", "expected_stage06_supervisor_pid"),
    ("active_watcher", "expected_script_path"),
    ("active_watcher", "expected_script_sha256"),
    ("active_watcher", "launch_receipt_path"),
    ("active_watcher", "launch_receipt_sha256"),
    ("active_watcher", "launch_receipt_schema"),
    ("runtime", "python_path"),
    ("runtime", "dependency_manifest_path"),
    ("runtime", "private_site_packages_root"),
    ("stage07", "generator_path"),
    ("stage07", "generator_sha256"),
    ("stage08", "generator_path"),
    ("stage08", "generator_sha256"),
    ("stage08", "preregistration_path"),
    ("stage08", "preregistration_sha256"),
    ("three_chain", "generator_path"),
    ("three_chain", "generator_sha256"),
    ("full_band_v3", "bundle_runtime_dir"),
    ("full_band_v3", "generator_name"),
    ("full_band_v3", "generator_sha256"),
    ("full_band_v3", "base_generator_path"),
    ("full_band_v3", "base_generator_sha256"),
    ("full_band_v3", "panel_addendum_name"),
    ("full_band_v3", "panel_addendum_sha256"),
    ("full_band_v3", "method_preregistration_path"),
    ("full_band_v3", "method_preregistration_sha256"),
    ("full_band_v3", "stage06_config_path"),
    ("full_band_v3", "stage06_config_sha256"),
}
COMPATIBILITY_ADAPTER_KEYS = {
    "schema", "status", "input_schema", "output_schema", "identity_status_mapping",
    "stage06_terminalization_mapping", "reporting_label_mapping", "metric_version_mapping",
    "fixed_bin_variant_mapping", "primary_joint_derivation",
    "source_scientific_projection_sha256", "output_restored_scientific_projection_sha256",
    "numeric_value_or_denominator_mutation_allowed", "proxy_emx_target_chains_may_be_combined",
    "q_floor_is_primary_and_exact_q_is_secondary",
    "survivor_metrics_are_original_10000_unconditional",
}
LAUNCHER_EVIDENCE_KEYS = {
    "bundle_manifest", "common", "contract", "controller",
    "independent_review_go", "runtime_dependency_manifest",
}
RESUME_PREFLIGHT_EVIDENCE_KEYS = {
    "bundle_manifest", "contract", "identity_gate", "launcher_authentication_terminal",
    "review_go", "runtime_dependency_manifest", "runtime_interpreter", "sources",
    "watcher_launch_receipt", "watcher_process_identity", "watcher_script",
}
THREE_PREFLIGHT_EVIDENCE_KEYS = {
    "generator", "launcher_authentication_terminal", "runtime_dependency_manifest",
    "stage08_terminal",
}
FULL_PREFLIGHT_EVIDENCE_KEYS = {
    "runtime_dependency_manifest", "launcher_authentication_terminal", "source_bindings",
    "stage08_terminal",
}
FULL_SOURCE_BINDING_KEYS = {
    "panel_schema_addendum", "superseded_base_generator",
    "unchanged_method_preregistration", "unchanged_stage06_config", "v3_generator",
}
FULL_MANIFEST_INPUT_KEYS = {
    "identity_receipt", "identity_summary", "identity_rows", "stage06_launch_receipt",
    "stage06_terminal_receipt", "stage06_dataset_rows", "stage06_config",
    "stage08_terminal_receipt", "method_preregistration", "auditor_script",
}
FULL_MANIFEST_RUNTIME_KEYS = {
    "rfic_transformer_inverse_design/analysis/extraction.py",
    "rfic_transformer_inverse_design/sim/touchstone.py",
    "rfic_transformer_inverse_design/network_analysis.py",
    "rfic_transformer_inverse_design/dataset.py",
}
NESTED_ARTIFACT_ROLES = {
    nested_artifact_role("stage07_manifest", "script", "script"),
    *{
        nested_artifact_role("stage07_manifest", "artifacts", key)
        for key in MANIFEST_ARTIFACT_KEYS["stage07_manifest"]
    },
    *{
        nested_artifact_role("stage08_manifest", "artifacts", key)
        for key in MANIFEST_ARTIFACT_KEYS["stage08_manifest"]
    },
    *{
        nested_artifact_role("three_chain_manifest", "artifacts", key)
        for key in MANIFEST_ARTIFACT_KEYS["three_chain_manifest"]
    },
    *{
        nested_artifact_role("full_band_v3_manifest", "inputs", key)
        for key in FULL_MANIFEST_INPUT_KEYS
    },
    *{
        nested_artifact_role("full_band_v3_manifest", "runtime_sources", key)
        for key in FULL_MANIFEST_RUNTIME_KEYS
    },
    *{
        nested_artifact_role("full_band_v3_manifest", "outputs", key)
        for key in MANIFEST_ARTIFACT_KEYS["full_band_v3_manifest"]
    },
}
GO_KEYS = {
    "bundle_manifest_sha256", "contract_sha256", "exact_resume_only", "reviewed_utc",
    "reviewer", "runtime_dependency_manifest_path", "runtime_dependency_manifest_sha256",
    "runtime_dependency_root_digest", "runtime_python_path", "runtime_python_sha256",
    "runtime_site_packages_root", "schema", "scientific_release_authorized", "status",
    "training_authorized", "transport_authorized", "watcher_process_identity",
}
WATCHER_LAUNCH_RECEIPT_KEYS = {
    "schema", "status_at_receipt", "started_utc", "host", "pid",
    "stage06_supervisor_pid", "watcher_script", "physical_statistics_script_sha256",
    "v2_statistics_script_sha256", "preregistration_sha256", "expected_physical_count",
    "v2_is_primary_for_final_report", "v1_preserved_as_superseded_physical_evidence",
    "no_clobber",
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class GateError(RuntimeError):
    """A fail-closed compatibility or provenance gate failed."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GateError(f"value is not strict finite JSON: {exc}") from exc
    return (text + "\n").encode("utf-8")


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise GateError(f"{label} must be lowercase SHA-256")
    return value


def require_exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise GateError(f"{label} must equal exact integer {expected}")


COUNT_CONTAINER_KEYS = {
    "counts", "expected_panel_counts", "panel_counts", "selection_strata",
}
EXACT_INTEGER_FIELD_NAMES = {
    "bin_order", "concurrency", "denominator", "eligible", "completed", "order",
    "panel_order", "points", "stage_order",
}


def validate_exact_count_types(
    value: Any,
    label: str,
    *,
    field_name: str | None = None,
    inside_count_container: bool = False,
) -> None:
    """Reject bool and integral-float substitutions for every count-bearing field."""
    if isinstance(value, Mapping):
        for child_name, child in value.items():
            child_is_container = inside_count_container or child_name in COUNT_CONTAINER_KEYS
            validate_exact_count_types(
                child,
                f"{label}.{child_name}",
                field_name=child_name,
                inside_count_container=child_is_container,
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            validate_exact_count_types(
                child,
                f"{label}[{index}]",
                field_name=field_name,
                inside_count_container=inside_count_container,
            )
        return
    is_count = (
        inside_count_container
        or field_name in EXACT_INTEGER_FIELD_NAMES
        or (isinstance(field_name, str) and field_name.endswith("_count"))
        or (isinstance(field_name, str) and field_name.endswith("_denominator"))
    )
    if is_count and type(value) is not int:
        raise GateError(f"{label} must be an exact built-in integer count")


def require_exact_typed_value(actual: Any, expected: Any, label: str) -> None:
    """Deep exact equality that never conflates bool/int/float."""
    if type(actual) is not type(expected):
        raise GateError(
            f"{label} exact type drift: {type(actual).__name__} != {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        if set(actual) != set(expected):
            raise GateError(f"{label} exact key-set drift")
        for key in expected:
            require_exact_typed_value(actual[key], expected[key], f"{label}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise GateError(f"{label} exact list length drift")
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            require_exact_typed_value(actual_item, expected_item, f"{label}[{index}]")
        return
    if actual != expected:
        raise GateError(f"{label} exact value drift")


def reject_authority_like_keys(value: Any, label: str, path: tuple[str, ...] = ()) -> None:
    """Reject undeclared action-scope keys before any producer value propagates."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise GateError(f"{label} contains a non-string object key")
            if AUTHORITY_LIKE_KEY.search(key):
                location = ".".join((*path, key))
                raise GateError(f"{label} contains undeclared authority-like key: {location}")
            reject_authority_like_keys(child, label, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_authority_like_keys(child, label, (*path, str(index)))


def finite(value: Any, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise GateError(f"{label} must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise GateError(f"{label} must be finite")
    return result


def safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise GateError(f"{label} must be a non-empty POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise GateError(f"{label} is not a safe relative path")
    return pure.as_posix()


def _read_regular_bytes(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.name or path.name in (".", ".."):
        raise GateError(f"invalid regular source path: {path!r}")
    return _read_relative_regular_bytes(path.parent, path.name, f"regular source {path}")


def _open_root_component_chain(root: Path, label: str) -> list[int]:
    """Lease every root-path component without following ancestor symlinks."""
    raw = os.fspath(root)
    if not raw or "\x00" in raw:
        raise GateError(f"{label} root path is invalid")
    pure = PurePosixPath(raw)
    if not pure.is_absolute():
        if any(part in ("", ".", "..") for part in pure.parts):
            raise GateError(f"{label} relative root contains a forbidden component")
        # os.getcwd() is the kernel-reported physical cwd; prepend lexically and
        # still traverse every resulting absolute component from the trusted / fd.
        pure = PurePosixPath(os.getcwd()) / pure
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    held: list[int] = []
    try:
        current_fd = os.open("/", directory_flags)
        components = pure.parts[1:]
        held.append(current_fd)
        for part in components:
            if part in ("", ".", ".."):
                raise GateError(f"{label} root contains forbidden component {part!r}")
            try:
                current_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                raise GateError(
                    f"{label} root ancestor is not a nofollow directory: {part}"
                ) from exc
            if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
                os.close(current_fd)
                raise GateError(f"{label} root ancestor is not a directory: {part}")
            held.append(current_fd)
        return held
    except Exception:
        for fd in reversed(held):
            os.close(fd)
        raise


def _source_stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def _read_fd_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


class HeldRootLease:
    """Hold a nofollow root component chain and verify its named continuity."""

    def __init__(self, root: Path, label: str):
        self.root = root
        self.label = label
        self._fds = _open_root_component_chain(root, label)
        self.root_fd = self._fds[-1]
        self._identities = [
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in self._fds
        ]
        self._closed = False

    def verify_named_continuity(self) -> None:
        if self._closed:
            raise GateError(f"{self.label} root lease is closed")
        fresh = _open_root_component_chain(self.root, f"{self.label} continuity")
        try:
            fresh_identities = [
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in fresh
            ]
            held_identities = [
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in self._fds
            ]
            if held_identities != self._identities or fresh_identities != self._identities:
                raise GateError(f"{self.label} named root no longer identifies the held chain")
        finally:
            for fd in reversed(fresh):
                os.close(fd)

    def _open_file_fd(self, relative: str, label: str) -> int:
        safe = safe_relative(relative, label)
        parts = PurePosixPath(safe).parts
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        current_fd = self.root_fd
        temporary: list[int] = []
        try:
            for part in parts[:-1]:
                try:
                    current_fd = os.open(part, directory_flags, dir_fd=current_fd)
                except OSError as exc:
                    raise GateError(
                        f"{label} ancestor component is not a non-symlink directory: {part}"
                    ) from exc
                temporary.append(current_fd)
            try:
                return os.open(parts[-1], file_flags, dir_fd=current_fd)
            except OSError as exc:
                raise GateError(
                    f"{label} final component is not a non-symlink regular file"
                ) from exc
        finally:
            for fd in reversed(temporary):
                os.close(fd)

    def open_regular(self, relative: str, label: str) -> "HeldRegularSnapshot":
        self.verify_named_continuity()
        fd = self._open_file_fd(relative, label)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise GateError(f"{label} final component is not a regular file")
            if before.st_nlink != 1:
                raise GateError(f"{label} must be a single-link regular file")
            raw = _read_fd_all(fd)
            after = os.fstat(fd)
            if _source_stat_identity(before) != _source_stat_identity(after):
                raise GateError(f"{label} changed while held open")
            if len(raw) != before.st_size:
                raise GateError(f"{label} held size differs from bytes read")
            snapshot = HeldRegularSnapshot(
                root_lease=self,
                relative=safe_relative(relative, label),
                label=label,
                fd=fd,
                identity=_source_stat_identity(before),
                raw=raw,
            )
            fd = -1
            return snapshot
        finally:
            if fd >= 0:
                os.close(fd)

    def close(self) -> None:
        if not self._closed:
            for fd in reversed(self._fds):
                os.close(fd)
            self._closed = True


@dataclass
class HeldRegularSnapshot:
    root_lease: HeldRootLease
    relative: str
    label: str
    fd: int
    identity: tuple[int, int, int, int, int, int, int]
    raw: bytes
    _closed: bool = False

    def verify_named_continuity(self, *, verify_root: bool = True) -> None:
        if self._closed:
            raise GateError(f"{self.label} snapshot is closed")
        if verify_root:
            self.root_lease.verify_named_continuity()
        held_before = os.fstat(self.fd)
        held_raw = _read_fd_all(self.fd)
        held_after = os.fstat(self.fd)
        if (
            _source_stat_identity(held_before) != self.identity
            or _source_stat_identity(held_after) != self.identity
            or held_raw != self.raw
        ):
            raise GateError(f"{self.label} held bytes changed")
        fresh_fd = self.root_lease._open_file_fd(self.relative, f"{self.label} continuity")
        try:
            fresh = os.fstat(fresh_fd)
            if _source_stat_identity(fresh) != self.identity:
                raise GateError(f"{self.label} named path no longer identifies held bytes")
        finally:
            os.close(fresh_fd)

    def close(self) -> None:
        if not self._closed:
            os.close(self.fd)
            self._closed = True


def _read_relative_regular_bytes(root: Path, relative: str, label: str) -> bytes:
    """Read one single-link regular file through a nofollow held root."""
    lease: HeldRootLease | None = None
    snapshot: HeldRegularSnapshot | None = None
    try:
        lease = HeldRootLease(root, label)
        snapshot = lease.open_regular(relative, label)
        snapshot.verify_named_continuity()
        return snapshot.raw
    finally:
        if snapshot is not None:
            snapshot.close()
        if lease is not None:
            lease.close()


def reject_internal_ancestor_symlinks(root: Path, relative: str, label: str) -> None:
    # Compatibility helper retained for explicit hostile tests.  The actual
    # source read below uses the same dirfd traversal and never reopens by path.
    _read_relative_regular_bytes(root, relative, label)


def load_json(path: Path, label: str) -> Any:
    raw = _read_regular_bytes(path)
    return strict_json_bytes(raw, label)


def strict_json_bytes(raw: bytes, label: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise GateError(f"{label} contains non-finite JSON constant {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise GateError(f"{label} contains duplicate JSON object key {key!r}")
            output[key] = value
        return output

    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    return value


def frozen_release_contract_reference() -> dict[str, Any]:
    reference_path = Path(__file__).with_name(FROZEN_RELEASE_CONTRACT_REFERENCE_NAME)
    raw = _read_regular_bytes(reference_path)
    if sha_bytes(raw) != FROZEN_RELEASE_CONTRACT_REFERENCE_SHA256:
        raise GateError("frozen-v8 release-contract reference SHA drift")
    value = strict_json_bytes(raw, "frozen-v8 release-contract reference")
    if type(value) is not dict or set(value) != RELEASE_CONTRACT_ROOT_KEYS or len(value) != 24:
        raise GateError("frozen-v8 release-contract reference root is not exact 24-key shape")
    return copy.deepcopy(value)


def _validate_contract_dynamic_value(value: Any, reference: Any, path: tuple[str, ...]) -> None:
    label = "release contract." + ".".join(path)
    if type(value) is not type(reference):
        raise GateError(f"{label} exact dynamic type drift")
    key = path[-1]
    if type(value) is int:
        if value <= 0:
            raise GateError(f"{label} must be a positive exact integer")
        return
    if type(value) is str:
        if not value or "\x00" in value:
            raise GateError(f"{label} must be a non-empty string")
        if key.endswith("_sha256"):
            require_sha(value, label)
        elif key.endswith("_path") or key in {"bundle_runtime_dir"}:
            if not Path(value).is_absolute():
                raise GateError(f"{label} must be absolute")
        elif key in {"generator_name", "panel_addendum_name"}:
            if PurePosixPath(value).name != value:
                raise GateError(f"{label} must be one basename")
        return
    raise GateError(f"{label} has no declared dynamic validator")


def _validate_contract_node(
    value: Any,
    reference: Any,
    path: tuple[str, ...] = (),
) -> None:
    if path in RELEASE_CONTRACT_DYNAMIC_FIELDS:
        _validate_contract_dynamic_value(value, reference, path)
        return
    label = "release contract" + ("." + ".".join(path) if path else "")
    if type(value) is not type(reference):
        raise GateError(f"{label} exact type drift")
    if isinstance(reference, dict):
        if set(value) != set(reference):
            raise GateError(f"{label} exact nested key-set drift")
        for key in reference:
            _validate_contract_node(value[key], reference[key], path + (key,))
        return
    if isinstance(reference, list):
        if len(value) != len(reference):
            raise GateError(f"{label} exact list length drift")
        for index, (actual_item, expected_item) in enumerate(zip(value, reference)):
            _validate_contract_node(actual_item, expected_item, path + (str(index),))
        return
    if value != reference:
        raise GateError(f"{label} exact frozen value drift")


def require_exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = sorted(value) if isinstance(value, Mapping) else type(value).__name__
        raise GateError(f"{label} exact key-set drift: {actual!r}")
    return value


def require_record(
    value: Any,
    label: str,
    *,
    row_count: int | None = None,
    public: bool = False,
) -> tuple[str, str]:
    expected_keys = {"path", "sha256"}
    if public:
        expected_keys.add("size_bytes")
    if row_count is not None:
        expected_keys.add("row_count")
    record = require_exact_keys(value, expected_keys, label)
    path = record.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise GateError(f"{label}.path is invalid")
    sha = require_sha(record.get("sha256"), f"{label}.sha256")
    if public:
        size = record.get("size_bytes")
        if type(size) is not int or size <= 0:
            raise GateError(f"{label}.size_bytes must be a positive exact integer")
    if row_count is not None:
        require_exact_int(record.get("row_count"), row_count, f"{label}.row_count")
    return path, sha


def _same_record(actual: Any, expected: Any, label: str) -> None:
    if not isinstance(actual, Mapping) or not isinstance(expected, Mapping) or dict(actual) != dict(expected):
        raise GateError(f"{label} record binding drift")


def _record_identity(value: Any, label: str) -> tuple[str, str, int | None]:
    if not isinstance(value, Mapping):
        raise GateError(f"{label} must be a record object")
    path = value.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise GateError(f"{label}.path is invalid")
    digest = require_sha(value.get("sha256"), f"{label}.sha256")
    size = value.get("size_bytes")
    if size is not None and (type(size) is not int or size <= 0):
        raise GateError(f"{label}.size_bytes must be a positive exact integer")
    return path, digest, size


def _same_file_identity(actual: Any, expected: Any, label: str) -> None:
    left = _record_identity(actual, f"{label}.actual")
    right = _record_identity(expected, f"{label}.expected")
    if left[:2] != right[:2]:
        raise GateError(f"{label} path/SHA binding drift")
    if left[2] is not None and right[2] is not None and left[2] != right[2]:
        raise GateError(f"{label} size binding drift")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _quantile_linear(values: Sequence[float], q: float) -> float:
    if not values:
        raise GateError("quantile input is empty")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _nonnegative_metrics(values: Sequence[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) or value < 0.0 for value in values):
        raise GateError("engineering-joint values must be finite and nonnegative")
    n = len(values)
    return {
        "joint_mae_fraction": sum(values) / n,
        "joint_rmse_fraction": math.sqrt(sum(value * value for value in values) / n),
        "p50_fraction": _quantile_linear(values, 0.50),
        "p90_fraction": _quantile_linear(values, 0.90),
        "p95_fraction": _quantile_linear(values, 0.95),
        "p99_fraction": _quantile_linear(values, 0.99),
        "maximum_fraction": max(values),
    }


def _signed_metrics(values: Sequence[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise GateError("signed metric values must be nonempty and finite")
    absolute = [abs(value) for value in values]
    n = len(values)
    return {
        "bias": sum(values) / n,
        "mae": sum(absolute) / n,
        "rmse": math.sqrt(sum(value * value for value in values) / n),
        "p50": _quantile_linear(absolute, 0.50),
        "p90": _quantile_linear(absolute, 0.90),
        "p95": _quantile_linear(absolute, 0.95),
        "p99": _quantile_linear(absolute, 0.99),
        "maximum": max(absolute),
    }


def _paired_target_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Mapping[str, Any]]]:
    if len(rows) != 29192:
        raise GateError("paired_feature_rows must contain exactly 29,192 rows")
    by_target: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise GateError(f"paired_feature_rows[{index}] must be an object")
        if set(row) != set(PAIRED_FEATURE_ROW_FIELDS):
            raise GateError(f"paired_feature_rows[{index}] exact field-set drift")
        target_id = row.get("target_id")
        feature = row.get("feature_key")
        if not isinstance(target_id, str) or not target_id:
            raise GateError(f"paired_feature_rows[{index}].target_id is invalid")
        if feature not in FEATURES or feature in by_target[target_id]:
            raise GateError(f"paired_feature_rows has invalid/duplicate feature for {target_id}")
        target = finite(row.get("target_value"), f"paired_feature_rows[{index}].target_value")
        proxy = finite(row.get("proxy_value"), f"paired_feature_rows[{index}].proxy_value")
        emx = finite(row.get("emx_value"), f"paired_feature_rows[{index}].emx_value")
        if feature in {"lp_nh", "ls_nh", "qmin", "k_abs"} and min(target, proxy, emx) < 0.0:
            raise GateError(f"paired_feature_rows[{index}] physical value is negative")
        if feature == "k_abs" and max(target, proxy, emx) > 1.0:
            raise GateError(f"paired_feature_rows[{index}] |K| lies outside [0,1]")
        if row.get("feature") != FEATURE_LABELS[feature] or row.get("unit") != UNITS[feature]:
            raise GateError(f"paired_feature_rows[{index}] feature label/unit drift")
        span = SPANS[str(feature)]
        _same_number(row.get("target_normalized_fraction"), target / span, "paired target normalized")
        _same_number(row.get("proxy_normalized_fraction"), proxy / span, "paired proxy normalized")
        _same_number(row.get("emx_normalized_fraction"), emx / span, "paired EMX normalized")
        _same_number(
            row.get("proxy_minus_emx_range_fraction"),
            (proxy - emx) / span,
            "paired proxy-minus-EMX normalized",
        )
        by_target[target_id][str(feature)] = row
    if not by_target:
        raise GateError("paired_feature_rows must be non-empty")
    for target_id, feature_rows in by_target.items():
        if set(feature_rows) != set(FEATURES):
            raise GateError(f"paired_feature_rows does not contain exactly four features for {target_id}")
        panels = {feature_rows[feature].get("panel_key") for feature in FEATURES}
        if len(panels) != 1 or next(iter(panels)) not in {"legacy_k_le_0p8", "extension_k_gt_0p8"}:
            raise GateError(f"paired_feature_rows panel drift for {target_id}")
        panel_key = str(next(iter(panels)))
        expected_label = next(label for _, key, label, _ in PANELS if key == panel_key)
        if any(feature_rows[feature].get("panel") != expected_label for feature in FEATURES):
            raise GateError(f"paired_feature_rows panel label drift for {target_id}")
        target_k_abs = finite(feature_rows["k_abs"].get("target_value"), f"{target_id}.target_k_abs")
        derived_panel = (
            "legacy_k_le_0p8" if abs(target_k_abs) <= 0.8 else "extension_k_gt_0p8"
        )
        if panel_key != derived_panel:
            raise GateError(
                f"paired_feature_rows panel is not derived from target |K| for {target_id}"
            )
    panel_counts = Counter(
        str(feature_rows["lp_nh"]["panel_key"]) for feature_rows in by_target.values()
    )
    if len(by_target) != 7298 or panel_counts != {
        "legacy_k_le_0p8": 5992,
        "extension_k_gt_0p8": 1306,
    }:
        raise GateError(
            "paired_feature_rows must contain exactly 7,298 unique targets with 5,992/1,306 panels"
        )
    return dict(by_target)


def derive_source_science_tables(
    paired_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Recompute producer feature36/exact-joint9/Q3 from the exact paired rows."""
    targets_by_id = _paired_target_rows(paired_rows)
    grouped: dict[str, list[dict[str, Mapping[str, Any]]]] = {
        "overall": list(targets_by_id.values()),
        "legacy_k_le_0p8": [],
        "extension_k_gt_0p8": [],
    }
    for feature_rows in targets_by_id.values():
        grouped[str(feature_rows["k_abs"]["panel_key"])].append(feature_rows)

    feature_output: list[dict[str, Any]] = []
    exact_joint_output: list[dict[str, Any]] = []
    q_output: list[dict[str, Any]] = []
    for panel_order, panel_key, panel_label, _ in PANELS:
        targets = grouped[panel_key]
        if len(targets) != _expected_panel_count(panel_key):
            raise GateError(f"recomputed panel denominator drift: {panel_key}")
        for scope, scope_label in CHAINS:
            components_by_target: list[list[float]] = []
            for rows in targets:
                components: list[float] = []
                for feature in FEATURES:
                    row = rows[feature]
                    target = finite(row["target_value"], f"{scope}.{feature}.target")
                    proxy = finite(row["proxy_value"], f"{scope}.{feature}.proxy")
                    emx = finite(row["emx_value"], f"{scope}.{feature}.emx")
                    error = {
                        "target_vs_proxy": proxy - target,
                        "target_vs_emx": emx - target,
                        "proxy_vs_emx": proxy - emx,
                    }[scope]
                    components.append(error / SPANS[feature])
                components_by_target.append(components)
            for feature_index, feature in enumerate(FEATURES):
                physical = [
                    components[feature_index] * SPANS[feature]
                    for components in components_by_target
                ]
                normalized = [components[feature_index] for components in components_by_target]
                raw_stats = _signed_metrics(physical)
                fixed_stats = _signed_metrics(normalized)
                feature_output.append(
                    {
                        "panel_order": panel_order,
                        "panel_key": panel_key,
                        "panel": panel_label,
                        "comparison_scope": scope,
                        "comparison_scope_label": scope_label,
                        "feature_key": feature,
                        "feature": FEATURE_LABELS[feature],
                        "unit": UNITS[feature],
                        "row_count": len(targets),
                        "metric_contract_version": SOURCE_METRIC_VERSION,
                        "bias_physical": raw_stats["bias"],
                        "mae_physical": raw_stats["mae"],
                        "rmse_physical": raw_stats["rmse"],
                        "bias_fixed_frame_fraction": fixed_stats["bias"],
                        "mae_fixed_frame_fraction": fixed_stats["mae"],
                        "rmse_fixed_frame_range_fraction": fixed_stats["rmse"],
                        "p50_physical": raw_stats["p50"],
                        "p90_physical": raw_stats["p90"],
                        "p95_physical": raw_stats["p95"],
                        "p99_physical": raw_stats["p99"],
                        "maximum_physical": raw_stats["maximum"],
                        "p50_fixed_frame_fraction": fixed_stats["p50"],
                        "p90_fixed_frame_fraction": fixed_stats["p90"],
                        "p95_fixed_frame_fraction": fixed_stats["p95"],
                        "p99_fixed_frame_fraction": fixed_stats["p99"],
                        "maximum_fixed_frame_fraction": fixed_stats["maximum"],
                    }
                )
            joint_values = [
                math.sqrt(sum(value * value for value in components) / len(FEATURES))
                for components in components_by_target
            ]
            joint_stats = _nonnegative_metrics(joint_values)
            exact_joint_output.append(
                {
                    "panel_order": panel_order,
                    "panel_key": panel_key,
                    "panel": panel_label,
                    "comparison_scope": scope,
                    "comparison_scope_label": scope_label,
                    "metric_variant": EXACT_Q_VARIANT,
                    "row_count": len(targets),
                    **joint_stats,
                }
            )
        shortfalls = [
            max(
                finite(rows["qmin"]["target_value"], "Q target")
                - finite(rows["qmin"]["emx_value"], "Q EMX"),
                0.0,
            )
            for rows in targets
        ]
        q_stats = _nonnegative_metrics(shortfalls)
        q_output.append(
            {
                "panel_order": panel_order,
                "panel_key": panel_key,
                "panel": panel_label,
                "row_count": len(targets),
                "q_target_met_fraction": sum(value == 0.0 for value in shortfalls) / len(targets),
                "q_shortfall_mae": q_stats["joint_mae_fraction"],
                "q_shortfall_rmse": q_stats["joint_rmse_fraction"],
                "q_shortfall_p50": q_stats["p50_fraction"],
                "q_shortfall_p90": q_stats["p90_fraction"],
                "q_shortfall_p95": q_stats["p95_fraction"],
                "q_shortfall_p99": q_stats["p99_fraction"],
                "q_shortfall_max": q_stats["maximum_fraction"],
            }
        )
    return feature_output, exact_joint_output, q_output


def _compare_recomputed_rows(
    actual: Any, expected: Sequence[Mapping[str, Any]], label: str
) -> None:
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise GateError(f"{label} row cardinality/order drift")
    for row_index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        if not isinstance(actual_row, Mapping) or set(actual_row) != set(expected_row):
            raise GateError(f"{label}[{row_index}] exact field-set drift")
        for field, expected_value in expected_row.items():
            actual_value = actual_row.get(field)
            if type(expected_value) is int:
                if type(actual_value) is not int or actual_value != expected_value:
                    raise GateError(f"{label}[{row_index}].{field} exact integer drift")
            elif type(expected_value) is float:
                _same_number(actual_value, float(expected_value), f"{label}[{row_index}].{field}")
            elif actual_value != expected_value:
                raise GateError(f"{label}[{row_index}].{field} differs from recomputation")


def validate_source_science_tables(payload: Mapping[str, Any]) -> None:
    expected_feature, expected_joint, expected_q = derive_source_science_tables(
        payload.get("paired_feature_rows")
    )
    _compare_recomputed_rows(
        payload.get("comparison_feature_metrics"), expected_feature, "comparison_feature_metrics"
    )
    _compare_recomputed_rows(payload.get("joint_metrics"), expected_joint, "joint_metrics")
    _compare_recomputed_rows(payload.get("q_metrics"), expected_q, "q_metrics")


def validate_stage_funnel(value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(STAGE_COUNT_ROWS):
        raise GateError("stage_counts must contain the exact ordered six-stage funnel")
    for index, (actual, expected) in enumerate(zip(value, STAGE_COUNT_ROWS)):
        actual = require_exact_keys(actual, set(STAGE_COUNT_FIELDS), f"stage_counts[{index}]")
        for field in STAGE_COUNT_FIELDS:
            require_exact_typed_value(
                actual[field], expected[field], f"stage_counts[{index}].{field}"
            )


def derive_primary_engineering_joint(
    paired_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    targets = _paired_target_rows(paired_rows)
    values_by_panel: dict[str, list[float]] = {key: [] for _, key, _, _ in PANELS}
    for feature_rows in targets.values():
        panel = str(feature_rows["lp_nh"]["panel_key"])
        components: list[float] = []
        for feature in FEATURES:
            row = feature_rows[feature]
            target = finite(row["target_value"], f"{feature}.target")
            emx = finite(row["emx_value"], f"{feature}.emx")
            error = max(target - emx, 0.0) if feature == "qmin" else emx - target
            components.append(error / SPANS[feature])
        joint = math.sqrt(math.fsum(value * value for value in components) / 4.0)
        values_by_panel["overall"].append(joint)
        values_by_panel[panel].append(joint)
    rows: list[dict[str, Any]] = []
    for order, key, label, _ in PANELS:
        values = values_by_panel[key]
        if not values:
            raise GateError(f"engineering-joint panel is empty: {key}")
        rows.append(
            {
                "panel_order": order,
                "panel_key": key,
                "panel": label,
                "comparison_scope": "target_vs_emx",
                "comparison_scope_label": "Target vs fresh EMX",
                "metric_variant": REPORT_PRIMARY_VARIANT,
                "adapter_derivation": "paired_rows_fixed_spans_q_floor_numpy_linear_v1",
                "row_count": len(values),
                **_nonnegative_metrics(values),
            }
        )
    return rows, values_by_panel


def _same_number(actual: Any, expected: float, label: str, tolerance: float = 1e-12) -> None:
    value = finite(actual, label)
    if not math.isclose(value, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise GateError(f"{label} does not match the paired-row recomputation")


def validate_and_alias_fixed_bins(
    rows: Sequence[Mapping[str, Any]], values_by_panel: Mapping[str, Sequence[float]]
) -> list[dict[str, Any]]:
    if not isinstance(rows, Sequence) or len(rows) != 30:
        raise GateError("fixed_bin_metrics must contain exactly 30 rows")
    indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise GateError(f"fixed_bin_metrics[{index}] must be an object")
        if set(row) != set(FIXED_BIN_METRIC_FIELDS):
            raise GateError(f"fixed_bin_metrics[{index}] exact field-set drift")
        if (
            row.get("comparison_scope") != "target_vs_emx"
            or row.get("comparison_scope_label") != "Target vs fresh EMX"
            or row.get("metric_variant") != PRIMARY_FIXED_BIN_VARIANT
        ):
            raise GateError("producer fixed bins must be target-vs-EMX stats-v2 Q-floor primary bins")
        key = row.get("panel_key")
        order = row.get("bin_order")
        if key not in values_by_panel or type(order) is not int or (str(key), order) in indexed:
            raise GateError("fixed-bin panel/order identity is invalid or duplicated")
        indexed[(str(key), order)] = row
    output: list[dict[str, Any]] = []
    for panel_order, panel_key, panel_label, _ in PANELS:
        values = values_by_panel[panel_key]
        for bin_order, (lower, upper) in enumerate(zip(FIXED_BINS[:-1], FIXED_BINS[1:])):
            source = indexed.get((panel_key, bin_order))
            if source is None:
                raise GateError(f"missing fixed bin {panel_key}:{bin_order}")
            count = sum(value >= lower and (value < upper or math.isinf(upper)) for value in values)
            require_exact_int(source.get("count"), count, f"fixed bin count {panel_key}:{bin_order}")
            require_exact_int(source.get("denominator"), len(values), f"fixed bin denominator {panel_key}:{bin_order}")
            _same_number(source.get("lower_fraction"), lower, "fixed-bin lower")
            if source.get("upper_fraction_or_inf") != ("inf" if math.isinf(upper) else upper):
                raise GateError("fixed-bin upper edge drift")
            expected_label = f"[{lower:g},{'inf' if math.isinf(upper) else format(upper, 'g')})"
            if source.get("bin_label") != expected_label:
                raise GateError("fixed-bin label drift")
            if source.get("is_overflow") is not math.isinf(upper):
                raise GateError("fixed-bin overflow flag drift")
            _same_number(source.get("fraction"), count / len(values), "fixed-bin fraction")
            if source.get("panel_order") != panel_order or source.get("panel") != panel_label:
                raise GateError("fixed-bin panel label/order drift")
            aliased = copy.deepcopy(dict(source))
            aliased["producer_metric_variant"] = PRIMARY_FIXED_BIN_VARIANT
            aliased["metric_variant"] = REPORT_PRIMARY_VARIANT
            output.append(aliased)
    if len(indexed) != len(output):
        raise GateError("unexpected extra producer fixed-bin rows")
    return output


def _scientific_projection(payload: Mapping[str, Any], *, output_form: bool) -> dict[str, Any]:
    projection = {
        key: copy.deepcopy(payload.get(key))
        for key in (
            "stage_counts",
            "comparison_feature_metrics",
            "joint_metrics",
            "q_metrics",
            "fixed_bin_metrics",
            "paired_feature_rows",
        )
    }
    if output_form:
        for row in projection["comparison_feature_metrics"]:
            row["metric_contract_version"] = SOURCE_METRIC_VERSION
            row.pop("producer_metric_contract_version", None)
        projection["joint_metrics"] = [
            row for row in projection["joint_metrics"] if row.get("adapter_derivation") is None
        ]
        for row in projection["fixed_bin_metrics"]:
            row["metric_variant"] = row.pop("producer_metric_variant")
    return projection


def _expected_panel_count(panel_key: str) -> int:
    return {"overall": 7298, "legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306}[panel_key]


def validate_producer_survivor_statement(value: Any) -> None:
    if type(value) is not str or value != PRODUCER_SURVIVOR_CONDITIONING_STATEMENT:
        raise GateError(
            "producer survivor statement must exactly match the frozen "
            "survivor-conditional producer contract"
        )


def validate_completion_contract(payload: Mapping[str, Any]) -> None:
    completion = require_exact_keys(
        payload.get("completion_contract"), COMPLETION_CONTRACT_KEYS,
        "completion_contract",
    )
    expected_lists = {
        "comparison_feature_metric_fields": list(FEATURE_METRIC_FIELDS),
        "joint_metric_fields": list(JOINT_METRIC_FIELDS),
        "q_metric_fields": list(Q_METRIC_FIELDS),
        "fixed_bin_metric_fields": list(FIXED_BIN_METRIC_FIELDS),
        "stage_count_fields": list(STAGE_COUNT_FIELDS),
        "paired_feature_row_fields": list(PAIRED_FEATURE_ROW_FIELDS),
    }
    for key, expected in expected_lists.items():
        if completion.get(key) != expected:
            raise GateError(f"completion_contract exact ordered field list drift: {key}")


def validate_release_record_closure(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], Mapping[str, Any]]:
    source_files = payload.get("source_files")
    if not isinstance(source_files, list) or len(source_files) != len(PRODUCER_TERMINAL_ROLES):
        raise GateError("producer source_files must contain the exact 17 terminal/manifest records")
    roles: dict[str, Mapping[str, Any]] = {}
    for position, record in enumerate(source_files):
        record = require_exact_keys(
            record, {"role", "path", "sha256"}, f"producer source_files[{position}]"
        )
        role = record.get("role")
        if not isinstance(role, str) or role in roles:
            raise GateError(f"producer source_files[{position}].role is invalid or duplicated")
        require_record(
            {"path": record.get("path"), "sha256": record.get("sha256")},
            f"producer source {role}",
        )
        roles[role] = record
    if set(roles) != PRODUCER_TERMINAL_ROLES:
        raise GateError(
            f"producer terminal/manifest role closure drift: "
            f"missing={sorted(PRODUCER_TERMINAL_ROLES-set(roles))}, "
            f"extra={sorted(set(roles)-PRODUCER_TERMINAL_ROLES)}"
        )

    release = payload.get("complete_emx_release_chain")
    expected_release_keys = (
        set(RELEASE_RECORD_BINDINGS.values())
        | {"launcher_preflight_terminals"}
        | set(RELEASE_FLAG_VALUES)
    )
    release = require_exact_keys(release, expected_release_keys, "complete_emx_release_chain")
    for source_role, release_key in RELEASE_RECORD_BINDINGS.items():
        source_record = {
            "path": roles[source_role].get("path"),
            "sha256": roles[source_role].get("sha256"),
        }
        require_record(
            release.get(release_key), f"complete release {release_key}", public=True
        )
        _same_file_identity(release.get(release_key), source_record, f"source/{release_key}")
    launchers = require_exact_keys(
        release.get("launcher_preflight_terminals"),
        LAUNCHER_PREFLIGHT_KEYS,
        "complete release launcher_preflight_terminals",
    )
    for key in sorted(LAUNCHER_PREFLIGHT_KEYS):
        release_record = launchers.get(key)
        require_record(release_record, f"complete release launcher {key}", public=True)
        source = roles[f"canonical_{key}"]
        _same_file_identity(
            release_record,
            {"path": source.get("path"), "sha256": source.get("sha256")},
            f"source/launcher {key}",
        )
    for key, expected in RELEASE_FLAG_VALUES.items():
        if release.get(key) != expected:
            raise GateError(f"complete release exact flag/semantic drift: {key}")
    return roles, release


def validate_table_cardinalities(payload: Mapping[str, Any], *, portable: bool = False) -> None:
    feature_rows = payload.get("comparison_feature_metrics")
    if not isinstance(feature_rows, list) or len(feature_rows) != 36:
        raise GateError("comparison_feature_metrics must contain exactly 36 rows")
    feature_seen: set[tuple[str, str, str]] = set()
    panel_by_key = {key: (order, label) for order, key, label, _ in PANELS}
    chain_by_key = {key: label for key, label in CHAINS}
    for index, row in enumerate(feature_rows):
        if not isinstance(row, Mapping):
            raise GateError(f"comparison_feature_metrics[{index}] must be an object")
        expected_fields = set(FEATURE_METRIC_FIELDS) | ({"producer_metric_contract_version"} if portable else set())
        if set(row) != expected_fields:
            raise GateError(f"comparison_feature_metrics[{index}] exact field-set drift")
        panel_key = row.get("panel_key")
        scope = row.get("comparison_scope")
        feature = row.get("feature_key")
        combo = (str(panel_key), str(scope), str(feature))
        if panel_key not in panel_by_key or scope not in chain_by_key or feature not in FEATURES or combo in feature_seen:
            raise GateError("feature metric panel/scope/feature identity is invalid or duplicated")
        feature_seen.add(combo)
        panel_order, panel_label = panel_by_key[str(panel_key)]
        expected_version = REPORT_METRIC_VERSION if portable else SOURCE_METRIC_VERSION
        if (
            row.get("panel_order") != panel_order
            or row.get("panel") != panel_label
            or row.get("comparison_scope_label") != chain_by_key[str(scope)]
            or row.get("feature") != FEATURE_LABELS[str(feature)]
            or row.get("unit") != UNITS[str(feature)]
            or row.get("row_count") != _expected_panel_count(str(panel_key))
            or row.get("metric_contract_version") != expected_version
        ):
            raise GateError("feature metric exact label/order/count/version drift")
        if portable and row.get("producer_metric_contract_version") != SOURCE_METRIC_VERSION:
            raise GateError("portable feature metric producer version binding drift")
    if len(feature_seen) != 36:
        raise GateError("feature metric cardinality closure failed")

    joint_rows = payload.get("joint_metrics")
    expected_joint_count = 12 if portable else 9
    if not isinstance(joint_rows, list) or len(joint_rows) != expected_joint_count:
        raise GateError(f"joint_metrics must contain exactly {expected_joint_count} rows")
    exact_seen: set[tuple[str, str]] = set()
    derived_seen: set[str] = set()
    for index, row in enumerate(joint_rows):
        if not isinstance(row, Mapping):
            raise GateError(f"joint_metrics[{index}] must be an object")
        is_derived = row.get("adapter_derivation") is not None
        expected_fields = set(JOINT_METRIC_FIELDS) | ({"adapter_derivation"} if is_derived else set())
        if set(row) != expected_fields:
            raise GateError(f"joint_metrics[{index}] exact field-set drift")
        panel_key = row.get("panel_key")
        if panel_key not in panel_by_key:
            raise GateError("joint metric panel key drift")
        panel_order, panel_label = panel_by_key[str(panel_key)]
        if row.get("panel_order") != panel_order or row.get("panel") != panel_label or row.get("row_count") != _expected_panel_count(str(panel_key)):
            raise GateError("joint metric panel/order/count drift")
        if row.get("adapter_derivation") is None:
            scope = row.get("comparison_scope")
            combo = (str(panel_key), str(scope))
            if scope not in chain_by_key or combo in exact_seen:
                raise GateError("exact-Q joint panel/scope is invalid or duplicated")
            exact_seen.add(combo)
            if row.get("comparison_scope_label") != chain_by_key[str(scope)] or row.get("metric_variant") != EXACT_Q_VARIANT:
                raise GateError("exact-Q joint label/variant drift")
        else:
            if not portable or panel_key in derived_seen:
                raise GateError("derived engineering-joint row is invalid or duplicated")
            derived_seen.add(str(panel_key))
            if (
                row.get("comparison_scope") != "target_vs_emx"
                or row.get("comparison_scope_label") != "Target vs fresh EMX"
                or row.get("metric_variant") != REPORT_PRIMARY_VARIANT
                or row.get("adapter_derivation") != "paired_rows_fixed_spans_q_floor_numpy_linear_v1"
            ):
                raise GateError("derived engineering-joint identity drift")
    if len(exact_seen) != 9 or (portable and derived_seen != set(panel_by_key)):
        raise GateError("joint metric cardinality closure failed")

    q_rows = payload.get("q_metrics")
    if not isinstance(q_rows, list) or len(q_rows) != 3:
        raise GateError("q_metrics must contain exactly three panel rows")
    q_seen: set[str] = set()
    for row in q_rows:
        if not isinstance(row, Mapping) or row.get("panel_key") not in panel_by_key or row.get("panel_key") in q_seen:
            raise GateError("Q metric panel is invalid or duplicated")
        if set(row) != set(Q_METRIC_FIELDS):
            raise GateError("Q metric exact field-set drift")
        panel_key = str(row["panel_key"])
        q_seen.add(panel_key)
        panel_order, panel_label = panel_by_key[panel_key]
        if row.get("panel_order") != panel_order or row.get("panel") != panel_label or row.get("row_count") != _expected_panel_count(panel_key):
            raise GateError("Q metric panel/order/count drift")
    if q_seen != set(panel_by_key):
        raise GateError("Q metric panel cardinality closure failed")


def validate_preregistration(payload: Any) -> None:
    if not isinstance(payload, Mapping) or payload.get("schema") != PREREG_SCHEMA or payload.get("status") != "FROZEN_BEFORE_ANY_FRESH_EMX_RESULT":
        raise GateError("statistics-v2 preregistration schema/status drift")
    if payload.get("feature_order") != list(FEATURES) or payload.get("fixed_frame_spans") != SPANS:
        raise GateError("statistics-v2 preregistration feature order/fixed spans drift")
    if payload.get("fixed_histogram_bins_fraction_of_full_scale") != [
        0.0, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, "INF"
    ]:
        raise GateError("statistics-v2 preregistration fixed bins drift")
    denominators = payload.get("denominators")
    expected_denominators = {
        "original_target_count": 10000,
        "analytical_pass_count": 7926,
        "analytical_fail_count": 2074,
        "cadence_pass_count": 7373,
        "cadence_fail_count": 553,
    }
    if not isinstance(denominators, Mapping) or any(denominators.get(key) != value for key, value in expected_denominators.items()):
        raise GateError("statistics-v2 preregistration denominator drift")
    row_metrics = payload.get("row_metrics")
    exact_formulas = {
        "q_floor_shortfall": "max(target_qmin - achieved_qmin, 0)",
        "q_floor_shortfall_normalized": "q_floor_shortfall / 20",
        "q_floor_pass": "achieved_qmin >= target_qmin",
        "joint_engineering_fixed_frame_error": "sqrt(mean([r_lp^2, r_ls^2, r_q_shortfall^2, r_k^2]))",
    }
    if not isinstance(row_metrics, Mapping) or any(row_metrics.get(key) != value for key, value in exact_formulas.items()):
        raise GateError("statistics-v2 preregistration Q-floor formula drift")
    inference = payload.get("inference_boundary")
    if not isinstance(inference, str) or "conditional descriptive statistics" not in inference or "no deployment-population CI" not in inference:
        raise GateError("statistics-v2 preregistration inference boundary drift")


def validate_canonical_terminal_payload(payload: Any, kind: str) -> Mapping[str, Any]:
    schema, stage, check_keys = TERMINAL_SPECS[kind]
    terminal = require_exact_keys(payload, TERMINAL_TOP_LEVEL_KEYS, f"{kind} terminal")
    if (
        terminal.get("schema") != schema
        or terminal.get("stage") != stage
        or terminal.get("overall_status") != "PASS"
        or terminal.get("result_rows_embedded") is not False
        or terminal.get("error") is not None
        or not isinstance(terminal.get("finished_utc"), str)
        or not terminal.get("finished_utc")
    ):
        raise GateError(f"{kind} canonical terminal schema/stage/PASS/result-free semantics drift")
    checks = require_exact_keys(terminal.get("checks"), set(check_keys), f"{kind} terminal checks")
    if not checks or any(value is not True for value in checks.values()):
        raise GateError(f"{kind} canonical terminal checks are not all exact true")
    if not isinstance(terminal.get("evidence"), Mapping):
        raise GateError(f"{kind} terminal evidence must be an object")
    return terminal


def validate_identity_receipt(payload: Any) -> None:
    expected_keys = {
        "finished_utc", "overall_status", "physical_error_metrics_accessed",
        "raw_byte_equality_is_not_a_gate", "rows", "schema", "summary",
        "timestamp_normalized_identity_is_primary_fail_closed_gate",
    }
    receipt = require_exact_keys(payload, expected_keys, "identity receipt")
    if (
        receipt.get("schema") != IDENTITY_RECEIPT_SCHEMA
        or receipt.get("overall_status") != "PASS"
        or receipt.get("physical_error_metrics_accessed") is not False
        or receipt.get("raw_byte_equality_is_not_a_gate") is not True
        or receipt.get("timestamp_normalized_identity_is_primary_fail_closed_gate") is not True
    ):
        raise GateError("identity receipt schema/PASS/result-blind gate drift")
    require_record(receipt.get("rows"), "identity receipt rows", row_count=7298)
    require_record(receipt.get("summary"), "identity receipt summary")


def _validate_record_map(value: Any, expected_keys: set[str], label: str) -> Mapping[str, Any]:
    records = require_exact_keys(value, expected_keys, label)
    for key in sorted(expected_keys):
        require_record(records.get(key), f"{label}.{key}")
    return records


def _require_generated_artifact_record(
    value: Any,
    label: str,
    *,
    family: str,
    row_count: int | None = None,
) -> Mapping[str, Any]:
    if family in {"stage07", "stage08"}:
        keys = {"exists", "path", "sha256", "size_bytes"}
        if row_count is not None:
            keys.add("row_count")
        record = require_exact_keys(value, keys, label)
        if record.get("exists") is not True:
            raise GateError(f"{label}.exists must be exact true")
        projected = {key: record[key] for key in keys if key != "exists"}
        if row_count == -1:
            if type(projected.get("row_count")) is not int or projected["row_count"] < 0:
                raise GateError(f"{label}.row_count must be a nonnegative exact integer")
            require_record(
                {key: value for key, value in projected.items() if key != "row_count"},
                label,
                public=True,
            )
        else:
            require_record(projected, label, public=True, row_count=row_count)
        return record
    if family == "three_chain":
        require_record(value, label, row_count=row_count)
        return value
    if family == "full_band_v3":
        require_record(value, label, public=True, row_count=row_count)
        return value
    raise GateError(f"unknown generated artifact record family: {family}")


def validate_manifest_payload(payload: Any, kind: str) -> None:
    if kind == "stage07_manifest":
        manifest = require_exact_keys(
            payload, {"schema", "generated_utc", "script", "artifacts"}, "Stage07 manifest"
        )
        if manifest.get("schema") != STAGE07_MANIFEST_SCHEMA:
            raise GateError("Stage07 manifest schema drift")
        if not isinstance(manifest.get("generated_utc"), str) or not manifest["generated_utc"]:
            raise GateError("Stage07 manifest generated_utc is invalid")
        _require_generated_artifact_record(
            manifest.get("script"), "Stage07 manifest script", family="stage07"
        )
        artifacts = require_exact_keys(
            manifest.get("artifacts"), MANIFEST_ARTIFACT_KEYS[kind],
            "Stage07 manifest artifacts",
        )
        for key in sorted(MANIFEST_ARTIFACT_KEYS[kind]):
            _require_generated_artifact_record(
                artifacts[key], f"Stage07 manifest artifacts.{key}", family="stage07"
            )
        return
    if kind == "stage08_manifest":
        manifest = require_exact_keys(payload, {"schema", "artifacts"}, "Stage08 manifest")
        if manifest.get("schema") != STAGE08_MANIFEST_SCHEMA:
            raise GateError("Stage08 manifest schema drift")
        artifacts = require_exact_keys(
            manifest.get("artifacts"), MANIFEST_ARTIFACT_KEYS[kind],
            "Stage08 manifest artifacts",
        )
        row_counts = {
            "v2_rows": 7298,
            "fixed_histogram_counts": -1,
            "fixed_secondary_percent_histogram_counts": -1,
        }
        for key in sorted(MANIFEST_ARTIFACT_KEYS[kind]):
            _require_generated_artifact_record(
                artifacts[key], f"Stage08 manifest artifacts.{key}", family="stage08",
                row_count=row_counts.get(key),
            )
        return
    if kind == "three_chain_manifest":
        manifest = require_exact_keys(
            payload, {"schema", "overall_status", "artifacts"}, "three-chain manifest"
        )
        if manifest.get("schema") != THREE_CHAIN_MANIFEST_SCHEMA or manifest.get("overall_status") != "PASS":
            raise GateError("three-chain manifest schema/PASS drift")
        artifacts = require_exact_keys(
            manifest.get("artifacts"), MANIFEST_ARTIFACT_KEYS[kind],
            "three-chain manifest artifacts",
        )
        for key in sorted(MANIFEST_ARTIFACT_KEYS[kind]):
            _require_generated_artifact_record(
                artifacts[key], f"three-chain manifest artifacts.{key}", family="three_chain"
            )
        return
    if kind == "full_band_v3_manifest":
        manifest = require_exact_keys(
            payload,
            {"schema", "generated_utc", "inputs", "runtime_sources", "outputs"},
            "full-band-v3 manifest",
        )
        if manifest.get("schema") != FULL_BAND_MANIFEST_SCHEMA:
            raise GateError("full-band-v3 manifest schema drift")
        if not isinstance(manifest.get("generated_utc"), str) or not manifest["generated_utc"]:
            raise GateError("full-band-v3 manifest generated_utc is invalid")
        for key, exact_roles in (
            ("inputs", FULL_MANIFEST_INPUT_KEYS),
            ("runtime_sources", FULL_MANIFEST_RUNTIME_KEYS),
        ):
            records = require_exact_keys(
                manifest.get(key), exact_roles, f"full-band-v3 manifest {key}"
            )
            for name in sorted(exact_roles):
                _require_generated_artifact_record(
                    records[name], f"full-band-v3 manifest {key}.{name}",
                    family="full_band_v3",
                )
        outputs = require_exact_keys(
            manifest.get("outputs"), MANIFEST_ARTIFACT_KEYS[kind],
            "full-band-v3 manifest outputs",
        )
        for key in sorted(MANIFEST_ARTIFACT_KEYS[kind]):
            _require_generated_artifact_record(
                outputs[key], f"full-band-v3 manifest outputs.{key}",
                family="full_band_v3",
            )
        return
    raise GateError(f"unknown manifest kind: {kind}")


def validate_full_band_internal(payload: Any, release: Mapping[str, Any]) -> None:
    keys = {
        "schema", "overall_status", "finished_utc", "expected_count", "audited_count",
        "structural_execution_pass_count", "diagnostic_flag_candidate_count_nonfiltering",
        "method_preregistration", "identity_terminal_receipt", "stage06_terminal_receipt",
        "stage08_terminal_receipt", "summary", "rows", "fixed_histograms", "plot",
        "manifest", "candidate_inclusion_changed",
        "stage07_08_primary_15ghz_statistics_changed", "diagnostic_only_nonfiltering",
        "panel_schema_only_remediation",
    }
    internal = require_exact_keys(payload, keys, "full-band-v3 internal terminal")
    if (
        internal.get("schema") != FULL_BAND_INTERNAL_SCHEMA
        or internal.get("overall_status") != "PASS"
        or internal.get("expected_count") != 7298
        or internal.get("audited_count") != 7298
        or internal.get("structural_execution_pass_count") != 7298
        or type(internal.get("diagnostic_flag_candidate_count_nonfiltering")) is not int
        or not 0 <= internal["diagnostic_flag_candidate_count_nonfiltering"] <= 7298
        or internal.get("candidate_inclusion_changed") is not False
        or internal.get("stage07_08_primary_15ghz_statistics_changed") is not False
        or internal.get("diagnostic_only_nonfiltering") is not True
        or internal.get("panel_schema_only_remediation") is not True
        or not isinstance(internal.get("finished_utc"), str)
        or not internal["finished_utc"]
    ):
        raise GateError("full-band-v3 internal terminal schema/PASS/count/nonfiltering drift")
    for key in (
        "method_preregistration", "identity_terminal_receipt", "stage06_terminal_receipt",
        "stage08_terminal_receipt", "summary", "fixed_histograms", "plot", "manifest",
    ):
        require_record(internal.get(key), f"full-band-v3 internal {key}", public=True)
    require_record(
        internal.get("rows"), "full-band-v3 internal rows", row_count=7298, public=True
    )
    _same_file_identity(
        internal.get("manifest"), release.get("full_band_v3_manifest"),
        "full-band-v3 internal/release manifest",
    )


def validate_release_contract_payload(value: Any) -> Mapping[str, Any]:
    contract = require_exact_keys(value, RELEASE_CONTRACT_ROOT_KEYS, "release contract")
    if len(contract) != 24:
        raise GateError("release contract must contain exactly 24 root keys")
    reference = frozen_release_contract_reference()
    _validate_contract_node(contract, reference)
    validate_exact_count_types(contract, "release contract")
    if (
        contract.get("schema") != RELEASE_CONTRACT_SCHEMA
        or contract.get("status") != PREPARED_RESULT_FREE_STATUS
        or contract.get("deployment_authorized") is not False
        or contract.get("execution_authorized") is not False
        or contract.get("independent_review_required") is not True
        or contract.get("expected_count") != 7298
        or contract.get("expected_original_denominator") != 10000
        or contract.get("expected_panel_counts")
        != {"legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306}
    ):
        raise GateError("release contract schema/status/result-free/count semantics drift")
    watcher = require_exact_keys(
        contract.get("active_watcher"),
        {
            "allowed_executable_realpaths", "expected_host", "expected_pid",
            "expected_stage06_supervisor_pid", "expected_script_path",
            "expected_script_sha256", "launch_receipt_path", "launch_receipt_sha256",
            "launch_receipt_schema", "maximum_launch_receipt_after_proc_start_seconds",
            "process_identity_algorithm", "require_exact_cmdline", "require_pidfd",
        },
        "release contract active_watcher",
    )
    if (
        watcher.get("allowed_executable_realpaths") != ["/bin/bash", "/usr/bin/bash"]
        or watcher.get("process_identity_algorithm") != WATCHER_PROCESS_IDENTITY_ALGORITHM
        or watcher.get("maximum_launch_receipt_after_proc_start_seconds") != 120
        or watcher.get("require_exact_cmdline") is not True
        or watcher.get("require_pidfd") is not True
    ):
        raise GateError("release contract watcher frozen identity semantics drift")
    for key in ("expected_pid", "expected_stage06_supervisor_pid"):
        if type(watcher.get(key)) is not int or watcher[key] <= 0:
            raise GateError(f"release contract watcher {key} is invalid")
    for key in ("expected_script_path", "launch_receipt_path"):
        if not isinstance(watcher.get(key), str) or not Path(watcher[key]).is_absolute():
            raise GateError(f"release contract watcher {key} must be absolute")
    for key in ("expected_script_sha256", "launch_receipt_sha256"):
        require_sha(watcher.get(key), f"release contract watcher {key}")

    runtime = require_exact_keys(
        contract.get("runtime"),
        {
            "python_path", "python_sha256_must_be_supplied_by_fresh_independent_go",
            "dependency_manifest_path", "private_site_packages_root", "required_distributions",
            "isolated_flags", "pre_auth_imports_standard_library_only",
            "third_party_imports_after_exact_tree_validation_only",
            "root_dirfd_inode_lease_required_through_generator_exit",
            "child_import_root_must_be_proc_self_fd",
            "post_import_origin_version_and_bytes_revalidation_required",
        },
        "release contract runtime",
    )
    if (
        runtime.get("isolated_flags") != ["-I", "-B", "-S"]
        or runtime.get("required_distributions") != ["matplotlib", "numpy"]
        or any(
            runtime.get(key) is not True
            for key in (
                "python_sha256_must_be_supplied_by_fresh_independent_go",
                "pre_auth_imports_standard_library_only",
                "third_party_imports_after_exact_tree_validation_only",
                "root_dirfd_inode_lease_required_through_generator_exit",
                "child_import_root_must_be_proc_self_fd",
                "post_import_origin_version_and_bytes_revalidation_required",
            )
        )
    ):
        raise GateError("release contract runtime isolation semantics drift")
    for key in ("python_path", "dependency_manifest_path", "private_site_packages_root"):
        if not isinstance(runtime.get(key), str) or not Path(runtime[key]).is_absolute():
            raise GateError(f"release contract runtime {key} must be absolute")
    go_policy = require_exact_keys(
        contract.get("independent_go"),
        {
            "required_schema", "required_status", "must_bind_bundle_manifest_sha256",
            "must_bind_contract_sha256", "must_bind_runtime_python_sha256",
            "must_bind_runtime_dependency_manifest_sha256_and_root_digest",
            "must_bind_watcher_process_start_identity", "receipt_present_in_prepared_bundle",
        },
        "release contract independent_go",
    )
    if (
        go_policy.get("required_schema") != INDEPENDENT_GO_SCHEMA
        or go_policy.get("required_status") != INDEPENDENT_GO_STATUS
        or go_policy.get("receipt_present_in_prepared_bundle") is not False
        or any(
            go_policy.get(key) is not True
            for key in (
                "must_bind_bundle_manifest_sha256", "must_bind_contract_sha256",
                "must_bind_runtime_python_sha256",
                "must_bind_runtime_dependency_manifest_sha256_and_root_digest",
                "must_bind_watcher_process_start_identity",
            )
        )
    ):
        raise GateError("release contract independent-GO policy drift")
    return contract


def validate_bundle_manifest_payload(value: Any) -> Mapping[str, Any]:
    manifest = require_exact_keys(
        value,
        {
            "bundle_role", "created_utc", "execution_authorized", "file_count", "files",
            "manifest_self_inclusion", "schema", "status", "unhashed_closure_files",
        },
        "release bundle manifest",
    )
    if (
        manifest.get("schema") != BUNDLE_MANIFEST_SCHEMA
        or manifest.get("status") != PREPARED_RESULT_FREE_STATUS
        or manifest.get("bundle_role") != "RESULT_FREE_PREPARED_CANDIDATE_NOT_LAUNCH_AUTHORITY"
        or manifest.get("execution_authorized") is not False
        or manifest.get("manifest_self_inclusion") is not False
        or not isinstance(manifest.get("created_utc"), str)
        or not manifest["created_utc"]
    ):
        raise GateError("release bundle manifest result-free authority semantics drift")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or manifest.get("file_count") != len(files):
        raise GateError("release bundle manifest file count drift")
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(files):
        record = require_exact_keys(
            record, {"relative_path", "role", "sha256", "size_bytes"},
            f"release bundle manifest files[{index}]",
        )
        relative = safe_relative(record.get("relative_path"), f"bundle file {index}")
        role = record.get("role")
        if not isinstance(role, str) or not role or (relative, role) in seen:
            raise GateError("release bundle manifest file identity is invalid/duplicated")
        seen.add((relative, role))
        require_sha(record.get("sha256"), f"bundle file {index} SHA")
        if type(record.get("size_bytes")) is not int or record["size_bytes"] <= 0:
            raise GateError(f"bundle file {index} size is invalid")
    if manifest.get("unhashed_closure_files") != [
        "BUNDLE_MANIFEST.json", "PREPARED_RESULT_FREE_RECEIPT.json",
        "PREPARED_VALIDATION_OUTPUT.json", "SHA256SUMS",
    ]:
        raise GateError("release bundle manifest unhashed closure drift")
    return manifest


def validate_runtime_manifest_payload(
    value: Any, *, expected_root_digest: str, expected_site_packages_root: str
) -> Mapping[str, Any]:
    manifest = require_exact_keys(
        value,
        {"distributions", "exact_file_set", "files", "root_digest", "schema",
         "site_packages_root", "status"},
        "runtime dependency manifest",
    )
    if (
        manifest.get("schema") != RUNTIME_MANIFEST_SCHEMA
        or manifest.get("status") != "FROZEN_RESULT_FREE_RUNTIME_IDENTITY"
        or manifest.get("exact_file_set") is not True
        or manifest.get("site_packages_root") != expected_site_packages_root
    ):
        raise GateError("runtime dependency manifest schema/status/root semantics drift")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise GateError("runtime dependency manifest files are empty")
    seen: set[str] = set()
    digest_lines: list[str] = []
    for index, item in enumerate(files):
        item = require_exact_keys(
            item, {"mode", "relative_path", "sha256", "size_bytes"},
            f"runtime dependency files[{index}]",
        )
        relative = safe_relative(item.get("relative_path"), f"runtime file {index}")
        if relative in seen:
            raise GateError(f"duplicate runtime dependency record: {relative}")
        seen.add(relative)
        digest = require_sha(item.get("sha256"), f"runtime file {relative} SHA")
        size = item.get("size_bytes")
        mode = item.get("mode")
        if type(size) is not int or size <= 0 or not isinstance(mode, str) or re.fullmatch(r"0[0-7]{3}", mode) is None:
            raise GateError(f"runtime file {relative} size/mode drift")
        digest_lines.append(f"{relative}\0{digest}\0{size}\0{mode}\n")
    root_digest = hashlib.sha256("".join(sorted(digest_lines)).encode("utf-8")).hexdigest()
    if manifest.get("root_digest") != root_digest or root_digest != expected_root_digest:
        raise GateError("runtime dependency manifest root digest drift")
    distributions = require_exact_keys(
        manifest.get("distributions"), {"matplotlib", "numpy"},
        "runtime dependency distributions",
    )
    for name in ("matplotlib", "numpy"):
        distribution = require_exact_keys(
            distributions[name],
            {"distribution_record_relative_path", "import_relative_path", "version"},
            f"runtime dependency distribution {name}",
        )
        if not isinstance(distribution.get("version"), str) or not distribution["version"]:
            raise GateError(f"runtime dependency distribution {name} version is invalid")
        for key in ("distribution_record_relative_path", "import_relative_path"):
            if distribution.get(key) not in seen:
                raise GateError(f"runtime dependency distribution {name}.{key} is not listed")
    return manifest


def validate_process_identity(
    value: Any, authorization: Mapping[str, Any], contract: Mapping[str, Any]
) -> Mapping[str, Any]:
    process = require_exact_keys(value, PROCESS_IDENTITY_KEYS, "authorization watcher process identity")
    watcher = contract["active_watcher"]
    if process.get("algorithm") != watcher["process_identity_algorithm"]:
        raise GateError("authorization watcher process algorithm drift")
    if not isinstance(process.get("boot_id"), str) or not process["boot_id"]:
        raise GateError("authorization watcher process boot_id is invalid")
    for key in ("cmdline_sha256", "exe_sha256", "launch_receipt_sha256", "script_sha256"):
        require_sha(process.get(key), f"authorization watcher process {key}")
    for key in (
        "exe_device", "exe_inode", "exe_size_bytes", "pid", "ppid", "proc_starttime_ticks",
        "proc_start_unix_ns", "stage06_supervisor_pid",
    ):
        if type(process.get(key)) is not int or process[key] <= 0:
            raise GateError(f"authorization watcher process {key} must be positive exact integer")
    if type(process.get("uid")) is not int or process["uid"] < 0:
        raise GateError("authorization watcher process uid must be nonnegative exact integer")
    delta = process.get("launch_receipt_after_proc_start_ns")
    if type(delta) is not int or not 0 <= delta <= MAXIMUM_LAUNCH_RECEIPT_AFTER_PROC_START_NS:
        raise GateError("authorization watcher process launch receipt delta is invalid")
    if process.get("exe_realpath") not in watcher["allowed_executable_realpaths"]:
        raise GateError("authorization watcher process exe_realpath is outside frozen allowlist")
    argv = process.get("argv")
    if (
        not isinstance(argv, list) or len(argv) != 2 or type(argv[0]) is not str
        or Path(argv[0]).name != "bash" or argv[1] != watcher["expected_script_path"]
    ):
        raise GateError("authorization watcher process argv is not exact bash plus frozen watcher")
    canonical_cmdline = json.dumps(argv, ensure_ascii=False, separators=(",", ":")).encode()
    if process.get("cmdline_sha256") != hashlib.sha256(canonical_cmdline).hexdigest():
        raise GateError("authorization watcher process cmdline SHA drift")
    if (
        process.get("pid") != watcher["expected_pid"]
        or process.get("stage06_supervisor_pid") != watcher["expected_stage06_supervisor_pid"]
    ):
        raise GateError("authorization watcher process frozen PID/supervisor drift")
    watcher_script = require_record(
        authorization.get("watcher_script"), "authorization watcher_script", public=True
    )
    launch_receipt = require_record(
        authorization.get("watcher_launch_receipt"), "authorization watcher_launch_receipt",
        public=True,
    )
    if (
        watcher_script != (watcher["expected_script_path"], watcher["expected_script_sha256"])
        or launch_receipt != (watcher["launch_receipt_path"], watcher["launch_receipt_sha256"])
    ):
        raise GateError("authorization watcher script/launch record differs from frozen contract")
    if process.get("script_sha256") != watcher_script[1]:
        raise GateError("authorization watcher process/script SHA binding drift")
    if process.get("launch_receipt_sha256") != launch_receipt[1]:
        raise GateError("authorization watcher process/launch-receipt SHA binding drift")
    return process


def validate_watcher_launch_receipt_payload(
    value: Any, contract: Mapping[str, Any], process: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Replicate the frozen-v8 launch-receipt semantic and time-delta gate."""
    launch = require_exact_keys(
        value, WATCHER_LAUNCH_RECEIPT_KEYS, "watcher launch receipt"
    )
    watcher = contract["active_watcher"]
    if (
        launch.get("schema") != watcher["launch_receipt_schema"]
        or launch.get("status_at_receipt") != "WAITING_FOR_STAGE06"
        or launch.get("host") != watcher["expected_host"]
        or launch.get("pid") != process["pid"]
        or launch.get("stage06_supervisor_pid") != process["stage06_supervisor_pid"]
        or launch.get("expected_physical_count") != 7298
        or launch.get("v2_is_primary_for_final_report") is not True
        or launch.get("v1_preserved_as_superseded_physical_evidence") is not True
        or launch.get("no_clobber") is not True
    ):
        raise GateError("watcher launch receipt schema/status/host/process/count semantics drift")
    for key, section, contract_key in (
        ("physical_statistics_script_sha256", "stage07", "generator_sha256"),
        ("v2_statistics_script_sha256", "stage08", "generator_sha256"),
        ("preregistration_sha256", "stage08", "preregistration_sha256"),
    ):
        digest = require_sha(launch.get(key), f"watcher launch receipt {key}")
        if digest != contract[section][contract_key]:
            raise GateError(f"watcher launch receipt {key} differs from release contract")
    launch_script = require_exact_keys(
        launch.get("watcher_script"), {"path", "sha256"},
        "watcher launch receipt script",
    )
    require_record(launch_script, "watcher launch receipt script")
    if (
        launch_script["path"] != watcher["expected_script_path"]
        or launch_script["sha256"] != watcher["expected_script_sha256"]
    ):
        raise GateError("watcher launch receipt script differs from release contract")
    started_text = launch.get("started_utc")
    if type(started_text) is not str:
        raise GateError("watcher launch receipt started_utc must be text")
    try:
        started = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError("watcher launch receipt started_utc is invalid ISO-8601") from exc
    if started.tzinfo is None:
        raise GateError("watcher launch receipt started_utc must carry an offset")
    recomputed_delta_ns = int(started.timestamp() * 1_000_000_000) - process["proc_start_unix_ns"]
    if recomputed_delta_ns != process["launch_receipt_after_proc_start_ns"]:
        raise GateError("watcher launch receipt/process start delta recomputation drift")
    return launch


def validate_independent_go_payload(
    value: Any,
    *,
    contract: Mapping[str, Any],
    authorization: Mapping[str, Any],
    process_identity: Mapping[str, Any],
) -> Mapping[str, Any]:
    go = require_exact_keys(value, GO_KEYS, "independent review GO")
    if (
        go.get("schema") != contract["independent_go"]["required_schema"]
        or go.get("status") != contract["independent_go"]["required_status"]
        or go.get("exact_resume_only") is not True
        or go.get("transport_authorized") is not False
        or go.get("training_authorized") is not False
        or go.get("scientific_release_authorized") is not False
        or not isinstance(go.get("reviewer"), str) or not go["reviewer"]
        or not isinstance(go.get("reviewed_utc"), str) or not go["reviewed_utc"]
    ):
        raise GateError("independent review GO schema/status/authority semantics drift")
    record_bindings = {
        "bundle_manifest_sha256": "bundle_manifest",
        "contract_sha256": "contract",
        "runtime_dependency_manifest_sha256": "runtime_dependency_manifest",
        "runtime_python_sha256": "runtime_interpreter",
    }
    for go_key, auth_key in record_bindings.items():
        _, expected_sha = require_record(
            authorization.get(auth_key), f"authorization {auth_key}", public=True
        )
        if go.get(go_key) != expected_sha:
            raise GateError(f"independent review GO {go_key} binding drift")
    runtime = contract["runtime"]
    if (
        go.get("runtime_dependency_manifest_path") != runtime["dependency_manifest_path"]
        or go.get("runtime_python_path") != runtime["python_path"]
        or go.get("runtime_site_packages_root") != runtime["private_site_packages_root"]
        or go.get("runtime_dependency_root_digest")
        != authorization.get("runtime_dependency_root_digest")
        or go.get("watcher_process_identity") != process_identity
    ):
        raise GateError("independent review GO runtime/process projection binding drift")
    for key in (
        "bundle_manifest_sha256", "contract_sha256", "runtime_dependency_manifest_sha256",
        "runtime_dependency_root_digest", "runtime_python_sha256",
    ):
        require_sha(go.get(key), f"independent review GO {key}")
    return go


def _read_bound_public_bytes(
    read_ref: Any, path: str, sha: str, size_bytes: Any, label: str,
) -> bytes:
    if type(size_bytes) is not int or size_bytes <= 0:
        raise GateError(f"{label} public size_bytes must be a positive exact integer")
    raw = read_ref(path, sha, label)
    if sha_bytes(raw) != sha:
        raise GateError(f"{label} SHA mismatch after bound read")
    if len(raw) != size_bytes:
        raise GateError(
            f"{label} public size_bytes differs from held-FD bytes: "
            f"declared={size_bytes}, actual={len(raw)}"
        )
    return raw


def _load_bound_json(
    read_ref: Any, path: str, sha: str, size_bytes: Any, label: str,
) -> Any:
    raw = _read_bound_public_bytes(read_ref, path, sha, size_bytes, label)
    value = strict_json_bytes(raw, label)
    validate_exact_count_types(value, label)
    return value


def validate_bound_source_documents(
    payload: Mapping[str, Any], read_ref: Any,
) -> list[tuple[str, str, str]]:
    """Validate the exact frozen-v8 control graph and return extra local sources."""
    roles, release = validate_release_record_closure(payload)

    reverse_release = {role: key for role, key in RELEASE_RECORD_BINDINGS.items()}

    def source_public_record(role: str) -> Mapping[str, Any]:
        if role in reverse_release:
            return release[reverse_release[role]]
        launcher_key = role.removeprefix("canonical_")
        if launcher_key in LAUNCHER_PREFLIGHT_KEYS:
            return release["launcher_preflight_terminals"][launcher_key]
        raise GateError(f"producer source role has no public release record: {role}")

    def source_ref(role: str) -> tuple[str, str, int]:
        record = source_public_record(role)
        path, digest = require_record(record, f"producer source {role}", public=True)
        return path, digest, record["size_bytes"]

    def load_terminal(kind: str, role: str) -> Mapping[str, Any]:
        path, digest, size_bytes = source_ref(role)
        return validate_canonical_terminal_payload(
            _load_bound_json(
                read_ref, path, digest, size_bytes, f"{kind} canonical terminal"
            ),
            kind,
        )

    stage07 = load_terminal("stage07", "canonical_stage07")
    stage08 = load_terminal("stage08", "canonical_stage08")
    controller = load_terminal("controller", "canonical_controller")
    three = load_terminal("three_chain", "canonical_three_chain")
    full = load_terminal("full_band_v3", "canonical_full_band_v3")
    resume_preflight = load_terminal("preflight", "canonical_resume_preflight_terminal")
    three_preflight = load_terminal(
        "three_chain_preflight", "canonical_three_chain_preflight_terminal"
    )
    full_preflight = load_terminal(
        "full_band_v3_preflight", "canonical_full_band_v3_preflight_terminal"
    )

    launcher_payloads: dict[str, Mapping[str, Any]] = {}
    launcher_evidence: dict[str, Mapping[str, Any]] = {}
    for launcher_key in sorted(LAUNCHER_PREFLIGHT_KEYS):
        terminal = load_terminal("launcher_preflight", f"canonical_{launcher_key}")
        evidence = require_exact_keys(
            terminal.get("evidence"), LAUNCHER_EVIDENCE_KEYS,
            f"launcher {launcher_key} evidence",
        )
        for evidence_key in sorted(LAUNCHER_EVIDENCE_KEYS):
            require_record(
                evidence[evidence_key], f"launcher {launcher_key}.{evidence_key}",
                public=True,
            )
        for evidence_key in ("common", "controller"):
            record = evidence[evidence_key]
            _read_bound_public_bytes(
                read_ref,
                record["path"],
                record["sha256"],
                record["size_bytes"],
                f"launcher {launcher_key}.{evidence_key}",
            )
        expected_controller = launcher_key.removeprefix("launcher_preflight__")
        if PurePosixPath(str(evidence["controller"]["path"])).name != expected_controller:
            raise GateError(f"launcher {launcher_key} controller basename drift")
        launcher_payloads[launcher_key] = terminal
        launcher_evidence[launcher_key] = evidence
    baseline_launcher = launcher_evidence[sorted(LAUNCHER_PREFLIGHT_KEYS)[0]]
    for launcher_key, evidence in launcher_evidence.items():
        for shared_key in (
            "bundle_manifest", "common", "contract", "independent_review_go",
            "runtime_dependency_manifest",
        ):
            _same_file_identity(
                evidence[shared_key], baseline_launcher[shared_key],
                f"launcher shared {shared_key} for {launcher_key}",
            )

    stage07_ref = release["stage07_terminal"]
    stage08_ref = release["stage08_terminal"]
    stage07_evidence = require_exact_keys(
        stage07.get("evidence"), {"summary", "rows", "manifest"}, "Stage07 evidence"
    )
    require_record(stage07_evidence["summary"], "Stage07 summary", public=True)
    require_record(stage07_evidence["rows"], "Stage07 rows", row_count=7298, public=True)
    require_record(stage07_evidence["manifest"], "Stage07 manifest", public=True)
    _same_file_identity(stage07_evidence["manifest"], release["stage07_manifest"], "Stage07 manifest")

    stage08_evidence = require_exact_keys(
        stage08.get("evidence"),
        {"summary", "rows", "manifest", "generator", "preregistration",
         "legacy_watch_terminal", "upstream_stage07_terminal", "identity_gate"},
        "Stage08 evidence",
    )
    for key in (
        "summary", "manifest", "generator", "preregistration", "legacy_watch_terminal",
        "upstream_stage07_terminal",
    ):
        require_record(stage08_evidence[key], f"Stage08 {key}", public=True)
    require_record(stage08_evidence["rows"], "Stage08 rows", row_count=7298, public=True)
    _same_file_identity(stage08_evidence["manifest"], release["stage08_manifest"], "Stage08 manifest")
    _same_file_identity(stage08_evidence["upstream_stage07_terminal"], stage07_ref, "Stage08 upstream Stage07")

    identity_gate = require_exact_keys(
        stage08_evidence["identity_gate"],
        {"receipt", "summary", "rows", "auditor", "target_id_set_sha256",
         "candidate_id_set_sha256"},
        "Stage08 identity gate",
    )
    require_record(identity_gate["receipt"], "Stage08 identity receipt", public=True)
    require_record(identity_gate["summary"], "Stage08 identity summary", public=True)
    require_record(identity_gate["rows"], "Stage08 identity rows", row_count=7298, public=True)
    require_record(identity_gate["auditor"], "Stage08 identity auditor", public=True)
    require_sha(identity_gate["target_id_set_sha256"], "Stage08 identity target set")
    require_sha(identity_gate["candidate_id_set_sha256"], "Stage08 identity candidate set")
    identity_ref = {
        "path": payload["terminal_normalized_gds_identity_audit"]["receipt_path"],
        "sha256": payload["terminal_normalized_gds_identity_audit"]["receipt_sha256"],
    }
    _same_file_identity(identity_gate["receipt"], identity_ref, "Stage08 identity receipt")
    identity_path, identity_sha = require_record(
        identity_gate["receipt"], "identity receipt binding", public=True
    )
    identity_receipt = _load_bound_json(
        read_ref,
        identity_path,
        identity_sha,
        identity_gate["receipt"]["size_bytes"],
        "identity receipt",
    )
    validate_identity_receipt(identity_receipt)
    _same_file_identity(identity_receipt["summary"], identity_gate["summary"], "identity summary")
    _same_file_identity(identity_receipt["rows"], identity_gate["rows"], "identity rows")

    manifest_payloads: dict[str, Mapping[str, Any]] = {}
    for kind in (
        "stage07_manifest", "stage08_manifest", "three_chain_manifest",
        "full_band_v3_manifest",
    ):
        path, digest, size_bytes = source_ref(f"canonical_{kind}")
        manifest = _load_bound_json(read_ref, path, digest, size_bytes, kind)
        validate_manifest_payload(manifest, kind)
        manifest_payloads[kind] = manifest

    nested_artifact_records: dict[str, Mapping[str, Any]] = {}
    nested_artifact_identities: dict[tuple[str, str], str] = {}

    def bind_nested_artifact(
        kind: str, section: str, key: str, record: Mapping[str, Any], label: str
    ) -> None:
        role = nested_artifact_role(kind, section, key)
        if role in nested_artifact_records:
            raise GateError(f"duplicate nested artifact role: {role}")
        path, digest, declared_size = _record_identity(record, label)
        identity = (path, digest)
        if identity in nested_artifact_identities:
            raise GateError(
                "distinct nested artifact roles must bind distinct path/SHA identities: "
                f"{nested_artifact_identities[identity]} and {role}"
            )
        raw = read_ref(path, digest, label)
        if sha_bytes(raw) != digest:
            raise GateError(f"{label} held SHA mismatch")
        if declared_size is not None and len(raw) != declared_size:
            raise GateError(
                f"{label} declared size differs from held bytes: "
                f"declared={declared_size}, actual={len(raw)}"
            )
        if len(raw) <= 0:
            raise GateError(f"{label} held artifact bytes are empty")
        nested_artifact_records[role] = record
        nested_artifact_identities[identity] = role

    stage07_manifest = manifest_payloads["stage07_manifest"]
    bind_nested_artifact(
        "stage07_manifest", "script", "script", stage07_manifest["script"],
        "Stage07 manifest script bytes",
    )
    for key in sorted(MANIFEST_ARTIFACT_KEYS["stage07_manifest"]):
        bind_nested_artifact(
            "stage07_manifest", "artifacts", key,
            stage07_manifest["artifacts"][key],
            f"Stage07 manifest artifact bytes {key}",
        )
    for key in sorted(MANIFEST_ARTIFACT_KEYS["stage08_manifest"]):
        bind_nested_artifact(
            "stage08_manifest", "artifacts", key,
            manifest_payloads["stage08_manifest"]["artifacts"][key],
            f"Stage08 manifest artifact bytes {key}",
        )
    for key in sorted(MANIFEST_ARTIFACT_KEYS["three_chain_manifest"]):
        bind_nested_artifact(
            "three_chain_manifest", "artifacts", key,
            manifest_payloads["three_chain_manifest"]["artifacts"][key],
            f"three-chain manifest artifact bytes {key}",
        )
    full_manifest_for_closure = manifest_payloads["full_band_v3_manifest"]
    for section, keys in (
        ("inputs", FULL_MANIFEST_INPUT_KEYS),
        ("runtime_sources", FULL_MANIFEST_RUNTIME_KEYS),
        ("outputs", MANIFEST_ARTIFACT_KEYS["full_band_v3_manifest"]),
    ):
        for key in sorted(keys):
            bind_nested_artifact(
                "full_band_v3_manifest", section, key,
                full_manifest_for_closure[section][key],
                f"full-band manifest {section} bytes {key}",
            )
    if set(nested_artifact_records) != NESTED_ARTIFACT_ROLES:
        raise GateError("nested manifest artifact role closure is not exact")
    if len(nested_artifact_identities) != len(NESTED_ARTIFACT_ROLES):
        raise GateError("nested manifest artifact identity closure is not one-to-one")
    stage07_artifacts = manifest_payloads["stage07_manifest"]["artifacts"]
    _same_file_identity(
        stage07_evidence["summary"],
        stage07_artifacts["historical_200k_fresh_emx_statistics_summary.json"],
        "Stage07 terminal/manifest summary",
    )
    _same_file_identity(
        stage07_evidence["rows"],
        stage07_artifacts["historical_200k_fresh_emx_evaluated_rows.csv"],
        "Stage07 terminal/manifest rows",
    )
    stage08_artifacts = manifest_payloads["stage08_manifest"]["artifacts"]
    _same_file_identity(stage08_evidence["summary"], stage08_artifacts["summary"], "Stage08 summary")
    _same_file_identity(stage08_evidence["rows"], stage08_artifacts["v2_rows"], "Stage08 rows")

    resume_evidence = require_exact_keys(
        resume_preflight.get("evidence"), RESUME_PREFLIGHT_EVIDENCE_KEYS,
        "resume preflight evidence",
    )
    for key in (
        "bundle_manifest", "contract", "launcher_authentication_terminal", "review_go",
        "runtime_dependency_manifest", "runtime_interpreter", "watcher_launch_receipt",
        "watcher_script",
    ):
        require_record(resume_evidence[key], f"resume preflight {key}", public=True)
    if resume_evidence["identity_gate"] != identity_gate:
        raise GateError("resume preflight identity gate differs from Stage08")
    resume_sources = require_exact_keys(
        resume_evidence["sources"],
        {"stage07_generator", "stage08_generator", "stage08_preregistration"},
        "resume preflight sources",
    )
    for key in resume_sources:
        require_record(resume_sources[key], f"resume preflight sources.{key}", public=True)
    if not isinstance(resume_evidence["watcher_process_identity"], Mapping):
        raise GateError("resume preflight watcher process identity is absent")
    for evidence_key in ("runtime_interpreter", "watcher_script"):
        record = resume_evidence[evidence_key]
        _read_bound_public_bytes(
            read_ref,
            record["path"],
            record["sha256"],
            record["size_bytes"],
            f"resume preflight {evidence_key}",
        )
    resume_launcher_key = "launcher_preflight__resume_exact_watcher_stage07_08_v5.py"
    _same_file_identity(
        resume_evidence["launcher_authentication_terminal"],
        release["launcher_preflight_terminals"][resume_launcher_key],
        "resume preflight launcher parent",
    )
    for child_key, launcher_key in (
        ("bundle_manifest", "bundle_manifest"), ("contract", "contract"),
        ("review_go", "independent_review_go"),
        ("runtime_dependency_manifest", "runtime_dependency_manifest"),
    ):
        _same_file_identity(
            resume_evidence[child_key], launcher_evidence[resume_launcher_key][launcher_key],
            f"resume preflight/launcher {child_key}",
        )
    _same_file_identity(
        resume_sources["stage07_generator"], manifest_payloads["stage07_manifest"]["script"],
        "resume Stage07 generator",
    )
    _same_file_identity(resume_sources["stage08_generator"], stage08_evidence["generator"], "resume Stage08 generator")
    _same_file_identity(
        resume_sources["stage08_preregistration"], stage08_evidence["preregistration"],
        "resume Stage08 preregistration",
    )

    three_preflight_evidence = require_exact_keys(
        three_preflight.get("evidence"), THREE_PREFLIGHT_EVIDENCE_KEYS,
        "three-chain preflight evidence",
    )
    for key in THREE_PREFLIGHT_EVIDENCE_KEYS:
        require_record(three_preflight_evidence[key], f"three-chain preflight {key}", public=True)
    three_launcher_key = "launcher_preflight__run_three_chain_after_stage08_v5.py"
    _same_file_identity(three_preflight_evidence["stage08_terminal"], stage08_ref, "three preflight Stage08")
    _same_file_identity(
        three_preflight_evidence["launcher_authentication_terminal"],
        release["launcher_preflight_terminals"][three_launcher_key], "three preflight launcher",
    )
    _same_file_identity(
        three_preflight_evidence["runtime_dependency_manifest"],
        launcher_evidence[three_launcher_key]["runtime_dependency_manifest"],
        "three preflight runtime",
    )

    full_preflight_evidence = require_exact_keys(
        full_preflight.get("evidence"), FULL_PREFLIGHT_EVIDENCE_KEYS,
        "full-band preflight evidence",
    )
    for key in ("runtime_dependency_manifest", "launcher_authentication_terminal", "stage08_terminal"):
        require_record(full_preflight_evidence[key], f"full-band preflight {key}", public=True)
    preflight_full_bindings = require_exact_keys(
        full_preflight_evidence["source_bindings"], FULL_SOURCE_BINDING_KEYS,
        "full-band preflight source bindings",
    )
    for key in FULL_SOURCE_BINDING_KEYS:
        require_record(preflight_full_bindings[key], f"full-band preflight source {key}", public=True)
    full_launcher_key = "launcher_preflight__run_full_band_v3_after_stage08_v5.py"
    _same_file_identity(full_preflight_evidence["stage08_terminal"], stage08_ref, "full preflight Stage08")
    _same_file_identity(
        full_preflight_evidence["launcher_authentication_terminal"],
        release["launcher_preflight_terminals"][full_launcher_key], "full preflight launcher",
    )
    _same_file_identity(
        full_preflight_evidence["runtime_dependency_manifest"],
        launcher_evidence[full_launcher_key]["runtime_dependency_manifest"],
        "full preflight runtime",
    )

    three_evidence = require_exact_keys(
        three.get("evidence"),
        {"canonical_stage08_terminal", "preflight_terminal", "manifest", "rows", "summary"},
        "three-chain evidence",
    )
    for key in ("canonical_stage08_terminal", "preflight_terminal", "manifest", "summary"):
        require_record(three_evidence[key], f"three-chain {key}", public=True)
    require_record(three_evidence["rows"], "three-chain rows", row_count=7298, public=True)
    _same_file_identity(three_evidence["canonical_stage08_terminal"], stage08_ref, "three-chain Stage08")
    _same_file_identity(three_evidence["preflight_terminal"], release["three_chain_preflight_terminal"], "three preflight")
    _same_file_identity(three_evidence["manifest"], release["three_chain_manifest"], "three manifest")
    three_artifacts = manifest_payloads["three_chain_manifest"]["artifacts"]
    _same_file_identity(three_evidence["summary"], three_artifacts["summary"], "three summary")
    _same_file_identity(three_evidence["rows"], three_artifacts["rows"], "three rows")

    full_evidence = require_exact_keys(
        full.get("evidence"),
        {"canonical_stage08_terminal", "preflight_terminal", "source_bindings", "manifest",
         "rows", "summary", "internal_terminal"},
        "full-band-v3 evidence",
    )
    for key in (
        "canonical_stage08_terminal", "preflight_terminal", "manifest", "summary",
        "internal_terminal",
    ):
        require_record(full_evidence[key], f"full-band-v3 {key}", public=True)
    require_record(full_evidence["rows"], "full-band-v3 rows", row_count=7298, public=True)
    full_bindings = require_exact_keys(
        full_evidence["source_bindings"], FULL_SOURCE_BINDING_KEYS,
        "full-band-v3 source bindings",
    )
    for key in FULL_SOURCE_BINDING_KEYS:
        require_record(full_bindings[key], f"full-band-v3 source {key}", public=True)
        _same_file_identity(full_bindings[key], preflight_full_bindings[key], f"full source {key}")
    _same_file_identity(full_evidence["canonical_stage08_terminal"], stage08_ref, "full Stage08")
    _same_file_identity(full_evidence["preflight_terminal"], release["full_band_v3_preflight_terminal"], "full preflight")
    _same_file_identity(full_evidence["manifest"], release["full_band_v3_manifest"], "full manifest")
    _same_file_identity(full_evidence["internal_terminal"], release["full_band_v3_internal_terminal"], "full internal")
    full_manifest = manifest_payloads["full_band_v3_manifest"]
    _same_file_identity(full_evidence["summary"], full_manifest["outputs"]["summary"], "full summary")
    _same_file_identity(full_evidence["rows"], full_manifest["outputs"]["rows"], "full rows")
    _same_file_identity(full_bindings["v3_generator"], full_manifest["inputs"]["auditor_script"], "full generator input")
    _same_file_identity(full_bindings["unchanged_method_preregistration"], full_manifest["inputs"]["method_preregistration"], "full method input")
    _same_file_identity(full_bindings["unchanged_stage06_config"], full_manifest["inputs"]["stage06_config"], "full config input")

    internal_path, internal_sha, internal_size = source_ref(
        "canonical_full_band_v3_internal_terminal"
    )
    internal = _load_bound_json(
        read_ref,
        internal_path,
        internal_sha,
        internal_size,
        "full-band-v3 internal terminal",
    )
    validate_full_band_internal(internal, release)
    _same_file_identity(internal["summary"], full_evidence["summary"], "full internal summary")
    _same_file_identity(internal["rows"], full_evidence["rows"], "full internal rows")
    _same_file_identity(internal["fixed_histograms"], full_manifest["outputs"]["fixed_histograms"], "full internal fixed histograms")
    _same_file_identity(internal["plot"], full_manifest["outputs"]["plot"], "full internal plot")
    _same_file_identity(internal["identity_terminal_receipt"], identity_ref, "full internal identity")
    _same_file_identity(internal["method_preregistration"], full_manifest["inputs"]["method_preregistration"], "full internal method")
    _same_file_identity(internal["stage08_terminal_receipt"], full_manifest["inputs"]["stage08_terminal_receipt"], "full internal Stage08")

    controller_evidence = require_exact_keys(
        controller.get("evidence"),
        {"authorization", "preflight_terminal", "resume_outcome", "stage07_terminal", "stage08_terminal"},
        "controller evidence",
    )
    for key in controller_evidence:
        require_record(controller_evidence[key], f"controller {key}", public=True)
    _same_file_identity(controller_evidence["stage07_terminal"], stage07_ref, "controller Stage07")
    _same_file_identity(controller_evidence["stage08_terminal"], stage08_ref, "controller Stage08")
    _same_file_identity(controller_evidence["preflight_terminal"], release["resume_preflight_terminal"], "controller resume preflight")

    auth_path, auth_sha = require_record(controller_evidence["authorization"], "controller authorization", public=True)
    authorization = require_exact_keys(
        _load_bound_json(
            read_ref,
            auth_path,
            auth_sha,
            controller_evidence["authorization"]["size_bytes"],
            "controller authorization",
        ),
        {"schema", "status", "authorized_utc", "contract", "bundle_manifest",
         "independent_review_go", "runtime_dependency_manifest", "runtime_dependency_root_digest",
         "runtime_interpreter", "isolated_flags", "launcher_authentication_terminal",
         "preflight_terminal", "watcher_launch_receipt", "watcher_script",
         "watcher_process_start_identity", "identity_gate", "same_prelaunched_watcher_only",
         "transport_authorized", "scientific_release_authorized"},
        "controller authorization",
    )
    if (
        authorization.get("schema") != AUTHORIZATION_SCHEMA
        or authorization.get("status") != "AUTHORIZED_NOT_YET_RESUMED"
        or authorization.get("same_prelaunched_watcher_only") is not True
        or authorization.get("transport_authorized") is not False
        or authorization.get("scientific_release_authorized") is not False
        or authorization.get("isolated_flags") != ["-I", "-B", "-S"]
        or not isinstance(authorization.get("authorized_utc"), str)
        or not authorization["authorized_utc"]
    ):
        raise GateError("controller authorization schema/status/authority drift")
    require_sha(authorization.get("runtime_dependency_root_digest"), "authorization runtime root")
    for key in (
        "contract", "bundle_manifest", "independent_review_go", "runtime_dependency_manifest",
        "runtime_interpreter", "launcher_authentication_terminal", "preflight_terminal",
        "watcher_launch_receipt", "watcher_script",
    ):
        require_record(authorization[key], f"controller authorization {key}", public=True)
    _same_file_identity(authorization["launcher_authentication_terminal"], release["launcher_preflight_terminals"][resume_launcher_key], "authorization launcher")
    _same_file_identity(authorization["preflight_terminal"], controller_evidence["preflight_terminal"], "authorization preflight")
    for auth_key, resume_key in (
        ("bundle_manifest", "bundle_manifest"), ("contract", "contract"),
        ("independent_review_go", "review_go"),
        ("runtime_dependency_manifest", "runtime_dependency_manifest"),
        ("runtime_interpreter", "runtime_interpreter"),
        ("watcher_launch_receipt", "watcher_launch_receipt"),
        ("watcher_script", "watcher_script"),
    ):
        _same_file_identity(authorization[auth_key], resume_evidence[resume_key], f"authorization/resume {auth_key}")
    if authorization["identity_gate"] != identity_gate:
        raise GateError("authorization identity gate drift")

    contract_path, contract_sha = require_record(authorization["contract"], "authorization contract", public=True)
    contract = validate_release_contract_payload(
        _load_bound_json(
            read_ref,
            contract_path,
            contract_sha,
            authorization["contract"]["size_bytes"],
            "release contract",
        )
    )
    for key, spec_key in (
        ("stage07_generator", ("stage07", "generator_path", "generator_sha256")),
        ("stage08_generator", ("stage08", "generator_path", "generator_sha256")),
        ("stage08_preregistration", ("stage08", "preregistration_path", "preregistration_sha256")),
    ):
        section, path_key, sha_key = spec_key
        if _record_identity(resume_sources[key], f"resume source {key}")[:2] != (
            contract[section][path_key], contract[section][sha_key]
        ):
            raise GateError(f"resume source {key} differs from release contract")
    if _record_identity(three_preflight_evidence["generator"], "three generator")[:2] != (
        contract["three_chain"]["generator_path"], contract["three_chain"]["generator_sha256"]
    ):
        raise GateError("three-chain generator differs from release contract")
    full_spec = contract["full_band_v3"]
    full_expected = {
        "panel_schema_addendum": (str(Path(full_spec["bundle_runtime_dir"]) / full_spec["panel_addendum_name"]), full_spec["panel_addendum_sha256"]),
        "superseded_base_generator": (full_spec["base_generator_path"], full_spec["base_generator_sha256"]),
        "unchanged_method_preregistration": (full_spec["method_preregistration_path"], full_spec["method_preregistration_sha256"]),
        "unchanged_stage06_config": (full_spec["stage06_config_path"], full_spec["stage06_config_sha256"]),
        "v3_generator": (str(Path(full_spec["bundle_runtime_dir"]) / full_spec["generator_name"]), full_spec["generator_sha256"]),
    }
    for key, expected in full_expected.items():
        if _record_identity(full_bindings[key], f"full source {key}")[:2] != expected:
            raise GateError(f"full source {key} differs from release contract")

    process_identity = validate_process_identity(
        authorization["watcher_process_start_identity"], authorization, contract
    )
    if resume_evidence["watcher_process_identity"] != process_identity:
        raise GateError("resume preflight/process identity projection drift")
    launch_path, launch_sha = require_record(
        authorization["watcher_launch_receipt"], "authorization watcher launch receipt",
        public=True,
    )
    validate_watcher_launch_receipt_payload(
        _load_bound_json(
            read_ref,
            launch_path,
            launch_sha,
            authorization["watcher_launch_receipt"]["size_bytes"],
            "watcher launch receipt",
        ),
        contract,
        process_identity,
    )

    bundle_path, bundle_sha = require_record(authorization["bundle_manifest"], "authorization bundle", public=True)
    bundle_manifest = validate_bundle_manifest_payload(
        _load_bound_json(
            read_ref,
            bundle_path,
            bundle_sha,
            authorization["bundle_manifest"]["size_bytes"],
            "release bundle manifest",
        )
    )
    members = {item["relative_path"]: item for item in bundle_manifest["files"]}
    for launcher_key, evidence in launcher_evidence.items():
        for key in ("common", "contract", "controller"):
            record = evidence[key]
            name = PurePosixPath(record["path"]).name
            member = members.get(name)
            if member is None or member["sha256"] != record["sha256"] or member["size_bytes"] != record["size_bytes"]:
                raise GateError(f"launcher {launcher_key} {key} is not the exact bundle member")

    go_path, go_sha = require_record(authorization["independent_review_go"], "authorization GO", public=True)
    go = validate_independent_go_payload(
        _load_bound_json(
            read_ref,
            go_path,
            go_sha,
            authorization["independent_review_go"]["size_bytes"],
            "independent review GO",
        ),
        contract=contract, authorization=authorization, process_identity=process_identity,
    )
    runtime_path, runtime_sha = require_record(
        authorization["runtime_dependency_manifest"], "authorization runtime manifest", public=True
    )
    validate_runtime_manifest_payload(
        _load_bound_json(
            read_ref,
            runtime_path,
            runtime_sha,
            authorization["runtime_dependency_manifest"]["size_bytes"],
            "runtime dependency manifest",
        ),
        expected_root_digest=go["runtime_dependency_root_digest"],
        expected_site_packages_root=go["runtime_site_packages_root"],
    )

    outcome_path, outcome_sha = require_record(controller_evidence["resume_outcome"], "controller outcome", public=True)
    outcome = require_exact_keys(
        _load_bound_json(
            read_ref,
            outcome_path,
            outcome_sha,
            controller_evidence["resume_outcome"]["size_bytes"],
            "controller resume outcome",
        ),
        {"schema", "overall_status", "resumed_utc", "watcher_pid", "authorization",
         "identity_revalidated_same_snapshots", "pidfd_signal_used",
         "watcher_process_start_identity", "live_proc_identity_revalidated_before_pidfd_signal"},
        "controller resume outcome",
    )
    if (
        outcome.get("schema") != OUTCOME_SCHEMA or outcome.get("overall_status") != "PASS"
        or outcome.get("identity_revalidated_same_snapshots") is not True
        or outcome.get("pidfd_signal_used") is not True
        or outcome.get("live_proc_identity_revalidated_before_pidfd_signal") is not True
        or outcome.get("watcher_pid") != process_identity["pid"]
        or outcome.get("watcher_process_start_identity") != process_identity
        or not isinstance(outcome.get("resumed_utc"), str) or not outcome["resumed_utc"]
    ):
        raise GateError("controller resume outcome exact semantics drift")
    _same_file_identity(outcome["authorization"], controller_evidence["authorization"], "outcome authorization")

    prereg_path = payload["fresh_metric_contract"]["statistics_v2_manifest_path"]
    prereg_sha = require_sha(payload["fresh_metric_contract"]["statistics_v2_manifest_sha256"], "preregistration SHA")
    _same_file_identity(stage08_evidence["preregistration"], {"path": prereg_path, "sha256": prereg_sha}, "Stage08 preregistration")
    validate_preregistration(
        _load_bound_json(
            read_ref,
            prereg_path,
            prereg_sha,
            stage08_evidence["preregistration"]["size_bytes"],
            "statistics-v2 preregistration",
        )
    )

    discovered_records = {
        "controller_resume_authorization": controller_evidence["authorization"],
        "controller_resume_preflight_terminal": controller_evidence["preflight_terminal"],
        "controller_resume_outcome": controller_evidence["resume_outcome"],
        "controller_release_contract": authorization["contract"],
        "controller_bundle_manifest": authorization["bundle_manifest"],
        "controller_independent_review_go": authorization["independent_review_go"],
        "controller_runtime_dependency_manifest": authorization["runtime_dependency_manifest"],
        "controller_runtime_interpreter": authorization["runtime_interpreter"],
        "controller_watcher_launch_receipt": authorization["watcher_launch_receipt"],
        "controller_watcher_script": authorization["watcher_script"],
        "controller_release_common": baseline_launcher["common"],
        **nested_artifact_records,
    }
    for launcher_key, evidence in sorted(launcher_evidence.items()):
        controller_name = launcher_key.removeprefix("launcher_preflight__")
        discovered_records[f"controller_bound_launcher__{controller_name}"] = evidence[
            "controller"
        ]
    return [
        (role, str(record["path"]), require_sha(record["sha256"], f"{role} SHA"))
        for role, record in sorted(discovered_records.items())
    ]


def validate_producer_interface(payload: Any) -> dict[str, list[float]]:
    payload = require_exact_keys(payload, PRODUCER_ROOT_KEYS, "producer interface root")
    reject_authority_like_keys(payload, "producer interface")
    validate_exact_count_types(payload, "producer interface")
    if payload.get("schema") != INPUT_SCHEMA or payload.get("status") != "complete":
        raise GateError("input must be a complete producer EMX interface v1")
    run = require_exact_keys(payload.get("run"), PRODUCER_RUN_KEYS, "producer run")
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
        "fresh_emx_reporting_label": SOURCE_REPORTING_LABEL,
        "selection_is_response_blind": True,
        "selection_strata": {"legacy_k_le_0p8": 5992, "extension_k_gt_0p8": 1306},
        "selection_weights_path": None,
        "selection_weights_sha256": None,
    }
    for key, expected in exact_run.items():
        require_exact_typed_value(run[key], expected, f"producer run.{key}")
    validate_producer_survivor_statement(run.get("survivor_conditioning_statement"))
    identity = require_exact_keys(
        payload.get("terminal_normalized_gds_identity_audit"),
        PRODUCER_IDENTITY_KEYS,
        "producer normalized-GDS identity audit",
    )
    expected_identity = {
        "status": "PASS",
        "expected_candidate_count": 7298,
        "algorithm": IDENTITY_ALGORITHM,
        "terminal_match_count": 7298,
        "terminal_mismatch_count": 0,
        "result_publication_allowed": True,
    }
    for key, expected in expected_identity.items():
        require_exact_typed_value(identity.get(key), expected, f"producer identity.{key}")
    require_sha(identity.get("receipt_sha256"), "identity receipt SHA")
    stage06 = require_exact_keys(
        payload.get("fresh_emx_stage06_running_state"),
        PRODUCER_STAGE06_KEYS,
        "producer Stage06 template state",
    )
    stale_exact = {
        "status": "RUNNING_NO_RESULT_AVAILABLE",
        "identity_audit_gate_status": "IDENTITY_AUDIT_GATE_PENDING",
        "full_7298_normalized_identity_terminal_audit_present": False,
        "stage07_result_present": False,
        "stage08_result_present": False,
        "numeric_fresh_emx_claim_allowed": False,
        "expected_candidate_count": 7298,
    }
    for key, expected in stale_exact.items():
        require_exact_typed_value(stage06.get(key), expected, f"producer Stage06.{key}")
    metric = require_exact_keys(
        payload.get("fresh_metric_contract"),
        PRODUCER_METRIC_CONTRACT_KEYS,
        "producer fresh metric contract",
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
    }
    for key, expected in expected_metric.items():
        require_exact_typed_value(metric.get(key), expected, f"producer metric contract.{key}")
    for field in ("statistics_v2_manifest_sha256", "statistics_v2_readme_sha256"):
        require_sha(metric.get(field), f"metric contract {field}")
    for key in ("stage_counts", "q_metrics", "fixed_bin_metrics", "paired_feature_rows"):
        if not isinstance(payload.get(key), list) or not payload[key]:
            raise GateError(f"producer {key} must be non-empty")
    validate_completion_contract(payload)
    validate_stage_funnel(payload.get("stage_counts"))
    validate_table_cardinalities(payload, portable=False)
    validate_source_science_tables(payload)
    validate_release_record_closure(payload)
    derived, values_by_panel = derive_primary_engineering_joint(payload["paired_feature_rows"])
    del derived
    validate_and_alias_fixed_bins(payload["fixed_bin_metrics"], values_by_panel)
    return values_by_panel


def _mirror_index(
    manifest: Any,
    expected_interface_sha: str,
    mirror_root: Path,
    *,
    mirror_lease: HeldRootLease | None = None,
    held_snapshots: dict[tuple[str, str], HeldRegularSnapshot] | None = None,
) -> dict[tuple[str, str], str]:
    if not isinstance(manifest, dict) or manifest.get("schema") != MIRROR_SCHEMA:
        raise GateError("unexpected local source-mirror manifest schema")
    if manifest.get("status") != "PASS_LOCAL_NO_CLOBBER_MIRROR" or manifest.get("remote_generation_performed") is not False:
        raise GateError("source mirror is not a local no-clobber PASS receipt")
    if manifest.get("input_interface_sha256") != expected_interface_sha:
        raise GateError("source mirror is not bound to the exact producer interface bytes")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise GateError("source mirror manifest has no files")
    index: dict[tuple[str, str], str] = {}
    path_to_sha: dict[str, str] = {}
    for position, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("original_path"), str):
            raise GateError(f"mirror record {position} is invalid")
        original = record["original_path"]
        if not original or "\x00" in original:
            raise GateError(f"mirror record {position} original path is invalid")
        expected = require_sha(record.get("sha256"), f"mirror record {position} sha")
        relative = safe_relative(record.get("mirror_relative_path"), f"mirror record {position} path")
        if original in path_to_sha and path_to_sha[original] != expected:
            raise GateError(f"one original path has conflicting mirror SHAs: {original}")
        path_to_sha[original] = expected
        key = (original, expected)
        if key in index:
            raise GateError(f"duplicate mirror mapping: {original}")
        if mirror_lease is None:
            raw = _read_relative_regular_bytes(
                mirror_root, relative, f"mirror record {position}"
            )
        else:
            snapshot = mirror_lease.open_regular(relative, f"mirror record {position}")
            try:
                snapshot.verify_named_continuity(verify_root=False)
                raw = snapshot.raw
                if held_snapshots is not None:
                    held_snapshots[key] = snapshot
                    snapshot = None
            finally:
                if snapshot is not None:
                    snapshot.close()
        if sha_bytes(raw) != expected:
            raise GateError(f"local mirror SHA mismatch: {relative}")
        index[key] = relative
    return index


def _required_source_refs(payload: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    identity = payload["terminal_normalized_gds_identity_audit"]
    metric = payload["fresh_metric_contract"]
    run = payload["run"]
    refs = [
        ("terminal_normalized_gds_identity_receipt", identity.get("receipt_path"), identity.get("receipt_sha256")),
        ("statistics_v2_preregistration", metric.get("statistics_v2_manifest_path"), metric.get("statistics_v2_manifest_sha256")),
        ("statistics_v2_readme", metric.get("statistics_v2_readme_path"), metric.get("statistics_v2_readme_sha256")),
        ("executed_statistics_copy", run.get("mars_executed_statistics_copy_path"), run.get("mars_executed_statistics_copy_sha256")),
    ]
    output: list[tuple[str, str, str]] = []
    for role, path, sha in refs:
        if not isinstance(path, str) or not path:
            raise GateError(f"required source path is absent: {role}")
        output.append((role, path, require_sha(sha, f"required source {role}")))
    return output


def _all_source_refs(
    payload: Mapping[str, Any], extra_refs: Sequence[tuple[str, str, str]] = (),
) -> list[tuple[str, str, str]]:
    refs: list[tuple[str, str, str]] = []
    seen_roles: set[str] = set()
    source_files = payload.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise GateError("producer source_files must be non-empty")
    for position, record in enumerate(source_files):
        if not isinstance(record, dict) or not isinstance(record.get("role"), str) or not isinstance(record.get("path"), str):
            raise GateError(f"producer source_files[{position}] is invalid")
        role = f"producer__{record['role']}"
        if role in seen_roles:
            raise GateError(f"duplicate producer source role: {role}")
        seen_roles.add(role)
        refs.append((role, record["path"], require_sha(record.get("sha256"), f"producer source {role}")))
    for role, path, sha in _required_source_refs(payload):
        if role in seen_roles:
            raise GateError(f"reserved required source role collision: {role}")
        seen_roles.add(role)
        refs.append((role, path, sha))
    for role, path, sha in extra_refs:
        if role in seen_roles:
            raise GateError(f"reserved discovered source role collision: {role}")
        if not isinstance(path, str) or not path:
            raise GateError(f"discovered source path is absent: {role}")
        seen_roles.add(role)
        refs.append((role, path, require_sha(sha, f"discovered source {role}")))
    return refs


def _sanitized_basename(original: str) -> str:
    name = PurePosixPath(original).name or "source.bin"
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned[:96] or "source.bin"


def _write_exclusive(path: Path, raw: bytes, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, mode)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise GateError(f"short write to {path}")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_exclusive_at(directory_fd: int, name: str, raw: bytes, mode: int = 0o600) -> None:
    if not isinstance(name, str) or PurePosixPath(name).name != name or name in ("", ".", ".."):
        raise GateError(f"exclusive output name is invalid: {name!r}")
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(name, flags, mode, dir_fd=directory_fd)
    except OSError as exc:
        raise GateError(f"exclusive dirfd create failed for {name}") from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise GateError(f"short write to held output {name}")
            view = view[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(raw):
            raise GateError(f"held output {name} is not the expected single-link regular file")
    finally:
        os.close(fd)


def _chmod_regular_at(directory_fd: int, name: str, mode: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GateError(f"output {name} is not a single-link regular file during freeze")
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


class HeldOutputLease:
    """Exclusive nofollow output publication rooted in immutable held dirfds."""

    def __init__(self, output_dir: Path):
        if not isinstance(output_dir, Path) or output_dir.name in ("", ".", ".."):
            raise GateError("no-clobber output path is invalid")
        if PurePosixPath(output_dir.name).name != output_dir.name:
            raise GateError("no-clobber output root must be one final path component")
        self.output_dir = output_dir
        self.parent = HeldRootLease(output_dir.parent, "output parent")
        self.root_fd = -1
        self.source_fd = -1
        self.root_identity: tuple[int, int] | None = None
        self.source_identity: tuple[int, int] | None = None
        self._held_root_files: dict[str, tuple[int, tuple[int, int], str, int]] = {}
        self._held_source_files: dict[str, tuple[int, tuple[int, int], str, int]] = {}
        self._closed = False
        self.parent.verify_named_continuity()
        try:
            os.mkdir(output_dir.name, 0o700, dir_fd=self.parent.root_fd)
        except FileExistsError as exc:
            self.close()
            raise GateError(f"no-clobber output already exists: {output_dir}") from exc
        except OSError as exc:
            self.close()
            raise GateError(f"exclusive output mkdir failed: {output_dir}") from exc
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            self.root_fd = os.open(output_dir.name, directory_flags, dir_fd=self.parent.root_fd)
            root_info = os.fstat(self.root_fd)
            if not stat.S_ISDIR(root_info.st_mode):
                raise GateError("held output root is not a directory")
            self.root_identity = (root_info.st_dev, root_info.st_ino)
            self.verify_named_continuity()
            os.mkdir("portable_sources", 0o700, dir_fd=self.root_fd)
            self.source_fd = os.open("portable_sources", directory_flags, dir_fd=self.root_fd)
            source_info = os.fstat(self.source_fd)
            if not stat.S_ISDIR(source_info.st_mode):
                raise GateError("held portable_sources is not a directory")
            self.source_identity = (source_info.st_dev, source_info.st_ino)
            self.verify_named_continuity()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _read_held_file(fd: int, expected_size: int, label: str) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = expected_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise GateError(f"held output {label} ended before its frozen size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise GateError(f"held output {label} exceeds its frozen size")
        return b"".join(chunks)

    def _bind_written_file(
        self,
        directory_fd: int,
        name: str,
        raw: bytes,
        held_files: dict[str, tuple[int, tuple[int, int], str, int]],
        label: str,
    ) -> None:
        if name in held_files:
            raise GateError(f"duplicate held output binding: {label}/{name}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise GateError(f"cannot hold newly written output {label}/{name}") from exc
        try:
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            expected_sha = sha_bytes(raw)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != len(raw)
                or self._read_held_file(fd, len(raw), f"{label}/{name}") != raw
            ):
                raise GateError(
                    f"newly written output {label}/{name} is not the exact "
                    "single-link byte version supplied by the adapter"
                )
            held_files[name] = (fd, identity, expected_sha, len(raw))
            fd = -1
        finally:
            if fd >= 0:
                os.close(fd)

    def _verify_held_file_versions(self) -> None:
        for label, directory_fd, held_files in (
            ("root", self.root_fd, self._held_root_files),
            ("portable_sources", self.source_fd, self._held_source_files),
        ):
            if directory_fd < 0:
                continue
            for name, (fd, identity, expected_sha, expected_size) in held_files.items():
                held = os.fstat(fd)
                try:
                    named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise GateError(f"named held output disappeared: {label}/{name}") from exc
                if (
                    not stat.S_ISREG(held.st_mode)
                    or not stat.S_ISREG(named.st_mode)
                    or held.st_nlink != 1
                    or named.st_nlink != 1
                    or (held.st_dev, held.st_ino) != identity
                    or (named.st_dev, named.st_ino) != identity
                    or held.st_size != expected_size
                    or named.st_size != expected_size
                ):
                    raise GateError(f"named output version identity drift: {label}/{name}")
                raw = self._read_held_file(fd, expected_size, f"{label}/{name}")
                if sha_bytes(raw) != expected_sha:
                    raise GateError(f"held output byte version drift: {label}/{name}")

    def verify_named_continuity(self) -> None:
        if self._closed:
            raise GateError("output lease is closed")
        self.parent.verify_named_continuity()
        if self.root_fd >= 0:
            held = os.fstat(self.root_fd)
            try:
                named = os.stat(
                    self.output_dir.name,
                    dir_fd=self.parent.root_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise GateError("named output root is absent after exclusive mkdir") from exc
            if (
                not stat.S_ISDIR(named.st_mode)
                or (held.st_dev, held.st_ino) != self.root_identity
                or (named.st_dev, named.st_ino) != self.root_identity
            ):
                raise GateError("named output root no longer identifies held root")
        if self.source_fd >= 0:
            held = os.fstat(self.source_fd)
            try:
                named = os.stat(
                    "portable_sources", dir_fd=self.root_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise GateError("named portable_sources is absent") from exc
            if (
                not stat.S_ISDIR(named.st_mode)
                or (held.st_dev, held.st_ino) != self.source_identity
                or (named.st_dev, named.st_ino) != self.source_identity
            ):
                raise GateError("named portable_sources no longer identifies held directory")
        self._verify_held_file_versions()

    def write_root(self, name: str, raw: bytes) -> None:
        self.verify_named_continuity()
        _write_exclusive_at(self.root_fd, name, raw)
        self._bind_written_file(
            self.root_fd, name, raw, self._held_root_files, "root"
        )
        self.verify_named_continuity()

    def write_source(self, name: str, raw: bytes) -> None:
        self.verify_named_continuity()
        _write_exclusive_at(self.source_fd, name, raw)
        self._bind_written_file(
            self.source_fd, name, raw, self._held_source_files, "portable_sources"
        )
        self.verify_named_continuity()

    def freeze(self, root_names: Iterable[str], source_names: Iterable[str]) -> None:
        self.verify_named_continuity()
        expected_root_names = tuple(root_names)
        expected_source_names = tuple(source_names)
        if len(expected_root_names) != len(set(expected_root_names)) or set(expected_root_names) != set(self._held_root_files):
            raise GateError("root output freeze set differs from continuously held versions")
        if len(expected_source_names) != len(set(expected_source_names)) or set(expected_source_names) != set(self._held_source_files):
            raise GateError("portable-source freeze set differs from continuously held versions")
        for name in expected_source_names:
            fd = self._held_source_files[name][0]
            os.fchmod(fd, 0o444)
            os.fsync(fd)
        for name in expected_root_names:
            fd = self._held_root_files[name][0]
            os.fchmod(fd, 0o444)
            os.fsync(fd)
        self.verify_named_continuity()
        os.fchmod(self.source_fd, 0o555)
        os.fsync(self.source_fd)
        os.fchmod(self.root_fd, 0o555)
        os.fsync(self.root_fd)
        self.verify_named_continuity()
        os.fsync(self.parent.root_fd)

    def write_failure_marker(self) -> None:
        if self.root_fd < 0:
            return
        try:
            _write_exclusive_at(
                self.root_fd,
                "ADAPTER_FAIL_NO_GO.txt",
                b"FAIL_NO_GO; partial output must not be consumed or retried in place.\n",
            )
            os.fsync(self.root_fd)
        except Exception:
            pass

    def close(self) -> None:
        if not self._closed:
            for held_files in (self._held_source_files, self._held_root_files):
                for fd, _, _, _ in held_files.values():
                    os.close(fd)
                held_files.clear()
            if self.source_fd >= 0:
                os.close(self.source_fd)
                self.source_fd = -1
            if self.root_fd >= 0:
                os.close(self.root_fd)
                self.root_fd = -1
            self.parent.close()
            self._closed = True


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_active_paths(value: Any, mapping: Mapping[str, str], key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _replace_active_paths(item_value, mapping, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_replace_active_paths(item, mapping, key) for item in value]
    if isinstance(value, str) and key is not None and (key == "path" or key.endswith("_path")):
        if value in mapping:
            return mapping[value]
        if Path(value).is_absolute() or "\\" in value or "\x00" in value:
            raise GateError(f"unmirrored nonportable active path: {value}")
    return value


def adapt_interface(
    input_interface: Path,
    mirror_manifest_path: Path,
    mirror_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    input_lease = HeldRootLease(input_interface.parent, "producer interface parent")
    mirror_manifest_lease = HeldRootLease(
        mirror_manifest_path.parent, "mirror manifest parent"
    )
    mirror_lease = HeldRootLease(mirror_root, "local source mirror root")
    input_snapshot: HeldRegularSnapshot | None = None
    mirror_manifest_snapshot: HeldRegularSnapshot | None = None
    mirror_snapshots: dict[tuple[str, str], HeldRegularSnapshot] = {}
    output_lease: HeldOutputLease | None = None

    def verify_all_source_continuity() -> None:
        input_lease.verify_named_continuity()
        mirror_manifest_lease.verify_named_continuity()
        mirror_lease.verify_named_continuity()
        if input_snapshot is not None:
            input_snapshot.verify_named_continuity(verify_root=False)
        if mirror_manifest_snapshot is not None:
            mirror_manifest_snapshot.verify_named_continuity(verify_root=False)
        for snapshot in mirror_snapshots.values():
            snapshot.verify_named_continuity(verify_root=False)

    try:
        input_snapshot = input_lease.open_regular(
            input_interface.name, "producer interface"
        )
        input_raw = input_snapshot.raw
        input_sha = sha_bytes(input_raw)
        payload = strict_json_bytes(input_raw, "producer interface")
        values_by_panel = validate_producer_interface(payload)

        mirror_manifest_snapshot = mirror_manifest_lease.open_regular(
            mirror_manifest_path.name, "mirror manifest"
        )
        mirror_manifest_raw = mirror_manifest_snapshot.raw
        mirror_manifest = strict_json_bytes(mirror_manifest_raw, "mirror manifest")
        mirror_index = _mirror_index(
            mirror_manifest,
            input_sha,
            mirror_root,
            mirror_lease=mirror_lease,
            held_snapshots=mirror_snapshots,
        )

        def read_mirror_ref(original: str, expected: str, label: str) -> bytes:
            relative = mirror_index.get((original, expected))
            snapshot = mirror_snapshots.get((original, expected))
            if relative is None or snapshot is None:
                raise GateError(f"{label} is absent from mirror closure: {original}")
            snapshot.verify_named_continuity(verify_root=False)
            if sha_bytes(snapshot.raw) != expected:
                raise GateError(f"{label} held mirror SHA drift")
            return snapshot.raw

        discovered_refs = validate_bound_source_documents(payload, read_mirror_ref)
        source_refs = _all_source_refs(payload, discovered_refs)
        for _, original, expected in source_refs:
            if (original, expected) not in mirror_index:
                raise GateError(f"required source is absent from mirror closure: {original}")
        verify_all_source_continuity()
        output_lease = HeldOutputLease(output_dir)

        unique_refs = sorted({(original, expected) for _, original, expected in source_refs})
        path_map: dict[str, str] = {}
        key_to_bundle: dict[tuple[str, str], str] = {}
        source_names: list[str] = []
        for position, (original, expected) in enumerate(unique_refs):
            filename = f"{position:03d}_{expected[:16]}_{_sanitized_basename(original)}"
            relative = f"portable_sources/{filename}"
            snapshot = mirror_snapshots[(original, expected)]
            snapshot.verify_named_continuity(verify_root=False)
            source_raw = snapshot.raw
            if sha_bytes(source_raw) != expected:
                raise GateError(f"source mirror changed before publication: {original}")
            output_lease.write_source(filename, source_raw)
            source_names.append(filename)
            if original in path_map and path_map[original] != relative:
                raise GateError(f"ambiguous portable mapping for {original}")
            path_map[original] = relative
            key_to_bundle[(original, expected)] = relative
        os.fsync(output_lease.source_fd)
        original_relative = "portable_sources/producer_complete_interface_original.json"
        output_lease.write_source(PurePosixPath(original_relative).name, input_raw)
        source_names.append(PurePosixPath(original_relative).name)

        output = _replace_active_paths(copy.deepcopy(payload), path_map)
        output["schema"] = OUTPUT_SCHEMA
        output["run"]["fresh_emx_evidence_scope_detail"] = SOURCE_REPORTING_LABEL
        output["run"]["fresh_emx_reporting_label"] = REPORT_REPORTING_LABEL
        output["run"]["survivor_conditioning_statement"] = SURVIVOR_CONDITIONING_STATEMENT
        output["run"]["survivor_scope"] = copy.deepcopy(SURVIVOR_SCOPE)
        output["terminal_normalized_gds_identity_audit"]["producer_status"] = "PASS"
        output["terminal_normalized_gds_identity_audit"]["status"] = "PASS_7298_OF_7298"
        output["fresh_emx_stage06_running_state"].update(
            {
                "producer_template_status": "RUNNING_NO_RESULT_AVAILABLE",
                "status": "TERMINAL_PASS_7298_OF_7298_NO_PENDING_RESULTS",
                "identity_audit_gate_status": "PASS_7298_OF_7298_NORMALIZED_EXACT_LAYOUT_STREAM_IDENTITY",
                "full_7298_normalized_identity_terminal_audit_present": True,
                "stage07_result_present": True,
                "stage08_result_present": True,
                "numeric_fresh_emx_claim_allowed": True,
            }
        )
        for row in output["comparison_feature_metrics"]:
            row["producer_metric_contract_version"] = SOURCE_METRIC_VERSION
            row["metric_contract_version"] = REPORT_METRIC_VERSION
        primary_joint, values_by_panel_check = derive_primary_engineering_joint(output["paired_feature_rows"])
        require_exact_typed_value(
            values_by_panel_check,
            values_by_panel,
            "paired-row primary derivation during path-only adaptation",
        )
        output["joint_metrics"].extend(primary_joint)
        output["fixed_bin_metrics"] = validate_and_alias_fixed_bins(output["fixed_bin_metrics"], values_by_panel)
        output["fresh_metric_contract"]["producer_metric_contract_version"] = SOURCE_METRIC_VERSION
        output["fresh_metric_contract"]["report_interface_metric_contract_version"] = REPORT_METRIC_VERSION
        output["fresh_metric_contract"]["primary_joint_adapter_derivation"] = (
            "paired_rows_fixed_spans_q_floor_numpy_linear_v1"
        )
        validate_table_cardinalities(output, portable=True)

        records: list[dict[str, Any]] = []
        for role, original, expected in source_refs:
            held_size = len(mirror_snapshots[(original, expected)].raw)
            records.append(
                {
                    "role": role,
                    "path": key_to_bundle[(original, expected)],
                    "sha256": expected,
                    "size_bytes": held_size,
                    "origin_path_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                    "origin_path_was_absolute": Path(original).is_absolute(),
                }
            )
        records.append(
            {
                "role": "producer_complete_interface_original",
                "path": original_relative,
                "sha256": input_sha,
                "size_bytes": len(input_raw),
                "origin_path_sha256": hashlib.sha256(str(input_interface).encode("utf-8")).hexdigest(),
                "origin_path_was_absolute": input_interface.is_absolute(),
            }
        )
        records.sort(key=lambda row: (row["role"], row["path"], row["sha256"]))
        output["source_files"] = records
        source_projection = _scientific_projection(payload, output_form=False)
        output_projection = _scientific_projection(output, output_form=True)
        require_exact_typed_value(
            output_projection,
            source_projection,
            "adapter restored scientific projection",
        )
        source_digest = digest_json(source_projection)
        output["compatibility_adapter"] = {
            "schema": "monday_fresh_emx_report_interface_compatibility_v8",
            "status": "PASS_DECLARED_MAPPINGS_ONLY",
            "input_schema": INPUT_SCHEMA,
            "output_schema": OUTPUT_SCHEMA,
            "identity_status_mapping": "PASS=>PASS_7298_OF_7298_only_after_exact_counts_and_receipt_binding",
            "stage06_terminalization_mapping": "exact_known_stale_template=>terminal_stage07_stage08_identity_PASS",
            "reporting_label_mapping": f"{SOURCE_REPORTING_LABEL}=>{REPORT_REPORTING_LABEL}",
            "metric_version_mapping": f"{SOURCE_METRIC_VERSION}=>{REPORT_METRIC_VERSION}",
            "fixed_bin_variant_mapping": f"{PRIMARY_FIXED_BIN_VARIANT}=>{REPORT_PRIMARY_VARIANT}",
            "primary_joint_derivation": "paired_rows_fixed_spans_q_floor_numpy_linear_v1",
            "source_scientific_projection_sha256": source_digest,
            "output_restored_scientific_projection_sha256": digest_json(output_projection),
            "numeric_value_or_denominator_mutation_allowed": False,
            "proxy_emx_target_chains_may_be_combined": False,
            "q_floor_is_primary_and_exact_q_is_secondary": True,
            "survivor_metrics_are_original_10000_unconditional": False,
        }
        closure_manifest = {
            "schema": "portable_complete_emx_source_closure_manifest_v1",
            "status": "PASS_SELF_CONTAINED_INTERFACE_BUNDLE_RELATIVE",
            "path_semantics": "relative_to_interface_bundle_root",
            "producer_interface_sha256": input_sha,
            "mirror_manifest_sha256": sha_bytes(mirror_manifest_raw),
            "source_record_count": len(records),
            "discovered_control_source_record_count": len(discovered_refs),
            "source_records_sha256": digest_json(records),
            "records": records,
        }
        closure_raw = canonical_json_bytes(closure_manifest)
        closure_sha = sha_bytes(closure_raw)
        closure_path = "PORTABLE_SOURCE_CLOSURE_MANIFEST.json"
        verify_all_source_continuity()
        output_lease.write_root(closure_path, closure_raw)
        output["portable_source_closure"] = {
            "status": "PASS_SELF_CONTAINED_INTERFACE_BUNDLE_RELATIVE",
            "path_semantics": "relative_to_interface_bundle_root",
            "manifest_path": closure_path,
            "manifest_sha256": closure_sha,
            "source_record_count": len(records),
            "source_records_sha256": digest_json(records),
            "absolute_active_source_dependency_count": 0,
        }
        interface_name = "COMPLETE_EMX_RESULT_INTERFACE_PORTABLE_V8.json"
        interface_raw = canonical_json_bytes(output)
        verify_all_source_continuity()
        output_lease.write_root(interface_name, interface_raw)
        receipt = {
            "schema": "complete_emx_report_interface_adapter_v8_receipt",
            "status": "PASS_AUTHOR_ADAPTER_EXECUTION",
            "input_interface_sha256": input_sha,
            "output_interface_path": interface_name,
            "output_interface_sha256": sha_bytes(interface_raw),
            "closure_manifest_path": closure_path,
            "closure_manifest_sha256": closure_sha,
            "source_record_count": len(records),
            "discovered_control_source_record_count": len(discovered_refs),
            "source_scientific_projection_sha256": source_digest,
            "output_restored_scientific_projection_sha256": digest_json(output_projection),
            "remote_login_performed": False,
            "remote_generation_performed": False,
            "production_chain_executed": False,
        }
        receipt_raw = canonical_json_bytes(receipt)
        verify_all_source_continuity()
        output_lease.write_root("ADAPTER_RECEIPT.json", receipt_raw)
        indexed = {
            interface_name: sha_bytes(interface_raw),
            closure_path: closure_sha,
            "ADAPTER_RECEIPT.json": sha_bytes(receipt_raw),
        }
        index_raw = "".join(f"{sha}  {name}\n" for name, sha in sorted(indexed.items())).encode("utf-8")
        verify_all_source_continuity()
        output_lease.write_root("SHA256SUMS", index_raw)
        output_lease.freeze((*indexed, "SHA256SUMS"), source_names)
        verify_all_source_continuity()
        return receipt
    except Exception:
        if output_lease is not None:
            output_lease.write_failure_marker()
        raise
    finally:
        if output_lease is not None:
            output_lease.close()
        for snapshot in mirror_snapshots.values():
            snapshot.close()
        if mirror_manifest_snapshot is not None:
            mirror_manifest_snapshot.close()
        if input_snapshot is not None:
            input_snapshot.close()
        mirror_lease.close()
        mirror_manifest_lease.close()
        input_lease.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-interface", required=True, type=Path)
    parser.add_argument("--mirror-manifest", required=True, type=Path)
    parser.add_argument("--mirror-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    receipt = adapt_interface(
        args.input_interface,
        args.mirror_manifest,
        args.mirror_root,
        args.output_dir,
    )
    print(json.dumps({"status": receipt["status"], "output_interface_sha256": receipt["output_interface_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
