#!/usr/bin/env bash
set -euo pipefail

# Visible-terminal MARS entry point after uploading:
#   next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz
#   next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz.sha256
#
# This script installs the current approved next-gen S8P packet and then calls
# NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh.  It keeps the sync-packet install
# directory separate from the long-running EMX work directory so reruns do not
# delete logs, progress CSVs, or return packages.
#
# Default behavior starts the approved real 500-row / 8-worker EMX run:
#   bash NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh
#
# Dry-run only:
#   RUN_REAL_EMX=0 bash NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260619.sh

PACKET_NAME="next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix.tar.gz"
EXPECTED_PACKET_SHA256="${EXPECTED_PACKET_SHA256:-${PACKET_SHA256:-}}"
PACKET_ROOT="next_gen_s8p_mars_sync_packet_20260620_touchstone_all500_gate_fix"
RUNBOOK_ROOT="next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready"
RUN_DIR_NAME="new_s8p_physical_feature_emx_500"

RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-0}"
ALLOW_UNVERIFIED_PACKET="${ALLOW_UNVERIFIED_PACKET:-0}"
PROJECT_DIR="${PROJECT_DIR:-${PROJECT:-/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design}}"
RUN_WORK_DIR="${RUN_WORK_DIR:-${WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}}"
SYNC_INSTALL_DIR="${SYNC_INSTALL_DIR:-$HOME/codex_next_gen_s8p_sync_install_20260620_touchstone_all500_gate_fix}"

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    shasum -a 256 "$path" | awk '{print $1}'
  fi
}

abs_dir() {
  local path="$1"
  mkdir -p "$path"
  (cd "$path" && pwd -P)
}

pick_python() {
  if [[ -n "${PYTHON:-}" && -x "${PYTHON}" ]]; then
    printf '%s\n' "${PYTHON}"
  elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$PROJECT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    return 1
  fi
}

locate_packet() {
  if [[ -n "${PACKET_PATH:-}" && -f "${PACKET_PATH}" ]]; then
    printf '%s\n' "${PACKET_PATH}"
    return 0
  fi
  local root found
  for root in "$PWD" "$RUN_WORK_DIR" "$HOME" /tmp /var/tmp; do
    if [[ -d "$root" ]]; then
      found="$(find "$root" -maxdepth 5 -name "$PACKET_NAME" -type f 2>/dev/null | head -n 1 || true)"
      if [[ -n "$found" ]]; then
        printf '%s\n' "$found"
        return 0
      fi
    fi
  done
  return 1
}

locate_project() {
  if [[ -f "${PROJECT_DIR}/pyproject.toml" && -d "${PROJECT_DIR}/rfic_transformer_inverse_design" ]]; then
    printf '%s\n' "${PROJECT_DIR}"
    return 0
  fi
  local candidate found
  for candidate in \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  found="$(find /home/researcher /shared/research/researcher -maxdepth 7 -type d -name rfic-transformer-inverse-design 2>/dev/null | head -n 1 || true)"
  if [[ -n "$found" && -f "$found/pyproject.toml" ]]; then
    printf '%s\n' "$found"
    return 0
  fi
  return 1
}

print_approval_review_evidence() {
  local python_cmd="$1"
  local approval_root="$PROJECT_DIR/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619"
  local summary="$approval_root/s8p_combined_approval_readiness_summary.json"
  local board="$approval_root/s8p_combined_approval_readiness_board.png"

  echo "APPROVAL_REVIEW_ROOT=$approval_root"
  if [[ -f "$summary" ]]; then
    echo "APPROVAL_SUMMARY_JSON=$summary"
    "$python_cmd" - "$summary" <<'PY' || true
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
state = data.get("approval_state") or {}
print(f"APPROVAL_DECISION={data.get('decision')}")
print(f"CAN_START_REAL_EMX={data.get('can_start_real_emx')}")
print(f"PORT_MAP_APPROVED={state.get('port_map_approved')}")
print(f"GEOMETRY_CONTRACT_APPROVED={state.get('geometry_contract_approved')}")
print(f"MARS_EXECUTION_PACKET_READY={state.get('mars_execution_packet_ready')}")
PY
  else
    echo "APPROVAL_SUMMARY_JSON_MISSING=$summary"
  fi
  if [[ -f "$board" ]]; then
    echo "APPROVAL_BOARD_PNG=$board"
  fi
}

write_local_digest_if_possible() {
  local python_cmd="$1"
  local label="$2"
  local digest_root="$RUN_WORK_DIR/post_login_status_${label}"
  local quality_dir="$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature"
  mkdir -p "$digest_root"

  if [[ -f "$PROJECT_DIR/scripts/summarize_next_gen_s8p_mars_run.py" ]]; then
    "$python_cmd" "$PROJECT_DIR/scripts/summarize_next_gen_s8p_mars_run.py" \
      --run-dir "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME" \
      --quality-dir "$quality_dir" \
      --out-dir "$digest_root/mars_run_status" \
      --expected-count 500 \
      --expected-jobs 8 \
      --expected-ports 8 \
      --expected-frequency-start-ghz 5 \
      --expected-frequency-stop-ghz 50 \
      --expected-frequency-step-ghz 0.1 \
      --expected-frequency-points 451 \
      --no-fail-exit || true
  fi

  echo "STATUS_DIGEST_DIR=$digest_root"
}

echo "[0/8] Select run mode"
case "$RUN_REAL_EMX" in
  0|1) ;;
  *)
    echo "ERROR: RUN_REAL_EMX must be 0 or 1, got: $RUN_REAL_EMX" >&2
    exit 4
    ;;
esac
echo "RUN_REAL_EMX=$RUN_REAL_EMX"
echo "AUTO_INSTALL_PY_DEPS=$AUTO_INSTALL_PY_DEPS"
echo "ALLOW_UNVERIFIED_PACKET=$ALLOW_UNVERIFIED_PACKET"

echo "[1/8] Locate uploaded packet"
PACKET_PATH="$(locate_packet)" || {
  echo "ERROR: could not find $PACKET_NAME" >&2
  echo "Upload it with Guacamole file transfer, then rerun this script." >&2
  exit 2
}
echo "PACKET_PATH=$PACKET_PATH"

echo "[2/8] Verify packet SHA256"
ACTUAL_SHA256="$(sha256_file "$PACKET_PATH")"
echo "ACTUAL_SHA256=$ACTUAL_SHA256"
SHA256_SIDECAR="${PACKET_SHA256_FILE:-${PACKET_PATH}.sha256}"
if [[ -z "$EXPECTED_PACKET_SHA256" && -f "$SHA256_SIDECAR" ]]; then
  EXPECTED_PACKET_SHA256="$(awk 'NF {print $1; exit}' "$SHA256_SIDECAR")"
  echo "EXPECTED_SHA256_SOURCE=$SHA256_SIDECAR"
fi
if [[ -z "$EXPECTED_PACKET_SHA256" ]]; then
  if [[ "$ALLOW_UNVERIFIED_PACKET" == "1" ]]; then
    echo "WARNING: no EXPECTED_PACKET_SHA256 or sidecar file found; ALLOW_UNVERIFIED_PACKET=1 so continuing with displayed ACTUAL_SHA256 only." >&2
  else
    echo "ERROR: no EXPECTED_PACKET_SHA256 or sidecar file found." >&2
    echo "Upload ${PACKET_NAME}.sha256 beside the tarball, or set EXPECTED_PACKET_SHA256 explicitly." >&2
    echo "Set ALLOW_UNVERIFIED_PACKET=1 only for manual recovery when you have independently verified the tarball." >&2
    exit 5
  fi
elif [[ "$ACTUAL_SHA256" != "$EXPECTED_PACKET_SHA256" ]]; then
  echo "ERROR: packet SHA256 mismatch." >&2
  echo "EXPECTED_SHA256=$EXPECTED_PACKET_SHA256" >&2
  exit 5
fi

echo "[3/8] Locate repository"
PROJECT_DIR="$(locate_project)" || {
  echo "ERROR: could not locate rfic-transformer-inverse-design." >&2
  echo "Set PROJECT_DIR=/path/to/rfic-transformer-inverse-design and rerun." >&2
  exit 3
}
echo "PROJECT_DIR=$PROJECT_DIR"

RUN_WORK_DIR="$(abs_dir "$RUN_WORK_DIR")"
SYNC_INSTALL_DIR="$(abs_dir "$SYNC_INSTALL_DIR")"
if [[ "$RUN_WORK_DIR" == "$SYNC_INSTALL_DIR" ]]; then
  echo "ERROR: RUN_WORK_DIR and SYNC_INSTALL_DIR resolve to the same path." >&2
  echo "This would risk deleting long-run EMX logs/returns during packet reinstall." >&2
  exit 6
fi
echo "RUN_WORK_DIR=$RUN_WORK_DIR"
echo "SYNC_INSTALL_DIR=$SYNC_INSTALL_DIR"

echo "[4/8] Extract and install sync packet"
rm -rf "$SYNC_INSTALL_DIR"
mkdir -p "$SYNC_INSTALL_DIR"
tar -xzf "$PACKET_PATH" -C "$SYNC_INSTALL_DIR"
cd "$PROJECT_DIR"
PYTHON_CMD="$(pick_python)"
export PYTHON="$PYTHON_CMD"
PROJECT="$PROJECT_DIR" bash "$SYNC_INSTALL_DIR/$PACKET_ROOT/INSTALL_ON_MARS.sh"

echo "[5/8] Print approved contract evidence"
print_approval_review_evidence "$PYTHON_CMD"

echo "[6/8] Launch current TSMC65 runner"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$RUN_WORK_DIR/post_login_logs_${STAMP}"
mkdir -p "$LOG_DIR"
echo "LOG_DIR=$LOG_DIR"

RUNNER="$PROJECT_DIR/NEXT_GEN_S8P_MARS_TSMC65_RUN_20260620.sh"
if [[ ! -f "$RUNNER" ]]; then
  echo "ERROR: missing runner after install: $RUNNER" >&2
  exit 7
fi

set +e
PROJECT_DIR="$PROJECT_DIR" \
  RUN_REAL_EMX="$RUN_REAL_EMX" \
  AUTO_INSTALL_PY_DEPS="$AUTO_INSTALL_PY_DEPS" \
  WORK_DIR="$RUN_WORK_DIR" \
  bash "$RUNNER" 2>&1 | tee "$LOG_DIR/tsmc65_runner.log"
RUNNER_RC="${PIPESTATUS[0]}"
set -e
printf '%s\n' "$RUNNER_RC" > "$LOG_DIR/tsmc65_runner_exit_code.txt"
echo "TSMC65_RUNNER_EXIT_CODE=$RUNNER_RC"

echo "[7/8] Run/read status check"
if [[ -f "$PROJECT_DIR/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh" ]]; then
  WORK_DIR="$RUN_WORK_DIR" bash "$PROJECT_DIR/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh" 2>&1 | tee "$LOG_DIR/status_check.log" || true
else
  echo "STATUS_CHECK_SKIPPED missing=$PROJECT_DIR/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh"
fi
write_local_digest_if_possible "$PYTHON_CMD" "after_tsmc65_runner_${STAMP}" 2>&1 | tee "$LOG_DIR/local_digest.log" || true

echo "[8/8] Done"
if [[ "$RUN_REAL_EMX" == "1" ]]; then
  echo "Requested real EMX launch. If dry preflight passed, the 500-row run is detached under nohup."
  echo "Do not close or log out of MARS unnecessarily; monitor with:"
  echo "  WORK_DIR=$RUN_WORK_DIR bash $PROJECT_DIR/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh"
  echo "  tail -n 160 $RUN_WORK_DIR/logs/tsmc65_real_emx_500_latest.log"
  echo "  tail -n 40 $RUN_WORK_DIR/logs/tsmc65_real_emx_500_latest_progress.csv"
else
  echo "Dry-run mode only; real EMX was not requested."
fi
echo "RUN_WORK_DIR=$RUN_WORK_DIR"
echo "LOG_DIR=$LOG_DIR"

exit "$RUNNER_RC"
