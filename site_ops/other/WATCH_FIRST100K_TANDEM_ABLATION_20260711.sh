#!/usr/bin/env bash
# Run the physical-feature tandem ablation after the primary first-100k
# checkpoint completes. This watcher is read-only with respect to EMX output and
# does not modify the audited production queue, process stack, or port contract.

set -u

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
STATUS_ROOT="${STATUS_ROOT:-$BASE/status/first100k_urgent_targeted_20260709}"
CHECKPOINT="${CHECKPOINT:-$STATUS_ROOT/accepted_checkpoint_watcher/first100k_accepted_model_checkpoint}"
PRIMARY_COMPLETE="${PRIMARY_COMPLETE:-$STATUS_ROOT/accepted_checkpoint_watcher/first100k_model_checkpoint.complete}"
TRAINING_CSV="${TRAINING_CSV:-$CHECKPOINT/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv}"
TRAINING_MANIFEST="${TRAINING_MANIFEST:-$CHECKPOINT/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json}"
OUT_DIR="${OUT_DIR:-$CHECKPOINT/physical_feature_tandem_inverse}"
POLL_SECONDS="${POLL_SECONDS:-300}"

INPUT_COLUMNS="input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"
PHYSICAL_CELL_LOWER="0.5,0.5,5.0,0.0"
PHYSICAL_CELL_UPPER="3.0,3.0,25.0,0.8"
GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"
LOCK_DIR="$OUT_DIR/active.lock"
LOG="$OUT_DIR/tandem_ablation_watcher.log"
COMPLETE="$OUT_DIR/tandem_ablation.complete"

mkdir -p "$OUT_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG"
}

manifest_ready() {
  "$PY" - "$TRAINING_MANIFEST" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
raise SystemExit(0 if payload.get("overall_status") == "PASS" and int(payload.get("training_count") or 0) >= 100000 else 1)
PY
}

if [[ -f "$COMPLETE" ]]; then
  exit 0
fi

log "tandem ablation watcher start"
while [[ ! -f "$PRIMARY_COMPLETE" ]]; do
  sleep "$POLL_SECONDS"
done

if [[ ! -f "$TRAINING_CSV" ]] || ! manifest_ready; then
  log "BLOCKED: primary checkpoint marker exists but the real 100k training table contract is incomplete"
  exit 2
fi

log "primary checkpoint complete; starting tandem ablation"
OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8 nice -n 15 \
  "$PY" "$PROJECT/scripts/train_physical_feature_tandem_inverse.py" \
  --training-csv "$TRAINING_CSV" \
  --out-dir "$OUT_DIR" \
  --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" \
  --min-training-rows 100000 \
  --split-mode physical_cell_grouped --physical-cell-bins 4 \
  --physical-cell-lower "$PHYSICAL_CELL_LOWER" \
  --physical-cell-upper "$PHYSICAL_CELL_UPPER" \
  --forward-depth 3 --forward-width 256 \
  --inverse-depth 3 --inverse-width 256 \
  --batch-size 1024 --forward-epochs 160 --inverse-epochs 180 \
  --patience 20 --geometry-anchor-weight 0.01 \
  --local-refinement-steps 40 --local-refinement-starts 4 \
  --local-refinement-learning-rate 0.05 --local-refinement-jitter 0.05 \
  --no-fail-exit >> "$LOG" 2>&1

"$PY" - "$OUT_DIR/physical_feature_tandem_inverse_summary.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
complete_statuses = {"PASS", "FAIL", "COMPLETE_REVIEW_REQUIRED"}
raise SystemExit(0 if payload.get("overall_status") in complete_statuses and int(payload.get("training_count") or 0) >= 100000 else 1)
PY
if [[ $? -ne 0 ]]; then
  log "BLOCKED: tandem ablation did not produce a complete traceable summary"
  exit 3
fi

touch "$COMPLETE"
log "tandem ablation complete; status is recorded in the summary and still requires real EMX verification"
