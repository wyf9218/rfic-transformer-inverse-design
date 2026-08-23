#!/usr/bin/env python3
"""Analyze exactly five preregistered paired data-size replicates.

The primary interval resamples/varies training replicates, never target rows.
Plots combine mean bars with all paired replicate dots and connecting lines so
the five observations and their dependence remain visible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


PAIR_COUNT = 5
T_CRITICAL_975_DF4 = 2.7764451051977987
BOOTSTRAP_SEED = 2026082202
BOOTSTRAP_DRAWS = 100000
REQUIRED_COLUMNS = (
    "estimand_id",
    "metric_id",
    "metric_label",
    "panel",
    "role",
    "unit",
    "better_direction",
    "replicate",
    "small_value",
    "large_value",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-metrics-csv", required=True)
    parser.add_argument("--expected-paired-metrics-sha256", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--clarification-addendum-json", required=True)
    parser.add_argument("--expected-clarification-addendum-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(path: Path, expected: str, label: str) -> str:
    actual = _sha256(path)
    if actual != expected.strip().lower():
        raise ValueError(f"{label} SHA-256 mismatch: {actual} != {expected}")
    return actual


def _safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return result or "metric"


def _mean_sd_ci(values: np.ndarray) -> dict[str, float]:
    vector = np.asarray(values, dtype=float)
    if vector.shape != (PAIR_COUNT,) or not np.all(np.isfinite(vector)):
        raise ValueError("each estimate requires exactly five finite paired values")
    mean = float(np.mean(vector))
    sd = float(np.std(vector, ddof=1))
    se = sd / math.sqrt(PAIR_COUNT)
    half_width = T_CRITICAL_975_DF4 * se
    return {
        "mean": mean,
        "sample_sd": sd,
        "standard_error": se,
        "ci95_t_df4_lower": mean - half_width,
        "ci95_t_df4_upper": mean + half_width,
    }


def _bootstrap_interval(values: np.ndarray) -> dict[str, Any]:
    vector = np.asarray(values, dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, PAIR_COUNT, size=(BOOTSTRAP_DRAWS, PAIR_COUNT))
    means = np.mean(vector[indices], axis=1)
    return {
        "method": "paired_training_replicate_percentile_bootstrap_sensitivity",
        "seed": BOOTSTRAP_SEED,
        "draws": BOOTSTRAP_DRAWS,
        "lower_2p5": float(np.percentile(means, 2.5)),
        "upper_97p5": float(np.percentile(means, 97.5)),
        "boundary": "Small-n sensitivity only; not the primary interval and not a target-row or population bootstrap.",
    }


def _load_groups(path: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS).difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"paired metrics CSV missing columns {sorted(missing)}")
        for row in reader:
            groups[(row["estimand_id"], row["metric_id"])].append(row)
    if not groups:
        raise ValueError("paired metrics CSV has no metric groups")
    return groups


def _analyze_group(rows: list[dict[str, str]]) -> dict[str, Any]:
    if len(rows) != PAIR_COUNT:
        raise ValueError(f"expected exactly five paired rows, found {len(rows)}")
    rows = sorted(rows, key=lambda row: int(row["replicate"]))
    replicates = [int(row["replicate"]) for row in rows]
    if replicates != [1, 2, 3, 4, 5]:
        raise ValueError(f"replicate IDs must be 1..5, found {replicates}")
    invariant_columns = (
        "estimand_id",
        "metric_id",
        "metric_label",
        "panel",
        "role",
        "unit",
        "better_direction",
    )
    for column in invariant_columns:
        if len({row[column] for row in rows}) != 1:
            raise ValueError(f"{column} changes within a metric group")
    direction = rows[0]["better_direction"]
    if direction not in {"lower", "higher"}:
        raise ValueError("better_direction must be lower or higher")
    small = np.asarray([float(row["small_value"]) for row in rows], dtype=float)
    large = np.asarray([float(row["large_value"]) for row in rows], dtype=float)
    if not np.all(np.isfinite(small)) or not np.all(np.isfinite(large)):
        raise ValueError("paired metric values must be finite")
    raw_delta = large - small
    signed_improvement = -raw_delta if direction == "lower" else raw_delta
    if np.any(np.abs(small) <= 1e-15):
        relative_improvement = None
    else:
        relative_improvement = signed_improvement / np.abs(small)
    raw_stats = _mean_sd_ci(raw_delta)
    improvement_stats = _mean_sd_ci(signed_improvement)
    relative_stats = (
        _mean_sd_ci(relative_improvement) if relative_improvement is not None else None
    )
    paired_effect_dz = (
        improvement_stats["mean"] / improvement_stats["sample_sd"]
        if improvement_stats["sample_sd"] > 0.0
        else None
    )
    return {
        "estimand_id": rows[0]["estimand_id"],
        "metric_id": rows[0]["metric_id"],
        "metric_label": rows[0]["metric_label"],
        "panel": rows[0]["panel"],
        "role": rows[0]["role"],
        "unit": rows[0]["unit"],
        "better_direction": direction,
        "replicates": replicates,
        "small_values": small.tolist(),
        "large_values": large.tolist(),
        "small_arm": _mean_sd_ci(small),
        "large_arm": _mean_sd_ci(large),
        "raw_delta_large_minus_small": {
            **raw_stats,
            "values": raw_delta.tolist(),
        },
        "signed_improvement_positive_is_better": {
            **improvement_stats,
            "values": signed_improvement.tolist(),
            "paired_effect_size_dz": paired_effect_dz,
            "paired_bootstrap_sensitivity": _bootstrap_interval(signed_improvement),
        },
        "relative_signed_improvement": (
            {
                **relative_stats,
                "values": relative_improvement.tolist(),
                "interpretation": "per-replicate signed improvement divided by absolute small-arm value",
            }
            if relative_stats is not None
            else None
        ),
    }


def _write_plot(result: dict[str, Any], output_base: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    small = np.asarray(result["small_values"], dtype=float)
    large = np.asarray(result["large_values"], dtype=float)
    fig, ax = plt.subplots(figsize=(6.8, 5.0), constrained_layout=True)
    ax.bar([0, 1], [np.mean(small), np.mean(large)], width=0.58, color=["#4C78A8", "#F58518"], alpha=0.76)
    for index in range(PAIR_COUNT):
        ax.plot([0, 1], [small[index], large[index]], color="#5A5A5A", alpha=0.55, linewidth=1.0, zorder=2)
        ax.scatter([0, 1], [small[index], large[index]], color=["#1F4E79", "#A94B00"], s=28, zorder=3)
    improvement = result["signed_improvement_positive_is_better"]
    annotation = (
        f"Paired improvement (positive=better): {improvement['mean']:.4g}\n"
        f"95% t interval across 5 pairs: [{improvement['ci95_t_df4_lower']:.4g}, "
        f"{improvement['ci95_t_df4_upper']:.4g}]"
    )
    ax.text(0.5, 0.98, annotation, transform=ax.transAxes, ha="center", va="top", fontsize=9,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#BBBBBB", "alpha": 0.9})
    ax.set_xticks([0, 1], ["100k train", "200k train"])
    ax.set_ylabel(f"{result['metric_label']} ({result['unit']})")
    ax.set_title(f"{result['metric_label']} · {result['panel']}\nBars=means; dots/lines=5 preregistered pairs")
    ax.grid(axis="y", alpha=0.22)
    fig.text(
        0.5,
        0.005,
        "Conditional on one historical pool and (for RQ-I) one shared F_ref; not a deployment-population CI.",
        ha="center",
        fontsize=8,
        color="#444444",
    )
    fig.savefig(output_base.with_suffix(".png"), dpi=220)
    fig.savefig(output_base.with_suffix(".svg"))
    plt.close(fig)


def main() -> int:
    args = _parse_args()
    input_path = Path(args.paired_metrics_csv).expanduser().resolve()
    prereg_path = Path(args.preregistration_json).expanduser().resolve()
    clarification_path = Path(args.clarification_addendum_json).expanduser().resolve()
    sources = {
        "paired_metrics_csv": {
            "path": str(input_path),
            "sha256": _require_sha(
                input_path, args.expected_paired_metrics_sha256, "paired metrics CSV"
            ),
        },
        "preregistration": {
            "path": str(prereg_path),
            "sha256": _require_sha(
                prereg_path, args.expected_preregistration_sha256, "preregistration"
            ),
        },
        "clarification_addendum": {
            "path": str(clarification_path),
            "sha256": _require_sha(
                clarification_path,
                args.expected_clarification_addendum_sha256,
                "clarification addendum",
            ),
        },
    }
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    clarification = json.loads(clarification_path.read_text(encoding="utf-8"))
    if prereg.get("schema") != "controlled_historical_data_scaling_preregistration_v1":
        raise ValueError("wrong preregistration schema")
    if clarification.get("parent_preregistration_sha256") != sources["preregistration"]["sha256"]:
        raise ValueError("clarification does not bind the supplied preregistration")

    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise FileExistsError(f"no-clobber output exists: {out_dir}")
    out_dir.mkdir(parents=True)
    groups = _load_groups(input_path)
    results = [_analyze_group(groups[key]) for key in sorted(groups)]
    figure_records: list[dict[str, str]] = []
    for result in results:
        name = _safe_name(f"{result['estimand_id']}__{result['metric_id']}")
        base = out_dir / name
        _write_plot(result, base)
        figure_records.append(
            {
                "estimand_id": result["estimand_id"],
                "metric_id": result["metric_id"],
                "png": str(base.with_suffix(".png")),
                "png_sha256": _sha256(base.with_suffix(".png")),
                "svg": str(base.with_suffix(".svg")),
                "svg_sha256": _sha256(base.with_suffix(".svg")),
            }
        )

    flat_path = out_dir / "controlled_paired_effects.csv"
    with flat_path.open("x", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "estimand_id",
            "metric_id",
            "metric_label",
            "panel",
            "role",
            "unit",
            "better_direction",
            "small_mean",
            "large_mean",
            "raw_delta_large_minus_small_mean",
            "signed_improvement_mean",
            "signed_improvement_ci95_lower",
            "signed_improvement_ci95_upper",
            "relative_signed_improvement_mean",
            "paired_effect_size_dz",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            improvement = result["signed_improvement_positive_is_better"]
            relative = result["relative_signed_improvement"]
            writer.writerow(
                {
                    "estimand_id": result["estimand_id"],
                    "metric_id": result["metric_id"],
                    "metric_label": result["metric_label"],
                    "panel": result["panel"],
                    "role": result["role"],
                    "unit": result["unit"],
                    "better_direction": result["better_direction"],
                    "small_mean": result["small_arm"]["mean"],
                    "large_mean": result["large_arm"]["mean"],
                    "raw_delta_large_minus_small_mean": result[
                        "raw_delta_large_minus_small"
                    ]["mean"],
                    "signed_improvement_mean": improvement["mean"],
                    "signed_improvement_ci95_lower": improvement["ci95_t_df4_lower"],
                    "signed_improvement_ci95_upper": improvement["ci95_t_df4_upper"],
                    "relative_signed_improvement_mean": (
                        relative["mean"] if relative is not None else ""
                    ),
                    "paired_effect_size_dz": improvement["paired_effect_size_dz"],
                }
            )
    summary = {
        "schema": "controlled_paired_training_replicate_statistics_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS_CONDITIONAL_PAIRED_REPLICATE_INFERENCE",
        "sources": sources,
        "statistical_contract": {
            "pair_count": PAIR_COUNT,
            "primary_interval": "Student-t interval over paired training-replicate deltas",
            "degrees_of_freedom": 4,
            "t_critical_0p975": T_CRITICAL_975_DF4,
            "target_row_bootstrap_used": False,
            "deployment_population_ci_claim": False,
            "conditional_on_shared_historical_pool": True,
            "conditional_on_single_shared_fref_for_rq_i": True,
            "single_fref_randomness_in_interval": False,
            "small_subset_pairwise_dependence_disclosed": True,
            "two_points_establish_scaling_law": False
        },
        "results": results,
        "figures": figure_records,
        "outputs": {
            "flat_csv": str(flat_path),
            "flat_csv_sha256": _sha256(flat_path),
        },
    }
    summary_path = out_dir / "controlled_paired_statistics_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={summary['overall_status']}")
    print(f"metric_group_count={len(results)}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
