#!/usr/bin/env bash
set -euo pipefail

# Run on mars-0002 after Guacamole control is restored.
# Purpose: check whether the 15 GHz / 1-point clean mini smoke exists; if not,
# start exactly one real EMX sample in a new clean directory.

PROJECT_DIR="${PROJECT_DIR:-/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design}"
WORK_ROOT="${WORK_ROOT:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
CFG="${CFG:-next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/02_final_s8p_config/final_s8p_physical_feature_500.yaml}"
CAND="${CAND:-next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/physical_feature_s8p_launch_packet/geometry_bootstrap_candidate_queue/s8p_geometry_bootstrap_candidate_queue.csv}"

export CDS_LIC_FILE="${CDS_LIC_FILE:-27000@example-license-server}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
export EMX_BINARY="${EMX_BINARY:-${PROJECT_DIR}/mars_local_bin/emx_cae_singularity}"

cd "${PROJECT_DIR}"

echo "PROJECT_DIR=${PROJECT_DIR}"
echo "WORK_ROOT=${WORK_ROOT}"
echo "EMX_BINARY=${EMX_BINARY}"
echo "CDS_LIC_FILE=${CDS_LIC_FILE}"

echo "[1/4] Existing clean mini smoke directories"
find "${WORK_ROOT}" -maxdepth 1 -type d -name 'clean_mini_1freq_*' \
  -printf '%TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort | tail -10 || true

latest_mini="$(
  find "${WORK_ROOT}" -maxdepth 1 -type d -name 'clean_mini_1freq_*' \
    -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true
)"

if [[ -n "${latest_mini}" ]]; then
  echo "[2/4] Latest mini smoke status: ${latest_mini}"
  ps -u "${USER:-researcher}" -o pid,stat,etime,cmd | grep "${latest_mini}" | grep -v grep || true
  find "${latest_mini}" -type f \( -name '*.s8p' -o -name '*.S8P' \) -printf '%s %p\n' 2>/dev/null || true
  if [[ -f "${latest_mini}/parallel_candidate_queue_dataset_summary.json" ]]; then
    python3 - "${latest_mini}/parallel_candidate_queue_dataset_summary.json" <<'PY' || true
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print("SUMMARY_JSON=" + str(path))
for key in ("overall_status", "ok_count", "fail_count", "expected_count"):
    print(f"{key}={data.get(key)}")
PY
  fi
fi

running_count="$(
  ps -u "${USER:-researcher}" -o cmd | grep 'clean_mini_1freq_' | grep -v grep | wc -l | tr -d ' '
)"
nonzero_count=0
if [[ -n "${latest_mini}" ]]; then
  nonzero_count="$(
    find "${latest_mini}" -type f \( -name '*.s8p' -o -name '*.S8P' \) -size +0c 2>/dev/null | wc -l | tr -d ' '
  )"
fi

if [[ "${running_count}" != "0" || "${nonzero_count}" != "0" ]]; then
  echo "[3/4] Not starting a duplicate mini smoke"
  echo "running_count=${running_count}"
  echo "nonzero_s8p_count=${nonzero_count}"
  exit 0
fi

echo "[3/4] Start new 15 GHz / 1-point clean mini smoke"
MINI="${WORK_ROOT}/clean_mini_1freq_$(date +%Y%m%d_%H%M%S)"
MINI_CFG="${MINI}/mini_15ghz.yaml"
mkdir -p "${MINI}"

python3 - <<'PY'
import os
from pathlib import Path
import yaml

project = Path(os.environ["PROJECT_DIR"])
cfg = project / os.environ["CFG"]
out = Path(os.environ["MINI_CFG"])
data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
target = data.setdefault("target", {})
target["frequency_start_hz"] = 15.0e9
target["frequency_stop_hz"] = 15.0e9
target["frequency_step_hz"] = 0.1e9
target["band_points"] = 1
out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
PY

nohup .venv/bin/python scripts/run_candidate_queue_dataset_parallel.py \
  --candidate-csv "${CAND}" \
  --out-dir "${MINI}" \
  --config "${MINI_CFG}" \
  --jobs 1 \
  --max-count 1 \
  --expected-count 1 \
  --batch-size 1 \
  --expected-jobs 1 \
  --expected-port-mode single_ended_shield_grounded \
  --expected-pin-purpose 51 \
  --expected-frequency-start-ghz 15.0 \
  --expected-frequency-stop-ghz 15.0 \
  --expected-frequency-step-ghz 0.1 \
  --expected-frequency-points 1 \
  --fail-on-error > "${MINI}/run.log" 2>&1 &

echo "MINI_PID=$!"
echo "MINI=${MINI}"
echo "MINI_CFG=${MINI_CFG}"
echo "[4/4] Monitor with:"
echo "tail -f ${MINI}/run.log"
