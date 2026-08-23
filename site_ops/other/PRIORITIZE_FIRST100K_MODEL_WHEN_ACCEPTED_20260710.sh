#!/usr/bin/env bash
# When 100k strict real-label rows exist, temporarily pause the residual EMX
# process group so the deadline model checkpoint can use CPU, then allow the
# resume-only production supervisor to continue the remaining raw queue.

set -u

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
STATUS_ROOT="${STATUS_ROOT:-$BASE/status/first100k_urgent_targeted_20260709}"
WATCHER_DIR="${WATCHER_DIR:-$STATUS_ROOT/accepted_checkpoint_watcher}"
LATEST_STATUS="${LATEST_STATUS:-$WATCHER_DIR/latest_acceptance_status.json}"
MODEL_COMPLETE="${MODEL_COMPLETE:-$WATCHER_DIR/first100k_model_checkpoint.complete}"
PAUSE_MARKER="${PAUSE_MARKER:-$WATCHER_DIR/model_training_priority.pause_production}"
POLL_SECONDS="${POLL_SECONDS:-60}"
MODEL_TIMEOUT_SECONDS="${MODEL_TIMEOUT_SECONDS:-43200}"

OUT_DIR="$STATUS_ROOT/model_training_priority"
LOG="$OUT_DIR/model_training_priority.log"
LOCK_DIR="$OUT_DIR/active.lock"
mkdir -p "$OUT_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s model-priority watcher already active\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG"
  exit 0
fi

pause_active=0
cleanup() {
  if [[ "$pause_active" -eq 1 ]]; then
    rm -f "$PAUSE_MARKER"
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >> "$LOG"
}

accepted_target_reached() {
  python3 - "$LATEST_STATUS" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
if not bool(payload.get("accepted_target_reached")):
    raise SystemExit(1)
if int(payload.get("accepted_combined_count") or 0) < int(payload.get("target_accepted_count") or 100000):
    raise SystemExit(1)
PY
}

production_runner_pid() {
  pgrep -u "$USER" -f "run_candidate_queue_dataset_parallel.py.*residual_parallel2" 2>/dev/null | head -n 1 || true
}

production_processes_active() {
  pgrep -u "$USER" -f "run_candidate_queue_dataset_parallel.py.*residual_parallel2" >/dev/null 2>&1 \
    || pgrep -u "$USER" -f "run_candidate_queue_dataset.py.*residual_parallel2" >/dev/null 2>&1 \
    || pgrep -u "$USER" -f "emx_cae_singularity.*residual_parallel2" >/dev/null 2>&1
}

pause_production() {
  touch "$PAUSE_MARKER"
  pause_active=1
  runner_pid="$(production_runner_pid)"
  if [[ -z "$runner_pid" ]]; then
    log "production already inactive when model priority began"
    return 0
  fi
  pgid="$(ps -o pgid= -p "$runner_pid" | tr -d ' ')"
  log "stopping residual production process group pgid=$pgid for model training"
  kill -TERM -- "-$pgid" 2>/dev/null || true
  for _ in $(seq 1 120); do
    if ! production_processes_active; then
      log "residual production paused cleanly"
      return 0
    fi
    sleep 1
  done
  log "WARNING: residual production did not fully stop within 120 seconds"
}

log "model-priority watcher start"
while [[ ! -f "$MODEL_COMPLETE" ]]; do
  if accepted_target_reached; then
    log "accepted target reached; activating model-training priority"
    pause_production
    break
  fi
  sleep "$POLL_SECONDS"
done

if [[ -f "$MODEL_COMPLETE" ]]; then
  log "model checkpoint already complete; no production pause needed"
  exit 0
fi

start_epoch="$(date +%s)"
while [[ ! -f "$MODEL_COMPLETE" ]]; do
  now="$(date +%s)"
  elapsed=$((now - start_epoch))
  if [[ "$elapsed" -ge "$MODEL_TIMEOUT_SECONDS" ]]; then
    log "WARNING: model checkpoint timeout after ${elapsed}s; releasing production pause"
    break
  fi
  log "model training active elapsed_seconds=$elapsed"
  sleep 300
done

if [[ -f "$MODEL_COMPLETE" ]]; then
  log "first100k model checkpoint complete; releasing production pause"
fi
rm -f "$PAUSE_MARKER"
pause_active=0
log "production resume is now permitted"
