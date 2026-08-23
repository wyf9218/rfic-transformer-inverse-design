#!/usr/bin/env bash
set -euo pipefail

# DRC-gated MARS EMX pilot for the grounded-power-line transformer.
# Run this on MARS after extracting mars56_s4p_drc_gated_run20_20260705.tar.gz.
#
# Touchstone contract:
# - exported file: .s4p
# - signal ports: P001-P004
# - auxiliary vertical power-line labels P005-P008 are local grounded references
# - frequency grid: 5-60 GHz, 0.5 GHz step, 111 points

PACKET_DIR="${PACKET_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-drc-20260705}"
OUT_ROOT="${OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_drc_outputs_20260705}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAX_COUNT="${MAX_COUNT:-20}"
JOBS="${JOBS:-8}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
ONLY_AUDIT="${ONLY_AUDIT:-0}"
RUN_TAG="${RUN_TAG:-mars56_grounded_s4p_drc20_$(date +%Y%m%d_%H%M%S)}"

CADENCE_LICENSE_FILE="${CADENCE_LICENSE_FILE:-27000@example-license-server}"
CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"
CADENCE_RUNTIME_LD_PATH="${CADENCE_RUNTIME_LD_PATH:-${CADENCE_INSTALL_ROOT}/tools.lnx86/lib/64bit/RHEL/RHEL7:/cae/apps/data/cadence-2025/installs/INNOVUS211/tools.lnx86/lib}"
EMX_PROCESS_FILE="${EMX_PROCESS_FILE:-/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc}"
CADENCE_PDK_CDS_LIB="${CADENCE_PDK_CDS_LIB:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib}"
CADENCE_LAYER_MAP="${CADENCE_LAYER_MAP:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap}"
TECH_LIB_NAME="${TECH_LIB_NAME:-tsmcN65}"

CAE_WRAPPER="${CAE_WRAPPER:-/cae/apps/data/cadence-2025/installs/bin/emx}"
REAL_EMX="${REAL_EMX:-/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx}"
SINGULARITY_IMAGE="${SINGULARITY_IMAGE:-/cae/apps/data/cadence-2025/CAE/image/cadence-alma-wrapper.sif}"

echo "CODEX_DRC_S4P_PACKET_DIR=${PACKET_DIR}"
echo "CODEX_DRC_S4P_PROJECT=${PROJECT}"
echo "CODEX_DRC_S4P_OUT_ROOT=${OUT_ROOT}"
echo "CODEX_DRC_S4P_JOBS=${JOBS}"
echo "CODEX_DRC_S4P_MAX_COUNT=${MAX_COUNT}"

test -d "${PACKET_DIR}/repo_overlay"
test -f "${PACKET_DIR}/candidate_queue/mars56_grounded_s4p_candidate_queue.csv"

mkdir -p "${PROJECT}" "${OUT_ROOT}" "${PROJECT}/outputs/drc_gated_s4p_candidate_queue20_20260705" "${PROJECT}/mars_local_bin"
cp -R "${PACKET_DIR}/repo_overlay/." "${PROJECT}/"
cp "${PACKET_DIR}/candidate_queue/mars56_grounded_s4p_candidate_queue.csv" \
  "${PROJECT}/outputs/drc_gated_s4p_candidate_queue20_20260705/mars56_grounded_s4p_candidate_queue.csv"

cd "${PROJECT}"

export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export CDSLMD_LICENSE_FILE="${CDSLMD_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
export SINGULARITYENV_CDSLMD_LICENSE_FILE="${SINGULARITYENV_CDSLMD_LICENSE_FILE:-${CDSLMD_LICENSE_FILE}}"
export LD_LIBRARY_PATH="${CADENCE_RUNTIME_LD_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

echo "CODEX_DRC_S4P_MARS_PATH_CHECK_START $(date)"
test -d "${CADENCE_INSTALL_ROOT}"
test -x "${CADENCE_INSTALL_ROOT}/bin/dbAccess"
test -f "${EMX_PROCESS_FILE}"
test -f "${CADENCE_PDK_CDS_LIB}"
test -f "${CADENCE_LAYER_MAP}"
test -x "${REAL_EMX}"

LICENSE_PROBE_LOG="${OUT_ROOT}/drc_s4p_dbaccess_license_probe_$(date +%Y%m%d_%H%M%S).log"
set +e
"${CADENCE_INSTALL_ROOT}/bin/dbAccess" -version > "${LICENSE_PROBE_LOG}" 2>&1
license_probe_rc=$?
set -e
echo "CODEX_DRC_S4P_DBACCESS_LICENSE_PROBE_LOG=${LICENSE_PROBE_LOG}"
cat "${LICENSE_PROBE_LOG}"
if [[ "${license_probe_rc}" != "0" ]]; then
  echo "CODEX_DRC_S4P_DBACCESS_LICENSE_PROBE_FAIL rc=${license_probe_rc}"
  exit "${license_probe_rc}"
fi
echo "CODEX_DRC_S4P_DBACCESS_LICENSE_PROBE_PASS"

EMX_WRAPPER="${PROJECT}/mars_local_bin/emx_cae_singularity"
if [[ -f "${CAE_WRAPPER}" ]]; then
  sed \
    -e 's#/usr/bin/apptainer#/usr/bin/singularity#g' \
    -e 's#BINDPATH="/opt:/opt,/cae:/cae"#BINDPATH="/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home"#' \
    "${CAE_WRAPPER}" > "${EMX_WRAPPER}"
else
  test -f "${SINGULARITY_IMAGE}"
  cat > "${EMX_WRAPPER}" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
SINGULARITY_BIN="\${SINGULARITY_BIN:-\$(command -v singularity || true)}"
if [[ -z "\${SINGULARITY_BIN}" ]]; then
  echo "ERROR: singularity not found for CAE EMX wrapper." >&2
  exit 127
fi
exec "\${SINGULARITY_BIN}" exec --bind "/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home" "${SINGULARITY_IMAGE}" "${REAL_EMX}" "\$@"
WRAP
fi
chmod +x "${EMX_WRAPPER}"

CONFIG_TEMPLATE="configs/mars_s4p_grounded_powerline_physical_feature_500_template.yaml"
PATCHED_CONFIG="configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml"
PATCH_SUMMARY="${OUT_ROOT}/drc_s4p_path_patch_${RUN_TAG}.json"

"${PYTHON_BIN}" scripts/patch_mars_config_paths.py "${CONFIG_TEMPLATE}" \
  --out-config "${PATCHED_CONFIG}" \
  --summary "${PATCH_SUMMARY}" \
  --emx-binary "${EMX_WRAPPER}" \
  --emx-process-file "${EMX_PROCESS_FILE}" \
  --cadence-install-root "${CADENCE_INSTALL_ROOT}" \
  --cadence-pdk-cds-lib "${CADENCE_PDK_CDS_LIB}" \
  --cadence-layer-map "${CADENCE_LAYER_MAP}" \
  --cadence-tech-lib "${TECH_LIB_NAME}" \
  --license-file "${CADENCE_LICENSE_FILE}" \
  --cdslmd-license-file "${CADENCE_LICENSE_FILE}" \
  --check-paths

AUDIT_TAG="${RUN_TAG}_objective_audit"
ONLY_AUDIT=1 CONFIG="${PATCHED_CONFIG}" PYTHON_BIN="${PYTHON_BIN}" RUN_TAG="${AUDIT_TAG}" OUT_ROOT="${OUT_ROOT}" \
  bash scripts/run_mars56_grounded_s4p_dataset.sh

if [[ "${ONLY_AUDIT}" == "1" ]]; then
  echo "CODEX_DRC_S4P_ONLY_AUDIT_DONE"
  exit 0
fi

if [[ "${RUN_REAL_EMX}" != "1" ]]; then
  echo "CODEX_DRC_S4P_REAL_EMX_SKIPPED RUN_REAL_EMX=${RUN_REAL_EMX}"
  exit 0
fi

CANDIDATE_CSV="${PROJECT}/outputs/drc_gated_s4p_candidate_queue20_20260705/mars56_grounded_s4p_candidate_queue.csv"
LOG="${OUT_ROOT}/${RUN_TAG}.log"
echo "CODEX_DRC_S4P_REAL_EMX_START $(date) RUN_TAG=${RUN_TAG} JOBS=${JOBS} MAX_COUNT=${MAX_COUNT} LOG=${LOG}"
CONFIG="${PATCHED_CONFIG}" \
OUT_ROOT="${OUT_ROOT}" \
RUN_TAG="${RUN_TAG}" \
JOBS="${JOBS}" \
MAX_COUNT="${MAX_COUNT}" \
CANDIDATE_CSV="${CANDIDATE_CSV}" \
PYTHON_BIN="${PYTHON_BIN}" \
  bash scripts/run_mars56_grounded_s4p_dataset.sh 2>&1 | tee "${LOG}"

SUMMARY="${OUT_ROOT}/${RUN_TAG}_dataset/parallel_candidate_queue_dataset_summary.json"
echo "CODEX_DRC_S4P_REAL_EMX_DONE $(date)"
echo "CODEX_DRC_S4P_SUMMARY=${SUMMARY}"
if [[ -f "${SUMMARY}" ]]; then
  "${PYTHON_BIN}" - <<PY
import json
from pathlib import Path
summary = json.loads(Path("${SUMMARY}").read_text())
print("CODEX_DRC_S4P_OVERALL_STATUS=" + str(summary.get("overall_status")))
print("CODEX_DRC_S4P_ROWS=" + str(summary.get("merged_row_count")))
print("CODEX_DRC_S4P_ELAPSED_SECONDS=" + str(summary.get("elapsed_seconds")))
print("CODEX_DRC_S4P_SECONDS_PER_ROW_EFFECTIVE=" + str(summary.get("seconds_per_row_effective")))
PY
fi
