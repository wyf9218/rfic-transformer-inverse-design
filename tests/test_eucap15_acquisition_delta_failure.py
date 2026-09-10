"""Synthetic saved-audit fixtures only; not native GDS/EMX validation."""
import copy
import hashlib
import json
import socket
import subprocess

import pytest

from research.broadband56_nn import eucap15_acquisition_delta_failure as subject
from research.broadband56_nn.eucap15_selected_evidence import SelectedEvidenceError


def encoded(value):
    return value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, allow_nan=False).encode()


def pin(path, value):
    raw = encoded(value)
    return dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


class FakeReader:
    """In-memory exact-pin reader, deliberately not a physical parser or filesystem."""
    def __init__(self, values):
        self.values = copy.deepcopy(values)
        self.evidence = {}

    def read(self, expected):
        if expected["path"] not in self.values:
            raise SelectedEvidenceError("Missing exact synthetic mirror")
        value = self.values[expected["path"]]
        if pin(expected["path"], value) != expected:
            raise SelectedEvidenceError("Synthetic SHA/size mismatch")
        self.evidence[expected["path"]] = copy.deepcopy(expected)
        return encoded(value)

    def document(self, expected):
        return json.loads(self.read(expected))


class Fixture:
    def __init__(self, monkeypatch, failed=("geometry_range_pass",), doe=False):
        self.base = "/synthetic/acquisition/request-001"
        self.values = {}
        self.proposal = dict(candidate_id="synthetic-candidate", request_id="request-001", q_proxy=None if doe else 11,
            canonical_geometry_sha256="a"*64, arm="DIRECTED", arm_order=1, global_order=1,
            source="EXPLORATION" if doe else "SPARSE_TARGETED", analytic_pass=True, local_dispatch_eligible=True,
            target=None if doe else [1., 1., 11., .5], proxy=None if doe else [1.01, 1.02, 11.1, .51])
        self.batch = {key: self.add("/synthetic/shared/"+key, ("fixture-"+key).encode())
                      for key in ("manifest", "recipe", "proposals")}
        self.batch["rows"] = {self.proposal["candidate_id"]: copy.deepcopy(self.proposal)}
        self.config = self.add("/synthetic/shared/private_config", b"synthetic config only")
        monkeypatch.setattr(subject, "CONFIG_SHA", self.config["sha256"])
        self.gds = self.add(self.base+"/cadence/layout.gds", b"not-a-physical-GDS; pin closure fixture only")
        self.port = self.add(self.base+"/cadence/ports.json", {"synthetic": True})
        cid_sha = hashlib.sha256(self.proposal["candidate_id"].encode()).hexdigest()
        checks = {name: name not in failed for name in subject.GEOMETRY_CHECKS}
        checks.update({name: False for name in failed})
        self.geometry = dict(overall_status="FAIL", candidate_id_sha256=cid_sha,
            candidate_geometry_identity_sha256=self.proposal["canonical_geometry_sha256"],
            gds_path=self.gds["path"], gds_sha256=self.gds["sha256"], checks=checks,
            original_artifacts=[self.gds, self.port])
        self.record = dict(candidate_id=self.proposal["candidate_id"], candidate_id_sha256=cid_sha,
            candidate_geometry_identity_sha256=self.proposal["canonical_geometry_sha256"],
            analytic_grid=True, status="FAIL", audit_attempted=True, cadence_routed=True, calibre_eligible=False,
            failed_checks=list(failed), original_record=copy.deepcopy(self.proposal), gds=self.gds, port_manifest=self.port)
        context = dict(request_id="request-001", frequency_ghz=15, q_proxy=self.proposal["q_proxy"],
            model_id="ACQUISITION_RECIPE_BOUND_NOT_MODEL_INFERRED_HERE", dataset_scope="DEVELOPMENT_ACQUISITION_PAIR",
            target_source=self.proposal["source"], arm="DIRECTED", arm_order=1, global_order=1)
        self.audit = dict(schema="eucap15_acquisition_gds_audit.v1", request=context, N_logical=1, N_audit_attempted=1,
            source_pins={**{k:self.batch[k] for k in ("manifest", "recipe", "proposals")}, "private_config": self.config},
            records=[self.record])
        self.terminal = dict(status="CANDIDATE_FAILURE_RETAINED_NO_SUBSTITUTION", request_id="request-001",
            candidate_id=self.proposal["candidate_id"], q_proxy=self.proposal["q_proxy"], error="ACTUAL_GDS_AUDIT_REJECTED")
        self.item = dict(candidate_id=self.proposal["candidate_id"], request_id="request-001", q_proxy=self.proposal["q_proxy"],
            evidence_status="FAILURE_EVIDENCE_BOUND", original_status=self.terminal["status"],
            original_error=self.terminal["error"], failure_stage="ACTUAL_GDS_AUDIT", failed_checks=list(failed))

    def add(self, path, value):
        self.values[path] = value
        return pin(path, value)

    def prepared(self):
        self.record["geometry_audit"] = self.add(self.base+"/gds_audit/GEOMETRY_AUDIT.json", self.geometry)
        self.add(self.base+"/gds_audit/REQUEST_GDS_AUDIT.json", self.audit)
        self.item["original_result"] = self.add(self.base+"/RESULT.json", self.terminal)
        return FakeReader(self.values), {path:pin(path,value) for path,value in self.values.items()}

    def run(self):
        reader, index = self.prepared()
        return subject.failure_row(reader, self.item, self.proposal, self.batch, index)


@pytest.fixture(autouse=True)
def prohibit_execution(monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("No process or network in synthetic audit tests")
    monkeypatch.setattr(socket, "socket", prohibited)
    monkeypatch.setattr(subprocess, "Popen", prohibited)


@pytest.mark.parametrize("failed", [("geometry_range_pass",), ("angle_45_135_pass",),
                                     ("topology_pass", "new_native_guard")])
def test_dynamic_real_false_check_names_and_order(monkeypatch, failed):
    f = Fixture(monkeypatch, failed)
    f.record["failed_checks"].reverse()
    row = f.run()
    assert row["failed_checks"] == list(failed) and row["state"] == "GDS_FAIL"


@pytest.mark.parametrize("doe", [False, True])
def test_failure_nulls_and_original_selection_are_preserved(monkeypatch, doe):
    f = Fixture(monkeypatch, doe=doe)
    before = copy.deepcopy(f.proposal)
    row = f.run()
    for key in ("actual", "strict_valid", "core_eligible", "descriptor_valid", "physics_qa_pass",
                "strict_joint_hit", "emx_minus_target", "emx_minus_proxy", "feature", "s4p"):
        assert row[key] is None
    assert row["target"] == f.proposal["target"] and row["q_proxy"] == f.proposal["q_proxy"]
    assert row["physical_numbers_available"] is False and row["target_errors_defined"] is False
    assert f.proposal == before


def test_success_normalizer_preserves_existing_chain_mapping(monkeypatch):
    f = Fixture(monkeypatch)
    f.prepared()
    item = dict(candidate_id=f.proposal["candidate_id"], request_id="request-001", q_proxy=11,
        evidence_status="CANDIDATE_PHYSICAL_CHAIN_BOUND", original_status="FRESH_EMX_EXTRACTED",
        geometry_sha256="a"*64, production_accepted=False, strict_valid_source_flag=False,
        core15_eligible_source_flag=False, original_result=f.item["original_result"], feature={"opaque": True})
    result = subject.normalized_entry(item, f.proposal)
    assert result["feature"] == item["feature"] and result["result"] == item["original_result"]
    assert result["strict_valid"] is False and result["core_eligible"] is False and result["arm"] == "DIRECTED"


@pytest.mark.parametrize("field,value", [("failure_stage", "DRC"), ("failure_stage", "SOLVER_FAIL"),
    ("original_error", "unknown"), ("evidence_status", "PENDING"), ("q_proxy", 12)])
def test_unknown_or_other_stage_never_reclassified(monkeypatch, field, value):
    f = Fixture(monkeypatch); f.item[field] = value
    with pytest.raises(SelectedEvidenceError): f.run()


def test_missing_mandatory_geometry_check_rejected(monkeypatch):
    f = Fixture(monkeypatch); del f.geometry["checks"]["topology_pass"]
    with pytest.raises(SelectedEvidenceError, match="Mandatory"): f.run()


@pytest.mark.parametrize("value", [0, "false", None])
def test_non_boolean_check_rejected_even_if_not_declared_failed(monkeypatch, value):
    f = Fixture(monkeypatch); f.geometry["checks"]["topology_pass"] = value
    with pytest.raises(SelectedEvidenceError, match="booleans"): f.run()


@pytest.mark.parametrize("kind", ["empty", "duplicate", "no_actual_false", "different_record"])
def test_failed_check_sets_cannot_be_invented(monkeypatch, kind):
    f = Fixture(monkeypatch)
    if kind == "empty": f.item["failed_checks"] = []
    elif kind == "duplicate": f.item["failed_checks"] *= 2
    elif kind == "no_actual_false": f.geometry["checks"]["geometry_range_pass"] = True
    else: f.record["failed_checks"] = ["topology_pass"]
    with pytest.raises(SelectedEvidenceError): f.run()


@pytest.mark.parametrize("kind", ["terminal_candidate", "record_geometry", "request_model", "actual_gds"])
def test_rehashed_identity_conflicts_rejected(monkeypatch, kind):
    f = Fixture(monkeypatch)
    if kind == "terminal_candidate": f.terminal["candidate_id"] = "foreign"
    elif kind == "record_geometry": f.record["candidate_geometry_identity_sha256"] = "b"*64
    elif kind == "request_model": f.audit["request"]["model_id"] = "foreign"
    else: f.geometry["gds_sha256"] = "b"*64
    with pytest.raises(SelectedEvidenceError): f.run()


@pytest.mark.parametrize("kind", ["omit_gds", "omit_port", "duplicate", "other_port", "manifest", "config"])
def test_original_artifact_and_frozen_source_closure(monkeypatch, kind):
    f = Fixture(monkeypatch)
    if kind == "omit_gds": f.geometry["original_artifacts"] = [f.port]
    elif kind == "omit_port": f.geometry["original_artifacts"] = [f.gds]
    elif kind == "duplicate": f.geometry["original_artifacts"].append(f.port)
    elif kind == "other_port": f.record["port_manifest"] = f.add(f.base+"/cadence/other_ports.json", {"foreign": True})
    elif kind == "manifest": f.audit["source_pins"]["manifest"] = f.add("/synthetic/other_manifest", b"foreign")
    else: f.audit["source_pins"]["private_config"] = f.add("/synthetic/other_config", b"foreign")
    with pytest.raises(SelectedEvidenceError): f.run()


@pytest.mark.parametrize("kind", ["missing", "sha", "index"])
def test_consumed_mirror_or_export_pin_mismatch(monkeypatch, kind):
    f = Fixture(monkeypatch); reader, index = f.prepared()
    if kind == "missing": del reader.values[f.gds["path"]]
    elif kind == "sha": reader.values[f.port["path"]] = b"mutated"
    else: index[f.gds["path"]] = dict(f.gds, sha256="b"*64)
    with pytest.raises(SelectedEvidenceError): subject.failure_row(reader, f.item, f.proposal, f.batch, index)


def test_positive_terminal_output_contradiction_rejected(monkeypatch):
    f = Fixture(monkeypatch); f.terminal["feature"] = {"path": "claimed output"}
    with pytest.raises(SelectedEvidenceError, match="contradictory"): f.run()


def test_proposal_must_be_exact_frozen_row(monkeypatch):
    f = Fixture(monkeypatch); f.proposal["target"][0] = 1.2
    with pytest.raises(SelectedEvidenceError, match="frozen batch"): f.run()


def native_derived_fixture(monkeypatch, doe=False):
    f = Fixture(monkeypatch, doe=doe)
    f.proposal["geometry"] = [float(i) for i in range(10)]
    f.batch["rows"][f.proposal["candidate_id"]] = copy.deepcopy(f.proposal)
    f.record["original_record"] = dict(copy.deepcopy(f.proposal), grid_geometry=list(f.proposal["geometry"]),
        grid_proxy=copy.deepcopy(f.proposal["proxy"]), analytic_grid=f.proposal["analytic_pass"],
        candidate_id_sha256=hashlib.sha256(f.proposal["candidate_id"].encode()).hexdigest(),
        candidate_geometry_identity_sha256=f.proposal["canonical_geometry_sha256"])
    return f


@pytest.mark.parametrize("doe", [False, True])
def test_native_derived_record_exact_five_fields_pass(monkeypatch, doe):
    f = native_derived_fixture(monkeypatch, doe=doe)
    row = f.run()
    assert row["state"] == "GDS_FAIL" and row["actual"] is None
    assert row["q_proxy"] == f.proposal["q_proxy"] and row["target"] == f.proposal["target"]


@pytest.mark.parametrize("key,value", [("grid_geometry", [99.]*10), ("grid_proxy", [2.]*4),
    ("analytic_grid", 1), ("candidate_id_sha256", "b"*64), ("candidate_geometry_identity_sha256", "b"*64)])
def test_native_derived_record_each_derived_value_must_match(monkeypatch, key, value):
    f = native_derived_fixture(monkeypatch)
    f.record["original_record"][key] = value
    with pytest.raises(SelectedEvidenceError, match="Derived failure"): f.run()


@pytest.mark.parametrize("kind", ["shared_drift", "unknown_extra", "partial_derived"])
def test_native_derived_record_no_shared_drift_or_unproven_extra(monkeypatch, kind):
    f = native_derived_fixture(monkeypatch)
    original = f.record["original_record"]
    if kind == "shared_drift": original["q_proxy"] = 12
    elif kind == "unknown_extra": original["unproven_native_annotation"] = "ignored?"
    else: del original["grid_proxy"]
    with pytest.raises(SelectedEvidenceError): f.run()
