from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


FEATURES = ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"]
INPUT_COLUMNS = [f"input__{name}" for name in FEATURES]
LOWER = np.asarray((0.5, 0.5, 5.0, 0.0), dtype=float)
UPPER = np.asarray((3.0, 3.0, 25.0, 0.8), dtype=float)
SPANS = UPPER - LOWER


def _load_script(name: str):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(
    root: Path,
    global_module,
    *,
    row_count: int,
    cell_count: int,
    heteroscedastic: bool,
) -> tuple[Path, Path]:
    rng = np.random.default_rng(20260711)
    coordinates = np.asarray(list(np.ndindex((4, 4, 4, 4)))[:cell_count], dtype=int)
    predictions = root / "predictions.csv"
    rows = []
    for source_index in range(row_count):
        coordinate = coordinates[source_index % len(coordinates)]
        target = LOWER + (coordinate + 0.5) * SPANS / 4.0
        target = target + rng.uniform(-0.08, 0.08, size=4) * SPANS / 4.0
        relative_scale = 0.010
        if heteroscedastic:
            relative_scale = 0.006 + 0.0009 * float(source_index % len(coordinates))
        forward_error = rng.normal(0.0, relative_scale, size=4) * SPANS
        inverse_error = rng.normal(0.0, 1.35 * relative_scale, size=4) * SPANS
        row = {"source_row_index": source_index}
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
                "training_count": row_count,
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


def _run_global(global_module, summary: Path, out_dir: Path, min_rows: int) -> Path:
    assert (
        global_module.main(
            [
                "--tandem-summary",
                str(summary),
                "--out-dir",
                str(out_dir),
                "--min-source-rows",
                str(min_rows),
                "--min-prediction-rows",
                str(min_rows),
                "--min-calibration-rows",
                str(min_rows // 3),
                "--min-evaluation-rows",
                str(min_rows // 3),
            ]
        )
        == 0
    )
    return out_dir / "physical_feature_conformal_calibration_summary.json"


def _comparison_args(
    summary: Path,
    global_summary: Path,
    out_dir: Path,
    *,
    min_rows: int,
    min_cell_rows: int = 30,
) -> list[str]:
    return [
        "--tandem-summary",
        str(summary),
        "--global-summary",
        str(global_summary),
        "--out-dir",
        str(out_dir),
        "--min-source-rows",
        str(min_rows),
        "--min-prediction-rows",
        str(min_rows),
        "--min-calibration-rows",
        str(min_rows // 3),
        "--min-evaluation-rows",
        str(min_rows // 3),
        "--min-cell-calibration-rows",
        str(min_cell_rows),
        "--min-cell-evaluation-rows",
        str(min_cell_rows),
        "--min-supported-cells",
        "8",
        "--min-supported-cell-fraction",
        "0.80",
        "--min-supported-row-fraction",
        "0.80",
    ]


def test_mondrian_comparison_uses_same_split_and_improves_heteroscedastic_cell_tail(tmp_path):
    global_module = _load_script("audit_physical_feature_conformal_calibration.py")
    module = _load_script("compare_global_vs_mondrian_conformal_calibration.py")
    summary, _ = _write_fixture(
        tmp_path, global_module, row_count=8000, cell_count=16, heteroscedastic=True
    )
    global_summary = _run_global(global_module, summary, tmp_path / "global", 5000)
    out_dir = tmp_path / "mondrian"

    assert module.main(_comparison_args(summary, global_summary, out_dir, min_rows=5000)) == 0
    result = json.loads(
        (out_dir / "physical_feature_mondrian_conformal_comparison_summary.json").read_text()
    )
    assert result["overall_status"] == "PASS"
    assert all(result["checks"].values())
    assert result["analysis"]["support"]["supported_evaluation_cell_count"] == 16
    assert result["checks"]["global_split_fingerprint_matches"] is True
    assert (
        result["recommendation"]["aggregate_metrics"]["mean_p10_cell_coverage_improvement"]
        > 0.0
    )
    assert Path(result["artifacts"]["cell_metrics_csv"]).is_file()
    assert Path(result["artifacts"]["figure_png"]).stat().st_size > 1000


def test_mondrian_comparison_rejects_sparse_unsupported_cells(tmp_path):
    global_module = _load_script("audit_physical_feature_conformal_calibration.py")
    module = _load_script("compare_global_vs_mondrian_conformal_calibration.py")
    summary, _ = _write_fixture(
        tmp_path, global_module, row_count=4096, cell_count=256, heteroscedastic=False
    )
    global_summary = _run_global(global_module, summary, tmp_path / "global", 3000)
    out_dir = tmp_path / "mondrian"

    assert module.main(_comparison_args(summary, global_summary, out_dir, min_rows=3000)) == 2
    result = json.loads(
        (out_dir / "physical_feature_mondrian_conformal_comparison_summary.json").read_text()
    )
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["supported_cells_meet_minimum"] is False
    assert result["checks"]["supported_cell_fraction_meets_minimum"] is False
    assert result["decision"] == "DO_NOT_USE_MONDRIAN_COMPARISON"


def test_mondrian_comparison_rejects_tampered_global_split_fingerprint(tmp_path):
    global_module = _load_script("audit_physical_feature_conformal_calibration.py")
    module = _load_script("compare_global_vs_mondrian_conformal_calibration.py")
    summary, _ = _write_fixture(
        tmp_path, global_module, row_count=8000, cell_count=16, heteroscedastic=False
    )
    global_summary = _run_global(global_module, summary, tmp_path / "global", 5000)
    payload = json.loads(global_summary.read_text())
    payload["analysis"]["split_fingerprint_sha256"] = "0" * 64
    global_summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_dir = tmp_path / "mondrian"

    assert module.main(_comparison_args(summary, global_summary, out_dir, min_rows=5000)) == 2
    result = json.loads(
        (out_dir / "physical_feature_mondrian_conformal_comparison_summary.json").read_text()
    )
    assert result["checks"]["global_split_fingerprint_matches"] is False
    assert result["overall_status"] == "FAIL"
