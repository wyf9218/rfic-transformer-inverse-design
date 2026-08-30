from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    SNAPSHOT_SCHEMA,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_PASS_DECISION,
    PRODUCTION_BACKEND_ID,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_stage_launcher.py"
SPEC = importlib.util.spec_from_file_location("stage_launcher", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _snapshot() -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "resource_policy": RESOURCE_POLICY,
        "captured_utc": "2026-08-30T20:00:00Z",
        "resources": {
            "logical_cpu_count": 192,
            "physical_cpu_count": 96,
            "load_1m": 20.0,
            "load_5m": 20.0,
            "load_15m": 20.0,
            "cpu_total_utilization_percent": 20.0,
            "cpu_user_utilization_percent": 15.0,
            "cpu_system_utilization_percent": 5.0,
            "iowait_percent": 0.0,
            "runnable_process_count": 2,
            "blocked_process_count": 0,
            "memory_total_bytes": 1_000_000,
            "memory_available_bytes": 600_000,
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


def _fixture(tmp_path: Path) -> argparse.Namespace:
    campaign_root = tmp_path / "campaign"
    (campaign_root / "stages").mkdir(parents=True)
    backend_script = _write(
        tmp_path / "backend.py",
        """import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--stage');p.add_argument('--target',type=int);p.add_argument('--out-dir');ns=p.parse_args()\nout=Path(ns.out_dir);out.mkdir(parents=True)\nterm={'GOLDEN':'GOLDEN_COMPLETE'}[ns.stage]\nd={'overall_status':'PASS','stage':ns.stage,'terminal_state':term,'accepted_unique_geometries':ns.target,'campaign_id':'broadband56_real_emx_balanced200k_tsmc65_v2','contract_fingerprint_sha256':'f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e'}\n(out/'STAGE_RECEIPT.json').write_text(json.dumps(d))\n""",
    )
    backend = {
        "schema": "rfic_transformer.broadband56_v2_private_backend_identity.v1",
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_id": PRODUCTION_BACKEND_ID,
        "script_identities": {"stage_launcher": {"sha256": _sha(SCRIPT)}},
        "stage_commands": {
            "GOLDEN": {
                "argv": [
                    sys.executable,
                    str(backend_script),
                    "--stage",
                    "{stage}",
                    "--target",
                    "{cumulative_target}",
                    "--out-dir",
                    "{backend_out_dir}",
                ],
                "identity_argv_index": 1,
                "identity_sha256": _sha(backend_script),
            }
        },
    }
    backend_path = _write(tmp_path / "backend.json", backend)
    receipt = {
        "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "overall_status": "PASS",
        "decision": FULL_CAMPAIGN_PASS_DECISION,
        "authorization_scope": "FULL_CAMPAIGN",
        "campaign_id": "broadband56_real_emx_balanced200k_tsmc65_v2",
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_identity_manifest": {"sha256": _sha(backend_path)},
        "simulator_geometry_limit": 200_000,
        "automatic_ordered_stage_execution_authorized": True,
        "cadence_authorized_within_current_stage": True,
        "calibre_authorized_within_current_stage": True,
        "emx_authorized_within_current_stage": True,
        "campaign_200k_authorized": True,
    }
    receipt_path = _write(tmp_path / "receipt.json", receipt)
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    return argparse.Namespace(
        stage="GOLDEN",
        cumulative_target=1,
        campaign_root=str(campaign_root),
        out_dir=str(tmp_path / "launch"),
        full_campaign_receipt=str(receipt_path),
        backend_identity_manifest=str(backend_path),
        resource_snapshot=str(snapshot_path),
        max_concurrency=1,
    )


def test_launches_hash_bound_backend_and_preserves_exact_receipt(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    out_dir = Path(args.out_dir)

    receipt = MODULE.launch_stage(args, out_dir=out_dir)

    assert receipt["overall_status"] == "PASS"
    assert receipt["stage"] == "GOLDEN"
    assert (out_dir / "STAGE_LAUNCH_AUDIT.json").is_file()
    assert (out_dir / "STAGE_RECEIPT.json").is_file()


def test_rejects_backend_executable_hash_drift(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    backend = json.loads(Path(args.backend_identity_manifest).read_text())
    backend["stage_commands"]["GOLDEN"]["identity_sha256"] = "0" * 64
    Path(args.backend_identity_manifest).write_text(json.dumps(backend))
    receipt = json.loads(Path(args.full_campaign_receipt).read_text())
    receipt["backend_identity_manifest"]["sha256"] = _sha(Path(args.backend_identity_manifest))
    Path(args.full_campaign_receipt).write_text(json.dumps(receipt))

    with pytest.raises(MODULE.StageLauncherError, match="executable identity"):
        MODULE.launch_stage(args, out_dir=Path(args.out_dir))


def test_rejects_unapproved_receipt(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    receipt = json.loads(Path(args.full_campaign_receipt).read_text())
    receipt["overall_status"] = "FAIL"
    Path(args.full_campaign_receipt).write_text(json.dumps(receipt))

    with pytest.raises(MODULE.StageLauncherError, match="receipt identity"):
        MODULE.launch_stage(args, out_dir=Path(args.out_dir))
