#!/usr/bin/env bash
set -euo pipefail

# Current-goal watcher for MARS56 grounded S4P, not the old S8P flow.
#
# It never prompts for a password or Duo. It only uses BatchMode SSH checks.
# If a working non-interactive SSH path appears, it uploads the verified S4P
# universal runner and starts the real 20-sample EMX run detached on MARS.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
REMOTE="${REMOTE:-researcher@mars.example.edu}"
REMOTE_READY_DIR="${REMOTE_READY_DIR:-/shared/research/researcher}"
ATTEMPTS="${ATTEMPTS:-288}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-8}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
STATE_DIR="${STATE_DIR:-$ROOT/.mars_s4p_watch_state}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/mars56_s4p_ssh_watch_${STAMP}.log}"
LAUNCH_MARKER="$STATE_DIR/mars56_s4p_remote_started"
PID_FILE="$STATE_DIR/mars56_s4p_ssh_watch.pid"

mkdir -p "$LOG_DIR" "$STATE_DIR"
printf '%s\n' "$$" > "$PID_FILE"

log() {
  printf '[%s] %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG_FILE"
}

ssh_ready() {
  local args=(-o BatchMode=yes -o ConnectTimeout="$CONNECT_TIMEOUT" -o ConnectionAttempts=1 -o StrictHostKeyChecking=accept-new)
  if [[ -n "${SSH_PROXY_JUMP:-}" ]]; then
    args+=(-J "$SSH_PROXY_JUMP")
  fi
  ssh "${args[@]}" "$REMOTE" "hostname >/dev/null && test -d '$REMOTE_READY_DIR'" >>"$LOG_FILE" 2>&1
}

trap 'rc=$?; log "WATCH_EXIT rc=$rc"; exit $rc' EXIT

log "MARS56_S4P_WATCH_START"
log "REMOTE=$REMOTE"
log "REMOTE_READY_DIR=$REMOTE_READY_DIR"
log "ATTEMPTS=$ATTEMPTS SLEEP_SECONDS=$SLEEP_SECONDS CONNECT_TIMEOUT=$CONNECT_TIMEOUT"
log "LOG_FILE=$LOG_FILE"
log "PID=$$"
log "This watcher uses SSH BatchMode only; it will not trigger Duo prompts."

for ((attempt=1; attempt<=ATTEMPTS; attempt++)); do
  log "ATTEMPT $attempt/$ATTEMPTS: checking non-interactive SSH readiness"
  if ssh_ready; then
    log "SSH_READY_NONINTERACTIVE"
    if [[ -f "$LAUNCH_MARKER" ]]; then
      log "S4P_RUN_ALREADY_MARKED_STARTED at $(cat "$LAUNCH_MARKER")"
      exit 0
    fi
    log "STARTING_CURRENT_S4P_REMOTE_RUN"
    if SSH_BATCH_MODE=1 SSH_CONNECT_TIMEOUT="$CONNECT_TIMEOUT" "$ROOT/RUN_MARS56_S4P_REMOTE_VIA_SSH_20260702.sh" >>"$LOG_FILE" 2>&1; then
      date -Iseconds > "$LAUNCH_MARKER"
      log "CURRENT_S4P_REMOTE_RUN_STARTED"
      exit 0
    fi
    log "CURRENT_S4P_REMOTE_RUN_START_FAILED; WILL_RETRY"
  else
    log "SSH_NOT_READY_NONINTERACTIVE"
  fi

  if (( attempt < ATTEMPTS )); then
    log "SLEEPING ${SLEEP_SECONDS}s"
    sleep "$SLEEP_SECONDS"
  fi
done

log "MARS56_S4P_WATCH_EXHAUSTED_ATTEMPTS"
exit 124
