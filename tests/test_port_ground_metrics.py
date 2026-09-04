import importlib.util
import json
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tests.port_ground_fixture import make_fixture
from rfic_transformer_inverse_design.layout.port_ground_metrics import (
    COUNT, ERROR, EVIDENCE, FRAME, NOT_EVALUATED, aggregate_checks,
    attach_actual_gds_metrics, measure_port_ground_metrics, validate_metric_evidence,
)


@pytest.fixture(autouse=True)
def no_process_launch(monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("no subprocess or simulator permitted in geometry tests")
    monkeypatch.setattr(subprocess, "Popen", prohibited)
    for name in ("system", "fork", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name): monkeypatch.setattr(os, name, prohibited)


def produce(tmp_path, mutate=None):
    gds = tmp_path / "synthetic.gds"
    source, foundry = make_fixture(gds, mutate)
    check = {"metrics": {}}
    result = attach_actual_gds_metrics(check, gds_path=gds, power_line_audit=source, foundry_audit=foundry)
    return check, result


def test_valid_actual_eight_and_derived_aggregate(tmp_path):
    check, result = produce(tmp_path)
    assert result["metrics"] == {COUNT: 8, ERROR: 0.0}
    assert result["metrics"] == aggregate_checks(result[EVIDENCE])
    assert all(r["overlap_area_um2"] > 0 for r in result[EVIDENCE])
    assert result["via_stack_check"]["overall_status"] == "PASS"
    assert validate_metric_evidence(check, gds_sha256=result["gds_sha256"])["power_line_check"] == "PASS"


@pytest.mark.parametrize("port", ["P001", "P002", "P003", "P004", "P005", "P006", "P007", "P008"])
def test_every_missing_overlap_is_observed_not_a_constant(tmp_path, port):
    check, result = produce(tmp_path, lambda cell, objects, _: cell.remove(objects[port]))
    assert result["metrics"][COUNT] < 8
    assert result["power_line_check"] == "FAIL"
    assert validate_metric_evidence(check, gds_sha256=result["gds_sha256"])["power_line_check"] == "FAIL"


def test_one_grid_step_is_not_rounded_to_zero(tmp_path):
    def mutate(cell, objects, _): objects["P001"].translate(.005, 0)
    _, result = produce(tmp_path, mutate)
    assert result["metrics"][COUNT] == 7
    assert result["metrics"][ERROR] == pytest.approx(.005)
    assert result["via_stack_check"]["overall_status"] == NOT_EVALUATED


def test_wrong_signal_metal_fails(tmp_path):
    _, result = produce(tmp_path, lambda cell, objects, _: setattr(objects["P001"], "layer", 39))
    assert result["metrics"][COUNT] == 7


def test_wrong_ground_object_fails(tmp_path):
    def mutate(cell, objects, source):
        for label in cell.labels:
            if label.text == "P001_G": label.text = "UNRELATED_GROUND"
    _, result = produce(tmp_path, mutate)
    assert result["metrics"][COUNT] == 7


def test_wrong_frame_fails(tmp_path):
    gds = tmp_path / "synthetic.gds"
    source, foundry = make_fixture(gds)
    result = measure_port_ground_metrics(gds_path=gds, power_line_audit=source, foundry_audit=foundry, coordinate_frame="PIXEL_XY")
    assert result["metrics"][COUNT] == 0


@pytest.mark.parametrize("key", [COUNT, ERROR])
def test_missing_aggregate_fails_closed_and_vias_not_evaluated(tmp_path, key):
    check, result = produce(tmp_path)
    del check["metrics"][key]
    states = validate_metric_evidence(check, gds_sha256=result["gds_sha256"])
    assert states["power_line_check"] == "FAIL"
    assert states["via_stack_check"] == NOT_EVALUATED


def test_aggregate_evidence_inconsistency_fails(tmp_path):
    check, result = produce(tmp_path)
    check["metrics"][COUNT] = 7
    assert validate_metric_evidence(check, gds_sha256=result["gds_sha256"])["power_line_check"] == "FAIL"


def test_fake_zero_cannot_hide_observed_grid_step(tmp_path):
    check, result = produce(tmp_path)
    result[EVIDENCE][0]["observed_geometry"]["terminal_edge_um"] += .005
    assert validate_metric_evidence(check, gds_sha256=result["gds_sha256"])["power_line_check"] == "FAIL"


def test_wrong_ground_layer_fails(tmp_path):
    def mutate(cell, objects, source):
        for poly in cell.polygons:
            if poly.layer == 35: poly.layer = 34
    _, result = produce(tmp_path, mutate)
    assert result["metrics"][COUNT] == 0


def test_metric_contract_binds_the_actual_sources():
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / "docs/research/POWER_LINE_8PORT_GEOMETRY_METRIC_CONTRACT.json").read_text())
    for item in contract["implementation_bindings"]:
        assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("invalid", [False, True])
def test_post_streamout_hook_preserves_input_and_rejects_clobber(tmp_path, invalid):
    directory = tmp_path / "evaluation"
    (directory / "layout").mkdir(parents=True)
    gds = directory / "synthetic.gds"
    def mutate(cell, objects, source): objects["P001"].translate(.005, 0)
    source, foundry = make_fixture(gds, mutate if invalid else None)
    summary = {"geometry_check": {"metrics": {}, "power_line_8port_geometry_audit": source}}
    path = directory / "summary.json"
    path.write_text(json.dumps(summary))
    before, gds_before = path.read_bytes(), gds.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts/run_broadband56_v2_cadence_streamout_batch.py"
    spec = importlib.util.spec_from_file_location("metric_hook_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if invalid:
        with pytest.raises(module.CadenceBatchError, match="geometry metric contract FAIL"):
            module._produce_post_streamout_metrics(directory, gds, foundry)
    else:
        module._produce_post_streamout_metrics(directory, gds, foundry)
    assert (directory / "summary.pre_port_ground_metrics.json").read_bytes() == before
    assert gds.read_bytes() == gds_before
    enriched = path.read_bytes()
    with pytest.raises(FileExistsError):
        module._produce_post_streamout_metrics(directory, gds, foundry)
    assert path.read_bytes() == enriched


def test_via_independent_after_overlap_pass(tmp_path):
    def mutate(cell, objects, source):
        cell.remove(next(p for p in cell.polygons if p.layer == 55))
    check, result = produce(tmp_path, mutate)
    assert result["metrics"] == {COUNT: 8, ERROR: 0.0}
    assert result["power_line_check"] == "PASS"
    assert result["via_stack_check"]["overall_status"] == "FAIL"
    assert result["via_stack_check"]["via_missing_area_um2"] > 0
    assert validate_metric_evidence(check, gds_sha256=result["gds_sha256"])["via_stack_check"] == "FAIL"


@pytest.mark.parametrize("invalid", [False, True])
def test_real_calibre_consumer_accepts_only_valid_producer(tmp_path, invalid):
    from tests.test_run_broadband56_v2_calibre_batch import _fixture, _sha
    fixture = _fixture(tmp_path)
    import csv
    row = next(csv.DictReader(fixture["input_index"].open()))
    source_path, physical_path = fixture["source_audits"][0], fixture["physical_audits"][0]
    source = json.loads(source_path.read_text())
    if invalid:
        evaluation = Path(source["evaluation_summary_path"])
        payload = json.loads(evaluation.read_text())
        del payload["geometry_check"]["metrics"][COUNT]
        evaluation.write_text(json.dumps(payload))
        source["evaluation_summary_sha256"] = _sha(evaluation)
    script = Path(__file__).resolve().parents[1] / "scripts/run_broadband56_v2_calibre_batch.py"
    spec = importlib.util.spec_from_file_location("metric_consumer_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    kwargs = dict(row=row, source_audit=source, source_audit_path=source_path,
                  physical_audit=json.loads(physical_path.read_text()), physical_audit_path=physical_path,
                  process_path=tmp_path/"TSMC65_05_12_26/process.proc")
    if invalid:
        with pytest.raises(module.CalibreBatchError, match=NOT_EVALUATED):
            module._current_contract_delegate_geometry_audit(**kwargs)
    else:
        receipt = module._current_contract_delegate_geometry_audit(**kwargs)
        assert receipt["overall_status"] == "PASS"
        assert receipt["simulator_action_taken"] is False
