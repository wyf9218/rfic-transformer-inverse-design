"""Synthetic delivery tests. No research training, server or simulator is invoked."""

import copy
from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn import delivery
from research.broadband56_nn.io import canonical_sha, load_checkpoint, save_checkpoint, save_json, sha256
from research.broadband56_nn.models import build_forward
from research.broadband56_nn.training import Bundle, model_digest


def synthetic_bundle(tmp_path):
    root = tmp_path / "synthetic_data"
    root.mkdir()
    rng = np.random.default_rng(7)
    np.savez(root / "dataset.npz", geometry_ids=np.arange(10).astype(str),
             geometry_sha256=np.array([f"{i:064x}" for i in range(10)]),
             geometry=rng.uniform(size=(10, 2)), frequency_hz=np.arange(5, 61) * 1_000_000_000,
             s=rng.normal(size=(10, 56, 32)), y=np.ones((10, 56, 4)),
             y_valid=np.ones((10, 56, 4), bool), split=np.array([0] * 8 + [1] * 2))
    norm = {"field_names": ["a", "b"], "g_min": [0, 0], "g_max": [1, 1],
            "s_mean": [0] * 32, "s_scale": [1] * 32, "y_mean": [0] * 4, "y_scale": [1] * 4}
    save_json(root / "normalizer.json", norm)
    save_json(root / "splits.json", {"evidence": "SYNTHETIC_TEST_ONLY"})
    save_json(root / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS",
              "evidence": "SYNTHETIC_TEST_ONLY", "artifacts": {
                  name: {"path": name, "sha256": sha256(root / name)}
                  for name in ("dataset.npz", "normalizer.json", "splits.json")}})
    return Bundle(root)


def test_copy_is_no_clobber_and_not_hardlinked(tmp_path):
    source, destination = tmp_path / "original", tmp_path / "copy"
    source.write_bytes(b"immutable synthetic bytes")
    delivery._copy_pin(source, destination)
    assert source.stat().st_ino != destination.stat().st_ino
    with pytest.raises(FileExistsError):
        delivery._copy_pin(source, destination)
    destination.write_bytes(b"changed copy")
    assert source.read_bytes() == b"immutable synthetic bytes"


def test_status_promotion_requires_exact_best_and_last_resume_proof():
    trained = {"updates_this_run": 128, "stop_reason": "UPDATE_BUDGET_COMPLETE", "best_sha256": "a", "last_sha256": "b"}
    proof = {"status": "PASS", "original_best_sha256": "a", "original_last_sha256": "b",
             "checks": {name: True for name in delivery.REQUIRED_RESUME_CHECKS}}
    assert delivery._package_status(trained, proof) == "PRETRAINED"
    assert delivery._package_status(trained, None) == "PRETRAINED_PARTIAL"
    assert delivery._package_status(trained, {**proof, "original_best_sha256": "wrong"}) == "PRETRAINED_PARTIAL"
    assert delivery._package_status({**trained, "stop_reason": "TIME_BUDGET_PARTIAL"}, proof) == "PRETRAINED_PARTIAL"
    assert delivery._package_status({**trained, "updates_this_run": 0}, proof) == "FAILED"
    missing_checks = {key: value for key, value in proof.items() if key != "checks"}
    assert delivery._package_status(trained, missing_checks) == "PRETRAINED_PARTIAL"
    bad_checks = {**proof, "checks": {**proof["checks"], "optimizer_step_increment": False}}
    assert delivery._package_status(trained, bad_checks) == "PRETRAINED_PARTIAL"


def test_load_resume_refuses_campaign_without_terminal_receipt(tmp_path):
    with pytest.raises(ValueError, match="terminal receipt"):
        delivery.verify_load_resume(tmp_path / "not_read", tmp_path, tmp_path / "not_created", "cpu")
    assert not (tmp_path / "not_created").exists()


def test_fresh_process_load_output_agrees_for_synthetic_checkpoint(tmp_path):
    bundle = synthetic_bundle(tmp_path)
    torch.manual_seed(17)
    model = build_forward("F1", 2)
    state = {"evidence": "SYNTHETIC_RANDOM_WEIGHTS_NOT_PRETRAINED", "role": "forward", "kind": "F1",
             "model_state": model.state_dict(), "model_sha": model_digest(model),
             "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha}
    checkpoint = tmp_path / "synthetic.pt"
    save_checkpoint(checkpoint, state)
    expected = delivery._output_arrays(bundle, state, "cpu", None)
    worker_out = tmp_path / "fresh"
    command = [delivery.sys.executable, "-m", "research.broadband56_nn.delivery", "_load-worker",
               "--data", str(bundle.root), "--best", str(checkpoint), "--last", str(checkpoint),
               "--out", str(worker_out), "--device", "cpu"]
    process = delivery._command(command, tmp_path / "worker.log")
    assert process["pid"] != delivery.os.getpid()
    for name in ("best", "last"):
        with np.load(worker_out / f"{name}_outputs.npz") as actual:
            for key, values in expected.items():
                np.testing.assert_allclose(actual[key], values, rtol=1e-8, atol=1e-8)


def test_parity_contract_uses_original_bytes_not_reserialized_run_copy(tmp_path):
    original = tmp_path / "original.json"
    original.write_text('{"field_names":["a","b"]}', encoding="utf-8")
    value = {"field_names": ["a", "b"]}
    save_json(tmp_path / "contract.json", value)
    assert sha256(original) != sha256(tmp_path / "contract.json")
    parity = tmp_path / "parity.json"
    save_json(parity, {"contract_path": str(original), "contract_sha": sha256(original)})
    state = {"contract_sha": canonical_sha(value),
             "train_config": {"physical_ready": True, "physical_parity_receipt": str(parity)}}
    selected, selected_parity = delivery._resume_contract(state, tmp_path)
    assert selected == original and selected_parity == parity


def test_sampler_expected_continuation_is_deterministic(tmp_path):
    bundle = synthetic_bundle(tmp_path)
    rng = np.random.default_rng(17)
    for role in ("forward", "inverse"):
        state = {"role": role, "train_config": {"physical_ready": True},
                 "rng_state": {"sampler": copy.deepcopy(rng.bit_generator.state)}}
        before = canonical_sha(state["rng_state"]["sampler"])
        first = delivery._expected_sampler_state(state, bundle, 8)
        second = delivery._expected_sampler_state(state, bundle, 8)
        assert canonical_sha(first) == canonical_sha(second) != before
        assert canonical_sha(state["rng_state"]["sampler"]) == before


def checkpoint_pair_fixture(directory, bundle, *, kind="F1"):
    directory.mkdir(parents=True)
    contract = {"field_names": ["a", "b"], "lower": [0., 0.], "upper": [1., 1.]}
    best_path, last_path = directory / "best.pt", directory / "last.pt"
    common = {"role": "forward", "kind": kind, "geometry_dim": 2, "contract": contract,
              "contract_sha": canonical_sha(contract), "normalizer_sha": bundle.norm_sha,
              "data_sha": bundle.data_sha, "forward_model_sha": None, "architecture": {"kind": kind},
              "best_validation": .5, "best_checkpoint": str(best_path), "model_state": {},
              "model_sha": f"synthetic-{directory.name}-{kind}", "optimizer_state": {"state": {0: {"step": torch.tensor(5.)}}},
              "scheduler_state": {"last_epoch": 5}}
    save_checkpoint(best_path, {**common, "step": 5})
    save_checkpoint(last_path, {**common, "step": 8})
    receipt = {"status": "PRETRAINED_PARTIAL", "updates_this_run": 8,
               "trainable_weights_changed": True, "data_sha": bundle.data_sha,
               "best_checkpoint": str(best_path), "last_checkpoint": str(last_path),
               "best_sha256": sha256(best_path), "last_sha256": sha256(last_path),
               "completed_step": 8, "best_validation": .5}
    save_json(directory / "TRAINING_RECEIPT.json", receipt)
    return receipt, common


def test_checkpoint_pair_rejects_wrong_named_architecture_and_accepts_earlier_best(tmp_path):
    bundle = synthetic_bundle(tmp_path)
    receipt, _ = checkpoint_pair_fixture(tmp_path / "F1", bundle)
    _, states = delivery._checkpoint_pair(tmp_path / "F1", bundle.data_sha, "F1")
    assert states["best"]["step"] == 5 < states["last"]["step"]
    with pytest.raises(ValueError, match="mapping mismatch"):
        delivery._checkpoint_pair(tmp_path / "F1", bundle.data_sha, "F2")


def test_nonterminal_campaign_marker_is_not_launch_authority(tmp_path):
    results = {label: {"status": "PRETRAINED_PARTIAL"} for label in delivery.RUN_LABELS}
    results["BB06"] = {"status": "RUNNING"}
    save_json(tmp_path / "CAMPAIGN_RECEIPT.json", {"results": results})
    with pytest.raises(ValueError, match="nonterminal"):
        delivery.verify_load_resume(tmp_path / "unused", tmp_path, tmp_path / "not-created", "cpu")
    assert not (tmp_path / "not-created").exists()


def test_exact_shared_forward_mapping_and_independent_reference_required(tmp_path):
    bundle = synthetic_bundle(tmp_path)
    f2_receipt, f2 = checkpoint_pair_fixture(tmp_path / "runs" / "shared_forward" / "F2", bundle, kind="F2")
    ref_receipt, ref = checkpoint_pair_fixture(tmp_path / "runs" / "shared_forward" / "FREF", bundle)
    inverse = {"forward_checkpoint": f2_receipt["best_checkpoint"], "forward_model_sha": f2["model_sha"]}
    delivery._require_forward_reference({"best": inverse, "last": inverse}, "BB02", tmp_path / "runs")
    wrong = {"forward_checkpoint": ref_receipt["best_checkpoint"], "forward_model_sha": ref["model_sha"]}
    with pytest.raises(ValueError, match="exact package-mapped"):
        delivery._require_forward_reference({"best": wrong, "last": wrong}, "BB02", tmp_path / "runs")


def test_nonfinite_load_output_cannot_pass_nan_equality(tmp_path):
    bundle = synthetic_bundle(tmp_path)
    model = build_forward("F1", 2)
    with torch.no_grad():
        next(model.parameters()).fill_(float("nan"))
    state = {"role": "forward", "kind": "F1", "model_state": model.state_dict(),
             "model_sha": model_digest(model)}
    with pytest.raises(ValueError, match="nonfinite"):
        delivery._output_arrays(bundle, state, "cpu", None)


def test_portable_resume_passes_verified_best_override_and_rejects_exit_zero_no_receipt(tmp_path, monkeypatch):
    bundle = synthetic_bundle(tmp_path)
    package = tmp_path / "portable"
    receipt, common = checkpoint_pair_fixture(package, bundle)
    contract = package / "contract.json"
    save_json(contract, common["contract"])
    software = package / "software"
    software.mkdir()
    save_json(software / "SOFTWARE_IDENTITY.json", {"files": []})
    artifact = {"last_checkpoint": "last.pt", "last_sha256": receipt["last_sha256"],
                "best_checkpoint": "best.pt", "best_sha256": receipt["best_sha256"],
                "contract_path": "contract.json", "contract_sha256": sha256(contract),
                "data_root": str(bundle.root), "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha,
                "software_root": "software", "software_identity_sha256": sha256(software / "SOFTWARE_IDENTITY.json")}
    save_json(package / "PACKAGE.json", artifact)
    commands = []
    def no_training(command, log, software_root):
        commands.append(command)
        return {"pid": delivery.os.getpid() + 1, "returncode": 0}
    monkeypatch.setattr(delivery, "_command", no_training)
    output = tmp_path / "resume"
    with pytest.raises(FileNotFoundError):
        delivery.resume_package(package, output, 1, "cpu")
    command = commands[0]
    assert command[command.index("--best-checkpoint") + 1] == str(package / "best.pt")
    assert command[command.index("--best-checkpoint-sha256") + 1] == receipt["best_sha256"]
    assert not (output / "RESUME_PACKAGE_RECEIPT.json").exists()
