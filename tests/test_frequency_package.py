"""Metadata-only packaging tests; no model, optimizer or real test-data access."""
import json
from pathlib import Path

import pytest

from research.broadband56_nn import frequency_package as package
from research.broadband56_nn.io import save_json, sha256


def write(path, value):
    save_json(path, value)
    return path


def pin(path):
    return {"path": str(path), "sha256": sha256(path)}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    study, data = tmp_path / "study", tmp_path / "data"
    study.mkdir(); data.mkdir()
    dataset = data / "dataset.npz"
    dataset.write_bytes(b"SYNTHETIC_NO_NUMERICAL_DATA")
    manifest = write(data / "data_manifest.json", {"artifacts": {"dataset.npz": pin(dataset)}})
    for name in ("normalizer.json", "splits.json", "geometry_provenance.json"):
        write(data / name, {"synthetic": True})
    recipe = write(tmp_path / "recipe.json", {"synthetic": True})
    request = write(study / "request.json", {"legacy_replay_receipt": str(recipe),
        "legacy_replay_sha256": sha256(recipe), "resources": {"min_available_bytes": 1, "min_disk_bytes": 1}})
    roles, results = {}, {}
    for role in ("forward", "inverse"):
        attempt = study / role / "attempt_0001"
        attempt.mkdir(parents=True)
        entry = {}
        for selection, step in (("best", 1), ("last", 3)):
            weight = attempt / f"checkpoint_step_{step:06d}.pt"
            weight.write_bytes((role + selection + "SYNTHETIC_NOT_TORCH").encode())
            write(Path(str(weight)+".identity.json"), {"path": weight.name, "sha256": sha256(weight)})
            entry[selection] = pin(weight)
        receipt = {"role": role, "updates_this_run": 3, "started_step": 0, "completed_step": 3,
            "elapsed_seconds": .1, "eligible_rows": {"train": {"eligible_geometries": 6}},
            "stop_reason": "UPDATE_BUDGET_COMPLETE",
            **{s+"_sha256": entry[s]["sha256"] for s in ("best", "last")}}
        entry["receipt"] = pin(write(attempt / "TRAINING_RECEIPT.json", receipt))
        for name in ("config.json", "contract.json", "history.json"):
            write(attempt / name, {"synthetic": True})
        write(attempt / "normalizer.json", {"train_support_min": [0]*4, "train_support_max": [1]*4})
        (attempt.parent / "attempt_0001.log").write_text("synthetic completed log\n")
        roles[role] = entry
        results[role] = {"status": "PASS", "checks": {name: True for name in package.ACCEPTANCE_CHECKS},
            **{"original_"+s+"_sha256": entry[s]["sha256"] for s in ("best", "last")}}
    pair = write(study / "PAIR_RECEIPT.json", {"schema": "frequency_pair_receipt.v1",
        "status": "TRAINED_BUDGET_OR_EARLY_STOP", "roles": roles, "request": pin(request),
        "data_root": str(data), "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
        "experiment_class": "SYNTHETIC_DEVELOPMENT"})
    profile = write(tmp_path / "profile" / "frequency_data_profile.json", {
        "schema": "bb_frequency_data_profile.v1", "frequency_slots": 56,
        "snapshot_unique_geometries": 8, "source_identity": {
            "dataset": pin(dataset), "data_manifest_sha256": sha256(manifest)},
        "rows": [{"frequency_ghz": f, "label_modes": {mode: {
            "status": "NO_STRICT_LABELS" if mode == "STRICT_LUMPED" and f > 30 else "PROVISIONAL",
            "eligible_count": 0 if mode == "STRICT_LUMPED" and f > 30 else 8,
            "splits": {"train": {"eligible": 6}, "validation": {"eligible": 1}, "test": {"eligible": 1}}}
            for mode in package.MODES}} for f in range(5, 61)]})
    acceptance = write(tmp_path / "acceptance" / "BB00_LOAD_RESUME_RECEIPT.json", {
        "schema": "bb00_load_resume_proof.v1", "status": "PASS", "data_sha": sha256(dataset),
        "original_checkpoint_bytes_unchanged": True, "results": results})
    identity = {"dataset": pin(dataset), "frequency_ghz": 15, "label_mode": "STRICT_LUMPED",
        **{role+"_checkpoint": roles[role]["best"] for role in roles}}
    evaluation = tmp_path / "evaluation"
    freeze = write(evaluation / "TEST_FREEZE.json", {"schema": "frequency_evaluation_freeze.v1",
        "status": "FROZEN", "identity": identity})
    for split in ("validation", "test"):
        summary = {"schema": "frequency_evaluation_summary.v1", "status": "COMPLETE_DESCRIPTIVE_EVALUATION",
            "split": split, "identity": identity, "artifacts": {},
            "configuration_freeze": pin(freeze) if split == "test" else None}
        write(evaluation / split / "EVALUATION_SUMMARY.json", summary)
    # The actual package implementation reloads models; this bounded test does
    # not compete with an authorized real training process for compute.
    monkeypatch.setattr(package, "_reload_copies", lambda *a: {"status": "PASS", "evidence": "MOCK_NO_MODEL"})
    return pair, profile, evaluation, acceptance


def test_portable_copy_index_and_no_duplicate_submission(inputs, tmp_path):
    output = tmp_path / "package"
    receipt = package.package_model(*inputs, output)
    assert receipt["trained_model_count"] == 1 and receipt["source_snapshot_geometries"] == 8
    assert receipt["relocated_optimizer_resume"] == "NOT_RUN"
    root, index = package.load_index(output / "MODEL_INDEX.json")
    assert len(index["frequencies"]) == 56
    assert sum(s["model_available"] for r in index["frequencies"] for s in r["label_modes"].values()) == 1
    model = package._route(index, 15, "STRICT_LUMPED")
    assert model["formal_10k"] is False and model["training_status"] == "PROVISIONAL_PARTIAL"
    assert not (root / "data_references" / "dataset.npz").exists()
    for role, selection in (("forward", "best"), ("inverse", "last")):
        copied = root / model["roles"][role][selection]["path"]
        assert sha256(copied) == model["roles"][role][selection]["sha256"]
        assert Path(str(copied)+".identity.json").exists()
    # Original sources may become unavailable; exact index loading is portable.
    inputs[0].parent.rename(tmp_path / "original_study_moved")
    package.load_index(output / "MODEL_INDEX.json")
    with pytest.raises(FileExistsError):
        package.package_model(*inputs, output)


@pytest.mark.parametrize("frequency,mode", [(14, "STRICT_LUMPED"), (31, "STRICT_LUMPED"),
                                            (15, "POINTWISE_DESCRIPTOR_EXPERIMENTAL"),
                                            (15.5, "STRICT_LUMPED")])
def test_untrained_routes_never_fallback(inputs, tmp_path, frequency, mode):
    package.package_model(*inputs, tmp_path / "package")
    _, index = package.load_index(tmp_path / "package" / "MODEL_INDEX.json")
    with pytest.raises(ValueError):
        package._route(index, frequency, mode)


def test_changed_weights_do_not_load(inputs, tmp_path):
    package.package_model(*inputs, tmp_path / "package")
    root, index = package.load_index(tmp_path / "package" / "MODEL_INDEX.json")
    model = package._route(index, 15, "STRICT_LUMPED")
    (root / model["roles"]["forward"]["best"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="identity mismatch"):
        package.load_index(root / "MODEL_INDEX.json")


def test_empty_acceptance_checks_cannot_claim_resume(inputs, tmp_path):
    proof = json.loads(inputs[3].read_text())
    proof["results"]["forward"]["checks"] = {"pretend": True}
    inputs[3].write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="incomplete"):
        package.package_model(*inputs, tmp_path / "package")
    assert (tmp_path / "package" / "PACKAGE_FAILED.json").exists()


def test_missing_test_freeze_refuses_delivery(inputs, tmp_path):
    summary = inputs[2] / "test" / "EVALUATION_SUMMARY.json"
    value = json.loads(summary.read_text())
    value["configuration_freeze"]["sha256"] = "0"*64
    summary.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="identity mismatch"):
        package.package_model(*inputs, tmp_path / "package")


def test_output_inside_evidence_is_rejected_without_writes(inputs):
    destination = inputs[2] / "bad_package"
    with pytest.raises(ValueError, match="outside"):
        package.package_model(*inputs, destination)
    assert not destination.exists()


def test_package_can_be_nested_in_study_but_not_in_prepared_data(inputs, tmp_path):
    output = inputs[0].parent / "posttrain" / "package"
    package.package_model(*inputs, output)
    package.load_index(output / "MODEL_INDEX.json")
    bad = tmp_path / "data" / "package"
    with pytest.raises(ValueError, match="outside"):
        package.package_model(*inputs, bad)
    assert not bad.exists()


@pytest.mark.parametrize("role", ["forward", "inverse"])
def test_resume_regenerates_config_and_relocates_best_without_optimizer(inputs, tmp_path, monkeypatch, role):
    from dataclasses import asdict
    from research.broadband56_nn import bb00, study_once
    package.package_model(*inputs, tmp_path / "package")
    config = bb00.BB00Config(role, device="cpu")
    monkeypatch.setattr(bb00, "load_bb00", lambda *a, **k: (None, {"train_config": asdict(config)}))
    monkeypatch.setattr(study_once, "resource_snapshot", lambda *a, **k: {"status": "PASS"})
    observed = []
    def no_training(*args, **kwargs):
        observed.append((args, kwargs))
        return {"status": "MOCK_NO_OPTIMIZER"}
    monkeypatch.setattr(bb00, "train_bb00", no_training)
    result = package.resume_package(tmp_path / "package" / "MODEL_INDEX.json", tmp_path / "data",
        tmp_path / "resume", frequency_ghz=15, label_mode="STRICT_LUMPED", role=role, steps=1,
        deadline_utc="2099-01-01T00:00:00Z", device_lock=tmp_path / "shared_device.lock", resume_probe=True)
    assert result["status"] == "MOCK_NO_OPTIMIZER" and len(observed) == 1
    args, kwargs = observed[0]
    assert args[2].steps == 1 and args[2].deadline_utc == "2099-01-01T00:00:00Z"
    assert "package/models" in str(kwargs["resume_best_checkpoint"])
    assert sha256(kwargs["resume_best_checkpoint"]) == kwargs["resume_best_checkpoint_sha256"]
    assert kwargs["resume_probe"] is True
    assert (tmp_path / "resume" / "RESUME_CONFIG.json").is_file()
    package.load_index(tmp_path / "package" / "MODEL_INDEX.json")
