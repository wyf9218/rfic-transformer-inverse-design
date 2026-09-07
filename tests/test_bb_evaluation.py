"""Synthetic evaluation gates/panels; never evaluates research checkpoints."""
import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from research.broadband56_nn.evaluation import (  # noqa: E402
    FORWARD_IDS, PACKAGE_IDS, build_panels, discover_runs, evaluate,
    freeze_panels, verify_configuration_freeze, _slice_spec, _panel_support,
    evaluation_protocol_identity, _grid_from_contract, _grid_geometry,
)
from research.broadband56_nn.io import save_json, sha256  # noqa: E402
from research.broadband56_nn.specs import tokenize  # noqa: E402


class SyntheticBundle:
    def __init__(self):
        self.data_sha, self.norm_sha, self.manifest_sha = "d" * 64, "n" * 64, "m" * 64
        self.norm = {"s_mean": [0.] * 32, "s_scale": [1.] * 32,
                     "y_mean": [0.] * 4, "y_scale": [1.] * 4}
        self.frequency = torch.arange(5., 61.) * 1e9
        y_valid = np.ones((6, 56, 4), dtype=bool)
        y_valid[2] = False  # One validation geometry has no strict physical label.
        self.arrays = {"geometry": np.zeros((6, 10)), "s": np.zeros((6, 56, 32)),
                       "y": np.ones((6, 56, 4)), "y_valid": y_valid,
                       "split": np.array([0, 1, 1, 1, 2, 2]),
                       "geometry_ids": np.array([f"synthetic-{i}" for i in range(6)]),
                       "geometry_sha256": np.array([str(i) * 64 for i in range(6)]),
                       "frequency_hz": np.arange(5, 61, dtype=np.int64) * 1_000_000_000}

    def batch(self, indices, device):
        return {key: torch.as_tensor(self.arrays[key][indices], device=device,
                                    dtype=torch.bool if key == "y_valid" else torch.float32)
                for key in ("geometry", "s", "y", "y_valid")}


def records():
    return {name: {"checkpoint": {"sha256": f"{index:064x}"}} for index, name in enumerate((*FORWARD_IDS, *PACKAGE_IDS))}


def test_sealed_test_requires_exact_all_checkpoint_freeze(tmp_path):
    bundle, runs = SyntheticBundle(), records()
    with pytest.raises(ValueError, match="sealed test"):
        verify_configuration_freeze(None, runs, bundle, "test")
    assert verify_configuration_freeze(None, runs, bundle, "validation")["test_access"] is False
    freeze = {"schema": "bb_evaluation_configuration_freeze.v1", "status": "FROZEN",
              "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha,
              "evaluation_protocol_sha256": evaluation_protocol_identity()["sha256"],
              "checkpoints": {name: record["checkpoint"]["sha256"] for name, record in runs.items()}}
    path = tmp_path / "freeze.json"
    save_json(path, freeze)
    assert verify_configuration_freeze(path, runs, bundle, "test")["test_access"] is True
    freeze["checkpoints"]["BB06"] = "changed"
    changed = tmp_path / "changed.json"
    save_json(changed, freeze)
    with pytest.raises(ValueError, match="ten selected"):
        verify_configuration_freeze(changed, runs, bundle, "test")


def test_all_holdout_rows_are_used_and_source_validity_exclusions_explicit():
    bundle = SyntheticBundle()
    indices, panels = build_panels(bundle, "validation")
    np.testing.assert_array_equal(indices, [1, 2, 3])
    assert len(panels) == 8
    for panel in panels.values():
        expected = [1, 3] if panel["task"] == "PHYSICAL" else [1, 2, 3]
        np.testing.assert_array_equal(panel["indices"], expected)
        spec = panel["spec"]
        if panel["task"] == "PHYSICAL":
            assert not spec["s_mask"].any()
        else:
            assert not spec["y_mask"].any()
    _, repeated = build_panels(bundle, "validation")
    for name, panel in panels.items():
        assert torch.equal(panel["spec"]["s_mask"], repeated[name]["spec"]["s_mask"])
        assert torch.equal(panel["spec"]["y_mask"], repeated[name]["spec"]["y_mask"])


def test_frozen_panel_artifacts_bind_masks_ids_and_original_snapshot(tmp_path):
    bundle = SyntheticBundle()
    indices, panels = build_panels(bundle, "validation")
    identity = freeze_panels(tmp_path, bundle, indices, panels, "validation")
    assert identity["sha256"] == sha256(identity["path"])
    manifest = json.loads((tmp_path / "fixed_specs" / "PANEL_MANIFEST.json").read_text())
    assert manifest["data_sha"] == bundle.data_sha
    assert manifest["holdout_geometries"] == 3
    physical = next(panel for panel in manifest["panels"] if panel["name"] == "physical_full")
    assert physical["excluded_no_valid_source_physical_target"] == 1
    assert physical["geometry_ids"] == ["synthetic-1", "synthetic-3"]
    assert physical["requested_s_conditions"] == 0
    assert physical["mask_artifact"]["sha256"] == sha256(physical["mask_artifact"]["path"])


def test_spec_slicing_matches_whole_panel_tokens():
    bundle = SyntheticBundle()
    _, panels = build_panels(bundle, "validation")
    spec = panels["physical_multi"]["spec"]
    whole, mask = tokenize(spec, bundle.norm)
    subset = _slice_spec(spec, 1, 2, torch.device("cpu"))
    tokens, subset_mask = tokenize(subset, bundle.norm)
    torch.testing.assert_close(tokens, whole[1:2])
    assert torch.equal(subset_mask, mask[1:2])
    assert subset["frequency_hz"].shape == (56,)


def test_no_valid_source_physical_labels_remain_explicitly_not_evaluable():
    bundle = SyntheticBundle()
    bundle.arrays["y_valid"][:] = False
    _, panels = build_panels(bundle, "test")
    assert len(panels["spectrum_full"]["indices"]) == 2
    assert panels["physical_full"]["spec"] is None
    assert panels["physical_full"]["status"] == "NOT_EVALUABLE_NO_VALID_SOURCE_PHYSICAL_TARGET"


def test_receipt_discovery_requires_positive_updates_before_any_model_load(tmp_path):
    directory = tmp_path / "shared_forward" / "F1"
    directory.mkdir(parents=True)
    save_json(directory / "TRAINING_RECEIPT.json", {"status": "PRETRAINED", "updates_this_run": 0})
    with pytest.raises(ValueError, match="positive training"):
        discover_runs(tmp_path, tmp_path / "unused.pt")


def test_failure_is_recorded_once_and_existing_output_not_overwritten(tmp_path):
    out = tmp_path / "new-evaluation"
    with pytest.raises(FileNotFoundError):
        evaluate(tmp_path / "absent-data", tmp_path / "absent-runs", tmp_path / "absent.pt", out, device="cpu")
    failure = out / "EVALUATION_FAILED.json"
    assert failure.is_file()
    before = sha256(failure)
    with pytest.raises(FileExistsError):
        evaluate(tmp_path / "absent-data", tmp_path / "absent-runs", tmp_path / "absent.pt", out, device="cpu")
    assert sha256(failure) == before


def test_physical_full_with_partial_validity_is_not_called_full56():
    bundle = SyntheticBundle()
    bundle.arrays["y_valid"][1, 5:] = False
    _, panels = build_panels(bundle, "validation")
    support = _panel_support(panels["physical_full"])
    assert support["actual_frequency_counts"] == [5, 56]
    assert support["rows_requesting_all_56_frequencies"] == 1
    assert "NOT a full-56 guarantee" in support["physical_full_semantics"]


def test_synthetic_end_to_end_artifacts_and_fixed_denominators(tmp_path, monkeypatch):
    """Tiny analytic substitute models exercise all evaluation IO, not checkpoint qualification."""
    import research.broadband56_nn.evaluation as module
    from research.broadband56_nn.io import canonical_sha
    from research.broadband56_nn.models import PACKAGE_MAPPING

    bundle = SyntheticBundle()
    bundle.dim = 10
    bundle.root = tmp_path / "synthetic-data"
    bundle.root.mkdir()
    save_json(bundle.root / "data_manifest.json", {"fixture": "SYNTHETIC_ONLY"})
    bundle.norm.update({"g_min": [0.] * 10, "g_max": [2.] * 10})
    bundle.g_normalize = lambda geometry: geometry - 1
    bundle.denormalize_s = lambda response: response
    contract = {"field_names": [f"synthetic_g{i}" for i in range(10)],
                "lower": [0.] * 10, "upper": [2.] * 10,
                "grid_um": 0.005, "grid_source_sha256": "a" * 64,
                "grid_status": "SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR",
                "port_contract": {"ports": 4, "port_order": ["P001", "P002", "P003", "P004"],
                                  "reference_impedance_ohm": 50.}}
    run_records = {}
    for name in (*FORWARD_IDS, *PACKAGE_IDS):
        path = tmp_path / f"{name}.json"
        save_json(path, {"fixture": name})
        run_records[name] = {"name": name, "checkpoint": {"path": str(path), "sha256": sha256(path)},
                             "data_sha": bundle.data_sha}

    class TinyForward(torch.nn.Module):
        def forward(self, geometry, frequency):
            return torch.zeros((len(geometry), 56, 32), dtype=geometry.dtype, device=geometry.device)

    class TinyInverse(torch.nn.Module):
        def forward(self, tokens, condition):
            return torch.zeros((len(tokens), 10), dtype=tokens.dtype, device=tokens.device)

    def synthetic_load(record, received_bundle, device, role, expected_kind=None):
        name = record["name"]
        meta = {"role": role, "kind": expected_kind or "F2", "step": 1,
                "contract": contract, "contract_sha": canonical_sha(contract),
                "architecture": {"fixture": "tiny synthetic IO test, not BB model"}, "model_sha": name}
        if role == "inverse":
            own = PACKAGE_MAPPING[name][0]
            meta.update({"forward_model_sha": own, "forward_checkpoint": run_records[own]["checkpoint"]["path"]})
        return (TinyForward() if role == "forward" else TinyInverse()), meta

    monkeypatch.setattr(module, "Bundle", lambda _: bundle)
    monkeypatch.setattr(module, "discover_runs", lambda *_: run_records)
    monkeypatch.setattr(module, "_load_model", synthetic_load)
    summary = module.evaluate(bundle.root, tmp_path, run_records["FREF"]["checkpoint"]["path"],
                              tmp_path / "evaluation", device="cpu", micro_batch=2)
    assert summary["status"] == "COMPLETE_PROXY_EVALUATION"
    assert summary["geometry_count"] == 3
    assert len(summary["packages"]) == 6
    assert summary["physical_winner"] == "NOT_ESTABLISHED"
    result = json.loads((tmp_path / "evaluation" / "BB01" / "metrics.json").read_text())
    physical = result["panels"]["physical_full"]
    assert physical["source_label_ineligible_geometry_count"] == 1
    assert physical["common_forward_continuous"]["condition_violation_rate"] == 1.
    assert physical["common_forward_continuous"]["normalized_rmse"] is None
    assert (tmp_path / "evaluation" / "BB06" / "candidates.csv").is_file()
    assert (tmp_path / "evaluation" / "SHA256SUMS.txt").is_file()
    assert result["panel_manifest"]["sha256"] == summary["panel_manifest"]["sha256"]
    assert physical["panel_manifest_sha256"] == summary["panel_manifest"]["sha256"]
    assert physical["grid_um"] == contract["grid_um"]
    assert physical["continuous_feasibility"]["geometry_check_dtype"] == "float64"
    assert physical["memory_observation"]["process_lifetime_peak_rss_bytes"] > 0


def test_sealed_test_rejects_missing_or_stale_protocol_even_with_matching_models(tmp_path):
    bundle, runs = SyntheticBundle(), records()
    base = {"schema": "bb_evaluation_configuration_freeze.v1", "status": "FROZEN",
            "data_sha": bundle.data_sha, "normalizer_sha": bundle.norm_sha,
            "checkpoints": {name: record["checkpoint"]["sha256"] for name, record in runs.items()}}
    for name, changed in (("missing", base), ("stale", {**base, "evaluation_protocol_sha256": "0" * 64})):
        path = tmp_path / f"{name}.json"
        save_json(path, changed)
        with pytest.raises(ValueError, match="evaluation protocol"):
            verify_configuration_freeze(path, runs, bundle, "test")


def test_grid_is_source_contract_bound_and_rounds_exported_values_as_float64():
    with pytest.raises(ValueError, match="source-SHA-verified"):
        _grid_from_contract({"grid_um": .005})
    contract = {"grid_um": .01, "grid_source_sha256": "b" * 64,
                "grid_status": "SOURCE_VERIFIED_EXPORT_ONLY_NO_STRAIGHT_THROUGH_ESTIMATOR"}
    assert _grid_from_contract(contract) == .01  # No silently hardcoded .005.
    coordinates = np.array([[160.0025]], dtype=np.float32)
    gridded = _grid_geometry(coordinates, .005)
    assert gridded.dtype == np.float64
    # The actual float32 coordinate is slightly ABOVE 160.0025. Do not round
    # its quotient in float32 and incorrectly manufacture an exact .5 tie.
    assert float(coordinates[0, 0]) > 160.0025
    assert gridded[0, 0] == pytest.approx(160.005)


def test_official_physical_metrics_use_cpu_float64_even_when_network_device_mps(monkeypatch):
    import research.broadband56_nn.evaluation as module
    bundle = SyntheticBundle()
    seen = []
    def extractor(s, frequency, contract):
        seen.append((s.dtype, s.device.type, frequency.dtype))
        y = s[..., :4].clone()
        return {"y": y, "valid_strict": torch.ones_like(y, dtype=torch.bool)}
    monkeypatch.setattr(module, "extract_physical", extractor)
    raw = np.full((2, 56, 32), 1. + 1e-10, dtype=np.float64)
    values, valid = module._physical_arrays(raw, bundle, {"port_contract": {}}, micro_batch=1, device="mps")
    assert seen == [(torch.float64, "cpu", torch.float64)] * 2
    np.testing.assert_array_equal(values, raw[..., :4])
    assert valid.all()


def test_portable_forward_reference_requires_exact_weights_and_checkpoint_bytes(tmp_path):
    import research.broadband56_nn.evaluation as module
    current = tmp_path / "copied-forward.pt"
    current.write_bytes(b"synthetic checkpoint identity")
    reference = tmp_path / "forward_reference.json"
    save_json(reference, {"sha256": sha256(current), "model_sha": "model-one"})
    metadata = {"forward_model_sha": "model-one", "forward_checkpoint": str(tmp_path / "missing-original.pt")}
    package = {"forward_reference": json.loads(reference.read_text()),
               "forward_reference_evidence": {"path": str(reference), "sha256": sha256(reference)}}
    own = {"checkpoint": {"path": str(current), "sha256": sha256(current)}}
    module._verify_own_forward_binding(metadata, package, own, {"model_sha": "model-one"}, {"model_sha": "independent"})
    with pytest.raises(ValueError, match="unproven"):
        module._verify_own_forward_binding(metadata, {}, own, {"model_sha": "model-one"}, {"model_sha": "independent"})
    with pytest.raises(ValueError, match="not independent"):
        module._verify_own_forward_binding(metadata, package, own, {"model_sha": "model-one"}, {"model_sha": "model-one"})
