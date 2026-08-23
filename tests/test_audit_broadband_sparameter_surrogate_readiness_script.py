from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_broadband_sparameter_surrogate_readiness.py"
    spec = importlib.util.spec_from_file_location("audit_broadband_sparameter_surrogate_readiness_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_fixture(root: Path, *, nonreciprocal: bool = False) -> Path:
    frequencies = np.asarray([5.0e9, 10.0e9, 15.0e9])
    fields = [
        "evaluation",
        "touchstone_path",
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
        *[f"geom__g{index}" for index in range(10)],
    ]
    csv_path = root / "training.csv"
    rows = []
    for index in range(8):
        path = root / f"sample_{index}.s4p"
        s_matrix = np.zeros((len(frequencies), 4, 4), dtype=np.complex128)
        for freq_index in range(len(frequencies)):
            s_matrix[freq_index] = np.eye(4) * (0.10 + 0.01j * freq_index)
            s_matrix[freq_index, 0, 1] = 0.04 + 0.01j
            s_matrix[freq_index, 1, 0] = 0.09 + 0.01j if nonreciprocal else 0.04 + 0.01j
        _write_touchstone(path, frequencies, s_matrix)
        row = {
            "evaluation": f"sample_{index}",
            "touchstone_path": str(path),
            "input__lp_nh_center": 1.0,
            "input__ls_nh_center": 1.2,
            "input__q_center": 12.0,
            "input__k_abs_center": 0.5,
        }
        row.update({f"geom__g{geom_index}": index + geom_index for geom_index in range(10)})
        rows.append(row)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def test_broadband_readiness_accepts_real_grid_reciprocal_passive_s4p(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-files",
            "4",
            "--max-files",
            "8",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "15",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "3",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "broadband_sparameter_surrogate_readiness_summary.json").read_text()
    )
    assert summary["overall_status"] == "PASS"
    assert summary["sampled_touchstone_pass_count"] == 8
    assert summary["checks"]["ten_geometry_columns_present"] is True
    assert summary["sample_metric_summary"]["max_reciprocity_error"] == 0.0
    assert summary["estimated_full_complex_s_float32_gib"] > 0.0
    assert summary["reciprocal_upper_triangle_contract"]["unique_complex_entries_per_frequency"] == 10
    assert summary["reciprocal_upper_triangle_contract"]["real_channels_per_frequency"] == 20
    assert (
        summary["reciprocal_upper_triangle_contract"]["estimated_float32_gib"]
        < summary["estimated_full_complex_s_float32_gib"]
    )
    assert summary["paper_aligned_adaptation"]["pulserf_unet_role"] == "CONTROLLED_FORWARD_SURROGATE_ABLATION_ONLY"
    assert (
        summary["paper_aligned_adaptation"]["causality_layer_status"]
        == "NOT_A_HARD_GATE_ON_TRUNCATED_5_TO_60_GHZ_DATA"
    )


def test_broadband_readiness_rejects_nonreciprocal_labels(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path, nonreciprocal=True)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-files",
            "4",
            "--max-files",
            "8",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "15",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "3",
            "--max-reciprocity-error",
            "0.01",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "broadband_sparameter_surrogate_readiness_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["all_sampled_touchstones_pass"] is False
