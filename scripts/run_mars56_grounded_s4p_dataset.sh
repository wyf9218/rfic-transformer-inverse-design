#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CONFIG="${CONFIG:-configs/mars_s4p_grounded_powerline_physical_feature_500_template.yaml}"
RUN_TAG="${RUN_TAG:-mars56_grounded_s4p_$(date +%Y%m%d_%H%M%S)}"
OUT_ROOT="${OUT_ROOT:-${REPO_ROOT}/outputs}"
AUDIT_OUT="${AUDIT_OUT:-${OUT_ROOT}/${RUN_TAG}_objective_audit}"
RUN_DIR="${RUN_DIR:-${OUT_ROOT}/${RUN_TAG}_dataset}"
JOBS="${JOBS:-8}"
MAX_COUNT="${MAX_COUNT:-20}"
CREATE_ONLY="${CREATE_ONLY:-0}"
ONLY_AUDIT="${ONLY_AUDIT:-0}"

echo "[1/4] S4P objective preflight: ${CONFIG}"
"${PYTHON_BIN}" scripts/audit_mars56_grounded_s4p_objective.py \
  --config "${CONFIG}" \
  --out-dir "${AUDIT_OUT}" \
  --export-smoke

if [[ "${ONLY_AUDIT}" == "1" ]]; then
  echo "ONLY_AUDIT=1; stopping after S4P objective preflight."
  echo "Audit output: ${AUDIT_OUT}"
  exit 0
fi

if grep -Eq '/REPLACE/WITH/REAL|REPLACE_WITH_REAL' "${CONFIG}"; then
  echo "ERROR: ${CONFIG} still contains placeholder EMX/Cadence/PDK paths." >&2
  echo "On MARS, first run scripts/discover_mars_emx_cadence_paths.py and scripts/patch_mars_config_paths.py to create a patched config, then set CONFIG=/path/to/patched.yaml." >&2
  exit 2
fi

if [[ -z "${CANDIDATE_CSV:-}" ]]; then
  QUEUE_DIR="${QUEUE_DIR:-${OUT_ROOT}/${RUN_TAG}_candidate_queue}"
  echo "[2/4] No CANDIDATE_CSV set; building geometry-only S4P queue"
  "${PYTHON_BIN}" scripts/build_mars56_grounded_s4p_candidate_queue.py \
    --config "${CONFIG}" \
    --out-dir "${QUEUE_DIR}" \
    --count "${MAX_COUNT}" \
    --expected-count "${MAX_COUNT}"
  CANDIDATE_CSV="${QUEUE_DIR}/mars56_grounded_s4p_candidate_queue.csv"
fi

RUN_FLAGS=()
if [[ "${CREATE_ONLY}" == "1" ]]; then
  RUN_FLAGS+=(--create-only)
fi

echo "[3/4] Parallel candidate queue run"
echo "  CANDIDATE_CSV=${CANDIDATE_CSV}"
echo "  RUN_DIR=${RUN_DIR}"
echo "  JOBS=${JOBS}"
echo "  MAX_COUNT=${MAX_COUNT}"
"${PYTHON_BIN}" scripts/run_candidate_queue_dataset_parallel.py \
  --candidate-csv "${CANDIDATE_CSV}" \
  --config "${CONFIG}" \
  --out-dir "${RUN_DIR}" \
  --jobs "${JOBS}" \
  --expected-jobs "${JOBS}" \
  --max-count "${MAX_COUNT}" \
  --expected-count "${MAX_COUNT}" \
  --batch-size 1 \
  --force-wideband-5-60-0p5 \
  --expected-frequency-start-ghz 5 \
  --expected-frequency-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 \
  --expected-touchstone-extension .s4p \
  --expected-ports 4 \
  --max-touchstone-checks "${MAX_COUNT}" \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51 \
  --fail-on-error \
  "${RUN_FLAGS[@]}"

echo "[4/4] Package run evidence"
"${PYTHON_BIN}" scripts/package_mars_dataset_run.py "${RUN_DIR}" \
  --include-layout-previews \
  --include-quality-figures || true

if [[ "${CREATE_ONLY}" != "1" ]]; then
  echo "[post] Package deterministic random validation sample"
  "${PYTHON_BIN}" scripts/package_mars56_grounded_s4p_validation_sample.py "${RUN_DIR}" \
    --out-dir "${RUN_DIR}/validation_sample_for_hfss" \
    --seed "${VALIDATION_SEED:-20260702}" \
    --port-pairs "1,2:3,4" \
    --start-ghz 5 \
    --stop-ghz 60 \
    --step-ghz 0.5 \
    --frequency-points 111 \
    --target-ghz 15
fi

echo "Done"
echo "Audit output: ${AUDIT_OUT}"
echo "Dataset output: ${RUN_DIR}"
echo "Parallel summary: ${RUN_DIR}/parallel_candidate_queue_dataset_summary.json"
