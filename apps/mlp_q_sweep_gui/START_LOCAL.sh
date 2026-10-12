#!/usr/bin/env bash
# Local GUI only. Model artifacts are external and must match their contract.
set -euo pipefail
: "${RFIC_Q_SWEEP_MODEL_DIR:?Set RFIC_Q_SWEEP_MODEL_DIR to the private complete model directory.}"
: "${RFIC_Q_SWEEP_OUTPUT_ROOT:?Set RFIC_Q_SWEEP_OUTPUT_ROOT to a private GUI history directory.}"
exec "${RFIC_Q_SWEEP_PYTHON:-python3}" -m rfic_transformer_inverse_design.synthesis.q_sweep_gui \
  --model-dir "$RFIC_Q_SWEEP_MODEL_DIR" \
  --model-profile "${RFIC_Q_SWEEP_MODEL_PROFILE:-deployed100k}" \
  --output-root "$RFIC_Q_SWEEP_OUTPUT_ROOT" \
  --mode proxy --host 127.0.0.1 --port "${RFIC_Q_SWEEP_PORT:-8765}" --open-browser
