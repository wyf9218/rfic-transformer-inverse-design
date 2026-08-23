#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/home/researcher/researcher/transformer_inverse/rfic-transformer-inverse-design}"
PACKET="next_gen_s8p_mars_execution_packet_20260630_5_60_1p0_grounded_tap_20pilot"
LAUNCH="physical_feature_s8p_launch_packet_20_5_60_1p0_grounded_tap"
CONFIG="$REPO/$PACKET/02_final_s8p_config/final_s8p_physical_feature_500.yaml"
PROC="/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc"

cd "$REPO"
test -f "$CONFIG"
test -f "$PROC"

cp "$CONFIG" "$CONFIG.procfix_$(date +%Y%m%d_%H%M%S).bak"
sed -i "s#^  emx_process_file:.*#  emx_process_file: $PROC#" "$CONFIG"
grep -n "emx_process_file" "$CONFIG"

OUT="mars56_procfix_$(date +%Y%m%d_%H%M%S).out"
PIDFILE="mars56_procfix.pid"
nohup bash "$PACKET/$LAUNCH/physical_feature_s8p_launch.commands.sh" > "$OUT" 2>&1 &
echo "$!" > "$PIDFILE"
echo "PROC_FIX_LAUNCH_PID=$(cat "$PIDFILE")"
echo "PROC_FIX_LAUNCH_LOG=$REPO/$OUT"
