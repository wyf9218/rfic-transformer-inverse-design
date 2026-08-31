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
    FULL_CAMPAIGN_APPROVAL_SCOPE,
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
    PROFILE_KEY,
    PROFILE_SCHEMA,
    expected_result_path_fields,
    expected_stage_role_order,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_broadband56_v2_production_stage_backend.py"
SPEC = importlib.util.spec_from_file_location("production_stage_backend", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MOCK_ROLE_SCRIPT = r'''#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

CAMPAIGN_ID = "broadband56_real_emx_balanced200k_tsmc65_v2"
FINGERPRINT = "f86a00efbf7756b7421b863bbb16c340db6b423640f63a3257d46c1af49eb55e"
FAILURE_FIELDS = (
    "raw_geometry_candidates", "duplicate_candidates", "geometry_bound_failures",
    "analytical_failures", "topology_failures", "cadence_failures",
    "calibre_blocking_failures", "emx_failures", "incomplete_frequency_failures",
    "s4p_parsing_failures", "s_to_z_failures", "feature_extraction_failures",
    "accepted_geometries",
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path

def evidence(path):
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": sha(path)}

parser = argparse.ArgumentParser()
parser.add_argument("--role", required=True)
parser.add_argument("--stage", required=True)
parser.add_argument("--cumulative-target", type=int, required=True)
parser.add_argument("--backend-out-dir", required=True)
parser.add_argument("--role-out-dir", required=True)
args = parser.parse_args()
backend = Path(args.backend_out_dir)
role_out = Path(args.role_out_dir)
results = backend / "results"
results.mkdir(parents=True, exist_ok=True)
rows = args.cumulative_target * 56

role_receipt = {
    "overall_status": "PASS",
    "campaign_id": CAMPAIGN_ID,
    "contract_fingerprint_sha256": FINGERPRINT,
    "stage": args.stage,
    "role": args.role,
    "simulator_action_taken": False,
}
if os.environ.get("MOCK_FAIL_ROLE") == args.role:
    role_receipt["overall_status"] = "FAIL"
    write(role_out / "ROLE_RECEIPT.json", role_receipt)
    raise SystemExit(7)

if args.role == "exact_audited_gds_emx_runner":
    write(results / "exact_gds_emx_receipt_index.json", {"overall_status": "PASS", "count": args.cumulative_target})

if args.role == "stage_attempt_finalizer":
    artifact_fields = (
        "attempt_ledger", "accepted_geometry_increment",
        "rejected_geometry_increment", "exact_gds_emx_receipt_index",
        "s4p_artifact_index", "long_features", "failure_funnel",
    )
    base_by_stage = {
        "GOLDEN": 0, "PILOT_32": 1, "PILOT_1000": 32,
        "PHASE_A": 1000, "PHASE_B": 50000, "PHASE_C": 150000,
    }
    if os.environ.get("MOCK_STAGE_SHORTFALL") == "1":
        context = json.loads(Path(os.environ["BROADBAND56_STAGE_CONTEXT"]).read_text())
        artifacts = {
            field: evidence(write(role_out / "attempt_artifacts" / f"{field}.csv", f"{field}\n"))
            for field in artifact_fields
        }
        funnel = {field: 0 for field in FAILURE_FIELDS}
        funnel["raw_geometry_candidates"] = 1
        funnel["analytical_failures"] = 1
        progress = {
            "schema": "rfic_transformer.broadband56_v2_stage_progress_receipt.v2",
            "overall_status": "INCOMPLETE",
            "decision": "CONTINUE_SAMPLING",
            "campaign_id": CAMPAIGN_ID,
            "contract_fingerprint_sha256": FINGERPRINT,
            "backend_id": "MARS_CADENCE_GDS_IDENTITY_CALIBRE_EMX_S4P_QA_V10",
            "stage": args.stage,
            "attempt_index": 1,
            "cumulative_target": args.cumulative_target,
            "accepted_before": base_by_stage[args.stage],
            "accepted_this_attempt": 0,
            "accepted_after": base_by_stage[args.stage],
            "remaining_after": args.cumulative_target - base_by_stage[args.stage],
            "raw_candidates_this_attempt": 1,
            "terminal_attempts_this_attempt": 1,
            "prior_progress_receipt_sha256": None,
            "backend_identity_manifest_sha256": context["backend_identity_manifest"]["sha256"],
            "full_campaign_authorization_receipt_sha256": context["full_campaign_authorization_receipt"]["sha256"],
            "safeguards": {
                "proxy_label_count": 0, "historical_label_count": 0,
                "interpolated_frequency_record_count": 0,
                "accepted_duplicate_geometry_count": 0,
                "accepted_blocking_calibre_count": 0,
                "manual_gds_modification_count": 0,
                "mixed_contract_fingerprint_count": 0,
            },
            "failure_accounting": funnel,
            "artifacts": artifacts,
            "simulator_action_taken": False,
            "stage_pass_receipt_created": False,
            "evidence_preserved": True,
        }
        progress_path = write(role_out / "STAGE_PROGRESS_RECEIPT.json", progress)
        role_receipt.update({
            "schema": "rfic_transformer.broadband56_v2_stage_attempt_finalizer.v1",
            "decision": "CONTINUE_SAMPLING",
            "accepted_before": base_by_stage[args.stage],
            "accepted_after": base_by_stage[args.stage],
            "cumulative_target": args.cumulative_target,
            "progress_receipt": evidence(progress_path),
            "cumulative_stage_inputs": None,
            "simulator_invoked_by_finalizer": False,
        })
    else:
        cumulative = {
            field: evidence(
                write(role_out / "cumulative_stage_inputs" / f"{field}.csv", f"{field}\n")
            )
            for field in artifact_fields
        }
        role_receipt.update({
            "schema": "rfic_transformer.broadband56_v2_stage_attempt_finalizer.v1",
            "decision": "STAGE_TARGET_REACHED",
            "accepted_before": base_by_stage[args.stage],
            "accepted_after": args.cumulative_target,
            "cumulative_target": args.cumulative_target,
            "progress_receipt": None,
            "cumulative_stage_inputs": cumulative,
            "simulator_invoked_by_finalizer": False,
        })

if args.role == "raw_products_finalizer":
    attempt = write(results / "attempt_ledger.jsonl", "attempt\n")
    accepted = write(results / "accepted_geometries.jsonl", "accepted\n")
    rejected = write(results / "rejected_geometries.jsonl", "rejected\n")
    artifact_index = write(results / "s4p_artifact_index.jsonl", "artifact\n")
    features = write(results / "long_features_manifest.json", {"rows": rows})
    funnel = {field: 0 for field in FAILURE_FIELDS}
    funnel["raw_geometry_candidates"] = args.cumulative_target
    funnel["accepted_geometries"] = args.cumulative_target
    funnel_path = write(results / "failure_funnel.json", funnel)
    raw = {
        "schema": "broadband56_raw_products_receipt_v1",
        "overall_status": "PASS",
        "decision": "USE_AS_FRESH_REAL_EMX_RAW_PRODUCTS",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": FINGERPRINT,
        "counts": {
            "accepted_geometries": args.cumulative_target,
            "accepted_s4p_geometries": args.cumulative_target,
            "accepted_feature_complete_geometries": args.cumulative_target,
            "s4p_artifacts": args.cumulative_target,
            "independent_designs": args.cumulative_target,
            "geometry_frequency_rows": rows,
        },
        "checks": {
            "all_accepted_s4p_are_fresh_exact_56_point_four_port": True,
            "long_features_bound_to_exact_s4p_s_and_z": True,
            "long_physical_features_recomputed_from_exact_s4p": True,
            "proxy_values_excluded_from_labels": True,
        },
        "failure_funnel": funnel,
        "inputs": {"attempt_ledger": evidence(attempt)},
        "outputs": {
            "accepted_geometries": evidence(accepted),
            "rejected_geometries": evidence(rejected),
            "artifact_index": evidence(artifact_index),
            "long_features_manifest": evidence(features),
            "failure_funnel": evidence(funnel_path),
        },
    }
    write(results / "raw_products_receipt.json", raw)

if args.role == "checkpoint_auditor":
    parseable = 0 if os.environ.get("MOCK_BAD_PARSEABLE") == "1" else rows
    coverage = write(results / "coverage_summary.json", {
        "overall_status": "PASS",
        "validity_counts": {
            "parseable_rows": parseable,
            "broadband_descriptor_valid": rows,
            "strict_lumped_valid": rows,
        },
    })
    status = write(results / "checkpoint_status.json", {
        "overall_status": "PASS", "checkpoint_status": "GOLDEN_COMPLETE"
    })
    checkpoint = {
        "overall_status": "PASS",
        "decision": "USE_CHECKPOINT",
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": FINGERPRINT,
        "expected_accepted": args.cumulative_target,
        "checks": [{"name": "exact_count", "pass": True}],
        "outputs": {
            "coverage_summary": evidence(coverage),
            "checkpoint_status": evidence(status),
        },
    }
    checkpoint_path = write(results / "checkpoint_receipt.json", checkpoint)
    write(results / "SHA256SUMS.txt", f"{sha(checkpoint_path)}  {checkpoint_path.name}\n")

terminal = {
    "campaign_histories_finalizer": ("campaign_history_receipt.json", "USE_AS_AUDITED_CAMPAIGN_HISTORY"),
    "training_readiness_finalizer": ("training_readiness_receipt.json", "USE_DERIVED_PRODUCTS_FOR_FUTURE_TRAINING_PREPARATION_ONLY"),
    "checkpoint_figure_renderer": ("checkpoint_figure_receipt.json", "USE_AS_AUDITED_STATIC_CHECKPOINT_FIGURES"),
    "final_delivery_auditor": ("final_delivery_receipt.json", "REPORT_COMPLETE_200K_WITH_SEPARATE_COVERAGE_STATUS"),
}
if args.role in terminal:
    name, decision = terminal[args.role]
    receipt = {
        "overall_status": "PASS",
        "decision": decision,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": FINGERPRINT,
    }
    if args.role == "final_delivery_auditor":
        receipt.update({
            "execution_completion": "COMPLETE_200K",
            "terminal_counts": {
                "accepted_geometries": 200000,
                "s4p_artifacts": 200000,
                "geometry_frequency_rows": 11200000,
            },
        })
    write(results / name, receipt)

write(role_out / "ROLE_RECEIPT.json", role_receipt)
'''


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(value, encoding="utf-8")
    return path


def _identity(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _snapshot() -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "campaign_id": CAMPAIGN_ID,
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
    mock_role = _write(tmp_path / "mock_role.py", MOCK_ROLE_SCRIPT)
    mock_role.chmod(0o755)

    profile = {
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
                        "argv": [
                            "--role", role,
                            "--stage", "{stage}",
                            "--cumulative-target", "{cumulative_target}",
                            "--backend-out-dir", "{backend_out_dir}",
                            "--role-out-dir", "{role_out_dir}",
                        ],
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
    private_config = _write(tmp_path / "private_config.json", {"frequency": "exact56"})
    profile_path = _write(tmp_path / "stage_execution_profile.json", {PROFILE_KEY: profile})

    executable_roles = {
        role
        for stage in STAGES
        for role in expected_stage_role_order(stage.name)
    }
    scripts: dict[str, dict[str, object]] = {}
    for role in REQUIRED_SCRIPT_ROLES:
        if role == "production_stage_backend":
            path = SCRIPT
        elif role in executable_roles:
            path = mock_role
        else:
            path = _write(tmp_path / "scripts" / f"{role}.py", f"# {role}\n")
        scripts[role] = _identity(path)
    scripts["production_stage_backend"]["executable"] = True

    runtimes: dict[str, dict[str, object]] = {}
    for role in REQUIRED_RUNTIME_ROLES:
        path = (
            private_config
            if role == "private_configuration"
            else profile_path
            if role == "stage_execution_profile"
            else _write(tmp_path / "runtime" / role, f"{role}\n")
        )
        if role in {"emx_wrapper", "python_executable"}:
            path.chmod(0o755)
        runtimes[role] = _identity(path)
    runtimes["emx_wrapper"]["executable"] = True
    runtimes["python_executable"]["executable"] = True

    history_one = _write(tmp_path / "history" / "one.json", {"overall_status": "PASS", "id": 1})
    history_two = _write(tmp_path / "history" / "two.json", {"overall_status": "PASS", "id": 2})
    history_gds = _write(tmp_path / "history" / "gds.json", {"overall_status": "PASS", "id": "gds"})

    def pass_record(path: Path) -> dict[str, object]:
        return {**_identity(path), "overall_status": "PASS"}

    command = [
        str(SCRIPT.resolve()),
        *[
            item
            for flag, placeholder in STAGE_COMMAND_ARGUMENTS
            for item in (flag, placeholder)
        ],
    ]
    manifest = {
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
            "private_configuration_sha256": _sha(private_config),
            "historical_configuration_sha256": "3" * 64,
            "operational_policy_approval_receipt_sha256": "4" * 64,
        },
        "script_identities": scripts,
        "runtime_identities": runtimes,
        "stage_commands": {
            stage.name: {
                "argv": command,
                "identity_argv_index": 0,
                "identity_role": "production_stage_backend",
                "identity_sha256": _sha(SCRIPT),
                "shell_used": False,
            }
            for stage in STAGES
        },
        "historical_backend_pass_receipts": [pass_record(history_one), pass_record(history_two)],
        "historical_gds_identity_pass_receipt": pass_record(history_gds),
    }
    manifest_path = _write(tmp_path / "backend_manifest.json", manifest)
    authorization = {
        "schema": FULL_CAMPAIGN_APPROVAL_SCHEMA,
        "overall_status": "PASS",
        "decision": FULL_CAMPAIGN_PASS_DECISION,
        "authorization_scope": FULL_CAMPAIGN_APPROVAL_SCOPE,
        "campaign_id": CAMPAIGN_ID,
        "contract_fingerprint_sha256": SCIENTIFIC_CONTRACT_FINGERPRINT,
        "backend_identity_manifest": {"sha256": _sha(manifest_path)},
        "accepted_geometry_target": 200_000,
        "replenished_attempt_rounds_authorized": True,
        "attempt_replenishment_contract": ATTEMPT_REPLENISHMENT_CONTRACT,
        "automatic_ordered_stage_execution_authorized": True,
        "cadence_authorized_within_current_stage": True,
        "calibre_authorized_within_current_stage": True,
        "emx_authorized_within_current_stage": True,
        "campaign_200k_authorized": True,
    }
    authorization_path = _write(tmp_path / "authorization.json", authorization)
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    return argparse.Namespace(
        stage="GOLDEN",
        cumulative_target=1,
        campaign_root=str(campaign_root),
        backend_out_dir=str(tmp_path / "backend_out"),
        full_campaign_receipt=str(authorization_path),
        backend_identity_manifest=str(manifest_path),
        resource_snapshot=str(snapshot_path),
        max_concurrency=1,
    )


def _argv(args: argparse.Namespace) -> list[str]:
    return [
        "--stage", args.stage,
        "--cumulative-target", str(args.cumulative_target),
        "--campaign-root", args.campaign_root,
        "--backend-out-dir", args.backend_out_dir,
        "--full-campaign-receipt", args.full_campaign_receipt,
        "--backend-identity-manifest", args.backend_identity_manifest,
        "--resource-snapshot", args.resource_snapshot,
        "--max-concurrency", str(args.max_concurrency),
    ]


def test_executes_exact_hash_bound_golden_role_chain(tmp_path: Path) -> None:
    args = _fixture(tmp_path)

    assert MODULE.main(_argv(args)) == 0

    out = Path(args.backend_out_dir)
    receipt = json.loads((out / "STAGE_RECEIPT.json").read_text(encoding="utf-8"))
    trace = json.loads((out / "STAGE_EXECUTION_TRACE.json").read_text(encoding="utf-8"))
    assert receipt["overall_status"] == "PASS"
    assert receipt["accepted_unique_geometries"] == 1
    assert trace["role_order"] == list(expected_stage_role_order("GOLDEN"))
    assert all(item["shell_used"] is False for item in trace["roles"])


def test_failed_role_preserves_fail_receipt_and_no_stage_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _fixture(tmp_path)
    monkeypatch.setenv("MOCK_FAIL_ROLE", "calibre_runner")

    assert MODULE.main(_argv(args)) == 2

    out = Path(args.backend_out_dir)
    failure = json.loads((out / "BACKEND_FAILURE.json").read_text(encoding="utf-8"))
    assert failure["overall_status"] == "FAIL"
    assert failure["stage_receipt_created"] is False
    assert not (out / "STAGE_RECEIPT.json").exists()


def test_checkpoint_count_mismatch_fails_after_mock_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _fixture(tmp_path)
    monkeypatch.setenv("MOCK_BAD_PARSEABLE", "1")

    assert MODULE.main(_argv(args)) == 2

    out = Path(args.backend_out_dir)
    failure = json.loads((out / "BACKEND_FAILURE.json").read_text(encoding="utf-8"))
    assert "parseable row count mismatch" in failure["error"]


def test_shortfall_stops_before_terminal_roles_and_preserves_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _fixture(tmp_path)
    monkeypatch.setenv("MOCK_STAGE_SHORTFALL", "1")

    assert MODULE.main(_argv(args)) == 0

    out = Path(args.backend_out_dir)
    progress = json.loads(
        (out / "STAGE_PROGRESS_RECEIPT.json").read_text(encoding="utf-8")
    )
    assert progress["decision"] == "CONTINUE_SAMPLING"
    assert progress["accepted_after"] == 0
    assert not (out / "STAGE_RECEIPT.json").exists()
    assert not list((out / "roles").glob("*raw_products_finalizer"))
    assert not list((out / "roles").glob("*checkpoint_auditor"))
    assert not (out / "STAGE_RECEIPT.json").exists()


def test_role_script_hash_drift_is_rejected_before_execution(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    manifest = json.loads(Path(args.backend_identity_manifest).read_text(encoding="utf-8"))
    role_path = Path(manifest["script_identities"]["calibre_runner"]["path"])
    role_path.write_text(role_path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")

    assert MODULE.main(_argv(args)) == 2
    assert not (Path(args.backend_out_dir) / "STAGE_RECEIPT.json").exists()


def test_no_clobber_rejects_existing_backend_output(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    out = Path(args.backend_out_dir)
    out.mkdir()
    marker = _write(out / "existing.txt", "preserve\n")

    assert MODULE.main(_argv(args)) == 2
    assert marker.read_text(encoding="utf-8") == "preserve\n"


def test_out_of_order_stage_is_rejected_before_role_execution(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    args.stage = "PILOT_32"
    args.cumulative_target = 32

    assert MODULE.main(_argv(args)) == 2
    assert not (Path(args.backend_out_dir) / "STAGE_RECEIPT.json").exists()
