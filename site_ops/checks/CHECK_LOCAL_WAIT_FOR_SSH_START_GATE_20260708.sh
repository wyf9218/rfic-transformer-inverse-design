#!/usr/bin/env bash
set -euo pipefail

# Local regression guard for the wait-for-SSH then start-1M watcher.
# This must not SSH or start remote production; it uses LOCAL_DRY_RUN only.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
WATCHER="$ROOT_DIR/RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh"
DETACHED="$ROOT_DIR/START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh"

for script in "$WATCHER" "$DETACHED"; do
  if [ ! -f "$script" ]; then
    echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL missing_script=$script"
    exit 1
  fi
  bash -n "$script"
done

for token in \
  "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh" \
  "RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh" \
  "START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh" \
  "PASS_REUSABLE_CONTROL_CONNECTION" \
  "WAITING_FOR_INTERACTIVE_AUTH" \
  "START_SCRIPT" \
  "BOOTSTRAP_SCRIPT" \
  "SSH_WAIT_STATUS=STARTED_DRY_RUN" \
  "SSH_WAIT_STATUS=REQUESTED_ITERATIONS_DONE" \
  "WAIT_STATUS_JSON" \
  "interactive_bootstrap_script" \
  "interactive_bootstrap_command" \
  "interactive_bootstrap_dry_run_command" \
  "interactive_bootstrap_success_status" \
  "recommended_next_action" \
  "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION" \
  "start_env_policy" \
  "production_explicit_env_on_ssh_ready" \
  "LOCAL_DRY_RUN=0" \
  "RUN_LAUNCH_AUDIT=1" \
  "POST_DUO_LOG_CAPTURE=1" \
  "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0" \
  "MARS56 wait-for-SSH then start 1M watcher"
do
  if ! grep -Fq "$token" "$WATCHER"; then
    echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL missing_watcher_token=$token"
    exit 1
  fi
done

for token in \
  "WAIT_FOR_SSH_DETACHED_STATUS=STARTED" \
  "START_SCRIPT" \
  "watcher_sha256" \
  "start_script_sha256" \
  "RESTART_ON_CONFIG_MISMATCH" \
  "CLEANUP_STALE_WAITERS" \
  "cleanup_stale_orphan_waiters" \
  "WAIT_FOR_SSH_STALE_WAITER_CLEANUP" \
  "SIGTERM_ORPHAN_LOGIN_PIDS" \
  "config_matches_requested" \
  "WAIT_FOR_SSH_DETACHED_STATUS=CONFIG_MISMATCH" \
  "WAIT_FOR_SSH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH" \
  "WAIT_FOR_SSH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0" \
  "WAIT_LOG_CAPTURE=0" \
  "WAIT_STATUS_JSON=" \
  "< /dev/null" \
  "DRY_RUN_PROBE_STATUSES"
do
  if ! grep -Fq "$token" "$DETACHED"; then
    echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL missing_detached_token=$token"
    exit 1
  fi
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

require_token() {
  local file="$1"
  local token="$2"
  if ! grep -Fq "$token" "$file"; then
    echo "missing_token=$token"
    echo "output_file=$file"
    sed -n '1,260p' "$file"
    return 1
  fi
}

run_case() {
  local name="$1"
  shift
  local out="$TMP_DIR/${name}.log"
  set +e
  "$@" >"$out" 2>&1
  local rc=$?
  set -e
  echo "WAIT_FOR_SSH_START_GATE_CASE=$name rc=$rc output=$out"
  return "$rc"
}

START_JSON="$TMP_DIR/start_case/status.json"
mkdir -p "$(dirname "$START_JSON")"
if ! run_case start_after_dry_probe env \
  LOCAL_DRY_RUN=1 \
  WAIT_LOG_CAPTURE=0 \
  WAIT_STATUS_JSON="$START_JSON" \
  WAIT_ITERATIONS=2 \
  SLEEP_SECONDS=0 \
  START_ON_PASS=1 \
  DRY_RUN_PROBE_STATUSES=WAITING_FOR_INTERACTIVE_AUTH,PASS_REUSABLE_CONTROL_CONNECTION \
  bash "$WATCHER"
then
  echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL start_after_dry_probe"
  sed -n '1,260p' "$TMP_DIR/start_after_dry_probe.log"
  exit 1
fi
for token in \
  "SSH_WAIT_STATUS=WAITING_FOR_INTERACTIVE_AUTH" \
  "LOCAL_DRY_RUN probe_status=PASS_REUSABLE_CONTROL_CONNECTION" \
  "LOCAL_DRY_RUN start_command=" \
  "LOCAL_DRY_RUN=0" \
  "RUN_LAUNCH_AUDIT=1" \
  "POST_DUO_LOG_CAPTURE=1" \
  "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0" \
  "SSH_WAIT_STATUS=STARTED_DRY_RUN"
do
  require_token "$TMP_DIR/start_after_dry_probe.log" "$token"
done
echo "WAIT_FOR_SSH_START_GATE_CASE=production_start_env_tokens status=PASS LOCAL_DRY_RUN=0 RUN_LAUNCH_AUDIT=1 POST_DUO_LOG_CAPTURE=1 LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0"
python3 - "$START_JSON" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["state"] == "STARTED_DRY_RUN", d
assert d["iteration"] == 2, d
assert d["probe_status"] == "PASS_REUSABLE_CONTROL_CONNECTION", d
assert d["start_return_code"] == 0, d
assert d["local_dry_run"] is True, d
assert d["start_on_pass"] is True, d
assert d["start_env_policy"] == "dry_run_no_remote_start", d
assert d["interactive_bootstrap_script"].endswith("START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh"), d
assert d["interactive_bootstrap_command"] == "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh", d
assert d["interactive_bootstrap_dry_run_command"] == "LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh", d
assert d["interactive_bootstrap_success_status"] == "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION", d
PY

WAIT_JSON="$TMP_DIR/wait_case/status.json"
mkdir -p "$(dirname "$WAIT_JSON")"
if run_case wait_iterations_done env \
  LOCAL_DRY_RUN=1 \
  WAIT_LOG_CAPTURE=0 \
  WAIT_STATUS_JSON="$WAIT_JSON" \
  WAIT_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  START_ON_PASS=1 \
  DRY_RUN_PROBE_STATUSES=WAITING_FOR_INTERACTIVE_AUTH \
  bash "$WATCHER"
then
  echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL wait_iterations_done_unexpected_success"
  sed -n '1,260p' "$TMP_DIR/wait_iterations_done.log"
  exit 1
fi
require_token "$TMP_DIR/wait_iterations_done.log" "SSH_WAIT_STATUS=REQUESTED_ITERATIONS_DONE"
python3 - "$WAIT_JSON" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["state"] == "REQUESTED_ITERATIONS_DONE", d
assert d["probe_status"] == "WAITING_FOR_INTERACTIVE_AUTH", d
assert d["local_dry_run"] is True, d
assert d["start_env_policy"] == "dry_run_no_remote_start", d
assert d["interactive_bootstrap_script"].endswith("START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh"), d
assert d["interactive_bootstrap_command"] == "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh", d
assert d["interactive_bootstrap_dry_run_command"] == "LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh", d
assert d["interactive_bootstrap_success_status"] == "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION", d
assert "complete password/Duo" in d["recommended_next_action"], d
PY

DETACHED_DIR="$TMP_DIR/detached"
if ! run_case detached_dry_run env \
  ACTION=start \
  STATE_DIR="$DETACHED_DIR" \
  SCREEN_SESSION="mars56_wait_for_ssh_gate_$$" \
  LOCAL_DRY_RUN=1 \
  WAIT_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  START_ON_PASS=0 \
  DRY_RUN_PROBE_STATUSES=PASS_REUSABLE_CONTROL_CONNECTION \
  bash "$DETACHED"
then
  echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL detached_dry_run"
  sed -n '1,260p' "$TMP_DIR/detached_dry_run.log"
  exit 1
fi
for token in \
  "WAIT_FOR_SSH_DETACHED_STATUS=STARTED" \
  "start_script=$ROOT_DIR/RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh" \
  "watcher_sha256=" \
  "start_script_sha256=" \
  "status_json=$DETACHED_DIR/mars56_wait_for_ssh_start_latest_detached_status.json"
do
  require_token "$TMP_DIR/detached_dry_run.log" "$token"
done
python3 - "$DETACHED_DIR/mars56_wait_for_ssh_start_latest_detached_status.json" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["state"] == "STARTED", d
assert d["local_dry_run"] is True, d
assert d["start_on_pass"] is False, d
assert d["wait_iterations"] == 1, d
assert d["start_script"].endswith("RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh"), d
assert d.get("watcher_sha256"), d
assert d.get("start_script_sha256"), d
PY

write_old_wait_state() {
  local state_dir="$1"
  local pid="$2"
  mkdir -p "$state_dir"
  printf '%s\n' "$pid" >"$state_dir/mars56_wait_for_ssh_start_latest.pid"
  python3 - "$state_dir/mars56_wait_for_ssh_start_latest_detached_status.json" "$pid" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
pid = int(sys.argv[2])
data = {
    "updated_utc": "2026-07-08T00:00:00+00:00",
    "state": "STARTED",
    "pid": pid,
    "log": str(path.parent / "old_waiter.log"),
    "watcher": "/home/researcher/Documents/模拟变压器AI反向建模/RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh",
    "wait_iterations": 1,
    "sleep_seconds": 0,
    "local_dry_run": True,
    "start_on_pass": True,
    "stop_on_probe_error": False,
    "launch_method": "nohup",
    "screen_session": "unused_old_waiter",
    "runner_script": str(path.parent / "old_runner.sh")
}
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
}

OLD_FAIL_DIR="$TMP_DIR/wait_mismatch_fail"
sleep 60 &
OLD_FAIL_PID="$!"
write_old_wait_state "$OLD_FAIL_DIR" "$OLD_FAIL_PID"
set +e
ACTION=start \
LAUNCH_METHOD=nohup \
STATE_DIR="$OLD_FAIL_DIR" \
LOCAL_DRY_RUN=1 \
WAIT_ITERATIONS=1 \
SLEEP_SECONDS=0 \
START_ON_PASS=1 \
RESTART_ON_CONFIG_MISMATCH=0 \
bash "$DETACHED" >"$TMP_DIR/wait_config_mismatch_no_restart.log" 2>&1
NO_RESTART_RC=$?
set -e
kill "$OLD_FAIL_PID" >/dev/null 2>&1 || true
if [ "$NO_RESTART_RC" -eq 0 ]; then
  echo "WAIT_FOR_SSH_START_GATE_CASE=wait_config_mismatch_no_restart status=FAIL expected_nonzero"
  sed -n '1,260p' "$TMP_DIR/wait_config_mismatch_no_restart.log"
  exit 1
fi
for token in \
  "WAIT_FOR_SSH_DETACHED_STATUS=CONFIG_MISMATCH" \
  "start_script_sha256:expected=" \
  "actual=None" \
  "WAIT_FOR_SSH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
do
  require_token "$TMP_DIR/wait_config_mismatch_no_restart.log" "$token"
done
echo "WAIT_FOR_SSH_START_GATE_CASE=wait_config_mismatch_no_restart status=PASS"

OLD_RESTART_DIR="$TMP_DIR/wait_mismatch_restart"
sleep 60 &
OLD_RESTART_PID="$!"
write_old_wait_state "$OLD_RESTART_DIR" "$OLD_RESTART_PID"
if ! run_case wait_config_mismatch_restart env \
  ACTION=start \
  LAUNCH_METHOD=nohup \
  STATE_DIR="$OLD_RESTART_DIR" \
  LOCAL_DRY_RUN=1 \
  WAIT_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  START_ON_PASS=1 \
  DRY_RUN_PROBE_STATUSES=WAITING_FOR_INTERACTIVE_AUTH \
  bash "$DETACHED"
then
  echo "WAIT_FOR_SSH_START_GATE_STATUS=FAIL wait_config_mismatch_restart"
  sed -n '1,260p' "$TMP_DIR/wait_config_mismatch_restart.log"
  exit 1
fi
for token in \
  "WAIT_FOR_SSH_DETACHED_STATUS=CONFIG_MISMATCH" \
  "WAIT_FOR_SSH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH" \
  "WAIT_FOR_SSH_DETACHED_STATUS=STARTED"
do
  require_token "$TMP_DIR/wait_config_mismatch_restart.log" "$token"
done
if kill -0 "$OLD_RESTART_PID" >/dev/null 2>&1; then
  echo "WAIT_FOR_SSH_START_GATE_CASE=wait_config_mismatch_restart status=FAIL old_pid_still_alive"
  exit 1
fi
python3 - "$OLD_RESTART_DIR/mars56_wait_for_ssh_start_latest_detached_status.json" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["state"] == "STARTED", d
assert d.get("watcher_sha256"), d
assert d.get("start_script_sha256"), d
assert d["restart_on_config_mismatch"] is True, d
PY

echo "WAIT_FOR_SSH_START_GATE_STATUS=PASS"
