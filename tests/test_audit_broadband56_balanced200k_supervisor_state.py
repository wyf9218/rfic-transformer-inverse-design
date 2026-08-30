from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    contract_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONTRACT = ROOT / "configs" / "broadband56_real_emx_balanced200k_tsmc65_v2.json"


def _load_module():
    path = ROOT / "scripts" / "audit_broadband56_balanced200k_supervisor_state.py"
    spec = importlib.util.spec_from_file_location(
        "audit_broadband56_balanced200k_supervisor_state", path
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


def _evidence(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_index(directory: Path) -> None:
    index = directory / "SHA256SUMS.txt"
    lines = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(directory.iterdir())
        if path.is_file() and path != index
    ]
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepared_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    preparation = tmp_path / "preparation"
    preparation.mkdir()
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
    contract_path = preparation / "campaign_contract_frozen.json"
    _write_json(contract_path, contract)

    artifact_paths = {
        "primary_bins": preparation / "PRIMARY_BINS_FROZEN.json",
        "secondary_coverage": preparation / "SECONDARY_COVERAGE_FROZEN.json",
        "geometry_bounds": preparation / "GEOMETRY_BOUNDS_FROZEN.json",
        "phase_plan": preparation / "PHASE_PLAN_FROZEN.json",
    }
    for path in artifact_paths.values():
        _write_json(path, {"campaign_id": CAMPAIGN_ID, "fixture": True})
    receipt = {
        "overall_status": "PASS",
        "decision": "PREPARED_FOR_GOLDEN_GATE",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": fingerprint,
        "checks": [{"name": "fixture", "pass": True}],
        "artifacts": {
            "frozen_contract": _evidence(contract_path),
            **{name: _evidence(path) for name, path in artifact_paths.items()},
        },
    }
    _write_json(preparation / "PREPARATION_RECEIPT.json", receipt)
    _write_index(preparation)
    return contract_path, preparation, fingerprint


def _write_resource_gate(
    tmp_path: Path,
    fingerprint: str,
    *,
    available: bool = True,
    permission_level: str = "golden",
) -> Path:
    if permission_level not in {"golden", "full"}:
        raise ValueError(f"unsupported permission level: {permission_level}")
    full = permission_level == "full"
    scope = "FULL_CAMPAIGN" if full else "ONE_GOLDEN_ONLY"
    authorization = tmp_path / f"authorization_{permission_level}.json"
    auth_permissions = {
        "one_golden_authorized": True,
        "pilot_32_authorized": full,
        "pilot_1000_authorized": full,
        "queue_authorized": full,
        "supervisor_authorized": full,
        "phase_a_authorized": full,
        "phase_b_authorized": full,
        "phase_c_authorized": full,
        "campaign_200k_authorized": full,
    }
    _write_json(
        authorization,
        {
            "schema": (
                "rfic_transformer.broadband56_v2_stage_authorization.v1"
                if full
                else "rfic_transformer.broadband56_v2_golden_authorization.v1"
            ),
            "overall_status": "PASS",
            "decision": (
                "APPROVE_EXPLICIT_BROADBAND56_V2_STAGES"
                if full
                else "APPROVE_RESOURCE_LICENSE_GATE_AND_ONE_GOLDEN_ONLY"
            ),
            "authorization_scope": scope,
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "approved_by": "unit-test-project-owner",
            "approved_utc": "2026-08-30T18:00:00Z",
            "approval_source": "EXPLICIT_USER_OR_PROJECT_LEADER_INSTRUCTION",
            "approval_reference": "explicit unit-test stage approval",
            "approved_candidate": {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "sha256": "d" * 64,
            },
            "resource_and_license_gate_authorized": True,
            "cadence_authorized_for_one_golden": True,
            "calibre_authorized_for_one_golden": True,
            "emx_authorized_for_one_golden": True,
            "simulator_geometry_limit": 200_000 if full else 1,
            "checks": [{"name": "fixture", "pass": True}],
            "execution_effect": "NONE_RECORD_ONLY",
            **auth_permissions,
        },
    )
    path = tmp_path / (
        f"resource_gate_{permission_level}_pass.json"
        if available
        else f"resource_gate_{permission_level}_wait.json"
    )
    launch_permissions = {
        "golden_launch_authorized": available,
        "pilot_32_launch_authorized": available and full,
        "pilot_1000_launch_authorized": available and full,
        "queue_launch_authorized": available and full,
        "supervisor_launch_authorized": available and full,
        "phase_a_launch_authorized": available and full,
        "phase_b_launch_authorized": available and full,
        "phase_c_launch_authorized": available and full,
        "campaign_launch_authorized": available and full,
    }
    _write_json(
        path,
        {
            "schema": "rfic_transformer.broadband56_v2_resource_license_gate.v1",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "overall_status": "PASS" if available else "WAIT",
            "authorization_scope": scope,
            "resources_available": available,
            "load_gate_pass": available,
            "memory_gate_pass": available,
            "storage_gate_pass": available,
            "license_gate_pass": available,
            "isolation_gate_pass": available,
            "valid_until_utc": "2099-01-01T00:00:00Z",
            "checks": [{"name": "fixture", "pass": available}],
            "evidence": {
                (
                    "stage_authorization_receipt"
                    if full
                    else "golden_authorization_receipt"
                ): _evidence(authorization)
            },
            **launch_permissions,
        },
    )
    return path


def _write_capacity_resource_gate(tmp_path: Path, fingerprint: str) -> Path:
    authorization = tmp_path / "capacity_policy_authorization.json"
    _write_json(
        authorization,
        {
            "schema": "rfic_transformer.broadband56_v2_operational_policy_amendment_approval.v1",
            "overall_status": "PASS",
            "decision": "APPROVE_CAPACITY_NORMALIZED_STAGED_EXECUTION",
            "authorization_scope": "FULL_CAMPAIGN",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "resource_policy": "CAPACITY_NORMALIZED_HIGH_LOAD_V1",
            "approved_by": "unit-test-project-owner",
            "approved_utc": "2026-08-30T18:00:00Z",
            "approval_source": "EXPLICIT_PROJECT_OWNER_INSTRUCTION",
            "approval_reference": "explicit unit-test capacity-policy approval",
            "approved_candidate": {
                "campaign_id": CAMPAIGN_ID,
                "contract_fingerprint_sha256": fingerprint,
                "sha256": "d" * 64,
            },
            "one_golden_authorized": True,
            "pilot_32_authorized": True,
            "pilot_1000_authorized": True,
            "queue_authorized": True,
            "supervisor_authorized": True,
            "phase_a_authorized": True,
            "phase_b_authorized": True,
            "phase_c_authorized": True,
            "campaign_200k_authorized": True,
            "checks": [{"name": "fixture", "pass": True}],
            "execution_effect": "NONE_RECORD_ONLY",
        },
    )
    path = tmp_path / "capacity_resource_gate.json"
    _write_json(
        path,
        {
            "schema": "rfic_transformer.broadband56_v2_capacity_resource_gate.v1",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "resource_policy": "CAPACITY_NORMALIZED_HIGH_LOAD_V1",
            "overall_status": "PASS",
            "authorization_scope": "FULL_CAMPAIGN",
            "resources_available": True,
            "load_gate_pass": True,
            "memory_gate_pass": True,
            "iowait_gate_pass": True,
            "swap_gate_pass": True,
            "storage_gate_pass": True,
            "license_gate_pass": True,
            "isolation_gate_pass": True,
            "valid_until_utc": "2099-01-01T00:00:00Z",
            "checks": [{"name": "fixture", "pass": True}],
            "evidence": {
                "operational_policy_approval_receipt": _evidence(authorization)
            },
            "golden_launch_authorized": True,
            "pilot_32_launch_authorized": True,
            "pilot_1000_launch_authorized": True,
            "queue_launch_authorized": True,
            "supervisor_launch_authorized": True,
            "phase_a_launch_authorized": True,
            "phase_b_launch_authorized": True,
            "phase_c_launch_authorized": True,
            "campaign_launch_authorized": True,
        },
    )
    return path


def _write_audit(
    tmp_path: Path,
    *,
    name: str,
    count: int,
    mode: str,
    state: str,
    fingerprint: str,
) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    status_path = directory / "CHECKPOINT_STATUS.json"
    coverage_path = directory / "COVERAGE_SUMMARY.json"
    failure_path = directory / "FAILURE_FUNNEL.csv"
    _write_json(
        status_path,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "audit_mode": mode,
            "checkpoint_status": state,
            "coverage_status": "COVERAGE_PARTIAL",
            "accepted_geometries": count,
            "s4p_artifacts": count,
            "geometry_frequency_rows": count * len(FREQUENCY_GRID_HZ),
        },
    )
    _write_json(
        coverage_path,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "feature_row_count": count * len(FREQUENCY_GRID_HZ),
            "coverage_status": "COVERAGE_PARTIAL",
            "geometry_unique_anchor_coverage": {
                "observed_cells": min(count, 10),
                "observed_cell_fraction": min(count, 10) / 10,
                "normalized_entropy": 0.5,
                "underfilled_cells": max(0, 10 - count),
            },
        },
    )
    failure_path.write_text("stage,count\naccepted_geometries,%d\n" % count, encoding="utf-8")
    _write_json(
        directory / "CHECKPOINT_RECEIPT.json",
        {
            "overall_status": "PASS",
            "decision": "USE_CHECKPOINT",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "expected_accepted": count,
            "audit_mode": mode,
            "checks": [{"name": "fixture", "pass": True}],
            "outputs": {
                "checkpoint_status": _evidence(status_path),
                "coverage_summary": _evidence(coverage_path),
                "failure_funnel": _evidence(failure_path),
            },
        },
    )
    _write_index(directory)
    return directory


def _write_gate_sequence(tmp_path: Path, fingerprint: str) -> tuple[Path, Path, Path]:
    golden = _write_audit(
        tmp_path,
        name="golden",
        count=1,
        mode="golden",
        state="GOLDEN_COMPLETE",
        fingerprint=fingerprint,
    )
    pilot_32 = _write_audit(
        tmp_path,
        name="pilot_32",
        count=32,
        mode="pilot",
        state="PILOT_32_COMPLETE",
        fingerprint=fingerprint,
    )
    pilot_1000 = _write_audit(
        tmp_path,
        name="pilot_1000",
        count=1_000,
        mode="pilot",
        state="PILOT_1000_COMPLETE",
        fingerprint=fingerprint,
    )
    return golden, pilot_32, pilot_1000


def _write_resource_estimate(
    tmp_path: Path,
    fingerprint: str,
    *,
    target: int = 200_000,
) -> Path:
    directory = tmp_path / "resource_estimate"
    directory.mkdir()
    _write_json(
        directory / "RESOURCE_ESTIMATE.json",
        {
            "overall_status": "PASS",
            "decision": "RESOURCE_ESTIMATE_READY",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "target_accepted_geometries": target,
            "target_geometry_frequency_rows": target * len(FREQUENCY_GRID_HZ),
            "checks": [{"name": "fixture", "pass": True}],
        },
    )
    (directory / "RESOURCE_ESTIMATE.md").write_text("fixture\n", encoding="utf-8")
    _write_index(directory)
    return directory


def _write_registry(
    tmp_path: Path,
    fingerprint: str,
    *,
    active_count: int = 1,
    hostname: str | None = None,
) -> tuple[Path, Path]:
    run_root = tmp_path / "run_root"
    run_root.mkdir(exist_ok=True)
    path = tmp_path / f"supervisor_{active_count}_{hostname or 'local'}.json"
    _write_json(
        path,
        {
            "schema": "broadband56_authoritative_supervisor_registry_v1",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "authoritative": True,
            "active_supervisor_count": active_count,
            "duplicate_supervisors_detected": active_count != 1,
            "run_root": str(run_root.resolve()),
            "hostname": hostname or socket.gethostname(),
            "owner_pid": os.getpid(),
            "supervisor_id": "fixture-supervisor",
            "supervisor_job": "fixture-job",
        },
    )
    return path, run_root


def _audit(
    module,
    *,
    contract_path: Path,
    preparation: Path,
    out_dir: Path,
    resource_gate: Path | None = None,
    golden: Path | None = None,
    pilot_32: Path | None = None,
    pilot_1000: Path | None = None,
    resource_estimate: Path | None = None,
    campaign_audits: tuple[Path, ...] = (),
    registry: Path | None = None,
    run_root: Path | None = None,
):
    return module.audit_supervisor_state(
        contract_path=contract_path,
        preparation_dir=preparation,
        resource_gate_path=resource_gate,
        golden_dir=golden,
        pilot_32_dir=pilot_32,
        pilot_1000_dir=pilot_1000,
        resource_estimate_dir=resource_estimate,
        audit_dirs=campaign_audits,
        supervisor_registry_path=registry,
        expected_run_root=run_root,
        out_dir=out_dir,
    )


def test_missing_resource_gate_is_prepared_waiting_without_remote_action(tmp_path: Path) -> None:
    module = _load_module()
    contract_path, preparation, _ = _prepared_fixture(tmp_path)
    out_dir = tmp_path / "state_waiting"

    snapshot = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        out_dir=out_dir,
    )

    assert snapshot["campaign_status"] == "PREPARED"
    assert snapshot["lifecycle_state"] == "PREPARED_WAITING_FOR_RESOURCE"
    assert snapshot["current_accepted"] == 0
    assert snapshot["raw_candidates"] is None
    assert snapshot["checks"]["no_remote_action_or_process_signal_performed"] is True
    assert (out_dir / "SUPERVISOR_STATE.json").is_file()
    assert (out_dir / "SHA256SUMS.txt").is_file()


def test_pass_resource_gate_advances_only_to_golden(tmp_path: Path) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_resource_gate(tmp_path, fingerprint)

    snapshot = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        out_dir=tmp_path / "state_golden",
    )

    assert snapshot["lifecycle_state"] == "READY_FOR_GOLDEN"
    assert snapshot["next_action"] == "RUN_ONE_EXACT_CONTRACT_GOLDEN_GEOMETRY"
    assert snapshot["stage_authorizations"]["golden"] is True
    assert snapshot["stage_authorizations"]["pilot_32"] is False


def test_capacity_policy_gate_supports_full_ordered_stage_authorization(
    tmp_path: Path,
) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_capacity_resource_gate(tmp_path, fingerprint)

    snapshot = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        out_dir=tmp_path / "state_capacity_golden",
    )

    assert snapshot["lifecycle_state"] == "READY_FOR_GOLDEN"
    assert all(snapshot["stage_authorizations"].values())


def test_one_golden_scope_stops_after_golden_receipt(tmp_path: Path) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_resource_gate(tmp_path, fingerprint)
    golden = _write_audit(
        tmp_path,
        name="golden",
        count=1,
        mode="golden",
        state="GOLDEN_COMPLETE",
        fingerprint=fingerprint,
    )

    snapshot = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        golden=golden,
        out_dir=tmp_path / "state_after_golden",
    )

    assert snapshot["campaign_status"] == "PREPARED"
    assert (
        snapshot["lifecycle_state"]
        == "GOLDEN_COMPLETE_WAITING_FOR_PILOT_32_AUTHORIZATION"
    )
    assert snapshot["next_action"] == "STOP_AND_REQUEST_EXPLICIT_PILOT_32_AUTHORIZATION"
    assert snapshot["blocker"] == "32-geometry pilot execution is not explicitly authorized"


def test_legacy_campaign_boolean_cannot_bypass_staged_authorization(
    tmp_path: Path,
) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    gate = tmp_path / "legacy_resource_gate.json"
    _write_json(
        gate,
        {
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": fingerprint,
            "overall_status": "PASS",
            "resources_available": True,
            "load_gate_pass": True,
            "license_gate_pass": True,
            "campaign_launch_authorized": True,
        },
    )

    with pytest.raises(module.SupervisorStateError, match="schema mismatch"):
        _audit(
            module,
            contract_path=contract_path,
            preparation=preparation,
            resource_gate=gate,
            out_dir=tmp_path / "state_legacy_gate",
        )


def test_tampered_authorization_receipt_fails_closed(tmp_path: Path) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    gate = _write_resource_gate(tmp_path, fingerprint)
    authorization = tmp_path / "authorization_golden.json"
    payload = json.loads(authorization.read_text(encoding="utf-8"))
    payload["pilot_32_authorized"] = True
    _write_json(authorization, payload)

    with pytest.raises(
        module.SupervisorStateError,
        match="authorization-receipt evidence mismatch",
    ):
        _audit(
            module,
            contract_path=contract_path,
            preparation=preparation,
            resource_gate=gate,
            out_dir=tmp_path / "state_tampered_authorization",
        )


def test_empty_preparation_checks_fail_without_output(tmp_path: Path) -> None:
    module = _load_module()
    contract_path, preparation, _ = _prepared_fixture(tmp_path)
    receipt_path = preparation / "PREPARATION_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["checks"] = []
    _write_json(receipt_path, receipt)
    _write_index(preparation)
    out_dir = tmp_path / "state_empty_checks"

    with pytest.raises(module.SupervisorStateError, match="not exact PASS"):
        _audit(
            module,
            contract_path=contract_path,
            preparation=preparation,
            out_dir=out_dir,
        )

    assert not out_dir.exists()


def test_numeric_evidence_rejects_fractional_boolean_and_nonfinite_values() -> None:
    module = _load_module()

    with pytest.raises(module.SupervisorStateError, match="invalid integer"):
        module._as_int(1.2, "fixture")
    with pytest.raises(module.SupervisorStateError, match="invalid integer"):
        module._as_int(True, "fixture")
    with pytest.raises(module.SupervisorStateError, match="non-finite"):
        module._optional_float(float("nan"))


def test_exact_audit_prefix_supports_resume_and_one_live_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "ALL_AUDIT_COUNTS", (100, 1_000, 5_000, 55_000))
    monkeypatch.setattr(module, "REQUIRED_CHECKPOINT_COUNTS", (100, 1_000, 5_000))
    monkeypatch.setattr(
        module,
        "phase_for_accepted_count",
        lambda count: "PHASE_B" if count >= 50_000 else "PHASE_A",
    )
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_resource_gate(
        tmp_path, fingerprint, permission_level="full"
    )
    golden, pilot_32, pilot_1000 = _write_gate_sequence(tmp_path, fingerprint)
    estimate = _write_resource_estimate(tmp_path, fingerprint)
    audits = tuple(
        _write_audit(
            tmp_path,
            name=f"audit_{count}",
            count=count,
            mode="checkpoint" if count in (100, 1_000, 5_000) else "round",
            state="CHECKPOINT_COMPLETE" if count in (100, 1_000, 5_000) else f"ROUND_{count}_COMPLETE",
            fingerprint=fingerprint,
        )
        for count in module.ALL_AUDIT_COUNTS
    )

    resumable = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        golden=golden,
        pilot_32=pilot_32,
        pilot_1000=pilot_1000,
        resource_estimate=estimate,
        campaign_audits=audits,
        out_dir=tmp_path / "state_resume",
    )
    registry, run_root = _write_registry(tmp_path, fingerprint)
    active = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        golden=golden,
        pilot_32=pilot_32,
        pilot_1000=pilot_1000,
        resource_estimate=estimate,
        campaign_audits=audits,
        registry=registry,
        run_root=run_root,
        out_dir=tmp_path / "state_active",
    )

    assert resumable["campaign_status"] == "PREPARED"
    assert resumable["lifecycle_state"] == "PHASE_B_RESUME_FROM_LATEST_TERMINAL_AUDIT"
    assert resumable["current_accepted"] == 55_000
    assert resumable["current_feature_rows"] == 55_000 * len(FREQUENCY_GRID_HZ)
    assert active["campaign_status"] == "PHASE_B_RUNNING"
    assert active["lifecycle_state"] == "PHASE_B_ACTIVE"
    assert active["supervisor_owner_pid"] == os.getpid()


def test_non_prefix_campaign_audit_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "ALL_AUDIT_COUNTS", (100, 1_000))
    monkeypatch.setattr(module, "REQUIRED_CHECKPOINT_COUNTS", (100, 1_000))
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_resource_gate(
        tmp_path, fingerprint, permission_level="full"
    )
    golden, pilot_32, pilot_1000 = _write_gate_sequence(tmp_path, fingerprint)
    estimate = _write_resource_estimate(tmp_path, fingerprint)
    skipped = _write_audit(
        tmp_path,
        name="audit_1000",
        count=1_000,
        mode="checkpoint",
        state="CHECKPOINT_COMPLETE",
        fingerprint=fingerprint,
    )
    out_dir = tmp_path / "state_invalid_prefix"

    with pytest.raises(module.SupervisorStateError, match="not an exact prefix"):
        _audit(
            module,
            contract_path=contract_path,
            preparation=preparation,
            resource_gate=resource_gate,
            golden=golden,
            pilot_32=pilot_32,
            pilot_1000=pilot_1000,
            resource_estimate=estimate,
            campaign_audits=(skipped,),
            out_dir=out_dir,
        )

    assert not out_dir.exists()


@pytest.mark.parametrize(
    ("active_count", "hostname", "error"),
    [
        (2, None, "not one exact authoritative run root"),
        (1, "different-host.example", "another host"),
    ],
)
def test_duplicate_or_remote_supervisor_registry_fails_closed(
    tmp_path: Path,
    active_count: int,
    hostname: str | None,
    error: str,
) -> None:
    module = _load_module()
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    registry, run_root = _write_registry(
        tmp_path,
        fingerprint,
        active_count=active_count,
        hostname=hostname,
    )
    out_dir = tmp_path / "state_registry_fail"

    with pytest.raises(module.SupervisorStateError, match=error):
        _audit(
            module,
            contract_path=contract_path,
            preparation=preparation,
            registry=registry,
            run_root=run_root,
            out_dir=out_dir,
        )

    assert not out_dir.exists()


def test_terminal_exact_prefix_is_complete_200k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "TARGET_ACCEPTED_GEOMETRIES", 300)
    monkeypatch.setattr(module, "EXPECTED_FEATURE_ROWS", 300 * len(FREQUENCY_GRID_HZ))
    monkeypatch.setattr(module, "ALL_AUDIT_COUNTS", (100, 200, 300))
    monkeypatch.setattr(module, "REQUIRED_CHECKPOINT_COUNTS", (100, 200, 300))
    contract_path, preparation, fingerprint = _prepared_fixture(tmp_path)
    resource_gate = _write_resource_gate(
        tmp_path, fingerprint, permission_level="full"
    )
    golden, pilot_32, pilot_1000 = _write_gate_sequence(tmp_path, fingerprint)
    estimate = _write_resource_estimate(tmp_path, fingerprint, target=300)
    audits = tuple(
        _write_audit(
            tmp_path,
            name=f"terminal_audit_{count}",
            count=count,
            mode="checkpoint",
            state="COMPLETE_200K" if count == 300 else "CHECKPOINT_COMPLETE",
            fingerprint=fingerprint,
        )
        for count in module.ALL_AUDIT_COUNTS
    )

    snapshot = _audit(
        module,
        contract_path=contract_path,
        preparation=preparation,
        resource_gate=resource_gate,
        golden=golden,
        pilot_32=pilot_32,
        pilot_1000=pilot_1000,
        resource_estimate=estimate,
        campaign_audits=audits,
        out_dir=tmp_path / "state_complete",
    )

    assert snapshot["campaign_status"] == "COMPLETE_200K"
    assert snapshot["lifecycle_state"] == "COMPLETE_200K"
    assert snapshot["current_accepted"] == 300
    assert snapshot["next_action"].startswith("RUN_TERMINAL_HISTORY")
