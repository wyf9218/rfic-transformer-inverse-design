from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import math
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "train_broadband_sparameter_pca_surrogate.py"
    spec = importlib.util.spec_from_file_location("train_broadband_sparameter_pca_surrogate_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_fixture(root: Path, rows: int = 120) -> Path:
    rng = np.random.default_rng(20260711)
    frequencies = np.asarray([5.0e9, 10.0e9, 15.0e9, 20.0e9, 25.0e9])
    geometry_columns = [f"geom__g{index}" for index in range(10)]
    fields = [
        "evaluation",
        "touchstone_path",
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__qp_center",
        "input__qs_center",
        "input__k_abs_center",
        *geometry_columns,
    ]
    csv_path = root / "training.csv"
    records = []
    for index in range(rows):
        geometry = rng.uniform(-1.0, 1.0, size=10)
        lp = rng.uniform(0.5, 3.0)
        ls = rng.uniform(0.5, 3.0)
        q = rng.uniform(5.0, 25.0)
        qp = q
        qs = q + rng.uniform(0.0, 3.0)
        k = rng.uniform(0.0, 0.8)
        matrix = np.zeros((len(frequencies), 4, 4), dtype=np.complex128)
        for frequency_index, frequency in enumerate(frequencies):
            phase = frequency / frequencies[-1]
            diagonal = 0.08 + 0.01 * geometry[0] + 1j * (0.02 * phase + 0.005 * geometry[1])
            coupling = 0.025 + 0.004 * geometry[2] + 1j * (0.01 * phase + 0.002 * geometry[3])
            matrix[frequency_index] = np.eye(4) * diagonal
            matrix[frequency_index, 0, 1] = matrix[frequency_index, 1, 0] = coupling
            matrix[frequency_index, 2, 3] = matrix[frequency_index, 3, 2] = coupling * 0.8
        touchstone = root / f"sample_{index:04d}.s4p"
        _write_touchstone(touchstone, frequencies, matrix)
        record = {
            "evaluation": f"sample_{index:04d}",
            "touchstone_path": str(touchstone),
            "input__lp_nh_center": lp,
            "input__ls_nh_center": ls,
            "input__q_center": q,
            "input__qp_center": qp,
            "input__qs_center": qs,
            "input__k_abs_center": k,
        }
        record.update({column: float(geometry[column_index]) for column_index, column in enumerate(geometry_columns)})
        records.append(record)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return csv_path


def test_trains_truthful_broadband_pca_ridge_baseline(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-rows",
            "100",
            "--max-rows",
            "120",
            "--pca-rank",
            "6",
            "--pca-oversample",
            "3",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "25",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "5",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "broadband_sparameter_pca_surrogate_summary.json").read_text())
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["acceptance_thresholds"]["configured"] is False
    assert summary["split_audit"]["physical_cell_overlap_count"] == 0
    metrics = summary["metrics"]
    assert math.isfinite(metrics["test_raw_complex_rmse"])
    assert metrics["target_frequency_requested_ghz"] == 15.0
    assert metrics["target_frequency_used_ghz"] == 15.0
    assert metrics["target_frequency_grid_error_hz"] == 0.0
    assert math.isfinite(metrics["target_test_raw_complex_rmse"])
    assert math.isfinite(metrics["target_test_raw_complex_mae"])
    assert metrics["raw_reciprocity_error"] == 0.0
    assert metrics["projected_max_passivity_excess"] < 1.0e-10
    input_quality = summary["input_s4p_quality"]
    assert input_quality["audit_stage"] == "raw complex S4P before reciprocal symmetrization"
    assert input_quality["reciprocity"]["max_error"] == 0.0
    assert input_quality["passivity"]["max_singular_value_excess"] == 0.0
    assert input_quality["raw_touchstone_content_sha256"] == summary["touchstone_content_sha256"]
    assert summary["reciprocal_training_content_sha256"]
    assert Path(summary["artifacts"]["weights"]).is_file()
    assert Path(summary["artifacts"]["frequency_errors"]).is_file()


def test_raw_nonreciprocal_content_is_detected_before_reciprocal_encoding(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)
    common = [
        "--training-csv",
        str(training_csv),
        "--min-rows",
        "100",
        "--max-rows",
        "120",
        "--pca-rank",
        "6",
        "--pca-oversample",
        "3",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "25",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "5",
    ]
    assert module.main(common + ["--out-dir", str(tmp_path / "before")]) == 0
    before = json.loads(
        (tmp_path / "before" / "broadband_sparameter_pca_surrogate_summary.json").read_text()
    )

    touchstone_path = tmp_path / "sample_0000.s4p"
    touchstone = module.load_touchstone(touchstone_path)
    matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128).copy()
    matrix[:, 0, 1] += 0.08
    matrix[:, 1, 0] -= 0.08
    _write_touchstone(touchstone_path, touchstone.freqs_hz, matrix)

    assert module.main(
        common
        + [
            "--out-dir",
            str(tmp_path / "after"),
            "--max-input-reciprocity-error",
            "0.01",
            "--no-fail-exit",
        ]
    ) == 0
    after = json.loads(
        (tmp_path / "after" / "broadband_sparameter_pca_surrogate_summary.json").read_text()
    )
    assert after["overall_status"] == "FAIL"
    assert after["decision"] == "REJECT_INPUT_S4P_FIX_PORT_OR_SOLVER_CONTRACT"
    assert after["checks"]["input_reciprocity_threshold_pass"] is False
    assert after["input_s4p_quality"]["reciprocity"]["max_error"] > 0.15
    assert after["touchstone_content_sha256"] != before["touchstone_content_sha256"]
    assert (
        after["reciprocal_training_content_sha256"]
        == before["reciprocal_training_content_sha256"]
    )


def test_raw_nonpassive_content_can_hard_fail_before_model_conclusion(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)
    touchstone_path = tmp_path / "sample_0000.s4p"
    touchstone = module.load_touchstone(touchstone_path)
    matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128).copy()
    matrix[:, 0, 0] = 1.3 + 0.0j
    _write_touchstone(touchstone_path, touchstone.freqs_hz, matrix)

    assert module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-rows",
            "100",
            "--max-rows",
            "120",
            "--pca-rank",
            "6",
            "--pca-oversample",
            "3",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "25",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "5",
            "--max-input-passivity-excess",
            "0.01",
            "--no-fail-exit",
        ]
    ) == 0
    summary = json.loads(
        (tmp_path / "out" / "broadband_sparameter_pca_surrogate_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["input_passivity_threshold_pass"] is False
    assert summary["input_s4p_quality"]["passivity"]["max_singular_value_excess"] > 0.25


def test_waits_when_real_broadband_rows_are_insufficient(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path, rows=20)
    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-rows",
            "30",
            "--max-rows",
            "40",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "25",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "5",
            "--no-fail-exit",
        ]
    )
    assert status == 0
    summary = json.loads((tmp_path / "out" / "broadband_sparameter_pca_surrogate_summary.json").read_text())
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_BROADBAND_DATA"


def test_trains_physical_spec_to_spectrum_expander_baseline_on_same_ood_split(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)
    physical_columns = (
        "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"
    )

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "spectral_expander"),
            "--geometry-columns",
            physical_columns,
            "--split-reference-columns",
            physical_columns,
            "--predictor-role",
            "physical_spec",
            "--expected-predictor-count",
            "4",
            "--min-rows",
            "100",
            "--max-rows",
            "120",
            "--pca-rank",
            "6",
            "--pca-oversample",
            "3",
            "--expected-frequency-start-ghz",
            "5",
            "--expected-frequency-stop-ghz",
            "25",
            "--expected-frequency-step-ghz",
            "5",
            "--expected-frequency-points",
            "5",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "spectral_expander" / "broadband_sparameter_pca_surrogate_summary.json").read_text()
    )
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["decision"] == "COMPARE_WITH_NEURAL_SPECTRAL_EXPANDER"
    assert summary["predictor_role"] == "physical_spec"
    assert len(summary["predictor_columns"]) == 4
    assert summary["checks"]["predictor_role_contract"] is True
    assert summary["split_audit"]["physical_cell_overlap_count"] == 0
    assert summary["row_identity_sha256"]
    assert summary["touchstone_content_sha256"]
    assert summary["frequency_grid_sha256"]


def test_rejects_target_frequency_that_is_not_on_grid(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)

    with pytest.raises(SystemExit):
        module.main(
            [
                "--training-csv",
                str(training_csv),
                "--out-dir",
                str(tmp_path / "out"),
                "--min-rows",
                "100",
                "--max-rows",
                "120",
                "--expected-frequency-start-ghz",
                "5",
                "--expected-frequency-stop-ghz",
                "25",
                "--expected-frequency-step-ghz",
                "5",
                "--expected-frequency-points",
                "5",
                "--target-frequency-ghz",
                "16",
            ]
        )


def test_qp_qs_physical_spec_uses_same_rows_and_ood_split_as_qmin(tmp_path):
    module = _load_module()
    training_csv = _write_training_fixture(tmp_path)
    split_columns = "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center"
    qmin_columns = split_columns
    qp_qs_columns = (
        "input__lp_nh_center,input__ls_nh_center,input__qp_center,"
        "input__qs_center,input__k_abs_center"
    )

    common = [
        "--training-csv",
        str(training_csv),
        "--split-reference-columns",
        split_columns,
        "--predictor-role",
        "physical_spec",
        "--min-rows",
        "100",
        "--max-rows",
        "120",
        "--pca-rank",
        "6",
        "--pca-oversample",
        "3",
        "--expected-frequency-start-ghz",
        "5",
        "--expected-frequency-stop-ghz",
        "25",
        "--expected-frequency-step-ghz",
        "5",
        "--expected-frequency-points",
        "5",
    ]
    assert module.main(
        common
        + [
            "--out-dir",
            str(tmp_path / "qmin"),
            "--geometry-columns",
            qmin_columns,
            "--expected-predictor-count",
            "4",
        ]
    ) == 0
    assert module.main(
        common
        + [
            "--out-dir",
            str(tmp_path / "qp_qs"),
            "--geometry-columns",
            qp_qs_columns,
            "--expected-predictor-count",
            "5",
        ]
    ) == 0

    qmin = json.loads((tmp_path / "qmin" / "broadband_sparameter_pca_surrogate_summary.json").read_text())
    qp_qs = json.loads((tmp_path / "qp_qs" / "broadband_sparameter_pca_surrogate_summary.json").read_text())
    assert qp_qs["checks"]["predictor_role_contract"] is True
    assert len(qp_qs["predictor_columns"]) == 5
    assert qmin["row_identity_sha256"] == qp_qs["row_identity_sha256"]
    assert qmin["touchstone_content_sha256"] == qp_qs["touchstone_content_sha256"]
    assert qmin["frequency_grid_sha256"] == qp_qs["frequency_grid_sha256"]
    assert qmin["split_audit"]["split_fingerprint_sha256"] == qp_qs["split_audit"]["split_fingerprint_sha256"]
