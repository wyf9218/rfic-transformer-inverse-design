#!/usr/bin/env python3
"""Record explicit approval of the broadband56 capacity policy amendment.

The recorder has no execution capability.  It validates the exact public
candidate and writes a no-clobber, hash-indexed receipt for downstream gates.
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

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (  # noqa: E402
    POLICY_APPROVAL_SCHEMA,
    POLICY_APPROVAL_SCOPE,
    POLICY_CANDIDATE_SCHEMA,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    CAMPAIGN_ID,
    TARGET_ACCEPTED_GEOMETRIES,
)


ROOT = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
APPROVAL_SOURCE = "EXPLICIT_PROJECT_OWNER_INSTRUCTION"
PASS_DECISION = POLICY_APPROVAL_SCOPE
FAIL_DECISION = "DO_NOT_AUTHORIZE_CAPACITY_POLICY_OR_SIMULATOR_ACTION"
PERMISSION_FIELDS = (
    "one_golden_authorized",
    "pilot_32_authorized",
    "pilot_1000_authorized",
    "queue_authorized",
    "supervisor_authorized",
    "phase_a_authorized",
    "phase_b_authorized",
    "phase_c_authorized",
    "campaign_200k_authorized",
)
PUBLIC_EVIDENCE_FIELDS = {
    "contract_path": "contract_sha256",
    "r2_candidate_path": "r2_candidate_sha256",
}
PRIOR_EVIDENCE_FIELDS = {
    "public_r2_preparation_status_path": "public_r2_preparation_status_sha256",
    "historical_one_golden_status_path": "historical_one_golden_status_sha256",
    "historical_absolute_load_wait_status_path": (
        "historical_absolute_load_wait_status_sha256"
    ),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    candidate_path = Path(args.candidate).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"overall_status=FAIL\nerror=no-clobber output exists: {out_dir}", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []
    candidate = _read_json(candidate_path, checks)
    expected_sha = str(args.candidate_sha256).strip().lower()
    actual_sha = _sha256(candidate_path) if candidate_path.is_file() else None
    approved_by = str(args.approved_by).strip()
    approved_utc = str(args.approved_utc).strip()
    approval_reference = str(args.approval_reference).strip()
    checks.extend(
        [
            _check("candidate_sha_argument", bool(SHA256_PATTERN.fullmatch(expected_sha)), expected_sha),
            _check("candidate_sha_exact", actual_sha == expected_sha, f"expected={expected_sha}, actual={actual_sha}"),
            _check("candidate_schema", candidate.get("schema") == POLICY_CANDIDATE_SCHEMA, candidate.get("schema")),
            _check("candidate_campaign", candidate.get("campaign_id") == CAMPAIGN_ID, candidate.get("campaign_id")),
            _check(
                "candidate_scientific_fingerprint",
                candidate.get("scientific_contract_fingerprint_sha256")
                == SCIENTIFIC_CONTRACT_FINGERPRINT,
                candidate.get("scientific_contract_fingerprint_sha256"),
            ),
            _check("candidate_resource_policy", candidate.get("resource_policy") == RESOURCE_POLICY, candidate.get("resource_policy")),
            _check("candidate_scope", candidate.get("authorization_scope") == POLICY_APPROVAL_SCOPE, candidate.get("authorization_scope")),
            _check("scientific_change_forbidden", candidate.get("scientific_contract_change_authorized") is False, candidate.get("scientific_contract_change_authorized")),
            _check("operational_change_only", candidate.get("operational_policy_change_only") is True, candidate.get("operational_policy_change_only")),
            _check("frequency_contract_exact", _frequency_contract_exact(candidate), candidate.get("frequency_contract")),
            _check("terminal_contract_exact", _terminal_contract_exact(candidate), candidate.get("terminal_contract")),
            _check("capacity_gate_exact", _capacity_gate_exact(candidate), candidate.get("capacity_gate")),
            _check("ordered_stages_exact", _ordered_stages_exact(candidate), candidate.get("ordered_stages")),
            _check("stage_transition_contract_exact", _stage_transition_exact(candidate), candidate.get("stage_transition_contract")),
            _check("adaptive_concurrency_exact", _adaptive_concurrency_exact(candidate), candidate.get("adaptive_concurrency")),
            _check("golden_contract_exact", _golden_contract_exact(candidate), candidate.get("golden_acceptance_contract")),
            _check("authorized_actions_exact", _authorized_actions_exact(candidate), candidate.get("authorized_actions_after_pass_receipt")),
            _check("candidate_no_execution_effect", candidate.get("execution_effect_of_candidate_file") == "NONE_POLICY_DESCRIPTION_ONLY", candidate.get("execution_effect_of_candidate_file")),
            _check("public_contract_evidence", _evidence_hashes_match(candidate.get("public_contract_evidence"), PUBLIC_EVIDENCE_FIELDS), "repository bytes"),
            _check("prior_receipts_preserved", _prior_receipts_exact(candidate), "repository bytes"),
            _check("approved_by_explicit", bool(approved_by), approved_by or "missing"),
            _check("approved_utc_timezone_aware", _aware_datetime(approved_utc), approved_utc or "missing"),
            _check("approval_reference_explicit", bool(approval_reference), approval_reference or "missing"),
        ]
    )
    passed = bool(checks) and all(item["pass"] for item in checks)
    receipt = {
        "schema": POLICY_APPROVAL_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS" if passed else "FAIL",
        "decision": PASS_DECISION if passed else FAIL_DECISION,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "authorization_scope": "FULL_CAMPAIGN",
        "approved_by": approved_by,
        "approved_utc": approved_utc,
        "approval_source": APPROVAL_SOURCE,
        "approval_reference": approval_reference,
        "approved_candidate": {
            "path": str(candidate_path),
            "size_bytes": candidate_path.stat().st_size if candidate_path.is_file() else None,
            "sha256": actual_sha,
            "campaign_id": candidate.get("campaign_id"),
            "contract_fingerprint_sha256": candidate.get(
                "scientific_contract_fingerprint_sha256"
            ),
            "resource_policy": candidate.get("resource_policy"),
        },
        "fresh_capacity_inspection_authorized": passed,
        "automatic_capacity_wait_resume_authorized": passed,
        "cadence_authorized_within_current_stage": passed,
        "calibre_authorized_within_current_stage": passed,
        "emx_authorized_within_current_stage": passed,
        "simulator_geometry_limit": TARGET_ACCEPTED_GEOMETRIES if passed else 0,
        **{field: passed for field in PERMISSION_FIELDS},
        "checks": checks,
        "execution_effect": "NONE_RECORD_ONLY",
        "authorization_boundary": (
            "A PASS receipt authorizes only ordered, receipt-gated, capacity-normalized "
            "execution of the frozen broadband56 V2 scientific contract. It does not alter "
            "the process, geometry, ports, frequency grid, physical equations, acceptance "
            "rules, or exact 200K terminal target."
        ),
    }
    out_dir.mkdir(parents=True)
    receipt_path = out_dir / "OPERATIONAL_POLICY_APPROVAL_RECEIPT.json"
    _write_json(receipt_path, receipt)
    (out_dir / "SHA256SUMS.txt").write_text(
        f"{_sha256(receipt_path)}  {receipt_path.name}\n", encoding="utf-8"
    )
    print(f"overall_status={receipt['overall_status']}")
    print(f"decision={receipt['decision']}")
    print(f"policy_sha256={actual_sha}")
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


def _frequency_contract_exact(candidate: Mapping[str, Any]) -> bool:
    return candidate.get("frequency_contract") == {
        "start_ghz": 5,
        "stop_ghz": 60,
        "step_ghz": 1,
        "points": 56,
        "ports": 4,
        "touchstone_extension": ".s4p",
    }


def _terminal_contract_exact(candidate: Mapping[str, Any]) -> bool:
    return candidate.get("terminal_contract") == {
        "accepted_unique_geometries": 200000,
        "s4p_artifacts": 200000,
        "geometry_frequency_rows": 11200000,
        "label_source": "FRESH_REAL_EMX_ONLY",
    }


def _capacity_gate_exact(candidate: Mapping[str, Any]) -> bool:
    gate = candidate.get("capacity_gate")
    return isinstance(gate, Mapping) and gate == {
        "normalized_load1_max": 0.9,
        "normalized_load5_max": 0.95,
        "available_memory_fraction_min": 0.2,
        "iowait_percent_max": 10,
        "swap_sample_interval_seconds_min": 60,
        "active_swap_thrashing_allowed": False,
        "cadence_license_required": True,
        "calibre_license_required": True,
        "emx_license_required": True,
        "duplicate_authoritative_supervisor_allowed": False,
        "duplicate_runner_allowed": False,
        "output_path_collision_allowed": False,
        "golden_minimum_free_storage_bytes": 10737418240,
        "later_stage_storage_formula": "ceil(measured_pilot_bytes_per_geometry * remaining_geometries * 1.25)",
        "raw_load1_absolute_limit": None,
        "raw_load1_over_40_is_an_independent_blocker": False,
    }


def _ordered_stages_exact(candidate: Mapping[str, Any]) -> bool:
    stages = candidate.get("ordered_stages")
    if not isinstance(stages, list) or len(stages) != len(STAGES):
        return False
    return [item.get("stage") for item in stages if isinstance(item, Mapping)] == [
        item.name for item in STAGES
    ] and [
        item.get("cumulative_accepted_unique_geometries")
        for item in stages
        if isinstance(item, Mapping)
    ] == [item.cumulative_target for item in STAGES]


def _stage_transition_exact(candidate: Mapping[str, Any]) -> bool:
    contract = candidate.get("stage_transition_contract")
    required_true = {
        "prior_stage_exact_pass_receipt_required",
        "no_additional_human_approval_between_passed_stages",
        "golden_failure_blocks_all_later_stages",
        "no_200000_job_simultaneous_launch",
        "no_clobber_shards_required",
        "retry_failed_shards_only",
        "exact_200000_completion_required",
    }
    return isinstance(contract, Mapping) and set(contract) == required_true and all(
        contract.get(key) is True for key in required_true
    )


def _adaptive_concurrency_exact(candidate: Mapping[str, Any]) -> bool:
    contract = candidate.get("adaptive_concurrency")
    if not isinstance(contract, Mapping):
        return False
    expected = {
        "increase_workers_max_per_transition": 1,
        "healthy_checks_required_before_increase": 10,
        "logical_cpu_fraction_max": 0.1,
        "license_capacity_is_hard_cap": True,
        "halve_when_normalized_load1_above": 1.0,
        "halve_when_iowait_percent_above": 15,
        "halve_when_available_memory_fraction_below": 0.15,
        "pause_when_normalized_load1_above": 1.2,
        "pause_when_available_memory_fraction_below": 0.1,
        "pause_on_active_swap_thrashing": True,
        "pause_on_license_failure": True,
        "healthy_existing_emx_children_terminated_for_raw_load_over_40": False,
    }
    return contract == expected


def _golden_contract_exact(candidate: Mapping[str, Any]) -> bool:
    contract = candidate.get("golden_acceptance_contract")
    if not isinstance(contract, Mapping):
        return False
    return (
        contract.get("exact_frequency_points") == 56
        and contract.get("first_frequency_ghz") == 5
        and contract.get("last_frequency_ghz") == 60
        and contract.get("frequency_step_ghz") == 1
        and contract.get("extracted_feature_rows") == 56
        and all(
            contract.get(key) is True
            for key in (
                "deterministic_canonical_geometry_required",
                "frozen_ten_dimensional_bounds_required",
                "analytical_geometry_pass_required",
                "topology_pass_required",
                "cadence_gds_required",
                "zero_blocking_calibre_violations_required",
                "fresh_real_emx_required",
                "parseable_four_port_s4p_required",
                "complete_finite_s_matrix_required",
                "complete_finite_z_matrix_required",
                "complete_geometry_to_s4p_hash_chain_required",
            )
        )
    )


def _authorized_actions_exact(candidate: Mapping[str, Any]) -> bool:
    actions = candidate.get("authorized_actions_after_pass_receipt")
    required = {
        "fresh_read_only_capacity_inspection",
        "one_golden_when_capacity_gate_passes",
        "one_lightweight_waiting_supervisor",
        "automatic_pause_and_resume",
        "automatic_ordered_staged_execution_after_each_prior_pass",
        "cadence_calibre_emx_within_current_stage_only",
    }
    return isinstance(actions, Mapping) and set(actions) == required and all(
        actions.get(key) is True for key in required
    )


def _prior_receipts_exact(candidate: Mapping[str, Any]) -> bool:
    prior = candidate.get("prior_receipts")
    return (
        isinstance(prior, Mapping)
        and prior.get("preserve_without_alteration") is True
        and _evidence_hashes_match(prior, PRIOR_EVIDENCE_FIELDS)
    )


def _evidence_hashes_match(
    evidence: Any, fields: Mapping[str, str]
) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    for path_key, sha_key in fields.items():
        path_value = evidence.get(path_key)
        expected_sha = str(evidence.get(sha_key) or "").lower()
        if not isinstance(path_value, str) or not SHA256_PATTERN.fullmatch(expected_sha):
            return False
        path = (ROOT / path_value).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            return False
        if not path.is_file() or _sha256(path) != expected_sha:
            return False
    return True


def _read_json(path: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    checks.append(_check("candidate_exists", path.is_file(), str(path)))
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check("candidate_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check("candidate_parses", isinstance(payload, dict), type(payload).__name__))
    return payload if isinstance(payload, dict) else {}


def _aware_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": str(detail)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
