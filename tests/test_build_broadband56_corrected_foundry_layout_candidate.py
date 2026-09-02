from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_broadband56_corrected_foundry_layout_candidate.py"
SPEC = importlib.util.spec_from_file_location("foundry_layout_candidate_builder", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _baseline_payload() -> dict:
    return {
        "target": {
            "topology_mode": "1t1t",
            "frequency_start_hz": 5_000_000_000.0,
            "frequency_stop_hz": 60_000_000_000.0,
            "frequency_step_hz": 1_000_000_000.0,
            "band_points": 56,
        },
        "topology": {
            "primary": {"turns": 1, "center_tap": True},
            "secondary": {"turns": 1, "center_tap": True},
        },
        "emx": {
            "port_mode": "single_ended_shield_grounded",
            "cadence_pin_purpose": 51,
            "ground_unused_s8p_ports": False,
            "power_line_8port": {
                "touchstone_mode": "signal_4_grounded_aux",
                "port_ground_reference": "shield",
                "port_map": ["P001", "P002", "P003", "P004"],
            },
        },
        "bounds": {
            "primary": {
                "outer_width_um": [160.0, 520.0],
                "outer_height_um": [160.0, 520.0],
                "trace_width_um": [3.0, 12.0],
                "terminal_y_span_um": [20.0, 90.0],
                "feed_extension_um": [100.0, 320.0],
            },
            "secondary": {
                "outer_width_um": [160.0, 520.0],
                "outer_height_um": [160.0, 520.0],
                "trace_width_um": [3.0, 12.0],
                "terminal_y_span_um": [20.0, 90.0],
                "feed_extension_um": [100.0, 320.0],
            },
            "offset_um": [-90.0, 90.0],
        },
    }


def _write_baseline(path: Path) -> Path:
    path.write_text(yaml.safe_dump(_baseline_payload(), sort_keys=False), encoding="utf-8")
    return path


def _controller_snapshot() -> dict:
    return {
        "audited_utc": "2026-09-02T10:00:00+00:00",
        "controller_count": 1,
        "controllers": [
            {
                "pid": MODULE.EXPECTED_CONTROLLER_PID,
                "state": "T (stopped)",
                "command_sha256": "1" * 64,
            }
        ],
        "authoritative_controller_pid": MODULE.EXPECTED_CONTROLLER_PID,
        "project_active_simulator_count": 0,
        "project_active_simulator_pids": [],
        "process_scan_method": "test-fixture",
    }


def _build_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[argparse.Namespace, Path]:
    baseline = _write_baseline(tmp_path / "baseline.yaml")
    deck = tmp_path / "foundry.svrf"
    deck.write_text("DRC deck\n", encoding="utf-8")
    full_candidate = _write_json(
        tmp_path / "full_candidate.json",
        {"campaign_id": MODULE.CAMPAIGN_ID},
    )
    monkeypatch.setattr(MODULE, "EXPECTED_FULL_CAMPAIGN_CANDIDATE_SHA256", _sha(full_candidate))
    monkeypatch.setattr(MODULE, "EXPECTED_CALIBRE_RULE_DECK_SHA256", _sha(deck))

    failed_golden_001 = _write_json(
        tmp_path / "failed_golden_001.json",
        {"overall_status": "INCOMPLETE", "stage": "GOLDEN"},
    )
    evidence_payloads = {
        "full_campaign_authorization": {
            "overall_status": "PASS",
            "decision": MODULE.FULL_CAMPAIGN_PASS_DECISION,
            "authorization_scope": MODULE.FULL_CAMPAIGN_APPROVAL_SCOPE,
            "campaign_id": MODULE.CAMPAIGN_ID,
            "contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
            "approved_candidate": {"sha256": _sha(full_candidate)},
            "private_identity_bindings": {
                "private_configuration": {"sha256": _sha(baseline)}
            },
        },
        "full_campaign_candidate": {"campaign_id": MODULE.CAMPAIGN_ID},
        "preparation_receipt": {
            "overall_status": "PASS",
            "decision": "PREPARED_FOR_GOLDEN_GATE",
            "campaign_id": MODULE.CAMPAIGN_ID,
        },
        "campaign_contract_frozen": {
            "campaign_id": MODULE.CAMPAIGN_ID,
            "contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
        },
        "backend_identity_manifest": {
            "campaign_id": MODULE.CAMPAIGN_ID,
            "contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
            "runtime_identities": {
                "private_configuration": {"sha256": _sha(baseline)},
                "calibre_rule_deck": {
                    "path": str(deck),
                    "sha256": _sha(deck),
                    "size_bytes": deck.stat().st_size,
                },
            },
        },
        "backend_identity_verification": {
            "overall_status": "PASS",
            "decision": "USE_HASH_BOUND_PRODUCTION_BACKEND",
        },
        "failed_golden_001": json.loads(failed_golden_001.read_text()),
        "golden_001_preservation": {
            "overall_status": "PASS",
            "mark": "GOLDEN_ATTEMPT_001_DRC_FAIL_NOT_ACCEPTED",
            "blocking_drc_violation_count": 337,
            "key_evidence": {
                "stage_progress_receipt": {"sha256": _sha(failed_golden_001)}
            },
        },
        "failed_safe_anchor_golden": {
            "overall_status": "FAIL",
            "decision": "BLOCKED_SYSTEMIC_DRC_CONTRACT_MISMATCH",
            "gates": {"calibre_blocking_violations": 331, "emx": "NOT_RUN"},
            "candidate": {"historical_safe_anchor_id": MODULE.EXPECTED_SAFE_ANCHOR_ID},
        },
        "safe_anchor_source": {
            "overall_status": "PASS",
            "historical_candidate_id": MODULE.EXPECTED_SAFE_ANCHOR_ID,
        },
        "root_cause_addendum": {
            "overall_status": "FAIL",
            "decision": "BLOCKED_SYSTEMIC_DRC_CONTRACT_MISMATCH",
            "simulator_action_taken_for_addendum": False,
        },
        "controller_pause_receipt": {
            "overall_status": "PASS",
            "controller_pid": MODULE.EXPECTED_CONTROLLER_PID,
            "controller_alive": True,
            "controller_killed": False,
            "controller_restarted": False,
        },
    }
    evidence_paths: dict[str, Path] = {}
    for name, payload in evidence_payloads.items():
        if name == "full_campaign_candidate":
            evidence_paths[name] = full_candidate
        elif name == "failed_golden_001":
            evidence_paths[name] = failed_golden_001
        else:
            evidence_paths[name] = _write_json(tmp_path / f"{name}.json", payload)

    values: dict[str, object] = {
        "out_dir": str(tmp_path / "out"),
        "generated_utc": "2026-09-02T10:00:00+00:00",
        "baseline_config": str(baseline),
        "baseline_config_sha256": _sha(baseline),
        "runtime_repo": str(tmp_path / "runtime"),
        "authoritative_controller_pid": MODULE.EXPECTED_CONTROLLER_PID,
    }
    for name, flag in MODULE.EVIDENCE_ARGUMENTS.items():
        attribute = flag.replace("-", "_")
        values[attribute] = str(evidence_paths[name])
        values[attribute + "_sha256"] = _sha(evidence_paths[name])
    args = argparse.Namespace(**values)
    monkeypatch.setattr(
        MODULE,
        "_git_identity",
        lambda _path: {
            "path": str(tmp_path / "runtime"),
            "head_commit": MODULE.EXACT_PUBLIC_CODE_COMMIT,
            "tree_sha1": "2" * 40,
            "working_tree_clean": True,
        },
    )
    monkeypatch.setattr(MODULE, "_controller_snapshot", lambda _pid: _controller_snapshot())
    return args, baseline


def test_exact_five_leaf_correction_and_frequency_contract(tmp_path: Path) -> None:
    baseline_path = _write_baseline(tmp_path / "baseline.yaml")
    baseline_text = baseline_path.read_text(encoding="utf-8")
    corrected_text, corrected = MODULE.build_corrected_configuration(baseline_text)
    baseline = yaml.safe_load(baseline_text)
    changes = MODULE.configuration_changes(baseline, corrected)

    assert corrected_text != baseline_text
    assert corrected["emx"]["foundry_layout"] == MODULE.EXPECTED_FOUNDRY_LAYOUT
    assert {item["path"] for item in changes} == set(MODULE.EXPECTED_CHANGED_PATHS)
    MODULE._validate_only_approved_changes(changes)
    MODULE._validate_corrected_contract(corrected)
    assert corrected["target"]["band_points"] == 56
    assert corrected["target"]["frequency_step_hz"] == 1_000_000_000.0


def test_unauthorized_field_change_is_rejected(tmp_path: Path) -> None:
    baseline_path = _write_baseline(tmp_path / "baseline.yaml")
    baseline = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
    _, corrected = MODULE.build_corrected_configuration(
        baseline_path.read_text(encoding="utf-8")
    )
    corrected["target"]["band_points"] = 55
    changes = MODULE.configuration_changes(baseline, corrected)
    with pytest.raises(MODULE.CandidateBuildError, match="unauthorized"):
        MODULE._validate_only_approved_changes(changes)


def test_builds_bound_execution_free_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, baseline = _build_args(tmp_path, monkeypatch)
    result = MODULE.build_artifacts(args, out_dir=Path(args.out_dir))

    corrected_path = Path(result["corrected_config_path"])
    diff_path = Path(result["diff_path"])
    candidate_path = Path(result["candidate_path"])
    receipt_path = Path(result["verification_receipt_path"])
    corrected = yaml.safe_load(corrected_path.read_text(encoding="utf-8"))
    diff = json.loads(diff_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert corrected["emx"]["foundry_layout"] == MODULE.EXPECTED_FOUNDRY_LAYOUT
    assert diff["changed_field_count"] == 5
    assert diff["summary"] == {
        "DRC_RULE_CHANGE": "no",
        "FREQUENCY_CONTRACT_CHANGE": "no",
        "GEOMETRY_BOUNDS_CHANGE": "no",
        "GEOMETRY_GENERATION_CORRECTION": "yes",
        "overall_status": "PASS",
        "unapproved_field_change_count": 0,
    }
    assert candidate["candidate_file_authorizes_execution"] is False
    assert candidate["simulator_action_taken"] is False
    assert candidate["corrected_public_runtime"]["head_commit"] == MODULE.EXACT_PUBLIC_CODE_COMMIT
    assert candidate["controller_invariant"]["controller_count"] == 1
    assert candidate["controller_invariant"]["project_active_simulator_count"] == 0
    assert candidate["frequency_contract"]["exact_hz"] == list(MODULE.FREQUENCY_GRID_HZ)
    assert MODULE.validate_candidate(candidate) == []
    requirements = candidate["generated_layout_audit_contract"]["requirements"]
    assert [item["name"] for item in requirements] == list(
        MODULE.GENERATED_LAYOUT_AUDIT_REQUIREMENTS
    )
    assert all(item["current_result"] == "NOT_RUN" for item in requirements)
    assert receipt["overall_status"] == "PASS"
    assert receipt["layout_level_validation_status"] == "NOT_RUN_PENDING_CORRECTED_GDS"
    assert result["corrected_config_sha256"] == _sha(corrected_path)
    assert result["diff_sha256"] == _sha(diff_path)
    assert result["candidate_sha256"] == _sha(candidate_path)
    assert candidate["previous_private_configuration"]["sha256"] == _sha(baseline)


def test_wrong_sha_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _ = _build_args(tmp_path, monkeypatch)
    args.baseline_config_sha256 = "0" * 64
    with pytest.raises(MODULE.CandidateBuildError, match="SHA-256 mismatch"):
        MODULE.build_artifacts(args, out_dir=Path(args.out_dir))


def test_no_clobber_rejected_before_any_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _ = _build_args(tmp_path, monkeypatch)
    out_dir = Path(args.out_dir)
    out_dir.mkdir()
    argv = []
    for name, value in vars(args).items():
        flag = "--" + name.replace("_", "-")
        argv.extend([flag, str(value)])
    assert MODULE.main(argv) == 2


def test_candidate_rejects_claimed_layout_execution() -> None:
    candidate = {
        "schema": MODULE.CANDIDATE_SCHEMA,
        "campaign_id": MODULE.CAMPAIGN_ID,
        "scientific_contract_fingerprint_sha256": MODULE.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "requested_authorization_scope": MODULE.REQUESTED_AUTHORIZATION_SCOPE,
        "candidate_file_authorizes_execution": False,
        "simulator_action_taken": True,
    }
    assert "simulator_free" in MODULE.validate_candidate(candidate)
