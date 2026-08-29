"""Auditable temporary forward ensemble for broadband56 acquisition.

The ensemble may rank unevaluated geometries.  It never creates accepted
samples, EMX labels, or physical-coverage evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .broadband56_balanced200k import ANCHOR_FREQUENCIES_GHZ, GEOMETRY_FIELDS


ENSEMBLE_RECEIPT_SCHEMA = "broadband56_acquisition_ensemble_receipt_v1"
MODEL_SCHEMA = "broadband56_random_feature_ridge_member_v1"
PREDICTED_FEATURES = (
    "xp_ohm",
    "xs_ohm",
    "qp",
    "qs",
    "qmin",
    "k_abs",
    "feature_validity_probability",
)
CONTINUOUS_FEATURES = PREDICTED_FEATURES[:-1]
DEFAULT_MEMBER_SEEDS = (2026082801, 2026082802, 2026082803, 2026082804, 2026082805)
DEFAULT_HIDDEN_FEATURES = 128
DEFAULT_RIDGE = 1.0e-3
DEFAULT_TRAIN_FRACTION = 0.80
DEFAULT_CALIBRATION_FRACTION = 0.10
DEFAULT_INTERVAL_TARGET = 0.90
MINIMUM_MEMBERS = 5

OUTPUT_OFFSETS = np.asarray((130.0, 130.0, 18.5, 18.5, 18.5, 0.45), dtype=float)
OUTPUT_SCALES = np.asarray((120.0, 120.0, 16.5, 16.5, 16.5, 0.40), dtype=float)
OUTPUT_RANGES = 2.0 * OUTPUT_SCALES
VALIDATION_LIMITS = {
    "mean_range_normalized_mae_max": 0.20,
    "worst_feature_range_normalized_mae_max": 0.35,
    "minimum_scaled_interval_coverage": 0.80,
    "validity_brier_max": 0.25,
    "validity_accuracy_min": 0.50,
    "minimum_nonzero_disagreement_fraction": 0.50,
}


@dataclass(frozen=True)
class GeometrySplit:
    train_indices: np.ndarray
    calibration_indices: np.ndarray
    validation_indices: np.ndarray
    train_hash_sha256: str
    calibration_hash_sha256: str
    validation_hash_sha256: str


@dataclass(frozen=True)
class RandomFeatureMember:
    seed: int
    projection: np.ndarray
    bias: np.ndarray
    continuous_coefficients: np.ndarray
    validity_coefficients: np.ndarray
    valid_training_counts: np.ndarray
    training_geometry_count: int
    hidden_features: int
    ridge: float


def deterministic_geometry_split(
    geometry_hashes: Sequence[str],
    *,
    split_seed: int,
    train_fraction: float = DEFAULT_TRAIN_FRACTION,
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
) -> GeometrySplit:
    """Create exact, disjoint train/calibration/sealed-validation identities."""

    hashes = tuple(str(value).lower() for value in geometry_hashes)
    count = len(hashes)
    if count < 10 or len(set(hashes)) != count or any(not _is_sha256(value) for value in hashes):
        raise ValueError("geometry hashes must contain at least ten unique SHA-256 identities")
    if not 0.0 < train_fraction < 1.0 or not 0.0 < calibration_fraction < 1.0:
        raise ValueError("split fractions must lie strictly inside (0,1)")
    if train_fraction + calibration_fraction >= 1.0:
        raise ValueError("train and calibration fractions leave no sealed validation set")

    ordering = sorted(
        range(count),
        key=lambda index: hashlib.sha256(
            f"{int(split_seed)}|{hashes[index]}".encode("ascii")
        ).digest(),
    )
    train_count = max(1, int(math.floor(count * train_fraction)))
    calibration_count = max(1, int(math.floor(count * calibration_fraction)))
    validation_count = count - train_count - calibration_count
    if validation_count < 1:
        raise ValueError("sealed validation split is empty")
    train = np.asarray(ordering[:train_count], dtype=np.int64)
    calibration = np.asarray(ordering[train_count : train_count + calibration_count], dtype=np.int64)
    validation = np.asarray(ordering[train_count + calibration_count :], dtype=np.int64)
    return GeometrySplit(
        train_indices=train,
        calibration_indices=calibration,
        validation_indices=validation,
        train_hash_sha256=_identity_digest(hashes[index] for index in train),
        calibration_hash_sha256=_identity_digest(hashes[index] for index in calibration),
        validation_hash_sha256=_identity_digest(hashes[index] for index in validation),
    )


def fit_random_feature_member(
    *,
    geometry_normalized: np.ndarray,
    continuous_targets: np.ndarray,
    validity_targets: np.ndarray,
    train_indices: np.ndarray,
    seed: int,
    hidden_features: int = DEFAULT_HIDDEN_FEATURES,
    ridge: float = DEFAULT_RIDGE,
    bootstrap_fraction: float = 1.0,
) -> RandomFeatureMember:
    """Fit one independently seeded random-feature ridge forward model."""

    geometry, continuous, validity = _training_arrays(
        geometry_normalized, continuous_targets, validity_targets
    )
    indices = np.asarray(train_indices, dtype=np.int64)
    if indices.ndim != 1 or indices.size < 2 or np.any(indices < 0) or np.any(indices >= len(geometry)):
        raise ValueError("train_indices are invalid")
    if int(hidden_features) < 4 or not math.isfinite(float(ridge)) or float(ridge) <= 0.0:
        raise ValueError("hidden_features and ridge must be positive")
    if not 0.0 < float(bootstrap_fraction) <= 1.0:
        raise ValueError("bootstrap_fraction must lie in (0,1]")

    rng = np.random.default_rng(int(seed))
    projection = rng.normal(
        loc=0.0,
        scale=1.0 / math.sqrt(geometry.shape[1]),
        size=(geometry.shape[1], int(hidden_features)),
    )
    bias = rng.uniform(-math.pi, math.pi, size=int(hidden_features))
    sample_count = max(2, int(round(indices.size * float(bootstrap_fraction))))
    bootstrap = rng.choice(indices, size=sample_count, replace=True)
    basis = random_feature_basis(geometry[bootstrap], projection, bias)

    continuous_coefficients = np.zeros(
        (len(ANCHOR_FREQUENCIES_GHZ), basis.shape[1], len(CONTINUOUS_FEATURES)),
        dtype=float,
    )
    valid_counts = np.zeros(len(ANCHOR_FREQUENCIES_GHZ), dtype=np.int64)
    standardized = (continuous - OUTPUT_OFFSETS[None, None, :]) / OUTPUT_SCALES[None, None, :]
    for anchor_index in range(len(ANCHOR_FREQUENCIES_GHZ)):
        mask = validity[bootstrap, anchor_index] & np.all(
            np.isfinite(standardized[bootstrap, anchor_index, :]), axis=1
        )
        valid_counts[anchor_index] = int(np.sum(mask))
        if valid_counts[anchor_index] < max(2, basis.shape[1] // 4):
            raise ValueError(
                f"insufficient valid training rows at anchor {ANCHOR_FREQUENCIES_GHZ[anchor_index]} GHz"
            )
        continuous_coefficients[anchor_index] = _ridge_fit(
            basis[mask], standardized[bootstrap[mask], anchor_index, :], float(ridge)
        )

    validity_labels = np.where(validity[bootstrap], 2.0, -2.0)
    validity_coefficients = _ridge_fit(basis, validity_labels, float(ridge))
    member = RandomFeatureMember(
        seed=int(seed),
        projection=projection,
        bias=bias,
        continuous_coefficients=continuous_coefficients,
        validity_coefficients=validity_coefficients,
        valid_training_counts=valid_counts,
        training_geometry_count=int(indices.size),
        hidden_features=int(hidden_features),
        ridge=float(ridge),
    )
    _validate_member(member)
    return member


def predict_member(member: RandomFeatureMember, geometry_normalized: np.ndarray) -> np.ndarray:
    """Return member predictions with shape [geometry, anchor, seven features]."""

    _validate_member(member)
    geometry = _geometry_matrix(geometry_normalized)
    basis = random_feature_basis(geometry, member.projection, member.bias)
    continuous = np.empty(
        (len(geometry), len(ANCHOR_FREQUENCIES_GHZ), len(CONTINUOUS_FEATURES)),
        dtype=float,
    )
    for anchor_index in range(len(ANCHOR_FREQUENCIES_GHZ)):
        standardized = basis @ member.continuous_coefficients[anchor_index]
        continuous[:, anchor_index, :] = (
            standardized * OUTPUT_SCALES[None, :] + OUTPUT_OFFSETS[None, :]
        )
    qp_index = CONTINUOUS_FEATURES.index("qp")
    qs_index = CONTINUOUS_FEATURES.index("qs")
    qmin_index = CONTINUOUS_FEATURES.index("qmin")
    continuous[:, :, qmin_index] = np.minimum(
        continuous[:, :, qp_index], continuous[:, :, qs_index]
    )
    logits = np.clip(basis @ member.validity_coefficients, -40.0, 40.0)
    validity = 1.0 / (1.0 + np.exp(-logits))
    result = np.concatenate((continuous, validity[:, :, None]), axis=2)
    if not np.all(np.isfinite(result)):
        raise ValueError("member prediction contains non-finite values")
    return result


def fit_uncertainty_calibration(
    *,
    member_predictions: np.ndarray,
    continuous_targets: np.ndarray,
    validity_targets: np.ndarray,
    interval_target: float = DEFAULT_INTERVAL_TARGET,
) -> dict[str, Any]:
    """Fit non-shrinking feature scales on the calibration split only."""

    predictions, continuous, validity = _evaluation_arrays(
        member_predictions, continuous_targets, validity_targets
    )
    if not 0.5 < float(interval_target) < 1.0:
        raise ValueError("interval_target must lie in (0.5,1)")
    mean = np.mean(predictions, axis=0)
    disagreement = np.std(predictions, axis=0, ddof=0)
    scales: dict[str, float] = {}
    sample_counts: dict[str, int] = {}
    for feature_index, feature in enumerate(PREDICTED_FEATURES):
        if feature == "feature_validity_probability":
            target = validity.astype(float)
            mask = np.ones_like(validity, dtype=bool)
        else:
            target = continuous[:, :, feature_index]
            mask = validity & np.isfinite(target)
        error = np.abs(mean[:, :, feature_index][mask] - target[mask])
        raw = disagreement[:, :, feature_index][mask]
        if error.size < 1:
            raise ValueError(f"no calibration observations for {feature}")
        floor = max(float(np.median(raw)) * 1.0e-6, 1.0e-12)
        ratio = error / np.maximum(raw, floor)
        scale = _higher_quantile(ratio, float(interval_target))
        scales[feature] = max(1.0, float(scale))
        sample_counts[feature] = int(error.size)
    return {
        "schema": "broadband56_ensemble_uncertainty_calibration_v1",
        "interval_target": float(interval_target),
        "feature_scales": scales,
        "sample_counts": sample_counts,
        "fit_split": "calibration_geometry_identities_only",
    }


def evaluate_ensemble(
    *,
    member_predictions: np.ndarray,
    continuous_targets: np.ndarray,
    validity_targets: np.ndarray,
    calibration: Mapping[str, Any],
    validation_limits: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate once on the sealed validation identities."""

    predictions, continuous, validity = _evaluation_arrays(
        member_predictions, continuous_targets, validity_targets
    )
    limits = dict(VALIDATION_LIMITS if validation_limits is None else validation_limits)
    scales = calibration.get("feature_scales") if isinstance(calibration.get("feature_scales"), Mapping) else {}
    mean = np.mean(predictions, axis=0)
    disagreement = np.std(predictions, axis=0, ddof=0)
    feature_metrics: dict[str, dict[str, Any]] = {}
    continuous_nmae: list[float] = []
    all_interval_hits: list[np.ndarray] = []
    all_nonzero: list[np.ndarray] = []
    for feature_index, feature in enumerate(CONTINUOUS_FEATURES):
        target = continuous[:, :, feature_index]
        mask = validity & np.isfinite(target)
        error = np.abs(mean[:, :, feature_index][mask] - target[mask])
        raw = disagreement[:, :, feature_index][mask]
        scale = float(scales.get(feature, math.nan))
        normalized_mae = float(np.mean(error) / OUTPUT_RANGES[feature_index]) if error.size else math.nan
        coverage = float(np.mean(error <= scale * raw)) if error.size and math.isfinite(scale) else math.nan
        nonzero = float(np.mean(raw > 1.0e-12)) if raw.size else 0.0
        feature_metrics[feature] = {
            "sample_count": int(error.size),
            "mae": float(np.mean(error)) if error.size else math.nan,
            "rmse": float(np.sqrt(np.mean(error**2))) if error.size else math.nan,
            "range_normalized_mae": normalized_mae,
            "scaled_interval_coverage": coverage,
            "nonzero_disagreement_fraction": nonzero,
            "uncertainty_scale": scale,
        }
        continuous_nmae.append(normalized_mae)
        if error.size:
            all_interval_hits.append(error <= scale * raw)
            all_nonzero.append(raw > 1.0e-12)

    validity_mean = mean[:, :, len(CONTINUOUS_FEATURES)]
    validity_binary = validity.astype(float)
    validity_brier = float(np.mean((validity_mean - validity_binary) ** 2))
    validity_accuracy = float(np.mean((validity_mean >= 0.5) == validity))
    interval_coverage = float(np.mean(np.concatenate(all_interval_hits))) if all_interval_hits else math.nan
    nonzero_fraction = float(np.mean(np.concatenate(all_nonzero))) if all_nonzero else 0.0
    aggregate = {
        "mean_range_normalized_mae": float(np.mean(continuous_nmae)),
        "worst_feature_range_normalized_mae": float(np.max(continuous_nmae)),
        "scaled_interval_empirical_coverage": interval_coverage,
        "nonzero_disagreement_fraction": nonzero_fraction,
        "validity_brier": validity_brier,
        "validity_accuracy": validity_accuracy,
        "validity_positive_fraction": float(np.mean(validity)),
    }
    gates = {
        "metrics_finite": all(
            math.isfinite(float(value)) for value in aggregate.values()
        ),
        "mean_range_normalized_mae": aggregate["mean_range_normalized_mae"]
        <= float(limits["mean_range_normalized_mae_max"]),
        "worst_feature_range_normalized_mae": aggregate[
            "worst_feature_range_normalized_mae"
        ]
        <= float(limits["worst_feature_range_normalized_mae_max"]),
        "scaled_interval_coverage": aggregate["scaled_interval_empirical_coverage"]
        >= float(limits["minimum_scaled_interval_coverage"]),
        "validity_brier": validity_brier <= float(limits["validity_brier_max"]),
        "validity_accuracy": validity_accuracy >= float(limits["validity_accuracy_min"]),
        "nonzero_disagreement": nonzero_fraction
        >= float(limits["minimum_nonzero_disagreement_fraction"]),
    }
    return {
        "overall_status": "PASS" if all(gates.values()) else "FAIL",
        "feature_metrics": feature_metrics,
        "aggregate_metrics": aggregate,
        "validation_limits": limits,
        "gates": gates,
        "evaluation_split": "sealed_validation_geometry_identities_only",
    }


def ensemble_mean_and_uncertainty(
    member_predictions: np.ndarray,
    calibration: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    predictions = np.asarray(member_predictions, dtype=float)
    expected_tail = (len(ANCHOR_FREQUENCIES_GHZ), len(PREDICTED_FEATURES))
    if predictions.ndim != 4 or predictions.shape[0] < MINIMUM_MEMBERS or predictions.shape[2:] != expected_tail:
        raise ValueError("member_predictions shape is invalid")
    if not np.all(np.isfinite(predictions)):
        raise ValueError("member_predictions contain non-finite values")
    scales = calibration.get("feature_scales") if isinstance(calibration.get("feature_scales"), Mapping) else {}
    scale_vector = np.asarray([float(scales.get(feature, math.nan)) for feature in PREDICTED_FEATURES])
    if not np.all(np.isfinite(scale_vector)) or np.any(scale_vector < 1.0):
        raise ValueError("uncertainty calibration scales are missing or invalid")
    return np.mean(predictions, axis=0), np.std(predictions, axis=0, ddof=0) * scale_vector[None, None, :]


def save_member(path: Path, member: RandomFeatureMember) -> None:
    _validate_member(member)
    metadata = {
        "schema": MODEL_SCHEMA,
        "seed": member.seed,
        "geometry_fields": list(GEOMETRY_FIELDS),
        "anchor_frequencies_ghz": list(ANCHOR_FREQUENCIES_GHZ),
        "predicted_features": list(PREDICTED_FEATURES),
        "hidden_features": member.hidden_features,
        "ridge": member.ridge,
        "training_geometry_count": member.training_geometry_count,
        "valid_training_counts": member.valid_training_counts.astype(int).tolist(),
    }
    np.savez_compressed(
        path,
        metadata_json=np.asarray(json.dumps(metadata, separators=(",", ":"))),
        projection=member.projection,
        bias=member.bias,
        continuous_coefficients=member.continuous_coefficients,
        validity_coefficients=member.validity_coefficients,
        valid_training_counts=member.valid_training_counts,
    )


def load_member(path: Path) -> RandomFeatureMember:
    with np.load(path, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata_json"].item()))
        if metadata.get("schema") != MODEL_SCHEMA:
            raise ValueError("acquisition member schema mismatch")
        member = RandomFeatureMember(
            seed=int(metadata["seed"]),
            projection=np.asarray(archive["projection"], dtype=float),
            bias=np.asarray(archive["bias"], dtype=float),
            continuous_coefficients=np.asarray(archive["continuous_coefficients"], dtype=float),
            validity_coefficients=np.asarray(archive["validity_coefficients"], dtype=float),
            valid_training_counts=np.asarray(archive["valid_training_counts"], dtype=np.int64),
            training_geometry_count=int(metadata["training_geometry_count"]),
            hidden_features=int(metadata["hidden_features"]),
            ridge=float(metadata["ridge"]),
        )
    _validate_member(member)
    return member


def random_feature_basis(
    geometry_normalized: np.ndarray,
    projection: np.ndarray,
    bias: np.ndarray,
) -> np.ndarray:
    geometry = _geometry_matrix(geometry_normalized)
    projection_array = np.asarray(projection, dtype=float)
    bias_array = np.asarray(bias, dtype=float)
    if projection_array.shape[0] != geometry.shape[1] or projection_array.shape[1:] != bias_array.shape:
        raise ValueError("random-feature projection and bias shapes are inconsistent")
    centered = 2.0 * geometry - 1.0
    random = np.tanh(centered @ projection_array + bias_array[None, :])
    return np.concatenate((np.ones((len(geometry), 1)), centered, random), axis=1)


def _ridge_fit(basis: np.ndarray, targets: np.ndarray, ridge: float) -> np.ndarray:
    x = np.asarray(basis, dtype=float)
    y = np.asarray(targets, dtype=float)
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y) or len(x) < 2:
        raise ValueError("ridge inputs are invalid")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("ridge inputs contain non-finite values")
    gram = x.T @ x
    penalty = np.eye(gram.shape[0], dtype=float) * float(ridge)
    penalty[0, 0] = float(ridge) * 1.0e-6
    return np.linalg.solve(gram + penalty, x.T @ y)


def _training_arrays(
    geometry: np.ndarray,
    continuous: np.ndarray,
    validity: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    geometry_array = _geometry_matrix(geometry)
    continuous_array = np.asarray(continuous, dtype=float)
    validity_array = np.asarray(validity, dtype=bool)
    expected_continuous = (
        len(geometry_array),
        len(ANCHOR_FREQUENCIES_GHZ),
        len(CONTINUOUS_FEATURES),
    )
    expected_validity = (len(geometry_array), len(ANCHOR_FREQUENCIES_GHZ))
    if continuous_array.shape != expected_continuous or validity_array.shape != expected_validity:
        raise ValueError("training target shapes are invalid")
    return geometry_array, continuous_array, validity_array


def _evaluation_arrays(
    member_predictions: np.ndarray,
    continuous: np.ndarray,
    validity: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    predictions = np.asarray(member_predictions, dtype=float)
    if predictions.ndim != 4 or predictions.shape[0] < MINIMUM_MEMBERS:
        raise ValueError("at least five member prediction arrays are required")
    continuous_array = np.asarray(continuous, dtype=float)
    validity_array = np.asarray(validity, dtype=bool)
    expected = (
        predictions.shape[1],
        len(ANCHOR_FREQUENCIES_GHZ),
        len(PREDICTED_FEATURES),
    )
    if predictions.shape[1:] != expected:
        raise ValueError("member prediction shape is invalid")
    if continuous_array.shape != expected[:-1] + (len(CONTINUOUS_FEATURES),):
        raise ValueError("continuous target shape is invalid")
    if validity_array.shape != expected[:-1]:
        raise ValueError("validity target shape is invalid")
    if not np.all(np.isfinite(predictions)):
        raise ValueError("member predictions contain non-finite values")
    return predictions, continuous_array, validity_array


def _geometry_matrix(value: np.ndarray) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] < 1 or matrix.shape[1] != len(GEOMETRY_FIELDS):
        raise ValueError("geometry matrix must be nonempty normalized 10-D data")
    if not np.all(np.isfinite(matrix)) or np.any(matrix < 0.0) or np.any(matrix > 1.0):
        raise ValueError("geometry matrix must lie in normalized [0,1] bounds")
    return matrix


def _validate_member(member: RandomFeatureMember) -> None:
    basis_count = 1 + len(GEOMETRY_FIELDS) + int(member.hidden_features)
    expected_continuous = (
        len(ANCHOR_FREQUENCIES_GHZ),
        basis_count,
        len(CONTINUOUS_FEATURES),
    )
    if member.projection.shape != (len(GEOMETRY_FIELDS), int(member.hidden_features)):
        raise ValueError("member projection shape mismatch")
    if member.bias.shape != (int(member.hidden_features),):
        raise ValueError("member bias shape mismatch")
    if member.continuous_coefficients.shape != expected_continuous:
        raise ValueError("member continuous coefficient shape mismatch")
    if member.validity_coefficients.shape != (basis_count, len(ANCHOR_FREQUENCIES_GHZ)):
        raise ValueError("member validity coefficient shape mismatch")
    if member.valid_training_counts.shape != (len(ANCHOR_FREQUENCIES_GHZ),):
        raise ValueError("member valid-training count shape mismatch")
    arrays = (
        member.projection,
        member.bias,
        member.continuous_coefficients,
        member.validity_coefficients,
    )
    if any(not np.all(np.isfinite(value)) for value in arrays):
        raise ValueError("member arrays contain non-finite values")


def _higher_quantile(values: np.ndarray, probability: float) -> float:
    array = np.sort(np.asarray(values, dtype=float))
    if array.ndim != 1 or array.size < 1 or not np.all(np.isfinite(array)):
        raise ValueError("quantile values are invalid")
    index = min(array.size - 1, max(0, int(math.ceil(probability * array.size) - 1)))
    return float(array[index])


def _identity_digest(values: Sequence[str] | Any) -> str:
    digest = hashlib.sha256()
    for value in sorted(str(item) for item in values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())
