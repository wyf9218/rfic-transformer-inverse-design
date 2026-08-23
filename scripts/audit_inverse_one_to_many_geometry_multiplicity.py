#!/usr/bin/env python3
"""Audit whether fine physical-feature cells contain multiple geometry modes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


INPUT_COLUMNS = (
    "input__lp_nh_center",
    "input__ls_nh_center",
    "input__q_center",
    "input__k_abs_center",
)
INPUT_RANGES = np.asarray(((0.5, 3.0), (0.5, 3.0), (5.0, 25.0), (0.0, 0.8)), dtype=float)
GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
    "geom__line_width_um",
    "geom__primary_terminal_y_span_um",
    "geom__secondary_terminal_y_span_um",
    "geom__offset_um",
    "geom__primary_feed_extension_um",
    "geom__secondary_feed_extension_um",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source = _load_cells(training_csv, args)
    analysis = _analyze_cells(source, args)
    checks = _checks(source, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"
    recommendation = _recommendation(analysis, args) if status == "PASS" else {
        "status": "UNAVAILABLE",
        "decision": "FIX_GEOMETRY_MULTIPLICITY_AUDIT_INPUTS",
    }

    cells_csv = out_dir / "inverse_geometry_multiplicity_cells.csv"
    figure_path = out_dir / "inverse_geometry_multiplicity.png"
    summary_path = out_dir / "inverse_geometry_multiplicity_summary.json"
    report_path = out_dir / "inverse_geometry_multiplicity_report.md"
    _write_csv(cells_csv, analysis.get("cell_records") or [])
    if analysis.get("available") is True:
        _plot(figure_path, analysis, args)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "ONE_TO_MANY_GEOMETRY_AUDIT_READY" if status == "PASS" else "DO_NOT_INTERPRET_GEOMETRY_MULTIPLICITY",
        "recommendation": recommendation,
        "evidence_stage": args.evidence_stage,
        "training_csv": str(training_csv),
        "training_csv_sha256": _sha256(training_csv),
        "input_columns": list(INPUT_COLUMNS),
        "input_ranges": {
            column: {"min": float(INPUT_RANGES[index, 0]), "max": float(INPUT_RANGES[index, 1])}
            for index, column in enumerate(INPUT_COLUMNS)
        },
        "geometry_columns": list(GEOMETRY_COLUMNS),
        "multiplicity_method": {
            "within_cell_physical_trend": "affine ridge residualization",
            "geometry_clustering": "deterministic two-center clustering on residual geometry",
            "target_overlap_gate": (
                "cluster labels must remain within the declared maximum physical-center separation and must not "
                "be predictable from physical targets by leave-one-out nearest-neighbor classification"
            ),
            "max_cluster_physical_separation_rms": float(args.max_cluster_physical_separation_rms),
            "max_cluster_physical_knn_balanced_accuracy": float(
                args.max_cluster_physical_knn_balanced_accuracy
            ),
        },
        "source_evidence": _public_source(source),
        "checks": checks,
        "analysis": {key: value for key, value in analysis.items() if key != "cell_records"},
        "artifacts": {
            "cell_metrics_csv": str(cells_csv),
            "figure_png": str(figure_path),
            "report_md": str(report_path),
        },
        "literature_basis": [
            {
                "source": "Diffusion Model Inverse Modeling and Applications to Microwave Filters, Electronics 2026",
                "url": "https://www.mdpi.com/2079-9292/15/3/527",
                "adaptation": "Test whether one target region has multiple valid parameter modes before training a top-k conditional generator.",
            },
            {
                "source": "Tandem Neural Network Based Design of Multiband Antennas",
                "adaptation": (
                    "Use forward-equivalent geometry multiplicity rather than direct geometry-label uniqueness; "
                    "remove within-cell physical drift before deciding that one target has multiple geometry modes."
                ),
            },
        ],
        "scientific_boundary": (
            "PASS means the configured cell-resolution multiplicity calculation is traceable and numerically valid. "
            "The exploratory_coarse stage cannot authorize a generative model or replace the deterministic/tandem "
            "baselines; only the later confirmatory_fine audit may support a preregistered top-k ablation. The recommendation "
            "is diagnostic. Geometry is first residualized against continuous physical variation inside each fine cell, "
            "and candidate mode labels must overlap in physical-target space. Fabrication symmetries or nonlinear "
            "within-cell drift can still mimic modes. A top-k generator still requires fixed-split forward screening, "
            "DRC, and real EMX verification."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={payload['decision']}")
    print(f"recommendation={recommendation.get('decision')}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--evidence-stage",
        choices=("exploratory_coarse", "confirmatory_fine"),
        default="confirmatory_fine",
    )
    parser.add_argument("--min-source-rows", type=int, default=500_000)
    parser.add_argument("--physical-cell-bins", type=int, default=10)
    parser.add_argument("--min-cell-rows", type=int, default=12)
    parser.add_argument("--max-rows-per-cell", type=int, default=256)
    parser.add_argument("--min-analyzed-cells", type=int, default=128)
    parser.add_argument("--max-analyzed-cells", type=int, default=2_048)
    parser.add_argument("--kmeans-iterations", type=int, default=30)
    parser.add_argument("--residual-ridge", type=float, default=1.0e-8)
    parser.add_argument("--min-k2-sse-reduction", type=float, default=0.30)
    parser.add_argument("--min-center-separation-rms", type=float, default=0.15)
    parser.add_argument("--min-cluster-fraction", type=float, default=0.20)
    parser.add_argument("--max-cluster-physical-separation-rms", type=float, default=0.25)
    parser.add_argument("--max-cluster-physical-knn-balanced-accuracy", type=float, default=0.75)
    parser.add_argument("--min-multimodal-cell-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_source_rows < 1 or args.physical_cell_bins < 2 or args.min_cell_rows < 4:
        parser.error("source rows must be positive, bins >= 2, and min-cell-rows >= 4")
    if args.max_rows_per_cell < args.min_cell_rows:
        parser.error("--max-rows-per-cell must be at least --min-cell-rows")
    if not 1 <= args.min_analyzed_cells <= args.max_analyzed_cells:
        parser.error("analyzed-cell counts must satisfy 1 <= min <= max")
    for value in (
        args.min_k2_sse_reduction,
        args.min_cluster_fraction,
        args.max_cluster_physical_knn_balanced_accuracy,
        args.min_multimodal_cell_fraction,
    ):
        if not 0.0 <= value <= 1.0:
            parser.error("fraction thresholds must be in [0, 1]")
    if (
        args.min_center_separation_rms < 0.0
        or args.max_cluster_physical_separation_rms < 0.0
        or args.residual_ridge < 0.0
        or args.kmeans_iterations < 1
    ):
        parser.error("separation/ridge must be nonnegative and K-means iterations positive")
    return args


def _load_cells(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = {
        "row_count": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "out_of_range_count": 0,
        "duplicate_geometry_count": 0,
        "columns_present": False,
        "cell_count": 0,
        "eligible_cell_count": 0,
        "geometry_min": np.full(len(GEOMETRY_COLUMNS), np.inf),
        "geometry_max": np.full(len(GEOMETRY_COLUMNS), -np.inf),
        "cells": {},
    }
    if not path.is_file():
        return result
    rng = np.random.default_rng(int(args.seed))
    seen_geometry: set[bytes] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(INPUT_COLUMNS) | set(GEOMETRY_COLUMNS)
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            return result
        for row in reader:
            result["row_count"] += 1
            physical = _float_row(row, INPUT_COLUMNS)
            geometry = _float_row(row, GEOMETRY_COLUMNS)
            if physical is None or geometry is None:
                result["invalid_count"] += 1
                continue
            if not _in_range(physical):
                result["out_of_range_count"] += 1
                continue
            digest = _vector_digest(geometry)
            if digest in seen_geometry:
                result["duplicate_geometry_count"] += 1
                continue
            seen_geometry.add(digest)
            cell_key = _cell_key(physical, int(args.physical_cell_bins))
            cell = result["cells"].setdefault(cell_key, {"count": 0, "physical": [], "geometry": []})
            cell["count"] += 1
            result["valid_count"] += 1
            result["geometry_min"] = np.minimum(result["geometry_min"], geometry)
            result["geometry_max"] = np.maximum(result["geometry_max"], geometry)
            if len(cell["geometry"]) < int(args.max_rows_per_cell):
                cell["physical"].append(physical)
                cell["geometry"].append(geometry)
            else:
                replacement = int(rng.integers(0, int(cell["count"])))
                if replacement < int(args.max_rows_per_cell):
                    cell["physical"][replacement] = physical
                    cell["geometry"][replacement] = geometry
    result["cell_count"] = len(result["cells"])
    result["eligible_cell_count"] = sum(
        int(cell["count"]) >= int(args.min_cell_rows) for cell in result["cells"].values()
    )
    return result


def _analyze_cells(source: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    geometry_min = np.asarray(source.get("geometry_min"), dtype=float)
    geometry_max = np.asarray(source.get("geometry_max"), dtype=float)
    geometry_span = geometry_max - geometry_min
    if source.get("valid_count", 0) < 1 or np.any(~np.isfinite(geometry_span)) or np.any(geometry_span <= 0.0):
        return {"available": False, "cell_records": []}
    eligible = [
        (key, cell)
        for key, cell in source["cells"].items()
        if int(cell["count"]) >= int(args.min_cell_rows)
    ]
    eligible.sort(key=lambda item: _cell_hash(item[0], int(args.seed)))
    selected = eligible[: int(args.max_analyzed_cells)]
    records = []
    for key, cell in selected:
        geometry = np.asarray(cell["geometry"], dtype=float)
        physical = np.asarray(cell["physical"], dtype=float)
        geometry_normalized = (geometry - geometry_min[None, :]) / geometry_span[None, :]
        physical_normalized = (physical - INPUT_RANGES[:, 0][None, :]) / (
            INPUT_RANGES[:, 1] - INPUT_RANGES[:, 0]
        )[None, :]
        geometry_residual, residualization = _residualize_geometry(
            physical_normalized,
            geometry_normalized,
            float(args.residual_ridge),
        )
        cluster, labels = _two_cluster(geometry_residual, int(args.kmeans_iterations))
        raw_geometry_spread = float(np.sqrt(np.mean(np.var(geometry_normalized, axis=0))))
        residual_geometry_spread = float(np.sqrt(np.mean(np.var(geometry_residual, axis=0))))
        physical_spread = float(np.sqrt(np.mean(np.var(physical_normalized, axis=0))))
        cluster_physical_separation = _cluster_center_separation(
            physical_normalized * float(args.physical_cell_bins),
            labels,
        )
        cluster_physical_knn_accuracy = _leave_one_out_physical_knn_balanced_accuracy(
            physical_normalized * float(args.physical_cell_bins),
            labels,
        )
        multimodal = bool(
            cluster["k2_sse_reduction"] >= float(args.min_k2_sse_reduction)
            and cluster["center_separation_rms"] >= float(args.min_center_separation_rms)
            and cluster["minimum_cluster_fraction"] >= float(args.min_cluster_fraction)
            and cluster_physical_separation <= float(args.max_cluster_physical_separation_rms)
            and cluster_physical_knn_accuracy
            <= float(args.max_cluster_physical_knn_balanced_accuracy)
        )
        records.append(
            {
                "cell_key": "|".join(str(value) for value in key),
                "source_cell_count": int(cell["count"]),
                "sampled_cell_count": int(len(geometry)),
                "physical_rms_spread": physical_spread,
                "raw_geometry_rms_spread": raw_geometry_spread,
                "residual_geometry_rms_spread": residual_geometry_spread,
                "geometry_rms_spread": residual_geometry_spread,
                "residualization_explained_fraction": residualization["explained_fraction"],
                "residualization_design_rank": residualization["design_rank"],
                "cluster_physical_center_separation_rms": cluster_physical_separation,
                "cluster_physical_knn_balanced_accuracy": cluster_physical_knn_accuracy,
                **cluster,
                "multimodal_evidence": multimodal,
            }
        )
    multimodal_count = sum(item["multimodal_evidence"] for item in records)
    return {
        "available": bool(records),
        "analyzed_cell_count": len(records),
        "multimodal_cell_count": int(multimodal_count),
        "multimodal_cell_fraction": float(multimodal_count / len(records)) if records else 0.0,
        "median_k2_sse_reduction": _median(records, "k2_sse_reduction"),
        "median_center_separation_rms": _median(records, "center_separation_rms"),
        "median_geometry_rms_spread": _median(records, "geometry_rms_spread"),
        "median_raw_geometry_rms_spread": _median(records, "raw_geometry_rms_spread"),
        "median_residual_geometry_rms_spread": _median(records, "residual_geometry_rms_spread"),
        "median_residualization_explained_fraction": _median(records, "residualization_explained_fraction"),
        "median_cluster_physical_center_separation_rms": _median(
            records, "cluster_physical_center_separation_rms"
        ),
        "median_cluster_physical_knn_balanced_accuracy": _median(
            records, "cluster_physical_knn_balanced_accuracy"
        ),
        "median_physical_rms_spread": _median(records, "physical_rms_spread"),
        "geometry_observed_min": {
            column: float(geometry_min[index]) for index, column in enumerate(GEOMETRY_COLUMNS)
        },
        "geometry_observed_max": {
            column: float(geometry_max[index]) for index, column in enumerate(GEOMETRY_COLUMNS)
        },
        "cell_records": records,
    }


def _residualize_geometry(
    physical: np.ndarray,
    geometry: np.ndarray,
    ridge: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    centered_physical = physical - np.mean(physical, axis=0, keepdims=True)
    design = np.column_stack((np.ones(len(physical), dtype=float), centered_physical))
    gram = design.T @ design
    penalty = np.eye(gram.shape[0], dtype=float) * float(ridge)
    penalty[0, 0] = 0.0
    try:
        coefficients = np.linalg.solve(gram + penalty, design.T @ geometry)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.lstsq(gram + penalty, design.T @ geometry, rcond=None)[0]
    predicted = design @ coefficients
    residual = geometry - predicted
    centered_geometry = geometry - np.mean(geometry, axis=0, keepdims=True)
    total_sse = float(np.sum(centered_geometry**2))
    residual_sse = float(np.sum(residual**2))
    explained = 0.0 if total_sse <= 0.0 else 1.0 - residual_sse / total_sse
    return residual, {
        "explained_fraction": float(min(1.0, max(0.0, explained))),
        "design_rank": int(np.linalg.matrix_rank(design)),
    }


def _cluster_center_separation(values: np.ndarray, labels: np.ndarray) -> float:
    if len(values) != len(labels) or len(np.unique(labels)) < 2:
        return 0.0
    centers = [np.mean(values[labels == cluster], axis=0) for cluster in (0, 1)]
    return float(np.sqrt(np.mean((centers[0] - centers[1]) ** 2)))


def _leave_one_out_physical_knn_balanced_accuracy(
    values: np.ndarray,
    labels: np.ndarray,
) -> float:
    if len(values) != len(labels) or len(values) < 4 or len(np.unique(labels)) < 2:
        return 1.0
    distances = np.sum((values[:, None, :] - values[None, :, :]) ** 2, axis=2)
    np.fill_diagonal(distances, np.inf)
    predictions = labels[np.argmin(distances, axis=1)]
    recalls = [float(np.mean(predictions[labels == cluster] == cluster)) for cluster in (0, 1)]
    return float(np.mean(recalls))


def _two_cluster(values: np.ndarray, iterations: int) -> tuple[dict[str, Any], np.ndarray]:
    mean = np.mean(values, axis=0)
    residual = values - mean[None, :]
    sse_one = float(np.sum(residual**2))
    first = int(np.argmax(np.sum(residual**2, axis=1)))
    second = int(np.argmax(np.sum((values - values[first][None, :]) ** 2, axis=1)))
    centers = np.vstack((values[first], values[second])).astype(float)
    labels = np.zeros(len(values), dtype=int)
    for _ in range(iterations):
        distances = np.sum((values[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        if len(np.unique(labels)) < 2:
            break
        centers = np.vstack([np.mean(values[labels == cluster], axis=0) for cluster in (0, 1)])
    counts = np.bincount(labels, minlength=2)
    if np.any(counts == 0) or sse_one <= 0.0:
        return {
            "k1_sse": sse_one,
            "k2_sse": sse_one,
            "k2_sse_reduction": 0.0,
            "center_separation_rms": 0.0,
            "within_cluster_rms": float(np.sqrt(sse_one / max(1, values.size))),
            "minimum_cluster_fraction": 0.0,
        }, labels
    sse_two = float(sum(np.sum((values[labels == cluster] - centers[cluster][None, :]) ** 2) for cluster in (0, 1)))
    return {
        "k1_sse": sse_one,
        "k2_sse": sse_two,
        "k2_sse_reduction": float(max(0.0, 1.0 - sse_two / sse_one)),
        "center_separation_rms": float(np.sqrt(np.mean((centers[0] - centers[1]) ** 2))),
        "within_cluster_rms": float(np.sqrt(sse_two / max(1, values.size))),
        "minimum_cluster_fraction": float(np.min(counts) / len(values)),
    }, labels


def _checks(source: dict[str, Any], analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    records = analysis.get("cell_records") or []
    finite_fields = (
        "physical_rms_spread",
        "raw_geometry_rms_spread",
        "residual_geometry_rms_spread",
        "geometry_rms_spread",
        "residualization_explained_fraction",
        "cluster_physical_center_separation_rms",
        "cluster_physical_knn_balanced_accuracy",
        "k2_sse_reduction",
        "center_separation_rms",
        "within_cluster_rms",
        "minimum_cluster_fraction",
    )
    geometry_span = np.asarray(source.get("geometry_max"), dtype=float) - np.asarray(source.get("geometry_min"), dtype=float)
    checks = {
        "formal_columns_present": source.get("columns_present") is True,
        "source_rows_meet_500k_stage_minimum": int(source.get("valid_count") or 0) >= int(args.min_source_rows),
        "source_rows_finite": int(source.get("invalid_count") or 0) == 0,
        "all_rows_inside_declared_physical_ranges": int(source.get("out_of_range_count") or 0) == 0,
        "independent_geometry_unique": int(source.get("duplicate_geometry_count") or 0) == 0,
        "all_geometry_dimensions_vary": geometry_span.shape == (len(GEOMETRY_COLUMNS),)
        and np.isfinite(geometry_span).all()
        and np.all(geometry_span > 0.0),
        "enough_fine_physical_cells": int(source.get("eligible_cell_count") or 0) >= int(args.min_analyzed_cells),
        "enough_analyzed_cells": int(analysis.get("analyzed_cell_count") or 0) >= int(args.min_analyzed_cells),
        "cell_metrics_finite": bool(records)
        and all(all(_finite(item.get(field)) is not None for field in finite_fields) for item in records),
        "cell_samples_meet_minimum": bool(records)
        and all(int(item.get("sampled_cell_count") or 0) >= int(args.min_cell_rows) for item in records),
    }
    return {key: bool(value) for key, value in checks.items()}


def _recommendation(analysis: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    fraction = float(analysis.get("multimodal_cell_fraction") or 0.0)
    threshold = float(args.min_multimodal_cell_fraction)
    supports_modes = fraction >= threshold
    if args.evidence_stage == "exploratory_coarse":
        decision = (
            "COARSE_MULTIMODAL_SIGNAL_CONTINUE_TANDEM_AND_PLAN_FINE_AUDIT"
            if supports_modes
            else "COARSE_MULTIMODAL_SIGNAL_WEAK_CONTINUE_FIXED_BASELINES"
        )
        status = "EXPLORATORY_ONLY_NO_MODEL_AUTHORIZATION"
    elif supports_modes:
        decision = "SUPPORTS_TOP_K_GENERATIVE_MODEL_ABLATION_AT_500K"
        status = "AUDIT_ONLY_NO_AUTOMATIC_MODEL_CHANGE"
    else:
        decision = "WEAK_MULTIMODAL_EVIDENCE_KEEP_DETERMINISTIC_BASELINE"
        status = "AUDIT_ONLY_NO_AUTOMATIC_MODEL_CHANGE"
    comparison = ">=" if supports_modes else "<"
    reason = f"multimodal cell fraction={fraction:.6g} {comparison} {threshold:.6g}"
    return {
        "status": status,
        "decision": decision,
        "reason": reason,
        "multimodal_cell_fraction": fraction,
        "threshold": threshold,
        "eligible_for_top_k_ablation": bool(
            args.evidence_stage == "confirmatory_fine" and supports_modes
        ),
        "eligible_for_model_replacement": False,
    }


def _plot(path: Path, analysis: dict[str, Any], args: argparse.Namespace) -> None:
    records = analysis["cell_records"]
    reduction = np.asarray([item["k2_sse_reduction"] for item in records], dtype=float)
    separation = np.asarray([item["center_separation_rms"] for item in records], dtype=float)
    physical_knn_accuracy = np.asarray(
        [item["cluster_physical_knn_balanced_accuracy"] for item in records], dtype=float
    )
    geometry_spread = np.asarray([item["residual_geometry_rms_spread"] for item in records], dtype=float)
    physical_spread = np.asarray([item["physical_rms_spread"] for item in records], dtype=float)
    multimodal = np.asarray([item["multimodal_evidence"] for item in records], dtype=bool)
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), constrained_layout=True)
    fig.patch.set_facecolor("white")
    for axis in axes.flat:
        axis.set_facecolor("white")
        axis.tick_params(colors="#202020")
        axis.xaxis.label.set_color("#202020")
        axis.yaxis.label.set_color("#202020")
        axis.title.set_color("#202020")
    axes[0, 0].hist(reduction, bins=30, color="#3976b7", alpha=0.85)
    axes[0, 0].axvline(float(args.min_k2_sse_reduction), color="#ba3d31", linestyle="--")
    axes[0, 0].set_title("Two-cluster SSE reduction")
    axes[0, 0].set_xlabel("1 - SSE(k=2) / SSE(k=1)")
    axes[0, 0].set_ylabel("Fine physical cells")

    axes[0, 1].scatter(physical_spread[~multimodal], geometry_spread[~multimodal], s=15, alpha=0.55, label="single-mode evidence")
    axes[0, 1].scatter(physical_spread[multimodal], geometry_spread[multimodal], s=20, alpha=0.75, label="multimodal evidence")
    axes[0, 1].set_title("Residual geometry spread inside tight physical cells")
    axes[0, 1].set_xlabel("Physical RMS spread (declared-range normalized)")
    axes[0, 1].set_ylabel("Residual geometry RMS spread (observed-span normalized)")
    axes[0, 1].legend(facecolor="white", framealpha=1.0)

    axes[1, 0].scatter(separation, reduction, c=np.where(multimodal, 1.0, 0.0), cmap="coolwarm", s=18, alpha=0.7)
    axes[1, 0].axvline(float(args.min_center_separation_rms), color="#666666", linestyle="--")
    axes[1, 0].axhline(float(args.min_k2_sse_reduction), color="#666666", linestyle="--")
    axes[1, 0].set_title("Multimodality decision plane")
    axes[1, 0].set_xlabel("Geometry-center separation RMS")
    axes[1, 0].set_ylabel("Two-cluster SSE reduction")

    axes[1, 1].scatter(
        physical_knn_accuracy[~multimodal],
        separation[~multimodal],
        s=16,
        alpha=0.55,
        label="rejected mode evidence",
    )
    axes[1, 1].scatter(
        physical_knn_accuracy[multimodal],
        separation[multimodal],
        s=22,
        alpha=0.75,
        label="retained mode evidence",
    )
    axes[1, 1].axvline(
        float(args.max_cluster_physical_knn_balanced_accuracy), color="#666666", linestyle="--"
    )
    axes[1, 1].axhline(float(args.min_center_separation_rms), color="#666666", linestyle="--")
    axes[1, 1].set_title("Geometry modes vs physical-label predictability")
    axes[1, 1].set_xlabel("Physical-target 1-NN balanced accuracy for mode label")
    axes[1, 1].set_ylabel("Residual geometry-center separation RMS")
    axes[1, 1].legend(facecolor="white", framealpha=1.0)

    fig.suptitle("One-to-many inverse-design evidence from real-EMX-labeled geometry cells", fontsize=15, color="#202020")
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _cell_key(physical: np.ndarray, bins: int) -> tuple[int, ...]:
    normalized = (physical - INPUT_RANGES[:, 0]) / (INPUT_RANGES[:, 1] - INPUT_RANGES[:, 0])
    indices = np.floor(normalized * bins).astype(int)
    indices[np.isclose(physical, INPUT_RANGES[:, 1], rtol=0.0, atol=1.0e-12)] = bins - 1
    return tuple(int(value) for value in indices)


def _in_range(physical: np.ndarray) -> bool:
    return bool(np.all(physical >= INPUT_RANGES[:, 0]) and np.all(physical <= INPUT_RANGES[:, 1]))


def _cell_hash(key: tuple[int, ...], seed: int) -> str:
    return hashlib.sha256(f"{seed}|{'|'.join(map(str, key))}".encode("ascii")).hexdigest()


def _median(records: list[dict[str, Any]], field: str) -> float | None:
    values = np.asarray([float(item[field]) for item in records], dtype=float)
    return float(np.median(values)) if values.size else None


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key not in {"cells", "geometry_min", "geometry_max"}
    }


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    lines = [
        "# Inverse one-to-many geometry multiplicity audit",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Decision: **{data['decision']}**",
        f"- Evidence stage: **{data['evidence_stage']}**",
        f"- Recommendation: **{data['recommendation'].get('decision')}**",
        f"- Analyzed fine physical cells: `{analysis.get('analyzed_cell_count')}`",
        f"- Multimodal evidence fraction: `{analysis.get('multimodal_cell_fraction')}`",
        f"- Median physical-trend fraction removed: `{analysis.get('median_residualization_explained_fraction')}`",
        f"- Median cluster physical-center separation: `{analysis.get('median_cluster_physical_center_separation_rms')}`",
        f"- Median physical-target 1-NN mode accuracy: `{analysis.get('median_cluster_physical_knn_balanced_accuracy')}`",
        "",
        data["scientific_boundary"],
        "",
    ]
    return "\n".join(lines)


def _float_row(row: dict[str, str], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vector_digest(values: np.ndarray) -> bytes:
    return hashlib.blake2b(np.asarray(values, dtype="<f8").tobytes(), digest_size=16).digest()


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
