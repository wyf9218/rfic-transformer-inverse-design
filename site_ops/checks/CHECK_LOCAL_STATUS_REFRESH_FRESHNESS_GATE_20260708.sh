#!/usr/bin/env bash
set -euo pipefail

# Local-only freshness gate for the MARS56 1M status refresh monitor.
# It prevents stale or dry-run monitor output from being mistaken for current
# readiness evidence. It does not connect to MARS and does not mutate remote
# production.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$ROOT_DIR/logs/mars56_1m_local_status_refresh/mars56_1m_local_status_refresh_latest_detached_status.json}"
REFRESH_STATUS_JSON="${REFRESH_STATUS_JSON:-$ROOT_DIR/logs/mars56_1m_local_status_refresh/mars56_1m_local_status_refresh_latest_status.json}"
SCREEN_SESSION="${SCREEN_SESSION:-mars56_1m_local_status_refresh}"
EXPECTED_SLEEP_SECONDS="${EXPECTED_SLEEP_SECONDS:-300}"
EXPECTED_RUN_PROBE_EACH_REFRESH="${EXPECTED_RUN_PROBE_EACH_REFRESH:-1}"
EXPECTED_LOCAL_DRY_RUN="${EXPECTED_LOCAL_DRY_RUN:-0}"
MAX_AGE_SECONDS="${MAX_AGE_SECONDS:-900}"

python3 - "$DETACHED_STATUS_JSON" "$REFRESH_STATUS_JSON" "$SCREEN_SESSION" "$EXPECTED_SLEEP_SECONDS" "$EXPECTED_RUN_PROBE_EACH_REFRESH" "$EXPECTED_LOCAL_DRY_RUN" "$MAX_AGE_SECONDS" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

detached_path = Path(sys.argv[1])
refresh_path = Path(sys.argv[2])
screen_session = sys.argv[3]
expected_sleep = int(sys.argv[4])
expected_run_probe = sys.argv[5] == "1"
expected_local_dry_run = sys.argv[6] == "1"
max_age_seconds = int(sys.argv[7])

errors: list[str] = []

def load_json(path: Path) -> dict:
    if not path.exists():
        errors.append(f"missing_json:{path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"json_parse_error:{path}:{type(exc).__name__}")
        return {}

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

detached = load_json(detached_path)
refresh = load_json(refresh_path)

screen = subprocess.run(["screen", "-ls"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
screen_output = (screen.stdout or "") + (screen.stderr or "")
screen_running = any(part.endswith(f".{screen_session}") for part in screen_output.split())
if not screen_running:
    errors.append(f"screen_not_running:{screen_session}")

detached_state = detached.get("state")
if detached_state not in {"RUNNING", "STARTED", "ALREADY_RUNNING"}:
    errors.append(f"bad_detached_state:{detached_state}")

if detached.get("refresh_status_json") != str(refresh_path):
    errors.append(
        "refresh_status_json_mismatch:"
        f"expected={refresh_path},actual={detached.get('refresh_status_json')}"
    )
if detached.get("sleep_seconds") != expected_sleep:
    errors.append(f"detached_sleep_mismatch:{detached.get('sleep_seconds')}")
if detached.get("run_probe_each_refresh") is not expected_run_probe:
    errors.append(f"detached_run_probe_mismatch:{detached.get('run_probe_each_refresh')}")
if detached.get("local_dry_run") is not expected_local_dry_run:
    errors.append(f"detached_local_dry_run_mismatch:{detached.get('local_dry_run')}")

if refresh.get("state") not in {"REFRESHED", "REQUESTED_ITERATIONS_DONE"}:
    errors.append(f"bad_refresh_state:{refresh.get('state')}")
if refresh.get("summary_return_code") != 0:
    errors.append(f"bad_refresh_summary_rc:{refresh.get('summary_return_code')}")
if refresh.get("sleep_seconds") != expected_sleep:
    errors.append(f"refresh_sleep_mismatch:{refresh.get('sleep_seconds')}")
if refresh.get("run_probe_each_refresh") is not expected_run_probe:
    errors.append(f"refresh_run_probe_mismatch:{refresh.get('run_probe_each_refresh')}")
if refresh.get("local_dry_run") is not expected_local_dry_run:
    errors.append(f"refresh_local_dry_run_mismatch:{refresh.get('local_dry_run')}")

updated = parse_iso(refresh.get("updated_utc"))
if updated is None:
    errors.append("missing_or_bad_refresh_updated_utc")
    age_seconds = None
else:
    age_seconds = int(max(0, (datetime.now(timezone.utc) - updated).total_seconds()))
    if age_seconds > max_age_seconds:
        errors.append(f"refresh_status_stale:age={age_seconds},max={max_age_seconds}")

status = "PASS" if not errors else "FAIL"
print(f"LOCAL_STATUS_REFRESH_FRESHNESS_GATE_STATUS={status}")
print(f"screen_session={screen_session}")
print(f"refresh_status_json={refresh_path}")
print(f"refresh_age_seconds={age_seconds}")
print(f"max_age_seconds={max_age_seconds}")
print(f"detached_state={detached_state}")
print(f"refresh_state={refresh.get('state')}")
if errors:
    for error in errors:
        print(f"ERROR={error}")
    raise SystemExit(1)
PY
