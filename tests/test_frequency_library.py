"""Synthetic metadata only; no real weights, inference, EMX or existing tests."""
import hashlib
import json
from pathlib import Path

import pytest

from research.broadband56_nn import frequency_library as library


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")
    return path


def pin(path):
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root, pkg = tmp_path / "rollup", tmp_path / "package"
    index = pkg / "MODEL_INDEX.json"
    receipt = pkg / "PACKAGE_RECEIPT.json"
    registry = root / "MODEL_REGISTRY.json"
    pair_pin = {"path": str(tmp_path / "NOT_READ_PAIR.json"), "sha256": "a" * 64, "bytes": 123}
    model = {"model_id": "synthetic-f05", "frequency_ghz": 5, "label_mode": "STRICT_LUMPED",
             "training_status": "PROVISIONAL_PARTIAL", "convergence_established": False,
             "formal_10k": True, "source_snapshot_geometries": 10000}
    idx = {"schema": "frequency_model_index.v1", "models": {model["model_id"]: model},
           "frequencies": [{"frequency_ghz": 5, "label_modes": {
               "STRICT_LUMPED": {"model_id": model["model_id"], "model_available": True,
                                 "model_status": "PROVISIONAL_PARTIAL"},
               "POINTWISE_DESCRIPTOR_EXPERIMENTAL": {"model_id": None, "model_available": False,
                                                      "model_status": "NOT_TRAINED"}}}]}
    pack = {"schema": "frequency_package_receipt.v1", "status": "PORTABLE_INFERENCE_PACKAGE_READY",
            "model_status": "PROVISIONAL_PARTIAL", "inputs": {"pair": pair_pin}}
    reg = {"frequency_is_input": False, "models": [{"frequency_ghz": 5,
           "nearest_frequency_fallback": False, "status": "PROVISIONAL_PARTIAL", "pair": pair_pin}]}
    def seal():
        write(index, idx)
        pack["model_index"] = {"path": index.name, "sha256": pin(index)["sha256"]}
        write(receipt, pack)
        reg["models"][0].update(package_receipt=pin(receipt), model_index=pin(index))
        seal_registry()
    def seal_registry():
        write(registry, reg)
        write(root / "ROLLUP_RECEIPT.json", {"status": "PASS_SAVED_ARTIFACT_RECONCILIATION",
                                            "artifacts": [pin(registry)]})
    seal()
    def forbidden(*a, **k):
        pytest.fail("metadata resolve must not load model weights")
    monkeypatch.setattr(library.package, "load_index", forbidden)
    return {"root": root, "pkg": pkg, "index": index, "receipt": receipt, "registry": registry,
            "reg": reg, "pack": pack, "idx": idx, "model": model, "seal": seal,
            "seal_registry": seal_registry}


def resolve(fixture, **kwargs):
    return library.resolve(fixture["registry"], frequency_ghz=kwargs.pop("frequency_ghz", 5), **kwargs)


def test_metadata_resolve_never_loads_weights(fixture):
    result = resolve(fixture)
    assert result["model_id"] == "synthetic-f05"
    assert result["model_status"] == "PROVISIONAL_PARTIAL"
    assert result["weights_verified"] is False and result["models_loaded"] == 0
    assert result["validation_source"] == "SELF_PROXY" and result["REAL_EMX_VALIDATION"] == "NOT_RUN"
    assert result["source"]["model_index"] == pin(fixture["index"])


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("bytes", 0)])
def test_bad_registry_pin(fixture, field, value):
    path = fixture["root"] / "ROLLUP_RECEIPT.json"
    rec = json.loads(path.read_text()); rec["artifacts"][0][field] = value; write(path, rec)
    with pytest.raises(ValueError, match="registry SHA/bytes"):
        resolve(fixture)


@pytest.mark.parametrize("bad", ["duplicate", "float", "boolean", "outside", "input", "nearest"])
def test_registry_global_contract(fixture, bad):
    reg = fixture["reg"]
    if bad == "duplicate":
        reg["models"].append(dict(reg["models"][0]))
    elif bad == "input":
        reg["frequency_is_input"] = True
    elif bad == "nearest":
        reg["models"][0]["nearest_frequency_fallback"] = True
    else:
        reg["models"][0]["frequency_ghz"] = {"float": 5.0, "boolean": True, "outside": 21}[bad]
    fixture["seal_registry"]()
    with pytest.raises(ValueError, match="registry requires"):
        resolve(fixture)


@pytest.mark.parametrize("frequency", [6, 4, 21, 5.0, True, "5"])
def test_no_nearest_or_frequency_coercion(fixture, frequency):
    with pytest.raises(ValueError):
        resolve(fixture, frequency_ghz=frequency)


@pytest.mark.parametrize("mode", ["UNKNOWN", "POINTWISE_DESCRIPTOR_EXPERIMENTAL"])
def test_exact_mode_no_fallback(fixture, mode):
    with pytest.raises(ValueError):
        resolve(fixture, label_mode=mode)


@pytest.mark.parametrize("name,field", [("package_receipt", "sha256"), ("package_receipt", "bytes"),
                                      ("model_index", "sha256"), ("model_index", "bytes")])
def test_package_and_index_pins(fixture, name, field):
    fixture["reg"]["models"][0][name][field] = "0" * 64 if field == "sha256" else 0
    fixture["seal_registry"]()
    with pytest.raises(ValueError, match="metadata SHA/bytes"):
        resolve(fixture)


@pytest.mark.parametrize("bad", ["model_id", "frequency_ghz", "label_mode", "status", "index_duplicate"])
def test_route_identity_is_not_architecture_inference(fixture, bad):
    if bad == "index_duplicate":
        fixture["idx"]["frequencies"].append(fixture["idx"]["frequencies"][0])
    elif bad == "status":
        fixture["model"]["training_status"] = "COMPLETE"
    else:
        fixture["model"][bad] = {"model_id": "other-model", "frequency_ghz": 10,
                                 "label_mode": "POINTWISE_DESCRIPTOR_EXPERIMENTAL"}[bad]
    fixture["seal"]()
    with pytest.raises(ValueError, match="route"):
        resolve(fixture)


@pytest.mark.parametrize("bad", ["index_path", "index_sha", "pair", "failed", "terminal", "rollup_terminal"])
def test_receipt_bindings_and_terminality(fixture, bad):
    if bad == "rollup_terminal":
        path = fixture["root"] / "ROLLUP_RECEIPT.json"
        rec = json.loads(path.read_text()); rec["status"] = "RUNNING"; write(path, rec)
    elif bad == "failed":
        write(fixture["pkg"] / "PACKAGE_FAILED.json", {"status": "FAIL"})
    else:
        if bad.startswith("index_"):
            fixture["pack"]["model_index"]["path" if bad == "index_path" else "sha256"] = (
                "other.json" if bad == "index_path" else "0" * 64)
        elif bad == "pair":
            fixture["pack"]["inputs"] = {"pair": {**fixture["reg"]["models"][0]["pair"], "sha256": "b" * 64}}
        else:
            fixture["pack"]["status"] = "RUNNING"
        write(fixture["receipt"], fixture["pack"])
        fixture["reg"]["models"][0]["package_receipt"] = pin(fixture["receipt"])
        fixture["seal_registry"]()
    with pytest.raises(ValueError):
        resolve(fixture)


@pytest.mark.parametrize("extrapolate", [False, True])
def test_infer_delegates_targets_and_explicit_extrapolation(fixture, monkeypatch, extrapolate):
    targets = [[-1.25, 9.125, 99.0, 1.02]]  # Not clipped or rejected by this thin wrapper.
    observed = []
    native = {"geometry": [[1, 2]], "validation_source": "SELF_PROXY", "REAL_EMX_VALIDATION": "NOT_RUN"}
    def stub(index, supplied, **kwargs):
        observed.append((index, supplied, kwargs))
        return native
    monkeypatch.setattr(library.package, "infer_index", stub)
    result = library.infer(fixture["registry"], targets, frequency_ghz=5, allow_extrapolation=extrapolate)
    assert observed == [(str(fixture["index"]), targets,
                         {"frequency_ghz": 5, "label_mode": "STRICT_LUMPED", "allow_extrapolation": extrapolate})]
    assert observed[0][1] is targets and result["result"] is native
    assert result["route"]["model_status"] == "PROVISIONAL_PARTIAL"
    assert result["REAL_EMX_VALIDATION"] == "NOT_RUN"


def test_default_no_extrapolation_and_native_failure_preserved(fixture, monkeypatch):
    def stub(index, targets, **kwargs):
        assert kwargs["allow_extrapolation"] is False
        raise ValueError("outside train support; upstream did not clip")
    monkeypatch.setattr(library.package, "infer_index", stub)
    with pytest.raises(ValueError, match="outside train support"):
        library.infer(fixture["registry"], [0, 0, 0, 0], frequency_ghz=5)
    with pytest.raises(ValueError, match="explicit boolean"):
        library.infer(fixture["registry"], [0, 0, 0, 0], frequency_ghz=5, allow_extrapolation="yes")


def test_cli_resolve_and_infer(fixture, monkeypatch, capsys):
    common = ["--registry", str(fixture["registry"]), "--frequency-ghz", "5", "--label-mode", "STRICT_LUMPED"]
    library.main(["resolve", *common])
    assert json.loads(capsys.readouterr().out)["model_id"] == "synthetic-f05"
    def stub(index, targets, **kwargs):
        assert targets == [1, 2, 3, 0.4] and kwargs["allow_extrapolation"] is True
        return {"targets": targets}
    monkeypatch.setattr(library.package, "infer_index", stub)
    library.main(["infer", *common, "--targets", "[1,2,3,0.4]", "--allow-extrapolation"])
    assert json.loads(capsys.readouterr().out)["validation_source"] == "SELF_PROXY"
