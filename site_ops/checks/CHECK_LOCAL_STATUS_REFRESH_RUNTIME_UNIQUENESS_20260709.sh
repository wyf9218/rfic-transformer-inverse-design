#!/usr/bin/env bash
set -euo pipefail

# Runtime guard for the local status refresh monitor.
#
# Local-only: this does not SSH to MARS and does not mutate remote production.
# It proves that exactly one local status refresh screen is active, that no old
# orphan runner can overwrite the latest status JSON, and that the refresh JSON
# is backed by a live local refresh process.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/logs/mars56_1m_local_status_refresh}"
SCREEN_SESSION="${SCREEN_SESSION:-mars56_1m_local_status_refresh}"
LAUNCHER_SCRIPT="${LAUNCHER_SCRIPT:-$ROOT_DIR/START_MARS56_1M_LOCAL_STATUS_REFRESH_DETACHED_20260708.sh}"
REFRESH_STATUS_JSON="${REFRESH_STATUS_JSON:-$STATE_DIR/mars56_1m_local_status_refresh_latest_status.json}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$STATE_DIR/mars56_1m_local_status_refresh_latest_detached_status.json}"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-$STATE_DIR/mars56_1m_local_status_refresh_screen_runner.sh}"
RUN_SCRIPT="${RUN_SCRIPT:-$ROOT_DIR/RUN_MARS56_1M_LOCAL_STATUS_REFRESH_20260708.sh}"
SUMMARY_SCRIPT="${SUMMARY_SCRIPT:-$ROOT_DIR/SUMMARIZE_MARS56_1M_LOCAL_STATUS_20260708.sh}"
EXPECTED_SLEEP_SECONDS="${EXPECTED_SLEEP_SECONDS:-300}"
EXPECTED_RUN_PROBE_EACH_REFRESH="${EXPECTED_RUN_PROBE_EACH_REFRESH:-1}"
EXPECTED_LOCAL_DRY_RUN="${EXPECTED_LOCAL_DRY_RUN:-0}"
MAX_AGE_SECONDS="${MAX_AGE_SECONDS:-900}"

for path in "$LAUNCHER_SCRIPT" "$REFRESH_STATUS_JSON" "$DETACHED_STATUS_JSON" "$RUNNER_SCRIPT" "$RUN_SCRIPT" "$SUMMARY_SCRIPT"; do
  if [ ! -e "$path" ]; then
    echo "LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_STATUS=FAIL missing_path=$path"
    exit 1
  fi
done

ACTION=status \
SCREEN_SESSION="$SCREEN_SESSION" \
LOG_DIR="$STATE_DIR" \
DETACHED_STATUS_JSON="$DETACHED_STATUS_JSON" \
REFRESH_STATUS_JSON="$REFRESH_STATUS_JSON" \
bash "$LAUNCHER_SCRIPT" >/dev/null 2>&1 || true

python3 - "$SCREEN_SESSION" "$REFRESH_STATUS_JSON" "$DETACHED_STATUS_JSON" "$RUNNER_SCRIPT" "$RUN_SCRIPT" "$SUMMARY_SCRIPT" "$EXPECTED_SLEEP_SECONDS" "$EXPECTED_RUN_PROBE_EACH_REFRESH" "$EXPECTED_LOCAL_DRY_RUN" "$MAX_AGE_SECONDS" <<'PY'
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    screen_session,
    refresh_status_json,
    detached_status_json,
    runner_script,
    run_script,
    summary_script,
    expected_sleep_seconds,
    expected_run_probe_each_refresh,
    expected_local_dry_run,
    max_age_seconds,
) = sys.argv[1:]
expected_sleep_seconds = int(expected_sleep_seconds)
expected_run_probe_each_refresh = expected_run_probe_each_refresh == "1"
expected_local_dry_run = expected_local_dry_run == "1"
max_age_seconds = int(max_age_seconds)

def fail(message):
    print(f"LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_STATUS=FAIL {message}")
    raise SystemExit(1)

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"json_load_error path={path} error={type(exc).__name__}")

def parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

screen_output = subprocess.run(
    ["screen", "-ls"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
screen_lines = [
    line.strip()
    for line in screen_output.stdout.splitlines()
    if f".{screen_session}" in line
]
if len(screen_lines) != 1:
    fail(f"screen_session_count={len(screen_lines)} expected=1 screen_session={screen_session!r}")

ps_output = subprocess.check_output(
    ["ps", "-eo", "pid=,ppid=,command="],
    text=True,
    stderr=subprocess.DEVNULL,
)
runner_login_pids = []
orphan_runner_login_pids = []
run_script_pids = []
summary_script_pids = []
tee_pids = []
for raw_line in ps_output.splitlines():
    parts = raw_line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    pid_s, ppid_s, command = parts
    try:
        pid = int(pid_s)
        ppid = int(ppid_s)
    except ValueError:
        continue
    if runner_script in command and "login -pflq" in command:
        runner_login_pids.append(pid)
        if ppid == 1:
            orphan_runner_login_pids.append(pid)
    if run_script in command and "bash" in command:
        run_script_pids.append(pid)
    if summary_script in command:
        summary_script_pids.append(pid)
    if "tee -a" in command and "mars56_1m_local_status_refresh" in command:
        tee_pids.append(pid)

if len(runner_login_pids) != 1:
    fail(f"runner_login_process_count={len(runner_login_pids)} expected=1")
if orphan_runner_login_pids:
    fail("orphan_runner_login_pids=" + ",".join(map(str, orphan_runner_login_pids)))
if len(run_script_pids) < 1:
    fail("run_script_process_count=0 expected_at_least=1")

refresh = load_json(refresh_status_json)
detached = load_json(detached_status_json)

if detached.get("state") != "RUNNING":
    fail(f"detached_state={detached.get('state')!r} expected='RUNNING'")
if detached.get("screen_session") != screen_session:
    fail(f"detached_screen_session={detached.get('screen_session')!r} expected={screen_session!r}")
if detached.get("refresh_status_json") != refresh_status_json:
    fail("detached_refresh_status_json_mismatch")
if detached.get("sleep_seconds") != expected_sleep_seconds:
    fail(f"detached_sleep_seconds={detached.get('sleep_seconds')!r} expected={expected_sleep_seconds}")
if detached.get("run_probe_each_refresh") is not expected_run_probe_each_refresh:
    fail("detached_run_probe_each_refresh_mismatch")
if detached.get("local_dry_run") is not expected_local_dry_run:
    fail("detached_local_dry_run_mismatch")

if refresh.get("state") not in {"REFRESHED", "REQUESTED_ITERATIONS_DONE"}:
    fail(f"refresh_state={refresh.get('state')!r} expected REFRESHED/REQUESTED_ITERATIONS_DONE")
if refresh.get("summary_return_code") != 0:
    fail(f"refresh_summary_return_code={refresh.get('summary_return_code')!r} expected=0")
if refresh.get("sleep_seconds") != expected_sleep_seconds:
    fail(f"refresh_sleep_seconds={refresh.get('sleep_seconds')!r} expected={expected_sleep_seconds}")
if refresh.get("run_probe_each_refresh") is not expected_run_probe_each_refresh:
    fail("refresh_run_probe_each_refresh_mismatch")
if refresh.get("local_dry_run") is not expected_local_dry_run:
    fail("refresh_local_dry_run_mismatch")
updated = parse_iso(refresh.get("updated_utc"))
if updated is None:
    fail("refresh_updated_utc_missing_or_bad")
age_seconds = int(max(0, (datetime.now(timezone.utc) - updated).total_seconds()))
if age_seconds > max_age_seconds:
    fail(f"refresh_status_stale age_seconds={age_seconds} max_age_seconds={max_age_seconds}")

print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=single_screen status=PASS")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=single_runner_login status=PASS")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=no_orphan_runner_login status=PASS")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=run_script_process_present status=PASS")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=detached_status_running status=PASS")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_CASE=refresh_status_fresh status=PASS")
print(f"LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_DETAIL=run_script_process_count:{len(run_script_pids)}")
print(f"LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_DETAIL=summary_script_process_count:{len(summary_script_pids)}")
print(f"LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_DETAIL=tee_process_count:{len(tee_pids)}")
print("LOCAL_STATUS_REFRESH_RUNTIME_UNIQUENESS_STATUS=PASS")
PY
