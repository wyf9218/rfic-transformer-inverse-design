#!/usr/bin/env bash
set -euo pipefail

# Upload and launch the DRC-gated grounded S4P pilot on MARS.
# This script stores no password. SSH/Duo prompts stay interactive.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
REMOTE="${REMOTE:-researcher@mars.example.edu}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_mars56_s4p_drc_20260705}"
REMOTE_OUT_ROOT="${REMOTE_OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_drc_outputs_20260705}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
TARBALL="${TARBALL:-${ROOT}/mars56_s4p_drc_gated_run20_20260705.tar.gz}"
SHA_FILE="${SHA_FILE:-${TARBALL}.sha256}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-20}"
SSH_BATCH_MODE="${SSH_BATCH_MODE:-0}"
JOBS="${JOBS:-8}"
MAX_COUNT="${MAX_COUNT:-20}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
RUN_BENCHMARK="${RUN_BENCHMARK:-0}"
BENCH_JOBS="${BENCH_JOBS:-1 4 8 16 24}"
BENCH_MAX_COUNT="${BENCH_MAX_COUNT:-20}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"

SSH_ARGS=(-o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)
SCP_ARGS=(-o ConnectTimeout="${SSH_CONNECT_TIMEOUT}" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)

if [[ "${SSH_BATCH_MODE}" == "1" ]]; then
  SSH_ARGS+=(-o BatchMode=yes)
  SCP_ARGS+=(-o BatchMode=yes)
fi

if [[ -n "${SSH_PROXY_JUMP:-}" ]]; then
  SSH_ARGS+=(-J "${SSH_PROXY_JUMP}")
  SCP_ARGS+=(-o "ProxyJump=${SSH_PROXY_JUMP}")
fi

test -f "${TARBALL}"
if [[ -f "${SHA_FILE}" ]]; then
  shasum -a 256 -c "${SHA_FILE}"
fi

echo "CODEX_DRC_S4P_SSH_UPLOAD_START $(date)"
echo "REMOTE=${REMOTE}"
echo "REMOTE_WORK_DIR=${REMOTE_WORK_DIR}"
echo "REMOTE_PROJECT=${REMOTE_PROJECT}"
echo "REMOTE_OUT_ROOT=${REMOTE_OUT_ROOT}"
echo "JOBS=${JOBS} MAX_COUNT=${MAX_COUNT} RUN_REAL_EMX=${RUN_REAL_EMX}"

ssh "${SSH_ARGS[@]}" "${REMOTE}" "mkdir -p '${REMOTE_WORK_DIR}' '${REMOTE_OUT_ROOT}'"
scp "${SCP_ARGS[@]}" "${TARBALL}" "${REMOTE}:${REMOTE_WORK_DIR}/mars56_s4p_drc_gated_run20_20260705.tar.gz"

REMOTE_LOG="${REMOTE_OUT_ROOT}/mars56_s4p_drc_ssh_runner_${STAMP}.log"
REMOTE_COMMAND=$(
  cat <<REMOTE
set -euo pipefail
cd '${REMOTE_WORK_DIR}'
rm -rf mars56_s4p_drc_gated_run20_20260705
tar -xzf mars56_s4p_drc_gated_run20_20260705.tar.gz
chmod +x mars56_s4p_drc_gated_run20_20260705/MARS56_S4P_DRC_GATED_20_RUN_20260705.sh
chmod +x mars56_s4p_drc_gated_run20_20260705/MARS56_S4P_DRC_BENCHMARK_20260705.sh
cd mars56_s4p_drc_gated_run20_20260705
nohup env PACKET_DIR="\$PWD" PROJECT='${REMOTE_PROJECT}' OUT_ROOT='${REMOTE_OUT_ROOT}' JOBS='${JOBS}' MAX_COUNT='${MAX_COUNT}' RUN_REAL_EMX='${RUN_REAL_EMX}' \
  bash ./MARS56_S4P_DRC_GATED_20_RUN_20260705.sh > '${REMOTE_LOG}' 2>&1 < /dev/null &
echo CODEX_DRC_S4P_REMOTE_PID=\$!
echo CODEX_DRC_S4P_REMOTE_LOG='${REMOTE_LOG}'
if [[ '${RUN_BENCHMARK}' == '1' ]]; then
  BENCH_LOG='${REMOTE_OUT_ROOT}/mars56_s4p_drc_benchmark_${STAMP}.log'
  nohup env PACKET_DIR="\$PWD" PROJECT='${REMOTE_PROJECT}' OUT_ROOT='${REMOTE_OUT_ROOT}' BENCH_JOBS='${BENCH_JOBS}' BENCH_MAX_COUNT='${BENCH_MAX_COUNT}' RUN_SETUP=1 \
    bash ./MARS56_S4P_DRC_BENCHMARK_20260705.sh > "\${BENCH_LOG}" 2>&1 < /dev/null &
  echo CODEX_DRC_S4P_BENCH_REMOTE_PID=\$!
  echo CODEX_DRC_S4P_BENCH_REMOTE_LOG="\${BENCH_LOG}"
fi
REMOTE
)

ssh "${SSH_ARGS[@]}" "${REMOTE}" "${REMOTE_COMMAND}"

echo "CODEX_DRC_S4P_SSH_LAUNCHED"
echo "Monitor:"
echo "  ssh ${SSH_PROXY_JUMP:+-J ${SSH_PROXY_JUMP} }${REMOTE} 'tail -f ${REMOTE_LOG}'"
