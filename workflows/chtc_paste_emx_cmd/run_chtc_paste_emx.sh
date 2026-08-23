#!/bin/bash
set -euo pipefail

JOB_CONFIG="${1:-emx_job_paste.sh}"

if [ ! -f "$JOB_CONFIG" ]; then
  echo "ERROR: missing job config: $JOB_CONFIG" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$JOB_CONFIG"

: "${ZIP_FILE:?set ZIP_FILE in $JOB_CONFIG}"
: "${PROC_FILE:?set PROC_FILE in $JOB_CONFIG}"
: "${ORIGINAL_WORK_DIR:?set ORIGINAL_WORK_DIR in $JOB_CONFIG}"
: "${ORIGINAL_PROC_FILE:?set ORIGINAL_PROC_FILE in $JOB_CONFIG}"
: "${EMX_CMD_RAW:?set EMX_CMD_RAW in $JOB_CONFIG}"

OUT_DIR="${OUT_DIR:-emx_paste_cmd}"
UNPACK_DIR="${OUT_DIR}/unzipped"
INPUT_DIR="${OUT_DIR}/input_files"
STDOUT_LOG="${OUT_DIR}/emx_stdout.log"
STDERR_LOG="${OUT_DIR}/emx_stderr.log"
TIME_LOG="${OUT_DIR}/timing.txt"
COMMAND_FILE="${OUT_DIR}/emx_command.txt"

mkdir -p "$OUT_DIR" "$UNPACK_DIR" "$INPUT_DIR"

if [ -f "/software/chtc/modules/conda/setup_env.sh" ]; then
  source /software/chtc/modules/conda/setup_env.sh
fi

if [ -n "${CADENCE_LICENSE_FILE:-}" ]; then
  export LM_LICENSE_FILE="$CADENCE_LICENSE_FILE"
  export CDS_LIC_FILE="$CADENCE_LICENSE_FILE"
fi
if [ -n "${CADENCE_CDSLMD_LICENSE_FILE:-}" ]; then
  export CDSLMD_LICENSE_FILE="$CADENCE_CDSLMD_LICENSE_FILE"
elif [ -n "${CADENCE_LICENSE_FILE:-}" ]; then
  export CDSLMD_LICENSE_FILE="$CADENCE_LICENSE_FILE"
fi
if [ -n "${CADENCE_BIN_DIR:-}" ]; then
  export PATH="${CADENCE_BIN_DIR}:$PATH"
fi
if [ -n "${CADENCE_INSTALL_ROOT:-}" ]; then
  export CADENCE_PATH="$CADENCE_INSTALL_ROOT"
fi
export CDS_SKIP_OS_CHECK_ON_STARTUP="1"

if ! command -v emx >/dev/null 2>&1; then
  echo "ERROR: emx not found on PATH after CHTC cadence setup" >&2
  exit 2
fi

cp "$JOB_CONFIG" "$INPUT_DIR/"
cp "$ZIP_FILE" "$INPUT_DIR/"
cp "$PROC_FILE" "$INPUT_DIR/"

unzip -q "$ZIP_FILE" -d "$UNPACK_DIR"
WORK_DIR="$(find "$UNPACK_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "${WORK_DIR:-}" ] || [ ! -d "$WORK_DIR" ]; then
  echo "ERROR: could not locate extracted work directory" >&2
  exit 1
fi

LOCAL_PROC="${INPUT_DIR}/$(basename "$PROC_FILE")"

{
  echo "=============================================="
  echo "CHTC pasted-command EMX job"
  echo "Date: $(date)"
  echo "Host: $(hostname)"
  echo "PWD: $(pwd)"
  echo "Config: $JOB_CONFIG"
  echo "Zip: $ZIP_FILE"
  echo "Proc file: $LOCAL_PROC"
  echo "Original work dir: $ORIGINAL_WORK_DIR"
  echo "Local work dir: $WORK_DIR"
  echo "EMX: $(command -v emx)"
  echo "=============================================="
} | tee "${OUT_DIR}/job_header.txt"

start_ts="$(date -Iseconds)"
start_epoch="$(date +%s)"
export STDOUT_LOG STDERR_LOG EMX_CMD_RAW
set +e
python3 - "$WORK_DIR" "$LOCAL_PROC" "$ORIGINAL_WORK_DIR" "$ORIGINAL_PROC_FILE" "$COMMAND_FILE" <<'PY'
import os
import pathlib
import shlex
import subprocess
import sys

work_dir = pathlib.Path(sys.argv[1])
local_proc = sys.argv[2]
original_work_dir = sys.argv[3]
original_proc = sys.argv[4]
command_file = pathlib.Path(sys.argv[5])
raw = os.environ["EMX_CMD_RAW"]

tokens = shlex.split(raw)
rewritten = []
for token in tokens:
    if token == "emx" or token.endswith("/emx"):
        rewritten.append("emx")
        continue
    replaced = token.replace(original_work_dir, str(work_dir))
    replaced = replaced.replace(original_proc, local_proc)
    rewritten.append(replaced)

command_file.write_text(" ".join(shlex.quote(tok) for tok in rewritten) + "\n", encoding="utf-8")

stdout_path = pathlib.Path(os.environ["STDOUT_LOG"])
stderr_path = pathlib.Path(os.environ["STDERR_LOG"])

with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
    proc = subprocess.run(rewritten, stdout=stdout, stderr=stderr, text=True)
    raise SystemExit(proc.returncode)
PY
status=$?
set -e
end_ts="$(date -Iseconds)"
end_epoch="$(date +%s)"

{
  echo "start=${start_ts}"
  echo "end=${end_ts}"
  echo "wall_time_sec=$((end_epoch - start_epoch))"
  echo "exit_code=${status}"
} >"$TIME_LOG"

find "$OUT_DIR" -maxdepth 3 -type f | sort
exit "$status"
