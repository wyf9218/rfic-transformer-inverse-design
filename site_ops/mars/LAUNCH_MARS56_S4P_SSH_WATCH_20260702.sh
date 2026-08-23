#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/mars56_s4p_ssh_watch_launch_${STAMP}.log}"

nohup "$ROOT/WATCH_AND_RUN_MARS56_S4P_REMOTE_20260702.sh" > "$LOG_FILE" 2>&1 < /dev/null &
pid=$!

echo "CODEX_MARS56_S4P_WATCH_LAUNCHED pid=$pid"
echo "LOG_FILE=$LOG_FILE"
