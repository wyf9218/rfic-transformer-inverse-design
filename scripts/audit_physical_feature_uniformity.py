#!/usr/bin/env python3
"""Audit Lp/Ls/Q/K reasonableness and distribution uniformity.

This gate is intended for large EMX chunks after response-feature extraction.
It does not generate labels. It only checks simulator-derived physical
features already present in a CSV such as
physical_feature_inverse_training_table.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np


FEATURE_ALIASES = {
    "lp": ("input__lp_nh_center", "phys__lp_nh_center", "lp_nh_center"),
    "ls": ("input__ls_nh_center", "phys__ls_nh_center", "ls_nh_center"),
    "q": ("input__q_center", "phys__q_center", "q_center"),
    "qp": ("input__qp_center", "phys__qp_center", "qp_center"),
    "qs": ("input__qs_center", "phys__qs_center", "qs_center"),
    "k": ("input__k_center", "phys__k_center", "k_center"),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    in_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(in_csv)
    features, rejects, source_columns = _extract_features(rows, args)
    ranges = _feature_ranges(features, args)
    k_sign_diagnostics = _k_sign_diagnostics(features, args)
    checks = _build_checks(rows, features, rejects, ranges, args)
    one_d = _one_dimensional_uniformity(features, ranges, args)
    pairwise = _pairwise_uniformity(features, ranges, args)
    four_d = _four_dimensional_uniformity(features, ranges, args)
    checks.extend(_uniformity_checks(one_d, pairwise, four_d, args))
    plots = _write_plots(out_dir, features, ranges, one_d, pairwise, args)
    checks.extend(_plot_checks(plots, features, args))

    status = "PASS" if all(item["status"] != "FAIL" for item in checks) and features["count"] else "FAIL"
    summary_path = out_dir / "physical_feature_uniformity_summary.json"
    report_path = out_dir / "physical_feature_uniformity_report.md"
    one_d_csv_path = out_dir / "physical_feature_uniformity_1d.csv"
    pair_csv_path = out_dir / "physical_feature_uniformity_pairwise.csv"
    manifest_path = out_dir / "physical_feature_uniformity_manifest.json"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_csv": str(in_csv),
        "out_dir": str(out_dir),
        "overall_status": status,
        "row_count": len(rows),
        "valid_feature_count": int(features["count"]),
        "feature_source_columns": source_columns,
        "k_mode": args.k_mode,
        "k_sign_diagnostics": k_sign_diagnostics,
        "ranges": ranges,
        "one_dimensional_uniformity": one_d,
        "pairwise_uniformity": pairwise,
        "four_dimensional_uniformity": four_d,
        "distribution_thresholds": {
            "min_1d_occupied_fraction": float(args.min_1d_occupied_frac),
            "min_1d_normalized_entropy": float(args.min_1d_entropy_frac),
            "max_1d_nonzero_bin_imbalance": float(args.max_1d_bin_imbalance),
            "min_pair_occupied_fraction": float(args.min_pair_occupied_frac),
            "min_pair_normalized_entropy": float(args.min_pair_entropy_frac),
            "require_four_d_gate": bool(args.require_four_d_gate),
            "min_four_d_occupied_fraction": float(args.min_four_d_occupied_frac),
            "min_four_d_normalized_entropy": float(args.min_four_d_entropy_frac),
            "max_four_d_nonzero_bin_imbalance": float(args.max_four_d_bin_imbalance),
        },
        "reject_summary": rejects,
        "plots": plots,
        "artifact_manifest": str(manifest_path),
        "checks": checks,
        "limitations": [
            "This audit checks extracted EMX-derived labels only; it does not run EMX/HFSS/ADS.",
            "A PASS means the configured plausibility and distribution gates pass for this CSV.",
            "If no explicit physical target ranges are provided, uniformity is measured over observed ranges and should be treated as diagnostic, not as a final design-range claim.",
        ],
    }
    _write_json(summary_path, summary)
    report_path.write_text(_report(summary), encoding="utf-8")
    _write_metric_csv(one_d_csv_path, one_d)
    _write_pair_csv(pair_csv_path, pairwise)
    manifest = _artifact_manifest(
        out_dir=out_dir,
        summary=summary,
        required_paths={
            "summary_json": summary_path,
            "report_md": report_path,
            "one_dimensional_csv": one_d_csv_path,
            "pairwise_csv": pair_csv_path,
        },
        plots=plots,
        require_plots=bool(args.require_plots and features["count"]),
    )
    _write_json(manifest_path, manifest)
    print(f"overall_status={status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"manifest={manifest_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-valid-count", type=int, default=1)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--pair-bins", type=int, default=10)
    parser.add_argument("--four-d-bins", type=int, default=4)
    parser.add_argument("--k-mode", choices=["magnitude", "signed"], default="magnitude")
    parser.add_argument("--min-l-nh", type=float, default=0.0)
    parser.add_argument("--min-q", type=float, default=0.0)
    parser.add_argument("--max-abs-k", type=float, default=1.05)
    parser.add_argument("--max-outside-range-frac", type=float, default=0.02)
    parser.add_argument("--lp-min-nh", type=float)
    parser.add_argument("--lp-max-nh", type=float)
    parser.add_argument("--ls-min-nh", type=float)
    parser.add_argument("--ls-max-nh", type=float)
    parser.add_argument("--q-min", type=float)
    parser.add_argument("--q-max", type=float)
    parser.add_argument("--k-min", type=float)
    parser.add_argument("--k-max", type=float)
    parser.add_argument("--require-explicit-ranges", action="store_true")
    parser.add_argument("--min-1d-occupied-frac", type=float, default=0.90)
    parser.add_argument("--min-1d-entropy-frac", type=float, default=0.90)
    parser.add_argument("--max-1d-bin-imbalance", type=float, default=2.50)
    parser.add_argument("--min-pair-occupied-frac", type=float, default=0.65)
    parser.add_argument("--min-pair-entropy-frac", type=float, default=0.80)
    parser.add_argument("--require-four-d-gate", action="store_true")
    parser.add_argument("--min-four-d-occupied-frac", type=float, default=0.50)
    parser.add_argument("--min-four-d-entropy-frac", type=float, default=0.80)
    parser.add_argument("--max-four-d-bin-imbalance", type=float, default=4.0)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--require-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 <= args.min_four_d_occupied_frac <= 1.0:
        parser.error("--min-four-d-occupied-frac must be in [0,1]")
    if not 0.0 <= args.min_four_d_entropy_frac <= 1.0:
        parser.error("--min-four-d-entropy-frac must be in [0,1]")
    if not math.isfinite(args.max_four_d_bin_imbalance) or args.max_four_d_bin_imbalance < 1.0:
        parser.error("--max-four-d-bin-imbalance must be finite and at least 1")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _extract_features(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    values: list[list[float]] = []
    signed_k_values: list[float] = []
    rejects = {"missing_or_nonfinite": 0, "nonpositive_l_or_q": 0, "k_out_of_plausible_bound": 0}
    source_columns = {}
    for row in rows:
        lp, lp_col = _first_float_with_column(row, FEATURE_ALIASES["lp"])
        ls, ls_col = _first_float_with_column(row, FEATURE_ALIASES["ls"])
        q, q_col = _first_float_with_column(row, FEATURE_ALIASES["q"])
        if q is None:
            qp, qp_col = _first_float_with_column(row, FEATURE_ALIASES["qp"])
            qs, qs_col = _first_float_with_column(row, FEATURE_ALIASES["qs"])
            if qp is not None and qs is not None:
                q = min(qp, qs)
                q_col = f"min({qp_col},{qs_col})"
        k, k_col = _first_float_with_column(row, FEATURE_ALIASES["k"])
        if any(item is None or not math.isfinite(float(item)) for item in (lp, ls, q, k)):
            rejects["missing_or_nonfinite"] += 1
            continue
        lp_f, ls_f, q_f, k_f = float(lp), float(ls), float(q), float(k)
        if lp_f <= args.min_l_nh or ls_f <= args.min_l_nh or q_f <= args.min_q:
            rejects["nonpositive_l_or_q"] += 1
            continue
        if abs(k_f) > float(args.max_abs_k):
            rejects["k_out_of_plausible_bound"] += 1
            continue
        signed_k_values.append(k_f)
        if args.k_mode == "magnitude":
            k_f = abs(k_f)
        values.append([lp_f, ls_f, q_f, k_f])
        for key, col in (("lp", lp_col), ("ls", ls_col), ("q", q_col), ("k", k_col)):
            if col and key not in source_columns:
                source_columns[key] = col
    arr = np.asarray(values, dtype=float) if values else np.empty((0, 4), dtype=float)
    signed_k_arr = np.asarray(signed_k_values, dtype=float) if signed_k_values else np.empty((0,), dtype=float)
    return {"columns": ["lp", "ls", "q", "k"], "values": arr, "k_signed_values": signed_k_arr, "count": arr.shape[0]}, rejects, source_columns


def _k_sign_diagnostics(features: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    signed = features.get("k_signed_values")
    if not isinstance(signed, np.ndarray) or signed.size == 0:
        return {
            "k_mode": args.k_mode,
            "uniformity_k_axis": "|K|" if args.k_mode == "magnitude" else "signed K",
            "signed_k_count": 0,
            "interpretation": "No valid signed K values were available after plausibility filtering.",
        }
    abs_values = np.abs(signed)
    negative_count = int(np.sum(signed < 0))
    zero_count = int(np.sum(signed == 0))
    positive_count = int(np.sum(signed > 0))
    count = int(signed.size)
    return {
        "k_mode": args.k_mode,
        "uniformity_k_axis": "|K|" if args.k_mode == "magnitude" else "signed K",
        "signed_k_count": count,
        "positive_k_count": positive_count,
        "zero_k_count": zero_count,
        "negative_k_count": negative_count,
        "negative_k_fraction": float(negative_count / count),
        "min_signed_k": float(np.min(signed)),
        "max_signed_k": float(np.max(signed)),
        "min_abs_k": float(np.min(abs_values)),
        "max_abs_k": float(np.max(abs_values)),
        "max_abs_k_allowed": float(args.max_abs_k),
        "interpretation": (
            "Uniformity is evaluated on |K| as coupling-strength coverage; signed K is retained here as a diagnostic. "
            "Rows with abs(K) above max_abs_k are rejected before this summary."
            if args.k_mode == "magnitude"
            else "Uniformity is evaluated on signed K; abs(K) plausibility filtering is still applied."
        ),
    }


def _first_float_with_column(row: dict[str, str], names: tuple[str, ...]) -> tuple[float | None, str | None]:
    for name in names:
        value = _as_float(row.get(name))
        if value is not None:
            return value, name
    return None, None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _feature_ranges(features: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    values = features["values"]
    explicit = {
        "lp": (args.lp_min_nh, args.lp_max_nh),
        "ls": (args.ls_min_nh, args.ls_max_nh),
        "q": (args.q_min, args.q_max),
        "k": (args.k_min, args.k_max),
    }
    ranges: dict[str, Any] = {}
    for idx, name in enumerate(features["columns"]):
        lo, hi = explicit[name]
        configured = lo is not None and hi is not None
        if configured:
            lo_f, hi_f = float(lo), float(hi)
            source = "explicit"
        elif values.shape[0]:
            lo_f, hi_f = _padded_range(values[:, idx])
            source = "observed_with_5pct_padding"
        else:
            lo_f = hi_f = float("nan")
            source = "missing"
        ranges[name] = {
            "min": lo_f,
            "max": hi_f,
            "source": source,
            "explicit": configured,
            "valid": math.isfinite(lo_f) and math.isfinite(hi_f) and hi_f > lo_f,
        }
    return ranges


def _padded_range(values: np.ndarray) -> tuple[float, float]:
    lo = float(np.nanmin(values))
    hi = float(np.nanmax(values))
    if math.isclose(lo, hi):
        pad = max(abs(lo) * 0.05, 0.5)
    else:
        pad = 0.05 * (hi - lo)
    return lo - pad, hi + pad


def _build_checks(
    rows: list[dict[str, str]],
    features: dict[str, Any],
    rejects: dict[str, Any],
    ranges: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    checks = [
        _check(bool(rows), "training rows present", f"rows={len(rows)}"),
        _check(features["count"] >= int(args.min_valid_count), "minimum valid physical rows", f"valid={features['count']}, required={args.min_valid_count}"),
        _check(rejects["nonpositive_l_or_q"] == 0, "positive Lp/Ls/Q", f"rejected={rejects['nonpositive_l_or_q']}"),
        _check(rejects["k_out_of_plausible_bound"] == 0, "plausible |K|", f"rejected={rejects['k_out_of_plausible_bound']}, max_abs_k={args.max_abs_k}"),
    ]
    if args.require_explicit_ranges:
        missing = [name for name, item in ranges.items() if not item["explicit"]]
        checks.append(_check(not missing, "explicit physical target ranges", f"missing={missing}"))
    values = features["values"]
    if values.shape[0]:
        for idx, name in enumerate(features["columns"]):
            item = ranges[name]
            checks.append(_check(item["valid"], f"{name} range valid", f"range={item}"))
            if item["valid"] and item["explicit"]:
                arr = values[:, idx]
                outside = np.count_nonzero((arr < item["min"]) | (arr > item["max"]))
                frac = outside / arr.size if arr.size else 1.0
                checks.append(_check(frac <= float(args.max_outside_range_frac), f"{name} outside explicit range", f"outside={outside}, fraction={frac:.6g}, limit={args.max_outside_range_frac}"))
    return checks


def _one_dimensional_uniformity(features: dict[str, Any], ranges: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    values = features["values"]
    result: dict[str, Any] = {}
    if values.shape[0] == 0:
        return result
    for idx, name in enumerate(features["columns"]):
        item = ranges[name]
        counts = _histogram(values[:, idx], item["min"], item["max"], int(args.bins))
        result[name] = _hist_summary(counts)
    return result


def _pairwise_uniformity(features: dict[str, Any], ranges: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    values = features["values"]
    result: dict[str, Any] = {}
    if values.shape[0] == 0:
        return result
    columns = features["columns"]
    for i, j in combinations(range(len(columns)), 2):
        left, right = columns[i], columns[j]
        left_range = ranges[left]
        right_range = ranges[right]
        counts, _x_edges, _y_edges = np.histogram2d(
            values[:, i],
            values[:, j],
            bins=int(args.pair_bins),
            range=[(left_range["min"], left_range["max"]), (right_range["min"], right_range["max"])],
        )
        result[f"{left}_{right}"] = _hist_summary(counts)
    return result


def _four_dimensional_uniformity(features: dict[str, Any], ranges: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    values = features["values"]
    if values.shape[0] == 0:
        return {}
    hist_ranges = [(ranges[name]["min"], ranges[name]["max"]) for name in features["columns"]]
    counts, _edges = np.histogramdd(values, bins=[int(args.four_d_bins)] * 4, range=hist_ranges)
    return _hist_summary(counts)


def _histogram(values: np.ndarray, lo: float, hi: float, bins: int) -> np.ndarray:
    counts, _edges = np.histogram(values, bins=max(2, int(bins)), range=(lo, hi))
    return counts.astype(float)


def _hist_summary(counts: np.ndarray) -> dict[str, Any]:
    flat = counts.astype(float).ravel()
    total = float(np.sum(flat))
    occupied = int(np.count_nonzero(flat))
    nonzero = flat[flat > 0]
    expected = total / flat.size if flat.size else 0.0
    entropy = _normalized_entropy(flat)
    imbalance = float(np.max(nonzero) / np.min(nonzero)) if nonzero.size else float("inf")
    cv = float(np.std(flat) / expected) if expected > 0 else float("inf")
    return {
        "bin_count": int(flat.size),
        "sample_count": int(total),
        "occupied_bins": occupied,
        "occupied_fraction": occupied / flat.size if flat.size else 0.0,
        "empty_bins": int(flat.size - occupied),
        "empty_fraction": 1.0 - occupied / flat.size if flat.size else 1.0,
        "min_count": int(np.min(flat)) if flat.size else 0,
        "max_count": int(np.max(flat)) if flat.size else 0,
        "nonzero_min_count": int(np.min(nonzero)) if nonzero.size else 0,
        "nonzero_max_count": int(np.max(nonzero)) if nonzero.size else 0,
        "max_to_min_nonzero_ratio": imbalance,
        "coefficient_of_variation": cv,
        "normalized_entropy": entropy,
    }


def _normalized_entropy(counts: np.ndarray) -> float:
    flat = counts.astype(float).ravel()
    total = float(np.sum(flat))
    if total <= 0 or flat.size <= 1:
        return 0.0
    p = flat[flat > 0] / total
    entropy = -float(np.sum(p * np.log(p)))
    return entropy / math.log(flat.size)


def _uniformity_checks(one_d: dict[str, Any], pairwise: dict[str, Any], four_d: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, item in one_d.items():
        checks.append(_check(item["occupied_fraction"] >= float(args.min_1d_occupied_frac), f"{name} 1D occupied bins", f"occupied_fraction={item['occupied_fraction']:.6g}, required={args.min_1d_occupied_frac}"))
        checks.append(_check(item["normalized_entropy"] >= float(args.min_1d_entropy_frac), f"{name} 1D entropy", f"entropy={item['normalized_entropy']:.6g}, required={args.min_1d_entropy_frac}"))
        checks.append(_check(item["max_to_min_nonzero_ratio"] <= float(args.max_1d_bin_imbalance), f"{name} 1D bin imbalance", f"ratio={item['max_to_min_nonzero_ratio']:.6g}, limit={args.max_1d_bin_imbalance}"))
    for name, item in pairwise.items():
        checks.append(_check(item["occupied_fraction"] >= float(args.min_pair_occupied_frac), f"{name} pair occupied bins", f"occupied_fraction={item['occupied_fraction']:.6g}, required={args.min_pair_occupied_frac}"))
        checks.append(_check(item["normalized_entropy"] >= float(args.min_pair_entropy_frac), f"{name} pair entropy", f"entropy={item['normalized_entropy']:.6g}, required={args.min_pair_entropy_frac}"))
    if args.require_four_d_gate and four_d:
        checks.append(_check(four_d["occupied_fraction"] >= float(args.min_four_d_occupied_frac), "Lp/Ls/Q/K 4D occupied bins", f"occupied_fraction={four_d['occupied_fraction']:.6g}, required={args.min_four_d_occupied_frac}"))
        checks.append(_check(four_d["normalized_entropy"] >= float(args.min_four_d_entropy_frac), "Lp/Ls/Q/K 4D normalized entropy", f"entropy={four_d['normalized_entropy']:.6g}, required={args.min_four_d_entropy_frac}"))
        checks.append(_check(four_d["max_to_min_nonzero_ratio"] <= float(args.max_four_d_bin_imbalance), "Lp/Ls/Q/K 4D nonzero-bin imbalance", f"ratio={four_d['max_to_min_nonzero_ratio']:.6g}, limit={args.max_four_d_bin_imbalance}"))
    return checks


def _plot_checks(plots: dict[str, str], features: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if not args.require_plots or features["count"] == 0:
        return []
    required = ("marginal_histograms", "pair_scatter", "pair_occupancy_heatmaps")
    checks = []
    for name in required:
        path = Path(plots.get(name, ""))
        exists = path.is_file() and path.stat().st_size > 0
        checks.append(_check(exists, f"{name} visual artifact", str(path) if str(path) != "." else "missing"))
    return checks


def _write_plots(
    out_dir: Path,
    features: dict[str, Any],
    ranges: dict[str, Any],
    one_d: dict[str, Any],
    pairwise: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, str]:
    if args.no_plots or features["count"] == 0:
        return {}
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return {}
    values = features["values"]
    cols = features["columns"]
    paths: dict[str, str] = {}

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for ax, name, arr in zip(axes.ravel(), cols, values.T):
        r = ranges[name]
        ax.hist(arr, bins=int(args.bins), range=(r["min"], r["max"]), color="#2563eb", edgecolor="white")
        ax.set_title(f"{name.upper()} marginal")
        ax.set_xlabel(name)
        ax.set_ylabel("count")
        ax.grid(True, alpha=0.25)
        if name in one_d:
            ax.text(
                0.02,
                0.96,
                _marginal_annotation(one_d[name]),
                transform=ax.transAxes,
                va="top",
                fontsize=9,
            )
    fig.tight_layout()
    path = out_dir / "physical_feature_marginal_histograms.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["marginal_histograms"] = str(path)

    pairs = [("lp", "ls"), ("q", "k")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, (x_name, y_name) in zip(axes, pairs):
        x_idx = cols.index(x_name)
        y_idx = cols.index(y_name)
        ax.scatter(values[:, x_idx], values[:, y_idx], s=5, alpha=0.45)
        ax.set_xlabel(x_name)
        ax.set_ylabel(y_name)
        ax.set_title(f"{x_name.upper()} vs {y_name.upper()}")
        ax.grid(True, alpha=0.25)
    fig.tight_layout()
    path = out_dir / "physical_feature_pair_scatter.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["pair_scatter"] = str(path)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, (x_name, y_name) in zip(axes, pairs):
        x_idx = cols.index(x_name)
        y_idx = cols.index(y_name)
        xr = ranges[x_name]
        yr = ranges[y_name]
        counts, _x_edges, _y_edges = np.histogram2d(
            values[:, x_idx],
            values[:, y_idx],
            bins=int(args.pair_bins),
            range=[(xr["min"], xr["max"]), (yr["min"], yr["max"])],
        )
        im = ax.imshow(counts.T, origin="lower", aspect="auto", cmap="viridis")
        ax.set_title(f"{x_name.upper()}-{y_name.upper()} occupancy")
        ax.set_xlabel(f"{x_name} bin")
        ax.set_ylabel(f"{y_name} bin")
        fig.colorbar(im, ax=ax, shrink=0.85)
    fig.tight_layout()
    path = out_dir / "physical_feature_pair_occupancy_heatmaps.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths["pair_occupancy_heatmaps"] = str(path)
    return paths


def _marginal_annotation(metrics: dict[str, Any]) -> str:
    return f"H={metrics['normalized_entropy']:.3f}\nocc={metrics['occupied_fraction']:.2f}"


def _artifact_manifest(
    *,
    out_dir: Path,
    summary: dict[str, Any],
    required_paths: dict[str, Path],
    plots: dict[str, str],
    require_plots: bool,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for name, path in required_paths.items():
        artifacts[name] = _artifact_entry(path)
    for name, raw_path in plots.items():
        artifacts[f"plot_{name}"] = _artifact_entry(Path(raw_path))
    if require_plots:
        for name in ("marginal_histograms", "pair_scatter", "pair_occupancy_heatmaps"):
            artifacts.setdefault(f"plot_{name}", {"path": "", "exists": False, "size_bytes": 0})
    manifest_status = "PASS" if all(item.get("exists") and item.get("size_bytes", 0) > 0 for item in artifacts.values()) else "FAIL"
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": manifest_status,
        "out_dir": str(out_dir),
        "artifact_manifest_json": str(out_dir / "physical_feature_uniformity_manifest.json"),
        "uniformity_status": summary["overall_status"],
        "valid_feature_count": summary["valid_feature_count"],
        "k_mode": summary["k_mode"],
        "require_plots": require_plots,
        "visual_artifact_count": sum(1 for key, item in artifacts.items() if key.startswith("plot_") and item.get("exists")),
        "artifacts": artifacts,
    }


def _artifact_entry(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
    }


def _check(passed: bool, name: str, detail: str) -> dict[str, Any]:
    return {"status": "PASS" if passed else "FAIL", "name": name, "detail": detail}


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_metric_csv(path: Path, metrics: dict[str, Any]) -> None:
    rows = []
    for name, item in metrics.items():
        row = {"feature": name}
        row.update(item)
        rows.append(row)
    _write_rows(path, rows)


def _write_pair_csv(path: Path, metrics: dict[str, Any]) -> None:
    rows = []
    for name, item in metrics.items():
        row = {"pair": name}
        row.update(item)
        rows.append(row)
    _write_rows(path, rows)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical Feature Uniformity Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Valid physical rows: `{summary['valid_feature_count']}` / `{summary['row_count']}`",
        f"- K mode: `{summary['k_mode']}`",
        "",
        "## K Sign Diagnostics",
    ]
    k_diag = summary.get("k_sign_diagnostics") if isinstance(summary.get("k_sign_diagnostics"), dict) else {}
    if k_diag:
        lines.extend(
            [
                f"- Uniformity K axis: `{k_diag.get('uniformity_k_axis')}`",
                f"- Signed K count: `{k_diag.get('signed_k_count')}`",
                f"- Positive / zero / negative K: `{k_diag.get('positive_k_count')}` / `{k_diag.get('zero_k_count')}` / `{k_diag.get('negative_k_count')}`",
                f"- Signed K range: `{k_diag.get('min_signed_k')}` to `{k_diag.get('max_signed_k')}`",
                f"- |K| range: `{k_diag.get('min_abs_k')}` to `{k_diag.get('max_abs_k')}`",
                f"- Interpretation: {k_diag.get('interpretation')}",
                "",
            ]
        )
    lines.extend([
        "## Checks",
    ])
    for check in summary["checks"]:
        lines.append(f"- {check['status']}: {check['name']} - {check['detail']}")
    lines.extend(["", "## Ranges"])
    for name, item in summary["ranges"].items():
        lines.append(f"- `{name}`: {item['min']:.6g} to {item['max']:.6g} ({item['source']})")
    lines.extend(["", "## 1D Uniformity"])
    for name, item in summary["one_dimensional_uniformity"].items():
        lines.append(
            f"- `{name}`: occupied={item['occupied_fraction']:.3f}, entropy={item['normalized_entropy']:.3f}, "
            f"max/min(nonzero)={item['max_to_min_nonzero_ratio']:.3f}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
