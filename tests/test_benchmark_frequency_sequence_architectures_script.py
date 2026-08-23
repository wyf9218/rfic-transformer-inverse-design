from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import math
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_frequency_sequence_architectures.py"
    spec = importlib.util.spec_from_file_location("benchmark_frequency_sequence_architectures_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(root: Path, rows: int = 72) -> tuple[Path, Path]:
    rng = np.random.default_rng(20260712)
    frequencies = np.asarray([5.0e9, 10.0e9, 15.0e9, 20.0e9, 25.0e9])
    geometry_columns = [f"geom__g{index}" for index in range(10)]
    fields = [
        "evaluation",
        "touchstone_path",
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
        *geometry_columns,
    ]
    records = []
    for index in range(rows):
        geometry = rng.uniform(-1.0, 1.0, size=10)
        matrix = np.zeros((len(frequencies), 4, 4), dtype=np.complex128)
        for frequency_index, frequency in enumerate(frequencies):
            phase = frequency / frequencies[-1]
            diagonal = 0.10 + 0.01 * geometry[0] + 1j * (0.025 * phase + 0.005 * geometry[1])
            coupling = 0.035 + 0.006 * geometry[2] + 1j * (0.012 * phase + 0.004 * geometry[3])
            matrix[frequency_index] = np.eye(4) * diagonal
            matrix[frequency_index, 0, 1] = matrix[frequency_index, 1, 0] = coupling
            matrix[frequency_index, 2, 3] = matrix[frequency_index, 3, 2] = 0.8 * coupling
            matrix[frequency_index, 0, 2] = matrix[frequency_index, 2, 0] = 0.35 * coupling
            matrix[frequency_index, 1, 3] = matrix[frequency_index, 3, 1] = 0.35 * coupling
        touchstone = root / f"sample_{index:04d}.s4p"
        _write_touchstone(touchstone, frequencies, matrix)
        record = {
            "evaluation": f"sample_{index:04d}",
            "touchstone_path": str(touchstone),
            "input__lp_nh_center": rng.uniform(0.5, 3.0),
            "input__ls_nh_center": rng.uniform(0.5, 3.0),
            "input__q_center": rng.uniform(5.0, 25.0),
            "input__k_abs_center": rng.uniform(0.0, 0.8),
        }
        record.update({column: float(geometry[position]) for position, column in enumerate(geometry_columns)})
        records.append(record)
    csv_path = root / "training.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps({"overall_status": "PASS", "training_count": rows, "training_csv": str(csv_path)}) + "\n",
        encoding="utf-8",
    )
    return csv_path, manifest_path


def _arguments(csv_path: Path, manifest_path: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(csv_path),
        "--training-manifest",
        str(manifest_path),
        "--out-dir",
        str(out_dir),
        "--min-rows",
        "60",
        "--max-rows",
        "72",
        "--epochs",
        "2",
        "--batch-size",
        "16",
        "--gru-hidden-width",
        "6",
        "--model-seeds",
        "31",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "25",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "5",
    ]


def test_runs_equal_budget_pointwise_mlp_vs_gru_on_same_real_rows(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_arguments(csv_path, manifest_path, out_dir)) == 0
    summary = json.loads((out_dir / "frequency_sequence_architecture_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["checks"]["physical_cell_overlap_zero"] is True
    assert summary["checks"]["equal_optimizer_updates_per_seed"] is True
    assert summary["checks"]["parameter_budget_ratio_within_limit"] is True
    assert summary["checks"]["physical_metric_extraction_available"] is True
    assert summary["equal_budget_contract"]["test_set_used_for_selection"] is False
    assert summary["architecture"]["parameter_count_ratio"] <= 1.10
    assert math.isfinite(summary["comparison"]["test_full_band_relative_improvement"])
    assert math.isfinite(summary["comparison"]["test_resonance_relative_improvement"])
    assert (out_dir / "frequency_sequence_frequency_errors.csv").is_file()
    assert (out_dir / "frequency_sequence_frequency_errors.png").is_file()
    assert (out_dir / "frequency_sequence_weights.npz").is_file()


def test_waits_for_minimum_real_s4p_rows(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path, rows=20)
    out_dir = tmp_path / "out"
    arguments = _arguments(csv_path, manifest_path, out_dir)
    arguments[arguments.index("60")] = "30"
    arguments[arguments.index("72")] = "40"
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "frequency_sequence_architecture_summary.json").read_text())
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_BROADBAND_DATA"


def test_numpy_gru_backprop_matches_finite_difference():
    module = _load_module()
    rng = np.random.default_rng(9)
    model = module._init_gru(3, 2, 2, rng)
    x = rng.normal(size=(2, 3, 3))
    y = rng.normal(size=(2, 3, 2))
    gradients = module._gru_gradients(x, y, model, 0.0, 1.0e9)

    def loss() -> float:
        prediction, _ = module._gru_forward(model, x, return_cache=False)
        return float(np.sum((prediction - y) ** 2) / (x.shape[0] * x.shape[1]))

    epsilon = 1.0e-6
    probes = [("Wz", (0, 0)), ("Ur", (1, 0)), ("Wh", (2, 1)), ("Wo", (0, 1)), ("bo", (1,))]
    for name, index in probes:
        original = float(model[name][index])
        model[name][index] = original + epsilon
        positive = loss()
        model[name][index] = original - epsilon
        negative = loss()
        model[name][index] = original
        numerical = (positive - negative) / (2.0 * epsilon)
        assert gradients[name][index] == pytest.approx(numerical, rel=2.0e-4, abs=2.0e-5)


def test_adoption_gate_requires_physical_improvement_and_absolute_quality():
    module = _load_module()
    args = type(
        "Args",
        (),
        {
            "minimum_material_improvement": 0.03,
            "max_frequency_regression_fraction": 0.20,
            "max_passivity_correction_increase": 0.01,
            "max_candidate_test_complex_rmse": 0.05,
            "max_candidate_raw_passivity_excess": 0.05,
        },
    )()
    comparison = {
        "validation_full_band_relative_improvement": 0.10,
        "validation_target_relative_improvement": 0.10,
        "test_full_band_relative_improvement": 0.10,
        "test_target_relative_improvement": 0.10,
        "test_resonance_relative_improvement": 0.10,
        "test_target_lp_nh_mae_relative_improvement": 0.10,
        "test_target_ls_nh_mae_relative_improvement": -0.01,
        "test_target_q_mae_relative_improvement": -0.02,
        "test_target_k_abs_mae_relative_improvement": -0.10,
        "test_frequency_regression_fraction": 0.0,
        "test_passivity_projection_correction_increase": -0.01,
        "candidate_test_raw_complex_rmse": 0.02,
        "candidate_test_raw_max_passivity_excess": 0.01,
    }
    assert module._decision(comparison, args) == "RETAIN_POINTWISE_MLP_MIXED_SEQUENCE_EVIDENCE"

    comparison.update(
        {
            "test_target_ls_nh_mae_relative_improvement": 0.10,
            "test_target_q_mae_relative_improvement": 0.10,
            "test_target_k_abs_mae_relative_improvement": 0.10,
            "candidate_test_raw_complex_rmse": 0.20,
        }
    )
    assert module._decision(comparison, args) == "RETAIN_POINTWISE_MLP_MIXED_SEQUENCE_EVIDENCE"

    comparison["candidate_test_raw_complex_rmse"] = 0.02
    assert module._decision(comparison, args) == "REVIEW_GRU_FOR_FROZEN_FORWARD_INVERSE_ABLATION"
