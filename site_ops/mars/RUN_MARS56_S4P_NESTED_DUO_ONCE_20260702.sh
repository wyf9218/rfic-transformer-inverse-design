#!/usr/bin/env bash
set -euo pipefail

# Launch the current MARS56 grounded S4P real EMX pilot through best-linux
# with only one jump-host login and one MARS login.
#
# This script stores no password and creates no fake data. It writes the
# locally verified universal runner to MARS, verifies its SHA256 there, and
# starts the real 20-sample EMX run detached.

ROOT="${ROOT:-/home/researcher/Documents/模拟变压器AI反向建模}"
JUMP="${JUMP:-researcher@login.example.edu}"
MARS="${MARS:-researcher@mars.example.edu}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/shared/research/researcher/codex_mars56_grounded_s4p_20260702_nested_runner}"
REMOTE_PROJECT="${REMOTE_PROJECT:-/shared/research/researcher/rfic-transformer-inverse-design-mars56-s4p-20260702}"
REMOTE_OUT_ROOT="${REMOTE_OUT_ROOT:-/shared/research/researcher/mars56_grounded_s4p_outputs_20260702}"
RUNNER="$ROOT/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh"
VERIFY_PACKAGES="$ROOT/VERIFY_MARS56_S4P_LOCAL_PACKAGES_20260702.sh"
JOBS="${JOBS:-8}"
MAX_COUNT="${MAX_COUNT:-20}"
RUN_REAL_EMX="${RUN_REAL_EMX:-1}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-30}"

if [[ ! -f "$RUNNER" ]]; then
  echo "Missing runner: $RUNNER" >&2
  exit 2
fi

if [[ -x "$VERIFY_PACKAGES" ]]; then
  "$VERIFY_PACKAGES"
fi

RUNNER_SHA="$(shasum -a 256 "$RUNNER" | awk '{print $1}')"
if [[ -z "$RUNNER_SHA" ]]; then
  echo "Failed to compute runner SHA256" >&2
  exit 2
fi

tmp_best_script="$(mktemp "${TMPDIR:-/tmp}/mars56_s4p_nested_best.XXXXXX.sh")"
cleanup() {
  rm -f "$tmp_best_script"
}
trap cleanup EXIT

{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' 'set -euo pipefail'
  printf 'echo CODEX_MARS56_NESTED_ON_JUMP "$(hostname)" "$(date)"\n'
  printf 'ssh -tt -o ConnectTimeout=%q -o StrictHostKeyChecking=accept-new %q bash -s <<'\''MARS_SCRIPT'\''\n' "$SSH_CONNECT_TIMEOUT" "$MARS"
  printf '%s\n' 'set -euo pipefail'
  printf 'REMOTE_WORK_DIR=%q\n' "$REMOTE_WORK_DIR"
  printf 'REMOTE_PROJECT=%q\n' "$REMOTE_PROJECT"
  printf 'REMOTE_OUT_ROOT=%q\n' "$REMOTE_OUT_ROOT"
  printf 'JOBS=%q\n' "$JOBS"
  printf 'MAX_COUNT=%q\n' "$MAX_COUNT"
  printf 'RUN_REAL_EMX=%q\n' "$RUN_REAL_EMX"
  printf 'STAMP=%q\n' "$STAMP"
  printf 'RUNNER_SHA=%q\n' "$RUNNER_SHA"
  cat <<'MARS_HEADER'
RUNNER_PATH="$REMOTE_WORK_DIR/MARS56_UNIVERSAL_RESUME_OR_INSTALL_RUN20_20260702.sh"
RUNNER_B64="$RUNNER_PATH.b64"
REMOTE_LOG="$REMOTE_OUT_ROOT/mars56_grounded_s4p_nested_runner_${STAMP}.log"
mkdir -p "$REMOTE_WORK_DIR" "$REMOTE_OUT_ROOT"
cat > "$RUNNER_B64" <<'RUNNER_B64_PAYLOAD'
MARS_HEADER
  base64 < "$RUNNER"
  cat <<'MARS_TAIL'
RUNNER_B64_PAYLOAD
if command -v base64 >/dev/null 2>&1; then
  if ! base64 -d "$RUNNER_B64" > "$RUNNER_PATH" 2>/dev/null; then
    base64 --decode "$RUNNER_B64" > "$RUNNER_PATH"
  fi
else
  python3 - "$RUNNER_B64" "$RUNNER_PATH" <<'PY'
import base64
import sys
from pathlib import Path
Path(sys.argv[2]).write_bytes(base64.b64decode(Path(sys.argv[1]).read_bytes()))
PY
fi
chmod +x "$RUNNER_PATH"
if command -v sha256sum >/dev/null 2>&1; then
  actual_sha="$(sha256sum "$RUNNER_PATH" | awk '{print $1}')"
else
  actual_sha="$(python3 - "$RUNNER_PATH" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
fi
echo "CODEX_MARS56_REMOTE_RUNNER_SHA=$actual_sha"
if [[ "$actual_sha" != "$RUNNER_SHA" ]]; then
  echo "CODEX_MARS56_REMOTE_RUNNER_SHA_MISMATCH expected=$RUNNER_SHA actual=$actual_sha" >&2
  exit 10
fi
cd "$REMOTE_WORK_DIR"
nohup env PROJECT="$REMOTE_PROJECT" OUT_ROOT="$REMOTE_OUT_ROOT" WORK_DIR="$REMOTE_WORK_DIR/work" PYTHON_BIN=python3 JOBS="$JOBS" MAX_COUNT="$MAX_COUNT" RUN_REAL_EMX="$RUN_REAL_EMX" \
  "$RUNNER_PATH" > "$REMOTE_LOG" 2>&1 < /dev/null &
remote_pid=$!
echo "CODEX_MARS56_REMOTE_PID=$remote_pid"
echo "CODEX_MARS56_REMOTE_LOG=$REMOTE_LOG"
echo "CODEX_MARS56_NESTED_LAUNCH_DONE"
MARS_SCRIPT
MARS_TAIL
} > "$tmp_best_script"

echo "CODEX_MARS56_NESTED_START $(date)"
echo "JUMP=$JUMP"
echo "MARS=$MARS"
echo "REMOTE_WORK_DIR=$REMOTE_WORK_DIR"
echo "REMOTE_PROJECT=$REMOTE_PROJECT"
echo "REMOTE_OUT_ROOT=$REMOTE_OUT_ROOT"
echo "JOBS=$JOBS MAX_COUNT=$MAX_COUNT RUN_REAL_EMX=$RUN_REAL_EMX"
echo "RUNNER_SHA=$RUNNER_SHA"
echo "This will prompt for jump-host login/Duo and then MARS login/Duo."

ssh -tt -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" -o StrictHostKeyChecking=accept-new "$JUMP" bash -s < "$tmp_best_script"

echo "CODEX_MARS56_NESTED_DONE"
