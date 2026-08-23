#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
SYNC_DIR="$ROOT/mars56_grounded_s4p_sync_20260702"
TMP_ROOT="${TMP_ROOT:-$ROOT/tmp/verify_mars56_s4p_local_packages_20260702}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

rm -rf "$TMP_ROOT"
mkdir -p "$TMP_ROOT"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

verify_b64_package() {
  local label="$1"
  local dir="$2"
  local init_glob="$3"
  local sha_file_glob="$4"
  local b64_name="$5"
  local output_name="$6"

  local work="$TMP_ROOT/$label"
  mkdir -p "$work"
  log "VERIFY_START $label"

  local init_script
  init_script="$(find "$dir" -maxdepth 1 -type f -name "$init_glob" | sort | head -1)"
  if [[ -z "$init_script" ]]; then
    echo "Missing init script for $label in $dir" >&2
    return 2
  fi

  WORK_DIR="$work" bash "$init_script" >/dev/null

  local line_count=0
  while IFS= read -r line_script; do
    WORK_DIR="$work" bash "$line_script" >/dev/null
    line_count=$((line_count + 1))
  done < <(find "$dir" -maxdepth 1 -type f -name 'LINE_*_OF_*.sh' | sort)

  if [[ "$line_count" -le 0 ]]; then
    echo "No LINE chunks found for $label in $dir" >&2
    return 2
  fi

  "$PYTHON_BIN" - "$work/$b64_name" "$work/$output_name" <<'PY'
import base64
import sys
src, dst = sys.argv[1:3]
with open(src, "rb") as handle:
    payload = base64.b64decode(handle.read())
with open(dst, "wb") as handle:
    handle.write(payload)
PY

  local sha_file
  sha_file="$(find "$work" -maxdepth 1 -type f -name "$sha_file_glob" | sort | head -1)"
  if [[ -z "$sha_file" ]]; then
    echo "Missing sha file for $label in $work" >&2
    return 2
  fi

  (
    cd "$work"
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum -c "$(basename "$sha_file")"
    else
      shasum -a 256 -c "$(basename "$sha_file")"
    fi
  )

  log "VERIFY_PASS $label chunks=$line_count output=$work/$output_name"
}

verify_b64_package \
  "audit_hardening_patch" \
  "$SYNC_DIR/audit_hardening_patch_tiny_paste_900" \
  "00_INIT_AUDIT_PATCH_UPLOAD.sh" \
  "audit_hardening.patch.gz.sha256" \
  "audit_hardening.patch.gz.b64" \
  "audit_hardening.patch.gz"

verify_b64_package \
  "resume_existing_runner" \
  "$SYNC_DIR/resume_existing_project_run20_tiny_paste_900" \
  "00_INIT_RESUME_RUNNER_UPLOAD.sh" \
  "MARS56_RESUME_EXISTING_PROJECT_RUN20_20260702.sh.sha256" \
  "MARS56_RESUME_EXISTING_PROJECT_RUN20_20260702.sh.b64" \
  "MARS56_RESUME_EXISTING_PROJECT_RUN20_20260702.sh"

verify_b64_package \
  "universal_runner" \
  "$SYNC_DIR/universal_resume_or_install_run20_tiny_paste_900" \
  "00_INIT_UNIVERSAL_RUNNER_UPLOAD.sh" \
  "MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh.sha256" \
  "MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh.b64" \
  "MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh"

log "ALL_MARS56_S4P_LOCAL_PACKAGES_VERIFY_PASS"
