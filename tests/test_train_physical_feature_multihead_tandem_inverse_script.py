import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "train_physical_feature_multihead_tandem_inverse.py"
    )
    spec = importlib.util.spec_from_file_location(
        "train_physical_feature_multihead_tandem_inverse_script", script_path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_csv(
    path: Path,
    rows: int = 420,
    with_preferences: bool = False,
) -> None:
    rng = np.random.default_rng(20260712)
    geometry_columns = [
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
    fieldnames = [
        "evaluation",
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
        *(["aux__qp_center", "aux__qs_center"] if with_preferences else []),
        *geometry_columns,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index in range(rows):
            lp_nh = rng.uniform(0.55, 2.95)
            ls_nh = rng.uniform(0.55, 2.95)
            q_value = rng.uniform(5.2, 24.8)
            k_abs = rng.uniform(0.01, 0.79)
            p_width = 220.0 + 25.0 * lp_nh + rng.uniform(-1.0, 1.0)
            p_height = 210.0 + 30.0 * lp_nh + rng.uniform(-1.0, 1.0)
            s_width = 185.0 + 30.0 * ls_nh + rng.uniform(-1.0, 1.0)
            s_height = 180.0 + 32.0 * ls_nh + rng.uniform(-1.0, 1.0)
            line_width = 10.5 - 0.25 * q_value + rng.uniform(-0.1, 0.1)
            p_terminal = 35.0 + 10.0 * k_abs + rng.uniform(0.0, 5.0)
            s_terminal = 34.0 + 8.0 * k_abs + rng.uniform(0.0, 5.0)
            offset = 80.0 * (0.4 - k_abs) + rng.uniform(-0.5, 0.5)
            p_feed = 125.0 + 20.0 * lp_nh + rng.uniform(-1.0, 1.0)
            s_feed = 125.0 + 20.0 * ls_nh + rng.uniform(-1.0, 1.0)
            row = {
                "evaluation": f"synthetic_contract_fixture_{index:05d}",
                "input__lp_nh_center": lp_nh,
                "input__ls_nh_center": ls_nh,
                "input__q_center": q_value,
                "input__k_abs_center": k_abs,
                "geom__primary_outer_width_um": p_width,
                "geom__primary_outer_height_um": p_height,
                "geom__secondary_outer_width_um": s_width,
                "geom__secondary_outer_height_um": s_height,
                "geom__line_width_um": line_width,
                "geom__primary_terminal_y_span_um": p_terminal,
                "geom__secondary_terminal_y_span_um": s_terminal,
                "geom__offset_um": offset,
                "geom__primary_feed_extension_um": p_feed,
                "geom__secondary_feed_extension_um": s_feed,
            }
            if with_preferences:
                delta_q = 0.5 + 0.2 * (index % 5)
                if index % 2 == 0:
                    row.update(
                        {
                            "aux__qp_center": q_value,
                            "aux__qs_center": q_value + delta_q,
                        }
                    )
                else:
                    row.update(
                        {
                            "aux__qp_center": q_value + delta_q,
                            "aux__qs_center": q_value,
                        }
                    )
            writer.writerow(row)


def _write_training_manifest(
    path: Path,
    training_csv: Path,
    module,
    *,
    surrogate_values_allowed: bool = False,
    training_csv_sha256: str | None = None,
) -> None:
    payload = {
        "overall_status": "PASS",
        "training_table_source": {
            "sha256": training_csv_sha256 or module._sha256_file(training_csv),
        },
        "auxiliary_output_columns": ["aux__qp_center", "aux__qs_center"],
        "auxiliary_output_contract": {
            "same_real_simulator_row_required": True,
            "included_in_inverse_model_inputs": False,
            "predicted_or_surrogate_values_allowed": surrogate_values_allowed,
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_multihead_tandem_executes_with_fixed_physical_cell_ood(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "200",
            "--head-count",
            "3",
            "--forward-depth",
            "1",
            "--forward-width",
            "20",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "24",
            "--batch-size",
            "64",
            "--forward-epochs",
            "5",
            "--inverse-epochs",
            "6",
            "--patience",
            "3",
            "--learning-rate",
            "0.003",
            "--max-prediction-rows",
            "20",
        ]
    )

    assert status == 0
    summary_path = tmp_path / "out" / "physical_feature_multihead_tandem_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["execution_status"] == "PASS"
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["evaluation_scope"]["formal_explicit_physical_cell_ood"] is True
    assert summary["evaluation_isolation"]["overall_status"] == "PASS"
    assert all(summary["evaluation_isolation"]["checks"].values())
    assert summary["method"]["head_count"] == 3
    assert len(summary["geometry_columns"]) == 10
    assert len(summary["model_contract"]["fingerprint_sha256"]) == 64
    metrics = summary["metrics"]
    assert metrics["candidate_count_per_target"] == 3
    assert metrics["test_row_count"] > 0
    assert math.isfinite(metrics["best_of_k"]["response_range_normalized_rmse"])
    assert math.isfinite(metrics["diversity"]["mean_pairwise_normalized_distance"])
    assert 0.0 <= metrics["diversity"]["mean_unique_candidate_fraction"] <= 1.0
    assert sum(metrics["diversity"]["head_utilization_counts"]) == metrics["test_row_count"]
    for key in (
        "history_csv",
        "test_candidates_csv",
        "weights_npz",
    ):
        path = Path(summary[key])
        assert path.is_file() and path.stat().st_size > 0
        assert summary[f"{key}_sha256"] == module._sha256_file(path)
    history = list(csv.DictReader(Path(summary["history_csv"]).open(encoding="utf-8")))
    assert {row["stage"] for row in history} == {"forward_proxy", "multihead_inverse"}
    assert "validation_normalized_rmse" in history[0]
    assert "validation_best_of_k_response_rmse" in history[0]
    candidates = list(csv.DictReader(Path(summary["test_candidates_csv"]).open(encoding="utf-8")))
    assert len(candidates) == min(20, metrics["test_row_count"]) * 3
    assert {int(row["head_index"]) for row in candidates} == {0, 1, 2}
    assert sum(row["selected_best_of_k"] == "True" for row in candidates) == min(
        20, metrics["test_row_count"]
    )


def test_multihead_waits_truthfully_for_formal_table(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "small.csv"
    _write_training_csv(training_csv, rows=12)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "100",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_multihead_tandem_summary.json").read_text()
    )
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_DATA"
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["decision"] == "WAIT_FOR_FORMAL_REAL_EMX_TABLE"


def test_qp_qs_preference_heads_execute_with_same_real_s4p_manifest(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training_with_qp_qs.csv"
    manifest_path = tmp_path / "training_manifest.json"
    _write_training_csv(training_csv, with_preferences=True)
    _write_training_manifest(manifest_path, training_csv, module)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "200",
            "--head-count",
            "4",
            "--head-semantics",
            "qp_qs_preference",
            "--training-manifest-json",
            str(manifest_path),
            "--forward-depth",
            "1",
            "--forward-width",
            "20",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "24",
            "--batch-size",
            "64",
            "--forward-epochs",
            "4",
            "--inverse-epochs",
            "5",
            "--patience",
            "3",
            "--learning-rate",
            "0.003",
            "--max-prediction-rows",
            "12",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_multihead_tandem_summary.json").read_text()
    )
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["preference_manifest_audit"]["overall_status"] == "PASS"
    assert summary["preference_qmin_consistency"]["overall_status"] == "PASS"
    contract = summary["preference_head_contract"]
    assert contract["enabled"] is True
    assert contract["head_count"] == 4
    assert contract["quantile_edges_fit_on_train_only"] is True
    assert contract["validation_or_test_preferences_used_to_fit_edges"] is False
    assert contract["test_preferences_used_for_candidate_selection"] is False
    assert len(contract["head_semantics"]) == 4
    assert {
        item["orientation"] for item in contract["head_semantics"]
    } == {"qp_is_qmin_qs_is_higher", "qs_is_qmin_qp_is_higher"}
    assert summary["method"]["winner_selection"] == (
        "minimum_frozen_forward_feature_balanced_response_error"
    )
    candidates = list(
        csv.DictReader(Path(summary["test_candidates_csv"]).open(encoding="utf-8"))
    )
    assert candidates
    assert {row["head_semantics"] for row in candidates} == {"qp_qs_preference"}
    assert all(row["head_preference_orientation"] for row in candidates)
    with np.load(summary["weights_npz"]) as weights:
        assert weights["head_semantics"].tolist() == ["qp_qs_preference"]
        assert len(weights["preference_head_contract_fingerprint_sha256"][0]) == 64


def test_qp_qs_preference_heads_fail_closed_on_surrogate_manifest(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training_with_qp_qs.csv"
    manifest_path = tmp_path / "bad_manifest.json"
    _write_training_csv(training_csv, with_preferences=True)
    _write_training_manifest(
        manifest_path,
        training_csv,
        module,
        surrogate_values_allowed=True,
    )

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "200",
            "--head-count",
            "4",
            "--head-semantics",
            "qp_qs_preference",
            "--training-manifest-json",
            str(manifest_path),
        ]
    )

    assert status == 2
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_multihead_tandem_summary.json").read_text()
    )
    assert summary["overall_status"] == "FAIL"
    assert summary["preference_manifest_audit"]["overall_status"] == "FAIL"
    assert summary["checks"][
        "preference_manifest_proves_same_real_s4p_rows_when_enabled"
    ] is False
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["weights_npz"] == ""


def test_qp_qs_semantic_head_edges_and_fingerprint_are_train_only():
    module = _load_module()
    train = np.arange(8, dtype=int)
    split = {
        "train": train,
        "validation": np.asarray([8, 9], dtype=int),
        "test": np.asarray([10, 11], dtype=int),
    }
    matrix = {
        "count": 12,
        "source_geometry_identities": [f"geometry_{index:02d}" for index in range(12)],
    }
    values = np.asarray(
        [
            [8.0, 8.5],
            [8.7, 8.0],
            [8.0, 8.9],
            [9.1, 8.0],
            [8.0, 9.3],
            [9.5, 8.0],
            [8.0, 9.7],
            [9.9, 8.0],
            [8.0, 8.4],
            [8.6, 8.0],
            [8.0, 8.8],
            [9.0, 8.0],
        ],
        dtype=float,
    )
    altered = values.copy()
    altered[8:] = np.asarray(
        [[30.0, 8.0], [8.0, 35.0], [40.0, 8.0], [8.0, 45.0]], dtype=float
    )
    manifest_audit = {"overall_status": "PASS"}
    qmin_consistency = {"overall_status": "PASS"}

    first, first_assignments = module._configure_preference_heads(
        values,
        split,
        matrix,
        ["aux__qp_center", "aux__qs_center"],
        4,
        True,
        manifest_audit,
        qmin_consistency,
    )
    second, second_assignments = module._configure_preference_heads(
        altered,
        split,
        matrix,
        ["aux__qp_center", "aux__qs_center"],
        4,
        True,
        manifest_audit,
        qmin_consistency,
    )

    assert first["head_semantics"] == second["head_semantics"]
    assert first["fingerprint_sha256"] == second["fingerprint_sha256"]
    np.testing.assert_array_equal(first_assignments[train], second_assignments[train])


def test_multihead_exact_optimizer_budget_and_joint_cell_sampler_are_audited(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "200",
            "--head-count",
            "3",
            "--training-batch-sampler",
            "joint_cell_balanced",
            "--forward-depth",
            "1",
            "--forward-width",
            "16",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "20",
            "--batch-size",
            "64",
            "--forward-max-optimizer-updates",
            "5",
            "--inverse-max-optimizer-updates",
            "6",
            "--validation-every-optimizer-updates",
            "2",
            "--max-prediction-rows",
            "5",
        ]
    )

    assert status == 0
    summary = json.loads(
        (tmp_path / "out" / "physical_feature_multihead_tandem_summary.json").read_text()
    )
    sampler = summary["training_batch_sampler_contract"]
    budget = summary["optimizer_budget_contract"]
    assert sampler["family"] == "joint_cell_balanced"
    assert sampler["validation_or_test_rows_eligible_for_sampling"] is False
    assert budget["mode"] == "fixed_optimizer_updates"
    assert budget["realized"]["forward_optimizer_updates"] == 5
    assert budget["realized"]["inverse_optimizer_updates"] == 6
    assert budget["realized"]["exact_update_budget_pass"] is True


def test_diversity_gradient_pushes_distinct_heads_farther_apart():
    module = _load_module()
    geometry = np.asarray([[[-0.2, 0.0], [0.2, 0.0]]], dtype=float)
    penalty, gradient, distance = module._diversity_repulsion_and_gradient(geometry, 0.5)

    delta = geometry[0, 0] - geometry[0, 1]
    gradient_delta = gradient[0, 0] - gradient[0, 1]
    assert 0.0 < penalty < 1.0
    assert distance > 0.0
    assert float(np.dot(delta, gradient_delta)) < 0.0


def test_diversity_gradient_matches_central_finite_difference():
    module = _load_module()
    rng = np.random.default_rng(20260712)
    geometry = rng.normal(size=(2, 3, 4))
    scale = 0.7
    _penalty, gradient, _distance = module._diversity_repulsion_and_gradient(
        geometry, scale
    )
    epsilon = 1.0e-6
    for index in ((0, 0, 0), (0, 2, 3), (1, 1, 2)):
        plus = geometry.copy()
        minus = geometry.copy()
        plus[index] += epsilon
        minus[index] -= epsilon
        plus_penalty = module._diversity_repulsion_and_gradient(plus, scale)[0]
        minus_penalty = module._diversity_repulsion_and_gradient(minus, scale)[0]
        finite_difference = (plus_penalty - minus_penalty) / (2.0 * epsilon)
        assert math.isclose(
            gradient[index], finite_difference, rel_tol=1.0e-6, abs_tol=1.0e-8
        )


def test_greedy_unique_count_uses_normalized_geometry_distance():
    module = _load_module()
    candidates = np.asarray([[0.0, 0.0], [0.01, 0.01], [0.2, 0.2]])
    assert module._greedy_unique_count(candidates, 0.05) == 2
