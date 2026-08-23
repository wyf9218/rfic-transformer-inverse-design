from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys


INPUT_COLUMNS = [
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
]
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


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_tandem_local_refinement_benchmark.py"
    spec = importlib.util.spec_from_file_location("plan_tandem_local_refinement_benchmark_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, target_count: int = 24) -> tuple[Path, Path, Path]:
    summary = root / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "overall_status": "COMPLETE_REVIEW_REQUIRED",
                "training_count": 100,
                "input_columns": INPUT_COLUMNS,
                "geometry_columns": GEOMETRY_COLUMNS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    forward_weight = np.zeros((10, 4), dtype=float)
    forward_weight[:4, :4] = np.eye(4)
    inverse_weight = np.zeros((4, 10), dtype=float)
    inverse_weight[:4, :4] = np.eye(4) * 0.35
    weights = root / "weights.npz"
    np.savez_compressed(
        weights,
        forward_weight_0=forward_weight,
        forward_bias_0=np.zeros(4),
        inverse_weight_0=inverse_weight,
        inverse_bias_0=np.zeros(10),
        normalization__x_mean=np.zeros(4),
        normalization__x_scale=np.ones(4),
        normalization__y_mean=np.zeros(10),
        normalization__y_scale=np.ones(10),
        normalization__geometry_lower=np.zeros(10),
        normalization__geometry_upper=np.ones(10),
        normalization__response_loss_physical_spans=np.ones(4),
    )
    rng = np.random.default_rng(77)
    predictions = root / "predictions.csv"
    rows = []
    for index in range(target_count):
        target = rng.uniform(0.12, 0.88, size=4)
        rows.append(
            {
                "source_row_index": 1000 + index,
                **{f"target__{column.removeprefix('input__')}": target[col] for col, column in enumerate(INPUT_COLUMNS)},
            }
        )
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary, weights, predictions


def _args(summary: Path, weights: Path, predictions: Path, out_dir: Path, *, count: int = 8) -> list[str]:
    return [
        "--tandem-summary",
        str(summary),
        "--weights-npz",
        str(weights),
        "--predictions-csv",
        str(predictions),
        "--out-dir",
        str(out_dir),
        "--candidate-count",
        str(count),
        "--min-source-rows",
        "100",
        "--min-target-rows",
        str(count),
        "--max-target-scan",
        "24",
        "--trust-weight",
        "0.001",
    ]


def test_planner_builds_equal_disjoint_arms_and_improves_proxy_objective(tmp_path):
    module = _load_module()
    summary, weights, predictions = _write_fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary, weights, predictions, out_dir)) == 0
    result = json.loads((out_dir / "tandem_local_refinement_plan_summary.json").read_text())
    assert result["overall_status"] == "PASS"
    assert result["outcome_status"] == "AWAITING_REAL_EMX"
    assert result["analysis"]["selected_pair_count"] == 8
    assert result["analysis"]["arm_selected_counts"] == {"inverse_only": 8, "inverse_lbfgsb": 8}
    assert result["checks"]["geometry_disjoint_within_and_across_arms"] is True
    rows = list(csv.DictReader((out_dir / "tandem_local_refinement_candidates.csv").open()))
    assert len(rows) == 16
    assert {row["label_status"] for row in rows} == {"AWAITING_REAL_EMX"}
    assert {row["drc_status"] for row in rows} == {"NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX"}
    assert len({row["candidate_id"] for row in rows}) == 16
    for row in rows:
        geometry = np.asarray([float(row[column]) for column in GEOMETRY_COLUMNS])
        assert np.all(geometry >= 0.0)
        assert np.all(geometry <= 1.0)
    for pair in result["analysis"]["pair_metrics"]:
        assert pair["refined_response_rmse"] < pair["baseline_response_rmse"]
    assert (out_dir / "tandem_local_refinement_proxy_comparison.png").is_file()


def test_planner_rejects_insufficient_pair_budget(tmp_path):
    module = _load_module()
    summary, weights, predictions = _write_fixture(tmp_path, target_count=4)
    out_dir = tmp_path / "out"

    assert module.main(_args(summary, weights, predictions, out_dir, count=8)) == 2
    result = json.loads((out_dir / "tandem_local_refinement_plan_summary.json").read_text())
    assert result["overall_status"] == "FAIL"
    assert result["checks"]["prediction_targets_meet_minimum"] is False
    assert result["checks"]["selected_pair_budget_exact"] is False
