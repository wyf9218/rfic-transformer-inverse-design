#!/usr/bin/env python3
"""Compare Q=min(Qp,Qs) and separate-Qp/Qs tandem models fairly."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    q_path = Path(args.q_summary).expanduser().resolve()
    qp_qs_path = Path(args.qp_qs_summary).expanduser().resolve()
    q_broadband_path = Path(args.q_broadband_summary).expanduser().resolve() if args.q_broadband_summary else None
    qp_qs_broadband_path = (
        Path(args.qp_qs_broadband_summary).expanduser().resolve() if args.qp_qs_broadband_summary else None
    )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "physical_feature_q_input_ablation_summary.json"
    report_path = out_dir / "physical_feature_q_input_ablation_report.md"

    q_data = _read_json(q_path)
    qp_qs_data = _read_json(qp_qs_path)
    q_broadband = _read_json(q_broadband_path) if q_broadband_path is not None else {}
    qp_qs_broadband = _read_json(qp_qs_broadband_path) if qp_qs_broadband_path is not None else {}
    q_common = ((q_data.get("metrics") or {}).get("common_lp_ls_qmin_absk_contract") or {})
    qp_qs_common = ((qp_qs_data.get("metrics") or {}).get("common_lp_ls_qmin_absk_contract") or {})
    q_error = _finite(q_common.get("test_range_normalized_rmse"))
    qp_qs_error = _finite(qp_qs_common.get("test_range_normalized_rmse"))
    q_split = q_data.get("split_audit") or {}
    qp_qs_split = qp_qs_data.get("split_audit") or {}
    checks = {
        "q_summary_exists": q_path.is_file(),
        "qp_qs_summary_exists": qp_qs_path.is_file(),
        "same_training_count": int(q_data.get("training_count") or 0)
        == int(qp_qs_data.get("training_count") or -1),
        "same_split_fingerprint": bool(q_split.get("split_fingerprint_sha256"))
        and q_split.get("split_fingerprint_sha256") == qp_qs_split.get("split_fingerprint_sha256"),
        "same_split_cell_partition": bool(q_split.get("physical_cell_partition_fingerprint_sha256"))
        and q_split.get("physical_cell_partition_fingerprint_sha256")
        == qp_qs_split.get("physical_cell_partition_fingerprint_sha256"),
        "q_common_metric_valid": q_common.get("status") == "PASS" and q_error is not None,
        "qp_qs_common_metric_valid": qp_qs_common.get("status") == "PASS" and qp_qs_error is not None,
        "q_representation_correct": q_common.get("q_representation") == "Q_scalar",
        "qp_qs_representation_correct": qp_qs_common.get("q_representation") == "min_Qp_Qs",
    }
    broadband_requested = q_broadband_path is not None or qp_qs_broadband_path is not None
    broadband_comparison = _broadband_comparison(
        q_broadband_path,
        qp_qs_broadband_path,
        q_broadband,
        qp_qs_broadband,
    )
    if broadband_requested:
        checks.update(broadband_comparison["checks"])
    comparable = all(checks.values())
    improvement = None
    broadband_improvement = broadband_comparison.get("relative_improvement")
    broadband_target_improvement = broadband_comparison.get("target_relative_improvement")
    if comparable and q_error is not None and qp_qs_error is not None:
        improvement = (q_error - qp_qs_error) / max(q_error, 1.0e-12)
        threshold = float(args.minimum_material_improvement)
        if (
            broadband_requested
            and broadband_improvement is not None
            and broadband_target_improvement is not None
        ):
            evidence = (improvement, broadband_target_improvement, broadband_improvement)
            if all(value >= threshold for value in evidence):
                decision = "REVIEW_QP_QS_FOR_FUTURE_INPUT_CONTRACT_WITH_BROADBAND_AND_REAL_EMX_CLOSURE"
            elif all(value <= -threshold for value in evidence):
                decision = "RETAIN_QMIN_BASELINE_QP_QS_IS_MATERIALLY_WORSE"
            elif all(abs(value) < threshold for value in evidence):
                decision = "RETAIN_SIMPLER_QMIN_NO_MATERIAL_QP_QS_GAIN"
            else:
                decision = "RETAIN_QMIN_MIXED_PHYSICAL_TARGET_AND_BROADBAND_EVIDENCE"
        elif improvement >= threshold:
            decision = "REVIEW_QP_QS_FOR_FUTURE_INPUT_CONTRACT_WITH_REAL_EMX_CLOSURE"
        elif improvement <= -threshold:
            decision = "RETAIN_QMIN_BASELINE_QP_QS_IS_MATERIALLY_WORSE"
        else:
            decision = "RETAIN_SIMPLER_QMIN_NO_MATERIAL_QP_QS_GAIN"
        overall_status = "PASS"
    else:
        decision = "FIX_ABLATION_COMPARISON_CONTRACT"
        overall_status = "FAIL"

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "checks": checks,
        "training_count": int(q_data.get("training_count") or 0),
        "common_metric": "fixed-range OOD RMSE over [Lp,Ls,min(Qp,Qs),|K|]",
        "q_model_error": q_error,
        "qp_qs_model_error": qp_qs_error,
        "qp_qs_relative_improvement": improvement,
        "broadband_comparison_requested": broadband_requested,
        "broadband_q_model_error": broadband_comparison.get("q_error"),
        "broadband_qp_qs_model_error": broadband_comparison.get("qp_qs_error"),
        "broadband_qp_qs_relative_improvement": broadband_improvement,
        "target_frequency_ghz": broadband_comparison.get("target_frequency_ghz"),
        "target_frequency_q_model_error": broadband_comparison.get("q_target_error"),
        "target_frequency_qp_qs_model_error": broadband_comparison.get("qp_qs_target_error"),
        "target_frequency_qp_qs_relative_improvement": broadband_target_improvement,
        "broadband_comparison": broadband_comparison,
        "minimum_material_improvement": float(args.minimum_material_improvement),
        "q_per_feature_physical_mae": q_common.get("per_feature_physical_mae") or {},
        "qp_qs_per_feature_physical_mae": qp_qs_common.get("per_feature_physical_mae") or {},
        "artifacts": {
            "q_summary": str(q_path),
            "qp_qs_summary": str(qp_qs_path),
            "q_broadband_summary": "" if q_broadband_path is None else str(q_broadband_path),
            "qp_qs_broadband_summary": "" if qp_qs_broadband_path is None else str(qp_qs_broadband_path),
            "report": str(report_path),
        },
        "scientific_boundary": (
            "This ablation may recommend a future input contract but never rewrites the active EMX production contract. "
            "When broadband summaries are supplied, both arms must use the same real S4P rows, target frequency, and physical-cell OOD split. "
            "A future input change requires material improvement in the physical target, target-frequency complex S, and full-band complex S. "
            "Any Qp/Qs adoption still requires DRC and real-EMX closed-loop verification."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")
    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--q-summary", required=True)
    parser.add_argument("--qp-qs-summary", required=True)
    parser.add_argument("--q-broadband-summary")
    parser.add_argument("--qp-qs-broadband-summary")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--minimum-material-improvement", type=float, default=0.05)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 <= args.minimum_material_improvement < 1.0:
        parser.error("--minimum-material-improvement must be in [0,1)")
    return args


def _broadband_comparison(
    q_path: Path | None,
    qp_qs_path: Path | None,
    q_data: dict[str, Any],
    qp_qs_data: dict[str, Any],
) -> dict[str, Any]:
    requested = q_path is not None or qp_qs_path is not None
    if not requested:
        return {
            "requested": False,
            "checks": {},
            "q_error": None,
            "qp_qs_error": None,
            "relative_improvement": None,
            "q_target_error": None,
            "qp_qs_target_error": None,
            "target_relative_improvement": None,
        }
    q_metrics = q_data.get("metrics") or {}
    qp_qs_metrics = qp_qs_data.get("metrics") or {}
    q_error = _finite(q_metrics.get("test_raw_complex_rmse"))
    qp_qs_error = _finite(qp_qs_metrics.get("test_raw_complex_rmse"))
    q_target_error = _finite(q_metrics.get("target_test_raw_complex_rmse"))
    qp_qs_target_error = _finite(qp_qs_metrics.get("target_test_raw_complex_rmse"))
    q_target_frequency = _finite(q_metrics.get("target_frequency_used_ghz"))
    qp_qs_target_frequency = _finite(qp_qs_metrics.get("target_frequency_used_ghz"))
    q_floor = _finite(q_metrics.get("test_pca_floor_complex_rmse"))
    qp_qs_floor = _finite(qp_qs_metrics.get("test_pca_floor_complex_rmse"))
    q_split = q_data.get("split_audit") or {}
    qp_qs_split = qp_qs_data.get("split_audit") or {}
    q_input_quality = q_data.get("input_s4p_quality") or {}
    qp_qs_input_quality = qp_qs_data.get("input_s4p_quality") or {}
    q_quality_thresholds = q_data.get("acceptance_thresholds") or {}
    qp_qs_quality_thresholds = qp_qs_data.get("acceptance_thresholds") or {}
    q_columns = list(q_data.get("predictor_columns") or [])
    qp_qs_columns = list(qp_qs_data.get("predictor_columns") or [])
    checks = {
        "broadband_both_summaries_requested": q_path is not None and qp_qs_path is not None,
        "broadband_q_summary_exists": q_path is not None and q_path.is_file(),
        "broadband_qp_qs_summary_exists": qp_qs_path is not None and qp_qs_path.is_file(),
        "broadband_models_complete": q_data.get("overall_status") == "COMPLETE_REVIEW_REQUIRED"
        and qp_qs_data.get("overall_status") == "COMPLETE_REVIEW_REQUIRED",
        "broadband_predictor_roles_are_physical_spec": q_data.get("predictor_role") == "physical_spec"
        and qp_qs_data.get("predictor_role") == "physical_spec",
        "broadband_predictor_dimensions_are_qmin_4_and_qp_qs_5": len(q_columns) == 4 and len(qp_qs_columns) == 5,
        "broadband_same_training_count": int(q_data.get("training_count") or 0)
        == int(qp_qs_data.get("training_count") or -1),
        "broadband_same_real_s4p_rows": bool(q_data.get("row_identity_sha256"))
        and q_data.get("row_identity_sha256") == qp_qs_data.get("row_identity_sha256"),
        "broadband_same_real_s4p_content": bool(q_data.get("touchstone_content_sha256"))
        and q_data.get("touchstone_content_sha256") == qp_qs_data.get("touchstone_content_sha256"),
        "broadband_same_reciprocal_training_content": bool(
            q_data.get("reciprocal_training_content_sha256")
        )
        and q_data.get("reciprocal_training_content_sha256")
        == qp_qs_data.get("reciprocal_training_content_sha256"),
        "broadband_raw_input_quality_audited": (
            q_input_quality.get("audit_stage")
            == "raw complex S4P before reciprocal symmetrization"
            and qp_qs_input_quality.get("audit_stage")
            == "raw complex S4P before reciprocal symmetrization"
        ),
        "broadband_input_quality_thresholds_predeclared": bool(
            q_quality_thresholds.get("input_quality_configured")
        )
        and bool(qp_qs_quality_thresholds.get("input_quality_configured")),
        "broadband_input_quality_thresholds_pass": bool(
            (q_input_quality.get("reciprocity") or {}).get("hard_threshold_pass")
        )
        and bool((q_input_quality.get("passivity") or {}).get("hard_threshold_pass"))
        and bool((qp_qs_input_quality.get("reciprocity") or {}).get("hard_threshold_pass"))
        and bool((qp_qs_input_quality.get("passivity") or {}).get("hard_threshold_pass")),
        "broadband_same_frequency_grid": bool(q_data.get("frequency_grid_sha256"))
        and q_data.get("frequency_grid_sha256") == qp_qs_data.get("frequency_grid_sha256"),
        "broadband_same_split_fingerprint": bool(q_split.get("split_fingerprint_sha256"))
        and q_split.get("split_fingerprint_sha256") == qp_qs_split.get("split_fingerprint_sha256"),
        "broadband_same_split_cell_partition": bool(q_split.get("physical_cell_partition_fingerprint_sha256"))
        and q_split.get("physical_cell_partition_fingerprint_sha256")
        == qp_qs_split.get("physical_cell_partition_fingerprint_sha256"),
        "broadband_errors_valid": q_error is not None and qp_qs_error is not None,
        "broadband_target_errors_valid": q_target_error is not None and qp_qs_target_error is not None,
        "broadband_same_target_frequency": q_target_frequency is not None
        and qp_qs_target_frequency is not None
        and math.isclose(q_target_frequency, qp_qs_target_frequency, rel_tol=0.0, abs_tol=1.0e-9),
        "broadband_same_pca_representation_floor": q_floor is not None
        and qp_qs_floor is not None
        and math.isclose(q_floor, qp_qs_floor, rel_tol=1.0e-10, abs_tol=1.0e-12),
    }
    improvement = None
    target_improvement = None
    if all(checks.values()) and q_error is not None and qp_qs_error is not None:
        improvement = (q_error - qp_qs_error) / max(q_error, 1.0e-12)
        if q_target_error is not None and qp_qs_target_error is not None:
            target_improvement = (q_target_error - qp_qs_target_error) / max(q_target_error, 1.0e-12)
    return {
        "requested": True,
        "checks": checks,
        "q_error": q_error,
        "qp_qs_error": qp_qs_error,
        "relative_improvement": improvement,
        "q_target_error": q_target_error,
        "qp_qs_target_error": qp_qs_target_error,
        "target_relative_improvement": target_improvement,
        "target_frequency_ghz": q_target_frequency,
        "metric": "physical-cell OOD full-band reciprocal complex-S RMSE",
        "target_metric": "physical-cell OOD target-frequency reciprocal complex-S RMSE",
        "pca_floor_q": q_floor,
        "pca_floor_qp_qs": qp_qs_floor,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _render_report(data: dict[str, Any]) -> str:
    improvement = data.get("qp_qs_relative_improvement")
    improvement_text = "unavailable" if improvement is None else f"{100.0 * float(improvement):.2f}%"
    broadband_improvement = data.get("broadband_qp_qs_relative_improvement")
    broadband_text = (
        "not requested" if not data.get("broadband_comparison_requested")
        else ("unavailable" if broadband_improvement is None else f"{100.0 * float(broadband_improvement):.2f}%")
    )
    target_improvement = data.get("target_frequency_qp_qs_relative_improvement")
    target_text = (
        "not requested" if not data.get("broadband_comparison_requested")
        else ("unavailable" if target_improvement is None else f"{100.0 * float(target_improvement):.2f}%")
    )
    return "\n".join(
        [
            "# Q versus Qp/Qs physical-feature input ablation",
            "",
            f"- Overall status: **{data['overall_status']}**",
            f"- Decision: **{data['decision']}**",
            f"- Q-model fixed-range OOD RMSE: `{data.get('q_model_error')}`",
            f"- Qp/Qs-model fixed-range OOD RMSE: `{data.get('qp_qs_model_error')}`",
            f"- Relative Qp/Qs improvement: `{improvement_text}`",
            f"- Broadband complex-S Qp/Qs improvement: `{broadband_text}`",
            f"- Target-frequency complex-S Qp/Qs improvement: `{target_text}`",
            "",
            data["scientific_boundary"],
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
