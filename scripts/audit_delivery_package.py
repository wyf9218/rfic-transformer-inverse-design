#!/usr/bin/env python3
"""Audit the desktop delivery package and MARS handoff bundle.

This is a reproducibility gate for local artifacts. It verifies checksums,
report assets, portable MARS commands, and conservative acceptance-matrix
status. It does not prove that MARS jobs, HFSS, ADS, or EMX have completed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_VALIDATION_SCRIPTS = (
    "audit_dataset_touchstones.py",
    "audit_delivery_package.py",
    "audit_ads_metric_formula_consistency.py",
    "audit_geometry_quality.py",
    "audit_hfss_model_geometry_assets.py",
    "audit_mars_run_progress.py",
    "audit_photo_matched_vs_target_geometry.py",
    "watch_mars_run_progress.py",
    "discover_and_verify_mars_emx_return.py",
    "watch_mars_emx_return.py",
    "audit_response_feature_coverage.py",
    "audit_sampling_distribution.py",
    "audit_touchstone_transformer.py",
    "audit_zin_coverage.py",
    "audit_248k_launch_readiness.py",
    "audit_ads_photo_reference_alignment.py",
    "backfill_dataset_frequency_metadata.py",
    "build_acceptance_matrix.py",
    "build_clean_delivery_zip.py",
    "build_emx_first_validation_gate.py",
    "build_photo_matched_hfss_reference_evidence.py",
    "build_project_validation_report.py",
    "build_validation_chain_decision_card.py",
    "build_mars_next_action_packet.py",
    "compare_emx_hfss_ads.py",
    "discover_mars_emx_cadence_paths.py",
    "extract_touchstone_response_features.py",
    "package_mars_dataset_run.py",
    "patch_mars_config_paths.py",
    "plot_emx_hfss_ads_style_metrics.py",
    "preflight_dataset_config.py",
    "prepare_mars_wideband_config.py",
    "prepare_target_emx_postrun_validation.py",
    "prepare_target_emx_wideband_rerun.py",
    "run_dataset_quality_gates.py",
    "run_accepted_emx_hfss_ads_validation.py",
    "verify_accepted_emx_hfss_ads_figures.py",
    "run_local_project_health_check.py",
    "run_package_selfcheck_compare.py",
    "run_hfss_emx_validation_batch.py",
    "scan_s4p_ads_photo_reference_candidates.py",
    "select_hfss_validation_samples.py",
    "verify_mars_dataset_package.py",
    "verify_mars_handoff_install.py",
    "verify_target_emx_postrun_package.py",
)

WIDEBAND_HANDOFF_CONFIG = Path("configs/mars_dataset_500_wideband_20260613.yaml")
TARGET_EMX_HANDOFF_COMMAND = Path(
    "project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh"
)
TARGET_EMX_POSTRUN_HANDOFF_COMMAND = Path(
    "project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_postrun_validation.commands.sh"
)
QUALITY_GATE_REQUIRED_FRAGMENTS = (
    "--require-emx",
    "--expected-port-mode single_ended_shield_grounded",
    "--expected-pin-purpose 51",
    "--require-clearance-audit",
    "--expected-frequency-start-ghz 5.0",
    "--expected-frequency-stop-ghz 50.0",
    "--expected-frequency-step-ghz 0.1",
    "--expected-frequency-points 451",
    "--max-touchstone-frequency-checks 500",
    "--audit-sampling-distribution",
    "--sampling-require-uniform-closer-than-normal",
    "--sampling-min-uniform-vs-normal-fields-fraction 0.8",
    "--sampling-min-histogram-entropy-frac 0.85",
    "--sampling-max-min-norm 0.05",
    "--sampling-min-max-norm 0.95",
    "--sampling-space-filling-strata 20",
    "--sampling-max-space-filling-empty-strata-frac 0",
    "--sampling-max-space-filling-duplicate-frac 0",
    "--touchstone-all",
    "--touchstone-target-frequency-ghz 15",
    "--touchstone-positive-window-start-ghz 5.0",
    "--touchstone-positive-window-stop-ghz 30",
    "--touchstone-shape-window-start-ghz 5.0",
    "--touchstone-shape-window-stop-ghz 30",
    "--touchstone-max-shape-spike-ratio 4",
    "--touchstone-max-shape-relative-step 0.25",
    "--extract-response-features",
    "--audit-response-feature-coverage",
    "--response-require-cm",
    "--response-min-valid-count 500",
    "--audit-zin-coverage",
    "--zin-min-valid-count 500",
    "--select-hfss-samples",
    "--hfss-sample-count 8",
)
PROGRESS_AUDIT_REQUIRED_FRAGMENTS = (
    "scripts/audit_mars_run_progress.py",
    "--expected-count 500",
    "--expected-frequency-start-ghz 5.0",
    "--expected-frequency-stop-ghz 50.0",
    "--expected-frequency-step-ghz 0.1",
    "--expected-frequency-points 451",
    "--max-touchstone-frequency-checks 500",
    "--require-clearance-audit",
    "--require-geometry-quality",
    "--internal-angle-deg 135",
    "--terminal-angle-deg 90",
    "--require-emx-command",
    "--expected-port-mode single_ended_shield_grounded",
    "--expected-pin-purpose 51",
)
COMPARE_GATE_REQUIRED_FRAGMENTS = (
    "scripts/compare_emx_hfss_ads.py",
    "--compare-start-ghz 5",
    "--compare-stop-ghz 50",
    "--min-frequency-points 451",
    "--expected-frequency-step-ghz 0.1",
    "--expected-frequency-points 451",
    "--require-matching-frequency-grid",
    "--max-percent-error 5",
    "ADS no-extrapolation coverage",
)
VALIDATION_CHAIN_REQUIRED_FRAGMENTS = (
    "BLOCKED_BY_EMX_REFERENCE",
    "BLOCKED_BY_HFSS_GEOMETRY_GATE",
    "PASS_DIAGNOSTIC_ONLY",
    "DO_NOT_USE_HFSS_COMPARISON",
    "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN",
    "--hfss-geometry-summary",
    "EMX-first golden reference",
    "HFSS geometry asset traceability",
    "HFSS physical S4P gate",
    "Accepted EMX-vs-HFSS/ADS comparison",
    "ADS no-extrapolation plot grid",
    "WAIT_FOR_HFSS_GEOMETRY_AUDIT",
    "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
    "A diagnostic HFSS geometry or physical PASS cannot override",
)
BATCH_COMPARE_REQUIRED_FRAGMENTS = (
    "--selection-csv",
    "--hfss-dir",
    "--run-available",
    "--require-all-present",
    "--require-all-pass",
    "--require-matching-frequency-grid",
    "hfss_emx_validation_batch_summary.json",
    "no_extrapolation_status",
    "ADS no-extrapolation coverage",
    "_compare_summary_failures",
    "_compare_source_failures",
    "criterion_max_percent_error",
    "metric_{name}_max_percent_error",
    "source_mismatch",
)
PRODUCTION_READINESS_REQUIRED_FRAGMENTS = (
    "--production-preflight-summary",
    "--wideband-quality-summary",
    "--hfss-batch-summary",
    "--production-count",
    "--production-batch-size",
    "--zin-target-envelope-config",
    "--response-target-envelope-config",
    "Zin target-envelope config",
    "response target-envelope config",
    "TEMPLATE_ONLY",
    "run_dataset_quality_gates.py",
    "248k_launch_readiness_summary.json",
    "248k_launch_commands.sh",
    "REQUIRED_QUALITY_STEPS",
    "_hfss_batch_no_extrapolation_failures",
    "_compare_summary_failures",
    "summary_path_missing",
    "matching HFSS/ADS frequency grid",
    "emx_source",
    "hfss_ads_source",
    "criterion_max_percent_error",
    "source_mismatch",
    "metric_{name}_max_percent_error",
    "no_extrapolation_status",
    "strict grid/no-extrapolation PASS",
)
ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS = (
    "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
    "ACCEPT_HFSS_VALIDATION_SAMPLE",
    "accepted EMX import verifier evidence",
    "accepted EMX import artifact bundle",
    "_accepted_import_artifact_bundle_check",
    "accepted_emx_reference_bundle",
    "READY_FOR_HFSS",
    "accepted EMX import core metric artifact",
    "--hfss-geometry-summary",
    "_hfss_geometry_summary_checks",
    "HFSS geometry asset audit evidence",
    "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
    "HFSS geometry required asset checks",
    "HFSS geometry artifact paths",
    "HFSS STEP model",
    "EMX-first stop before HFSS comparison",
    "Touchstone physical gate coupling arguments",
    "Touchstone physical gate required physics checks",
    "post-run validation artifact content",
    "emx_first_validation_gate_core_metrics.png",
    "--expected-source-kind",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "EMX-first gate internal checks",
    "--min-target-abs-k",
    "--min-window-abs-k",
    "--max-target-abs-k",
    "review-only and cannot be used for final HFSS .s4p acceptance",
    "FAIL/DO_NOT_USE_HFSS_COMPARISON until a real HFSS .s4p passes the physical gate",
    "ADS/Python formula note",
    "formula_note",
    "Z_diff = transpose(T) * Z_single * T",
    "ADS Data Display equation template",
    "Zp = Z11 - Z12 + Z22 - Z21",
    "Zm = Z31 - Z32 + Z42 - Z41",
    "_compare_source_checks",
    "_compare_criterion_checks",
    "_compare_metric_checks",
    "_plot_data_checks",
    "_target_marker_records",
    "_write_ads_style_target_marker_tables",
    "_target_marker_checks",
    "_artifact_records",
    "_artifact_manifest_checks",
    "ADS-style plot_data integrity",
    "ADS-style target marker table",
    "target_marker_paths",
    "figure_records",
    "target_marker_records",
    "ADS-style core metric figure manifest",
    "ADS-style target marker manifest",
    "ads_style_target_marker_values",
    "K/Qp/Qs/Lp/Ls marker values recorded",
    "finite K/Qp/Qs/Lp/Ls arrays",
    "contains non-finite values",
    "not generated because ADS-style plot_data integrity failed",
    "EMX-vs-HFSS compare source traceability",
    "EMX-vs-HFSS compare core metric errors",
    "criterion_max_percent_error",
    "metric_{name}_max_percent_error",
    "required grid/no-extrapolation checks PASS",
    "compare summary does not contain plot_data",
    "_png_figure_failures",
    "missing PNG IHDR chunk",
    "_png_figure_content_failures",
    "MIN_ACCEPTED_FIGURE_COLOR_DELTA",
    "blank or nearly constant PNG",
    "valid dimensions and nonblank content",
    "ADS no-extrapolation coverage",
)
ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS = (
    "ACCEPT_HFSS_VALIDATION_SAMPLE",
    "ACCEPT_FINAL_LP_LS_Q_K_FIGURES",
    "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES",
    "Touchstone 2.1",
    "port pairing must be recorded",
    "Touchstone reference impedance",
    "ADS Data Display equation template",
    "Zp = Z11 - Z12 + Z22 - Z21",
    "Zs = Z33 - Z34 + Z44 - Z43",
    "Zm = Z31 - Z32 + Z42 - Z41",
    "M  = imag(Zdiff[2,1]) / omega",
    "K  = M / sqrt(abs(Lp * Ls))",
    "Qp = imag(Zdiff[1,1]) / real(Zdiff[1,1])",
    "Qs = imag(Zdiff[2,2]) / real(Zdiff[2,2])",
    "REQUIRED_CORE_METRICS",
    "REQUIRED_TARGET_MARKER_METRICS",
    "k",
    "qp",
    "qs",
    "lp_nh",
    "ls_nh",
    "REQUIRED_FIGURES",
    "emx_ads_style_core_metrics",
    "hfss_ads_style_core_metrics",
    "emx_vs_hfss_ads_style_core_overlay",
    "ADS no-extrapolation coverage",
    "hfss_geometry_summary",
    "HFSS geometry asset audit evidence",
    "HFSS geometry required asset checks",
    "HFSS geometry artifact paths",
    "accepted ADS-style plot_data arrays",
    "accepted target marker table",
    "accepted final figure manifest",
    "accepted target marker manifest",
    "_target_marker_checks",
    "_target_marker_row_failures",
    "_target_marker_single_row_failures",
    "_artifact_record_checks",
    "_artifact_record_failures",
    "target_marker_paths",
    "figure_records",
    "target_marker_records",
    "sha256 mismatch",
    "K/Qp/Qs/Lp/Ls marker CSV/Markdown match plot_data",
    "accepted K/Qp/Qs/Lp/Ls <= 5% errors",
    "blank or nearly constant PNG",
    "MIN_PNG_COLOR_DELTA",
    "frequency_window_hz",
    "max_percent_error",
)
FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS = (
    "scripts/verify_accepted_emx_hfss_ads_figures.py",
    "--accepted-summary",
    "--hfss-geometry-summary",
    "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
    "ACCEPT_FINAL_LP_LS_Q_K_FIGURES",
    "DO_NOT_USE_FINAL_LP_LS_Q_K_FIGURES",
    "Lp/Ls/Qp/Qs/K figures must remain diagnostic or blocked",
    "ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md",
)
EMX_FIRST_GATE_REQUIRED_FRAGMENTS = (
    "basic numeric physics sanity",
    "not a golden-reference acceptance",
    "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE",
    "ACCEPT_AS_GOLDEN_EMX_REFERENCE",
    "ADS photo anchor",
    "final ADS sweep coverage",
    "ADS no-extrapolation plot grid",
    "physical metric window",
    "smooth transformer metric window",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "max_shape_relative_step",
    "approved port-pair photo alignment",
)
TARGET_EMX_IMPORT_VERIFIER_REQUIRED_FRAGMENTS = (
    "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
    "VALIDATION_EVIDENCE_TRANSFERRED_NO_LOCAL_EMX",
    "DO_NOT_IMPORT_TARGET_EMX_REFERENCE",
    "accepted_emx_reference_bundle",
    "_accepted_emx_reference_bundle",
    "READY_FOR_HFSS",
    "downstream_required_decision",
    "accepted_emx_reference_bundle is the only structured EMX artifact bundle",
    "MARS-recorded EMX S4P SHA",
    "invalid SHA256 digest",
    "required non-empty files present",
    "emx_first_validation_gate_port_pair_sensitivity.csv",
    "emx_first_validation_gate_core_metrics.png",
    "emx_first_validation_gate_port_pair_sensitivity.png",
    "post-run validation artifact content",
    "_artifact_content_checks",
    "_csv_numeric_column_failures",
    "_metrics_csv_frequency_grid_failures",
    "_csv_frequency_grid_failures",
    "finite numeric metric columns",
    "non-finite numeric value",
    "nonnumeric value",
    "metrics CSVs have 5-50 GHz / 0.1 GHz / 451-point grids",
    "frequency points expected",
    "MIN_VALIDATION_PNG_WIDTH",
    "MIN_VALIDATION_PNG_BYTES",
    "MIN_VALIDATION_PNG_COLOR_DELTA",
    "nontrivial plot images",
    "_png_image_content_failures",
    "blank or nearly constant PNG",
    "PNG dimensions {width}x{height} below minimum",
    "PNG bytes {len(data)} below minimum",
    "missing PNG IHDR chunk",
    "missing columns",
    "_required_emx_first_check_statuses",
    "EMX-first gate required physics/photo/port checks",
    "ADS no-extrapolation plot grid",
    "ADS photo anchor",
    "approved port-pair photo alignment",
    "any port-pair photo alignment",
    "--expected-approved-port-pairs",
    "--expected-port-pair-count",
    "--expected-port-pair-metric-count",
    "--expected-photo-max-percent-error",
    "_port_pair_sensitivity_csv_gate_checks",
    "_port_pair_sensitivity_csv_failures",
    "_resolve_port_pair_csv_path",
    "EMX-first port-pair sensitivity CSV gate",
    "ordered four-port pairings checked",
    "approved pair {args.expected_approved_port_pairs} PASS",
    "_safe_extractall_compat",
    "Python < 3.12 has no extraction filter argument",
    "--require-emx-s4p",
    "Touchstone physical gate required physics checks",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "_emx_first_curve_gate_argument_checks",
    "EMX-first gate curve-window arguments",
    "physical/smooth windows and K/shape thresholds recorded as expected",
    "EMX-first gate internal checks",
)
TOUCHSTONE_TRANSFORMER_AUDIT_REQUIRED_FRAGMENTS = (
    "--expected-source-kind",
    "--expected-frequency-step-ghz",
    "--expected-frequency-points",
    "per-step grid expected=",
    "bad_step_count",
    "max_expected_step_error_hz",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "ADS-equivalent metric finiteness",
    "target-frequency transformer metrics",
    "positive metric window",
    "smooth transformer metric window",
    "touchstone_ads_equivalent_metrics.png",
)
EMX_RETURN_WATCHER_REQUIRED_FRAGMENTS = (
    "discover_and_verify_mars_emx_return.py",
    "mars_emx_return_watch_summary.json",
    "mars_emx_return_watch_history.csv",
    "mars_emx_return_watch_history.jsonl",
    "WAITING_FOR_MARS_RETURN",
    "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
    "run_accepted_emx_hfss_ads_validation.py next",
    "--max-iterations",
    "--no-fail-exit",
    "tarball_candidate_count",
    "s4p_candidate_count",
    "--expected-sample-id",
    "expected_sample_id",
    "sample_status",
    "_sample_id_failures",
    "expected sample id",
    "verifier_decision",
    "accepted_emx_reference",
    "hfss_comparison_allowed",
    "NOT_ACCEPTED_EMX_REFERENCE",
    "WAIT_FOR_AND_IMPORT_MARS_WIDEBAND_EMX_RETURN",
)
TARGET_ENVELOPE_QUALITY_SCRIPT_REQUIRED_FRAGMENTS = {
    "run_dataset_quality_gates.py": (
        "--zin-target-envelope-config",
        "--zin-target-real-min-ohm",
        "--zin-min-target-envelope-area-frac",
        "--response-target-envelope-config",
        "--response-target-k-min",
        "--response-min-target-k-qp-area-frac",
        "--response-target-lp-min-nh",
        "--response-min-target-lp-ls-area-frac",
    ),
    "audit_zin_coverage.py": (
        "--target-envelope-config",
        "target_envelope_config",
        "--target-real-min-ohm",
        "--min-target-envelope-area-frac",
        "zin_target_envelope_bins.csv",
        "target_envelope_summary",
    ),
    "audit_response_feature_coverage.py": (
        "--target-envelope-config",
        "target_envelope_config",
        "--target-k-min",
        "--min-target-k-qp-area-frac",
        "response_target_k_qp_bins.csv",
        "--target-lp-min-nh",
        "--min-target-lp-ls-area-frac",
        "response_target_lp_ls_bins.csv",
        "target_envelopes",
    ),
}


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name, "detail": self.detail}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package_dir = Path(args.package_dir).expanduser().resolve()
    zip_path = Path(args.zip_path).expanduser().resolve()
    zip_sha_record = Path(args.zip_sha_record).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    checks.extend(_package_sha_checks(package_dir))
    checks.append(_package_python_cache_check(package_dir))
    checks.extend(_zip_checks(zip_path, zip_sha_record))
    checks.extend(
        _report_manifest_checks(
            package_dir,
            min_assets=args.min_report_assets,
            min_cards=args.min_status_cards,
            min_source_summaries=args.min_source_summaries,
        )
    )
    checks.append(
        _package_selfcheck_compare_gate(
            package_dir,
            expected_start_ghz=args.selfcheck_compare_start_ghz,
            expected_stop_ghz=args.selfcheck_compare_stop_ghz,
            expected_points=args.selfcheck_compare_points,
            max_percent_error=args.selfcheck_compare_max_percent_error,
        )
    )
    checks.append(_emx_first_gate_package_check(package_dir))
    checks.append(_ads_metric_formula_consistency_package_check(package_dir))
    checks.extend(_validation_script_checks(package_dir, min_scripts=args.min_validation_scripts))
    checks.extend(_handoff_checks(package_dir))
    checks.extend(_acceptance_matrix_checks(package_dir))

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "zip_sha_record": str(zip_sha_record),
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This audit verifies local delivery artifacts only.",
            "A PASS does not mean MARS final-500, wideband 500, 248k production, HFSS, ADS, or EMX runs completed.",
            "Acceptance matrix INCOMPLETE is expected until external simulator evidence exists.",
        ],
    }
    summary_path = out_dir / "delivery_package_audit_summary.json"
    report_path = out_dir / "delivery_package_audit_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    default_project = Path("/home/researcher/Documents/模拟变压器AI反向建模")
    default_package = Path("/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(default_package))
    parser.add_argument("--zip-path", default=str(default_package.with_suffix(".zip")))
    parser.add_argument(
        "--zip-sha-record",
        default=str(
            default_project
            / "hfss_validation"
            / "final500_ec6698dfc575950b"
            / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=str(
            default_project
            / "hfss_validation"
            / "final500_ec6698dfc575950b"
            / "delivery_package_audit_20260613"
        ),
    )
    parser.add_argument("--min-report-assets", type=int, default=27)
    parser.add_argument("--min-status-cards", type=int, default=28)
    parser.add_argument("--min-source-summaries", type=int, default=21)
    parser.add_argument("--min-validation-scripts", type=int, default=34)
    parser.add_argument("--selfcheck-compare-start-ghz", type=float, default=13.5)
    parser.add_argument("--selfcheck-compare-stop-ghz", type=float, default=16.5)
    parser.add_argument("--selfcheck-compare-points", type=int, default=9)
    parser.add_argument("--selfcheck-compare-max-percent-error", type=float, default=5.0)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _package_sha_checks(package_dir: Path) -> list[Check]:
    checks: list[Check] = []
    if not package_dir.is_dir():
        return [Check("FAIL", "package directory", f"missing: {package_dir}")]
    checks.append(Check("PASS", "package directory", str(package_dir)))

    sha_path = package_dir / "SHA256SUMS.txt"
    if not sha_path.exists():
        return checks + [Check("FAIL", "package SHA manifest", "SHA256SUMS.txt is missing")]
    entries = _read_sha_manifest(sha_path)
    listed_paths = {rel for _, rel in entries}
    actual_paths = {
        str(path.relative_to(package_dir))
        for path in package_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    missing_from_manifest = sorted(actual_paths - listed_paths)
    stale_entries = sorted(listed_paths - actual_paths)
    mismatches: list[str] = []
    for expected, rel_path in entries:
        file_path = package_dir / rel_path
        if file_path.exists() and _sha256(file_path) != expected:
            mismatches.append(rel_path)
    if missing_from_manifest or stale_entries or mismatches:
        detail = (
            f"entries={len(entries)}, missing_from_manifest={missing_from_manifest[:5]}, "
            f"stale_entries={stale_entries[:5]}, mismatches={mismatches[:5]}"
        )
        checks.append(Check("FAIL", "package SHA manifest", detail))
    else:
        checks.append(Check("PASS", "package SHA manifest", f"{len(entries)} files covered and matched"))
    return checks


def _zip_checks(zip_path: Path, zip_sha_record: Path) -> list[Check]:
    checks: list[Check] = []
    if not zip_path.exists():
        return [Check("FAIL", "desktop zip", f"missing: {zip_path}")]
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad = archive.testzip()
            names = archive.namelist()
    except Exception as exc:  # noqa: BLE001 - exact archive issue belongs in report.
        checks.append(Check("FAIL", "desktop zip integrity", f"{type(exc).__name__}: {exc}"))
    else:
        if bad:
            checks.append(Check("FAIL", "desktop zip integrity", f"bad member: {bad}"))
        else:
            checks.append(Check("PASS", "desktop zip integrity", f"archive OK: {zip_path.name}"))
        checks.append(_zip_clean_metadata_check(names))
        checks.append(_zip_python_cache_check(names))

    if not zip_sha_record.exists():
        checks.append(Check("FAIL", "desktop zip external SHA", f"missing: {zip_sha_record}"))
        return checks
    expected = zip_sha_record.read_text(encoding="utf-8").split()[0]
    actual = _sha256(zip_path)
    if actual == expected:
        checks.append(Check("PASS", "desktop zip external SHA", actual))
    else:
        checks.append(Check("FAIL", "desktop zip external SHA", f"expected={expected}, actual={actual}"))
    return checks


def _zip_clean_metadata_check(names: list[str]) -> Check:
    metadata_names: list[str] = []
    unsafe_names: list[str] = []
    for name in names:
        parts = [part for part in name.split("/") if part]
        if name.startswith("/") or ".." in parts:
            unsafe_names.append(name)
            continue
        if parts and parts[0] == "__MACOSX":
            metadata_names.append(name)
        elif any(part == ".DS_Store" or part.startswith("._") for part in parts):
            metadata_names.append(name)
    if unsafe_names:
        return Check("FAIL", "desktop zip clean metadata", f"unsafe member paths={unsafe_names[:8]}")
    if metadata_names:
        return Check("FAIL", "desktop zip clean metadata", f"macOS metadata members={metadata_names[:8]}")
    return Check("PASS", "desktop zip clean metadata", "no __MACOSX, .DS_Store, AppleDouble, absolute, or parent paths")


def _package_python_cache_check(package_dir: Path) -> Check:
    if not package_dir.is_dir():
        return Check("FAIL", "package bytecode/cache hygiene", f"missing: {package_dir}")
    offenders = sorted(
        str(path.relative_to(package_dir))
        for path in package_dir.rglob("*")
        if _is_python_cache_path(path.relative_to(package_dir))
    )
    if offenders:
        return Check("FAIL", "package bytecode/cache hygiene", f"remove generated cache files={offenders[:8]}")
    return Check("PASS", "package bytecode/cache hygiene", "no __pycache__, .pyc, or .pyo files in package directory")


def _zip_python_cache_check(names: list[str]) -> Check:
    offenders = sorted(name for name in names if _is_python_cache_name(name))
    if offenders:
        return Check("FAIL", "desktop zip bytecode/cache hygiene", f"generated cache members={offenders[:8]}")
    return Check("PASS", "desktop zip bytecode/cache hygiene", "no __pycache__, .pyc, or .pyo members")


def _report_manifest_checks(
    package_dir: Path,
    *,
    min_assets: int,
    min_cards: int,
    min_source_summaries: int,
) -> list[Check]:
    manifest_path = package_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
    if not manifest_path.exists():
        return [Check("FAIL", "report manifest", f"missing: {manifest_path}")]
    manifest = _read_json(manifest_path)
    checks: list[Check] = []
    asset_count = int(manifest.get("asset_count", 0) or 0)
    card_count = int(manifest.get("card_count", 0) or 0)
    source_summary_count = int(manifest.get("source_summary_count", 0) or 0)
    count_failures = []
    if asset_count < min_assets:
        count_failures.append(f"asset_count={asset_count} < {min_assets}")
    if card_count < min_cards:
        count_failures.append(f"card_count={card_count} < {min_cards}")
    if source_summary_count < min_source_summaries:
        count_failures.append(f"source_summary_count={source_summary_count} < {min_source_summaries}")
    if count_failures:
        checks.append(Check("FAIL", "report manifest counts", "; ".join(count_failures)))
    else:
        checks.append(
            Check(
                "PASS",
                "report manifest counts",
                f"assets={asset_count}, cards={card_count}, source_summaries={source_summary_count}",
            )
        )

    asset_failures: list[str] = []
    for asset in manifest.get("assets", []):
        if asset.get("status") != "OK":
            asset_failures.append(f"{asset.get('title')}: status={asset.get('status')}")
            continue
        rel_file = str(asset.get("file", ""))
        expected_sha = str(asset.get("sha256", ""))
        file_path = manifest_path.parent / rel_file
        if not file_path.exists():
            asset_failures.append(f"{rel_file}: missing")
        elif expected_sha and _sha256(file_path) != expected_sha:
            asset_failures.append(f"{rel_file}: sha mismatch")
    if asset_failures:
        checks.append(Check("FAIL", "report assets", "; ".join(asset_failures[:8])))
    else:
        checks.append(Check("PASS", "report assets", f"{asset_count} assets exist and match manifest SHA"))
    checks.append(_report_asset_usage_contract_check(manifest))
    checks.append(_report_local_health_pytest_gate_check(manifest))
    checks.append(_report_html_reference_check(manifest_path, manifest))
    checks.append(_report_image_quality_check(manifest_path, manifest))
    return checks


def _report_asset_usage_contract_check(manifest: dict[str, Any]) -> Check:
    allowed = {
        "ACCEPTED_FOR_CURRENT_CLAIM",
        "DIAGNOSTIC_ONLY",
        "BLOCKED_AS_FINAL_EVIDENCE",
        "MISSING",
    }
    final_compare_fragments = ("hfss_vs_emx", "package_selfcheck", "emx_first_gate")
    hfss_standalone_fragments = ("hfss_touchstone", "hfss_wideband_ads_formula")
    validation_chain_accepted = _report_validation_chain_accepted(manifest)
    assets = manifest.get("assets", [])
    failures: list[str] = []
    counts: dict[str, int] = {}
    blocked_final_count = 0
    diagnostic_hfss_count = 0
    for asset in assets:
        title = str(asset.get("title", "untitled"))
        evidence_use = str(asset.get("evidence_use", ""))
        rel_file = str(asset.get("file", "")).lower()
        usage_note = str(asset.get("usage_note", "")).strip()
        if evidence_use not in allowed:
            failures.append(f"{title}: evidence_use={evidence_use or 'missing'}")
            continue
        if not usage_note:
            failures.append(f"{title}: usage_note missing")
        counts[evidence_use] = counts.get(evidence_use, 0) + 1
        if any(fragment in rel_file for fragment in final_compare_fragments):
            if validation_chain_accepted:
                continue
            if evidence_use == "BLOCKED_AS_FINAL_EVIDENCE":
                blocked_final_count += 1
            else:
                failures.append(f"{title}: final comparison asset must stay BLOCKED_AS_FINAL_EVIDENCE while EMX/HFSS/ADS chain is not accepted")
        if any(fragment in rel_file for fragment in hfss_standalone_fragments):
            if evidence_use == "DIAGNOSTIC_ONLY":
                diagnostic_hfss_count += 1
            else:
                failures.append(f"{title}: standalone HFSS physical plot must stay DIAGNOSTIC_ONLY")

    manifest_counts = manifest.get("asset_usage_counts")
    if isinstance(manifest_counts, dict):
        normalized = {str(key): int(value) for key, value in manifest_counts.items()}
        if normalized != counts:
            failures.append(f"asset_usage_counts mismatch manifest={normalized} actual={counts}")
    else:
        failures.append("asset_usage_counts missing")

    if failures:
        return Check("FAIL", "report asset usage contract", "; ".join(failures[:8]))
    return Check(
        "PASS",
        "report asset usage contract",
        (
            f"usage_counts={dict(sorted(counts.items()))}; "
            f"blocked_final_comparison_assets={blocked_final_count}; "
            f"diagnostic_hfss_standalone_assets={diagnostic_hfss_count}; "
            f"validation_chain_accepted={validation_chain_accepted}"
        ),
    )


def _report_validation_chain_accepted(manifest: dict[str, Any]) -> bool:
    for card in manifest.get("cards", []):
        if not isinstance(card, dict):
            continue
        if str(card.get("name")) != "EMX/HFSS/ADS validation-chain decision":
            continue
        return (
            str(card.get("status")) == "PASS"
            and "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN" in str(card.get("detail", ""))
        )
    return False


def _report_local_health_pytest_gate_check(manifest: dict[str, Any]) -> Check:
    for card in manifest.get("cards", []):
        if not isinstance(card, dict):
            continue
        if str(card.get("name")) != "Local project health check":
            continue
        status = str(card.get("status"))
        detail = str(card.get("detail", ""))
        failures: list[str] = []
        if status != "PASS":
            failures.append(f"status={status}")
        required_fragments = (
            "Full pytest gate:",
            "passed",
            "skipped",
            "optional extras are represented as pytest skips",
            "does not run MARS/HFSS/ADS/EMX",
        )
        missing = [fragment for fragment in required_fragments if fragment not in detail]
        if missing:
            failures.append(f"missing={missing}")
        if failures:
            return Check("FAIL", "report local health pytest gate", "; ".join(failures))
        return Check("PASS", "report local health pytest gate", detail)
    return Check("FAIL", "report local health pytest gate", "Local project health check card missing")


def _report_html_reference_check(manifest_path: Path, manifest: dict[str, Any]) -> Check:
    html_path = manifest_path.parent / "index.html"
    if not html_path.exists():
        return Check("FAIL", "report html image references", f"missing: {html_path}")
    html_text = html_path.read_text(encoding="utf-8")
    image_refs = set(re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', html_text))
    manifest_images = {
        str(asset.get("file", ""))
        for asset in manifest.get("assets", [])
        if asset.get("status") == "OK" and str(asset.get("file", "")).lower().endswith((".png", ".jpg", ".jpeg"))
    }
    missing_in_html = sorted(manifest_images - image_refs)
    stale_html_refs = sorted(
        ref
        for ref in image_refs
        if ref.startswith("assets/")
        and not (manifest_path.parent / ref).exists()
    )
    required_selfcheck = {
        "assets/23_package_selfcheck_k_comparison.png",
        "assets/24_package_selfcheck_qp_comparison.png",
        "assets/25_package_selfcheck_qs_comparison.png",
        "assets/26_package_selfcheck_lp_comparison.png",
        "assets/27_package_selfcheck_ls_comparison.png",
    }
    required_emx_first = {
        "assets/30a_emx_first_gate_core_metrics.png",
    }
    missing_selfcheck = sorted(required_selfcheck - image_refs)
    missing_emx_first = sorted(required_emx_first - image_refs)
    failures = []
    if missing_in_html:
        failures.append(f"manifest images not referenced={missing_in_html[:8]}")
    if stale_html_refs:
        failures.append(f"HTML image refs missing on disk={stale_html_refs[:8]}")
    if missing_selfcheck:
        failures.append(f"selfcheck plots not referenced={missing_selfcheck}")
    if missing_emx_first:
        failures.append(f"EMX-first core metric plots not referenced={missing_emx_first}")
    if failures:
        return Check("FAIL", "report html image references", "; ".join(failures))
    return Check(
        "PASS",
        "report html image references",
        f"{len(image_refs)} image refs resolve and include package selfcheck plus EMX-first core metric plots",
    )


def _report_image_quality_check(manifest_path: Path, manifest: dict[str, Any]) -> Check:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return Check("FAIL", "report image nonblank", f"Pillow unavailable: {type(exc).__name__}: {exc}")

    image_assets = [
        asset
        for asset in manifest.get("assets", [])
        if asset.get("status") == "OK" and str(asset.get("file", "")).lower().endswith((".png", ".jpg", ".jpeg"))
    ]
    if not image_assets:
        return Check("FAIL", "report image nonblank", "no OK image assets found in report manifest")

    failures: list[str] = []
    for asset in image_assets:
        rel_file = str(asset.get("file", ""))
        file_path = manifest_path.parent / rel_file
        try:
            with Image.open(file_path) as image:
                rgb = image.convert("RGB")
                width, height = rgb.size
                extrema = ImageStat.Stat(rgb).extrema
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{rel_file}: {type(exc).__name__}: {exc}")
            continue
        max_delta = max((hi - lo) for lo, hi in extrema)
        if width < 8 or height < 8:
            failures.append(f"{rel_file}: too small {width}x{height}")
        elif max_delta <= 1:
            failures.append(f"{rel_file}: blank or nearly constant image")

    if failures:
        return Check("FAIL", "report image nonblank", "; ".join(failures[:8]))
    return Check("PASS", "report image nonblank", f"{len(image_assets)} images open, have usable dimensions, and are nonblank")


def _package_selfcheck_compare_gate(
    package_dir: Path,
    *,
    expected_start_ghz: float,
    expected_stop_ghz: float,
    expected_points: int,
    max_percent_error: float,
) -> Check:
    summary_path = package_dir / "package_selfcheck_compare_window_20260613" / "emx_hfss_ads_comparison_summary.json"
    wrapper_path = package_dir / "package_selfcheck_compare_window_20260613" / "package_selfcheck_compare_run_summary.json"
    if not summary_path.exists():
        return Check("FAIL", "package selfcheck compare gate", f"missing: {summary_path}")
    summary = _read_json(summary_path)
    wrapper = _read_json(wrapper_path) if wrapper_path.exists() else {}
    failures: list[str] = []
    if not wrapper:
        failures.append(f"missing wrapper: {wrapper_path}")
    elif wrapper.get("scope") != "NARROWBAND_PACKAGE_SELF_CONSISTENCY_ONLY":
        failures.append(f"wrapper_scope={wrapper.get('scope')!r}")
    elif wrapper.get("decision") != "NOT_A_GOLDEN_EMX_REFERENCE_GATE":
        failures.append(f"wrapper_decision={wrapper.get('decision')!r}")
    elif wrapper.get("evidence_use") != "NOT_FINAL_LP_LS_Q_K_EVIDENCE":
        failures.append(f"wrapper_evidence_use={wrapper.get('evidence_use')!r}")
    if summary.get("overall_status") != "PASS":
        failures.append(f"overall_status={summary.get('overall_status')}")
    window = summary.get("frequency_window_hz", {})
    start_hz = float(expected_start_ghz) * 1.0e9
    stop_hz = float(expected_stop_ghz) * 1.0e9
    if abs(float(window.get("min", float("nan"))) - start_hz) > 1.0e5:
        failures.append(f"window_min={window.get('min')}")
    if abs(float(window.get("max", float("nan"))) - stop_hz) > 1.0e5:
        failures.append(f"window_max={window.get('max')}")
    if int(window.get("count", 0) or 0) != int(expected_points):
        failures.append(f"window_count={window.get('count')}")
    grid_checks = summary.get("frequency_grid_checks", {})
    required_grid_checks = (
        "comparison point count",
        "expected frequency points",
        "expected frequency step",
        "expected window start point",
        "expected window stop point",
    )
    for check_name in required_grid_checks:
        item = grid_checks.get(check_name, {})
        if item.get("status") != "PASS":
            failures.append(f"{check_name}={item.get('status')}")
    metrics = summary.get("metrics", {})
    required_metrics = ("k", "qp", "qs", "lp_nh", "ls_nh")
    for metric in required_metrics:
        item = metrics.get(metric, {})
        if item.get("status") != "PASS":
            failures.append(f"{metric}_status={item.get('status')}")
        try:
            metric_error = float(item.get("max_percent_error"))
        except (TypeError, ValueError):
            failures.append(f"{metric}_max_percent_error={item.get('max_percent_error')}")
            continue
        if metric_error > max_percent_error:
            failures.append(f"{metric}_max_percent_error={metric_error:.6g}%")
    if failures:
        return Check("FAIL", "package selfcheck compare gate", "; ".join(failures[:8]))
    max_errors = {
        metric: float(metrics[metric]["max_percent_error"])
        for metric in required_metrics
    }
    worst_metric, worst_error = max(max_errors.items(), key=lambda item: item[1])
    return Check(
        "PASS",
        "package selfcheck compare gate",
        (
            f"{expected_start_ghz:g}-{expected_stop_ghz:g} GHz/{expected_points} points, "
            f"grid checks PASS, K/Q/L <= {max_percent_error:g}% max error; "
            f"wrapper_evidence_use=NOT_FINAL_LP_LS_Q_K_EVIDENCE; worst={worst_metric}:{worst_error:.4g}%"
        ),
        )


def _emx_first_gate_package_check(package_dir: Path) -> Check:
    summary_path = package_dir / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"
    if not summary_path.exists():
        return Check("FAIL", "EMX-first package gate evidence", f"missing: {summary_path}")
    summary = _read_json(summary_path)
    check_names = {str(item.get("name", "")) for item in summary.get("checks", [])}
    failures: list[str] = []
    if "basic numeric physics sanity" not in check_names:
        failures.append("missing basic numeric physics sanity check")
    if "physical metric window" not in check_names:
        failures.append("missing physical metric window check")
    if "smooth transformer metric window" not in check_names:
        failures.append("missing smooth transformer metric window check")
    if "ADS no-extrapolation plot grid" not in check_names:
        failures.append("missing ADS no-extrapolation plot grid check")
    if "target transformer sanity" in check_names:
        failures.append("stale target transformer sanity check name present")
    notes_text = "\n".join(str(item) for item in summary.get("method_notes", []))
    if "not a golden-reference acceptance" not in notes_text:
        failures.append("missing basic-sanity method boundary")
    decision = str(summary.get("decision", ""))
    if decision not in {"ACCEPT_AS_GOLDEN_EMX_REFERENCE", "DO_NOT_USE_AS_GOLDEN_EMX_REFERENCE"}:
        failures.append(f"unexpected decision={decision!r}")
    if failures:
        return Check("FAIL", "EMX-first package gate evidence", "; ".join(failures))
    return Check(
        "PASS",
        "EMX-first package gate evidence",
        f"decision={decision}, refreshed EMX-first physical-window and ADS no-extrapolation boundaries present",
    )


def _ads_metric_formula_consistency_package_check(package_dir: Path) -> Check:
    evidence_dir = package_dir / "ads_metric_formula_consistency_20260614"
    summary_path = evidence_dir / "ads_metric_formula_consistency_summary.json"
    report_path = evidence_dir / "ads_metric_formula_consistency_report.md"
    plot_path = evidence_dir / "ads_metric_formula_consistency_curves.png"
    template_path = evidence_dir / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"
    missing = [str(path.name) for path in (summary_path, report_path, plot_path, template_path) if not path.is_file()]
    if missing:
        return Check("FAIL", "ADS metric formula consistency evidence", f"missing={missing}")
    summary = _read_json(summary_path)
    freq = summary.get("frequency_ghz", {})
    failures: list[str] = []
    if summary.get("overall_status") != "PASS":
        failures.append(f"overall_status={summary.get('overall_status')}")
    if summary.get("decision") != "ADS_FORMULA_IMPLEMENTATION_ACCEPTED":
        failures.append(f"decision={summary.get('decision')}")
    required_checks = (
        "helper formula equals direct ADS expression",
        "known transformer metric recovery",
        "formula audit frequency grid",
        "ADS Data Display equation template",
    )
    by_name = {str(item.get("name")): item for item in summary.get("checks", [])}
    for name in required_checks:
        if by_name.get(name, {}).get("status") != "PASS":
            failures.append(f"{name}={by_name.get(name, {}).get('status')}")
    if float(freq.get("start", float("nan"))) != 5.0:
        failures.append(f"freq_start={freq.get('start')}")
    if float(freq.get("stop", float("nan"))) != 50.0:
        failures.append(f"freq_stop={freq.get('stop')}")
    if float(freq.get("step", float("nan"))) != 0.1:
        failures.append(f"freq_step={freq.get('step')}")
    if int(freq.get("points", 0) or 0) != 451:
        failures.append(f"freq_points={freq.get('points')}")
    failures.extend(_ads_data_display_template_failures(template_path, summary))
    worst_metric, worst_error = _worst_formula_percent_error(summary.get("metric_recovery_errors", {}))
    if worst_error > 1.0e-6:
        failures.append(f"worst_recovery={worst_metric}:{worst_error:.6g}%")
    failures.extend(_standalone_png_quality_failures(plot_path))
    if failures:
        return Check("FAIL", "ADS metric formula consistency evidence", "; ".join(failures[:8]))
    return Check(
        "PASS",
        "ADS metric formula consistency evidence",
        f"formula audit PASS on 5-50 GHz / 0.1 GHz / 451 points; worst_recovery={worst_metric}:{worst_error:.4g}%; ADS template, plot are decodable and nonblank",
    )


def _ads_data_display_template_failures(path: Path, summary: dict[str, Any]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    required = (
        "ADS Data Display equation template",
        "Touchstone reference impedance",
        f"port pairs {summary.get('port_pairs', '1,2:3,4')}",
        "Zp = Z11 - Z12 + Z22 - Z21",
        "Zs = Z33 - Z34 + Z44 - Z43",
        "Zm = Z31 - Z32 + Z42 - Z41",
        "Lp = imag(Zp) / omega",
        "Ls = imag(Zs) / omega",
        "M  = imag(Zm) / omega",
        "K  = M / sqrt(Lp*Ls)",
        "Qp = imag(Zp) / real(Zp)",
        "Qs = imag(Zs) / real(Zs)",
        "target_marker_ghz = 15",
        "5-50 GHz / 0.1 GHz / 451 points",
        "no ADS extrapolation",
    )
    missing = [fragment for fragment in required if fragment not in text]
    return [f"ADS template missing fragments={missing[:4]}"] if missing else []


def _validation_script_checks(package_dir: Path, *, min_scripts: int) -> list[Check]:
    script_dir = package_dir / "validation_scripts"
    if not script_dir.is_dir():
        return [
            Check("FAIL", "validation scripts inventory", f"missing: {script_dir}"),
            Check("FAIL", "validation scripts syntax", "validation_scripts directory is missing"),
            Check("FAIL", "local health-check runner contract", "validation_scripts directory is missing"),
            Check("FAIL", "MARS dataset package helper contract", "validation_scripts directory is missing"),
            Check("FAIL", "MARS dataset package verifier contract", "validation_scripts directory is missing"),
            Check("FAIL", "HFSS/EMX batch compare runner contract", "validation_scripts directory is missing"),
            Check("FAIL", "accepted final figure verifier contract", "validation_scripts directory is missing"),
            Check("FAIL", "target EMX post-run import verifier contract", "validation_scripts directory is missing"),
            Check("FAIL", "Touchstone transformer audit contract", "validation_scripts directory is missing"),
            Check("FAIL", "target EMX return watcher contract", "validation_scripts directory is missing"),
            Check("FAIL", "248k launch readiness contract", "validation_scripts directory is missing"),
        ]

    scripts = sorted(path for path in script_dir.glob("*.py") if path.is_file())
    names = {path.name for path in scripts}
    missing = sorted(set(REQUIRED_VALIDATION_SCRIPTS) - names)
    inventory_failures: list[str] = []
    if len(scripts) < min_scripts:
        inventory_failures.append(f"script_count={len(scripts)} < {min_scripts}")
    if missing:
        inventory_failures.append(f"missing_required={missing[:8]}")
    if inventory_failures:
        inventory = Check("FAIL", "validation scripts inventory", "; ".join(inventory_failures))
    else:
        inventory = Check(
            "PASS",
            "validation scripts inventory",
            f"{len(scripts)} python scripts present; {len(REQUIRED_VALIDATION_SCRIPTS)} required helpers present",
        )

    syntax_failures: list[str] = []
    for script in scripts:
        try:
            source = script.read_text(encoding="utf-8")
            compile(source, str(script), "exec")
        except SyntaxError as exc:
            syntax_failures.append(f"{script.name}:{exc.lineno}:{exc.msg}")
        except Exception as exc:  # noqa: BLE001 - exact local file read issue belongs in audit.
            syntax_failures.append(f"{script.name}:{type(exc).__name__}:{exc}")
    if syntax_failures:
        syntax = Check("FAIL", "validation scripts syntax", "; ".join(syntax_failures[:8]))
    else:
        syntax = Check("PASS", "validation scripts syntax", f"{len(scripts)} scripts compile without syntax errors")
    return [
        inventory,
        syntax,
        _local_health_check_runner_contract_check(
            script_dir / "run_local_project_health_check.py",
            "local health-check runner contract",
        ),
        _dataset_package_helper_contract_check(script_dir),
        _dataset_package_verifier_contract_check(script_dir),
        _batch_compare_runner_contract_check(
            script_dir / "run_hfss_emx_validation_batch.py",
            "HFSS/EMX batch compare runner contract",
        ),
        _accepted_emx_hfss_runner_contract_check(
            script_dir / "run_accepted_emx_hfss_ads_validation.py",
            "accepted EMX/HFSS final runner contract",
        ),
        _accepted_figure_verifier_contract_check(
            script_dir / "verify_accepted_emx_hfss_ads_figures.py",
            "accepted final figure verifier contract",
        ),
        _emx_first_gate_script_contract_check(
            script_dir / "build_emx_first_validation_gate.py",
            "EMX-first gate script contract",
        ),
        _target_emx_import_verifier_contract_check(
            script_dir / "verify_target_emx_postrun_package.py",
            "target EMX post-run import verifier contract",
        ),
        _touchstone_transformer_audit_contract_check(
            script_dir / "audit_touchstone_transformer.py",
            "Touchstone transformer audit contract",
        ),
        _emx_return_watcher_contract_check(
            script_dir / "watch_mars_emx_return.py",
            "target EMX return watcher contract",
        ),
        _target_envelope_quality_scripts_contract_check(
            script_dir,
            "target-envelope quality scripts contract",
        ),
        _production_readiness_contract_check(
            script_dir / "audit_248k_launch_readiness.py",
            "248k launch readiness contract",
        ),
    ]


def _local_health_check_runner_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    required_source_fragments = (
        "_run_mars_emx_return_watch",
        "watch_mars_emx_return.py",
        "MARS target EMX return watcher",
        "WAITING_FOR_MARS_RETURN",
        "watcher records local pull state only",
        "--max-iterations",
        "--no-fail-exit",
        "s4p_candidates",
        "tarball_candidates",
        "accepted_emx_reference",
        "NOT_ACCEPTED_EMX_REFERENCE",
        "not an accepted EMX reference",
    )
    missing_source = [fragment for fragment in required_source_fragments if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check(
        "PASS",
        name,
        f"{len(required_source_fragments)} health-check watcher fragments present",
    )


def _dataset_package_helper_contract_check(script_dir: Path) -> Check:
    script_path = script_dir / "package_mars_dataset_run.py"
    if not script_path.exists():
        return Check("FAIL", "MARS dataset package helper contract", f"missing: {script_path}")

    source = script_path.read_text(encoding="utf-8")
    required_source_fragments = (
        "--report",
        "category_counts",
        "# MARS Dataset Transfer Inventory",
        "_is_excluded_transfer_path",
        "bytecode/cache and platform metadata files are excluded",
        "mars_run_progress_watch",
        "progress_audit_summary_files",
        "final500_ground_clearance_audit.json",
        "clearance_audit_files",
        "_empty_transfer_files",
        "Refusing to package empty transfer files",
        "empty_file_count",
        "Empty transfer files are refused before tarball creation",
    )
    missing_source = [fragment for fragment in required_source_fragments if fragment not in source]

    command = [sys.executable or "python3", str(script_path), "--help"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return Check("FAIL", "MARS dataset package helper contract", f"{type(exc).__name__}: {exc}")
    help_text = f"{result.stdout}\n{result.stderr}"
    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"--help returncode={result.returncode}")
    if "--report" not in help_text:
        failures.append("--help missing --report")
    if missing_source:
        failures.append(f"source missing={missing_source}")
    if failures:
        return Check("FAIL", "MARS dataset package helper contract", "; ".join(failures[:5]))
    return Check("PASS", "MARS dataset package helper contract", "--report help, category_counts, Markdown report renderer, raw clearance audit packaging, metadata/cache exclude logic, empty-file refusal, and progress/watch evidence packaging present")


def _dataset_package_verifier_contract_check(script_dir: Path) -> Check:
    script_path = script_dir / "verify_mars_dataset_package.py"
    if not script_path.exists():
        return Check("FAIL", "MARS dataset package verifier contract", f"missing: {script_path}")

    source = script_path.read_text(encoding="utf-8")
    required_source_fragments = (
        "--require-quality-gates",
        "--inventory-report",
        "packaged dataset quality gates",
        "--require-clearance-audit",
        "inventory clearance-audit evidence",
        "required clearance audit",
        "final500_ground_clearance_audit.json",
        "clearance_audit_files",
        "dataset_quality_gates*/dataset_quality_gates_summary.json",
        "inventory Markdown report",
        "inventory category counts",
        "inventory expected-count evidence",
        "inventory non-empty files",
        "_inventory_nonempty_files_check",
        "tar metadata/cache hygiene",
        "tar inventory exactness",
        "tar duplicate member hygiene",
        "--require-progress-evidence",
        "packaged run progress evidence",
        "--require-geometry-quality",
        "--internal-angle-deg",
        "--terminal-angle-deg",
        "_safe_extractall_compat",
        "Python < 3.12 has no extraction filter argument",
        "--require-emx-command",
        "--expected-port-mode",
        "--expected-pin-purpose",
        "_is_metadata_or_cache_member",
        "_tar_inventory_exactness_check",
        "_tar_duplicate_member_check",
    )
    missing_source = [fragment for fragment in required_source_fragments if fragment not in source]

    command = [sys.executable or "python3", str(script_path), "--help"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return Check("FAIL", "MARS dataset package verifier contract", f"{type(exc).__name__}: {exc}")
    help_text = f"{result.stdout}\n{result.stderr}"
    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"--help returncode={result.returncode}")
    if "--require-quality-gates" not in help_text:
        failures.append("--help missing --require-quality-gates")
    if "--require-progress-evidence" not in help_text:
        failures.append("--help missing --require-progress-evidence")
    if "--require-quality-figures" not in help_text:
        failures.append("--help missing --require-quality-figures")
    if "--require-clearance-audit" not in help_text:
        failures.append("--help missing --require-clearance-audit")
    if "--require-geometry-quality" not in help_text:
        failures.append("--help missing --require-geometry-quality")
    if "--internal-angle-deg" not in help_text:
        failures.append("--help missing --internal-angle-deg")
    if "--terminal-angle-deg" not in help_text:
        failures.append("--help missing --terminal-angle-deg")
    if "--inventory-report" not in help_text:
        failures.append("--help missing --inventory-report")
    if "--require-emx-command" not in help_text:
        failures.append("--help missing --require-emx-command")
    if "--expected-port-mode" not in help_text:
        failures.append("--help missing --expected-port-mode")
    if "--expected-pin-purpose" not in help_text:
        failures.append("--help missing --expected-pin-purpose")
    if missing_source:
        failures.append(f"source missing={missing_source}")
    if failures:
        return Check("FAIL", "MARS dataset package verifier contract", "; ".join(failures[:5]))
    return Check(
        "PASS",
        "MARS dataset package verifier contract",
        "--require-quality-gates/--require-progress-evidence/--require-quality-figures/--inventory-report help, raw-clearance/geometry-quality/EMX command contract pass-through, Markdown report check, progress evidence check, quality-gate/clearance-contract/figure/raw-clearance checks, inventory category-count check, non-empty inventory file check, clearance-audit inventory check, expected-count evidence check, tar metadata/cache hygiene check, tar inventory exactness check, duplicate-member check, and Python <3.12 extraction fallback present",
    )


def _batch_compare_runner_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [fragment for fragment in BATCH_COMPARE_REQUIRED_FRAGMENTS if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check("PASS", name, f"{len(BATCH_COMPARE_REQUIRED_FRAGMENTS)} batch runner fragments present")


def _accepted_emx_hfss_runner_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [
        fragment for fragment in ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS if fragment not in source
    ]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check(
        "PASS",
        name,
        f"{len(ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS)} accepted-EMX/HFSS runner fragments present",
    )


def _accepted_figure_verifier_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [
        fragment for fragment in ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS if fragment not in source
    ]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check(
        "PASS",
        name,
        f"{len(ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS)} accepted final-figure verifier fragments present",
    )


def _emx_first_gate_script_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source: list[str] = []
    if "target transformer sanity" in source:
        missing_source.append("stale target transformer sanity check name must be removed")
    missing_source.extend(fragment for fragment in EMX_FIRST_GATE_REQUIRED_FRAGMENTS if fragment not in source)
    if missing_source:
        return Check("FAIL", name, f"missing_or_stale={missing_source[:8]}")
    return Check("PASS", name, f"{len(EMX_FIRST_GATE_REQUIRED_FRAGMENTS)} EMX-first gate boundary fragments present")


def _target_emx_import_verifier_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [fragment for fragment in TARGET_EMX_IMPORT_VERIFIER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check("PASS", name, f"{len(TARGET_EMX_IMPORT_VERIFIER_REQUIRED_FRAGMENTS)} import verifier fragments present")


def _touchstone_transformer_audit_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [fragment for fragment in TOUCHSTONE_TRANSFORMER_AUDIT_REQUIRED_FRAGMENTS if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check("PASS", name, f"{len(TOUCHSTONE_TRANSFORMER_AUDIT_REQUIRED_FRAGMENTS)} Touchstone physical/step-grid fragments present")


def _emx_return_watcher_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [fragment for fragment in EMX_RETURN_WATCHER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check("PASS", name, f"{len(EMX_RETURN_WATCHER_REQUIRED_FRAGMENTS)} MARS returned-EMX watch fragments present")


def _target_envelope_quality_scripts_contract_check(script_dir: Path, name: str) -> Check:
    failures: list[str] = []
    total_fragments = 0
    for script_name, fragments in TARGET_ENVELOPE_QUALITY_SCRIPT_REQUIRED_FRAGMENTS.items():
        script_path = script_dir / script_name
        if not script_path.exists():
            failures.append(f"missing {script_name}")
            continue
        source = script_path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in source]
        total_fragments += len(fragments)
        if missing:
            failures.append(f"{script_name} missing {missing[:6]}")
    if failures:
        return Check("FAIL", name, "; ".join(failures[:4]))
    return Check(
        "PASS",
        name,
        f"{total_fragments} Zin/K-Qp/Lp-Ls target-envelope fragments present",
    )


def _production_readiness_contract_check(script_path: Path, name: str) -> Check:
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing_source = [fragment for fragment in PRODUCTION_READINESS_REQUIRED_FRAGMENTS if fragment not in source]
    if missing_source:
        return Check("FAIL", name, f"missing={missing_source[:8]}")
    return Check("PASS", name, f"{len(PRODUCTION_READINESS_REQUIRED_FRAGMENTS)} readiness fragments present")


def _handoff_checks(package_dir: Path) -> list[Check]:
    checks: list[Check] = []
    handoff_dir = package_dir / "mars_handoff_bundle_20260613"
    tar_path = package_dir / "mars_handoff_bundle_20260613.tar.gz"
    sha_record = package_dir / "mars_handoff_bundle_20260613.tar.gz.sha256"
    if not handoff_dir.is_dir():
        return [Check("FAIL", "MARS handoff staging", f"missing: {handoff_dir}")]
    checks.append(Check("PASS", "MARS handoff staging", str(handoff_dir)))

    checks.append(_handoff_sha_check(handoff_dir, "MARS handoff internal SHA"))
    checks.append(_handoff_portability_check(handoff_dir, "MARS handoff portable commands"))
    checks.append(_handoff_config_contract_check(handoff_dir, "MARS handoff config contract"))
    checks.append(_handoff_target_emx_command_contract_check(handoff_dir, "MARS handoff target EMX rerun command contract"))
    checks.append(_handoff_target_emx_postrun_command_contract_check(handoff_dir, "MARS handoff target EMX post-run validation command contract"))
    checks.append(_handoff_package_source_contract_check(handoff_dir, "MARS handoff package source contract"))
    checks.append(_handoff_path_discovery_contract_check(handoff_dir, "MARS handoff path discovery helper contract"))
    checks.append(
        _touchstone_transformer_audit_contract_check(
            handoff_dir / "scripts" / "audit_touchstone_transformer.py",
            "MARS handoff Touchstone transformer audit contract",
        )
    )
    checks.append(
        _emx_return_watcher_contract_check(
            handoff_dir / "scripts" / "watch_mars_emx_return.py",
            "MARS handoff target EMX return watcher contract",
        )
    )
    checks.append(_handoff_path_patcher_smoke_check(handoff_dir, "MARS handoff path patcher smoke"))
    checks.append(_handoff_progress_audit_contract_check(handoff_dir, "MARS handoff run-progress contract"))
    checks.append(_handoff_quality_gate_contract_check(handoff_dir, "MARS handoff quality-gate contract"))
    checks.append(_handoff_shape_gate_check(handoff_dir, "MARS handoff shape-window gate"))
    checks.append(_handoff_compare_gate_check(handoff_dir, "MARS handoff HFSS/EMX compare grid gate"))
    checks.append(
        _handoff_final_figure_verifier_runbook_contract_check(
            handoff_dir,
            "MARS handoff accepted final figure verifier runbook contract",
        )
    )
    checks.append(_handoff_validation_chain_contract_check(handoff_dir, "MARS handoff validation-chain contract"))
    checks.append(
        _batch_compare_runner_contract_check(
            handoff_dir / "scripts" / "run_hfss_emx_validation_batch.py",
            "MARS handoff HFSS/EMX batch runner contract",
        )
    )
    checks.append(
        _accepted_emx_hfss_runner_contract_check(
            handoff_dir / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
            "MARS handoff accepted EMX/HFSS final runner contract",
        )
    )
    checks.append(
        _accepted_figure_verifier_contract_check(
            handoff_dir / "scripts" / "verify_accepted_emx_hfss_ads_figures.py",
            "MARS handoff accepted final figure verifier contract",
        )
    )
    checks.append(
        _emx_first_gate_script_contract_check(
            handoff_dir / "scripts" / "build_emx_first_validation_gate.py",
            "MARS handoff EMX-first gate script contract",
        )
    )
    checks.append(
        _target_emx_import_verifier_contract_check(
            handoff_dir / "scripts" / "verify_target_emx_postrun_package.py",
            "MARS handoff target EMX post-run import verifier contract",
        )
    )
    checks.append(
        _target_envelope_quality_scripts_contract_check(
            handoff_dir / "scripts",
            "MARS handoff target-envelope quality scripts contract",
        )
    )
    checks.append(
        _production_readiness_contract_check(
            handoff_dir / "scripts" / "audit_248k_launch_readiness.py",
            "MARS handoff 248k launch readiness contract",
        )
    )
    checks.append(_handoff_command_syntax_check(handoff_dir, "MARS handoff command syntax"))

    if tar_path.exists() and sha_record.exists():
        expected = sha_record.read_text(encoding="utf-8").split()[0]
        actual = _sha256(tar_path)
        if expected == actual:
            checks.append(Check("PASS", "MARS handoff tar SHA", actual))
        else:
            checks.append(Check("FAIL", "MARS handoff tar SHA", f"expected={expected}, actual={actual}"))
        try:
            with tarfile.open(tar_path, "r:gz") as archive:
                names = set(archive.getnames())
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("FAIL", "MARS handoff tar contents", f"{type(exc).__name__}: {exc}"))
        else:
            required = {
                "mars_handoff_bundle_20260613/project_runbook/mars_dataset_500_wideband_20260613.commands.sh",
                "mars_handoff_bundle_20260613/project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh",
                "mars_handoff_bundle_20260613/project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_postrun_validation.commands.sh",
                "mars_handoff_bundle_20260613/configs/mars_dataset_500_wideband_20260613.yaml",
                "mars_handoff_bundle_20260613/scripts/run_dataset_quality_gates.py",
                "mars_handoff_bundle_20260613/scripts/prepare_target_emx_wideband_rerun.py",
                "mars_handoff_bundle_20260613/scripts/prepare_target_emx_postrun_validation.py",
                "mars_handoff_bundle_20260613/scripts/discover_and_verify_mars_emx_return.py",
                "mars_handoff_bundle_20260613/scripts/discover_mars_emx_cadence_paths.py",
                "mars_handoff_bundle_20260613/scripts/watch_mars_emx_return.py",
                "mars_handoff_bundle_20260613/scripts/run_hfss_emx_validation_batch.py",
                "mars_handoff_bundle_20260613/scripts/build_validation_chain_decision_card.py",
                "mars_handoff_bundle_20260613/scripts/run_accepted_emx_hfss_ads_validation.py",
                "mars_handoff_bundle_20260613/scripts/build_emx_first_validation_gate.py",
                "mars_handoff_bundle_20260613/scripts/verify_target_emx_postrun_package.py",
                "mars_handoff_bundle_20260613/scripts/audit_248k_launch_readiness.py",
                "mars_handoff_bundle_20260613/scripts/verify_mars_handoff_install.py",
                "mars_handoff_bundle_20260613/scripts/backfill_ground_clearance_audit.py",
                "mars_handoff_bundle_20260613/rfic_transformer_inverse_design/dataset.py",
                "mars_handoff_bundle_20260613/rfic_transformer_inverse_design/execution/evaluator.py",
                "mars_handoff_bundle_20260613/rfic_transformer_inverse_design/layout/export.py",
            }
            missing = sorted(required - names)
            checks.append(
                Check(
                    "PASS" if not missing else "FAIL",
                    "MARS handoff tar contents",
                    "portable config/commands, gate script, and clearance package source present" if not missing else f"missing={missing}",
                )
            )
            checks.extend(_extracted_handoff_checks(tar_path))
    else:
        checks.append(Check("FAIL", "MARS handoff tar SHA", "tar or sha record is missing"))
    return checks


def _extracted_handoff_checks(tar_path: Path) -> list[Check]:
    with tempfile.TemporaryDirectory(prefix="mars_handoff_audit_") as tmpdir:
        extract_dir = Path(tmpdir)
        try:
            with tarfile.open(tar_path, "r:gz") as archive:
                safe, reason = _safe_extractall(archive, extract_dir)
        except Exception as exc:  # noqa: BLE001
            return [Check("FAIL", "MARS handoff tar extract dry-run", f"{type(exc).__name__}: {exc}")]
        if not safe:
            return [Check("FAIL", "MARS handoff tar extract dry-run", reason)]

        extracted_handoff = extract_dir / "mars_handoff_bundle_20260613"
        if not extracted_handoff.is_dir():
            return [
                Check(
                    "FAIL",
                    "MARS handoff tar extract dry-run",
                    "archive did not extract mars_handoff_bundle_20260613 root",
                )
            ]

        return [
            Check("PASS", "MARS handoff tar extract dry-run", f"extracted cleanly to temporary directory from {tar_path.name}"),
            _handoff_sha_check(extracted_handoff, "MARS handoff extracted SHA"),
            _handoff_portability_check(extracted_handoff, "MARS handoff extracted portable commands"),
            _handoff_config_contract_check(extracted_handoff, "MARS handoff extracted config contract"),
            _handoff_target_emx_command_contract_check(
                extracted_handoff,
                "MARS handoff extracted target EMX rerun command contract",
            ),
            _handoff_target_emx_postrun_command_contract_check(
                extracted_handoff,
                "MARS handoff extracted target EMX post-run validation command contract",
            ),
            _handoff_package_source_contract_check(extracted_handoff, "MARS handoff extracted package source contract"),
            _handoff_path_discovery_contract_check(
                extracted_handoff,
                "MARS handoff extracted path discovery helper contract",
            ),
            _touchstone_transformer_audit_contract_check(
                extracted_handoff / "scripts" / "audit_touchstone_transformer.py",
                "MARS handoff extracted Touchstone transformer audit contract",
            ),
            _emx_return_watcher_contract_check(
                extracted_handoff / "scripts" / "watch_mars_emx_return.py",
                "MARS handoff extracted target EMX return watcher contract",
            ),
            _handoff_path_patcher_smoke_check(extracted_handoff, "MARS handoff extracted path patcher smoke"),
            _handoff_progress_audit_contract_check(extracted_handoff, "MARS handoff extracted run-progress contract"),
            _handoff_quality_gate_contract_check(extracted_handoff, "MARS handoff extracted quality-gate contract"),
            _handoff_shape_gate_check(extracted_handoff, "MARS handoff extracted shape-window gate"),
            _handoff_compare_gate_check(extracted_handoff, "MARS handoff extracted HFSS/EMX compare grid gate"),
            _handoff_final_figure_verifier_runbook_contract_check(
                extracted_handoff,
                "MARS handoff extracted accepted final figure verifier runbook contract",
            ),
            _handoff_validation_chain_contract_check(
                extracted_handoff,
                "MARS handoff extracted validation-chain contract",
            ),
            _batch_compare_runner_contract_check(
                extracted_handoff / "scripts" / "run_hfss_emx_validation_batch.py",
                "MARS handoff extracted HFSS/EMX batch runner contract",
            ),
            _accepted_emx_hfss_runner_contract_check(
                extracted_handoff / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
                "MARS handoff extracted accepted EMX/HFSS final runner contract",
            ),
            _accepted_figure_verifier_contract_check(
                extracted_handoff / "scripts" / "verify_accepted_emx_hfss_ads_figures.py",
                "MARS handoff extracted accepted final figure verifier contract",
            ),
            _emx_first_gate_script_contract_check(
                extracted_handoff / "scripts" / "build_emx_first_validation_gate.py",
                "MARS handoff extracted EMX-first gate script contract",
            ),
            _target_emx_import_verifier_contract_check(
                extracted_handoff / "scripts" / "verify_target_emx_postrun_package.py",
                "MARS handoff extracted target EMX post-run import verifier contract",
            ),
            _target_envelope_quality_scripts_contract_check(
                extracted_handoff / "scripts",
                "MARS handoff extracted target-envelope quality scripts contract",
            ),
            _production_readiness_contract_check(
                extracted_handoff / "scripts" / "audit_248k_launch_readiness.py",
                "MARS handoff extracted 248k launch readiness contract",
            ),
            _handoff_command_syntax_check(extracted_handoff, "MARS handoff extracted command syntax"),
        ]


def _safe_extractall(archive: tarfile.TarFile, destination: Path) -> tuple[bool, str]:
    for member in archive.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            return False, f"unsafe tar member path: {member.name}"
        if member.issym() or member.islnk():
            return False, f"tar member link is not allowed in delivery audit: {member.name}"
    _safe_extractall_compat(archive, destination)
    return True, "extracted"


def _safe_extractall_compat(archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        # Python < 3.12 has no extraction filter argument. Path/link safety is
        # already enforced by _safe_extractall before this fallback is reached.
        archive.extractall(destination)


def _handoff_sha_check(handoff_dir: Path, name: str) -> Check:
    sha_path = handoff_dir / "SHA256SUMS.txt"
    if not sha_path.exists():
        return Check("FAIL", name, "SHA256SUMS.txt is missing")
    entries = _read_sha_manifest(sha_path)
    mismatches = [
        rel
        for expected, rel in entries
        if not (handoff_dir / rel).exists() or _sha256(handoff_dir / rel) != expected
    ]
    if mismatches:
        return Check("FAIL", name, f"mismatches={mismatches[:8]}")
    return Check("PASS", name, f"{len(entries)} files matched")


def _handoff_portability_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    config_path = handoff_dir / "configs" / "mars_dataset_500_wideband_20260613.yaml"
    if not command_path.exists() or not config_path.exists():
        return Check("FAIL", name, "command or config file is missing")
    command_text = command_path.read_text(encoding="utf-8")
    portable = (
        "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}" in command_text
        and "scripts/audit_mars_run_progress.py" in command_text
        and "scripts/run_dataset_quality_gates.py" in command_text
        and "/home/researcher" not in command_text
    )
    if portable:
        return Check("PASS", name, "relative config path, run-progress audit, and post-run gate present")
    return Check("FAIL", name, "commands are missing relative config, run-progress audit, gate, or contain local paths")


def _handoff_config_contract_check(handoff_dir: Path, name: str) -> Check:
    config_path = handoff_dir / WIDEBAND_HANDOFF_CONFIG
    if not config_path.exists():
        return Check("FAIL", name, f"missing: {config_path}")
    try:
        data = _load_yaml_mapping(config_path)
    except Exception as exc:  # noqa: BLE001
        return Check("FAIL", name, f"{type(exc).__name__}: {exc}")
    if not isinstance(data, dict):
        return Check("FAIL", name, f"top-level YAML is {type(data).__name__}")
    target = data.get("target", {})
    emx = data.get("emx", {})
    shield = data.get("transformer", {}).get("shield", {})
    failures: list[str] = []
    _require_numeric(failures, target, "frequency_start_hz", 5.0e9)
    _require_numeric(failures, target, "frequency_stop_hz", 50.0e9)
    _require_numeric(failures, target, "frequency_step_hz", 1.0e8)
    _require_int(failures, target, "band_points", 451)
    _require_value(failures, emx, "port_mode", "single_ended_shield_grounded")
    _require_int(failures, emx, "cadence_pin_purpose", 51)
    _require_value(failures, shield, "enabled", True)
    _require_value(failures, shield, "kind", "ring")
    if "/home/researcher" in config_path.read_text(encoding="utf-8"):
        failures.append("config must not contain local macOS /home/researcher paths")
    if failures:
        return Check("FAIL", name, "; ".join(failures[:8]))
    return Check("PASS", name, "5-50 GHz, 0.1 GHz step, 451 points, grounded shield, pin purpose 51")


def _handoff_target_emx_command_contract_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / TARGET_EMX_HANDOFF_COMMAND
    if not command_path.exists():
        return Check("FAIL", name, f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required = (
        "/cae/apps/data/cadence-2025/installs/EMX20251/bin/emx",
        "TRANSFORMER_021_ec6698df",
        "--cadence-pins=51",
        "--port=P001=P001:P001_G",
        "--port=P002=P002:P002_G",
        "--port=P003=P003:P003_G",
        "--port=P004=P004:P004_G",
        "emx_wideband_5_50_0p1/emx.s4p",
        "5000000000",
        "50000000000",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if "/home/researcher" in text:
        missing.append("must not contain local macOS /home/researcher paths")
    missing.extend(_target_emx_command_frequency_grid_failures(text))
    if missing:
        return Check("FAIL", name, f"missing_or_bad={missing[:8]}")
    return Check(
        "PASS",
        name,
        "target EMX rerun command has MARS paths, pin 51, grounded ports, and exact 5-50 GHz / 0.1 GHz / 451-point frequency list",
    )


def _target_emx_command_frequency_grid_failures(command_text: str) -> list[str]:
    command_line = next(
        (
            line.strip()
            for line in command_text.splitlines()
            if "/bin/emx " in line and "emx_wideband_5_50_0p1/emx.s4p" in line
        ),
        "",
    )
    if not command_line:
        return ["target EMX command line not found"]
    try:
        tokens = shlex.split(command_line)
    except ValueError as exc:
        return [f"target EMX command is not shell-parseable: {exc}"]
    freqs_reversed: list[int] = []
    for token in reversed(tokens):
        if re.fullmatch(r"\d+", token):
            freqs_reversed.append(int(token))
        elif freqs_reversed:
            break
    freqs = list(reversed(freqs_reversed))
    expected = list(range(5_000_000_000, 50_000_000_000 + 100_000_000, 100_000_000))
    if freqs == expected:
        return []
    failures = [
        (
            "target EMX frequency list must be explicit 5-50 GHz / 0.1 GHz / 451 points; "
            f"got count={len(freqs)}"
        )
    ]
    if freqs:
        step = freqs[1] - freqs[0] if len(freqs) >= 2 else None
        failures.append(f"freq_start={freqs[0]}, freq_stop={freqs[-1]}, freq_step={step}")
    return failures


def _handoff_target_emx_postrun_command_contract_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / TARGET_EMX_POSTRUN_HANDOFF_COMMAND
    if not command_path.exists():
        return Check("FAIL", name, f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required = (
        'test -s "$EMX_S4P"',
    "scripts/audit_touchstone_transformer.py",
    "--expected-source-kind EMX",
    "--expected-frequency-start-ghz 5.0",
        "--expected-frequency-stop-ghz 50.0",
        "--expected-frequency-step-ghz 0.1",
        "--expected-frequency-points 451",
        "--positive-window-start-ghz 5.0",
        "--positive-window-stop-ghz 30.0",
        "--min-target-abs-k 0.05",
        "--min-window-abs-k 0.05",
        "--shape-window-start-ghz 5.0",
        "--shape-window-stop-ghz 30.0",
        "scripts/build_emx_first_validation_gate.py",
        "--required-sweep-start-ghz 5.0",
        "--required-sweep-stop-ghz 50.0",
        "--required-sweep-step-ghz 0.1",
        "--required-sweep-points 451",
        "--physical-window-start-ghz 5.0",
        "--physical-window-stop-ghz 30.0",
        "--max-shape-spike-ratio 4",
        "--max-shape-relative-step 0.25",
        "--photo-max-percent-error 5.0",
        "tar -czf",
        "sha256sum",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if "/home/researcher" in text:
        missing.append("must not contain local macOS /home/researcher paths")
    if missing:
        return Check("FAIL", name, f"missing_or_bad={missing[:8]}")
    return Check("PASS", name, "post-run EMX file check, Touchstone physical gate, EMX-first gate, SHA, and transfer tarball present")


def _handoff_package_source_contract_check(handoff_dir: Path, name: str) -> Check:
    required_fragments = {
        Path("rfic_transformer_inverse_design/dataset.py"): (
            "GROUND_CLEARANCE_AUDIT_FILENAME",
            "write_ground_clearance_audit",
            "ground_clearance_quality",
        ),
        Path("rfic_transformer_inverse_design/execution/evaluator.py"): (
            "SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME",
            "_attach_signal_shield_clearance_audit",
            "layout is not None and error is None",
        ),
        Path("rfic_transformer_inverse_design/layout/export.py"): (
            "SIGNAL_SHIELD_CLEARANCE_AUDIT_FILENAME",
            "_signal_shield_clearance_report",
            "_write_signal_shield_clearance_audit",
        ),
    }
    failures: list[str] = []
    for rel_path, fragments in required_fragments.items():
        path = handoff_dir / rel_path
        if not path.is_file():
            failures.append(f"missing {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in text]
        if missing:
            failures.append(f"{rel_path} missing {missing}")
    if failures:
        return Check("FAIL", name, "; ".join(failures[:4]))
    return Check("PASS", name, "sample-dataset raw clearance audit, evaluator pre-EMX rejection, and layout sidecar source present")


def _handoff_path_discovery_contract_check(handoff_dir: Path, name: str) -> Check:
    script_path = handoff_dir / "scripts" / "discover_mars_emx_cadence_paths.py"
    if not script_path.is_file():
        return Check("FAIL", name, f"missing: {script_path}")
    text = script_path.read_text(encoding="utf-8")
    required = (
        "--root",
        "--hint-command",
        "--tech-lib-hint",
        "DEFAULT_HINT_COMMANDS",
        "target_emx_wideband_rerun.commands.sh",
        "hint_command_files",
        "ready_to_patch",
        "suggested_patch_command",
        "patch_mars_config_paths.py",
        "preflight_dataset_config.py",
        "This helper is intentionally read-only",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        return Check("FAIL", name, f"missing={missing[:8]}")
    return Check("PASS", name, "read-only MARS EMX/Cadence path discovery and patch-suggestion source contract present")


def _handoff_path_patcher_smoke_check(handoff_dir: Path, name: str) -> Check:
    script_path = handoff_dir / "scripts" / "patch_mars_config_paths.py"
    config_path = handoff_dir / WIDEBAND_HANDOFF_CONFIG
    if not script_path.exists() or not config_path.exists():
        return Check("FAIL", name, "patcher script or wideband config is missing")
    with tempfile.TemporaryDirectory(prefix="mars_path_patch_smoke_") as tmpdir:
        smoke_dir = Path(tmpdir)
        patched_config = smoke_dir / "patched.yaml"
        summary_path = smoke_dir / "summary.json"
        command = [
            sys.executable or "python3",
            str(script_path),
            str(config_path),
            "--out-config",
            str(patched_config),
            "--summary",
            str(summary_path),
            "--emx-binary",
            "/opt/emx/bin/emx",
            "--emx-process-file",
            "/opt/pdk/proc.proc",
            "--cadence-install-root",
            "/opt/cadence/IC",
            "--cadence-pdk-cds-lib",
            "/opt/pdk/cds.lib",
            "--cadence-tech-lib",
            "tsmc65lp",
            "--cadence-layer-map",
            "/opt/pdk/layers.layermap",
        ]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError as exc:
            return Check("FAIL", name, f"{type(exc).__name__}: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
            return Check("FAIL", name, f"returncode={result.returncode}, detail={detail}")
        try:
            patched = _load_yaml_mapping(patched_config)
            summary = _read_json(summary_path)
        except Exception as exc:  # noqa: BLE001
            return Check("FAIL", name, f"{type(exc).__name__}: {exc}")
    failures: list[str] = []
    target = patched.get("target", {}) if isinstance(patched, dict) else {}
    emx = patched.get("emx", {}) if isinstance(patched, dict) else {}
    _require_numeric(failures, target, "frequency_start_hz", 5.0e9)
    _require_numeric(failures, target, "frequency_stop_hz", 50.0e9)
    _require_int(failures, target, "band_points", 451)
    _require_value(failures, emx, "port_mode", "single_ended_shield_grounded")
    _require_value(failures, emx, "emx_binary", "/opt/emx/bin/emx")
    if summary.get("overall_status") != "PASS":
        failures.append(f"summary overall_status={summary.get('overall_status')!r}")
    if failures:
        return Check("FAIL", name, "; ".join(failures[:8]))
    return Check("PASS", name, f"patched config read back; yaml_backend={summary.get('yaml_backend', 'unknown')}")


def _load_yaml_mapping(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml_mapping(text)
    return yaml.safe_load(text)


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped.startswith("- "):
            continue
        if ":" not in stripped:
            raise ValueError(f"line {line_number}: expected key: value")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"line {line_number}: invalid indentation")
        parent = stack[-1][1]
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_simple_yaml_scalar(value)
    return root


def _parse_simple_yaml_scalar(value: str) -> Any:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return stripped


def _handoff_quality_gate_contract_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    if not command_path.exists():
        return Check("FAIL", name, "command file is missing")
    command_text = command_path.read_text(encoding="utf-8")
    if "scripts/run_dataset_quality_gates.py" not in command_text:
        return Check("FAIL", name, "run_dataset_quality_gates.py call is missing")
    missing = [fragment for fragment in QUALITY_GATE_REQUIRED_FRAGMENTS if fragment not in command_text]
    if missing:
        return Check("FAIL", name, f"missing={missing[:8]}")
    return Check("PASS", name, f"{len(QUALITY_GATE_REQUIRED_FRAGMENTS)} required post-run gate args present")


def _handoff_progress_audit_contract_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    if not command_path.exists():
        return Check("FAIL", name, "command file is missing")
    command_text = command_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in PROGRESS_AUDIT_REQUIRED_FRAGMENTS if fragment not in command_text]
    if missing:
        return Check("FAIL", name, f"missing={missing[:8]}")
    return Check("PASS", name, f"{len(PROGRESS_AUDIT_REQUIRED_FRAGMENTS)} required run-progress args present")


def _require_numeric(failures: list[str], mapping: Any, key: str, expected: float, *, tolerance: float = 1.0) -> None:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    try:
        actual = float(value)
    except (TypeError, ValueError):
        failures.append(f"{key}={value!r}")
        return
    if abs(actual - expected) > tolerance:
        failures.append(f"{key}={actual:g}, expected={expected:g}")


def _require_int(failures: list[str], mapping: Any, key: str, expected: int) -> None:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    try:
        actual = int(value)
    except (TypeError, ValueError):
        failures.append(f"{key}={value!r}")
        return
    if actual != expected:
        failures.append(f"{key}={actual}, expected={expected}")


def _require_value(failures: list[str], mapping: Any, key: str, expected: Any) -> None:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if value != expected:
        failures.append(f"{key}={value!r}, expected={expected!r}")


def _handoff_shape_gate_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    if not command_path.exists():
        return Check("FAIL", name, "command file is missing")
    command_text = command_path.read_text(encoding="utf-8")
    required = [
        "--touchstone-shape-window-start-ghz",
        "--touchstone-shape-window-stop-ghz",
        "--touchstone-max-shape-spike-ratio",
        "--touchstone-max-shape-relative-step",
    ]
    missing = [item for item in required if item not in command_text]
    if missing:
        return Check("FAIL", name, f"missing shape gate args: {missing}")
    return Check("PASS", name, "Touchstone shape-window spike/jump gate args present")


def _handoff_compare_gate_check(handoff_dir: Path, name: str) -> Check:
    runbook_path = handoff_dir / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    if not runbook_path.exists():
        return Check("FAIL", name, "runbook is missing")
    text = runbook_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in COMPARE_GATE_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", name, f"missing strict compare args: {missing[:8]}")
    return Check("PASS", name, f"{len(COMPARE_GATE_REQUIRED_FRAGMENTS)} strict compare args present")


def _handoff_final_figure_verifier_runbook_contract_check(handoff_dir: Path, name: str) -> Check:
    runbook_path = handoff_dir / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    if not runbook_path.exists():
        return Check("FAIL", name, "runbook is missing")
    text = runbook_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", name, f"missing final-figure verifier runbook fragments: {missing[:8]}")
    return Check("PASS", name, f"{len(FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS)} final-figure runbook fragments present")


def _handoff_validation_chain_contract_check(handoff_dir: Path, name: str) -> Check:
    script_path = handoff_dir / "scripts" / "build_validation_chain_decision_card.py"
    if not script_path.exists():
        return Check("FAIL", name, f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in VALIDATION_CHAIN_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", name, f"missing={missing[:8]}")
    return Check("PASS", name, f"{len(VALIDATION_CHAIN_REQUIRED_FRAGMENTS)} validation-chain fragments present")


def _handoff_command_syntax_check(handoff_dir: Path, name: str) -> Check:
    command_path = handoff_dir / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    if not command_path.exists():
        return Check("FAIL", name, "command file is missing")
    result = subprocess.run(
        ["bash", "-n", str(command_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0:
        return Check("PASS", name, "bash -n passed")
    detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    return Check("FAIL", name, detail[:300])


def _acceptance_matrix_checks(package_dir: Path) -> list[Check]:
    path = package_dir / "acceptance_matrix_20260613.json"
    if not path.exists():
        return [Check("FAIL", "acceptance matrix", f"missing: {path}")]
    data = _read_json(path)
    status = data.get("overall_status")
    counts = data.get("status_counts", {})
    helper_items = [
        item
        for item in data.get("items", [])
        if item.get("requirement") == "MARS pull, progress, path, local gate, and handoff helpers exist"
    ]
    if status != "INCOMPLETE":
        return [Check("FAIL", "acceptance matrix boundary", f"expected INCOMPLETE, got {status}")]
    if not helper_items or helper_items[0].get("status") != "PASS":
        return [Check("FAIL", "acceptance matrix helper evidence", "portable MARS helper item is not PASS")]
    return [Check("PASS", "acceptance matrix boundary", f"overall_status=INCOMPLETE, counts={counts}")]


def _read_sha_manifest(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        digest, sep, rel_path = line.partition("  ")
        if not sep:
            continue
        entries.append((digest, rel_path.removeprefix("./")))
    return entries


def _is_python_cache_path(rel_path: Path) -> bool:
    parts = rel_path.as_posix().split("/")
    return any(part == "__pycache__" for part in parts) or rel_path.suffix in {".pyc", ".pyo"}


def _is_python_cache_name(name: str) -> bool:
    parts = [part for part in name.split("/") if part]
    return any(part == "__pycache__" for part in parts) or name.endswith((".pyc", ".pyo"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _worst_formula_percent_error(errors: Any) -> tuple[str, float]:
    if not isinstance(errors, dict):
        return "n/a", float("inf")
    parsed: list[tuple[str, float]] = []
    for name, item in errors.items():
        if isinstance(item, dict) and "max_percent_error" in item:
            try:
                parsed.append((str(name), float(item["max_percent_error"])))
            except (TypeError, ValueError):
                continue
    if not parsed:
        return "n/a", float("inf")
    return max(parsed, key=lambda item: item[1])


def _standalone_png_quality_failures(path: Path) -> list[str]:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return [f"Pillow unavailable: {type(exc).__name__}: {exc}"]
    if not path.is_file():
        return [f"missing PNG: {path.name}"]
    try:
        data = path.read_bytes()
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            extrema = ImageStat.Stat(rgb).extrema
    except Exception as exc:  # noqa: BLE001
        return [f"{path.name}: {type(exc).__name__}: {exc}"]
    failures: list[str] = []
    max_delta = max((hi - lo) for lo, hi in extrema)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        failures.append(f"{path.name}: missing PNG signature")
    if width < 64 or height < 36:
        failures.append(f"{path.name}: dimensions {width}x{height} below minimum")
    if len(data) < 1024:
        failures.append(f"{path.name}: bytes {len(data)} below minimum")
    if max_delta <= 1:
        failures.append(f"{path.name}: blank or nearly constant PNG")
    return failures


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Delivery Package Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Package: `{summary['package_dir']}`",
        f"- Zip: `{summary['zip_path']}`",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {check['status']} | {check['name']} | {check['detail']} |")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- This audit verifies local files, hashes, report assets, validation scripts, and MARS handoff portability.",
            "- It does not claim that MARS final-500, wideband 500, 248k, HFSS, ADS, or EMX runs are complete.",
        ]
    )
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
