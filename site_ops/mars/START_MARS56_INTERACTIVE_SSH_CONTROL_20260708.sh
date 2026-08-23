#!/usr/bin/env bash
set -euo pipefail

# Interactive SSH bootstrap for the MARS56 1M campaign.
#
# Purpose:
#   Create the same local SSH ControlMaster socket used by
#   CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh and the detached
#   wait-for-SSH starter. After the user completes password/Duo here, the
#   background waiter can detect PASS_REUSABLE_CONTROL_CONNECTION and launch
#   the post-Duo 1M production/checkpoint chain automatically.
#
# Print the exact command without connecting:
#   LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh
#
# Real interactive bootstrap:
#   bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
USER_NAME="${USER_NAME:-researcher}"
JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
# Keep the Unix-domain socket path short on macOS; $TMPDIR is long enough to
# exceed OpenSSH's sockaddr_un limit. %C also gives each endpoint its own hash.
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
RUN_PROBE_AFTER="${RUN_PROBE_AFTER:-1}"
KEEP_REMOTE_SHELL="${KEEP_REMOTE_SHELL:-0}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-30}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/logs/mars56_interactive_ssh_bootstrap}"
OUT_JSON="${OUT_JSON:-$OUT_DIR/mars56_interactive_ssh_bootstrap_latest.json}"
PROBE_SCRIPT="${PROBE_SCRIPT:-$ROOT_DIR/CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_PROBE_AFTER" in 0|1) ;;
  *) echo "ERROR: RUN_PROBE_AFTER must be 0 or 1." >&2; exit 2 ;;
esac
case "$KEEP_REMOTE_SHELL" in 0|1) ;;
  *) echo "ERROR: KEEP_REMOTE_SHELL must be 0 or 1." >&2; exit 2 ;;
esac
case "$CONNECT_TIMEOUT" in
  ''|*[!0-9]*) echo "ERROR: CONNECT_TIMEOUT must be a positive integer." >&2; exit 2 ;;
esac
for value in "$USER_NAME" "$JUMP_HOST" "$MARS_HOST" "$SSH_PERSIST" "$CONTROL_DIR" "$SSH_CONTROL_PATH" "$OUT_DIR" "$OUT_JSON" "$PROBE_SCRIPT"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: SSH bootstrap settings contain unsupported newline characters." >&2
    exit 2
  fi
done
if [ ! -f "$PROBE_SCRIPT" ]; then
  echo "ERROR: probe script missing: $PROBE_SCRIPT" >&2
  exit 2
fi

mkdir -p "$CONTROL_DIR" "$OUT_DIR"

if [ "$KEEP_REMOTE_SHELL" = "1" ]; then
  REMOTE_CMD='hostname; date "+%Y-%m-%d %H:%M:%S %Z"; echo MARS56_INTERACTIVE_BOOTSTRAP_REMOTE_READY; exec ${SHELL:-/bin/bash} -l'
else
  REMOTE_CMD='hostname; date "+%Y-%m-%d %H:%M:%S %Z"; echo MARS56_INTERACTIVE_BOOTSTRAP_REMOTE_READY'
fi

SSH_CMD=(
  ssh
  -tt
  -o "StrictHostKeyChecking=accept-new"
  -o "ConnectTimeout=${CONNECT_TIMEOUT}"
  -o "ControlMaster=auto"
  -o "ControlPersist=${SSH_PERSIST}"
  -o "ControlPath=${SSH_CONTROL_PATH}"
  -J "${USER_NAME}@${JUMP_HOST}"
  "${USER_NAME}@${MARS_HOST}"
  "$REMOTE_CMD"
)

write_status_json() {
  local state="$1"
  local ssh_rc="$2"
  local probe_rc="$3"
  local message="$4"
  python3 - "$OUT_JSON" "$state" "$ssh_rc" "$probe_rc" "$message" "$USER_NAME" "$JUMP_HOST" "$MARS_HOST" "$SSH_PERSIST" "$CONTROL_DIR" "$SSH_CONTROL_PATH" "$LOCAL_DRY_RUN" "$RUN_PROBE_AFTER" "$KEEP_REMOTE_SHELL" "$PROBE_SCRIPT" "${SSH_CMD[@]}" <<'PY'
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    path,
    state,
    ssh_rc,
    probe_rc,
    message,
    user_name,
    jump_host,
    mars_host,
    ssh_persist,
    control_dir,
    ssh_control_path,
    local_dry_run,
    run_probe_after,
    keep_remote_shell,
    probe_script,
    *ssh_cmd,
) = sys.argv[1:]

def maybe_int(value):
    if value in {"", "NA"}:
        return None
    try:
        return int(value)
    except Exception:
        return value

path_obj = Path(path)
path_obj.parent.mkdir(parents=True, exist_ok=True)
data = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "state": state,
    "ssh_return_code": maybe_int(ssh_rc),
    "probe_return_code": maybe_int(probe_rc),
    "message": message,
    "user_name": user_name,
    "jump_host": jump_host,
    "mars_host": mars_host,
    "ssh_persist": ssh_persist,
    "control_dir": control_dir,
    "ssh_control_path": ssh_control_path,
    "local_dry_run": local_dry_run == "1",
    "run_probe_after": run_probe_after == "1",
    "keep_remote_shell": keep_remote_shell == "1",
    "probe_script": probe_script,
    "ssh_command": ssh_cmd,
    "ssh_command_quoted": " ".join(shlex.quote(part) for part in ssh_cmd),
}
path_obj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

echo "MARS56 interactive SSH ControlMaster bootstrap"
echo "user_name=$USER_NAME"
echo "jump_host=$JUMP_HOST"
echo "mars_host=$MARS_HOST"
echo "ssh_control_path=$SSH_CONTROL_PATH"
echo "ssh_persist=$SSH_PERSIST"
echo "local_dry_run=$LOCAL_DRY_RUN"
echo "run_probe_after=$RUN_PROBE_AFTER"
echo "keep_remote_shell=$KEEP_REMOTE_SHELL"
echo "status_json=$OUT_JSON"
printf 'ssh_command='
printf '%q ' "${SSH_CMD[@]}"
printf '\n'

if [ "$LOCAL_DRY_RUN" = "1" ]; then
  write_status_json "DRY_RUN_COMMAND_READY" "NA" "NA" "Dry-run only; no SSH connection attempted."
  echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=DRY_RUN_COMMAND_READY"
  exit 0
fi

echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_ACTION=OPEN_INTERACTIVE_SSH"
echo "Approve password/Duo if prompted. Guacamole alone does not create this local reusable control socket."
set +e
"${SSH_CMD[@]}"
ssh_rc=$?
set -e

if [ "$ssh_rc" -ne 0 ]; then
  write_status_json "SSH_BOOTSTRAP_FAILED" "$ssh_rc" "NA" "Interactive SSH command failed before a reusable control connection was proven."
  echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=SSH_BOOTSTRAP_FAILED rc=$ssh_rc"
  exit "$ssh_rc"
fi

probe_rc="NA"
if [ "$RUN_PROBE_AFTER" = "1" ]; then
  echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_ACTION=VERIFY_REUSABLE_CONTROL_CONNECTION"
  set +e
  CONTROL_DIR="$CONTROL_DIR" SSH_CONTROL_PATH="$SSH_CONTROL_PATH" SSH_PERSIST="$SSH_PERSIST" bash "$PROBE_SCRIPT"
  probe_rc=$?
  set -e
  if [ "$probe_rc" -eq 0 ]; then
    write_status_json "PASS_REUSABLE_CONTROL_CONNECTION" "$ssh_rc" "$probe_rc" "Interactive SSH completed and the BatchMode probe can reuse the control connection."
    echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION"
    exit 0
  fi
  write_status_json "PROBE_AFTER_BOOTSTRAP_FAILED" "$ssh_rc" "$probe_rc" "Interactive SSH command exited successfully, but the BatchMode probe could not reuse the connection."
  echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PROBE_AFTER_BOOTSTRAP_FAILED probe_rc=$probe_rc"
  exit "$probe_rc"
fi

write_status_json "SSH_BOOTSTRAP_COMMAND_COMPLETED" "$ssh_rc" "$probe_rc" "Interactive SSH completed; RUN_PROBE_AFTER=0 so reusable connection was not verified."
echo "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=SSH_BOOTSTRAP_COMMAND_COMPLETED"
