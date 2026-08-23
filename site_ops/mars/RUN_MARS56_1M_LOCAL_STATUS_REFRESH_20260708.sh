#!/usr/bin/env bash
set -euo pipefail

# Periodically refreshes the local status summary for the MARS56 1M campaign.
#
# This is a local monitor only. It does not mutate remote production state. By
# default it runs the read-only BatchMode SSH probe, then regenerates
# reports/mars56_1m_current_status_latest_{CN.md,json}.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SUMMARY_SCRIPT="${SUMMARY_SCRIPT:-$ROOT_DIR/SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh}"
REFRESH_ITERATIONS="${REFRESH_ITERATIONS:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"
RUN_PROBE_EACH_REFRESH="${RUN_PROBE_EACH_REFRESH:-1}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
REFRESH_LOG_CAPTURE="${REFRESH_LOG_CAPTURE:-1}"
REFRESH_LOG_DIR="${REFRESH_LOG_DIR:-$ROOT_DIR/logs/mars56_1m_local_status_refresh}"
REFRESH_STATUS_JSON="${REFRESH_STATUS_JSON:-$REFRESH_LOG_DIR/mars56_1m_local_status_refresh_latest_status.json}"

case "$REFRESH_ITERATIONS" in
  ''|*[!0-9]*) echo "ERROR: REFRESH_ITERATIONS must be a non-negative integer." >&2; exit 2 ;;
esac
case "$SLEEP_SECONDS" in
  ''|*[!0-9]*) echo "ERROR: SLEEP_SECONDS must be a non-negative integer." >&2; exit 2 ;;
esac
case "$RUN_PROBE_EACH_REFRESH" in 0|1) ;;
  *) echo "ERROR: RUN_PROBE_EACH_REFRESH must be 0 or 1." >&2; exit 2 ;;
esac
case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$REFRESH_LOG_CAPTURE" in 0|1) ;;
  *) echo "ERROR: REFRESH_LOG_CAPTURE must be 0 or 1." >&2; exit 2 ;;
esac

mkdir -p "$REFRESH_LOG_DIR"

sha256_optional() {
  if [ -f "$1" ]; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    printf ''
  fi
}

SUMMARY_SCRIPT_SHA="$(sha256_optional "$SUMMARY_SCRIPT")"

write_refresh_status_json() {
  local state="$1"
  local iteration="$2"
  local summary_rc="$3"
  local message="$4"
  python3 - "$REFRESH_STATUS_JSON" "$state" "$iteration" "$summary_rc" "$message" "$SLEEP_SECONDS" "$RUN_PROBE_EACH_REFRESH" "$LOCAL_DRY_RUN" "$SUMMARY_SCRIPT" "$SUMMARY_SCRIPT_SHA" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": sys.argv[2],
    "iteration": int(sys.argv[3]),
    "summary_return_code": None if sys.argv[4] == "NA" else int(sys.argv[4]),
    "message": sys.argv[5],
    "sleep_seconds": int(sys.argv[6]),
    "run_probe_each_refresh": sys.argv[7] == "1",
    "local_dry_run": sys.argv[8] == "1",
    "summary_script": sys.argv[9],
    "summary_script_sha256": sys.argv[10],
    "summary_json": "reports/mars56_1m_current_status_latest.json",
    "summary_markdown": "reports/mars56_1m_current_status_latest_CN.md",
}
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
PY
}

run_refresh_once() {
  local iteration="$1"
  if [ "$LOCAL_DRY_RUN" = "1" ]; then
    echo "LOCAL_STATUS_REFRESH_DRY_RUN iteration=$iteration run_probe=$RUN_PROBE_EACH_REFRESH"
    return 0
  fi
  RUN_PROBE="$RUN_PROBE_EACH_REFRESH" bash "$SUMMARY_SCRIPT"
}

main() {
  if [ ! -f "$SUMMARY_SCRIPT" ]; then
    echo "ERROR: missing summary script: $SUMMARY_SCRIPT" >&2
    write_refresh_status_json "FAILED" 0 2 "Missing summary script."
    exit 2
  fi

  echo "MARS56 1M local status refresh"
  echo "summary_script=$SUMMARY_SCRIPT"
  echo "summary_script_sha256=$SUMMARY_SCRIPT_SHA"
  echo "refresh_iterations=$REFRESH_ITERATIONS"
  echo "sleep_seconds=$SLEEP_SECONDS"
  echo "run_probe_each_refresh=$RUN_PROBE_EACH_REFRESH"
  echo "local_dry_run=$LOCAL_DRY_RUN"
  echo "refresh_status_json=$REFRESH_STATUS_JSON"
  echo "note=REFRESH_ITERATIONS=0 means refresh forever."

  local iteration=0
  while true; do
    iteration=$((iteration + 1))
    echo
    echo "========== LOCAL_STATUS_REFRESH_ITERATION $iteration =========="
    date '+refresh_time=%Y-%m-%d %H:%M:%S %Z'
    set +e
    run_refresh_once "$iteration"
    summary_rc=$?
    set -e
    if [ "$summary_rc" -eq 0 ]; then
      write_refresh_status_json "REFRESHED" "$iteration" "$summary_rc" "Local status summary refreshed."
      if [ "$LOCAL_DRY_RUN" = "0" ]; then
        RUN_PROBE=0 bash "$SUMMARY_SCRIPT" >/dev/null 2>&1 || true
      fi
      echo "LOCAL_STATUS_REFRESH_STATUS=REFRESHED"
    else
      write_refresh_status_json "FAILED" "$iteration" "$summary_rc" "Local status summary refresh failed."
      if [ "$LOCAL_DRY_RUN" = "0" ]; then
        RUN_PROBE=0 bash "$SUMMARY_SCRIPT" >/dev/null 2>&1 || true
      fi
      echo "LOCAL_STATUS_REFRESH_STATUS=FAILED rc=$summary_rc"
    fi

    if [ "$REFRESH_ITERATIONS" != "0" ] && [ "$iteration" -ge "$REFRESH_ITERATIONS" ]; then
      write_refresh_status_json "REQUESTED_ITERATIONS_DONE" "$iteration" "$summary_rc" "Requested local status refresh iterations completed."
      if [ "$LOCAL_DRY_RUN" = "0" ]; then
        RUN_PROBE=0 bash "$SUMMARY_SCRIPT" >/dev/null 2>&1 || true
      fi
      echo "LOCAL_STATUS_REFRESH_STATUS=REQUESTED_ITERATIONS_DONE"
      exit "$summary_rc"
    fi
    echo "LOCAL_STATUS_REFRESH_SLEEP_SECONDS=$SLEEP_SECONDS"
    sleep "$SLEEP_SECONDS"
  done
}

if [ "$REFRESH_LOG_CAPTURE" = "1" ]; then
  log_path="$REFRESH_LOG_DIR/mars56_1m_local_status_refresh_$(date +%Y%m%d_%H%M%S).log"
  exec > >(tee -a "$log_path") 2>&1
  echo "local_status_refresh_log=$log_path"
fi

main
