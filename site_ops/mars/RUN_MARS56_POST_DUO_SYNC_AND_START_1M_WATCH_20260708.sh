#!/usr/bin/env bash
set -euo pipefail

# One-command post-Duo production control for the MARS56 1M campaign.
#
# Purpose:
#   1. run local proof/readiness gates;
#   2. sync the strict local runner/checkpoint/contract stack to MARS;
#   3. verify the remote stack;
#   4. start the detached continuous watcher that resumes production, runs
#      per-100k/cumulative checkpoints, builds evidence indexes, and performs
#      the final 1M audit.
#
# Safe local dry-run, no SSH:
#   LOCAL_DRY_RUN=1 bash RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh
#
# After Duo is available:
#   bash RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"

LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
RUN_LOCAL_GATES="${RUN_LOCAL_GATES:-1}"
SYNC_REMOTE_STACK="${SYNC_REMOTE_STACK:-1}"
START_WATCHER="${START_WATCHER:-1}"
RUN_LAUNCH_AUDIT="${RUN_LAUNCH_AUDIT:-1}"
WATCH_ITERATIONS="${WATCH_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1800}"
STOP_ON_GOAL_PASS="${STOP_ON_GOAL_PASS:-1}"
VERIFY_RUNNER_REQUIRED="${VERIFY_RUNNER_REQUIRED:-1}"
RUN_ADAPTIVE_ACQUISITION="${RUN_ADAPTIVE_ACQUISITION:-1}"
RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"
ALLOW_MISMATCH="${ALLOW_MISMATCH:-0}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"
POST_DUO_LOG_CAPTURE="${POST_DUO_LOG_CAPTURE:-1}"
POST_DUO_LOG_DIR="${POST_DUO_LOG_DIR:-$ROOT_DIR/logs/mars56_post_duo_sync_start}"
POST_DUO_LOG_PATH="${POST_DUO_LOG_PATH:-}"
POST_DUO_STATUS_JSON="${POST_DUO_STATUS_JSON:-$POST_DUO_LOG_DIR/mars56_post_duo_sync_start_latest_status.json}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$ROOT_DIR/logs/mars56_post_duo_continuous_watch/mars56_post_duo_continuous_watch_latest_detached_status.json}"
LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO="${LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO:-1}"
LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN="${LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN:-0}"

READINESS="$ROOT_DIR/CHECK_LOCAL_MARS56_1M_GOAL_READINESS_20260707.sh"
FINAL_AUDIT_CONTRACT_GATE="$ROOT_DIR/CHECK_LOCAL_FINAL_1M_AUDIT_CONTRACT_GATE_20260708.sh"
SUPERVISOR_SUMMARY_GATE="$ROOT_DIR/CHECK_LOCAL_SUPERVISOR_SUMMARY_GOAL_PROOF_20260707.sh"
VERIFY_SYNC_RUNNER="$ROOT_DIR/RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"
VERIFY_SYNC_STACK="$ROOT_DIR/RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"
SUPERVISOR="$ROOT_DIR/RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh"
EVIDENCE_INDEX_SCRIPT="$ROOT_DIR/RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"
DETACHED_WATCH="$ROOT_DIR/START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh"
LAUNCH_AUDIT="$ROOT_DIR/CHECK_MARS56_POST_DUO_SYNC_START_LAUNCH_STATUS_20260708.sh"
TARGET_ENVELOPE_CONFIG_LOCAL="${TARGET_ENVELOPE_CONFIG_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json}"
ADAPTIVE_ROUND_LOCAL="${ADAPTIVE_ROUND_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh}"
SURROGATE_PREDICT_LOCAL="${SURROGATE_PREDICT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/build_physical_feature_surrogate_candidate_predictions.py}"
MODEL_CHECKPOINT_LOCAL="${MODEL_CHECKPOINT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_accepted_physical_feature_model_checkpoint.sh}"
TANDEM_TRAIN_LOCAL="${TANDEM_TRAIN_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/train_physical_feature_tandem_inverse.py}"
TANDEM_ANCHOR_COMPARE_LOCAL="${TANDEM_ANCHOR_COMPARE_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/compare_tandem_geometry_anchor_ablation.py}"
BNI_TEMPERATURE_SELECT_LOCAL="${BNI_TEMPERATURE_SELECT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/select_balanced_mse_bni_temperature.py}"
BNI_ABLATION_COMPARE_LOCAL="${BNI_ABLATION_COMPARE_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/compare_balanced_mse_bni_ablation.py}"
LEARNING_CURVE_AUDIT_LOCAL="${LEARNING_CURVE_AUDIT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_physical_feature_model_learning_curve.py}"
TANDEM_FEASIBILITY_AUDIT_LOCAL="${TANDEM_FEASIBILITY_AUDIT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_tandem_predicted_geometry_feasibility.py}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_LOCAL_GATES" in 0|1) ;;
  *) echo "ERROR: RUN_LOCAL_GATES must be 0 or 1." >&2; exit 2 ;;
esac
case "$SYNC_REMOTE_STACK" in 0|1) ;;
  *) echo "ERROR: SYNC_REMOTE_STACK must be 0 or 1." >&2; exit 2 ;;
esac
case "$START_WATCHER" in 0|1) ;;
  *) echo "ERROR: START_WATCHER must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_LAUNCH_AUDIT" in 0|1) ;;
  *) echo "ERROR: RUN_LAUNCH_AUDIT must be 0 or 1." >&2; exit 2 ;;
esac
case "$STOP_ON_GOAL_PASS" in 0|1) ;;
  *) echo "ERROR: STOP_ON_GOAL_PASS must be 0 or 1." >&2; exit 2 ;;
esac
case "$VERIFY_RUNNER_REQUIRED" in 0|1) ;;
  *) echo "ERROR: VERIFY_RUNNER_REQUIRED must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_ADAPTIVE_ACQUISITION" in 0|1) ;;
  *) echo "ERROR: RUN_ADAPTIVE_ACQUISITION must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_ADAPTIVE_EMX" in 0|1) ;;
  *) echo "ERROR: RUN_ADAPTIVE_EMX must be 0 or 1." >&2; exit 2 ;;
esac
case "$ALLOW_MISMATCH" in 0|1) ;;
  *) echo "ERROR: ALLOW_MISMATCH must be 0 or 1." >&2; exit 2 ;;
esac
case "$POST_DUO_LOG_CAPTURE" in 0|1) ;;
  *) echo "ERROR: POST_DUO_LOG_CAPTURE must be 0 or 1." >&2; exit 2 ;;
esac
case "$LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO" in 0|1) ;;
  *) echo "ERROR: LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO must be 0 or 1." >&2; exit 2 ;;
esac
case "$LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
if ! [[ "$WATCH_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: WATCH_ITERATIONS must be a nonnegative integer; use 0 for forever." >&2
  exit 2
fi
if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: SLEEP_SECONDS must be a nonnegative integer." >&2
  exit 2
fi
for value in "$CONTROL_DIR" "$SSH_CONTROL_PATH" "$POST_DUO_LOG_DIR" "$POST_DUO_LOG_PATH" "$POST_DUO_STATUS_JSON" "$DETACHED_STATUS_JSON" "$TARGET_ENVELOPE_CONFIG_LOCAL" "$ADAPTIVE_ROUND_LOCAL" "$SURROGATE_PREDICT_LOCAL" "$MODEL_CHECKPOINT_LOCAL" "$TANDEM_TRAIN_LOCAL" "$TANDEM_ANCHOR_COMPARE_LOCAL" "$BNI_TEMPERATURE_SELECT_LOCAL" "$BNI_ABLATION_COMPARE_LOCAL" "$LEARNING_CURVE_AUDIT_LOCAL" "$TANDEM_FEASIBILITY_AUDIT_LOCAL"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path settings contain unsupported newline characters." >&2
    exit 2
  fi
done
if [ ! -f "$TARGET_ENVELOPE_CONFIG_LOCAL" ]; then
  echo "ERROR: target envelope config missing: $TARGET_ENVELOPE_CONFIG_LOCAL" >&2
  exit 2
fi
if [ ! -f "$ADAPTIVE_ROUND_LOCAL" ] || [ ! -f "$SURROGATE_PREDICT_LOCAL" ] || [ ! -f "$MODEL_CHECKPOINT_LOCAL" ] || [ ! -f "$TANDEM_TRAIN_LOCAL" ] || [ ! -f "$TANDEM_ANCHOR_COMPARE_LOCAL" ] || [ ! -f "$BNI_TEMPERATURE_SELECT_LOCAL" ] || [ ! -f "$BNI_ABLATION_COMPARE_LOCAL" ] || [ ! -f "$LEARNING_CURVE_AUDIT_LOCAL" ] || [ ! -f "$TANDEM_FEASIBILITY_AUDIT_LOCAL" ]; then
  echo "ERROR: acquisition/model implementation file missing." >&2
  echo "adaptive_round_local=$ADAPTIVE_ROUND_LOCAL" >&2
  echo "surrogate_predict_local=$SURROGATE_PREDICT_LOCAL" >&2
  echo "model_checkpoint_local=$MODEL_CHECKPOINT_LOCAL" >&2
  echo "tandem_train_local=$TANDEM_TRAIN_LOCAL" >&2
  echo "tandem_anchor_compare_local=$TANDEM_ANCHOR_COMPARE_LOCAL" >&2
  echo "bni_temperature_select_local=$BNI_TEMPERATURE_SELECT_LOCAL" >&2
  echo "bni_ablation_compare_local=$BNI_ABLATION_COMPARE_LOCAL" >&2
  echo "learning_curve_audit_local=$LEARNING_CURVE_AUDIT_LOCAL" >&2
  echo "tandem_feasibility_audit_local=$TANDEM_FEASIBILITY_AUDIT_LOCAL" >&2
  exit 2
fi
if [ ! -f "$EVIDENCE_INDEX_SCRIPT" ]; then
  echo "ERROR: evidence index script missing: $EVIDENCE_INDEX_SCRIPT" >&2
  exit 2
fi
if [ ! -f "$VERIFY_SYNC_RUNNER" ] || [ ! -f "$VERIFY_SYNC_STACK" ]; then
  echo "ERROR: remote sync verifier script missing." >&2
  echo "verify_sync_runner=$VERIFY_SYNC_RUNNER" >&2
  echo "verify_sync_stack=$VERIFY_SYNC_STACK" >&2
  exit 2
fi

mkdir -p "$CONTROL_DIR"
export SSH_CONTROL_PATH SSH_PERSIST
TARGET_ENVELOPE_SHA256="$(shasum -a 256 "$TARGET_ENVELOPE_CONFIG_LOCAL" | awk '{print $1}')"
EVIDENCE_INDEX_SCRIPT_SHA256="$(shasum -a 256 "$EVIDENCE_INDEX_SCRIPT" | awk '{print $1}')"
VERIFY_SYNC_RUNNER_SHA256="$(shasum -a 256 "$VERIFY_SYNC_RUNNER" | awk '{print $1}')"
VERIFY_SYNC_STACK_SHA256="$(shasum -a 256 "$VERIFY_SYNC_STACK" | awk '{print $1}')"
ADAPTIVE_ROUND_SHA256="$(shasum -a 256 "$ADAPTIVE_ROUND_LOCAL" | awk '{print $1}')"
SURROGATE_PREDICT_SHA256="$(shasum -a 256 "$SURROGATE_PREDICT_LOCAL" | awk '{print $1}')"
MODEL_CHECKPOINT_SHA256="$(shasum -a 256 "$MODEL_CHECKPOINT_LOCAL" | awk '{print $1}')"
TANDEM_TRAIN_SHA256="$(shasum -a 256 "$TANDEM_TRAIN_LOCAL" | awk '{print $1}')"
TANDEM_ANCHOR_COMPARE_SHA256="$(shasum -a 256 "$TANDEM_ANCHOR_COMPARE_LOCAL" | awk '{print $1}')"
BNI_TEMPERATURE_SELECT_SHA256="$(shasum -a 256 "$BNI_TEMPERATURE_SELECT_LOCAL" | awk '{print $1}')"
BNI_ABLATION_COMPARE_SHA256="$(shasum -a 256 "$BNI_ABLATION_COMPARE_LOCAL" | awk '{print $1}')"
LEARNING_CURVE_AUDIT_SHA256="$(shasum -a 256 "$LEARNING_CURVE_AUDIT_LOCAL" | awk '{print $1}')"
TANDEM_FEASIBILITY_AUDIT_SHA256="$(shasum -a 256 "$TANDEM_FEASIBILITY_AUDIT_LOCAL" | awk '{print $1}')"

write_post_duo_status_json() {
  local rc="$1"
  local log_path="$2"
  local state
  if [ "$rc" -eq 0 ] && [ "$LOCAL_DRY_RUN" = "1" ]; then
    state=DRY_RUN_PASS
  elif [ "$rc" -eq 0 ]; then
    state=PASS
  else
    state=FAIL
  fi
  python3 - "$POST_DUO_STATUS_JSON" "$state" "$rc" "$log_path" "$LOCAL_DRY_RUN" "$SSH_CONTROL_PATH" "$SSH_PERSIST" "$SYNC_REMOTE_STACK" "$START_WATCHER" "$WATCH_ITERATIONS" "$SLEEP_SECONDS" "$DETACHED_STATUS_JSON" "$RUN_ADAPTIVE_ACQUISITION" "$RUN_ADAPTIVE_EMX" "$TARGET_ENVELOPE_CONFIG_LOCAL" "$TARGET_ENVELOPE_SHA256" "$EVIDENCE_INDEX_SCRIPT" "$EVIDENCE_INDEX_SCRIPT_SHA256" "$VERIFY_SYNC_RUNNER" "$VERIFY_SYNC_RUNNER_SHA256" "$VERIFY_SYNC_STACK" "$VERIFY_SYNC_STACK_SHA256" "$ADAPTIVE_ROUND_LOCAL" "$ADAPTIVE_ROUND_SHA256" "$SURROGATE_PREDICT_LOCAL" "$SURROGATE_PREDICT_SHA256" "$MODEL_CHECKPOINT_LOCAL" "$MODEL_CHECKPOINT_SHA256" "$TANDEM_TRAIN_LOCAL" "$TANDEM_TRAIN_SHA256" "$TANDEM_ANCHOR_COMPARE_LOCAL" "$TANDEM_ANCHOR_COMPARE_SHA256" "$BNI_TEMPERATURE_SELECT_LOCAL" "$BNI_TEMPERATURE_SELECT_SHA256" "$BNI_ABLATION_COMPARE_LOCAL" "$BNI_ABLATION_COMPARE_SHA256" "$LEARNING_CURVE_AUDIT_LOCAL" "$LEARNING_CURVE_AUDIT_SHA256" "$TANDEM_FEASIBILITY_AUDIT_LOCAL" "$TANDEM_FEASIBILITY_AUDIT_SHA256" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    state,
    rc,
    log_path,
    local_dry_run,
    ssh_control_path,
    ssh_persist,
    sync_remote_stack,
    start_watcher,
    watch_iterations,
    sleep_seconds,
    detached_status_json,
    run_adaptive_acquisition,
    run_adaptive_emx,
    target_envelope_config,
    target_envelope_sha256,
    evidence_index_script,
    evidence_index_script_sha256,
    verify_sync_runner,
    verify_sync_runner_sha256,
    verify_sync_stack,
    verify_sync_stack_sha256,
    adaptive_round_local,
    adaptive_round_sha256,
    surrogate_predict_local,
    surrogate_predict_sha256,
    model_checkpoint_local,
    model_checkpoint_sha256,
    tandem_train_local,
    tandem_train_sha256,
    tandem_anchor_compare_local,
    tandem_anchor_compare_sha256,
    bni_temperature_select_local,
    bni_temperature_select_sha256,
    bni_ablation_compare_local,
    bni_ablation_compare_sha256,
    learning_curve_audit_local,
    learning_curve_audit_sha256,
    tandem_feasibility_audit_local,
    tandem_feasibility_audit_sha256,
) = sys.argv[1:]
path_obj = Path(path)
path_obj.parent.mkdir(parents=True, exist_ok=True)

data = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "return_code": int(rc),
    "log": log_path,
    "local_dry_run": local_dry_run == "1",
    "remote_actions_executed": local_dry_run != "1",
    "ssh_control_path": ssh_control_path,
    "ssh_persist": ssh_persist,
    "sync_remote_stack": sync_remote_stack == "1",
    "start_watcher": start_watcher == "1",
    "watch_iterations": int(watch_iterations),
    "sleep_seconds": int(sleep_seconds),
    "detached_status_json": detached_status_json,
    "run_adaptive_acquisition": run_adaptive_acquisition == "1",
    "run_adaptive_emx": run_adaptive_emx == "1",
    "run_evidence_index": True,
    "target_envelope_config": target_envelope_config,
    "target_envelope_sha256": target_envelope_sha256,
    "evidence_index_script": evidence_index_script,
    "evidence_index_script_sha256": evidence_index_script_sha256,
    "verify_sync_runner_script": verify_sync_runner,
    "verify_sync_runner_script_sha256": verify_sync_runner_sha256,
    "verify_sync_stack_script": verify_sync_stack,
    "verify_sync_stack_script_sha256": verify_sync_stack_sha256,
    "adaptive_round_script": adaptive_round_local,
    "adaptive_round_sha256": adaptive_round_sha256,
    "surrogate_predict_script": surrogate_predict_local,
    "surrogate_predict_sha256": surrogate_predict_sha256,
    "model_checkpoint_script": model_checkpoint_local,
    "model_checkpoint_sha256": model_checkpoint_sha256,
    "tandem_train_script": tandem_train_local,
    "tandem_train_sha256": tandem_train_sha256,
    "tandem_anchor_compare_script": tandem_anchor_compare_local,
    "tandem_anchor_compare_sha256": tandem_anchor_compare_sha256,
    "bni_temperature_select_script": bni_temperature_select_local,
    "bni_temperature_select_sha256": bni_temperature_select_sha256,
    "bni_ablation_compare_script": bni_ablation_compare_local,
    "bni_ablation_compare_sha256": bni_ablation_compare_sha256,
    "learning_curve_audit_script": learning_curve_audit_local,
    "learning_curve_audit_sha256": learning_curve_audit_sha256,
    "tandem_feasibility_audit_script": tandem_feasibility_audit_local,
    "tandem_feasibility_audit_sha256": tandem_feasibility_audit_sha256,
}
with path_obj.open("w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

if [ "$POST_DUO_LOG_CAPTURE" = "1" ]; then
  mkdir -p "$POST_DUO_LOG_DIR"
  if [ -z "$POST_DUO_LOG_PATH" ]; then
    stamp="$(date '+%Y%m%d_%H%M%S')"
    POST_DUO_LOG_PATH="$POST_DUO_LOG_DIR/mars56_post_duo_sync_start_${stamp}.log"
  fi
  if [ "${POST_DUO_LOG_REEXECED:-0}" != "1" ]; then
    export LOCAL_DRY_RUN RUN_LOCAL_GATES SYNC_REMOTE_STACK START_WATCHER
    export RUN_LAUNCH_AUDIT WATCH_ITERATIONS SLEEP_SECONDS STOP_ON_GOAL_PASS VERIFY_RUNNER_REQUIRED RUN_ADAPTIVE_ACQUISITION RUN_ADAPTIVE_EMX ALLOW_MISMATCH
    export SSH_PERSIST CONTROL_DIR SSH_CONTROL_PATH
    export TARGET_ENVELOPE_CONFIG_LOCAL BNI_TEMPERATURE_SELECT_LOCAL BNI_ABLATION_COMPARE_LOCAL LEARNING_CURVE_AUDIT_LOCAL
    export DETACHED_STATUS_JSON
    export LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN
    export POST_DUO_LOG_CAPTURE POST_DUO_LOG_DIR POST_DUO_LOG_PATH POST_DUO_STATUS_JSON POST_DUO_LOG_REEXECED=1
    set +e
    bash "$0" "$@" 2>&1 | tee -a "$POST_DUO_LOG_PATH"
    rc="${PIPESTATUS[0]}"
    set -e
    write_post_duo_status_json "$rc" "$POST_DUO_LOG_PATH"
    echo "POST_DUO_SYNC_AND_START_STATUS_JSON=$POST_DUO_STATUS_JSON"
    if [ "$rc" -eq 0 ] && [ "$RUN_LAUNCH_AUDIT" = "1" ] && [ "$START_WATCHER" = "1" ]; then
      echo "POST_DUO_LAUNCH_AUDIT_AUTO=START"
      set +e
      WRAPPER_STATUS_JSON="$POST_DUO_STATUS_JSON" \
      DETACHED_STATUS_JSON="$DETACHED_STATUS_JSON" \
      REQUIRE_SYNC_REMOTE_STACK="$SYNC_REMOTE_STACK" \
      REQUIRE_START_WATCHER="$START_WATCHER" \
      REQUIRE_WATCH_ITERATIONS_ZERO="$LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO" \
      ALLOW_LOCAL_DRY_RUN="$LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN" \
      bash "$LAUNCH_AUDIT" 2>&1 | tee -a "$POST_DUO_LOG_PATH"
      audit_rc="${PIPESTATUS[0]}"
      set -e
      echo "POST_DUO_LAUNCH_AUDIT_AUTO_RC=$audit_rc"
      if [ "$audit_rc" -ne 0 ]; then
        exit "$audit_rc"
      fi
    elif [ "$RUN_LAUNCH_AUDIT" = "0" ]; then
      echo "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_RUN_LAUNCH_AUDIT_0"
    elif [ "$START_WATCHER" = "0" ]; then
      echo "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_START_WATCHER_0"
    fi
    exit "$rc"
  fi
fi

for script in \
  "$READINESS" \
  "$FINAL_AUDIT_CONTRACT_GATE" \
  "$SUPERVISOR_SUMMARY_GATE" \
  "$VERIFY_SYNC_RUNNER" \
  "$VERIFY_SYNC_STACK" \
  "$SUPERVISOR" \
  "$DETACHED_WATCH" \
  "$LAUNCH_AUDIT"
do
  if [ ! -f "$script" ]; then
    echo "ERROR: required script missing: $script" >&2
    exit 2
  fi
done

run_step() {
  local label="$1"
  local remote_marker=0
  shift
  printf '\n========== %s ==========\n' "$label"
  if [ "${1:-}" = "__REMOTE__" ]; then
    remote_marker=1
    shift
  fi
  if [ "$LOCAL_DRY_RUN" = "1" ] && [ "$remote_marker" = "1" ]; then
    printf 'LOCAL_DRY_RUN remote_command='
    printf '%s ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

run_clean_local_gate() {
  run_step "$1" env \
    -u LOCAL_DRY_RUN \
    -u RUN_LOCAL_GATES \
    -u SYNC_REMOTE_STACK \
    -u START_WATCHER \
    -u RUN_LAUNCH_AUDIT \
    -u WATCH_ITERATIONS \
    -u SLEEP_SECONDS \
    -u STOP_ON_GOAL_PASS \
    -u VERIFY_RUNNER_REQUIRED \
    -u RUN_ADAPTIVE_ACQUISITION \
    -u RUN_ADAPTIVE_EMX \
    -u ALLOW_MISMATCH \
    -u POST_DUO_LOG_CAPTURE \
    -u POST_DUO_LOG_DIR \
    -u POST_DUO_LOG_PATH \
    -u POST_DUO_STATUS_JSON \
    -u DETACHED_STATUS_JSON \
    -u LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO \
    -u LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN \
    bash "$2"
}

echo "MARS56 post-Duo sync/start control"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "run_local_gates=$RUN_LOCAL_GATES"
echo "sync_remote_stack=$SYNC_REMOTE_STACK"
echo "start_watcher=$START_WATCHER"
echo "run_launch_audit=$RUN_LAUNCH_AUDIT"
echo "watch_iterations=$WATCH_ITERATIONS"
echo "sleep_seconds=$SLEEP_SECONDS"
echo "stop_on_goal_pass=$STOP_ON_GOAL_PASS"
echo "verify_runner_required=$VERIFY_RUNNER_REQUIRED"
echo "run_adaptive_acquisition=$RUN_ADAPTIVE_ACQUISITION"
echo "run_adaptive_emx=$RUN_ADAPTIVE_EMX"
echo "run_evidence_index=1"
echo "target_envelope_config_local=$TARGET_ENVELOPE_CONFIG_LOCAL"
echo "target_envelope_sha256=$TARGET_ENVELOPE_SHA256"
echo "evidence_index_script=$EVIDENCE_INDEX_SCRIPT"
echo "evidence_index_script_sha256=$EVIDENCE_INDEX_SCRIPT_SHA256"
echo "verify_sync_runner_script=$VERIFY_SYNC_RUNNER"
echo "verify_sync_runner_script_sha256=$VERIFY_SYNC_RUNNER_SHA256"
echo "verify_sync_stack_script=$VERIFY_SYNC_STACK"
echo "verify_sync_stack_script_sha256=$VERIFY_SYNC_STACK_SHA256"
echo "adaptive_round_script=$ADAPTIVE_ROUND_LOCAL"
echo "adaptive_round_sha256=$ADAPTIVE_ROUND_SHA256"
echo "surrogate_predict_script=$SURROGATE_PREDICT_LOCAL"
echo "surrogate_predict_sha256=$SURROGATE_PREDICT_SHA256"
echo "model_checkpoint_script=$MODEL_CHECKPOINT_LOCAL"
echo "model_checkpoint_sha256=$MODEL_CHECKPOINT_SHA256"
echo "tandem_train_script=$TANDEM_TRAIN_LOCAL"
echo "tandem_train_sha256=$TANDEM_TRAIN_SHA256"
echo "tandem_anchor_compare_script=$TANDEM_ANCHOR_COMPARE_LOCAL"
echo "tandem_anchor_compare_sha256=$TANDEM_ANCHOR_COMPARE_SHA256"
echo "bni_temperature_select_script=$BNI_TEMPERATURE_SELECT_LOCAL"
echo "bni_temperature_select_sha256=$BNI_TEMPERATURE_SELECT_SHA256"
echo "bni_ablation_compare_script=$BNI_ABLATION_COMPARE_LOCAL"
echo "bni_ablation_compare_sha256=$BNI_ABLATION_COMPARE_SHA256"
echo "learning_curve_audit_script=$LEARNING_CURVE_AUDIT_LOCAL"
echo "learning_curve_audit_sha256=$LEARNING_CURVE_AUDIT_SHA256"
echo "tandem_feasibility_audit_script=$TANDEM_FEASIBILITY_AUDIT_LOCAL"
echo "tandem_feasibility_audit_sha256=$TANDEM_FEASIBILITY_AUDIT_SHA256"
echo "allow_mismatch=$ALLOW_MISMATCH"
echo "launch_audit_require_watch_iterations_zero=$LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO"
echo "launch_audit_allow_local_dry_run=$LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN"
echo "ssh_control_path=$SSH_CONTROL_PATH"
echo "ssh_persist=$SSH_PERSIST"
if [ "$POST_DUO_LOG_CAPTURE" = "1" ]; then
  echo "post_duo_log=$POST_DUO_LOG_PATH"
  echo "post_duo_status_json=$POST_DUO_STATUS_JSON"
  echo "detached_status_json=$DETACHED_STATUS_JSON"
else
  echo "post_duo_log=disabled"
  echo "post_duo_status_json=disabled"
  echo "detached_status_json=$DETACHED_STATUS_JSON"
fi
echo "note=LOCAL_DRY_RUN=1 prints remote actions without SSH; real run may require password/Duo."

if [ "$RUN_LOCAL_GATES" = "1" ]; then
  run_clean_local_gate "LOCAL_1M_GOAL_READINESS" "$READINESS"
  run_clean_local_gate "LOCAL_FINAL_AUDIT_CONTRACT_GATE" "$FINAL_AUDIT_CONTRACT_GATE"
  run_clean_local_gate "LOCAL_SUPERVISOR_SUMMARY_GOAL_PROOF_GATE" "$SUPERVISOR_SUMMARY_GATE"
else
  echo "RUN_LOCAL_GATES=0: skipping recursive local readiness gates for behavior/static self-test."
fi

if [ "$SYNC_REMOTE_STACK" = "1" ]; then
  run_step "REMOTE_SYNC_100K_RUNNER" "__REMOTE__" env \
    SYNC_REMOTE_RUNNER=1 \
    ALLOW_MISMATCH="$ALLOW_MISMATCH" \
    SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
    SSH_PERSIST="$SSH_PERSIST" \
    bash "$VERIFY_SYNC_RUNNER"
  run_step "REMOTE_SYNC_CHECKPOINT_STACK" "__REMOTE__" env \
    SYNC_REMOTE_CHECKPOINT_STACK=1 \
    ALLOW_MISMATCH="$ALLOW_MISMATCH" \
    SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
    SSH_PERSIST="$SSH_PERSIST" \
    bash "$VERIFY_SYNC_STACK"
else
  run_step "REMOTE_VERIFY_100K_RUNNER" "__REMOTE__" env \
    ALLOW_MISMATCH="$ALLOW_MISMATCH" \
    SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
    SSH_PERSIST="$SSH_PERSIST" \
    bash "$VERIFY_SYNC_RUNNER"
  run_step "REMOTE_VERIFY_CHECKPOINT_STACK" "__REMOTE__" env \
    ALLOW_MISMATCH="$ALLOW_MISMATCH" \
    SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
    SSH_PERSIST="$SSH_PERSIST" \
    bash "$VERIFY_SYNC_STACK"
fi

run_step "SUPERVISOR_VERIFY_RUNNER" "__REMOTE__" env \
  MODE=verify-runner \
  SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
  SSH_PERSIST="$SSH_PERSIST" \
  bash "$SUPERVISOR"

if [ "$START_WATCHER" = "1" ]; then
  run_step "START_DETACHED_CONTINUOUS_WATCHER" "__REMOTE__" env \
    ACTION=start \
    WATCH_ITERATIONS="$WATCH_ITERATIONS" \
    SLEEP_SECONDS="$SLEEP_SECONDS" \
    STOP_ON_GOAL_PASS="$STOP_ON_GOAL_PASS" \
    VERIFY_RUNNER_REQUIRED="$VERIFY_RUNNER_REQUIRED" \
    RUN_VERIFY_RUNNER=1 \
    RUN_RESUME_WATCHERS=1 \
    RUN_RATE_AUDIT=1 \
    RUN_ADAPTIVE_ACQUISITION="$RUN_ADAPTIVE_ACQUISITION" \
    RUN_ADAPTIVE_EMX="$RUN_ADAPTIVE_EMX" \
    RUN_CHECKPOINT=1 \
    RUN_CUMULATIVE=1 \
    RUN_EVIDENCE_INDEX=1 \
    RUN_AUDIT=1 \
    SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
    SSH_PERSIST="$SSH_PERSIST" \
    DETACHED_STATUS_JSON="$DETACHED_STATUS_JSON" \
    bash "$DETACHED_WATCH"
else
  echo "START_WATCHER=0: remote stack synced/verified but detached watcher was not started."
fi

if [ "$POST_DUO_LOG_CAPTURE" = "0" ]; then
  write_post_duo_status_json 0 "disabled"
  echo "POST_DUO_SYNC_AND_START_STATUS_JSON=$POST_DUO_STATUS_JSON"
  if [ "$RUN_LAUNCH_AUDIT" = "1" ] && [ "$START_WATCHER" = "1" ]; then
    echo "POST_DUO_LAUNCH_AUDIT_AUTO=START"
    set +e
    WRAPPER_STATUS_JSON="$POST_DUO_STATUS_JSON" \
    DETACHED_STATUS_JSON="$DETACHED_STATUS_JSON" \
    REQUIRE_SYNC_REMOTE_STACK="$SYNC_REMOTE_STACK" \
    REQUIRE_START_WATCHER="$START_WATCHER" \
    REQUIRE_WATCH_ITERATIONS_ZERO="$LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO" \
    ALLOW_LOCAL_DRY_RUN="$LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN" \
    bash "$LAUNCH_AUDIT"
    audit_rc=$?
    set -e
    echo "POST_DUO_LAUNCH_AUDIT_AUTO_RC=$audit_rc"
    if [ "$audit_rc" -ne 0 ]; then
      exit "$audit_rc"
    fi
  elif [ "$RUN_LAUNCH_AUDIT" = "0" ]; then
    echo "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_RUN_LAUNCH_AUDIT_0"
  elif [ "$START_WATCHER" = "0" ]; then
    echo "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_START_WATCHER_0"
  fi
fi

echo "POST_DUO_SYNC_AND_START_STATUS=PASS"
