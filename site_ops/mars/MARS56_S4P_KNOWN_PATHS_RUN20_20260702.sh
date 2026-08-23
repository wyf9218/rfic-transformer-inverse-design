#!/usr/bin/env bash
set -euo pipefail

# Run from the patched MARS project root.
PROJECT="${PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702}"
OUT_ROOT="${OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_outputs_20260702}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAX_COUNT="${MAX_COUNT:-20}"
JOBS="${JOBS:-8}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"

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

cd "$PROJECT"
mkdir -p "$OUT_ROOT" mars_local_bin

export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export CDSLMD_LICENSE_FILE="${CDSLMD_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
export SINGULARITYENV_CDSLMD_LICENSE_FILE="${SINGULARITYENV_CDSLMD_LICENSE_FILE:-${CDSLMD_LICENSE_FILE}}"
export LD_LIBRARY_PATH="${CADENCE_RUNTIME_LD_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

echo "CODEX_MARS56_KNOWN_PATHS_CHECK_START $(date)"
test -d "$CADENCE_INSTALL_ROOT"
test -x "$CADENCE_INSTALL_ROOT/bin/dbAccess"
test -f "$EMX_PROCESS_FILE"
test -f "$CADENCE_PDK_CDS_LIB"
test -f "$CADENCE_LAYER_MAP"
test -x "$REAL_EMX"

LICENSE_PROBE_LOG="$OUT_ROOT/mars56_grounded_s4p_dbaccess_license_probe_$(date +%Y%m%d_%H%M%S).log"
set +e
"$CADENCE_INSTALL_ROOT/bin/dbAccess" -version > "$LICENSE_PROBE_LOG" 2>&1
license_probe_rc=$?
set -e
echo "CODEX_MARS56_DBACCESS_LICENSE_PROBE_LOG=$LICENSE_PROBE_LOG"
cat "$LICENSE_PROBE_LOG"
if [[ "$license_probe_rc" != "0" ]]; then
  echo "CODEX_MARS56_DBACCESS_LICENSE_PROBE_FAIL rc=$license_probe_rc"
  exit "$license_probe_rc"
fi
echo "CODEX_MARS56_DBACCESS_LICENSE_PROBE_PASS"

EMX_WRAPPER="$PROJECT/mars_local_bin/emx_cae_singularity"
if [[ -f "$CAE_WRAPPER" ]]; then
  sed \
    -e 's#/usr/bin/apptainer#/usr/bin/singularity#g' \
    -e 's#BINDPATH="/opt:/opt,/cae:/cae"#BINDPATH="/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home"#' \
    "$CAE_WRAPPER" > "$EMX_WRAPPER"
else
  test -f "$SINGULARITY_IMAGE"
  cat > "$EMX_WRAPPER" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
SINGULARITY_BIN="\${SINGULARITY_BIN:-\$(command -v singularity || true)}"
if [[ -z "\$SINGULARITY_BIN" ]]; then
  echo "ERROR: singularity not found for CAE EMX wrapper." >&2
  exit 127
fi
exec "\$SINGULARITY_BIN" exec --bind "/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home" "$SINGULARITY_IMAGE" "$REAL_EMX" "\$@"
WRAP
fi
chmod +x "$EMX_WRAPPER"

CONFIG_TEMPLATE="configs/mars_s4p_grounded_powerline_physical_feature_500_template.yaml"
PATCHED_CONFIG="configs/mars_s4p_grounded_powerline_physical_feature_500_mars_paths.yaml"
PATCH_SUMMARY="$OUT_ROOT/mars56_grounded_s4p_known_paths_patch_$(date +%Y%m%d_%H%M%S).json"

"$PYTHON_BIN" scripts/patch_mars_config_paths.py "$CONFIG_TEMPLATE" \
  --out-config "$PATCHED_CONFIG" \
  --summary "$PATCH_SUMMARY" \
  --emx-binary "$EMX_WRAPPER" \
  --emx-process-file "$EMX_PROCESS_FILE" \
  --cadence-install-root "$CADENCE_INSTALL_ROOT" \
  --cadence-pdk-cds-lib "$CADENCE_PDK_CDS_LIB" \
  --cadence-layer-map "$CADENCE_LAYER_MAP" \
  --cadence-tech-lib "$TECH_LIB_NAME" \
  --license-file "$CADENCE_LICENSE_FILE" \
  --cdslmd-license-file "$CADENCE_LICENSE_FILE" \
  --check-paths

RUN_TAG="mars56_grounded_s4p_known_paths_preflight_$(date +%Y%m%d_%H%M%S)"
ONLY_AUDIT=1 CONFIG="$PATCHED_CONFIG" PYTHON_BIN="$PYTHON_BIN" RUN_TAG="$RUN_TAG" OUT_ROOT="$OUT_ROOT" \
  bash scripts/run_mars56_grounded_s4p_dataset.sh

echo "CODEX_MARS56_KNOWN_PATHS_PREFLIGHT_PASS"
echo "PATCHED_CONFIG=$PROJECT/$PATCHED_CONFIG"
echo "PATCH_SUMMARY=$PATCH_SUMMARY"

if [[ "$RUN_REAL_EMX" != "1" ]]; then
  echo "CODEX_MARS56_REAL_EMX_SKIPPED RUN_REAL_EMX=$RUN_REAL_EMX"
  exit 0
fi

RUN_TAG="mars56_grounded_s4p_real20_$(date +%Y%m%d_%H%M%S)"
LOG="$OUT_ROOT/${RUN_TAG}.log"
echo "CODEX_MARS56_REAL_EMX_START RUN_TAG=$RUN_TAG JOBS=$JOBS MAX_COUNT=$MAX_COUNT LOG=$LOG"
CONFIG="$PATCHED_CONFIG" OUT_ROOT="$OUT_ROOT" JOBS="$JOBS" MAX_COUNT="$MAX_COUNT" RUN_TAG="$RUN_TAG" \
  bash scripts/run_mars56_grounded_s4p_dataset.sh 2>&1 | tee "$LOG"
echo "CODEX_MARS56_REAL_EMX_DONE RUN_TAG=$RUN_TAG LOG=$LOG"
