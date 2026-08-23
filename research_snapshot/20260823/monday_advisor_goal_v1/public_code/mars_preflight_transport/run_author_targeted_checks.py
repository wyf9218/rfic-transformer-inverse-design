#!/usr/bin/env python3
"""Run only targeted local result-blind compile/synthetic/hostile checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[2]
AUTHORITATIVE = (
    WORKSPACE / "reports/historical_200k_fixed10k_mars_physical_20260822"
)
PYTHON_SOURCES = (
    "verify_result_free_preflight_transport_candidate.py",
    "test_result_free_preflight_transport_candidate.py",
    "run_author_targeted_checks.py",
    "freeze_prepared_candidate.py",
    "upstream/preflight_v3_prepared/run_result_free_mars_native_preflight_v3.py",
    "upstream/preflight_v3_prepared/test_result_free_mars_native_preflight_v3_synthetic.py",
    "upstream/preflight_v3_root_redteam_supporting/ROOT_REDTEAM_QA_HARNESS.py",
    "upstream/transport_v10_prepared/build_result_free_transport_runtime_v10.py",
    "upstream/transport_v10_prepared/result_free_runtime_smoke_v10.py",
    "upstream/transport_v10_prepared/test_result_free_runtime_smoke_v10_synthetic.py",
    "upstream/transport_v10_prepared/test_transport_runtime_layout_builder_v10_synthetic.py",
)
COMMANDS = (
    (
        "meta_contract_and_watcher_hostile_matrix",
        ROOT / "test_result_free_preflight_transport_candidate.py",
        ROOT / "test_result_free_preflight_transport_candidate.py",
    ),
    (
        "preflight_v3_author_synthetic_164",
        AUTHORITATIVE
        / "result_free_mars_native_preflight_v3_prepared_20260822T230419Z"
        / "test_result_free_mars_native_preflight_v3_synthetic.py",
        ROOT
        / "upstream/preflight_v3_prepared/test_result_free_mars_native_preflight_v3_synthetic.py",
    ),
    (
        "preflight_v3_root_redteam_supporting_36",
        AUTHORITATIVE
        / "root_redteam_result_free_mars_native_preflight_v3_qa_wip_20260822T230613Z"
        / "ROOT_REDTEAM_QA_HARNESS.py",
        ROOT / "upstream/preflight_v3_root_redteam_supporting/ROOT_REDTEAM_QA_HARNESS.py",
    ),
    (
        "transport_v10_builder_synthetic_101",
        AUTHORITATIVE
        / "transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
        / "test_transport_runtime_layout_builder_v10_synthetic.py",
        ROOT
        / "upstream/transport_v10_prepared/test_transport_runtime_layout_builder_v10_synthetic.py",
    ),
    (
        "transport_v10_smoke_synthetic_107",
        AUTHORITATIVE
        / "transport_runtime_layout_builder_v10_prepared_20260822T205518Z"
        / "test_result_free_runtime_smoke_v10_synthetic.py",
        ROOT / "upstream/transport_v10_prepared/test_result_free_runtime_smoke_v10_synthetic.py",
    ),
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compile_records: list[dict[str, object]] = []
    for relative in PYTHON_SOURCES:
        path = ROOT / relative
        raw = path.read_bytes()
        compile(raw, str(path), "exec")
        compile_records.append(
            {
                "relative_path": relative,
                "sha256": sha(raw),
                "size_bytes": len(raw),
                "status": "PASS_IN_MEMORY_COMPILE",
            }
        )
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="monday-preflight-transport-checks-") as cache:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = cache
        for label, execution_path, candidate_copy_path in COMMANDS:
            execution_raw = execution_path.read_bytes()
            candidate_copy_raw = candidate_copy_path.read_bytes()
            if execution_raw != candidate_copy_raw:
                raise RuntimeError(f"authoritative/candidate test bytes differ: {label}")
            completed = subprocess.run(
                [sys.executable, "-B", os.fspath(execution_path)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=180,
            )
            record: dict[str, object] = {
                "label": label,
                "execution_path_from_workspace": os.fspath(execution_path.relative_to(WORKSPACE)),
                "candidate_copy_relative_path": os.fspath(candidate_copy_path.relative_to(ROOT)),
                "authoritative_and_candidate_copy_byte_equal": True,
                "source_sha256": sha(execution_raw),
                "returncode": completed.returncode,
                "stdout_sha256": sha(completed.stdout),
                "stderr_sha256": sha(completed.stderr),
                "stdout_size_bytes": len(completed.stdout),
                "stderr_size_bytes": len(completed.stderr),
            }
            try:
                parsed = json.loads(completed.stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            if type(parsed) is dict:
                for key in ("status", "checked", "passed", "failed"):
                    if key in parsed:
                        record[key] = parsed[key]
            if completed.returncode != 0:
                record["status"] = "FAIL"
                runs.append(record)
                result = {
                    "status": "FAIL_TARGETED_RESULT_BLIND_CHECK",
                    "compile": compile_records,
                    "runs": runs,
                    "scope": {
                        "mars_accessed": False,
                        "results_accessed": False,
                        "signals_sent": False,
                        "stage07_or_stage08_executed": False,
                    },
                }
                raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
                write_exclusive(args.output, raw)
                print(json.dumps({"status": result["status"], "failed_label": label}))
                return 1
            runs.append(record)
    result = {
        "status": "PASS_TARGETED_RESULT_BLIND_CHECKS_ONLY_NOT_AUTHORIZATION",
        "compile_checked": len(compile_records),
        "compile_failed": 0,
        "compile": compile_records,
        "run_checked": len(runs),
        "run_failed": 0,
        "runs": runs,
        "scope": {
            "external_processes": "LOCAL_TEST_CHILDREN_ONLY",
            "mars_accessed": False,
            "mars_written": False,
            "native_preflight_executed": False,
            "production_root_or_journal_written": False,
            "results_accessed": False,
            "signals_sent": False,
            "stage07_or_stage08_executed": False,
            "transport_built_or_smoked": False,
        },
    }
    raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    write_exclusive(args.output, raw)
    print(
        json.dumps(
            {
                "status": result["status"],
                "compile_checked": len(compile_records),
                "run_checked": len(runs),
                "output_sha256": sha(raw),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
