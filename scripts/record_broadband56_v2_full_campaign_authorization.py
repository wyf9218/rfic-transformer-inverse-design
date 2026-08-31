#!/usr/bin/env python3
"""Record the one exact-SHA broadband56 V2 FULL_CAMPAIGN approval.

The recorder is deliberately execution-free.  It validates the public
candidate, recomputes every private preparation/backend identity supplied on
MARS, and writes one no-clobber receipt.  It never starts a queue, Cadence,
Calibre, or EMX.
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
    EXPECTED_FEATURE_ROWS,
    TARGET_ACCEPTED_GEOMETRIES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (  # noqa: E402
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_APPROVAL_SCOPE,
    FULL_CAMPAIGN_FAIL_DECISION,
    FULL_CAMPAIGN_PASS_DECISION,
    FULL_CAMPAIGN_RECEIPT_EFFECT,
    PRODUCTION_BACKEND_ID,
    validate_full_campaign_candidate,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (  # noqa: E402
    BACKEND_VERIFICATION_PASS_DECISION,
    BACKEND_VERIFICATION_PASS_CHECKS,
    BACKEND_VERIFICATION_SCHEMA,
    validate_backend_identity_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
APPROVAL_SOURCE = "EXPLICIT_PROJECT_OWNER_INSTRUCTION"
PLACEHOLDERS = {"", "TBD", "UNKNOWN", "PLACEHOLDER", "NONE"}

PRIVATE_PREPARATION_FILES = {
    "preparation_receipt": "preparation_receipt_sha256",
    "private_configuration": "private_configuration_sha256",
    "campaign_contract_frozen": "campaign_contract_frozen_sha256",
    "primary_bins_frozen": "primary_bins_frozen_sha256",
    "secondary_coverage_frozen": "secondary_coverage_frozen_sha256",
    "geometry_bounds_frozen": "geometry_bounds_frozen_sha256",
    "phase_plan_frozen": "phase_plan_frozen_sha256",
    "operational_policy_approval_receipt": "operational_policy_approval_receipt_sha256",
}


class RecorderError(RuntimeError):
    """Fail-closed recorder error."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_path = Path(args.candidate).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []
    candidate = _read_json(candidate_path, checks, "candidate")
    expected_sha = str(args.candidate_sha256).strip().lower()
    actual_sha = _sha256(candidate_path) if candidate_path.is_file() else None
    checks.extend(
        [
            _check("candidate_sha256_argument", bool(SHA256_PATTERN.fullmatch(expected_sha)), expected_sha),
            _check(
                "candidate_sha256_exact_bytes",
                actual_sha is not None and actual_sha == expected_sha,
                f"expected={expected_sha}, actual={actual_sha}",
            ),
        ]
    )
    candidate_errors = validate_full_campaign_candidate(
        candidate,
        repository_root=ROOT,
    )
    checks.append(
        _check(
            "candidate_static_contract",
            not candidate_errors,
            "PASS" if not candidate_errors else "; ".join(candidate_errors),
        )
    )

    approved_by = str(args.approved_by).strip()
    approved_utc = str(args.approved_utc).strip()
    approval_reference = str(args.approval_reference).strip()
    checks.extend(
        [
            _check(
                "approved_by_explicit",
                approved_by.upper() not in PLACEHOLDERS,
                approved_by or "missing",
            ),
            _check(
                "approved_utc_timezone_aware",
                _aware_datetime(approved_utc),
                approved_utc or "missing",
            ),
            _check(
                "approval_reference_explicit",
                approval_reference.upper() not in PLACEHOLDERS,
                approval_reference or "missing",
            ),
        ]
    )

    private = candidate.get("private_preparation_evidence")
    if not isinstance(private, Mapping):
        private = {}
    private_paths: dict[str, Path] = {}
    for argument_name, candidate_sha_field in PRIVATE_PREPARATION_FILES.items():
        path = Path(getattr(args, argument_name)).expanduser().resolve()
        private_paths[argument_name] = path
        expected = str(private.get(candidate_sha_field) or "").lower()
        actual = _sha256(path) if path.is_file() else None
        checks.extend(
            [
                _check(f"{argument_name}_exists", path.is_file(), str(path)),
                _check(
                    f"{argument_name}_sha256",
                    bool(SHA256_PATTERN.fullmatch(expected)) and actual == expected,
                    f"expected={expected}, actual={actual}",
                ),
            ]
        )

    preparation = _read_json(
        private_paths["preparation_receipt"], checks, "preparation_receipt"
    )
    preparation_checks = preparation.get("checks")
    preparation_passes = (
        sum(1 for item in preparation_checks if isinstance(item, Mapping) and item.get("pass") is True)
        if isinstance(preparation_checks, list)
        else -1
    )
    checks.extend(
        [
            _check("preparation_overall_status", preparation.get("overall_status") == "PASS", preparation.get("overall_status")),
            _check("preparation_decision", preparation.get("decision") == "PREPARED_FOR_GOLDEN_GATE", preparation.get("decision")),
            _check("preparation_campaign", preparation.get("campaign_id") == CAMPAIGN_ID, preparation.get("campaign_id")),
            _check(
                "preparation_contract_fingerprint",
                preparation.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT,
                preparation.get("contract_fingerprint_sha256"),
            ),
            _check("preparation_all_40_checks_pass", preparation_passes == 40, preparation_passes),
            _check(
                "preparation_receipt_size",
                private_paths["preparation_receipt"].is_file()
                and private_paths["preparation_receipt"].stat().st_size
                == private.get("preparation_receipt_size_bytes"),
                private_paths["preparation_receipt"].stat().st_size
                if private_paths["preparation_receipt"].is_file()
                else "missing",
            ),
        ]
    )

    historical_config_sha = _sha256(Path(args.historical_configuration).expanduser().resolve()) if Path(args.historical_configuration).expanduser().resolve().is_file() else None
    checks.extend(
        [
            _check(
                "historical_configuration_exists",
                Path(args.historical_configuration).expanduser().resolve().is_file(),
                str(Path(args.historical_configuration).expanduser().resolve()),
            ),
            _check(
                "historical_configuration_sha256",
                historical_config_sha == private.get("historical_configuration_sha256"),
                historical_config_sha,
            ),
        ]
    )

    backend_manifest_path = Path(args.backend_identity_manifest).expanduser().resolve()
    backend_manifest = _read_json(backend_manifest_path, checks, "backend_identity_manifest")
    runtime = candidate.get("runtime_and_backend_identity")
    if not isinstance(runtime, Mapping):
        runtime = {}
    backend_sha = _sha256(backend_manifest_path) if backend_manifest_path.is_file() else None
    backend_errors = validate_backend_identity_manifest(
        backend_manifest,
        verify_files=True,
    )
    backend_verification_path = (
        Path(args.backend_identity_verification_receipt).expanduser().resolve()
    )
    backend_verification = _read_json(
        backend_verification_path,
        checks,
        "backend_identity_verification_receipt",
    )
    backend_verification_sha = (
        _sha256(backend_verification_path) if backend_verification_path.is_file() else None
    )
    checks.extend(
        [
            _check(
                "backend_identity_manifest_sha256",
                backend_sha == runtime.get("backend_identity_manifest_sha256"),
                backend_sha,
            ),
            _check(
                "backend_manifest_complete",
                not backend_errors,
                "PASS" if not backend_errors else "; ".join(backend_errors[:8]),
            ),
            _check("backend_manifest_campaign", backend_manifest.get("campaign_id") == CAMPAIGN_ID, backend_manifest.get("campaign_id")),
            _check(
                "backend_manifest_fingerprint",
                backend_manifest.get("contract_fingerprint_sha256") == SCIENTIFIC_CONTRACT_FINGERPRINT,
                backend_manifest.get("contract_fingerprint_sha256"),
            ),
            _check("backend_manifest_id", backend_manifest.get("backend_id") == PRODUCTION_BACKEND_ID, backend_manifest.get("backend_id")),
            _check(
                "backend_manifest_candidate_identity",
                _backend_manifest_matches_candidate(backend_manifest, candidate),
                "backend script and PASS-receipt identities",
            ),
            _check(
                "backend_identity_verification_receipt_sha256",
                backend_verification_sha
                == runtime.get("backend_identity_verification_receipt_sha256"),
                backend_verification_sha,
            ),
            _check(
                "backend_identity_verification_receipt_pass",
                backend_verification.get("schema") == BACKEND_VERIFICATION_SCHEMA
                and backend_verification.get("overall_status") == "PASS"
                and backend_verification.get("decision")
                == BACKEND_VERIFICATION_PASS_DECISION
                and backend_verification.get("campaign_id") == CAMPAIGN_ID
                and backend_verification.get("contract_fingerprint_sha256")
                == SCIENTIFIC_CONTRACT_FINGERPRINT
                and backend_verification.get("backend_identity_manifest", {}).get(
                    "sha256"
                )
                == backend_sha
                and backend_verification.get("checks")
                == BACKEND_VERIFICATION_PASS_CHECKS
                and backend_verification.get("errors") == []
                and backend_verification.get("simulator_action_taken") is False,
                backend_verification.get("overall_status"),
            ),
        ]
    )

    passed = bool(checks) and all(item["pass"] for item in checks)
    receipt = {
        "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": FULL_CAMPAIGN_PASS_DECISION if passed else FULL_CAMPAIGN_FAIL_DECISION,
        "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approved_by": approved_by,
        "approved_utc": approved_utc,
        "approval_source": APPROVAL_SOURCE,
        "approval_reference": approval_reference,
        "approved_candidate": {
            "path": str(candidate_path),
            "size_bytes": candidate_path.stat().st_size if candidate_path.is_file() else None,
            "sha256": actual_sha,
        },
        "private_identity_bindings": {
            name: {
                "path": str(path),
                "size_bytes": path.stat().st_size if path.is_file() else None,
                "sha256": _sha256(path) if path.is_file() else None,
            }
            for name, path in private_paths.items()
        },
        "historical_configuration": {
            "path": str(Path(args.historical_configuration).expanduser().resolve()),
            "sha256": historical_config_sha,
        },
        "backend_identity_manifest": {
            "path": str(backend_manifest_path),
            "size_bytes": backend_manifest_path.stat().st_size if backend_manifest_path.is_file() else None,
            "sha256": backend_sha,
            "backend_id": backend_manifest.get("backend_id"),
        },
        "backend_identity_verification_receipt": {
            "path": str(backend_verification_path),
            "size_bytes": (
                backend_verification_path.stat().st_size
                if backend_verification_path.is_file()
                else None
            ),
            "sha256": backend_verification_sha,
        },
        "queue_authorized": passed,
        "supervisor_authorized": passed,
        "automatic_capacity_wait_resume_authorized": passed,
        "automatic_ordered_stage_execution_authorized": passed,
        "cadence_authorized_within_current_stage": passed,
        "calibre_authorized_within_current_stage": passed,
        "emx_authorized_within_current_stage": passed,
        "one_golden_authorized": passed,
        "pilot_32_authorized": passed,
        "pilot_1000_authorized": passed,
        "phase_a_authorized": passed,
        "phase_b_authorized": passed,
        "phase_c_authorized": passed,
        "campaign_200k_authorized": passed,
        "simulator_geometry_limit": TARGET_ACCEPTED_GEOMETRIES if passed else 0,
        "expected_feature_rows": EXPECTED_FEATURE_ROWS if passed else 0,
        "ordered_stages": [stage.name for stage in STAGES],
        "checks": checks,
        "execution_effect": FULL_CAMPAIGN_RECEIPT_EFFECT,
        "authorization_boundary": (
            "A PASS receipt authorizes the exact ordered, receipt-gated, capacity-normalized "
            "broadband56 V2 chain through exactly 200,000 accepted geometry-unique fresh-EMX "
            "S4P designs. The receipt itself performs no queue or simulator action and cannot "
            "change any scientific, foundry, layout, DRC, port, frequency, or provenance field."
        ),
    }
    out_dir.mkdir(parents=True, mode=0o700)
    receipt_path = out_dir / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    _write_json(receipt_path, receipt)
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{_sha256(receipt_path)}  {receipt_path.name}\n", encoding="utf-8")

    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"candidate_sha256={actual_sha}")
    print(f"receipt={receipt_path}")
    return 0 if passed or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-utc", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--private-configuration", required=True)
    parser.add_argument("--historical-configuration", required=True)
    parser.add_argument("--campaign-contract-frozen", required=True)
    parser.add_argument("--primary-bins-frozen", required=True)
    parser.add_argument("--secondary-coverage-frozen", required=True)
    parser.add_argument("--geometry-bounds-frozen", required=True)
    parser.add_argument("--phase-plan-frozen", required=True)
    parser.add_argument("--operational-policy-approval-receipt", required=True)
    parser.add_argument("--backend-identity-manifest", required=True)
    parser.add_argument("--backend-identity-verification-receipt", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _backend_manifest_matches_candidate(
    manifest: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    candidate_runtime = candidate.get("runtime_and_backend_identity")
    candidate_private = candidate.get("private_preparation_evidence")
    if not isinstance(candidate_runtime, Mapping) or not isinstance(candidate_private, Mapping):
        return False
    script_identities = manifest.get("script_identities")
    receipts = manifest.get("historical_backend_pass_receipts")
    if not isinstance(script_identities, Mapping) or not isinstance(receipts, list):
        return False
    expected_scripts = {
        "queue_controller": "queue_controller_sha256",
        "stage_launcher": "stage_launcher_sha256",
        "production_stage_backend": "production_stage_backend_sha256",
        "phase_a_queue_builder": "phase_a_queue_builder_sha256",
        "adaptive_candidate_pool_builder": "adaptive_candidate_pool_builder_sha256",
        "acquisition_ensemble_trainer": "acquisition_ensemble_trainer_sha256",
        "acquisition_predictor": "acquisition_predictor_sha256",
        "adaptive_candidate_selector": "adaptive_candidate_selector_sha256",
        "adaptive_round_stager": "adaptive_round_stager_sha256",
        "cadence_streamout_runner": "cadence_streamout_runner_sha256",
        "calibre_runner": "calibre_runner_sha256",
        "calibre_zero_blocking_receipt_builder": (
            "calibre_zero_blocking_receipt_builder_sha256"
        ),
        "exact_audited_gds_emx_runner": "exact_audited_gds_emx_runner_sha256",
        "exact_audited_gds_emx_module": "exact_audited_gds_emx_module_sha256",
        "full_band_s4p_qa_builder": "full_band_s4p_qa_builder_sha256",
        "full_band_s4p_qa_module": "full_band_s4p_qa_module_sha256",
        "raw_products_finalizer": "raw_products_finalizer_sha256",
        "checkpoint_auditor": "checkpoint_auditor_sha256",
        "campaign_histories_finalizer": "campaign_histories_finalizer_sha256",
        "training_readiness_finalizer": "training_readiness_finalizer_sha256",
        "final_delivery_auditor": "final_delivery_auditor_sha256",
    }
    for manifest_key, candidate_key in expected_scripts.items():
        record = script_identities.get(manifest_key)
        if not isinstance(record, Mapping) or record.get("sha256") != candidate_runtime.get(candidate_key):
            return False
    gds_receipt = manifest.get("historical_gds_identity_pass_receipt")
    if not isinstance(gds_receipt, Mapping) or gds_receipt.get("sha256") != candidate_runtime.get(
        "historical_gds_identity_pass_receipt_sha256"
    ):
        return False
    expected_receipts = candidate_runtime.get("historical_backend_pass_receipts")
    if not isinstance(expected_receipts, list):
        return False
    compact = [
        {
            "overall_status": item.get("overall_status"),
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"),
        }
        for item in receipts
        if isinstance(item, Mapping)
    ]
    preparation_bindings = manifest.get("preparation_bindings")
    if not isinstance(preparation_bindings, Mapping):
        return False
    for field in (
        "preparation_receipt_sha256",
        "private_configuration_sha256",
        "historical_configuration_sha256",
        "operational_policy_approval_receipt_sha256",
    ):
        if preparation_bindings.get(field) != candidate_private.get(field):
            return False
    return compact == expected_receipts


def _read_json(path: Path, checks: list[dict[str, Any]], label: str) -> dict[str, Any]:
    checks.append(_check(f"{label}_exists", path.is_file(), str(path)))
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(f"{label}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{label}_parses", isinstance(value, dict), type(value).__name__))
    return value if isinstance(value, dict) else {}


def _aware_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


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


if __name__ == "__main__":
    raise SystemExit(main())
