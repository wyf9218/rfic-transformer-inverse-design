#!/usr/bin/env bash
set -euo pipefail

# Read-only status check for the MARS 56-point grounded-tap S8P pilot.

REMOTE_HOST="${REMOTE_HOST:-mars.example.edu}"
REMOTE_USER="${REMOTE_USER:-researcher}"
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design}"
RETURNS_DIR="$REMOTE_WORK_DIR/returns"

echo "MARS_56PT_STATUS_CHECK_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
echo "REMOTE_PROJECT=$REMOTE_PROJECT"

ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$REMOTE" /bin/bash <<REMOTE_SCRIPT
set -euo pipefail
REMOTE_WORK_DIR="$REMOTE_WORK_DIR"
REMOTE_PROJECT="$REMOTE_PROJECT"
RETURNS_DIR="$RETURNS_DIR"

echo "HOST=\$(hostname)"
echo "DATE=\$(date)"
echo "PWD=\$(pwd)"
echo "RETURNS_DIR=\$RETURNS_DIR"

if [[ -d "\$RETURNS_DIR" ]]; then
  echo "RETURN_FILES:"
  ls -lh "\$RETURNS_DIR" | sed -n '1,80p'
  echo "LATEST_LINKS:"
  find "\$RETURNS_DIR" -maxdepth 1 \( -name 'next_gen_s8p_56pt_grounded_tap_latest*' -o -name 'mars_s8p_56pt_grounded_tap_20pilot_*.log' \) -print | sort | tail -30
else
  echo "RETURNS_DIR_MISSING"
fi

if [[ -f "\$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_manifest.json" ]]; then
  echo "LATEST_MANIFEST:"
  RETURNS_DIR="\$RETURNS_DIR" python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path(os.environ["RETURNS_DIR"]) / "next_gen_s8p_56pt_grounded_tap_latest_manifest.json"
data = json.loads(path.read_text(encoding="utf-8"))
for key in [
    "status",
    "run_return_code",
    "frequency_start_ghz",
    "frequency_stop_ghz",
    "frequency_step_ghz",
    "frequency_points",
    "touchstone_extension",
    "touchstone_ports",
    "active_rf_pairs",
    "ac_grounded_unused_ports",
    "inventory_file_count",
]:
    print(f"{key}={data.get(key)}")
PY
fi

if [[ -f "\$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_verify_summary.json" ]]; then
  echo "LATEST_VERIFY_SUMMARY:"
  RETURNS_DIR="\$RETURNS_DIR" python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path(os.environ["RETURNS_DIR"]) / "next_gen_s8p_56pt_grounded_tap_latest_verify_summary.json"
data = json.loads(path.read_text(encoding="utf-8"))
print("overall_status=" + str(data.get("overall_status")))
for check in data.get("checks", [])[:20]:
    print(f"{check.get('status')} {check.get('name')}: {check.get('detail')}")
PY
fi
REMOTE_SCRIPT

echo "MARS_56PT_STATUS_CHECK_DONE $(date)"
