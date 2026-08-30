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
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_capacity_supervisor.py"
SPEC = importlib.util.spec_from_file_location("capacity_supervisor", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _snapshot(*, swap_in: int = 1) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "captured_utc": "2026-08-30T18:00:00Z",
        "resources": {
            "logical_cpu_count": 192,
            "physical_cpu_count": 96,
            "load_1m": 225.6,
            "load_5m": 222.0,
            "load_15m": 210.0,
            "cpu_total_utilization_percent": 70.0,
            "cpu_user_utilization_percent": 58.0,
            "cpu_system_utilization_percent": 11.0,
            "iowait_percent": 0.01,
            "runnable_process_count": 10,
            "blocked_process_count": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 430_000,
            "swap_total_bytes": 100_000,
            "swap_used_bytes": 20_000,
            "swap_sample_interval_seconds": 60.0,
            "swap_in_pages_delta": swap_in,
            "swap_out_pages_delta": 0,
            "active_swap_thrashing": bool(swap_in),
            "filesystem_free_bytes": 20 * 1024**3,
        },
        "licenses": {
            "cadence_available": True,
            "calibre_available": True,
            "emx_available": True,
            "simulator_license_capacity": 4,
        },
        "isolation": {
            "authoritative_supervisor_count": 0,
            "duplicate_supervisor_count": 0,
            "duplicate_runner_count": 0,
            "unexpected_project_child_count": 0,
            "project_owned_cadence_children": 0,
            "project_owned_calibre_children": 0,
            "project_owned_emx_children": 0,
            "output_path_collision": False,
        },
    }


def _file(path: Path, payload: dict | str) -> Path:
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload))
    else:
        path.write_text(payload)
    return path


def test_one_check_records_wait_without_running_probe_or_simulator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = _file(tmp_path / "snapshot.json", _snapshot())
    gate = _file(tmp_path / "gate.json", {"overall_status": "WAIT"})
    probe_marker = tmp_path / "probe_ran"
    probe = _file(tmp_path / "probe.sh", f"touch {probe_marker}\n")
    dummy = _file(tmp_path / "dummy.json", {})
    auditor = _file(tmp_path / "auditor.py", "raise SystemExit(99)\n")
    monkeypatch.setattr(MODULE, "_validate_control_evidence", lambda _inputs: None)
    args = argparse.Namespace(
        frozen_contract=str(dummy),
        preparation_receipt=str(dummy),
        policy_approval_receipt=str(dummy),
        probe_script=str(probe),
        resource_gate_auditor=str(auditor),
        initial_snapshot=str(snapshot),
        initial_resource_gate=str(gate),
        python_bin=str(Path(__import__("sys").executable)),
        poll_seconds=300,
        max_checks=1,
        max_age_seconds=900,
    )

    state = MODULE.run_supervisor(args, out_dir=tmp_path / "supervisor")

    assert state["overall_status"] == "WAITING_FOR_CAPACITY"
    assert state["resource_gate"] == "WAIT"
    assert state["max_allowed_concurrency"] == 0
    assert state["simulator_action_taken"] is False
    assert state["active_simulator_jobs"] == 0
    assert not probe_marker.exists()


def test_cli_rejects_busy_loop_poll_interval() -> None:
    with pytest.raises(SystemExit):
        MODULE._parse_args(
            [
                "--frozen-contract",
                "a",
                "--preparation-receipt",
                "b",
                "--policy-approval-receipt",
                "c",
                "--probe-script",
                "d",
                "--resource-gate-auditor",
                "e",
                "--initial-snapshot",
                "f",
                "--initial-resource-gate",
                "g",
                "--out-dir",
                "h",
                "--poll-seconds",
                "299",
            ]
        )
