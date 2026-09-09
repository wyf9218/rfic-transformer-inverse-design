"""Small metadata/routing unit tests, NOT model/numerical validation.

The established evaluator is deliberately stubbed at its public call boundary;
no prior training, inference, 42-case or 138-case suite is rerun by these tests.
Every input, checkpoint placeholder and delegated result is SYNTHETIC.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.broadband56_nn import eucap15_development_evaluation as development
from research.broadband56_nn.io import canonical_sha, save_json


@pytest.fixture
def frame(tmp_path):
    data = tmp_path / "synthetic_data"
    data.mkdir()
    (data / "dataset.npz").write_bytes(b"SYNTHETIC METADATA PLACEHOLDER; NO NPZ OR MODEL LOADED\n")
    save_json(data / "splits.json", {"synthetic": True})
    data_sha = development.pin(data / "dataset.npz")["sha256"]
    save_json(data / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS",
        "artifacts": {"dataset.npz": {"sha256": data_sha}}, "evidence": "SYNTHETIC_TEST_ONLY"})
    norm = {"synthetic": True, "g_mean": [0.5, 0.5]}
    contract = {"field_names": ["a", "b"], "lower": [0, 0], "upper": [1, 1]}
    exposure = {"train": {"split_geometries": 6, "eligible_geometries": 6},
                "validation": {"split_geometries": 2, "eligible_geometries": 2},
                "test": {"split_geometries": 0, "eligible_geometries": 0}}
    roles = {}
    for name, role, hidden, seed in (("common", "forward", [256] * 3, 17),
                                    ("own", "forward", [128] * 3, 29),
                                    ("inverse", "inverse", [128] * 3, 29)):
        directory = tmp_path / name
        directory.mkdir()
        for suffix in ("best", "last"):
            (directory / f"{suffix}.pt").write_bytes(f"SYNTHETIC {name} {suffix}, NOT A MODEL\n".encode())
        best, last = (development.pin(directory / f"{s}.pt") for s in ("best", "last"))
        architecture = {"widths": [2, *hidden, 4] if role == "forward" else [4, *hidden, 2]}
        config = {"role": role, "architecture": architecture, "seed": seed,
            "normalizer_sha": canonical_sha(norm), "contract_sha": canonical_sha(contract),
            "data_sha": data_sha, "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
            "resume_probe": False, "research_comparison_eligible": True, "test_labels_optimized": False,
            "response_spans": [2.5, 2.5, 20.0, 0.8],
            "forward_checkpoint": str(tmp_path / "common/best.pt") if role == "inverse" else None}
        for file, value in (("config.json", config), ("normalizer.json", norm),
                            ("contract.json", contract), ("history.json", [{"step": 3}])):
            save_json(directory / file, value)
        receipt = {"schema": "bb00_training_receipt.v1", "role": role, "kind": "BB00",
            "status": "SMOKE_TRAINED", "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
            "data_sha": data_sha, "validation_selected_checkpoint": True,
            "resume_probe": False, "research_comparison_eligible": True, "historical_weights_loaded": False,
            "completed_step": 3, "normalizer_sha": canonical_sha(norm),
            "best_checkpoint": best["path"], "best_sha256": best["sha256"],
            "last_checkpoint": last["path"], "last_sha256": last["sha256"],
            "eligible_rows": exposure, "unique_gradient_geometries": 6, "gradient_draws": 96,
            "model_sha": name + "_SYNTHETIC_MODEL_DIGEST_NOT_EVALUATED"}
        path = directory / "TRAINING_RECEIPT.json"
        save_json(path, receipt)
        roles[name] = {"pin": development.pin(path), "best": best, "last": last, "config": config}
    return {"root": tmp_path, "data": data, "roles": roles, "exposure": exposure,
            "manifest": development.pin(data / "data_manifest.json")}


def invoke(frame, out, *, shared=False, **kwargs):
    roles = frame["roles"]
    return development.evaluate_development(frame["data"],
        roles["own" if shared else "common"]["pin"], roles["inverse"]["pin"], out,
        expected_data_manifest=frame["manifest"],
        inverse_forward_receipt_pin=roles["common"]["pin"] if shared else None, **kwargs)


def install_delegate_stub(monkeypatch, frame, calls):
    def delegate(data, forward, inverse, out, **kwargs):
        calls.append((str(forward), str(inverse), kwargs))
        assert kwargs["split"] == "validation" and "configuration_freeze" not in kwargs
        assert str(forward) == frame["roles"]["common"]["best"]["path"]
        out = Path(out)
        out.mkdir()
        for filename in ("forward_predictions.csv", "inverse_predictions.csv"):
            (out / filename).write_text("SYNTHETIC_PLACEHOLDER_ONLY\n")
        meta = {role: {"step": 2, "seed": info["config"]["seed"],
            "architecture": info["config"]["architecture"], "parameter_count": 1,
            "model_sha": name + "_SYNTHETIC_MODEL_DIGEST_NOT_EVALUATED"}
            for role, name in (("forward", "common"), ("inverse", "inverse"))
            for info in (frame["roles"][name],)}
        return {"split": "validation", "configuration_freeze": None,
            "identity": {"dataset": development.pin(frame["data"] / "dataset.npz"),
                "protocol_sha256": canonical_sha(development.protocol_identity()),
                "forward_checkpoint": development.pin(forward), "inverse_checkpoint": development.pin(inverse)},
            "model_metadata": meta, "exposure": frame["exposure"], "target_count": 2,
            "target_id_order_sha256": canonical_sha(["SYNTHETIC_VAL_A", "SYNTHETIC_VAL_B"]),
            "source_snapshot_geometries": 8, "source_split_geometries": 2,
            "forward": {"features": {"fixture": "COMMON_FORWARD_NOT_OWN"}},
            "inverse": {"continuous": {"synthetic": True}, "grid": {"synthetic": True}},
            "grid_effect": {"synthetic": True}}
    monkeypatch.setattr(development, "evaluate_frequency", delegate)


def test_baseline_routes_once_to_validation_and_preserves_no_clobber(frame, monkeypatch):
    calls = []
    install_delegate_stub(monkeypatch, frame, calls)
    monkeypatch.setattr(development, "_own_forward", lambda *a: pytest.fail("baseline must reuse existing F predictions"))
    out = frame["root"] / "baseline"
    result = invoke(frame, out)
    assert len(calls) == 1 and result["test_evaluation"] == "DEFERRED_UNTIL_FIXED_STUDY_COMPLETE"
    assert result["own_forward_checkpoint"] == result["inverse_scoring_forward_checkpoint"]
    assert not (out / "test").exists() and not (out / "TEST_CONFIGURATION_FREEZE.json").exists()
    assert len(json.loads((out / "ARM_SOURCE_TABLE.json").read_text())) == 2
    with pytest.raises(FileExistsError):
        invoke(frame, out)
    assert len(calls) == 1


def test_shared_common_inverse_never_substitutes_own_forward_scores(frame, monkeypatch):
    calls = []
    install_delegate_stub(monkeypatch, frame, calls)
    own_calls = []
    def own(data, info, summary, delegated_dir, out, device, micro_batch):
        own_calls.append(summary["target_id_order_sha256"])
        assert info["sources"]["best"] == frame["roles"]["own"]["best"]
        (out / "own_forward_predictions.csv").write_text("SYNTHETIC_OWN_FORWARD_ONLY\n")
        return {"features": {"fixture": "OWN_NOT_COMMON"}}, {
            "step": 2, "seed": info["config"]["seed"], "architecture": info["config"]["architecture"],
            "parameter_count": 1, "model_sha": "OWN_SYNTHETIC_NOT_INFERRED"}
    monkeypatch.setattr(development, "_own_forward", own)
    out = frame["root"] / "shared"
    result = invoke(frame, out, shared=True)
    summary = json.loads((out / "EVALUATION_SUMMARY.json").read_text())
    assert len(calls) == len(own_calls) == 1
    assert own_calls[0] == summary["target_id_order_sha256"]
    assert summary["own_forward"]["features"]["fixture"] == "OWN_NOT_COMMON"
    assert summary["inverse"]["scoring_forward_relationship"] == "SHARED_FROZEN_FORWARD"
    assert result["own_forward_checkpoint"] != result["inverse_scoring_forward_checkpoint"]
    assert len(json.loads((out / "ARM_SOURCE_TABLE.json").read_text())) == 3


@pytest.mark.parametrize("target", ["receipt", "manifest", "common_checkpoint"])
def test_bad_pins_fail_before_delegate(frame, monkeypatch, target):
    monkeypatch.setattr(development, "evaluate_frequency", lambda *a, **k: pytest.fail("must fail before inference"))
    if target == "receipt":
        frame["roles"]["own"]["pin"]["sha256"] = "0" * 64
    elif target == "manifest":
        frame["manifest"]["sha256"] = "0" * 64
    else:
        Path(frame["roles"]["common"]["best"]["path"]).write_bytes(b"SYNTHETIC CHANGED CHECKPOINT")
    with pytest.raises(ValueError):
        invoke(frame, frame["root"] / "rejected", shared=True)


def test_missing_common_binding_is_not_silently_replaced_by_own_forward(frame, monkeypatch):
    monkeypatch.setattr(development, "evaluate_frequency", lambda *a, **k: pytest.fail("wrong common F"))
    with pytest.raises(ValueError, match="not bound"):
        development.evaluate_development(frame["data"], frame["roles"]["own"]["pin"],
            frame["roles"]["inverse"]["pin"], frame["root"] / "wrong_common")


def test_test_request_is_rejected_without_data_or_model_access(tmp_path, monkeypatch):
    monkeypatch.setattr(development, "evaluate_development", lambda *a, **k: pytest.fail("test is forbidden"))
    request = tmp_path / "test_request.json"
    save_json(request, {"schema": development.SCHEMA, "split": "test", "test_evaluation_authorized": True})
    with pytest.raises(ValueError, match="validation-only"):
        development.evaluate_request(request, tmp_path / "forbidden")
