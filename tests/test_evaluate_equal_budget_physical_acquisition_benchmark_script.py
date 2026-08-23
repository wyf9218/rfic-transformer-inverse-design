from tests.rfic_transformer_inverse_design.shared import *

import csv
import importlib.util
import sys

from matplotlib import image as mpl_image
import pytest


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
FEATURE_COLUMNS = ["lp_nh_center", "ls_nh_center", "q_center", "k_abs_center"]
ARMS = [
    "random",
    "geometry_kmeanspp",
    "physical_deficit",
    "deficit_uncertainty",
    "deficit_diversity",
    "hierarchical_gap",
]


def _load_script(name: str):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_sources(root: Path) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(47)
    accepted = root / "accepted.csv"
    accepted_rows = []
    for index in range(160):
        geometry = rng.uniform(-0.8, 0.8, size=10)
        features = [rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), rng.uniform(5.0, 25.0), rng.uniform(0.0, 0.8)]
        accepted_rows.append(
            {
                **{name: geometry[column] for column, name in enumerate(GEOMETRY_COLUMNS)},
                **{name: features[column] for column, name in enumerate(FEATURE_COLUMNS)},
            }
        )
    _write_rows(accepted, accepted_rows)

    candidates = root / "candidates.csv"
    candidate_rows = []
    for index in range(420):
        geometry = rng.uniform(-1.8, 1.8, size=10)
        features = [rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), rng.uniform(5.0, 25.0), rng.uniform(0.0, 0.8)]
        uncertainty = [rng.uniform(0.01, 0.15), rng.uniform(0.01, 0.15), rng.uniform(0.1, 1.0), rng.uniform(0.005, 0.05)]
        candidate_rows.append(
            {
                "candidate_id": f"candidate_{index:05d}",
                **{name: geometry[column] for column, name in enumerate(GEOMETRY_COLUMNS)},
                **{f"pred_{name}": features[column] for column, name in enumerate(FEATURE_COLUMNS)},
                **{f"pred_uncertainty_{name}": uncertainty[column] for column, name in enumerate(FEATURE_COLUMNS)},
            }
        )
    _write_rows(candidates, candidate_rows)
    candidate_summary = root / "candidate_summary.json"
    candidate_summary.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "decision": "USE_AS_CANDIDATE_PREDICTIONS_ONLY",
                "bounds": {name: {"min": -2.0, "max": 2.0} for name in GEOMETRY_COLUMNS},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return accepted, candidates, candidate_summary


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _build_plan_and_results(root: Path, *, budget: int = 12) -> tuple[Path, Path, dict[str, Path]]:
    planner = _load_script("plan_equal_budget_physical_acquisition_benchmark")
    accepted, candidates, candidate_summary = _write_sources(root)
    plan_dir = root / "plan"
    assert planner.main(
        [
            "--candidate-csv",
            str(candidates),
            "--candidate-summary",
            str(candidate_summary),
            "--accepted-csv",
            str(accepted),
            "--out-dir",
            str(plan_dir),
            "--arm-budget",
            str(budget),
            "--min-accepted-rows",
            "100",
            "--kmeans-prefilter-factor",
            "4",
            "--kmeans-ranking-factor",
            "1.2",
        ]
    ) == 0
    plan_summary = plan_dir / "equal_budget_acquisition_benchmark_plan_summary.json"
    result_paths = {}
    for arm_index, arm in enumerate(ARMS):
        source = plan_dir / f"arm_{arm}_candidates.csv"
        rows = list(csv.DictReader(source.open()))
        result_rows = []
        for row_index, row in enumerate(rows):
            real = np.asarray([float(row[f"pred_{name}"]) for name in FEATURE_COLUMNS])
            shift = (arm_index - 2.0) * np.asarray([0.004, -0.003, 0.02, 0.001])
            real = np.clip(real + shift, [0.5, 0.5, 5.0, 0.0], [3.0, 3.0, 25.0, 0.8])
            touchstone = root / f"{arm}_{row_index:03d}.s4p"
            touchstone.write_text("! synthetic nonempty real-EMX return fixture\n", encoding="ascii")
            result_rows.append(
                {
                    **row,
                    "ok": "true",
                    "touchstone_path": str(touchstone),
                    "qp_center": real[2],
                    "qs_center": min(25.0, real[2] + 0.1),
                    **{name: real[column] for column, name in enumerate(FEATURE_COLUMNS)},
                }
            )
        result_path = root / f"result_{arm}.csv"
        _write_rows(result_path, result_rows)
        result_paths[arm] = result_path
    return accepted, plan_summary, result_paths


def _arguments(accepted: Path, plan: Path, results: dict[str, Path], out_dir: Path, budget: int) -> list[str]:
    args = [
        "--plan-summary",
        str(plan),
        "--baseline-accepted-csv",
        str(accepted),
        "--out-dir",
        str(out_dir),
        "--expected-arm-budget",
        str(budget),
        "--min-success-fraction",
        "1.0",
        "--bootstrap-replicates",
        "40",
    ]
    for arm in ARMS:
        args.extend(["--arm-result", f"{arm}={results[arm]}"])
    return args


def test_real_emx_policy_evaluator_requires_all_six_equal_budget_arms(tmp_path):
    module = _load_script("evaluate_equal_budget_physical_acquisition_benchmark")
    accepted, plan, results = _build_plan_and_results(tmp_path, budget=12)
    out_dir = tmp_path / "evaluation"

    assert module.main(_arguments(accepted, plan, results, out_dir, 12)) == 0
    summary = json.loads((out_dir / "equal_budget_real_emx_benchmark_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["outcome_status"] == "REAL_EMX_COVERAGE_COMPLETE_MODEL_ABLATION_PENDING"
    assert set(summary["analysis"]["coverage_ranking"]) == set(ARMS)
    assert summary["analysis"]["ranking_status"] == "ADVISORY_ONLY_MODEL_RETRAIN_REQUIRED"
    robustness = summary["analysis"]["ranking_robustness"]
    assert robustness["weight_simplex_sensitivity"]["grid_count"] == 66
    assert robustness["row_resampling_sensitivity"]["replicates"] == 40
    assert sum(robustness["weight_simplex_sensitivity"]["top_fraction"].values()) == pytest.approx(1.0)
    assert sum(robustness["row_resampling_sensitivity"]["top_fraction"].values()) == pytest.approx(1.0)
    assert (out_dir / "equal_budget_real_emx_policy_metrics.csv").is_file()
    figure = out_dir / "equal_budget_real_emx_coverage_comparison.png"
    assert figure.is_file()
    pixels = mpl_image.imread(figure)
    assert float(np.mean(pixels[0, 0, :3])) > 0.95


def test_real_emx_policy_evaluator_rejects_missing_planned_candidate(tmp_path):
    module = _load_script("evaluate_equal_budget_physical_acquisition_benchmark")
    accepted, plan, results = _build_plan_and_results(tmp_path, budget=10)
    random_rows = list(csv.DictReader(results["random"].open()))
    _write_rows(results["random"], random_rows[:-1])
    out_dir = tmp_path / "evaluation"

    assert module.main(_arguments(accepted, plan, results, out_dir, 10)) == 2
    summary = json.loads((out_dir / "equal_budget_real_emx_benchmark_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["all_result_ids_exact"] is False


def test_weight_sensitivity_splits_exact_ties_without_arm_order_bias():
    module = _load_script("evaluate_equal_budget_physical_acquisition_benchmark")
    distribution = {
        "four_d": {"normalized_entropy": 0.75, "occupied_fraction": 0.50},
        "mean_pair_normalized_entropy": 0.70,
    }
    arms = {arm: {"distribution_after_adding_arm": distribution} for arm in ARMS}
    result = module._weight_sensitivity(arms, 0.10)

    assert result["grid_count"] == 66
    assert result["tie_policy"].startswith("exact score ties")
    assert all(value == pytest.approx(1.0 / len(ARMS)) for value in result["top_fraction"].values())
    assert all(value == pytest.approx((len(ARMS) + 1.0) / 2.0) for value in result["mean_rank"].values())
