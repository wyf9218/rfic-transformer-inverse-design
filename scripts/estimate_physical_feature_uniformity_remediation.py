#!/usr/bin/env python3
"""Estimate an idealized acquisition budget for physical-feature uniformity.

The estimate is computed only from real rows already present in a training CSV.
It assumes every hypothetical new sample can be placed in the currently least
populated selected bin. Therefore it is an optimistic planning diagnostic, not
an EMX yield forecast and never a substitute for returned simulator labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_abs_center"
DEFAULT_LOWER = (0.5, 0.5, 5.0, 0.0)
DEFAULT_UPPER = (3.0, 3.0, 25.0, 0.8)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    training_csv = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(training_csv)
    requested_columns = _split_csv(args.feature_columns)
    resolved_columns = _resolve_columns(rows, requested_columns)
    lower = np.asarray(_split_floats(args.feature_lower), dtype=float)
    upper = np.asarray(_split_floats(args.feature_upper), dtype=float)
    matrix, rejected = _extract_matrix(rows, resolved_columns, lower, upper)

    one_d: dict[str, Any] = {}
    one_d_plot: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for index, name in enumerate(requested_columns):
        counts, _edges = np.histogram(
            matrix[:, index] if len(matrix) else np.asarray([], dtype=float),
            bins=int(args.bins),
            range=(float(lower[index]), float(upper[index])),
        )
        estimate = _estimate_additions(
            counts,
            min_occupied_fraction=float(args.min_1d_occupied_fraction),
            min_normalized_entropy=float(args.min_1d_normalized_entropy),
            max_nonzero_imbalance=float(args.max_1d_nonzero_imbalance),
            max_additions=int(args.max_ideal_additions),
        )
        one_d[name] = estimate
        one_d_plot[name] = (counts.astype(int), np.asarray(estimate["idealized_counts"], dtype=int))

    four_counts, _edges = np.histogramdd(
        matrix if len(matrix) else np.empty((0, len(requested_columns))),
        bins=[int(args.four_d_bins)] * len(requested_columns),
        range=[(float(lo), float(hi)) for lo, hi in zip(lower, upper)],
    )
    four_flat = four_counts.astype(int).reshape(-1)
    four_d = _estimate_additions(
        four_flat,
        min_occupied_fraction=float(args.min_four_d_occupied_fraction),
        min_normalized_entropy=float(args.min_four_d_normalized_entropy),
        max_nonzero_imbalance=float(args.max_four_d_nonzero_imbalance),
        max_additions=int(args.max_ideal_additions),
    )
    four_d_full_grid = _estimate_additions(
        four_flat,
        min_occupied_fraction=1.0,
        min_normalized_entropy=float(args.min_four_d_normalized_entropy),
        max_nonzero_imbalance=float(args.max_four_d_nonzero_imbalance),
        max_additions=int(args.max_ideal_additions),
    )

    plot_path = out_dir / "physical_feature_uniformity_remediation.png"
    plot_status = _write_plot(plot_path, one_d_plot, four_flat, np.asarray(four_d["idealized_counts"], dtype=int))
    checks = {
        "training_csv_exists": training_csv.is_file(),
        "rows_present": bool(rows),
        "four_feature_columns_resolved": len(resolved_columns) == 4 and all(resolved_columns),
        "valid_rows_present": len(matrix) > 0,
        "all_estimates_converged": all(item["converged"] for item in one_d.values())
        and four_d["converged"]
        and four_d_full_grid["converged"],
        "plot_written": plot_status == "PASS",
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_OPTIMISTIC_ACQUISITION_BUDGET_DIAGNOSTIC" if status == "PASS" else "FIX_INPUT_OR_ESTIMATE",
        "training_csv": _file_record(training_csv),
        "row_count": len(rows),
        "valid_in_range_row_count": int(len(matrix)),
        "rejected_row_count": int(rejected),
        "requested_feature_columns": requested_columns,
        "resolved_feature_columns": resolved_columns,
        "feature_lower": lower.tolist(),
        "feature_upper": upper.tolist(),
        "one_dimensional": one_d,
        "four_dimensional_minimum_occupancy_scenario": four_d,
        "four_dimensional_full_grid_scenario": four_d_full_grid,
        "plot": _file_record(plot_path),
        "checks": checks,
        "scientific_boundary": (
            "The water-fill estimate assumes every added real sample lands exactly in the selected least-populated bin, "
            "never increases an already dominant bin, and is accepted by all geometry/DRC/EMX gates. It is optimistic, "
            "not a lower-bound proof or time forecast. Only returned real EMX labels can demonstrate remediation."
        ),
        "arguments": vars(args),
    }
    summary_path = out_dir / "physical_feature_uniformity_remediation_summary.json"
    report_path = out_dir / "physical_feature_uniformity_remediation_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"summary={summary_path}")
    print(f"plot={plot_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--feature-lower", default=",".join(str(value) for value in DEFAULT_LOWER))
    parser.add_argument("--feature-upper", default=",".join(str(value) for value in DEFAULT_UPPER))
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--four-d-bins", type=int, default=4)
    parser.add_argument("--min-1d-occupied-fraction", type=float, default=0.90)
    parser.add_argument("--min-1d-normalized-entropy", type=float, default=0.90)
    parser.add_argument("--max-1d-nonzero-imbalance", type=float, default=2.50)
    parser.add_argument("--min-four-d-occupied-fraction", type=float, default=0.50)
    parser.add_argument("--min-four-d-normalized-entropy", type=float, default=0.80)
    parser.add_argument("--max-four-d-nonzero-imbalance", type=float, default=4.0)
    parser.add_argument("--max-ideal-additions", type=int, default=2_000_000)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.bins < 2 or args.four_d_bins < 2 or args.max_ideal_additions < 0:
        parser.error("bin counts must be >=2 and max additions must be nonnegative")
    if len(_split_csv(args.feature_columns)) != 4:
        parser.error("exactly four feature columns are required")
    if len(_split_floats(args.feature_lower)) != 4 or len(_split_floats(args.feature_upper)) != 4:
        parser.error("feature lower/upper must each contain four values")
    return args


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_columns(rows: list[dict[str, str]], requested: list[str]) -> list[str]:
    fields = set(rows[0]) if rows else set()
    resolved = []
    for name in requested:
        candidates = (name, f"input__{name}", f"phys__{name}")
        resolved.append(next((candidate for candidate in candidates if candidate in fields), ""))
    return resolved


def _extract_matrix(
    rows: list[dict[str, str]],
    columns: list[str],
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, int]:
    values: list[list[float]] = []
    rejected = 0
    if not all(columns):
        return np.empty((0, 4), dtype=float), len(rows)
    for row in rows:
        try:
            point = np.asarray([float(row[column]) for column in columns], dtype=float)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if not np.all(np.isfinite(point)) or np.any(point < lower) or np.any(point > upper):
            rejected += 1
            continue
        values.append(point.tolist())
    return (np.asarray(values, dtype=float).reshape(-1, 4), rejected)


def _estimate_additions(
    counts: np.ndarray,
    *,
    min_occupied_fraction: float,
    min_normalized_entropy: float,
    max_nonzero_imbalance: float,
    max_additions: int,
) -> dict[str, Any]:
    base = np.asarray(counts, dtype=int).reshape(-1)
    required_occupied = int(math.ceil(len(base) * min_occupied_fraction))
    occupied = np.flatnonzero(base > 0).tolist()
    empty = np.flatnonzero(base == 0).tolist()
    active = occupied + empty[: max(0, required_occupied - len(occupied))]
    if not active and len(base):
        active = [0]

    def candidate(additions: int) -> np.ndarray:
        return _waterfill(base, active, additions)

    initial = _count_metrics(base)
    if _passes(initial, len(base), min_occupied_fraction, min_normalized_entropy, max_nonzero_imbalance):
        additions = 0
        final = base.copy()
        converged = True
    else:
        high = 1
        while high < max_additions:
            metrics = _count_metrics(candidate(high))
            if _passes(metrics, len(base), min_occupied_fraction, min_normalized_entropy, max_nonzero_imbalance):
                break
            high = min(max_additions, high * 2)
        high_metrics = _count_metrics(candidate(high))
        converged = _passes(high_metrics, len(base), min_occupied_fraction, min_normalized_entropy, max_nonzero_imbalance)
        if converged:
            low = 0
            while low < high:
                middle = (low + high) // 2
                metrics = _count_metrics(candidate(middle))
                if _passes(metrics, len(base), min_occupied_fraction, min_normalized_entropy, max_nonzero_imbalance):
                    high = middle
                else:
                    low = middle + 1
            additions = low
            final = candidate(additions)
        else:
            additions = max_additions
            final = candidate(additions)
    return {
        "converged": bool(converged),
        "idealized_additions": int(additions),
        "selected_active_bin_count": len(active),
        "required_occupied_bin_count": required_occupied,
        "initial": initial,
        "idealized_after": _count_metrics(final),
        "initial_counts": base.tolist(),
        "idealized_counts": final.tolist(),
    }


def _waterfill(base: np.ndarray, active: list[int], additions: int) -> np.ndarray:
    result = np.asarray(base, dtype=int).copy()
    if additions <= 0 or not active:
        return result
    ordered = sorted((int(result[index]), int(index)) for index in active)
    remaining = int(additions)
    level = ordered[0][0]
    width = 1
    while width < len(ordered):
        next_level = ordered[width][0]
        cost = (next_level - level) * width
        if cost > remaining:
            break
        remaining -= cost
        level = next_level
        width += 1
    quotient, remainder = divmod(remaining, width)
    target_level = level + quotient
    for order, (_value, index) in enumerate(ordered[:width]):
        result[index] = target_level + (1 if order < remainder else 0)
    return result


def _count_metrics(counts: np.ndarray) -> dict[str, Any]:
    values = np.asarray(counts, dtype=int).reshape(-1)
    nonzero = values[values > 0]
    total = int(values.sum())
    entropy = 0.0
    if total > 0 and len(values) > 1:
        probabilities = nonzero.astype(float) / total
        entropy = float(-np.sum(probabilities * np.log(probabilities)) / math.log(len(values)))
    return {
        "sample_count": total,
        "bin_count": len(values),
        "occupied_bins": int(len(nonzero)),
        "occupied_fraction": float(len(nonzero) / len(values)) if len(values) else 0.0,
        "normalized_entropy": entropy,
        "nonzero_min_count": int(nonzero.min()) if len(nonzero) else 0,
        "nonzero_max_count": int(nonzero.max()) if len(nonzero) else 0,
        "max_to_min_nonzero_ratio": float(nonzero.max() / nonzero.min()) if len(nonzero) else math.inf,
    }


def _passes(metrics: dict[str, Any], bin_count: int, occupied: float, entropy: float, imbalance: float) -> bool:
    return bool(
        metrics["occupied_bins"] >= int(math.ceil(bin_count * occupied))
        and metrics["normalized_entropy"] >= entropy
        and metrics["max_to_min_nonzero_ratio"] <= imbalance
    )


def _write_plot(
    path: Path,
    one_d: dict[str, tuple[np.ndarray, np.ndarray]],
    four_current: np.ndarray,
    four_ideal: np.ndarray,
) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 3, figsize=(14.5, 8.2), constrained_layout=True)
        for axis, (name, (current, ideal)) in zip(axes.flat[:4], one_d.items()):
            x = np.arange(len(current))
            axis.bar(x - 0.2, current, width=0.4, color="#1f77b4", label="Current real EMX")
            axis.bar(x + 0.2, ideal, width=0.4, color="#f28e2b", label="Idealized water-fill")
            axis.set_title(name)
            axis.set_xlabel("Declared-range bin")
            axis.set_ylabel("Count")
            axis.grid(True, axis="y", alpha=0.25)
        axes.flat[0].legend(frameon=False, fontsize=8)
        order = np.arange(len(four_current))
        axes.flat[4].plot(order, np.sort(four_current), color="#1f77b4", label="Current")
        axes.flat[4].plot(order, np.sort(four_ideal), color="#f28e2b", label="Idealized")
        axes.flat[4].set_title("4-D cell counts (sorted)")
        axes.flat[4].set_xlabel("4-D cell rank")
        axes.flat[4].set_ylabel("Count")
        axes.flat[4].grid(True, alpha=0.25)
        axes.flat[4].legend(frameon=False, fontsize=8)
        axes.flat[5].axis("off")
        fig.suptitle(
            "Planning diagnostic: blue is current real EMX; orange is hypothetical perfect-target water-fill\n"
            "Orange bars are not simulated or accepted samples",
            fontsize=13,
        )
        fig.savefig(path, dpi=220)
        plt.close(fig)
    except Exception:  # pragma: no cover - backend failure is reported by the artifact gate.
        return "FAIL"
    return "PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL"


def _file_record(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "size_bytes": 0}
    if path.is_file():
        data = path.read_bytes()
        record.update({"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return record


def _render_report(data: dict[str, Any]) -> str:
    lines = [
        "# Physical-feature uniformity remediation estimate",
        "",
        f"- Overall status: **{data['overall_status']}**",
        f"- Real in-range rows: `{data['valid_in_range_row_count']}`",
        "",
        "| Feature | Idealized additions | Current entropy | Current imbalance |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in data["one_dimensional"].items():
        initial = item["initial"]
        lines.append(
            f"| {name} | {item['idealized_additions']} | {initial['normalized_entropy']:.6f} | "
            f"{initial['max_to_min_nonzero_ratio']:.6f} |"
        )
    four = data["four_dimensional_minimum_occupancy_scenario"]
    lines.extend(
        [
            "",
            f"- 4-D minimum-occupancy idealized additions: `{four['idealized_additions']}`",
            f"- 4-D full-grid idealized additions: `{data['four_dimensional_full_grid_scenario']['idealized_additions']}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _split_floats(raw: str) -> list[float]:
    return [float(item.strip()) for item in str(raw).split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
