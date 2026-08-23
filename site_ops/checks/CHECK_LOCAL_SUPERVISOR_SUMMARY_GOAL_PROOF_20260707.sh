#!/usr/bin/env bash
set -euo pipefail

# Local-only behavioral test for SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh.
#
# It verifies that goal completion is proven only by a strict final audit log:
#   - ONE_MILLION_GOAL_STATUS=PASS
#   - ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW
#   - formal_chunk_pass_count=10
#   - cumulative_pass_count=10
#   - total_nonempty_formal_s4p>=1000000
#   - evidence_index_status=PASS
#   - evidence formal/cumulative pass counts>=10
#   - evidence_index_audit_result=PASS
#   - production_plan_contract_audit_result=PASS
#   - failure_count=0
#
# A weak log with only ONE_MILLION_GOAL_STATUS=PASS must not prove completion.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SUMMARY_SCRIPT="$ROOT_DIR/SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh"

if [ ! -f "$SUMMARY_SCRIPT" ]; then
  echo "ERROR: missing summary script: $SUMMARY_SCRIPT" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mars56_summary_goal_proof.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

python3 - "$TMP_DIR" <<'PY'
from pathlib import Path
import sys

tmp = Path(sys.argv[1])

formal_lines = []
for idx in range(1, 11):
    tag = f"chunk_{idx:03d}_100k_after_chunk08_pass"
    formal_lines.append(
        f"FORMAL_CHUNK index={idx} tag={tag} state=PASS exists=1 nonempty=100000 dataset_status=PASS checkpoint_status=PASS checkpoint_proof=PASS dataset=/remote/{tag}"
    )

cumulative_lines = []
for idx in range(1, 11):
    expected = idx * 100000
    tag = f"cumulative_{idx * 100:04d}k_after_chunk08_pass"
    cumulative_lines.append(
        f"CUMULATIVE_PREFIX index={idx} tag={tag} expected_rows={expected} state=PASS checkpoint_status=PASS checkpoint_proof=PASS"
    )

(tmp / "valid_final_audit.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=audit",
            "========== ONE_MILLION_FINAL_GOAL_AUDIT ==========",
            *formal_lines,
            *cumulative_lines,
            "formal_chunk_pass_count=10",
            "cumulative_pass_count=10",
            "total_nonempty_formal_s4p=1000000",
            "evidence_index_json=/remote/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.json",
            "evidence_index_md=/remote/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.md",
            "evidence_index_status=PASS",
            "evidence_formal_100k_dataset_count=10",
            "evidence_formal_100k_evidence_pass_count=10",
            "evidence_cumulative_evidence_count=10",
            "evidence_cumulative_evidence_pass_count=10",
            "evidence_index_audit_result=PASS",
            "production_plan_contract_audit_summary=/tmp/mars56_1m_contract_audit/audit/mars56_1m_production_plan_contract_audit_summary.json",
            "production_plan_contract_audit_result=PASS",
            "failure_count=0",
            "ONE_MILLION_GOAL_STATUS=PASS",
            "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
            "SUPERVISOR_STATUS=AUDIT_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)

(tmp / "missing_contract_audit.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=audit",
            *formal_lines,
            *cumulative_lines,
            "formal_chunk_pass_count=10",
            "cumulative_pass_count=10",
            "total_nonempty_formal_s4p=1000000",
            "evidence_index_json=/remote/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.json",
            "evidence_index_md=/remote/status/100k_checkpoint_evidence_index_20260707/mars56_100k_checkpoint_evidence_index.md",
            "evidence_index_status=PASS",
            "evidence_formal_100k_dataset_count=10",
            "evidence_formal_100k_evidence_pass_count=10",
            "evidence_cumulative_evidence_count=10",
            "evidence_cumulative_evidence_pass_count=10",
            "evidence_index_audit_result=PASS",
            "failure_count=0",
            "ONE_MILLION_GOAL_STATUS=PASS",
            "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
            "SUPERVISOR_STATUS=AUDIT_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)

(tmp / "missing_evidence_index.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=audit",
            *formal_lines,
            *cumulative_lines,
            "formal_chunk_pass_count=10",
            "cumulative_pass_count=10",
            "total_nonempty_formal_s4p=1000000",
            "failure_count=0",
            "ONE_MILLION_GOAL_STATUS=PASS",
            "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
            "SUPERVISOR_STATUS=AUDIT_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)

(tmp / "weak_status_only.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=audit",
            "ONE_MILLION_GOAL_STATUS=PASS",
            "SUPERVISOR_STATUS=AUDIT_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)

(tmp / "bad_counts.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=audit",
            "formal_chunk_pass_count=9",
            "cumulative_pass_count=10",
            "total_nonempty_formal_s4p=1000000",
            "failure_count=0",
            "ONE_MILLION_GOAL_STATUS=PASS",
            "ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW",
            "SUPERVISOR_STATUS=AUDIT_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)

(tmp / "later_preflight.log").write_text(
    "\n".join(
        [
            "MARS56 post-Duo supervisor",
            "mode=preflight",
            "CHECKPOINT_PROOF_CONSISTENCY_STATUS=PASS",
            "LOCAL_GOAL_READINESS_STATUS=PASS",
            "GOAL_COMPLETION_STATUS=NOT_PROVEN_LOCAL_ONLY",
            "SUPERVISOR_STATUS=PREFLIGHT_ONLY_DONE",
        ]
    )
    + "\n",
    encoding="utf-8",
)
PY

run_case() {
  local case_name="$1"
  local expected_ever="$2"
  local expected_latest="$3"
  shift 3
  local out="$TMP_DIR/${case_name}.json"
  OUT="$out" bash "$SUMMARY_SCRIPT" "$@" >/dev/null
  python3 - "$out" "$case_name" "$expected_ever" "$expected_latest" <<'PY'
import json
import sys

path, case_name, expected_ever, expected_latest = sys.argv[1:5]
data = json.load(open(path))
actual_ever = str(bool(data.get("goal_completion_ever_proven")))
actual_latest = str(bool(data.get("latest_goal_completion_proven")))
if actual_ever != expected_ever or actual_latest != expected_latest:
    print(
        f"SUMMARY_GOAL_PROOF_CASE={case_name} status=FAIL "
        f"expected_ever={expected_ever} actual_ever={actual_ever} "
        f"expected_latest={expected_latest} actual_latest={actual_latest}"
    )
    raise SystemExit(1)
print(
    f"SUMMARY_GOAL_PROOF_CASE={case_name} status=PASS "
    f"ever={actual_ever} latest={actual_latest} "
    f"latest_proven_log={data.get('latest_goal_completion_proven_log')}"
)
PY
}

run_case weak_status_only False False "$TMP_DIR/weak_status_only.log"
run_case bad_counts False False "$TMP_DIR/bad_counts.log"
run_case missing_evidence_index False False "$TMP_DIR/missing_evidence_index.log"
run_case missing_contract_audit False False "$TMP_DIR/missing_contract_audit.log"
run_case valid_final_audit True True "$TMP_DIR/valid_final_audit.log"
run_case valid_then_later_preflight True False "$TMP_DIR/valid_final_audit.log" "$TMP_DIR/later_preflight.log"

echo "SUMMARY_GOAL_PROOF_STATUS=PASS"
