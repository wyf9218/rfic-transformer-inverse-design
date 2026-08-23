#!/usr/bin/env python3
"""Audit monotonic proxy-to-real calibration for acquisition bin targeting.

The input rows must pair candidate-time predictions with real EMX-derived
Lp/Ls/Q/|K| labels for the same geometry. A geometry-group holdout decides
whether an isotonic map is useful. Only a favorable holdout result emits a
deployment mapping, and that mapping is restricted to acquisition ranking.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from physical_feature_prediction_calibration import (  # noqa: E402
    APPROVED_DECISION,
    SCHEMA,
    apply_matrix,
    fit_isotonic_mapping,
)


DEFAULT_FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
DEFAULT_RANGES = {
    "lp_nh_center": (0.5, 3.0),
    "ls_nh_center": (0.5, 3.0),
    "q_center": (5.0, 25.0),
    "k_abs_center": (0.0, 0.8),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    feature_columns = tuple(item.strip() for item in args.feature_columns.split(",") if item.strip())
    ranges = _resolve_ranges(feature_columns, args.feature_ranges_json)
    sources = [Path(value).expanduser().resolve() for value in args.paired_csv]
    loaded = _load_pairs(sources, feature_columns, args.pred_column_prefix, ranges, args.require_touchstone)
    groups = loaded["groups"]
    checks = {
        "all_sources_exist": all(path.is_file() for path in sources),
        "feature_contract_supported": bool(feature_columns) and all(feature in ranges for feature in feature_columns),
        "minimum_independent_geometries": len(groups) >= args.min_independent_geometries,
    }

    summary_path = out_dir / "proxy_to_real_physical_calibration_summary.json"
    report_path = out_dir / "proxy_to_real_physical_calibration_report.md"
    figure_path = out_dir / "proxy_to_real_physical_calibration_holdout.png"
    if not all(checks.values()):
        payload = _base_payload(args, feature_columns, ranges, sources, loaded, checks)
        payload.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_MORE_REAL_EMX_PAIRS",
                "eligible_for_selector": False,
                "failure_reasons": [key for key, passed in checks.items() if not passed],
                "evaluation_mapping": None,
                "deployment_mapping": None,
            }
        )
        _write_outputs(summary_path, report_path, payload)
        print("overall_status=WAITING")
        print("decision=WAIT_FOR_MORE_REAL_EMX_PAIRS")
        print(f"summary={summary_path}")
        return 0 if args.no_fail_exit else 2

    group_keys = sorted(groups)
    if args.holdout_mode == "latest-source":
        latest_source_index = max(int(item["latest_source_index"]) for item in groups.values())
        holdout_keys = {
            key for key, item in groups.items() if latest_source_index in item["source_indices"]
        }
        split_policy = "latest paired CSV source held out by independent geometry"
    else:
        latest_source_index = None
        holdout_keys = _holdout_groups(group_keys, args.seed, args.holdout_fraction, args.min_holdout_geometries)
        split_policy = "deterministic independent-geometry hash holdout"
    train_keys = [key for key in group_keys if key not in holdout_keys]
    checks["minimum_holdout_geometries"] = len(holdout_keys) >= args.min_holdout_geometries
    checks["nonempty_training_geometries"] = len(train_keys) >= 2
    if not checks["minimum_holdout_geometries"] or not checks["nonempty_training_geometries"]:
        payload = _base_payload(args, feature_columns, ranges, sources, loaded, checks)
        payload.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_MORE_REAL_EMX_PAIRS",
                "eligible_for_selector": False,
                "failure_reasons": [key for key, passed in checks.items() if not passed],
                "evaluation_mapping": None,
                "deployment_mapping": None,
            }
        )
        _write_outputs(summary_path, report_path, payload)
        return 0 if args.no_fail_exit else 2

    train_pred, train_real = _arrays(groups, train_keys)
    holdout_pred, holdout_real = _arrays(groups, sorted(holdout_keys))
    evaluation_mapping = _fit_mapping(train_pred, train_real, feature_columns, ranges)
    holdout_calibrated = apply_matrix(holdout_pred, feature_columns, evaluation_mapping)
    raw_metrics = _metrics(holdout_pred, holdout_real, feature_columns, ranges, args.bins, args.feature_pairs)
    calibrated_metrics = _metrics(
        holdout_calibrated, holdout_real, feature_columns, ranges, args.bins, args.feature_pairs
    )
    improvements = _improvements(raw_metrics, calibrated_metrics, feature_columns)
    approval_checks = {
        "range_normalized_mae_improvement": improvements["range_normalized_mae_relative_improvement"]
        >= args.min_mae_relative_improvement,
        "mean_1d_bin_accuracy_improvement": improvements["mean_one_d_bin_accuracy_delta"]
        >= args.min_mean_one_d_accuracy_delta,
        "mean_pairwise_bin_accuracy_improvement": improvements["mean_pairwise_bin_accuracy_delta"]
        >= args.min_mean_pairwise_accuracy_delta,
        "four_d_bin_accuracy_not_regressed": improvements["four_d_bin_accuracy_delta"]
        >= -args.max_four_d_accuracy_regression,
        "per_feature_mae_regression_controlled": improvements["maximum_per_feature_range_normalized_mae_regression"]
        <= args.max_per_feature_mae_regression,
    }
    approved = all(approval_checks.values())
    deployment_mapping = (
        _fit_mapping(*_arrays(groups, group_keys), feature_columns, ranges) if approved else None
    )
    decision = APPROVED_DECISION if approved else "KEEP_RAW_PREDICTIONS"
    payload = _base_payload(args, feature_columns, ranges, sources, loaded, checks)
    payload.update(
        {
            "overall_status": "PASS",
            "decision": decision,
            "eligible_for_selector": approved,
            "split": {
                "policy": split_policy,
                "holdout_mode": args.holdout_mode,
                "latest_source_index": latest_source_index,
                "seed": args.seed,
                "train_geometry_count": len(train_keys),
                "holdout_geometry_count": len(holdout_keys),
                "train_group_sha256": _digest_lines(train_keys),
                "holdout_group_sha256": _digest_lines(sorted(holdout_keys)),
            },
            "evaluation_mapping": evaluation_mapping,
            "deployment_mapping": deployment_mapping,
            "holdout_metrics": {"raw": raw_metrics, "calibrated": calibrated_metrics},
            "improvements": improvements,
            "approval_checks": approval_checks,
            "thresholds": {
                "min_mae_relative_improvement": args.min_mae_relative_improvement,
                "min_mean_one_d_accuracy_delta": args.min_mean_one_d_accuracy_delta,
                "min_mean_pairwise_accuracy_delta": args.min_mean_pairwise_accuracy_delta,
                "max_four_d_accuracy_regression": args.max_four_d_accuracy_regression,
                "max_per_feature_mae_regression": args.max_per_feature_mae_regression,
            },
            "artifacts": {"summary": str(summary_path), "report": str(report_path), "figure": str(figure_path)},
            "scientific_boundary": (
                "The mapping may alter candidate ranking and predicted bin assignment only. "
                "It is not an EMX label, does not change the final uniformity gate, and must be refit after any port, process, frequency, geometry-range, or candidate-distribution change."
            ),
        }
    )
    _plot(figure_path, holdout_pred, holdout_real, holdout_calibrated, evaluation_mapping, feature_columns, ranges)
    _write_outputs(summary_path, report_path, payload)
    print("overall_status=PASS")
    print(f"decision={decision}")
    print(f"eligible_for_selector={str(approved).lower()}")
    print(f"summary={summary_path}")
    print(f"figure={figure_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-csv", action="append", required=True, help="Repeatable CSV with pred_* and real EMX features")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=",".join(DEFAULT_FEATURES))
    parser.add_argument("--pred-column-prefix", default="pred_")
    parser.add_argument("--feature-ranges-json", help="Optional JSON object mapping each feature to [min,max]")
    parser.add_argument("--feature-pairs", default="lp_nh_center:q_center,ls_nh_center:q_center")
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--holdout-fraction", type=float, default=0.25)
    parser.add_argument(
        "--holdout-mode",
        choices=("hash", "latest-source"),
        default="hash",
        help="Use a deterministic geometry hash split, or train on history and hold out the latest paired CSV source.",
    )
    parser.add_argument("--seed", type=int, default=2026071101)
    parser.add_argument("--min-independent-geometries", type=int, default=80)
    parser.add_argument("--min-holdout-geometries", type=int, default=20)
    parser.add_argument("--min-mae-relative-improvement", type=float, default=0.05)
    parser.add_argument("--min-mean-one-d-accuracy-delta", type=float, default=0.02)
    parser.add_argument("--min-mean-pairwise-accuracy-delta", type=float, default=0.01)
    parser.add_argument("--max-four-d-accuracy-regression", type=float, default=0.0)
    parser.add_argument("--max-per-feature-mae-regression", type=float, default=0.002)
    parser.add_argument("--require-touchstone", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.bins < 2:
        parser.error("--bins must be at least 2")
    if not 0.05 <= args.holdout_fraction <= 0.5:
        parser.error("--holdout-fraction must be between 0.05 and 0.5")
    return args


def _resolve_ranges(features: tuple[str, ...], path: str | None) -> dict[str, tuple[float, float]]:
    raw: dict[str, Any] = dict(DEFAULT_RANGES)
    if path:
        raw.update(json.loads(Path(path).expanduser().read_text(encoding="utf-8")))
    result = {}
    for feature in features:
        values = raw.get(feature)
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            continue
        lower, upper = float(values[0]), float(values[1])
        if math.isfinite(lower) and math.isfinite(upper) and upper > lower:
            result[feature] = (lower, upper)
    return result


def _load_pairs(
    sources: list[Path],
    features: tuple[str, ...],
    prefix: str,
    ranges: dict[str, tuple[float, float]],
    require_touchstone: bool,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[np.ndarray, np.ndarray]]] = defaultdict(list)
    stats = defaultdict(int)
    source_records = []
    if not all(feature in ranges for feature in features):
        return {
            "groups": {},
            "stats": {"unsupported_feature_range_count": sum(feature not in ranges for feature in features)},
            "sources": [{"path": str(source), "exists": source.is_file(), "sha256": _file_sha(source)} for source in sources],
        }
    for source_index, source in enumerate(sources):
        record = {"path": str(source), "exists": source.is_file(), "sha256": _file_sha(source)}
        source_records.append(record)
        if not source.is_file():
            continue
        with source.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            geometry_fields = sorted(
                field for field in fields if field.startswith("geom__") or field.startswith("candidate__geom__")
            )
            prediction_fields = _resolve_prediction_fields(fields, features, prefix)
            record["resolved_prediction_columns"] = prediction_fields
            real_contract = _real_feature_contract(fields, features)
            record["real_feature_contract"] = real_contract
            if not all(real_contract.values()) or len(prediction_fields) != len(features):
                stats["source_contract_failure_count"] += 1
                continue
            for row in reader:
                stats["row_count"] += 1
                if not _truthy(row.get("ok", "true")):
                    stats["not_ok_count"] += 1
                    continue
                real = _real_feature_vector(row, features)
                predicted = _float_vector(row, tuple(prediction_fields[feature] for feature in features))
                if real is None or predicted is None:
                    stats["nonfinite_count"] += 1
                    continue
                qp = _finite(row.get("qp_center"))
                qs = _finite(row.get("qs_center"))
                if "q_center" in features and qp is not None and qs is not None:
                    q_index = features.index("q_center")
                    if not math.isclose(real[q_index], min(qp, qs), rel_tol=1.0e-8, abs_tol=1.0e-8):
                        stats["q_consistency_failure_count"] += 1
                        continue
                if any(not ranges[feature][0] <= real[index] <= ranges[feature][1] for index, feature in enumerate(features)):
                    stats["real_out_of_range_count"] += 1
                    continue
                if require_touchstone and not _valid_touchstone(row, source.parent):
                    stats["touchstone_failure_count"] += 1
                    continue
                group_key = _geometry_group(row, geometry_fields)
                if group_key is None:
                    stats["missing_geometry_identity_count"] += 1
                    continue
                grouped[group_key].append((predicted, real, source_index))
                stats["accepted_pair_row_count"] += 1
    groups = {}
    duplicate_rows = 0
    for key, pairs in grouped.items():
        duplicate_rows += max(0, len(pairs) - 1)
        groups[key] = {
            "predicted": np.mean(np.vstack([item[0] for item in pairs]), axis=0),
            "real": np.mean(np.vstack([item[1] for item in pairs]), axis=0),
            "repeat_count": len(pairs),
            "source_indices": sorted({int(item[2]) for item in pairs}),
            "latest_source_index": max(int(item[2]) for item in pairs),
        }
    stats["duplicate_geometry_row_count"] = duplicate_rows
    stats["independent_geometry_count"] = len(groups)
    return {"groups": groups, "stats": dict(stats), "sources": source_records}


def _resolve_prediction_fields(
    fields: list[str],
    features: tuple[str, ...],
    prefix: str,
) -> dict[str, str]:
    available = set(fields)
    result = {}
    for feature in features:
        if prefix == "pred_":
            aliases = (
                f"raw_pred_{feature}",
                f"queue__raw_pred_{feature}",
                f"candidate__pred_{feature}",
                f"pred_{feature}",
                f"queue__pred_{feature}",
            )
        else:
            aliases = (f"{prefix}{feature}", f"queue__{prefix}{feature}")
        for alias in aliases:
            if alias in available:
                result[feature] = alias
                break
    return result


def _real_feature_contract(fields: list[str], features: tuple[str, ...]) -> dict[str, bool]:
    available = set(fields)
    contract = {}
    for feature in features:
        if feature == "q_center":
            contract[feature] = feature in available or {"qp_center", "qs_center"}.issubset(available)
        elif feature == "k_abs_center":
            contract[feature] = feature in available or "k_center" in available
        else:
            contract[feature] = feature in available
    return contract


def _real_feature_vector(row: dict[str, str], features: tuple[str, ...]) -> np.ndarray | None:
    values: list[float] = []
    for feature in features:
        value = _finite(row.get(feature))
        if value is None and feature == "q_center":
            qp = _finite(row.get("qp_center"))
            qs = _finite(row.get("qs_center"))
            if qp is not None and qs is not None:
                value = min(qp, qs)
        if value is None and feature == "k_abs_center":
            signed = _finite(row.get("k_center"))
            if signed is not None:
                value = abs(signed)
        if value is None:
            return None
        values.append(float(value))
    return np.asarray(values, dtype=float)


def _fit_mapping(
    predicted: np.ndarray,
    real: np.ndarray,
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
) -> dict[str, Any]:
    mapping = {}
    for index, feature in enumerate(features):
        fitted = fit_isotonic_mapping(predicted[:, index], real[:, index])
        lower, upper = ranges[feature]
        fitted["output_knots"] = [float(np.clip(value, lower, upper)) for value in fitted["output_knots"]]
        fitted.update({"method": "increasing_isotonic_pava", "range": [lower, upper], "fit_row_count": len(real)})
        mapping[feature] = fitted
    return mapping


def _metrics(
    predicted: np.ndarray,
    real: np.ndarray,
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
    bins: int,
    pair_spec: str,
) -> dict[str, Any]:
    spans = np.asarray([ranges[feature][1] - ranges[feature][0] for feature in features])
    error = np.abs(predicted - real) / spans[None, :]
    pred_bins = np.column_stack([_bin_indices(predicted[:, i], ranges[feature], bins) for i, feature in enumerate(features)])
    real_bins = np.column_stack([_bin_indices(real[:, i], ranges[feature], bins) for i, feature in enumerate(features)])
    one_d = {feature: float(np.mean(pred_bins[:, i] == real_bins[:, i])) for i, feature in enumerate(features)}
    pairs = _parse_pairs(pair_spec, features)
    pairwise = {}
    for left, right in pairs:
        li, ri = features.index(left), features.index(right)
        pairwise[f"{left}:{right}"] = float(
            np.mean((pred_bins[:, li] == real_bins[:, li]) & (pred_bins[:, ri] == real_bins[:, ri]))
        )
    return {
        "row_count": len(real),
        "range_normalized_mae": float(np.mean(error)),
        "per_feature_range_normalized_mae": {feature: float(np.mean(error[:, i])) for i, feature in enumerate(features)},
        "mean_one_d_bin_accuracy": float(np.mean(list(one_d.values()))),
        "per_feature_bin_accuracy": one_d,
        "mean_pairwise_bin_accuracy": float(np.mean(list(pairwise.values()))) if pairwise else 0.0,
        "pairwise_bin_accuracy": pairwise,
        "four_d_bin_accuracy": float(np.mean(np.all(pred_bins == real_bins, axis=1))),
        "bins_per_feature": bins,
    }


def _improvements(raw: dict[str, Any], calibrated: dict[str, Any], features: tuple[str, ...]) -> dict[str, Any]:
    raw_mae = float(raw["range_normalized_mae"])
    calibrated_mae = float(calibrated["range_normalized_mae"])
    regressions = {
        feature: float(calibrated["per_feature_range_normalized_mae"][feature])
        - float(raw["per_feature_range_normalized_mae"][feature])
        for feature in features
    }
    return {
        "range_normalized_mae_relative_improvement": (raw_mae - calibrated_mae) / max(raw_mae, 1.0e-12),
        "mean_one_d_bin_accuracy_delta": float(calibrated["mean_one_d_bin_accuracy"] - raw["mean_one_d_bin_accuracy"]),
        "mean_pairwise_bin_accuracy_delta": float(
            calibrated["mean_pairwise_bin_accuracy"] - raw["mean_pairwise_bin_accuracy"]
        ),
        "four_d_bin_accuracy_delta": float(calibrated["four_d_bin_accuracy"] - raw["four_d_bin_accuracy"]),
        "per_feature_range_normalized_mae_regression": regressions,
        "maximum_per_feature_range_normalized_mae_regression": max(regressions.values()),
    }


def _plot(
    path: Path,
    raw: np.ndarray,
    real: np.ndarray,
    calibrated: np.ndarray,
    mapping: dict[str, Any],
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 9), constrained_layout=True)
    fig.patch.set_facecolor("white")
    labels = {"lp_nh_center": "Lp (nH)", "ls_nh_center": "Ls (nH)", "q_center": "Q", "k_abs_center": "|K|"}
    for index, (axis, feature) in enumerate(zip(axes.flat, features)):
        lower, upper = ranges[feature]
        axis.set_facecolor("white")
        axis.scatter(raw[:, index], real[:, index], s=18, alpha=0.45, color="#7a7a7a", label="Raw proxy")
        axis.scatter(calibrated[:, index], real[:, index], s=18, alpha=0.55, color="#126f5b", label="Calibrated proxy")
        axis.plot([lower, upper], [lower, upper], color="#b33a3a", linewidth=1.2, linestyle="--", label="Ideal")
        knots = mapping[feature]
        axis.plot(knots["input_knots"], knots["output_knots"], color="#2459a6", linewidth=1.6, label="Train-only isotonic map")
        x_lower = min(lower, float(np.min(raw[:, index])), float(np.min(calibrated[:, index])), min(knots["input_knots"]))
        x_upper = max(upper, float(np.max(raw[:, index])), float(np.max(calibrated[:, index])), max(knots["input_knots"]))
        x_pad = max((x_upper - x_lower) * 0.025, 1.0e-9)
        axis.set_xlim(x_lower - x_pad, x_upper + x_pad)
        axis.set_ylim(lower, upper)
        axis.set_title(labels.get(feature, feature))
        axis.set_xlabel("Predicted")
        axis.set_ylabel("Real EMX")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8)
    fig.suptitle("Proxy-to-real physical-feature calibration on independent geometry holdout", fontsize=14)
    fig.savefig(path, dpi=200, facecolor="white", transparent=False)
    plt.close(fig)


def _base_payload(
    args: argparse.Namespace,
    features: tuple[str, ...],
    ranges: dict[str, tuple[float, float]],
    sources: list[Path],
    loaded: dict[str, Any],
    checks: dict[str, bool],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature_columns": list(features),
        "feature_ranges": {key: list(value) for key, value in ranges.items()},
        "source_csvs": loaded.get("sources") or [{"path": str(path)} for path in sources],
        "data_audit": loaded.get("stats") or {},
        "checks": checks,
        "arguments": vars(args),
    }


def _write_outputs(summary_path: Path, report_path: Path, payload: dict[str, Any]) -> None:
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    metrics = payload.get("holdout_metrics") or {}
    improvements = payload.get("improvements") or {}
    lines = [
        "# Proxy-to-real physical-feature calibration audit",
        "",
        f"- Overall status: **{payload.get('overall_status')}**",
        f"- Decision: **{payload.get('decision')}**",
        f"- Independent geometries: `{(payload.get('data_audit') or {}).get('independent_geometry_count', 0)}`",
        f"- Selector eligible: `{payload.get('eligible_for_selector')}`",
        "",
    ]
    if metrics:
        lines.extend(
            [
                "## Independent holdout",
                "",
                f"- Raw range-normalized MAE: `{metrics['raw']['range_normalized_mae']:.6g}`",
                f"- Calibrated range-normalized MAE: `{metrics['calibrated']['range_normalized_mae']:.6g}`",
                f"- Relative MAE improvement: `{improvements['range_normalized_mae_relative_improvement']:.3%}`",
                f"- Mean 1-D bin accuracy delta: `{improvements['mean_one_d_bin_accuracy_delta']:.3%}`",
                f"- Mean Lp-Q/Ls-Q bin accuracy delta: `{improvements['mean_pairwise_bin_accuracy_delta']:.3%}`",
                f"- Exact 4-D bin accuracy delta: `{improvements['four_d_bin_accuracy_delta']:.3%}`",
                "",
            ]
        )
    lines.extend(["## Boundary", "", payload.get("scientific_boundary") or "No calibration was approved.", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _arrays(groups: dict[str, Any], keys: list[str]) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.vstack([groups[key]["predicted"] for key in keys]).astype(float),
        np.vstack([groups[key]["real"] for key in keys]).astype(float),
    )


def _holdout_groups(keys: list[str], seed: int, fraction: float, minimum: int) -> set[str]:
    ordered = sorted(keys, key=lambda key: hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest())
    count = max(minimum, int(round(len(keys) * fraction)))
    count = min(max(1, count), max(1, len(keys) - 2))
    return set(ordered[:count])


def _bin_indices(values: np.ndarray, bounds: tuple[float, float], bins: int) -> np.ndarray:
    lower, upper = bounds
    scaled = (np.asarray(values, dtype=float) - lower) / (upper - lower)
    return np.clip(np.floor(scaled * bins).astype(int), 0, bins - 1)


def _parse_pairs(raw: str, features: tuple[str, ...]) -> list[tuple[str, str]]:
    result = []
    for token in raw.split(","):
        parts = tuple(item.strip() for item in token.split(":") if item.strip())
        if len(parts) == 2 and parts[0] in features and parts[1] in features and parts[0] != parts[1]:
            if parts not in result:
                result.append(parts)
    return result


def _geometry_group(row: dict[str, str], geometry_fields: list[str]) -> str | None:
    if geometry_fields:
        values = []
        for field in geometry_fields:
            value = _finite(row.get(field))
            if value is None:
                return None
            values.append(f"{value:.17g}")
        raw = "geometry|" + "|".join(values)
    else:
        candidate_id = str(row.get("candidate_id") or row.get("evaluation") or "").strip()
        if not candidate_id:
            return None
        raw = "candidate|" + candidate_id
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_touchstone(row: dict[str, str], parent: Path) -> bool:
    raw = str(row.get("touchstone_path") or row.get("s4p_path") or "").strip()
    if not raw:
        return False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (parent / path).resolve()
    return path.suffix.lower() == ".s4p" and path.is_file() and path.stat().st_size > 0


def _float_vector(row: dict[str, str], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    if any(value is None for value in values):
        return None
    return np.asarray(values, dtype=float)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "fail", "failed"}


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_lines(values: list[str]) -> str:
    return hashlib.sha256(("\n".join(values) + "\n").encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
