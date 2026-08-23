#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PRODUCTION_DIR="${PRODUCTION_DIR:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
STATUS_DIR="${STATUS_DIR:-$BASE/status/first100k_urgent_targeted_20260709/live_monitor}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
TARGET_COUNT="${TARGET_COUNT:-100000}"
SOURCE_VALID_COUNT="${SOURCE_VALID_COUNT:-26410}"
FIRST_CHECKPOINT_NEW_COUNT="${FIRST_CHECKPOINT_NEW_COUNT:-73590}"
POLL_SECONDS="${POLL_SECONDS:-300}"

mkdir -p "$STATUS_DIR"
CSV="$STATUS_DIR/first100k_live_progress.csv"
ETA_CSV="$STATUS_DIR/first100k_live_eta_v2.csv"
JSON="$STATUS_DIR/first100k_live_status.json"
ACCEPTANCE_STATUS="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher/latest_acceptance_status.json"
MODEL_PAUSE_MARKER="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher/model_training_priority.pause_production"
if [ ! -s "$CSV" ]; then
  printf '%s\n' 'timestamp_epoch,timestamp_local,raw_s4p_count,source_valid_count,raw_combined_count,worker_count,runner_count,elapsed_seconds,seconds_per_raw_s4p,eta_seconds_to_100k_queue' > "$CSV"
fi
if [ ! -s "$ETA_CSV" ]; then
  printf '%s\n' 'timestamp_epoch,timestamp_local,raw_s4p_count,rolling_seconds_per_raw_s4p,formal_acceptance_rate,estimated_raw_needed_for_100k_accepted,eta_seconds_to_100k_accepted,eta_seconds_to_100k_raw,state' > "$ETA_CSV"
fi

START_MARKER="$PRODUCTION_DIR/candidate_queue_preflight/mars56_s4p_candidate_queue_provenance_summary.json"
START_EPOCH="$(stat -c %Y "$START_MARKER" 2>/dev/null || date +%s)"

while :; do
  NOW="$(date +%s)"
  LOCAL_TIME="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  RAW_COUNT="$( { find "$PRODUCTION_DIR/dataset" -type f -name '*.s4p' -size +0c 2>/dev/null || true; } | wc -l | tr -d ' ')"
  WORKERS="$( { pgrep -af "run_candidate_queue_dataset.py.*$PRODUCTION_DIR/dataset" || true; } | awk '!/run_candidate_queue_dataset_parallel.py/' | wc -l | tr -d ' ')"
  RUNNERS="$( { pgrep -af "run_candidate_queue_dataset_parallel.py.*$PRODUCTION_DIR/dataset" || true; } | wc -l | tr -d ' ')"
  ELAPSED=$((NOW - START_EPOCH))
  COMBINED=$((SOURCE_VALID_COUNT + RAW_COUNT))

  "$PY" - "$JSON.tmp" "$ETA_CSV" "$CSV" "$ACCEPTANCE_STATUS" "$MODEL_PAUSE_MARKER" "$NOW" "$LOCAL_TIME" "$RAW_COUNT" "$SOURCE_VALID_COUNT" "$COMBINED" "$WORKERS" "$RUNNERS" "$ELAPSED" "$TARGET_COUNT" "$FIRST_CHECKPOINT_NEW_COUNT" "$PRODUCTION_DIR" <<'PY'
import csv
import json
import math
import pathlib
import sys

(out, eta_csv_raw, progress_csv_raw, acceptance_raw, pause_raw, now, local_time, raw, source, combined, workers, runners, elapsed, target, checkpoint, production) = sys.argv[1:]
raw = int(raw); source = int(source); combined = int(combined); elapsed = int(elapsed)
target = int(target); checkpoint = int(checkpoint)
rate = elapsed / raw if raw > 0 else None
progress_path = pathlib.Path(progress_csv_raw)
recent = []
if progress_path.is_file():
    with progress_path.open(newline="", encoding="utf-8-sig") as handle:
        recent = list(csv.DictReader(handle))[-4:]
rolling_rate = None
for row in recent:
    try:
        old_time = int(row["timestamp_epoch"])
        old_count = int(row["raw_s4p_count"])
    except (KeyError, TypeError, ValueError):
        continue
    if raw > old_count and int(now) > old_time:
        rolling_rate = (int(now) - old_time) / (raw - old_count)
        break
if rolling_rate is None:
    rolling_rate = rate

acceptance_payload = {}
acceptance_path = pathlib.Path(acceptance_raw)
if acceptance_path.is_file():
    acceptance_payload = json.loads(acceptance_path.read_text(encoding="utf-8"))
formal_raw = int(acceptance_payload.get("raw_new_s4p_milestone") or 0)
formal_accepted = int(acceptance_payload.get("accepted_combined_count") or 0)
formal_new_accepted = max(0, formal_accepted - source)
formal_acceptance_rate = formal_new_accepted / formal_raw if formal_raw > 0 else None
estimated_raw_needed = (
    math.ceil(max(0, target - source) / formal_acceptance_rate)
    if formal_acceptance_rate is not None and formal_acceptance_rate > 0
    else None
)
eta_accepted = (
    max(0, estimated_raw_needed - raw) * rolling_rate
    if estimated_raw_needed is not None and rolling_rate is not None
    else None
)
eta_raw = max(0, target - raw) * rolling_rate if rolling_rate is not None else None
pause_active = pathlib.Path(pause_raw).is_file()
state = "QUEUE_COMPLETE" if raw >= target else "MODEL_TRAINING_PRIORITY" if pause_active else "RUNNING" if int(runners) > 0 else "STOPPED_AWAITING_SUPERVISOR"
payload = {
    "timestamp_epoch": int(now),
    "timestamp_local": local_time,
    "state": state,
    "production_dir": production,
    "raw_s4p_count": raw,
    "target_queue_count": target,
    "source_accepted_valid_count": source,
    "raw_combined_count": combined,
    "first_checkpoint_new_raw_threshold": checkpoint,
    "first_checkpoint_raw_threshold_reached": raw >= checkpoint,
    "worker_count": int(workers),
    "runner_count": int(runners),
    "elapsed_seconds": elapsed,
    "seconds_per_raw_s4p": rate,
    "rolling_seconds_per_raw_s4p": rolling_rate,
    "formal_acceptance_checkpoint_raw_count": formal_raw,
    "formal_acceptance_checkpoint_combined_count": formal_accepted,
    "formal_new_sample_acceptance_rate": formal_acceptance_rate,
    "estimated_raw_needed_for_100k_accepted": estimated_raw_needed,
    "eta_seconds_to_100k_accepted": eta_accepted,
    "eta_seconds_to_queue_completion": eta_raw,
    "scientific_caveat": "Raw S4P count is not the accepted Lp/Ls/Q/K training count; response extraction, range filtering, uniformity audit, and traceability gates are still required.",
}
pathlib.Path(out).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
with pathlib.Path(eta_csv_raw).open("a", newline="", encoding="utf-8") as handle:
    csv.writer(handle).writerow(
        [
            int(now),
            local_time,
            raw,
            rolling_rate,
            formal_acceptance_rate,
            estimated_raw_needed,
            eta_accepted,
            eta_raw,
            state,
        ]
    )
PY
  mv "$JSON.tmp" "$JSON"

  RATE=""
  ETA=""
  if [ "$RAW_COUNT" -gt 0 ]; then
    RATE="$(awk -v e="$ELAPSED" -v n="$RAW_COUNT" 'BEGIN { printf "%.9f", e/n }')"
    ETA="$(awk -v r="$RATE" -v t="$TARGET_COUNT" -v n="$RAW_COUNT" 'BEGIN { printf "%.3f", (t-n)*r }')"
  fi
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$NOW" "$LOCAL_TIME" "$RAW_COUNT" "$SOURCE_VALID_COUNT" "$COMBINED" "$WORKERS" "$RUNNERS" "$ELAPSED" "$RATE" "$ETA" >> "$CSV"

  if [ "$RAW_COUNT" -ge "$TARGET_COUNT" ]; then
    break
  fi
  sleep "$POLL_SECONDS"
done
