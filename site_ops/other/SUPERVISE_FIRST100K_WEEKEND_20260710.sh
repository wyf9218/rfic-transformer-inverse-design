#!/usr/bin/env bash
# Keep the urgent first-100k EMX run and its accepted-data training watcher alive.
# Relaunches are resume-only and are blocked unless the recorded migration and
# parallel-2 solver contracts are still valid.

set -u

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
CONFIG="${CONFIG:-$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
PRODUCTION_DIR="${PRODUCTION_DIR:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
STATUS_ROOT="${STATUS_ROOT:-$BASE/status/first100k_urgent_targeted_20260709}"
QUEUE="${QUEUE:-$STATUS_ROOT/parallel2_residual_migration/scaling_benchmark/residual_after_n96.csv}"
RESIDUAL_OUT="${RESIDUAL_OUT:-$PRODUCTION_DIR/dataset/residual_parallel2}"
MIGRATION_SUMMARY="${MIGRATION_SUMMARY:-$STATUS_ROOT/parallel2_residual_migration/migration_summary.json}"
ACCEPTANCE_WATCHER="${ACCEPTANCE_WATCHER:-$PROJECT/scripts/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh}"
MODEL_PRIORITY_PAUSE="${MODEL_PRIORITY_PAUSE:-$STATUS_ROOT/accepted_checkpoint_watcher/model_training_priority.pause_production}"
POLL_SECONDS="${POLL_SECONDS:-300}"

SUPERVISOR_DIR="$STATUS_ROOT/weekend_supervisor"
LOG="$SUPERVISOR_DIR/supervisor.log"
LOCK_DIR="$SUPERVISOR_DIR/active.lock"
mkdir -p "$SUPERVISOR_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s supervisor already active: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$LOCK_DIR" >> "$LOG"
  exit 0
fi
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >> "$LOG"
}

contract_ready() {
  "$PY" - "$MIGRATION_SUMMARY" "$CONFIG" "$QUEUE" <<'PY'
import csv
import json
import pathlib
import sys

summary_path, config_path, queue_path = map(pathlib.Path, sys.argv[1:])
if not summary_path.is_file() or not config_path.is_file() or not queue_path.is_file():
    raise SystemExit(1)
summary = json.loads(summary_path.read_text(encoding="utf-8"))
if summary.get("overall_status") != "PASS":
    raise SystemExit(2)
config_text = config_path.read_text(encoding="utf-8")
if "--parallel=2" not in config_text or "--simultaneous-frequencies=0" not in config_text:
    raise SystemExit(3)
with queue_path.open(newline="", encoding="utf-8-sig") as handle:
    row_count = sum(1 for _ in csv.DictReader(handle))
if row_count != 99251:
    raise SystemExit(4)
PY
}

raw_count() {
  find "$PRODUCTION_DIR/dataset" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' '
}

production_process_count() {
  local top_count shard_count
  top_count="$(pgrep -u "$USER" -fc "run_candidate_queue_dataset_parallel.py.*residual_after_n96.csv.*residual_parallel2" 2>/dev/null || true)"
  shard_count="$(pgrep -u "$USER" -fc "run_candidate_queue_dataset.py.*residual_parallel2" 2>/dev/null || true)"
  printf '%s' "$((top_count + shard_count))"
}

start_production() {
  log "relaunching residual parallel-2 production in resume mode"
  setsid -f -w env RFIC_SKIP_LAYOUT_PREVIEWS=1 RFIC_SKIP_LUMPED_COMPARE=1 \
    "$PY" "$PROJECT/scripts/run_candidate_queue_dataset_parallel.py" \
    --candidate-csv "$QUEUE" \
    --config "$CONFIG" \
    --out-dir "$RESIDUAL_OUT" \
    --jobs 48 \
    --expected-jobs 48 \
    --chunk-size 64 \
    --max-count 99251 \
    --expected-count 99251 \
    --batch-size 1 \
    --force-wideband-5-60-0p5 \
    --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 \
    --expected-touchstone-extension .s4p \
    --expected-ports 4 \
    --max-touchstone-checks 99251 \
    --expected-port-mode single_ended_shield_grounded \
    --expected-pin-purpose 51 \
    --no-fail-exit \
    --resume-completed >> "$SUPERVISOR_DIR/restarted_production.log" 2>&1 &
}

acceptance_watcher_count() {
  pgrep -u "$USER" -fc "WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" 2>/dev/null || true
}

start_acceptance_watcher() {
  log "relaunching accepted-data audit and training watcher"
  setsid -f -w bash "$ACCEPTANCE_WATCHER" >> "$SUPERVISOR_DIR/restarted_acceptance_watcher.log" 2>&1 &
}

log "supervisor start"
while :; do
  count="$(raw_count)"
  production_count="$(production_process_count)"
  watcher_count="$(acceptance_watcher_count)"
  log "heartbeat raw_s4p=$count production_processes=$production_count acceptance_watchers=$watcher_count"

  if [[ "$watcher_count" -eq 0 && ! -f "$STATUS_ROOT/accepted_checkpoint_watcher/first100k_model_checkpoint.complete" ]]; then
    start_acceptance_watcher
  fi

  if [[ "$count" -ge 100000 ]]; then
    log "raw production reached 100000; production supervision complete"
    break
  fi

  if [[ -f "$MODEL_PRIORITY_PAUSE" ]]; then
    log "model-training priority pause is active; production relaunch suppressed"
  elif [[ "$production_count" -eq 0 ]]; then
    if contract_ready; then
      start_production
    else
      log "BLOCKED: production process absent and resume contract validation failed"
    fi
  fi

  sleep "$POLL_SECONDS"
done
