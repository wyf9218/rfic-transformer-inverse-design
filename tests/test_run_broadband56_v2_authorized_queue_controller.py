from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    SNAPSHOT_SCHEMA,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_authorized_queue_controller.py"
SPEC = importlib.util.spec_from_file_location("authorized_queue_controller", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, value: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _snapshot(*, wait: bool) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "captured_utc": "2026-08-30T20:00:00Z",
        "resources": {
            "logical_cpu_count": 192,
            "physical_cpu_count": 96,
            "load_1m": 230.0 if wait else 40.0,
            "load_5m": 220.0 if wait else 42.0,
            "load_15m": 210.0 if wait else 45.0,
            "cpu_total_utilization_percent": 60.0,
            "cpu_user_utilization_percent": 50.0,
            "cpu_system_utilization_percent": 10.0,
            "iowait_percent": 0.1,
            "runnable_process_count": 5,
            "blocked_process_count": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 500_000,
            "swap_total_bytes": 100_000,
            "swap_used_bytes": 0,
            "swap_sample_interval_seconds": 60.0,
            "swap_in_pages_delta": 0,
            "swap_out_pages_delta": 0,
            "active_swap_thrashing": False,
            "filesystem_free_bytes": 20 * 1024**3,
        },
        "licenses": {
            "cadence_available": True,
            "calibre_available": True,
            "emx_available": True,
            "simulator_license_capacity": 4,
        },
        "isolation": {
            "authoritative_supervisor_count": 1,
            "duplicate_supervisor_count": 0,
            "duplicate_runner_count": 0,
            "unexpected_project_child_count": 0,
            "project_owned_cadence_children": 0,
            "project_owned_calibre_children": 0,
            "project_owned_emx_children": 0,
            "output_path_collision": False,
        },
    }


def _evidence(tmp_path: Path) -> dict:
    contract = _write(tmp_path / "contract.json", {"campaign_id": "test"})
    preparation = _write(tmp_path / "prep.json", {"overall_status": "PASS"})
    policy = _write(tmp_path / "policy.json", {"overall_status": "PASS"})
    candidate = _write(tmp_path / "candidate.json", {"schema": "candidate"})
    backend = _write(tmp_path / "backend.json", {"schema": "backend"})
    launcher = _write(tmp_path / "launcher.py", "raise SystemExit(99)\n")
    return {
        "contract": MODULE._file_record(contract),
        "preparation_receipt": MODULE._file_record(preparation),
        "policy_approval_receipt": MODULE._file_record(policy),
        "candidate": MODULE._file_record(candidate),
        "backend_identity_manifest": MODULE._file_record(backend),
        "stage_launcher": MODULE._file_record(launcher),
        "frequency_contract": {"points": 56},
        "port_and_grounding_contract": {"ports": 4},
        "unchanged_physical_contract_items": ["calibre_drc"],
        "backend_id": "MARS_CADENCE_CALIBRE_ZERO_BLOCKING_EMX_S4P_QA_V1",
    }


def _args(tmp_path: Path, *, receipt_exists: bool = False) -> argparse.Namespace:
    files = {}
    for name in (
        "frozen_contract",
        "preparation_receipt",
        "policy_approval_receipt",
        "full_campaign_candidate",
        "backend_identity_manifest",
        "stage_launcher",
        "probe_script",
        "resource_gate_auditor",
    ):
        files[name] = _write(tmp_path / f"{name}.txt", "fixture\n")
    receipt = tmp_path / "full_receipt.json"
    if receipt_exists:
        _write(receipt, {"overall_status": "PASS"})
    return argparse.Namespace(
        **{name: str(path) for name, path in files.items()},
        full_campaign_candidate_sha256="a" * 64,
        full_campaign_receipt=str(receipt),
        python_bin=str(Path(__import__("sys").executable)),
        campaign_root=str(tmp_path / "campaign"),
        lock_path=str(tmp_path / "controller.lock"),
        poll_seconds=60,
        max_checks=1,
        max_age_seconds=300,
        resume=False,
    )


def _install_common(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    wait: bool,
    authorized: bool,
) -> tuple[argparse.Namespace, Path]:
    args = _args(tmp_path, receipt_exists=authorized)
    evidence = _evidence(tmp_path / "evidence")
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot(wait=wait))
    monkeypatch.setattr(MODULE, "STOP_REQUESTED", False)
    monkeypatch.setattr(MODULE, "_validate_control_evidence", lambda _inputs, _args: evidence)
    monkeypatch.setattr(MODULE, "_run_probe", lambda *_args, **_kwargs: snapshot_path)
    monkeypatch.setattr(
        MODULE,
        "_load_full_campaign_receipt",
        (lambda *_args, **_kwargs: {"overall_status": "PASS"})
        if authorized
        else (lambda *_args, **_kwargs: None),
    )
    return args, Path(args.campaign_root)


def test_registers_durable_queue_and_keeps_simulators_zero_without_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, campaign_root = _install_common(
        tmp_path, monkeypatch, wait=False, authorized=False
    )
    launch_marker = tmp_path / "launched"
    monkeypatch.setattr(MODULE, "_run_stage_launcher", lambda **_kwargs: launch_marker.touch())

    state = MODULE.run_controller(args, campaign_root=campaign_root)

    assert state["overall_status"] == "QUEUED_WAITING_FOR_AUTHORIZATION"
    assert state["active_simulator_jobs"] == 0
    assert state["simulator_action_taken_on_this_iteration"] is False
    assert not launch_marker.exists()
    for name in MODULE.IMMUTABLE_ARTIFACTS:
        assert (campaign_root / name).is_file()
    receipt = json.loads((campaign_root / "MARS_QUEUE_RECEIPT.json").read_text())
    assert receipt["overall_status"] == "PASS"
    assert receipt["simulator_action_taken"] is False


def test_authorized_but_unsafe_capacity_waits_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, campaign_root = _install_common(
        tmp_path, monkeypatch, wait=True, authorized=True
    )
    gate = _write(tmp_path / "gate.json", {"overall_status": "WAIT"})
    monkeypatch.setattr(MODULE, "_write_resource_gate", lambda **_kwargs: gate)
    launch_marker = tmp_path / "launched"
    monkeypatch.setattr(MODULE, "_run_stage_launcher", lambda **_kwargs: launch_marker.touch())

    state = MODULE.run_controller(args, campaign_root=campaign_root)

    assert state["overall_status"] == "QUEUED_WAITING_FOR_CAPACITY"
    assert state["resource_gate"] == "WAIT"
    assert not launch_marker.exists()


def test_exact_authorization_and_safe_capacity_invoke_one_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, campaign_root = _install_common(
        tmp_path, monkeypatch, wait=False, authorized=True
    )
    gate = _write(tmp_path / "gate.json", {"overall_status": "PASS"})
    monkeypatch.setattr(MODULE, "_write_resource_gate", lambda **_kwargs: gate)
    monkeypatch.setattr(MODULE, "_materialize_authorization_receipt", lambda **_kwargs: None)
    launches = []
    monkeypatch.setattr(MODULE, "_run_stage_launcher", lambda **kwargs: launches.append(kwargs["stage"]))

    state = MODULE.run_controller(args, campaign_root=campaign_root)

    assert launches == ["GOLDEN"]
    assert state["overall_status"] == "GOLDEN_RUNNING"
    assert state["simulator_action_taken_on_this_iteration"] is True
    assert state["current_concurrency"] == 1


def test_no_clobber_existing_campaign_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, campaign_root = _install_common(
        tmp_path, monkeypatch, wait=False, authorized=False
    )
    campaign_root.mkdir()

    with pytest.raises(MODULE.ControllerError, match="no-clobber"):
        MODULE.run_controller(args, campaign_root=campaign_root)
