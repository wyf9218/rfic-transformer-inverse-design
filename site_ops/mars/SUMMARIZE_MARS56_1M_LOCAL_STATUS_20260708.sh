#!/usr/bin/env bash
set -euo pipefail

# Local-only status summary for the MARS56 1M campaign.
#
# This script intentionally does not claim remote progress. By default it only
# reads local evidence files. Set RUN_PROBE=1 to also run the read-only
# BatchMode SSH probe before summarizing.

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
RUN_PROBE="${RUN_PROBE:-0}"
RUN_SCREEN_STATUS="${RUN_SCREEN_STATUS:-1}"
OUT_MD="${OUT_MD:-$ROOT_DIR/reports/mars56_1m_current_status_latest_CN.md}"
OUT_JSON="${OUT_JSON:-$ROOT_DIR/reports/mars56_1m_current_status_latest.json}"

case "$RUN_PROBE" in
  0|1) ;;
  *) echo "ERROR: RUN_PROBE must be 0 or 1." >&2; exit 2 ;;
esac
case "$RUN_SCREEN_STATUS" in
  0|1) ;;
  *) echo "ERROR: RUN_SCREEN_STATUS must be 0 or 1." >&2; exit 2 ;;
esac

if [ "$RUN_PROBE" = "1" ]; then
  set +e
  bash "$ROOT_DIR/CHECK_MARS56_NONINTERACTIVE_SSH_PROBE_20260708.sh" >/tmp/mars56_status_probe_$$.log 2>&1
  probe_rc=$?
  set -e
else
  probe_rc="not_run"
fi

python3 - "$ROOT_DIR" "$OUT_MD" "$OUT_JSON" "$probe_rc" "$RUN_SCREEN_STATUS" <<'PY'
import json
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
out_md = Path(sys.argv[2])
out_json = Path(sys.argv[3])
probe_rc = sys.argv[4]
run_screen_status = sys.argv[5] == "1"

def load_json(rel):
    path = root / rel
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception as exc:
        return {"_load_error": str(exc), "_path": str(path)}

def sha256_rel(rel):
    path = root / rel
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def screen_status():
    try:
        completed = subprocess.run(
            ["bash", "-lc", "ACTION=status bash START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh"],
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        return {
            "return_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as exc:
        return {"return_code": None, "stdout": "", "stderr": str(exc)}

live = load_json("reports/mars56_million_campaign_live_status.json") or {}
readiness = load_json("reports/mars56_1m_goal_readiness_local_audit_20260707.json") or {}
probe = load_json("logs/mars56_noninteractive_ssh_probe/mars56_noninteractive_ssh_probe_latest.json") or {}
wait = load_json("logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_latest_status.json") or {}
detached = load_json("logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_latest_detached_status.json") or {}
refresh = load_json("logs/mars56_1m_local_status_refresh/mars56_1m_local_status_refresh_latest_status.json") or {}
refresh_detached = load_json("logs/mars56_1m_local_status_refresh/mars56_1m_local_status_refresh_latest_detached_status.json") or {}
post_duo = load_json("logs/mars56_post_duo_sync_start/mars56_post_duo_sync_start_latest_status.json") or {}
interactive_bootstrap = load_json("logs/mars56_interactive_ssh_bootstrap/mars56_interactive_ssh_bootstrap_latest.json") or {}
target_envelope_config = load_json("rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json") or {}
screen = screen_status() if run_screen_status else {
    "return_code": None,
    "stdout": "SKIPPED_RUN_SCREEN_STATUS_0",
    "stderr": "",
}
if run_screen_status:
    detached = load_json("logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_latest_detached_status.json") or detached

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S CDT")
now_utc = datetime.now(timezone.utc)

def parse_iso_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

refresh_updated_at = parse_iso_utc(refresh.get("updated_utc"))
refresh_sleep_seconds = refresh.get("sleep_seconds")
try:
    refresh_sleep_seconds = int(refresh_sleep_seconds)
except (TypeError, ValueError):
    refresh_sleep_seconds = None
refresh_age_seconds = None
if refresh_updated_at is not None:
    refresh_age_seconds = int(max(0, (now_utc - refresh_updated_at).total_seconds()))
refresh_max_age_seconds = max(900, 2 * refresh_sleep_seconds) if refresh_sleep_seconds is not None else 900
if refresh_age_seconds is None:
    refresh_freshness_status = "UNKNOWN_NO_TIMESTAMP"
elif refresh_age_seconds <= refresh_max_age_seconds:
    refresh_freshness_status = "FRESH"
else:
    refresh_freshness_status = "STALE"
wait_message = wait.get("message", "")
if "Duo/Guacamole" in wait_message:
    wait_message = "Waiting for interactive SSH/Duo authentication or a reusable local SSH control connection."
readiness_pass = readiness.get("local_readiness_status") == "PASS" or (
    "LOCAL_GOAL_READINESS_STATUS=PASS"
    in (readiness.get("stdout", "") + "\n" + json.dumps(readiness, ensure_ascii=False))
)
if readiness.get("goal_completion_status") == "NOT_PROVEN_LOCAL_ONLY":
    goal_completion_status = "NOT_PROVEN_LOCAL_ONLY"
else:
    goal_completion_status = readiness.get("goal_completion_status") or live.get("goal_completion_status") or "NOT_PROVEN_LOCAL_ONLY"

checkpoint_contract = {
    "formal_100k_chunks_required": 10,
    "rows_per_formal_chunk_required": 100000,
    "total_rows_required": 1000000,
    "cumulative_prefix_checkpoints_required": 10,
    "touchstone_extension": ".s4p",
    "ports": 4,
    "frequency_start_ghz": 5.0,
    "frequency_stop_ghz": 60.0,
    "frequency_step_ghz": 0.5,
    "frequency_points": 111,
    "physical_features": ["Lp", "Ls", "Q", "|K|"],
    "physical_feature_ranges": {
        "lp_nh": [0.5, 3.0],
        "ls_nh": [0.5, 3.0],
        "q": [5.0, 25.0],
        "k_abs": [0.0, 0.8],
    },
    "uniformity_gates": {
        "marginal_bins": 10,
        "pair_bins": 10,
        "four_d_bins": 4,
        "min_1d_occupied_fraction": 0.90,
        "min_1d_entropy_fraction": 0.90,
        "max_1d_bin_imbalance": 2.50,
        "min_pair_occupied_fraction": 0.65,
        "min_pair_entropy_fraction": 0.80,
        "min_four_d_occupied_fraction": 0.50,
        "min_four_d_normalized_entropy": 0.80,
        "max_four_d_nonzero_bin_imbalance": 4.0,
        "require_distribution_plots": True,
    },
    "required_checkpoint_steps": [
        "stable_index",
        "response_features",
        "enrichment",
        "uniformity",
        "uniformity_manifest",
        "training",
        "model",
        "traceability",
    ],
}

waiter_version = {
    "state": detached.get("state", "UNKNOWN"),
    "watcher_sha256": detached.get("watcher_sha256"),
    "start_script_sha256": detached.get("start_script_sha256"),
    "wait_iterations": detached.get("wait_iterations"),
    "sleep_seconds": detached.get("sleep_seconds"),
    "start_on_pass": detached.get("start_on_pass"),
}
refresh_version = {
    "state": refresh_detached.get("state", "UNKNOWN"),
    "run_script_sha256": refresh_detached.get("run_script_sha256"),
    "summary_script_sha256": refresh_detached.get("summary_script_sha256"),
    "refresh_log_dir": refresh_detached.get("refresh_log_dir"),
    "refresh_status_json": refresh_detached.get("refresh_status_json"),
    "refresh_iterations": refresh_detached.get("refresh_iterations"),
    "sleep_seconds": refresh_detached.get("sleep_seconds"),
    "run_probe_each_refresh": refresh_detached.get("run_probe_each_refresh"),
    "local_dry_run": refresh_detached.get("local_dry_run"),
    "latest_refresh_updated_utc": refresh.get("updated_utc"),
    "latest_refresh_age_seconds": refresh_age_seconds,
    "latest_refresh_max_age_seconds": refresh_max_age_seconds,
    "latest_refresh_freshness_status": refresh_freshness_status,
}

def wait_for_ssh_runtime_contract():
    screen_session = "mars56_wait_for_ssh_start"
    runner_script = str(root / "logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_screen_runner.sh")
    watcher_script = str(root / "RUN_MARS56_WAIT_FOR_SSH_AND_START_1M_20260708.sh")
    expected_bootstrap_command = "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh"
    try:
        screen_completed = subprocess.run(
            ["screen", "-ls"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        screen_lines = [
            line.strip()
            for line in screen_completed.stdout.splitlines()
            if f".{screen_session}" in line
        ]
    except Exception as exc:
        screen_lines = []
        screen_error = f"{type(exc).__name__}: {exc}"
    else:
        screen_error = ""

    try:
        ps_output = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception as exc:
        ps_output = ""
        ps_error = f"{type(exc).__name__}: {exc}"
    else:
        ps_error = ""

    runner_login_pids = []
    orphan_runner_login_pids = []
    watcher_pids = []
    for raw_line in ps_output.splitlines():
        parts = raw_line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, ppid_s, command = parts
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
        except ValueError:
            continue
        if runner_script in command and "login -pflq" in command:
            runner_login_pids.append(pid)
            if ppid == 1:
                orphan_runner_login_pids.append(pid)
        if watcher_script in command and "bash" in command:
            watcher_pids.append(pid)

    checks = {
        "single_screen": len(screen_lines) == 1,
        "single_runner_login": len(runner_login_pids) == 1,
        "no_orphan_runner_login": len(orphan_runner_login_pids) == 0,
        "single_watcher_process": len(watcher_pids) == 1,
        "detached_status_running": detached.get("state") == "RUNNING",
        "wait_status_has_bootstrap_command": wait.get("interactive_bootstrap_command") == expected_bootstrap_command,
        "wait_status_has_production_env_policy": wait.get("start_env_policy") == "production_explicit_env_on_ssh_ready",
    }
    return {
        "runtime_status": "PASS" if all(checks.values()) and not screen_error and not ps_error else "CHECK_REPORT",
        "checks": checks,
        "screen_session": screen_session,
        "screen_session_count": len(screen_lines),
        "screen_lines": screen_lines,
        "screen_error": screen_error,
        "runner_login_process_count": len(runner_login_pids),
        "runner_login_pids": runner_login_pids,
        "orphan_runner_login_process_count": len(orphan_runner_login_pids),
        "orphan_runner_login_pids": orphan_runner_login_pids,
        "watcher_process_count": len(watcher_pids),
        "watcher_pids": watcher_pids,
        "ps_error": ps_error,
        "wait_status_json": str(root / "logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_latest_status.json"),
        "wait_status_state": wait.get("state"),
        "wait_status_iteration": wait.get("iteration"),
        "wait_status_bootstrap_command": wait.get("interactive_bootstrap_command"),
        "wait_status_recommended_next_action": wait.get("recommended_next_action"),
        "detached_status_json": str(root / "logs/mars56_wait_for_ssh_start/mars56_wait_for_ssh_start_latest_detached_status.json"),
        "detached_status_state": detached.get("state"),
        "detached_status_screen_session": detached.get("screen_session"),
    }

readiness_checks = readiness.get("checks", {}) if isinstance(readiness.get("checks", {}), dict) else {}
target_envelope = target_envelope_config.get("physical_feature_target_envelope", {}) if isinstance(target_envelope_config, dict) else {}
target_features = target_envelope.get("features", {}) if isinstance(target_envelope, dict) else {}
adaptive_targeting_contract = {
    "config_path": "rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
    "config_status": target_envelope_config.get("status"),
    "config_name": target_envelope_config.get("name"),
    "planning_source_required": "configured_feature_bounds",
    "feature_columns": target_envelope.get("feature_columns"),
    "feature_bounds": {
        key: {"min": value.get("min"), "max": value.get("max"), "unit": value.get("unit")}
        for key, value in target_features.items()
        if isinstance(value, dict)
    },
    "queue_count_per_adaptive_round": target_envelope.get("next_count"),
    "bins_per_feature": target_envelope.get("bins"),
    "four_d_bin_count": (int(target_envelope.get("bins") or 0) ** len(target_envelope.get("feature_columns") or []))
    if isinstance(target_envelope.get("feature_columns"), list)
    else None,
    "target_count_per_bin": target_envelope.get("target_count_per_bin"),
    "desired_total_count": target_envelope.get("desired_total_count"),
    "readiness_config_gate": readiness_checks.get("physical_feature_target_envelope_config_gate", {}).get("status"),
    "readiness_behavior_gate": readiness_checks.get("adaptive_physical_acquisition_round_behavior_gate", {}).get("status"),
    "readiness_wrapper_gate": readiness_checks.get("adaptive_after_duo_wrapper_gate", {}).get("status"),
}
checkpoint_stack_sync_contract = {
    "verify_script": "RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh",
    "verify_script_sha256": sha256_rel("RUN_MARS56_VERIFY_OR_SYNC_REMOTE_CHECKPOINT_STACK_AFTER_DUO_20260707.sh"),
    "target_envelope_config": "rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json",
    "target_envelope_sha256": sha256_rel("rfic-transformer-inverse-design/configs/mars56_s4p_1m_physical_feature_target_envelope_20260708.json"),
    "local_contract_only_status": "LOCAL_CONTRACT_ONLY_NOT_REMOTE_EVIDENCE",
    "required_remote_contract_status": "REMOTE_TARGET_ENVELOPE_CONTRACT_STATUS=PASS",
    "required_contract_fields": {
        "target_count_per_bin": 391,
        "desired_total_count": 100000,
        "four_d_bin_count": 256,
    },
    "readiness_gate": readiness_checks.get("remote_checkpoint_stack_verify_sync_gate", {}).get("status"),
}
post_duo_wrapper = {
    "state": post_duo.get("state"),
    "return_code": post_duo.get("return_code"),
    "local_dry_run": post_duo.get("local_dry_run"),
    "remote_actions_executed": post_duo.get("remote_actions_executed"),
    "sync_remote_stack": post_duo.get("sync_remote_stack"),
    "start_watcher": post_duo.get("start_watcher"),
    "run_adaptive_acquisition": post_duo.get("run_adaptive_acquisition"),
    "run_adaptive_emx": post_duo.get("run_adaptive_emx"),
    "target_envelope_sha256": post_duo.get("target_envelope_sha256"),
    "evidence_interpretation": (
        "DRY_RUN_ONLY_NOT_REMOTE_EVIDENCE"
        if post_duo.get("local_dry_run") is True or post_duo.get("remote_actions_executed") is False or post_duo.get("state") == "DRY_RUN_PASS"
        else "REMOTE_ACTION_EVIDENCE_CANDIDATE"
        if post_duo.get("state") == "PASS"
        else "NO_POST_DUO_REMOTE_EVIDENCE"
    ),
}

wait_runtime_contract = wait_for_ssh_runtime_contract()

interactive_ssh_bootstrap = {
    "script": "START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "script_sha256": sha256_rel("START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh"),
    "behavior_gate_script": "CHECK_LOCAL_INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_20260708.sh",
    "behavior_gate_script_sha256": sha256_rel("CHECK_LOCAL_INTERACTIVE_SSH_BOOTSTRAP_BEHAVIOR_20260708.sh"),
    "status_json": str(root / "logs/mars56_interactive_ssh_bootstrap/mars56_interactive_ssh_bootstrap_latest.json"),
    "latest_state": interactive_bootstrap.get("state", "NO_STATUS_JSON_YET"),
    "latest_updated_utc": interactive_bootstrap.get("updated_utc"),
    "latest_message": interactive_bootstrap.get("message"),
    "ssh_control_path": interactive_bootstrap.get("ssh_control_path") or f"/tmp/mars56_ssh_mux_{os.getuid()}/%C",
    "dry_run_command": "LOCAL_DRY_RUN=1 bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "real_command": "bash START_MARS56_INTERACTIVE_SSH_CONTROL_20260708.sh",
    "success_status": "MARS56_INTERACTIVE_SSH_BOOTSTRAP_STATUS=PASS_REUSABLE_CONTROL_CONNECTION",
    "post_success_automation": "The detached wait-for-SSH watcher can then detect PASS_REUSABLE_CONTROL_CONNECTION and launch RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh.",
}
if probe.get("status") == "WAITING_FOR_INTERACTIVE_AUTH":
    interactive_ssh_bootstrap["recommended_next_action"] = (
        "Run the real_command in a local terminal and complete password/Duo. "
        "This creates the reusable SSH ControlMaster socket; Guacamole alone does not."
    )
else:
    interactive_ssh_bootstrap["recommended_next_action"] = (
        "No interactive bootstrap action is required by the latest local probe status."
    )
latest_seconds_per_row = live.get("effective_wall_seconds_per_accepted_row") or live.get("chunk05_effective_seconds_per_row")
latest_days_per_100k = live.get("first_100k_raw_days_at_current_rate")
latest_parallel_jobs = live.get("chunk08_jobs") or live.get("chunk08_parallel_jobs") or live.get("chunk07_parallel_jobs") or live.get("chunk06_parallel_jobs") or live.get("chunk05_parallel_jobs")

def finite_float(value):
    try:
        value = float(value)
    except Exception:
        return None
    return value if value > 0 else None

seconds_for_status = finite_float(latest_seconds_per_row)
days_for_status = finite_float(latest_days_per_100k)
target_seconds = 4.0
target_days = 5.0
max_seconds = 4.5
max_days = 5.5
if seconds_for_status is None or days_for_status is None:
    throughput_target_status = "UNKNOWN_NO_CURRENT_REMOTE_RATE_PROOF"
    throughput_gate_status = "UNKNOWN_NO_CURRENT_REMOTE_RATE_PROOF"
else:
    throughput_target_status = "PASS" if seconds_for_status <= target_seconds and days_for_status <= target_days else "REVIEW"
    throughput_gate_status = "PASS" if seconds_for_status <= max_seconds and days_for_status <= max_days else "FAIL"

throughput_contract = {
    "expected_parallel_jobs": 48,
    "target_seconds_per_accepted_row": target_seconds,
    "target_days_per_100k": target_days,
    "max_seconds_per_accepted_row_gate": max_seconds,
    "max_days_per_100k_gate": max_days,
    "latest_known_parallel_jobs": latest_parallel_jobs,
    "latest_known_seconds_per_accepted_row": latest_seconds_per_row,
    "latest_known_days_per_100k": latest_days_per_100k,
    "target_status_from_latest_local_snapshot": throughput_target_status,
    "gate_status_from_latest_local_snapshot": throughput_gate_status,
    "evidence_boundary": "Latest rate values are from the last locally cached remote live_status snapshot; fresh proof still requires SSH/Duo access.",
}

summary = {
    "updated_at_cdt": now,
    "probe_run_this_time": probe_rc,
    "screen_status_probe_run": run_screen_status,
    "remote_auth_status": probe.get("status", "UNKNOWN"),
    "remote_auth_interpretation": probe.get("interpretation", "missing probe evidence"),
    "local_waiter_state": wait.get("state", "UNKNOWN"),
    "local_waiter_message": wait_message,
    "detached_waiter_state": detached.get("state", "UNKNOWN"),
    "local_status_refresh_state": refresh.get("state", "UNKNOWN"),
    "local_status_refresh_updated_utc": refresh.get("updated_utc"),
    "local_status_refresh_age_seconds": refresh_age_seconds,
    "local_status_refresh_max_age_seconds": refresh_max_age_seconds,
    "local_status_refresh_freshness_status": refresh_freshness_status,
    "local_status_refresh_detached_state": refresh_detached.get("state", "UNKNOWN"),
    "screen_status_stdout": screen.get("stdout", ""),
    "readiness_status": "PASS" if readiness_pass else "CHECK_REPORT",
    "goal_completion_status": goal_completion_status,
    "last_known_remote_chunk08_s4p_count": live.get("chunk08_s4p_count"),
    "last_known_remote_chunk08_target_count": live.get("chunk08_target_count"),
    "last_known_remote_evidence_time": live.get("updated_at_cdt"),
    "latest_gate_status": live.get("latest_gate_status"),
    "can_launch_first_100k": live.get("can_launch_first_100k"),
    "first_100k_block_reason": live.get("first_100k_block_reason"),
    "accepted_pool_after_chunk05_count": live.get("accepted_pool_after_chunk05_count"),
    "accepted_pool_after_chunk05_four_d_occupied_fraction": live.get("accepted_pool_after_chunk05_four_d_occupied_fraction"),
    "accepted_pool_after_chunk05_four_d_normalized_entropy": live.get("accepted_pool_after_chunk05_four_d_normalized_entropy"),
    "accepted_pool_after_chunk05_four_d_nonzero_bin_imbalance": live.get("accepted_pool_after_chunk05_four_d_nonzero_bin_imbalance"),
    "chunk05_effective_seconds_per_row": live.get("chunk05_effective_seconds_per_row"),
    "first_100k_raw_days_at_current_rate": live.get("first_100k_raw_days_at_current_rate"),
    "checkpoint_contract": checkpoint_contract,
    "adaptive_targeting_contract": adaptive_targeting_contract,
    "checkpoint_stack_sync_contract": checkpoint_stack_sync_contract,
    "post_duo_wrapper_latest": post_duo_wrapper,
    "interactive_ssh_bootstrap": interactive_ssh_bootstrap,
    "wait_for_ssh_runtime_contract": wait_runtime_contract,
    "throughput_contract": throughput_contract,
    "wait_for_ssh_watcher_version": waiter_version,
    "local_status_refresh_version": refresh_version,
    "boundary": (
        "Local automation readiness is evidence only for the local control flow. "
        "Remote 1M generation, every 100k model checkpoint, physical-feature uniformity, "
        "and final completion remain unproven until MARS evidence is accessible and audited."
    ),
}

out_json.parent.mkdir(parents=True, exist_ok=True)
out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

chunk08 = "unknown"
if summary["last_known_remote_chunk08_s4p_count"] is not None and summary["last_known_remote_chunk08_target_count"] is not None:
    chunk08 = f'{summary["last_known_remote_chunk08_s4p_count"]}/{summary["last_known_remote_chunk08_target_count"]}'

md = f"""# MARS56 1M 数据生成当前状态

更新时间：{now}

## 已经完成/已证明

- 本地自动化链路已准备好：readiness 状态为 `{summary["readiness_status"]}`。
- 本地 `screen` 等待器正在守着 SSH/Duo 可用性：`{summary["detached_waiter_state"]}`。
- 本地状态摘要刷新器：`{summary["local_status_refresh_detached_state"]}` / `{summary["local_status_refresh_state"]}`。
- 等待器入口：`START_MARS56_WAIT_FOR_SSH_AND_START_1M_DETACHED_20260708.sh`
- 交互 SSH 恢复入口：`{interactive_ssh_bootstrap["script"]}`
- 认证可用后会自动触发：`RUN_MARS56_POST_DUO_SYNC_AND_START_1M_WATCH_20260708.sh`
- 后续远端 watcher 会按设计执行 100k checkpoint、物理特征均匀性检查、模型测试、evidence index 和 final audit。

## 当前无人值守守护器版本

- wait-for-SSH watcher 状态：`{waiter_version["state"]}`。
- wait-for-SSH watcher SHA：`{waiter_version["watcher_sha256"]}`。
- post-Duo start script SHA：`{waiter_version["start_script_sha256"]}`。
- 本地状态刷新器状态：`{refresh_version["state"]}`。
- 本地状态刷新器 script SHA：`{refresh_version["run_script_sha256"]}`。
- 当前状态摘要 script SHA：`{refresh_version["summary_script_sha256"]}`。
- 本地状态刷新器配置：sleep `{refresh_version["sleep_seconds"]}` 秒，run_probe `{refresh_version["run_probe_each_refresh"]}`，local_dry_run `{refresh_version["local_dry_run"]}`。
- 本地状态新鲜度：`{refresh_version["latest_refresh_freshness_status"]}`，age `{refresh_version["latest_refresh_age_seconds"]}` 秒，max `{refresh_version["latest_refresh_max_age_seconds"]}` 秒。
- refresh status JSON：`{refresh_version["refresh_status_json"]}`。

## wait-for-SSH runtime 唯一性

- runtime status：`{wait_runtime_contract["runtime_status"]}`。
- screen session：`{wait_runtime_contract["screen_session"]}`，count `{wait_runtime_contract["screen_session_count"]}`。
- runner login process count：`{wait_runtime_contract["runner_login_process_count"]}`。
- orphan runner login process count：`{wait_runtime_contract["orphan_runner_login_process_count"]}`。
- watcher process count：`{wait_runtime_contract["watcher_process_count"]}`。
- wait status：`{wait_runtime_contract["wait_status_state"]}`，iteration `{wait_runtime_contract["wait_status_iteration"]}`。
- wait bootstrap command：`{wait_runtime_contract["wait_status_bootstrap_command"]}`。
- detached status：`{wait_runtime_contract["detached_status_state"]}`。

## 交互 SSH/Duo 恢复入口

- 当前 bootstrap 状态：`{interactive_ssh_bootstrap["latest_state"]}`。
- bootstrap status JSON：`{interactive_ssh_bootstrap["status_json"]}`。
- bootstrap script SHA：`{interactive_ssh_bootstrap["script_sha256"]}`。
- control socket：`{interactive_ssh_bootstrap["ssh_control_path"]}`。
- 查看命令但不连接：`{interactive_ssh_bootstrap["dry_run_command"]}`。
- 实际恢复命令：`{interactive_ssh_bootstrap["real_command"]}`。
- 成功标志：`{interactive_ssh_bootstrap["success_status"]}`。
- 下一步动作：{interactive_ssh_bootstrap["recommended_next_action"]}
- 成功后自动化：{interactive_ssh_bootstrap["post_success_automation"]}

## post-Duo wrapper 最新状态

- wrapper state：`{post_duo_wrapper["state"]}`。
- local dry-run：`{post_duo_wrapper["local_dry_run"]}`。
- remote actions executed：`{post_duo_wrapper["remote_actions_executed"]}`。
- evidence interpretation：`{post_duo_wrapper["evidence_interpretation"]}`。
- target envelope SHA：`{post_duo_wrapper["target_envelope_sha256"]}`。

## 当前未证明/不能宣称完成

- 远端认证状态：`{summary["remote_auth_status"]}`。
- 解释：{summary["remote_auth_interpretation"]}
- 100 万条生成完成：未证明。
- 每 10 万条模型测试全部完成：未证明。
- Lp/Ls/Q/|K| 在合理范围内均匀分布：未证明。
- 最后 1M final audit PASS：未证明。

## 最后已知远端证据

- 最后已知 chunk08 进度：`{chunk08}`。
- 最后已知远端证据时间：`{summary["last_known_remote_evidence_time"]}`。
- chunk05 实测 accepted row 平均时间：`{summary["chunk05_effective_seconds_per_row"]}` 秒/条。
- 10 万条预计周期：约 `{summary["first_100k_raw_days_at_current_rate"]}` 天。

## 生产吞吐合同

- 最优并行设置：`{throughput_contract["expected_parallel_jobs"]}` workers。
- 目标吞吐：`{throughput_contract["target_seconds_per_accepted_row"]}` 秒/accepted row，约 `{throughput_contract["target_days_per_100k"]}` 天/10万条。
- 硬门槛：不超过 `{throughput_contract["max_seconds_per_accepted_row_gate"]}` 秒/accepted row，且不超过 `{throughput_contract["max_days_per_100k_gate"]}` 天/10万条。
- 最新本地缓存远端快照：parallel `{throughput_contract["latest_known_parallel_jobs"]}`，`{throughput_contract["latest_known_seconds_per_accepted_row"]}` 秒/条，约 `{throughput_contract["latest_known_days_per_100k"]}` 天/10万条。
- target status：`{throughput_contract["target_status_from_latest_local_snapshot"]}`；gate status：`{throughput_contract["gate_status_from_latest_local_snapshot"]}`。
- 边界：{throughput_contract["evidence_boundary"]}

## 物理特征均匀性状态

- chunk05 后 accepted in-range pool：`{summary["accepted_pool_after_chunk05_count"]}` 条。
- chunk05 后 4D Lp/Ls/Q/|K| occupied fraction：`{summary["accepted_pool_after_chunk05_four_d_occupied_fraction"]}`。
- chunk05 后 4D normalized entropy：`{summary["accepted_pool_after_chunk05_four_d_normalized_entropy"]}`（旧缓存未记录时为 `None`，不得补画或推测）。
- chunk05 后 4D nonzero-bin max/min：`{summary["accepted_pool_after_chunk05_four_d_nonzero_bin_imbalance"]}`（旧缓存未记录时为 `None`，需重新认证后实测）。
- 当前 first100k block reason：{summary["first_100k_block_reason"]}

## Adaptive 补采样正式物理范围

- 配置文件：`{adaptive_targeting_contract["config_path"]}`。
- 配置状态：`{adaptive_targeting_contract["config_status"]}` / `{adaptive_targeting_contract["config_name"]}`。
- 规划范围来源要求：`{adaptive_targeting_contract["planning_source_required"]}`。
- 均匀目标：`{adaptive_targeting_contract["desired_total_count"]}` 条 checkpoint / `{adaptive_targeting_contract["four_d_bin_count"]}` 个 4D bins，目标 `{adaptive_targeting_contract["target_count_per_bin"]}` 条/bin。
- 每轮 adaptive queue：`{adaptive_targeting_contract["queue_count_per_adaptive_round"]}` 条，`{adaptive_targeting_contract["bins_per_feature"]}` bins/feature。
- 范围：Lp `{adaptive_targeting_contract["feature_bounds"].get("lp_nh_center")}`，Ls `{adaptive_targeting_contract["feature_bounds"].get("ls_nh_center")}`，Q `{adaptive_targeting_contract["feature_bounds"].get("q_center")}`，|K| `{adaptive_targeting_contract["feature_bounds"].get("k_abs_center")}`。
- readiness gate：config `{adaptive_targeting_contract["readiness_config_gate"]}`，wrapper `{adaptive_targeting_contract["readiness_wrapper_gate"]}`，behavior `{adaptive_targeting_contract["readiness_behavior_gate"]}`。

## 远端 checkpoint stack 同步合同

- verify script：`{checkpoint_stack_sync_contract["verify_script"]}`。
- verify script SHA：`{checkpoint_stack_sync_contract["verify_script_sha256"]}`。
- target envelope SHA：`{checkpoint_stack_sync_contract["target_envelope_sha256"]}`。
- 本地合同检查边界：`{checkpoint_stack_sync_contract["local_contract_only_status"]}`。
- 远端必须证明：`{checkpoint_stack_sync_contract["required_remote_contract_status"]}`。
- 合同字段：`{checkpoint_stack_sync_contract["required_contract_fields"]}`。
- readiness gate：`{checkpoint_stack_sync_contract["readiness_gate"]}`。

## 100k checkpoint 正式合同

- 正式 chunk：`10` 个，每个 `100000` 条，总计 `1000000` 条。
- 每个正式 chunk 必须是 `.s4p`、`4 port`、`5-60 GHz`、`0.5 GHz step`、`111` 个频点。
- 每个 100k checkpoint 必须完成：`stable_index -> response_features -> enrichment -> uniformity -> uniformity_manifest -> training -> model -> traceability`。
- 分布门槛使用 `Lp`, `Ls`, `Q`, `|K|`：Lp/Ls `0.5-3.0 nH`，Q `5-25`，|K| `0-0.8`。
- 均匀性要求：
  - 1D marginal：occupied fraction 至少 `0.90`，entropy fraction 至少 `0.90`，bin imbalance 不超过 `2.50`；
  - pairwise：occupied fraction 至少 `0.65`，entropy fraction 至少 `0.80`；
  - 4D：occupied fraction 至少 `0.50`、normalized entropy 至少 `0.80`、nonzero-bin max/min 不超过 `4.0`；
  - 必须有分布图像和 manifest。
- 最终完成还必须有 10 个累计前缀 checkpoint：100k、200k、...、1000k 全部 PASS。

## 边界

{summary["boundary"]}
"""

out_md.write_text(md)

print(f"MARS56_1M_LOCAL_STATUS_JSON={out_json}")
print(f"MARS56_1M_LOCAL_STATUS_MD={out_md}")
print(f"REMOTE_AUTH_STATUS={summary['remote_auth_status']}")
print(f"DETACHED_WAITER_STATE={summary['detached_waiter_state']}")
print(f"READINESS_STATUS={summary['readiness_status']}")
print(f"GOAL_COMPLETION_STATUS={summary['goal_completion_status']}")
print(f"LAST_KNOWN_CHUNK08={chunk08}")
PY
