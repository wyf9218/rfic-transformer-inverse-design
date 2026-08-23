#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for audit_physical_checkpoint_traceability.py.
#
# This does not touch MARS or any production dataset. It builds tiny synthetic
# checkpoint folders and verifies that the traceability audit accepts a complete
# source-to-model evidence chain and rejects broken chains.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
AUDIT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_physical_checkpoint_traceability.py"

if [ ! -f "$AUDIT" ]; then
  echo "TRACEABILITY_AUDIT_BEHAVIOR_STATUS=FAIL missing audit script: $AUDIT" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_traceability_behavior.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

write_csv_rows() {
  local path="$1"
  shift
  mkdir -p "$(dirname "$path")"
  {
    echo "evaluation,lp_nh,ls_nh,q,k_abs"
    local evaluation
    for evaluation in "$@"; do
      echo "$evaluation,1.0,1.1,10.0,0.4"
    done
  } >"$path"
}

write_stable_manifest() {
  local path="$1"
  shift
  mkdir -p "$(dirname "$path")"
  {
    echo "evaluation,source_path,indexed_path,numeric_rows_detected"
    local evaluation
    for evaluation in "$@"; do
      echo "$evaluation,$TMP_ROOT/dataset/$evaluation.s4p,$TMP_ROOT/pass/stable_index/indexed/$evaluation.s4p,111"
    done
  } >"$path"
}

write_json() {
  local path="$1"
  local payload="$2"
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$payload" >"$path"
}

create_pass_checkpoint() {
  local checkpoint="$1"
  local dataset="$TMP_ROOT/dataset"
  local evals=(eval_001 eval_002 eval_003)
  mkdir -p "$dataset" "$checkpoint/stable_index/indexed"

  local evaluation
  for evaluation in "${evals[@]}"; do
    printf '# synthetic touchstone %s\n1 0 0 0 0\n' "$evaluation" >"$dataset/$evaluation.s4p"
    printf '# indexed synthetic touchstone %s\n1 0 0 0 0\n' "$evaluation" >"$checkpoint/stable_index/indexed/$evaluation.s4p"
  done

  write_json "$checkpoint/stable_index/stable_touchstone_index_summary.json" \
    "{\"status\":\"PASS\",\"indexed_count\":3,\"dataset_dir\":\"$dataset\"}"
  write_json "$checkpoint/stable_index/dataset_manifest.json" \
    "{\"source_dataset_dir\":\"$dataset\"}"
  write_stable_manifest "$checkpoint/stable_index/stable_touchstone_index_manifest.csv" "${evals[@]}"

  write_json "$checkpoint/response_features/response_feature_extraction_summary.json" \
    "{\"overall_status\":\"PASS\",\"dataset_dir\":\"$checkpoint/stable_index\",\"counts\":{\"ok_rows\":3}}"
  write_json "$checkpoint/response_features/dataset_manifest.json" \
    "{\"source_dataset_dir\":\"$checkpoint/stable_index\"}"
  write_csv_rows "$checkpoint/response_features/response_features.csv" "${evals[@]}"
  write_csv_rows "$checkpoint/response_features/dataset_rows.csv" "${evals[@]}"

  write_json "$checkpoint/enriched_geometry/geometry_enrichment_manifest.json" \
    "{\"overall_status\":\"PASS\",\"enriched_row_count\":3}"
  write_csv_rows "$checkpoint/enriched_geometry/dataset_rows.csv" "${evals[@]}"

  write_json "$checkpoint/physical_feature_uniformity/physical_feature_uniformity_summary.json" \
    "{\"overall_status\":\"PASS\",\"valid_feature_count\":3}"
  write_json "$checkpoint/physical_feature_uniformity/physical_feature_uniformity_manifest.json" \
    "{\"overall_status\":\"PASS\",\"visual_artifact_count\":3}"

  write_json "$checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json" \
    "{\"overall_status\":\"PASS\",\"training_count\":3,\"dataset_source\":{\"sha256\":\"abc123\"}}"
  write_csv_rows "$checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv" "${evals[@]}"

  write_json "$checkpoint/physical_feature_inverse_checkpoint_test/physical_feature_inverse_checkpoint_test_summary.json" \
    "{\"overall_status\":\"PASS\",\"usable_row_count\":3}"
  write_json "$checkpoint/mars56_s4p_physical_checkpoint_pipeline_summary.json" \
    "{\"overall_status\":\"PASS\"}"
}

run_case_expect_pass() {
  local checkpoint="$TMP_ROOT/pass"
  create_pass_checkpoint "$checkpoint"
  python3 "$AUDIT" \
    --checkpoint-dir "$checkpoint" \
    --dataset-dir "$TMP_ROOT/dataset" \
    --expected-count 3 \
    --min-valid 3 >"$TMP_ROOT/pass.out"
  python3 - "$checkpoint/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["overall_status"] == "PASS", data
assert data["row_counts"]["stable_manifest_rows"] == 3, data["row_counts"]
assert data["row_counts"]["stable_unique_evaluations"] == 3, data["row_counts"]
assert data["row_counts"]["response_feature_rows"] == 3, data["row_counts"]
assert data["row_counts"]["response_unique_evaluations"] == 3, data["row_counts"]
assert data["row_counts"]["response_dataset_rows"] == 3, data["row_counts"]
assert data["row_counts"]["response_dataset_unique_evaluations"] == 3, data["row_counts"]
assert data["row_counts"]["enriched_rows"] == 3, data["row_counts"]
assert data["row_counts"]["enriched_unique_evaluations"] == 3, data["row_counts"]
assert data["row_counts"]["training_rows"] == 3, data["row_counts"]
assert data["row_counts"]["training_unique_evaluations"] == 3, data["row_counts"]
assert data["evaluation_overlaps"]["stable_vs_response_missing"] == 0, data["evaluation_overlaps"]
PY
  echo "TRACEABILITY_BEHAVIOR_CASE=complete_chain status=PASS"
}

run_case_expect_missing_source_fail() {
  local checkpoint="$TMP_ROOT/missing_source"
  create_pass_checkpoint "$checkpoint"
  rm -f "$TMP_ROOT/dataset/eval_002.s4p"
  if python3 "$AUDIT" \
    --checkpoint-dir "$checkpoint" \
    --dataset-dir "$TMP_ROOT/dataset" \
    --expected-count 3 \
    --min-valid 3 >"$TMP_ROOT/missing_source.out" 2>&1; then
    echo "TRACEABILITY_BEHAVIOR_CASE=missing_source status=FAIL unexpected_pass" >&2
    exit 1
  fi
  python3 - "$checkpoint/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
checks = {item["name"]: item["pass"] for item in data["checks"]}
assert data["overall_status"] == "FAIL", data["overall_status"]
assert checks["stable_source_paths_exist"] is False, checks
PY
  echo "TRACEABILITY_BEHAVIOR_CASE=missing_source status=PASS"
}

run_case_expect_low_training_fail() {
  local checkpoint="$TMP_ROOT/low_training"
  create_pass_checkpoint "$checkpoint"
  write_json "$checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json" \
    "{\"overall_status\":\"PASS\",\"training_count\":2,\"dataset_source\":{\"sha256\":\"abc123\"}}"
  write_csv_rows "$checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv" eval_001 eval_002
  if python3 "$AUDIT" \
    --checkpoint-dir "$checkpoint" \
    --dataset-dir "$TMP_ROOT/dataset" \
    --expected-count 3 \
    --min-valid 3 >"$TMP_ROOT/low_training.out" 2>&1; then
    echo "TRACEABILITY_BEHAVIOR_CASE=low_training status=FAIL unexpected_pass" >&2
    exit 1
  fi
  python3 - "$checkpoint/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
checks = {item["name"]: item["pass"] for item in data["checks"]}
assert data["overall_status"] == "FAIL", data["overall_status"]
assert checks["training_count"] is False, checks
assert checks["enriched_to_training_evaluations"] is False, checks
PY
  echo "TRACEABILITY_BEHAVIOR_CASE=low_training status=PASS"
}

run_case_expect_duplicate_training_fail() {
  local checkpoint="$TMP_ROOT/duplicate_training"
  create_pass_checkpoint "$checkpoint"
  write_csv_rows "$checkpoint/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv" eval_001 eval_001 eval_002
  if python3 "$AUDIT" \
    --checkpoint-dir "$checkpoint" \
    --dataset-dir "$TMP_ROOT/dataset" \
    --expected-count 3 \
    --min-valid 3 >"$TMP_ROOT/duplicate_training.out" 2>&1; then
    echo "TRACEABILITY_BEHAVIOR_CASE=duplicate_training status=FAIL unexpected_pass" >&2
    exit 1
  fi
  python3 - "$checkpoint/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
checks = {item["name"]: item["pass"] for item in data["checks"]}
assert data["overall_status"] == "FAIL", data["overall_status"]
assert data["row_counts"]["training_rows"] == 3, data["row_counts"]
assert data["row_counts"]["training_unique_evaluations"] == 2, data["row_counts"]
assert checks["training_unique_evaluations"] is False, checks
assert checks["enriched_to_training_evaluations"] is False, checks
PY
  echo "TRACEABILITY_BEHAVIOR_CASE=duplicate_training status=PASS"
}

run_case_expect_pass
run_case_expect_missing_source_fail
run_case_expect_low_training_fail
run_case_expect_duplicate_training_fail

echo "TRACEABILITY_AUDIT_BEHAVIOR_STATUS=PASS"
