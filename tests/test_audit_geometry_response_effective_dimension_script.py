from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


GEOMETRY_COLUMNS = [
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
]
INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "audit_geometry_response_effective_dimension.py"
    spec = importlib.util.spec_from_file_location("audit_geometry_response_effective_dimension_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, write_weights: bool = True) -> Path:
    rng = np.random.default_rng(17)
    geometry = rng.uniform(-1.0, 1.0, size=(1200, 10))
    response = np.column_stack(
        (
            1.6 + 0.30 * geometry[:, 0] + 0.10 * geometry[:, 1],
            1.8 + 0.25 * geometry[:, 2] - 0.12 * geometry[:, 1],
            13.0 + 2.0 * geometry[:, 3] + 0.8 * geometry[:, 0],
            0.42 + 0.10 * geometry[:, 4] - 0.04 * geometry[:, 2],
        )
    )
    training_csv = root / "training.csv"
    with training_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INPUT_COLUMNS + GEOMETRY_COLUMNS)
        writer.writeheader()
        for response_row, geometry_row in zip(response, geometry):
            writer.writerow(
                {
                    **{name: float(response_row[index]) for index, name in enumerate(INPUT_COLUMNS)},
                    **{name: float(geometry_row[index]) for index, name in enumerate(GEOMETRY_COLUMNS)},
                }
            )

    x_mean = np.mean(response, axis=0)
    x_scale = np.std(response, axis=0)
    y_mean = np.mean(geometry, axis=0)
    y_scale = np.std(geometry, axis=0)
    response_normalized = (response - x_mean) / x_scale
    geometry_normalized = (geometry - y_mean) / y_scale
    forward_weight = np.linalg.lstsq(geometry_normalized, response_normalized, rcond=None)[0]
    weights_path = root / "weights.npz"
    if write_weights:
        np.savez_compressed(
            weights_path,
            forward_weight_0=forward_weight,
            forward_bias_0=np.zeros(4),
            normalization__x_mean=x_mean,
            normalization__x_scale=x_scale,
            normalization__y_mean=y_mean,
            normalization__y_scale=y_scale,
            normalization__geometry_lower=np.min(geometry_normalized, axis=0),
            normalization__geometry_upper=np.max(geometry_normalized, axis=0),
            normalization__response_loss_physical_spans=np.asarray([2.5, 2.5, 20.0, 0.8]),
        )
    summary_path = root / "tandem_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "overall_status": "COMPLETE_REVIEW_REQUIRED",
                "training_count": len(geometry),
                "training_csv": str(training_csv),
                "weights_npz": str(weights_path),
                "input_columns": INPUT_COLUMNS,
                "geometry_columns": GEOMETRY_COLUMNS,
                "split_audit": {"split_mode": "physical_cell_grouped", "physical_cell_overlap_count": 0},
                "metrics": {"forward_proxy": {"test_normalized_rmse": 0.01, "test_normalized_r2": 0.999}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary_path


def _arguments(summary_path: Path, out_dir: Path) -> list[str]:
    return [
        "--tandem-summary",
        str(summary_path),
        "--out-dir",
        str(out_dir),
        "--min-source-rows",
        "500",
        "--min-sample-rows",
        "200",
        "--max-sample-rows",
        "600",
        "--permutation-rows",
        "200",
        "--permutation-repeats",
        "2",
        "--jacobian-batch-size",
        "64",
    ]


def test_geometry_effective_dimension_audit_finds_sparse_active_directions(tmp_path):
    module = _load_module()
    summary_path = _write_fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_arguments(summary_path, out_dir)) == 0
    summary = json.loads((out_dir / "geometry_response_effective_dimension_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    analysis = summary["analysis"]
    assert analysis["active_subspace"]["dimension_for_90_percent_energy"] <= 4
    assert GEOMETRY_COLUMNS[0] in analysis["ranked_geometry_variables"][:5]
    assert GEOMETRY_COLUMNS[2] in analysis["ranked_geometry_variables"][:5]
    assert analysis["permutation_repeat_stability_min_cosine"] > 0.9
    assert (out_dir / "geometry_response_effective_dimension.png").is_file()
    assert (out_dir / "geometry_response_sensitivity.csv").is_file()
    assert (out_dir / "geometry_response_effective_dimension_report.md").is_file()


def test_geometry_effective_dimension_audit_rejects_missing_forward_weights(tmp_path):
    module = _load_module()
    summary_path = _write_fixture(tmp_path, write_weights=False)
    out_dir = tmp_path / "out"

    assert module.main(_arguments(summary_path, out_dir)) == 2
    summary = json.loads((out_dir / "geometry_response_effective_dimension_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["forward_model_available"] is False
    assert not (out_dir / "geometry_response_effective_dimension.png").exists()
