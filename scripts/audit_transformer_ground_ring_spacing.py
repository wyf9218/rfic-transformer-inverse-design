#!/usr/bin/env python3
"""Audit ground-ring spacing as a size-normalized RF diagnostic.

The audit uses the shield margin recorded on each real-EMX geometry row and
normalizes it by that row's largest transformer outer dimension.  The
one-third-diameter value is an advisory literature heuristic, not a DRC rule
and not an automatic dataset rejection criterion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from array import array
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_OUTER_COLUMNS = (
    "geom__primary_outer_width_um",
    "geom__primary_outer_height_um",
    "geom__secondary_outer_width_um",
    "geom__secondary_outer_height_um",
)
FEATURE_CANDIDATES = {
    "lp_nh": ("input__lp_nh_center", "lp_nh_center", "lp_nh"),
    "ls_nh": ("input__ls_nh_center", "ls_nh_center", "ls_nh"),
    "q": ("input__q_center", "q_center", "q_min_center", "q_min"),
    "k_abs": ("input__k_abs_center", "k_abs_center", "k_abs"),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(args.training_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "ground_ring_spacing_audit_summary.json"
    rows_path = out_dir / "ground_ring_spacing_audit_rows.csv"
    histogram_path = out_dir / "ground_ring_spacing_ratio_histogram.png"
    response_path = out_dir / "ground_ring_spacing_response_scatter.png"
    report_path = out_dir / "ground_ring_spacing_audit_report.md"

    outer_columns = _split_columns(args.outer_columns)
    headers = set(_read_headers(source))
    feature_columns = {
        name: tuple(column for column in candidates if column in headers)
        for name, candidates in FEATURE_CANDIDATES.items()
    }
    arrays, sampled_records, rejected_rows, rejected_count, source_row_count, margin_evidence_counts = _scan_dataset(
        source,
        outer_columns,
        feature_columns,
        args,
        sample_limit=max(int(args.max_row_artifact), int(args.max_plot_points)),
    )
    valid_row_count = len(arrays["margin_to_max_outer_ratio"])
    analysis = _summarize_arrays(arrays, float(args.recommended_margin_to_diameter_ratio))
    ratios = arrays["margin_to_max_outer_ratio"]
    margin_input_available = args.shield_margin_column in headers or (
        args.recover_margin_from_evaluation_metadata and args.touchstone_column in headers
    )
    checks = {
        "training_csv_exists": source.is_file(),
        "required_geometry_columns_present": bool(headers)
        and all(column in headers for column in outer_columns)
        and margin_input_available,
        "valid_rows_meet_minimum": valid_row_count >= int(args.min_rows),
        "all_source_rows_audited": source_row_count > 0
        and valid_row_count == source_row_count
        and rejected_count == 0,
        "all_spacing_ratios_finite_positive": bool(ratios.size)
        and bool(np.all(np.isfinite(ratios)))
        and bool(np.all(ratios > 0.0)),
        "shield_margin_is_row_level_evidence": bool(ratios.size)
        and sum(margin_evidence_counts.values()) == valid_row_count,
    }
    if all(checks.values()):
        overall_status = "PASS"
        below_fraction = float(analysis.get("below_recommended_fraction") or 0.0)
        if below_fraction <= float(args.max_below_recommended_fraction):
            decision = "ADVISORY_GROUND_RING_SPACING_HEURISTIC_SATISFIED"
        else:
            decision = "REVIEW_LOW_MARGIN_STRATUM_WITH_REAL_EMX_DO_NOT_AUTO_REJECT"
    else:
        overall_status = (
            "WAITING_FOR_COMPLETE_GEOMETRY_EVIDENCE"
            if valid_row_count < int(args.min_rows)
            else "FAIL"
        )
        decision = "FIX_GROUND_RING_SPACING_AUDIT_INPUTS"

    row_artifact_records = _deterministic_spread_sample(sampled_records, int(args.max_row_artifact))
    plot_records = _deterministic_spread_sample(sampled_records, int(args.max_plot_points))
    _write_csv(rows_path, row_artifact_records)
    histogram_status = _plot_histogram(
        histogram_path,
        ratios,
        float(args.recommended_margin_to_diameter_ratio),
    )
    response_status = _plot_response_scatter(response_path, plot_records, feature_columns)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "training_csv": str(source),
        "training_csv_sha256": _sha256(source) if source.is_file() else "",
        "source_row_count": source_row_count,
        "valid_row_count": valid_row_count,
        "row_artifact_count": len(row_artifact_records),
        "response_plot_point_count": len(plot_records),
        "rejected_row_count": rejected_count,
        "rejected_rows": rejected_rows,
        "geometry_contract": {
            "outer_dimension_columns": outer_columns,
            "shield_margin_column": args.shield_margin_column,
            "touchstone_column": args.touchstone_column,
            "evaluation_metadata_fallback_enabled": bool(args.recover_margin_from_evaluation_metadata),
            "evaluation_metadata_relative_path": args.metadata_geometry_relative_path,
            "metadata_outer_match_tolerance_um": float(args.metadata_outer_match_tolerance_um),
            "margin_evidence_counts": dict(sorted(margin_evidence_counts.items())),
            "feature_columns_found": feature_columns,
            "recommended_margin_to_max_outer_ratio": float(args.recommended_margin_to_diameter_ratio),
            "recommended_interpretation": "advisory RF rule of thumb, not DRC and not an automatic acceptance gate",
            "full_dataset_scan": True,
            "row_artifact_sampling": "deterministic seeded reservoir; full statistics are computed before sampling",
            "sample_seed": int(args.sample_seed),
        },
        "checks": checks,
        "analysis": analysis,
        "artifacts": {
            "summary": str(summary_path),
            "rows": str(rows_path),
            "ratio_histogram": str(histogram_path) if histogram_status == "PASS" else "",
            "ratio_histogram_status": histogram_status,
            "response_scatter": str(response_path) if response_status == "PASS" else "",
            "response_scatter_status": response_status,
            "report": str(report_path),
        },
        "scientific_boundary": (
            "Integrated Transformers: Basic Concepts, Design Intuition and Practical Considerations describes a "
            "ground ring placed too close to the coils as a shorted third winding that can reduce inductance and Q, "
            "and presents roughly one-third of the outer diameter as a rule of thumb. This audit measures that "
            "geometry ratio and response stratification only. A correlation is confounded by transformer size and "
            "does not establish causality; DRC, full-band real EMX, and sampled HFSS remain mandatory."
        ),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--outer-columns", default=",".join(DEFAULT_OUTER_COLUMNS))
    parser.add_argument("--shield-margin-column", default="geom__shield_margin_um")
    parser.add_argument("--touchstone-column", default="touchstone_path")
    parser.add_argument("--recover-margin-from-evaluation-metadata", action="store_true")
    parser.add_argument("--metadata-geometry-relative-path", default="layout/geometry.json")
    parser.add_argument("--metadata-outer-match-tolerance-um", type=float, default=1.0e-6)
    parser.add_argument("--min-rows", type=int, default=100)
    parser.add_argument("--recommended-margin-to-diameter-ratio", type=float, default=1.0 / 3.0)
    parser.add_argument("--max-below-recommended-fraction", type=float, default=0.0)
    parser.add_argument("--max-row-artifact", type=int, default=20000)
    parser.add_argument("--max-plot-points", type=int, default=20000)
    parser.add_argument("--sample-seed", type=int, default=20260711)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_rows < 2:
        parser.error("--min-rows must be at least 2")
    if args.recommended_margin_to_diameter_ratio <= 0.0:
        parser.error("--recommended-margin-to-diameter-ratio must be positive")
    if not 0.0 <= args.max_below_recommended_fraction <= 1.0:
        parser.error("--max-below-recommended-fraction must be in [0,1]")
    if args.max_row_artifact < 1 or args.max_plot_points < 1:
        parser.error("row-artifact and plot-point limits must be positive")
    if args.metadata_outer_match_tolerance_um < 0.0:
        parser.error("--metadata-outer-match-tolerance-um must be nonnegative")
    metadata_path = Path(args.metadata_geometry_relative_path)
    if metadata_path.is_absolute() or ".." in metadata_path.parts:
        parser.error("--metadata-geometry-relative-path must stay inside the evaluation directory")
    if len(_split_columns(args.outer_columns)) != 4:
        parser.error("--outer-columns must name four primary/secondary width/height columns")
    return args


def _read_headers(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or [])


def _split_columns(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _scan_dataset(
    path: Path,
    outer_columns: list[str],
    feature_columns: dict[str, tuple[str, ...]],
    args: argparse.Namespace,
    *,
    sample_limit: int,
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]], list[dict[str, Any]], int, int, dict[str, int]]:
    storage = {
        "margin_to_max_outer_ratio": array("d"),
        "shield_margin_um": array("d"),
        "max_outer_dimension_um": array("d"),
        **{feature: array("d") for feature in FEATURE_CANDIDATES},
    }
    sampled: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    rejected_count = 0
    source_row_count = 0
    margin_evidence_counts: Counter[str] = Counter()
    rng = np.random.default_rng(int(args.sample_seed))
    if not path.is_file():
        return {key: np.empty(0, dtype=float) for key in storage}, sampled, rejected_rows, 0, 0, {}
    threshold = float(args.recommended_margin_to_diameter_ratio)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row_index, row in enumerate(csv.DictReader(handle)):
            source_row_count += 1
            try:
                record = _record_from_row(
                    row_index,
                    row,
                    outer_columns,
                    feature_columns,
                    args,
                    threshold,
                    source_parent=path.parent,
                )
                storage["margin_to_max_outer_ratio"].append(float(record["margin_to_max_outer_ratio"]))
                storage["shield_margin_um"].append(float(record["shield_margin_um"]))
                storage["max_outer_dimension_um"].append(float(record["max_outer_dimension_um"]))
                for feature in FEATURE_CANDIDATES:
                    value = _finite(record.get(feature))
                    storage[feature].append(math.nan if value is None else value)
                margin_evidence_counts[str(record["shield_margin_source"])] += 1
                valid_seen = len(storage["margin_to_max_outer_ratio"])
                if len(sampled) < sample_limit:
                    sampled.append(record)
                else:
                    replacement = int(rng.integers(0, valid_seen))
                    if replacement < sample_limit:
                        sampled[replacement] = record
            except Exception as exc:  # noqa: BLE001 - exact row rejection is audit evidence.
                rejected_count += 1
                if len(rejected_rows) < 100:
                    rejected_rows.append(
                        {"source_row_index": row_index, "reason": f"{type(exc).__name__}: {exc}"}
                    )
    sampled.sort(key=lambda item: int(item["source_row_index"]))
    arrays = {key: np.asarray(values, dtype=float) for key, values in storage.items()}
    return arrays, sampled, rejected_rows, rejected_count, source_row_count, dict(margin_evidence_counts)


def _record_from_row(
    row_index: int,
    row: dict[str, str],
    outer_columns: list[str],
    feature_columns: dict[str, tuple[str, ...]],
    args: argparse.Namespace,
    threshold: float,
    *,
    source_parent: Path,
) -> dict[str, Any]:
    dimensions = [_finite(row.get(column)) for column in outer_columns]
    if any(value is None or value <= 0.0 for value in dimensions):
        raise ValueError("outer dimensions must be finite and positive")
    finite_dimensions = [float(value) for value in dimensions if value is not None]
    margin, margin_source, margin_evidence_path = _resolve_shield_margin(
        row,
        finite_dimensions,
        args,
        source_parent=source_parent,
    )
    maximum = max(float(value) for value in dimensions if value is not None)
    ratio = float(margin) / maximum
    record: dict[str, Any] = {
        "source_row_index": row_index,
        "shield_margin_source": margin_source,
        "shield_margin_evidence_path": margin_evidence_path,
        "shield_margin_um": float(margin),
        "max_outer_dimension_um": maximum,
        "margin_to_max_outer_ratio": ratio,
        "recommended_margin_um": threshold * maximum,
        "margin_minus_recommended_um": float(margin) - threshold * maximum,
        "meets_advisory_one_third_rule": ratio + 1.0e-12 >= threshold,
    }
    for column, value in zip(outer_columns, dimensions, strict=True):
        record[column] = float(value) if value is not None else None
    for feature, columns in feature_columns.items():
        record[feature] = _first_finite(row, columns)
    return record


def _first_finite(row: dict[str, str], columns: tuple[str, ...]) -> float | None:
    for column in columns:
        value = _finite(row.get(column))
        if value is not None:
            return value
    return None


def _resolve_shield_margin(
    row: dict[str, str],
    dimensions: list[float],
    args: argparse.Namespace,
    *,
    source_parent: Path,
) -> tuple[float, str, str]:
    margin = _finite(row.get(args.shield_margin_column))
    if margin is not None and margin > 0.0:
        return float(margin), f"csv:{args.shield_margin_column}", ""
    if not args.recover_margin_from_evaluation_metadata:
        raise ValueError("row-level shield margin is missing or nonpositive")

    touchstone_raw = str(row.get(args.touchstone_column) or "").strip()
    if not touchstone_raw:
        raise ValueError(f"missing {args.touchstone_column} for evaluation-metadata recovery")
    touchstone_path = Path(touchstone_raw).expanduser()
    if not touchstone_path.is_absolute():
        touchstone_path = source_parent / touchstone_path
    evaluation_dir = touchstone_path.parent.parent
    metadata_path = evaluation_dir / args.metadata_geometry_relative_path
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing evaluation geometry metadata: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    shield = metadata.get("shield") or {}
    if shield.get("enabled") is not True:
        raise ValueError(f"evaluation metadata does not prove an enabled shield: {metadata_path}")
    metadata_margin = _finite(shield.get("margin_um"))
    if metadata_margin is None or metadata_margin <= 0.0:
        raise ValueError(f"evaluation metadata shield.margin_um is invalid: {metadata_path}")

    metadata_dimensions = [
        _finite(((metadata.get("primary") or {}).get("geometry") or {}).get("outer_width_um")),
        _finite(((metadata.get("primary") or {}).get("geometry") or {}).get("outer_height_um")),
        _finite(((metadata.get("secondary") or {}).get("geometry") or {}).get("outer_width_um")),
        _finite(((metadata.get("secondary") or {}).get("geometry") or {}).get("outer_height_um")),
    ]
    if any(value is None for value in metadata_dimensions):
        raise ValueError(f"evaluation metadata outer dimensions are incomplete: {metadata_path}")
    tolerance = float(args.metadata_outer_match_tolerance_um)
    if any(
        abs(float(actual) - expected) > tolerance
        for actual, expected in zip(metadata_dimensions, dimensions, strict=True)
    ):
        raise ValueError(f"evaluation metadata outer dimensions do not match the dataset row: {metadata_path}")
    return (
        float(metadata_margin),
        f"evaluation_metadata:{args.metadata_geometry_relative_path}:shield.margin_um",
        str(metadata_path.resolve()),
    )


def _summarize_arrays(arrays: dict[str, np.ndarray], threshold: float) -> dict[str, Any]:
    ratios = arrays["margin_to_max_outer_ratio"]
    margins = arrays["shield_margin_um"]
    diameters = arrays["max_outer_dimension_um"]
    below_mask = ratios + 1.0e-12 < threshold
    at_or_above_mask = ~below_mask
    feature_diagnostics: dict[str, Any] = {}
    for feature in FEATURE_CANDIDATES:
        values = arrays[feature]
        finite_mask = np.isfinite(values)
        if not np.any(finite_mask):
            feature_diagnostics[feature] = {"available": False}
            continue
        feature_diagnostics[feature] = {
            "available": True,
            "all_rows": _stats(values[finite_mask]),
            "below_recommended": _stats(values[finite_mask & below_mask]),
            "at_or_above_recommended": _stats(values[finite_mask & at_or_above_mask]),
            "pearson_with_spacing_ratio": _pearson(ratios[finite_mask], values[finite_mask]),
            "correlation_boundary": "diagnostic association only; transformer size is a confounder",
        }
    return {
        "spacing_ratio": _stats(ratios),
        "shield_margin_um": _stats(margins),
        "max_outer_dimension_um": _stats(diameters),
        "recommended_ratio": threshold,
        "below_recommended_count": int(np.count_nonzero(below_mask)),
        "below_recommended_fraction": float(np.mean(below_mask)) if ratios.size else None,
        "at_or_above_recommended_count": int(np.count_nonzero(at_or_above_mask)),
        "unique_shield_margin_count": len(set(float(value) for value in margins)),
        "feature_diagnostics": feature_diagnostics,
    }


def _finite_values(records: list[dict[str, Any]], key: str) -> np.ndarray:
    values = []
    for item in records:
        value = _finite(item.get(key))
        if value is not None:
            values.append(value)
    return np.asarray(values, dtype=float)


def _deterministic_spread_sample(records: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if len(records) <= maximum:
        return list(records)
    indices = np.linspace(0, len(records) - 1, maximum, dtype=int)
    return [records[int(index)] for index in indices]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stats(values: np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        return {"count": 0, "min": None, "q05": None, "q25": None, "median": None, "q75": None, "q95": None, "max": None}
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "q05": float(np.quantile(array, 0.05)),
        "q25": float(np.quantile(array, 0.25)),
        "median": float(np.median(array)),
        "q75": float(np.quantile(array, 0.75)),
        "q95": float(np.quantile(array, 0.95)),
        "max": float(np.max(array)),
    }


def _pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3 or len(left) != len(right) or float(np.std(left)) <= 0.0 or float(np.std(right)) <= 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plot_histogram(path: Path, values: np.ndarray, threshold: float) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return "UNAVAILABLE:NO_VALID_ROWS"
    figure, axis = plt.subplots(figsize=(7.8, 4.8), dpi=180)
    axis.hist(values, bins=min(30, max(8, int(math.sqrt(len(values))))), color="#2268b2", edgecolor="white")
    axis.axvline(threshold, color="#c43c35", linestyle="--", label=f"Advisory ratio = {threshold:.3f}")
    axis.set_xlabel("Ground-ring margin / maximum outer dimension")
    axis.set_ylabel("Real EMX geometry rows")
    axis.set_title("Ground-ring spacing ratio distribution")
    axis.grid(True, alpha=0.2)
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _plot_response_scatter(
    path: Path,
    records: list[dict[str, Any]],
    feature_columns: dict[str, tuple[str, ...]],
) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    available = [
        feature
        for feature, columns in feature_columns.items()
        if columns and _finite_values(records, feature).size
    ]
    if not available:
        return "UNAVAILABLE:NO_PHYSICAL_FEATURE_COLUMNS"
    figure, axes = plt.subplots(1, len(available), figsize=(4.2 * len(available), 3.8), dpi=180, squeeze=False)
    for axis, feature in zip(axes[0], available, strict=True):
        pairs = [
            (float(item["margin_to_max_outer_ratio"]), float(item[feature]))
            for item in records
            if _finite(item.get(feature)) is not None
        ]
        axis.scatter([item[0] for item in pairs], [item[1] for item in pairs], s=8, alpha=0.35, color="#16836b")
        axis.set_xlabel("Margin / max outer dimension")
        axis.set_ylabel(feature)
        axis.grid(True, alpha=0.2)
    figure.suptitle("Size-normalized ground spacing versus physical features (diagnostic only)")
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return "PASS"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_report(data: dict[str, Any]) -> str:
    analysis = data.get("analysis") or {}
    ratio = analysis.get("spacing_ratio") or {}
    return "\n".join(
        [
            "# Ground-ring spacing audit",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Real EMX geometry rows: `{data.get('valid_row_count')}`",
            f"- Margin/max-outer ratio median: `{ratio.get('median')}`",
            f"- Margin/max-outer ratio minimum: `{ratio.get('min')}`",
            f"- Below advisory one-third ratio: `{analysis.get('below_recommended_fraction')}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
