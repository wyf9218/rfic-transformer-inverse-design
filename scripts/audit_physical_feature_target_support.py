#!/usr/bin/env python3
"""Audit whether requested Lp/Ls/Q/|K| targets have real-data support.

The gate combines exact 4-D cell occupancy with a declared-range-normalized
nearest-neighbor score.  A deterministic reference/calibration split selects a
distance threshold from real accepted rows only.  This is an empirical support
diagnostic, not a surrogate-error interval, an inverse-model accuracy claim, or
proof of physical feasibility.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
DEFAULT_LOWER = (0.5, 0.5, 5.0, 0.0)
DEFAULT_UPPER = (3.0, 3.0, 25.0, 0.8)
ID_CANDIDATES = ("touchstone_sha256", "evaluation", "queue__candidate_id")

SUPPORTED = "OCCUPIED_CELL_WITHIN_EMPIRICAL_DISTANCE"
DISTANCE_OOD = "OCCUPIED_CELL_DISTANCE_OOD"
EMPTY_PAIRWISE_SUPPORTED = "EMPTY_4D_CELL_PAIRWISE_SUPPORTED"
EMPTY_PAIRWISE_UNSUPPORTED = "EMPTY_4D_CELL_PAIRWISE_UNSUPPORTED"
OUT_OF_RANGE = "OUT_OF_DECLARED_RANGE"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    target_csv = Path(args.target_csv).expanduser().resolve() if args.target_csv else None
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / "physical_feature_target_support_summary.json",
        "targets": out_dir / "physical_feature_target_support_targets.csv",
        "report": out_dir / "physical_feature_target_support_report.md",
        "figure": out_dir / "physical_feature_target_support.png",
    }
    requested = _split_csv(args.feature_columns)
    lower = np.asarray(_split_floats(args.feature_lower), dtype=float)
    upper = np.asarray(_split_floats(args.feature_upper), dtype=float)
    matrix, row_ids, input_stats = _read_training(
        training_csv, requested, args.id_column, lower, upper
    )
    targets, target_stats = _read_targets(target_csv, requested, lower, upper, int(args.bins))
    duplicate_ids = len(row_ids) - len(set(row_ids))
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "target_csv_exists_or_grid_requested": target_csv is None or target_csv.is_file(),
        "exact_four_feature_contract": tuple(requested) == FEATURES,
        "finite_ordered_explicit_ranges": bool(
            len(lower) == 4
            and len(upper) == 4
            and np.all(np.isfinite(lower))
            and np.all(np.isfinite(upper))
            and np.all(upper > lower)
        ),
        "all_training_rows_valid_and_in_range": int(input_stats["rejected_row_count"]) == 0,
        "stable_training_ids_present": bool(input_stats["resolved_id_columns"]),
        "stable_training_ids_unique": duplicate_ids == 0,
        "minimum_training_rows": len(matrix) >= int(args.min_training_rows),
        "targets_present": bool(targets),
        "target_rows_parse": int(target_stats["invalid_target_rows"]) == 0,
    }

    records: list[dict[str, Any]] = []
    analysis: dict[str, Any] = {}
    plot_status = "NOT_ATTEMPTED_INPUT_FAILURE"
    if all(checks.values()):
        reference, calibration = _split_indices(
            row_ids, float(args.calibration_fraction), int(args.split_seed)
        )
        checks["minimum_reference_rows"] = len(reference) >= int(args.min_reference_rows)
        checks["minimum_calibration_rows"] = len(calibration) >= int(args.min_calibration_rows)
        if checks["minimum_reference_rows"] and checks["minimum_calibration_rows"]:
            records, analysis = _audit(
                matrix,
                reference,
                calibration,
                targets,
                lower,
                upper,
                int(args.bins),
                float(args.alpha),
            )
            _write_records(paths["targets"], records)
            plot_status = "SKIPPED_BY_REQUEST" if args.no_plots else _write_plot(
                paths["figure"], records, int(args.bins), target_csv is None
            )
            checks["target_records_written"] = paths["targets"].is_file()
            checks["plot_written_or_explicitly_skipped"] = (
                plot_status
                in {
                    "PASS",
                    "SKIPPED_BY_REQUEST",
                    "SKIPPED_TARGET_CSV_NOT_REGULAR_GRID",
                }
            )
        else:
            checks["target_records_written"] = False
            checks["plot_written_or_explicitly_skipped"] = False
    else:
        checks["minimum_reference_rows"] = False
        checks["minimum_calibration_rows"] = False
        checks["target_records_written"] = False
        checks["plot_written_or_explicitly_skipped"] = False

    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "USE_AS_PRE_INFERENCE_SUPPORT_GATE_REAL_EM_CLOSURE_STILL_REQUIRED"
            if status == "PASS"
            else "FIX_SUPPORT_GATE_INPUTS_BEFORE_INFERENCE"
        ),
        "training_csv": _file_record(training_csv),
        "target_source": (
            _file_record(target_csv) if target_csv is not None else {"mode": "all_4d_grid_centers"}
        ),
        "requested_feature_columns": requested,
        "feature_lower": lower.tolist(),
        "feature_upper": upper.tolist(),
        "bins_per_feature": int(args.bins),
        "input_stats": {**input_stats, "duplicate_id_count": int(duplicate_ids)},
        "target_stats": target_stats,
        "analysis": analysis,
        "checks": checks,
        "status_actions": {
            SUPPORTED: "Candidate generation may proceed, but DRC and real EM closure remain mandatory.",
            DISTANCE_OOD: "Do not trust inverse output without targeted real EM evidence.",
            EMPTY_PAIRWISE_SUPPORTED: "Exact 4-D target has no real label; pair projections do not prove feasibility.",
            EMPTY_PAIRWISE_UNSUPPORTED: "Exact and lower-order support are incomplete; require targeted real EM evidence.",
            OUT_OF_RANGE: "Reject as outside the declared project target envelope.",
        },
        "scientific_boundary": (
            "The calibrated score is nearest-neighbor support distance, not inverse-model error. Its split-quantile "
            "interpretation requires exchangeable accepted rows and does not extend to deliberately OOD targets. "
            "An occupied cell and short distance do not prove physical feasibility or model accuracy. Empty-cell and "
            "distance-OOD flags do not prove physical impossibility. Every generated design still requires geometry, "
            "DRC, port, and returned real EM validation."
        ),
        "production_contract": {
            "production_runtime_modified": False,
            "training_labels_modified": False,
            "uniformity_denominator_modified": False,
        },
        "outputs": {
            "targets": _file_record(paths["targets"]),
            "figure": _file_record(paths["figure"]),
            "figure_status": plot_status,
        },
        "arguments": vars(args),
    }
    paths["summary"].write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    paths["report"].write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"summary={paths['summary']}")
    print(f"targets={paths['targets']}")
    print(f"figure_status={plot_status}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--target-csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=",".join(FEATURES))
    parser.add_argument("--feature-lower", default=",".join(str(value) for value in DEFAULT_LOWER))
    parser.add_argument("--feature-upper", default=",".join(str(value) for value in DEFAULT_UPPER))
    parser.add_argument("--id-column")
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--calibration-fraction", type=float, default=0.20)
    parser.add_argument("--alpha", type=float, default=0.01)
    parser.add_argument("--split-seed", type=int, default=31051992)
    parser.add_argument("--min-training-rows", type=int, default=1000)
    parser.add_argument("--min-reference-rows", type=int, default=500)
    parser.add_argument("--min-calibration-rows", type=int, default=200)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.bins < 2:
        parser.error("--bins must be at least 2")
    if not 0.0 < args.calibration_fraction < 1.0:
        parser.error("--calibration-fraction must be in (0,1)")
    if not 0.0 < args.alpha < 1.0:
        parser.error("--alpha must be in (0,1)")
    if len(_split_csv(args.feature_columns)) != 4:
        parser.error("exactly four feature columns are required")
    if len(_split_floats(args.feature_lower)) != 4 or len(_split_floats(args.feature_upper)) != 4:
        parser.error("feature lower/upper must each contain four values")
    return args


def _read_training(
    path: Path,
    requested: list[str],
    explicit_id: str | None,
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    values: list[list[float]] = []
    row_ids: list[str] = []
    stats: Counter[str] = Counter()
    resolved: list[str] = []
    resolved_ids: list[str] = []
    if not path.is_file():
        return np.empty((0, 4), dtype=float), [], _input_stats(
            stats, resolved, resolved_ids
        )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        for name in requested:
            candidates = (name, f"input__{name}", f"phys__{name}")
            resolved.append(next((candidate for candidate in candidates if candidate in fields), ""))
        id_candidates = (explicit_id,) if explicit_id else ID_CANDIDATES
        resolved_ids = [name for name in id_candidates if name and name in fields]
        for row in reader:
            stats["input_row_count"] += 1
            if not all(resolved):
                stats["missing_feature_column_rows"] += 1
                continue
            row_id_column = next(
                (name for name in resolved_ids if str(row.get(name) or "").strip()),
                "",
            )
            if not row_id_column:
                stats["missing_id_rows"] += 1
                continue
            try:
                point = np.asarray([float(row[column]) for column in resolved], dtype=float)
            except (KeyError, TypeError, ValueError):
                stats["non_numeric_rows"] += 1
                continue
            if not np.all(np.isfinite(point)):
                stats["non_finite_rows"] += 1
                continue
            if np.any(point < lower) or np.any(point > upper):
                stats["out_of_range_rows"] += 1
                continue
            values.append(point.tolist())
            row_ids.append(str(row[row_id_column]).strip())
            stats[f"id_source__{row_id_column}"] += 1
            stats["valid_in_range_row_count"] += 1
    return np.asarray(values, dtype=float).reshape(-1, 4), row_ids, _input_stats(
        stats, resolved, resolved_ids
    )


def _input_stats(
    stats: Counter[str], resolved: list[str], resolved_ids: list[str]
) -> dict[str, Any]:
    rejected = sum(
        stats[name]
        for name in (
            "missing_feature_column_rows",
            "missing_id_rows",
            "non_numeric_rows",
            "non_finite_rows",
            "out_of_range_rows",
        )
    )
    return {
        "input_row_count": int(stats["input_row_count"]),
        "valid_in_range_row_count": int(stats["valid_in_range_row_count"]),
        "rejected_row_count": int(rejected),
        "missing_feature_column_rows": int(stats["missing_feature_column_rows"]),
        "missing_id_rows": int(stats["missing_id_rows"]),
        "non_numeric_rows": int(stats["non_numeric_rows"]),
        "non_finite_rows": int(stats["non_finite_rows"]),
        "out_of_range_rows": int(stats["out_of_range_rows"]),
        "resolved_feature_columns": resolved,
        "resolved_id_columns": resolved_ids,
        "id_source_counts": {
            name.removeprefix("id_source__"): int(count)
            for name, count in stats.items()
            if name.startswith("id_source__")
        },
    }


def _read_targets(
    path: Path | None,
    requested: list[str],
    lower: np.ndarray,
    upper: np.ndarray,
    bins: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None:
        targets = []
        for index in itertools.product(range(bins), repeat=4):
            point = lower + (np.asarray(index, dtype=float) + 0.5) * (upper - lower) / bins
            targets.append(
                {
                    "target_id": "grid_" + "_".join(str(value) for value in index),
                    "point": point,
                    "source": "all_4d_grid_centers",
                }
            )
        return targets, {
            "target_mode": "all_4d_grid_centers",
            "input_target_rows": len(targets),
            "valid_target_rows": len(targets),
            "invalid_target_rows": 0,
        }
    targets: list[dict[str, Any]] = []
    invalid = 0
    input_count = 0
    if not path.is_file():
        return [], {
            "target_mode": "target_csv",
            "input_target_rows": 0,
            "valid_target_rows": 0,
            "invalid_target_rows": 0,
        }
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        resolved = [
            next(
                (
                    candidate
                    for candidate in (name, f"input__{name}", f"phys__{name}")
                    if candidate in fields
                ),
                "",
            )
            for name in requested
        ]
        for row_index, row in enumerate(reader):
            input_count += 1
            try:
                point = np.asarray([float(row[column]) for column in resolved], dtype=float)
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            if not np.all(np.isfinite(point)):
                invalid += 1
                continue
            targets.append(
                {
                    "target_id": str(row.get("target_id") or f"target_{row_index:06d}"),
                    "point": point,
                    "source": "target_csv",
                }
            )
    return targets, {
        "target_mode": "target_csv",
        "input_target_rows": input_count,
        "valid_target_rows": len(targets),
        "invalid_target_rows": invalid,
    }


def _split_indices(row_ids: list[str], fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    calibration = []
    reference = []
    boundary = int(float(fraction) * (1 << 64))
    for index, row_id in enumerate(row_ids):
        digest = hashlib.sha256(f"{seed}:{row_id}".encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big")
        (calibration if value < boundary else reference).append(index)
    return np.asarray(reference, dtype=int), np.asarray(calibration, dtype=int)


def _audit(
    matrix: np.ndarray,
    reference: np.ndarray,
    calibration: np.ndarray,
    targets: list[dict[str, Any]],
    lower: np.ndarray,
    upper: np.ndarray,
    bins: int,
    alpha: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    span = upper - lower
    normalized = (matrix - lower) / span
    reference_tree = cKDTree(normalized[reference])
    calibration_distance, _ = reference_tree.query(normalized[calibration], k=1)
    sorted_distance = np.sort(np.asarray(calibration_distance, dtype=float))
    order = min(len(sorted_distance), int(math.ceil((len(sorted_distance) + 1) * (1.0 - alpha))))
    threshold = float(sorted_distance[order - 1])
    empirical_calibration_fraction = float(np.mean(sorted_distance <= threshold))
    full_tree = cKDTree(normalized)

    indices = _bin_indices(matrix, lower, upper, bins)
    counts_4d = np.zeros((bins, bins, bins, bins), dtype=np.int64)
    np.add.at(counts_4d, tuple(indices[:, axis] for axis in range(4)), 1)
    pair_counts: dict[tuple[int, int], np.ndarray] = {}
    for left, right in itertools.combinations(range(4), 2):
        counts = np.zeros((bins, bins), dtype=np.int64)
        np.add.at(counts, (indices[:, left], indices[:, right]), 1)
        pair_counts[(left, right)] = counts

    records: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for target in targets:
        point = np.asarray(target["point"], dtype=float)
        in_range = bool(np.all(np.isfinite(point)) and np.all(point >= lower) and np.all(point <= upper))
        normalized_point = (point - lower) / span
        distance, nearest_row = full_tree.query(normalized_point, k=1)
        if in_range:
            index = tuple(int(value) for value in _bin_indices(point[None, :], lower, upper, bins)[0])
            exact_count = int(counts_4d[index])
            missing_pairs = [
                (left, right)
                for left, right in itertools.combinations(range(4), 2)
                if int(pair_counts[(left, right)][index[left], index[right]]) == 0
            ]
            if exact_count == 0:
                target_status = (
                    EMPTY_PAIRWISE_UNSUPPORTED if missing_pairs else EMPTY_PAIRWISE_SUPPORTED
                )
            elif float(distance) > threshold:
                target_status = DISTANCE_OOD
            else:
                target_status = SUPPORTED
        else:
            index = (-1, -1, -1, -1)
            exact_count = 0
            missing_pairs = []
            target_status = OUT_OF_RANGE
        status_counts[target_status] += 1
        record: dict[str, Any] = {
            "target_id": target["target_id"],
            "source": target["source"],
            "support_status": target_status,
            "in_declared_range": in_range,
            "bin_key": "|".join(str(value) for value in index),
            "exact_real_em_cell_count": exact_count,
            "nearest_real_normalized_euclidean_distance": float(distance),
            "calibrated_distance_threshold": threshold,
            "distance_within_threshold": bool(float(distance) <= threshold),
            "nearest_real_row_index": int(nearest_row),
            "missing_pairwise_projections": [
                f"{FEATURES[left]}__{FEATURES[right]}" for left, right in missing_pairs
            ],
        }
        for axis, feature in enumerate(FEATURES):
            record[feature] = float(point[axis])
            record[f"{feature}__bin"] = int(index[axis])
            record[f"nearest_real__{feature}"] = float(matrix[int(nearest_row), axis])
        records.append(record)
    return records, {
        "reference_row_count": int(len(reference)),
        "calibration_row_count": int(len(calibration)),
        "reference_index_sha256": _index_sha(reference),
        "calibration_index_sha256": _index_sha(calibration),
        "calibration_alpha": float(alpha),
        "calibration_order_statistic": int(order),
        "calibration_distance_threshold": threshold,
        "calibration_score_empirical_fraction_le_threshold": empirical_calibration_fraction,
        "distance_normalization": "declared_feature_range",
        "calibration_tree": "reference_split_only",
        "inference_tree": "full_real_accepted_pool",
        "support_status_counts": dict(status_counts),
        "occupied_full_pool_4d_cells": int(np.count_nonzero(counts_4d)),
    }


def _bin_indices(matrix: np.ndarray, lower: np.ndarray, upper: np.ndarray, bins: int) -> np.ndarray:
    scaled = (matrix - lower[None, :]) / (upper - lower)[None, :]
    return np.clip(np.floor(scaled * bins).astype(int), 0, bins - 1)


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "target_id",
        "source",
        "support_status",
        "in_declared_range",
        "bin_key",
        "exact_real_em_cell_count",
        "nearest_real_normalized_euclidean_distance",
        "calibrated_distance_threshold",
        "distance_within_threshold",
        "nearest_real_row_index",
        "missing_pairwise_projections",
    ]
    for feature in FEATURES:
        fields.extend([feature, f"{feature}__bin", f"nearest_real__{feature}"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["missing_pairwise_projections"] = ";".join(
                record["missing_pairwise_projections"]
            )
            writer.writerow(row)


def _write_plot(path: Path, records: list[dict[str, Any]], bins: int, grid_mode: bool) -> str:
    if not grid_mode:
        return "SKIPPED_TARGET_CSV_NOT_REGULAR_GRID"
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm, ListedColormap
        from matplotlib.patches import Patch

        status_order = [
            EMPTY_PAIRWISE_UNSUPPORTED,
            EMPTY_PAIRWISE_SUPPORTED,
            DISTANCE_OOD,
            SUPPORTED,
        ]
        colors = ["#f4a261", "#4ea8de", "#e9c46a", "#2a9d8f"]
        codes = {status: index for index, status in enumerate(status_order)}
        cube = np.zeros((bins, bins, bins, bins), dtype=int)
        for record in records:
            index = tuple(int(record[f"{feature}__bin"]) for feature in FEATURES)
            cube[index] = codes[record["support_status"]]
        cmap = ListedColormap(colors)
        norm = BoundaryNorm(np.arange(-0.5, len(colors) + 0.5), cmap.N)
        fig, axes = plt.subplots(
            bins,
            bins,
            figsize=(2.55 * bins, 2.3 * bins),
            sharex=True,
            sharey=True,
            constrained_layout=True,
        )
        axes = np.asarray(axes).reshape(bins, bins)
        for lp_bin in range(bins):
            for ls_bin in range(bins):
                axis = axes[bins - 1 - ls_bin, lp_bin]
                axis.imshow(
                    cube[lp_bin, ls_bin].T,
                    origin="lower",
                    cmap=cmap,
                    norm=norm,
                    aspect="equal",
                )
                axis.set_title(f"Lp bin {lp_bin}, Ls bin {ls_bin}", fontsize=8)
                axis.set_xticks(range(bins))
                axis.set_yticks(range(bins))
                if bins - 1 - ls_bin == bins - 1:
                    axis.set_xlabel("Q bin")
                if lp_bin == 0:
                    axis.set_ylabel("|K| bin")
        fig.suptitle(
            "Pre-inference support gate on real accepted EM data\n"
            "Green means empirically supported, not physically guaranteed",
            fontsize=13,
        )
        fig.legend(
            handles=[Patch(facecolor=color, label=status) for color, status in zip(colors, status_order)],
            loc="outside lower center",
            ncol=2,
            fontsize=8,
        )
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return "PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL"
    except Exception as exc:  # pragma: no cover - defensive artifact boundary
        path.with_suffix(".plot_error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )
        return f"FAIL: {type(exc).__name__}: {exc}"


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    counts = analysis.get("support_status_counts") or {}
    lines = [
        "# Physical-feature target support gate",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Decision: `{payload['decision']}`",
        f"- Real accepted rows: `{payload['input_stats']['valid_in_range_row_count']}`",
        f"- Distance threshold: `{analysis.get('calibration_distance_threshold')}`",
        "",
        "## Target status counts",
        "",
    ]
    lines.extend(f"- `{name}`: `{count}`" for name, count in sorted(counts.items()))
    lines.extend(["", "## Scientific boundary", "", payload["scientific_boundary"], ""])
    return "\n".join(lines)


def _index_sha(indices: np.ndarray) -> str:
    text = "\n".join(str(int(value)) for value in np.asarray(indices, dtype=int)) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_record(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "size_bytes": 0, "sha256": None}
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _file_sha(path),
    }


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _split_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in str(value).split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
