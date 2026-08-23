#!/usr/bin/env python3
"""Verify a MARS handoff bundle after unpacking or installing it.

This is a local filesystem readiness gate. It does not run EMX, Cadence, HFSS,
or ADS. It catches common handoff mistakes before spending simulator time:
missing helper scripts, stale SHA manifests, non-portable runbook commands, and
missing Touchstone shape-window gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_SCRIPTS = (
    "audit_mars_run_progress.py",
    "watch_mars_run_progress.py",
    "discover_and_verify_mars_emx_return.py",
    "watch_mars_emx_return.py",
    "verify_target_emx_postrun_package.py",
    "package_mars_dataset_run.py",
    "verify_mars_dataset_package.py",
    "discover_mars_emx_cadence_paths.py",
    "patch_mars_config_paths.py",
    "preflight_dataset_config.py",
    "prepare_mars_wideband_config.py",
    "prepare_target_emx_wideband_rerun.py",
    "prepare_target_emx_postrun_validation.py",
    "backfill_ground_clearance_audit.py",
    "run_dataset_quality_gates.py",
    "audit_dataset_touchstones.py",
    "audit_touchstone_transformer.py",
    "compare_emx_hfss_ads.py",
    "build_validation_chain_decision_card.py",
    "build_mars_next_action_packet.py",
    "run_accepted_emx_hfss_ads_validation.py",
    "verify_accepted_emx_hfss_ads_figures.py",
    "run_package_selfcheck_compare.py",
    "audit_geometry_quality.py",
    "audit_sampling_distribution.py",
    "extract_touchstone_response_features.py",
    "audit_response_feature_coverage.py",
    "audit_zin_coverage.py",
    "select_hfss_validation_samples.py",
    "run_hfss_emx_validation_batch.py",
    "audit_248k_launch_readiness.py",
    "diagnose_cm_mismatch.py",
    "build_emx_first_validation_gate.py",
    "audit_photo_matched_vs_target_geometry.py",
)
REQUIRED_PACKAGE_SOURCES = (
    Path("rfic_transformer_inverse_design/dataset.py"),
    Path("rfic_transformer_inverse_design/execution/evaluator.py"),
    Path("rfic_transformer_inverse_design/layout/export.py"),
)

WIDEBAND_CONFIG = "configs/mars_dataset_500_wideband_20260613.yaml"
WIDEBAND_COMMANDS = "project_runbook/mars_dataset_500_wideband_20260613.commands.sh"
TARGET_EMX_COMMANDS = "project_runbook/target_emx_wideband_rerun_20260613/target_emx_wideband_rerun.commands.sh"
TARGET_EMX_POSTRUN_COMMANDS = (
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
    root = Path(args.root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else root / "mars_handoff_verify_20260613"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[Check] = []
    checks.extend(_root_checks(root))
    checks.extend(_required_file_checks(root))
    checks.append(_sha_manifest_check(root, require_sha=not args.allow_missing_sha))
    checks.append(_dataset_package_helper_contract_check(root))
    checks.append(_dataset_package_verifier_contract_check(root))
    checks.append(_package_source_contract_check(root))
    checks.append(_command_portability_check(root))
    checks.append(_wideband_config_contract_check(root))
    checks.append(_target_emx_rerun_command_contract_check(root))
    checks.append(_target_emx_postrun_command_contract_check(root))
    checks.append(_path_discovery_helper_contract_check(root))
    checks.append(_path_patcher_smoke_check(root, out_dir))
    checks.append(_progress_audit_contract_check(root))
    checks.append(_quality_gate_contract_check(root))
    checks.append(_command_shape_gate_check(root))
    checks.append(_compare_gate_contract_check(root))
    checks.append(_validation_chain_contract_check(root))
    checks.append(_batch_compare_runner_contract_check(root))
    checks.append(_accepted_emx_hfss_runner_contract_check(root))
    checks.append(_accepted_figure_verifier_contract_check(root))
    checks.append(_accepted_final_figure_runbook_contract_check(root))
    checks.append(_emx_first_gate_script_contract_check(root))
    checks.append(_target_emx_import_verifier_contract_check(root))
    checks.append(_touchstone_transformer_audit_contract_check(root))
    checks.append(_emx_return_watcher_contract_check(root))
    checks.append(_target_envelope_quality_scripts_contract_check(root))
    checks.append(_production_readiness_contract_check(root))
    checks.append(_command_syntax_check(root))

    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "root": str(root),
        "checks": [check.as_dict() for check in checks],
        "limitations": [
            "This verifier checks MARS handoff/install files only.",
            "A PASS does not mean EMX, Cadence, MARS dataset generation, HFSS, or ADS has run.",
            "Strict EMX/Cadence path preflight must still pass before launching the wideband pilot.",
        ],
    }
    summary_path = out_dir / "mars_handoff_verify_summary.json"
    report_path = out_dir / "mars_handoff_verify_report.md"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    for check in checks:
        print(f"{check.status:4s} {check.name}: {check.detail}")
    return 2 if overall_status == "FAIL" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Unpacked handoff or installed project root")
    parser.add_argument("--out-dir")
    parser.add_argument(
        "--allow-missing-sha",
        action="store_true",
        help="Use after installing into an existing project checkout where SHA256SUMS.txt was not copied",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _root_checks(root: Path) -> list[Check]:
    if not root.is_dir():
        return [Check("FAIL", "handoff root", f"missing directory: {root}")]
    checks = [Check("PASS", "handoff root", str(root))]
    for dirname in ("scripts", "configs", "project_runbook", "rfic_transformer_inverse_design"):
        path = root / dirname
        checks.append(Check("PASS" if path.is_dir() else "FAIL", f"{dirname} directory", str(path)))
    return checks


def _required_file_checks(root: Path) -> list[Check]:
    required = [Path("scripts") / name for name in REQUIRED_SCRIPTS]
    required.extend([Path(WIDEBAND_CONFIG), Path(WIDEBAND_COMMANDS), Path(TARGET_EMX_COMMANDS), Path(TARGET_EMX_POSTRUN_COMMANDS)])
    required.extend(REQUIRED_PACKAGE_SOURCES)
    missing = [str(path) for path in required if not (root / path).is_file()]
    if missing:
        return [Check("FAIL", "required handoff files", f"missing={missing}")]
    return [Check("PASS", "required handoff files", f"{len(required)} files present")]


def _package_source_contract_check(root: Path) -> Check:
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
        path = root / rel_path
        if not path.is_file():
            failures.append(f"missing {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in text]
        if missing:
            failures.append(f"{rel_path} missing {missing}")
    if failures:
        return Check("FAIL", "package source clearance contract", "; ".join(failures[:4]))
    return Check("PASS", "package source clearance contract", "sample-dataset raw clearance audit, evaluator pre-EMX rejection, and layout sidecar source present")


def _sha_manifest_check(root: Path, *, require_sha: bool) -> Check:
    sha_path = root / "SHA256SUMS.txt"
    if not sha_path.exists():
        status = "FAIL" if require_sha else "WARN"
        return Check(status, "SHA256SUMS manifest", "SHA256SUMS.txt is missing")
    entries = _read_sha_manifest(sha_path)
    mismatches = [
        rel_path
        for expected, rel_path in entries
        if not (root / rel_path).is_file() or _sha256(root / rel_path) != expected
    ]
    if mismatches:
        return Check("FAIL", "SHA256SUMS manifest", f"mismatches={mismatches[:8]}")
    return Check("PASS", "SHA256SUMS manifest", f"{len(entries)} files matched")


def _dataset_package_helper_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "package_mars_dataset_run.py"
    if not script_path.exists():
        return Check("FAIL", "dataset package helper contract", f"missing: {script_path}")
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
    result = _run_help(script_path)
    failures = []
    if result.returncode != 0:
        failures.append(f"--help returncode={result.returncode}")
    help_text = f"{result.stdout}\n{result.stderr}"
    if "--report" not in help_text:
        failures.append("--help missing --report")
    if missing_source:
        failures.append(f"source missing={missing_source}")
    if failures:
        return Check("FAIL", "dataset package helper contract", "; ".join(failures[:5]))
    return Check("PASS", "dataset package helper contract", "--report help, category_counts, Markdown report renderer, raw clearance audit packaging, metadata/cache exclude logic, empty-file refusal, and progress/watch evidence packaging present")


def _dataset_package_verifier_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "verify_mars_dataset_package.py"
    if not script_path.exists():
        return Check("FAIL", "dataset package verifier contract", f"missing: {script_path}")
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
    result = _run_help(script_path)
    failures = []
    if result.returncode != 0:
        failures.append(f"--help returncode={result.returncode}")
    help_text = f"{result.stdout}\n{result.stderr}"
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
        return Check("FAIL", "dataset package verifier contract", "; ".join(failures[:5]))
    return Check(
        "PASS",
        "dataset package verifier contract",
        "--require-quality-gates/--require-progress-evidence/--require-quality-figures/--inventory-report help, raw-clearance/geometry-quality/EMX command contract pass-through, Markdown report check, progress evidence check, quality-gate/clearance-contract/figure/raw-clearance checks, inventory category-count check, non-empty inventory file check, clearance-audit inventory check, expected-count evidence check, tar metadata/cache hygiene check, tar inventory exactness check, duplicate-member check, and Python <3.12 extraction fallback present",
    )


def _run_help(script_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable or "python3", str(script_path), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _command_portability_check(root: Path) -> Check:
    command_path = root / WIDEBAND_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "portable wideband commands", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required_fragments = [
        "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}",
        "scripts/preflight_dataset_config.py",
        "scripts/audit_mars_run_progress.py",
        "scripts/run_dataset_quality_gates.py",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if "/home/researcher" in text:
        missing.append("must not contain local macOS /home/researcher paths")
    if missing:
        return Check("FAIL", "portable wideband commands", f"missing_or_bad={missing}")
    return Check("PASS", "portable wideband commands", "relative config path, progress audit, and quality gate commands present")


def _path_discovery_helper_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "discover_mars_emx_cadence_paths.py"
    if not script_path.exists():
        return Check("FAIL", "path discovery helper contract", f"missing: {script_path}")
    help_result = _run_help(script_path)
    source = script_path.read_text(encoding="utf-8")
    combined = help_result.stdout + "\n" + source
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
    missing = [fragment for fragment in required if fragment not in combined]
    if help_result.returncode != 0:
        missing.append(f"--help returncode={help_result.returncode}")
    if missing:
        return Check("FAIL", "path discovery helper contract", f"missing_or_bad={missing[:8]}")
    return Check(
        "PASS",
        "path discovery helper contract",
        "read-only discovery, target-command hints, tech-lib hints, patch suggestion, and strict-preflight follow-up are present",
    )


def _command_shape_gate_check(root: Path) -> Check:
    command_path = root / WIDEBAND_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "Touchstone shape-window gate", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required = [
        "--touchstone-shape-window-start-ghz",
        "--touchstone-shape-window-stop-ghz",
        "--touchstone-max-shape-spike-ratio",
        "--touchstone-max-shape-relative-step",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        return Check("FAIL", "Touchstone shape-window gate", f"missing={missing}")
    return Check("PASS", "Touchstone shape-window gate", "shape spike/jump gate arguments present")


def _compare_gate_contract_check(root: Path) -> Check:
    runbook_path = root / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    if not runbook_path.exists():
        return Check("FAIL", "HFSS/EMX compare grid gate", f"missing: {runbook_path}")
    text = runbook_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in COMPARE_GATE_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", "HFSS/EMX compare grid gate", f"missing={missing[:8]}")
    return Check("PASS", "HFSS/EMX compare grid gate", f"{len(COMPARE_GATE_REQUIRED_FRAGMENTS)} strict compare args present")


def _validation_chain_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "build_validation_chain_decision_card.py"
    if not script_path.exists():
        return Check("FAIL", "EMX/HFSS/ADS validation-chain contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in VALIDATION_CHAIN_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "EMX/HFSS/ADS validation-chain contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "EMX/HFSS/ADS validation-chain contract",
        f"{len(VALIDATION_CHAIN_REQUIRED_FRAGMENTS)} validation-chain boundary fragments present",
    )


def _batch_compare_runner_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "run_hfss_emx_validation_batch.py"
    if not script_path.exists():
        return Check("FAIL", "HFSS/EMX batch compare runner contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in BATCH_COMPARE_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "HFSS/EMX batch compare runner contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "HFSS/EMX batch compare runner contract",
        f"{len(BATCH_COMPARE_REQUIRED_FRAGMENTS)} batch runner fragments present",
    )


def _accepted_emx_hfss_runner_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
    if not script_path.exists():
        return Check("FAIL", "accepted EMX/HFSS final runner contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "accepted EMX/HFSS final runner contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "accepted EMX/HFSS final runner contract",
        f"{len(ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS)} accepted-EMX/HFSS runner fragments present",
    )


def _accepted_figure_verifier_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
    if not script_path.exists():
        return Check("FAIL", "accepted final figure verifier contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "accepted final figure verifier contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "accepted final figure verifier contract",
        f"{len(ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS)} accepted final-figure verifier fragments present",
    )


def _accepted_final_figure_runbook_contract_check(root: Path) -> Check:
    runbook_path = root / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    if not runbook_path.exists():
        return Check("FAIL", "accepted final figure verifier runbook contract", f"missing: {runbook_path}")
    text = runbook_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", "accepted final figure verifier runbook contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "accepted final figure verifier runbook contract",
        f"{len(FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS)} final-figure runbook fragments present",
    )


def _emx_first_gate_script_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "build_emx_first_validation_gate.py"
    if not script_path.exists():
        return Check("FAIL", "EMX-first gate script contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing: list[str] = []
    if "target transformer sanity" in source:
        missing.append("stale target transformer sanity check name must be removed")
    missing.extend(fragment for fragment in EMX_FIRST_GATE_REQUIRED_FRAGMENTS if fragment not in source)
    if missing:
        return Check("FAIL", "EMX-first gate script contract", f"missing_or_stale={missing[:8]}")
    return Check(
        "PASS",
        "EMX-first gate script contract",
        f"{len(EMX_FIRST_GATE_REQUIRED_FRAGMENTS)} EMX-first gate boundary fragments present",
    )


def _target_emx_import_verifier_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "verify_target_emx_postrun_package.py"
    if not script_path.exists():
        return Check("FAIL", "target EMX post-run import verifier contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in TARGET_EMX_IMPORT_VERIFIER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "target EMX post-run import verifier contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "target EMX post-run import verifier contract",
        f"{len(TARGET_EMX_IMPORT_VERIFIER_REQUIRED_FRAGMENTS)} import verifier fragments present",
    )


def _touchstone_transformer_audit_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "audit_touchstone_transformer.py"
    if not script_path.exists():
        return Check("FAIL", "Touchstone transformer audit contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in TOUCHSTONE_TRANSFORMER_AUDIT_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "Touchstone transformer audit contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "Touchstone transformer audit contract",
        f"{len(TOUCHSTONE_TRANSFORMER_AUDIT_REQUIRED_FRAGMENTS)} Touchstone physical/step-grid fragments present",
    )


def _emx_return_watcher_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "watch_mars_emx_return.py"
    if not script_path.exists():
        return Check("FAIL", "target EMX return watcher contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in EMX_RETURN_WATCHER_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "target EMX return watcher contract", f"missing={missing[:8]}")
    return Check(
        "PASS",
        "target EMX return watcher contract",
        f"{len(EMX_RETURN_WATCHER_REQUIRED_FRAGMENTS)} MARS returned-EMX watch fragments present",
    )


def _target_envelope_quality_scripts_contract_check(root: Path) -> Check:
    failures: list[str] = []
    total_fragments = 0
    for script_name, fragments in TARGET_ENVELOPE_QUALITY_SCRIPT_REQUIRED_FRAGMENTS.items():
        script_path = root / "scripts" / script_name
        if not script_path.exists():
            failures.append(f"missing {script_name}")
            continue
        source = script_path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in source]
        total_fragments += len(fragments)
        if missing:
            failures.append(f"{script_name} missing {missing[:6]}")
    if failures:
        return Check("FAIL", "target-envelope quality scripts contract", "; ".join(failures[:4]))
    return Check(
        "PASS",
        "target-envelope quality scripts contract",
        f"{total_fragments} Zin/K-Qp/Lp-Ls target-envelope fragments present",
    )


def _production_readiness_contract_check(root: Path) -> Check:
    script_path = root / "scripts" / "audit_248k_launch_readiness.py"
    if not script_path.exists():
        return Check("FAIL", "248k launch readiness contract", f"missing: {script_path}")
    source = script_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in PRODUCTION_READINESS_REQUIRED_FRAGMENTS if fragment not in source]
    if missing:
        return Check("FAIL", "248k launch readiness contract", f"missing={missing[:8]}")
    return Check("PASS", "248k launch readiness contract", f"{len(PRODUCTION_READINESS_REQUIRED_FRAGMENTS)} readiness fragments present")


def _quality_gate_contract_check(root: Path) -> Check:
    command_path = root / WIDEBAND_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "quality-gate command contract", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    if "scripts/run_dataset_quality_gates.py" not in text:
        return Check("FAIL", "quality-gate command contract", "run_dataset_quality_gates.py call is missing")
    missing = [fragment for fragment in QUALITY_GATE_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", "quality-gate command contract", f"missing={missing[:8]}")
    return Check("PASS", "quality-gate command contract", f"{len(QUALITY_GATE_REQUIRED_FRAGMENTS)} required post-run gate args present")


def _progress_audit_contract_check(root: Path) -> Check:
    command_path = root / WIDEBAND_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "run-progress command contract", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    missing = [fragment for fragment in PROGRESS_AUDIT_REQUIRED_FRAGMENTS if fragment not in text]
    if missing:
        return Check("FAIL", "run-progress command contract", f"missing={missing[:8]}")
    return Check("PASS", "run-progress command contract", f"{len(PROGRESS_AUDIT_REQUIRED_FRAGMENTS)} required run-progress args present")


def _wideband_config_contract_check(root: Path) -> Check:
    config_path = root / WIDEBAND_CONFIG
    if not config_path.exists():
        return Check("FAIL", "wideband config contract", f"missing: {config_path}")
    try:
        data = _load_yaml_mapping(config_path)
    except Exception as exc:  # noqa: BLE001 - report exact config parse problem.
        return Check("FAIL", "wideband config contract", f"{type(exc).__name__}: {exc}")
    if not isinstance(data, dict):
        return Check("FAIL", "wideband config contract", f"top-level YAML is {type(data).__name__}")

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
        return Check("FAIL", "wideband config contract", "; ".join(failures[:8]))
    return Check(
        "PASS",
        "wideband config contract",
        "5-50 GHz, 0.1 GHz step, 451 points, grounded shield port mode, pin purpose 51",
    )


def _target_emx_rerun_command_contract_check(root: Path) -> Check:
    command_path = root / TARGET_EMX_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "target EMX wideband rerun command contract", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required_fragments = (
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
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if "/home/researcher" in text:
        missing.append("must not contain local macOS /home/researcher paths")
    missing.extend(_target_emx_command_frequency_grid_failures(text))
    if missing:
        return Check("FAIL", "target EMX wideband rerun command contract", f"missing_or_bad={missing[:8]}")
    return Check(
        "PASS",
        "target EMX wideband rerun command contract",
        "target sample EMX command, grounded ports, pin 51, and exact 5-50 GHz / 0.1 GHz / 451-point frequency list present",
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


def _target_emx_postrun_command_contract_check(root: Path) -> Check:
    command_path = root / TARGET_EMX_POSTRUN_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "target EMX post-run validation command contract", f"missing: {command_path}")
    text = command_path.read_text(encoding="utf-8")
    required_fragments = (
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
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if "/home/researcher" in text:
        missing.append("must not contain local macOS /home/researcher paths")
    if missing:
        return Check("FAIL", "target EMX post-run validation command contract", f"missing_or_bad={missing[:8]}")
    return Check(
        "PASS",
        "target EMX post-run validation command contract",
        "non-empty EMX file check, Touchstone physical gate, EMX-first ADS-photo gate, SHA, and transfer tarball are present",
    )


def _path_patcher_smoke_check(root: Path, out_dir: Path) -> Check:
    script_path = root / "scripts" / "patch_mars_config_paths.py"
    config_path = root / WIDEBAND_CONFIG
    if not script_path.exists():
        return Check("FAIL", "path patcher smoke", f"missing: {script_path}")
    if not config_path.exists():
        return Check("FAIL", "path patcher smoke", f"missing: {config_path}")
    patched_config = out_dir / "path_patcher_smoke_patched.yaml"
    summary_path = out_dir / "path_patcher_smoke_summary.json"
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
        return Check("FAIL", "path patcher smoke", f"{type(exc).__name__}: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
        return Check("FAIL", "path patcher smoke", f"returncode={result.returncode}, detail={detail}")
    try:
        patched = _load_yaml_mapping(patched_config)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report exact readback failure.
        return Check("FAIL", "path patcher smoke", f"{type(exc).__name__}: {exc}")
    failures: list[str] = []
    _require_numeric(failures, patched.get("target", {}) if isinstance(patched, dict) else {}, "frequency_start_hz", 5.0e9)
    _require_numeric(failures, patched.get("target", {}) if isinstance(patched, dict) else {}, "frequency_stop_hz", 50.0e9)
    _require_int(failures, patched.get("target", {}) if isinstance(patched, dict) else {}, "band_points", 451)
    _require_value(failures, patched.get("emx", {}) if isinstance(patched, dict) else {}, "port_mode", "single_ended_shield_grounded")
    _require_value(failures, patched.get("emx", {}) if isinstance(patched, dict) else {}, "emx_binary", "/opt/emx/bin/emx")
    if summary.get("overall_status") != "PASS":
        failures.append(f"summary overall_status={summary.get('overall_status')!r}")
    if failures:
        return Check("FAIL", "path patcher smoke", "; ".join(failures[:8]))
    backend = summary.get("yaml_backend", "unknown")
    return Check("PASS", "path patcher smoke", f"patched config read back; yaml_backend={backend}")


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


def _command_syntax_check(root: Path) -> Check:
    command_path = root / WIDEBAND_COMMANDS
    if not command_path.exists():
        return Check("FAIL", "wideband command syntax", f"missing: {command_path}")
    result = subprocess.run(
        ["bash", "-n", str(command_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0:
        return Check("PASS", "wideband command syntax", "bash -n passed")
    detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
    return Check("FAIL", "wideband command syntax", detail[:300])


def _read_sha_manifest(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        digest, sep, rel_path = line.partition("  ")
        if sep:
            entries.append((digest, rel_path.removeprefix("./")))
    return entries


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS Handoff Verify Report",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Root: `{summary['root']}`",
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
            "- This verifier checks handoff/install files only.",
            "- It does not prove that EMX, Cadence, MARS dataset generation, HFSS, or ADS has run.",
            "- Strict path preflight remains required before launching the wideband pilot.",
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
