#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="${WORK_DIR:-/shared/research/researcher/codex_mars56_grounded_s4p_20260702_minpatch}"
PATCH_GZ="${PATCH_GZ:-mars56_s4p_minimal.patch.gz}"
EXPECTED_B64_SIZE="${EXPECTED_B64_SIZE:-42140}"
EXPECTED_GZ_SIZE="${EXPECTED_GZ_SIZE:-31605}"
EXPECTED_SHA="${EXPECTED_SHA:-b41d3f5af1d07425e66a6bd8da0732ac97f9ceee50d1583281b7d5350f955e4a}"
CHUNK_SIZE="${CHUNK_SIZE:-900}"

echo "CODEX_MINPATCH_PARTIAL_CHECK_START $(date)"
echo "WORK_DIR=$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

if [[ ! -f "${PATCH_GZ}.b64" ]]; then
  echo "CODEX_MINPATCH_PARTIAL_STATUS=EMPTY"
  echo "NEXT_STEP=run 00_INIT_MINPATCH_UPLOAD.sh, then LINE_001_OF_047.sh"
  exit 0
fi

actual_b64_size="$(wc -c < "${PATCH_GZ}.b64")"
echo "CODEX_MINPATCH_B64_SIZE=$actual_b64_size/$EXPECTED_B64_SIZE"

if [[ "$actual_b64_size" -lt "$EXPECTED_B64_SIZE" ]]; then
  completed_full_chunks=$(( actual_b64_size / CHUNK_SIZE ))
  remainder=$(( actual_b64_size % CHUNK_SIZE ))
  next_line=$(( completed_full_chunks + 1 ))
  if [[ "$remainder" != "0" ]]; then
    echo "CODEX_MINPATCH_PARTIAL_STATUS=PARTIAL_CHUNK"
    echo "WARNING=Current b64 file ends inside a chunk; safest recovery is rerun 00_INIT and upload using 2-line rate-limited batches."
  else
    echo "CODEX_MINPATCH_PARTIAL_STATUS=INCOMPLETE"
    printf 'NEXT_LINE=LINE_%03d_OF_047.sh\n' "$next_line"
  fi
  exit 0
fi

if [[ "$actual_b64_size" -gt "$EXPECTED_B64_SIZE" ]]; then
  echo "CODEX_MINPATCH_PARTIAL_STATUS=TOO_LARGE"
  echo "WARNING=The b64 file is larger than expected; safest recovery is rerun 00_INIT and upload using 2-line rate-limited batches."
  exit 2
fi

base64 -d "${PATCH_GZ}.b64" > "$PATCH_GZ"
actual_gz_size="$(wc -c < "$PATCH_GZ")"
echo "CODEX_MINPATCH_GZ_SIZE=$actual_gz_size/$EXPECTED_GZ_SIZE"
test "$actual_gz_size" = "$EXPECTED_GZ_SIZE"
printf '%s  %s\n' "$EXPECTED_SHA" "$PATCH_GZ" > "${PATCH_GZ}.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "${PATCH_GZ}.sha256"
else
  shasum -a 256 -c "${PATCH_GZ}.sha256"
fi
echo "CODEX_MINPATCH_PARTIAL_STATUS=COMPLETE_VERIFY_PASS"
echo "NEXT_STEP=run 99_APPLY_MINPATCH_AND_PREFLIGHT_ON_MARS.sh"
