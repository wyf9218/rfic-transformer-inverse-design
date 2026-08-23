#!/usr/bin/env bash
set -euo pipefail

# Runtime guard for the local wait-for-SSH starter.
#
# This is local-only. It does not SSH to MARS and does not start/stop remote
# work. It proves that the current local auth waiter has a single active
# writer and that its status JSON contains the interactive SSH recovery
# command needed to resume the 1M campaign after password/Duo.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/logs/mars56_wait_for_ssh_start}"
SCREEN_SESSION="${SCREEN_SESSION:-mars56_wait_for_ssh_start}"
WAIT_STATUS_JSON="${WAIT_STATUS_JSON:-$STATE_DIR/mars56_wait_for_ssh_start_latest_status.json}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$STATE_DIR/mars56_wait_for_ssh_start_latest_detached_status.json}"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-$STATE_DIR/mars56_wait_for_ssh_start_screen_runner.sh}"
WATCHER_SCRIPT="${WATCHER_SCRIPT:-$ROOT_DIR/RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh}"
BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT_DIR/START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh}"
START_SCRIPT="${START_SCRIPT:-$ROOT_DIR/RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh}"

for path in "$WAIT_STATUS_JSON" "$DETACHED_STATUS_JSON" "$RUNNER_SCRIPT" "$WATCHER_SCRIPT" "$BOOTSTRAP_SCRIPT" "$START_SCRIPT"; do
  if [ ! -e "$path" ]; then
    echo "WAIT_FOR_SSH_RUNTIME_UNIQUENESS_STATUS=FAIL missing_path=$path"
    exit 1
  fi
done

python3 - "$ROOT_DIR" "$SCREEN_SESSION" "$WAIT_STATUS_JSON" "$DETACHED_STATUS_JSON" "$RUNNER_SCRIPT" "$WATCHER_SCRIPT" "$BOOTSTRAP_SCRIPT" "$START_SCRIPT" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

(
    root_dir,
    screen_session,
    wait_status_json,
    detached_status_json,
    runner_script,
    watcher_script,
    bootstrap_script,
    start_script,
) = sys.argv[1:]

def fail(message):
    print(f"WAIT_FOR_SSH_RUNTIME_UNIQUENESS_STATUS=FAIL {message}")
    raise SystemExit(1)

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"json_load_error path={path} error={type(exc).__name__}")

screen_output = subprocess.run(
    ["screen", "-ls"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
screen_lines = [
    line.strip()
    for line in screen_output.stdout.splitlines()
    if f".{screen_session}" in line
]
if len(screen_lines) != 1:
    fail(f"screen_session_count={len(screen_lines)} expected=1 screen_session={screen_session!r}")

ps_output = subprocess.check_output(
    ["ps", "-eo", "pid=,ppid=,command="],
    text=True,
    stderr=subprocess.DEVNULL,
)
runner_login_processes = []
watcher_processes = []
orphan_runner_login_processes = []
for raw_line in ps_output.splitlines():
    parts = raw_line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    pid_s, ppid_s, command = parts
    try:
        pid = int(pid_s)
        ppid = int(ppid_s)
    except ValueError:
        continue
    if runner_script in command and "login -pflq" in command:
        runner_login_processes.append({"pid": pid, "ppid": ppid, "command": command})
        if ppid == 1:
            orphan_runner_login_processes.append({"pid": pid, "ppid": ppid, "command": command})
    if watcher_script in command and "bash" in command:
        watcher_processes.append({"pid": pid, "ppid": ppid, "command": command})

if len(runner_login_processes) != 1:
    fail(f"runner_login_process_count={len(runner_login_processes)} expected=1")
if orphan_runner_login_processes:
    fail(
        "orphan_runner_login_processes="
        + ",".join(str(item["pid"]) for item in orphan_runner_login_processes)
    )
if len(watcher_processes) != 1:
    fail(f"watcher_process_count={len(watcher_processes)} expected=1")

wait_status = load_json(wait_status_json)
detached_status = load_json(detached_status_json)

if detached_status.get("state") != "RUNNING":
    fail(f"detached_state={detached_status.get('state')!r} expected='RUNNING'")
if detached_status.get("screen_session") != screen_session:
    fail(f"detached_screen_session={detached_status.get('screen_session')!r} expected={screen_session!r}")
if detached_status.get("watcher") != watcher_script:
    fail("detached_watcher_path_mismatch")
if detached_status.get("start_script") != start_script:
    fail("detached_start_script_path_mismatch")

expected_wait_fields = {
    "interactive_bootstrap_script": bootstrap_script,
    "interactive_bootstrap_command": "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "interactive_bootstrap_dry_run_command": "LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "interactive_bootstrap_success_status": "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION",
    "start_env_policy": "production_explicit_env_on_ssh_ready",
}
for key, expected in expected_wait_fields.items():
    if wait_status.get(key) != expected:
        fail(f"wait_status_field_mismatch key={key} actual={wait_status.get(key)!r} expected={expected!r}")
if wait_status.get("state") == "WAITING_FOR_INTERACTIVE_AUTH":
    action = wait_status.get("recommended_next_action", "")
    if "complete password/Duo" not in action:
        fail("wait_status_missing_password_duo_recommended_action")

print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_screen status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_runner_login status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=no_orphan_runner_login status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=single_watcher_process status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=detached_status_running status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_CASE=wait_status_bootstrap_fields status=PASS")
print("WAIT_FOR_SSH_RUNTIME_UNIQUENESS_STATUS=PASS")
PY
