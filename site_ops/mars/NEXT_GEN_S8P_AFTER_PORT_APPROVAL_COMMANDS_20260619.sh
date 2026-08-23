#!/usr/bin/env bash
set -euo pipefail

# Safety guard: this script records approval and regenerates local launch,
# readiness, and strict-path MARS execution packets only after explicit
# user/advisor confirmation of both the port map and the geometry contract.
# It does not execute the generated 500-sample EMX command file.
if [[ "${CONFIRM_S8P_PORT_MAP_APPROVED:-}" != "YES" || "${CONFIRM_S8P_GEOMETRY_CONTRACT_APPROVED:-}" != "YES" ]]; then
  cat >&2 <<'MSG'
Refusing to mark the S8P port map / geometry contract as approved.

Only run this after user/advisor approval of:
  Pair 1 / primary / Lp: P001-P004, syntax 1,4
  Pair 2 / secondary / Ls: P005-P006, syntax 5,6
  Full syntax: 1,4:5,6
  Geometry: bridge width = vertical power-line width = 10um
  Geometry: literal 10nm / 0.01um bridge interpretation is rejected
  Geometry: vertical power-line length = 1.5 * max(primary_outer_height, secondary_outer_height)
  Geometry: M5 rectangular ground frame width = 100um

To proceed after approval:
  CONFIRM_S8P_PORT_MAP_APPROVED=YES CONFIRM_S8P_GEOMETRY_CONTRACT_APPROVED=YES bash NEXT_GEN_S8P_AFTER_PORT_APPROVAL_COMMANDS_20260619.sh
MSG
  exit 3
fi

ROOT="/home/researcher/Documents/模拟变压器AI反向建模"
REPO_ROOT="${ROOT}/rfic-transformer-inverse-design"
SOURCE_ROOT="${ROOT}/outputs/s8p_port_order_from_the_best_20260619"
APPROVED_ROOT="${ROOT}/outputs/s8p_port_order_from_the_best_20260619_approved_after_review"
GEOMETRY_AUDIT="${SOURCE_ROOT}/power_line_8port_contract_audit/power_line_8port_contract_audit_summary.json"
GEOMETRY_CHECKLIST="${ROOT}/S8P_GEOMETRY_CONTRACT_APPROVAL_CHECKLIST_20260619_CN.md"
APPROVED_GEOMETRY_ROOT="${ROOT}/outputs/s8p_geometry_contract_approval_20260619_approved_after_review"

CONFIG="${SOURCE_ROOT}/final_s8p_physical_feature_500_the_best_candidate.yaml"
SMOKE_EVAL="${SOURCE_ROOT}/layout_smoke_create_only/evaluations/c4ea3c929a89d3df"
POWER_LINE="${SMOKE_EVAL}/layout/power_line_8port_geometry.json"
LAYOUT_JSON="${SMOKE_EVAL}/layout/transformer_layout.layout.json"
PREVIEW="${SMOKE_EVAL}/layout/transformer_layout_preview.png"
PORT_DEBUG="${SMOKE_EVAL}/layout/transformer_port_debug.png"

APPROVED_LAUNCH_DIR="${APPROVED_ROOT}/physical_feature_s8p_launch_packet_gated_approved"
APPROVED_RUN_DIR="${APPROVED_ROOT}/new_s8p_physical_feature_emx_500_approved"
APPROVED_MARS_PACKET_DIR="${APPROVED_ROOT}/next_gen_s8p_mars_execution_packet_strict_path_preflight"
APPROVED_COMBINED_ROOT="${APPROVED_ROOT}/s8p_combined_approval_readiness_approved_after_review"
READINESS_DIR="${APPROVED_ROOT}/readiness_dryrun"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "No usable Python found." >&2
  exit 11
fi

mkdir -p "${APPROVED_ROOT}"
mkdir -p "${APPROVED_GEOMETRY_ROOT}"

echo "[1/5] Record approved S8P port map"
"${PYTHON}" "${REPO_ROOT}/scripts/build_s8p_port_map_approval_packet.py" \
  --power-line-geometry "${POWER_LINE}" \
  --layout-json "${LAYOUT_JSON}" \
  --preview-image "${PREVIEW}" \
  --port-debug-image "${PORT_DEBUG}" \
  --port-pairs "1,4:5,6" \
  --out-dir "${APPROVED_ROOT}" \
  --approved

APPROVAL_SUMMARY="${APPROVED_ROOT}/s8p_port_map_approval_summary.json"

echo "[2/5] Record approved S8P geometry contract"
"${PYTHON}" "${REPO_ROOT}/scripts/build_s8p_geometry_contract_approval_packet.py" \
  --contract-audit-summary "${GEOMETRY_AUDIT}" \
  --checklist "${GEOMETRY_CHECKLIST}" \
  --out-dir "${APPROVED_GEOMETRY_ROOT}" \
  --approved

GEOMETRY_APPROVAL_SUMMARY="${APPROVED_GEOMETRY_ROOT}/s8p_geometry_contract_approval_summary.json"

echo "[3/5] Regenerate approval-gated launch packet"
cd "${REPO_ROOT}"
"${PYTHON}" scripts/build_physical_feature_s8p_launch_packet.py \
  --bootstrap-geometry-candidate-queue \
  --bootstrap-sampler lhs_optimized \
  --bootstrap-seed 20260616 \
  --bootstrap-oversample-factor 4.0 \
  --bootstrap-max-sampling-rounds 20 \
  --config "${CONFIG}" \
  --out-dir "${APPROVED_LAUNCH_DIR}" \
  --run-dir "${APPROVED_RUN_DIR}" \
  --physical-feature-columns lp_nh_center,ls_nh_center,q_center,k_center \
  --scalar-q-definition min \
  --scalar-q-output-column q_center \
  --inverse-candidate-count 500 \
  --inverse-k-neighbors 8 \
  --jobs 8 \
  --expected-jobs 8 \
  --emx-max-count 500 \
  --expected-emx-count 500 \
  --batch-size 1 \
  --expected-bridge-width-um 10.0 \
  --bridge-width-tolerance-um 1e-12 \
  --expected-ground-frame-width-um 100.0 \
  --ground-frame-width-tolerance-um 1e-9 \
  --expected-ground-frame-policy power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame \
  --port-map-approval-summary "${APPROVAL_SUMMARY}" \
  --geometry-contract-approval-summary "${GEOMETRY_APPROVAL_SUMMARY}" \
  --s8p-touchstone-checks 50 \
  --validation-sample-count 1 \
  --validation-port-pairs "1,4:5,6" \
  --coverage-plan-bins 4 \
  --coverage-plan-next-count 100 \
  --no-package

echo "[4/5] Generate strict-path MARS execution packet"
"${PYTHON}" scripts/build_next_gen_s8p_mars_execution_packet.py \
  --out-dir "${APPROVED_MARS_PACKET_DIR}" \
  --bootstrap-geometry-candidate-queue \
  --port-map "P001,P002,P003,P004,P005,P006,P007,P008" \
  --role-labels "primary_top=P001,left_power_top=P002,left_power_bottom=P003,primary_bottom=P004,secondary_bottom=P005,secondary_top=P006,right_power_top=P007,right_power_bottom=P008" \
  --differential-port-pairs "1,4:5,6" \
  --port-map-approval-summary "${APPROVAL_SUMMARY}" \
  --geometry-contract-approval-summary "${GEOMETRY_APPROVAL_SUMMARY}" \
  --scalar-q-definition min \
  --primary-power-line-layer 74 \
  --secondary-power-line-layer 39 \
  --expected-sample-count 500 \
  --expected-jobs 8

echo "[5/6] Build combined approval readiness packet"
"${PYTHON}" scripts/build_s8p_combined_approval_readiness_packet.py \
  --port-map-approval-summary "${APPROVAL_SUMMARY}" \
  --geometry-contract-approval-summary "${GEOMETRY_APPROVAL_SUMMARY}" \
  --execution-packet-summary "${APPROVED_MARS_PACKET_DIR}/next_gen_s8p_mars_execution_packet_summary.json" \
  --out-dir "${APPROVED_COMBINED_ROOT}"

COMBINED_APPROVAL_SUMMARY="${APPROVED_COMBINED_ROOT}/s8p_combined_approval_readiness_summary.json"

echo "[6/6] Regenerate readiness dry-run"
"${PYTHON}" scripts/audit_next_gen_s8p_goal_readiness.py \
  --config "${CONFIG}" \
  --combined-approval-readiness-summary "${COMBINED_APPROVAL_SUMMARY}" \
  --launch-packet-summary "${APPROVED_LAUNCH_DIR}/physical_feature_s8p_launch_packet_summary.json" \
  --out-dir "${READINESS_DIR}" \
  --no-fail-exit

cat <<MSG

Approval packet and local launch/readiness files regenerated.

Approved summary:
  ${APPROVAL_SUMMARY}

Approved geometry summary:
  ${GEOMETRY_APPROVAL_SUMMARY}

Combined approval readiness:
  ${COMBINED_APPROVAL_SUMMARY}

Launch packet:
  ${APPROVED_LAUNCH_DIR}/physical_feature_s8p_launch_packet_summary.json

Strict-path MARS execution packet:
  ${APPROVED_MARS_PACKET_DIR}/next_gen_s8p_mars_execution_packet_summary.json

Readiness:
  ${READINESS_DIR}/next_gen_s8p_goal_readiness_summary.json

This script did NOT start EMX. Before running the generated command file on
MARS, confirm the config uses real MARS/Cadence/EMX paths rather than local
dry-run paths.
MSG
