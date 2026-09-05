#!/usr/bin/env python3
"""Finalize one bounded broadband56 attempt without launching a simulator.

The command validates the current shard's terminal partition and exact 56-row
feature grain.  A shortfall writes a SHA-linked nonterminal progress receipt.
An exact stage target writes cumulative CSV inputs for the terminal raw-product
and checkpoint roles.  Overshoot and identity drift fail closed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    FROZEN_INTERMEDIATE_ACCEPTED_BOUNDARIES,
    next_frozen_accepted_boundary,
    FREQUENCY_GRID_HZ,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
    STAGE_BY_NAME,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    validate_stage_receipt_chain,
)
from rfic_transformer_inverse_design.campaigns.broadband56_golden_source import GoldenSourceError
from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden_stage
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (  # noqa: E402
    ATTEMPT_FAILURE_ACCOUNTING_FIELDS,
    STAGE_PROGRESS_ARTIFACT_FIELDS,
    STAGE_PROGRESS_DECISION,
    STAGE_PROGRESS_SCHEMA,
    STAGE_PROGRESS_SAFEGUARDS,
    STAGE_PROGRESS_STATUS,
    STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA,
    STAGE_ATTEMPT_TARGET_REACHED_DECISION,
    accepted_after_progress,
    validate_stage_progress_chain,
)


ROLE_RECEIPT_SCHEMA = STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA
ROLE_RECEIPT_NAME = "STAGE_ATTEMPT_FINALIZER_RECEIPT.json"
PROGRESS_RECEIPT_NAME = "STAGE_PROGRESS_RECEIPT.json"
ATTEMPT_ARTIFACT_DIR_NAME = "attempt_artifacts"
CUMULATIVE_DIR_NAME = "cumulative_stage_inputs"
CSV_ARTIFACT_FIELDS = STAGE_PROGRESS_ARTIFACT_FIELDS[:-1]
PRIOR_STAGE_ARTIFACT_FIELDS = {
    "attempt_ledger": "attempt_ledger",
    "accepted_geometry_increment": "accepted_geometry_index",
    "rejected_geometry_increment": "rejected_geometry_index",
    "exact_gds_emx_receipt_index": "exact_gds_emx_receipt_index",
    "s4p_artifact_index": "s4p_artifact_index",
    "failure_funnel": "failure_funnel",
}
MINIMUM_COLUMNS = {
    "attempt_ledger": {"attempt_id", "geometry_sha256", "terminal_stage"},
    "accepted_geometry_increment": {"geometry_sha256"},
    "rejected_geometry_increment": {"geometry_sha256"},
    "exact_gds_emx_receipt_index": {"candidate_id_sha256", "geometry_sha256"},
    "s4p_artifact_index": {"geometry_sha256"},
    "long_features": {"geometry_sha256", "frequency_hz"},
}


class StageAttemptFinalizationError(RuntimeError):
    """Raised when an attempt cannot support progress or exact completion."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = finalize_stage_attempt(args, out_dir=out_dir)
    except StageAttemptFinalizationError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"decision={result['decision']}")
    print(f"accepted_after={result['accepted_after']}")
    print(f"receipt={out_dir / ROLE_RECEIPT_NAME}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--attempt-ledger", required=True)
    parser.add_argument("--accepted-geometry-increment", required=True)
    parser.add_argument("--rejected-geometry-increment", required=True)
    parser.add_argument("--exact-gds-emx-receipt-index", required=True)
    parser.add_argument("--s4p-artifact-index", required=True)
    parser.add_argument("--long-features", required=True)
    parser.add_argument("--failure-funnel", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--simulator-action-taken", action="store_true")
    parser.add_argument("--golden-attempt-products-receipt")
    return parser.parse_args(argv)


def finalize_stage_attempt(
    args: argparse.Namespace,
    *,
    out_dir: Path,
) -> dict[str, Any]:
    stage = str(args.stage).upper()
    spec = STAGE_BY_NAME.get(stage)
    if spec is None:
        raise StageAttemptFinalizationError(f"unknown stage: {stage}")
    campaign_root = Path(args.campaign_root).expanduser().resolve()
    backend_path = _required_file(Path(args.backend_identity_manifest), "backend manifest")
    authorization_path = _required_file(
        Path(args.full_campaign_receipt), "FULL_CAMPAIGN receipt"
    )
    backend_sha256 = _sha256(backend_path)
    authorization_sha256 = _sha256(authorization_path)
    prior_stage_artifacts = _prior_stage_cumulative_artifacts(
        campaign_root,
        stage=stage,
        backend_sha256=backend_sha256,
        authorization_sha256=authorization_sha256,
    )
    artifacts = {
        field: _required_file(Path(getattr(args, field)), field)
        for field in STAGE_PROGRESS_ARTIFACT_FIELDS
    }

    prior_records = _progress_records(campaign_root, stage=stage)
    base_accepted = 0 if stage == "PILOT_32" and not prior_stage_artifacts else _stage_base_accepted(stage)
    progress_errors = validate_stage_progress_chain(
        prior_records,
        stage=stage,
        base_accepted=base_accepted,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        verify_artifacts=True,
    )
    if progress_errors:
        raise StageAttemptFinalizationError(
            "prior progress chain failed validation: " + "; ".join(progress_errors[:10])
        )
    accepted_before = accepted_after_progress(
        prior_records,
        base_accepted=base_accepted,
    )
    attempt_index = len(prior_records) + 1

    golden_path = getattr(args, "golden_attempt_products_receipt", None)
    if golden_path is not None:
        if stage != "GOLDEN" or prior_records or accepted_before != 0:
            raise StageAttemptFinalizationError("validation-only Golden requires the first Golden attempt")
        path = _required_file(Path(golden_path), "Golden attempt products")
        record = _published_file_record(path, path)
        try:
            attempt = golden_stage.validate_attempt(
                record, backend_sha256=backend_sha256, authorization_sha256=authorization_sha256,
            )
            for field, artifact in artifacts.items():
                if attempt.get(field) != _published_file_record(artifact, artifact):
                    raise GoldenSourceError(f"Golden finalizer {field} is not the bound attempt input")
        except (GoldenSourceError, OSError, ValueError, KeyError, TypeError) as exc:
            raise StageAttemptFinalizationError(f"Golden validation failed: {exc}") from exc
        receipt = {
            "schema": ROLE_RECEIPT_SCHEMA, "generated_utc": _utc_now(), "overall_status": "PASS",
            "decision": golden_stage.FINALIZER_DECISION, "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_id": PRODUCTION_BACKEND_ID, "stage": "GOLDEN", "attempt_index": 1,
            "accepted_before": 0, "accepted_this_attempt": 0, "accepted_after": 0,
            "cumulative_target": 1, "raw_candidates_this_attempt": 1,
            "progress_receipt": None, "cumulative_stage_inputs": None,
            "simulator_action_taken": bool(args.simulator_action_taken),
            "simulator_invoked_by_finalizer": False,
            "golden_terminal_mode": golden_stage.TERMINAL_MODE,
            "golden_attempt_products_receipt": record,
            "golden_validation": attempt["golden_validation"],
            "production_accepted_count_delta": 0,
        }
        out_dir.mkdir(parents=True, exist_ok=False)
        _write_json(out_dir / ROLE_RECEIPT_NAME, receipt)
        _write_sha256s(out_dir)
        return receipt

    tables = {
        field: _read_csv(artifacts[field], field)
        for field in CSV_ARTIFACT_FIELDS
    }
    funnel = _read_failure_funnel(artifacts["failure_funnel"])
    raw_count = len(tables["attempt_ledger"][1])
    accepted_count = len(tables["accepted_geometry_increment"][1])
    rejected_count = len(tables["rejected_geometry_increment"][1])
    if raw_count <= 0:
        raise StageAttemptFinalizationError("attempt ledger must contain at least one terminal row")
    if accepted_count + rejected_count != raw_count:
        raise StageAttemptFinalizationError(
            "accepted and rejected increments do not partition the attempt ledger"
        )
    if len(tables["exact_gds_emx_receipt_index"][1]) != accepted_count:
        raise StageAttemptFinalizationError("exact GDS/EMX index count mismatch")
    if len(tables["s4p_artifact_index"][1]) != accepted_count:
        raise StageAttemptFinalizationError("S4P artifact index count mismatch")
    if len(tables["long_features"][1]) != accepted_count * len(FREQUENCY_GRID_HZ):
        raise StageAttemptFinalizationError("long-feature row count is not accepted_count times 56")
    _validate_geometry_identity_sets(tables)
    _validate_failure_funnel(
        funnel,
        raw_count=raw_count,
        accepted_count=accepted_count,
    )

    accepted_after = accepted_before + accepted_count
    next_boundary = next_frozen_accepted_boundary(
        accepted_before, cumulative_target=spec.cumulative_target
    )
    if accepted_after > next_boundary:
        raise StageAttemptFinalizationError(
            f"accepted count overshoots {stage} checkpoint: {accepted_after}>{next_boundary}"
        )

    staging = out_dir.with_name(f".{out_dir.name}.staging.{os.getpid()}")
    if staging.exists():
        raise StageAttemptFinalizationError(f"staging path already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        staged_artifacts, artifact_records = _stage_attempt_artifacts(
            staging / ATTEMPT_ARTIFACT_DIR_NAME,
            published_dir=out_dir / ATTEMPT_ARTIFACT_DIR_NAME,
            source_artifacts=artifacts,
        )
        if accepted_after < spec.cumulative_target:
            round_cumulative_outputs = None
            if accepted_after in set(FROZEN_INTERMEDIATE_ACCEPTED_BOUNDARIES):
                round_cumulative_outputs = _write_cumulative_inputs(
                    staging / CUMULATIVE_DIR_NAME,
                    published_out_dir=out_dir / CUMULATIVE_DIR_NAME,
                    prior_stage_artifacts=prior_stage_artifacts,
                    prior_records=prior_records,
                    current_artifacts=staged_artifacts,
                )
            progress_path = staging / PROGRESS_RECEIPT_NAME
            progress = _progress_receipt(
                stage=stage,
                attempt_index=attempt_index,
                accepted_before=accepted_before,
                accepted_count=accepted_count,
                raw_count=raw_count,
                funnel=funnel,
                artifact_records=artifact_records,
                prior_records=prior_records,
                backend_sha256=backend_sha256,
                authorization_sha256=authorization_sha256,
                simulator_action_taken=bool(args.simulator_action_taken),
                round_cumulative_inputs=round_cumulative_outputs,
            )
            _write_json(progress_path, progress)
            cumulative_outputs = None
            decision = STAGE_PROGRESS_DECISION
        else:
            cumulative_outputs = _write_cumulative_inputs(
                staging / CUMULATIVE_DIR_NAME,
                published_out_dir=out_dir / CUMULATIVE_DIR_NAME,
                prior_stage_artifacts=prior_stage_artifacts,
                prior_records=prior_records,
                current_artifacts=staged_artifacts,
            )
            progress_path = None
            decision = STAGE_ATTEMPT_TARGET_REACHED_DECISION

        role_receipt = {
            "schema": ROLE_RECEIPT_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "PASS",
            "decision": decision,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_id": PRODUCTION_BACKEND_ID,
            "stage": stage,
            "attempt_index": attempt_index,
            "accepted_before": accepted_before,
            "accepted_this_attempt": accepted_count,
            "accepted_after": accepted_after,
            "cumulative_target": spec.cumulative_target,
            "raw_candidates_this_attempt": raw_count,
            "simulator_action_taken": bool(args.simulator_action_taken),
            "attempt_artifacts": artifact_records,
            "progress_receipt": (
                _published_file_record(
                    progress_path,
                    out_dir / PROGRESS_RECEIPT_NAME,
                )
                if progress_path
                else None
            ),
            "cumulative_stage_inputs": cumulative_outputs,
            "simulator_invoked_by_finalizer": False,
        }
        _write_json(staging / ROLE_RECEIPT_NAME, role_receipt)
        _write_sha256s(staging)
        staging.rename(out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return role_receipt


def _progress_receipt(
    *,
    stage: str,
    attempt_index: int,
    accepted_before: int,
    accepted_count: int,
    raw_count: int,
    funnel: Mapping[str, int],
    artifact_records: Mapping[str, Mapping[str, Any]],
    prior_records: Sequence[tuple[Path, Mapping[str, Any]]],
    backend_sha256: str,
    authorization_sha256: str,
    simulator_action_taken: bool,
    round_cumulative_inputs: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    target = STAGE_BY_NAME[stage].cumulative_target
    accepted_after = accepted_before + accepted_count
    return {
        "schema": STAGE_PROGRESS_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": STAGE_PROGRESS_STATUS,
        "decision": STAGE_PROGRESS_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "attempt_index": attempt_index,
        "cumulative_target": target,
        "accepted_before": accepted_before,
        "accepted_this_attempt": accepted_count,
        "accepted_after": accepted_after,
        "remaining_after": target - accepted_after,
        "raw_candidates_this_attempt": raw_count,
        "terminal_attempts_this_attempt": raw_count,
        "prior_progress_receipt_sha256": (
            _sha256(prior_records[-1][0]) if prior_records else None
        ),
        "backend_identity_manifest_sha256": backend_sha256,
        "full_campaign_authorization_receipt_sha256": authorization_sha256,
        "safeguards": dict(STAGE_PROGRESS_SAFEGUARDS),
        "failure_accounting": dict(funnel),
        "artifacts": {field: dict(record) for field, record in artifact_records.items()},
        "round_cumulative_inputs": (
            {field: dict(record) for field, record in round_cumulative_inputs.items()}
            if round_cumulative_inputs is not None
            else None
        ),
        "simulator_action_taken": simulator_action_taken,
        "stage_pass_receipt_created": False,
        "evidence_preserved": True,
    }


def _write_cumulative_inputs(
    out_dir: Path,
    *,
    published_out_dir: Path,
    prior_stage_artifacts: Mapping[str, Path],
    prior_records: Sequence[tuple[Path, Mapping[str, Any]]],
    current_artifacts: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    out_dir.mkdir()
    sources: dict[str, list[Path]] = {field: [] for field in CSV_ARTIFACT_FIELDS}
    funnels: list[Path] = []
    if prior_stage_artifacts:
        for field in CSV_ARTIFACT_FIELDS:
            sources[field].append(prior_stage_artifacts[field])
        funnels.append(prior_stage_artifacts["failure_funnel"])
    for _, receipt in prior_records:
        records = receipt.get("artifacts")
        if not isinstance(records, Mapping):
            raise StageAttemptFinalizationError("prior progress receipt lacks artifacts")
        for field in CSV_ARTIFACT_FIELDS:
            sources[field].append(Path(str(records[field]["path"])).resolve())
        funnels.append(Path(str(records["failure_funnel"]["path"])).resolve())
    for field in CSV_ARTIFACT_FIELDS:
        sources[field].append(current_artifacts[field])
    funnels.append(current_artifacts["failure_funnel"])

    outputs: dict[str, dict[str, Any]] = {}
    for field, paths in sources.items():
        destination = out_dir / f"{field}.csv"
        _merge_csv(paths, destination)
        outputs[field] = _published_file_record(
            destination,
            published_out_dir / destination.name,
        )
    combined = Counter({field: 0 for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS})
    for path in funnels:
        combined.update(_read_failure_funnel(path))
    funnel_path = out_dir / "failure_funnel.csv"
    with funnel_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage", "count"])
        writer.writeheader()
        for field in ATTEMPT_FAILURE_ACCOUNTING_FIELDS:
            writer.writerow({"stage": field, "count": combined[field]})
    outputs["failure_funnel"] = _published_file_record(
        funnel_path,
        published_out_dir / funnel_path.name,
    )
    return outputs


def _prior_stage_cumulative_artifacts(
    campaign_root: Path,
    *,
    stage: str,
    backend_sha256: str,
    authorization_sha256: str,
) -> dict[str, Path]:
    """Resolve the immediately preceding stage's cumulative raw products."""

    stage_names = [item.name for item in STAGES]
    stage_index = stage_names.index(stage)
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_RECEIPT.json")):
        receipt = _read_json(path, "prior stage receipt")
        if receipt.get("overall_status") == "PASS":
            records.append((path.resolve(), receipt))
    errors = validate_stage_receipt_chain(
        records,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        verify_artifacts=True,
    )
    if errors:
        raise StageAttemptFinalizationError(
            "prior stage receipt chain failed validation: " + "; ".join(errors[:10])
        )
    if len(records) != stage_index:
        raise StageAttemptFinalizationError(
            f"prior stage receipt count mismatch for {stage}: "
            f"actual={len(records)}, expected={stage_index}"
        )
    if stage_index == 0:
        return {}

    _, prior_receipt = records[-1]
    expected_prior = STAGES[stage_index - 1]
    if prior_receipt.get("golden_terminal_mode") == golden_stage.TERMINAL_MODE:
        if stage != "PILOT_32" or prior_receipt.get("accepted_unique_geometries") != 0:
            raise StageAttemptFinalizationError("validation-only Golden precedes only PILOT_32 with zero accepted")
        # Its independently verified evidence stays in the Golden stage, not production tables.
        return {}
    if (
        prior_receipt.get("stage") != expected_prior.name
        or prior_receipt.get("accepted_unique_geometries")
        != expected_prior.cumulative_target
    ):
        raise StageAttemptFinalizationError("immediately preceding stage identity mismatch")
    evidence = prior_receipt.get("artifacts")
    if not isinstance(evidence, Mapping):
        raise StageAttemptFinalizationError("prior stage receipt lacks artifacts")

    resolved = {
        field: _verified_evidence_path(evidence.get(source), f"prior stage {field}")
        for field, source in PRIOR_STAGE_ARTIFACT_FIELDS.items()
    }
    raw_receipt_path = _verified_evidence_path(
        evidence.get("raw_products_receipt"),
        "prior stage raw-products receipt",
    )
    raw_receipt = _read_json(raw_receipt_path, "prior stage raw-products receipt")
    raw_outputs = raw_receipt.get("outputs")
    if not isinstance(raw_outputs, Mapping):
        raise StageAttemptFinalizationError(
            "prior stage raw-products receipt lacks outputs"
        )
    resolved["long_features"] = _verified_evidence_path(
        raw_outputs.get("long_features"),
        "prior stage long features",
    )
    if "stage_execution_trace" in evidence:
        # Published raw-product indexes are projections, not append-compatible
        # attempt tables. Reuse the full inputs bound by the completed execution.
        return _trace_bound_cumulative_inputs(prior_receipt, raw_receipt, resolved)
    return resolved


def _trace_bound_cumulative_inputs(
    stage_receipt: Mapping[str, Any],
    raw_receipt: Mapping[str, Any],
    published: Mapping[str, Path],
) -> dict[str, Path]:
    trace_path = _verified_evidence_path(
        stage_receipt["artifacts"]["stage_execution_trace"], "prior stage execution trace"
    )
    trace = _read_json(trace_path, "prior stage execution trace")
    if (
        trace.get("overall_status") != "PASS"
        or trace.get("campaign_id") != CAMPAIGN_ID
        or trace.get("contract_fingerprint_sha256") != SCIENTIFIC_CONTRACT_FINGERPRINT
        or trace.get("stage") != stage_receipt["stage"]
        or trace.get("all_role_return_codes_zero") is not True
        or trace.get("all_role_receipts_pass") is not True
    ):
        raise StageAttemptFinalizationError("prior cumulative execution trace identity mismatch")
    roles = trace.get("roles")
    if not isinstance(roles, list):
        raise StageAttemptFinalizationError("prior cumulative execution trace lacks roles")
    finalizers = [r for r in roles if isinstance(r, Mapping) and r.get("role") == "stage_attempt_finalizer"]
    if len(finalizers) != 1 or finalizers[0].get("return_code") != 0:
        raise StageAttemptFinalizationError("prior cumulative finalizer role is not unique and successful")
    finalizer_path = _verified_evidence_path(finalizers[0].get("receipt"), "prior cumulative finalizer")
    finalizer = _read_json(finalizer_path, "prior cumulative finalizer")
    if (
        finalizer.get("schema") != ROLE_RECEIPT_SCHEMA
        or finalizer.get("overall_status") != "PASS"
        or finalizer.get("decision") != STAGE_ATTEMPT_TARGET_REACHED_DECISION
        or finalizer.get("campaign_id") != CAMPAIGN_ID
        or finalizer.get("contract_fingerprint_sha256") != SCIENTIFIC_CONTRACT_FINGERPRINT
        or finalizer.get("stage") != stage_receipt["stage"]
        or finalizer.get("accepted_after") != stage_receipt["accepted_unique_geometries"]
    ):
        raise StageAttemptFinalizationError("prior cumulative finalizer identity mismatch")
    records = finalizer.get("cumulative_stage_inputs")
    if not isinstance(records, Mapping) or set(records) != set(STAGE_PROGRESS_ARTIFACT_FIELDS):
        raise StageAttemptFinalizationError("prior cumulative finalizer input schema mismatch")
    resolved = {
        field: _verified_evidence_path(records[field], f"prior full cumulative {field}")
        for field in STAGE_PROGRESS_ARTIFACT_FIELDS
    }
    raw_inputs = raw_receipt.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        raise StageAttemptFinalizationError("prior raw products lack source input bindings")
    for field in ("attempt_ledger", "long_features"):
        source = _verified_evidence_path(raw_inputs.get(field), f"prior raw source {field}")
        if source != resolved[field]:
            raise StageAttemptFinalizationError(f"prior cumulative {field} differs from audited raw source")
    for field in ("attempt_ledger", "long_features", "exact_gds_emx_receipt_index", "failure_funnel"):
        if _sha256(resolved[field]) != _sha256(published[field]):
            raise StageAttemptFinalizationError(f"prior cumulative {field} differs from published evidence")
    tables = {field: _read_csv(resolved[field], field) for field in CSV_ARTIFACT_FIELDS}
    _validate_geometry_identity_sets(tables)
    if len(tables["accepted_geometry_increment"][1]) != stage_receipt["accepted_unique_geometries"]:
        raise StageAttemptFinalizationError("prior cumulative accepted count mismatch")
    # Compare every shared published value; aliases remain distinct, not filled.
    for field in ("accepted_geometry_increment", "rejected_geometry_increment", "s4p_artifact_index"):
        fields, rows = _read_csv(published[field], field)
        full_fields, full_rows = tables[field]
        common = set(fields) & set(full_fields)
        projection = lambda values: Counter(tuple(row[k] for k in sorted(common)) for row in values)
        if projection(rows) != projection(full_rows):
            raise StageAttemptFinalizationError(f"prior cumulative {field} differs from published rows")
    return resolved


def _verified_evidence_path(value: Any, label: str) -> Path:
    if not isinstance(value, Mapping):
        raise StageAttemptFinalizationError(f"{label} evidence is not an object")
    path = _required_file(Path(str(value.get("path") or "")), label)
    if value.get("size_bytes") != path.stat().st_size:
        raise StageAttemptFinalizationError(f"{label} size evidence mismatch")
    if value.get("sha256") != _sha256(path):
        raise StageAttemptFinalizationError(f"{label} SHA-256 evidence mismatch")
    return path


def _stage_attempt_artifacts(
    out_dir: Path,
    *,
    published_dir: Path,
    source_artifacts: Mapping[str, Path],
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    """Preserve each attempt input beneath its immutable no-clobber result."""

    out_dir.mkdir()
    staged: dict[str, Path] = {}
    records: dict[str, dict[str, Any]] = {}
    for field in STAGE_PROGRESS_ARTIFACT_FIELDS:
        source = _required_file(source_artifacts[field], field)
        destination = out_dir / f"{field}.csv"
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        staged[field] = destination
        records[field] = _published_file_record(
            destination,
            published_dir / destination.name,
        )
    return staged, records


def _progress_records(
    campaign_root: Path,
    *,
    stage: str,
) -> list[tuple[Path, dict[str, Any]]]:
    records = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_PROGRESS_RECEIPT.json")):
        value = _read_json(path, "stage progress receipt")
        if str(value.get("stage") or "").upper() == stage:
            records.append((path.resolve(), value))
    return records


def _stage_base_accepted(stage: str) -> int:
    names = [item.name for item in STAGES]
    index = names.index(stage)
    return 0 if index == 0 else STAGES[index - 1].cumulative_target


def _read_csv(path: Path, label: str) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        required = MINIMUM_COLUMNS[label]
        if not required.issubset(fields):
            raise StageAttemptFinalizationError(
                f"{label} lacks columns: {sorted(required - set(fields))}"
            )
        rows = [{key: str(value or "").strip() for key, value in row.items()} for row in reader]
    return fields, rows


def _read_failure_funnel(path: Path) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"stage", "count"}.issubset(reader.fieldnames or []):
            raise StageAttemptFinalizationError("failure_funnel lacks stage,count columns")
        counts: dict[str, int] = {}
        for line, row in enumerate(reader, start=2):
            stage = str(row.get("stage") or "").strip()
            if stage in counts:
                raise StageAttemptFinalizationError(
                    f"failure_funnel repeats stage {stage!r} at line {line}"
                )
            try:
                count = int(str(row.get("count") or "").strip())
            except ValueError as exc:
                raise StageAttemptFinalizationError(
                    f"failure_funnel count is invalid at line {line}"
                ) from exc
            if count < 0:
                raise StageAttemptFinalizationError("failure_funnel count is negative")
            counts[stage] = count
    if set(counts) != set(ATTEMPT_FAILURE_ACCOUNTING_FIELDS):
        raise StageAttemptFinalizationError("failure_funnel fields mismatch")
    return counts


def _validate_failure_funnel(
    funnel: Mapping[str, int],
    *,
    raw_count: int,
    accepted_count: int,
) -> None:
    if funnel["raw_geometry_candidates"] != raw_count:
        raise StageAttemptFinalizationError("failure_funnel raw count mismatch")
    if funnel["accepted_geometries"] != accepted_count:
        raise StageAttemptFinalizationError("failure_funnel accepted count mismatch")
    if sum(
        count for field, count in funnel.items() if field != "raw_geometry_candidates"
    ) != raw_count:
        raise StageAttemptFinalizationError("failure_funnel does not partition raw candidates")


def _validate_geometry_identity_sets(
    tables: Mapping[str, tuple[list[str], list[dict[str, str]]]],
) -> None:
    accepted = [row["geometry_sha256"] for row in tables["accepted_geometry_increment"][1]]
    rejected = [row["geometry_sha256"] for row in tables["rejected_geometry_increment"][1]]
    if len(set(accepted)) != len(accepted):
        raise StageAttemptFinalizationError("accepted increment contains duplicate geometry")
    if set(accepted) & set(rejected):
        raise StageAttemptFinalizationError("accepted and rejected geometry sets overlap")
    for field in ("exact_gds_emx_receipt_index", "s4p_artifact_index"):
        values = [row["geometry_sha256"] for row in tables[field][1]]
        if set(values) != set(accepted) or len(values) != len(accepted):
            raise StageAttemptFinalizationError(f"{field} geometry set mismatch")
    feature_counts = Counter(row["geometry_sha256"] for row in tables["long_features"][1])
    if set(feature_counts) != set(accepted) or any(
        count != len(FREQUENCY_GRID_HZ) for count in feature_counts.values()
    ):
        raise StageAttemptFinalizationError("long-feature geometry grain mismatch")


def _merge_csv(paths: Sequence[Path], destination: Path) -> None:
    fields: list[str] | None = None
    with destination.open("w", newline="", encoding="utf-8") as target:
        writer: csv.DictWriter[str] | None = None
        for path in paths:
            with path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                current = list(reader.fieldnames or [])
                if fields is None:
                    fields = current
                    writer = csv.DictWriter(target, fieldnames=fields)
                    writer.writeheader()
                elif current != fields:
                    raise StageAttemptFinalizationError(
                        f"cumulative CSV header mismatch: {path}"
                    )
                assert writer is not None
                writer.writerows(reader)


def _required_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise StageAttemptFinalizationError(f"{label} is missing or empty: {resolved}")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageAttemptFinalizationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageAttemptFinalizationError(f"{label} is not an object")
    return value


def _published_file_record(staged_path: Path, published_path: Path) -> dict[str, Any]:
    resolved = _required_file(staged_path, "identity file")
    return {
        "path": str(published_path.expanduser().resolve()),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_sha256s(root: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(root)}\n" for path in files),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
