#!/usr/bin/env bash
set -euo pipefail

# Upload/install the current 56-point grounded-tap S8P pilot on MARS and start
# the EMX run detached with nohup. This avoids losing a long EMX run if the SSH
# tunnel drops after launch. It does not know or submit a password.

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

echo "MARS_56PT_DETACHED_PREFLIGHT_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
echo "REMOTE_PROJECT=$REMOTE_PROJECT"
echo "PACKET=$PACKET"
echo "PACKET_SHA256=$PACKET_SHA"

ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$REMOTE" \
  "hostname && mkdir -p '$REMOTE_WORK_DIR' '$REMOTE_PROJECT' '$REMOTE_WORK_DIR/returns' && date"

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

mkdir -p "\$REMOTE_WORK_DIR/returns"
RUN_STAMP="\$(date +%Y%m%d_%H%M%S)"
RUN_LOG="\$REMOTE_WORK_DIR/returns/mars_s8p_56pt_grounded_tap_detached_\${RUN_STAMP}.log"
PID_FILE="\$REMOTE_WORK_DIR/returns/mars_s8p_56pt_grounded_tap_detached_latest.pid"
INFO_FILE="\$REMOTE_WORK_DIR/returns/mars_s8p_56pt_grounded_tap_detached_latest.info"

if [[ -f "\$PID_FILE" ]]; then
  OLD_PID="\$(cat "\$PID_FILE" 2>/dev/null || true)"
  if [[ -n "\$OLD_PID" ]] && kill -0 "\$OLD_PID" >/dev/null 2>&1; then
    echo "MARS_56PT_DETACHED_ALREADY_RUNNING pid=\$OLD_PID"
    echo "pid=\$OLD_PID"
    echo "log=\$(cat "\$INFO_FILE" 2>/dev/null | sed -n 's/^log=//p' | tail -1)"
    exit 0
  fi
fi

{
  echo "MARS_56PT_DETACHED_REMOTE_START \$(date)"
  echo "FREQUENCY_CONTRACT=5-60GHz inclusive, 1GHz step, 56 points"
  echo "TOUCHSTONE_CONTRACT=.s8p, 8 ports"
  echo "AC_GROUNDED_UNUSED_PORTS=P2,P3,P7,P8"
  echo "PROJECT=\$REMOTE_PROJECT"
  echo "LAUNCH_SCRIPT=\$LAUNCH_SCRIPT"
} > "\$RUN_LOG"

nohup bash "\$LAUNCH_SCRIPT" >> "\$RUN_LOG" 2>&1 &
PID="\$!"
printf "%s\n" "\$PID" > "\$PID_FILE"
{
  echo "pid=\$PID"
  echo "log=\$RUN_LOG"
  echo "started_at=\$(date -Iseconds)"
  echo "remote_project=\$REMOTE_PROJECT"
  echo "remote_work_dir=\$REMOTE_WORK_DIR"
} > "\$INFO_FILE"

echo "MARS_56PT_DETACHED_STARTED pid=\$PID"
echo "log=\$RUN_LOG"
echo "pid_file=\$PID_FILE"
echo "info_file=\$INFO_FILE"
REMOTE_SCRIPT

echo "MARS_56PT_DETACHED_LAUNCH_COMMAND_DONE $(date)"
echo "Expected return:"
echo "  $REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.tar.gz"
echo "  $REMOTE_WORK_DIR/returns/next_gen_s8p_56pt_grounded_tap_latest.inventory.json"
