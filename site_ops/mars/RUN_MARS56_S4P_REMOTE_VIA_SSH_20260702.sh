#!/usr/bin/env bash
set -euo pipefail

# Start the real MARS56 grounded S4P 20-sample EMX run through SSH.
#
# This script does not store a password. SSH/Duo prompts stay interactive.
# It uploads the already locally verified universal runner and starts it
# detached on MARS, so the run can continue after the local terminal exits.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
REMOTE="${REMOTE:-researcher@mars.example.edu}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_mars56_grounded_s4p_20260702_ssh_runner}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702}"
REMOTE_OUT_ROOT="${REMOTE_OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_outputs_20260702}"
RUNNER="$ROOT/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh"
VERIFY_PACKAGES="$ROOT/VERIFY_MARS56_S4P_LOCAL_PACKAGES_20260702.sh"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-20}"
SSH_BATCH_MODE="${SSH_BATCH_MODE:-0}"
JOBS="${JOBS:-8}"
MAX_COUNT="${MAX_COUNT:-20}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"

SSH_ARGS=(-o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)
SCP_ARGS=(-o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)

if [[ "$SSH_BATCH_MODE" == "1" ]]; then
  SSH_ARGS+=(-o BatchMode=yes)
  SCP_ARGS+=(-o BatchMode=yes)
fi

if [[ -n "${SSH_PROXY_JUMP:-}" ]]; then
  SSH_ARGS+=(-J "$SSH_PROXY_JUMP")
  SCP_ARGS+=(-o "ProxyJump=$SSH_PROXY_JUMP")
fi

if [[ ! -f "$RUNNER" ]]; then
  echo "Missing runner: $RUNNER" >&2
  exit 2
fi

if [[ -x "$VERIFY_PACKAGES" ]]; then
  "$VERIFY_PACKAGES"
fi

echo "CODEX_MARS56_SSH_START $(date)"
echo "REMOTE=$REMOTE"
echo "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
echo "REMOTE_PROJECT=$REMOTE_PROJECT"
echo "REMOTE_OUT_ROOT=$REMOTE_OUT_ROOT"
echo "JOBS=$JOBS MAX_COUNT=$MAX_COUNT RUN_REAL_EMX=$RUN_REAL_EMX"

ssh "${SSH_ARGS[@]}" "$REMOTE" "mkdir -p '$REMOTE_WORK_DIR' '$REMOTE_OUT_ROOT'"
scp "${SCP_ARGS[@]}" "$RUNNER" "$REMOTE:$REMOTE_WORK_DIR/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh"

REMOTE_LOG="$REMOTE_OUT_ROOT/mars56_grounded_s4p_ssh_runner_${STAMP}.log"
ssh "${SSH_ARGS[@]}" "$REMOTE" \
  "chmod +x '$REMOTE_WORK_DIR/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh' && \
   cd '$REMOTE_WORK_DIR' && \
   nohup env PROJECT='$REMOTE_PROJECT' OUT_ROOT='$REMOTE_OUT_ROOT' WORK_DIR='$REMOTE_WORK_DIR/work' PYTHON_BIN=python3 JOBS='$JOBS' MAX_COUNT='$MAX_COUNT' RUN_REAL_EMX='$RUN_REAL_EMX' \
     '$REMOTE_WORK_DIR/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh' > '$REMOTE_LOG' 2>&1 < /dev/null & \
   echo CODEX_MARS56_REMOTE_PID=\$! && echo CODEX_MARS56_REMOTE_LOG='$REMOTE_LOG'"

echo "CODEX_MARS56_SSH_LAUNCHED"
echo "To monitor:"
echo "  ssh ${SSH_PROXY_JUMP:+-J $SSH_PROXY_JUMP }$REMOTE 'tail -f $REMOTE_LOG'"
