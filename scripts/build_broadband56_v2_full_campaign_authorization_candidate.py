#!/usr/bin/env python3
"""Build the one public-safe broadband56 V2 FULL_CAMPAIGN candidate.

This command is execution-free. It accepts only SHA-256 identities and
public repository evidence, validates the complete static authorization
contract, and writes a new no-clobber candidate directory. It has no queue,
Cadence, Calibre, or EMX capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_CANDIDATE_EFFECT,
    FULL_CAMPAIGN_CANDIDATE_SCHEMA,
    FULL_CAMPAIGN_PENDING_STATUS,
    PORT_AND_GROUNDING_CONTRACT,
    PRODUCTION_BACKEND_ID,
    PUBLIC_EVIDENCE_FIELDS,
    UNCHANGED_PHYSICAL_CONTRACT_ITEMS,
    expected_frequency_contract,
    expected_geometry_contract,
    expected_stage_contract,
    expected_terminal_contract,
    validate_full_campaign_candidate,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    BACKEND_VERIFICATION_PASS_CHECKS,
    BACKEND_VERIFICATION_PASS_DECISION,
    BACKEND_VERIFICATION_SCHEMA,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    validate_backend_identity_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CANDIDATE_PREFIX = "BROADBAND56_V2_FULL_CAMPAIGN_AUTHORIZATION_CANDIDATE_"


class CandidateBuildError(RuntimeError):
    """Fail-closed candidate construction error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2
    try:
        result = build_candidate(args, out_dir=out_dir)
    except CandidateBuildError as exc:
        print(f"overall_status=FAIL\nerror={exc}", file=sys.stderr)
        return 2
    print("overall_status=PASS")
    print(f"candidate={result['candidate_path']}")
    print(f"candidate_sha256={result['candidate_sha256']}")
    print("execution_effect=NONE_REQUEST_ONLY")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--generated-utc")
    parser.add_argument("--preparation-receipt-sha256", required=True)
    parser.add_argument("--private-configuration-sha256", required=True)
    parser.add_argument("--historical-configuration-sha256", required=True)
    parser.add_argument("--campaign-contract-frozen-sha256", required=True)
    parser.add_argument("--primary-bins-frozen-sha256", required=True)
    parser.add_argument("--secondary-coverage-frozen-sha256", required=True)
    parser.add_argument("--geometry-bounds-frozen-sha256", required=True)
    parser.add_argument("--phase-plan-frozen-sha256", required=True)
    parser.add_argument("--operational-policy-approval-receipt-sha256", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--backend-identity-manifest-sha256", required=True)
    parser.add_argument("--backend-identity-verification-receipt", required=True)
    parser.add_argument("--backend-identity-verification-receipt-sha256", required=True)
    parser.add_argument("--queue-controller-sha256", required=True)
    parser.add_argument("--resource-gate-auditor-sha256", required=True)
    parser.add_argument("--stage-launcher-sha256", required=True)
    parser.add_argument("--production-stage-backend-sha256", required=True)
    parser.add_argument("--phase-a-queue-builder-sha256", required=True)
    parser.add_argument("--adaptive-checkpoint-materializer-sha256", required=True)
    parser.add_argument("--adaptive-candidate-pool-builder-sha256", required=True)
    parser.add_argument("--acquisition-ensemble-trainer-sha256", required=True)
    parser.add_argument("--acquisition-predictor-sha256", required=True)
    parser.add_argument("--adaptive-candidate-selector-sha256", required=True)
    parser.add_argument("--adaptive-round-stager-sha256", required=True)
    parser.add_argument("--cadence-streamout-runner-sha256", required=True)
    parser.add_argument("--cadence-streamout-delegate-sha256", required=True)
    parser.add_argument("--foundry-layout-audit-producer-sha256", required=True)
    parser.add_argument("--candidate-gds-index-builder-sha256", required=True)
    parser.add_argument("--gds-physical-identity-auditor-sha256", required=True)
    parser.add_argument("--gds-physical-identity-delegate-sha256", required=True)
    parser.add_argument("--gds-physical-identity-module-sha256", required=True)
    parser.add_argument("--calibre-runner-sha256", required=True)
    parser.add_argument("--calibre-batch-delegate-sha256", required=True)
    parser.add_argument("--calibre-zero-blocking-receipt-builder-sha256", required=True)
    parser.add_argument(
        "--calibre-zero-blocking-single-receipt-builder-sha256", required=True
    )
    parser.add_argument("--exact-audited-gds-emx-runner-sha256", required=True)
    parser.add_argument("--exact-audited-gds-emx-single-runner-sha256", required=True)
    parser.add_argument("--exact-audited-gds-emx-module-sha256", required=True)
    parser.add_argument("--full-band-s4p-qa-builder-sha256", required=True)
    parser.add_argument("--full-band-s4p-qa-module-sha256", required=True)
    parser.add_argument("--stage-attempt-product-builder-sha256", required=True)
    parser.add_argument("--stage-attempt-finalizer-sha256", required=True)
    parser.add_argument("--raw-products-finalizer-sha256", required=True)
    parser.add_argument("--checkpoint-auditor-sha256", required=True)
    parser.add_argument("--campaign-histories-finalizer-sha256", required=True)
    parser.add_argument("--training-readiness-finalizer-sha256", required=True)
    parser.add_argument("--checkpoint-figure-renderer-sha256", required=True)
    parser.add_argument("--final-delivery-auditor-sha256", required=True)
    parser.add_argument("--resource-probe-sha256", required=True)
    parser.add_argument("--python-executable-sha256", required=True)
    parser.add_argument("--historical-gds-identity-pass-receipt-sha256", required=True)
    parser.add_argument(
        "--historical-backend-pass-receipt",
        action="append",
        default=[],
        metavar="SHA256:SIZE_BYTES",
        help="Repeat for every historical PASS receipt; at least two are required.",
    )
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        if name.endswith("sha256") and not _is_sha256(value):
            parser.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256 digest")
    return args


def build_candidate(args: argparse.Namespace, *, out_dir: Path) -> dict[str, str]:
    generated_utc = str(args.generated_utc or _utc_now())
    if not _aware_datetime(generated_utc):
        raise CandidateBuildError("--generated-utc must be timezone-aware")
    pass_receipts = _parse_historical_receipts(args.historical_backend_pass_receipt)
    _validate_verified_backend_inputs(args, pass_receipts=pass_receipts)
    public_evidence = _public_evidence()
    candidate = {
        "schema": FULL_CAMPAIGN_CANDIDATE_SCHEMA,
        "generated_utc": generated_utc,
        "campaign_id": CAMPAIGN_ID,
        "scientific_contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approval_status": FULL_CAMPAIGN_PENDING_STATUS,
        "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
        "execution_effect_of_candidate_file": FULL_CAMPAIGN_CANDIDATE_EFFECT,
        "automatic_campaign_execution_authorized": False,
        "frequency_contract": expected_frequency_contract(),
        "terminal_contract": expected_terminal_contract(),
        "geometry_contract": expected_geometry_contract(),
        "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
        "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
        "unchanged_physical_contract_items": list(UNCHANGED_PHYSICAL_CONTRACT_ITEMS),
        "ordered_stages": expected_stage_contract(),
        "stage_transition_contract": {
            "prior_stage_exact_pass_receipt_required": True,
            "no_additional_human_approval_after_full_campaign_pass": True,
            "golden_failure_blocks_later_stages": True,
            "bounded_pending_work_window_required": True,
            "no_clobber_shards_required": True,
            "retry_failed_shards_only": True,
            "exact_200000_completion_required": True,
        },
        "queue_contract": {
            "registration_authorized_before_full_campaign_approval": True,
            "zero_simulator_before_exact_pass_receipt": True,
            "one_authoritative_supervisor": True,
            "survives_terminal_and_browser_disconnect": True,
            "persistent_no_clobber_private_root": True,
            "poll_seconds": 60,
            "resource_shortage_state": "QUEUED_WAITING_FOR_CAPACITY",
            "resource_shortage_is_terminal_blocker": False,
        },
        "label_contract": {
            "final_label_source": "FRESH_REAL_EMX_ONLY",
            "proxy_may_rank_candidates_only": True,
            "proxy_as_label_forbidden": True,
            "historical_label_reuse_forbidden": True,
            "frequency_interpolation_forbidden": True,
            "failed_or_duplicate_geometry_counted_as_accepted": False,
            "cadence_required": True,
            "zero_blocking_calibre_required": True,
            "geometry_to_s4p_hash_chain_required": True,
            "calibre_audited_gds_must_equal_emx_input_bytes": True,
            "cadence_or_gds_regeneration_after_calibre_forbidden": True,
            "exact_audited_gds_emx_receipt_required": True,
        },
        "private_preparation_evidence": {
            "preparation_receipt_sha256": args.preparation_receipt_sha256,
            "preparation_receipt_size_bytes": 10439,
            "preparation_overall_status": "PASS",
            "preparation_decision": "PREPARED_FOR_GOLDEN_GATE",
            "preparation_check_count": 40,
            "preparation_pass_count": 40,
            "preparation_fail_count": 0,
            "private_configuration_sha256": args.private_configuration_sha256,
            "historical_configuration_sha256": args.historical_configuration_sha256,
            "campaign_contract_frozen_sha256": args.campaign_contract_frozen_sha256,
            "primary_bins_frozen_sha256": args.primary_bins_frozen_sha256,
            "secondary_coverage_frozen_sha256": args.secondary_coverage_frozen_sha256,
            "geometry_bounds_frozen_sha256": args.geometry_bounds_frozen_sha256,
            "phase_plan_frozen_sha256": args.phase_plan_frozen_sha256,
            "operational_policy_approval_receipt_sha256": (
                args.operational_policy_approval_receipt_sha256
            ),
            "private_paths_published": False,
        },
        "runtime_and_backend_identity": {
            "backend_id": PRODUCTION_BACKEND_ID,
            "backend_identity_manifest_sha256": args.backend_identity_manifest_sha256,
            "backend_identity_verification_receipt_sha256": (
                args.backend_identity_verification_receipt_sha256
            ),
            "queue_controller_sha256": args.queue_controller_sha256,
            "resource_gate_auditor_sha256": args.resource_gate_auditor_sha256,
            "stage_launcher_sha256": args.stage_launcher_sha256,
            "production_stage_backend_sha256": args.production_stage_backend_sha256,
            "phase_a_queue_builder_sha256": args.phase_a_queue_builder_sha256,
            "adaptive_checkpoint_materializer_sha256": (
                args.adaptive_checkpoint_materializer_sha256
            ),
            "adaptive_candidate_pool_builder_sha256": (
                args.adaptive_candidate_pool_builder_sha256
            ),
            "acquisition_ensemble_trainer_sha256": (
                args.acquisition_ensemble_trainer_sha256
            ),
            "acquisition_predictor_sha256": args.acquisition_predictor_sha256,
            "adaptive_candidate_selector_sha256": (
                args.adaptive_candidate_selector_sha256
            ),
            "adaptive_round_stager_sha256": args.adaptive_round_stager_sha256,
            "cadence_streamout_runner_sha256": args.cadence_streamout_runner_sha256,
            "cadence_streamout_delegate_sha256": (
                args.cadence_streamout_delegate_sha256
            ),
            "foundry_layout_audit_producer_sha256": (
                args.foundry_layout_audit_producer_sha256
            ),
            "candidate_gds_index_builder_sha256": (
                args.candidate_gds_index_builder_sha256
            ),
            "gds_physical_identity_auditor_sha256": (
                args.gds_physical_identity_auditor_sha256
            ),
            "gds_physical_identity_delegate_sha256": (
                args.gds_physical_identity_delegate_sha256
            ),
            "gds_physical_identity_module_sha256": (
                args.gds_physical_identity_module_sha256
            ),
            "resource_policy": RESOURCE_POLICY,
            "operational_policy_approval_scope": POLICY_APPROVAL_SCOPE,
            "calibre_runner_sha256": args.calibre_runner_sha256,
            "calibre_batch_delegate_sha256": args.calibre_batch_delegate_sha256,
            "calibre_zero_blocking_receipt_builder_sha256": (
                args.calibre_zero_blocking_receipt_builder_sha256
            ),
            "calibre_zero_blocking_single_receipt_builder_sha256": (
                args.calibre_zero_blocking_single_receipt_builder_sha256
            ),
            "exact_audited_gds_emx_runner_sha256": (
                args.exact_audited_gds_emx_runner_sha256
            ),
            "exact_audited_gds_emx_single_runner_sha256": (
                args.exact_audited_gds_emx_single_runner_sha256
            ),
            "exact_audited_gds_emx_module_sha256": (
                args.exact_audited_gds_emx_module_sha256
            ),
            "full_band_s4p_qa_builder_sha256": args.full_band_s4p_qa_builder_sha256,
            "full_band_s4p_qa_module_sha256": args.full_band_s4p_qa_module_sha256,
            "stage_attempt_product_builder_sha256": (
                args.stage_attempt_product_builder_sha256
            ),
            "stage_attempt_finalizer_sha256": args.stage_attempt_finalizer_sha256,
            "raw_products_finalizer_sha256": args.raw_products_finalizer_sha256,
            "checkpoint_auditor_sha256": args.checkpoint_auditor_sha256,
            "campaign_histories_finalizer_sha256": (
                args.campaign_histories_finalizer_sha256
            ),
            "training_readiness_finalizer_sha256": (
                args.training_readiness_finalizer_sha256
            ),
            "checkpoint_figure_renderer_sha256": (
                args.checkpoint_figure_renderer_sha256
            ),
            "final_delivery_auditor_sha256": args.final_delivery_auditor_sha256,
            "resource_probe_sha256": args.resource_probe_sha256,
            "python_executable_sha256": args.python_executable_sha256,
            "historical_gds_identity_pass_receipt_sha256": (
                args.historical_gds_identity_pass_receipt_sha256
            ),
            "historical_backend_pass_receipts": pass_receipts,
            "cadence_identity_reverified": True,
            "calibre_zero_blocking_gate_required": True,
            "emx_wrapper_identity_reverified": True,
            "emx_process_identity_reverified": True,
            "full_band_s4p_qa_required": True,
            "private_paths_published": False,
        },
        "public_evidence": public_evidence,
        "approval_request": {
            "required_approval_identity": "Yufeng Wang, project owner and project leader",
            "required_binding": "EXACT_CANDIDATE_SHA256",
            "receipt_recorder": "scripts/record_broadband56_v2_full_campaign_authorization.py",
            "candidate_file_alone_authorizes_execution": False,
        },
    }
    errors = validate_full_campaign_candidate(candidate, repository_root=ROOT)
    if errors:
        raise CandidateBuildError("candidate static validation failed: " + "; ".join(errors))
    out_dir.mkdir(parents=True, mode=0o700)
    stamp = _filename_stamp(generated_utc)
    candidate_path = out_dir / f"{CANDIDATE_PREFIX}{stamp}.json"
    _write_json(candidate_path, candidate)
    digest = _sha256(candidate_path)
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{digest}  {candidate_path.name}\n", encoding="utf-8"
    )
    return {"candidate_path": str(candidate_path), "candidate_sha256": digest}


def _public_evidence() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for label, relative in PUBLIC_EVIDENCE_FIELDS.items():
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise CandidateBuildError(f"public evidence escapes repository: {relative}") from exc
        if not path.is_file() or path.stat().st_size <= 0:
            raise CandidateBuildError(f"public evidence is missing or empty: {relative}")
        records[label] = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return records


def _parse_historical_receipts(values: list[str]) -> list[dict[str, Any]]:
    if len(values) < 2:
        raise CandidateBuildError("at least two historical backend PASS receipts are required")
    records = []
    for value in values:
        try:
            digest, raw_size = str(value).split(":", 1)
            size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise CandidateBuildError(
                "historical receipt must use SHA256:SIZE_BYTES"
            ) from exc
        if not _is_sha256(digest) or size <= 0:
            raise CandidateBuildError("historical receipt identity is invalid")
        records.append({"overall_status": "PASS", "sha256": digest, "size_bytes": size})
    return records


def _validate_verified_backend_inputs(
    args: argparse.Namespace,
    *,
    pass_receipts: list[dict[str, Any]],
) -> None:
    manifest_path = Path(args.backend_identity_manifest).expanduser()
    verification_path = Path(
        args.backend_identity_verification_receipt
    ).expanduser()
    manifest, manifest_identity = _read_stable_json(
        manifest_path,
        label="backend identity manifest",
    )
    verification, verification_identity = _read_stable_json(
        verification_path,
        label="backend identity verification receipt",
    )
    manifest_errors = validate_backend_identity_manifest(
        manifest,
        verify_files=True,
    )
    if manifest_errors:
        raise CandidateBuildError(
            "backend identity manifest failed validation: "
            + "; ".join(manifest_errors[:16])
        )
    if manifest_identity["sha256"] != args.backend_identity_manifest_sha256:
        raise CandidateBuildError("backend identity manifest SHA-256 mismatch")
    if (
        verification_identity["sha256"]
        != args.backend_identity_verification_receipt_sha256
    ):
        raise CandidateBuildError("backend verification receipt SHA-256 mismatch")
    if not (
        verification.get("schema") == BACKEND_VERIFICATION_SCHEMA
        and verification.get("overall_status") == "PASS"
        and verification.get("decision") == BACKEND_VERIFICATION_PASS_DECISION
        and verification.get("campaign_id") == CAMPAIGN_ID
        and verification.get("contract_fingerprint_sha256")
        == SCIENTIFIC_CONTRACT_FINGERPRINT
        and verification.get("checks") == BACKEND_VERIFICATION_PASS_CHECKS
        and verification.get("errors") == []
        and verification.get("simulator_action_taken") is False
        and verification.get("authorization_effect")
        == "NONE_IDENTITY_VERIFICATION_ONLY"
    ):
        raise CandidateBuildError("backend identity verification receipt is not exact PASS")
    verified_manifest = verification.get("backend_identity_manifest")
    if not isinstance(verified_manifest, Mapping) or verified_manifest != manifest_identity:
        raise CandidateBuildError(
            "backend verification receipt does not bind the exact manifest"
        )

    script_identities = manifest.get("script_identities")
    runtime_identities = manifest.get("runtime_identities")
    if not isinstance(script_identities, Mapping) or not isinstance(
        runtime_identities, Mapping
    ):
        raise CandidateBuildError("backend manifest lacks exact identity maps")
    for role in REQUIRED_SCRIPT_ROLES:
        record = script_identities.get(role)
        expected = getattr(args, f"{role}_sha256", None)
        if not isinstance(record, Mapping) or record.get("sha256") != expected:
            raise CandidateBuildError(
                f"candidate {role} SHA-256 differs from the verified manifest"
            )
    runtime_candidate_fields = {
        "resource_probe": "resource_probe_sha256",
        "python_executable": "python_executable_sha256",
    }
    if not set(runtime_candidate_fields).issubset(REQUIRED_RUNTIME_ROLES):
        raise CandidateBuildError("runtime candidate field map is inconsistent")
    for role, field in runtime_candidate_fields.items():
        record = runtime_identities.get(role)
        if not isinstance(record, Mapping) or record.get("sha256") != getattr(
            args, field
        ):
            raise CandidateBuildError(
                f"candidate {role} SHA-256 differs from the verified manifest"
            )

    preparation = manifest.get("preparation_bindings")
    if not isinstance(preparation, Mapping):
        raise CandidateBuildError("backend manifest lacks preparation bindings")
    preparation_fields = (
        "preparation_receipt_sha256",
        "private_configuration_sha256",
        "historical_configuration_sha256",
        "operational_policy_approval_receipt_sha256",
    )
    for field in preparation_fields:
        if preparation.get(field) != getattr(args, field):
            raise CandidateBuildError(
                f"candidate {field} differs from the verified manifest"
            )

    manifest_receipts = manifest.get("historical_backend_pass_receipts")
    if not isinstance(manifest_receipts, list):
        raise CandidateBuildError("backend manifest lacks historical PASS receipts")
    compact_receipts = [
        {
            "overall_status": item.get("overall_status"),
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"),
        }
        for item in manifest_receipts
        if isinstance(item, Mapping)
    ]
    if compact_receipts != pass_receipts:
        raise CandidateBuildError(
            "candidate historical PASS receipts differ from the verified manifest"
        )
    gds_receipt = manifest.get("historical_gds_identity_pass_receipt")
    if not isinstance(gds_receipt, Mapping) or gds_receipt.get("sha256") != (
        args.historical_gds_identity_pass_receipt_sha256
    ):
        raise CandidateBuildError(
            "candidate historical GDS receipt differs from the verified manifest"
        )
    _require_identity_unchanged(
        manifest_identity,
        label="backend identity manifest",
    )
    _require_identity_unchanged(
        verification_identity,
        label="backend identity verification receipt",
    )


def _read_stable_json(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
            raise CandidateBuildError(f"{label} is missing, empty, or a symlink")
        resolved = path.resolve()
        before = resolved.stat()
        payload = resolved.read_bytes()
        after = resolved.stat()
    except OSError as exc:
        raise CandidateBuildError(f"cannot read {label}") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise CandidateBuildError(f"{label} changed while reading")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateBuildError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CandidateBuildError(f"{label} is not a JSON object")
    return value, {
        "path": str(resolved),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _require_identity_unchanged(
    identity: Mapping[str, Any],
    *,
    label: str,
) -> None:
    path = Path(str(identity.get("path", "")))
    try:
        if (
            not path.is_file()
            or path.stat().st_size != identity.get("size_bytes")
            or _sha256(path) != identity.get("sha256")
        ):
            raise CandidateBuildError(f"{label} changed during candidate construction")
    except OSError as exc:
        raise CandidateBuildError(f"cannot recheck {label}") from exc


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def _aware_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _filename_stamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
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
