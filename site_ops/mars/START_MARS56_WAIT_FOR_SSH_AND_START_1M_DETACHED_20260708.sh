#!/usr/bin/env bash
set -euo pipefail

# Detached launcher/status wrapper for the local wait-for-SSH 1M starter.
#
# Start:
#   bash START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh
#
# Status:
#   ACTION=status bash START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh
#
# ACTION=start|status are the supported modes.
#
# In this Codex desktop environment, plain nohup children may be cleaned up
# when the launching command exits. Therefore LAUNCH_METHOD=auto prefers
# screen(1), which creates a real detached local session.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
WATCHER="$ROOT_DIR/RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh"
START_SCRIPT="${START_SCRIPT:-$ROOT_DIR/RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh}"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/logs/mars56_wait_for_ssh_start}"
ACTION="${ACTION:-start}"

PID_FILE="${PID_FILE:-$STATE_DIR/mars56_wait_for_ssh_start_latest.pid}"
DETACHED_LOG="${DETACHED_LOG:-}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$STATE_DIR/mars56_wait_for_ssh_start_latest_detached_status.json}"
WAIT_STATUS_JSON="${WAIT_STATUS_JSON:-$STATE_DIR/mars56_wait_for_ssh_start_latest_status.json}"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-$STATE_DIR/mars56_wait_for_ssh_start_screen_runner.sh}"

WAIT_ITERATIONS="${WAIT_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
START_ON_PASS="${START_ON_PASS:-1}"
STOP_ON_PROBE_ERROR="${STOP_ON_PROBE_ERROR:-0}"
DRY_RUN_PROBE_STATUSES="${DRY_RUN_PROBE_STATUSES:-WAITING_FOR_INTERACTIVE_AUTH,PASS_REUSABLE_CONTROL_CONNECTION}"
LAUNCH_METHOD="${LAUNCH_METHOD:-auto}"
SCREEN_SESSION="${SCREEN_SESSION:-mars56_wait_for_ssh_start}"
RESTART_ON_CONFIG_MISMATCH="${RESTART_ON_CONFIG_MISMATCH:-1}"
CLEANUP_STALE_WAITERS="${CLEANUP_STALE_WAITERS:-1}"

case "$ACTION" in start|status) ;;
  *) echo "ERROR: ACTION must be start or status." >&2; exit 2 ;;
esac
case "$LAUNCH_METHOD" in auto|screen|nohup) ;;
  *) echo "ERROR: LAUNCH_METHOD must be auto, screen, or nohup." >&2; exit 2 ;;
esac
for value in "$WATCHER" "$START_SCRIPT" "$STATE_DIR" "$PID_FILE" "$DETACHED_LOG" "$DETACHED_STATUS_JSON" "$WAIT_STATUS_JSON" "$RUNNER_SCRIPT" "$DRY_RUN_PROBE_STATUSES" "$LAUNCH_METHOD" "$SCREEN_SESSION"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: detached wait/start settings contain unsupported newline characters." >&2
    exit 2
  fi
done
for var_name in LOCAL_DRY_RUN START_ON_PASS STOP_ON_PROBE_ERROR RESTART_ON_CONFIG_MISMATCH CLEANUP_STALE_WAITERS; do
  value="${!var_name}"
  case "$value" in 0|1) ;;
    *) echo "ERROR: $var_name must be 0 or 1." >&2; exit 2 ;;
  esac
done
if ! [[ "$WAIT_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: WAIT_ITERATIONS must be a nonnegative integer; use 0 for forever." >&2
  exit 2
fi
if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: SLEEP_SECONDS must be a nonnegative integer." >&2
  exit 2
fi
if [ ! -f "$WATCHER" ] || [ ! -f "$START_SCRIPT" ]; then
  echo "ERROR: wait/start watcher or post-Duo start script missing." >&2
  echo "watcher=$WATCHER" >&2
  echo "start_script=$START_SCRIPT" >&2
  exit 2
fi

mkdir -p "$STATE_DIR"

screen_available() {
  command -v screen >/dev/null 2>&1
}

resolved_launch_method() {
  if [ "$LAUNCH_METHOD" = "auto" ]; then
    if screen_available; then
      echo screen
    else
      echo nohup
    fi
  else
    echo "$LAUNCH_METHOD"
  fi
}

screen_running() {
  screen_available || return 1
  local screen_list
  screen_list="$(screen -ls 2>/dev/null || true)"
  printf '%s\n' "$screen_list" | grep -F ".${SCREEN_SESSION}" >/dev/null 2>&1
}

is_pid_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

WATCHER_SHA="$(sha256_file "$WATCHER")"
START_SCRIPT_SHA="$(sha256_file "$START_SCRIPT")"

latest_pid=""
if [ -s "$PID_FILE" ]; then
  latest_pid="$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)"
fi

write_detached_status_json() {
  local state="$1"
  local pid="$2"
  local log_path="$3"
  local method
  method="$(resolved_launch_method)"
  python3 - "$DETACHED_STATUS_JSON" "$state" "$pid" "$log_path" "$WATCHER" "$START_SCRIPT" "$WATCHER_SHA" "$START_SCRIPT_SHA" "$WAIT_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$START_ON_PASS" "$STOP_ON_PROBE_ERROR" "$method" "$SCREEN_SESSION" "$RUNNER_SCRIPT" "$RESTART_ON_CONFIG_MISMATCH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    state,
    pid,
    log_path,
    watcher,
    start_script,
    watcher_sha,
    start_script_sha,
    wait_iterations,
    sleep_seconds,
    local_dry_run,
    start_on_pass,
    stop_on_probe_error,
    launch_method,
    screen_session,
    runner_script,
    restart_on_config_mismatch,
) = sys.argv[1:]

path_obj = Path(path)
path_obj.parent.mkdir(parents=True, exist_ok=True)

previous = {}
try:
    previous = json.loads(path_obj.read_text(encoding="utf-8"))
except Exception:
    previous = {}

pid_value = int(pid) if pid.isdigit() else None
if state in {"RUNNING", "NOT_RUNNING"} and not log_path:
    log_path = previous.get("log", "")

def preserve_or_current(key, current):
    if state in {"RUNNING", "NOT_RUNNING"}:
        return previous.get(key)
    return current

data = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "pid": pid_value,
    "log": log_path,
    "watcher": preserve_or_current("watcher", watcher),
    "start_script": preserve_or_current("start_script", start_script),
    "watcher_sha256": preserve_or_current("watcher_sha256", watcher_sha),
    "start_script_sha256": preserve_or_current("start_script_sha256", start_script_sha),
    "wait_iterations": preserve_or_current("wait_iterations", int(wait_iterations)),
    "sleep_seconds": preserve_or_current("sleep_seconds", int(sleep_seconds)),
    "local_dry_run": preserve_or_current("local_dry_run", local_dry_run == "1"),
    "start_on_pass": preserve_or_current("start_on_pass", start_on_pass == "1"),
    "stop_on_probe_error": preserve_or_current("stop_on_probe_error", stop_on_probe_error == "1"),
    "launch_method": preserve_or_current("launch_method", launch_method),
    "screen_session": preserve_or_current("screen_session", screen_session),
    "runner_script": preserve_or_current("runner_script", runner_script),
    "restart_on_config_mismatch": preserve_or_current("restart_on_config_mismatch", restart_on_config_mismatch == "1"),
}
path_obj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

method="$(resolved_launch_method)"

config_matches_requested() {
  python3 - "$DETACHED_STATUS_JSON" "$WATCHER" "$START_SCRIPT" "$WATCHER_SHA" "$START_SCRIPT_SHA" "$WAIT_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$START_ON_PASS" "$STOP_ON_PROBE_ERROR" "$method" "$SCREEN_SESSION" "$RUNNER_SCRIPT" <<'PY'
import json
import sys
from pathlib import Path

(
    path,
    watcher,
    start_script,
    watcher_sha,
    start_script_sha,
    wait_iterations,
    sleep_seconds,
    local_dry_run,
    start_on_pass,
    stop_on_probe_error,
    launch_method,
    screen_session,
    runner_script,
) = sys.argv[1:]

path_obj = Path(path)
if not path_obj.exists():
    print("MISSING_STATUS_JSON")
    raise SystemExit(1)
try:
    data = json.loads(path_obj.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"STATUS_JSON_PARSE_ERROR:{type(exc).__name__}")
    raise SystemExit(1)

expected = {
    "watcher": watcher,
    "start_script": start_script,
    "watcher_sha256": watcher_sha,
    "start_script_sha256": start_script_sha,
    "wait_iterations": int(wait_iterations),
    "sleep_seconds": int(sleep_seconds),
    "local_dry_run": local_dry_run == "1",
    "start_on_pass": start_on_pass == "1",
    "stop_on_probe_error": stop_on_probe_error == "1",
    "launch_method": launch_method,
    "screen_session": screen_session,
    "runner_script": runner_script,
}
mismatches = []
for key, expected_value in expected.items():
    actual_value = data.get(key)
    if actual_value != expected_value:
        mismatches.append(f"{key}:expected={expected_value!r},actual={actual_value!r}")
if mismatches:
    print("CONFIG_MISMATCH " + "; ".join(mismatches))
    raise SystemExit(1)
print("CONFIG_MATCH")
PY
}

stop_existing_runner() {
  if [ "$method" = "screen" ] && screen_running; then
    screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
      if ! screen_running; then
        return 0
      fi
      sleep 1
    done
    return 1
  fi
  if [ -n "$latest_pid" ] && is_pid_alive "$latest_pid"; then
    kill "$latest_pid" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5; do
      if ! is_pid_alive "$latest_pid"; then
        return 0
      fi
      sleep 1
    done
    return 1
  fi
  return 0
}

cleanup_stale_orphan_waiters() {
  if [ "$CLEANUP_STALE_WAITERS" != "1" ]; then
    echo "WAIT_FOR_SSH_STALE_WAITER_CLEANUP=SKIPPED_CLEANUP_STALE_WAITERS_0"
    return 0
  fi
  python3 - "$RUNNER_SCRIPT" <<'PY'
import os
import signal
import subprocess
import sys

runner_script = sys.argv[1]
try:
    output = subprocess.check_output(
        ["ps", "-eo", "pid=,ppid=,command="],
        text=True,
        stderr=subprocess.DEVNULL,
    )
except Exception as exc:
    print(f"WAIT_FOR_SSH_STALE_WAITER_CLEANUP=PS_FAILED error={type(exc).__name__}")
    raise SystemExit(0)

stale_pids = []
for raw_line in output.splitlines():
    parts = raw_line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    pid_s, ppid_s, command = parts
    try:
        pid = int(pid_s)
        ppid = int(ppid_s)
    except ValueError:
        continue
    if ppid != 1:
        continue
    if runner_script not in command:
        continue
    if "login -pflq" not in command:
        continue
    stale_pids.append(pid)

for pid in stale_pids:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        print(f"WAIT_FOR_SSH_STALE_WAITER_CLEANUP=PERMISSION_DENIED pid={pid}")

if stale_pids:
    print("WAIT_FOR_SSH_STALE_WAITER_CLEANUP=SIGTERM_ORPHAN_LOGIN_PIDS " + ",".join(map(str, stale_pids)))
else:
    print("WAIT_FOR_SSH_STALE_WAITER_CLEANUP=NO_ORPHAN_LOGIN_PIDS")
PY
}

if [ "$ACTION" = "status" ]; then
  if [ "$method" = "screen" ] && screen_running; then
    echo "WAIT_FOR_SSH_DETACHED_STATUS=RUNNING"
    echo "screen_session=$SCREEN_SESSION"
    echo "pid_file=$PID_FILE"
    echo "status_json=$DETACHED_STATUS_JSON"
    write_detached_status_json RUNNING "${latest_pid:-}" ""
    exit 0
  fi
  if [ -n "$latest_pid" ] && is_pid_alive "$latest_pid"; then
    echo "WAIT_FOR_SSH_DETACHED_STATUS=RUNNING"
    echo "pid=$latest_pid"
    echo "pid_file=$PID_FILE"
    echo "status_json=$DETACHED_STATUS_JSON"
    write_detached_status_json RUNNING "$latest_pid" ""
    exit 0
  fi
  echo "WAIT_FOR_SSH_DETACHED_STATUS=NOT_RUNNING"
  echo "pid_file=$PID_FILE"
  echo "status_json=$DETACHED_STATUS_JSON"
  write_detached_status_json NOT_RUNNING "${latest_pid:-}" ""
  exit 0
fi

if { [ "$method" = "screen" ] && screen_running; } || { [ -n "$latest_pid" ] && is_pid_alive "$latest_pid"; }; then
  config_check_output=""
  if config_check_output="$(config_matches_requested 2>&1)"; then
    cleanup_stale_orphan_waiters
    echo "WAIT_FOR_SSH_DETACHED_STATUS=ALREADY_RUNNING"
    if [ "$method" = "screen" ]; then
      echo "screen_session=$SCREEN_SESSION"
    else
      echo "pid=$latest_pid"
    fi
    echo "pid_file=$PID_FILE"
    echo "status_json=$DETACHED_STATUS_JSON"
    echo "config_status=$config_check_output"
    exit 0
  fi
  echo "WAIT_FOR_SSH_DETACHED_STATUS=CONFIG_MISMATCH"
  if [ "$method" = "screen" ]; then
    echo "screen_session=$SCREEN_SESSION"
  else
    echo "pid=$latest_pid"
  fi
  echo "pid_file=$PID_FILE"
  echo "status_json=$DETACHED_STATUS_JSON"
  echo "config_mismatch=$config_check_output"
  if [ "$RESTART_ON_CONFIG_MISMATCH" != "1" ]; then
    echo "WAIT_FOR_SSH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
    exit 1
  fi
  echo "WAIT_FOR_SSH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH"
  if ! stop_existing_runner; then
    echo "WAIT_FOR_SSH_DETACHED_STATUS=FAIL_OLD_WAITER_STILL_RUNNING"
    exit 1
  fi
fi

cleanup_stale_orphan_waiters

if [ "$method" = "screen" ] && ! screen_available; then
  echo "ERROR: screen requested but not available." >&2
  exit 2
fi

if [ -z "$DETACHED_LOG" ]; then
  stamp="$(date '+%Y%m%d_%H%M%S')"
  DETACHED_LOG="$STATE_DIR/mars56_wait_for_ssh_start_detached_${stamp}.log"
fi

echo "WAIT_FOR_SSH_DETACHED_STARTING"
echo "watcher=$WATCHER"
echo "wait_iterations=$WAIT_ITERATIONS"
echo "sleep_seconds=$SLEEP_SECONDS"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "start_on_pass=$START_ON_PASS"
echo "launch_method=$method"
echo "screen_session=$SCREEN_SESSION"
echo "start_script=$START_SCRIPT"
echo "watcher_sha256=$WATCHER_SHA"
echo "start_script_sha256=$START_SCRIPT_SHA"
echo "restart_on_config_mismatch=$RESTART_ON_CONFIG_MISMATCH"
echo "detached_log=$DETACHED_LOG"

if [ "$method" = "screen" ]; then
  python3 - "$RUNNER_SCRIPT" "$ROOT_DIR" "$WAIT_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$START_ON_PASS" "$STOP_ON_PROBE_ERROR" "$WAIT_STATUS_JSON" "$DRY_RUN_PROBE_STATUSES" "$WATCHER" "$START_SCRIPT" "$DETACHED_LOG" <<'PY'
import shlex
import sys
from pathlib import Path

(
    runner_script,
    root_dir,
    wait_iterations,
    sleep_seconds,
    local_dry_run,
    start_on_pass,
    stop_on_probe_error,
    wait_status_json,
    dry_run_probe_statuses,
    watcher,
    start_script,
    detached_log,
) = sys.argv[1:]

lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    f"cd {shlex.quote(root_dir)}",
    "exec env \\",
    f"  WAIT_ITERATIONS={shlex.quote(wait_iterations)} \\",
    f"  SLEEP_SECONDS={shlex.quote(sleep_seconds)} \\",
    f"  LOCAL_DRY_RUN={shlex.quote(local_dry_run)} \\",
    f"  START_ON_PASS={shlex.quote(start_on_pass)} \\",
    f"  STOP_ON_PROBE_ERROR={shlex.quote(stop_on_probe_error)} \\",
    "  WAIT_LOG_CAPTURE=0 \\",
    f"  WAIT_STATUS_JSON={shlex.quote(wait_status_json)} \\",
    f"  DRY_RUN_PROBE_STATUSES={shlex.quote(dry_run_probe_statuses)} \\",
    f"  START_SCRIPT={shlex.quote(start_script)} \\",
    f"  bash {shlex.quote(watcher)} >>{shlex.quote(detached_log)} 2>&1 < /dev/null",
]
path = Path(runner_script)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
path.chmod(0o755)
PY
  screen -dmS "$SCREEN_SESSION" bash "$RUNNER_SCRIPT"
  pid=""
  printf '%s\n' "$SCREEN_SESSION" >"$PID_FILE"
else
  nohup env \
    WAIT_ITERATIONS="$WAIT_ITERATIONS" \
    SLEEP_SECONDS="$SLEEP_SECONDS" \
    LOCAL_DRY_RUN="$LOCAL_DRY_RUN" \
    START_ON_PASS="$START_ON_PASS" \
    STOP_ON_PROBE_ERROR="$STOP_ON_PROBE_ERROR" \
    WAIT_LOG_CAPTURE=0 \
    WAIT_STATUS_JSON="$WAIT_STATUS_JSON" \
    DRY_RUN_PROBE_STATUSES="$DRY_RUN_PROBE_STATUSES" \
    START_SCRIPT="$START_SCRIPT" \
    bash -c 'bash "$1"; rc=$?; echo "WAIT_FOR_SSH_WATCHER_EXIT_RC=$rc"; exit "$rc"' _ "$WATCHER" >"$DETACHED_LOG" 2>&1 < /dev/null &
  pid="$!"
  printf '%s\n' "$pid" >"$PID_FILE"
fi

write_detached_status_json STARTED "$pid" "$DETACHED_LOG"

echo "WAIT_FOR_SSH_DETACHED_STATUS=STARTED"
echo "pid=$pid"
echo "screen_session=$SCREEN_SESSION"
echo "pid_file=$PID_FILE"
echo "detached_log=$DETACHED_LOG"
echo "status_json=$DETACHED_STATUS_JSON"
