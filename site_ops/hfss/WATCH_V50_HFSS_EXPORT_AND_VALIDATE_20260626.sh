#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/researcher/Documents/模拟变压器AI反向建模"
S8P="$ROOT/reports/s8p_shared_line_width_mars_evidence_20260622/hfss_aedt_candidate_26cb45d70af3cfd0_pdkproc_v50_strict_local_pxxxg_ref_s8p_contract/samples/01_26cb45d70af3cfd0/hfss_solve_export_results/26cb45d70af3cfd0_hfss_export.s8p"
VALIDATE="$ROOT/RUN_V50_HFSS_POSTRUN_VALIDATION_20260626.sh"

TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-21600}"
SLEEP_SECONDS="${SLEEP_SECONDS:-60}"
START_EPOCH="$(date +%s)"

echo "Waiting for HFSS v50 S8P:"
echo "$S8P"
echo "Timeout: ${TIMEOUT_SECONDS}s"

while [[ ! -s "$S8P" ]]; do
  now="$(date +%s)"
  elapsed="$((now - START_EPOCH))"
  if (( elapsed >= TIMEOUT_SECONDS )); then
    echo "Timed out waiting for HFSS .s8p after ${elapsed}s" >&2
    exit 2
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] still waiting (${elapsed}s elapsed)"
  sleep "$SLEEP_SECONDS"
done

echo "Found HFSS S8P. Running validation..."
bash "$VALIDATE"
