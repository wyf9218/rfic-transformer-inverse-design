#!/usr/bin/env bash
set -euo pipefail

# Local behavior regression for merging adaptive EMX output back into the
# accepted physical-feature pool. This does not run EMX; it proves the
# post-EMX bookkeeping contract on a small fixture.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/merge_physical_feature_accepted_pool.py"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_pool_merge.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

if [ ! -f "$SCRIPT" ]; then
  echo "ACCEPTED_POOL_MERGE_BEHAVIOR_STATUS=FAIL missing script: $SCRIPT" >&2
  exit 2
fi

BASE_POOL="$TMP_ROOT/base_pool"
TRAINING="$TMP_ROOT/adaptive_checkpoint/physical_feature_inverse_training_table.csv"
OUT_DIR="$TMP_ROOT/merged_pool"
mkdir -p "$BASE_POOL" "$(dirname "$TRAINING")"

python3 - "$BASE_POOL/dataset_rows.csv" "$TRAINING" <<'PY'
import csv
import sys
from pathlib import Path

base_csv = Path(sys.argv[1])
training_csv = Path(sys.argv[2])

geom = {
    "geom__primary_outer_width_um": 230.0,
    "geom__primary_outer_height_um": 240.0,
    "geom__secondary_outer_width_um": 220.0,
    "geom__secondary_outer_height_um": 225.0,
    "geom__line_width_um": 6.0,
    "geom__primary_width_um": 6.0,
    "geom__secondary_width_um": 6.0,
    "geom__primary_terminal_y_span_um": 90.0,
    "geom__secondary_terminal_y_span_um": 92.0,
    "geom__offset_um": 0.0,
    "geom__primary_feed_extension_um": 140.0,
    "geom__secondary_feed_extension_um": 145.0,
}

base_rows = []
for idx, (evaluation, lp, ls, q, k) in enumerate(
    [
        ("base_a", 1.0, 1.1, 9.0, -0.35),
        ("base_b", 1.6, 1.5, 13.0, 0.42),
    ]
):
    row = {
        "ok": "true",
        "evaluation": evaluation,
        "touchstone_path": f"/fake/{evaluation}.s8p",
        "lp_nh_center": lp,
        "ls_nh_center": ls,
        "q_center": q,
        "k_center": k,
    }
    for key, value in geom.items():
        row[key] = value + idx
    base_rows.append(row)

with base_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(base_rows[0]))
    writer.writeheader()
    writer.writerows(base_rows)

training_rows = []
valid = {
    "evaluation": "adaptive_new",
    "input__lp_nh_center": 2.1,
    "input__ls_nh_center": 2.0,
    "input__q_center": 18.0,
    "input__k_center": -0.55,
}
for key, value in geom.items():
    valid[key] = value + 10.0
training_rows.append(valid)

duplicate = {
    "evaluation": "base_a",
    "touchstone_path": "/fake/base_a.s8p",
    "input__lp_nh_center": 1.0,
    "input__ls_nh_center": 1.1,
    "input__q_center": 9.0,
    "input__k_center": -0.35,
}
for key, value in geom.items():
    duplicate[key] = value
training_rows.append(duplicate)

outside_range = {
    "evaluation": "adaptive_bad_q",
    "input__lp_nh_center": 2.2,
    "input__ls_nh_center": 2.1,
    "input__q_center": 40.0,
    "input__k_center": 0.45,
}
for key, value in geom.items():
    outside_range[key] = value + 20.0
training_rows.append(outside_range)

with training_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=sorted({key for row in training_rows for key in row}))
    writer.writeheader()
    writer.writerows(training_rows)
PY

python3 "$SCRIPT" \
  --base-pool-dir "$BASE_POOL" \
  --training-csv "$TRAINING" \
  --out-dir "$OUT_DIR" \
  --min-row-count 3 \
  --uniformity-min-valid-count 3 \
  --run-uniformity \
  --require-plots \
  --no-fail-exit

python3 - "$OUT_DIR/accepted_pool_merge_summary.json" "$OUT_DIR/dataset_rows.csv" <<'PY'
import csv
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert summary["overall_status"] == "PASS", summary
assert summary["decision"] == "USE_AS_NEXT_ACCEPTED_POOL_FOR_ADAPTIVE_PLANNING", summary
assert summary["row_count"] == 3, summary
assert summary["reject_summary"]["duplicate"] == 1, summary["reject_summary"]
assert summary["reject_summary"]["outside_range"] == 1, summary["reject_summary"]
assert summary["uniformity"]["summary"].endswith("physical_feature_uniformity_summary.json"), summary["uniformity"]
with open(sys.argv[2], newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
assert len(rows) == 3, rows
assert "k_abs_center" in rows[0], rows[0]
assert any(row["evaluation"] == "adaptive_new" for row in rows), rows
assert all(float(row["k_abs_center"]) >= 0 for row in rows), rows
PY

echo "ACCEPTED_POOL_MERGE_CASE=base_plus_adaptive_training status=PASS"
echo "ACCEPTED_POOL_MERGE_BEHAVIOR_STATUS=PASS"
