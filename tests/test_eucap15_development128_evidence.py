"""Synthetic native-shaped128 evidence; never model/solver/real-pilot reads.

Reuse the immutable old fixture only. Immutable manifest/QA constants are
replaced with synthetic fixture SHA identities, never the validator or IO.
The unchanged pinned synthetic fixture is included in this repository.
"""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_selected_evidence as evidence

FIXTURE = Path(__file__).parent / "fixtures/eucap15_selected_evidence_fixture.py"
assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == "9d884386c1dd42edf091e7e1642c61f34d9e02de4e61620520f5565e3a8bad5b"
spec = importlib.util.spec_from_file_location("_development128_synthetic_native_fixture", FIXTURE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def make_chain(root, monkeypatch, **kwargs):
    c = fixture.SyntheticEvidence(root, **kwargs)
    old_model = c.model_id
    c.model_id = evidence.DEVELOPMENT_MODEL
    c.tau = [.125, .125, 1., .04000000000000001]
    protocol = dict(q_values=list(range(10, 21)), q_scalar="min(Qp,Qs)",
                    score_scale=c.scale, absolute_tolerances=c.tau)

    def convert(value):
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        if value == old_model:
            return c.model_id
        if value == "FORMAL_10K":
            return evidence.DEVELOPMENT_SCOPE
        if value == "DEVELOPMENT_UNIFORM_TRIPLE":
            return "DEVELOPMENT_CURRENT_SNAPSHOT_UNIFORM_TRIPLE"
        return value

    for key in list(c.json_order):
        c.put(key, c.paths[key], convert(c.read(key)))
    c.records = convert(c.records)
    c.blob("records", c.paths["records"], b"".join(
        (fixture.json.dumps(r, sort_keys=True) + "\n").encode() for r in c.records))
    c.put("pair", c.root/"SYNTHETIC_PAIR.json", {"synthetic": True, "model_id": c.model_id})
    c.put("qa", c.root/"SYNTHETIC_CANDIDATE_QA.json", {"synthetic": True, "status": "GO"})
    c.put("reference", c.paths["reference"], dict(
        schema="eucap15_current_development_model_identity.v1", model_id=c.model_id,
        experiment_class=evidence.DEVELOPMENT_SCOPE, model_role="DEVELOPMENT_BASELINE_NOT_FINAL",
        source_rows=6329, gradient_train_rows=3801, validation_rows=1269, test_rows_count_only=1259,
        FINAL=False, REAL_EMX_VALIDATION="NOT_RUN", pair=c.p("pair"),
        selection_basis="PREDECLARED_FIRST_COMPLETED_3X256_SEED17_NOT_ABLATION_WINNER"))
    c.put("freeze", c.paths["freeze"], dict(
        schema="eucap15_current_development_pilot_freeze.v1",
        status="FROZEN_BEFORE_ANY_PREDICTION_AND_NATIVE", model_id=c.model_id, frequency_ghz=15,
        config={"dataset_scope": evidence.DEVELOPMENT_SCOPE, "allow_extrapolation": True},
        N_original_requests=128, N_proxy_slots=1408, score_scale=c.scale, absolute_tolerances=c.tau,
        tie_break="EXACT_TIE_SMALLER_Q", selection="ALL_ELEVEN_FINITE_SCORES_THEN_MINIMUM_NO_ANALYTICAL_RERANK",
        model_identity=c.p("reference")))
    c.item["source_records"] = c.p("records")
    c.item["source_record_canonical_sha256"] = fixture.canonical(c.records[c.q-10])
    c.put("pilot", c.paths["pilot"], dict(
        schema=evidence.DEVELOPMENT_SCHEMA, status="NOT_DISPATCHED_REQUIRES_OWNER_DEVELOPMENT_SCOPE_ADAPTER",
        model_id=c.model_id, dataset_scope=evidence.DEVELOPMENT_SCOPE, model_role="DEVELOPMENT_BASELINE_NOT_FINAL",
        frequency_ghz=15, label_mode="STRICT_LUMPED", N_original_requests=128, N_proxy_slots=1408,
        N_q_proxy_selected=128, N_selected_analytic_pass=93, N_native_started=0,
        no_failure_replacement=True, production_campaign_membership=False, FINAL=False, REAL_EMX_VALIDATION="NOT_RUN",
        model_identity=c.p("reference"), freeze=c.p("freeze"),
        requests=[c.item]+[{"request_id":f"SYNTHETIC-request-{i:06d}", "candidate_id":f"SYNTHETIC-other-{i}"} for i in range(1,128)]))
    c.repin_after("reference")
    binding = dict(schema="eucap15_development128_physical_binding.v1",
        dataset_scope=evidence.DEVELOPMENT_SCOPE, model_role="DEVELOPMENT_BASELINE_NOT_FINAL", model_id=c.model_id,
        model_identity=c.p("reference"), original_model_identity=c.p("reference"), model_pair=c.p("pair"),
        selected_manifest=c.p("pilot"), original_selected_manifest=c.p("pilot"), independent_candidate_qa=c.p("qa"),
        original_request_denominator=128, request_id=c.request_id, q_proxy=c.q,
        selected_candidate_id=c.candidate_id, candidate_geometry_identity_sha256=c.geometry_sha,
        no_failure_replacement=True, production_campaign_membership=False, FINAL=False)
    for key in ("audit", "request", "proof", "feature"):
        c.edit(key, lambda d: d.update(development_binding=deepcopy(binding)))
    c.edit("audit", lambda d: d.update(original_request_denominator=128))
    c.edit("proof", lambda d: d.update(original_request_denominator=128,
        protocol=protocol, executed_hit_tolerances=c.tau))
    c.edit("feature", lambda d: d.update(original_request_denominator=128, absolute_hit_tolerances=c.tau))
    c.ctx.manifest_pin = c.p("pilot"); c.ctx.manifest = c.read("pilot")
    c.ctx.freeze_pin = c.p("freeze"); c.ctx.freeze = c.read("freeze")
    c.ctx.reference_pin = c.p("reference")
    c.ctx.reference_receipt_pin = c.p("pair")
    c.ctx.original_manifest_pin = c.p("pilot")
    c.ctx.development_binding = deepcopy(binding)
    c.ctx.comparison_protocol = protocol
    c.item = deepcopy(c.ctx.manifest["requests"][0])
    monkeypatch.setattr(evidence, "DEVELOPMENT_MANIFEST_SHA", c.p("pilot")["sha256"])
    monkeypatch.setattr(evidence, "DEVELOPMENT_QA_SHA", c.p("qa")["sha256"])
    monkeypatch.setattr(evidence, "DEVELOPMENT_QA_BYTES", c.p("qa")["bytes"])
    return c


@pytest.mark.parametrize("below_half_srf", [True, False])
def test_development128_strict_and_srf_invalid_are_readonly(tmp_path, monkeypatch, below_half_srf):
    c = make_chain(tmp_path/"native", monkeypatch, below_half_srf=below_half_srf)
    before = {k: p.read_bytes() for k,p in c.paths.items()}
    result = c.inspect()
    assert result["status"] == ("STRICT_VALID" if below_half_srf else "EMX_INVALID")
    assert result["strict_joint_hit"] is below_half_srf
    assert result["actual"] == [1.02,1.48,13.2,.52]
    assert "q_emx" not in result
    assert before == {k:p.read_bytes() for k,p in c.paths.items()}


@pytest.mark.parametrize("key", ["audit", "request", "proof", "feature"])
@pytest.mark.parametrize("field,value", [("q_proxy",14),("model_id","wrong"),("original_request_denominator",93)])
def test_each_stage_exact_binding_drift_rejected(tmp_path, monkeypatch, key, field, value):
    c = make_chain(tmp_path/"native", monkeypatch)
    c.edit(key, lambda d: d["development_binding"].update({field:value}))
    with pytest.raises(evidence.SelectedEvidenceError):
        c.inspect()


@pytest.mark.parametrize("field,value", [("dataset_scope","FORMAL_10K"),("N_original_requests",93),("N_proxy_slots",704)])
def test_wrong_scope_or_original_denominator_rejected(tmp_path, monkeypatch, field, value):
    c = make_chain(tmp_path/"native", monkeypatch)
    c.edit("pilot", lambda d: d.update({field:value}))
    c.ctx.manifest = c.read("pilot"); c.ctx.manifest_pin = c.p("pilot")
    monkeypatch.setattr(evidence, "DEVELOPMENT_MANIFEST_SHA", c.p("pilot")["sha256"])
    with pytest.raises(evidence.SelectedEvidenceError):
        c.inspect()


@pytest.mark.parametrize("field,value", [("q_proxy",14),("selected_candidate_id","other"),("FINAL",True)])
def test_context_binding_is_per_current_item_not_first_request(tmp_path, monkeypatch, field, value):
    c = make_chain(tmp_path/"native", monkeypatch)
    c.ctx.development_binding[field] = value
    with pytest.raises(evidence.SelectedEvidenceError, match="binding differs"):
        c.inspect()


def test_exact128_manifest_and_qa_identity_required(tmp_path, monkeypatch):
    c = make_chain(tmp_path/"native", monkeypatch)
    monkeypatch.setattr(evidence, "DEVELOPMENT_MANIFEST_SHA", "0"*64)
    with pytest.raises(evidence.SelectedEvidenceError, match="manifest identity"):
        c.inspect()
    monkeypatch.setattr(evidence, "DEVELOPMENT_MANIFEST_SHA", c.p("pilot")["sha256"])
    monkeypatch.setattr(evidence, "DEVELOPMENT_QA_SHA", "0"*64)
    with pytest.raises(evidence.SelectedEvidenceError, match="candidate QA"):
        c.inspect()


@pytest.mark.parametrize("below_half_srf", [True, False])
def test_full_development_mirror_keeps_original_pins(tmp_path, monkeypatch, below_half_srf):
    c = make_chain(tmp_path/"native", monkeypatch, below_half_srf=below_half_srf)
    entry = c.entry(); expected = c.inspect()
    destination = tmp_path/"mirror"; destination.mkdir()
    mapping = {}
    for i,p in enumerate(c.paths.values()):
        target = destination/f"{i}.opaque"; target.write_bytes(p.read_bytes())
        mapping[str(p)] = str(target)
    c.root.rename(tmp_path/"unavailable_synthetic_original")
    result = evidence.inspect_features(entry,c.item,c.ctx,physical_path_map=mapping)
    assert {k:v for k,v in result.items() if k != "resolution_evidence"} == {k:v for k,v in expected.items() if k != "resolution_evidence"}
    assert all(p["resolved"]["path"] == mapping[p["original"]["path"]] for p in result["resolution_evidence"])


def test_protocol_and_missing_bridge_rejected(tmp_path, monkeypatch):
    c = make_chain(tmp_path/"native", monkeypatch)
    c.ctx.comparison_protocol = {**c.ctx.comparison_protocol,"extra":"not-owner-protocol"}
    with pytest.raises(evidence.SelectedEvidenceError,match="protocol"):
        c.inspect()
    del c.ctx.development_binding
    with pytest.raises(evidence.SelectedEvidenceError,match="bridge incomplete"):
        c.inspect()
