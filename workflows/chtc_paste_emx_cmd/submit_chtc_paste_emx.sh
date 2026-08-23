#!/bin/bash
set -euo pipefail
mkdir -p logs
condor_submit chtc_paste_emx.sub
