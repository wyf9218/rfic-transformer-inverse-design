#!/usr/bin/env python3
"""Record an explicit one-golden approval without running any remote action.

This is an audit recorder, not an authorization source. It validates the exact
public candidate and its locally available preparation evidence, then writes a
no-clobber receipt. It cannot query resources or licenses and never invokes
MARS, Cadence, Calibre, EMX, a queue, a runner, or a supervisor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SCHEMA = "rfic_transformer.broadband56_v2_golden_authorization_candidate.v1"
APPROVAL_SCHEMA = "rfic_transformer.broadband56_v2_golden_authorization.v1"
CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
APPROVAL_SCOPE = "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY"
PASS_DECISION = APPROVAL_SCOPE
FAIL_DECISION = "DO_NOT_AUTHORIZE_RESOURCE_LICENSE_GATE_OR_GOLDEN"
APPROVAL_SOURCE = "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION"
PENDING_STATUS = "PENDING_EXPLICIT_PROJECT_OWNER_SHA256_APPROVAL"
STOP_CONDITION = "STOP_AFTER_ONE_GOLDEN_RECEIPT_REGARDLESS_OF_PASS_OR_FAIL"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PLACEHOLDERS = {"", "TBD", "UNKNOWN", "PLACEHOLDER"}
EXPECTED_PUBLIC_EVIDENCE = {
    "r2_candidate_path": "r2_candidate_sha256",
    "r2_approval_receipt_path": "r2_approval_receipt_sha256",
    "public_preparation_status_path": "public_preparation_status_sha256",
}
PERMITTED_KEYS = {
    "read_only_resource_and_load_gate",
    "read_only_license_availability_gate",
    "create_one_no_clobber_golden_run_root",
    "prepare_exactly_one_deterministic_golden_geometry_from_frozen_bounds",
    "run_analytical_and_topology_gates_for_that_geometry",
    "run_cadence_for_that_geometry",
    "run_calibre_drc_for_that_geometry",
    "run_emx_for_that_geometry",
    "parse_and_audit_one_exact_56_point_four_port_s4p",
    "write_one_no_clobber_golden_audit_and_receipt",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_path = Path(args.candidate).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    _require_new_output_directory(out_dir)

    checks: list[dict[str, Any]] = []
    candidate = _read_json(candidate_path, checks, "candidate")
    actual_sha256 = _sha256(candidate_path) if candidate_path.is_file() else None
    expected_sha256 = str(args.candidate_sha256).strip().lower()
    approved_by = str(args.approved_by).strip()
    approved_utc = str(args.approved_utc).strip()
    approval_reference = str(args.approval_reference).strip()

    checks.extend(
        [
            _check("candidate_sha256_argument_is_valid", bool(SHA256_PATTERN.fullmatch(expected_sha256)), expected_sha256),
            _check(
                "candidate_sha256_matches_exact_bytes",
                actual_sha256 is not None and actual_sha256 == expected_sha256,
                f"expected={expected_sha256}, actual={actual_sha256}",
            ),
            _check("candidate_schema", candidate.get("schema") == CANDIDATE_SCHEMA, candidate.get("schema")),
            _check("candidate_campaign_id", candidate.get("campaign_id") == CAMPAIGN_ID, candidate.get("campaign_id")),
            _check(
                "candidate_contract_fingerprint_is_sha256",
                bool(SHA256_PATTERN.fullmatch(str(candidate.get("contract_fingerprint_sha256") or ""))),
                candidate.get("contract_fingerprint_sha256"),
            ),
            _check("candidate_pending_approval", candidate.get("approval_status") == PENDING_STATUS, candidate.get("approval_status")),
            _check("candidate_scope", candidate.get("authorization_scope") == APPROVAL_SCOPE, candidate.get("authorization_scope")),
            _check(
                "candidate_automatic_campaign_execution_forbidden",
                candidate.get("automatic_campaign_execution_authorized") is False,
                candidate.get("automatic_campaign_execution_authorized"),
            ),
            _check("candidate_pilot_execution_forbidden", candidate.get("pilot_execution_authorized") is False, candidate.get("pilot_execution_authorized")),
            _check("candidate_phase_execution_forbidden", candidate.get("phase_execution_authorized") is False, candidate.get("phase_execution_authorized")),
            _check("candidate_stop_condition", candidate.get("stop_condition") == STOP_CONDITION, candidate.get("stop_condition")),
            _check(
                "candidate_has_no_execution_effect",
                candidate.get("execution_effect_of_this_candidate_file") == "NONE_REQUEST_ONLY",
                candidate.get("execution_effect_of_this_candidate_file"),
            ),
            _check("candidate_frequency_contract", _frequency_contract_is_exact(candidate), candidate.get("frozen_frequency_contract")),
            _check("candidate_preparation_evidence", _preparation_evidence_is_exact(candidate), candidate.get("preparation_evidence")),
            _check("candidate_permitted_action_set", _permitted_action_set_is_exact(candidate), candidate.get("permitted_only_after_exact_sha256_approval")),
            _check("candidate_golden_contract", _golden_contract_is_exact(candidate), candidate.get("golden_acceptance_contract")),
            _check("candidate_public_evidence_hashes", _public_evidence_hashes_match(candidate), "recomputed from repository files"),
            _check("approved_by_is_explicit", bool(approved_by) and approved_by.upper() not in PLACEHOLDERS, approved_by or "missing"),
            _check("approved_utc_is_timezone_aware", _is_timezone_aware_iso8601(approved_utc), approved_utc or "missing"),
            _check(
                "approval_reference_is_explicit",
                bool(approval_reference) and approval_reference.upper() not in PLACEHOLDERS,
                approval_reference or "missing",
            ),
        ]
    )

    passed = bool(checks) and all(item["pass"] for item in checks)
    receipt = {
        "schema": APPROVAL_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": PASS_DECISION if passed else FAIL_DECISION,
        "approved_by": approved_by,
        "approved_utc": approved_utc,
        "approval_source": APPROVAL_SOURCE,
        "approval_reference": approval_reference,
        "approved_candidate": {
            "path": str(candidate_path),
            "size_bytes": candidate_path.stat().st_size if candidate_path.is_file() else None,
            "sha256": actual_sha256,
            "campaign_id": candidate.get("campaign_id"),
            "contract_fingerprint_sha256": candidate.get("contract_fingerprint_sha256"),
        },
        "resource_and_license_gate_authorized": passed,
        "one_golden_authorized": passed,
        "cadence_authorized_for_one_golden": passed,
        "calibre_authorized_for_one_golden": passed,
        "emx_authorized_for_one_golden": passed,
        "simulator_geometry_limit": 1 if passed else 0,
        "pilot_32_authorized": False,
        "pilot_1000_authorized": False,
        "queue_authorized": False,
        "supervisor_authorized": False,
        "phase_a_authorized": False,
        "phase_b_authorized": False,
        "phase_c_authorized": False,
        "campaign_200k_authorized": False,
        "checks": checks,
        "execution_effect": "NONE_RECORD_ONLY",
        "authorization_boundary": (
            "A PASS receipt authorizes fresh resource/load/license gates and, only when all are PASS, "
            "one exact-contract golden geometry. Execution must stop after the golden receipt. It does "
            "not authorize either pilot, a queue, a supervisor, any campaign phase, or the 200K campaign."
        ),
    }

    out_dir.mkdir(parents=True)
    receipt_path = out_dir / "GOLDEN_AUTHORIZATION_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{_sha256(receipt_path)}  {receipt_path.name}\n",
        encoding="utf-8",
    )

    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"receipt={receipt_path}")
    return 0 if passed or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approved-utc", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _require_new_output_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"no-clobber output already exists: {path}")


def _read_json(path: Path, checks: list[dict[str, Any]], label: str) -> dict[str, Any]:
    checks.append(_check(f"{label}_exists", path.is_file(), str(path)))
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(f"{label}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{label}_parses", isinstance(payload, dict), type(payload).__name__))
    return payload if isinstance(payload, dict) else {}


def _frequency_contract_is_exact(candidate: dict[str, Any]) -> bool:
    contract = candidate.get("frozen_frequency_contract")
    if not isinstance(contract, dict):
        return False
    return contract == {
        "start_ghz": 5,
        "stop_ghz": 60,
        "step_ghz": 1,
        "points": 56,
        "ports": 4,
        "touchstone_extension": ".s4p",
    }


def _preparation_evidence_is_exact(candidate: dict[str, Any]) -> bool:
    evidence = candidate.get("preparation_evidence")
    if not isinstance(evidence, dict):
        return False
    sha_fields = [
        "r2_candidate_sha256",
        "r2_approval_receipt_sha256",
        "public_preparation_status_sha256",
        "private_preparation_receipt_sha256",
        "private_configuration_sha256",
        "historical_configuration_sha256",
    ]
    return (
        all(SHA256_PATTERN.fullmatch(str(evidence.get(field) or "")) for field in sha_fields)
        and evidence.get("preparation_status") == "PASS"
        and evidence.get("preparation_checks_passed") == 40
        and evidence.get("preparation_checks_failed") == 0
        and evidence.get("private_preparation_receipt_size_bytes") == 10439
    )


def _permitted_action_set_is_exact(candidate: dict[str, Any]) -> bool:
    permitted = candidate.get("permitted_only_after_exact_sha256_approval")
    return isinstance(permitted, dict) and set(permitted) == PERMITTED_KEYS and all(
        permitted.get(key) is True for key in PERMITTED_KEYS
    )


def _golden_contract_is_exact(candidate: dict[str, Any]) -> bool:
    contract = candidate.get("golden_acceptance_contract")
    if not isinstance(contract, dict):
        return False
    required_true = {
        "analytical_geometry_pass_required",
        "topology_pass_required",
        "cadence_gds_required",
        "zero_blocking_calibre_violations_required",
        "fresh_real_emx_required",
        "parseable_four_port_touchstone_required",
        "exact_frequency_vector_required",
        "complete_s_and_z_matrices_required",
        "finite_feature_rows_required",
        "candidate_gds_calibre_emx_s4p_hash_chain_required",
    }
    return (
        contract.get("expected_unique_geometries") == 1
        and contract.get("audit_mode") == "golden"
        and contract.get("terminal_state") == "GOLDEN_COMPLETE"
        and all(contract.get(key) is True for key in required_true)
    )


def _public_evidence_hashes_match(candidate: dict[str, Any]) -> bool:
    evidence = candidate.get("preparation_evidence")
    if not isinstance(evidence, dict):
        return False
    for path_key, sha_key in EXPECTED_PUBLIC_EVIDENCE.items():
        relative = evidence.get(path_key)
        expected_sha = str(evidence.get(sha_key) or "")
        if not isinstance(relative, str) or not SHA256_PATTERN.fullmatch(expected_sha):
            return False
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            return False
        if not path.is_file() or _sha256(path) != expected_sha:
            return False
    return True


def _is_timezone_aware_iso8601(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
