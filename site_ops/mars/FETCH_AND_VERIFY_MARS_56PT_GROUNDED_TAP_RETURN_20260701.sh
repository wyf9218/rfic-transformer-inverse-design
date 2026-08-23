#!/usr/bin/env bash
set -euo pipefail

# Fetch the latest MARS 56-point grounded-tap S8P return package over SSH/SCP
# and run the local contract verifier.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${REMOTE_HOST:-mars.example.edu}"
REMOTE_USER="${REMOTE_USER:-researcher}"
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
REMOTE_RETURNS_DIR="${REMOTE_RETURNS_DIR:-$REMOTE_WORK_DIR/returns}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
LOCAL_DIR="${LOCAL_DIR:-$ROOT_DIR/outputs/s8p_5_60_1ghz_grounded_tap_update_20260630/mars_return_${STAMP}}"

mkdir -p "$LOCAL_DIR"

REMOTE_BASE="$REMOTE_RETURNS_DIR/next_gen_s8p_56pt_grounded_tap_latest"
LOCAL_TARBALL="$LOCAL_DIR/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"

echo "MARS_56PT_FETCH_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_RETURNS_DIR=$REMOTE_RETURNS_DIR"
echo "LOCAL_DIR=$LOCAL_DIR"

ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$REMOTE" /bin/bash <<REMOTE_SCRIPT
set -euo pipefail
REMOTE_BASE="$REMOTE_BASE"
for required in "\${REMOTE_BASE}.tar.gz" "\${REMOTE_BASE}.tar.gz.sha256" "\${REMOTE_BASE}.inventory.json" "\${REMOTE_BASE}.inventory.md"; do
  if [[ ! -f "\$required" ]]; then
    echo "Missing required return file: \$required" >&2
    exit 30
  fi
done
ls -lh "\${REMOTE_BASE}"*
REMOTE_SCRIPT

scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new \
  "$REMOTE:${REMOTE_BASE}.tar.gz" \
  "$REMOTE:${REMOTE_BASE}.tar.gz.sha256" \
  "$REMOTE:${REMOTE_BASE}.inventory.json" \
  "$REMOTE:${REMOTE_BASE}.inventory.md" \
  "$LOCAL_DIR/"

for optional in \
  "${REMOTE_BASE}_manifest.json" \
  "${REMOTE_BASE}_verify.log" \
  "${REMOTE_BASE}_verify_summary.json"; do
  scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new \
    "$REMOTE:$optional" "$LOCAL_DIR/" >/dev/null 2>&1 || true
done

cd "$ROOT_DIR"
INVENTORY="$LOCAL_DIR/next_gen_s8p_56pt_grounded_tap_latest.inventory.json" \
INVENTORY_REPORT="$LOCAL_DIR/next_gen_s8p_56pt_grounded_tap_latest.inventory.md" \
SHA256_FILE="$LOCAL_DIR/next_gen_s8p_56pt_grounded_tap_latest.tar.gz.sha256" \
OUT_DIR="$LOCAL_DIR/local_verify" \
  bash VERIFY_MARS_56PT_GROUNDED_TAP_RETURN_20260630.sh "$LOCAL_TARBALL"

echo "MARS_56PT_FETCH_AND_VERIFY_DONE $(date)"
echo "local_tarball=$LOCAL_TARBALL"
echo "local_verify_summary=$LOCAL_DIR/local_verify/mars_dataset_package_verify_summary.json"
echo "local_verify_report=$LOCAL_DIR/local_verify/mars_dataset_package_verify_report.md"
