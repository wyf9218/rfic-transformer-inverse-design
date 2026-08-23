#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for the MARS56 S4P candidate-queue provenance preflight.
# It does not run EMX. It verifies that production accepts only a queue that
# traces back to physical-feature target bins and rejects geometry-only queues.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
AUDIT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_mars56_s4p_candidate_queue_provenance.py"
MATERIALIZE="$ROOT_DIR/rfic-transformer-inverse-design/scripts/materialize_physical_feature_targeted_s4p_queue.py"

if [ ! -f "$AUDIT" ] || [ ! -f "$MATERIALIZE" ]; then
  echo "QUEUE_PROVENANCE_PREFLIGHT_BEHAVIOR_STATUS=FAIL missing audit/materializer script" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_queue_preflight.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

write_good_queue() {
  local dir="$1"
  mkdir -p "$dir"
  cat >"$dir/physical_feature_targeted_candidate_selection.csv" <<'CSV'
selection_rank,candidate_index,candidate_id,target_rank,target_bin_key,target_recommended_new_samples,inside_target_bin,selection_score,pred_lp_nh,target_lp_nh,target_lp_nh_min,target_lp_nh_max,pred_ls_nh,target_ls_nh,target_ls_nh_min,target_ls_nh_max,pred_q,target_q,target_q_min,target_q_max,pred_k,target_k,target_k_min,target_k_max,candidate__geom__primary_outer_width_um,candidate__geom__primary_outer_height_um,candidate__geom__secondary_outer_width_um,candidate__geom__secondary_outer_height_um,candidate__geom__line_width_um,candidate__geom__primary_width_um,candidate__geom__secondary_width_um,candidate__geom__primary_terminal_y_span_um,candidate__geom__secondary_terminal_y_span_um,candidate__geom__offset_um,candidate__geom__primary_feed_extension_um,candidate__geom__secondary_feed_extension_um
1,0,cand_a,1,lp0_ls0_q0_k0,3,true,0.01,1.1,1.1,1.0,1.2,1.2,1.2,1.1,1.3,10,10,8,12,0.45,0.45,0.4,0.5,220,230,210,225,6,6,6,50,52,0,130,132
2,1,cand_b,1,lp0_ls0_q0_k0,3,true,0.02,1.15,1.1,1.0,1.2,1.18,1.2,1.1,1.3,10.5,10,8,12,0.46,0.45,0.4,0.5,225,235,215,230,6.2,6.2,6.2,51,53,2,131,133
3,2,cand_c,1,lp0_ls0_q0_k0,3,true,0.03,1.08,1.1,1.0,1.2,1.24,1.2,1.1,1.3,9.8,10,8,12,0.43,0.45,0.4,0.5,230,240,220,235,6.4,6.4,6.4,52,54,-2,132,134
CSV
  cat >"$dir/physical_feature_targeted_candidate_selection_summary.json" <<JSON
{
  "overall_status": "PASS",
  "decision": "USE_SELECTED_CANDIDATES_FOR_NEXT_EMX_BATCH",
  "selected_csv": "$dir/physical_feature_targeted_candidate_selection.csv",
  "selected_candidate_count": 3,
  "selected_inside_target_bin_count": 3,
  "feature_columns": ["lp_nh", "ls_nh", "q", "k"]
}
JSON
  python3 "$MATERIALIZE" \
    --selection-csv "$dir/physical_feature_targeted_candidate_selection.csv" \
    --out-dir "$dir" \
    --expected-count 3 >/dev/null
}

write_geometry_only_queue() {
  local dir="$1"
  mkdir -p "$dir"
  cat >"$dir/mars56_grounded_s4p_candidate_queue.csv" <<'CSV'
candidate_id,bootstrap_source,geom__primary_outer_width_um,geom__primary_outer_height_um,geom__secondary_outer_width_um,geom__secondary_outer_height_um,geom__line_width_um,geom__primary_width_um,geom__secondary_width_um,geom__primary_terminal_y_span_um,geom__secondary_terminal_y_span_um,geom__offset_um,geom__primary_feed_extension_um,geom__secondary_feed_extension_um
mars56_grounded_s4p_000001,geometry_space_filling_no_physical_labels,220,230,210,225,6,6,6,50,52,0,130,132
mars56_grounded_s4p_000002,geometry_space_filling_no_physical_labels,225,235,215,230,6.2,6.2,6.2,51,53,2,131,133
mars56_grounded_s4p_000003,geometry_space_filling_no_physical_labels,230,240,220,235,6.4,6.4,6.4,52,54,-2,132,134
CSV
  cat >"$dir/mars56_grounded_s4p_candidate_queue_summary.json" <<JSON
{
  "overall_status": "PASS",
  "decision": "USE_GEOMETRY_QUEUE_FOR_MARS56_GROUNDED_S4P_EMX",
  "candidate_csv": "$dir/mars56_grounded_s4p_candidate_queue.csv",
  "sample_count": 3,
  "expected_count": 3,
  "limitations": ["This queue contains geometry only; no physical-feature values are fabricated."]
}
JSON
}

run_expect() {
  local name="$1"
  local expected_status="$2"
  local expected_reason="${3:-}"
  local case_dir="$TMP_ROOT/$name"
  local out_dir="$case_dir/out"
  python3 "$AUDIT" \
    --candidate-csv "$case_dir/mars56_grounded_s4p_candidate_queue.csv" \
    --expected-count 3 \
    --out-dir "$out_dir" \
    --no-fail-exit >"$case_dir/run.out"
  python3 - "$out_dir/mars56_s4p_candidate_queue_provenance_summary.json" "$expected_status" "$expected_reason" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
reason = sys.argv[3]
assert summary["overall_status"] == expected, summary
if reason:
    haystack = json.dumps(summary, ensure_ascii=False)
    assert reason in haystack, haystack
PY
  echo "QUEUE_PROVENANCE_PREFLIGHT_CASE=$name status=PASS"
}

write_good_queue "$TMP_ROOT/physical_targeted"
run_expect physical_targeted PASS

write_geometry_only_queue "$TMP_ROOT/geometry_only"
run_expect geometry_only FAIL geometry_space_filling_no_physical_labels

write_good_queue "$TMP_ROOT/too_few_rows"
python3 - "$TMP_ROOT/too_few_rows/mars56_grounded_s4p_candidate_queue.csv" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
PY
run_expect too_few_rows FAIL candidate_rows_meet_expected_count

write_good_queue "$TMP_ROOT/missing_selection_source"
rm "$TMP_ROOT/missing_selection_source/physical_feature_targeted_candidate_selection.csv"
run_expect missing_selection_source FAIL source_selection_csv_exists

echo "QUEUE_PROVENANCE_PREFLIGHT_BEHAVIOR_STATUS=PASS"
