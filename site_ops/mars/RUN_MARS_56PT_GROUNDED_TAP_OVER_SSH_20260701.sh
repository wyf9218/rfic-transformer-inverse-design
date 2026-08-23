#!/usr/bin/env bash
set -euo pipefail

# Upload and launch the current 56-point grounded-tap S8P pilot when SSH works.
# This script does not know or submit a password. It only runs after the SSH
# session is already reachable through keys/agent/interactive terminal state.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${REMOTE_HOST:-mars.example.edu}"
REMOTE_USER="${REMOTE_USER:-researcher}"
REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_next_gen_s8p_ssh_20260620}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design}"
PACKET_NAME="next_gen_s8p_mars_sync_packet_20260630_5_60_1p0_grounded_tap_20pilot_minimal.tar.gz"
PACKET="$ROOT_DIR/$PACKET_NAME"
PACKET_SHA="$(shasum -a 256 "$PACKET" | awk '{print $1}')"
SYNC_DIR="next_gen_s8p_mars_sync_packet_20260630_5_60_1p0_grounded_tap_20pilot_minimal"
LAUNCH_SCRIPT="MARS_S8P_56PT_GROUNDED_TAP_20_PILOT_20260630.sh"

if [[ ! -f "$PACKET" ]]; then
  echo "Missing local packet: $PACKET" >&2
  exit 2
fi

echo "MARS_56PT_SSH_PREFLIGHT_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
echo "REMOTE_PROJECT=$REMOTE_PROJECT"
echo "PACKET=$PACKET"
echo "PACKET_SHA256=$PACKET_SHA"

ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$REMOTE" \
  "hostname && mkdir -p '$REMOTE_WORK_DIR' '$REMOTE_PROJECT' && date"

scp -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new \
  "$PACKET" "$REMOTE:$REMOTE_WORK_DIR/$PACKET_NAME"

ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new "$REMOTE" /bin/bash <<REMOTE_SCRIPT
set -euo pipefail
REMOTE_WORK_DIR="$REMOTE_WORK_DIR"
REMOTE_PROJECT="$REMOTE_PROJECT"
PACKET_NAME="$PACKET_NAME"
EXPECTED_SHA="$PACKET_SHA"
SYNC_DIR="$SYNC_DIR"
LAUNCH_SCRIPT="$LAUNCH_SCRIPT"

cd "\$REMOTE_WORK_DIR"
if command -v sha256sum >/dev/null 2>&1; then
  printf "%s  %s\n" "\$EXPECTED_SHA" "\$PACKET_NAME" | sha256sum -c -
else
  printf "%s  %s\n" "\$EXPECTED_SHA" "\$PACKET_NAME" | shasum -a 256 -c -
fi

rm -rf "\$SYNC_DIR"
tar -xzf "\$PACKET_NAME"
if command -v sha256sum >/dev/null 2>&1; then
  (cd "\$SYNC_DIR" && sha256sum -c SHA256SUMS.txt)
else
  (cd "\$SYNC_DIR" && shasum -a 256 -c SHA256SUMS.txt)
fi

PROJECT="\$REMOTE_PROJECT" bash "\$SYNC_DIR/INSTALL_ON_MARS.sh"
cd "\$REMOTE_PROJECT"
chmod +x "\$LAUNCH_SCRIPT"
bash "\$LAUNCH_SCRIPT"
REMOTE_SCRIPT

echo "MARS_56PT_SSH_LAUNCH_DONE $(date)"
echo "Expected return:"
echo "  $REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"
echo "  $REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.inventory.json"
