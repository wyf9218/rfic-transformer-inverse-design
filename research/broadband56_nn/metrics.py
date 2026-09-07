"""Pure NumPy Broadband56 metrics with explicit scientific denominators.

Channel order matches ``data.S_COLUMNS``: row-major S11..S44, interleaved
real/imaginary components. Forward metrics assume the caller already verified
the common real 50-ohm power-wave port contract. Sampled singular values do not
prove continuous-frequency passivity, causality, manufacturing or fresh EMX.

Inverse error uses fixed train-derived/design channel scales, never the target
as a divisor. Invalid requested predictions count as violations, not as zero
error and not as dropped requests. Unavailable numerical errors remain None.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np


S_CHANNEL_ORDER = [f"s{row}{column}_{part}" for row in range(1, 5)
                   for column in range(1, 5) for part in ("re", "im")]
COMPLEX_CHANNEL_ORDER = [f"s{row}{column}" for row in range(1, 5) for column in range(1, 5)]
FREQUENCY_HZ = np.arange(5, 61, dtype=np.float64) * 1e9


def _real_array(value: Any, name: str) -> np.ndarray:
    if np.iscomplexobj(value):
        raise ValueError(f"{name} must contain real-valued channels, not complex values")
    return np.asarray(value, dtype=np.float64)


def _scale(value: Any, channels: int) -> np.ndarray:
    scale = _real_array(value, "channel scale")
    if scale.shape != (channels,) or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError("channel scale must be finite, strictly positive and shape [C]; use the frozen scale floor")
    return scale


def _rms(value: np.ndarray, axis: Any = None) -> np.ndarray:
    """Scaled RMS avoids squaring large finite residuals before reduction."""
    magnitude = np.abs(value)
    maximum = np.max(magnitude, axis=axis, keepdims=True)
    safe_maximum = np.where(maximum > 0, maximum, 1.0)
    result = maximum * np.sqrt(np.mean((magnitude / safe_maximum) ** 2, axis=axis, keepdims=True))
    return np.squeeze(result, axis=axis)


def _distribution(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("distribution input must be one-dimensional")
    return {
        "count": int(values.size),
        "median": float(np.median(values)) if values.size else None,
        "p95": float(np.percentile(values, 95, method="linear")) if values.size else None,
        "max": float(np.max(values)) if values.size else None,
        "percentile_method": "linear",
    }


def _json_ready(document: dict[str, Any]) -> dict[str, Any]:
    # A numerical overflow is a failure, never a serialized NaN/Infinity result.
    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("metric output is not finite JSON; check numerical input range") from exc
    return document


def _reciprocity(s: np.ndarray) -> dict[str, Any]:
    row, column = np.triu_indices(4, k=1)
    residual = s[..., row, column] - s[..., column, row]
    magnitude = np.abs(residual)
    return {
        "definition": "S_ij-S_ji for six unique off-diagonal pairs, in the same port normalization",
        "pair_order": [f"s{r + 1}{c + 1}-s{c + 1}{r + 1}" for r, c in zip(row, column)],
        "complex_pair_observations": int(magnitude.size),
        "mean_absolute_residual": float(magnitude.mean()),
        "complex_rmse": float(_rms(magnitude)),
        "maximum_absolute_residual": float(magnitude.max()),
    }


def _sampled_passivity(s: np.ndarray) -> dict[str, Any]:
    singular_max = np.linalg.svd(s, compute_uv=False)[..., 0]
    exceed = singular_max > 1.0
    return {
        "definition": "largest singular value of the four-port S matrix strictly greater than 1",
        "threshold": 1.0, "numerical_threshold_margin": 0.0,
        "frequency_samples": int(exceed.size),
        "samples_exceeding_one": int(exceed.sum()),
        "sample_exceedance_rate": float(exceed.mean()),
        "geometries_with_any_exceedance": int(exceed.any(axis=1).sum()),
        "geometry_count": int(s.shape[0]),
        "maximum_singular_value": float(singular_max.max()),
        "scope": "sampled_grid_only; requires verified common real positive power-wave reference impedances",
        "continuous_frequency_passivity_proven": False,
        "fresh_emx_generated_geometry_validation": False,
    }


def forward_metrics(
    predicted_s: np.ndarray,
    target_s: np.ndarray,
    channel_scale: np.ndarray,
    frequency_hz: np.ndarray,
) -> dict[str, Any]:
    """Score a full finite [N,56,32] four-port response on the frozen 1-GHz grid.

    Per-geometry tails use shared-scale full-spectrum RMSE; every geometry has
    the same 56x32 denominator. Physical-feature metrics belong to the separately
    parity-validated physical extractor and are intentionally not guessed here.
    """
    predicted, target = _real_array(predicted_s, "predicted_s"), _real_array(target_s, "target_s")
    if predicted.ndim != 3 or predicted.shape[0] == 0 or predicted.shape[1:] != (56, 32):
        raise ValueError("predicted_s must have nonempty shape [N,56,32]")
    if target.shape != predicted.shape or not np.isfinite(predicted).all() or not np.isfinite(target).all():
        raise ValueError("target_s must match predicted_s; all full-spectrum S channels must be finite")
    frequencies = _real_array(frequency_hz, "frequency_hz")
    if frequencies.shape != (56,) or not np.array_equal(frequencies, FREQUENCY_HZ):
        raise ValueError("frequency_hz must be the exact 5..60 GHz, 1-GHz, 56-point grid")
    scale = _scale(channel_scale, 32)
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        residual = predicted - target
        normalized = residual / scale
        pairs = residual.reshape(-1, 56, 16, 2)
        complex_error_magnitude = np.hypot(pairs[..., 0], pairs[..., 1])
        per_geometry = _rms(normalized, axis=(1, 2))
        per_frequency = _rms(normalized, axis=(0, 2))
        predicted_pairs = predicted.reshape(-1, 56, 4, 4, 2)
        target_pairs = target.reshape(-1, 56, 4, 4, 2)
        predicted_complex = predicted_pairs[..., 0] + 1j * predicted_pairs[..., 1]
        target_complex = target_pairs[..., 0] + 1j * target_pairs[..., 1]
        bands = []
        for label, low, high, inclusive_high in (
            ("5–<20 GHz", 5e9, 20e9, False),
            ("20–<40 GHz", 20e9, 40e9, False),
            ("40–60 GHz", 40e9, 60e9, True),
        ):
            selected = (frequencies >= low) & ((frequencies <= high) if inclusive_high else (frequencies < high))
            bands.append({
                "label": label, "lower_hz": low, "upper_hz": high,
                "upper_inclusive": inclusive_high, "geometry_count": int(predicted.shape[0]),
                "frequency_count": int(selected.sum()),
                "real_channel_observations": int(predicted[:, selected].size),
                "shared_scale_nrmse": float(_rms(normalized[:, selected])),
                "raw_real_imag_mae": float(np.abs(residual[:, selected]).mean()),
                "raw_complex_rmse": float(_rms(complex_error_magnitude[:, selected])),
            })
        return _json_ready({
            "schema": "broadband56_forward_metrics.v1",
            "evidence_class": "heldout_response_prediction; not generated-geometry fresh EMX",
            "geometry_count": int(predicted.shape[0]), "frequency_count": 56,
            "real_channel_count": 32, "complex_channel_count": 16,
            "real_channel_observations": int(predicted.size),
            "channel_order": S_CHANNEL_ORDER, "complex_channel_order": COMPLEX_CHANNEL_ORDER,
            "channel_mae": np.abs(residual).mean(axis=(0, 1)).tolist(),
            "complex_channel_rmse": _rms(complex_error_magnitude, axis=(0, 1)).tolist(),
            "raw_real_imag_mae": float(np.abs(residual).mean()),
            "raw_complex_rmse": float(_rms(complex_error_magnitude)),
            "shared_scale_nrmse": float(_rms(normalized)),
            "normalization": "fixed supplied positive channel scales; no target-relative denominator",
            "channel_scale": scale.tolist(),
            "per_geometry": {
                "metric": "shared_scale_full_spectrum_rmse",
                "real_channel_observations_per_geometry": 56 * 32,
                **_distribution(per_geometry), "values": per_geometry.tolist(),
            },
            "per_frequency": {
                "frequency_hz": frequencies.tolist(),
                "real_channel_observations_per_frequency": int(predicted.shape[0] * 32),
                "shared_scale_nrmse": per_frequency.tolist(),
                "raw_complex_rmse": _rms(complex_error_magnitude, axis=(0, 2)).tolist(),
                "raw_real_imag_mae": np.abs(residual).mean(axis=(0, 2)).tolist(),
            },
            "frequency_bands": bands,
            "reciprocity": {"predicted": _reciprocity(predicted_complex), "target": _reciprocity(target_complex)},
            "sampled_passivity": {"predicted": _sampled_passivity(predicted_complex), "target": _sampled_passivity(target_complex)},
        })


def _broadcast_boolean(value: Any, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype != np.bool_:
        raise ValueError(f"{name} must be a boolean mask")
    if array.shape == shape[:2]:
        array = array[..., None]
    elif array.shape == shape[:1]:
        array = array[:, None, None]
    try:
        return np.broadcast_to(array, shape)
    except ValueError as exc:
        raise ValueError(f"{name} shape cannot broadcast to requested response shape") from exc


def inverse_metrics(
    predicted_response: np.ndarray,
    target: np.ndarray,
    condition_mask: np.ndarray,
    scale: np.ndarray,
    prediction_valid: np.ndarray | None = None,
    tolerance_fraction: float = 0.05,
    relation: np.ndarray | str | int | None = None,
) -> dict[str, Any]:
    """Score requested EQ/LOWER/UPPER conditions without dropping invalid outputs.

    Relation codes: 0=EQ, 1=LOWER, 2=UPPER. Tolerance is in fixed channel-scale
    units, not a percentage of a potentially zero target. EQ error is absolute
    normalized deviation; bounds use normalized one-sided shortfall/excess.
    Global numerical errors are None if *any* requested prediction is invalid.
    Valid-only diagnostics have explicitly separate condition denominators.
    """
    predicted, targets = _real_array(predicted_response, "predicted_response"), _real_array(target, "target")
    if predicted.ndim != 3 or min(predicted.shape) < 1 or targets.shape != predicted.shape:
        raise ValueError("predicted_response and target must have matching nonempty [N,F,C] shape")
    conditions = np.asarray(condition_mask)
    if conditions.dtype != np.bool_ or conditions.shape != predicted.shape:
        raise ValueError("condition_mask must be bool with the exact [N,F,C] response shape")
    requested_per_geometry = conditions.sum(axis=(1, 2))
    if np.any(requested_per_geometry == 0):
        raise ValueError("all-empty geometry specification is not a metric sample")
    if not np.isfinite(targets[conditions]).all():
        raise ValueError("requested target values must be finite")
    channels = _scale(scale, predicted.shape[-1])
    tolerance = np.asarray(tolerance_fraction, dtype=np.float64)
    if tolerance.ndim != 0 or not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance_fraction must be a finite nonnegative scalar in fixed-scale units")
    if relation is None:
        relations = np.zeros(predicted.shape, dtype=np.int8)
    else:
        raw_relation = np.asarray(relation)
        if raw_relation.dtype.kind in {"U", "S", "O"}:
            mapping = {"EQ": 0, "LOWER": 1, "UPPER": 2}
            try:
                raw_relation = np.vectorize(mapping.__getitem__, otypes=[np.int8])(raw_relation)
            except KeyError as exc:
                raise ValueError("unknown relation; use EQ/LOWER/UPPER or 0/1/2") from exc
        try:
            relations = np.broadcast_to(raw_relation, predicted.shape)
        except ValueError as exc:
            raise ValueError("relation shape cannot broadcast to response shape") from exc
        if not np.isin(relations[conditions], [0, 1, 2]).all():
            raise ValueError("requested relation codes must be EQ=0, LOWER=1, UPPER=2")
    finite_prediction = np.isfinite(predicted)
    declared_valid = (np.ones(predicted.shape, dtype=bool) if prediction_valid is None
                      else _broadcast_boolean(prediction_valid, predicted.shape, "prediction_valid"))
    evaluable = conditions & finite_prediction & declared_valid
    invalid = conditions & ~evaluable
    requested_count, valid_count, invalid_count = int(conditions.sum()), int(evaluable.sum()), int(invalid.sum())
    # Mask *before* arithmetic: absent labels may be NaN and are not constraints.
    safe_prediction = np.where(evaluable, predicted, 0.0)
    safe_target = np.where(evaluable, targets, 0.0)
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        signed_error = (safe_prediction - safe_target) / channels
        error = np.where(relations == 0, np.abs(signed_error),
                         np.where(relations == 1, np.maximum(-signed_error, 0.0), np.maximum(signed_error, 0.0)))
    violating = invalid | (evaluable & (error > float(tolerance)))
    valid_values = error[evaluable]
    valid_rmse = float(_rms(valid_values)) if valid_count else None
    valid_mae = float(valid_values.mean()) if valid_count else None
    fully_valid = ~invalid.any(axis=(1, 2))
    per_geometry_values: list[float | None] = []
    for row in range(predicted.shape[0]):
        per_geometry_values.append(float(_rms(error[row][conditions[row]])) if fully_valid[row] else None)
    complete_values = np.asarray([value for value in per_geometry_values if value is not None], dtype=np.float64)
    complete_distribution = _distribution(complete_values)
    return _json_ready({
        "schema": "broadband56_inverse_metrics.v1",
        "evidence_class": "requested_response_proxy; not generated-geometry fresh EMX",
        "geometry_count": int(predicted.shape[0]),
        "requested_conditions": requested_count,
        "evaluable_requested_predictions": valid_count,
        "invalid_requested_predictions": invalid_count,
        "nonfinite_requested_predictions": int((conditions & ~finite_prediction).sum()),
        "declared_invalid_requested_predictions": int((conditions & ~declared_valid).sum()),
        "invalid_category_counts_overlap": True,
        "numerical_scope": "all requested conditions" if invalid_count == 0 else "full requested numerical errors unavailable; no imputation",
        "normalized_rmse": valid_rmse if invalid_count == 0 else None,
        "normalized_mae": valid_mae if invalid_count == 0 else None,
        "p95_per_geometry_normalized_rmse": complete_distribution["p95"] if invalid_count == 0 else None,
        "valid_requested_numeric": {
            "scope": "conditional on evaluable requested predictions; not the fixed-request success denominator",
            "condition_denominator": valid_count, "normalized_rmse": valid_rmse,
            "normalized_mae": valid_mae,
        },
        "per_geometry": {
            "metric": "requested_condition_normalized_rmse",
            "values": per_geometry_values,
            "requested_condition_counts": requested_per_geometry.tolist(),
            "invalid_requested_counts": invalid.sum(axis=(1, 2)).tolist(),
            "fully_evaluable_distribution": {"scope": "only geometries with every requested prediction evaluable", **complete_distribution},
        },
        "condition_violation_count": int(violating.sum()),
        "condition_violation_denominator": requested_count,
        "condition_violation_rate": float(violating.sum() / requested_count),
        "invalid_predictions_count_as_violations": True,
        "geometries_with_any_condition_violation": int(violating.any(axis=(1, 2)).sum()),
        "geometry_any_violation_rate": float(violating.any(axis=(1, 2)).mean()),
        "tolerance_fraction_of_channel_scale": float(tolerance),
        "channel_scale": channels.tolist(),
        "error_definition": "EQ: abs(pred-target)/scale; LOWER: max(target-pred,0)/scale; UPPER: max(pred-target,0)/scale",
        "violation_definition": "invalid prediction OR normalized relation-specific error > tolerance_fraction",
        "relation_requested_counts": {name: int((conditions & (relations == code)).sum()) for name, code in (("EQ", 0), ("LOWER", 1), ("UPPER", 2))},
        "target_relative_percentage_error_used": False,
    })
