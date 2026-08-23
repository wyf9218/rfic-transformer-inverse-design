from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import math
import sys


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "benchmark_cross_frequency_resolution.py"
    )
    spec = importlib.util.spec_from_file_location(
        "benchmark_cross_frequency_resolution_script",
        script_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(
    root: Path,
    rows: int = 96,
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
        normalized_frequency = frequencies / frequencies[-1]
        for frequency_index, phase in enumerate(normalized_frequency):
            resonance = 0.004 * math.sin(math.pi * phase)
            diagonal = 0.08 + 0.008 * geometry[0] + resonance + 1j * (
                0.015 * phase + 0.004 * geometry[1]
            )
            coupling = 0.03 + 0.005 * geometry[2] + 1j * (
                0.008 * phase + 0.003 * geometry[3]
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
        record.update(
            {
                column: float(geometry[position])
                for position, column in enumerate(geometry_columns)
            }
        )
        records.append(record)
    csv_path = root / "training.csv"
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
        "96",
        "--sparse-frequency-stride",
        "2",
        "--optimizer-updates",
        "12",
        "--hidden-depth",
        "1",
        "--hidden-width",
        "12",
        "--batch-size",
        "32",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "25",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "5",
    ]


def test_equal_update_cross_resolution_ablation_uses_unseen_frequency_holdout(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_args(csv_path, manifest_path, out_dir)) == 0
    summary = json.loads((out_dir / "cross_frequency_resolution_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["checks"]["physical_cell_overlap_zero"] is True
    assert summary["checks"]["frequency_partition_complete"] is True
    assert summary["checks"]["frequency_partition_disjoint"] is True
    assert summary["checks"]["sparse_training_excludes_all_held_out_frequencies"] is True
    assert summary["checks"]["dense_training_uses_at_least_one_held_out_frequency"] is True
    assert summary["checks"]["equal_optimizer_updates"] is True
    assert summary["checks"]["raw_input_reciprocity_threshold_pass"] is True
    assert summary["checks"]["raw_input_passivity_threshold_pass"] is True
    assert summary["input_s4p_quality"]["audit_stage"].startswith("raw complex S4P")
    assert summary["frequency_partition"]["observed_indices"] == [0, 2, 4]
    assert summary["frequency_partition"]["held_out_indices"] == [1, 3]
    contract = summary["equal_budget_contract"]
    assert contract["same_initial_weights"] is True
    assert contract["dense_optimizer_updates"] == 12
    assert contract["sparse_optimizer_updates"] == 12
    assert contract["equal_optimizer_updates"] is True
    assert contract["same_physical_row_schedule"] is True
    assert contract["shared_output_normalization_source"].endswith("observed frequencies only")
    assert summary["architecture"]["is_neural_operator"] is False
    assert "not proof of a neural operator" in summary["scientific_boundary"]
    assert math.isfinite(summary["comparison"]["test_held_out_relative_degradation"])
    assert (out_dir / "cross_frequency_resolution_frequency_errors.csv").is_file()
    assert (out_dir / "cross_frequency_resolution_frequency_errors.png").is_file()
    assert (out_dir / "cross_frequency_resolution_weights.npz").is_file()


def test_waits_for_minimum_real_touchstone_rows(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path, rows=20)
    out_dir = tmp_path / "out"
    arguments = _args(csv_path, manifest_path, out_dir)
    arguments[arguments.index("80")] = "30"
    arguments[arguments.index("96")] = "40"
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "cross_frequency_resolution_summary.json").read_text())
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_BROADBAND_DATA"
    assert summary["decision"] == "WAIT_FOR_REAL_S4P_ROWS"


def test_frequency_partition_keeps_last_endpoint_and_never_overlaps():
    module = _load_module()
    observed, held_out = module._frequency_partition(6, 4)
    assert observed.tolist() == [0, 4, 5]
    assert held_out.tolist() == [1, 2, 3]
    assert set(observed).isdisjoint(set(held_out))
    assert sorted([*observed, *held_out]) == list(range(6))


def test_raw_nonreciprocal_touchstones_fail_before_model_symmetrization(tmp_path):
    module = _load_module()
    csv_path, manifest_path = _fixture(tmp_path, nonreciprocal=True)
    out_dir = tmp_path / "out"
    arguments = _args(csv_path, manifest_path, out_dir)
    arguments.append("--no-fail-exit")

    assert module.main(arguments) == 0
    summary = json.loads((out_dir / "cross_frequency_resolution_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["raw_input_reciprocity_threshold_pass"] is False
    assert summary["input_s4p_quality"]["reciprocity"]["max_error"] > 0.02
    assert "before reciprocal symmetrization" in summary["input_s4p_quality"]["audit_stage"]


def test_sparse_grid_adoption_decision_requires_validation_and_test_holdout_limits():
    module = _load_module()
    args = type(
        "Args",
        (),
        {
            "maximum_held_out_relative_degradation": 0.15,
            "max_held_out_regression_fraction": 0.25,
        },
    )()
    favorable = {
        "validation_held_out_relative_degradation": 0.10,
        "test_held_out_relative_degradation": 0.12,
        "test_held_out_frequency_regression_fraction": 0.20,
    }
    assert module._decision(favorable, args).startswith("REVIEW_SPARSE_FREQUENCY_GRID")
    unfavorable = dict(favorable)
    unfavorable["test_held_out_relative_degradation"] = 0.30
    assert module._decision(unfavorable, args) == "RETAIN_FULL_0P5_GHZ_GRID_FOR_PROXY_AND_EMX"
