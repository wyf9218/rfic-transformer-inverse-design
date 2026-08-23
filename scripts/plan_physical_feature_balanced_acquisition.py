#!/usr/bin/env python3
"""Plan next acquisitions from sparse physical-feature bins.

This is the response-space replacement for the older Zin-balanced planner. It
reads completed rows with real EMX-derived labels, bins the selected physical
features, and recommends which sparse feature bins should be targeted next.

It does not invent labels or infer geometry by itself. The intended workflow is:

1. Generate/simulate a pilot batch.
2. Extract L/Q/K labels from real Touchstone files.
3. Use this plan to bias candidate generation toward under-filled feature bins.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_center"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _apply_target_envelope_config(args)
    if args.next_count is None:
        args.next_count = 100

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else dataset_dir / "physical_feature_balanced_acquisition_plan"
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_csv = dataset_dir / "dataset_rows.csv"
    rows = _read_rows(dataset_csv)
    ok_rows = [row for row in rows if _truthy(row.get("ok", "true"))]
    feature_columns = _feature_columns(args.feature_columns)
    labels = _collect_labels(ok_rows, feature_columns)
    envelope = _resolve_envelope(labels, args)
    bins = _build_bins(labels, envelope, args)
    targets = _select_targets(bins, args)
    checks = _build_checks(rows, ok_rows, labels, envelope, bins, targets, args)

    bins_csv = out_dir / "physical_feature_acquisition_bins.csv"
    targets_csv = out_dir / "physical_feature_acquisition_targets.csv"
    summary_path = out_dir / "physical_feature_acquisition_plan_summary.json"
    report_path = out_dir / "physical_feature_acquisition_plan_report.md"
    _write_csv(bins_csv, bins)
    _write_csv(targets_csv, targets)
    visual_evidence = _write_visual_evidence(labels, envelope, bins, out_dir, args)
    checks = checks + _visual_evidence_checks(visual_evidence, labels)
    overall_status = "NOT_READY" if labels["valid_count"] == 0 else ("FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "dataset_source": _dataset_source_summary(dataset_csv, row_count=len(rows), ok_count=len(ok_rows)),
        "out_dir": str(out_dir),
        "overall_status": overall_status,
        "plan_status": _plan_status(labels, bins, targets),
        "feature_columns": feature_columns,
        "rows": {"row_count": len(rows), "ok_count": len(ok_rows)},
        "label_summary": _label_summary(labels),
        "target_envelope_config": getattr(args, "_target_envelope_config_summary", {"configured": False}),
        "planning_envelope": envelope,
        "acquisition_allocation_policy": {
            "name": "deficit_first_then_low_count_topup",
            "description": (
                "Allocate the next acquisition budget to under-filled physical-feature bins. "
                "First cover formal per-bin deficits, then keep using any remaining budget on "
                "the lowest projected-count sparse bins so large EMX queues are not truncated "
                "when target_count_per_bin is only a coverage floor."
            ),
        },
        "bin_summary": _bin_summary(bins),
        "visual_evidence": visual_evidence,
        "target_summary": _target_summary(targets),
        "bins_csv": str(bins_csv),
        "targets_csv": str(targets_csv),
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This script plans physical-feature target bins from existing simulator labels only.",
            "It does not redefine the grounded center-tap reduction; use it only after the formal 4-port S4P extraction contract is fixed.",
            "The default Q representation is the scalar q_center used by the physical-feature inverse model; explicit Qp/Qs columns can still be passed through --feature-columns for diagnostic studies.",
            "A PASS here means the next acquisition target bins are traceable; final acceptance still requires EMX/HFSS correlation.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"plan_status={summary['plan_status']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"bins_csv={bins_csv}")
    print(f"targets_csv={targets_csv}")
    for check in checks:
        print(f"{check['status']:9s} {check['name']}: {check['detail']}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--target-envelope-config", help="JSON file containing physical feature envelope bounds")
    parser.add_argument("--target-count-per-bin", type=int)
    parser.add_argument("--desired-total-count", type=int, help="Desired total count across all feature bins")
    parser.add_argument("--next-count", type=int, help="Recommended new samples allocated across sparse bins")
    parser.add_argument("--max-target-bins", type=int)
    parser.add_argument("--max-bin-count", type=int, default=20000)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _apply_target_envelope_config(args: argparse.Namespace) -> None:
    path_raw = getattr(args, "target_envelope_config", None)
    if not path_raw:
        args._target_envelope_config_summary = {"configured": False, "status": "NOT_CONFIGURED"}
        return
    path = Path(path_raw).expanduser().resolve()
    summary: dict[str, Any] = {"configured": True, "path": str(path)}
    if not path.is_file():
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"missing config file: {path}"}
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - exact config parser detail is useful here.
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        return
    if not isinstance(data, dict):
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": f"top-level JSON is {type(data).__name__}"}
        return
    if "TEMPLATE_ONLY" in str(data.get("status", "")).upper():
        args._target_envelope_config_summary = {
            **summary,
            "status": "FAIL",
            "error": "physical feature envelope config is marked TEMPLATE_ONLY; fill a project-specific copy before using it",
        }
        return
    envelope = data.get("physical_feature_target_envelope", data)
    if not isinstance(envelope, dict):
        args._target_envelope_config_summary = {**summary, "status": "FAIL", "error": "physical_feature_target_envelope is not an object"}
        return

    applied: dict[str, Any] = {}
    if args.feature_columns == DEFAULT_FEATURE_COLUMNS and isinstance(envelope.get("feature_columns"), list):
        args.feature_columns = ",".join(str(item) for item in envelope["feature_columns"])
        applied["feature_columns"] = args.feature_columns
    for source_key, arg_name in (
        ("target_count_per_bin", "target_count_per_bin"),
        ("desired_total_count", "desired_total_count"),
        ("next_count", "next_count"),
        ("bins", "bins"),
        ("max_bin_count", "max_bin_count"),
    ):
        if source_key not in envelope or envelope[source_key] is None or getattr(args, arg_name) is not None and arg_name not in {"bins", "max_bin_count"}:
            continue
        try:
            value = int(envelope[source_key])
        except (TypeError, ValueError):
            args._target_envelope_config_summary = {
                **summary,
                "status": "FAIL",
                "error": f"invalid integer field {source_key}={envelope[source_key]!r}",
            }
            return
        setattr(args, arg_name, value)
        applied[arg_name] = value
    args._target_envelope_config_summary = {
        **summary,
        "status": "PASS",
        "schema": data.get("schema", "direct_or_physical_feature_target_envelope"),
        "name": data.get("name") or envelope.get("name"),
        "applied_fields": applied,
        "has_feature_bounds": isinstance(envelope.get("features"), dict),
        "notes": data.get("notes", []),
    }
    args._target_envelope_data = envelope


def _feature_columns(text: str) -> list[str]:
    columns = [item.strip() for item in str(text).split(",") if item.strip()]
    if not columns:
        raise ValueError("--feature-columns must contain at least one column")
    return columns


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _collect_labels(rows: list[dict[str, str]], feature_columns: list[str]) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    missing_counts = {column: 0 for column in feature_columns}
    for idx, row in enumerate(rows):
        values: list[float] = []
        missing = False
        for column in feature_columns:
            value = _feature_value(row, column)
            if value is None:
                missing_counts[column] += 1
                missing = True
                break
            values.append(value)
        if missing:
            continue
        points.append(
            {
                "row_index": idx,
                "evaluation": row.get("evaluation") or row.get("sample_id") or row.get("cache_key") or "",
                "values": values,
            }
        )
    matrix = np.asarray([point["values"] for point in points], dtype=float)
    if matrix.size == 0:
        matrix = np.empty((0, len(feature_columns)), dtype=float)
    return {
        "points": points,
        "valid_count": len(points),
        "feature_columns": feature_columns,
        "values": matrix,
        "missing_counts": missing_counts,
        "derived_feature_rules": _derived_feature_rules(feature_columns),
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


def _derived_feature_rules(feature_columns: list[str]) -> dict[str, str]:
    rules = {}
    for column in feature_columns:
        if str(column).strip().lower() in {"q", "q_center", "q_min", "q_scalar"}:
            rules[column] = "direct q column if present; otherwise min(qp_center, qs_center) from diagnostic Qp/Qs columns"
        if str(column).strip().lower() in {"k_abs", "k_abs_center", "abs_k", "abs_k_center", "k_magnitude", "k_magnitude_center"}:
            rules[column] = "direct |K| column if present; otherwise abs(k_center) from signed coupling"
    return rules


def _resolve_envelope(labels: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    feature_columns = list(labels["feature_columns"])
    config = getattr(args, "_target_envelope_data", {})
    configured_features = config.get("features") if isinstance(config, dict) else None
    bounds: dict[str, dict[str, float | None]] = {}
    source = "configured_feature_bounds"
    status = "PASS"
    error = None
    for idx, column in enumerate(feature_columns):
        configured = configured_features.get(column) if isinstance(configured_features, dict) else None
        if isinstance(configured, dict) and configured.get("min") is not None and configured.get("max") is not None:
            lo = _as_float(configured.get("min"))
            hi = _as_float(configured.get("max"))
        elif labels["valid_count"]:
            lo, hi = _padded_min_max(labels["values"][:, idx])
            source = "observed_label_range_with_5pct_padding"
        else:
            lo = hi = None
            source = "unavailable_no_labels"
        if lo is None or hi is None:
            status = "NOT_READY"
            error = f"no valid bounds for {column}"
        elif hi <= lo:
            status = "FAIL"
            error = f"feature bound max must be greater than min for {column}"
        bounds[column] = {"min": lo, "max": hi}
    bin_count = int(args.bins)
    total_bins = int(bin_count ** max(0, len(feature_columns)))
    if total_bins > int(args.max_bin_count):
        status = "FAIL"
        error = f"bin grid too large: {total_bins} bins > max_bin_count {int(args.max_bin_count)}"
    return {
        "status": status,
        "source": source,
        "error": error,
        "feature_bounds": bounds,
        "bins_per_feature": bin_count,
        "total_bin_count": total_bins,
        "target_count_per_bin": _target_count_per_bin(args, total_bins),
        "desired_total_count": None if args.desired_total_count is None else int(args.desired_total_count),
        "next_count": None if args.next_count is None else int(args.next_count),
    }


def _target_count_per_bin(args: argparse.Namespace, total_bins: int) -> int:
    if args.target_count_per_bin is not None:
        return max(0, int(args.target_count_per_bin))
    if args.desired_total_count is not None:
        return int(math.ceil(max(0, int(args.desired_total_count)) / float(max(1, total_bins))))
    return 1


def _build_bins(labels: dict[str, Any], envelope: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if labels["valid_count"] == 0 or envelope["status"] != "PASS":
        return []
    columns = list(labels["feature_columns"])
    bounds = envelope["feature_bounds"]
    ranges = [(float(bounds[column]["min"]), float(bounds[column]["max"])) for column in columns]
    bin_count = int(envelope["bins_per_feature"])
    hist, edges = np.histogramdd(labels["values"], bins=[bin_count] * len(columns), range=ranges)
    target_count = int(envelope["target_count_per_bin"])
    rows: list[dict[str, Any]] = []
    for index in product(*(range(bin_count) for _ in columns)):
        count = int(hist[index])
        deficit = max(0, target_count - count)
        row: dict[str, Any] = {
            "bin_key": "|".join(str(item) for item in index),
            "current_count": count,
            "target_count": target_count,
            "deficit": deficit,
            "status": "underfilled" if deficit > 0 else "covered",
        }
        for axis, column in enumerate(columns):
            lo = float(edges[axis][index[axis]])
            hi = float(edges[axis][index[axis] + 1])
            row[f"{column}__bin"] = int(index[axis])
            row[f"{column}__min"] = lo
            row[f"{column}__max"] = hi
            row[f"{column}__target"] = 0.5 * (lo + hi)
        rows.append(row)
    return rows


def _select_targets(bins: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    sparse = [row for row in bins if int(row["deficit"]) > 0]
    sparse.sort(key=lambda row: (-int(row["deficit"]), int(row["current_count"]), str(row["bin_key"])))
    if args.max_target_bins is not None:
        sparse = sparse[: max(0, int(args.max_target_bins))]
    allocations = _allocate_new_samples_across_sparse_bins(sparse, args.next_count)
    targets: list[dict[str, Any]] = []
    for rank, (row, recommended) in enumerate(zip(sparse, allocations), start=1):
        if recommended <= 0:
            continue
        target = dict(row)
        target["rank"] = rank
        target["recommended_new_samples"] = int(recommended)
        target["priority_weight"] = float(int(row["deficit"]) / max(1, int(row["target_count"])))
        targets.append(target)
    return targets


def _allocate_new_samples_across_sparse_bins(sparse: list[dict[str, Any]], next_count: int | None) -> list[int]:
    if not sparse:
        return []
    deficits = [max(0, int(row["deficit"])) for row in sparse]
    current_counts = [max(0, int(row["current_count"])) for row in sparse]
    if next_count is None:
        return deficits
    remaining = max(0, int(next_count))
    allocations = [0 for _ in sparse]
    active = [idx for idx, deficit in enumerate(deficits) if deficit > 0]
    if remaining <= 0 or not active:
        return allocations

    # Phase 1: close formal deficits broadly across sparse bins. This preserves
    # the old "fill under-covered bins first" behavior.
    deficit_budget = min(remaining, sum(deficits))
    if deficit_budget > 0:
        base = deficit_budget // len(active)
        extra = deficit_budget % len(active)
        for order, idx in enumerate(active):
            requested = base + (1 if order < extra else 0)
            assigned = min(deficits[idx], requested)
            allocations[idx] += assigned
            remaining -= assigned
        cursor = 0
        while remaining > 0 and any(allocations[idx] < deficits[idx] for idx in active):
            refillable = [idx for idx in active if allocations[idx] < deficits[idx]]
            idx = refillable[cursor % len(refillable)]
            allocations[idx] += 1
            remaining -= 1
            cursor += 1

    # Phase 2: if the requested acquisition queue is larger than the formal
    # deficit sum, keep targeting sparse bins instead of truncating the queue.
    # This matters for production where target_count_per_bin may be a coverage
    # floor (for example 1/sample-bin) while each EMX acquisition round still
    # needs thousands of candidates to make progress.
    while remaining > 0:
        idx = min(
            active,
            key=lambda item: (
                current_counts[item] + allocations[item],
                allocations[item],
                str(sparse[item].get("bin_key", "")),
            ),
        )
        allocations[idx] += 1
        remaining -= 1
    return allocations


def _build_checks(
    rows: list[dict[str, str]],
    ok_rows: list[dict[str, str]],
    labels: dict[str, Any],
    envelope: dict[str, Any],
    bins: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    checks = [
        _check("PASS" if rows else "FAIL", "dataset rows", f"rows={len(rows)}, ok_rows={len(ok_rows)}"),
        _check("PASS" if labels["valid_count"] else "NOT_READY", "physical feature labels", f"valid={labels['valid_count']}"),
        _check(str(envelope["status"]), "planning envelope", envelope.get("error") or envelope.get("source", "")),
    ]
    config = getattr(args, "_target_envelope_config_summary", {"configured": False})
    if config.get("configured"):
        checks.append(_check(str(config.get("status", "FAIL")), "target envelope config", config.get("error", f"applied={config.get('applied_fields', {})}")))
    missing = {key: value for key, value in labels["missing_counts"].items() if value}
    if missing:
        checks.append(_check("WARN", "missing feature labels", str(missing)))
    if labels["valid_count"]:
        checks.append(_check("PASS" if bins else "FAIL", "feature bins", f"bins={len(bins)}"))
        checks.append(_check("PASS" if targets else "WARN", "recommended sparse feature bins", f"target_bins={len(targets)}"))
    return checks


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _plan_status(labels: dict[str, Any], bins: list[dict[str, Any]], targets: list[dict[str, Any]]) -> str:
    if labels["valid_count"] == 0:
        return "NOT_READY"
    if not bins:
        return "NO_BINS"
    if not targets:
        return "FEATURE_BINS_ALREADY_COVERED"
    return "SPARSE_FEATURE_BINS_PRIORITIZED"


def _label_summary(labels: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "valid_count": int(labels["valid_count"]),
        "missing_counts": labels["missing_counts"],
        "derived_feature_rules": labels.get("derived_feature_rules", {}),
        "features": {},
    }
    values = labels["values"]
    for idx, column in enumerate(labels["feature_columns"]):
        summary["features"][column] = _range_summary(values[:, idx] if values.size else np.asarray([], dtype=float))
    return summary


def _bin_summary(bins: list[dict[str, Any]]) -> dict[str, Any]:
    if not bins:
        return {"bin_count": 0}
    counts = np.asarray([int(row["current_count"]) for row in bins], dtype=float)
    deficits = np.asarray([int(row["deficit"]) for row in bins], dtype=float)
    targets = np.asarray([int(row["target_count"]) for row in bins], dtype=float)
    occupied = counts[counts > 0]
    probabilities = occupied / float(np.sum(occupied)) if occupied.size and np.sum(occupied) > 0 else np.asarray([], dtype=float)
    max_entropy = math.log(len(counts)) if len(counts) > 1 else 0.0
    entropy = float(-np.sum(probabilities * np.log(probabilities))) if probabilities.size else 0.0
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else None
    mean_count = float(np.mean(counts)) if counts.size else 0.0
    return {
        "bin_count": len(bins),
        "covered_bins": int(np.sum(counts >= targets)),
        "underfilled_bins": int(np.sum(deficits > 0)),
        "empty_bins": int(np.sum(counts == 0)),
        "empty_bin_fraction": float(np.mean(counts == 0)),
        "max_count": int(np.max(counts)),
        "min_count": int(np.min(counts)),
        "mean_count": mean_count,
        "count_cv": None if mean_count <= 0.0 else float(np.std(counts) / mean_count),
        "normalized_entropy": normalized_entropy,
        "max_deficit": int(np.max(deficits)),
        "total_deficit": int(np.sum(deficits)),
    }


def _target_summary(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "target_bin_count": len(targets),
        "recommended_new_sample_count": int(sum(int(row["recommended_new_samples"]) for row in targets)),
    }


def _write_visual_evidence(
    labels: dict[str, Any],
    envelope: dict[str, Any],
    bins: list[dict[str, Any]],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    paths = {
        "marginal_histograms": out_dir / "physical_feature_marginal_histograms.png",
        "pairwise_scatter": out_dir / "physical_feature_pairwise_scatter.png",
        "bin_coverage_heatmap": out_dir / "physical_feature_bin_coverage_heatmap.png",
    }
    if labels["valid_count"] == 0 or envelope.get("status") != "PASS":
        return {
            "status": "NOT_READY",
            "reason": "no valid physical-feature labels or planning envelope",
            "figures": {key: str(path) for key, path in paths.items()},
        }
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001 - visual evidence is optional until deps are installed.
        return {
            "status": "FAIL",
            "reason": f"matplotlib unavailable: {type(exc).__name__}: {exc}",
            "figures": {key: str(path) for key, path in paths.items()},
        }

    values = np.asarray(labels["values"], dtype=float)
    columns = list(labels["feature_columns"])
    bounds = envelope.get("feature_bounds") or {}
    figure_status = {
        "marginal_histograms": _plot_feature_marginals(plt, values, columns, bounds, paths["marginal_histograms"], int(args.bins)),
        "pairwise_scatter": _plot_feature_pairwise_scatter(plt, values, columns, paths["pairwise_scatter"]),
        "bin_coverage_heatmap": _plot_bin_coverage_heatmap(plt, bins, columns, envelope, paths["bin_coverage_heatmap"]),
    }
    return {
        "status": "PASS" if all(figure_status.values()) else "FAIL",
        "figure_status": figure_status,
        "figures": {key: str(path) for key, path in paths.items()},
        "method": {
            "marginal_histograms": "per-feature observed labels compared with the planning envelope",
            "pairwise_scatter": "pairwise Lp/Ls/Q/K response-space coverage",
            "bin_coverage_heatmap": "multi-dimensional feature bins flattened as first-half axes vs second-half axes",
        },
    }


def _visual_evidence_checks(visual_evidence: dict[str, Any], labels: dict[str, Any]) -> list[dict[str, str]]:
    if labels["valid_count"] == 0:
        return [_check("NOT_READY", "physical feature visual evidence", visual_evidence.get("reason", ""))]
    checks = [_check(str(visual_evidence.get("status", "FAIL")), "physical feature visual evidence", visual_evidence.get("reason", "figures generated"))]
    for name, raw_path in (visual_evidence.get("figures") or {}).items():
        path = Path(str(raw_path))
        checks.append(_check("PASS" if path.is_file() else "FAIL", f"visual figure exists: {name}", str(path)))
    return checks


def _plot_feature_marginals(plt: Any, values: np.ndarray, columns: list[str], bounds: dict[str, Any], path: Path, bins: int) -> bool:
    if values.size == 0:
        return False
    ncols = min(2, max(1, len(columns)))
    nrows = int(math.ceil(len(columns) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(6.2 * ncols, 3.4 * nrows), squeeze=False)
    for idx, column in enumerate(columns):
        ax = axes.flat[idx]
        arr = values[:, idx]
        bound = bounds.get(column) if isinstance(bounds, dict) else None
        lo = _as_float(bound.get("min")) if isinstance(bound, dict) else None
        hi = _as_float(bound.get("max")) if isinstance(bound, dict) else None
        hist_range = (float(lo), float(hi)) if lo is not None and hi is not None and hi > lo else None
        ax.hist(arr, bins=max(2, int(bins)), range=hist_range, color="#2563eb", alpha=0.76, edgecolor="white")
        if hist_range is not None:
            ax.axvline(hist_range[0], color="#111827", linewidth=1.0, linestyle=":")
            ax.axvline(hist_range[1], color="#111827", linewidth=1.0, linestyle=":")
        ax.set_title(column)
        ax.set_xlabel("EMX-derived label value")
        ax.set_ylabel("count")
        ax.grid(True, alpha=0.22)
    for ax in axes.flat[len(columns) :]:
        ax.axis("off")
    fig.suptitle("Physical Feature Marginal Coverage", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path.is_file()


def _plot_feature_pairwise_scatter(plt: Any, values: np.ndarray, columns: list[str], path: Path) -> bool:
    if values.size == 0 or len(columns) < 2:
        return False
    n = len(columns)
    fig, axes = plt.subplots(n, n, figsize=(2.45 * n, 2.45 * n), squeeze=False)
    for row, ycol in enumerate(columns):
        for col, xcol in enumerate(columns):
            ax = axes[row, col]
            if row == col:
                ax.hist(values[:, col], bins=12, color="#60a5fa", edgecolor="white")
            else:
                ax.scatter(values[:, col], values[:, row], s=16, alpha=0.62, color="#0f766e", linewidths=0)
            if row == n - 1:
                ax.set_xlabel(xcol, fontsize=7)
            else:
                ax.set_xticklabels([])
            if col == 0:
                ax.set_ylabel(ycol, fontsize=7)
            else:
                ax.set_yticklabels([])
            ax.grid(True, alpha=0.18)
    fig.suptitle("Physical Feature Pairwise Coverage", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path.is_file()


def _plot_bin_coverage_heatmap(
    plt: Any,
    bins: list[dict[str, Any]],
    columns: list[str],
    envelope: dict[str, Any],
    path: Path,
) -> bool:
    if not bins:
        return False
    bin_count = int(envelope.get("bins_per_feature") or 0)
    if bin_count <= 0:
        return False
    split = max(1, len(columns) // 2)
    left_dims = split
    right_dims = len(columns) - split
    left_size = int(bin_count**left_dims)
    right_size = int(bin_count**max(1, right_dims))
    matrix = np.zeros((left_size, right_size), dtype=float)
    for row in bins:
        key = [int(item) for item in str(row.get("bin_key", "")).split("|") if str(item).strip()]
        if len(key) != len(columns):
            continue
        left_index = _flatten_bin_index(key[:split], bin_count)
        right_key = key[split:] if key[split:] else [0]
        right_index = _flatten_bin_index(right_key, bin_count)
        matrix[left_index, right_index] = int(row.get("current_count") or 0)
    fig, ax = plt.subplots(figsize=(max(7.0, 0.35 * right_size + 2.0), max(4.6, 0.35 * left_size + 1.6)))
    image = ax.imshow(matrix, aspect="auto", cmap="viridis")
    ax.set_title("Physical Feature Bin Coverage")
    ax.set_xlabel("flattened bins: " + ", ".join(columns[split:] or ["constant"]))
    ax.set_ylabel("flattened bins: " + ", ".join(columns[:split]))
    fig.colorbar(image, ax=ax, label="current sample count")
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path.is_file()


def _flatten_bin_index(items: list[int], bin_count: int) -> int:
    out = 0
    for item in items:
        out = out * int(bin_count) + int(item)
    return out


def _dataset_source_summary(path: Path, *, row_count: int, ok_count: int) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(path), "exists": path.exists(), "csv_row_count": int(row_count), "ok_row_count": int(ok_count)}
    if not path.exists():
        return summary
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    summary.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    return summary


def _padded_min_max(arr: np.ndarray) -> tuple[float, float]:
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    pad = 0.05 * max(hi - lo, 1.0)
    return lo - pad, hi + pad


def _range_summary(arr: np.ndarray) -> dict[str, float | None]:
    if arr.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {"min": float(np.min(arr)), "max": float(np.max(arr)), "mean": float(np.mean(arr)), "std": float(np.std(arr))}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical Feature Balanced Acquisition Plan",
        "",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Dataset: `{summary['dataset_dir']}`",
        f"- Dataset CSV SHA256: `{summary['dataset_source'].get('sha256', 'missing')}`",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Plan status: **{summary['plan_status']}**",
        f"- Feature columns: `{', '.join(summary['feature_columns'])}`",
        f"- Valid labels: `{summary['label_summary']['valid_count']}`",
        f"- Target bins: `{summary['target_summary']['target_bin_count']}`",
        f"- Recommended new samples: `{summary['target_summary']['recommended_new_sample_count']}`",
        f"- Targets CSV: `{summary['targets_csv']}`",
        f"- Visual evidence status: **{summary.get('visual_evidence', {}).get('status', 'UNKNOWN')}**",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(["", "## Feature Bin Coverage", ""])
    bin_summary = summary.get("bin_summary") or {}
    for key in (
        "bin_count",
        "covered_bins",
        "underfilled_bins",
        "empty_bins",
        "empty_bin_fraction",
        "count_cv",
        "normalized_entropy",
        "total_deficit",
    ):
        if key in bin_summary:
            lines.append(f"- `{key}`: `{bin_summary[key]}`")
    lines.extend(["", "## Visual Evidence", ""])
    figures = (summary.get("visual_evidence") or {}).get("figures") or {}
    if figures:
        for name, path in figures.items():
            lines.append(f"- `{name}`: `{path}`")
    else:
        lines.append("- No visual evidence generated yet.")
    lines.extend(["", "## Method", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


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


if __name__ == "__main__":
    raise SystemExit(main())
