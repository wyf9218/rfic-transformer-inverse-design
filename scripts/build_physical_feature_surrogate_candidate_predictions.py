#!/usr/bin/env python3
"""Build candidate-geometry physical-feature predictions for acquisition.

This is the Lp/Ls/Q/K replacement for the older Zin candidate predictor. It
trains a small auditable KNN surrogate from completed simulator rows, then
predicts where new candidate geometries may land in physical-feature space.

Predictions from this script are only for candidate prioritization. They are
not simulator labels and must not be used for training the final inverse model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import qmc


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    feature_columns = _split_columns(args.feature_columns)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    target_bins_path = Path(args.target_bins_csv).expanduser().resolve() if args.target_bins_csv else None
    target_rows = _read_rows(target_bins_path) if target_bins_path is not None else []
    pairwise_bins_path = Path(args.pairwise_bins_csv).expanduser().resolve() if args.pairwise_bins_csv else None
    pairwise_bin_rows = _read_rows(pairwise_bins_path) if pairwise_bins_path is not None else []
    manifest = _read_manifest(args, dataset_dir)
    requested_geometry_columns = _split_columns(args.geometry_columns)
    geom_columns = _resolve_geometry_columns(
        rows,
        prefix=args.geom_prefix,
        min_span=args.min_geometry_span,
        requested=requested_geometry_columns,
    )
    missing_requested_geometry_columns = [column for column in requested_geometry_columns if column not in geom_columns]
    training = _training_matrix(rows, geom_columns, feature_columns)
    bounds = _resolve_bounds(training, geom_columns, manifest, args)

    checks = [
        _check("dataset_rows_csv_exists", dataset_csv.is_file(), str(dataset_csv)),
        _check("dataset_rows_present", bool(rows), f"rows={len(rows)}"),
        _check("geometry_columns_present", bool(geom_columns), f"columns={len(geom_columns)}"),
        _check(
            "requested_geometry_columns_resolved",
            not missing_requested_geometry_columns,
            "missing=" + ",".join(missing_requested_geometry_columns),
        ),
        _check("feature_columns_present", bool(feature_columns), ",".join(feature_columns)),
        _check("training_rows_present", training["count"] > 0, f"rows={training['count']}"),
        _check("bounds_valid", bool(bounds), f"bounds={len(bounds)}"),
        _check("candidate_count_positive", int(args.candidate_count) > 0, args.candidate_count),
        _check("prediction_batch_size_positive", int(args.prediction_batch_size) > 0, args.prediction_batch_size),
        _check(
            "local_target_fraction_valid",
            0.0 <= float(args.local_target_fraction) <= 1.0,
            args.local_target_fraction,
        ),
        _check(
            "rare_marginal_fraction_valid",
            0.0 <= float(args.rare_marginal_fraction) <= 1.0,
            args.rare_marginal_fraction,
        ),
        _check(
            "pairwise_target_fraction_valid",
            0.0 <= float(args.pairwise_target_fraction) <= 1.0,
            args.pairwise_target_fraction,
        ),
        _check(
            "local_fraction_sum_valid",
            (
                float(args.local_target_fraction)
                + float(args.rare_marginal_fraction)
                + float(args.pairwise_target_fraction)
            )
            <= 1.0,
            (
                float(args.local_target_fraction)
                + float(args.rare_marginal_fraction)
                + float(args.pairwise_target_fraction)
            ),
        ),
    ]
    if float(args.local_target_fraction) > 0.0:
        checks.extend(
            [
                _check(
                    "target_bins_csv_exists",
                    target_bins_path is not None and target_bins_path.is_file(),
                    str(target_bins_path) if target_bins_path is not None else "",
                ),
                _check("target_bin_rows_present", bool(target_rows), f"rows={len(target_rows)}"),
                _check("local_seed_count_positive", int(args.local_seed_count) > 0, args.local_seed_count),
                _check("local_perturbation_scales_valid", bool(_local_scales(args)), args.local_perturbation_scales),
                _check(
                    "local_seed_anchor_strength_valid",
                    0.0 <= float(args.local_seed_anchor_strength) <= 1.0,
                    args.local_seed_anchor_strength,
                ),
                _check(
                    "local_seed_anchor_radius_positive",
                    float(args.local_seed_anchor_radius) > 0.0,
                    args.local_seed_anchor_radius,
                ),
            ]
        )
    if float(args.rare_marginal_fraction) > 0.0:
        checks.extend(
            [
                _check("rare_marginal_bins_valid", int(args.rare_marginal_bins) >= 2, args.rare_marginal_bins),
                _check("local_perturbation_scales_valid_for_rare_margins", bool(_local_scales(args)), args.local_perturbation_scales),
            ]
        )
    if float(args.pairwise_target_fraction) > 0.0:
        checks.extend(
            [
                _check(
                    "pairwise_bins_csv_exists",
                    pairwise_bins_path is not None and pairwise_bins_path.is_file(),
                    str(pairwise_bins_path) if pairwise_bins_path is not None else "",
                ),
                _check("pairwise_bin_rows_present", bool(pairwise_bin_rows), f"rows={len(pairwise_bin_rows)}"),
                _check(
                    "pairwise_feature_pairs_valid",
                    bool(_parse_feature_pairs(args.pairwise_feature_pairs, feature_columns)),
                    args.pairwise_feature_pairs,
                ),
                _check("local_seed_count_positive_for_pairwise", int(args.local_seed_count) > 0, args.local_seed_count),
                _check(
                    "local_perturbation_scales_valid_for_pairwise",
                    bool(_local_scales(args)),
                    args.local_perturbation_scales,
                ),
            ]
        )

    candidate_rows: list[dict[str, Any]] = []
    validation = {"status": "NOT_RUN"}
    if all(item["pass"] for item in checks):
        validation = _cross_validate(training, bounds, feature_columns, args)
        candidate_rows = _build_candidates(
            training,
            bounds,
            feature_columns,
            target_rows,
            pairwise_bin_rows,
            args,
        )
        checks.append(_check("candidate_predictions_present", bool(candidate_rows), f"rows={len(candidate_rows)}"))

    candidate_csv = out_dir / "candidate_physical_feature_predictions.csv"
    summary_path = out_dir / "candidate_physical_feature_prediction_summary.json"
    report_path = out_dir / "candidate_physical_feature_prediction_report.md"
    _write_csv(candidate_csv, candidate_rows)
    figures = [] if args.no_plots or not candidate_rows else _write_plots(candidate_rows, feature_columns, out_dir)

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_CANDIDATE_PREDICTIONS_ONLY" if status == "PASS" else "DO_NOT_USE_CANDIDATE_PREDICTIONS",
        "dataset_dir": str(dataset_dir),
        "dataset_source": _file_source(dataset_csv),
        "out_dir": str(out_dir),
        "candidate_csv": str(candidate_csv),
        "figures": figures,
        "row_count": len(rows),
        "training_count": training["count"],
        "candidate_count": len(candidate_rows),
        "geometry_columns": geom_columns,
        "requested_geometry_columns": requested_geometry_columns,
        "feature_columns": feature_columns,
        "target_bins_csv": str(target_bins_path) if target_bins_path is not None else "",
        "target_bin_count": len(target_rows),
        "pairwise_bins_csv": str(pairwise_bins_path) if pairwise_bins_path is not None else "",
        "pairwise_bin_count": len(pairwise_bin_rows),
        "candidate_generation": _candidate_generation_summary(candidate_rows),
        "bounds": bounds,
        "validation": validation,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "Predicted physical features are not labels and must not be used as EMX/HFSS/ADS ground truth.",
            "Use the candidate CSV only to prioritize the next Cadence/EMX acquisition batch.",
            "Local real-seed anchoring is a continuity heuristic for rare-bin acquisition; its improvement must be measured from the resulting EMX labels.",
            "Pairwise-gap local candidates are seeded from the nearest real-label rows and remain acquisition hypotheses, not evidence that an empty Lp-Q or Ls-Q cell is physically reachable.",
            "The selected candidates still require geometry DRC, EMX .s4p generation, physical-feature extraction, and sampled EMX/HFSS validation.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"candidate_csv={candidate_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--candidate-count", type=int, default=5000)
    parser.add_argument("--prediction-batch-size", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=20260615)
    parser.add_argument(
        "--lhs-optimization",
        choices=["none", "random-cd", "lloyd"],
        default="random-cd",
        help="Latin-hypercube post-optimization. Use none for large candidate pools where speed matters.",
    )
    parser.add_argument("--k-neighbors", type=int, default=8)
    parser.add_argument("--distance-power", type=float, default=2.0)
    parser.add_argument(
        "--target-bins-csv",
        help="Optional sparse-bin target CSV used to seed local geometry perturbations around rare real-label rows.",
    )
    parser.add_argument(
        "--local-target-fraction",
        type=float,
        default=0.0,
        help="Fraction of candidates generated by target-aware local perturbation; zero preserves global-LHS-only behavior.",
    )
    parser.add_argument(
        "--rare-marginal-fraction",
        type=float,
        default=0.0,
        help=(
            "Fraction of candidates locally perturbed around real rows in underfilled one-dimensional "
            "Lp/Ls/Q/|K| bins. This complements coarse four-dimensional target bins."
        ),
    )
    parser.add_argument(
        "--pairwise-target-fraction",
        type=float,
        default=0.0,
        help=(
            "Fraction of candidates locally perturbed around real rows nearest to underfilled pairwise "
            "physical-feature bins. This is candidate generation, not selector quota."
        ),
    )
    parser.add_argument(
        "--pairwise-bins-csv",
        help="Full physical_feature_acquisition_bins.csv used to derive pairwise deficits.",
    )
    parser.add_argument(
        "--pairwise-feature-pairs",
        default="lp_nh_center:q_center,ls_nh_center:q_center",
        help="Comma-separated physical-feature pairs targeted by local candidate generation.",
    )
    parser.add_argument(
        "--rare-marginal-bins",
        type=int,
        default=10,
        help="Number of explicit-range marginal bins used to identify rare real-label seeds.",
    )
    parser.add_argument(
        "--rare-marginal-feature-weights",
        default="0.5,0.5,2.0,1.5",
        help="Comma-separated priority weights corresponding to the requested feature columns.",
    )
    parser.add_argument("--local-seed-count", type=int, default=8)
    parser.add_argument(
        "--local-perturbation-scales",
        default="0.01,0.03,0.08",
        help="Comma-separated Gaussian perturbation scales as fractions of each geometry bound span.",
    )
    parser.add_argument(
        "--local-seed-anchor-strength",
        type=float,
        default=0.95,
        help=(
            "Maximum blend weight toward the real-label seed for target-aware local candidates. "
            "This only changes acquisition-priority predictions; zero disables anchoring."
        ),
    )
    parser.add_argument(
        "--local-seed-anchor-radius",
        type=float,
        default=0.03,
        help="Normalized RMS geometry distance at which the local real-seed anchor decays by exp(-1).",
    )
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument(
        "--geometry-columns",
        default="",
        help=(
            "Comma-separated independent geometry columns. Use this for merged datasets where derived or "
            "legacy geom__ columns are not populated in every acquisition round."
        ),
    )
    parser.add_argument("--geom-prefix", default="geom__")
    parser.add_argument("--min-geometry-span", type=float, default=1e-12)
    parser.add_argument("--manifest")
    parser.add_argument("--bounds-source", choices=["manifest_then_observed", "observed"], default="manifest_then_observed")
    parser.add_argument("--max-validation-rows", type=int, default=1000)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _split_columns(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_manifest(args: argparse.Namespace, dataset_dir: Path) -> dict[str, Any]:
    candidates = [Path(args.manifest).expanduser().resolve()] if args.manifest else [dataset_dir / "dataset_manifest.json"]
    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            data["_manifest_path"] = str(path)
            return data
    return {}


def _resolve_geometry_columns(
    rows: list[dict[str, str]],
    *,
    prefix: str,
    min_span: float,
    requested: list[str],
) -> list[str]:
    if requested:
        candidates = list(dict.fromkeys(requested))
    elif rows:
        candidates = sorted(key for key in rows[0] if key.startswith(prefix))
    else:
        candidates = []
    return _varying_numeric_columns(rows, candidates, min_span)


def _infer_geometry_columns(rows: list[dict[str, str]], prefix: str, min_span: float) -> list[str]:
    """Backward-compatible helper retained for callers importing the script."""
    return _resolve_geometry_columns(rows, prefix=prefix, min_span=min_span, requested=[])


def _varying_numeric_columns(rows: list[dict[str, str]], candidates: list[str], min_span: float) -> list[str]:
    if not rows:
        return []
    selected: list[str] = []
    for key in candidates:
        values = [_as_float(row.get(key)) for row in rows]
        finite = [value for value in values if value is not None]
        if len(finite) < 2:
            continue
        if max(finite) - min(finite) <= float(min_span):
            continue
        selected.append(key)
    return selected


def _training_matrix(rows: list[dict[str, str]], geom_columns: list[str], feature_columns: list[str]) -> dict[str, Any]:
    vectors: list[list[float]] = []
    labels: list[list[float]] = []
    source_indices: list[int] = []
    for idx, row in enumerate(rows):
        if not _truthy(row.get("ok", "true")):
            continue
        label = [_feature_value(row, column) for column in feature_columns]
        if any(value is None for value in label):
            continue
        vector = [_as_float(row.get(column)) for column in geom_columns]
        if any(value is None for value in vector):
            continue
        vectors.append([float(value) for value in vector if value is not None])
        labels.append([float(value) for value in label if value is not None])
        source_indices.append(idx)
    if not vectors:
        return {"count": 0, "x": np.empty((0, 0)), "y": np.empty((0, len(feature_columns))), "source_indices": []}
    return {
        "count": len(vectors),
        "x": np.asarray(vectors, dtype=float),
        "y": np.asarray(labels, dtype=float),
        "source_indices": source_indices,
    }


def _feature_value(row: dict[str, str], column: str) -> float | None:
    direct = _as_float(row.get(column))
    if direct is not None:
        return direct
    normalized = str(column).strip().lower()
    if normalized in {"q", "q_center", "q_min", "q_scalar"}:
        for alias in ("q", "q_min", "q_scalar", "metrics__q", "metric__q"):
            value = _as_float(row.get(alias))
            if value is not None:
                return value
        primary = _first_float(row, ("qp_center", "q_primary_center", "q_primary", "qp", "metrics__qp", "metrics__q_primary"))
        secondary = _first_float(row, ("qs_center", "q_secondary_center", "q_secondary", "qs", "metrics__qs", "metrics__q_secondary"))
        if primary is not None and secondary is not None:
            return min(primary, secondary)
    if normalized in {"k_abs", "k_abs_center", "abs_k", "abs_k_center", "k_magnitude", "k_magnitude_center"}:
        signed = _first_float(row, ("k_center", "k", "kw_center", "kw", "metrics__k", "metrics__kw"))
        if signed is not None:
            return abs(signed)
    return None


def _first_float(row: dict[str, str], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _resolve_bounds(
    training: dict[str, Any],
    geom_columns: list[str],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, dict[str, float | str]]:
    if training["count"] == 0:
        return {}
    x = training["x"]
    manifest_bounds = manifest.get("bounds") if isinstance(manifest, dict) else {}
    manifest_bounds = manifest_bounds if isinstance(manifest_bounds, dict) else {}
    bounds: dict[str, dict[str, float | str]] = {}
    for col_idx, column in enumerate(geom_columns):
        observed_min = float(np.min(x[:, col_idx]))
        observed_max = float(np.max(x[:, col_idx]))
        lo = observed_min
        hi = observed_max
        source = "observed_training_range"
        raw_key = column.removeprefix(args.geom_prefix)
        maybe = manifest_bounds.get(raw_key)
        if args.bounds_source == "manifest_then_observed" and isinstance(maybe, (list, tuple)) and len(maybe) == 2:
            lo_candidate = _as_float(maybe[0])
            hi_candidate = _as_float(maybe[1])
            if lo_candidate is not None and hi_candidate is not None and hi_candidate > lo_candidate:
                lo = lo_candidate
                hi = hi_candidate
                source = "dataset_manifest_bounds"
        if hi <= lo:
            continue
        bounds[column] = {
            "min": float(lo),
            "max": float(hi),
            "observed_min": observed_min,
            "observed_max": observed_max,
            "source": source,
        }
    return bounds


def _cross_validate(
    training: dict[str, Any],
    bounds: dict[str, dict[str, float | str]],
    feature_columns: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    x = _normalize(training["x"], bounds)
    y = training["y"]
    count = x.shape[0]
    if count < 3:
        return {"status": "SKIPPED_TOO_FEW_ROWS", "row_count": int(count)}
    rng = np.random.default_rng(int(args.seed))
    indices = np.arange(count)
    if count > int(args.max_validation_rows):
        indices = np.sort(rng.choice(indices, size=int(args.max_validation_rows), replace=False))
    k = max(1, min(int(args.k_neighbors), count - 1))
    errors = {column: [] for column in feature_columns}
    for idx in indices:
        distances = np.linalg.norm(x - x[idx], axis=1)
        order = np.argsort(distances)
        neighbors = [item for item in order if item != idx][:k]
        pred, _unc, _mean_distance = _predict_from_neighbors(y, distances, neighbors, args.distance_power)
        for col_idx, column in enumerate(feature_columns):
            errors[column].append(abs(float(pred[col_idx] - y[idx, col_idx])))
    return {
        "status": "PASS",
        "method": "leave_one_out_knn_idw",
        "validated_rows": len(indices),
        "k_neighbors": k,
        "absolute_error": {column: _error_summary(values) for column, values in errors.items()},
    }


def _build_candidates(
    training: dict[str, Any],
    bounds: dict[str, dict[str, float | str]],
    feature_columns: list[str],
    target_rows: list[dict[str, str]],
    pairwise_bin_rows: list[dict[str, str]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    field_order = list(bounds)
    dims = len(field_order)
    count = max(0, int(args.candidate_count))
    if dims == 0 or count <= 0:
        return []
    lows = np.asarray([float(bounds[key]["min"]) for key in field_order], dtype=float)
    highs = np.asarray([float(bounds[key]["max"]) for key in field_order], dtype=float)
    candidates_x, candidate_metadata = _candidate_geometry_vectors(
        training,
        target_rows,
        pairwise_bin_rows,
        feature_columns,
        lows,
        highs,
        count,
        args,
    )
    train_x = _normalize(training["x"], bounds)
    train_y = training["y"]
    k = max(1, min(int(args.k_neighbors), train_x.shape[0]))
    tree = cKDTree(train_x)

    rows: list[dict[str, Any]] = []
    batch_size = max(1, int(args.prediction_batch_size))
    denom = np.maximum(highs - lows, 1e-12)
    for start in range(0, count, batch_size):
        stop = min(count, start + batch_size)
        batch_vectors = candidates_x[start:stop]
        batch_norm = (batch_vectors - lows) / denom
        neighbor_distances, neighbors = tree.query(batch_norm, k=k)
        if k == 1:
            neighbor_distances = neighbor_distances[:, None]
            neighbors = neighbors[:, None]
        neighbor_distances = np.asarray(neighbor_distances, dtype=float)
        neighbors = np.asarray(neighbors, dtype=int)
        neighbor_y = train_y[neighbors]

        zero_rows = np.any(neighbor_distances < 1e-12, axis=1)
        weights = 1.0 / np.maximum(neighbor_distances, 1e-12) ** float(args.distance_power)
        weights = weights / np.maximum(np.sum(weights, axis=1, keepdims=True), 1e-12)
        preds = np.sum(neighbor_y * weights[:, :, None], axis=1)
        uncertainty = np.sqrt(np.sum(((neighbor_y - preds[:, None, :]) ** 2) * weights[:, :, None], axis=1))
        if np.any(zero_rows):
            zero_choice = np.argmax(neighbor_distances[zero_rows] < 1e-12, axis=1)
            preds[zero_rows] = neighbor_y[zero_rows, zero_choice, :]
            uncertainty[zero_rows] = 0.0
        mean_distances = np.mean(neighbor_distances, axis=1)

        anchor_strength = min(1.0, max(0.0, float(args.local_seed_anchor_strength)))
        anchor_radius = max(float(args.local_seed_anchor_radius), 1.0e-12)
        for offset, metadata in enumerate(candidate_metadata[start:stop]):
            metadata["candidate_seed_anchor_weight"] = 0.0
            metadata["candidate_seed_geometry_rms_distance"] = ""
            if metadata.get("candidate_generation_mode") not in {
                "local_sparse_target_perturbation",
                "local_rare_marginal_perturbation",
                "local_pairwise_gap_perturbation",
            }:
                continue
            try:
                seed_index = int(metadata.get("candidate_seed_training_index"))
            except (TypeError, ValueError):
                continue
            if seed_index < 0 or seed_index >= train_x.shape[0] or anchor_strength <= 0.0:
                continue
            rms_distance = float(np.sqrt(np.mean((batch_norm[offset] - train_x[seed_index]) ** 2)))
            anchor_weight = float(anchor_strength * math.exp(-((rms_distance / anchor_radius) ** 2)))
            seed_features = train_y[seed_index]
            unanchored = preds[offset].copy()
            preds[offset] = (1.0 - anchor_weight) * unanchored + anchor_weight * seed_features
            uncertainty[offset] = np.sqrt(
                uncertainty[offset] ** 2 + (anchor_weight * np.abs(seed_features - unanchored)) ** 2
            )
            metadata["candidate_seed_anchor_weight"] = anchor_weight
            metadata["candidate_seed_geometry_rms_distance"] = rms_distance

        for offset, vector in enumerate(batch_vectors):
            idx = start + offset
            row: dict[str, Any] = {
                "candidate_id": f"physical_feature_candidate_{idx:06d}",
                "pred_neighbor_mean_distance": float(mean_distances[offset]),
                "pred_source": (
                    "knn_idw_with_local_real_seed_anchor_for_candidate_priority_only"
                    if float(candidate_metadata[idx].get("candidate_seed_anchor_weight") or 0.0) > 0.0
                    else "knn_idw_surrogate_for_candidate_priority_only"
                ),
                "pred_k_neighbors": k,
                **candidate_metadata[idx],
            }
            for col_idx, column in enumerate(feature_columns):
                row[f"pred_{column}"] = float(preds[offset, col_idx])
                row[f"pred_uncertainty_{column}"] = float(uncertainty[offset, col_idx])
            for col_idx, column in enumerate(field_order):
                row[column] = float(vector[col_idx])
            rows.append(row)
    return rows


def _candidate_geometry_vectors(
    training: dict[str, Any],
    target_rows: list[dict[str, str]],
    pairwise_bin_rows: list[dict[str, str]],
    feature_columns: list[str],
    lows: np.ndarray,
    highs: np.ndarray,
    count: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    local_fraction = min(1.0, max(0.0, float(args.local_target_fraction)))
    rare_fraction = min(1.0, max(0.0, float(args.rare_marginal_fraction)))
    pairwise_fraction = min(1.0, max(0.0, float(args.pairwise_target_fraction)))
    local_count = int(round(count * local_fraction)) if target_rows else 0
    rare_count = int(round(count * rare_fraction))
    pairwise_count = int(round(count * pairwise_fraction))
    global_count = count - local_count - rare_count - pairwise_count
    if global_count < 0:
        overflow = -global_count
        reduction = min(pairwise_count, overflow)
        pairwise_count -= reduction
        overflow -= reduction
        reduction = min(rare_count, overflow)
        rare_count -= reduction
        overflow -= reduction
        local_count -= min(local_count, overflow)
        global_count = 0
    vectors: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []

    if local_count > 0:
        local_vectors, local_metadata = _local_target_vectors(
            training,
            target_rows,
            feature_columns,
            lows,
            highs,
            local_count,
            args,
        )
        vectors.append(local_vectors)
        metadata.extend(local_metadata)

    if rare_count > 0:
        rare_vectors, rare_metadata = _rare_marginal_vectors(
            training,
            target_rows,
            feature_columns,
            lows,
            highs,
            rare_count,
            args,
        )
        if int(rare_vectors.shape[0]) == rare_count:
            vectors.append(rare_vectors)
            metadata.extend(rare_metadata)
        else:
            global_count += rare_count

    if pairwise_count > 0:
        pairwise_vectors, pairwise_metadata = _pairwise_target_vectors(
            training,
            pairwise_bin_rows,
            feature_columns,
            lows,
            highs,
            pairwise_count,
            args,
        )
        if int(pairwise_vectors.shape[0]) == pairwise_count:
            vectors.append(pairwise_vectors)
            metadata.extend(pairwise_metadata)
        else:
            global_count += pairwise_count

    if global_count > 0:
        optimization = None if args.lhs_optimization == "none" else args.lhs_optimization
        sampler = qmc.LatinHypercube(d=len(lows), seed=int(args.seed), optimization=optimization)
        unit = sampler.random(n=global_count)
        vectors.append(qmc.scale(unit, lows, highs))
        metadata.extend(
            {
                "candidate_generation_mode": "global_latin_hypercube",
                "candidate_target_bin_key": "",
                "candidate_seed_training_index": "",
                "candidate_local_scale": "",
                "candidate_seed_anchor_weight": 0.0,
                "candidate_seed_geometry_rms_distance": "",
                "candidate_marginal_feature": "",
                "candidate_marginal_bin": "",
                "candidate_marginal_seed_count": "",
                "candidate_marginal_min": "",
                "candidate_marginal_max": "",
                "candidate_marginal_target": "",
                "candidate_marginal_priority_weight": "",
            }
            for _ in range(global_count)
        )

    if not vectors:
        return np.empty((0, len(lows)), dtype=float), []
    return np.vstack(vectors), metadata


def _local_target_vectors(
    training: dict[str, Any],
    target_rows: list[dict[str, str]],
    feature_columns: list[str],
    lows: np.ndarray,
    highs: np.ndarray,
    count: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    rng = np.random.default_rng(int(args.seed) + 1_000_003)
    valid_targets = [row for row in target_rows if _target_center(row, feature_columns) is not None]
    if not valid_targets or count <= 0:
        return np.empty((0, len(lows)), dtype=float), []

    allocations = _weighted_allocations(valid_targets, count)
    feature_lows = np.asarray(
        [min(float(row[f"{column}__min"]) for row in valid_targets) for column in feature_columns],
        dtype=float,
    )
    feature_highs = np.asarray(
        [max(float(row[f"{column}__max"]) for row in valid_targets) for column in feature_columns],
        dtype=float,
    )
    feature_spans = np.maximum(feature_highs - feature_lows, 1.0e-12)
    geometry_spans = np.maximum(highs - lows, 1.0e-12)
    seed_count = max(1, min(int(args.local_seed_count), int(training["count"])))
    scales = _local_scales(args)
    train_x = np.asarray(training["x"], dtype=float)
    train_y = np.asarray(training["y"], dtype=float)
    vectors: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []

    for target, allocation in zip(valid_targets, allocations):
        if allocation <= 0:
            continue
        center = np.asarray(_target_center(target, feature_columns), dtype=float)
        distances = np.sqrt(np.mean(((train_y - center[None, :]) / feature_spans[None, :]) ** 2, axis=1))
        seed_indices = np.argsort(distances, kind="stable")[:seed_count]
        for local_index in range(allocation):
            seed_index = int(seed_indices[local_index % len(seed_indices)])
            scale = float(scales[(local_index // len(seed_indices)) % len(scales)])
            applied_scale = min(scales) * 0.25 if local_index < len(seed_indices) else scale
            vector = train_x[seed_index] + rng.normal(0.0, applied_scale, size=train_x.shape[1]) * geometry_spans
            vector = np.clip(vector, lows, highs)
            vectors.append(vector)
            metadata.append(
                {
                    "candidate_generation_mode": "local_sparse_target_perturbation",
                    "candidate_target_bin_key": target.get("bin_key", ""),
                    "candidate_seed_training_index": seed_index,
                    "candidate_seed_target_distance": float(distances[seed_index]),
                    "candidate_local_scale": applied_scale,
                }
            )
    return np.asarray(vectors, dtype=float), metadata


def _target_center(row: dict[str, str], feature_columns: list[str]) -> list[float] | None:
    values = [_as_float(row.get(f"{column}__target")) for column in feature_columns]
    if any(value is None for value in values):
        return None
    return [float(value) for value in values if value is not None]


def _rare_marginal_vectors(
    training: dict[str, Any],
    target_rows: list[dict[str, str]],
    feature_columns: list[str],
    lows: np.ndarray,
    highs: np.ndarray,
    count: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    if count <= 0 or int(training["count"]) <= 0:
        return np.empty((0, len(lows)), dtype=float), []
    train_x = np.asarray(training["x"], dtype=float)
    train_y = np.asarray(training["y"], dtype=float)
    marginal_bins = max(2, int(args.rare_marginal_bins))
    feature_lows, feature_highs = _feature_target_bounds(train_y, target_rows, feature_columns)
    spans = np.maximum(feature_highs - feature_lows, 1.0e-12)
    bin_indices = np.floor((train_y - feature_lows[None, :]) / spans[None, :] * marginal_bins).astype(int)
    bin_indices = np.clip(bin_indices, 0, marginal_bins - 1)
    feature_weights = _rare_feature_weights(args, len(feature_columns))
    desired_per_bin = int(math.ceil(float(train_y.shape[0]) / float(marginal_bins)))

    categories: list[dict[str, Any]] = []
    for feature_index, feature_name in enumerate(feature_columns):
        for bin_index in range(marginal_bins):
            seed_indices = np.flatnonzero(bin_indices[:, feature_index] == bin_index)
            current = int(seed_indices.size)
            if current <= 0 or current >= desired_per_bin:
                continue
            deficit = desired_per_bin - current
            categories.append(
                {
                    "feature_index": feature_index,
                    "feature_name": feature_name,
                    "bin_index": bin_index,
                    "seed_indices": seed_indices,
                    "current_count": current,
                    "weight": float(deficit) * float(feature_weights[feature_index]),
                }
            )
    if not categories:
        return np.empty((0, len(lows)), dtype=float), []

    total_weight = sum(max(1.0e-12, float(item["weight"])) for item in categories)
    exact = [max(1.0e-12, float(item["weight"])) / total_weight * count for item in categories]
    allocations = [int(math.floor(value)) for value in exact]
    remainder = count - sum(allocations)
    order = sorted(range(len(categories)), key=lambda index: (-(exact[index] - allocations[index]), index))
    for index in order[:remainder]:
        allocations[index] += 1

    rng = np.random.default_rng(int(args.seed) + 2_000_003)
    geometry_spans = np.maximum(highs - lows, 1.0e-12)
    scales = _local_scales(args)
    vectors: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for category, allocation in zip(categories, allocations):
        if allocation <= 0:
            continue
        seeds = np.asarray(category["seed_indices"], dtype=int).copy()
        rng.shuffle(seeds)
        feature_index = int(category["feature_index"])
        bin_index = int(category["bin_index"])
        bin_low = float(feature_lows[feature_index] + spans[feature_index] * bin_index / marginal_bins)
        bin_high = float(feature_lows[feature_index] + spans[feature_index] * (bin_index + 1) / marginal_bins)
        for local_index in range(allocation):
            seed_index = int(seeds[local_index % len(seeds)])
            scale = float(scales[(local_index // len(seeds)) % len(scales)])
            applied_scale = min(scales) * 0.25 if local_index < len(seeds) else scale
            vector = train_x[seed_index] + rng.normal(0.0, applied_scale, size=train_x.shape[1]) * geometry_spans
            vector = np.clip(vector, lows, highs)
            vectors.append(vector)
            metadata.append(
                {
                    "candidate_generation_mode": "local_rare_marginal_perturbation",
                    "candidate_target_bin_key": "marginal:{}:{}".format(
                        category["feature_name"], category["bin_index"]
                    ),
                    "candidate_seed_training_index": seed_index,
                    "candidate_seed_target_distance": "",
                    "candidate_local_scale": applied_scale,
                    "candidate_marginal_feature": category["feature_name"],
                    "candidate_marginal_bin": int(category["bin_index"]),
                    "candidate_marginal_seed_count": int(category["current_count"]),
                    "candidate_marginal_min": bin_low,
                    "candidate_marginal_max": bin_high,
                    "candidate_marginal_target": (bin_low + bin_high) / 2.0,
                    "candidate_marginal_priority_weight": float(category["weight"]),
                }
            )
    return np.asarray(vectors, dtype=float), metadata


def _pairwise_target_vectors(
    training: dict[str, Any],
    pairwise_bin_rows: list[dict[str, str]],
    feature_columns: list[str],
    lows: np.ndarray,
    highs: np.ndarray,
    count: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Perturb real geometries nearest to underfilled pairwise feature cells."""

    if count <= 0 or int(training["count"]) <= 0 or not pairwise_bin_rows:
        return np.empty((0, len(lows)), dtype=float), []
    pairs = _parse_feature_pairs(args.pairwise_feature_pairs, feature_columns)
    categories = _pairwise_deficit_categories(pairwise_bin_rows, feature_columns, pairs)
    if not categories:
        return np.empty((0, len(lows)), dtype=float), []

    allocations = _weighted_count_allocations(
        [float(category["deficit_fraction"]) for category in categories],
        count,
    )
    train_x = np.asarray(training["x"], dtype=float)
    train_y = np.asarray(training["y"], dtype=float)
    feature_lows, feature_highs = _feature_target_bounds(train_y, pairwise_bin_rows, feature_columns)
    feature_spans = np.maximum(feature_highs - feature_lows, 1.0e-12)
    geometry_spans = np.maximum(highs - lows, 1.0e-12)
    seed_count = max(1, min(int(args.local_seed_count), int(training["count"])))
    scales = _local_scales(args)
    rng = np.random.default_rng(int(args.seed) + 3_000_003)
    vectors: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []

    for category, allocation in zip(categories, allocations):
        if allocation <= 0:
            continue
        feature_a = str(category["feature_a"])
        feature_b = str(category["feature_b"])
        axes = (feature_columns.index(feature_a), feature_columns.index(feature_b))
        center = np.asarray([category["target_a"], category["target_b"]], dtype=float)
        pair_spans = feature_spans[np.asarray(axes, dtype=int)]
        distances = np.sqrt(
            np.mean(((train_y[:, np.asarray(axes, dtype=int)] - center[None, :]) / pair_spans[None, :]) ** 2, axis=1)
        )
        seed_indices = np.argsort(distances, kind="stable")[:seed_count]
        for local_index in range(allocation):
            seed_index = int(seed_indices[local_index % len(seed_indices)])
            scale = float(scales[(local_index // len(seed_indices)) % len(scales)])
            applied_scale = min(scales) * 0.25 if local_index < len(seed_indices) else scale
            vector = train_x[seed_index] + rng.normal(0.0, applied_scale, size=train_x.shape[1]) * geometry_spans
            vector = np.clip(vector, lows, highs)
            vectors.append(vector)
            metadata.append(
                {
                    "candidate_generation_mode": "local_pairwise_gap_perturbation",
                    "candidate_target_bin_key": "pairwise:{}:{}:{}:{}".format(
                        feature_a,
                        category["bin_a"],
                        feature_b,
                        category["bin_b"],
                    ),
                    "candidate_seed_training_index": seed_index,
                    "candidate_seed_target_distance": float(distances[seed_index]),
                    "candidate_local_scale": applied_scale,
                    "candidate_pairwise_features": f"{feature_a}:{feature_b}",
                    "candidate_pairwise_feature_a": feature_a,
                    "candidate_pairwise_feature_b": feature_b,
                    "candidate_pairwise_bin_a": int(category["bin_a"]),
                    "candidate_pairwise_bin_b": int(category["bin_b"]),
                    "candidate_pairwise_target_a": float(category["target_a"]),
                    "candidate_pairwise_target_b": float(category["target_b"]),
                    "candidate_pairwise_current_count": int(category["current_count"]),
                    "candidate_pairwise_target_count": int(category["target_count"]),
                    "candidate_pairwise_deficit_fraction": float(category["deficit_fraction"]),
                }
            )
    return np.asarray(vectors, dtype=float), metadata


def _pairwise_deficit_categories(
    bin_rows: list[dict[str, str]],
    feature_columns: list[str],
    pairs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    for feature_a, feature_b in pairs:
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        try:
            for row in bin_rows:
                bin_a = int(float(row[f"{feature_a}__bin"]))
                bin_b = int(float(row[f"{feature_b}__bin"]))
                current = int(float(row.get("current_count") or 0))
                target = int(float(row.get("target_count") or 0))
                key = (bin_a, bin_b)
                item = grouped.setdefault(
                    key,
                    {
                        "feature_a": feature_a,
                        "feature_b": feature_b,
                        "bin_a": bin_a,
                        "bin_b": bin_b,
                        "lower_a": float(row[f"{feature_a}__min"]),
                        "upper_a": float(row[f"{feature_a}__max"]),
                        "lower_b": float(row[f"{feature_b}__min"]),
                        "upper_b": float(row[f"{feature_b}__max"]),
                        "current_count": 0,
                        "target_count": 0,
                    },
                )
                item["current_count"] += current
                item["target_count"] += target
        except (KeyError, TypeError, ValueError):
            return []
        for item in grouped.values():
            target = int(item["target_count"])
            current = int(item["current_count"])
            if target <= 0 or current >= target:
                continue
            item["target_a"] = 0.5 * (float(item["lower_a"]) + float(item["upper_a"]))
            item["target_b"] = 0.5 * (float(item["lower_b"]) + float(item["upper_b"]))
            item["deficit_fraction"] = float((target - current) / target)
            categories.append(item)
    return sorted(
        categories,
        key=lambda item: (
            feature_columns.index(str(item["feature_a"])),
            feature_columns.index(str(item["feature_b"])),
            int(item["bin_a"]),
            int(item["bin_b"]),
        ),
    )


def _parse_feature_pairs(raw: str, feature_columns: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for token in str(raw).split(","):
        parts = [item.strip() for item in token.split(":") if item.strip()]
        if len(parts) != 2 or parts[0] == parts[1]:
            continue
        if parts[0] not in feature_columns or parts[1] not in feature_columns:
            continue
        ordered = tuple(sorted(parts, key=feature_columns.index))
        if ordered not in pairs:
            pairs.append(ordered)
    return pairs


def _weighted_count_allocations(weights: list[float], total: int) -> list[int]:
    if not weights:
        return []
    positive = np.asarray([max(0.0, float(value)) for value in weights], dtype=float)
    if float(np.sum(positive)) <= 0.0:
        positive = np.ones(len(weights), dtype=float)
    exact = positive / np.sum(positive) * max(0, int(total))
    allocations = np.floor(exact).astype(int)
    remainder = int(total) - int(np.sum(allocations))
    order = np.argsort(-(exact - allocations), kind="stable")
    for index in order[:remainder]:
        allocations[int(index)] += 1
    return allocations.tolist()


def _feature_target_bounds(
    train_y: np.ndarray,
    target_rows: list[dict[str, str]],
    feature_columns: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    lows: list[float] = []
    highs: list[float] = []
    for feature_index, column in enumerate(feature_columns):
        target_lows = [_as_float(row.get(f"{column}__min")) for row in target_rows]
        target_highs = [_as_float(row.get(f"{column}__max")) for row in target_rows]
        finite_lows = [float(value) for value in target_lows if value is not None]
        finite_highs = [float(value) for value in target_highs if value is not None]
        lows.append(min(finite_lows) if finite_lows else float(np.min(train_y[:, feature_index])))
        highs.append(max(finite_highs) if finite_highs else float(np.max(train_y[:, feature_index])))
    return np.asarray(lows, dtype=float), np.asarray(highs, dtype=float)


def _rare_feature_weights(args: argparse.Namespace, count: int) -> list[float]:
    parsed = [
        float(value)
        for item in str(args.rare_marginal_feature_weights).split(",")
        if (value := _as_float(item.strip())) is not None and value > 0.0
    ]
    if len(parsed) != count:
        return [1.0] * count
    return parsed


def _weighted_allocations(target_rows: list[dict[str, str]], total: int) -> list[int]:
    if not target_rows:
        return []
    weights = np.asarray(
        [max(1.0, float(_as_float(row.get("recommended_new_samples")) or 1.0)) for row in target_rows],
        dtype=float,
    )
    exact = weights / np.sum(weights) * max(0, int(total))
    allocations = np.floor(exact).astype(int)
    remainder = int(total) - int(np.sum(allocations))
    order = np.argsort(-(exact - allocations), kind="stable")
    for index in order[:remainder]:
        allocations[int(index)] += 1
    return allocations.tolist()


def _local_scales(args: argparse.Namespace) -> list[float]:
    values = []
    for item in str(args.local_perturbation_scales).split(","):
        value = _as_float(item.strip())
        if value is not None and value > 0.0:
            values.append(float(value))
    return values


def _candidate_generation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    target_bins: set[str] = set()
    target_bins_by_mode: dict[str, set[str]] = {}
    anchor_weights: list[float] = []
    for row in rows:
        mode = str(row.get("candidate_generation_mode") or "unspecified")
        counts[mode] = counts.get(mode, 0) + 1
        if row.get("candidate_target_bin_key") not in (None, ""):
            target_key = str(row["candidate_target_bin_key"])
            target_bins.add(target_key)
            target_bins_by_mode.setdefault(mode, set()).add(target_key)
        anchor_weight = _as_float(row.get("candidate_seed_anchor_weight"))
        if anchor_weight is not None and anchor_weight > 0.0:
            anchor_weights.append(float(anchor_weight))
    return {
        "mode_counts": counts,
        "local_target_bin_count": len(target_bins),
        "target_bin_count_by_mode": {mode: len(keys) for mode, keys in sorted(target_bins_by_mode.items())},
        "candidate_count": len(rows),
        "anchored_candidate_count": len(anchor_weights),
        "anchor_weight_mean": float(np.mean(anchor_weights)) if anchor_weights else 0.0,
        "anchor_weight_max": max(anchor_weights) if anchor_weights else 0.0,
    }


def _normalize(x: np.ndarray, bounds: dict[str, dict[str, float | str]]) -> np.ndarray:
    field_order = list(bounds)
    lows = np.asarray([float(bounds[key]["min"]) for key in field_order], dtype=float)
    highs = np.asarray([float(bounds[key]["max"]) for key in field_order], dtype=float)
    return (np.asarray(x, dtype=float) - lows) / np.maximum(highs - lows, 1e-12)


def _predict_from_neighbors(
    y: np.ndarray,
    distances: np.ndarray,
    neighbors: list[int],
    power: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    neighbor_distances = np.asarray([float(distances[idx]) for idx in neighbors], dtype=float)
    neighbor_y = y[np.asarray(neighbors, dtype=int), :]
    if np.any(neighbor_distances < 1e-12):
        pred = neighbor_y[int(np.argmin(neighbor_distances))]
        uncertainty = np.zeros(y.shape[1], dtype=float)
    else:
        weights = 1.0 / np.maximum(neighbor_distances, 1e-12) ** float(power)
        weights = weights / np.sum(weights)
        pred = np.sum(neighbor_y * weights[:, None], axis=0)
        uncertainty = np.sqrt(np.sum(((neighbor_y - pred) ** 2) * weights[:, None], axis=0))
    return pred, uncertainty, float(np.mean(neighbor_distances))


def _write_plots(rows: list[dict[str, Any]], feature_columns: list[str], out_dir: Path) -> list[dict[str, str]]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    if len(feature_columns) < 2:
        return []
    x_name = feature_columns[0]
    y_name = feature_columns[1]
    x = np.asarray([float(row[f"pred_{x_name}"]) for row in rows], dtype=float)
    y = np.asarray([float(row[f"pred_{y_name}"]) for row in rows], dtype=float)
    fig, ax = plt.subplots(figsize=(6.5, 5.2))
    ax.scatter(x, y, s=9, alpha=0.45)
    ax.set_xlabel(f"predicted {x_name}")
    ax.set_ylabel(f"predicted {y_name}")
    ax.set_title("Candidate predicted physical-feature scatter")
    ax.grid(True, alpha=0.25)
    path = out_dir / "candidate_predicted_physical_feature_scatter.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return [{"title": "candidate predicted physical-feature scatter", "path": str(path)}]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "none", "no", "nan"}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _error_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p90": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {"median": float(np.median(arr)), "p90": float(np.percentile(arr, 90)), "max": float(np.max(arr))}


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature Surrogate Candidate Predictions",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Training rows: `{summary['training_count']}`",
        f"Candidate rows: `{summary['candidate_count']}`",
        f"Feature columns: `{', '.join(summary['feature_columns'])}`",
        f"Candidate CSV: `{summary['candidate_csv']}`",
        "",
        "## Cross-validation",
        "",
        f"Validation status: `{summary['validation'].get('status')}`",
    ]
    validation = summary.get("validation") or {}
    if validation.get("absolute_error"):
        lines.append(f"Absolute error: `{validation['absolute_error']}`")
    lines.extend(["", "## Checks", "", "| Check | Pass | Detail |", "| --- | --- | --- |"])
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
