#!/usr/bin/env bash
set -euo pipefail

# Detached launcher/status wrapper for the post-Duo continuous watcher.
#
# Safe local smoke test:
#   LOCAL_DRY_RUN=1 WATCH_ITERATIONS=1 SLEEP_SECONDS=0 bash START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh
#
# After interactive Duo access is available, start the unattended watcher:
#   WATCH_ITERATIONS=0 SLEEP_SECONDS=1800 bash START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh
#
# Check the latest detached watcher:
#   ACTION=status bash START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh
#
# ACTION=start|status are the supported modes.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
WATCHER="$ROOT_DIR/RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/logs/mars56_post_duo_continuous_watch}"
ACTION="${ACTION:-start}"

PID_FILE="${PID_FILE:-$STATE_DIR/mars56_post_duo_continuous_watch_latest.pid}"
DETACHED_LOG="${DETACHED_LOG:-}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$STATE_DIR/mars56_post_duo_continuous_watch_latest_detached_status.json}"

WATCH_ITERATIONS="${WATCH_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1800}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
STOP_ON_ERROR="${STOP_ON_ERROR:-0}"
STOP_ON_GOAL_PASS="${STOP_ON_GOAL_PASS:-1}"
RUN_CHECKPOINT="${RUN_CHECKPOINT:-1}"
RUN_CUMULATIVE="${RUN_CUMULATIVE:-1}"
RUN_AUDIT="${RUN_AUDIT:-1}"
RUN_VERIFY_RUNNER="${RUN_VERIFY_RUNNER:-1}"
VERIFY_RUNNER_REQUIRED="${VERIFY_RUNNER_REQUIRED:-1}"
RUN_RESUME_WATCHERS="${RUN_RESUME_WATCHERS:-1}"
RUN_RATE_AUDIT="${RUN_RATE_AUDIT:-1}"
RUN_ADAPTIVE_ACQUISITION="${RUN_ADAPTIVE_ACQUISITION:-1}"
RUN_ADAPTIVE_EMX="${RUN_ADAPTIVE_EMX:-1}"
RUN_EVIDENCE_INDEX="${RUN_EVIDENCE_INDEX:-1}"
RESTART_ON_CONFIG_MISMATCH="${RESTART_ON_CONFIG_MISMATCH:-1}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"

case "$ACTION" in start|status) ;;
  *) echo "ERROR: ACTION must be start or status." >&2; exit 2 ;;
esac
for value in "$STATE_DIR" "$PID_FILE" "$DETACHED_LOG" "$DETACHED_STATUS_JSON" "$CONTROL_DIR" "$SSH_CONTROL_PATH"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path settings contain unsupported newline characters." >&2
    exit 2
  fi
done
for var_name in LOCAL_DRY_RUN STOP_ON_ERROR STOP_ON_GOAL_PASS RUN_CHECKPOINT RUN_CUMULATIVE RUN_AUDIT RUN_VERIFY_RUNNER VERIFY_RUNNER_REQUIRED RUN_RESUME_WATCHERS RUN_RATE_AUDIT RUN_ADAPTIVE_ACQUISITION RUN_ADAPTIVE_EMX RUN_EVIDENCE_INDEX RESTART_ON_CONFIG_MISMATCH; do
  value="${!var_name}"
  case "$value" in 0|1) ;;
    *) echo "ERROR: $var_name must be 0 or 1." >&2; exit 2 ;;
  esac
done
if ! [[ "$WATCH_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: WATCH_ITERATIONS must be a nonnegative integer; use 0 for forever." >&2
  exit 2
fi
if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: SLEEP_SECONDS must be a nonnegative integer." >&2
  exit 2
fi
if [ ! -f "$WATCHER" ]; then
  echo "ERROR: watcher script missing: $WATCHER" >&2
  exit 2
fi

mkdir -p "$STATE_DIR"
mkdir -p "$CONTROL_DIR"
export SSH_CONTROL_PATH SSH_PERSIST CONTROL_DIR

is_pid_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1
}

latest_pid=""
if [ -s "$PID_FILE" ]; then
  latest_pid="$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null || true)"
fi

write_status_json() {
  local state="$1"
  local pid="$2"
  local log_path="$3"
  python3 - "$DETACHED_STATUS_JSON" "$state" "$pid" "$log_path" "$WATCHER" "$WATCH_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$SSH_CONTROL_PATH" "$SSH_PERSIST" "$RUN_VERIFY_RUNNER" "$RUN_RESUME_WATCHERS" "$RUN_RATE_AUDIT" "$RUN_ADAPTIVE_ACQUISITION" "$RUN_ADAPTIVE_EMX" "$RUN_CHECKPOINT" "$RUN_CUMULATIVE" "$RUN_EVIDENCE_INDEX" "$RUN_AUDIT" "$RESTART_ON_CONFIG_MISMATCH" <<'PY'
import json
import sys
from datetime import datetime, timezone

(
    path,
    state,
    pid,
    log_path,
    watcher,
    iterations,
    sleep_seconds,
    local_dry_run,
    ssh_control_path,
    ssh_persist,
    run_verify_runner,
    run_resume_watchers,
    run_rate_audit,
    run_adaptive_acquisition,
    run_adaptive_emx,
    run_checkpoint,
    run_cumulative,
    run_evidence_index,
    run_audit,
    restart_on_config_mismatch,
) = sys.argv[1:]
previous = {}
try:
    with open(path, encoding="utf-8") as fh:
        previous = json.load(fh)
except Exception:
    previous = {}

pid_value = int(pid) if pid.isdigit() else None
reuse_previous_launch = (
    state in {"RUNNING", "NOT_RUNNING"}
    and pid_value is not None
    and previous.get("pid") == pid_value
)
if reuse_previous_launch and not log_path:
    log_path = previous.get("log", "")
if reuse_previous_launch and "watch_iterations" in previous:
    iterations_value = previous.get("watch_iterations")
else:
    iterations_value = int(iterations)
if reuse_previous_launch and "sleep_seconds" in previous:
    sleep_seconds_value = previous.get("sleep_seconds")
else:
    sleep_seconds_value = int(sleep_seconds)
if reuse_previous_launch and "local_dry_run" in previous:
    local_dry_run_value = previous.get("local_dry_run")
else:
    local_dry_run_value = local_dry_run == "1"
if reuse_previous_launch and "ssh_control_path" in previous:
    ssh_control_path_value = previous.get("ssh_control_path")
else:
    ssh_control_path_value = ssh_control_path
if reuse_previous_launch and "ssh_persist" in previous:
    ssh_persist_value = previous.get("ssh_persist")
else:
    ssh_persist_value = ssh_persist

def bool_value(key, raw_value):
    if reuse_previous_launch and key in previous:
        return previous.get(key)
    return raw_value == "1"

data = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "pid": pid_value,
    "log": log_path,
    "watcher": watcher,
    "watch_iterations": iterations_value,
    "sleep_seconds": sleep_seconds_value,
    "local_dry_run": local_dry_run_value,
    "ssh_control_path": ssh_control_path_value,
    "ssh_persist": ssh_persist_value,
    "run_verify_runner": bool_value("run_verify_runner", run_verify_runner),
    "run_resume_watchers": bool_value("run_resume_watchers", run_resume_watchers),
    "run_rate_audit": bool_value("run_rate_audit", run_rate_audit),
    "run_adaptive_acquisition": bool_value("run_adaptive_acquisition", run_adaptive_acquisition),
    "run_adaptive_emx": bool_value("run_adaptive_emx", run_adaptive_emx),
    "run_checkpoint": bool_value("run_checkpoint", run_checkpoint),
    "run_cumulative": bool_value("run_cumulative", run_cumulative),
    "run_evidence_index": bool_value("run_evidence_index", run_evidence_index),
    "run_audit": bool_value("run_audit", run_audit),
    "restart_on_config_mismatch": bool_value("restart_on_config_mismatch", restart_on_config_mismatch),
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
}

config_matches_requested() {
  python3 - "$DETACHED_STATUS_JSON" "$WATCHER" "$WATCH_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$SSH_CONTROL_PATH" "$SSH_PERSIST" "$RUN_VERIFY_RUNNER" "$RUN_RESUME_WATCHERS" "$RUN_RATE_AUDIT" "$RUN_ADAPTIVE_ACQUISITION" "$RUN_ADAPTIVE_EMX" "$RUN_CHECKPOINT" "$RUN_CUMULATIVE" "$RUN_EVIDENCE_INDEX" "$RUN_AUDIT" <<'PY'
import json
import sys
from pathlib import Path

(
    path,
    watcher,
    iterations,
    sleep_seconds,
    local_dry_run,
    ssh_control_path,
    ssh_persist,
    run_verify_runner,
    run_resume_watchers,
    run_rate_audit,
    run_adaptive_acquisition,
    run_adaptive_emx,
    run_checkpoint,
    run_cumulative,
    run_evidence_index,
    run_audit,
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
    "watch_iterations": int(iterations),
    "sleep_seconds": int(sleep_seconds),
    "local_dry_run": local_dry_run == "1",
    "ssh_control_path": ssh_control_path,
    "ssh_persist": ssh_persist,
    "run_verify_runner": run_verify_runner == "1",
    "run_resume_watchers": run_resume_watchers == "1",
    "run_rate_audit": run_rate_audit == "1",
    "run_adaptive_acquisition": run_adaptive_acquisition == "1",
    "run_adaptive_emx": run_adaptive_emx == "1",
    "run_checkpoint": run_checkpoint == "1",
    "run_cumulative": run_cumulative == "1",
    "run_evidence_index": run_evidence_index == "1",
    "run_audit": run_audit == "1",
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

if [ "$ACTION" = "status" ]; then
  if [ -n "$latest_pid" ] && is_pid_alive "$latest_pid"; then
    echo "DETACHED_WATCH_STATUS=RUNNING"
    echo "pid=$latest_pid"
    echo "pid_file=$PID_FILE"
    echo "status_json=$DETACHED_STATUS_JSON"
    write_status_json RUNNING "$latest_pid" "$(python3 - "$DETACHED_STATUS_JSON" <<'PY' 2>/dev/null || true
import json
import sys
try:
    print(json.load(open(sys.argv[1])).get("log", ""))
except Exception:
    print("")
PY
)"
    exit 0
  fi
  echo "DETACHED_WATCH_STATUS=NOT_RUNNING"
  echo "pid_file=$PID_FILE"
  echo "status_json=$DETACHED_STATUS_JSON"
  write_status_json NOT_RUNNING "${latest_pid:-}" ""
  exit 0
fi

if [ -n "$latest_pid" ] && is_pid_alive "$latest_pid"; then
  config_check_output=""
  if config_check_output="$(config_matches_requested 2>&1)"; then
    echo "DETACHED_WATCH_STATUS=ALREADY_RUNNING"
    echo "pid=$latest_pid"
    echo "pid_file=$PID_FILE"
    echo "status_json=$DETACHED_STATUS_JSON"
    echo "config_status=$config_check_output"
    exit 0
  fi
  echo "DETACHED_WATCH_STATUS=CONFIG_MISMATCH"
  echo "pid=$latest_pid"
  echo "pid_file=$PID_FILE"
  echo "status_json=$DETACHED_STATUS_JSON"
  echo "config_mismatch=$config_check_output"
  if [ "$RESTART_ON_CONFIG_MISMATCH" != "1" ]; then
    echo "DETACHED_WATCH_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
    exit 1
  fi
  echo "DETACHED_WATCH_DECISION=RESTART_CONFIG_MISMATCH"
  kill "$latest_pid" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    if ! is_pid_alive "$latest_pid"; then
      break
    fi
    sleep 1
  done
  if is_pid_alive "$latest_pid"; then
    echo "DETACHED_WATCH_STATUS=FAIL_OLD_WATCHER_STILL_RUNNING"
    exit 1
  fi
fi

if [ -z "$DETACHED_LOG" ]; then
  stamp="$(date '+%Y%m%d_%H%M%S')"
  DETACHED_LOG="$STATE_DIR/mars56_post_duo_continuous_watch_detached_${stamp}.log"
fi

echo "DETACHED_WATCH_STARTING"
echo "watcher=$WATCHER"
echo "watch_iterations=$WATCH_ITERATIONS"
echo "sleep_seconds=$SLEEP_SECONDS"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "run_adaptive_acquisition=$RUN_ADAPTIVE_ACQUISITION"
echo "run_adaptive_emx=$RUN_ADAPTIVE_EMX"
echo "restart_on_config_mismatch=$RESTART_ON_CONFIG_MISMATCH"
echo "ssh_control_path=$SSH_CONTROL_PATH"
echo "ssh_persist=$SSH_PERSIST"
echo "detached_log=$DETACHED_LOG"

nohup env \
  WATCH_ITERATIONS="$WATCH_ITERATIONS" \
  SLEEP_SECONDS="$SLEEP_SECONDS" \
  LOCAL_DRY_RUN="$LOCAL_DRY_RUN" \
  STOP_ON_ERROR="$STOP_ON_ERROR" \
  STOP_ON_GOAL_PASS="$STOP_ON_GOAL_PASS" \
  RUN_CHECKPOINT="$RUN_CHECKPOINT" \
  RUN_CUMULATIVE="$RUN_CUMULATIVE" \
  RUN_AUDIT="$RUN_AUDIT" \
  RUN_VERIFY_RUNNER="$RUN_VERIFY_RUNNER" \
  VERIFY_RUNNER_REQUIRED="$VERIFY_RUNNER_REQUIRED" \
  RUN_RESUME_WATCHERS="$RUN_RESUME_WATCHERS" \
  RUN_RATE_AUDIT="$RUN_RATE_AUDIT" \
  RUN_ADAPTIVE_ACQUISITION="$RUN_ADAPTIVE_ACQUISITION" \
  RUN_ADAPTIVE_EMX="$RUN_ADAPTIVE_EMX" \
  RUN_EVIDENCE_INDEX="$RUN_EVIDENCE_INDEX" \
  RESTART_ON_CONFIG_MISMATCH="$RESTART_ON_CONFIG_MISMATCH" \
  SSH_CONTROL_PATH="$SSH_CONTROL_PATH" \
  SSH_PERSIST="$SSH_PERSIST" \
  CONTROL_DIR="$CONTROL_DIR" \
  bash "$WATCHER" >"$DETACHED_LOG" 2>&1 &
pid="$!"
printf '%s\n' "$pid" >"$PID_FILE"
write_status_json STARTED "$pid" "$DETACHED_LOG"

echo "DETACHED_WATCH_STATUS=STARTED"
echo "pid=$pid"
echo "pid_file=$PID_FILE"
echo "detached_log=$DETACHED_LOG"
echo "status_json=$DETACHED_STATUS_JSON"
