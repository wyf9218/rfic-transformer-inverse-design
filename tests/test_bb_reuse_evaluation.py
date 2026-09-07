"""Read-only reuse checks against synthetic evidence, never research inference."""
from pathlib import Path

import pytest

from research.broadband56_nn import seven_evaluation as seven
from research.broadband56_nn.io import read_json, save_json, sha256
from tests.test_bb_seven_evaluation import prepared


def _verifier():
    from research.broadband56_nn.reuse_evaluation import verify_existing_evaluation
    return verify_existing_evaluation


def _rewrite_synthetic(path, value):
    """Mutate disposable test evidence only, never any real artifact."""
    import json
    path.write_text(json.dumps(value, indent=2) + "\n")


def _reseal_synthetic(root):
    from research.broadband56_nn.io import manifest_tree
    import json
    files = manifest_tree(root, exclude=("ARTIFACT_MANIFEST.json", "SHA256SUMS.txt"))
    (root / "ARTIFACT_MANIFEST.json").write_text(json.dumps({"files": files}) + "\n")
    (root / "SHA256SUMS.txt").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in
                                               manifest_tree(root, exclude=("SHA256SUMS.txt",))))


@pytest.fixture
def common_evidence(prepared, tmp_path):
    data, registry, _, _ = prepared
    vp, tp = tmp_path / "val_plan.json", tmp_path / "test_plan.json"
    seven.freeze_common_15ghz_targets(data, vp)
    seven.freeze_common_15ghz_targets(data, tp, split="test")
    out = tmp_path / "common_val"
    seven.evaluate_seven(data, registry, vp, out, device="cpu", micro_batch=2)
    return data, registry, vp, tp, out


def test_existing_common_reuse_is_read_only_no_model_or_bundle(common_evidence, monkeypatch):
    data, registry, vp, _, out = common_evidence
    before = {str(p): sha256(p) for p in out.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError("read-only verifier attempted inference or array loading")
    monkeypatch.setattr(seven, "Bundle", forbidden)
    monkeypatch.setattr(seven, "_load_component", forbidden)
    result = _verifier()(out, data / "data_manifest.json", registry, "validation", "common15", vp)
    assert result["status"] == "PASS" and result["reuse_allowed"] is True
    assert result["model_inference_performed"] is False and result["files_modified"] is False
    assert before == {str(p): sha256(p) for p in out.rglob("*") if p.is_file()}


def test_self_consistent_old_weights_cannot_be_reused(common_evidence):
    data, registry, vp, _, out = common_evidence
    path = out / "EVALUATION_SUMMARY.json"
    summary = read_json(path)
    summary["checkpoints"]["BB05"] = "0" * 64
    _rewrite_synthetic(path, summary)
    _reseal_synthetic(out)
    with pytest.raises(ValueError, match="checkpoint"):
        _verifier()(out, data / "data_manifest.json", registry, "validation", "common15", vp)


def test_extra_file_and_tampered_artifact_rejected(common_evidence):
    data, registry, vp, _, out = common_evidence
    (out / "unexpected.txt").write_text("unmanifested")
    with pytest.raises(ValueError, match="manifest.*set|extra|missing"):
        _verifier()(out, data / "data_manifest.json", registry, "validation", "common15", vp)


def test_wrong_active_target_or_split_rejected(common_evidence, tmp_path):
    data, registry, vp, tp, out = common_evidence
    with pytest.raises(ValueError, match="target"):
        _verifier()(out, data / "data_manifest.json", registry, "validation", "common15", tp)
    with pytest.raises(ValueError, match="split"):
        _verifier()(out, data / "data_manifest.json", registry, "test", "common15", vp)


def test_existing_sealed_test_exact_gate(common_evidence, tmp_path):
    data, registry, vp, tp, val = common_evidence
    freeze = tmp_path / "freeze.json"
    seven.freeze_seven_configuration(registry, data, val / "EVALUATION_SUMMARY.json", tp, freeze)
    out = tmp_path / "common_test"
    seven.evaluate_seven(data, registry, tp, out, split="test", configuration_freeze=freeze, device="cpu", micro_batch=2)
    result = _verifier()(out, data / "data_manifest.json", registry, "test", "common15", tp)
    assert result["status"] == "PASS"
    changed = read_json(freeze)
    changed["checkpoints"]["BB00"] = "0" * 64
    _rewrite_synthetic(freeze, changed)
    with pytest.raises(ValueError, match="freeze|configuration"):
        _verifier()(out, data / "data_manifest.json", registry, "test", "common15", tp)


def _broadband_evidence(data, registry, out):
    out.mkdir()
    identity = seven._data_identity(data)
    records = seven._registry(registry, identity)
    selected = {name: value for name, value in records.items() if not name.startswith("BB00")}
    protocol = seven.broadband.evaluation_protocol_identity()
    panels = out / "fixed_specs"
    panels.mkdir()
    panel = {"schema": "bb_fixed_evaluation_specs.v1", "seed": seven.broadband.PANEL_SEED,
             "split": "validation", "data_sha": identity["data_sha"], "normalizer_sha": identity["normalizer_sha"],
             "data_manifest_sha": identity["data_manifest_sha256"], "evaluation_protocol": protocol,
             "panels": [{"name": task.lower() + "_" + mode} for task, mode in seven.broadband.PANEL_DEFINITIONS]}
    save_json(panels / "PANEL_MANIFEST.json", panel)
    panel_pin = seven._pin(panels / "PANEL_MANIFEST.json")
    request = {"split": "validation", "data_sha": identity["data_sha"], "normalizer_sha": identity["normalizer_sha"],
               "source_runs": selected, "evaluation_protocol": protocol, "panel_manifest": panel_pin,
               "data_manifest": seven._pin(data / "data_manifest.json"),
               "configuration_freeze": {"status": "VALIDATION_ONLY_NO_TEST_RELEASE", "test_access": False}}
    save_json(out / "EVALUATION_REQUEST.json", request)
    reports, forwards = {}, {}
    for name, record in selected.items():
        folder = out / "shared_forward" / name if name.startswith("F") else out / name
        folder.mkdir(parents=True)
        report = {"checkpoint": record["checkpoint"]} if name.startswith("F") else {
            "selected_checkpoint": record["checkpoint"], "panel_manifest": panel_pin,
            "own_forward": selected[seven.PACKAGE_MAPPING[name][0]]["checkpoint"],
            "common_forward": selected["FREF"]["checkpoint"]}
        save_json(folder / "metrics.json", report)
        (forwards if name.startswith("F") else reports)[name] = {"metrics": seven._pin(folder / "metrics.json")}
    summary = {"schema": "bb_evaluation_summary.v1", "status": "COMPLETE_PROXY_EVALUATION", "split": "validation",
               "data_sha": identity["data_sha"], "normalizer_sha": identity["normalizer_sha"],
               "evaluation_protocol": protocol, "panel_manifest": panel_pin,
               "configuration_freeze": request["configuration_freeze"], "forward_models": forwards, "packages": reports,
               "test_used_to_select_model": False, "real_emx_validation": "NOT_RUN", "physical_winner": "NOT_ESTABLISHED"}
    save_json(out / "EVALUATION_SUMMARY.json", summary)
    seven._write_index(out)


def test_broadband_reuse_matches_active_ten_not_just_manifest(prepared, tmp_path):
    data, registry, _, _ = prepared
    out = tmp_path / "broadband"
    _broadband_evidence(data, registry, out)
    result = _verifier()(out, data / "data_manifest.json", registry, "validation", "broadband")
    assert result["checkpoint_count"] == 10 and result["status"] == "PASS"
    request = read_json(out / "EVALUATION_REQUEST.json")
    request["source_runs"]["FREF"]["checkpoint"]["sha256"] = "0" * 64
    _rewrite_synthetic(out / "EVALUATION_REQUEST.json", request)
    _reseal_synthetic(out)
    with pytest.raises(ValueError, match="checkpoint|source"):
        _verifier()(out, data / "data_manifest.json", registry, "validation", "broadband")
