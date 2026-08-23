#!/usr/bin/env bash
# Activate only the model-checkpoint runtime after an accepted-data audit
# boundary. Active EMX runners and queue-generation code are intentionally
# excluded so an in-flight production chunk cannot mix solver code versions.

set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
STAGE="${STAGE:-$BASE/status/model_checkpoint_patch_staging_20260712_0057}"
RUNTIME="$STAGE/complete_runtime"
WATCHER="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher"
BOUNDARY_MARKER="${BOUNDARY_MARKER:-$WATCHER/milestone_73590/milestone.complete}"
EXPECTED_ARCHIVE_SHA="e37a7acbd10dd3a2ee63becaf7bb568a2b5798606b299489c41eb9058c8c6540"
EXPECTED_ENVELOPE_SHA="5ea344150e01700a36b06b79f17ac413caadf9476f5d956a48ae7d75bcf77ff2"
EXPECTED_RUNNER_SHA="33527752f103e4485e9dd668385f2e49ebb9eee42343032e8fc793968a190441"

if [[ "$(hostname -f 2>/dev/null || hostname)" != mars.example.edu ]]; then
  echo "ERROR: this activation must run on mars.example.edu." >&2
  exit 2
fi
if [[ ! -f "$BOUNDARY_MARKER" ]]; then
  echo "ERROR: audit boundary is not complete: $BOUNDARY_MARKER" >&2
  exit 2
fi
if pgrep -af "$WATCHER/milestone_73590" >/dev/null 2>&1; then
  echo "ERROR: milestone_73590 audit process is still active." >&2
  exit 2
fi
if [[ ! -f "$STAGE/COMPLETE_RUNTIME_STAGED" ]]; then
  echo "ERROR: complete runtime staging evidence is missing." >&2
  exit 2
fi
if ! grep -Fqx "complete_runtime_sha256=$EXPECTED_ARCHIVE_SHA" "$STAGE/COMPLETE_RUNTIME_STAGED"; then
  echo "ERROR: staged runtime marker SHA does not match." >&2
  exit 2
fi
if [[ "$(sha256sum "$STAGE/model_checkpoint_complete_runtime_20260712.tar.gz" | awk '{print $1}')" != "$EXPECTED_ARCHIVE_SHA" ]]; then
  echo "ERROR: staged runtime archive SHA does not match." >&2
  exit 2
fi
if [[ "$(sha256sum "$PROJECT/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json" | awk '{print $1}')" != "$EXPECTED_ENVELOPE_SHA" ]]; then
  echo "ERROR: active physical-feature envelope changed; refusing activation." >&2
  exit 2
fi
if [[ "$(sha256sum "$RUNTIME/scripts/run_accepted_physical_feature_model_checkpoint.sh" | awk '{print $1}')" != "$EXPECTED_RUNNER_SHA" ]]; then
  echo "ERROR: staged model runner SHA does not match." >&2
  exit 2
fi

FILES=(
  run_accepted_physical_feature_model_checkpoint.sh
  audit_broadband_sparameter_surrogate_readiness.py
  audit_geometry_response_effective_dimension.py
  audit_inverse_one_to_many_geometry_multiplicity.py
  audit_low_frequency_coupled_rl_consistency.py
  audit_physical_cell_model_tail_error.py
  audit_physical_feature_boundary_ood_stress.py
  audit_physical_feature_conformal_calibration.py
  audit_physical_feature_extraction_frequency_stability.py
  audit_physical_feature_input_ablation_readiness.py
  audit_physical_feature_uniformity.py
  audit_tandem_predicted_geometry_feasibility.py
  audit_transformer_ground_ring_spacing.py
  benchmark_cross_frequency_resolution.py
  benchmark_frequency_domain_self_transfer.py
  build_physical_feature_inverse_nn_report_figures.py
  build_physical_feature_inverse_training_table.py
  compare_balanced_mse_bni_ablation.py
  compare_global_vs_mondrian_conformal_calibration.py
  compare_physical_feature_q_input_ablation.py
  compare_tandem_geometry_anchor_ablation.py
  plan_physical_feature_inverse_nn_architecture_search.py
  plan_tandem_local_refinement_benchmark.py
  run_physical_feature_inverse_checkpoint_test.py
  select_balanced_mse_bni_temperature.py
  select_balanced_physical_feature_checkpoint.py
  train_broadband_sparameter_pca_surrogate.py
  train_physical_feature_inverse_nn_architecture_search.py
  train_physical_feature_tandem_inverse.py
  audit_accepted_1m_campaign_completion.py
)

stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup="$STAGE/activation_backup_$stamp"
mkdir -p "$backup"

for name in "${FILES[@]}"; do
  source_path="$RUNTIME/scripts/$name"
  target_path="$PROJECT/scripts/$name"
  [[ -f "$source_path" ]] || { echo "ERROR: staged file missing: $name" >&2; exit 2; }
  if [[ -e "$target_path" ]]; then
    cp -p "$target_path" "$backup/$name"
  else
    : > "$backup/$name.REMOTE_MISSING"
  fi
  temp_path="$target_path.tmp.model-runtime.$stamp"
  cp -p "$source_path" "$temp_path"
  mv -f "$temp_path" "$target_path"
done

bash -n "$PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh"
actual_runner_sha="$(sha256sum "$PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh" | awk '{print $1}')"
if [[ "$actual_runner_sha" != "$EXPECTED_RUNNER_SHA" ]]; then
  echo "ERROR: activated model runner SHA does not match." >&2
  exit 2
fi

{
  echo "activated_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "boundary_marker=$BOUNDARY_MARKER"
  echo "runtime_archive_sha256=$EXPECTED_ARCHIVE_SHA"
  echo "runner_sha256=$actual_runner_sha"
  echo "installed_file_count=${#FILES[@]}"
  echo "backup_dir=$backup"
  echo "production_runner_changed=false"
  echo "candidate_queue_worker_changed=false"
  echo "port_process_frequency_contract_changed=false"
} > "$STAGE/MODEL_RUNTIME_ACTIVATED"

cat "$STAGE/MODEL_RUNTIME_ACTIVATED"
