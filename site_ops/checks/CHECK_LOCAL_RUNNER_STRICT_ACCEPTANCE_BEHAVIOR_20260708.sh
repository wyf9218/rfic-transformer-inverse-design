#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for the queue-driven 100k production runner's final
# strict-acceptance logic. It does not run EMX. It extracts the runner's final
# Python summary block and feeds synthetic dataset/checkpoint/audit summaries.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
RUNNER="$ROOT_DIR/rfic-transformer-inverse-design/scripts/run_mars56_s4p_100k_chunk_from_queue.sh"

if [ ! -f "$RUNNER" ]; then
  echo "RUNNER_STRICT_ACCEPTANCE_BEHAVIOR_STATUS=FAIL missing runner: $RUNNER" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_runner_acceptance.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

SUMMARY_PY="$TMP_ROOT/runner_summary_block.py"
python3 - "$RUNNER" "$SUMMARY_PY" <<'PY'
from pathlib import Path
import sys

runner = Path(sys.argv[1])
target = Path(sys.argv[2])
text = runner.read_text(encoding="utf-8")
marker = 'python3 - "$OUT_DIR" "$CHUNK_INDEX" "$COUNT"'
start = text.find(marker)
if start < 0:
    raise SystemExit("runner final summary heredoc marker not found")
start = text.find("\n", start) + 1
end = text.find("\nPY\n", start)
if end < 0:
    raise SystemExit("runner final summary heredoc end not found")
target.write_text(text[start:end] + "\n", encoding="utf-8")
PY

write_json() {
  local path="$1"
  local payload="$2"
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$payload" >"$path"
}

checkpoint_payload() {
  local variant="$1"
  case "$variant" in
    good)
      cat <<'JSON'
{
  "overall_status": "PASS",
  "expected_count": 3,
  "min_valid": 3,
  "physical_uniformity_gate": {
    "k_mode": "magnitude",
    "bins": 10,
    "pair_bins": 10,
    "min_1d_occupied_fraction": 0.9,
    "min_1d_entropy_fraction": 0.9,
    "max_1d_bin_imbalance": 2.5,
    "min_pair_occupied_fraction": 0.65,
    "min_pair_entropy_fraction": 0.8,
    "four_d_bins": 4,
    "require_four_d_gate": true,
    "min_four_d_occupied_fraction": 0.5,
    "min_four_d_normalized_entropy": 0.8,
    "max_four_d_nonzero_bin_imbalance": 4.0,
    "target_ranges": {
      "lp": {"min": 0.5, "max": 3.0},
      "ls": {"min": 0.5, "max": 3.0},
      "q": {"min": 5.0, "max": 25.0},
      "k": {"min": 0.0, "max": 0.8}
    }
  },
  "statuses": {
    "stable_index": "PASS",
    "response_features": "PASS",
    "enrichment": "PASS",
    "uniformity": "PASS",
    "uniformity_manifest": "PASS",
    "training": "PASS",
    "model": "PASS",
    "traceability": "PASS"
  },
  "details": {
    "uniformity": {
      "valid_feature_count": 3,
      "k_mode": "magnitude",
      "ranges": {
        "lp": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": true, "valid": true},
        "ls": {"min": 0.5, "max": 3.0, "source": "explicit", "explicit": true, "valid": true},
        "q": {"min": 5.0, "max": 25.0, "source": "explicit", "explicit": true, "valid": true},
        "k": {"min": 0.0, "max": 0.8, "source": "explicit", "explicit": true, "valid": true}
      },
      "four_dimensional_uniformity": {
        "occupied_fraction": 0.5,
        "occupied_bins": 128,
        "total_bins": 256,
        "normalized_entropy": 0.9,
        "max_to_min_nonzero_ratio": 2.0
      },
      "one_dimensional_uniformity": {
        "lp": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
        "ls": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
        "q": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2},
        "k": {"occupied_fraction": 1.0, "normalized_entropy": 0.95, "max_to_min_nonzero_ratio": 1.2}
      },
      "pairwise_uniformity": {
        "lp_ls": {"occupied_fraction": 0.7, "normalized_entropy": 0.85},
        "lp_q": {"occupied_fraction": 0.7, "normalized_entropy": 0.85},
        "lp_k": {"occupied_fraction": 0.7, "normalized_entropy": 0.85},
        "ls_q": {"occupied_fraction": 0.7, "normalized_entropy": 0.85},
        "ls_k": {"occupied_fraction": 0.7, "normalized_entropy": 0.85},
        "q_k": {"occupied_fraction": 0.7, "normalized_entropy": 0.85}
      },
      "k_sign_diagnostics": {
        "uniformity_k_axis": "|K|",
        "signed_k_count": 3,
        "positive_k_count": 2,
        "zero_k_count": 0,
        "negative_k_count": 1
      }
    },
    "uniformity_manifest": {"visual_artifact_count": 3, "require_plots": true},
    "training": {"training_count": 3},
    "model": {
      "usable_row_count": 3,
      "test_row_count": 1,
      "metrics": {
        "test_count": 1,
        "geometry_count": 11,
        "max_normalized_mae": 0.1,
        "max_normalized_rmse": 0.2,
        "mean_normalized_mae": 0.05,
        "mean_normalized_rmse": 0.1
      }
    },
    "traceability": {
      "stable_manifest_rows": 3,
      "stable_unique_evaluations": 3,
      "response_feature_rows": 3,
      "response_unique_evaluations": 3,
      "response_dataset_rows": 3,
      "response_dataset_unique_evaluations": 3,
      "enriched_rows": 3,
      "enriched_unique_evaluations": 3,
      "training_rows": 3,
      "training_unique_evaluations": 3
    }
  }
}
JSON
      ;;
    missing_traceability_details)
      cat <<'JSON'
{
  "overall_status": "PASS",
  "expected_count": 3,
  "min_valid": 3,
  "statuses": {
    "stable_index": "PASS",
    "response_features": "PASS",
    "enrichment": "PASS",
    "uniformity": "PASS",
    "uniformity_manifest": "PASS",
    "training": "PASS",
    "model": "PASS",
    "traceability": "PASS"
  },
  "details": {
    "uniformity": {
      "valid_feature_count": 3,
      "k_mode": "magnitude",
      "k_sign_diagnostics": {
        "uniformity_k_axis": "|K|",
        "signed_k_count": 3,
        "positive_k_count": 2,
        "zero_k_count": 0,
        "negative_k_count": 1
      }
    },
    "uniformity_manifest": {"visual_artifact_count": 3, "require_plots": true},
    "training": {"training_count": 3},
    "model": {"usable_row_count": 3}
  }
}
JSON
      ;;
    missing_uniformity_manifest_details)
      cat <<'JSON'
{
  "overall_status": "PASS",
  "expected_count": 3,
  "min_valid": 3,
  "statuses": {
    "stable_index": "PASS",
    "response_features": "PASS",
    "enrichment": "PASS",
    "uniformity": "PASS",
    "uniformity_manifest": "PASS",
    "training": "PASS",
    "model": "PASS",
    "traceability": "PASS"
  },
  "details": {
    "uniformity": {
      "valid_feature_count": 3,
      "k_mode": "magnitude",
      "k_sign_diagnostics": {
        "uniformity_k_axis": "|K|",
        "signed_k_count": 3,
        "positive_k_count": 2,
        "zero_k_count": 0,
        "negative_k_count": 1
      }
    },
    "training": {"training_count": 3},
    "model": {"usable_row_count": 3},
    "traceability": {
      "stable_manifest_rows": 3,
      "response_feature_rows": 3,
      "enriched_rows": 3,
      "training_rows": 3
    }
  }
}
JSON
      ;;
    low_traceability_row)
      cat <<'JSON'
{
  "overall_status": "PASS",
  "expected_count": 3,
  "min_valid": 3,
  "statuses": {
    "stable_index": "PASS",
    "response_features": "PASS",
    "enrichment": "PASS",
    "uniformity": "PASS",
    "uniformity_manifest": "PASS",
    "training": "PASS",
    "model": "PASS",
    "traceability": "PASS"
  },
  "details": {
    "uniformity": {
      "valid_feature_count": 3,
      "k_mode": "magnitude",
      "k_sign_diagnostics": {
        "uniformity_k_axis": "|K|",
        "signed_k_count": 3,
        "positive_k_count": 2,
        "zero_k_count": 0,
        "negative_k_count": 1
      }
    },
    "uniformity_manifest": {"visual_artifact_count": 3, "require_plots": true},
    "training": {"training_count": 3},
    "model": {"usable_row_count": 3},
    "traceability": {
      "stable_manifest_rows": 3,
      "response_feature_rows": 2,
      "enriched_rows": 3,
      "training_rows": 3
    }
  }
}
JSON
      ;;
    missing_k_sign_diagnostics)
      cat <<'JSON'
{
  "overall_status": "PASS",
  "expected_count": 3,
  "min_valid": 3,
  "statuses": {
    "stable_index": "PASS",
    "response_features": "PASS",
    "enrichment": "PASS",
    "uniformity": "PASS",
    "uniformity_manifest": "PASS",
    "training": "PASS",
    "model": "PASS",
    "traceability": "PASS"
  },
  "details": {
    "uniformity": {"valid_feature_count": 3, "k_mode": "magnitude"},
    "uniformity_manifest": {"visual_artifact_count": 3, "require_plots": true},
    "training": {"training_count": 3},
    "model": {"usable_row_count": 3},
    "traceability": {
      "stable_manifest_rows": 3,
      "response_feature_rows": 3,
      "enriched_rows": 3,
      "training_rows": 3
    }
  }
}
JSON
      ;;
    missing_queue_preflight)
      checkpoint_payload good
      ;;
    low_four_d_occupancy)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["uniformity"]["four_dimensional_uniformity"]["occupied_fraction"]=0.25; json.dump(data, sys.stdout)'
      ;;
    low_four_d_entropy)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["uniformity"]["four_dimensional_uniformity"]["normalized_entropy"]=0.79; json.dump(data, sys.stdout)'
      ;;
    high_four_d_imbalance)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["uniformity"]["four_dimensional_uniformity"]["max_to_min_nonzero_ratio"]=5.0; json.dump(data, sys.stdout)'
      ;;
    low_one_d_entropy)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["uniformity"]["one_dimensional_uniformity"]["lp"]["normalized_entropy"]=0.50; json.dump(data, sys.stdout)'
      ;;
    low_pair_occupancy)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["uniformity"]["pairwise_uniformity"]["lp_ls"]["occupied_fraction"]=0.20; json.dump(data, sys.stdout)'
      ;;
    missing_model_metrics)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["model"].pop("metrics"); json.dump(data, sys.stdout)'
      ;;
    zero_model_test_rows)
      checkpoint_payload good | python3 -c 'import json,sys; data=json.load(sys.stdin); data["details"]["model"]["test_row_count"]=0; data["details"]["model"]["metrics"]["test_count"]=0; json.dump(data, sys.stdout)'
      ;;
    *)
      echo "unknown checkpoint payload variant: $variant" >&2
      return 2
      ;;
  esac
}

run_case() {
  local name="$1"
  local variant="$2"
  local expected_decision="$3"
  local expected_reason="${4:-}"

  local case_dir="$TMP_ROOT/$name"
  local out_dir="$case_dir/out"
  local dataset_dir="$case_dir/dataset"
  local checkpoint_dir="$case_dir/checkpoint"
  local audit_dir="$case_dir/audit"
  local preflight_dir="$out_dir/candidate_queue_preflight"
  mkdir -p "$out_dir" "$dataset_dir" "$checkpoint_dir" "$audit_dir" "$preflight_dir"

  write_json "$dataset_dir/parallel_candidate_queue_dataset_summary.json" '{"overall_status":"PASS"}'
  checkpoint_payload "$variant" >"$checkpoint_dir/mars56_s4p_physical_checkpoint_pipeline_summary.json"
  write_json "$audit_dir/mars56_s4p_million_chunk_checkpoint_summary.json" '{"overall_status":"PASS"}'
  if [ "$variant" != "missing_queue_preflight" ]; then
    write_json "$preflight_dir/mars56_s4p_candidate_queue_provenance_summary.json" '{"overall_status":"PASS"}'
  fi

  python3 "$SUMMARY_PY" "$out_dir" 1 3 "$case_dir/candidates.csv" "$dataset_dir" "$checkpoint_dir" "$audit_dir" 0.50 0.5 3 0.5 3 5 25 0 0.8 0.90 0.90 2.50 0.65 0.80 0.80 4.0 >"$case_dir/run.out"
  python3 - "$out_dir/mars56_s4p_100k_chunk_run_summary.json" "$expected_decision" "$expected_reason" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
expected_decision = sys.argv[2]
expected_reason = sys.argv[3]
assert summary["decision"] == expected_decision, summary
if expected_reason:
    reasons = "\n".join(summary["strict_acceptance"].get("checkpoint_proof_reasons", []))
    assert expected_reason in reasons, reasons
PY
  echo "RUNNER_STRICT_ACCEPTANCE_CASE=$name status=PASS"
}

run_case complete_chain good ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK
run_case missing_traceability_details missing_traceability_details STOP_BEFORE_NEXT_100K_CHUNK traceability.details_missing
run_case missing_uniformity_manifest_details missing_uniformity_manifest_details STOP_BEFORE_NEXT_100K_CHUNK uniformity_manifest.visual_artifact_count
run_case low_traceability_row low_traceability_row STOP_BEFORE_NEXT_100K_CHUNK traceability.response_feature_rows=2
run_case missing_k_sign_diagnostics missing_k_sign_diagnostics STOP_BEFORE_NEXT_100K_CHUNK uniformity.k_sign_diagnostics
run_case missing_model_metrics missing_model_metrics STOP_BEFORE_NEXT_100K_CHUNK model.metrics=MISSING
run_case zero_model_test_rows zero_model_test_rows STOP_BEFORE_NEXT_100K_CHUNK model.test_row_count=0
run_case missing_queue_preflight missing_queue_preflight STOP_BEFORE_NEXT_100K_CHUNK
run_case low_four_d_occupancy low_four_d_occupancy STOP_BEFORE_NEXT_100K_CHUNK uniformity.four_dimensional_uniformity.occupied_fraction
run_case low_four_d_entropy low_four_d_entropy STOP_BEFORE_NEXT_100K_CHUNK uniformity.four_dimensional_uniformity.normalized_entropy
run_case high_four_d_imbalance high_four_d_imbalance STOP_BEFORE_NEXT_100K_CHUNK uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio
run_case low_one_d_entropy low_one_d_entropy STOP_BEFORE_NEXT_100K_CHUNK uniformity.one_dimensional_uniformity.lp.normalized_entropy
run_case low_pair_occupancy low_pair_occupancy STOP_BEFORE_NEXT_100K_CHUNK uniformity.pairwise_uniformity.lp_ls.occupied_fraction

echo "RUNNER_STRICT_ACCEPTANCE_BEHAVIOR_STATUS=PASS"
