#!/usr/bin/env python3
"""Partition exact-56 S4P QA outcomes and aggregate accepted products."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as qa_module  # noqa: E402
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
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


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_exact56_s4p_qa_batch.v1"
RECEIPT_NAME = "EXACT56_S4P_QA_BATCH_ROLE_RECEIPT.json"
PASS_INDEX_NAME = "EXACT_GDS_EMX_QA_PASS_INDEX.csv"
QA_INDEX_NAME = "S4P_ARTIFACT_INDEX.csv"
LONG_FEATURES_NAME = "BROADBAND_FEATURES_LONG.csv"
FAILURE_INDEX_NAME = "EXACT56_S4P_QA_FAILURE_INDEX.csv"
EVIDENCE_INDEX_NAME = "EXACT56_S4P_QA_EVIDENCE_INDEX.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

INPUT_FIELDS = (
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

FAILURE_FIELDS = (
    "submitted_sequence",
    "geometry_id",
    "geometry_sha256",
    "candidate_id_sha256",
    "terminal_stage",
    "error",
    "qa_failure_path",
    "qa_failure_sha256",
)

EVIDENCE_FIELDS = (
    "submitted_sequence",
    "geometry_sha256",
    "candidate_id_sha256",
    "overall_status",
    "qa_receipt_path",
    "qa_receipt_sha256",
    "qa_failure_path",
    "qa_failure_sha256",
)


class Exact56QaBatchError(RuntimeError):
    """Raised when exact-56 QA cannot be partitioned without ambiguity."""


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
    except (Exact56QaBatchError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"qa_pass_count={receipt['qa_pass_count']}")
    print(f"qa_fail_count={receipt['qa_fail_count']}")
    print(f"geometry_frequency_rows={receipt['geometry_frequency_rows']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input-role-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--max-concurrency", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def run_batch(args: argparse.Namespace, *, out_dir: Path) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    if stage not in {item.name for item in STAGES}:
        raise Exact56QaBatchError(f"unknown campaign stage: {stage}")
    max_concurrency = int(args.max_concurrency)
    if max_concurrency < 1:
        raise Exact56QaBatchError("max_concurrency must be positive")

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
        raise Exact56QaBatchError("backend manifest campaign or contract mismatch")
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    self_path = _identity_path(
        scripts.get("full_band_s4p_qa_builder"), "exact56 QA batch role"
    )
    if self_path != Path(__file__).resolve():
        raise Exact56QaBatchError("exact56 QA batch self-identity mismatch")
    module_path = _identity_path(
        scripts.get("full_band_s4p_qa_module"), "exact56 QA module"
    )
    if module_path != Path(qa_module.__file__).resolve():
        raise Exact56QaBatchError("exact56 QA module import identity mismatch")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    _validate_authorization(authorization, manifest_sha=manifest_sha)
    input_receipt_path = _regular_file(
        Path(args.input_role_receipt), "exact GDS fresh-EMX batch receipt"
    )
    input_receipt = _read_json(
        input_receipt_path, "exact GDS fresh-EMX batch receipt"
    )
    _validate_input_receipt(input_receipt, expected_stage=stage)
    input_index_path = _identity_path(
        input_receipt.get("pass_index"), "fresh-EMX pass index"
    )
    rows, input_fields = _read_input_rows(input_index_path)
    pins = {
        "manifest": (manifest_path, manifest_sha),
        "module": (module_path, _sha256(module_path)),
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
                candidate_dir=candidates_dir
                / f"{index + 1:06d}_{row['candidate_id_sha256']}",
            ): index
            for index, row in enumerate(rows)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                terminal[index] = future.result()
            except Exception as exc:  # Preserve per-candidate QA failure.
                terminal[index] = _failure_result(
                    row=rows[index],
                    submitted_sequence=index + 1,
                    error=f"{type(exc).__name__}: {exc}",
                    candidate_dir=candidates_dir
                    / f"{index + 1:06d}_{rows[index]['candidate_id_sha256']}",
                )
    results = [item for item in terminal if item is not None]
    if len(results) != len(rows):
        raise Exact56QaBatchError("exact56 QA terminal partition is incomplete")
    passed = [item for item in results if item["overall_status"] == "PASS"]
    failed = [item for item in results if item["overall_status"] != "PASS"]

    pass_rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for accepted_sequence, item in enumerate(passed, start=1):
        source = dict(item["source_row"])
        source["accepted_sequence"] = accepted_sequence
        pass_rows.append(source)
        qa_row = dict(item["qa_row"])
        qa_row["accepted_sequence"] = accepted_sequence
        qa_rows.append(qa_row)
        for feature_row in item["feature_rows"]:
            output = dict(feature_row)
            output["accepted_sequence"] = accepted_sequence
            feature_rows.append(output)
    failure_rows = [
        {field: item.get(field, "") for field in FAILURE_FIELDS}
        for item in failed
    ]
    evidence_rows = [
        {field: item.get(field, "") for field in EVIDENCE_FIELDS}
        for item in results
    ]
    if len(feature_rows) != len(passed) * len(FREQUENCY_GRID_HZ):
        raise Exact56QaBatchError(
            "aggregate feature row count is not QA pass count times 56"
        )

    pass_path = out_dir / PASS_INDEX_NAME
    qa_path = out_dir / QA_INDEX_NAME
    feature_path = out_dir / LONG_FEATURES_NAME
    failure_path = out_dir / FAILURE_INDEX_NAME
    evidence_path = out_dir / EVIDENCE_INDEX_NAME
    _write_csv(pass_path, input_fields, pass_rows)
    _write_csv(qa_path, list(qa_module.QA_INDEX_FIELDS), qa_rows)
    _write_csv(feature_path, list(qa_module.LONG_FEATURE_FIELDS), feature_rows)
    _write_csv(failure_path, list(FAILURE_FIELDS), failure_rows)
    _write_csv(evidence_path, list(EVIDENCE_FIELDS), evidence_rows)

    for label, (path, digest) in pins.items():
        _require_unchanged(path, digest, label)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "TERMINAL_PARTITION_EXACT56_S4P_QA",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "submitted_count": len(rows),
        "terminal_count": len(results),
        "qa_pass_count": len(passed),
        "qa_fail_count": len(failed),
        "geometry_frequency_rows": len(feature_rows),
        "failed_candidates_counted_as_accepted": False,
        "proxy_or_historical_labels_used": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "full_campaign_authorization_receipt": _file_record(
            authorization_path
        ),
        "input_role_receipt": _file_record(input_receipt_path),
        "input_index": _file_record(input_index_path),
        "qa_pass_index": _file_record(pass_path),
        "exact_gds_emx_receipt_index": _file_record(pass_path),
        "s4p_artifact_index": _file_record(qa_path),
        "long_features": _file_record(feature_path),
        "failure_index": _file_record(failure_path),
        "evidence_index": _file_record(evidence_path),
        "simulator_action_taken": False,
    }
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(
        out_dir,
        [
            receipt_path,
            pass_path,
            qa_path,
            feature_path,
            failure_path,
            evidence_path,
        ],
    )
    return receipt


def _run_one(
    *,
    row: Mapping[str, str],
    submitted_sequence: int,
    candidate_dir: Path,
) -> dict[str, Any]:
    candidate_dir.mkdir(mode=0o700)
    input_path = candidate_dir / "FRESH_EMX_RECEIPT_INDEX.csv"
    one_row = dict(row)
    one_row["accepted_sequence"] = 1
    _write_csv(input_path, list(row), [one_row])
    qa_dir = candidate_dir / "qa"
    try:
        result = qa_module.build_exact56_s4p_qa_products(
            input_index_path=input_path,
            out_dir=qa_dir,
            expected_geometry_count=1,
        )
        receipt_path = _regular_file(
            Path(result["receipt_path"]), "candidate QA receipt"
        )
        receipt = _read_json(receipt_path, "candidate QA receipt")
        if not (
            receipt.get("overall_status") == "PASS"
            and receipt.get("geometry_count") == 1
            and receipt.get("geometry_frequency_rows") == len(FREQUENCY_GRID_HZ)
            and receipt.get("proxy_or_historical_labels_used") is False
            and receipt.get("simulator_action_taken") is False
        ):
            raise Exact56QaBatchError("candidate QA receipt contract mismatch")
        qa_rows, _ = _read_csv(Path(result["qa_index_path"]))
        feature_rows, _ = _read_csv(Path(result["long_features_path"]))
        if len(qa_rows) != 1 or len(feature_rows) != len(FREQUENCY_GRID_HZ):
            raise Exact56QaBatchError("candidate QA output grain mismatch")
        return {
            "overall_status": "PASS",
            "submitted_sequence": submitted_sequence,
            "geometry_sha256": row["geometry_sha256"],
            "candidate_id_sha256": row["candidate_id_sha256"],
            "qa_receipt_path": str(receipt_path),
            "qa_receipt_sha256": _sha256(receipt_path),
            "qa_failure_path": "",
            "qa_failure_sha256": "",
            "source_row": dict(row),
            "qa_row": qa_rows[0],
            "feature_rows": feature_rows,
        }
    except Exception as exc:
        return _failure_result(
            row=row,
            submitted_sequence=submitted_sequence,
            error=f"{type(exc).__name__}: {exc}",
            candidate_dir=candidate_dir,
        )


def _failure_result(
    *,
    row: Mapping[str, str],
    submitted_sequence: int,
    error: str,
    candidate_dir: Path,
) -> dict[str, Any]:
    failure_path = candidate_dir / "qa" / qa_module.QA_FAILURE_NAME
    return {
        "overall_status": "FAIL",
        "submitted_sequence": submitted_sequence,
        "geometry_id": row.get("geometry_id", ""),
        "geometry_sha256": row.get("geometry_sha256", ""),
        "candidate_id_sha256": row.get("candidate_id_sha256", ""),
        "terminal_stage": "exact56_s4p_qa",
        "error": error,
        "qa_receipt_path": "",
        "qa_receipt_sha256": "",
        "qa_failure_path": str(failure_path) if failure_path.is_file() else "",
        "qa_failure_sha256": _sha256(failure_path) if failure_path.is_file() else "",
    }


def _read_input_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    rows, fields = _read_csv(path)
    missing = set(INPUT_FIELDS) - set(fields)
    if missing:
        raise Exact56QaBatchError(
            f"fresh-EMX pass index lacks fields: {sorted(missing)}"
        )
    candidates: set[str] = set()
    geometries: set[str] = set()
    for line, row in enumerate(rows, start=2):
        for field in INPUT_FIELDS:
            if not str(row.get(field) or "").strip():
                raise Exact56QaBatchError(
                    f"fresh-EMX pass index line {line} has empty {field}"
                )
        candidate = _sha_value(row["candidate_id_sha256"], "candidate")
        geometry = _sha_value(row["geometry_sha256"], "geometry")
        if candidate in candidates or geometry in geometries:
            raise Exact56QaBatchError(
                "fresh-EMX pass candidate or geometry identities are duplicated"
            )
        candidates.add(candidate)
        geometries.add(geometry)
        if (
            row["campaign_contract_fingerprint"]
            != SCIENTIFIC_CONTRACT_FINGERPRINT
        ):
            raise Exact56QaBatchError("fresh-EMX pass fingerprint mismatch")
        _pinned_file(
            Path(row["exact_gds_emx_receipt_path"]),
            row["exact_gds_emx_receipt_sha256"],
            "exact GDS fresh-EMX receipt",
        )
    return rows, fields


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
        raise Exact56QaBatchError("FULL_CAMPAIGN authorization mismatch")


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
        raise Exact56QaBatchError("fresh-EMX batch receipt mismatch")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Exact56QaBatchError(f"{label} is not an object")
    return value


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if (
        record.get("size_bytes") != path.stat().st_size
        or record.get("sha256") != _sha256(path)
    ):
        raise Exact56QaBatchError(f"{label} identity mismatch")
    return path


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise Exact56QaBatchError(f"{label} is missing or empty: {resolved}")
    return resolved


def _pinned_file(path: Path, expected_sha: str, label: str) -> Path:
    resolved = _regular_file(path, label)
    _require_unchanged(resolved, expected_sha, label)
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Exact56QaBatchError(f"{label} is not an object")
    return value


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [
            {key: str(value or "").strip() for key, value in row.items()}
            for row in reader
        ]
    return rows, fields


def _write_csv(
    path: Path, fields: list[str], rows: list[Mapping[str, Any]]
) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore"
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
        raise Exact56QaBatchError(f"{label} SHA-256 changed")


def _sha_value(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(text) is None:
        raise Exact56QaBatchError(f"{label} is not SHA-256")
    return text


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
