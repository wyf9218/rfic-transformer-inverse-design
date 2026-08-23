#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/researcher/Documents/模拟变压器AI反向建模"
PACKET="$ROOT/reports/s8p_shared_line_width_mars_evidence_20260622/hfss_aedt_candidate_26cb45d70af3cfd0_pdkproc_v49_local_pxxxg_ref_s8p_contract/hfss_s8p_aedt_script_packet_summary.json"
OUT="$ROOT/reports/s8p_shared_line_width_mars_evidence_20260622/hfss_postrun_candidate_26cb45d70af3cfd0_pdkproc_v49_local_pxxxg_ref_s8p_contract"

python3 "$ROOT/rfic-transformer-inverse-design/scripts/run_s8p_hfss_postrun_validation_from_aedt_packet.py" \
  --aedt-packet-summary "$PACKET" \
  --out-dir "$OUT" \
  --compare-start-ghz 5 \
  --compare-stop-ghz 60 \
  --expected-frequency-step-ghz 0.5 \
  --expected-frequency-points 111 \
  --target-ghz 15 \
  --max-percent-error 10 \
  --require-all-pass
