from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    CAMPAIGN_ID,
)
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import (
    RESOURCE_POLICY,
    SCIENTIFIC_CONTRACT_FINGERPRINT,
    SNAPSHOT_SCHEMA,
    STAGES,
)
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import (
    ATTEMPT_REPLENISHMENT_CONTRACT,
    FULL_CAMPAIGN_APPROVAL_SCHEMA,
    FULL_CAMPAIGN_PASS_DECISION,
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
    LABEL_CONTRACT,
    PRODUCTION_CHAIN,
    REQUIRED_RUNTIME_ROLES,
    REQUIRED_SCRIPT_ROLES,
    STAGE_COMMAND_ARGUMENTS,
)
from rfic_transformer_inverse_design.campaigns.broadband56_stage_execution import (
    PROFILE_EXECUTION_MODE,
    PROFILE_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
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


def _fixture(tmp_path: Path) -> argparse.Namespace:
    campaign_root = tmp_path / "campaign"
    (campaign_root / "stages").mkdir(parents=True)
    backend_script = _write(
        tmp_path / "backend.py",
        """#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "__REPOSITORY_ROOT__")

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import CAMPAIGN_ID
from rfic_transformer_inverse_design.campaigns.broadband56_capacity_policy import SCIENTIFIC_CONTRACT_FINGERPRINT, STAGE_BY_NAME
from rfic_transformer_inverse_design.campaigns.broadband56_full_campaign_authorization import PORT_AND_GROUNDING_CONTRACT, PRODUCTION_BACKEND_ID, expected_frequency_contract
from rfic_transformer_inverse_design.campaigns.broadband56_production_backend import FAILURE_ACCOUNTING_FIELDS, STAGE_ARTIFACT_FIELDS, STAGE_GATE_FIELDS, STAGE_RECEIPT_SCHEMA

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

parser = argparse.ArgumentParser()
parser.add_argument('--stage')
parser.add_argument('--cumulative-target', type=int)
parser.add_argument('--campaign-root')
parser.add_argument('--backend-out-dir')
parser.add_argument('--full-campaign-receipt')
parser.add_argument('--backend-identity-manifest')
parser.add_argument('--resource-snapshot')
parser.add_argument('--max-concurrency', type=int)
args = parser.parse_args()
out = Path(args.backend_out_dir)
out.mkdir(parents=True)
rows = args.cumulative_target * 56

if os.environ.get('MOCK_LAUNCHER_PROGRESS') == '1':
    artifacts = {}
    for role in (
        'attempt_ledger', 'accepted_geometry_increment',
        'rejected_geometry_increment', 'exact_gds_emx_receipt_index',
        's4p_artifact_index', 'long_features', 'failure_funnel',
    ):
        path = out / 'attempt_artifacts' / (role + '.csv')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(role + '\\n', encoding='utf-8')
        artifacts[role] = {
            'path': str(path.resolve()), 'sha256': sha(path),
            'size_bytes': path.stat().st_size,
        }
    failure = {field: 0 for field in FAILURE_ACCOUNTING_FIELDS}
    failure['raw_geometry_candidates'] = 1
    failure['analytical_failures'] = 1
    progress = {
        'schema': 'rfic_transformer.broadband56_v2_stage_progress_receipt.v3',
        'overall_status': 'INCOMPLETE',
        'decision': 'CONTINUE_SAMPLING',
        'campaign_id': CAMPAIGN_ID,
        'contract_fingerprint_sha256': SCIENTIFIC_CONTRACT_FINGERPRINT,
        'backend_id': PRODUCTION_BACKEND_ID,
        'stage': args.stage,
        'attempt_index': 1,
        'cumulative_target': args.cumulative_target,
        'accepted_before': 0,
        'accepted_this_attempt': 0,
        'accepted_after': 0,
        'remaining_after': args.cumulative_target,
        'raw_candidates_this_attempt': 1,
        'terminal_attempts_this_attempt': 1,
        'prior_progress_receipt_sha256': None,
        'backend_identity_manifest_sha256': sha(Path(args.backend_identity_manifest)),
        'full_campaign_authorization_receipt_sha256': sha(Path(args.full_campaign_receipt)),
        'safeguards': {
            'proxy_label_count': 0, 'historical_label_count': 0,
            'interpolated_frequency_record_count': 0,
            'accepted_duplicate_geometry_count': 0,
            'accepted_blocking_calibre_count': 0,
            'manual_gds_modification_count': 0,
            'mixed_contract_fingerprint_count': 0,
        },
        'failure_accounting': failure,
        'artifacts': artifacts,
        'round_cumulative_inputs': None,
        'simulator_action_taken': False,
        'stage_pass_receipt_created': False,
        'evidence_preserved': True,
    }
    (out / 'STAGE_PROGRESS_RECEIPT.json').write_text(
        json.dumps(progress) + '\\n', encoding='utf-8'
    )
    raise SystemExit(0)

raw_receipt = {
    'schema': 'broadband56_raw_products_receipt_v1',
    'overall_status': 'PASS',
    'decision': 'USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS',
    'campaign_id': CAMPAIGN_ID,
    'contract_fingerprint_sha256': SCIENTIFIC_CONTRACT_FINGERPRINT,
    'counts': {
        'accepted_geometries': args.cumulative_target,
        's4p_artifacts': args.cumulative_target,
        'geometry_frequency_rows': rows,
    },
    'checks': {
        'all_accepted_s4p_are_fresh_exact_56_point_four_port': True,
        'long_features_bound_to_exact_s4p_s_and_z': True,
        'long_physical_features_recomputed_from_exact_s4p': True,
        'proxy_values_excluded_from_labels': True,
    },
}

artifacts = {}
for role in STAGE_ARTIFACT_FIELDS:
    suffix = '.json' if role in {'raw_products_receipt', 'checkpoint_receipt'} else '.txt'
    path = out / (role + suffix)
    if role == 'raw_products_receipt':
        path.write_text(json.dumps(raw_receipt) + '\\n', encoding='utf-8')
    elif role == 'checkpoint_receipt':
        path.write_text(json.dumps({
            'overall_status': 'PASS',
            'decision': 'USE_CHECKPOINT',
            'campaign_id': CAMPAIGN_ID,
            'contract_fingerprint_sha256': SCIENTIFIC_CONTRACT_FINGERPRINT,
            'expected_accepted': args.cumulative_target,
            'checks': [{'name': 'exact_count', 'pass': True}],
        }) + '\\n', encoding='utf-8')
    else:
        path.write_text('test artifact: ' + role + '\\n', encoding='utf-8')
    artifacts[role] = {
        'path': str(path.resolve()),
        'sha256': sha(path),
        'size_bytes': path.stat().st_size,
    }

failure_accounting = {field: 0 for field in FAILURE_ACCOUNTING_FIELDS}
failure_accounting['raw_geometry_candidates'] = args.cumulative_target
failure_accounting['accepted_geometries'] = args.cumulative_target
receipt = {
    'schema': STAGE_RECEIPT_SCHEMA,
    'overall_status': 'PASS',
    'decision': 'ACCEPT_STAGE',
    'campaign_id': CAMPAIGN_ID,
    'contract_fingerprint_sha256': SCIENTIFIC_CONTRACT_FINGERPRINT,
    'backend_id': PRODUCTION_BACKEND_ID,
    'stage': args.stage,
    'terminal_state': STAGE_BY_NAME[args.stage].receipt_status,
    'cumulative_target': args.cumulative_target,
    'accepted_unique_geometries': args.cumulative_target,
    'backend_identity_manifest_sha256': sha(Path(args.backend_identity_manifest)),
    'full_campaign_authorization_receipt_sha256': sha(Path(args.full_campaign_receipt)),
    'prior_stage_receipt_sha256': None,
    'frequency_contract': expected_frequency_contract(),
    'port_and_grounding_contract': PORT_AND_GROUNDING_CONTRACT,
    'label_source': 'FRESH_REAL_EMX_ONLY',
    'counts': {
        'accepted_unique_geometries': args.cumulative_target,
        'valid_s4p_geometries': args.cumulative_target,
        'feature_complete_geometries': args.cumulative_target,
        's4p_artifacts': args.cumulative_target,
        'independent_designs': args.cumulative_target,
        'geometry_frequency_rows': rows,
        'broadband_descriptor_valid_rows': rows,
        'strict_lumped_valid_rows': rows,
    },
    'safeguards': {
        'proxy_label_count': 0,
        'historical_label_count': 0,
        'interpolated_frequency_record_count': 0,
        'accepted_duplicate_geometry_count': 0,
        'accepted_blocking_calibre_count': 0,
        'manual_gds_modification_count': 0,
        'mixed_contract_fingerprint_count': 0,
    },
    'gates': {field: True for field in STAGE_GATE_FIELDS},
    'failure_accounting': failure_accounting,
    'artifacts': artifacts,
}
(out / 'STAGE_RECEIPT.json').write_text(
    json.dumps(receipt) + '\\n', encoding='utf-8'
)
""".replace("__REPOSITORY_ROOT__", str(ROOT)),
    )
    backend_script.chmod(0o755)
    script_identities = {}
    for role in REQUIRED_SCRIPT_ROLES:
        path = (
            SCRIPT
            if role == "stage_launcher"
            else backend_script
            if role == "production_stage_backend"
            else _write(tmp_path / "scripts" / f"{role}.py", f"# {role}\n")
        )
        script_identities[role] = {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        if role == "production_stage_backend":
            script_identities[role]["executable"] = True
    runtime_identities = {}
    for role in REQUIRED_RUNTIME_ROLES:
        path = _write(
            tmp_path / "runtime" / f"{role}.dat",
            _execution_profile()
            if role == "stage_execution_profile"
            else f"{role}\n",
        )
        if role in {"emx_wrapper", "python_executable"}:
            path.chmod(0o755)
        runtime_identities[role] = {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        if role in {"emx_wrapper", "python_executable"}:
            runtime_identities[role]["executable"] = True
    command = [
        str(backend_script.resolve()),
        *[
            item
            for flag, placeholder in STAGE_COMMAND_ARGUMENTS
            for item in (flag, placeholder)
        ],
    ]
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

    def pass_record(path: Path) -> dict[str, object]:
        return {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
            "overall_status": "PASS",
        }

    backend = {
        "schema": BACKEND_MANIFEST_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
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
            "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
            "terminal_contract": expected_terminal_contract(),
            "ordered_stages": expected_stage_contract(),
        },
        "preparation_bindings": {
            "preparation_receipt_sha256": "1" * 64,
            "private_configuration_sha256": runtime_identities[
                "private_configuration"
            ]["sha256"],
            "historical_configuration_sha256": "3" * 64,
            "operational_policy_approval_receipt_sha256": "4" * 64,
        },
        "script_identities": script_identities,
        "runtime_identities": runtime_identities,
        "stage_commands": {
            stage.name: {
                "argv": command,
                "shell_used": False,
                "identity_role": "production_stage_backend",
                "identity_argv_index": 0,
                "identity_sha256": _sha(backend_script),
            }
            for stage in STAGES
        },
        "historical_gds_identity_pass_receipt": pass_record(historical_gds),
        "historical_backend_pass_receipts": [
            pass_record(historical_one),
            pass_record(historical_two),
        ],
    }
    backend_path = _write(tmp_path / "backend.json", backend)
    receipt = {
        "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "overall_status": "PASS",
        "decision": FULL_CAMPAIGN_PASS_DECISION,
        "authorization_scope": "FULL_CAMPAIGN",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_identity_manifest": {"sha256": _sha(backend_path)},
        "accepted_geometry_target": 200_000,
        "replenished_attempt_rounds_authorized": True,
        "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
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


def test_launcher_preserves_valid_nonterminal_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _fixture(tmp_path)
    out_dir = Path(args.out_dir)
    monkeypatch.setenv("MOCK_LAUNCHER_PROGRESS", "1")

    progress = MODULE.launch_stage(args, out_dir=out_dir)

    assert progress["decision"] == "CONTINUE_SAMPLING"
    assert progress["accepted_after"] == 0
    assert (out_dir / "STAGE_PROGRESS_RECEIPT.json").is_file()
    assert not (out_dir / "STAGE_RECEIPT.json").exists()


def test_rejects_backend_executable_hash_drift(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    backend = json.loads(Path(args.backend_identity_manifest).read_text())
    backend["stage_commands"]["GOLDEN"]["identity_sha256"] = "0" * 64
    Path(args.backend_identity_manifest).write_text(json.dumps(backend))
    receipt = json.loads(Path(args.full_campaign_receipt).read_text())
    receipt["backend_identity_manifest"]["sha256"] = _sha(Path(args.backend_identity_manifest))
    Path(args.full_campaign_receipt).write_text(json.dumps(receipt))

    with pytest.raises(MODULE.StageLauncherError, match="identity"):
        MODULE.launch_stage(args, out_dir=Path(args.out_dir))


def test_rejects_unapproved_receipt(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    receipt = json.loads(Path(args.full_campaign_receipt).read_text())
    receipt["overall_status"] = "FAIL"
    Path(args.full_campaign_receipt).write_text(json.dumps(receipt))

    with pytest.raises(MODULE.StageLauncherError, match="receipt identity"):
        MODULE.launch_stage(args, out_dir=Path(args.out_dir))
