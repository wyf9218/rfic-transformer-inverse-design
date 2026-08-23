#!/usr/bin/env python3
"""Select candidate geometries for sparse physical-feature target bins.

This script bridges ``plan_physical_feature_balanced_acquisition.py`` and the
next EMX batch. It reads target bins in Lp/Ls/Q/K space plus a candidate table
with predicted physical features, then selects candidate geometries that land
inside or nearest to under-filled bins.

It does not run Cadence, EMX, HFSS, or ADS, and it does not create labels.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from physical_feature_prediction_calibration import apply_matrix, load_approved_calibration


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_dir = Path(args.plan_dir).expanduser().resolve()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    targets_csv = Path(args.targets_csv).expanduser().resolve() if args.targets_csv else plan_dir / "physical_feature_acquisition_targets.csv"
    bins_csv = plan_dir / "physical_feature_acquisition_bins.csv"
    targets = _read_csv(targets_csv)
    bins = _read_csv(bins_csv)
    candidates = _read_csv(candidate_csv)
    feature_columns = _resolve_feature_columns(args.feature_columns, targets)
    raw_prediction_columns = _resolve_prediction_columns(feature_columns, candidates, args.pred_column_prefix)
    prediction_columns = dict(raw_prediction_columns)
    calibration = {
        "mode": "raw_proxy_predictions",
        "requested": bool(args.prediction_calibration_json),
        "source": None,
        "decision": None,
        "eligible_for_selector": False,
        "error": None,
    }
    if args.prediction_calibration_json:
        calibration_path = Path(args.prediction_calibration_json).expanduser().resolve()
        calibration["source"] = _file_source(calibration_path)
        try:
            payload, mapping = load_approved_calibration(calibration_path, feature_columns)
            raw_matrix, _raw_valid = _candidate_prediction_matrix(
                candidates, feature_columns, raw_prediction_columns
            )
            calibrated_matrix = apply_matrix(raw_matrix, feature_columns, mapping)
            prediction_columns = {}
            for feature_index, feature in enumerate(feature_columns):
                column = f"calibrated_pred_{feature}"
                prediction_columns[feature] = column
                for row_index, candidate in enumerate(candidates):
                    value = float(calibrated_matrix[row_index, feature_index])
                    candidate[column] = value if math.isfinite(value) else ""
            calibration.update(
                {
                    "mode": "approved_holdout_isotonic_calibration",
                    "decision": payload.get("decision"),
                    "eligible_for_selector": True,
                    "holdout_metrics": payload.get("holdout_metrics"),
                    "improvements": payload.get("improvements"),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            calibration["error"] = f"{type(exc).__name__}: {exc}"

    checks = [
        _check("plan_targets_csv_exists", targets_csv.is_file(), str(targets_csv)),
        _check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)),
        _check("target_rows_present", bool(targets), f"rows={len(targets)}"),
        _check("candidate_rows_present", bool(candidates), f"rows={len(candidates)}"),
        _check("feature_columns_present", bool(feature_columns), ",".join(feature_columns)),
        _check("prediction_columns_present", len(prediction_columns) == len(feature_columns), prediction_columns),
    ]
    if args.prediction_calibration_json:
        checks.append(
            _check(
                "prediction_calibration_approved_for_acquisition_only",
                calibration.get("eligible_for_selector") is True,
                calibration,
            )
        )
    if int(args.pairwise_fallback_max_total or 0) > 0:
        checks.extend(
            [
                _check("plan_bins_csv_exists_for_pairwise_fallback", bins_csv.is_file(), str(bins_csv)),
                _check("plan_bin_rows_present_for_pairwise_fallback", bool(bins), f"rows={len(bins)}"),
            ]
        )
    selected: list[dict[str, Any]] = []
    per_target: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"status": "NOT_RUN"}
    if all(item["pass"] for item in checks):
        rare_quota = max(0, int(args.rare_marginal_max_total or 0))
        pairwise_quota = max(0, int(args.pairwise_fallback_max_total or 0))
        if args.max_total is not None:
            rare_quota = min(rare_quota, max(0, int(args.max_total)))
            pairwise_quota = min(pairwise_quota, max(0, int(args.max_total) - rare_quota))
        normal_args = copy.copy(args)
        if normal_args.max_total is not None:
            normal_args.max_total = max(0, int(normal_args.max_total) - rare_quota - pairwise_quota)
        selected, per_target, diagnostics = _select_candidates(
            targets, candidates, feature_columns, prediction_columns, normal_args
        )
        pairwise_selected, pairwise_diagnostics = _select_pairwise_gap_candidates(
            candidates,
            selected,
            bins,
            feature_columns,
            prediction_columns,
            pairwise_quota,
            args,
        )
        selected.extend(pairwise_selected)
        rare_selected, rare_diagnostics = _select_rare_marginal_candidates(
            candidates,
            selected,
            feature_columns,
            prediction_columns,
            rare_quota,
            args,
        )
        selected.extend(rare_selected)
        calibration_sha = ((calibration.get("source") or {}).get("sha256") if calibration else None)
        for row in selected:
            row["prediction_value_source"] = calibration.get("mode")
            row["prediction_calibration_sha256"] = calibration_sha or ""
            for feature in feature_columns:
                raw_column = raw_prediction_columns.get(feature)
                raw_value = row.get(f"candidate__{raw_column}") if raw_column else None
                row[f"raw_pred_{feature}"] = raw_value
                row[f"calibrated_pred_{feature}"] = (
                    row.get(f"pred_{feature}")
                    if calibration.get("eligible_for_selector") is True
                    else ""
                )
        diagnostics["pairwise_gap_fallback"] = pairwise_diagnostics
        diagnostics["rare_marginal"] = rare_diagnostics
        normal_requested = int(diagnostics.get("effective_requested_candidate_count") or _requested_count(targets, normal_args))
        requested = normal_requested + pairwise_quota + rare_quota
        checks.extend(
            [
                _check("requested_candidate_count_positive", requested > 0, requested),
                _check("selected_candidates_present", bool(selected), f"selected={len(selected)}"),
            ]
        )
        if args.reachable_targets_only:
            checks.append(
                _check(
                    "reachable_targets_present",
                    int(diagnostics.get("reachable_target_count") or 0) > 0,
                    diagnostics.get("reachable_target_count"),
                )
            )
    else:
        requested = 0

    selected_csv = out_dir / "physical_feature_targeted_candidate_selection.csv"
    summary_path = out_dir / "physical_feature_targeted_candidate_selection_summary.json"
    report_path = out_dir / "physical_feature_targeted_candidate_selection_report.md"
    _write_csv(selected_csv, selected)

    status = _status(checks, len(selected), requested)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": _decision(status),
        "plan_dir": str(plan_dir),
        "targets_csv": str(targets_csv),
        "targets_source": _file_source(targets_csv),
        "bins_csv": str(bins_csv),
        "bins_source": _file_source(bins_csv),
        "candidate_csv": str(candidate_csv),
        "candidate_source": _file_source(candidate_csv),
        "out_dir": str(out_dir),
        "selected_csv": str(selected_csv),
        "feature_columns": feature_columns,
        "raw_prediction_columns": raw_prediction_columns,
        "prediction_columns": prediction_columns,
        "prediction_calibration": calibration,
        "original_requested_candidate_count": diagnostics.get("original_requested_candidate_count", requested),
        "requested_candidate_count": requested,
        "selected_candidate_count": len(selected),
        "selected_inside_target_bin_count": sum(1 for row in selected if row.get("inside_target_bin") is True),
        "selected_rare_marginal_count": sum(
            1 for row in selected if str(row.get("selection_source")) == "rare_marginal_real_seed"
        ),
        "selected_pairwise_gap_count": sum(
            1 for row in selected if str(row.get("selection_source")) == "pairwise_gap_fallback"
        ),
        "target_count": len(targets),
        "candidate_count": len(candidates),
        "selection_diagnostics": diagnostics,
        "per_target": per_target,
        "checks": checks,
        "arguments": vars(args),
        "limitations": [
            "This selector ranks proposed geometries from predicted physical features only.",
            "Use the selected candidates only as the next acquisition queue. Final labels must come from simulator-generated S-parameters.",
            "Prediction quality depends on the surrogate or optimizer that produced the candidate CSV.",
            "Rare-marginal rows are still candidate priorities; their predicted edge-bin membership must be confirmed by real EMX labels.",
            "Pairwise-gap fallback rows target declared marginal/pair deficits without claiming full 4-D target-bin membership; real EMX labels decide whether coverage improved.",
            "When an approved calibration is supplied, pred_* selection fields contain calibrated acquisition-only values while candidate__pred_* retains the raw proxy values.",
            "Calibration approval is based on an independent-geometry real-EMX holdout and must be repeated after any port, process, frequency, geometry-range, or candidate-distribution change.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"selected_csv={selected_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", required=True, help="Directory produced by plan_physical_feature_balanced_acquisition.py")
    parser.add_argument("--targets-csv", help="Override path to physical_feature_acquisition_targets.csv")
    parser.add_argument("--candidate-csv", required=True, help="CSV containing proposed geometries and predicted physical features")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--feature-columns", help="Comma-separated feature columns; defaults to columns inferred from target CSV")
    parser.add_argument("--pred-column-prefix", default="pred_")
    parser.add_argument(
        "--prediction-calibration-json",
        help="Approved proxy-to-real calibration summary; used for acquisition bin assignment only.",
    )
    parser.add_argument("--candidate-id-column", default="candidate_id")
    parser.add_argument("--max-total", type=int, help="Cap total selected candidates across all target bins")
    parser.add_argument("--max-per-target", type=int, help="Cap selected candidates per target bin")
    parser.add_argument(
        "--rare-marginal-max-total",
        type=int,
        default=0,
        help="Reserve up to this many selections for local candidates aimed at underfilled one-dimensional bins.",
    )
    parser.add_argument(
        "--pairwise-fallback-max-total",
        type=int,
        default=0,
        help="Reserve selections for marginal/pairwise gaps when strict 4-D inside-bin candidates are scarce.",
    )
    parser.add_argument(
        "--pairwise-feature-pairs",
        default="lp_nh_center:q_center,ls_nh_center:q_center",
        help="Comma-separated feature pairs scored by the fallback, for example lp_nh_center:q_center.",
    )
    parser.add_argument(
        "--pairwise-marginal-features",
        default="q_center,k_abs_center",
        help="Comma-separated one-dimensional deficit features included in fallback scoring.",
    )
    parser.add_argument("--pairwise-deficit-weight", type=float, default=1.0)
    parser.add_argument("--pairwise-marginal-deficit-weight", type=float, default=0.5)
    parser.add_argument("--pairwise-four-d-novelty-weight", type=float, default=0.25)
    parser.add_argument("--allow-outside-bin", action="store_true", help="Allow nearest candidates even when outside a target bin")
    parser.add_argument("--reachable-targets-only", action="store_true", help="Drop target bins with too few inside-bin predicted candidates")
    parser.add_argument("--min-candidates-per-reachable-target", type=int, default=1)
    parser.add_argument("--redistribute-reachable-quota", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _resolve_feature_columns(explicit: str | None, targets: list[dict[str, str]]) -> list[str]:
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]
    if not targets:
        return []
    columns = []
    for key in targets[0]:
        if key.endswith("__target"):
            candidate = key.removesuffix("__target")
            if f"{candidate}__min" in targets[0] and f"{candidate}__max" in targets[0]:
                columns.append(candidate)
    return columns


def _resolve_prediction_columns(feature_columns: list[str], candidates: list[dict[str, str]], prefix: str) -> dict[str, str]:
    if not candidates:
        return {}
    fields = set(candidates[0])
    resolved = {}
    for column in feature_columns:
        preferred = f"{prefix}{column}"
        if preferred in fields:
            resolved[column] = preferred
        elif column in fields:
            resolved[column] = column
    return resolved


def _select_candidates(
    targets: list[dict[str, str]],
    candidates: list[dict[str, str]],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    per_target: list[dict[str, Any]] = []
    sorted_targets = _sorted_targets(targets)
    original_requested_total = _requested_count(targets, args)
    prediction_matrix, valid_predictions = _candidate_prediction_matrix(candidates, feature_columns, prediction_columns)
    inside_indices = _inside_candidate_indices(sorted_targets, prediction_matrix, valid_predictions, feature_columns)
    inside_counts = {key: int(len(indices)) for key, indices in inside_indices.items()}
    min_reachable = max(1, int(args.min_candidates_per_reachable_target))
    active_targets = [
        target for target in sorted_targets if not args.reachable_targets_only or inside_counts[_target_key(target)] >= min_reachable
    ]
    skipped_targets = [
        target for target in sorted_targets if args.reachable_targets_only and inside_counts[_target_key(target)] < min_reachable
    ]
    quota_by_key = _quota_by_target(active_targets, inside_counts, args, original_requested_total)
    effective_requested_total = sum(quota_by_key.values())
    remaining_total = effective_requested_total
    used = np.zeros(len(candidates), dtype=bool)

    for target in skipped_targets:
        per_target.append(_target_record(target, _target_request(target, args), 0, "UNREACHABLE_SKIPPED", inside_counts[_target_key(target)]))

    for target in active_targets:
        requested = min(quota_by_key.get(_target_key(target), 0), remaining_total)
        if requested <= 0:
            per_target.append(_target_record(target, requested, 0, "SKIPPED", inside_counts[_target_key(target)]))
            continue
        key = _target_key(target)
        if args.allow_outside_bin:
            candidate_indices = np.flatnonzero(valid_predictions & ~used)
        else:
            candidate_indices = inside_indices[key]
            candidate_indices = candidate_indices[~used[candidate_indices]]
        chosen_indices, chosen_scores = _rank_candidate_indices(
            target,
            candidate_indices,
            prediction_matrix,
            feature_columns,
            requested,
        )
        inside_set = None if not args.allow_outside_bin else set(inside_indices[key].tolist())
        for idx, score in zip(chosen_indices.tolist(), chosen_scores.tolist()):
            used[idx] = True
            prediction = {
                "predictions": {
                    feature: float(prediction_matrix[idx, feature_idx])
                    for feature_idx, feature in enumerate(feature_columns)
                },
                "inside_target_bin": True if inside_set is None else idx in inside_set,
            }
            selected.append(_selection_row(target, candidates[idx], prediction, float(score), idx, len(selected) + 1, args))
        chosen_count = int(len(chosen_indices))
        remaining_total -= chosen_count
        per_target.append(
            _target_record(target, requested, chosen_count, "PASS" if chosen_count == requested else "PARTIAL", inside_counts[key])
        )
        if remaining_total <= 0:
            break

    diagnostics = {
        "status": "PASS",
        "original_requested_candidate_count": original_requested_total,
        "effective_requested_candidate_count": effective_requested_total,
        "reachable_targets_only": bool(args.reachable_targets_only),
        "redistribute_reachable_quota": bool(args.redistribute_reachable_quota),
        "min_candidates_per_reachable_target": min_reachable,
        "reachable_target_count": len(active_targets),
        "unreachable_target_count": len(skipped_targets),
        "reachable_inside_candidate_capacity": sum(inside_counts[_target_key(target)] for target in active_targets),
        "selected_inside_target_bin_count": sum(1 for row in selected if row.get("inside_target_bin") is True),
    }
    return selected, per_target, diagnostics


def _candidate_prediction_matrix(
    candidates: list[dict[str, str]],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.full((len(candidates), len(feature_columns)), np.nan, dtype=float)
    for row_idx, candidate in enumerate(candidates):
        for feature_idx, feature in enumerate(feature_columns):
            value = _as_float(candidate.get(prediction_columns.get(feature, "")))
            if value is not None:
                matrix[row_idx, feature_idx] = value
    return matrix, np.all(np.isfinite(matrix), axis=1)


def _select_pairwise_gap_candidates(
    candidates: list[dict[str, str]],
    already_selected: list[dict[str, Any]],
    bins: list[dict[str, str]],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
    requested: int,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if requested <= 0:
        return [], {"requested": 0, "eligible": 0, "selected": 0, "groups": 0}
    tables = _build_deficit_tables(bins, feature_columns)
    pairs = _parse_feature_pairs(args.pairwise_feature_pairs, feature_columns)
    marginals = [
        item.strip()
        for item in str(args.pairwise_marginal_features).split(",")
        if item.strip() in feature_columns
    ]
    if tables.get("status") != "PASS" or not pairs:
        return [], {
            "requested": requested,
            "eligible": 0,
            "selected": 0,
            "groups": 0,
            "status": "FAIL_INVALID_DEFICIT_TABLE_OR_PAIRS",
            "table_status": tables.get("status"),
            "pairs": pairs,
        }
    prediction_matrix, valid_predictions = _candidate_prediction_matrix(
        candidates, feature_columns, prediction_columns
    )
    used = {
        int(row["candidate_index"])
        for row in already_selected
        if str(row.get("candidate_index", "")).lstrip("-").isdigit()
    }
    pair_weight = max(0.0, float(args.pairwise_deficit_weight))
    marginal_weight = max(0.0, float(args.pairwise_marginal_deficit_weight))
    four_d_weight = max(0.0, float(args.pairwise_four_d_novelty_weight))
    grouped: dict[str, list[dict[str, Any]]] = {}
    group_priority: dict[str, float] = {}
    eligible_count = 0
    for candidate_index in np.flatnonzero(valid_predictions):
        idx = int(candidate_index)
        if idx in used:
            continue
        values = prediction_matrix[idx]
        bin_tuple = _prediction_bin_tuple(values, feature_columns, tables["edges"])
        if bin_tuple is None:
            continue
        pair_scores: list[tuple[float, tuple[str, str]]] = []
        for pair in pairs:
            axes = (feature_columns.index(pair[0]), feature_columns.index(pair[1]))
            pair_key = (bin_tuple[axes[0]], bin_tuple[axes[1]])
            pair_scores.append((float(tables["pair_deficit"][pair].get(pair_key, 0.0)), pair))
        strongest, primary_pair = max(pair_scores, key=lambda item: (item[0], item[1]))
        pair_total = float(sum(score for score, _pair in pair_scores))
        marginal_total = float(
            sum(
                tables["marginal_deficit"].get(feature, {}).get(
                    bin_tuple[feature_columns.index(feature)], 0.0
                )
                for feature in marginals
            )
        )
        four_d = float(tables["four_d_deficit"].get(tuple(bin_tuple), 0.0))
        priority = pair_weight * pair_total + marginal_weight * marginal_total + four_d_weight * four_d
        if priority <= 0.0 or strongest <= 0.0:
            continue
        eligible_count += 1
        primary_axes = (feature_columns.index(primary_pair[0]), feature_columns.index(primary_pair[1]))
        primary_bins = (bin_tuple[primary_axes[0]], bin_tuple[primary_axes[1]])
        group_key = f"{primary_pair[0]}:{primary_pair[1]}:{primary_bins[0]}:{primary_bins[1]}"
        center_distance = _bin_center_distance(values, bin_tuple, feature_columns, tables["edges"])
        grouped.setdefault(group_key, []).append(
            {
                "candidate_index": idx,
                "priority": priority,
                "pair_total": pair_total,
                "marginal_total": marginal_total,
                "four_d": four_d,
                "strongest_pair_deficit": strongest,
                "primary_pair": primary_pair,
                "bin_tuple": tuple(bin_tuple),
                "center_distance": center_distance,
            }
        )
        group_priority[group_key] = max(group_priority.get(group_key, 0.0), strongest)
    for rows in grouped.values():
        rows.sort(key=lambda item: (-item["priority"], item["center_distance"], item["candidate_index"]))
    group_order = sorted(grouped, key=lambda key: (-group_priority[key], key))
    chosen: list[dict[str, Any]] = []
    cursor = {key: 0 for key in group_order}
    while len(chosen) < requested:
        progressed = False
        for key in group_order:
            position = cursor[key]
            if position >= len(grouped[key]):
                continue
            chosen.append(grouped[key][position])
            cursor[key] += 1
            progressed = True
            if len(chosen) >= requested:
                break
        if not progressed:
            break
    selected: list[dict[str, Any]] = []
    for item in chosen:
        idx = int(item["candidate_index"])
        candidate = candidates[idx]
        bin_tuple = item["bin_tuple"]
        primary_pair = item["primary_pair"]
        row: dict[str, Any] = {
            "selection_rank": len(already_selected) + len(selected) + 1,
            "candidate_index": idx,
            "candidate_id": candidate.get(args.candidate_id_column)
            or candidate.get("sample_id")
            or candidate.get("evaluation")
            or str(idx),
            "target_rank": "pairwise",
            "target_bin_key": "pairwise:{}:{}:{}:{}".format(
                primary_pair[0],
                bin_tuple[feature_columns.index(primary_pair[0])],
                primary_pair[1],
                bin_tuple[feature_columns.index(primary_pair[1])],
            ),
            "target_recommended_new_samples": requested,
            "inside_target_bin": False,
            "inside_pairwise_target_bin": True,
            "selection_source": "pairwise_gap_fallback",
            "selection_score": -float(item["priority"]),
            "pairwise_priority_score": float(item["priority"]),
            "pairwise_deficit_score": float(item["pair_total"]),
            "marginal_deficit_score": float(item["marginal_total"]),
            "four_d_novelty_score": float(item["four_d"]),
        }
        for axis, feature in enumerate(feature_columns):
            value = float(prediction_matrix[idx, axis])
            edge = tables["edges"][feature][bin_tuple[axis]]
            row[f"pred_{feature}"] = value
            row[f"target_{feature}"] = 0.5 * (edge[0] + edge[1])
            row[f"target_{feature}_min"] = edge[0]
            row[f"target_{feature}_max"] = edge[1]
        for key, value in candidate.items():
            row[f"candidate__{key}"] = value
        selected.append(row)
    top_groups = [
        {
            "group": key,
            "deficit_priority": group_priority[key],
            "eligible_candidates": len(grouped[key]),
            "selected_candidates": cursor[key],
        }
        for key in group_order[:50]
    ]
    return selected, {
        "requested": requested,
        "eligible": eligible_count,
        "selected": len(selected),
        "groups": len(grouped),
        "status": "PASS" if len(selected) == requested else "PARTIAL",
        "feature_pairs": [list(pair) for pair in pairs],
        "marginal_features": marginals,
        "weights": {
            "pairwise": pair_weight,
            "marginal": marginal_weight,
            "four_d_novelty": four_d_weight,
        },
        "top_groups": top_groups,
        "selection_policy": (
            "Round-robin across strongest underfilled pair bins; rank within each group by pairwise + marginal + 4-D deficit and target-cell center distance."
        ),
    }


def _build_deficit_tables(
    bins: list[dict[str, str]], feature_columns: list[str]
) -> dict[str, Any]:
    edges: dict[str, dict[int, tuple[float, float]]] = {feature: {} for feature in feature_columns}
    four_counts: dict[tuple[int, ...], int] = {}
    four_targets: dict[tuple[int, ...], int] = {}
    try:
        for row in bins:
            index = tuple(int(float(row[f"{feature}__bin"])) for feature in feature_columns)
            current = int(float(row.get("current_count") or 0))
            target = int(float(row.get("target_count") or 0))
            four_counts[index] = current
            four_targets[index] = target
            for axis, feature in enumerate(feature_columns):
                edges[feature][index[axis]] = (
                    _required_float(row, f"{feature}__min"),
                    _required_float(row, f"{feature}__max"),
                )
    except (KeyError, TypeError, ValueError) as exc:
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    if not four_counts or any(not value for value in edges.values()):
        return {"status": "FAIL", "error": "empty 4-D bins or feature edges"}
    marginal_counts: dict[str, dict[int, int]] = {feature: {} for feature in feature_columns}
    marginal_targets: dict[str, dict[int, int]] = {feature: {} for feature in feature_columns}
    pair_counts: dict[tuple[str, str], dict[tuple[int, int], int]] = {}
    pair_targets: dict[tuple[str, str], dict[tuple[int, int], int]] = {}
    all_pairs = [
        (feature_columns[left], feature_columns[right])
        for left in range(len(feature_columns))
        for right in range(left + 1, len(feature_columns))
    ]
    for pair in all_pairs:
        pair_counts[pair] = {}
        pair_targets[pair] = {}
    for index, current in four_counts.items():
        target = four_targets[index]
        for axis, feature in enumerate(feature_columns):
            marginal_counts[feature][index[axis]] = marginal_counts[feature].get(index[axis], 0) + current
            marginal_targets[feature][index[axis]] = marginal_targets[feature].get(index[axis], 0) + target
        for pair in all_pairs:
            axes = (feature_columns.index(pair[0]), feature_columns.index(pair[1]))
            key = (index[axes[0]], index[axes[1]])
            pair_counts[pair][key] = pair_counts[pair].get(key, 0) + current
            pair_targets[pair][key] = pair_targets[pair].get(key, 0) + target
    return {
        "status": "PASS",
        "edges": edges,
        "four_d_deficit": {
            key: _deficit_fraction(four_counts[key], four_targets[key]) for key in four_counts
        },
        "marginal_deficit": {
            feature: {
                key: _deficit_fraction(count, marginal_targets[feature][key])
                for key, count in counts.items()
            }
            for feature, counts in marginal_counts.items()
        },
        "pair_deficit": {
            pair: {
                key: _deficit_fraction(count, pair_targets[pair][key])
                for key, count in counts.items()
            }
            for pair, counts in pair_counts.items()
        },
    }


def _deficit_fraction(current: int, target: int) -> float:
    if target <= 0:
        return 0.0
    return float(max(0, target - current) / target)


def _parse_feature_pairs(raw: str, feature_columns: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for token in str(raw).split(","):
        parts = [item.strip() for item in token.split(":") if item.strip()]
        if len(parts) != 2 or parts[0] == parts[1]:
            continue
        if parts[0] not in feature_columns or parts[1] not in feature_columns:
            continue
        ordered = tuple(sorted(parts, key=feature_columns.index))
        if ordered not in pairs:
            pairs.append(ordered)
    return pairs


def _prediction_bin_tuple(
    values: np.ndarray,
    feature_columns: list[str],
    edges: dict[str, dict[int, tuple[float, float]]],
) -> tuple[int, ...] | None:
    indices: list[int] = []
    for axis, feature in enumerate(feature_columns):
        value = float(values[axis])
        matches = [
            index
            for index, (lower, upper) in sorted(edges[feature].items())
            if lower <= value <= upper
        ]
        if not matches:
            return None
        indices.append(matches[0] if len(matches) == 1 else matches[-1])
    return tuple(indices)


def _bin_center_distance(
    values: np.ndarray,
    bin_tuple: tuple[int, ...],
    feature_columns: list[str],
    edges: dict[str, dict[int, tuple[float, float]]],
) -> float:
    terms = []
    for axis, feature in enumerate(feature_columns):
        lower, upper = edges[feature][bin_tuple[axis]]
        center = 0.5 * (lower + upper)
        half_width = max(0.5 * (upper - lower), 1.0e-12)
        terms.append(((float(values[axis]) - center) / half_width) ** 2)
    return float(math.sqrt(sum(terms) / max(1, len(terms))))


def _select_rare_marginal_candidates(
    candidates: list[dict[str, str]],
    already_selected: list[dict[str, Any]],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
    requested: int,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if requested <= 0:
        return [], {"requested": 0, "eligible": 0, "selected": 0, "groups": 0}
    used_ids = {str(row.get("candidate_id") or "") for row in already_selected}
    eligible: list[tuple[float, float, float, int, dict[str, str], str, int]] = []
    groups: set[tuple[str, int]] = set()
    for candidate_index, candidate in enumerate(candidates):
        if candidate.get("candidate_generation_mode") != "local_rare_marginal_perturbation":
            continue
        candidate_id = str(
            candidate.get(args.candidate_id_column)
            or candidate.get("sample_id")
            or candidate.get("evaluation")
            or candidate_index
        )
        if candidate_id in used_ids:
            continue
        feature = str(candidate.get("candidate_marginal_feature") or "")
        if feature not in feature_columns:
            continue
        bin_index = int(float(candidate.get("candidate_marginal_bin") or -1))
        lower = _as_float(candidate.get("candidate_marginal_min"))
        upper = _as_float(candidate.get("candidate_marginal_max"))
        center = _as_float(candidate.get("candidate_marginal_target"))
        predicted = _as_float(candidate.get(prediction_columns.get(feature, "")))
        if lower is None or upper is None or center is None or predicted is None or not lower <= predicted <= upper:
            continue
        half_width = max((upper - lower) / 2.0, 1.0e-12)
        distance = abs(predicted - center) / half_width
        seed_count = float(_as_float(candidate.get("candidate_marginal_seed_count")) or 1.0)
        priority = float(_as_float(candidate.get("candidate_marginal_priority_weight")) or 1.0)
        anchor = float(_as_float(candidate.get("candidate_seed_anchor_weight")) or 0.0)
        eligible.append((-priority, seed_count, distance - 0.05 * anchor, candidate_index, candidate, feature, bin_index))
        groups.add((feature, bin_index))
    eligible.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    chosen = eligible[: min(requested, len(eligible))]
    selected: list[dict[str, Any]] = []
    for _priority, _seed_count, score, candidate_index, candidate, feature, bin_index in chosen:
        row: dict[str, Any] = {
            "selection_rank": len(already_selected) + len(selected) + 1,
            "candidate_index": candidate_index,
            "candidate_id": candidate.get(args.candidate_id_column) or str(candidate_index),
            "target_rank": "marginal",
            "target_bin_key": "marginal:{}:{}".format(feature, bin_index),
            "target_recommended_new_samples": requested,
            "inside_target_bin": True,
            "selection_score": float(score),
            "selection_source": "rare_marginal_real_seed",
        }
        for requested_feature in feature_columns:
            value = _as_float(candidate.get(prediction_columns.get(requested_feature, "")))
            row["pred_{}".format(requested_feature)] = value
            if requested_feature == feature:
                row["target_{}".format(requested_feature)] = candidate.get("candidate_marginal_target")
                row["target_{}_min".format(requested_feature)] = candidate.get("candidate_marginal_min")
                row["target_{}_max".format(requested_feature)] = candidate.get("candidate_marginal_max")
            else:
                row["target_{}".format(requested_feature)] = ""
                row["target_{}_min".format(requested_feature)] = ""
                row["target_{}_max".format(requested_feature)] = ""
        for key, value in candidate.items():
            row["candidate__{}".format(key)] = value
        selected.append(row)
    return selected, {
        "requested": requested,
        "eligible": len(eligible),
        "selected": len(selected),
        "groups": len(groups),
        "selection_policy": "highest marginal deficit priority, then rare seed count, target-center distance, and anchor weight",
    }


def _inside_candidate_indices(
    targets: list[dict[str, str]],
    prediction_matrix: np.ndarray,
    valid_predictions: np.ndarray,
    feature_columns: list[str],
) -> dict[tuple[str, str], np.ndarray]:
    out: dict[tuple[str, str], np.ndarray] = {}
    for target in targets:
        lower = np.asarray([_required_float(target, f"{feature}__min") for feature in feature_columns], dtype=float)
        upper = np.asarray([_required_float(target, f"{feature}__max") for feature in feature_columns], dtype=float)
        mask = valid_predictions & np.all(prediction_matrix >= lower[None, :], axis=1)
        mask &= np.all(prediction_matrix <= upper[None, :], axis=1)
        out[_target_key(target)] = np.flatnonzero(mask)
    return out


def _rank_candidate_indices(
    target: dict[str, str],
    candidate_indices: np.ndarray,
    prediction_matrix: np.ndarray,
    feature_columns: list[str],
    requested: int,
) -> tuple[np.ndarray, np.ndarray]:
    if requested <= 0 or candidate_indices.size == 0:
        return np.asarray([], dtype=int), np.asarray([], dtype=float)
    lower = np.asarray([_required_float(target, f"{feature}__min") for feature in feature_columns], dtype=float)
    upper = np.asarray([_required_float(target, f"{feature}__max") for feature in feature_columns], dtype=float)
    center = np.asarray([_required_float(target, f"{feature}__target") for feature in feature_columns], dtype=float)
    half_width = np.maximum(np.abs(upper - lower) / 2.0, 1e-12)
    values = prediction_matrix[candidate_indices]
    scores = np.sqrt(np.mean(((values - center[None, :]) / half_width[None, :]) ** 2, axis=1))
    order = np.lexsort((candidate_indices, scores))
    order = order[: min(int(requested), len(order))]
    return candidate_indices[order], scores[order]


def _candidate_prediction(
    candidate: dict[str, str],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
) -> dict[str, float] | None:
    prediction = {}
    for feature in feature_columns:
        value = _as_float(candidate.get(prediction_columns.get(feature, "")))
        if value is None:
            return None
        prediction[feature] = value
    return prediction


def _inside_candidate_counts(
    targets: list[dict[str, str]],
    candidates: list[dict[str, str]],
    feature_columns: list[str],
    prediction_columns: dict[str, str],
) -> dict[tuple[str, str], int]:
    counts = {_target_key(target): 0 for target in targets}
    for target in targets:
        key = _target_key(target)
        for candidate in candidates:
            prediction = _candidate_prediction(candidate, feature_columns, prediction_columns)
            if prediction is None:
                continue
            _score, inside = _target_distance(target, prediction, feature_columns)
            if inside:
                counts[key] += 1
    return counts


def _quota_by_target(
    active_targets: list[dict[str, str]],
    inside_counts: dict[tuple[str, str], int],
    args: argparse.Namespace,
    original_requested_total: int,
) -> dict[tuple[str, str], int]:
    if not args.redistribute_reachable_quota:
        uncapped = {
            _target_key(target): min(_target_request(target, args), inside_counts.get(_target_key(target), 0))
            if args.reachable_targets_only and not args.allow_outside_bin
            else _target_request(target, args)
            for target in active_targets
        }
        remaining = max(0, int(original_requested_total))
        capped = {key: 0 for key in uncapped}
        for target in active_targets:
            key = _target_key(target)
            assigned = min(uncapped[key], remaining)
            capped[key] = assigned
            remaining -= assigned
            if remaining <= 0:
                break
        return capped
    desired_total = original_requested_total
    if args.max_total is not None:
        desired_total = min(desired_total, max(0, int(args.max_total)))
    capacities = {
        _target_key(target): inside_counts.get(_target_key(target), 0) if not args.allow_outside_bin else desired_total
        for target in active_targets
    }
    if args.max_per_target is not None:
        capacities = {key: min(value, max(0, int(args.max_per_target))) for key, value in capacities.items()}
    quotas = {key: 0 for key in capacities}
    ordered_keys = [_target_key(target) for target in active_targets]
    remaining = min(desired_total, sum(capacities.values()))
    while remaining > 0 and ordered_keys:
        progressed = False
        for key in ordered_keys:
            if remaining <= 0:
                break
            if quotas[key] >= capacities[key]:
                continue
            quotas[key] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break
    return quotas


def _target_key(target: dict[str, str]) -> tuple[str, str]:
    return (str(target.get("rank")), str(target.get("bin_key")))


def _sorted_targets(targets: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(targets, key=lambda row: (int(float(row.get("rank", "0") or 0)), -int(float(row.get("recommended_new_samples", "0") or 0))))


def _requested_count(targets: list[dict[str, str]], args: argparse.Namespace) -> int:
    total = sum(_target_request(row, args) for row in targets)
    if args.max_total is not None:
        total = min(total, max(0, int(args.max_total)))
    return total


def _target_request(target: dict[str, str], args: argparse.Namespace) -> int:
    requested = int(float(target.get("recommended_new_samples", "0") or 0))
    if args.max_per_target is not None:
        requested = min(requested, max(0, int(args.max_per_target)))
    return max(0, requested)


def _target_distance(target: dict[str, str], prediction: dict[str, float], feature_columns: list[str]) -> tuple[float, bool]:
    squared = 0.0
    inside = True
    for feature in feature_columns:
        lo = _required_float(target, f"{feature}__min")
        hi = _required_float(target, f"{feature}__max")
        center = _required_float(target, f"{feature}__target")
        value = prediction[feature]
        half_width = max(abs(hi - lo) / 2.0, 1e-12)
        squared += ((value - center) / half_width) ** 2
        inside = inside and lo <= value <= hi
    return float(math.sqrt(squared / max(1, len(feature_columns)))), inside


def _selection_row(
    target: dict[str, str],
    candidate: dict[str, str],
    prediction: dict[str, Any],
    score: float,
    candidate_index: int,
    selection_rank: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    candidate_id = candidate.get(args.candidate_id_column) or candidate.get("sample_id") or candidate.get("evaluation") or str(candidate_index)
    row: dict[str, Any] = {
        "selection_rank": selection_rank,
        "candidate_index": candidate_index,
        "candidate_id": candidate_id,
        "target_rank": target.get("rank"),
        "target_bin_key": target.get("bin_key"),
        "target_recommended_new_samples": target.get("recommended_new_samples"),
        "inside_target_bin": prediction["inside_target_bin"],
        "selection_score": score,
        "selection_source": "four_d_target_bin",
    }
    for feature, value in prediction["predictions"].items():
        row[f"pred_{feature}"] = value
        row[f"target_{feature}"] = target.get(f"{feature}__target")
        row[f"target_{feature}_min"] = target.get(f"{feature}__min")
        row[f"target_{feature}_max"] = target.get(f"{feature}__max")
    for key, value in candidate.items():
        row[f"candidate__{key}"] = value
    return row


def _target_record(
    target: dict[str, str],
    requested: int,
    selected: int,
    status: str,
    inside_candidate_count: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "target_rank": target.get("rank"),
        "bin_key": target.get("bin_key"),
        "requested": requested,
        "selected": selected,
        "missing": max(0, requested - selected),
        "inside_candidate_count": inside_candidate_count,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _required_float(row: dict[str, str], key: str) -> float:
    value = _as_float(row.get(key))
    if value is None:
        raise ValueError(f"target row missing numeric {key}: {row}")
    return value


def _file_source(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _status(checks: list[dict[str, Any]], selected_count: int, requested_count: int) -> str:
    if any(not item["pass"] for item in checks):
        return "FAIL"
    if selected_count >= requested_count:
        return "PASS"
    return "PARTIAL"


def _decision(status: str) -> str:
    return {
        "PASS": "USE_SELECTED_CANDIDATES_FOR_NEXT_EMX_BATCH",
        "PARTIAL": "USE_SELECTED_CANDIDATES_WITH_MISSING_TARGET_CAVEAT",
        "FAIL": "DO_NOT_USE_CANDIDATE_SELECTION",
    }[status]


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Physical-Feature Targeted Candidate Geometry Selection",
        "",
        f"Status: **{summary['overall_status']}**",
        f"Decision: **{summary['decision']}**",
        f"Requested candidates: `{summary['requested_candidate_count']}`",
        f"Selected candidates: `{summary['selected_candidate_count']}`",
        f"Selected inside target-bin candidates: `{summary.get('selected_inside_target_bin_count')}`",
        f"Feature columns: `{', '.join(summary['feature_columns'])}`",
        f"Selected CSV: `{summary['selected_csv']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['name'])} | {check['pass']} | {_cell(str(check['detail']))} |")
    lines.extend(["", "## Per-target fill", "", "| Target rank | Bin | Requested | Selected | Missing | Status |", "| --- | --- | --- | --- | --- | --- |"])
    for row in summary["per_target"]:
        lines.append(
            f"| {row.get('target_rank')} | {row.get('bin_key')} | {row.get('requested')} | {row.get('selected')} | {row.get('missing')} | {row.get('status')} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
