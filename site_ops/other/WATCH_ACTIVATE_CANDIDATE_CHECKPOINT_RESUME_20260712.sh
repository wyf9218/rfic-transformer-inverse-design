#!/usr/bin/env bash
# Wait for the current first-100k quick NN to exit, then invoke the guarded
# one-time activation. This watcher never kills or restarts a process.

set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
STAGE="${STAGE:-$BASE/status/candidate_checkpoint_resume_staging_20260712_1925}"
ACTIVATE="$STAGE/ACTIVATE_CANDIDATE_CHECKPOINT_RESUME_AFTER_QUICK_NN_20260712.sh"
NN_OUT="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher/first100k_accepted_model_checkpoint/nn_architecture_training"
MARKER="$STAGE/CANDIDATE_CHECKPOINT_RESUME_ACTIVATED"
LOG="$STAGE/activation_watcher.log"
PID_FILE="$STAGE/activation_watcher.pid"
POLL_SECONDS="${POLL_SECONDS:-30}"

if [[ "$(hostname -f 2>/dev/null || hostname)" != "mars.example.edu" ]]; then
  echo "ERROR: watcher must run on mars.example.edu." >&2
  exit 2
fi
[[ -f "$ACTIVATE" ]] || { echo "ERROR: activation script missing: $ACTIVATE" >&2; exit 2; }

quick_nn_active() {
  ps -u "$(id -un)" -o args= \
    | grep '[t]rain_physical_feature_inverse_nn_architecture_search.py' \
    | grep -F "$NN_OUT" >/dev/null
}

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(tr -cd '0-9' < "$PID_FILE")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "ERROR: activation watcher already active: pid=$existing_pid" >&2
    exit 2
  fi
fi

printf '%s\n' "$$" > "$PID_FILE.tmp"
mv -f "$PID_FILE.tmp" "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT

printf '%s watcher_start pid=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$$" >> "$LOG"
heartbeat=0
while [[ ! -f "$MARKER" ]]; do
  if quick_nn_active; then
    if (( heartbeat % 10 == 0 )); then
      printf '%s waiting_for_quick_nn\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
    fi
    heartbeat=$((heartbeat + 1))
    sleep "$POLL_SECONDS"
    continue
  fi

  printf '%s quick_nn_absent_attempt_activation\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
  if bash "$ACTIVATE" >> "$LOG" 2>&1; then
    printf '%s activation_pass\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
    break
  fi

  # A new retry may have started between the process check and activation.
  if quick_nn_active; then
    printf '%s activation_deferred_new_quick_nn_detected\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
    sleep "$POLL_SECONDS"
    continue
  fi

  printf '%s activation_failed_without_active_nn_manual_review_required\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
  exit 2
done

[[ -f "$MARKER" ]] || { echo "ERROR: activation marker missing after watcher completion." >&2; exit 2; }
printf '%s watcher_complete\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOG"
