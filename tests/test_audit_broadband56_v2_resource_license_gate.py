from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    contract_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"
NOW = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def _load_module():
    path = ROOT / "scripts" / "audit_broadband56_v2_resource_license_gate.py"
    spec = importlib.util.spec_from_file_location(
        "audit_broadband56_v2_resource_license_gate", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _fixture_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    contract = json.loads(PUBLIC_CONTRACT.read_text(encoding="utf-8"))
    contract["inherited_contract_evidence"] = {
        "previous_campaign_id": "fixture_v1",
        "previous_contract_sha256": "a" * 64,
        "previous_config_sha256": "b" * 64,
        "production_config_sha256": "c" * 64,
        "private_runtime_paths_not_for_publication": True,
    }
    contract["preparation_status"] = "PASS"
    contract["contract_fingerprint_sha256"] = contract_fingerprint(contract)
    fingerprint = str(contract["contract_fingerprint_sha256"])
    contract_path = tmp_path / "campaign_contract_frozen.json"
    _write_json(contract_path, contract)

    preparation = tmp_path / "PREPARATION_RECEIPT.json"
    _write_json(
        preparation,
        {
            "overall_status": "PASS",
            "decision": "PREPARED_FOR_GOLDEN_GATE",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "checks": [{"name": "fixture", "pass": True}],
        },
    )

    authorization = tmp_path / "GOLDEN_AUTHORIZATION_RECEIPT.json"
    _write_json(
        authorization,
        {
            "schema": "rfic_transformer.broadband56_v2_golden_authorization.v1",
            "overall_status": "PASS",
            "decision": "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY",
            "approved_by": "unit-test-project-owner",
            "approved_utc": NOW.isoformat(),
            "approval_reference": "explicit unit-test one-golden approval",
            "approved_candidate": {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "sha256": "d" * 64,
            },
            "resource_and_license_gate_authorized": True,
            "one_golden_authorized": True,
            "cadence_authorized_for_one_golden": True,
            "calibre_authorized_for_one_golden": True,
            "emx_authorized_for_one_golden": True,
            "simulator_geometry_limit": 1,
            "pilot_32_authorized": False,
            "pilot_1000_authorized": False,
            "queue_authorized": False,
            "supervisor_authorized": False,
            "phase_a_authorized": False,
            "phase_b_authorized": False,
            "phase_c_authorized": False,
            "campaign_200k_authorized": False,
            "checks": [{"name": "fixture", "pass": True}],
            "execution_effect": "NONE_RECORD_ONLY",
        },
    )

    snapshot = tmp_path / "resource_snapshot.json"
    _write_snapshot(snapshot, fingerprint)
    return contract_path, preparation, authorization, snapshot, fingerprint


def _write_snapshot(
    path: Path,
    fingerprint: str,
    *,
    captured_utc: datetime = NOW,
    available: bool = True,
) -> None:
    _write_json(
        path,
        {
            "schema": "rfic_transformer.broadband56_v2_resource_license_snapshot.v1",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "captured_utc": captured_utc.isoformat(),
            "resources": {
                "load_1m": 1.0 if available else 9.0,
                "load_limit": 4.0,
                "load_gate_pass": available,
                "available_memory_bytes": 64_000_000_000,
                "minimum_available_memory_bytes": 8_000_000_000,
                "memory_gate_pass": True,
                "available_storage_bytes": 1_000_000_000_000,
                "minimum_available_storage_bytes": 100_000_000_000,
                "storage_gate_pass": True,
                "resources_available": available,
            },
            "licenses": {
                "cadence_available": True,
                "calibre_available": True,
                "emx_available": True,
                "license_gate_pass": True,
            },
            "isolation": {
                "duplicate_supervisor_count": 0,
                "duplicate_queue_count": 0,
                "conflicting_solver_process_count": 0,
                "output_path_collision": False,
                "isolation_gate_pass": True,
            },
        },
    )


def _audit(module, tmp_path: Path, *, out_name: str = "gate"):
    contract, preparation, authorization, snapshot, _ = _fixture_paths(tmp_path)
    return module.audit_resource_license_gate(
        frozen_contract_path=contract,
        preparation_receipt_path=preparation,
        authorization_receipt_path=authorization,
        snapshot_path=snapshot,
        out_dir=tmp_path / out_name,
        max_age_seconds=900,
        evaluated_utc=NOW,
    )


def test_pass_authorizes_exactly_one_golden_and_nothing_later(tmp_path: Path) -> None:
    module = _load_module()

    receipt = _audit(module, tmp_path)

    assert receipt["overall_status"] == "PASS"
    assert receipt["decision"] == "READY_FOR_ONE_GOLDEN_ONLY"
    assert receipt["golden_launch_authorized"] is True
    assert receipt["simulator_geometry_limit"] == 1
    assert receipt["pilot_32_launch_authorized"] is False
    assert receipt["pilot_1000_launch_authorized"] is False
    assert receipt["queue_launch_authorized"] is False
    assert receipt["supervisor_launch_authorized"] is False
    assert receipt["campaign_launch_authorized"] is False
    assert receipt["valid_until_utc"] == (NOW + timedelta(seconds=900)).isoformat()
    gate = tmp_path / "gate" / "RESOURCE_LICENSE_GATE.json"
    assert gate.is_file()
    assert (tmp_path / "gate" / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{_sha256(gate)}  {gate.name}\n"
    )


def test_wait_authorizes_no_simulator_action(tmp_path: Path) -> None:
    module = _load_module()
    contract, preparation, authorization, snapshot, fingerprint = _fixture_paths(tmp_path)
    _write_snapshot(snapshot, fingerprint, available=False)

    receipt = module.audit_resource_license_gate(
        frozen_contract_path=contract,
        preparation_receipt_path=preparation,
        authorization_receipt_path=authorization,
        snapshot_path=snapshot,
        out_dir=tmp_path / "wait",
        max_age_seconds=900,
        evaluated_utc=NOW,
    )

    assert receipt["overall_status"] == "WAIT"
    assert receipt["decision"] == "PREPARED_WAITING_FOR_RESOURCE"
    assert receipt["golden_launch_authorized"] is False
    assert receipt["simulator_geometry_limit"] == 0


def test_stale_snapshot_fails_closed(tmp_path: Path) -> None:
    module = _load_module()
    contract, preparation, authorization, snapshot, fingerprint = _fixture_paths(tmp_path)
    _write_snapshot(snapshot, fingerprint, captured_utc=NOW - timedelta(minutes=16))

    receipt = module.audit_resource_license_gate(
        frozen_contract_path=contract,
        preparation_receipt_path=preparation,
        authorization_receipt_path=authorization,
        snapshot_path=snapshot,
        out_dir=tmp_path / "stale",
        max_age_seconds=900,
        evaluated_utc=NOW,
    )

    assert receipt["overall_status"] == "FAIL"
    assert receipt["golden_launch_authorized"] is False
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert "snapshot_is_fresh" in failed


def test_one_golden_receipt_cannot_escalate_to_pilot(tmp_path: Path) -> None:
    module = _load_module()
    contract, preparation, authorization, snapshot, _ = _fixture_paths(tmp_path)
    payload = json.loads(authorization.read_text(encoding="utf-8"))
    payload["pilot_32_authorized"] = True
    _write_json(authorization, payload)

    receipt = module.audit_resource_license_gate(
        frozen_contract_path=contract,
        preparation_receipt_path=preparation,
        authorization_receipt_path=authorization,
        snapshot_path=snapshot,
        out_dir=tmp_path / "escalated",
        max_age_seconds=900,
        evaluated_utc=NOW,
    )

    assert receipt["overall_status"] == "FAIL"
    assert receipt["golden_launch_authorized"] is False
    assert receipt["pilot_32_launch_authorized"] is False
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert "authorization_pilot_32_forbidden" in failed


def test_internally_inconsistent_snapshot_fails_instead_of_waiting(
    tmp_path: Path,
) -> None:
    module = _load_module()
    contract, preparation, authorization, snapshot, _ = _fixture_paths(tmp_path)
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["resources"]["load_1m"] = 9.0
    payload["resources"]["load_gate_pass"] = True
    payload["resources"]["resources_available"] = True
    _write_json(snapshot, payload)

    receipt = module.audit_resource_license_gate(
        frozen_contract_path=contract,
        preparation_receipt_path=preparation,
        authorization_receipt_path=authorization,
        snapshot_path=snapshot,
        out_dir=tmp_path / "inconsistent",
        max_age_seconds=900,
        evaluated_utc=NOW,
    )

    assert receipt["overall_status"] == "FAIL"
    assert receipt["golden_launch_authorized"] is False
    failed = {item["name"] for item in receipt["checks"] if not item["pass"]}
    assert "reported_load_gate_consistent" in failed
    assert "snapshot_resources_available_consistent" in failed


def test_output_directory_is_no_clobber(tmp_path: Path) -> None:
    module = _load_module()
    _audit(module, tmp_path)
    contract, preparation, authorization, snapshot, _ = _fixture_paths(
        tmp_path / "second_fixture"
    )

    with pytest.raises(module.ResourceGateError, match="no-clobber"):
        module.audit_resource_license_gate(
            frozen_contract_path=contract,
            preparation_receipt_path=preparation,
            authorization_receipt_path=authorization,
            snapshot_path=snapshot,
            out_dir=tmp_path / "gate",
            max_age_seconds=900,
            evaluated_utc=NOW,
        )
