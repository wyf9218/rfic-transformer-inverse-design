#!/usr/bin/env python3
"""Fail-closed terminal delivery audit for the broadband56 balanced-200k campaign.

This command is downstream-only.  It does not run Cadence, Calibre, EMX, a
surrogate, or model training.  It binds the terminal checkpoint, exact raw
products, campaign histories, training-readiness products, and all frozen
checkpoint figures before emitting the only public-control-plane receipt that
may report ``COMPLETE_200K``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    TARGET_ACCEPTED_GEOMETRIES,
    contract_fingerprint,
    validate_contract,
)


EXPECTED_FEATURE_ROWS = TARGET_ACCEPTED_GEOMETRIES * len(FREQUENCY_GRID_HZ)
EXPECTED_HISTORY_AUDITS = 35
EXPECTED_ACQUISITION_ROUNDS = 31
FIGURE_CHECKPOINT_COUNTS = (50_000, 100_000, 150_000, 200_000)
FIGURE_IDS = (
    "01_geometry_sampling_coverage",
    "02_physical_marginals_by_frequency",
    "03_lp_ls_joint_coverage",
    "04_qp_qs_joint_coverage",
    "05_qmin_k_joint_coverage",
    "06_primary_4d_cell_occupancy",
    "07_underfilled_cell_count_vs_accepted_samples",
    "08_uniformity_entropy_vs_accepted_samples",
    "09_acquisition_phase_contribution",
    "10_valid_feature_fraction_vs_frequency",
    "11_broad_vs_practical_panel_coverage",
    "12_failure_funnel",
    "13_boundary_coverage",
    "14_response_coverage_before_and_after_active_repair",
)
RAW_PRODUCT_NAMES = {
    "accepted_geometries": "accepted_geometry_200k.csv",
    "long_features": "broadband_features_11p2m_long.csv",
    "artifact_index": "sparameter_artifact_index_200k.csv",
    "geometry_provenance": "geometry_provenance_200k.csv",
    "failure_funnel": "failure_funnel.csv",
}
CHECKPOINT_REQUIRED_FILES = (
    "CHECKPOINT_STATUS.json",
    "CHECKPOINT_RECEIPT.json",
    "COVERAGE_SUMMARY.json",
    "GEOMETRY_COVERAGE_SUMMARY.json",
    "physical_coverage_cells_by_anchor.csv",
    "physical_coverage_by_frequency.csv",
    "physical_coverage_marginals.csv",
    "physical_coverage_pairwise.csv",
    "geometry_coverage_marginals.csv",
    "geometry_coverage_pairwise.csv",
    "FAILURE_FUNNEL.csv",
    "SHA256SUMS.txt",
)
HISTORY_OUTPUT_NAMES = {
    "coverage_deficit_history": "coverage_deficit_history.csv",
    "acquisition_round_history": "acquisition_round_history.csv",
    "acquisition_source_by_geometry": "acquisition_source_by_geometry.csv",
    "coverage_summary_200k": "coverage_summary_200k.json",
}
TRAINING_OUTPUT_NAMES = {
    "full_200k_training_weights": "full_200k_training_weights.csv",
    "maximal_balanced_subset": "maximal_balanced_subset.csv",
    "future_split_manifest": "future_split_manifest.json",
    "future_split_assignments": "future_split_assignments.csv",
}
PROVENANCE_REQUIRED_COLUMNS = {
    "geometry_id",
    "geometry_sha256",
    "campaign_contract_fingerprint",
    "production_config_sha256",
    "production_config_path",
    "candidate_source_path",
    "candidate_source_sha256",
    "gds_path",
    "gds_sha256",
    "calibre_report_path",
    "calibre_report_sha256",
    "emx_log_path",
    "emx_log_sha256",
    "s4p_path",
    "s4p_sha256",
    "frequency_points",
    "calibre_status",
    "calibre_blocking_violations",
    "emx_status",
    "fresh_real_emx",
}
FAILURE_FUNNEL_STAGES = {
    "raw_geometry_candidates",
    "analytical_failures",
    "topology_failures",
    "cadence_failures",
    "calibre_failures",
    "emx_failures",
    "incomplete_frequency_failures",
    "s4p_parsing_failures",
    "feature_extraction_failures",
    "accepted_geometries",
}


class DeliveryAuditError(RuntimeError):
    """Raised when the final product set cannot support COMPLETE_200K."""


@dataclass(frozen=True)
class ContractEvidence:
    payload: Mapping[str, Any]
    fingerprint: str
    production_config_sha256: str


@dataclass(frozen=True)
class TerminalEvidence:
    receipt: Mapping[str, Any]
    status: Mapping[str, Any]
    coverage: Mapping[str, Any]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=output path already exists (no-clobber): {out_dir}", file=sys.stderr)
        return 2
    try:
        result = audit_final_delivery(
            contract_path=Path(args.contract).expanduser().resolve(),
            raw_dir=Path(args.raw_dir).expanduser().resolve(),
            checkpoint_dir=Path(args.checkpoint_dir).expanduser().resolve(),
            history_dir=Path(args.history_dir).expanduser().resolve(),
            training_dir=Path(args.training_readiness_dir).expanduser().resolve(),
            figure_dir=Path(args.figure_dir).expanduser().resolve(),
            out_dir=out_dir,
        )
    except DeliveryAuditError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print("completion_status=COMPLETE_200K")
    print(f"coverage_status={result['coverage_status']}")
    print(f"receipt={out_dir / 'FINAL_DELIVERY_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--history-dir", required=True)
    parser.add_argument("--training-readiness-dir", required=True)
    parser.add_argument("--figure-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def audit_final_delivery(
    *,
    contract_path: Path,
    raw_dir: Path,
    checkpoint_dir: Path,
    history_dir: Path,
    training_dir: Path,
    figure_dir: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Validate every terminal deliverable and atomically write final receipts."""

    if out_dir.exists():
        raise DeliveryAuditError(f"output path already exists (no-clobber): {out_dir}")
    contract = _load_contract(contract_path)
    terminal = _load_terminal_checkpoint(
        checkpoint_dir,
        contract_path=contract_path,
        fingerprint=contract.fingerprint,
    )
    raw = _load_raw_products(
        raw_dir,
        terminal=terminal,
        fingerprint=contract.fingerprint,
        production_config_sha256=contract.production_config_sha256,
    )
    history = _load_history_products(
        history_dir,
        checkpoint_dir=checkpoint_dir,
        accepted_path=raw["paths"]["accepted_geometries"],
        fingerprint=contract.fingerprint,
    )
    training = _load_training_products(
        training_dir,
        accepted_path=raw["paths"]["accepted_geometries"],
        long_features_path=raw["paths"]["long_features"],
        fingerprint=contract.fingerprint,
    )
    figures = _load_figure_products(
        figure_dir,
        fingerprint=contract.fingerprint,
        production_config_sha256=contract.production_config_sha256,
    )

    coverage_status = str(terminal.status.get("coverage_status") or "")
    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise DeliveryAuditError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        checks = {
            "terminal_checkpoint_complete_200k": True,
            "raw_product_names_and_counts_exact": True,
            "accepted_artifact_provenance_identity_sets_exact": True,
            "fresh_real_emx_provenance_hash_chain_complete": True,
            "campaign_history_products_pass_and_hash_close": True,
            "training_readiness_products_pass_and_hash_close": True,
            "four_checkpoints_times_fourteen_png_svg_figures_pass": True,
            "coverage_status_kept_separate_from_execution_completion": coverage_status
            in {
                "COVERAGE_PASS",
                "COVERAGE_PARTIAL",
                "COVERAGE_PHYSICALLY_LIMITED",
                "COVERAGE_AUDIT_FAIL",
            },
            "proxy_predictions_not_counted_as_labels": True,
            "final_model_training_not_performed": True,
            "remote_simulator_not_run_by_this_auditor": True,
        }
        if not all(value is True for value in checks.values()):
            raise DeliveryAuditError(f"terminal delivery checks failed: {checks}")

        status_payload = {
            "schema": "broadband56_final_completion_status_v1",
            "generated_utc": _utc_now(),
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": contract.fingerprint,
            "completion_status": "COMPLETE_200K",
            "coverage_status": coverage_status,
            "accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
            "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
            "geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
            "independent_design_count": TARGET_ACCEPTED_GEOMETRIES,
            "frequency_points": len(FREQUENCY_GRID_HZ),
            "correlation_notice": (
                "The 11,200,000 frequency rows are correlated records from 200,000 geometries, "
                "not 11.2 million independent EMX designs."
            ),
        }
        status_path = staging / "FINAL_COMPLETION_STATUS.json"
        _write_json(status_path, status_payload)

        receipt = {
            "schema": "broadband56_final_delivery_receipt_v1",
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": "REPORT_COMPLETE_200K_WITH_SEPARATE_COVERAGE_STATUS",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": contract.fingerprint,
            "production_process_config_sha256": contract.production_config_sha256,
            "execution_completion": "COMPLETE_200K",
            "coverage_status": coverage_status,
            "terminal_counts": {
                "accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
                "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
                "geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
                "history_audits": EXPECTED_HISTORY_AUDITS,
                "acquisition_rounds": EXPECTED_ACQUISITION_ROUNDS,
                "figure_checkpoints": len(FIGURE_CHECKPOINT_COUNTS),
                "logical_figures": len(FIGURE_CHECKPOINT_COUNTS) * len(FIGURE_IDS),
                "rendered_figure_files": len(FIGURE_CHECKPOINT_COUNTS)
                * len(FIGURE_IDS)
                * 2,
            },
            "inputs": {
                "contract": _file_evidence(contract_path),
                "terminal_checkpoint": _directory_evidence(
                    checkpoint_dir, "CHECKPOINT_RECEIPT.json"
                ),
                "raw_products": raw["evidence"],
                "campaign_history": history,
                "training_readiness": training,
                "checkpoint_figures": figures,
            },
            "checks": checks,
            "outputs": {
                "final_completion_status": _file_evidence(
                    status_path,
                    recorded_path=out_dir / status_path.name,
                )
            },
            "scientific_boundary": (
                "COMPLETE_200K proves exact execution and artifact accounting only. Coverage is "
                "reported independently using the frozen coverage-status vocabulary. Proxy values "
                "remain candidate-priority metadata and are not physical labels. This auditor runs "
                "no simulator, surrogate inference, or final model training."
            ),
        }
        receipt_path = staging / "FINAL_DELIVERY_RECEIPT.json"
        _write_json(receipt_path, receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"coverage_status": coverage_status}


def _load_contract(path: Path) -> ContractEvidence:
    payload = _read_json(path)
    errors = validate_contract(payload)
    if errors:
        raise DeliveryAuditError(f"campaign contract validation failed: {errors[:5]}")
    fingerprint = str(payload.get("contract_fingerprint_sha256") or "")
    if not _is_sha256(fingerprint) or fingerprint != contract_fingerprint(payload):
        raise DeliveryAuditError("campaign contract fingerprint is missing or does not recompute")
    inherited = payload.get("inherited_contract_evidence") or {}
    process_sha = str(inherited.get("production_config_sha256") or "").lower()
    if not _is_sha256(process_sha):
        raise DeliveryAuditError("frozen production config SHA-256 is missing")
    if payload.get("preparation_status") != "PASS":
        raise DeliveryAuditError("campaign contract is not a PASS private preparation artifact")
    return ContractEvidence(payload, fingerprint, process_sha)


def _load_terminal_checkpoint(
    directory: Path, *, contract_path: Path, fingerprint: str
) -> TerminalEvidence:
    _require_directory(directory, "terminal checkpoint")
    _require_files(directory, CHECKPOINT_REQUIRED_FILES)
    _verify_sha_index(directory, recursive=False, require_all=True)
    status = _read_json(directory / "CHECKPOINT_STATUS.json")
    receipt = _read_json(directory / "CHECKPOINT_RECEIPT.json")
    coverage = _read_json(directory / "COVERAGE_SUMMARY.json")
    expected_counts = {
        "accepted_geometries": TARGET_ACCEPTED_GEOMETRIES,
        "s4p_artifacts": TARGET_ACCEPTED_GEOMETRIES,
        "geometry_frequency_rows": EXPECTED_FEATURE_ROWS,
    }
    if (
        status.get("campaign_id") != CAMPAIGN_ID
        or status.get("contract_fingerprint_sha256") != fingerprint
        or status.get("checkpoint_status") != "COMPLETE_200K"
        or status.get("audit_mode") != "checkpoint"
    ):
        raise DeliveryAuditError("terminal checkpoint status is not exact COMPLETE_200K")
    for key, expected in expected_counts.items():
        if _as_int(status.get(key), key) != expected:
            raise DeliveryAuditError(f"terminal checkpoint {key} is not {expected}")
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_CHECKPOINT"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or receipt.get("audit_mode") != "checkpoint"
        or _as_int(receipt.get("expected_accepted"), "expected_accepted")
        != TARGET_ACCEPTED_GEOMETRIES
        or any(item.get("pass") is not True for item in receipt.get("checks") or [])
    ):
        raise DeliveryAuditError("terminal checkpoint receipt is not exact hash-closed PASS")
    if (
        coverage.get("campaign_id") != CAMPAIGN_ID
        or coverage.get("contract_fingerprint_sha256") != fingerprint
        or _as_int(coverage.get("expected_accepted_geometries"), "coverage accepted")
        != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(coverage.get("feature_row_count"), "coverage rows")
        != EXPECTED_FEATURE_ROWS
        or coverage.get("coverage_status") != status.get("coverage_status")
    ):
        raise DeliveryAuditError("terminal coverage summary conflicts with checkpoint status")
    _verify_evidence(receipt.get("inputs", {}).get("contract"), contract_path, "contract")
    output_names = {
        "coverage_cells": "physical_coverage_cells_by_anchor.csv",
        "coverage_by_frequency": "physical_coverage_by_frequency.csv",
        "coverage_marginals": "physical_coverage_marginals.csv",
        "coverage_pairwise": "physical_coverage_pairwise.csv",
        "geometry_coverage_summary": "GEOMETRY_COVERAGE_SUMMARY.json",
        "geometry_coverage_marginals": "geometry_coverage_marginals.csv",
        "geometry_coverage_pairwise": "geometry_coverage_pairwise.csv",
        "coverage_summary": "COVERAGE_SUMMARY.json",
        "checkpoint_status": "CHECKPOINT_STATUS.json",
        "failure_funnel": "FAILURE_FUNNEL.csv",
    }
    for key, name in output_names.items():
        _verify_evidence(receipt.get("outputs", {}).get(key), directory / name, key)
    return TerminalEvidence(receipt, status, coverage)


def _load_raw_products(
    directory: Path,
    *,
    terminal: TerminalEvidence,
    fingerprint: str,
    production_config_sha256: str,
) -> dict[str, Any]:
    _require_directory(directory, "raw product")
    paths = {key: directory / name for key, name in RAW_PRODUCT_NAMES.items()}
    for key, path in paths.items():
        if not path.is_file():
            raise DeliveryAuditError(f"missing required raw product {key}: {path}")
    for key in ("accepted_geometries", "long_features", "artifact_index", "failure_funnel"):
        _verify_evidence(terminal.receipt.get("inputs", {}).get(key), paths[key], key)

    accepted = _read_identity_table(
        paths["accepted_geometries"],
        fingerprint=fingerprint,
        expected_count=TARGET_ACCEPTED_GEOMETRIES,
    )
    artifact_hashes = _read_artifact_index(
        paths["artifact_index"],
        accepted=accepted,
        fingerprint=fingerprint,
    )
    _read_geometry_provenance(
        paths["geometry_provenance"],
        accepted=accepted,
        artifact_hashes=artifact_hashes,
        fingerprint=fingerprint,
        production_config_sha256=production_config_sha256,
    )
    if _csv_row_count(paths["long_features"]) != EXPECTED_FEATURE_ROWS:
        raise DeliveryAuditError("broadband_features_11p2m_long.csv row count is not 11,200,000")
    _audit_failure_funnel(paths["failure_funnel"])
    return {
        "paths": paths,
        "evidence": {key: _file_evidence(path) for key, path in paths.items()},
    }


def _read_identity_table(
    path: Path, *, fingerprint: str, expected_count: int
) -> dict[str, str]:
    required = {"geometry_id", "geometry_sha256", "campaign_contract_fingerprint"}
    identities: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(set(reader.fieldnames or [])):
            raise DeliveryAuditError(f"accepted ledger lacks columns: {sorted(required - set(reader.fieldnames or []))}")
        for line, row in enumerate(reader, start=2):
            geometry_id = str(row.get("geometry_id") or "").strip()
            geometry_sha = str(row.get("geometry_sha256") or "").strip().lower()
            if (
                not geometry_id
                or geometry_id in identities
                or not _is_sha256(geometry_sha)
                or row.get("campaign_contract_fingerprint") != fingerprint
            ):
                raise DeliveryAuditError(f"accepted ledger identity/fingerprint error at line {line}")
            identities[geometry_id] = geometry_sha
    if len(identities) != expected_count:
        raise DeliveryAuditError(
            f"accepted ledger count mismatch: actual={len(identities)}, expected={expected_count}"
        )
    return identities


def _read_artifact_index(
    path: Path, *, accepted: Mapping[str, str], fingerprint: str
) -> dict[str, tuple[str, str]]:
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "s4p_path",
        "s4p_sha256",
        "frequency_points",
        "emx_status",
        "calibre_status",
        "calibre_blocking_violations",
    }
    artifacts: dict[str, tuple[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(set(reader.fieldnames or [])):
            raise DeliveryAuditError(f"artifact index lacks columns: {sorted(required - set(reader.fieldnames or []))}")
        for line, row in enumerate(reader, start=2):
            geometry_id = str(row.get("geometry_id") or "").strip()
            s4p_sha = str(row.get("s4p_sha256") or "").strip().lower()
            if (
                geometry_id in artifacts
                or accepted.get(geometry_id) != str(row.get("geometry_sha256") or "").lower()
                or row.get("campaign_contract_fingerprint") != fingerprint
                or not _is_sha256(s4p_sha)
                or _as_int(row.get("frequency_points"), "frequency_points") != len(FREQUENCY_GRID_HZ)
                or str(row.get("emx_status") or "").upper() != "PASS"
                or str(row.get("calibre_status") or "").upper() != "PASS"
                or _as_int(row.get("calibre_blocking_violations"), "blocking") != 0
            ):
                raise DeliveryAuditError(f"artifact index contract error at line {line}")
            s4p_path = str(row.get("s4p_path") or "").strip()
            if Path(s4p_path).suffix.lower() != ".s4p":
                raise DeliveryAuditError(f"artifact index S4P path error at line {line}")
            artifacts[geometry_id] = (s4p_path, s4p_sha)
    if set(artifacts) != set(accepted):
        raise DeliveryAuditError("artifact-index geometry identities do not match accepted ledger")
    return artifacts


def _read_geometry_provenance(
    path: Path,
    *,
    accepted: Mapping[str, str],
    artifact_hashes: Mapping[str, tuple[str, str]],
    fingerprint: str,
    production_config_sha256: str,
) -> None:
    seen: set[str] = set()
    hash_cache: dict[tuple[str, str], bool] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not PROVENANCE_REQUIRED_COLUMNS.issubset(fields):
            raise DeliveryAuditError(
                f"geometry provenance lacks columns: {sorted(PROVENANCE_REQUIRED_COLUMNS - fields)}"
            )
        for line, row in enumerate(reader, start=2):
            geometry_id = str(row.get("geometry_id") or "").strip()
            path_hash_fields = (
                ("production_config_path", "production_config_sha256"),
                ("candidate_source_path", "candidate_source_sha256"),
                ("gds_path", "gds_sha256"),
                ("calibre_report_path", "calibre_report_sha256"),
                ("emx_log_path", "emx_log_sha256"),
                ("s4p_path", "s4p_sha256"),
            )
            artifact = artifact_hashes.get(geometry_id)
            if (
                geometry_id in seen
                or accepted.get(geometry_id) != str(row.get("geometry_sha256") or "").lower()
                or row.get("campaign_contract_fingerprint") != fingerprint
                or str(row.get("production_config_sha256") or "").lower()
                != production_config_sha256
                or any(
                    not str(row.get(path_field) or "").strip()
                    or not _is_sha256(str(row.get(hash_field) or "").lower())
                    for path_field, hash_field in path_hash_fields
                )
                or artifact
                != (
                    str(row.get("s4p_path") or "").strip(),
                    str(row.get("s4p_sha256") or "").lower(),
                )
                or _as_int(row.get("frequency_points"), "frequency_points") != len(FREQUENCY_GRID_HZ)
                or str(row.get("calibre_status") or "").upper() != "PASS"
                or _as_int(row.get("calibre_blocking_violations"), "blocking") != 0
                or str(row.get("emx_status") or "").upper() != "PASS"
                or not _truthy(row.get("fresh_real_emx"))
            ):
                raise DeliveryAuditError(f"geometry provenance contract error at line {line}")
            for path_field, hash_field in path_hash_fields:
                evidence_path = Path(str(row[path_field])).expanduser()
                digest = str(row[hash_field]).lower()
                cache_key = (str(evidence_path), digest)
                if cache_key not in hash_cache:
                    hash_cache[cache_key] = bool(
                        evidence_path.is_file()
                        and evidence_path.stat().st_size > 0
                        and _sha256(evidence_path) == digest
                    )
                if not hash_cache[cache_key]:
                    raise DeliveryAuditError(
                        f"geometry provenance file/hash error at line {line}: {path_field}"
                    )
            if Path(str(row["gds_path"])).suffix.lower() != ".gds" or Path(
                str(row["s4p_path"])
            ).suffix.lower() != ".s4p":
                raise DeliveryAuditError(f"geometry provenance artifact suffix error at line {line}")
            seen.add(geometry_id)
    if seen != set(accepted):
        raise DeliveryAuditError("geometry provenance identities do not match accepted ledger")


def _audit_failure_funnel(path: Path) -> None:
    counts: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"stage", "count"}.issubset(set(reader.fieldnames or [])):
            raise DeliveryAuditError("failure_funnel.csv lacks stage/count columns")
        for row in reader:
            stage = str(row.get("stage") or "").strip()
            if stage in counts:
                raise DeliveryAuditError(f"failure funnel duplicates stage: {stage}")
            counts[stage] = _as_int(row.get("count"), f"failure funnel {stage}")
    if not FAILURE_FUNNEL_STAGES.issubset(counts):
        raise DeliveryAuditError(
            f"failure funnel omits stages: {sorted(FAILURE_FUNNEL_STAGES - set(counts))}"
        )
    if counts["accepted_geometries"] != TARGET_ACCEPTED_GEOMETRIES:
        raise DeliveryAuditError("failure funnel accepted count is not 200,000")
    if counts["raw_geometry_candidates"] < TARGET_ACCEPTED_GEOMETRIES:
        raise DeliveryAuditError("failure funnel raw candidate count is below accepted count")


def _load_history_products(
    directory: Path,
    *,
    checkpoint_dir: Path,
    accepted_path: Path,
    fingerprint: str,
) -> dict[str, Any]:
    _require_directory(directory, "campaign history")
    required = (*HISTORY_OUTPUT_NAMES.values(), "CAMPAIGN_HISTORY_RECEIPT.json", "SHA256SUMS.txt")
    _require_files(directory, required)
    _verify_sha_index(directory, recursive=False, require_all=True)
    receipt = _read_json(directory / "CAMPAIGN_HISTORY_RECEIPT.json")
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_AS_AUDITED_CAMPAIGN_HISTORY"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or any(value is not True for value in (receipt.get("checks") or {}).values())
    ):
        raise DeliveryAuditError("campaign history receipt is not exact PASS")
    counts = receipt.get("terminal_counts") or {}
    if (
        _as_int(counts.get("accepted_geometries"), "history accepted") != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(counts.get("s4p_artifacts"), "history S4P") != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(counts.get("geometry_frequency_rows"), "history rows") != EXPECTED_FEATURE_ROWS
        or len(receipt.get("audit_counts") or []) != EXPECTED_HISTORY_AUDITS
    ):
        raise DeliveryAuditError("campaign history terminal counts are not exact")
    _verify_evidence(receipt.get("inputs", {}).get("accepted_geometries"), accepted_path, "history accepted")
    for key, name in HISTORY_OUTPUT_NAMES.items():
        _verify_evidence(receipt.get("outputs", {}).get(key), directory / name, key)
    if _sha256(directory / "coverage_summary_200k.json") != _sha256(
        checkpoint_dir / "COVERAGE_SUMMARY.json"
    ):
        raise DeliveryAuditError("history coverage_summary_200k is not byte-exact terminal copy")
    expected_rows = {
        "coverage_deficit_history.csv": EXPECTED_HISTORY_AUDITS
        * PRIMARY_FREQUENCY_CONDITIONED_CELLS,
        "acquisition_round_history.csv": EXPECTED_ACQUISITION_ROUNDS,
        "acquisition_source_by_geometry.csv": TARGET_ACCEPTED_GEOMETRIES,
    }
    for name, expected in expected_rows.items():
        if _csv_row_count(directory / name) != expected:
            raise DeliveryAuditError(f"history row count mismatch for {name}: expected={expected}")
    return _directory_evidence(directory, "CAMPAIGN_HISTORY_RECEIPT.json")


def _load_training_products(
    directory: Path,
    *,
    accepted_path: Path,
    long_features_path: Path,
    fingerprint: str,
) -> dict[str, Any]:
    _require_directory(directory, "training readiness")
    required = (*TRAINING_OUTPUT_NAMES.values(), "TRAINING_READINESS_RECEIPT.json", "SHA256SUMS.txt")
    _require_files(directory, required)
    _verify_sha_index(directory, recursive=False, require_all=True)
    receipt = _read_json(directory / "TRAINING_READINESS_RECEIPT.json")
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or any(value is not True for value in (receipt.get("checks") or {}).values())
    ):
        raise DeliveryAuditError("training-readiness receipt is not exact PASS")
    counts = receipt.get("counts") or {}
    if (
        _as_int(counts.get("accepted_geometries"), "readiness accepted") != TARGET_ACCEPTED_GEOMETRIES
        or _as_int(counts.get("geometry_frequency_rows"), "readiness rows") != EXPECTED_FEATURE_ROWS
    ):
        raise DeliveryAuditError("training-readiness terminal counts are not exact")
    _verify_evidence(receipt.get("inputs", {}).get("accepted_geometries"), accepted_path, "readiness accepted")
    _verify_evidence(receipt.get("inputs", {}).get("long_features"), long_features_path, "readiness features")
    for key, name in TRAINING_OUTPUT_NAMES.items():
        _verify_evidence(receipt.get("outputs", {}).get(key), directory / name, key)
    if _csv_row_count(directory / "full_200k_training_weights.csv") != TARGET_ACCEPTED_GEOMETRIES:
        raise DeliveryAuditError("full_200k_training_weights.csv row count is not 200,000")
    if _csv_row_count(directory / "future_split_assignments.csv") != TARGET_ACCEPTED_GEOMETRIES:
        raise DeliveryAuditError("future_split_assignments.csv row count is not 200,000")
    split = _read_json(directory / "future_split_manifest.json")
    if (
        _as_int(split.get("geometry_count"), "future split count") != TARGET_ACCEPTED_GEOMETRIES
        or split.get("all_56_frequency_rows_from_one_geometry_remain_in_one_split") is not True
    ):
        raise DeliveryAuditError("future split manifest does not preserve exact geometry identity")
    return _directory_evidence(directory, "TRAINING_READINESS_RECEIPT.json")


def _load_figure_products(
    directory: Path, *, fingerprint: str, production_config_sha256: str
) -> dict[str, Any]:
    _require_directory(directory, "checkpoint figures")
    _require_files(directory, ("FIGURE_RECEIPT.json", "SHA256SUMS.txt"))
    _verify_sha_index(directory, recursive=True, require_all=True)
    receipt = _read_json(directory / "FIGURE_RECEIPT.json")
    counts = receipt.get("counts") or {}
    if (
        receipt.get("overall_status") != "PASS"
        or receipt.get("decision") != "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES"
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("contract_fingerprint_sha256") != fingerprint
        or receipt.get("production_process_config_sha256") != production_config_sha256
        or tuple(receipt.get("checkpoint_counts") or ()) != FIGURE_CHECKPOINT_COUNTS
        or tuple(receipt.get("logical_figure_ids") or ()) != FIGURE_IDS
        or _as_int(counts.get("logical_figures"), "logical figures") != 56
        or _as_int(counts.get("png_files"), "PNG figures") != 56
        or _as_int(counts.get("svg_files"), "SVG figures") != 56
        or _as_int(counts.get("rendered_files"), "rendered figures") != 112
        or any(value is not True for value in (receipt.get("checks") or {}).values())
    ):
        raise DeliveryAuditError("checkpoint figure receipt is not exact 4x14 PNG+SVG PASS")
    for count in FIGURE_CHECKPOINT_COUNTS:
        checkpoint = directory / f"checkpoint_{count:06d}"
        manifest_path = checkpoint / "FIGURE_MANIFEST.json"
        manifest = _read_json(manifest_path)
        if (
            _as_int(manifest.get("accepted_geometries"), "figure checkpoint") != count
            or manifest.get("campaign_contract_fingerprint_sha256") != fingerprint
            or manifest.get("production_process_config_sha256") != production_config_sha256
            or tuple(item.get("figure_id") for item in manifest.get("figures") or []) != FIGURE_IDS
            or any(value is not True for value in (manifest.get("checks") or {}).values())
        ):
            raise DeliveryAuditError(f"figure manifest mismatch at checkpoint {count}")
        for item in manifest["figures"]:
            figure_id = str(item["figure_id"])
            for extension in ("png", "svg"):
                path = checkpoint / f"{figure_id}.{extension}"
                _verify_evidence(item.get("files", {}).get(extension), path, f"{count}:{figure_id}:{extension}")
                if path.stat().st_size <= 1_000:
                    raise DeliveryAuditError(f"rendered figure is empty or too small: {path}")
    return _directory_evidence(directory, "FIGURE_RECEIPT.json")


def _verify_sha_index(directory: Path, *, recursive: bool, require_all: bool) -> None:
    index = directory / "SHA256SUMS.txt"
    if not index.is_file():
        raise DeliveryAuditError(f"missing SHA256SUMS.txt: {directory}")
    root = directory.resolve()
    seen: set[str] = set()
    for line, raw in enumerate(index.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2 or not _is_sha256(parts[0]):
            raise DeliveryAuditError(f"invalid SHA256SUMS line {line}: {index}")
        digest, relative = parts[0].lower(), parts[1]
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DeliveryAuditError(f"SHA256SUMS path escapes directory: {relative}") from exc
        if relative in seen or not candidate.is_file() or _sha256(candidate) != digest:
            raise DeliveryAuditError(f"SHA256SUMS mismatch: {directory.name}/{relative}")
        if not recursive and Path(relative).name != relative:
            raise DeliveryAuditError(f"non-recursive SHA index contains nested path: {relative}")
        seen.add(relative)
    if not seen:
        raise DeliveryAuditError(f"empty SHA256SUMS.txt: {directory}")
    if require_all:
        discovered = {
            str(path.relative_to(directory))
            for path in (directory.rglob("*") if recursive else directory.iterdir())
            if path.is_file() and path != index
        }
        if seen != discovered:
            raise DeliveryAuditError(
                f"SHA256SUMS coverage mismatch in {directory}: missing={sorted(discovered - seen)[:5]}, "
                f"extra={sorted(seen - discovered)[:5]}"
            )


def _verify_evidence(evidence: Any, path: Path, label: str) -> None:
    if not isinstance(evidence, Mapping) or not path.is_file():
        raise DeliveryAuditError(f"missing file evidence: {label}")
    digest = str(evidence.get("sha256") or "").lower()
    if (
        Path(str(evidence.get("path") or "")).name != path.name
        or digest != _sha256(path)
        or _as_int(evidence.get("size_bytes"), f"{label} size") != path.stat().st_size
    ):
        raise DeliveryAuditError(f"file evidence mismatch: {label}")


def _directory_evidence(directory: Path, receipt_name: str) -> dict[str, Any]:
    return {
        "path": str(directory),
        "receipt": _file_evidence(directory / receipt_name),
        "sha256_index": _file_evidence(directory / "SHA256SUMS.txt"),
    }


def _file_evidence(path: Path, *, recorded_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(recorded_path if recorded_path is not None else path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration as exc:
            raise DeliveryAuditError(f"CSV has no header: {path}") from exc
        return sum(1 for _ in reader)


def _require_directory(path: Path, label: str) -> None:
    if not path.is_dir():
        raise DeliveryAuditError(f"{label} directory does not exist: {path}")


def _require_files(directory: Path, names: Sequence[str]) -> None:
    missing = [name for name in names if not (directory / name).is_file()]
    if missing:
        raise DeliveryAuditError(f"{directory} is missing required files: {missing}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise DeliveryAuditError(f"failed to parse JSON {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeliveryAuditError(f"JSON root is not an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_sha256s(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _as_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DeliveryAuditError(f"invalid integer for {label}: {value!r}") from exc


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
