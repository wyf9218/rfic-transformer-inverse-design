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


def pending_frozen_cohort(
    terminal_ledgers: list[Path], *, excluded_hashes: set[str], fingerprint: str,
    seed: int, sampler: str, acquisition_source: str, phase: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Find unfinished source rows using only already validated terminal ledgers.

    No active attempt directory is discovered or claimed here. The caller must
    validate the campaign progress chain before supplying these ledger paths.
    """
    sources: dict[str, str] = {}
    ledger_pins = []
    for ledger in dict.fromkeys(terminal_ledgers):
        ledger_pins.append(file_identity(ledger))
        with ledger.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                if row.get("terminal_stage") == "GOLDEN_VALIDATION_PASS":
                    continue
                path, sha = row.get("candidate_source_path"), row.get("candidate_source_sha256")
                if not path and not sha:
                    if row.get("attempt_id"):
                        raise ValueError("terminal attempt lacks candidate source identity")
                    continue  # Validated Golden anchor exclusions have no attempt.
                if not path or not sha or (path in sources and sources[path] != sha):
                    raise ValueError("terminal ledger candidate source identities disagree")
                sources[path] = sha
    cohorts: dict[str, dict[str, Any]] = {}
    for path, sha in sources.items():
        queue = Path(path)
        pin = file_identity(queue)
        if not queue.is_absolute() or pin["sha256"] != sha:
            raise ValueError("terminal candidate source CSV identity mismatch")
        receipt_path = queue.with_name("broadband56_candidate_queue_summary.json")
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("candidate_queue") != pin:
            raise ValueError("terminal candidate source summary/CSV mismatch")
        if "frozen_batch" in receipt:
            source = receipt["frozen_batch"]["source_receipt"]
            source_path = Path(source["path"])
            validate_frozen_selection(receipt_path, source_receipt_path=source_path,
                source_receipt_sha256=source["sha256"],
                candidate_ceiling=receipt["requested_candidate_ceiling"], fingerprint=fingerprint)
            receipt_path = source_path
            receipt = json.loads(receipt_path.read_text())
        source_pin = file_identity(receipt_path)
        # Validate even exhausted sources; corruption is not permission to resample.
        _, proof = select_frozen_queue(receipt_path, source_pin["sha256"], count=1,
            excluded_hashes=set(), fingerprint=fingerprint, seed=receipt["seed"],
            sampler=receipt["sampler"], acquisition_source=receipt["acquisition_source"],
            phase=receipt["campaign_phase"])
        with Path(proof["source_queue"]["path"]).open(newline="") as stream:
            remaining = sum(row["geometry_sha256"] not in excluded_hashes for row in csv.DictReader(stream))
        if remaining:
            expected = {"seed": seed, "sampler": sampler,
                        "acquisition_source": acquisition_source, "campaign_phase": phase}
            if any(receipt.get(key) != value for key, value in expected.items()):
                raise ValueError("unfinished cohort sampling contract differs; cannot discard it")
        cohorts[source_pin["path"]] = {"source_receipt": source_pin,
            "source_candidate_count": proof["source_candidate_count"], "remaining_count": remaining}
    pending = [row["source_receipt"] for row in cohorts.values() if row["remaining_count"]]
    if len(pending) > 1:
        raise ValueError("multiple unfinished frozen cohorts; no unique dispatch source")
    if any(file_identity(Path(pin["path"])) != pin for pin in ledger_pins):
        raise ValueError("terminal ledger changed during cohort discovery")
    return (pending[0] if pending else None), {
        "terminal_ledgers": ledger_pins, "cohorts": list(cohorts.values()),
        "all_prior_cohorts_terminal": not pending, "active_attempts_discovered": False,
        "dispatch_claim_created": False,
    }


def validate_frozen_selection(
    receipt_path: Path, *, source_receipt_path: Path, source_receipt_sha256: str,
    candidate_ceiling: int, fingerprint: str,
) -> dict[str, Any]:
    """Recheck the actual selected CSV before the first simulator role.

    A short final slice is allowed, but it is never reported as a full slice
    or as progress toward the accepted checkpoint before physical validation.
    """
    if type(candidate_ceiling) is not int or not 1 <= candidate_ceiling <= 48:
        raise ValueError("frozen selection ceiling must be 1..48")
    receipt_pin = file_identity(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    source_pin = file_identity(source_receipt_path)
    if source_pin["sha256"] != source_receipt_sha256:
        raise ValueError("frozen source receipt SHA mismatch at dispatch")
    source = json.loads(source_receipt_path.read_text())
    proof = receipt.get("frozen_batch", {})
    expected = {
        "overall_status": "PASS", "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "canonical_geometry_fields": list(GEOMETRY_FIELDS),
    }
    for value in (source, receipt):
        if any(value.get(key) != item for key, item in expected.items()):
            raise ValueError("frozen selection contract mismatch at dispatch")
        checks = value.get("checks")
        if not isinstance(checks, list) or not checks or any(c.get("pass") is not True for c in checks):
            raise ValueError("frozen selection lacks passing source checks")
    for key in ("seed", "sampler", "acquisition_source", "campaign_phase"):
        if key not in source or source[key] != receipt.get(key):
            raise ValueError("frozen selection sampling provenance changed")
    if (proof.get("source_receipt") != source_pin
            or proof.get("source_queue") != source.get("candidate_queue")
            or proof.get("requested_candidate_ceiling") != candidate_ceiling
            or receipt.get("requested_candidate_ceiling") != candidate_ceiling
            or proof.get("sampler_executed") is not False
            or proof.get("source_rows_changed") is not False
            or proof.get("dispatch_claim_created") is not False
            or receipt.get("sampling_attempts") != 0):
        raise ValueError("frozen selection source or ceiling binding mismatch")
    groups = []
    for value in (source, receipt):
        pin = value["candidate_queue"]
        path = Path(pin["path"])
        if not path.is_absolute() or file_identity(path) != pin:
            raise ValueError("frozen selection CSV identity mismatch at dispatch")
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        if (not rows or value.get("queue_count") != len(rows)
                or any(not row.get(key) for row in rows for key in ("candidate_id_sha256", "geometry_sha256"))
                or any(len({row[key] for row in rows}) != len(rows)
                       for key in ("candidate_id_sha256", "geometry_sha256"))):
            raise ValueError("frozen selection count or uniqueness mismatch")
        groups.append(rows)
    original, selected = groups
    indexes = proof.get("source_row_indexes")
    if (not isinstance(indexes, list) or len(indexes) != len(selected)
            or any(type(i) is not int or not 0 <= i < len(original) for i in indexes)
            or indexes != sorted(set(indexes))
            or selected != [original[i] for i in indexes]):
        raise ValueError("selected rows differ from frozen source rows")
    count = len(selected)
    if (not 1 <= count <= candidate_ceiling
            or proof.get("selected_count") != count
            or proof.get("source_candidate_count") != len(original)
            or receipt.get("requested_count") != count):
        raise ValueError("frozen selected count differs from actual CSV")
    if file_identity(receipt_path) != receipt_pin or file_identity(source_receipt_path) != source_pin:
        raise ValueError("frozen selection receipt changed during validation")
    return {"queue_receipt": receipt_pin, "source_receipt": source_pin,
            "source_queue": source["candidate_queue"], "selected_queue": receipt["candidate_queue"],
            "source_row_indexes": indexes, "candidate_ceiling": candidate_ceiling,
            "actual_selected_candidates": count, "source_candidate_count": len(original),
            "evidence_class": "GEOMETRY_SELECTION_NOT_PHYSICAL_ACCEPTANCE",
            "accepted_increment": 0, "source_rows_changed": False}


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
    if type(count) is not int or not 1 <= count <= 48:
        raise ValueError("frozen queue attempt count must be 1..48")
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
