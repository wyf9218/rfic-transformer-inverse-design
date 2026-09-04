#!/usr/bin/env python3
"""Assemble one broadband56 attempt into an exact terminal partition.

This post-processing role joins the hash-bound Cadence, GDS identity,
Calibre, fresh-EMX, and exact56 QA receipts.  It never launches a simulator.
Every raw candidate must appear in exactly one terminal state before the
stage-attempt finalizer can count any accepted geometry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ACQUISITION_SOURCES_BY_PHASE,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (  # noqa: E402
    ATTEMPT_FAILURE_ACCOUNTING_FIELDS,
)
from rfic_transformer_inverse_design.campaigns.broadband56_golden_source import (  # noqa: E402
    GoldenSourceError, SAFE_ANCHOR_SOURCE, validate_safe_anchor_source,
    validate_safe_anchor_qa_receipt,
)


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_stage_attempt_products.v1"
RECEIPT_NAME = "STAGE_ATTEMPT_PRODUCTS_ROLE_RECEIPT.json"
VALIDATION_RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_golden_validation_attempt_products.v1"
VALIDATION_DECISION = "USE_GOLDEN_VALIDATION_ONLY_TERMINAL_PRODUCTS"
ATTEMPT_LEDGER_NAME = "ATTEMPT_LEDGER.csv"
ACCEPTED_INCREMENT_NAME = "ACCEPTED_GEOMETRY_INCREMENT.csv"
REJECTED_INCREMENT_NAME = "REJECTED_GEOMETRY_INCREMENT.csv"
EXACT_RECEIPT_INDEX_NAME = "EXACT_GDS_EMX_RECEIPT_INDEX.csv"
S4P_ARTIFACT_INDEX_NAME = "S4P_ARTIFACT_INDEX.csv"
LONG_FEATURES_NAME = "BROADBAND_FEATURES_LONG.csv"
FAILURE_FUNNEL_NAME = "FAILURE_FUNNEL.csv"
SHA256SUMS_NAME = "SHA256SUMS.txt"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

EXPECTED_DECISIONS = {
    "cadence": "TERMINAL_PARTITION_CANDIDATE_BOUND_CADENCE_STREAMOUT",
    "gds": "TERMINAL_PARTITION_GDS_PHYSICAL_IDENTITY_AUDIT",
    "calibre": "TERMINAL_PARTITION_FOUNDRY_CALIBRE_DRC",
    "calibre_zero": "TERMINAL_PARTITION_ZERO_BLOCKING_CALIBRE_RECEIPTS",
    "exact_emx": "TERMINAL_PARTITION_EXACT_GDS_FRESH_EMX_ATTEMPT",
    "exact56": "TERMINAL_PARTITION_EXACT56_S4P_QA",
}

STATUS_FIELDS = (
    "duplicate_status",
    "geometry_bounds_status",
    "analytical_status",
    "topology_status",
    "cadence_gds_status",
    "calibre_status",
    "emx_status",
    "s4p_status",
    "s_to_z_status",
    "feature_extraction_status",
)
STATUS_BY_TERMINAL = {
    "CADENCE_FAILURE": ("PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "CALIBRE_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "EMX_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN"),
    "INCOMPLETE_FREQUENCY_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN"),
    "S4P_PARSING_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN", "NOT_RUN"),
    "S_TO_Z_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL", "NOT_RUN"),
    "FEATURE_EXTRACTION_FAILURE": ("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "FAIL"),
    "ACCEPTED": ("PASS",) * 10,
    "GOLDEN_VALIDATION_PASS": ("HISTORICAL_NOT_PRODUCTION",) + ("PASS",) * 9,
}
FUNNEL_BY_TERMINAL = {
    "CADENCE_FAILURE": "cadence_failures",
    "CALIBRE_FAILURE": "calibre_blocking_failures",
    "EMX_FAILURE": "emx_failures",
    "INCOMPLETE_FREQUENCY_FAILURE": "incomplete_frequency_failures",
    "S4P_PARSING_FAILURE": "s4p_parsing_failures",
    "S_TO_Z_FAILURE": "s_to_z_failures",
    "FEATURE_EXTRACTION_FAILURE": "feature_extraction_failures",
    "ACCEPTED": "accepted_geometries",
    "GOLDEN_VALIDATION_PASS": "golden_validation_geometries",
}
PATH_HASH_FIELDS = (
    ("candidate_source_path", "candidate_source_sha256"),
    ("gds_path", "gds_sha256"),
    ("calibre_report_path", "calibre_report_sha256"),
    ("emx_log_path", "emx_log_sha256"),
    ("s4p_path", "s4p_sha256"),
)
LEDGER_FIELDS = (
    "attempt_id",
    "retry_of_attempt_id",
    "geometry_id",
    "geometry_sha256",
    "campaign_contract_fingerprint",
    "accepted_sequence",
    "campaign_phase",
    "acquisition_source",
    "terminal_stage",
    "terminal_error",
    "calibre_blocking_violations",
    "frequency_points",
    "fresh_real_emx",
    *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    *STATUS_FIELDS,
    *(name for pair in PATH_HASH_FIELDS for name in pair),
)


class AttemptProductError(RuntimeError):
    """Raised when the attempt cannot be closed without ambiguity."""


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
        receipt = build_attempt_products(args, out_dir=out_dir)
    except (AttemptProductError, GoldenSourceError, OSError, json.JSONDecodeError) as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"raw_candidates={receipt['raw_candidate_count']}")
    print(f"accepted={receipt['accepted_count']}")
    print(f"rejected={receipt['rejected_count']}")
    print(f"receipt={out_dir / RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--current-accepted", type=int, required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--cadence-role-receipt", required=True)
    parser.add_argument("--gds-identity-role-receipt", required=True)
    parser.add_argument("--calibre-role-receipt", required=True)
    parser.add_argument("--calibre-zero-role-receipt", required=True)
    parser.add_argument("--exact-gds-emx-role-receipt", required=True)
    parser.add_argument("--exact56-role-receipt", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--golden-source-receipt")
    return parser.parse_args(argv)


def build_attempt_products(
    args: argparse.Namespace,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    stage = str(args.stage).strip().upper()
    spec = STAGE_BY_NAME.get(stage)
    if spec is None:
        raise AttemptProductError(f"unknown campaign stage: {stage}")
    current_accepted = int(args.current_accepted)
    if current_accepted < 0 or current_accepted >= spec.cumulative_target:
        raise AttemptProductError("current_accepted is outside the active stage")

    manifest_path = _regular_file(
        Path(args.backend_identity_manifest), "backend identity manifest"
    )
    manifest = _read_json(manifest_path, "backend identity manifest")
    manifest_sha = _sha256(manifest_path)
    if not (
        manifest.get("campaign_id") == CAMPAIGN_ID
        and manifest.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise AttemptProductError("backend manifest campaign or contract mismatch")
    scripts = _mapping(manifest.get("script_identities"), "script identities")
    self_path = _identity_path(
        scripts.get("stage_attempt_product_builder"),
        "stage-attempt product builder identity",
    )
    if self_path != Path(__file__).resolve():
        raise AttemptProductError("stage-attempt product builder self-identity mismatch")

    authorization_path = _regular_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    authorization_sha = _sha256(authorization_path)
    _validate_authorization(authorization, manifest_sha=manifest_sha)

    role_paths = {
        "cadence": _regular_file(Path(args.cadence_role_receipt), "Cadence receipt"),
        "gds": _regular_file(Path(args.gds_identity_role_receipt), "GDS identity receipt"),
        "calibre": _regular_file(Path(args.calibre_role_receipt), "Calibre receipt"),
        "calibre_zero": _regular_file(Path(args.calibre_zero_role_receipt), "Calibre zero-blocking receipt"),
        "exact_emx": _regular_file(Path(args.exact_gds_emx_role_receipt), "exact GDS fresh-EMX receipt"),
        "exact56": _regular_file(Path(args.exact56_role_receipt), "exact56 QA receipt"),
    }
    roles = {
        name: _validate_role_receipt(
            path,
            label=name,
            stage=stage,
            manifest_sha=manifest_sha,
            authorization_sha=authorization_sha,
        )
        for name, path in role_paths.items()
    }
    _assert_record_matches(
        roles["calibre"].get("input_role_receipt"),
        role_paths["gds"],
        "Calibre input receipt",
    )
    _assert_record_matches(
        roles["calibre_zero"].get("input_role_receipt"),
        role_paths["calibre"],
        "zero-blocking input receipt",
    )
    _assert_record_matches(
        roles["exact_emx"].get("input_role_receipt"),
        role_paths["calibre_zero"],
        "fresh-EMX input receipt",
    )
    _assert_record_matches(
        roles["exact56"].get("input_role_receipt"),
        role_paths["exact_emx"],
        "exact56 input receipt",
    )

    candidate_path = _identity_path(
        roles["cadence"].get("input_candidate_queue"), "raw candidate queue"
    )
    raw_rows, _ = _read_csv(candidate_path)
    golden_source = None
    if getattr(args, "golden_source_receipt", None):
        if stage != "GOLDEN" or current_accepted != 0:
            raise AttemptProductError("validation-only anchor requires GOLDEN at accepted=0")
        source_path = _regular_file(Path(args.golden_source_receipt), "Golden source receipt")
        golden_source = _file_record(source_path)
        summary = _read_json(source_path, "Golden source receipt")
        _assert_record_matches(summary.get("candidate_queue"), candidate_path, "Golden raw queue")
        if roles["exact56"].get("golden_source_receipt") != golden_source:
            raise AttemptProductError("Golden source receipt is not bound to exact56 QA role")
    raw = _candidate_map(raw_rows, golden_source_receipt=golden_source)
    if not raw:
        raise AttemptProductError("raw candidate queue is empty")
    candidate_source_sha = _sha256(candidate_path)

    cadence_pass = _index_map(
        _identity_path(roles["cadence"].get("pass_candidate_queue"), "Cadence pass queue"),
        "Cadence pass queue",
    )
    cadence_fail = _index_map(
        _identity_path(roles["cadence"].get("failure_index"), "Cadence failure index"),
        "Cadence failure index",
    )
    _partition(raw, cadence_pass, cadence_fail, "Cadence")
    _validate_counts(roles["cadence"], len(raw), len(cadence_pass), len(cadence_fail), "cadence")

    gds_pass = _index_map(
        _identity_path(roles["gds"].get("pass_index"), "GDS identity pass index"),
        "GDS identity pass index",
    )
    gds_fail = _index_map(
        _identity_path(roles["gds"].get("failure_index"), "GDS identity failure index"),
        "GDS identity failure index",
    )
    _partition(cadence_pass, gds_pass, gds_fail, "GDS identity")
    _validate_counts(roles["gds"], len(cadence_pass), len(gds_pass), len(gds_fail), "identity")
    if gds_fail:
        raise AttemptProductError(
            "GDS physical-identity failures are provenance failures and cannot be relabeled as candidate failures"
        )

    calibre_pass = _index_map(
        _identity_path(roles["calibre"].get("pass_index"), "Calibre pass index"),
        "Calibre pass index",
    )
    calibre_fail = _index_map(
        _identity_path(roles["calibre"].get("failure_index"), "Calibre failure index"),
        "Calibre failure index",
    )
    _partition(gds_pass, calibre_pass, calibre_fail, "Calibre")
    _validate_counts(roles["calibre"], len(gds_pass), len(calibre_pass), len(calibre_fail), "calibre")

    zero_pass = _index_map(
        _identity_path(roles["calibre_zero"].get("pass_index"), "zero-blocking pass index"),
        "zero-blocking pass index",
    )
    zero_fail = _index_map(
        _identity_path(roles["calibre_zero"].get("failure_index"), "zero-blocking failure index"),
        "zero-blocking failure index",
    )
    _partition(calibre_pass, zero_pass, zero_fail, "zero-blocking receipt")
    _validate_counts(roles["calibre_zero"], len(calibre_pass), len(zero_pass), len(zero_fail), "receipt")
    if zero_fail:
        raise AttemptProductError(
            "zero-blocking receipt failures are evidence-integrity failures and require a retry"
        )

    exact_pass = _index_map(
        _identity_path(roles["exact_emx"].get("pass_index"), "fresh-EMX pass index"),
        "fresh-EMX pass index",
    )
    exact_fail = _index_map(
        _identity_path(roles["exact_emx"].get("failure_index"), "fresh-EMX failure index"),
        "fresh-EMX failure index",
    )
    exact_evidence = _index_map(
        _identity_path(roles["exact_emx"].get("delegate_evidence_index"), "fresh-EMX evidence index"),
        "fresh-EMX evidence index",
    )
    _partition(zero_pass, exact_pass, exact_fail, "fresh EMX")
    _validate_counts(roles["exact_emx"], len(zero_pass), len(exact_pass), len(exact_fail), "emx")
    if set(exact_evidence) != set(zero_pass):
        raise AttemptProductError("fresh-EMX evidence index does not cover every submitted candidate")

    qa_pass_path = _identity_path(
        roles["exact56"].get("qa_pass_index"), "exact56 QA pass index"
    )
    qa_pass = _index_map(qa_pass_path, "exact56 QA pass index")
    qa_fail = _index_map(
        _identity_path(roles["exact56"].get("failure_index"), "exact56 QA failure index"),
        "exact56 QA failure index",
    )
    _partition(exact_pass, qa_pass, qa_fail, "exact56 QA")
    _validate_counts(roles["exact56"], len(exact_pass), len(qa_pass), len(qa_fail), "qa")

    terminal_by_candidate: dict[str, tuple[str, Mapping[str, str]]] = {}
    _add_terminal_rows(terminal_by_candidate, cadence_fail, "CADENCE_FAILURE")
    _add_terminal_rows(terminal_by_candidate, calibre_fail, "CALIBRE_FAILURE")
    for candidate, row in exact_fail.items():
        terminal = _exact_failure_terminal(row)
        _add_terminal(terminal_by_candidate, candidate, terminal, row)
    for candidate, row in qa_fail.items():
        terminal = _qa_failure_terminal(row)
        _add_terminal(terminal_by_candidate, candidate, terminal, row)
    golden_validation = None
    if golden_source is not None and qa_pass:
        if len(qa_pass) != 1 or len(raw) != 1:
            raise AttemptProductError("Golden validation must cover exactly one candidate")
        evidence = _index_map(
            _identity_path(roles["exact56"].get("evidence_index"), "Golden QA evidence"),
            "Golden QA evidence",
        )
        candidate = next(iter(qa_pass))
        qa_row = evidence.get(candidate, {})
        qa_receipt_path = _pinned_file(Path(str(qa_row.get("qa_receipt_path", ""))), qa_row.get("qa_receipt_sha256"), "Golden QA receipt")
        exact_row = exact_pass[candidate]
        exact_receipt_path = _pinned_file(Path(exact_row["exact_gds_emx_receipt_path"]), exact_row["exact_gds_emx_receipt_sha256"], "Golden EMX receipt")
        golden_validation = validate_safe_anchor_qa_receipt(
            golden_source, _file_record(qa_receipt_path),
            exact_emx_receipt_record=_file_record(exact_receipt_path),
        )
        if roles["exact56"].get("golden_validation") != golden_validation:
            raise AttemptProductError("Golden QA role/per-candidate eligibility mismatch")
    _add_terminal_rows(terminal_by_candidate, qa_pass, "GOLDEN_VALIDATION_PASS" if golden_source is not None else "ACCEPTED")
    if set(terminal_by_candidate) != set(raw):
        missing = sorted(set(raw) - set(terminal_by_candidate))[:5]
        extra = sorted(set(terminal_by_candidate) - set(raw))[:5]
        raise AttemptProductError(
            f"terminal candidate partition mismatch: missing={missing}, extra={extra}"
        )

    accepted_candidates = [
        candidate
        for candidate in raw
        if terminal_by_candidate[candidate][0] == "ACCEPTED"
    ]
    if current_accepted + len(accepted_candidates) > spec.cumulative_target:
        raise AttemptProductError("attempt would overshoot the active stage target")
    accepted_sequence = {
        candidate: current_accepted + index
        for index, candidate in enumerate(accepted_candidates, start=1)
    }

    ledgers: list[dict[str, Any]] = []
    for submitted_sequence, (candidate, candidate_row) in enumerate(raw.items(), start=1):
        terminal, terminal_row = terminal_by_candidate[candidate]
        ledgers.append(
            _ledger_row(
                candidate_row=candidate_row,
                candidate_source_path=candidate_path,
                candidate_source_sha=candidate_source_sha,
                submitted_sequence=submitted_sequence,
                stage=stage,
                terminal=terminal,
                terminal_row=terminal_row,
                accepted_sequence=accepted_sequence.get(candidate),
                gds_row=gds_pass.get(candidate),
                calibre_failure_row=calibre_fail.get(candidate),
                zero_row=zero_pass.get(candidate),
                exact_pass_row=exact_pass.get(candidate),
                exact_evidence_row=exact_evidence.get(candidate),
            )
        )

    funnel = Counter({field: 0 for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS})
    funnel["raw_geometry_candidates"] = len(ledgers)
    for row in ledgers:
        funnel[FUNNEL_BY_TERMINAL[row["terminal_stage"]]] += 1
    if sum(
        value
        for field, value in funnel.items()
        if field != "raw_geometry_candidates"
    ) != len(ledgers):
        raise AttemptProductError("failure funnel does not close to raw candidates")

    accepted_rows = [row for row in ledgers if row["terminal_stage"] == "ACCEPTED"]
    validation_rows = [row for row in ledgers if row["terminal_stage"] == "GOLDEN_VALIDATION_PASS"]
    rejected_rows = [row for row in ledgers if row["terminal_stage"] not in {"ACCEPTED", "GOLDEN_VALIDATION_PASS"}]
    exact_rows, exact_fields = _read_csv(qa_pass_path)
    artifact_rows, artifact_fields = _read_csv(
        _identity_path(roles["exact56"].get("s4p_artifact_index"), "S4P artifact index")
    )
    feature_rows, feature_fields = _read_csv(
        _identity_path(roles["exact56"].get("long_features"), "long-feature table")
    )
    if golden_validation is not None:
        individual_qa = _read_json(qa_receipt_path, "Golden individual QA")
        for key, actual in (
            ("source_fresh_emx_receipt_index", exact_rows),
            ("qa_index", artifact_rows), ("broadband_features_long", feature_rows),
        ):
            expected_rows, _ = _read_csv(_identity_path(individual_qa.get(key), f"Golden {key}"))
            if actual != expected_rows:
                raise AttemptProductError("Golden batch products differ from the verified per-candidate QA")
    product_sequence = {row["geometry_sha256"]: index for index, row in enumerate(validation_rows, start=1)} if golden_source is not None else accepted_sequence
    _renumber_accepted_products(
        product_sequence,
        exact_rows=exact_rows,
        artifact_rows=artifact_rows,
        feature_rows=feature_rows,
    )
    product_count = len(validation_rows) if golden_source is not None else len(accepted_rows)
    if len(exact_rows) != product_count or len(artifact_rows) != product_count:
        raise AttemptProductError("accepted product indexes do not match accepted ledger rows")
    if len(feature_rows) != product_count * len(FREQUENCY_GRID_HZ):
        raise AttemptProductError("long-feature count is not accepted count times 56")

    pins = {
        "backend_identity_manifest": (manifest_path, manifest_sha),
        "full_campaign_authorization_receipt": (authorization_path, authorization_sha),
        **{f"{name}_role_receipt": (path, _sha256(path)) for name, path in role_paths.items()},
        "raw_candidate_queue": (candidate_path, candidate_source_sha),
    }
    if golden_source is not None:
        pins["golden_source_receipt"] = (Path(golden_source["path"]), golden_source["sha256"])

    out_dir.mkdir(parents=True, mode=0o700)
    attempt_path = out_dir / ATTEMPT_LEDGER_NAME
    accepted_path = out_dir / ACCEPTED_INCREMENT_NAME
    rejected_path = out_dir / REJECTED_INCREMENT_NAME
    exact_path = out_dir / EXACT_RECEIPT_INDEX_NAME
    artifact_path = out_dir / S4P_ARTIFACT_INDEX_NAME
    feature_path = out_dir / LONG_FEATURES_NAME
    funnel_path = out_dir / FAILURE_FUNNEL_NAME
    validation_products = {}
    validation_files = []
    if golden_source is not None:
        for key, name, fields, rows in (
            ("validation_geometry", "GOLDEN_VALIDATION_GEOMETRY.csv", list(LEDGER_FIELDS), validation_rows),
            ("exact_gds_emx_receipt_index", "GOLDEN_VALIDATION_EXACT_GDS_EMX_RECEIPT_INDEX.csv", exact_fields, exact_rows),
            ("s4p_artifact_index", "GOLDEN_VALIDATION_S4P_ARTIFACT_INDEX.csv", artifact_fields, artifact_rows),
            ("long_features", "GOLDEN_VALIDATION_FEATURES_LONG.csv", feature_fields, feature_rows),
        ):
            path = out_dir / name
            _write_csv(path, fields, rows)
            validation_products[key] = _file_record(path)
            validation_files.append(path)
        exact_rows, artifact_rows, feature_rows = [], [], []
    _write_csv(attempt_path, list(LEDGER_FIELDS), ledgers)
    _write_csv(accepted_path, list(LEDGER_FIELDS), accepted_rows)
    _write_csv(rejected_path, list(LEDGER_FIELDS), rejected_rows)
    _write_csv(exact_path, exact_fields, exact_rows)
    _write_csv(artifact_path, artifact_fields, artifact_rows)
    _write_csv(feature_path, feature_fields, feature_rows)
    _write_csv(
        funnel_path,
        ["stage", "count"],
        [
            {"stage": field, "count": funnel[field]}
            for field in funnel
        ],
    )
    for label, (path, digest) in pins.items():
        _require_unchanged(path, digest, label)

    receipt = {
        "schema": VALIDATION_RECEIPT_SCHEMA if golden_source is not None else RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": VALIDATION_DECISION if golden_source is not None else "USE_EXACT_TERMINAL_ATTEMPT_PRODUCTS",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "current_accepted": current_accepted,
        "raw_candidate_count": len(ledgers),
        "accepted_count": len(accepted_rows),
        "rejected_count": len(rejected_rows),
        "geometry_frequency_rows": len(feature_rows),
        "terminal_partition_complete": True,
        "failed_or_duplicate_candidates_counted_as_accepted": False,
        "proxy_or_historical_labels_used": False,
        "backend_identity_manifest": _file_record(manifest_path),
        "full_campaign_authorization_receipt": _file_record(authorization_path),
        "input_role_receipts": {
            name: _file_record(path) for name, path in role_paths.items()
        },
        "attempt_ledger": _file_record(attempt_path),
        "accepted_geometry_increment": _file_record(accepted_path),
        "rejected_geometry_increment": _file_record(rejected_path),
        "exact_gds_emx_receipt_index": _file_record(exact_path),
        "s4p_artifact_index": _file_record(artifact_path),
        "long_features": _file_record(feature_path),
        "failure_funnel": _file_record(funnel_path),
        "failure_accounting": dict(funnel),
        "simulator_action_taken": False,
    }
    if golden_source is not None:
        receipt.update({
            "golden_source_receipt": golden_source,
            "golden_validation": golden_validation,
            "golden_validation_status": "PASS" if golden_validation is not None else "FAIL",
            "validation_geometry_count": len(validation_rows),
            "validation_feature_rows": len(validation_rows) * len(FREQUENCY_GRID_HZ),
            "validation_products": validation_products,
            "production_accepted_count_delta": 0,
            "production_geometry_frequency_rows": 0,
        })
    receipt_path = out_dir / RECEIPT_NAME
    _write_json(receipt_path, receipt)
    _write_sums(
        out_dir,
        [
            receipt_path,
            attempt_path,
            accepted_path,
            rejected_path,
            exact_path,
            artifact_path,
            feature_path,
            funnel_path,
            *validation_files,
        ],
    )
    return receipt


def _validate_authorization(receipt: Mapping[str, Any], *, manifest_sha: str) -> None:
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
        raise AttemptProductError("FULL_CAMPAIGN authorization mismatch")


def _validate_role_receipt(
    path: Path,
    *,
    label: str,
    stage: str,
    manifest_sha: str,
    authorization_sha: str,
) -> dict[str, Any]:
    receipt = _read_json(path, f"{label} role receipt")
    if not (
        receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == EXPECTED_DECISIONS[label]
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and str(receipt.get("stage") or "").upper() == stage
    ):
        raise AttemptProductError(f"{label} role receipt contract mismatch")
    manifest_record = receipt.get("backend_identity_manifest")
    if isinstance(manifest_record, Mapping) and manifest_record.get("sha256") != manifest_sha:
        raise AttemptProductError(f"{label} role receipt backend identity mismatch")
    authorization_record = receipt.get("full_campaign_authorization_receipt")
    if (
        isinstance(authorization_record, Mapping)
        and authorization_record.get("sha256") != authorization_sha
    ):
        raise AttemptProductError(f"{label} role receipt authorization mismatch")
    return receipt


def _validate_counts(
    receipt: Mapping[str, Any],
    submitted: int,
    passed: int,
    failed: int,
    prefix: str,
) -> None:
    pass_keys = {
        "cadence": "cadence_pass_count",
        "identity": "identity_pass_count",
        "calibre": "calibre_pass_count",
        "receipt": "receipt_pass_count",
        "emx": "emx_pass_count",
        "qa": "qa_pass_count",
    }
    fail_keys = {
        "cadence": "cadence_fail_count",
        "identity": "identity_fail_count",
        "calibre": "calibre_fail_count",
        "receipt": "receipt_fail_count",
        "emx": "emx_fail_count",
        "qa": "qa_fail_count",
    }
    values = (
        _nonnegative_int(receipt.get("submitted_count"), f"{prefix}.submitted_count"),
        _nonnegative_int(receipt.get(pass_keys[prefix]), f"{prefix}.pass_count"),
        _nonnegative_int(receipt.get(fail_keys[prefix]), f"{prefix}.fail_count"),
    )
    if values != (submitted, passed, failed) or passed + failed != submitted:
        raise AttemptProductError(f"{prefix} receipt counts do not match its indexes")


def _candidate_map(
    rows: Sequence[Mapping[str, str]], *, golden_source_receipt: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    if golden_source_receipt is not None and len(rows) != 1:
        raise AttemptProductError("historical Golden source must bind exactly one row")
    required = {
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "geometry_id",
        "geometry_sha256",
        "campaign_id",
        "campaign_contract_fingerprint",
        "campaign_phase",
        "acquisition_source",
        "analytical_status",
        "topology_status",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    result: dict[str, dict[str, str]] = {}
    for line, raw in enumerate(rows, start=2):
        row = {key: str(value or "").strip() for key, value in raw.items()}
        missing = [field for field in required if not row.get(field)]
        if missing:
            raise AttemptProductError(
                f"raw candidate line {line} lacks values: {sorted(missing)}"
            )
        candidate = _sha_value(row["candidate_id_sha256"], "candidate identity")
        geometry = _sha_value(row["geometry_sha256"], "geometry identity")
        if candidate in result:
            raise AttemptProductError("raw candidate identities are duplicated")
        if not (
            candidate == geometry
            and row["candidate_geometry_identity_sha256"].lower() == geometry
            and row["geometry_id"] == geometry
        ):
            raise AttemptProductError("candidate and canonical geometry identities are not bound")
        actual_geometry = canonical_geometry_sha256(
            {name: row[f"geom__{name}"] for name in GEOMETRY_FIELDS}
        )
        if actual_geometry != geometry:
            raise AttemptProductError("raw candidate canonical geometry SHA-256 mismatch")
        if not (
            row["campaign_id"] == CAMPAIGN_ID
            and row["campaign_contract_fingerprint"]
            == SCIENTIFIC_CONTRACT_FINGERPRINT
            and row["analytical_status"].upper() == "PASS"
            and row["topology_status"].upper() == "PASS"
        ):
            raise AttemptProductError("raw candidate contract or pre-simulator gates mismatch")
        phase = row["campaign_phase"]
        if golden_source_receipt is not None:
            source = _read_json(_identity_path(golden_source_receipt, "Golden source receipt"), "Golden source receipt")
            validate_safe_anchor_source(
                golden_source_receipt, stage="GOLDEN", geometry_sha256=geometry,
                config_sha256=source.get("corrected_private_configuration", {}).get("sha256"),
                contract_fingerprint=SCIENTIFIC_CONTRACT_FINGERPRINT,
            )
            if row["acquisition_source"] != SAFE_ANCHOR_SOURCE or phase != "PHASE_A":
                raise AttemptProductError("Golden candidate source does not match bound source")
        elif (
            phase not in ACQUISITION_SOURCES_BY_PHASE
            or row["acquisition_source"] not in ACQUISITION_SOURCES_BY_PHASE[phase]
        ):
            raise AttemptProductError("raw candidate acquisition source is invalid")
        result[candidate] = row
    return result


def _index_map(path: Path, label: str) -> dict[str, dict[str, str]]:
    rows, fields = _read_csv(path)
    if "candidate_id_sha256" not in fields or not {
        "geometry_sha256",
        "candidate_geometry_identity_sha256",
    }.intersection(fields):
        raise AttemptProductError(f"{label} lacks candidate/geometry identity fields")
    result: dict[str, dict[str, str]] = {}
    geometries: set[str] = set()
    for line, raw in enumerate(rows, start=2):
        row = dict(raw)
        candidate = _sha_value(row.get("candidate_id_sha256"), f"{label} candidate")
        geometry = _sha_value(
            row.get("geometry_sha256")
            or row.get("candidate_geometry_identity_sha256"),
            f"{label} geometry",
        )
        candidate_geometry = str(
            row.get("candidate_geometry_identity_sha256") or ""
        ).strip()
        if candidate_geometry and _sha_value(
            candidate_geometry,
            f"{label} candidate geometry",
        ) != geometry:
            raise AttemptProductError(
                f"{label} candidate/geometry identity aliases disagree at line {line}"
            )
        row["geometry_sha256"] = geometry
        if candidate in result or geometry in geometries:
            raise AttemptProductError(f"{label} contains duplicate candidate or geometry")
        result[candidate] = row
        geometries.add(geometry)
    return result


def _partition(
    submitted: Mapping[str, Any],
    passed: Mapping[str, Any],
    failed: Mapping[str, Any],
    label: str,
) -> None:
    if set(passed) & set(failed):
        raise AttemptProductError(f"{label} pass/fail indexes overlap")
    if set(passed) | set(failed) != set(submitted):
        raise AttemptProductError(f"{label} pass/fail indexes do not partition input")
    for candidate, row in {**passed, **failed}.items():
        if row["geometry_sha256"] != submitted[candidate]["geometry_sha256"]:
            raise AttemptProductError(f"{label} candidate-to-geometry binding drifted")


def _add_terminal_rows(
    terminal: dict[str, tuple[str, Mapping[str, str]]],
    rows: Mapping[str, Mapping[str, str]],
    terminal_stage: str,
) -> None:
    for candidate, row in rows.items():
        _add_terminal(terminal, candidate, terminal_stage, row)


def _add_terminal(
    terminal: dict[str, tuple[str, Mapping[str, str]]],
    candidate: str,
    terminal_stage: str,
    row: Mapping[str, str],
) -> None:
    if candidate in terminal:
        raise AttemptProductError("candidate appears in multiple terminal stages")
    terminal[candidate] = (terminal_stage, row)


def _exact_failure_terminal(row: Mapping[str, str]) -> str:
    value = str(row.get("terminal_stage") or "").strip().upper()
    aliases = {
        "EMX_FAILURES": "EMX_FAILURE",
        "EMX_FAILURE": "EMX_FAILURE",
        "INCOMPLETE_FREQUENCY_FAILURES": "INCOMPLETE_FREQUENCY_FAILURE",
        "INCOMPLETE_FREQUENCY_FAILURE": "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURES": "S4P_PARSING_FAILURE",
        "S4P_PARSING_FAILURE": "S4P_PARSING_FAILURE",
    }
    if value not in aliases:
        raise AttemptProductError(f"fresh-EMX failure has unsupported terminal stage {value!r}")
    terminal = aliases[value]
    if terminal in {"INCOMPLETE_FREQUENCY_FAILURE", "S4P_PARSING_FAILURE"}:
        _pinned_file(
            Path(str(row.get("s4p_path") or "")),
            row.get("s4p_sha256"),
            "failed fresh-EMX S4P",
        )
        if str(row.get("fresh_real_emx") or "").strip().lower() not in {"1", "true", "yes"}:
            raise AttemptProductError("post-EMX S4P failure is not marked fresh real EMX")
    return terminal


def _qa_failure_terminal(row: Mapping[str, str]) -> str:
    explicit = str(row.get("terminal_stage") or "").strip().upper()
    if explicit in {"S_TO_Z_FAILURE", "FEATURE_EXTRACTION_FAILURE"}:
        return explicit
    error = str(row.get("error") or "")
    if any(
        token in error
        for token in (
            "S-to-Z conversion failed",
            "S-to-Z conversion produced incomplete or non-finite Z",
        )
    ):
        return "S_TO_Z_FAILURE"
    if "differential Z projection produced incomplete or non-finite data" in error:
        return "FEATURE_EXTRACTION_FAILURE"
    raise AttemptProductError(
        "exact56 QA failure is not a classified physical-data failure; retry after diagnosing evidence"
    )


def _ledger_row(
    *,
    candidate_row: Mapping[str, str],
    candidate_source_path: Path,
    candidate_source_sha: str,
    submitted_sequence: int,
    stage: str,
    terminal: str,
    terminal_row: Mapping[str, str],
    accepted_sequence: int | None,
    gds_row: Mapping[str, str] | None,
    calibre_failure_row: Mapping[str, str] | None,
    zero_row: Mapping[str, str] | None,
    exact_pass_row: Mapping[str, str] | None,
    exact_evidence_row: Mapping[str, str] | None,
) -> dict[str, Any]:
    candidate = candidate_row["candidate_id_sha256"]
    attempt_id = (
        f"b56v2_{stage.lower()}_{candidate_source_sha[:12]}_"
        f"{submitted_sequence:06d}_{candidate[:16]}"
    )
    statuses = STATUS_BY_TERMINAL[terminal]
    row: dict[str, Any] = {
        "attempt_id": attempt_id,
        "retry_of_attempt_id": candidate_row.get("retry_of_attempt_id", ""),
        "geometry_id": candidate_row["geometry_id"],
        "geometry_sha256": candidate_row["geometry_sha256"],
        "campaign_contract_fingerprint": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "accepted_sequence": accepted_sequence or "",
        "campaign_phase": candidate_row["campaign_phase"],
        "acquisition_source": candidate_row["acquisition_source"],
        "terminal_stage": terminal,
        "terminal_error": terminal_row.get("error", ""),
        "calibre_blocking_violations": 0,
        "frequency_points": 0,
        "fresh_real_emx": "false",
        "candidate_source_path": str(candidate_source_path),
        "candidate_source_sha256": candidate_source_sha,
        "gds_path": "",
        "gds_sha256": "",
        "calibre_report_path": "",
        "calibre_report_sha256": "",
        "emx_log_path": "",
        "emx_log_sha256": "",
        "s4p_path": "",
        "s4p_sha256": "",
    }
    for name in GEOMETRY_FIELDS:
        row[f"geom__{name}"] = candidate_row[f"geom__{name}"]
    row.update(dict(zip(STATUS_FIELDS, statuses)))

    if terminal == "CALIBRE_FAILURE":
        if gds_row is None or calibre_failure_row is None:
            raise AttemptProductError("Calibre failure lacks GDS or DRC evidence")
        blocking = _positive_int(
            calibre_failure_row.get("blocking_drc_violation_count"),
            "Calibre blocking violation count",
        )
        row["calibre_blocking_violations"] = blocking
        _bind_path_pair(row, gds_row, "gds_path", "gds_sha256", "Calibre-failed GDS")
        summary_path = _pinned_file(
            Path(str(calibre_failure_row.get("drc_summary_path") or "")),
            calibre_failure_row.get("drc_summary_sha256"),
            "Calibre failure summary",
        )
        summary = _read_json(summary_path, "Calibre failure summary")
        report_path = _pinned_file(
            Path(str(summary.get("drc_report_path") or "")),
            summary.get("drc_report_sha256"),
            "Calibre failure report",
        )
        row["calibre_report_path"] = str(report_path)
        row["calibre_report_sha256"] = _sha256(report_path)

    if terminal in {
        "EMX_FAILURE",
        "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURE",
        "S_TO_Z_FAILURE",
        "FEATURE_EXTRACTION_FAILURE",
        "ACCEPTED",
        "GOLDEN_VALIDATION_PASS",
    }:
        if zero_row is None:
            raise AttemptProductError("post-Calibre terminal row lacks zero-blocking evidence")
        zero_receipt_path = _pinned_file(
            Path(zero_row["calibre_receipt_path"]),
            zero_row["calibre_receipt_sha256"],
            "zero-blocking Calibre receipt",
        )
        zero_receipt = _read_json(zero_receipt_path, "zero-blocking Calibre receipt")
        _bind_receipt_path_pair(row, zero_receipt, "gds_path", "gds_sha256", "zero-blocking GDS")
        _bind_receipt_path_pair(
            row,
            zero_receipt,
            "calibre_report_path",
            "calibre_report_sha256",
            "zero-blocking Calibre report",
        )

    if terminal in {
        "S_TO_Z_FAILURE",
        "FEATURE_EXTRACTION_FAILURE",
        "ACCEPTED",
        "GOLDEN_VALIDATION_PASS",
    }:
        if exact_pass_row is None:
            raise AttemptProductError("post-EMX QA terminal lacks exact fresh-EMX receipt")
        exact_receipt_path = _pinned_file(
            Path(exact_pass_row["exact_gds_emx_receipt_path"]),
            exact_pass_row["exact_gds_emx_receipt_sha256"],
            "exact fresh-EMX receipt",
        )
        exact_receipt = _read_json(exact_receipt_path, "exact fresh-EMX receipt")
        output = _mapping(exact_receipt.get("emx_output"), "fresh-EMX output")
        _bind_receipt_path_pair(
            row,
            output,
            "emx_log_path",
            "emx_log_sha256",
            "fresh-EMX command evidence",
            source_path_field="emx_command_path",
            source_sha_field="emx_command_sha256",
        )
        _bind_receipt_path_pair(
            row,
            output,
            "s4p_path",
            "s4p_sha256",
            "fresh-EMX S4P",
            source_path_field="touchstone_path",
            source_sha_field="touchstone_sha256",
        )
        row["frequency_points"] = len(FREQUENCY_GRID_HZ)
        row["fresh_real_emx"] = "true"

    if terminal in {
        "EMX_FAILURE",
        "INCOMPLETE_FREQUENCY_FAILURE",
        "S4P_PARSING_FAILURE",
    }:
        if exact_evidence_row is None:
            raise AttemptProductError("fresh-EMX failure lacks delegate evidence")
        _bind_path_pair(
            row,
            exact_evidence_row,
            "delegate_result_path",
            "delegate_result_sha256",
            "fresh-EMX attempt evidence",
            destination_path_field="emx_log_path",
            destination_sha_field="emx_log_sha256",
        )
        if terminal in {"INCOMPLETE_FREQUENCY_FAILURE", "S4P_PARSING_FAILURE"}:
            _bind_path_pair(
                row,
                terminal_row,
                "s4p_path",
                "s4p_sha256",
                "failed fresh-EMX S4P",
            )
            row["frequency_points"] = _nonnegative_int(
                terminal_row.get("frequency_points"), "failed S4P frequency count"
            )
            row["fresh_real_emx"] = "true"
    return {field: row.get(field, "") for field in LEDGER_FIELDS}


def _renumber_accepted_products(
    accepted_sequence: Mapping[str, int],
    *,
    exact_rows: list[dict[str, str]],
    artifact_rows: list[dict[str, str]],
    feature_rows: list[dict[str, str]],
) -> None:
    for label, rows in (
        ("exact receipt index", exact_rows),
        ("S4P artifact index", artifact_rows),
        ("long features", feature_rows),
    ):
        seen: set[str] = set()
        for row in rows:
            candidate = _sha_value(row.get("candidate_id_sha256"), f"{label} candidate")
            if candidate not in accepted_sequence:
                raise AttemptProductError(f"{label} contains a nonaccepted candidate")
            row["accepted_sequence"] = str(accepted_sequence[candidate])
            seen.add(candidate)
        if seen != set(accepted_sequence):
            raise AttemptProductError(f"{label} does not cover every accepted candidate")
    feature_rows.sort(
        key=lambda row: (
            int(row["accepted_sequence"]),
            int(row["frequency_hz"]),
        )
    )
    exact_rows.sort(key=lambda row: int(row["accepted_sequence"]))
    artifact_rows.sort(key=lambda row: int(row["accepted_sequence"]))


def _bind_path_pair(
    destination: dict[str, Any],
    source: Mapping[str, Any],
    source_path_field: str,
    source_sha_field: str,
    label: str,
    *,
    destination_path_field: str | None = None,
    destination_sha_field: str | None = None,
) -> None:
    path = _pinned_file(
        Path(str(source.get(source_path_field) or "")),
        source.get(source_sha_field),
        label,
    )
    destination[destination_path_field or source_path_field] = str(path)
    destination[destination_sha_field or source_sha_field] = _sha256(path)


def _bind_receipt_path_pair(
    destination: dict[str, Any],
    source: Mapping[str, Any],
    destination_path_field: str,
    destination_sha_field: str,
    label: str,
    *,
    source_path_field: str | None = None,
    source_sha_field: str | None = None,
) -> None:
    _bind_path_pair(
        destination,
        source,
        source_path_field or destination_path_field,
        source_sha_field or destination_sha_field,
        label,
        destination_path_field=destination_path_field,
        destination_sha_field=destination_sha_field,
    )


def _assert_record_matches(value: Any, expected_path: Path, label: str) -> None:
    actual = _identity_path(value, label)
    if actual != expected_path.resolve():
        raise AttemptProductError(f"{label} path does not match the preceding role")


def _identity_path(value: Any, label: str) -> Path:
    record = _mapping(value, label)
    path = _regular_file(Path(str(record.get("path") or "")), label)
    if (
        record.get("size_bytes") != path.stat().st_size
        or record.get("sha256") != _sha256(path)
    ):
        raise AttemptProductError(f"{label} identity mismatch")
    return path


def _pinned_file(path: Path, expected_sha: Any, label: str) -> Path:
    resolved = _regular_file(path, label)
    digest = _sha_value(expected_sha, f"{label} SHA-256")
    if _sha256(resolved) != digest:
        raise AttemptProductError(f"{label} SHA-256 mismatch")
    return resolved


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise AttemptProductError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AttemptProductError(f"{label} is not an object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AttemptProductError(f"{label} is not an object")
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
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def _write_sums(root: Path, paths: Sequence[Path]) -> None:
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


def _require_unchanged(path: Path, expected_sha: str, label: str) -> None:
    if _sha256(path) != expected_sha:
        raise AttemptProductError(f"{label} changed during assembly")


def _sha_value(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(text) is None:
        raise AttemptProductError(f"{label} is not SHA-256")
    return text


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AttemptProductError(f"{label} is not an integer") from exc
    if parsed < 0:
        raise AttemptProductError(f"{label} is negative")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    parsed = _nonnegative_int(value, label)
    if parsed <= 0:
        raise AttemptProductError(f"{label} must be positive")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
