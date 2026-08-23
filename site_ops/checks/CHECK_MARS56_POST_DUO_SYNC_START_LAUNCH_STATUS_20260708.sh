#!/usr/bin/env bash
set -euo pipefail

# Audit the launch evidence after running:
#   bash RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh
#
# This script does not start generation. It verifies that the wrapper status
# and detached watcher status prove a production-ready post-Duo handoff.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
WRAPPER_STATUS_JSON="${WRAPPER_STATUS_JSON:-$ROOT_DIR/logs/mars56_post_duo_sync_start/mars56_post_duo_sync_start_latest_status.json}"
DETACHED_STATUS_JSON="${DETACHED_STATUS_JSON:-$ROOT_DIR/logs/mars56_post_duo_continuous_watch/mars56_post_duo_continuous_watch_latest_detached_status.json}"

REQUIRE_SYNC_REMOTE_STACK="${REQUIRE_SYNC_REMOTE_STACK:-1}"
REQUIRE_START_WATCHER="${REQUIRE_START_WATCHER:-1}"
REQUIRE_WATCH_ITERATIONS_ZERO="${REQUIRE_WATCH_ITERATIONS_ZERO:-1}"
ALLOW_LOCAL_DRY_RUN="${ALLOW_LOCAL_DRY_RUN:-0}"

case "$REQUIRE_SYNC_REMOTE_STACK" in 0|1) ;;
  *) echo "ERROR: REQUIRE_SYNC_REMOTE_STACK must be 0 or 1." >&2; exit 2 ;;
esac
case "$REQUIRE_START_WATCHER" in 0|1) ;;
  *) echo "ERROR: REQUIRE_START_WATCHER must be 0 or 1." >&2; exit 2 ;;
esac
case "$REQUIRE_WATCH_ITERATIONS_ZERO" in 0|1) ;;
  *) echo "ERROR: REQUIRE_WATCH_ITERATIONS_ZERO must be 0 or 1." >&2; exit 2 ;;
esac
case "$ALLOW_LOCAL_DRY_RUN" in 0|1) ;;
  *) echo "ERROR: ALLOW_LOCAL_DRY_RUN must be 0 or 1." >&2; exit 2 ;;
esac
for value in "$WRAPPER_STATUS_JSON" "$DETACHED_STATUS_JSON"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "ERROR: status JSON path contains unsupported newline characters." >&2
    exit 2
  fi
done

python3 - "$WRAPPER_STATUS_JSON" "$DETACHED_STATUS_JSON" "$REQUIRE_SYNC_REMOTE_STACK" "$REQUIRE_START_WATCHER" "$REQUIRE_WATCH_ITERATIONS_ZERO" "$ALLOW_LOCAL_DRY_RUN" <<'PY'
import json
import re
import sys
from pathlib import Path

(
    wrapper_path,
    detached_path,
    require_sync_remote_stack,
    require_start_watcher,
    require_watch_iterations_zero,
    allow_local_dry_run,
) = sys.argv[1:]

wrapper_path = Path(wrapper_path)
detached_path = Path(detached_path)
require_sync_remote_stack = require_sync_remote_stack == "1"
require_start_watcher = require_start_watcher == "1"
require_watch_iterations_zero = require_watch_iterations_zero == "1"
allow_local_dry_run = allow_local_dry_run == "1"

failures = []

def valid_sha256(value):
    return re.fullmatch(r"[0-9a-f]{64}", str(value or "")) is not None

def load_json(path, label):
    if not path.exists():
        failures.append(f"{label}_missing:{path}")
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        failures.append(f"{label}_parse_error:{type(exc).__name__}")
        return None

wrapper = load_json(wrapper_path, "wrapper_status_json")
detached = load_json(detached_path, "detached_status_json") if require_start_watcher else None

if wrapper is not None:
    allowed_wrapper_states = {"PASS"}
    if allow_local_dry_run:
        allowed_wrapper_states.add("DRY_RUN_PASS")
    if wrapper.get("state") not in allowed_wrapper_states:
        failures.append(f"wrapper_state_not_PASS:{wrapper.get('state')}")
    if not allow_local_dry_run and wrapper.get("local_dry_run") is True:
        failures.append("wrapper_local_dry_run_true")
    if not allow_local_dry_run and wrapper.get("remote_actions_executed") is False:
        failures.append("wrapper_remote_actions_not_executed")
    if wrapper.get("return_code") != 0:
        failures.append(f"wrapper_return_code_not_zero:{wrapper.get('return_code')}")
    if not wrapper.get("log"):
        failures.append("wrapper_log_missing")
    if not wrapper.get("ssh_control_path"):
        failures.append("wrapper_ssh_control_path_missing")
    if not wrapper.get("ssh_persist"):
        failures.append("wrapper_ssh_persist_missing")
    if require_sync_remote_stack and wrapper.get("sync_remote_stack") is not True:
        failures.append(f"wrapper_sync_remote_stack_not_true:{wrapper.get('sync_remote_stack')}")
    if require_sync_remote_stack:
        target_envelope = str(wrapper.get("target_envelope_config") or "")
        target_envelope_sha = str(wrapper.get("target_envelope_sha256") or "")
        verify_sync_runner = str(wrapper.get("verify_sync_runner_script") or "")
        verify_sync_runner_sha = str(wrapper.get("verify_sync_runner_script_sha256") or "")
        verify_sync_stack = str(wrapper.get("verify_sync_stack_script") or "")
        verify_sync_stack_sha = str(wrapper.get("verify_sync_stack_script_sha256") or "")
        adaptive_round = str(wrapper.get("adaptive_round_script") or "")
        adaptive_round_sha = str(wrapper.get("adaptive_round_sha256") or "")
        surrogate_predict = str(wrapper.get("surrogate_predict_script") or "")
        surrogate_predict_sha = str(wrapper.get("surrogate_predict_sha256") or "")
        model_checkpoint = str(wrapper.get("model_checkpoint_script") or "")
        model_checkpoint_sha = str(wrapper.get("model_checkpoint_sha256") or "")
        tandem_train = str(wrapper.get("tandem_train_script") or "")
        tandem_train_sha = str(wrapper.get("tandem_train_sha256") or "")
        tandem_anchor_compare = str(wrapper.get("tandem_anchor_compare_script") or "")
        tandem_anchor_compare_sha = str(wrapper.get("tandem_anchor_compare_sha256") or "")
        tandem_feasibility_audit = str(wrapper.get("tandem_feasibility_audit_script") or "")
        tandem_feasibility_audit_sha = str(wrapper.get("tandem_feasibility_audit_sha256") or "")
        if "mars56_s4p_1m_physical_feature_target_envelope_20260708.json" not in target_envelope:
            failures.append(f"wrapper_target_envelope_config_missing_or_unexpected:{target_envelope}")
        if len(target_envelope_sha) != 64:
            failures.append(f"wrapper_target_envelope_sha256_invalid:{target_envelope_sha}")
        if "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_100K_RUNNER_AFTER_DUO_20260707.sh" not in verify_sync_runner:
            failures.append(f"wrapper_verify_sync_runner_script_missing_or_unexpected:{verify_sync_runner}")
        if len(verify_sync_runner_sha) != 64:
            failures.append(f"wrapper_verify_sync_runner_script_sha256_invalid:{verify_sync_runner_sha}")
        if "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh" not in verify_sync_stack:
            failures.append(f"wrapper_verify_sync_stack_script_missing_or_unexpected:{verify_sync_stack}")
        if len(verify_sync_stack_sha) != 64:
            failures.append(f"wrapper_verify_sync_stack_script_sha256_invalid:{verify_sync_stack_sha}")
        if not adaptive_round.endswith("run_mars56_s4p_adaptive_physical_acquisition_round.sh"):
            failures.append(f"wrapper_adaptive_round_script_missing_or_unexpected:{adaptive_round}")
        if not valid_sha256(adaptive_round_sha):
            failures.append(f"wrapper_adaptive_round_sha256_invalid:{adaptive_round_sha}")
        if not surrogate_predict.endswith("build_physical_feature_surrogate_candidate_predictions.py"):
            failures.append(f"wrapper_surrogate_predict_script_missing_or_unexpected:{surrogate_predict}")
        if not valid_sha256(surrogate_predict_sha):
            failures.append(f"wrapper_surrogate_predict_sha256_invalid:{surrogate_predict_sha}")
        if not model_checkpoint.endswith("run_accepted_physical_feature_model_checkpoint.sh"):
            failures.append(f"wrapper_model_checkpoint_script_missing_or_unexpected:{model_checkpoint}")
        if not valid_sha256(model_checkpoint_sha):
            failures.append(f"wrapper_model_checkpoint_sha256_invalid:{model_checkpoint_sha}")
        if not tandem_train.endswith("train_physical_feature_tandem_inverse.py"):
            failures.append(f"wrapper_tandem_train_script_missing_or_unexpected:{tandem_train}")
        if not valid_sha256(tandem_train_sha):
            failures.append(f"wrapper_tandem_train_sha256_invalid:{tandem_train_sha}")
        if not tandem_anchor_compare.endswith("compare_tandem_geometry_anchor_ablation.py"):
            failures.append(
                f"wrapper_tandem_anchor_compare_script_missing_or_unexpected:{tandem_anchor_compare}"
            )
        if not valid_sha256(tandem_anchor_compare_sha):
            failures.append(f"wrapper_tandem_anchor_compare_sha256_invalid:{tandem_anchor_compare_sha}")
        if not tandem_feasibility_audit.endswith("audit_tandem_predicted_geometry_feasibility.py"):
            failures.append(
                f"wrapper_tandem_feasibility_audit_script_missing_or_unexpected:{tandem_feasibility_audit}"
            )
        if not valid_sha256(tandem_feasibility_audit_sha):
            failures.append(
                f"wrapper_tandem_feasibility_audit_sha256_invalid:{tandem_feasibility_audit_sha}"
            )
    if require_start_watcher and wrapper.get("start_watcher") is not True:
        failures.append(f"wrapper_start_watcher_not_true:{wrapper.get('start_watcher')}")
    if require_start_watcher and wrapper.get("run_adaptive_acquisition") is not True:
        failures.append(f"wrapper_run_adaptive_acquisition_not_true:{wrapper.get('run_adaptive_acquisition')}")
    if require_start_watcher and wrapper.get("run_adaptive_emx") is not True:
        failures.append(f"wrapper_run_adaptive_emx_not_true:{wrapper.get('run_adaptive_emx')}")
    if require_start_watcher and wrapper.get("run_evidence_index") is not True:
        failures.append(f"wrapper_run_evidence_index_not_true:{wrapper.get('run_evidence_index')}")
    if require_start_watcher:
        evidence_index_script = str(wrapper.get("evidence_index_script") or "")
        evidence_index_script_sha = str(wrapper.get("evidence_index_script_sha256") or "")
        if "RUN_MARS56_BUILD_100K_EVIDENCE_INDEX_AFTER_DUO_20260707.sh" not in evidence_index_script:
            failures.append(f"wrapper_evidence_index_script_missing_or_unexpected:{evidence_index_script}")
        if len(evidence_index_script_sha) != 64:
            failures.append(f"wrapper_evidence_index_script_sha256_invalid:{evidence_index_script_sha}")
    if require_watch_iterations_zero and wrapper.get("watch_iterations") != 0:
        failures.append(f"wrapper_watch_iterations_not_zero:{wrapper.get('watch_iterations')}")

if require_start_watcher and detached is not None:
    allowed_states = {"STARTED", "RUNNING", "ALREADY_RUNNING"}
    if detached.get("state") not in allowed_states:
        failures.append(f"detached_state_not_active:{detached.get('state')}")
    if detached.get("pid") is None:
        failures.append("detached_pid_missing")
    if not detached.get("log"):
        failures.append("detached_log_missing")
    if detached.get("ssh_control_path") != (wrapper or {}).get("ssh_control_path"):
        failures.append(
            "ssh_control_path_mismatch:"
            f"wrapper={(wrapper or {}).get('ssh_control_path')},detached={detached.get('ssh_control_path')}"
        )
    if detached.get("ssh_persist") != (wrapper or {}).get("ssh_persist"):
        failures.append(
            "ssh_persist_mismatch:"
            f"wrapper={(wrapper or {}).get('ssh_persist')},detached={detached.get('ssh_persist')}"
        )
    if detached.get("run_adaptive_acquisition") != (wrapper or {}).get("run_adaptive_acquisition"):
        failures.append(
            "run_adaptive_acquisition_mismatch:"
            f"wrapper={(wrapper or {}).get('run_adaptive_acquisition')},detached={detached.get('run_adaptive_acquisition')}"
        )
    if detached.get("run_adaptive_emx") != (wrapper or {}).get("run_adaptive_emx"):
        failures.append(
            "run_adaptive_emx_mismatch:"
            f"wrapper={(wrapper or {}).get('run_adaptive_emx')},detached={detached.get('run_adaptive_emx')}"
        )
    if require_watch_iterations_zero and detached.get("watch_iterations") != 0:
        failures.append(f"detached_watch_iterations_not_zero:{detached.get('watch_iterations')}")
    if not allow_local_dry_run and detached.get("local_dry_run") is True:
        failures.append("detached_local_dry_run_true")
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
        if detached.get(key) is not True:
            failures.append(f"detached_{key}_not_true:{detached.get(key)}")

summary = {
    "wrapper_status_json": str(wrapper_path),
    "detached_status_json": str(detached_path),
    "wrapper_state": None if wrapper is None else wrapper.get("state"),
    "detached_state": None if detached is None else detached.get("state"),
    "require_sync_remote_stack": require_sync_remote_stack,
    "require_start_watcher": require_start_watcher,
    "require_watch_iterations_zero": require_watch_iterations_zero,
    "allow_local_dry_run": allow_local_dry_run,
    "target_envelope_config": None if wrapper is None else wrapper.get("target_envelope_config"),
    "target_envelope_sha256": None if wrapper is None else wrapper.get("target_envelope_sha256"),
    "evidence_index_script": None if wrapper is None else wrapper.get("evidence_index_script"),
    "evidence_index_script_sha256": None if wrapper is None else wrapper.get("evidence_index_script_sha256"),
    "verify_sync_runner_script": None if wrapper is None else wrapper.get("verify_sync_runner_script"),
    "verify_sync_runner_script_sha256": None if wrapper is None else wrapper.get("verify_sync_runner_script_sha256"),
    "verify_sync_stack_script": None if wrapper is None else wrapper.get("verify_sync_stack_script"),
    "verify_sync_stack_script_sha256": None if wrapper is None else wrapper.get("verify_sync_stack_script_sha256"),
    "adaptive_round_script": None if wrapper is None else wrapper.get("adaptive_round_script"),
    "adaptive_round_sha256": None if wrapper is None else wrapper.get("adaptive_round_sha256"),
    "surrogate_predict_script": None if wrapper is None else wrapper.get("surrogate_predict_script"),
    "surrogate_predict_sha256": None if wrapper is None else wrapper.get("surrogate_predict_sha256"),
    "model_checkpoint_script": None if wrapper is None else wrapper.get("model_checkpoint_script"),
    "model_checkpoint_sha256": None if wrapper is None else wrapper.get("model_checkpoint_sha256"),
    "tandem_train_script": None if wrapper is None else wrapper.get("tandem_train_script"),
    "tandem_train_sha256": None if wrapper is None else wrapper.get("tandem_train_sha256"),
    "tandem_anchor_compare_script": None if wrapper is None else wrapper.get("tandem_anchor_compare_script"),
    "tandem_anchor_compare_sha256": None if wrapper is None else wrapper.get("tandem_anchor_compare_sha256"),
    "tandem_feasibility_audit_script": None if wrapper is None else wrapper.get("tandem_feasibility_audit_script"),
    "tandem_feasibility_audit_sha256": None if wrapper is None else wrapper.get("tandem_feasibility_audit_sha256"),
    "failure_count": len(failures),
    "failures": failures,
}

print("POST_DUO_LAUNCH_AUDIT_SUMMARY=" + json.dumps(summary, sort_keys=True))
if failures:
    print("POST_DUO_LAUNCH_AUDIT_STATUS=FAIL")
    raise SystemExit(1)
print("POST_DUO_LAUNCH_AUDIT_STATUS=PASS")
PY
