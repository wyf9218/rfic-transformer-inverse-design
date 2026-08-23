#!/usr/bin/env bash
set -euo pipefail

# Read-only probe for the MARS56 production path.
# It never stores a password and uses BatchMode=yes, so it will not block on
# password/Duo prompts. A PASS means an existing SSH control connection can be
# reused; WAITING means interactive authentication is still required.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
USER_NAME="${USER_NAME:-researcher}"
JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
SSH_PERSIST="${SSH_PERSIST:-30m}"
CONTROL_DIR="${CONTROL_DIR:-/tmp/mars56_ssh_mux_$(id -u)}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-${CONTROL_DIR}/%C}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-12}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/logs/mars56_noninteractive_ssh_probe}"
OUT_JSON="${OUT_JSON:-$OUT_DIR/mars56_noninteractive_ssh_probe_latest.json}"
LIVE_STATUS_JSON="${LIVE_STATUS_JSON:-$ROOT_DIR/reports/mars56_million_campaign_live_status.json}"

case "$CONNECT_TIMEOUT" in
  ''|*[!0-9]*) echo "ERROR: CONNECT_TIMEOUT must be a positive integer." >&2; exit 2 ;;
esac
for value in "$USER_NAME" "$JUMP_HOST" "$MARS_HOST" "$SSH_PERSIST" "$CONTROL_DIR" "$SSH_CONTROL_PATH" "$OUT_DIR" "$OUT_JSON" "$LIVE_STATUS_JSON"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: probe settings contain unsupported newline characters." >&2
    exit 2
  fi
done

mkdir -p "$OUT_DIR" "$CONTROL_DIR"

python3 - "$USER_NAME" "$JUMP_HOST" "$MARS_HOST" "$SSH_PERSIST" "$SSH_CONTROL_PATH" "$CONNECT_TIMEOUT" "$OUT_JSON" "$LIVE_STATUS_JSON" <<'PY'
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

(
    user_name,
    jump_host,
    mars_host,
    ssh_persist,
    ssh_control_path,
    connect_timeout,
    out_json,
    live_status_json,
) = sys.argv[1:]

connect_timeout_int = int(connect_timeout)
out_path = Path(out_json)
live_path = Path(live_status_json)
out_path.parent.mkdir(parents=True, exist_ok=True)

base_opts = [
    "-o", "BatchMode=yes",
    "-o", f"ConnectTimeout={connect_timeout_int}",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ControlMaster=auto",
    "-o", f"ControlPersist={ssh_persist}",
    "-o", f"ControlPath={ssh_control_path}",
]

def run_probe(name, cmd):
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=connect_timeout_int + 8,
        )
        return {
            "name": name,
            "command": " ".join(cmd),
            "return_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "command": " ".join(cmd),
            "return_code": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "timeout_expired": True,
        }

jump_target = f"{user_name}@{jump_host}"
mars_target = f"{user_name}@{mars_host}"
remote_readonly = "hostname; date '+%Y-%m-%d %H:%M:%S %Z'; true"

jump = run_probe("jump_host_batchmode", ["ssh", *base_opts, jump_target, remote_readonly])
mars = run_probe("mars_via_jump_batchmode", ["ssh", *base_opts, "-J", jump_target, mars_target, remote_readonly])

combined = "\n".join(
    str(x.get(k, ""))
    for x in (jump, mars)
    for k in ("stdout", "stderr")
)
if mars.get("return_code") == 0:
    status = "PASS_REUSABLE_CONTROL_CONNECTION"
    interpretation = "BatchMode SSH can reach MARS now; remote state can be queried without a new interactive Duo step."
    reconnect_needed = False
elif "Permission denied" in combined or "keyboard-interactive" in combined:
    status = "WAITING_FOR_INTERACTIVE_AUTH"
    interpretation = "No reusable local SSH control connection is available; an interactive SSH login with password/Duo is required before this local automation can verify remote MARS state. A Guacamole web session alone does not create that local SSH control connection."
    reconnect_needed = True
elif "Operation timed out" in combined or "Connection timed out" in combined or jump.get("timeout_expired") or mars.get("timeout_expired"):
    status = "NETWORK_TIMEOUT"
    interpretation = "SSH did not reach the remote path within the timeout; this is a connectivity/authentication availability issue, not proof of MARS production progress."
    reconnect_needed = True
else:
    status = "FAIL_OTHER"
    interpretation = "Noninteractive SSH probe failed for a reason other than the expected Duo/keyboard-interactive path; inspect stderr in the probe JSON."
    reconnect_needed = True

now_cdt = datetime.now().strftime("%Y-%m-%d %H:%M:%S CDT")
result = {
    "updated_at_cdt": now_cdt,
    "status": status,
    "interpretation": interpretation,
    "user_name": user_name,
    "jump_host": jump_host,
    "mars_host": mars_host,
    "ssh_control_path": ssh_control_path,
    "ssh_persist": ssh_persist,
    "connect_timeout_seconds": connect_timeout_int,
    "jump_probe": jump,
    "mars_probe": mars,
}
out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

if live_path.exists():
    try:
        live = json.loads(live_path.read_text(encoding="utf-8"))
    except Exception:
        live = {}
else:
    live = {}

live["updated_at_cdt"] = f"{now_cdt}: noninteractive SSH probe status={status}; remote 1M completion still unproven."
live["remote_connection_status"] = interpretation
live["remote_reconnect_needed"] = reconnect_needed
live["latest_remote_verification_status"] = status
live["latest_noninteractive_ssh_probe"] = {
    "time_cdt": now_cdt,
    "status": status,
    "command": mars["command"],
    "return_code": mars.get("return_code"),
    "result": (mars.get("stderr") or mars.get("stdout") or "")[-600:],
    "interpretation": interpretation,
    "report_json": str(out_path),
}
live_path.write_text(json.dumps(live, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print("MARS56_NONINTERACTIVE_SSH_PROBE_JSON=" + str(out_path))
print("MARS56_NONINTERACTIVE_SSH_PROBE_STATUS=" + status)
print("MARS56_NONINTERACTIVE_SSH_PROBE_INTERPRETATION=" + interpretation)

if status == "PASS_REUSABLE_CONTROL_CONNECTION":
    raise SystemExit(0)
if status == "WAITING_FOR_INTERACTIVE_AUTH":
    raise SystemExit(3)
raise SystemExit(1)
PY
