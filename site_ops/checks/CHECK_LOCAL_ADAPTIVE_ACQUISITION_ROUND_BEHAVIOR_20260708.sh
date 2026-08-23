#!/usr/bin/env bash
set -euo pipefail

# Behavior regression for the adaptive Lp/Ls/Q/|K| acquisition round.
#
# It proves two local invariants that matter before spending MARS time:
#   1. k_abs_center can be derived from signed k_center for acquisition.
#   2. the materialized S4P queue is traceably physical-feature targeted.
#   3. the adaptive planner can use the formal configured Lp/Ls/Q/|K|
#      envelope instead of the observed pilot-data min/max range.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh"
BUILDER="$ROOT_DIR/rfic-transformer-inverse-design/scripts/build_physical_feature_surrogate_candidate_predictions.py"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_adaptive_acq.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

DATASET="$TMP_ROOT/dataset"
mkdir -p "$DATASET"
python3 - "$DATASET/dataset_rows.csv" "$DATASET/dataset_manifest.json" "$TMP_ROOT/candidate_predictions.csv" <<'PY'
import csv
import itertools
import json
import sys
from pathlib import Path

dataset_csv = Path(sys.argv[1])
manifest = Path(sys.argv[2])
candidate_csv = Path(sys.argv[3])

geom_fields = [
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_width_um",
    "secondary_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
]

base_geoms = {
    "primary_outer_width_um": 230.0,
    "primary_outer_height_um": 240.0,
    "secondary_outer_width_um": 220.0,
    "secondary_outer_height_um": 225.0,
    "line_width_um": 6.0,
    "primary_width_um": 6.0,
    "secondary_width_um": 6.0,
    "primary_terminal_y_span_um": 90.0,
    "secondary_terminal_y_span_um": 92.0,
    "offset_um": 0.0,
    "primary_feed_extension_um": 140.0,
    "secondary_feed_extension_um": 145.0,
}

dataset_rows = []
feature_rows = [
    (1.0, 1.0, 8.0, -0.20),
    (1.2, 1.2, 9.0, -0.25),
    (2.0, 2.0, 18.0, 0.60),
    (2.2, 2.2, 19.0, 0.65),
]
for idx, (lp, ls, q, k) in enumerate(feature_rows):
    row = {"evaluation": f"r{idx}", "ok": "true", "lp_nh_center": lp, "ls_nh_center": ls, "q_center": q, "k_center": k}
    for field, value in base_geoms.items():
        row[f"geom__{field}"] = value + idx * 0.1
    dataset_rows.append(row)

with dataset_csv.open("w", newline="", encoding="utf-8") as handle:
    fields = list(dataset_rows[0])
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(dataset_rows)

manifest.write_text(json.dumps({"bounds": {field: [base_geoms[field], base_geoms[field] + 10.0] for field in geom_fields}}, indent=2), encoding="utf-8")

# Candidate predictions cover both halves of every feature axis, so whichever
# sparse 4D target bins are selected can be filled with inside-bin candidates.
lp_values = [1.27, 1.93]
ls_values = [1.27, 1.93]
q_values = [10.5, 16.5]
k_abs_values = [0.30, 0.55]
candidate_rows = []
rank = 0
for lp, ls, q, k_abs in itertools.product(lp_values, ls_values, q_values, k_abs_values):
    rank += 1
    row = {
        "candidate_id": f"pred_{rank:03d}",
        "pred_lp_nh_center": lp,
        "pred_ls_nh_center": ls,
        "pred_q_center": q,
        "pred_k_abs_center": k_abs,
    }
    for field, value in base_geoms.items():
        row[f"geom__{field}"] = value + rank * 0.01
    candidate_rows.append(row)

with candidate_csv.open("w", newline="", encoding="utf-8") as handle:
    fields = list(candidate_rows[0])
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(candidate_rows)
PY

cat > "$TMP_ROOT/target_envelope.json" <<'JSON'
{
  "schema": "physical_feature_target_envelope.v1",
  "name": "local_behavior_test_physical_feature_target_envelope",
  "status": "ACTIVE",
  "physical_feature_target_envelope": {
    "feature_columns": [
      "lp_nh_center",
      "ls_nh_center",
      "q_center",
      "k_abs_center"
    ],
    "features": {
      "lp_nh_center": {"min": 0.5, "max": 3.0, "unit": "nH"},
      "ls_nh_center": {"min": 0.5, "max": 3.0, "unit": "nH"},
      "q_center": {"min": 5.0, "max": 25.0, "unit": "dimensionless"},
      "k_abs_center": {"min": 0.0, "max": 0.8, "unit": "dimensionless"}
    },
    "bins": 2,
    "target_count_per_bin": 2,
    "next_count": 4,
    "max_bin_count": 16
  }
}
JSON

cat > "$TMP_ROOT/target_envelope_topup.json" <<'JSON'
{
  "schema": "physical_feature_target_envelope.v1",
  "name": "local_behavior_test_physical_feature_target_envelope_topup",
  "status": "ACTIVE",
  "physical_feature_target_envelope": {
    "feature_columns": [
      "lp_nh_center",
      "ls_nh_center",
      "q_center",
      "k_abs_center"
    ],
    "features": {
      "lp_nh_center": {"min": 0.5, "max": 3.0, "unit": "nH"},
      "ls_nh_center": {"min": 0.5, "max": 3.0, "unit": "nH"},
      "q_center": {"min": 5.0, "max": 25.0, "unit": "dimensionless"},
      "k_abs_center": {"min": 0.0, "max": 0.8, "unit": "dimensionless"}
    },
    "bins": 2,
    "target_count_per_bin": 1,
    "next_count": 20,
    "max_bin_count": 16
  }
}
JSON

python3 "$ROOT_DIR/rfic-transformer-inverse-design/scripts/plan_physical_feature_balanced_acquisition.py" \
  "$DATASET" \
  --out-dir "$TMP_ROOT/topup_plan" \
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
  --target-envelope-config "$TMP_ROOT/target_envelope_topup.json" \
  --no-fail-exit >/tmp/mars56_adaptive_topup_plan_$$.log

python3 - "$TMP_ROOT/topup_plan/physical_feature_acquisition_plan_summary.json" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["overall_status"] == "PASS", summary
assert summary["planning_envelope"]["target_count_per_bin"] == 1, summary["planning_envelope"]
assert summary["planning_envelope"]["next_count"] == 20, summary["planning_envelope"]
assert summary["target_summary"]["recommended_new_sample_count"] == 20, summary["target_summary"]
assert summary["acquisition_allocation_policy"]["name"] == "deficit_first_then_low_count_topup", summary["acquisition_allocation_policy"]
targets = [row for row in open(summary["targets_csv"], encoding="utf-8").read().splitlines() if row.strip()]
assert len(targets) > 2, targets
import csv
with open(summary["targets_csv"], newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
assert any(int(row["recommended_new_samples"]) > 1 for row in rows), rows
PY

python3 "$BUILDER" "$DATASET" \
  --out-dir "$TMP_ROOT/builder_kabs" \
  --candidate-count 12 \
  --prediction-batch-size 4 \
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
  --no-plots \
  --no-fail-exit >/tmp/mars56_adaptive_builder_$$.log

python3 - "$TMP_ROOT/builder_kabs/candidate_physical_feature_prediction_summary.json" "$TMP_ROOT/builder_kabs/candidate_physical_feature_predictions.csv" <<'PY'
import csv
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["overall_status"] == "PASS", summary
assert "k_abs_center" in summary["feature_columns"], summary["feature_columns"]
with open(sys.argv[2], newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
assert rows and "pred_k_abs_center" in rows[0], rows[:1]
PY

bash "$SCRIPT" \
  --dataset-dir "$DATASET" \
  --out-dir "$TMP_ROOT/round" \
  --queue-count 4 \
  --candidate-predictions-csv "$TMP_ROOT/candidate_predictions.csv" \
  --feature-columns lp_nh_center,ls_nh_center,q_center,k_abs_center \
  --target-envelope-config "$TMP_ROOT/target_envelope.json" \
  --max-target-bins 4 \
  --rare-marginal-fraction 0

python3 - "$TMP_ROOT/round/adaptive_physical_acquisition_round_summary.json" <<'PY'
import csv
import json
import sys
from pathlib import Path

summary = json.load(open(sys.argv[1], encoding="utf-8"))
assert summary["overall_status"] == "PASS", summary
assert summary["decision"] == "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION", summary
assert summary["feature_columns"][-1] == "k_abs_center", summary["feature_columns"]
assert summary["selected_candidate_count"] == 4, summary
assert summary["selected_inside_target_bin_count"] == 4, summary
assert summary["statuses"]["provenance"] == "PASS", summary["statuses"]
identity = summary["queue_identity_evidence"]
assert identity["require_unique_geometry"] is True, identity
assert identity["require_unique_source_id"] is True, identity
assert identity["identity_audit"]["unique_geometry_fingerprint_count"] == 4, identity
assert identity["identity_audit"]["duplicate_geometry_extra_row_count"] == 0, identity
plan = summary["artifacts"]["plan_summary"]
plan_summary = json.load(open(plan, encoding="utf-8"))
assert plan_summary["target_envelope_config"]["status"] == "PASS", plan_summary["target_envelope_config"]
assert plan_summary["planning_envelope"]["source"] == "configured_feature_bounds", plan_summary["planning_envelope"]
bounds = plan_summary["planning_envelope"]["feature_bounds"]
assert bounds["lp_nh_center"] == {"min": 0.5, "max": 3.0}, bounds
assert bounds["ls_nh_center"] == {"min": 0.5, "max": 3.0}, bounds
assert bounds["q_center"] == {"min": 5.0, "max": 25.0}, bounds
assert bounds["k_abs_center"] == {"min": 0.0, "max": 0.8}, bounds
queue_csv = Path(summary["artifacts"]["queue_csv"])
with queue_csv.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
assert len(rows) == 4, len(rows)
assert all(float(row["line_width_um"]) == float(row["primary_width_um"]) == float(row["secondary_width_um"]) for row in rows), rows
PY

echo "ADAPTIVE_TARGET_ENVELOPE_CONFIG_STATUS=PASS"
echo "ADAPTIVE_PLANNING_ENVELOPE_SOURCE=configured_feature_bounds"
echo "ADAPTIVE_TOPUP_ALLOCATION_POLICY=deficit_first_then_low_count_topup"
echo "ADAPTIVE_ACQUISITION_ROUND_BEHAVIOR_STATUS=PASS"
