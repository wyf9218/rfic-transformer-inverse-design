#!/usr/bin/env bash
# Execution-only smoke test for the first-100k inverse-training pipeline.
# Its 1k raw rows are not an accepted scientific result.

set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
PY="${PY:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
ROOT="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher"
TRAINING_CSV="$ROOT/milestone_1000/new_training_table/physical_feature_inverse_training_table.csv"
OUT_DIR="$ROOT/pipeline_smoke_not_scientific"

INPUT_COLUMNS="input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center"
GEOMETRY_COLUMNS="geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um"

mkdir -p "$OUT_DIR"

nice -n 19 "$PY" "$PROJECT/scripts/plan_physical_feature_inverse_nn_architecture_search.py" \
  --training-csv "$TRAINING_CSV" \
  --out-dir "$OUT_DIR/plan" \
  --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" \
  --min-training-rows 1000 \
  --seeds 20260709 \
  --hidden-widths 64 \
  --depths 2 \
  --dropouts 0 \
  --learning-rates 0.001 \
  --weight-decays 0 \
  --batch-sizes 256 \
  --max-candidates 1

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 19 \
  "$PY" "$PROJECT/scripts/train_physical_feature_inverse_nn_architecture_search.py" \
  --training-csv "$TRAINING_CSV" \
  --candidate-csv "$OUT_DIR/plan/physical_feature_inverse_nn_architecture_candidates.csv" \
  --out-dir "$OUT_DIR/training" \
  --input-columns "$INPUT_COLUMNS" \
  --geometry-columns "$GEOMETRY_COLUMNS" \
  --min-training-rows 1000 \
  --max-candidates 1 \
  --max-epochs-cap 3 \
  --patience-cap 2 \
  --resume-completed-candidates \
  --no-fail-exit

cat "$OUT_DIR/training/physical_feature_inverse_nn_architecture_search_training_summary.json"
