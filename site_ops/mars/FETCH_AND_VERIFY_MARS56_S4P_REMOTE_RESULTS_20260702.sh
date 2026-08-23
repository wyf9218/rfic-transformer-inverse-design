#!/usr/bin/env bash
set -euo pipefail

# Fetch the latest real MARS56 grounded S4P run and verify that it contains
# real 4-port .s4p files before any local validation package is trusted.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
REMOTE="${REMOTE:-researcher@mars.example.edu}"
REMOTE_OUT_ROOT="${REMOTE_OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_outputs_20260702}"
LOCAL_PULL_ROOT="${LOCAL_PULL_ROOT:-$ROOT/outputs/mars56_grounded_s4p_remote_pull_$(date +%Y%m%d_%H%M%S)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-20}"

SSH_ARGS=(-o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)
SCP_ARGS=(-o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new)
if [[ -n "${SSH_PROXY_JUMP:-}" ]]; then
  SSH_ARGS+=(-J "$SSH_PROXY_JUMP")
  SCP_ARGS+=(-o "ProxyJump=$SSH_PROXY_JUMP")
fi

mkdir -p "$LOCAL_PULL_ROOT"
REMOTE_TMP_TAR="/tmp/mars56_grounded_s4p_latest_${USER:-codex}_$$.tar.gz"

echo "CODEX_MARS56_FETCH_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_OUT_ROOT=$REMOTE_OUT_ROOT"
echo "LOCAL_PULL_ROOT=$LOCAL_PULL_ROOT"

REMOTE_DATASET_DIR="$(ssh "${SSH_ARGS[@]}" "$REMOTE" "find '$REMOTE_OUT_ROOT' -maxdepth 1 -type d -name 'mars56_grounded_s4p_real20_*_dataset' | sort | tail -1")"
if [[ -z "$REMOTE_DATASET_DIR" ]]; then
  echo "CODEX_MARS56_NO_REAL20_DATASET_FOUND" >&2
  exit 3
fi
echo "REMOTE_DATASET_DIR=$REMOTE_DATASET_DIR"

ssh "${SSH_ARGS[@]}" "$REMOTE" "python3 - '$REMOTE_DATASET_DIR' <<'PY'
import csv
import json
import sys
from pathlib import Path

dataset = Path(sys.argv[1])
rows_path = dataset / 'dataset_rows.csv'
summary_path = dataset / 'parallel_candidate_queue_dataset_summary.json'
if not rows_path.is_file():
    raise SystemExit('missing dataset_rows.csv')
rows = list(csv.DictReader(rows_path.open(newline='', encoding='utf-8-sig')))
ok_rows = [r for r in rows if str(r.get('ok', '')).strip().lower() in {'1','true','t','yes','y','pass','ok'}]
s4p = []
missing = []
for row in ok_rows:
    raw = (row.get('touchstone_path') or row.get('raw_touchstone_path') or '').strip()
    if not raw:
        missing.append('missing path')
        continue
    path = Path(raw)
    if not path.is_absolute():
        path = dataset / path
    if path.is_file() and path.suffix.lower() == '.s4p' and path.stat().st_size > 0:
        s4p.append(str(path))
    else:
        missing.append(str(path))
print(json.dumps({
    'dataset': str(dataset),
    'row_count': len(rows),
    'ok_row_count': len(ok_rows),
    'real_s4p_count': len(s4p),
    'missing_or_invalid_count': len(missing),
    'summary_exists': summary_path.is_file(),
    'example_s4p': s4p[:3],
    'example_missing': missing[:3],
}, indent=2))
if len(rows) < 20 or len(ok_rows) < 20 or len(s4p) < 20 or missing:
    raise SystemExit('real S4P output gate failed')
PY"

REMOTE_PARENT="$(dirname "$REMOTE_DATASET_DIR")"
REMOTE_BASE="$(basename "$REMOTE_DATASET_DIR")"
ssh "${SSH_ARGS[@]}" "$REMOTE" "cd '$REMOTE_PARENT' && tar -czf '$REMOTE_TMP_TAR' '$REMOTE_BASE'"
scp "${SCP_ARGS[@]}" "$REMOTE:$REMOTE_TMP_TAR" "$LOCAL_PULL_ROOT/"
ssh "${SSH_ARGS[@]}" "$REMOTE" "rm -f '$REMOTE_TMP_TAR'"

tar -xzf "$LOCAL_PULL_ROOT/$(basename "$REMOTE_TMP_TAR")" -C "$LOCAL_PULL_ROOT"
LOCAL_DATASET_DIR="$LOCAL_PULL_ROOT/$REMOTE_BASE"

echo "LOCAL_DATASET_DIR=$LOCAL_DATASET_DIR"
"$PYTHON_BIN" "$ROOT/rfic-transformer-inverse-design/scripts/package_mars56_grounded_s4p_validation_sample.py" \
  "$LOCAL_DATASET_DIR" \
  --out-dir "$LOCAL_DATASET_DIR/validation_sample_for_hfss_local_verified" \
  --seed 20260702 \
  --port-pairs "1,2:3,4" \
  --start-ghz 5 \
  --stop-ghz 60 \
  --step-ghz 1 \
  --frequency-points 56 \
  --target-ghz 15

echo "CODEX_MARS56_FETCH_VERIFY_DONE"
