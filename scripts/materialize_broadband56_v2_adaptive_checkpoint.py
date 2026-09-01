#!/usr/bin/env python3
"""Expose one exact real-EMX checkpoint for a frozen accepted-count shard.

At a required checkpoint or adaptive 5k boundary this role builds the
cumulative raw products and runs the non-simulator checkpoint auditor.  Inside
a partially accepted shard it reuses the exact checkpoint that began that
shard.  It never launches Cadence, Calibre, EMX, or a candidate queue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FREQUENCY_POINTS,
    REQUIRED_CHECKPOINT_COUNTS,
    adaptive_round_for_current_accepted,
    frozen_checkpoint_start,
    next_frozen_accepted_boundary,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
    PRODUCTION_BACKEND_ID,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    validate_backend_identity_manifest,
    validate_stage_receipt_chain,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (  # noqa: E402
    STAGE_PROGRESS_ARTIFACT_FIELDS,
    accepted_after_progress,
    validate_stage_progress_chain,
)


RECEIPT_SCHEMA = "rfic_transformer.broadband56_v2_adaptive_checkpoint_materializer.v2"
RECEIPT_NAME = "ADAPTIVE_CHECKPOINT_MATERIALIZER_RECEIPT.json"
ROLE_RECEIPT_NAME = "ROLE_RECEIPT.json"


class AdaptiveCheckpointError(RuntimeError):
    """Raised when exact checkpoint evidence cannot be materialized or reused."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(
        path.name
        for path in out_dir.iterdir()
        if path.name not in {"stdout.log", "stderr.log"}
    )
    if unexpected:
        print(
            "overall_status=FAIL\n"
            f"error=no-clobber adaptive checkpoint output is not empty: {unexpected}",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = materialize_checkpoint(args, out_dir=out_dir)
    except (AdaptiveCheckpointError, OSError, ValueError) as exc:
        _write_failure(out_dir, str(exc))
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    _write_json(out_dir / ROLE_RECEIPT_NAME, receipt)
    _write_sha256s(out_dir)
    print("overall_status=PASS")
    print(f"decision={receipt['decision']}")
    print(f"checkpoint_accepted={receipt['checkpoint_accepted']}")
    print(f"checkpoint_dir={receipt['checkpoint_dir']}")
    print(f"receipt={out_dir / ROLE_RECEIPT_NAME}")
    return 0


def materialize_checkpoint(
    args: argparse.Namespace,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    role_receipt_path = out_dir / ROLE_RECEIPT_NAME
    if role_receipt_path.exists() or (out_dir / "checkpoint").exists():
        raise AdaptiveCheckpointError("no-clobber adaptive checkpoint output already exists")

    stage = str(args.stage).upper()
    if stage not in {"PILOT_1000", "PHASE_A", "PHASE_B", "PHASE_C"}:
        raise AdaptiveCheckpointError(
            f"frozen checkpoint role cannot run for {stage}"
        )
    current_accepted = int(args.current_accepted)
    stage_spec = STAGE_BY_NAME[stage]

    campaign_root = Path(args.campaign_root).expanduser().resolve()
    contract_path = Path(args.contract).expanduser().resolve()
    production_config_path = Path(args.production_config).expanduser().resolve()
    geometry_bounds_path = Path(args.geometry_bounds).expanduser().resolve()
    backend_path = Path(args.backend_identity_manifest).expanduser().resolve()
    authorization_path = Path(args.full_campaign_receipt).expanduser().resolve()
    for path, label in (
        (campaign_root, "campaign root"),
        (contract_path, "frozen campaign contract"),
        (production_config_path, "private production configuration"),
        (geometry_bounds_path, "frozen geometry bounds"),
        (backend_path, "backend identity manifest"),
        (authorization_path, "FULL_CAMPAIGN receipt"),
    ):
        if label == "campaign root":
            if not path.is_dir():
                raise AdaptiveCheckpointError(f"{label} is missing: {path}")
        elif not path.is_file() or path.stat().st_size <= 0:
            raise AdaptiveCheckpointError(f"{label} is missing or empty: {path}")

    backend = _read_json(backend_path, "backend identity manifest")
    authorization = _read_json(authorization_path, "FULL_CAMPAIGN receipt")
    backend_sha = _sha256(backend_path)
    authorization_sha = _sha256(authorization_path)
    backend_errors = validate_backend_identity_manifest(backend, verify_files=True)
    if backend_errors:
        raise AdaptiveCheckpointError(
            "backend identity validation failed: " + "; ".join(backend_errors[:10])
        )
    _validate_authorization(authorization, backend_sha=backend_sha)
    _validate_self_identity(backend)

    stage_records = _ordered_stage_records(campaign_root)
    stage_errors = validate_stage_receipt_chain(
        stage_records,
        backend_manifest_sha256=backend_sha,
        authorization_receipt_sha256=authorization_sha,
        verify_artifacts=True,
    )
    if stage_errors:
        raise AdaptiveCheckpointError(
            "stage receipt chain failed: " + "; ".join(stage_errors[:10])
        )
    base_accepted = (
        int(stage_records[-1][1]["accepted_unique_geometries"])
        if stage_records
        else 0
    )
    progress_records = _ordered_progress_records(campaign_root, stage=stage)
    progress_errors = validate_stage_progress_chain(
        progress_records,
        stage=stage,
        base_accepted=base_accepted,
        backend_manifest_sha256=backend_sha,
        authorization_receipt_sha256=authorization_sha,
        verify_artifacts=True,
    )
    if progress_errors:
        raise AdaptiveCheckpointError(
            "stage progress chain failed: " + "; ".join(progress_errors[:10])
        )
    observed_current = accepted_after_progress(
        progress_records,
        base_accepted=base_accepted,
    )
    if observed_current != current_accepted:
        raise AdaptiveCheckpointError(
            f"controller current accepted {current_accepted} differs from receipt chain {observed_current}"
        )

    try:
        checkpoint_count = frozen_checkpoint_start(
            current_accepted,
            stage_base_accepted=base_accepted,
            cumulative_target=stage_spec.cumulative_target,
        )
        accepted_target = next_frozen_accepted_boundary(
            current_accepted,
            cumulative_target=stage_spec.cumulative_target,
        )
    except ValueError as exc:
        raise AdaptiveCheckpointError(str(exc)) from exc
    raw_selection_count = accepted_target - current_accepted
    if stage in {"PHASE_B", "PHASE_C"}:
        round_spec, adaptive_remaining = adaptive_round_for_current_accepted(
            current_accepted
        )
        if (
            round_spec.phase != stage
            or round_spec.accepted_start != checkpoint_count
            or round_spec.accepted_target != accepted_target
            or adaptive_remaining != raw_selection_count
        ):
            raise AdaptiveCheckpointError(
                "adaptive round contract differs from frozen shard boundary"
            )

    subprocesses: list[dict[str, Any]] = []
    if current_accepted == checkpoint_count and current_accepted > base_accepted:
        checkpoint_dir, raw_receipt, subprocesses = _build_boundary_checkpoint(
            out_dir=out_dir,
            stage=stage,
            checkpoint_count=checkpoint_count,
            progress_records=progress_records,
            contract_path=contract_path,
            production_config_path=production_config_path,
            geometry_bounds_path=geometry_bounds_path,
            backend=backend,
        )
        decision = "MATERIALIZE_EXACT_REAL_EMX_FROZEN_CHECKPOINT"
        source = {
            "kind": "CURRENT_STAGE_PROGRESS_BOUNDARY",
            "progress_receipt": _file_record(progress_records[-1][0]),
            "raw_products_receipt": _file_record(raw_receipt),
        }
    elif current_accepted == checkpoint_count:
        checkpoint_dir, source, subprocesses = _checkpoint_from_stage_boundary(
            out_dir=out_dir,
            campaign_root=campaign_root,
            stage_records=stage_records,
            checkpoint_count=checkpoint_count,
            contract_path=contract_path,
            geometry_bounds_path=geometry_bounds_path,
            backend=backend,
        )
        if checkpoint_dir.resolve() != (out_dir / "checkpoint").resolve():
            _link_checkpoint(out_dir / "checkpoint", checkpoint_dir)
            decision = "REUSE_EXACT_TERMINAL_STAGE_CHECKPOINT"
        else:
            decision = "MATERIALIZE_FORMAL_CHECKPOINT_FROM_TERMINAL_RAW_PRODUCTS"
    else:
        checkpoint_dir, source = _checkpoint_from_prior_materializer(
            campaign_root=campaign_root,
            stage=stage,
            checkpoint_count=checkpoint_count,
            backend_sha=backend_sha,
            authorization_sha=authorization_sha,
        )
        _link_checkpoint(out_dir / "checkpoint", checkpoint_dir)
        decision = "REUSE_EXACT_FROZEN_SHARD_START_CHECKPOINT"

    published_checkpoint = (out_dir / "checkpoint").resolve()
    checkpoint_receipt = published_checkpoint / "CHECKPOINT_RECEIPT.json"
    checkpoint_status = published_checkpoint / "CHECKPOINT_STATUS.json"
    _validate_checkpoint(
        checkpoint_dir=published_checkpoint,
        expected_accepted=checkpoint_count,
    )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": decision,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "current_accepted": current_accepted,
        "checkpoint_accepted": checkpoint_count,
        "round_accepted_target": accepted_target,
        "raw_selection_count": raw_selection_count,
        "backend_identity_manifest": _file_record(backend_path),
        "full_campaign_authorization_receipt": _file_record(authorization_path),
        "source": source,
        "checkpoint_dir": str(published_checkpoint),
        "checkpoint_receipt": _file_record(checkpoint_receipt),
        "checkpoint_status": _file_record(checkpoint_status),
        "subprocesses": subprocesses,
        "checks": {
            "full_campaign_authorization_exact": True,
            "backend_manifest_exact": True,
            "stage_and_progress_chains_valid": True,
            "checkpoint_accepted_equals_frozen_shard_start": True,
            "raw_selection_count_closes_to_frozen_boundary": True,
            "fresh_real_emx_checkpoint_pass": True,
            "simulator_action_taken": False,
        },
        "simulator_action_taken": False,
        "cadence_action_taken": False,
        "calibre_action_taken": False,
        "emx_action_taken": False,
    }
    _write_json(out_dir / RECEIPT_NAME, receipt)
    return receipt


def _build_boundary_checkpoint(
    *,
    out_dir: Path,
    stage: str,
    checkpoint_count: int,
    progress_records: Sequence[tuple[Path, Mapping[str, Any]]],
    contract_path: Path,
    production_config_path: Path,
    geometry_bounds_path: Path,
    backend: Mapping[str, Any],
) -> tuple[Path, Path, list[dict[str, Any]]]:
    if not progress_records:
        raise AdaptiveCheckpointError("adaptive boundary has no progress receipt")
    progress = progress_records[-1][1]
    if int(progress.get("accepted_after") or -1) != checkpoint_count:
        raise AdaptiveCheckpointError("latest progress receipt is not the requested boundary")
    cumulative = progress.get("round_cumulative_inputs")
    if not isinstance(cumulative, Mapping) or set(cumulative) != set(
        STAGE_PROGRESS_ARTIFACT_FIELDS
    ):
        raise AdaptiveCheckpointError(
            "boundary progress receipt lacks exact cumulative input identities"
        )
    inputs = {
        name: _verified_file_record(
            cumulative.get(name),
            label=f"round_cumulative_inputs.{name}",
        )
        for name in STAGE_PROGRESS_ARTIFACT_FIELDS
    }
    scripts = backend.get("script_identities")
    if not isinstance(scripts, Mapping):
        raise AdaptiveCheckpointError("backend manifest lacks script identities")
    raw_script = _verified_script(scripts, "raw_products_finalizer")
    checkpoint_script = _verified_script(scripts, "checkpoint_auditor")
    raw_out = out_dir / "raw_products"
    checkpoint_out = out_dir / "checkpoint"
    raw_command = [
        sys.executable,
        str(raw_script),
        "--contract",
        str(contract_path),
        "--production-config",
        str(production_config_path),
        "--attempt-ledger",
        str(inputs["attempt_ledger"]),
        "--long-features",
        str(inputs["long_features"]),
        "--expected-accepted",
        str(checkpoint_count),
        "--out-dir",
        str(raw_out),
    ]
    subprocesses = [_run_bound_command(raw_command, out_dir, "raw_products")]
    raw_receipt = raw_out / "RAW_PRODUCTS_RECEIPT.json"
    raw = _read_json(raw_receipt, "raw products receipt")
    if raw.get("overall_status") != "PASS":
        raise AdaptiveCheckpointError("raw products receipt is not PASS")
    outputs = raw.get("outputs")
    if not isinstance(outputs, Mapping):
        raise AdaptiveCheckpointError("raw products receipt lacks outputs")
    raw_paths = {
        name: _verified_file_record(outputs.get(name), label=f"raw_outputs.{name}")
        for name in (
            "accepted_geometries",
            "long_features",
            "artifact_index",
            "failure_funnel",
        )
    }
    audit_mode = (
        "checkpoint" if checkpoint_count in REQUIRED_CHECKPOINT_COUNTS else "round"
    )
    checkpoint_command = [
        sys.executable,
        str(checkpoint_script),
        "--contract",
        str(contract_path),
        "--accepted-geometries",
        str(raw_paths["accepted_geometries"]),
        "--geometry-bounds",
        str(geometry_bounds_path),
        "--long-features",
        str(raw_paths["long_features"]),
        "--artifact-index",
        str(raw_paths["artifact_index"]),
        "--failure-funnel",
        str(raw_paths["failure_funnel"]),
        "--audit-mode",
        audit_mode,
        "--expected-accepted",
        str(checkpoint_count),
        "--out-dir",
        str(checkpoint_out),
    ]
    subprocesses.append(
        _run_bound_command(checkpoint_command, out_dir, "checkpoint_audit")
    )
    return checkpoint_out, raw_receipt, subprocesses


def _checkpoint_from_stage_boundary(
    *,
    out_dir: Path,
    campaign_root: Path,
    stage_records: Sequence[tuple[Path, Mapping[str, Any]]],
    checkpoint_count: int,
    contract_path: Path,
    geometry_bounds_path: Path,
    backend: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    for receipt_path, receipt in reversed(stage_records):
        if int(receipt.get("accepted_unique_geometries") or -1) != checkpoint_count:
            continue
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise AdaptiveCheckpointError(
                "terminal stage receipt lacks artifact identities"
            )
        checkpoint_receipt = _verified_file_record(
            artifacts.get("checkpoint_receipt"),
            label="terminal_stage.checkpoint_receipt",
        )
        checkpoint_dir = checkpoint_receipt.parent
        _require_under(checkpoint_dir, campaign_root, "terminal checkpoint")
        checkpoint = _read_json(checkpoint_receipt, "terminal checkpoint receipt")
        expected_mode = _expected_checkpoint_mode(checkpoint_count)
        actual_mode = str(checkpoint.get("audit_mode") or "")
        if actual_mode == expected_mode:
            _validate_checkpoint(
                checkpoint_dir=checkpoint_dir,
                expected_accepted=checkpoint_count,
            )
            return checkpoint_dir, {
                "kind": "TERMINAL_STAGE_RECEIPT",
                "stage_receipt": _file_record(receipt_path),
            }, []
        if not (
            checkpoint_count == 1_000
            and actual_mode == "pilot"
            and expected_mode == "checkpoint"
        ):
            raise AdaptiveCheckpointError(
                "terminal checkpoint mode cannot satisfy frozen boundary: "
                f"accepted={checkpoint_count}, actual={actual_mode}, "
                f"expected={expected_mode}"
            )
        raw_receipt = _verified_file_record(
            artifacts.get("raw_products_receipt"),
            label="terminal_stage.raw_products_receipt",
        )
        checkpoint_dir, subprocesses = _build_checkpoint_from_raw_products(
            out_dir=out_dir,
            checkpoint_count=checkpoint_count,
            raw_receipt=raw_receipt,
            contract_path=contract_path,
            geometry_bounds_path=geometry_bounds_path,
            backend=backend,
        )
        return checkpoint_dir, {
            "kind": "TERMINAL_STAGE_RAW_PRODUCTS_REAUDIT",
            "stage_receipt": _file_record(receipt_path),
            "raw_products_receipt": _file_record(raw_receipt),
            "superseded_pilot_checkpoint_receipt": _file_record(
                checkpoint_receipt
            ),
        }, subprocesses
    raise AdaptiveCheckpointError(
        f"no terminal stage checkpoint found at {checkpoint_count} accepted"
    )


def _build_checkpoint_from_raw_products(
    *,
    out_dir: Path,
    checkpoint_count: int,
    raw_receipt: Path,
    contract_path: Path,
    geometry_bounds_path: Path,
    backend: Mapping[str, Any],
) -> tuple[Path, list[dict[str, Any]]]:
    raw = _read_json(raw_receipt, "raw products receipt")
    counts = raw.get("counts")
    checks = raw.get("checks")
    if not (
        raw.get("schema") == "broadband56_raw_products_receipt_v1"
        and raw.get("overall_status") == "PASS"
        and raw.get("decision") == "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS"
        and raw.get("campaign_id") == CAMPAIGN_ID
        and raw.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and isinstance(counts, Mapping)
        and int(counts.get("accepted_geometries") or -1) == checkpoint_count
        and int(counts.get("s4p_artifacts") or -1) == checkpoint_count
        and int(counts.get("geometry_frequency_rows") or -1)
        == checkpoint_count * FREQUENCY_POINTS
        and isinstance(checks, Mapping)
        and checks.get("all_accepted_s4p_are_fresh_exact_56_point_four_port")
        is True
        and checks.get("long_features_bound_to_exact_s4p_s_and_z") is True
        and checks.get("long_physical_features_recomputed_from_exact_s4p")
        is True
        and checks.get("proxy_values_excluded_from_labels") is True
    ):
        raise AdaptiveCheckpointError(
            "terminal raw products are not exact fresh-real-EMX PASS evidence"
        )
    outputs = raw.get("outputs")
    if not isinstance(outputs, Mapping):
        raise AdaptiveCheckpointError("raw products receipt lacks outputs")
    raw_paths = {
        name: _verified_file_record(outputs.get(name), label=f"raw_outputs.{name}")
        for name in (
            "accepted_geometries",
            "long_features",
            "artifact_index",
            "failure_funnel",
        )
    }
    scripts = backend.get("script_identities")
    if not isinstance(scripts, Mapping):
        raise AdaptiveCheckpointError("backend manifest lacks script identities")
    checkpoint_script = _verified_script(scripts, "checkpoint_auditor")
    checkpoint_out = out_dir / "checkpoint"
    command = [
        sys.executable,
        str(checkpoint_script),
        "--contract",
        str(contract_path),
        "--accepted-geometries",
        str(raw_paths["accepted_geometries"]),
        "--geometry-bounds",
        str(geometry_bounds_path),
        "--long-features",
        str(raw_paths["long_features"]),
        "--artifact-index",
        str(raw_paths["artifact_index"]),
        "--failure-funnel",
        str(raw_paths["failure_funnel"]),
        "--audit-mode",
        _expected_checkpoint_mode(checkpoint_count),
        "--expected-accepted",
        str(checkpoint_count),
        "--out-dir",
        str(checkpoint_out),
    ]
    subprocesses = [_run_bound_command(command, out_dir, "checkpoint_reaudit")]
    _validate_checkpoint(
        checkpoint_dir=checkpoint_out,
        expected_accepted=checkpoint_count,
    )
    return checkpoint_out, subprocesses


def _checkpoint_from_prior_materializer(
    *,
    campaign_root: Path,
    stage: str,
    checkpoint_count: int,
    backend_sha: str,
    authorization_sha: str,
) -> tuple[Path, dict[str, Any]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(
        (campaign_root / "stages").glob(
            f"**/{RECEIPT_NAME}"
        )
    ):
        receipt = _read_json(path, "prior adaptive checkpoint receipt")
        if (
            receipt.get("overall_status") == "PASS"
            and str(receipt.get("stage") or "").upper() == stage
            and int(receipt.get("checkpoint_accepted") or -1) == checkpoint_count
            and receipt.get("backend_identity_manifest", {}).get("sha256")
            == backend_sha
            and receipt.get("full_campaign_authorization_receipt", {}).get("sha256")
            == authorization_sha
            and receipt.get("simulator_action_taken") is False
        ):
            candidates.append((path, receipt))
    if not candidates:
        raise AdaptiveCheckpointError(
            f"no prior materializer checkpoint found at {checkpoint_count} accepted"
        )
    path, receipt = candidates[-1]
    checkpoint_receipt = _verified_file_record(
        receipt.get("checkpoint_receipt"),
        label="prior_materializer.checkpoint_receipt",
    )
    checkpoint_dir = checkpoint_receipt.parent
    _require_under(checkpoint_dir, campaign_root, "prior materializer checkpoint")
    _validate_checkpoint(
        checkpoint_dir=checkpoint_dir,
        expected_accepted=checkpoint_count,
    )
    return checkpoint_dir, {
        "kind": "PRIOR_FROZEN_CHECKPOINT_MATERIALIZER",
        "materializer_receipt": _file_record(path),
    }


def _validate_checkpoint(*, checkpoint_dir: Path, expected_accepted: int) -> None:
    receipt_path = checkpoint_dir / "CHECKPOINT_RECEIPT.json"
    status_path = checkpoint_dir / "CHECKPOINT_STATUS.json"
    receipt = _read_json(receipt_path, "checkpoint receipt")
    status = _read_json(status_path, "checkpoint status")
    receipt_checks = receipt.get("checks")
    expected_mode = _expected_checkpoint_mode(expected_accepted)
    if expected_accepted == TARGET_ACCEPTED_GEOMETRIES:
        expected_state = "COMPLETE_200K"
    elif expected_mode == "golden":
        expected_state = "GOLDEN_COMPLETE"
    elif expected_mode == "pilot":
        expected_state = f"PILOT_{expected_accepted}_COMPLETE"
    elif expected_mode == "checkpoint":
        expected_state = "CHECKPOINT_COMPLETE"
    else:
        expected_state = f"ROUND_{expected_accepted}_COMPLETE"
    inputs = receipt.get("inputs")
    outputs = receipt.get("outputs")
    input_fields = {
        "contract",
        "geometry_bounds",
        "accepted_geometries",
        "long_features",
        "artifact_index",
        "failure_funnel",
    }
    output_fields = {
        "coverage_cells",
        "coverage_by_frequency",
        "coverage_marginals",
        "coverage_pairwise",
        "geometry_coverage_summary",
        "geometry_coverage_marginals",
        "geometry_coverage_pairwise",
        "coverage_summary",
        "checkpoint_status",
        "failure_funnel",
    }
    evidence_valid = (
        isinstance(inputs, Mapping)
        and input_fields.issubset(inputs)
        and all(
            _identity_record_matches_file(inputs[field]) for field in input_fields
        )
        and isinstance(outputs, Mapping)
        and output_fields.issubset(outputs)
        and all(
            _identity_record_matches_file(outputs[field], root=checkpoint_dir)
            for field in output_fields
        )
        and Path(str(outputs["checkpoint_status"]["path"])).resolve()
        == status_path.resolve()
    )
    if not (
        receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == "USE_CHECKPOINT"
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and int(receipt.get("expected_accepted") or -1) == expected_accepted
        and receipt.get("audit_mode") == expected_mode
        and isinstance(receipt_checks, list)
        and receipt_checks
        and all(
            isinstance(item, Mapping) and item.get("pass") is True
            for item in receipt_checks
        )
        and status.get("campaign_id") == CAMPAIGN_ID
        and status.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and status.get("audit_mode") == expected_mode
        and status.get("checkpoint_status") == expected_state
        and int(status.get("accepted_geometries") or -1) == expected_accepted
        and int(status.get("s4p_artifacts") or -1) == expected_accepted
        and int(status.get("geometry_frequency_rows") or -1)
        == expected_accepted * FREQUENCY_POINTS
        and evidence_valid
    ):
        raise AdaptiveCheckpointError(
            f"checkpoint at {checkpoint_dir} is not exact PASS evidence"
        )


def _expected_checkpoint_mode(expected_accepted: int) -> str:
    accepted = int(expected_accepted)
    if accepted == 1:
        return "golden"
    if accepted == 32:
        return "pilot"
    if accepted in REQUIRED_CHECKPOINT_COUNTS:
        return "checkpoint"
    return "round"


def _identity_record_matches_file(
    value: Any,
    *,
    root: Path | None = None,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    path = Path(str(value.get("path") or "")).expanduser().resolve()
    if root is not None:
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return False
    return (
        path.is_file()
        and path.stat().st_size == value.get("size_bytes")
        and _sha256(path) == value.get("sha256")
    )


def _validate_authorization(receipt: Mapping[str, Any], *, backend_sha: str) -> None:
    required_true = (
        "automatic_ordered_stage_execution_authorized",
        "cadence_authorized_within_current_stage",
        "calibre_authorized_within_current_stage",
        "emx_authorized_within_current_stage",
        "campaign_200k_authorized",
        "replenished_attempt_rounds_authorized",
    )
    if not (
        receipt.get("schema") == FULL_CAMPAIGN_APPROVAL_SCHEMA
        and receipt.get("overall_status") == "PASS"
        and receipt.get("decision") == FULL_CAMPAIGN_PASS_DECISION
        and receipt.get("authorization_scope") == FULL_CAMPAIGN_APPROVAL_SCOPE
        and receipt.get("campaign_id") == CAMPAIGN_ID
        and receipt.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and receipt.get("backend_identity_manifest", {}).get("sha256")
        == backend_sha
        and receipt.get("accepted_geometry_target")
        == TARGET_ACCEPTED_GEOMETRIES
        and receipt.get("attempt_replenishment_contract")
        == ATTEMPT_REPLENISHMENT_CONTRACT
        and "simulator_geometry_limit" not in receipt
        and all(receipt.get(field) is True for field in required_true)
    ):
        raise AdaptiveCheckpointError("FULL_CAMPAIGN authorization mismatch")


def _validate_self_identity(backend: Mapping[str, Any]) -> None:
    record = backend.get("script_identities", {}).get(
        "adaptive_checkpoint_materializer"
    )
    self_path = Path(__file__).resolve()
    if not (
        isinstance(record, Mapping)
        and Path(str(record.get("path") or "")).resolve() == self_path
        and record.get("sha256") == _sha256(self_path)
    ):
        raise AdaptiveCheckpointError("adaptive checkpoint self-identity mismatch")


def _ordered_stage_records(
    campaign_root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_RECEIPT.json")):
        receipt = _read_json(path, "stage receipt")
        if receipt.get("overall_status") == "PASS":
            records.append((path, receipt))
    return records


def _ordered_progress_records(
    campaign_root: Path,
    *,
    stage: str,
) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(
        (campaign_root / "stages").glob("*/STAGE_PROGRESS_RECEIPT.json")
    ):
        receipt = _read_json(path, "stage progress receipt")
        if str(receipt.get("stage") or "").upper() == stage:
            records.append((path, receipt))
    return records


def _verified_script(records: Mapping[str, Any], role: str) -> Path:
    record = records.get(role)
    if not isinstance(record, Mapping):
        raise AdaptiveCheckpointError(f"backend lacks {role} identity")
    path = Path(str(record.get("path") or "")).expanduser().resolve()
    if not path.is_file() or record.get("sha256") != _sha256(path):
        raise AdaptiveCheckpointError(f"{role} identity mismatch")
    return path


def _verified_file_record(value: Any, *, label: str) -> Path:
    if not isinstance(value, Mapping):
        raise AdaptiveCheckpointError(f"{label} is not an identity record")
    path = Path(str(value.get("path") or "")).expanduser().resolve()
    if not (
        path.is_file()
        and path.stat().st_size == value.get("size_bytes")
        and _sha256(path) == value.get("sha256")
    ):
        raise AdaptiveCheckpointError(f"{label} identity mismatch: {path}")
    return path


def _run_bound_command(
    command: list[str],
    out_dir: Path,
    label: str,
) -> dict[str, Any]:
    stdout_path = out_dir / f"{label}.stdout.log"
    stderr_path = out_dir / f"{label}.stderr.log"
    started = _utc_now()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
            shell=False,
            env=dict(os.environ),
        )
    record = {
        "label": label,
        "started_utc": started,
        "finished_utc": _utc_now(),
        "command_argv_sha256": hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "return_code": int(result.returncode),
        "shell_used": False,
        "stdout": _file_record(stdout_path),
        "stderr": _file_record(stderr_path),
        "simulator_action_taken": False,
    }
    if result.returncode != 0:
        raise AdaptiveCheckpointError(
            f"{label} exited with return code {result.returncode}"
        )
    return record


def _link_checkpoint(link: Path, source: Path) -> None:
    if link.exists() or link.is_symlink():
        raise AdaptiveCheckpointError(f"checkpoint link already exists: {link}")
    link.symlink_to(source, target_is_directory=True)


def _require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise AdaptiveCheckpointError(f"{label} escapes campaign root") from exc


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--current-accepted", required=True, type=int)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--production-config", required=True)
    parser.add_argument("--geometry-bounds", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdaptiveCheckpointError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise AdaptiveCheckpointError(f"{label} is not a JSON object")
    return value


def _file_record(path: Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise AdaptiveCheckpointError(f"identity file is missing: {resolved}")
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_failure(out_dir: Path, error: str) -> None:
    path = out_dir / "ADAPTIVE_CHECKPOINT_MATERIALIZER_FAILURE.json"
    if not path.exists():
        _write_json(
            path,
            {
                "schema": RECEIPT_SCHEMA,
                "generated_utc": _utc_now(),
                "overall_status": "FAIL",
                "decision": "DO_NOT_BUILD_OR_REUSE_ADAPTIVE_CHECKPOINT",
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
                "error": error,
                "simulator_action_taken": False,
            },
        )


def _write_sha256s(out_dir: Path) -> None:
    files = sorted(
        path
        for path in out_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
