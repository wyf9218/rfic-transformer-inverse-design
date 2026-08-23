#!/usr/bin/env python3
"""Build an uploadable MARS sync packet for the next-generation S8P flow.

The packet updates the remote repository with the current S8P code, copies the
guarded bootstrap execution runbook, and records hashes for traceability. It
does not log in to MARS, run Cadence/EMX, or claim that any .s8p data exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKET_NAME = "next_gen_s8p_mars_sync_packet_20260619"
EXECUTION_PACKET_NAME = "next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready"
DOC_NAMES = (
    "NEXT_GEN_S8P_REQUIRED_INPUTS_AUDIT_20260616_CN.md",
    "NEXT_GEN_S8P_PHYSICAL_FEATURE_REQUIREMENTS_20260615_CN.md",
    "NEXT_GEN_S8P_CURRENT_STATUS_20260619_CN.md",
    "NEXT_GEN_S8P_OBJECTIVE_COMPLETION_AUDIT_20260619_CN.md",
    "S8P_PORT_MAP_APPROVAL_CHECKLIST_20260619_CN.md",
    "S8P_GEOMETRY_CONTRACT_APPROVAL_CHECKLIST_20260619_CN.md",
    "MARS_TRAINING_ATTEMPT_20260619_CN.md",
    "NEXT_GEN_S8P_MARS_RECOVERY_README_20260619_CN.md",
    "NEXT_MARS_S8P_REGEN_COMMANDS_20260626_CN.md",
    "HFSS_EMX_S8P_VALIDATION_CURRENT_STATUS_20260626_CN.md",
    "LOCAL_S8P_STRUCTURE_PREFLIGHT_20260626_CN.md",
)
RECOVERY_FILE_NAMES = (
    "MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh",
    "NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh",
    "NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh",
    "NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh",
    "NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh",
    "NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh",
    "MARS_S8P_20_AFTER_UNLOCK_20260626.sh",
    "NEXT_GEN_S8P_MARS_RECOVERY_BUNDLE_PASTE_20260619.sh",
    "NEXT_GEN_S8P_MARS_RECOVERY_README_20260619_CN.md",
    "MARS_TRAINING_ATTEMPT_20260619_CN.md",
)
REQUIRED_EXECUTION_PACKET_FILES = (
    "next_gen_s8p_mars_execution.commands.sh",
    "next_gen_s8p_mars_execution_packet_summary.json",
    "next_gen_s8p_required_inputs.json",
    "NEXT_GEN_S8P_MARS_EXECUTION_PACKET_CN.md",
)
REQUIRED_REPO_FILES = (
    "configs/mars_s8p_physical_feature_500_template.yaml",
    "scripts/build_s8p_geometry_bootstrap_candidate_queue.py",
    "scripts/build_physical_feature_s8p_launch_packet.py",
    "scripts/build_next_gen_s8p_mars_execution_packet.py",
    "scripts/build_s8p_port_map_approval_packet.py",
    "scripts/build_s8p_geometry_contract_approval_packet.py",
    "scripts/build_s8p_combined_approval_readiness_packet.py",
    "scripts/discover_mars_emx_cadence_paths.py",
    "scripts/prepare_final_s8p_physical_feature_config.py",
    "scripts/preflight_dataset_config.py",
    "scripts/run_candidate_queue_dataset.py",
    "scripts/run_candidate_queue_dataset_parallel.py",
    "scripts/run_dataset_quality_gates.py",
    "scripts/summarize_next_gen_s8p_mars_run.py",
    "scripts/discover_next_gen_s8p_mars_return.py",
    "scripts/import_next_gen_s8p_mars_return_package.py",
    "scripts/import_latest_s8p_20_pilot_return.py",
    "scripts/watch_s8p_20_pilot_return_and_process.py",
    "scripts/run_gated_s8p_million_sample_campaign.py",
    "scripts/import_stage1_mars_calibration_return.py",
    "scripts/build_next_gen_s8p_objective_acceptance_audit.py",
    "scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py",
    "scripts/build_s8p_final_report_evidence_packet.py",
    "scripts/audit_geometry_quality.py",
    "scripts/audit_power_line_8port_contract.py",
    "scripts/audit_s8p_physical_feature_dataset.py",
    "scripts/audit_s8p_port_pair_physical_candidates.py",
    "scripts/audit_selected_power_line_8port_layout_samples.py",
    "scripts/discover_final_valid_emx_s8p_candidates.py",
    "scripts/export_final_valid_emx_s8p_samples.py",
    "scripts/audit_next_gen_s8p_goal_readiness.py",
    "scripts/build_selected_s8p_hfss_handoff_packet.py",
    "scripts/build_s8p_hfss_aedt_scripts_from_handoff.py",
    "scripts/render_hfss_model_views_from_payload.py",
    "scripts/plan_physical_feature_balanced_acquisition.py",
    "scripts/build_physical_feature_surrogate_candidate_predictions.py",
    "scripts/select_physical_feature_validation_samples.py",
    "scripts/build_physical_feature_inverse_training_table.py",
    "scripts/audit_physical_feature_inverse_model_quality.py",
    "scripts/train_physical_feature_inverse_model.py",
    "scripts/predict_geometry_with_saved_inverse_model.py",
    "scripts/predict_geometry_from_physical_features.py",
    "scripts/derive_scalar_q_feature.py",
    "scripts/visualize_dataset_quality.py",
    "scripts/audit_sampling_distribution.py",
    "scripts/audit_dataset_touchstones.py",
    "scripts/audit_touchstone_transformer.py",
    "scripts/compare_emx_hfss_ads.py",
    "scripts/plot_emx_hfss_ads_style_metrics.py",
    "scripts/package_mars_dataset_run.py",
    "scripts/verify_mars_dataset_package.py",
    "scripts/audit_mars_run_progress.py",
    "rfic_transformer_inverse_design/layout/export.py",
    "rfic_transformer_inverse_design/execution/evaluator.py",
    "rfic_transformer_inverse_design/interfaces/cli.py",
)
OPTIONAL_EVIDENCE_FILES = (
    Path("outputs/s8p_port_order_from_the_best_20260619/the_best_s8p_port_order_reference_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/the_best_s8p_port_order_reference_ports.csv"),
    Path("outputs/s8p_port_order_from_the_best_20260619/THE_BEST_S8P_PORT_ORDER_REFERENCE_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/final_s8p_physical_feature_500_the_best_candidate.yaml"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_port_map_approval_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/S8P_PORT_MAP_APPROVAL_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_ads_python_formula_trace.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_port_map_roles.csv"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_differential_port_pairs.csv"),
    Path(
        "outputs/s8p_port_order_from_the_best_20260619/"
        "physical_feature_s8p_launch_packet_gated_unapproved/"
        "physical_feature_s8p_launch_packet_summary.json"
    ),
    Path("outputs/s8p_port_order_from_the_best_20260619/power_line_8port_contract_audit/power_line_8port_contract_audit_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/power_line_8port_contract_audit/power_line_8port_contract_audit_report.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_candidate/s8p_geometry_contract_approval_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_candidate/S8P_GEOMETRY_CONTRACT_APPROVAL_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/S8P_COMBINED_APPROVAL_READINESS_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_board.png"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/S8P_PORT_MAP_APPROVAL_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_ads_python_formula_trace.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_roles.csv"),
    Path("outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_differential_port_pairs.csv"),
    Path("outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/S8P_GEOMETRY_CONTRACT_APPROVAL_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/S8P_COMBINED_APPROVAL_READINESS_REPORT_CN.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_board.png"),
    Path("outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch.commands.sh"),
    Path("outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_report.md"),
    Path("outputs/next_gen_s8p_goal_readiness_user_approved_ready_20260619/next_gen_s8p_goal_readiness_summary.json"),
    Path("outputs/next_gen_s8p_goal_readiness_user_approved_ready_20260619/next_gen_s8p_goal_readiness_report.md"),
    Path("outputs/next_gen_s8p_goal_readiness_user_approved_ready_20260619/next_gen_s8p_goal_readiness_evidence.csv"),
    Path("outputs/next_gen_s8p_objective_acceptance_current_20260620/next_gen_s8p_objective_acceptance_summary.json"),
    Path("outputs/next_gen_s8p_objective_acceptance_current_20260620/NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"),
    Path("outputs/next_gen_s8p_objective_acceptance_current_20260620/next_gen_s8p_objective_acceptance_evidence.csv"),
    Path("outputs/next_gen_s8p_goal_readiness_clearance_current_20260620/next_gen_s8p_goal_readiness_summary.json"),
    Path("outputs/next_gen_s8p_goal_readiness_clearance_current_20260620/next_gen_s8p_goal_readiness_report.md"),
    Path("outputs/next_gen_s8p_goal_readiness_clearance_current_20260620/next_gen_s8p_goal_readiness_evidence.csv"),
    Path("outputs/next_gen_s8p_objective_acceptance_clearance_current_20260620/next_gen_s8p_objective_acceptance_summary.json"),
    Path("outputs/next_gen_s8p_objective_acceptance_clearance_current_20260620/NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"),
    Path("outputs/next_gen_s8p_objective_acceptance_clearance_current_20260620/next_gen_s8p_objective_acceptance_evidence.csv"),
    Path("outputs/next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/physical_feature_s8p_launch_packet_aligned_local_20260620/physical_feature_s8p_launch_packet_summary.json"),
    Path("outputs/next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/physical_feature_s8p_launch_packet_aligned_local_20260620/physical_feature_s8p_launch_packet_report.md"),
    Path("outputs/next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/physical_feature_s8p_launch_packet_aligned_local_20260620/physical_feature_s8p_launch.commands.sh"),
    Path("outputs/next_gen_s8p_goal_readiness_aligned_current_20260620/next_gen_s8p_goal_readiness_summary.json"),
    Path("outputs/next_gen_s8p_goal_readiness_aligned_current_20260620/next_gen_s8p_goal_readiness_report.md"),
    Path("outputs/next_gen_s8p_goal_readiness_aligned_current_20260620/next_gen_s8p_goal_readiness_evidence.csv"),
    Path("outputs/next_gen_s8p_objective_acceptance_aligned_current_20260620/next_gen_s8p_objective_acceptance_summary.json"),
    Path("outputs/next_gen_s8p_objective_acceptance_aligned_current_20260620/NEXT_GEN_S8P_OBJECTIVE_ACCEPTANCE_AUDIT_CN.md"),
    Path("outputs/next_gen_s8p_objective_acceptance_aligned_current_20260620/next_gen_s8p_objective_acceptance_evidence.csv"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/candidate_queue_dataset_summary.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/dataset_manifest.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/dataset_rows.csv"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/evaluations/c4ea3c929a89d3df/summary.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/evaluations/c4ea3c929a89d3df/layout/power_line_8port_geometry.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/evaluations/c4ea3c929a89d3df/layout/transformer_layout_preview.png"),
    Path("outputs/clearance_fix_local_create_only_20260620/create_only_one/evaluations/c4ea3c929a89d3df/layout/transformer_port_debug.png"),
    Path("outputs/clearance_fix_local_create_only_20260620/geometry_audit_one/geometry_quality_audit_summary.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/geometry_audit_one/geometry_quality_audit_report.md"),
    Path("outputs/clearance_fix_local_create_only_20260620/preflight_contract/power_line_8port_contract_audit_summary.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/preflight_contract/power_line_8port_contract_audit_report.md"),
    Path("outputs/clearance_fix_local_create_only_20260620/selected_layout_audit_one/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/clearance_fix_local_create_only_20260620/selected_layout_audit_one/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/clearance_fix_local_create_only_20260620/selected_layout_audit_one/selected_power_line_8port_layout_audit_checks.csv"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/candidate_queue_dataset_summary.json"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/dataset_manifest.json"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/dataset_rows.csv"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/evaluations/cef232f3625e7ad8/summary.json"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/evaluations/cef232f3625e7ad8/layout/power_line_8port_geometry.json"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/evaluations/cef232f3625e7ad8/layout/transformer_layout_preview.png"),
    Path("outputs/current_local_create_only_gate_20260626/create_only_one/evaluations/cef232f3625e7ad8/layout/transformer_port_debug.png"),
    Path("outputs/current_local_create_only_gate_20260626/selected_layout_audit_one/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/current_local_create_only_gate_20260626/selected_layout_audit_one/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/current_local_create_only_gate_20260626/selected_layout_audit_one/selected_power_line_8port_layout_audit_checks.csv"),
    Path("outputs/watch_s8p_20_pilot_return_current/watch_s8p_20_pilot_return_summary.json"),
    Path("outputs/watch_s8p_20_pilot_return_current/WATCH_S8P_20_PILOT_RETURN_REPORT_CN.md"),
    Path("outputs/s8p_port_map_approval_visual_packet_20260619/S8P_PORT_MAP_APPROVAL_VISUAL_PACKET_CN.md"),
    Path("outputs/s8p_port_map_approval_visual_packet_20260619/s8p_port_map_approval_visual_packet.png"),
    Path("outputs/s8p_port_map_approval_visual_packet_20260619/s8p_port_map_approval_visual_packet_summary.json"),
    Path("outputs/s8p_port_map_approval_visual_packet_20260619/source_transformer_layout_preview.png"),
    Path("outputs/s8p_port_map_approval_visual_packet_20260619/source_transformer_port_debug.png"),
    Path("outputs/s8p_safety_gate_verification_20260619/unapproved_after_approval_guard.exit_code.txt"),
    Path("outputs/s8p_safety_gate_verification_20260619/unapproved_after_approval_guard.stderr.txt"),
    Path("outputs/s8p_safety_gate_verification_20260619/strict_path_preflight_current_candidate_summary.json"),
    Path("outputs/s8p_safety_gate_verification_20260619/strict_path_preflight_current_candidate_report.md"),
    Path("outputs/s8p_mars_path_guard_verification_20260619/01_discovery_rejects_dryrun/mars_emx_cadence_path_discovery_summary.json"),
    Path("outputs/s8p_mars_path_guard_verification_20260619/01_discovery_rejects_dryrun/mars_emx_cadence_path_discovery_report.md"),
    Path("outputs/s8p_mars_path_guard_verification_20260619/02_final_config_rejects_missing_real_emx/final_s8p_physical_feature_config_summary.json"),
    Path("outputs/s8p_mars_path_guard_verification_20260619/02_final_config_rejects_missing_real_emx/final_s8p_physical_feature_config_report.md"),
    Path("outputs/current_s8p_power_line_structure_preview_fixed_20260616/evaluations/3d62f49583790ca9/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_fixed_20260616/evaluations/3d62f49583790ca9/layout/transformer_layout_annotated_8port_evidence_fixed.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_valid_bounds/evaluations/8815ba1d71eb5022/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_valid_bounds/evaluations/8815ba1d71eb5022/layout/transformer_layout_annotated_physical_lr_8port_evidence.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_valid_bounds/evaluations/8815ba1d71eb5022/layout/transformer_port_debug.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_valid_bounds/evaluations/8815ba1d71eb5022/layout/power_line_8port_geometry.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_offset70_visual_check/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_offset70_visual_check/evaluations/4ab97f2c4cf6c05f/layout/transformer_port_debug.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_offset70_visual_check/evaluations/4ab97f2c4cf6c05f/layout/power_line_8port_geometry.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_offset70_visual_check/selected_layout_audit/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_offset70_visual_check/selected_layout_audit/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview_labeled_roles.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_port_debug.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/evaluations/4ab97f2c4cf6c05f/layout/power_line_8port_geometry.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/selected_layout_audit/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_current_labeled_recheck/selected_layout_audit/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview_inside_shield_labeled_roles.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/evaluations/4ab97f2c4cf6c05f/layout/transformer_port_debug.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/evaluations/4ab97f2c4cf6c05f/layout/power_line_8port_geometry.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/selected_layout_audit/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck/selected_layout_audit/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview_topology_locked_labeled_roles.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/evaluations/4ab97f2c4cf6c05f/layout/transformer_layout_preview.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/evaluations/4ab97f2c4cf6c05f/layout/transformer_port_debug.png"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/evaluations/4ab97f2c4cf6c05f/layout/power_line_8port_geometry.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/selected_layout_audit/selected_power_line_8port_layout_audit_summary.json"),
    Path("outputs/current_s8p_power_line_structure_preview_physical_lr_20260616/create_only_inside_shield_recheck_topology_locked/selected_layout_audit/selected_power_line_8port_layout_audit_report.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/readiness_dryrun/next_gen_s8p_goal_readiness_summary.json"),
    Path("outputs/s8p_port_order_from_the_best_20260619/readiness_dryrun/next_gen_s8p_goal_readiness_report.md"),
    Path("outputs/s8p_port_order_from_the_best_20260619/readiness_dryrun/next_gen_s8p_goal_readiness_evidence.csv"),
    Path("reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz"),
    Path("reports/s8p_shared_line_width_mars_evidence_20260622/calibration_execution_packet_stage1_wideband_20260626.tar.gz.sha256"),
)
FIXED_TAR_MTIME = int(datetime(2026, 6, 19, tzinfo=timezone.utc).timestamp())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    packet_dir = Path(args.packet_dir).expanduser().resolve()
    tar_path = Path(args.tar_path).expanduser().resolve()
    bootstrap_path = Path(args.bootstrap_path).expanduser().resolve()
    report_dir = Path(args.report_dir).expanduser().resolve()

    execution_packet_dir = Path(args.execution_packet_dir).expanduser().resolve()
    execution_packet_name = execution_packet_dir.name
    _validate_inputs(repo_root, project_root, execution_packet_dir, execution_packet_name)
    if packet_dir.exists() and any(packet_dir.iterdir()):
        if not args.force:
            raise SystemExit(f"Packet directory is not empty; pass --force to replace: {packet_dir}")
        shutil.rmtree(packet_dir)
    packet_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    _copy_repo_snapshot(repo_root, packet_dir, copied)
    _copy_execution_packet(execution_packet_dir, packet_dir, copied, execution_packet_name)
    _copy_docs(project_root, packet_dir, copied)
    _copy_recovery_files(project_root, packet_dir, copied)
    _copy_optional_evidence(project_root, packet_dir, copied)
    _write_installer(packet_dir, copied, execution_packet_name)
    _write_readme(packet_dir, copied)
    _write_inventory(packet_dir, repo_root, project_root, copied, execution_packet_name)
    _write_sha_manifest(packet_dir)

    tar_path.parent.mkdir(parents=True, exist_ok=True)
    _write_deterministic_tar(tar_path, packet_dir)
    tar_sha_path = tar_path.with_suffix(tar_path.suffix + ".sha256")
    tar_sha_path.write_text(f"{_sha256(tar_path)}  {tar_path.name}\n", encoding="utf-8")
    _write_bootstrap(bootstrap_path, tar_path.name, packet_dir.name)
    bootstrap_sha_path = bootstrap_path.with_suffix(bootstrap_path.suffix + ".sha256")
    bootstrap_sha_path.write_text(f"{_sha256(bootstrap_path)}  {bootstrap_path.name}\n", encoding="utf-8")

    summary = _build_summary(
        repo_root=repo_root,
        project_root=project_root,
        execution_packet_dir=execution_packet_dir,
        packet_dir=packet_dir,
        tar_path=tar_path,
        tar_sha_path=tar_sha_path,
        bootstrap_path=bootstrap_path,
        bootstrap_sha_path=bootstrap_sha_path,
        copied=copied,
        execution_packet_name=execution_packet_name,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path = report_dir / "next_gen_s8p_mars_sync_packet_summary_20260619.json"
    report_path = report_dir / "NEXT_GEN_S8P_MARS_SYNC_PACKET_20260619_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"status={summary['status']}")
    print(f"decision={summary['decision']}")
    print(f"packet_dir={packet_dir}")
    print(f"tar_path={tar_path}")
    print(f"tar_sha_path={tar_sha_path}")
    print(f"bootstrap_path={bootstrap_path}")
    print(f"bootstrap_sha_path={bootstrap_sha_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_repo = Path(__file__).resolve().parents[1]
    default_project = default_repo.parent
    default_packet = default_project / PACKET_NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(default_repo))
    parser.add_argument("--project-root", default=str(default_project))
    parser.add_argument("--packet-dir", default=str(default_packet))
    parser.add_argument("--tar-path", default=str(default_project / f"{PACKET_NAME}.tar.gz"))
    parser.add_argument("--bootstrap-path", default=str(default_project / f"{PACKET_NAME}_BOOTSTRAP.sh"))
    parser.add_argument(
        "--execution-packet-dir",
        default=str(default_project / "outputs" / EXECUTION_PACKET_NAME),
    )
    parser.add_argument("--report-dir", default=str(default_project / "reports" / "next_gen_s8p_sync_packet_20260619"))
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _validate_inputs(repo_root: Path, project_root: Path, execution_dir: Path, execution_packet_name: str) -> None:
    if not repo_root.is_dir():
        raise SystemExit(f"repo root not found: {repo_root}")
    if not project_root.is_dir():
        raise SystemExit(f"project root not found: {project_root}")
    missing = [rel for rel in REQUIRED_REPO_FILES if not (repo_root / rel).is_file()]
    missing.extend(
        f"{execution_packet_name}/{rel}"
        for rel in REQUIRED_EXECUTION_PACKET_FILES
        if not (execution_dir / rel).is_file()
    )
    if missing:
        raise SystemExit("Required next-gen S8P sync inputs are missing:\n" + "\n".join(f"- {item}" for item in missing))


def _copy_repo_snapshot(repo_root: Path, packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    repo_dest = packet_dir / "files" / "repo"
    _copy_tree(repo_root / "scripts", repo_dest / "scripts", packet_dir, copied, suffixes={".py"})
    _copy_tree(repo_root / "configs", repo_dest / "configs", packet_dir, copied, suffixes=None)
    _copy_tree(repo_root / "rfic_transformer_inverse_design", repo_dest / "rfic_transformer_inverse_design", packet_dir, copied, suffixes={".py"})
    for name in ("pyproject.toml",):
        source = repo_root / name
        if source.is_file():
            _copy_file(source, repo_dest / name, packet_dir, copied)


def _copy_execution_packet(source_dir: Path, packet_dir: Path, copied: list[dict[str, Any]], execution_packet_name: str) -> None:
    target_dir = packet_dir / "files" / "project_runbooks" / execution_packet_name
    _copy_tree(source_dir, target_dir, packet_dir, copied, suffixes=None)
    for shell_path in target_dir.rglob("*.sh"):
        shell_path.chmod(shell_path.stat().st_mode | stat.S_IXUSR)


def _copy_docs(project_root: Path, packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    target_dir = packet_dir / "files" / "project_docs"
    for name in DOC_NAMES:
        source = project_root / name
        if source.is_file():
            _copy_file(source, target_dir / name, packet_dir, copied)


def _copy_recovery_files(project_root: Path, packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    target_dir = packet_dir / "files" / "project_recovery"
    for name in RECOVERY_FILE_NAMES:
        source = project_root / name
        if source.is_file():
            _copy_file(source, target_dir / name, packet_dir, copied)
            if source.suffix == ".sh":
                (target_dir / name).chmod((target_dir / name).stat().st_mode | stat.S_IXUSR)


def _copy_optional_evidence(project_root: Path, packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    target_dir = packet_dir / "files" / "evidence"
    for rel in OPTIONAL_EVIDENCE_FILES:
        source = project_root / rel
        if source.is_file():
            _copy_file(source, target_dir / rel, packet_dir, copied)


def _copy_tree(source_dir: Path, target_dir: Path, packet_dir: Path, copied: list[dict[str, Any]], *, suffixes: set[str] | None) -> None:
    if not source_dir.is_dir():
        raise SystemExit(f"source directory not found: {source_dir}")
    for source in sorted(source_dir.rglob("*")):
        if not source.is_file():
            continue
        if _is_ignored(source):
            continue
        if suffixes is not None and source.suffix not in suffixes:
            continue
        rel = source.relative_to(source_dir)
        _copy_file(source, target_dir / rel, packet_dir, copied)


def _copy_file(source: Path, target: Path, packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    mode = source.stat().st_mode
    if mode & stat.S_IXUSR:
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    copied.append(_record_file(target, packet_dir))


def _is_ignored(path: Path) -> bool:
    parts = set(path.parts)
    return "__pycache__" in parts or ".git" in parts or path.suffix in {".pyc", ".pyo"}


def _write_installer(packet_dir: Path, copied: list[dict[str, Any]], execution_packet_name: str) -> None:
    installer = packet_dir / "INSTALL_ON_MARS.sh"
    installer.write_text(_installer_text(execution_packet_name), encoding="utf-8")
    installer.chmod(0o755)
    copied.append(_record_file(installer, packet_dir))


def _installer_text(execution_packet_name: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Run from the extracted {PACKET_NAME} directory on MARS.
# It syncs local next-gen S8P source/runbooks into the remote repo. It does not
# start EMX. Keep RUN_EMX=0 until the port map and geometry contract are
# approved and strict real-path preflight passes on MARS.

PACKET_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

locate_project() {{
  if [[ -n "${{PROJECT:-}}" && -d "${{PROJECT}}" ]]; then
    printf '%s\\n' "${{PROJECT}}"
    return 0
  fi
  for candidate in \\
    "$PWD" \\
    "$PWD/rfic-transformer-inverse-design" \\
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \\
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \\
    "/shared/research/${{USER:-researcher}}/rfic-transformer-inverse-design" \\
    "/shared/research/${{USER:-researcher}}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      printf '%s\\n' "$candidate"
      return 0
    fi
  done
  if command -v find >/dev/null 2>&1 && [[ -d /shared/research ]]; then
    found="$(find /shared/research -maxdepth 5 -type d -name rfic-transformer-inverse-design 2>/dev/null | head -n 1 || true)"
    if [[ -n "$found" && -f "$found/pyproject.toml" ]]; then
      printf '%s\\n' "$found"
      return 0
    fi
  fi
  return 1
}}

default_project_dir() {{
  local user_name="${{USER:-researcher}}"
  if [[ -d "/shared/research/${{user_name}}" ]]; then
    printf '%s\\n' "/shared/research/${{user_name}}/rfic-transformer-inverse-design"
  else
    printf '%s\\n' "$HOME/rfic-transformer-inverse-design"
  fi
}}

bootstrap_project() {{
  local target="${{PROJECT:-$(default_project_dir)}}"
  if [[ -e "$target" && ! -d "$target" ]]; then
    echo "ERROR: PROJECT target exists but is not a directory: $target" >&2
    exit 2
  fi
  mkdir -p "$target"
  printf '%s\\n' "$target"
}}

PROJECT_DIR="$(locate_project || true)"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(bootstrap_project)"
  echo "NEXT_GEN_S8P_BOOTSTRAPPED_PROJECT=$PROJECT_DIR"
else
  echo "NEXT_GEN_S8P_FOUND_PROJECT=$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
mkdir -p scripts configs rfic_transformer_inverse_design
cp -R "$PACKET_DIR/files/repo/scripts/." scripts/
cp -R "$PACKET_DIR/files/repo/configs/." configs/
cp -R "$PACKET_DIR/files/repo/rfic_transformer_inverse_design/." rfic_transformer_inverse_design/
if [[ -f "$PACKET_DIR/files/repo/pyproject.toml" ]]; then
  cp "$PACKET_DIR/files/repo/pyproject.toml" pyproject.toml
fi
mkdir -p "{execution_packet_name}"
cp -R "$PACKET_DIR/files/project_runbooks/{execution_packet_name}/." "{execution_packet_name}/"
mkdir -p next_gen_s8p_docs_20260619 next_gen_s8p_evidence_20260619 outputs
if [[ -d "$PACKET_DIR/files/project_docs" ]]; then
  cp -R "$PACKET_DIR/files/project_docs/." next_gen_s8p_docs_20260619/
fi
if [[ -d "$PACKET_DIR/files/project_recovery" ]]; then
  cp -R "$PACKET_DIR/files/project_recovery/." ./
  chmod +x MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh 2>/dev/null || true
fi
if [[ -d "$PACKET_DIR/files/evidence" ]]; then
  cp -R "$PACKET_DIR/files/evidence/." next_gen_s8p_evidence_20260619/
fi
if [[ -d "$PACKET_DIR/files/evidence/outputs" ]]; then
  cp -R "$PACKET_DIR/files/evidence/outputs/." outputs/
fi
RUNBOOK="{execution_packet_name}/next_gen_s8p_mars_execution.commands.sh"
if [[ -f "$RUNBOOK" ]]; then
  "${{PYTHON:-python3}}" - "$RUNBOOK" <<'PY'
from pathlib import Path
import sys

runbook = Path(sys.argv[1])
text = runbook.read_text(encoding="utf-8")
text = text.replace("/home/researcher/Documents/模拟变压器AI反向建模/outputs/", "outputs/")
runbook.write_text(text, encoding="utf-8")
PY
fi

chmod +x scripts/*.py "{execution_packet_name}/next_gen_s8p_mars_execution.commands.sh"

STAMP="$(date +%Y%m%d_%H%M%S)"
MANIFEST="{execution_packet_name}/installed_next_gen_s8p_sync_${{STAMP}}.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum \\
    scripts/build_next_gen_s8p_mars_execution_packet.py \\
    scripts/build_physical_feature_s8p_launch_packet.py \\
    scripts/train_physical_feature_inverse_model.py \\
    scripts/predict_geometry_with_saved_inverse_model.py \\
    scripts/build_s8p_geometry_bootstrap_candidate_queue.py \\
    scripts/build_s8p_geometry_contract_approval_packet.py \\
    scripts/build_s8p_combined_approval_readiness_packet.py \\
    scripts/discover_mars_emx_cadence_paths.py \\
    scripts/prepare_final_s8p_physical_feature_config.py \\
    scripts/discover_final_valid_emx_s8p_candidates.py \\
    rfic_transformer_inverse_design/layout/export.py \\
    "{execution_packet_name}/next_gen_s8p_mars_execution.commands.sh" \\
    | tee "$MANIFEST"
else
  shasum -a 256 \\
    scripts/build_next_gen_s8p_mars_execution_packet.py \\
    scripts/build_physical_feature_s8p_launch_packet.py \\
    scripts/train_physical_feature_inverse_model.py \\
    scripts/predict_geometry_with_saved_inverse_model.py \\
    scripts/build_s8p_geometry_bootstrap_candidate_queue.py \\
    scripts/build_s8p_geometry_contract_approval_packet.py \\
    scripts/build_s8p_combined_approval_readiness_packet.py \\
    scripts/discover_mars_emx_cadence_paths.py \\
    scripts/prepare_final_s8p_physical_feature_config.py \\
    scripts/discover_final_valid_emx_s8p_candidates.py \\
    rfic_transformer_inverse_design/layout/export.py \\
    "{execution_packet_name}/next_gen_s8p_mars_execution.commands.sh" \\
    | tee "$MANIFEST"
fi

echo "NEXT_GEN_S8P_SYNC_PROJECT=$PROJECT_DIR"
echo "NEXT_GEN_S8P_SYNC_MANIFEST=$PROJECT_DIR/$MANIFEST"
echo "NEXT_GEN_S8P_STRUCTURE_EVIDENCE=$PROJECT_DIR/next_gen_s8p_evidence_20260619"
echo "NEXT_GEN_S8P_SYNCED_OUTPUTS=$PROJECT_DIR/outputs"
echo "NEXT_GEN_S8P_POST_LOGIN=$PROJECT_DIR/NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh"
echo "NEXT_GEN_S8P_START_CURRENT=$PROJECT_DIR/NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh"
echo "NEXT_GEN_S8P_RECOVERY_LAUNCH=$PROJECT_DIR/NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh"
echo "NEXT_GEN_S8P_STATUS_CHECK=$PROJECT_DIR/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh"
echo "Next dry-run command:"
echo "  cd '$PROJECT_DIR' && bash {execution_packet_name}/next_gen_s8p_mars_execution.commands.sh"
echo "Approved real EMX launch command:"
echo "  cd '$PROJECT_DIR' && RUN_EMX=1 bash {execution_packet_name}/next_gen_s8p_mars_execution.commands.sh"
echo "Current 56-point grounded-tap pilot launcher:"
echo "  cd '$PROJECT_DIR' && bash MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh"
echo "Visible-terminal combined entry point:"
echo "  cd '$PROJECT_DIR' && bash NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh"
echo "Current packet one-step launcher:"
echo "  cd '/shared/research/researcher/codex_next_gen_s8p_ssh_20260620' && bash '$PROJECT_DIR/NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh'"
"""


def _write_readme(packet_dir: Path, copied: list[dict[str, Any]]) -> None:
    readme = packet_dir / "README_CN.md"
    packet_name = packet_dir.name
    readme.write_text(
        f"""# Next-Gen S8P MARS Sync Packet

这个包用于把本地最新的 S8P 结构生成、受控 EMX 队列、HFSS handoff、HFSS post-run 验证脚本、端口审批材料和路径安全门控同步到 MARS。

`INSTALL_ON_MARS.sh` 只做同步和可追溯安装，不会自动启动 Cadence/EMX/HFSS：

1. 解压 `{packet_name}.tar.gz`。
2. 运行 `bash {packet_name}/INSTALL_ON_MARS.sh`。
3. 真实 EMX 使用安装脚本输出的当前 execution packet 命令：`RUN_EMX=1 bash <execution_packet>/next_gen_s8p_mars_execution.commands.sh`。
4. 如果在可见 MARS terminal 中希望一键安装并启动当前批准版流程，可运行：`bash NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh` 或 `bash NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh`。
5. 只检查不启动真实 EMX 时：`RUN_REAL_EMX=0 bash NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh`。

关键边界：

	- 当前包包含 user-approved port-map 和 geometry-contract summaries；该批准来自用户本轮“调好后直接用 MARS 训练”的指令，不代表真实 EMX/HFSS 已完成。
	- 当前包包含可见终端入口和恢复脚本：`MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh`、`NEXT_GEN_S8P_MARS_START_CURRENT_20260620.sh`、`NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh`、`NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh`、`NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh`、`MARS_S8P_20_AFTER_UNLOCK_20260626.sh` 和 `NEXT_GEN_S8P_MARS_RECOVERY_BUNDLE_PASTE_20260619.sh`。
	- 当前 56 点 pilot 合同：5-60 GHz inclusive、1 GHz step、56 points；`.s8p` 保留 P1/P4/P5/P6 为 RF signal ports，P2/P3/P7/P8 在物理特征提取中 AC-ground。
	- 安装脚本本身仍不启动 `RUN_EMX=1`；真实启动必须显式运行当前 execution packet 命令。
	- 当前 execution packet 会先做 strict real-path preflight 和 one-sample layout smoke/audit，再在 `RUN_EMX=1` 时启动配置数量的真实 EMX 队列。
- 这个同步包不能证明任何 .s8p 已生成，也不能证明 HFSS/EMX 误差在 5% 内；这些要等 MARS 和 HFSS 真实输出后再由后续 gate 判断。
""",
        encoding="utf-8",
    )
    copied.append(_record_file(readme, packet_dir))


def _write_inventory(
    packet_dir: Path,
    repo_root: Path,
    project_root: Path,
    copied: list[dict[str, Any]],
    execution_packet_name: str,
) -> None:
    inventory = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packet_name": packet_dir.name,
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "execution_packet_name": execution_packet_name,
        "file_count_before_inventory_and_sha": len(copied),
        "files": copied,
        "limitations": [
            "This packet syncs code and runbooks only.",
            "It does not prove MARS installation, EMX execution, HFSS export, or EMX/HFSS agreement.",
            "RUN_EMX remains guarded in the copied execution command.",
            "The current synced port-map and geometry summaries are user-approved for run preparation, but RUN_EMX remains guarded until MARS strict real-path preflight passes.",
            "Recovery launch/status helper scripts are copied when present, but they are not evidence that MARS has run them.",
        ],
    }
    path = packet_dir / "NEXT_GEN_S8P_MARS_SYNC_INVENTORY_20260619.json"
    path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_sha_manifest(packet_dir: Path) -> None:
    files = sorted(path for path in packet_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    with (packet_dir / "SHA256SUMS.txt").open("w", encoding="utf-8") as handle:
        for path in files:
            handle.write(f"{_sha256(path)}  ./{path.relative_to(packet_dir).as_posix()}\n")


def _write_deterministic_tar(tar_path: Path, packet_dir: Path) -> None:
    with tarfile.open(tar_path, "w:gz") as tar:
        for path in sorted(packet_dir.rglob("*")):
            arcname = packet_dir.name / path.relative_to(packet_dir) if isinstance(packet_dir.name, Path) else f"{packet_dir.name}/{path.relative_to(packet_dir).as_posix()}"
            info = tar.gettarinfo(str(path), arcname=str(arcname))
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = FIXED_TAR_MTIME
            if path.is_file():
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            else:
                tar.addfile(info)


def _write_bootstrap(path: Path, tar_name: str, packet_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
base64 -d {tar_name}.b64 > {tar_name}
tar -xzf {tar_name}
bash {packet_name}/INSTALL_ON_MARS.sh
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _build_summary(
    *,
    repo_root: Path,
    project_root: Path,
    execution_packet_dir: Path,
    packet_dir: Path,
    tar_path: Path,
    tar_sha_path: Path,
    bootstrap_path: Path,
    bootstrap_sha_path: Path,
    copied: list[dict[str, Any]],
    execution_packet_name: str,
) -> dict[str, Any]:
    checks = _verify_packet(packet_dir, tar_path, tar_sha_path, bootstrap_path, bootstrap_sha_path, execution_packet_name)
    status = "PASS" if all(check["pass"] for check in checks) else "FAIL"
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "decision": "NEXT_GEN_S8P_MARS_SYNC_PACKET_READY" if status == "PASS" else "NEXT_GEN_S8P_MARS_SYNC_PACKET_NOT_READY",
        "repo_root": str(repo_root),
        "project_root": str(project_root),
        "execution_packet_dir": str(execution_packet_dir),
        "packet_dir": str(packet_dir),
        "tar_path": str(tar_path),
        "tar_sha_path": str(tar_sha_path),
        "bootstrap_path": str(bootstrap_path),
        "bootstrap_sha_path": str(bootstrap_sha_path),
        "copied_file_count": len(copied),
        "checks": checks,
        "next_commands": [
            f"bash {packet_dir.name}/INSTALL_ON_MARS.sh",
            f"bash {execution_packet_name}/next_gen_s8p_mars_execution.commands.sh",
            "bash NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh",
            "bash NEXT_GEN_S8P_MARS_RECOVERY_LAUNCH_20260619.sh",
        ],
        "limitations": [
            "This is upload/install preparation only.",
            "The current packet syncs user-approved port-map and geometry-contract summaries for MARS run preparation.",
            "Real completion still requires 500 EMX .s8p files, random HFSS validation, and <=5% Lp/Ls/Q/K/Kw curve comparison.",
        ],
    }


def _verify_packet(
    packet_dir: Path,
    tar_path: Path,
    tar_sha_path: Path,
    bootstrap_path: Path,
    bootstrap_sha_path: Path,
    execution_packet_name: str,
) -> list[dict[str, Any]]:
    required = [
        "README_CN.md",
        "INSTALL_ON_MARS.sh",
        "SHA256SUMS.txt",
        "NEXT_GEN_S8P_MARS_SYNC_INVENTORY_20260619.json",
        f"files/project_runbooks/{execution_packet_name}/next_gen_s8p_mars_execution.commands.sh",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_candidate/s8p_port_map_approval_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_candidate/s8p_geometry_contract_approval_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_candidate/s8p_combined_approval_readiness_board.png",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_board.png",
        "files/evidence/outputs/s8p_port_order_from_the_best_20260619/physical_feature_s8p_launch_packet_user_approved_ready/physical_feature_s8p_launch_packet_summary.json",
        "files/evidence/outputs/next_gen_s8p_goal_readiness_user_approved_ready_20260619/next_gen_s8p_goal_readiness_summary.json",
        "files/evidence/outputs/s8p_mars_path_guard_verification_20260619/01_discovery_rejects_dryrun/mars_emx_cadence_path_discovery_summary.json",
    ]
    required.extend(f"files/repo/{rel}" for rel in REQUIRED_REPO_FILES)
    checks: list[dict[str, Any]] = []
    checks.append(_check("packet_dir_exists", packet_dir.is_dir(), str(packet_dir)))
    for rel in required:
        checks.append(_check(f"packet_file_exists:{rel}", (packet_dir / rel).is_file(), rel))
    checks.append(_check("installer_is_executable", _is_executable(packet_dir / "INSTALL_ON_MARS.sh"), str(packet_dir / "INSTALL_ON_MARS.sh")))
    checks.extend(_verify_sha_manifest(packet_dir))
    checks.extend(_verify_file_sha(tar_path, tar_sha_path, "tarball"))
    checks.extend(_verify_file_sha(bootstrap_path, bootstrap_sha_path, "bootstrap"))
    if tar_path.is_file():
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                names = set(tar.getnames())
            missing = [f"{packet_dir.name}/{rel}" for rel in required if f"{packet_dir.name}/{rel}" not in names]
            checks.append(_check("tarball_contains_required_files", not missing, missing))
        except Exception as exc:  # noqa: BLE001 - exact failure is useful evidence.
            checks.append(_check("tarball_readable", False, f"{type(exc).__name__}: {exc}"))
    return checks


def _verify_sha_manifest(packet_dir: Path) -> list[dict[str, Any]]:
    sha_path = packet_dir / "SHA256SUMS.txt"
    if not sha_path.is_file():
        return [_check("sha256sums_present", False, str(sha_path))]
    checks = [_check("sha256sums_present", True, str(sha_path))]
    for line in sha_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, rel = line.split(maxsplit=1)
        rel = rel.strip().lstrip("*")
        if rel.startswith("./"):
            rel = rel[2:]
        path = packet_dir / rel
        checks.append(_check(f"sha256_matches:{rel}", path.is_file() and _sha256(path) == expected, rel))
    return checks


def _verify_file_sha(path: Path, sha_path: Path, label: str) -> list[dict[str, Any]]:
    checks = [
        _check(f"{label}_exists", path.is_file(), str(path)),
        _check(f"{label}_sha_file_exists", sha_path.is_file(), str(sha_path)),
    ]
    if path.is_file() and sha_path.is_file():
        expected = sha_path.read_text(encoding="utf-8").strip().split()[0]
        checks.append(_check(f"{label}_sha256_matches", _sha256(path) == expected, {"path": str(path), "expected": expected}))
    return checks


def _record_file(path: Path, packet_dir: Path) -> dict[str, Any]:
    return {
        "relative_path": path.relative_to(packet_dir).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_executable(path: Path) -> bool:
    return path.is_file() and bool(path.stat().st_mode & os.X_OK)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Gen S8P MARS Sync Packet",
        "",
        f"- Status: **{summary['status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Packet dir: `{summary['packet_dir']}`",
        f"- Tar: `{summary['tar_path']}`",
        f"- Bootstrap: `{summary['bootstrap_path']}`",
        f"- Copied files: `{summary['copied_file_count']}`",
        "",
        "## Next Commands",
        "",
    ]
    lines.extend(f"- `{command}`" for command in summary["next_commands"])
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
