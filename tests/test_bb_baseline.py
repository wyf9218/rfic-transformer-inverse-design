"""Synthetic baseline CLI boundary tests; real replay remains private."""

import csv
import json
import warnings

import numpy as np
import pytest

from research.broadband56_nn.baseline import VALIDATION_NAME, replay_baseline
from research.broadband56_nn.data import sha256
from rfic_transformer_inverse_design.synthesis.frozen_mlp import FrozenTandemMLP, GEOMETRY_COLUMNS, INPUT_COLUMNS, _predict
from rfic_transformer_inverse_design.synthesis import frozen_mlp


def fixture_baseline(tmp_path, change_export=False):
    run = tmp_path / "synthetic"
    run.mkdir()
    rng = np.random.default_rng(17)
    arrays = {"forward_weight_0": rng.normal(0, 0.03, (10, 4)), "forward_bias_0": np.zeros(4), "inverse_weight_0": rng.normal(0, 0.03, (4, 10)), "inverse_bias_0": np.zeros(10)}
    normalization = {"x_mean": [1.5, 1.5, 15, 0.4], "x_scale": [1, 1, 5, 0.2], "y_mean": [100] * 10, "y_scale": [10] * 10, "geometry_lower": [0] * 10, "geometry_upper": [1] * 10}
    arrays.update({f"normalization__{key}": np.asarray(value) for key, value in normalization.items()})
    np.savez(run / "weights.npz", **arrays)
    summary = {"input_columns": list(INPUT_COLUMNS), "geometry_columns": list(GEOMETRY_COLUMNS), "method": {"geometry_output_constraint": "sigmoid_projection_to_observed_training_envelope", "local_refinement": "disabled"}, "evaluation_isolation": {"row_counts": {"train": 5, "validation": 3, "test": 2}}, "model_comparison_contract": {"trainer_implementation_sha256": "a" * 64, "loss": {"family": "synthetic"}}, "training_count": 10}
    (run / "summary.json").write_text(json.dumps(summary))
    contract = {"schema": "rfic_frozen_tandem_mlp_public_contract.v1", "model_id": "SYNTHETIC_ONLY", "model_seed": 17, "target_frequency_ghz": 15, "input_columns": list(INPUT_COLUMNS), "geometry_columns": list(GEOMETRY_COLUMNS), "declared_support_lower": [0.5, 0.5, 5, 0], "declared_support_upper": [3, 3, 25, 0.8], "architecture": {"inverse_mlp": [4, 10], "forward_surrogate": [10, 4]}, "artifacts": {"summary": {"filename": "summary.json", "sha256": sha256(run / "summary.json")}, "weights": {"filename": "weights.npz", "sha256": sha256(run / "weights.npz")}}}
    contract_path = run / "contract.json"
    contract_path.write_text(json.dumps(contract))
    model = FrozenTandemMLP.load(run, contract_path=contract_path)
    targets = np.array([[1, 1, 10, 0.2], [2, 2, 20, 0.6], [1.5, 1.5, 15, 0.4]])
    predicted = model.predict(targets)
    paired = np.arange(30).reshape(3, 10) * 0.05 + 102
    forward = _predict((paired - model.arrays["y_mean"]) / model.arrays["y_scale"], model.arrays["forward_weights"], model.arrays["forward_biases"])
    forward = forward * model.arrays["x_scale"] + model.arrays["x_mean"]
    rows = []
    for i in range(3):
        row = {"validation_index": i, "evaluation_split": "validation", "matrix_index": i, "source_row_index": i, "source_evaluation": f"synthetic_{i}", "source_geometry_identity_sha256": f"{i:064x}"}
        for j, column in enumerate(INPUT_COLUMNS):
            name = column.removeprefix("input__")
            row.update({f"target__{name}": targets[i, j], f"forward__{name}": forward[i, j], f"reconstructed__{name}": predicted.proxy_features[i, j] + (0.1 if change_export else 0)})
        for j, column in enumerate(GEOMETRY_COLUMNS):
            name = column.removeprefix("geom__")
            row.update({f"paired_geometry__{name}": paired[i, j], f"predicted_geometry__{name}": predicted.geometry[i, j]})
        rows.append(row)
    with (run / VALIDATION_NAME).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return run, contract_path


def test_replay_both_paths_and_identity_boundary(tmp_path):
    run, contract = fixture_baseline(tmp_path)
    result = replay_baseline(run, tmp_path / "out", contract_path=contract, expected_validation_sha256=sha256(run / VALIDATION_NAME))
    assert result["status"] == "PASS"
    assert result["validation_rows_replayed"] == 3
    assert all(value == 0 for value in result["numerical_parity"].values())
    assert result["trainer_identity"]["exact_original_trainer_identity_proven"] is False
    assert result["test_set_accessed"] is False
    arrays = np.load(tmp_path / "out/replay_arrays.npz", allow_pickle=False)
    assert arrays["source_geometry_ids"].shape == (3,)


def test_replay_reports_actual_numerical_mismatch(tmp_path):
    run, contract = fixture_baseline(tmp_path, change_export=True)
    result = replay_baseline(run, tmp_path / "out", contract_path=contract, expected_validation_sha256=sha256(run / VALIDATION_NAME))
    assert result["status"] == "FAIL"
    assert result["numerical_parity"]["inverse_reconstruction_max_absolute_difference"] > 0.09


def test_wrong_source_hash_is_fail_closed(tmp_path):
    run, contract = fixture_baseline(tmp_path)
    with pytest.raises(ValueError, match="SHA mismatch"):
        replay_baseline(run, tmp_path / "out", contract_path=contract, expected_validation_sha256="b" * 64)
    receipt = json.loads((tmp_path / "out/REPLAY_RECEIPT.json").read_text())
    assert receipt["status"] == "FAIL"
    assert not (tmp_path / "out/replay_arrays.npz").exists()


def test_no_clobber_and_no_tolerance_relaxation(tmp_path):
    run, contract = fixture_baseline(tmp_path)
    (tmp_path / "existing").mkdir()
    with pytest.raises(FileExistsError):
        replay_baseline(run, tmp_path / "existing", contract_path=contract)
    with pytest.raises(ValueError, match="tolerance"):
        replay_baseline(run, tmp_path / "out", contract_path=contract, absolute_tolerance=0.1)


def test_runtime_warnings_preserved_in_future_receipts(tmp_path, monkeypatch):
    run, contract = fixture_baseline(tmp_path)
    original = frozen_mlp._predict
    def warn_and_predict(*args, **kwargs):
        warnings.warn("synthetic numeric warning", RuntimeWarning)
        return original(*args, **kwargs)
    monkeypatch.setattr(frozen_mlp, "_predict", warn_and_predict)
    result = replay_baseline(run, tmp_path / "out", contract_path=contract, expected_validation_sha256=sha256(run / VALIDATION_NAME))
    assert result["status"] == "PASS"
    assert len(result["runtime_warnings"]) == 3
    assert all(item["message"] == "synthetic numeric warning" for item in result["runtime_warnings"])
