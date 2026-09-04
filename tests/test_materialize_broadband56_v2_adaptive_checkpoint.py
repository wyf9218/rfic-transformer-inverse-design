from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "materialize_broadband56_v2_adaptive_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("adaptive_checkpoint_materializer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _load_test_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_checkpoint_and_delivery_require_the_actual_producer_calibre_field(tmp_path):
    raw_fixture = _load_test_module("funnel_schema_raw_fixture", "tests/test_finalize_broadband56_balanced200k_raw_products.py")
    raw = raw_fixture._load_module()
    checkpoint = _load_test_module("funnel_schema_checkpoint", "scripts/audit_broadband56_balanced200k_checkpoint.py")
    delivery = _load_test_module("funnel_schema_delivery", "scripts/audit_broadband56_balanced200k_final_delivery.py")
    assert "calibre_blocking_failures" in raw.FAILURE_FUNNEL_ORDER
    assert "calibre_blocking_failures" in delivery.FAILURE_FUNNEL_STAGES
    path = tmp_path / "funnel.csv"
    counts = {field: 0 for field in raw.FAILURE_FUNNEL_ORDER}
    counts.update(raw_geometry_candidates=200_000, accepted_geometries=200_000)
    raw._write_failure_funnel(path, counts)
    checks = []
    checkpoint._audit_failure_funnel(path, 200_000, checks)
    assert all(item["pass"] for item in checks), checks
    delivery._audit_failure_funnel(path)
    path.write_text(path.read_text().replace("calibre_blocking_failures", "calibre_failures"))
    checks = []
    checkpoint._audit_failure_funnel(path, 200_000, checks)
    assert any(item["name"] == "failure_funnel_all_stages" and not item["pass"] for item in checks)
    with pytest.raises(delivery.DeliveryAuditError, match="omits stages"):
        delivery._audit_failure_funnel(path)


@pytest.mark.parametrize("tamper_s4p", [False, True])
def test_first_production_boundary_runs_real_software_audits_without_solver(tmp_path, monkeypatch, tamper_s4p):
    # The S4P and receipts are synthetic fixtures, not fresh physical labels.
    raw_fixture = _load_test_module("first_checkpoint_raw_fixture", "tests/test_finalize_broadband56_balanced200k_raw_products.py")
    raw = raw_fixture._load_module()
    audit = _load_test_module("first_checkpoint_auditor", "scripts/audit_broadband56_balanced200k_checkpoint.py")
    contract_fixture = _load_test_module("first_checkpoint_contract_fixture", "tests/test_broadband56_balanced200k_contract.py")
    fixture = raw_fixture._fixture(tmp_path)
    # This integration fixture has a temporary self-consistent contract rather
    # than the private production contract.  Bind the materializer to that
    # fixture identity for this test only; production entry-point tests retain
    # the frozen campaign fingerprint.
    assert fixture["fingerprint"] != MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT
    monkeypatch.setattr(
        MODULE,
        "SCIENTIFIC_CONTRACT_FINGERPRINT",
        fixture["fingerprint"],
    )
    attempt = fixture["attempts"][0]
    raw_fixture._write_csv(fixture["ledger"], [attempt])
    raw_fixture._write_csv(fixture["features"], raw_fixture._feature_rows([attempt]))
    bounds = tmp_path / "geometry_bounds.json"
    bounds.write_text(json.dumps(contract_fixture.geometry_bounds_payload(
        bounds=contract_fixture._test_geometry_bounds(), contract_fingerprint_sha256=fixture["fingerprint"])))
    cumulative = {}
    for field in MODULE.STAGE_PROGRESS_ARTIFACT_FIELDS:
        if field == "attempt_ledger":
            path = fixture["ledger"]
        elif field == "long_features":
            path = fixture["features"]
        else:
            path = tmp_path / (field + ".csv")
            path.write_text("geometry_sha256\n" + attempt["geometry_sha256"] + "\n")
        cumulative[field] = _identity(path)
    progress = {"accepted_after": 1, "round_cumulative_inputs": cumulative}
    progress_path = tmp_path / "STAGE_PROGRESS_RECEIPT.json"
    progress_path.write_text(json.dumps(progress))
    authorization_path = tmp_path / "FULL_CAMPAIGN_AUTHORIZATION_RECEIPT.json"
    authorization_path.write_text("{}\n", encoding="utf-8")
    backend = {"script_identities": {"raw_products_finalizer": _identity(Path(raw.__file__)),
                                     "checkpoint_auditor": _identity(Path(audit.__file__))}}
    calls = []
    def deny_process(*args, **kwargs):
        raise AssertionError("no simulator or subprocess permitted by this test")
    monkeypatch.setattr(subprocess, "Popen", deny_process)
    def run_in_process(argv, out, label):
        assert argv[0] == sys.executable
        module = raw if label == "raw_products" else audit
        assert argv[1] == module.__file__
        calls.append(argv)
        if module.main(argv[2:]) != 0:
            raise MODULE.AdaptiveCheckpointError("in-process delegated audit failed")
        return {"return_code": 0, "shell_used": False, "test_only_in_process": True}
    monkeypatch.setattr(MODULE, "_run_bound_command", run_in_process)
    if tamper_s4p:
        Path(attempt["s4p_path"]).write_text("tampered synthetic S4P")
    out = tmp_path / "materializer"
    out.mkdir()
    kwargs = dict(out_dir=out, stage="PILOT_32", checkpoint_count=1,
        progress_records=[(progress_path, progress)], contract_path=fixture["contract"],
        production_config_path=fixture["production_config"],
        authorization_path=authorization_path,
        geometry_bounds_path=bounds, backend=backend)
    if tamper_s4p:
        with pytest.raises(MODULE.AdaptiveCheckpointError, match="delegated audit failed"):
            MODULE._build_boundary_checkpoint(**kwargs)
        assert not (out / "checkpoint/CHECKPOINT_RECEIPT.json").exists()
    else:
        checkpoint, _, _ = MODULE._build_boundary_checkpoint(**kwargs)
        assert calls[0][calls[0].index("--full-campaign-receipt") + 1] == str(
            authorization_path
        )
        assert calls[1][calls[1].index("--audit-mode") + 1] == "golden"
        assert calls[1][calls[1].index("--expected-accepted") + 1] == "1"
        MODULE._validate_checkpoint(checkpoint_dir=checkpoint, expected_accepted=1)
        status = json.loads((checkpoint / "CHECKPOINT_STATUS.json").read_text())
        assert status["accepted_geometries"] == status["s4p_artifacts"] == 1
        assert status["geometry_frequency_rows"] == 56


def _write_checkpoint(root: Path, *, accepted: int) -> Path:
    root.mkdir(parents=True)
    input_fields = (
        "contract",
        "geometry_bounds",
        "accepted_geometries",
        "long_features",
        "artifact_index",
        "failure_funnel",
    )
    output_fields = (
        "coverage_cells",
        "coverage_by_frequency",
        "coverage_marginals",
        "coverage_pairwise",
        "geometry_coverage_summary",
        "geometry_coverage_marginals",
        "geometry_coverage_pairwise",
        "coverage_summary",
        "failure_funnel",
    )
    inputs = {}
    for field in input_fields:
        path = root / f"input_{field}.txt"
        path.write_text(f"{field}\n", encoding="utf-8")
        inputs[field] = _identity(path)
    outputs = {}
    for field in output_fields:
        path = root / f"output_{field}.txt"
        path.write_text(f"{field}\n", encoding="utf-8")
        outputs[field] = _identity(path)
    audit_mode = MODULE._expected_checkpoint_mode(accepted)
    if accepted == MODULE.TARGET_ACCEPTED_GEOMETRIES:
        checkpoint_state = "COMPLETE_200K"
    elif audit_mode == "golden":
        checkpoint_state = "GOLDEN_COMPLETE"
    elif audit_mode == "pilot":
        checkpoint_state = f"PILOT_{accepted}_COMPLETE"
    elif audit_mode == "checkpoint":
        checkpoint_state = "CHECKPOINT_COMPLETE"
    else:
        checkpoint_state = f"ROUND_{accepted}_COMPLETE"
    receipt = {
        "overall_status": "PASS",
        "decision": "USE_CHECKPOINT",
        "campaign_id": MODULE.CAMPAIGN_ID,
        "contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "expected_accepted": accepted,
        "audit_mode": audit_mode,
        "checks": [{"name": "fixture", "pass": True}],
        "inputs": inputs,
        "outputs": outputs,
    }
    status = {
        "campaign_id": MODULE.CAMPAIGN_ID,
        "contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "audit_mode": audit_mode,
        "checkpoint_status": checkpoint_state,
        "accepted_geometries": accepted,
        "s4p_artifacts": accepted,
        "geometry_frequency_rows": accepted * MODULE.FREQUENCY_POINTS,
    }
    (root / "CHECKPOINT_RECEIPT.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )
    (root / "CHECKPOINT_STATUS.json").write_text(
        json.dumps(status) + "\n",
        encoding="utf-8",
    )
    receipt["outputs"]["checkpoint_status"] = _identity(
        root / "CHECKPOINT_STATUS.json"
    )
    (root / "CHECKPOINT_RECEIPT.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )
    return root


def test_exact_checkpoint_validator_rejects_wrong_feature_grain(tmp_path: Path) -> None:
    checkpoint = _write_checkpoint(tmp_path / "checkpoint", accepted=50_000)
    status_path = checkpoint / "CHECKPOINT_STATUS.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["geometry_frequency_rows"] -= 1
    status_path.write_text(json.dumps(status) + "\n", encoding="utf-8")

    try:
        MODULE._validate_checkpoint(
            checkpoint_dir=checkpoint,
            expected_accepted=50_000,
        )
    except MODULE.AdaptiveCheckpointError as exc:
        assert "not exact PASS evidence" in str(exc)
    else:
        raise AssertionError("wrong 56-point feature grain must fail closed")


def test_exact_checkpoint_validator_rejects_changed_output_bytes(tmp_path: Path) -> None:
    checkpoint = _write_checkpoint(tmp_path / "checkpoint", accepted=55_000)
    (checkpoint / "output_coverage_summary.txt").write_text(
        "changed\n",
        encoding="utf-8",
    )

    try:
        MODULE._validate_checkpoint(
            checkpoint_dir=checkpoint,
            expected_accepted=55_000,
        )
    except MODULE.AdaptiveCheckpointError as exc:
        assert "not exact PASS evidence" in str(exc)
    else:
        raise AssertionError("changed checkpoint output must fail closed")


def test_prior_materializer_reuses_only_exact_hash_bound_checkpoint(
    tmp_path: Path,
) -> None:
    campaign_root = tmp_path / "campaign"
    role_dir = campaign_root / "stages" / "phase_b_attempt_1" / "roles" / "00_checkpoint"
    checkpoint = _write_checkpoint(role_dir / "checkpoint", accepted=50_000)
    checkpoint_receipt = checkpoint / "CHECKPOINT_RECEIPT.json"
    materializer_receipt = {
        "overall_status": "PASS",
        "stage": "PHASE_B",
        "checkpoint_accepted": 50_000,
        "backend_identity_manifest": {"sha256": "1" * 64},
        "full_campaign_authorization_receipt": {"sha256": "2" * 64},
        "checkpoint_receipt": _identity(checkpoint_receipt),
        "simulator_action_taken": False,
    }
    receipt_path = role_dir / MODULE.RECEIPT_NAME
    receipt_path.write_text(json.dumps(materializer_receipt) + "\n", encoding="utf-8")

    resolved, source = MODULE._checkpoint_from_prior_materializer(
        campaign_root=campaign_root,
        stage="PHASE_B",
        checkpoint_count=50_000,
        backend_sha="1" * 64,
        authorization_sha="2" * 64,
    )

    assert resolved == checkpoint.resolve()
    assert source["kind"] == "PRIOR_FROZEN_CHECKPOINT_MATERIALIZER"
    assert source["materializer_receipt"]["sha256"] == _sha(receipt_path)


def test_prior_materializer_rejects_changed_checkpoint_bytes(tmp_path: Path) -> None:
    campaign_root = tmp_path / "campaign"
    role_dir = campaign_root / "stages" / "phase_b_attempt_1" / "roles" / "00_checkpoint"
    checkpoint = _write_checkpoint(role_dir / "checkpoint", accepted=50_000)
    checkpoint_receipt = checkpoint / "CHECKPOINT_RECEIPT.json"
    materializer_receipt = {
        "overall_status": "PASS",
        "stage": "PHASE_B",
        "checkpoint_accepted": 50_000,
        "backend_identity_manifest": {"sha256": "1" * 64},
        "full_campaign_authorization_receipt": {"sha256": "2" * 64},
        "checkpoint_receipt": _identity(checkpoint_receipt),
        "simulator_action_taken": False,
    }
    receipt_path = role_dir / MODULE.RECEIPT_NAME
    receipt_path.write_text(json.dumps(materializer_receipt) + "\n", encoding="utf-8")
    checkpoint_receipt.write_text(
        checkpoint_receipt.read_text(encoding="utf-8") + "changed\n",
        encoding="utf-8",
    )

    try:
        MODULE._checkpoint_from_prior_materializer(
            campaign_root=campaign_root,
            stage="PHASE_B",
            checkpoint_count=50_000,
            backend_sha="1" * 64,
            authorization_sha="2" * 64,
        )
    except MODULE.AdaptiveCheckpointError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("changed checkpoint receipt must fail closed")


def test_checkpoint_modes_distinguish_golden_pilot_and_formal_counts(
    tmp_path: Path,
) -> None:
    assert MODULE._expected_checkpoint_mode(1) == "golden"
    assert MODULE._expected_checkpoint_mode(32) == "pilot"
    assert MODULE._expected_checkpoint_mode(100) == "checkpoint"
    assert MODULE._expected_checkpoint_mode(1_000) == "checkpoint"
    assert MODULE._expected_checkpoint_mode(55_000) == "round"

    for accepted in (1, 32, 100, 1_000, 55_000):
        checkpoint = _write_checkpoint(
            tmp_path / f"checkpoint_{accepted}",
            accepted=accepted,
        )
        MODULE._validate_checkpoint(
            checkpoint_dir=checkpoint,
            expected_accepted=accepted,
        )


def test_main_refuses_nonempty_role_output_before_any_work(tmp_path: Path) -> None:
    out_dir = tmp_path / "role"
    out_dir.mkdir()
    (out_dir / "prior_failure.json").write_text("{}\n", encoding="utf-8")

    args = argparse.Namespace(
        stage="PHASE_B",
        campaign_root=str(tmp_path / "campaign"),
        current_accepted=50_000,
        contract=str(tmp_path / "contract.json"),
        production_config=str(tmp_path / "config.yaml"),
        geometry_bounds=str(tmp_path / "bounds.json"),
        backend_identity_manifest=str(tmp_path / "backend.json"),
        full_campaign_receipt=str(tmp_path / "authorization.json"),
        out_dir=str(out_dir),
    )
    original = MODULE._parse_args
    MODULE._parse_args = lambda argv=None: args
    try:
        assert MODULE.main([]) == 2
    finally:
        MODULE._parse_args = original
