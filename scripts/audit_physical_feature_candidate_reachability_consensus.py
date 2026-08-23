#!/usr/bin/env python3
"""Audit candidate-pool evidence for underfilled 4-D physical-feature bins.

The current acquisition selector calls a target reachable when one candidate
prediction falls inside it. This audit asks a stricter question: does candidate
evidence recur across independent CSV batches (or deterministic folds of one
large pool), and do uncertainty intervals remain inside the same target bin?

The classifications describe current surrogate/candidate evidence only. Empty
or inconsistent bins are never declared physically impossible, and this audit
cannot relax final real-EMX uniformity gates automatically.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from physical_feature_prediction_calibration import (  # noqa: E402
    apply_feature_mapping,
    load_approved_calibration,
)


FEATURES = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
ROBUST = "CONSENSUS_ROBUST_CANDIDATE_EVIDENCE"
NOMINAL = "CONSENSUS_NOMINAL_BUT_UNCERTAIN"
SPARSE = "INCONSISTENT_OR_SPARSE_CANDIDATE_EVIDENCE"
NONE = "NO_CURRENT_SURROGATE_CANDIDATE_EVIDENCE"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bins_csv = Path(args.bins_csv).expanduser().resolve()
    candidate_csvs = [Path(value).expanduser().resolve() for value in args.candidate_csv]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    features = tuple(item.strip() for item in args.feature_columns.split(",") if item.strip())
    paths = {
        "summary": out_dir / "candidate_reachability_consensus_summary.json",
        "report": out_dir / "candidate_reachability_consensus_report.md",
        "records": out_dir / "candidate_reachability_consensus_bins.csv",
        "figure": out_dir / "candidate_reachability_consensus.png",
    }
    bins, bin_contract = _load_bins(bins_csv, features)
    calibration_payload: dict[str, Any] | None = None
    calibration_mapping: dict[str, Any] | None = None
    calibration_error = ""
    if args.prediction_calibration_json:
        try:
            calibration_payload, calibration_mapping = load_approved_calibration(
                args.prediction_calibration_json, features
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            calibration_error = f"{type(exc).__name__}: {exc}"

    checks = {
        "bins_csv_exists": bins_csv.is_file(),
        "candidate_csvs_exist": bool(candidate_csvs) and all(path.is_file() for path in candidate_csvs),
        "four_feature_contract": features == FEATURES,
        "bins_contract_valid": bin_contract.get("status") == "PASS" and bool(bins),
        "explicit_calibration_valid": not args.prediction_calibration_json or not calibration_error,
    }
    base = _base_payload(args, bins_csv, candidate_csvs, features, bin_contract, checks, paths)
    base["prediction_calibration"] = {
        "requested": bool(args.prediction_calibration_json),
        "path": str(Path(args.prediction_calibration_json).expanduser().resolve())
        if args.prediction_calibration_json
        else "",
        "sha256": _file_sha(Path(args.prediction_calibration_json).expanduser().resolve())
        if args.prediction_calibration_json
        else None,
        "decision": (calibration_payload or {}).get("decision"),
        "error": calibration_error,
    }
    if not all(checks.values()):
        base.update(
            {
                "overall_status": "FAIL",
                "decision": "FIX_CANDIDATE_REACHABILITY_AUDIT_INPUTS",
                "failure_reasons": [name for name, passed in checks.items() if not passed],
                "classification_counts": {},
                "records": [],
            }
        )
        _write_outputs(base, paths, [], write_plot=False)
        return _finish(base, paths, bool(args.no_fail_exit))

    source_batch_mode = "candidate_csv_as_batch" if len(candidate_csvs) > 1 else "stable_candidate_hash_folds"
    batch_count = len(candidate_csvs) if len(candidate_csvs) > 1 else int(args.pseudo_batches)
    nominal_counts = np.zeros((batch_count, len(bins)), dtype=np.int64)
    robust_counts = np.zeros((batch_count, len(bins)), dtype=np.int64)
    batch_rows = np.zeros(batch_count, dtype=np.int64)
    stats: Counter[str] = Counter()
    source_records: list[dict[str, Any]] = []
    index_to_position = {tuple(item["index"]): position for position, item in enumerate(bins)}
    edges = bin_contract["edges"]
    for source_index, candidate_csv in enumerate(candidate_csvs):
        record, source_stats = _scan_candidates(
            candidate_csv,
            source_index,
            len(candidate_csvs),
            batch_count,
            features,
            edges,
            bins,
            index_to_position,
            nominal_counts,
            robust_counts,
            batch_rows,
            float(args.uncertainty_z),
            calibration_mapping,
        )
        source_records.append(record)
        stats.update(source_stats)
    checks.update(
        {
            "minimum_batch_count": batch_count >= int(args.min_batches),
            "minimum_candidate_rows": int(stats["accepted_candidate_rows"])
            >= int(args.min_candidate_rows),
            "all_batches_nonempty": bool(batch_rows.size) and bool(np.all(batch_rows > 0)),
            "uncertainty_provenance_present": int(stats["accepted_candidate_rows"]) > 0
            and int(stats["missing_or_invalid_uncertainty_rows"]) == 0,
        }
    )
    if not all(checks.values()):
        base["checks"] = checks
        base.update(
            {
                "overall_status": "WAITING",
                "decision": "WAIT_FOR_COMPLETE_MULTI_BATCH_CANDIDATE_EVIDENCE",
                "failure_reasons": [name for name, passed in checks.items() if not passed],
                "input_stats": dict(stats),
                "source_records": source_records,
                "batch_count": batch_count,
                "batch_row_counts": batch_rows.tolist(),
                "classification_counts": {},
                "records": [],
            }
        )
        _write_outputs(base, paths, [], write_plot=False)
        return _finish(base, paths, bool(args.no_fail_exit))

    records = _classify_bins(
        bins,
        nominal_counts,
        robust_counts,
        int(args.min_nominal_candidates_per_batch),
        int(args.min_robust_candidates_per_batch),
        float(args.min_nominal_batch_fraction),
        float(args.min_robust_batch_fraction),
    )
    classification_counts = Counter(
        record["candidate_evidence_class"] for record in records if record["status"] == "underfilled"
    )
    payload = _base_payload(args, bins_csv, candidate_csvs, features, bin_contract, checks, paths)
    payload.update(
        {
            "overall_status": "PASS",
            "decision": "USE_AS_ADVISORY_REACHABILITY_CONSENSUS_NOT_PHYSICAL_FEASIBILITY_PROOF",
            "candidate_batch_mode": source_batch_mode,
            "batch_count": batch_count,
            "batch_row_counts": batch_rows.tolist(),
            "input_stats": dict(stats),
            "source_records": source_records,
            "classification_counts": dict(classification_counts),
            "underfilled_bin_count": int(sum(record["status"] == "underfilled" for record in records)),
            "records": records,
            "prediction_calibration": base["prediction_calibration"],
        }
    )
    _write_outputs(payload, paths, records, write_plot=not bool(args.no_plots))
    return _finish(payload, paths, bool(args.no_fail_exit))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bins-csv", required=True)
    parser.add_argument("--candidate-csv", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=",".join(FEATURES))
    parser.add_argument("--prediction-calibration-json")
    parser.add_argument("--pseudo-batches", type=int, default=4)
    parser.add_argument("--min-batches", type=int, default=4)
    parser.add_argument("--min-candidate-rows", type=int, default=1000)
    parser.add_argument("--uncertainty-z", type=float, default=1.0)
    parser.add_argument("--min-nominal-candidates-per-batch", type=int, default=2)
    parser.add_argument("--min-robust-candidates-per-batch", type=int, default=1)
    parser.add_argument("--min-nominal-batch-fraction", type=float, default=0.75)
    parser.add_argument("--min-robust-batch-fraction", type=float, default=0.50)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if int(args.pseudo_batches) < 2 or int(args.min_batches) < 2:
        parser.error("pseudo/min batches must be at least 2")
    if float(args.uncertainty_z) < 0.0:
        parser.error("--uncertainty-z must be nonnegative")
    for name in ("min_nominal_batch_fraction", "min_robust_batch_fraction"):
        if not 0.0 <= float(getattr(args, name)) <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be in [0,1]")
    return args


def _load_bins(path: Path, features: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        return [], {"status": "FAIL", "error": "bins_csv_missing"}
    rows: list[dict[str, Any]] = []
    intervals: dict[str, dict[int, tuple[float, float]]] = {feature: {} for feature in features}
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for raw in csv.DictReader(handle):
                index = tuple(int(float(raw[f"{feature}__bin"])) for feature in features)
                item: dict[str, Any] = {
                    "bin_key": str(raw.get("bin_key") or "|".join(str(value) for value in index)),
                    "index": index,
                    "current_count": int(float(raw.get("current_count") or 0)),
                    "target_count": int(float(raw.get("target_count") or 0)),
                    "deficit": int(float(raw.get("deficit") or 0)),
                    "status": str(raw.get("status") or ""),
                }
                bounds = []
                for axis, feature in enumerate(features):
                    lower = float(raw[f"{feature}__min"])
                    upper = float(raw[f"{feature}__max"])
                    if not math.isfinite(lower) or not math.isfinite(upper) or upper <= lower:
                        raise ValueError(f"invalid interval for {feature}")
                    intervals[feature][index[axis]] = (lower, upper)
                    bounds.append((lower, upper))
                item["bounds"] = tuple(bounds)
                rows.append(item)
    except (KeyError, TypeError, ValueError) as exc:
        return [], {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    if not rows or any(not values for values in intervals.values()):
        return [], {"status": "FAIL", "error": "empty bins or feature intervals"}
    edges: dict[str, list[float]] = {}
    for feature, values in intervals.items():
        ordered = [values[index] for index in sorted(values)]
        for left, right in zip(ordered, ordered[1:]):
            if not math.isclose(left[1], right[0], rel_tol=1.0e-9, abs_tol=1.0e-12):
                return [], {"status": "FAIL", "error": f"noncontiguous intervals for {feature}"}
        edges[feature] = [ordered[0][0], *[item[1] for item in ordered]]
    return rows, {
        "status": "PASS",
        "bin_count": len(rows),
        "edges": edges,
        "bin_index_sha256": hashlib.sha256(
            ("\n".join(item["bin_key"] for item in rows) + "\n").encode("utf-8")
        ).hexdigest(),
    }


def _scan_candidates(
    path: Path,
    source_index: int,
    source_count: int,
    batch_count: int,
    features: tuple[str, ...],
    edges: dict[str, list[float]],
    bins: list[dict[str, Any]],
    index_to_position: dict[tuple[int, ...], int],
    nominal_counts: np.ndarray,
    robust_counts: np.ndarray,
    batch_rows: np.ndarray,
    uncertainty_z: float,
    calibration_mapping: dict[str, Any] | None,
) -> tuple[dict[str, Any], Counter[str]]:
    stats: Counter[str] = Counter()
    source_record: dict[str, Any] = {
        "path": str(path),
        "sha256": _file_sha(path),
        "source_index": source_index,
    }
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        prediction_fields = _resolve_fields(fields, features, "pred_")
        uncertainty_fields = _resolve_fields(fields, features, "pred_uncertainty_")
        source_record["prediction_fields"] = prediction_fields
        source_record["uncertainty_fields"] = uncertainty_fields
        if len(prediction_fields) != len(features) or len(uncertainty_fields) != len(features):
            stats["source_contract_failure_count"] += 1
            return source_record, stats
        for row_number, row in enumerate(reader):
            stats["candidate_rows"] += 1
            values = [_finite(row.get(prediction_fields[feature])) for feature in features]
            uncertainties = [_finite(row.get(uncertainty_fields[feature])) for feature in features]
            if any(value is None for value in values):
                stats["missing_or_invalid_prediction_rows"] += 1
                continue
            if any(value is None or float(value) < 0.0 for value in uncertainties):
                stats["missing_or_invalid_uncertainty_rows"] += 1
                continue
            effective_values = [float(value) for value in values if value is not None]
            effective_uncertainties = [float(value) for value in uncertainties if value is not None]
            if calibration_mapping is not None:
                for axis, feature in enumerate(features):
                    mapping = calibration_mapping[feature]
                    center = float(
                        apply_feature_mapping(np.asarray([effective_values[axis]], dtype=float), mapping)[0]
                    )
                    low_raw = effective_values[axis] - uncertainty_z * effective_uncertainties[axis]
                    high_raw = effective_values[axis] + uncertainty_z * effective_uncertainties[axis]
                    mapped = apply_feature_mapping(np.asarray([low_raw, high_raw], dtype=float), mapping)
                    effective_values[axis] = center
                    mapped_half_width = max(
                        abs(center - float(mapped[0])),
                        abs(float(mapped[1]) - center),
                    )
                    effective_uncertainties[axis] = (
                        mapped_half_width / uncertainty_z if uncertainty_z > 0.0 else 0.0
                    )
            index = tuple(_bin_index(effective_values[axis], edges[feature]) for axis, feature in enumerate(features))
            if any(value is None for value in index):
                stats["prediction_outside_declared_bins"] += 1
                continue
            integer_index = tuple(int(value) for value in index if value is not None)
            position = index_to_position.get(integer_index)
            if position is None:
                stats["prediction_bin_missing_from_plan"] += 1
                continue
            candidate_id = str(
                row.get("candidate_id")
                or row.get("sample_id")
                or row.get("evaluation")
                or f"{source_index}:{row_number}"
            )
            batch = source_index if source_count > 1 else _stable_batch(candidate_id, batch_count)
            nominal_counts[batch, position] += 1
            batch_rows[batch] += 1
            bounds = bins[position]["bounds"]
            robust = all(
                bounds[axis][0]
                <= effective_values[axis] - uncertainty_z * effective_uncertainties[axis]
                and effective_values[axis] + uncertainty_z * effective_uncertainties[axis]
                <= bounds[axis][1]
                for axis in range(len(features))
            )
            if robust:
                robust_counts[batch, position] += 1
            stats["accepted_candidate_rows"] += 1
            stats["robust_candidate_rows"] += int(robust)
    return source_record, stats


def _resolve_fields(fields: set[str], features: tuple[str, ...], prefix: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for feature in features:
        aliases = (f"{prefix}{feature}", f"candidate__{prefix}{feature}", f"queue__{prefix}{feature}")
        for alias in aliases:
            if alias in fields:
                result[feature] = alias
                break
    return result


def _classify_bins(
    bins: list[dict[str, Any]],
    nominal_counts: np.ndarray,
    robust_counts: np.ndarray,
    min_nominal: int,
    min_robust: int,
    nominal_fraction_threshold: float,
    robust_fraction_threshold: float,
) -> list[dict[str, Any]]:
    records = []
    batch_count = nominal_counts.shape[0]
    for position, item in enumerate(bins):
        nominal = nominal_counts[:, position]
        robust = robust_counts[:, position]
        nominal_fraction = float(np.mean(nominal >= int(min_nominal)))
        robust_fraction = float(np.mean(robust >= int(min_robust)))
        if nominal_fraction >= nominal_fraction_threshold and robust_fraction >= robust_fraction_threshold:
            classification = ROBUST
        elif nominal_fraction >= nominal_fraction_threshold:
            classification = NOMINAL
        elif int(np.sum(nominal)) > 0:
            classification = SPARSE
        else:
            classification = NONE
        record: dict[str, Any] = {
            "bin_key": item["bin_key"],
            "status": item["status"],
            "current_count": item["current_count"],
            "target_count": item["target_count"],
            "deficit": item["deficit"],
            "candidate_evidence_class": classification,
            "batch_count": batch_count,
            "nominal_batch_fraction": nominal_fraction,
            "robust_batch_fraction": robust_fraction,
            "nominal_total_candidates": int(np.sum(nominal)),
            "robust_total_candidates": int(np.sum(robust)),
            "nominal_min_per_batch": int(np.min(nominal)),
            "nominal_median_per_batch": float(np.median(nominal)),
            "nominal_max_per_batch": int(np.max(nominal)),
            "robust_min_per_batch": int(np.min(robust)),
            "robust_median_per_batch": float(np.median(robust)),
            "robust_max_per_batch": int(np.max(robust)),
        }
        for axis, feature in enumerate(FEATURES):
            record[f"{feature}__bin"] = item["index"][axis]
            record[f"{feature}__min"] = item["bounds"][axis][0]
            record[f"{feature}__max"] = item["bounds"][axis][1]
        records.append(record)
    return records


def _bin_index(value: float, edges: list[float]) -> int | None:
    if not math.isfinite(value) or value < edges[0] or value > edges[-1]:
        return None
    if math.isclose(value, edges[-1], rel_tol=1.0e-12, abs_tol=1.0e-15):
        return len(edges) - 2
    index = bisect.bisect_right(edges, value) - 1
    return index if 0 <= index < len(edges) - 1 else None


def _stable_batch(candidate_id: str, count: int) -> int:
    value = int.from_bytes(hashlib.sha256(candidate_id.encode("utf-8")).digest()[:8], "big")
    return int(value % int(count))


def _base_payload(
    args: argparse.Namespace,
    bins_csv: Path,
    candidate_csvs: list[Path],
    features: tuple[str, ...],
    bin_contract: dict[str, Any],
    checks: dict[str, bool],
    paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bins_csv": str(bins_csv),
        "bins_csv_sha256": _file_sha(bins_csv),
        "candidate_csvs": [
            {"path": str(path), "sha256": _file_sha(path), "exists": path.is_file()}
            for path in candidate_csvs
        ],
        "feature_columns": list(features),
        "bin_contract": bin_contract,
        "checks": checks,
        "arguments": vars(args),
        "artifacts": {name: str(path) for name, path in paths.items()},
        "scientific_boundary": (
            "Reachability means recurring current surrogate/candidate evidence, not physical feasibility. No-evidence bins remain part of the final real-EMX uniformity denominator and cannot be deleted, relabeled, or declared impossible without separate real-solver evidence."
        ),
    }


def _write_outputs(
    payload: dict[str, Any],
    paths: dict[str, Path],
    records: list[dict[str, Any]],
    *,
    write_plot: bool,
) -> None:
    _write_records(paths["records"], records)
    plot_status = _write_plot(paths["figure"], records) if write_plot and records else "SKIPPED"
    payload["artifacts"]["figure_status"] = plot_status
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["report"].write_text(_render_report(payload), encoding="utf-8")


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _write_plot(path: Path, records: list[dict[str, Any]]) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{type(exc).__name__}"
    underfilled = [record for record in records if record["status"] == "underfilled"]
    counts = Counter(record["candidate_evidence_class"] for record in underfilled)
    order = (ROBUST, NOMINAL, SPARSE, NONE)
    labels = ("Robust\nconsensus", "Nominal,\nuncertain", "Sparse /\ninconsistent", "No current\nevidence")
    colors = ("#2a9d8f", "#e9c46a", "#f4a261", "#d9d9d9")
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), dpi=180, facecolor="white")
    axes[0].bar(labels, [counts.get(key, 0) for key in order], color=colors)
    axes[0].set_ylabel("Underfilled 4-D bins")
    axes[0].set_title("Candidate-evidence classifications")
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].scatter(
        [record["nominal_batch_fraction"] for record in underfilled],
        [record["robust_batch_fraction"] for record in underfilled],
        c=[colors[order.index(record["candidate_evidence_class"])] for record in underfilled],
        s=24,
        alpha=0.75,
        edgecolors="none",
    )
    axes[1].set_xlim(-0.03, 1.03)
    axes[1].set_ylim(-0.03, 1.03)
    axes[1].set_xlabel("Nominal batch-hit fraction")
    axes[1].set_ylabel("Uncertainty-robust batch-hit fraction")
    axes[1].set_title("Candidate-pool stability")
    axes[1].grid(alpha=0.25)
    figure.suptitle("4-D physical-feature candidate reachability consensus")
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return "PASS"


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate reachability consensus audit",
        "",
        f"- Status: **{payload.get('overall_status')}**",
        f"- Decision: **{payload.get('decision')}**",
        f"- Candidate batch mode: `{payload.get('candidate_batch_mode', 'MISSING')}`",
        f"- Candidate batches: `{payload.get('batch_count', 0)}`",
        f"- Accepted candidate rows: `{(payload.get('input_stats') or {}).get('accepted_candidate_rows', 0)}`",
        "",
        "## Underfilled-bin classifications",
        "",
        "| Classification | Bins |",
        "| --- | ---: |",
    ]
    for key, value in sorted((payload.get("classification_counts") or {}).items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Boundary", "", str(payload.get("scientific_boundary"))])
    return "\n".join(lines) + "\n"


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
