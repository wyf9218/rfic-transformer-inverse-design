#!/usr/bin/env bash
set -euo pipefail

# Local-only readiness audit for the active 1M MARS56 objective.
#
# This does not connect to MARS and does not claim the goal is complete.
# It checks whether the local automation can prove, after Duo/remote access:
#   - 1,000,000 formal production .s4p rows,
#   - 10 formal 100k chunks,
#   - a model/physical checkpoint after every 100k chunk,
#   - cumulative checkpoints at 100k, 200k, ..., 1000k,
#   - Lp/Ls/Q/|K| range + 4D uniformity gates in those checkpoints,
#   - transcript-level evidence that those remote runs can be summarized later,
#   - a continuous post-Duo watcher can keep checking/reporting without manual
#     intervention at every 100k boundary.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
OUT="${OUT:-$ROOT_DIR/reports/mars56_1m_goal_readiness_local_audit_20260707.json}"

python3 - "$ROOT_DIR" "$OUT" <<'PY'
from pathlib import Path
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1])
out = Path(sys.argv[2])

def read(rel):
    path = root / rel
    return path.read_text(errors="replace") if path.exists() else None

def bash_n(rel):
    path = root / rel
    if not path.exists():
        return False, "missing"
    completed = subprocess.run(
        ["bash", "-n", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0:
        return True, "bash_n_pass"
    return False, completed.stderr.strip() or f"rc={completed.returncode}"

def has_all(text, tokens):
    missing = [token for token in tokens if token not in text]
    return not missing, missing

def check_script(rel, tokens, syntax=True):
    text = read(rel)
    if text is None:
        return {
            "status": "FAIL",
            "file": str(root / rel),
            "reason": "missing file",
            "missing_tokens": tokens,
            "bash_n": "not_run",
        }
    ok_tokens, missing = has_all(text, tokens)
    ok_syntax, syntax_detail = (True, "not_requested")
    if syntax:
        ok_syntax, syntax_detail = bash_n(rel)
    return {
        "status": "PASS" if ok_tokens and ok_syntax else "FAIL",
        "file": str(root / rel),
        "missing_tokens": missing,
        "bash_n": syntax_detail,
    }

entrypoints = [
    "RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh",
    "CHECK_LOCAL_CHECKPOINT_PROOF_CONSISTENCY_20260707.sh",
    "CHECK_MARS56_MILLION_CAMPAIGN_STATUS_20260707.sh",
    "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh",
    "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh",
    "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh",
    "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh",
    "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh",
    "SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh",
    "CHECK_LOCAL_SUPERVISOR_SUMMARY_GOAL_PROOF_20260707.sh",
    "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    "CHECK_LOCAL_CONTINUOUS_WATCH_ORDER_BEHAVIOR_20260708.sh",
    "START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh",
    "CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh",
    "CHECK_LOCAL_PRODUCTION_RATE_ARTIFACT_BEHAVIOR_20260708.sh",
    "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh",
    "RUN_MARS56_RESUME_PRODUCTION_WATCHERS_AFTER_DUO_20260707.sh",
    "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh",
    "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh",
    "CHECK_LOCAL_TRACEABILITY_AUDIT_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_RUNNER_STRICT_ACCEPTANCE_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_PIPELINE_SUMMARY_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_CHECKPOINT_RUNNER_4D_REUSE_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_100K_EVIDENCE_INDEX_STRICT_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_QUEUE_PROVENANCE_PREFLIGHT_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_ADAPTIVE_ACQUISITION_ROUND_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_ACCEPTED_POOL_MERGE_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_1M_PRODUCTION_PLAN_CONTRACT_BEHAVIOR_20260708.sh",
    "CHECK_LOCAL_FINAL_1M_AUDIT_CONTRACT_GATE_20260708.sh",
    "RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh",
    "CHECK_LOCAL_POST_DUO_SYNC_START_GATE_20260708.sh",
    "CHECK_MARS56_POST_DUO_SYNC_START_LAUNCH_STATUS_20260708.sh",
    "CHECK_LOCAL_POST_DUO_LAUNCH_AUDIT_BEHAVIOR_20260708.sh",
    "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh",
    "START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "CHECK_LOCAL_INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_20260708.sh",
    "RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh",
    "START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh",
    "CHECK_LOCAL_WAIT_FOR_SSH_START_GATE_20260708.sh",
    "CHECK_LOCAL_WAIT_FOR_SSH_RUNTIME_UNIQUENESS_20260708.sh",
    "SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh",
    "RUN_MARS56_1M_LOCAL_STATUS_REFRESH_20260708.sh",
    "START_MARS56_1M_LOCAL_STATUS_REFRESH_DETACHED_20260708.sh",
    "CHECK_LOCAL_STATUS_REFRESH_DETACHED_GATE_20260708.sh",
    "CHECK_LOCAL_STATUS_REFRESH_FRESHNESS_GATE_20260708.sh",
    "CHECK_LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_20260709.sh",
    "CHECK_LOCAL_K_SIGN_DIAGNOSTIC_BEHAVIOR_20260708.sh",
    "RUN_MARS56_ADAPTIVE_ACQUISITION_AFTER_DUO_20260708.sh",
    "rfic-transformer-inverse-design/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh",
    "rfic-transformer-inverse-design/scripts/run_mars56_s4p_100k_chunk_from_queue.sh",
    "MARS56_S4P_MILLION_CAMPAIGN_20260705.sh",
]

checks = {}

checks["entrypoint_syntax"] = {
    rel: {"status": "PASS" if ok else "FAIL", "detail": detail}
    for rel, (ok, detail) in ((rel, bash_n(rel)) for rel in entrypoints)
}

checks["supervisor_preflight_gate"] = check_script(
    "RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh",
    [
        "MODE=preflight",
        "LOCAL_CHECKPOINT_PROOF_CONSISTENCY_PREFLIGHT",
        "CHECK_LOCAL_CHECKPOINT_PROOF_CONSISTENCY_20260707.sh",
        "SUPERVISOR_STATUS=PREFLIGHT_ONLY_DONE",
        "run_step \"LOCAL_CHECKPOINT_PROOF_CONSISTENCY_PREFLIGHT\"",
        "READ_ONLY_STATUS",
        "AUTO_CHECKPOINT_DRY_RUN",
        "CUMULATIVE_CHECKPOINT_DRY_RUN",
        "ONE_MILLION_FINAL_GOAL_AUDIT",
        "resume-watchers",
        "RESUME_PRODUCTION_WATCHERS_DRY_RUN",
        "RESUME_PRODUCTION_WATCHERS_RUN",
        "verify-runner",
        "REMOTE_100K_RUNNER_VERIFY",
        "REMOTE_CHECKPOINT_STACK_VERIFY",
        "verify_remote_execution_stack",
        "REMOTE_100K_RUNNER_VERIFY_BEFORE_MUTATION",
        "REMOTE_CHECKPOINT_STACK_VERIFY_BEFORE_MUTATION",
        "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh",
        "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh",
        "MODE=rate",
        "PRODUCTION_RATE_AND_ETA_AUDIT",
        "CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh",
        "MODE=evidence-index",
        "BUILD_100K_CHECKPOINT_EVIDENCE_INDEX",
        "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh",
        "MODE=adaptive-acquisition",
        'RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"',
        "ADAPTIVE_ACQUISITION_DRY_RUN",
        "ADAPTIVE_ACQUISITION_BUILD_QUEUE",
        "RUN_EMX=\"$RUN_ADAPTIVE_EMX\"",
        "checkpoint)",
        "cumulative)",
        "resume-watchers)",
        "rerun-failed)",
        "verify_remote_execution_stack",
        "SUPERVISOR_LOG_REEXECED",
        "tee -a \"$SUPERVISOR_LOG_PATH\"",
    ],
)

supervisor_text = read("RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh") or ""
mutation_modes = ("checkpoint", "cumulative", "resume-watchers", "rerun-failed")
mutation_verify_missing = []
for mode in mutation_modes:
    match = re.search(rf"(?m)^  {re.escape(mode)}\)", supervisor_text)
    if match is None:
        mutation_verify_missing.append(f"{mode}:missing_case")
        continue
    start = match.start()
    next_match = re.search(r"(?m)^  [A-Za-z0-9_-]+\)", supervisor_text[match.end():])
    next_case = match.end() + next_match.start() if next_match else -1
    block = supervisor_text[start:] if next_case < 0 else supervisor_text[start:next_case]
    if "verify_remote_execution_stack" not in block:
        mutation_verify_missing.append(f"{mode}:missing_verify")

checks["supervisor_mutation_requires_remote_verify_gate"] = {
    "status": "PASS" if not mutation_verify_missing else "FAIL",
    "file": str(root / "RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh"),
    "mutation_modes": list(mutation_modes),
    "missing": mutation_verify_missing,
}

checks["proof_consistency_gate"] = check_script(
    "CHECK_LOCAL_CHECKPOINT_PROOF_CONSISTENCY_20260707.sh",
    [
        "CHECKPOINT_PROOF_CONSISTENCY_STATUS=PASS",
        "valid_full_pass",
        "weak_missing_expected_count",
        "wrong_min_valid",
        "bad_uniformity_step",
        "low_model_usable_row_count",
        "missing_uniformity_manifest_step",
        "low_uniformity_visual_artifact_count",
        "missing_traceability_step",
        "low_traceability_training_rows",
        "missing_traceability_details",
        "missing_traceability_row_field",
        "traceability.details_missing",
        "traceability.{key}=MISSING",
        "uniformity_manifest_plots_not_required",
        "missing_k_sign_diagnostics",
        "wrong_k_uniformity_axis",
        "low_signed_k_count",
        "uniformity.k_sign_diagnostics",
        "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh",
        "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh",
    ],
)

physical_gate_tokens = [
    "run_mars56_s4p_physical_checkpoint_pipeline.sh",
    "--count \"$EXPECTED\"",
    "--min-valid \"$EXPECTED\"",
    "--target-ghz 15",
    "--bins 10",
    "--pair-bins 10",
    "--lp-min 0.5 --lp-max 3",
    "--ls-min 0.5 --ls-max 3",
    "--q-min 5 --q-max 25",
    "--k-min 0 --k-max 0.8",
    "--require-four-d-gate",
    "--four-d-bins 4",
    "--min-four-d-occupied-frac 0.50",
    "--require-plots",
    "checkpoint_proof",
    "expected_physical_ranges",
    "expected_min_four_d_occupied_fraction = 0.50",
    "expected_uniformity_thresholds",
    "min_1d_occupied_fraction",
    "min_1d_entropy_fraction",
    "max_1d_bin_imbalance",
    "min_pair_occupied_fraction",
    "min_pair_entropy_fraction",
    "physical_uniformity_gate.require_four_d_gate",
    "physical_uniformity_gate.min_four_d_occupied_fraction",
    "uniformity.one_dimensional_uniformity=MISSING",
    "uniformity.one_dimensional_uniformity.{feature_name}=MISSING",
    "uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}",
    "uniformity.pairwise_uniformity=MISSING",
    "uniformity.pairwise_uniformity.{pair_name}",
    "uniformity.pairwise_uniformity.{pair_name}.{metric_name}",
    "uniformity.four_dimensional_uniformity=MISSING",
    "uniformity.four_dimensional_uniformity.occupied_fraction",
    "expected_count",
    "min_valid",
    '"uniformity"',
    '"training"',
    '"model"',
    '"traceability"',
    "stable_manifest_rows",
    "stable_unique_evaluations",
    "response_feature_rows",
    "response_unique_evaluations",
    "response_dataset_rows",
    "response_dataset_unique_evaluations",
    "enriched_rows",
    "enriched_unique_evaluations",
    "training_rows",
    "training_unique_evaluations",
    "uniformity.ranges",
    "uniformity.ranges.{feature_name}",
    "uniformity.ranges.{feature_name}.explicit",
    'item.get("source") != "explicit"',
    "math.isclose(actual_min, target_min",
    "expected=({target_min},{target_max})",
]

checks["first100k_physical_model_gate"] = check_script(
    "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh",
    physical_gate_tokens
    + [
        "STATUS=FIRST100K_CHECKPOINT_PROOF_PASS",
        "STATUS=FIRST100K_CHECKPOINT_PROOF_FAIL",
        "U8=$BASE/status/accepted_inrange_pool_after_chunk08_20260706/physical_feature_uniformity/physical_feature_uniformity_summary.json",
        "STATUS=WAIT_U8_SUMMARY_MISSING",
        "STATUS=WAIT_U8_NOT_PASS",
        "First100k model test is intentionally blocked until U8 physical-feature uniformity is PASS.",
        "parallel_candidate_queue_dataset_summary.json",
    ],
)

checks["generic_100k_physical_model_gate"] = check_script(
    "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh",
    physical_gate_tokens
    + [
        "STATUS=100K_CHECKPOINT_PROOF_PASS",
        "STATUS=100K_CHECKPOINT_PROOF_FAIL",
        "REMOTE_DATASET_DIR",
        "parallel_candidate_queue_dataset_summary.json",
    ],
)

checks["auto_completed_100k_checkpoint_gate"] = check_script(
    "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh",
    physical_gate_tokens
    + [
        "U8=$BASE/status/accepted_inrange_pool_after_chunk08_20260706/physical_feature_uniformity/physical_feature_uniformity_summary.json",
        "STATUS=WAIT_U8_NOT_PASS",
        "No 100k production checkpoint should run before U8 physical-feature uniformity passes.",
        "*_100k_after_chunk08_pass",
        "SKIP_GEOMETRY_ONLY",
        "SKIP_ALREADY_CHECKPOINTED_PASS",
        "RERUN_FAILED_CHECKPOINTS",
    ],
)

checks["cumulative_100k_to_1000k_gate"] = check_script(
    "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh",
    [
        "MAX_CHUNKS=\"${MAX_CHUNKS:-10}\"",
        "EXPECTED_PER_CHUNK=100000",
        "CUM_SOURCE",
        "checkpoint_proof",
        "expected_physical_ranges",
        "CUM_STOP_PREFIX_NOT_READY",
        "cumulative_%04dk_after_chunk08_pass",
        "run_mars56_s4p_physical_checkpoint_pipeline.sh",
        "--count \"$expected\"",
        "--min-valid \"$expected\"",
        "--lp-min 0.5 --lp-max 3",
        "--ls-min 0.5 --ls-max 3",
        "--q-min 5 --q-max 25",
        "--k-min 0 --k-max 0.8",
        "--require-four-d-gate",
        "--four-d-bins 4",
        "--min-four-d-occupied-frac 0.50",
        "uniformity.ranges",
        "uniformity.ranges.{feature_name}",
        "uniformity.ranges.{feature_name}.explicit",
        "expected_min_four_d_occupied_fraction = 0.50",
        "expected_uniformity_thresholds",
        "min_1d_occupied_fraction",
        "min_1d_entropy_fraction",
        "max_1d_bin_imbalance",
        "min_pair_occupied_fraction",
        "min_pair_entropy_fraction",
        "physical_uniformity_gate.require_four_d_gate",
        "physical_uniformity_gate.min_four_d_occupied_fraction",
        "uniformity.one_dimensional_uniformity=MISSING",
        "uniformity.one_dimensional_uniformity.{feature_name}=MISSING",
        "uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}",
        "uniformity.pairwise_uniformity=MISSING",
        "uniformity.pairwise_uniformity.{pair_name}",
        "uniformity.pairwise_uniformity.{pair_name}.{metric_name}",
        "uniformity.four_dimensional_uniformity=MISSING",
        "uniformity.four_dimensional_uniformity.occupied_fraction",
        "CUM_SKIP_ALREADY_PASS",
    ],
)

checks["final_1m_completion_audit"] = check_script(
    "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh",
    [
        "EXPECTED_CHUNKS=10",
        "EXPECTED_PER_CHUNK=100000",
        "EXPECTED_TOTAL=1000000",
        "chunk_%03d_100k_after_chunk08_pass",
        "cumulative_%04dk_after_chunk08_pass",
        "total_nonempty_formal_s4p",
        "checkpoint_proof",
        "expected_physical_ranges",
        "expected_min_four_d_occupied_fraction = 0.50",
        "expected_uniformity_thresholds",
        "min_1d_occupied_fraction",
        "min_1d_entropy_fraction",
        "max_1d_bin_imbalance",
        "min_pair_occupied_fraction",
        "min_pair_entropy_fraction",
        "physical_uniformity_gate.require_four_d_gate",
        "physical_uniformity_gate.min_four_d_occupied_fraction",
        "uniformity.one_dimensional_uniformity=MISSING",
        "uniformity.one_dimensional_uniformity.{feature_name}=MISSING",
        "uniformity.one_dimensional_uniformity.{feature_name}.{metric_name}",
        "uniformity.pairwise_uniformity=MISSING",
        "uniformity.pairwise_uniformity.{pair_name}",
        "uniformity.pairwise_uniformity.{pair_name}.{metric_name}",
        "uniformity.four_dimensional_uniformity=MISSING",
        "uniformity.four_dimensional_uniformity.occupied_fraction",
        "uniformity.ranges",
        "uniformity.ranges.{feature_name}",
        "uniformity.ranges.{feature_name}.explicit",
        "uniformity_manifest",
        "visual_artifact_count",
        "require_plots",
        "formal_chunk_pass_count",
        "cumulative_pass_count",
        "mars56_100k_checkpoint_evidence_index.json",
        "mars56_100k_checkpoint_evidence_index.md",
        "evidence_index_status",
        "evidence_index_audit_result",
        "CONTRACT_BUILD",
        "CONTRACT_AUDIT",
        "build_mars56_1m_production_plan_contract.py",
        "audit_mars56_1m_production_plan_contract.py",
        "production_plan_contract_audit_result",
        "production_plan_contract_audit_summary",
        "evidence_formal_100k_dataset_count",
        "evidence_formal_100k_evidence_pass_count",
        "evidence_cumulative_evidence_count",
        "evidence_cumulative_evidence_pass_count",
        "evidence_unexpected_formal_100k_tags",
        "evidence_unexpected_cumulative_checkpoint_tags",
        "evidence_duplicate_cumulative_checkpoint_tags",
        "ONE_MILLION_GOAL_STATUS=PASS",
        "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
        '"stable_index"',
        '"response_features"',
        '"enrichment"',
        '"uniformity"',
        '"training"',
        '"model"',
        '"traceability"',
        "traceability.{key}",
        "stable_manifest_rows",
        "stable_unique_evaluations",
        "response_feature_rows",
        "response_unique_evaluations",
        "response_dataset_rows",
        "response_dataset_unique_evaluations",
        "enriched_rows",
        "enriched_unique_evaluations",
        "training_rows",
        "training_unique_evaluations",
    ],
)

checks["supervisor_transcript_summary_gate"] = check_script(
    "SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh",
    [
        "SUPERVISOR_LOG_SUMMARY_STATUS=PASS",
        "mars56_post_duo_supervisor_*.log",
        "latest_goal_completion_proven",
        "goal_completion_ever_proven",
        "latest_goal_completion_proven_log",
        "EXPECTED_FORMAL_CHUNKS = 10",
        "EXPECTED_CUMULATIVE_PREFIXES = 10",
        "EXPECTED_TOTAL_NONEMPTY = 1000000",
        "formal_chunk_pass_count == EXPECTED_FORMAL_CHUNKS",
        "cumulative_pass_count == EXPECTED_CUMULATIVE_PREFIXES",
        "total_nonempty_formal_s4p >= EXPECTED_TOTAL_NONEMPTY",
        "failure_count == 0",
        "EXPECTED_EVIDENCE_INDEX_PASS = 10",
        "evidence_index_status == \"PASS\"",
        "evidence_formal_100k_evidence_pass_count >= EXPECTED_EVIDENCE_INDEX_PASS",
        "evidence_cumulative_evidence_pass_count >= EXPECTED_EVIDENCE_INDEX_PASS",
        "evidence_index_audit_result == \"PASS\"",
        "production_plan_contract_audit_result == \"PASS\"",
        "GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
        "FORMAL_CHUNK",
        "CUMULATIVE_PREFIX",
        "DATASET",
        "CHECKPOINT_RESULT",
        "CUM_CHECKPOINT_RESULT",
        "ONE_MILLION_GOAL_STATUS",
        "formal_chunk_pass_count",
        "cumulative_prefix_pass_count",
        "checkpoint_result_pass_count",
        "evidence_index_status_reported",
        "evidence_formal_100k_evidence_pass_count_reported",
        "evidence_cumulative_evidence_pass_count_reported",
        "evidence_index_audit_result_reported",
        "production_plan_contract_audit_result_reported",
    ],
)

checks["supervisor_summary_goal_proof_behavior_gate"] = check_script(
    "CHECK_LOCAL_SUPERVISOR_SUMMARY_GOAL_PROOF_20260707.sh",
    [
        "SUMMARY_GOAL_PROOF_STATUS=PASS",
        "weak_status_only",
        "bad_counts",
        "missing_evidence_index",
        "missing_contract_audit",
        "valid_final_audit",
        "valid_then_later_preflight",
        "goal_completion_ever_proven",
        "latest_goal_completion_proven",
        "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
        "formal_chunk_pass_count=10",
        "cumulative_pass_count=10",
        "total_nonempty_formal_s4p=1000000",
        "evidence_index_status=PASS",
        "evidence_formal_100k_evidence_pass_count=10",
        "evidence_cumulative_evidence_pass_count=10",
        "evidence_index_audit_result=PASS",
        "production_plan_contract_audit_result=PASS",
        "failure_count=0",
    ],
)

checks["physical_uniformity_visual_manifest_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/audit_physical_feature_uniformity.py",
    [
        "--require-plots",
        "physical_feature_uniformity_manifest.json",
        "physical_feature_marginal_histograms.png",
        "physical_feature_pair_scatter.png",
        "physical_feature_pair_occupancy_heatmaps.png",
        "visual_artifact_count",
        "_plot_checks",
        "_artifact_manifest",
        "manifest=",
        "k_sign_diagnostics",
        "uniformity_k_axis",
        "signed_k_count",
        "negative_k_count",
        "Uniformity is evaluated on |K|",
    ],
    syntax=False,
)

k_diag_script = root / "CHECK_LOCAL_K_SIGN_DIAGNOSTIC_BEHAVIOR_20260708.sh"
if k_diag_script.exists():
    completed = subprocess.run(
        ["bash", str(k_diag_script)],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    output = completed.stdout
    required_k_diag_tokens = ["K_SIGN_DIAGNOSTIC_BEHAVIOR_STATUS=PASS"]
    checks["k_sign_diagnostic_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in output for token in required_k_diag_tokens) else "FAIL",
        "file": str(k_diag_script),
        "returncode": completed.returncode,
        "required_tokens": required_k_diag_tokens,
        "missing_tokens": [token for token in required_k_diag_tokens if token not in output],
    }
else:
    checks["k_sign_diagnostic_behavior_gate"] = {
        "status": "FAIL",
        "file": str(k_diag_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["physical_checkpoint_traceability_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/audit_physical_checkpoint_traceability.py",
    [
        "physical_checkpoint_traceability_summary.json",
        "stable_touchstone_index_manifest.csv",
        "response_feature_extraction_summary.json",
        "geometry_enrichment_manifest.json",
        "physical_feature_uniformity_summary.json",
        "physical_feature_inverse_training_manifest.json",
        "physical_feature_inverse_checkpoint_test_summary.json",
        "source_path_exists",
        "indexed_path_exists",
        "stable_manifest_rows",
        "stable_unique_evaluations",
        "response_feature_rows",
        "response_unique_evaluations",
        "response_dataset_rows",
        "response_dataset_unique_evaluations",
        "enriched_rows",
        "enriched_unique_evaluations",
        "training_rows",
        "training_unique_evaluations",
        "training_source_sha_present",
        "stable_to_response_evaluations",
        "response_to_enriched_evaluations",
        "enriched_to_training_evaluations",
    ],
    syntax=False,
)

traceability_behavior_script = root / "CHECK_LOCAL_TRACEABILITY_AUDIT_BEHAVIOR_20260708.sh"
if traceability_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(traceability_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    checks["physical_checkpoint_traceability_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and "TRACEABILITY_AUDIT_BEHAVIOR_STATUS=PASS" in behavior_output else "FAIL",
        "file": str(traceability_behavior_script),
        "returncode": completed.returncode,
        "required_cases": [
            "TRACEABILITY_BEHAVIOR_CASE=complete_chain status=PASS",
            "TRACEABILITY_BEHAVIOR_CASE=missing_source status=PASS",
            "TRACEABILITY_BEHAVIOR_CASE=low_training status=PASS",
            "TRACEABILITY_AUDIT_BEHAVIOR_STATUS=PASS",
        ],
        "missing_tokens": [
            token
            for token in (
                "TRACEABILITY_BEHAVIOR_CASE=complete_chain status=PASS",
                "TRACEABILITY_BEHAVIOR_CASE=missing_source status=PASS",
                "TRACEABILITY_BEHAVIOR_CASE=low_training status=PASS",
                "TRACEABILITY_AUDIT_BEHAVIOR_STATUS=PASS",
            )
            if token not in behavior_output
        ],
    }
else:
    checks["physical_checkpoint_traceability_behavior_gate"] = {
        "status": "FAIL",
        "file": str(traceability_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["physical_checkpoint_pipeline_artifact_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh",
    [
        "--require-plots",
        "audit_physical_checkpoint_traceability.py",
        "physical_checkpoint_traceability",
        "physical_checkpoint_traceability_summary.json",
        "physical_feature_uniformity_manifest.json",
        "uniformity_manifest",
        "visual_artifact_count",
        "require_plots",
        "proof_reasons",
        "traceability.details_missing",
        "traceability.{key}=MISSING",
        "uniformity_manifest.visual_artifact_count",
        "uniformity_manifest.require_plots",
        "uniformity.k_mode",
        "physical_uniformity_gate",
        "target_ranges",
        "uniformity.ranges",
        "uniformity.four_dimensional_uniformity",
        "four_dimensional_uniformity.occupied_fraction",
        "uniformity.k_sign_diagnostics",
        "uniformity.k_sign_diagnostics.signed_k_count",
        "uniformity.k_sign_diagnostics.uniformity_k_axis",
        "model.test_row_count",
        "model.metrics=MISSING",
        "model.metrics.test_count",
        "model.metrics.geometry_count",
        "model.metrics.{metric_key}",
        "max_normalized_mae",
        "max_normalized_rmse",
        "mean_normalized_mae",
        "mean_normalized_rmse",
        '"traceability"',
        "stable_manifest_rows",
        "stable_unique_evaluations",
        "response_feature_rows",
        "response_unique_evaluations",
        "response_dataset_rows",
        "response_dataset_unique_evaluations",
        "enriched_rows",
        "enriched_unique_evaluations",
        "training_rows",
        "training_unique_evaluations",
    ],
)

pipeline_behavior_script = root / "CHECK_LOCAL_PIPELINE_SUMMARY_BEHAVIOR_20260708.sh"
if pipeline_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(pipeline_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_pipeline_behavior_tokens = [
        "PIPELINE_SUMMARY_CASE=complete_chain status=PASS",
        "PIPELINE_SUMMARY_CASE=missing_traceability_field status=PASS",
        "PIPELINE_SUMMARY_CASE=low_visual_count status=PASS",
        "PIPELINE_SUMMARY_CASE=low_training_count status=PASS",
        "PIPELINE_SUMMARY_CASE=missing_model_metrics status=PASS",
        "PIPELINE_SUMMARY_CASE=zero_model_test_rows status=PASS",
        "PIPELINE_SUMMARY_CASE=missing_k_sign_diagnostics status=PASS",
        "PIPELINE_SUMMARY_CASE=low_four_d_occupancy status=PASS",
        "PIPELINE_SUMMARY_CASE=wrong_explicit_range status=PASS",
        "PIPELINE_SUMMARY_CASE=observed_range_not_explicit status=PASS",
        "PIPELINE_SUMMARY_BEHAVIOR_STATUS=PASS",
    ]
    checks["physical_checkpoint_pipeline_summary_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_pipeline_behavior_tokens) else "FAIL",
        "file": str(pipeline_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_pipeline_behavior_tokens,
        "missing_tokens": [token for token in required_pipeline_behavior_tokens if token not in behavior_output],
    }
else:
    checks["physical_checkpoint_pipeline_summary_behavior_gate"] = {
        "status": "FAIL",
        "file": str(pipeline_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

runner_4d_reuse_behavior_script = root / "CHECK_LOCAL_CHECKPOINT_RUNNER_4D_REUSE_BEHAVIOR_20260708.sh"
if runner_4d_reuse_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(runner_4d_reuse_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    runner_4d_scripts = [
        "RUN_MARS56_FIRST100K_MODEL_TEST_AFTER_DUO_20260707.sh",
        "RUN_MARS56_100K_MODEL_TEST_FOR_DATASET_AFTER_DUO_20260707.sh",
        "RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh",
        "RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh",
    ]
    runner_4d_variants = [
        "complete",
        "missing_four_d_gate",
        "low_four_d_occupancy",
        "missing_one_d_uniformity",
        "low_one_d_entropy",
        "high_one_d_imbalance",
        "low_pair_occupancy",
        "low_pair_entropy",
        "missing_model_metrics",
        "zero_model_test_rows",
        "missing_four_d_summary",
    ]
    required_runner_4d_tokens = [
        f"CHECKPOINT_RUNNER_4D_REUSE_CASE={script}:{variant} status=PASS"
        for script in runner_4d_scripts
        for variant in runner_4d_variants
    ] + ["CHECKPOINT_RUNNER_4D_REUSE_BEHAVIOR_STATUS=PASS"]
    checks["checkpoint_runner_4d_reuse_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_runner_4d_tokens) else "FAIL",
        "file": str(runner_4d_reuse_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_runner_4d_tokens,
        "missing_tokens": [token for token in required_runner_4d_tokens if token not in behavior_output],
    }
else:
    checks["checkpoint_runner_4d_reuse_behavior_gate"] = {
        "status": "FAIL",
        "file": str(runner_4d_reuse_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["chunk_audit_uniformity_visual_artifact_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/audit_mars56_s4p_million_chunk_checkpoint.py",
    [
        "physical_feature_uniformity_manifest.json",
        "uniformity_manifest",
        "physical feature uniformity artifact manifest PASS",
        "uniformity visual artifact count",
        "visual_artifact_count",
    ],
    syntax=False,
)

checks["continuous_100k_watch_gate"] = check_script(
    "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    [
        "WATCH_ITERATIONS",
        "SLEEP_SECONDS",
        "LOCAL_DRY_RUN",
        "STOP_ON_GOAL_PASS",
        "RUN_RESUME_WATCHERS",
        "VERIFY_RUNNER_REQUIRED",
        "WATCH_LOG_CAPTURE",
        "WATCH_LOG_DIR",
        "WATCH_LOG_PATH",
        "WATCH_LOG_REEXECED",
        "SSH_CONTROL_PATH",
        "SSH_PERSIST",
        "CONTROL_DIR",
        "export SSH_CONTROL_PATH SSH_PERSIST CONTROL_DIR",
        "tee -a \"$WATCH_LOG_PATH\"",
        "watch_log=",
        "logs/mars56_post_duo_continuous_watch",
        "RUN_CHECKPOINT",
        "RUN_CUMULATIVE",
        "RUN_AUDIT",
        "RUN_VERIFY_RUNNER",
        "RUN_RATE_AUDIT",
        "RUN_ADAPTIVE_ACQUISITION",
        "RUN_ADAPTIVE_EMX",
        'RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"',
        "RUN_EVIDENCE_INDEX",
        "goal_completion_proven",
        "goal_completion_ever_proven",
        "WATCH_GOAL_COMPLETION_PROVEN",
        "WATCH_STATUS=ONE_MILLION_GOAL_PROVEN_STOPPING",
        "WATCH_STATUS=REQUIRED_STEP_FAILED",
        "local rc=0",
        "rc=$?",
        "run_supervisor_mode \"preflight\"",
        "run_supervisor_mode \"verify-runner\" \"$VERIFY_RUNNER_REQUIRED\"",
        "run_supervisor_mode \"resume-watchers\"",
        "run_supervisor_mode \"rate\"",
        "run_supervisor_mode \"checkpoint\"",
        "run_supervisor_mode \"cumulative\"",
        "run_supervisor_mode \"evidence-index\"",
        "run_supervisor_mode \"audit\"",
        "summarize_supervisor_logs",
        "WATCH_STATUS=REQUESTED_ITERATIONS_DONE",
        "WATCH_ITERATIONS=0 means run forever until interrupted.",
    ],
)

continuous_watch_order_behavior_script = root / "CHECK_LOCAL_CONTINUOUS_WATCH_ORDER_BEHAVIOR_20260708.sh"
if continuous_watch_order_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(continuous_watch_order_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_continuous_order_tokens = [
        "CONTINUOUS_WATCH_ORDER=rate_before_evidence_index",
        "CONTINUOUS_WATCH_ORDER=checkpoint_before_evidence_index",
        "CONTINUOUS_WATCH_ORDER=cumulative_before_evidence_index",
        "CONTINUOUS_WATCH_ORDER_BEHAVIOR_CASE=dry_run_sequence status=PASS",
        "CONTINUOUS_WATCH_ORDER_BEHAVIOR_STATUS=PASS",
    ]
    checks["continuous_watch_order_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_continuous_order_tokens) else "FAIL",
        "file": str(continuous_watch_order_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_continuous_order_tokens,
        "missing_tokens": [token for token in required_continuous_order_tokens if token not in behavior_output],
    }
else:
    checks["continuous_watch_order_behavior_gate"] = {
        "status": "FAIL",
        "file": str(continuous_watch_order_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["checkpoint_evidence_index_gate"] = check_script(
    "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh",
    [
        "EVIDENCE_INDEX_STATUS",
        "mars56_100k_checkpoint_evidence_index.json",
        "mars56_100k_checkpoint_evidence_index.md",
        "EXPECTED_PER_CHUNK=\"${EXPECTED_PER_CHUNK:-100000}\"",
        "EXPECTED_CHUNKS=\"${EXPECTED_CHUNKS:-10}\"",
        "from __future__ import annotations",
        "physical_feature_uniformity_manifest.json",
        "physical_feature_marginal_histograms.png",
        "physical_feature_pair_scatter.png",
        "physical_feature_pair_occupancy_heatmaps.png",
        "physical_checkpoint_traceability_summary.json",
        "physical_checkpoint_traceability_report.md",
        "traceability_summary",
        "traceability_report",
        '"traceability"',
        "min_1d_occupied_fraction",
        "min_1d_entropy_fraction",
        "max_1d_bin_imbalance",
        "min_pair_occupied_fraction",
        "min_pair_entropy_fraction",
        "one_dimensional_uniformity",
        "pairwise_uniformity",
        "uniformity.one_dimensional_uniformity",
        "uniformity.pairwise_uniformity",
        "physical_feature_inverse_checkpoint_test_summary.json",
        "physical_feature_inverse_training_table.csv",
        "training_csv",
        "mars56_s4p_physical_checkpoint_pipeline_summary.json",
        "checkpoint_proof",
        "missing_required_artifacts",
        "empty_required_artifacts",
        "required_artifact_status",
        "required_artifact_failures",
        "empty_required_artifact",
        "formal_100k_evidence_pass_count",
        "cumulative_evidence_pass_count",
        "production_rate_artifact",
        "mars56_production_rate_eta_latest.json",
        "mars56_production_rate_eta_latest_CN.md",
        "production_rate_artifact_status",
        "global_training_evaluation_proof",
        "required_global_training_evaluation_proof",
        "global_training_evaluation_status",
        "global_training_evaluation_unique_count",
        "global_training_evaluation_duplicate_count",
        "expected_formal_100k_tags",
        "missing_expected_formal_100k_tags",
        "unexpected_formal_100k_tags",
        "formal_100k_tag_status",
        "required_formal_100k_tags",
        "expected_cumulative_checkpoint_tags",
        "missing_expected_cumulative_checkpoint_tags",
        "unexpected_cumulative_checkpoint_tags",
        "duplicate_cumulative_checkpoint_tags",
        "cumulative_checkpoint_tag_status",
        "required_cumulative_checkpoint_tags",
        "strict_evidence_contract",
        "required_count_details",
        "required_k_contract",
        "required_physical_ranges",
        "uniformity.valid_feature_count",
        "training.training_count",
        "model.usable_row_count",
        "model.test_row_count",
        "model.metrics=MISSING",
        "model.metrics.test_count",
        "model.metrics.geometry_count",
        "model.metrics.{metric_key}",
        "max_normalized_mae",
        "max_normalized_rmse",
        "mean_normalized_mae",
        "mean_normalized_rmse",
        "traceability.stable_manifest_rows",
        "traceability.stable_unique_evaluations",
        "traceability.response_feature_rows",
        "traceability.response_unique_evaluations",
        "traceability.response_dataset_rows",
        "traceability.response_dataset_unique_evaluations",
        "traceability.enriched_rows",
        "traceability.enriched_unique_evaluations",
        "traceability.training_rows",
        "traceability.training_unique_evaluations",
        "uniformity.k_mode",
        "uniformity.ranges",
        "uniformity.ranges.{feature_name}",
        "uniformity.ranges.{feature_name}.explicit",
        "uniformity.k_sign_diagnostics",
        "uniformity.k_sign_diagnostics.signed_k_count",
        "uniformity.k_sign_diagnostics.uniformity_k_axis",
        "EVIDENCE_INDEX_LOCAL_DRY_RUN=1",
        "remote_audit_contains=EVIDENCE_INDEX_STATUS",
        "remote_audit_contains=production_rate_artifact",
        "remote_audit_contains=mars56_production_rate_eta_latest.json",
        "Then this will build a remote checkpoint evidence index without rerunning EMX/model tests.",
    ],
)

evidence_index_behavior_script = root / "CHECK_LOCAL_100K_EVIDENCE_INDEX_STRICT_BEHAVIOR_20260708.sh"
if evidence_index_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(evidence_index_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_evidence_index_behavior_tokens = [
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=complete status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_training_csv status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_traceability_rows status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_traceability_unique status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=wrong_k_axis status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_model_rows status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_model_metrics status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=zero_model_test_rows status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_k_diagnostics status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=signed_k_count_low status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=wrong_explicit_range status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=observed_range_not_explicit status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_one_d_uniformity status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_one_d_entropy status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=high_one_d_imbalance status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_pair_occupancy status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_pair_entropy status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_four_d_gate status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=low_four_d_occupancy status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=duplicate_global_training status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_expected_formal_tag status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=unexpected_formal_tag status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=missing_expected_cumulative_tag status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=unexpected_cumulative_tag status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_CASE=duplicate_cumulative_tag status=PASS",
        "EVIDENCE_INDEX_STRICT_BEHAVIOR_STATUS=PASS",
    ]
    checks["checkpoint_evidence_index_strict_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_evidence_index_behavior_tokens) else "FAIL",
        "file": str(evidence_index_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_evidence_index_behavior_tokens,
        "missing_tokens": [token for token in required_evidence_index_behavior_tokens if token not in behavior_output],
    }
else:
    checks["checkpoint_evidence_index_strict_behavior_gate"] = {
        "status": "FAIL",
        "file": str(evidence_index_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["production_rate_eta_gate"] = check_script(
    "CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh",
    [
        "PRODUCTION_RATE_AUDIT_STATUS",
        "PRODUCTION_RATE_AUDIT_REASONS",
        "EXPECTED_PARALLEL_JOBS=\"${EXPECTED_PARALLEL_JOBS:-48}\"",
        "TARGET_ROWS_PER_CHECKPOINT=\"${TARGET_ROWS_PER_CHECKPOINT:-100000}\"",
        "TARGET_TOTAL_ROWS=\"${TARGET_TOTAL_ROWS:-1000000}\"",
        "TARGET_SECONDS_PER_ACCEPTED_ROW=\"${TARGET_SECONDS_PER_ACCEPTED_ROW:-4.0}\"",
        "TARGET_DAYS_PER_100K=\"${TARGET_DAYS_PER_100K:-5.0}\"",
        "MAX_SECONDS_PER_ACCEPTED_ROW=\"${MAX_SECONDS_PER_ACCEPTED_ROW:-4.5}\"",
        "MAX_DAYS_PER_100K=\"${MAX_DAYS_PER_100K:-5.5}\"",
        "OUT_JSON=\"${OUT_JSON:-$ROOT_DIR/reports/mars56_production_rate_eta_latest.json}\"",
        "OUT_MD=\"${OUT_MD:-$ROOT_DIR/reports/mars56_production_rate_eta_latest_CN.md}\"",
        "REMOTE_BASE=\"${REMOTE_BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}\"",
        "REMOTE_STATUS_DIR=\"${REMOTE_STATUS_DIR:-$REMOTE_BASE/status}\"",
        "SYNC_RATE_ARTIFACT_TO_REMOTE=\"${SYNC_RATE_ARTIFACT_TO_REMOTE:-1}\"",
        "write_audit_artifacts",
        "sync_rate_artifacts_to_remote",
        "PRODUCTION_RATE_AUDIT_JSON",
        "PRODUCTION_RATE_AUDIT_MD",
        "PRODUCTION_RATE_REMOTE_ARTIFACT_SYNC=PASS",
        "PRODUCTION_RATE_REMOTE_ARTIFACT_SYNC=SKIPPED_SYNC_RATE_ARTIFACT_TO_REMOTE_0",
        "remote_rate_json=",
        "remote_rate_md=",
        "remote_artifact_sync_target",
        "remote_artifact_sync_contains=mars56_production_rate_eta_latest.json",
        "REMOTE_READ_ONLY_AUDIT",
        "LOCAL_DRY_RUN records only the configured throughput contract",
        "measured_seconds_per_accepted_row",
        "eta_days_per_100k",
        "eta_days_for_1m_at_same_rate",
        "PRODUCTION_RATE_TARGET_STATUS",
        "target_seconds_per_accepted_row",
        "target_days_per_100k",
        "latest_parallel_jobs",
        "active_100k_runner_processes",
        "active_100k_worker_processes",
        "active_100k_emx_processes",
        "DATASET_RATE_SUMMARY",
        "LOCAL_DRY_RUN=1",
        "remote_audit_contains=PRODUCTION_RATE_AUDIT_STATUS",
        "remote_audit_contains=PRODUCTION_RATE_TARGET_STATUS",
        "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted.",
    ],
)

production_rate_artifact_behavior_script = root / "CHECK_LOCAL_PRODUCTION_RATE_ARTIFACT_BEHAVIOR_20260708.sh"
if production_rate_artifact_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(production_rate_artifact_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_rate_artifact_tokens = [
        "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_CASE=dry_run_artifacts status=PASS",
        "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_STATUS=PASS",
    ]
    checks["production_rate_artifact_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_rate_artifact_tokens) else "FAIL",
        "file": str(production_rate_artifact_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_rate_artifact_tokens,
        "missing_tokens": [token for token in required_rate_artifact_tokens if token not in behavior_output],
    }
else:
    checks["production_rate_artifact_behavior_gate"] = {
        "status": "FAIL",
        "file": str(production_rate_artifact_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["detached_continuous_watch_launcher_gate"] = check_script(
    "START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh",
    [
        "Detached launcher/status wrapper for the post-Duo continuous watcher.",
        "ACTION=start|status",
        "ACTION=\"${ACTION:-start}\"",
        "WATCH_ITERATIONS=\"${WATCH_ITERATIONS:-0}\"",
        "SLEEP_SECONDS=\"${SLEEP_SECONDS:-1800}\"",
        "LOCAL_DRY_RUN=\"${LOCAL_DRY_RUN:-0}\"",
        "PID_FILE",
        "DETACHED_LOG",
        "DETACHED_STATUS_JSON",
        "SSH_CONTROL_PATH",
        "SSH_PERSIST",
        "CONTROL_DIR",
        "export SSH_CONTROL_PATH SSH_PERSIST CONTROL_DIR",
        "mars56_post_duo_continuous_watch_latest.pid",
        "mars56_post_duo_continuous_watch_latest_detached_status.json",
        "is_pid_alive",
        "write_status_json",
        "DETACHED_WATCH_STATUS=RUNNING",
        "DETACHED_WATCH_STATUS=NOT_RUNNING",
        "DETACHED_WATCH_STATUS=ALREADY_RUNNING",
        "DETACHED_WATCH_STATUS=STARTED",
        "nohup env",
        "WATCH_ITERATIONS=\"$WATCH_ITERATIONS\"",
        "LOCAL_DRY_RUN=\"$LOCAL_DRY_RUN\"",
        "RUN_VERIFY_RUNNER=\"$RUN_VERIFY_RUNNER\"",
        "RUN_RESUME_WATCHERS=\"$RUN_RESUME_WATCHERS\"",
        "RUN_RATE_AUDIT=\"$RUN_RATE_AUDIT\"",
        "RUN_ADAPTIVE_ACQUISITION",
        "RUN_ADAPTIVE_EMX",
        'RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"',
        "RESTART_ON_CONFIG_MISMATCH",
        "config_matches_requested",
        "DETACHED_WATCH_STATUS=CONFIG_MISMATCH",
        "DETACHED_WATCH_DECISION=RESTART_CONFIG_MISMATCH",
        "DETACHED_WATCH_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0",
        "FAIL_OLD_WATCHER_STILL_RUNNING",
        "RUN_EVIDENCE_INDEX=\"$RUN_EVIDENCE_INDEX\"",
        "SSH_CONTROL_PATH=\"$SSH_CONTROL_PATH\"",
        "SSH_PERSIST=\"$SSH_PERSIST\"",
        "CONTROL_DIR=\"$CONTROL_DIR\"",
        '"ssh_control_path"',
        '"ssh_persist"',
        "bash \"$WATCHER\" >\"$DETACHED_LOG\" 2>&1 &",
    ],
)

checks["remote_100k_runner_verify_sync_gate"] = check_script(
    "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh",
    [
        "SYNC_REMOTE_RUNNER",
        "ALLOW_MISMATCH",
        "LOCAL_RUNNER",
        "REMOTE_PROJECT",
        "REMOTE_RUNNER",
        "run_mars56_s4p_100k_chunk_from_queue.sh",
        "local_runner_sha256",
        "remote_runner_sha256",
        "remote_runner_hash_match",
        "REMOTE_RUNNER_VERIFY_STATUS=PASS",
        "REMOTE_RUNNER_VERIFY_STATUS=FAIL",
        "REMOTE_RUNNER_TOKEN",
        "LOCAL_QUEUE_PREFLIGHT",
        "REMOTE_QUEUE_PREFLIGHT",
        "queue_preflight_sha256",
        "remote_queue_preflight_sha256",
        "REMOTE_QUEUE_PREFLIGHT_TOKEN",
        "REMOTE_QUEUE_PREFLIGHT_VERIFY_STATUS=PASS",
        "strict_acceptance",
        "candidate_queue_provenance_preflight",
        "candidate_queue_provenance_status",
        "audit_mars56_s4p_candidate_queue_provenance.py",
        "CANDIDATE_QUEUE_PROVENANCE_STATUS",
        "checkpoint_proof",
        "checkpoint_proof_reasons",
        "uniformity_manifest",
        "traceability",
        "traceability.details_missing",
        "traceability.{key}=MISSING",
        "stable_manifest_rows",
        "stable_unique_evaluations",
        "response_feature_rows",
        "response_unique_evaluations",
        "response_dataset_rows",
        "response_dataset_unique_evaluations",
        "enriched_rows",
        "enriched_unique_evaluations",
        "training_rows",
        "training_unique_evaluations",
        "uniformity_manifest.visual_artifact_count",
        "uniformity_manifest.require_plots",
        "physical_uniformity_gate",
        "target_ranges",
        "uniformity.ranges",
        "uniformity.four_dimensional_uniformity",
        "four_dimensional_uniformity.occupied_fraction",
        "uniformity.k_sign_diagnostics",
        "uniformity.k_sign_diagnostics.signed_k_count",
        "uniformity.k_sign_diagnostics.uniformity_k_axis",
        "ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK",
        "STOP_BEFORE_NEXT_100K_CHUNK",
        "REMOTE_RUNNER_SYNC_INSTALL=PASS",
        "ProxyJump",
        "bash -s",
        "remote_verify_script",
        "REMOTE_BACKUP_SCRIPT",
        "REMOTE_INSTALL_SCRIPT",
    ],
)

checks["remote_checkpoint_stack_verify_sync_gate"] = check_script(
    "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh",
    [
        "SYNC_REMOTE_CHECKPOINT_STACK",
        "ALLOW_MISMATCH",
        "LOCAL_CONTRACT_ONLY",
        "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE",
        "REMOTE_CHECKPOINT_STACK_VERIFY_DECISION=NO_SSH_NO_REMOTE_CLAIM",
        "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=PASS",
        "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=FAIL",
        "REMOTE_CHECKPOINT_STACK_TOKEN",
        "REMOTE_CHECKPOINT_STACK_SYNC_INSTALL",
        "run_mars56_s4p_100k_chunk_from_queue.sh",
        "audit_mars56_s4p_candidate_queue_provenance.py",
        "run_mars56_s4p_adaptive_physical_acquisition_round.sh",
        "merge_physical_feature_accepted_pool.py",
        "plan_physical_feature_balanced_acquisition.py",
        "build_physical_feature_surrogate_candidate_predictions.py",
        "select_physical_feature_targeted_candidate_geometries.py",
        "materialize_physical_feature_targeted_s4p_queue.py",
        "run_mars56_s4p_physical_checkpoint_pipeline.sh",
        "audit_physical_checkpoint_traceability.py",
        "audit_physical_feature_uniformity.py",
        "audit_mars56_s4p_million_chunk_checkpoint.py",
        "TRACEABILITY_LOCAL",
        "TRACEABILITY_REMOTE",
        "TRACEABILITY_SHA",
        "TARGET_ENVELOPE_LOCAL",
        "TARGET_ENVELOPE_REMOTE",
        "TARGET_ENVELOPE_SHA",
        "mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
        "mars56_s4p_1m_official_physical_feature_envelope_20260708",
        "target_envelope_sha256",
        "TARGET_ENVELOPE_LOCAL_CONTRACT_STATUS=PASS",
        "REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=PASS",
        "target_count_per_bin=391",
        "desired_total_count=100000",
        "four_d_bin_count=256",
        "json.tool",
        "QUEUE_PREFLIGHT_LOCAL",
        "QUEUE_PREFLIGHT_REMOTE",
        "QUEUE_PREFLIGHT_SHA",
        "ADAPTIVE_ROUND_LOCAL",
        "ADAPTIVE_ROUND_REMOTE",
        "ADAPTIVE_ROUND_SHA",
        "MERGE_ACCEPTED_POOL_LOCAL",
        "MERGE_ACCEPTED_POOL_REMOTE",
        "MERGE_ACCEPTED_POOL_SHA",
        "PLAN_ACQUISITION_LOCAL",
        "PLAN_ACQUISITION_REMOTE",
        "PLAN_ACQUISITION_SHA",
        "SURROGATE_PREDICT_LOCAL",
        "SURROGATE_PREDICT_REMOTE",
        "SURROGATE_PREDICT_SHA",
        "TARGET_SELECT_LOCAL",
        "TARGET_SELECT_REMOTE",
        "TARGET_SELECT_SHA",
        "QUEUE_MATERIALIZE_LOCAL",
        "QUEUE_MATERIALIZE_REMOTE",
        "QUEUE_MATERIALIZE_SHA",
        "PLAN_CONTRACT_BUILD_LOCAL",
        "PLAN_CONTRACT_AUDIT_LOCAL",
        "PLAN_CONTRACT_BUILD_REMOTE",
        "PLAN_CONTRACT_AUDIT_REMOTE",
        "PLAN_CONTRACT_BUILD_SHA",
        "PLAN_CONTRACT_AUDIT_SHA",
        "strict_acceptance",
        "candidate_queue_provenance_preflight",
        "CANDIDATE_QUEUE_PROVENANCE_STATUS",
        "geometry_space_filling_no_physical_labels",
        "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION",
        "DO_NOT_RUN_EMX_FIX_TARGETING_FIRST",
        "USE_AS_NEXT_ACCEPTED_POOL_FOR_ADAPTIVE_PLANNING",
        "accepted_pool_merge_summary.json",
        "Surrogate candidate predictions are intentionally not accepted as labels",
        "lp_nh_center,ls_nh_center,q_center,k_abs_center",
        "knn_idw_surrogate_for_candidate_priority_only",
        "SPARSE_FEATURE_BINS_PRIORITIZED",
        "deficit_first_then_low_count_topup",
        "acquisition_allocation_policy",
        "physical_feature_targeted_candidate_selection.csv",
        "mars56_grounded_s4p_candidate_queue.csv",
        "sync_primary_secondary_width_to_line_width",
        "PRODUCTION_PLAN_CONTRACT_STATUS=CONTRACT_WRITTEN_NOT_EVIDENCE",
        "PRODUCTION_PLAN_CONTRACT_AUDIT_STATUS",
        "ONE_MILLION_PLAN_CONTRACT_EVIDENCE_PASS",
        "checkpoint_proof",
        "physical_uniformity_gate",
        "physical_uniformity_gate.require_four_d_gate",
        "physical_uniformity_gate.min_four_d_occupied_fraction",
        "target_ranges",
        "physical_uniformity_gate.target_ranges",
        "uniformity.ranges",
        "uniformity.ranges.{feature_name}",
        "uniformity.ranges.{feature_name}.explicit",
        'item.get("source") != "explicit"',
        "math.isclose(actual_min, target_min",
        "expected=({target_min},{target_max})",
        "expected_min_four_d_occupied_frac",
        "require_four_d_gate",
        "min_four_d_occupied_fraction",
        "min_four_d_occupied_frac",
        "uniformity.four_dimensional_uniformity",
        "uniformity.four_dimensional_uniformity=MISSING",
        "uniformity.four_dimensional_uniformity.occupied_fraction",
        "four_dimensional_uniformity.occupied_fraction",
        "required={expected_min_four_d_occupied_frac}",
        "required={min_four_d_occupied_frac}",
        "--require-four-d-gate",
        "--require-plots",
        "traceability_summary",
        "physical_checkpoint_traceability_summary.json",
        "traceability.details_missing",
        "traceability.{key}=MISSING",
        "proof_reasons",
        "uniformity_manifest.visual_artifact_count",
        "uniformity_manifest.require_plots",
        "physical_feature_uniformity_manifest.json",
        "physical_feature_marginal_histograms.png",
        "physical_feature_pair_scatter.png",
        "physical_feature_pair_occupancy_heatmaps.png",
        "visual_artifact_count",
        "k_sign_diagnostics",
        "uniformity_k_axis",
        "signed_k_count",
        "negative_k_count",
        "Uniformity is evaluated on |K|",
        "Adaptive acquisition must target sparse Lp/Ls/Q/|K| bins inside these fixed physical ranges",
        "ProxyJump",
        "bash -s",
        "remote_verify_script",
        "prep_script",
        "install_script",
    ],
)

target_envelope_path = root / "rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json"
expected_target_bounds = {
    "lp_nh_center": {"min": 0.5, "max": 3.0},
    "ls_nh_center": {"min": 0.5, "max": 3.0},
    "q_center": {"min": 5.0, "max": 25.0},
    "k_abs_center": {"min": 0.0, "max": 0.8},
}
if target_envelope_path.exists():
    try:
        target_envelope_data = json.loads(target_envelope_path.read_text(encoding="utf-8"))
        envelope = target_envelope_data.get("physical_feature_target_envelope", {})
        features = envelope.get("features", {})
        observed_bounds = {
            key: {"min": features.get(key, {}).get("min"), "max": features.get(key, {}).get("max")}
            for key in expected_target_bounds
        }
        missing_or_wrong = {
            key: {"expected": value, "observed": observed_bounds.get(key)}
            for key, value in expected_target_bounds.items()
            if observed_bounds.get(key) != value
        }
        expected_target_count_per_bin = 391
        expected_desired_total_count = 100000
        target_count_per_bin = int(envelope.get("target_count_per_bin") or 0)
        desired_total_count = int(envelope.get("desired_total_count") or 0)
        checks["physical_feature_target_envelope_config_gate"] = {
            "status": "PASS"
            if (
                target_envelope_data.get("status") == "ACTIVE"
                and not missing_or_wrong
                and int(envelope.get("bins", 0)) == 4
                and int(envelope.get("next_count", 0)) == 8000
                and target_count_per_bin == expected_target_count_per_bin
                and desired_total_count == expected_desired_total_count
            )
            else "FAIL",
            "file": str(target_envelope_path),
            "schema": target_envelope_data.get("schema"),
            "name": target_envelope_data.get("name"),
            "status_field": target_envelope_data.get("status"),
            "expected_bounds": expected_target_bounds,
            "observed_bounds": observed_bounds,
            "bins": envelope.get("bins"),
            "target_count_per_bin": target_count_per_bin,
            "expected_target_count_per_bin": expected_target_count_per_bin,
            "desired_total_count": desired_total_count,
            "expected_desired_total_count": expected_desired_total_count,
            "next_count": envelope.get("next_count"),
            "missing_or_wrong": missing_or_wrong,
        }
    except Exception as exc:
        checks["physical_feature_target_envelope_config_gate"] = {
            "status": "FAIL",
            "file": str(target_envelope_path),
            "error": f"{type(exc).__name__}: {exc}",
        }
else:
    checks["physical_feature_target_envelope_config_gate"] = {
        "status": "FAIL",
        "file": str(target_envelope_path),
        "error": "missing config file",
    }

checks["adaptive_after_duo_wrapper_gate"] = check_script(
    "RUN_MARS56_ADAPTIVE_ACQUISITION_AFTER_DUO_20260708.sh",
    [
        "TARGET_ENVELOPE_CONFIG",
        "mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
        "--target-envelope-config",
        "ADAPTIVE_ACQUISITION_STATUS=FAIL_TARGET_ENVELOPE_MISSING",
        "target_envelope_config",
        "ADAPTIVE_ACQUISITION_STATUS=WOULD_BUILD_QUEUE",
        "ADAPTIVE_ACQUISITION_STATUS=QUEUE_READY",
        "ADAPTIVE_ACQUISITION_STATUS=QUEUE_ALREADY_READY",
        "ADAPTIVE_ACQUISITION_STATUS=SKIP_UNIFORMITY_ALREADY_PASS",
        "adaptive_physical_acquisition_after_duo_latest_summary.json",
        "run_mars56_s4p_adaptive_physical_acquisition_round.sh",
        "merge_physical_feature_accepted_pool.py",
        "lp_nh_center,ls_nh_center,q_center,k_abs_center",
        "k_axis_policy",
        "dataset_rows.csv",
        "physical_feature_uniformity_summary.json",
        "sha256",
        "candidate_queues",
        "adaptive_runs",
        "RUN_EMX",
        "run_mars56_s4p_100k_chunk_from_queue.sh",
        "ADAPTIVE_EMX_STATUS=DONE",
        "ADAPTIVE_ACCEPTED_POOL_DIR",
        "adaptive_emx_command_finished_and_pool_merged",
        "accepted_inrange_pool_after_",
    ],
)

merge_accepted_pool_script = root / "rfic-transformer-inverse-design/scripts/merge_physical_feature_accepted_pool.py"
if merge_accepted_pool_script.exists():
    completed = subprocess.run(
        ["python3", "-m", "py_compile", str(merge_accepted_pool_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    merge_text = merge_accepted_pool_script.read_text(errors="replace")
    required_merge_tokens = [
        "Merge accepted physical-feature rows",
        "USE_AS_NEXT_ACCEPTED_POOL_FOR_ADAPTIVE_PLANNING",
        "Surrogate candidate predictions are intentionally not accepted as labels",
        "k_abs_center=abs(k_center)",
        "audit_physical_feature_uniformity.py",
        "--k-mode",
        "magnitude",
        "accepted_pool_merge_summary.json",
        "accepted_pool_merge_report.md",
    ]
    checks["accepted_pool_merge_script_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in merge_text for token in required_merge_tokens) else "FAIL",
        "file": str(merge_accepted_pool_script),
        "returncode": completed.returncode,
        "missing_tokens": [token for token in required_merge_tokens if token not in merge_text],
        "stderr_tail": (completed.stderr or "").splitlines()[-5:],
    }
else:
    checks["accepted_pool_merge_script_gate"] = {
        "status": "FAIL",
        "file": str(merge_accepted_pool_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

accepted_pool_merge_behavior_script = root / "CHECK_LOCAL_ACCEPTED_POOL_MERGE_BEHAVIOR_20260708.sh"
if accepted_pool_merge_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(accepted_pool_merge_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_pool_merge_tokens = [
        "ACCEPTED_POOL_MERGE_CASE=base_plus_adaptive_training status=PASS",
        "ACCEPTED_POOL_MERGE_BEHAVIOR_STATUS=PASS",
    ]
    checks["accepted_pool_merge_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_pool_merge_tokens) else "FAIL",
        "file": str(accepted_pool_merge_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_pool_merge_tokens,
        "missing_tokens": [token for token in required_pool_merge_tokens if token not in behavior_output],
        "stdout_tail": (completed.stdout or "").splitlines()[-10:],
        "stderr_tail": (completed.stderr or "").splitlines()[-10:],
    }
else:
    checks["accepted_pool_merge_behavior_gate"] = {
        "status": "FAIL",
        "file": str(accepted_pool_merge_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["production_watcher_resume_gate"] = check_script(
    "RUN_MARS56_RESUME_PRODUCTION_WATCHERS_AFTER_DUO_20260707.sh",
    [
        "DRY_RUN",
        "SSH_CONTROL_PATH",
        "watch_chunk08_checkpoint_merge_accept_20260706.sh",
        "watch_chunk08_pass_prepare_and_launch_first_100k_20260706.sh",
        "watch_production_100k_chunks_02_to_10_after_chunk08_20260706.sh",
        "resume_watcher",
        "nohup bash \"$script\"",
        "WATCHER_STATE",
        "WATCHER_RESUME_STATUS=PASS",
        "purpose=restart_existing_remote_watchers_only_no_new_generation_logic",
    ],
)

checks["production_queue_provenance_preflight_script_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/audit_mars56_s4p_candidate_queue_provenance.py",
    [
        "CANDIDATE_QUEUE_PROVENANCE_STATUS",
        "source_selection_csv",
        "geometry_space_filling_no_physical_labels",
        "selection_has_predicted_physical_features",
        "selection_has_target_physical_bins",
        "candidate_rows_meet_expected_count",
        "STOP_BEFORE_EMX_QUEUE_NOT_PROVEN_PHYSICAL_TARGETED",
    ],
    syntax=False,
)

checks["production_1m_plan_contract_script_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/build_mars56_1m_production_plan_contract.py",
    [
        "mars56_s4p_1m_physical_feature_uniformity_contract",
        "expected_chunks",
        "expected_per_chunk",
        "expected_total_rows",
        "chunk_{index:03d}_100k_after_chunk08_pass",
        "cumulative_{index * 100:04d}k_after_chunk08_pass",
        "Lp",
        "Ls",
        "Q",
        "K",
        "PRODUCTION_PLAN_CONTRACT_STATUS=CONTRACT_WRITTEN_NOT_EVIDENCE",
    ],
    syntax=False,
)

checks["production_1m_plan_contract_audit_script_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/audit_mars56_1m_production_plan_contract.py",
    [
        "PRODUCTION_PLAN_CONTRACT_AUDIT_STATUS",
        "formal_chunk_",
        "cumulative_checkpoint_",
        "formal_pass_count_meets_contract",
        "cumulative_pass_count_meets_contract",
        "total_nonempty_s4p_meets_contract",
        "missing_formal_chunk_in_evidence",
        "missing_cumulative_checkpoint_in_evidence",
        "ONE_MILLION_PLAN_CONTRACT_EVIDENCE_PASS",
    ],
    syntax=False,
)

checks["production_100k_runner_strict_acceptance_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/run_mars56_s4p_100k_chunk_from_queue.sh",
    [
        "run_candidate_queue_dataset_parallel.py",
        "candidate_queue_provenance_preflight",
        "audit_mars56_s4p_candidate_queue_provenance.py",
        "candidate_queue_preflight",
        "candidate_queue_provenance_status",
        "mars56_s4p_candidate_queue_provenance_summary.json",
        "--expected-frequency-start-ghz 5",
        "--expected-frequency-stop-ghz 60",
        "--expected-frequency-step-ghz 0.5",
        "--expected-frequency-points 111",
        "--expected-touchstone-extension .s4p",
        "--expected-ports 4",
        "--expected-port-mode single_ended_shield_grounded",
        "--expected-pin-purpose 51",
        "run_mars56_s4p_physical_checkpoint_pipeline.sh",
        "--require-four-d-gate",
        "--require-plots",
        "--min-1d-occupied-frac",
        "--min-1d-entropy-frac",
        "--max-1d-bin-imbalance",
        "--min-pair-occupied-frac",
        "--min-pair-entropy-frac",
        "--min-four-d-occupied-frac",
        "audit_mars56_s4p_million_chunk_checkpoint.py",
        "checkpoint_proof",
        "expected_count",
        "min_valid",
        "uniformity_manifest",
        "physical_uniformity_gate",
        "target_ranges",
        "uniformity.ranges",
        "uniformity.one_dimensional_uniformity",
        "uniformity.pairwise_uniformity",
        "uniformity.four_dimensional_uniformity",
        "four_dimensional_uniformity.occupied_fraction",
        '"traceability"',
        "traceability.details_missing",
        "traceability.{key}=MISSING",
        "stable_manifest_rows",
        "stable_unique_evaluations",
        "response_feature_rows",
        "response_unique_evaluations",
        "response_dataset_rows",
        "response_dataset_unique_evaluations",
        "enriched_rows",
        "enriched_unique_evaluations",
        "training_rows",
        "training_unique_evaluations",
        "uniformity_manifest.visual_artifact_count",
        "uniformity_manifest.require_plots",
        "uniformity.k_sign_diagnostics",
        "uniformity.k_sign_diagnostics.signed_k_count",
        "uniformity.k_sign_diagnostics.uniformity_k_axis",
        "strict_acceptance",
        "checkpoint_proof_reasons",
        "ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK",
        "STOP_BEFORE_NEXT_100K_CHUNK",
    ],
)

runner_behavior_script = root / "CHECK_LOCAL_RUNNER_STRICT_ACCEPTANCE_BEHAVIOR_20260708.sh"
if runner_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(runner_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_runner_behavior_tokens = [
        "RUNNER_STRICT_ACCEPTANCE_CASE=complete_chain status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=missing_traceability_details status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=missing_uniformity_manifest_details status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=low_traceability_row status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=missing_k_sign_diagnostics status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=missing_model_metrics status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=zero_model_test_rows status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=missing_queue_preflight status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=low_four_d_occupancy status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=low_one_d_entropy status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_CASE=low_pair_occupancy status=PASS",
        "RUNNER_STRICT_ACCEPTANCE_BEHAVIOR_STATUS=PASS",
    ]
    checks["production_100k_runner_strict_acceptance_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_runner_behavior_tokens) else "FAIL",
        "file": str(runner_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_runner_behavior_tokens,
        "missing_tokens": [token for token in required_runner_behavior_tokens if token not in behavior_output],
    }
else:
    checks["production_100k_runner_strict_acceptance_behavior_gate"] = {
        "status": "FAIL",
        "file": str(runner_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

queue_preflight_behavior_script = root / "CHECK_LOCAL_QUEUE_PROVENANCE_PREFLIGHT_BEHAVIOR_20260708.sh"
if queue_preflight_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(queue_preflight_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_queue_behavior_tokens = [
        "QUEUE_PROVENANCE_PREFLIGHT_CASE=physical_targeted status=PASS",
        "QUEUE_PROVENANCE_PREFLIGHT_CASE=geometry_only status=PASS",
        "QUEUE_PROVENANCE_PREFLIGHT_CASE=too_few_rows status=PASS",
        "QUEUE_PROVENANCE_PREFLIGHT_CASE=missing_selection_source status=PASS",
        "QUEUE_PROVENANCE_PREFLIGHT_BEHAVIOR_STATUS=PASS",
    ]
    checks["production_queue_provenance_preflight_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_queue_behavior_tokens) else "FAIL",
        "file": str(queue_preflight_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_queue_behavior_tokens,
        "missing_tokens": [token for token in required_queue_behavior_tokens if token not in behavior_output],
    }
else:
    checks["production_queue_provenance_preflight_behavior_gate"] = {
        "status": "FAIL",
        "file": str(queue_preflight_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["adaptive_physical_acquisition_round_script_gate"] = check_script(
    "rfic-transformer-inverse-design/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh",
    [
        "plan_physical_feature_balanced_acquisition.py",
        "build_physical_feature_surrogate_candidate_predictions.py",
        "select_physical_feature_targeted_candidate_geometries.py",
        "materialize_physical_feature_targeted_s4p_queue.py",
        "audit_mars56_s4p_candidate_queue_provenance.py",
        "lp_nh_center,ls_nh_center,q_center,k_abs_center",
        "k_axis_policy",
        "--target-envelope-config",
        "TARGET_ENVELOPE_CONFIG",
        "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION",
        "DO_NOT_RUN_EMX_FIX_TARGETING_FIRST",
        "selected_inside_target_bin_count",
        "provenance",
    ],
)

adaptive_acquisition_behavior_script = root / "CHECK_LOCAL_ADAPTIVE_ACQUISITION_ROUND_BEHAVIOR_20260708.sh"
if adaptive_acquisition_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(adaptive_acquisition_behavior_script)],
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_adaptive_acquisition_tokens = [
        "ADAPTIVE_ACQUISITION_ROUND_BEHAVIOR_STATUS=PASS",
        "ADAPTIVE_TARGET_ENVELOPE_CONFIG_STATUS=PASS",
        "ADAPTIVE_PLANNING_ENVELOPE_SOURCE=configured_feature_bounds",
        "ADAPTIVE_TOPUP_ALLOCATION_POLICY=deficit_first_then_low_count_topup",
        "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION",
        "CANDIDATE_QUEUE_PROVENANCE_STATUS=PASS",
    ]
    checks["adaptive_physical_acquisition_round_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_adaptive_acquisition_tokens) else "FAIL",
        "file": str(adaptive_acquisition_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_adaptive_acquisition_tokens,
        "missing_tokens": [token for token in required_adaptive_acquisition_tokens if token not in behavior_output],
    }
else:
    checks["adaptive_physical_acquisition_round_behavior_gate"] = {
        "status": "FAIL",
        "file": str(adaptive_acquisition_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

plan_contract_behavior_script = root / "CHECK_LOCAL_1M_PRODUCTION_PLAN_CONTRACT_BEHAVIOR_20260708.sh"
if plan_contract_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(plan_contract_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_plan_contract_tokens = [
        "PRODUCTION_PLAN_CONTRACT_CASE=complete status=PASS",
        "PRODUCTION_PLAN_CONTRACT_CASE=missing_formal_chunk status=PASS",
        "PRODUCTION_PLAN_CONTRACT_CASE=low_formal_rows status=PASS",
        "PRODUCTION_PLAN_CONTRACT_CASE=formal_checkpoint_fail status=PASS",
        "PRODUCTION_PLAN_CONTRACT_CASE=missing_cumulative status=PASS",
        "PRODUCTION_PLAN_CONTRACT_CASE=cumulative_checkpoint_fail status=PASS",
        "PRODUCTION_PLAN_CONTRACT_BEHAVIOR_STATUS=PASS",
    ]
    checks["production_1m_plan_contract_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_plan_contract_tokens) else "FAIL",
        "file": str(plan_contract_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_plan_contract_tokens,
        "missing_tokens": [token for token in required_plan_contract_tokens if token not in behavior_output],
    }
else:
    checks["production_1m_plan_contract_behavior_gate"] = {
        "status": "FAIL",
        "file": str(plan_contract_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

final_audit_contract_gate_script = root / "CHECK_LOCAL_FINAL_1M_AUDIT_CONTRACT_GATE_20260708.sh"
if final_audit_contract_gate_script.exists():
    completed = subprocess.run(
        ["bash", str(final_audit_contract_gate_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_final_audit_contract_tokens = [
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=contract_required_in_final_pass status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_artifact_required_in_evidence_index status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_complete status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=formal_tag_behavior_missing_expected status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=formal_tag_behavior_unexpected_extra status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=cumulative_tag_behavior_missing_expected status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=cumulative_tag_behavior_unexpected_extra status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=cumulative_tag_behavior_duplicate_summary status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=global_behavior_missing_proof status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=global_behavior_low_unique status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=global_behavior_duplicate status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_missing_rate status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_local_dry_run status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_wrong_parallel status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_wrong_expected_parallel status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_slow_seconds status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_slow_eta status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_target_fail status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_missing_rate_md_flag status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_empty_rate_json status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=rate_behavior_empty_rate_md status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_complete status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_missing_one_d_uniformity status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_low_one_d_entropy status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_high_one_d_imbalance status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_low_pair_occupancy status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_low_pair_entropy status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_low_four_d_occupancy status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_missing_model_metrics status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_CASE=checkpoint_proof_zero_model_test_rows status=PASS",
        "FINAL_1M_AUDIT_CONTRACT_GATE_STATUS=PASS",
    ]
    checks["final_1m_audit_requires_plan_contract_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_final_audit_contract_tokens) else "FAIL",
        "file": str(final_audit_contract_gate_script),
        "returncode": completed.returncode,
        "required_cases": required_final_audit_contract_tokens,
        "missing_tokens": [token for token in required_final_audit_contract_tokens if token not in behavior_output],
    }
else:
    checks["final_1m_audit_requires_plan_contract_gate"] = {
        "status": "FAIL",
        "file": str(final_audit_contract_gate_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["post_duo_sync_start_wrapper_gate"] = check_script(
    "RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh",
    [
        "LOCAL_DRY_RUN",
        "RUN_LOCAL_GATES",
        "local remote_marker=0",
        "remote_marker=1",
        "SSH_CONTROL_PATH",
        "SSH_PERSIST",
        "POST_DUO_LOG_CAPTURE",
        "POST_DUO_LOG_REEXECED",
        "POST_DUO_STATUS_JSON",
        "DETACHED_STATUS_JSON",
        "RUN_LAUNCH_AUDIT",
        "LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO",
        "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN",
        "write_post_duo_status_json",
        "run_clean_local_gate",
        "-u RUN_LAUNCH_AUDIT",
        "-u LOCAL_DRY_RUN",
        "DRY_RUN_PASS",
        "local_dry_run",
        "remote_actions_executed",
        "POST_DUO_SYNC_AND_START_STATUS_JSON",
        "POST_DUO_LAUNCH_AUDIT_AUTO=START",
        "POST_DUO_LAUNCH_AUDIT_AUTO_RC",
        "SYNC_REMOTE_STACK",
        "START_WATCHER",
        "WATCH_ITERATIONS",
        "SLEEP_SECONDS",
        "STOP_ON_GOAL_PASS",
        "VERIFY_RUNNER_REQUIRED",
        "RUN_ADAPTIVE_ACQUISITION",
        "RUN_ADAPTIVE_EMX",
        "TARGET_ENVELOPE_CONFIG_LOCAL",
        "TARGET_ENVELOPE_SHA256",
        "target_envelope_config",
        "target_envelope_sha256",
        "mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
        "EVIDENCE_INDEX_SCRIPT",
        "EVIDENCE_INDEX_SCRIPT_SHA256",
        "evidence_index_script",
        "evidence_index_script_sha256",
        "VERIFY_SYNC_RUNNER_SHA256",
        "VERIFY_SYNC_STACK_SHA256",
        "verify_sync_runner_script",
        "verify_sync_runner_script_sha256",
        "verify_sync_stack_script",
        "verify_sync_stack_script_sha256",
        "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh",
        "run_evidence_index",
        'RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"',
        "RUN_LOCAL_GATES=0: skipping recursive local readiness gates",
        "REMOTE_SYNC_100K_RUNNER",
        "SYNC_REMOTE_RUNNER=1",
        "REMOTE_SYNC_CHECKPOINT_STACK",
        "SYNC_REMOTE_CHECKPOINT_STACK=1",
        "SUPERVISOR_VERIFY_RUNNER",
        "MODE=verify-runner",
        "START_DETACHED_CONTINUOUS_WATCHER",
        "ACTION=start",
        "RUN_EVIDENCE_INDEX=1",
        "RUN_ADAPTIVE_ACQUISITION=",
        "RUN_ADAPTIVE_EMX=",
        "RUN_AUDIT=1",
        "POST_DUO_SYNC_AND_START_STATUS=PASS",
    ],
)

post_duo_sync_start_gate_script = root / "CHECK_LOCAL_POST_DUO_SYNC_START_GATE_20260708.sh"
if post_duo_sync_start_gate_script.exists():
    completed = subprocess.run(
        ["bash", str(post_duo_sync_start_gate_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_post_duo_sync_start_tokens = [
        "POST_DUO_SYNC_START_GATE_CASE=sync_and_start_dry_run status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=sync_before_start_order status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=verify_only_no_start_dry_run status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=logging_status_json_dry_run status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=auto_launch_audit_dry_run status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=detached_watcher_ssh_env_dry_run status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=detached_config_mismatch_no_restart status=PASS",
        "POST_DUO_SYNC_START_GATE_CASE=detached_config_mismatch_restart status=PASS",
        "POST_DUO_SYNC_START_GATE_STATUS=PASS",
    ]
    checks["post_duo_sync_start_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_post_duo_sync_start_tokens) else "FAIL",
        "file": str(post_duo_sync_start_gate_script),
        "returncode": completed.returncode,
        "required_cases": required_post_duo_sync_start_tokens,
        "missing_tokens": [token for token in required_post_duo_sync_start_tokens if token not in behavior_output],
    }
else:
    checks["post_duo_sync_start_behavior_gate"] = {
        "status": "FAIL",
        "file": str(post_duo_sync_start_gate_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["post_duo_launch_audit_script_gate"] = check_script(
    "CHECK_MARS56_POST_DUO_SYNC_START_LAUNCH_STATUS_20260708.sh",
    [
        "POST_DUO_LAUNCH_AUDIT_STATUS=PASS",
        "POST_DUO_LAUNCH_AUDIT_STATUS=FAIL",
        "POST_DUO_LAUNCH_AUDIT_SUMMARY",
        "WRAPPER_STATUS_JSON",
        "DETACHED_STATUS_JSON",
        "REQUIRE_SYNC_REMOTE_STACK",
        "REQUIRE_START_WATCHER",
        "REQUIRE_WATCH_ITERATIONS_ZERO",
        "ALLOW_LOCAL_DRY_RUN",
        "DRY_RUN_PASS",
        "wrapper_local_dry_run_true",
        "wrapper_remote_actions_not_executed",
        "wrapper_state_not_PASS",
        "wrapper_return_code_not_zero",
        "wrapper_sync_remote_stack_not_true",
        "wrapper_start_watcher_not_true",
        "wrapper_run_adaptive_acquisition_not_true",
        "wrapper_run_adaptive_emx_not_true",
        "wrapper_run_evidence_index_not_true",
        "wrapper_target_envelope_config_missing_or_unexpected",
        "wrapper_target_envelope_sha256_invalid",
        "wrapper_evidence_index_script_missing_or_unexpected",
        "wrapper_evidence_index_script_sha256_invalid",
        "wrapper_verify_sync_runner_script_missing_or_unexpected",
        "wrapper_verify_sync_runner_script_sha256_invalid",
        "wrapper_verify_sync_stack_script_missing_or_unexpected",
        "wrapper_verify_sync_stack_script_sha256_invalid",
        "wrapper_watch_iterations_not_zero",
        "detached_state_not_active",
        "detached_pid_missing",
        "ssh_control_path_mismatch",
        "ssh_persist_mismatch",
        "run_adaptive_acquisition_mismatch",
        "run_adaptive_emx_mismatch",
        "detached_local_dry_run_true",
        "detached_{key}_not_true",
        "run_adaptive_acquisition",
        "run_adaptive_emx",
        "target_envelope_config",
        "target_envelope_sha256",
        "evidence_index_script",
        "evidence_index_script_sha256",
        "verify_sync_runner_script",
        "verify_sync_runner_script_sha256",
        "verify_sync_stack_script",
        "verify_sync_stack_script_sha256",
        "run_checkpoint",
        "run_cumulative",
        "run_evidence_index",
        "run_audit",
    ],
)

post_duo_launch_audit_behavior_script = root / "CHECK_LOCAL_POST_DUO_LAUNCH_AUDIT_BEHAVIOR_20260708.sh"
if post_duo_launch_audit_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(post_duo_launch_audit_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_post_duo_launch_audit_tokens = [
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=complete_launch status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=wrapper_fail status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=missing_detached status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=ssh_mismatch status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=adaptive_emx_disabled status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=missing_evidence_index_script status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=bad_evidence_index_sha status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=missing_verify_sync_stack_script status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=bad_verify_sync_runner_sha status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=detached_local_dry_run status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=wrapper_local_dry_run status=PASS",
        "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_STATUS=PASS",
    ]
    checks["post_duo_launch_audit_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_post_duo_launch_audit_tokens) else "FAIL",
        "file": str(post_duo_launch_audit_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_post_duo_launch_audit_tokens,
        "missing_tokens": [token for token in required_post_duo_launch_audit_tokens if token not in behavior_output],
    }
else:
    checks["post_duo_launch_audit_behavior_gate"] = {
        "status": "FAIL",
        "file": str(post_duo_launch_audit_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["noninteractive_ssh_probe_gate"] = check_script(
    "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh",
    [
        "BatchMode=yes",
        "MARS56_NONINTERACTIVE_SSH_PROBE_STATUS",
        "PASS_REUSABLE_CONTROL_CONNECTION",
        "WAITING_FOR_INTERACTIVE_AUTH",
        "NETWORK_TIMEOUT",
        "logs/mars56_noninteractive_ssh_probe",
        "reports/mars56_million_campaign_live_status.json",
        "remote_reconnect_needed",
        "latest_remote_verification_status",
        "latest_noninteractive_ssh_probe",
        "ssh_control_path",
        "No reusable local SSH control connection",
    ],
)

checks["interactive_ssh_bootstrap_gate"] = check_script(
    "START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    [
        "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh",
        "ControlMaster=auto",
        "ControlPersist=${SSH_PERSIST}",
        "ControlPath=${SSH_CONTROL_PATH}",
        "-J \"${USER_NAME}@${JUMP_HOST}\"",
        "PASS_REUSABLE_CONTROL_CONNECTION",
        "MARS56_INTERACTIVE_BOOTSTRAP_REMOTE_READY",
        "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=DRY_RUN_COMMAND_READY",
        "RUN_PROBE_AFTER",
        "KEEP_REMOTE_SHELL",
        "logs/mars56_interactive_ssh_bootstrap",
        "Guacamole alone does not create this local reusable control socket.",
    ],
)

interactive_ssh_bootstrap_behavior_script = root / "CHECK_LOCAL_INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_20260708.sh"
if interactive_ssh_bootstrap_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(interactive_ssh_bootstrap_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_interactive_bootstrap_tokens = [
        "INTERACTIVE_SSH_BOOTSTRAP_CASE=static_contract status=PASS",
        "INTERACTIVE_SSH_BOOTSTRAP_CASE=dry_run_writes_status status=PASS",
        "INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_STATUS=PASS",
    ]
    checks["interactive_ssh_bootstrap_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_interactive_bootstrap_tokens) else "FAIL",
        "file": str(interactive_ssh_bootstrap_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_interactive_bootstrap_tokens,
        "missing_tokens": [token for token in required_interactive_bootstrap_tokens if token not in behavior_output],
    }
else:
    checks["interactive_ssh_bootstrap_behavior_gate"] = {
        "status": "FAIL",
        "file": str(interactive_ssh_bootstrap_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["wait_for_ssh_start_watcher_gate"] = check_script(
    "RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh",
    [
        "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh",
        "RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh",
        "START_SCRIPT",
        "BOOTSTRAP_SCRIPT",
        "PASS_REUSABLE_CONTROL_CONNECTION",
        "WAITING_FOR_INTERACTIVE_AUTH",
        "SSH_WAIT_STATUS=STARTED_DRY_RUN",
        "SSH_WAIT_STATUS=REQUESTED_ITERATIONS_DONE",
        "WAIT_STATUS_JSON",
        "write_wait_status_json",
        "interactive_bootstrap_script",
        "interactive_bootstrap_command",
        "interactive_bootstrap_dry_run_command",
        "interactive_bootstrap_success_status",
        "recommended_next_action",
        "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION",
        "start_env_policy",
        "production_explicit_env_on_ssh_ready",
        "LOCAL_DRY_RUN=0",
        "RUN_LAUNCH_AUDIT=1",
        "POST_DUO_LOG_CAPTURE=1",
        "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0",
        "WAIT_ITERATIONS=0 means wait forever",
    ],
)

checks["wait_for_ssh_detached_launcher_gate"] = check_script(
    "START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh",
    [
        "RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh",
        "ACTION=start|status",
        "WAIT_FOR_SSH_DETACHED_STATUS=STARTED",
        "WAIT_FOR_SSH_DETACHED_STATUS=RUNNING",
        "WAIT_FOR_SSH_DETACHED_STATUS=NOT_RUNNING",
        "WAIT_FOR_SSH_DETACHED_STATUS=CONFIG_MISMATCH",
        "WAIT_FOR_SSH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH",
        "WAIT_FOR_SSH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0",
        "START_SCRIPT",
        "START_SCRIPT_SHA",
        "WATCHER_SHA",
        "watcher_sha256",
        "start_script_sha256",
        "RESTART_ON_CONFIG_MISMATCH",
        "CLEANUP_STALE_WAITERS",
        "cleanup_stale_orphan_waiters",
        "WAIT_FOR_SSH_STALE_WAITER_CLEANUP",
        "SIGTERM_ORPHAN_LOGIN_PIDS",
        "config_matches_requested",
        "mars56_wait_for_ssh_start_latest.pid",
        "mars56_wait_for_ssh_start_latest_detached_status.json",
        "nohup env",
        "WAIT_LOG_CAPTURE=0",
        "WAIT_STATUS_JSON=",
        "< /dev/null",
        "DRY_RUN_PROBE_STATUSES",
    ],
)

wait_runtime_uniqueness_script = root / "CHECK_LOCAL_WAIT_FOR_SSH_RUNTIME_UNIQUENESS_20260708.sh"
if wait_runtime_uniqueness_script.exists():
    completed = subprocess.run(
        ["bash", str(wait_runtime_uniqueness_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    runtime_output = (completed.stdout or "") + (completed.stderr or "")
    required_wait_runtime_tokens = [
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_screen status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_runner_login status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=no_orphan_runner_login status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_watcher_process status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=detached_status_running status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=wait_status_bootstrap_fields status=PASS",
        "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_STATUS=PASS",
    ]
    checks["wait_for_ssh_runtime_uniqueness_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in runtime_output for token in required_wait_runtime_tokens) else "FAIL",
        "file": str(wait_runtime_uniqueness_script),
        "returncode": completed.returncode,
        "required_cases": required_wait_runtime_tokens,
        "missing_tokens": [token for token in required_wait_runtime_tokens if token not in runtime_output],
    }
else:
    checks["wait_for_ssh_runtime_uniqueness_gate"] = {
        "status": "FAIL",
        "file": str(wait_runtime_uniqueness_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["local_status_summary_gate"] = check_script(
    "SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh",
    [
        "reports/mars56_1m_current_status_latest.json",
        "reports/mars56_1m_current_status_latest_CN.md",
        "RUN_SCREEN_STATUS",
        "screen_status_probe_run",
        "remote_auth_status",
        "goal_completion_status",
        "NOT_PROVEN_LOCAL_ONLY",
        "last_known_remote_chunk08_s4p_count",
        "accepted_pool_after_chunk05_four_d_occupied_fraction",
        "checkpoint_contract",
        "adaptive_targeting_contract",
        "throughput_contract",
        "expected_parallel_jobs",
        "target_seconds_per_accepted_row",
        "target_days_per_100k",
        "max_seconds_per_accepted_row_gate",
        "max_days_per_100k_gate",
        "target_status_from_latest_local_snapshot",
        "gate_status_from_latest_local_snapshot",
        "checkpoint_stack_sync_contract",
        "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh",
        "verify_script_sha256",
        "target_envelope_sha256",
        "LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE",
        "REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=PASS",
        "required_contract_fields",
        "post_duo_wrapper_latest",
        "interactive_ssh_bootstrap",
        "wait_for_ssh_runtime_contract",
        "runtime_status",
        "screen_session_count",
        "runner_login_process_count",
        "orphan_runner_login_process_count",
        "watcher_process_count",
        "wait_status_bootstrap_command",
        "wait-for-SSH runtime 唯一性",
        "START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
        "CHECK_LOCAL_INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_20260708.sh",
        "mars56_interactive_ssh_bootstrap_latest.json",
        "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION",
        "dry_run_command",
        "real_command",
        "recommended_next_action",
        "交互 SSH/Duo 恢复入口",
        "local_dry_run",
        "remote_actions_executed",
        "DRY_RUN_ONLY_NOT_REMOTE_EVIDENCE",
        "post-Duo wrapper 最新状态",
        "mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
        "configured_feature_bounds",
        "desired_total_count",
        "target_count_per_bin",
        "four_d_bin_count",
        "wait_for_ssh_watcher_version",
        "local_status_refresh_version",
        "local_status_refresh_freshness_status",
        "local_status_refresh_age_seconds",
        "local_status_refresh_max_age_seconds",
        "latest_refresh_freshness_status",
        "latest_refresh_age_seconds",
        "latest_refresh_max_age_seconds",
        "refresh_status_json",
        "refresh_log_dir",
        "local_dry_run",
        "watcher_sha256",
        "start_script_sha256",
        "run_script_sha256",
        "summary_script_sha256",
        "formal_100k_chunks_required",
        "rows_per_formal_chunk_required",
        "cumulative_prefix_checkpoints_required",
        "min_1d_occupied_fraction",
        "min_1d_entropy_fraction",
        "max_1d_bin_imbalance",
        "min_pair_occupied_fraction",
        "min_pair_entropy_fraction",
        "min_four_d_occupied_fraction",
        "1D marginal",
        "pairwise",
        "Adaptive 补采样正式物理范围",
        "100k checkpoint 正式合同",
        "Local automation readiness is evidence only",
    ],
)

checks["local_status_refresh_gate"] = check_script(
    "RUN_MARS56_1M_LOCAL_STATUS_REFRESH_20260708.sh",
    [
        "SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh",
        "SUMMARY_SCRIPT_SHA",
        "summary_script_sha256",
        "RUN_PROBE_EACH_REFRESH",
        "REFRESH_ITERATIONS=0 means refresh forever",
        "LOCAL_STATUS_REFRESH_STATUS=REFRESHED",
        "LOCAL_STATUS_REFRESH_STATUS=REQUESTED_ITERATIONS_DONE",
        "reports/mars56_1m_current_status_latest.json",
        "reports/mars56_1m_current_status_latest_CN.md",
    ],
)

checks["local_status_refresh_detached_gate"] = check_script(
    "START_MARS56_1M_LOCAL_STATUS_REFRESH_DETACHED_20260708.sh",
    [
        'ACTION="${ACTION:-start}"',
        "ACTION must be start or status",
        "mars56_1m_local_status_refresh",
        "screen -dmS",
        "LOCAL_STATUS_REFRESH_DETACHED_STATUS=RUNNING",
        "LOCAL_STATUS_REFRESH_DETACHED_STATUS=STARTED",
        "run_script_sha256",
        "SUMMARY_SCRIPT_SHA",
        "summary_script_sha256",
        "REFRESH_LOG_DIR",
        "REFRESH_STATUS_JSON",
        "refresh_log_dir",
        "refresh_status_json",
        "config_matches_requested",
        "LOCAL_STATUS_REFRESH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH",
        "RESTART_ON_CONFIG_MISMATCH",
    ],
)

wait_for_ssh_behavior_script = root / "CHECK_LOCAL_WAIT_FOR_SSH_START_GATE_20260708.sh"
if wait_for_ssh_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(wait_for_ssh_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_wait_for_ssh_tokens = [
        "WAIT_FOR_SSH_START_GATE_CASE=start_after_dry_probe",
        "WAIT_FOR_SSH_START_GATE_CASE=wait_iterations_done",
        "WAIT_FOR_SSH_START_GATE_CASE=detached_dry_run",
        "WAIT_FOR_SSH_START_GATE_CASE=wait_config_mismatch_no_restart status=PASS",
        "WAIT_FOR_SSH_START_GATE_CASE=wait_config_mismatch_restart",
        "LOCAL_DRY_RUN=0",
        "RUN_LAUNCH_AUDIT=1",
        "POST_DUO_LOG_CAPTURE=1",
        "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0",
        "WAIT_FOR_SSH_START_GATE_STATUS=PASS",
    ]
    checks["wait_for_ssh_start_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_wait_for_ssh_tokens) else "FAIL",
        "file": str(wait_for_ssh_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_wait_for_ssh_tokens,
        "missing_tokens": [token for token in required_wait_for_ssh_tokens if token not in behavior_output],
    }
else:
    checks["wait_for_ssh_start_behavior_gate"] = {
        "status": "FAIL",
        "file": str(wait_for_ssh_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

status_refresh_behavior_script = root / "CHECK_LOCAL_STATUS_REFRESH_DETACHED_GATE_20260708.sh"
if status_refresh_behavior_script.exists():
    completed = subprocess.run(
        ["bash", str(status_refresh_behavior_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    behavior_output = (completed.stdout or "") + (completed.stderr or "")
    required_status_refresh_tokens = [
        "STATUS_REFRESH_DETACHED_GATE_CASE=start_matching_config status=PASS",
        "STATUS_REFRESH_DETACHED_GATE_CASE=already_running_matching_config status=PASS",
        "STATUS_REFRESH_DETACHED_GATE_CASE=config_mismatch_no_restart status=PASS",
        "STATUS_REFRESH_DETACHED_GATE_CASE=config_mismatch_restart status=PASS",
        "STATUS_REFRESH_DETACHED_GATE_CASE=summary_contract_refresh status=PASS",
        "STATUS_REFRESH_DETACHED_GATE_STATUS=PASS",
    ]
    checks["local_status_refresh_detached_behavior_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in behavior_output for token in required_status_refresh_tokens) else "FAIL",
        "file": str(status_refresh_behavior_script),
        "returncode": completed.returncode,
        "required_cases": required_status_refresh_tokens,
        "missing_tokens": [token for token in required_status_refresh_tokens if token not in behavior_output],
    }
else:
    checks["local_status_refresh_detached_behavior_gate"] = {
        "status": "FAIL",
        "file": str(status_refresh_behavior_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }


status_refresh_freshness_script = root / "CHECK_LOCAL_STATUS_REFRESH_FRESHNESS_GATE_20260708.sh"
if status_refresh_freshness_script.exists():
    completed = subprocess.run(
        ["bash", str(status_refresh_freshness_script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    freshness_output = (completed.stdout or "") + (completed.stderr or "")
    required_freshness_tokens = [
        "LOCAL_STATUS_REFRESH_FRESHNESS_GATE_STATUS=PASS",
        "refresh_status_json=",
        "refresh_age_seconds=",
        "max_age_seconds=",
        "detached_state=",
        "refresh_state=",
    ]
    checks["local_status_refresh_freshness_gate"] = {
        "status": "PASS" if completed.returncode == 0 and all(token in freshness_output for token in required_freshness_tokens) else "FAIL",
        "file": str(status_refresh_freshness_script),
        "returncode": completed.returncode,
        "required_cases": required_freshness_tokens,
        "missing_tokens": [token for token in required_freshness_tokens if token not in freshness_output],
    }
else:
    checks["local_status_refresh_freshness_gate"] = {
        "status": "FAIL",
        "file": str(status_refresh_freshness_script),
        "returncode": None,
        "missing_tokens": ["script missing"],
    }

checks["legacy_geometry_campaign_guard"] = check_script(
    "MARS56_S4P_MILLION_CAMPAIGN_20260705.sh",
    [
        "Legacy MARS56 grounded S4P million-sample campaign.",
        "ALLOW_LEGACY_GEOMETRY_CAMPAIGN",
        "formal U8-gated *_100k_after_chunk08_pass datasets",
        "Lp/Ls/Q/|K| uniformity and per-100k model-checkpoint contract",
        "RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh",
        "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
        "exit 2",
    ],
)

live_status_path = root / "reports/mars56_million_campaign_live_status.json"
live_status = {}
if live_status_path.exists():
    try:
        live_status = json.loads(live_status_path.read_text())
    except Exception as exc:
        live_status = {"_parse_error": type(exc).__name__}

checks["live_status_boundary"] = {
    "status": "PASS" if live_status and "_parse_error" not in live_status else "FAIL",
    "file": str(live_status_path),
    "latest_verified_remote_time_cdt": live_status.get("latest_verified_remote_time_cdt"),
    "latest_verified_chunk08_nonempty_s4p_count": live_status.get("latest_verified_chunk08_nonempty_s4p_count"),
    "latest_verified_chunk08_target": live_status.get("latest_verified_chunk08_target"),
    "latest_noninteractive_ssh_probe": live_status.get("latest_noninteractive_ssh_probe"),
    "remote_reconnect_needed": live_status.get("remote_reconnect_needed"),
}

requirements = {
    "generate_1m_formal_s4p": {
        "needed_final_evidence": "CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh prints ONE_MILLION_GOAL_STATUS=PASS after remote audit.",
        "local_readiness_check": "final_1m_completion_audit",
    },
    "run_model_test_every_100k": {
        "needed_final_evidence": "10 per-chunk mars56_s4p_physical_checkpoint_pipeline_summary.json files with checkpoint_proof=PASS.",
        "local_readiness_check": "auto_completed_100k_checkpoint_gate",
    },
    "keep_cumulative_dataset_valid_every_100k": {
        "needed_final_evidence": "cumulative_0100k through cumulative_1000k checkpoint summaries with checkpoint_proof=PASS.",
        "local_readiness_check": "cumulative_100k_to_1000k_gate",
    },
    "uniform_Lp_Ls_Q_K": {
        "needed_final_evidence": "Each checkpoint pipeline is invoked with Lp/Ls/Q/|K| range bounds, pair/4D uniformity gates, and min valid row count equal to the expected count.",
        "local_readiness_check": "first100k_physical_model_gate, generic_100k_physical_model_gate, auto_completed_100k_checkpoint_gate, cumulative_100k_to_1000k_gate, checkpoint_runner_4d_reuse_behavior_gate",
    },
    "publish_distribution_evidence_every_100k": {
        "needed_final_evidence": "Each 100k checkpoint includes physical_feature_uniformity_manifest.json plus marginal histograms, pair scatter, pair occupancy heatmaps, and traceability summary for Lp/Ls/Q/|K|.",
        "local_readiness_check": "physical_uniformity_visual_manifest_gate, physical_checkpoint_traceability_gate, physical_checkpoint_traceability_behavior_gate, physical_checkpoint_pipeline_artifact_gate, physical_checkpoint_pipeline_summary_behavior_gate, chunk_audit_uniformity_visual_artifact_gate",
    },
    "target_sparse_physical_feature_bins_before_more_emx": {
        "needed_final_evidence": "When Lp/Ls/Q/|K| coverage is sparse, a new queue is generated from sparse physical-feature bins using |K| as k_abs_center, and provenance audit proves it is not a geometry-only queue before EMX time is spent.",
        "local_readiness_check": "adaptive_physical_acquisition_round_script_gate, adaptive_physical_acquisition_round_behavior_gate, adaptive_after_duo_wrapper_gate, accepted_pool_merge_script_gate, accepted_pool_merge_behavior_gate, production_queue_provenance_preflight_behavior_gate",
    },
    "do_not_mark_complete_without_remote_proof": {
        "needed_final_evidence": "Remote final audit must PASS; current local status only tracks readiness.",
        "local_readiness_check": "live_status_boundary",
    },
    "keep_checkpoint_evidence_traceable": {
        "needed_final_evidence": "Post-Duo supervisor transcripts are archived and summarized into reports/mars56_post_duo_supervisor_log_summary_20260707.json.",
        "local_readiness_check": "supervisor_transcript_summary_gate",
    },
    "continuous_post_duo_operation": {
        "needed_final_evidence": "A post-Duo watcher repeatedly runs strict per-100k checkpoint, cumulative checkpoint, final audit, and transcript summary until the campaign is complete.",
        "local_readiness_check": "continuous_100k_watch_gate, continuous_watch_order_behavior_gate",
    },
    "detached_unattended_post_duo_operation": {
        "needed_final_evidence": "After interactive Duo access, the continuous watcher can be started detached with a PID file, log file, JSON status, duplicate-run guard, and status command.",
        "local_readiness_check": "detached_continuous_watch_launcher_gate",
    },
    "weekly_100k_throughput_is_audited": {
        "needed_final_evidence": "After Duo, each supervisor/watch cycle can record measured seconds per accepted row, latest parallelism evidence, and ETA per 100k so the weekly checkpoint cadence is evidence-backed.",
        "local_readiness_check": "production_rate_eta_gate, production_rate_artifact_behavior_gate, supervisor_preflight_gate, continuous_100k_watch_gate, continuous_watch_order_behavior_gate",
    },
    "every_100k_checkpoint_has_traceable_evidence_index": {
        "needed_final_evidence": "After Duo, each formal 100k and cumulative checkpoint can be indexed into JSON/Markdown evidence listing summaries, Lp/Ls/Q/|K| plots, manifests, source traceability summaries, training artifacts, model summaries, and missing-artifact reasons.",
        "local_readiness_check": "checkpoint_evidence_index_gate, supervisor_preflight_gate, continuous_100k_watch_gate, continuous_watch_order_behavior_gate",
    },
    "trace_every_100k_checkpoint_to_source_s4p": {
        "needed_final_evidence": "Each 100k pipeline includes physical_checkpoint_traceability_summary.json proving stable index/source path and row-count/evaluation consistency across response features, geometry enrichment, inverse-training table, and model checkpoint.",
        "local_readiness_check": "physical_checkpoint_traceability_gate, physical_checkpoint_traceability_behavior_gate, physical_checkpoint_pipeline_artifact_gate, physical_checkpoint_pipeline_summary_behavior_gate, checkpoint_evidence_index_gate",
    },
    "resume_remote_production_watchers_after_disconnect": {
        "needed_final_evidence": "After Duo, missing remote watcher processes for chunk08 checkpoint, first100k launch, and chunk002-010 production can be restarted from the existing MARS watcher scripts.",
        "local_readiness_check": "production_watcher_resume_gate",
    },
    "verify_remote_100k_runner_before_resuming_production": {
        "needed_final_evidence": "After Duo, the remote 100k production runner plus checkpoint stack hashes, syntax, strict checkpoint-proof tokens, and visual evidence tokens match the local hardened versions before production watchers are resumed.",
        "local_readiness_check": "remote_100k_runner_verify_sync_gate, remote_checkpoint_stack_verify_sync_gate, supervisor_preflight_gate, continuous_100k_watch_gate",
    },
    "one_command_post_duo_sync_and_start": {
        "needed_final_evidence": "After Duo, a single wrapper can sync the hardened runner/checkpoint/contract stack, verify remote runner state, and start the detached continuous watcher with evidence-index and final-audit modes enabled.",
        "local_readiness_check": "post_duo_sync_start_wrapper_gate, post_duo_sync_start_behavior_gate",
    },
    "audit_post_duo_launch_handoff": {
        "needed_final_evidence": "After the post-Duo wrapper runs, the wrapper latest status JSON and detached watcher latest status JSON must prove PASS/STARTED-or-RUNNING, matching SSH control path, non-dry-run continuous watcher, and checkpoint/cumulative/evidence-index/final-audit modes enabled.",
        "local_readiness_check": "post_duo_launch_audit_script_gate, post_duo_launch_audit_behavior_gate",
    },
    "accept_each_100k_only_after_strict_chunk_proof": {
        "needed_final_evidence": "The queue-driven 100k runner accepts a chunk only after EMX generation, strict physical checkpoint proof, and chunk audit pass.",
        "local_readiness_check": "production_100k_runner_strict_acceptance_gate, production_100k_runner_strict_acceptance_behavior_gate",
    },
    "avoid_legacy_geometry_only_million_launch": {
        "needed_final_evidence": "The old geometry-uniform million launcher is guarded so it cannot be mistaken for the active U8-gated physical-feature production flow.",
        "local_readiness_check": "legacy_geometry_campaign_guard",
    },
}

all_local_ready = all(
    item.get("status") == "PASS"
    for group in checks.values()
    for item in (group.values() if isinstance(group, dict) and all(isinstance(v, dict) for v in group.values()) else [group])
    if isinstance(item, dict) and "status" in item
)

summary = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "audit_type": "local_goal_readiness_only_no_mars_connection",
    "objective": "Generate 1,000,000 MARS56 .s4p rows and run a physical/model checkpoint every 100k with Lp/Ls/Q/|K| uniform coverage.",
    "local_readiness_status": "PASS" if all_local_ready else "FAIL",
    "goal_completion_status": "NOT_PROVEN_LOCAL_ONLY",
    "requirements": requirements,
    "checks": checks,
}

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"LOCAL_GOAL_READINESS_STATUS={summary['local_readiness_status']}")
print("GOAL_COMPLETION_STATUS=NOT_PROVEN_LOCAL_ONLY")
print(f"REPORT={out}")
for name, check in checks.items():
    if isinstance(check, dict) and "status" in check:
        print(f"CHECK {name} status={check['status']}")
    elif isinstance(check, dict):
        statuses = []
        for key, value in check.items():
            if isinstance(value, dict) and "status" in value:
                statuses.append(f"{key}:{value['status']}")
        print(f"CHECK {name} " + " ".join(statuses))

if summary["local_readiness_status"] != "PASS":
    raise SystemExit(1)
PY
