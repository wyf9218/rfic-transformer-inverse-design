#!/usr/bin/env python3
"""Build the final model evidence matrix without conflating campaign and publication gates.

The one-million campaign can be complete before sampled EMX-HFSS correlation is
publication-ready.  This audit keeps those decisions separate and accepts only
traceable, same-contract, real Touchstone evidence for the cross-solver gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRIMARY_METRICS = ("lp_nh", "ls_nh", "q", "k")
EXPECTED_INPUT_CONTRACT = ["Lp", "Ls", "Q=min(Qp,Qs)", "|K|"]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    completion_path = Path(args.campaign_completion_summary).expanduser().resolve()
    learning_path = Path(args.learning_curve_summary).expanduser().resolve()
    manifest_path = Path(args.final_model_manifest).expanduser().resolve()
    completion = _read_json(completion_path)
    learning = _read_json(learning_path)
    manifest = _read_json(manifest_path)

    campaign_checks = _campaign_checks(completion, args)
    model_checks = _model_checks(completion, learning, manifest, args)
    campaign_status = "PASS" if all(campaign_checks.values()) else "FAIL"
    model_status = "PASS" if all(model_checks.values()) else "FAIL"

    validation_records = [
        _audit_hfss_record(Path(raw).expanduser().resolve(), args)
        for raw in args.hfss_validation_record
    ]
    cross_solver_status = _cross_solver_status(validation_records, args)
    cross_solver_bias = _cross_solver_bias_diagnostic(validation_records, cross_solver_status, args)
    if campaign_status != "PASS" or model_status != "PASS":
        publication_status = "FAIL_BASE_MODEL_EVIDENCE"
    elif cross_solver_status == "PASS":
        publication_status = "PASS"
    elif cross_solver_status.startswith("WAITING"):
        publication_status = cross_solver_status
    else:
        publication_status = "FAIL_EMX_HFSS_VALIDATION"

    overall_status = "PASS" if campaign_status == model_status == "PASS" else "FAIL"
    decision = (
        "PUBLICATION_EVIDENCE_READY"
        if publication_status == "PASS"
        else (
            "CAMPAIGN_AND_MODEL_EVIDENCE_COMPLETE_PUBLICATION_VALIDATION_PENDING"
            if overall_status == "PASS" and publication_status.startswith("WAITING")
            else (
                "CAMPAIGN_AND_MODEL_EVIDENCE_COMPLETE_PUBLICATION_VALIDATION_FAILED"
                if overall_status == "PASS"
                else "DO_NOT_CLAIM_FINAL_MODEL_EVIDENCE_COMPLETE"
            )
        )
    )

    summary_path = out_dir / "final_model_publication_readiness_summary.json"
    report_path = out_dir / "final_model_publication_readiness_report.md"
    evidence_marker = out_dir / "final_model_evidence_matrix.pass"
    publication_marker = out_dir / "final_model_publication_ready.pass"
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "campaign_completion_status": campaign_status,
        "model_evidence_status": model_status,
        "cross_solver_validation_status": cross_solver_status,
        "publication_readiness_status": publication_status,
        "decision": decision,
        "campaign_checks": campaign_checks,
        "model_checks": model_checks,
        "hfss_validation": {
            "required_minimum_independent_samples": int(args.min_hfss_samples),
            "provided_record_count": len(validation_records),
            "passing_record_count": sum(item.get("overall_status") == "PASS" for item in validation_records),
            "records": validation_records,
            "acceptance_contract": {
                "real_sources_only": True,
                "touchstone_suffix": ".s4p",
                "port_count": 4,
                "port_pairs": "1,2:3,4",
                "full_frequency_grid": "5-60 GHz, 0.5 GHz, 111 points",
                "target_frequency_ghz": 15.0,
                "primary_metrics": list(PRIMARY_METRICS),
                "maximum_percent_error": float(args.max_percent_error),
                "valid_window_rule": "pre-resonance window includes 5-15 GHz and stops no later than 30 GHz",
                "same_geometry_process_port_contract_required": True,
            },
        },
        "cross_solver_bias_diagnostic": cross_solver_bias,
        "artifacts": {
            "campaign_completion_summary": str(completion_path),
            "learning_curve_summary": str(learning_path),
            "final_model_manifest": str(manifest_path),
            "report": str(report_path),
            "evidence_matrix_marker": str(evidence_marker),
            "publication_ready_marker": str(publication_marker),
        },
        "scientific_boundary": (
            "overall_status=PASS proves that the final campaign/model evidence matrix is complete. "
            "Only publication_readiness_status=PASS proves the separately sampled same-geometry, "
            "same-process, same-port EMX-HFSS correlation gate. Proxy predictions, old S8P evidence, "
            "or visually plausible curves never satisfy that gate. The cross-solver bias diagnostic is advisory: "
            "it can nominate a co-kriging-style residual ablation only after the raw <=10% gate passes, and "
            "calibrated values can never replace raw EMX-HFSS publication evidence."
        ),
        "arguments": vars(args),
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(_render_report(payload), encoding="utf-8")

    _set_marker(evidence_marker, overall_status == "PASS")
    _set_marker(publication_marker, publication_status == "PASS")
    print(f"overall_status={overall_status}")
    print(f"campaign_completion_status={campaign_status}")
    print(f"model_evidence_status={model_status}")
    print(f"cross_solver_validation_status={cross_solver_status}")
    print(f"publication_readiness_status={publication_status}")
    print(f"summary={summary_path}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-completion-summary", required=True)
    parser.add_argument("--learning-curve-summary", required=True)
    parser.add_argument("--final-model-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--hfss-validation-record", action="append", default=[])
    parser.add_argument("--expected-total", type=int, default=1_000_000)
    parser.add_argument("--expected-checkpoints", type=int, default=10)
    parser.add_argument("--min-hfss-samples", type=int, default=5)
    parser.add_argument("--max-percent-error", type=float, default=10.0)
    parser.add_argument("--min-bias-percent-for-calibration-review", type=float, default=2.0)
    parser.add_argument("--min-bias-sign-agreement", type=float, default=0.80)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.expected_total < 1 or args.expected_checkpoints < 1 or args.min_hfss_samples < 1:
        parser.error("counts must be positive")
    if not math.isfinite(args.max_percent_error) or args.max_percent_error <= 0:
        parser.error("max percent error must be finite and positive")
    if (
        not math.isfinite(args.min_bias_percent_for_calibration_review)
        or args.min_bias_percent_for_calibration_review < 0.0
        or not 0.5 <= args.min_bias_sign_agreement <= 1.0
    ):
        parser.error("bias threshold must be nonnegative and sign agreement must be in [0.5, 1]")
    return args


def _campaign_checks(completion: dict[str, Any], args: argparse.Namespace) -> dict[str, bool]:
    checks = completion.get("checks") if isinstance(completion.get("checks"), dict) else {}
    dataset = completion.get("dataset_audit") if isinstance(completion.get("dataset_audit"), dict) else {}
    uniformity = completion.get("final_uniformity_checks") if isinstance(completion.get("final_uniformity_checks"), dict) else {}
    required_completion_checks = (
        "pool_summary_pass",
        "accepted_count_at_least_expected",
        "all_rows_finite_and_in_range",
        "all_rows_have_complete_geometry",
        "independent_geometry_unique",
        "touchstone_paths_unique",
        "all_touchstone_paths_are_s4p",
        "all_touchstone_files_nonempty",
        "all_frequency_metadata_match",
        "q_equals_min_qp_qs",
        "checkpoint_contract_pass",
        "learning_curve_has_ten_comparable_checkpoints",
        "final_uniformity_contract_pass",
        "final_model_manifest_pass",
    )
    return {
        "completion_summary_pass": completion.get("overall_status") == "PASS",
        "completion_decision_is_final": completion.get("decision") == "ACCEPTED_1M_REAL_EMX_CAMPAIGN_COMPLETE",
        "all_required_completion_checks_pass": all(checks.get(name) is True for name in required_completion_checks),
        "accepted_row_count": int(dataset.get("row_count") or 0) >= int(args.expected_total),
        "unique_geometry_count": int(dataset.get("unique_geometry_digest_count") or 0) >= int(args.expected_total),
        "unique_s4p_count": int(dataset.get("unique_touchstone_path_digest_count") or 0) >= int(args.expected_total),
        "strict_uniformity_components_pass": bool(uniformity) and all(value is True for value in uniformity.values()),
    }


def _model_checks(
    completion: dict[str, Any],
    learning: dict[str, Any],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, bool]:
    split = manifest.get("tandem_ood_split_audit") if isinstance(manifest.get("tandem_ood_split_audit"), dict) else {}
    metrics = manifest.get("tandem_ood_metrics") if isinstance(manifest.get("tandem_ood_metrics"), dict) else {}
    tandem_metrics = metrics.get("tandem_inverse") if isinstance(metrics.get("tandem_inverse"), dict) else {}
    range_norm = metrics.get("range_normalization") if isinstance(metrics.get("range_normalization"), dict) else {}
    comparison = learning.get("comparison_contract") if isinstance(learning.get("comparison_contract"), dict) else {}
    completion_artifacts = completion.get("artifacts") if isinstance(completion.get("artifacts"), dict) else {}
    recorded_manifest = Path(str(completion_artifacts.get("final_model_manifest") or "")).expanduser()
    provided_manifest = Path(str(args.final_model_manifest)).expanduser().resolve()
    recorded_resolved = recorded_manifest.resolve() if str(recorded_manifest) else Path()
    return {
        "learning_curve_pass": learning.get("overall_status") == "PASS",
        "ten_comparable_checkpoints": int(learning.get("checkpoint_count") or 0) == int(args.expected_checkpoints)
        and comparison.get("comparable") is True,
        "completion_points_to_same_manifest": recorded_resolved == provided_manifest,
        "final_manifest_pass": manifest.get("overall_status") == "PASS"
        and manifest.get("model_test_status") == "PASS",
        "final_manifest_count": int(manifest.get("accepted_checkpoint_count") or 0) >= int(args.expected_total),
        "formal_input_contract": manifest.get("input_contract") == EXPECTED_INPUT_CONTRACT,
        "formal_output_contract": manifest.get("output_contract") == "10 independent geometry variables",
        "uniformity_pass": manifest.get("uniformity_status") == "PASS",
        "broadband_touchstone_readiness": manifest.get("broadband_sparameter_readiness_status") == "PASS",
        "physical_cell_ood_split": split.get("split_mode") == "physical_cell_grouped"
        and int(split.get("physical_cell_overlap_count") or 0) == 0
        and split.get("all_rows_assigned_once") is True,
        "stable_ood_partition": split.get("physical_cell_partition_stable_for_existing_cells") is True,
        "direct_tandem_shared_split": manifest.get("direct_tandem_shared_split_fingerprint") is True,
        "declared_range_metric": range_norm.get("source") == "declared_physical_cell_range"
        and _finite(tandem_metrics.get("test_response_range_normalized_rmse")) is not None,
        "q_definition_audited": manifest.get("input_ablation_readiness_status") == "PASS",
        "boundary_ood_audit_recorded": manifest.get("physical_feature_boundary_ood_stress_status")
        == "NOT_REPEATED_AFTER_900K",
        "physical_spec_spectral_expander_recorded": manifest.get("physical_spec_spectral_expander_status")
        == "COMPLETE_REVIEW_REQUIRED"
        and _finite(manifest.get("physical_spec_spectral_expander_test_complex_rmse")) is not None,
    }


def _audit_hfss_record(record_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    record = _read_json(record_path)
    contract = record.get("contract") if isinstance(record.get("contract"), dict) else {}
    full_path = _resolve_child(record_path, record.get("full_grid_comparison_summary"))
    valid_path = _resolve_child(record_path, record.get("valid_window_comparison_summary"))
    full = _read_json(full_path)
    valid = _read_json(valid_path)
    emx_path = Path(str(full.get("emx_source") or "")).expanduser()
    hfss_path = Path(str(full.get("hfss_ads_source") or "")).expanduser()
    full_window = full.get("frequency_window_hz") if isinstance(full.get("frequency_window_hz"), dict) else {}
    valid_window = valid.get("frequency_window_hz") if isinstance(valid.get("frequency_window_hz"), dict) else {}
    full_target = full.get("target_marker") if isinstance(full.get("target_marker"), dict) else {}
    target_metrics = full_target.get("metrics") if isinstance(full_target.get("metrics"), dict) else {}
    valid_metrics = valid.get("metrics") if isinstance(valid.get("metrics"), dict) else {}
    grid_checks = full.get("frequency_grid_checks") if isinstance(full.get("frequency_grid_checks"), dict) else {}
    tol = float(args.frequency_tolerance_hz)
    stop = _finite(valid_window.get("max"))
    start = _finite(valid_window.get("min"))
    expected_valid_points = None
    if start is not None and stop is not None:
        expected_valid_points = int(round((stop - start) / 0.5e9)) + 1
    same_sources = (
        str(full.get("emx_source") or "") == str(valid.get("emx_source") or "")
        and str(full.get("hfss_ads_source") or "") == str(valid.get("hfss_ads_source") or "")
    )
    checks = {
        "record_declared_pass": record.get("overall_status") == "PASS",
        "sample_id_present": bool(str(record.get("sample_id") or "").strip()),
        "comparison_summaries_exist": full_path.is_file() and valid_path.is_file(),
        "comparison_sources_identical": same_sources,
        "same_geometry_verified": contract.get("same_geometry_verified") is True,
        "same_process_stack_verified": contract.get("same_process_stack_verified") is True,
        "same_port_mapping_verified": contract.get("same_port_mapping_verified") is True,
        "independent_geometry": contract.get("independent_geometry") is True,
        "contract_hashes_present": all(
            _valid_sha(contract.get(name))
            for name in ("geometry_contract_sha256", "process_stack_contract_sha256", "port_contract_sha256")
        ),
        "four_port_s4p_contract": str(contract.get("expected_touchstone_suffix") or "").lower() == ".s4p"
        and int(contract.get("expected_port_count") or 0) == 4
        and str(contract.get("port_pairs") or "").replace(" ", "") == "1,2:3,4",
        "real_s4p_sources_exist": _nonempty_s4p(emx_path) and _nonempty_s4p(hfss_path),
        "touchstone_hashes_match": _matches_sha(emx_path, contract.get("emx_touchstone_sha256"))
        and _matches_sha(hfss_path, contract.get("hfss_touchstone_sha256")),
        "full_grid_5_60_111": _close(full_window.get("min"), 5.0e9, tol)
        and _close(full_window.get("max"), 60.0e9, tol)
        and int(full_window.get("count") or 0) == 111,
        "full_grid_checks_pass": bool(grid_checks)
        and all(isinstance(item, dict) and item.get("status") == "PASS" for item in grid_checks.values()),
        "target_is_15ghz": full_target.get("frequency_status") == "PASS"
        and _close(full_target.get("nearest_frequency_hz"), 15.0e9, tol),
        "target_primary_metrics_within_limit": _metric_items_pass(
            target_metrics, "percent_error", float(args.max_percent_error)
        ),
        "valid_window_declared_pre_resonance": contract.get("valid_window_basis")
        in {"BELOW_MIN_SRF_OVER_2", "DECLARED_LOW_FREQUENCY_PRE_RESONANCE"},
        "valid_window_includes_5_to_15ghz": start is not None
        and stop is not None
        and abs(start - 5.0e9) <= tol
        and stop >= 15.0e9 - tol
        and stop <= 30.0e9 + tol,
        "valid_window_half_ghz_grid": expected_valid_points is not None
        and int(valid_window.get("count") or 0) == expected_valid_points,
        "valid_window_primary_metrics_within_limit": _metric_items_pass(
            valid_metrics, "max_percent_error", float(args.max_percent_error)
        ),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "overall_status": status,
        "record_path": str(record_path),
        "sample_id": str(record.get("sample_id") or ""),
        "geometry_contract_sha256": str(contract.get("geometry_contract_sha256") or ""),
        "target_marker_metrics": {
            name: {
                key: target_metrics.get(name, {}).get(key)
                for key in ("status", "emx", "hfss_ads", "abs_error", "percent_error")
            }
            for name in PRIMARY_METRICS
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "artifacts": {
            "full_grid_comparison_summary": str(full_path),
            "valid_window_comparison_summary": str(valid_path),
            "emx_touchstone": str(emx_path),
            "hfss_touchstone": str(hfss_path),
        },
    }


def _cross_solver_status(records: list[dict[str, Any]], args: argparse.Namespace) -> str:
    if not records:
        return "WAITING_FOR_EMX_HFSS_VALIDATION"
    sample_ids = [str(item.get("sample_id") or "") for item in records]
    geometry_hashes = [str(item.get("geometry_contract_sha256") or "") for item in records]
    if len(set(sample_ids)) != len(sample_ids) or len(set(geometry_hashes)) != len(geometry_hashes):
        return "FAIL_DUPLICATE_HFSS_VALIDATION_SAMPLE"
    if any(item.get("overall_status") != "PASS" for item in records):
        return "FAIL_EMX_HFSS_VALIDATION"
    if len(records) < int(args.min_hfss_samples):
        return "WAITING_FOR_MORE_EMX_HFSS_SAMPLES"
    return "PASS"


def _cross_solver_bias_diagnostic(
    records: list[dict[str, Any]],
    cross_solver_status: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    metric_values: dict[str, list[float]] = {name: [] for name in PRIMARY_METRICS}
    for record in records:
        metrics = record.get("target_marker_metrics")
        if not isinstance(metrics, dict):
            continue
        for name in PRIMARY_METRICS:
            item = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
            emx = _finite(item.get("emx"))
            hfss = _finite(item.get("hfss_ads"))
            percent_error = _finite(item.get("percent_error"))
            if emx is None or hfss is None or percent_error is None:
                continue
            direction = 1.0 if hfss > emx else (-1.0 if hfss < emx else 0.0)
            metric_values[name].append(direction * abs(percent_error))

    metrics: dict[str, Any] = {}
    stable_metrics: list[str] = []
    for name, raw_values in metric_values.items():
        values = [float(value) for value in raw_values if math.isfinite(float(value))]
        positive = sum(value > 0.0 for value in values)
        negative = sum(value < 0.0 for value in values)
        nonzero = positive + negative
        sign_agreement = max(positive, negative) / nonzero if nonzero else 0.0
        median_signed = _median(values)
        mean_absolute = sum(abs(value) for value in values) / len(values) if values else None
        stable = bool(
            len(values) >= int(args.min_hfss_samples)
            and median_signed is not None
            and abs(median_signed) >= float(args.min_bias_percent_for_calibration_review)
            and sign_agreement >= float(args.min_bias_sign_agreement)
        )
        if stable:
            stable_metrics.append(name)
        metrics[name] = {
            "sample_count": len(values),
            "median_signed_percent": median_signed,
            "mean_absolute_percent": mean_absolute,
            "sign_agreement_fraction": float(sign_agreement),
            "stable_systematic_bias": stable,
        }

    complete_signed_metrics = all(
        int(metrics[name]["sample_count"]) >= int(args.min_hfss_samples) for name in PRIMARY_METRICS
    )
    if cross_solver_status != "PASS":
        status = "WAITING_FOR_RAW_CROSS_SOLVER_PASS"
        decision = "DO_NOT_CALIBRATE_A_FAILED_OR_INCOMPLETE_RAW_VALIDATION"
    elif not complete_signed_metrics:
        status = "WAITING_FOR_SIGNED_TARGET_METRICS"
        decision = "REGENERATE_COMPARISON_SUMMARIES_WITH_SIGNED_EMX_HFSS_VALUES"
    else:
        status = "READY"
        decision = (
            "REVIEW_COKRIGING_STYLE_CROSS_SOLVER_RESIDUAL_ABLATION"
            if stable_metrics
            else "NO_STABLE_CROSS_SOLVER_BIAS_DETECTED"
        )
    return {
        "status": status,
        "decision": decision,
        "stable_bias_metrics": stable_metrics,
        "metrics": metrics,
        "thresholds": {
            "minimum_samples": int(args.min_hfss_samples),
            "minimum_absolute_median_bias_percent": float(args.min_bias_percent_for_calibration_review),
            "minimum_sign_agreement_fraction": float(args.min_bias_sign_agreement),
        },
        "literature_basis": {
            "source": "Machine-learning-based global optimization of microwave passives with variable-fidelity EM models and response features, Scientific Reports 2024",
            "url": "https://www.nature.com/articles/s41598-024-56823-7",
            "adaptation": "Diagnose a repeatable paired-solver residual before any co-kriging-style correction ablation.",
        },
        "scientific_boundary": (
            "This diagnostic never changes raw EMX or HFSS values and never participates in the <=10% publication gate. "
            "A stable signed bias only nominates a leave-one-geometry-out residual-model ablation."
        ),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _metric_items_pass(items: dict[str, Any], error_key: str, limit: float) -> bool:
    for name in PRIMARY_METRICS:
        item = items.get(name) if isinstance(items.get(name), dict) else {}
        error = _finite(item.get(error_key))
        if item.get("status") != "PASS" or error is None or error > limit:
            return False
    return True


def _resolve_child(parent: Path, raw: Any) -> Path:
    path = Path(str(raw or "")).expanduser()
    if not path.is_absolute():
        path = parent.parent / path
    return path.resolve()


def _nonempty_s4p(path: Path) -> bool:
    try:
        return path.suffix.lower() == ".s4p" and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _matches_sha(path: Path, expected: Any) -> bool:
    expected_text = str(expected or "").lower()
    return _valid_sha(expected_text) and path.is_file() and _sha256(path) == expected_text


def _valid_sha(value: Any) -> bool:
    return bool(SHA256_RE.fullmatch(str(value or "").lower()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _close(value: Any, target: float, tolerance: float) -> bool:
    number = _finite(value)
    return number is not None and abs(number - target) <= tolerance


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _set_marker(path: Path, present: bool) -> None:
    if present:
        path.touch()
    elif path.exists():
        path.unlink()


def _render_report(data: dict[str, Any]) -> str:
    hfss = data["hfss_validation"]
    bias = data.get("cross_solver_bias_diagnostic") or {}
    lines = [
        "# Final model evidence and publication-readiness matrix",
        "",
        "| Evidence gate | Status | Meaning |",
        "| --- | --- | --- |",
        f"| One-million real-EMX campaign | **{data['campaign_completion_status']}** | Real, in-range, geometry-unique S4P data and strict uniformity |",
        f"| Final model evidence | **{data['model_evidence_status']}** | Ten comparable checkpoints and physical-cell OOD model contract |",
        f"| Sampled EMX-HFSS correlation | **{data['cross_solver_validation_status']}** | Same geometry/process/port, S4P, valid-window Lp/Ls/Q/K <=10% |",
        f"| Publication readiness | **{data['publication_readiness_status']}** | All preceding gates plus at least {hfss['required_minimum_independent_samples']} independent HFSS samples |",
        "",
        f"- Decision: **{data['decision']}**",
        f"- HFSS records: `{hfss['provided_record_count']}` provided, `{hfss['passing_record_count']}` individually passing",
        f"- Cross-solver bias diagnostic: **{bias.get('status')}**, decision **{bias.get('decision')}**",
        f"- Stable signed-bias metrics: `{bias.get('stable_bias_metrics')}`",
        "",
        "## HFSS sample audit",
        "",
        "| Sample | Status | Failed checks |",
        "| --- | --- | --- |",
    ]
    if not hfss["records"]:
        lines.append("| - | WAITING | No current four-port same-contract validation record supplied |")
    else:
        for item in hfss["records"]:
            failed = ", ".join(item.get("failed_checks") or []) or "-"
            lines.append(f"| {item.get('sample_id') or '-'} | {item.get('overall_status')} | {failed} |")
    lines.extend(["", data["scientific_boundary"], ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
