#!/usr/bin/env python3
"""Shared helpers for acquisition-only physical-feature calibration."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "rfic_physical_feature_prediction_calibration_v1"
APPROVED_DECISION = "USE_CALIBRATION_FOR_ACQUISITION_ONLY"


def fit_isotonic_mapping(x: np.ndarray, y: np.ndarray) -> dict[str, list[float]]:
    """Fit a deterministic increasing isotonic map with weighted PAVA."""

    x = np.asarray(x, dtype=float).reshape(-1)
    y = np.asarray(y, dtype=float).reshape(-1)
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]
    if x.size < 2:
        raise ValueError("isotonic calibration requires at least two finite pairs")

    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    unique_x, first, counts = np.unique(x, return_index=True, return_counts=True)
    sums = np.add.reduceat(y, first)
    means = sums / counts

    block_starts: list[int] = []
    block_ends: list[int] = []
    block_weights: list[float] = []
    block_values: list[float] = []
    for index, (value, weight) in enumerate(zip(means.tolist(), counts.astype(float).tolist())):
        block_starts.append(index)
        block_ends.append(index + 1)
        block_weights.append(weight)
        block_values.append(float(value))
        while len(block_values) >= 2 and block_values[-2] > block_values[-1]:
            merged_weight = block_weights[-2] + block_weights[-1]
            merged_value = (
                block_values[-2] * block_weights[-2] + block_values[-1] * block_weights[-1]
            ) / merged_weight
            block_ends[-2] = block_ends[-1]
            block_weights[-2] = merged_weight
            block_values[-2] = merged_value
            block_starts.pop()
            block_ends.pop()
            block_weights.pop()
            block_values.pop()

    fitted = np.empty_like(unique_x, dtype=float)
    for start, end, value in zip(block_starts, block_ends, block_values):
        fitted[start:end] = value
    return {
        "input_knots": [float(value) for value in unique_x],
        "output_knots": [float(value) for value in fitted],
    }


def apply_feature_mapping(values: np.ndarray, mapping: dict[str, Any]) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    x = np.asarray(mapping.get("input_knots") or [], dtype=float)
    y = np.asarray(mapping.get("output_knots") or [], dtype=float)
    if x.size < 2 or x.size != y.size:
        raise ValueError("calibration mapping must contain equally sized input/output knots")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("calibration knots must be finite")
    if not np.all(np.diff(x) > 0.0):
        raise ValueError("calibration input knots must be strictly increasing")
    if not np.all(np.diff(y) >= -1.0e-12):
        raise ValueError("calibration output knots must be nondecreasing")
    return np.interp(values, x, y, left=y[0], right=y[-1])


def apply_matrix(
    matrix: np.ndarray,
    feature_columns: list[str] | tuple[str, ...],
    mapping: dict[str, Any],
) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != len(feature_columns):
        raise ValueError("prediction matrix shape does not match feature columns")
    calibrated = np.array(matrix, copy=True)
    for index, feature in enumerate(feature_columns):
        feature_mapping = mapping.get(feature)
        if not isinstance(feature_mapping, dict):
            raise ValueError(f"missing calibration mapping for {feature}")
        calibrated[:, index] = apply_feature_mapping(matrix[:, index], feature_mapping)
    return calibrated


def load_approved_calibration(
    path: str | Path,
    feature_columns: list[str] | tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema={payload.get('schema')!r}")
    if payload.get("overall_status") != "PASS":
        errors.append(f"overall_status={payload.get('overall_status')!r}")
    if payload.get("decision") != APPROVED_DECISION:
        errors.append(f"decision={payload.get('decision')!r}")
    if payload.get("eligible_for_selector") is not True:
        errors.append("eligible_for_selector is not true")
    declared = list(payload.get("feature_columns") or [])
    if declared != list(feature_columns):
        errors.append(f"feature_columns={declared!r}")
    mapping = payload.get("deployment_mapping")
    if not isinstance(mapping, dict):
        errors.append("deployment_mapping is missing")
    else:
        for feature in feature_columns:
            try:
                probe = np.asarray([-1.0, 0.0, 1.0], dtype=float)
                mapped = apply_feature_mapping(probe, mapping.get(feature) or {})
                if not all(math.isfinite(float(value)) for value in mapped):
                    raise ValueError("non-finite mapped values")
            except (TypeError, ValueError) as exc:
                errors.append(f"{feature}: {exc}")
    if errors:
        raise ValueError("calibration is not approved for acquisition: " + "; ".join(errors))
    return payload, mapping
