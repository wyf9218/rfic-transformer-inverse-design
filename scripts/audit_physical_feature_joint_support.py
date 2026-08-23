#!/usr/bin/env python3
"""Diagnose support gaps in the real Lp/Ls/Q/|K| joint distribution.

This audit uses returned, in-range EM rows only.  It distinguishes empty 4-D
cells whose one-dimensional or pairwise projections are also empty from cells
whose six pairwise projections are all populated.  The latter are interaction
gaps, not evidence that the requested physical combination is feasible.

No class produced here can remove a cell from the final uniformity denominator
or declare it physically impossible.  Only additional returned EM labels can
establish support for an empty cell.
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
FEATURE_LABELS = ("Lp", "Ls", "Q", "|K|")
DEFAULT_LOWER = (0.5, 0.5, 5.0, 0.0)
DEFAULT_UPPER = (3.0, 3.0, 25.0, 0.8)

OCCUPIED = "OCCUPIED_REAL_EM_CELL"
PAIRWISE_SUPPORTED = "PAIRWISE_SUPPORTED_4D_EMPTY"
PAIRWISE_UNSUPPORTED = "PAIRWISE_UNSUPPORTED"
MARGINAL_UNSUPPORTED = "MARGINAL_UNSUPPORTED"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / "physical_feature_joint_support_summary.json",
        "records": out_dir / "physical_feature_joint_support_cells.csv",
        "report": out_dir / "physical_feature_joint_support_report.md",
        "figure": out_dir / "physical_feature_joint_support.png",
    }

    requested = _split_csv(args.feature_columns)
    lower = np.asarray(_split_floats(args.feature_lower), dtype=float)
    upper = np.asarray(_split_floats(args.feature_upper), dtype=float)
    matrix, input_stats = _read_matrix(training_csv, requested, lower, upper)
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "exact_four_feature_contract": tuple(requested) == FEATURES,
        "finite_ordered_explicit_ranges": bool(
            len(lower) == 4
            and len(upper) == 4
            and np.all(np.isfinite(lower))
            and np.all(np.isfinite(upper))
            and np.all(upper > lower)
        ),
        "rows_present": int(input_stats["input_row_count"]) > 0,
        "all_rows_valid_and_in_range": int(input_stats["rejected_row_count"]) == 0,
        "valid_rows_present": len(matrix) > 0,
    }

    records: list[dict[str, Any]] = []
    analysis: dict[str, Any] = {}
    if all(checks.values()):
        records, analysis = _audit(matrix, lower, upper, int(args.bins))
        _write_records(paths["records"], records, requested)
        plot_status = "SKIPPED_BY_REQUEST" if args.no_plots else _write_plot(
            paths["figure"], records, int(args.bins)
        )
        checks["records_written"] = paths["records"].is_file()
        checks["plot_written_or_explicitly_skipped"] = (
            plot_status == "SKIPPED_BY_REQUEST" or plot_status == "PASS"
        )
    else:
        plot_status = "NOT_ATTEMPTED_INPUT_FAILURE"
        checks["records_written"] = False
        checks["plot_written_or_explicitly_skipped"] = False

    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "USE_AS_ADVISORY_JOINT_SUPPORT_DIAGNOSTIC_NOT_PHYSICAL_FEASIBILITY_PROOF"
            if status == "PASS"
            else "FIX_REAL_ACCEPTED_INPUT_BEFORE_JOINT_SUPPORT_DIAGNOSIS"
        ),
        "training_csv": _file_record(training_csv),
        "requested_feature_columns": requested,
        "feature_labels": list(FEATURE_LABELS),
        "feature_lower": lower.tolist(),
        "feature_upper": upper.tolist(),
        "bins_per_feature": int(args.bins),
        "total_4d_cell_count": int(args.bins) ** 4,
        "input_stats": input_stats,
        "analysis": analysis,
        "checks": checks,
        "classification_definitions": {
            OCCUPIED: "At least one returned real EM sample occupies the exact 4-D cell.",
            PAIRWISE_SUPPORTED: (
                "The exact 4-D cell is empty, but every one of its six pairwise projected cells "
                "contains at least one returned real EM sample."
            ),
            PAIRWISE_UNSUPPORTED: (
                "All four marginal bins are populated, but at least one of the six pairwise "
                "projected cells is empty."
            ),
            MARGINAL_UNSUPPORTED: "At least one of the four one-dimensional bins is empty.",
        },
        "uniformity_contract": {
            "denominator_changed": False,
            "all_4d_cells_remain_in_final_denominator": True,
            "production_queue_or_labels_modified": False,
        },
        "scientific_boundary": (
            "Projection support and nearest-neighbor distance are descriptive evidence only. "
            "PAIRWISE_SUPPORTED_4D_EMPTY does not prove that a four-feature combination is "
            "physically reachable; PAIRWISE_UNSUPPORTED and MARGINAL_UNSUPPORTED do not prove "
            "physical impossibility. No class may relax, reweight, or remove a final real-EM "
            "uniformity cell."
        ),
        "outputs": {
            "records": _file_record(paths["records"]),
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
    print(f"records={paths['records']}")
    print(f"figure_status={plot_status}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=",".join(FEATURES))
    parser.add_argument("--feature-lower", default=",".join(str(value) for value in DEFAULT_LOWER))
    parser.add_argument("--feature-upper", default=",".join(str(value) for value in DEFAULT_UPPER))
    parser.add_argument("--bins", type=int, default=4)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.bins) < 2:
        parser.error("--bins must be at least 2")
    if len(_split_csv(args.feature_columns)) != 4:
        parser.error("exactly four feature columns are required")
    if len(_split_floats(args.feature_lower)) != 4 or len(_split_floats(args.feature_upper)) != 4:
        parser.error("feature lower/upper must each contain four values")
    return args


def _read_matrix(
    path: Path,
    requested: list[str],
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    stats: Counter[str] = Counter()
    values: list[list[float]] = []
    resolved: list[str] = []
    if not path.is_file():
        return np.empty((0, 4), dtype=float), {
            "input_row_count": 0,
            "valid_in_range_row_count": 0,
            "rejected_row_count": 0,
            "resolved_feature_columns": [],
        }
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        for name in requested:
            candidates = (name, f"input__{name}", f"phys__{name}")
            resolved.append(next((candidate for candidate in candidates if candidate in fields), ""))
        for row in reader:
            stats["input_row_count"] += 1
            if not all(resolved):
                stats["missing_feature_column_rows"] += 1
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
            stats["valid_in_range_row_count"] += 1
    rejected = (
        stats["missing_feature_column_rows"]
        + stats["non_numeric_rows"]
        + stats["non_finite_rows"]
        + stats["out_of_range_rows"]
    )
    return np.asarray(values, dtype=float).reshape(-1, 4), {
        "input_row_count": int(stats["input_row_count"]),
        "valid_in_range_row_count": int(stats["valid_in_range_row_count"]),
        "rejected_row_count": int(rejected),
        "missing_feature_column_rows": int(stats["missing_feature_column_rows"]),
        "non_numeric_rows": int(stats["non_numeric_rows"]),
        "non_finite_rows": int(stats["non_finite_rows"]),
        "out_of_range_rows": int(stats["out_of_range_rows"]),
        "resolved_feature_columns": resolved,
    }


def _audit(
    matrix: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    bins: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    span = upper - lower
    normalized = (matrix - lower) / span
    indices = np.floor(normalized * bins).astype(int)
    indices = np.clip(indices, 0, bins - 1)

    marginal_counts = [np.bincount(indices[:, axis], minlength=bins) for axis in range(4)]
    pair_counts: dict[tuple[int, int], np.ndarray] = {}
    for left, right in itertools.combinations(range(4), 2):
        counts = np.zeros((bins, bins), dtype=np.int64)
        np.add.at(counts, (indices[:, left], indices[:, right]), 1)
        pair_counts[(left, right)] = counts
    counts_4d = np.zeros((bins, bins, bins, bins), dtype=np.int64)
    np.add.at(counts_4d, tuple(indices[:, axis] for axis in range(4)), 1)

    grid = list(itertools.product(range(bins), repeat=4))
    empty_grid = [index for index in grid if int(counts_4d[index]) == 0]
    nearest: dict[tuple[int, int, int, int], tuple[float, int]] = {}
    if empty_grid:
        centers = np.asarray(
            [[(value + 0.5) / bins for value in index] for index in empty_grid], dtype=float
        )
        distances, nearest_rows = cKDTree(normalized).query(centers, k=1)
        nearest = {
            index: (float(distance), int(row_index))
            for index, distance, row_index in zip(empty_grid, distances, nearest_rows)
        }

    records: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    for index in grid:
        exact_count = int(counts_4d[index])
        marginal_missing = [
            axis for axis in range(4) if int(marginal_counts[axis][index[axis]]) == 0
        ]
        missing_pairs = [
            (left, right)
            for left, right in itertools.combinations(range(4), 2)
            if int(pair_counts[(left, right)][index[left], index[right]]) == 0
        ]
        if exact_count > 0:
            classification = OCCUPIED
        elif marginal_missing:
            classification = MARGINAL_UNSUPPORTED
        elif missing_pairs:
            classification = PAIRWISE_UNSUPPORTED
        else:
            classification = PAIRWISE_SUPPORTED
        class_counts[classification] += 1
        distance, nearest_row = nearest.get(index, (0.0, -1))
        record: dict[str, Any] = {
            "bin_key": "|".join(str(value) for value in index),
            "classification": classification,
            "exact_real_em_count": exact_count,
            "nearest_real_normalized_euclidean_distance": distance,
            "missing_marginal_features": [FEATURES[axis] for axis in marginal_missing],
            "missing_pairwise_projections": [
                f"{FEATURES[left]}__{FEATURES[right]}" for left, right in missing_pairs
            ],
        }
        for axis, feature in enumerate(FEATURES):
            edge_low = lower[axis] + index[axis] * span[axis] / bins
            edge_high = lower[axis] + (index[axis] + 1) * span[axis] / bins
            record[f"{feature}__bin"] = index[axis]
            record[f"{feature}__min"] = float(edge_low)
            record[f"{feature}__max"] = float(edge_high)
            record[f"{feature}__marginal_count"] = int(marginal_counts[axis][index[axis]])
            record[f"nearest_real__{feature}"] = (
                float(matrix[nearest_row, axis]) if nearest_row >= 0 else None
            )
        for left, right in itertools.combinations(range(4), 2):
            name = f"pair_count__{FEATURES[left]}__{FEATURES[right]}"
            record[name] = int(pair_counts[(left, right)][index[left], index[right]])
        records.append(record)

    empty_records = [record for record in records if record["classification"] != OCCUPIED]
    distance_by_class: dict[str, Any] = {}
    for classification in (PAIRWISE_SUPPORTED, PAIRWISE_UNSUPPORTED, MARGINAL_UNSUPPORTED):
        distances = np.asarray(
            [
                record["nearest_real_normalized_euclidean_distance"]
                for record in empty_records
                if record["classification"] == classification
            ],
            dtype=float,
        )
        distance_by_class[classification] = _distance_summary(distances)
    return records, {
        "classification_counts": dict(class_counts),
        "occupied_4d_cell_count": int(np.count_nonzero(counts_4d)),
        "empty_4d_cell_count": int(len(empty_grid)),
        "occupied_4d_fraction": float(np.count_nonzero(counts_4d) / counts_4d.size),
        "pairwise_projection_occupied_counts": {
            f"{FEATURES[left]}__{FEATURES[right]}": int(np.count_nonzero(counts))
            for (left, right), counts in pair_counts.items()
        },
        "marginal_occupied_counts": {
            FEATURES[axis]: int(np.count_nonzero(counts))
            for axis, counts in enumerate(marginal_counts)
        },
        "nearest_distance_by_empty_class": distance_by_class,
    }


def _distance_summary(values: np.ndarray) -> dict[str, Any]:
    if not len(values):
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    return {
        "count": int(len(values)),
        "min": float(np.min(values)),
        "median": float(np.median(values)),
        "p95": float(np.quantile(values, 0.95)),
        "max": float(np.max(values)),
    }


def _write_records(path: Path, records: list[dict[str, Any]], requested: list[str]) -> None:
    fields = [
        "bin_key",
        "classification",
        "exact_real_em_count",
        "nearest_real_normalized_euclidean_distance",
        "missing_marginal_features",
        "missing_pairwise_projections",
    ]
    for feature in requested:
        fields.extend(
            [
                f"{feature}__bin",
                f"{feature}__min",
                f"{feature}__max",
                f"{feature}__marginal_count",
                f"nearest_real__{feature}",
            ]
        )
    for left, right in itertools.combinations(range(4), 2):
        fields.append(f"pair_count__{FEATURES[left]}__{FEATURES[right]}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["missing_marginal_features"] = ";".join(record["missing_marginal_features"])
            row["missing_pairwise_projections"] = ";".join(
                record["missing_pairwise_projections"]
            )
            writer.writerow(row)


def _write_plot(path: Path, records: list[dict[str, Any]], bins: int) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm, ListedColormap
        from matplotlib.patches import Patch

        codes = {
            MARGINAL_UNSUPPORTED: 0,
            PAIRWISE_UNSUPPORTED: 1,
            PAIRWISE_SUPPORTED: 2,
            OCCUPIED: 3,
        }
        cube = np.zeros((bins, bins, bins, bins), dtype=int)
        counts = np.zeros_like(cube)
        for record in records:
            index = tuple(int(record[f"{feature}__bin"]) for feature in FEATURES)
            cube[index] = codes[record["classification"]]
            counts[index] = int(record["exact_real_em_count"])
        colors = ["#d9d9d9", "#f4a261", "#4ea8de", "#2a9d8f"]
        labels = [MARGINAL_UNSUPPORTED, PAIRWISE_UNSUPPORTED, PAIRWISE_SUPPORTED, OCCUPIED]
        cmap = ListedColormap(colors)
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)
        fig, axes = plt.subplots(
            bins,
            bins,
            figsize=(2.55 * bins, 2.35 * bins),
            sharex=True,
            sharey=True,
            constrained_layout=True,
        )
        axes = np.asarray(axes).reshape(bins, bins)
        for lp_bin in range(bins):
            for ls_bin in range(bins):
                axis = axes[bins - 1 - ls_bin, lp_bin]
                panel = cube[lp_bin, ls_bin].T
                axis.imshow(panel, origin="lower", cmap=cmap, norm=norm, aspect="equal")
                for q_bin in range(bins):
                    for k_bin in range(bins):
                        count = int(counts[lp_bin, ls_bin, q_bin, k_bin])
                        if count:
                            axis.text(q_bin, k_bin, str(count), ha="center", va="center", fontsize=6)
                axis.set_title(f"Lp bin {lp_bin}, Ls bin {ls_bin}", fontsize=8)
                axis.set_xticks(range(bins))
                axis.set_yticks(range(bins))
                if bins - 1 - ls_bin == bins - 1:
                    axis.set_xlabel("Q bin")
                if lp_bin == 0:
                    axis.set_ylabel("|K| bin")
        fig.suptitle(
            "Real-EM Lp/Ls/Q/|K| joint-support diagnostic\n"
            "Blue empty cells have all six pairwise projections populated; this is not feasibility proof",
            fontsize=13,
        )
        fig.legend(
            handles=[Patch(facecolor=color, label=label) for color, label in zip(colors, labels)],
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
    counts = analysis.get("classification_counts") or {}
    lines = [
        "# Physical-feature joint-support diagnostic",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Decision: `{payload['decision']}`",
        f"- Real in-range rows: `{payload['input_stats']['valid_in_range_row_count']}`",
        f"- Occupied 4-D cells: `{analysis.get('occupied_4d_cell_count', 0)}/{payload['total_4d_cell_count']}`",
        f"- Pairwise-supported but 4-D empty: `{counts.get(PAIRWISE_SUPPORTED, 0)}`",
        f"- Pairwise-unsupported empty: `{counts.get(PAIRWISE_UNSUPPORTED, 0)}`",
        f"- Marginal-unsupported empty: `{counts.get(MARGINAL_UNSUPPORTED, 0)}`",
        "",
        "## Scientific boundary",
        "",
        payload["scientific_boundary"],
        "",
        "All 4-D cells remain in the final real-EM uniformity denominator. This audit does not modify production.",
        "",
    ]
    return "\n".join(lines)


def _file_record(path: Path) -> dict[str, Any]:
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
