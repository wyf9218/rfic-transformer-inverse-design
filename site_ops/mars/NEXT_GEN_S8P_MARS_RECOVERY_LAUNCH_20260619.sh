#!/usr/bin/env bash
set -euo pipefail

# Run this on MARS after the Guacamole terminal or CAE SSH session is usable.
# It verifies the previously transferred sync packet, installs it into the
# remote rfic-transformer-inverse-design repo, runs the strict dry-run gates,
# and then starts the approved 500-sample S8P EMX run by default.
#
# Override RUN_REAL_EMX=0 to stop after dry-run/preflight.

WORK_DIR="${WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
TAR_NAME="${TAR_NAME:-next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz}"
PACKET_DIR="${PACKET_DIR:-next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix}"
EXPECTED_SHA="${EXPECTED_SHA:-}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-1}"

mkdir -p "${WORK_DIR}/logs"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${WORK_DIR}/logs/recovery_launch_${STAMP}.log"
STATUS="${WORK_DIR}/logs/latest_status.txt"
JSON_STATUS="${WORK_DIR}/logs/latest_status.json"

write_status() {
  local state="$1"
  local message="$2"
  printf '%s\n' "${state}: ${message}" | tee "${STATUS}"
  python3 - "$JSON_STATUS" "$state" "$message" "$LOG" "$WORK_DIR" <<'PY' || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, state, message, log, work_dir = sys.argv[1:]
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "state": state,
    "message": message,
    "log": log,
    "work_dir": work_dir,
}
Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
  if command -v xclip >/dev/null 2>&1; then xclip -selection clipboard < "${STATUS}" || true; fi
  if command -v xsel >/dev/null 2>&1; then xsel -ib < "${STATUS}" || true; fi
  if command -v wl-copy >/dev/null 2>&1; then wl-copy < "${STATUS}" || true; fi
}

{
  set -x
  write_status STARTED "MARS S8P recovery launch started"
  cd "${WORK_DIR}"

  stty echo || true

  if [[ ! -s "${TAR_NAME}" && -s "${TAR_NAME}.b64" ]]; then
    write_status DECODING "Decoding ${TAR_NAME}.b64"
    base64 -d "${TAR_NAME}.b64" > "${TAR_NAME}"
  fi

  test -s "${TAR_NAME}"
  if [[ -n "${EXPECTED_SHA}" ]]; then
    printf '%s  %s\n' "${EXPECTED_SHA}" "${TAR_NAME}" | sha256sum -c -
  elif [[ -s "${TAR_NAME}.sha256" ]]; then
    sha256sum -c "${TAR_NAME}.sha256"
  else
    echo "ERROR: missing ${TAR_NAME}.sha256. Transfer the .sha256 sidecar or set EXPECTED_SHA before running." >&2
    exit 2
  fi
  wc -c "${TAR_NAME}"
  write_status SHA_PASS "Sync packet SHA256 verified"

  rm -rf "${PACKET_DIR}"
  tar -xzf "${TAR_NAME}"
  test -x "${PACKET_DIR}/INSTALL_ON_MARS.sh"

  INSTALL_LOG="${WORK_DIR}/logs/install_${STAMP}.log"
  bash "${PACKET_DIR}/INSTALL_ON_MARS.sh" 2>&1 | tee "${INSTALL_LOG}"
  PROJECT_DIR="$(sed -n 's/^NEXT_GEN_S8P_SYNC_PROJECT=//p' "${INSTALL_LOG}" | tail -n 1)"
  if [[ -z "${PROJECT_DIR}" || ! -d "${PROJECT_DIR}" ]]; then
    PROJECT_DIR="$(find /shared/research/researcher -maxdepth 6 -type d -name rfic-transformer-inverse-design 2>/dev/null | head -n 1 || true)"
  fi
  test -n "${PROJECT_DIR}"
  cd "${PROJECT_DIR}"
  write_status INSTALLED "Installed sync packet into ${PROJECT_DIR}"

  RUNBOOK="next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/next_gen_s8p_mars_execution.commands.sh"
  test -x "${RUNBOOK}"

  TSMC65_RUNNER="${PROJECT_DIR}/NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh"
  if [[ ! -x "${TSMC65_RUNNER}" ]]; then
    echo "ERROR: missing 2026-06-20 TSMC65 runner: ${TSMC65_RUNNER}" >&2
    echo "This recovery script no longer runs the generic runbook directly, because /cae/apps is not a valid Cadence IC root." >&2
    exit 2
  fi

  write_status TSMC65_RUNNER_START "Delegating dry-run/real EMX to ${TSMC65_RUNNER}"
  PROJECT_DIR="${PROJECT_DIR}" WORK_DIR="${WORK_DIR}" RUN_REAL_EMX="${RUN_REAL_EMX}" AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS}" bash "${TSMC65_RUNNER}"
  write_status TSMC65_RUNNER_DONE "Inspect ${WORK_DIR}/logs/tsmc65_*"
} >> "${LOG}" 2>&1
