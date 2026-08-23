from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

from matplotlib import image as mpl_image


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
INPUT_COLUMNS = tuple(f"input__{name}" for name in FEATURES)
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)
RANGES = np.asarray(((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8)), dtype=float)


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_physical_feature_boundary_ood_stress.py"
    spec = importlib.util.spec_from_file_location("audit_physical_feature_boundary_ood_stress_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, row_count: int = 6000, interior_count: int = 3000) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(113)
    spans = RANGES[:, 1] - RANGES[:, 0]
    predictions = root / "predictions.csv"
    rows = []
    for source_index in range(row_count):
        if source_index < interior_count:
            unit = rng.uniform(0.25, 0.75, size=4)
            inverse_std = 0.015
        else:
            unit = rng.uniform(0.15, 0.85, size=4)
            feature_index = source_index % 4
            unit[feature_index] = 0.03 if source_index % 2 == 0 else 0.97
            inverse_std = 0.045
        target = RANGES[:, 0] + unit * spans
        forward = target + rng.normal(0.0, 0.010, size=4) * spans
        reconstructed = target + rng.normal(0.0, inverse_std, size=4) * spans
        row = {"source_row_index": source_index}
        for feature_index, feature in enumerate(FEATURES):
            row[f"target__{feature}"] = target[feature_index]
            row[f"forward__{feature}"] = forward[feature_index]
            row[f"reconstructed__{feature}"] = reconstructed[feature_index]
        rows.append(row)
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    forward_weight = np.zeros((10, 4), dtype=float)
    forward_weight[:4, :4] = np.eye(4)
    inverse_weight = np.zeros((4, 10), dtype=float)
    inverse_weight[:4, :4] = np.eye(4) * 0.5
    weights = root / "weights.npz"
    np.savez_compressed(
        weights,
        forward_weight_0=forward_weight,
        forward_bias_0=np.zeros(4),
        inverse_weight_0=inverse_weight,
        inverse_bias_0=np.zeros(10),
        normalization__x_mean=RANGES[:, 0],
        normalization__x_scale=spans,
        normalization__y_mean=np.zeros(10),
        normalization__y_scale=np.ones(10),
        normalization__geometry_lower=np.zeros(10),
        normalization__geometry_upper=np.ones(10),
    )
    summary = root / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "overall_status": "COMPLETE_REVIEW_REQUIRED",
                "training_count": 9000,
                "input_columns": list(INPUT_COLUMNS),
                "geometry_columns": list(GEOMETRY_COLUMNS),
                "test_predictions_csv": str(predictions),
                "weights_npz": str(weights),
                "split_audit": {"split_mode": "physical_cell_grouped", "physical_cell_overlap_count": 0},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary, predictions, weights


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
        "--min-boundary-rows",
        "500",
        "--min-interior-rows",
        "500",
        "--min-group-evaluation-rows",
        "200",
        "--max-stress-rows",
        "512",
    ]


def test_boundary_ood_audit_separates_groups_and_reports_stress(tmp_path):
    module = _load_module()
    summary_path, _, _ = _write_fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary_path, out_dir)) == 0
    summary = json.loads((out_dir / "boundary_ood_stress_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["checks"]["boundary_interior_disjoint"] is True
    assert summary["analysis"]["partition_counts"]["boundary"] >= 2500
    assert summary["analysis"]["partition_counts"]["interior"] >= 2500
    ratio = summary["analysis"]["group_metrics"]["tandem_inverse"]["boundary_to_interior_rmse_ratio"]
    assert ratio > 2.0
    assert len(summary["analysis"]["stress"]["levels"]) == 4
    assert summary["analysis"]["stress"]["audit_row_count"] == 512
    assert summary["recommendation"]["all_predeclared_robustness_gates_pass"] is False
    figure = out_dir / "boundary_ood_stress_audit.png"
    assert figure.is_file()
    pixels = mpl_image.imread(figure)
    assert float(np.mean(pixels[0, 0, :3])) > 0.95


def test_boundary_ood_audit_rejects_insufficient_interior_evidence(tmp_path):
    module = _load_module()
    summary_path, _, _ = _write_fixture(tmp_path, interior_count=20)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary_path, out_dir)) == 2
    summary = json.loads((out_dir / "boundary_ood_stress_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["interior_rows_meet_minimum"] is False
    assert summary["decision"] == "DO_NOT_INTERPRET_BOUNDARY_OOD_STRESS"
