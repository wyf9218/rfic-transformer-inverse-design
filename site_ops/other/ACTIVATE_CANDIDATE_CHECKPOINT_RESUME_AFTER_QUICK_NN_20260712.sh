#!/usr/bin/env bash
# Activate the candidate-level NN checkpoint/resume patch only after the
# currently running first-100k quick NN has naturally exited.

set -euo pipefail

BASE="${BASE:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705/mars56_s4p_million_20260705_1256}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
RUNTIME="${RUNTIME:-$BASE/status/cadence_stderr_gate_staging_20260712_050310/complete_runtime}"
STAGE="${STAGE:-$BASE/status/candidate_checkpoint_resume_staging_20260712_1925}"
NN_OUT="$BASE/status/first100k_urgent_targeted_20260709/accepted_checkpoint_watcher/first100k_accepted_model_checkpoint/nn_architecture_training"

EXPECTED_TRAIN_SHA="74772aa33105ac7889daef0f8fc2e931346ce1c18984444b26a505e62e7000fe"
EXPECTED_RUNNER_SHA="4b63ff4b0e337abf9ff32ff54c60e2abbb64f1ec33b02b9e3076c9ce8fa01213"
EXPECTED_UNIFORMITY_SHA="033e2e477173b2bf51527c9a0ed2b55dbc492af9c09b9d1919795d97aac1cd16"
EXPECTED_WATCHER_SHA="b75d788cace56c19dc7dc0a237df0bc81e43eba1cd6a2f46c6c1b3b6651e0302"
EXPECTED_SMOKE_SHA="37ea656cf06e4dfc9b1dbe40a7a7f6d03ba685bfa259999286a846eafc8d7f30"

if [[ "$(hostname -f 2>/dev/null || hostname)" != "mars.example.edu" ]]; then
  echo "ERROR: activation must run on mars.example.edu." >&2
  exit 2
fi

if ps -u "$(id -un)" -o args= \
  | grep '[t]rain_physical_feature_inverse_nn_architecture_search.py' \
  | grep -F "$NN_OUT" >/dev/null; then
  echo "ERROR: first-100k quick NN is still active; no files changed." >&2
  exit 2
fi

check_sha() {
  local path="$1" expected="$2"
  [[ -f "$path" ]] || { echo "ERROR: staged file missing: $path" >&2; exit 2; }
  local actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: SHA mismatch for $path: $actual != $expected" >&2
    exit 2
  }
}

check_sha "$STAGE/train_physical_feature_inverse_nn_architecture_search.py" "$EXPECTED_TRAIN_SHA"
check_sha "$STAGE/run_accepted_physical_feature_model_checkpoint.sh" "$EXPECTED_RUNNER_SHA"
check_sha "$STAGE/audit_physical_feature_uniformity.py" "$EXPECTED_UNIFORMITY_SHA"
check_sha "$STAGE/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" "$EXPECTED_WATCHER_SHA"
check_sha "$STAGE/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh" "$EXPECTED_SMOKE_SHA"

required_targets=(
  "$PROJECT/scripts/train_physical_feature_inverse_nn_architecture_search.py"
  "$PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh"
  "$PROJECT/scripts/audit_physical_feature_uniformity.py"
  "$PROJECT/scripts/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh"
  "$PROJECT/scripts/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh"
  "$RUNTIME/scripts/train_physical_feature_inverse_nn_architecture_search.py"
  "$RUNTIME/scripts/run_accepted_physical_feature_model_checkpoint.sh"
  "$RUNTIME/scripts/audit_physical_feature_uniformity.py"
)
for target in "${required_targets[@]}"; do
  [[ -f "$target" ]] || { echo "ERROR: target missing before activation: $target" >&2; exit 2; }
done

stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup="$STAGE/activation_backup_$stamp"
mkdir -p "$backup/project_scripts" "$backup/runtime_scripts"

install_atomic() {
  local source="$1" target="$2" backup_dir="$3"
  [[ -f "$target" ]] || { echo "ERROR: target missing: $target" >&2; exit 2; }
  cp -p "$target" "$backup_dir/$(basename "$target")"
  local temporary="$target.tmp.candidate-checkpoint.$stamp"
  cp -p "$source" "$temporary"
  mv -f "$temporary" "$target"
}

for name in \
  train_physical_feature_inverse_nn_architecture_search.py \
  run_accepted_physical_feature_model_checkpoint.sh \
  audit_physical_feature_uniformity.py
do
  install_atomic "$STAGE/$name" "$PROJECT/scripts/$name" "$backup/project_scripts"
  install_atomic "$STAGE/$name" "$RUNTIME/scripts/$name" "$backup/runtime_scripts"
done

install_atomic \
  "$STAGE/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" \
  "$PROJECT/scripts/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" \
  "$backup/project_scripts"
install_atomic \
  "$STAGE/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh" \
  "$PROJECT/scripts/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh" \
  "$backup/project_scripts"

bash -n \
  "$PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh" \
  "$RUNTIME/scripts/run_accepted_physical_feature_model_checkpoint.sh" \
  "$PROJECT/scripts/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" \
  "$PROJECT/scripts/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh"

PY=/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python
"$PY" -m py_compile \
  "$PROJECT/scripts/train_physical_feature_inverse_nn_architecture_search.py" \
  "$PROJECT/scripts/audit_physical_feature_uniformity.py" \
  "$RUNTIME/scripts/train_physical_feature_inverse_nn_architecture_search.py" \
  "$RUNTIME/scripts/audit_physical_feature_uniformity.py"

check_sha "$PROJECT/scripts/train_physical_feature_inverse_nn_architecture_search.py" "$EXPECTED_TRAIN_SHA"
check_sha "$RUNTIME/scripts/train_physical_feature_inverse_nn_architecture_search.py" "$EXPECTED_TRAIN_SHA"
check_sha "$PROJECT/scripts/run_accepted_physical_feature_model_checkpoint.sh" "$EXPECTED_RUNNER_SHA"
check_sha "$RUNTIME/scripts/run_accepted_physical_feature_model_checkpoint.sh" "$EXPECTED_RUNNER_SHA"
check_sha "$PROJECT/scripts/audit_physical_feature_uniformity.py" "$EXPECTED_UNIFORMITY_SHA"
check_sha "$RUNTIME/scripts/audit_physical_feature_uniformity.py" "$EXPECTED_UNIFORMITY_SHA"
check_sha "$PROJECT/scripts/WATCH_FIRST100K_ACCEPTANCE_AND_TRAIN_20260709.sh" "$EXPECTED_WATCHER_SHA"
check_sha "$PROJECT/scripts/SMOKE_FIRST100K_TRAINING_PIPELINE_20260710.sh" "$EXPECTED_SMOKE_SHA"

marker="$STAGE/CANDIDATE_CHECKPOINT_RESUME_ACTIVATED"
temporary="$marker.tmp"
{
  echo "activated_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "backup_dir=$backup"
  echo "quick_nn_was_active=false"
  echo "remote_processes_restarted=false"
  echo "port_process_frequency_contract_changed=false"
  echo "train_sha256=$EXPECTED_TRAIN_SHA"
  echo "runner_sha256=$EXPECTED_RUNNER_SHA"
  echo "uniformity_sha256=$EXPECTED_UNIFORMITY_SHA"
  echo "watcher_sha256=$EXPECTED_WATCHER_SHA"
  echo "smoke_sha256=$EXPECTED_SMOKE_SHA"
} > "$temporary"
mv -f "$temporary" "$marker"
cat "$marker"
