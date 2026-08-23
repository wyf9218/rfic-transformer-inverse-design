#!/usr/bin/env bash
set -euo pipefail

# Continuous post-Duo supervisor loop for the active MARS56 1M campaign.
#
# This wrapper intentionally does not reimplement any checkpoint physics logic.
# It repeatedly calls RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh so every
# 100k production chunk can be checkpoint-tested as soon as it becomes ready.
#
# Safe local smoke test, no SSH:
#   LOCAL_DRY_RUN=1 WATCH_ITERATIONS=1 SLEEP_SECONDS=0 bash RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh
#
# After Duo is available, run one full check cycle:
#   WATCH_ITERATIONS=1 bash RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh
#
# After Duo is available, keep watching until stopped:
#   WATCH_ITERATIONS=0 SLEEP_SECONDS=1800 bash RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh
#
# By default the watcher exits after the transcript summary proves
# ONE_MILLION_GOAL_STATUS=PASS. Disable with STOP_ON_GOAL_PASS=0.
#
# By default the watcher also archives its own top-level loop transcript under
# logs/mars56_post_duo_continuous_watch. Disable with WATCH_LOG_CAPTURE=0.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SUPERVISOR="$ROOT_DIR/RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh"
SUMMARY="$ROOT_DIR/SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh"

WATCH_ITERATIONS="${WATCH_ITERATIONS:-1}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1800}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
STOP_ON_ERROR="${STOP_ON_ERROR:-0}"
STOP_ON_GOAL_PASS="${STOP_ON_GOAL_PASS:-1}"
RUN_CHECKPOINT="${RUN_CHECKPOINT:-1}"
RUN_CUMULATIVE="${RUN_CUMULATIVE:-1}"
RUN_AUDIT="${RUN_AUDIT:-1}"
RUN_VERIFY_RUNNER="${RUN_VERIFY_RUNNER:-1}"
VERIFY_RUNNER_REQUIRED="${VERIFY_RUNNER_REQUIRED:-1}"
RUN_RESUME_WATCHERS="${RUN_RESUME_WATCHERS:-1}"
RUN_RATE_AUDIT="${RUN_RATE_AUDIT:-1}"
RUN_ADAPTIVE_ACQUISITION="${RUN_ADAPTIVE_ACQUISITION:-1}"
RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"
RUN_EVIDENCE_INDEX="${RUN_EVIDENCE_INDEX:-1}"
WATCH_LOG_CAPTURE="${WATCH_LOG_CAPTURE:-1}"
WATCH_LOG_DIR="${WATCH_LOG_DIR:-$ROOT_DIR/logs/mars56_post_duo_continuous_watch}"
WATCH_LOG_PATH="${WATCH_LOG_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$STOP_ON_ERROR" in 0|1) ;;
  *) echo "ERROR: STOP_ON_ERROR must be 0 or 1." >&2; exit 2 ;;
esac
case "$STOP_ON_GOAL_PASS" in 0|1) ;;
  *) echo "ERROR: STOP_ON_GOAL_PASS must be 0 or 1." >&2; exit 2 ;;
esac
case "$VERIFY_RUNNER_REQUIRED" in 0|1) ;;
  *) echo "ERROR: VERIFY_RUNNER_REQUIRED must be 0 or 1." >&2; exit 2 ;;
esac
case "$WATCH_LOG_CAPTURE" in 0|1) ;;
  *) echo "ERROR: WATCH_LOG_CAPTURE must be 0 or 1." >&2; exit 2 ;;
esac
for var_name in RUN_CHECKPOINT RUN_CUMULATIVE RUN_AUDIT RUN_VERIFY_RUNNER RUN_RESUME_WATCHERS RUN_RATE_AUDIT RUN_ADAPTIVE_ACQUISITION RUN_ADAPTIVE_EMX RUN_EVIDENCE_INDEX; do
  value="${!var_name}"
  case "$value" in 0|1) ;;
    *) echo "ERROR: $var_name must be 0 or 1." >&2; exit 2 ;;
  esac
done
if ! [[ "$WATCH_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: WATCH_ITERATIONS must be a nonnegative integer; use 0 for forever." >&2
  exit 2
fi
if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: SLEEP_SECONDS must be a nonnegative integer." >&2
  exit 2
fi
if [ ! -f "$SUPERVISOR" ] || [ ! -f "$SUMMARY" ]; then
  echo "ERROR: required supervisor or summary script is missing." >&2
  exit 2
fi
if [[ "$WATCH_LOG_DIR" == *$'\n'* || "$WATCH_LOG_DIR" == *$'\r'* || "$WATCH_LOG_PATH" == *$'\n'* || "$WATCH_LOG_PATH" == *$'\r'* || "$CONTROL_DIR" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\n'* || "$CONTROL_DIR" == *$'\r'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: watcher log path settings contain unsupported newline characters." >&2
  exit 2
fi
mkdir -p "$CONTROL_DIR"
export SSH_CONTROL_PATH SSH_PERSIST CONTROL_DIR
if [ "$WATCH_LOG_CAPTURE" = "1" ]; then
  mkdir -p "$WATCH_LOG_DIR"
  if [ -z "$WATCH_LOG_PATH" ]; then
    stamp=$(date '+%Y%m%d_%H%M%S')
    WATCH_LOG_PATH="$WATCH_LOG_DIR/mars56_post_duo_continuous_watch_${stamp}.log"
  fi
  if [ "${WATCH_LOG_REEXECED:-0}" != "1" ]; then
    export WATCH_ITERATIONS SLEEP_SECONDS LOCAL_DRY_RUN STOP_ON_ERROR STOP_ON_GOAL_PASS
    export RUN_CHECKPOINT RUN_CUMULATIVE RUN_AUDIT RUN_VERIFY_RUNNER VERIFY_RUNNER_REQUIRED RUN_RESUME_WATCHERS RUN_RATE_AUDIT RUN_ADAPTIVE_ACQUISITION RUN_ADAPTIVE_EMX RUN_EVIDENCE_INDEX
    export SSH_CONTROL_PATH SSH_PERSIST CONTROL_DIR
    export WATCH_LOG_CAPTURE WATCH_LOG_DIR WATCH_LOG_PATH WATCH_LOG_REEXECED=1
    bash "$0" "$@" 2>&1 | tee -a "$WATCH_LOG_PATH"
    exit "${PIPESTATUS[0]}"
  fi
fi

goal_completion_proven() {
  python3 - "$ROOT_DIR/reports/mars56_post_duo_supervisor_log_summary_20260707.json" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    print("False")
    raise SystemExit(0)
print("True" if data.get("goal_completion_ever_proven") is True else "False")
PY
}

run_cmd() {
  local label="$1"
  local required="${2:-0}"
  shift
  shift
  printf '\n========== WATCH_STEP %s ==========\n' "$label"
  if [ "$LOCAL_DRY_RUN" = "1" ]; then
    printf 'LOCAL_DRY_RUN command='
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  local rc=0
  if "$@"; then
    echo "WATCH_STEP_STATUS label=$label status=PASS"
    return 0
  else
    rc=$?
  fi
  echo "WATCH_STEP_STATUS label=$label status=FAIL rc=$rc"
  if [ "$required" = "1" ]; then
    echo "WATCH_STATUS=REQUIRED_STEP_FAILED label=$label rc=$rc"
    exit "$rc"
  fi
  if [ "$STOP_ON_ERROR" = "1" ]; then
    exit "$rc"
  fi
  return 0
}

run_supervisor_mode() {
  local mode="$1"
  local required="${2:-0}"
  run_cmd "supervisor_${mode}" "$required" env MODE="$mode" RUN_ADAPTIVE_EMX="$RUN_ADAPTIVE_EMX" bash "$SUPERVISOR"
}

echo "MARS56 post-Duo continuous watcher"
echo "watch_iterations=$WATCH_ITERATIONS"
echo "sleep_seconds=$SLEEP_SECONDS"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "stop_on_error=$STOP_ON_ERROR"
echo "stop_on_goal_pass=$STOP_ON_GOAL_PASS"
echo "watch_log_capture=$WATCH_LOG_CAPTURE"
if [ "$WATCH_LOG_CAPTURE" = "1" ]; then
  echo "watch_log=$WATCH_LOG_PATH"
else
  echo "watch_log=disabled"
fi
echo "run_checkpoint=$RUN_CHECKPOINT"
echo "run_cumulative=$RUN_CUMULATIVE"
echo "run_audit=$RUN_AUDIT"
echo "run_verify_runner=$RUN_VERIFY_RUNNER"
echo "verify_runner_required=$VERIFY_RUNNER_REQUIRED"
echo "run_resume_watchers=$RUN_RESUME_WATCHERS"
echo "run_rate_audit=$RUN_RATE_AUDIT"
echo "run_adaptive_acquisition=$RUN_ADAPTIVE_ACQUISITION"
echo "run_adaptive_emx=$RUN_ADAPTIVE_EMX"
echo "run_evidence_index=$RUN_EVIDENCE_INDEX"
echo "ssh_control_path=$SSH_CONTROL_PATH"
echo "ssh_persist=$SSH_PERSIST"
echo "note=WATCH_ITERATIONS=0 means run forever until interrupted."
echo "note=Each real supervisor run archives its own transcript under logs/mars56_post_duo_supervisor."

run_supervisor_mode "preflight"

iteration=0
while :; do
  iteration=$((iteration + 1))
  printf '\n========== WATCH_ITERATION %s ==========\n' "$iteration"
  date '+watch_iteration_time=%Y-%m-%d %H:%M:%S %Z'

  if [ "$RUN_VERIFY_RUNNER" = "1" ]; then
    run_supervisor_mode "verify-runner" "$VERIFY_RUNNER_REQUIRED"
  fi
  if [ "$RUN_RESUME_WATCHERS" = "1" ]; then
    run_supervisor_mode "resume-watchers"
  fi
  if [ "$RUN_RATE_AUDIT" = "1" ]; then
    run_supervisor_mode "rate"
  fi
  if [ "$RUN_ADAPTIVE_ACQUISITION" = "1" ]; then
    run_supervisor_mode "adaptive-acquisition"
  fi
  if [ "$RUN_CHECKPOINT" = "1" ]; then
    run_supervisor_mode "checkpoint"
  fi
  if [ "$RUN_CUMULATIVE" = "1" ]; then
    run_supervisor_mode "cumulative"
  fi
  if [ "$RUN_EVIDENCE_INDEX" = "1" ]; then
    run_supervisor_mode "evidence-index"
  fi
  if [ "$RUN_AUDIT" = "1" ]; then
    run_supervisor_mode "audit"
  fi
  run_cmd "summarize_supervisor_logs" 0 bash "$SUMMARY"

  if [ "$LOCAL_DRY_RUN" != "1" ] && [ "$STOP_ON_GOAL_PASS" = "1" ]; then
    proven="$(goal_completion_proven)"
    echo "WATCH_GOAL_COMPLETION_PROVEN=$proven"
    if [ "$proven" = "True" ]; then
      echo "WATCH_STATUS=ONE_MILLION_GOAL_PROVEN_STOPPING"
      exit 0
    fi
  fi

  if [ "$WATCH_ITERATIONS" != "0" ] && [ "$iteration" -ge "$WATCH_ITERATIONS" ]; then
    echo "WATCH_STATUS=REQUESTED_ITERATIONS_DONE"
    exit 0
  fi

  echo "WATCH_SLEEP_SECONDS=$SLEEP_SECONDS"
  if [ "$LOCAL_DRY_RUN" = "1" ] || [ "$SLEEP_SECONDS" = "0" ]; then
    echo "WATCH_STATUS=NO_SLEEP_CONTINUE"
  else
    sleep "$SLEEP_SECONDS"
  fi
done
