from __future__ import annotations

import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    PRODUCTION_BACKEND_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    PROFILE_EXECUTION_MODE,
    PROFILE_KEY,
    PROFILE_SCHEMA,
    StageExecutionProfileError,
    expected_result_path_fields,
    expected_stage_role_order,
    expand_argument,
    read_execution_profile,
    resolve_under,
    validate_execution_profile,
)


def _profile() -> tuple[dict, dict]:
    roles = {
        role
        for stage in STAGES
        for role in expected_stage_role_order(stage.name)
    }
    manifest = {
        "script_identities": {
            role: {
                "path": f"/private/scripts/{role}.py",
                "size_bytes": 1,
                "sha256": "1" * 64,
            }
            for role in roles
        }
    }
    profile = {
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
                        "argv": ["--out-dir", "{role_out_dir}", "--stage", "{stage}"],
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
    return profile, manifest


def test_exact_execution_profile_passes() -> None:
    profile, manifest = _profile()

    assert validate_execution_profile(profile, backend_manifest=manifest) == []


def test_explicit_historical_golden_terminal_keeps_full_physical_role_order():
    from rfic_transformer_inverse_design.campaigns.broadband56_golden_stage import TERMINAL_MODE
    profile, manifest = _profile()
    profile["stages"]["GOLDEN"]["golden_terminal_mode"] = TERMINAL_MODE
    assert validate_execution_profile(profile, backend_manifest=manifest) == []
    profile["stages"]["GOLDEN"]["commands"].pop(4)
    assert validate_execution_profile(profile, backend_manifest=manifest)


@pytest.mark.parametrize("stage,mode", [("PILOT_32", "HISTORICAL_GOLDEN_VALIDATION_ONLY_ZERO_PRODUCTION"),
                                        ("GOLDEN", "SKIP_PHYSICAL_GATES")])
def test_profile_rejects_validation_terminal_outside_exact_golden(stage, mode):
    profile, manifest = _profile()
    profile["stages"][stage]["golden_terminal_mode"] = mode
    assert validate_execution_profile(profile, backend_manifest=manifest)


def test_adaptive_role_order_matches_contract_runbook() -> None:
    roles = expected_stage_role_order("PHASE_B")

    assert roles[:6] == (
        "adaptive_checkpoint_materializer",
        "acquisition_ensemble_trainer",
        "adaptive_round_stager",
        "adaptive_candidate_pool_builder",
        "acquisition_predictor",
        "adaptive_candidate_selector",
    )


def test_nonadaptive_checkpointed_stage_role_orders_are_explicit() -> None:
    assert expected_stage_role_order("GOLDEN")[0] == "phase_a_queue_builder"
    assert expected_stage_role_order("PILOT_32")[:2] == (
        "adaptive_checkpoint_materializer",
        "phase_a_queue_builder",
    )
    assert expected_stage_role_order("PILOT_1000")[:2] == (
        "adaptive_checkpoint_materializer",
        "phase_a_queue_builder",
    )
    assert expected_stage_role_order("PHASE_A")[:2] == (
        "adaptive_checkpoint_materializer",
        "phase_a_queue_builder",
    )


def test_rejects_reordered_role_chain() -> None:
    profile, manifest = _profile()
    commands = profile["stages"]["GOLDEN"]["commands"]
    commands[0], commands[1] = commands[1], commands[0]

    errors = validate_execution_profile(profile, backend_manifest=manifest)

    assert "stages.GOLDEN.commands role order mismatch" in errors


def test_rejects_shell_unknown_placeholder_and_missing_role_output_binding() -> None:
    profile, manifest = _profile()
    command = profile["stages"]["GOLDEN"]["commands"][0]
    command["argv"] = ["--unexpected", "{not_authorized}"]
    command["shell_used"] = True

    errors = validate_execution_profile(profile, backend_manifest=manifest)

    assert any("unknown placeholders" in error for error in errors)
    assert any("must bind role_out_dir" in error for error in errors)
    assert any("shell_used mismatch" in error for error in errors)


def test_rejects_result_and_receipt_path_escape() -> None:
    profile, manifest = _profile()
    profile["stages"]["GOLDEN"]["result_paths"][
        "raw_products_receipt"
    ] = "../outside.json"
    profile["stages"]["GOLDEN"]["commands"][0]["receipt"] = "/tmp/pass.json"

    errors = validate_execution_profile(profile, backend_manifest=manifest)

    assert any("raw_products_receipt must be a safe relative path" in error for error in errors)
    assert any("receipt must be a safe relative path" in error for error in errors)


def test_reads_profile_from_hash_bound_configuration(tmp_path: Path) -> None:
    profile, _ = _profile()
    path = tmp_path / "private.json"
    path.write_text(json.dumps({PROFILE_KEY: profile}) + "\n", encoding="utf-8")

    assert read_execution_profile(path) == profile


def test_argument_expansion_and_root_resolution_fail_closed(tmp_path: Path) -> None:
    assert expand_argument("{stage}/result", {"{stage}": "GOLDEN"}) == "GOLDEN/result"
    with pytest.raises(StageExecutionProfileError, match="unresolved"):
        expand_argument("{unknown}", {})
    with pytest.raises(StageExecutionProfileError, match="safe relative"):
        resolve_under(tmp_path, "../outside", label="result")


def test_profile_validator_has_no_process_execution_capability() -> None:
    source = Path(read_execution_profile.__code__.co_filename).read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "subprocess.run" not in source
