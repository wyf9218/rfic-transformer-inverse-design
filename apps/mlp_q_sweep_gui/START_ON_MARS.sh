#!/usr/bin/env bash
set -euo pipefail

: "${RFIC_Q_SWEEP_MODEL_DIR:?Set RFIC_Q_SWEEP_MODEL_DIR to the private frozen-model directory.}"

MODE="${RFIC_Q_SWEEP_MODE:-proxy}"
MODEL_PROFILE="${RFIC_Q_SWEEP_MODEL_PROFILE:-deployed100k}"
OUTPUT_ROOT="${RFIC_Q_SWEEP_OUTPUT_ROOT:-$PWD/q_sweep_gui_runs}"
HOST="${RFIC_Q_SWEEP_HOST:-127.0.0.1}"
PORT="${RFIC_Q_SWEEP_PORT:-8765}"
PYTHON_BIN="${RFIC_Q_SWEEP_PYTHON:-python3}"

if [[ "$MODE" == "physical" ]]; then
  : "${RFIC_Q_SWEEP_PHYSICAL_BACKEND:?Physical mode requires the private MARS GDS/EMX backend command.}"
elif [[ "$MODE" == "layout" ]]; then
  : "${RFIC_Q_SWEEP_LAYOUT_BACKEND:?Layout mode requires the packaged structured-GDS backend command.}"
fi

exec "$PYTHON_BIN" -m rfic_transformer_inverse_design.synthesis.q_sweep_gui \
  --model-dir "$RFIC_Q_SWEEP_MODEL_DIR" \
  --model-profile "$MODEL_PROFILE" \
  --output-root "$OUTPUT_ROOT" \
  --mode "$MODE" \
  --host "$HOST" \
  --port "$PORT" \
  --open-browser
