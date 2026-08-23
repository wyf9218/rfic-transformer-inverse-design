#!/usr/bin/env python3
"""Build a disjoint five-arm candidate queue for the next real-EMX round.

The selector combines the existing 4-D, rare-marginal, and pairwise-gap
selector with two explicit exploration arms. Predictions rank candidates only;
all labels and realized-bin assignments still require new EMX S-parameters.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FEATURE_COLUMNS = "lp_nh_center,ls_nh_center,q_center,k_abs_center"
DEFAULT_GEOMETRY_COLUMNS = (
    "geom__primary_outer_width_um,geom__primary_outer_height_um,"
    "geom__secondary_outer_width_um,geom__secondary_outer_height_um,"
    "geom__line_width_um,geom__primary_terminal_y_span_um,"
    "geom__secondary_terminal_y_span_um,geom__offset_um,"
    "geom__primary_feed_extension_um,geom__secondary_feed_extension_um"
)
ARM_SOURCE = {
    "coarse_4d": "four_d_target_bin",
    "rare_marginal": "rare_marginal_real_seed",
    "pairwise_gap": "pairwise_gap_fallback",
    "random_exploration": "random_exploration",
    "geometry_diversity": "geometry_diversity",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_dir = Path(args.plan_dir).expanduser().resolve()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    accepted_dir = Path(args.accepted_dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    targeted_dir = out_dir / "targeted_selection"
    selected_csv = out_dir / "physical_feature_targeted_candidate_selection.csv"
    summary_path = out_dir / "physical_feature_targeted_candidate_selection_summary.json"

    quotas = {
        "coarse_4d": int(args.coarse_4d_max_total),
        "rare_marginal": int(args.rare_marginal_max_total),
        "pairwise_gap": int(args.pairwise_gap_max_total),
        "random_exploration": int(args.random_exploration_max_total),
        "geometry_diversity": int(args.geometry_diversity_max_total),
    }
    checks = {
        "all_quotas_nonnegative": all(value >= 0 for value in quotas.values()),
        "quota_sum_matches_max_total": sum(quotas.values()) == int(args.max_total),
        "candidate_csv_exists": candidate_csv.is_file(),
        "accepted_dataset_csv_exists": (accepted_dir / "dataset_rows.csv").is_file(),
        "plan_targets_exist": (plan_dir / "physical_feature_acquisition_targets.csv").is_file(),
        "plan_bins_exist": (plan_dir / "physical_feature_acquisition_bins.csv").is_file(),
    }
    targeted_status = "NOT_RUN"
    targeted_summary: dict[str, Any] = {}
    combined: list[dict[str, Any]] = []
    diversity_diagnostics: dict[str, Any] = {"status": "NOT_RUN"}
    random_diagnostics: dict[str, Any] = {"status": "NOT_RUN"}
    if all(checks.values()):
        targeted_total = quotas["coarse_4d"] + quotas["rare_marginal"] + quotas["pairwise_gap"]
        targeted_args = [
            "--plan-dir", str(plan_dir),
            "--candidate-csv", str(candidate_csv),
            "--out-dir", str(targeted_dir),
            "--feature-columns", args.feature_columns,
            "--max-total", str(targeted_total),
            "--rare-marginal-max-total", str(quotas["rare_marginal"]),
            "--pairwise-fallback-max-total", str(quotas["pairwise_gap"]),
            "--pairwise-feature-pairs", args.pairwise_feature_pairs,
            "--pairwise-marginal-features", args.pairwise_marginal_features,
            "--min-candidates-per-reachable-target", str(args.min_candidates_per_reachable_target),
            "--no-fail-exit",
        ]
        if args.reachable_targets_only:
            targeted_args.append("--reachable-targets-only")
        if args.redistribute_reachable_quota:
            targeted_args.append("--redistribute-reachable-quota")
        if args.prediction_calibration_json:
            targeted_args.extend(["--prediction-calibration-json", args.prediction_calibration_json])
        targeted_module = _load_sibling("select_physical_feature_targeted_candidate_geometries.py")
        targeted_module.main(targeted_args)
        targeted_summary = _read_json(targeted_dir / "physical_feature_targeted_candidate_selection_summary.json")
        targeted_status = str(targeted_summary.get("overall_status") or "FAIL")
        combined = _read_csv(targeted_dir / "physical_feature_targeted_candidate_selection.csv")

        candidates = _read_csv(candidate_csv)
        accepted = [
            row for row in _read_csv(accepted_dir / "dataset_rows.csv")
            if _truthy(row.get("ok", "true"))
        ]
        used_indices = {
            int(float(row["candidate_index"]))
            for row in combined
            if _finite(row.get("candidate_index")) is not None
        }
        features = _columns(args.feature_columns)
        geometry = _columns(args.geometry_columns)
        diversity_rows, diversity_diagnostics = _select_geometry_diversity(
            candidates,
            accepted,
            used_indices,
            features,
            geometry,
            quotas["geometry_diversity"],
            args,
            rank_offset=len(combined),
        )
        combined.extend(diversity_rows)
        used_indices.update(int(row["candidate_index"]) for row in diversity_rows)
        random_rows, random_diagnostics = _select_random_exploration(
            candidates,
            used_indices,
            features,
            geometry,
            quotas["random_exploration"],
            args,
            rank_offset=len(combined),
        )
        combined.extend(random_rows)

    counts = {
        arm: sum(1 for row in combined if str(row.get("selection_source")) == source)
        for arm, source in ARM_SOURCE.items()
    }
    candidate_indices = [str(row.get("candidate_index") or "") for row in combined]
    candidate_ids = [str(row.get("candidate_id") or "") for row in combined]
    checks.update(
        {
            "targeted_selector_pass": targeted_status == "PASS",
            "every_arm_meets_exact_quota": counts == quotas,
            "combined_count_matches_max_total": len(combined) == int(args.max_total),
            "candidate_indices_nonempty_unique": bool(combined)
            and all(candidate_indices)
            and len(candidate_indices) == len(set(candidate_indices)),
            "candidate_ids_nonempty_unique": bool(combined)
            and all(candidate_ids)
            and len(candidate_ids) == len(set(candidate_ids)),
            "geometry_diversity_complete": diversity_diagnostics.get("selected")
            == quotas["geometry_diversity"],
            "random_exploration_complete": random_diagnostics.get("selected")
            == quotas["random_exploration"],
        }
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    _write_csv(selected_csv, combined)
    targeted_count = counts["coarse_4d"] + counts["rare_marginal"] + counts["pairwise_gap"]
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_SELECTED_CANDIDATES_FIVE_ARM_FOR_NEXT_REAL_EMX" if status == "PASS" else "DO_NOT_RUN_EMX_FIX_MIX_FIRST",
        "outcome_status": "CANDIDATE_QUEUE_ONLY_AWAITING_REAL_EMX",
        "selected_csv": str(selected_csv),
        "candidate_csv": str(candidate_csv),
        "accepted_dataset_csv": str(accepted_dir / "dataset_rows.csv"),
        "accepted_dataset_source": _file_source(accepted_dir / "dataset_rows.csv"),
        "feature_columns": _columns(args.feature_columns),
        "geometry_columns": _columns(args.geometry_columns),
        "requested_candidate_count": int(args.max_total),
        "selected_candidate_count": len(combined),
        "selected_inside_target_bin_count": sum(1 for row in combined if _truthy(row.get("inside_target_bin"))),
        "selected_pairwise_gap_count": counts["pairwise_gap"],
        "selected_inside_or_pairwise_target_count": targeted_count,
        "selected_policy_eligible_count": len(combined) if status == "PASS" else 0,
        "acquisition_mix_contract": {
            "policy_version": 1,
            "requested_counts": quotas,
            "selected_counts": counts,
            "count_sum": len(combined),
            "seed": int(args.seed),
            "arms_are_disjoint": checks["candidate_indices_nonempty_unique"],
            "proxy_values_are_acquisition_only": True,
            "automatic_emx_launch_authorized": status == "PASS",
        },
        "targeted_selector_summary": targeted_summary,
        "geometry_diversity_diagnostics": diversity_diagnostics,
        "random_exploration_diagnostics": random_diagnostics,
        "checks": checks,
        "arguments": vars(args),
        "scientific_boundary": (
            "All response values in this file are proxy priorities, not labels. Realized Lp/Ls/Q/|K| bins, "
            "acceptance, and training labels must be recomputed from new nonempty EMX S4P files."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"overall_status={status}")
    print(f"selected_csv={selected_csv}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", required=True)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--accepted-dataset-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", default=DEFAULT_FEATURE_COLUMNS)
    parser.add_argument("--geometry-columns", default=DEFAULT_GEOMETRY_COLUMNS)
    parser.add_argument("--max-total", required=True, type=int)
    parser.add_argument("--coarse-4d-max-total", required=True, type=int)
    parser.add_argument("--rare-marginal-max-total", required=True, type=int)
    parser.add_argument("--pairwise-gap-max-total", required=True, type=int)
    parser.add_argument("--random-exploration-max-total", required=True, type=int)
    parser.add_argument("--geometry-diversity-max-total", required=True, type=int)
    parser.add_argument("--pairwise-feature-pairs", default="lp_nh_center:q_center,ls_nh_center:q_center")
    parser.add_argument("--pairwise-marginal-features", default="q_center,k_abs_center")
    parser.add_argument("--prediction-calibration-json")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--geometry-diversity-bins", type=int, default=3)
    parser.add_argument("--geometry-diversity-prefilter-factor", type=int, default=4)
    parser.add_argument("--accepted-reference-limit", type=int, default=8192)
    parser.add_argument("--reachable-targets-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--redistribute-reachable-quota", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-candidates-per-reachable-target", type=int, default=1)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.max_total < 1 or args.geometry_diversity_bins < 2:
        parser.error("--max-total must be positive and --geometry-diversity-bins must be >=2")
    if args.geometry_diversity_prefilter_factor < 1 or args.accepted_reference_limit < 1:
        parser.error("diversity prefilter and accepted reference limits must be positive")
    return args


def _select_geometry_diversity(
    candidates: list[dict[str, str]],
    accepted: list[dict[str, str]],
    used: set[int],
    features: list[str],
    geometry_columns: list[str],
    requested: int,
    args: argparse.Namespace,
    *,
    rank_offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if requested <= 0:
        return [], {"status": "PASS", "requested": 0, "eligible": 0, "selected": 0}
    candidate_matrix, candidate_valid = _matrix(candidates, geometry_columns)
    accepted_matrix, accepted_valid = _matrix(accepted, geometry_columns)
    global_mask = np.asarray(
        [str(row.get("candidate_generation_mode")) == "global_latin_hypercube" for row in candidates],
        dtype=bool,
    )
    for index in used:
        if 0 <= index < len(global_mask):
            global_mask[index] = False
    eligible = np.flatnonzero(candidate_valid & global_mask)
    accepted_values = accepted_matrix[accepted_valid]
    if eligible.size < requested or accepted_values.size == 0:
        return [], {
            "status": "FAIL_CAPACITY",
            "requested": requested,
            "eligible": int(eligible.size),
            "accepted_valid": int(len(accepted_values)),
            "selected": 0,
        }
    rng = np.random.default_rng(int(args.seed) + 101)
    if len(accepted_values) > int(args.accepted_reference_limit):
        ref_indices = np.sort(
            rng.choice(len(accepted_values), size=int(args.accepted_reference_limit), replace=False)
        )
        reference = accepted_values[ref_indices]
    else:
        reference = accepted_values
    combined = np.vstack([candidate_matrix[eligible], reference])
    lower = np.nanmin(combined, axis=0)
    upper = np.nanmax(combined, axis=0)
    span = np.where(upper > lower, upper - lower, 1.0)
    candidate_n = np.clip((candidate_matrix[eligible] - lower) / span, 0.0, 1.0)
    reference_n = np.clip((reference - lower) / span, 0.0, 1.0)
    try:
        from scipy.spatial import cKDTree

        novelty = np.asarray(cKDTree(reference_n).query(candidate_n, k=1, workers=1)[0], dtype=float)
    except Exception as exc:  # noqa: BLE001
        return [], {
            "status": f"FAIL_NEAREST_NEIGHBOR:{type(exc).__name__}",
            "requested": requested,
            "eligible": int(eligible.size),
            "selected": 0,
        }
    prefilter_count = min(
        len(eligible),
        max(requested, requested * int(args.geometry_diversity_prefilter_factor)),
    )
    if prefilter_count < len(eligible):
        positions = np.argpartition(novelty, -prefilter_count)[-prefilter_count:]
    else:
        positions = np.arange(len(eligible), dtype=int)
    bins = int(args.geometry_diversity_bins)
    cells = np.floor(candidate_n[positions] * bins).astype(int)
    cells = np.clip(cells, 0, bins - 1)
    groups: dict[tuple[int, ...], list[tuple[float, int]]] = {}
    for position, cell in zip(positions.tolist(), cells.tolist()):
        index = int(eligible[position])
        groups.setdefault(tuple(cell), []).append((float(novelty[position]), index))
    for rows in groups.values():
        rows.sort(key=lambda item: (-item[0], item[1]))
    order = sorted(groups, key=lambda cell: (-groups[cell][0][0], cell))
    chosen: list[tuple[float, int, tuple[int, ...]]] = []
    cursor = {cell: 0 for cell in order}
    while len(chosen) < requested:
        progress = False
        for cell in order:
            position = cursor[cell]
            if position >= len(groups[cell]):
                continue
            novelty_value, index = groups[cell][position]
            chosen.append((novelty_value, index, cell))
            cursor[cell] += 1
            progress = True
            if len(chosen) >= requested:
                break
        if not progress:
            break
    rows = [
        _exploration_row(
            candidates[index], index, rank_offset + offset + 1, features,
            source="geometry_diversity", score=-novelty_value,
            extra={"geometry_novelty_to_accepted": novelty_value, "geometry_diversity_cell": "|".join(map(str, cell))},
        )
        for offset, (novelty_value, index, cell) in enumerate(chosen)
    ]
    return rows, {
        "status": "PASS" if len(rows) == requested else "FAIL_PARTIAL",
        "requested": requested,
        "eligible": int(len(eligible)),
        "accepted_valid": int(len(accepted_values)),
        "accepted_reference_count": int(len(reference)),
        "prefilter_count": int(prefilter_count),
        "occupied_prefilter_cells": int(len(groups)),
        "selected": len(rows),
        "policy": "nearest-accepted novelty prefilter plus round-robin normalized geometry cells",
    }


def _select_random_exploration(
    candidates: list[dict[str, str]],
    used: set[int],
    features: list[str],
    geometry_columns: list[str],
    requested: int,
    args: argparse.Namespace,
    *,
    rank_offset: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if requested <= 0:
        return [], {"status": "PASS", "requested": 0, "eligible": 0, "selected": 0}
    _matrix_values, valid = _matrix(candidates, geometry_columns)
    eligible = np.asarray(
        [
            index
            for index, row in enumerate(candidates)
            if valid[index]
            and index not in used
            and str(row.get("candidate_generation_mode")) == "global_latin_hypercube"
        ],
        dtype=int,
    )
    rng = np.random.default_rng(int(args.seed) + 211)
    chosen = eligible[rng.permutation(len(eligible))[: min(requested, len(eligible))]]
    rows = [
        _exploration_row(
            candidates[int(index)], int(index), rank_offset + offset + 1, features,
            source="random_exploration", score=0.0, extra={"random_selection_seed": int(args.seed) + 211},
        )
        for offset, index in enumerate(chosen.tolist())
    ]
    return rows, {
        "status": "PASS" if len(rows) == requested else "FAIL_CAPACITY",
        "requested": requested,
        "eligible": int(len(eligible)),
        "selected": len(rows),
        "seed": int(args.seed) + 211,
        "policy": "seeded random sample from unused global Latin-hypercube candidates",
    }


def _exploration_row(
    candidate: dict[str, str],
    index: int,
    rank: int,
    features: list[str],
    *,
    source: str,
    score: float,
    extra: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "selection_rank": rank,
        "candidate_index": index,
        "candidate_id": candidate.get("candidate_id") or candidate.get("sample_id") or str(index),
        "target_rank": source,
        "target_bin_key": source,
        "target_recommended_new_samples": "",
        "inside_target_bin": False,
        "inside_pairwise_target_bin": False,
        "selection_score": score,
        "selection_source": source,
        "acquisition_policy_authorized": True,
        **extra,
    }
    for feature in features:
        value = _finite(candidate.get(f"pred_{feature}"))
        if value is None:
            value = _finite(candidate.get(feature))
        row[f"pred_{feature}"] = "" if value is None else value
        row[f"target_{feature}"] = ""
        row[f"target_{feature}_min"] = ""
        row[f"target_{feature}_max"] = ""
    for key, value in candidate.items():
        row[f"candidate__{key}"] = value
    return row


def _matrix(rows: list[dict[str, str]], columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.full((len(rows), len(columns)), np.nan, dtype=float)
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            value = _finite(row.get(column))
            if value is None and column.startswith("geom__"):
                value = _finite(row.get(column.removeprefix("geom__")))
            if value is not None:
                matrix[row_index, column_index] = value
    return matrix, np.all(np.isfinite(matrix), axis=1)


def _load_sibling(name: str):
    path = Path(__file__).resolve().with_name(name)
    module_name = f"_{path.stem}_mix_delegate"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _columns(raw: str) -> list[str]:
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _file_source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


if __name__ == "__main__":
    raise SystemExit(main())
