#!/usr/bin/env python3
"""Run the hash-bound Calibre batch delegate and partition candidate results."""

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


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_calibre_batch.v1"
RECEIPT_NAME = "CALIBRE_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "CALIBRE_PASS_INDEX.csv"
FAILURE_INDEX_NAME = "CALIBRE_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "CALIBRE_EVIDENCE_INDEX.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

EXPECTED_DECK_MEMBER = "MAIN_DRC_TopMu/CLN65S_9M_6X1Z1U.26_2a"
EXPECTED_USER_GUIDE_MEMBER = "3_UserGuide.txt"
EXPECTED_PROCESS_TOKEN = "/TSMC65_05_12_26/"
EXPECTED_TOP_CELL = "TRANSFORMER"
EXPECTED_CALIBRE_MODULE = "mentor/old/2025"

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
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "input_index": (input_index_path, _sha256(input_index_path)),
    }
    out_dir.mkdir(parents=True, mode=0o700)
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
    command = [
        str(python_path),
        str(delegate_path),
        "--input-index-csv",
        str(input_index_path),
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
    if record.get("size_bytes") != path.stat().st_size or record.get("sha256") != _sha256(path):
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
