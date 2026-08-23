#!/usr/bin/env bash
set -euo pipefail

# Prepare a new physical-feature-targeted acquisition queue after Duo/SSH is
# available.  This bridges the gap between "uniformity failed" and "the next
# EMX acquisition is targeted at sparse Lp/Ls/Q/|K| bins".
#
# Default behavior is conservative: build/audit the targeted queue, but do not
# launch EMX unless RUN_EMX=1 is explicitly set by the caller.
#
# Dry run:
#   DRY_RUN=1 bash RUN_MARS56_ADAPTIVE_ACQUISITION_AFTER_DUO_20260708.sh
#
# Build the next queue on MARS:
#   DRY_RUN=0 bash RUN_MARS56_ADAPTIVE_ACQUISITION_AFTER_DUO_20260708.sh

JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
USER_NAME="${USER_NAME:-researcher}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
REMOTE_PYTHON="${REMOTE_PYTHON:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
CAMPAIGN_BASE="${CAMPAIGN_BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
CONFIG="${CONFIG:-$REMOTE_PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
TARGET_ENVELOPE_CONFIG="${TARGET_ENVELOPE_CONFIG:-$REMOTE_PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json}"

DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
RUN_EMX="${RUN_EMX:-0}"
QUEUE_COUNT="${QUEUE_COUNT:-8000}"
CANDIDATE_COUNT="${CANDIDATE_COUNT:-300000}"
PREDICTION_BATCH_SIZE="${PREDICTION_BATCH_SIZE:-4096}"
BINS="${BINS:-4}"
FEATURE_COLUMNS="${FEATURE_COLUMNS:-lp_nh_center,ls_nh_center,q_center,k_abs_center}"
SOURCE_DATASET_DIR="${SOURCE_DATASET_DIR:-}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

case "$DRY_RUN" in 0|1) ;;
  *) echo "ERROR: DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$FORCE" in 0|1) ;;
  *) echo "ERROR: FORCE must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_EMX" in 0|1) ;;
  *) echo "ERROR: RUN_EMX must be 0 or 1." >&2; exit 2 ;;
esac
for int_var in QUEUE_COUNT CANDIDATE_COUNT PREDICTION_BATCH_SIZE BINS; do
  value="${!int_var}"
  if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -le 0 ]; then
    echo "ERROR: $int_var must be a positive integer." >&2
    exit 2
  fi
done
for value in "$SSH_CONTROL_PATH" "$REMOTE_PROJECT" "$REMOTE_PYTHON" "$CAMPAIGN_BASE" "$CONFIG" "$TARGET_ENVELOPE_CONFIG" "$SOURCE_DATASET_DIR" "$FEATURE_COLUMNS"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path/settings contain unsupported quote or newline characters." >&2
    exit 2
  fi
done

SSH_ARGS=(-tt)
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_RUN <<'REMOTE' || true
set -euo pipefail

status_json="$BASE/status/adaptive_physical_acquisition_after_duo_latest_summary.json"
mkdir -p "$BASE/status" "$BASE/candidate_queues" "$BASE/adaptive_runs" "$BASE/logs"

write_status() {
  local state="$1"
  local reason="$2"
  local source_dir="${3:-}"
  local out_dir="${4:-}"
  local queue_csv="${5:-}"
  local run_dir="${6:-}"
  "$PY" - "$status_json" "$state" "$reason" "$source_dir" "$out_dir" "$queue_csv" "$run_dir" "$QUEUE_COUNT" "$CANDIDATE_COUNT" "$DRY_RUN" "$RUN_EMX" "$TARGET_ENVELOPE_CONFIG" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    state,
    reason,
    source_dir,
    out_dir,
    queue_csv,
    run_dir,
    queue_count,
    candidate_count,
    dry_run,
    run_emx,
    target_envelope_config,
) = sys.argv[1:]
payload = {
    "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "state": state,
    "reason": reason,
    "source_dataset_dir": source_dir,
    "adaptive_round_dir": out_dir,
    "queue_csv": queue_csv,
    "adaptive_run_dir": run_dir,
    "queue_count": int(queue_count),
    "candidate_count": int(candidate_count),
    "dry_run": dry_run == "1",
    "run_emx": run_emx == "1",
    "target_envelope_config": target_envelope_config,
    "k_axis_policy": "|K| via k_abs_center; signed k_center may be present but is not the acquisition axis.",
}
Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

choose_source_dataset() {
  if [ -n "${SOURCE_DATASET_DIR:-}" ]; then
    if [ -f "$SOURCE_DATASET_DIR/dataset_rows.csv" ]; then
      printf '%s\n' "$SOURCE_DATASET_DIR"
      return 0
    fi
    echo "ERROR: SOURCE_DATASET_DIR has no dataset_rows.csv: $SOURCE_DATASET_DIR" >&2
    return 1
  fi
  "$PY" - "$BASE/status" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
candidates = []
for path in root.glob("accepted_inrange_pool_after_*"):
    if not (path / "dataset_rows.csv").is_file():
        continue
    rows = 0
    try:
        csv_path = path / "dataset_rows.csv"
        with csv_path.open(encoding="utf-8", errors="replace") as fh:
            rows = max(sum(1 for _ in fh) - 1, 0)
    except Exception:
        rows = 0
    try:
        mtime = csv_path.stat().st_mtime
    except OSError:
        mtime = 0
    candidates.append((mtime, rows, str(path)))
if not candidates:
    raise SystemExit(1)
candidates.sort(key=lambda item: (item[0], item[1], item[2]))
print(candidates[-1][2])
PY
}

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'project=%s\n' "$PROJECT"
printf 'dry_run=%s\n' "$DRY_RUN"
printf 'run_emx=%s\n' "$RUN_EMX"
printf 'queue_count=%s\n' "$QUEUE_COUNT"
printf 'candidate_count=%s\n' "$CANDIDATE_COUNT"
printf 'feature_columns=%s\n' "$FEATURE_COLUMNS"
printf 'target_envelope_config=%s\n' "$TARGET_ENVELOPE_CONFIG"
echo "purpose=build_next_targeted_queue_when_Lp_Ls_Q_absK_uniformity_is_sparse"

if [ ! -d "$BASE" ]; then
  write_status FAIL "campaign_base_missing" "" "" "" ""
  echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_BASE_MISSING"
  exit 1
fi
if [ ! -x "$PY" ]; then
  write_status FAIL "python_missing_or_not_executable" "" "" "" ""
  echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_PYTHON_MISSING"
  exit 1
fi
if [ ! -f "$PROJECT/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh" ]; then
  write_status FAIL "remote_adaptive_round_script_missing" "" "" "" ""
  echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_SCRIPT_MISSING"
  exit 1
fi
if [ ! -f "$PROJECT/scripts/merge_physical_feature_accepted_pool.py" ]; then
  write_status FAIL "remote_accepted_pool_merge_script_missing" "" "" "" ""
  echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_MERGE_SCRIPT_MISSING"
  exit 1
fi
if [ -n "$TARGET_ENVELOPE_CONFIG" ] && [ ! -f "$TARGET_ENVELOPE_CONFIG" ]; then
  write_status FAIL "target_envelope_config_missing" "" "" "" ""
  echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_TARGET_ENVELOPE_MISSING"
  exit 1
fi

source_dir="$(choose_source_dataset)"
source_csv="$source_dir/dataset_rows.csv"
uniformity_summary="$source_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json"
source_rows="$(awk 'END { if (NR > 0) print NR - 1; else print 0 }' "$source_csv" 2>/dev/null || echo 0)"
source_hash="$("$PY" - "$source_csv" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as fh:
    print(hashlib.sha256(fh.read()).hexdigest()[:12])
PY
)"
source_tag="$(basename "$source_dir")"
adaptive_tag="adaptive_${source_tag}_${source_hash}_n${QUEUE_COUNT}"
out_dir="$BASE/candidate_queues/$adaptive_tag"
queue_csv="$out_dir/queue/mars56_grounded_s4p_candidate_queue.csv"
summary="$out_dir/adaptive_physical_acquisition_round_summary.json"
run_dir="$BASE/adaptive_runs/$adaptive_tag"

printf 'source_dataset_dir=%s\n' "$source_dir"
printf 'source_rows=%s\n' "$source_rows"
printf 'source_hash=%s\n' "$source_hash"
printf 'uniformity_summary=%s\n' "$uniformity_summary"
if [ -f "$uniformity_summary" ]; then
  "$PY" - "$uniformity_summary" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print("uniformity_summary_parse_error=", type(exc).__name__)
else:
    fd = data.get("four_dimensional_uniformity", {})
    print("uniformity_overall_status=", data.get("overall_status"))
    print("uniformity_valid_feature_count=", data.get("valid_feature_count"))
    print("uniformity_4d_occupied_fraction=", fd.get("occupied_fraction"))
PY
else
  echo "uniformity_summary_missing"
fi
printf 'adaptive_round_dir=%s\n' "$out_dir"
printf 'queue_csv=%s\n' "$queue_csv"

uniformity_status="$("$PY" - "$uniformity_summary" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "MISSING"))
except Exception:
    print("MISSING")
PY
)"
if [ "$uniformity_status" = "PASS" ] && [ "$FORCE" != "1" ]; then
  write_status SKIP_UNIFORMITY_ALREADY_PASS "source_uniformity_pass_no_adaptive_needed" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
  echo "ADAPTIVE_ACQUISITION_STATUS=SKIP_UNIFORMITY_ALREADY_PASS"
  exit 0
fi

existing_status="$("$PY" - "$summary" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "MISSING"))
except Exception:
    print("MISSING")
PY
)"
if [ "$existing_status" = "PASS" ] && [ -s "$queue_csv" ] && [ "$FORCE" != "1" ]; then
  write_status QUEUE_ALREADY_READY "adaptive_queue_already_passed_for_source_hash" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
  echo "ADAPTIVE_ACQUISITION_STATUS=QUEUE_ALREADY_READY"
else
  if [ "$DRY_RUN" = "1" ]; then
    write_status WOULD_BUILD_QUEUE "dry_run_would_build_adaptive_queue" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
    echo "ADAPTIVE_ACQUISITION_STATUS=WOULD_BUILD_QUEUE"
    exit 0
  fi
  mkdir -p "$out_dir"
  round_cmd=(
    bash "$PROJECT/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh" \
    --dataset-dir "$source_dir" \
    --out-dir "$out_dir" \
    --queue-count "$QUEUE_COUNT" \
    --candidate-count "$CANDIDATE_COUNT" \
    --prediction-batch-size "$PREDICTION_BATCH_SIZE" \
    --bins "$BINS" \
    --feature-columns "$FEATURE_COLUMNS" \
    --python "$PY"
  )
  if [ -n "$TARGET_ENVELOPE_CONFIG" ]; then
    round_cmd+=(--target-envelope-config "$TARGET_ENVELOPE_CONFIG")
  fi
  "${round_cmd[@]}"
  new_status="$("$PY" - "$summary" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "MISSING"))
except Exception:
    print("MISSING")
PY
)"
  if [ "$new_status" != "PASS" ] || [ ! -s "$queue_csv" ]; then
    write_status FAIL "adaptive_queue_build_failed" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
    echo "ADAPTIVE_ACQUISITION_STATUS=FAIL_QUEUE_BUILD"
    exit 1
  fi
  write_status QUEUE_READY "adaptive_queue_built" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
  echo "ADAPTIVE_ACQUISITION_STATUS=QUEUE_READY"
fi

if [ "$RUN_EMX" != "1" ]; then
  echo "ADAPTIVE_EMX_STATUS=SKIPPED_RUN_EMX_0"
  exit 0
fi
if [ "$DRY_RUN" = "1" ]; then
  echo "ADAPTIVE_EMX_STATUS=WOULD_RUN_EMX"
  exit 0
fi
if [ -f "$run_dir/mars56_s4p_100k_chunk_run_summary.json" ]; then
  run_status="$("$PY" - "$run_dir/mars56_s4p_100k_chunk_run_summary.json" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("overall_status", "MISSING"))
except Exception:
    print("MISSING")
PY
)"
  if [ "$run_status" = "PASS" ] && [ "$FORCE" != "1" ]; then
    write_status ADAPTIVE_EMX_ALREADY_PASS "adaptive_emx_run_already_passed" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
    echo "ADAPTIVE_EMX_STATUS=ALREADY_PASS"
    exit 0
  fi
fi
runner_count="$(ps -fu researcher | grep -F "$run_dir" | grep -F 'run_candidate_queue_dataset_parallel.py' | grep -v grep | wc -l | tr -d ' ')"
if [ "$runner_count" -gt 0 ]; then
  write_status ADAPTIVE_EMX_RUNNING "adaptive_emx_runner_already_running" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
  echo "ADAPTIVE_EMX_STATUS=ALREADY_RUNNING"
  exit 0
fi
min_valid="$("$PY" - "$QUEUE_COUNT" <<'PY'
import math
import sys
print(max(1, math.ceil(int(sys.argv[1]) * 0.85)))
PY
)"
mkdir -p "$run_dir"
bash "$PROJECT/scripts/run_mars56_s4p_100k_chunk_from_queue.sh" \
  --candidate-csv "$queue_csv" \
  --config "$CONFIG" \
  --out-dir "$run_dir" \
  --chunk-index 0 \
  --count "$QUEUE_COUNT" \
  --min-valid "$min_valid" \
  --jobs 48 \
  --python "$PY"
training_csv="$run_dir/checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv"
merged_pool_dir="$BASE/status/accepted_inrange_pool_after_${adaptive_tag}"
if [ ! -s "$training_csv" ]; then
  write_status FAIL "adaptive_emx_finished_but_training_csv_missing" "$source_dir" "$out_dir" "$queue_csv" "$run_dir"
  echo "ADAPTIVE_EMX_STATUS=FAIL_TRAINING_CSV_MISSING"
  exit 1
fi
"$PY" "$PROJECT/scripts/merge_physical_feature_accepted_pool.py" \
  --base-pool-dir "$source_dir" \
  --training-csv "$training_csv" \
  --out-dir "$merged_pool_dir" \
  --min-row-count "$source_rows" \
  --uniformity-min-valid-count "$source_rows" \
  --run-uniformity \
  --require-plots \
  --no-fail-exit \
  --python "$PY"
write_status ADAPTIVE_EMX_DONE "adaptive_emx_command_finished_and_pool_merged" "$merged_pool_dir" "$out_dir" "$queue_csv" "$run_dir"
echo "ADAPTIVE_EMX_STATUS=DONE"
echo "ADAPTIVE_ACCEPTED_POOL_DIR=$merged_pool_dir"
REMOTE

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted if no SSH control connection exists."
echo "Adaptive acquisition: DRY_RUN=${DRY_RUN} RUN_EMX=${RUN_EMX} QUEUE_COUNT=${QUEUE_COUNT}"
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" \
  "BASE='${CAMPAIGN_BASE}' PROJECT='${REMOTE_PROJECT}' PY='${REMOTE_PYTHON}' CONFIG='${CONFIG}' TARGET_ENVELOPE_CONFIG='${TARGET_ENVELOPE_CONFIG}' DRY_RUN='${DRY_RUN}' FORCE='${FORCE}' RUN_EMX='${RUN_EMX}' QUEUE_COUNT='${QUEUE_COUNT}' CANDIDATE_COUNT='${CANDIDATE_COUNT}' PREDICTION_BATCH_SIZE='${PREDICTION_BATCH_SIZE}' BINS='${BINS}' FEATURE_COLUMNS='${FEATURE_COLUMNS}' SOURCE_DATASET_DIR='${SOURCE_DATASET_DIR}' ssh -tt ${MARS_HOST} 'BASE='\\''${CAMPAIGN_BASE}'\\'' PROJECT='\\''${REMOTE_PROJECT}'\\'' PY='\\''${REMOTE_PYTHON}'\\'' CONFIG='\\''${CONFIG}'\\'' TARGET_ENVELOPE_CONFIG='\\''${TARGET_ENVELOPE_CONFIG}'\\'' DRY_RUN='\\''${DRY_RUN}'\\'' FORCE='\\''${FORCE}'\\'' RUN_EMX='\\''${RUN_EMX}'\\'' QUEUE_COUNT='\\''${QUEUE_COUNT}'\\'' CANDIDATE_COUNT='\\''${CANDIDATE_COUNT}'\\'' PREDICTION_BATCH_SIZE='\\''${PREDICTION_BATCH_SIZE}'\\'' BINS='\\''${BINS}'\\'' FEATURE_COLUMNS='\\''${FEATURE_COLUMNS}'\\'' SOURCE_DATASET_DIR='\\''${SOURCE_DATASET_DIR}'\\'' bash -s'" \
  <<<"$REMOTE_RUN"
