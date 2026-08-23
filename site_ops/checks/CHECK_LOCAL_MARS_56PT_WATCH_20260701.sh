#!/usr/bin/env bash
set -euo pipefail

LABEL="edu.example.mars56ptwatch"
TMPROOT="/tmp/mars56ptwatch"
UID_VALUE="$(id -u)"

echo "LOCAL_MARS_56PT_WATCH_STATUS $(date)"
echo "launch_label=$LABEL"
echo "tmp_root=$TMPROOT"

echo
echo "LAUNCHD_STATE:"
launchctl print "gui/$UID_VALUE/$LABEL" 2>&1 | sed -n '1,80p' || true

echo
echo "PROCESSES:"
pgrep -fl 'WATCH_AND_RUN_MARS_56PT_GROUNDED_TAP_20260701|LAUNCHD_MARS_56PT_WATCH' || true

echo
echo "LATEST_WATCH_LOG:"
latest="$(ls -t "$TMPROOT"/logs/mars_56pt_grounded_tap_watch_launchd_*.log 2>/dev/null | head -1 || true)"
if [[ -n "$latest" ]]; then
  echo "$latest"
  tail -80 "$latest"
else
  echo "NO_LAUNCHD_WATCH_LOG_FOUND"
fi

echo
echo "TMP_OUTPUTS:"
find "$TMPROOT/outputs" -maxdepth 4 -type f 2>/dev/null | sort | tail -80 || true

echo
echo "STDERR:"
cat "$TMPROOT/logs/mars_56pt_launchd_stderr.log" 2>/dev/null || true
