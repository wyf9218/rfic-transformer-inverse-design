#!/usr/bin/env bash
set -euo pipefail

# MARS entry point for the current S8P contract:
#   - 5-60 GHz inclusive
#   - 1.0 GHz spacing, 56 frequency points
#   - 8-port Touchstone output (.s8p)
#   - RF signal pairs stay active: P1-P4 and P5-P6
#   - unused supply/center-tap ports are AC-grounded during metric extraction:
#       P2, P3, P7, P8
#
# This script is meant to run on mars-0002 after INSTALL_ON_MARS.sh syncs the
# current repo snapshot. It starts the guarded execution packet and packages the
# resulting run directory for transfer back to the local machine.

locate_repo() {
  for candidate in \
    "${PROJECT:-}" \
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

REPO_ROOT="${REPO_ROOT:-$(locate_repo)}"
cd "$REPO_ROOT"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
  else
    PYTHON="$(command -v python)"
  fi
fi
export PYTHON

STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
WORK_DIR="${WORK_DIR:-/shared/research/${USER:-researcher}/codex_s8p_56pt_grounded_tap_20260630}"
RETURNS_DIR="${RETURNS_DIR:-$WORK_DIR/returns}"
EXEC_PACKET="${EXEC_PACKET:-next_gen_s8p_mars_execution_packet_20260630_5_60_1p0_grounded_tap_20pilot}"
RUN_DIR="${RUN_DIR:-$EXEC_PACKET/new_s8p_physical_feature_emx_20_5_60_1p0_grounded_tap}"
LOG_PATH="$RETURNS_DIR/mars_s8p_56pt_grounded_tap_20pilot_${STAMP}.log"

mkdir -p "$RETURNS_DIR"

link_latest() {
  local source_path="$1"
  local target_path="$2"
  rm -f "$target_path"
  ln -s "$source_path" "$target_path" 2>/dev/null || cp -f "$source_path" "$target_path"
}

echo "S8P_56PT_GROUNDED_TAP_START $(date)"
echo "REPO_ROOT=$REPO_ROOT"
echo "PYTHON=$PYTHON"
echo "EXEC_PACKET=$EXEC_PACKET"
echo "RUN_DIR=$RUN_DIR"
echo "RETURNS_DIR=$RETURNS_DIR"
echo "FREQUENCY_CONTRACT=5-60GHz inclusive, 1GHz step, 56 points"
echo "TOUCHSTONE_CONTRACT=.s8p, 8 ports"
echo "ACTIVE_RF_PAIRS=P1-P4:P5-P6"
echo "AC_GROUNDED_UNUSED_PORTS=P2,P3,P7,P8"
echo "SIGNAL_PORT_REFERENCE_GROUND=local shield/M5 reference conductor, not exported as extra ports"

CADENCE_LICENSE_FILE="${CADENCE_LICENSE_FILE:-27000@example-license-server}"
export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export CDSLMD_LICENSE_FILE="${CDSLMD_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"
export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"
export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"
export CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"
export CADENCE_RUNTIME_LD_PATH="${CADENCE_RUNTIME_LD_PATH:-${CADENCE_INSTALL_ROOT}/tools.lnx86/lib/64bit/RHEL/RHEL7:/cae/apps/data/cadence-2025/installs/INNOVUS211/tools.lnx86/lib}"
export LD_LIBRARY_PATH="${CADENCE_RUNTIME_LD_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export EMX_PROCESS_FILE="${EMX_PROCESS_FILE:-/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc}"
export CADENCE_PDK_CDS_LIB="${CADENCE_PDK_CDS_LIB:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib}"
export CADENCE_LAYER_MAP="${CADENCE_LAYER_MAP:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap}"
export TECH_LIB_NAME="${TECH_LIB_NAME:-tsmcN65}"
echo "CADENCE_LICENSE_FILE=${CADENCE_LICENSE_FILE}"
echo "CADENCE_INSTALL_ROOT=${CADENCE_INSTALL_ROOT}"
echo "CADENCE_RUNTIME_LD_PATH=${CADENCE_RUNTIME_LD_PATH}"
echo "EMX_PROCESS_FILE=${EMX_PROCESS_FILE}"
echo "CADENCE_LAYER_MAP=${CADENCE_LAYER_MAP}"

if [[ ! -f "$EXEC_PACKET/next_gen_s8p_mars_execution.commands.sh" ]]; then
  echo "Missing execution command: $EXEC_PACKET/next_gen_s8p_mars_execution.commands.sh" >&2
  exit 20
fi

export REPO_ROOT
export RUN_EMX="${RUN_EMX:-1}"
export PACKET_ROOT="${PACKET_ROOT:-$EXEC_PACKET}"
export AUTO_INSTALL_PY_DEPS="${AUTO_INSTALL_PY_DEPS:-0}"

set +e
bash "$EXEC_PACKET/next_gen_s8p_mars_execution.commands.sh" 2>&1 | tee "$LOG_PATH"
run_rc=${PIPESTATUS[0]}
set -e

PACKAGE_TAR="$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_20pilot_${STAMP}.tar.gz"
PACKAGE_INVENTORY="${PACKAGE_TAR}.inventory.json"
PACKAGE_REPORT="${PACKAGE_TAR}.inventory.md"
PACKAGE_MANIFEST="${PACKAGE_TAR}.launch_manifest.json"
PACKAGE_VERIFY_DIR="$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_20pilot_verify_${STAMP}"
PACKAGE_VERIFY_LOG="$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_20pilot_verify_${STAMP}.log"

packaged_with_official_inventory=0
if [[ -d "$RUN_DIR" && -f "scripts/package_mars_dataset_run.py" ]]; then
  set +e
  "$PYTHON" scripts/package_mars_dataset_run.py "$RUN_DIR" \
    --out "$PACKAGE_TAR" \
    --inventory "$PACKAGE_INVENTORY" \
    --report "$PACKAGE_REPORT" \
    --include-layout-previews \
    --include-quality-figures \
    --include-hfss-validation-assets
  package_rc=$?
  set -e
  if [[ "$package_rc" -eq 0 && -f "$PACKAGE_TAR" && -f "$PACKAGE_INVENTORY" && -f "$PACKAGE_REPORT" ]]; then
    packaged_with_official_inventory=1
  else
    echo "WARNING: official package_mars_dataset_run.py packaging failed with rc=$package_rc; using fallback run bundle." >&2
  fi
fi

if [[ "$packaged_with_official_inventory" != "1" ]]; then
  "$PYTHON" - "$REPO_ROOT" "$EXEC_PACKET" "$RUN_DIR" "$LOG_PATH" "$run_rc" "$PACKAGE_TAR" "$PACKAGE_MANIFEST" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
exec_packet = (repo_root / sys.argv[2]).resolve()
run_dir = (repo_root / sys.argv[3]).resolve()
log_path = Path(sys.argv[4]).resolve()
run_rc = int(sys.argv[5])
tar_path = Path(sys.argv[6]).resolve()
manifest_path = Path(sys.argv[7]).resolve()

tar_path.parent.mkdir(parents=True, exist_ok=True)
roots = []
for path in (exec_packet, run_dir, log_path):
    if path.exists():
        roots.append(path)

files = []
with tarfile.open(tar_path, "w:gz") as tar:
    for root in roots:
        if root.is_file():
            arcname = Path(root.name)
            tar.add(root, arcname=str(arcname))
            files.append(root)
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            arcname = root.name / path.relative_to(root)
            tar.add(path, arcname=str(arcname))
            files.append(path)

sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
manifest = {
    "schema": "rfic_transformer_s8p_56pt_grounded_tap_mars_return.v1",
    "status": "PASS" if run_rc == 0 else "FAIL",
    "run_return_code": run_rc,
    "frequency_start_ghz": 5.0,
    "frequency_stop_ghz": 60.0,
    "frequency_step_ghz": 1.0,
    "frequency_points": 56,
    "touchstone_extension": ".s8p",
    "touchstone_ports": 8,
    "active_rf_pairs": "1,4:5,6",
    "ac_grounded_unused_ports": [2, 3, 7, 8],
    "signal_port_reference_ground": "local shield/M5 reference conductor",
    "tarball": str(tar_path),
    "tarball_sha256": sha,
    "exec_packet": str(exec_packet),
    "run_dir": str(run_dir),
    "log_path": str(log_path),
    "file_count": len(files),
    "s8p_count": sum(1 for path in files if path.suffix.lower() == ".s8p"),
    "files": [str(path) for path in files],
}
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
tar_path.with_suffix(tar_path.suffix + ".sha256").write_text(f"{sha}  {tar_path.name}\n", encoding="utf-8")
print(f"RETURN_PACKAGE={tar_path}")
print(f"RETURN_SHA256={sha}")
print(f"RETURN_MANIFEST={manifest_path}")
print(f"RETURN_STATUS={manifest['status']}")
print(f"RETURN_S8P_COUNT={manifest['s8p_count']}")
PY
else
  "$PYTHON" - "$REPO_ROOT" "$EXEC_PACKET" "$RUN_DIR" "$LOG_PATH" "$run_rc" "$PACKAGE_TAR" "$PACKAGE_INVENTORY" "$PACKAGE_MANIFEST" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
exec_packet = (repo_root / sys.argv[2]).resolve()
run_dir = (repo_root / sys.argv[3]).resolve()
log_path = Path(sys.argv[4]).resolve()
run_rc = int(sys.argv[5])
tar_path = Path(sys.argv[6]).resolve()
inventory_path = Path(sys.argv[7]).resolve()
manifest_path = Path(sys.argv[8]).resolve()

sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
manifest = {
    "schema": "rfic_transformer_s8p_56pt_grounded_tap_mars_return_launch.v1",
    "status": "PASS" if run_rc == 0 else "FAIL",
    "run_return_code": run_rc,
    "frequency_start_ghz": 5.0,
    "frequency_stop_ghz": 60.0,
    "frequency_step_ghz": 1.0,
    "frequency_points": 56,
    "touchstone_extension": ".s8p",
    "touchstone_ports": 8,
    "active_rf_pairs": "1,4:5,6",
    "ac_grounded_unused_ports": [2, 3, 7, 8],
    "signal_port_reference_ground": "local shield/M5 reference conductor",
    "tarball": str(tar_path),
    "tarball_sha256": sha,
    "inventory": str(inventory_path),
    "inventory_file_count": inventory.get("file_count"),
    "inventory_category_counts": inventory.get("category_counts"),
    "exec_packet": str(exec_packet),
    "run_dir": str(run_dir),
    "log_path": str(log_path),
}
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"RETURN_PACKAGE={tar_path}")
print(f"RETURN_SHA256={sha}")
print(f"RETURN_INVENTORY={inventory_path}")
print(f"RETURN_MANIFEST={manifest_path}")
print(f"RETURN_STATUS={manifest['status']}")
PY
fi

if [[ "$packaged_with_official_inventory" == "1" && -f "$PACKAGE_INVENTORY" ]]; then
  (
    cd "$REPO_ROOT"
    "$PYTHON" scripts/verify_mars_dataset_package.py "$PACKAGE_TAR" \
      --inventory "$PACKAGE_INVENTORY" \
      --inventory-report "$PACKAGE_REPORT" \
      --sha256-file "${PACKAGE_TAR}.sha256" \
      --out-dir "$PACKAGE_VERIFY_DIR" \
      --run-progress-audit \
      --expected-count 20 \
      --expected-touchstone-ports 8 \
      --required-touchstone-extension .s8p \
      --expected-frequency-start-ghz 5 \
      --expected-frequency-stop-ghz 60 \
      --expected-frequency-step-ghz 1 \
      --expected-frequency-points 56 \
      --require-emx-command \
      --expected-port-mode single_ended_shield_grounded \
      --expected-pin-purpose 51 \
      --require-s8p-quality-gates \
      --require-next-gen-s8p-status \
      --require-run-config \
      --no-fail-exit
  ) > "$PACKAGE_VERIFY_LOG" 2>&1 || true
else
  {
    echo "overall_status=SKIP"
    echo "reason=official package inventory not available; fallback return bundle was created instead"
    echo "tarball=$PACKAGE_TAR"
    echo "manifest=$PACKAGE_MANIFEST"
  } > "$PACKAGE_VERIFY_LOG"
fi

link_latest "$PACKAGE_TAR" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"
link_latest "${PACKAGE_TAR}.sha256" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.tar.gz.sha256"
link_latest "$PACKAGE_MANIFEST" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_manifest.json"
if [[ -f "$PACKAGE_INVENTORY" ]]; then
  link_latest "$PACKAGE_INVENTORY" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.inventory.json"
fi
if [[ -f "$PACKAGE_REPORT" ]]; then
  link_latest "$PACKAGE_REPORT" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.inventory.md"
fi
if [[ -f "$PACKAGE_VERIFY_LOG" ]]; then
  link_latest "$PACKAGE_VERIFY_LOG" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_verify.log"
fi
if [[ -f "$PACKAGE_VERIFY_DIR/mars_dataset_package_verify_summary.json" ]]; then
  link_latest "$PACKAGE_VERIFY_DIR/mars_dataset_package_verify_summary.json" "$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_verify_summary.json"
fi

echo "S8P_56PT_GROUNDED_TAP_DONE $(date) return_code=$run_rc"
echo "LATEST_RETURN=$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"
echo "LATEST_MANIFEST=$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest_manifest.json"
echo "LATEST_INVENTORY=$RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest.inventory.json"
exit "$run_rc"
