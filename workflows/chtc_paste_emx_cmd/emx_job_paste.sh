#!/bin/bash

# Edit only these fields:
OUT_DIR="emx_paste_cmd"
ZIP_FILE="example_emx_work.zip"
PROC_FILE="example.proc"
ORIGINAL_WORK_DIR="/original/path/example.work"
ORIGINAL_PROC_FILE="/original/path/example.proc"

# Paste the exact EMX command here. Keep it as one command, but line breaks are fine.
EMX_CMD_RAW=$(cat <<'EOF'
emx /original/path/example.work/example.gds EXAMPLE_TOP /original/path/example.proc --sweep 0 5e+10 --sweep-stepsize 1e+09 --format=touchstone2 -s /original/path/example.work/example.s%dp
EOF
)
