"""Relocated synthetic package tests; no research model is trained or evaluated."""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn import evaluation
from research.broadband56_nn import package_evaluation as module
from research.broadband56_nn.delivery import RUN_LABELS
from research.broadband56_nn.io import canonical_sha, load_checkpoint, save_checkpoint, save_json, sha256
from research.broadband56_nn.models import PACKAGE_MAPPING


def synthetic_package(tmp_path):
    root = tmp_path / "before_move"
    root.mkdir()
    data = root / "shared_data"
    data.mkdir()
    norm = {"field_names": [f"synthetic_g{i}" for i in range(10)],
            "g_min": [0.] * 10, "g_max": [2.] * 10, "s_mean": [0.] * 32,
            "s_scale": [1.] * 32, "y_mean": [0.] * 4, "y_scale": [1.] * 4}
    np.savez(data / "dataset.npz", geometry_ids=np.array(["train0", "train1", "val", "test"]),
             geometry_sha256=np.array([f"{i:064x}" for i in range(4)]), geometry=np.ones((4, 10)),
             frequency_hz=np.arange(5, 61, dtype=np.int64) * 1_000_000_000,
             s=np.zeros((4, 56, 32)), y=np.ones((4, 56, 4)), y_valid=np.ones((4, 56, 4), dtype=bool),
             split=np.array([0, 0, 1, 2]))
    save_json(data / "normalizer.json", norm)
    save_json(data / "splits.json", {"evidence": "SYNTHETIC_TEST_ONLY"})
    save_json(data / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS",
              "evidence": "SYNTHETIC_TEST_ONLY", "artifacts": {
                  name: {"path": name, "sha256": sha256(data / name)}
                  for name in ("dataset.npz", "normalizer.json", "splits.json")}})
    data_sha, norm_sha = sha256(data / "dataset.npz"), canonical_sha(norm)
    contract = {"field_names": norm["field_names"], "lower": [0.] * 10, "upper": [2.] * 10,
                "grid_um": .005, "grid_source_sha256": "c" * 64,
                "grid_status": "SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR",
                "port_contract": {"ports": 4, "port_order": ["P001", "P002", "P003", "P004"],
                                  "reference_impedance_ohm": 50.}}
    entries, forward_meta = {}, {}
    for number, label in enumerate(RUN_LABELS):
        is_forward = label.startswith("F")
        directory = root / "shared_forward" / label if is_forward else root / label
        directory.mkdir(parents=True)
        checkpoints = directory / "checkpoints"
        checkpoints.mkdir()
        state = {"evidence": "SYNTHETIC_RANDOM_TINY_FIXTURE_NOT_BB_PRETRAINING",
                 "role": "forward" if is_forward else "inverse",
                 "kind": ("F1" if label == "FREF" else label) if is_forward else PACKAGE_MAPPING[label][1],
                 "data_sha": data_sha, "normalizer_sha": norm_sha, "step": 1,
                 "model_state": {"synthetic_weight": torch.tensor([float(number)])},
                 "geometry_dim": 10, "contract": contract, "contract_sha": canonical_sha(contract),
                 "architecture": {"fixture": "tiny analytic substitute; not actual BB architecture"}}
        state["model_sha"] = module._model_state_digest(state)
        if not is_forward:
            own = PACKAGE_MAPPING[label][0]
            state["forward_model_sha"] = forward_meta[own]["model_sha"]
            state["forward_checkpoint"] = str(tmp_path / "never_created_historical_run" / own / "checkpoint.pt")
        checkpoint = checkpoints / "checkpoint_step_000001.pt"
        save_checkpoint(checkpoint, state)
        digest = sha256(checkpoint)
        if is_forward:
            forward_meta[label] = {"model_sha": state["model_sha"], "sha256": digest, "path": checkpoint}
        original = {"status": "PRETRAINED_PARTIAL", "updates_this_run": 1, "completed_step": 1,
                    "trainable_weights_changed": True, "data_sha": data_sha, "elapsed_seconds": .1,
                    "parameter_counts": {"total": 1}, "best_sha256": digest, "last_sha256": digest,
                    "best_checkpoint": str(tmp_path / "never_created_historical_run" / label / "best.pt"),
                    "last_checkpoint": str(tmp_path / "never_created_historical_run" / label / "last.pt")}
        evidence = directory / "original_evidence" / "TRAINING_RECEIPT.json"
        save_json(evidence, original)
        save_json(directory / "runtime_contract.json", contract)
        artifact = {"schema": "bb_portable_package.v1", "label": label,
                    "role": state["role"], "kind": state["kind"], "data_sha": data_sha,
                    "normalizer_sha": norm_sha, "data_manifest_sha": sha256(data / "data_manifest.json"),
                    "data_root": "../../shared_data" if is_forward else "../shared_data",
                    "contract_path": "runtime_contract.json", "contract_sha256": sha256(directory / "runtime_contract.json"),
                    "best_checkpoint": "checkpoints/checkpoint_step_000001.pt",
                    "last_checkpoint": "checkpoints/checkpoint_step_000001.pt",
                    "best_sha256": digest, "last_sha256": digest,
                    "training_receipt_sha256": sha256(evidence)}
        if not is_forward:
            own = PACKAGE_MAPPING[label][0]
            artifact["forward_reference"] = {
                "checkpoint_path": f"../shared_forward/{own}/checkpoints/checkpoint_step_000001.pt",
                "sha256": forward_meta[own]["sha256"], "model_sha": forward_meta[own]["model_sha"]}
        save_json(directory / "PACKAGE.json", artifact)
        entries[label] = {"status": "PRETRAINED_PARTIAL", "package_path": str(directory.relative_to(root)),
                          "package_sha256": sha256(directory / "PACKAGE.json")}
    save_json(root / "PACKAGE_RECEIPT.json", {"schema": "bb_delivery_package.v1", "data_sha": data_sha,
              "results": entries, "evidence": "SYNTHETIC_TEST_ONLY"})
    moved = tmp_path / "relocated_package"
    root.rename(moved)
    assert not root.exists() and not (tmp_path / "never_created_historical_run").exists()
    return moved


def test_check_only_relocated_registry_never_reads_original_paths_or_computes_statistics(tmp_path, monkeypatch):
    root = synthetic_package(tmp_path)
    before = {str(p.relative_to(root)): sha256(p) for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(module, "evaluate", lambda *_a, **_k: pytest.fail("check-only called numerical evaluation"))
    result = module.evaluate_package(root, tmp_path / "identity_check", check_only=True)
    assert result["status"] == "PASS_IDENTITY_CHECK_ONLY"
    assert result["numerical_evaluation_performed"] is False
    assert {str(p.relative_to(root)): sha256(p) for p in root.rglob("*") if p.is_file()} == before
    derived = json.loads((tmp_path / "identity_check" / "registry" / "BB05" / "TRAINING_RECEIPT.json").read_text())
    assert Path(derived["best_checkpoint"]).is_relative_to(root)
    assert "never_created_historical_run" not in derived["best_checkpoint"]
    original_copy = tmp_path / "identity_check" / "registry" / "BB05" / "ORIGINAL_TRAINING_RECEIPT.json"
    assert sha256(original_copy) == derived["original_receipt"]["sha256"]
    assert "never_created_historical_run" in json.loads(original_copy.read_text())["best_checkpoint"]


def test_registry_rejects_tampered_checkpoint_and_preserves_failure(tmp_path):
    root = synthetic_package(tmp_path)
    checkpoint = root / "BB06" / "checkpoints" / "checkpoint_step_000001.pt"
    with checkpoint.open("ab") as stream:
        stream.write(b"synthetic tamper")
    with pytest.raises(ValueError, match="SHA mismatch"):
        module.build_registry(root, tmp_path / "failed_registry")
    assert (tmp_path / "failed_registry" / "REGISTRY_FAILED.json").is_file()


def test_operational_references_cannot_escape_package(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="escapes"):
        module._inside(root, root, "../outside")
    with pytest.raises(ValueError, match="relative"):
        module._inside(root, root, str(outside))


def test_actual_evaluator_runs_on_relocated_synthetic_registry_and_recomputes_outputs(tmp_path, monkeypatch):
    root = synthetic_package(tmp_path)

    class TinyForward(torch.nn.Module):
        def forward(self, geometry, frequency):
            return torch.zeros((len(geometry), 56, 32), dtype=geometry.dtype, device=geometry.device)

    class TinyInverse(torch.nn.Module):
        def forward(self, tokens, condition):
            return torch.zeros((len(tokens), 10), dtype=tokens.dtype, device=tokens.device)

    def load_synthetic_model(record, bundle, device, role, expected_kind=None):
        state = load_checkpoint(record["checkpoint"]["path"])
        assert state["role"] == role and state["kind"] == expected_kind
        metadata = {key: state.get(key) for key in ("role", "kind", "step", "model_sha", "contract", "contract_sha",
                                                    "architecture", "forward_model_sha", "forward_checkpoint")}
        return (TinyForward() if role == "forward" else TinyInverse()), metadata

    monkeypatch.setattr(evaluation, "_load_model", load_synthetic_model)
    # No mock of discover_runs, panel construction, metric calculation, candidate
    # export or the evaluator: only tiny synthetic network implementations.
    result = module.evaluate_package(root, tmp_path / "fresh_evaluation", device="cpu", micro_batch=1)
    assert result["status"] == "COMPLETE_PROXY_EVALUATION"
    assert result["geometry_count"] == 1
    assert len(result["packages"]) == 6
    assert result["physical_winner"] == "NOT_ESTABLISHED"
    assert (tmp_path / "fresh_evaluation" / "evaluation" / "BB06" / "candidates.csv").is_file()
    proof = json.loads((tmp_path / "fresh_evaluation" / "PACKAGE_EVALUATION_RECEIPT.json").read_text())
    assert proof["new_statistics_recomputed"] is True
    assert proof["original_files_modified"] is False
    assert not (tmp_path / "never_created_historical_run").exists()


def test_package_test_split_keeps_unchanged_sealed_test_gate(tmp_path):
    root = synthetic_package(tmp_path)
    with pytest.raises(ValueError, match="sealed test"):
        module.evaluate_package(root, tmp_path / "sealed_attempt", split="test", device="cpu")
    assert not (tmp_path / "sealed_attempt" / "evaluation" / "fixed_specs").exists()


def test_package_wrapper_no_clobber(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        module.evaluate_package(tmp_path / "not_read", output, check_only=True)
    assert list(output.iterdir()) == []
