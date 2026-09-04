from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.broadband56_emx_runtime import (
    EmxRuntimeIdentityError, launch_spec, load_identity,
)


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/broadband56_emx_runtime.py"
ENTRY = ROOT / "scripts/run_broadband56_exact_audited_gds_emx.py"


@pytest.fixture(scope="module")
def runtime_snapshot():
    # Discover dependency roots from the running test interpreter, never PATH.
    import numpy
    dependency = Path(numpy.__file__).resolve().parent.parent
    source = '''import importlib.util, json, sys
from pathlib import Path
bootstrap, root, dependency, launcher, entry = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("_b56_emx_runtime", bootstrap)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
identity = module.describe_loaded_runtime(
    launcher=launcher, entrypoint=entry, runtime_root=root,
    dependency_roots=[dependency], environment={"PATH": "/usr/bin:/bin"})
print(json.dumps(identity))
'''
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", source, str(BOOTSTRAP),
         str(ROOT), str(dependency), sys.executable, str(ENTRY)],
        env={"PATH": "/usr/bin:/bin"}, shell=False, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _identity_file(tmp_path, snapshot):
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(snapshot))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(snapshot, tmp_path):
    args = ["--import-preflight-only", "--out-dir", str(tmp_path / "must_not_exist")]
    for name in ("config", "gds", "manifest", "calibre-receipt", "full-campaign-receipt"):
        args += ["--" + name, str(tmp_path / name), "--expected-" + name + "-sha256", "1" * 64]
    for name in ("candidate-id-sha256", "geometry-identity-sha256"):
        args += ["--" + name, "2" * 64]
    args += ["--expected-runner-sha256", snapshot["entrypoint"]["sha256"],
             "--expected-module-sha256", snapshot["modules"][
                 "rfic_transformer_inverse_design.campaigns.broadband56_exact_gds_emx"]["sha256"]]
    return args


def _child(tmp_path, snapshot, *, remove_isolation=False):
    path, digest = _identity_file(tmp_path, snapshot)
    specification = launch_spec(path, digest, _arguments(snapshot, tmp_path))
    if remove_isolation:
        specification["args"].remove("-I")
    return subprocess.run(**specification, text=True, capture_output=True)


def test_real_child_loads_campaigns_without_ambient_package(tmp_path, runtime_snapshot, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "obsolete_installation"))
    result = _child(tmp_path, runtime_snapshot)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["campaigns_module_import"] == "PASS"
    assert receipt["emx_entrypoint_import"] == "PASS"
    assert receipt["module_root_identity"] == "PASS"
    assert receipt["simulator_action_taken"] is False
    assert not (tmp_path / "must_not_exist").exists()


def test_production_launch_spec_pins_interpreter_and_environment(tmp_path, runtime_snapshot, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/unapproved")
    path, digest = _identity_file(tmp_path, runtime_snapshot)
    spec = launch_spec(path, digest, ["--import-preflight-only"])
    assert spec["args"][:5] == [runtime_snapshot["python_launcher"]["path"], "-I", "-B", "-S", str(ENTRY)]
    assert spec["shell"] is False
    assert spec["env"] == {"PATH": "/usr/bin:/bin"}
    assert spec["cwd"] == str(ROOT)


def test_nonisolated_child_rejected_before_imports(tmp_path, runtime_snapshot):
    result = _child(tmp_path, runtime_snapshot, remove_isolation=True)
    assert result.returncode == 2
    assert "isolated -I -B -S" in result.stderr


@pytest.mark.parametrize("field,value", [
    ("path", "/usr/bin/python"),
    ("realpath", "/unapproved/python"),
    ("version", [0, 0, 0]),
])
def test_other_python_identity_rejected(tmp_path, runtime_snapshot, field, value):
    snapshot = json.loads(json.dumps(runtime_snapshot))
    snapshot["python_runtime"][field] = value
    try:
        result = _child(tmp_path, snapshot)
    except EmxRuntimeIdentityError:
        return
    assert result.returncode == 2
    assert "Python" in result.stderr


@pytest.mark.parametrize("mutation", ["outside_root", "source_hash", "missing_binding"])
def test_old_or_unbound_module_rejected(tmp_path, runtime_snapshot, mutation):
    snapshot = json.loads(json.dumps(runtime_snapshot))
    name = "rfic_transformer_inverse_design.campaigns"
    if mutation == "outside_root":
        snapshot["modules"][name]["expected_root"] = str(tmp_path)
    elif mutation == "source_hash":
        snapshot["modules"][name]["sha256"] = "f" * 64
    else:
        del snapshot["modules"][name]
    try:
        result = _child(tmp_path, snapshot)
    except EmxRuntimeIdentityError:
        return
    assert result.returncode == 2, result.stdout
    assert not (tmp_path / "must_not_exist").exists()


def test_manifest_hash_and_ambient_environment_rejected(tmp_path, runtime_snapshot):
    path, digest = _identity_file(tmp_path, runtime_snapshot)
    with pytest.raises(EmxRuntimeIdentityError, match="SHA-256"):
        load_identity(path, "0" * 64)
    snapshot = json.loads(json.dumps(runtime_snapshot))
    snapshot["environment"]["PYTHONPATH"] = "/old/install"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(EmxRuntimeIdentityError, match="ambient"):
        load_identity(path, hashlib.sha256(path.read_bytes()).hexdigest())
