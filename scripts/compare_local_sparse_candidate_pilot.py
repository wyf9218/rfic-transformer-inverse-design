#!/usr/bin/env python3
"""Compare global-LHS and sparse-target-local candidate coverage pilots."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--variants", default="global100,mixed70")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    results = [_variant_summary(root, variant) for variant in variants]
    by_name = {item["variant"]: item for item in results}
    baseline = by_name.get("global100")
    mixed = by_name.get("mixed70")
    comparison: dict[str, Any] = {}
    if baseline and mixed:
        comparison = {
            "reachable_target_gain": mixed["reachable_target_count"] - baseline["reachable_target_count"],
            "reachable_target_ratio": _ratio(mixed["reachable_target_count"], baseline["reachable_target_count"]),
            "inside_capacity_gain": mixed["reachable_inside_candidate_capacity"] - baseline["reachable_inside_candidate_capacity"],
            "inside_capacity_ratio": _ratio(
                mixed["reachable_inside_candidate_capacity"], baseline["reachable_inside_candidate_capacity"]
            ),
            "unique_geometry_ratio_mixed": _ratio(mixed["unique_geometry_count"], mixed["candidate_count"]),
            "training_geometry_overlap_mixed": mixed["training_geometry_overlap_count"],
        }
    checks = [
        _check("all_variants_have_candidates", all(item["candidate_count"] > 0 for item in results), results),
        _check("all_variants_have_selection_summary", all(item["selection_status"] == "PASS" for item in results), results),
        _check(
            "mixed_reaches_more_targets_than_global",
            bool(baseline and mixed and mixed["reachable_target_count"] > baseline["reachable_target_count"]),
            comparison,
        ),
        _check(
            "mixed_has_more_inside_capacity_than_global",
            bool(
                baseline
                and mixed
                and mixed["reachable_inside_candidate_capacity"] > baseline["reachable_inside_candidate_capacity"]
            ),
            comparison,
        ),
        _check(
            "mixed_geometry_candidates_are_unique",
            bool(mixed and mixed["unique_geometry_count"] == mixed["candidate_count"]),
            None if not mixed else f"{mixed['unique_geometry_count']}/{mixed['candidate_count']}",
        ),
        _check(
            "mixed_geometry_candidates_do_not_repeat_training_rows",
            bool(mixed and mixed["training_geometry_overlap_count"] == 0),
            None if not mixed else mixed["training_geometry_overlap_count"],
        ),
    ]
    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_MIXED_GLOBAL_AND_LOCAL_SPARSE_TARGET_CANDIDATES_FOR_NEXT_ACQUISITION"
        if status == "PASS"
        else "DO_NOT_PROMOTE_LOCAL_SPARSE_CANDIDATE_METHOD_YET",
        "root": str(root),
        "variants": results,
        "comparison": comparison,
        "checks": checks,
        "scientific_boundary": (
            "This compares surrogate candidate coverage only. Real physical-feature improvement must be confirmed "
            "from subsequent EMX Touchstone labels before the dataset is called more uniform."
        ),
    }
    out = Path(args.out).expanduser().resolve() if args.out else root / "local_sparse_candidate_pilot_comparison.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if status == "PASS" else 2


def _variant_summary(root: Path, name: str) -> dict[str, Any]:
    candidate_csv = root / name / "candidate_physical_feature_predictions.csv"
    prediction_summary = _json(root / name / "candidate_physical_feature_prediction_summary.json")
    selection_summary = _json(root / f"{name}_selection" / "physical_feature_targeted_candidate_selection_summary.json")
    selection_csv = root / f"{name}_selection" / "physical_feature_targeted_candidate_selection.csv"
    candidates = _csv(candidate_csv)
    selected = _csv(selection_csv)
    q = np.asarray([float(row["pred_q_center"]) for row in candidates], dtype=float)
    k = np.asarray([float(row["pred_k_abs_center"]) for row in candidates], dtype=float)
    geometry_columns = list(prediction_summary.get("geometry_columns") or [])
    geometry_keys = {
        tuple(round(float(row[column]), 12) for column in geometry_columns)
        for row in candidates
    }
    training_source = prediction_summary.get("dataset_source") if isinstance(prediction_summary.get("dataset_source"), dict) else {}
    training_path = Path(str(training_source.get("path") or ""))
    training_rows = _csv(training_path) if training_path.is_file() else []
    training_geometry_keys = {
        tuple(round(float(row[column]), 12) for column in geometry_columns)
        for row in training_rows
        if all(row.get(column) not in (None, "") for column in geometry_columns)
    }
    target_q = np.asarray([float(row["target_q_center"]) for row in selected], dtype=float) if selected else np.empty(0)
    diagnostics = selection_summary.get("selection_diagnostics") or {}
    return {
        "variant": name,
        "prediction_status": prediction_summary.get("overall_status"),
        "selection_status": selection_summary.get("overall_status"),
        "candidate_count": len(candidates),
        "unique_geometry_count": len(geometry_keys),
        "training_geometry_overlap_count": len(geometry_keys.intersection(training_geometry_keys)),
        "candidate_generation": prediction_summary.get("candidate_generation") or {},
        "reachable_target_count": int(diagnostics.get("reachable_target_count") or 0),
        "unreachable_target_count": int(diagnostics.get("unreachable_target_count") or 0),
        "reachable_inside_candidate_capacity": int(diagnostics.get("reachable_inside_candidate_capacity") or 0),
        "selected_inside_target_bin_count": int(selection_summary.get("selected_inside_target_bin_count") or 0),
        "q_quantiles": _quantiles(q),
        "q_counts_5_10_15_20_25": np.histogram(q, bins=[5, 10, 15, 20, 25])[0].astype(int).tolist(),
        "k_abs_quantiles": _quantiles(k),
        "selected_target_q_counts_5_10_15_20_25": np.histogram(target_q, bins=[5, 10, 15, 20, 25])[0]
        .astype(int)
        .tolist(),
    }


def _quantiles(values: np.ndarray) -> dict[str, float]:
    quantiles = np.quantile(values, [0.0, 0.01, 0.5, 0.99, 1.0])
    return {key: float(value) for key, value in zip(("min", "p01", "median", "p99", "max"), quantiles, strict=True)}


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else float(numerator) / float(denominator)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
