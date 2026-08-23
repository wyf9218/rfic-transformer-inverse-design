from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALUATOR = _load(
    "controlled_shared_fref_evaluator",
    "evaluate_controlled_tandem_shared_fref_fixed_targets.py",
)
STATISTICS = _load(
    "controlled_paired_statistics",
    "analyze_controlled_paired_replicates.py",
)
RUNNER = _load(
    "controlled_paired_runner",
    "run_controlled_paired_training.py",
)
FORWARD_EVALUATOR = _load(
    "controlled_forward_common_holdout",
    "evaluate_controlled_forward_common_holdout.py",
)
RESULT_PIPELINE = _load(
    "controlled_result_pipeline",
    "evaluate_and_analyze_controlled_results.py",
)


def _model(forward_offset: float = 0.0, inverse_offset: float = 0.0):
    return {
        "forward_weights": [
            np.arange(12, dtype=float).reshape(3, 4) + forward_offset,
            np.arange(8, dtype=float).reshape(4, 2) + forward_offset,
        ],
        "forward_biases": [
            np.arange(4, dtype=float) + forward_offset,
            np.arange(2, dtype=float) + forward_offset,
        ],
        "inverse_weights": [np.asarray([[inverse_offset]], dtype=float)],
        "inverse_biases": [np.asarray([inverse_offset], dtype=float)],
    }


def test_forward_component_digest_binds_forward_and_ignores_inverse() -> None:
    base = EVALUATOR._canonical_forward_component_sha256(_model())
    assert base == EVALUATOR._canonical_forward_component_sha256(
        _model(inverse_offset=10.0)
    )
    assert base != EVALUATOR._canonical_forward_component_sha256(
        _model(forward_offset=1.0)
    )


def _write_forward_only_fref(path: Path, *, include_inverse: bool = False) -> None:
    arrays = {
        "forward_weight_0": np.zeros((10, 128)),
        "forward_weight_1": np.zeros((128, 128)),
        "forward_weight_2": np.zeros((128, 4)),
        "forward_bias_0": np.zeros(128),
        "forward_bias_1": np.zeros(128),
        "forward_bias_2": np.zeros(4),
        "normalization__x_mean": np.zeros(4),
        "normalization__x_scale": np.ones(4),
        "normalization__y_mean": np.zeros(10),
        "normalization__y_scale": np.ones(10),
        "normalization__geometry_lower": -np.ones(10),
        "normalization__geometry_upper": np.ones(10),
        "normalization__dimension_weights": np.ones(4),
    }
    if include_inverse:
        arrays["inverse_weight_0"] = np.zeros((4, 10))
    np.savez_compressed(path, **arrays)


def test_forward_only_fref_loader_accepts_exact_order_and_normalization(tmp_path: Path) -> None:
    path = tmp_path / "fref.npz"
    _write_forward_only_fref(path)
    model = EVALUATOR._load_forward_only_fref(path)
    assert EVALUATOR._canonical_forward_component_sha256(model)
    assert [value.shape for value in model["forward_weights"]] == [
        (10, 128),
        (128, 128),
        (128, 4),
    ]


def test_forward_only_fref_loader_rejects_inverse_arrays(tmp_path: Path) -> None:
    path = tmp_path / "fref_with_inverse.npz"
    _write_forward_only_fref(path, include_inverse=True)
    with pytest.raises(ValueError, match="inverse arrays"):
        EVALUATOR._load_forward_only_fref(path)


def _paired_rows():
    rows = []
    for replicate, small, large in zip(
        range(1, 6),
        [10.0, 11.0, 12.0, 13.0, 14.0],
        [9.0, 10.5, 10.0, 12.0, 12.5],
    ):
        rows.append(
            {
                "estimand_id": "RQ_I",
                "metric_id": "rmse",
                "metric_label": "Range RMSE",
                "panel": "legacy8000",
                "role": "primary",
                "unit": "fraction",
                "better_direction": "lower",
                "replicate": str(replicate),
                "small_value": str(small),
                "large_value": str(large),
            }
        )
    return rows


def test_paired_statistics_uses_five_deltas_and_positive_is_better() -> None:
    result = STATISTICS._analyze_group(_paired_rows())
    expected_raw = np.asarray([-1.0, -0.5, -2.0, -1.0, -1.5])
    np.testing.assert_allclose(
        result["raw_delta_large_minus_small"]["values"], expected_raw
    )
    np.testing.assert_allclose(
        result["signed_improvement_positive_is_better"]["values"], -expected_raw
    )
    assert result["signed_improvement_positive_is_better"]["mean"] == 1.2
    assert (
        result["signed_improvement_positive_is_better"][
            "paired_bootstrap_sensitivity"
        ]["seed"]
        == 2026082202
    )


def test_paired_statistics_plot_exposes_bars_and_all_pairs(tmp_path: Path) -> None:
    result = STATISTICS._analyze_group(_paired_rows())
    output = tmp_path / "paired"
    STATISTICS._write_plot(result, output)
    assert output.with_suffix(".png").stat().st_size > 0
    assert output.with_suffix(".svg").stat().st_size > 0


def test_paired_runner_freezes_equal_update_and_validation_cadence(tmp_path: Path) -> None:
    command = RUNNER._base_command(
        python=Path("/venv/python"),
        trainer=Path("/runtime/trainer.py"),
        training_csv=Path("/data/arm.csv"),
        run_dir=tmp_path / "run",
        train_rows=100000,
        seed=20260711,
        holdout=Path("/data/holdout.json"),
        normalization=Path("/runtime/normalization.json"),
    )

    def value(option: str) -> str:
        return command[command.index(option) + 1]

    assert value("--batch-size") == "4096"
    assert value("--exact-update-batch-mode") == "continuous_permutation_full_batch"
    assert value("--validation-every-optimizer-updates") == "40"
    assert value("--response-schedule-domain") == "optimizer_update"
    assert value("--response-warmup-optimizer-updates") == "240"
    assert value("--response-ramp-optimizer-updates") == "960"
    assert value("--q-target-semantics") == "exact"
    assert value("--evaluation-mode") == "validation_only"
    assert value("--local-refinement-steps") == "0"


def test_common_holdout_forward_metrics_use_declared_ranges() -> None:
    truth = np.asarray([[1.0, 1.0, 10.0, 0.2], [2.0, 2.0, 20.0, 0.6]])
    prediction = truth + np.asarray([[0.25, -0.5, 2.0, 0.08], [-0.25, 0.5, -2.0, -0.08]])
    result = FORWARD_EVALUATOR._metrics(truth, prediction)
    expected_normalized = np.asarray(
        [[0.1, -0.2, 0.1, 0.1], [-0.1, 0.2, -0.1, -0.1]]
    )
    assert result["row_count"] == 2
    np.testing.assert_allclose(
        result["joint_declared_range_normalized_rmse"],
        np.sqrt(np.mean(expected_normalized**2)),
    )
    np.testing.assert_allclose(result["per_feature"]["lp_nh"]["mae_physical"], 0.25)


def test_result_pipeline_metric_rows_match_statistics_schema() -> None:
    row = RESULT_PIPELINE._metric_row(
        estimand="RQ_I_shared_F_ref",
        metric_id="rmse",
        label="Range RMSE",
        panel="legacy8000",
        role="primary",
        unit="fraction",
        direction="lower",
        replicate=1,
        small=0.2,
        large=0.1,
    )
    assert tuple(row) == STATISTICS.REQUIRED_COLUMNS
    assert row["small_value"] == 0.2
    assert row["large_value"] == 0.1
