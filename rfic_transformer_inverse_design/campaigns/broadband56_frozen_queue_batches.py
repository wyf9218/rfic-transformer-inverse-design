"""Select bounded attempts without rerunning the geometry sampler."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .broadband56_balanced200k import CAMPAIGN_ID, GEOMETRY_FIELDS


def file_identity(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def select_frozen_queue(
    receipt_path: Path, expected_sha256: str, *, count: int,
    excluded_hashes: set[str], fingerprint: str, seed: int,
    sampler: str, acquisition_source: str, phase: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Preserve every selected CSV field, including original candidate indexes.

    This is a read-only selection, not a job claim. The sole backend owns
    dispatch and its validated terminal ledger supplies the exclusion set.
    Exhaustion fails closed; it never silently samples replacement geometry.
    """
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("frozen queue attempt count must be 1..32")
    receipt_pin = file_identity(receipt_path)
    if receipt_pin["sha256"] != expected_sha256:
        raise ValueError("frozen queue receipt SHA mismatch")
    receipt = json.loads(receipt_path.read_text())
    expected = {"overall_status": "PASS", "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint, "seed": seed,
                "sampler": sampler, "acquisition_source": acquisition_source,
                "campaign_phase": phase, "canonical_geometry_fields": list(GEOMETRY_FIELDS)}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("frozen queue contract or sampling provenance mismatch")
    checks = receipt.get("checks")
    if not isinstance(checks, list) or not checks or any(c.get("pass") is not True for c in checks):
        raise ValueError("frozen queue lacks passing source checks")
    source_pin = receipt["candidate_queue"]
    source_path = Path(source_pin["path"])
    if not source_path.is_absolute() or file_identity(source_path) != source_pin:
        raise ValueError("frozen queue CSV identity mismatch")
    with source_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    ids = [row.get("candidate_id_sha256") for row in rows]
    geometries = [row.get("geometry_sha256") for row in rows]
    if (not rows or receipt.get("queue_count") != len(rows)
            or len(set(ids)) != len(rows) or len(set(geometries)) != len(rows)
            or not all(ids) or not all(geometries)):
        raise ValueError("frozen queue count or candidate uniqueness mismatch")
    remaining = [(index, row) for index, row in enumerate(rows)
                 if row["geometry_sha256"] not in excluded_hashes]
    if not remaining:
        raise ValueError("frozen queue exhausted; do not silently resample")
    selected = remaining[:count]
    proof = {"source_receipt": receipt_pin, "source_queue": source_pin,
             "source_candidate_count": len(rows), "source_row_indexes": [i for i, _ in selected],
             "requested_candidate_ceiling": count, "selected_count": len(selected),
             "remaining_unselected_count": len(remaining)-len(selected),
             "sampler_executed": False, "source_rows_changed": False,
             "dispatch_claim_created": False}
    return [dict(row) for _, row in selected], proof
