#!/usr/bin/env python3
"""Evaluate pair-matched inverse-only and refined candidates with real EMX.

The evaluator accepts no proxy-only result. Candidate identities, planned
geometries, Q=min(Qp,Qs), and nonempty S4P evidence are verified before the
paired fixed-range physical-feature errors are compared.
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

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


ARMS = ("inverse_only", "inverse_lbfgsb")
FEATURE_COLUMNS = ("lp_nh_center", "ls_nh_center", "q_center", "k_abs_center")
QP_QS_COLUMNS = ("qp_center", "qs_center")
FEATURE_SPANS = np.asarray((2.5, 2.5, 20.0, 0.8), dtype=float)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan_path = Path(args.plan_summary).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    geometry_columns = tuple(plan.get("geometry_columns") or ())
    planned = _load_planned(plan, plan_path, geometry_columns)
    result_paths = _parse_arm_results(args.arm_result)
    results = {
        arm: _load_results(
            result_paths.get(arm, Path("__missing__")),
            arm,
            planned.get(arm, {}),
            geometry_columns,
            bool(args.check_touchstone_exists),
        )
        for arm in ARMS
    }
    analysis = _analyze(planned, results, args)
    checks = _checks(plan, planned, result_paths, results, geometry_columns, analysis, args)
    status = "PASS" if all(checks.values()) else "FAIL"

    metrics_path = out_dir / "tandem_local_refinement_real_emx_pair_metrics.csv"
    figure_path = out_dir / "tandem_local_refinement_real_emx_comparison.png"
    summary_path = out_dir / "tandem_local_refinement_real_emx_summary.json"
    report_path = out_dir / "tandem_local_refinement_real_emx_report.md"
    _write_csv(metrics_path, analysis.get("pair_rows") or [])
    if analysis.get("available") is True:
        _plot(figure_path, analysis)

    if status != "PASS":
        decision = "DO_NOT_COMPARE_REFINEMENT_FIX_REAL_EMX_EVIDENCE"
        outcome = "INCOMPLETE"
    elif analysis.get("material_improvement_supported") is True:
        decision = "KEEP_LOCAL_REFINEMENT_FOR_FURTHER_SHARED_SPLIT_ABLATION"
        outcome = "REAL_EMX_REFINEMENT_IMPROVEMENT_SUPPORTED"
    else:
        decision = "RETAIN_INVERSE_ONLY_BASELINE_REFINEMENT_NOT_PROVEN"
        outcome = "REAL_EMX_REFINEMENT_IMPROVEMENT_NOT_PROVEN"
    public_analysis = {key: value for key, value in analysis.items() if key != "pair_rows"}
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "outcome_status": outcome,
        "plan_summary": str(plan_path),
        "plan_summary_sha256": _sha256(plan_path),
        "arm_result_paths": {arm: str(path) for arm, path in result_paths.items()},
        "arm_result_sha256": {arm: _sha256(path) for arm, path in result_paths.items()},
        "checks": checks,
        "arm_evidence": {arm: _public_counts(results[arm]) for arm in ARMS},
        "analysis": public_analysis,
        "artifacts": {
            "paired_metrics_csv": str(metrics_path),
            "real_emx_comparison_figure": str(figure_path),
            "report_md": str(report_path),
        },
        "scientific_boundary": (
            "A complete paired real-EMX comparison can support retaining or rejecting local refinement for the "
            "tested targets. It does not prove HFSS agreement, wafer accuracy, or generalization outside the "
            "declared physical and geometry ranges."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"outcome_status={outcome}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-summary", required=True)
    parser.add_argument("--arm-result", action="append", default=[], help="arm=returned_real_emx.csv")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-arm-budget", type=int)
    parser.add_argument("--min-success-fraction", type=float, default=0.98)
    parser.add_argument("--minimum-material-relative-improvement", type=float, default=0.02)
    parser.add_argument("--bootstrap-repeats", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--check-touchstone-exists", action="store_true", default=True)
    parser.add_argument("--no-check-touchstone-exists", action="store_false", dest="check_touchstone_exists")
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.expected_arm_budget is not None and args.expected_arm_budget < 1:
        parser.error("--expected-arm-budget must be positive")
    if not 0.0 < args.min_success_fraction <= 1.0:
        parser.error("--min-success-fraction must be in (0,1]")
    if args.minimum_material_relative_improvement < 0.0 or args.bootstrap_repeats < 100:
        parser.error("improvement must be nonnegative and bootstrap repeats at least 100")
    return args


def _parse_arm_results(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        arm, separator, raw_path = value.partition("=")
        if separator and arm in ARMS and arm not in result:
            result[arm] = Path(raw_path).expanduser().resolve()
    return result


def _load_planned(
    plan: dict[str, Any],
    plan_path: Path,
    geometry_columns: tuple[str, ...],
) -> dict[str, dict[str, dict[str, Any]]]:
    result = {arm: {} for arm in ARMS}
    raw_paths = ((plan.get("artifacts") or {}).get("arm_candidate_csvs") or {})
    for arm in ARMS:
        path = Path(str(raw_paths.get(arm) or ""))
        if not path.is_absolute():
            path = (plan_path.parent / path).resolve()
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = (
                {"candidate_id", "pair_id", "benchmark_arm", "label_status"}
                | {f"target__{name}" for name in FEATURE_COLUMNS}
                | set(geometry_columns)
            )
            if not required.issubset(set(reader.fieldnames or [])):
                continue
            for row in reader:
                candidate_id = str(row.get("candidate_id") or "")
                target = _float_row(row, tuple(f"target__{name}" for name in FEATURE_COLUMNS))
                geometry = _float_row(row, geometry_columns)
                if (
                    not candidate_id
                    or candidate_id in result[arm]
                    or row.get("benchmark_arm") != arm
                    or row.get("label_status") != "AWAITING_REAL_EMX"
                    or target is None
                    or geometry is None
                ):
                    continue
                result[arm][candidate_id] = {
                    "candidate_id": candidate_id,
                    "pair_id": str(row.get("pair_id") or ""),
                    "target": target,
                    "geometry": geometry,
                    "proxy_response_range_rmse": _finite(row.get("proxy_response_range_rmse")),
                }
    return result


def _load_results(
    path: Path,
    arm: str,
    planned: dict[str, dict[str, Any]],
    geometry_columns: tuple[str, ...],
    check_touchstone: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "path_exists": path.is_file(),
        "columns_present": False,
        "row_count": 0,
        "duplicate_candidate_id_count": 0,
        "unexpected_candidate_id_count": 0,
        "geometry_mismatch_count": 0,
        "invalid_feature_count": 0,
        "q_min_consistency_failure_count": 0,
        "touchstone_failure_count": 0,
        "drc_failure_count": 0,
        "valid": {},
        "all_candidate_ids": set(),
        "touchstone_paths": [],
    }
    if not path.is_file():
        result["missing_candidate_id_count"] = len(planned)
        result["success_fraction"] = 0.0
        return result
    required = (
        {"candidate_id", "pair_id", "benchmark_arm", "touchstone_path", "drc_status"}
        | set(FEATURE_COLUMNS)
        | set(QP_QS_COLUMNS)
        | set(geometry_columns)
    )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        result["columns_present"] = required.issubset(set(reader.fieldnames or []))
        if not result["columns_present"]:
            result["missing_candidate_id_count"] = len(planned)
            result["success_fraction"] = 0.0
            return result
        for row in reader:
            result["row_count"] += 1
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id in result["all_candidate_ids"]:
                result["duplicate_candidate_id_count"] += 1
                continue
            result["all_candidate_ids"].add(candidate_id)
            expected = planned.get(candidate_id)
            if expected is None or row.get("benchmark_arm") != arm or str(row.get("pair_id") or "") != expected["pair_id"]:
                result["unexpected_candidate_id_count"] += 1
                continue
            geometry = _float_row(row, geometry_columns)
            if geometry is None or not np.allclose(geometry, expected["geometry"], rtol=1.0e-10, atol=1.0e-10):
                result["geometry_mismatch_count"] += 1
                continue
            features = _float_row(row, FEATURE_COLUMNS)
            qp_qs = _float_row(row, QP_QS_COLUMNS)
            if features is None or qp_qs is None:
                result["invalid_feature_count"] += 1
                continue
            if not math.isclose(float(features[2]), min(float(qp_qs[0]), float(qp_qs[1])), rel_tol=1.0e-8, abs_tol=1.0e-8):
                result["q_min_consistency_failure_count"] += 1
                continue
            touchstone = Path(str(row.get("touchstone_path") or "")).expanduser()
            if not touchstone.is_absolute():
                touchstone = (path.parent / touchstone).resolve()
            touchstone_ok = touchstone.suffix.lower() == ".s4p"
            if check_touchstone:
                touchstone_ok = touchstone_ok and touchstone.is_file() and touchstone.stat().st_size > 0
            if not touchstone_ok:
                result["touchstone_failure_count"] += 1
                continue
            if str(row.get("drc_status") or "").strip().upper() != "PASS":
                result["drc_failure_count"] += 1
                continue
            if str(row.get("ok") or "true").strip().lower() not in {"true", "1", "yes", "pass"}:
                result["invalid_feature_count"] += 1
                continue
            result["touchstone_paths"].append(str(touchstone))
            result["valid"][candidate_id] = {
                "candidate_id": candidate_id,
                "pair_id": expected["pair_id"],
                "target": expected["target"],
                "geometry": expected["geometry"],
                "features": features,
                "touchstone_path": str(touchstone),
                "proxy_response_range_rmse": expected.get("proxy_response_range_rmse"),
            }
    expected_ids = set(planned)
    actual_ids = result["all_candidate_ids"]
    result["missing_candidate_id_count"] = len(expected_ids - actual_ids)
    result["unexpected_candidate_id_count"] += len(actual_ids - expected_ids)
    result["valid_count"] = len(result["valid"])
    result["success_fraction"] = len(result["valid"]) / len(planned) if planned else 0.0
    return result


def _analyze(
    planned: dict[str, dict[str, dict[str, Any]]],
    results: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    valid_by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for arm in ARMS:
        for item in results[arm].get("valid", {}).values():
            valid_by_pair.setdefault(item["pair_id"], {})[arm] = item
    rows = []
    for pair_id in sorted(valid_by_pair):
        pair = valid_by_pair[pair_id]
        if set(pair) != set(ARMS):
            continue
        baseline = pair["inverse_only"]
        refined = pair["inverse_lbfgsb"]
        if not np.allclose(baseline["target"], refined["target"], rtol=0.0, atol=1.0e-12):
            continue
        baseline_error = float(np.sqrt(np.mean(((baseline["features"] - baseline["target"]) / FEATURE_SPANS) ** 2)))
        refined_error = float(np.sqrt(np.mean(((refined["features"] - refined["target"]) / FEATURE_SPANS) ** 2)))
        rows.append(
            {
                "pair_id": pair_id,
                "inverse_only_candidate_id": baseline["candidate_id"],
                "inverse_lbfgsb_candidate_id": refined["candidate_id"],
                "inverse_only_real_emx_range_rmse": baseline_error,
                "inverse_lbfgsb_real_emx_range_rmse": refined_error,
                "paired_error_delta_refined_minus_baseline": refined_error - baseline_error,
                "refinement_wins": str(refined_error < baseline_error).lower(),
                "inverse_only_s4p": baseline["touchstone_path"],
                "inverse_lbfgsb_s4p": refined["touchstone_path"],
            }
        )
    if not rows:
        return {"available": False, "pair_rows": [], "paired_count": 0}
    baseline_errors = np.asarray([row["inverse_only_real_emx_range_rmse"] for row in rows], dtype=float)
    refined_errors = np.asarray([row["inverse_lbfgsb_real_emx_range_rmse"] for row in rows], dtype=float)
    deltas = refined_errors - baseline_errors
    mean_baseline = float(np.mean(baseline_errors))
    mean_refined = float(np.mean(refined_errors))
    relative_improvement = float((mean_baseline - mean_refined) / max(mean_baseline, 1.0e-18))
    confidence_interval = _bootstrap_mean_delta(deltas, int(args.bootstrap_repeats), int(args.seed))
    supported = (
        relative_improvement >= float(args.minimum_material_relative_improvement)
        and confidence_interval[1] < 0.0
    )
    return {
        "available": True,
        "pair_rows": rows,
        "paired_count": len(rows),
        "inverse_only_mean_real_emx_range_rmse": mean_baseline,
        "inverse_lbfgsb_mean_real_emx_range_rmse": mean_refined,
        "inverse_only_median_real_emx_range_rmse": float(np.median(baseline_errors)),
        "inverse_lbfgsb_median_real_emx_range_rmse": float(np.median(refined_errors)),
        "refinement_win_fraction": float(np.mean(refined_errors < baseline_errors)),
        "mean_relative_improvement": relative_improvement,
        "mean_delta_refined_minus_baseline": float(np.mean(deltas)),
        "bootstrap_95pct_mean_delta_interval": {"lower": confidence_interval[0], "upper": confidence_interval[1]},
        "minimum_material_relative_improvement": float(args.minimum_material_relative_improvement),
        "material_improvement_supported": bool(supported),
    }


def _bootstrap_mean_delta(values: np.ndarray, repeats: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = np.empty(repeats, dtype=float)
    for index in range(repeats):
        means[index] = float(np.mean(rng.choice(values, size=len(values), replace=True)))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _checks(
    plan: dict[str, Any],
    planned: dict[str, dict[str, dict[str, Any]]],
    paths: dict[str, Path],
    results: dict[str, dict[str, Any]],
    geometry_columns: tuple[str, ...],
    analysis: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    expected_budget = int(args.expected_arm_budget or plan.get("arm_budget") or 0)
    planned_ids = [candidate_id for arm in ARMS for candidate_id in planned[arm]]
    planned_geometries = [
        _vector_digest(item["geometry"]) for arm in ARMS for item in planned[arm].values()
    ]
    all_touchstones = [path for arm in ARMS for path in results[arm].get("touchstone_paths", [])]
    return {
        "plan_ready_and_awaiting_real_emx": plan.get("overall_status") == "PASS"
        and plan.get("decision") == "RUN_EQUAL_BUDGET_REAL_EMX_REFINEMENT_BENCHMARK"
        and plan.get("outcome_status") == "AWAITING_REAL_EMX",
        "geometry_contract_present": len(geometry_columns) == 10,
        "both_result_paths_present": set(paths) == set(ARMS) and all(path.is_file() for path in paths.values()),
        "planned_equal_arm_budget": expected_budget > 0 and all(len(planned[arm]) == expected_budget for arm in ARMS),
        "planned_candidate_ids_disjoint": len(planned_ids) == 2 * expected_budget == len(set(planned_ids)),
        "planned_geometries_disjoint": len(planned_geometries) == 2 * expected_budget == len(set(planned_geometries)),
        "all_result_columns_present": all(results[arm].get("columns_present") is True for arm in ARMS),
        "all_result_candidate_ids_exact": all(
            int(results[arm].get("missing_candidate_id_count") or 0) == 0
            and int(results[arm].get("unexpected_candidate_id_count") or 0) == 0
            and int(results[arm].get("duplicate_candidate_id_count") or 0) == 0
            for arm in ARMS
        ),
        "all_returned_geometries_match_plan": all(
            int(results[arm].get("geometry_mismatch_count") or 0) == 0 for arm in ARMS
        ),
        "q_min_semantics_consistent": all(
            int(results[arm].get("q_min_consistency_failure_count") or 0) == 0 for arm in ARMS
        ),
        "real_s4p_evidence_complete": all(
            int(results[arm].get("touchstone_failure_count") or 0) == 0 for arm in ARMS
        )
        and len(all_touchstones) == 2 * expected_budget
        and len(all_touchstones) == len(set(all_touchstones)),
        "explicit_drc_pass_complete": all(
            int(results[arm].get("drc_failure_count") or 0) == 0 for arm in ARMS
        ),
        "both_arms_meet_success_fraction": all(
            float(results[arm].get("success_fraction") or 0.0) >= float(args.min_success_fraction) for arm in ARMS
        ),
        "paired_real_emx_analysis_available": analysis.get("available") is True,
        "paired_fraction_meets_success_gate": int(analysis.get("paired_count") or 0)
        >= math.ceil(expected_budget * float(args.min_success_fraction)),
    }


def _plot(path: Path, analysis: dict[str, Any]) -> None:
    rows = analysis.get("pair_rows") or []
    baseline = np.asarray([row["inverse_only_real_emx_range_rmse"] for row in rows], dtype=float)
    refined = np.asarray([row["inverse_lbfgsb_real_emx_range_rmse"] for row in rows], dtype=float)
    figure, axes = plt.subplots(1, 2, figsize=(11.0, 4.1), constrained_layout=True)
    for index in range(len(rows)):
        axes[0].plot([0, 1], [baseline[index], refined[index]], color="#9aa3ad", alpha=0.35, linewidth=0.8)
    axes[0].scatter(np.zeros_like(baseline), baseline, color="#1769aa", s=20, label="Inverse only")
    axes[0].scatter(np.ones_like(refined), refined, color="#cf5c36", s=20, label="Inverse + L-BFGS-B")
    axes[0].set_xticks([0, 1], ["Inverse only", "Refined"])
    axes[0].set_ylabel("Real EMX fixed-range RMSE")
    axes[0].set_title("Pair-matched targets")
    axes[0].grid(axis="y", alpha=0.22)
    axes[1].scatter(baseline, refined, s=24, alpha=0.75, color="#147d64")
    limit = max(float(np.max(baseline)), float(np.max(refined)), 1.0e-12)
    axes[1].plot([0.0, limit], [0.0, limit], linestyle="--", color="#666666", linewidth=1.1)
    axes[1].set_xlabel("Inverse-only real EMX range RMSE")
    axes[1].set_ylabel("Refined real EMX range RMSE")
    axes[1].set_title("Points below diagonal favor refinement")
    axes[1].grid(alpha=0.22)
    figure.suptitle("Real EMX validation of tandem local refinement", fontsize=14, fontweight="bold")
    figure.patch.set_facecolor("white")
    figure.savefig(path, dpi=180, facecolor="white")
    plt.close(figure)


def _render_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    return "\n".join(
        [
            "# Real EMX tandem local-refinement evaluation",
            "",
            f"- Overall status: `{payload['overall_status']}`",
            f"- Outcome: `{payload['outcome_status']}`",
            f"- Paired real-EMX count: {analysis.get('paired_count', 0)}",
            f"- Inverse-only mean fixed-range RMSE: {analysis.get('inverse_only_mean_real_emx_range_rmse')}",
            f"- Refined mean fixed-range RMSE: {analysis.get('inverse_lbfgsb_mean_real_emx_range_rmse')}",
            f"- Mean relative improvement: {analysis.get('mean_relative_improvement')}",
            "",
            "Only nonempty returned S4P files contribute to this result. Proxy-only candidate scores are not accepted as validation evidence.",
            "",
        ]
    )


def _public_counts(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in {"valid", "all_candidate_ids", "touchstone_paths"}
        and isinstance(value, (str, int, float, bool, type(None)))
    }


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


def _float_row(row: dict[str, Any], columns: tuple[str, ...]) -> np.ndarray | None:
    values = [_finite(row.get(column)) for column in columns]
    return None if any(value is None for value in values) else np.asarray(values, dtype=float)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vector_digest(values: np.ndarray) -> str:
    return hashlib.sha256(np.round(np.asarray(values, dtype=np.float64), 12).tobytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
