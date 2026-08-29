#!/usr/bin/env python3
"""Finalize audited coverage and acquisition histories for broadband56 V2.

The script consumes every frozen Phase-A checkpoint and every 5k Phase-B/C
endpoint.  It does not run a simulator, train a model, or infer missing audit
states.  Any missing, non-terminal, fingerprint-mismatched, or hash-mismatched
input prevents the output directory from being created.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ACQUISITION_SOURCES_BY_PHASE,
    ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS,
    ADAPTIVE_ROUND_START_COUNTS,
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    REQUIRED_CHECKPOINT_COUNTS,
    TARGET_ACCEPTED_GEOMETRIES,
    adaptive_round_spec,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)


REQUIRED_HISTORY_AUDIT_COUNTS = tuple(
    sorted(set(REQUIRED_CHECKPOINT_COUNTS) | set(ADAPTIVE_INTERMEDIATE_AUDIT_COUNTS))
)
KNOWN_ACQUISITION_SOURCES = tuple(
    sorted({source for values in ACQUISITION_SOURCES_BY_PHASE.values() for source in values})
)
REQUIRED_AUDIT_FILES = (
    "CHECKPOINT_STATUS.json",
    "CHECKPOINT_RECEIPT.json",
    "COVERAGE_SUMMARY.json",
    "physical_coverage_cells_by_anchor.csv",
    "SHA256SUMS.txt",
)


class HistoryFinalizationError(RuntimeError):
    """Raised when campaign-history evidence is not terminal and self-consistent."""


@dataclass(frozen=True)
class RoundExpectation:
    round_id: str
    phase: str
    accepted_start: int
    accepted_end: int
    active_source_quotas: tuple[tuple[str, int], ...]
    fallback_source_quotas: tuple[tuple[str, int], ...]

    @property
    def batch_size(self) -> int:
        return self.accepted_end - self.accepted_start


@dataclass(frozen=True)
class AuditEvidence:
    accepted_count: int
    audit_mode: str
    checkpoint_status: str
    path: Path
    receipt_path: Path
    coverage_path: Path
    cells_path: Path
    receipt_sha256: str
    coverage_sha256: str
    cells_sha256: str
    overall_metrics: Mapping[str, Any]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"output path already exists (no-clobber): {out_dir}")
    try:
        result = finalize_campaign_histories(
            contract_path=Path(args.contract).expanduser().resolve(),
            accepted_path=Path(args.accepted_geometries).expanduser().resolve(),
            audit_dirs=[Path(value).expanduser().resolve() for value in args.audit_dir],
            out_dir=out_dir,
        )
    except HistoryFinalizationError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"coverage_deficit_rows={result['coverage_deficit_rows']}")
    print(f"acquisition_round_rows={result['acquisition_round_rows']}")
    print(f"acquisition_source_rows={result['acquisition_source_rows']}")
    print(f"receipt={out_dir / 'CAMPAIGN_HISTORY_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--accepted-geometries", required=True)
    parser.add_argument(
        "--audit-dir",
        action="append",
        required=True,
        help="Repeat once for every frozen Phase-A checkpoint and adaptive 5k endpoint.",
    )
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def finalize_campaign_histories(
    *,
    contract_path: Path,
    accepted_path: Path,
    audit_dirs: Sequence[Path],
    out_dir: Path,
) -> dict[str, int]:
    contract, fingerprint = _validate_contract_file(contract_path)
    rounds = _round_expectations()
    _validate_round_partition(rounds)
    audit_by_count = _load_all_audits(audit_dirs, fingerprint=fingerprint)
    required_counts = set(REQUIRED_HISTORY_AUDIT_COUNTS)
    if set(audit_by_count) != required_counts:
        raise HistoryFinalizationError(
            "audit count set mismatch: "
            f"missing={sorted(required_counts - set(audit_by_count))}, "
            f"extra={sorted(set(audit_by_count) - required_counts)}"
        )
    accepted_summary = _audit_final_accepted_ledger(
        accepted_path,
        fingerprint=fingerprint,
        rounds=rounds,
    )
    final_audit = audit_by_count.get(TARGET_ACCEPTED_GEOMETRIES)
    if final_audit is None:
        raise HistoryFinalizationError("terminal 200k checkpoint audit is missing")
    final_receipt = _read_json(final_audit.receipt_path)
    _verify_file_evidence(
        final_receipt.get("inputs", {}).get("accepted_geometries"),
        accepted_path,
        "terminal accepted ledger",
    )
    round_modes = _validate_round_source_quotas(
        accepted_summary["round_source_counts"], rounds
    )

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise HistoryFinalizationError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        deficit_path = staging / "coverage_deficit_history.csv"
        deficit_rows = _write_coverage_deficit_history(
            deficit_path,
            audit_by_count,
            fingerprint=fingerprint,
        )

        round_path = staging / "acquisition_round_history.csv"
        round_rows = _write_acquisition_round_history(
            round_path,
            rounds=rounds,
            round_source_counts=accepted_summary["round_source_counts"],
            round_modes=round_modes,
            audit_by_count=audit_by_count,
        )

        source_path = staging / "acquisition_source_by_geometry.csv"
        source_rows = _write_acquisition_source_by_geometry(
            source_path,
            accepted_path=accepted_path,
            accepted_ledger_sha256=_sha256(accepted_path),
            rounds=rounds,
            round_modes=round_modes,
            fingerprint=fingerprint,
        )
        if source_rows != TARGET_ACCEPTED_GEOMETRIES:
            raise HistoryFinalizationError(
                f"acquisition source row count mismatch: {source_rows}"
            )

        final_summary_path = staging / "coverage_summary_200k.json"
        shutil.copyfile(final_audit.coverage_path, final_summary_path)
        if _sha256(final_summary_path) != final_audit.coverage_sha256:
            raise HistoryFinalizationError("terminal coverage summary copy changed bytes")

        receipt_path = staging / "CAMPAIGN_HISTORY_RECEIPT.json"
        checks = {
            "all_required_audit_counts_present": set(audit_by_count)
            == set(REQUIRED_HISTORY_AUDIT_COUNTS),
            "all_audits_pass_and_hash_close": True,
            "terminal_accepted_count_exact": accepted_summary["row_count"]
            == TARGET_ACCEPTED_GEOMETRIES,
            "round_partition_exact": sum(item.batch_size for item in rounds)
            == TARGET_ACCEPTED_GEOMETRIES,
            "round_source_quotas_exact_active_or_fallback": True,
            "coverage_deficit_history_row_count_exact": deficit_rows
            == len(REQUIRED_HISTORY_AUDIT_COUNTS)
            * PRIMARY_FREQUENCY_CONDITIONED_CELLS,
            "acquisition_round_history_count_exact": round_rows == len(rounds),
            "acquisition_source_geometry_count_exact": source_rows
            == TARGET_ACCEPTED_GEOMETRIES,
            "coverage_summary_200k_byte_exact_copy": True,
            "proxy_predictions_excluded_from_labels": True,
            "simulator_and_model_training_not_run": True,
        }
        if not all(value is True for value in checks.values()):
            raise HistoryFinalizationError(f"campaign-history checks failed: {checks}")
        receipt = {
            "schema": "broadband56_campaign_history_receipt_v1",
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_CAMPAIGN_HISTORY",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "terminal_counts": {
                "accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
                "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
                "geometry_frequency_rows": TARGET_ACCEPTED_GEOMETRIES
                * len(FREQUENCY_GRID_HZ),
            },
            "audit_counts": list(REQUIRED_HISTORY_AUDIT_COUNTS),
            "inputs": {
                "contract": _file_evidence(contract_path),
                "accepted_geometries": _file_evidence(accepted_path),
                "audit_directories": [
                    {
                        "accepted_count": count,
                        "path": str(audit_by_count[count].path),
                        "receipt_sha256": audit_by_count[count].receipt_sha256,
                        "coverage_summary_sha256": audit_by_count[count].coverage_sha256,
                        "coverage_cells_sha256": audit_by_count[count].cells_sha256,
                    }
                    for count in sorted(audit_by_count)
                ],
            },
            "checks": checks,
            "outputs": {
                "coverage_deficit_history": _file_evidence(
                    deficit_path, recorded_path=out_dir / deficit_path.name
                ),
                "acquisition_round_history": _file_evidence(
                    round_path, recorded_path=out_dir / round_path.name
                ),
                "acquisition_source_by_geometry": _file_evidence(
                    source_path, recorded_path=out_dir / source_path.name
                ),
                "coverage_summary_200k": _file_evidence(
                    final_summary_path,
                    recorded_path=out_dir / final_summary_path.name,
                ),
            },
            "scientific_boundary": (
                "Every history row comes from a PASS fresh-real-EMX checkpoint/round audit. "
                "Acquisition source is accepted-ledger provenance, not a claim that proxy "
                "predictions are labels. Frequency rows remain correlated within geometry."
            ),
            "contract_snapshot": {
                "campaign_id": contract.get("campaign_id"),
                "frequency_points": len(FREQUENCY_GRID_HZ),
            },
        }
        _write_json(receipt_path, receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "coverage_deficit_rows": deficit_rows,
        "acquisition_round_rows": round_rows,
        "acquisition_source_rows": source_rows,
    }


def _round_expectations() -> tuple[RoundExpectation, ...]:
    rows = [
        RoundExpectation(
            round_id="phase_a_base_000000_050000",
            phase="PHASE_A",
            accepted_start=0,
            accepted_end=50_000,
            active_source_quotas=(("base_space_filling", 50_000),),
            fallback_source_quotas=(("base_space_filling", 50_000),),
        )
    ]
    for accepted_start in ADAPTIVE_ROUND_START_COUNTS:
        spec = adaptive_round_spec(accepted_start)
        rows.append(
            RoundExpectation(
                round_id=spec.round_id,
                phase=spec.phase,
                accepted_start=spec.accepted_start,
                accepted_end=spec.accepted_target,
                active_source_quotas=spec.source_quotas,
                fallback_source_quotas=spec.fallback_source_quotas,
            )
        )
    return tuple(rows)


def _validate_round_partition(rounds: Sequence[RoundExpectation]) -> None:
    if not rounds or rounds[0].accepted_start != 0:
        raise HistoryFinalizationError("round partition does not start at zero")
    previous = 0
    seen_ids: set[str] = set()
    for item in rounds:
        if item.round_id in seen_ids or item.accepted_start != previous:
            raise HistoryFinalizationError("round partition is duplicated or non-contiguous")
        if item.accepted_end <= item.accepted_start:
            raise HistoryFinalizationError(f"invalid round bounds: {item.round_id}")
        if sum(value for _, value in item.active_source_quotas) != item.batch_size:
            raise HistoryFinalizationError(f"active quota mismatch: {item.round_id}")
        if sum(value for _, value in item.fallback_source_quotas) != item.batch_size:
            raise HistoryFinalizationError(f"fallback quota mismatch: {item.round_id}")
        seen_ids.add(item.round_id)
        previous = item.accepted_end
    if previous != TARGET_ACCEPTED_GEOMETRIES:
        raise HistoryFinalizationError(
            f"round partition ends at {previous}, expected {TARGET_ACCEPTED_GEOMETRIES}"
        )


def _load_all_audits(
    audit_dirs: Sequence[Path], *, fingerprint: str
) -> dict[int, AuditEvidence]:
    by_count: dict[int, AuditEvidence] = {}
    for directory in audit_dirs:
        evidence = _load_audit(directory, fingerprint=fingerprint)
        if evidence.accepted_count in by_count:
            raise HistoryFinalizationError(
                f"duplicate audit accepted count: {evidence.accepted_count}"
            )
        by_count[evidence.accepted_count] = evidence
    return by_count


def _load_audit(directory: Path, *, fingerprint: str) -> AuditEvidence:
    if not directory.is_dir():
        raise HistoryFinalizationError(f"audit directory does not exist: {directory}")
    for name in REQUIRED_AUDIT_FILES:
        if not (directory / name).is_file():
            raise HistoryFinalizationError(f"audit {directory} is missing {name}")
    _verify_sha256s(directory)
    status_path = directory / "CHECKPOINT_STATUS.json"
    receipt_path = directory / "CHECKPOINT_RECEIPT.json"
    coverage_path = directory / "COVERAGE_SUMMARY.json"
    cells_path = directory / "physical_coverage_cells_by_anchor.csv"
    status = _read_json(status_path)
    receipt = _read_json(receipt_path)
    coverage = _read_json(coverage_path)
    count = _as_int(status.get("accepted_geometries"))
    if count is None or count not in REQUIRED_HISTORY_AUDIT_COUNTS:
        raise HistoryFinalizationError(f"audit has unsupported accepted count: {count}")
    expected_mode = "checkpoint" if count in REQUIRED_CHECKPOINT_COUNTS else "round"
    expected_state = (
        "COMPLETE_200K"
        if count == TARGET_ACCEPTED_GEOMETRIES
        else ("CHECKPOINT_COMPLETE" if expected_mode == "checkpoint" else f"ROUND_{count}_COMPLETE")
    )
    exact_fields = {
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "audit_mode": expected_mode,
        "checkpoint_status": expected_state,
        "s4p_artifacts": count,
        "geometry_frequency_rows": count * len(FREQUENCY_GRID_HZ),
    }
    for field, expected in exact_fields.items():
        if status.get(field) != expected:
            raise HistoryFinalizationError(
                f"audit {count} status {field} mismatch: {status.get(field)!r} != {expected!r}"
            )
    if receipt.get("overall_status") != "PASS" or receipt.get("decision") != "USE_CHECKPOINT":
        raise HistoryFinalizationError(f"audit {count} receipt is not PASS/USE_CHECKPOINT")
    if receipt.get("campaign_id") != CAMPAIGN_ID or receipt.get(
        "contract_fingerprint_sha256"
    ) != fingerprint:
        raise HistoryFinalizationError(f"audit {count} receipt identity mismatch")
    if receipt.get("audit_mode") != expected_mode or _as_int(
        receipt.get("expected_accepted")
    ) != count:
        raise HistoryFinalizationError(f"audit {count} receipt mode/count mismatch")
    receipt_checks = receipt.get("checks")
    if not isinstance(receipt_checks, list) or not receipt_checks:
        raise HistoryFinalizationError(f"audit {count} receipt has no checks")
    if any(not isinstance(item, Mapping) or not bool(item.get("pass")) for item in receipt_checks):
        raise HistoryFinalizationError(f"audit {count} receipt contains a failed/malformed check")
    if coverage.get("campaign_id") != CAMPAIGN_ID or coverage.get(
        "contract_fingerprint_sha256"
    ) != fingerprint:
        raise HistoryFinalizationError(f"audit {count} coverage identity mismatch")
    if _as_int(coverage.get("expected_accepted_geometries")) != count or _as_int(
        coverage.get("feature_row_count")
    ) != count * len(FREQUENCY_GRID_HZ):
        raise HistoryFinalizationError(f"audit {count} coverage count mismatch")
    overall = coverage.get("geometry_unique_anchor_coverage")
    if not isinstance(overall, Mapping):
        raise HistoryFinalizationError(f"audit {count} lacks primary coverage metrics")
    for evidence, path, name in (
        (receipt.get("outputs", {}).get("checkpoint_status"), status_path, "status"),
        (receipt.get("outputs", {}).get("coverage_summary"), coverage_path, "coverage"),
        (receipt.get("outputs", {}).get("coverage_cells"), cells_path, "cells"),
    ):
        _verify_file_evidence(evidence, path, f"audit {count} {name}")
    _validate_cell_table(cells_path, accepted_count=count)
    return AuditEvidence(
        accepted_count=count,
        audit_mode=expected_mode,
        checkpoint_status=expected_state,
        path=directory,
        receipt_path=receipt_path,
        coverage_path=coverage_path,
        cells_path=cells_path,
        receipt_sha256=_sha256(receipt_path),
        coverage_sha256=_sha256(coverage_path),
        cells_sha256=_sha256(cells_path),
        overall_metrics=overall,
    )


def _validate_cell_table(path: Path, *, accepted_count: int) -> None:
    seen: set[int] = set()
    target = accepted_count / float(PRIMARY_CELLS_PER_ANCHOR)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "anchor_ghz",
            "cell_id",
            "local_cell_index",
            "conditioned_cell_index",
            "actual_count",
            "target_count",
            "deficit",
            "cell_status",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise HistoryFinalizationError(f"coverage cell table missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            index = _as_int(row.get("conditioned_cell_index"))
            anchor = _as_int(row.get("anchor_ghz"))
            local = _as_int(row.get("local_cell_index"))
            actual = _as_int(row.get("actual_count"))
            declared_target = _as_float(row.get("target_count"))
            deficit = _as_float(row.get("deficit"))
            if index is None or not 0 <= index < PRIMARY_FREQUENCY_CONDITIONED_CELLS or index in seen:
                raise HistoryFinalizationError(f"cell table line {line} has invalid/duplicate index")
            anchor_index, expected_local = divmod(index, PRIMARY_CELLS_PER_ANCHOR)
            if (
                anchor != ANCHOR_FREQUENCIES_GHZ[anchor_index]
                or local != expected_local
                or row.get("cell_id") != _cell_id(index)
            ):
                raise HistoryFinalizationError(f"cell table line {line} cell identity mismatch")
            if actual is None or actual < 0 or declared_target is None or deficit is None:
                raise HistoryFinalizationError(f"cell table line {line} has invalid numeric values")
            expected_status = (
                "unobserved_under_current_geometry_contract"
                if actual == 0
                else ("underfilled" if actual < target else "observed")
            )
            if not math.isclose(declared_target, target, rel_tol=0.0, abs_tol=1.0e-9):
                raise HistoryFinalizationError(f"cell table line {line} target mismatch")
            if not math.isclose(deficit, max(target - actual, 0.0), rel_tol=0.0, abs_tol=1.0e-9):
                raise HistoryFinalizationError(f"cell table line {line} deficit mismatch")
            if row.get("cell_status") != expected_status:
                raise HistoryFinalizationError(f"cell table line {line} status mismatch")
            seen.add(index)
    if seen != set(range(PRIMARY_FREQUENCY_CONDITIONED_CELLS)):
        raise HistoryFinalizationError(
            f"cell table does not enumerate all fixed cells: {len(seen)}"
        )


def _audit_final_accepted_ledger(
    path: Path,
    *,
    fingerprint: str,
    rounds: Sequence[RoundExpectation],
) -> dict[str, Any]:
    if not path.is_file():
        raise HistoryFinalizationError(f"accepted ledger does not exist: {path}")
    round_counts = {item.round_id: Counter() for item in rounds}
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    row_count = 0
    round_index = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "geometry_id",
            "geometry_sha256",
            "campaign_contract_fingerprint",
            "accepted_sequence",
            "campaign_phase",
            "acquisition_source",
            *(f"geom__{name}" for name in GEOMETRY_FIELDS),
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise HistoryFinalizationError(f"accepted ledger missing columns: {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            row_count += 1
            sequence = _as_int(row.get("accepted_sequence"))
            if sequence != row_count:
                raise HistoryFinalizationError(
                    f"accepted ledger line {line} sequence is not exact contiguous order"
                )
            while round_index < len(rounds) and sequence > rounds[round_index].accepted_end:
                round_index += 1
            if round_index >= len(rounds):
                raise HistoryFinalizationError(f"accepted sequence {sequence} is outside round partition")
            expected_round = rounds[round_index]
            if not expected_round.accepted_start < sequence <= expected_round.accepted_end:
                raise HistoryFinalizationError(f"accepted sequence {sequence} has no round")
            geometry_id = str(row.get("geometry_id") or "").strip()
            supplied_hash = str(row.get("geometry_sha256") or "").strip().lower()
            if not geometry_id or geometry_id in seen_ids or supplied_hash in seen_hashes:
                raise HistoryFinalizationError(f"accepted ledger line {line} identity duplicated/missing")
            values = {name: row.get(f"geom__{name}") for name in GEOMETRY_FIELDS}
            try:
                actual_hash = canonical_geometry_sha256(values)
            except (KeyError, TypeError, ValueError) as exc:
                raise HistoryFinalizationError(f"accepted line {line} geometry hash failed: {exc}") from exc
            if actual_hash != supplied_hash:
                raise HistoryFinalizationError(f"accepted ledger line {line} geometry SHA mismatch")
            if str(row.get("campaign_contract_fingerprint") or "") != fingerprint:
                raise HistoryFinalizationError(f"accepted ledger line {line} fingerprint mismatch")
            phase = str(row.get("campaign_phase") or "")
            source = str(row.get("acquisition_source") or "")
            if phase != expected_round.phase:
                raise HistoryFinalizationError(f"accepted ledger line {line} phase mismatch")
            allowed = set(dict(expected_round.active_source_quotas)) | set(
                dict(expected_round.fallback_source_quotas)
            )
            if source not in allowed:
                raise HistoryFinalizationError(f"accepted ledger line {line} source not allowed")
            seen_ids.add(geometry_id)
            seen_hashes.add(supplied_hash)
            round_counts[expected_round.round_id][source] += 1
    if row_count != TARGET_ACCEPTED_GEOMETRIES:
        raise HistoryFinalizationError(
            f"accepted ledger count mismatch: {row_count} != {TARGET_ACCEPTED_GEOMETRIES}"
        )
    return {"row_count": row_count, "round_source_counts": round_counts}


def _validate_round_source_quotas(
    actual_by_round: Mapping[str, Counter[str]],
    rounds: Sequence[RoundExpectation],
) -> dict[str, str]:
    modes: dict[str, str] = {}
    for item in rounds:
        actual = dict(actual_by_round[item.round_id])
        active = dict(item.active_source_quotas)
        fallback = dict(item.fallback_source_quotas)
        if actual == active:
            modes[item.round_id] = "ACTIVE_MIXTURE"
        elif actual == fallback:
            modes[item.round_id] = "MAXIMIN_FALLBACK"
        else:
            raise HistoryFinalizationError(
                f"round {item.round_id} source counts match neither active nor fallback: {actual}"
            )
    return modes


def _write_coverage_deficit_history(
    path: Path,
    audit_by_count: Mapping[int, AuditEvidence],
    *,
    fingerprint: str,
) -> int:
    fields = [
        "accepted_geometries",
        "audit_mode",
        "checkpoint_status",
        "anchor_ghz",
        "cell_id",
        "local_cell_index",
        "conditioned_cell_index",
        "actual_count",
        "target_count",
        "deficit",
        "cell_status",
        "source_cells_sha256",
        "source_receipt_sha256",
        "campaign_contract_fingerprint",
    ]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for accepted_count in sorted(audit_by_count):
            audit = audit_by_count[accepted_count]
            with audit.cells_path.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    writer.writerow(
                        {
                            "accepted_geometries": accepted_count,
                            "audit_mode": audit.audit_mode,
                            "checkpoint_status": audit.checkpoint_status,
                            "anchor_ghz": row.get("anchor_ghz"),
                            "cell_id": row.get("cell_id"),
                            "local_cell_index": row.get("local_cell_index"),
                            "conditioned_cell_index": row.get("conditioned_cell_index"),
                            "actual_count": row.get("actual_count"),
                            "target_count": row.get("target_count"),
                            "deficit": row.get("deficit"),
                            "cell_status": row.get("cell_status"),
                            "source_cells_sha256": audit.cells_sha256,
                            "source_receipt_sha256": audit.receipt_sha256,
                            "campaign_contract_fingerprint": fingerprint,
                        }
                    )
                    count += 1
    return count


def _write_acquisition_round_history(
    path: Path,
    *,
    rounds: Sequence[RoundExpectation],
    round_source_counts: Mapping[str, Counter[str]],
    round_modes: Mapping[str, str],
    audit_by_count: Mapping[int, AuditEvidence],
) -> int:
    fields = [
        "round_id",
        "campaign_phase",
        "accepted_start",
        "accepted_end",
        "batch_size",
        "execution_mode",
        "actual_source_counts_json",
        "active_source_quotas_json",
        "fallback_source_quotas_json",
        *[f"actual__{source}" for source in KNOWN_ACQUISITION_SOURCES],
        "primary_observed_cells",
        "primary_observed_cell_fraction",
        "primary_normalized_entropy",
        "primary_coefficient_of_variation",
        "primary_gini_coefficient",
        "primary_underfilled_cells",
        "primary_top_1pct_cell_concentration",
        "endpoint_receipt_sha256",
        "endpoint_coverage_summary_sha256",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rounds:
            audit = audit_by_count.get(item.accepted_end)
            if audit is None:
                raise HistoryFinalizationError(
                    f"round endpoint audit missing: {item.accepted_end}"
                )
            actual = dict(round_source_counts[item.round_id])
            metrics = audit.overall_metrics
            writer.writerow(
                {
                    "round_id": item.round_id,
                    "campaign_phase": item.phase,
                    "accepted_start": item.accepted_start,
                    "accepted_end": item.accepted_end,
                    "batch_size": item.batch_size,
                    "execution_mode": round_modes[item.round_id],
                    "actual_source_counts_json": _compact_json(actual),
                    "active_source_quotas_json": _compact_json(dict(item.active_source_quotas)),
                    "fallback_source_quotas_json": _compact_json(
                        dict(item.fallback_source_quotas)
                    ),
                    **{
                        f"actual__{source}": int(actual.get(source, 0))
                        for source in KNOWN_ACQUISITION_SOURCES
                    },
                    "primary_observed_cells": metrics.get("observed_cells"),
                    "primary_observed_cell_fraction": metrics.get(
                        "observed_cell_fraction"
                    ),
                    "primary_normalized_entropy": metrics.get("normalized_entropy"),
                    "primary_coefficient_of_variation": metrics.get(
                        "coefficient_of_variation"
                    ),
                    "primary_gini_coefficient": metrics.get("gini_coefficient"),
                    "primary_underfilled_cells": metrics.get("underfilled_cells"),
                    "primary_top_1pct_cell_concentration": metrics.get(
                        "top_1pct_cell_concentration"
                    ),
                    "endpoint_receipt_sha256": audit.receipt_sha256,
                    "endpoint_coverage_summary_sha256": audit.coverage_sha256,
                }
            )
    return len(rounds)


def _write_acquisition_source_by_geometry(
    path: Path,
    *,
    accepted_path: Path,
    accepted_ledger_sha256: str,
    rounds: Sequence[RoundExpectation],
    round_modes: Mapping[str, str],
    fingerprint: str,
) -> int:
    fields = [
        "geometry_id",
        "geometry_sha256",
        "accepted_sequence",
        "campaign_phase",
        "acquisition_source",
        "round_id",
        "round_execution_mode",
        "campaign_contract_fingerprint",
        "accepted_ledger_sha256",
        "evidence_class",
    ]
    count = 0
    round_index = 0
    with accepted_path.open(newline="", encoding="utf-8-sig") as source, path.open(
        "w", newline="", encoding="utf-8"
    ) as output:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in reader:
            count += 1
            while count > rounds[round_index].accepted_end:
                round_index += 1
            item = rounds[round_index]
            writer.writerow(
                {
                    "geometry_id": row.get("geometry_id"),
                    "geometry_sha256": row.get("geometry_sha256"),
                    "accepted_sequence": row.get("accepted_sequence"),
                    "campaign_phase": row.get("campaign_phase"),
                    "acquisition_source": row.get("acquisition_source"),
                    "round_id": item.round_id,
                    "round_execution_mode": round_modes[item.round_id],
                    "campaign_contract_fingerprint": fingerprint,
                    "accepted_ledger_sha256": accepted_ledger_sha256,
                    "evidence_class": "accepted_fresh_real_emx_geometry_provenance",
                }
            )
    return count


def _validate_contract_file(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise HistoryFinalizationError(f"contract does not exist: {path}")
    contract = _read_json(path)
    errors = validate_contract(contract)
    if errors:
        raise HistoryFinalizationError(f"contract validation failed: {errors[:5]}")
    actual = contract_fingerprint(contract)
    declared = str(contract.get("contract_fingerprint_sha256") or actual)
    if declared != actual:
        raise HistoryFinalizationError("contract fingerprint does not match contract bytes")
    return contract, actual


def _verify_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    seen: set[str] = set()
    for line_number, raw in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise HistoryFinalizationError(f"invalid SHA256SUMS line {line_number}")
        expected, name = parts
        path = directory / name
        if Path(name).name != name or name in seen or not path.is_file():
            raise HistoryFinalizationError(f"invalid SHA256SUMS target: {name!r}")
        seen.add(name)
        if _sha256(path) != expected.lower():
            raise HistoryFinalizationError(f"SHA256SUMS mismatch: {path}")
    required = set(REQUIRED_AUDIT_FILES) - {"SHA256SUMS.txt"}
    if not required.issubset(seen):
        raise HistoryFinalizationError(
            f"SHA256SUMS omits required audit files: {sorted(required - seen)}"
        )


def _verify_file_evidence(evidence: Any, path: Path, name: str) -> None:
    if not isinstance(evidence, Mapping):
        raise HistoryFinalizationError(f"missing receipt evidence: {name}")
    expected = str(evidence.get("sha256") or "").lower()
    if len(expected) != 64 or _sha256(path) != expected:
        raise HistoryFinalizationError(f"receipt SHA mismatch: {name}")


def _file_evidence(path: Path, *, recorded_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(recorded_path if recorded_path is not None else path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HistoryFinalizationError(
            f"failed to parse JSON {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise HistoryFinalizationError(f"JSON root is not an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not parsed.is_integer():
        return None
    return int(parsed)


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def _cell_id(conditioned_index: int) -> str:
    anchor_index, local = divmod(int(conditioned_index), PRIMARY_CELLS_PER_ANCHOR)
    bins = 6
    k_bin = local % bins
    local //= bins
    qmin_bin = local % bins
    local //= bins
    xs_bin = local % bins
    xp_bin = local // bins
    anchor = ANCHOR_FREQUENCIES_GHZ[anchor_index]
    return f"f{anchor:02d}_xp{xp_bin}_xs{xs_bin}_q{qmin_bin}_k{k_bin}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
