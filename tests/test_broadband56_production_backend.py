from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    PORT_AND_GROUNDING_CONTRACT,
    PRODUCTION_BACKEND_ID,
    expected_frequency_contract,
    expected_geometry_contract,
    expected_stage_contract,
    expected_terminal_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (
    BACKEND_MANIFEST_EFFECT,
    BACKEND_MANIFEST_SCHEMA,
    BACKEND_VERIFICATION_PASS_CHECKS,
    FAILURE_ACCOUNTING_FIELDS,
    LABEL_CONTRACT,
    PRODUCTION_CHAIN,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    STAGE_ARTIFACT_FIELDS,
    STAGE_COMMAND_ARGUMENTS,
    STAGE_GATE_FIELDS,
    STAGE_RECEIPT_SCHEMA,
    stage_artifact_fields,
    validate_backend_identity_manifest,
    validate_stage_receipt,
    validate_stage_receipt_chain,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    PROFILE_EXECUTION_MODE,
    PROFILE_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
)


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_SCRIPT = ROOT / "scripts" / "verify_broadband56_v2_private_backend_identity.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "private_backend_identity_verifier", VERIFIER_SCRIPT
)
assert VERIFIER_SPEC and VERIFIER_SPEC.loader
VERIFIER_MODULE = importlib.util.module_from_spec(VERIFIER_SPEC)
VERIFIER_SPEC.loader.exec_module(VERIFIER_MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _write(path: Path, value: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _execution_profile() -> dict:
    return {
        "schema": PROFILE_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
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


def _backend_manifest(tmp_path: Path) -> tuple[dict, Path]:
    files = {
        role: _write(tmp_path / "files" / role, f"{role}\n")
        for role in (*REQUIRED_SCRIPT_ROLES, *REQUIRED_RUNTIME_ROLES)
    }
    files["production_stage_backend"].chmod(0o755)
    files["emx_wrapper"].chmod(0o755)
    files["stage_execution_profile"] = _write(
        files["stage_execution_profile"],
        _execution_profile(),
    )
    scripts = {role: _identity(files[role]) for role in REQUIRED_SCRIPT_ROLES}
    runtimes = {role: _identity(files[role]) for role in REQUIRED_RUNTIME_ROLES}
    scripts["production_stage_backend"]["executable"] = True
    runtimes["emx_wrapper"]["executable"] = True
    historical_one = _write(
        tmp_path / "history" / "backend_one.json",
        {"overall_status": "PASS", "receipt_id": "one"},
    )
    historical_two = _write(
        tmp_path / "history" / "backend_two.json",
        {"overall_status": "PASS", "receipt_id": "two"},
    )
    historical_gds = _write(
        tmp_path / "history" / "gds.json",
        {"overall_status": "PASS", "receipt_id": "gds"},
    )
    backend_path = str(files["production_stage_backend"].resolve())
    argv = [
        backend_path,
        *[
            item
            for flag, placeholder in STAGE_COMMAND_ARGUMENTS
            for item in (flag, placeholder)
        ],
    ]
    manifest = {
        "schema": BACKEND_MANIFEST_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
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
            "terminal_contract": expected_terminal_contract(),
            "ordered_stages": expected_stage_contract(),
        },
        "preparation_bindings": {
            "preparation_receipt_sha256": "1" * 64,
            "private_configuration_sha256": "2" * 64,
            "historical_configuration_sha256": "3" * 64,
            "operational_policy_approval_receipt_sha256": "4" * 64,
        },
        "script_identities": scripts,
        "runtime_identities": runtimes,
        "stage_commands": {
            stage.name: {
                "argv": argv,
                "identity_argv_index": 0,
                "identity_role": "production_stage_backend",
                "identity_sha256": scripts["production_stage_backend"]["sha256"],
                "shell_used": False,
            }
            for stage in STAGES
        },
        "historical_backend_pass_receipts": [
            {**_identity(historical_one), "overall_status": "PASS"},
            {**_identity(historical_two), "overall_status": "PASS"},
        ],
        "historical_gds_identity_pass_receipt": {
            **_identity(historical_gds),
            "overall_status": "PASS",
        },
    }
    path = _write(tmp_path / "backend_manifest.json", manifest)
    return manifest, path


def _stage_receipt(
    tmp_path: Path,
    *,
    target: int = 1,
    stage: str = "GOLDEN",
) -> dict:
    raw_receipt = {
        "schema": "broadband56_raw_products_receipt_v1",
        "overall_status": "PASS",
        "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "counts": {
            "accepted_geometries": target,
            "s4p_artifacts": target,
            "geometry_frequency_rows": target * 56,
        },
        "checks": {
            "all_accepted_s4p_are_fresh_exact_56_point_four_port": True,
            "long_features_bound_to_exact_s4p_s_and_z": True,
            "long_physical_features_recomputed_from_exact_s4p": True,
            "proxy_values_excluded_from_labels": True,
        },
    }
    raw_path = _write(tmp_path / "artifacts" / "raw_products.json", raw_receipt)
    checkpoint_receipt = {
        "overall_status": "PASS",
        "decision": "USE_CHECKPOINT",
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "expected_accepted": target,
        "checks": [{"name": "exact_count", "pass": True}],
    }
    checkpoint_path = _write(
        tmp_path / "artifacts" / "checkpoint_receipt.json",
        checkpoint_receipt,
    )
    terminal_receipts = {
        "campaign_history_receipt": {
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_CAMPAIGN_HISTORY",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        },
        "training_readiness_receipt": {
            "overall_status": "PASS",
            "decision": "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        },
        "checkpoint_figure_receipt": {
            "overall_status": "PASS",
            "decision": "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        },
        "final_delivery_receipt": {
            "overall_status": "PASS",
            "decision": "REPORT_COMPLETE_200K_WITH_SEPARATE_COVERAGE_STATUS",
            "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
            "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
            "execution_completion": "COMPLETE_200K",
            "terminal_counts": {
                "accepted_geometries": 200_000,
                "s4p_artifacts": 200_000,
                "geometry_frequency_rows": 11_200_000,
            },
        },
    }
    artifact_paths = {}
    for role in stage_artifact_fields(stage):
        if role == "raw_products_receipt":
            path = raw_path
        elif role == "checkpoint_receipt":
            path = checkpoint_path
        elif role in terminal_receipts:
            path = _write(
                tmp_path / "artifacts" / f"{role}.json",
                terminal_receipts[role],
            )
        else:
            path = _write(tmp_path / "artifacts" / role, f"{role}\n")
        artifact_paths[role] = path
    failure = {field: 0 for field in FAILURE_ACCOUNTING_FIELDS}
    failure["raw_geometry_candidates"] = target
    failure["accepted_geometries"] = target
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "overall_status": "PASS",
        "decision": "ACCEPT_STAGE",
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "stage": stage,
        "terminal_state": {item.name: item.receipt_status for item in STAGES}[stage],
        "cumulative_target": target,
        "accepted_unique_geometries": target,
        "backend_identity_manifest_sha256": "8" * 64,
        "full_campaign_authorization_receipt_sha256": "9" * 64,
        "prior_stage_receipt_sha256": None,
        "frequency_contract": expected_frequency_contract(),
        "port_and_grounding_contract": PORT_AND_GROUNDING_CONTRACT,
        "label_source": "FRESH_REAL_EMX_ONLY",
        "counts": {
            "accepted_unique_geometries": target,
            "valid_s4p_geometries": target,
            "feature_complete_geometries": target,
            "s4p_artifacts": target,
            "independent_designs": target,
            "geometry_frequency_rows": target * 56,
            "broadband_descriptor_valid_rows": target * 56,
            "strict_lumped_valid_rows": target * 56,
        },
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
        "failure_accounting": failure,
        "artifacts": {role: _identity(path) for role, path in artifact_paths.items()},
    }


def test_backend_manifest_verifies_every_file_and_stage(tmp_path: Path) -> None:
    manifest, _ = _backend_manifest(tmp_path)

    assert validate_backend_identity_manifest(manifest, verify_files=True) == []


def test_backend_manifest_rejects_identity_drift(tmp_path: Path) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    path = Path(manifest["runtime_identities"]["emx_process_file"]["path"])
    path.write_text("changed\n", encoding="utf-8")

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert any("emx_process_file.sha256 mismatches file" in error for error in errors)


def test_backend_manifest_rejects_missing_stage(tmp_path: Path) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    del manifest["stage_commands"]["PHASE_C"]

    errors = validate_backend_identity_manifest(manifest, verify_files=False)

    assert "stage_commands keys do not exactly match the ordered stages" in errors


@pytest.mark.parametrize(
    "role",
    (
        "candidate_gds_index_builder",
        "gds_physical_identity_auditor",
        "gds_physical_identity_module",
    ),
)
def test_backend_manifest_requires_current_gds_identity_roles(
    tmp_path: Path, role: str
) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    del manifest["script_identities"][role]

    errors = validate_backend_identity_manifest(manifest, verify_files=False)

    assert f"script_identities lacks roles: ['{role}']" in errors


def test_backend_manifest_rejects_unbound_interpreter_prefix(tmp_path: Path) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    command = manifest["stage_commands"]["GOLDEN"]
    command["argv"].insert(0, "/usr/bin/python3")
    command["identity_argv_index"] = 1

    errors = validate_backend_identity_manifest(manifest, verify_files=False)

    assert "stage_commands.GOLDEN.identity_argv_index must be zero" in errors


def test_backend_manifest_rejects_non_executable_emx_wrapper(tmp_path: Path) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    wrapper = Path(manifest["runtime_identities"]["emx_wrapper"]["path"])
    wrapper.chmod(0o644)

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert "runtime_identities.emx_wrapper.path is not executable" in errors


def test_backend_manifest_reparses_stage_profile_after_identity_match(
    tmp_path: Path,
) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    record = manifest["runtime_identities"]["stage_execution_profile"]
    path = Path(record["path"])
    profile = json.loads(path.read_text(encoding="utf-8"))
    commands = profile["stages"]["GOLDEN"]["commands"]
    commands[0], commands[1] = commands[1], commands[0]
    _write(path, profile)
    record.update(_identity(path))

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert any(
        "stage_execution_profile: stages.GOLDEN.commands role order mismatch"
        in error
        for error in errors
    )


def test_backend_manifest_rejects_historical_receipt_file_tamper(
    tmp_path: Path,
) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    path = Path(manifest["historical_backend_pass_receipts"][0]["path"])
    path.write_text("tampered\n", encoding="utf-8")

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert any(
        "historical_backend_pass_receipts.0.sha256 mismatches file" in error
        for error in errors
    )


def test_backend_manifest_reparses_historical_receipt_pass_status(
    tmp_path: Path,
) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    record = manifest["historical_gds_identity_pass_receipt"]
    path = Path(record["path"])
    _write(path, {"overall_status": "FAIL", "receipt_id": "gds"})
    record.update(_identity(path))

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert (
        "historical_gds_identity_pass_receipt.file is not top-level PASS" in errors
    )


def test_backend_manifest_rejects_duplicate_historical_receipt_identity(
    tmp_path: Path,
) -> None:
    manifest, _ = _backend_manifest(tmp_path)
    manifest["historical_backend_pass_receipts"][1] = dict(
        manifest["historical_backend_pass_receipts"][0]
    )

    errors = validate_backend_identity_manifest(manifest, verify_files=False)

    assert "historical backend receipt paths must be distinct" in errors
    assert "historical backend receipt bytes must be distinct" in errors


def test_exact_stage_receipt_passes(tmp_path: Path) -> None:
    receipt = _stage_receipt(tmp_path)

    assert validate_stage_receipt(
        receipt,
        stage="GOLDEN",
        cumulative_target=1,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        prior_stage_receipt_sha256=None,
        verify_artifacts=True,
    ) == []


def test_stage_receipt_rejects_proxy_label_and_artifact_drift(tmp_path: Path) -> None:
    receipt = _stage_receipt(tmp_path)
    receipt["safeguards"]["proxy_label_count"] = 1
    artifact = Path(receipt["artifacts"]["attempt_ledger"]["path"])
    artifact.write_text("tampered\n", encoding="utf-8")

    errors = validate_stage_receipt(
        receipt,
        stage="GOLDEN",
        cumulative_target=1,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        prior_stage_receipt_sha256=None,
        verify_artifacts=True,
    )

    assert "safeguards mismatch" in errors
    assert any("artifacts.attempt_ledger.sha256 mismatches file" in error for error in errors)


def test_stage_receipt_chain_revalidates_prior_stage_bytes(tmp_path: Path) -> None:
    stage_dir = tmp_path / "stages" / "000001_golden"
    receipt = _stage_receipt(stage_dir / "backend")
    receipt_path = _write(stage_dir / "STAGE_RECEIPT.json", receipt)
    records = [(receipt_path, receipt)]

    assert validate_stage_receipt_chain(
        records,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        verify_artifacts=True,
    ) == []

    receipt["gates"]["fresh_real_emx_gate_complete"] = False
    errors = validate_stage_receipt_chain(
        records,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        verify_artifacts=True,
    )
    assert any(
        "stage_chain.GOLDEN: gates.fresh_real_emx_gate_complete must be true"
        in error
        for error in errors
    )


def test_phase_c_requires_exact_terminal_delivery_counts(tmp_path: Path) -> None:
    receipt = _stage_receipt(tmp_path, target=200_000, stage="PHASE_C")
    receipt["prior_stage_receipt_sha256"] = "7" * 64

    assert validate_stage_receipt(
        receipt,
        stage="PHASE_C",
        cumulative_target=200_000,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        prior_stage_receipt_sha256="7" * 64,
        verify_artifacts=True,
    ) == []

    final_record = receipt["artifacts"]["final_delivery_receipt"]
    final_path = Path(final_record["path"])
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["terminal_counts"]["geometry_frequency_rows"] = 11_199_999
    _write(final_path, final)
    final_record.update(_identity(final_path))
    errors = validate_stage_receipt(
        receipt,
        stage="PHASE_C",
        cumulative_target=200_000,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        prior_stage_receipt_sha256="7" * 64,
        verify_artifacts=True,
    )
    assert "final_delivery_receipt.terminal_counts.geometry_frequency_rows mismatch" in errors


def test_stage_receipt_rejects_artifact_path_outside_stage_root(
    tmp_path: Path,
) -> None:
    receipt = _stage_receipt(tmp_path / "outside")
    expected_root = tmp_path / "expected"
    expected_root.mkdir()

    errors = validate_stage_receipt(
        receipt,
        stage="GOLDEN",
        cumulative_target=1,
        backend_manifest_sha256="8" * 64,
        authorization_receipt_sha256="9" * 64,
        prior_stage_receipt_sha256=None,
        verify_artifacts=True,
        artifact_root=expected_root,
    )
    assert any("escapes the stage artifact root" in error for error in errors)


def test_backend_verifier_writes_hash_bound_pass_receipt(tmp_path: Path) -> None:
    _, manifest_path = _backend_manifest(tmp_path)
    out_dir = tmp_path / "verification"

    assert VERIFIER_MODULE.main(
        [
            "--backend-identity-manifest",
            str(manifest_path),
            "--out-dir",
            str(out_dir),
        ]
    ) == 0

    receipt_path = out_dir / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["backend_identity_manifest"]["sha256"] == _sha(manifest_path)
    assert receipt["checks"] == BACKEND_VERIFICATION_PASS_CHECKS
    assert receipt["errors"] == []
    assert receipt["simulator_action_taken"] is False
    assert _sha(receipt_path) in (out_dir / "SHA256SUMS.txt").read_text()


def test_backend_verifier_records_fail_on_named_file_tamper(tmp_path: Path) -> None:
    manifest, manifest_path = _backend_manifest(tmp_path)
    tampered = Path(manifest["runtime_identities"]["emx_process_file"]["path"])
    tampered.write_text("tampered\n", encoding="utf-8")
    out_dir = tmp_path / "verification"

    assert VERIFIER_MODULE.main(
        [
            "--backend-identity-manifest",
            str(manifest_path),
            "--out-dir",
            str(out_dir),
        ]
    ) == 2

    receipt = json.loads(
        (out_dir / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["overall_status"] == "FAIL"
    assert any("emx_process_file.sha256 mismatches file" in error for error in receipt["errors"])
    assert receipt["checks"]["all_named_files_exist"] is True
    assert receipt["checks"]["all_named_file_sha256_values_match"] is False
    assert receipt["checks"]["all_stage_commands_hash_bound"] is True
    assert receipt["simulator_action_taken"] is False


def test_backend_verifier_preserves_existing_receipt_directory(tmp_path: Path) -> None:
    _, manifest_path = _backend_manifest(tmp_path)
    out_dir = tmp_path / "verification"
    out_dir.mkdir()
    marker = _write(out_dir / "marker.txt", "preserve\n")

    assert VERIFIER_MODULE.main(
        [
            "--backend-identity-manifest",
            str(manifest_path),
            "--out-dir",
            str(out_dir),
        ]
    ) == 2
    assert marker.read_text(encoding="utf-8") == "preserve\n"
    assert not (out_dir / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json").exists()
