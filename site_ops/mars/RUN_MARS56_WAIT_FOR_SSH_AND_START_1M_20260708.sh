#!/usr/bin/env bash
set -euo pipefail

# Local auth-availability watcher for the MARS56 1M campaign.
#
# It repeatedly runs the read-only BatchMode SSH probe. Once a reusable SSH
# control connection is available, it launches the post-Duo 1M sync/start
# wrapper. This avoids missing the window after the user completes local
# interactive SSH/Duo authentication and a reusable control connection exists.
#
# Safe local test:
#   LOCAL_DRY_RUN=1 WAIT_ITERATIONS=2 SLEEP_SECONDS=0 \
#   DRY_RUN_PROBE_STATUSES=WAITING_FOR_INTERACTIVE_AUTH,PASS_REUSABLE_CONTROL_CONNECTION \
#   bash RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
PROBE_SCRIPT="${PROBE_SCRIPT:-$ROOT_DIR/CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh}"
START_SCRIPT="${START_SCRIPT:-$ROOT_DIR/RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh}"
BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT_DIR/START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh}"

WAIT_ITERATIONS="${WAIT_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
START_ON_PASS="${START_ON_PASS:-1}"
STOP_ON_PROBE_ERROR="${STOP_ON_PROBE_ERROR:-0}"
WAIT_LOG_CAPTURE="${WAIT_LOG_CAPTURE:-1}"
WAIT_LOG_DIR="${WAIT_LOG_DIR:-$ROOT_DIR/logs/mars56_wait_for_ssh_start}"
WAIT_LOG_PATH="${WAIT_LOG_PATH:-}"
WAIT_STATUS_JSON="${WAIT_STATUS_JSON:-$WAIT_LOG_DIR/mars56_wait_for_ssh_start_latest_status.json}"
DRY_RUN_PROBE_STATUSES="${DRY_RUN_PROBE_STATUSES:-WAITING_FOR_INTERACTIVE_AUTH,PASS_REUSABLE_CONTROL_CONNECTION}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$START_ON_PASS" in 0|1) ;;
  *) echo "ERROR: START_ON_PASS must be 0 or 1." >&2; exit 2 ;;
esac
case "$STOP_ON_PROBE_ERROR" in 0|1) ;;
  *) echo "ERROR: STOP_ON_PROBE_ERROR must be 0 or 1." >&2; exit 2 ;;
esac
case "$WAIT_LOG_CAPTURE" in 0|1) ;;
  *) echo "ERROR: WAIT_LOG_CAPTURE must be 0 or 1." >&2; exit 2 ;;
esac
if ! [[ "$WAIT_ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: WAIT_ITERATIONS must be a nonnegative integer; use 0 for forever." >&2
  exit 2
fi
if ! [[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: SLEEP_SECONDS must be a nonnegative integer." >&2
  exit 2
fi
for value in "$PROBE_SCRIPT" "$START_SCRIPT" "$BOOTSTRAP_SCRIPT" "$WAIT_LOG_DIR" "$WAIT_LOG_PATH" "$WAIT_STATUS_JSON" "$DRY_RUN_PROBE_STATUSES"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: wait/start settings contain unsupported newline characters." >&2
    exit 2
  fi
done
if [ ! -f "$PROBE_SCRIPT" ] || [ ! -f "$START_SCRIPT" ] || [ ! -f "$BOOTSTRAP_SCRIPT" ]; then
  echo "ERROR: required probe/start script missing." >&2
  echo "probe_script=$PROBE_SCRIPT" >&2
  echo "start_script=$START_SCRIPT" >&2
  echo "bootstrap_script=$BOOTSTRAP_SCRIPT" >&2
  exit 2
fi

last_iteration="NA"
trap 'rc=$?; echo "SSH_WAIT_PROCESS_EXIT rc=$rc last_iteration=${last_iteration:-NA}"' EXIT

write_wait_status_json() {
  local state="$1"
  local iteration="$2"
  local probe_status="$3"
  local probe_rc="$4"
  local start_rc="$5"
  local message="$6"
  python3 - "$WAIT_STATUS_JSON" "$state" "$iteration" "$probe_status" "$probe_rc" "$start_rc" "$message" "$PROBE_SCRIPT" "$START_SCRIPT" "$BOOTSTRAP_SCRIPT" "$WAIT_ITERATIONS" "$SLEEP_SECONDS" "$LOCAL_DRY_RUN" "$START_ON_PASS" "$WAIT_LOG_PATH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    state,
    iteration,
    probe_status,
    probe_rc,
    start_rc,
    message,
    probe_script,
    start_script,
    bootstrap_script,
    wait_iterations,
    sleep_seconds,
    local_dry_run,
    start_on_pass,
    wait_log_path,
) = sys.argv[1:]
path_obj = Path(path)
path_obj.parent.mkdir(parents=True, exist_ok=True)

def maybe_int(value):
    if value in {"", "NA"}:
        return None
    try:
        return int(value)
    except Exception:
        return value

data = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "iteration": maybe_int(iteration),
    "probe_status": probe_status,
    "probe_return_code": maybe_int(probe_rc),
    "start_return_code": maybe_int(start_rc),
    "message": message,
    "probe_script": probe_script,
    "start_script": start_script,
    "interactive_bootstrap_script": bootstrap_script,
    "interactive_bootstrap_command": "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "interactive_bootstrap_dry_run_command": "LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "interactive_bootstrap_success_status": "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION",
    "recommended_next_action": (
        "Run interactive_bootstrap_command in a local terminal and complete password/Duo."
        if state == "WAITING_FOR_INTERACTIVE_AUTH" or probe_status == "WAITING_FOR_INTERACTIVE_AUTH"
        else "No interactive SSH bootstrap action required for this wait state."
    ),
    "wait_iterations": int(wait_iterations),
    "sleep_seconds": int(sleep_seconds),
    "local_dry_run": local_dry_run == "1",
    "start_on_pass": start_on_pass == "1",
    "start_env_policy": (
        "dry_run_no_remote_start"
        if local_dry_run == "1"
        else "production_explicit_env_on_ssh_ready"
    ),
    "log": wait_log_path,
}
path_obj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

if [ "$WAIT_LOG_CAPTURE" = "1" ]; then
  mkdir -p "$WAIT_LOG_DIR"
  if [ -z "$WAIT_LOG_PATH" ]; then
    stamp="$(date '+%Y%m%d_%H%M%S')"
    WAIT_LOG_PATH="$WAIT_LOG_DIR/mars56_wait_for_ssh_start_${stamp}.log"
  fi
  if [ "${WAIT_LOG_REEXECED:-0}" != "1" ]; then
    export PROBE_SCRIPT START_SCRIPT WAIT_ITERATIONS SLEEP_SECONDS LOCAL_DRY_RUN START_ON_PASS STOP_ON_PROBE_ERROR
    export WAIT_LOG_CAPTURE WAIT_LOG_DIR WAIT_LOG_PATH WAIT_STATUS_JSON WAIT_LOG_REEXECED=1 DRY_RUN_PROBE_STATUSES
    set +e
    bash "$0" "$@" 2>&1 | tee -a "$WAIT_LOG_PATH"
    rc="${PIPESTATUS[0]}"
    set -e
    exit "$rc"
  fi
else
  mkdir -p "$(dirname "$WAIT_STATUS_JSON")"
fi

echo "MARS56 wait-for-SSH then start 1M watcher"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "wait_iterations=$WAIT_ITERATIONS"
echo "sleep_seconds=$SLEEP_SECONDS"
echo "start_on_pass=$START_ON_PASS"
echo "stop_on_probe_error=$STOP_ON_PROBE_ERROR"
echo "probe_script=$PROBE_SCRIPT"
echo "start_script=$START_SCRIPT"
echo "bootstrap_script=$BOOTSTRAP_SCRIPT"
echo "wait_status_json=$WAIT_STATUS_JSON"
if [ "$WAIT_LOG_CAPTURE" = "1" ]; then
  echo "wait_log=$WAIT_LOG_PATH"
else
  echo "wait_log=disabled"
fi
echo "note=WAIT_ITERATIONS=0 means wait forever until a reusable SSH connection appears."

IFS=',' read -r -a dry_run_statuses <<<"$DRY_RUN_PROBE_STATUSES"
if [ "${#dry_run_statuses[@]}" -eq 0 ]; then
  dry_run_statuses=("WAITING_FOR_INTERACTIVE_AUTH")
fi

iteration=0
while :; do
  iteration=$((iteration + 1))
  last_iteration="$iteration"
  printf '\n========== SSH_WAIT_ITERATION %s ==========\n' "$iteration"
  date '+ssh_wait_iteration_time=%Y-%m-%d %H:%M:%S %Z'

  probe_status=""
  probe_rc=0
  if [ "$LOCAL_DRY_RUN" = "1" ]; then
    dry_index=$((iteration - 1))
    if [ "$dry_index" -ge "${#dry_run_statuses[@]}" ]; then
      dry_index=$((${#dry_run_statuses[@]} - 1))
    fi
    probe_status="${dry_run_statuses[$dry_index]}"
    case "$probe_status" in
      PASS_REUSABLE_CONTROL_CONNECTION) probe_rc=0 ;;
      WAITING_FOR_INTERACTIVE_AUTH) probe_rc=3 ;;
      *) probe_rc=1 ;;
    esac
    echo "LOCAL_DRY_RUN probe_status=$probe_status probe_rc=$probe_rc"
  else
    echo "SSH_WAIT_PROBE_ACTION=START"
    set +e
    bash "$PROBE_SCRIPT"
    probe_rc=$?
    set -e
    echo "SSH_WAIT_PROBE_ACTION=DONE rc=$probe_rc"
    probe_status="$(python3 - "$ROOT_DIR/logs/mars56_noninteractive_ssh_probe/mars56_noninteractive_ssh_probe_latest.json" <<'PY' 2>/dev/null || true
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", "UNKNOWN"))
except Exception:
    print("UNKNOWN")
PY
)"
    echo "probe_status=$probe_status"
    echo "probe_rc=$probe_rc"
  fi

  if [ "$probe_status" = "PASS_REUSABLE_CONTROL_CONNECTION" ] || [ "$probe_rc" -eq 0 ]; then
    write_wait_status_json "SSH_READY" "$iteration" "$probe_status" "$probe_rc" "NA" "Reusable SSH connection is available."
    echo "SSH_WAIT_STATUS=SSH_READY"
    if [ "$START_ON_PASS" = "0" ]; then
      write_wait_status_json "PASS_DETECTED_NO_START" "$iteration" "$probe_status" "$probe_rc" "NA" "SSH ready; START_ON_PASS=0 so production launch was not started."
      echo "SSH_WAIT_STATUS=PASS_DETECTED_NO_START"
      exit 0
    fi
    echo "SSH_WAIT_ACTION=START_POST_DUO_1M"
    if [ "$LOCAL_DRY_RUN" = "1" ]; then
      printf 'LOCAL_DRY_RUN start_command='
      printf '%q ' env LOCAL_DRY_RUN=0 RUN_LOCAL_GATES=1 SYNC_REMOTE_STACK=1 START_WATCHER=1 RUN_LAUNCH_AUDIT=1 WATCH_ITERATIONS=0 SLEEP_SECONDS=1800 STOP_ON_GOAL_PASS=1 VERIFY_RUNNER_REQUIRED=1 RUN_ADAPTIVE_ACQUISITION=1 RUN_ADAPTIVE_EMX=1 ALLOW_MISMATCH=0 POST_DUO_LOG_CAPTURE=1 LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO=1 LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0 bash "$START_SCRIPT"
      printf '\n'
      write_wait_status_json "STARTED_DRY_RUN" "$iteration" "$probe_status" "$probe_rc" 0 "Dry-run saw SSH ready and would start the post-Duo 1M wrapper."
      echo "SSH_WAIT_STATUS=STARTED_DRY_RUN"
      exit 0
    fi
    set +e
    env \
      LOCAL_DRY_RUN=0 \
      RUN_LOCAL_GATES=1 \
      SYNC_REMOTE_STACK=1 \
      START_WATCHER=1 \
      RUN_LAUNCH_AUDIT=1 \
      WATCH_ITERATIONS=0 \
      SLEEP_SECONDS=1800 \
      STOP_ON_GOAL_PASS=1 \
      VERIFY_RUNNER_REQUIRED=1 \
      RUN_ADAPTIVE_ACQUISITION=1 \
      RUN_ADAPTIVE_EMX=1 \
      ALLOW_MISMATCH=0 \
      POST_DUO_LOG_CAPTURE=1 \
      LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO=1 \
      LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=0 \
      bash "$START_SCRIPT"
    start_rc=$?
    set -e
    if [ "$start_rc" -eq 0 ]; then
      write_wait_status_json "STARTED" "$iteration" "$probe_status" "$probe_rc" "$start_rc" "Post-Duo 1M wrapper completed launch handoff."
      echo "SSH_WAIT_STATUS=STARTED"
      exit 0
    fi
    write_wait_status_json "START_FAILED" "$iteration" "$probe_status" "$probe_rc" "$start_rc" "Post-Duo 1M wrapper failed after SSH became available."
    echo "SSH_WAIT_STATUS=START_FAILED rc=$start_rc"
    exit "$start_rc"
  fi

  if [ "$probe_status" = "WAITING_FOR_INTERACTIVE_AUTH" ] || [ "$probe_rc" -eq 3 ]; then
    write_wait_status_json "WAITING_FOR_INTERACTIVE_AUTH" "$iteration" "$probe_status" "$probe_rc" "NA" "Waiting for interactive SSH/Duo authentication or a reusable local SSH control connection."
    echo "SSH_WAIT_STATUS=WAITING_FOR_INTERACTIVE_AUTH"
  else
    write_wait_status_json "PROBE_FAILED" "$iteration" "$probe_status" "$probe_rc" "NA" "Probe failed before SSH became available."
    echo "SSH_WAIT_STATUS=PROBE_FAILED probe_rc=$probe_rc"
    if [ "$STOP_ON_PROBE_ERROR" = "1" ]; then
      exit "$probe_rc"
    fi
  fi

  if [ "$WAIT_ITERATIONS" != "0" ] && [ "$iteration" -ge "$WAIT_ITERATIONS" ]; then
    write_wait_status_json "REQUESTED_ITERATIONS_DONE" "$iteration" "$probe_status" "$probe_rc" "NA" "Requested wait iterations ended before production launch."
    echo "SSH_WAIT_STATUS=REQUESTED_ITERATIONS_DONE"
    exit 3
  fi

  echo "SSH_WAIT_SLEEP_SECONDS=$SLEEP_SECONDS"
  if [ "$SLEEP_SECONDS" != "0" ]; then
    sleep "$SLEEP_SECONDS"
  fi
done
