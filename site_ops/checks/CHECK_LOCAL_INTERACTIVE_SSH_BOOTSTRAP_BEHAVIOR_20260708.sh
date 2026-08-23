#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh.
# It never opens SSH. It verifies that the bootstrap command uses the same
# ControlMaster/ControlPath contract as the noninteractive probe and detached
# wait-for-SSH starter.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
BOOTSTRAP="$ROOT_DIR/START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh"
PROBE="$ROOT_DIR/CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh"
WAITER="$ROOT_DIR/RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh"

if [ ! -f "$BOOTSTRAP" ] || [ ! -f "$PROBE" ] || [ ! -f "$WAITER" ]; then
  echo "INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_STATUS=FAIL missing bootstrap/probe/waiter script" >&2
  exit 2
fi

bash -n "$BOOTSTRAP" "$PROBE" "$WAITER"

python3 - "$BOOTSTRAP" "$PROBE" "$WAITER" <<'PY'
import sys
from pathlib import Path

bootstrap = Path(sys.argv[1]).read_text(encoding="utf-8")
probe = Path(sys.argv[2]).read_text(encoding="utf-8")
waiter = Path(sys.argv[3]).read_text(encoding="utf-8")

required_bootstrap_tokens = [
    "CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh",
    'CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"',
    'SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"',
    'ControlMaster=auto',
    'ControlPersist=${SSH_PERSIST}',
    'ControlPath=${SSH_CONTROL_PATH}',
    '-J "${USER_NAME}@${JUMP_HOST}"',
    'PASS_REUSABLE_CONTROL_CONNECTION',
    'LOCAL_DRY_RUN',
    'RUN_PROBE_AFTER',
    'MARS56_INTERACTIVE_BOOTSTRAP_REMOTE_READY',
    'MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=DRY_RUN_COMMAND_READY',
]
required_probe_tokens = [
    'CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"',
    'SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"',
    'ControlMaster=auto',
    'ControlPersist={ssh_persist}',
    'ControlPath={ssh_control_path}',
]
required_waiter_tokens = [
    'PASS_REUSABLE_CONTROL_CONNECTION',
    'START_POST_DUO_1M',
    'CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh',
]
missing = []
missing += [f"bootstrap:{token}" for token in required_bootstrap_tokens if token not in bootstrap]
missing += [f"probe:{token}" for token in required_probe_tokens if token not in probe]
missing += [f"waiter:{token}" for token in required_waiter_tokens if token not in waiter]
if missing:
    print("INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_STATUS=FAIL")
    print("missing_tokens=" + ",".join(missing))
    raise SystemExit(1)
print("INTERACTIVE_SSH_BOOTSTRAP_CASE=static_contract status=PASS")
PY

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_interactive_ssh_bootstrap.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT
OUT_JSON="$TMP_ROOT/status.json"
CONTROL_DIR="$TMP_ROOT/mux"
LOCAL_DRY_RUN=1 \
  OUT_JSON="$OUT_JSON" \
  CONTROL_DIR="$CONTROL_DIR" \
  bash "$BOOTSTRAP" >"$TMP_ROOT/dry_run.out"

python3 - "$OUT_JSON" "$TMP_ROOT/dry_run.out" "$CONTROL_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
stdout_path = Path(sys.argv[2])
control_dir = Path(sys.argv[3])

data = json.loads(status_path.read_text(encoding="utf-8"))
stdout = stdout_path.read_text(encoding="utf-8")
assert data["state"] == "DRY_RUN_COMMAND_READY", data
assert data["local_dry_run"] is True, data
assert data["run_probe_after"] is True, data
assert os.path.normpath(data["ssh_control_path"]) == os.path.normpath(str(control_dir / "%C")), data
cmd = data["ssh_command"]
assert cmd[0] == "ssh", cmd
assert "-J" in cmd, cmd
assert "researcher@login.example.edu" in cmd, cmd
assert "researcher@mars.example.edu" in cmd, cmd
assert "MARS56_INTERACTIVE_BOOTSTRAP_REMOTE_READY" in " ".join(cmd), cmd
assert "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=DRY_RUN_COMMAND_READY" in stdout, stdout
print("INTERACTIVE_SSH_BOOTSTRAP_CASE=dry_run_writes_status status=PASS")
PY

echo "INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_STATUS=PASS"
