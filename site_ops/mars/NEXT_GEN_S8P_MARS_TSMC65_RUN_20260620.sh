#!/usr/bin/env bash
set -euo pipefail

# Run this on MARS after the updated sync packet is installed.
# It uses explicit TSMC65/IC231/EMX paths verified on 2026-06-20, runs a strict
# dry-run first, then starts the 500-row / 8-worker S8P EMX run in the background.

PROJECT_DIR="${PROJECT_DIR:-/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design}"
WORK_DIR="${WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
RUNBOOK="${RUNBOOK:-next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/next_gen_s8p_mars_execution.commands.sh}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-0}"

CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"
EMX_PROCESS_FILE="${EMX_PROCESS_FILE:-/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc}"
CADENCE_PDK_CDS_LIB="${CADENCE_PDK_CDS_LIB:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib}"
CADENCE_LAYER_MAP="${CADENCE_LAYER_MAP:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap}"
TECH_LIB_NAME="${TECH_LIB_NAME:-tsmcN65}"
CADENCE_LICENSE_FILE="${CADENCE_LICENSE_FILE:-27000@example-license-server}"

mkdir -p "${WORK_DIR}/logs"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_ROOT="${WORK_DIR}/logs"
DRY_LOG="${LOG_ROOT}/tsmc65_dryrun_${STAMP}.log"
REAL_LOG="${LOG_ROOT}/tsmc65_real_emx_500_${STAMP}.log"
PID_FILE="${LOG_ROOT}/tsmc65_real_emx_500_${STAMP}.pid"
STATUS_FILE="${LOG_ROOT}/tsmc65_latest_status.txt"
JSON_STATUS_FILE="${LOG_ROOT}/tsmc65_latest_status.json"
LATEST_DRY_LOG="${LOG_ROOT}/tsmc65_dryrun_latest.log"
LATEST_REAL_LOG="${LOG_ROOT}/tsmc65_real_emx_500_latest.log"
LATEST_PID_FILE="${LOG_ROOT}/tsmc65_real_emx_500_latest.pid"
LATEST_MONITOR_LOG="${LOG_ROOT}/tsmc65_real_emx_500_latest_monitor.log"
LATEST_PROGRESS_CSV="${LOG_ROOT}/tsmc65_real_emx_500_latest_progress.csv"
LATEST_STATUS_CHECK_LOG="${LOG_ROOT}/tsmc65_real_emx_500_latest_status_check.log"
RETURNS_DIR="${WORK_DIR}/returns"
LATEST_TRANSFER_TARBALL="${RETURNS_DIR}/next_gen_s8p_mars_return_latest.tar.gz"
LATEST_TRANSFER_SHA="${RETURNS_DIR}/next_gen_s8p_mars_return_latest.tar.gz.sha256"
LATEST_TRANSFER_INVENTORY="${RETURNS_DIR}/next_gen_s8p_mars_return_latest.tar.gz.inventory.json"
LATEST_TRANSFER_REPORT="${RETURNS_DIR}/next_gen_s8p_mars_return_latest.tar.gz.inventory.md"
LATEST_TRANSFER_VERIFY_LOG="${RETURNS_DIR}/next_gen_s8p_mars_return_latest_verify.log"
LATEST_TRANSFER_VERIFY_SUMMARY="${RETURNS_DIR}/next_gen_s8p_mars_return_latest_verify_summary.json"
RUN_DIR="${PROJECT_DIR}/next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/new_s8p_physical_feature_emx_500"
EXPECTED_S8P_COUNT="${EXPECTED_S8P_COUNT:-500}"

write_status() {
  local message="$*"
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${message}" | tee "${STATUS_FILE}"
  python3 - "${JSON_STATUS_FILE}" "${message}" "${PROJECT_DIR}" "${WORK_DIR}" "${DRY_LOG}" "${REAL_LOG}" "${PID_FILE}" <<'PY' || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, message, project_dir, work_dir, dry_log, real_log, pid_file = sys.argv[1:]
payload = {
    "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "message": message,
    "project_dir": project_dir,
    "work_dir": work_dir,
    "dry_log": dry_log,
    "real_log": real_log,
    "pid_file": pid_file,
}
Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
}

link_latest() {
  local source="$1"
  local target="$2"
  rm -f "${target}"
  ln -s "${source}" "${target}" 2>/dev/null || cp -f "${source}" "${target}" 2>/dev/null || true
}

is_pid_alive() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

find_project_if_needed() {
  if [[ -f "${PROJECT_DIR}/pyproject.toml" && -d "${PROJECT_DIR}/rfic_transformer_inverse_design" ]]; then
    return 0
  fi
  local found
  found="$(find /home/researcher /shared/research/researcher -maxdepth 7 -type d -name rfic-transformer-inverse-design 2>/dev/null | head -n 1 || true)"
  if [[ -n "${found}" && -f "${found}/pyproject.toml" ]]; then
    PROJECT_DIR="${found}"
    return 0
  fi
  echo "ERROR: cannot locate rfic-transformer-inverse-design; set PROJECT_DIR explicitly" >&2
  return 2
}

make_emx_wrapper() {
  local wrapper="${PROJECT_DIR}/mars_local_bin/emx_cae_singularity"
  local cae_wrapper="/cae/apps/data/cadence-2025/installs/bin/emx"
  mkdir -p "$(dirname "${wrapper}")"
  if [[ -f "${cae_wrapper}" ]]; then
    sed \
      -e 's#/usr/bin/apptainer#/usr/bin/singularity#g' \
      -e 's#BINDPATH="/opt:/opt,/cae:/cae"#BINDPATH="/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home"#' \
      "${cae_wrapper}" > "${wrapper}"
  else
    cat > "${wrapper}" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail

SINGULARITY_BIN="${SINGULARITY_BIN:-$(command -v singularity || true)}"
if [[ -z "${SINGULARITY_BIN}" ]]; then
  echo "ERROR: singularity not found; CAE /cae/apps/bin/emx wrapper expects apptainer, but MARS has singularity." >&2
  exit 127
fi

IMAGE="/cae/apps/data/cadence-2025/CAE/image/cadence-alma-wrapper.sif"
REAL_EMX="/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx"
if [[ ! -f "${IMAGE}" ]]; then
  echo "ERROR: missing Cadence singularity image: ${IMAGE}" >&2
  exit 2
fi

BIND_PATHS="/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home"
for maybe in /data /raid /disk; do
  if [[ -d "${maybe}" ]]; then
    BIND_PATHS="${BIND_PATHS},${maybe}:${maybe}"
  fi
done

exec "${SINGULARITY_BIN}" exec --bind "${BIND_PATHS}" "${IMAGE}" "${REAL_EMX}" "$@"
WRAP
  fi
  chmod +x "${wrapper}"
  EMX_BINARY="${wrapper}"
}

setup_cadence_license_env() {
  export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"
  export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
  export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
  export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
}

preflight_paths() {
  local missing=0
  for tool in dbAccess strmin strmout virtuoso; do
    if [[ ! -x "${CADENCE_INSTALL_ROOT}/bin/${tool}" ]]; then
      echo "ERROR: missing executable ${CADENCE_INSTALL_ROOT}/bin/${tool}" >&2
      missing=1
    fi
  done
  for path in "${EMX_PROCESS_FILE}" "${CADENCE_PDK_CDS_LIB}" "${CADENCE_LAYER_MAP}"; do
    if [[ ! -e "${path}" ]]; then
      echo "ERROR: missing required TSMC65 path ${path}" >&2
      missing=1
    fi
  done
  if [[ "${missing}" != "0" ]]; then
    return 2
  fi
}

run_dry_preflight() {
  setup_cadence_license_env
  export EMX_BINARY EMX_PROCESS_FILE CADENCE_INSTALL_ROOT CADENCE_PDK_CDS_LIB CADENCE_LAYER_MAP TECH_LIB_NAME AUTO_INSTALL_PY_DEPS
  write_status "DRY_RUN_START project=${PROJECT_DIR}"
  (
    cd "${PROJECT_DIR}"
    "${EMX_BINARY}" --version
    RUN_EMX=0 bash "${RUNBOOK}"
  ) > "${DRY_LOG}" 2>&1
  link_latest "${DRY_LOG}" "${LATEST_DRY_LOG}"
  write_status "DRY_RUN_PASS log=${DRY_LOG}"
}

start_real_emx_background() {
  setup_cadence_license_env
  export EMX_BINARY EMX_PROCESS_FILE CADENCE_INSTALL_ROOT CADENCE_PDK_CDS_LIB CADENCE_LAYER_MAP TECH_LIB_NAME AUTO_INSTALL_PY_DEPS CDS_LIC_FILE LM_LICENSE_FILE SINGULARITYENV_CDS_LIC_FILE SINGULARITYENV_LM_LICENSE_FILE
  if [[ -s "${LATEST_PID_FILE}" ]]; then
    local existing_pid
    existing_pid="$(cat "${LATEST_PID_FILE}" 2>/dev/null || true)"
    if is_pid_alive "${existing_pid}"; then
      write_status "REAL_EMX_ALREADY_RUNNING pid=${existing_pid} latest_log=${LATEST_REAL_LOG}"
      echo "ERROR: real EMX appears to already be running with pid=${existing_pid}." >&2
      echo "Inspect ${LATEST_REAL_LOG} or remove ${LATEST_PID_FILE} only after confirming the old run stopped." >&2
      return 2
    fi
  fi
  write_status "REAL_EMX_STARTING log=${REAL_LOG}"
  nohup bash -lc "
set -euo pipefail
cd '${PROJECT_DIR}'
export EMX_BINARY='${EMX_BINARY}'
export EMX_PROCESS_FILE='${EMX_PROCESS_FILE}'
export CADENCE_INSTALL_ROOT='${CADENCE_INSTALL_ROOT}'
export CADENCE_PDK_CDS_LIB='${CADENCE_PDK_CDS_LIB}'
export CADENCE_LAYER_MAP='${CADENCE_LAYER_MAP}'
export TECH_LIB_NAME='${TECH_LIB_NAME}'
export AUTO_INSTALL_PY_DEPS='${AUTO_INSTALL_PY_DEPS}'
export CDS_LIC_FILE='${CDS_LIC_FILE}'
export LM_LICENSE_FILE='${LM_LICENSE_FILE}'
export SINGULARITYENV_CDS_LIC_FILE='${SINGULARITYENV_CDS_LIC_FILE}'
export SINGULARITYENV_LM_LICENSE_FILE='${SINGULARITYENV_LM_LICENSE_FILE}'
export RUN_EMX=1
echo REAL_EMX_500_START \$(date)
bash '${RUNBOOK}'
echo REAL_EMX_500_DONE \$(date)
" > "${REAL_LOG}" 2>&1 &
  echo $! > "${PID_FILE}"
  cp -f "${PID_FILE}" "${LATEST_PID_FILE}"
  link_latest "${REAL_LOG}" "${LATEST_REAL_LOG}"
  start_real_emx_monitor "$(cat "${PID_FILE}")"
  write_status "REAL_EMX_BACKGROUND pid=$(cat "${PID_FILE}") log=${REAL_LOG}"
}

start_real_emx_monitor() {
  local real_pid="$1"
  local monitor_log="${LOG_ROOT}/tsmc65_real_emx_500_monitor_${STAMP}.log"
  local progress_csv="${LOG_ROOT}/tsmc65_real_emx_500_progress_${STAMP}.csv"
  local status_check_log="${LOG_ROOT}/tsmc65_real_emx_500_status_check_${STAMP}.log"
  link_latest "${monitor_log}" "${LATEST_MONITOR_LOG}"
  link_latest "${progress_csv}" "${LATEST_PROGRESS_CSV}"
  link_latest "${status_check_log}" "${LATEST_STATUS_CHECK_LOG}"
  nohup bash -lc "
set +e
pid='${real_pid}'
real_log='${REAL_LOG}'
status_file='${STATUS_FILE}'
json_status='${JSON_STATUS_FILE}'
project_dir='${PROJECT_DIR}'
work_dir='${WORK_DIR}'
pid_file='${PID_FILE}'
run_dir='${RUN_DIR}'
expected_count='${EXPECTED_S8P_COUNT}'
progress_csv='${progress_csv}'
status_check_log='${status_check_log}'
returns_dir='${RETURNS_DIR}'
latest_transfer_tarball='${LATEST_TRANSFER_TARBALL}'
latest_transfer_sha='${LATEST_TRANSFER_SHA}'
latest_transfer_inventory='${LATEST_TRANSFER_INVENTORY}'
latest_transfer_report='${LATEST_TRANSFER_REPORT}'
latest_transfer_verify_log='${LATEST_TRANSFER_VERIFY_LOG}'
latest_transfer_verify_summary='${LATEST_TRANSFER_VERIFY_SUMMARY}'
printf 'timestamp,state,pid,s8p_count,dataset_data_rows,expected_count,log\n' > \"\${progress_csv}\"
echo MONITOR_START \$(date) pid=\${pid}
while kill -0 \"\${pid}\" >/dev/null 2>&1; do
  s8p_count=\$(find \"\${run_dir}\" -type f \( -name '*.s8p' -o -name '*.S8P' \) 2>/dev/null | wc -l | tr -d ' ')
  if [[ -f \"\${run_dir}/dataset_rows.csv\" ]]; then
    dataset_rows=\$(awk 'END { if (NR > 0) print NR - 1; else print 0 }' \"\${run_dir}/dataset_rows.csv\" 2>/dev/null)
  else
    dataset_rows=0
  fi
  timestamp=\$(date '+%Y-%m-%d %H:%M:%S %Z')
  message=\"REAL_EMX_RUNNING pid=\${pid} s8p_count=\${s8p_count} dataset_rows=\${dataset_rows} expected=\${expected_count} log=\${real_log}\"
  printf '%s %s\n' \"\${timestamp}\" \"\${message}\" | tee \"\${status_file}\"
  printf '%s,%s,%s,%s,%s,%s,%s\n' \"\${timestamp}\" REAL_EMX_RUNNING \"\${pid}\" \"\${s8p_count}\" \"\${dataset_rows}\" \"\${expected_count}\" \"\${real_log}\" >> \"\${progress_csv}\"
  python3 - \"\${json_status}\" \"\${message}\" \"\${project_dir}\" \"\${work_dir}\" \"${DRY_LOG}\" \"\${real_log}\" \"\${pid_file}\" \"\${s8p_count}\" \"\${dataset_rows}\" \"\${expected_count}\" <<'PY' || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, message, project_dir, work_dir, dry_log, real_log, pid_file, s8p_count, dataset_rows, expected_count = sys.argv[1:]
payload = {
    \"generated_utc\": datetime.now(timezone.utc).isoformat(timespec=\"seconds\"),
    \"message\": message,
    \"project_dir\": project_dir,
    \"work_dir\": work_dir,
    \"dry_log\": dry_log,
    \"real_log\": real_log,
    \"pid_file\": pid_file,
    \"s8p_count\": int(s8p_count),
    \"dataset_data_rows\": int(dataset_rows),
    \"expected_count\": int(expected_count),
}
Path(path).write_text(json.dumps(payload, indent=2), encoding=\"utf-8\")
PY
  sleep 60
done
s8p_count=\$(find \"\${run_dir}\" -type f \( -name '*.s8p' -o -name '*.S8P' \) 2>/dev/null | wc -l | tr -d ' ')
if [[ -f \"\${run_dir}/dataset_rows.csv\" ]]; then
  dataset_rows=\$(awk 'END { if (NR > 0) print NR - 1; else print 0 }' \"\${run_dir}/dataset_rows.csv\" 2>/dev/null)
else
  dataset_rows=0
fi
if grep -q 'REAL_EMX_500_DONE' \"\${real_log}\" 2>/dev/null; then
  state='REAL_EMX_DONE'
else
  state='REAL_EMX_STOPPED_OR_FAILED'
fi
message=\"\${state} pid=\${pid} s8p_count=\${s8p_count} dataset_rows=\${dataset_rows} expected=\${expected_count} log=\${real_log}\"
printf '%s %s\n' \"\$(date '+%Y-%m-%d %H:%M:%S %Z')\" \"\${message}\" | tee \"\${status_file}\"
printf '%s,%s,%s,%s,%s,%s,%s\n' \"\$(date '+%Y-%m-%d %H:%M:%S %Z')\" \"\${state}\" \"\${pid}\" \"\${s8p_count}\" \"\${dataset_rows}\" \"\${expected_count}\" \"\${real_log}\" >> \"\${progress_csv}\"
python3 - \"\${json_status}\" \"\${message}\" \"\${project_dir}\" \"\${work_dir}\" \"${DRY_LOG}\" \"\${real_log}\" \"\${pid_file}\" \"\${s8p_count}\" \"\${dataset_rows}\" \"\${expected_count}\" <<'PY' || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path, message, project_dir, work_dir, dry_log, real_log, pid_file, s8p_count, dataset_rows, expected_count = sys.argv[1:]
payload = {
    \"generated_utc\": datetime.now(timezone.utc).isoformat(timespec=\"seconds\"),
    \"message\": message,
    \"project_dir\": project_dir,
    \"work_dir\": work_dir,
    \"dry_log\": dry_log,
    \"real_log\": real_log,
    \"pid_file\": pid_file,
    \"s8p_count\": int(s8p_count),
    \"dataset_data_rows\": int(dataset_rows),
    \"expected_count\": int(expected_count),
}
Path(path).write_text(json.dumps(payload, indent=2), encoding=\"utf-8\")
PY
status_check_script=\"\${project_dir}/NEXT_GEN_S8P_MARS_STATUS_CHECK_20260619.sh\"
if [[ -f \"\${status_check_script}\" ]]; then
  echo FINAL_STATUS_CHECK_START \$(date) script=\${status_check_script}
  (cd \"\${project_dir}\" && WORK_DIR=\"\${work_dir}\" bash \"\${status_check_script}\") > \"\${status_check_log}\" 2>&1
  echo FINAL_STATUS_CHECK_DONE \$(date) log=\${status_check_log}
else
  echo FINAL_STATUS_CHECK_SKIPPED missing=\${status_check_script}
fi
if [[ \"\${state}\" == 'REAL_EMX_DONE' && -d \"\${run_dir}\" && -f \"\${project_dir}/scripts/package_mars_dataset_run.py\" ]]; then
  mkdir -p \"\${returns_dir}\"
  final_config=\"\${project_dir}/next_gen_s8p_mars_execution_packet_20260619_the_best_user_approved_ready/02_final_s8p_config/final_s8p_physical_feature_500.yaml\"
  if [[ -s \"\${final_config}\" ]]; then
    cp -f \"\${final_config}\" \"\${run_dir}/final_s8p_physical_feature_500.yaml\"
    echo RETURN_PACKAGE_CONFIG_COPIED source=\${final_config} target=\${run_dir}/final_s8p_physical_feature_500.yaml
  else
    echo RETURN_PACKAGE_CONFIG_MISSING expected=\${final_config}
  fi
  package_tar=\"\${returns_dir}/next_gen_s8p_mars_return_${STAMP}.tar.gz\"
  package_inventory=\"\${package_tar}.inventory.json\"
  package_report=\"\${package_tar}.inventory.md\"
  package_verify_dir=\"\${returns_dir}/next_gen_s8p_mars_return_verify_${STAMP}\"
  package_verify_log=\"\${returns_dir}/next_gen_s8p_mars_return_verify_${STAMP}.log\"
  echo RETURN_PACKAGE_START \$(date) tar=\${package_tar}
  (
    cd \"\${project_dir}\"
    python3 scripts/package_mars_dataset_run.py \"\${run_dir}\" \
      --out \"\${package_tar}\" \
      --inventory \"\${package_inventory}\" \
      --report \"\${package_report}\" \
      --include-quality-figures \
      --include-hfss-validation-assets
    python3 scripts/verify_mars_dataset_package.py \"\${package_tar}\" \
      --inventory \"\${package_inventory}\" \
      --inventory-report \"\${package_report}\" \
      --sha256-file \"\${package_tar}.sha256\" \
      --out-dir \"\${package_verify_dir}\" \
      --run-progress-audit \
      --expected-count \"\${expected_count}\" \
      --expected-touchstone-ports 8 \
      --required-touchstone-extension .s8p \
      --expected-frequency-start-ghz 5 \
      --expected-frequency-stop-ghz 60 \
      --expected-frequency-step-ghz 0.5 \
      --expected-frequency-points 111 \
      --require-emx-command \
      --expected-port-mode single_ended_shield_grounded \
      --expected-pin-purpose 51 \
      --require-s8p-quality-gates \
      --require-next-gen-s8p-status \
      --require-run-config \
      --no-fail-exit
  ) > \"\${package_verify_log}\" 2>&1
  rm -f \"\${latest_transfer_tarball}\" \"\${latest_transfer_sha}\" \"\${latest_transfer_inventory}\" \"\${latest_transfer_report}\" \"\${latest_transfer_verify_log}\" \"\${latest_transfer_verify_summary}\"
  ln -s \"\${package_tar}\" \"\${latest_transfer_tarball}\" 2>/dev/null || cp -f \"\${package_tar}\" \"\${latest_transfer_tarball}\" 2>/dev/null || true
  actual_latest_sha=\$(sha256sum \"\${latest_transfer_tarball}\" 2>/dev/null | awk '{print \$1}' || true)
  if [[ -n \"\${actual_latest_sha}\" ]]; then
    printf '%s  %s\n' \"\${actual_latest_sha}\" \"\$(basename \"\${latest_transfer_tarball}\")\" > \"\${latest_transfer_sha}\"
  fi
  ln -s \"\${package_inventory}\" \"\${latest_transfer_inventory}\" 2>/dev/null || cp -f \"\${package_inventory}\" \"\${latest_transfer_inventory}\" 2>/dev/null || true
  ln -s \"\${package_report}\" \"\${latest_transfer_report}\" 2>/dev/null || cp -f \"\${package_report}\" \"\${latest_transfer_report}\" 2>/dev/null || true
  ln -s \"\${package_verify_log}\" \"\${latest_transfer_verify_log}\" 2>/dev/null || cp -f \"\${package_verify_log}\" \"\${latest_transfer_verify_log}\" 2>/dev/null || true
  if [[ -f \"\${package_verify_dir}/mars_dataset_package_verify_summary.json\" ]]; then
    ln -s \"\${package_verify_dir}/mars_dataset_package_verify_summary.json\" \"\${latest_transfer_verify_summary}\" 2>/dev/null || cp -f \"\${package_verify_dir}/mars_dataset_package_verify_summary.json\" \"\${latest_transfer_verify_summary}\" 2>/dev/null || true
  fi
  echo RETURN_PACKAGE_DONE \$(date) tar=\${package_tar} verify_log=\${package_verify_log}
else
  echo RETURN_PACKAGE_SKIPPED state=\${state} run_dir=\${run_dir}
fi
echo MONITOR_DONE \$(date) state=\${state}
" > "${monitor_log}" 2>&1 &
}

find_project_if_needed
make_emx_wrapper
preflight_paths
run_dry_preflight

if [[ "${RUN_REAL_EMX}" == "1" ]]; then
  start_real_emx_background
else
  write_status "STOP_AFTER_DRY_RUN RUN_REAL_EMX=${RUN_REAL_EMX}"
fi

cat <<EOF
PROJECT_DIR=${PROJECT_DIR}
EMX_BINARY=${EMX_BINARY}
CADENCE_INSTALL_ROOT=${CADENCE_INSTALL_ROOT}
EMX_PROCESS_FILE=${EMX_PROCESS_FILE}
CADENCE_PDK_CDS_LIB=${CADENCE_PDK_CDS_LIB}
CADENCE_LAYER_MAP=${CADENCE_LAYER_MAP}
TECH_LIB_NAME=${TECH_LIB_NAME}
DRY_LOG=${DRY_LOG}
REAL_LOG=${REAL_LOG}
LATEST_REAL_LOG=${LATEST_REAL_LOG}
PID_FILE=${PID_FILE}
LATEST_PID_FILE=${LATEST_PID_FILE}
STATUS_FILE=${STATUS_FILE}
JSON_STATUS_FILE=${JSON_STATUS_FILE}
LATEST_MONITOR_LOG=${LATEST_MONITOR_LOG}
LATEST_PROGRESS_CSV=${LATEST_PROGRESS_CSV}
LATEST_STATUS_CHECK_LOG=${LATEST_STATUS_CHECK_LOG}
LATEST_TRANSFER_TARBALL=${LATEST_TRANSFER_TARBALL}
LATEST_TRANSFER_SHA=${LATEST_TRANSFER_SHA}
LATEST_TRANSFER_INVENTORY=${LATEST_TRANSFER_INVENTORY}
LATEST_TRANSFER_REPORT=${LATEST_TRANSFER_REPORT}
LATEST_TRANSFER_VERIFY_LOG=${LATEST_TRANSFER_VERIFY_LOG}
LATEST_TRANSFER_VERIFY_SUMMARY=${LATEST_TRANSFER_VERIFY_SUMMARY}
EOF
