#!/usr/bin/env python3
"""Run the isolated, DRC-first 14-point high-|K|/Q second-stage pilot.

The supervisor is intentionally outside the million-sample controller.  It
accepts only the frozen PASS builder receipt and exact 14-row unlabeled queue,
then executes this one-way evidence chain:

1. candidate-bound Cadence streamout only (no EMX),
2. exact candidate-to-GDS binding,
3. direct-layout/Cadence physical structural-identity audit,
4. TSMC65 Calibre macro/IP DRC with ``mentor/old/2025``,
5. a hash-bound queue containing only Calibre PASS / zero-blocking rows,
6. fresh real EMX for exactly that filtered count.

Every external stage records UTC start/end, elapsed time, PID, argv, return
code, and stdout/stderr hashes.  Immutable inputs and prior-stage artifacts
are rehashed at every boundary.  Existing output directories, concurrent old
stage processes, source drift, count drift, identity drift, and stale output
all fail closed.  No merge, training, controller, or production action exists
in this tool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


EXPECTED_COUNT = 14
FRESH_EMX_BATCH_SIZE = 1
BUILDER_SCHEMA = "high_k_q_overlap_reachability_pilot.v2"
BUILDER_DECISION = "ISOLATED_14_CANDIDATES_READY_FOR_SEPARATE_GDS_DRC_REVIEW"
BUILDER_UNLABELED_STATUS = (
    "UNLABELED_AWAITING_SEPARATE_GDS_DRC_AND_FRESH_REAL_EMX"
)
CALIBRE_MODULE = "mentor/old/2025"
CALIBRE_BATCH_SCHEMA = "tsmc65_calibre_macro_ip_back_end_drc_batch_v1"
CALIBRE_ROW_SCHEMA = "candidate_bound_tsmc65_calibre_macro_ip_back_end_drc_v1"
CALIBRE_DRC_SCOPE = "foundry_macro_ip_back_end"
CALIBRE_PROCESS_TOKEN = "/TSMC65_05_12_26/"
CALIBRE_SWITCH_POLICY = {
    "MIXED_SCHEME": False,
    "CHECK_LOW_DENSITY": False,
    "FRONT_END": False,
    "BACK_END": True,
    "FULL_CHIP": False,
    "WLCSP_2_MASK": False,
    "GP": True,
    "LPG": False,
    "LP": False,
    "HALF_NODE": False,
    "28K_AP": True,
    "WLCSP_SEALRING": False,
    "DFM": False,
    "DFM_ONLY": False,
}
SUMMARY_NAME = "high_k_q_overlap_stage2_supervisor_summary.json"
STATE_NAME = "high_k_q_overlap_stage2_supervisor_state.json"
FILTERED_CSV_NAME = "calibre_pass_zero_blocking_candidates.csv"
FILTER_RECEIPT_NAME = "calibre_pass_filter_receipt.json"
AUTOMATIC_SUMMARY_FLAGS = (
    "automatic_cadence_authorized",
    "automatic_calibre_authorized",
    "automatic_emx_authorized",
    "automatic_production_authorized",
    "automatic_merge_authorized",
)
AUTOMATIC_ROW_FLAGS = (
    "automatic_cadence_authorized",
    "automatic_calibre_authorized",
    "automatic_emx_authorized",
    "automatic_production_authorized",
    "automatic_physical_delivery_authorized",
    "automatic_production_acceptance_authorized",
    "automatic_merge_authorized",
)
STAGE_SCRIPT_NAMES = (
    "run_high_k_q_overlap_stage2_supervisor.py",
    "run_high_k_q_overlap_stage2_resume_fresh_emx.py",
    "run_candidate_bound_existing_gds_fresh_emx.py",
    "run_candidate_queue_dataset.py",
    "run_candidate_queue_dataset_parallel.py",
    "build_current_foundry_candidate_gds_index.py",
    "audit_current_foundry_gds_physical_identity.py",
    "run_tsmc65_calibre_macro_drc.py",
    "run_transformer_zeus_cadence_roundtrip.py",
    "run_transformer_zeus_cadence_roundtrip_batch.py",
)
LOW_LEVEL_STAGE_EXECUTABLES = (
    "calibre",
    "emx",
    "strmout",
    "strmin",
    "dbAccess",
)
RUNTIME_SOURCE_EXCLUDED_PARTS = ("__pycache__", ".git")
RUNTIME_SOURCE_EXCLUDED_SUFFIXES = (".pyc", ".pyo")
RUNTIME_TREE_MANIFEST_SCHEMA = "high_k_q_overlap_stage2_runtime_tree.v1"


class SupervisorFailure(RuntimeError):
    """A fail-closed contract violation with evidence suitable for a receipt."""


class StageSignalInterruption(BaseException):
    """Signal converted to an exception so the isolated stage group is reaped."""

    def __init__(self, signum: int):
        self.signum = int(signum)
        super().__init__(f"stage supervisor received signal {self.signum}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir_preexisted = out_dir.exists()
    state: dict[str, Any] = {
        "schema": "high_k_q_overlap_stage2_supervisor_state.v1",
        "overall_status": "NOT_STARTED",
        "started_utc": _utc_now(),
        "supervisor_pid": os.getpid(),
        "stages": [],
        "automatic_production_authorized": False,
        "automatic_merge_authorized": False,
    }
    lock: dict[str, Any] | None = None
    release_lock = True
    try:
        lock = _acquire_single_instance_lock(
            candidate_csv=Path(args.candidate_csv).expanduser().resolve(),
            out_dir=out_dir,
        )
        state["single_instance_lock"] = {
            key: value for key, value in lock.items() if key != "token"
        }
        result = run_supervisor(args, state=state)
    except BaseException as exc:  # noqa: BLE001 - interruption must also leave evidence.
        if not isinstance(exc, Exception):
            release_lock = False
            state["single_instance_lock_retained_for_manual_audit"] = True
        state.update(
            {
                "overall_status": "FAIL",
                "decision": "STOP_STAGE2_SUPERVISOR_FAILED_CLOSED",
                "finished_utc": _utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        owns_failure_dir = bool(state.get("out_dir_created_by_supervisor"))
        if not out_dir_preexisted and not out_dir.exists():
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                out_dir.mkdir(exist_ok=False)
                owns_failure_dir = True
            except FileExistsError:
                # Another invocation won the path race; never write into it.
                owns_failure_dir = False
        if not out_dir_preexisted and owns_failure_dir and out_dir.is_dir():
            _write_json_atomic(out_dir / STATE_NAME, state)
            failure_path = out_dir / SUMMARY_NAME
            _write_json_atomic(failure_path, state)
            print(f"summary={failure_path}")
        print(f"overall_status=FAIL")
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None and release_lock:
            _release_single_instance_lock(lock)
    print(f"overall_status={result['overall_status']}")
    print(f"fresh_emx_count={result['fresh_emx_count']}")
    print(f"summary={result['summary_path']}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builder-summary", required=True)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-builder-summary-sha256", required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument(
        "--runtime-repo-root",
        default=str(SCRIPT_DIR.parent),
        help=(
            "Complete frozen repository containing scripts/ and "
            "rfic_transformer_inverse_design/. Required when this supervisor is "
            "deployed in a separate evidence-tools directory."
        ),
    )
    parser.add_argument(
        "--physical-audit-script",
        default=str(SCRIPT_DIR / "audit_current_foundry_gds_physical_identity.py"),
        help="Exact separately deployed physical-identity auditor to execute and pin.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=14,
        help=(
            "Cadence streamout-only scheduling batch size. Fresh EMX is always "
            "forced to batch size 1 so every Calibre-approved candidate owns an "
            "independently hashable GDS."
        ),
    )
    parser.add_argument("--nice-level", type=int, default=19)
    args = parser.parse_args(argv)
    for name in (
        "expected_builder_summary_sha256",
        "expected_candidate_sha256",
        "expected_config_sha256",
    ):
        value = str(getattr(args, name)).lower()
        if not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be a SHA-256 digest")
        setattr(args, name, value)
    if int(args.batch_size) < 1 or int(args.batch_size) > EXPECTED_COUNT:
        parser.error(f"--batch-size must be between 1 and {EXPECTED_COUNT}")
    if not 0 <= int(args.nice_level) <= 19:
        parser.error("--nice-level must be between 0 and 19")
    return args


def run_supervisor(
    args: argparse.Namespace,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supervisor_started_monotonic = time.monotonic()
    state = state if state is not None else {
        "schema": "high_k_q_overlap_stage2_supervisor_state.v1",
        "started_utc": _utc_now(),
        "supervisor_pid": os.getpid(),
        "stages": [],
    }
    builder_summary_path = Path(args.builder_summary).expanduser().resolve()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    # Preserve the lexical venv entry point.  Resolving a venv's python symlink
    # to its base interpreter discards pyvenv.cfg/site-packages selection.
    python_bin = Path(os.path.abspath(os.path.expanduser(str(args.python_bin))))
    runtime_repo_root = Path(args.runtime_repo_root).expanduser().resolve()
    runtime_script_dir = runtime_repo_root / "scripts"
    scripts = {
        "candidate_runner": runtime_script_dir / "run_candidate_queue_dataset.py",
        "gds_index": runtime_script_dir / "build_current_foundry_candidate_gds_index.py",
        "physical_audit": Path(args.physical_audit_script).expanduser().resolve(),
        "calibre": runtime_script_dir / "run_tsmc65_calibre_macro_drc.py",
        "supervisor": Path(__file__).resolve(),
    }

    if out_dir.exists():
        raise SupervisorFailure(f"refusing existing output directory: {out_dir}")
    if not python_bin.is_file():
        raise SupervisorFailure(f"Python executable is missing: {python_bin}")
    if not (runtime_repo_root / "rfic_transformer_inverse_design").is_dir():
        raise SupervisorFailure(
            f"runtime repository package is missing: {runtime_repo_root}"
        )
    for label, script in scripts.items():
        if not script.is_file():
            raise SupervisorFailure(f"required {label} script is missing: {script}")
    _assert_no_conflicting_processes()
    _validate_expected_file(
        builder_summary_path,
        str(args.expected_builder_summary_sha256),
        "builder summary",
    )
    _validate_expected_file(
        candidate_csv,
        str(args.expected_candidate_sha256),
        "candidate CSV",
    )
    _validate_expected_file(
        config_path,
        str(args.expected_config_sha256),
        "config",
    )
    builder_summary = _read_json(builder_summary_path)
    candidate_rows, candidate_fields = _read_csv(candidate_csv)
    _validate_builder_contract(
        builder_summary=builder_summary,
        builder_summary_path=builder_summary_path,
        candidate_csv=candidate_csv,
        candidate_rows=candidate_rows,
        candidate_fields=candidate_fields,
        config_path=config_path,
    )

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(exist_ok=False)
    state["out_dir_created_by_supervisor"] = True
    log_dir = out_dir / "logs"
    log_dir.mkdir()
    pins: dict[str, str] = {}
    runtime_source_paths = _runtime_source_paths(runtime_repo_root)
    for path in (
        builder_summary_path,
        candidate_csv,
        config_path,
        python_bin,
        *scripts.values(),
        *runtime_source_paths,
    ):
        _pin_file(pins, path)
    selected_python_runtime = _runtime_environment_record(
        python_bin=python_bin,
        runtime_repo_root=runtime_repo_root,
    )
    _assert_current_runtime_matches_selected(selected_python_runtime)
    runtime_dependencies = list(selected_python_runtime["dependencies"])
    for path in (Path(record["path"]) for record in runtime_dependencies):
        _pin_file(pins, path)
    external_runtime = _config_external_runtime_record(
        config_path=config_path,
        python_bin=python_bin,
        runtime_repo_root=runtime_repo_root,
    )
    for record in external_runtime["files"]:
        _pin_file(pins, Path(str(record["path"])))
    stage_executable_names = {
        Path(str(external_runtime["emx_binary"])).name,
    }
    _assert_pins(pins)
    runtime_manifest_path = out_dir / "runtime_source_manifest.json"
    runtime_manifest = {
        "schema": "high_k_q_overlap_stage2_runtime_source_manifest.v2",
        "generated_utc": _utc_now(),
        "runtime_repo_root": str(runtime_repo_root),
        "python_bin": _file_record(python_bin),
        "selected_python_runtime": {
            key: value
            for key, value in selected_python_runtime.items()
            if key != "dependencies"
        },
        "runtime_dependencies": runtime_dependencies,
        "external_runtime": external_runtime,
        "file_count": len(runtime_source_paths),
        "files": [_file_record(path) for path in runtime_source_paths],
        "aggregate_sha256": _json_sha256(
            [
                {"path": str(path), "sha256": _sha256(path)}
                for path in runtime_source_paths
            ]
        ),
    }
    _write_json_atomic(runtime_manifest_path, runtime_manifest)
    _pin_file(pins, runtime_manifest_path)
    state.update(
        {
            "overall_status": "RUNNING",
            "decision": "RUN_ISOLATED_DRC_FIRST_STAGE2_ONLY",
            "out_dir": str(out_dir),
            "python_bin": str(python_bin),
            "runtime_repo_root": str(runtime_repo_root),
            "runtime_source_manifest": _file_record(runtime_manifest_path),
            "immutable_file_sha256": dict(pins),
            "builder_contract": {
                "summary": _file_record(builder_summary_path),
                "candidate_csv": _file_record(candidate_csv),
                "config": _file_record(config_path),
                "candidate_count": len(candidate_rows),
                "all_automatic_authorizations_false": True,
            },
        }
    )
    _write_json_atomic(out_dir / STATE_NAME, state)

    cadence_dir = out_dir / "01_cadence_streamout_only"
    cadence_command = [
        str(python_bin),
        str(scripts["candidate_runner"]),
        "--candidate-csv",
        str(candidate_csv),
        "--out-dir",
        str(cadence_dir),
        "--config",
        str(config_path),
        "--expected-config-sha256",
        str(args.expected_config_sha256),
        "--max-count",
        str(EXPECTED_COUNT),
        "--batch-size",
        str(args.batch_size),
        "--cadence-streamout-only",
        "--fail-on-error",
        "--force-wideband-5-60-0p5",
        "--expected-touchstone-extension",
        ".s4p",
        "--expected-ports",
        "4",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "60",
        "--expected-frequency-step-ghz",
        "0.5",
        "--expected-frequency-points",
        "111",
    ]
    _prepare_stage(pins, cadence_dir, stage_executable_names)
    _run_stage(
        name="cadence_streamout_only_exact14",
        command=cadence_command,
        log_dir=log_dir,
        out_dir=out_dir,
        state=state,
        cwd=runtime_repo_root,
    )
    cadence_summary_path = cadence_dir / "candidate_queue_dataset_summary.json"
    cadence_rows_path = cadence_dir / "dataset_rows.csv"
    _validate_cadence_summary(cadence_summary_path, candidate_csv)
    _pin_file(pins, cadence_summary_path)
    _pin_file(pins, cadence_rows_path)
    _attach_stage_artifacts(
        state,
        out_dir,
        {
            "summary": cadence_summary_path,
            "dataset_rows": cadence_rows_path,
        },
    )

    binding_dir = out_dir / "02_candidate_gds_binding"
    binding_command = [
        str(python_bin),
        str(scripts["gds_index"]),
        "--candidate-csv",
        str(candidate_csv),
        "--dataset-dir",
        str(cadence_dir),
        "--out-dir",
        str(binding_dir),
        "--expected-count",
        str(EXPECTED_COUNT),
    ]
    _prepare_stage(pins, binding_dir, stage_executable_names)
    _run_stage(
        name="candidate_gds_binding_exact14",
        command=binding_command,
        log_dir=log_dir,
        out_dir=out_dir,
        state=state,
        cwd=runtime_repo_root,
    )
    binding_summary_path = (
        binding_dir / "candidate_bound_cadence_gds_index_summary.json"
    )
    binding_index_path = binding_dir / "candidate_bound_cadence_gds_index.csv"
    _validate_binding_summary(binding_summary_path, binding_index_path)
    _pin_file(pins, binding_summary_path)
    _pin_file(pins, binding_index_path)
    _attach_stage_artifacts(
        state,
        out_dir,
        {"summary": binding_summary_path, "gds_index": binding_index_path},
    )

    physical_dir = out_dir / "03_gds_physical_identity"
    physical_command = [
        str(python_bin),
        str(scripts["physical_audit"]),
        "--candidate-csv",
        str(candidate_csv),
        "--dataset-dir",
        str(cadence_dir),
        "--input-index-csv",
        str(binding_index_path),
        "--out-dir",
        str(physical_dir),
        "--expected-count",
        str(EXPECTED_COUNT),
        "--expected-candidate-sha256",
        _sha256(candidate_csv),
        "--expected-index-sha256",
        _sha256(binding_index_path),
    ]
    _prepare_stage(pins, physical_dir, stage_executable_names)
    _run_stage(
        name="gds_physical_identity_exact14",
        command=physical_command,
        log_dir=log_dir,
        out_dir=out_dir,
        state=state,
        cwd=runtime_repo_root,
    )
    physical_summary_path = physical_dir / "gds_physical_identity_audit_summary.json"
    audited_index_path = physical_dir / "physically_audited_gds_index.csv"
    _validate_physical_summary(physical_summary_path, audited_index_path)
    _pin_file(pins, physical_summary_path)
    _pin_file(pins, audited_index_path)
    _attach_stage_artifacts(
        state,
        out_dir,
        {"summary": physical_summary_path, "audited_gds_index": audited_index_path},
    )

    calibre_dir = out_dir / "04_calibre_drc"
    calibre_command = [
        str(python_bin),
        str(scripts["calibre"]),
        "--input-index-csv",
        str(audited_index_path),
        "--out-dir",
        str(calibre_dir),
        "--calibre-module",
        CALIBRE_MODULE,
        "--maximum-candidates",
        str(EXPECTED_COUNT),
        "--nice-level",
        str(args.nice_level),
        "--no-fail-exit",
    ]
    _prepare_stage(pins, calibre_dir, stage_executable_names)
    _run_stage(
        name="tsmc65_calibre_drc_exact14",
        command=calibre_command,
        log_dir=log_dir,
        out_dir=out_dir,
        state=state,
        cwd=runtime_repo_root,
    )
    calibre_summary_path = calibre_dir / "tsmc65_calibre_macro_drc_batch_summary.json"
    drc_index_path = calibre_dir / "drc_index.csv"
    _pin_file(pins, calibre_summary_path)
    _pin_file(pins, drc_index_path)
    _attach_stage_artifacts(
        state,
        out_dir,
        {"summary": calibre_summary_path, "drc_index": drc_index_path},
    )

    filter_dir = out_dir / "05_calibre_pass_filter"
    _prepare_stage(pins, filter_dir, stage_executable_names)
    filter_started = time.monotonic()
    filter_stage = {
        "name": "calibre_pass_zero_blocking_filter",
        "started_utc": _utc_now(),
        "pid": os.getpid(),
        "command": ["internal", "filter_calibre_pass_and_zero_blocking"],
        "status": "RUNNING",
    }
    state["stages"].append(filter_stage)
    _write_json_atomic(out_dir / STATE_NAME, state)
    filter_dir.mkdir(exist_ok=False)
    try:
        filter_result = _filter_calibre_pass_candidates(
            candidate_csv=candidate_csv,
            audited_index_csv=audited_index_path,
            calibre_summary_path=calibre_summary_path,
            drc_index_path=drc_index_path,
            out_dir=filter_dir,
        )
    except Exception as exc:
        filter_stage.update(
            {
                "finished_utc": _utc_now(),
                "elapsed_seconds": time.monotonic() - filter_started,
                "return_code": 2,
                "status": "FAIL",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        _write_json_atomic(out_dir / STATE_NAME, state)
        raise
    filter_stage.update(
        {
            "finished_utc": _utc_now(),
            "elapsed_seconds": time.monotonic() - filter_started,
            "return_code": 0,
            "status": "PASS",
            "artifacts": {
                "filtered_candidate_csv": _file_record(
                    Path(filter_result["filtered_candidate_csv"])
                ),
                "filter_receipt": _file_record(Path(filter_result["receipt_path"])),
            },
        }
    )
    filtered_candidate_csv = Path(filter_result["filtered_candidate_csv"])
    filter_receipt_path = Path(filter_result["receipt_path"])
    filtered_count = int(filter_result["pass_count"])
    _pin_file(pins, filtered_candidate_csv)
    _pin_file(pins, filter_receipt_path)
    _write_json_atomic(out_dir / STATE_NAME, state)

    fresh_dir = out_dir / "06_fresh_real_emx"
    fresh_command = [
        str(python_bin),
        str(scripts["candidate_runner"]),
        "--candidate-csv",
        str(filtered_candidate_csv),
        "--out-dir",
        str(fresh_dir),
        "--config",
        str(config_path),
        "--expected-config-sha256",
        str(args.expected_config_sha256),
        "--max-count",
        str(filtered_count),
        "--batch-size",
        str(FRESH_EMX_BATCH_SIZE),
        "--fail-on-error",
        "--force-wideband-5-60-0p5",
        "--expected-touchstone-extension",
        ".s4p",
        "--expected-ports",
        "4",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "60",
        "--expected-frequency-step-ghz",
        "0.5",
        "--expected-frequency-points",
        "111",
        "--max-touchstone-checks",
        str(filtered_count),
    ]
    _prepare_stage(pins, fresh_dir, stage_executable_names)
    _run_stage(
        name="fresh_real_emx_exact_calibre_pass_count",
        command=fresh_command,
        log_dir=log_dir,
        out_dir=out_dir,
        state=state,
        cwd=runtime_repo_root,
    )
    fresh_summary_path = fresh_dir / "candidate_queue_dataset_summary.json"
    fresh_rows_path = fresh_dir / "dataset_rows.csv"
    fresh_evidence = _validate_fresh_emx(
        summary_path=fresh_summary_path,
        dataset_rows_path=fresh_rows_path,
        filtered_candidate_csv=filtered_candidate_csv,
        audited_index_csv=audited_index_path,
        physical_audit_script=scripts["physical_audit"],
        runtime_repo_root=runtime_repo_root,
        fresh_dir=fresh_dir,
        expected_count=filtered_count,
    )
    _pin_file_expected(
        pins,
        fresh_summary_path,
        str(fresh_evidence["summary"]["sha256"]),
    )
    _pin_file_expected(
        pins,
        fresh_rows_path,
        str(fresh_evidence["dataset_rows"]["sha256"]),
    )
    for record in fresh_evidence["touchstone_files"]:
        _pin_file_expected(
            pins,
            Path(str(record["path"])),
            str(record["sha256"]),
        )
    for record in fresh_evidence["fresh_gds_reproducibility"]:
        _pin_file_expected(
            pins,
            Path(str(record["fresh_gds_path"])),
            str(record["fresh_gds_sha256"]),
        )
    _attach_stage_artifacts(
        state,
        out_dir,
        {
            "summary": fresh_summary_path,
            "dataset_rows": fresh_rows_path,
        },
    )
    _assert_pins(pins)

    merge_artifacts = _find_forbidden_merge_artifacts(out_dir)
    if merge_artifacts:
        raise SupervisorFailure(
            f"forbidden merge/production artifacts appeared: {merge_artifacts[:10]}"
        )
    finished_utc = _utc_now()
    final_summary = {
        "schema": "high_k_q_overlap_stage2_supervisor.v1",
        "generated_utc": finished_utc,
        "overall_status": "PASS",
        "decision": "ISOLATED_STAGE2_FRESH_EMX_RETURN_READY_FOR_INDEPENDENT_AUDIT_ONLY",
        "supervisor_pid": os.getpid(),
        "started_utc": state["started_utc"],
        "finished_utc": finished_utc,
        "elapsed_seconds": time.monotonic() - supervisor_started_monotonic,
        "frozen_candidate_count": EXPECTED_COUNT,
        "calibre_pass_zero_blocking_count": filtered_count,
        "fresh_emx_count": filtered_count,
        "stages": state["stages"],
        "immutable_file_sha256": dict(pins),
        "runtime_source_manifest": _file_record(runtime_manifest_path),
        "builder_summary": _file_record(builder_summary_path),
        "candidate_csv": _file_record(candidate_csv),
        "config": _file_record(config_path),
        "cadence_summary": _file_record(cadence_summary_path),
        "gds_binding_summary": _file_record(binding_summary_path),
        "physical_identity_summary": _file_record(physical_summary_path),
        "calibre_summary": _file_record(calibre_summary_path),
        "calibre_pass_filter_receipt": _file_record(filter_receipt_path),
        "fresh_emx_summary": _file_record(fresh_summary_path),
        "fresh_touchstone_evidence": fresh_evidence,
        "merge_executed": False,
        "merge_artifact_count": 0,
        "training_executed": False,
        "controller_modified": False,
        "automatic_production_authorized": False,
        "automatic_production_acceptance_authorized": False,
        "automatic_merge_authorized": False,
        "external_solver_identity_boundary": external_runtime["solver_boundary"],
        "gds_structural_identity_boundary": (
            "The physical identity is a flattened polygon multiset plus base-marker "
            "identity; it is not a Boolean-union equivalence proof."
        ),
        "scientific_boundary": (
            "PASS proves an isolated exact-14 Cadence/GDS/physical-identity/Calibre chain "
            "and fresh real-EMX completion for exactly the zero-blocking Calibre subset. "
            "It does not merge data, authorize training or production, or prove high-|K|/Q reachability."
        ),
    }
    summary_path = out_dir / SUMMARY_NAME
    _write_json_atomic(summary_path, final_summary)
    state.update(
        {
            "overall_status": "PASS",
            "decision": final_summary["decision"],
            "finished_utc": finished_utc,
            "summary_path": str(summary_path),
            "summary_sha256": _sha256(summary_path),
            "immutable_file_sha256": dict(pins),
        }
    )
    _write_json_atomic(out_dir / STATE_NAME, state)
    return {
        "overall_status": "PASS",
        "fresh_emx_count": filtered_count,
        "summary_path": str(summary_path),
        "summary": final_summary,
    }


def _validate_builder_contract(
    *,
    builder_summary: dict[str, Any],
    builder_summary_path: Path,
    candidate_csv: Path,
    candidate_rows: list[dict[str, str]],
    candidate_fields: set[str],
    config_path: Path,
) -> None:
    artifacts = builder_summary.get("artifacts") or {}
    candidate_artifact = artifacts.get("candidate_csv") or {}
    sources = builder_summary.get("sources") or {}
    config_source = sources.get("frozen_config") or {}
    checks = {
        "builder_schema_exact": builder_summary.get("schema") == BUILDER_SCHEMA,
        "builder_overall_status_pass": builder_summary.get("overall_status") == "PASS",
        "builder_decision_exact": builder_summary.get("decision") == BUILDER_DECISION,
        "builder_did_not_stop_without_generation": builder_summary.get(
            "stop_without_generation"
        )
        is False,
        "builder_candidate_count_exact_14": builder_summary.get(
            "isolated_candidate_count"
        )
        == EXPECTED_COUNT,
        "builder_input_checks_all_pass": bool(builder_summary.get("input_checks"))
        and all((builder_summary.get("input_checks") or {}).values()),
        "builder_generation_checks_all_pass": bool(
            builder_summary.get("generation_checks")
        )
        and all((builder_summary.get("generation_checks") or {}).values()),
        "builder_failed_check_lists_empty": not builder_summary.get(
            "failed_input_checks"
        )
        and not builder_summary.get("failed_generation_checks"),
        "builder_summary_authorizations_explicit_false": all(
            _explicit_false(builder_summary.get(name)) for name in AUTOMATIC_SUMMARY_FLAGS
        ),
        "candidate_artifact_path_matches": _record_path(candidate_artifact, builder_summary_path)
        == candidate_csv,
        "candidate_artifact_hash_matches": str(
            candidate_artifact.get("sha256") or ""
        ).lower()
        == _sha256(candidate_csv),
        "config_source_path_matches": _record_path(config_source, builder_summary_path)
        == config_path,
        "config_source_hash_matches": str(config_source.get("sha256") or "").lower()
        == _sha256(config_path),
        "candidate_count_exact_14": len(candidate_rows) == EXPECTED_COUNT,
        "candidate_required_authorization_columns_present": set(
            AUTOMATIC_ROW_FLAGS
        ).issubset(candidate_fields),
        "candidate_authorizations_explicit_false": len(candidate_rows)
        == EXPECTED_COUNT
        and all(
            all(_explicit_false(row.get(name)) for name in AUTOMATIC_ROW_FLAGS)
            for row in candidate_rows
        ),
        "candidate_ids_and_hashes_unique": _candidate_identity_sets_valid(candidate_rows),
        "candidate_rows_unlabeled": all(
            str(row.get("label_status") or "").upper()
            == BUILDER_UNLABELED_STATUS
            and not str(row.get("real_emx_touchstone_path") or "").strip()
            for row in candidate_rows
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SupervisorFailure(f"builder contract failed: {failed}")


def _validate_cadence_summary(summary_path: Path, candidate_csv: Path) -> None:
    summary = _read_json(summary_path)
    cadence = summary.get("cadence_streamout_output_contract") or {}
    checks = {
        "status_pass": summary.get("overall_status") == "PASS",
        "candidate_source_sha_matches": str(
            (summary.get("candidate_source") or {}).get("sha256") or ""
        ).lower()
        == _sha256(candidate_csv),
        "counts_exact_14": all(
            summary.get(name) == EXPECTED_COUNT
            for name in (
                "input_row_count",
                "selected_row_count",
                "geometry_count",
                "result_count",
                "ok_count",
            )
        )
        and summary.get("fail_count") == 0,
        "cadence_only_flags_exact": summary.get("cadence_streamout_only") is True
        and summary.get("run_emx") is False
        and summary.get("create_only") is False,
        "cadence_contract_checked_exact_14": cadence.get("checked") is True
        and cadence.get("result_count") == EXPECTED_COUNT
        and cadence.get("valid_candidate_bound_gds_count") == EXPECTED_COUNT
        and cadence.get("touchstone_file_count") == 0,
        "all_summary_checks_pass": bool(summary.get("checks"))
        and all(check.get("pass") is True for check in summary.get("checks") or []),
    }
    _require_checks(checks, "Cadence streamout summary")


def _validate_binding_summary(summary_path: Path, index_path: Path) -> None:
    summary = _read_json(summary_path)
    checks = {
        "status_pass": summary.get("overall_status") == "PASS",
        "expected_count_14": summary.get("expected_count") == EXPECTED_COUNT,
        "counts_exact_14": all(
            summary.get(name) == EXPECTED_COUNT
            for name in ("candidate_count", "dataset_row_count", "gds_count", "pass_count")
        ),
        "all_checks_pass": bool(summary.get("checks"))
        and all((summary.get("checks") or {}).values()),
        "index_hash_matches": str((summary.get("index_csv") or {}).get("sha256") or "").lower()
        == _sha256(index_path),
        "production_acceptance_false": summary.get(
            "automatic_production_acceptance_authorized"
        )
        is False,
    }
    _require_checks(checks, "candidate GDS binding summary")


def _validate_physical_summary(summary_path: Path, audited_index_path: Path) -> None:
    summary = _read_json(summary_path)
    boundary = summary.get("timestamp_normalized_sha256_boundary") or {}
    checks = {
        "status_pass": summary.get("overall_status") == "PASS",
        "counts_exact_14": all(
            summary.get(name) == EXPECTED_COUNT
            for name in ("expected_count", "candidate_count", "index_count", "dataset_count", "pass_count")
        ),
        "all_checks_pass": bool(summary.get("checks"))
        and all((summary.get("checks") or {}).values()),
        "audited_index_hash_matches": str(
            (summary.get("audited_index_csv") or {}).get("sha256") or ""
        ).lower()
        == _sha256(audited_index_path),
        "normalized_hash_limit_explicit": boundary.get("physical_equivalence_proof")
        is False
        and boundary.get("physical_distinctness_proof") is False,
        "automatic_authorizations_false": all(
            summary.get(name) is False
            for name in (
                "automatic_calibre_authorized",
                "automatic_emx_authorized",
                "automatic_production_authorized",
                "automatic_merge_authorized",
            )
        ),
    }
    _require_checks(checks, "GDS physical identity summary")


def _filter_calibre_pass_candidates(
    *,
    candidate_csv: Path,
    audited_index_csv: Path,
    calibre_summary_path: Path,
    drc_index_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    summary = _read_json(calibre_summary_path)
    candidate_rows, _ = _read_csv(candidate_csv)
    audited_rows, _ = _read_csv(audited_index_csv)
    drc_rows, drc_fields = _read_csv(drc_index_path)
    candidate_by_hash = _unique_rows(candidate_rows, "candidate_id_sha256", "candidate")
    audited_by_hash = _unique_rows(audited_rows, "candidate_id_sha256", "audited GDS")
    drc_by_hash = _unique_rows(drc_rows, "candidate_id_sha256", "DRC")
    expected_ids = set(candidate_by_hash)
    required_drc_fields = {
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "gds_sha256",
        "gds_timestamp_normalized_sha256",
        "gds_timestamp_normalization_algorithm",
        "drc_summary_path",
        "drc_summary_sha256",
        "blocking_drc_violation_count",
        "overall_status",
    }
    preflight = {
        "calibre_batch_schema_exact": summary.get("schema") == CALIBRE_BATCH_SCHEMA,
        "calibre_drc_scope_exact": summary.get("drc_scope") == CALIBRE_DRC_SCOPE,
        "calibre_switch_policy_exact": summary.get("switch_policy")
        == CALIBRE_SWITCH_POLICY,
        "calibre_status_valid": summary.get("overall_status") in {"PASS", "FAIL"},
        "candidate_count_exact_14": len(candidate_rows) == EXPECTED_COUNT,
        "audited_index_count_exact_14": len(audited_rows) == EXPECTED_COUNT,
        "drc_index_count_exact_14": len(drc_rows) == EXPECTED_COUNT,
        "identity_sets_exact": set(audited_by_hash) == expected_ids
        and set(drc_by_hash) == expected_ids,
        "drc_fields_complete": required_drc_fields.issubset(drc_fields),
        "calibre_candidate_count_exact_14": summary.get("candidate_count")
        == EXPECTED_COUNT,
        "calibre_input_hash_matches_audited_index": str(
            summary.get("input_index_sha256") or ""
        ).lower()
        == _sha256(audited_index_csv),
        "calibre_drc_index_hash_matches": str(summary.get("drc_index_sha256") or "").lower()
        == _sha256(drc_index_path),
        "calibre_module_exact": summary.get("calibre_module") == CALIBRE_MODULE,
        "calibre_batch_prerequisite_checks_pass": bool(summary.get("checks"))
        and all((summary.get("checks") or {}).values()),
        "calibre_diagnostics_empty": not summary.get("diagnostics"),
        "calibre_automatic_execution_false": summary.get(
            "automatic_execution_authorized"
        )
        is False,
        "calibre_production_modification_false": summary.get(
            "production_campaign_modification_authorized"
        )
        is False,
    }
    _require_checks(preflight, "Calibre pass-filter preflight")

    selected_ids: set[str] = set()
    row_evidence: list[dict[str, Any]] = []
    for candidate_id_sha in sorted(expected_ids):
        candidate = candidate_by_hash[candidate_id_sha]
        audited = audited_by_hash[candidate_id_sha]
        drc = drc_by_hash[candidate_id_sha]
        status = str(drc.get("overall_status") or "").upper()
        if status not in {"PASS", "FAIL"}:
            raise SupervisorFailure(f"invalid Calibre row status for {candidate_id_sha}")
        geometry_sha = str(candidate.get("candidate_geometry_identity_sha256") or "").lower()
        blocking = _strict_integer(drc.get("blocking_drc_violation_count"))
        drc_summary_path = _resolve_artifact(drc_index_path, drc.get("drc_summary_path"))
        drc_payload = _read_json(drc_summary_path)
        row_checks = {
            "geometry_identity_matches": geometry_sha
            == str(audited.get("candidate_geometry_identity_sha256") or "").lower()
            == str(drc.get("candidate_geometry_identity_sha256") or "").lower(),
            "gds_sha_matches": str(audited.get("gds_sha256") or "").lower()
            == str(drc.get("gds_sha256") or "").lower(),
            "normalized_gds_sha_matches": str(
                audited.get("gds_timestamp_normalized_sha256") or ""
            ).lower()
            == str(drc.get("gds_timestamp_normalized_sha256") or "").lower(),
            "normalized_gds_algorithm_matches": str(
                audited.get("gds_timestamp_normalization_algorithm") or ""
            )
            == str(drc.get("gds_timestamp_normalization_algorithm") or "")
            == str(
                drc_payload.get("gds_timestamp_normalization_algorithm") or ""
            ),
            "physical_identity_audit_pass": str(
                audited.get("physical_identity_audit_status") or ""
            ).upper()
            == "PASS",
            "drc_summary_hash_matches": drc_summary_path.is_file()
            and _sha256(drc_summary_path)
            == str(drc.get("drc_summary_sha256") or "").lower(),
            "blocking_count_is_nonnegative_integer": blocking is not None
            and blocking >= 0,
            "drc_summary_candidate_matches": str(
                drc_payload.get("candidate_id_sha256") or ""
            ).lower()
            == candidate_id_sha,
            "drc_summary_geometry_matches": str(
                drc_payload.get("candidate_geometry_identity_sha256") or ""
            ).lower()
            == geometry_sha,
            "drc_summary_gds_matches": str(
                drc_payload.get("gds_sha256") or ""
            ).lower()
            == str(drc.get("gds_sha256") or "").lower(),
            "drc_summary_normalized_gds_matches": str(
                drc_payload.get("gds_timestamp_normalized_sha256") or ""
            ).lower()
            == str(drc.get("gds_timestamp_normalized_sha256") or "").lower(),
            "drc_summary_status_matches": str(
                drc_payload.get("overall_status") or ""
            ).upper()
            == status,
            "drc_summary_schema_exact": drc_payload.get("schema")
            == CALIBRE_ROW_SCHEMA,
            "drc_summary_scope_exact": drc_payload.get("drc_scope")
            == CALIBRE_DRC_SCOPE,
            "drc_summary_process_token_exact": drc_payload.get("process_token")
            == CALIBRE_PROCESS_TOKEN,
            "drc_summary_switch_policy_exact": drc_payload.get("switch_policy")
            == CALIBRE_SWITCH_POLICY,
            "drc_summary_engine_exact": drc_payload.get("drc_engine") == "Calibre",
            "drc_summary_checks_present_and_pass_for_selected": bool(
                drc_payload.get("checks")
            )
            and (
                status != "PASS"
                or all(value is True for value in drc_payload.get("checks", {}).values())
            ),
            "drc_summary_blocking_count_matches": _strict_integer(
                drc_payload.get("blocking_drc_violation_count")
            )
            == blocking,
            "drc_summary_automatic_execution_false": drc_payload.get(
                "automatic_execution_authorized"
            )
            is False,
            "drc_summary_production_modification_false": drc_payload.get(
                "production_campaign_modification_authorized"
            )
            is False,
        }
        _require_checks(row_checks, f"Calibre row {candidate_id_sha}")
        selected = status == "PASS" and blocking == 0
        if status == "PASS" and blocking != 0:
            raise SupervisorFailure(
                f"Calibre PASS row has nonzero blocking count: {candidate_id_sha}"
            )
        if selected:
            selected_ids.add(candidate_id_sha)
        row_evidence.append(
            {
                "candidate_id_sha256": candidate_id_sha,
                "candidate_geometry_identity_sha256": geometry_sha,
                "calibre_status": status,
                "blocking_drc_violation_count": blocking,
                "selected_for_fresh_emx": selected,
                "drc_summary_path": str(drc_summary_path),
                "drc_summary_sha256": _sha256(drc_summary_path),
            }
        )
    if not selected_ids:
        raise SupervisorFailure("zero Calibre PASS / zero-blocking candidates; EMX not run")
    selected_rows = [
        row
        for row in candidate_rows
        if str(row.get("candidate_id_sha256") or "").lower() in selected_ids
    ]
    pass_count = len(selected_rows)
    summary_counts_match = (
        summary.get("pass_count") == pass_count
        and summary.get("fail_count") == EXPECTED_COUNT - pass_count
    )
    if not summary_counts_match:
        raise SupervisorFailure("Calibre summary pass/fail counts differ from DRC index")
    expected_batch_status = "PASS" if pass_count == EXPECTED_COUNT else "FAIL"
    if summary.get("overall_status") != expected_batch_status:
        raise SupervisorFailure(
            "Calibre batch status is inconsistent with per-candidate statuses"
        )
    filtered_path = out_dir / FILTERED_CSV_NAME
    _write_csv_atomic(filtered_path, selected_rows)
    receipt = {
        "schema": "calibre_pass_zero_blocking_filter.v1",
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "RUN_FRESH_EMX_ONLY_FOR_CALIBRE_PASS_ZERO_BLOCKING_SUBSET",
        "input_candidate_count": len(candidate_rows),
        "calibre_pass_zero_blocking_count": pass_count,
        "calibre_nonpass_count": EXPECTED_COUNT - pass_count,
        "candidate_csv": _file_record(candidate_csv),
        "physically_audited_gds_index": _file_record(audited_index_csv),
        "calibre_summary": _file_record(calibre_summary_path),
        "drc_index": _file_record(drc_index_path),
        "filtered_candidate_csv": _file_record(filtered_path),
        "selected_candidate_id_sha256": sorted(selected_ids),
        "rows": row_evidence,
        "automatic_emx_authorized": False,
        "automatic_production_authorized": False,
        "automatic_merge_authorized": False,
    }
    receipt_path = out_dir / FILTER_RECEIPT_NAME
    _write_json_atomic(receipt_path, receipt)
    return {
        "pass_count": pass_count,
        "filtered_candidate_csv": str(filtered_path),
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }


def _validate_fresh_emx(
    *,
    summary_path: Path,
    dataset_rows_path: Path,
    filtered_candidate_csv: Path,
    audited_index_csv: Path,
    physical_audit_script: Path,
    runtime_repo_root: Path,
    fresh_dir: Path,
    expected_count: int,
) -> dict[str, Any]:
    if expected_count < 1 or expected_count > EXPECTED_COUNT:
        raise SupervisorFailure("fresh EMX expected count is outside 1..14")
    summary_file = _file_record(summary_path)
    dataset_rows_file = _file_record(dataset_rows_path)
    summary = _read_json(summary_path)
    filtered_rows, _ = _read_csv(filtered_candidate_csv)
    dataset_rows, dataset_fields = _read_csv(dataset_rows_path)
    audited_rows, _ = _read_csv(audited_index_csv)
    touchstone = summary.get("touchstone_output_contract") or {}
    filtered_by_hash = _unique_rows(
        filtered_rows, "candidate_id_sha256", "fresh EMX filtered candidate"
    )
    expected_ids = set(filtered_by_hash)
    expected_geometry_by_id = {
        str(row.get("candidate_id_sha256") or "").lower(): str(
            row.get("candidate_geometry_identity_sha256") or ""
        ).lower()
        for row in filtered_rows
    }
    observed_ids = {
        str(row.get("queue__candidate_id_sha256") or "").lower()
        for row in dataset_rows
    }
    audited_by_hash = _unique_rows(
        audited_rows, "candidate_id_sha256", "physically audited GDS"
    )
    physical_identity = _load_physical_identity_module(
        script_path=physical_audit_script,
        runtime_repo_root=runtime_repo_root,
    )
    touchstone_loader, touchstone_parser_record = _load_touchstone_parser(
        runtime_repo_root=runtime_repo_root,
    )
    dataset_by_hash = _unique_rows(
        dataset_rows, "queue__candidate_id_sha256", "fresh EMX dataset"
    )
    observed_geometry_by_id = {
        candidate_id: str(
            row.get("queue__candidate_geometry_identity_sha256") or ""
        ).lower()
        for candidate_id, row in dataset_by_hash.items()
    }
    audited_geometry_by_id = {
        candidate_id: str(
            row.get("candidate_geometry_identity_sha256") or ""
        ).lower()
        for candidate_id, row in audited_by_hash.items()
    }
    touchstone_records: list[dict[str, Any]] = []
    paths: list[Path] = []
    fresh_gds_records: list[dict[str, Any]] = []
    for candidate_id_sha in sorted(expected_ids):
        audited = audited_by_hash.get(candidate_id_sha) or {}
        dataset_row = dataset_by_hash.get(candidate_id_sha) or {}
        evaluation = str(dataset_row.get("evaluation") or "")
        evaluation_is_exact_cache_key = bool(re.fullmatch(r"[0-9a-f]{16}", evaluation))
        if not evaluation_is_exact_cache_key:
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} has invalid evaluation cache key: "
                f"{evaluation!r}"
            )
        expected_work_dir = (fresh_dir / "evaluations" / evaluation).resolve()
        raw_work_dir = Path(str(dataset_row.get("work_dir") or "")).expanduser()
        if not raw_work_dir.is_absolute():
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} work_dir is not absolute"
            )
        work_dir = raw_work_dir.resolve()
        if work_dir != expected_work_dir or not _is_within(work_dir, fresh_dir):
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} work_dir does not match "
                f"evaluation: {work_dir}"
            )
        raw_touchstone = Path(
            str(dataset_row.get("touchstone_path") or "")
        ).expanduser()
        if not raw_touchstone.is_absolute():
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} touchstone path is not absolute"
            )
        touchstone_path = raw_touchstone.resolve()
        expected_emx_dir = (work_dir / "emx").resolve()
        if not _is_within(touchstone_path, expected_emx_dir):
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} touchstone is not bound to "
                f"its evaluation/emx directory: {touchstone_path}"
            )
        paths.append(touchstone_path)
        touchstone_file = _file_record(touchstone_path)
        independent_parse = _independent_touchstone_parse(
            touchstone_path,
            loader=touchstone_loader,
        )
        if _sha256(touchstone_path) != touchstone_file["sha256"]:
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} touchstone drifted during parse"
            )
        touchstone_records.append(
            {
                "candidate_id_sha256": candidate_id_sha,
                "evaluation_cache_key": evaluation,
                "work_dir": str(work_dir),
                **touchstone_file,
                "independent_parse": independent_parse,
            }
        )
        raw_touchstone_value = str(dataset_row.get("raw_touchstone_path") or "").strip()
        if raw_touchstone_value:
            raw_source = Path(raw_touchstone_value).expanduser()
            if not raw_source.is_absolute() or not _is_within(
                raw_source.resolve(), expected_emx_dir
            ):
                raise SupervisorFailure(
                    f"fresh EMX candidate {candidate_id_sha} raw touchstone is not "
                    "bound to its evaluation/emx directory"
                )
        fresh_gds = (
            work_dir / "streamout" / "transformer_layout_cadpins.gds"
        ).resolve()
        if not fresh_gds.is_file() or not _is_within(fresh_gds, work_dir):
            raise SupervisorFailure(
                f"fresh EMX candidate {candidate_id_sha} expected GDS is missing or "
                f"escapes its evaluation: {fresh_gds}"
            )
        normalized_sha = physical_identity.gds_timestamp_normalized_sha256(
            fresh_gds
        )
        structure = physical_identity._gds_structural_identity(fresh_gds)
        fresh_gds_records.append(
            {
                "candidate_id_sha256": candidate_id_sha,
                "evaluation_cache_key": evaluation,
                "fresh_gds_path": str(fresh_gds),
                "fresh_gds_sha256": _sha256(fresh_gds),
                "fresh_gds_timestamp_normalized_sha256": normalized_sha,
                "calibre_audited_gds_timestamp_normalized_sha256": str(
                    audited.get("gds_timestamp_normalized_sha256") or ""
                ).lower(),
                "fresh_gds_structural_sha256": structure.get("structural_sha256"),
                "calibre_audited_gds_structural_sha256": str(
                    audited.get("candidate_physical_identity_sha256") or ""
                ).lower(),
                "normalized_stream_identity_matches": normalized_sha
                == str(
                    audited.get("gds_timestamp_normalized_sha256") or ""
                ).lower(),
                "physical_structural_identity_matches": structure.get(
                    "overall_status"
                )
                == "PASS"
                and structure.get("structural_sha256")
                == str(
                    audited.get("candidate_physical_identity_sha256") or ""
                ).lower(),
            }
        )
    checks = {
        "status_pass": summary.get("overall_status") == "PASS",
        "fresh_real_emx_mode": summary.get("run_emx") is True
        and summary.get("cadence_streamout_only") is False
        and summary.get("create_only") is False,
        "counts_exact_filtered_count": all(
            summary.get(name) == expected_count
            for name in (
                "input_row_count",
                "selected_row_count",
                "geometry_count",
                "result_count",
                "ok_count",
            )
        )
        and summary.get("fail_count") == 0,
        "candidate_source_hash_matches": str(
            (summary.get("candidate_source") or {}).get("sha256") or ""
        ).lower()
        == _sha256(filtered_candidate_csv),
        "dataset_count_exact": len(dataset_rows) == expected_count,
        "dataset_identity_columns_present": {
            "evaluation",
            "work_dir",
            "touchstone_path",
            "queue__candidate_id_sha256",
            "queue__candidate_geometry_identity_sha256",
        }.issubset(dataset_fields),
        "dataset_identity_set_exact": observed_ids == expected_ids
        and len(observed_ids) == expected_count,
        "candidate_to_geometry_mapping_exact_across_filter_fresh_and_calibre": (
            observed_geometry_by_id == expected_geometry_by_id
            and all(
                audited_geometry_by_id.get(candidate_id) == geometry_sha
                for candidate_id, geometry_sha in expected_geometry_by_id.items()
            )
        ),
        "physically_audited_identity_set_contains_exact_filtered_set": set(
            audited_by_hash
        ).issuperset(expected_ids)
        and len(expected_ids) == expected_count,
        "all_filtered_rows_have_passed_physical_gds_audit": all(
            str(
                (audited_by_hash.get(candidate_id) or {}).get(
                    "physical_identity_audit_status"
                )
                or ""
            ).upper()
            == "PASS"
            for candidate_id in expected_ids
        ),
        "all_dataset_rows_ok": all(_truthy(row.get("ok")) for row in dataset_rows),
        "touchstone_contract_exact": touchstone.get("checked") is True
        and touchstone.get("ok_row_count") == expected_count
        and touchstone.get("existing_file_count") == expected_count
        and touchstone.get("nonzero_file_count") == expected_count
        and touchstone.get("extension_match_count") == expected_count
        and touchstone.get("sampled_count") == expected_count
        and touchstone.get("parsed_count") == expected_count
        and touchstone.get("parse_error_count") == 0
        and touchstone.get("port_error_count") == 0
        and touchstone.get("frequency_error_count") == 0,
        "touchstone_paths_exact_and_fresh": len(paths) == expected_count
        and len(set(paths)) == expected_count
        and all(
            path.is_file()
            and path.stat().st_size > 0
            and path.suffix.lower() == ".s4p"
            and _is_within(path, fresh_dir)
            for path in paths
        ),
        "touchstone_hashes_unique": len(
            {record.get("sha256") for record in touchstone_records}
        )
        == expected_count,
        "touchstones_independently_reparsed_exact_4port_5_60_0p5": all(
            record["independent_parse"]["overall_status"] == "PASS"
            for record in touchstone_records
        ),
        "fresh_gds_count_exact": len(fresh_gds_records) == expected_count,
        "fresh_evaluation_cache_keys_unique": len(
            {record["evaluation_cache_key"] for record in fresh_gds_records}
        )
        == expected_count,
        "fresh_gds_paths_unique_and_in_fresh_output": len(
            {record["fresh_gds_path"] for record in fresh_gds_records}
        )
        == expected_count
        and all(
            _is_within(Path(record["fresh_gds_path"]), fresh_dir)
            for record in fresh_gds_records
        ),
        "fresh_gds_matches_calibre_audited_normalized_stream": all(
            record["normalized_stream_identity_matches"]
            for record in fresh_gds_records
        ),
        "fresh_gds_matches_calibre_audited_physical_structure": all(
            record["physical_structural_identity_matches"]
            for record in fresh_gds_records
        ),
        "all_summary_checks_pass": bool(summary.get("checks"))
        and all(check.get("pass") is True for check in summary.get("checks") or []),
    }
    _require_checks(checks, "fresh EMX summary")
    if (
        _sha256(summary_path) != summary_file["sha256"]
        or _sha256(dataset_rows_path) != dataset_rows_file["sha256"]
    ):
        raise SupervisorFailure("fresh EMX summary or dataset drifted during validation")
    return {
        "expected_count": expected_count,
        "summary": summary_file,
        "dataset_rows": dataset_rows_file,
        "touchstone_count": len(touchstone_records),
        "touchstone_parser": touchstone_parser_record,
        "touchstone_files": touchstone_records,
        "fresh_gds_reproducibility": fresh_gds_records,
        "checks": checks,
    }


def _run_stage(
    *,
    name: str,
    command: list[str],
    log_dir: Path,
    out_dir: Path,
    state: dict[str, Any],
    cwd: Path,
) -> None:
    stdout_path = log_dir / f"{len(state['stages']) + 1:02d}_{name}.stdout.log"
    stderr_path = log_dir / f"{len(state['stages']) + 1:02d}_{name}.stderr.log"
    started_monotonic = time.monotonic()
    record: dict[str, Any] = {
        "name": name,
        "started_utc": _utc_now(),
        "command": list(command),
        "cwd": str(cwd),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "status": "STARTING",
    }
    state["stages"].append(record)
    _write_json_atomic(out_dir / STATE_NAME, state)
    environment = os.environ.copy()
    # Do not allow an ambient PYTHONPATH/sitecustomize tree to participate in
    # the hash-pinned stage runtime.  Repository scripts add their frozen root
    # explicitly; the private direct runner is launched with ``-I -B``.
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONSTARTUP", None)
    environment.pop("PYTHONINSPECT", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = str(out_dir / ".runtime_pycache")
    environment["RFIC_STAGE2_RUNTIME_REPO_ROOT"] = str(cwd)
    interruption: BaseException | None = None
    return_code = -1
    previous_handlers = _install_stage_signal_handlers()
    process: subprocess.Popen[Any] | None = None
    try:
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    cwd=str(cwd),
                    env=environment,
                    start_new_session=True,
                )
                record.update({"pid": process.pid, "pgid": process.pid, "status": "RUNNING"})
                _write_json_atomic(out_dir / STATE_NAME, state)
                return_code = process.wait()
            except BaseException as exc:  # Kill the isolated group before lock handling.
                interruption = exc
                if process is not None:
                    _kill_and_reap_process_group(process)
                    return_code = (
                        process.returncode if process.returncode is not None else -9
                    )
                else:
                    return_code = -int(getattr(exc, "signum", signal.SIGKILL))
    finally:
        _restore_stage_signal_handlers(previous_handlers)
    record.update(
        {
            "finished_utc": _utc_now(),
            "elapsed_seconds": time.monotonic() - started_monotonic,
            "return_code": return_code,
            "stdout_sha256": _sha256(stdout_path),
            "stderr_sha256": _sha256(stderr_path),
            "status": "PASS" if return_code == 0 else "FAIL",
            "interruption": (
                None
                if interruption is None
                else f"{type(interruption).__name__}: {interruption}"
            ),
        }
    )
    _write_json_atomic(out_dir / STATE_NAME, state)
    if interruption is not None:
        raise interruption
    if return_code != 0:
        raise SupervisorFailure(f"stage {name} exited with status {return_code}")


def _kill_and_reap_process_group(process: subprocess.Popen[Any]) -> None:
    """Unconditionally kill and reap an interrupted start_new_session stage."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    while process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except KeyboardInterrupt:
            # Cleanup remains uninterruptible; the original interruption is
            # re-raised only after the process group has been reaped.
            continue


def _raise_stage_signal(signum: int, _frame: Any) -> None:
    raise StageSignalInterruption(signum)


def _install_stage_signal_handlers() -> dict[int, Any]:
    """Install temporary TERM/HUP/INT handlers when running in the main thread."""

    previous: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            previous[int(signum)] = signal.getsignal(signum)
            signal.signal(signum, _raise_stage_signal)
        except (ValueError, OSError):
            _restore_stage_signal_handlers(previous)
            return {}
    return previous


def _restore_stage_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            continue


def _attach_stage_artifacts(
    state: dict[str, Any],
    out_dir: Path,
    artifacts: dict[str, Path],
) -> None:
    if not state.get("stages"):
        raise SupervisorFailure("cannot attach artifacts without a stage record")
    state["stages"][-1]["artifacts"] = {
        name: _file_record(path) for name, path in artifacts.items()
    }
    if not all(record["exists"] and record["sha256"] for record in state["stages"][-1]["artifacts"].values()):
        raise SupervisorFailure("stage artifact is missing or unhashable")
    _write_json_atomic(out_dir / STATE_NAME, state)


def _prepare_stage(
    pins: dict[str, str],
    target_dir: Path,
    extra_executable_names: set[str] | None = None,
) -> None:
    _assert_pins(pins)
    _assert_no_conflicting_processes(
        extra_executable_names=extra_executable_names,
    )
    if target_dir.exists():
        raise SupervisorFailure(f"refusing existing stage directory: {target_dir}")


def _assert_no_conflicting_processes(
    *,
    process_rows: list[tuple[int, int, str]] | None = None,
    extra_executable_names: set[str] | None = None,
) -> None:
    rows = process_rows if process_rows is not None else _process_rows()
    excluded = _ancestor_pids(rows, os.getpid())
    conflicts = []
    for pid, _ppid, command in rows:
        if pid in excluded:
            continue
        if any(
            _command_contains_script(command, name) for name in STAGE_SCRIPT_NAMES
        ) or any(
            _command_contains_executable(command, name)
            for name in (
                *LOW_LEVEL_STAGE_EXECUTABLES,
                *(extra_executable_names or set()),
            )
        ):
            conflicts.append({"pid": pid, "command": command})
    if conflicts:
        raise SupervisorFailure(f"conflicting old stage processes detected: {conflicts}")


def _process_rows() -> list[tuple[int, int, str]]:
    result = subprocess.run(
        ["ps", "-eo", "uid=,pid=,ppid=,args="],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$", line)
        if match and int(match.group(1)) == (
            os.getuid() if hasattr(os, "getuid") else int(match.group(1))
        ):
            rows.append((int(match.group(2)), int(match.group(3)), match.group(4)))
    return rows


def _ancestor_pids(rows: list[tuple[int, int, str]], current_pid: int) -> set[int]:
    parents = {pid: ppid for pid, ppid, _command in rows}
    result = {current_pid}
    cursor = current_pid
    while cursor in parents and parents[cursor] > 0 and parents[cursor] not in result:
        cursor = parents[cursor]
        result.add(cursor)
    return result


def _command_contains_script(command: str, name: str) -> bool:
    return any(
        token == name or token.endswith(f"/{name}")
        for token in str(command).split()
    )


def _command_contains_executable(command: str, name: str) -> bool:
    for token in str(command).split():
        executable = Path(token).name
        if executable == name:
            return True
    return False


def _acquire_single_instance_lock(
    *,
    candidate_csv: Path,
    out_dir: Path,
    lock_root: Path = Path("/tmp"),
) -> dict[str, Any]:
    """Acquire a host/user-wide, crash-persistent fail-closed stage-2 lock.

    The fixed lock domain closes the process-scan TOCTOU window even when two
    supervisors target different output directories or candidate CSV paths.
    A killed supervisor deliberately leaves the lock behind: an operator must
    inspect the recorded PID/paths and remove it explicitly before any retry.
    """

    uid = os.getuid() if hasattr(os, "getuid") else 0
    lock_path = lock_root.resolve() / f"rfic_high_k_q_overlap_stage2_uid{uid}.lock"
    token = secrets.token_hex(32)
    payload = {
        "schema": "high_k_q_overlap_stage2_single_instance_lock.v1",
        "acquired_utc": _utc_now(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "uid": uid,
        "candidate_csv": str(candidate_csv.resolve()),
        "out_dir": str(out_dir.resolve()),
        "token": token,
    }
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as exc:
        existing: Any = "unreadable"
        try:
            existing_payload = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(existing_payload, dict):
                existing = {
                    key: value
                    for key, value in existing_payload.items()
                    if key != "token"
                }
        except (OSError, json.JSONDecodeError):
            pass
        raise SupervisorFailure(
            f"single-instance lock already exists: {lock_path}; "
            f"existing_receipt={existing}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SupervisorFailure("single-instance lock is not a regular file")
        payload.update(
            {
                "lock_device": int(opened.st_dev),
                "lock_inode": int(opened.st_ino),
            }
        )
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise SupervisorFailure("short write creating single-instance lock")
            offset += written
        os.fsync(descriptor)
    except Exception:
        try:
            lock_path.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    return {**payload, "path": str(lock_path)}


def _release_single_instance_lock(lock: dict[str, Any]) -> None:
    """Release only the exact token and inode created by this supervisor."""

    lock_path = Path(str(lock.get("path") or ""))
    expected_token = str(lock.get("token") or "")
    expected_identity = (
        int(lock.get("lock_device") or -1),
        int(lock.get("lock_inode") or -1),
    )
    if not expected_token or min(expected_identity) < 0:
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags)
    except OSError:
        return
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (int(opened.st_dev), int(opened.st_ino)) != expected_identity
        ):
            return
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        after_fd = os.fstat(descriptor)
        after_path = os.lstat(lock_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    finally:
        os.close(descriptor)
    if (
        not isinstance(payload, dict)
        or payload.get("token") != expected_token
        or (int(after_fd.st_dev), int(after_fd.st_ino)) != expected_identity
        or (int(after_path.st_dev), int(after_path.st_ino)) != expected_identity
    ):
        return
    try:
        final = os.lstat(lock_path)
        if (int(final.st_dev), int(final.st_ino)) != expected_identity:
            return
        lock_path.unlink()
    except OSError:
        return


def _runtime_source_paths(runtime_repo_root: Path) -> list[Path]:
    """Enumerate the complete executable source/resource tree to freeze."""

    roots = (
        runtime_repo_root / "scripts",
        runtime_repo_root / "rfic_transformer_inverse_design",
    )
    paths: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            raise SupervisorFailure(f"runtime source root is missing: {root}")
        for path in root.rglob("*"):
            relative_parts = path.relative_to(runtime_repo_root).parts
            if any(part in RUNTIME_SOURCE_EXCLUDED_PARTS for part in relative_parts):
                continue
            if path.is_symlink():
                raise SupervisorFailure(
                    f"runtime source tree contains an unpinned symlink: {path}"
                )
            if not path.is_file():
                continue
            if path.suffix.lower() in RUNTIME_SOURCE_EXCLUDED_SUFFIXES:
                continue
            if path.name.casefold() in {"sitecustomize.py", "usercustomize.py"} or (
                path.suffix.casefold() == ".pth"
            ):
                raise SupervisorFailure(
                    f"runtime source tree contains a Python startup hook: {path}"
                )
            paths.add(path.resolve())
    for root_file_name in ("pyproject.toml", "setup.py", "setup.cfg"):
        root_file = runtime_repo_root / root_file_name
        if root_file.is_file() and not root_file.is_symlink():
            paths.add(root_file.resolve())
    if not paths:
        raise SupervisorFailure(
            f"runtime source manifest would be empty: {runtime_repo_root}"
        )
    return sorted(paths, key=lambda value: str(value))


def _canonical_runtime_tree_manifest(runtime_repo_root: Path) -> dict[str, Any]:
    """Build the externally pinnable full scripts/package runtime closure.

    The digest is SHA-256 over one canonical JSON object followed by LF.  Only
    paths relative to the frozen repository are encoded, so an identical
    no-clobber deployment has the same root independent of its absolute path.
    """

    root = runtime_repo_root.resolve()
    records = sorted(
        [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in _runtime_source_paths(root)
        ],
        key=lambda item: item["path"],
    )
    if not records or records != sorted(records, key=lambda item: item["path"]):
        raise SupervisorFailure("canonical runtime-tree paths are not sorted/nonempty")
    relative_paths = [record["path"] for record in records]
    if len(relative_paths) != len(set(relative_paths)) or any(
        not _is_sha256(record["sha256"]) for record in records
    ):
        raise SupervisorFailure("canonical runtime-tree records are invalid/duplicate")
    payload = {
        "schema": RUNTIME_TREE_MANIFEST_SCHEMA,
        "files": records,
    }
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    return {
        **payload,
        "file_count": len(records),
        "aggregate_sha256": hashlib.sha256(encoded).hexdigest(),
        "canonical_encoding": "json-sort-keys-compact-utf8-plus-lf",
    }


def _runtime_environment_record(
    *,
    python_bin: Path,
    runtime_repo_root: Path,
) -> dict[str, Any]:
    """Resolve the selected venv and both Python/native gdstk artifacts."""

    probe = (
        "import json,pathlib,sys,gdstk,numpy; "
        "native=getattr(gdstk,'_gdstk'); "
        "print(json.dumps({"
        "'sys_executable':sys.executable,"
        "'prefix':sys.prefix,"
        "'base_prefix':sys.base_prefix,"
        "'python_major_minor':[sys.version_info.major,sys.version_info.minor],"
        "'numpy_version':str(numpy.__version__),"
        "'gdstk_version':str(gdstk.__version__),"
        "'gdstk_package_path':str(pathlib.Path(gdstk.__file__).resolve()),"
        "'gdstk_native_path':str(pathlib.Path(native.__file__).resolve())"
        "},sort_keys=True))"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONSTARTUP", None)
    environment.pop("PYTHONINSPECT", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(python_bin), "-I", "-B", "-c", probe],
        cwd=str(runtime_repo_root),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SupervisorFailure(
            "selected Python cannot resolve required gdstk runtime: "
            f"return_code={result.returncode}, stderr={result.stderr.strip()}"
        )
    try:
        payload = json.loads(result.stdout.strip())
        package_path = Path(str(payload["gdstk_package_path"])).resolve()
        native_path = Path(str(payload["gdstk_native_path"])).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SupervisorFailure(
            f"invalid gdstk runtime probe output: {result.stdout!r}"
        ) from exc
    for label, path in (("package", package_path), ("native", native_path)):
        if not path.is_file() or path.stat().st_size <= 0:
            raise SupervisorFailure(f"gdstk {label} runtime file is missing: {path}")
    return {
        "sys_executable": str(payload.get("sys_executable") or ""),
        "prefix": str(Path(str(payload.get("prefix") or "")).resolve()),
        "base_prefix": str(Path(str(payload.get("base_prefix") or "")).resolve()),
        "python_major_minor": list(payload.get("python_major_minor") or []),
        "numpy_version": str(payload.get("numpy_version") or ""),
        "gdstk_version": str(payload.get("gdstk_version") or ""),
        "dependencies": [
            {
                "name": "gdstk_package",
                "version": str(payload.get("gdstk_version") or ""),
                **_file_record(package_path),
            },
            {
                "name": "gdstk_native_extension",
                "version": str(payload.get("gdstk_version") or ""),
                **_file_record(native_path),
            },
        ],
    }


def _runtime_dependency_records(
    *,
    python_bin: Path,
    runtime_repo_root: Path,
) -> list[dict[str, Any]]:
    """Compatibility wrapper returning the selected runtime dependency files."""

    return list(
        _runtime_environment_record(
            python_bin=python_bin,
            runtime_repo_root=runtime_repo_root,
        )["dependencies"]
    )


def _config_external_runtime_record(
    *,
    config_path: Path,
    python_bin: Path,
    runtime_repo_root: Path,
) -> dict[str, Any]:
    """Resolve and hash external Cadence/EMX inputs named by the frozen config."""

    probe = (
        "import json,sys; sys.path.insert(0,sys.argv[2]); "
        "from rfic_transformer_inverse_design.api import load_run_config; "
        "cfg=load_run_config(sys.argv[1]); emx=cfg.emx; "
        "print(json.dumps({"
        "'execution_mode':str(emx.execution_mode),"
        "'emx_binary':str(emx.emx_binary),"
        "'emx_process_file':str(emx.emx_process_file),"
        "'cadence_pdk_cds_lib':str(emx.cadence_pdk_cds_lib),"
        "'cadence_layer_map':str(emx.cadence_layer_map),"
        "'cadence_install_root':str(emx.cadence_install_root),"
        "'emx_home':None if emx.emx_home is None else str(emx.emx_home),"
        "'extra_args':[str(value) for value in emx.extra_args]"
        "},sort_keys=True))"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONSTARTUP", None)
    environment.pop("PYTHONINSPECT", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            str(python_bin),
            "-I",
            "-B",
            "-c",
            probe,
            str(config_path),
            str(runtime_repo_root),
        ],
        cwd=str(runtime_repo_root),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SupervisorFailure(
            "cannot resolve external runtime from frozen config: "
            f"return_code={result.returncode}, stderr={result.stderr.strip()}"
        )
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise SupervisorFailure(
            f"invalid frozen-config runtime probe output: {result.stdout!r}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("execution_mode") != "local":
        raise SupervisorFailure(
            f"stage-2 requires local EMX execution mode: {payload!r}"
        )
    fields = (
        "emx_binary",
        "emx_process_file",
        "cadence_pdk_cds_lib",
        "cadence_layer_map",
    )
    files: list[dict[str, Any]] = []
    for field in fields:
        raw = str(payload.get(field) or "")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise SupervisorFailure(
                f"frozen config {field} must be an absolute path: {raw!r}"
            )
        path = Path(os.path.abspath(str(path)))
        if not path.is_file() or path.stat().st_size <= 0:
            raise SupervisorFailure(
                f"frozen config external runtime file is missing: {field}={path}"
            )
        if field == "emx_binary" and not os.access(path, os.X_OK):
            raise SupervisorFailure(f"EMX wrapper is not executable: {path}")
        files.append(
            {
                "role": field,
                "is_symlink": path.is_symlink(),
                "symlink_target": str(path.resolve()) if path.is_symlink() else None,
                **_file_record(path),
            }
        )
        payload[field] = str(path)
    cadence_install_root = Path(
        str(payload.get("cadence_install_root") or "")
    ).expanduser()
    if not cadence_install_root.is_absolute() or not cadence_install_root.is_dir():
        raise SupervisorFailure(
            f"Cadence install root is missing: {cadence_install_root}"
        )
    return {
        "schema": "high_k_q_overlap_stage2_external_runtime.v1",
        "execution_mode": "local",
        "emx_binary": str(payload["emx_binary"]),
        "cadence_install_root": str(cadence_install_root),
        "emx_home": payload.get("emx_home"),
        "extra_args": list(payload.get("extra_args") or []),
        "files": files,
        "solver_boundary": (
            "Hashes bind the configured EMX wrapper, process file, cds.lib, and "
            "Cadence layer map. A wrapper-selected container or module tree is not "
            "a solver-version proof unless that artifact is itself one of these files."
        ),
    }


def _assert_current_runtime_matches_selected(selected: dict[str, Any]) -> None:
    """Ensure in-process fresh-GDS hashing uses the selected stage venv."""

    try:
        current_gdstk = __import__("gdstk")
        current_native = getattr(current_gdstk, "_gdstk")
        current_numpy = __import__("numpy")
    except (ImportError, AttributeError) as exc:
        raise SupervisorFailure(
            f"supervisor interpreter cannot resolve the selected gdstk runtime: {exc}"
        ) from exc
    selected_dependencies = {
        str(record.get("name") or ""): Path(str(record.get("path") or "")).resolve()
        for record in selected.get("dependencies") or []
    }
    checks = {
        "sys_executable_matches": Path(os.path.abspath(sys.executable))
        == Path(os.path.abspath(str(selected.get("sys_executable") or ""))),
        "venv_prefix_matches": Path(sys.prefix).resolve()
        == Path(str(selected.get("prefix") or "")).resolve(),
        "base_prefix_matches": Path(sys.base_prefix).resolve()
        == Path(str(selected.get("base_prefix") or "")).resolve(),
        "python_major_minor_matches": list(selected.get("python_major_minor") or [])
        == [sys.version_info.major, sys.version_info.minor],
        "gdstk_package_matches": Path(str(current_gdstk.__file__)).resolve()
        == selected_dependencies.get("gdstk_package"),
        "gdstk_native_extension_matches": Path(str(current_native.__file__)).resolve()
        == selected_dependencies.get("gdstk_native_extension"),
        "gdstk_version_matches": str(getattr(current_gdstk, "__version__", ""))
        == str(selected.get("gdstk_version") or ""),
        "numpy_version_matches": str(getattr(current_numpy, "__version__", ""))
        == str(selected.get("numpy_version") or ""),
        "numpy_version_in_canonical_reference_set": str(
            getattr(current_numpy, "__version__", "")
        )
        in {"2.4.6", "2.5.0"},
    }
    _require_checks(checks, "selected Python runtime identity")


def _load_touchstone_parser(
    *,
    runtime_repo_root: Path,
) -> tuple[Any, dict[str, Any]]:
    """Load the pinned runtime parser and prove its module provenance."""

    if str(runtime_repo_root) not in sys.path:
        sys.path.insert(0, str(runtime_repo_root))
    try:
        module = __import__(
            "rfic_transformer_inverse_design.sim.touchstone",
            fromlist=["load_touchstone"],
        )
    except ImportError as exc:
        raise SupervisorFailure(f"cannot load pinned Touchstone parser: {exc}") from exc
    module_path = Path(str(getattr(module, "__file__", ""))).resolve()
    loader = getattr(module, "load_touchstone", None)
    if not _is_within(module_path, runtime_repo_root) or not callable(loader):
        raise SupervisorFailure(
            f"Touchstone parser is outside selected runtime or invalid: {module_path}"
        )
    return loader, _file_record(module_path)


def _independent_touchstone_parse(path: Path, *, loader: Any) -> dict[str, Any]:
    """Reparse one S4P and enforce the frozen 5..60 GHz / 0.5 GHz grid."""

    try:
        network = loader(path)
    except Exception as exc:
        raise SupervisorFailure(f"independent Touchstone parse failed for {path}: {exc}") from exc
    frequencies = [float(value) for value in network.freqs_hz]
    expected_frequencies = [5.0e9 + index * 0.5e9 for index in range(111)]
    matrix_values = [complex(value) for value in network.s_matrix.flat]
    checks = {
        "port_count_4": int(network.num_ports) == 4,
        "frequency_count_111": int(network.num_freqs) == 111,
        "frequency_grid_exact_within_1hz": len(frequencies) == 111
        and all(
            math.isfinite(observed) and abs(observed - expected) <= 1.0
            for observed, expected in zip(frequencies, expected_frequencies)
        ),
        "s_matrix_shape_exact": tuple(network.s_matrix.shape) == (111, 4, 4),
        "s_matrix_all_finite": bool(matrix_values)
        and all(
            math.isfinite(value.real) and math.isfinite(value.imag)
            for value in matrix_values
        ),
    }
    _require_checks(checks, f"independent Touchstone {path}")
    return {
        "overall_status": "PASS",
        "num_ports": int(network.num_ports),
        "num_frequencies": int(network.num_freqs),
        "frequency_start_hz": frequencies[0],
        "frequency_stop_hz": frequencies[-1],
        "frequency_step_hz": frequencies[1] - frequencies[0],
        "checks": checks,
    }


def _load_physical_identity_module(
    *,
    script_path: Path,
    runtime_repo_root: Path,
) -> Any:
    """Load the pinned auditor against the explicitly selected runtime tree."""

    script_path = script_path.resolve()
    runtime_repo_root = runtime_repo_root.resolve()
    if not script_path.is_file():
        raise SupervisorFailure(f"physical audit module is missing: {script_path}")
    package_root = runtime_repo_root / "rfic_transformer_inverse_design"
    if not package_root.is_dir():
        raise SupervisorFailure(
            f"physical audit runtime package is missing: {package_root}"
        )
    if str(runtime_repo_root) not in sys.path:
        sys.path.insert(0, str(runtime_repo_root))
    module_name = (
        "_stage2_physical_identity_"
        + hashlib.sha256(
            f"{script_path}\0{runtime_repo_root}".encode("utf-8")
        ).hexdigest()[:16]
    )
    for imported_name, imported_module in tuple(sys.modules.items()):
        if not (
            imported_name == "rfic_transformer_inverse_design"
            or imported_name.startswith("rfic_transformer_inverse_design.")
        ):
            continue
        imported_file = getattr(imported_module, "__file__", None)
        if imported_file and not _is_within(Path(imported_file), runtime_repo_root):
            raise SupervisorFailure(
                "physical audit dependency was already imported from outside the "
                f"selected runtime: {imported_name}={imported_file}"
            )
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise SupervisorFailure(
                f"cannot construct physical audit module spec: {script_path}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        previous_runtime_root = os.environ.get("RFIC_STAGE2_RUNTIME_REPO_ROOT")
        os.environ["RFIC_STAGE2_RUNTIME_REPO_ROOT"] = str(runtime_repo_root)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise SupervisorFailure(
                f"cannot load physical audit module {script_path}: {exc}"
            ) from exc
        finally:
            if previous_runtime_root is None:
                os.environ.pop("RFIC_STAGE2_RUNTIME_REPO_ROOT", None)
            else:
                os.environ["RFIC_STAGE2_RUNTIME_REPO_ROOT"] = previous_runtime_root
    for imported_name, imported_module in tuple(sys.modules.items()):
        if not (
            imported_name == "rfic_transformer_inverse_design"
            or imported_name.startswith("rfic_transformer_inverse_design.")
        ):
            continue
        imported_file = getattr(imported_module, "__file__", None)
        if imported_file and not _is_within(Path(imported_file), runtime_repo_root):
            raise SupervisorFailure(
                "physical audit dependency loaded from outside the selected runtime: "
                f"{imported_name}={imported_file}"
            )
    for function_name in (
        "gds_timestamp_normalized_sha256",
        "_gds_structural_identity",
    ):
        if not callable(getattr(module, function_name, None)):
            raise SupervisorFailure(
                f"physical audit module lacks required function {function_name}"
            )
    return module


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_manifest_binding_record(
    *,
    candidate_id_sha256: str,
    physical_receipt_path: Path,
    physical_receipt: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path, dict[str, bool]]:
    """Locate and semantically revalidate one original pre-Cadence manifest."""

    candidate_id = str(candidate_id_sha256 or "").lower()
    pre_raw = Path(str(physical_receipt.get("pre_cadence_gds_path") or "")).expanduser()
    pre_lexical = (
        pre_raw
        if pre_raw.is_absolute()
        else physical_receipt_path.parent / pre_raw
    )
    pre_gds = pre_lexical.resolve()
    manifest_path = pre_gds.parent / "transformer_layout.layout.json"
    payload = _read_json(manifest_path)
    expected_ports = [
        {
            "name": f"P{index:03d}",
            "signal_labels": [f"P{index:03d}"],
            "ground_labels": [f"P{index:03d}_G"],
        }
        for index in range(1, 5)
    ]
    ports = payload.get("ports")
    port_semantics = []
    if isinstance(ports, list):
        port_semantics = [
            {
                "name": str(port.get("name") or "") if isinstance(port, dict) else "",
                "signal_labels": (
                    list(port.get("signal_labels") or [])
                    if isinstance(port, dict)
                    else []
                ),
                "ground_labels": (
                    list(port.get("ground_labels") or [])
                    if isinstance(port, dict)
                    else []
                ),
            }
            for port in ports
        ]
    declared_layout = _resolve_artifact(manifest_path, payload.get("layout_path"))
    checks = {
        "candidate_id_sha256_valid": _is_sha256(candidate_id),
        "pre_cadence_gds_regular_non_symlink": pre_gds.is_file()
        and pre_gds.stat().st_size > 0
        and not _path_has_symlink_component(pre_lexical),
        "manifest_regular_non_symlink": manifest_path.is_file()
        and manifest_path.stat().st_size > 0
        and not _path_has_symlink_component(manifest_path),
        "manifest_layout_path_exact_pre_cadence_gds": declared_layout == pre_gds,
        "manifest_top_cell_exact": payload.get("top_cell") == "TRANSFORMER",
        "manifest_cadence_pin_purpose_exact": type(
            payload.get("cadence_pin_purpose")
        )
        is int
        and payload.get("cadence_pin_purpose") == 51,
        "manifest_port_count_exact_four": isinstance(ports, list)
        and len(ports) == 4,
        "manifest_name_signal_ground_contract_exact": port_semantics
        == expected_ports,
    }
    semantic_pass = all(checks.values())
    record = {
        "candidate_id_sha256": candidate_id,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "semantic_contract_pass": semantic_pass,
    }
    return record, pre_gds, manifest_path, checks


def _path_has_symlink_component(path: Path) -> bool:
    """Check an absolute lexical path without resolving away symlink evidence."""

    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _source_manifest_aggregate_sha256(records: list[dict[str, Any]]) -> str:
    """Canonical externally pinnable exact-14 manifest aggregate."""

    ordered = sorted(
        [
            {
                "candidate_id_sha256": str(record.get("candidate_id_sha256") or "").lower(),
                "manifest_path": str(record.get("manifest_path") or ""),
                "manifest_sha256": str(record.get("manifest_sha256") or "").lower(),
                "semantic_contract_pass": record.get("semantic_contract_pass") is True,
            }
            for record in records
        ],
        key=lambda record: record["candidate_id_sha256"],
    )
    encoded = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in ordered
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scan_fresh_output_tree(root: Path) -> dict[str, list[str]]:
    """Reject symlinks and any case variant of forbidden generated artifacts."""

    symlinks: list[str] = []
    gds_files: list[str] = []
    forbidden_directories: list[str] = []
    forbidden_names = {"cadence", "streamout", "calibre", "drc", "gds"}
    for path in root.rglob("*"):
        if path.is_symlink():
            symlinks.append(str(path))
            continue
        if path.is_file() and path.suffix.casefold() == ".gds":
            gds_files.append(str(path))
        if path.is_dir() and any(
            token in path.name.casefold() for token in forbidden_names
        ):
            forbidden_directories.append(str(path))
    return {
        "symlinks": sorted(symlinks),
        "gds_files": sorted(gds_files),
        "forbidden_directories": sorted(forbidden_directories),
    }


def _find_forbidden_merge_artifacts(out_dir: Path) -> list[str]:
    pattern = re.compile(r"(?:merged|accepted[_-]?pool|production[_-]?merge)", re.I)
    return [
        str(path)
        for path in out_dir.rglob("*")
        if path.is_file() and pattern.search(path.name)
    ]


def _pin_file(pins: dict[str, str], path: Path) -> None:
    # Keep lexical executable paths (notably venv/bin/python) so every stage
    # rehashes the entry point it will actually invoke.
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    digest = _sha256(absolute)
    if not digest:
        raise SupervisorFailure(f"cannot pin missing/empty-path file: {absolute}")
    pins[str(absolute)] = digest


def _pin_file_expected(
    pins: dict[str, str],
    path: Path,
    expected_sha256: str,
) -> None:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    actual = _sha256(absolute)
    if not _is_sha256(expected_sha256) or actual != expected_sha256:
        raise SupervisorFailure(
            f"validated artifact drift before pin: {absolute}; "
            f"expected={expected_sha256}, actual={actual}"
        )
    pins[str(absolute)] = expected_sha256


def _assert_pins(pins: dict[str, str]) -> None:
    drift: dict[str, dict[str, str]] = {}
    for path, expected in pins.items():
        actual = _sha256(Path(path))
        if actual != expected:
            drift[path] = {"expected": expected, "actual": actual}
    if drift:
        raise SupervisorFailure(f"immutable source/artifact drift detected: {drift}")


def _validate_expected_file(path: Path, expected_sha: str, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise SupervisorFailure(f"{label} missing or empty: {path}")
    actual = _sha256(path)
    if actual != str(expected_sha).lower():
        raise SupervisorFailure(
            f"{label} SHA-256 mismatch: expected {expected_sha}, actual {actual}"
        )


def _candidate_identity_sets_valid(rows: list[dict[str, str]]) -> bool:
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
    candidate_hashes = [
        str(row.get("candidate_id_sha256") or "").lower() for row in rows
    ]
    geometry_hashes = [
        str(row.get("candidate_geometry_identity_sha256") or "").lower()
        for row in rows
    ]
    return bool(rows) and all(candidate_ids) and all(
        _is_sha256(value) for value in (*candidate_hashes, *geometry_hashes)
    ) and all(
        len(values) == len(set(values))
        for values in (candidate_ids, candidate_hashes, geometry_hashes)
    )


def _unique_rows(
    rows: list[dict[str, str]], field: str, label: str
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        key = str(row.get(field) or "").lower()
        if not _is_sha256(key) or key in result:
            raise SupervisorFailure(f"{label} rows have invalid/duplicate {field}")
        result[key] = row
    return result


def _record_path(record: dict[str, Any], parent: Path) -> Path:
    value = str(record.get("path") or "")
    if not value:
        return Path("__missing__")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (parent.parent / path).resolve()


def _resolve_artifact(parent: Path, raw: Any) -> Path:
    value = str(raw or "").strip()
    if not value:
        return Path("__missing__")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (parent.parent / path).resolve()


def _strict_integer(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not re.fullmatch(r"-?\d+", text):
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def _explicit_false(value: Any) -> bool:
    return value is False or value == 0 or str(value).strip().lower() == "false"


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "pass",
        "ok",
    }


def _require_checks(checks: dict[str, bool], label: str) -> None:
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SupervisorFailure(f"{label} checks failed: {failed}")


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        return [], set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], set(reader.fieldnames or [])


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SupervisorFailure(f"JSON artifact missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorFailure(f"JSON artifact unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SupervisorFailure(f"JSON artifact is not an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else "",
    }


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
