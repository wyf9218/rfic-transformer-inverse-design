#!/usr/bin/env bash
set -euo pipefail

# Current one-step MARS launcher for the next-gen S8P 500-row EMX run.
# Run this on MARS from the directory containing the current sync packet tarball
# and its .sha256 sidecar. It verifies the packet, installs it, then starts the
# approved TSMC65 runner. It does not claim any EMX/HFSS result exists.

WORK_DIR="${WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
PACKET_NAME="${PACKET_NAME:-next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz}"
PACKET_ROOT="${PACKET_ROOT:-next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix}"
PROJECT_DIR="${PROJECT_DIR:-${PROJECT:-/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design}}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-0}"

ORIGIN_DIR="$(pwd -P)"
mkdir -p "${WORK_DIR}/logs"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${WORK_DIR}/logs/start_current_${STAMP}.log"

locate_packet() {
  local root
  for root in "${ORIGIN_DIR}" "${WORK_DIR}" "${HOME:-}"; do
    if [[ -n "${root}" && -s "${root}/${PACKET_NAME}" ]]; then
      printf '%s\n' "${root}/${PACKET_NAME}"
      return 0
    fi
  done
  return 1
}

{
  echo "START_CURRENT_NEXT_GEN_S8P $(date)"
  echo "WORK_DIR=${WORK_DIR}"
  echo "PACKET_NAME=${PACKET_NAME}"
  echo "PACKET_ROOT=${PACKET_ROOT}"
  echo "PROJECT_DIR=${PROJECT_DIR}"
  echo "RUN_REAL_EMX=${RUN_REAL_EMX}"
  echo "AUTO_INSTALL_PY_DEPS=${AUTO_INSTALL_PY_DEPS}"

  PACKET_PATH="$(locate_packet)" || {
    echo "ERROR: cannot find ${PACKET_NAME} in ${ORIGIN_DIR}, ${WORK_DIR}, or HOME." >&2
    exit 2
  }
  SHA_PATH="${PACKET_PATH}.sha256"
  if [[ ! -s "${SHA_PATH}" ]]; then
    echo "ERROR: missing ${SHA_PATH}. Upload the .sha256 sidecar beside the tarball." >&2
    exit 2
  fi
  echo "PACKET_PATH=${PACKET_PATH}"
  echo "SHA_PATH=${SHA_PATH}"

  cd "${WORK_DIR}"
  if [[ "${PACKET_PATH}" != "${WORK_DIR}/${PACKET_NAME}" ]]; then
    cp -f "${PACKET_PATH}" "${WORK_DIR}/${PACKET_NAME}"
    cp -f "${SHA_PATH}" "${WORK_DIR}/${PACKET_NAME}.sha256"
  fi
  test -s "${PACKET_NAME}"
  test -s "${PACKET_NAME}.sha256"
  sha256sum -c "${PACKET_NAME}.sha256"

  rm -rf "${PACKET_ROOT}"
  tar -xzf "${PACKET_NAME}"
  test -x "${PACKET_ROOT}/INSTALL_ON_MARS.sh"

  INSTALL_LOG="${WORK_DIR}/logs/start_current_install_${STAMP}.log"
  PROJECT="${PROJECT_DIR}" bash "${PACKET_ROOT}/INSTALL_ON_MARS.sh" 2>&1 | tee "${INSTALL_LOG}"

  installed_project="$(sed -n 's/^NEXT_GEN_S8P_SYNC_PROJECT=//p' "${INSTALL_LOG}" | tail -n 1)"
  if [[ -n "${installed_project}" ]]; then
    PROJECT_DIR="${installed_project}"
  fi
  test -d "${PROJECT_DIR}"
  cd "${PROJECT_DIR}"
  test -x NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh

  echo "INSTALLED_PROJECT_DIR=${PROJECT_DIR}"
  echo "STARTING_TSMC65_RUNNER"
  RUN_REAL_EMX="${RUN_REAL_EMX}" \
    AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS}" \
    WORK_DIR="${WORK_DIR}" \
    bash NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh

  echo "START_CURRENT_NEXT_GEN_S8P_DONE $(date)"
} 2>&1 | tee "${LOG}"

echo "START_CURRENT_LOG=${LOG}"
