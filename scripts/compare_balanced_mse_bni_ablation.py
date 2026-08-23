#!/usr/bin/env python3
"""Compare fixed MSE and Balanced-MSE BNI tandem models on one complete OOD test set."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_tandem_geometry_anchor_ablation import (  # noqa: E402
    _paired_cluster_bootstrap,
    _per_feature_error,
    _read_json,
    _response_metric,
)


COMPLETE_STATUSES = {"PASS", "COMPLETE_REVIEW_REQUIRED"}
REQUIRED_BUDGET_FIELDS = (
    "seed",
    "split_seed",
    "validation_fraction",
    "test_fraction",
    "split_mode",
    "physical_cell_bins",
    "physical_cell_lower",
    "physical_cell_upper",
    "forward_depth",
    "forward_width",
    "inverse_depth",
    "inverse_width",
    "batch_size",
    "forward_epochs",
    "inverse_epochs",
    "patience",
    "learning_rate",
    "weight_decay",
    "response_weight",
    "geometry_anchor_weight",
    "topology_feasibility_weight",
    "response_ramp_fraction",
    "response_loss_scaling",
    "response_weight_schedule",
    "response_warmup_fraction",
    "response_adaptive_ema_decay",
    "response_adaptive_min_multiplier",
    "response_adaptive_max_multiplier",
    "normalization_floor",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    mse_path = Path(args.mse_summary).expanduser().resolve()
    bni_path = Path(args.bni_summary).expanduser().resolve()
    selection_path = Path(args.temperature_selection).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "balanced_mse_bni_ablation_summary.json"
    report_path = out_dir / "balanced_mse_bni_ablation_report.md"

    mse = _read_json(mse_path)
    bni = _read_json(bni_path)
    selection = _read_json(selection_path)
    mse_loss = mse.get("response_loss_contract") or {}
    bni_loss = bni.get("response_loss_contract") or {}
    bni_contract = bni_loss.get("balanced_mse_bni") or {}
    mse_split = mse.get("split_audit") or {}
    bni_split = bni.get("split_audit") or {}
    mse_args = mse.get("arguments") or {}
    bni_args = bni.get("arguments") or {}
    mse_budget = _training_budget(mse_args)
    bni_budget = _training_budget(bni_args)
    missing_budget_fields = [
        name
        for name in REQUIRED_BUDGET_FIELDS
        if name not in mse_args or name not in bni_args
    ]

    selected_tau = _finite(selection.get("selected_temperature_tau"), positive=True)
    bni_tau = _finite(bni_contract.get("temperature_tau"), positive=True)
    bni_arg_tau = _finite(bni_args.get("balanced_mse_temperature"), positive=True)
    selection_provenance = selection.get("provenance") or {}

    args.anchored_predictions = args.mse_predictions
    args.response_only_predictions = args.bni_predictions
    paired_bootstrap = _paired_cluster_bootstrap(mse, bni, args)
    checks = {
        "mse_summary_exists": mse_path.is_file(),
        "bni_summary_exists": bni_path.is_file(),
        "temperature_selection_exists": selection_path.is_file(),
        "both_models_complete": mse.get("overall_status") in COMPLETE_STATUSES
        and bni.get("overall_status") in COMPLETE_STATUSES,
        "mse_loss_family": mse_loss.get("family") == "mse",
        "bni_loss_family": bni_loss.get("family") == "balanced_mse_bni",
        "bni_contract_enabled": bni_contract.get("enabled") is True,
        "bni_prior_train_only": bni_contract.get("validation_or_test_rows_used_in_prior") is False,
        "same_training_count": int(mse.get("training_count") or 0)
        == int(bni.get("training_count") or -1),
        "same_training_csv_sha256": _is_sha256(str(mse.get("training_csv_sha256") or ""))
        and mse.get("training_csv_sha256") == bni.get("training_csv_sha256"),
        "same_input_columns": list(mse.get("input_columns") or []) == list(bni.get("input_columns") or []),
        "same_geometry_columns": list(mse.get("geometry_columns") or [])
        == list(bni.get("geometry_columns") or []),
        "same_split_fingerprint": _is_sha256(str(mse_split.get("split_fingerprint_sha256") or ""))
        and mse_split.get("split_fingerprint_sha256") == bni_split.get("split_fingerprint_sha256"),
        "same_cell_partition_fingerprint": _is_sha256(
            str(mse_split.get("physical_cell_partition_fingerprint_sha256") or "")
        )
        and mse_split.get("physical_cell_partition_fingerprint_sha256")
        == bni_split.get("physical_cell_partition_fingerprint_sha256"),
        "training_budget_fields_complete": not missing_budget_fields,
        "same_training_budget": not missing_budget_fields and mse_budget == bni_budget,
        "temperature_selection_pass": selection.get("overall_status") == "PASS",
        "temperature_selection_used_no_test_evidence": selection.get("test_metrics_used") is False
        and selection.get("test_predictions_used") is False
        and selection.get("hyperparameter_sweep_performed") is False,
        "temperature_selection_baseline_sha_matches": _is_sha256(
            str(selection_provenance.get("mse_summary_sha256") or "")
        )
        and selection_provenance.get("mse_summary_sha256") == _sha256_file(mse_path),
        "temperature_selection_training_sha_matches": selection_provenance.get("training_csv_sha256")
        == mse.get("training_csv_sha256"),
        "temperature_selection_split_sha_matches": selection_provenance.get("split_fingerprint_sha256")
        == mse_split.get("split_fingerprint_sha256"),
        "selected_temperature_matches_bni": selected_tau is not None
        and bni_tau is not None
        and bni_arg_tau is not None
        and math.isclose(selected_tau, bni_tau, rel_tol=1.0e-12, abs_tol=1.0e-15)
        and math.isclose(selected_tau, bni_arg_tau, rel_tol=1.0e-12, abs_tol=1.0e-15),
        "complete_paired_test_bootstrap": paired_bootstrap.get("status") == "PASS",
    }
    comparable = all(checks.values())
    mse_metric = _response_metric(mse)
    bni_metric = _response_metric(bni)
    point_improvement = (
        None
        if mse_metric is None or bni_metric is None
        else (mse_metric - bni_metric) / max(mse_metric, 1.0e-12)
    )
    lower_bounds = [
        _finite(paired_bootstrap.get("relative_improvement_ci_lower")),
        _finite(paired_bootstrap.get("cell_balanced_relative_improvement_ci_lower")),
        _finite(paired_bootstrap.get("p90_tail_relative_improvement_ci_lower")),
    ]
    upper_bounds = [
        _finite(paired_bootstrap.get("relative_improvement_ci_upper")),
        _finite(paired_bootstrap.get("cell_balanced_relative_improvement_ci_upper")),
        _finite(paired_bootstrap.get("p90_tail_relative_improvement_ci_upper")),
    ]
    threshold = float(args.minimum_material_improvement)
    if comparable and all(value is not None for value in lower_bounds + upper_bounds):
        if min(float(value) for value in lower_bounds if value is not None) >= threshold:
            decision = "REVIEW_BNI_FOR_REAL_EMX_CLOSURE"
        elif any(float(value) <= -threshold for value in upper_bounds if value is not None):
            decision = "RETAIN_MSE_BNI_IS_MATERIALLY_WORSE"
        else:
            decision = "RETAIN_MSE_NO_CONFIDENT_MATERIAL_BNI_GAIN"
        status = "PASS"
    else:
        decision = "FIX_BNI_ABLATION_CONTRACT"
        status = "FAIL"

    paired_bootstrap["arm_aliases"] = {"anchored": "mse", "response_only": "balanced_mse_bni"}
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "checks": checks,
        "missing_training_budget_fields": missing_budget_fields,
        "training_count": int(mse.get("training_count") or 0),
        "comparison_metric": "complete physical-cell OOD declared-range-normalized Lp/Ls/Q/|K| response RMSE",
        "mse_response_rmse": mse_metric,
        "bni_response_rmse": bni_metric,
        "bni_relative_improvement": point_improvement,
        "minimum_material_improvement": threshold,
        "decision_rule": "row_equal_cell_and_p90_tail_cluster_bootstrap_ci_lower_ge_material_improvement",
        "selected_temperature_tau": selected_tau,
        "shared_training_budget": mse_budget if mse_budget == bni_budget else None,
        "paired_cluster_bootstrap": paired_bootstrap,
        "mse_per_feature_range_normalized_mae": _per_feature_error(mse),
        "bni_per_feature_range_normalized_mae": _per_feature_error(bni),
        "artifacts": {
            "mse_summary": str(mse_path),
            "bni_summary": str(bni_path),
            "mse_predictions": str(Path(args.mse_predictions).expanduser().resolve()),
            "bni_predictions": str(Path(args.bni_predictions).expanduser().resolve()),
            "temperature_selection": str(selection_path),
            "report": str(report_path),
        },
        "scientific_boundary": (
            "PASS means the two fixed models were compared under one auditable contract; it does not mean BNI won. "
            "Only REVIEW_BNI_FOR_REAL_EMX_CLOSURE indicates all three paired 95% CI lower bounds cleared the "
            "predeclared gain. Even then BNI cannot replace MSE before DRC and real EMX closed-loop verification."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mse-summary", required=True)
    parser.add_argument("--bni-summary", required=True)
    parser.add_argument("--mse-predictions", required=True)
    parser.add_argument("--bni-predictions", required=True)
    parser.add_argument("--temperature-selection", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=20260711)
    parser.add_argument("--physical-cell-bins", type=int, default=4)
    parser.add_argument("--physical-cell-lower", default="0.5,0.5,5,0")
    parser.add_argument("--physical-cell-upper", default="3,3,25,0.8")
    parser.add_argument("--minimum-paired-test-rows", type=int, default=1000)
    parser.add_argument("--minimum-paired-test-cells", type=int, default=8)
    parser.add_argument("--metric-consistency-tolerance", type=float, default=1.0e-10)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 <= float(args.minimum_material_improvement) < 1.0:
        parser.error("--minimum-material-improvement must be in [0, 1)")
    if int(args.bootstrap_replicates) < 100:
        parser.error("--bootstrap-replicates must be at least 100")
    if not 0.0 < float(args.bootstrap_confidence) < 1.0:
        parser.error("--bootstrap-confidence must be in (0, 1)")
    if int(args.physical_cell_bins) < 2:
        parser.error("--physical-cell-bins must be at least 2")
    if int(args.minimum_paired_test_rows) < 1 or int(args.minimum_paired_test_cells) < 2:
        parser.error("paired bootstrap minimum rows/cells are invalid")
    if not math.isfinite(float(args.metric_consistency_tolerance)) or float(args.metric_consistency_tolerance) < 0.0:
        parser.error("--metric-consistency-tolerance must be finite and nonnegative")
    return args


def _training_budget(arguments: dict[str, Any]) -> dict[str, Any]:
    return {name: arguments.get(name) for name in REQUIRED_BUDGET_FIELDS}


def _finite(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0.0):
        return None
    return number


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _render_report(payload: dict[str, Any]) -> str:
    checks = "\n".join(
        f"- {name}: {'PASS' if passed else 'FAIL'}"
        for name, passed in payload["checks"].items()
    )
    bootstrap = payload.get("paired_cluster_bootstrap") or {}
    return (
        "# MSE versus Balanced-MSE BNI ablation\n\n"
        f"- Overall status: `{payload['overall_status']}`\n"
        f"- Decision: `{payload['decision']}`\n"
        f"- MSE response RMSE: `{payload['mse_response_rmse']}`\n"
        f"- BNI response RMSE: `{payload['bni_response_rmse']}`\n"
        f"- Selected tau: `{payload['selected_temperature_tau']}`\n"
        f"- Row CI lower: `{bootstrap.get('relative_improvement_ci_lower')}`\n"
        f"- Equal-cell CI lower: `{bootstrap.get('cell_balanced_relative_improvement_ci_lower')}`\n"
        f"- P90-tail CI lower: `{bootstrap.get('p90_tail_relative_improvement_ci_lower')}`\n\n"
        "## Contract checks\n\n"
        f"{checks}\n\n"
        "## Scientific boundary\n\n"
        f"{payload['scientific_boundary']}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
