#!/usr/bin/env python3
"""Bind one Cadence-pass batch to exact candidate and geometry identities.

This role never launches Cadence, Calibre, or EMX.  It consumes the immutable
Cadence batch receipt, verifies every candidate/evaluation/GDS relationship,
and writes the exact index consumed by the broadband GDS identity auditor.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
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


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_candidate_gds_index_batch.v1"
AUDIT_SCHEMA = "rfic_transformer.broadband56_v2_candidate_gds_binding.v1"
RECEIPT_NAME = "CANDIDATE_GDS_INDEX_BATCH_ROLE_RECEIPT.json"
INDEX_NAME = "candidate_bound_cadence_gds_index.csv"
SUMMARY_NAME = "CANDIDATE_GDS_INDEX_BATCH_SUMMARY.json"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

INDEX_FIELDS = (
    "overall_status",
    "candidate_id",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "evaluation_cache_key",
    "gds_path",
    "gds_sha256",
    "gds_timestamp_normalized_sha256_algorithm",
    "gds_timestamp_normalized_sha256",
    "layout_manifest_path",
    "layout_manifest_sha256",
    "geometry_audit_path",
    "geometry_audit_sha256",
)


class CandidateGdsIndexError(RuntimeError):
    """Raised when candidate-to-GDS identity cannot be proven."""


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
    except (CandidateGdsIndexError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"expected_count={receipt['expected_count']}")
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
        raise CandidateGdsIndexError(f"unknown campaign stage: {stage}")

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
        raise CandidateGdsIndexError(
            "backend manifest campaign or contract mismatch"
        )
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    self_path = _identity_path(
        scripts.get("candidate_gds_index_builder"), "candidate GDS index role"
    )
    if self_path != Path(__file__).resolve():
        raise CandidateGdsIndexError("candidate GDS index self-identity mismatch")
    identity_module_path = _identity_path(
        scripts.get("gds_physical_identity_module"), "GDS identity module"
    )

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)

    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "Cadence batch receipt"
    )
    input_receipt = _read_json(input_receipt_path, "Cadence batch receipt")
    if not (
        input_receipt.get("overall_status") == "PASS"
        and input_receipt.get("campaign_id") == CAMPAIGN_ID
        and input_receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and str(input_receipt.get("stage") or "").upper() == stage
        and input_receipt.get("backend_identity_manifest", {}).get("sha256")
        == manifest_sha
        and input_receipt.get("full_campaign_authorization_receipt", {}).get(
            "sha256"
        )
        == _sha256(authorization_path)
    ):
        raise CandidateGdsIndexError("Cadence batch receipt identity mismatch")

    candidate_path = _record_path(
        input_receipt.get("pass_candidate_queue"), "Cadence pass candidate queue"
    )
    dataset_rows_path = _record_path(
        input_receipt.get("pass_dataset_rows"), "Cadence pass dataset rows"
    )
    dataset_dir = Path(
        str(input_receipt.get("pass_dataset_dir") or "")
    ).expanduser().resolve()
    if not dataset_dir.is_dir() or dataset_rows_path != dataset_dir / "dataset_rows.csv":
        raise CandidateGdsIndexError("Cadence pass dataset directory mismatch")
    expected_count = _integer(
        input_receipt.get("cadence_pass_count"), "cadence_pass_count"
    )
    candidate_rows, candidate_fields = _read_csv_allow_empty(candidate_path)
    dataset_rows, dataset_fields = _read_csv_allow_empty(dataset_rows_path)
    if len(candidate_rows) != expected_count or len(dataset_rows) != expected_count:
        raise CandidateGdsIndexError("Cadence pass row count mismatch")

    pinned = {
        "manifest": (manifest_path, manifest_sha),
        "identity_module": (identity_module_path, _sha256(identity_module_path)),
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "candidate": (candidate_path, _sha256(candidate_path)),
        "dataset_rows": (dataset_rows_path, _sha256(dataset_rows_path)),
    }

    out_dir.mkdir(parents=True, mode=0o700)
    audits_dir = out_dir / "candidate_bound_geometry_audits"
    audits_dir.mkdir()
    records: list[dict[str, Any]] = []
    if expected_count:
        required_candidate = {
            "candidate_id",
            "candidate_id_sha256",
            "candidate_geometry_identity_sha256",
            "campaign_id",
            "campaign_contract_fingerprint",
            *{f"geom__{name}" for name in GEOMETRY_FIELDS},
        }
        required_dataset = {
            "evaluation",
            "ok",
            "queue__candidate_id",
            "queue__candidate_id_sha256",
            "queue__candidate_geometry_identity_sha256",
            *{f"geom__{name}" for name in GEOMETRY_FIELDS},
        }
        if not required_candidate.issubset(candidate_fields):
            raise CandidateGdsIndexError("candidate queue columns are incomplete")
        if not required_dataset.issubset(dataset_fields):
            raise CandidateGdsIndexError("dataset row columns are incomplete")
        dataset_by_id = _unique_rows(
            dataset_rows, "queue__candidate_id_sha256", "dataset rows"
        )
        seen_geometry: set[str] = set()
        for candidate in candidate_rows:
            record = _bind_candidate(
                candidate=candidate,
                dataset_by_id=dataset_by_id,
                dataset_dir=dataset_dir,
                audit_dir=audits_dir,
            )
            geometry_sha = str(record["candidate_geometry_identity_sha256"])
            if geometry_sha in seen_geometry:
                raise CandidateGdsIndexError("duplicate geometry in Cadence pass batch")
            seen_geometry.add(geometry_sha)
            records.append(record)
        if len(dataset_by_id) != len(records):
            raise CandidateGdsIndexError("candidate and dataset identity sets differ")

    index_path = out_dir / INDEX_NAME
    _write_csv(index_path, INDEX_FIELDS, records)
    summary = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "CANDIDATE_BOUND_CADENCE_GDS_READY_FOR_IDENTITY_AUDIT",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "expected_count": expected_count,
        "candidate_csv": _file_record(candidate_path),
        "dataset_rows_csv": _file_record(dataset_rows_path),
        "dataset_dir": str(dataset_dir),
        "candidate_bound_index": _file_record(index_path),
        "automatic_calibre_authorized": False,
        "automatic_emx_authorized": False,
        "simulator_action_taken": False,
    }
    summary_path = out_dir / SUMMARY_NAME
    _write_json(summary_path, summary)
    for label, (path, digest) in pinned.items():
        _require_unchanged(path, digest, label)
    receipt = {
        **summary,
        "backend_identity_manifest": _file_record(manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "full_campaign_authorization_receipt": _file_record(
            authorization_path
        ),
        "summary": _file_record(summary_path),
    }
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(out_dir)
    return receipt


def _bind_candidate(
    *,
    candidate: Mapping[str, str],
    dataset_by_id: Mapping[str, Mapping[str, str]],
    dataset_dir: Path,
    audit_dir: Path,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    candidate_sha = _sha_value(
        candidate.get("candidate_id_sha256"), "candidate identity"
    )
    geometry_sha = _sha_value(
        candidate.get("candidate_geometry_identity_sha256"), "geometry identity"
    )
    if not candidate_id:
        raise CandidateGdsIndexError("candidate_id is empty")
    if not (
        candidate.get("campaign_id") == CAMPAIGN_ID
        and candidate.get("campaign_contract_fingerprint")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise CandidateGdsIndexError("candidate campaign or contract mismatch")
    candidate_geometry = {
        name: candidate.get(f"geom__{name}") for name in GEOMETRY_FIELDS
    }
    if canonical_geometry_sha256(candidate_geometry) != geometry_sha:
        raise CandidateGdsIndexError("candidate geometry identity does not recompute")
    if candidate_sha != geometry_sha:
        raise CandidateGdsIndexError("candidate and geometry SHA aliases differ")
    dataset = dataset_by_id.get(candidate_sha)
    if dataset is None:
        raise CandidateGdsIndexError("candidate is absent from dataset rows")
    dataset_geometry = {
        name: dataset.get(f"geom__{name}") for name in GEOMETRY_FIELDS
    }
    if not (
        _truthy(dataset.get("ok"))
        and str(dataset.get("queue__candidate_id") or "") == candidate_id
        and _sha_value(
            dataset.get("queue__candidate_geometry_identity_sha256"),
            "dataset geometry identity",
        )
        == geometry_sha
        and canonical_geometry_sha256(dataset_geometry) == geometry_sha
    ):
        raise CandidateGdsIndexError("dataset row candidate or geometry mismatch")

    evaluation = str(dataset.get("evaluation") or "").strip()
    if not evaluation or Path(evaluation).name != evaluation:
        raise CandidateGdsIndexError("unsafe or empty evaluation cache key")
    evaluation_dir = (dataset_dir / "evaluations" / evaluation).resolve()
    try:
        evaluation_dir.relative_to(dataset_dir)
    except ValueError as exc:
        raise CandidateGdsIndexError("evaluation directory escapes dataset root") from exc
    if not evaluation_dir.is_dir():
        raise CandidateGdsIndexError("evaluation directory is missing")
    gds_path = _regular_file(
        evaluation_dir / "streamout" / "transformer_layout_cadpins.gds",
        "Cadence streamout GDS",
    )
    direct_gds_path = _regular_file(
        evaluation_dir / "layout" / "transformer_layout.gds",
        "direct layout GDS",
    )
    layout_manifest_path = _regular_file(
        evaluation_dir / "layout" / "transformer_layout.layout.json",
        "layout manifest",
    )
    evaluation_summary_path = _regular_file(
        evaluation_dir / "summary.json", "evaluation summary"
    )
    evaluation_summary = _read_json(evaluation_summary_path, "evaluation summary")
    summary_geometry = evaluation_summary.get("geometry")
    if not isinstance(summary_geometry, Mapping):
        raise CandidateGdsIndexError("evaluation summary geometry is missing")
    if canonical_geometry_sha256(summary_geometry) != geometry_sha:
        raise CandidateGdsIndexError("evaluation geometry identity mismatch")

    gds_sha = _sha256(gds_path)
    normalized_sha = gds_timestamp_normalized_sha256(gds_path)
    layout_sha = _sha256(layout_manifest_path)
    audit = {
        "schema": AUDIT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "candidate_id": candidate_id,
        "candidate_id_sha256": candidate_sha,
        "candidate_geometry_identity_sha256": geometry_sha,
        "evaluation_cache_key": evaluation,
        "gds_path": str(gds_path),
        "gds_sha256": gds_sha,
        "gds_timestamp_normalized_sha256_algorithm": (
            GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        ),
        "gds_timestamp_normalized_sha256": normalized_sha,
        "direct_gds_path": str(direct_gds_path),
        "direct_gds_sha256": _sha256(direct_gds_path),
        "layout_manifest_path": str(layout_manifest_path),
        "layout_manifest_sha256": layout_sha,
        "evaluation_summary_path": str(evaluation_summary_path),
        "evaluation_summary_sha256": _sha256(evaluation_summary_path),
        "checks": {
            "candidate_geometry_recomputed": True,
            "dataset_geometry_recomputed": True,
            "evaluation_geometry_recomputed": True,
            "cadence_gds_present_and_nonempty": True,
            "direct_gds_present_and_nonempty": True,
            "layout_manifest_present_and_nonempty": True,
        },
        "simulator_action_taken": False,
    }
    audit_path = audit_dir / f"{candidate_sha}.json"
    _write_json(audit_path, audit)
    return {
        "overall_status": "PASS",
        "candidate_id": candidate_id,
        "candidate_id_sha256": candidate_sha,
        "candidate_geometry_identity_sha256": geometry_sha,
        "evaluation_cache_key": evaluation,
        "gds_path": str(gds_path),
        "gds_sha256": gds_sha,
        "gds_timestamp_normalized_sha256_algorithm": (
            GDS_TIMESTAMP_NORMALIZED_SHA256_ALGORITHM
        ),
        "gds_timestamp_normalized_sha256": normalized_sha,
        "layout_manifest_path": str(layout_manifest_path),
        "layout_manifest_sha256": layout_sha,
        "geometry_audit_path": str(audit_path),
        "geometry_audit_sha256": _sha256(audit_path),
    }


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
        raise CandidateGdsIndexError("FULL_CAMPAIGN authorization mismatch")


def _unique_rows(
    rows: list[dict[str, str]], field: str, label: str
) -> dict[str, Mapping[str, str]]:
    result: dict[str, Mapping[str, str]] = {}
    for row in rows:
        key = _sha_value(row.get(field), f"{label}.{field}")
        if key in result:
            raise CandidateGdsIndexError(f"{label} contains duplicate {field}")
        result[key] = row
    return result


def _record_path(value: Any, label: str) -> Path:
    return _identity_path(_mapping(value, label), label)


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if _integer(record.get("size_bytes"), f"{label}.size_bytes") != path.stat().st_size:
        raise CandidateGdsIndexError(f"{label} size mismatch")
    if _sha_value(record.get("sha256"), f"{label}.sha256") != _sha256(path):
        raise CandidateGdsIndexError(f"{label} SHA mismatch")
    return path


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateGdsIndexError(f"{label} is not an object")
    return value


def _read_csv_allow_empty(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], set(reader.fieldnames or [])


def _write_csv(
    path: Path, fields: tuple[str, ...], rows: list[Mapping[str, Any]]
) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CandidateGdsIndexError(f"{label} is not a JSON object")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _file_record(path: Path) -> dict[str, Any]:
    resolved = _regular_file(path, "artifact")
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise CandidateGdsIndexError(f"{label} is missing or empty: {resolved}")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_value(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(text) is None:
        raise CandidateGdsIndexError(f"{label} is not SHA-256")
    return text


def _integer(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CandidateGdsIndexError(f"{label} is not an integer") from exc
    if result < 0:
        raise CandidateGdsIndexError(f"{label} is negative")
    return result


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass"}


def _require_unchanged(path: Path, expected: str, label: str) -> None:
    if _sha256(path) != expected:
        raise CandidateGdsIndexError(f"{label} changed during execution")


def _write_sums(out_dir: Path) -> None:
    paths = sorted(
        path
        for path in out_dir.rglob("*")
        if path.is_file() and path.name != SHA256SUMS_NAME
    )
    lines = [f"{_sha256(path)}  {path.relative_to(out_dir)}" for path in paths]
    (out_dir / SHA256SUMS_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
