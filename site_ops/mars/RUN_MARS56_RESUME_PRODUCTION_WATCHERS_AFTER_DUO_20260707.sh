#!/usr/bin/env bash
set -euo pipefail

# Resume the existing remote production watcher scripts after Duo is available.
#
# This script does not reimplement queue selection, EMX generation, or physical
# feature logic. It only checks whether the already-created MARS watcher scripts
# are present/running and restarts missing watcher processes from those scripts.
#
# Dry run:
#   DRY_RUN=1 bash RUN_MARS56_RESUME_PRODUCTION_WATCHERS_AFTER_DUO_20260707.sh
#
# Resume if needed:
#   bash RUN_MARS56_RESUME_PRODUCTION_WATCHERS_AFTER_DUO_20260707.sh

JUMP_HOST="login.example.edu"
MARS_HOST="mars.example.edu"
USER_NAME="researcher"
DRY_RUN="${DRY_RUN:-0}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

case "$DRY_RUN" in 0|1) ;;
  *) echo "ERROR: DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
if [[ "$SSH_CONTROL_PATH" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: SSH_CONTROL_PATH contains unsupported newline characters." >&2
  exit 2
fi

SSH_ARGS=(-tt)
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_RUN <<'REMOTE' || true
set -euo pipefail

BASE=/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
U8=$BASE/status/accepted_inrange_pool_after_chunk08_20260706/physical_feature_uniformity/physical_feature_uniformity_summary.json
DATA8=$BASE/datasets/chunk_08_accepted_pool_after_chunk07_qgap_nearest_8000_widthfix
FIRST100K=$BASE/datasets/chunk_001_100k_after_chunk08_pass
DRY_RUN="${DRY_RUN:-0}"

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'dry_run=%s\n' "$DRY_RUN"
echo "purpose=restart_existing_remote_watchers_only_no_new_generation_logic"

if [ ! -d "$BASE" ]; then
  echo "WATCHER_RESUME_STATUS=FAIL_BASE_MISSING"
  exit 1
fi

printf '\n-- current gate snapshot --\n'
printf 'chunk08_nonempty='; find "$DATA8" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l
printf 'chunk08_empty_stale15='; find "$DATA8" -type f -name '*.s4p' -size 0c -mmin +15 2>/dev/null | wc -l
if [ -f "$U8" ]; then
  "$PY" - "$U8" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:
    print("U8_parse_error=", type(exc).__name__)
else:
    print("U8_overall_status=", d.get("overall_status"))
    print("U8_valid_feature_count=", d.get("valid_feature_count"))
    fd = d.get("four_dimensional_uniformity", {})
    print("U8_4d_occupied_fraction=", fd.get("occupied_fraction"))
PY
else
  echo "U8_missing"
fi
if [ -d "$FIRST100K" ]; then
  printf 'first100k_nonempty='; find "$FIRST100K" -type f -name '*.s4p' -size +0c 2>/dev/null | wc -l
else
  echo "first100k_dataset_missing_or_not_launched"
fi

resume_watcher() {
  local name="$1"
  local script="$2"
  local log="$3"

  printf '\n-- watcher %s --\n' "$name"
  printf 'watcher_name=%s\n' "$name"
  printf 'watcher_script=%s\n' "$script"
  printf 'watcher_log=%s\n' "$log"

  if [ ! -f "$script" ]; then
    echo "WATCHER_STATE name=$name state=MISSING_SCRIPT"
    return 0
  fi

  local count
  count=$(ps -fu researcher | grep -F "$script" | grep -v grep | wc -l | tr -d ' ')
  printf 'watcher_process_count=%s\n' "$count"
  if [ "$count" -gt 0 ]; then
    ps -fu researcher | grep -F "$script" | grep -v grep || true
    echo "WATCHER_STATE name=$name state=ALREADY_RUNNING"
    return 0
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "WATCHER_STATE name=$name state=WOULD_START"
    return 0
  fi

  mkdir -p "$(dirname "$log")"
  nohup bash "$script" >> "$log" 2>&1 &
  local pid=$!
  sleep 2
  local after
  after=$(ps -fu researcher | grep -F "$script" | grep -v grep | wc -l | tr -d ' ')
  printf 'watcher_started_pid=%s\n' "$pid"
  printf 'watcher_process_count_after_start=%s\n' "$after"
  if [ "$after" -gt 0 ]; then
    echo "WATCHER_STATE name=$name state=STARTED"
  else
    echo "WATCHER_STATE name=$name state=START_ATTEMPT_NO_PROCESS"
  fi
}

resume_watcher \
  chunk08_checkpoint \
  "$BASE/status/watch_chunk08_checkpoint_merge_accept_20260706.sh" \
  "$BASE/logs/watch_chunk08_checkpoint_merge_accept_20260706.resume.log"

resume_watcher \
  chunk08_pass_first100k_launcher \
  "$BASE/status/watch_chunk08_pass_prepare_and_launch_first_100k_20260706.sh" \
  "$BASE/logs/watch_chunk08_pass_prepare_and_launch_first_100k_20260706.resume.log"

resume_watcher \
  production_chunks_02_to_10_after_chunk08 \
  "$BASE/status/watch_production_100k_chunks_02_to_10_after_chunk08_20260706.sh" \
  "$BASE/logs/watch_production_100k_chunks_02_to_10_after_chunk08_20260706.resume.log"

echo "WATCHER_RESUME_STATUS=PASS"
REMOTE

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "This resumes existing MARS production watcher scripts only. DRY_RUN=${DRY_RUN}."
ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" \
  "DRY_RUN='${DRY_RUN}' ssh -tt ${MARS_HOST} 'DRY_RUN='\\''${DRY_RUN}'\\'' bash -s'" \
  <<<"$REMOTE_RUN"
