#!/usr/bin/env bash
set -euo pipefail

# Local-only importer for the automatic MARS return package.
# It verifies the package before extraction and never starts EMX, HFSS, ADS,
# Cadence, or any GUI workflow.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$SCRIPT_DIR/rfic-transformer-inverse-design}"
IMPORT_SCRIPT="$REPO_DIR/scripts/import_next_gen_s8p_mars_return_package.py"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/outputs/next_gen_s8p_mars_return_import_current}"

usage() {
  cat <<'USAGE'
Usage:
  bash NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh /path/to/next_gen_s8p_mars_return_latest.tar.gz

If the tarball path is omitted, the script searches the current directory and
this project directory for next_gen_s8p_mars_return*.tar.gz.

Optional environment variables:
  REPO_DIR=/path/to/rfic-transformer-inverse-design
  OUT_DIR=/path/to/import_output
  PYTHON=/path/to/python
  EXPECTED_COUNT=500
  EXPECTED_JOBS=8
  REQUIRE_HFSS_VALIDATION_ASSETS=1
USAGE
}

pick_python() {
  if [[ -n "${PYTHON:-}" && -x "${PYTHON}" ]]; then
    printf '%s\n' "${PYTHON}"
  elif [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$REPO_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    return 1
  fi
}

find_return_tarball() {
  if [[ $# -gt 0 && -n "${1:-}" ]]; then
    printf '%s\n' "$1"
    return 0
  fi

  for candidate in \
    "$PWD/next_gen_s8p_mars_return_latest.tar.gz" \
    "$SCRIPT_DIR/next_gen_s8p_mars_return_latest.tar.gz" \
    "$SCRIPT_DIR/returns/next_gen_s8p_mars_return_latest.tar.gz" \
    "$SCRIPT_DIR/mars_returns/next_gen_s8p_mars_return_latest.tar.gz" \
    "$SCRIPT_DIR/downloads/next_gen_s8p_mars_return_latest.tar.gz"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  find "$PWD" "$SCRIPT_DIR" -maxdepth 4 -type f -name 'next_gen_s8p_mars_return*.tar.gz' 2>/dev/null | sort | tail -n 1
}

first_existing_sidecar() {
  for candidate in "$@"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$IMPORT_SCRIPT" ]]; then
  echo "ERROR: missing importer: $IMPORT_SCRIPT" >&2
  exit 3
fi

TARBALL="$(find_return_tarball "${1:-}")"
if [[ -z "$TARBALL" || ! -f "$TARBALL" ]]; then
  echo "ERROR: could not locate next_gen_s8p_mars_return*.tar.gz" >&2
  usage >&2
  exit 2
fi
TARBALL="$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL")"
TARBALL_DIR="$(dirname "$TARBALL")"
TARBALL_BASE="$(basename "$TARBALL")"

SHA_FILE="$(first_existing_sidecar \
  "$TARBALL.sha256" \
  "$TARBALL_DIR/${TARBALL_BASE}.sha256" \
  "$TARBALL_DIR/next_gen_s8p_mars_return_latest.tar.gz.sha256" || true)"
INVENTORY_JSON="$(first_existing_sidecar \
  "$TARBALL.inventory.json" \
  "$TARBALL_DIR/${TARBALL_BASE}.inventory.json" \
  "$TARBALL_DIR/next_gen_s8p_mars_return_latest.tar.gz.inventory.json" || true)"
INVENTORY_MD="$(first_existing_sidecar \
  "$TARBALL.inventory.md" \
  "$TARBALL_DIR/${TARBALL_BASE}.inventory.md" \
  "$TARBALL_DIR/next_gen_s8p_mars_return_latest.tar.gz.inventory.md" || true)"

PYTHON_CMD="$(pick_python)"
mkdir -p "$OUT_DIR"
LOG_PATH="$OUT_DIR/import_next_gen_s8p_mars_return.log"

COMMAND=(
  "$PYTHON_CMD"
  "$IMPORT_SCRIPT"
  "$TARBALL"
  "--out-dir" "$OUT_DIR"
  "--expected-count" "${EXPECTED_COUNT:-500}"
  "--expected-jobs" "${EXPECTED_JOBS:-8}"
  "--expected-ports" "${EXPECTED_PORTS:-8}"
  "--expected-frequency-start-ghz" "${EXPECTED_FREQUENCY_START_GHZ:-5.0}"
  "--expected-frequency-stop-ghz" "${EXPECTED_FREQUENCY_STOP_GHZ:-50.0}"
  "--expected-frequency-step-ghz" "${EXPECTED_FREQUENCY_STEP_GHZ:-0.1}"
  "--expected-frequency-points" "${EXPECTED_FREQUENCY_POINTS:-451}"
  "--max-touchstone-checks" "${MAX_TOUCHSTONE_CHECKS:-500}"
  "--max-touchstone-frequency-checks" "${MAX_TOUCHSTONE_FREQUENCY_CHECKS:-500}"
)

if [[ -n "$SHA_FILE" ]]; then
  COMMAND+=("--sha256-file" "$SHA_FILE")
fi
if [[ -n "$INVENTORY_JSON" ]]; then
  COMMAND+=("--inventory" "$INVENTORY_JSON")
fi
if [[ -n "$INVENTORY_MD" ]]; then
  COMMAND+=("--inventory-report" "$INVENTORY_MD")
fi
if [[ "${REQUIRE_HFSS_VALIDATION_ASSETS:-0}" == "1" ]]; then
  COMMAND+=("--require-hfss-validation-assets")
fi

echo "RETURN_TARBALL=$TARBALL"
echo "RETURN_SHA_FILE=${SHA_FILE:-missing; importer will use default if available}"
echo "RETURN_INVENTORY_JSON=${INVENTORY_JSON:-missing}"
echo "RETURN_INVENTORY_MD=${INVENTORY_MD:-missing}"
echo "IMPORT_OUT_DIR=$OUT_DIR"
echo "IMPORT_LOG=$LOG_PATH"

set +e
"${COMMAND[@]}" 2>&1 | tee "$LOG_PATH"
IMPORT_RC="${PIPESTATUS[0]}"
set -e

SUMMARY_PATH="$OUT_DIR/next_gen_s8p_mars_return_import_summary.json"
REPORT_PATH="$OUT_DIR/NEXT_GEN_S8P_MARS_RETURN_IMPORT_REPORT.md"
echo "IMPORT_EXIT_CODE=$IMPORT_RC"
echo "IMPORT_SUMMARY=$SUMMARY_PATH"
echo "IMPORT_REPORT=$REPORT_PATH"

if [[ -f "$SUMMARY_PATH" ]]; then
  "$PYTHON_CMD" - "$SUMMARY_PATH" <<'PY' || true
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print(f"IMPORT_OVERALL_STATUS={data.get('overall_status')}")
print(f"IMPORT_DECISION={data.get('decision')}")
next_steps = data.get("next_steps_result") or {}
print(f"NEXT_STEPS_GENERATED={next_steps.get('generated')}")
print(f"NEXT_STEPS_SCRIPT={next_steps.get('script_path')}")
print(f"NEXT_STEPS_REPORT={next_steps.get('report_path')}")
for check in data.get("checks", []):
    print(f"IMPORT_CHECK {check.get('status')} {check.get('name')}: {check.get('detail')}")
PY
fi

exit "$IMPORT_RC"
