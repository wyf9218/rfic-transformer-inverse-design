#!/usr/bin/env bash
set -euo pipefail

# Local behavior gate for START_MARS56_1M_LOCAL_STATUS_REFRESH_DETACHED_20260708.sh.
#
# It uses temporary screen sessions only. The real
# mars56_1m_local_status_refresh session is not touched.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
LAUNCHER="$ROOT_DIR/START_MARS56_1M_LOCAL_STATUS_REFRESH_DETACHED_20260708.sh"
RUN_SCRIPT="$ROOT_DIR/RUN_MARS56_1M_LOCAL_STATUS_REFRESH_20260708.sh"
SUMMARY_SCRIPT="$ROOT_DIR/SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh"

if [ ! -f "$LAUNCHER" ] || [ ! -f "$RUN_SCRIPT" ] || [ ! -f "$SUMMARY_SCRIPT" ]; then
  echo "ERROR: missing launcher, run script, or summary script." >&2
  exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mars56_status_refresh_gate.XXXXXX")"
trap 'cleanup' EXIT

cleanup() {
  for session_file in "$TMP_DIR"/*.session; do
    [ -f "$session_file" ] || continue
    session="$(cat "$session_file" 2>/dev/null || true)"
    if [ -n "$session" ]; then
      screen -S "$session" -X quit >/dev/null 2>&1 || true
    fi
  done
  python3 - "$TMP_DIR" <<'PY' >/dev/null 2>&1 || true
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

tmp_dir = sys.argv[1]
self_pid = os.getpid()
parent_pid = os.getppid()


def matching_pids() -> list[int]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if pid in {self_pid, parent_pid}:
            continue
        if tmp_dir in command:
            pids.append(pid)
    return pids


for sig in (signal.SIGTERM, signal.SIGKILL):
    pids = matching_pids()
    if not pids:
        break
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    time.sleep(0.5)
PY
  rm -rf "$TMP_DIR"
}

run_launcher() {
  local case_name="$1"
  local out="$TMP_DIR/${case_name}.out"
  shift
  set +e
  "$@" >"$out" 2>&1
  local rc=$?
  set -e
  printf '%s' "$rc" >"$TMP_DIR/${case_name}.rc"
  cat "$out"
  return 0
}

assert_contains() {
  local case_name="$1"
  local needle="$2"
  if ! grep -F "$needle" "$TMP_DIR/${case_name}.out" >/dev/null; then
    echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case_name status=FAIL missing=$needle" >&2
    echo "--- output ---" >&2
    cat "$TMP_DIR/${case_name}.out" >&2
    exit 1
  fi
}

assert_rc() {
  local case_name="$1"
  local expected="$2"
  local actual
  actual="$(cat "$TMP_DIR/${case_name}.rc")"
  if [ "$actual" != "$expected" ]; then
    echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case_name status=FAIL expected_rc=$expected actual_rc=$actual" >&2
    cat "$TMP_DIR/${case_name}.out" >&2
    exit 1
  fi
}

run_status_launcher() {
  local case_name="$1"
  local session="$2"
  local dir="$3"
  shift 3
  printf '%s\n' "$session" >"$TMP_DIR/${session}.session"
  run_launcher "$case_name" env \
    SCREEN_SESSION="$session" \
    LOG_DIR="$dir" \
    PID_FILE="$dir/latest.pid" \
    DETACHED_STATUS_JSON="$dir/latest_detached_status.json" \
    RUN_SCRIPT="$RUN_SCRIPT" \
    SUMMARY_SCRIPT="$SUMMARY_SCRIPT" \
    REFRESH_ITERATIONS=0 \
    SLEEP_SECONDS=60 \
    RUN_PROBE_EACH_REFRESH=0 \
    LOCAL_DRY_RUN=1 \
    "$@" \
    bash "$LAUNCHER"
}

case1="start_matching_config"
dir1="$TMP_DIR/$case1"
mkdir -p "$dir1"
session1="mars56_status_refresh_gate_1_$$"
run_status_launcher "$case1" "$session1" "$dir1" ACTION=start
assert_rc "$case1" 0
assert_contains "$case1" "LOCAL_STATUS_REFRESH_DETACHED_STATUS=STARTED"
assert_contains "$case1" "run_script_sha256="
assert_contains "$case1" "summary_script_sha256="
python3 - "$dir1/latest_detached_status.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data.get("run_script_sha256"), data
assert data.get("summary_script_sha256"), data
assert data.get("refresh_iterations") == 0, data
assert data.get("sleep_seconds") == 60, data
assert data.get("run_probe_each_refresh") is False, data
assert data.get("local_dry_run") is True, data
PY
echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case1 status=PASS"

case2="already_running_matching_config"
run_status_launcher "$case2" "$session1" "$dir1" ACTION=start
assert_rc "$case2" 0
assert_contains "$case2" "LOCAL_STATUS_REFRESH_DETACHED_STATUS=ALREADY_RUNNING"
assert_contains "$case2" "config_status=CONFIG_MATCH"
echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case2 status=PASS"

case3="config_mismatch_no_restart"
run_status_launcher "$case3" "$session1" "$dir1" ACTION=start SLEEP_SECONDS=61 RESTART_ON_CONFIG_MISMATCH=0
assert_rc "$case3" 1
assert_contains "$case3" "LOCAL_STATUS_REFRESH_DETACHED_STATUS=CONFIG_MISMATCH"
assert_contains "$case3" "LOCAL_STATUS_REFRESH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case3 status=PASS"

case4="config_mismatch_restart"
run_status_launcher "$case4" "$session1" "$dir1" ACTION=start SLEEP_SECONDS=61 RESTART_ON_CONFIG_MISMATCH=1
assert_rc "$case4" 0
assert_contains "$case4" "LOCAL_STATUS_REFRESH_DETACHED_STATUS=CONFIG_MISMATCH"
assert_contains "$case4" "LOCAL_STATUS_REFRESH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH"
assert_contains "$case4" "LOCAL_STATUS_REFRESH_DETACHED_STATUS=STARTED"
python3 - "$dir1/latest_detached_status.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
assert data.get("sleep_seconds") == 61, data
assert data.get("run_script_sha256"), data
assert data.get("summary_script_sha256"), data
PY
echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case4 status=PASS"

case5="summary_contract_refresh"
dir5="$TMP_DIR/$case5"
mkdir -p "$dir5"
run_launcher "$case5" env \
  REFRESH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  RUN_PROBE_EACH_REFRESH=0 \
  LOCAL_DRY_RUN=0 \
  REFRESH_LOG_CAPTURE=0 \
  REFRESH_LOG_DIR="$dir5" \
  REFRESH_STATUS_JSON="$dir5/latest_refresh_status.json" \
  OUT_JSON="$dir5/status.json" \
  OUT_MD="$dir5/status.md" \
  RUN_SCREEN_STATUS=0 \
  SUMMARY_SCRIPT="$SUMMARY_SCRIPT" \
  bash "$RUN_SCRIPT"
assert_rc "$case5" 0
assert_contains "$case5" "LOCAL_STATUS_REFRESH_STATUS=REFRESHED"
assert_contains "$case5" "LOCAL_STATUS_REFRESH_STATUS=REQUESTED_ITERATIONS_DONE"
python3 - "$dir5/status.json" "$dir5/status.md" "$dir5/latest_refresh_status.json" <<'PY'
import json
import sys
from pathlib import Path

status = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
markdown = Path(sys.argv[2]).read_text(encoding="utf-8")
refresh = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

contract = status.get("checkpoint_stack_sync_contract") or {}
fields = contract.get("required_contract_fields") or {}
assert fields.get("target_count_per_bin") == 391, fields
assert fields.get("desired_total_count") == 100000, fields
assert fields.get("four_d_bin_count") == 256, fields
assert contract.get("local_contract_only_status") == "LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE", contract
assert contract.get("required_remote_contract_status") == "REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=PASS", contract
adaptive = status.get("adaptive_targeting_contract") or {}
assert adaptive.get("target_count_per_bin") == 391, adaptive
assert adaptive.get("desired_total_count") == 100000, adaptive
assert adaptive.get("four_d_bin_count") == 256, adaptive
throughput = status.get("throughput_contract") or {}
assert throughput.get("expected_parallel_jobs") == 48, throughput
assert throughput.get("target_seconds_per_accepted_row") == 4.0, throughput
assert throughput.get("target_days_per_100k") == 5.0, throughput
assert throughput.get("max_seconds_per_accepted_row_gate") == 4.5, throughput
assert throughput.get("max_days_per_100k_gate") == 5.5, throughput
assert status.get("screen_status_probe_run") is False, status
assert "目标 `391` 条/bin" in markdown, markdown[:1000]
assert "目标吞吐" in markdown, markdown[:1000]
assert "LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE" in markdown, markdown[:1000]
assert refresh.get("state") == "REQUESTED_ITERATIONS_DONE", refresh
assert refresh.get("summary_json") == "reports/mars56_1m_current_status_latest.json", refresh
PY
echo "STATUS_REFRESH_DETACHED_GATE_CASE=$case5 status=PASS"

echo "STATUS_REFRESH_DETACHED_GATE_STATUS=PASS"
