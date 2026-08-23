#!/usr/bin/env python3
"""Fit existing HFSS substrate-conductivity branches to estimate Q/K limits."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

METRICS = ("k", "qp", "qs", "lp_nh", "ls_nh")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    branches = load_branches(args.branch)
    grid = np.linspace(args.grid_min, args.grid_max, args.grid_count)

    summary = build_summary(branches, grid, max_percent_error=args.max_percent_error)
    curve_csv = out_dir / "substrate_conductivity_fit_curves.csv"
    summary_json = out_dir / "substrate_conductivity_fit_summary.json"
    report_md = out_dir / "substrate_conductivity_fit_report.md"
    write_curve_csv(curve_csv, summary)
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_md.write_text(render_report(summary), encoding="utf-8")
    plots = maybe_plot(out_dir, summary)
    manifest = write_manifest(out_dir, [Path(item["path"]) for item in branches], [curve_csv, summary_json, report_md, *plots])

    print(f"summary={summary_json}")
    print(f"curves_csv={curve_csv}")
    print(f"report={report_md}")
    for plot in plots:
        print(f"plot={plot}")
    print(f"manifest={manifest}")
    print(f"best_qp_sigma={summary['metric_fits']['qp']['best_sigma_s_per_m']:.6g} maxerr={summary['metric_fits']['qp']['best_max_percent_error']:.6g}%")
    print(f"best_qs_sigma={summary['metric_fits']['qs']['best_sigma_s_per_m']:.6g} maxerr={summary['metric_fits']['qs']['best_max_percent_error']:.6g}%")
    print(f"best_combined_q_sigma={summary['combined_fits']['qp_qs']['best_sigma_s_per_m']:.6g} maxerr={summary['combined_fits']['qp_qs']['best_max_percent_error']:.6g}%")
    print(f"best_all_sigma={summary['combined_fits']['all_gate_metrics']['best_sigma_s_per_m']:.6g} maxerr={summary['combined_fits']['all_gate_metrics']['best_max_percent_error']:.6g}%")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", action="append", required=True, help="Branch spec: sigma:path/to/comparison_summary.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--grid-min", type=float, default=0.0)
    parser.add_argument("--grid-max", type=float, default=10.0)
    parser.add_argument("--grid-count", type=int, default=1001)
    parser.add_argument("--max-percent-error", type=float, default=5.0)
    return parser.parse_args(argv)


def load_branches(specs: list[str]) -> list[dict[str, Any]]:
    branches = []
    for spec in specs:
        sigma_text, path_text = spec.split(":", 1)
        path = Path(path_text).expanduser().resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        branches.append({"sigma_s_per_m": float(sigma_text), "path": str(path), "data": data})
    branches.sort(key=lambda item: item["sigma_s_per_m"])
    if len(branches) < 3:
        raise ValueError("Need at least three substrate branches for quadratic fit")
    return branches


def build_summary(branches: list[dict[str, Any]], grid: np.ndarray, *, max_percent_error: float) -> dict[str, Any]:
    sigmas = np.asarray([item["sigma_s_per_m"] for item in branches], dtype=float)
    freq = np.asarray(branches[0]["data"]["plot_data"]["freq_hz"], dtype=float)
    metric_fits = {}
    fitted_curves: dict[str, dict[str, list[float]]] = {}
    for metric in METRICS:
        emx = np.asarray(branches[0]["data"]["plot_data"]["emx"][metric], dtype=float)
        hfss_by_sigma = np.asarray([item["data"]["plot_data"]["hfss_ads"][metric] for item in branches], dtype=float)
        coeffs = np.asarray([np.polyfit(sigmas, hfss_by_sigma[:, idx], 2) for idx in range(len(freq))])
        pred = np.asarray([np.polyval(coeffs[idx], grid) for idx in range(len(freq))])
        floor = 1.0e-3 if metric == "k" else 1.0e-9
        pct = np.abs(pred - emx[:, np.newaxis]) / np.maximum(np.abs(emx[:, np.newaxis]), floor) * 100.0
        max_err = np.max(pct, axis=0)
        mean_err = np.mean(pct, axis=0)
        best_idx = int(np.argmin(max_err))
        metric_fits[metric] = {
            "best_sigma_s_per_m": float(grid[best_idx]),
            "best_max_percent_error": float(max_err[best_idx]),
            "best_mean_percent_error": float(mean_err[best_idx]),
            "passes_5_percent_gate": bool(max_err[best_idx] <= max_percent_error),
            "existing_branch_errors": {
                str(item["sigma_s_per_m"]): {
                    "max_percent_error": float(item["data"]["metrics"][metric]["max_percent_error"]),
                    "mean_percent_error": float(item["data"]["metrics"][metric]["mean_percent_error"]),
                    "status": item["data"]["metrics"][metric]["status"],
                }
                for item in branches
            },
        }
        fitted_curves[metric] = {
            "grid_sigma_s_per_m": grid.tolist(),
            "max_percent_error": max_err.tolist(),
            "mean_percent_error": mean_err.tolist(),
        }
    combined_fits = {
        "qp_qs": combined_fit(metric_fits, fitted_curves, grid, ["qp", "qs"], max_percent_error),
        "k_qp_qs": combined_fit(metric_fits, fitted_curves, grid, ["k", "qp", "qs"], max_percent_error),
        "all_gate_metrics": combined_fit(metric_fits, fitted_curves, grid, list(METRICS), max_percent_error),
    }
    return {
        "branches": [{"sigma_s_per_m": item["sigma_s_per_m"], "path": item["path"]} for item in branches],
        "criterion": {"max_percent_error": max_percent_error},
        "fit_model": "quadratic HFSS metric value vs substrate conductivity at each frequency point",
        "frequency_hz": freq.tolist(),
        "metric_fits": metric_fits,
        "combined_fits": combined_fits,
        "fitted_curves": fitted_curves,
        "interpretation": (
            "Substrate conductivity can reduce Q error, but the existing 0/2/10 S/m branches and quadratic fit do not make k pass. "
            "This means substrate loss calibration alone is insufficient for full validation."
        ),
    }


def combined_fit(
    metric_fits: dict[str, Any],
    fitted_curves: dict[str, dict[str, list[float]]],
    grid: np.ndarray,
    metrics: list[str],
    max_percent_error: float,
) -> dict[str, Any]:
    max_by_metric = np.asarray([fitted_curves[metric]["max_percent_error"] for metric in metrics], dtype=float)
    worst = np.max(max_by_metric, axis=0)
    idx = int(np.argmin(worst))
    return {
        "metrics": metrics,
        "best_sigma_s_per_m": float(grid[idx]),
        "best_max_percent_error": float(worst[idx]),
        "passes_5_percent_gate": bool(worst[idx] <= max_percent_error),
        "metric_errors_at_best_sigma": {
            metric: float(fitted_curves[metric]["max_percent_error"][idx]) for metric in metrics
        },
    }


def write_curve_csv(path: Path, summary: dict[str, Any]) -> None:
    grid = summary["fitted_curves"]["k"]["grid_sigma_s_per_m"]
    rows = []
    for idx, sigma in enumerate(grid):
        row: dict[str, Any] = {"sigma_s_per_m": sigma}
        for metric in METRICS:
            row[f"{metric}_max_percent_error"] = summary["fitted_curves"][metric]["max_percent_error"][idx]
            row[f"{metric}_mean_percent_error"] = summary["fitted_curves"][metric]["mean_percent_error"][idx]
        rows.append(row)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Substrate Conductivity Calibration Diagnostic",
        "",
        f"Fit model: {summary['fit_model']}.",
        "",
        "## Per-Metric Optima",
        "",
        "| Metric | Best sigma (S/m) | Best max error | Passes 5% |",
        "|---|---:|---:|---:|",
    ]
    for metric, item in summary["metric_fits"].items():
        lines.append(
            f"| `{metric}` | {item['best_sigma_s_per_m']:.4g} | {item['best_max_percent_error']:.4f}% | {item['passes_5_percent_gate']} |"
        )
    lines.extend(["", "## Combined Optima", "", "| Metrics | Best sigma (S/m) | Best max error | Passes 5% |", "|---|---:|---:|---:|"])
    for name, item in summary["combined_fits"].items():
        lines.append(
            f"| `{name}` | {item['best_sigma_s_per_m']:.4g} | {item['best_max_percent_error']:.4f}% | {item['passes_5_percent_gate']} |"
        )
    lines.extend(["", "## Interpretation", "", summary["interpretation"], ""])
    return "\n".join(lines)


def maybe_plot(out_dir: Path, summary: dict[str, Any]) -> list[Path]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return []
    paths = []
    grid = np.asarray(summary["fitted_curves"]["k"]["grid_sigma_s_per_m"], dtype=float)
    fig, ax = plt.subplots(figsize=(10.8, 5.2), facecolor="#FCFCFD")
    colors = {"k": "#5477C4", "qp": "#CC6F47", "qs": "#BD569B", "lp_nh": "#71B436", "ls_nh": "#B8A037"}
    for metric in METRICS:
        ax.plot(grid, summary["fitted_curves"][metric]["max_percent_error"], label=metric, color=colors[metric], linewidth=2.0)
    ax.axhline(5.0, color="#1F2430", linestyle="--", linewidth=1.0, label="5% gate")
    ax.set_xlabel("HFSS substrate conductivity (S/m)")
    ax.set_ylabel("Fitted max percent error vs EMX")
    ax.set_title("Substrate conductivity improves Q but cannot make k pass", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#E6E8F0")
    ax.legend(frameon=False, ncols=3)
    path = out_dir / "substrate_conductivity_fit_errors.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths.append(path)

    fig, ax = plt.subplots(figsize=(8.5, 4.8), facecolor="#FCFCFD")
    labels = []
    vals = []
    colors_bar = []
    for name, item in summary["combined_fits"].items():
        labels.append(name)
        vals.append(item["best_max_percent_error"])
        colors_bar.append("#71B436" if item["passes_5_percent_gate"] else "#CC6F47")
    ax.bar(labels, vals, color=colors_bar, edgecolor="#1F2430", linewidth=0.4)
    ax.axhline(5.0, color="#1F2430", linestyle="--", linewidth=1.0)
    ax.set_ylabel("Best possible fitted max percent error")
    ax.set_title("No fitted substrate conductivity passes all validation metrics", loc="left", fontweight="bold")
    ax.tick_params(axis="x", rotation=15)
    ax.grid(axis="y", color="#E6E8F0")
    path = out_dir / "substrate_conductivity_combined_optima.png"
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    paths.append(path)
    return paths


def write_manifest(out_dir: Path, inputs: list[Path], outputs: list[Path]) -> Path:
    payload = {
        "inputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in inputs],
        "outputs": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in outputs],
    }
    path = out_dir / "substrate_conductivity_fit_manifest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
