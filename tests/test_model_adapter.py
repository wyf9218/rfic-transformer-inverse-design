from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from rfic_transformer_inverse_design.synthesis.frozen_mlp import (
    FrozenTandemMLP, GEOMETRY_COLUMNS, INPUT_COLUMNS,
)
from rfic_transformer_inverse_design.synthesis.model_adapter import (
    describe_model, infer, load_model,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_package(root: Path, *, hard: bool = True) -> tuple[Path, dict]:
    root.mkdir()
    projection = "hard_feasible_topology_v1" if hard else "independent_sigmoid"
    summary = {
        "input_columns": list(INPUT_COLUMNS), "geometry_columns": list(GEOMETRY_COLUMNS),
        "training_count": 100,
        "split_audit": {"row_counts": {"train": 70, "validation": 15, "test": 15}},
        "arguments": {"seed": 7, "q_target_semantics": "minimum", "q_minimum_margin_physical": 2.0},
    }
    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(summary))
    arrays = {
        "forward_weight_0": np.zeros((10, 4)), "forward_bias_0": np.zeros(4),
        "inverse_weight_0": np.zeros((4, 10)),
        "inverse_bias_0": np.asarray([-2, 0, 2, 0, 0, 0, 0, 0, 0, 0]),
        "normalization__x_mean": np.asarray([1.5, 1.6, 12.0, 0.5]),
        "normalization__x_scale": np.ones(4),
        "normalization__y_mean": np.zeros(10),
        "normalization__y_scale": np.ones(10),
        "normalization__geometry_lower": np.asarray([160, 160, 160, 160, 3.05, 20, 20, -40, 100, 100]),
        "normalization__geometry_upper": np.asarray([520, 520, 520, 520, 12, 90, 90, 40, 320, 320]),
    }
    if hard:
        arrays["inverse_geometry_projection__mode"] = np.asarray([projection])
        arrays["inverse_geometry_projection__topology_contract_json"] = np.asarray([json.dumps({
            "available": True,
            "geometry_columns": list(GEOMETRY_COLUMNS),
            "index_by_semantic": {key.removeprefix("geom__"): i for i, key in enumerate(GEOMETRY_COLUMNS)},
            "power_line_port_ground_overlap": {
                "enabled": True, "bar_offset_um": 12.0,
                "shield_opening_clearance_um": 10.0, "training_safety_margin_um": 0.0,
            },
        })])
    weights_path = root / "weights.npz"
    np.savez(weights_path, **arrays)
    contract = {
        "schema": "rfic_frozen_tandem_mlp_public_contract.v1",
        "model_id": "fixture_minimum_q", "model_seed": 7, "target_frequency_ghz": 15.0,
        "input_columns": list(INPUT_COLUMNS), "geometry_columns": list(GEOMETRY_COLUMNS),
        "input_units": ["nH", "nH", "dimensionless", "dimensionless"], "geometry_unit": "um",
        "q_target_semantics": "minimum", "q_training_margin_physical": 2.0,
        "row_counts": {"source": 100, "train": 70, "validation": 15, "test": 15},
        "declared_support_lower": [0.5, 0.5, 5, 0], "declared_support_upper": [3, 3, 25, 0.8],
        "architecture": {"inverse_mlp": [4, 10], "forward_surrogate": [10, 4],
                         "hidden_activation": "gelu", "geometry_projection": projection},
        "artifacts": {
            "summary": {"filename": "summary.json", "sha256": _sha(summary_path)},
            "weights": {"filename": "weights.npz", "sha256": _sha(weights_path)},
        },
    }
    path = root / "model_contract.json"
    path.write_text(json.dumps(contract))
    return path, contract


def test_custom_complete_package_reports_identity_and_keeps_q_input(tmp_path: Path) -> None:
    _fixture_package(tmp_path / "model")
    model = load_model(tmp_path / "model", profile="custom")
    result = infer(model, np.asarray([[1.5, 1.6, 25.0, 0.5]]))
    assert result.geometry.shape == (1, 10)
    np.testing.assert_equal(result.proxy_features[0], [1.5, 1.6, 12, 0.5])
    assert model.contract_path.name == "model_contract.json"
    metadata = describe_model(model)
    assert metadata["q_training_semantics"] == "minimum"
    assert metadata["q_training_margin"] == 2.0
    assert metadata["selection_q_semantics"] == "exact_symmetric"
    assert metadata["row_counts"]["train"] == 70
    assert metadata["geometry_projection"] == "hard_feasible_topology_v1"
    assert not model.arrays["inverse_weights"][0].flags.writeable
    assert str(tmp_path) not in json.dumps(metadata)
    with pytest.raises(ValueError, match="outside"):
        infer(model, np.asarray([[1.5, 1.6, 25.0001, 0.5]]))


def test_hard_decoder_couples_primary_feed_to_secondary_width(tmp_path: Path) -> None:
    _fixture_package(tmp_path / "hard")
    _fixture_package(tmp_path / "independent", hard=False)
    hard = load_model(tmp_path / "hard", profile="custom").predict([1.5, 1.6, 12, 0.5]).geometry[0]
    independent = load_model(tmp_path / "independent", profile="custom").predict([1.5, 1.6, 12, 0.5]).geometry[0]
    required = (hard[2] - hard[0]) / 2 + 22 + hard[4]
    assert hard[8] == pytest.approx(required + 0.5 * (320 - required))
    assert hard[8] > independent[8]
    np.testing.assert_equal(hard[:8], independent[:8])


@pytest.mark.parametrize("change,match", [
    (lambda c: c["architecture"].update(geometry_projection="independent_sigmoid"), "projection"),
    (lambda c: c["architecture"].update(hidden_activation="relu"), "activation"),
    (lambda c: c.update(q_target_semantics="exact"), "Q semantics"),
    (lambda c: c.update(q_training_margin_physical=0), "training margin"),
    (lambda c: c.update(target_frequency_ghz=20), "15 GHz"),
    (lambda c: c.update(geometry_unit="nm"), "geometry units"),
    (lambda c: c["row_counts"].update(train=80), "row counts"),
    (lambda c: c["declared_support_upper"].__setitem__(2, 19), "Q=10..20"),
    (lambda c: c.pop("input_units"), "declare units"),
])
def test_custom_package_rejects_incompatible_metadata(tmp_path: Path, change, match) -> None:
    path, contract = _fixture_package(tmp_path / "model")
    change(contract)
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match=match):
        load_model(path.parent, profile="custom")


def test_wrong_weight_bytes_are_rejected_before_inference(tmp_path: Path) -> None:
    path, _ = _fixture_package(tmp_path / "model")
    with (path.parent / "weights.npz").open("ab") as handle:
        handle.write(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_model(path.parent, profile="custom")


def test_summary_decoder_cannot_disagree_with_npz(tmp_path: Path) -> None:
    path, contract = _fixture_package(tmp_path / "model")
    summary_path = path.parent / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["arguments"]["inverse_geometry_projection"] = "independent_sigmoid"
    summary_path.write_text(json.dumps(summary))
    contract["artifacts"]["summary"]["sha256"] = _sha(summary_path)
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match="summary geometry projection"):
        load_model(path.parent, profile="custom")


def test_legacy_npz_without_decoder_remains_independent_sigmoid(tmp_path: Path) -> None:
    path, contract = _fixture_package(tmp_path / "legacy", hard=False)
    contract["architecture"].pop("geometry_projection")
    contract["architecture"].pop("hidden_activation")
    path.write_text(json.dumps(contract))
    model = FrozenTandemMLP.load(path.parent, contract_path=path)
    assert model.arrays["projection_mode"] == "independent_sigmoid"
    assert model.predict([1.5, 1.6, 12, 0.5]).geometry[0, 8] == 210


def test_deployed100k_matches_exact_frozen_reference_without_retraining() -> None:
    """Opt-in private regression: 15 targets, no fixed10k or physical execution."""
    model_dir = os.environ.get("RFIC_TEST_DEPLOYED100K_MODEL_DIR")
    reference_path = os.environ.get("RFIC_TEST_DEPLOYED100K_REFERENCE_RUNTIME")
    if not model_dir or not reference_path:
        pytest.skip("private deployed100k package and reference runtime not configured")
    reference = Path(reference_path)
    assert _sha(reference) == "d962de6f923234d5e9702556a1df16ff63d84df59c2684f3b46e469f2fe39eb9"
    spec = importlib.util.spec_from_file_location("_deployed100k_frozen_reference", reference)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    baseline = module.FrozenInverseModel.load(model_dir)
    model = load_model(model_dir)
    targets = np.asarray(
        [[1.15, 1.40, float(q), 0.76] for q in range(10, 21)]
        + [[0.5, 0.5, 5.0, 0.01], [3.0, 3.0, 25.0, 0.8],
           [2.9, 0.55, 25.0, 0.05], [0.65, 2.9, 5.0, 0.79]]
    )
    actual = model.predict(targets)
    expected_geometry = []
    expected_proxy = []
    for i, values in enumerate(targets):
        row = baseline.infer(module.PhysicalTarget(f"parity_{i}", *values))
        expected_geometry.append([row.geometry_um[key.removeprefix("geom__")] for key in GEOMETRY_COLUMNS])
        expected_proxy.append([row.proxy_diagnostic[key] for key in ("Lp_nH", "Ls_nH", "Q_proxy", "K_abs_proxy")])
    np.testing.assert_allclose(actual.geometry, expected_geometry, rtol=0, atol=1e-9)
    np.testing.assert_allclose(actual.proxy_features, expected_proxy, rtol=0, atol=1e-10)
    assert describe_model(model)["row_counts"] == {"source": 100000, "train": 78891, "validation": 9740, "test": 11369}
    assert model.model_id == "current_foundry_qmin_response_only_seed20260713"
