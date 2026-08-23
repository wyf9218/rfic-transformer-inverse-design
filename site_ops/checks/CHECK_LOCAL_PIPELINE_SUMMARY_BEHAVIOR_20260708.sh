#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for run_mars56_s4p_physical_checkpoint_pipeline.sh final
# summary logic. It does not run EMX or the full checkpoint pipeline.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
PIPELINE="$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh"

if [ ! -f "$PIPELINE" ]; then
  echo "PIPELINE_SUMMARY_BEHAVIOR_STATUS=FAIL missing pipeline: $PIPELINE" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_pipeline_summary.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

SUMMARY_PY="$TMP_ROOT/pipeline_summary_block.py"
python3 - "$PIPELINE" "$SUMMARY_PY" <<'PY'
from pathlib import Path
import sys

pipeline = Path(sys.argv[1])
target = Path(sys.argv[2])
text = pipeline.read_text(encoding="utf-8")
marker = 'python3 - "$OUT_DIR" "$COUNT" "$MIN_VALID"'
start = text.find(marker)
if start < 0:
    raise SystemExit("pipeline final summary heredoc marker not found")
start = text.find("\n", start) + 1
end = text.find("\nPY\n", start)
if end < 0:
    raise SystemExit("pipeline final summary heredoc end not found")
target.write_text(text[start:end] + "\n", encoding="utf-8")
PY

write_json() {
  local path="$1"
  local payload="$2"
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$payload" >"$path"
}

write_uniformity_summary() {
  local path="$1"
  local variant="${2:-good}"
  mkdir -p "$(dirname "$path")"
  python3 - "$path" "$variant" <<'PY'
import json
import sys

path = sys.argv[1]
variant = sys.argv[2]
summary = {
    "overall_status": "PASS",
    "valid_feature_count": 3,
    "k_mode": "magnitude",
    "ranges": {
        "lp": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True, "valid": True},
        "ls": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": True, "valid": True},
        "q": {"min": 5.0, "max": 25.0, "source": "explicit", "explicit": True, "valid": True},
        "k": {"min": 0.0, "max": 0.8, "source": "explicit", "explicit": True, "valid": True},
    },
    "one_dimensional_uniformity": {
        name: {
            "occupied_fraction": 1.0,
            "normalized_entropy": 0.98,
            "max_to_min_nonzero_ratio": 1.5,
        }
        for name in ("lp", "ls", "q", "k")
    },
    "pairwise_uniformity": {
        name: {"occupied_fraction": 0.70, "normalized_entropy": 0.85}
        for name in ("lp_ls", "lp_q", "lp_k", "ls_q", "ls_k", "q_k")
    },
    "four_dimensional_uniformity": {
        "occupied_fraction": 0.5,
        "occupied_bins": 128,
        "total_bins": 256,
        "normalized_entropy": 0.9,
        "max_to_min_nonzero_ratio": 2.0,
    },
    "k_sign_diagnostics": {
        "uniformity_k_axis": "|K|",
        "signed_k_count": 3,
        "positive_k_count": 2,
        "zero_k_count": 0,
        "negative_k_count": 1,
    },
}

if variant == "missing_k_sign_diagnostics":
    summary.pop("k_sign_diagnostics")
elif variant == "low_four_d_occupancy":
    summary["four_dimensional_uniformity"]["occupied_fraction"] = 0.25
    summary["four_dimensional_uniformity"]["occupied_bins"] = 64
elif variant == "low_four_d_entropy":
    summary["four_dimensional_uniformity"]["normalized_entropy"] = 0.79
elif variant == "high_four_d_imbalance":
    summary["four_dimensional_uniformity"]["max_to_min_nonzero_ratio"] = 5.0
elif variant == "wrong_explicit_range":
    summary["ranges"]["lp"]["max"] = 3.5
elif variant == "observed_range_not_explicit":
    summary["ranges"]["q"] = {
        "min": 5.0,
        "max": 25.0,
        "source": "observed_with_5pct_padding",
        "explicit": False,
        "valid": True,
    }
elif variant == "missing_one_d_uniformity":
    summary.pop("one_dimensional_uniformity")
elif variant == "low_one_d_entropy":
    summary["one_dimensional_uniformity"]["q"]["normalized_entropy"] = 0.85
elif variant == "high_one_d_imbalance":
    summary["one_dimensional_uniformity"]["k"]["max_to_min_nonzero_ratio"] = 3.0
elif variant == "low_pair_occupancy":
    summary["pairwise_uniformity"]["lp_k"]["occupied_fraction"] = 0.60
elif variant == "low_pair_entropy":
    summary["pairwise_uniformity"]["ls_q"]["normalized_entropy"] = 0.75
elif variant != "good":
    raise SystemExit(f"unknown uniformity summary variant: {variant}")

with open(path, "w", encoding="utf-8") as handle:
    json.dump(summary, handle, separators=(",", ":"))
    handle.write("\n")
PY
}

create_case() {
  local out_dir="$1"
  local variant="$2"
  mkdir -p "$out_dir/stable_index/evaluations/eval_001/emx" \
    "$out_dir/stable_index/evaluations/eval_002/emx" \
    "$out_dir/stable_index/evaluations/eval_003/emx"
  printf '# s4p\n' >"$out_dir/stable_index/evaluations/eval_001/emx/eval_001.s4p"
  printf '# s4p\n' >"$out_dir/stable_index/evaluations/eval_002/emx/eval_002.s4p"
  printf '# s4p\n' >"$out_dir/stable_index/evaluations/eval_003/emx/eval_003.s4p"

  write_json "$out_dir/response_features/response_feature_extraction_summary.json" '{"overall_status":"PASS"}'
  write_json "$out_dir/enriched_geometry/geometry_enrichment_manifest.json" '{"overall_status":"PASS"}'
  write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" good
  write_json "$out_dir/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json" '{"overall_status":"PASS","training_count":3}'
  write_json "$out_dir/physical_feature_inverse_checkpoint_test/physical_feature_inverse_checkpoint_test_summary.json" '{"overall_status":"PASS","quality_status":"PASS","decision":"MODEL_CHECKPOINT_TEST_COMPLETED","method":"polynomial_ridge_holdout","usable_row_count":3,"train_row_count":2,"test_row_count":1,"metrics":{"test_count":1,"geometry_count":11,"max_normalized_mae":0.1,"max_normalized_rmse":0.2,"mean_normalized_mae":0.05,"mean_normalized_rmse":0.1}}'

  case "$variant" in
    good)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    missing_traceability_field)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    low_visual_count)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":2,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    low_training_count)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json" '{"overall_status":"PASS","training_count":2}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    missing_model_metrics)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_feature_inverse_checkpoint_test/physical_feature_inverse_checkpoint_test_summary.json" '{"overall_status":"PASS","quality_status":"PASS","decision":"MODEL_CHECKPOINT_TEST_COMPLETED","method":"polynomial_ridge_holdout","usable_row_count":3,"train_row_count":2,"test_row_count":1}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    zero_model_test_rows)
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_feature_inverse_checkpoint_test/physical_feature_inverse_checkpoint_test_summary.json" '{"overall_status":"PASS","quality_status":"PASS","decision":"MODEL_CHECKPOINT_TEST_COMPLETED","method":"polynomial_ridge_holdout","usable_row_count":3,"train_row_count":3,"test_row_count":0,"metrics":{"test_count":0,"geometry_count":11,"max_normalized_mae":0.1,"max_normalized_rmse":0.2,"mean_normalized_mae":0.05,"mean_normalized_rmse":0.1}}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    missing_k_sign_diagnostics)
      write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" missing_k_sign_diagnostics
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    low_four_d_occupancy)
      write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" low_four_d_occupancy
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    wrong_explicit_range)
      write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" wrong_explicit_range
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    observed_range_not_explicit)
      write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" observed_range_not_explicit
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    missing_one_d_uniformity|low_one_d_entropy|high_one_d_imbalance|low_pair_occupancy|low_pair_entropy|low_four_d_entropy|high_four_d_imbalance)
      write_uniformity_summary "$out_dir/physical_feature_uniformity/physical_feature_uniformity_summary.json" "$variant"
      write_json "$out_dir/physical_feature_uniformity/physical_feature_uniformity_manifest.json" '{"overall_status":"PASS","visual_artifact_count":3,"require_plots":true}'
      write_json "$out_dir/physical_checkpoint_traceability/physical_checkpoint_traceability_summary.json" '{"overall_status":"PASS","expected_count":3,"min_valid":3,"row_counts":{"stable_manifest_rows":3,"stable_unique_evaluations":3,"response_feature_rows":3,"response_unique_evaluations":3,"response_dataset_rows":3,"response_dataset_unique_evaluations":3,"enriched_rows":3,"enriched_unique_evaluations":3,"training_rows":3,"training_unique_evaluations":3}}'
      ;;
    *)
      echo "unknown case variant: $variant" >&2
      return 2
      ;;
  esac
}

run_case() {
  local name="$1"
  local variant="$2"
  local expected_status="$3"
  local expected_reason="${4:-}"
  local out_dir="$TMP_ROOT/$name"
  create_case "$out_dir" "$variant"
  python3 "$SUMMARY_PY" "$out_dir" 3 3 10 10 4 0.50 0.5 3 0.5 3 5 25 0 0.8 1 0.90 0.90 2.50 0.65 0.80 0.80 4.0 >"$out_dir.out"
  python3 - "$out_dir/mars56_s4p_physical_checkpoint_pipeline_summary.json" "$expected_status" "$expected_reason" <<'PY'
import json
import sys
summary = json.load(open(sys.argv[1], encoding="utf-8"))
expected_status = sys.argv[2]
expected_reason = sys.argv[3]
assert summary["overall_status"] == expected_status, summary
if expected_reason:
    reasons = "\n".join(summary.get("proof_reasons", []))
    assert expected_reason in reasons, reasons
PY
  echo "PIPELINE_SUMMARY_CASE=$name status=PASS"
}

run_case complete_chain good PASS
run_case missing_traceability_field missing_traceability_field FAIL traceability.response_feature_rows=MISSING
run_case low_visual_count low_visual_count FAIL uniformity_manifest.visual_artifact_count=2
run_case low_training_count low_training_count FAIL training.training_count=2
run_case missing_model_metrics missing_model_metrics FAIL model.metrics=MISSING
run_case zero_model_test_rows zero_model_test_rows FAIL model.test_row_count=0
run_case missing_k_sign_diagnostics missing_k_sign_diagnostics FAIL uniformity.k_sign_diagnostics
run_case low_four_d_occupancy low_four_d_occupancy FAIL uniformity.four_dimensional_uniformity.occupied_fraction
run_case low_four_d_entropy low_four_d_entropy FAIL uniformity.four_dimensional_uniformity.normalized_entropy
run_case high_four_d_imbalance high_four_d_imbalance FAIL uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio
run_case wrong_explicit_range wrong_explicit_range FAIL "uniformity.ranges.lp=(0.5,3.5),expected=(0.5,3.0)"
run_case observed_range_not_explicit observed_range_not_explicit FAIL "uniformity.ranges.q.explicit=False,source='observed_with_5pct_padding'"
run_case missing_one_d_uniformity missing_one_d_uniformity FAIL uniformity.one_dimensional_uniformity
run_case low_one_d_entropy low_one_d_entropy FAIL uniformity.one_dimensional_uniformity.q.normalized_entropy
run_case high_one_d_imbalance high_one_d_imbalance FAIL uniformity.one_dimensional_uniformity.k.max_to_min_nonzero_ratio
run_case low_pair_occupancy low_pair_occupancy FAIL uniformity.pairwise_uniformity.lp_k.occupied_fraction
run_case low_pair_entropy low_pair_entropy FAIL uniformity.pairwise_uniformity.ls_q.normalized_entropy

echo "PIPELINE_SUMMARY_BEHAVIOR_STATUS=PASS"
