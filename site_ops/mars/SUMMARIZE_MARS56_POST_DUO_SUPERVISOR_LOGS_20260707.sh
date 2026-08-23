#!/usr/bin/env bash
set -euo pipefail

# Summarize local transcripts produced by RUN_MARS56_POST_DUO_SUPERVISOR_20260707.sh.
#
# Usage:
#   bash SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh
#   bash SUMMARIZE_MARS56_POST_DUO_SUPERVISOR_LOGS_20260707.sh /path/to/log1.log /path/to/log2.log
#
# Default output:
#   reports/mars56_post_duo_supervisor_log_summary_20260707.json

ROOT_DIR="/home/researcher/Documents/模拟变压器AI反向建模"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/mars56_post_duo_supervisor}"
OUT="${OUT:-$ROOT_DIR/reports/mars56_post_duo_supervisor_log_summary_20260707.json}"

python3 - "$ROOT_DIR" "$LOG_DIR" "$OUT" "$@" <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import sys

root = Path(sys.argv[1])
log_dir = Path(sys.argv[2])
out = Path(sys.argv[3])
provided = [Path(p) for p in sys.argv[4:]]
EXPECTED_FORMAL_CHUNKS = 10
EXPECTED_CUMULATIVE_PREFIXES = 10
EXPECTED_TOTAL_NONEMPTY = 1000000
EXPECTED_EVIDENCE_INDEX_PASS = 10
EXPECTED_COMPLETION_DECISION = "GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW"

if provided:
    logs = provided
else:
    logs = sorted(log_dir.glob("mars56_post_duo_supervisor_*.log"), key=lambda p: (p.stat().st_mtime if p.exists() else 0, str(p)))

kv_line_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
section_re = re.compile(r"^=+ ([^=]+?) =+$")

def parse_token_kv(tokens):
    result = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        result[key] = value
    return result

def last_status(statuses, key):
    values = statuses.get(key)
    return values[-1] if values else None

def last_int(statuses, key):
    value = last_status(statuses, key)
    try:
        return int(value)
    except Exception:
        return None

def parse_log(path):
    text = path.read_text(errors="replace") if path.exists() else ""
    lines = text.splitlines()
    item = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "modified_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else None,
        "mode": None,
        "supervisor_log": None,
        "sections": [],
        "status_lines": {},
        "checkpoint_proof_consistency": [],
        "readiness_checks": [],
        "formal_chunks": [],
        "cumulative_prefixes": [],
        "datasets": [],
        "checkpoint_results": [],
        "cumulative_checkpoint_results": [],
        "notable_lines": [],
    }
    for line in lines:
        match = section_re.match(line)
        if match:
            item["sections"].append(match.group(1))
            continue

        kv_match = kv_line_re.match(line)
        if kv_match:
            key, value = kv_match.group(1), kv_match.group(2)
            if key in {
                "mode",
                "supervisor_log",
                "remote_time",
                "base",
                "CHECKPOINT_PROOF_CONSISTENCY_STATUS",
                "LOCAL_GOAL_READINESS_STATUS",
                "GOAL_COMPLETION_STATUS",
                "SUPERVISOR_STATUS",
                "ONE_MILLION_GOAL_STATUS",
                "ONE_MILLION_GOAL_DECISION",
                "STATUS",
                "checkpoint_proof",
                "checkpoint_summary",
                "formal_chunk_pass_count",
                "cumulative_pass_count",
                "total_nonempty_formal_s4p",
                "failure_count",
                "evidence_index_status",
                "evidence_formal_100k_dataset_count",
                "evidence_formal_100k_evidence_pass_count",
                "evidence_cumulative_evidence_count",
                "evidence_cumulative_evidence_pass_count",
                "evidence_index_audit_result",
                "production_plan_contract_audit_result",
                "production_plan_contract_audit_summary",
                "completed_100k_dataset_candidates",
                "completed",
                "checkpoint_pass",
                "checkpoint_fail",
                "waiting",
                "command_failed",
                "cumulative_ready_source_chunks",
            }:
                item["status_lines"].setdefault(key, []).append(value)
            if key == "mode":
                item["mode"] = value
            elif key == "supervisor_log":
                item["supervisor_log"] = value
            continue

        if line.startswith("CHECKPOINT_PROOF_CONSISTENCY "):
            item["checkpoint_proof_consistency"].append(parse_token_kv(line.split()[1:]))
            continue
        if line.startswith("CHECK "):
            parts = line.split()
            if len(parts) >= 2:
                parsed = {"check": parts[1]}
                parsed.update(parse_token_kv(parts[2:]))
                item["readiness_checks"].append(parsed)
            continue
        if line.startswith("FORMAL_CHUNK "):
            item["formal_chunks"].append(parse_token_kv(line.split()[1:]))
            continue
        if line.startswith("CUMULATIVE_PREFIX "):
            item["cumulative_prefixes"].append(parse_token_kv(line.split()[1:]))
            continue
        if line.startswith("DATASET "):
            item["datasets"].append(parse_token_kv(line.split()[1:]))
            continue
        if line.startswith("CHECKPOINT_RESULT "):
            item["checkpoint_results"].append(parse_token_kv(line.split()[1:]))
            continue
        if line.startswith("CUM_CHECKPOINT_RESULT "):
            item["cumulative_checkpoint_results"].append(parse_token_kv(line.split()[1:]))
            continue
        if any(marker in line for marker in ("FAIL", "ERROR", "WAIT_", "SKIP_", "READY_", "ONE_MILLION_GOAL_STATUS", "SUPERVISOR_STATUS")):
            item["notable_lines"].append(line)

    statuses = item["status_lines"]
    one_million_goal_status = last_status(statuses, "ONE_MILLION_GOAL_STATUS")
    one_million_goal_decision = last_status(statuses, "ONE_MILLION_GOAL_DECISION")
    formal_chunk_pass_count = last_int(statuses, "formal_chunk_pass_count")
    cumulative_pass_count = last_int(statuses, "cumulative_pass_count")
    total_nonempty_formal_s4p = last_int(statuses, "total_nonempty_formal_s4p")
    failure_count = last_int(statuses, "failure_count")
    evidence_index_status = last_status(statuses, "evidence_index_status")
    evidence_formal_100k_dataset_count = last_int(statuses, "evidence_formal_100k_dataset_count")
    evidence_formal_100k_evidence_pass_count = last_int(statuses, "evidence_formal_100k_evidence_pass_count")
    evidence_cumulative_evidence_count = last_int(statuses, "evidence_cumulative_evidence_count")
    evidence_cumulative_evidence_pass_count = last_int(statuses, "evidence_cumulative_evidence_pass_count")
    evidence_index_audit_result = last_status(statuses, "evidence_index_audit_result")
    production_plan_contract_audit_result = last_status(statuses, "production_plan_contract_audit_result")
    strict_goal_completion_proven = (
        one_million_goal_status == "PASS"
        and one_million_goal_decision == EXPECTED_COMPLETION_DECISION
        and formal_chunk_pass_count == EXPECTED_FORMAL_CHUNKS
        and cumulative_pass_count == EXPECTED_CUMULATIVE_PREFIXES
        and total_nonempty_formal_s4p is not None
        and total_nonempty_formal_s4p >= EXPECTED_TOTAL_NONEMPTY
        and evidence_index_status == "PASS"
        and evidence_formal_100k_dataset_count is not None
        and evidence_formal_100k_dataset_count >= EXPECTED_EVIDENCE_INDEX_PASS
        and evidence_formal_100k_evidence_pass_count is not None
        and evidence_formal_100k_evidence_pass_count >= EXPECTED_EVIDENCE_INDEX_PASS
        and evidence_cumulative_evidence_count is not None
        and evidence_cumulative_evidence_count >= EXPECTED_EVIDENCE_INDEX_PASS
        and evidence_cumulative_evidence_pass_count is not None
        and evidence_cumulative_evidence_pass_count >= EXPECTED_EVIDENCE_INDEX_PASS
        and evidence_index_audit_result == "PASS"
        and production_plan_contract_audit_result == "PASS"
        and failure_count == 0
    )
    item["derived"] = {
        "preflight_consistency_pass": last_status(statuses, "CHECKPOINT_PROOF_CONSISTENCY_STATUS") == "PASS",
        "goal_readiness_pass": last_status(statuses, "LOCAL_GOAL_READINESS_STATUS") == "PASS",
        "goal_completion_proven": strict_goal_completion_proven,
        "goal_completion_proof_rule": "requires ONE_MILLION_GOAL_STATUS=PASS, completion decision, 10 formal chunk PASS, 10 cumulative PASS, total_nonempty>=1000000, evidence_index_status=PASS, evidence formal/cumulative pass counts>=10, evidence_index_audit_result=PASS, production_plan_contract_audit_result=PASS, failure_count=0",
        "supervisor_final_status": last_status(statuses, "SUPERVISOR_STATUS"),
        "one_million_goal_status": one_million_goal_status,
        "one_million_goal_decision": one_million_goal_decision,
        "formal_chunk_pass_count_reported": formal_chunk_pass_count,
        "cumulative_pass_count_reported": cumulative_pass_count,
        "total_nonempty_formal_s4p_reported": total_nonempty_formal_s4p,
        "failure_count_reported": failure_count,
        "evidence_index_status_reported": evidence_index_status,
        "evidence_formal_100k_dataset_count_reported": evidence_formal_100k_dataset_count,
        "evidence_formal_100k_evidence_pass_count_reported": evidence_formal_100k_evidence_pass_count,
        "evidence_cumulative_evidence_count_reported": evidence_cumulative_evidence_count,
        "evidence_cumulative_evidence_pass_count_reported": evidence_cumulative_evidence_pass_count,
        "evidence_index_audit_result_reported": evidence_index_audit_result,
        "production_plan_contract_audit_result_reported": production_plan_contract_audit_result,
        "formal_chunk_pass_count": sum(1 for row in item["formal_chunks"] if row.get("state") == "PASS"),
        "formal_chunk_fail_count": sum(1 for row in item["formal_chunks"] if row.get("state") == "FAIL"),
        "cumulative_prefix_pass_count": sum(1 for row in item["cumulative_prefixes"] if row.get("state") == "PASS"),
        "cumulative_prefix_fail_count": sum(1 for row in item["cumulative_prefixes"] if row.get("state") == "FAIL"),
        "checkpoint_result_pass_count": sum(1 for row in item["checkpoint_results"] if row.get("proof") == "PASS"),
        "checkpoint_result_nonpass_count": sum(1 for row in item["checkpoint_results"] if row.get("proof") not in (None, "PASS")),
    }
    return item

summaries = [parse_log(path) for path in logs]
latest = summaries[-1] if summaries else None
completion_logs = [item for item in summaries if item["derived"]["goal_completion_proven"]]
latest_completion = completion_logs[-1] if completion_logs else None
report = {
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "audit_type": "local_supervisor_transcript_summary",
    "log_dir": str(log_dir),
    "log_count": len(summaries),
    "latest_log": latest["path"] if latest else None,
    "latest_supervisor_status": latest["derived"]["supervisor_final_status"] if latest else None,
    "latest_goal_completion_proven": latest["derived"]["goal_completion_proven"] if latest else False,
    "goal_completion_ever_proven": bool(completion_logs),
    "latest_goal_completion_proven_log": latest_completion["path"] if latest_completion else None,
    "latest_one_million_goal_status": latest["derived"]["one_million_goal_status"] if latest else None,
    "goal_completion_proof_rule": "requires ONE_MILLION_GOAL_STATUS=PASS, ONE_MILLION_GOAL_DECISION=GOAL_CAN_BE_MARKED_COMPLETE_AFTER_REVIEW, formal_chunk_pass_count=10, cumulative_pass_count=10, total_nonempty_formal_s4p>=1000000, evidence_index_status=PASS, evidence_formal_100k_evidence_pass_count>=10, evidence_cumulative_evidence_pass_count>=10, evidence_index_audit_result=PASS, production_plan_contract_audit_result=PASS, failure_count=0",
    "logs": summaries,
}

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"SUPERVISOR_LOG_SUMMARY_STATUS=PASS")
print(f"LOG_COUNT={len(summaries)}")
if latest:
    print(f"LATEST_LOG={latest['path']}")
    print(f"LATEST_SUPERVISOR_STATUS={latest['derived']['supervisor_final_status']}")
    print(f"LATEST_GOAL_COMPLETION_PROVEN={latest['derived']['goal_completion_proven']}")
print(f"GOAL_COMPLETION_EVER_PROVEN={bool(completion_logs)}")
if latest_completion:
    print(f"LATEST_GOAL_COMPLETION_PROVEN_LOG={latest_completion['path']}")
print(f"REPORT={out}")
PY
