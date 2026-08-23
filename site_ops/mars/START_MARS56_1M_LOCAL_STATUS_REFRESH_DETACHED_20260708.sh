#!/usr/bin/env bash
set -euo pipefail

# Starts or reports a detached local status refresh loop for the MARS56 1M
# campaign. This is local-only monitoring and does not mutate remote production.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
RUN_SCRIPT="${RUN_SCRIPT:-$ROOT_DIR/RUN_MARS56_1M_LOCAL_STATUS_REFRESH_20260708.sh}"
SUMMARY_SCRIPT="${SUMMARY_SCRIPT:-$ROOT_DIR/SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh}"
ACTION="${ACTION:-start}"
SCREEN_SESSION="${SCREEN_SESSION:-mars56_1m_local_status_refresh}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/mars56_1m_local_status_refresh}"
PID_FILE="${PID_FILE:-$LOG_DIR/mars56_1m_local_status_refresh_latest.pid}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$LOG_DIR/mars56_1m_local_status_refresh_latest_detached_status.json}"
REFRESH_LOG_DIR="${REFRESH_LOG_DIR:-$LOG_DIR}"
REFRESH_STATUS_JSON="${REFRESH_STATUS_JSON:-$REFRESH_LOG_DIR/mars56_1m_local_status_refresh_latest_status.json}"
REFRESH_ITERATIONS="${REFRESH_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
RUN_PROBE_EACH_REFRESH="${RUN_PROBE_EACH_REFRESH:-1}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
RESTART_ON_CONFIG_MISMATCH="${RESTART_ON_CONFIG_MISMATCH:-1}"

case "$ACTION" in start|status) ;;
  *) echo "ERROR: ACTION must be start or status." >&2; exit 2 ;;
esac
for var_name in REFRESH_ITERATIONS SLEEP_SECONDS; do
  value="${!var_name}"
  case "$value" in
    ''|*[!0-9]*) echo "ERROR: $var_name must be a non-negative integer." >&2; exit 2 ;;
  esac
done
for var_name in RUN_PROBE_EACH_REFRESH LOCAL_DRY_RUN RESTART_ON_CONFIG_MISMATCH; do
  value="${!var_name}"
  case "$value" in
    0|1) ;;
    *) echo "ERROR: $var_name must be 0 or 1." >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR"

screen_running() {
  local output
  output="$(screen -ls 2>/dev/null || true)"
  printf "%s\n" "$output" | awk -v session="$SCREEN_SESSION" '
    $1 ~ ("[.]" session "$") { found=1 }
    END { exit found ? 0 : 1 }
  '
}

screen_session_pids() {
  local output
  output="$(screen -ls 2>/dev/null || true)"
  printf "%s\n" "$output" | awk -v session="$SCREEN_SESSION" '
    $1 ~ ("[.]" session "$") {
      split($1, parts, ".")
      print parts[1]
    }
  '
}

screen_session_tree_pids() {
  local roots
  roots="$(screen_session_pids | tr '\n' ' ')"
  [ -n "$roots" ] || return 0
  python3 - "$roots" <<'PY'
from __future__ import annotations

import subprocess
import sys

roots = []
for item in sys.argv[1].split():
    try:
        roots.append(int(item))
    except ValueError:
        pass
if not roots:
    raise SystemExit(0)

completed = subprocess.run(
    ["ps", "-axo", "pid=,ppid="],
    text=True,
    stdout=subprocess.PIPE,
    check=False,
)
children: dict[int, list[int]] = {}
known: set[int] = set()
for line in completed.stdout.splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) != 2:
        continue
    try:
        pid = int(parts[0])
        ppid = int(parts[1])
    except ValueError:
        continue
    known.add(pid)
    children.setdefault(ppid, []).append(pid)

found: set[int] = set()
stack = list(roots)
while stack:
    pid = stack.pop()
    if pid in found or pid not in known:
        continue
    found.add(pid)
    stack.extend(children.get(pid, []))
for pid in sorted(found):
    print(pid)
PY
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

RUN_SCRIPT_SHA="$(sha256_file "$RUN_SCRIPT")"
SUMMARY_SCRIPT_SHA="$(sha256_file "$SUMMARY_SCRIPT")"

write_detached_status_json() {
  local state="$1"
  local message="$2"
  python3 - "$DETACHED_STATUS_JSON" "$state" "$message" "$SCREEN_SESSION" "$PID_FILE" "$RUN_SCRIPT" "$RUN_SCRIPT_SHA" "$SUMMARY_SCRIPT" "$SUMMARY_SCRIPT_SHA" "$REFRESH_LOG_DIR" "$REFRESH_STATUS_JSON" "$REFRESH_ITERATIONS" "$SLEEP_SECONDS" "$RUN_PROBE_EACH_REFRESH" "$LOCAL_DRY_RUN" "$RESTART_ON_CONFIG_MISMATCH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
try:
    previous = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    previous = {}

state = sys.argv[2]

def preserve_or_current(key, current):
    if state in {"RUNNING", "NOT_RUNNING"}:
        return previous.get(key)
    return current

payload = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "message": sys.argv[3],
    "screen_session": preserve_or_current("screen_session", sys.argv[4]),
    "pid_file": sys.argv[5],
    "run_script": preserve_or_current("run_script", sys.argv[6]),
    "run_script_sha256": preserve_or_current("run_script_sha256", sys.argv[7]),
    "summary_script": preserve_or_current("summary_script", sys.argv[8]),
    "summary_script_sha256": preserve_or_current("summary_script_sha256", sys.argv[9]),
    "refresh_log_dir": preserve_or_current("refresh_log_dir", sys.argv[10]),
    "refresh_status_json": preserve_or_current("refresh_status_json", sys.argv[11]),
    "refresh_iterations": preserve_or_current("refresh_iterations", int(sys.argv[12])),
    "sleep_seconds": preserve_or_current("sleep_seconds", int(sys.argv[13])),
    "run_probe_each_refresh": preserve_or_current("run_probe_each_refresh", sys.argv[14] == "1"),
    "local_dry_run": preserve_or_current("local_dry_run", sys.argv[15] == "1"),
    "restart_on_config_mismatch": preserve_or_current("restart_on_config_mismatch", sys.argv[16] == "1"),
}
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
PY
}

config_matches_requested() {
  python3 - "$DETACHED_STATUS_JSON" "$SCREEN_SESSION" "$RUN_SCRIPT" "$RUN_SCRIPT_SHA" "$SUMMARY_SCRIPT" "$SUMMARY_SCRIPT_SHA" "$REFRESH_LOG_DIR" "$REFRESH_STATUS_JSON" "$REFRESH_ITERATIONS" "$SLEEP_SECONDS" "$RUN_PROBE_EACH_REFRESH" "$LOCAL_DRY_RUN" <<'PY'
import json
import sys
from pathlib import Path

(
    path,
    screen_session,
    run_script,
    run_script_sha,
    summary_script,
    summary_script_sha,
    refresh_log_dir,
    refresh_status_json,
    refresh_iterations,
    sleep_seconds,
    run_probe_each_refresh,
    local_dry_run,
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
    "screen_session": screen_session,
    "run_script": run_script,
    "run_script_sha256": run_script_sha,
    "summary_script": summary_script,
    "summary_script_sha256": summary_script_sha,
    "refresh_log_dir": refresh_log_dir,
    "refresh_status_json": refresh_status_json,
    "refresh_iterations": int(refresh_iterations),
    "sleep_seconds": int(sleep_seconds),
    "run_probe_each_refresh": run_probe_each_refresh == "1",
    "local_dry_run": local_dry_run == "1",
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

stop_existing_screen() {
  if ! screen_running; then
    return 0
  fi
  local old_pids
  old_pids="$(screen_session_tree_pids | tr '\n' ' ')"
  screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    if ! screen_running; then
      kill_old_screen_descendants "$old_pids"
      return 0
    fi
    sleep 1
  done
  kill_old_screen_descendants "$old_pids"
  if ! screen_running; then
    return 0
  fi
  return 1
}

kill_old_screen_descendants() {
  local old_pids="$1"
  [ -n "$old_pids" ] || return 0
  python3 - "$old_pids" <<'PY' >/dev/null 2>&1 || true
from __future__ import annotations

import os
import signal
import sys
import time

old_pids = []
for item in sys.argv[1].split():
    try:
        old_pids.append(int(item))
    except ValueError:
        pass
old_pids = sorted(set(pid for pid in old_pids if pid != os.getpid()))
if not old_pids:
    raise SystemExit(0)


def alive_pids() -> list[int]:
    alive: list[int] = []
    for pid in old_pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            alive.append(pid)
        else:
            alive.append(pid)
    return alive


for sig in (signal.SIGTERM, signal.SIGKILL):
    pids = alive_pids()
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
}

if [ "$ACTION" = "status" ]; then
  if screen_running; then
    write_detached_status_json "RUNNING" "Local status refresh screen session is running."
    echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=RUNNING"
    echo "screen_session=$SCREEN_SESSION"
    echo "status_json=$DETACHED_STATUS_JSON"
    exit 0
  fi
  write_detached_status_json "NOT_RUNNING" "Local status refresh screen session is not running."
  echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=NOT_RUNNING"
  echo "screen_session=$SCREEN_SESSION"
  echo "status_json=$DETACHED_STATUS_JSON"
  exit 1
fi

if screen_running; then
  config_check_output=""
  if config_check_output="$(config_matches_requested 2>&1)"; then
    write_detached_status_json "ALREADY_RUNNING" "Local status refresh screen session was already running with matching config."
    echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=ALREADY_RUNNING"
    echo "screen_session=$SCREEN_SESSION"
    echo "status_json=$DETACHED_STATUS_JSON"
    echo "config_status=$config_check_output"
    exit 0
  fi
  echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=CONFIG_MISMATCH"
  echo "screen_session=$SCREEN_SESSION"
  echo "status_json=$DETACHED_STATUS_JSON"
  echo "config_mismatch=$config_check_output"
  if [ "$RESTART_ON_CONFIG_MISMATCH" != "1" ]; then
    echo "LOCAL_STATUS_REFRESH_DETACHED_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
    exit 1
  fi
  echo "LOCAL_STATUS_REFRESH_DETACHED_DECISION=RESTART_CONFIG_MISMATCH"
  if ! stop_existing_screen; then
    write_detached_status_json "FAILED" "Existing local status refresh screen session could not be stopped."
    echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=FAIL_OLD_REFRESH_STILL_RUNNING"
    exit 1
  fi
fi

runner="$LOG_DIR/mars56_1m_local_status_refresh_screen_runner.sh"
cat > "$runner" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$ROOT_DIR"
exec env \\
  REFRESH_ITERATIONS="$REFRESH_ITERATIONS" \\
  SLEEP_SECONDS="$SLEEP_SECONDS" \\
  RUN_PROBE_EACH_REFRESH="$RUN_PROBE_EACH_REFRESH" \\
  LOCAL_DRY_RUN="$LOCAL_DRY_RUN" \\
  SUMMARY_SCRIPT="$SUMMARY_SCRIPT" \\
  REFRESH_LOG_DIR="$REFRESH_LOG_DIR" \\
  REFRESH_STATUS_JSON="$REFRESH_STATUS_JSON" \\
  bash "$RUN_SCRIPT"
EOF
chmod +x "$runner"

screen -dmS "$SCREEN_SESSION" bash "$runner"
sleep 1

if screen_running; then
  screen -ls | awk -v name="$SCREEN_SESSION" '$0 ~ name {print $1}' | head -n 1 > "$PID_FILE" || true
  write_detached_status_json "STARTED" "Local status refresh screen session started."
  echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=STARTED"
  echo "screen_session=$SCREEN_SESSION"
  echo "pid_file=$PID_FILE"
  echo "status_json=$DETACHED_STATUS_JSON"
  echo "run_script_sha256=$RUN_SCRIPT_SHA"
  echo "summary_script_sha256=$SUMMARY_SCRIPT_SHA"
  exit 0
fi

write_detached_status_json "FAILED" "Failed to start local status refresh screen session."
echo "LOCAL_STATUS_REFRESH_DETACHED_STATUS=FAILED"
echo "screen_session=$SCREEN_SESSION"
echo "status_json=$DETACHED_STATUS_JSON"
exit 1
