#!/usr/bin/env python3
"""Execute one authorized broadband56 stage through hash-bound role scripts.

This is the simulator-capable backend invoked by the stage launcher.  It can
run only the exact role order stored inside the SHA-bound private production
configuration.  Every role uses ``shell=False`` and must return a PASS receipt.
The backend independently revalidates cumulative raw products and the formal
checkpoint before it writes a stage receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
    next_frozen_accepted_boundary,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    CapacityPolicyError,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGE_BY_NAME,
    adaptive_concurrency,
    evaluate_capacity_snapshot,
    stage_for_progress,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_PASS_DECISION,
    PORT_AND_GROUNDING_CONTRACT,
    PRODUCTION_BACKEND_ID,
    expected_frequency_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    FAILURE_ACCOUNTING_FIELDS,
    STAGE_GATE_FIELDS,
    STAGE_RECEIPT_SCHEMA,
    stage_artifact_fields,
    validate_backend_identity_manifest,
    validate_stage_receipt,
    validate_stage_receipt_chain,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (  # noqa: E402
    PROFILE_EXECUTION_MODE,
    StageExecutionProfileError,
    expand_argument,
    expected_stage_role_order,
    read_execution_profile,
    resolve_under,
    validate_execution_profile,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_progress import (  # noqa: E402
    STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA,
    STAGE_ATTEMPT_TARGET_REACHED_DECISION,
    STAGE_PROGRESS_ARTIFACT_FIELDS,
    STAGE_PROGRESS_DECISION,
    accepted_after_progress,
    validate_stage_progress_chain,
    validate_stage_progress_receipt,
)
from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden_stage


TRACE_SCHEMA = "rfic_transformer.broadband56_v2_stage_execution_trace.v1"
RESOURCE_SUMMARY_SCHEMA = "rfic_transformer.broadband56_v2_stage_resource_summary.v1"
FAILURE_SCHEMA = "rfic_transformer.broadband56_v2_stage_backend_failure.v1"


class ProductionStageBackendError(RuntimeError):
    """Fail-closed production-stage error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.backend_out_dir).expanduser().resolve()
    if out_dir.exists():
        print(
            f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}",
            file=sys.stderr,
        )
        return 2
    completed_roles: list[dict[str, Any]] = []
    stage = str(args.stage).upper()
    try:
        result = run_stage_backend(
            args,
            out_dir=out_dir,
            completed_roles=completed_roles,
        )
    except (
        CapacityPolicyError,
        OSError,
        ProductionStageBackendError,
        StageExecutionProfileError,
        subprocess.SubprocessError,
    ) as exc:
        if out_dir.is_dir():
            _write_failure(
                out_dir,
                stage=stage,
                error=str(exc),
                completed_roles=completed_roles,
            )
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    if result.get("decision") == STAGE_PROGRESS_DECISION:
        print("overall_status=INCOMPLETE")
        print(f"stage={result['stage']}")
        print(f"accepted_after={result['accepted_after']}")
        print(f"receipt={out_dir / 'STAGE_PROGRESS_RECEIPT.json'}")
    else:
        print("overall_status=PASS")
        print(f"stage={result['stage']}")
        print(f"accepted_unique_geometries={result['accepted_unique_geometries']}")
        print(f"receipt={out_dir / 'STAGE_RECEIPT.json'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--cumulative-target", type=int, required=True)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--backend-out-dir", required=True)
    parser.add_argument("--full-campaign-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--resource-snapshot", required=True)
    parser.add_argument("--max-concurrency", type=int, required=True)
    return parser.parse_args(argv)


def run_stage_backend(
    args: argparse.Namespace,
    *,
    out_dir: Path,
    completed_roles: list[dict[str, Any]],
) -> dict[str, Any]:
    stage = str(args.stage).upper()
    spec = STAGE_BY_NAME.get(stage)
    if spec is None:
        raise ProductionStageBackendError(f"unknown stage: {stage}")
    if int(args.cumulative_target) != spec.cumulative_target:
        raise ProductionStageBackendError("stage cumulative target mismatch")
    if int(args.max_concurrency) < 1:
        raise ProductionStageBackendError("max concurrency must be positive")

    campaign_root = Path(args.campaign_root).expanduser().resolve()
    authorization_path = Path(args.full_campaign_receipt).expanduser().resolve()
    backend_manifest_path = Path(args.backend_identity_manifest).expanduser().resolve()
    snapshot_path = Path(args.resource_snapshot).expanduser().resolve()
    if not campaign_root.is_dir():
        raise ProductionStageBackendError(f"campaign root is missing: {campaign_root}")
    authorization = _read_json(authorization_path, "full-campaign receipt")
    backend_manifest = _read_json(backend_manifest_path, "backend identity manifest")
    snapshot = _read_json(snapshot_path, "resource snapshot")
    backend_sha256 = _sha256(backend_manifest_path)
    authorization_sha256 = _sha256(authorization_path)

    _validate_authorization(authorization, backend_sha256=backend_sha256)
    backend_errors = validate_backend_identity_manifest(
        backend_manifest,
        verify_files=True,
    )
    if backend_errors:
        raise ProductionStageBackendError(
            "backend identity manifest failed validation: "
            + "; ".join(backend_errors[:10])
        )
    _validate_self_identity(backend_manifest)

    prior_records = _ordered_stage_receipt_records(campaign_root)
    chain_errors = validate_stage_receipt_chain(
        prior_records,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        verify_artifacts=True,
    )
    if chain_errors:
        raise ProductionStageBackendError(
            "prior stage receipt chain failed validation: "
            + "; ".join(chain_errors[:10])
        )
    prior_receipts = [receipt for _, receipt in prior_records]
    base_accepted = (
        int(prior_receipts[-1]["accepted_unique_geometries"])
        if prior_receipts
        else 0
    )
    next_stage = stage_for_progress(
        current_accepted=base_accepted,
        stage_receipts=prior_receipts,
    )
    if next_stage != stage:
        raise ProductionStageBackendError(
            f"requested stage {stage} is out of order; next legal stage is {next_stage}"
        )
    progress_records = _ordered_stage_progress_receipt_records(
        campaign_root,
        stage=stage,
    )
    progress_errors = validate_stage_progress_chain(
        progress_records,
        stage=stage,
        base_accepted=base_accepted,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        verify_artifacts=True,
    )
    if progress_errors:
        raise ProductionStageBackendError(
            "prior stage progress chain failed validation: "
            + "; ".join(progress_errors[:10])
        )
    current_accepted = accepted_after_progress(
        progress_records,
        base_accepted=base_accepted,
    )

    capacity = evaluate_capacity_snapshot(
        snapshot,
        stage=stage,
        current_accepted=current_accepted,
        measured_pilot_bytes_per_geometry=_pilot_bytes_per_geometry(campaign_root),
    )
    if not capacity["pass"]:
        raise ProductionStageBackendError(
            "resource snapshot is WAIT: " + ",".join(capacity["failed_checks"])
        )
    from rfic_transformer_inverse_design.campaigns.broadband56_scheduling import concurrency_for_snapshot

    allowed = concurrency_for_snapshot(
        snapshot_path=snapshot_path, campaign_root=campaign_root,
        stage=stage, current_accepted=current_accepted,
        policy=capacity, legacy_policy=adaptive_concurrency,
        measured_pilot_bytes_per_geometry=_pilot_bytes_per_geometry(campaign_root),
        pilot_1000_safe_concurrency=_pilot_safe_concurrency(campaign_root),
    )
    max_concurrency = int(args.max_concurrency)
    if max_concurrency > int(allowed["concurrency"]):
        raise ProductionStageBackendError(
            f"requested concurrency {max_concurrency} exceeds allowed {allowed['concurrency']}"
        )

    runtime_identities = backend_manifest.get("runtime_identities", {})
    private_config_record = runtime_identities.get("private_configuration")
    if not isinstance(private_config_record, Mapping):
        raise ProductionStageBackendError("backend manifest lacks private configuration")
    private_config_path = Path(str(private_config_record.get("path") or "")).resolve()
    if _sha256(private_config_path) != private_config_record.get("sha256"):
        raise ProductionStageBackendError("private configuration identity drifted")
    profile_record = runtime_identities.get("stage_execution_profile")
    if not isinstance(profile_record, Mapping):
        raise ProductionStageBackendError("backend manifest lacks stage execution profile")
    profile_path = Path(str(profile_record.get("path") or "")).resolve()
    if _sha256(profile_path) != profile_record.get("sha256"):
        raise ProductionStageBackendError("stage execution profile identity drifted")
    profile = read_execution_profile(profile_path)
    profile_errors = validate_execution_profile(
        profile,
        backend_manifest=backend_manifest,
    )
    if profile_errors:
        raise ProductionStageBackendError(
            "private stage profile failed validation: "
            + "; ".join(profile_errors[:12])
        )

    out_dir.mkdir(parents=True, mode=0o700)
    start_monotonic = time.monotonic()
    started_utc = _utc_now()
    prior_path = prior_records[-1][0] if prior_records else None
    frozen_accepted_target = next_frozen_accepted_boundary(
        current_accepted,
        cumulative_target=spec.cumulative_target,
    )
    selection_accepted_target = frozen_accepted_target
    attempt_limit = profile["stages"][stage].get("max_candidates_per_attempt")
    if attempt_limit is not None:
        selection_accepted_target = min(selection_accepted_target, current_accepted + attempt_limit)
    selection_count = selection_accepted_target - current_accepted
    context_path = out_dir / "STAGE_CONTEXT.json"
    context = {
        "schema": "rfic_transformer.broadband56_v2_stage_context.v2",
        "generated_utc": started_utc,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "cumulative_target": spec.cumulative_target,
        "current_accepted": current_accepted,
        "stage_remaining_accepted": spec.cumulative_target - current_accepted,
        "frozen_accepted_target": frozen_accepted_target,
        "remaining_to_frozen_accepted_boundary": frozen_accepted_target - current_accepted,
        "selection_accepted_target": selection_accepted_target,
        "max_candidates_per_attempt": attempt_limit,
        "selection_count_mode": "FROZEN_QUEUE_CEILING" if attempt_limit else "EXACT_SAMPLER_COUNT",
        "remaining_accepted": selection_count,
        "max_concurrency": max_concurrency,
        "scheduling_decision": allowed,
        "backend_identity_manifest": _file_record(backend_manifest_path),
        "full_campaign_authorization_receipt": _file_record(authorization_path),
        "resource_snapshot": _file_record(snapshot_path),
        "private_configuration": _file_record(private_config_path),
        "stage_execution_profile": _file_record(profile_path),
        "prior_stage_receipt": _file_record(prior_path) if prior_path else None,
        "execution_mode": PROFILE_EXECUTION_MODE,
        "shell_used": False,
    }
    _write_json(context_path, context)

    stage_profile = profile["stages"][stage]
    commands = stage_profile["commands"]
    expected_roles = expected_stage_role_order(stage)
    if len(commands) != len(expected_roles):
        raise ProductionStageBackendError(
            "stage command count differs from the exact role order"
        )
    progress_source_path: Path | None = None
    progress_receipt: dict[str, Any] | None = None
    golden_finalizer_record: dict[str, Any] | None = None
    frozen_selection: dict[str, Any] | None = None
    dispatched_selection_count = selection_count
    for index, (role, command_profile) in enumerate(
        zip(expected_roles, commands),
        start=1,
    ):
        role_out_dir = out_dir / "roles" / f"{index:02d}_{role}"
        role_log_dir = out_dir / "role_logs" / f"{index:02d}_{role}"
        role_log_dir.mkdir(parents=True, mode=0o700)
        substitutions = {
            "{stage}": stage,
            "{cumulative_target}": str(spec.cumulative_target),
            "{campaign_root}": str(campaign_root),
            "{backend_out_dir}": str(out_dir),
            "{role_out_dir}": str(role_out_dir),
            "{full_campaign_receipt}": str(authorization_path),
            "{backend_identity_manifest}": str(backend_manifest_path),
            "{resource_snapshot}": str(snapshot_path),
            "{max_concurrency}": str(max_concurrency),
            "{prior_stage_receipt}": str(prior_path or ""),
            "{current_accepted}": str(current_accepted),
            "{remaining_accepted}": str(dispatched_selection_count),
            "{private_configuration}": str(private_config_path),
        }
        role_identity = backend_manifest["script_identities"][role]
        role_path = Path(str(role_identity["path"])).resolve()
        if _sha256(role_path) != role_identity["sha256"]:
            raise ProductionStageBackendError(f"{role} identity drifted before execution")
        role_args = [
            expand_argument(argument, substitutions)
            for argument in command_profile["argv"]
        ]
        command = [sys.executable, str(role_path), *role_args]
        command_digest = hashlib.sha256(
            json.dumps(command, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        role_started = _utc_now()
        role_start = time.monotonic()
        with (role_log_dir / "stdout.log").open("w", encoding="utf-8") as stdout, (
            role_log_dir / "stderr.log"
        ).open("w", encoding="utf-8") as stderr:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
                shell=False,
                env={
                    **os.environ,
                    "BROADBAND56_STAGE_CONTEXT": str(context_path),
                    "BROADBAND56_MAX_CONCURRENCY": str(max_concurrency),
                },
            )
        if _sha256(role_path) != role_identity["sha256"]:
            raise ProductionStageBackendError(f"{role} identity drifted during execution")
        receipt_path = resolve_under(
            role_out_dir,
            str(command_profile["receipt"]),
            label=f"{role} receipt",
        )
        role_record = {
            "role": role,
            "started_utc": role_started,
            "finished_utc": _utc_now(),
            "duration_seconds": round(time.monotonic() - role_start, 6),
            "script_identity": _file_record(role_path),
            "command_argv_sha256": command_digest,
            "shell_used": False,
            "return_code": int(result.returncode),
            "stdout": _file_record(role_log_dir / "stdout.log"),
            "stderr": _file_record(role_log_dir / "stderr.log"),
        }
        completed_roles.append(role_record)
        if result.returncode != 0:
            raise ProductionStageBackendError(
                f"{role} exited with return code {result.returncode}"
            )
        role_receipt = _read_json(receipt_path, f"{role} receipt")
        _validate_role_receipt(role_receipt, role=role, stage=stage)
        role_record["receipt"] = _file_record(receipt_path)
        role_record["simulator_action_taken"] = bool(
            role_receipt.get("simulator_action_taken", False)
        )
        if role == "phase_a_queue_builder" and attempt_limit is not None:
            from rfic_transformer_inverse_design.campaigns.broadband56_frozen_queue_batches import validate_frozen_selection

            try:
                frozen_selection = validate_frozen_selection(
                    receipt_path,
                    source_receipt_path=Path(role_args[role_args.index("--frozen-queue-receipt") + 1]),
                    source_receipt_sha256=role_args[role_args.index("--frozen-queue-receipt-sha256") + 1],
                    candidate_ceiling=selection_count,
                    fingerprint=SCIENTIFIC_CONTRACT_FINGERPRINT,
                )
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                raise ProductionStageBackendError(f"frozen selection before Cadence: {exc}") from exc
            dispatched_selection_count = frozen_selection["actual_selected_candidates"]
            role_record["frozen_selection"] = frozen_selection
        if role == "stage_attempt_finalizer":
            decision, candidate_progress_path, candidate_progress = (
                _validate_stage_attempt_finalizer_receipt(
                    role_receipt,
                    role_out_dir=role_out_dir,
                    backend_out_dir=out_dir,
                    stage=stage,
                    current_accepted=current_accepted,
                    cumulative_target=spec.cumulative_target,
                    progress_records=progress_records,
                    backend_manifest_sha256=backend_sha256,
                    authorization_receipt_sha256=authorization_sha256,
                )
            )
            if decision == STAGE_PROGRESS_DECISION:
                progress_source_path = candidate_progress_path
                progress_receipt = candidate_progress
                break
            if decision == golden_stage.FINALIZER_DECISION:
                if stage_profile.get("golden_terminal_mode") != golden_stage.TERMINAL_MODE:
                    raise ProductionStageBackendError("profile lacks the explicit Golden validation-only terminal")
                golden_finalizer_record = _file_record(receipt_path)
                break

    trace_path = out_dir / "STAGE_EXECUTION_TRACE.json"
    trace = {
        "schema": TRACE_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "INCOMPLETE" if progress_receipt else "PASS",
        "decision": (
            STAGE_PROGRESS_DECISION if progress_receipt else (
                golden_stage.FINALIZER_DECISION if golden_finalizer_record else "COMPLETE_STAGE_ROLE_CHAIN"
            )
        ),
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "role_order": [item["role"] for item in completed_roles],
        "expected_terminal_role_order": (
            list(expected_roles[:expected_roles.index("stage_attempt_finalizer") + 1])
            if golden_finalizer_record else list(expected_roles)
        ),
        "roles": completed_roles,
        "frozen_selection": frozen_selection,
        "all_role_return_codes_zero": all(
            item["return_code"] == 0 for item in completed_roles
        ),
        "all_role_receipts_pass": True,
        "shell_used": False,
    }
    _write_json(trace_path, trace)

    resource_summary_path = out_dir / "RESOURCE_SUMMARY.json"
    resource_summary = {
        "schema": RESOURCE_SUMMARY_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "stage": stage,
        "started_utc": started_utc,
        "duration_seconds": round(time.monotonic() - start_monotonic, 6),
        "max_concurrency": max_concurrency,
        "role_count": len(completed_roles),
        "capacity_decision": capacity["decision"],
        "capacity_metrics": capacity["metrics"],
        "resource_snapshot": _file_record(snapshot_path),
        "simulator_action_taken": any(
            item.get("simulator_action_taken") is True for item in completed_roles
        ),
    }
    _write_json(resource_summary_path, resource_summary)

    if progress_receipt is not None:
        if progress_source_path is None:
            raise ProductionStageBackendError("progress receipt path is missing")
        progress_path = out_dir / "STAGE_PROGRESS_RECEIPT.json"
        if progress_path.exists():
            raise ProductionStageBackendError("backend progress receipt path already exists")
        shutil.copyfile(progress_source_path, progress_path)
        if _sha256(progress_path) != _sha256(progress_source_path):
            raise ProductionStageBackendError("copied progress receipt identity drifted")
        (out_dir / "SHA256SUMS.txt").write_text(
            "\n".join(
                f"{_sha256(path)}  {path.name}"
                for path in (
                    context_path,
                    trace_path,
                    resource_summary_path,
                    progress_path,
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return progress_receipt

    if golden_finalizer_record is not None:
        receipt = _build_golden_validation_stage_receipt(
            golden_finalizer_record, context_path=context_path, trace_path=trace_path,
            resource_summary_path=resource_summary_path,
            backend_sha256=backend_sha256, authorization_sha256=authorization_sha256,
        )
    else:
        result_paths = {
            field: resolve_under(out_dir, str(relative), label=f"result_paths.{field}")
            for field, relative in stage_profile["result_paths"].items()
        }
        receipt = _build_stage_receipt(
            stage=stage, cumulative_target=spec.cumulative_target,
            backend_manifest_sha256=backend_sha256,
            authorization_receipt_sha256=authorization_sha256,
            prior_stage_receipt_sha256=_sha256(prior_path) if prior_path else None,
            out_dir=out_dir, result_paths=result_paths,
            trace_path=trace_path, resource_summary_path=resource_summary_path,
        )
    receipt_errors = validate_stage_receipt(
        receipt,
        stage=stage,
        cumulative_target=spec.cumulative_target,
        backend_manifest_sha256=backend_sha256,
        authorization_receipt_sha256=authorization_sha256,
        prior_stage_receipt_sha256=_sha256(prior_path) if prior_path else None,
        verify_artifacts=True,
        artifact_root=out_dir,
    )
    if receipt_errors:
        raise ProductionStageBackendError(
            "constructed stage receipt failed validation: "
            + "; ".join(receipt_errors[:12])
        )
    receipt_path = out_dir / "STAGE_RECEIPT.json"
    _write_json(receipt_path, receipt)
    (out_dir / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{_sha256(path)}  {path.name}"
            for path in (
                context_path,
                trace_path,
                resource_summary_path,
                receipt_path,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return receipt


def _validate_stage_attempt_finalizer_receipt(
    receipt: Mapping[str, Any],
    *,
    role_out_dir: Path,
    backend_out_dir: Path,
    stage: str,
    current_accepted: int,
    cumulative_target: int,
    progress_records: list[tuple[Path, dict[str, Any]]],
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
) -> tuple[str, Path | None, dict[str, Any] | None]:
    if receipt.get("schema") != STAGE_ATTEMPT_FINALIZER_RECEIPT_SCHEMA:
        raise ProductionStageBackendError("stage-attempt finalizer schema mismatch")
    if _nonnegative_int(
        receipt.get("accepted_before"), "stage_attempt.accepted_before"
    ) != current_accepted:
        raise ProductionStageBackendError(
            "stage-attempt finalizer accepted_before mismatch"
        )
    accepted_after = _nonnegative_int(
        receipt.get("accepted_after"), "stage_attempt.accepted_after"
    )
    if _nonnegative_int(
        receipt.get("cumulative_target"), "stage_attempt.cumulative_target"
    ) != cumulative_target:
        raise ProductionStageBackendError(
            "stage-attempt finalizer cumulative target mismatch"
        )
    if receipt.get("simulator_invoked_by_finalizer") is not False:
        raise ProductionStageBackendError(
            "stage-attempt finalizer must not invoke a simulator"
        )

    decision = receipt.get("decision")
    if decision == golden_stage.FINALIZER_DECISION:
        if stage != "GOLDEN" or progress_records or current_accepted != 0 or accepted_after != 0:
            raise ProductionStageBackendError("validation-only finalizer is not the initial Golden")
        _evidence_path(receipt.get("golden_attempt_products_receipt"),
                       label="Golden attempt products", artifact_root=backend_out_dir)
        try:
            golden_stage.validate_finalizer(receipt, backend_sha256=backend_manifest_sha256,
                                            authorization_sha256=authorization_receipt_sha256)
        except (golden_stage.GoldenSourceError, OSError, ValueError, TypeError, KeyError) as exc:
            raise ProductionStageBackendError(f"Golden finalizer evidence failed: {exc}") from exc
        return decision, None, None
    if decision == STAGE_PROGRESS_DECISION:
        if accepted_after >= cumulative_target:
            raise ProductionStageBackendError(
                "nonterminal progress is not strictly below the stage target"
            )
        progress_path = _evidence_path(
            receipt.get("progress_receipt"),
            label="stage progress receipt",
            artifact_root=backend_out_dir,
        )
        progress = _read_json(progress_path, "stage progress receipt")
        errors = validate_stage_progress_receipt(
            progress,
            stage=stage,
            attempt_index=len(progress_records) + 1,
            accepted_before=current_accepted,
            prior_progress_receipt_sha256=(
                _sha256(progress_records[-1][0]) if progress_records else None
            ),
            backend_manifest_sha256=backend_manifest_sha256,
            authorization_receipt_sha256=authorization_receipt_sha256,
            verify_artifacts=True,
            artifact_root=progress_path.parent,
        )
        if errors:
            raise ProductionStageBackendError(
                "stage progress receipt failed validation: "
                + "; ".join(errors[:10])
            )
        if receipt.get("cumulative_stage_inputs") is not None:
            raise ProductionStageBackendError(
                "nonterminal attempt unexpectedly exposed cumulative inputs"
            )
        return STAGE_PROGRESS_DECISION, progress_path, progress

    if decision != STAGE_ATTEMPT_TARGET_REACHED_DECISION:
        raise ProductionStageBackendError(
            "stage-attempt finalizer decision is not recognized"
        )
    if accepted_after != cumulative_target:
        raise ProductionStageBackendError(
            "terminal attempt does not close exactly to the stage target"
        )
    if receipt.get("progress_receipt") is not None:
        raise ProductionStageBackendError(
            "terminal attempt unexpectedly exposed a progress receipt"
        )
    cumulative = receipt.get("cumulative_stage_inputs")
    if not isinstance(cumulative, Mapping) or set(cumulative) != set(
        STAGE_PROGRESS_ARTIFACT_FIELDS
    ):
        raise ProductionStageBackendError(
            "terminal attempt cumulative input fields mismatch"
        )
    for field in STAGE_PROGRESS_ARTIFACT_FIELDS:
        _evidence_path(
            cumulative.get(field),
            label=f"cumulative stage input {field}",
            artifact_root=role_out_dir,
        )
    return STAGE_ATTEMPT_TARGET_REACHED_DECISION, None, None


def _build_golden_validation_stage_receipt(
    finalizer_record: Mapping[str, Any], *, context_path: Path, trace_path: Path,
    resource_summary_path: Path, backend_sha256: str, authorization_sha256: str,
) -> dict[str, Any]:
    finalizer = _read_json(Path(finalizer_record["path"]), "Golden finalizer")
    attempt = golden_stage.validate_finalizer(finalizer, backend_sha256=backend_sha256,
                                              authorization_sha256=authorization_sha256)
    return {
        "schema": STAGE_RECEIPT_SCHEMA, "generated_utc": _utc_now(), "overall_status": "PASS",
        "decision": "ACCEPT_STAGE", "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID, "stage": "GOLDEN",
        "terminal_state": STAGE_BY_NAME["GOLDEN"].receipt_status, "cumulative_target": 1,
        "accepted_unique_geometries": 0, "production_accepted_count_delta": 0,
        "golden_terminal_mode": golden_stage.TERMINAL_MODE,
        "validation_geometry_count": 1, "validation_feature_rows": 56,
        "golden_validation": attempt["golden_validation"],
        "backend_identity_manifest_sha256": backend_sha256,
        "full_campaign_authorization_receipt_sha256": authorization_sha256,
        "prior_stage_receipt_sha256": None, "frequency_contract": expected_frequency_contract(),
        "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT, "label_source": "FRESH_REAL_EMX_ONLY",
        "counts": {key: 0 for key in (
            "accepted_unique_geometries", "valid_s4p_geometries", "feature_complete_geometries",
            "s4p_artifacts", "independent_designs", "geometry_frequency_rows",
            "broadband_descriptor_valid_rows", "strict_lumped_valid_rows",
        )},
        "safeguards": {key: 0 for key in (
            "proxy_label_count", "historical_label_count", "interpolated_frequency_record_count",
            "accepted_duplicate_geometry_count", "accepted_blocking_calibre_count",
            "manual_gds_modification_count", "mixed_contract_fingerprint_count",
        )},
        "gates": {key: True for key in STAGE_GATE_FIELDS},
        "failure_accounting": attempt["failure_accounting"],
        "artifacts": {
            "golden_attempt_products_receipt": finalizer["golden_attempt_products_receipt"],
            "stage_attempt_finalizer_receipt": dict(finalizer_record),
            "stage_execution_trace": _file_record(trace_path),
            "resource_summary": _file_record(resource_summary_path),
            "stage_context": _file_record(context_path),
            "accepted_geometry_index": attempt["accepted_geometry_increment"],
            "rejected_geometry_index": attempt["rejected_geometry_increment"],
            "validation_geometry": attempt["validation_products"]["validation_geometry"],
        },
    }


def _build_stage_receipt(
    *,
    stage: str,
    cumulative_target: int,
    backend_manifest_sha256: str,
    authorization_receipt_sha256: str,
    prior_stage_receipt_sha256: str | None,
    out_dir: Path,
    result_paths: Mapping[str, Path],
    trace_path: Path,
    resource_summary_path: Path,
) -> dict[str, Any]:
    raw_path = result_paths["raw_products_receipt"]
    checkpoint_path = result_paths["checkpoint_receipt"]
    raw = _read_json(raw_path, "raw-products receipt")
    checkpoint = _read_json(checkpoint_path, "checkpoint receipt")
    raw_counts = _mapping(raw.get("counts"), "raw-products counts")
    funnel = _mapping(raw.get("failure_funnel"), "raw-products failure funnel")
    raw_inputs = _mapping(raw.get("inputs"), "raw-products inputs")
    raw_outputs = _mapping(raw.get("outputs"), "raw-products outputs")
    checkpoint_outputs = _mapping(checkpoint.get("outputs"), "checkpoint outputs")
    coverage_path = _evidence_path(
        checkpoint_outputs.get("coverage_summary"),
        label="checkpoint coverage summary",
        artifact_root=out_dir,
    )
    coverage = _read_json(coverage_path, "coverage summary")
    validity = _mapping(coverage.get("validity_counts"), "coverage validity counts")
    expected_rows = cumulative_target * 56

    artifacts: dict[str, dict[str, Any]] = {
        "stage_execution_trace": _file_record(trace_path),
        "attempt_ledger": _file_record(
            _evidence_path(
                raw_inputs.get("attempt_ledger"),
                label="attempt ledger",
                artifact_root=out_dir,
            )
        ),
        "accepted_geometry_index": _file_record(
            _evidence_path(
                raw_outputs.get("accepted_geometries"),
                label="accepted geometries",
                artifact_root=out_dir,
            )
        ),
        "rejected_geometry_index": _file_record(
            _evidence_path(
                raw_outputs.get("rejected_geometries"),
                label="rejected geometries",
                artifact_root=out_dir,
            )
        ),
        "s4p_artifact_index": _file_record(
            _evidence_path(
                raw_outputs.get("artifact_index"),
                label="S4P artifact index",
                artifact_root=out_dir,
            )
        ),
        "broadband_features_manifest": _file_record(
            _evidence_path(
                raw_outputs.get("long_features_manifest"),
                label="long-feature manifest",
                artifact_root=out_dir,
            )
        ),
        "failure_funnel": _file_record(
            _evidence_path(
                raw_outputs.get("failure_funnel"),
                label="failure funnel",
                artifact_root=out_dir,
            )
        ),
        "exact_gds_emx_receipt_index": _file_record(
            _require_file_under(
                result_paths["exact_gds_emx_receipt_index"],
                out_dir,
                "exact GDS/EMX receipt index",
            )
        ),
        "raw_products_receipt": _file_record(
            _require_file_under(raw_path, out_dir, "raw-products receipt")
        ),
        "checkpoint_receipt": _file_record(
            _require_file_under(checkpoint_path, out_dir, "checkpoint receipt")
        ),
        "checkpoint_sha256s": _file_record(
            _require_file_under(
                checkpoint_path.parent / "SHA256SUMS.txt",
                out_dir,
                "checkpoint SHA256SUMS",
            )
        ),
        "checkpoint_status": _file_record(
            _evidence_path(
                checkpoint_outputs.get("checkpoint_status"),
                label="checkpoint status",
                artifact_root=out_dir,
            )
        ),
        "coverage_summary": _file_record(coverage_path),
        "resource_summary": _file_record(resource_summary_path),
    }
    terminal_mapping = {
        "campaign_history_receipt": "campaign_history_receipt",
        "training_readiness_receipt": "training_readiness_receipt",
        "checkpoint_figure_receipt": "checkpoint_figure_receipt",
        "final_delivery_receipt": "final_delivery_receipt",
    }
    for artifact_role, result_role in terminal_mapping.items():
        if result_role in result_paths:
            artifacts[artifact_role] = _file_record(
                _require_file_under(
                    result_paths[result_role],
                    out_dir,
                    artifact_role,
                )
            )
    expected_artifacts = set(stage_artifact_fields(stage))
    if set(artifacts) != expected_artifacts:
        raise ProductionStageBackendError(
            f"stage artifact fields mismatch: {sorted(set(artifacts) ^ expected_artifacts)}"
        )

    failure_accounting = {
        field: _nonnegative_int(funnel.get(field), f"failure_funnel.{field}")
        for field in FAILURE_ACCOUNTING_FIELDS
    }
    counts = {
        "accepted_unique_geometries": _nonnegative_int(
            raw_counts.get("accepted_geometries"),
            "raw_counts.accepted_geometries",
        ),
        "valid_s4p_geometries": _nonnegative_int(
            raw_counts.get("accepted_s4p_geometries"),
            "raw_counts.accepted_s4p_geometries",
        ),
        "feature_complete_geometries": _nonnegative_int(
            raw_counts.get("accepted_feature_complete_geometries"),
            "raw_counts.accepted_feature_complete_geometries",
        ),
        "s4p_artifacts": _nonnegative_int(
            raw_counts.get("s4p_artifacts"),
            "raw_counts.s4p_artifacts",
        ),
        "independent_designs": _nonnegative_int(
            raw_counts.get("independent_designs"),
            "raw_counts.independent_designs",
        ),
        "geometry_frequency_rows": _nonnegative_int(
            raw_counts.get("geometry_frequency_rows"),
            "raw_counts.geometry_frequency_rows",
        ),
        "broadband_descriptor_valid_rows": _nonnegative_int(
            validity.get("broadband_descriptor_valid"),
            "validity_counts.broadband_descriptor_valid",
        ),
        "strict_lumped_valid_rows": _nonnegative_int(
            validity.get("strict_lumped_valid"),
            "validity_counts.strict_lumped_valid",
        ),
    }
    if counts["geometry_frequency_rows"] != expected_rows:
        raise ProductionStageBackendError("raw-products geometry-frequency count mismatch")
    if _nonnegative_int(
        validity.get("parseable_rows"), "validity_counts.parseable_rows"
    ) != expected_rows:
        raise ProductionStageBackendError("checkpoint parseable row count mismatch")

    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "generated_utc": _utc_now(),
        "overall_status": "PASS",
        "decision": "ACCEPT_STAGE",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "terminal_state": STAGE_BY_NAME[stage].receipt_status,
        "cumulative_target": cumulative_target,
        "accepted_unique_geometries": cumulative_target,
        "backend_identity_manifest_sha256": backend_manifest_sha256,
        "full_campaign_authorization_receipt_sha256": authorization_receipt_sha256,
        "prior_stage_receipt_sha256": prior_stage_receipt_sha256,
        "frequency_contract": expected_frequency_contract(),
        "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
        "label_source": "FRESH_REAL_EMX_ONLY",
        "counts": counts,
        "safeguards": {
            "proxy_label_count": 0,
            "historical_label_count": 0,
            "interpolated_frequency_record_count": 0,
            "accepted_duplicate_geometry_count": 0,
            "accepted_blocking_calibre_count": 0,
            "manual_gds_modification_count": 0,
            "mixed_contract_fingerprint_count": 0,
        },
        "gates": {field: True for field in STAGE_GATE_FIELDS},
        "failure_accounting": failure_accounting,
        "artifacts": artifacts,
    }


def _validate_authorization(receipt: Mapping[str, Any], *, backend_sha256: str) -> None:
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
        == backend_sha256
        and receipt.get("accepted_geometry_target") == TARGET_ACCEPTED_GEOMETRIES
        and receipt.get("attempt_replenishment_contract")
        == ATTEMPT_REPLENISHMENT_CONTRACT
        and "simulator_geometry_limit" not in receipt
        and all(receipt.get(field) is True for field in required_true)
    ):
        raise ProductionStageBackendError(
            "FULL_CAMPAIGN receipt identity or permission mismatch"
        )


def _validate_self_identity(backend_manifest: Mapping[str, Any]) -> None:
    identity = backend_manifest.get("script_identities", {}).get(
        "production_stage_backend"
    )
    if not isinstance(identity, Mapping):
        raise ProductionStageBackendError("production backend identity is missing")
    self_path = Path(__file__).resolve()
    if not (
        Path(str(identity.get("path") or "")).resolve() == self_path
        and identity.get("sha256") == _sha256(self_path)
    ):
        raise ProductionStageBackendError("production backend self-identity mismatch")


def _validate_role_receipt(
    receipt: Mapping[str, Any],
    *,
    role: str,
    stage: str,
) -> None:
    if receipt.get("overall_status") != "PASS":
        raise ProductionStageBackendError(f"{role} receipt is not PASS")
    if "campaign_id" in receipt and receipt.get("campaign_id") != CAMPAIGN_ID:
        raise ProductionStageBackendError(f"{role} receipt campaign mismatch")
    if (
        "contract_fingerprint_sha256" in receipt
        and receipt.get("contract_fingerprint_sha256")
        != SCIENTIFIC_CONTRACT_FINGERPRINT
    ):
        raise ProductionStageBackendError(f"{role} receipt contract mismatch")
    if "stage" in receipt and str(receipt.get("stage")).upper() != stage:
        raise ProductionStageBackendError(f"{role} receipt stage mismatch")


def _evidence_path(
    value: Any,
    *,
    label: str,
    artifact_root: Path,
) -> Path:
    if not isinstance(value, Mapping):
        raise ProductionStageBackendError(f"{label} evidence is not an object")
    path = _require_file_under(
        Path(str(value.get("path") or "")), artifact_root, label
    )
    if value.get("size_bytes") != path.stat().st_size:
        raise ProductionStageBackendError(f"{label} size evidence mismatch")
    if value.get("sha256") != _sha256(path):
        raise ProductionStageBackendError(f"{label} SHA-256 evidence mismatch")
    return path


def _require_file_under(path: Path, root: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    resolved_root = Path(root).expanduser().resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ProductionStageBackendError(f"{label} escapes backend output root") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ProductionStageBackendError(f"{label} is missing or empty: {resolved}")
    return resolved


def _ordered_stage_receipt_records(
    campaign_root: Path,
) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((campaign_root / "stages").glob("*/STAGE_RECEIPT.json")):
        receipt = _read_json(path, "prior stage receipt")
        if receipt.get("overall_status") == "PASS":
            records.append((path, receipt))
    return records


def _ordered_stage_progress_receipt_records(
    campaign_root: Path,
    *,
    stage: str,
) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(
        (campaign_root / "stages").glob("*/STAGE_PROGRESS_RECEIPT.json")
    ):
        receipt = _read_json(path, "prior stage progress receipt")
        if str(receipt.get("stage") or "").upper() == stage:
            records.append((path, receipt))
    return records


def _pilot_bytes_per_geometry(campaign_root: Path) -> float | None:
    path = campaign_root / "PILOT_1000_RESOURCE_SUMMARY.json"
    if not path.is_file():
        return None
    value = _read_json(path, "pilot resource summary").get("bytes_per_geometry")
    return float(value) if value is not None else None


def _pilot_safe_concurrency(campaign_root: Path) -> int | None:
    path = campaign_root / "PILOT_1000_RESOURCE_SUMMARY.json"
    if not path.is_file():
        return None
    value = _read_json(path, "pilot resource summary").get("safe_concurrency")
    return int(value) if value is not None else None


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionStageBackendError(f"{label} must be an object")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProductionStageBackendError(f"{label} must be a nonnegative integer")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionStageBackendError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ProductionStageBackendError(f"{label} is not a JSON object")
    return value


def _file_record(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise ProductionStageBackendError("cannot record a missing file")
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ProductionStageBackendError(f"identity file is missing: {resolved}")
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


def _write_failure(
    out_dir: Path,
    *,
    stage: str,
    error: str,
    completed_roles: list[dict[str, Any]],
) -> None:
    failure_path = out_dir / "BACKEND_FAILURE.json"
    if failure_path.exists():
        return
    _write_json(
        failure_path,
        {
            "schema": FAILURE_SCHEMA,
            "generated_utc": _utc_now(),
            "overall_status": "FAIL",
            "decision": "DO_NOT_ACCEPT_STAGE",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "stage": stage,
            "error": error,
            "completed_role_count": len(completed_roles),
            "completed_roles": completed_roles,
            "simulator_action_may_have_occurred": any(
                item.get("simulator_action_taken") is True
                for item in completed_roles
            ),
            "stage_receipt_created": False,
            "evidence_preserved": True,
        },
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
