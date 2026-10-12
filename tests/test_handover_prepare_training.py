"""Portable relocation checks only: no scientific experiment is executed."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/handover/prepare_training.py"
spec = importlib.util.spec_from_file_location("prepare_training_under_test", SCRIPT)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    assets = tmp_path / "private assets"
    assets.mkdir()
    dataset = tmp_path / "original.csv"
    dataset.write_text("id,value\n0,1\n", encoding="utf-8")
    data_sha = h.digest(dataset)
    contract = {"schema": "fixture", "mother": {"path": "/old/private/data.csv", "sha256": data_sha, "rows": 1},
                "indices": {"path": "/old/indices", "sha256": "unchanged"},
                "qualification": {"path": "/old/q", "sha256": "unchanged"},
                "original_summary": {"path": "/old/summary", "sha256": "unchanged"},
                "splitter": {"path": "/old/splitter", "sha256": "unchanged"},
                "scientific_setting": {"keep": [1, 2, 3]}}
    for name in h.PINS:
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contract) if name == h.CONTRACT else "# frozen fixture\n", encoding="utf-8")
    pins = {name: h.digest(assets / name) for name in h.PINS}
    monkeypatch.setattr(h, "PINS", pins)
    monkeypatch.setattr(h, "MOTHER_SHA", data_sha)
    return assets, dataset, tmp_path / "new output", contract


def test_prepare_relocates_only_and_does_not_start(inputs):
    assets, dataset, out, original = inputs
    before = {p: p.read_bytes() for p in assets.rglob("*") if p.is_file()}
    result = h.prepare(assets, dataset, out, 2)
    assert result["status"] == "PREPARED_NOT_TRAINED"
    current = h.read(out / "RELOCATED_CONTRACT.json")
    for key in ("mother", "indices", "qualification", "original_summary", "splitter"):
        current[key]["path"] = original[key]["path"]
    assert current == original
    assert before == {p: p.read_bytes() for p in before}
    work, _, env = h.validate_prepared(out)
    assert not (work / h.ADAPTER).parent.joinpath("runs").exists()
    assert env["OPENBLAS_NUM_THREADS"] == "2"
    assert env["EUCAP_DATASIZE_TRAINER"] == str(work / h.TRAINER)


def test_existing_output_refused(inputs):
    assets, dataset, out, _ = inputs
    h.prepare(assets, dataset, out, 2)
    with pytest.raises(FileExistsError):
        h.prepare(assets, dataset, out, 2)


def test_source_drift_rejected_before_output(inputs):
    assets, dataset, out, _ = inputs
    (assets / h.TRAINER).write_text("changed")
    with pytest.raises(ValueError, match="identity mismatch"):
        h.prepare(assets, dataset, out, 2)
    assert not out.exists()


def test_dataset_drift_rejected(inputs):
    assets, dataset, out, _ = inputs
    dataset.write_text("not original")
    with pytest.raises(ValueError, match="identity mismatch"):
        h.prepare(assets, dataset, out, 2)


def test_prepared_sources_still_pinned(inputs):
    assets, dataset, out, _ = inputs
    h.prepare(assets, dataset, out, 2)
    (out / "frozen" / h.ADAPTER).write_text("changed")
    with pytest.raises(ValueError, match="identity mismatch"):
        h.validate_prepared(out)


def test_rehashing_changed_recipe_does_not_allow_it(inputs):
    assets, dataset, out, _ = inputs
    h.prepare(assets, dataset, out, 2)
    path = out / "RELOCATED_CONTRACT.json"
    contract = h.read(path)
    contract["scientific_setting"]["keep"] = [9]
    path.write_text(json.dumps(contract))
    identity_path = out / "PREPARATION.json"
    identity = h.read(identity_path)
    identity["contract_sha256"] = h.digest(path)
    identity_path.write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="Only path relocation"):
        h.validate_prepared(out)


def test_output_cannot_modify_private_repository(inputs):
    assets, dataset, _, _ = inputs
    with pytest.raises(ValueError, match="outside"):
        h.prepare(assets, dataset, assets / "new run", 2)


def test_execute_uses_argv_not_shell_and_preserves_explicit_arm(inputs, monkeypatch):
    assets, dataset, out, _ = inputs
    h.prepare(assets, dataset, out, 2)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(h.subprocess, "run", fake_run)
    assert h.execute(out, "D50k", "resume") == 0
    argv, kwargs = calls[0]
    assert argv[-2:] == ["--arm", "D50k"]
    assert "resume" in argv
    assert "shell" not in kwargs
    assert kwargs["env"]["OMP_NUM_THREADS"] == "2"


def test_environment_restored_after_exception():
    original = dict(os.environ)
    with pytest.raises(RuntimeError):
        with h.local_environment({"ONLY_TEST": "1"}):
            assert os.environ["ONLY_TEST"] == "1"
            raise RuntimeError("fixture")
    assert dict(os.environ) == original


def test_run_requires_explicit_arm(tmp_path):
    with pytest.raises(SystemExit) as exc:
        h.main(["run", "--out", str(tmp_path)])
    assert exc.value.code == 2
