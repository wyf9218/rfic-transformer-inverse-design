#!/usr/bin/env bash
set -euo pipefail

locate_repo() {
  for candidate in \
    "${REPO:-}" \
    "$PWD" \
    "$PWD/rfic-transformer-inverse-design" \
    "$HOME/researcher/transformer_inverse/rfic-transformer-inverse-design" \
    "$HOME/transformer_inverse/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/rfic-transformer-inverse-design" \
    "/shared/research/${USER:-researcher}/transformer_inverse/rfic-transformer-inverse-design"; do
    if [[ -n "$candidate" && -f "$candidate/pyproject.toml" && -d "$candidate/rfic_transformer_inverse_design" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

HOTFIX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HOTFIX_DIR/files"
REPO_ROOT="$(locate_repo)"
cd "$REPO_ROOT"

CADENCE_LICENSE_FILE="${CADENCE_LICENSE_FILE:-27000@example-license-server}"
CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"
EMX_PROCESS_FILE="${EMX_PROCESS_FILE:-/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc}"
CADENCE_PDK_CDS_LIB="${CADENCE_PDK_CDS_LIB:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib}"
CADENCE_LAYER_MAP="${CADENCE_LAYER_MAP:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap}"
TECH_LIB_NAME="${TECH_LIB_NAME:-tsmcN65}"

export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export CDSLMD_LICENSE_FILE="${CDSLMD_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
export CADENCE_INSTALL_ROOT EMX_PROCESS_FILE CADENCE_PDK_CDS_LIB CADENCE_LAYER_MAP TECH_LIB_NAME

install -m 0644 "$SRC/scripts/discover_mars_emx_cadence_paths.py" "$REPO_ROOT/scripts/discover_mars_emx_cadence_paths.py"
install -m 0644 "$SRC/scripts/prepare_final_s8p_physical_feature_config.py" "$REPO_ROOT/scripts/prepare_final_s8p_physical_feature_config.py"
install -m 0644 "$SRC/scripts/build_next_gen_s8p_mars_execution_packet.py" "$REPO_ROOT/scripts/build_next_gen_s8p_mars_execution_packet.py"
if [[ -f "$SRC/MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh" ]]; then
  install -m 0755 "$SRC/MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh" "$REPO_ROOT/MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh"
fi

WRAPPER="$REPO_ROOT/mars_local_bin/emx_cae_singularity"
CAE_WRAPPER="/cae/apps/data/cadence-2025/installs/bin/emx"
mkdir -p "$(dirname "$WRAPPER")"
if [[ -f "$CAE_WRAPPER" ]]; then
  sed \
    -e 's#/usr/bin/apptainer#/usr/bin/singularity#g' \
    -e 's#BINDPATH="/opt:/opt,/cae:/cae"#BINDPATH="/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home"#' \
    "$CAE_WRAPPER" > "$WRAPPER"
else
  cat > "$WRAPPER" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
SINGULARITY_BIN="${SINGULARITY_BIN:-$(command -v singularity || true)}"
if [[ -z "$SINGULARITY_BIN" ]]; then
  echo "ERROR: singularity not found for CAE EMX wrapper." >&2
  exit 127
fi
IMAGE="/cae/apps/data/cadence-2025/CAE/image/cadence-alma-wrapper.sif"
REAL_EMX="/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx"
exec "$SINGULARITY_BIN" exec --bind "/opt:/opt,/cae:/cae,/srv:/srv,/volumes:/volumes,/home:/home" "$IMAGE" "$REAL_EMX" "$@"
WRAP
fi
chmod +x "$WRAPPER"
export EMX_BINARY="$WRAPPER"

CONFIG="$REPO_ROOT/next_gen_s8p_mars_execution_packet_20260630_5_60_1p0_grounded_tap_20pilot/02_final_s8p_config/final_s8p_physical_feature_500.yaml"
if [[ ! -f "$CONFIG" ]]; then
  echo "ERROR: final config not found: $CONFIG" >&2
  exit 22
fi
cp "$CONFIG" "$CONFIG.hotfix_$(date +%Y%m%d_%H%M%S).bak"
"${PYTHON:-python3}" - "$CONFIG" "$WRAPPER" <<'PY'
from pathlib import Path
import sys
import yaml

config = Path(sys.argv[1])
wrapper = sys.argv[2]
data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
target = data.setdefault("target", {})
target["topology_mode"] = "1t1t"
target["frequency_start_hz"] = 5.0e9
target["frequency_stop_hz"] = 60.0e9
target["frequency_step_hz"] = 1.0e9
target["band_points"] = 56

emx = data.setdefault("emx", {})
emx["emx_binary"] = wrapper
emx["emx_process_file"] = "/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc"
emx["license_file"] = "27000@example-license-server"
emx["cdslmd_license_file"] = "27000@example-license-server"
emx["cadence_install_root"] = "/cae/apps/data/cadence-2025/installs/IC231"
emx["cadence_pdk_cds_lib"] = "/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib"
emx["cadence_tech_lib"] = "tsmcN65"
emx["cadence_layer_map"] = "/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap"
emx["port_mode"] = "single_ended_shield_grounded"
emx["cadence_pin_purpose"] = 51
emx["differential_port_pairs"] = "1,4:5,6"
emx["ground_unused_s8p_ports"] = True
power = emx.setdefault("power_line_8port", {})
power["enabled"] = True
power["port_ground_reference"] = "shield"

config.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")
PY

PROBE="mars56_hotfix_license_probe_$(date +%Y%m%d_%H%M%S).log"
set +e
"$CADENCE_INSTALL_ROOT/bin/dbAccess" -version > "$PROBE" 2>&1
probe_rc=$?
set -e
echo "HOTFIX_PROBE_LOG=$REPO_ROOT/$PROBE"
cat "$PROBE"
if [[ "$probe_rc" != "0" ]]; then
  echo "HOTFIX_PROBE_STATUS=FAIL rc=$probe_rc"
  exit "$probe_rc"
fi
echo "HOTFIX_PROBE_STATUS=PASS"

if [[ "${RUN_AFTER_INSTALL:-1}" == "1" ]]; then
  PACKET="next_gen_s8p_mars_execution_packet_20260630_5_60_1p0_grounded_tap_20pilot"
  LAUNCH="$PACKET/physical_feature_s8p_launch_packet_20_5_60_1p0_grounded_tap/physical_feature_s8p_launch.commands.sh"
  if [[ ! -f "$LAUNCH" ]]; then
    echo "ERROR: launch command not found: $LAUNCH" >&2
    exit 23
  fi
  OUT="mars56_hotfix_rerun_$(date +%Y%m%d_%H%M%S).out"
  nohup bash "$LAUNCH" > "$OUT" 2>&1 &
  echo "$!" > mars56_hotfix_rerun.pid
  echo "HOTFIX_RERUN_PID=$(cat mars56_hotfix_rerun.pid)"
  echo "HOTFIX_RERUN_LOG=$REPO_ROOT/$OUT"
fi

echo "HOTFIX_INSTALL_STATUS=PASS"
