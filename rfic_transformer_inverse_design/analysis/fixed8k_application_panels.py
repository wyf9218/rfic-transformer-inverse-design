"""Derived application-panel statistics for a frozen fixed8k proxy result.

This module never performs model inference.  It accepts already-frozen target
and proxy-prediction matrices, applies target-only panel membership rules, and
computes explicitly defined descriptive diagnostics.  All callers must retain
the proxy-only evidence boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from rfic_transformer_inverse_design.model_splitting import (
    split_physical_feature_indices,
)


FEATURE_NAMES = ("Lp_nH", "Ls_nH", "Qmin", "K_abs")
NORMALIZATION_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)
TOLERANCES = (0.05, 0.10, 0.15, 0.20, 0.25)
LEGACY_METRIC_ID = "legacy_q_shortfall_engineering_joint_score"
SYMMETRIC_METRIC_ID = "symmetric_absolute_q_four_feature_fidelity"
SYMMETRIC_JOINT_METRIC_ID = "symmetric_absolute_q_joint_rmse"
STRICT_METRIC_ID = "strict_all_feature_absolute_q"


@dataclass(frozen=True)
class PanelSpec:
    """Target-only rectangular and turns-ratio limits for one report panel."""

    name: str
    role: str
    lower: tuple[float, float, float, float]
    upper: tuple[float, float, float, float]
    ratio_lower: float | None
    ratio_upper: float | None

    def definition(self) -> str:
        ratio = "none"
        if self.ratio_lower is not None and self.ratio_upper is not None:
            ratio = f"{self.ratio_lower:g}<=Ls/Lp<={self.ratio_upper:g}"
        return (
            f"{self.lower[0]:g}<=Lp<={self.upper[0]:g} nH; "
            f"{self.lower[1]:g}<=Ls<={self.upper[1]:g} nH; "
            f"{self.lower[2]:g}<=Qmin<={self.upper[2]:g}; "
            f"{self.lower[3]:g}<=|K|<={self.upper[3]:g}; ratio={ratio}"
        )


CORE_PANEL = PanelSpec(
    name="core_15ghz_application",
    role="primary advisor-report panel",
    lower=(0.5, 0.5, 10.0, 0.5),
    upper=(1.5, 1.5, 20.0, 0.8),
    ratio_lower=0.67,
    ratio_upper=1.5,
)

EXTENDED_PANEL = PanelSpec(
    name="extended_15ghz_practical",
    role="secondary practical-coverage panel",
    lower=(0.5, 0.5, 8.0, 0.3),
    upper=(2.0, 2.0, 20.0, 0.8),
    ratio_lower=0.5,
    ratio_upper=2.0,
)

FULL_PANEL = PanelSpec(
    name="full_declared_range_stress",
    role="unchanged full-frame stress-test panel",
    lower=(0.5, 0.5, 5.0, 0.0),
    upper=(3.0, 3.0, 25.0, 0.8),
    ratio_lower=None,
    ratio_upper=None,
)

PANELS = (CORE_PANEL, EXTENDED_PANEL, FULL_PANEL)

EXPECTED_ABSOLUTE_Q_COUNTS = {
    CORE_PANEL.name: (150, 131, 97),
    EXTENDED_PANEL.name: (817, 593, 451),
    FULL_PANEL.name: (8000, 3109, 2214),
}


def sha256_file(path: str | Path) -> str:
    """Return the lowercase SHA-256 digest of a file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_sha256(path: str | Path, expected: str) -> str:
    """Fail closed unless *path* has the exact expected SHA-256."""

    actual = sha256_file(path)
    expected_normalized = str(expected).strip().lower()
    if len(expected_normalized) != 64 or actual != expected_normalized:
        raise ValueError(
            f"SHA-256 mismatch for {Path(path)}: expected={expected_normalized} actual={actual}"
        )
    return actual


def create_no_clobber_directory(path: str | Path) -> Path:
    """Create a report directory and refuse to reuse an existing path."""

    output = Path(path).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    return output


def validate_target_prediction_matrices(
    target_ids: Sequence[str],
    targets: np.ndarray,
    prediction_ids: Sequence[str],
    predictions: np.ndarray,
) -> None:
    """Validate exact one-row-per-target alignment and finite matrices."""

    target_values = np.asarray(targets, dtype=float)
    prediction_values = np.asarray(predictions, dtype=float)
    if target_values.ndim != 2 or target_values.shape[1] != 4:
        raise ValueError("targets must be an N x 4 matrix")
    if prediction_values.shape != target_values.shape:
        raise ValueError("prediction matrix does not align with targets")
    if len(target_ids) != len(target_values) or len(prediction_ids) != len(target_values):
        raise ValueError("target and prediction ID counts do not align")
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("duplicate target IDs are not allowed")
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("duplicate prediction target IDs are not allowed")
    if tuple(target_ids) != tuple(prediction_ids):
        raise ValueError("prediction rows are not aligned to the exact target order")
    if not np.isfinite(target_values).all() or not np.isfinite(prediction_values).all():
        raise ValueError("target or prediction matrix contains NaN or Inf")


def panel_membership(
    targets: np.ndarray,
    panels: Sequence[PanelSpec] = PANELS,
) -> tuple[dict[str, np.ndarray], dict[str, list[str]], dict[str, dict[str, int]]]:
    """Return target-only panel masks, exclusion reasons, and exclusion counts."""

    values = np.asarray(targets, dtype=float)
    if values.ndim != 2 or values.shape[1] != 4 or not np.isfinite(values).all():
        raise ValueError("panel targets must be a finite N x 4 matrix")
    masks: dict[str, np.ndarray] = {}
    reasons: dict[str, list[str]] = {}
    counts: dict[str, dict[str, int]] = {}
    labels = ("Lp", "Ls", "Qmin", "K_abs")
    for panel in panels:
        lower = np.asarray(panel.lower, dtype=float)
        upper = np.asarray(panel.upper, dtype=float)
        criterion_pass = [
            (values[:, index] >= lower[index]) & (values[:, index] <= upper[index])
            for index in range(4)
        ]
        criterion_names = [f"{label}_outside_range" for label in labels]
        if panel.ratio_lower is not None and panel.ratio_upper is not None:
            ratio = values[:, 1] / values[:, 0]
            criterion_pass.append(
                (ratio >= float(panel.ratio_lower))
                & (ratio <= float(panel.ratio_upper))
            )
            criterion_names.append("Ls_over_Lp_outside_range")
        matrix = np.column_stack(criterion_pass)
        mask = np.all(matrix, axis=1)
        masks[panel.name] = mask
        panel_reasons: list[str] = []
        for row_index in range(len(values)):
            failed = [
                criterion_names[index]
                for index, passed in enumerate(matrix[row_index])
                if not bool(passed)
            ]
            panel_reasons.append(";".join(failed))
        reasons[panel.name] = panel_reasons
        exclusion_counts = {
            name: int(np.sum(~matrix[:, index]))
            for index, name in enumerate(criterion_names)
        }
        exclusion_counts["multiple_filters"] = int(
            np.sum(np.sum(~matrix, axis=1) > 1)
        )
        counts[panel.name] = exclusion_counts

    core = masks[CORE_PANEL.name]
    extended = masks[EXTENDED_PANEL.name]
    full = masks[FULL_PANEL.name]
    if np.any(core & ~extended):
        raise ValueError("core panel is not a subset of extended panel")
    if np.any(extended & ~full):
        raise ValueError("extended panel is not a subset of full panel")
    return masks, reasons, counts


def derive_errors(
    targets: np.ndarray,
    predictions: np.ndarray,
    *,
    spans: np.ndarray = NORMALIZATION_SPANS,
) -> dict[str, np.ndarray]:
    """Compute the frozen absolute-Q and one-sided-Q error definitions."""

    target_values = np.asarray(targets, dtype=float)
    predicted_values = np.asarray(predictions, dtype=float)
    span_values = np.asarray(spans, dtype=float)
    if target_values.shape != predicted_values.shape or target_values.ndim != 2:
        raise ValueError("targets and predictions must have the same N x D shape")
    if target_values.shape[1] != 4 or span_values.shape != (4,):
        raise ValueError("the frozen application report requires four features")
    if np.any(span_values <= 0.0) or not np.isfinite(span_values).all():
        raise ValueError("normalization spans must be finite and positive")
    if not np.isfinite(target_values).all() or not np.isfinite(predicted_values).all():
        raise ValueError("targets or predictions contain NaN or Inf")
    raw = predicted_values - target_values
    absolute = np.abs(raw)
    normalized = absolute / span_values[None, :]
    q_shortfall = np.maximum(target_values[:, 2] - predicted_values[:, 2], 0.0)
    joint = np.sqrt(np.mean(normalized**2, axis=1))
    strict = np.max(normalized, axis=1)
    legacy_components = normalized.copy()
    legacy_components[:, 2] = q_shortfall / span_values[2]
    legacy_engineering_joint = np.sqrt(np.mean(legacy_components**2, axis=1))
    result = {
        "raw": raw,
        "absolute": absolute,
        "normalized_absolute": normalized,
        "q_shortfall": q_shortfall,
        "q_target_met": predicted_values[:, 2] >= target_values[:, 2],
        "joint_normalized_rmse": joint,
        "strict_max_feature_error": strict,
        "legacy_engineering_joint_normalized_rmse": legacy_engineering_joint,
    }
    if any(not np.isfinite(value).all() for value in result.values()):
        raise ValueError("derived error calculation produced NaN or Inf")
    return result


def percentile(values: np.ndarray, quantile: float) -> float:
    """Return a deterministic NumPy linear percentile."""

    return float(np.percentile(np.asarray(values, dtype=float), quantile))


def distribution_statistics(
    signed_values: np.ndarray,
    magnitude_values: np.ndarray | None = None,
) -> dict[str, float | int]:
    """Return count, bias, MAE/RMSE, and absolute-error percentiles."""

    signed = np.asarray(signed_values, dtype=float)
    magnitude = np.abs(signed) if magnitude_values is None else np.asarray(magnitude_values, dtype=float)
    if signed.ndim != 1 or magnitude.shape != signed.shape or len(signed) == 0:
        raise ValueError("statistics require non-empty aligned one-dimensional arrays")
    if not np.isfinite(signed).all() or not np.isfinite(magnitude).all():
        raise ValueError("statistics contain NaN or Inf")
    return {
        "count": int(len(signed)),
        "bias": float(np.mean(signed)),
        "mae": float(np.mean(magnitude)),
        "rmse": float(np.sqrt(np.mean(signed**2))),
        "p50": percentile(magnitude, 50.0),
        "p90": percentile(magnitude, 90.0),
        "p95": percentile(magnitude, 95.0),
        "p99": percentile(magnitude, 99.0),
        "maximum": float(np.max(magnitude)),
    }


def raw_feature_error_rows(
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    targets: np.ndarray,
    predictions: np.ndarray,
) -> list[dict[str, Any]]:
    """Build raw physical-unit statistics for every panel and feature."""

    target_values = np.asarray(targets, dtype=float)
    predicted_values = np.asarray(predictions, dtype=float)
    raw = np.asarray(errors["raw"], dtype=float)
    absolute = np.asarray(errors["absolute"], dtype=float)
    shortfall = np.asarray(errors["q_shortfall"], dtype=float)
    rows: list[dict[str, Any]] = []
    feature_metadata = (
        ("Lp_absolute_error", 0, "predicted_Lp_nH - target_Lp_nH", "nH"),
        ("Ls_absolute_error", 1, "predicted_Ls_nH - target_Ls_nH", "nH"),
        ("Q_absolute_error", 2, "predicted_Qmin - target_Qmin; percentiles use absolute error", ""),
        ("K_absolute_error", 3, "predicted_K_abs - target_K_abs", ""),
    )
    for panel_name, mask in masks.items():
        panel_mask = np.asarray(mask, dtype=bool)
        denominator = int(np.sum(panel_mask))
        if denominator == 0:
            raise ValueError(f"panel {panel_name} has no targets")
        q_met = predicted_values[panel_mask, 2] >= target_values[panel_mask, 2]
        q_short = shortfall[panel_mask]
        unmet = ~q_met
        q_common = {
            "q_target_met_count": int(np.sum(q_met)),
            "q_target_met_fraction": float(np.mean(q_met)),
            "q_unmet_count": int(np.sum(unmet)),
            "q_unmet_fraction": float(np.mean(unmet)),
            "q_shortfall_mean_all_targets": float(np.mean(q_short)),
            "q_shortfall_rmse_all_targets": float(np.sqrt(np.mean(q_short**2))),
            "q_shortfall_p50_all_targets": percentile(q_short, 50.0),
            "q_shortfall_p90_all_targets": percentile(q_short, 90.0),
            "q_shortfall_p95_all_targets": percentile(q_short, 95.0),
            "q_shortfall_unmet_denominator": int(np.sum(unmet)),
            "q_shortfall_p50_unmet_only": percentile(q_short[unmet], 50.0) if np.any(unmet) else None,
            "q_shortfall_p90_unmet_only": percentile(q_short[unmet], 90.0) if np.any(unmet) else None,
            "q_shortfall_p95_unmet_only": percentile(q_short[unmet], 95.0) if np.any(unmet) else None,
        }
        for metric_name, index, definition, unit in feature_metadata:
            stats = distribution_statistics(raw[panel_mask, index], absolute[panel_mask, index])
            row = {
                "panel": panel_name,
                "metric": metric_name,
                "error_definition": definition,
                "unit": unit,
                "denominator_definition": f"all {denominator} target-only panel members",
                **stats,
            }
            if index == 2:
                row.update(q_common)
            rows.append(row)
        short_stats = distribution_statistics(q_short, q_short)
        rows.append(
            {
                "panel": panel_name,
                "metric": "Q_one_sided_shortfall",
                "error_definition": "max(target_Qmin - predicted_Qmin, 0)",
                "unit": "",
                "denominator_definition": f"all {denominator} target-only panel members",
                **short_stats,
                **q_common,
            }
        )
    return rows


def tolerance_success_rows(
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    tolerances: Iterable[float] = TOLERANCES,
) -> list[dict[str, Any]]:
    """Compute joint-RMSE and strict all-feature fixed-frame success rates."""

    joint = np.asarray(errors["joint_normalized_rmse"], dtype=float)
    strict = np.asarray(errors["strict_max_feature_error"], dtype=float)
    rows: list[dict[str, Any]] = []
    for panel_name, mask in masks.items():
        panel_mask = np.asarray(mask, dtype=bool)
        denominator = int(np.sum(panel_mask))
        for tolerance in tolerances:
            threshold = float(tolerance)
            joint_count = int(np.sum(joint[panel_mask] <= threshold))
            strict_count = int(np.sum(strict[panel_mask] <= threshold))
            rows.append(
                {
                    "panel": panel_name,
                    "tolerance": threshold,
                    "joint_rmse_success_count": joint_count,
                    "joint_rmse_success_fraction": joint_count / denominator,
                    "strict_all_feature_success_count": strict_count,
                    "strict_all_feature_success_fraction": strict_count / denominator,
                    "denominator": denominator,
                }
            )
    return rows


def metric_definition_vectors(
    errors: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Return the three explicitly named, non-interchangeable report metrics."""

    vectors = {
        LEGACY_METRIC_ID: np.asarray(
            errors["legacy_engineering_joint_normalized_rmse"], dtype=float
        ),
        SYMMETRIC_METRIC_ID: np.asarray(errors["joint_normalized_rmse"], dtype=float),
        STRICT_METRIC_ID: np.asarray(errors["strict_max_feature_error"], dtype=float),
    }
    shapes = {value.shape for value in vectors.values()}
    if len(shapes) != 1 or next(iter(shapes), ()) == ():
        raise ValueError("dual-metric vectors must be aligned non-scalar arrays")
    if any(value.ndim != 1 or not np.isfinite(value).all() for value in vectors.values()):
        raise ValueError("dual-metric vectors must be finite one-dimensional arrays")
    return vectors


def joint_metrics_by_definition_rows(
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
) -> list[dict[str, Any]]:
    """Summarize the legacy and symmetric joint scores without merging semantics."""

    vectors = metric_definition_vectors(errors)
    definitions = (
        (
            LEGACY_METRIC_ID,
            "max(target_Qmin - predicted_Qmin, 0) / 20",
        ),
        (
            SYMMETRIC_METRIC_ID,
            "abs(predicted_Qmin - target_Qmin) / 20",
        ),
    )
    rows: list[dict[str, Any]] = []
    for panel_name, mask in masks.items():
        panel_mask = np.asarray(mask, dtype=bool)
        denominator = int(np.sum(panel_mask))
        if denominator == 0:
            raise ValueError(f"panel {panel_name} has no targets")
        for metric_id, q_definition in definitions:
            values = vectors[metric_id][panel_mask]
            success_count = int(np.sum(values <= 0.10))
            rows.append(
                {
                    "panel": panel_name,
                    "metric_family": metric_id,
                    "q_error_definition": q_definition,
                    "denominator": denominator,
                    "median": float(np.median(values)),
                    "mae": float(np.mean(np.abs(values))),
                    "rmse": float(np.sqrt(np.mean(values**2))),
                    "p90": percentile(values, 90.0),
                    "p95": percentile(values, 95.0),
                    "success_count_at_10pct": success_count,
                    "success_fraction_at_10pct": success_count / denominator,
                }
            )
    return rows


def tolerance_success_by_definition_rows(
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    tolerances: Iterable[float] = TOLERANCES,
) -> list[dict[str, Any]]:
    """Return separately labeled tolerance rates for both joint scores and strict fidelity."""

    vectors = metric_definition_vectors(errors)
    q_definitions = {
        LEGACY_METRIC_ID: "one-sided Q shortfall",
        SYMMETRIC_JOINT_METRIC_ID: "absolute Q error",
        STRICT_METRIC_ID: "absolute Q error",
    }
    tolerance_vectors = {
        LEGACY_METRIC_ID: vectors[LEGACY_METRIC_ID],
        SYMMETRIC_JOINT_METRIC_ID: vectors[SYMMETRIC_METRIC_ID],
        STRICT_METRIC_ID: vectors[STRICT_METRIC_ID],
    }
    rows: list[dict[str, Any]] = []
    for panel_name, mask in masks.items():
        panel_mask = np.asarray(mask, dtype=bool)
        denominator = int(np.sum(panel_mask))
        for tolerance in tolerances:
            threshold = float(tolerance)
            for metric_id in (
                LEGACY_METRIC_ID,
                SYMMETRIC_JOINT_METRIC_ID,
                STRICT_METRIC_ID,
            ):
                count = int(np.sum(tolerance_vectors[metric_id][panel_mask] <= threshold))
                rows.append(
                    {
                        "panel": panel_name,
                        "metric_definition": metric_id,
                        "q_error_definition": q_definitions[metric_id],
                        "tolerance": threshold,
                        "success_count": count,
                        "success_fraction": count / denominator,
                        "denominator": denominator,
                    }
                )
    return rows


def independent_metric_reproduction(
    targets: np.ndarray,
    predictions: np.ndarray,
    masks: Mapping[str, np.ndarray],
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Independently recompute and gate the legacy and absolute-Q metric families."""

    target_values = np.asarray(targets, dtype=float)
    predicted_values = np.asarray(predictions, dtype=float)
    if target_values.shape != predicted_values.shape or target_values.ndim != 2:
        raise ValueError("independent reproduction inputs must be aligned N x 4 matrices")
    if target_values.shape[1] != 4:
        raise ValueError("independent reproduction requires four physical features")
    if not np.isfinite(target_values).all() or not np.isfinite(predicted_values).all():
        raise ValueError("independent reproduction inputs contain NaN or Inf")

    raw = predicted_values - target_values
    e_lp = np.abs(raw[:, 0]) / 2.5
    e_ls = np.abs(raw[:, 1]) / 2.5
    e_q_abs = np.abs(raw[:, 2]) / 20.0
    e_q_shortfall = np.maximum(-raw[:, 2], 0.0) / 20.0
    e_k = np.abs(raw[:, 3]) / 0.8
    legacy = np.sqrt((e_lp**2 + e_ls**2 + e_q_shortfall**2 + e_k**2) / 4.0)
    symmetric = np.sqrt((e_lp**2 + e_ls**2 + e_q_abs**2 + e_k**2) / 4.0)
    strict = np.maximum.reduce([e_lp, e_ls, e_q_abs, e_k])
    vectors = {
        LEGACY_METRIC_ID: legacy,
        SYMMETRIC_METRIC_ID: symmetric,
        STRICT_METRIC_ID: strict,
    }

    full_mask = np.asarray(masks[FULL_PANEL.name], dtype=bool)
    legacy_median = float(np.median(legacy[full_mask]))
    legacy_fraction = float(np.mean(legacy[full_mask] <= 0.10))
    legacy_gate = {
        "denominator": int(np.sum(full_mask)),
        "median": legacy_median,
        "success_count_at_10pct": int(np.sum(legacy[full_mask] <= 0.10)),
        "success_fraction_at_10pct": legacy_fraction,
        "rounded_median_percent": round(legacy_median * 100.0, 2),
        "rounded_success_percent": round(legacy_fraction * 100.0, 1),
    }
    legacy_gate["status"] = (
        "PASS"
        if legacy_gate["rounded_median_percent"] == 12.46
        and legacy_gate["rounded_success_percent"] == 43.0
        else "FAIL"
    )

    absolute_panels: dict[str, Any] = {}
    absolute_pass = True
    for panel_name, (expected_n, expected_joint, expected_strict) in EXPECTED_ABSOLUTE_Q_COUNTS.items():
        panel_mask = np.asarray(masks[panel_name], dtype=bool)
        actual = {
            "denominator": int(np.sum(panel_mask)),
            "symmetric_joint_rmse_le_10_count": int(np.sum(symmetric[panel_mask] <= 0.10)),
            "strict_all_feature_le_10_count": int(np.sum(strict[panel_mask] <= 0.10)),
            "expected_denominator": expected_n,
            "expected_symmetric_joint_rmse_le_10_count": expected_joint,
            "expected_strict_all_feature_le_10_count": expected_strict,
        }
        actual["status"] = (
            "PASS"
            if (
                actual["denominator"],
                actual["symmetric_joint_rmse_le_10_count"],
                actual["strict_all_feature_le_10_count"],
            )
            == (expected_n, expected_joint, expected_strict)
            else "FAIL"
        )
        absolute_pass = absolute_pass and actual["status"] == "PASS"
        absolute_panels[panel_name] = actual

    gate = {
        "status": "PASS" if legacy_gate["status"] == "PASS" and absolute_pass else "FAIL",
        "legacy_reproduction": legacy_gate,
        "absolute_q_count_reproduction": {
            "status": "PASS" if absolute_pass else "FAIL",
            "panels": absolute_panels,
        },
        "definition_separation": (
            "A Q value above target has zero engineering shortfall but nonzero "
            "absolute-Q fidelity error. The metric families are not numerically interchangeable."
        ),
    }
    return gate, vectors


def report_headline_binding(
    reproduction_gate: Mapping[str, Any],
) -> dict[str, str]:
    """Bind advisor-facing headlines to independently gated integer counts."""

    if reproduction_gate.get("status") != "PASS":
        raise ValueError("report headlines require a passing independent reproduction gate")
    panels = (reproduction_gate.get("absolute_q_count_reproduction") or {}).get("panels") or {}
    expected_names = {CORE_PANEL.name, EXTENDED_PANEL.name, FULL_PANEL.name}
    if set(panels) != expected_names:
        raise ValueError("report headline panel set does not match the frozen contract")
    core = panels[CORE_PANEL.name]
    extended = panels[EXTENDED_PANEL.name]
    full = panels[FULL_PANEL.name]
    legacy = reproduction_gate["legacy_reproduction"]
    return {
        CORE_PANEL.name: (
            f"{core['symmetric_joint_rmse_le_10_count']}/{core['denominator']} "
            "(87.3%) satisfy symmetric joint-RMSE <=10%; "
            f"{core['strict_all_feature_le_10_count']}/{core['denominator']} "
            "(64.7%) satisfy strict all-four-within-10%."
        ),
        EXTENDED_PANEL.name: (
            f"{extended['symmetric_joint_rmse_le_10_count']}/{extended['denominator']} "
            "(72.58%) satisfy symmetric joint-RMSE <=10%; "
            f"{extended['strict_all_feature_le_10_count']}/{extended['denominator']} "
            "(55.20%) satisfy strict all-four-within-10%."
        ),
        FULL_PANEL.name: (
            f"{full['symmetric_joint_rmse_le_10_count']}/{full['denominator']} "
            "(38.8625%) satisfy symmetric joint-RMSE <=10%; "
            f"{full['strict_all_feature_le_10_count']}/{full['denominator']} "
            "(27.675%) satisfy strict all-four-within-10%."
        ),
        "legacy_continuity": (
            f"Legacy Q-shortfall engineering score: {legacy['success_count_at_10pct']}/"
            f"{legacy['denominator']} ({legacy['rounded_success_percent']:.1f}%) satisfy "
            f"legacy joint score <=10%; median={legacy['rounded_median_percent']:.2f}%."
        ),
    }


def panel_summary_rows(
    masks: Mapping[str, np.ndarray],
    errors: Mapping[str, np.ndarray],
    targets: np.ndarray,
    predictions: np.ndarray,
) -> list[dict[str, Any]]:
    """Create one mixed-metric summary row per target-only panel."""

    target_values = np.asarray(targets, dtype=float)
    predicted_values = np.asarray(predictions, dtype=float)
    raw = np.asarray(errors["raw"], dtype=float)
    absolute = np.asarray(errors["absolute"], dtype=float)
    joint = np.asarray(errors["joint_normalized_rmse"], dtype=float)
    strict = np.asarray(errors["strict_max_feature_error"], dtype=float)
    shortfall = np.asarray(errors["q_shortfall"], dtype=float)
    rows: list[dict[str, Any]] = []
    for panel_name, mask in masks.items():
        panel_mask = np.asarray(mask, dtype=bool)
        count = int(np.sum(panel_mask))
        q_met = predicted_values[panel_mask, 2] >= target_values[panel_mask, 2]
        rows.append(
            {
                "panel": panel_name,
                "count": count,
                "Lp_mae_nH": float(np.mean(absolute[panel_mask, 0])),
                "Lp_rmse_nH": float(np.sqrt(np.mean(raw[panel_mask, 0] ** 2))),
                "Ls_mae_nH": float(np.mean(absolute[panel_mask, 1])),
                "Ls_rmse_nH": float(np.sqrt(np.mean(raw[panel_mask, 1] ** 2))),
                "Q_absolute_mae": float(np.mean(absolute[panel_mask, 2])),
                "Q_absolute_rmse": float(np.sqrt(np.mean(raw[panel_mask, 2] ** 2))),
                "Q_shortfall_mae": float(np.mean(shortfall[panel_mask])),
                "Q_target_met_fraction": float(np.mean(q_met)),
                "K_mae": float(np.mean(absolute[panel_mask, 3])),
                "K_rmse": float(np.sqrt(np.mean(raw[panel_mask, 3] ** 2))),
                "median_joint_normalized_rmse": float(np.median(joint[panel_mask])),
                "joint_rmse_le_10_fraction": float(np.mean(joint[panel_mask] <= 0.10)),
                "strict_all_four_le_10_fraction": float(np.mean(strict[panel_mask] <= 0.10)),
            }
        )
    return rows


def reconstruct_exact_training_response_cloud(
    all_training_responses: np.ndarray,
    *,
    seed: int,
    validation_fraction: float,
    test_fraction: float,
    bins: int,
    lower: Sequence[float],
    upper: Sequence[float],
    expected_train_count: int,
    expected_train_index_sha256: str,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Reconstruct and identity-check the exact gradient-training response cloud."""

    responses = np.asarray(all_training_responses, dtype=float)
    if responses.ndim != 2 or responses.shape[1] != 4 or not np.isfinite(responses).all():
        raise ValueError("training response table must be a finite N x 4 matrix")
    split, audit = split_physical_feature_indices(
        responses,
        mode="physical_cell_grouped",
        seed=int(seed),
        validation_fraction=float(validation_fraction),
        test_fraction=float(test_fraction),
        physical_cell_bins=int(bins),
        physical_cell_lower=np.asarray(lower, dtype=float),
        physical_cell_upper=np.asarray(upper, dtype=float),
    )
    indices = np.asarray(split["train"], dtype=np.int64)
    actual_sha = hashlib.sha256(indices.tobytes()).hexdigest()
    if len(indices) != int(expected_train_count):
        raise ValueError(
            f"gradient-training row count mismatch: {len(indices)} != {expected_train_count}"
        )
    if actual_sha != str(expected_train_index_sha256).strip().lower():
        raise ValueError(
            "gradient-training index identity mismatch: "
            f"{actual_sha} != {expected_train_index_sha256}"
        )
    return responses[indices], indices, audit


def normalized_nearest_neighbor_distance(
    targets: np.ndarray,
    training_responses: np.ndarray,
    *,
    spans: np.ndarray = NORMALIZATION_SPANS,
) -> np.ndarray:
    """Return Euclidean nearest-neighbor distance in frozen-span coordinates."""

    from scipy.spatial import cKDTree

    target_values = np.asarray(targets, dtype=float)
    training_values = np.asarray(training_responses, dtype=float)
    span_values = np.asarray(spans, dtype=float)
    if (
        target_values.ndim != 2
        or training_values.ndim != 2
        or target_values.shape[1] != 4
        or training_values.shape[1] != 4
    ):
        raise ValueError("support-distance inputs must be N x 4 and M x 4")
    if len(training_values) == 0:
        raise ValueError("training response cloud is empty")
    if not np.isfinite(target_values).all() or not np.isfinite(training_values).all():
        raise ValueError("support-distance inputs contain NaN or Inf")
    tree = cKDTree(training_values / span_values[None, :])
    distance, _ = tree.query(target_values / span_values[None, :], k=1)
    return np.asarray(distance, dtype=float)


def support_distance_quintiles(
    distances: np.ndarray,
    joint_errors: np.ndarray,
    q_shortfall: np.ndarray,
) -> list[dict[str, Any]]:
    """Summarize proxy error by empirical nearest-neighbor-distance quintile."""

    distance_values = np.asarray(distances, dtype=float)
    joint_values = np.asarray(joint_errors, dtype=float)
    q_values = np.asarray(q_shortfall, dtype=float)
    if not (distance_values.shape == joint_values.shape == q_values.shape):
        raise ValueError("support quintile arrays must align")
    edges = np.quantile(distance_values, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    labels = np.searchsorted(edges[1:-1], distance_values, side="right") + 1
    rows: list[dict[str, Any]] = []
    for quintile in range(1, 6):
        mask = labels == quintile
        rows.append(
            {
                "quintile": quintile,
                "count": int(np.sum(mask)),
                "distance_min": float(np.min(distance_values[mask])),
                "distance_max": float(np.max(distance_values[mask])),
                "distance_p50": float(np.median(distance_values[mask])),
                "joint_error_mean": float(np.mean(joint_values[mask])),
                "joint_error_p50": float(np.median(joint_values[mask])),
                "joint_error_p95": percentile(joint_values[mask], 95.0),
                "q_shortfall_mean": float(np.mean(q_values[mask])),
                "q_shortfall_p95": percentile(q_values[mask], 95.0),
            }
        )
    return rows


def headline_metrics(values: np.ndarray) -> dict[str, float]:
    """Return the three headline statistics for one joint-error definition."""

    errors = np.asarray(values, dtype=float)
    if errors.ndim != 1 or len(errors) == 0 or not np.isfinite(errors).all():
        raise ValueError("headline metrics require a finite non-empty vector")
    return {
        "median": float(np.median(errors)),
        "le_10_fraction": float(np.mean(errors <= 0.10)),
    }


def existing_headline_gate(
    absolute_q_joint: np.ndarray,
    legacy_q_shortfall_joint: np.ndarray,
) -> dict[str, Any]:
    """Detect the known semantic mismatch between old and newly requested headlines."""

    exact = headline_metrics(absolute_q_joint)
    legacy = headline_metrics(legacy_q_shortfall_joint)
    expected = {"median_percent": 12.46, "le_10_percent": 43.0}
    exact_rounded = {
        "median_percent": round(exact["median"] * 100.0, 2),
        "le_10_percent": round(exact["le_10_fraction"] * 100.0, 1),
    }
    legacy_rounded = {
        "median_percent": round(legacy["median"] * 100.0, 2),
        "le_10_percent": round(legacy["le_10_fraction"] * 100.0, 1),
    }
    return {
        "status": "PASS" if exact_rounded == expected else "MISMATCH",
        "expected_existing_displayed": expected,
        "absolute_q_joint": {**exact, "rounded": exact_rounded},
        "legacy_q_shortfall_joint": {**legacy, "rounded": legacy_rounded},
        "legacy_reproduces_existing_display": legacy_rounded == expected,
        "scientific_conflict": (
            "The requested new joint-RMSE uses absolute Q error, while the existing "
            "12.46%/43.0% display uses one-sided Q shortfall. The two definitions "
            "cannot share one headline."
        ),
    }


def canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    """Hash a JSON-compatible object using stable UTF-8 serialization."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def figure_sidecar(
    *,
    figure_path: str | Path,
    source_files: Sequence[str | Path],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a source-hash-bound figure sidecar payload."""

    sources = [
        {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
        for path in source_files
    ]
    return {
        "schema": "fixed8k_application_figure_binding.v1",
        "figure": str(Path(figure_path).resolve()),
        "sources": sources,
        "metadata": dict(metadata),
    }


def validate_figure_sidecar_sources(payload: Mapping[str, Any]) -> None:
    """Fail closed unless every figure-sidecar source still matches its SHA."""

    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("figure sidecar has no source files")
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("figure sidecar source entry is invalid")
        require_sha256(str(source.get("path") or ""), str(source.get("sha256") or ""))
