#!/usr/bin/env python3
"""Build tandem-inverse candidates aimed at sparse physical-feature cells.

The acquisition planner identifies under-filled Lp/Ls/Q/|K| cells. This
script samples traceable targets inside those cells and maps them to bounded
geometry proposals with a completed tandem inverse model. The frozen forward
network is used only to describe and prioritize the proposals. Its predictions
are not EMX labels, and no coverage claim is valid until real S4P files return.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import qmc

from plan_tandem_local_refinement_benchmark import (
    EXPECTED_GEOMETRY_COUNT,
    EXPECTED_INPUT_COLUMNS,
    _load_model,
    _predict,
    _predict_inverse,
    _refine_geometry,
    _response_mse,
    _sha256,
    _vector_digest,
)


FEATURE_NAMES = tuple(column.removeprefix("input__") for column in EXPECTED_INPUT_COLUMNS)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.tandem_summary).expanduser().resolve()
    weights_path = Path(args.weights_npz).expanduser().resolve()
    targets_path = Path(args.targets_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tandem = _read_json(summary_path)
    model = _load_model(weights_path)
    input_columns = tuple(tandem.get("input_columns") or ())
    geometry_columns = tuple(tandem.get("geometry_columns") or ())
    targets = _load_sparse_targets(targets_path, FEATURE_NAMES)
    analysis = _build_candidates(model, targets, input_columns, geometry_columns, args)
    checks = _checks(tandem, model, targets, input_columns, geometry_columns, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    candidates_path = out_dir / "tandem_sparse_cell_candidate_predictions.csv"
    summary_out = out_dir / "tandem_sparse_cell_candidate_predictions_summary.json"
    report_path = out_dir / "tandem_sparse_cell_candidate_predictions_report.md"
    rows = analysis.pop("candidate_rows", [])
    _write_csv(candidates_path, rows)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": (
            "ELIGIBLE_FOR_EQUAL_BUDGET_REAL_EMX_CANDIDATE_ARM"
            if status == "PASS"
            else "DO_NOT_USE_FIX_CANDIDATE_ARM"
        ),
        "outcome_status": "AWAITING_REAL_EMX",
        "candidate_role": "independent_advisory_acquisition_arm",
        "candidate_generation_mode": "tandem_sparse_cell_inverse",
        "prediction_value_source": "frozen_tandem_forward_proxy_for_candidate_priority_only",
        "tandem_summary": _source(summary_path),
        "weights_npz": _source(weights_path),
        "targets_csv": _source(targets_path),
        "input_columns": list(input_columns),
        "geometry_columns": list(geometry_columns),
        "requested_candidate_count": int(args.candidate_count),
        "checks": checks,
        "analysis": analysis,
        "arguments": vars(args),
        "artifacts": {
            "candidate_predictions_csv": str(candidates_path),
            "summary_json": str(summary_out),
            "report_md": str(report_path),
        },
        "scientific_boundary": (
            "Every row is an unlabeled geometry proposal. The target coordinates and frozen-forward "
            "predictions are acquisition evidence only. They are not simulator labels, do not count "
            "toward the million-sample total, and cannot establish physical-space coverage. DRC and "
            "real EMX S4P evaluation are required before any proposal is accepted."
        ),
    }
    summary_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"outcome_status={payload['outcome_status']}")
    print(f"selected_candidate_count={analysis.get('selected_candidate_count', 0)}")
    print(f"summary={summary_out}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--weights-npz", required=True)
    parser.add_argument("--targets-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--candidate-count", type=int, default=256)
    parser.add_argument("--min-source-rows", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument(
        "--local-refinement",
        action="store_true",
        help="Use bounded L-BFGS-B after the inverse proposal; still proxy-only evidence.",
    )
    parser.add_argument("--trust-weight", type=float, default=0.01)
    parser.add_argument("--max-iterations", type=int, default=80)
    parser.add_argument("--gradient-tolerance", type=float, default=1.0e-8)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.candidate_count < 1 or args.min_source_rows < 1:
        parser.error("candidate count and source-row gate must be positive")
    if args.trust_weight < 0.0 or args.max_iterations < 1 or args.gradient_tolerance <= 0.0:
        parser.error("local-refinement settings are invalid")
    return args


def _load_sparse_targets(path: Path, feature_names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path_exists": path.is_file(),
        "columns_present": False,
        "row_count": 0,
        "usable_count": 0,
        "allocation_sum": 0,
        "targets": [],
    }
    if not path.is_file():
        return result
    required = {"bin_key", "rank", "recommended_new_samples"}
    for feature in feature_names:
        required.update((f"{feature}__min", f"{feature}__max", f"{feature}__target"))
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        result["columns_present"] = required.issubset(set(reader.fieldnames or ()))
        if not result["columns_present"]:
            return result
        for row in reader:
            result["row_count"] += 1
            allocation = _integer(row.get("recommended_new_samples"))
            rank = _integer(row.get("rank"))
            bounds = []
            centers = []
            for feature in feature_names:
                lower = _finite(row.get(f"{feature}__min"))
                upper = _finite(row.get(f"{feature}__max"))
                center = _finite(row.get(f"{feature}__target"))
                bounds.append((lower, upper))
                centers.append(center)
            if (
                allocation is None
                or allocation <= 0
                or rank is None
                or not str(row.get("bin_key") or "").strip()
                or any(lower is None or upper is None or lower >= upper for lower, upper in bounds)
                or any(center is None for center in centers)
                or any(not (lower <= center <= upper) for (lower, upper), center in zip(bounds, centers))
            ):
                continue
            target = {
                "bin_key": str(row["bin_key"]),
                "rank": int(rank),
                "recommended_new_samples": int(allocation),
                "bounds": np.asarray(bounds, dtype=float),
                "centers": np.asarray(centers, dtype=float),
                "deficit": int(_integer(row.get("deficit")) or 0),
                "current_count": int(_integer(row.get("current_count")) or 0),
                "priority_weight": float(_finite(row.get("priority_weight")) or 0.0),
            }
            result["targets"].append(target)
            result["allocation_sum"] += int(allocation)
    result["targets"].sort(key=lambda item: (item["rank"], item["bin_key"]))
    result["usable_count"] = len(result["targets"])
    return result


def _target_sequence(targets: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    remaining = int(count)
    for target in targets:
        if remaining <= 0:
            break
        allocation = min(int(target["recommended_new_samples"]), remaining)
        if allocation <= 0:
            continue
        sampler = qmc.LatinHypercube(d=len(FEATURE_NAMES), seed=int(seed) + int(target["rank"]) * 1009)
        unit = sampler.random(n=allocation)
        bounds = np.asarray(target["bounds"], dtype=float)
        sampled = qmc.scale(unit, bounds[:, 0], bounds[:, 1])
        sampled[0] = np.asarray(target["centers"], dtype=float)
        for within_bin_index, values in enumerate(sampled):
            sequence.append({**target, "within_bin_index": within_bin_index, "target_physical": values})
        remaining -= allocation
    return sequence


def _normalization_valid(model: dict[str, Any], feature_count: int, geometry_count: int) -> bool:
    normalization = model.get("normalization") or {}
    shapes = {
        "x_mean": (feature_count,),
        "x_scale": (feature_count,),
        "y_mean": (geometry_count,),
        "y_scale": (geometry_count,),
        "geometry_lower": (geometry_count,),
        "geometry_upper": (geometry_count,),
        "response_loss_physical_spans": (feature_count,),
    }
    if any(np.asarray(normalization.get(key, [])).shape != shape for key, shape in shapes.items()):
        return False
    return bool(
        np.all(np.asarray(normalization["x_scale"]) > 0.0)
        and np.all(np.asarray(normalization["y_scale"]) > 0.0)
        and np.all(np.asarray(normalization["response_loss_physical_spans"]) > 0.0)
        and np.all(np.asarray(normalization["geometry_upper"]) > np.asarray(normalization["geometry_lower"]))
    )


def _build_candidates(
    model: dict[str, Any],
    targets: dict[str, Any],
    input_columns: tuple[str, ...],
    geometry_columns: tuple[str, ...],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if (
        model.get("available") is not True
        or input_columns != EXPECTED_INPUT_COLUMNS
        or len(geometry_columns) != EXPECTED_GEOMETRY_COUNT
        or not _normalization_valid(model, len(input_columns), len(geometry_columns))
    ):
        return {"available": False, "candidate_rows": [], "selected_candidate_count": 0}
    sequence = _target_sequence(targets.get("targets") or [], int(args.candidate_count), int(args.seed))
    normalization = model["normalization"]
    lower = normalization["geometry_lower"]
    upper = normalization["geometry_upper"]
    rows: list[dict[str, Any]] = []
    geometry_digests: set[str] = set()
    inside_count = 0
    refined_count = 0
    proxy_range_rmse: list[float] = []

    for attempt, target in enumerate(sequence):
        target_physical = np.asarray(target["target_physical"], dtype=float)
        target_normalized = (target_physical - normalization["x_mean"]) / normalization["x_scale"]
        geometry = _predict_inverse(
            target_normalized[None, :],
            model["inverse_weights"],
            model["inverse_biases"],
            lower,
            upper,
        )[0]
        proxy = _predict(geometry[None, :], model["forward_weights"], model["forward_biases"])[0]
        refinement_used = False
        optimizer_success = False
        if args.local_refinement:
            refined = _refine_geometry(
                geometry,
                target_normalized,
                model["forward_weights"],
                model["forward_biases"],
                lower,
                upper,
                normalization["x_scale"],
                normalization["response_loss_physical_spans"],
                args,
            )
            optimizer_success = bool(refined["optimizer_success"])
            baseline_error = _response_mse(
                proxy,
                target_normalized,
                normalization["x_scale"],
                normalization["response_loss_physical_spans"],
            )
            if optimizer_success and float(refined["response_mse"]) < baseline_error:
                geometry = np.asarray(refined["geometry"], dtype=float)
                proxy = np.asarray(refined["prediction"], dtype=float)
                refinement_used = True
                refined_count += 1

        physical_geometry = geometry * normalization["y_scale"] + normalization["y_mean"]
        digest = _vector_digest(physical_geometry)
        if digest in geometry_digests or not np.isfinite(physical_geometry).all():
            continue
        geometry_digests.add(digest)
        proxy_physical = proxy * normalization["x_scale"] + normalization["x_mean"]
        bounds = np.asarray(target["bounds"], dtype=float)
        inside = bool(np.all(proxy_physical >= bounds[:, 0]) and np.all(proxy_physical <= bounds[:, 1]))
        inside_count += int(inside)
        response_mse = _response_mse(
            proxy,
            target_normalized,
            normalization["x_scale"],
            normalization["response_loss_physical_spans"],
        )
        proxy_range_rmse.append(float(math.sqrt(max(0.0, response_mse))))
        row: dict[str, Any] = {
            "candidate_id": f"tandem_sparse_{len(rows):06d}",
            "candidate_generation_mode": "tandem_sparse_cell_inverse",
            "pred_source": "frozen_tandem_forward_proxy_for_candidate_priority_only",
            "label_status": "AWAITING_REAL_EMX",
            "drc_status": "NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX",
            "target_bin_key": target["bin_key"],
            "target_rank": int(target["rank"]),
            "target_recommended_new_samples": int(target["recommended_new_samples"]),
            "target_within_bin_index": int(target["within_bin_index"]),
            "target_current_count": int(target["current_count"]),
            "target_deficit": int(target["deficit"]),
            "target_priority_weight": float(target["priority_weight"]),
            "proxy_inside_target_cell": str(inside).lower(),
            "proxy_response_range_rmse": float(proxy_range_rmse[-1]),
            "local_refinement_requested": str(bool(args.local_refinement)).lower(),
            "local_refinement_used": str(refinement_used).lower(),
            "optimizer_success": str(optimizer_success).lower(),
            "geometry_digest_sha256": digest,
            "generation_attempt_index": int(attempt),
        }
        for index, feature in enumerate(FEATURE_NAMES):
            row[f"target__{feature}"] = float(target_physical[index])
            row[f"target__{feature}__min"] = float(bounds[index, 0])
            row[f"target__{feature}__max"] = float(bounds[index, 1])
            row[f"pred_{feature}"] = float(proxy_physical[index])
        for index, column in enumerate(geometry_columns):
            row[column] = float(physical_geometry[index])
        rows.append(row)
        if len(rows) >= int(args.candidate_count):
            break

    return {
        "available": bool(rows),
        "candidate_rows": rows,
        "selected_candidate_count": len(rows),
        "unique_geometry_count": len(geometry_digests),
        "proxy_inside_target_cell_count": inside_count,
        "proxy_inside_target_cell_fraction": inside_count / len(rows) if rows else None,
        "local_refinement_used_count": refined_count,
        "mean_proxy_response_range_rmse": float(np.mean(proxy_range_rmse)) if proxy_range_rmse else None,
        "target_bins_represented": len({str(row["target_bin_key"]) for row in rows}),
    }


def _checks(
    tandem: dict[str, Any],
    model: dict[str, Any],
    targets: dict[str, Any],
    input_columns: tuple[str, ...],
    geometry_columns: tuple[str, ...],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    rows = analysis.get("candidate_rows") or []
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
    geometry_digests = [str(row.get("geometry_digest_sha256") or "") for row in rows]
    return {
        "tandem_artifact_complete": tandem.get("overall_status") in {"PASS", "COMPLETE_REVIEW_REQUIRED"},
        "source_rows_meet_gate": int(tandem.get("training_count") or 0) >= int(args.min_source_rows),
        "input_contract_is_lp_ls_q_absk": input_columns == EXPECTED_INPUT_COLUMNS,
        "geometry_contract_has_10_independent_variables": len(geometry_columns) == EXPECTED_GEOMETRY_COUNT,
        "weights_model_available": model.get("available") is True,
        "normalization_shapes_and_bounds_valid": _normalization_valid(
            model, len(EXPECTED_INPUT_COLUMNS), EXPECTED_GEOMETRY_COUNT
        )
        if model.get("available") is True
        else False,
        "target_csv_exists": targets.get("path_exists") is True,
        "target_columns_present": targets.get("columns_present") is True,
        "usable_sparse_target_cells_present": int(targets.get("usable_count") or 0) > 0,
        "target_allocations_cover_requested_budget": int(targets.get("allocation_sum") or 0) >= int(args.candidate_count),
        "candidate_budget_exact": len(rows) == int(args.candidate_count),
        "candidate_ids_unique": len(candidate_ids) == len(set(candidate_ids)) == int(args.candidate_count),
        "independent_geometry_vectors_unique": len(geometry_digests)
        == len(set(geometry_digests))
        == int(args.candidate_count),
        "all_candidates_unlabeled": bool(rows) and {row.get("label_status") for row in rows} == {"AWAITING_REAL_EMX"},
        "all_candidates_require_drc": bool(rows)
        and {row.get("drc_status") for row in rows} == {"NOT_EVALUATED_REQUIRED_BEFORE_REAL_EMX"},
        "no_fabricated_uncertainty_fields": bool(rows)
        and not any(any(str(key).startswith("pred_uncertainty_") for key in row) for row in rows),
        "all_target_samples_inside_declared_cells": bool(rows)
        and all(
            all(
                float(row[f"target__{feature}__min"])
                <= float(row[f"target__{feature}"])
                <= float(row[f"target__{feature}__max"])
                for feature in FEATURE_NAMES
            )
            for row in rows
        ),
    }


def _source(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size if path.is_file() else 0}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    return "\n".join(
        [
            "# Tandem sparse-cell candidate arm",
            "",
            f"- Overall status: `{payload['overall_status']}`",
            f"- Outcome: `{payload['outcome_status']}`",
            f"- Requested candidates: {payload['requested_candidate_count']}",
            f"- Built candidates: {analysis.get('selected_candidate_count', 0)}",
            f"- Target cells represented: {analysis.get('target_bins_represented', 0)}",
            f"- Frozen-proxy inside-cell fraction: {analysis.get('proxy_inside_target_cell_fraction')}",
            "",
            "This is an independent advisory acquisition arm. It must be compared at equal real-EMX budget against the existing KNN/local-seed arm. No row is a simulator label.",
            "",
        ]
    )


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None and float(number).is_integer() else None


if __name__ == "__main__":
    raise SystemExit(main())
