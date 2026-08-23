#!/usr/bin/env bash
set -euo pipefail

# Local-only post-return processor for the next-gen S8P MARS return package.
# It verifies/imports the downloaded MARS return tarball, then runs the
# generated local next-steps script:
# next_gen_s8p_after_import_next_steps.commands.sh.
# That script builds physical-feature tables, inverse-model evidence,
# selected-sample HFSS handoff assets, postrun validation checks, and final
# report evidence manifests. This wrapper does not run EMX, HFSS, ADS, Cadence,
# or any GUI.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPORT_SCRIPT="${IMPORT_SCRIPT:-$SCRIPT_DIR/NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh}"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/outputs/next_gen_s8p_mars_return_import_current}"
RUN_NEXT_STEPS="${RUN_NEXT_STEPS:-1}"

usage() {
  cat <<'USAGE'
Usage:
  bash NEXT_GEN_S8P_AFTER_MARS_RETURN_AUTOPROCESS_20260620.sh /path/to/next_gen_s8p_mars_return_latest.tar.gz

If the tarball path is omitted, NEXT_GEN_S8P_IMPORT_MARS_RETURN_20260620.sh
searches the current/project directory for next_gen_s8p_mars_return*.tar.gz.

Optional environment variables:
  OUT_DIR=/path/to/import_output
  RUN_NEXT_STEPS=0        Import only; do not run generated local next-steps.
  TARGET_JSON=/path.json  Optional Lp/Ls/Q/K target for inverse prediction smoke.
  REQUIRE_HFSS_VALIDATION_ASSETS=1
USAGE
}

pick_python() {
  if [[ -n "${PYTHON:-}" && -x "${PYTHON}" ]]; then
    printf '%s\n' "${PYTHON}"
  elif [[ -x "$SCRIPT_DIR/rfic-transformer-inverse-design/.venv/bin/python" ]]; then
    printf '%s\n' "$SCRIPT_DIR/rfic-transformer-inverse-design/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    return 1
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -x "$IMPORT_SCRIPT" ]]; then
  echo "ERROR: missing executable importer: $IMPORT_SCRIPT" >&2
  exit 3
fi

mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$OUT_DIR/autoprocess_logs"
mkdir -p "$LOG_DIR"
IMPORT_LOG="$LOG_DIR/import_${STAMP}.log"
NEXT_LOG="$LOG_DIR/after_import_next_steps_${STAMP}.log"

echo "AFTER_RETURN_AUTOPROCESS_START $(date)"
echo "IMPORT_SCRIPT=$IMPORT_SCRIPT"
echo "OUT_DIR=$OUT_DIR"
echo "RUN_NEXT_STEPS=$RUN_NEXT_STEPS"
echo "IMPORT_LOG=$IMPORT_LOG"
echo "NEXT_LOG=$NEXT_LOG"

set +e
OUT_DIR="$OUT_DIR" bash "$IMPORT_SCRIPT" "${1:-}" 2>&1 | tee "$IMPORT_LOG"
IMPORT_RC="${PIPESTATUS[0]}"
set -e
echo "IMPORT_EXIT_CODE=$IMPORT_RC"
if [[ "$IMPORT_RC" != "0" ]]; then
  echo "ERROR: import failed; not running after-import next steps." >&2
  exit "$IMPORT_RC"
fi

SUMMARY_PATH="$OUT_DIR/next_gen_s8p_mars_return_import_summary.json"
if [[ ! -f "$SUMMARY_PATH" ]]; then
  echo "ERROR: import summary missing: $SUMMARY_PATH" >&2
  exit 4
fi

PYTHON_CMD="$(pick_python)"
NEXT_STEPS_SCRIPT="$("$PYTHON_CMD" - "$SUMMARY_PATH" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
next_steps = summary.get("next_steps_result") or {}
print(next_steps.get("script_path") or "")
PY
)"

echo "IMPORT_SUMMARY=$SUMMARY_PATH"
echo "NEXT_STEPS_SCRIPT=${NEXT_STEPS_SCRIPT:-missing}"

if [[ "$RUN_NEXT_STEPS" != "1" ]]; then
  echo "RUN_NEXT_STEPS is not 1; stopping after verified import."
  exit 0
fi

if [[ -z "$NEXT_STEPS_SCRIPT" || ! -x "$NEXT_STEPS_SCRIPT" ]]; then
  echo "ERROR: generated next-steps script missing or not executable: ${NEXT_STEPS_SCRIPT:-missing}" >&2
  exit 5
fi

set +e
bash "$NEXT_STEPS_SCRIPT" 2>&1 | tee "$NEXT_LOG"
NEXT_RC="${PIPESTATUS[0]}"
set -e
echo "NEXT_STEPS_EXIT_CODE=$NEXT_RC"
echo "AFTER_RETURN_AUTOPROCESS_DONE $(date)"
exit "$NEXT_RC"
