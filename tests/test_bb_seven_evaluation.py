"""Synthetic-only common15 protocol tests; no real model/data inference."""
import csv
from pathlib import Path

import numpy as np
import pytest
import torch

from research.broadband56_nn import seven_evaluation as module
from research.broadband56_nn.io import canonical_sha, load_checkpoint, read_json, save_checkpoint, save_json, sha256
from research.broadband56_nn.physics import extract_physical
from research.broadband56_nn.specs import tokenize


def synthetic_data(tmp_path):
    data = tmp_path / "synthetic_data"
    data.mkdir()
    frequency = np.arange(5, 61, dtype=np.int64) * 1_000_000_000
    z = np.eye(4)[None] * (5 + 1j * 2 * np.pi * frequency * 1e-9)[:, None, None]
    identity = np.eye(4)[None]
    s = (z - 50 * identity) @ np.linalg.inv(z + 50 * identity)
    raw = np.stack((s.real, s.imag), -1).reshape(56, 32)
    contract = {"field_names": [f"synthetic_geometry_{i}" for i in range(10)],
                "lower": [0.] * 10, "upper": [2.] * 10,
                "grid_um": .005, "grid_source_sha256": "c" * 64,
                "grid_status": "SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR",
                "port_contract": {"ports": 4, "port_order": ["P001", "P002", "P003", "P004"],
                                  "reference_impedance_ohm": 50.}}
    physical = extract_physical(torch.tensor(raw[None]), torch.tensor(frequency), contract["port_contract"])
    y = np.repeat(physical["y"].numpy(), 8, axis=0)
    valid = np.repeat(physical["valid_strict"].numpy(), 8, axis=0)
    valid[3, 10] = False
    split = np.array([0, 0, 1, 1, 1, 1, 2, 2])
    hashes = [f"{i:064x}" for i in range(8)]
    np.savez(data / "dataset.npz", geometry_ids=np.array([f"synthetic_{i}" for i in range(8)]),
             geometry_sha256=np.array(hashes), geometry=np.ones((8, 10)), frequency_hz=frequency,
             s=np.repeat(raw[None], 8, axis=0), y=y, y_valid=valid, split=split,
             strict_lumped_valid=valid.all(-1))
    norm = {"field_names": contract["field_names"], "g_min": [0.] * 10, "g_max": [2.] * 10,
            "s_mean": [0.] * 32, "s_scale": [1.] * 32, "y_mean": [0.] * 4,
            "y_scale": [1., 1., 20., 1.]}
    save_json(data / "normalizer.json", norm)
    names = ("train", "validation", "test")
    save_json(data / "splits.json", {"by_geometry_sha256": {key: names[int(value)] for key, value in zip(hashes, split)}})
    save_json(data / "data_manifest.json", {"schema": "bb_data_manifest.v1", "status": "PASS",
              "evidence": "SYNTHETIC_TEST_ONLY", "normalizer_fit_split": "train", "artifacts": {
                  name: {"path": name, "sha256": sha256(data / name)}
                  for name in ("dataset.npz", "normalizer.json", "splits.json")}})
    return data, norm, contract, raw, y[0, 10], dict(zip(hashes, split.tolist()))


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    data, norm, contract, raw, target, split_map = synthetic_data(tmp_path)
    records, states = {}, {}
    for name in module.RECORDS:
        role = "forward" if name in module.FORWARDS else "inverse"
        is_bb00 = name.startswith("BB00")
        kind = "BB00" if is_bb00 else ("F1" if name == "FREF" else name) if role == "forward" else module.PACKAGE_MAPPING[name][1]
        private_norm = {**norm, "y_scale": [99.] * 4} if is_bb00 else norm
        state = {"schema": "bb00_training_state.v1" if is_bb00 else "bb_training_state.v1",
                 "fixture": "SYNTHETIC_TINY_IO_NOT_TRAINED_BB_MODEL", "role": role, "kind": kind,
                 "step": 2, "data_sha": sha256(data / "dataset.npz"), "data_manifest_sha": sha256(data / "data_manifest.json"),
                 "normalizer": private_norm, "normalizer_sha": canonical_sha(private_norm),
                 "split_by_geometry_sha256": split_map, "contract": contract, "contract_sha": canonical_sha(contract),
                 "geometry_dim": 10, "model_sha": name, "model_state": {"synthetic": torch.ones(1)},
                 "architecture": {"fixture": "tiny synthetic substitutes, not actual BB architecture"},
                 "train_config": {"seed": 29 if name == "FREF" else 17, "effective_batch": 32,
                                  "micro_batch": 8, "validation_interval": 32},
                 "parent_checkpoint": None, "initialization": "RANDOM_FROM_SCRATCH_NO_LEGACY_WEIGHTS"}
        if role == "inverse":
            own = "BB00_FORWARD" if name == "BB00" else module.PACKAGE_MAPPING[name][0]
            state.update(forward_model_sha=own, forward_checkpoint=records[own]["checkpoint"]["path"],
                         forward_checkpoint_sha256=records[own]["checkpoint"]["sha256"])
        checkpoint = tmp_path / f"{name}.pt"
        save_checkpoint(checkpoint, state)
        receipt = tmp_path / f"{name}_TRAINING_RECEIPT.json"
        save_json(receipt, {"status": "SMOKE_TRAINED", "data_sha": state["data_sha"],
                  "updates_this_run": 4, "completed_step": 4, "best_sha256": sha256(checkpoint),
                  "best_checkpoint": str(checkpoint), "trainable_weights_changed": True, "test_access": False})
        records[name] = {"checkpoint": module._pin(checkpoint), "receipt": module._pin(receipt)}
        states[name] = state
    registry = tmp_path / "STUDY_MODELS.json"
    save_json(registry, {"schema": "bb_seven_registry.v1", "data_sha": sha256(data / "dataset.npz"),
                        "normalizer_sha": canonical_sha(norm), "records": records})

    class TinyForward(torch.nn.Module):
        def forward(self, geometry, frequency):
            return torch.as_tensor(raw, dtype=geometry.dtype, device=geometry.device)[None].expand(len(geometry), -1, -1)

    class TinyDirectForward(torch.nn.Module):
        def forward(self, geometry):
            return torch.as_tensor(target, dtype=geometry.dtype, device=geometry.device)[None].expand(len(geometry), -1)

    class TinyInverse(torch.nn.Module):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def forward(self, inputs, mask=None):
            if self.name == "BB00":
                assert inputs.shape[1:] == (4,)
                return torch.ones((len(inputs), 10), dtype=inputs.dtype, device=inputs.device)
            assert inputs.shape[1:] == (56, 137)
            assert mask.sum(-1).eq(1).all() and mask[:, 10].all()
            # The 17 deterministic frequency fields may remain; all hidden
            # sample-specific values, masks, tolerances and relations are zero.
            hidden = torch.ones(56, dtype=torch.bool, device=inputs.device)
            hidden[10] = False
            assert inputs[:, hidden, 17:].eq(0).all()
            assert inputs[:, :, 17:113].eq(0).all()  # all S channels and masks
            value = float("nan") if self.name == "BB04" else 0.
            return torch.full((len(inputs), 10), value, dtype=inputs.dtype, device=inputs.device)

    def loader(record, bundle, device):
        name = record["name"]
        state = states[name]
        meta = {**state, "parameter_count": 1, "seed": 29 if name == "FREF" else 17}
        model = TinyDirectForward() if name == "BB00_FORWARD" else TinyForward() if name in module.FORWARDS else TinyInverse(name)
        return model.to(device), meta

    monkeypatch.setattr(module, "_load_component", loader)
    return data, registry, states, records


def test_common_spec_contains_only_same_four_15ghz_targets():
    values = np.array([[1., 2., 3., .4], [2., 3., 4., .5]])
    frequency = np.arange(5, 61) * 1e9
    spec = module.physical15_spec(values, frequency)
    assert not spec["s_mask"].any()
    assert spec["s_target"].eq(0).all()
    assert spec["y_mask"].sum().item() == 8
    np.testing.assert_allclose(spec["y_target"][:, 10], values)
    assert spec["y_target"][:, :10].eq(0).all() and spec["y_target"][:, 11:].eq(0).all()
    with pytest.raises(ValueError, match="four|requests only"):
        module.physical15_spec(np.ones((2, 56, 4)), frequency)


def test_invalid_unavailable_and_infeasible_remain_fixed_denominator():
    target = np.ones((3, 4))
    pred = target.copy()
    pred[1, 0] = np.nan
    metric = module.physical_response_metrics(pred, target, np.ones(4), feasible=np.array([True, True, False]))
    assert metric["requested_conditions"] == 12
    assert metric["invalid_requested_predictions"] == 5
    assert metric["normalized_rmse"] is None and metric["p95_per_geometry_normalized_rmse"] is None
    assert metric["joint_hit_count"] == 1 and metric["joint_hit_denominator"] == 3
    assert metric["target_failure_count"] == 2
    missing = module.physical_response_metrics(None, target, np.ones(4))
    assert missing["joint_hit_rate"] == 0 and missing["invalid_requested_predictions"] == 12
    assert missing["physical_unit_features"]["Lp_nH"]["mae"] is None


def test_target_plan_source_strict_ids_frozen_and_no_clobber(prepared, tmp_path):
    data, _, _, _ = prepared
    path = tmp_path / "validation_targets.json"
    module.freeze_common_15ghz_targets(data, path)
    plan = read_json(path)
    assert plan["target_ids"] == ["synthetic_2", "synthetic_4", "synthetic_5"]
    assert plan["source_split_geometries"] == 4 and plan["source_label_ineligible_geometries"] == 1
    assert plan["channel_scale"] == [1., 1., 20., 1.]
    with pytest.raises(FileExistsError):
        module.freeze_common_15ghz_targets(data, path)


def test_reordered_or_missing_target_plan_is_rejected(prepared, tmp_path):
    data, _, _, _ = prepared
    path = tmp_path / "targets.json"
    module.freeze_common_15ghz_targets(data, path)
    bad = read_json(path)
    bad["target_ids"].reverse()
    altered = tmp_path / "altered.json"
    save_json(altered, bad)
    with pytest.raises(ValueError, match="IDs/order"):
        module._verify_targets(altered, module.Bundle(data), "validation")


def test_test_requires_freeze_before_loading_test_arrays(prepared, tmp_path, monkeypatch):
    data, registry, _, _ = prepared
    def forbidden(*_):
        raise AssertionError("Bundle/test arrays loaded before release gate")
    monkeypatch.setattr(module, "Bundle", forbidden)
    with pytest.raises(ValueError, match="sealed common15"):
        module.evaluate_seven(data, registry, tmp_path / "not-created-targets", tmp_path / "blocked",
                              split="test", device="cpu")
    assert (tmp_path / "blocked/EVALUATION_FAILED.json").is_file()


def test_synthetic_full_seven_validation_freeze_test_and_bb00_not_supported(prepared, tmp_path):
    data, registry, _, _ = prepared
    vp, tp = tmp_path / "val_targets.json", tmp_path / "test_targets.json"
    module.freeze_common_15ghz_targets(data, vp)
    module.freeze_common_15ghz_targets(data, tp, split="test")
    val = module.evaluate_seven(data, registry, vp, tmp_path / "validation", device="cpu", micro_batch=2)
    assert val["status"] == "COMPLETE_PROXY_EVALUATION" and len(val["packages"]) == 7
    assert val["target_count"] == 3 and val["physical_winner"] == "NOT_ESTABLISHED"
    assert val["forward_models"]["BB00_FORWARD"]["broadband_spectrum"] == "NOT_SUPPORTED"
    bad = read_json(tmp_path / "validation/BB04/metrics.json")
    assert bad["modes"]["continuous"]["common"]["target_failure_count"] == 3
    assert bad["modes"]["continuous"]["common"]["joint_hit_denominator"] == 3
    assert bad["modes"]["grid"]["common"]["normalized_rmse"] is None
    for name in module.PACKAGES:
        report = read_json(tmp_path / "validation" / name / "metrics.json")
        assert report["target_plan"]["sha256"] == sha256(vp)
        assert report["completed_updates"] == 4 and report["selected_step"] == 2
        assert report["real_emx_validation"] == "NOT_RUN"
    freeze = tmp_path / "freeze.json"
    module.freeze_seven_configuration(registry, data, tmp_path / "validation/EVALUATION_SUMMARY.json", tp, freeze)
    tested = module.evaluate_seven(data, registry, tp, tmp_path / "test", split="test",
                                   configuration_freeze=freeze, device="cpu", micro_batch=2)
    assert tested["target_count"] == 2
    assert tested["best_validation_candidate"] == "NOT_DETERMINED"
    assert not tested["test_used_to_select_model"]
    assert (tmp_path / "test/comparison_15ghz.csv").is_file()
    assert (tmp_path / "test/SHA256SUMS.txt").is_file()
    with pytest.raises(FileExistsError):
        module.evaluate_seven(data, registry, vp, tmp_path / "validation", device="cpu")


def test_old_snapshot_checkpoint_lineage_rejected_before_model_calls(prepared, tmp_path):
    data, registry, states, records = prepared
    bad = {**states["BB00"], "data_sha": "0" * 64}
    checkpoint = tmp_path / "olddata.pt"
    save_checkpoint(checkpoint, bad)
    record = {"name": "BB00", "checkpoint": module._pin(checkpoint)}
    with pytest.raises(ValueError, match="not new-snapshot"):
        module._verify_new_training_lineage(record, module._data_identity(data))


def test_resume_probe_and_changed_split_are_ineligible(prepared, tmp_path):
    data, _, states, _ = prepared
    for suffix, changes, message in (("probe", {"resume_probe": True}, "diagnostic"),
                                      ("split", {"split_by_geometry_sha256": {}}, "splits differ")):
        checkpoint = tmp_path / (suffix + ".pt")
        save_checkpoint(checkpoint, {**states["BB00"], **changes})
        with pytest.raises(ValueError, match=message):
            module._verify_new_training_lineage({"name": "BB00", "checkpoint": module._pin(checkpoint)},
                                                module._data_identity(data))


def test_test_target_created_after_validation_is_not_a_preregistration(prepared, tmp_path):
    data, registry, _, _ = prepared
    vp, tp = tmp_path / "val.json", tmp_path / "late_test.json"
    module.freeze_common_15ghz_targets(data, vp)
    module.evaluate_seven(data, registry, vp, tmp_path / "validation", device="cpu", micro_batch=2)
    module.freeze_common_15ghz_targets(data, tp, split="test")
    with pytest.raises(ValueError, match="predeclared before validation"):
        module.freeze_seven_configuration(registry, data, tmp_path / "validation/EVALUATION_SUMMARY.json",
                                          tp, tmp_path / "blocked_freeze.json")
    assert not (tmp_path / "blocked_freeze.json").exists()


def test_actual_bb00_loader_native_four_input_api_without_training(tmp_path):
    """Random BB00 serialization/API smoke only; not trained-model evidence."""
    from research.broadband56_nn.bb00 import BB00MLP
    from research.broadband56_nn.training import model_digest
    data, shared, contract, _, _, split_map = synthetic_data(tmp_path)
    private = {"g_mean": [1.] * 10, "g_scale": [1.] * 10, "g_lower": [0.] * 10, "g_upper": [2.] * 10,
               "y_mean": [1.] * 4, "y_scale": [99.] * 4}
    model = BB00MLP("inverse", private)
    checkpoint = tmp_path / "random_serialization_fixture.pt"
    state = {"schema": "bb00_training_state.v1", "kind": "BB00", "role": "inverse",
             "data_sha": sha256(data / "dataset.npz"), "geometry_dim": 10, "step": 1,
             "normalizer": private, "normalizer_sha": canonical_sha(private),
             "contract": contract, "contract_sha": canonical_sha(contract),
             "model_state": model.state_dict(), "model_sha": model_digest(model),
             "architecture": model.architecture, "fixture": "RANDOM_SERIALIZATION_ONLY_NOT_PRETRAINED"}
    save_checkpoint(checkpoint, state)
    loaded, metadata = module._load_component({"name": "BB00", "checkpoint": module._pin(checkpoint)}, module.Bundle(data), "cpu")
    with torch.no_grad():
        geometry = loaded(torch.ones((2, 4)))
    assert geometry.shape == (2, 10) and torch.isfinite(geometry).all()
    assert metadata["normalizer_sha"] != canonical_sha(shared)
    assert metadata["parameter_count"] > 0


def test_broadband_export_only_saved_metrics_own_grid_explicit_missing(tmp_path):
    root = tmp_path / "broadband"
    root.mkdir()
    summary = {"status": "COMPLETE_PROXY_EVALUATION", "packages": {}}
    names = [task.lower() + "_" + mode for task, mode in module.broadband.PANEL_DEFINITIONS]
    for package in module.PACKAGE_MAPPING:
        directory = root / package
        directory.mkdir()
        panels = {name: {"status": "EVALUATED_PROXY_ONLY", "source_geometry_count": 2,
                        "holdout_geometry_count": 3, "source_label_ineligible_geometry_count": 1,
                        **{key: {"normalized_rmse": .25, "requested_conditions": 8,
                                 "condition_violation_denominator": 8, "invalid_requested_predictions": 0}
                           for key in ("own_forward_continuous", "common_forward_continuous", "common_forward_grid")}}
                  for name in names}
        save_json(directory / "metrics.json", {"panels": panels})
        summary["packages"][package] = {"metrics": module._pin(directory / "metrics.json")}
    save_json(root / "EVALUATION_SUMMARY.json", summary)
    csv_path = tmp_path / "comparison_broadband.csv"
    receipt = module.export_broadband_comparison(root, csv_path)
    assert receipt["rows"] == 192 and receipt["new_model_calls"] == 0
    rows = list(csv.DictReader(csv_path.open()))
    missing = [row for row in rows if row["proxy"] == "own" and row["geometry_mode"] == "grid"]
    assert len(missing) == 48
    assert all(row["status"] == "NOT_RECORDED" and row["normalized_rmse"] == "" for row in missing)
    assert receipt["bb00_broadband"] == "NOT_SUPPORTED"
    with pytest.raises(FileExistsError):
        module.export_broadband_comparison(root, csv_path)
