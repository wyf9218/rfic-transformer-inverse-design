#!/usr/bin/env bash
set -euo pipefail

# Local regression guard for the production-rate / ETA audit artifacts.
# It must not SSH; it verifies the dry-run contract is written to JSON/Markdown.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPT="$ROOT_DIR/CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh"

if [ ! -f "$SCRIPT" ]; then
  echo "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_STATUS=FAIL missing_script=$SCRIPT"
  exit 1
fi
bash -n "$SCRIPT"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

OUT_JSON="$TMP_DIR/rate.json"
OUT_MD="$TMP_DIR/rate.md"
OUT_LOG="$TMP_DIR/rate.log"

if ! OUT_JSON="$OUT_JSON" OUT_MD="$OUT_MD" LOCAL_DRY_RUN=1 bash "$SCRIPT" >"$OUT_LOG" 2>&1; then
  echo "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_CASE=dry_run_artifacts status=FAIL"
  sed -n '1,240p' "$OUT_LOG"
  exit 1
fi

python3 - "$OUT_JSON" "$OUT_MD" "$OUT_LOG" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
md_path = Path(sys.argv[2])
log_path = Path(sys.argv[3])

data = json.loads(json_path.read_text(encoding="utf-8"))
md = md_path.read_text(encoding="utf-8")
log = log_path.read_text(encoding="utf-8")

contract = data["contract"]
assert data["audit_mode"] == "LOCAL_DRY_RUN", data
assert contract["expected_parallel_jobs"] == 48, contract
assert contract["target_rows_per_checkpoint"] == 100000, contract
assert contract["target_total_rows"] == 1000000, contract
assert contract["target_seconds_per_accepted_row"] == 4.0, contract
assert contract["target_days_per_100k"] == 5.0, contract
assert contract["max_seconds_per_accepted_row_gate"] == 4.5, contract
assert contract["max_days_per_100k_gate"] == 5.5, contract
assert data["interpreted"]["production_rate_audit_status"] == "DRY_RUN_CONTRACT_ONLY", data
assert data["parsed_metrics"]["remote_audit_contains"] == "active_100k_runner_processes", data
assert data["parsed_metrics"]["remote_artifact_sync_contains"] == "mars56_production_rate_eta_latest.json", data
assert "PRODUCTION_RATE_AUDIT_JSON=" in log, log
assert "PRODUCTION_RATE_AUDIT_MD=" in log, log
assert "remote_artifact_sync_target=" in log, log
assert "Expected parallel jobs" in md, md
assert "Boundary" in md, md
PY

echo "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_CASE=dry_run_artifacts status=PASS"
echo "PRODUCTION_RATE_ARTIFACT_BEHAVIOR_STATUS=PASS"
