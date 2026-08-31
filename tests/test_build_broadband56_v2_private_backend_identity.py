from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (
    BACKEND_MANIFEST_SCHEMA,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    validate_backend_identity_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_v2_private_backend_identity.py"
SPEC = importlib.util.spec_from_file_location("private_backend_identity_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VERIFIER_SCRIPT = ROOT / "scripts" / "verify_broadband56_v2_private_backend_identity.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "private_backend_identity_verifier_for_builder_test", VERIFIER_SCRIPT
)
assert VERIFIER_SPEC and VERIFIER_SPEC.loader
VERIFIER_MODULE = importlib.util.module_from_spec(VERIFIER_SPEC)
VERIFIER_SPEC.loader.exec_module(VERIFIER_MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")
    return path


def _build_args(tmp_path: Path) -> tuple[list[str], dict[str, Path], Path]:
    files = {
        role: _write(tmp_path / "private" / role, f"{role}\n")
        for role in (*REQUIRED_SCRIPT_ROLES, *REQUIRED_RUNTIME_ROLES)
    }
    files["production_stage_backend"].chmod(0o755)
    files["emx_wrapper"].chmod(0o755)
    historical_one = _write(
        tmp_path / "historical" / "backend_one.json",
        {"overall_status": "PASS", "receipt_id": "one"},
    )
    historical_two = _write(
        tmp_path / "historical" / "backend_two.json",
        {"overall_status": "PASS", "receipt_id": "two"},
    )
    historical_gds = _write(
        tmp_path / "historical" / "gds.json",
        {"overall_status": "PASS", "receipt_id": "gds"},
    )
    out_dir = tmp_path / "private_manifest"
    argv = [
        "--out-dir",
        str(out_dir),
        "--preparation-receipt-sha256",
        "1" * 64,
        "--private-configuration-sha256",
        "2" * 64,
        "--historical-configuration-sha256",
        "3" * 64,
        "--operational-policy-approval-receipt-sha256",
        "4" * 64,
    ]
    for role in REQUIRED_SCRIPT_ROLES:
        argv.extend([f"--script-{role.replace('_', '-')}", str(files[role])])
    for role in REQUIRED_RUNTIME_ROLES:
        argv.extend([f"--runtime-{role.replace('_', '-')}", str(files[role])])
    argv.extend(
        [
            "--historical-backend-pass-receipt",
            str(historical_one),
            "--historical-backend-pass-receipt",
            str(historical_two),
            "--historical-gds-identity-pass-receipt",
            str(historical_gds),
        ]
    )
    return argv, files, out_dir


def test_builder_writes_complete_self_validating_manifest(tmp_path: Path) -> None:
    argv, files, out_dir = _build_args(tmp_path)

    assert MODULE.main(argv) == 0

    manifest_path = out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == BACKEND_MANIFEST_SCHEMA
    assert manifest["contract_fingerprint_sha256"] == SCIENTIFIC_CONTRACT_FINGERPRINT
    assert manifest["simulator_action_taken"] is False
    assert manifest["manifest_effect"] == "IDENTITY_ONLY_NO_EXECUTION"
    assert set(manifest["script_identities"]) == set(REQUIRED_SCRIPT_ROLES)
    assert set(manifest["runtime_identities"]) == set(REQUIRED_RUNTIME_ROLES)
    assert manifest["script_identities"]["full_band_s4p_qa_builder"]["sha256"] == _sha(
        files["full_band_s4p_qa_builder"]
    )
    assert manifest["script_identities"]["full_band_s4p_qa_module"]["sha256"] == _sha(
        files["full_band_s4p_qa_module"]
    )
    assert manifest["script_identities"]["candidate_gds_index_builder"]["sha256"] == _sha(
        files["candidate_gds_index_builder"]
    )
    assert manifest["script_identities"]["gds_physical_identity_auditor"]["sha256"] == _sha(
        files["gds_physical_identity_auditor"]
    )
    assert manifest["script_identities"]["gds_physical_identity_module"]["sha256"] == _sha(
        files["gds_physical_identity_module"]
    )
    assert set(manifest["stage_commands"]) == {stage.name for stage in STAGES}
    assert all(
        command["argv"][0]
        == manifest["script_identities"]["production_stage_backend"]["path"]
        and command["identity_argv_index"] == 0
        and command["shell_used"] is False
        for command in manifest["stage_commands"].values()
    )
    assert validate_backend_identity_manifest(manifest, verify_files=True) == []
    assert (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{_sha(manifest_path)}  {manifest_path.name}\n"
    )


def test_builder_is_no_clobber(tmp_path: Path) -> None:
    argv, _, out_dir = _build_args(tmp_path)
    assert MODULE.main(argv) == 0
    before = (out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json").read_bytes()

    assert MODULE.main(argv) == 2
    assert (out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json").read_bytes() == before


def test_builder_output_passes_independent_verifier(tmp_path: Path) -> None:
    argv, _, out_dir = _build_args(tmp_path)
    assert MODULE.main(argv) == 0
    verification_dir = tmp_path / "verification"

    assert VERIFIER_MODULE.main(
        [
            "--backend-identity-manifest",
            str(out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"),
            "--out-dir",
            str(verification_dir),
        ]
    ) == 0

    receipt = json.loads(
        (
            verification_dir
            / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["overall_status"] == "PASS"
    assert receipt["errors"] == []
    assert receipt["simulator_action_taken"] is False


def test_builder_rejects_non_sha_preparation_binding(tmp_path: Path) -> None:
    argv, _, out_dir = _build_args(tmp_path)
    index = argv.index("--preparation-receipt-sha256") + 1
    argv[index] = "not-a-digest"

    assert MODULE.main(argv) == 2
    assert not out_dir.exists()


def test_builder_rejects_non_executable_stage_backend(tmp_path: Path) -> None:
    argv, files, out_dir = _build_args(tmp_path)
    files["production_stage_backend"].chmod(0o644)

    assert MODULE.main(argv) == 2
    assert not out_dir.exists()


def test_builder_rejects_non_pass_historical_receipt(tmp_path: Path) -> None:
    argv, _, out_dir = _build_args(tmp_path)
    index = argv.index("--historical-backend-pass-receipt") + 1
    Path(argv[index]).write_text(
        json.dumps({"overall_status": "FAIL"}) + "\n",
        encoding="utf-8",
    )

    assert MODULE.main(argv) == 2
    assert not out_dir.exists()


def test_builder_rejects_duplicate_historical_receipt_bytes(tmp_path: Path) -> None:
    argv, _, out_dir = _build_args(tmp_path)
    indexes = [
        index + 1
        for index, value in enumerate(argv)
        if value == "--historical-backend-pass-receipt"
    ]
    Path(argv[indexes[1]]).write_bytes(Path(argv[indexes[0]]).read_bytes())

    assert MODULE.main(argv) == 2
    assert not out_dir.exists()


def test_written_manifest_detects_later_identity_drift(tmp_path: Path) -> None:
    argv, files, out_dir = _build_args(tmp_path)
    assert MODULE.main(argv) == 0
    manifest = json.loads(
        (out_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    files["full_band_s4p_qa_module"].write_text("changed\n", encoding="utf-8")

    errors = validate_backend_identity_manifest(manifest, verify_files=True)

    assert any(
        "full_band_s4p_qa_module.sha256 mismatches file" in error
        for error in errors
    )
