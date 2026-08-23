#!/usr/bin/env bash
set -euo pipefail

# Watch for MARS SSH to become reachable, then start the detached 56-point
# grounded-tap S8P pilot and keep polling until the return package can be
# fetched and verified locally.
#
# Defaults are intentionally finite. Override ATTEMPTS/SLEEP_SECONDS if you
# want a longer or shorter watch window.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${REMOTE_HOST:-mars.example.edu}"
REMOTE_USER="${REMOTE_USER:-researcher}"
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
ATTEMPTS="${ATTEMPTS:-288}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-10}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs}"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/.mars_watch_state}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/mars_56pt_grounded_tap_watch_${STAMP}.log}"
LAUNCH_MARKER="$STATE_DIR/mars_56pt_grounded_tap_detached_started"
SUCCESS_MARKER="$STATE_DIR/mars_56pt_grounded_tap_verified"
PID_FILE="$STATE_DIR/mars_56pt_grounded_tap_watch.pid"
DAEMON_MODE="${DAEMON_MODE:-0}"

mkdir -p "$LOG_DIR" "$STATE_DIR"

log() {
  local line
  line="$(printf '[%s] %s\n' "$(date -Iseconds)" "$*")"
  if [[ "$DAEMON_MODE" == "1" ]]; then
    printf '%s\n' "$line" >> "$LOG_FILE"
  else
    printf '%s\n' "$line" | tee -a "$LOG_FILE"
  fi
}

on_exit() {
  local rc=$?
  log "WATCH_EXIT rc=$rc"
}

trap on_exit EXIT
printf '%s\n' "$$" > "$PID_FILE"

ssh_ready() {
  ssh -o BatchMode=yes \
      -o ConnectTimeout="$CONNECT_TIMEOUT" \
      -o ServerAliveInterval=5 \
      -o ServerAliveCountMax=1 \
      -o StrictHostKeyChecking=accept-new \
      "$REMOTE" "hostname >/dev/null && test -d '$REMOTE_WORK_DIR'" \
      >>"$LOG_FILE" 2>&1
}

remote_return_ready() {
  ssh -o BatchMode=yes \
      -o ConnectTimeout="$CONNECT_TIMEOUT" \
      -o StrictHostKeyChecking=accept-new \
      "$REMOTE" \
      "test -f '$REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.tar.gz' && test -f '$REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.inventory.json'" \
	      >>"$LOG_FILE" 2>&1
}

log_network_diagnosis() {
  local attempt="$1"
  if (( attempt != 1 && attempt % 12 != 0 )); then
    return 0
  fi

  log "NETWORK_DIAG_START attempt=$attempt"
  {
    echo "DNS_FOR_REMOTE_HOST:"
    if command -v dig >/dev/null 2>&1; then
      dig +short "$REMOTE_HOST" || true
    else
      host "$REMOTE_HOST" || true
    fi
    echo "ROUTE_TO_REMOTE_HOST:"
    route -n get "$REMOTE_HOST" 2>&1 || true
    echo "TEN_130_ROUTES:"
    netstat -rn -f inet 2>/dev/null | awk '$1 ~ /^10\\.130/ || $2 ~ /^10\\.130/ {print}' || true
    echo "ACTIVE_UTUN_IPV4:"
    ifconfig 2>/dev/null | awk '
      /^utun[0-9]+:/ {iface=$1; sub(":", "", iface)}
      /inet / && iface {print iface, $0}
    ' || true
  } >>"$LOG_FILE" 2>&1
  log "NETWORK_DIAG_DONE attempt=$attempt"
}

log "MARS_56PT_WATCH_START"
log "REMOTE=$REMOTE"
log "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
log "ATTEMPTS=$ATTEMPTS"
log "SLEEP_SECONDS=$SLEEP_SECONDS"
log "LOG_FILE=$LOG_FILE"
log "PID=$$"
log "DAEMON_MODE=$DAEMON_MODE"

for ((attempt=1; attempt<=ATTEMPTS; attempt++)); do
  log "ATTEMPT $attempt/$ATTEMPTS: checking SSH reachability"

	  if ! ssh_ready; then
	    log "SSH_NOT_READY"
	    log_network_diagnosis "$attempt"
	  else
    log "SSH_READY"

    if [[ ! -f "$LAUNCH_MARKER" ]]; then
      log "STARTING_DETACHED_MARS_RUN"
      if bash "$ROOT_DIR/START_MARS_56PT_GROUNDED_TAP_DETACHED_OVER_SSH_20260701.sh" >>"$LOG_FILE" 2>&1; then
        date -Iseconds > "$LAUNCH_MARKER"
        log "DETACHED_MARS_RUN_STARTED"
      else
        log "DETACHED_MARS_RUN_START_FAILED"
      fi
    else
      log "DETACHED_MARS_RUN_ALREADY_MARKED_STARTED at $(cat "$LAUNCH_MARKER")"
    fi

    log "CHECKING_REMOTE_RETURN_PACKAGE"
    if remote_return_ready; then
      log "REMOTE_RETURN_READY; FETCHING_AND_VERIFYING"
      if bash "$ROOT_DIR/FETCH_AND_VERIFY_MARS_56PT_GROUNDED_TAP_RETURN_20260701.sh" >>"$LOG_FILE" 2>&1; then
        date -Iseconds > "$SUCCESS_MARKER"
        log "MARS_56PT_RETURN_FETCH_VERIFY_SUCCESS"
        exit 0
      fi
      log "FETCH_OR_VERIFY_FAILED; WILL_RETRY"
    else
      log "REMOTE_RETURN_NOT_READY"
      bash "$ROOT_DIR/CHECK_MARS_56PT_GROUNDED_TAP_STATUS_OVER_SSH_20260701.sh" >>"$LOG_FILE" 2>&1 || true
    fi
  fi

  if (( attempt < ATTEMPTS )); then
    log "SLEEPING ${SLEEP_SECONDS}s"
    sleep "$SLEEP_SECONDS"
  fi
done

log "MARS_56PT_WATCH_EXHAUSTED_ATTEMPTS"
exit 124
