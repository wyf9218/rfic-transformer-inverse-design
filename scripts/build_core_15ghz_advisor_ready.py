#!/usr/bin/env python3
"""Build the advisor-ready 15-GHz core-panel proxy report.

This script is intentionally narrow. It reads the existing frozen fixed8k
target/prediction artifacts, applies the frozen target-only core filter, and
computes descriptive proxy diagnostics. It performs no model inference,
training, target generation, support-distance analysis, or EM simulation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.analysis.fixed8k_application_panels import (  # noqa: E402
    CORE_PANEL,
    FEATURE_NAMES,
    NORMALIZATION_SPANS,
    create_no_clobber_directory,
    derive_errors,
    distribution_statistics,
    panel_membership,
    sha256_file,
)
from scripts.build_fixed8k_15ghz_application_panels import (  # noqa: E402
    EVIDENCE_CLASS,
    EXPECTED_MODEL_ID,
    MODEL_CONTRACT_SHA256,
    MODEL_SUMMARY_SHA256,
    MODEL_WEIGHTS_SHA256,
    PREDICTION_SHA256,
    TARGET_SHA256,
    TRAINING_TABLE_SHA256,
    _aligned_fixed8k,
    _identity_audit,
    _load_identity_sources,
)


EXPECTED_CORE_N = 150
EXPECTED_JOINT_10_COUNT = 131
EXPECTED_STRICT_10_COUNT = 97
PANEL_DESCRIPTION = "Literature-informed 15-GHz core application panel"
PANEL_SUBTITLE = (
    "Target-only application-aligned subset of the frozen deterministic fixed8k frame."
)
FEATURE_LABELS = ("Lp", "Ls", "Qmin", "|K|")
FEATURE_UNITS = ("nH", "nH", "dimensionless", "dimensionless")
COLORS = ("#0B6EDE", "#E4572E", "#7048E8", "#008F7A")
NAVY = "#071A33"
INK = "#111827"
MUTED = "#526174"
GRID = "#D7DEE8"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output-utc", default="")
    return parser.parse_args(argv)


def _utc_token(value: str) -> str:
    if value:
        token = value.strip()
        try:
            datetime.strptime(token, "%Y%m%dT%H%M%SZ")
        except ValueError as exc:
            raise ValueError("--output-utc must use YYYYMMDDTHHMMSSZ") from exc
        return token
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _manual_core_mask(targets: np.ndarray) -> np.ndarray:
    values = np.asarray(targets, dtype=float)
    ratio = values[:, 1] / values[:, 0]
    return (
        (values[:, 0] >= 0.5)
        & (values[:, 0] <= 1.5)
        & (values[:, 1] >= 0.5)
        & (values[:, 1] <= 1.5)
        & (values[:, 2] >= 10.0)
        & (values[:, 2] <= 20.0)
        & (values[:, 3] >= 0.5)
        & (values[:, 3] <= 0.8)
        & (ratio >= 0.67)
        & (ratio <= 1.5)
    )


def _independent_core_reproduction(
    targets: np.ndarray,
    predictions: np.ndarray,
    implementation_mask: np.ndarray,
    implementation_errors: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    """Recompute the core mask and metrics without using report vectors."""

    target_values = np.asarray(targets, dtype=float)
    predicted_values = np.asarray(predictions, dtype=float)
    manual_mask = _manual_core_mask(target_values)
    raw = predicted_values - target_values
    normalized = np.abs(raw) / np.asarray([2.5, 2.5, 20.0, 0.8])[None, :]
    joint = np.sqrt(np.mean(normalized**2, axis=1))
    strict = np.max(normalized, axis=1)
    denominator = int(np.sum(manual_mask))
    joint_count = int(np.sum(joint[manual_mask] <= 0.10))
    strict_count = int(np.sum(strict[manual_mask] <= 0.10))
    crosschecks = {
        "target_only_mask_equal": bool(np.array_equal(manual_mask, implementation_mask)),
        "normalized_absolute_max_difference": float(
            np.max(
                np.abs(
                    normalized
                    - np.asarray(implementation_errors["normalized_absolute"], dtype=float)
                )
            )
        ),
        "joint_rmse_max_difference": float(
            np.max(
                np.abs(
                    joint
                    - np.asarray(implementation_errors["joint_normalized_rmse"], dtype=float)
                )
            )
        ),
        "strict_max_difference": float(
            np.max(
                np.abs(
                    strict
                    - np.asarray(implementation_errors["strict_max_feature_error"], dtype=float)
                )
            )
        ),
    }
    expected = (EXPECTED_CORE_N, EXPECTED_JOINT_10_COUNT, EXPECTED_STRICT_10_COUNT)
    actual = (denominator, joint_count, strict_count)
    status = (
        "PASS"
        if actual == expected
        and crosschecks["target_only_mask_equal"]
        and all(
            crosschecks[name] <= 1e-15
            for name in (
                "normalized_absolute_max_difference",
                "joint_rmse_max_difference",
                "strict_max_difference",
            )
        )
        else "FAIL"
    )
    result = {
        "status": status,
        "expected": {
            "core_n": EXPECTED_CORE_N,
            "symmetric_joint_rmse_le_0p10_count": EXPECTED_JOINT_10_COUNT,
            "strict_all_four_le_0p10_count": EXPECTED_STRICT_10_COUNT,
        },
        "actual": {
            "core_n": denominator,
            "symmetric_joint_rmse_le_0p10_count": joint_count,
            "symmetric_joint_rmse_le_0p10_fraction": joint_count / denominator,
            "strict_all_four_le_0p10_count": strict_count,
            "strict_all_four_le_0p10_fraction": strict_count / denominator,
        },
        "implementation_crosschecks": crosschecks,
    }
    if status != "PASS":
        raise RuntimeError(f"core-panel reproduction gate failed: {result}")
    return result


def _core_rows(
    ids: Sequence[str],
    targets: np.ndarray,
    predictions: np.ndarray,
    mask: np.ndarray,
    errors: Mapping[str, np.ndarray],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ratio = targets[:, 1] / targets[:, 0]
    core_indices = np.flatnonzero(mask)
    for index in core_indices:
        row: dict[str, Any] = {
            "target_id": ids[index],
            "target_Ls_over_Lp": float(ratio[index]),
        }
        for feature_index, feature in enumerate(FEATURE_NAMES):
            row[f"target_{feature}"] = float(targets[index, feature_index])
            row[f"predicted_{feature}"] = float(predictions[index, feature_index])
            row[f"signed_{feature}_error"] = float(errors["raw"][index, feature_index])
            row[f"absolute_{feature}_error"] = float(
                errors["absolute"][index, feature_index]
            )
            row[f"normalized_absolute_{feature}_error"] = float(
                errors["normalized_absolute"][index, feature_index]
            )
        row.update(
            {
                "Q_shortfall": float(errors["q_shortfall"][index]),
                "Q_target_met": int(bool(errors["q_target_met"][index])),
                "symmetric_joint_rmse": float(
                    errors["joint_normalized_rmse"][index]
                ),
                "strict_all_four_error": float(
                    errors["strict_max_feature_error"][index]
                ),
            }
        )
        rows.append(row)
    return rows


def _feature_metric_rows(
    errors: Mapping[str, np.ndarray], mask: np.ndarray
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (label, unit, span) in enumerate(
        zip(FEATURE_LABELS, FEATURE_UNITS, NORMALIZATION_SPANS)
    ):
        stats = distribution_statistics(
            np.asarray(errors["raw"])[mask, index],
            np.asarray(errors["absolute"])[mask, index],
        )
        rows.append(
            {
                "feature": label,
                "unit": unit,
                "normalization_span": float(span),
                "count": int(stats["count"]),
                "bias": float(stats["bias"]),
                "mae": float(stats["mae"]),
                "rmse": float(stats["rmse"]),
                "p50_absolute_error": float(stats["p50"]),
                "p90_absolute_error": float(stats["p90"]),
                "p95_absolute_error": float(stats["p95"]),
                "p99_absolute_error": float(stats["p99"]),
                "maximum_absolute_error": float(stats["maximum"]),
            }
        )
    return rows


def _q_metrics(
    targets: np.ndarray,
    predictions: np.ndarray,
    mask: np.ndarray,
) -> dict[str, Any]:
    target = np.asarray(targets)[mask, 2]
    predicted = np.asarray(predictions)[mask, 2]
    met = predicted >= target
    shortfall = np.maximum(target - predicted, 0.0)
    return {
        "schema": "core_15ghz_q_engineering_metrics.v1",
        "definition": "Q_shortfall=max(target_Qmin-predicted_Qmin,0)",
        "denominator": int(len(shortfall)),
        "q_target_met_count": int(np.sum(met)),
        "q_target_met_fraction": float(np.mean(met)),
        "q_shortfall_mean": float(np.mean(shortfall)),
        "q_shortfall_rmse": float(np.sqrt(np.mean(shortfall**2))),
        "q_shortfall_p90": float(np.percentile(shortfall, 90.0)),
        "q_shortfall_p95": float(np.percentile(shortfall, 95.0)),
        "primary_joint_metric_uses_q_shortfall": False,
        "primary_joint_metric_q_definition": "abs(predicted_Qmin-target_Qmin)/20",
    }


def _save_figure(fig: plt.Figure, stem: Path) -> tuple[Path, Path]:
    png = stem.with_suffix(".png")
    svg = stem.with_suffix(".svg")
    fig.savefig(png, dpi=300, facecolor="white")
    fig.savefig(svg, facecolor="white")
    plt.close(fig)
    return png, svg


def _base_figure(title: str, subtitle: str = "") -> plt.Figure:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    fig.text(0.055, 0.925, title, fontsize=23, fontweight="bold", color=NAVY)
    if subtitle:
        fig.text(0.055, 0.875, subtitle, fontsize=10.5, color=MUTED)
    fig.add_artist(
        plt.Line2D([0.055, 0.945], [0.845, 0.845], color=GRID, linewidth=1.2)
    )
    return fig


def _plot_headline(
    output_dir: Path,
    reproduction: Mapping[str, Any],
) -> tuple[Path, Path]:
    actual = reproduction["actual"]
    fig = _base_figure(
        "Frozen 15-GHz inverse model: core-panel proxy fidelity",
        PANEL_DESCRIPTION,
    )
    ax = fig.add_axes([0.055, 0.27, 0.89, 0.50])
    ax.axis("off")
    ax.add_patch(
        plt.Rectangle((0.00, 0.32), 0.45, 0.62, facecolor="#F3F7FD", edgecolor="#B8C7DB")
    )
    ax.add_patch(
        plt.Rectangle((0.55, 0.32), 0.45, 0.62, facecolor="#F5F1FD", edgecolor="#C8B9E8")
    )
    ax.text(0.225, 0.72, "87.3%", ha="center", va="center", fontsize=48,
            fontweight="bold", color="#0B6EDE")
    ax.text(0.225, 0.55, "Joint-RMSE <=10%", ha="center", va="center", fontsize=17,
            fontweight="bold", color=INK)
    ax.text(0.225, 0.41, f"{actual['symmetric_joint_rmse_le_0p10_count']} / {actual['core_n']} targets",
            ha="center", va="center", fontsize=15, color=MUTED)
    ax.text(0.775, 0.72, "64.7%", ha="center", va="center", fontsize=48,
            fontweight="bold", color="#7048E8")
    ax.text(0.775, 0.55, "All four features individually <=10%", ha="center", va="center",
            fontsize=15.5, fontweight="bold", color=INK)
    ax.text(0.775, 0.41, f"{actual['strict_all_four_le_0p10_count']} / {actual['core_n']} targets",
            ha="center", va="center", fontsize=15, color=MUTED)
    ranges = (
        "Lp/Ls: 0.5-1.5 nH     Qmin: 10-20     |K|: 0.5-0.8     "
        "Ls/Lp: 0.67-1.5"
    )
    ax.text(0.5, 0.12, ranges, ha="center", va="center", fontsize=14,
            fontweight="bold", color=NAVY)
    footer = (
        "Frozen-forward proxy diagnostic  |  Not fresh EMX  |  "
        "Target-only application-aligned subset  |  N=150"
    )
    fig.text(0.5, 0.09, footer, ha="center", fontsize=9.5, color=MUTED)
    return _save_figure(fig, output_dir / "01_core_panel_headline")


def _ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=float))
    return ordered, np.arange(1, len(ordered) + 1, dtype=float) / len(ordered)


def _plot_cdf(
    output_dir: Path,
    errors: Mapping[str, np.ndarray],
    mask: np.ndarray,
) -> tuple[Path, Path]:
    fig = _base_figure(
        "Normalized absolute-error distribution",
        "One common horizontal scale; frozen spans: Lp=2.5 nH, Ls=2.5 nH, Qmin=20, |K|=0.8",
    )
    ax = fig.add_axes([0.07, 0.15, 0.62, 0.64])
    normalized = np.asarray(errors["normalized_absolute"])[mask] * 100.0
    xmax = max(10.0, math.ceil(float(np.max(normalized)) / 5.0) * 5.0)
    percentiles: list[tuple[str, float, float, float]] = []
    for index, (label, color) in enumerate(zip(FEATURE_LABELS, COLORS)):
        x, y = _ecdf(normalized[:, index])
        ax.step(x, y, where="post", linewidth=2.3, color=color, label=label)
        percentiles.append(
            (
                label,
                float(np.percentile(x, 50.0)),
                float(np.percentile(x, 90.0)),
                float(np.percentile(x, 95.0)),
            )
        )
    ax.axvline(10.0, color=INK, linestyle="--", linewidth=1.2, label="10% threshold")
    ax.set_xlim(0.0, xmax)
    ax.set_ylim(0.0, 1.01)
    ax.set_xlabel("Absolute error / frozen normalization span (%)", fontsize=11)
    ax.set_ylabel("Cumulative fraction of core targets", fontsize=11)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.75)
    ax.legend(loc="lower right", frameon=False, ncol=2)

    table_ax = fig.add_axes([0.73, 0.27, 0.22, 0.43])
    table_ax.axis("off")
    table_ax.text(0.0, 1.08, "Normalized absolute-error percentiles", fontsize=11,
                  fontweight="bold", color=NAVY, transform=table_ax.transAxes)
    cells = [[label, f"{p50:.2f}%", f"{p90:.2f}%", f"{p95:.2f}%"]
             for label, p50, p90, p95 in percentiles]
    table = table_ax.table(
        cellText=cells,
        colLabels=["Feature", "P50", "P90", "P95"],
        cellLoc="center",
        colLoc="center",
        loc="upper left",
        bbox=[0.0, 0.15, 1.0, 0.78],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_facecolor(NAVY)
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
    fig.text(0.73, 0.20, "N=150 target-only core-panel rows", fontsize=9.5, color=MUTED)
    return _save_figure(fig, output_dir / "02_core_normalized_error_cdf")


def _plot_feature_summary(
    output_dir: Path,
    feature_rows: Sequence[Mapping[str, Any]],
    q_metrics: Mapping[str, Any],
) -> tuple[Path, Path]:
    fig = _base_figure(
        "Core-panel feature errors in physical units",
        "Bias is available in the audit table; this view compares absolute-error MAE, RMSE, and P95",
    )
    positions = (
        [0.055, 0.34, 0.20, 0.43],
        [0.275, 0.34, 0.20, 0.43],
        [0.495, 0.34, 0.20, 0.43],
        [0.715, 0.34, 0.20, 0.43],
    )
    bar_colors = ("#0B6EDE", "#4C86D9", "#90B5E8")
    for row, position in zip(feature_rows, positions):
        ax = fig.add_axes(position)
        values = [float(row["mae"]), float(row["rmse"]), float(row["p95_absolute_error"])]
        bars = ax.bar([0, 1, 2], values, color=bar_colors, width=0.64)
        ax.set_xticks([0, 1, 2], ["MAE", "RMSE", "P95"])
        ax.set_title(str(row["feature"]), fontsize=14, fontweight="bold", color=NAVY, pad=10)
        unit = str(row["unit"])
        ax.set_ylabel(unit if unit != "dimensionless" else "dimensionless", fontsize=9.5)
        ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.75)
        ax.set_axisbelow(True)
        ymax = max(values) * 1.25 if max(values) > 0 else 1.0
        ax.set_ylim(0.0, ymax)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + ymax * 0.025,
                f"{value:.3g}",
                ha="center",
                va="bottom",
                fontsize=9,
                color=INK,
            )
        ax.spines[["top", "right"]].set_visible(False)

    box = fig.add_axes([0.055, 0.12, 0.89, 0.14])
    box.axis("off")
    box.add_patch(
        plt.Rectangle((0.0, 0.0), 1.0, 1.0, facecolor="#F5F8FA", edgecolor="#C7D1DD")
    )
    box.text(0.025, 0.67, "Q engineering view", fontsize=11.5, fontweight="bold", color=NAVY)
    box.text(
        0.025,
        0.30,
        f"Target met: {q_metrics['q_target_met_fraction'] * 100.0:.1f}% "
        f"({q_metrics['q_target_met_count']}/{q_metrics['denominator']})",
        fontsize=11,
        color=INK,
    )
    box.text(
        0.42,
        0.30,
        f"Shortfall mean: {q_metrics['q_shortfall_mean']:.3f}",
        fontsize=11,
        color=INK,
    )
    box.text(
        0.72,
        0.30,
        f"Shortfall P95: {q_metrics['q_shortfall_p95']:.3f}",
        fontsize=11,
        color=INK,
    )
    return _save_figure(fig, output_dir / "03_core_feature_error_summary")


def _core_contract(generated_utc: str) -> dict[str, Any]:
    return {
        "schema": "core_15ghz_advisor_panel_contract.v1",
        "frozen_utc": generated_utc,
        "panel_description": PANEL_DESCRIPTION,
        "panel_subtitle": PANEL_SUBTITLE,
        "frequency_ghz": 15.0,
        "selection_source": "target values only",
        "selection": {
            "target_Lp_nH": [0.5, 1.5],
            "target_Ls_nH": [0.5, 1.5],
            "target_Qmin": [10.0, 20.0],
            "target_K_abs": [0.5, 0.8],
            "target_Ls_over_Lp": [0.67, 1.5],
        },
        "forbidden_selection_inputs": [
            "predicted values",
            "prediction error",
            "joint score",
            "Q target-met status",
            "geometry quality",
            "result-dependent thresholds",
        ],
        "normalization_spans": dict(zip(FEATURE_NAMES, NORMALIZATION_SPANS.tolist())),
        "primary_metrics": {
            "feature_error": "abs(predicted-target)/frozen_span for all four features",
            "symmetric_joint_rmse": "sqrt(mean(e_Lp^2,e_Ls^2,e_Q^2,e_K^2))",
            "strict_all_four_error": "max(e_Lp,e_Ls,e_Q,e_K)",
            "threshold": 0.10,
            "Q_definition": "absolute Q error",
        },
        "q_engineering_metric": {
            "definition": "max(target_Qmin-predicted_Qmin,0)",
            "included_in_primary_joint_metric": False,
        },
        "expected_reproduction": {
            "core_n": EXPECTED_CORE_N,
            "joint_le_0p10_count": EXPECTED_JOINT_10_COUNT,
            "strict_le_0p10_count": EXPECTED_STRICT_10_COUNT,
        },
        "classification": "project application limits; not an industry or IEEE standard",
        "evidence_class": EVIDENCE_CLASS,
        "fresh_emx": False,
    }


def _summary_text(q_metrics: Mapping[str, Any]) -> str:
    return (
        "# Application-Aligned Proxy Performance of the Frozen 15-GHz Inverse Transformer Model\n\n"
        f"**Panel:** {PANEL_DESCRIPTION}.  \n"
        f"**Definition:** {PANEL_SUBTITLE}\n\n"
        "## Main result\n\n"
        "Within the literature-informed 15-GHz core application panel, "
        "131 of 150 targets, or 87.3%, satisfy a symmetric normalized "
        "joint-RMSE threshold of 10%. Under the stricter requirement that "
        "Lp, Ls, Qmin, and |K| must each individually remain within 10% "
        "of the frozen normalization span, 97 of 150 targets, or 64.7%, "
        "satisfy the criterion.\n\n"
        "## Interpretation\n\n"
        "The frozen inverse model demonstrates strong proxy-level "
        "performance within the target region most relevant to the intended "
        "15-GHz transformer matching application.\n\n"
        "## Q engineering view\n\n"
        f"Predicted Qmin meets or exceeds the requested lower bound for "
        f"{q_metrics['q_target_met_count']} of {q_metrics['denominator']} targets "
        f"({q_metrics['q_target_met_fraction'] * 100.0:.1f}%). The mean one-sided "
        f"Q shortfall is {q_metrics['q_shortfall_mean']:.4f}, and its P95 is "
        f"{q_metrics['q_shortfall_p95']:.4f}. This separate engineering view is "
        "not used inside the primary absolute-Q fidelity metric.\n\n"
        "## Limitations\n\n"
        "- This is a target-only application-aligned subset.\n"
        "- The panel was introduced as an application-specific analysis.\n"
        "- The panel contains 150 deterministic targets.\n"
        "- It is a frozen-forward proxy diagnostic.\n"
        "- It is not fresh-EMX accuracy.\n"
        "- The percentages must not be described as whole-domain accuracy.\n"
        "- The parameter limits are project application limits, not an industry or IEEE standard.\n"
        "- Marginal range compliance does not prove joint physical feasibility.\n"
    )


def _sha_index(output_dir: Path) -> None:
    index = output_dir / "SHA256SUMS.txt"
    files = sorted(path for path in output_dir.iterdir() if path.is_file() and path != index)
    index.write_text(
        "\n".join(f"{sha256_file(path)}  {path.name}" for path in files) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace = Path(args.workspace_root).expanduser().resolve()
    utc_token = _utc_token(str(args.output_utc))
    generated_utc = (
        datetime.strptime(utc_token, "%Y%m%dT%H%M%SZ")
        .replace(tzinfo=timezone.utc)
        .isoformat()
    )

    sources = _load_identity_sources(workspace)
    aligned = _aligned_fixed8k(sources)
    source_audit = _identity_audit(sources, aligned)
    masks, _, _ = panel_membership(aligned["targets"])
    core_mask = np.asarray(masks[CORE_PANEL.name], dtype=bool)
    errors = derive_errors(aligned["targets"], aligned["predictions"])
    reproduction = _independent_core_reproduction(
        aligned["targets"], aligned["predictions"], core_mask, errors
    )

    output_dir = create_no_clobber_directory(
        workspace / "reports" / f"core_15ghz_advisor_ready_{utc_token}"
    )
    contract_path = output_dir / "CORE_PANEL_CONTRACT.json"
    audit_path = output_dir / "SOURCE_IDENTITY_AUDIT.json"
    rows_path = output_dir / "core_panel_rows.csv"
    feature_path = output_dir / "core_feature_metrics.csv"
    q_path = output_dir / "core_q_metrics.json"
    summary_path = output_dir / "CORE_ADVISOR_SUMMARY.md"
    receipt_path = output_dir / "FINAL_RECEIPT.json"

    _write_json(contract_path, _core_contract(generated_utc))
    tailored_audit = {
        "schema": "core_15ghz_source_identity_audit.v1",
        "status": "PASS",
        "generated_utc": generated_utc,
        "checks": {
            name: passed
            for name, passed in source_audit["checks"].items()
            if not name.startswith("previous_")
        },
        "frozen_target_artifact": source_audit["frozen_target_artifact"],
        "prediction_artifact": source_audit["prediction_artifact"],
        "model": source_audit["model"],
        "core_panel": {
            "selection_source": "target values only",
            "row_count": EXPECTED_CORE_N,
            "reproduction": reproduction,
        },
        "prohibited_actions_performed": {
            "model_retrained": False,
            "targets_regenerated": False,
            "model_inference_rerun": False,
            "emx_run": False,
            "support_distance_analysis": False,
        },
    }
    _write_json(audit_path, tailored_audit)

    core_rows = _core_rows(
        aligned["target_ids"],
        aligned["targets"],
        aligned["predictions"],
        core_mask,
        errors,
    )
    feature_rows = _feature_metric_rows(errors, core_mask)
    q_metrics = _q_metrics(aligned["targets"], aligned["predictions"], core_mask)
    _write_csv(rows_path, core_rows)
    _write_csv(feature_path, feature_rows)
    _write_json(q_path, q_metrics)
    summary_path.write_text(_summary_text(q_metrics), encoding="utf-8")

    figure_pairs = [
        _plot_headline(output_dir, reproduction),
        _plot_cdf(output_dir, errors, core_mask),
        _plot_feature_summary(output_dir, feature_rows, q_metrics),
    ]
    figure_files = [path for pair in figure_pairs for path in pair]
    figure_source_bindings = {
        path.name: {
            "sha256": sha256_file(path),
            "source_files": {
                rows_path.name: sha256_file(rows_path),
                feature_path.name: sha256_file(feature_path),
                q_path.name: sha256_file(q_path),
            },
            "evidence_class": EVIDENCE_CLASS,
            "fresh_emx": False,
            "denominator": EXPECTED_CORE_N,
        }
        for path in figure_files
    }
    receipt = {
        "schema": "core_15ghz_advisor_ready_receipt.v1",
        "status": "COMPLETE",
        "blocker": None,
        "generated_utc": generated_utc,
        "output_directory": str(output_dir),
        "report": str(summary_path),
        "panel_description": PANEL_DESCRIPTION,
        "core_n": reproduction["actual"]["core_n"],
        "core_joint_10_count": reproduction["actual"][
            "symmetric_joint_rmse_le_0p10_count"
        ],
        "core_joint_10_fraction": reproduction["actual"][
            "symmetric_joint_rmse_le_0p10_fraction"
        ],
        "core_strict_10_count": reproduction["actual"][
            "strict_all_four_le_0p10_count"
        ],
        "core_strict_10_fraction": reproduction["actual"][
            "strict_all_four_le_0p10_fraction"
        ],
        "q_target_met_fraction": q_metrics["q_target_met_fraction"],
        "reproduction_gate": reproduction,
        "source_identity": {
            "target_sha256": TARGET_SHA256,
            "prediction_sha256": PREDICTION_SHA256,
            "model_contract_sha256": MODEL_CONTRACT_SHA256,
            "model_summary_sha256": MODEL_SUMMARY_SHA256,
            "model_weights_sha256": MODEL_WEIGHTS_SHA256,
            "training_table_sha256": TRAINING_TABLE_SHA256,
            "model_id": EXPECTED_MODEL_ID,
        },
        "figure_count": 3,
        "figure_file_count": len(figure_files),
        "figure_source_bindings": figure_source_bindings,
        "model_retrained": False,
        "model_inference_rerun": False,
        "emx_run": False,
        "fresh_emx": False,
        "evidence_class": EVIDENCE_CLASS,
    }
    _write_json(receipt_path, receipt)
    _sha_index(output_dir)
    print(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
