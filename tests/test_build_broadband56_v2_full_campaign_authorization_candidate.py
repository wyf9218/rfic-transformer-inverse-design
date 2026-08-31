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
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    PRODUCTION_BACKEND_ID,
    PUBLIC_EVIDENCE_FIELDS,
    validate_full_campaign_candidate,
)
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import (
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    PROFILE_EXECUTION_MODE,
    PROFILE_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_v2_full_campaign_authorization_candidate.py"
SPEC = importlib.util.spec_from_file_location("full_campaign_candidate_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MANIFEST_SCRIPT = ROOT / "scripts" / "build_broadband56_v2_private_backend_identity.py"
MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "candidate_test_manifest_builder", MANIFEST_SCRIPT
)
assert MANIFEST_SPEC and MANIFEST_SPEC.loader
MANIFEST_MODULE = importlib.util.module_from_spec(MANIFEST_SPEC)
MANIFEST_SPEC.loader.exec_module(MANIFEST_MODULE)
VERIFY_SCRIPT = ROOT / "scripts" / "verify_broadband56_v2_private_backend_identity.py"
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "candidate_test_manifest_verifier", VERIFY_SCRIPT
)
assert VERIFY_SPEC and VERIFY_SPEC.loader
VERIFY_MODULE = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY_MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


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


def _args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(MODULE, "ROOT", repo)
    for index, relative in enumerate(PUBLIC_EVIDENCE_FIELDS.values()):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"evidence-{index}\n", encoding="utf-8")
    private = tmp_path / "private"
    files = {
        role: _write(private / "identity" / role, f"identity: {role}\n")
        for role in (*REQUIRED_SCRIPT_ROLES, *REQUIRED_RUNTIME_ROLES)
    }
    files["production_stage_backend"].chmod(0o755)
    files["python_executable"].chmod(0o755)
    files["emx_wrapper"].chmod(0o755)
    files["stage_execution_profile"] = _write(
        files["stage_execution_profile"],
        _execution_profile(),
    )
    historical = [
        _write(
            private / "history" / f"backend_{index}.json",
            {"overall_status": "PASS", "receipt_id": index},
        )
        for index in (1, 2)
    ]
    historical_gds = _write(
        private / "history" / "gds.json",
        {"overall_status": "PASS", "receipt_id": "gds"},
    )
    preparation_hashes = {
        "preparation_receipt_sha256": "1" * 64,
        "private_configuration_sha256": _sha(files["private_configuration"]),
        "historical_configuration_sha256": "3" * 64,
        "operational_policy_approval_receipt_sha256": "9" * 64,
    }
    manifest_dir = private / "manifest"
    manifest_argv = [
        "--out-dir",
        str(manifest_dir),
        "--preparation-receipt-sha256",
        preparation_hashes["preparation_receipt_sha256"],
        "--private-configuration-sha256",
        preparation_hashes["private_configuration_sha256"],
        "--historical-configuration-sha256",
        preparation_hashes["historical_configuration_sha256"],
        "--operational-policy-approval-receipt-sha256",
        preparation_hashes["operational_policy_approval_receipt_sha256"],
    ]
    for role in REQUIRED_SCRIPT_ROLES:
        manifest_argv.extend(
            [f"--script-{role.replace('_', '-')}", str(files[role])]
        )
    for role in REQUIRED_RUNTIME_ROLES:
        manifest_argv.extend(
            [f"--runtime-{role.replace('_', '-')}", str(files[role])]
        )
    for receipt in historical:
        manifest_argv.extend(
            ["--historical-backend-pass-receipt", str(receipt)]
        )
    manifest_argv.extend(
        ["--historical-gds-identity-pass-receipt", str(historical_gds)]
    )
    assert MANIFEST_MODULE.main(manifest_argv) == 0
    manifest_path = manifest_dir / "PRIVATE_BACKEND_IDENTITY_MANIFEST.json"
    verification_dir = private / "verification"
    assert VERIFY_MODULE.main(
        [
            "--backend-identity-manifest",
            str(manifest_path),
            "--out-dir",
            str(verification_dir),
        ]
    ) == 0
    verification_path = (
        verification_dir / "PRIVATE_BACKEND_IDENTITY_VERIFICATION_RECEIPT.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    argv = [
        "--out-dir",
        str(tmp_path / "candidate"),
        "--generated-utc",
        "2026-08-30T20:30:00Z",
        "--preparation-receipt-sha256",
        preparation_hashes["preparation_receipt_sha256"],
        "--private-configuration-sha256",
        preparation_hashes["private_configuration_sha256"],
        "--historical-configuration-sha256",
        preparation_hashes["historical_configuration_sha256"],
        "--campaign-contract-frozen-sha256",
        "4" * 64,
        "--primary-bins-frozen-sha256",
        "5" * 64,
        "--secondary-coverage-frozen-sha256",
        "6" * 64,
        "--geometry-bounds-frozen-sha256",
        "7" * 64,
        "--phase-plan-frozen-sha256",
        "8" * 64,
        "--operational-policy-approval-receipt-sha256",
        preparation_hashes["operational_policy_approval_receipt_sha256"],
        "--backend-identity-manifest",
        str(manifest_path),
        "--backend-identity-manifest-sha256",
        _sha(manifest_path),
        "--backend-identity-verification-receipt",
        str(verification_path),
        "--backend-identity-verification-receipt-sha256",
        _sha(verification_path),
    ]
    for role, record in manifest["script_identities"].items():
        argv.extend([f"--{role.replace('_', '-')}-sha256", record["sha256"]])
    for role in ("resource_probe", "python_executable"):
        argv.extend(
            [
                f"--{role.replace('_', '-')}-sha256",
                manifest["runtime_identities"][role]["sha256"],
            ]
        )
    argv.extend(
        [
            "--historical-gds-identity-pass-receipt-sha256",
            manifest["historical_gds_identity_pass_receipt"]["sha256"],
        ]
    )
    for receipt in manifest["historical_backend_pass_receipts"]:
        argv.extend(
            [
                "--historical-backend-pass-receipt",
                f"{receipt['sha256']}:{receipt['size_bytes']}",
            ]
        )
    return argv


def test_builds_public_safe_exact_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)

    assert MODULE.main(argv) == 0

    out_dir = tmp_path / "candidate"
    paths = list(out_dir.glob("BROADBAND56_V2_FULL_CAMPAIGN_AUTHORIZATION_CANDIDATE_*.json"))
    assert len(paths) == 1
    candidate = json.loads(paths[0].read_text())
    assert not validate_full_campaign_candidate(candidate, repository_root=MODULE.ROOT)
    assert candidate["execution_effect_of_candidate_file"] == "NONE_REQUEST_ONLY"
    assert candidate["automatic_campaign_execution_authorized"] is False
    runtime = candidate["runtime_and_backend_identity"]
    assert runtime["candidate_gds_index_builder_sha256"]
    assert runtime["gds_physical_identity_auditor_sha256"]
    assert runtime["gds_physical_identity_module_sha256"]
    assert runtime["resource_gate_auditor_sha256"]
    assert runtime["resource_probe_sha256"]
    assert runtime["python_executable_sha256"]
    serialized = json.dumps(candidate)
    assert "/volumes/" not in serialized
    assert _sha(paths[0]) in (out_dir / "SHA256SUMS.txt").read_text()


def test_rejects_existing_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    (tmp_path / "candidate").mkdir()

    assert MODULE.main(argv) == 2


def test_rejects_too_few_historical_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    marker = argv.index("--historical-backend-pass-receipt")
    del argv[marker : marker + 2]

    assert MODULE.main(argv) == 2


def test_rejects_non_timezone_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    argv[argv.index("--generated-utc") + 1] = "2026-08-30T20:30:00"

    assert MODULE.main(argv) == 2


def test_rejects_backend_role_sha_not_bound_by_verified_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    marker = argv.index("--resource-gate-auditor-sha256")
    argv[marker + 1] = "f" * 64

    assert MODULE.main(argv) == 2
    assert not (tmp_path / "candidate").exists()


def test_rejects_modified_backend_verification_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    marker = argv.index("--backend-identity-verification-receipt")
    receipt_path = Path(argv[marker + 1])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["checks"]["all_stage_commands_shell_free"] = False
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    sha_marker = argv.index("--backend-identity-verification-receipt-sha256")
    argv[sha_marker + 1] = _sha(receipt_path)

    assert MODULE.main(argv) == 2
    assert not (tmp_path / "candidate").exists()


def test_rejects_manifest_bound_file_drift_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _args(tmp_path, monkeypatch)
    marker = argv.index("--backend-identity-manifest")
    manifest_path = Path(argv[marker + 1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gate_path = Path(
        manifest["script_identities"]["resource_gate_auditor"]["path"]
    )
    gate_path.write_text("identity drift\n", encoding="utf-8")

    assert MODULE.main(argv) == 2
    assert not (tmp_path / "candidate").exists()
