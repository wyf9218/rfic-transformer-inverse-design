from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

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
    validate_backend_identity_manifest,
    validate_stage_receipt,
    validate_stage_receipt_chain,
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


def _backend_manifest(tmp_path: Path) -> tuple[dict, Path]:
    files = {
        role: _write(tmp_path / "files" / role, f"{role}\n")
        for role in (*REQUIRED_SCRIPT_ROLES, *REQUIRED_RUNTIME_ROLES)
    }
    files["production_stage_backend"].chmod(0o755)
    files["emx_wrapper"].chmod(0o755)
    scripts = {role: _identity(files[role]) for role in REQUIRED_SCRIPT_ROLES}
    runtimes = {role: _identity(files[role]) for role in REQUIRED_RUNTIME_ROLES}
    scripts["production_stage_backend"]["executable"] = True
    runtimes["emx_wrapper"]["executable"] = True
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
            {"overall_status": "PASS", "sha256": "5" * 64, "size_bytes": 100},
            {"overall_status": "PASS", "sha256": "6" * 64, "size_bytes": 200},
        ],
        "historical_gds_identity_pass_receipt": {
            "overall_status": "PASS",
            "sha256": "7" * 64,
            "size_bytes": 300,
        },
    }
    path = _write(tmp_path / "backend_manifest.json", manifest)
    return manifest, path


def _stage_receipt(tmp_path: Path, *, target: int = 1) -> dict:
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
    artifact_paths = {
        role: (
            raw_path
            if role == "raw_products_receipt"
            else _write(tmp_path / "artifacts" / role, f"{role}\n")
        )
        for role in STAGE_ARTIFACT_FIELDS
    }
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
        "stage": "GOLDEN",
        "terminal_state": "GOLDEN_COMPLETE",
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
