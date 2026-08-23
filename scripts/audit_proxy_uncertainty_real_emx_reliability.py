#!/usr/bin/env python3
"""Audit whether candidate-time uncertainty predicts real EMX realization error.

The KNN uncertainty emitted during candidate generation is not a simulator
label. This audit pairs it with later real-EMX returns by independent geometry
and asks whether low-uncertainty candidates actually realize their predicted
Lp/Ls/Q/|K| bins more reliably. One source uses a geometry-hash holdout; two or
more sources train interval scales on history and evaluate the latest source.

The output is diagnostic only. It never changes geometry, labels, acceptance,
or acquisition ranking automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_proxy_to_real_physical_feature_calibration as calibration  # noqa: E402


DEFAULT_FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
APPROVED_DECISION = "UNCERTAINTY_RELIABLE_FOR_TARGET_HIT_ABLATION_ONLY"
REJECTED_DECISION = "DO_NOT_USE_UNCERTAINTY_FOR_TARGET_HIT_RANKING"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    features = tuple(item.strip() for item in args.feature_columns.split(",") if item.strip())
    ranges = calibration._resolve_ranges(features, args.feature_ranges_json)
    sources = [Path(value).expanduser().resolve() for value in args.paired_csv]
    loaded = _load_records(sources, features, ranges, bool(args.require_touchstone))
    groups = loaded["groups"]

    checks: dict[str, bool] = {
        "all_sources_exist": all(path.is_file() for path in sources),
        "feature_contract_supported": bool(features) and all(feature in ranges for feature in features),
        "minimum_independent_geometries": len(groups) >= int(args.min_independent_geometries),
    }
    paths = {
        "summary": out_dir / "proxy_uncertainty_real_emx_reliability_summary.json",
        "report": out_dir / "proxy_uncertainty_real_emx_reliability_report.md",
        "figure": out_dir / "proxy_uncertainty_real_emx_reliability.png",
    }
    if not all(checks.values()):
        payload = _base_payload(args, features, ranges, sources, loaded, checks, paths)
        payload.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_UNCERTAINTY_AND_REAL_EMX_PAIRS",
                "eligible_for_acquisition_ablation": False,
                "failure_reasons": [name for name, passed in checks.items() if not passed],
            }
        )
        _write_outputs(payload, paths, write_plot=False)
        return _finish(payload, paths, bool(args.no_fail_exit))

    group_keys = sorted(groups)
    if args.holdout_mode == "latest-source":
        latest_source_index = max(int(item["latest_source_index"]) for item in groups.values())
        holdout_keys = {
            key for key, item in groups.items() if latest_source_index in item["source_indices"]
        }
        split_policy = "latest paired CSV source held out by independent geometry"
    else:
        latest_source_index = None
        holdout_keys = calibration._holdout_groups(
            group_keys,
            int(args.seed),
            float(args.holdout_fraction),
            int(args.min_holdout_geometries),
        )
        split_policy = "deterministic independent-geometry hash holdout"
    train_keys = [key for key in group_keys if key not in holdout_keys]
    checks["minimum_holdout_geometries"] = len(holdout_keys) >= int(args.min_holdout_geometries)
    checks["nonempty_training_geometries"] = len(train_keys) >= 2
    if not checks["minimum_holdout_geometries"] or not checks["nonempty_training_geometries"]:
        payload = _base_payload(args, features, ranges, sources, loaded, checks, paths)
        payload.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_UNCERTAINTY_AND_REAL_EMX_PAIRS",
                "eligible_for_acquisition_ablation": False,
                "failure_reasons": [name for name, passed in checks.items() if not passed],
            }
        )
        _write_outputs(payload, paths, write_plot=False)
        return _finish(payload, paths, bool(args.no_fail_exit))

    train_pred, train_unc, train_real = _arrays(groups, train_keys)
    holdout_pred, holdout_unc, holdout_real = _arrays(groups, sorted(holdout_keys))
    interval_scales = _fit_interval_scales(
        train_pred,
        train_unc,
        train_real,
        float(args.interval_coverage),
        float(args.uncertainty_floor),
    )
    metrics = _metrics(
        holdout_pred,
        holdout_unc,
        holdout_real,
        features,
        ranges,
        int(args.bins),
        interval_scales,
        float(args.uncertainty_floor),
    )
    approval_checks = {
        "aggregate_spearman": metrics["aggregate_spearman_uncertainty_vs_error"]
        >= float(args.min_aggregate_spearman),
        "high_low_error_ratio": metrics["high_vs_low_uncertainty_error_ratio"]
        >= float(args.min_high_low_error_ratio),
        "low_high_bin_hit_delta": metrics["low_minus_high_uncertainty_mean_1d_bin_accuracy"]
        >= float(args.min_low_high_bin_accuracy_delta),
        "quartile_error_monotonicity": metrics["nondecreasing_error_quartile_steps"]
        >= int(args.min_nondecreasing_quartile_steps),
        "scaled_interval_coverage": metrics["scaled_interval_empirical_coverage"]
        >= float(args.interval_coverage) - float(args.interval_coverage_tolerance),
    }
    approved = all(approval_checks.values())
    payload = _base_payload(args, features, ranges, sources, loaded, checks, paths)
    payload.update(
        {
            "overall_status": "PASS",
            "decision": APPROVED_DECISION if approved else REJECTED_DECISION,
            "eligible_for_acquisition_ablation": approved,
            "split": {
                "policy": split_policy,
                "holdout_mode": args.holdout_mode,
                "latest_source_index": latest_source_index,
                "seed": int(args.seed),
                "train_geometry_count": len(train_keys),
                "holdout_geometry_count": len(holdout_keys),
                "train_group_sha256": calibration._digest_lines(train_keys),
                "holdout_group_sha256": calibration._digest_lines(sorted(holdout_keys)),
                "train_real_target_sha256": _real_target_digest(groups, train_keys, features),
                "holdout_real_target_sha256": _real_target_digest(
                    groups,
                    sorted(holdout_keys),
                    features,
                ),
            },
            "interval_scales": {
                feature: float(interval_scales[index]) for index, feature in enumerate(features)
            },
            "holdout_metrics": metrics,
            "approval_checks": approval_checks,
            "thresholds": {
                "min_aggregate_spearman": float(args.min_aggregate_spearman),
                "min_high_low_error_ratio": float(args.min_high_low_error_ratio),
                "min_low_high_bin_accuracy_delta": float(args.min_low_high_bin_accuracy_delta),
                "min_nondecreasing_quartile_steps": int(args.min_nondecreasing_quartile_steps),
                "interval_coverage": float(args.interval_coverage),
                "interval_coverage_tolerance": float(args.interval_coverage_tolerance),
            },
        }
    )
    _write_outputs(payload, paths, write_plot=not bool(args.no_plots), plot_data=(holdout_pred, holdout_unc, holdout_real))
    return _finish(payload, paths, bool(args.no_fail_exit))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-csv", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=",".join(DEFAULT_FEATURES))
    parser.add_argument("--feature-ranges-json")
    parser.add_argument("--holdout-mode", choices=("hash", "latest-source"), default="hash")
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument("--min-independent-geometries", type=int, default=80)
    parser.add_argument("--min-holdout-geometries", type=int, default=20)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--interval-coverage", type=float, default=0.90)
    parser.add_argument("--interval-coverage-tolerance", type=float, default=0.08)
    parser.add_argument("--uncertainty-floor", type=float, default=1.0e-9)
    parser.add_argument("--min-aggregate-spearman", type=float, default=0.20)
    parser.add_argument("--min-high-low-error-ratio", type=float, default=1.25)
    parser.add_argument("--min-low-high-bin-accuracy-delta", type=float, default=0.05)
    parser.add_argument("--min-nondecreasing-quartile-steps", type=int, default=2)
    parser.add_argument("--require-touchstone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 < float(args.holdout_fraction) < 1.0:
        parser.error("--holdout-fraction must be in (0, 1)")
    if not 0.0 < float(args.interval_coverage) < 1.0:
        parser.error("--interval-coverage must be in (0, 1)")
    if int(args.bins) < 2:
        parser.error("--bins must be at least 2")
    return args


def _load_records(
    sources: list[Path],
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
    require_touchstone: bool,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray, int]]] = defaultdict(list)
    stats: dict[str, int] = defaultdict(int)
    source_records: list[dict[str, Any]] = []
    for source_index, source in enumerate(sources):
        record: dict[str, Any] = {
            "path": str(source),
            "exists": source.is_file(),
            "sha256": calibration._file_sha(source),
        }
        source_records.append(record)
        if not source.is_file():
            continue
        with source.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            geometry_fields = sorted(
                field for field in fields if field.startswith("geom__") or field.startswith("candidate__geom__")
            )
            prediction_fields = calibration._resolve_prediction_fields(fields, features, "pred_")
            uncertainty_fields = _resolve_uncertainty_fields(fields, features)
            real_contract = calibration._real_feature_contract(fields, features)
            record["resolved_prediction_columns"] = prediction_fields
            record["resolved_uncertainty_columns"] = uncertainty_fields
            record["real_feature_contract"] = real_contract
            if (
                len(prediction_fields) != len(features)
                or len(uncertainty_fields) != len(features)
                or not all(real_contract.values())
            ):
                stats["source_contract_failure_count"] += 1
                continue
            for row in reader:
                stats["row_count"] += 1
                if not calibration._truthy(row.get("ok", "true")):
                    stats["not_ok_count"] += 1
                    continue
                predicted = _vector(row, tuple(prediction_fields[feature] for feature in features))
                uncertainty = _vector(row, tuple(uncertainty_fields[feature] for feature in features))
                real = calibration._real_feature_vector(row, features)
                if predicted is None or uncertainty is None or real is None:
                    stats["nonfinite_count"] += 1
                    continue
                if np.any(uncertainty < 0.0):
                    stats["negative_uncertainty_count"] += 1
                    continue
                if any(
                    not ranges[feature][0] <= real[index] <= ranges[feature][1]
                    for index, feature in enumerate(features)
                ):
                    stats["real_out_of_range_count"] += 1
                    continue
                if require_touchstone and not calibration._valid_touchstone(row, source.parent):
                    stats["touchstone_failure_count"] += 1
                    continue
                group_key = calibration._geometry_group(row, geometry_fields)
                if group_key is None:
                    stats["missing_geometry_identity_count"] += 1
                    continue
                grouped[group_key].append((predicted, uncertainty, real, source_index))
                stats["accepted_pair_row_count"] += 1
    groups: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    for key, rows in grouped.items():
        duplicate_rows += max(0, len(rows) - 1)
        groups[key] = {
            "predicted": np.mean(np.vstack([item[0] for item in rows]), axis=0),
            "uncertainty": np.mean(np.vstack([item[1] for item in rows]), axis=0),
            "real": np.mean(np.vstack([item[2] for item in rows]), axis=0),
            "repeat_count": len(rows),
            "source_indices": sorted({int(item[3]) for item in rows}),
            "latest_source_index": max(int(item[3]) for item in rows),
        }
    stats["duplicate_geometry_row_count"] = duplicate_rows
    stats["independent_geometry_count"] = len(groups)
    return {"groups": groups, "stats": dict(stats), "sources": source_records}


def _resolve_uncertainty_fields(fields: list[str], features: tuple[str, ...]) -> dict[str, str]:
    available = set(fields)
    result: dict[str, str] = {}
    for feature in features:
        aliases = (
            f"pred_uncertainty_{feature}",
            f"queue__pred_uncertainty_{feature}",
            f"candidate__pred_uncertainty_{feature}",
        )
        for alias in aliases:
            if alias in available:
                result[feature] = alias
                break
    return result


def _vector(row: dict[str, str], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [calibration._finite(row.get(column)) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def _arrays(
    groups: dict[str, dict[str, Any]], keys: list[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.vstack([groups[key]["predicted"] for key in keys]),
        np.vstack([groups[key]["uncertainty"] for key in keys]),
        np.vstack([groups[key]["real"] for key in keys]),
    )


def _real_target_digest(
    groups: dict[str, dict[str, Any]],
    keys: list[str],
    features: tuple[str, ...],
) -> str:
    lines = ["features=" + ",".join(features)]
    for key in sorted(keys):
        values = np.asarray(groups[key]["real"], dtype=float)
        lines.append(key + "|" + "|".join(float(value).hex() for value in values))
    return calibration._digest_lines(lines)


def _fit_interval_scales(
    predicted: np.ndarray,
    uncertainty: np.ndarray,
    real: np.ndarray,
    coverage: float,
    floor: float,
) -> np.ndarray:
    ratios = np.abs(predicted - real) / np.maximum(uncertainty, floor)
    scales = []
    for axis in range(ratios.shape[1]):
        values = np.sort(np.asarray(ratios[:, axis], dtype=float))
        rank = min(len(values) - 1, max(0, int(math.ceil((len(values) + 1) * coverage)) - 1))
        scales.append(float(values[rank]))
    return np.asarray(scales, dtype=float)


def _metrics(
    predicted: np.ndarray,
    uncertainty: np.ndarray,
    real: np.ndarray,
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
    bins: int,
    interval_scales: np.ndarray,
    floor: float,
) -> dict[str, Any]:
    spans = np.asarray([ranges[feature][1] - ranges[feature][0] for feature in features], dtype=float)
    normalized_error = np.abs(predicted - real) / spans[None, :]
    normalized_uncertainty = uncertainty / spans[None, :]
    aggregate_error = np.mean(normalized_error, axis=1)
    aggregate_uncertainty = np.mean(normalized_uncertainty, axis=1)
    predicted_bins = np.column_stack(
        [calibration._bin_indices(predicted[:, axis], ranges[feature], bins) for axis, feature in enumerate(features)]
    )
    real_bins = np.column_stack(
        [calibration._bin_indices(real[:, axis], ranges[feature], bins) for axis, feature in enumerate(features)]
    )
    per_row_1d_accuracy = np.mean(predicted_bins == real_bins, axis=1)
    per_row_4d_accuracy = np.all(predicted_bins == real_bins, axis=1).astype(float)
    order = np.argsort(aggregate_uncertainty, kind="stable")
    quartiles = []
    for index, indices in enumerate(np.array_split(order, 4), start=1):
        quartiles.append(
            {
                "quartile": index,
                "count": int(len(indices)),
                "mean_range_normalized_uncertainty": float(np.mean(aggregate_uncertainty[indices])),
                "mean_range_normalized_absolute_error": float(np.mean(aggregate_error[indices])),
                "mean_1d_bin_accuracy": float(np.mean(per_row_1d_accuracy[indices])),
                "four_d_bin_accuracy": float(np.mean(per_row_4d_accuracy[indices])),
            }
        )
    low = quartiles[0]
    high = quartiles[-1]
    errors = [float(item["mean_range_normalized_absolute_error"]) for item in quartiles]
    covered = np.abs(predicted - real) <= np.maximum(uncertainty, floor) * interval_scales[None, :]
    feature_metrics = {}
    for axis, feature in enumerate(features):
        feature_metrics[feature] = {
            "spearman_uncertainty_vs_absolute_error": _spearman(
                normalized_uncertainty[:, axis], normalized_error[:, axis]
            ),
            "mean_range_normalized_absolute_error": float(np.mean(normalized_error[:, axis])),
            "mean_range_normalized_uncertainty": float(np.mean(normalized_uncertainty[:, axis])),
            "scaled_interval_empirical_coverage": float(np.mean(covered[:, axis])),
        }
    return {
        "geometry_count": int(predicted.shape[0]),
        "aggregate_spearman_uncertainty_vs_error": _spearman(aggregate_uncertainty, aggregate_error),
        "high_vs_low_uncertainty_error_ratio": float(
            high["mean_range_normalized_absolute_error"]
            / max(float(low["mean_range_normalized_absolute_error"]), 1.0e-15)
        ),
        "low_minus_high_uncertainty_mean_1d_bin_accuracy": float(
            low["mean_1d_bin_accuracy"] - high["mean_1d_bin_accuracy"]
        ),
        "nondecreasing_error_quartile_steps": int(
            sum(errors[index + 1] + 1.0e-15 >= errors[index] for index in range(3))
        ),
        "mean_range_normalized_absolute_error": float(np.mean(aggregate_error)),
        "mean_range_normalized_uncertainty": float(np.mean(aggregate_uncertainty)),
        "mean_1d_bin_accuracy": float(np.mean(per_row_1d_accuracy)),
        "four_d_bin_accuracy": float(np.mean(per_row_4d_accuracy)),
        "scaled_interval_empirical_coverage": float(np.mean(covered)),
        "quartiles": quartiles,
        "per_feature": feature_metrics,
    }


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    x = _rankdata(np.asarray(left, dtype=float))
    y = _rankdata(np.asarray(right, dtype=float))
    if len(x) < 2 or float(np.std(x)) <= 0.0 or float(np.std(y)) <= 0.0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and values[order[stop]] == values[order[start]]:
            stop += 1
        ranks[order[start:stop]] = 0.5 * (start + stop - 1) + 1.0
        start = stop
    return ranks


def _base_payload(
    args: argparse.Namespace,
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
    sources: list[Path],
    loaded: dict[str, Any],
    checks: dict[str, bool],
    paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature_columns": list(features),
        "feature_ranges": {feature: list(ranges[feature]) for feature in features if feature in ranges},
        "source_csvs": loaded.get("sources")
        or [{"path": str(path), "exists": path.is_file()} for path in sources],
        "input_stats": loaded.get("stats", {}),
        "checks": checks,
        "artifacts": {name: str(path) for name, path in paths.items()},
        "scientific_boundary": (
            "Candidate-time uncertainty is acquisition provenance, not an EMX label. This audit can only justify an equal-budget target-hit ranking ablation; it never changes geometry, simulator labels, acceptance, final uniformity, or publication gates automatically."
        ),
    }


def _write_outputs(
    payload: dict[str, Any],
    paths: dict[str, Path],
    *,
    write_plot: bool,
    plot_data: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> None:
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["report"].write_text(_render_report(payload), encoding="utf-8")
    if write_plot and plot_data is not None and payload.get("overall_status") == "PASS":
        _write_plot(paths["figure"], payload, *plot_data)


def _write_plot(
    path: Path,
    payload: dict[str, Any],
    predicted: np.ndarray,
    uncertainty: np.ndarray,
    real: np.ndarray,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    features = tuple(payload["feature_columns"])
    ranges = payload["feature_ranges"]
    spans = np.asarray([ranges[feature][1] - ranges[feature][0] for feature in features], dtype=float)
    error = np.mean(np.abs(predicted - real) / spans[None, :], axis=1)
    unc = np.mean(uncertainty / spans[None, :], axis=1)
    quartiles = payload["holdout_metrics"]["quartiles"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), facecolor="white")
    axes[0].scatter(unc, error, s=16, alpha=0.55, color="#1f77b4", edgecolors="none")
    axes[0].set_xlabel("Mean range-normalized predicted uncertainty")
    axes[0].set_ylabel("Mean range-normalized real EMX absolute error")
    axes[0].grid(alpha=0.25)
    axes[1].bar(
        [f"Q{item['quartile']}" for item in quartiles],
        [item["mean_range_normalized_absolute_error"] for item in quartiles],
        color=["#2a9d8f", "#8ab17d", "#e9c46a", "#e76f51"],
    )
    axes[1].set_xlabel("Predicted uncertainty quartile")
    axes[1].set_ylabel("Mean real EMX absolute error")
    axes[1].grid(axis="y", alpha=0.25)
    fig.suptitle("Candidate-time uncertainty vs real EMX realization error")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Proxy Uncertainty vs Real EMX Reliability Audit",
        "",
        f"- Status: `{payload.get('overall_status')}`",
        f"- Decision: `{payload.get('decision')}`",
        f"- Independent geometries: `{payload.get('input_stats', {}).get('independent_geometry_count', 0)}`",
        "",
    ]
    metrics = payload.get("holdout_metrics") or {}
    if metrics:
        lines.extend(
            [
                "## Holdout evidence",
                "",
                f"- Aggregate Spearman: `{metrics.get('aggregate_spearman_uncertainty_vs_error')}`",
                f"- High/low uncertainty error ratio: `{metrics.get('high_vs_low_uncertainty_error_ratio')}`",
                f"- Low minus high uncertainty 1-D bin accuracy: `{metrics.get('low_minus_high_uncertainty_mean_1d_bin_accuracy')}`",
                f"- Scaled interval empirical coverage: `{metrics.get('scaled_interval_empirical_coverage')}`",
                "",
                "| Quartile | Rows | Mean uncertainty | Mean real error | Mean 1-D bin accuracy | 4-D bin accuracy |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in metrics.get("quartiles", []):
            lines.append(
                "| Q{} | {} | {:.6g} | {:.6g} | {:.6g} | {:.6g} |".format(
                    item["quartile"],
                    item["count"],
                    item["mean_range_normalized_uncertainty"],
                    item["mean_range_normalized_absolute_error"],
                    item["mean_1d_bin_accuracy"],
                    item["four_d_bin_accuracy"],
                )
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            str(payload.get("scientific_boundary")),
        ]
    )
    return "\n".join(lines) + "\n"


def _finish(payload: dict[str, Any], paths: dict[str, Path], no_fail_exit: bool) -> int:
    print(f"overall_status={payload.get('overall_status')}")
    print(f"decision={payload.get('decision')}")
    print(f"summary={paths['summary']}")
    print(f"report={paths['report']}")
    if payload.get("overall_status") in {"PASS", "WAITING"}:
        return 0 if payload.get("overall_status") == "PASS" or no_fail_exit else 2
    return 0 if no_fail_exit else 2


if __name__ == "__main__":
    raise SystemExit(main())
