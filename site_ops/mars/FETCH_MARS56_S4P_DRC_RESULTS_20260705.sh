#!/usr/bin/env bash
set -euo pipefail

# Fetch DRC-gated S4P MARS pilot/benchmark evidence after remote execution.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
REMOTE="${REMOTE:-researcher@mars.example.edu}"
REMOTE_OUT_ROOT="${REMOTE_OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_drc_outputs_20260705}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_mars56_s4p_drc_20260705}"
LOCAL_OUT="${LOCAL_OUT:-${ROOT}/mars56_s4p_drc_return_20260705}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-20}"
SSH_BATCH_MODE="${SSH_BATCH_MODE:-0}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"

SSH_ARGS=(-o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)
SCP_ARGS=(-o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new)

if [[ "${SSH_BATCH_MODE}" == "1" ]]; then
  SSH_ARGS+=(-o BatchMode=yes)
  SCP_ARGS+=(-o BatchMode=yes)
fi

if [[ -n "${SSH_PROXY_JUMP:-}" ]]; then
  SSH_ARGS+=(-J "${SSH_PROXY_JUMP}")
  SCP_ARGS+=(-o "ProxyJump=${SSH_PROXY_JUMP}")
fi

mkdir -p "${LOCAL_OUT}"

REMOTE_BUNDLE="${REMOTE_WORK_DIR}/mars56_s4p_drc_return_${STAMP}.tar.gz"
REMOTE_MANIFEST="${REMOTE_WORK_DIR}/mars56_s4p_drc_return_${STAMP}.manifest.txt"

ssh "${SSH_ARGS[@]}" "${REMOTE}" "set -euo pipefail
mkdir -p '${REMOTE_WORK_DIR}'
cd '${REMOTE_OUT_ROOT}'
find . -type f \\( \\
  -name '*summary*.json' -o \\
  -name '*report*.md' -o \\
  -name '*.csv' -o \\
  -name '*.log' -o \\
  -name '*.sha256' -o \\
  -name '*minimal*.tar.gz' -o \\
  -name '*validation_sample*.tar.gz' \\
\\) | sort > '${REMOTE_MANIFEST}'
tar -czf '${REMOTE_BUNDLE}' -T '${REMOTE_MANIFEST}'
echo CODEX_DRC_S4P_REMOTE_BUNDLE='${REMOTE_BUNDLE}'
echo CODEX_DRC_S4P_REMOTE_MANIFEST='${REMOTE_MANIFEST}'
wc -l '${REMOTE_MANIFEST}'
"

scp "${SCP_ARGS[@]}" "${REMOTE}:${REMOTE_BUNDLE}" "${LOCAL_OUT}/"
scp "${SCP_ARGS[@]}" "${REMOTE}:${REMOTE_MANIFEST}" "${LOCAL_OUT}/"

LOCAL_BUNDLE="${LOCAL_OUT}/$(basename "${REMOTE_BUNDLE}")"
tar -xzf "${LOCAL_BUNDLE}" -C "${LOCAL_OUT}"

echo "CODEX_DRC_S4P_FETCH_DONE"
echo "LOCAL_OUT=${LOCAL_OUT}"
echo "LOCAL_BUNDLE=${LOCAL_BUNDLE}"

ESTIMATOR="${ROOT}/rfic-transformer-inverse-design/scripts/estimate_mars_s4p_runtime_from_parallel_summaries.py"
if [[ -f "${ESTIMATOR}" ]]; then
  mapfile -t SUMMARIES < <(find "${LOCAL_OUT}" -type f -name 'parallel_candidate_queue_dataset_summary.json' | sort)
  if [[ "${#SUMMARIES[@]}" -gt 0 ]]; then
    "${ROOT}/rfic-transformer-inverse-design/.venv/bin/python" "${ESTIMATOR}" \
      "${SUMMARIES[@]}" \
      --out-dir "${LOCAL_OUT}/runtime_estimate_from_return" \
      --target-count 1000000 \
      --no-fail-exit || true
  fi
fi
