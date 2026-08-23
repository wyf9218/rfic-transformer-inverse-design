#!/usr/bin/env bash
set -euo pipefail

# Local verifier for the MARS 56-point grounded-tap S8P return package.
# Usage:
#   bash VERIFY_MARS_56PT_GROUNDED_TAP_RETURN_20260630.sh /path/to/next_gen_s8p_56pt_grounded_tap_latest.tar.gz

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/next_gen_s8p_56pt_grounded_tap_latest.tar.gz" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARBALL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
if [[ -z "${INVENTORY:-}" ]]; then
  INVENTORY="${TARBALL}.inventory.json"
  if [[ ! -f "$INVENTORY" && -f "${TARBALL%.tar.gz}.inventory.json" ]]; then
    INVENTORY="${TARBALL%.tar.gz}.inventory.json"
  fi
fi
if [[ -z "${INVENTORY_REPORT:-}" ]]; then
  INVENTORY_REPORT="${TARBALL}.inventory.md"
  if [[ ! -f "$INVENTORY_REPORT" && -f "${TARBALL%.tar.gz}.inventory.md" ]]; then
    INVENTORY_REPORT="${TARBALL%.tar.gz}.inventory.md"
  fi
fi
SHA256_FILE="${SHA256_FILE:-${TARBALL}.sha256}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/outputs/s8p_5_60_1ghz_grounded_tap_update_20260630/return_verify_${STAMP}}"

cd "$ROOT_DIR"

python3 rfic-transformer-inverse-design/scripts/verify_mars_dataset_package.py "$TARBALL" \
  --inventory "$INVENTORY" \
  --inventory-report "$INVENTORY_REPORT" \
  --sha256-file "$SHA256_FILE" \
  --out-dir "$OUT_DIR" \
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
  --require-run-config

echo "VERIFY_MARS_56PT_GROUNDED_TAP_RETURN_DONE"
echo "summary=$OUT_DIR/mars_dataset_package_verify_summary.json"
echo "report=$OUT_DIR/mars_dataset_package_verify_report.md"
