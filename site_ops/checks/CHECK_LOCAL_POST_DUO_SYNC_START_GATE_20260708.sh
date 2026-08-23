#!/usr/bin/env bash
set -euo pipefail

# Local regression guard for the post-Duo one-command sync/start wrapper.
# It must not SSH or start remote work; it only validates dry-run control flow.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
SCRIPT="$ROOT_DIR/RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh"
DETACHED="$ROOT_DIR/START_MARS56_POST_DUO_CONTINUOUS_WATCH_DETACHED_20260707.sh"

if [ ! -f "$SCRIPT" ]; then
  echo "POST_DUO_SYNC_START_GATE_STATUS=FAIL missing_script=$SCRIPT"
  exit 1
fi
if [ ! -f "$DETACHED" ]; then
  echo "POST_DUO_SYNC_START_GATE_STATUS=FAIL missing_detached_launcher=$DETACHED"
  exit 1
fi

bash -n "$SCRIPT"
bash -n "$DETACHED"

for token in \
  "local remote_marker=0" \
  "remote_marker=1" \
  'if [ "$LOCAL_DRY_RUN" = "1" ] && [ "$remote_marker" = "1" ]; then' \
  "SSH_CONTROL_PATH" \
  "SSH_PERSIST" \
  "RUN_LAUNCH_AUDIT" \
  "RUN_ADAPTIVE_ACQUISITION" \
  "RUN_ADAPTIVE_EMX" \
  "TARGET_ENVELOPE_CONFIG_LOCAL" \
  "TARGET_ENVELOPE_SHA256" \
  "target_envelope_config" \
  "target_envelope_sha256" \
  "EVIDENCE_INDEX_SCRIPT" \
  "EVIDENCE_INDEX_SCRIPT_SHA256" \
  "evidence_index_script" \
  "evidence_index_script_sha256" \
  "VERIFY_SYNC_RUNNER_SHA256" \
  "VERIFY_SYNC_STACK_SHA256" \
  "verify_sync_runner_script" \
  "verify_sync_runner_script_sha256" \
  "verify_sync_stack_script" \
  "verify_sync_stack_script_sha256" \
  "ADAPTIVE_ROUND_SHA256" \
  "SURROGATE_PREDICT_SHA256" \
  "adaptive_round_script" \
  "adaptive_round_sha256" \
  "surrogate_predict_script" \
  "surrogate_predict_sha256" \
  "MODEL_CHECKPOINT_SHA256" \
  "TANDEM_TRAIN_SHA256" \
  "TANDEM_ANCHOR_COMPARE_SHA256" \
  "BNI_TEMPERATURE_SELECT_SHA256" \
  "BNI_ABLATION_COMPARE_SHA256" \
  "LEARNING_CURVE_AUDIT_SHA256" \
  "model_checkpoint_script" \
  "model_checkpoint_sha256" \
  "tandem_train_script" \
  "tandem_train_sha256" \
  "tandem_anchor_compare_script" \
  "tandem_anchor_compare_sha256" \
  "bni_temperature_select_script" \
  "bni_temperature_select_sha256" \
  "bni_ablation_compare_script" \
  "bni_ablation_compare_sha256" \
  "learning_curve_audit_script" \
  "learning_curve_audit_sha256" \
  "tandem_feasibility_audit_script" \
  "tandem_feasibility_audit_sha256" \
  "run_evidence_index" \
  "POST_DUO_LOG_CAPTURE" \
  "POST_DUO_LOG_REEXECED" \
  "POST_DUO_STATUS_JSON" \
  "DETACHED_STATUS_JSON" \
  "LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO" \
  "LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN" \
  "write_post_duo_status_json" \
  "DRY_RUN_PASS" \
  "local_dry_run" \
  "remote_actions_executed" \
  "POST_DUO_SYNC_AND_START_STATUS_JSON" \
  "POST_DUO_LAUNCH_AUDIT_AUTO=START" \
  "POST_DUO_LAUNCH_AUDIT_AUTO_RC="
do
  if ! grep -Fq "$token" "$SCRIPT"; then
    echo "POST_DUO_SYNC_START_GATE_STATUS=FAIL missing_real_run_marker_handling_token=$token"
    exit 1
  fi
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

require_token() {
  local file="$1"
  local token="$2"
  if ! grep -Fq "$token" "$file"; then
    echo "missing_token=$token"
    echo "output_file=$file"
    sed -n '1,240p' "$file"
    return 1
  fi
}

require_order() {
  local file="$1"
  shift
  python3 - "$file" "$@" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
tokens = sys.argv[2:]
text = path.read_text(encoding="utf-8", errors="replace")
positions = []
for token in tokens:
    pos = text.find(token)
    if pos < 0:
        print(f"missing_order_token={token}")
        raise SystemExit(1)
    positions.append(pos)
if positions != sorted(positions) or len(set(positions)) != len(positions):
    print("order_violation=" + " -> ".join(tokens))
    print("positions=" + ",".join(str(value) for value in positions))
    raise SystemExit(1)
PY
}

run_case() {
  local name="$1"
  shift
  local out="$TMP_DIR/${name}.log"
  if ! "$@" >"$out" 2>&1; then
    echo "POST_DUO_SYNC_START_GATE_CASE=$name status=FAIL rc=$?"
    sed -n '1,240p' "$out"
    return 1
  fi
  echo "POST_DUO_SYNC_START_GATE_CASE=$name status=PASS output=$out"
}

run_case sync_and_start_dry_run env \
  LOCAL_DRY_RUN=1 \
  POST_DUO_LOG_CAPTURE=0 \
  POST_DUO_STATUS_JSON="$TMP_DIR/sync_and_start_status.json" \
  DETACHED_STATUS_JSON="$TMP_DIR/sync_and_start_detached_status.json" \
  RUN_LOCAL_GATES=0 \
  SYNC_REMOTE_STACK=1 \
  START_WATCHER=1 \
  RUN_LAUNCH_AUDIT=0 \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  STOP_ON_GOAL_PASS=1 \
  VERIFY_RUNNER_REQUIRED=1 \
  ALLOW_MISMATCH=0 \
  bash "$SCRIPT"

SYNC_START_OUT="$TMP_DIR/sync_and_start_dry_run.log"
for token in \
  "POST_DUO_SYNC_AND_START_STATUS=PASS" \
  "run_local_gates=0" \
  "ssh_control_path=" \
  "post_duo_log=disabled" \
  "RUN_LOCAL_GATES=0: skipping recursive local readiness gates" \
  "========== REMOTE_SYNC_100K_RUNNER ==========" \
  "LOCAL_DRY_RUN remote_command=env SYNC_REMOTE_RUNNER=1" \
  "SSH_CONTROL_PATH=" \
  "SSH_PERSIST=" \
  "SYNC_REMOTE_CHECKPOINT_STACK=1" \
  "========== SUPERVISOR_VERIFY_RUNNER ==========" \
  "MODE=verify-runner" \
  "========== START_DETACHED_CONTINUOUS_WATCHER ==========" \
  "ACTION=start" \
  "WATCH_ITERATIONS=1" \
  "SLEEP_SECONDS=0" \
  "RUN_EVIDENCE_INDEX=1" \
  "RUN_ADAPTIVE_ACQUISITION=1" \
  "RUN_ADAPTIVE_EMX=1" \
  "evidence_index_script=" \
  "evidence_index_script_sha256=" \
  "verify_sync_runner_script=" \
  "verify_sync_runner_script_sha256=" \
  "verify_sync_stack_script=" \
  "verify_sync_stack_script_sha256=" \
  "adaptive_round_script=" \
  "adaptive_round_sha256=" \
  "surrogate_predict_script=" \
  "surrogate_predict_sha256=" \
  "model_checkpoint_script=" \
  "model_checkpoint_sha256=" \
  "tandem_train_script=" \
  "tandem_train_sha256=" \
  "tandem_anchor_compare_script=" \
  "tandem_anchor_compare_sha256=" \
  "bni_temperature_select_script=" \
  "bni_temperature_select_sha256=" \
  "bni_ablation_compare_script=" \
  "bni_ablation_compare_sha256=" \
  "learning_curve_audit_script=" \
  "learning_curve_audit_sha256=" \
  "tandem_feasibility_audit_script=" \
  "tandem_feasibility_audit_sha256=" \
  "RUN_AUDIT=1" \
  "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_RUN_LAUNCH_AUDIT_0"
do
  require_token "$SYNC_START_OUT" "$token"
done

require_order "$SYNC_START_OUT" \
  "========== REMOTE_SYNC_100K_RUNNER ==========" \
  "========== REMOTE_SYNC_CHECKPOINT_STACK ==========" \
  "========== SUPERVISOR_VERIFY_RUNNER ==========" \
  "========== START_DETACHED_CONTINUOUS_WATCHER =========="
echo "POST_DUO_SYNC_START_GATE_CASE=sync_before_start_order status=PASS"

run_case verify_only_no_start_dry_run env \
  LOCAL_DRY_RUN=1 \
  POST_DUO_LOG_CAPTURE=0 \
  POST_DUO_STATUS_JSON="$TMP_DIR/verify_only_no_start_status.json" \
  DETACHED_STATUS_JSON="$TMP_DIR/verify_only_no_start_detached_status.json" \
  RUN_LOCAL_GATES=0 \
  SYNC_REMOTE_STACK=0 \
  START_WATCHER=0 \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  STOP_ON_GOAL_PASS=1 \
  VERIFY_RUNNER_REQUIRED=1 \
  ALLOW_MISMATCH=0 \
  bash "$SCRIPT"

VERIFY_ONLY_OUT="$TMP_DIR/verify_only_no_start_dry_run.log"
for token in \
  "POST_DUO_SYNC_AND_START_STATUS=PASS" \
  "========== REMOTE_VERIFY_100K_RUNNER ==========" \
  "========== REMOTE_VERIFY_CHECKPOINT_STACK ==========" \
  "START_WATCHER=0: remote stack synced/verified but detached watcher was not started."
do
  require_token "$VERIFY_ONLY_OUT" "$token"
done

run_case logging_status_json_dry_run env \
  LOCAL_DRY_RUN=1 \
  POST_DUO_LOG_CAPTURE=1 \
  POST_DUO_LOG_DIR="$TMP_DIR/post_duo_logs" \
  RUN_LOCAL_GATES=0 \
  SYNC_REMOTE_STACK=0 \
  START_WATCHER=0 \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  STOP_ON_GOAL_PASS=1 \
  VERIFY_RUNNER_REQUIRED=1 \
  ALLOW_MISMATCH=0 \
  bash "$SCRIPT"

LOGGING_OUT="$TMP_DIR/logging_status_json_dry_run.log"
for token in \
  "post_duo_log=$TMP_DIR/post_duo_logs/" \
  "post_duo_status_json=$TMP_DIR/post_duo_logs/mars56_post_duo_sync_start_latest_status.json" \
  "POST_DUO_SYNC_AND_START_STATUS_JSON=$TMP_DIR/post_duo_logs/mars56_post_duo_sync_start_latest_status.json" \
  "POST_DUO_LAUNCH_AUDIT_AUTO=SKIPPED_START_WATCHER_0"
do
  require_token "$LOGGING_OUT" "$token"
done

python3 - "$TMP_DIR/post_duo_logs/mars56_post_duo_sync_start_latest_status.json" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
assert data["state"] == "DRY_RUN_PASS", data
assert data["return_code"] == 0, data
assert data["local_dry_run"] is True, data
assert data["remote_actions_executed"] is False, data
assert data["start_watcher"] is False, data
assert data["sync_remote_stack"] is False, data
assert data["run_adaptive_acquisition"] is True, data
assert data["run_adaptive_emx"] is True, data
assert data["run_evidence_index"] is True, data
assert data["target_envelope_config"].endswith("mars56_s4p_1m_physical_feature_target_envelope_20260708.json"), data
assert len(data["target_envelope_sha256"]) == 64, data
assert data["evidence_index_script"].endswith("RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"), data
assert len(data["evidence_index_script_sha256"]) == 64, data
assert data["verify_sync_runner_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"), data
assert len(data["verify_sync_runner_script_sha256"]) == 64, data
assert data["verify_sync_stack_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"), data
assert len(data["verify_sync_stack_script_sha256"]) == 64, data
assert data["adaptive_round_script"].endswith("run_mars56_s4p_adaptive_physical_acquisition_round.sh"), data
assert len(data["adaptive_round_sha256"]) == 64, data
assert data["surrogate_predict_script"].endswith("build_physical_feature_surrogate_candidate_predictions.py"), data
assert len(data["surrogate_predict_sha256"]) == 64, data
assert data["model_checkpoint_script"].endswith("run_accepted_physical_feature_model_checkpoint.sh"), data
assert len(data["model_checkpoint_sha256"]) == 64, data
assert data["tandem_train_script"].endswith("train_physical_feature_tandem_inverse.py"), data
assert len(data["tandem_train_sha256"]) == 64, data
assert data["tandem_anchor_compare_script"].endswith("compare_tandem_geometry_anchor_ablation.py"), data
assert len(data["tandem_anchor_compare_sha256"]) == 64, data
assert data["bni_temperature_select_script"].endswith("select_balanced_mse_bni_temperature.py"), data
assert len(data["bni_temperature_select_sha256"]) == 64, data
assert data["bni_ablation_compare_script"].endswith("compare_balanced_mse_bni_ablation.py"), data
assert len(data["bni_ablation_compare_sha256"]) == 64, data
assert data["learning_curve_audit_script"].endswith("audit_physical_feature_model_learning_curve.py"), data
assert len(data["learning_curve_audit_sha256"]) == 64, data
assert data["tandem_feasibility_audit_script"].endswith("audit_tandem_predicted_geometry_feasibility.py"), data
assert len(data["tandem_feasibility_audit_sha256"]) == 64, data
assert data["watch_iterations"] == 1, data
assert data["sleep_seconds"] == 0, data
assert data.get("ssh_control_path"), data
assert data.get("ssh_persist"), data
assert data.get("detached_status_json"), data
PY

AUTO_DETACHED_DIR="$TMP_DIR/auto_detached"
mkdir -p "$AUTO_DETACHED_DIR"
AUTO_DETACHED_JSON="$AUTO_DETACHED_DIR/detached.json"
AUTO_SSH_CONTROL_PATH="$TMP_DIR/auto_mux/%r@%h:%p"
python3 - "$AUTO_DETACHED_JSON" "$AUTO_SSH_CONTROL_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
ssh_control_path = sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
data = {
    "updated_utc": "2026-07-08T00:00:00+00:00",
    "state": "STARTED",
    "pid": 24680,
    "log": str(path.parent / "detached.log"),
    "watcher": "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    "watch_iterations": 1,
    "sleep_seconds": 0,
    "local_dry_run": True,
    "ssh_control_path": ssh_control_path,
    "ssh_persist": "9m",
    "run_verify_runner": True,
    "run_resume_watchers": True,
    "run_rate_audit": True,
    "run_adaptive_acquisition": True,
    "run_adaptive_emx": True,
    "run_checkpoint": True,
    "run_cumulative": True,
    "run_evidence_index": True,
    "run_audit": True,
}
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY

run_case auto_launch_audit_dry_run env \
  LOCAL_DRY_RUN=1 \
  POST_DUO_LOG_CAPTURE=1 \
  POST_DUO_LOG_DIR="$TMP_DIR/auto_launch_logs" \
  RUN_LOCAL_GATES=0 \
  SYNC_REMOTE_STACK=0 \
  START_WATCHER=1 \
  RUN_LAUNCH_AUDIT=1 \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  STOP_ON_GOAL_PASS=1 \
  VERIFY_RUNNER_REQUIRED=1 \
  ALLOW_MISMATCH=0 \
  LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO=0 \
  LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=1 \
  SSH_CONTROL_PATH="$AUTO_SSH_CONTROL_PATH" \
  SSH_PERSIST=9m \
  DETACHED_STATUS_JSON="$AUTO_DETACHED_JSON" \
  bash "$SCRIPT"

AUTO_AUDIT_OUT="$TMP_DIR/auto_launch_audit_dry_run.log"
for token in \
  "POST_DUO_LAUNCH_AUDIT_AUTO=START" \
  "POST_DUO_LAUNCH_AUDIT_STATUS=PASS" \
  "POST_DUO_LAUNCH_AUDIT_AUTO_RC=0" \
  "POST_DUO_SYNC_AND_START_STATUS_JSON=$TMP_DIR/auto_launch_logs/mars56_post_duo_sync_start_latest_status.json"
do
  require_token "$AUTO_AUDIT_OUT" "$token"
done

python3 - "$TMP_DIR/auto_launch_logs/mars56_post_duo_sync_start_latest_status.json" "$AUTO_DETACHED_JSON" <<'PY'
import json
import sys
wrapper = json.load(open(sys.argv[1], encoding="utf-8"))
detached = json.load(open(sys.argv[2], encoding="utf-8"))
assert wrapper["state"] == "DRY_RUN_PASS", wrapper
assert wrapper["return_code"] == 0, wrapper
assert wrapper["local_dry_run"] is True, wrapper
assert wrapper["remote_actions_executed"] is False, wrapper
assert wrapper["start_watcher"] is True, wrapper
assert wrapper["sync_remote_stack"] is False, wrapper
assert wrapper["run_adaptive_acquisition"] is True, wrapper
assert wrapper["run_adaptive_emx"] is True, wrapper
assert wrapper["run_evidence_index"] is True, wrapper
assert wrapper["target_envelope_config"].endswith("mars56_s4p_1m_physical_feature_target_envelope_20260708.json"), wrapper
assert len(wrapper["target_envelope_sha256"]) == 64, wrapper
assert wrapper["evidence_index_script"].endswith("RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["evidence_index_script_sha256"]) == 64, wrapper
assert wrapper["verify_sync_runner_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["verify_sync_runner_script_sha256"]) == 64, wrapper
assert wrapper["verify_sync_stack_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["verify_sync_stack_script_sha256"]) == 64, wrapper
assert wrapper["adaptive_round_script"].endswith("run_mars56_s4p_adaptive_physical_acquisition_round.sh"), wrapper
assert len(wrapper["adaptive_round_sha256"]) == 64, wrapper
assert wrapper["surrogate_predict_script"].endswith("build_physical_feature_surrogate_candidate_predictions.py"), wrapper
assert len(wrapper["surrogate_predict_sha256"]) == 64, wrapper
assert wrapper["model_checkpoint_script"].endswith("run_accepted_physical_feature_model_checkpoint.sh"), wrapper
assert len(wrapper["model_checkpoint_sha256"]) == 64, wrapper
assert wrapper["tandem_train_script"].endswith("train_physical_feature_tandem_inverse.py"), wrapper
assert len(wrapper["tandem_train_sha256"]) == 64, wrapper
assert wrapper["tandem_anchor_compare_script"].endswith("compare_tandem_geometry_anchor_ablation.py"), wrapper
assert len(wrapper["tandem_anchor_compare_sha256"]) == 64, wrapper
assert wrapper["bni_temperature_select_script"].endswith("select_balanced_mse_bni_temperature.py"), wrapper
assert len(wrapper["bni_temperature_select_sha256"]) == 64, wrapper
assert wrapper["bni_ablation_compare_script"].endswith("compare_balanced_mse_bni_ablation.py"), wrapper
assert len(wrapper["bni_ablation_compare_sha256"]) == 64, wrapper
assert wrapper["learning_curve_audit_script"].endswith("audit_physical_feature_model_learning_curve.py"), wrapper
assert len(wrapper["learning_curve_audit_sha256"]) == 64, wrapper
assert wrapper["tandem_feasibility_audit_script"].endswith("audit_tandem_predicted_geometry_feasibility.py"), wrapper
assert len(wrapper["tandem_feasibility_audit_sha256"]) == 64, wrapper
assert wrapper["detached_status_json"] == sys.argv[2], wrapper
assert wrapper["ssh_control_path"] == detached["ssh_control_path"], (wrapper, detached)
assert wrapper["ssh_persist"] == detached["ssh_persist"], (wrapper, detached)
PY

NOLOG_DETACHED_DIR="$TMP_DIR/nolog_detached"
mkdir -p "$NOLOG_DETACHED_DIR"
NOLOG_DETACHED_JSON="$NOLOG_DETACHED_DIR/detached.json"
NOLOG_STATUS_JSON="$TMP_DIR/nolog_status/wrapper.json"
NOLOG_SSH_CONTROL_PATH="$TMP_DIR/nolog_mux/%r@%h:%p"
python3 - "$NOLOG_DETACHED_JSON" "$NOLOG_SSH_CONTROL_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
ssh_control_path = sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
data = {
    "updated_utc": "2026-07-08T00:00:00+00:00",
    "state": "STARTED",
    "pid": 13579,
    "log": str(path.parent / "detached.log"),
    "watcher": "RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    "watch_iterations": 1,
    "sleep_seconds": 0,
    "local_dry_run": True,
    "ssh_control_path": ssh_control_path,
    "ssh_persist": "11m",
    "run_verify_runner": True,
    "run_resume_watchers": True,
    "run_rate_audit": True,
    "run_adaptive_acquisition": True,
    "run_adaptive_emx": True,
    "run_checkpoint": True,
    "run_cumulative": True,
    "run_evidence_index": True,
    "run_audit": True,
}
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY

run_case no_log_auto_launch_audit_dry_run env \
  LOCAL_DRY_RUN=1 \
  POST_DUO_LOG_CAPTURE=0 \
  POST_DUO_STATUS_JSON="$NOLOG_STATUS_JSON" \
  RUN_LOCAL_GATES=0 \
  SYNC_REMOTE_STACK=0 \
  START_WATCHER=1 \
  RUN_LAUNCH_AUDIT=1 \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  STOP_ON_GOAL_PASS=1 \
  VERIFY_RUNNER_REQUIRED=1 \
  ALLOW_MISMATCH=0 \
  LAUNCH_AUDIT_REQUIRE_WATCH_ITERATIONS_ZERO=0 \
  LAUNCH_AUDIT_ALLOW_LOCAL_DRY_RUN=1 \
  SSH_CONTROL_PATH="$NOLOG_SSH_CONTROL_PATH" \
  SSH_PERSIST=11m \
  DETACHED_STATUS_JSON="$NOLOG_DETACHED_JSON" \
  bash "$SCRIPT"

NOLOG_AUDIT_OUT="$TMP_DIR/no_log_auto_launch_audit_dry_run.log"
for token in \
  "POST_DUO_SYNC_AND_START_STATUS_JSON=$NOLOG_STATUS_JSON" \
  "POST_DUO_LAUNCH_AUDIT_AUTO=START" \
  "POST_DUO_LAUNCH_AUDIT_STATUS=PASS" \
  "POST_DUO_LAUNCH_AUDIT_AUTO_RC=0" \
  "POST_DUO_SYNC_AND_START_STATUS=PASS"
do
  require_token "$NOLOG_AUDIT_OUT" "$token"
done

python3 - "$NOLOG_STATUS_JSON" "$NOLOG_DETACHED_JSON" <<'PY'
import json
import sys
wrapper = json.load(open(sys.argv[1], encoding="utf-8"))
detached = json.load(open(sys.argv[2], encoding="utf-8"))
assert wrapper["state"] == "DRY_RUN_PASS", wrapper
assert wrapper["return_code"] == 0, wrapper
assert wrapper["local_dry_run"] is True, wrapper
assert wrapper["remote_actions_executed"] is False, wrapper
assert wrapper["log"] == "disabled", wrapper
assert wrapper["start_watcher"] is True, wrapper
assert wrapper["sync_remote_stack"] is False, wrapper
assert wrapper["run_adaptive_acquisition"] is True, wrapper
assert wrapper["run_adaptive_emx"] is True, wrapper
assert wrapper["run_evidence_index"] is True, wrapper
assert wrapper["target_envelope_config"].endswith("mars56_s4p_1m_physical_feature_target_envelope_20260708.json"), wrapper
assert len(wrapper["target_envelope_sha256"]) == 64, wrapper
assert wrapper["evidence_index_script"].endswith("RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["evidence_index_script_sha256"]) == 64, wrapper
assert wrapper["verify_sync_runner_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["verify_sync_runner_script_sha256"]) == 64, wrapper
assert wrapper["verify_sync_stack_script"].endswith("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"), wrapper
assert len(wrapper["verify_sync_stack_script_sha256"]) == 64, wrapper
assert wrapper["adaptive_round_script"].endswith("run_mars56_s4p_adaptive_physical_acquisition_round.sh"), wrapper
assert len(wrapper["adaptive_round_sha256"]) == 64, wrapper
assert wrapper["surrogate_predict_script"].endswith("build_physical_feature_surrogate_candidate_predictions.py"), wrapper
assert len(wrapper["surrogate_predict_sha256"]) == 64, wrapper
assert wrapper["model_checkpoint_script"].endswith("run_accepted_physical_feature_model_checkpoint.sh"), wrapper
assert len(wrapper["model_checkpoint_sha256"]) == 64, wrapper
assert wrapper["tandem_train_script"].endswith("train_physical_feature_tandem_inverse.py"), wrapper
assert len(wrapper["tandem_train_sha256"]) == 64, wrapper
assert wrapper["tandem_anchor_compare_script"].endswith("compare_tandem_geometry_anchor_ablation.py"), wrapper
assert len(wrapper["tandem_anchor_compare_sha256"]) == 64, wrapper
assert wrapper["bni_temperature_select_script"].endswith("select_balanced_mse_bni_temperature.py"), wrapper
assert len(wrapper["bni_temperature_select_sha256"]) == 64, wrapper
assert wrapper["bni_ablation_compare_script"].endswith("compare_balanced_mse_bni_ablation.py"), wrapper
assert len(wrapper["bni_ablation_compare_sha256"]) == 64, wrapper
assert wrapper["learning_curve_audit_script"].endswith("audit_physical_feature_model_learning_curve.py"), wrapper
assert len(wrapper["learning_curve_audit_sha256"]) == 64, wrapper
assert wrapper["tandem_feasibility_audit_script"].endswith("audit_tandem_predicted_geometry_feasibility.py"), wrapper
assert len(wrapper["tandem_feasibility_audit_sha256"]) == 64, wrapper
assert wrapper["detached_status_json"] == sys.argv[2], wrapper
assert wrapper["ssh_control_path"] == detached["ssh_control_path"], (wrapper, detached)
assert wrapper["ssh_persist"] == detached["ssh_persist"], (wrapper, detached)
PY

run_case detached_watcher_ssh_env_dry_run env \
  ACTION=start \
  LOCAL_DRY_RUN=1 \
  STATE_DIR="$TMP_DIR/detached_watch" \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  RUN_VERIFY_RUNNER=1 \
  RUN_RESUME_WATCHERS=1 \
  RUN_RATE_AUDIT=1 \
  RUN_ADAPTIVE_ACQUISITION=1 \
  RUN_ADAPTIVE_EMX=1 \
  RUN_CHECKPOINT=1 \
  RUN_CUMULATIVE=1 \
  RUN_EVIDENCE_INDEX=1 \
  RUN_AUDIT=1 \
  SSH_CONTROL_PATH="$TMP_DIR/mux/%r@%h:%p" \
  SSH_PERSIST=7m \
  bash "$DETACHED"

DETACHED_OUT="$TMP_DIR/detached_watcher_ssh_env_dry_run.log"
for token in \
  "DETACHED_WATCH_STATUS=STARTED" \
  "ssh_control_path=$TMP_DIR/mux/%r@%h:%p" \
  "ssh_persist=7m" \
  "restart_on_config_mismatch=1" \
  "status_json=$TMP_DIR/detached_watch/mars56_post_duo_continuous_watch_latest_detached_status.json"
do
  require_token "$DETACHED_OUT" "$token"
done

python3 - "$TMP_DIR/detached_watch/mars56_post_duo_continuous_watch_latest_detached_status.json" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
assert data["state"] == "STARTED", data
assert data["local_dry_run"] is True, data
assert data["watch_iterations"] == 1, data
assert data["sleep_seconds"] == 0, data
assert data["ssh_control_path"].endswith("/mux/%r@%h:%p"), data
assert data["ssh_persist"] == "7m", data
for key in [
    "run_verify_runner",
    "run_resume_watchers",
  "run_rate_audit",
  "run_adaptive_acquisition",
  "run_adaptive_emx",
  "run_checkpoint",
    "run_cumulative",
    "run_evidence_index",
    "run_audit",
]:
    assert data[key] is True, (key, data)
PY

write_old_detached_state() {
  local state_dir="$1"
  local pid="$2"
  local ssh_path="$3"
  mkdir -p "$state_dir"
  printf '%s\n' "$pid" >"$state_dir/mars56_post_duo_continuous_watch_latest.pid"
  python3 - "$state_dir/mars56_post_duo_continuous_watch_latest_detached_status.json" "$pid" "$ssh_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
pid = int(sys.argv[2])
ssh_path = sys.argv[3]
data = {
    "updated_utc": "2026-07-08T00:00:00+00:00",
    "state": "STARTED",
    "pid": pid,
    "log": str(path.parent / "old_detached.log"),
    "watcher": "/home/researcher/Documents/模拟变压器AI反向建模/RUN_MARS56_POST_DUO_CONTINUOUS_WATCH_20260707.sh",
    "watch_iterations": 1,
    "sleep_seconds": 0,
    "local_dry_run": True,
    "ssh_control_path": ssh_path,
    "ssh_persist": "7m",
    "run_verify_runner": True,
    "run_resume_watchers": True,
    "run_rate_audit": True,
    "run_adaptive_acquisition": True,
    "run_adaptive_emx": False,
    "run_checkpoint": True,
    "run_cumulative": True,
    "run_evidence_index": True,
    "run_audit": True,
}
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
PY
}

OLD_FAIL_DIR="$TMP_DIR/detached_mismatch_fail"
sleep 60 &
OLD_FAIL_PID="$!"
write_old_detached_state "$OLD_FAIL_DIR" "$OLD_FAIL_PID" "$TMP_DIR/mismatch_mux/%r@%h:%p"
set +e
ACTION=start \
LOCAL_DRY_RUN=1 \
STATE_DIR="$OLD_FAIL_DIR" \
WATCH_ITERATIONS=1 \
SLEEP_SECONDS=0 \
RUN_ADAPTIVE_ACQUISITION=1 \
RUN_ADAPTIVE_EMX=1 \
RESTART_ON_CONFIG_MISMATCH=0 \
SSH_CONTROL_PATH="$TMP_DIR/mismatch_mux/%r@%h:%p" \
SSH_PERSIST=7m \
bash "$DETACHED" >"$TMP_DIR/detached_config_mismatch_no_restart.log" 2>&1
NO_RESTART_RC=$?
set -e
kill "$OLD_FAIL_PID" >/dev/null 2>&1 || true
if [ "$NO_RESTART_RC" -eq 0 ]; then
  echo "POST_DUO_SYNC_START_GATE_CASE=detached_config_mismatch_no_restart status=FAIL expected_nonzero"
  sed -n '1,240p' "$TMP_DIR/detached_config_mismatch_no_restart.log"
  exit 1
fi
for token in \
  "DETACHED_WATCH_STATUS=CONFIG_MISMATCH" \
  "run_adaptive_emx:expected=True,actual=False" \
  "DETACHED_WATCH_DECISION=FAIL_RESTART_ON_CONFIG_MISMATCH_0"
do
  require_token "$TMP_DIR/detached_config_mismatch_no_restart.log" "$token"
done
echo "POST_DUO_SYNC_START_GATE_CASE=detached_config_mismatch_no_restart status=PASS"

OLD_RESTART_DIR="$TMP_DIR/detached_mismatch_restart"
sleep 60 &
OLD_RESTART_PID="$!"
write_old_detached_state "$OLD_RESTART_DIR" "$OLD_RESTART_PID" "$TMP_DIR/restart_mux/%r@%h:%p"
run_case detached_config_mismatch_restart env \
  ACTION=start \
  LOCAL_DRY_RUN=1 \
  STATE_DIR="$OLD_RESTART_DIR" \
  WATCH_ITERATIONS=1 \
  SLEEP_SECONDS=0 \
  RUN_ADAPTIVE_ACQUISITION=1 \
  RUN_ADAPTIVE_EMX=1 \
  SSH_CONTROL_PATH="$TMP_DIR/restart_mux/%r@%h:%p" \
  SSH_PERSIST=7m \
  bash "$DETACHED"

RESTART_OUT="$TMP_DIR/detached_config_mismatch_restart.log"
for token in \
  "DETACHED_WATCH_STATUS=CONFIG_MISMATCH" \
  "run_adaptive_emx:expected=True,actual=False" \
  "DETACHED_WATCH_DECISION=RESTART_CONFIG_MISMATCH" \
  "DETACHED_WATCH_STATUS=STARTED"
do
  require_token "$RESTART_OUT" "$token"
done
if kill -0 "$OLD_RESTART_PID" >/dev/null 2>&1; then
  echo "POST_DUO_SYNC_START_GATE_CASE=detached_config_mismatch_restart status=FAIL old_pid_still_alive"
  exit 1
fi
python3 - "$OLD_RESTART_DIR/mars56_post_duo_continuous_watch_latest_detached_status.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["state"] == "STARTED", data
assert data["run_adaptive_emx"] is True, data
assert data["restart_on_config_mismatch"] is True, data
PY

echo "POST_DUO_SYNC_START_GATE_STATUS=PASS"
