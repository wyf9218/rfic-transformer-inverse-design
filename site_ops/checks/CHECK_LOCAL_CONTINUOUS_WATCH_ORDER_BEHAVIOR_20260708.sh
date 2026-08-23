#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for the post-Duo continuous watcher ordering.
# It must not SSH; LOCAL_DRY_RUN=1 prints the intended supervisor sequence.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
WATCHER="$ROOT_DIR/RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh"

if [ ! -f "$WATCHER" ]; then
  echo "CONTINUOUS_WATCH_ORDER_BEHAVIOR_STATUS=FAIL missing_watcher=$WATCHER"
  exit 1
fi
bash -n "$WATCHER"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
OUT="$TMP_DIR/watch_order.log"

if ! LOCAL_DRY_RUN=1 WATCH_ITERATIONS=1 SLEEP_SECONDS=0 WATCH_LOG_CAPTURE=0 bash "$WATCHER" >"$OUT" 2>&1; then
  echo "CONTINUOUS_WATCH_ORDER_BEHAVIOR_CASE=dry_run_sequence status=FAIL"
  sed -n '1,260p' "$OUT"
  exit 1
fi

python3 - "$OUT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(errors="replace").splitlines()

labels = {}
for index, line in enumerate(lines, start=1):
    marker = "========== WATCH_STEP "
    if line.startswith(marker) and line.endswith(" =========="):
        label = line[len(marker):-len(" ==========")]
        labels[label] = index

required = [
    "supervisor_preflight",
    "supervisor_verify-runner",
    "supervisor_resume-watchers",
    "supervisor_rate",
    "supervisor_adaptive-acquisition",
    "supervisor_checkpoint",
    "supervisor_cumulative",
    "supervisor_evidence-index",
    "supervisor_audit",
    "summarize_supervisor_logs",
]
missing = [label for label in required if label not in labels]
if missing:
    raise SystemExit(f"missing watcher steps: {missing}; labels={labels}")

order_checks = [
    ("supervisor_preflight", "supervisor_verify-runner"),
    ("supervisor_verify-runner", "supervisor_resume-watchers"),
    ("supervisor_resume-watchers", "supervisor_rate"),
    ("supervisor_rate", "supervisor_adaptive-acquisition"),
    ("supervisor_rate", "supervisor_checkpoint"),
    ("supervisor_rate", "supervisor_cumulative"),
    ("supervisor_rate", "supervisor_evidence-index"),
    ("supervisor_checkpoint", "supervisor_evidence-index"),
    ("supervisor_cumulative", "supervisor_evidence-index"),
    ("supervisor_evidence-index", "supervisor_audit"),
    ("supervisor_audit", "summarize_supervisor_logs"),
]
violations = [
    f"{before}@{labels[before]} !< {after}@{labels[after]}"
    for before, after in order_checks
    if labels[before] >= labels[after]
]
if violations:
    raise SystemExit("order violations: " + "; ".join(violations))

text = "\n".join(lines)
for token in [
    "run_rate_audit=1",
    "run_evidence_index=1",
    "LOCAL_DRY_RUN command=env MODE=rate",
    "LOCAL_DRY_RUN command=env MODE=evidence-index",
    "WATCH_STATUS=REQUESTED_ITERATIONS_DONE",
]:
    if token not in text:
        raise SystemExit(f"missing output token: {token}")

print("CONTINUOUS_WATCH_ORDER=rate_before_evidence_index")
print("CONTINUOUS_WATCH_ORDER=checkpoint_before_evidence_index")
print("CONTINUOUS_WATCH_ORDER=cumulative_before_evidence_index")
PY

echo "CONTINUOUS_WATCH_ORDER_BEHAVIOR_CASE=dry_run_sequence status=PASS"
echo "CONTINUOUS_WATCH_ORDER_BEHAVIOR_STATUS=PASS"
