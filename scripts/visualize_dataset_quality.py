#!/usr/bin/env python3
"""Create visual evidence for RFIC transformer dataset quality.

The script is read-only with respect to the dataset. It consumes a dataset
directory containing dataset_manifest.json and dataset_rows.csv, then writes
PNG figures, a Markdown index, and a machine-readable summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover - environment failure path
    print(f"ERROR: matplotlib is required to create figures: {exc}", file=sys.stderr)
    sys.exit(2)

try:  # scipy is used only for evidence statistics; figures still work without it.
    from scipy import stats as scipy_stats
except Exception:  # pragma: no cover - optional dependency
    scipy_stats = None


INPUT_FIELD_LABELS = {
    "primary_outer_width_um": "Primary outer W",
    "primary_outer_height_um": "Primary outer H",
    "secondary_outer_width_um": "Secondary outer W",
    "secondary_outer_height_um": "Secondary outer H",
    "primary_width_um": "Primary trace W",
    "secondary_width_um": "Secondary trace W",
    "primary_terminal_y_span_um": "Primary terminal span",
    "secondary_terminal_y_span_um": "Secondary terminal span",
    "offset_um": "Offset",
    "primary_feed_extension_um": "Primary feed ext.",
    "secondary_feed_extension_um": "Secondary feed ext.",
}

METRIC_ALIASES = {
    "k": ("metrics__k", "metric__k", "k"),
    "lp_h": ("metrics__lp_h", "metric__lp_h", "lp_h", "metrics__lp"),
    "ls_h": ("metrics__ls_h", "metric__ls_h", "ls_h", "metrics__ls"),
    "q_primary": ("metrics__q_primary", "metric__q_primary", "q_primary", "metrics__qp", "qp"),
    "q_secondary": ("metrics__q_secondary", "metric__q_secondary", "q_secondary", "metrics__qs", "qs"),
    "real_z11_ohm": ("metrics__real_z11_ohm", "real_z11_ohm"),
    "real_z22_ohm": ("metrics__real_z22_ohm", "real_z22_ohm"),
}

FIGURE_FOOTER = ""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    output_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "dataset_visualizations"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(dataset_dir / "dataset_manifest.json")
    rows = _load_rows(dataset_dir / "dataset_rows.csv")
    ok_rows = [row for row in rows if _as_bool(row.get("ok"))]
    rows_for_values = ok_rows or rows

    field_order = list(manifest.get("field_order") or _infer_input_fields(rows_for_values))
    bounds = _as_dict(manifest.get("bounds"))
    input_data = _collect_input_data(rows_for_values, field_order, bounds)
    normalized_data = _normalize_input_data(input_data, bounds)
    numeric_columns = _collect_numeric_columns(rows_for_values)
    figures: list[dict[str, str]] = []
    data_status = _data_status(manifest, rows, args)

    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#333333",
            "axes.labelcolor": "#222222",
            "xtick.color": "#222222",
            "ytick.color": "#222222",
            "font.size": 9,
        }
    )

    summary: dict[str, Any] = {
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "requested_count": manifest.get("requested_count"),
        "ok_count": manifest.get("ok_count"),
        "fail_count": manifest.get("fail_count"),
        "row_count": len(rows),
        "plotted_row_count": len(rows_for_values),
        "field_order": field_order,
        "data_status": data_status,
        "figures": [],
        "uniformity_evidence": _uniformity_evidence(manifest, normalized_data, args.bins),
        "normality_evidence": _normality_evidence(normalized_data),
        "coverage": _coverage_summary(manifest, numeric_columns),
        "command": " ".join(sys.argv),
    }
    global FIGURE_FOOTER
    FIGURE_FOOTER = _figure_footer(dataset_dir, summary)

    _add(figures, output_dir, "01_input_marginal_histograms.png", "Input marginal histograms", _plot_input_marginals(input_data, bounds, output_dir / "01_input_marginal_histograms.png", args.bins))
    _add(figures, output_dir, "02_input_lhs_bin_balance.png", "LHS bin balance heatmap", _plot_lhs_bin_balance(normalized_data, output_dir / "02_input_lhs_bin_balance.png", args.bins))
    _add(figures, output_dir, "03_input_uniform_quantiles.png", "Uniform quantile plot", _plot_uniform_quantiles(normalized_data, output_dir / "03_input_uniform_quantiles.png"))
    _add(figures, output_dir, "04_input_pairwise_scatter_matrix.png", "Normalized input scatter matrix", _plot_pairwise_matrix(normalized_data, output_dir / "04_input_pairwise_scatter_matrix.png", args.max_scatter_dims))
    _add(figures, output_dir, "05_input_correlation_heatmap.png", "Input correlation heatmap", _plot_correlation_heatmap(normalized_data, output_dir / "05_input_correlation_heatmap.png", "Input Correlation Heatmap"))
    _add(figures, output_dir, "06_input_nearest_neighbor_distances.png", "Space-filling nearest-neighbor distances", _plot_nearest_neighbor(normalized_data, output_dir / "06_input_nearest_neighbor_distances.png"))
    _add(figures, output_dir, "07_geometry_angle_summary.png", "Manufacturing angle summary", _plot_geometry_angles(numeric_columns, manifest, output_dir / "07_geometry_angle_summary.png"))
    _add(figures, output_dir, "08_zin_center_coverage.png", "Center-frequency Zin coverage", _plot_zin_center(numeric_columns, manifest, output_dir / "08_zin_center_coverage.png"))
    _add(figures, output_dir, "09_zin_range_summary.png", "Zin range summary", _plot_zin_ranges(manifest, output_dir / "09_zin_range_summary.png"))
    _add(figures, output_dir, "10_em_metric_distributions.png", "EM label metric distributions", _plot_metric_distributions(numeric_columns, manifest, output_dir / "10_em_metric_distributions.png"))
    _add(figures, output_dir, "11_sparameter_quality.png", "S-parameter physical quality", _plot_sparameter_quality(numeric_columns, manifest, output_dir / "11_sparameter_quality.png"))
    _add(figures, output_dir, "12_input_output_correlation.png", "Input-output correlation overview", _plot_input_output_correlation(normalized_data, numeric_columns, output_dir / "12_input_output_correlation.png"))
    _add(figures, output_dir, "13_dataset_dashboard.png", "Dataset quality dashboard", _plot_dashboard(manifest, summary, output_dir / "13_dataset_dashboard.png"))

    summary["figures"] = figures
    (output_dir / "visualization_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "visual_report_index.md").write_text(_render_index(summary), encoding="utf-8")

    print(f"visualizations={output_dir}")
    print(f"status={data_status['status_label']}")
    print(f"figures={len(figures)}")
    print(f"index={output_dir / 'visual_report_index.md'}")
    if args.require_report_ready and not data_status["report_ready"]:
        print("not_report_ready_reasons=" + "; ".join(data_status["reasons"]), file=sys.stderr)
        return 3
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Directory with dataset_manifest.json and dataset_rows.csv")
    parser.add_argument("--out-dir", default=None, help="Output directory for figures and reports")
    parser.add_argument("--bins", type=int, default=10, help="Histogram/bin count for uniformity figures")
    parser.add_argument("--max-scatter-dims", type=int, default=6, help="Maximum input dimensions in scatter matrix")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=None)
    parser.add_argument("--expected-frequency-points", type=int, default=None)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0)
    parser.add_argument(
        "--require-report-ready",
        action="store_true",
        help="Exit nonzero unless the dataset has complete rows, EM/Zin labels for all ok samples, and any requested frequency grid.",
    )
    return parser.parse_args(argv)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing CSV: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _infer_input_fields(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    fields = []
    for key in rows[0].keys():
        if key.startswith("geom__") and _as_float(rows[0].get(key)) is not None:
            fields.append(key.removeprefix("geom__"))
    return fields


def _collect_input_data(rows: list[dict[str, str]], fields: list[str], bounds: dict[str, Any]) -> dict[str, np.ndarray]:
    data = {}
    for field in fields:
        column = f"geom__{field}"
        values = [_as_float(row.get(column)) for row in rows]
        arr = np.array([value for value in values if value is not None], dtype=float)
        if arr.size and field in bounds:
            data[field] = arr
    return data


def _normalize_input_data(data: dict[str, np.ndarray], bounds: dict[str, Any]) -> dict[str, np.ndarray]:
    normalized = {}
    for field, arr in data.items():
        lo_hi = bounds.get(field)
        if not isinstance(lo_hi, list | tuple) or len(lo_hi) != 2:
            continue
        lo = _as_float(lo_hi[0])
        hi = _as_float(lo_hi[1])
        if lo is None or hi is None or hi <= lo:
            continue
        normalized[field] = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
    return normalized


def _collect_numeric_columns(rows: list[dict[str, str]]) -> dict[str, np.ndarray]:
    if not rows:
        return {}
    columns: dict[str, list[float]] = {key: [] for key in rows[0].keys()}
    for row in rows:
        for key, value in row.items():
            item = _as_float(value)
            if item is not None:
                columns[key].append(item)
    return {key: np.array(values, dtype=float) for key, values in columns.items() if len(values) >= max(3, int(len(rows) * 0.2))}


def _plot_input_marginals(data: dict[str, np.ndarray], bounds: dict[str, Any], path: Path, bins: int) -> bool:
    if not data:
        return False
    fields = list(data)
    ncols = 3
    nrows = math.ceil(len(fields) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(13, max(3, 2.7 * nrows)), squeeze=False)
    for ax, field in zip(axes.flat, fields):
        arr = data[field]
        lo, hi = [float(x) for x in bounds[field]]
        ax.hist(arr, bins=bins, range=(lo, hi), density=True, color="#3B82F6", alpha=0.70, edgecolor="white", label="data")
        ax.hlines(1.0 / (hi - lo), lo, hi, color="#111827", linewidth=1.6, label="uniform target")
        if arr.size > 1 and np.std(arr) > 0:
            x = np.linspace(lo, hi, 200)
            mu = float(np.mean(arr))
            sigma = float(np.std(arr, ddof=1))
            y = np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2.0 * math.pi))
            ax.plot(x, y, color="#DC2626", linewidth=1.2, linestyle="--", label="normal fit")
        ax.set_title(_label(field))
        ax.set_xlabel("value")
        ax.set_ylabel("density")
    for ax in axes.flat[len(fields) :]:
        ax.axis("off")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.955), ncol=3, frameon=False)
    fig.suptitle("Input Parameter Marginals: Uniform Target vs Normal Fit", y=0.99, fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    _save_figure(fig, path)
    return True


def _plot_lhs_bin_balance(normalized: dict[str, np.ndarray], path: Path, bins: int) -> bool:
    if not normalized:
        return False
    fields = list(normalized)
    counts = np.vstack([np.histogram(normalized[field], bins=bins, range=(0.0, 1.0))[0] for field in fields])
    fig, ax = plt.subplots(figsize=(12, max(4, 0.42 * len(fields) + 1.5)))
    im = ax.imshow(counts, aspect="auto", cmap="YlGnBu")
    ax.set_xticks(np.arange(bins))
    ax.set_xticklabels([str(i + 1) for i in range(bins)])
    ax.set_yticks(np.arange(len(fields)))
    ax.set_yticklabels([_label(field) for field in fields])
    ax.set_xlabel("Normalized LHS bin")
    ax.set_title("Per-Parameter LHS Bin Balance")
    for i in range(counts.shape[0]):
        for j in range(counts.shape[1]):
            ax.text(j, i, str(int(counts[i, j])), ha="center", va="center", fontsize=7, color="#111827")
    fig.colorbar(im, ax=ax, label="count")
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_uniform_quantiles(normalized: dict[str, np.ndarray], path: Path) -> bool:
    if not normalized:
        return False
    fig, ax = plt.subplots(figsize=(7.5, 7))
    for field, arr in normalized.items():
        values = np.sort(arr)
        expected = (np.arange(1, len(values) + 1) - 0.5) / len(values)
        ax.plot(expected, values, linewidth=1.0, alpha=0.75, label=_label(field))
    ax.plot([0, 1], [0, 1], color="#111827", linewidth=2.0, label="ideal uniform")
    ax.set_xlabel("Expected uniform quantile")
    ax.set_ylabel("Observed normalized quantile")
    ax.set_title("Uniform Quantile Evidence")
    ax.grid(True, alpha=0.25)
    if len(normalized) <= 12:
        ax.legend(fontsize=7, loc="best")
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_pairwise_matrix(normalized: dict[str, np.ndarray], path: Path, max_dims: int) -> bool:
    if len(normalized) < 2:
        return False
    fields = list(normalized)[: max(2, max_dims)]
    n = len(fields)
    fig, axes = plt.subplots(n, n, figsize=(2.1 * n, 2.1 * n), squeeze=False)
    for i, yfield in enumerate(fields):
        for j, xfield in enumerate(fields):
            ax = axes[i, j]
            if i == j:
                ax.hist(normalized[xfield], bins=10, range=(0, 1), color="#60A5FA", edgecolor="white")
            else:
                ax.scatter(normalized[xfield], normalized[yfield], s=10, alpha=0.55, color="#2563EB", linewidths=0)
            if i == n - 1:
                ax.set_xlabel(_short_label(xfield), fontsize=7)
            else:
                ax.set_xticklabels([])
            if j == 0:
                ax.set_ylabel(_short_label(yfield), fontsize=7)
            else:
                ax.set_yticklabels([])
            ax.set_xlim(0, 1)
            if i != j:
                ax.set_ylim(0, 1)
    fig.suptitle("Normalized Input Space Coverage", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    _save_figure(fig, path)
    return True


def _plot_correlation_heatmap(data: dict[str, np.ndarray], path: Path, title: str) -> bool:
    matrix, fields = _aligned_matrix(data)
    if matrix is None or matrix.shape[1] < 2:
        return False
    corr = np.corrcoef(matrix, rowvar=False)
    fig, ax = plt.subplots(figsize=(max(7, 0.55 * len(fields)), max(6, 0.55 * len(fields))))
    im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
    ax.set_xticks(np.arange(len(fields)))
    ax.set_yticks(np.arange(len(fields)))
    ax.set_xticklabels([_short_label(field) for field in fields], rotation=45, ha="right")
    ax.set_yticklabels([_short_label(field) for field in fields])
    for i in range(len(fields)):
        for j in range(len(fields)):
            ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center", fontsize=6)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="Pearson r")
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_nearest_neighbor(normalized: dict[str, np.ndarray], path: Path) -> bool:
    matrix, _fields = _aligned_matrix(normalized)
    if matrix is None or matrix.shape[0] < 3:
        return False
    distances = _nearest_neighbor_distances(matrix)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(distances, bins=min(30, max(8, int(math.sqrt(len(distances))))), color="#10B981", edgecolor="white", alpha=0.8)
    ax.axvline(np.mean(distances), color="#111827", linewidth=1.5, label=f"mean={np.mean(distances):.3g}")
    ax.axvline(np.min(distances), color="#DC2626", linewidth=1.2, linestyle="--", label=f"min={np.min(distances):.3g}")
    ax.set_xlabel("Nearest-neighbor distance in normalized input space")
    ax.set_ylabel("count")
    ax.set_title("Space-Filling Evidence")
    ax.legend(frameon=False)
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_geometry_angles(numeric: dict[str, np.ndarray], manifest: dict[str, Any], path: Path) -> bool:
    angle_keys = [key for key in numeric if "angle_deg" in key]
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = []
    values = []
    for key in angle_keys:
        arr = numeric[key]
        if arr.size:
            labels.append(_short_label(key.replace("geometry_check__", "")))
            values.append(arr)
    if values:
        ax.boxplot(values, vert=True, showfliers=False)
        ax.set_xticks(np.arange(1, len(labels) + 1))
        ax.set_xticklabels(labels)
        ax.tick_params(axis="x", rotation=35)
    else:
        geom = _as_dict(manifest.get("geometry_quality"))
        labels, mins, maxs = [], [], []
        for key in (
            "primary_internal_angle_deg",
            "secondary_internal_angle_deg",
            "primary_terminal_interface_angle_deg",
            "secondary_terminal_interface_angle_deg",
        ):
            summary = _as_dict(geom.get(key))
            lo = _summary_nested(summary, "min", "min")
            hi = _summary_nested(summary, "max", "max")
            if lo is not None and hi is not None:
                labels.append(_short_label(key))
                mins.append(lo)
                maxs.append(hi)
        if not labels:
            plt.close(fig)
            return False
        x = np.arange(len(labels))
        ax.bar(x, np.array(maxs) - np.array(mins), bottom=mins, color="#8B5CF6", alpha=0.75)
        ax.scatter(x, mins, color="#111827", s=18, label="min")
        ax.scatter(x, maxs, color="#DC2626", s=18, label="max")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.axhline(135, color="#059669", linestyle="--", linewidth=1.2, label="135 deg target")
    ax.axhline(90, color="#F59E0B", linestyle=":", linewidth=1.2, label="90 deg feed")
    ax.set_ylabel("angle (deg)")
    ax.set_title("Manufacturing Angle Verification")
    ax.legend(frameon=False)
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_zin_center(numeric: dict[str, np.ndarray], manifest: dict[str, Any], path: Path) -> bool:
    real_key = _find_column(numeric, include=("zin", "real"), exclude=("band", "min", "max"))
    imag_key = _find_column(numeric, include=("zin", "imag"), exclude=("band", "min", "max"))
    abs_key = _find_column(numeric, include=("zin", "abs"), exclude=("band", "min", "max"))
    if real_key and imag_key and len(numeric[real_key]) == len(numeric[imag_key]):
        real = numeric[real_key]
        imag = numeric[imag_key]
        color = numeric[abs_key] if abs_key and len(numeric[abs_key]) == len(real) else np.sqrt(real**2 + imag**2)
        fig, ax = plt.subplots(figsize=(7.5, 6))
        sc = ax.scatter(real, imag, c=color, cmap="viridis", s=22, alpha=0.78, linewidths=0)
        ax.set_xlabel("Re(Zin) ohm")
        ax.set_ylabel("Im(Zin) ohm")
        ax.set_title("Center-Frequency Zin Coverage")
        ax.grid(True, alpha=0.25)
        fig.colorbar(sc, ax=ax, label="|Zin| ohm")
        fig.tight_layout()
        _save_figure(fig, path)
        return True
    zin = _as_dict(manifest.get("zin_coverage"))
    real_summary = _as_dict(zin.get("real_ohm"))
    imag_summary = _as_dict(zin.get("imag_ohm"))
    if _as_float(real_summary.get("min")) is None or _as_float(imag_summary.get("min")) is None:
        return False
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.errorbar(
        [_as_float(real_summary.get("mean"))],
        [_as_float(imag_summary.get("mean"))],
        xerr=[[_as_float(real_summary.get("mean")) - _as_float(real_summary.get("min"))], [_as_float(real_summary.get("max")) - _as_float(real_summary.get("mean"))]],
        yerr=[[_as_float(imag_summary.get("mean")) - _as_float(imag_summary.get("min"))], [_as_float(imag_summary.get("max")) - _as_float(imag_summary.get("mean"))]],
        fmt="o",
        color="#2563EB",
        ecolor="#60A5FA",
        capsize=4,
    )
    ax.set_xlabel("Re(Zin) ohm")
    ax.set_ylabel("Im(Zin) ohm")
    ax.set_title("Center-Frequency Zin Range From Manifest")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_zin_ranges(manifest: dict[str, Any], path: Path) -> bool:
    zin = _as_dict(manifest.get("zin_coverage"))
    items = []
    for key, label in (
        ("real_ohm", "center Re"),
        ("imag_ohm", "center Im"),
        ("abs_ohm", "center |Z|"),
        ("band_real_max_ohm", "band Re max"),
        ("band_imag_max_ohm", "band Im max"),
        ("band_abs_max_ohm", "band |Z| max"),
    ):
        summary = _as_dict(zin.get(key))
        lo = _as_float(summary.get("min"))
        hi = _as_float(summary.get("max"))
        mean = _as_float(summary.get("mean"))
        if lo is not None and hi is not None:
            items.append((label, lo, hi, mean if mean is not None else (lo + hi) / 2.0))
    if not items:
        return False
    fig, ax = plt.subplots(figsize=(9, max(4, 0.55 * len(items))))
    y = np.arange(len(items))
    lows = np.array([item[1] for item in items])
    highs = np.array([item[2] for item in items])
    means = np.array([item[3] for item in items])
    ax.barh(y, highs - lows, left=lows, color="#38BDF8", alpha=0.75)
    ax.scatter(means, y, color="#111827", s=24, label="mean")
    ax.set_yticks(y)
    ax.set_yticklabels([item[0] for item in items])
    ax.set_xlabel("ohm")
    ax.set_title("Zin Coverage Ranges")
    ax.legend(frameon=False)
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_metric_distributions(numeric: dict[str, np.ndarray], manifest: dict[str, Any], path: Path) -> bool:
    metrics = _metric_columns(numeric)
    if not metrics:
        return False
    ncols = min(3, len(metrics))
    nrows = math.ceil(len(metrics) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 2.8 * nrows), squeeze=False)
    for ax, (name, arr) in zip(axes.flat, metrics.items()):
        values = arr[np.isfinite(arr)]
        if name in {"lp_h", "ls_h"}:
            values = values * 1e9
            xlabel = f"{name} (nH)"
        else:
            xlabel = name
        ax.hist(values, bins=min(30, max(8, int(math.sqrt(len(values))))), color="#F97316", alpha=0.78, edgecolor="white")
        ax.axvline(np.mean(values), color="#111827", linewidth=1.3, label=f"mean={np.mean(values):.3g}")
        ax.set_title(_short_label(name))
        ax.set_xlabel(xlabel)
        ax.set_ylabel("count")
        ax.legend(frameon=False, fontsize=7)
    for ax in axes.flat[len(metrics) :]:
        ax.axis("off")
    fig.suptitle("EM Label Distributions", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    _save_figure(fig, path)
    return True


def _plot_sparameter_quality(numeric: dict[str, np.ndarray], manifest: dict[str, Any], path: Path) -> bool:
    spq = _as_dict(manifest.get("sparameter_quality"))
    items = []
    for key, label in (
        ("reciprocity_error_abs", "reciprocity error"),
        ("passivity_sigma_max", "passivity sigma max"),
        ("passivity_excess", "passivity excess"),
        ("sdd11_center_db", "SDD11 center dB"),
        ("sdd21_center_db", "SDD21 center dB"),
    ):
        summary = _as_dict(spq.get(key))
        value = _as_float(summary.get("max"))
        if value is not None:
            items.append((label, value))
    row_keys = [
        key
        for key in numeric
        if any(token in key.lower() for token in ("passivity", "reciprocity", "sdd11", "sdd21"))
        and numeric[key].size >= 3
    ][:6]
    if not items and not row_keys:
        return False
    if row_keys:
        ncols = min(3, len(row_keys))
        nrows = math.ceil(len(row_keys) / ncols)
        fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 2.8 * nrows), squeeze=False)
        for ax, key in zip(axes.flat, row_keys):
            ax.hist(numeric[key], bins=min(30, max(8, int(math.sqrt(len(numeric[key]))))), color="#14B8A6", alpha=0.78, edgecolor="white")
            ax.set_title(_short_label(key))
        for ax in axes.flat[len(row_keys) :]:
            ax.axis("off")
        fig.suptitle("S-Parameter Quality Distributions", fontsize=14)
    else:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        x = np.arange(len(items))
        ax.bar(x, [item[1] for item in items], color="#14B8A6", alpha=0.78)
        ax.set_xticks(x)
        ax.set_xticklabels([item[0] for item in items], rotation=30, ha="right")
        ax.set_title("S-Parameter Quality From Manifest")
        ax.set_ylabel("max value")
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_input_output_correlation(normalized: dict[str, np.ndarray], numeric: dict[str, np.ndarray], path: Path) -> bool:
    outputs = _metric_columns(numeric)
    real_key = _find_column(numeric, include=("zin", "real"), exclude=("band", "min", "max"))
    imag_key = _find_column(numeric, include=("zin", "imag"), exclude=("band", "min", "max"))
    if real_key:
        outputs["zin_real"] = numeric[real_key]
    if imag_key:
        outputs["zin_imag"] = numeric[imag_key]
    if not normalized or not outputs:
        return False
    in_matrix, in_fields = _aligned_matrix(normalized)
    out_matrix, out_fields = _aligned_matrix(outputs)
    if in_matrix is None or out_matrix is None:
        return False
    n = min(in_matrix.shape[0], out_matrix.shape[0])
    if n < 3:
        return False
    in_matrix = in_matrix[:n]
    out_matrix = out_matrix[:n]
    corr = np.zeros((len(in_fields), len(out_fields)))
    for i in range(len(in_fields)):
        for j in range(len(out_fields)):
            corr[i, j] = _safe_corr(in_matrix[:, i], out_matrix[:, j])
    fig, ax = plt.subplots(figsize=(max(7, 0.8 * len(out_fields) + 4), max(5, 0.42 * len(in_fields) + 1.5)))
    im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(np.arange(len(out_fields)))
    ax.set_yticks(np.arange(len(in_fields)))
    ax.set_xticklabels([_short_label(field) for field in out_fields], rotation=30, ha="right")
    ax.set_yticklabels([_short_label(field) for field in in_fields])
    for i in range(corr.shape[0]):
        for j in range(corr.shape[1]):
            ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center", fontsize=6)
    ax.set_title("Input-to-Output Correlation Overview")
    fig.colorbar(im, ax=ax, label="Pearson r")
    fig.tight_layout()
    _save_figure(fig, path)
    return True


def _plot_dashboard(manifest: dict[str, Any], summary: dict[str, Any], path: Path) -> bool:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    requested = _as_float(manifest.get("requested_count")) or 0
    ok = _as_float(manifest.get("ok_count")) or 0
    fail = _as_float(manifest.get("fail_count")) or 0
    axes[0, 0].bar(["ok", "fail"], [ok, fail], color=["#22C55E", "#EF4444"], alpha=0.82)
    axes[0, 0].set_title(f"Run Counts (requested={int(requested)})")
    axes[0, 0].set_ylabel("count")

    uniform = summary.get("uniformity_evidence", {})
    fields = uniform.get("field_bin_imbalance", {})
    if fields:
        names = list(fields)[:12]
        values = [fields[name]["max_abs_imbalance_frac"] for name in names]
        axes[0, 1].barh(np.arange(len(names)), values, color="#3B82F6", alpha=0.78)
        axes[0, 1].set_yticks(np.arange(len(names)))
        axes[0, 1].set_yticklabels([_short_label(name) for name in names], fontsize=7)
        axes[0, 1].set_title("Uniform Bin Imbalance")
        axes[0, 1].set_xlabel("fraction of expected bin count")
    else:
        axes[0, 1].text(0.5, 0.5, "Uniformity data unavailable", ha="center", va="center")
        axes[0, 1].axis("off")

    zin = _as_dict(manifest.get("zin_coverage"))
    zin_items = []
    for key, label in (("real_ohm", "Re"), ("imag_ohm", "Im"), ("abs_ohm", "|Z|")):
        item = _as_dict(zin.get(key))
        lo = _as_float(item.get("min"))
        hi = _as_float(item.get("max"))
        if lo is not None and hi is not None:
            zin_items.append((label, hi - lo))
    if zin_items:
        axes[1, 0].bar([x[0] for x in zin_items], [x[1] for x in zin_items], color="#F97316", alpha=0.78)
        axes[1, 0].set_title("Center Zin Span")
        axes[1, 0].set_ylabel("ohm")
    else:
        axes[1, 0].text(0.5, 0.5, "Zin labels pending", ha="center", va="center")
        axes[1, 0].axis("off")

    spq = _as_dict(manifest.get("sparameter_quality"))
    valid_s = _as_float(spq.get("valid_sparameter_count")) or 0
    valid_z = _as_float(zin.get("valid_zin_count")) or 0
    axes[1, 1].bar(["S-param", "Zin"], [valid_s, valid_z], color=["#8B5CF6", "#06B6D4"], alpha=0.78)
    axes[1, 1].set_title("Valid EM Label Counts")
    axes[1, 1].set_ylabel("count")

    fig.suptitle("Dataset Quality Dashboard", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    _save_figure(fig, path)
    return True


def _uniformity_evidence(manifest: dict[str, Any], normalized: dict[str, np.ndarray], bins: int) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "sampler": manifest.get("sampler"),
        "manifest_space_filling": _as_dict(manifest.get("uniformity")).get("space_filling"),
        "field_bin_imbalance": {},
    }
    for field, arr in normalized.items():
        counts, _ = np.histogram(arr, bins=bins, range=(0, 1))
        expected = len(arr) / bins if bins else 0.0
        if expected > 0:
            imbalance = float(np.max(np.abs(counts - expected)) / expected)
        else:
            imbalance = None
        chi2_p = None
        if scipy_stats is not None and expected > 0:
            _stat, pvalue = scipy_stats.chisquare(counts, np.full_like(counts, expected, dtype=float))
            chi2_p = float(pvalue)
        evidence["field_bin_imbalance"][field] = {
            "counts": counts.astype(int).tolist(),
            "expected_per_bin": expected,
            "max_abs_imbalance_frac": imbalance,
            "uniform_chi_square_pvalue": chi2_p,
        }
    return evidence


def _normality_evidence(normalized: dict[str, np.ndarray]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for field, arr in normalized.items():
        values = arr[np.isfinite(arr)]
        item = {
            "n": int(values.size),
            "mean": float(np.mean(values)) if values.size else None,
            "std": float(np.std(values, ddof=1)) if values.size > 1 else None,
            "normaltest_pvalue": None,
            "ks_against_fitted_normal_pvalue": None,
        }
        if scipy_stats is not None and values.size >= 8 and np.std(values) > 0:
            try:
                _stat, pvalue = scipy_stats.normaltest(values)
                item["normaltest_pvalue"] = float(pvalue)
            except Exception:
                pass
            try:
                mu = float(np.mean(values))
                sigma = float(np.std(values, ddof=1))
                _stat, pvalue = scipy_stats.kstest(values, "norm", args=(mu, sigma))
                item["ks_against_fitted_normal_pvalue"] = float(pvalue)
            except Exception:
                pass
        evidence[field] = item
    return evidence


def _coverage_summary(manifest: dict[str, Any], numeric: dict[str, np.ndarray]) -> dict[str, Any]:
    return {
        "zin_coverage": manifest.get("zin_coverage"),
        "sparameter_quality": manifest.get("sparameter_quality"),
        "geometry_quality": manifest.get("geometry_quality"),
        "available_numeric_label_columns": [
            key
            for key in sorted(numeric)
            if key.startswith(("zin_", "zdd", "sparam_", "sdd", "metrics__", "objective__"))
        ],
    }


def _data_status(manifest: dict[str, Any], rows: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    reasons: list[str] = []
    requested = _as_int(manifest.get("requested_count"))
    ok_count = _as_int(manifest.get("ok_count"))
    fail_count = _as_int(manifest.get("fail_count"))
    row_count = len(rows)
    if requested is None:
        reasons.append("manifest missing requested_count")
    if ok_count is None:
        reasons.append("manifest missing ok_count")
    if fail_count is None:
        reasons.append("manifest missing fail_count")
    if requested is not None and row_count != requested:
        reasons.append(f"csv row_count {row_count} != requested_count {requested}")
    if requested is not None and ok_count is not None and fail_count is not None and ok_count + fail_count != requested:
        reasons.append(f"ok_count + fail_count {ok_count + fail_count} != requested_count {requested}")
    if fail_count not in (0, None):
        reasons.append(f"fail_count is {fail_count}")

    spq = _as_dict(manifest.get("sparameter_quality"))
    zin = _as_dict(manifest.get("zin_coverage"))
    valid_s = _as_int(spq.get("valid_sparameter_count"))
    valid_z = _as_int(zin.get("valid_zin_count"))
    if ok_count is not None:
        if valid_s != ok_count:
            reasons.append(f"valid_sparameter_count {valid_s} != ok_count {ok_count}")
        if valid_z != ok_count:
            reasons.append(f"valid_zin_count {valid_z} != ok_count {ok_count}")
    expected_frequency = _expected_frequency_from_args(args)
    if expected_frequency:
        target_frequency = _as_dict(manifest.get("target_frequency"))
        if not target_frequency:
            reasons.append("manifest missing target_frequency")
        else:
            mismatch = _frequency_mismatch_detail(target_frequency, expected_frequency, args.frequency_tolerance_hz)
            if mismatch:
                reasons.append(f"target_frequency {mismatch}")
    report_ready = not reasons
    return {
        "report_ready": report_ready,
        "status_label": "REPORT_READY" if report_ready else "PRELIMINARY",
        "reasons": reasons,
        "requested_count": requested,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "row_count": row_count,
        "valid_sparameter_count": valid_s,
        "valid_zin_count": valid_z,
        "expected_frequency": expected_frequency,
        "target_frequency": manifest.get("target_frequency"),
    }


def _expected_frequency_from_args(args: argparse.Namespace) -> dict[str, float | int] | None:
    values = (
        args.expected_frequency_start_ghz,
        args.expected_frequency_stop_ghz,
        args.expected_frequency_step_ghz,
        args.expected_frequency_points,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        return {
            "error": "expected frequency requires start, stop, step, and points",
        }
    return {
        "start_hz": float(args.expected_frequency_start_ghz) * 1.0e9,
        "stop_hz": float(args.expected_frequency_stop_ghz) * 1.0e9,
        "step_hz": float(args.expected_frequency_step_ghz) * 1.0e9,
        "points": int(args.expected_frequency_points),
    }


def _frequency_mismatch_detail(
    actual: dict[str, Any],
    expected: dict[str, float | int],
    tolerance_hz: float,
) -> str | None:
    if "error" in expected:
        return str(expected["error"])
    for name in ("start_hz", "stop_hz", "step_hz"):
        actual_value = _as_float(actual.get(name))
        expected_value = _as_float(expected.get(name))
        if actual_value is None:
            return f"missing {name}"
        if expected_value is not None and abs(actual_value - expected_value) > tolerance_hz:
            return f"{name} mismatch: actual={actual_value}, expected={expected_value}"
    actual_points = _as_int(actual.get("points"))
    expected_points = _as_int(expected.get("points"))
    if actual_points is None:
        return "missing points"
    if expected_points is not None and actual_points != expected_points:
        return f"points mismatch: actual={actual_points}, expected={expected_points}"
    return None


def _figure_footer(dataset_dir: Path, summary: dict[str, Any]) -> str:
    status = summary["data_status"]["status_label"]
    requested = summary.get("requested_count")
    ok_count = summary.get("ok_count")
    row_count = summary.get("row_count")
    return f"{status} | source_id={dataset_dir.name} | rows={row_count} | requested={requested} | ok={ok_count} | see visualization_summary.json"


def _save_figure(fig: Any, path: Path) -> None:
    if FIGURE_FOOTER:
        color = "#B91C1C" if FIGURE_FOOTER.startswith("PRELIMINARY") else "#166534"
        fig.text(0.01, 0.006, FIGURE_FOOTER, ha="left", va="bottom", fontsize=6.5, color=color)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _metric_columns(numeric: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    found: dict[str, np.ndarray] = {}
    lower_to_key = {key.lower(): key for key in numeric}
    for name, aliases in METRIC_ALIASES.items():
        for alias in aliases:
            key = lower_to_key.get(alias.lower())
            if key and numeric[key].size >= 3:
                found[name] = numeric[key]
                break
    if found:
        return found
    fallback = [key for key in numeric if key.startswith("metrics__")][:8]
    return {key.removeprefix("metrics__"): numeric[key] for key in fallback}


def _find_column(numeric: dict[str, np.ndarray], *, include: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
    candidates = []
    for key in numeric:
        low = key.lower()
        if all(token in low for token in include) and not any(token in low for token in exclude):
            candidates.append(key)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (len(item), item))
    return candidates[0]


def _aligned_matrix(data: dict[str, np.ndarray]) -> tuple[np.ndarray | None, list[str]]:
    fields = [field for field, arr in data.items() if arr.size >= 3]
    if len(fields) < 1:
        return None, []
    n = min(data[field].size for field in fields)
    matrix = np.column_stack([data[field][:n] for field in fields])
    finite = np.all(np.isfinite(matrix), axis=1)
    matrix = matrix[finite]
    if matrix.shape[0] < 3:
        return None, []
    return matrix, fields


def _nearest_neighbor_distances(matrix: np.ndarray) -> np.ndarray:
    diff = matrix[:, None, :] - matrix[None, :, :]
    distances = np.sqrt(np.sum(diff * diff, axis=2))
    np.fill_diagonal(distances, np.inf)
    return np.min(distances, axis=1)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 3 or b.size < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _add(figures: list[dict[str, str]], output_dir: Path, filename: str, title: str, created: bool) -> None:
    if created:
        figures.append({"title": title, "file": filename, "path": str(output_dir / filename)})


def _render_index(summary: dict[str, Any]) -> str:
    lines = [
        "# Dataset Visual Quality Report",
        "",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Output: `{summary['output_dir']}`",
        f"- Requested count: `{summary.get('requested_count')}`",
        f"- OK count: `{summary.get('ok_count')}`",
        f"- Failed count: `{summary.get('fail_count')}`",
        f"- Plotted rows: `{summary.get('plotted_row_count')}`",
        f"- Status: **{summary['data_status']['status_label']}**",
        "",
    ]
    if not summary["data_status"]["report_ready"]:
        lines.extend(
            [
                "## Status Notes",
                "",
                "This visualization set is not report-ready. Reasons:",
                "",
            ]
        )
        for reason in summary["data_status"]["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")
    proof_heading = "What Report-Ready Figures Prove" if summary["data_status"]["report_ready"] else "What These Preliminary Figures Check"
    proof_item_4 = (
        "Zin and EM metric figures show the labels cover a broad enough target space for inverse training."
        if summary["data_status"]["report_ready"]
        else "Zin and EM metric figures are generated only when real EM labels exist; missing labels keep the report preliminary."
    )
    lines.extend(
        [
            f"## {proof_heading}",
            "",
            "1. Input parameters are distributed over the full allowed ranges.",
            "2. LHS bins are balanced, which supports a uniform rather than normal sampling claim.",
            "3. Pairwise scatter and correlation plots show the design space is space-filling instead of clustered.",
            f"4. {proof_item_4}",
            "5. S-parameter and angle figures document physical sanity and manufacturability.",
            "",
            "## Figures",
            "",
        ]
    )
    for fig in summary["figures"]:
        lines.append(f"### {fig['title']}")
        lines.append("")
        lines.append(f"![{fig['title']}]({fig['file']})")
        lines.append("")
    lines.extend(
        [
            "## Evidence Tables",
            "",
            "### Uniformity Evidence",
            "```json",
            json.dumps(summary.get("uniformity_evidence"), indent=2),
            "```",
            "",
            "### Normality Evidence",
            "Small p-values in the normality tests support rejecting a fitted normal distribution for the normalized input variables.",
            "",
            "```json",
            json.dumps(summary.get("normality_evidence"), indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _label(field: str) -> str:
    return INPUT_FIELD_LABELS.get(field, _short_label(field))


def _short_label(text: str) -> str:
    text = text.removeprefix("geom__").removeprefix("metrics__").removeprefix("geometry_check__")
    replacements = {
        "primary": "pri",
        "secondary": "sec",
        "outer": "out",
        "width": "W",
        "height": "H",
        "terminal": "term",
        "extension": "ext",
        "_um": "",
        "_ohm": "",
        "_deg": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.replace("_", " ")


def _summary_nested(container: dict[str, Any], section: str, metric: str) -> float | None:
    return _as_float(_as_dict(container.get(section)).get(metric))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        item = float(value)
    except (TypeError, ValueError):
        return None
    return item if math.isfinite(item) else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


if __name__ == "__main__":
    sys.exit(main())
