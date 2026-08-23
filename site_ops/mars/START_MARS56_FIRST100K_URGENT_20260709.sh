#!/usr/bin/env bash
set -euo pipefail

# Prepare and run the first 100k physical-feature-targeted MARS56 S4P chunk.
#
# This launcher intentionally allows a non-uniform real-EMX source pool because
# the acquisition round is the mechanism used to target its sparse bins. The
# source audit is preserved verbatim and never relabeled as PASS. Surrogate
# values are used only to prioritize geometry; final labels come from EMX.

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
CONFIG="${CONFIG:-$PROJECT/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"
TARGET_ENVELOPE="${TARGET_ENVELOPE:-$PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json}"
SOURCE_POOL="${SOURCE_POOL:-$BASE/status/accepted_inrange_pool_after_chunk08_20260706}"
SOURCE_UNIFORMITY="${SOURCE_UNIFORMITY:-$SOURCE_POOL/physical_feature_uniformity/physical_feature_uniformity_summary.json}"

WORK_ROOT="${WORK_ROOT:-$BASE/status/first100k_urgent_targeted_20260709}"
PRED_DIR="$WORK_ROOT/predictions_1m"
ROUND_DIR="$WORK_ROOT/adaptive_round_100k"
QUEUE_CSV="$ROUND_DIR/queue/mars56_grounded_s4p_candidate_queue.csv"
PRODUCTION_DIR="${PRODUCTION_DIR:-$BASE/production_chunks/chunk_001_100k_urgent_targeted_20260709}"
STATUS_JSON="$WORK_ROOT/first100k_urgent_launch_status.json"

QUEUE_COUNT="${QUEUE_COUNT:-100000}"
CANDIDATE_COUNT="${CANDIDATE_COUNT:-1000000}"
JOBS="${JOBS:-48}"
SHARD_CHUNK_SIZE="${SHARD_CHUNK_SIZE:-64}"
MIN_SOURCE_VALID="${MIN_SOURCE_VALID:-25000}"
MIN_SOURCE_FOUR_D_OCCUPIED="${MIN_SOURCE_FOUR_D_OCCUPIED:-0.43}"

for value in "$BASE" "$PROJECT" "$PY" "$CONFIG" "$TARGET_ENVELOPE" "$SOURCE_POOL" "$WORK_ROOT" "$PRODUCTION_DIR"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" == *"'"* ]]; then
    echo "ERROR: path contains an unsupported quote or newline: $value" >&2
    exit 2
  fi
done

for value in "$QUEUE_COUNT" "$CANDIDATE_COUNT" "$JOBS" "$SHARD_CHUNK_SIZE" "$MIN_SOURCE_VALID"; do
  if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -le 0 ]; then
    echo "ERROR: expected a positive integer, got: $value" >&2
    exit 2
  fi
done

for path in "$PY" "$CONFIG" "$TARGET_ENVELOPE" "$SOURCE_POOL/dataset_rows.csv" "$SOURCE_UNIFORMITY"; do
  if [ ! -s "$path" ]; then
    echo "ERROR: required input is missing or empty: $path" >&2
    exit 2
  fi
done

mkdir -p "$WORK_ROOT" "$PRODUCTION_DIR"
cd "$PROJECT"

write_status() {
  local state="$1"
  local detail="$2"
  "$PY" - "$STATUS_JSON" "$state" "$detail" "$SOURCE_UNIFORMITY" "$PRED_DIR" "$ROUND_DIR" "$QUEUE_CSV" "$PRODUCTION_DIR" "$QUEUE_COUNT" "$CANDIDATE_COUNT" "$JOBS" "$SHARD_CHUNK_SIZE" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

(
    out_raw,
    state,
    detail,
    source_uniformity_raw,
    pred_dir,
    round_dir,
    queue_csv,
    production_dir,
    queue_count,
    candidate_count,
    jobs,
    shard_chunk_size,
) = sys.argv[1:]
source_path = pathlib.Path(source_uniformity_raw)
try:
    source = json.loads(source_path.read_text(encoding="utf-8"))
except Exception as exc:
    source = {"read_error": f"{type(exc).__name__}: {exc}"}
four_d = source.get("four_dimensional_uniformity") or {}
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "state": state,
    "detail": detail,
    "scientific_policy": {
        "source_uniformity_is_not_overridden": True,
        "nonuniform_source_allowed_only_for_targeted_acquisition": True,
        "surrogate_predictions_are_not_training_labels": True,
        "final_training_labels_must_come_from_emx_s4p": True,
        "production_acceptance_requires_realized_lp_ls_q_k_uniformity": True,
    },
    "source_uniformity": {
        "path": str(source_path),
        "overall_status": source.get("overall_status"),
        "valid_feature_count": source.get("valid_feature_count"),
        "four_d_occupied_fraction": four_d.get("occupied_fraction"),
    },
    "settings": {
        "queue_count": int(queue_count),
        "candidate_count": int(candidate_count),
        "jobs": int(jobs),
        "shard_chunk_size": int(shard_chunk_size),
        "frequency_start_ghz": 5.0,
        "frequency_stop_ghz": 60.0,
        "frequency_step_ghz": 0.5,
        "frequency_points": 111,
        "touchstone_extension": ".s4p",
        "ports": 4,
    },
    "artifacts": {
        "prediction_dir": pred_dir,
        "adaptive_round_dir": round_dir,
        "queue_csv": queue_csv,
        "production_dir": production_dir,
    },
}
out = pathlib.Path(out_raw)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

read_json_field() {
  local path="$1"
  local field="$2"
  "$PY" - "$path" "$field" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
try:
    value = json.loads(path.read_text(encoding="utf-8"))
    for part in field.split("."):
        value = value.get(part) if isinstance(value, dict) else None
except Exception:
    value = None
print("" if value is None else value)
PY
}

SOURCE_STATUS="$(read_json_field "$SOURCE_UNIFORMITY" overall_status)"
SOURCE_VALID="$(read_json_field "$SOURCE_UNIFORMITY" valid_feature_count)"
SOURCE_FOUR_D="$(read_json_field "$SOURCE_UNIFORMITY" four_dimensional_uniformity.occupied_fraction)"

"$PY" - "$SOURCE_VALID" "$SOURCE_FOUR_D" "$MIN_SOURCE_VALID" "$MIN_SOURCE_FOUR_D_OCCUPIED" <<'PY'
import sys

valid = int(float(sys.argv[1]))
four_d = float(sys.argv[2])
min_valid = int(sys.argv[3])
min_four_d = float(sys.argv[4])
if valid < min_valid:
    raise SystemExit(f"source valid rows {valid} < required {min_valid}")
if four_d < min_four_d:
    raise SystemExit(f"source 4D occupied fraction {four_d} < required {min_four_d}")
PY

write_status "SOURCE_ACCEPTED_FOR_TARGETED_ACQUISITION" "source_status=$SOURCE_STATUS valid=$SOURCE_VALID four_d=$SOURCE_FOUR_D"

PRED_SUMMARY="$PRED_DIR/candidate_physical_feature_prediction_summary.json"
PRED_CSV="$PRED_DIR/candidate_physical_feature_predictions.csv"
PRED_STATUS="$(read_json_field "$PRED_SUMMARY" overall_status)"
PRED_ROWS=0
if [ -s "$PRED_CSV" ]; then
  PRED_ROWS=$(( $(wc -l < "$PRED_CSV" | tr -d ' ') - 1 ))
fi
if [ "$PRED_STATUS" != "PASS" ] || [ "$PRED_ROWS" -ne "$CANDIDATE_COUNT" ]; then
  rm -rf "$PRED_DIR"
  mkdir -p "$PRED_DIR"
  write_status "BUILDING_SURROGATE_CANDIDATES" "candidate_count=$CANDIDATE_COUNT lhs_optimization=none"
  "$PY" scripts/build_physical_feature_surrogate_candidate_predictions.py "$SOURCE_POOL" \
    --out-dir "$PRED_DIR" \
    --candidate-count "$CANDIDATE_COUNT" \
    --prediction-batch-size 50000 \
    --seed 607111 \
    --lhs-optimization none \
    --k-neighbors 8 \
    --geometry-columns geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um \
    --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
    --no-plots \
    --no-fail-exit
fi

PRED_STATUS="$(read_json_field "$PRED_SUMMARY" overall_status)"
PRED_ROWS=$(( $(wc -l < "$PRED_CSV" | tr -d ' ') - 1 ))
if [ "$PRED_STATUS" != "PASS" ] || [ "$PRED_ROWS" -ne "$CANDIDATE_COUNT" ]; then
  write_status "FAIL" "surrogate candidate generation status=$PRED_STATUS rows=$PRED_ROWS"
  exit 3
fi

ROUND_SUMMARY="$ROUND_DIR/adaptive_physical_acquisition_round_summary.json"
ROUND_STATUS="$(read_json_field "$ROUND_SUMMARY" overall_status)"
QUEUE_ROWS=0
if [ -s "$QUEUE_CSV" ]; then
  QUEUE_ROWS=$(( $(wc -l < "$QUEUE_CSV" | tr -d ' ') - 1 ))
fi
if [ "$ROUND_STATUS" != "PASS" ] || [ "$QUEUE_ROWS" -ne "$QUEUE_COUNT" ]; then
  rm -rf "$ROUND_DIR"
  write_status "BUILDING_TARGETED_QUEUE" "queue_count=$QUEUE_COUNT source_status=$SOURCE_STATUS"
  env PYTHON_BIN="$PY" bash scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh \
    --dataset-dir "$SOURCE_POOL" \
    --out-dir "$ROUND_DIR" \
    --queue-count "$QUEUE_COUNT" \
    --candidate-predictions-csv "$PRED_CSV" \
    --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
    --bins 4 \
    --desired-total-count "$QUEUE_COUNT" \
    --target-envelope-config "$TARGET_ENVELOPE" \
    --reachable-targets-only \
    --redistribute-reachable-quota \
    --min-candidates-per-reachable-target 1 \
    --require-inside-target-bin \
    --python "$PY" \
    --no-fail-exit
fi

ROUND_STATUS="$(read_json_field "$ROUND_SUMMARY" overall_status)"
QUEUE_ROWS=$(( $(wc -l < "$QUEUE_CSV" | tr -d ' ') - 1 ))
if [ "$ROUND_STATUS" != "PASS" ] || [ "$QUEUE_ROWS" -ne "$QUEUE_COUNT" ]; then
  write_status "FAIL" "targeted queue status=$ROUND_STATUS rows=$QUEUE_ROWS"
  exit 4
fi

if [ -s "$PRODUCTION_DIR/mars56_s4p_100k_chunk_run_summary.json" ]; then
  EXISTING_STATUS="$(read_json_field "$PRODUCTION_DIR/mars56_s4p_100k_chunk_run_summary.json" overall_status)"
  if [ "$EXISTING_STATUS" = "PASS" ]; then
    write_status "COMPLETE" "existing first100k production checkpoint already passed"
    exit 0
  fi
fi

RUNNER_COUNT="$( { pgrep -af "run_mars56_s4p_100k_chunk_from_queue.sh.*$PRODUCTION_DIR|$PRODUCTION_DIR.*run_mars56_s4p_100k_chunk_from_queue.sh" || true; } | wc -l | tr -d ' ')"
if [ "$RUNNER_COUNT" -gt 0 ]; then
  write_status "RUNNING" "existing first100k production runner count=$RUNNER_COUNT"
  exit 0
fi

write_status "RUNNING" "first100k EMX launch accepted queue_rows=$QUEUE_ROWS"
exec env PYTHON_BIN="$PY" bash scripts/run_mars56_s4p_100k_chunk_from_queue.sh \
  --candidate-csv "$QUEUE_CSV" \
  --candidate-dir "$ROUND_DIR/queue" \
  --config "$CONFIG" \
  --out-dir "$PRODUCTION_DIR" \
  --chunk-index 1 \
  --count "$QUEUE_COUNT" \
  --min-valid "$QUEUE_COUNT" \
  --jobs "$JOBS" \
  --shard-chunk-size "$SHARD_CHUNK_SIZE" \
  --batch-size 1 \
  --python "$PY"
