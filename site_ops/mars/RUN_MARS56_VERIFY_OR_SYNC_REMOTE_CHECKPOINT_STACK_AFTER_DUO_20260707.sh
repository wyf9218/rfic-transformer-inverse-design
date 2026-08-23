#!/usr/bin/env bash
set -euo pipefail

# Verify, and optionally sync, the checkpoint stack used by every 100k MARS56
# production checkpoint. This complements the runner check by verifying the
# scripts that produce and audit Lp/Ls/Q/|K| distribution evidence.
#
# Default is read-only:
#   bash RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh
#
# Sync local checkpoint stack to MARS after backing up each remote file:
#   SYNC_REMOTE_CHECKPOINT_STACK=1 bash RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
PROJECT_LOCAL="${PROJECT_LOCAL:-$ROOT_DIR/rfic-transformer-inverse-design}"

JUMP_HOST="${JUMP_HOST:-login.example.edu}"
MARS_HOST="${MARS_HOST:-mars.example.edu}"
USER_NAME="${USER_NAME:-researcher}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"

SYNC_REMOTE_CHECKPOINT_STACK="${SYNC_REMOTE_CHECKPOINT_STACK:-0}"
ALLOW_MISMATCH="${ALLOW_MISMATCH:-0}"
LOCAL_CONTRACT_ONLY="${LOCAL_CONTRACT_ONLY:-0}"
SSH_CONTROL_PATH="${SSH_CONTROL_PATH:-}"
SSH_PERSIST="${SSH_PERSIST:-20m}"

case "$SYNC_REMOTE_CHECKPOINT_STACK" in 0|1) ;;
  *) echo "ERROR: SYNC_REMOTE_CHECKPOINT_STACK must be 0 or 1." >&2; exit 2 ;;
esac
case "$ALLOW_MISMATCH" in 0|1) ;;
  *) echo "ERROR: ALLOW_MISMATCH must be 0 or 1." >&2; exit 2 ;;
esac
case "$LOCAL_CONTRACT_ONLY" in 0|1) ;;
  *) echo "ERROR: LOCAL_CONTRACT_ONLY must be 0 or 1." >&2; exit 2 ;;
esac

RUNNER_LOCAL="$PROJECT_LOCAL/scripts/run_mars56_s4p_100k_chunk_from_queue.sh"
QUEUE_DATASET_LOCAL="$PROJECT_LOCAL/scripts/run_candidate_queue_dataset.py"
QUEUE_PREFLIGHT_LOCAL="$PROJECT_LOCAL/scripts/audit_mars56_s4p_candidate_queue_provenance.py"
ADAPTIVE_ROUND_LOCAL="$PROJECT_LOCAL/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh"
MERGE_ACCEPTED_POOL_LOCAL="$PROJECT_LOCAL/scripts/merge_physical_feature_accepted_pool.py"
PLAN_ACQUISITION_LOCAL="$PROJECT_LOCAL/scripts/plan_physical_feature_balanced_acquisition.py"
SURROGATE_PREDICT_LOCAL="$PROJECT_LOCAL/scripts/build_physical_feature_surrogate_candidate_predictions.py"
MODEL_CHECKPOINT_LOCAL="$PROJECT_LOCAL/scripts/run_accepted_physical_feature_model_checkpoint.sh"
CONFORMAL_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_physical_feature_conformal_calibration.py"
MONDRIAN_COMPARE_LOCAL="$PROJECT_LOCAL/scripts/compare_global_vs_mondrian_conformal_calibration.py"
FREQUENCY_RESOLUTION_LOCAL="$PROJECT_LOCAL/scripts/benchmark_cross_frequency_resolution.py"
TANDEM_TRAIN_LOCAL="$PROJECT_LOCAL/scripts/train_physical_feature_tandem_inverse.py"
TANDEM_ANCHOR_COMPARE_LOCAL="$PROJECT_LOCAL/scripts/compare_tandem_geometry_anchor_ablation.py"
BNI_TEMPERATURE_SELECT_LOCAL="$PROJECT_LOCAL/scripts/select_balanced_mse_bni_temperature.py"
BNI_ABLATION_COMPARE_LOCAL="$PROJECT_LOCAL/scripts/compare_balanced_mse_bni_ablation.py"
LEARNING_CURVE_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_physical_feature_model_learning_curve.py"
PHYSICAL_CELL_TAIL_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_physical_cell_model_tail_error.py"
TANDEM_FEASIBILITY_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_tandem_predicted_geometry_feasibility.py"
TARGET_SELECT_LOCAL="$PROJECT_LOCAL/scripts/select_physical_feature_targeted_candidate_geometries.py"
QUEUE_MATERIALIZE_LOCAL="$PROJECT_LOCAL/scripts/materialize_physical_feature_targeted_s4p_queue.py"
PLAN_CONTRACT_BUILD_LOCAL="$PROJECT_LOCAL/scripts/build_mars56_1m_production_plan_contract.py"
PLAN_CONTRACT_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_mars56_1m_production_plan_contract.py"
PIPELINE_LOCAL="$PROJECT_LOCAL/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh"
UNIFORMITY_LOCAL="$PROJECT_LOCAL/scripts/audit_physical_feature_uniformity.py"
CHUNK_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_mars56_s4p_million_chunk_checkpoint.py"
FINAL_AUDIT_LOCAL="$PROJECT_LOCAL/scripts/audit_accepted_1m_campaign_completion.py"
TRACEABILITY_LOCAL="$PROJECT_LOCAL/scripts/audit_physical_checkpoint_traceability.py"
TARGET_ENVELOPE_LOCAL="$PROJECT_LOCAL/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json"

RUNNER_REMOTE="$REMOTE_PROJECT/scripts/run_mars56_s4p_100k_chunk_from_queue.sh"
QUEUE_DATASET_REMOTE="$REMOTE_PROJECT/scripts/run_candidate_queue_dataset.py"
QUEUE_PREFLIGHT_REMOTE="$REMOTE_PROJECT/scripts/audit_mars56_s4p_candidate_queue_provenance.py"
ADAPTIVE_ROUND_REMOTE="$REMOTE_PROJECT/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh"
MERGE_ACCEPTED_POOL_REMOTE="$REMOTE_PROJECT/scripts/merge_physical_feature_accepted_pool.py"
PLAN_ACQUISITION_REMOTE="$REMOTE_PROJECT/scripts/plan_physical_feature_balanced_acquisition.py"
SURROGATE_PREDICT_REMOTE="$REMOTE_PROJECT/scripts/build_physical_feature_surrogate_candidate_predictions.py"
MODEL_CHECKPOINT_REMOTE="$REMOTE_PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh"
CONFORMAL_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_physical_feature_conformal_calibration.py"
MONDRIAN_COMPARE_REMOTE="$REMOTE_PROJECT/scripts/compare_global_vs_mondrian_conformal_calibration.py"
FREQUENCY_RESOLUTION_REMOTE="$REMOTE_PROJECT/scripts/benchmark_cross_frequency_resolution.py"
TANDEM_TRAIN_REMOTE="$REMOTE_PROJECT/scripts/train_physical_feature_tandem_inverse.py"
TANDEM_ANCHOR_COMPARE_REMOTE="$REMOTE_PROJECT/scripts/compare_tandem_geometry_anchor_ablation.py"
BNI_TEMPERATURE_SELECT_REMOTE="$REMOTE_PROJECT/scripts/select_balanced_mse_bni_temperature.py"
BNI_ABLATION_COMPARE_REMOTE="$REMOTE_PROJECT/scripts/compare_balanced_mse_bni_ablation.py"
LEARNING_CURVE_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_physical_feature_model_learning_curve.py"
PHYSICAL_CELL_TAIL_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_physical_cell_model_tail_error.py"
TANDEM_FEASIBILITY_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_tandem_predicted_geometry_feasibility.py"
TARGET_SELECT_REMOTE="$REMOTE_PROJECT/scripts/select_physical_feature_targeted_candidate_geometries.py"
QUEUE_MATERIALIZE_REMOTE="$REMOTE_PROJECT/scripts/materialize_physical_feature_targeted_s4p_queue.py"
PLAN_CONTRACT_BUILD_REMOTE="$REMOTE_PROJECT/scripts/build_mars56_1m_production_plan_contract.py"
PLAN_CONTRACT_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_mars56_1m_production_plan_contract.py"
PIPELINE_REMOTE="$REMOTE_PROJECT/scripts/run_mars56_s4p_physical_checkpoint_pipeline.sh"
UNIFORMITY_REMOTE="$REMOTE_PROJECT/scripts/audit_physical_feature_uniformity.py"
CHUNK_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_mars56_s4p_million_chunk_checkpoint.py"
FINAL_AUDIT_REMOTE="$REMOTE_PROJECT/scripts/audit_accepted_1m_campaign_completion.py"
TRACEABILITY_REMOTE="$REMOTE_PROJECT/scripts/audit_physical_checkpoint_traceability.py"
TARGET_ENVELOPE_REMOTE="$REMOTE_PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json"

for value in "$SSH_CONTROL_PATH" "$PROJECT_LOCAL" "$REMOTE_PROJECT" "$RUNNER_LOCAL" "$QUEUE_DATASET_LOCAL" "$QUEUE_PREFLIGHT_LOCAL" "$ADAPTIVE_ROUND_LOCAL" "$MERGE_ACCEPTED_POOL_LOCAL" "$PLAN_ACQUISITION_LOCAL" "$SURROGATE_PREDICT_LOCAL" "$MODEL_CHECKPOINT_LOCAL" "$CONFORMAL_AUDIT_LOCAL" "$MONDRIAN_COMPARE_LOCAL" "$FREQUENCY_RESOLUTION_LOCAL" "$TANDEM_TRAIN_LOCAL" "$TANDEM_ANCHOR_COMPARE_LOCAL" "$BNI_TEMPERATURE_SELECT_LOCAL" "$BNI_ABLATION_COMPARE_LOCAL" "$LEARNING_CURVE_AUDIT_LOCAL" "$PHYSICAL_CELL_TAIL_AUDIT_LOCAL" "$TANDEM_FEASIBILITY_AUDIT_LOCAL" "$TARGET_SELECT_LOCAL" "$QUEUE_MATERIALIZE_LOCAL" "$PLAN_CONTRACT_BUILD_LOCAL" "$PLAN_CONTRACT_AUDIT_LOCAL" "$PIPELINE_LOCAL" "$UNIFORMITY_LOCAL" "$CHUNK_AUDIT_LOCAL" "$FINAL_AUDIT_LOCAL" "$TRACEABILITY_LOCAL" "$TARGET_ENVELOPE_LOCAL"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: path/settings contain unsupported quote or newline characters." >&2
    exit 2
  fi
done
for path in "$RUNNER_LOCAL" "$QUEUE_DATASET_LOCAL" "$QUEUE_PREFLIGHT_LOCAL" "$ADAPTIVE_ROUND_LOCAL" "$MERGE_ACCEPTED_POOL_LOCAL" "$PLAN_ACQUISITION_LOCAL" "$SURROGATE_PREDICT_LOCAL" "$MODEL_CHECKPOINT_LOCAL" "$CONFORMAL_AUDIT_LOCAL" "$MONDRIAN_COMPARE_LOCAL" "$FREQUENCY_RESOLUTION_LOCAL" "$TANDEM_TRAIN_LOCAL" "$TANDEM_ANCHOR_COMPARE_LOCAL" "$BNI_TEMPERATURE_SELECT_LOCAL" "$BNI_ABLATION_COMPARE_LOCAL" "$LEARNING_CURVE_AUDIT_LOCAL" "$PHYSICAL_CELL_TAIL_AUDIT_LOCAL" "$TANDEM_FEASIBILITY_AUDIT_LOCAL" "$TARGET_SELECT_LOCAL" "$QUEUE_MATERIALIZE_LOCAL" "$PLAN_CONTRACT_BUILD_LOCAL" "$PLAN_CONTRACT_AUDIT_LOCAL" "$PIPELINE_LOCAL" "$UNIFORMITY_LOCAL" "$CHUNK_AUDIT_LOCAL" "$FINAL_AUDIT_LOCAL" "$TRACEABILITY_LOCAL" "$TARGET_ENVELOPE_LOCAL"; do
  if [ ! -f "$path" ]; then
    echo "ERROR: local checkpoint stack file missing: $path" >&2
    exit 2
  fi
done

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

RUNNER_SHA="$(sha256_file "$RUNNER_LOCAL")"
QUEUE_DATASET_SHA="$(sha256_file "$QUEUE_DATASET_LOCAL")"
QUEUE_PREFLIGHT_SHA="$(sha256_file "$QUEUE_PREFLIGHT_LOCAL")"
ADAPTIVE_ROUND_SHA="$(sha256_file "$ADAPTIVE_ROUND_LOCAL")"
MERGE_ACCEPTED_POOL_SHA="$(sha256_file "$MERGE_ACCEPTED_POOL_LOCAL")"
PLAN_ACQUISITION_SHA="$(sha256_file "$PLAN_ACQUISITION_LOCAL")"
SURROGATE_PREDICT_SHA="$(sha256_file "$SURROGATE_PREDICT_LOCAL")"
MODEL_CHECKPOINT_SHA="$(sha256_file "$MODEL_CHECKPOINT_LOCAL")"
CONFORMAL_AUDIT_SHA="$(sha256_file "$CONFORMAL_AUDIT_LOCAL")"
MONDRIAN_COMPARE_SHA="$(sha256_file "$MONDRIAN_COMPARE_LOCAL")"
FREQUENCY_RESOLUTION_SHA="$(sha256_file "$FREQUENCY_RESOLUTION_LOCAL")"
TANDEM_TRAIN_SHA="$(sha256_file "$TANDEM_TRAIN_LOCAL")"
TANDEM_ANCHOR_COMPARE_SHA="$(sha256_file "$TANDEM_ANCHOR_COMPARE_LOCAL")"
BNI_TEMPERATURE_SELECT_SHA="$(sha256_file "$BNI_TEMPERATURE_SELECT_LOCAL")"
BNI_ABLATION_COMPARE_SHA="$(sha256_file "$BNI_ABLATION_COMPARE_LOCAL")"
LEARNING_CURVE_AUDIT_SHA="$(sha256_file "$LEARNING_CURVE_AUDIT_LOCAL")"
PHYSICAL_CELL_TAIL_AUDIT_SHA="$(sha256_file "$PHYSICAL_CELL_TAIL_AUDIT_LOCAL")"
TANDEM_FEASIBILITY_AUDIT_SHA="$(sha256_file "$TANDEM_FEASIBILITY_AUDIT_LOCAL")"
TARGET_SELECT_SHA="$(sha256_file "$TARGET_SELECT_LOCAL")"
QUEUE_MATERIALIZE_SHA="$(sha256_file "$QUEUE_MATERIALIZE_LOCAL")"
PLAN_CONTRACT_BUILD_SHA="$(sha256_file "$PLAN_CONTRACT_BUILD_LOCAL")"
PLAN_CONTRACT_AUDIT_SHA="$(sha256_file "$PLAN_CONTRACT_AUDIT_LOCAL")"
PIPELINE_SHA="$(sha256_file "$PIPELINE_LOCAL")"
UNIFORMITY_SHA="$(sha256_file "$UNIFORMITY_LOCAL")"
CHUNK_AUDIT_SHA="$(sha256_file "$CHUNK_AUDIT_LOCAL")"
FINAL_AUDIT_SHA="$(sha256_file "$FINAL_AUDIT_LOCAL")"
TRACEABILITY_SHA="$(sha256_file "$TRACEABILITY_LOCAL")"
TARGET_ENVELOPE_SHA="$(sha256_file "$TARGET_ENVELOPE_LOCAL")"

python3 - "$TARGET_ENVELOPE_LOCAL" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
envelope = data.get("physical_feature_target_envelope", {})
features = envelope.get("features", {})
expected_bounds = {
    "lp_nh_center": {"min": 0.5, "max": 3.0},
    "ls_nh_center": {"min": 0.5, "max": 3.0},
    "q_center": {"min": 5.0, "max": 25.0},
    "k_abs_center": {"min": 0.0, "max": 0.8},
}
errors = []
if data.get("status") != "ACTIVE":
    errors.append(f"status={data.get('status')!r}")
if envelope.get("feature_columns") != list(expected_bounds):
    errors.append(f"feature_columns={envelope.get('feature_columns')!r}")
for key, expected in expected_bounds.items():
    observed = features.get(key, {})
    for bound, expected_value in expected.items():
        if observed.get(bound) != expected_value:
            errors.append(f"{key}.{bound}={observed.get(bound)!r}")
if int(envelope.get("bins") or 0) != 4:
    errors.append(f"bins={envelope.get('bins')!r}")
if int(envelope.get("target_count_per_bin") or 0) != 391:
    errors.append(f"target_count_per_bin={envelope.get('target_count_per_bin')!r}")
if int(envelope.get("desired_total_count") or 0) != 100000:
    errors.append(f"desired_total_count={envelope.get('desired_total_count')!r}")
if int(envelope.get("next_count") or 0) != 8000:
    errors.append(f"next_count={envelope.get('next_count')!r}")
if errors:
    print("TARGET_ENVELOPE_LOCAL_CONTRACT_STATUS=FAIL " + "; ".join(errors))
    raise SystemExit(2)
print("TARGET_ENVELOPE_LOCAL_CONTRACT_STATUS=PASS")
print("target_count_per_bin=391")
print("desired_total_count=100000")
print("four_d_bin_count=256")
PY

SSH_ARGS=(-tt)
PROXY_SSH_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
PROXY_SCP_ARGS=(-o "ProxyJump=${USER_NAME}@${JUMP_HOST}")
if [ -n "$SSH_CONTROL_PATH" ]; then
  SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SSH_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
  PROXY_SCP_ARGS+=(-o ControlMaster=auto -o "ControlPersist=${SSH_PERSIST}" -o "ControlPath=${SSH_CONTROL_PATH}")
fi

read -r -d '' REMOTE_VERIFY <<'REMOTE' || true
set -euo pipefail

calc_sha() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  else
    python3 - "$path" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "rb") as fh:
    print(hashlib.sha256(fh.read()).hexdigest())
PY
  fi
}

verify_file() {
  local label="$1"
  local path="$2"
  local expected_sha="$3"
  local syntax_kind="$4"
  shift 4

  printf '\n-- checkpoint stack file %s --\n' "$label"
  printf 'remote_file_label=%s\n' "$label"
  printf 'remote_file_path=%s\n' "$path"
  printf 'local_sha256=%s\n' "$expected_sha"

  if [ ! -f "$path" ]; then
    echo "REMOTE_CHECKPOINT_STACK_FILE label=$label status=FAIL_MISSING"
    return 1
  fi

  local remote_sha
  remote_sha="$(calc_sha "$path")"
  printf 'remote_sha256=%s\n' "$remote_sha"
  if [ "$remote_sha" = "$expected_sha" ]; then
    echo "REMOTE_CHECKPOINT_STACK_HASH label=$label status=PASS"
  else
    echo "REMOTE_CHECKPOINT_STACK_HASH label=$label status=FAIL"
    return 1
  fi

  if [ "$syntax_kind" = "bash" ]; then
    bash -n "$path"
  elif [ "$syntax_kind" = "python" ]; then
    python3 -m py_compile "$path"
  elif [ "$syntax_kind" = "json" ]; then
    python3 -m json.tool "$path" >/dev/null
  else
    echo "unknown syntax kind: $syntax_kind" >&2
    return 1
  fi
  echo "REMOTE_CHECKPOINT_STACK_SYNTAX label=$label status=PASS"

  local token
  for token in "$@"; do
    if grep -Fq -- "$token" "$path"; then
      printf 'REMOTE_CHECKPOINT_STACK_TOKEN label=%s token=%s status=PASS\n' "$label" "$token"
    else
      printf 'REMOTE_CHECKPOINT_STACK_TOKEN label=%s token=%s status=FAIL\n' "$label" "$token"
      return 1
    fi
  done
  echo "REMOTE_CHECKPOINT_STACK_FILE label=$label status=PASS"
}

verify_target_envelope_contract() {
  local path="$1"
  python3 - "$path" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 - shell transcript should show parser detail.
    print(f"REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=FAIL parse={type(exc).__name__}: {exc}")
    raise SystemExit(1)
envelope = data.get("physical_feature_target_envelope", {})
features = envelope.get("features", {})
expected_bounds = {
    "lp_nh_center": {"min": 0.5, "max": 3.0},
    "ls_nh_center": {"min": 0.5, "max": 3.0},
    "q_center": {"min": 5.0, "max": 25.0},
    "k_abs_center": {"min": 0.0, "max": 0.8},
}
errors = []
if data.get("status") != "ACTIVE":
    errors.append(f"status={data.get('status')!r}")
if envelope.get("feature_columns") != list(expected_bounds):
    errors.append(f"feature_columns={envelope.get('feature_columns')!r}")
for key, expected in expected_bounds.items():
    observed = features.get(key, {})
    for bound, expected_value in expected.items():
        if observed.get(bound) != expected_value:
            errors.append(f"{key}.{bound}={observed.get(bound)!r}")
if int(envelope.get("bins") or 0) != 4:
    errors.append(f"bins={envelope.get('bins')!r}")
if int(envelope.get("target_count_per_bin") or 0) != 391:
    errors.append(f"target_count_per_bin={envelope.get('target_count_per_bin')!r}")
if int(envelope.get("desired_total_count") or 0) != 100000:
    errors.append(f"desired_total_count={envelope.get('desired_total_count')!r}")
if int(envelope.get("next_count") or 0) != 8000:
    errors.append(f"next_count={envelope.get('next_count')!r}")
if errors:
    print("REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=FAIL " + "; ".join(errors))
    raise SystemExit(1)
print("REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=PASS")
print("remote_target_count_per_bin=391")
print("remote_desired_total_count=100000")
print("remote_four_d_bin_count=256")
PY
}

printf 'remote_time='; date '+%Y-%m-%d %H:%M:%S %Z'
echo "purpose=verify_remote_checkpoint_stack_for_100k_physical_feature_distribution_evidence"

failure_count=0
verify_file runner "$RUNNER_REMOTE" "$RUNNER_SHA" bash \
  "strict_acceptance" \
  "candidate_queue_provenance_preflight" \
  "candidate_queue_provenance_status" \
  "audit_mars56_s4p_candidate_queue_provenance.py" \
  "checkpoint_proof" \
  "checkpoint_proof_reasons" \
  "physical_uniformity_gate" \
  "physical_uniformity_gate.require_four_d_gate" \
  "physical_uniformity_gate.min_four_d_occupied_fraction" \
  "min_four_d_normalized_entropy" \
  "max_four_d_nonzero_bin_imbalance" \
  "target_ranges" \
  "physical_uniformity_gate.target_ranges" \
  "uniformity.ranges" \
  "uniformity.ranges.{feature_name}" \
  "uniformity.ranges.{feature_name}.explicit" \
  'item.get("source") != "explicit"' \
  "math.isclose(actual_min, target_min" \
  "expected=({target_min},{target_max})" \
  "expected_min_four_d_occupied_frac" \
  "uniformity.four_dimensional_uniformity" \
  "uniformity.four_dimensional_uniformity=MISSING" \
  "uniformity.four_dimensional_uniformity.occupied_fraction" \
  "uniformity.four_dimensional_uniformity.normalized_entropy" \
  "uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio" \
  "four_dimensional_uniformity.occupied_fraction" \
  "required={expected_min_four_d_occupied_frac}" \
  "--require-plots" \
  "uniformity.k_sign_diagnostics" \
  "uniformity.k_sign_diagnostics.signed_k_count" \
  "uniformity.k_sign_diagnostics.uniformity_k_axis" \
  "model.test_row_count" \
  "model.metrics=MISSING" \
  "model.metrics.test_count" \
  "model.metrics.geometry_count" \
  "model.metrics.{metric_key}" \
  "max_normalized_mae" \
  "max_normalized_rmse" \
  "mean_normalized_mae" \
  "mean_normalized_rmse" \
  "ACCEPT_AND_PROCEED_TO_NEXT_100K_CHUNK" \
  "STOP_BEFORE_NEXT_100K_CHUNK" || failure_count=$((failure_count + 1))

verify_file queue_dataset "$QUEUE_DATASET_REMOTE" "$QUEUE_DATASET_SHA" python \
  "QUEUE_METADATA_COLUMNS" \
  "geometry_fingerprint_sha256" \
  "geometry_fingerprint_schema" \
  "geometry_fingerprint_quantization_um" \
  "queue__" || failure_count=$((failure_count + 1))

verify_file queue_preflight "$QUEUE_PREFLIGHT_REMOTE" "$QUEUE_PREFLIGHT_SHA" python \
  "CANDIDATE_QUEUE_PROVENANCE_STATUS" \
  "source_selection_csv" \
  "geometry_space_filling_no_physical_labels" \
  "selection_has_predicted_physical_features" \
  "selection_has_target_physical_bins" \
  "geometry_fingerprints_match_recomputed_canonical_geometry" \
  "canonical_geometries_unique" \
  "source_candidate_ids_nonempty_unique" \
  "candidate_summary_identity_audit_pass" \
  "candidate_rows_meet_expected_count" \
  "STOP_BEFORE_EMX_QUEUE_NOT_PROVEN_PHYSICAL_TARGETED" || failure_count=$((failure_count + 1))

verify_file adaptive_round "$ADAPTIVE_ROUND_REMOTE" "$ADAPTIVE_ROUND_SHA" bash \
  "plan_physical_feature_balanced_acquisition.py" \
  "build_physical_feature_surrogate_candidate_predictions.py" \
  "select_physical_feature_targeted_candidate_geometries.py" \
  "materialize_physical_feature_targeted_s4p_queue.py" \
  "audit_mars56_s4p_candidate_queue_provenance.py" \
  "PAIRWISE_TARGET_FRACTION" \
  "--pairwise-target-fraction" \
  "physical_feature_acquisition_bins.csv" \
  "lp_nh_center,ls_nh_center,q_center,k_abs_center" \
  "k_axis_policy" \
  "USE_QUEUE_FOR_NEXT_EMX_ACQUISITION" \
  "DO_NOT_RUN_EMX_FIX_TARGETING_FIRST" \
  "selected_inside_target_bin_count" \
  "queue_identity_evidence" \
  "duplicate_geometry_extra_row_count" \
  "require_unique_geometry" || failure_count=$((failure_count + 1))

verify_file merge_accepted_pool "$MERGE_ACCEPTED_POOL_REMOTE" "$MERGE_ACCEPTED_POOL_SHA" python \
  "Merge accepted physical-feature rows" \
  "USE_AS_NEXT_ACCEPTED_POOL_FOR_ADAPTIVE_PLANNING" \
  "Surrogate candidate predictions are intentionally not accepted as labels" \
  "k_abs_center=abs(k_center)" \
  "audit_physical_feature_uniformity.py" \
  "--k-mode" \
  "magnitude" \
  "accepted_pool_merge_summary.json" \
  "accepted_pool_merge_report.md" \
  "canonical_geometry_fingerprint_sha256" \
  "mars56_grounded_s4p_geometry_v1" \
  "geometry_identity_mismatch" \
  "fingerprint_quantization_um" \
  "--min-four-d-entropy-frac" \
  "--max-four-d-bin-imbalance" \
  "four_d_normalized_entropy" \
  "four_d_nonzero_bin_imbalance" \
  "uniformity_audit_passed_required_four_d_balance_gate" || failure_count=$((failure_count + 1))

verify_file plan_acquisition "$PLAN_ACQUISITION_REMOTE" "$PLAN_ACQUISITION_SHA" python \
  "SPARSE_FEATURE_BINS_PRIORITIZED" \
  "physical_feature_acquisition_targets.csv" \
  "physical_feature_marginal_histograms.png" \
  "deficit_first_then_low_count_topup" \
  "acquisition_allocation_policy" \
  "target_count_per_bin" \
  "k_abs_center" \
  "direct |K| column if present" || failure_count=$((failure_count + 1))

verify_file surrogate_predict "$SURROGATE_PREDICT_REMOTE" "$SURROGATE_PREDICT_SHA" python \
  "knn_idw_surrogate_for_candidate_priority_only" \
  "--pairwise-target-fraction" \
  "--pairwise-bins-csv" \
  "--pairwise-feature-pairs" \
  "local_pairwise_gap_perturbation" \
  "candidate_pairwise_deficit_fraction" \
  "k_abs_center" \
  "abs_k_center" \
  "q_center" \
  "candidate_physical_feature_predictions.csv" || failure_count=$((failure_count + 1))

verify_file model_checkpoint "$MODEL_CHECKPOINT_REMOTE" "$MODEL_CHECKPOINT_SHA" bash \
  "train_tandem_response_only_physical_cell_ood_at_100k" \
  "compare_tandem_geometry_anchor_ablation.py" \
  "audit_physical_cell_model_tail_error.py" \
  'TANDEM_MAX_PREDICTION_ROWS="$CHECKPOINT_COUNT"' \
  "physical_cell_tail_error_status" \
  "physical_cell_tail_error_equal_cell_rmse" \
  "physical_cell_tail_error_p95_rmse" \
  "physical_cell_tail_error_max_rmse" \
  "--min-four-d-entropy-frac 0.80" \
  "--max-four-d-bin-imbalance 4.0" \
  "four_d_normalized_entropy" \
  "four_d_nonzero_bin_imbalance" \
  'physical_cell_tail.get("overall_status")=="PASS"' \
  "audit_tandem_predicted_geometry_feasibility.py" \
  "tandem_predicted_geometry_feasibility_status" \
  "tandem_predicted_geometry_valid_fraction" \
  "--topology-feasibility-weight" \
  "label_free_topology_feasibility" \
  "topology_feasibility_columns_available" \
  "--geometry-anchor-weight 0" \
  "--response-warmup-fraction 0 --response-ramp-fraction 0.20" \
  "tandem_geometry_anchor_ablation_status" \
  "tandem_response_only_relative_improvement" \
  "tandem_response_only_improvement_ci_lower" \
  "tandem_response_only_improvement_ci_upper" \
  "tandem_response_only_cell_balanced_improvement_ci_lower" \
  "tandem_response_only_cell_balanced_improvement_ci_upper" \
  "tandem_anchor_ablation_bootstrap_status" \
  "paired_cluster_bootstrap.status" \
  "paired_cluster_bootstrap_row_and_cell_balanced_ci_lower_ge_material_improvement" \
  "int(index)!=1 or anchor_ablation.get" \
  "select_balanced_mse_bni_temperature.py" \
  "compare_balanced_mse_bni_ablation.py" \
  "train_balanced_mse_bni_same_budget_at_200k" \
  "balanced_mse_bni_ablation_decision_rule" \
  "balanced_mse_bni_p90_tail_improvement_ci_lower" \
  "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement" \
  "compare_global_vs_mondrian_conformal_calibration.py" \
  "compare_global_vs_mondrian_conformal_calibration_at_600k" \
  "physical_feature_mondrian_conformal_status" \
  "physical_feature_mondrian_supported_cell_fraction" \
  "physical_feature_mondrian_conformal_comparison" \
  "--min-cell-calibration-rows 30" \
  "--min-supported-cell-fraction 0.80" \
  "benchmark_cross_frequency_resolution.py" \
  "cross_frequency_resolution_status" \
  "checks.sparse_training_excludes_all_held_out_frequencies" \
  "cross_frequency_resolution_is_neural_operator" || failure_count=$((failure_count + 1))

verify_file conformal_audit "$CONFORMAL_AUDIT_REMOTE" "$CONFORMAL_AUDIT_SHA" python \
  "finite-sample marginal coverage under exchangeability" \
  "split_fingerprint_sha256" \
  "calibration_mask" \
  "all_empirical_coverages_pass" || failure_count=$((failure_count + 1))

verify_file mondrian_compare "$MONDRIAN_COMPARE_REMOTE" "$MONDRIAN_COMPARE_SHA" python \
  "fixed-cell Mondrian split-conformal intervals" \
  "global_split_fingerprint_matches" \
  "supported_evaluation_cell_fraction" \
  "supported_evaluation_row_fraction" \
  "RETAIN_GLOBAL_INTERVALS_AND_REPORT_GROUP_DIAGNOSTICS" \
  "ADOPT_MONDRIAN_FOR_GROUP_REPORTED_INTERVALS" \
  "exact pointwise conditional coverage" || failure_count=$((failure_count + 1))

verify_file frequency_resolution "$FREQUENCY_RESOLUTION_REMOTE" "$FREQUENCY_RESOLUTION_SHA" python \
  "same-real-S4P, same-physical-cell-split, equal-update cross-resolution" \
  "sparse_training_excludes_all_held_out_frequencies" \
  "equal_optimizer_updates" \
  "shared_output_normalization_source" \
  "raw_input_reciprocity_threshold_pass" \
  "raw_input_passivity_threshold_pass" \
  "raw complex S4P before reciprocal symmetrization" \
  "is_neural_operator" \
  "not proof of a neural operator" \
  "KEEP_EMX_AT_0P5_GHZ" || failure_count=$((failure_count + 1))

verify_file tandem_train "$TANDEM_TRAIN_REMOTE" "$TANDEM_TRAIN_SHA" python \
  "feature_balanced_response_consistency_only" \
  "geometry_label_used_in_inverse_objective" \
  "response_only_fixed" \
  "_topology_feasibility_penalty_and_gradient" \
  "topology_feasibility_is_label_free" \
  "terminal-span/feed-support checks" \
  "--response-loss-family" \
  "--balanced-mse-temperature" \
  "joint_4d_equal_volume_physical_cell_numerical_integration" \
  "validation_or_test_rows_used_in_prior" \
  "training_csv_sha256" \
  "source_geometry_identity_sha256" \
  "ordered_inverse_geometry_float64_v1" \
  "--response-warmup-fraction must be 0 when --geometry-anchor-weight is 0" \
  "test_response_range_normalized_rmse" || failure_count=$((failure_count + 1))

verify_file tandem_anchor_compare "$TANDEM_ANCHOR_COMPARE_REMOTE" "$TANDEM_ANCHOR_COMPARE_SHA" python \
  "same_split_fingerprint" \
  "same_split_cell_partition" \
  "response_only_has_no_zero_gradient_warmup" \
  "paired_physical_cell_cluster_bootstrap_v1" \
  "prediction CSVs do not cover the complete shared test set" \
  "relative_improvement_ci_lower" \
  "cell_balanced_relative_improvement_ci_lower" \
  "p90_tail_relative_improvement_ci_lower" \
  "RETAIN_ANCHORED_BASELINE_UNCERTAIN_RESPONSE_ONLY_GAIN" \
  "REVIEW_RESPONSE_ONLY_FOR_REAL_EMX_CLOSURE" \
  "real EMX closed-loop verification" || failure_count=$((failure_count + 1))

verify_file bni_temperature_select "$BNI_TEMPERATURE_SELECT_REMOTE" "$BNI_TEMPERATURE_SELECT_SHA" python \
  "tau = 2 * validation_feature_balanced_response_normalized_rmse^2" \
  "test_metrics_used" \
  "test_predictions_used" \
  "hyperparameter_sweep_performed" \
  "RUN_SINGLE_BNI_ARM_WITH_RECORDED_TEMPERATURE" || failure_count=$((failure_count + 1))

verify_file bni_ablation_compare "$BNI_ABLATION_COMPARE_REMOTE" "$BNI_ABLATION_COMPARE_SHA" python \
  "same_training_csv_sha256" \
  "same_training_budget" \
  "temperature_selection_used_no_test_evidence" \
  "complete_paired_test_bootstrap" \
  "p90_tail_relative_improvement_ci_lower" \
  "REVIEW_BNI_FOR_REAL_EMX_CLOSURE" \
  "RETAIN_MSE_NO_CONFIDENT_MATERIAL_BNI_GAIN" \
  "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement" || failure_count=$((failure_count + 1))

verify_file learning_curve_audit "$LEARNING_CURVE_AUDIT_REMOTE" "$LEARNING_CURVE_AUDIT_SHA" python \
  "fixed_common_test_panel_geometry_ids.csv" \
  "source_geometry_identity_sha256" \
  "ordered_inverse_geometry_float64_v1" \
  "minimum-common-test-rows" \
  "minimum-first-panel-retention" \
  "targets_stable_across_checkpoints" \
  "common_panel_response_range_normalized_rmse" \
  "Expanding-cell OOD metrics remain diagnostic" || failure_count=$((failure_count + 1))

verify_file physical_cell_tail_audit "$PHYSICAL_CELL_TAIL_AUDIT_REMOTE" "$PHYSICAL_CELL_TAIL_AUDIT_SHA" python \
  "physical_cell_model_tail_error_v1" \
  "prediction physical cells differ from split audit" \
  "row_weighted_response_range_normalized_rmse" \
  "equal_cell_response_range_normalized_rmse" \
  "cell_response_range_normalized_rmse_p95" \
  "cell_response_range_normalized_rmse_max" \
  "worst_to_median_cell_rmse_ratio" \
  "It does not declare an accuracy PASS threshold" || failure_count=$((failure_count + 1))

verify_file tandem_feasibility_audit "$TANDEM_FEASIBILITY_AUDIT_REMOTE" "$TANDEM_FEASIBILITY_AUDIT_SHA" python \
  "TransformerSpec.validate()" \
  "audit_tsmc65_top_metal_geometry" \
  "all_predictions_satisfy_coupled_topology" \
  "all_predictions_satisfy_tsmc65_top_metal_gate" \
  "not GDS proof" || failure_count=$((failure_count + 1))

verify_file target_select "$TARGET_SELECT_REMOTE" "$TARGET_SELECT_SHA" python \
  "physical_feature_acquisition_targets.csv" \
  "physical_feature_targeted_candidate_selection.csv" \
  "inside_target_bin" \
  "reachable_targets_only" \
  "USE_SELECTED_CANDIDATES_FOR_NEXT_EMX_BATCH" || failure_count=$((failure_count + 1))

verify_file queue_materialize "$QUEUE_MATERIALIZE_REMOTE" "$QUEUE_MATERIALIZE_SHA" python \
  "source_selection_csv" \
  "mars56_grounded_s4p_candidate_queue.csv" \
  "sync_primary_secondary_width_to_line_width" \
  "CANONICAL_GEOMETRY_FIELDS" \
  "--require-unique-geometry" \
  "--require-unique-source-id" \
  "geometry_fingerprint_sha256" \
  "duplicate canonical geometry" \
  "candidate__geom__line_width_um preferred" \
  "Converted from physical-feature targeted surrogate selection" || failure_count=$((failure_count + 1))

verify_file plan_contract_build "$PLAN_CONTRACT_BUILD_REMOTE" "$PLAN_CONTRACT_BUILD_SHA" python \
  "mars56_s4p_1m_physical_feature_uniformity_contract" \
  "expected_chunks" \
  "expected_per_chunk" \
  "chunk_{index:03d}_100k_after_chunk08_pass" \
  "cumulative_{index * 100:04d}k_after_chunk08_pass" \
  "min_four_d_normalized_entropy" \
  "max_four_d_nonzero_bin_imbalance" \
  "PRODUCTION_PLAN_CONTRACT_STATUS=CONTRACT_WRITTEN_NOT_EVIDENCE" || failure_count=$((failure_count + 1))

verify_file plan_contract_audit "$PLAN_CONTRACT_AUDIT_REMOTE" "$PLAN_CONTRACT_AUDIT_SHA" python \
  "PRODUCTION_PLAN_CONTRACT_AUDIT_STATUS" \
  "checkpoint_contract_min_four_d_normalized_entropy_not_weakened" \
  "checkpoint_contract_max_four_d_nonzero_bin_imbalance_not_weakened" \
  "checkpoint_contract_requires_plots" \
  "formal_pass_count_meets_contract" \
  "cumulative_pass_count_meets_contract" \
  "total_nonempty_s4p_meets_contract" \
  "missing_formal_chunk_in_evidence" \
  "missing_cumulative_checkpoint_in_evidence" \
  "ONE_MILLION_PLAN_CONTRACT_EVIDENCE_PASS" || failure_count=$((failure_count + 1))

verify_file pipeline "$PIPELINE_REMOTE" "$PIPELINE_SHA" bash \
  "--require-four-d-gate" \
  "--require-plots" \
  "audit_physical_checkpoint_traceability.py" \
  "physical_checkpoint_traceability" \
  "physical_checkpoint_traceability_summary.json" \
  "physical_feature_uniformity_manifest.json" \
  "physical_uniformity_gate" \
  "require_four_d_gate" \
  "min_four_d_occupied_fraction" \
  "min_four_d_occupied_frac" \
  "min_four_d_normalized_entropy" \
  "max_four_d_nonzero_bin_imbalance" \
  "target_ranges" \
  "physical_uniformity_gate.target_ranges" \
  "uniformity.ranges" \
  "uniformity.ranges.{feature_name}" \
  "uniformity.ranges.{feature_name}.explicit" \
  'item.get("source") != "explicit"' \
  "math.isclose(actual_min, target_min" \
  "expected=({target_min},{target_max})" \
  "uniformity.four_dimensional_uniformity" \
  "uniformity.four_dimensional_uniformity=MISSING" \
  "uniformity.four_dimensional_uniformity.occupied_fraction" \
  "uniformity.four_dimensional_uniformity.normalized_entropy" \
  "uniformity.four_dimensional_uniformity.max_to_min_nonzero_ratio" \
  "four_dimensional_uniformity.occupied_fraction" \
  "required={min_four_d_occupied_frac}" \
  "uniformity_manifest" \
  "traceability" \
  "traceability.details_missing" \
  "traceability.{key}=MISSING" \
  "proof_reasons" \
  "model.test_row_count" \
  "model.metrics=MISSING" \
  "model.metrics.test_count" \
  "model.metrics.geometry_count" \
  "model.metrics.{metric_key}" \
  "max_normalized_mae" \
  "max_normalized_rmse" \
  "mean_normalized_mae" \
  "mean_normalized_rmse" \
  "uniformity_manifest.visual_artifact_count" \
  "uniformity_manifest.require_plots" \
  "visual_artifact_count" || failure_count=$((failure_count + 1))

verify_file uniformity "$UNIFORMITY_REMOTE" "$UNIFORMITY_SHA" python \
  "--min-four-d-entropy-frac" \
  "--max-four-d-bin-imbalance" \
  "min_four_d_normalized_entropy" \
  "max_four_d_nonzero_bin_imbalance" \
  "Lp/Ls/Q/K 4D normalized entropy" \
  "Lp/Ls/Q/K 4D nonzero-bin imbalance" \
  "--require-plots" \
  "physical_feature_uniformity_manifest.json" \
  "physical_feature_marginal_histograms.png" \
  "physical_feature_pair_scatter.png" \
  "physical_feature_pair_occupancy_heatmaps.png" \
  "visual_artifact_count" \
  "k_sign_diagnostics" \
  "uniformity_k_axis" \
  "signed_k_count" \
  "negative_k_count" \
  "Uniformity is evaluated on |K|" || failure_count=$((failure_count + 1))

verify_file chunk_audit "$CHUNK_AUDIT_REMOTE" "$CHUNK_AUDIT_SHA" python \
  "physical_feature_uniformity_manifest.json" \
  "uniformity_manifest" \
  "physical feature uniformity artifact manifest PASS" \
  "uniformity visual artifact count" \
  "candidate requires unique canonical geometry" \
  "candidate unique canonical geometry count" \
  "candidate unique source ID count" \
  "mars56_grounded_s4p_geometry_v1" || failure_count=$((failure_count + 1))

verify_file final_audit "$FINAL_AUDIT_REMOTE" "$FINAL_AUDIT_SHA" python \
  "four_d_entropy" \
  "four_d_nonzero_bin_imbalance" \
  "four_d_entropy_threshold_not_weakened" \
  "four_d_imbalance_threshold_not_weakened" \
  "pool_summary_geometry_identity_contract" \
  "all_rows_have_saved_canonical_geometry_identity" \
  "all_saved_geometry_fingerprints_match_recomputed" \
  "shared_width_aliases_match_line_width" \
  "canonical_geometry_fingerprint_sha256" \
  "mars56_grounded_s4p_geometry_v1" \
  "unique_geometry_fingerprint_count" \
  "manifest_physical_cell_tail_error" \
  'physical_cell_tail_error": item.get("manifest_physical_cell_tail_error") == "PASS"' \
  "manifest_balanced_mse_bni_status" \
  "manifest_balanced_mse_bni_decision_rule" \
  "manifest_balanced_mse_bni_p90_tail_ci_lower" \
  "balanced_mse_bni_artifact_sha256_matches" \
  "balanced_mse_bni_manifest_artifact_ci_match" \
  "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement" \
  "fixed_common_test_panel_contract_pass" \
  "artifact_fingerprint_matches" \
  "learning_curve_minimum_common_rows_not_weakened" \
  "DO_NOT_CLAIM_ONE_MILLION_COMPLETE" || failure_count=$((failure_count + 1))

verify_file traceability "$TRACEABILITY_REMOTE" "$TRACEABILITY_SHA" python \
  "physical_checkpoint_traceability_summary.json" \
  "stable_touchstone_index_manifest.csv" \
  "source_path_exists" \
  "indexed_path_exists" \
  "stable_manifest_rows" \
  "stable_unique_evaluations" \
  "response_feature_rows" \
  "response_unique_evaluations" \
  "response_dataset_rows" \
  "response_dataset_unique_evaluations" \
  "enriched_rows" \
  "enriched_unique_evaluations" \
  "training_rows" \
  "training_unique_evaluations" \
  "training_source_sha_present" \
  "stable_to_response_evaluations" \
  "response_to_enriched_evaluations" \
  "enriched_to_training_evaluations" || failure_count=$((failure_count + 1))

verify_file target_envelope "$TARGET_ENVELOPE_REMOTE" "$TARGET_ENVELOPE_SHA" json \
  "physical_feature_target_envelope" \
  "mars56_s4p_1m_official_physical_feature_envelope_20260708" \
  "desired_total_count" \
  "100000" \
  "target_count_per_bin" \
  "391" \
  "lp_nh_center" \
  "ls_nh_center" \
  "q_center" \
  "k_abs_center" \
  "0.5" \
  "3.0" \
  "25.0" \
  "0.8" \
  "ceil(100000 / 4^4) = 391" \
  "Adaptive acquisition must target sparse Lp/Ls/Q/|K| bins inside these fixed physical ranges" || failure_count=$((failure_count + 1))

verify_target_envelope_contract "$TARGET_ENVELOPE_REMOTE" || failure_count=$((failure_count + 1))

printf 'remote_checkpoint_stack_failure_count=%s\n' "$failure_count"
if [ "$failure_count" -eq 0 ]; then
  echo "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=PASS"
  exit 0
fi
echo "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=FAIL"
if [ "${ALLOW_MISMATCH:-0}" = "1" ]; then
  echo "REMOTE_CHECKPOINT_STACK_VERIFY_DECISION=ALLOW_MISMATCH_CONTINUE"
  exit 0
fi
exit 1
REMOTE

run_remote_verify() {
  local remote_verify_script
  read -r -d '' remote_verify_script <<SCRIPT || true
RUNNER_REMOTE='${RUNNER_REMOTE}'
QUEUE_DATASET_REMOTE='${QUEUE_DATASET_REMOTE}'
QUEUE_PREFLIGHT_REMOTE='${QUEUE_PREFLIGHT_REMOTE}'
ADAPTIVE_ROUND_REMOTE='${ADAPTIVE_ROUND_REMOTE}'
MERGE_ACCEPTED_POOL_REMOTE='${MERGE_ACCEPTED_POOL_REMOTE}'
PLAN_ACQUISITION_REMOTE='${PLAN_ACQUISITION_REMOTE}'
SURROGATE_PREDICT_REMOTE='${SURROGATE_PREDICT_REMOTE}'
MODEL_CHECKPOINT_REMOTE='${MODEL_CHECKPOINT_REMOTE}'
CONFORMAL_AUDIT_REMOTE='${CONFORMAL_AUDIT_REMOTE}'
MONDRIAN_COMPARE_REMOTE='${MONDRIAN_COMPARE_REMOTE}'
FREQUENCY_RESOLUTION_REMOTE='${FREQUENCY_RESOLUTION_REMOTE}'
TANDEM_TRAIN_REMOTE='${TANDEM_TRAIN_REMOTE}'
TANDEM_ANCHOR_COMPARE_REMOTE='${TANDEM_ANCHOR_COMPARE_REMOTE}'
BNI_TEMPERATURE_SELECT_REMOTE='${BNI_TEMPERATURE_SELECT_REMOTE}'
BNI_ABLATION_COMPARE_REMOTE='${BNI_ABLATION_COMPARE_REMOTE}'
LEARNING_CURVE_AUDIT_REMOTE='${LEARNING_CURVE_AUDIT_REMOTE}'
PHYSICAL_CELL_TAIL_AUDIT_REMOTE='${PHYSICAL_CELL_TAIL_AUDIT_REMOTE}'
TANDEM_FEASIBILITY_AUDIT_REMOTE='${TANDEM_FEASIBILITY_AUDIT_REMOTE}'
TARGET_SELECT_REMOTE='${TARGET_SELECT_REMOTE}'
QUEUE_MATERIALIZE_REMOTE='${QUEUE_MATERIALIZE_REMOTE}'
PLAN_CONTRACT_BUILD_REMOTE='${PLAN_CONTRACT_BUILD_REMOTE}'
PLAN_CONTRACT_AUDIT_REMOTE='${PLAN_CONTRACT_AUDIT_REMOTE}'
PIPELINE_REMOTE='${PIPELINE_REMOTE}'
UNIFORMITY_REMOTE='${UNIFORMITY_REMOTE}'
CHUNK_AUDIT_REMOTE='${CHUNK_AUDIT_REMOTE}'
FINAL_AUDIT_REMOTE='${FINAL_AUDIT_REMOTE}'
TRACEABILITY_REMOTE='${TRACEABILITY_REMOTE}'
TARGET_ENVELOPE_REMOTE='${TARGET_ENVELOPE_REMOTE}'
RUNNER_SHA='${RUNNER_SHA}'
QUEUE_DATASET_SHA='${QUEUE_DATASET_SHA}'
QUEUE_PREFLIGHT_SHA='${QUEUE_PREFLIGHT_SHA}'
ADAPTIVE_ROUND_SHA='${ADAPTIVE_ROUND_SHA}'
MERGE_ACCEPTED_POOL_SHA='${MERGE_ACCEPTED_POOL_SHA}'
PLAN_ACQUISITION_SHA='${PLAN_ACQUISITION_SHA}'
SURROGATE_PREDICT_SHA='${SURROGATE_PREDICT_SHA}'
MODEL_CHECKPOINT_SHA='${MODEL_CHECKPOINT_SHA}'
CONFORMAL_AUDIT_SHA='${CONFORMAL_AUDIT_SHA}'
MONDRIAN_COMPARE_SHA='${MONDRIAN_COMPARE_SHA}'
FREQUENCY_RESOLUTION_SHA='${FREQUENCY_RESOLUTION_SHA}'
TANDEM_TRAIN_SHA='${TANDEM_TRAIN_SHA}'
TANDEM_ANCHOR_COMPARE_SHA='${TANDEM_ANCHOR_COMPARE_SHA}'
BNI_TEMPERATURE_SELECT_SHA='${BNI_TEMPERATURE_SELECT_SHA}'
BNI_ABLATION_COMPARE_SHA='${BNI_ABLATION_COMPARE_SHA}'
LEARNING_CURVE_AUDIT_SHA='${LEARNING_CURVE_AUDIT_SHA}'
PHYSICAL_CELL_TAIL_AUDIT_SHA='${PHYSICAL_CELL_TAIL_AUDIT_SHA}'
TANDEM_FEASIBILITY_AUDIT_SHA='${TANDEM_FEASIBILITY_AUDIT_SHA}'
TARGET_SELECT_SHA='${TARGET_SELECT_SHA}'
QUEUE_MATERIALIZE_SHA='${QUEUE_MATERIALIZE_SHA}'
PLAN_CONTRACT_BUILD_SHA='${PLAN_CONTRACT_BUILD_SHA}'
PLAN_CONTRACT_AUDIT_SHA='${PLAN_CONTRACT_AUDIT_SHA}'
PIPELINE_SHA='${PIPELINE_SHA}'
UNIFORMITY_SHA='${UNIFORMITY_SHA}'
CHUNK_AUDIT_SHA='${CHUNK_AUDIT_SHA}'
FINAL_AUDIT_SHA='${FINAL_AUDIT_SHA}'
TRACEABILITY_SHA='${TRACEABILITY_SHA}'
TARGET_ENVELOPE_SHA='${TARGET_ENVELOPE_SHA}'
ALLOW_MISMATCH='${ALLOW_MISMATCH}'
export RUNNER_REMOTE QUEUE_DATASET_REMOTE QUEUE_PREFLIGHT_REMOTE ADAPTIVE_ROUND_REMOTE MERGE_ACCEPTED_POOL_REMOTE PLAN_ACQUISITION_REMOTE SURROGATE_PREDICT_REMOTE MODEL_CHECKPOINT_REMOTE CONFORMAL_AUDIT_REMOTE MONDRIAN_COMPARE_REMOTE FREQUENCY_RESOLUTION_REMOTE TANDEM_TRAIN_REMOTE TANDEM_ANCHOR_COMPARE_REMOTE BNI_TEMPERATURE_SELECT_REMOTE BNI_ABLATION_COMPARE_REMOTE LEARNING_CURVE_AUDIT_REMOTE PHYSICAL_CELL_TAIL_AUDIT_REMOTE TANDEM_FEASIBILITY_AUDIT_REMOTE TARGET_SELECT_REMOTE QUEUE_MATERIALIZE_REMOTE PLAN_CONTRACT_BUILD_REMOTE PLAN_CONTRACT_AUDIT_REMOTE PIPELINE_REMOTE UNIFORMITY_REMOTE CHUNK_AUDIT_REMOTE FINAL_AUDIT_REMOTE TRACEABILITY_REMOTE TARGET_ENVELOPE_REMOTE
export RUNNER_SHA QUEUE_DATASET_SHA QUEUE_PREFLIGHT_SHA ADAPTIVE_ROUND_SHA MERGE_ACCEPTED_POOL_SHA PLAN_ACQUISITION_SHA SURROGATE_PREDICT_SHA MODEL_CHECKPOINT_SHA CONFORMAL_AUDIT_SHA MONDRIAN_COMPARE_SHA FREQUENCY_RESOLUTION_SHA TANDEM_TRAIN_SHA TANDEM_ANCHOR_COMPARE_SHA BNI_TEMPERATURE_SELECT_SHA BNI_ABLATION_COMPARE_SHA LEARNING_CURVE_AUDIT_SHA PHYSICAL_CELL_TAIL_AUDIT_SHA TANDEM_FEASIBILITY_AUDIT_SHA TARGET_SELECT_SHA QUEUE_MATERIALIZE_SHA PLAN_CONTRACT_BUILD_SHA PLAN_CONTRACT_AUDIT_SHA PIPELINE_SHA UNIFORMITY_SHA CHUNK_AUDIT_SHA FINAL_AUDIT_SHA TRACEABILITY_SHA TARGET_ENVELOPE_SHA ALLOW_MISMATCH
${REMOTE_VERIFY}
SCRIPT
  ssh "${PROXY_SSH_ARGS[@]}" "${USER_NAME}@${MARS_HOST}" bash -s <<<"$remote_verify_script"
}

sync_one() {
  local local_path="$1"
  local remote_path="$2"
  local stamp="$3"
  local remote_tmp="${remote_path}.incoming.${stamp}.$$"
  local remote_backup="${remote_path}.bak.${stamp}"
  local remote_dir
  remote_dir="$(dirname "$remote_path")"
  local mars_target="${USER_NAME}@${MARS_HOST}"
  local prep_script
  read -r -d '' prep_script <<SCRIPT || true
set -euo pipefail
mkdir -p '${remote_dir}'
if [ -f '${remote_path}' ]; then
  cp '${remote_path}' '${remote_backup}'
  echo remote_backup='${remote_backup}'
else
  echo remote_backup=none_existing_file_missing
fi
SCRIPT
  ssh "${PROXY_SSH_ARGS[@]}" "$mars_target" bash -s <<<"$prep_script"
  scp "${PROXY_SCP_ARGS[@]}" "$local_path" "${mars_target}:${remote_tmp}"
  local install_script
  read -r -d '' install_script <<SCRIPT || true
set -euo pipefail
case '${remote_tmp}' in
  *.py) python3 -m py_compile '${remote_tmp}' ;;
  *.json) python3 -m json.tool '${remote_tmp}' >/dev/null ;;
  *) bash -n '${remote_tmp}' ;;
esac
chmod +x '${remote_tmp}'
mv '${remote_tmp}' '${remote_path}'
echo REMOTE_CHECKPOINT_STACK_SYNC_INSTALL path='${remote_path}' status=PASS
SCRIPT
  ssh "${PROXY_SSH_ARGS[@]}" "$mars_target" bash -s <<<"$install_script"
}

echo "MARS56 remote checkpoint stack verify/sync"
echo "sync_remote_checkpoint_stack=$SYNC_REMOTE_CHECKPOINT_STACK"
echo "allow_mismatch=$ALLOW_MISMATCH"
echo "local_contract_only=$LOCAL_CONTRACT_ONLY"
echo "remote_project=$REMOTE_PROJECT"
echo "runner_sha256=$RUNNER_SHA"
echo "queue_dataset_sha256=$QUEUE_DATASET_SHA"
echo "queue_preflight_sha256=$QUEUE_PREFLIGHT_SHA"
echo "adaptive_round_sha256=$ADAPTIVE_ROUND_SHA"
echo "merge_accepted_pool_sha256=$MERGE_ACCEPTED_POOL_SHA"
echo "plan_acquisition_sha256=$PLAN_ACQUISITION_SHA"
echo "surrogate_predict_sha256=$SURROGATE_PREDICT_SHA"
echo "model_checkpoint_sha256=$MODEL_CHECKPOINT_SHA"
echo "conformal_audit_sha256=$CONFORMAL_AUDIT_SHA"
echo "mondrian_compare_sha256=$MONDRIAN_COMPARE_SHA"
echo "frequency_resolution_sha256=$FREQUENCY_RESOLUTION_SHA"
echo "tandem_train_sha256=$TANDEM_TRAIN_SHA"
echo "tandem_anchor_compare_sha256=$TANDEM_ANCHOR_COMPARE_SHA"
echo "bni_temperature_select_sha256=$BNI_TEMPERATURE_SELECT_SHA"
echo "bni_ablation_compare_sha256=$BNI_ABLATION_COMPARE_SHA"
echo "learning_curve_audit_sha256=$LEARNING_CURVE_AUDIT_SHA"
echo "physical_cell_tail_audit_sha256=$PHYSICAL_CELL_TAIL_AUDIT_SHA"
echo "tandem_feasibility_audit_sha256=$TANDEM_FEASIBILITY_AUDIT_SHA"
echo "target_select_sha256=$TARGET_SELECT_SHA"
echo "queue_materialize_sha256=$QUEUE_MATERIALIZE_SHA"
echo "plan_contract_build_sha256=$PLAN_CONTRACT_BUILD_SHA"
echo "plan_contract_audit_sha256=$PLAN_CONTRACT_AUDIT_SHA"
echo "pipeline_sha256=$PIPELINE_SHA"
echo "uniformity_sha256=$UNIFORMITY_SHA"
echo "chunk_audit_sha256=$CHUNK_AUDIT_SHA"
echo "final_audit_sha256=$FINAL_AUDIT_SHA"
echo "traceability_sha256=$TRACEABILITY_SHA"
echo "target_envelope_sha256=$TARGET_ENVELOPE_SHA"

if [ "$LOCAL_CONTRACT_ONLY" = "1" ]; then
  echo "REMOTE_CHECKPOINT_STACK_VERIFY_STATUS=LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE"
  echo "REMOTE_CHECKPOINT_STACK_VERIFY_DECISION=NO_SSH_NO_REMOTE_CLAIM"
  exit 0
fi

if [ "$SYNC_REMOTE_CHECKPOINT_STACK" = "0" ]; then
  echo "Opening ${USER_NAME}@${JUMP_HOST}; approve Duo when prompted."
  run_remote_verify
  exit $?
fi

stamp="$(date '+%Y%m%d_%H%M%S')"
echo "SYNC_REMOTE_CHECKPOINT_STACK=1: backing up and replacing remote checkpoint stack files."
sync_one "$RUNNER_LOCAL" "$RUNNER_REMOTE" "$stamp"
sync_one "$QUEUE_DATASET_LOCAL" "$QUEUE_DATASET_REMOTE" "$stamp"
sync_one "$QUEUE_PREFLIGHT_LOCAL" "$QUEUE_PREFLIGHT_REMOTE" "$stamp"
sync_one "$ADAPTIVE_ROUND_LOCAL" "$ADAPTIVE_ROUND_REMOTE" "$stamp"
sync_one "$MERGE_ACCEPTED_POOL_LOCAL" "$MERGE_ACCEPTED_POOL_REMOTE" "$stamp"
sync_one "$PLAN_ACQUISITION_LOCAL" "$PLAN_ACQUISITION_REMOTE" "$stamp"
sync_one "$SURROGATE_PREDICT_LOCAL" "$SURROGATE_PREDICT_REMOTE" "$stamp"
sync_one "$MODEL_CHECKPOINT_LOCAL" "$MODEL_CHECKPOINT_REMOTE" "$stamp"
sync_one "$CONFORMAL_AUDIT_LOCAL" "$CONFORMAL_AUDIT_REMOTE" "$stamp"
sync_one "$MONDRIAN_COMPARE_LOCAL" "$MONDRIAN_COMPARE_REMOTE" "$stamp"
sync_one "$FREQUENCY_RESOLUTION_LOCAL" "$FREQUENCY_RESOLUTION_REMOTE" "$stamp"
sync_one "$TANDEM_TRAIN_LOCAL" "$TANDEM_TRAIN_REMOTE" "$stamp"
sync_one "$TANDEM_ANCHOR_COMPARE_LOCAL" "$TANDEM_ANCHOR_COMPARE_REMOTE" "$stamp"
sync_one "$BNI_TEMPERATURE_SELECT_LOCAL" "$BNI_TEMPERATURE_SELECT_REMOTE" "$stamp"
sync_one "$BNI_ABLATION_COMPARE_LOCAL" "$BNI_ABLATION_COMPARE_REMOTE" "$stamp"
sync_one "$LEARNING_CURVE_AUDIT_LOCAL" "$LEARNING_CURVE_AUDIT_REMOTE" "$stamp"
sync_one "$PHYSICAL_CELL_TAIL_AUDIT_LOCAL" "$PHYSICAL_CELL_TAIL_AUDIT_REMOTE" "$stamp"
sync_one "$TANDEM_FEASIBILITY_AUDIT_LOCAL" "$TANDEM_FEASIBILITY_AUDIT_REMOTE" "$stamp"
sync_one "$TARGET_SELECT_LOCAL" "$TARGET_SELECT_REMOTE" "$stamp"
sync_one "$QUEUE_MATERIALIZE_LOCAL" "$QUEUE_MATERIALIZE_REMOTE" "$stamp"
sync_one "$PLAN_CONTRACT_BUILD_LOCAL" "$PLAN_CONTRACT_BUILD_REMOTE" "$stamp"
sync_one "$PLAN_CONTRACT_AUDIT_LOCAL" "$PLAN_CONTRACT_AUDIT_REMOTE" "$stamp"
sync_one "$PIPELINE_LOCAL" "$PIPELINE_REMOTE" "$stamp"
sync_one "$UNIFORMITY_LOCAL" "$UNIFORMITY_REMOTE" "$stamp"
sync_one "$CHUNK_AUDIT_LOCAL" "$CHUNK_AUDIT_REMOTE" "$stamp"
sync_one "$FINAL_AUDIT_LOCAL" "$FINAL_AUDIT_REMOTE" "$stamp"
sync_one "$TRACEABILITY_LOCAL" "$TRACEABILITY_REMOTE" "$stamp"
sync_one "$TARGET_ENVELOPE_LOCAL" "$TARGET_ENVELOPE_REMOTE" "$stamp"
run_remote_verify
