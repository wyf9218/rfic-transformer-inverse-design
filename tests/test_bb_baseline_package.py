"""Synthetic-only immutable historical packaging acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from research.broadband56_nn.baseline_package import build_package, load_package, sha256
from research.broadband56_nn.baseline import replay_baseline, VALIDATION_NAME
from tests.test_bb_baseline import fixture_baseline


def package_fixture(tmp_path):
    run, contract_path = fixture_baseline(tmp_path)
    contract = json.loads(contract_path.read_text())
    contract["architecture"].update(hidden_activation="gelu", geometry_projection="sigmoid_to_training_envelope")
    contract_path.write_text(json.dumps(contract))
    replay = tmp_path/"replay"
    replay_baseline(run, replay, contract_path=contract_path, expected_validation_sha256=sha256(run/VALIDATION_NAME))
    receipt = replay/"REPLAY_RECEIPT.json"
    result = build_package(receipt, tmp_path/"package", expected_replay_sha256=sha256(receipt))
    return run, receipt, result


def test_package_relocated_fresh_process_without_source_tree(tmp_path):
    run, receipt, result = package_fixture(tmp_path)
    package = Path(result["package"])
    model = load_package(package, expected_manifest_sha256=result["manifest_sha256"])
    x = [[1, 1, 10, .2]]
    expected = model.predict(x).geometry.tolist()
    relocated = tmp_path/"relocated"
    shutil.copytree(package, relocated)
    # Only synthetic originals are made unavailable; research sources untouched.
    run.rename(tmp_path/"unavailable_synthetic")
    environment = dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-I", "-B", "-c", "import importlib.util,sys,json; p=sys.argv[1]; s=importlib.util.spec_from_file_location('standalone',p+'/load_baseline.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); b=m.load_package(p,expected_manifest_sha256=sys.argv[2]); print(json.dumps(b.predict([[1,1,10,.2]]).geometry.tolist()))", str(relocated), result["manifest_sha256"]]
    response = subprocess.run(command, env=environment, cwd=tmp_path, capture_output=True, text=True, check=True)
    np.testing.assert_allclose(json.loads(response.stdout), expected, atol=0, rtol=0)
    assert not list(relocated.rglob("__pycache__"))
    assert model.config["boundary"]["native_full56_s_supported"] is False


def test_native_request_boundary_and_no_clobber(tmp_path):
    _, receipt, result = package_fixture(tmp_path)
    model = load_package(result["package"], expected_manifest_sha256=result["manifest_sha256"])
    with pytest.raises(ValueError, match="fixed 15"):
        model.predict([[1,1,10,.2]], frequency_ghz=20)
    with pytest.raises(ValueError, match="native historical"):
        model.predict(np.ones((1,56,4)))
    with pytest.raises(FileExistsError):
        build_package(receipt, result["package"], expected_replay_sha256=sha256(receipt))


def test_source_and_portable_tamper_fail_closed(tmp_path):
    _, receipt, result = package_fixture(tmp_path)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_package(result["package"], expected_manifest_sha256="a"*64)
    (Path(result["package"])/"normalizer.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_package(result["package"], expected_manifest_sha256=result["manifest_sha256"])
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_package(receipt, tmp_path/"failed", expected_replay_sha256="b"*64)
    assert json.loads((tmp_path/"failed/PACKAGE_BUILD_FAILED.json").read_text())["status"] == "FAIL"
