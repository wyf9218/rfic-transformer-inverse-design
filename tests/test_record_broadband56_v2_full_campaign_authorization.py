from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    POLICY_APPROVAL_SCOPE,
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_CANDIDATE_EFFECT,
    FULL_CAMPAIGN_CANDIDATE_SCHEMA,
    FULL_CAMPAIGN_PENDING_STATUS,
    GEOMETRY_BOUNDS_UM,
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
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (
    BACKEND_MANIFEST_EFFECT,
    BACKEND_MANIFEST_SCHEMA,
    BACKEND_VERIFICATION_PASS_DECISION,
    BACKEND_VERIFICATION_PASS_CHECKS,
    BACKEND_VERIFICATION_SCHEMA,
    LABEL_CONTRACT,
    PRODUCTION_CHAIN,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    STAGE_COMMAND_ARGUMENTS,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    PROFILE_EXECUTION_MODE,
    PROFILE_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "record_broadband56_v2_full_campaign_authorization.py"
SPEC = importlib.util.spec_from_file_location("full_campaign_recorder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _identity_record(path: Path, value: str) -> dict[str, object]:
    written = _write(path, value)
    return {
        "path": str(written.resolve()),
        "sha256": _sha(written),
        "size_bytes": written.stat().st_size,
    }


def _execution_profile() -> dict:
    return {
        "schema": PROFILE_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "execution_mode": PROFILE_EXECUTION_MODE,
        "shell_used": False,
        "stages": {
            stage.name: {
                "commands": [
                    {
                        "role": role,
                        "argv": ["--out-dir", "{role_out_dir}"],
                        "receipt": "ROLE_RECEIPT.json",
                        "shell_used": False,
                    }
                    for role in expected_stage_role_order(stage.name)
                ],
                "result_paths": {
                    field: f"results/{field}.json"
                    for field in expected_result_path_fields(stage.name)
                },
            }
            for stage in STAGES
        },
    }


def _padded_preparation_receipt(path: Path) -> Path:
    payload = {
        "overall_status": "PASS",
        "decision": "PREPARED_FOR_GOLDEN_GATE",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "checks": [
            {"name": f"check_{index:02d}", "pass": True, "detail": "PASS"}
            for index in range(40)
        ],
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    assert len(raw) < 10439
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw + b" " * (10439 - len(raw)))
    return path


def _valid_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(MODULE, "ROOT", repo)

    public = {}
    for index, (label, relative) in enumerate(PUBLIC_EVIDENCE_FIELDS.items()):
        path = _write(repo / relative, f"public-evidence-{index}\n")
        public[label] = {"path": relative, "sha256": _sha(path)}

    private_root = tmp_path / "private"
    preparation = _padded_preparation_receipt(private_root / "PREPARATION_RECEIPT.json")
    private_config = _write(private_root / "config.yaml", "frequency: exact56\n")
    historical_config = _write(private_root / "historical.yaml", "frequency: historical111\n")
    campaign_contract = _write(private_root / "CAMPAIGN_CONTRACT.json", {"contract": "frozen"})
    primary_bins = _write(private_root / "PRIMARY_BINS_FROZEN.json", {"bins": 6})
    secondary = _write(private_root / "SECONDARY_COVERAGE_FROZEN.json", {"coverage": "frozen"})
    bounds = _write(private_root / "GEOMETRY_BOUNDS_FROZEN.json", {"field_bounds_um": GEOMETRY_BOUNDS_UM})
    phase_plan = _write(private_root / "PHASE_PLAN_FROZEN.json", {"stages": expected_stage_contract()})
    policy_receipt = _write(private_root / "OPERATIONAL_POLICY_APPROVAL_RECEIPT.json", {"overall_status": "PASS"})

    script_identities = {
        role: _identity_record(
            private_root / "scripts" / f"{role}.py",
            f"#!/usr/bin/env python3\n# test identity: {role}\n",
        )
        for role in REQUIRED_SCRIPT_ROLES
    }
    runtime_identities = {}
    for role in REQUIRED_RUNTIME_ROLES:
        path = (
            private_config
            if role == "private_configuration"
            else private_root / "runtime" / f"{role}.dat"
        )
        if role == "private_configuration":
            runtime_identities[role] = {
                "path": str(path.resolve()),
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        elif role == "stage_execution_profile":
            written = _write(path, _execution_profile())
            runtime_identities[role] = {
                "path": str(written.resolve()),
                "sha256": _sha(written),
                "size_bytes": written.stat().st_size,
            }
        else:
            runtime_identities[role] = _identity_record(
                path,
                f"test runtime identity: {role}\n",
            )
    production_backend = Path(
        str(script_identities["production_stage_backend"]["path"])
    )
    emx_wrapper = Path(str(runtime_identities["emx_wrapper"]["path"]))
    python_executable = Path(
        str(runtime_identities["python_executable"]["path"])
    )
    production_backend.chmod(0o755)
    emx_wrapper.chmod(0o755)
    python_executable.chmod(0o755)
    script_identities["production_stage_backend"]["executable"] = True
    runtime_identities["emx_wrapper"]["executable"] = True
    runtime_identities["python_executable"]["executable"] = True
    script_hashes = {
        role: str(record["sha256"])
        for role, record in script_identities.items()
    }
    runtime_hashes = {
        role: str(record["sha256"])
        for role, record in runtime_identities.items()
    }
    pass_receipt_records = [
        {
            **_identity_record(
                private_root / "history" / "backend_one.json",
                json.dumps({"overall_status": "PASS", "receipt_id": "one"})
                + "\n",
            ),
            "overall_status": "PASS",
        },
        {
            **_identity_record(
                private_root / "history" / "backend_two.json",
                json.dumps({"overall_status": "PASS", "receipt_id": "two"})
                + "\n",
            ),
            "overall_status": "PASS",
        },
    ]
    pass_receipts = [
        {
            "overall_status": record["overall_status"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
        }
        for record in pass_receipt_records
    ]
    gds_receipt_record = {
        **_identity_record(
            private_root / "history" / "gds.json",
            json.dumps({"overall_status": "PASS", "receipt_id": "gds"}) + "\n",
        ),
        "overall_status": "PASS",
    }
    backend_manifest = _write(
        private_root / "BACKEND_IDENTITY.json",
        {
            "schema": BACKEND_MANIFEST_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_id": PRODUCTION_BACKEND_ID,
            "manifest_effect": BACKEND_MANIFEST_EFFECT,
            "simulator_action_taken": False,
            "private_paths_published": False,
            "no_clobber_required": True,
            "execution_chain": list(PRODUCTION_CHAIN),
            "scientific_contract": {
                "frequency_contract": expected_frequency_contract(),
                "geometry_contract": expected_geometry_contract(),
                "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
                "label_contract": LABEL_CONTRACT,
                "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
                "terminal_contract": expected_terminal_contract(),
                "ordered_stages": expected_stage_contract(),
            },
            "preparation_bindings": {
                "preparation_receipt_sha256": _sha(preparation),
                "private_configuration_sha256": _sha(private_config),
                "historical_configuration_sha256": _sha(historical_config),
                "operational_policy_approval_receipt_sha256": _sha(policy_receipt),
            },
            "script_identities": script_identities,
            "runtime_identities": runtime_identities,
            "stage_commands": {
                stage.name: {
                    "argv": [
                        str(script_identities["production_stage_backend"]["path"]),
                        *[
                            item
                            for flag, placeholder in STAGE_COMMAND_ARGUMENTS
                            for item in (flag, placeholder)
                        ],
                    ],
                    "shell_used": False,
                    "identity_role": "production_stage_backend",
                    "identity_argv_index": 0,
                    "identity_sha256": script_hashes["production_stage_backend"],
                }
                for stage in STAGES
            },
            "historical_gds_identity_pass_receipt": gds_receipt_record,
            "historical_backend_pass_receipts": pass_receipt_records,
        },
    )
    backend_verification = _write(
        private_root / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json",
        {
            "schema": BACKEND_VERIFICATION_SCHEMA,
            "overall_status": "PASS",
            "decision": BACKEND_VERIFICATION_PASS_DECISION,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "backend_identity_manifest": {
                "path": str(backend_manifest.resolve()),
                "size_bytes": backend_manifest.stat().st_size,
                "sha256": _sha(backend_manifest),
            },
            "checks": BACKEND_VERIFICATION_PASS_CHECKS,
            "errors": [],
            "simulator_action_taken": False,
        },
    )

    candidate = {
        "schema": FULL_CAMPAIGN_CANDIDATE_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "scientific_contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "approval_status": FULL_CAMPAIGN_PENDING_STATUS,
        "authorization_scope": "FULL_CAMPAIGN",
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
            "preparation_receipt_sha256": _sha(preparation),
            "preparation_receipt_size_bytes": 10439,
            "preparation_overall_status": "PASS",
            "preparation_decision": "PREPARED_FOR_GOLDEN_GATE",
            "preparation_check_count": 40,
            "preparation_pass_count": 40,
            "preparation_fail_count": 0,
            "private_configuration_sha256": _sha(private_config),
            "historical_configuration_sha256": _sha(historical_config),
            "campaign_contract_frozen_sha256": _sha(campaign_contract),
            "primary_bins_frozen_sha256": _sha(primary_bins),
            "secondary_coverage_frozen_sha256": _sha(secondary),
            "geometry_bounds_frozen_sha256": _sha(bounds),
            "phase_plan_frozen_sha256": _sha(phase_plan),
            "operational_policy_approval_receipt_sha256": _sha(policy_receipt),
            "private_paths_published": False,
        },
        "runtime_and_backend_identity": {
            "backend_id": PRODUCTION_BACKEND_ID,
            "backend_identity_manifest_sha256": _sha(backend_manifest),
            "backend_identity_verification_receipt_sha256": _sha(
                backend_verification
            ),
            "queue_controller_sha256": script_hashes["queue_controller"],
            "resource_gate_auditor_sha256": script_hashes[
                "resource_gate_auditor"
            ],
            "stage_launcher_sha256": script_hashes["stage_launcher"],
            "production_stage_backend_sha256": script_hashes[
                "production_stage_backend"
            ],
            "phase_a_queue_builder_sha256": script_hashes["phase_a_queue_builder"],
            "adaptive_candidate_pool_builder_sha256": script_hashes[
                "adaptive_candidate_pool_builder"
            ],
            "acquisition_ensemble_trainer_sha256": script_hashes[
                "acquisition_ensemble_trainer"
            ],
            "acquisition_predictor_sha256": script_hashes["acquisition_predictor"],
            "adaptive_candidate_selector_sha256": script_hashes[
                "adaptive_candidate_selector"
            ],
            "adaptive_round_stager_sha256": script_hashes["adaptive_round_stager"],
            "cadence_streamout_runner_sha256": script_hashes[
                "cadence_streamout_runner"
            ],
            "candidate_gds_index_builder_sha256": script_hashes[
                "candidate_gds_index_builder"
            ],
            "gds_physical_identity_auditor_sha256": script_hashes[
                "gds_physical_identity_auditor"
            ],
            "gds_physical_identity_module_sha256": script_hashes[
                "gds_physical_identity_module"
            ],
            "resource_policy": RESOURCE_POLICY,
            "operational_policy_approval_scope": POLICY_APPROVAL_SCOPE,
            "calibre_runner_sha256": script_hashes["calibre_runner"],
            "calibre_zero_blocking_receipt_builder_sha256": script_hashes[
                "calibre_zero_blocking_receipt_builder"
            ],
            "exact_audited_gds_emx_runner_sha256": script_hashes[
                "exact_audited_gds_emx_runner"
            ],
            "exact_audited_gds_emx_module_sha256": script_hashes[
                "exact_audited_gds_emx_module"
            ],
            "full_band_s4p_qa_builder_sha256": script_hashes["full_band_s4p_qa_builder"],
            "full_band_s4p_qa_module_sha256": script_hashes[
                "full_band_s4p_qa_module"
            ],
            "raw_products_finalizer_sha256": script_hashes["raw_products_finalizer"],
            "checkpoint_auditor_sha256": script_hashes["checkpoint_auditor"],
            "campaign_histories_finalizer_sha256": script_hashes[
                "campaign_histories_finalizer"
            ],
            "training_readiness_finalizer_sha256": script_hashes[
                "training_readiness_finalizer"
            ],
            "checkpoint_figure_renderer_sha256": script_hashes[
                "checkpoint_figure_renderer"
            ],
            "final_delivery_auditor_sha256": script_hashes[
                "final_delivery_auditor"
            ],
            "resource_probe_sha256": runtime_hashes["resource_probe"],
            "python_executable_sha256": runtime_hashes["python_executable"],
            "historical_gds_identity_pass_receipt_sha256": gds_receipt_record[
                "sha256"
            ],
            "historical_backend_pass_receipts": pass_receipts,
            "cadence_identity_reverified": True,
            "calibre_zero_blocking_gate_required": True,
            "emx_wrapper_identity_reverified": True,
            "emx_process_identity_reverified": True,
            "full_band_s4p_qa_required": True,
            "private_paths_published": False,
        },
        "public_evidence": public,
    }
    candidate_path = _write(repo / "candidate.json", candidate)
    return {
        "repo": repo,
        "candidate": candidate,
        "candidate_path": candidate_path,
        "preparation": preparation,
        "private_config": private_config,
        "historical_config": historical_config,
        "campaign_contract": campaign_contract,
        "primary_bins": primary_bins,
        "secondary": secondary,
        "bounds": bounds,
        "phase_plan": phase_plan,
        "policy_receipt": policy_receipt,
        "backend_manifest": backend_manifest,
        "backend_verification": backend_verification,
    }


def _argv(fixture: dict[str, object], out_dir: Path) -> list[str]:
    candidate_path = fixture["candidate_path"]
    assert isinstance(candidate_path, Path)
    return [
        "--candidate",
        str(candidate_path),
        "--candidate-sha256",
        _sha(candidate_path),
        "--approved-by",
        "Yufeng Wang, project owner and project leader",
        "--approved-utc",
        "2026-08-30T20:00:00Z",
        "--approval-reference",
        "explicit approval of exact test candidate SHA-256",
        "--preparation-receipt",
        str(fixture["preparation"]),
        "--private-configuration",
        str(fixture["private_config"]),
        "--historical-configuration",
        str(fixture["historical_config"]),
        "--campaign-contract-frozen",
        str(fixture["campaign_contract"]),
        "--primary-bins-frozen",
        str(fixture["primary_bins"]),
        "--secondary-coverage-frozen",
        str(fixture["secondary"]),
        "--geometry-bounds-frozen",
        str(fixture["bounds"]),
        "--phase-plan-frozen",
        str(fixture["phase_plan"]),
        "--operational-policy-approval-receipt",
        str(fixture["policy_receipt"]),
        "--backend-identity-manifest",
        str(fixture["backend_manifest"]),
        "--backend-identity-verification-receipt",
        str(fixture["backend_verification"]),
        "--out-dir",
        str(out_dir),
    ]


def test_records_exact_full_campaign_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    out_dir = tmp_path / "receipt"

    assert MODULE.main(_argv(fixture, out_dir)) == 0

    receipt = json.loads((out_dir / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "APPROVE_FULL_CAMPAIGN"
    assert receipt["authorization_scope"] == "FULL_CAMPAIGN"
    assert receipt["campaign_200k_authorized"] is True
    assert receipt["accepted_geometry_target"] == 200_000
    assert receipt["replenished_attempt_rounds_authorized"] is True
    assert receipt["attempt_replenishment_contract"] == ATTEMPT_REPLENISHMENT_CONTRACT
    assert "simulator_geometry_limit" not in receipt
    assert receipt["expected_feature_rows"] == 11_200_000
    assert receipt["execution_effect"] == "NONE_RECORD_ONLY"
    assert all(item["pass"] for item in receipt["checks"])


def test_rejects_changed_frequency_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    candidate = fixture["candidate"]
    assert isinstance(candidate, dict)
    candidate["frequency_contract"]["points"] = 55
    candidate_path = fixture["candidate_path"]
    assert isinstance(candidate_path, Path)
    _write(candidate_path, candidate)

    assert MODULE.main(_argv(fixture, tmp_path / "receipt")) == 2


def test_rejects_gds_physical_identity_role_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    candidate = fixture["candidate"]
    candidate_path = fixture["candidate_path"]
    assert isinstance(candidate, dict)
    assert isinstance(candidate_path, Path)
    candidate["runtime_and_backend_identity"][
        "gds_physical_identity_auditor_sha256"
    ] = "f" * 64
    _write(candidate_path, candidate)

    assert MODULE.main(_argv(fixture, tmp_path / "receipt")) == 2


def test_rejects_private_preparation_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    private_config = fixture["private_config"]
    assert isinstance(private_config, Path)
    private_config.write_text("frequency: drifted\n", encoding="utf-8")

    assert MODULE.main(_argv(fixture, tmp_path / "receipt")) == 2


def test_rejects_false_check_in_hash_bound_backend_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    verification_path = fixture["backend_verification"]
    candidate = fixture["candidate"]
    candidate_path = fixture["candidate_path"]
    assert isinstance(verification_path, Path)
    assert isinstance(candidate, dict)
    assert isinstance(candidate_path, Path)

    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    verification["checks"]["all_named_file_sha256_values_match"] = False
    _write(verification_path, verification)
    candidate["runtime_and_backend_identity"][
        "backend_identity_verification_receipt_sha256"
    ] = _sha(verification_path)
    _write(candidate_path, candidate)

    assert MODULE.main(_argv(fixture, tmp_path / "receipt")) == 2


def test_candidate_validator_rejects_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    candidate = fixture["candidate"]
    assert isinstance(candidate, dict)
    candidate["note"] = "/volumes/private/runtime"

    errors = validate_full_campaign_candidate(candidate, repository_root=fixture["repo"])

    assert any("forbidden private token" in error for error in errors)


def test_no_clobber_receipt_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _valid_fixture(tmp_path, monkeypatch)
    out_dir = tmp_path / "receipt"
    out_dir.mkdir()

    assert MODULE.main(_argv(fixture, out_dir)) == 2
