#!/usr/bin/env python3
"""Optimize held-out physical targets with CMA-ES and a frozen tandem forward proxy.

This is a same-split surrogate baseline inspired by MOTIF-RF. It never creates
EM labels and cannot establish inverse-design success without real EMX closure.
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


FEATURE_SPANS = np.asarray([2.5, 2.5, 20.0, 0.8], dtype=float)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary_path = Path(args.tandem_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = _read_json(summary_path)
    weights_path = Path(summary.get("weights_npz") or "").expanduser().resolve()
    predictions_path = Path(args.targets_csv).expanduser().resolve() if args.targets_csv else Path(summary.get("test_predictions_csv") or "").expanduser().resolve()
    input_columns = list(summary.get("input_columns") or [])
    geometry_columns = list(summary.get("geometry_columns") or [])
    split = summary.get("split_audit") or {}
    checks = {
        "tandem_summary_exists": summary_path.is_file(),
        "tandem_execution_complete": summary.get("execution_status") == "PASS" or summary.get("overall_status") == "PASS",
        "physical_cell_ood_split": split.get("split_mode") == "physical_cell_grouped",
        "physical_cell_range_explicit": split.get("physical_cell_range_source") == "explicit",
        "physical_cell_overlap_zero": int(split.get("physical_cell_overlap_count") or 0) == 0,
        "four_physical_inputs": len(input_columns) == 4 and _semantics(input_columns) == ["lp", "ls", "q", "k"],
        "geometry_columns_present": bool(geometry_columns),
        "weights_exist": weights_path.is_file(),
        "held_out_targets_exist": predictions_path.is_file(),
    }
    rows = _read_csv(predictions_path)
    checks["held_out_targets_meet_minimum"] = len(rows) >= int(args.min_targets)
    dependency_error = ""
    try:
        import cma  # type: ignore
    except Exception as exc:  # noqa: BLE001
        cma = None
        dependency_error = f"{type(exc).__name__}: {exc}"
    checks["cma_dependency_available"] = cma is not None

    records: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    model_contract: dict[str, Any] = {}
    if all(checks.values()):
        model = _load_forward_model(weights_path, len(input_columns), len(geometry_columns))
        model_contract = model["contract"]
        selected_rows = rows[: min(len(rows), int(args.max_targets))]
        records = _optimize_rows(selected_rows, input_columns, geometry_columns, model, args, cma)
        metrics = _metrics(records, input_columns, geometry_columns, split)
        checks.update(
            {
                "all_targets_completed": len(records) == len(selected_rows),
                "all_records_finite": bool(records) and all(_record_finite(row, input_columns, geometry_columns) for row in records),
                "geometry_bounds_respected": bool(records)
                and all(float(row["geometry_bound_violation_count"]) == 0.0 for row in records),
                "optimization_budget_respected": bool(records)
                and all(int(row["proxy_evaluation_count"]) <= int(args.max_evaluations) for row in records),
            }
        )
    else:
        checks.update(
            {
                "all_targets_completed": False,
                "all_records_finite": False,
                "geometry_bounds_respected": False,
                "optimization_budget_respected": False,
            }
        )
    status = "PASS" if all(checks.values()) else "FAIL"
    results_csv = out_dir / "tandem_forward_cmaes_inverse_predictions.csv"
    summary_out = out_dir / "tandem_forward_cmaes_inverse_summary.json"
    report_out = out_dir / "tandem_forward_cmaes_inverse_report.md"
    _write_csv(results_csv, records)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "KEEP_AS_REAL_EMX_CLOSURE_CANDIDATE" if status == "PASS" else "DO_NOT_USE_FIX_BASELINE_FIRST",
        "outcome_status": "SURROGATE_ONLY_NOT_REAL_EMX_VALIDATED",
        "eligible_for_model_success_claim": False,
        "tandem_summary_source": _file_source(summary_path),
        "weights_source": _file_source(weights_path),
        "held_out_targets_source": _file_source(predictions_path),
        "input_columns": input_columns,
        "geometry_columns": geometry_columns,
        "model_contract": model_contract,
        "target_count": len(records),
        "metrics": metrics,
        "checks": checks,
        "dependency_error": dependency_error,
        "arguments": vars(args),
        "artifacts": {"predictions_csv": str(results_csv), "report": str(report_out)},
        "method_reference": {
            "paper": "MOTIF-RF: Multi-template On-chip Transformer Synthesis Incorporating Frequency-domain Self-transfer Learning for RFIC Design Automation",
            "venue": "ASP-DAC 2026",
            "url": "https://arxiv.org/abs/2511.21970",
            "adaptation": "CMA-ES over the project frozen physical-feature forward proxy with the same physical-cell OOD targets as tandem.",
        },
        "scientific_boundary": (
            "CMA-ES minimizes only the frozen forward-proxy response error inside the observed training geometry envelope. "
            "The result is not a DRC pass, not a real EMX label, and not evidence of HFSS/EMX agreement."
        ),
    }
    summary_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_out.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"summary={summary_out}")
    print(f"predictions={results_csv}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tandem-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--targets-csv")
    parser.add_argument("--min-targets", type=int, default=128)
    parser.add_argument("--max-targets", type=int, default=1000)
    parser.add_argument("--max-evaluations", type=int, default=256)
    parser.add_argument("--population-size", type=int, default=16)
    parser.add_argument("--sigma0", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_targets < 1 or args.max_targets < args.min_targets:
        parser.error("target limits must satisfy 1 <= min <= max")
    if args.max_evaluations < 8 or args.population_size < 4:
        parser.error("CMA-ES requires at least 8 evaluations and population size >=4")
    if not math.isfinite(args.sigma0) or args.sigma0 <= 0.0:
        parser.error("--sigma0 must be finite and positive")
    return args


def _load_forward_model(path: Path, input_count: int, geometry_count: int) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as archive:
        keys = set(archive.files)
        weight_indices = sorted(
            int(key.rsplit("_", 1)[1]) for key in keys if key.startswith("forward_weight_")
        )
        bias_indices = sorted(
            int(key.rsplit("_", 1)[1]) for key in keys if key.startswith("forward_bias_")
        )
        if not weight_indices or weight_indices != list(range(len(weight_indices))) or bias_indices != weight_indices:
            raise ValueError("forward weights/biases are incomplete")
        weights = [np.asarray(archive[f"forward_weight_{index}"], dtype=float) for index in weight_indices]
        biases = [np.asarray(archive[f"forward_bias_{index}"], dtype=float) for index in bias_indices]
        norm_keys = (
            "x_mean", "x_scale", "y_mean", "y_scale", "geometry_lower", "geometry_upper"
        )
        normalization = {
            key: np.asarray(archive[f"normalization__{key}"], dtype=float)
            for key in norm_keys
        }
        dimension_weights = np.asarray(
            archive["normalization__response_loss_dimension_weights"]
            if "normalization__response_loss_dimension_weights" in keys
            else np.ones(input_count),
            dtype=float,
        )
    if weights[0].shape[0] != geometry_count or weights[-1].shape[1] != input_count:
        raise ValueError("forward model dimensions do not match summary columns")
    if any(array.shape != (input_count,) for array in (normalization["x_mean"], normalization["x_scale"])):
        raise ValueError("feature normalization dimensions are invalid")
    if any(array.shape != (geometry_count,) for array in (normalization["y_mean"], normalization["y_scale"], normalization["geometry_lower"], normalization["geometry_upper"])):
        raise ValueError("geometry normalization dimensions are invalid")
    if dimension_weights.shape != (input_count,) or np.any(dimension_weights <= 0.0):
        raise ValueError("response-loss dimension weights are invalid")
    return {
        "weights": weights,
        "biases": biases,
        "normalization": normalization,
        "dimension_weights": dimension_weights,
        "contract": {
            "forward_layer_shapes": [list(weight.shape) for weight in weights],
            "feature_count": input_count,
            "geometry_count": geometry_count,
            "geometry_envelope": "observed training min/max in normalized coordinates",
            "objective": "weighted normalized physical-feature MSE",
        },
    }


def _optimize_rows(
    rows: list[dict[str, str]],
    input_columns: list[str],
    geometry_columns: list[str],
    model: dict[str, Any],
    args: argparse.Namespace,
    cma: Any,
) -> list[dict[str, Any]]:
    norm = model["normalization"]
    lower = np.asarray(norm["geometry_lower"], dtype=float)
    upper = np.asarray(norm["geometry_upper"], dtype=float)
    midpoint = 0.5 * (lower + upper)
    dimension_weights = np.asarray(model["dimension_weights"], dtype=float)
    records: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        target_physical = np.asarray(
            [_required_float(row, f"target__{column.removeprefix('input__')}") for column in input_columns],
            dtype=float,
        )
        target_normalized = (target_physical - norm["x_mean"]) / norm["x_scale"]
        options = {
            "bounds": [lower.tolist(), upper.tolist()],
            "seed": int(args.seed) + row_index,
            "popsize": int(args.population_size),
            "maxfevals": int(args.max_evaluations),
            "verbose": -9,
            "verb_log": 0,
        }
        es = cma.CMAEvolutionStrategy(midpoint.tolist(), float(args.sigma0), options)
        evaluation_count = 0
        best_geometry = midpoint.copy()
        best_cost = _objective(best_geometry[None, :], target_normalized, model)[0]
        while not es.stop() and evaluation_count < int(args.max_evaluations):
            remaining = int(args.max_evaluations) - evaluation_count
            batch_size = min(int(es.sp.popsize), remaining)
            if batch_size < max(4, int(getattr(es.sp, "mu", 1))):
                break
            candidates = np.asarray(es.ask(batch_size), dtype=float)
            candidates = np.clip(candidates, lower[None, :], upper[None, :])
            costs = _objective(candidates, target_normalized, model)
            es.tell(candidates.tolist(), costs.tolist())
            evaluation_count += len(candidates)
            candidate_index = int(np.argmin(costs))
            if float(costs[candidate_index]) < float(best_cost):
                best_cost = float(costs[candidate_index])
                best_geometry = candidates[candidate_index].copy()
        reconstructed_normalized = _predict(best_geometry[None, :], model["weights"], model["biases"])[0]
        reconstructed_physical = reconstructed_normalized * norm["x_scale"] + norm["x_mean"]
        predicted_geometry_physical = best_geometry * norm["y_scale"] + norm["y_mean"]
        tandem_reconstructed = np.asarray(
            [_required_float(row, f"reconstructed__{column.removeprefix('input__')}") for column in input_columns],
            dtype=float,
        )
        truth_geometry = np.asarray(
            [_required_float(row, f"paired_geometry__{column.removeprefix('geom__')}") for column in geometry_columns],
            dtype=float,
        )
        range_error = (reconstructed_physical - target_physical) / FEATURE_SPANS
        tandem_range_error = (tandem_reconstructed - target_physical) / FEATURE_SPANS
        record: dict[str, Any] = {
            "target_index": row_index,
            "source_row_index": row.get("source_row_index", ""),
            "source_evaluation": row.get("source_evaluation", ""),
            "source_geometry_identity_sha256": row.get("source_geometry_identity_sha256", ""),
            "proxy_evaluation_count": evaluation_count,
            "best_proxy_cost": float(best_cost),
            "cmaes_range_normalized_row_rmse": float(np.sqrt(np.mean(range_error**2))),
            "tandem_range_normalized_row_rmse": float(np.sqrt(np.mean(tandem_range_error**2))),
            "geometry_bound_violation_count": int(np.count_nonzero((best_geometry < lower) | (best_geometry > upper))),
        }
        for index, column in enumerate(input_columns):
            name = column.removeprefix("input__")
            record[f"target__{name}"] = float(target_physical[index])
            record[f"cmaes_reconstructed__{name}"] = float(reconstructed_physical[index])
            record[f"tandem_reconstructed__{name}"] = float(tandem_reconstructed[index])
        for index, column in enumerate(geometry_columns):
            name = column.removeprefix("geom__")
            record[f"paired_geometry__{name}"] = float(truth_geometry[index])
            record[f"cmaes_geometry__{name}"] = float(predicted_geometry_physical[index])
        records.append(record)
    return records


def _objective(candidates: np.ndarray, target: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    prediction = _predict(candidates, model["weights"], model["biases"])
    error = prediction - target[None, :]
    weights = np.asarray(model["dimension_weights"], dtype=float)
    return np.mean(error**2 * weights[None, :], axis=1)


def _predict(values: np.ndarray, weights: list[np.ndarray], biases: list[np.ndarray]) -> np.ndarray:
    output = np.asarray(values, dtype=float)
    for index, (weight, bias) in enumerate(zip(weights, biases)):
        output = output @ weight + bias
        if index < len(weights) - 1:
            output = _gelu(output)
    return output


def _gelu(value: np.ndarray) -> np.ndarray:
    constant = math.sqrt(2.0 / math.pi)
    return 0.5 * value * (1.0 + np.tanh(constant * (value + 0.044715 * value**3)))


def _metrics(records: list[dict[str, Any]], input_columns: list[str], geometry_columns: list[str], split: dict[str, Any]) -> dict[str, Any]:
    target = np.asarray([[float(row[f"target__{column.removeprefix('input__')}"]) for column in input_columns] for row in records])
    cmaes = np.asarray([[float(row[f"cmaes_reconstructed__{column.removeprefix('input__')}"]) for column in input_columns] for row in records])
    tandem = np.asarray([[float(row[f"tandem_reconstructed__{column.removeprefix('input__')}"]) for column in input_columns] for row in records])
    cma_error = (cmaes - target) / FEATURE_SPANS[None, :]
    tandem_error = (tandem - target) / FEATURE_SPANS[None, :]
    cma_rows = np.sqrt(np.mean(cma_error**2, axis=1))
    tandem_rows = np.sqrt(np.mean(tandem_error**2, axis=1))
    lower = np.asarray(split.get("physical_cell_lower") or [0.5, 0.5, 5.0, 0.0], dtype=float)
    upper = np.asarray(split.get("physical_cell_upper") or [3.0, 3.0, 25.0, 0.8], dtype=float)
    bins = int(split.get("physical_cell_bins_per_dimension") or 4)
    cells = _cell_indices(target, lower, upper, bins)
    unique_cells = sorted(set(cells))
    cma_cell = [float(np.mean(cma_rows[[index for index, cell in enumerate(cells) if cell == key]])) for key in unique_cells]
    tandem_cell = [float(np.mean(tandem_rows[[index for index, cell in enumerate(cells) if cell == key]])) for key in unique_cells]
    return {
        "comparison_scope": "same held-out physical-cell OOD targets and same frozen forward proxy",
        "target_count": len(records),
        "physical_cell_count": len(unique_cells),
        "cmaes": _error_summary(cma_error, cma_rows, cma_cell),
        "tandem": _error_summary(tandem_error, tandem_rows, tandem_cell),
        "paired_proxy_row_improvement_fraction": float(np.mean(cma_rows < tandem_rows)),
        "paired_proxy_row_tie_fraction": float(np.mean(np.isclose(cma_rows, tandem_rows, atol=1.0e-12))),
        "proxy_only_winner": "CMAES" if float(np.mean(cma_rows)) < float(np.mean(tandem_rows)) else "TANDEM",
        "geometry_anchor_mae": {
            column: float(
                np.mean(
                    np.abs(
                        np.asarray([row[f"cmaes_geometry__{column.removeprefix('geom__')}"] for row in records], dtype=float)
                        - np.asarray([row[f"paired_geometry__{column.removeprefix('geom__')}"] for row in records], dtype=float)
                    )
                )
            )
            for column in geometry_columns
        },
        "interpretation": "The proxy-only winner is an ablation result, not an adoption decision; real EMX closure decides physical success.",
    }


def _error_summary(error: np.ndarray, row_rmse: np.ndarray, equal_cell: list[float]) -> dict[str, Any]:
    return {
        "range_normalized_mae": float(np.mean(np.abs(error))),
        "range_normalized_rmse": float(np.sqrt(np.mean(error**2))),
        "row_rmse_mean": float(np.mean(row_rmse)),
        "row_rmse_p95": float(np.quantile(row_rmse, 0.95)),
        "row_rmse_max": float(np.max(row_rmse)),
        "equal_cell_row_rmse_mean": float(np.mean(equal_cell)),
        "equal_cell_row_rmse_max": float(np.max(equal_cell)),
    }


def _cell_indices(values: np.ndarray, lower: np.ndarray, upper: np.ndarray, bins: int) -> list[tuple[int, ...]]:
    scaled = (values - lower[None, :]) / np.maximum(upper - lower, 1.0e-12)[None, :]
    indices = np.floor(np.clip(scaled, 0.0, np.nextafter(1.0, 0.0)) * bins).astype(int)
    return [tuple(int(item) for item in row) for row in indices]


def _semantics(columns: list[str]) -> list[str]:
    result = []
    for column in columns:
        name = column.lower().removeprefix("input__")
        if "lp_nh" in name:
            result.append("lp")
        elif "ls_nh" in name:
            result.append("ls")
        elif "q_center" in name and "qp_" not in name and "qs_" not in name:
            result.append("q")
        elif "k_abs" in name or name == "k_center":
            result.append("k")
        else:
            result.append("unknown")
    return result


def _record_finite(row: dict[str, Any], input_columns: list[str], geometry_columns: list[str]) -> bool:
    fields = [f"cmaes_reconstructed__{column.removeprefix('input__')}" for column in input_columns]
    fields += [f"cmaes_geometry__{column.removeprefix('geom__')}" for column in geometry_columns]
    return all(math.isfinite(float(row[field])) for field in fields)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _required_float(row: dict[str, Any], key: str) -> float:
    try:
        value = float(row.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"missing numeric {key}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite {key}")
    return value


def _file_source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _render_report(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or {}
    lines = [
        "# Frozen-forward CMA-ES inverse baseline",
        "",
        f"- Status: **{data['overall_status']}**",
        f"- Outcome: **{data['outcome_status']}**",
        f"- Targets: `{data['target_count']}`",
        "",
    ]
    if metrics:
        lines.extend(
            [
                f"- Proxy-only winner: `{metrics['proxy_only_winner']}`",
                f"- CMA-ES range-normalized RMSE: `{metrics['cmaes']['range_normalized_rmse']:.6g}`",
                f"- Tandem range-normalized RMSE: `{metrics['tandem']['range_normalized_rmse']:.6g}`",
                f"- Paired CMA-ES improvement fraction: `{metrics['paired_proxy_row_improvement_fraction']:.4f}`",
                "",
            ]
        )
    lines.extend([data["scientific_boundary"], ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
