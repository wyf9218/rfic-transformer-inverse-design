#!/usr/bin/env bash
# Wait for the first accepted-100k model checkpoint and complete raw queue,
# then prepare a larger adaptive candidate queue for the next accepted-100k
# increment. This script prepares and audits the queue; it does not run EMX.

set -u

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
FIRST_PRODUCTION="${FIRST_PRODUCTION:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
FIRST_STATUS="${FIRST_STATUS:-$BASE/status/first100k_urgent_targeted_20260709}"
FIRST_WATCHER="$FIRST_STATUS/accepted_checkpoint_watcher"
FIRST_MODEL_COMPLETE="$FIRST_WATCHER/first100k_model_checkpoint.complete"
FIRST_RAW_MILESTONE_COMPLETE="$FIRST_WATCHER/milestone_100000/milestone.complete"
FIRST_ACCEPTED_POOL="$FIRST_WATCHER/milestone_100000/accepted_pool"
TARGET_ENVELOPE="${TARGET_ENVELOPE:-$PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json}"
OUT_DIR="${OUT_DIR:-$FIRST_STATUS/chunk2_adaptive_queue_preparation_20260710}"
QUEUE_COUNT="${QUEUE_COUNT:-120000}"
CANDIDATE_COUNT="${CANDIDATE_COUNT:-1200000}"
POLL_SECONDS="${POLL_SECONDS:-300}"

LOG="$OUT_DIR/chunk2_queue_preparation.log"
LOCK_DIR="$OUT_DIR/active.lock"
READY_MARKER="$OUT_DIR/chunk2_queue_preparation.complete"
SUMMARY="$OUT_DIR/chunk2_queue_preparation_summary.json"
mkdir -p "$OUT_DIR"

if [[ -f "$READY_MARKER" ]]; then
  exit 0
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s chunk2 preparation watcher already active\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG"
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

raw_count() {
  find "$FIRST_PRODUCTION/dataset" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l | tr -d ' '
}

first_contract_ready() {
  [[ -f "$FIRST_MODEL_COMPLETE" ]] \
    && [[ -f "$FIRST_RAW_MILESTONE_COMPLETE" ]] \
    && [[ "$(raw_count)" -ge 100000 ]]
}

accepted_pool_ready() {
  "$PY" - "$FIRST_ACCEPTED_POOL/accepted_pool_merge_summary.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("overall_status") != "PASS":
    raise SystemExit(2)
if int(payload.get("row_count") or 0) < 100000:
    raise SystemExit(3)
PY
}

log "chunk2 queue preparation watcher start"
while ! first_contract_ready; do
  log "waiting raw_s4p=$(raw_count) model_complete=$([[ -f "$FIRST_MODEL_COMPLETE" ]] && echo 1 || echo 0) milestone100000=$([[ -f "$FIRST_RAW_MILESTONE_COMPLETE" ]] && echo 1 || echo 0)"
  sleep "$POLL_SECONDS"
done

if ! accepted_pool_ready; then
  log "ERROR: first accepted pool contract is not ready"
  exit 2
fi

log "first accepted-100k contract ready; building chunk2 adaptive queue"
env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 \
  bash "$PROJECT/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh" \
  --dataset-dir "$FIRST_ACCEPTED_POOL" \
  --out-dir "$OUT_DIR/adaptive_round" \
  --queue-count "$QUEUE_COUNT" \
  --candidate-count "$CANDIDATE_COUNT" \
  --prediction-batch-size 8192 \
  --seed 2026071002 \
  --k-neighbors 8 \
  --target-envelope-config "$TARGET_ENVELOPE" \
  --local-target-fraction 0.50 \
  --rare-marginal-fraction 0.20 \
  --rare-marginal-bins 10 \
  --rare-marginal-feature-weights 0.5,0.5,2.0,1.5 \
  --local-seed-count 8 \
  --local-perturbation-scales 0.01,0.03,0.08 \
  --reachable-targets-only \
  --redistribute-reachable-quota \
  --require-inside-target-bin \
  --python "$PY" >> "$LOG" 2>&1
round_rc=$?

"$PY" - "$SUMMARY" "$OUT_DIR/adaptive_round/adaptive_physical_acquisition_round_summary.json" "$OUT_DIR/adaptive_round/queue/mars56_grounded_s4p_candidate_queue.csv" "$OUT_DIR/adaptive_round/provenance/mars56_s4p_candidate_queue_provenance_summary.json" "$FIRST_ACCEPTED_POOL/accepted_pool_merge_summary.json" "$QUEUE_COUNT" "$CANDIDATE_COUNT" "$round_rc" <<'PY'
import csv
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

target, round_raw, queue_raw, provenance_raw, pool_raw, queue_expected, candidate_count, returncode = sys.argv[1:]
round_path = pathlib.Path(round_raw)
queue_path = pathlib.Path(queue_raw)
provenance_path = pathlib.Path(provenance_raw)
pool_path = pathlib.Path(pool_raw)

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

def record(path):
    item = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        data = path.read_bytes()
        item.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return item

round_summary = read_json(round_path)
provenance = read_json(provenance_path)
pool = read_json(pool_path)
queue_rows = 0
if queue_path.is_file():
    with queue_path.open(newline="", encoding="utf-8-sig") as handle:
        queue_rows = sum(1 for _ in csv.DictReader(handle))
checks = {
    "adaptive_round_pass": round_summary.get("overall_status") == "PASS",
    "provenance_pass": provenance.get("overall_status") == "PASS",
    "queue_count_exact": queue_rows == int(queue_expected),
    "accepted_pool_at_least_100k": int(pool.get("row_count") or 0) >= 100000,
    "command_returncode_zero": int(returncode) == 0,
}
status = "PASS" if all(checks.values()) else "FAIL"
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "overall_status": status,
    "decision": "CHUNK2_ADAPTIVE_QUEUE_READY_FOR_SEPARATE_EMX_GATE" if status == "PASS" else "DO_NOT_START_CHUNK2_EMX",
    "accepted_pool": record(pool_path),
    "adaptive_round_summary": record(round_path),
    "candidate_queue": record(queue_path),
    "provenance_summary": record(provenance_path),
    "requested_candidate_pool_count": int(candidate_count),
    "requested_queue_count": int(queue_expected),
    "actual_queue_count": queue_rows,
    "checks": checks,
    "scientific_boundary": "Queue readiness is not an EMX label or uniformity result; chunk2 still needs real Touchstone generation and accepted-row checkpointing.",
}
pathlib.Path(target).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
if status != "PASS":
    raise SystemExit(2)
PY
summary_rc=$?

if [[ "$round_rc" -ne 0 || "$summary_rc" -ne 0 ]]; then
  log "ERROR: chunk2 queue preparation failed round_rc=$round_rc summary_rc=$summary_rc"
  exit 2
fi
touch "$READY_MARKER"
log "chunk2 adaptive queue preparation PASS queue_count=$QUEUE_COUNT"
