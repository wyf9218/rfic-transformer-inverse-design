#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
cd "$ROOT_DIR"

STAMP="launchd_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$ROOT_DIR/logs/mars_56pt_grounded_tap_watch_${STAMP}.log"

set +e
env \
  DAEMON_MODE=1 \
  ATTEMPTS="${ATTEMPTS:-5000}" \
  SLEEP_SECONDS="${SLEEP_SECONDS:-300}" \
  CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-10}" \
  STAMP="$STAMP" \
  LOG_FILE="$LOG_FILE" \
  bash "$ROOT_DIR/WATCH_AND_RUN_MARS_56PT_GROUNDED_TAP_20260701.sh"

rc=$?
set -e
echo "LAUNCHD_MARS_56PT_WATCH_EXIT rc=$rc $(date)" >> "$LOG_FILE"
exit "$rc"
