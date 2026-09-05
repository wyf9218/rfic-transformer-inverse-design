"""Synthetic post-processing fixtures only; no simulator or production labels."""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from rfic_transformer_inverse_design.campaigns import broadband56_s4p_qa as qa
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import FREQUENCY_GRID_HZ
from rfic_transformer_inverse_design.campaigns.broadband56_golden_source import SAFE_ANCHOR_SOURCE


ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = _load("golden_qa_source_fixture", "tests/test_broadband56_golden_source.py")
EXACT = _load("golden_qa_exact_fixture", "tests/test_broadband56_exact_gds_emx.py")
QA_FIXTURE = _load("golden_qa_numerical_fixture", "tests/test_broadband56_s4p_qa.py")
BATCH = _load("golden_qa_batch", "scripts/run_broadband56_v2_exact56_s4p_qa_batch.py")
PRODUCTS = _load("golden_attempt_products", "scripts/build_broadband56_v2_stage_attempt_products.py")
record = SOURCE.BUILDER._file_record
FINALIZER = _load("golden_stage_finalizer", "scripts/finalize_broadband56_stage_attempt.py")
STAGE_BACKEND = _load("golden_production_stage_backend", "scripts/run_broadband56_v2_production_stage_backend.py")
PHASE_QUEUE = _load("golden_phase_queue", "scripts/build_broadband56_phase_a_queue.py")
CHECKPOINT = _load("golden_pilot_checkpoint", "scripts/materialize_broadband56_v2_adaptive_checkpoint.py")
FINALIZER_FIXTURE = _load("golden_pilot_finalizer_fixture", "tests/test_finalize_broadband56_stage_attempt.py")


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Golden QA must not launch any child or simulator")
    monkeypatch.setattr(subprocess, "Popen", denied)


def _json(path, value):
    path.write_text(json.dumps(value))
    return path


def _read_rows(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path):
    source_path, source, kwargs = SOURCE._bound(tmp_path)
    exact_root = tmp_path / "synthetic_exact_evidence"
    exact_root.mkdir()
    fixture = EXACT._build_fixture(exact_root)
    geometry = kwargs["geometry_sha256"]
    zero_path = fixture["calibre_receipt"]
    zero = json.loads(zero_path.read_text())
    zero.update(candidate_id_sha256=geometry, geometry_identity_sha256=geometry, source_files_unchanged=True)
    for key, value in source["corrected_private_configuration"].items():
        zero[{"path": "config_path", "sha256": "config_sha256", "size_bytes": "config_size_bytes"}[key]] = value
    evidence = {}
    for name in ("evaluation_summary", "foundry_layout_audit", "gds_physical_identity_audit", "source_geometry_audit", "emx_process_file"):
        path = _json(exact_root / f"synthetic_{name}.json", {"overall_status": "PASS", "test_only": True})
        evidence[name] = record(path)
    audit_path = _json(exact_root / "synthetic_geometry_audit.json", {
        "schema": "rfic_transformer.broadband56_v2_current_contract_calibre_delegate_geometry_audit.v2",
        "overall_status": "PASS", "decision": "CURRENT_CONTRACT_GDS_READY_FOR_FOUNDRY_CALIBRE",
        "candidate_id_sha256": geometry, "candidate_geometry_identity_sha256": geometry,
        "gds_path": str(fixture["gds"]), "gds_sha256": fixture["gds_sha256"],
        "gds_top_cell": "TRANSFORMER",
        "geometry_metric_states": {"power_line_check": "PASS", "via_stack_check": "PASS"},
        "checks": {"foundry_layout_audit_pass": True}, "source_evidence": evidence,
    })
    zero["source_geometry_audit"] = record(audit_path)
    zero["source_calibre_summary"] = record(_json(exact_root / "synthetic_drc_summary.json", {"blocking_drc_violation_count": 0}))
    _json(zero_path, zero)
    s4p = exact_root / "synthetic_fresh.s4p"
    QA_FIXTURE._write_physical_s4p(s4p, np.asarray(FREQUENCY_GRID_HZ, dtype=float))
    emx_path = QA_FIXTURE._write_emx_receipt(exact_root, s4p_path=s4p, candidate_sha256=geometry, geometry_sha256=geometry)
    emx = json.loads(emx_path.read_text())
    command = _json(exact_root / "synthetic_emx_command.json", ["TEST_ONLY_NO_SOLVER"])
    emx["emx_output"].update(emx_command_path=str(command), emx_command_size_bytes=command.stat().st_size, emx_command_sha256=record(command)["sha256"])
    emx.update({
        "private_configuration": source["corrected_private_configuration"],
        "source_exact_gds": record(fixture["gds"]),
        "source_layout_manifest": record(fixture["manifest"]),
        "source_calibre_zero_blocking_receipt": record(zero_path),
        "source_calibre_report": record(Path(zero["calibre_report_path"])),
        "full_campaign_authorization_receipt": record(fixture["authorization_receipt"]),
        "top_cell": "TRANSFORMER",
    })
    emx["manifest_contract"]["top_cell"] = "TRANSFORMER"
    _json(emx_path, emx)
    index = QA_FIXTURE._write_input_index(exact_root, receipt_path=emx_path, geometry_sha256=geometry, candidate_sha256=geometry)
    rows = _read_rows(index)
    rows[0].update(geometry_id=geometry, acquisition_source=SAFE_ANCHOR_SOURCE)
    _write_rows(index, rows)
    return {"source": source_path, "input": index, "emx": emx_path, "zero": zero_path, "audit": audit_path, "s4p": s4p}


def _run(fixture, out):
    return qa.build_exact56_s4p_qa_products(
        input_index_path=fixture["input"], out_dir=out, expected_geometry_count=1,
        golden_source_receipt=record(fixture["source"]), stage="GOLDEN",
    )


def test_historical_golden_has_real_numerical_qa_but_zero_production_increment(tmp_path):
    fixture = _fixture(tmp_path)
    result = _run(fixture, tmp_path / "qa")
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    assert receipt["decision"] == qa.VALIDATION_QA_PASS_DECISION
    assert receipt["decision"] != qa.QA_PASS_DECISION
    assert receipt["geometry_frequency_rows"] == 56
    assert receipt["production_geometry_frequency_rows"] == 0
    binding = receipt["golden_validation"]
    assert binding["golden_stage_gate_eligible"] is True
    assert binding["production_dataset_accepted_eligible"] is False
    assert binding["production_accepted_count_delta"] == 0
    assert binding["source_binding"] == record(fixture["source"])
    assert binding["s4p"] == record(fixture["s4p"])
    assert [int(r["frequency_hz"]) for r in _read_rows(Path(result["long_features_path"]))] == list(FREQUENCY_GRID_HZ)


@pytest.mark.parametrize("key", [
    "private_configuration", "source_exact_gds", "source_layout_manifest",
    "source_calibre_zero_blocking_receipt", "source_calibre_report", "full_campaign_authorization_receipt",
])
def test_source_gds_calibre_or_authorization_drift_rejected(tmp_path, key):
    fixture = _fixture(tmp_path)
    emx = json.loads(fixture["emx"].read_text())
    path = Path(emx[key]["path"])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(qa.Broadband56S4pQaError):
        _run(fixture, tmp_path / "qa")
    assert not (tmp_path / "qa" / qa.QA_RECEIPT_NAME).exists()


@pytest.mark.parametrize("key", ["source_geometry_audit", "source_calibre_summary"])
def test_nested_calibre_evidence_drift_rejected(tmp_path, key):
    fixture = _fixture(tmp_path)
    zero = json.loads(fixture["zero"].read_text())
    Path(zero[key]["path"]).write_text("{}")
    with pytest.raises(qa.Broadband56S4pQaError):
        _run(fixture, tmp_path / "qa")


@pytest.mark.parametrize("key", ["foundry_layout_audit", "evaluation_summary", "gds_physical_identity_audit", "emx_process_file"])
def test_nested_geometry_evidence_drift_rejected(tmp_path, key):
    fixture = _fixture(tmp_path)
    audit = json.loads(fixture["audit"].read_text())
    Path(audit["source_evidence"][key]["path"]).write_text("{}")
    with pytest.raises(qa.Broadband56S4pQaError):
        _run(fixture, tmp_path / "qa")


def test_source_binding_does_not_bypass_frequency_validation(tmp_path):
    fixture = _fixture(tmp_path)
    QA_FIXTURE._write_physical_s4p(fixture["s4p"], np.asarray(FREQUENCY_GRID_HZ[:-1], dtype=float))
    emx = json.loads(fixture["emx"].read_text())
    emx["emx_output"]["touchstone_size_bytes"] = fixture["s4p"].stat().st_size
    emx["emx_output"]["touchstone_sha256"] = record(fixture["s4p"])["sha256"]
    _json(fixture["emx"], emx)
    rows = _read_rows(fixture["input"])
    rows[0]["exact_gds_emx_receipt_sha256"] = record(fixture["emx"])["sha256"]
    _write_rows(fixture["input"], rows)
    with pytest.raises(qa.Broadband56S4pQaError):
        _run(fixture, tmp_path / "qa")


def test_historical_source_without_binding_cannot_enter_production_qa(tmp_path):
    fixture = _fixture(tmp_path)
    with pytest.raises(qa.Broadband56S4pQaError, match="acquisition_source"):
        qa.build_exact56_s4p_qa_products(input_index_path=fixture["input"], out_dir=tmp_path / "qa")


def test_qa_batch_candidate_preserves_validation_only_identity(tmp_path):
    fixture = _fixture(tmp_path)
    result = BATCH._run_one(
        row=_read_rows(fixture["input"])[0], submitted_sequence=1,
        candidate_dir=tmp_path / "candidate_qa", stage="GOLDEN",
        golden_source_receipt=record(fixture["source"]),
    )
    assert result["overall_status"] == "PASS", result
    assert result["golden_validation"]["production_accepted_count_delta"] == 0


def _stage_fixture(tmp_path, *, failed_emx=False):
    fixture = _fixture(tmp_path)
    source = json.loads(fixture["source"].read_text())
    row = _read_rows(Path(source["candidate_queue"]["path"]))[0]
    emx = json.loads(fixture["emx"].read_text())
    from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden
    order = STAGE_BACKEND.expected_stage_role_order("GOLDEN")
    profile = _json(tmp_path / "synthetic_profile.json", {
        "stages": {"GOLDEN": {"golden_terminal_mode": golden.TERMINAL_MODE}},
    })
    manifest_path = _json(tmp_path / "synthetic_backend.json", {
        "campaign_id": PRODUCTS.CAMPAIGN_ID,
        "contract_fingerprint_sha256": PRODUCTS.SCIENTIFIC_CONTRACT_FINGERPRINT,
        "script_identities": {
            **{key: record(Path(STAGE_BACKEND.__file__)) for key in order},
            "production_stage_backend": record(Path(STAGE_BACKEND.__file__)),
            "stage_attempt_product_builder": record(Path(PRODUCTS.__file__)),
            "full_band_s4p_qa_builder": record(Path(BATCH.__file__)),
            "full_band_s4p_qa_module": record(Path(qa.__file__)),
        },
        "runtime_identities": {"stage_execution_profile": record(profile)},
    })
    auth_path = Path(emx["full_campaign_authorization_receipt"]["path"])
    auth = json.loads(auth_path.read_text())
    auth["backend_identity_manifest"] = record(manifest_path)
    _json(auth_path, auth)
    emx["full_campaign_authorization_receipt"] = record(auth_path)
    _json(fixture["emx"], emx)
    rows = _read_rows(fixture["input"])
    rows[0]["exact_gds_emx_receipt_sha256"] = record(fixture["emx"])["sha256"]
    _write_rows(fixture["input"], rows)

    def table(name, rows, fields=None):
        path = tmp_path / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return record(path)

    empty = table("empty.csv", [], ["candidate_id_sha256", "geometry_sha256", "terminal_stage", "error"])
    role_paths = {}

    def role(name, prefix, **fields):
        path = _json(tmp_path / f"{name}_role.json", {
            "overall_status": "PASS", "decision": PRODUCTS.EXPECTED_DECISIONS[name],
            "campaign_id": PRODUCTS.CAMPAIGN_ID,
            "contract_fingerprint_sha256": PRODUCTS.SCIENTIFIC_CONTRACT_FINGERPRINT,
            "stage": "GOLDEN", "backend_identity_manifest": record(manifest_path),
            "full_campaign_authorization_receipt": record(auth_path),
            "submitted_count": 1, f"{prefix}_pass_count": 1, f"{prefix}_fail_count": 0,
            "failure_index": empty, **fields,
        })
        role_paths[name] = path
        return record(path)

    role("cadence", "cadence", input_candidate_queue=source["candidate_queue"], pass_candidate_queue=source["candidate_queue"])
    gds = role("gds", "identity", pass_index=table("gds_pass.csv", [{**row, "gds_path": emx["source_exact_gds"]["path"], "gds_sha256": emx["source_exact_gds"]["sha256"]}]))
    calibre = role("calibre", "calibre", input_role_receipt=gds, pass_index=source["candidate_queue"])
    zero = role("calibre_zero", "receipt", input_role_receipt=calibre, pass_index=table("zero_pass.csv", [{**row, "calibre_receipt_path": str(fixture["zero"]), "calibre_receipt_sha256": record(fixture["zero"])["sha256"]}]))
    if failed_emx:
        failure_evidence = _json(tmp_path / "synthetic_emx_failure.json", {"error": "mock import failure"})
        failures = table("emx_fail.csv", [{**row, "terminal_stage": "EMX_FAILURE", "error": "mock import failure", "fresh_real_emx": "false"}])
        emx_pass = table("empty_emx_pass.csv", [], list(rows[0]))
    else:
        failure_evidence = fixture["emx"]
        failures = empty
        emx_pass = record(fixture["input"])
    exact_role = role("exact_emx", "emx", input_role_receipt=zero, pass_index=emx_pass,
        failure_index=failures, emx_pass_count=0 if failed_emx else 1, emx_fail_count=1 if failed_emx else 0,
        delegate_evidence_index=table("emx_evidence.csv", [{**row, "delegate_result_path": str(failure_evidence), "delegate_result_sha256": record(failure_evidence)["sha256"]}]))
    qa_dir = tmp_path / "batch_qa"
    BATCH.run_batch(SimpleNamespace(
        stage="GOLDEN", max_concurrency=1, backend_identity_manifest=str(manifest_path),
        full_campaign_receipt=str(auth_path), input_role_receipt=exact_role["path"],
        golden_source_receipt=str(fixture["source"]),
    ), out_dir=qa_dir)
    role_paths["exact56"] = qa_dir / BATCH.RECEIPT_NAME
    args = SimpleNamespace(
        stage="GOLDEN", current_accepted=0, backend_identity_manifest=str(manifest_path),
        full_campaign_receipt=str(auth_path), golden_source_receipt=str(fixture["source"]),
        cadence_role_receipt=str(role_paths["cadence"]), gds_identity_role_receipt=str(role_paths["gds"]),
        calibre_role_receipt=str(role_paths["calibre"]), calibre_zero_role_receipt=str(role_paths["calibre_zero"]),
        exact_gds_emx_role_receipt=str(role_paths["exact_emx"]), exact56_role_receipt=str(role_paths["exact56"]),
    )
    return args


def test_stage_aggregation_retains_golden_evidence_without_accepted_rows(tmp_path):
    args = _stage_fixture(tmp_path)
    result = PRODUCTS.build_attempt_products(args, out_dir=tmp_path / "products")
    assert result["schema"] == PRODUCTS.VALIDATION_RECEIPT_SCHEMA
    assert result["golden_validation_status"] == "PASS"
    assert (result["raw_candidate_count"], result["accepted_count"], result["rejected_count"]) == (1, 0, 0)
    assert result["validation_geometry_count"] == 1
    assert result["validation_feature_rows"] == 56
    assert result["geometry_frequency_rows"] == 0
    assert result["failure_accounting"]["golden_validation_geometries"] == 1
    for key in ("accepted_geometry_increment", "exact_gds_emx_receipt_index", "s4p_artifact_index", "long_features"):
        assert _read_rows(Path(result[key]["path"])) == []
    assert len(_read_rows(Path(result["validation_products"]["long_features"]["path"]))) == 56
    ledger = _read_rows(Path(result["attempt_ledger"]["path"]))
    assert ledger[0]["terminal_stage"] == "GOLDEN_VALIDATION_PASS"
    assert ledger[0]["accepted_sequence"] == ""
    assert ledger[0]["duplicate_status"] == "HISTORICAL_NOT_PRODUCTION"


def test_failed_historical_emx_has_no_golden_pass_or_production_increment(tmp_path):
    args = _stage_fixture(tmp_path, failed_emx=True)
    result = PRODUCTS.build_attempt_products(args, out_dir=tmp_path / "products")
    assert result["golden_validation_status"] == "FAIL"
    assert result["golden_validation"] is None
    assert result["accepted_count"] == result["validation_geometry_count"] == result["validation_feature_rows"] == 0
    assert result["rejected_count"] == result["failure_accounting"]["emx_failures"] == 1


def test_aggregator_rejects_independently_replaced_batch_features(tmp_path):
    args = _stage_fixture(tmp_path)
    role_path = Path(args.exact56_role_receipt)
    role = json.loads(role_path.read_text())
    features = Path(role["long_features"]["path"])
    rows = _read_rows(features)
    rows[0]["frequency_hz"] = "1"
    _write_rows(features, rows)
    role["long_features"] = record(features)
    _json(role_path, role)
    with pytest.raises(PRODUCTS.AttemptProductError, match="differ from"):
        PRODUCTS.build_attempt_products(args, out_dir=tmp_path / "products")


def test_aggregator_cannot_treat_anchor_as_production_without_source_binding(tmp_path):
    args = _stage_fixture(tmp_path)
    args.golden_source_receipt = None
    with pytest.raises(PRODUCTS.AttemptProductError, match="acquisition source"):
        PRODUCTS.build_attempt_products(args, out_dir=tmp_path / "products")


def test_historical_binding_is_rejected_in_pilot_stage(tmp_path):
    fixture = _fixture(tmp_path)
    with pytest.raises(qa.Broadband56S4pQaError, match="historical GOLDEN"):
        qa.build_exact56_s4p_qa_products(
            input_index_path=fixture["input"], out_dir=tmp_path / "qa",
            golden_source_receipt=record(fixture["source"]), stage="PILOT_32",
        )


def test_rehashed_qa_cannot_change_historical_accepted_delta(tmp_path):
    from rfic_transformer_inverse_design.campaigns.broadband56_golden_source import (
        GoldenSourceError, validate_safe_anchor_qa_receipt,
    )
    fixture = _fixture(tmp_path)
    result = _run(fixture, tmp_path / "qa")
    path = Path(result["receipt_path"])
    receipt = json.loads(path.read_text())
    receipt["golden_validation"]["production_accepted_count_delta"] = 1
    receipt["production_accepted_count_delta"] = 1
    _json(path, receipt)
    with pytest.raises(GoldenSourceError, match="production-count"):
        validate_safe_anchor_qa_receipt(record(fixture["source"]), record(path), exact_emx_receipt_record=record(fixture["emx"]))


def _finalizer_fixture(tmp_path, *, failed_emx=False):
    args = _stage_fixture(tmp_path, failed_emx=failed_emx)
    products_dir = tmp_path / "products"
    attempt = PRODUCTS.build_attempt_products(args, out_dir=products_dir)
    final_args = SimpleNamespace(
        stage="GOLDEN", campaign_root=str(tmp_path / "campaign"),
        backend_identity_manifest=args.backend_identity_manifest,
        full_campaign_receipt=args.full_campaign_receipt, simulator_action_taken=True,
        golden_attempt_products_receipt=str(products_dir / PRODUCTS.RECEIPT_NAME),
        **{key: attempt[key]["path"] for key in FINALIZER.STAGE_PROGRESS_ARTIFACT_FIELDS},
    )
    return final_args, attempt


def test_validation_only_golden_finalizes_at_zero_not_incomplete(tmp_path):
    from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden
    args, attempt = _finalizer_fixture(tmp_path)
    result = FINALIZER.finalize_stage_attempt(args, out_dir=tmp_path / "finalizer")
    assert result["decision"] == golden.FINALIZER_DECISION
    assert result["accepted_after"] == 0
    assert result["cumulative_stage_inputs"] is None
    assert result["progress_receipt"] is None
    assert golden.validate_finalizer(result,
        backend_sha256=record(Path(args.backend_identity_manifest))["sha256"],
        authorization_sha256=record(Path(args.full_campaign_receipt))["sha256"],
    )["golden_validation"] == attempt["golden_validation"]


def test_failed_emx_cannot_finalize_validation_only_golden(tmp_path):
    args, _ = _finalizer_fixture(tmp_path, failed_emx=True)
    with pytest.raises(FINALIZER.StageAttemptFinalizationError, match="Golden validation failed"):
        FINALIZER.finalize_stage_attempt(args, out_dir=tmp_path / "finalizer")
    assert not (tmp_path / "finalizer").exists()


@pytest.mark.parametrize("field,value", [("accepted_count", 1), ("validation_feature_rows", 55),
    ("golden_validation_status", "FAIL"), ("stage", "PILOT_32")])
def test_rehashed_aggregation_cannot_forge_golden_terminal(tmp_path, field, value):
    args, attempt = _finalizer_fixture(tmp_path)
    attempt[field] = value
    _json(Path(args.golden_attempt_products_receipt), attempt)
    with pytest.raises(FINALIZER.StageAttemptFinalizationError, match="Golden validation failed"):
        FINALIZER.finalize_stage_attempt(args, out_dir=tmp_path / "finalizer")


def test_finalizer_rechecks_actual_s4p_not_only_aggregate_flag(tmp_path):
    args, attempt = _finalizer_fixture(tmp_path)
    Path(attempt["golden_validation"]["s4p"]["path"]).write_text("changed")
    with pytest.raises(FINALIZER.StageAttemptFinalizationError, match="Golden validation failed"):
        FINALIZER.finalize_stage_attempt(args, out_dir=tmp_path / "finalizer")


def _completed_golden_fixture(tmp_path):
    from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden
    backend = tmp_path / "campaign/stages/000001_golden/backend"
    backend.mkdir(parents=True)
    args, attempt = _finalizer_fixture(backend)
    args.campaign_root = str(tmp_path / "campaign")
    finalizer_dir = backend / "finalizer"
    result = FINALIZER.finalize_stage_attempt(args, out_dir=finalizer_dir)
    finalizer_record = record(finalizer_dir / FINALIZER.ROLE_RECEIPT_NAME)
    manifest = json.loads(Path(args.backend_identity_manifest).read_text())
    snapshot = record(_json(backend / "synthetic_resource_snapshot.json", {"fixture_only": True}))
    context = record(_json(backend / "STAGE_CONTEXT.json", {
        "stage": "GOLDEN", "current_accepted": 0, "max_concurrency": 1,
        "backend_identity_manifest": attempt["backend_identity_manifest"],
        "full_campaign_authorization_receipt": attempt["full_campaign_authorization_receipt"],
        "prior_stage_receipt": None, "shell_used": False,
        "resource_snapshot": snapshot,
        "stage_execution_profile": manifest["runtime_identities"]["stage_execution_profile"],
    }))
    role_sources = {
        "phase_a_queue_builder": attempt["golden_source_receipt"],
        "cadence_streamout_runner": attempt["input_role_receipts"]["cadence"],
        "gds_physical_identity_auditor": attempt["input_role_receipts"]["gds"],
        "calibre_runner": attempt["input_role_receipts"]["calibre"],
        "calibre_zero_blocking_receipt_builder": attempt["input_role_receipts"]["calibre_zero"],
        "exact_audited_gds_emx_runner": attempt["input_role_receipts"]["exact_emx"],
        "full_band_s4p_qa_builder": attempt["input_role_receipts"]["exact56"],
        "stage_attempt_product_builder": record(Path(args.golden_attempt_products_receipt)),
        "stage_attempt_finalizer": finalizer_record,
    }
    full_order = STAGE_BACKEND.expected_stage_role_order("GOLDEN")
    prefix = list(full_order[:full_order.index("stage_attempt_finalizer") + 1])
    roles = []
    for name in prefix:
        role_record = role_sources.get(name)
        if role_record is None:
            role_record = record(_json(backend / (name + ".json"), {"overall_status": "PASS", "stage": "GOLDEN"}))
        # All receipts are synthetic fixture evidence, never a simulator execution claim.
        roles.append({"role": name, "return_code": 0, "shell_used": False,
                      "script_identity": manifest["script_identities"][name],
                      "receipt": role_record, "stdout": snapshot, "stderr": snapshot})
    trace = _json(backend / "STAGE_EXECUTION_TRACE.json", {
        "overall_status": "PASS", "decision": golden.FINALIZER_DECISION, "stage": "GOLDEN",
        "role_order": prefix, "expected_terminal_role_order": prefix, "roles": roles,
        "all_role_return_codes_zero": True, "all_role_receipts_pass": True, "shell_used": False,
    })
    resource = _json(backend / "RESOURCE_SUMMARY.json", {
        "overall_status": "PASS", "stage": "GOLDEN", "max_concurrency": 1, "resource_snapshot": snapshot,
    })
    receipt = STAGE_BACKEND._build_golden_validation_stage_receipt(
        finalizer_record, context_path=Path(context["path"]), trace_path=trace, resource_summary_path=resource,
        backend_sha256=attempt["backend_identity_manifest"]["sha256"],
        authorization_sha256=attempt["full_campaign_authorization_receipt"]["sha256"],
    )
    path = _json(backend.parent / "STAGE_RECEIPT.json", receipt)
    return path, receipt, args, result


def _validate_stage(path, receipt):
    return STAGE_BACKEND.validate_stage_receipt_chain([(path, receipt)],
        backend_manifest_sha256=receipt["backend_identity_manifest_sha256"],
        authorization_receipt_sha256=receipt["full_campaign_authorization_receipt_sha256"],
        verify_artifacts=True)


def test_golden_stage_chain_then_pilot_zero_base_and_anchor_exclusion(tmp_path):
    path, receipt, args, finalizer = _completed_golden_fixture(tmp_path)
    assert _validate_stage(path, receipt) == []
    assert STAGE_BACKEND.stage_for_progress(current_accepted=0, stage_receipts=[receipt]) == "PILOT_32"
    assert FINALIZER._prior_stage_cumulative_artifacts(Path(args.campaign_root), stage="PILOT_32",
        backend_sha256=receipt["backend_identity_manifest_sha256"],
        authorization_sha256=receipt["full_campaign_authorization_receipt_sha256"]) == {}
    checks = []
    excluded = PHASE_QUEUE._campaign_exclusion_paths(Path(args.campaign_root),
        stage="PILOT_32", current_accepted=0, requested_count=1, checks=checks)
    assert all(item["pass"] for item in checks), checks
    assert Path(receipt["artifacts"]["validation_geometry"]["path"]) in excluded
    assert STAGE_BACKEND._validate_stage_attempt_finalizer_receipt(finalizer,
        role_out_dir=path.parent / "backend/finalizer", backend_out_dir=path.parent / "backend",
        stage="GOLDEN", current_accepted=0, cumulative_target=1, progress_records=[],
        backend_manifest_sha256=receipt["backend_identity_manifest_sha256"],
        authorization_receipt_sha256=receipt["full_campaign_authorization_receipt_sha256"])[0] == finalizer["decision"]


@pytest.mark.parametrize("mutation", [None, "data", "simulation", "code", "contract", "authorization", "artifact", "source",
    "finalizer_unknown", "finalizer_known", "finalizer_no_binding", "finalizer_other_role",
    "scheduler_known", "scheduler_unknown", "scheduler_no_binding", "scheduler_wrong_role",
    "scheduler_bad_source", "scheduler_repeated", "scheduler_extra", "scheduler_drift", "scheduler_fake_boolean"])
def test_operational_golden_reuse_keeps_original_execution(tmp_path, monkeypatch, mutation):
    import copy
    from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden
    path, original, args, _ = _completed_golden_fixture(tmp_path)
    backend = json.loads(Path(args.backend_identity_manifest).read_text())
    target = copy.deepcopy(backend)
    target['operational_policy_version'] = 'test_only_pilot110'
    if mutation == 'code':
        role = next(iter(target['script_identities']))
        target['script_identities'][role] = record(_json(tmp_path/'changed_code.json', {'changed': True}))
    if mutation == 'contract':
        target['scientific_contract'] = {'frequency_points': 111}
    finalizer_rebind = None
    scheduler_rebind = None
    if mutation and mutation.startswith('finalizer_'):
        role = ('exact_audited_gds_emx_runner' if mutation == 'finalizer_other_role'
                else 'stage_attempt_finalizer')
        before = target['script_identities'][role]
        after = record(_json(tmp_path/'changed_finalizer.json', {'postprocess_fixture': True}))
        target['script_identities'][role] = after
        finalizer_rebind = dict(original=before, replacement=after, golden_execution_repeated=False)
        if mutation != 'finalizer_unknown':
            monkeypatch.setattr(golden, 'GOLDEN_COMPATIBLE_FINALIZER_REBINDS',
                                frozenset({(before['sha256'], after['sha256'])}))
    if mutation and mutation.startswith('scheduler_'):
        role = 'calibre_runner' if mutation == 'scheduler_wrong_role' else 'production_stage_backend'
        before = target['script_identities'][role]
        after = record(_json(tmp_path/'changed_scheduler.json', {'scheduler_fixture': True}))
        target['script_identities'][role] = after
        scheduler_rebind = {f'script_identities.{role}': dict(
            original=before, replacement=after, golden_execution_repeated=False)}
        if mutation != 'scheduler_unknown':
            monkeypatch.setattr(golden, 'GOLDEN_COMPATIBLE_SCHEDULER_REBINDS', frozenset({(
                'script_identities', 'production_stage_backend', before['sha256'], after['sha256'])}))
        item = scheduler_rebind[f'script_identities.{role}']
        if mutation == 'scheduler_bad_source': item['original'] = after
        if mutation == 'scheduler_repeated': item['golden_execution_repeated'] = True
        if mutation == 'scheduler_fake_boolean': item['golden_execution_repeated'] = 0
        if mutation == 'scheduler_extra': scheduler_rebind['runtime_identities.emx_process_file'] = {}
        if mutation == 'scheduler_drift': Path(after['path']).write_text('{}')
    target_pin = record(_json(tmp_path/'target_backend.json', target))
    authorization = dict(overall_status='PASS', authorization_scope='FULL_CAMPAIGN',
        backend_identity_manifest=target_pin, campaign_id=original['campaign_id'],
        contract_fingerprint_sha256=original['contract_fingerprint_sha256'], nn_training_authorized=False)
    if mutation == 'authorization':
        authorization['nn_training_authorized'] = True
    auth_pin = record(_json(tmp_path/'target_authorization.json', authorization))
    reused = copy.deepcopy(original)
    reused.update(backend_identity_manifest_sha256=target_pin['sha256'],
        full_campaign_authorization_receipt_sha256=auth_pin['sha256'],
        operational_progress_rebind=dict(kind='REUSE_COMPLETED_STAGE_UNCHANGED_SCIENTIFIC_CONTRACT',
            original_stage_receipt=record(path), target_backend_manifest=target_pin,
            target_authorization=auth_pin, new_simulator_execution=False, accepted_count_increment=0))
    if finalizer_rebind and mutation != 'finalizer_no_binding':
        reused['operational_progress_rebind']['postprocessing_only_finalizer_rebind'] = finalizer_rebind
    if scheduler_rebind and mutation != 'scheduler_no_binding':
        reused['operational_progress_rebind']['scheduling_only_role_rebinds'] = scheduler_rebind
    if mutation == 'data':
        reused['validation_feature_rows'] = 111
    elif mutation == 'simulation':
        reused['operational_progress_rebind']['new_simulator_execution'] = True
    elif mutation == 'artifact':
        reused['artifacts']['resource_summary'] = record(_json(tmp_path/'new_resource.json', {'overall_status': 'PASS'}))
    elif mutation == 'source':
        path.write_text('{}')
    if mutation in (None, 'finalizer_known', 'scheduler_known'):
        golden.validate_stage_evidence(reused)
        assert original == json.loads(path.read_text())
    else:
        with pytest.raises(golden.GoldenSourceError):
            golden.validate_stage_evidence(reused)


def test_golden_compatible_finalizer_binds_the_current_exact_fix():
    from rfic_transformer_inverse_design.campaigns import broadband56_golden_stage as golden
    current = record(Path(FINALIZER.__file__))
    assert golden.GOLDEN_COMPATIBLE_FINALIZER_REBINDS == frozenset({(
        '33bc608c24f85ec6024ddaa64b85a05492f774a9824592d6215d0cd1837b72d8', current['sha256'])})


@pytest.mark.parametrize("mutation", ["count", "missing_trace_role", "profile_mode", "calibre_bytes", "stage"])
def test_golden_stage_rejects_count_scope_trace_or_artifact_drift(tmp_path, mutation):
    path, receipt, args, _ = _completed_golden_fixture(tmp_path)
    if mutation == "count":
        receipt["accepted_unique_geometries"] = 1
    elif mutation == "stage":
        receipt["stage"] = "PILOT_32"
    elif mutation == "missing_trace_role":
        trace_path = Path(receipt["artifacts"]["stage_execution_trace"]["path"])
        trace = json.loads(trace_path.read_text())
        trace["roles"].pop(4)
        _json(trace_path, trace)
        receipt["artifacts"]["stage_execution_trace"] = record(trace_path)
    elif mutation == "profile_mode":
        profile_path = path.parent / "backend/synthetic_profile.json"
        _json(profile_path, {"stages": {"GOLDEN": {}}})
    else:
        Path(receipt["golden_validation"]["source_calibre_zero_blocking_receipt"]["path"]).write_text("changed")
    assert _validate_stage(path, receipt)


def test_pilot_initial_queue_cannot_skip_first_production_checkpoint(tmp_path):
    path, _, args, _ = _completed_golden_fixture(tmp_path)
    checks = []
    PHASE_QUEUE._campaign_exclusion_paths(Path(args.campaign_root),
        stage="PILOT_32", current_accepted=0, requested_count=32, checks=checks)
    assert any(not item["pass"] and item["name"] == "requested_count_matches_next_frozen_checkpoint" for item in checks)


def _pilot_args(tmp_path, golden_args, *, count, offset):
    args = FINALIZER_FIXTURE._args(tmp_path, accepted=count, raw=count, stage="GOLDEN")
    args.stage = "PILOT_32"
    args.campaign_root = golden_args.campaign_root
    args.backend_identity_manifest = golden_args.backend_identity_manifest
    args.full_campaign_receipt = golden_args.full_campaign_receipt
    for field in FINALIZER.CSV_ARTIFACT_FIELDS:
        path = Path(getattr(args, field))
        rows = _read_rows(path)
        for row in rows:
            for key in ("geometry_sha256", "candidate_id_sha256"):
                if key in row:
                    row[key] = f"{int(row[key], 16) + offset:064x}"
            if "attempt_id" in row:
                row["attempt_id"] = f"pilot_{offset}_{row['attempt_id']}"
        if rows:
            _write_rows(path, rows)
    return args


def test_historical_golden_then_one_and_32_new_production_geometries(tmp_path):
    _, golden, golden_args, _ = _completed_golden_fixture(tmp_path)
    root = Path(golden_args.campaign_root)
    first = _pilot_args(tmp_path / "first_input", golden_args, count=1, offset=0)
    first_stage = root / "stages/000002_pilot_32"
    first_stage.mkdir()
    result = FINALIZER.finalize_stage_attempt(first, out_dir=first_stage / "finalizer")
    assert result["accepted_before"] == 0
    assert result["accepted_after"] == 1
    assert result["decision"] == "CONTINUE_SAMPLING"
    progress_source = Path(result["progress_receipt"]["path"])
    progress_path = first_stage / "STAGE_PROGRESS_RECEIPT.json"
    progress_path.write_bytes(progress_source.read_bytes())
    progress = json.loads(progress_path.read_text())
    cumulative = progress["round_cumulative_inputs"]
    assert len(_read_rows(Path(cumulative["accepted_geometry_increment"]["path"]))) == 1
    assert len(_read_rows(Path(cumulative["long_features"]["path"]))) == 56
    checks = []
    PHASE_QUEUE._campaign_exclusion_paths(root, stage="PILOT_32",
        current_accepted=1, requested_count=31, checks=checks)
    assert all(item["pass"] for item in checks), checks

    second = _pilot_args(tmp_path / "second_input", golden_args, count=31, offset=1)
    result = FINALIZER.finalize_stage_attempt(second, out_dir=root / "stages/000003_pilot_32")
    assert result["accepted_before"] == 1
    assert result["accepted_after"] == 32
    assert result["decision"] == "STAGE_TARGET_REACHED"
    cumulative = result["cumulative_stage_inputs"]
    accepted = _read_rows(Path(cumulative["accepted_geometry_increment"]["path"]))
    assert len(accepted) == len({row["geometry_sha256"] for row in accepted}) == 32
    assert golden["golden_validation"]["geometry_sha256"] not in {row["geometry_sha256"] for row in accepted}
    assert len(_read_rows(Path(cumulative["long_features"]["path"]))) == 1792


def test_finalizer_cannot_skip_first_production_checkpoint(tmp_path):
    _, _, golden_args, _ = _completed_golden_fixture(tmp_path)
    args = _pilot_args(tmp_path / "input", golden_args, count=2, offset=0)
    with pytest.raises(FINALIZER.StageAttemptFinalizationError, match="overshoots.*checkpoint"):
        FINALIZER.finalize_stage_attempt(args, out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("tampered", [False, True])
def test_initial_pilot_materializer_never_fabricates_checkpoint_zero(tmp_path, monkeypatch, tampered):
    _, golden, args, _ = _completed_golden_fixture(tmp_path)
    # Only deployment identities are test doubles; real stage/artifact validation remains enabled.
    monkeypatch.setattr(CHECKPOINT, "validate_backend_identity_manifest", lambda *a, **k: [])
    monkeypatch.setattr(CHECKPOINT, "_validate_authorization", lambda *a, **k: None)
    monkeypatch.setattr(CHECKPOINT, "_validate_self_identity", lambda *a, **k: None)
    fixture_input = _json(tmp_path / "synthetic_contract_input.json", {"fixture_only": True})
    checkpoint_args = SimpleNamespace(**vars(args), current_accepted=0,
        contract=str(fixture_input), production_config=str(fixture_input), geometry_bounds=str(fixture_input))
    checkpoint_args.stage = "PILOT_32"
    out = tmp_path / "materializer"
    out.mkdir()
    if tampered:
        Path(golden["golden_validation"]["s4p"]["path"]).write_text("tampered")
        with pytest.raises(CHECKPOINT.AdaptiveCheckpointError, match="stage receipt chain failed"):
            CHECKPOINT.materialize_checkpoint(checkpoint_args, out_dir=out)
    else:
        result = CHECKPOINT.materialize_checkpoint(checkpoint_args, out_dir=out)
        assert result["decision"] == "FIRST_PRODUCTION_CHECKPOINT_PENDING"
        assert result["round_accepted_target"] == result["raw_selection_count"] == 1
        assert result["checkpoint_accepted"] is result["checkpoint_receipt"] is None
        assert result["subprocesses"] == []
        assert result["simulator_action_taken"] is False
    assert not (out / "checkpoint").exists()
