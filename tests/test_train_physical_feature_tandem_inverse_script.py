import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "train_physical_feature_tandem_inverse.py"
    spec = importlib.util.spec_from_file_location("train_physical_feature_tandem_inverse_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_training_csv(path: Path, rows: int = 320) -> None:
    rng = np.random.default_rng(20260711)
    fieldnames = [
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__qp_center",
        "input__qs_center",
        "input__k_abs_center",
        "geom__width_um",
        "geom__height_um",
        "geom__spacing_um",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for _ in range(rows):
            width = rng.uniform(200.0, 320.0)
            height = rng.uniform(190.0, 310.0)
            spacing = rng.uniform(4.0, 12.0)
            q_base = 18.0 - 0.25 * spacing + 0.005 * width
            qp = q_base + 0.4 + 0.001 * height
            qs = q_base + 0.2 + 0.001 * width
            writer.writerow(
                {
                    "input__lp_nh_center": 0.003 * width + 0.001 * height,
                    "input__ls_nh_center": 0.001 * width + 0.0035 * height,
                    "input__q_center": min(qp, qs),
                    "input__qp_center": qp,
                    "input__qs_center": qs,
                    "input__k_abs_center": 0.72 - 0.025 * spacing + 0.0002 * height,
                    "geom__width_um": width,
                    "geom__height_um": height,
                    "geom__spacing_um": spacing,
                }
            )


def _write_topology_training_csv(path: Path, rows: int = 320) -> None:
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
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
        *geometry_columns,
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for _ in range(rows):
            p_width = rng.uniform(210.0, 320.0)
            p_height = rng.uniform(210.0, 320.0)
            s_width = rng.uniform(180.0, 290.0)
            s_height = rng.uniform(180.0, 290.0)
            line_width = rng.uniform(3.0, 10.0)
            p_terminal = rng.uniform(25.0, 70.0)
            s_terminal = rng.uniform(25.0, 70.0)
            offset = rng.uniform(-50.0, 50.0)
            p_feed = rng.uniform(100.0, 220.0)
            s_feed = rng.uniform(100.0, 220.0)
            writer.writerow(
                {
                    "input__lp_nh_center": 0.0025 * p_width + 0.001 * p_height,
                    "input__ls_nh_center": 0.0026 * s_width + 0.0011 * s_height,
                    "input__q_center": 15.0 + 0.01 * (p_width + s_width) - 0.25 * line_width,
                    "input__k_abs_center": 0.48 - 0.0012 * abs(offset) + 0.0002 * (p_height + s_height),
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
            )


def test_waits_for_complete_real_training_data(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, rows=16)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "100",
            "--no-fail-exit",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "WAITING_FOR_COMPLETE_DATA"
    assert summary["execution_status"] == "WAITING_FOR_COMPLETE_DATA"
    assert summary["quality_status"] == "NOT_RUN"
    assert summary["eligible_for_checkpoint_model_acceptance"] is False
    assert summary["eligible_for_model_success_claim"] is False


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("input__lp_nh_center", "lp"),
        ("phys__ls_nh_center", "ls"),
        ("input__q_center", "q"),
        ("input__qp_center", "q"),
        ("aux__qp_center", "q"),
        ("aux__qs_center", "q"),
        ("aux_qp_center", "q"),
        ("aux_qs_center", "q"),
        ("input__k_abs_center", "k"),
    ],
)
def test_physical_feature_semantic_accepts_provenance_prefixes(column, expected):
    module = _load_module()
    assert module._physical_feature_semantic(column) == expected


def test_trains_forward_proxy_and_tandem_inverse(tmp_path):
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
            "--forward-depth",
            "2",
            "--forward-width",
            "32",
            "--inverse-depth",
            "2",
            "--inverse-width",
            "32",
            "--batch-size",
            "64",
            "--forward-epochs",
            "80",
            "--inverse-epochs",
            "100",
            "--patience",
            "20",
            "--learning-rate",
            "0.004",
            "--max-forward-test-rmse",
            "0.5",
            "--max-tandem-response-test-rmse",
            "0.7",
            "--local-refinement-steps",
            "5",
            "--local-refinement-starts",
            "2",
        ]
    )

    assert status == 0
    summary_path = tmp_path / "out" / "physical_feature_tandem_inverse_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["overall_status"] == "PASS"
    assert summary["execution_status"] == "PASS"
    assert summary["quality_status"] == "PASS"
    assert summary["eligible_for_checkpoint_model_acceptance"] is False
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["evaluation_scope"]["formal_explicit_physical_cell_ood"] is False
    isolation = summary["evaluation_isolation"]
    assert isolation["overall_status"] == "PASS"
    assert all(isolation["checks"].values())
    assert isolation["test_set_used_for_gradient_updates"] is False
    assert isolation["test_set_used_for_early_stopping"] is False
    assert isolation["test_set_used_for_model_or_hyperparameter_selection"] is False
    assert isolation["test_set_used_for_acceptance_threshold_tuning"] is False
    assert isolation["test_set_used_only_for_post_training_evaluation"] is True
    assert len(isolation["geometry_identity_set_sha256"]["test"]) == 64
    model_contract = summary["model_comparison_contract"]
    assert model_contract["schema"] == "physical_feature_tandem_cross_checkpoint_v1"
    assert len(model_contract["trainer_implementation_sha256"]) == 64
    assert len(model_contract["fingerprint_sha256"]) == 64
    assert len(summary["training_csv_sha256"]) == 64
    assert summary["training_csv_sha256"] == module._sha256_file(training_csv)
    assert summary["test_predictions_csv_sha256"] == module._sha256_file(
        Path(summary["test_predictions_csv"])
    )
    assert summary["history_csv_sha256"] == module._sha256_file(Path(summary["history_csv"]))
    assert summary["weights_npz_sha256"] == module._sha256_file(Path(summary["weights_npz"]))
    assert summary["metrics"]["forward_proxy"]["test_normalized_rmse"] < 0.5
    assert math.isfinite(summary["metrics"]["forward_proxy"]["test_normalized_r2"])
    assert summary["metrics"]["tandem_inverse"]["test_response_normalized_rmse"] < 0.7
    assert math.isfinite(summary["metrics"]["tandem_inverse"]["test_feature_balanced_response_normalized_rmse"])
    assert math.isfinite(summary["metrics"]["tandem_inverse"]["test_response_normalized_r2"])
    assert summary["metrics"]["tandem_inverse"]["geometry_envelope_sample_violation_rate"] == 0.0
    assert summary["metrics"]["tandem_inverse"]["geometry_envelope_definition"].endswith("_not_drc")
    refinement = summary["metrics"]["tandem_inverse"]["local_refinement"]
    assert refinement["enabled"] is True
    assert refinement["selection_nonworsening_by_construction"] is True
    assert refinement["selected_feature_balanced_normalized_rmse"] <= refinement[
        "baseline_feature_balanced_normalized_rmse"
    ]
    robustness = summary["metrics"]["input_noise_robustness"]
    assert robustness["status"] == "AUDIT_ONLY_NO_PASS_GATE"
    assert robustness["audit_row_count"] > 0
    assert [item["relative_noise_std"] for item in robustness["levels"]] == [0.01, 0.03, 0.05, 0.1]
    assert all(math.isfinite(item["clean_target_response_normalized_rmse_mean"]) for item in robustness["levels"])
    assert set(summary["metrics"]["per_feature_forward_proxy_physical_mae"]) == {
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
    }
    assert Path(summary["weights_npz"]).is_file()
    assert Path(summary["history_csv"]).is_file()
    assert Path(summary["test_predictions_csv"]).is_file()
    prediction_header = Path(summary["test_predictions_csv"]).read_text(encoding="utf-8").splitlines()[0]
    assert "forward__lp_nh_center" in prediction_header
    assert "reconstructed__lp_nh_center" in prediction_header
    assert "source_evaluation" in prediction_header
    assert "source_geometry_identity_sha256" in prediction_header
    prediction_rows = list(csv.DictReader(Path(summary["test_predictions_csv"]).open(encoding="utf-8")))
    assert len(prediction_rows) == summary["metrics"]["test_row_count"]
    assert all(len(row["source_geometry_identity_sha256"]) == 64 for row in prediction_rows)
    assert len({row["source_geometry_identity_sha256"] for row in prediction_rows}) == len(prediction_rows)
    response_contract = summary["response_loss_contract"]
    assert response_contract["family"] == "mse"
    assert response_contract["balanced_mse_bni"] is None
    assert response_contract["scaling"] == "declared_range"
    assert math.isclose(response_contract["dimension_weight_mean"], 1.0)
    assert set(response_contract["standardized_dimension_weights"]) == {
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
    }
    history_rows = list(csv.DictReader(Path(summary["history_csv"]).open(encoding="utf-8")))
    inverse_rows = [row for row in history_rows if row["stage"] == "tandem_inverse"]
    assert inverse_rows
    assert {row["response_weight_schedule_phase"] for row in inverse_rows} & {"warmup", "ramp"}


def test_multi_start_local_refinement_is_projected_and_nonworsening():
    module = _load_module()
    targets = np.asarray([[0.8, 0.2], [0.3, 0.7]], dtype=float)
    initial = np.asarray([[0.1, 0.9], [0.9, 0.1]], dtype=float)
    weights = [np.eye(2, dtype=float)]
    biases = [np.zeros(2, dtype=float)]
    args = SimpleNamespace(
        local_refinement_steps=30,
        local_refinement_starts=3,
        local_refinement_learning_rate=0.2,
        local_refinement_jitter=0.05,
        local_refinement_seed=17,
    )

    selected, audit = module._refine_geometry_candidates(
        targets,
        initial,
        weights,
        biases,
        np.zeros(2, dtype=float),
        np.ones(2, dtype=float),
        np.ones(2, dtype=float),
        args,
    )

    initial_mse = np.mean((initial - targets) ** 2, axis=1)
    selected_mse = np.mean((selected - targets) ** 2, axis=1)
    assert np.all(selected_mse <= initial_mse + 1.0e-12)
    assert np.any(selected_mse < initial_mse - 1.0e-6)
    assert np.all(selected >= 0.0)
    assert np.all(selected <= 1.0)
    assert audit["enabled"] is True
    assert audit["selection_nonworsening_by_construction"] is True
    assert audit["selected_feature_balanced_normalized_rmse"] <= audit[
        "baseline_feature_balanced_normalized_rmse"
    ]
    assert audit["selection_basis"] == "frozen_forward_proxy_only"


def _balanced_mse_bni_fixture(module):
    input_columns = [
        "input__lp_nh_center",
        "input__ls_nh_center",
        "input__q_center",
        "input__k_abs_center",
    ]
    split_x = np.asarray(
        [
            [0.1, 0.1, 0.1, 0.1],
            [0.2, 0.2, 0.2, 0.2],
            [1.1, 1.1, 1.1, 1.1],
            [2.1, 2.1, 2.1, 2.1],
            [3.1, 3.1, 3.1, 3.1],
        ],
        dtype=float,
    )
    data = {
        "split_x_physical": split_x,
        "split": {
            "train": np.asarray([0, 1, 2], dtype=int),
            "validation": np.asarray([3], dtype=int),
            "test": np.asarray([4], dtype=int),
        },
        "normalization": {
            "x_mean": np.zeros(4, dtype=float),
            "x_scale": np.ones(4, dtype=float),
        },
    }
    audit = {
        "split_mode": "physical_cell_grouped",
        "physical_cell_range_source": "explicit",
        "physical_cell_bins_per_dimension": 4,
        "physical_cell_lower": [0.0, 0.0, 0.0, 0.0],
        "physical_cell_upper": [4.0, 4.0, 4.0, 4.0],
        "split_index_sha256": {"train": "a" * 64},
    }
    return input_columns, data, audit


def test_balanced_mse_bni_prior_uses_training_cells_only():
    module = _load_module()
    input_columns, data, audit = _balanced_mse_bni_fixture(module)

    state, contract = module._build_balanced_mse_bni_state(
        data,
        input_columns,
        input_columns,
        audit,
        0.2,
    )
    changed_held_out = {
        **data,
        "split_x_physical": np.asarray(data["split_x_physical"]).copy(),
    }
    changed_held_out["split_x_physical"][3:] = np.asarray(
        [[3.8, 2.8, 3.8, 2.8], [2.8, 3.8, 2.8, 3.8]],
        dtype=float,
    )
    changed_state, changed_contract = module._build_balanced_mse_bni_state(
        changed_held_out,
        input_columns,
        input_columns,
        audit,
        0.2,
    )

    assert state["cell_indices"].tolist() == [[0, 0, 0, 0], [1, 1, 1, 1]]
    assert state["cell_row_counts"].tolist() == [2, 1]
    assert state["priors"] == pytest.approx([2.0 / 3.0, 1.0 / 3.0])
    assert state["centers_physical"].tolist() == [[0.5] * 4, [1.5] * 4]
    assert contract["validation_or_test_rows_used_in_prior"] is False
    assert contract["training_row_count"] == 3
    assert contract["prior_sum"] == pytest.approx(1.0)
    assert contract["fingerprint_sha256"] == changed_contract["fingerprint_sha256"]
    np.testing.assert_array_equal(state["cell_indices"], changed_state["cell_indices"])
    np.testing.assert_allclose(state["priors"], changed_state["priors"])


def test_balanced_mse_bni_gradient_matches_finite_difference_and_cell_order_is_invariant():
    module = _load_module()
    prediction = np.asarray([[0.2, -0.1, 0.4, 0.3], [0.7, 0.6, -0.2, 0.1]], dtype=float)
    truth = np.asarray([[0.1, 0.0, 0.5, 0.2], [0.8, 0.4, -0.1, 0.0]], dtype=float)
    weights = np.asarray([0.5, 1.0, 2.0, 0.8], dtype=float)
    state = {
        "family": "balanced_mse_bni",
        "temperature": 0.7,
        "centers_normalized": np.asarray(
            [[-1.0, -0.5, 0.0, 0.5], [0.2, 0.4, 0.6, 0.8], [1.0, 0.5, -0.5, -1.0]],
            dtype=float,
        ),
        "priors": np.asarray([0.2, 0.5, 0.3], dtype=float),
    }
    loss, gradient = module._response_loss_and_gradient(prediction, truth, weights, state)
    assert math.isfinite(loss)
    epsilon = 1.0e-6
    for row in range(prediction.shape[0]):
        for column in range(prediction.shape[1]):
            plus = prediction.copy()
            minus = prediction.copy()
            plus[row, column] += epsilon
            minus[row, column] -= epsilon
            plus_loss = module._response_loss_and_gradient(plus, truth, weights, state)[0]
            minus_loss = module._response_loss_and_gradient(minus, truth, weights, state)[0]
            finite_difference = (plus_loss - minus_loss) / (2.0 * epsilon)
            assert gradient[row, column] == pytest.approx(finite_difference, rel=2.0e-5, abs=2.0e-7)

    permutation = np.asarray([2, 0, 1], dtype=int)
    permuted_state = {
        **state,
        "centers_normalized": state["centers_normalized"][permutation],
        "priors": state["priors"][permutation],
    }
    permuted_loss, permuted_gradient = module._response_loss_and_gradient(
        prediction,
        truth,
        weights,
        permuted_state,
    )
    assert permuted_loss == pytest.approx(loss, rel=1.0e-12, abs=1.0e-12)
    np.testing.assert_allclose(permuted_gradient, gradient, rtol=1.0e-12, atol=1.0e-12)


def test_balanced_mse_bni_requires_explicit_grouped_matching_physical_contract():
    module = _load_module()
    input_columns, data, audit = _balanced_mse_bni_fixture(module)
    with pytest.raises(ValueError, match="exactly match"):
        module._build_balanced_mse_bni_state(
            data,
            input_columns,
            [*input_columns[:2], "input__qp_center", "input__k_abs_center"],
            audit,
            0.2,
        )
    observed_bounds = {**audit, "physical_cell_range_source": "observed_full_dataset_min_max"}
    with pytest.raises(ValueError, match="explicit preregistered"):
        module._build_balanced_mse_bni_state(
            data,
            input_columns,
            input_columns,
            observed_bounds,
            0.2,
        )


def test_balanced_mse_bni_requires_explicit_positive_temperature():
    module = _load_module()
    base = ["--training-csv", "training.csv", "--out-dir", "out"]
    args = module._parse_args(base)
    assert args.response_loss_family == "mse"
    assert args.balanced_mse_temperature is None
    with pytest.raises(SystemExit):
        module._parse_args([*base, "--response-loss-family", "balanced_mse_bni"])
    with pytest.raises(SystemExit):
        module._parse_args(
            [
                *base,
                "--response-loss-family",
                "balanced_mse_bni",
                "--balanced-mse-temperature",
                "0",
            ]
        )


def test_trains_balanced_mse_bni_ablation_and_serializes_train_prior(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, rows=640)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "400",
            "--split-mode",
            "physical_cell_grouped",
            "--physical-cell-bins",
            "4",
            "--physical-cell-lower",
            "0.5,0.5,10,0.3",
            "--physical-cell-upper",
            "1.5,1.6,20,0.8",
            "--response-loss-family",
            "balanced_mse_bni",
            "--balanced-mse-temperature",
            "0.1",
            "--forward-depth",
            "1",
            "--forward-width",
            "12",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "12",
            "--batch-size",
            "128",
            "--forward-epochs",
            "2",
            "--inverse-epochs",
            "2",
            "--patience",
            "1",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text())
    response_contract = summary["response_loss_contract"]
    bni_contract = response_contract["balanced_mse_bni"]
    assert response_contract["family"] == "balanced_mse_bni"
    assert summary["method"]["response_loss_family"] == "balanced_mse_bni"
    assert "balanced_mse_bni" in summary["method"]["inverse_loss"]
    assert bni_contract["validation_or_test_rows_used_in_prior"] is False
    assert bni_contract["training_row_count"] == summary["split_audit"]["row_counts"]["train"]
    assert bni_contract["occupied_training_cell_count"] > 1
    assert len(bni_contract["fingerprint_sha256"]) == 64
    assert summary["training_csv_sha256"] == module._sha256_file(training_csv)
    with np.load(summary["weights_npz"]) as weights:
        assert "response_loss__bni_centers_normalized" in weights
        assert "response_loss__bni_priors" in weights
        assert weights["response_loss__bni_priors"].sum() == pytest.approx(1.0)


def test_topology_penalty_is_zero_for_valid_geometry_and_positive_for_invalid_geometry():
    module = _load_module()
    columns = [f"geom__{name}" for name in module.TOPOLOGY_GEOMETRY_SEMANTICS]
    column_contract = module._topology_feasibility_column_contract(columns)
    normalization = {
        "y_mean": np.zeros(len(columns)),
        "y_scale": np.ones(len(columns)),
        "geometry_lower": np.zeros(len(columns)),
        "geometry_upper": np.full(len(columns), 300.0),
    }
    args = SimpleNamespace(topology_feasibility_weight=0.02)
    contract = module._configure_topology_feasibility(normalization, columns, column_contract, args)
    valid = np.asarray([[200.0, 200.0, 180.0, 180.0, 50.0, 50.0, 0.0, 100.0, 100.0]])
    invalid = np.asarray([[100.0, 100.0, 100.0, 100.0, 120.0, 120.0, 150.0, 10.0, 10.0]])

    valid_penalty, valid_gradient, valid_diagnostics = module._topology_feasibility_penalty_and_gradient(
        valid, normalization, contract
    )
    invalid_penalty, invalid_gradient, invalid_diagnostics = module._topology_feasibility_penalty_and_gradient(
        invalid, normalization, contract
    )

    assert valid_penalty == 0.0
    assert np.all(valid_gradient == 0.0)
    assert valid_diagnostics["violation_fraction"] == 0.0
    assert invalid_penalty > 0.0
    assert np.any(np.abs(invalid_gradient) > 0.0)
    assert invalid_diagnostics["violation_fraction"] > 0.0
    assert invalid_diagnostics["per_constraint"]["offset_within_secondary_feed_support"]["violation_count"] == 1


def test_topology_penalty_gradient_matches_finite_difference():
    module = _load_module()
    columns = [f"geom__{name}" for name in module.TOPOLOGY_GEOMETRY_SEMANTICS]
    column_contract = module._topology_feasibility_column_contract(columns)
    normalization = {
        "y_mean": np.zeros(len(columns)),
        "y_scale": np.ones(len(columns)),
        "geometry_lower": np.zeros(len(columns)),
        "geometry_upper": np.full(len(columns), 300.0),
    }
    contract = module._configure_topology_feasibility(
        normalization,
        columns,
        column_contract,
        SimpleNamespace(topology_feasibility_weight=0.02),
    )
    geometry = np.asarray([[100.0, 100.0, 110.0, 110.0, 120.0, 90.0, 0.0, 100.0, 100.0]])
    penalty, gradient, _diagnostics = module._topology_feasibility_penalty_and_gradient(
        geometry, normalization, contract
    )
    assert penalty > 0.0
    terminal_index = module.TOPOLOGY_GEOMETRY_SEMANTICS.index("primary_terminal_y_span_um")
    epsilon = 1.0e-5
    plus = geometry.copy()
    minus = geometry.copy()
    plus[0, terminal_index] += epsilon
    minus[0, terminal_index] -= epsilon
    plus_penalty = module._topology_feasibility_penalty_and_gradient(plus, normalization, contract)[0]
    minus_penalty = module._topology_feasibility_penalty_and_gradient(minus, normalization, contract)[0]
    finite_difference = (plus_penalty - minus_penalty) / (2.0 * epsilon)
    assert gradient[0, terminal_index] == pytest.approx(finite_difference, rel=1.0e-5, abs=1.0e-8)


def test_training_records_label_free_topology_feasibility_objective(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_topology_training_csv(training_csv)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "200",
            "--forward-depth",
            "1",
            "--forward-width",
            "16",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "16",
            "--batch-size",
            "64",
            "--forward-epochs",
            "3",
            "--inverse-epochs",
            "3",
            "--patience",
            "2",
            "--topology-feasibility-weight",
            "0.02",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text())
    assert summary["checks"]["topology_feasibility_columns_present"] is True
    assert summary["method"]["topology_feasibility_weight"] == 0.02
    assert summary["method"]["topology_feasibility_is_label_free"] is True
    assert summary["method"]["topology_feasibility_contract"]["available"] is True
    assert "label_free_topology_feasibility" in summary["method"]["inverse_loss"]
    topology_metrics = summary["metrics"]["tandem_inverse"]["topology_feasibility"]
    assert topology_metrics["constraint_count"] == 8
    assert 0.0 <= topology_metrics["violation_fraction"] <= 1.0
    history_rows = list(csv.DictReader(Path(summary["history_csv"]).open(encoding="utf-8")))
    inverse_rows = [row for row in history_rows if row["stage"] == "tandem_inverse"]
    assert inverse_rows
    assert all(row["validation_topology_feasibility_penalty"] != "" for row in inverse_rows)


def test_does_not_claim_pass_without_predeclared_thresholds(tmp_path):
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
            "--forward-depth",
            "1",
            "--forward-width",
            "16",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "16",
            "--batch-size",
            "64",
            "--forward-epochs",
            "4",
            "--inverse-epochs",
            "4",
            "--patience",
            "2",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text(encoding="utf-8"))
    assert summary["overall_status"] == "COMPLETE_REVIEW_REQUIRED"
    assert summary["execution_status"] == "PASS"
    assert summary["quality_status"] == "REVIEW_REQUIRED_THRESHOLDS_NOT_CONFIGURED"
    assert summary["eligible_for_checkpoint_model_acceptance"] is False
    assert summary["eligible_for_model_success_claim"] is False
    assert summary["acceptance_thresholds"]["configured"] is False
    assert summary["decision"] == "COMPARE_WITH_DIRECT_BASELINE_AND_EMX_VERIFY"


def test_physical_cell_grouped_split_holds_out_complete_joint_feature_cells(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, rows=640)

    status = module.main(
        [
            "--training-csv",
            str(training_csv),
            "--out-dir",
            str(tmp_path / "out"),
            "--min-training-rows",
            "400",
            "--split-mode",
            "physical_cell_grouped",
            "--physical-cell-bins",
            "4",
            "--physical-cell-lower",
            "0.5,0.5,10,0.3",
            "--physical-cell-upper",
            "1.5,1.6,20,0.8",
            "--seed",
            "999",
            "--split-seed",
            "123",
            "--forward-depth",
            "1",
            "--forward-width",
            "16",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "16",
            "--batch-size",
            "128",
            "--forward-epochs",
            "3",
            "--inverse-epochs",
            "3",
            "--patience",
            "2",
            "--max-forward-test-rmse",
            "999",
            "--max-tandem-response-test-rmse",
            "999",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text(encoding="utf-8"))
    audit = summary["split_audit"]
    assert summary["overall_status"] == "PASS"
    assert summary["quality_status"] == "PASS"
    assert summary["evaluation_scope"]["formal_explicit_physical_cell_ood"] is True
    assert summary["eligible_for_checkpoint_model_acceptance"] is True
    assert summary["eligible_for_model_success_claim"] is False
    assert audit["split_mode"] == "physical_cell_grouped"
    assert audit["physical_cell_range_source"] == "explicit"
    assert audit["physical_cell_overlap_count"] == 0
    assert audit["all_rows_assigned_once"] is True
    assert audit["out_of_range_row_count_before_clipping"] == 0
    assert audit["physical_cell_partition_seed"] == 123
    assert isinstance(audit["physical_cell_partition_stable_for_existing_cells"], bool)
    assert sum(audit["row_counts"].values()) == summary["training_count"]
    assert all(count > 0 for count in audit["row_counts"].values())
    assert len(audit["split_fingerprint_sha256"]) == 64
    assert set(audit["split_index_sha256"]) == {"train", "validation", "test"}
    metrics = summary["metrics"]
    assert metrics["range_normalization"]["source"] == "declared_physical_cell_range"
    assert math.isfinite(metrics["forward_proxy"]["test_range_normalized_rmse"])
    assert math.isfinite(metrics["tandem_inverse"]["test_response_range_normalized_rmse"])


def test_rejects_invalid_robustness_noise_levels():
    module = _load_module()

    with pytest.raises(ValueError, match="finite fractions"):
        module._parse_noise_levels("0.01,1.1")


def test_adaptive_picc_schedule_has_warmup_ramp_and_bounded_ema_stage():
    module = _load_module()
    args = SimpleNamespace(
        inverse_epochs=100,
        response_warmup_fraction=0.05,
        response_ramp_fraction=0.20,
        response_weight_schedule="warmup_ramp_adaptive_ema",
        response_weight=1.0,
        response_adaptive_ema_decay=0.9,
        response_adaptive_min_multiplier=0.25,
        response_adaptive_max_multiplier=4.0,
    )
    state = module._init_response_schedule_state(args)
    assert module._response_weight_for_epoch(1, args, state) == (0.0, "warmup", 0.0)
    ramp_weight, ramp_phase, _ = module._response_weight_for_epoch(10, args, state)
    assert ramp_phase == "ramp"
    assert 0.0 < ramp_weight < 1.0

    module._update_response_schedule_state(state, 0.20, 0.40, 25, args)
    state["ema_response_mse"] = 0.01
    state["ema_geometry_mse"] = 10.0
    adaptive_weight, phase, multiplier = module._response_weight_for_epoch(26, args, state)
    assert phase == "adaptive_ema"
    assert multiplier == 4.0
    assert adaptive_weight == 4.0


def test_response_only_schedule_never_uses_geometry_label_ema():
    module = _load_module()
    args = SimpleNamespace(
        inverse_epochs=100,
        response_warmup_fraction=0.0,
        response_ramp_fraction=0.20,
        response_weight_schedule="warmup_ramp_adaptive_ema",
        response_weight=1.0,
        geometry_anchor_weight=0.0,
        response_adaptive_ema_decay=0.9,
        response_adaptive_min_multiplier=0.25,
        response_adaptive_max_multiplier=4.0,
    )
    state = module._init_response_schedule_state(args)
    module._update_response_schedule_state(state, 0.01, 1.0e6, 25, args)
    weight, phase, multiplier = module._response_weight_for_epoch(26, args, state)
    assert phase == "response_only_fixed"
    assert weight == 1.0
    assert multiplier == 1.0


def test_response_only_rejects_zero_gradient_warmup():
    module = _load_module()
    with pytest.raises(SystemExit):
        module._parse_args(
            [
                "--training-csv",
                "training.csv",
                "--out-dir",
                "out",
                "--geometry-anchor-weight",
                "0",
                "--response-warmup-fraction",
                "0.05",
            ]
        )


def test_response_only_summary_records_no_geometry_label_objective(tmp_path):
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
            "--forward-depth",
            "1",
            "--forward-width",
            "16",
            "--inverse-depth",
            "1",
            "--inverse-width",
            "16",
            "--batch-size",
            "64",
            "--forward-epochs",
            "3",
            "--inverse-epochs",
            "3",
            "--patience",
            "2",
            "--geometry-anchor-weight",
            "0",
            "--response-warmup-fraction",
            "0",
        ]
    )

    assert status == 0
    summary = json.loads((tmp_path / "out" / "physical_feature_tandem_inverse_summary.json").read_text())
    assert summary["method"]["inverse_loss"] == "feature_balanced_response_consistency_only"
    assert summary["method"]["geometry_anchor_weight"] == 0.0
    assert summary["method"]["geometry_label_used_in_inverse_objective"] is False
    history_rows = list(csv.DictReader(Path(summary["history_csv"]).open(encoding="utf-8")))
    phases = {row["response_weight_schedule_phase"] for row in history_rows if row["stage"] == "tandem_inverse"}
    assert "warmup" not in phases


def test_q_and_qp_qs_models_share_split_and_common_metric(tmp_path):
    module = _load_module()
    training_csv = tmp_path / "training.csv"
    _write_training_csv(training_csv, rows=640)
    common = [
        "--training-csv",
        str(training_csv),
        "--min-training-rows",
        "400",
        "--split-mode",
        "physical_cell_grouped",
        "--physical-cell-bins",
        "4",
        "--physical-cell-lower",
        "0.5,0.5,10,0.3",
        "--physical-cell-upper",
        "1.5,1.6,20,0.8",
        "--seed",
        "777",
        "--split-seed",
        "444",
        "--forward-depth",
        "1",
        "--forward-width",
        "12",
        "--inverse-depth",
        "1",
        "--inverse-width",
        "12",
        "--batch-size",
        "128",
        "--forward-epochs",
        "2",
        "--inverse-epochs",
        "2",
        "--patience",
        "1",
    ]
    assert module.main([*common, "--out-dir", str(tmp_path / "q")]) == 0
    assert (
        module.main(
            [
                *common,
                "--out-dir",
                str(tmp_path / "qp_qs"),
                "--input-columns",
                "input__lp_nh_center,input__ls_nh_center,input__qp_center,input__qs_center,input__k_abs_center",
                "--split-reference-columns",
                "input__lp_nh_center,input__ls_nh_center,input__q_center,input__k_abs_center",
            ]
        )
        == 0
    )

    q_summary = json.loads((tmp_path / "q" / "physical_feature_tandem_inverse_summary.json").read_text())
    qp_qs_summary = json.loads(
        (tmp_path / "qp_qs" / "physical_feature_tandem_inverse_summary.json").read_text()
    )
    assert q_summary["split_audit"]["split_fingerprint_sha256"] == qp_qs_summary["split_audit"][
        "split_fingerprint_sha256"
    ]
    q_common = q_summary["metrics"]["common_lp_ls_qmin_absk_contract"]
    qp_qs_common = qp_qs_summary["metrics"]["common_lp_ls_qmin_absk_contract"]
    assert q_common["status"] == "PASS"
    assert q_common["q_representation"] == "Q_scalar"
    assert qp_qs_common["status"] == "PASS"
    assert qp_qs_common["q_representation"] == "min_Qp_Qs"
    assert math.isfinite(qp_qs_common["test_range_normalized_rmse"])
