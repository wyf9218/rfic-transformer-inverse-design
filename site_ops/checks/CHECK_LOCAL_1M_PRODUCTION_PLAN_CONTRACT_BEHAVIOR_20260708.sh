#!/usr/bin/env bash
set -euo pipefail

# Local behavior test for the 1M production plan contract audit. It does not
# touch MARS. It verifies that the audit accepts a complete evidence index and
# rejects missing/weak 100k or cumulative checkpoint evidence.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
BUILD="$ROOT_DIR/rfic-transformer-inverse-design/scripts/build_mars56_1m_production_plan_contract.py"
AUDIT="$ROOT_DIR/rfic-transformer-inverse-design/scripts/audit_mars56_1m_production_plan_contract.py"

if [ ! -f "$BUILD" ] || [ ! -f "$AUDIT" ]; then
  echo "PRODUCTION_PLAN_CONTRACT_BEHAVIOR_STATUS=FAIL missing script" >&2
  exit 2
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mars56_plan_contract.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

python3 "$BUILD" --out-dir "$TMP_ROOT/contract" --base /remote/base --remote-project /remote/project >/dev/null
CONTRACT="$TMP_ROOT/contract/mars56_1m_production_plan_contract.json"

make_evidence() {
  local path="$1"
  local variant="$2"
  python3 - "$path" "$variant" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
variant = sys.argv[2]
formal = []
for idx in range(1, 11):
    tag = f"chunk_{idx:03d}_100k_after_chunk08_pass"
    formal.append(
        {
            "tag": tag,
            "nonempty_s4p_count": 100000,
            "dataset_summary_status": "PASS",
            "checkpoint_proof": "PASS",
            "evidence_status": "PASS",
            "missing_required_artifacts": [],
        }
    )
cumulative = []
for idx in range(1, 11):
    cumulative.append(
        {
            "tag": f"cumulative_{idx * 100:04d}k_after_chunk08_pass",
            "expected_count": idx * 100000,
            "checkpoint_proof": "PASS",
            "evidence_status": "PASS",
            "missing_required_artifacts": [],
        }
    )
if variant == "missing_formal_chunk":
    formal = formal[:-1]
elif variant == "low_formal_rows":
    formal[3]["nonempty_s4p_count"] = 99999
elif variant == "formal_checkpoint_fail":
    formal[4]["checkpoint_proof"] = "FAIL"
elif variant == "missing_cumulative":
    cumulative = cumulative[:-1]
elif variant == "cumulative_checkpoint_fail":
    cumulative[6]["checkpoint_proof"] = "FAIL"
elif variant != "complete":
    raise SystemExit(f"unknown variant: {variant}")

data = {
    "overall_status": "PASS" if variant == "complete" else "IN_PROGRESS",
    "expected_per_chunk": 100000,
    "expected_chunks": 10,
    "formal_100k_dataset_count": len(formal),
    "formal_100k_complete_count": sum(1 for item in formal if item["nonempty_s4p_count"] >= 100000),
    "formal_100k_evidence_pass_count": sum(1 for item in formal if item["evidence_status"] == "PASS" and item["checkpoint_proof"] == "PASS"),
    "cumulative_evidence_count": len(cumulative),
    "cumulative_evidence_pass_count": sum(1 for item in cumulative if item["evidence_status"] == "PASS" and item["checkpoint_proof"] == "PASS"),
    "formal_100k": formal,
    "cumulative": cumulative,
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
}

run_case() {
  local name="$1"
  local variant="$2"
  local expected_status="$3"
  local expected_reason="${4:-}"
  local evidence="$TMP_ROOT/$name/evidence.json"
  local out_dir="$TMP_ROOT/$name/audit"
  make_evidence "$evidence" "$variant"
  python3 "$AUDIT" \
    --contract-json "$CONTRACT" \
    --evidence-index-json "$evidence" \
    --out-dir "$out_dir" \
    --no-fail-exit >"$TMP_ROOT/$name/run.out"
  python3 - "$out_dir/mars56_1m_production_plan_contract_audit_summary.json" "$expected_status" "$expected_reason" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
reason = sys.argv[3]
assert summary["overall_status"] == expected, summary
if reason:
    haystack = json.dumps(summary, ensure_ascii=False)
    assert reason in haystack, haystack
PY
  echo "PRODUCTION_PLAN_CONTRACT_CASE=$name status=PASS"
}

run_case complete complete PASS
run_case missing_formal_chunk missing_formal_chunk FAIL missing_formal_chunk_in_evidence
run_case low_formal_rows low_formal_rows FAIL nonempty_s4p_count=99999
run_case formal_checkpoint_fail formal_checkpoint_fail FAIL "checkpoint_proof='FAIL'"
run_case missing_cumulative missing_cumulative FAIL missing_cumulative_checkpoint_in_evidence
run_case cumulative_checkpoint_fail cumulative_checkpoint_fail FAIL "checkpoint_proof='FAIL'"

make_evidence "$TMP_ROOT/strict_contract/evidence.json" complete
for variant in low_four_d_entropy high_four_d_imbalance; do
  weak_contract="$TMP_ROOT/strict_contract/${variant}.json"
  python3 - "$CONTRACT" "$weak_contract" "$variant" <<'PY'
import json
import sys
from pathlib import Path

source, target, variant = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
data = json.loads(source.read_text(encoding="utf-8"))
checkpoint = data["physical_feature_checkpoint_contract"]
if variant == "low_four_d_entropy":
    checkpoint["min_four_d_normalized_entropy"] = 0.79
elif variant == "high_four_d_imbalance":
    checkpoint["max_four_d_nonzero_bin_imbalance"] = 5.0
else:
    raise SystemExit(variant)
target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  out_dir="$TMP_ROOT/strict_contract/${variant}_audit"
  python3 "$AUDIT" \
    --contract-json "$weak_contract" \
    --evidence-index-json "$TMP_ROOT/strict_contract/evidence.json" \
    --out-dir "$out_dir" --no-fail-exit >/dev/null
  python3 - "$out_dir/mars56_1m_production_plan_contract_audit_summary.json" "$variant" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
variant = sys.argv[2]
assert summary["overall_status"] == "FAIL", summary
failed = {item["name"] for item in summary["checks"] if not item["pass"]}
expected = {
    "low_four_d_entropy": "checkpoint_contract_min_four_d_normalized_entropy_not_weakened",
    "high_four_d_imbalance": "checkpoint_contract_max_four_d_nonzero_bin_imbalance_not_weakened",
}[variant]
assert expected in failed, failed
PY
  echo "PRODUCTION_PLAN_CONTRACT_CASE=$variant status=PASS"
done

echo "PRODUCTION_PLAN_CONTRACT_BEHAVIOR_STATUS=PASS"
