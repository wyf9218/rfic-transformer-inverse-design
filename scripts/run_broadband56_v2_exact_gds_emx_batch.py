#!/usr/bin/env python3
"""Run a bounded batch of exact-GDS fresh-EMX candidates fail closed.

The delegated single-candidate runner remains the only implementation that
may invoke EMX.  This wrapper pins every input and delegate by SHA-256,
launches at most the authorized concurrency, and preserves a terminal PASS or
FAIL row for every submitted candidate.  Candidate failures do not become
labels and do not erase successful candidates from the same bounded attempt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (  # noqa: E402
    EXACT_GDS_EMX_FAILURE_NAME,
    EXACT_GDS_EMX_PASS_DECISION,
    EXACT_GDS_EMX_RECEIPT_NAME,
    EXACT_GDS_EMX_RECEIPT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402
from scripts.broadband56_emx_runtime import (  # noqa: E402
    EmxRuntimeIdentityError, launch_spec, load_identity,
)
from rfic_transformer_inverse_design.campaigns.broadband56_dispatch import (  # noqa: E402
    bounded_completed, stage_admission,
)


BATCH_RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_exact_audited_gds_fresh_emx_batch.v1"
)
BATCH_PASS_DECISION = "TERMINAL_PARTITION_EXACT_GDS_FRESH_EMX_ATTEMPT"
BATCH_RECEIPT_NAME = "EXACT_GDS_EMX_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "EXACT_GDS_EMX_RECEIPT_INDEX.csv"
FAILURE_INDEX_NAME = "EXACT_GDS_EMX_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "EXACT_GDS_EMX_DELEGATE_EVIDENCE_INDEX.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

REQUIRED_INPUT_FIELDS = (
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "gds_path",
    "gds_sha256",
    "manifest_path",
    "manifest_sha256",
    "calibre_receipt_path",
    "calibre_receipt_sha256",
)

PASS_INDEX_FIELDS = (
    "accepted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "exact_gds_emx_receipt_path",
    "exact_gds_emx_receipt_sha256",
)

FAILURE_INDEX_FIELDS = (
    "submitted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "terminal_stage",
    "return_code",
    "failure_path",
    "failure_sha256",
    "s4p_path",
    "s4p_sha256",
    "frequency_points",
    "fresh_real_emx",
    "error",
)

EVIDENCE_INDEX_FIELDS = (
    "submitted_sequence",
    "geometry_sha256",
    "candidate_id_sha256",
    "overall_status",
    "return_code",
    "started_utc",
    "finished_utc",
    "command_argv_sha256",
    "stdout_path",
    "stdout_sha256",
    "stderr_path",
    "stderr_sha256",
    "delegate_result_path",
    "delegate_result_sha256",
)


class ExactGdsEmxBatchError(RuntimeError):
    """Raised when a bounded attempt cannot be partitioned safely."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = run_batch(args, out_dir=out_dir)
    except (ExactGdsEmxBatchError, EmxRuntimeIdentityError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"decision={receipt['decision']}")
    print(f"submitted_count={receipt['submitted_count']}")
    print(f"emx_pass_count={receipt['emx_pass_count']}")
    print(f"emx_fail_count={receipt['emx_fail_count']}")
    print(f"receipt={out_dir / BATCH_RECEIPT_NAME}")
    return 0


def run_batch(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    if stage not in {item.name for item in STAGES}:
        raise ExactGdsEmxBatchError(f"unknown campaign stage: {stage}")
    backend_manifest_path = _regular_file(
        Path(args.backend_identity_manifest), "backend identity manifest"
    )
    backend_manifest_sha256 = _sha256(backend_manifest_path)
    backend_manifest = _read_json(backend_manifest_path, "backend identity manifest")
    _validate_backend_manifest(backend_manifest)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "Calibre zero-blocking batch receipt"
    )
    input_receipt_sha256 = _sha256(input_receipt_path)
    input_receipt = _read_json(
        input_receipt_path, "Calibre zero-blocking batch receipt"
    )
    _validate_input_role_receipt(input_receipt, expected_stage=stage)
    input_path = _file_from_record(input_receipt.get("pass_index"), "pass index")

    scripts = _mapping(backend_manifest.get("script_identities"), "script identities")
    runtimes = _mapping(backend_manifest.get("runtime_identities"), "runtime identities")
    self_path = _file_from_record(
        scripts.get("exact_audited_gds_emx_runner"), "batch runner identity"
    )
    if self_path != Path(__file__).resolve():
        raise ExactGdsEmxBatchError("batch runner self-identity mismatch")
    runner_path = _file_from_record(
        scripts.get("exact_audited_gds_emx_single_runner"),
        "single-candidate exact EMX runner identity",
    )
    module_path = _file_from_record(
        scripts.get("exact_audited_gds_emx_module"), "exact EMX module identity"
    )
    python_path = _file_from_record(
        runtimes.get("python_executable"), "Python executable identity"
    )
    config_path = _file_from_record(
        runtimes.get("private_configuration"), "private configuration identity"
    )
    requested_config = _regular_file(Path(args.config), "private configuration")
    if requested_config != config_path:
        raise ExactGdsEmxBatchError("private configuration path mismatches manifest")
    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(
        authorization,
        backend_manifest_sha256=backend_manifest_sha256,
    )
    _validate_input_role_bindings(
        input_receipt,
        backend_manifest_sha256=backend_manifest_sha256,
        authorization_sha256=_sha256(authorization_path),
    )
    input_sha256 = _sha256(input_path)
    config_sha256 = _sha256(config_path)
    authorization_sha256 = _sha256(authorization_path)
    runner_sha256 = _sha256(runner_path)
    module_sha256 = _sha256(module_path)
    python_sha256 = _sha256(python_path)
    if not os.access(python_path, os.X_OK):
        raise ExactGdsEmxBatchError("Python executable is not executable")
    runtime_identity_path = _file_from_record(
        backend_manifest.get("emx_python_runtime"), "EMX Python runtime identity"
    )
    runtime_identity_sha256 = _sha256(runtime_identity_path)
    runtime_identity = load_identity(runtime_identity_path, runtime_identity_sha256)
    if Path(runtime_identity["python_launcher"]["path"]) != python_path:
        raise ExactGdsEmxBatchError("EMX private Python differs from backend")
    if Path(runtime_identity["entrypoint"]["path"]) != runner_path:
        raise ExactGdsEmxBatchError("EMX entrypoint differs from backend")

    rows = _read_input_rows(input_path)
    if int(args.max_concurrency) < 1:
        raise ExactGdsEmxBatchError("max_concurrency must be positive")
    max_workers = int(args.max_concurrency)
    admission = stage_admission(max_workers)

    out_dir.mkdir(parents=True, mode=0o700)
    candidates_dir = out_dir / "candidates"
    candidates_dir.mkdir()
    results: list[dict[str, Any] | None] = [None] * len(rows)

    def invoke(index):
        return _run_one(
            row=rows[index], submitted_sequence=index + 1,
            candidates_dir=candidates_dir, python_path=python_path,
            runner_path=runner_path, module_path=module_path,
            config_path=config_path, authorization_path=authorization_path,
            config_sha256=config_sha256, authorization_sha256=authorization_sha256,
            runner_sha256=runner_sha256, module_sha256=module_sha256,
            runtime_identity_path=runtime_identity_path,
            runtime_identity_sha256=runtime_identity_sha256,
        )

    for index, future in bounded_completed(
        len(rows), invoke, max_workers=max_workers, admission=admission,
        receipt_dir=out_dir / "dispatch",
    ):
        try:
            results[index] = future.result()
        except Exception as exc:  # Preserve an internal candidate failure.
            results[index] = _exception_result(
                row=rows[index], submitted_sequence=index + 1,
                error=f"{type(exc).__name__}: {exc}",
            )

    terminal = [item for item in results if item is not None]
    if len(terminal) != len(rows):
        raise ExactGdsEmxBatchError("candidate terminal partition is incomplete")
    pass_results = [item for item in terminal if item["overall_status"] == "PASS"]
    fail_results = [item for item in terminal if item["overall_status"] != "PASS"]

    pass_rows = []
    for accepted_sequence, item in enumerate(pass_results, start=1):
        pass_rows.append(
            {
                "accepted_sequence": accepted_sequence,
                **{field: item[field] for field in PASS_INDEX_FIELDS[1:]},
            }
        )
    failure_rows = [
        {field: item.get(field, "") for field in FAILURE_INDEX_FIELDS}
        for item in fail_results
    ]
    evidence_rows = [
        {field: item.get(field, "") for field in EVIDENCE_INDEX_FIELDS}
        for item in terminal
    ]
    pass_index_path = out_dir / PASS_INDEX_NAME
    failure_index_path = out_dir / FAILURE_INDEX_NAME
    evidence_index_path = out_dir / EVIDENCE_INDEX_NAME
    _write_csv(pass_index_path, PASS_INDEX_FIELDS, pass_rows)
    _write_csv(failure_index_path, FAILURE_INDEX_FIELDS, failure_rows)
    _write_csv(evidence_index_path, EVIDENCE_INDEX_FIELDS, evidence_rows)

    _require_sha_unchanged(input_path, input_sha256, "input index")
    _require_sha_unchanged(config_path, config_sha256, "private configuration")
    _require_sha_unchanged(
        authorization_path,
        authorization_sha256,
        "FULL_CAMPAIGN receipt",
    )
    _require_sha_unchanged(runner_path, runner_sha256, "exact EMX runner")
    _require_sha_unchanged(module_path, module_sha256, "exact EMX module")
    _require_sha_unchanged(python_path, python_sha256, "Python executable")
    _require_sha_unchanged(
        backend_manifest_path,
        backend_manifest_sha256,
        "backend identity manifest",
    )
    _require_sha_unchanged(
        input_receipt_path,
        input_receipt_sha256,
        "Calibre zero-blocking batch receipt",
    )

    receipt = {
        "schema": BATCH_RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": BATCH_PASS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": len(rows),
        "terminal_count": len(terminal),
        "emx_pass_count": len(pass_results),
        "emx_fail_count": len(fail_results),
        "terminal_partition_complete": len(terminal) == len(rows),
        "accepted_count_equals_fresh_emx_pass_count": True,
        "proxy_or_historical_labels_used": False,
        "failed_candidates_counted_as_accepted": False,
        "max_concurrency": max_workers,
        "backend_identity_manifest": _file_record(backend_manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "input_index": _file_record(input_path),
        "private_configuration": _file_record(config_path),
        "full_campaign_authorization_receipt": _file_record(authorization_path),
        "runner_script": _file_record(runner_path),
        "runner_module": _file_record(module_path),
        "python_executable": _file_record(python_path),
        "emx_python_runtime": _file_record(runtime_identity_path),
        "pass_index": _file_record(pass_index_path),
        "failure_index": _file_record(failure_index_path),
        "delegate_evidence_index": _file_record(evidence_index_path),
        "simulator_action_taken": bool(rows),
    }
    receipt_path = out_dir / BATCH_RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(
        out_dir,
        [receipt_path, pass_index_path, failure_index_path, evidence_index_path],
    )
    return receipt


def _run_one(
    *,
    row: Mapping[str, str],
    submitted_sequence: int,
    candidates_dir: Path,
    python_path: Path,
    runner_path: Path,
    module_path: Path,
    config_path: Path,
    authorization_path: Path,
    config_sha256: str,
    authorization_sha256: str,
    runner_sha256: str,
    module_sha256: str,
    runtime_identity_path: Path,
    runtime_identity_sha256: str,
) -> dict[str, Any]:
    candidate_id = row["candidate_id_sha256"]
    geometry_sha = row["geometry_sha256"]
    candidate_dir = candidates_dir / f"{submitted_sequence:06d}_{candidate_id}"
    arguments = [
        "--config",
        str(config_path),
        "--expected-config-sha256",
        config_sha256,
        "--gds",
        row["gds_path"],
        "--expected-gds-sha256",
        row["gds_sha256"],
        "--manifest",
        row["manifest_path"],
        "--expected-manifest-sha256",
        row["manifest_sha256"],
        "--calibre-receipt",
        row["calibre_receipt_path"],
        "--expected-calibre-receipt-sha256",
        row["calibre_receipt_sha256"],
        "--full-campaign-receipt",
        str(authorization_path),
        "--expected-full-campaign-receipt-sha256",
        authorization_sha256,
        "--candidate-id-sha256",
        candidate_id,
        "--geometry-identity-sha256",
        geometry_sha,
        "--out-dir",
        str(candidate_dir),
        "--expected-runner-sha256",
        runner_sha256,
        "--expected-module-sha256",
        module_sha256,
    ]
    specification = launch_spec(runtime_identity_path, runtime_identity_sha256, arguments)
    command = specification["args"]
    command_digest = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    started_utc = _utc_now()
    completed = subprocess.run(
        **specification,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    finished_utc = _utc_now()
    candidate_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = candidate_dir / "BATCH_DELEGATE_STDOUT.log"
    stderr_path = candidate_dir / "BATCH_DELEGATE_STDERR.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    receipt_path = candidate_dir / EXACT_GDS_EMX_RECEIPT_NAME
    failure_path = candidate_dir / EXACT_GDS_EMX_FAILURE_NAME
    common = {
        "submitted_sequence": submitted_sequence,
        "geometry_sha256": geometry_sha,
        "candidate_id_sha256": candidate_id,
        "return_code": int(completed.returncode),
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "command_argv_sha256": command_digest,
        "stdout_path": str(stdout_path),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": _sha256(stderr_path),
    }
    if completed.returncode == 0 and receipt_path.is_file():
        try:
            receipt = _read_json(receipt_path, "exact EMX receipt")
            _validate_pass_receipt(
                receipt,
                candidate_id=candidate_id,
                geometry_sha=geometry_sha,
            )
            result = {
                "overall_status": "PASS",
                **common,
                "geometry_id": row["geometry_id"],
                "campaign_phase": row["campaign_phase"],
                "acquisition_source": row["acquisition_source"],
                "campaign_contract_fingerprint": row[
                    "campaign_contract_fingerprint"
                ],
                "exact_gds_emx_receipt_path": str(receipt_path),
                "exact_gds_emx_receipt_sha256": _sha256(receipt_path),
            }
            return _write_delegate_result(candidate_dir, result)
        except (ExactGdsEmxBatchError, OSError, json.JSONDecodeError) as exc:
            result = _failure_result(
                row=row,
                submitted_sequence=submitted_sequence,
                return_code=completed.returncode,
                failure_path=failure_path if failure_path.is_file() else None,
                candidate_dir=candidate_dir,
                error=f"invalid exact EMX PASS receipt: {exc}",
            )
            return _write_delegate_result(candidate_dir, {**result, **common})
    result = _failure_result(
        row=row,
        submitted_sequence=submitted_sequence,
        return_code=completed.returncode,
        failure_path=failure_path if failure_path.is_file() else None,
        candidate_dir=candidate_dir,
        error=_bounded_error(completed.stderr or completed.stdout),
    )
    return _write_delegate_result(candidate_dir, {**result, **common})


def _validate_pass_receipt(
    receipt: Mapping[str, Any],
    *,
    candidate_id: str,
    geometry_sha: str,
) -> None:
    checks = {
        "schema": receipt.get("schema") == EXACT_GDS_EMX_RECEIPT_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision") == EXACT_GDS_EMX_PASS_DECISION,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate_id": receipt.get("candidate_id_sha256") == candidate_id,
        "geometry_id": receipt.get("geometry_identity_sha256") == geometry_sha,
        "fresh_real_emx": receipt.get("fresh_real_emx_executed") is True,
        "proxy_or_historical": receipt.get("proxy_or_historical_label_used")
        is False,
        "simulator_action": receipt.get("simulator_action_taken") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExactGdsEmxBatchError(
            "exact EMX PASS receipt mismatch: " + ",".join(failed)
        )


def _failure_result(
    *,
    row: Mapping[str, str],
    submitted_sequence: int,
    return_code: int,
    failure_path: Path | None,
    candidate_dir: Path | None,
    error: str,
) -> dict[str, Any]:
    diagnostic = _diagnose_fresh_emx_failure(
        candidate_dir=candidate_dir,
        failure_path=failure_path,
    )
    return {
        "overall_status": "FAIL",
        "submitted_sequence": submitted_sequence,
        "geometry_id": row["geometry_id"],
        "geometry_sha256": row["geometry_sha256"],
        "candidate_id_sha256": row["candidate_id_sha256"],
        "terminal_stage": diagnostic["terminal_stage"],
        "return_code": int(return_code),
        "failure_path": str(failure_path) if failure_path else "",
        "failure_sha256": _sha256(failure_path) if failure_path else "",
        "s4p_path": diagnostic["s4p_path"],
        "s4p_sha256": diagnostic["s4p_sha256"],
        "frequency_points": diagnostic["frequency_points"],
        "fresh_real_emx": diagnostic["fresh_real_emx"],
        "error": error or "exact EMX runner failed without diagnostic text",
    }


def _exception_result(
    *, row: Mapping[str, str], submitted_sequence: int, error: str
) -> dict[str, Any]:
    return _failure_result(
        row=row,
        submitted_sequence=submitted_sequence,
        return_code=-1,
        failure_path=None,
        candidate_dir=None,
        error=error,
    )


def _diagnose_fresh_emx_failure(
    *,
    candidate_dir: Path | None,
    failure_path: Path | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "terminal_stage": "EMX_FAILURE",
        "s4p_path": "",
        "s4p_sha256": "",
        "frequency_points": 0,
        "fresh_real_emx": "false",
    }
    emx_attempted = False
    if failure_path is not None and failure_path.is_file():
        try:
            failure = _read_json(failure_path, "exact EMX failure receipt")
            emx_attempted = failure.get("emx_attempted") is True
        except (OSError, json.JSONDecodeError, ExactGdsEmxBatchError):
            return result
    if not emx_attempted or candidate_dir is None or not candidate_dir.is_dir():
        return result
    s4p_files = sorted(
        path.resolve()
        for path in candidate_dir.rglob("*.s4p")
        if path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    )
    if len(s4p_files) != 1:
        return result
    s4p_path = s4p_files[0]
    result.update(
        {
            "s4p_path": str(s4p_path),
            "s4p_sha256": _sha256(s4p_path),
            "fresh_real_emx": "true",
        }
    )
    try:
        touchstone = load_touchstone(s4p_path)
    except Exception:
        result["terminal_stage"] = "S4P_PARSING_FAILURE"
        return result
    result["frequency_points"] = int(touchstone.num_freqs)
    grid = tuple(int(round(value)) for value in touchstone.freqs_hz)
    if int(touchstone.num_ports) != 4:
        result["terminal_stage"] = "S4P_PARSING_FAILURE"
    elif grid != FREQUENCY_GRID_HZ:
        result["terminal_stage"] = "INCOMPLETE_FREQUENCY_FAILURE"
    else:
        result.update(
            {
                "s4p_path": "",
                "s4p_sha256": "",
                "frequency_points": 0,
                "fresh_real_emx": "false",
            }
        )
    return result


def _write_delegate_result(
    candidate_dir: Path, result: Mapping[str, Any]
) -> dict[str, Any]:
    result_path = candidate_dir / "BATCH_DELEGATE_RESULT.json"
    _write_json(result_path, result)
    return {
        **dict(result),
        "delegate_result_path": str(result_path),
        "delegate_result_sha256": _sha256(result_path),
    }


def _read_input_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = set(REQUIRED_INPUT_FIELDS) - fields
        if missing:
            raise ExactGdsEmxBatchError(
                f"input index lacks fields: {sorted(missing)}"
            )
        rows = [dict(row) for row in reader]
    candidates: set[str] = set()
    geometries: set[str] = set()
    for number, row in enumerate(rows, start=2):
        for field in REQUIRED_INPUT_FIELDS:
            if not str(row.get(field) or "").strip():
                raise ExactGdsEmxBatchError(
                    f"input index line {number} has empty {field}"
                )
        candidate = _sha256_value(row["candidate_id_sha256"], "candidate ID")
        geometry = _sha256_value(row["geometry_sha256"], "geometry identity")
        if candidate in candidates:
            raise ExactGdsEmxBatchError("input candidate identities are not unique")
        if geometry in geometries:
            raise ExactGdsEmxBatchError("input geometry identities are not unique")
        candidates.add(candidate)
        geometries.add(geometry)
        row["candidate_id_sha256"] = candidate
        row["geometry_sha256"] = geometry
        if (
            row["campaign_contract_fingerprint"]
            != SCIENTIFIC_CONTRACT_FINGERPRINT
        ):
            raise ExactGdsEmxBatchError("input campaign fingerprint mismatch")
        for path_field, sha_field, label in (
            ("gds_path", "gds_sha256", "GDS"),
            ("manifest_path", "manifest_sha256", "layout manifest"),
            (
                "calibre_receipt_path",
                "calibre_receipt_sha256",
                "Calibre zero-blocking receipt",
            ),
        ):
            _pinned_file(Path(row[path_field]), row[sha_field], label)
    return rows


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input-role-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--max-concurrency", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _validate_backend_manifest(manifest: Mapping[str, Any]) -> None:
    checks = {
        "campaign_id": manifest.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint_sha256": manifest.get(
            "contract_fingerprint_sha256"
        )
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExactGdsEmxBatchError(
            "backend identity manifest mismatch: " + ",".join(failed)
        )


def _validate_authorization(
    receipt: Mapping[str, Any],
    *,
    backend_manifest_sha256: str,
) -> None:
    checks = {
        "schema": receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION,
        "authorization_scope": receipt.get("authorization_scope")
        == FULL_CAMPAIGN_APPROVAL_SCOPE,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_identity": receipt.get("backend_identity_manifest", {}).get(
            "sha256"
        )
        == backend_manifest_sha256,
        "approved_by": bool(str(receipt.get("approved_by") or "").strip()),
        "emx_authorized": receipt.get("emx_authorized_within_current_stage")
        is True,
        "campaign_200k_authorized": receipt.get("campaign_200k_authorized")
        is True,
        "accepted_geometry_target": receipt.get("accepted_geometry_target")
        == TARGET_ACCEPTED_GEOMETRIES,
        "replenishment_authorized": receipt.get(
            "replenished_attempt_rounds_authorized"
        )
        is True,
        "replenishment_contract": receipt.get("attempt_replenishment_contract")
        == ATTEMPT_REPLENISHMENT_CONTRACT,
        "legacy_geometry_limit_absent": "simulator_geometry_limit" not in receipt,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExactGdsEmxBatchError(
            "FULL_CAMPAIGN authorization mismatch: " + ",".join(failed)
        )


def _validate_input_role_receipt(
    receipt: Mapping[str, Any], *, expected_stage: str
) -> None:
    checks = {
        "overall_status": receipt.get("overall_status") == "PASS",
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract_fingerprint_sha256": receipt.get(
            "contract_fingerprint_sha256"
        )
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": receipt.get("stage") == expected_stage,
        "pass_index": isinstance(receipt.get("pass_index"), Mapping),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExactGdsEmxBatchError(
            "input role receipt mismatch: " + ",".join(failed)
        )


def _validate_input_role_bindings(
    receipt: Mapping[str, Any],
    *,
    backend_manifest_sha256: str,
    authorization_sha256: str,
) -> None:
    checks = {
        "backend_identity": receipt.get("backend_identity_manifest", {}).get(
            "sha256"
        )
        == backend_manifest_sha256,
        "full_campaign_authorization": receipt.get(
            "full_campaign_authorization_receipt", {}
        ).get("sha256")
        == authorization_sha256,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ExactGdsEmxBatchError(
            "input role receipt binding mismatch: " + ",".join(failed)
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExactGdsEmxBatchError(f"{label} is not an object")
    return value


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ExactGdsEmxBatchError(f"{label} is missing or empty: {resolved}")
    return resolved


def _file_from_record(record: Any, label: str) -> Path:
    value = _mapping(record, f"{label} record")
    raw_path = str(value.get("path") or "").strip()
    if not raw_path or not Path(raw_path).is_absolute():
        raise ExactGdsEmxBatchError(f"{label} record path is not absolute")
    path = _regular_file(Path(raw_path), label)
    try:
        expected_size = int(value.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise ExactGdsEmxBatchError(
            f"{label} record size_bytes is invalid"
        ) from exc
    if expected_size <= 0 or path.stat().st_size != expected_size:
        raise ExactGdsEmxBatchError(f"{label} size mismatch")
    _require_sha_unchanged(path, value.get("sha256"), label)
    return path


def _pinned_file(path: Path, expected_sha256: str, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ExactGdsEmxBatchError(f"{label} is missing or empty: {resolved}")
    _require_sha_unchanged(resolved, expected_sha256, label)
    return resolved


def _require_sha_unchanged(path: Path, expected_sha256: str, label: str) -> None:
    expected = _sha256_value(expected_sha256, f"expected {label} SHA-256")
    if _sha256(path) != expected:
        raise ExactGdsEmxBatchError(f"{label} SHA-256 mismatch")


def _sha256_value(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(text) is None:
        raise ExactGdsEmxBatchError(f"{label} is not SHA-256")
    return text


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExactGdsEmxBatchError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ExactGdsEmxBatchError(f"{label} is not an object")
    return value


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[Mapping[str, Any]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def _write_sums(root: Path, paths: list[Path]) -> None:
    (root / SHA256SUMS_NAME).write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in paths),
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_error(value: str, *, limit: int = 4000) -> str:
    text = str(value or "").strip()
    return text[-limit:]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
