#!/usr/bin/env bash
# Run one adaptive candidate queue through real EMX and merge accepted labels.
#
# This is an accepted-row round, not a raw-file checkpoint.  Raw S4P production
# is followed by exact Touchstone extraction, geometry enrichment, explicit
# physical-range filtering, independent-geometry deduplication, and an optional
# cumulative model checkpoint.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG=""
QUEUE_CSV=""
QUEUE_DIR=""
SOURCE_POOL_DIR=""
OUT_DIR=""
TARGET_ACCEPTED=""
CHECKPOINT_INDEX=""
RAW_COUNT=""
JOBS="${JOBS:-48}"
CHUNK_SIZE="${CHUNK_SIZE:-64}"
MAX_RUN_ATTEMPTS="${MAX_RUN_ATTEMPTS:-3}"
MODEL_MAX_CANDIDATES="${MODEL_MAX_CANDIDATES:-8}"
MODEL_MAX_EPOCHS="${MODEL_MAX_EPOCHS:-120}"
MODEL_PATIENCE="${MODEL_PATIENCE:-15}"
MODEL_THREADS="${MODEL_THREADS:-8}"
MODEL_SEED="${MODEL_SEED:-20260711}"
MODEL_SPLIT_SEED="${MODEL_SPLIT_SEED:-20260711}"
SEED="${SEED:-20260710}"
NO_FAIL_EXIT=0
PREFLIGHT_ONLY=0

GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"

usage() {
  cat <<'USAGE'
Usage:
  run_real_emx_accepted_increment_round.sh \
    --queue-csv QUEUE.csv --config CONFIG.yaml \
    --source-pool-dir ACCEPTED_POOL --out-dir ROUND_DIR \
    --target-accepted 200000 --checkpoint-index 2 [--raw-count 120000]

The queue must be physical-feature targeted and provenance-audited.  This
script uses only realized EMX .s4p files as labels.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --queue-csv) QUEUE_CSV="$2"; shift 2 ;;
    --queue-dir) QUEUE_DIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --source-pool-dir) SOURCE_POOL_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --target-accepted) TARGET_ACCEPTED="$2"; shift 2 ;;
    --checkpoint-index) CHECKPOINT_INDEX="$2"; shift 2 ;;
    --raw-count) RAW_COUNT="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --chunk-size) CHUNK_SIZE="$2"; shift 2 ;;
    --max-run-attempts) MAX_RUN_ATTEMPTS="$2"; shift 2 ;;
    --model-max-candidates) MODEL_MAX_CANDIDATES="$2"; shift 2 ;;
    --model-max-epochs) MODEL_MAX_EPOCHS="$2"; shift 2 ;;
    --model-patience) MODEL_PATIENCE="$2"; shift 2 ;;
    --model-threads) MODEL_THREADS="$2"; shift 2 ;;
    --model-seed) MODEL_SEED="$2"; shift 2 ;;
    --split-seed) MODEL_SPLIT_SEED="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    --no-fail-exit) NO_FAIL_EXIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for required in QUEUE_CSV CONFIG SOURCE_POOL_DIR OUT_DIR TARGET_ACCEPTED CHECKPOINT_INDEX; do
  if [[ -z "${!required}" ]]; then
    echo "ERROR: missing required value: $required" >&2
    usage >&2
    exit 2
  fi
done

QUEUE_CSV="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$QUEUE_CSV")"
CONFIG="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CONFIG")"
SOURCE_POOL_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$SOURCE_POOL_DIR")"
OUT_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OUT_DIR")"
if [[ -z "$QUEUE_DIR" ]]; then QUEUE_DIR="$(dirname "$QUEUE_CSV")"; fi
QUEUE_DIR="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$QUEUE_DIR")"

if [[ ! -f "$QUEUE_CSV" || ! -f "$CONFIG" || ! -f "$SOURCE_POOL_DIR/dataset_rows.csv" ]]; then
  echo "ERROR: queue, config, or source accepted pool is missing." >&2
  exit 2
fi
if [[ -z "$RAW_COUNT" ]]; then
  RAW_COUNT="$("$PYTHON_BIN" - "$QUEUE_CSV" <<'PY'
import csv,pathlib,sys
with pathlib.Path(sys.argv[1]).open(newline="",encoding="utf-8-sig") as h:
    print(sum(1 for _ in csv.DictReader(h)))
PY
)"
fi
for numeric in RAW_COUNT TARGET_ACCEPTED CHECKPOINT_INDEX JOBS CHUNK_SIZE MAX_RUN_ATTEMPTS; do
  if ! [[ "${!numeric}" =~ ^[0-9]+$ ]] || [[ "${!numeric}" -lt 1 ]]; then
    echo "ERROR: $numeric must be a positive integer." >&2
    exit 2
  fi
done

mkdir -p "$OUT_DIR"
LOCK_DIR="$OUT_DIR/active.lock"
COMPLETE_MARKER="$OUT_DIR/round.complete"
SUMMARY="$OUT_DIR/real_emx_accepted_increment_round_summary.json"
LOG="$OUT_DIR/real_emx_accepted_increment_round.log"
if [[ -f "$COMPLETE_MARKER" && -f "$SUMMARY" ]]; then exit 0; fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "ERROR: round lock is active: $LOCK_DIR pid=$old_pid" >&2
    exit 2
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" || exit 2
fi
echo "$$" > "$LOCK_DIR/pid"
cleanup() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG"; }
run_logged() {
  local label="$1"; shift
  {
    printf '\n===== %s =====\n' "$label"; date '+%Y-%m-%d %H:%M:%S %Z'
    printf 'COMMAND'; printf ' %q' "$@"; printf '\n'
  } >> "$LOG"
  nice -n 15 "$@" >> "$LOG" 2>&1
  local rc=$?; log "$label returncode=$rc"; return "$rc"
}

json_value() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1])
if not p.is_file(): print(""); raise SystemExit(0)
v=json.loads(p.read_text(encoding="utf-8"))
for part in sys.argv[2].split("."):
    v=v[part]
print(v)
PY
}

PREFLIGHT="$OUT_DIR/queue_preflight"
DATASET="$OUT_DIR/dataset"
INDEX="$OUT_DIR/stable_index"
FEATURES="$OUT_DIR/response_features"
ENRICHED="$OUT_DIR/enriched_geometry"
TRAINING="$OUT_DIR/new_training_table"
ACCEPTED="$OUT_DIR/accepted_pool"
MODEL="$OUT_DIR/model_checkpoint_${CHECKPOINT_INDEX}_n${TARGET_ACCEPTED}"

log "accepted increment round start raw=$RAW_COUNT target_accepted=$TARGET_ACCEPTED checkpoint=$CHECKPOINT_INDEX"
run_logged queue_provenance \
  "$PYTHON_BIN" "$SCRIPT_DIR/audit_mars56_s4p_candidate_queue_provenance.py" \
  --candidate-csv "$QUEUE_CSV" --expected-count "$RAW_COUNT" --out-dir "$PREFLIGHT" || exit 2
if [[ "$(json_value "$PREFLIGHT/mars56_s4p_candidate_queue_provenance_summary.json" overall_status)" != "PASS" ]]; then
  log "queue provenance failed"; exit 2
fi
if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
  touch "$OUT_DIR/preflight.pass"
  log "preflight-only PASS raw=$RAW_COUNT jobs=$JOBS target_accepted=$TARGET_ACCEPTED"
  exit 0
fi

production_pass=0
for attempt in $(seq 1 "$MAX_RUN_ATTEMPTS"); do
  log "EMX production attempt=$attempt"
  run_logged "emx_parallel_attempt_${attempt}" \
    env RFIC_SKIP_LAYOUT_PREVIEWS=1 RFIC_SKIP_LUMPED_COMPARE=1 \
    "$PYTHON_BIN" "$SCRIPT_DIR/run_candidate_queue_dataset_parallel.py" \
    --candidate-csv "$QUEUE_CSV" --config "$CONFIG" --out-dir "$DATASET" \
    --jobs "$JOBS" --expected-jobs "$JOBS" --chunk-size "$CHUNK_SIZE" \
    --max-count "$RAW_COUNT" --expected-count "$RAW_COUNT" --batch-size 1 \
    --force-wideband-5-60-0p5 --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 --expected-touchstone-extension .s4p \
    --expected-ports 4 --max-touchstone-checks "$RAW_COUNT" \
    --expected-port-mode single_ended_shield_grounded --expected-pin-purpose 51 \
    --resume-completed --no-fail-exit
  dataset_summary="$DATASET/parallel_candidate_queue_dataset_summary.json"
  if [[ "$(json_value "$dataset_summary" overall_status)" == "PASS" ]] \
    && [[ "$(json_value "$dataset_summary" merged_row_count)" == "$RAW_COUNT" ]]; then
    production_pass=1
    break
  fi
  log "EMX production contract not yet PASS; retrying incomplete shards"
  sleep 60
done
if [[ "$production_pass" -ne 1 ]]; then
  log "EMX production failed after $MAX_RUN_ATTEMPTS attempts"
  exit 2
fi

rm -rf "$INDEX" "$FEATURES" "$ENRICHED" "$TRAINING" "$ACCEPTED"
run_logged stable_touchstone_index \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_stable_touchstone_index.py" "$DATASET" \
  --out-dir "$INDEX" --max-count "$RAW_COUNT" --min-count "$RAW_COUNT" --clean --no-fail-exit || exit 2
if [[ "$(json_value "$INDEX/stable_touchstone_index_summary.json" status)" != "PASS" ]]; then exit 2; fi

run_logged extract_real_response_features \
  "$PYTHON_BIN" "$SCRIPT_DIR/extract_touchstone_response_features.py" "$INDEX" \
  --out-dir "$FEATURES" --expected-ports 4 --expected-frequency-start-ghz 5 \
  --expected-frequency-stop-ghz 60 --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 --target-frequency-ghz 15 --no-fail-exit || exit 2
if [[ "$(json_value "$FEATURES/response_feature_extraction_summary.json" overall_status)" != "PASS" ]]; then exit 2; fi

run_logged enrich_real_geometry \
  "$PYTHON_BIN" "$SCRIPT_DIR/enrich_response_features_with_geometry.py" \
  --features-csv "$FEATURES/response_features.csv" --out-dir "$ENRICHED" \
  --q-definition min --no-fail-exit || exit 2
if [[ "$(json_value "$ENRICHED/geometry_enrichment_manifest.json" overall_status)" != "PASS" ]]; then exit 2; fi

run_logged build_real_inverse_training_rows \
  "$PYTHON_BIN" "$SCRIPT_DIR/build_physical_feature_inverse_training_table.py" "$ENRICHED" \
  --out-dir "$TRAINING" --geometry-columns "$GEOMETRY_COLUMNS" \
  --input-prefix input__ --check-touchstone-exists --no-fail-exit || exit 2
if [[ "$(json_value "$TRAINING/physical_feature_inverse_training_manifest.json" overall_status)" != "PASS" ]]; then exit 2; fi

run_logged merge_range_accepted_unique_pool \
  "$PYTHON_BIN" "$SCRIPT_DIR/merge_physical_feature_accepted_pool.py" \
  --base-pool-dir "$SOURCE_POOL_DIR" \
  --training-csv "$TRAINING/physical_feature_inverse_training_table.csv" \
  --out-dir "$ACCEPTED" --min-row-count 1 \
  --lp-min-nh 0.5 --lp-max-nh 3.0 --ls-min-nh 0.5 --ls-max-nh 3.0 \
  --q-min 5 --q-max 25 --k-min 0 --k-max 0.8 \
  --bins 10 --pair-bins 10 --four-d-bins 4 --min-four-d-occupied-frac 0.50 \
  --min-four-d-entropy-frac 0.80 --max-four-d-bin-imbalance 4.0 \
  --run-uniformity --require-four-d-gate --require-plots --no-fail-exit || exit 2
if [[ "$(json_value "$ACCEPTED/accepted_pool_merge_summary.json" overall_status)" != "PASS" ]]; then exit 2; fi

accepted_count="$(json_value "$ACCEPTED/accepted_pool_merge_summary.json" row_count)"
model_status="NOT_REACHED"
uniformity_status="NOT_TESTED_AT_EXACT_CHECKPOINT"
if [[ "$accepted_count" -ge "$TARGET_ACCEPTED" ]]; then
  run_logged cumulative_model_checkpoint \
    bash "$SCRIPT_DIR/run_accepted_physical_feature_model_checkpoint.sh" \
    --accepted-pool-dir "$ACCEPTED" --out-dir "$MODEL" \
    --geometry-config "$CONFIG" \
    --checkpoint-count "$TARGET_ACCEPTED" --checkpoint-index "$CHECKPOINT_INDEX" \
    --seed "$SEED" --max-candidates "$MODEL_MAX_CANDIDATES" \
    --max-epochs "$MODEL_MAX_EPOCHS" --patience "$MODEL_PATIENCE" \
    --model-seed "$MODEL_SEED" --split-seed "$MODEL_SPLIT_SEED" \
    --model-threads "$MODEL_THREADS" --allow-provisional-uniformity \
    --no-fail-exit --python "$PYTHON_BIN" || exit 2
  model_manifest="$MODEL/accepted_physical_feature_model_checkpoint_manifest.json"
  model_status="$(json_value "$model_manifest" model_test_status)"
  uniformity_status="$(json_value "$model_manifest" uniformity_status)"
fi

"$PYTHON_BIN" - "$SUMMARY" "$CHECKPOINT_INDEX" "$TARGET_ACCEPTED" "$RAW_COUNT" "$QUEUE_CSV" "$DATASET/parallel_candidate_queue_dataset_summary.json" "$TRAINING/physical_feature_inverse_training_manifest.json" "$ACCEPTED/accepted_pool_merge_summary.json" "$MODEL/accepted_physical_feature_model_checkpoint_manifest.json" "$accepted_count" "$model_status" "$uniformity_status" <<'PY'
import hashlib,json,pathlib,sys
from datetime import datetime,timezone
(target,index,target_accepted,raw_count,queue,dataset,training,accepted,model,accepted_count,model_status,uniformity_status)=sys.argv[1:]
def read(raw):
    p=pathlib.Path(raw); return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
def rec(raw):
    p=pathlib.Path(raw); item={"path":str(p),"exists":p.is_file()}
    if p.is_file(): item["sha256"]=hashlib.sha256(p.read_bytes()).hexdigest()
    return item
model_data=read(model)
if model_data.get("overall_status")=="PASS":
    overall="PASS"; decision="FORMAL_ACCEPTED_CHECKPOINT_PASS"
elif model_status=="PASS":
    overall="PROVISIONAL_UNIFORMITY_FAIL"; decision="CONTINUE_ADAPTIVE_ACQUISITION_FOR_SPARSE_PHYSICAL_BINS"
else:
    overall="MORE_ACCEPTED_ROWS_REQUIRED"; decision="BUILD_NEXT_ADAPTIVE_QUEUE"
payload={
 "generated_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
 "overall_status":overall,"decision":decision,
 "checkpoint_index":int(index),"target_accepted_count":int(target_accepted),
 "raw_queue_count":int(raw_count),"accepted_combined_count":int(accepted_count),
 "realized_new_acceptance_rate":None,
 "model_test_status":model_status,"uniformity_status":uniformity_status,
 "artifacts":{"queue":rec(queue),"dataset":rec(dataset),"training":rec(training),"accepted_pool":rec(accepted),"model":rec(model)},
 "scientific_boundary":"Only realized EMX Touchstone rows inside explicit ranges and unique by the 10 independent geometry variables count toward accepted_combined_count.",
}
accepted_summary=read(accepted)
sources=accepted_summary.get("sources") or []
base_accepted=sum(int(s.get("accepted_after_source_filter") or 0) for s in sources if s.get("kind")=="base_pool")
accepted_new_unique=max(0,int(accepted_count)-base_accepted)
payload["realized_new_unique_accepted_count"]=accepted_new_unique
payload["realized_new_acceptance_rate"]=accepted_new_unique/float(int(raw_count))
payload["merge_reject_summary"]=accepted_summary.get("reject_summary") or {}
pathlib.Path(target).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
PY
summary_status="$(json_value "$SUMMARY" overall_status)"
touch "$COMPLETE_MARKER"
log "round complete status=$summary_status accepted=$accepted_count target=$TARGET_ACCEPTED"

if [[ "$summary_status" == "PASS" || "$NO_FAIL_EXIT" -eq 1 ]]; then exit 0; fi
exit 3
