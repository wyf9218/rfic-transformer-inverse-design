#!/usr/bin/env bash
set -euo pipefail

# Legacy MARS56 grounded S4P million-sample campaign.
#
# Safety note, 2026-07-07:
# The active 1M objective is no longer "generate geometry-uniform chunks and
# test them later". The current accepted flow is U8-gated physical-feature
# acquisition followed by formal *_100k_after_chunk08_pass datasets, strict
# Lp/Ls/Q/|K| 4D uniformity gates, per-100k model checkpoints, cumulative
# checkpoints, and final audit.
#
# Use these active entrypoints instead:
#   RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
#   RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh
#
# This legacy script remains only for reproducibility. It is blocked by default
# so it cannot accidentally generate 100k chunks that look like production data
# but do not satisfy the current physical-feature evidence contract.

ALLOW_LEGACY_GEOMETRY_CAMPAIGN="${ALLOW_LEGACY_GEOMETRY_CAMPAIGN:-0}"
case "$ALLOW_LEGACY_GEOMETRY_CAMPAIGN" in
  0|1) ;;
  *) echo "ERROR: ALLOW_LEGACY_GEOMETRY_CAMPAIGN must be 0 or 1." >&2; exit 2 ;;
esac
if [ "$ALLOW_LEGACY_GEOMETRY_CAMPAIGN" != "1" ]; then
  cat >&2 <<'EOF'
ERROR: This is the legacy geometry-uniform million-campaign launcher.

It is not the active 2026-07-07 1M evidence flow because it does not create
the formal U8-gated *_100k_after_chunk08_pass datasets required by the current
Lp/Ls/Q/|K| uniformity and per-100k model-checkpoint contract.

Use:
  MODE=preflight bash RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh
  WATCH_ITERATIONS=0 SLEEP_SECONDS=1800 bash RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh

To run this legacy script intentionally for reproduction only, set:
  ALLOW_LEGACY_GEOMETRY_CAMPAIGN=1
EOF
  exit 2
fi

PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
OUT_ROOT="${OUT_ROOT:-/shared/research/researcher/mars56_s4p_million_campaign_outputs_20260705}"
PYTHON_BIN="${PYTHON_BIN:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702/.venv/bin/python}"
CONFIG="${CONFIG:-${PROJECT}/configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml}"

RUN_TAG="${RUN_TAG:-mars56_s4p_million_$(date +%Y%m%d_%H%M%S)}"
TOTAL_COUNT="${TOTAL_COUNT:-1000000}"
CHUNK_SIZE="${CHUNK_SIZE:-100000}"
START_CHUNK="${START_CHUNK:-1}"
END_CHUNK="${END_CHUNK:-$(( (TOTAL_COUNT + CHUNK_SIZE - 1) / CHUNK_SIZE ))}"
JOBS="${JOBS:-48}"
SAMPLER="${SAMPLER:-sobol}"
SEED_BASE="${SEED_BASE:-2026070500}"
MAX_TOUCHSTONE_CHECKS="${MAX_TOUCHSTONE_CHECKS:-100000}"
MODEL_MAX_TRAIN_ROWS="${MODEL_MAX_TRAIN_ROWS:-80000}"
MODEL_MAX_TEST_ROWS="${MODEL_MAX_TEST_ROWS:-20000}"

CAMPAIGN_DIR="${OUT_ROOT}/${RUN_TAG}"
mkdir -p "${CAMPAIGN_DIR}"/{candidate_queues,datasets,model_tests,checkpoints,logs}

echo "CODEX_MARS56_S4P_MILLION_START $(date)"
echo "PROJECT=${PROJECT}"
echo "OUT_ROOT=${OUT_ROOT}"
echo "CAMPAIGN_DIR=${CAMPAIGN_DIR}"
echo "TOTAL_COUNT=${TOTAL_COUNT}"
echo "CHUNK_SIZE=${CHUNK_SIZE}"
echo "START_CHUNK=${START_CHUNK}"
echo "END_CHUNK=${END_CHUNK}"
echo "JOBS=${JOBS}"
echo "SAMPLER=${SAMPLER}"
df -h "${OUT_ROOT}" || true

status_json="${CAMPAIGN_DIR}/mars56_s4p_million_campaign_status.json"

write_status() {
  local chunk="$1"
  local state="$2"
  local detail="$3"
  "${PYTHON_BIN}" - "$status_json" "$chunk" "$state" "$detail" <<'PY'
import json
import sys
from datetime import datetime, timezone
path, chunk, state, detail = sys.argv[1:5]
try:
    data = json.load(open(path))
except Exception:
    data = {"chunks": []}
data.update({
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "campaign_status": state,
    "latest_chunk": int(chunk),
    "latest_detail": detail,
})
data.setdefault("chunks", []).append({
    "utc": data["generated_utc"],
    "chunk": int(chunk),
    "state": state,
    "detail": detail,
})
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, ensure_ascii=False)
PY
}

json_status() {
  local path="$1"
  "${PYTHON_BIN}" - "$path" <<'PY'
import json
import sys
path = sys.argv[1]
try:
    print(json.load(open(path)).get("overall_status", ""))
except Exception:
    print("")
PY
}

for chunk in $(seq "${START_CHUNK}" "${END_CHUNK}"); do
  chunk_tag="$(printf 'chunk_%02d_n%06d' "${chunk}" "${CHUNK_SIZE}")"
  queue_dir="${CAMPAIGN_DIR}/candidate_queues/${chunk_tag}"
  dataset_dir="${CAMPAIGN_DIR}/datasets/${chunk_tag}"
  quality_dir="${CAMPAIGN_DIR}/model_tests/${chunk_tag}/quality"
  model_test_dir="${CAMPAIGN_DIR}/model_tests/${chunk_tag}/checkpoint_model"
  checkpoint_dir="${CAMPAIGN_DIR}/checkpoints/${chunk_tag}"
  queue_csv="${queue_dir}/mars56_grounded_s4p_candidate_queue.csv"
  queue_summary="${queue_dir}/mars56_grounded_s4p_candidate_queue_summary.json"
  dataset_summary="${dataset_dir}/parallel_candidate_queue_dataset_summary.json"
  checkpoint_summary="${checkpoint_dir}/mars56_s4p_million_chunk_checkpoint_summary.json"
  chunk_log="${CAMPAIGN_DIR}/logs/${chunk_tag}.log"
  chunk_seed=$((SEED_BASE + chunk))

  {
    echo "CODEX_MARS56_S4P_CHUNK_START chunk=${chunk} tag=${chunk_tag} $(date)"
    write_status "${chunk}" "RUNNING_CHUNK_${chunk}" "started ${chunk_tag}"

    if [[ "$(json_status "${checkpoint_summary}")" == "PASS" ]]; then
      echo "CODEX_MARS56_S4P_CHUNK_SKIP_ALREADY_PASS chunk=${chunk}"
      write_status "${chunk}" "CHUNK_ALREADY_PASS" "${checkpoint_summary}"
      continue
    fi

    if [[ "$(json_status "${queue_summary}")" != "PASS" ]]; then
      rm -rf "${queue_dir}"
      mkdir -p "${queue_dir}"
      echo "CODEX_MARS56_S4P_QUEUE_START chunk=${chunk} seed=${chunk_seed}"
      "${PYTHON_BIN}" "${PROJECT}/scripts/build_mars56_grounded_s4p_candidate_queue.py" \
        --config "${CONFIG}" \
        --out-dir "${queue_dir}" \
        --count "${CHUNK_SIZE}" \
        --expected-count "${CHUNK_SIZE}" \
        --sampler "${SAMPLER}" \
        --seed "${chunk_seed}" \
        --candidate-id-prefix "mars56_s4p_c$(printf '%02d' "${chunk}")" \
        --uniformity-bins 20 \
        --no-fail-exit
    fi
    if [[ "$(json_status "${queue_summary}")" != "PASS" ]]; then
      write_status "${chunk}" "FAILED_QUEUE" "${queue_summary}"
      exit 2
    fi

    echo "CODEX_MARS56_S4P_EMX_START chunk=${chunk} jobs=${JOBS}"
    "${PYTHON_BIN}" "${PROJECT}/scripts/run_candidate_queue_dataset_parallel.py" \
      --candidate-csv "${queue_csv}" \
      --config "${CONFIG}" \
      --out-dir "${dataset_dir}" \
      --jobs "${JOBS}" \
      --max-count "${CHUNK_SIZE}" \
      --expected-count "${CHUNK_SIZE}" \
      --expected-jobs "${JOBS}" \
      --force-wideband-5-60-0p5 \
      --expected-frequency-start-ghz 5 \
      --expected-frequency-stop-ghz 60 \
      --expected-frequency-step-ghz 0.5 \
      --expected-frequency-points 111 \
      --expected-touchstone-extension .s4p \
      --expected-ports 4 \
      --max-touchstone-checks "${MAX_TOUCHSTONE_CHECKS}" \
      --resume-completed \
      --no-fail-exit
    if [[ "$(json_status "${dataset_summary}")" != "PASS" ]]; then
      write_status "${chunk}" "FAILED_EMX_DATASET" "${dataset_summary}"
      exit 2
    fi

    echo "CODEX_MARS56_S4P_MODEL_TEST_PREP_START chunk=${chunk}"
    rm -rf "${quality_dir}" "${model_test_dir}" "${checkpoint_dir}"
    mkdir -p "${quality_dir}" "${model_test_dir}" "${checkpoint_dir}"
    "${PYTHON_BIN}" "${PROJECT}/scripts/run_dataset_quality_gates.py" "${dataset_dir}" \
      --out-dir "${quality_dir}" \
      --require-emx \
      --expected-port-mode single_ended_shield_grounded \
      --expected-pin-purpose 51 \
      --expected-frequency-start-ghz 5 \
      --expected-frequency-stop-ghz 60 \
      --expected-frequency-step-ghz 0.5 \
      --expected-frequency-points 111 \
      --frequency-tolerance-hz 1 \
      --skip-visualization \
      --skip-touchstone-audit \
      --extract-response-features \
      --derive-scalar-q-feature \
      --scalar-q-definition min \
      --scalar-q-output-column q_center \
      --physical-feature-columns lp_nh_center,ls_nh_center,q_center,k_center \
      --select-physical-feature-validation-samples \
      --physical-feature-validation-sample-count 8 \
      --physical-feature-validation-mode coverage_then_random \
      --physical-feature-validation-check-touchstone-exists \
      --build-physical-feature-inverse-training-table \
      --inverse-geometry-config "${CONFIG}" \
      --inverse-training-check-touchstone-exists \
      --no-fail-exit

    training_csv="${quality_dir}/physical_feature_inverse_training_table/physical_feature_inverse_training_table.csv"
    "${PYTHON_BIN}" "${PROJECT}/scripts/run_physical_feature_inverse_checkpoint_test.py" \
      --training-csv "${training_csv}" \
      --out-dir "${model_test_dir}" \
      --min-training-rows "${CHUNK_SIZE}" \
      --max-train-rows "${MODEL_MAX_TRAIN_ROWS}" \
      --max-test-rows "${MODEL_MAX_TEST_ROWS}" \
      --seed "$((chunk_seed + 900000))" \
      --no-fail-exit

    "${PYTHON_BIN}" "${PROJECT}/scripts/audit_mars56_s4p_million_chunk_checkpoint.py" \
      --chunk-index "${chunk}" \
      --expected-sample-count "${CHUNK_SIZE}" \
      --candidate-dir "${queue_dir}" \
      --dataset-dir "${dataset_dir}" \
      --quality-dir "${quality_dir}" \
      --model-test-dir "${model_test_dir}" \
      --out-dir "${checkpoint_dir}" \
      --no-fail-exit
    if [[ "$(json_status "${checkpoint_summary}")" != "PASS" ]]; then
      write_status "${chunk}" "FAILED_CHECKPOINT" "${checkpoint_summary}"
      exit 2
    fi

    write_status "${chunk}" "CHUNK_PASS" "${checkpoint_summary}"
    echo "CODEX_MARS56_S4P_CHUNK_PASS chunk=${chunk} checkpoint=${checkpoint_summary} $(date)"
  } 2>&1 | tee -a "${chunk_log}"
done

write_status "${END_CHUNK}" "CAMPAIGN_COMMAND_FINISHED" "${CAMPAIGN_DIR}"
echo "CODEX_MARS56_S4P_MILLION_DONE $(date)"
echo "CODEX_MARS56_S4P_MILLION_STATUS=${status_json}"
