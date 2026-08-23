#!/usr/bin/env bash
set -euo pipefail

# Benchmark grounded-power-line DRC-gated S4P EMX generation on MARS.
# This script is meant to run after extracting mars56_s4p_drc_gated_run20_20260705.tar.gz.

PACKET_DIR="${PACKET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
OUT_ROOT="${OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_drc_outputs_20260705}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BENCH_TAG="${BENCH_TAG:-mars56_grounded_s4p_drc_benchmark_$(date +%Y%m%d_%H%M%S)}"
BENCH_JOBS="${BENCH_JOBS:-1 4 8 16 24}"
BENCH_MAX_COUNT="${BENCH_MAX_COUNT:-20}"
TARGET_COUNT="${TARGET_COUNT:-1000000}"
RUN_SETUP="${RUN_SETUP:-1}"

PATCHED_CONFIG="${PROJECT}/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml"
QUEUE200="${PROJECT}/outputs/drc_gated_s4p_candidate_queue200_20260705/mars56_grounded_s4p_candidate_queue.csv"
QUEUE20="${PROJECT}/outputs/drc_gated_s4p_candidate_queue20_20260705/mars56_grounded_s4p_candidate_queue.csv"

echo "CODEX_DRC_S4P_BENCH_START $(date)"
echo "PROJECT=${PROJECT}"
echo "OUT_ROOT=${OUT_ROOT}"
echo "BENCH_JOBS=${BENCH_JOBS}"
echo "BENCH_MAX_COUNT=${BENCH_MAX_COUNT}"
echo "TARGET_COUNT=${TARGET_COUNT}"

if [[ "${RUN_SETUP}" == "1" || ! -f "${PATCHED_CONFIG}" ]]; then
  echo "CODEX_DRC_S4P_BENCH_SETUP_START"
  ONLY_AUDIT=1 RUN_REAL_EMX=0 PACKET_DIR="${PACKET_DIR}" PROJECT="${PROJECT}" OUT_ROOT="${OUT_ROOT}" PYTHON_BIN="${PYTHON_BIN}" \
    bash "${PACKET_DIR}/MARS56_S4P_DRC_GATED_20_RUN_20260705.sh"
fi

mkdir -p "$(dirname "${QUEUE200}")" "$(dirname "${QUEUE20}")"
if [[ -f "${PACKET_DIR}/candidate_queue_200/mars56_grounded_s4p_candidate_queue.csv" ]]; then
  cp "${PACKET_DIR}/candidate_queue_200/mars56_grounded_s4p_candidate_queue.csv" "${QUEUE200}"
fi
if [[ -f "${PACKET_DIR}/candidate_queue/mars56_grounded_s4p_candidate_queue.csv" ]]; then
  cp "${PACKET_DIR}/candidate_queue/mars56_grounded_s4p_candidate_queue.csv" "${QUEUE20}"
fi

if [[ -n "${BENCH_CANDIDATE_CSV:-}" ]]; then
  CANDIDATE_CSV="${BENCH_CANDIDATE_CSV}"
elif [[ -f "${QUEUE200}" ]]; then
  CANDIDATE_CSV="${QUEUE200}"
else
  CANDIDATE_CSV="${QUEUE20}"
fi

test -f "${PATCHED_CONFIG}"
test -f "${CANDIDATE_CSV}"

SUMMARY_FILES=()
for jobs in ${BENCH_JOBS}; do
  RUN_DIR="${OUT_ROOT}/${BENCH_TAG}_jobs${jobs}_n${BENCH_MAX_COUNT}"
  LOG="${RUN_DIR}.log"
  mkdir -p "${RUN_DIR}"
  echo "CODEX_DRC_S4P_BENCH_RUN_START jobs=${jobs} n=${BENCH_MAX_COUNT} log=${LOG} $(date)"
  "${PYTHON_BIN}" "${PROJECT}/scripts/run_candidate_queue_dataset_parallel.py" \
    --candidate-csv "${CANDIDATE_CSV}" \
    --config "${PATCHED_CONFIG}" \
    --out-dir "${RUN_DIR}" \
    --jobs "${jobs}" \
    --expected-jobs "${jobs}" \
    --max-count "${BENCH_MAX_COUNT}" \
    --expected-count "${BENCH_MAX_COUNT}" \
    --batch-size 1 \
    --force-wideband-5-60-0p5 \
    --expected-frequency-start-ghz 5 \
    --expected-frequency-stop-ghz 60 \
    --expected-frequency-step-ghz 0.5 \
    --expected-frequency-points 111 \
    --expected-touchstone-extension .s4p \
    --expected-ports 4 \
    --max-touchstone-checks "${BENCH_MAX_COUNT}" \
    --expected-port-mode single_ended_shield_grounded \
    --expected-pin-purpose 51 \
    --fail-on-error 2>&1 | tee "${LOG}"
  SUMMARY="${RUN_DIR}/parallel_candidate_queue_dataset_summary.json"
  SUMMARY_FILES+=("${SUMMARY}")
  echo "CODEX_DRC_S4P_BENCH_RUN_DONE jobs=${jobs} summary=${SUMMARY} $(date)"
done

ESTIMATE_DIR="${OUT_ROOT}/${BENCH_TAG}_runtime_estimate"
"${PYTHON_BIN}" "${PROJECT}/scripts/estimate_mars_s4p_runtime_from_parallel_summaries.py" \
  "${SUMMARY_FILES[@]}" \
  --out-dir "${ESTIMATE_DIR}" \
  --target-count "${TARGET_COUNT}"

echo "CODEX_DRC_S4P_BENCH_DONE $(date)"
echo "CODEX_DRC_S4P_BENCH_ESTIMATE=${ESTIMATE_DIR}/mars_s4p_runtime_estimate_summary.json"
