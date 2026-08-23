#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/researcher/Documents/模拟变压器AI反向建模"
REPO_ROOT="${PROJECT_ROOT}/rfic-transformer-inverse-design"
PYTHON="${REPO_ROOT}/.venv/bin/python"
RUN_ROOT="${PROJECT_ROOT}/outputs/hfss_v65_export_to_million_gate_watch_current"
LOG_PATH="${RUN_ROOT}/watch_v65_hfss_export_to_million_gate.log"

TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-28800}"
POLL_SECONDS="${POLL_SECONDS:-120}"
MAX_PERCENT_ERROR="${MAX_PERCENT_ERROR:-10}"
ALLOW_REAL_EMX="${ALLOW_REAL_EMX:-0}"

mkdir -p "${RUN_ROOT}"
cd "${REPO_ROOT}"

{
  echo "== V65 HFSS export -> EMX/HFSS validation -> million gate watcher =="
  date -u "+utc=%Y-%m-%dT%H:%M:%SZ"
  echo "project_root=${PROJECT_ROOT}"
  echo "repo_root=${REPO_ROOT}"
  echo "timeout_seconds=${TIMEOUT_SECONDS}"
  echo "poll_seconds=${POLL_SECONDS}"
  echo "max_percent_error=${MAX_PERCENT_ERROR}"
  echo "allow_real_emx=${ALLOW_REAL_EMX}"
  echo

  echo "[1/2] Audit V65 HFSS execution handoff and any returned .s8p spec"
  "${PYTHON}" scripts/audit_hfss_v65_execution_handoff.py --no-fail-exit
  echo

  echo "[2/2] Watch diagnostic/full HFSS return gates"
  WATCH_ARGS=(
    scripts/watch_hfss_v65_diagnostic_to_million_gate.py
    --timeout-seconds "${TIMEOUT_SECONDS}"
    --poll-seconds "${POLL_SECONDS}"
    --max-percent-error "${MAX_PERCENT_ERROR}"
    --no-fail-exit
  )
  if [[ "${ALLOW_REAL_EMX}" == "1" ]]; then
    WATCH_ARGS+=(--allow-real-emx)
  fi
  "${PYTHON}" "${WATCH_ARGS[@]}"
  echo

  echo "== Final summaries =="
  "${PYTHON}" - <<'PY'
import json
from pathlib import Path

project = Path("/home/researcher/Documents/模拟变压器AI反向建模")
paths = [
    project / "outputs/hfss_v65_execution_handoff_audit_current/hfss_v65_execution_handoff_audit_summary.json",
    project / "outputs/hfss_v65_diagnostic_to_million_gate_watch_current/hfss_v65_diagnostic_to_million_gate_watch_summary.json",
    project / "outputs/hfss_lp_ls_full_sweep_promotion_current/hfss_lp_ls_full_sweep_promotion_summary.json",
    project / "outputs/s8p_million_sample_campaign_plan_current/s8p_million_sample_campaign_plan_summary.json",
]
for path in paths:
    print(f"-- {path}")
    if not path.is_file():
        print("missing")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = [
        "overall_status",
        "decision",
        "hfss_result_status",
        "exported_s8p_count",
        "exported_s8p_audit_count",
        "attempt_count",
        "chunk_count",
    ]
    print(json.dumps({key: data.get(key) for key in keys if key in data}, indent=2, ensure_ascii=False))
PY
} 2>&1 | tee -a "${LOG_PATH}"

echo "log=${LOG_PATH}"
