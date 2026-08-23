#!/usr/bin/env bash
set -euo pipefail

# Synthetic behavior regression for the post-Duo launch audit.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
AUDIT="$ROOT_DIR/CHECK_MARS56_POST_DUO_SYNC_START_LAUNCH_STATUS_20260708.sh"

if [ ! -f "$AUDIT" ]; then
  echo "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_STATUS=FAIL missing_audit=$AUDIT"
  exit 1
fi
bash -n "$AUDIT"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_case_json() {
  local case_dir="$1"
  local wrapper_state="${2:-PASS}"
  local detached_state="${3:-STARTED}"
  local detached_ssh_path="${4:-$case_dir/mux/%r@%h:%p}"
  local adaptive_emx="${5:-true}"
  local wrapper_dry_run="${6:-false}"
  mkdir -p "$case_dir"
  python3 - "$case_dir" "$wrapper_state" "$detached_state" "$detached_ssh_path" "$adaptive_emx" "$wrapper_dry_run" <<'PY'
import json
import sys
from pathlib import Path

case_dir = Path(sys.argv[1])
wrapper_state = sys.argv[2]
detached_state = sys.argv[3]
detached_ssh_path = sys.argv[4]
adaptive_emx = sys.argv[5].lower() == "true"
wrapper_dry_run = sys.argv[6].lower() == "true"
ssh_path = f"{case_dir}/mux/%r@%h:%p"

wrapper = {
    "updated_utc": "2026-07-08T00:00:00+00:00",
    "state": wrapper_state,
    "return_code": 0 if wrapper_state == "PASS" else 1,
    "log": str(case_dir / "wrapper.log"),
    "local_dry_run": wrapper_dry_run,
    "remote_actions_executed": not wrapper_dry_run,
    "ssh_control_path": ssh_path,
    "ssh_persist": "30m",
    "sync_remote_stack": True,
    "start_watcher": True,
    "watch_iterations": 0,
    "sleep_seconds": 1800,
    "run_adaptive_acquisition": True,
    "run_adaptive_emx": adaptive_emx,
    "run_evidence_index": True,
    "target_envelope_config": str(case_dir / "rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json"),
    "target_envelope_sha256": "a" * 64,
    "evidence_index_script": str(case_dir / "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"),
    "evidence_index_script_sha256": "b" * 64,
    "verify_sync_runner_script": str(case_dir / "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"),
    "verify_sync_runner_script_sha256": "c" * 64,
    "verify_sync_stack_script": str(case_dir / "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"),
    "verify_sync_stack_script_sha256": "d" * 64,
    "adaptive_round_script": str(case_dir / "rfic-transformer-inverse-design/scripts/run_mars56_s4p_adaptive_physical_acquisition_round.sh"),
    "adaptive_round_sha256": "e" * 64,
    "surrogate_predict_script": str(case_dir / "rfic-transformer-inverse-design/scripts/build_physical_feature_surrogate_candidate_predictions.py"),
    "surrogate_predict_sha256": "f" * 64,
    "model_checkpoint_script": str(case_dir / "rfic-transformer-inverse-design/scripts/run_accepted_physical_feature_model_checkpoint.sh"),
    "model_checkpoint_sha256": "1" * 64,
    "tandem_train_script": str(case_dir / "rfic-transformer-inverse-design/scripts/train_physical_feature_tandem_inverse.py"),
    "tandem_train_sha256": "2" * 64,
    "tandem_anchor_compare_script": str(case_dir / "rfic-transformer-inverse-design/scripts/compare_tandem_geometry_anchor_ablation.py"),
    "tandem_anchor_compare_sha256": "3" * 64,
    "tandem_feasibility_audit_script": str(case_dir / "rfic-transformer-inverse-design/scripts/audit_tandem_predicted_geometry_feasibility.py"),
    "tandem_feasibility_audit_sha256": "4" * 64,
}
detached = {
    "updated_utc": "2026-07-08T00:00:01+00:00",
    "state": detached_state,
    "pid": 12345,
    "log": str(case_dir / "detached.log"),
    "watcher": "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    "watch_iterations": 0,
    "sleep_seconds": 1800,
    "local_dry_run": False,
    "ssh_control_path": detached_ssh_path,
    "ssh_persist": "30m",
    "run_verify_runner": True,
    "run_resume_watchers": True,
    "run_rate_audit": True,
    "run_adaptive_acquisition": True,
    "run_adaptive_emx": adaptive_emx,
    "run_checkpoint": True,
    "run_cumulative": True,
    "run_evidence_index": True,
    "run_audit": True,
}
(case_dir / "wrapper.json").write_text(json.dumps(wrapper, indent=2), encoding="utf-8")
(case_dir / "detached.json").write_text(json.dumps(detached, indent=2), encoding="utf-8")
PY
}

run_expect() {
  local name="$1"
  local expected="$2"
  local wrapper="$3"
  local detached="$4"
  local out="$TMP_DIR/${name}.log"
  set +e
  WRAPPER_STATUS_JSON="$wrapper" \
  DETACHED_STATUS_JSON="$detached" \
  bash "$AUDIT" >"$out" 2>&1
  rc=$?
  set -e
  if [ "$expected" = "PASS" ]; then
    if [ "$rc" -eq 0 ] && grep -Fq "POST_DUO_LAUNCH_AUDIT_STATUS=PASS" "$out"; then
      echo "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=$name status=PASS"
      return 0
    fi
  else
    if [ "$rc" -ne 0 ] && grep -Fq "POST_DUO_LAUNCH_AUDIT_STATUS=FAIL" "$out"; then
      echo "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=$name status=PASS"
      return 0
    fi
  fi
  echo "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_CASE=$name status=FAIL expected=$expected rc=$rc"
  sed -n '1,240p' "$out"
  return 1
}

complete_dir="$TMP_DIR/complete"
write_case_json "$complete_dir"
run_expect complete_launch PASS "$complete_dir/wrapper.json" "$complete_dir/detached.json"

wrapper_fail_dir="$TMP_DIR/wrapper_fail"
write_case_json "$wrapper_fail_dir" FAIL
run_expect wrapper_fail FAIL "$wrapper_fail_dir/wrapper.json" "$wrapper_fail_dir/detached.json"

missing_detached_dir="$TMP_DIR/missing_detached"
write_case_json "$missing_detached_dir"
rm "$missing_detached_dir/detached.json"
run_expect missing_detached FAIL "$missing_detached_dir/wrapper.json" "$missing_detached_dir/detached.json"

ssh_mismatch_dir="$TMP_DIR/ssh_mismatch"
write_case_json "$ssh_mismatch_dir" PASS STARTED "$TMP_DIR/other_mux/%r@%h:%p"
run_expect ssh_mismatch FAIL "$ssh_mismatch_dir/wrapper.json" "$ssh_mismatch_dir/detached.json"

adaptive_emx_disabled_dir="$TMP_DIR/adaptive_emx_disabled"
write_case_json "$adaptive_emx_disabled_dir" PASS STARTED "$adaptive_emx_disabled_dir/mux/%r@%h:%p" false
run_expect adaptive_emx_disabled FAIL "$adaptive_emx_disabled_dir/wrapper.json" "$adaptive_emx_disabled_dir/detached.json"

missing_evidence_script_dir="$TMP_DIR/missing_evidence_script"
write_case_json "$missing_evidence_script_dir"
python3 - "$missing_evidence_script_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["evidence_index_script"] = ""
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect missing_evidence_index_script FAIL "$missing_evidence_script_dir/wrapper.json" "$missing_evidence_script_dir/detached.json"

bad_evidence_sha_dir="$TMP_DIR/bad_evidence_sha"
write_case_json "$bad_evidence_sha_dir"
python3 - "$bad_evidence_sha_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["evidence_index_script_sha256"] = "short"
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect bad_evidence_index_sha FAIL "$bad_evidence_sha_dir/wrapper.json" "$bad_evidence_sha_dir/detached.json"

missing_stack_sync_script_dir="$TMP_DIR/missing_stack_sync_script"
write_case_json "$missing_stack_sync_script_dir"
python3 - "$missing_stack_sync_script_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["verify_sync_stack_script"] = ""
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect missing_verify_sync_stack_script FAIL "$missing_stack_sync_script_dir/wrapper.json" "$missing_stack_sync_script_dir/detached.json"

bad_runner_sync_sha_dir="$TMP_DIR/bad_runner_sync_sha"
write_case_json "$bad_runner_sync_sha_dir"
python3 - "$bad_runner_sync_sha_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["verify_sync_runner_script_sha256"] = "short"
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect bad_verify_sync_runner_sha FAIL "$bad_runner_sync_sha_dir/wrapper.json" "$bad_runner_sync_sha_dir/detached.json"

bad_pairwise_builder_sha_dir="$TMP_DIR/bad_pairwise_builder_sha"
write_case_json "$bad_pairwise_builder_sha_dir"
python3 - "$bad_pairwise_builder_sha_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["surrogate_predict_sha256"] = "not-a-sha"
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect bad_pairwise_builder_sha FAIL "$bad_pairwise_builder_sha_dir/wrapper.json" "$bad_pairwise_builder_sha_dir/detached.json"

missing_adaptive_round_dir="$TMP_DIR/missing_adaptive_round"
write_case_json "$missing_adaptive_round_dir"
python3 - "$missing_adaptive_round_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["adaptive_round_script"] = ""
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect missing_adaptive_round_script FAIL "$missing_adaptive_round_dir/wrapper.json" "$missing_adaptive_round_dir/detached.json"

bad_tandem_compare_sha_dir="$TMP_DIR/bad_tandem_compare_sha"
write_case_json "$bad_tandem_compare_sha_dir"
python3 - "$bad_tandem_compare_sha_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["tandem_anchor_compare_sha256"] = "bad"
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect bad_tandem_compare_sha FAIL "$bad_tandem_compare_sha_dir/wrapper.json" "$bad_tandem_compare_sha_dir/detached.json"

bad_tandem_feasibility_sha_dir="$TMP_DIR/bad_tandem_feasibility_sha"
write_case_json "$bad_tandem_feasibility_sha_dir"
python3 - "$bad_tandem_feasibility_sha_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["tandem_feasibility_audit_sha256"] = "bad"
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect bad_tandem_feasibility_sha FAIL "$bad_tandem_feasibility_sha_dir/wrapper.json" "$bad_tandem_feasibility_sha_dir/detached.json"

dry_run_dir="$TMP_DIR/dry_run"
write_case_json "$dry_run_dir"
python3 - "$dry_run_dir/detached.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["local_dry_run"] = True
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect detached_local_dry_run FAIL "$dry_run_dir/wrapper.json" "$dry_run_dir/detached.json"

wrapper_dry_run_dir="$TMP_DIR/wrapper_dry_run"
write_case_json "$wrapper_dry_run_dir" DRY_RUN_PASS STARTED "$wrapper_dry_run_dir/mux/%r@%h:%p" true true
python3 - "$wrapper_dry_run_dir/wrapper.json" <<'PY'
import json
import sys
p = sys.argv[1]
d = json.load(open(p))
d["return_code"] = 0
open(p, "w").write(json.dumps(d, indent=2))
PY
run_expect wrapper_local_dry_run FAIL "$wrapper_dry_run_dir/wrapper.json" "$wrapper_dry_run_dir/detached.json"

echo "POST_DUO_LAUNCH_AUDIT_BEHAVIOR_STATUS=PASS"
