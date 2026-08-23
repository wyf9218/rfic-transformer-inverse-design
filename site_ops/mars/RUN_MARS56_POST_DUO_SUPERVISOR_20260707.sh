#!/usr/bin/env bash
set -euo pipefail

# One entry point after Duo is available.
#
# Default is read-only:
#   bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Local-only proof consistency + 1M goal-readiness preflight:
#   MODE=preflight bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Read-only status + dry-run checkpoint scan:
#   MODE=dry-run bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + dry-run + run missing per-chunk checkpoints for completed formal 100k chunks:
#   MODE=checkpoint bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + dry-run + cumulative 100k/200k/... checkpoint plan:
#   MODE=cumulative-dry-run bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + run cumulative 100k/200k/... checkpoints from PASS source chunks:
#   MODE=cumulative bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + build the next physical-feature-targeted acquisition queue if the
# accepted pool is still sparse in Lp/Ls/Q/|K|:
#   MODE=adaptive-acquisition bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Read-only final 1M goal audit:
#   MODE=audit bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + resume remote production watchers if they are missing:
#   MODE=resume-watchers bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + verify the remote 100k runner matches the local strict checkpoint-proof runner:
#   MODE=verify-runner bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Read-only generation-rate and ETA audit:
#   MODE=rate bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Build/update the remote 100k checkpoint evidence index:
#   MODE=evidence-index bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Status + per-chunk checkpoints + cumulative checkpoints:
#   MODE=full bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# Same as checkpoint, but explicitly reruns existing non-PASS checkpoint summaries:
#   MODE=rerun-failed bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#
# The child scripts use SSH_CONTROL_PATH when set, so the first successful Duo
# login to best-linux can be reused by later steps.
#
# By default, the full supervisor transcript is archived locally under
# logs/mars56_post_duo_supervisor. Disable with SUPERVISOR_LOG_CAPTURE=0.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
MODE="${MODE:-status}"
RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"
SUPERVISOR_LOG_CAPTURE="${SUPERVISOR_LOG_CAPTURE:-1}"
SUPERVISOR_LOG_DIR="${SUPERVISOR_LOG_DIR:-$ROOT_DIR/logs/mars56_post_duo_supervisor}"
SUPERVISOR_LOG_PATH="${SUPERVISOR_LOG_PATH:-}"

case "$MODE" in
  preflight|status|dry-run|checkpoint|cumulative-dry-run|cumulative|adaptive-acquisition|audit|resume-watchers|verify-runner|rate|evidence-index|full|rerun-failed) ;;
  *)
    echo "ERROR: unsupported MODE=$MODE" >&2
    echo "Use MODE=preflight, status, dry-run, checkpoint, cumulative-dry-run, cumulative, adaptive-acquisition, audit, resume-watchers, verify-runner, rate, evidence-index, full, or rerun-failed." >&2
    exit 2
    ;;
esac
case "$RUN_ADAPTIVE_EMX" in
  0|1) ;;
  *)
    echo "ERROR: RUN_ADAPTIVE_EMX must be 0 or 1." >&2
    exit 2
    ;;
esac
case "$SUPERVISOR_LOG_CAPTURE" in
  0|1) ;;
  *)
    echo "ERROR: SUPERVISOR_LOG_CAPTURE must be 0 or 1." >&2
    exit 2
    ;;
esac
if [[ "$SUPERVISOR_LOG_DIR" == *$'\n'* || "$SUPERVISOR_LOG_DIR" == *$'\r'* || "$SUPERVISOR_LOG_PATH" == *$'\n'* || "$SUPERVISOR_LOG_PATH" == *$'\r'* ]]; then
  echo "ERROR: supervisor log path settings contain unsupported newline characters." >&2
  exit 2
fi

mkdir -p "$CONTROL_DIR"
export SSH_CONTROL_PATH SSH_PERSIST

if [ "$SUPERVISOR_LOG_CAPTURE" = "1" ]; then
  mkdir -p "$SUPERVISOR_LOG_DIR"
  if [ -z "$SUPERVISOR_LOG_PATH" ]; then
    stamp=$(date '+%Y%m%d_%H%M%S')
    mode_slug=${MODE//[^A-Za-z0-9_-]/_}
    SUPERVISOR_LOG_PATH="$SUPERVISOR_LOG_DIR/mars56_post_duo_supervisor_${mode_slug}_${stamp}.log"
  fi
  if [ "${SUPERVISOR_LOG_REEXECED:-0}" != "1" ]; then
    export MODE SSH_PERSIST CONTROL_DIR SSH_CONTROL_PATH
    export RUN_ADAPTIVE_EMX
    export SUPERVISOR_LOG_CAPTURE SUPERVISOR_LOG_DIR SUPERVISOR_LOG_PATH
    export SUPERVISOR_LOG_REEXECED=1
    bash "$0" "$@" 2>&1 | tee -a "$SUPERVISOR_LOG_PATH"
    exit "${PIPESTATUS[0]}"
  fi
fi

STATUS_SCRIPT="$ROOT_DIR/CHECK_MARS56_MILLION_CAMPAIGN_STATUS_20260707.sh"
AUTO_SCRIPT="$ROOT_DIR/RUN_MARS56_AUTO_CHECKPOINT_COMPLETED_100K_CHUNKS_AFTER_DUO_20260707.sh"
CUM_SCRIPT="$ROOT_DIR/RUN_MARS56_CUMULATIVE_100K_CHECKPOINTS_AFTER_DUO_20260707.sh"
AUDIT_SCRIPT="$ROOT_DIR/CHECK_MARS56_1M_GOAL_COMPLETION_AFTER_DUO_20260707.sh"
RESUME_WATCHERS_SCRIPT="$ROOT_DIR/RUN_MARS56_RESUME_PRODUCTION_WATCHERS_AFTER_DUO_20260707.sh"
VERIFY_RUNNER_SCRIPT="$ROOT_DIR/RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"
VERIFY_CHECKPOINT_STACK_SCRIPT="$ROOT_DIR/RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"
RATE_SCRIPT="$ROOT_DIR/CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh"
EVIDENCE_INDEX_SCRIPT="$ROOT_DIR/RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"
ADAPTIVE_ACQUISITION_SCRIPT="$ROOT_DIR/RUN_MARS56_ADAPTIVE_ACQUISITION_AFTER_DUO_20260708.sh"
CONSISTENCY_SCRIPT="$ROOT_DIR/CHECK_LOCAL_CHECKPOINT_PROOF_CONSISTENCY_20260707.sh"
READINESS_SCRIPT="$ROOT_DIR/CHECK_LOCAL_MARS56_1M_GOAL_READINESS_20260707.sh"

run_step() {
  local label="$1"
  shift
  printf '\n========== %s ==========\n' "$label"
  "$@"
}

verify_remote_execution_stack() {
  run_step "REMOTE_100K_RUNNER_VERIFY_BEFORE_MUTATION" bash "$VERIFY_RUNNER_SCRIPT"
  run_step "REMOTE_CHECKPOINT_STACK_VERIFY_BEFORE_MUTATION" bash "$VERIFY_CHECKPOINT_STACK_SCRIPT"
}

echo "MARS56 post-Duo supervisor"
echo "mode=$MODE"
echo "run_adaptive_emx=$RUN_ADAPTIVE_EMX"
echo "ssh_control_path=$SSH_CONTROL_PATH"
echo "ssh_persist=$SSH_PERSIST"
if [ "$SUPERVISOR_LOG_CAPTURE" = "1" ]; then
  echo "supervisor_log=$SUPERVISOR_LOG_PATH"
else
  echo "supervisor_log=disabled"
fi
echo "note=Local proof-consistency and 1M goal-readiness preflight runs before any SSH/Duo step."
echo "note=First SSH step may require password/Duo; later steps should reuse the control connection when supported."

run_step "LOCAL_CHECKPOINT_PROOF_CONSISTENCY_PREFLIGHT" bash "$CONSISTENCY_SCRIPT"
run_step "LOCAL_1M_GOAL_READINESS_PREFLIGHT" bash "$READINESS_SCRIPT"

if [ "$MODE" = "preflight" ]; then
  echo "SUPERVISOR_STATUS=PREFLIGHT_ONLY_DONE"
  exit 0
fi

run_step "READ_ONLY_STATUS" bash "$STATUS_SCRIPT"

case "$MODE" in
  status)
    echo "SUPERVISOR_STATUS=STATUS_ONLY_DONE"
    exit 0
    ;;
  dry-run)
    run_step "AUTO_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$AUTO_SCRIPT"
    run_step "CUMULATIVE_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$CUM_SCRIPT"
    echo "SUPERVISOR_STATUS=DRY_RUN_DONE"
    exit 0
    ;;
  checkpoint)
    verify_remote_execution_stack
    run_step "AUTO_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$AUTO_SCRIPT"
    run_step "AUTO_CHECKPOINT_RUN_MISSING" env DRY_RUN=0 bash "$AUTO_SCRIPT"
    echo "SUPERVISOR_STATUS=CHECKPOINT_MODE_DONE"
    exit 0
    ;;
  cumulative-dry-run)
    run_step "CUMULATIVE_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$CUM_SCRIPT"
    echo "SUPERVISOR_STATUS=CUMULATIVE_DRY_RUN_DONE"
    exit 0
    ;;
  cumulative)
    verify_remote_execution_stack
    run_step "CUMULATIVE_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$CUM_SCRIPT"
    run_step "CUMULATIVE_CHECKPOINT_RUN_MISSING" env DRY_RUN=0 bash "$CUM_SCRIPT"
    echo "SUPERVISOR_STATUS=CUMULATIVE_MODE_DONE"
    exit 0
    ;;
  adaptive-acquisition)
    verify_remote_execution_stack
    run_step "ADAPTIVE_ACQUISITION_DRY_RUN" env DRY_RUN=1 RUN_EMX="$RUN_ADAPTIVE_EMX" bash "$ADAPTIVE_ACQUISITION_SCRIPT"
    run_step "ADAPTIVE_ACQUISITION_BUILD_QUEUE" env DRY_RUN=0 RUN_EMX="$RUN_ADAPTIVE_EMX" bash "$ADAPTIVE_ACQUISITION_SCRIPT"
    echo "SUPERVISOR_STATUS=ADAPTIVE_ACQUISITION_MODE_DONE"
    exit 0
    ;;
  audit)
    run_step "ONE_MILLION_FINAL_GOAL_AUDIT" bash "$AUDIT_SCRIPT"
    echo "SUPERVISOR_STATUS=AUDIT_MODE_DONE"
    exit 0
    ;;
  resume-watchers)
    verify_remote_execution_stack
    run_step "RESUME_PRODUCTION_WATCHERS_DRY_RUN" env DRY_RUN=1 bash "$RESUME_WATCHERS_SCRIPT"
    run_step "RESUME_PRODUCTION_WATCHERS_RUN" env DRY_RUN=0 bash "$RESUME_WATCHERS_SCRIPT"
    echo "SUPERVISOR_STATUS=RESUME_WATCHERS_MODE_DONE"
    exit 0
    ;;
  verify-runner)
    run_step "REMOTE_100K_RUNNER_VERIFY" bash "$VERIFY_RUNNER_SCRIPT"
    run_step "REMOTE_CHECKPOINT_STACK_VERIFY" bash "$VERIFY_CHECKPOINT_STACK_SCRIPT"
    echo "SUPERVISOR_STATUS=VERIFY_RUNNER_MODE_DONE"
    exit 0
    ;;
  rate)
    run_step "PRODUCTION_RATE_AND_ETA_AUDIT" bash "$RATE_SCRIPT"
    echo "SUPERVISOR_STATUS=RATE_MODE_DONE"
    exit 0
    ;;
  evidence-index)
    run_step "BUILD_100K_CHECKPOINT_EVIDENCE_INDEX" bash "$EVIDENCE_INDEX_SCRIPT"
    echo "SUPERVISOR_STATUS=EVIDENCE_INDEX_MODE_DONE"
    exit 0
    ;;
  full)
    run_step "REMOTE_100K_RUNNER_VERIFY" bash "$VERIFY_RUNNER_SCRIPT"
    run_step "REMOTE_CHECKPOINT_STACK_VERIFY" bash "$VERIFY_CHECKPOINT_STACK_SCRIPT"
    run_step "PRODUCTION_RATE_AND_ETA_AUDIT" bash "$RATE_SCRIPT"
    run_step "ADAPTIVE_ACQUISITION_DRY_RUN" env DRY_RUN=1 RUN_EMX="$RUN_ADAPTIVE_EMX" bash "$ADAPTIVE_ACQUISITION_SCRIPT"
    run_step "ADAPTIVE_ACQUISITION_BUILD_QUEUE" env DRY_RUN=0 RUN_EMX="$RUN_ADAPTIVE_EMX" bash "$ADAPTIVE_ACQUISITION_SCRIPT"
    run_step "RESUME_PRODUCTION_WATCHERS_DRY_RUN" env DRY_RUN=1 bash "$RESUME_WATCHERS_SCRIPT"
    run_step "RESUME_PRODUCTION_WATCHERS_RUN" env DRY_RUN=0 bash "$RESUME_WATCHERS_SCRIPT"
    run_step "AUTO_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$AUTO_SCRIPT"
    run_step "AUTO_CHECKPOINT_RUN_MISSING" env DRY_RUN=0 bash "$AUTO_SCRIPT"
    run_step "CUMULATIVE_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$CUM_SCRIPT"
    run_step "CUMULATIVE_CHECKPOINT_RUN_MISSING" env DRY_RUN=0 bash "$CUM_SCRIPT"
    run_step "BUILD_100K_CHECKPOINT_EVIDENCE_INDEX" bash "$EVIDENCE_INDEX_SCRIPT"
    run_step "ONE_MILLION_FINAL_GOAL_AUDIT" bash "$AUDIT_SCRIPT"
    echo "SUPERVISOR_STATUS=FULL_MODE_DONE"
    exit 0
    ;;
  rerun-failed)
    verify_remote_execution_stack
    run_step "AUTO_CHECKPOINT_DRY_RUN" env DRY_RUN=1 bash "$AUTO_SCRIPT"
    run_step "AUTO_CHECKPOINT_RERUN_FAILED" env DRY_RUN=0 RERUN_FAILED_CHECKPOINTS=1 bash "$AUTO_SCRIPT"
    echo "SUPERVISOR_STATUS=RERUN_FAILED_MODE_DONE"
    exit 0
    ;;
esac
