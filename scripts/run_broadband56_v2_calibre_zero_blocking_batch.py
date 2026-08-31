#!/usr/bin/env python3
"""Build per-candidate zero-blocking Calibre receipts without simulation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx import (  # noqa: E402
    CALIBRE_ZERO_BLOCKING_PASS_DECISION,
    CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)


RECEIPT_SCHEMA = (
    "rfic_transformer.broadband56_v2_calibre_zero_blocking_batch.v1"
)
RECEIPT_NAME = "CALIBRE_ZERO_BLOCKING_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "CALIBRE_ZERO_BLOCKING_PASS_INDEX.csv"
FAILURE_INDEX_NAME = "CALIBRE_ZERO_BLOCKING_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "CALIBRE_ZERO_BLOCKING_EVIDENCE_INDEX.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SINGLE_RECEIPT_NAME = "CALIBRE_ZERO_BLOCKING_RECEIPT.json"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

REQUIRED_INPUT_FIELDS = (
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "candidate_geometry_identity_sha256",
    "campaign_phase",
    "acquisition_source",
    "campaign_contract_fingerprint",
    "gds_path",
    "gds_sha256",
    "manifest_path",
    "manifest_sha256",
    "drc_summary_path",
    "drc_summary_sha256",
    "blocking_drc_violation_count",
    "overall_status",
)

PASS_INDEX_FIELDS = (
    "accepted_sequence",
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

FAILURE_INDEX_FIELDS = (
    "submitted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "terminal_stage",
    "return_code",
    "error",
)

EVIDENCE_INDEX_FIELDS = (
    "submitted_sequence",
    "geometry_sha256",
    "candidate_id_sha256",
    "overall_status",
    "return_code",
    "command_argv_sha256",
    "delegate_result_path",
    "delegate_result_sha256",
)


class CalibreZeroBlockingBatchError(RuntimeError):
    """Raised when zero-blocking receipts cannot be partitioned safely."""


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
    except (CalibreZeroBlockingBatchError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"receipt_pass_count={receipt['receipt_pass_count']}")
    print(f"receipt_fail_count={receipt['receipt_fail_count']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


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


def run_batch(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    if stage not in {item.name for item in STAGES}:
        raise CalibreZeroBlockingBatchError(f"unknown campaign stage: {stage}")
    max_concurrency = int(args.max_concurrency)
    if max_concurrency < 1:
        raise CalibreZeroBlockingBatchError("max_concurrency must be positive")

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
        raise CalibreZeroBlockingBatchError(
            "backend manifest campaign or contract mismatch"
        )
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    runtimes = _mapping(manifest.get("runtime_identities"), "runtime identities")
    self_path = _identity_path(
        scripts.get("calibre_zero_blocking_receipt_builder"),
        "Calibre zero-blocking batch role",
    )
    if self_path != Path(__file__).resolve():
        raise CalibreZeroBlockingBatchError(
            "Calibre zero-blocking batch self-identity mismatch"
        )
    delegate_path = _identity_path(
        scripts.get("calibre_zero_blocking_single_receipt_builder"),
        "single Calibre zero-blocking receipt builder",
    )
    python_path = _identity_path(
        runtimes.get("python_executable"), "Python runtime"
    )
    config_path = _identity_path(
        runtimes.get("private_configuration"), "private configuration"
    )
    requested_config = _regular_file(Path(args.config), "private configuration")
    if requested_config != config_path:
        raise CalibreZeroBlockingBatchError(
            "private configuration path mismatches manifest"
        )
    if not os.access(python_path, os.X_OK):
        raise CalibreZeroBlockingBatchError("Python runtime is not executable")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "Calibre batch receipt"
    )
    input_receipt = _read_json(input_receipt_path, "Calibre batch receipt")
    _validate_input_receipt(input_receipt, expected_stage=stage)
    input_index_path = _identity_path(
        input_receipt.get("pass_index"), "Calibre pass index"
    )
    rows = _read_input_rows(input_index_path)

    pins = {
        "manifest": (manifest_path, manifest_sha),
        "delegate": (delegate_path, _sha256(delegate_path)),
        "python": (python_path, _sha256(python_path)),
        "config": (config_path, _sha256(config_path)),
        "authorization": (authorization_path, _sha256(authorization_path)),
        "input_receipt": (input_receipt_path, _sha256(input_receipt_path)),
        "input_index": (input_index_path, _sha256(input_index_path)),
    }
    out_dir.mkdir(parents=True, mode=0o700)
    candidates_dir = out_dir / "candidates"
    candidates_dir.mkdir()
    terminal: list[dict[str, Any] | None] = [None] * len(rows)
    workers = min(max_concurrency, max(1, len(rows)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _run_one,
                row=row,
                submitted_sequence=index + 1,
                candidates_dir=candidates_dir,
                python_path=python_path,
                delegate_path=delegate_path,
                config_path=config_path,
                config_sha256=pins["config"][1],
            ): index
            for index, row in enumerate(rows)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                terminal[index] = future.result()
            except Exception as exc:  # Preserve a terminal candidate failure.
                terminal[index] = _failure_result(
                    rows[index],
                    submitted_sequence=index + 1,
                    return_code=-1,
                    error=f"{type(exc).__name__}: {exc}",
                )
    results = [item for item in terminal if item is not None]
    if len(results) != len(rows):
        raise CalibreZeroBlockingBatchError(
            "zero-blocking receipt terminal partition is incomplete"
        )
    passed = [item for item in results if item["overall_status"] == "PASS"]
    failed = [item for item in results if item["overall_status"] != "PASS"]
    pass_rows = [
        {
            "accepted_sequence": sequence,
            **{field: item[field] for field in PASS_INDEX_FIELDS[1:]},
        }
        for sequence, item in enumerate(passed, start=1)
    ]
    failure_rows = [
        {field: item.get(field, "") for field in FAILURE_INDEX_FIELDS}
        for item in failed
    ]
    evidence_rows = [
        {field: item.get(field, "") for field in EVIDENCE_INDEX_FIELDS}
        for item in results
    ]
    pass_path = out_dir / PASS_INDEX_NAME
    failure_path = out_dir / FAILURE_INDEX_NAME
    evidence_path = out_dir / EVIDENCE_INDEX_NAME
    _write_csv(pass_path, PASS_INDEX_FIELDS, pass_rows)
    _write_csv(failure_path, FAILURE_INDEX_FIELDS, failure_rows)
    _write_csv(evidence_path, EVIDENCE_INDEX_FIELDS, evidence_rows)

    for label, (path, digest) in pins.items():
        _require_unchanged(path, digest, label)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "TERMINAL_PARTITION_ZERO_BLOCKING_CALIBRE_RECEIPTS",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": len(rows),
        "terminal_count": len(results),
        "receipt_pass_count": len(passed),
        "receipt_fail_count": len(failed),
        "failed_candidates_counted_as_accepted": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "input_role_receipt": _file_record(input_receipt_path),
        "input_index": _file_record(input_index_path),
        "private_configuration": _file_record(config_path),
        "full_campaign_authorization_receipt": _file_record(authorization_path),
        "pass_index": _file_record(pass_path),
        "failure_index": _file_record(failure_path),
        "evidence_index": _file_record(evidence_path),
        "simulator_action_taken": False,
    }
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(out_dir, [receipt_path, pass_path, failure_path, evidence_path])
    return receipt


def _run_one(
    *,
    row: Mapping[str, str],
    submitted_sequence: int,
    candidates_dir: Path,
    python_path: Path,
    delegate_path: Path,
    config_path: Path,
    config_sha256: str,
) -> dict[str, Any]:
    candidate_id = row["candidate_id_sha256"]
    geometry_sha = row["geometry_sha256"]
    candidate_dir = candidates_dir / f"{submitted_sequence:06d}_{candidate_id}"
    command = [
        str(python_path),
        str(delegate_path),
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
        "--calibre-summary",
        row["drc_summary_path"],
        "--expected-calibre-summary-sha256",
        row["drc_summary_sha256"],
        "--candidate-id-sha256",
        candidate_id,
        "--geometry-identity-sha256",
        geometry_sha,
        "--out-dir",
        str(candidate_dir),
    ]
    command_sha = hashlib.sha256(
        json.dumps(command, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
    )
    result_path = candidate_dir / SINGLE_RECEIPT_NAME
    common = {
        "submitted_sequence": submitted_sequence,
        "geometry_id": row["geometry_id"],
        "geometry_sha256": geometry_sha,
        "candidate_id_sha256": candidate_id,
        "campaign_phase": row["campaign_phase"],
        "acquisition_source": row["acquisition_source"],
        "campaign_contract_fingerprint": row[
            "campaign_contract_fingerprint"
        ],
        "gds_path": row["gds_path"],
        "gds_sha256": row["gds_sha256"],
        "manifest_path": row["manifest_path"],
        "manifest_sha256": row["manifest_sha256"],
        "return_code": int(completed.returncode),
        "command_argv_sha256": command_sha,
    }
    if completed.returncode == 0 and result_path.is_file():
        try:
            receipt = _read_json(result_path, "Calibre zero-blocking receipt")
            _validate_single_receipt(
                receipt,
                candidate_id=candidate_id,
                geometry_sha=geometry_sha,
                config_sha=config_sha256,
                row=row,
            )
            result = {
                "overall_status": "PASS",
                **common,
                "calibre_receipt_path": str(result_path),
                "calibre_receipt_sha256": _sha256(result_path),
            }
            return _write_delegate_result(candidate_dir, result)
        except (CalibreZeroBlockingBatchError, OSError, json.JSONDecodeError) as exc:
            return _write_delegate_result(
                candidate_dir,
                _failure_result(
                    row,
                    submitted_sequence=submitted_sequence,
                    return_code=completed.returncode,
                    error=f"invalid zero-blocking receipt: {exc}",
                    common=common,
                ),
            )
    return _write_delegate_result(
        candidate_dir,
        _failure_result(
            row,
            submitted_sequence=submitted_sequence,
            return_code=completed.returncode,
            error=_bounded_error(completed.stderr or completed.stdout),
            common=common,
        ),
    )


def _validate_single_receipt(
    receipt: Mapping[str, Any],
    *,
    candidate_id: str,
    geometry_sha: str,
    config_sha: str,
    row: Mapping[str, str],
) -> None:
    checks = {
        "schema": receipt.get("schema") == CALIBRE_ZERO_BLOCKING_RECEIPT_SCHEMA,
        "overall_status": receipt.get("overall_status") == "PASS",
        "decision": receipt.get("decision") == CALIBRE_ZERO_BLOCKING_PASS_DECISION,
        "campaign_id": receipt.get("campaign_id") == CAMPAIGN_ID,
        "contract": receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT,
        "candidate": receipt.get("candidate_id_sha256") == candidate_id,
        "geometry": receipt.get("geometry_identity_sha256") == geometry_sha,
        "config": receipt.get("config_sha256") == config_sha,
        "gds": receipt.get("gds_sha256") == row["gds_sha256"],
        "manifest": receipt.get("manifest_sha256") == row["manifest_sha256"],
        "zero_blocking": receipt.get("calibre_blocking_violations") == 0,
        "source_unchanged": receipt.get("source_files_unchanged") is True,
        "no_simulator": receipt.get("simulator_action_taken") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise CalibreZeroBlockingBatchError(
            "single zero-blocking receipt mismatch: " + ",".join(failed)
        )


def _read_input_rows(path: Path) -> list[dict[str, str]]:
    rows, fields = _read_csv(path)
    missing = set(REQUIRED_INPUT_FIELDS) - set(fields)
    if missing:
        raise CalibreZeroBlockingBatchError(
            f"Calibre pass index lacks fields: {sorted(missing)}"
        )
    candidates: set[str] = set()
    geometries: set[str] = set()
    for line, row in enumerate(rows, start=2):
        for field in REQUIRED_INPUT_FIELDS:
            if not str(row.get(field) or "").strip():
                raise CalibreZeroBlockingBatchError(
                    f"Calibre pass index line {line} has empty {field}"
                )
        candidate = _sha_value(row["candidate_id_sha256"], "candidate")
        geometry = _sha_value(row["geometry_sha256"], "geometry")
        bound_geometry = _sha_value(
            row["candidate_geometry_identity_sha256"], "bound geometry"
        )
        if geometry != bound_geometry:
            raise CalibreZeroBlockingBatchError(
                "Calibre pass geometry aliases do not match"
            )
        if candidate in candidates or geometry in geometries:
            raise CalibreZeroBlockingBatchError(
                "Calibre pass candidate or geometry identities are duplicated"
            )
        candidates.add(candidate)
        geometries.add(geometry)
        row["candidate_id_sha256"] = candidate
        row["geometry_sha256"] = geometry
        if (
            row["campaign_contract_fingerprint"]
            != SCIENTIFIC_CONTRACT_FINGERPRINT
        ):
            raise CalibreZeroBlockingBatchError(
                "Calibre pass campaign fingerprint mismatch"
            )
        if row["overall_status"] != "PASS":
            raise CalibreZeroBlockingBatchError(
                "Calibre pass index contains a non-PASS row"
            )
        if _integer(row["blocking_drc_violation_count"], "blocking DRC count") != 0:
            raise CalibreZeroBlockingBatchError(
                "Calibre pass row contains blocking DRC violations"
            )
        for path_field, sha_field, label in (
            ("gds_path", "gds_sha256", "GDS"),
            ("manifest_path", "manifest_sha256", "layout manifest"),
            ("drc_summary_path", "drc_summary_sha256", "Calibre summary"),
        ):
            _pinned_file(Path(row[path_field]), row[sha_field], label)
    return rows


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
        raise CalibreZeroBlockingBatchError(
            "FULL_CAMPAIGN authorization mismatch"
        )


def _validate_input_receipt(
    receipt: Mapping[str, Any], *, expected_stage: str
) -> None:
    if not (
        receipt.get("overall_status") == "PASS"
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and str(receipt.get("stage") or "").upper() == expected_stage
        and isinstance(receipt.get("pass_index"), Mapping)
    ):
        raise CalibreZeroBlockingBatchError("Calibre batch receipt mismatch")


def _failure_result(
    row: Mapping[str, str],
    *,
    submitted_sequence: int,
    return_code: int,
    error: str,
    common: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **dict(common or {}),
        "overall_status": "FAIL",
        "submitted_sequence": submitted_sequence,
        "geometry_id": row.get("geometry_id", ""),
        "geometry_sha256": row.get("geometry_sha256", ""),
        "candidate_id_sha256": row.get("candidate_id_sha256", ""),
        "terminal_stage": "calibre_zero_blocking_receipt",
        "return_code": int(return_code),
        "error": error or "zero-blocking receipt builder failed",
    }


def _write_delegate_result(
    candidate_dir: Path, result: Mapping[str, Any]
) -> dict[str, Any]:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    result_path = candidate_dir / "BATCH_DELEGATE_RESULT.json"
    _write_json(result_path, result)
    return {
        **dict(result),
        "delegate_result_path": str(result_path),
        "delegate_result_sha256": _sha256(result_path),
    }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibreZeroBlockingBatchError(f"{label} is not an object")
    return value


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if (
        record.get("size_bytes") != path.stat().st_size
        or record.get("sha256") != _sha256(path)
    ):
        raise CalibreZeroBlockingBatchError(f"{label} identity mismatch")
    return path


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise CalibreZeroBlockingBatchError(
            f"{label} is missing or empty: {resolved}"
        )
    return resolved


def _pinned_file(path: Path, expected_sha: str, label: str) -> Path:
    resolved = _regular_file(path, label)
    _require_unchanged(resolved, expected_sha, label)
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalibreZeroBlockingBatchError(f"{label} is not an object")
    return value


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        return [dict(row) for row in reader], fields


def _write_csv(
    path: Path, fields: tuple[str, ...], rows: list[Mapping[str, Any]]
) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), extrasaction="ignore"
        )
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


def _require_unchanged(path: Path, expected_sha: Any, label: str) -> None:
    expected = _sha_value(expected_sha, f"expected {label}")
    if _sha256(path) != expected:
        raise CalibreZeroBlockingBatchError(f"{label} SHA-256 changed")


def _sha_value(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(text) is None:
        raise CalibreZeroBlockingBatchError(f"{label} is not SHA-256")
    return text


def _integer(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CalibreZeroBlockingBatchError(f"{label} is not an integer") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bounded_error(value: str) -> str:
    text = " ".join(str(value or "").split())
    return text[-1000:] if text else "zero-blocking receipt builder failed"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
