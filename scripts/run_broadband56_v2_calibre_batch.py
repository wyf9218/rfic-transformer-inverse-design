#!/usr/bin/env python3
"""Run the hash-bound Calibre batch delegate and partition candidate results."""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design import layout as _layout_package  # noqa: E402
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.campaigns.broadband56_gds_identity import (  # noqa: E402
    GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM,
    gds_timestamp_normalized_sha256,
)


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_calibre_batch.v1"
RECEIPT_NAME = "CALIBRE_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "CALIBRE_PASS_INDEX.csv"
FAILURE_INDEX_NAME = "CALIBRE_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "CALIBRE_EVIDENCE_INDEX.csv"
DELEGATE_INPUT_INDEX_NAME = "CALIBRE_DELEGATE_INPUT_INDEX.csv"
DELEGATE_GEOMETRY_AUDIT_DIR_NAME = "CALIBRE_DELEGATE_GEOMETRY_AUDITS"
DELEGATE_GEOMETRY_AUDIT_INDEX_NAME = (
    "CALIBRE_DELEGATE_GEOMETRY_AUDIT_INDEX.csv"
)
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

EXPECTED_DECK_MEMBER = "MAIN_DRC_TopMu/CLN65S_9M_6X1Z1U.26_2a"
EXPECTED_USER_GUIDE_MEMBER = "3_UserGuide.txt"
EXPECTED_PROCESS_TOKEN = "/TSMC65_05_12_26/"
EXPECTED_TOP_CELL = "TRANSFORMER"
EXPECTED_CALIBRE_MODULE = "mentor/old/2025"
DELEGATE_EXECUTION_MODE = (
    "importlib-main-with-current-contract-required-checks-v1"
)
LEGACY_GDS_HASH_MODULE_NAME = (
    "rfic_transformer_inverse_design.layout.gds_hash"
)
_MISSING_MODULE = object()

DELEGATE_REQUIRED_INPUT_FIELDS = (
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "gds_path",
    "gds_timestamp_normalized_sha256",
    "gds_timestamp_normalization_algorithm",
    "geometry_audit_path",
)
DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS = (
    "geometry_range_pass",
    "topology_pass",
    "line_width_sync_pass",
    "angle_45_135_pass",
    "ground_clearance_pass",
)
LEGACY_DELEGATE_REQUIRED_GEOMETRY_CHECKS = (
    *DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS,
    "foundry_layout_audit_pass",
    "manufacturing_grid_canonicalization_pass",
    "foundry_slotted_ground_frame_pass",
    "foundry_power_line_contract_pass",
    "foundry_via_stack_and_landing_pad_pass",
    "foundry_bridge_connection_pass",
)
SOURCE_NORMALIZATION_ALGORITHM_FIELD = (
    "gds_timestamp_normalized_sha256_algorithm"
)

DELEGATE_GEOMETRY_AUDIT_INDEX_FIELDS = (
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "source_geometry_audit_path",
    "source_geometry_audit_sha256",
    "gds_physical_identity_audit_path",
    "gds_physical_identity_audit_sha256",
    "evaluation_summary_path",
    "evaluation_summary_sha256",
    "delegate_geometry_audit_path",
    "delegate_geometry_audit_sha256",
)

FAILURE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "terminal_stage",
    "drc_summary_path",
    "drc_summary_sha256",
    "blocking_drc_violation_count",
    "error",
)

EVIDENCE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "overall_status",
    "drc_summary_path",
    "drc_summary_sha256",
    "drc_violation_count",
    "blocking_drc_violation_count",
    "documented_warning_count",
)


class CalibreBatchError(RuntimeError):
    """Raised when Calibre evidence cannot be partitioned exactly."""


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
    except (CalibreBatchError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"calibre_pass_count={receipt['calibre_pass_count']}")
    print(f"calibre_fail_count={receipt['calibre_fail_count']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input-role-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def run_batch(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    if stage not in {item.name for item in STAGES}:
        raise CalibreBatchError(f"unknown campaign stage: {stage}")
    manifest_path = _regular_file(
        Path(args.backend_identity_manifest), "backend identity manifest"
    )
    manifest_sha = _sha256(manifest_path)
    manifest = _read_json(manifest_path, "backend identity manifest")
    if not (
        manifest.get("campaign_id") == CAMPAIGN_ID
        and manifest.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CalibreBatchError("backend manifest campaign or contract mismatch")
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    runtimes = _mapping(manifest.get("runtime_identities"), "runtime identities")
    self_path = _identity_path(scripts.get("calibre_runner"), "Calibre batch role")
    if self_path != Path(__file__).resolve():
        raise CalibreBatchError("Calibre batch self-identity mismatch")
    delegate_path = _identity_path(
        scripts.get("calibre_batch_delegate"), "Calibre batch delegate"
    )
    python_path = _identity_path(runtimes.get("python_executable"), "Python runtime")
    archive_path = _identity_path(
        runtimes.get("calibre_foundry_archive"), "Calibre foundry archive"
    )
    rule_deck_path = _identity_path(
        runtimes.get("calibre_rule_deck"), "Calibre source rule deck"
    )
    user_guide_path = _identity_path(
        runtimes.get("calibre_user_guide"), "Calibre foundry user guide"
    )
    process_path = _identity_path(
        runtimes.get("emx_process_file"), "EMX process file"
    )
    if EXPECTED_PROCESS_TOKEN not in str(process_path):
        raise CalibreBatchError("EMX process path lacks the expected process token")
    if not os.access(python_path, os.X_OK):
        raise CalibreBatchError("Python runtime is not executable")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "GDS physical identity batch receipt"
    )
    input_receipt = _read_json(
        input_receipt_path, "GDS physical identity batch receipt"
    )
    if not (
        input_receipt.get("overall_status") == "PASS"
        and input_receipt.get("campaign_id") == CAMPAIGN_ID
        and input_receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and str(input_receipt.get("stage") or "").upper() == stage
    ):
        raise CalibreBatchError("GDS identity batch receipt identity mismatch")
    input_index_path = _identity_path(input_receipt.get("pass_index"), "GDS pass index")
    input_rows, input_fields = _read_csv(input_index_path)
    source_by_id: dict[str, dict[str, str]] = {}
    for row in input_rows:
        candidate = _sha_value(row.get("candidate_id_sha256"), "candidate")
        _sha_value(
            row.get("candidate_geometry_identity_sha256"), "candidate geometry"
        )
        if row.get("gds_physical_identity_status") != "PASS":
            raise CalibreBatchError("Calibre input contains non-PASS GDS identity")
        if candidate in source_by_id:
            raise CalibreBatchError("Calibre input candidate identities are duplicated")
        source_by_id[candidate] = row
    pinned = {
        "manifest": (manifest_path, manifest_sha),
        "delegate": (delegate_path, _sha256(delegate_path)),
        "python": (python_path, _sha256(python_path)),
        "archive": (archive_path, _sha256(archive_path)),
        "rule_deck": (rule_deck_path, _sha256(rule_deck_path)),
        "user_guide": (user_guide_path, _sha256(user_guide_path)),
        "process": (process_path, _sha256(process_path)),
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "input_index": (input_index_path, _sha256(input_index_path)),
    }
    out_dir.mkdir(parents=True, mode=0o700)
    delegate_rows, delegate_fields, audit_records, source_pins = (
        _prepare_delegate_input(
            input_rows,
            input_fields,
            out_dir=out_dir,
            process_path=process_path,
        )
    )
    pinned.update(source_pins)
    delegate_input_path = out_dir / DELEGATE_INPUT_INDEX_NAME
    _write_csv(delegate_input_path, delegate_fields, delegate_rows)
    delegate_audit_index_path = out_dir / DELEGATE_GEOMETRY_AUDIT_INDEX_NAME
    _write_csv(
        delegate_audit_index_path,
        list(DELEGATE_GEOMETRY_AUDIT_INDEX_FIELDS),
        audit_records,
    )
    pinned["delegate_input_index"] = (
        delegate_input_path,
        _sha256(delegate_input_path),
    )
    pinned["delegate_geometry_audit_index"] = (
        delegate_audit_index_path,
        _sha256(delegate_audit_index_path),
    )
    if not input_rows:
        pass_path = out_dir / PASS_INDEX_NAME
        failure_path = out_dir / FAILURE_INDEX_NAME
        evidence_path = out_dir / EVIDENCE_INDEX_NAME
        _write_csv(pass_path, input_fields, [])
        _write_csv(failure_path, list(FAILURE_FIELDS), [])
        _write_csv(evidence_path, list(EVIDENCE_FIELDS), [])
        for label, (path, digest) in pinned.items():
            _require_unchanged(path, digest, label)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "TERMINAL_PARTITION_FOUNDRY_CALIBRE_DRC",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "stage": stage,
            "submitted_count": 0,
            "terminal_count": 0,
            "calibre_pass_count": 0,
            "calibre_fail_count": 0,
            "failed_candidates_counted_as_accepted": False,
            "backend_identity_manifest": _file_record(manifest_path),
            "input_role_receipt": _file_record(input_receipt_path),
            "delegate_input_index": _file_record(delegate_input_path),
            "delegate_geometry_audit_index": _file_record(
                delegate_audit_index_path
            ),
            "effective_delegate_required_geometry_checks": list(
                DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS
            ),
            "full_campaign_authorization_receipt": _file_record(
                authorization_path
            ),
            "pass_index": _file_record(pass_path),
            "failure_index": _file_record(failure_path),
            "evidence_index": _file_record(evidence_path),
            "delegate_summary": None,
            "delegate_drc_index": None,
            "delegate_return_code": 0,
            "delegate_command_argv_sha256": hashlib.sha256(b"[]").hexdigest(),
            "delegate_stdout": None,
            "delegate_stderr": None,
            "delegate_skipped_reason": "no GDS-identity-pass candidates",
            "calibre_foundry_archive": _file_record(archive_path),
            "calibre_source_rule_deck": _file_record(rule_deck_path),
            "calibre_foundry_user_guide": _file_record(user_guide_path),
            "simulator_action_taken": False,
        }
        receipt_path = out_dir / RECEIPT_NAME
        _write_json(receipt_path, receipt)
        _write_sums(out_dir)
        return receipt
    delegate_dir = out_dir / "delegate_run"
    delegate_args = [
        "--input-index-csv",
        str(delegate_input_path),
        "--out-dir",
        str(delegate_dir),
        "--foundry-archive",
        str(archive_path),
        "--deck-member",
        EXPECTED_DECK_MEMBER,
        "--user-guide-member",
        EXPECTED_USER_GUIDE_MEMBER,
        "--expected-archive-sha256",
        pinned["archive"][1],
        "--expected-deck-sha256",
        pinned["rule_deck"][1],
        "--expected-user-guide-sha256",
        pinned["user_guide"][1],
        "--expected-process-token",
        EXPECTED_PROCESS_TOKEN,
        "--expected-top-cell",
        EXPECTED_TOP_CELL,
        "--calibre-module",
        EXPECTED_CALIBRE_MODULE,
        "--calibre-command",
        "calibre",
        "--nice-level",
        "19",
        "--maximum-candidates",
        str(len(input_rows)),
        "--no-fail-exit",
    ]
    command = [
        str(python_path),
        str(delegate_path),
        *delegate_args,
    ]
    command_sha = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    execution_contract = {
        "execution_mode": DELEGATE_EXECUTION_MODE,
        "delegate_sha256": pinned["delegate"][1],
        "delegate_command_argv_sha256": command_sha,
        "legacy_required_geometry_checks": list(
            LEGACY_DELEGATE_REQUIRED_GEOMETRY_CHECKS
        ),
        "effective_required_geometry_checks": list(
            DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS
        ),
    }
    execution_contract_sha = hashlib.sha256(
        json.dumps(
            execution_contract,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = _run_delegate_with_current_contract(
        delegate_path,
        delegate_args,
    )
    stdout_path = out_dir / "delegate_stdout.log"
    stderr_path = out_dir / "delegate_stderr.log"
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        raise CalibreBatchError(
            f"Calibre delegate exited with return code {result.returncode}"
        )
    summary_path = _regular_file(
        delegate_dir / "tsmc65_calibre_macro_drc_batch_summary.json",
        "Calibre delegate summary",
    )
    summary = _read_json(summary_path, "Calibre delegate summary")
    if not (
        summary.get("schema") == "tsmc65_calibre_macro_ip_back_end_drc_batch_v1"
        and _integer(summary.get("candidate_count"), "candidate_count")
        == len(input_rows)
        and _integer(summary.get("pass_count"), "pass_count")
        + _integer(summary.get("fail_count"), "fail_count")
        == len(input_rows)
        and summary.get("foundry_archive_sha256") == pinned["archive"][1]
        and summary.get("foundry_source_deck_sha256") == pinned["rule_deck"][1]
        and summary.get("foundry_user_guide_sha256") == pinned["user_guide"][1]
    ):
        raise CalibreBatchError("Calibre delegate summary contract mismatch")
    drc_index_path = _regular_file(
        Path(str(summary.get("drc_index_csv") or "")), "Calibre DRC index"
    )
    if summary.get("drc_index_sha256") != _sha256(drc_index_path):
        raise CalibreBatchError("Calibre DRC index SHA mismatch")
    drc_rows, drc_fields = _read_csv(drc_index_path)
    if len(drc_rows) != len(input_rows):
        raise CalibreBatchError("Calibre DRC index is not a complete partition")

    pass_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sequence, drc in enumerate(drc_rows, start=1):
        candidate = _sha_value(drc.get("candidate_id_sha256"), "candidate")
        geometry = _sha_value(
            drc.get("candidate_geometry_identity_sha256"), "candidate geometry"
        )
        if candidate in seen or candidate not in source_by_id:
            raise CalibreBatchError("Calibre DRC candidate set mismatch")
        seen.add(candidate)
        source = source_by_id[candidate]
        if source.get("candidate_geometry_identity_sha256") != geometry:
            raise CalibreBatchError("Calibre DRC geometry identity mismatch")
        status = str(drc.get("overall_status") or "")
        if status not in {"PASS", "FAIL"}:
            raise CalibreBatchError("Calibre DRC status is not terminal")
        summary_candidate_path = Path(str(drc.get("drc_summary_path") or "")).expanduser().resolve()
        summary_candidate_sha = ""
        if summary_candidate_path.is_file():
            summary_candidate_sha = _sha256(summary_candidate_path)
            if drc.get("drc_summary_sha256") != summary_candidate_sha:
                raise CalibreBatchError("candidate Calibre summary SHA mismatch")
            candidate_summary = _read_json(
                summary_candidate_path, "candidate Calibre summary"
            )
            if not (
                candidate_summary.get("overall_status") == status
                and candidate_summary.get("candidate_id_sha256") == candidate
                and candidate_summary.get("candidate_geometry_identity_sha256")
                == geometry
            ):
                raise CalibreBatchError("candidate Calibre summary identity mismatch")
        elif status == "PASS":
            raise CalibreBatchError("PASS candidate lacks a Calibre summary")
        evidence.append(
            {
                "submitted_sequence": sequence,
                "candidate_id_sha256": candidate,
                "candidate_geometry_identity_sha256": geometry,
                "overall_status": status,
                "drc_summary_path": str(summary_candidate_path)
                if summary_candidate_path.is_file()
                else "",
                "drc_summary_sha256": summary_candidate_sha,
                "drc_violation_count": drc.get("drc_violation_count", ""),
                "blocking_drc_violation_count": drc.get(
                    "blocking_drc_violation_count", ""
                ),
                "documented_warning_count": drc.get(
                    "documented_warning_count", ""
                ),
            }
        )
        if status == "PASS":
            if _integer(
                drc.get("blocking_drc_violation_count"),
                "blocking_drc_violation_count",
            ) != 0:
                raise CalibreBatchError("PASS candidate has blocking DRC violations")
            pass_rows.append({**source, **drc})
        else:
            failures.append(
                {
                    "submitted_sequence": sequence,
                    "candidate_id_sha256": candidate,
                    "candidate_geometry_identity_sha256": geometry,
                    "terminal_stage": "calibre",
                    "drc_summary_path": str(summary_candidate_path)
                    if summary_candidate_path.is_file()
                    else "",
                    "drc_summary_sha256": summary_candidate_sha,
                    "blocking_drc_violation_count": drc.get(
                        "blocking_drc_violation_count", ""
                    ),
                    "error": str(drc.get("error") or "Calibre candidate failed"),
                }
            )
    if seen != set(source_by_id):
        raise CalibreBatchError("Calibre DRC index omitted input candidates")

    pass_path = out_dir / PASS_INDEX_NAME
    failure_path = out_dir / FAILURE_INDEX_NAME
    evidence_path = out_dir / EVIDENCE_INDEX_NAME
    pass_fields = list(dict.fromkeys([*input_fields, *drc_fields]))
    _write_csv(pass_path, pass_fields, pass_rows)
    _write_csv(failure_path, list(FAILURE_FIELDS), failures)
    _write_csv(evidence_path, list(EVIDENCE_FIELDS), evidence)
    for label, (path, digest) in pinned.items():
        _require_unchanged(path, digest, label)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "TERMINAL_PARTITION_FOUNDRY_CALIBRE_DRC",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": len(input_rows),
        "terminal_count": len(drc_rows),
        "calibre_pass_count": len(pass_rows),
        "calibre_fail_count": len(failures),
        "failed_candidates_counted_as_accepted": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "delegate_input_index": _file_record(delegate_input_path),
        "delegate_geometry_audit_index": _file_record(
            delegate_audit_index_path
        ),
        "effective_delegate_required_geometry_checks": list(
            DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS
        ),
        "full_campaign_authorization_receipt": _file_record(
            authorization_path
        ),
        "pass_index": _file_record(pass_path),
        "failure_index": _file_record(failure_path),
        "evidence_index": _file_record(evidence_path),
        "delegate_summary": _file_record(summary_path),
        "delegate_drc_index": _file_record(drc_index_path),
        "delegate_return_code": int(result.returncode),
        "delegate_command_argv_sha256": command_sha,
        "delegate_execution_mode": DELEGATE_EXECUTION_MODE,
        "delegate_execution_contract_sha256": execution_contract_sha,
        "legacy_delegate_required_geometry_checks": list(
            LEGACY_DELEGATE_REQUIRED_GEOMETRY_CHECKS
        ),
        "delegate_stdout": _file_record(stdout_path),
        "delegate_stderr": _file_record(stderr_path),
        "calibre_foundry_archive": _file_record(archive_path),
        "calibre_source_rule_deck": _file_record(rule_deck_path),
        "calibre_foundry_user_guide": _file_record(user_guide_path),
        "simulator_action_taken": True,
    }
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(out_dir)
    return receipt


def _validate_authorization(
    receipt: Mapping[str, Any], *, manifest_sha: str
) -> None:
    if not (
        receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION
        and receipt.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and receipt.get("backend_identity_manifest", {}).get("sha256")
        == manifest_sha
        and receipt.get("calibre_authorized_within_current_stage") is True
    ):
        raise CalibreBatchError("FULL_CAMPAIGN Calibre authorization mismatch")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibreBatchError(f"{label} is not an object")
    return value


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if (
        record.get("size_bytes") != path.stat().st_size
        or record.get("sha256") != _sha256(path)
    ):
        raise CalibreBatchError(f"{label} identity mismatch")
    return path


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise CalibreBatchError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalibreBatchError(f"{label} is not a JSON object")
    return value


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _prepare_delegate_input(
    rows: list[dict[str, str]],
    fields: list[str],
    *,
    out_dir: Path,
    process_path: Path,
) -> tuple[
    list[dict[str, str]],
    list[str],
    list[dict[str, str]],
    dict[str, tuple[Path, str]],
]:
    delegate_fields = list(fields)
    if "gds_timestamp_normalization_algorithm" not in delegate_fields:
        delegate_fields.append("gds_timestamp_normalization_algorithm")
    for field in (
        "source_geometry_audit_path",
        "source_geometry_audit_sha256",
        "source_gds_physical_identity_audit_path",
        "source_gds_physical_identity_audit_sha256",
    ):
        if field not in delegate_fields:
            delegate_fields.append(field)

    prepared: list[dict[str, str]] = []
    audit_records: list[dict[str, str]] = []
    source_pins: dict[str, tuple[Path, str]] = {}
    delegate_audit_dir = out_dir / DELEGATE_GEOMETRY_AUDIT_DIR_NAME
    if rows:
        delegate_audit_dir.mkdir()
    for row_number, row in enumerate(rows, start=2):
        prepared_row = dict(row)
        normalized_algorithm = str(
            prepared_row.get("gds_timestamp_normalization_algorithm")
            or prepared_row.get(SOURCE_NORMALIZATION_ALGORITHM_FIELD)
            or ""
        )
        source_algorithm = str(
            prepared_row.get(SOURCE_NORMALIZATION_ALGORITHM_FIELD) or ""
        )
        if (
            source_algorithm
            and normalized_algorithm
            and source_algorithm != normalized_algorithm
        ):
            raise CalibreBatchError(
                f"Calibre input row {row_number} has conflicting normalized-GDS "
                "algorithm fields"
            )
        if normalized_algorithm != GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM:
            raise CalibreBatchError(
                f"Calibre input row {row_number} has an unsupported normalized-GDS "
                "algorithm"
            )
        prepared_row["gds_timestamp_normalization_algorithm"] = (
            normalized_algorithm
        )
        missing = [
            field
            for field in DELEGATE_REQUIRED_INPUT_FIELDS
            if not str(prepared_row.get(field) or "")
        ]
        if missing:
            raise CalibreBatchError(
                f"Calibre input row {row_number} lacks delegate fields: {missing}"
            )
        _sha_value(
            prepared_row.get("gds_timestamp_normalized_sha256"),
            "normalized GDS",
        )
        candidate = _sha_value(
            prepared_row.get("candidate_id_sha256"), "candidate"
        )
        geometry = _sha_value(
            prepared_row.get("candidate_geometry_identity_sha256"),
            "candidate geometry",
        )
        source_audit_path = _regular_file(
            Path(str(prepared_row.get("geometry_audit_path") or "")),
            "source geometry audit",
        )
        source_audit_sha = _sha256(source_audit_path)
        recorded_source_audit_sha = str(
            prepared_row.get("geometry_audit_sha256") or ""
        ).strip().lower()
        if (
            recorded_source_audit_sha
            and recorded_source_audit_sha != source_audit_sha
        ):
            raise CalibreBatchError(
                f"Calibre input row {row_number} source geometry-audit SHA mismatch"
            )
        physical_audit_path = _regular_file(
            Path(
                str(
                    prepared_row.get("gds_physical_identity_audit_path") or ""
                )
            ),
            "GDS physical identity audit",
        )
        physical_audit_sha = _sha256(physical_audit_path)
        if (
            str(
                prepared_row.get("gds_physical_identity_audit_sha256") or ""
            ).strip().lower()
            != physical_audit_sha
        ):
            raise CalibreBatchError(
                f"Calibre input row {row_number} physical-identity audit SHA mismatch"
            )
        source_audit = _read_json(source_audit_path, "source geometry audit")
        physical_audit = _read_json(
            physical_audit_path, "GDS physical identity audit"
        )
        compatibility_audit = _current_contract_delegate_geometry_audit(
            row=prepared_row,
            source_audit=source_audit,
            source_audit_path=source_audit_path,
            physical_audit=physical_audit,
            physical_audit_path=physical_audit_path,
            process_path=process_path,
        )
        delegate_audit_path = delegate_audit_dir / f"{candidate}.json"
        _write_json(delegate_audit_path, compatibility_audit)
        delegate_audit_sha = _sha256(delegate_audit_path)
        prepared_row["source_geometry_audit_path"] = str(source_audit_path)
        prepared_row["source_geometry_audit_sha256"] = source_audit_sha
        prepared_row["source_gds_physical_identity_audit_path"] = str(
            physical_audit_path
        )
        prepared_row["source_gds_physical_identity_audit_sha256"] = (
            physical_audit_sha
        )
        prepared_row["geometry_audit_path"] = str(delegate_audit_path)
        prepared_row["geometry_audit_sha256"] = delegate_audit_sha
        audit_records.append(
            {
                "candidate_id_sha256": candidate,
                "candidate_geometry_identity_sha256": geometry,
                "source_geometry_audit_path": str(source_audit_path),
                "source_geometry_audit_sha256": source_audit_sha,
                "gds_physical_identity_audit_path": str(physical_audit_path),
                "gds_physical_identity_audit_sha256": physical_audit_sha,
                "evaluation_summary_path": compatibility_audit[
                    "source_evidence"
                ]["evaluation_summary"]["path"],
                "evaluation_summary_sha256": compatibility_audit[
                    "source_evidence"
                ]["evaluation_summary"]["sha256"],
                "delegate_geometry_audit_path": str(delegate_audit_path),
                "delegate_geometry_audit_sha256": delegate_audit_sha,
            }
        )
        source_pins[f"source_geometry_audit::{candidate}"] = (
            source_audit_path,
            source_audit_sha,
        )
        source_pins[f"physical_identity_audit::{candidate}"] = (
            physical_audit_path,
            physical_audit_sha,
        )
        evaluation_record = compatibility_audit["source_evidence"][
            "evaluation_summary"
        ]
        source_pins[f"evaluation_summary::{candidate}"] = (
            Path(evaluation_record["path"]),
            str(evaluation_record["sha256"]),
        )
        prepared.append(prepared_row)
    return prepared, delegate_fields, audit_records, source_pins


def _current_contract_delegate_geometry_audit(
    *,
    row: Mapping[str, str],
    source_audit: Mapping[str, Any],
    source_audit_path: Path,
    physical_audit: Mapping[str, Any],
    physical_audit_path: Path,
    process_path: Path,
) -> dict[str, Any]:
    candidate = _sha_value(row.get("candidate_id_sha256"), "candidate")
    geometry = _sha_value(
        row.get("candidate_geometry_identity_sha256"), "candidate geometry"
    )
    gds_sha = _sha_value(row.get("gds_sha256"), "GDS")
    normalized_sha = _sha_value(
        row.get("gds_timestamp_normalized_sha256"), "normalized GDS"
    )
    if not (
        source_audit.get("overall_status") == "PASS"
        and source_audit.get("candidate_id_sha256") == candidate
        and source_audit.get("candidate_geometry_identity_sha256") == geometry
        and source_audit.get("gds_sha256") == gds_sha
        and source_audit.get("gds_timestamp_normalized_sha256")
        == normalized_sha
    ):
        raise CalibreBatchError("source geometry-audit identity mismatch")
    source_checks = _mapping(
        source_audit.get("checks"), "source geometry-audit checks"
    )
    required_source_checks = (
        "candidate_geometry_recomputed",
        "dataset_geometry_recomputed",
        "evaluation_geometry_recomputed",
        "cadence_gds_present_and_nonempty",
        "direct_gds_present_and_nonempty",
        "layout_manifest_present_and_nonempty",
    )
    if any(
        source_checks.get(name) is not True for name in required_source_checks
    ):
        raise CalibreBatchError("source geometry-audit checks are incomplete")
    if not (
        physical_audit.get("overall_status") == "PASS"
        and physical_audit.get("candidate_id_sha256") == candidate
        and physical_audit.get("candidate_geometry_identity_sha256") == geometry
        and physical_audit.get("cadence_gds_sha256") == gds_sha
        and physical_audit.get("cadence_gds_timestamp_normalized_sha256")
        == normalized_sha
    ):
        raise CalibreBatchError("GDS physical-identity audit mismatch")
    physical_checks = _mapping(
        physical_audit.get("checks"), "GDS physical-identity checks"
    )
    if not physical_checks or any(
        value is not True for value in physical_checks.values()
    ):
        raise CalibreBatchError("GDS physical-identity checks are not all PASS")

    evaluation_path = _regular_file(
        Path(str(source_audit.get("evaluation_summary_path") or "")),
        "evaluation summary",
    )
    evaluation_sha = _sha256(evaluation_path)
    if source_audit.get("evaluation_summary_sha256") != evaluation_sha:
        raise CalibreBatchError("evaluation summary SHA mismatch")
    evaluation = _read_json(evaluation_path, "evaluation summary")
    geometry_check = _mapping(
        evaluation.get("geometry_check"), "evaluation geometry check"
    )
    metrics = _mapping(
        geometry_check.get("metrics"), "evaluation geometry metrics"
    )
    if not (
        evaluation.get("ok") is True
        and geometry_check.get("ok") is True
        and list(geometry_check.get("errors") or []) == []
    ):
        raise CalibreBatchError("evaluation geometry check is not PASS")

    line_width = _finite_float(row.get("geom__line_width_um"), "line width")
    line_width_sync_pass = all(
        _close_float(metrics.get(name), line_width)
        for name in (
            "power_line_8port_bridge_width_um",
            "power_line_8port_primary_bridge_width_um",
            "power_line_8port_secondary_bridge_width_um",
        )
    )
    angle_45_135_pass = all(
        _close_float(metrics.get(name), expected)
        for name, expected in (
            ("primary_winding_centerline_min_internal_angle_deg", 135.0),
            ("primary_winding_centerline_max_internal_angle_deg", 135.0),
            ("primary_winding_centerline_min_terminal_angle_deg", 90.0),
            ("primary_winding_centerline_max_terminal_angle_deg", 90.0),
            ("secondary_winding_centerline_min_internal_angle_deg", 135.0),
            ("secondary_winding_centerline_max_internal_angle_deg", 135.0),
            ("secondary_winding_centerline_min_terminal_angle_deg", 90.0),
            ("secondary_winding_centerline_max_terminal_angle_deg", 90.0),
        )
    )
    checks = {
        "geometry_range_pass": row.get("analytical_status") == "PASS",
        "topology_pass": row.get("topology_status") == "PASS",
        "line_width_sync_pass": line_width_sync_pass,
        "angle_45_135_pass": angle_45_135_pass,
        "ground_clearance_pass": (
            row.get("top_metal_drc_status") == "PASS"
            and row.get("drc_status") == "PASS"
        ),
    }
    failed = [name for name, value in checks.items() if value is not True]
    if failed:
        raise CalibreBatchError(
            f"current-contract delegate geometry checks failed: {failed}"
        )
    return {
        "schema": (
            "rfic_transformer.broadband56_v2_current_contract_"
            "calibre_delegate_geometry_audit.v1"
        ),
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "CURRENT_CONTRACT_GDS_READY_FOR_FOUNDRY_CALIBRE",
        "candidate_id_sha256": candidate,
        "candidate_geometry_identity_sha256": geometry,
        "gds_path": str(Path(str(row.get("gds_path") or "")).resolve()),
        "gds_sha256": gds_sha,
        "gds_timestamp_normalized_sha256": normalized_sha,
        "gds_timestamp_normalization_algorithm": (
            GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        ),
        "gds_top_cell": EXPECTED_TOP_CELL,
        "process_token": EXPECTED_PROCESS_TOKEN,
        "checks": checks,
        "effective_required_geometry_checks": list(
            DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS
        ),
        "teacher_only_foundry_slotting_prechecks_applied": False,
        "source_evidence": {
            "source_geometry_audit": _file_record(source_audit_path),
            "gds_physical_identity_audit": _file_record(physical_audit_path),
            "evaluation_summary": _file_record(evaluation_path),
            "emx_process_file": _file_record(process_path),
        },
        "foundry_drc_executed": False,
        "fresh_real_emx_executed": False,
        "simulator_action_taken": False,
    }


def _run_delegate_with_current_contract(
    delegate_path: Path,
    delegate_args: list[str],
) -> argparse.Namespace:
    module_name = f"_b56_calibre_delegate_{_sha256(delegate_path)[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, delegate_path)
    if spec is None or spec.loader is None:
        raise CalibreBatchError("cannot load the pinned Calibre delegate")
    module = importlib.util.module_from_spec(spec)
    with _legacy_gds_hash_compat_module():
        try:
            spec.loader.exec_module(module)
        except (Exception, SystemExit) as exc:
            raise CalibreBatchError(
                f"cannot import the pinned Calibre delegate: {exc}"
            ) from exc
        legacy_checks = tuple(getattr(module, "REQUIRED_GEOMETRY_CHECKS", ()))
        if legacy_checks != LEGACY_DELEGATE_REQUIRED_GEOMETRY_CHECKS:
            raise CalibreBatchError(
                "pinned Calibre delegate check contract drifted"
            )
        module.REQUIRED_GEOMETRY_CHECKS = (
            DELEGATE_EFFECTIVE_REQUIRED_GEOMETRY_CHECKS
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                return_code = int(module.main(delegate_args))
        except SystemExit as exc:
            return_code = int(exc.code or 0)
        except Exception as exc:
            raise CalibreBatchError(
                f"Calibre delegate raised {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            module.REQUIRED_GEOMETRY_CHECKS = legacy_checks
    return argparse.Namespace(
        returncode=return_code,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


@contextlib.contextmanager
def _legacy_gds_hash_compat_module():
    """Expose the manifest-bound hash implementation to the legacy delegate."""

    previous_module = sys.modules.get(
        LEGACY_GDS_HASH_MODULE_NAME, _MISSING_MODULE
    )
    previous_attribute = getattr(_layout_package, "gds_hash", _MISSING_MODULE)
    compatibility_module = types.ModuleType(LEGACY_GDS_HASH_MODULE_NAME)
    compatibility_module.__package__ = "rfic_transformer_inverse_design.layout"
    compatibility_module.GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM = (
        GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
    )
    compatibility_module.gds_timestamp_normalized_sha256 = (
        gds_timestamp_normalized_sha256
    )
    compatibility_module.__all__ = (
        "GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM",
        "gds_timestamp_normalized_sha256",
    )
    sys.modules[LEGACY_GDS_HASH_MODULE_NAME] = compatibility_module
    _layout_package.gds_hash = compatibility_module
    try:
        yield
    finally:
        if previous_module is _MISSING_MODULE:
            sys.modules.pop(LEGACY_GDS_HASH_MODULE_NAME, None)
        else:
            sys.modules[LEGACY_GDS_HASH_MODULE_NAME] = previous_module
        if previous_attribute is _MISSING_MODULE:
            if hasattr(_layout_package, "gds_hash"):
                delattr(_layout_package, "gds_hash")
        else:
            _layout_package.gds_hash = previous_attribute


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibreBatchError(f"{label} is not numeric") from exc
    if not (float("-inf") < result < float("inf")):
        raise CalibreBatchError(f"{label} is not finite")
    return result


def _close_float(value: Any, expected: float, tolerance: float = 1.0e-9) -> bool:
    try:
        actual = float(value)
    except (TypeError, ValueError):
        return False
    return abs(actual - expected) <= tolerance


def _write_csv(
    path: Path, fields: list[str], rows: list[Mapping[str, Any]]
) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_sums(root: Path) -> None:
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != SHA256SUMS_NAME
    )
    (root / SHA256SUMS_NAME).write_text(
        "\n".join(f"{_sha256(path)}  {path.relative_to(root)}" for path in files)
        + "\n",
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha_value(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise CalibreBatchError(f"{label} is not SHA-256")
    return digest


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CalibreBatchError(f"{label} is not an integer") from exc


def _require_unchanged(path: Path, digest: str, label: str) -> None:
    if _sha256(path) != digest:
        raise CalibreBatchError(f"{label} changed during Calibre batch")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
