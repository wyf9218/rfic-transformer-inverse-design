#!/usr/bin/env python3
"""Audit input sampling distribution from dataset rows and manifest bounds.

This script recomputes input-space coverage from `dataset_rows.csv` instead of
trusting precomputed manifest uniformity. It is intended to support claims that
geometry inputs are space-filling/uniform rather than clustered or normal-like.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "sampling_distribution_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_json(dataset_dir / "dataset_manifest.json")
    rows = _read_rows(dataset_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    fields = _collect_normalized_fields(manifest, ok_rows)
    field_records = _field_records(fields, args)
    uniform_vs_normal = _uniform_vs_normal_summary(field_records, args)
    correlation = _correlation_summary(fields, args)
    space_filling = _space_filling_summary(fields, args)
    checks = _checks(manifest, rows, ok_rows, fields, field_records, uniform_vs_normal, correlation, space_filling, args)
    status = _overall_status(checks, fields)
    plots, plot_errors = _write_distribution_plots(out_dir, field_records, space_filling, args)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "arguments": {
            "bins": args.bins,
            "max_histogram_imbalance_frac": args.max_histogram_imbalance_frac,
            "max_min_norm": args.max_min_norm,
            "min_max_norm": args.min_max_norm,
            "max_correlation": args.max_correlation,
            "min_fields": args.min_fields,
            "require_uniform_closer_than_normal": args.require_uniform_closer_than_normal,
            "min_uniform_vs_normal_fields_fraction": args.min_uniform_vs_normal_fields_fraction,
            "min_histogram_entropy_frac": args.min_histogram_entropy_frac,
            "space_filling_strata": args.space_filling_strata,
            "max_space_filling_empty_strata_frac": args.max_space_filling_empty_strata_frac,
            "max_space_filling_duplicate_frac": args.max_space_filling_duplicate_frac,
            "space_filling_duplicate_round_decimals": args.space_filling_duplicate_round_decimals,
            "space_filling_max_nn_samples": args.space_filling_max_nn_samples,
            "min_space_filling_median_nn_distance": args.min_space_filling_median_nn_distance,
        },
        "field_count": len(fields),
        "field_records": field_records,
        "uniform_vs_normal_summary": uniform_vs_normal,
        "correlation_summary": correlation,
        "space_filling_summary": space_filling,
        "plots": plots,
        "plot_errors": plot_errors,
        "checks": checks,
        "limitations": [
            "This audit checks input geometry sampling only; it does not prove EM labels, Zin coverage, or HFSS/ADS agreement.",
            "The uniform-vs-normal diagnostic compares empirical CDF and histogram distances to a uniform target versus a fitted normal curve; it is descriptive evidence, not a substitute for DOE design intent.",
            "The space-filling diagnostic checks duplicate normalized design vectors, per-field strata occupancy, and sampled nearest-neighbor distances; it does not prove output-response coverage.",
            "For very small sample counts, correlation and normality diagnostics should be treated as preliminary.",
        ],
    }
    summary_path = out_dir / "sampling_distribution_audit_summary.json"
    report_path = out_dir / "sampling_distribution_audit_report.md"
    fields_path = out_dir / "sampling_distribution_fields.csv"
    corr_path = out_dir / "sampling_distribution_correlations.csv"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_fields_csv(fields_path, field_records)
    _write_correlation_csv(corr_path, correlation)

    print(f"overall_status={status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"fields_csv={fields_path}")
    print(f"correlations_csv={corr_path}")
    for check in checks:
        print(f"{check['status']:9s} {check['name']}: {check['detail']}")
    return 2 if status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--max-histogram-imbalance-frac", type=float, default=0.25)
    parser.add_argument(
        "--max-min-norm",
        type=float,
        default=0.05,
        help="Require each bounded input field to reach this normalized lower-bound neighborhood or below",
    )
    parser.add_argument(
        "--min-max-norm",
        type=float,
        default=0.95,
        help="Require each bounded input field to reach this normalized upper-bound neighborhood or above",
    )
    parser.add_argument("--max-correlation", type=float, default=0.35)
    parser.add_argument("--min-fields", type=int, default=1)
    parser.add_argument("--require-uniform-closer-than-normal", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-uniform-vs-normal-fields-fraction", type=float, default=1.0)
    parser.add_argument("--min-histogram-entropy-frac", type=float)
    parser.add_argument("--space-filling-strata", type=int, default=20)
    parser.add_argument("--max-space-filling-empty-strata-frac", type=float, default=0.0)
    parser.add_argument("--max-space-filling-duplicate-frac", type=float, default=0.0)
    parser.add_argument("--space-filling-duplicate-round-decimals", type=int, default=12)
    parser.add_argument("--space-filling-max-nn-samples", type=int, default=2000)
    parser.add_argument("--min-space-filling-median-nn-distance", type=float)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - exact parser problem is evidence.
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_normalized_fields(manifest: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, np.ndarray]:
    bounds = manifest.get("bounds")
    if not isinstance(bounds, dict):
        return {}
    result: dict[str, np.ndarray] = {}
    for field, raw_bounds in sorted(bounds.items()):
        if not isinstance(raw_bounds, (list, tuple)) or len(raw_bounds) != 2:
            continue
        lo = _as_float(raw_bounds[0])
        hi = _as_float(raw_bounds[1])
        if lo is None or hi is None or math.isclose(lo, hi):
            continue
        values = []
        for row in rows:
            value = _as_float(row.get(f"geom__{field}") or row.get(field))
            if value is not None:
                values.append((value - lo) / (hi - lo))
        if values:
            result[str(field)] = np.asarray(values, dtype=float)
    return result


def _field_records(fields: dict[str, np.ndarray], args: argparse.Namespace) -> list[dict[str, Any]]:
    records = []
    bins = max(int(args.bins), 1)
    for field, values in fields.items():
        finite = values[np.isfinite(values)]
        counts, _edges = np.histogram(finite, bins=bins, range=(0.0, 1.0))
        expected = finite.size / bins if bins else 0.0
        imbalance = float(np.max(np.abs(counts - expected)) / expected) if expected else math.inf
        empirical = counts / finite.size if finite.size else np.zeros(bins)
        uniform_probs = np.full(bins, 1.0 / bins)
        normal_probs = _fitted_normal_bin_probs(finite, bins)
        uniform_l1 = float(np.sum(np.abs(empirical - uniform_probs)))
        normal_l1 = float(np.sum(np.abs(empirical - normal_probs))) if normal_probs is not None else None
        uniform_ks = _uniform_ks_distance(finite)
        normal_ks = _fitted_normal_ks_distance(finite)
        uniform_vs_normal_ks_margin = normal_ks - uniform_ks if normal_ks is not None else None
        uniform_vs_normal_l1_margin = normal_l1 - uniform_l1 if normal_l1 is not None else None
        uniform_closer = bool(uniform_vs_normal_ks_margin is not None and uniform_vs_normal_ks_margin >= 0.0)
        entropy_frac = _histogram_entropy_frac(counts)
        edge_center_ratio = _edge_center_density_ratio(counts)
        out_of_bounds = int(np.sum((finite < -1.0e-9) | (finite > 1.0 + 1.0e-9)))
        entropy_ok = (
            args.min_histogram_entropy_frac is None
            or (entropy_frac is not None and entropy_frac >= float(args.min_histogram_entropy_frac))
        )
        min_norm = float(np.min(finite)) if finite.size else None
        max_norm = float(np.max(finite)) if finite.size else None
        min_boundary_ok = bool(min_norm is not None and min_norm <= float(args.max_min_norm))
        max_boundary_ok = bool(max_norm is not None and max_norm >= float(args.min_max_norm))
        boundary_coverage_ok = min_boundary_ok and max_boundary_ok
        pass_field = (
            finite.size > 0
            and out_of_bounds == 0
            and imbalance <= float(args.max_histogram_imbalance_frac)
            and entropy_ok
            and boundary_coverage_ok
        )
        records.append(
            {
                "field": field,
                "status": "PASS" if pass_field else "FAIL",
                "count": int(finite.size),
                "min_norm": min_norm,
                "max_norm": max_norm,
                "max_allowed_min_norm": float(args.max_min_norm),
                "min_required_max_norm": float(args.min_max_norm),
                "min_boundary_ok": min_boundary_ok,
                "max_boundary_ok": max_boundary_ok,
                "boundary_coverage_ok": boundary_coverage_ok,
                "mean_norm": float(np.mean(finite)) if finite.size else None,
                "std_norm": float(np.std(finite)) if finite.size else None,
                "histogram": counts.astype(int).tolist(),
                "expected_per_bin": expected,
                "max_abs_imbalance_frac": imbalance,
                "out_of_bounds_count": out_of_bounds,
                "uniform_l1_distance": uniform_l1,
                "fitted_normal_l1_distance": normal_l1,
                "uniform_vs_normal_l1_margin": uniform_vs_normal_l1_margin,
                "uniform_ks_distance": uniform_ks,
                "fitted_normal_ks_distance": normal_ks,
                "uniform_vs_normal_ks_margin": uniform_vs_normal_ks_margin,
                "uniform_closer_than_normal": uniform_closer,
                "histogram_entropy_frac": entropy_frac,
                "edge_center_density_ratio": edge_center_ratio,
            }
        )
    return records


def _fitted_normal_bin_probs(values: np.ndarray, bins: int) -> np.ndarray | None:
    if values.size < 2:
        return None
    sigma = float(np.std(values, ddof=1))
    if sigma <= 0.0 or not math.isfinite(sigma):
        return None
    mu = float(np.mean(values))
    edges = np.linspace(0.0, 1.0, bins + 1)
    probs = np.asarray([_normal_cdf(edge, mu, sigma) for edge in edges], dtype=float)
    masses = np.diff(probs)
    total = float(np.sum(masses))
    if total <= 0.0 or not math.isfinite(total):
        return None
    return masses / total


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _uniform_ks_distance(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    sorted_values = np.sort(np.clip(finite, 0.0, 1.0))
    n = sorted_values.size
    upper = np.arange(1, n + 1, dtype=float) / n - sorted_values
    lower = sorted_values - np.arange(0, n, dtype=float) / n
    return float(np.max(np.maximum(upper, lower)))


def _fitted_normal_ks_distance(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size < 2:
        return None
    sigma = float(np.std(finite, ddof=1))
    if sigma <= 0.0 or not math.isfinite(sigma):
        return None
    mu = float(np.mean(finite))
    cdf_lo = _normal_cdf(0.0, mu, sigma)
    cdf_hi = _normal_cdf(1.0, mu, sigma)
    total = cdf_hi - cdf_lo
    if total <= 0.0 or not math.isfinite(total):
        return None

    def cdf(x: float) -> float:
        clipped = min(1.0, max(0.0, float(x)))
        return min(1.0, max(0.0, (_normal_cdf(clipped, mu, sigma) - cdf_lo) / total))

    sorted_values = np.sort(np.clip(finite, 0.0, 1.0))
    n = sorted_values.size
    cdf_values = np.asarray([cdf(value) for value in sorted_values], dtype=float)
    upper = np.arange(1, n + 1, dtype=float) / n - cdf_values
    lower = cdf_values - np.arange(0, n, dtype=float) / n
    return float(np.max(np.maximum(upper, lower)))


def _histogram_entropy_frac(counts: np.ndarray) -> float | None:
    total = float(np.sum(counts))
    if total <= 0.0 or len(counts) <= 1:
        return None
    probabilities = counts.astype(float) / total
    positive = probabilities[probabilities > 0.0]
    entropy = -float(np.sum(positive * np.log(positive)))
    return entropy / math.log(len(counts))


def _edge_center_density_ratio(counts: np.ndarray) -> float | None:
    if len(counts) < 3:
        return None
    edge_indices = [0, len(counts) - 1]
    if len(counts) % 2 == 0:
        center_indices = [len(counts) // 2 - 1, len(counts) // 2]
    else:
        center_indices = [len(counts) // 2]
    edge_density = float(np.mean(counts[edge_indices]))
    center_density = float(np.mean(counts[center_indices]))
    if center_density <= 0.0:
        return math.inf if edge_density > 0.0 else None
    return edge_density / center_density


def _uniform_vs_normal_summary(field_records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    modeled = [record for record in field_records if record.get("fitted_normal_ks_distance") is not None]
    closer = [record for record in modeled if record.get("uniform_closer_than_normal")]
    margins = [
        float(record["uniform_vs_normal_ks_margin"])
        for record in modeled
        if record.get("uniform_vs_normal_ks_margin") is not None
    ]
    entropies = [
        float(record["histogram_entropy_frac"])
        for record in field_records
        if record.get("histogram_entropy_frac") is not None
    ]
    closer_fraction = len(closer) / len(modeled) if modeled else None
    summary = {
        "field_count": len(field_records),
        "fields_with_fitted_normal_model": len(modeled),
        "closer_to_uniform_count": len(closer),
        "closer_to_uniform_fraction": closer_fraction,
        "required_closer_to_uniform_fraction": float(args.min_uniform_vs_normal_fields_fraction),
        "min_ks_margin": min(margins) if margins else None,
        "median_ks_margin": float(np.median(margins)) if margins else None,
        "max_ks_margin": max(margins) if margins else None,
        "min_histogram_entropy_frac": min(entropies) if entropies else None,
        "median_histogram_entropy_frac": float(np.median(entropies)) if entropies else None,
        "required_histogram_entropy_frac": args.min_histogram_entropy_frac,
    }
    if not field_records:
        summary["status"] = "NOT_READY"
    elif args.require_uniform_closer_than_normal and not modeled:
        summary["status"] = "FAIL"
    elif args.require_uniform_closer_than_normal and (
        closer_fraction is None or closer_fraction < float(args.min_uniform_vs_normal_fields_fraction)
    ):
        summary["status"] = "FAIL"
    elif args.min_histogram_entropy_frac is not None and (
        summary["min_histogram_entropy_frac"] is None
        or summary["min_histogram_entropy_frac"] < float(args.min_histogram_entropy_frac)
    ):
        summary["status"] = "FAIL"
    else:
        summary["status"] = "PASS"
    return summary


def _correlation_summary(fields: dict[str, np.ndarray], args: argparse.Namespace) -> dict[str, Any]:
    if len(fields) < 2:
        return {"status": "NOT_READY", "max_abs_correlation": None, "pairs": []}
    names = list(fields)
    min_len = min(len(fields[name]) for name in names)
    if min_len < 3:
        return {"status": "NOT_READY", "max_abs_correlation": None, "pairs": []}
    matrix = np.vstack([fields[name][:min_len] for name in names])
    corr = np.corrcoef(matrix)
    pairs = []
    max_abs = 0.0
    for i, left in enumerate(names):
        for j, right in enumerate(names):
            if j <= i:
                continue
            value = float(corr[i, j])
            abs_value = abs(value)
            max_abs = max(max_abs, abs_value)
            pairs.append({"left": left, "right": right, "correlation": value, "abs_correlation": abs_value})
    pairs.sort(key=lambda item: item["abs_correlation"], reverse=True)
    return {
        "status": "PASS" if max_abs <= float(args.max_correlation) else "FAIL",
        "max_abs_correlation": max_abs,
        "limit": float(args.max_correlation),
        "pairs": pairs[:50],
    }


def _normalized_matrix(fields: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    if not fields:
        return [], np.empty((0, 0), dtype=float)
    names = list(fields)
    min_len = min(len(fields[name]) for name in names)
    if min_len <= 0:
        return names, np.empty((0, len(names)), dtype=float)
    matrix = np.vstack([fields[name][:min_len] for name in names]).T
    finite_mask = np.all(np.isfinite(matrix), axis=1)
    return names, matrix[finite_mask]


def _space_filling_summary(fields: dict[str, np.ndarray], args: argparse.Namespace) -> dict[str, Any]:
    names, matrix = _normalized_matrix(fields)
    sample_count = int(matrix.shape[0])
    dimension_count = int(matrix.shape[1]) if matrix.ndim == 2 else 0
    if sample_count == 0 or dimension_count == 0:
        return {
            "status": "NOT_READY",
            "sample_count": sample_count,
            "dimension_count": dimension_count,
            "duplicate_fraction": None,
            "strata": None,
            "field_strata": [],
            "nearest_neighbor": {"status": "NOT_READY"},
        }

    decimals = max(int(args.space_filling_duplicate_round_decimals), 0)
    rounded = np.round(matrix, decimals=decimals)
    unique_count = len({tuple(row) for row in rounded.tolist()})
    duplicate_count = sample_count - unique_count
    duplicate_fraction = duplicate_count / sample_count if sample_count else None

    strata = max(1, min(int(args.space_filling_strata), sample_count))
    field_strata = []
    failed_strata_fields = []
    max_empty_frac = 0.0
    max_strata_imbalance = 0.0
    for column_index, name in enumerate(names):
        values = matrix[:, column_index]
        counts, _edges = np.histogram(values, bins=strata, range=(0.0, 1.0))
        expected = sample_count / strata if strata else 0.0
        empty_count = int(np.sum(counts == 0))
        empty_frac = empty_count / strata if strata else math.inf
        imbalance = float(np.max(np.abs(counts - expected)) / expected) if expected else math.inf
        max_empty_frac = max(max_empty_frac, empty_frac)
        max_strata_imbalance = max(max_strata_imbalance, imbalance)
        status = "PASS" if empty_frac <= float(args.max_space_filling_empty_strata_frac) else "FAIL"
        if status != "PASS":
            failed_strata_fields.append(name)
        field_strata.append(
            {
                "field": name,
                "status": status,
                "strata": strata,
                "empty_strata_count": empty_count,
                "empty_strata_frac": empty_frac,
                "max_strata_imbalance_frac": imbalance,
                "min_strata_count": int(np.min(counts)) if counts.size else None,
                "max_strata_count": int(np.max(counts)) if counts.size else None,
                "strata_counts": counts.astype(int).tolist(),
            }
        )

    nearest_neighbor = _nearest_neighbor_summary(matrix, args)
    duplicate_status = (
        "PASS"
        if duplicate_fraction is not None and duplicate_fraction <= float(args.max_space_filling_duplicate_frac)
        else "FAIL"
    )
    strata_status = "PASS" if not failed_strata_fields else "FAIL"
    nnd_status = nearest_neighbor.get("status")
    hard_fail = duplicate_status == "FAIL" or strata_status == "FAIL" or nnd_status == "FAIL"
    status = "FAIL" if hard_fail else "PASS"
    return {
        "status": status,
        "sample_count": sample_count,
        "dimension_count": dimension_count,
        "field_order": names,
        "duplicate_round_decimals": decimals,
        "unique_vector_count": unique_count,
        "duplicate_vector_count": duplicate_count,
        "duplicate_fraction": duplicate_fraction,
        "max_duplicate_fraction": float(args.max_space_filling_duplicate_frac),
        "duplicate_status": duplicate_status,
        "strata": strata,
        "max_empty_strata_frac": max_empty_frac,
        "allowed_empty_strata_frac": float(args.max_space_filling_empty_strata_frac),
        "max_strata_imbalance_frac": max_strata_imbalance,
        "failed_strata_fields": failed_strata_fields,
        "strata_status": strata_status,
        "field_strata": field_strata,
        "nearest_neighbor": nearest_neighbor,
    }


def _nearest_neighbor_summary(matrix: np.ndarray, args: argparse.Namespace) -> dict[str, Any]:
    sample_count = int(matrix.shape[0])
    dimension_count = int(matrix.shape[1]) if matrix.ndim == 2 else 0
    max_samples = max(int(args.space_filling_max_nn_samples), 0)
    if sample_count < 2 or dimension_count == 0 or max_samples < 2:
        return {"status": "NOT_READY", "sample_count": sample_count, "reason": "not enough samples or disabled"}
    chosen = min(sample_count, max_samples)
    if chosen == sample_count:
        sample = matrix
    else:
        indices = np.linspace(0, sample_count - 1, chosen, dtype=int)
        indices = np.unique(indices)
        sample = matrix[indices]
    distances = _nearest_neighbor_distances(sample)
    finite = distances[np.isfinite(distances)]
    if finite.size == 0:
        return {"status": "NOT_READY", "sample_count": int(sample.shape[0]), "reason": "no finite distances"}
    median_distance = float(np.median(finite))
    min_distance = float(np.min(finite))
    p05_distance = float(np.percentile(finite, 5.0))
    p95_distance = float(np.percentile(finite, 95.0))
    expected_scale = float(sample.shape[0] ** (-1.0 / dimension_count)) if dimension_count else None
    scale_ratio = median_distance / expected_scale if expected_scale and expected_scale > 0.0 else None
    required = args.min_space_filling_median_nn_distance
    status = "PASS"
    if required is not None and median_distance < float(required):
        status = "FAIL"
    return {
        "status": status,
        "sample_count": int(sample.shape[0]),
        "max_sample_count": max_samples,
        "dimension_count": dimension_count,
        "min_distance": min_distance,
        "p05_distance": p05_distance,
        "median_distance": median_distance,
        "p95_distance": p95_distance,
        "unit_cube_expected_scale": expected_scale,
        "median_to_expected_scale_ratio": scale_ratio,
        "min_required_median_distance": required,
        "distance_sample": finite[: min(2000, finite.size)].astype(float).tolist(),
    }


def _nearest_neighbor_distances(sample: np.ndarray, block_size: int = 256) -> np.ndarray:
    sample = np.asarray(sample, dtype=float)
    count = int(sample.shape[0])
    nearest_sq = np.full(count, math.inf, dtype=float)
    for start in range(0, count, block_size):
        stop = min(start + block_size, count)
        block = sample[start:stop]
        diff = block[:, None, :] - sample[None, :, :]
        dist_sq = np.sum(diff * diff, axis=2)
        row_indices = np.arange(stop - start)
        dist_sq[row_indices, np.arange(start, stop)] = math.inf
        nearest_sq[start:stop] = np.min(dist_sq, axis=1)
    return np.sqrt(nearest_sq)


def _checks(
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    fields: dict[str, np.ndarray],
    field_records: list[dict[str, Any]],
    uniform_vs_normal: dict[str, Any],
    correlation: dict[str, Any],
    space_filling: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = [
        _check(bool(rows), "dataset rows", f"rows={len(rows)}, ok_rows={len(ok_rows)}"),
        _check("_parse_error" not in manifest, "manifest parses", manifest.get("_parse_error", "ok") if manifest else "missing"),
        _check(len(fields) >= int(args.min_fields), "bounded input fields", f"fields={len(fields)}, required={args.min_fields}"),
    ]
    if not fields:
        return checks
    failed_boundary_fields = [record["field"] for record in field_records if not record.get("boundary_coverage_ok")]
    checks.append(
        _check(
            not failed_boundary_fields,
            "per-field boundary coverage",
            (
                f"failed={failed_boundary_fields[:8]}, "
                f"require min_norm<={args.max_min_norm:.6g} and max_norm>={args.min_max_norm:.6g}"
                if failed_boundary_fields
                else f"fields={len(field_records)}, require min_norm<={args.max_min_norm:.6g} and max_norm>={args.min_max_norm:.6g}"
            ),
        )
    )
    failed_fields = [record["field"] for record in field_records if record["status"] != "PASS"]
    checks.append(_check(not failed_fields, "per-field uniformity", f"failed={failed_fields[:8]}" if failed_fields else f"fields={len(field_records)}"))
    if args.require_uniform_closer_than_normal or args.min_histogram_entropy_frac is not None:
        fraction = uniform_vs_normal.get("closer_to_uniform_fraction")
        fraction_text = "n/a" if fraction is None else f"{fraction:.6g}"
        min_entropy = uniform_vs_normal.get("min_histogram_entropy_frac")
        entropy_text = "n/a" if min_entropy is None else f"{min_entropy:.6g}"
        checks.append(
            _check(
                uniform_vs_normal.get("status") == "PASS",
                "uniform-vs-normal evidence",
                (
                    f"closer_fraction={fraction_text}, required={args.min_uniform_vs_normal_fields_fraction:.6g}; "
                    f"min_entropy={entropy_text}, entropy_required={args.min_histogram_entropy_frac}"
                ),
            )
        )
    if correlation["status"] == "NOT_READY":
        checks.append({"status": "WARN", "name": "pairwise correlation", "detail": "not enough bounded fields/samples"})
    else:
        checks.append(_check(correlation["status"] == "PASS", "pairwise correlation", f"max_abs={correlation['max_abs_correlation']:.6g}, limit={correlation['limit']:.6g}"))
    if space_filling["status"] == "NOT_READY":
        checks.append({"status": "WARN", "name": "space-filling coverage", "detail": "not enough bounded fields/samples"})
    else:
        duplicate_fraction = space_filling.get("duplicate_fraction")
        duplicate_text = "n/a" if duplicate_fraction is None else f"{duplicate_fraction:.6g}"
        checks.append(
            _check(
                space_filling.get("duplicate_status") == "PASS",
                "space-filling duplicate vectors",
                (
                    f"duplicate_fraction={duplicate_text}, "
                    f"limit={space_filling.get('max_duplicate_fraction')}, "
                    f"rounded_decimals={space_filling.get('duplicate_round_decimals')}"
                ),
            )
        )
        checks.append(
            _check(
                space_filling.get("strata_status") == "PASS",
                "space-filling strata coverage",
                (
                    f"strata={space_filling.get('strata')}, "
                    f"max_empty_frac={space_filling.get('max_empty_strata_frac')}, "
                    f"allowed={space_filling.get('allowed_empty_strata_frac')}, "
                    f"failed={space_filling.get('failed_strata_fields', [])[:8]}"
                ),
            )
        )
        nearest = space_filling.get("nearest_neighbor", {})
        if nearest.get("status") == "FAIL":
            checks.append(
                _check(
                    False,
                    "space-filling nearest-neighbor distance",
                    f"median={nearest.get('median_distance')}, required={nearest.get('min_required_median_distance')}",
                )
            )
    return checks


def _overall_status(checks: list[dict[str, str]], fields: dict[str, np.ndarray]) -> str:
    if not fields:
        return "NOT_READY"
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if any(check["status"] == "WARN" for check in checks):
        return "WARN"
    return "PASS"


def _write_fields_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "field",
        "status",
        "count",
        "min_norm",
        "max_norm",
        "max_allowed_min_norm",
        "min_required_max_norm",
        "min_boundary_ok",
        "max_boundary_ok",
        "boundary_coverage_ok",
        "mean_norm",
        "std_norm",
        "expected_per_bin",
        "max_abs_imbalance_frac",
        "out_of_bounds_count",
        "uniform_l1_distance",
        "fitted_normal_l1_distance",
        "uniform_vs_normal_l1_margin",
        "uniform_ks_distance",
        "fitted_normal_ks_distance",
        "uniform_vs_normal_ks_margin",
        "uniform_closer_than_normal",
        "histogram_entropy_frac",
        "edge_center_density_ratio",
        "histogram",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["histogram"] = json.dumps(row["histogram"])
            writer.writerow(row)


def _write_correlation_csv(path: Path, correlation: dict[str, Any]) -> None:
    fields = ["left", "right", "correlation", "abs_correlation"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in correlation.get("pairs", []):
            writer.writerow(row)


def _write_distribution_plots(
    out_dir: Path,
    field_records: list[dict[str, Any]],
    space_filling: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, str], list[str]]:
    if not field_records:
        return {}, []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - plotting is evidence, not the gate itself.
        return {}, [f"matplotlib unavailable: {type(exc).__name__}: {exc}"]

    plots: dict[str, str] = {}
    errors: list[str] = []
    try:
        png, svg = _plot_uniform_vs_normal_ks(out_dir, field_records, plt)
        plots["uniform_vs_normal_ks_png"] = str(png)
        plots["uniform_vs_normal_ks_svg"] = str(svg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"uniform-vs-normal KS plot failed: {type(exc).__name__}: {exc}")
    try:
        png, svg = _plot_histogram_entropy(out_dir, field_records, args, plt)
        plots["histogram_entropy_png"] = str(png)
        plots["histogram_entropy_svg"] = str(svg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"histogram entropy plot failed: {type(exc).__name__}: {exc}")
    try:
        png, svg = _plot_boundary_coverage(out_dir, field_records, args, plt)
        plots["boundary_coverage_png"] = str(png)
        plots["boundary_coverage_svg"] = str(svg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"boundary coverage plot failed: {type(exc).__name__}: {exc}")
    try:
        png, svg = _plot_space_filling_strata(out_dir, space_filling, plt)
        plots["space_filling_strata_png"] = str(png)
        plots["space_filling_strata_svg"] = str(svg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"space-filling strata plot failed: {type(exc).__name__}: {exc}")
    try:
        png, svg = _plot_nearest_neighbor_distances(out_dir, space_filling, plt)
        plots["nearest_neighbor_distances_png"] = str(png)
        plots["nearest_neighbor_distances_svg"] = str(svg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"nearest-neighbor plot failed: {type(exc).__name__}: {exc}")
    return plots, errors


def _plot_uniform_vs_normal_ks(out_dir: Path, records: list[dict[str, Any]], plt: Any) -> tuple[Path, Path]:
    plot_records = [
        record
        for record in records
        if record.get("uniform_ks_distance") is not None and record.get("fitted_normal_ks_distance") is not None
    ]
    if not plot_records:
        raise ValueError("no fields have fitted normal KS distances")
    plot_records = sorted(plot_records, key=lambda item: float(item.get("uniform_vs_normal_ks_margin") or -math.inf))[:24]
    labels = [str(record["field"]) for record in plot_records]
    uniform = [float(record["uniform_ks_distance"]) for record in plot_records]
    normal = [float(record["fitted_normal_ks_distance"]) for record in plot_records]
    y = np.arange(len(labels))
    height = 0.36
    fig_height = max(4.8, 0.34 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(9.6, fig_height), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.barh(y - height / 2, uniform, height=height, color="#A3BEFA", edgecolor="#2E4780", label="Uniform KS")
    ax.barh(y + height / 2, normal, height=height, color="#F0986E", edgecolor="#804126", label="Fitted normal KS")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("KS distance (lower is closer)")
    ax.grid(axis="x", color="#E6E8F0", linewidth=0.8)
    ax.legend(loc="lower right", frameon=False)
    _style_axes(ax)
    _add_chart_header(
        fig,
        "Uniform-vs-normal distance by input field",
        "Computed from normalized geometry rows; a smaller blue bar than orange supports uniform sampling evidence.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    png = out_dir / "sampling_distribution_uniform_vs_normal_ks.png"
    svg = out_dir / "sampling_distribution_uniform_vs_normal_ks.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _plot_histogram_entropy(
    out_dir: Path,
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    plt: Any,
) -> tuple[Path, Path]:
    plot_records = [record for record in records if record.get("histogram_entropy_frac") is not None]
    if not plot_records:
        raise ValueError("no fields have histogram entropy values")
    plot_records = sorted(plot_records, key=lambda item: float(item["histogram_entropy_frac"]))[:30]
    labels = [str(record["field"]) for record in plot_records]
    values = [float(record["histogram_entropy_frac"]) for record in plot_records]
    y = np.arange(len(labels))
    fig_height = max(4.8, 0.3 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(9.6, fig_height), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.barh(y, values, color="#FFE15B", edgecolor="#736422")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0.0, 1.03)
    ax.set_xlabel("Histogram entropy / max entropy")
    ax.grid(axis="x", color="#E6E8F0", linewidth=0.8)
    if args.min_histogram_entropy_frac is not None:
        ax.axvline(float(args.min_histogram_entropy_frac), color="#464C55", linestyle="--", linewidth=1.0)
        ax.text(
            float(args.min_histogram_entropy_frac),
            -0.65,
            "required",
            ha="center",
            va="bottom",
            color="#464C55",
            fontsize=8,
        )
    _style_axes(ax)
    _add_chart_header(
        fig,
        "Histogram entropy by input field",
        "Values near 1.0 indicate bin occupancy close to uniform across the configured geometry bounds.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    png = out_dir / "sampling_distribution_histogram_entropy.png"
    svg = out_dir / "sampling_distribution_histogram_entropy.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _plot_boundary_coverage(
    out_dir: Path,
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    plt: Any,
) -> tuple[Path, Path]:
    plot_records = [record for record in records if record.get("min_norm") is not None and record.get("max_norm") is not None]
    if not plot_records:
        raise ValueError("no fields have finite normalized min/max values")
    plot_records = sorted(
        plot_records,
        key=lambda item: max(
            0.0,
            float(item["min_norm"]) - float(args.max_min_norm),
            float(args.min_max_norm) - float(item["max_norm"]),
        ),
        reverse=True,
    )[:30]
    labels = [str(record["field"]) for record in plot_records]
    y = np.arange(len(labels))
    min_values = [float(record["min_norm"]) for record in plot_records]
    max_values = [float(record["max_norm"]) for record in plot_records]
    fig_height = max(4.8, 0.32 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(9.6, fig_height), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.hlines(y, min_values, max_values, color="#9AA7B8", linewidth=2.0, alpha=0.9)
    ax.scatter(min_values, y, color="#3E7CB1", s=34, label="Observed min")
    ax.scatter(max_values, y, color="#D1495B", s=34, label="Observed max")
    ax.axvline(float(args.max_min_norm), color="#3E7CB1", linestyle="--", linewidth=1.0)
    ax.axvline(float(args.min_max_norm), color="#D1495B", linestyle="--", linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(-0.02, 1.02)
    ax.set_xlabel("Normalized design-space coordinate")
    ax.grid(axis="x", color="#E6E8F0", linewidth=0.8)
    ax.legend(loc="lower right", frameon=False)
    _style_axes(ax)
    _add_chart_header(
        fig,
        "Boundary coverage by input field",
        "Each geometry input must reach both configured normalized design-space edges before training claims are accepted.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    png = out_dir / "sampling_distribution_boundary_coverage.png"
    svg = out_dir / "sampling_distribution_boundary_coverage.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _plot_space_filling_strata(out_dir: Path, space_filling: dict[str, Any], plt: Any) -> tuple[Path, Path]:
    records = [record for record in space_filling.get("field_strata", []) if record.get("empty_strata_frac") is not None]
    if not records:
        raise ValueError("no space-filling strata records")
    records = sorted(
        records,
        key=lambda item: (float(item["empty_strata_frac"]), float(item["max_strata_imbalance_frac"])),
        reverse=True,
    )[:30]
    labels = [str(record["field"]) for record in records]
    empty = [float(record["empty_strata_frac"]) for record in records]
    imbalance = [float(record["max_strata_imbalance_frac"]) for record in records]
    y = np.arange(len(labels))
    height = 0.36
    fig_height = max(4.8, 0.32 * len(labels) + 1.8)
    fig, ax = plt.subplots(figsize=(9.6, fig_height), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.barh(y - height / 2, empty, height=height, color="#72B7B2", edgecolor="#2C625F", label="Empty strata fraction")
    ax.barh(y + height / 2, imbalance, height=height, color="#E45756", edgecolor="#742B2A", label="Max strata imbalance")
    allowed = space_filling.get("allowed_empty_strata_frac")
    if allowed is not None:
        ax.axvline(float(allowed), color="#464C55", linestyle="--", linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Fraction")
    ax.grid(axis="x", color="#E6E8F0", linewidth=0.8)
    ax.legend(loc="lower right", frameon=False)
    _style_axes(ax)
    _add_chart_header(
        fig,
        "Space-filling strata coverage by input field",
        "Each normalized design variable is split into strata; empty strata reveal holes in the planned input space.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    png = out_dir / "sampling_distribution_space_filling_strata.png"
    svg = out_dir / "sampling_distribution_space_filling_strata.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _plot_nearest_neighbor_distances(out_dir: Path, space_filling: dict[str, Any], plt: Any) -> tuple[Path, Path]:
    nearest = space_filling.get("nearest_neighbor", {})
    values = np.asarray(nearest.get("distance_sample") or [], dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("no nearest-neighbor distances")
    fig, ax = plt.subplots(figsize=(9.6, 5.2), facecolor="#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.hist(values, bins=min(40, max(8, int(math.sqrt(values.size)))), color="#A3BEFA", edgecolor="#2E4780")
    median = nearest.get("median_distance")
    if median is not None:
        ax.axvline(float(median), color="#D1495B", linewidth=1.4, label="Median")
    required = nearest.get("min_required_median_distance")
    if required is not None:
        ax.axvline(float(required), color="#464C55", linestyle="--", linewidth=1.0, label="Required median")
    ax.set_xlabel("Nearest-neighbor distance in normalized input space")
    ax.set_ylabel("Sample count")
    ax.grid(axis="y", color="#E6E8F0", linewidth=0.8)
    if median is not None or required is not None:
        ax.legend(loc="upper right", frameon=False)
    _style_axes(ax)
    _add_chart_header(
        fig,
        "Nearest-neighbor distance distribution",
        "Computed on a bounded sample of normalized design vectors to reveal duplicates or tight clusters.",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    png = out_dir / "sampling_distribution_nearest_neighbor_distances.png"
    svg = out_dir / "sampling_distribution_nearest_neighbor_distances.svg"
    fig.savefig(png, dpi=180)
    fig.savefig(svg)
    plt.close(fig)
    return png, svg


def _add_chart_header(fig: Any, title: str, subtitle: str) -> None:
    fig.text(0.08, 0.975, title, ha="left", va="top", fontsize=13, fontweight="bold", color="#1F2430")
    fig.text(0.08, 0.94, subtitle, ha="left", va="top", fontsize=9, color="#6F768A")


def _style_axes(ax: Any) -> None:
    ax.tick_params(axis="both", colors="#6F768A")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D7DBE7")
    ax.spines["bottom"].set_color("#D7DBE7")


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Sampling Distribution Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- OK rows: `{summary['rows']['ok_count']}`",
        f"- Bounded input fields: `{summary['field_count']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    uvn = summary["uniform_vs_normal_summary"]
    args = summary["arguments"]
    space = summary.get("space_filling_summary", {})
    nearest = space.get("nearest_neighbor", {})
    boundary_failed = [record["field"] for record in summary["field_records"] if not record.get("boundary_coverage_ok")]
    lines.extend(
        [
            "",
            "## Uniform-vs-Normal Evidence",
            "",
            f"- Fields with fitted normal model: `{uvn['fields_with_fitted_normal_model']}`",
            f"- Closer to uniform by KS distance: `{uvn['closer_to_uniform_count']}`",
            f"- Closer-to-uniform fraction: `{uvn['closer_to_uniform_fraction']}`",
            f"- Required closer-to-uniform fraction: `{uvn['required_closer_to_uniform_fraction']}`",
            f"- Minimum KS margin `(normal - uniform)`: `{uvn['min_ks_margin']}`",
            f"- Median histogram entropy fraction: `{uvn['median_histogram_entropy_frac']}`",
            f"- Boundary coverage limits: `min_norm <= {args['max_min_norm']}` and `max_norm >= {args['min_max_norm']}`",
            f"- Boundary coverage failed fields: `{boundary_failed}`",
            "",
            "A positive KS margin means the empirical input distribution is closer to uniform than to a fitted normal distribution.",
        ]
    )
    lines.extend(
        [
            "",
            "## Space-Filling Evidence",
            "",
            f"- Space-filling status: `{space.get('status')}`",
            f"- Normalized design vectors: `{space.get('sample_count')}` rows x `{space.get('dimension_count')}` dimensions",
            f"- Unique rounded vectors: `{space.get('unique_vector_count')}`",
            f"- Duplicate vector fraction: `{space.get('duplicate_fraction')}`",
            f"- Duplicate vector limit: `{space.get('max_duplicate_fraction')}`",
            f"- Strata per field: `{space.get('strata')}`",
            f"- Max empty strata fraction: `{space.get('max_empty_strata_frac')}`",
            f"- Allowed empty strata fraction: `{space.get('allowed_empty_strata_frac')}`",
            f"- Failed strata fields: `{space.get('failed_strata_fields')}`",
            f"- Nearest-neighbor median distance: `{nearest.get('median_distance')}`",
            f"- Nearest-neighbor median / unit-cube scale: `{nearest.get('median_to_expected_scale_ratio')}`",
            "",
            "The space-filling check is row-vector based: it catches exact/near duplicate design points and empty LHS-style strata that can be missed by single-field histogram plots alone.",
        ]
    )
    if summary.get("plots"):
        lines.extend(["", "## Plots", ""])
        for name, path in summary["plots"].items():
            lines.append(f"- {name}: `{path}`")
    if summary.get("plot_errors"):
        lines.extend(["", "## Plot Errors", ""])
        lines.extend(f"- {item}" for item in summary["plot_errors"])
    lines.extend(
        [
            "",
            "## Worst Field Imbalance",
            "",
            "| Field | Status | Min norm | Max norm | Boundary OK | Imbalance | Uniform KS | Fitted normal KS | KS margin | Entropy | Edge/center | Histogram |",
            "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    records = sorted(summary["field_records"], key=lambda item: item["max_abs_imbalance_frac"], reverse=True)
    for record in records[:12]:
        normal_ks = record["fitted_normal_ks_distance"]
        normal_text = "" if normal_ks is None else f"{normal_ks:.6g}"
        margin = record["uniform_vs_normal_ks_margin"]
        margin_text = "" if margin is None else f"{margin:.6g}"
        entropy = record["histogram_entropy_frac"]
        entropy_text = "" if entropy is None else f"{entropy:.6g}"
        edge_center = record["edge_center_density_ratio"]
        edge_center_text = "" if edge_center is None else f"{edge_center:.6g}"
        lines.append(
            f"| {record['field']} | {record['status']} | {record['min_norm']:.6g} | {record['max_norm']:.6g} | "
            f"{record['boundary_coverage_ok']} | {record['max_abs_imbalance_frac']:.6g} | "
            f"{record['uniform_ks_distance']:.6g} | {normal_text} | {margin_text} | {entropy_text} | {edge_center_text} | `{record['histogram']}` |"
        )
    lines.extend(
        [
            "",
            "## Worst Space-Filling Strata",
            "",
            "| Field | Status | Empty strata | Empty fraction | Max imbalance | Min count | Max count |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    strata_records = sorted(
        space.get("field_strata", []),
        key=lambda item: (float(item.get("empty_strata_frac") or 0.0), float(item.get("max_strata_imbalance_frac") or 0.0)),
        reverse=True,
    )
    for record in strata_records[:12]:
        lines.append(
            f"| {record['field']} | {record['status']} | {record['empty_strata_count']} | "
            f"{record['empty_strata_frac']:.6g} | {record['max_strata_imbalance_frac']:.6g} | "
            f"{record['min_strata_count']} | {record['max_strata_count']} |"
        )
    lines.extend(["", "## Correlation Summary", "", "```json", json.dumps(summary["correlation_summary"], indent=2), "```", "", "## Limits", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _check(condition: bool, name: str, detail: str) -> dict[str, str]:
    return {"status": "PASS" if condition else "FAIL", "name": name, "detail": detail}


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "fail", "failed"}


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


if __name__ == "__main__":
    raise SystemExit(main())
