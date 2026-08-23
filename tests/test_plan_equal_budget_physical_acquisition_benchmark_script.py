from tests.rfic_transformer_inverse_design.shared import *

import argparse
import csv
import importlib.util
import math
import sys

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


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "plan_equal_budget_physical_acquisition_benchmark.py"
    spec = importlib.util.spec_from_file_location("plan_equal_budget_physical_acquisition_benchmark_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, candidate_count: int = 500) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(31)
    accepted_csv = root / "accepted.csv"
    accepted_rows = []
    for index in range(160):
        geometry = rng.uniform(-0.9, 0.9, size=10)
        features = np.asarray(
            [rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), rng.uniform(5.0, 25.0), rng.uniform(0.0, 0.8)]
        )
        accepted_rows.append(
            {
                **{name: float(geometry[column]) for column, name in enumerate(GEOMETRY_COLUMNS)},
                **{name: float(features[column]) for column, name in enumerate(FEATURE_COLUMNS)},
            }
        )
    with accepted_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(accepted_rows[0]))
        writer.writeheader()
        writer.writerows(accepted_rows)

    candidate_csv = root / "candidates.csv"
    candidate_rows = []
    for index in range(candidate_count):
        geometry = rng.uniform(-1.8, 1.8, size=10)
        features = np.asarray(
            [rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), rng.uniform(5.0, 25.0), rng.uniform(0.0, 0.8)]
        )
        uncertainty = np.asarray([rng.uniform(0.01, 0.2), rng.uniform(0.01, 0.2), rng.uniform(0.1, 1.5), rng.uniform(0.005, 0.08)])
        candidate_rows.append(
            {
                "candidate_id": f"candidate_{index:05d}",
                **{name: float(geometry[column]) for column, name in enumerate(GEOMETRY_COLUMNS)},
                **{f"pred_{name}": float(features[column]) for column, name in enumerate(FEATURE_COLUMNS)},
                **{f"pred_uncertainty_{name}": float(uncertainty[column]) for column, name in enumerate(FEATURE_COLUMNS)},
            }
        )
    with candidate_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(candidate_rows[0]))
        writer.writeheader()
        writer.writerows(candidate_rows)

    summary_path = root / "candidate_summary.json"
    summary_path.write_text(
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
    return accepted_csv, candidate_csv, summary_path


def _args(accepted: Path, candidates: Path, summary: Path, out_dir: Path, *, budget: int = 20) -> list[str]:
    return [
        "--candidate-csv",
        str(candidates),
        "--candidate-summary",
        str(summary),
        "--accepted-csv",
        str(accepted),
        "--out-dir",
        str(out_dir),
        "--arm-budget",
        str(budget),
        "--min-accepted-rows",
        "100",
        "--kmeans-prefilter-factor",
        "4",
        "--kmeans-ranking-factor",
        "1.2",
    ]


def test_equal_budget_planner_builds_six_disjoint_unlabeled_arms(tmp_path):
    module = _load_module()
    accepted, candidates, candidate_summary = _write_fixture(tmp_path)
    out_dir = tmp_path / "out"

    assert module.main(_args(accepted, candidates, candidate_summary, out_dir)) == 0
    summary = json.loads((out_dir / "equal_budget_acquisition_benchmark_plan_summary.json").read_text())
    assert summary["overall_status"] == "PASS"
    assert summary["outcome_status"] == "AWAITING_REAL_EMX"
    assert summary["analysis"]["selected_counts"] == {arm: 20 for arm in ARMS}
    assert summary["analysis"]["selected_index_overlap_count"] == 0
    combined = list(csv.DictReader((out_dir / "equal_budget_acquisition_assignments.csv").open()))
    assert len(combined) == 120
    assert {row["label_status"] for row in combined} == {"AWAITING_REAL_EMX"}
    assert len({row["candidate_id"] for row in combined}) == 120
    for arm in ARMS:
        rows = list(csv.DictReader((out_dir / f"arm_{arm}_candidates.csv").open()))
        assert len(rows) == 20
        assert {row["benchmark_arm"] for row in rows} == {arm}
    diversity_rows = [row for row in combined if row["benchmark_arm"] == "deficit_diversity"]
    assert all(math.isfinite(float(row["deficit_diversity_score"])) for row in diversity_rows)
    assert summary["analysis"]["deficit_diversity_configuration"] == {
        "deficit_weight": 0.65,
        "geometry_diversity_weight": 0.35,
        "prefilter_factor": 8,
        "ranking_factor": 1.5,
        "distance_normalization": "Euclidean distance in 10-D geometry normalized by traceable bounds, clipped by sqrt(10)",
        "production_policy_changed": False,
    }
    assert "sequential farthest-first" in summary["analysis"]["ranking_contract"]["deficit_diversity"]
    hierarchical_rows = [row for row in combined if row["benchmark_arm"] == "hierarchical_gap"]
    assert all(math.isfinite(float(row["hierarchical_gap_score"])) for row in hierarchical_rows)
    assert summary["analysis"]["hierarchical_gap_configuration"] == {
        "marginal_bins": 10,
        "pair_bins": 10,
        "pair_features": [
            ["lp_nh_center", "q_center"],
            ["ls_nh_center", "q_center"],
            ["q_center", "k_abs_center"],
        ],
        "marginal_weight": 0.25,
        "pair_weight": 0.45,
        "four_d_weight": 0.2,
        "geometry_novelty_weight": 0.1,
        "labels_for_priority": "accepted real EMX only",
        "candidate_response_values": "proxy ranking only",
        "production_policy_changed": False,
    }
    assert (out_dir / "equal_budget_predicted_coverage_comparison.png").is_file()


def test_equal_budget_planner_rejects_candidate_pool_smaller_than_total_budget(tmp_path):
    module = _load_module()
    accepted, candidates, candidate_summary = _write_fixture(tmp_path, candidate_count=50)
    out_dir = tmp_path / "out"

    assert module.main(_args(accepted, candidates, candidate_summary, out_dir, budget=20)) == 2
    summary = json.loads((out_dir / "equal_budget_acquisition_benchmark_plan_summary.json").read_text())
    assert summary["overall_status"] == "FAIL"
    assert summary["checks"]["candidate_pool_large_enough"] is False
    assert summary["outcome_status"] == "AWAITING_REAL_EMX"


def test_deficit_diversity_ranking_is_sequential_and_tie_aware():
    module = _load_module()
    geometry = np.asarray(
        [
            [0.0] * 10,
            [0.01] * 10,
            [1.0] * 10,
            [0.5] * 10,
        ],
        dtype=float,
    )
    deficits = np.ones(4, dtype=float)
    novelty = np.asarray([0.9, 0.89, 0.8, 0.7], dtype=float)
    args = argparse.Namespace(
        diversity_deficit_weight=0.65,
        diversity_geometry_weight=0.35,
        diversity_prefilter_factor=8,
        diversity_ranking_factor=1.0,
        seed=20260711,
    )

    assert module._percentile_rank(deficits).tolist() == pytest.approx([0.5, 0.5, 0.5, 0.5])
    order, scores = module._deficit_diversity_rank(geometry, deficits, novelty, 2, args)
    assert order[:2].tolist() == [0, 2]
    assert np.isfinite(scores).all()


def test_hierarchical_gap_score_prioritizes_missing_q_k_pair():
    module = _load_module()
    centers = np.asarray([0.125, 0.375, 0.625, 0.875])
    accepted = []
    for repeat in range(12):
        for q_bin in range(4):
            lp_bin = (repeat + q_bin) % 4
            ls_bin = (repeat + 2 * q_bin) % 4
            k_bin = 3 - q_bin
            normalized = [centers[lp_bin], centers[ls_bin], centers[q_bin], centers[k_bin]]
            accepted.append(
                [
                    0.5 + 2.5 * normalized[0],
                    0.5 + 2.5 * normalized[1],
                    5.0 + 20.0 * normalized[2],
                    0.8 * normalized[3],
                ]
            )
    candidates = np.asarray(
        [
            [2.6875, 2.6875, 22.5, 0.7],  # missing Q-high / K-high pair
            [2.6875, 2.6875, 22.5, 0.1],  # observed Q-high / K-low pair
        ],
        dtype=float,
    )
    args = argparse.Namespace(
        arm_budget=1,
        hierarchical_marginal_bins=4,
        hierarchical_pair_bins=4,
        hierarchical_marginal_weight=0.25,
        hierarchical_pair_weight=0.45,
        hierarchical_four_d_weight=0.20,
        hierarchical_novelty_weight=0.10,
    )
    result = module._hierarchical_gap_scores(
        np.asarray(accepted),
        candidates,
        np.zeros(2),
        np.ones(2),
        args,
    )

    assert result["pair_score"][0] > result["pair_score"][1]
    assert result["combined_score"][0] > result["combined_score"][1]
