#!/usr/bin/env python3
"""Partition one GDS physical-identity audit into candidate terminal outcomes."""

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


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_gds_physical_identity_batch.v1"
RECEIPT_NAME = "GDS_PHYSICAL_IDENTITY_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "GDS_PHYSICAL_IDENTITY_PASS_INDEX.csv"
FAILURE_INDEX_NAME = "GDS_PHYSICAL_IDENTITY_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "GDS_PHYSICAL_IDENTITY_EVIDENCE_INDEX.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

FAILURE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "terminal_stage",
    "audit_path",
    "audit_sha256",
    "error",
)

EVIDENCE_FIELDS = (
    "submitted_sequence",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "overall_status",
    "audit_path",
    "audit_sha256",
    "candidate_physical_identity_sha256",
)


class GdsIdentityBatchError(RuntimeError):
    """Raised when GDS identity evidence cannot be partitioned exactly."""


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
    except (GdsIdentityBatchError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"identity_pass_count={receipt['identity_pass_count']}")
    print(f"identity_fail_count={receipt['identity_fail_count']}")
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
        raise GdsIdentityBatchError(f"unknown campaign stage: {stage}")
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
        raise GdsIdentityBatchError("backend manifest campaign or contract mismatch")
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    runtimes = _mapping(manifest.get("runtime_identities"), "runtime identities")
    self_path = _identity_path(
        scripts.get("gds_physical_identity_auditor"), "GDS identity batch role"
    )
    if self_path != Path(__file__).resolve():
        raise GdsIdentityBatchError("GDS identity batch self-identity mismatch")
    delegate_path = _identity_path(
        scripts.get("gds_physical_identity_delegate"), "GDS identity delegate"
    )
    module_path = _identity_path(
        scripts.get("gds_physical_identity_module"), "GDS identity module"
    )
    python_path = _identity_path(runtimes.get("python_executable"), "Python runtime")
    if not os.access(python_path, os.X_OK):
        raise GdsIdentityBatchError("Python runtime is not executable")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "candidate GDS index receipt"
    )
    input_receipt = _read_json(input_receipt_path, "candidate GDS index receipt")
    if input_receipt.get("overall_status") != "PASS":
        raise GdsIdentityBatchError("candidate GDS index receipt is not PASS")
    candidate_path = _record_path(input_receipt.get("candidate_csv"), "candidate CSV")
    dataset_rows_path = _record_path(
        input_receipt.get("dataset_rows_csv"), "dataset rows CSV"
    )
    input_index_path = _record_path(
        input_receipt.get("candidate_bound_index"), "candidate GDS index"
    )
    dataset_dir = Path(str(input_receipt.get("dataset_dir") or "")).expanduser().resolve()
    if not dataset_dir.is_dir():
        raise GdsIdentityBatchError("candidate GDS dataset directory is missing")
    candidate_rows, candidate_fields = _read_csv_allow_empty(candidate_path)
    dataset_rows, _ = _read_csv_allow_empty(dataset_rows_path)
    index_rows, index_fields = _read_csv_allow_empty(input_index_path)
    expected_count = _integer(input_receipt.get("expected_count"), "expected_count")
    if not (
        len(candidate_rows) == expected_count
        and len(dataset_rows) == expected_count
        and len(index_rows) == expected_count
    ):
        raise GdsIdentityBatchError("candidate GDS index count mismatch")
    candidate_by_id: dict[str, dict[str, str]] = {}
    for candidate in candidate_rows:
        candidate_id = _sha_value(
            candidate.get("candidate_id_sha256"), "candidate queue identity"
        )
        geometry_id = _sha_value(
            candidate.get("geometry_sha256"), "candidate queue geometry"
        )
        bound_geometry_id = _sha_value(
            candidate.get("candidate_geometry_identity_sha256"),
            "candidate queue bound geometry",
        )
        if geometry_id != bound_geometry_id:
            raise GdsIdentityBatchError(
                "candidate queue geometry aliases do not match"
            )
        if candidate.get("campaign_id") != CAMPAIGN_ID:
            raise GdsIdentityBatchError("candidate queue campaign mismatch")
        if (
            candidate.get("campaign_contract_fingerprint")
            != SCIENTIFIC_CONTRACT_FINGERPRINT
        ):
            raise GdsIdentityBatchError("candidate queue contract mismatch")
        for field in ("geometry_id", "campaign_phase", "acquisition_source"):
            if not str(candidate.get(field) or "").strip():
                raise GdsIdentityBatchError(
                    f"candidate queue has empty {field}"
                )
        if candidate_id in candidate_by_id:
            raise GdsIdentityBatchError(
                "candidate queue identities are duplicated"
            )
        candidate_by_id[candidate_id] = candidate

    pinned = {
        "manifest": (manifest_path, manifest_sha),
        "delegate": (delegate_path, _sha256(delegate_path)),
        "module": (module_path, _sha256(module_path)),
        "python": (python_path, _sha256(python_path)),
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "candidate": (candidate_path, _sha256(candidate_path)),
        "dataset_rows": (dataset_rows_path, _sha256(dataset_rows_path)),
        "input_index": (input_index_path, _sha256(input_index_path)),
    }
    out_dir.mkdir(parents=True, mode=0o700)
    if expected_count == 0:
        pass_path = out_dir / PASS_INDEX_NAME
        failure_path = out_dir / FAILURE_INDEX_NAME
        evidence_path = out_dir / EVIDENCE_INDEX_NAME
        _write_csv(pass_path, list(index_fields), [])
        _write_csv(failure_path, list(FAILURE_FIELDS), [])
        _write_csv(evidence_path, list(EVIDENCE_FIELDS), [])
        for label, (path, digest) in pinned.items():
            _require_unchanged(path, digest, label)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "TERMINAL_PARTITION_GDS_PHYSICAL_IDENTITY_AUDIT",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "stage": stage,
            "submitted_count": 0,
            "terminal_count": 0,
            "identity_pass_count": 0,
            "identity_fail_count": 0,
            "failed_candidates_counted_as_accepted": False,
            "backend_identity_manifest": _file_record(manifest_path),
            "input_role_receipt": _file_record(input_receipt_path),
            "full_campaign_authorization_receipt": _file_record(
                authorization_path
            ),
            "pass_index": _file_record(pass_path),
            "failure_index": _file_record(failure_path),
            "evidence_index": _file_record(evidence_path),
            "delegate_summary": None,
            "delegate_audited_index": None,
            "delegate_return_code": 0,
            "delegate_command_argv_sha256": hashlib.sha256(b"[]").hexdigest(),
            "delegate_stdout": None,
            "delegate_stderr": None,
            "delegate_skipped_reason": "no Cadence-pass candidates",
            "simulator_action_taken": False,
        }
        receipt_path = out_dir / RECEIPT_NAME
        _write_json(receipt_path, receipt)
        _write_sums(out_dir)
        return receipt
    delegate_dir = out_dir / "delegate_audit"
    command = [
        str(python_path),
        str(delegate_path),
        "--candidate-csv",
        str(candidate_path),
        "--dataset-dir",
        str(dataset_dir),
        "--input-index-csv",
        str(input_index_path),
        "--out-dir",
        str(delegate_dir),
        "--expected-count",
        str(expected_count),
        "--expected-candidate-sha256",
        pinned["candidate"][1],
        "--expected-dataset-rows-sha256",
        pinned["dataset_rows"][1],
        "--expected-index-sha256",
        pinned["input_index"][1],
    ]
    command_sha = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    stdout_path = out_dir / "delegate_stdout.log"
    stderr_path = out_dir / "delegate_stderr.log"
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    summary_path = _regular_file(
        delegate_dir / "GDS_PHYSICAL_IDENTITY_AUDIT_SUMMARY.json",
        "GDS identity delegate summary",
    )
    summary = _read_json(summary_path, "GDS identity delegate summary")
    if not (
        summary.get("campaign_id") == CAMPAIGN_ID
        and summary.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and _integer(summary.get("expected_count"), "delegate expected_count")
        == expected_count
        and _integer(summary.get("pass_count"), "delegate pass_count")
        + _integer(summary.get("fail_count"), "delegate fail_count")
        == expected_count
    ):
        raise GdsIdentityBatchError("GDS identity delegate terminal counts mismatch")
    audited_path = _record_path(
        summary.get("audited_gds_index_csv"), "audited GDS index"
    )
    audited_rows, audited_fields = _read_csv(audited_path)
    if len(audited_rows) != expected_count:
        raise GdsIdentityBatchError("audited GDS index is not a complete partition")

    pass_rows: list[dict[str, str]] = []
    failures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sequence, row in enumerate(audited_rows, start=1):
        candidate_id = _sha_value(row.get("candidate_id_sha256"), "candidate")
        geometry_id = _sha_value(
            row.get("candidate_geometry_identity_sha256"), "candidate geometry"
        )
        if candidate_id in seen:
            raise GdsIdentityBatchError("audited GDS candidate identities are duplicated")
        seen.add(candidate_id)
        audit_path = _regular_file(
            Path(str(row.get("gds_physical_identity_audit_path") or "")),
            "candidate GDS identity audit",
        )
        audit_sha = _sha256(audit_path)
        if row.get("gds_physical_identity_audit_sha256") != audit_sha:
            raise GdsIdentityBatchError("candidate GDS identity audit SHA mismatch")
        audit = _read_json(audit_path, "candidate GDS identity audit")
        status = str(row.get("gds_physical_identity_status") or "")
        if audit.get("overall_status") != status or status not in {"PASS", "FAIL"}:
            raise GdsIdentityBatchError("candidate GDS identity status mismatch")
        source_candidate = candidate_by_id.get(candidate_id)
        if source_candidate is None:
            raise GdsIdentityBatchError(
                "audited GDS candidate is absent from the source queue"
            )
        if source_candidate["geometry_sha256"] != geometry_id:
            raise GdsIdentityBatchError(
                "audited GDS geometry mismatches the source queue"
            )
        gds_path = _regular_file(
            Path(str(row.get("gds_path") or "")),
            "audited candidate GDS",
        )
        if gds_path.name != "transformer_layout_cadpins.gds":
            raise GdsIdentityBatchError(
                "audited candidate GDS is not the Cadence streamout artifact"
            )
        layout_manifest_path = _regular_file(
            gds_path.parents[1] / "layout" / "transformer_layout.layout.json",
            "candidate layout manifest",
        )
        layout_manifest_sha256 = _sha256(layout_manifest_path)
        if row.get("layout_manifest_sha256") != layout_manifest_sha256:
            raise GdsIdentityBatchError(
                "candidate layout manifest SHA mismatches the bound GDS index"
            )
        evidence.append(
            {
                "submitted_sequence": sequence,
                "candidate_id_sha256": candidate_id,
                "candidate_geometry_identity_sha256": geometry_id,
                "overall_status": status,
                "audit_path": str(audit_path),
                "audit_sha256": audit_sha,
                "candidate_physical_identity_sha256": str(
                    audit.get("candidate_physical_identity_sha256") or ""
                ),
            }
        )
        if status == "PASS":
            _sha_value(
                audit.get("candidate_physical_identity_sha256"),
                "candidate physical identity",
            )
            pass_rows.append(
                {
                    **source_candidate,
                    **row,
                    "manifest_path": str(layout_manifest_path),
                    "manifest_sha256": layout_manifest_sha256,
                }
            )
        else:
            failures.append(
                {
                    "submitted_sequence": sequence,
                    "candidate_id_sha256": candidate_id,
                    "candidate_geometry_identity_sha256": geometry_id,
                    "terminal_stage": "gds_physical_identity",
                    "audit_path": str(audit_path),
                    "audit_sha256": audit_sha,
                    "error": str(audit.get("error") or "GDS identity audit failed"),
                }
            )

    if seen != set(candidate_by_id):
        raise GdsIdentityBatchError(
            "audited GDS index omitted source queue candidates"
        )

    pass_path = out_dir / PASS_INDEX_NAME
    failure_path = out_dir / FAILURE_INDEX_NAME
    evidence_path = out_dir / EVIDENCE_INDEX_NAME
    pass_fields = list(
        dict.fromkeys(
            [*candidate_fields, *audited_fields, "manifest_path", "manifest_sha256"]
        )
    )
    _write_csv(pass_path, pass_fields, pass_rows)
    _write_csv(failure_path, list(FAILURE_FIELDS), failures)
    _write_csv(evidence_path, list(EVIDENCE_FIELDS), evidence)
    for label, (path, digest) in pinned.items():
        _require_unchanged(path, digest, label)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "TERMINAL_PARTITION_GDS_PHYSICAL_IDENTITY_AUDIT",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": expected_count,
        "terminal_count": len(audited_rows),
        "identity_pass_count": len(pass_rows),
        "identity_fail_count": len(failures),
        "failed_candidates_counted_as_accepted": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "full_campaign_authorization_receipt": _file_record(
            authorization_path
        ),
        "pass_index": _file_record(pass_path),
        "failure_index": _file_record(failure_path),
        "evidence_index": _file_record(evidence_path),
        "delegate_summary": _file_record(summary_path),
        "delegate_audited_index": _file_record(audited_path),
        "delegate_return_code": int(result.returncode),
        "delegate_command_argv_sha256": command_sha,
        "delegate_stdout": _file_record(stdout_path),
        "delegate_stderr": _file_record(stderr_path),
        "simulator_action_taken": False,
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
    ):
        raise GdsIdentityBatchError("FULL_CAMPAIGN authorization mismatch")


def _record_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    return _identity_path(record, label)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GdsIdentityBatchError(f"{label} is not an object")
    return value


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if record.get("size_bytes") != path.stat().st_size or record.get("sha256") != _sha256(path):
        raise GdsIdentityBatchError(f"{label} identity mismatch")
    return path


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise GdsIdentityBatchError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GdsIdentityBatchError(f"{label} is not a JSON object")
    return value


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _read_csv_allow_empty(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    return _read_csv(path)


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
        raise GdsIdentityBatchError(f"{label} is not SHA-256")
    return digest


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GdsIdentityBatchError(f"{label} is not an integer") from exc


def _require_unchanged(path: Path, digest: str, label: str) -> None:
    if _sha256(path) != digest:
        raise GdsIdentityBatchError(f"{label} changed during GDS identity audit")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
