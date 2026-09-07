"""Analytic synthetic checks for fixed-denominator research metric definitions."""

import json

import numpy as np
import pytest

from research.broadband56_nn.metrics import forward_metrics, inverse_metrics


def frequencies():
    return np.arange(5, 61, dtype=np.float64) * 1e9


def test_forward_real_imag_channel_order_and_complex_rmse():
    predicted = np.zeros((2, 56, 32))
    predicted[..., 0], predicted[..., 1] = 3.0, 4.0
    result = forward_metrics(predicted, np.zeros_like(predicted), np.ones(32), frequencies())
    assert result["channel_order"][:4] == ["s11_re", "s11_im", "s12_re", "s12_im"]
    assert result["channel_mae"][:2] == [3.0, 4.0]
    assert result["complex_channel_rmse"][0] == 5.0
    assert result["complex_channel_rmse"][1:] == [0.0] * 15
    assert result["raw_complex_rmse"] == 1.25
    assert result["shared_scale_nrmse"] == pytest.approx(np.sqrt(25 / 32))
    assert result["per_geometry"]["p95"] == pytest.approx(np.sqrt(25 / 32))
    json.dumps(result, allow_nan=False)


def test_forward_equal_geometry_weighting_bands_and_frequency_counts():
    errors = np.arange(56, dtype=float) + 1
    predicted = np.broadcast_to(errors[None, :, None], (2, 56, 32)).copy()
    result = forward_metrics(predicted, np.zeros_like(predicted), np.ones(32), frequencies())
    np.testing.assert_allclose(result["per_frequency"]["shared_scale_nrmse"], errors)
    assert [band["frequency_count"] for band in result["frequency_bands"]] == [15, 20, 21]
    for band, selected in zip(result["frequency_bands"], (errors[:15], errors[15:35], errors[35:])):
        assert band["shared_scale_nrmse"] == pytest.approx(np.sqrt(np.mean(selected ** 2)))
        assert band["real_channel_observations"] == 2 * len(selected) * 32
    assert result["real_channel_observations"] == 2 * 56 * 32


def test_reciprocity_uses_six_unique_pairs_and_passivity_is_sampled_only():
    predicted = np.zeros((1, 56, 32))
    predicted[..., 2] = 2.0  # S12 real; S21 remains zero.
    result = forward_metrics(predicted, np.zeros_like(predicted), np.ones(32), frequencies())
    reciprocity = result["reciprocity"]["predicted"]
    assert reciprocity["complex_pair_observations"] == 56 * 6
    assert reciprocity["mean_absolute_residual"] == pytest.approx(2 / 6)
    assert reciprocity["complex_rmse"] == pytest.approx(np.sqrt(4 / 6))
    passivity = result["sampled_passivity"]["predicted"]
    assert passivity["samples_exceeding_one"] == 56
    assert passivity["maximum_singular_value"] == 2.0
    assert passivity["continuous_frequency_passivity_proven"] is False
    predicted[..., 2] = 1.0
    exact_one = forward_metrics(predicted, np.zeros_like(predicted), np.ones(32), frequencies())
    assert exact_one["sampled_passivity"]["predicted"]["samples_exceeding_one"] == 0


def test_forward_rejects_wrong_grid_nonfinite_and_zero_scale():
    zero = np.zeros((1, 56, 32))
    with pytest.raises(ValueError, match="strictly positive"):
        forward_metrics(zero, zero, np.zeros(32), frequencies())
    with pytest.raises(ValueError, match="exact 5..60"):
        forward_metrics(zero, zero, np.ones(32), frequencies() + 1e6)
    invalid = zero.copy()
    invalid[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        forward_metrics(invalid, zero, np.ones(32), frequencies())


def test_inverse_zero_targets_use_fixed_scale_and_fixed_denominator():
    predicted = np.array([[[0.0, 2.0]], [[2.0, 4.0]]])
    result = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool), np.array([2.0, 4.0]))
    error = np.array([0.0, 0.5, 1.0, 1.0])
    assert result["normalized_rmse"] == pytest.approx(np.sqrt(np.mean(error ** 2)))
    assert result["normalized_mae"] == pytest.approx(error.mean())
    assert result["condition_violation_count"] == 3
    assert result["condition_violation_denominator"] == 4
    assert result["condition_violation_rate"] == 0.75
    assert result["target_relative_percentage_error_used"] is False


def test_inverse_relations_single_sided_with_tolerance_in_scale_units():
    predicted = np.array([[[2.0, -2.0, -3.0, 3.0, 0.25]]])
    relation = np.array([[[1, 2, 1, 2, 0]]])
    result = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool),
                             np.ones(5), tolerance_fraction=0.25, relation=relation)
    assert result["normalized_mae"] == (0 + 0 + 3 + 3 + 0.25) / 5
    assert result["condition_violation_count"] == 2  # Equality at tolerance is accepted.
    assert result["relation_requested_counts"] == {"EQ": 1, "LOWER": 2, "UPPER": 2}
    all_lower = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool), np.ones(5), relation="LOWER")
    assert all_lower["relation_requested_counts"]["LOWER"] == 5


def test_invalid_requested_predictions_are_failures_not_dropped_or_zero_errors():
    predicted = np.array([[[0.0, np.nan]], [[0.0, 0.0]]])
    declared_valid = np.array([[[True, True]], [[False, True]]])
    result = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool),
                             np.ones(2), prediction_valid=declared_valid)
    assert result["requested_conditions"] == 4
    assert result["invalid_requested_predictions"] == 2
    assert result["condition_violation_rate"] == 0.5
    assert result["normalized_rmse"] is None
    assert result["normalized_mae"] is None
    assert result["p95_per_geometry_normalized_rmse"] is None
    assert result["valid_requested_numeric"]["condition_denominator"] == 2
    assert result["valid_requested_numeric"]["normalized_rmse"] == 0.0
    assert result["per_geometry"]["values"] == [None, None]
    assert result["per_geometry"]["fully_evaluable_distribution"]["count"] == 0
    json.dumps(result, allow_nan=False)


def test_absent_nan_labels_and_predictions_never_enter_arithmetic_or_denominator():
    predicted = np.array([[[1.0, np.nan]]])
    target = np.array([[[0.0, np.nan]]])
    mask = np.array([[[True, False]]])
    result = inverse_metrics(predicted, target, mask, np.ones(2))
    assert result["requested_conditions"] == 1
    assert result["normalized_rmse"] == 1.0
    assert result["invalid_requested_predictions"] == 0
    json.dumps(result, allow_nan=False)


def test_all_invalid_retains_fixed_denominator_and_null_numeric_values():
    predicted = np.full((2, 3, 4), np.nan)
    result = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool), np.ones(4))
    assert result["requested_conditions"] == 24
    assert result["condition_violation_count"] == 24
    assert result["condition_violation_rate"] == 1.0
    assert result["valid_requested_numeric"]["condition_denominator"] == 0
    assert result["valid_requested_numeric"]["normalized_rmse"] is None
    json.dumps(result, allow_nan=False)


def test_partial_valid_geometry_p95_is_explicitly_conditional():
    predicted = np.array([[[1.0]], [[2.0]], [[np.nan]]])
    result = inverse_metrics(predicted, np.zeros_like(predicted), np.ones_like(predicted, dtype=bool), np.ones(1))
    distribution = result["per_geometry"]["fully_evaluable_distribution"]
    assert distribution["count"] == 2
    assert distribution["p95"] == pytest.approx(1.95)
    assert result["p95_per_geometry_normalized_rmse"] is None
    assert result["per_geometry"]["values"] == [1.0, 2.0, None]


def test_inverse_rejects_invalid_contracts_and_target_values():
    zero = np.zeros((1, 2, 3))
    mask = np.ones_like(zero, dtype=bool)
    with pytest.raises(ValueError, match="all-empty"):
        inverse_metrics(zero, zero, ~mask, np.ones(3))
    with pytest.raises(ValueError, match="strictly positive"):
        inverse_metrics(zero, zero, mask, np.zeros(3))
    with pytest.raises(ValueError, match="nonnegative"):
        inverse_metrics(zero, zero, mask, np.ones(3), tolerance_fraction=-1)
    with pytest.raises(ValueError, match="finite"):
        inverse_metrics(zero, np.full_like(zero, np.nan), mask, np.ones(3))
    with pytest.raises(ValueError, match="relation"):
        inverse_metrics(zero, zero, mask, np.ones(3), relation=4)
    with pytest.raises(ValueError, match="boolean"):
        inverse_metrics(zero, zero, mask, np.ones(3), prediction_valid=np.ones_like(zero))
