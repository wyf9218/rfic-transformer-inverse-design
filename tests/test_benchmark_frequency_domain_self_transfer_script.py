from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import math
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_frequency_domain_self_transfer.py"
    spec = importlib.util.spec_from_file_location("benchmark_frequency_domain_self_transfer_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(
    root: Path,
    rows: int = 90,
    *,
    nonreciprocal: bool = False,
) -> tuple[Path, Path]:
    rng = np.random.default_rng(20260711)
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
    csv_path = root / "training.csv"
    records = []
    for index in range(rows):
        geometry = rng.uniform(-1.0, 1.0, size=10)
        physical = [
            rng.uniform(0.5, 3.0),
            rng.uniform(0.5, 3.0),
            rng.uniform(5.0, 25.0),
            rng.uniform(0.0, 0.8),
        ]
        matrix = np.zeros((len(frequencies), 4, 4), dtype=np.complex128)
        for frequency_index, frequency in enumerate(frequencies):
            normalized_frequency = frequency / frequencies[-1]
            diagonal = 0.08 + 0.008 * geometry[0] + 1j * (
                0.015 * normalized_frequency + 0.004 * geometry[1]
            )
            coupling = 0.03 + 0.005 * geometry[2] + 1j * (
                0.008 * normalized_frequency + 0.003 * geometry[3]
            )
            matrix[frequency_index] = np.eye(4) * diagonal
            matrix[frequency_index, 0, 1] = matrix[frequency_index, 1, 0] = coupling
            matrix[frequency_index, 2, 3] = matrix[frequency_index, 3, 2] = 0.8 * coupling
            if nonreciprocal:
                matrix[frequency_index, 1, 0] = coupling + 0.08
        touchstone = root / f"sample_{index:04d}.s4p"
        _write_touchstone(touchstone, frequencies, matrix)
        record = {
            "evaluation": f"sample_{index:04d}",
            "touchstone_path": str(touchstone),
            "input__lp_nh_center": physical[0],
            "input__ls_nh_center": physical[1],
            "input__q_center": physical[2],
            "input__k_abs_center": physical[3],
        }
        record.update({column: float(geometry[position]) for position, column in enumerate(geometry_columns)})
        records.append(record)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "training_count": rows,
                "training_csv": str(csv_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return csv_path, manifest_path


def _wideband_fixture(root: Path, rows: int = 90) -> tuple[Path, Path]:
    rng = np.random.default_rng(31)
    frequencies = np.linspace(5.0e9, 60.0e9, 111)
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
        matrix = np.zeros((111, 4, 4), dtype=np.complex128)
        normalized_frequency = frequencies / frequencies[-1]
        for frequency_index, phase in enumerate(normalized_frequency):
            diagonal = 0.07 + 0.006 * geometry[0] + 1j * (0.02 * phase + 0.003 * geometry[1])
            coupling = 0.025 + 0.004 * geometry[2] + 1j * (0.009 * phase + 0.002 * geometry[3])
            matrix[frequency_index] = np.eye(4) * diagonal
            matrix[frequency_index, 0, 1] = matrix[frequency_index, 1, 0] = coupling
            matrix[frequency_index, 2, 3] = matrix[frequency_index, 3, 2] = 0.75 * coupling
        touchstone = root / f"wide_{index:04d}.s4p"
        _write_touchstone(touchstone, frequencies, matrix)
        record = {
            "evaluation": f"wide_{index:04d}",
            "touchstone_path": str(touchstone),
            "input__lp_nh_center": rng.uniform(0.5, 3.0),
            "input__ls_nh_center": rng.uniform(0.5, 3.0),
            "input__q_center": rng.uniform(5.0, 25.0),
            "input__k_abs_center": rng.uniform(0.0, 0.8),
        }
        record.update({column: float(geometry[position]) for position, column in enumerate(geometry_columns)})
        records.append(record)
    csv_path = root / "wide_training.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    manifest_path = root / "wide_manifest.json"
    manifest_path.write_text(
        json.dumps({"overall_status": "PASS", "training_count": rows, "training_csv": str(csv_path)}) + "\n",
        encoding="utf-8",
    )
    return csv_path, manifest_path


def _args(csv_path: Path, manifest_path: Path, out_dir: Path) -> list[str]:
    return [
        "--training-csv",
        str(csv_path),
        "--training-manifest",
        str(manifest_path),
        "--out-dir",
        str(out_dir),
        "--min-rows",
        "80",
        "--max-rows",
        "90",
        "--band-count",
        "5",
        "--transfer-iterations",
        "1",
        "--epochs-per-session",
        "1",
        "--hidden-depth",
        "1",
        "--hidden-width",
        "16",
        "--batch-size",
        "64",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "25",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "5",
    ]


def test_runs_equal_budget_self_transfer_ablation_on_same_ood_rows(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_args(csv_path, manifest_path, out_dir)) == 0
    summary = json.loads((out_dir / "frequency_self_transfer_benchmark_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["checks"]["physical_cell_overlap_zero"] is True
    assert summary["touchstone_content_sha256"]
    assert summary["checks"]["all_frequency_points_assigned_once"] is True
    assert summary["checks"]["frequency_bands_are_monotonic_contiguous"] is True
    assert summary["checks"]["frequency_band_boundaries_are_contiguous"] is True
    assert summary["checks"]["same_initial_weights_per_band"] is True
    assert summary["checks"]["raw_input_reciprocity_threshold_pass"] is True
    assert summary["checks"]["raw_input_passivity_threshold_pass"] is True
    assert summary["checks"]["physical_metric_extraction_available"] is True
    assert summary["equal_budget_contract"]["equal_updates_per_band"] is True
    assert summary["equal_budget_contract"]["test_set_used_for_training"] is False
    assert summary["equal_budget_contract"]["test_set_used_for_checkpoint_selection"] is False
    assert summary["equal_budget_contract"]["only_arm_difference"].startswith("neighboring-band")
    comparison = summary["comparison"]
    assert math.isfinite(comparison["test_full_band_relative_improvement"])
    assert math.isfinite(comparison["test_target_relative_improvement"])
    assert (out_dir / "frequency_self_transfer_frequency_errors.csv").is_file()
    assert (out_dir / "frequency_self_transfer_frequency_errors.png").is_file()
    assert (out_dir / "frequency_self_transfer_weights.npz").is_file()


def test_waits_for_minimum_touchstone_rows(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path, rows=20)
    out_dir = tmp_path / "out"
    arguments = _args(csv_path, manifest_path, out_dir)
    arguments[arguments.index("80")] = "30"
    arguments[arguments.index("90")] = "40"
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "frequency_self_transfer_benchmark_summary.json").read_text())
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_BROADBAND_DATA"


def test_mixed_validation_and_test_evidence_retains_independent_baseline():
    module = _load_module()
    args = type(
        "Args",
        (),
        {
            "minimum_material_improvement": 0.05,
            "minimum_physical_improvement": 0.0,
            "max_frequency_regression_fraction": 0.20,
            "max_passivity_correction_increase": 0.01,
            "max_candidate_test_complex_rmse": 0.05,
            "max_candidate_raw_passivity_excess": 0.05,
        },
    )()
    comparison = {
        "validation_full_band_relative_improvement": 0.10,
        "validation_target_relative_improvement": 0.10,
        "test_full_band_relative_improvement": 0.08,
        "test_target_relative_improvement": -0.10,
        "test_frequency_regression_fraction": 0.10,
        "test_passivity_projection_correction_increase": 0.0,
        "candidate_test_raw_complex_rmse": 0.02,
        "candidate_test_raw_max_passivity_excess": 0.01,
        "candidate_test_target_physical_valid_fraction": 1.0,
    }
    for split_name in ("validation", "test"):
        for name in ("lp_nh", "ls_nh", "q", "k_abs"):
            comparison[f"{split_name}_target_{name}_mae_relative_improvement"] = 0.10
    assert module._decision(comparison, args) == (
        "RETAIN_INDEPENDENT_BAND_BASELINE_MIXED_SELF_TRANSFER_EVIDENCE"
    )


def test_111_point_grid_is_partitioned_once_across_ten_unequal_bands(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _wideband_fixture(tmp_path)
    out_dir = tmp_path / "wide_out"
    arguments = [
        "--training-csv",
        str(csv_path),
        "--training-manifest",
        str(manifest_path),
        "--out-dir",
        str(out_dir),
        "--min-rows",
        "80",
        "--max-rows",
        "90",
        "--band-count",
        "10",
        "--transfer-iterations",
        "1",
        "--epochs-per-session",
        "1",
        "--hidden-depth",
        "1",
        "--hidden-width",
        "8",
        "--batch-size",
        "512",
    ]

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "frequency_self_transfer_benchmark_summary.json").read_text())
    bands = summary["frequency_bands"]
    indices = [value for band in bands for value in band["frequency_indices"]]
    assert len(bands) == 10
    assert sorted(indices) == list(range(111))
    assert {band["point_count"] for band in bands} == {11, 12}
    assert summary["equal_budget_contract"]["equal_updates_per_band"] is True
    assert summary["training_manifest_sha256"]


def test_frequency_band_audit_rejects_shuffled_or_overlapping_partitions():
    module = _load_module()
    shuffled = [np.asarray([0, 2]), np.asarray([1, 3, 4])]
    audit = module._audit_frequency_bands(shuffled, 5, 2)
    assert audit["monotonic_contiguous"] is False
    assert audit["boundaries_contiguous"] is False
    assert audit["all_frequency_points_assigned_once_in_order"] is False

    overlapping = [np.asarray([0, 1, 2]), np.asarray([2, 3, 4])]
    audit = module._audit_frequency_bands(overlapping, 5, 2)
    assert audit["boundaries_contiguous"] is False
    assert audit["all_frequency_points_assigned_once_in_order"] is False


def test_equal_update_contract_rejects_zero_or_unequal_budgets():
    module = _load_module()
    assert module._equal_optimizer_updates(np.asarray([4, 4]), np.asarray([4, 4])) is True
    assert module._equal_optimizer_updates(np.asarray([4, 4]), np.asarray([4, 3])) is False
    assert module._equal_optimizer_updates(np.asarray([0, 0]), np.asarray([0, 0])) is False


def test_self_transfer_adoption_rejects_physical_k_regression():
    module = _load_module()
    args = type(
        "Args",
        (),
        {
            "minimum_material_improvement": 0.05,
            "minimum_physical_improvement": 0.0,
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
        "test_frequency_regression_fraction": 0.0,
        "test_passivity_projection_correction_increase": 0.0,
        "candidate_test_raw_complex_rmse": 0.02,
        "candidate_test_raw_max_passivity_excess": 0.01,
        "candidate_test_target_physical_valid_fraction": 1.0,
    }
    for split_name in ("validation", "test"):
        for name in ("lp_nh", "ls_nh", "q", "k_abs"):
            comparison[f"{split_name}_target_{name}_mae_relative_improvement"] = 0.10
    comparison["test_target_k_abs_mae_relative_improvement"] = -0.01
    assert module._decision(comparison, args) == (
        "RETAIN_INDEPENDENT_BAND_BASELINE_MIXED_SELF_TRANSFER_EVIDENCE"
    )


def test_raw_nonreciprocal_s4p_fails_before_reciprocal_encoding(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path, nonreciprocal=True)
    out_dir = tmp_path / "nonreciprocal"
    arguments = _args(csv_path, manifest_path, out_dir)
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "frequency_self_transfer_benchmark_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["raw_input_reciprocity_threshold_pass"] is False
    assert summary["input_s4p_quality"]["audit_stage"].endswith(
        "before reciprocal symmetrization"
    )
