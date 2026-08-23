from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

from matplotlib import image as mpl_image


FEATURES = ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"]
INPUT_COLUMNS = [f"input__{name}" for name in FEATURES]
RANGES = np.asarray(((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8)))


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_conformal_calibration.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_conformal_calibration_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, module, *, shifted_evaluation: bool) -> tuple[Path, Path]:
    rng = np.random.default_rng(89)
    predictions = root / "predictions.csv"
    rows = []
    spans = RANGES[:, 1] - RANGES[:, 0]
    for source_index in range(8000):
        target = rng.uniform(RANGES[:, 0], RANGES[:, 1])
        calibration = module._calibration_member(source_index, 20260711, 0.5)
        if shifted_evaluation and not calibration:
            forward_error = rng.normal(0.0, 0.25, size=4) * spans
            inverse_error = rng.normal(0.0, 0.35, size=4) * spans
        else:
            forward_error = rng.normal(0.0, 0.015, size=4) * spans
            inverse_error = rng.normal(0.0, 0.025, size=4) * spans
        row = {"source_row_index": source_index, "matrix_index": source_index, "test_index": source_index}
        for feature_index, feature in enumerate(FEATURES):
            row[f"target__{feature}"] = target[feature_index]
            row[f"forward__{feature}"] = target[feature_index] + forward_error[feature_index]
            row[f"reconstructed__{feature}"] = target[feature_index] + inverse_error[feature_index]
        rows.append(row)
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = root / "tandem_summary.json"
    summary.write_text(
        json.dumps(
            {
                "overall_status": "COMPLETE_REVIEW_REQUIRED",
                "training_count": 6000,
                "input_columns": INPUT_COLUMNS,
                "test_predictions_csv": str(predictions),
                "split_audit": {"split_mode": "physical_cell_grouped", "physical_cell_overlap_count": 0},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary, predictions


def _args(summary: Path, out_dir: Path) -> list[str]:
    return [
        "--tandem-summary",
        str(summary),
        "--out-dir",
        str(out_dir),
        "--min-source-rows",
        "5000",
        "--min-prediction-rows",
        "5000",
        "--min-calibration-rows",
        "2000",
        "--min-evaluation-rows",
        "2000",
    ]


def test_conformal_audit_measures_independent_ood_coverage(tmp_path):
    module = _load_module()
    summary_path, _ = _write_fixture(tmp_path, module, shifted_evaluation=False)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary_path, out_dir)) == 0
    summary = json.loads((out_dir / "physical_feature_conformal_calibration_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["analysis"]["methods"]["forward_proxy"]["all_coverages_pass"] is True
    assert summary["analysis"]["methods"]["tandem_inverse"]["all_coverages_pass"] is True
    assert summary["analysis"]["calibration_count"] >= 2000
    assert summary["analysis"]["evaluation_count"] >= 2000
    figure = out_dir / "physical_feature_conformal_calibration.png"
    pixels = mpl_image.imread(figure)
    assert float(np.mean(pixels[0, 0, :3])) > 0.95


def test_conformal_audit_rejects_distribution_shifted_undercoverage(tmp_path):
    module = _load_module()
    summary_path, _ = _write_fixture(tmp_path, module, shifted_evaluation=True)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary_path, out_dir)) == 2
    summary = json.loads((out_dir / "physical_feature_conformal_calibration_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["all_empirical_coverages_pass"] is False
    assert summary["decision"] == "DO_NOT_USE_CONFORMAL_INTERVALS"
