#!/usr/bin/env python3
"""Execute a gated S8P million-sample campaign plan chunk by chunk.

The plan must be produced by ``run_gated_s8p_million_sample_campaign.py`` after
the EMX/HFSS S8P validation gate passes.  This executor is intentionally
two-keyed for safety: real EMX generation only starts when both the plan was
created with ``allow_real_emx=true`` and this script receives
``--allow-real-emx``.  Otherwise it emits a dry-run execution audit and runs no
production commands.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
PROJECT_ROOT = REPO_ROOT.parent
DEFAULT_PLAN = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_plan_current" / "s8p_million_sample_campaign_plan_summary.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs" / "s8p_million_sample_campaign_execution_current"
REQUIRED_CHUNK_COMMANDS = (
    "build_candidate_queue",
    "run_emx_parallel",
    "run_quality_gates",
    "train_inverse_model",
    "audit_inverse_model",
    "plan_nn_architecture_search",
    "train_nn_architecture_search",
    "audit_chunk_checkpoint",
)
NN_INPUT_COLUMNS = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    plan = _read_json(plan_path)
    plan_checks = _plan_checks(plan_path, plan, args)
    executable = all(check["status"] == "PASS" for check in plan_checks)
    dry_run = not bool(args.allow_real_emx)
    chunks = _selected_chunks(plan, args) if executable else []

    chunk_results: list[dict[str, Any]] = []
    if executable and not dry_run:
        for chunk in chunks:
            result = _run_chunk(chunk, args)
            chunk_results.append(result)
            if result["overall_status"] != "PASS":
                break
    elif executable:
        chunk_results = [_dry_run_chunk(chunk) for chunk in chunks]

    overall_status = _overall_status(executable, dry_run, chunk_results)
    decision = _decision(overall_status, executable, dry_run)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "plan_summary": str(plan_path),
        "out_dir": str(out_dir),
        "allow_real_emx": bool(args.allow_real_emx),
        "dry_run": dry_run,
        "start_chunk": int(args.start_chunk),
        "stop_chunk": args.stop_chunk,
        "selected_chunk_count": len(chunks),
        "completed_chunk_count": sum(1 for item in chunk_results if item.get("overall_status") == "PASS"),
        "total_requested_samples": int(plan.get("total_requested_samples") or 0),
        "chunk_size": int(plan.get("chunk_size") or 0),
        "plan_checks": plan_checks,
        "chunk_results": chunk_results,
        "safety_notes": [
            "Real EMX generation requires both plan allow_real_emx=true and executor --allow-real-emx.",
            "Execution stops after the first command failure or first non-PASS 100k checkpoint.",
            "Each chunk must pass audit_s8p_million_chunk_checkpoint.py before the next chunk is trusted.",
        ],
    }
    summary_path = out_dir / "s8p_million_campaign_execution_summary.json"
    report_path = out_dir / "S8P_MILLION_CAMPAIGN_EXECUTION_CN.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if overall_status in {"PASS", "DRY_RUN"} or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--start-chunk", type=int, default=1)
    parser.add_argument("--stop-chunk", type=int)
    parser.add_argument("--allow-real-emx", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _plan_checks(plan_path: Path, plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, str]]:
    total_requested = int(plan.get("total_requested_samples") or 0)
    chunk_size = int(plan.get("chunk_size") or 0)
    declared_chunk_count = int(plan.get("chunk_count") or 0)
    checks = [
        _check("plan summary exists", plan_path.is_file(), str(plan_path)),
        _check("plan summary parses", bool(plan) and "_parse_error" not in plan, str(plan.get("_parse_error", "JSON object" if plan else "missing"))),
        _check("plan overall status PASS", plan.get("overall_status") == "PASS", str(plan.get("overall_status"))),
        _check("plan decision ready", plan.get("decision") == "READY_TO_RUN_GATED_MILLION_SAMPLE_CAMPAIGN", str(plan.get("decision"))),
        _check("validation gate PASS", (plan.get("validation_gate") or {}).get("status") == "PASS", str((plan.get("validation_gate") or {}).get("status"))),
        _check("plan chunk count positive", declared_chunk_count > 0, str(plan.get("chunk_count"))),
        _check("plan total samples one million", total_requested == 1_000_000, str(plan.get("total_requested_samples"))),
        _check("plan chunk size one hundred thousand", chunk_size == 100_000, str(plan.get("chunk_size"))),
        _check(
            "plan chunk count matches total and chunk size",
            chunk_size > 0 and declared_chunk_count == total_requested // chunk_size and total_requested % chunk_size == 0,
            f"chunk_count={declared_chunk_count}, total={total_requested}, chunk_size={chunk_size}",
        ),
        _check("executor start chunk valid", int(args.start_chunk) >= 1, str(args.start_chunk)),
    ]
    if args.stop_chunk is not None:
        checks.append(_check("executor stop chunk valid", int(args.stop_chunk) >= int(args.start_chunk), f"{args.start_chunk}-{args.stop_chunk}"))
    if args.allow_real_emx:
        checks.append(_check("plan was created for real EMX", bool(plan.get("allow_real_emx")), str(plan.get("allow_real_emx"))))
    if plan.get("overall_status") == "PASS":
        checks.extend(_chunk_command_contract_checks(plan))
    return checks


def _chunk_command_contract_checks(plan: dict[str, Any]) -> list[dict[str, str]]:
    raw_chunks = plan.get("chunks") or []
    chunks = [chunk for chunk in raw_chunks if isinstance(chunk, dict)]
    declared_chunk_count = int(plan.get("chunk_count") or 0)
    expected_chunk_size = int(plan.get("chunk_size") or 0)
    checks = [
        _check("chunk list matches declared chunk count", len(chunks) == declared_chunk_count, f"{len(chunks)} / {declared_chunk_count}")
    ]
    for position, chunk in enumerate(chunks, start=1):
        chunk_index = int(chunk.get("chunk_index") or position)
        label = f"chunk {chunk_index}"
        sample_count = int(chunk.get("sample_count") or 0)
        checks.append(_check(f"{label} sample count matches chunk size", sample_count == expected_chunk_size, str(sample_count)))
        commands = chunk.get("commands")
        checks.append(_check(f"{label} commands object exists", isinstance(commands, dict), type(commands).__name__))
        if not isinstance(commands, dict):
            continue
        for command_name in REQUIRED_CHUNK_COMMANDS:
            command = commands.get(command_name)
            checks.append(_check(f"{label} command {command_name} present", isinstance(command, list) and bool(command), command_name))
        checks.extend(_chunk_command_detail_checks(label, sample_count, commands))
    return checks


def _chunk_command_detail_checks(label: str, sample_count: int, commands: dict[str, Any]) -> list[dict[str, str]]:
    return [
        _script_check(label, commands, "build_candidate_queue", "scripts/build_s8p_geometry_bootstrap_candidate_queue.py"),
        _script_check(label, commands, "run_emx_parallel", "scripts/run_candidate_queue_dataset_parallel.py"),
        _arg_value_check(label, commands, "run_emx_parallel", "--expected-touchstone-extension", ".s8p"),
        _arg_value_check(label, commands, "run_emx_parallel", "--expected-ports", "8"),
        _script_check(label, commands, "run_quality_gates", "scripts/run_dataset_quality_gates.py"),
        _arg_value_check(label, commands, "run_quality_gates", "--touchstone-expected-ports", "8"),
        _flag_check(label, commands, "run_quality_gates", "--touchstone-all"),
        _flag_check(label, commands, "run_quality_gates", "--extract-response-features"),
        _flag_check(label, commands, "run_quality_gates", "--build-physical-feature-inverse-training-table"),
        _script_check(label, commands, "train_inverse_model", "scripts/train_physical_feature_inverse_model.py"),
        _script_check(label, commands, "audit_inverse_model", "scripts/audit_physical_feature_inverse_model_quality.py"),
        _script_check(label, commands, "plan_nn_architecture_search", "scripts/plan_physical_feature_inverse_nn_architecture_search.py"),
        _arg_value_check(label, commands, "plan_nn_architecture_search", "--input-columns", NN_INPUT_COLUMNS),
        _arg_value_check(label, commands, "plan_nn_architecture_search", "--min-training-rows", str(sample_count)),
        _script_check(label, commands, "train_nn_architecture_search", "scripts/train_physical_feature_inverse_nn_architecture_search.py"),
        _arg_value_check(label, commands, "train_nn_architecture_search", "--input-columns", NN_INPUT_COLUMNS),
        _arg_value_check(label, commands, "train_nn_architecture_search", "--min-training-rows", str(sample_count)),
        _script_check(label, commands, "audit_chunk_checkpoint", "scripts/audit_s8p_million_chunk_checkpoint.py"),
        _arg_value_check(label, commands, "audit_chunk_checkpoint", "--expected-sample-count", str(sample_count)),
        _arg_value_check(label, commands, "audit_chunk_checkpoint", "--min-training-rows", str(sample_count)),
    ]


def _script_check(label: str, commands: dict[str, Any], command_name: str, script_path: str) -> dict[str, str]:
    command = commands.get(command_name)
    return _check(f"{label} {command_name} script", _command_contains(command, script_path), script_path)


def _flag_check(label: str, commands: dict[str, Any], command_name: str, flag: str) -> dict[str, str]:
    command = commands.get(command_name)
    return _check(f"{label} {command_name} flag {flag}", _command_contains(command, flag), _command_text(command))


def _arg_value_check(label: str, commands: dict[str, Any], command_name: str, flag: str, expected: str) -> dict[str, str]:
    command = commands.get(command_name)
    actual = _arg_value(command, flag)
    return _check(f"{label} {command_name} {flag}", actual == expected, actual if actual is not None else "missing")


def _command_contains(command: Any, needle: str) -> bool:
    return isinstance(command, list) and needle in [str(item) for item in command]


def _arg_value(command: Any, flag: str) -> str | None:
    if not isinstance(command, list):
        return None
    rendered = [str(item) for item in command]
    try:
        index = rendered.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(rendered):
        return None
    return rendered[index + 1]


def _command_text(command: Any) -> str:
    if not isinstance(command, list):
        return str(type(command).__name__)
    return " ".join(str(item) for item in command)


def _selected_chunks(plan: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    start = int(args.start_chunk)
    stop = int(args.stop_chunk) if args.stop_chunk is not None else None
    chunks: list[dict[str, Any]] = []
    for chunk in plan.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        index = int(chunk.get("chunk_index") or 0)
        if index < start:
            continue
        if stop is not None and index > stop:
            continue
        chunks.append(chunk)
    return chunks


def _run_chunk(chunk: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    command_results: list[dict[str, Any]] = []
    for command_name, command in (chunk.get("commands") or {}).items():
        if not isinstance(command, list):
            command_results.append(
                {
                    "command_name": str(command_name),
                    "overall_status": "FAIL",
                    "returncode": 2,
                    "command": command,
                    "stdout_tail": "",
                    "stderr_tail": "command is not a list",
                }
            )
            break
        rendered = [str(item) for item in command]
        if rendered and rendered[0] == sys.executable:
            rendered[0] = str(args.python)
        result = _run_command(rendered, command_name=str(command_name))
        command_results.append(result)
        if result["returncode"] != 0:
            break
        if command_name == "audit_chunk_checkpoint":
            checkpoint = _read_checkpoint(chunk)
            result["checkpoint_summary"] = checkpoint
            if checkpoint.get("overall_status") != "PASS":
                result["overall_status"] = "FAIL"
                break
    status = "PASS" if command_results and all(item.get("overall_status") == "PASS" for item in command_results) and _read_checkpoint(chunk).get("overall_status") == "PASS" else "FAIL"
    return {
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "sample_start": int(chunk.get("sample_start") or 0),
        "sample_stop": int(chunk.get("sample_stop") or 0),
        "sample_count": int(chunk.get("sample_count") or 0),
        "overall_status": status,
        "command_results": command_results,
        "checkpoint_summary": _read_checkpoint(chunk),
    }


def _dry_run_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    commands = chunk.get("commands") if isinstance(chunk.get("commands"), dict) else {}
    return {
        "chunk_index": int(chunk.get("chunk_index") or 0),
        "sample_start": int(chunk.get("sample_start") or 0),
        "sample_stop": int(chunk.get("sample_stop") or 0),
        "sample_count": int(chunk.get("sample_count") or 0),
        "overall_status": "DRY_RUN",
        "command_count": len(commands),
        "command_names": list(commands),
    }


def _run_command(command: list[str], *, command_name: str) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command_name": command_name,
        "overall_status": "PASS" if completed.returncode == 0 else "FAIL",
        "returncode": int(completed.returncode),
        "command": command,
        "stdout_tail": (completed.stdout or "")[-3000:],
        "stderr_tail": (completed.stderr or "")[-3000:],
    }


def _read_checkpoint(chunk: dict[str, Any]) -> dict[str, Any]:
    checkpoint_dir = Path(str(chunk.get("checkpoint_dir") or "")).expanduser()
    return _read_json(checkpoint_dir / "s8p_million_chunk_checkpoint_summary.json")


def _overall_status(executable: bool, dry_run: bool, chunk_results: list[dict[str, Any]]) -> str:
    if not executable:
        return "FAIL"
    if dry_run:
        return "DRY_RUN"
    if not chunk_results:
        return "FAIL"
    return "PASS" if all(item.get("overall_status") == "PASS" for item in chunk_results) else "FAIL"


def _decision(status: str, executable: bool, dry_run: bool) -> str:
    if not executable:
        return "DO_NOT_EXECUTE_MILLION_CAMPAIGN_PLAN_NOT_READY"
    if dry_run:
        return "DRY_RUN_ONLY_ADD_ALLOW_REAL_EMX_TO_START_AFTER_GATE"
    if status == "PASS":
        return "MILLION_CAMPAIGN_SELECTED_CHUNKS_COMPLETED"
    return "STOP_MILLION_CAMPAIGN_FIX_FAILED_CHUNK"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": str(exc)}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _check(name: str, passed: bool, detail: Any) -> dict[str, str]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": str(detail)}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# S8P Million Campaign Execution",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: `{summary['decision']}`",
        f"- Plan: `{summary['plan_summary']}`",
        f"- Selected chunks: `{summary['selected_chunk_count']}`",
        f"- Completed chunks: `{summary['completed_chunk_count']}`",
        f"- Dry run: `{summary['dry_run']}`",
        "",
        "## Plan Checks",
        "",
    ]
    lines.extend(f"- {check['status']}: {check['name']} - {check['detail']}" for check in summary["plan_checks"])
    lines.extend(["", "## Chunk Results", ""])
    for chunk in summary["chunk_results"]:
        lines.append(f"- Chunk {chunk.get('chunk_index')}: `{chunk.get('overall_status')}` samples {chunk.get('sample_start')}-{chunk.get('sample_stop')}")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {item}" for item in summary["safety_notes"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
