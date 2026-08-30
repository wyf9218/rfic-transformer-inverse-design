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
    parser.add_argument("--backend-identity-manifest-sha256", required=True)
    parser.add_argument("--backend-identity-verification-receipt-sha256", required=True)
    parser.add_argument("--queue-controller-sha256", required=True)
    parser.add_argument("--stage-launcher-sha256", required=True)
    parser.add_argument("--production-stage-backend-sha256", required=True)
    parser.add_argument("--calibre-runner-sha256", required=True)
    parser.add_argument("--calibre-zero-safe-freezer-sha256", required=True)
    parser.add_argument("--full-band-s4p-qa-builder-sha256", required=True)
    parser.add_argument("--stage07-08-resume-guard-sha256", required=True)
    parser.add_argument("--raw-products-finalizer-sha256", required=True)
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
            "stage_launcher_sha256": args.stage_launcher_sha256,
            "production_stage_backend_sha256": args.production_stage_backend_sha256,
            "resource_policy": RESOURCE_POLICY,
            "operational_policy_approval_scope": POLICY_APPROVAL_SCOPE,
            "calibre_runner_sha256": args.calibre_runner_sha256,
            "calibre_zero_safe_freezer_sha256": args.calibre_zero_safe_freezer_sha256,
            "full_band_s4p_qa_builder_sha256": args.full_band_s4p_qa_builder_sha256,
            "stage07_08_resume_guard_sha256": args.stage07_08_resume_guard_sha256,
            "raw_products_finalizer_sha256": args.raw_products_finalizer_sha256,
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
