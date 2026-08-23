#!/usr/bin/env bash
set -euo pipefail

# Read-only production-rate / ETA audit for the active MARS56 1M campaign.
#
# Usage after Duo is available:
#   bash CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh
#
# Local dry-run, no SSH:
#   LOCAL_DRY_RUN=1 bash CHECK_MARS56_PRODUCTION_RATE_AND_ETA_AFTER_DUO_20260707.sh
#
# This script does not start, stop, or modify remote jobs. It records whether
# the observed 48-way EMX flow is still fast enough for weekly 100k checkpoints,
# and after a successful remote read-only audit it copies the JSON/Markdown
# audit artifacts into the campaign status directory for evidence indexing.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
USER_NAME="${USER_NAME:-researcher}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"
LOCAL_DRY_RUN="${LOCAL_DRY_RUN:-0}"
OUT_JSON="${OUT_JSON:-$ROOT_DIR/reports/mars56_production_rate_eta_latest.json}"
OUT_MD="${OUT_MD:-$ROOT_DIR/reports/mars56_production_rate_eta_latest_CN.md}"
REMOTE_BASE="${REMOTE_BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
REMOTE_STATUS_DIR="${REMOTE_STATUS_DIR:-$REMOTE_BASE/status}"
SYNC_RATE_ARTIFACT_TO_REMOTE="${SYNC_RATE_ARTIFACT_TO_REMOTE:-1}"

EXPECTED_PARALLEL_JOBS="${EXPECTED_PARALLEL_JOBS:-48}"
TARGET_ROWS_PER_CHECKPOINT="${TARGET_ROWS_PER_CHECKPOINT:-100000}"
TARGET_TOTAL_ROWS="${TARGET_TOTAL_ROWS:-1000000}"
TARGET_SECONDS_PER_ACCEPTED_ROW="${TARGET_SECONDS_PER_ACCEPTED_ROW:-4.0}"
TARGET_DAYS_PER_100K="${TARGET_DAYS_PER_100K:-5.0}"
MAX_SECONDS_PER_ACCEPTED_ROW="${MAX_SECONDS_PER_ACCEPTED_ROW:-4.5}"
MAX_DAYS_PER_100K="${MAX_DAYS_PER_100K:-5.5}"

case "$LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
case "$SYNC_RATE_ARTIFACT_TO_REMOTE" in 0|1) ;;
  *) echo "ERROR: SYNC_RATE_ARTIFACT_TO_REMOTE must be 0 or 1." >&2; exit 2 ;;
esac
if [[ "$SSH_CONTROL_PATH" == *$'\n'* || "$SSH_CONTROL_PATH" == *$'\r'* ]]; then
  echo "ERROR: SSH_CONTROL_PATH contains unsupported newline characters." >&2
  exit 2
fi
for value in "$REMOTE_BASE" "$REMOTE_STATUS_DIR" "$OUT_JSON" "$OUT_MD"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path/settings contain unsupported quote or newline characters." >&2
    exit 2
  fi
done
for value in "$EXPECTED_PARALLEL_JOBS" "$TARGET_ROWS_PER_CHECKPOINT" "$TARGET_TOTAL_ROWS"; do
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "ERROR: integer settings must be positive decimal integers." >&2
    exit 2
  fi
done

SSH_ARGS=(-tt)
PROXY_SSH_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
PROXY_SCP_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SCP_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

sync_rate_artifacts_to_remote() {
  if [ "$SYNC_RATE_ARTIFACT_TO_REMOTE" != "1" ]; then
    echo "PRODUCTION_RATE_REMOTE_ARTIFACT_SYNC=SKIPPED_SYNC_RATE_ARTIFACT_TO_REMOTE_0"
    return 0
  fi
  if [ ! -f "$OUT_JSON" ] || [ ! -f "$OUT_MD" ]; then
    echo "PRODUCTION_RATE_REMOTE_ARTIFACT_SYNC=FAIL_LOCAL_ARTIFACT_MISSING"
    return 1
  fi
  local mars_target="${USER_NAME}@${MARS_HOST}"
  local remote_json="${REMOTE_STATUS_DIR}/mars56_production_rate_eta_latest.json"
  local remote_md="${REMOTE_STATUS_DIR}/mars56_production_rate_eta_latest_CN.md"
  ssh "${PROXY_SSH_ARGS[@]}" "$mars_target" "mkdir -p '${REMOTE_STATUS_DIR}'"
  scp "${PROXY_SCP_ARGS[@]}" "$OUT_JSON" "${mars_target}:${remote_json}"
  scp "${PROXY_SCP_ARGS[@]}" "$OUT_MD" "${mars_target}:${remote_md}"
  echo "PRODUCTION_RATE_REMOTE_ARTIFACT_SYNC=PASS"
  echo "remote_rate_json=$remote_json"
  echo "remote_rate_md=$remote_md"
}

write_audit_artifacts() {
  local mode="$1"
  local rc="$2"
  local output="$3"
  local raw_output_file
  raw_output_file="$(mktemp)"
  printf '%s\n' "$output" >"$raw_output_file"
  python3 - "$OUT_JSON" "$OUT_MD" "$mode" "$rc" \
    "$EXPECTED_PARALLEL_JOBS" "$TARGET_ROWS_PER_CHECKPOINT" "$TARGET_TOTAL_ROWS" \
    "$TARGET_SECONDS_PER_ACCEPTED_ROW" "$TARGET_DAYS_PER_100K" \
    "$MAX_SECONDS_PER_ACCEPTED_ROW" "$MAX_DAYS_PER_100K" "$raw_output_file" <<'PY'
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

out_json = Path(sys.argv[1])
out_md = Path(sys.argv[2])
mode = sys.argv[3]
return_code = int(sys.argv[4])
expected_parallel = int(sys.argv[5])
target_rows = int(sys.argv[6])
target_total = int(sys.argv[7])
target_seconds = float(sys.argv[8])
target_days = float(sys.argv[9])
max_seconds = float(sys.argv[10])
max_days = float(sys.argv[11])
raw_output_file = Path(sys.argv[12])
raw_output = raw_output_file.read_text(errors="replace")

def coerce(value):
    value = value.strip()
    if value == "MISSING":
        return None
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        number = float(value)
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return value

metrics = {}
dataset_rate_summaries = []
for line in raw_output.splitlines():
    line = line.strip()
    if not line:
        continue
    if line.startswith("DATASET_RATE_SUMMARY "):
        item = {}
        for match in re.finditer(r"(\w+)=([^ ]+)", line):
            item[match.group(1)] = coerce(match.group(2))
        dataset_rate_summaries.append(item)
        continue
    if "=" in line and not line.startswith("COMMAND "):
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z0-9_./:-]+", key):
            metrics[key] = coerce(value)

seconds = metrics.get("measured_seconds_per_accepted_row")
days_100k = metrics.get("eta_days_per_100k")
latest_parallel = metrics.get("latest_parallel_jobs")
target_status = metrics.get("PRODUCTION_RATE_TARGET_STATUS")
gate_status = metrics.get("PRODUCTION_RATE_AUDIT_STATUS")
if seconds is None or days_100k is None:
    local_target_status = "UNKNOWN_NO_RATE"
    local_gate_status = "UNKNOWN_NO_RATE"
else:
    local_target_status = "PASS" if float(seconds) <= target_seconds and float(days_100k) <= target_days else "REVIEW"
    local_gate_status = "PASS" if float(seconds) <= max_seconds and float(days_100k) <= max_days else "FAIL"
if not target_status:
    target_status = local_target_status
if not gate_status:
    gate_status = local_gate_status if mode != "LOCAL_DRY_RUN" else "DRY_RUN_CONTRACT_ONLY"

summary = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "audit_mode": mode,
    "return_code": return_code,
    "artifact_boundary": (
        "LOCAL_DRY_RUN records only the configured throughput contract. "
        "REMOTE_READ_ONLY_AUDIT is evidence only when return_code is 0 and metrics were parsed from MARS output."
    ),
    "contract": {
        "expected_parallel_jobs": expected_parallel,
        "target_rows_per_checkpoint": target_rows,
        "target_total_rows": target_total,
        "target_seconds_per_accepted_row": target_seconds,
        "target_days_per_100k": target_days,
        "max_seconds_per_accepted_row_gate": max_seconds,
        "max_days_per_100k_gate": max_days,
    },
    "parsed_metrics": metrics,
    "dataset_rate_summaries": dataset_rate_summaries,
    "interpreted": {
        "latest_parallel_jobs": latest_parallel,
        "measured_seconds_per_accepted_row": seconds,
        "eta_days_per_100k": days_100k,
        "eta_days_for_1m_at_same_rate": metrics.get("eta_days_for_1m_at_same_rate"),
        "production_rate_target_status": target_status,
        "production_rate_audit_status": gate_status,
        "parallel_jobs_match": latest_parallel == expected_parallel if latest_parallel is not None else None,
    },
    "raw_output": raw_output,
}

out_json.parent.mkdir(parents=True, exist_ok=True)
out_md.parent.mkdir(parents=True, exist_ok=True)
out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

md = f"""# MARS56 Production Rate / ETA Audit

Updated UTC: `{summary['updated_utc']}`

## Contract

- Expected parallel jobs: `{expected_parallel}`
- Target: `{target_seconds}` seconds/accepted row, `{target_days}` days/100k
- Hard gate: `{max_seconds}` seconds/accepted row, `{max_days}` days/100k
- Target rows/checkpoint: `{target_rows}`
- Total target rows: `{target_total}`

## Parsed Result

- Audit mode: `{mode}`
- Return code: `{return_code}`
- Latest parallel jobs: `{latest_parallel}`
- Measured seconds/accepted row: `{seconds}`
- ETA days/100k: `{days_100k}`
- ETA days/1M: `{metrics.get('eta_days_for_1m_at_same_rate')}`
- Target status: `{target_status}`
- Gate status: `{gate_status}`

## Boundary

{summary['artifact_boundary']}
"""
out_md.write_text(md, encoding="utf-8")

print(f"PRODUCTION_RATE_AUDIT_JSON={out_json}")
print(f"PRODUCTION_RATE_AUDIT_MD={out_md}")
PY
  rm -f "$raw_output_file"
}

read -r -d '' REMOTE_AUDIT <<'REMOTE' || true
set -euo pipefail

BASE=/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256
LIVE=$BASE/status/live_status.json
PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python3
fi

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
printf 'base=%s\n' "$BASE"
printf 'live_status_json=%s\n' "$LIVE"

"$PY" - "$LIVE" "$EXPECTED_PARALLEL_JOBS" "$TARGET_ROWS_PER_CHECKPOINT" "$TARGET_TOTAL_ROWS" "$TARGET_SECONDS_PER_ACCEPTED_ROW" "$TARGET_DAYS_PER_100K" "$MAX_SECONDS_PER_ACCEPTED_ROW" "$MAX_DAYS_PER_100K" <<'PY'
import json
import math
import sys
from pathlib import Path

live_path = Path(sys.argv[1])
expected_parallel = int(sys.argv[2])
target_rows = int(sys.argv[3])
target_total = int(sys.argv[4])
target_seconds = float(sys.argv[5])
target_days = float(sys.argv[6])
max_seconds = float(sys.argv[7])
max_days = float(sys.argv[8])

data = {}
if live_path.exists():
    try:
        data = json.loads(live_path.read_text())
    except Exception as exc:
        print(f"live_status_parse_error={type(exc).__name__}")

def number(value):
    try:
        if value is None:
            return None
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return None

seconds_candidates = []
for key in (
    "effective_wall_seconds_per_accepted_row",
    "chunk05_effective_seconds_per_row",
    "chunk06_effective_seconds_per_row",
    "chunk07_effective_seconds_per_row",
    "chunk08_effective_seconds_per_row",
):
    value = number(data.get(key))
    if value is not None and value > 0:
        seconds_candidates.append((key, value))

parallel_candidates = []
for key, value in data.items():
    if key.endswith("_parallel_jobs"):
        n = number(value)
        if n is not None:
            parallel_candidates.append((key, int(n)))

seconds_source = seconds_candidates[0][0] if seconds_candidates else "MISSING"
seconds_per_row = seconds_candidates[0][1] if seconds_candidates else None
latest_parallel = parallel_candidates[-1][1] if parallel_candidates else None
latest_parallel_source = parallel_candidates[-1][0] if parallel_candidates else "MISSING"

if seconds_per_row is None:
    print("measured_seconds_per_accepted_row=MISSING")
    days_100k = None
    days_total = None
else:
    days_100k = target_rows * seconds_per_row / 86400.0
    days_total = target_total * seconds_per_row / 86400.0
    print(f"measured_seconds_per_accepted_row={seconds_per_row:.3f}")
    print(f"measured_seconds_source={seconds_source}")
    print(f"eta_days_per_100k={days_100k:.2f}")
    print(f"eta_days_for_1m_at_same_rate={days_total:.2f}")
    print("PRODUCTION_RATE_TARGET_STATUS=" + ("PASS" if seconds_per_row <= target_seconds and days_100k <= target_days else "REVIEW"))

print(f"expected_parallel_jobs={expected_parallel}")
print(f"latest_parallel_jobs={latest_parallel if latest_parallel is not None else 'MISSING'}")
print(f"latest_parallel_source={latest_parallel_source}")
print(f"target_rows_per_checkpoint={target_rows}")
print(f"target_total_rows={target_total}")
print(f"target_seconds_per_accepted_row={target_seconds:.2f}")
print(f"target_days_per_100k={target_days:.2f}")
print(f"max_seconds_per_accepted_row={max_seconds:.2f}")
print(f"max_days_per_100k={max_days:.2f}")

reasons = []
if seconds_per_row is None:
    reasons.append("missing_measured_seconds_per_accepted_row")
elif seconds_per_row > max_seconds:
    reasons.append(f"seconds_per_row_gt_limit:{seconds_per_row:.3f}>{max_seconds:.3f}")
if days_100k is None:
    reasons.append("missing_eta_days_per_100k")
elif days_100k > max_days:
    reasons.append(f"days_per_100k_gt_limit:{days_100k:.3f}>{max_days:.3f}")
if latest_parallel is None:
    reasons.append("missing_parallel_job_evidence")
elif latest_parallel != expected_parallel:
    reasons.append(f"parallel_jobs_mismatch:{latest_parallel}!={expected_parallel}")

print("PRODUCTION_RATE_AUDIT_STATUS=" + ("PASS" if not reasons else "FAIL"))
if reasons:
    print("PRODUCTION_RATE_AUDIT_REASONS=" + ",".join(reasons))
else:
    print("PRODUCTION_RATE_AUDIT_REASONS=none")
PY

printf '\n-- active production processes --\n'
printf 'active_100k_runner_processes='
ps -fu researcher | grep '_100k_after_chunk08_pass' | grep 'run_candidate_queue_dataset_parallel.py' | grep -v grep | wc -l | tr -d ' '
printf '\nactive_100k_worker_processes='
ps -fu researcher | grep '_100k_after_chunk08_pass' | grep 'run_candidate_queue_dataset.py' | grep -v grep | wc -l | tr -d ' '
printf '\nactive_100k_emx_processes='
ps -fu researcher | grep '_100k_after_chunk08_pass' | grep '/EMX20251/.*/emx' | grep -v grep | wc -l | tr -d ' '
printf '\n'

printf '\n-- completed formal 100k dataset summaries --\n'
find "$BASE/datasets" -maxdepth 2 -name parallel_candidate_queue_dataset_summary.json -path '*_100k_after_chunk08_pass/*' -print 2>/dev/null | sort | while read -r summary; do
  "$PY" - "$summary" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    d = json.loads(path.read_text())
except Exception as exc:
    print(f"DATASET_RATE_SUMMARY path={path} parse_error={type(exc).__name__}")
    raise SystemExit(0)
count = d.get("count") or d.get("completed_count") or d.get("expected_count")
elapsed = d.get("elapsed_seconds") or d.get("wall_seconds")
rate = None
try:
    if count and elapsed:
        rate = float(elapsed) / float(count)
except Exception:
    rate = None
print(
    "DATASET_RATE_SUMMARY "
    f"path={path} "
    f"overall_status={d.get('overall_status')} "
    f"count={count} "
    f"elapsed_seconds={elapsed} "
    f"seconds_per_row={rate:.3f}" if rate is not None else
    "DATASET_RATE_SUMMARY "
    f"path={path} "
    f"overall_status={d.get('overall_status')} "
    f"count={count} elapsed_seconds={elapsed} seconds_per_row=MISSING"
)
PY
done
REMOTE

if [ "$LOCAL_DRY_RUN" = "1" ]; then
  dry_run_output="$(cat <<EOF
PRODUCTION_RATE_AUDIT_LOCAL_DRY_RUN=1
expected_parallel_jobs=$EXPECTED_PARALLEL_JOBS
target_rows_per_checkpoint=$TARGET_ROWS_PER_CHECKPOINT
target_total_rows=$TARGET_TOTAL_ROWS
target_seconds_per_accepted_row=$TARGET_SECONDS_PER_ACCEPTED_ROW
target_days_per_100k=$TARGET_DAYS_PER_100K
max_seconds_per_accepted_row=$MAX_SECONDS_PER_ACCEPTED_ROW
max_days_per_100k=$MAX_DAYS_PER_100K
remote_audit_contains=PRODUCTION_RATE_AUDIT_STATUS
remote_audit_contains=PRODUCTION_RATE_TARGET_STATUS
remote_audit_contains=eta_days_per_100k
remote_audit_contains=active_100k_runner_processes
remote_artifact_sync_target=$REMOTE_STATUS_DIR
remote_artifact_sync_contains=mars56_production_rate_eta_latest.json
EOF
)"
  printf '%s\n' "$dry_run_output"
  write_audit_artifacts "LOCAL_DRY_RUN" 0 "$dry_run_output"
  exit 0
fi

echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
echo "Then this will query ${MARS_HOST} without changing remote files."
export EXPECTED_PARALLEL_JOBS TARGET_ROWS_PER_CHECKPOINT TARGET_TOTAL_ROWS TARGET_SECONDS_PER_ACCEPTED_ROW TARGET_DAYS_PER_100K MAX_SECONDS_PER_ACCEPTED_ROW MAX_DAYS_PER_100K
set +e
remote_output="$(ssh "${SSH_ARGS[@]}" "${USER_NAME}@${JUMP_HOST}" "ssh -tt ${MARS_HOST} 'EXPECTED_PARALLEL_JOBS=${EXPECTED_PARALLEL_JOBS} TARGET_ROWS_PER_CHECKPOINT=${TARGET_ROWS_PER_CHECKPOINT} TARGET_TOTAL_ROWS=${TARGET_TOTAL_ROWS} TARGET_SECONDS_PER_ACCEPTED_ROW=${TARGET_SECONDS_PER_ACCEPTED_ROW} TARGET_DAYS_PER_100K=${TARGET_DAYS_PER_100K} MAX_SECONDS_PER_ACCEPTED_ROW=${MAX_SECONDS_PER_ACCEPTED_ROW} MAX_DAYS_PER_100K=${MAX_DAYS_PER_100K} bash -s'" <<<"$REMOTE_AUDIT" 2>&1)"
remote_rc=$?
set -e
printf '%s\n' "$remote_output"
write_audit_artifacts "REMOTE_READ_ONLY_AUDIT" "$remote_rc" "$remote_output"
if [ "$remote_rc" -eq 0 ]; then
  sync_rate_artifacts_to_remote
fi
exit "$remote_rc"
