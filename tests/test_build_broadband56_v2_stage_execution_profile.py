from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

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
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (
    REQUIRED_SCRIPT_ROLES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    COMMAND_PLAN_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
    validate_execution_profile,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_v2_stage_execution_profile.py"
SPEC = importlib.util.spec_from_file_location("stage_execution_profile_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> dict:
    return {
        "schema": COMMAND_PLAN_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "shell_used": False,
        "stages": {
            stage.name: {
                "commands": [
                    {
                        "role": role,
                        "argv": [
                            "--stage",
                            "{stage}",
                            "--out-dir",
                            "{role_out_dir}",
                        ],
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


def _write_plan(tmp_path: Path, plan: dict | None = None) -> Path:
    path = tmp_path / "private_command_plan.json"
    path.write_text(json.dumps(plan or _plan()) + "\n", encoding="utf-8")
    return path


def _argv(tmp_path: Path, plan_path: Path) -> list[str]:
    return [
        "--command-plan",
        str(plan_path),
        "--out-dir",
        str(tmp_path / "profile_build"),
    ]


def _stub_manifest() -> dict:
    return {
        "script_identities": {
            role: {"role": role} for role in REQUIRED_SCRIPT_ROLES
        }
    }


def test_builder_writes_valid_profile_and_hash_bound_receipt(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)

    assert MODULE.main(_argv(tmp_path, plan_path)) == 0

    out = tmp_path / "profile_build"
    profile_path = out / "STAGE_EXECUTION_PROFILE.json"
    receipt_path = out / "STAGE_EXECUTION_PROFILE_BUILD_RECEIPT.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert validate_execution_profile(profile, backend_manifest=_stub_manifest()) == []
    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == MODULE.BUILD_DECISION
    assert receipt["command_plan"]["sha256"] == _sha(plan_path)
    assert receipt["stage_execution_profile"]["sha256"] == _sha(profile_path)
    assert receipt["simulator_action_taken"] is False
    sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert _sha(profile_path) in sums
    assert _sha(receipt_path) in sums


def test_builder_is_no_clobber(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    argv = _argv(tmp_path, plan_path)
    assert MODULE.main(argv) == 0
    profile = tmp_path / "profile_build" / "STAGE_EXECUTION_PROFILE.json"
    before = profile.read_bytes()

    assert MODULE.main(argv) == 2
    assert profile.read_bytes() == before


def test_builder_rejects_role_reordering(tmp_path: Path) -> None:
    plan = _plan()
    commands = plan["stages"]["GOLDEN"]["commands"]
    commands[0], commands[1] = commands[1], commands[0]
    plan_path = _write_plan(tmp_path, plan)

    assert MODULE.main(_argv(tmp_path, plan_path)) == 2
    assert not (tmp_path / "profile_build").exists()


def test_builder_rejects_unsafe_result_path(tmp_path: Path) -> None:
    plan = _plan()
    plan["stages"]["GOLDEN"]["result_paths"][
        "checkpoint_receipt"
    ] = "../outside.json"
    plan_path = _write_plan(tmp_path, plan)

    assert MODULE.main(_argv(tmp_path, plan_path)) == 2
    assert not (tmp_path / "profile_build").exists()


def test_builder_rejects_shell_execution(tmp_path: Path) -> None:
    plan = _plan()
    plan["stages"]["GOLDEN"]["commands"][0]["shell_used"] = True
    plan_path = _write_plan(tmp_path, plan)

    assert MODULE.main(_argv(tmp_path, plan_path)) == 2
    assert not (tmp_path / "profile_build").exists()


def test_builder_rejects_symlink_command_plan(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    symlink = tmp_path / "plan_link.json"
    symlink.symlink_to(plan_path)

    assert MODULE.main(_argv(tmp_path, symlink)) == 2
    assert not (tmp_path / "profile_build").exists()


def test_builder_has_no_process_execution_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "subprocess.run" not in source
