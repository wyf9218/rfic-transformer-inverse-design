#!/usr/bin/env bash
set -euo pipefail

# Paste/run this in a MARS terminal after uploading:
#   next_gen_s8p_mars_sync_packet_20260616.tar.gz
#
# Default is safe: it installs the synced code and runs the non-EMX dry-run.
# It starts the real 500-sample EMX run only when RUN_REAL_EMX=1 is set.

PACKET_NAME="next_gen_s8p_mars_sync_packet_20260616.tar.gz"
PACKET_SHA256="8c9df90d6bf37aa1ce79197fb17853a1336e22385523cbc711b303b139200668"
PACKET_ROOT="next_gen_s8p_mars_sync_packet_20260616"
RUNBOOK_ROOT="next_gen_s8p_mars_bootstrap_execution_packet_20260616"
RUN_DIR_NAME="new_s8p_physical_feature_emx_500"

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

write_status_digest() {
  local label="$1"
  local python_cmd="$2"
  local digest_root="$PROJECT_DIR/$RUNBOOK_ROOT/post_login_status_${label}"
  mkdir -p "$digest_root"
  if [[ -f "$PROJECT_DIR/scripts/summarize_next_gen_s8p_mars_run.py" ]]; then
    "$python_cmd" "$PROJECT_DIR/scripts/summarize_next_gen_s8p_mars_run.py" \
      --run-dir "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME" \
      --out-dir "$digest_root/mars_run_status" \
      --no-fail-exit || true
  fi
  if [[ -f "$PROJECT_DIR/scripts/audit_next_gen_s8p_goal_readiness.py" ]]; then
    "$python_cmd" "$PROJECT_DIR/scripts/audit_next_gen_s8p_goal_readiness.py" \
      --config "$PROJECT_DIR/$RUNBOOK_ROOT/02_final_s8p_config/final_s8p_physical_feature_500.yaml" \
      --launch-packet-summary "$PROJECT_DIR/$RUNBOOK_ROOT/physical_feature_s8p_launch_packet/physical_feature_s8p_launch_packet_summary.json" \
      --candidate-run-dir "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME" \
      --dataset-quality-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/dataset_quality_gates_summary.json" \
      --port-pair-candidate-audit-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/selected_s8p_port_pair_physical_candidate_audit/s8p_port_pair_physical_candidate_audit_summary.json" \
      --selected-handoff-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_handoff/selected_s8p_hfss_handoff_summary.json" \
      --aedt-packet-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_aedt_scripts/hfss_s8p_aedt_script_packet_summary.json" \
      --hfss-payload-render-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_payload_views/hfss_payload_geometry_render_batch_summary.json" \
      --postrun-validation-summary "$PROJECT_DIR/$RUNBOOK_ROOT/$RUN_DIR_NAME/dataset_quality_gates_s8p_physical_feature/selected_s8p_hfss_postrun_validation/s8p_hfss_postrun_validation_summary.json" \
      --out-dir "$digest_root/goal_readiness" \
      --no-fail-exit || true
  fi
  echo "STATUS_DIGEST_DIR=$digest_root"
}

echo "[1/7] Locate uploaded packet"
PACKET_PATH="${PACKET_PATH:-}"
if [[ -z "$PACKET_PATH" ]]; then
  PACKET_PATH="$(find "$PWD" "$HOME" /tmp /var/tmp -name "$PACKET_NAME" -type f 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "$PACKET_PATH" || ! -f "$PACKET_PATH" ]]; then
  echo "ERROR: could not find $PACKET_NAME" >&2
  echo "Upload it with Guacamole file transfer, then rerun this script." >&2
  exit 2
fi
echo "PACKET_PATH=$PACKET_PATH"

echo "[2/7] Verify packet SHA256"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(sha256sum "$PACKET_PATH" | awk '{print $1}')"
else
  ACTUAL_SHA256="$(shasum -a 256 "$PACKET_PATH" | awk '{print $1}')"
fi
echo "ACTUAL_SHA256=$ACTUAL_SHA256"
test "$ACTUAL_SHA256" = "$PACKET_SHA256"

echo "[3/7] Locate repository"
PROJECT_DIR="${PROJECT:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  for candidate in \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      PROJECT_DIR="$candidate"
      break
    fi
  done
fi
if [[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]]; then
  echo "ERROR: could not locate rfic-transformer-inverse-design." >&2
  echo "Set PROJECT=/path/to/rfic-transformer-inverse-design and rerun." >&2
  exit 3
fi
echo "PROJECT_DIR=$PROJECT_DIR"

echo "[4/7] Extract and install sync packet"
WORK_DIR="${WORK_DIR:-$HOME/codex_next_gen_s8p_sync_20260616}"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
tar -xzf "$PACKET_PATH" -C "$WORK_DIR"
PROJECT="$PROJECT_DIR" bash "$WORK_DIR/$PACKET_ROOT/INSTALL_ON_MARS.sh"

echo "[5/7] Run safe dry-run; this does not start EMX"
cd "$PROJECT_DIR"
PYTHON_CMD="$(pick_python)"
export PYTHON="$PYTHON_CMD"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$PROJECT_DIR/$RUNBOOK_ROOT/post_login_logs_${STAMP}"
mkdir -p "$LOG_DIR"
echo "LOG_DIR=$LOG_DIR"
AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-1}" \
  bash "$RUNBOOK_ROOT/next_gen_s8p_mars_execution.commands.sh" 2>&1 | tee "$LOG_DIR/dry_run.log"
write_status_digest "after_dry_run_${STAMP}" "$PYTHON_CMD" 2>&1 | tee "$LOG_DIR/status_after_dry_run.log"

echo "[6/7] Dry-run finished"
echo "Review generated summaries under:"
echo "  $PROJECT_DIR/$RUNBOOK_ROOT"

echo "[7/7] Optional real EMX launch"
if [[ "${RUN_REAL_EMX:-0}" == "1" ]]; then
  echo "RUN_REAL_EMX=1, starting the guarded 500-sample S8P EMX queue."
  AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-1}" \
  RUN_EMX=1 \
  bash "$RUNBOOK_ROOT/next_gen_s8p_mars_execution.commands.sh" 2>&1 | tee "$LOG_DIR/real_emx_run.log"
  write_status_digest "after_real_emx_${STAMP}" "$PYTHON_CMD" 2>&1 | tee "$LOG_DIR/status_after_real_emx.log"
else
  echo "Real EMX was not started."
  echo "To launch after reviewing dry-run:"
  echo "  cd '$PROJECT_DIR'"
  echo "  RUN_REAL_EMX=1 bash /path/to/NEXT_GEN_S8P_POST_LOGIN_COMMANDS_20260616.sh"
fi
