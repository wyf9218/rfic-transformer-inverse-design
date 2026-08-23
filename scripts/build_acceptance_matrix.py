#!/usr/bin/env python3
"""Build a requirement-by-requirement acceptance matrix for the project.

The matrix is deliberately conservative. It maps each important project claim to
local evidence files and marks missing external work as PENDING/BLOCKED instead
of inferring completion from nearby artifacts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

COMPARE_SCRIPT_REQUIRED_FLAGS = (
    "--expected-frequency-step-ghz",
    "--expected-frequency-points",
    "--frequency-tolerance-hz",
    "--require-matching-frequency-grid",
    "ADS no-extrapolation coverage",
)

ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS = (
    "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS",
    "ACCEPT_HFSS_VALIDATION_SAMPLE",
    "accepted EMX import verifier evidence",
    "accepted EMX import artifact bundle",
    "_accepted_import_artifact_bundle_check",
    "accepted_emx_reference_bundle",
    "READY_FOR_HFSS",
    "ADS/Python formula note",
    "formula_note",
    "ADS Data Display equation template",
    "Zp = Z11 - Z12 + Z22 - Z21",
    "Zm = Z31 - Z32 + Z42 - Z41",
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
    "--expected-source-kind",
    "differential Z finiteness",
    "differential Z reciprocity",
    "differential Z positive-realness",
    "EMX-first gate internal checks",
    "--min-target-abs-k",
    "--min-window-abs-k",
    "--max-target-abs-k",
    "_png_figure_failures",
    "missing PNG IHDR chunk",
    "valid dimensions",
    "ADS no-extrapolation coverage",
    "_compare_source_checks",
    "_compare_criterion_checks",
    "_compare_metric_checks",
    "_plot_data_checks",
    "_target_marker_records",
    "_write_ads_style_target_marker_tables",
    "_target_marker_checks",
    "ADS-style plot_data integrity",
    "ADS-style target marker table",
    "target_marker_paths",
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
    "_target_marker_checks",
    "_target_marker_row_failures",
    "_target_marker_single_row_failures",
    "target_marker_paths",
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

TARGET_EMX_POSTRUN_REQUIRED_FRAGMENTS = (
    'test -s "$EMX_S4P"',
    "sha256sum",
    "scripts/audit_touchstone_transformer.py",
    "--expected-source-kind EMX",
    "--expected-frequency-start-ghz 5.0",
    "--expected-frequency-stop-ghz 50.0",
    "--expected-frequency-step-ghz 0.1",
    "--expected-frequency-points 451",
    "--min-target-abs-k 0.05",
    "--max-target-abs-k 0.98",
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
)
FIXED_MATRIX_GENERATED_UTC = datetime(2026, 6, 13, tzinfo=timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class MatrixItem:
    requirement: str
    status: str
    evidence: list[str]
    finding: str
    next_action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "status": self.status,
            "evidence": self.evidence,
            "finding": self.finding,
            "next_action": self.next_action,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(args.project_root).expanduser().resolve()
    package_dir = Path(args.package_dir).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve()

    items = _build_items(project_root, package_dir)
    status_counts = _status_counts(items)
    summary = {
        "generated_utc": FIXED_MATRIX_GENERATED_UTC,
        "project_root": str(project_root),
        "package_dir": str(package_dir),
        "overall_status": "PASS" if all(item.status == "PASS" for item in items) else "INCOMPLETE",
        "status_counts": status_counts,
        "items": [item.as_dict() for item in items],
        "limitations": [
            "This matrix summarizes local evidence only.",
            "PENDING or BLOCKED items must not be reported as completed work.",
            "A PASS item may still be scoped, for example narrowband-only HFSS-vs-EMX correlation.",
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    out_md.write_text(_render_markdown(summary), encoding="utf-8")

    print(f"overall_status={summary['overall_status']}")
    print(f"summary={out_json}")
    print(f"report={out_md}")
    for status, count in sorted(status_counts.items()):
        print(f"{status}={count}")
    return 2 if summary["overall_status"] != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default="/home/researcher/Documents/模拟变压器AI反向建模")
    parser.add_argument("--package-dir", default="/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613")
    parser.add_argument("--out-json", default="/home/researcher/Documents/模拟变压器AI反向建模/acceptance_matrix_20260613.json")
    parser.add_argument("--out-md", default="/home/researcher/Documents/模拟变压器AI反向建模/ACCEPTANCE_MATRIX_20260613_CN.md")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _build_items(project_root: Path, package_dir: Path) -> list[MatrixItem]:
    hfss_preflight = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "touchstone_preflight_hfss_wideband_20260613"
        / "touchstone_transformer_audit_summary.json"
    )
    final500_geom = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "geometry_quality_audit_final500_selected_20260613"
        / "geometry_quality_audit_summary.json"
    )
    target_summary = _read_json(project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "summary.json")
    clearance = _read_json(project_root / "final500_clearance_audit_visuals_20260613" / "clearance_audit_visual_summary.json")
    narrowband_compare = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "full_sheetimpedance_freqexpr_m9pow1p5_refined_analysis"
        / "emx_hfss_ads_comparison_summary.json"
    )
    emx_first_gate = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "emx_first_validation_gate_20260613"
        / "emx_first_validation_gate_summary.json"
    )
    wideband_preflight = _read_json(project_root / "mars_dataset_500_wideband_20260613_preflight.json")
    wideband_strict = _read_json(project_root / "mars_dataset_500_wideband_20260613_preflight_strict_paths.json")
    package_manifest = _read_json(package_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json")
    delivery_audit = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    handoff_verify = _read_json(project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json")
    zin_audit = _read_json(project_root / "literature_improvement_create_only_50" / "zin_coverage_audit_20260613" / "zin_coverage_audit_summary.json")
    response_extract = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "response_feature_extraction_package_demo_20260613"
        / "response_feature_extraction_summary.json"
    )
    response_extract_min500 = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "response_feature_extraction_package_demo_20260613"
        / "zin_coverage_audit_min500_20260613"
        / "zin_coverage_audit_summary.json"
    )
    launch_readiness = _read_json(
        project_root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_summary.json"
    )
    target_emx_rerun = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_rerun_summary.json"
    )
    target_emx_postrun = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_postrun_validation_summary.json"
    )
    validation_chain = _read_json(
        project_root
        / "validation_chain_decision_20260614"
        / "validation_chain_decision_summary.json"
    )
    ads_metric_formula = _read_json(
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "ads_metric_formula_consistency_20260614"
        / "ads_metric_formula_consistency_summary.json"
    )
    mars_next_action = _read_json(
        project_root
        / "mars_next_action_packet_20260614"
        / "mars_next_action_packet_summary.json"
    )

    return [
        _hfss_touchstone_item(hfss_preflight, package_dir),
        _hfss_model_view_item(package_dir),
        _target_emx_first_gate_item(project_root, emx_first_gate),
        _hfss_emx_narrowband_item(narrowband_compare),
        _target_emx_wideband_rerun_item(project_root, target_emx_rerun),
        _target_emx_postrun_validation_item(project_root, target_emx_postrun),
        _hfss_emx_wideband_item(),
        _hfss_emx_strict_compare_contract_item(project_root, delivery_audit, handoff_verify),
        _accepted_emx_hfss_final_runner_contract_item(project_root, delivery_audit, handoff_verify),
        _accepted_final_figure_verifier_contract_item(project_root, delivery_audit, handoff_verify),
        _validation_chain_decision_item(project_root, validation_chain),
        _ads_metric_formula_consistency_item(project_root, ads_metric_formula),
        _mars_next_action_packet_item(project_root, mars_next_action),
        _geometry_item(project_root, final500_geom, target_summary),
        _clearance_item(clearance),
        _response_feature_extractor_item(project_root, response_extract, response_extract_min500),
        _zin_coverage_item(zin_audit),
        _create_only_distribution_item(project_root),
        _final500_pull_item(project_root),
        _wideband_config_item(wideband_preflight, wideband_strict),
        _mars_helpers_item(project_root / "rfic-transformer-inverse-design"),
        _delivery_package_audit_item(project_root, delivery_audit),
        _report_asset_usage_item(project_root, package_dir, package_manifest, delivery_audit),
        _production_248k_item(project_root, launch_readiness),
        _package_reproducibility_item(project_root, package_dir, package_manifest),
    ]


def _hfss_touchstone_item(summary: dict[str, Any], package_dir: Path) -> MatrixItem:
    evidence = [
        str(package_dir / "ec6698dfc575950b_HFSS_WIDEBAND_0p1_50GHz_step0p1.s4p"),
        str(package_dir / "touchstone_preflight_hfss_wideband_20260613" / "touchstone_transformer_audit_summary.json"),
    ]
    freq = summary.get("frequency", {})
    ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("port_count") == 4
        and int(freq.get("points", 0)) == 500
        and float(freq.get("stop_hz", 0.0)) >= 50.0e9
        and _summary_checks_pass(
            summary,
            (
                "source identity",
                "differential Z finiteness",
                "differential Z reciprocity",
                "differential Z positive-realness",
                "ADS-equivalent metric finiteness",
            ),
        )
    )
    return MatrixItem(
        "Single HFSS validation sample has usable wideband .s4p evidence",
        "PASS" if ok else "PENDING",
        evidence,
        "HFSS .s4p preflight PASS with 4 ports, 0.1-50 GHz / 500 points, and differential Z finiteness/reciprocity/positive-realness."
        if ok
        else "HFSS wideband Touchstone preflight evidence is missing, incomplete, or lacks differential Z health checks.",
        "Use this file for ADS 5-50 GHz plotting; do not substitute the narrowband EMX .s4p.",
    )


def _hfss_model_view_item(package_dir: Path) -> MatrixItem:
    summary_path = package_dir / "hfss_model_geometry_asset_audit_20260614" / "hfss_model_geometry_asset_audit_summary.json"
    summary = _read_json(summary_path)
    paths = [
        package_dir / "hfss_model_views" / "hfss_payload_geometry_top_annotated.png",
        package_dir / "hfss_model_views" / "hfss_payload_geometry_isometric.png",
        package_dir / "hfss_model_views" / "hfss_payload_geometry_quality_checks.png",
        package_dir / "hfss_model_views" / "ec6698dfc575950b_hfss_model_no_air.step",
        summary_path,
    ]
    ok = (
        all(path.exists() for path in paths)
        and summary.get("overall_status") == "PASS"
        and summary.get("decision") == "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS"
        and _summary_checks_pass(
            summary,
            (
                "HFSS top-view PNG",
                "HFSS isometric-view PNG",
                "HFSS geometry-quality PNG",
                "HFSS STEP model",
            ),
        )
    )
    return MatrixItem(
        "HFSS modeled geometry is visually traceable",
        "PASS" if ok else "PENDING",
        [str(path) for path in paths],
        "Top/isometric/quality PNGs are decodable, sufficiently large, and nonblank; STEP export has required STEP tokens and entity content."
        if ok
        else "HFSS model-view asset audit is missing/failing, or one or more PNG/STEP artifacts are not inspectable.",
        "Use these assets only for HFSS geometry traceability; physical correctness still requires EMX-first, HFSS physical, and ADS Lp/Ls/Q/K gates.",
    )


def _hfss_emx_narrowband_item(summary: dict[str, Any]) -> MatrixItem:
    metrics = summary.get("metrics", {})
    required = {"k", "qp", "qs", "lp_nh", "ls_nh"}
    metric_status_ok = required.issubset(metrics) and all(metrics[name].get("status") == "PASS" for name in required)
    window = summary.get("frequency_window_hz") or summary.get("frequency_overlap_hz", {})
    ok = summary.get("overall_status") == "PASS" and metric_status_ok and int(window.get("count", 0)) >= 9
    return MatrixItem(
        "Sample HFSS-vs-EMX narrowband core metrics meet the 5% gate",
        "PASS" if ok else "PARTIAL",
        [
            "/home/researcher/Documents/模拟变压器AI反向建模/hfss_validation/final500_ec6698dfc575950b/full_sheetimpedance_freqexpr_m9pow1p5_refined_analysis/emx_hfss_ads_comparison_summary.json",
            "/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613/hfss_vs_emx_narrowband_reference.png",
        ],
        "K/Qp/Qs/Lp/Ls pass <5% over the explicit 13.5-16.5 GHz / 9-point window; this is narrowband only."
        if ok
        else "Narrowband comparison is missing, one core metric exceeds 5%, or the required comparison window is not covered.",
        "Do not describe this as 5-50 GHz EMX validation; run wideband EMX/HFSS correlation after the MARS pilot.",
    )


def _target_emx_first_gate_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    evidence_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "emx_first_validation_gate_20260613"
    evidence = [
        str(evidence_dir / "emx_first_validation_gate_summary.json"),
        str(evidence_dir / "emx_first_validation_gate_report.md"),
        str(evidence_dir / "emx_first_validation_gate_ads_style_metrics.png"),
        str(evidence_dir / "emx_first_validation_gate_metrics.csv"),
    ]
    decision = summary.get("decision")
    overall = summary.get("overall_status")
    ok = overall == "PASS" and decision == "ACCEPT_AS_GOLDEN_EMX_REFERENCE"
    failed_checks = [
        str(item.get("name"))
        for item in summary.get("checks", [])
        if item.get("status") == "FAIL"
    ]
    if ok:
        status = "PASS"
        finding = "Target EMX .s4p passes source identity, ADS-photo anchor, sweep coverage, ADS no-extrapolation plot grid, physical metric window, smooth transformer metric window, and transformer sanity gates."
        next_action = "Use this accepted EMX file as the only reference for HFSS/ADS comparison."
    elif summary.get("_missing"):
        status = "PENDING"
        finding = "Target EMX-first validation evidence is missing."
        next_action = "Run build_emx_first_validation_gate.py on the regenerated wideband EMX .s4p before any HFSS comparison."
    else:
        status = "BLOCKED"
        failed_detail = ", ".join(failed_checks) if failed_checks else f"overall={overall}, decision={decision}"
        finding = f"Current target EMX cannot be used as a golden reference; EMX-first now also records ADS no-extrapolation plot grid, physical metric window, and smooth transformer metric window status; failed checks: {failed_detail}."
        next_action = "Regenerate the target EMX wideband 5-50 GHz / 0.1 GHz / 451-point .s4p on MARS and pass the EMX-first no-extrapolation/photo/physics gate before proceeding."
    return MatrixItem(
        "Target EMX .s4p is accepted as the golden ADS/physics reference",
        status,
        evidence,
        finding,
        next_action,
    )


def _target_emx_wideband_rerun_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    evidence_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
    )
    generated = summary.get("generated_frequency_hz", {})
    ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "READY_FOR_MARS_EMX_RERUN_COMMAND_ONLY"
        and int(generated.get("points", 0)) == 451
        and float(generated.get("start", 0.0)) == 5.0e9
        and float(generated.get("stop", 0.0)) == 50.0e9
        and float(generated.get("step", 0.0)) == 1.0e8
    )
    return MatrixItem(
        "Target sample EMX wideband rerun command is traceable",
        "PASS" if ok else "PENDING",
        [
            str(evidence_dir / "target_emx_wideband_rerun_summary.json"),
            str(evidence_dir / "target_emx_wideband_rerun.commands.sh"),
            str(evidence_dir / "target_emx_wideband_frequency_grid.csv"),
        ],
        (
            "Command recovered from target summary.json and prepared for 5-50 GHz / 0.1 GHz / 451-point EMX rerun; this is command provenance only, not a validated .s4p."
            if ok
            else "Target EMX wideband rerun command evidence is missing or does not match the required 451-point grid."
        ),
        "Run the command on MARS, then pass the generated .s4p through build_emx_first_validation_gate.py before ADS/HFSS comparison.",
    )


def _target_emx_postrun_validation_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    evidence_dir = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
    )
    checks = summary.get("checks", []) if isinstance(summary, dict) else []
    failed = [check for check in checks if check.get("status") == "FAIL"]
    command_path = evidence_dir / "target_emx_wideband_postrun_validation.commands.sh"
    missing_fragments = _missing_file_fragments(command_path, TARGET_EMX_POSTRUN_REQUIRED_FRAGMENTS)
    ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "READY_FOR_MARS_POSTRUN_VALIDATION"
        and not failed
        and not missing_fragments
    )
    return MatrixItem(
        "Target sample EMX post-run validation command is traceable",
        "PASS" if ok else "PENDING",
        [
            str(evidence_dir / "target_emx_wideband_postrun_validation_summary.json"),
            str(command_path),
            str(evidence_dir / "target_emx_wideband_postrun_validation_report.md"),
        ],
        (
            "Post-run command is prepared to require a real EMX .s4p, record SHA256, enforce 5-50 GHz / 0.1 GHz / 451-point Touchstone physical gate with nonzero coupling, run the EMX-first ADS-photo plus ADS no-extrapolation/physical/smooth window gate, and package validation evidence."
            if ok
            else (
                "Target EMX post-run validation command evidence is missing, failed, or lacks required gate fragments: "
                + (", ".join(missing_fragments[:8]) if missing_fragments else "summary/decision/checks not PASS")
            )
        ),
        "After target_emx_wideband_rerun.commands.sh finishes on MARS, run target_emx_wideband_postrun_validation.commands.sh; only then move to ADS plotting or HFSS rebuild.",
    )


def _hfss_emx_wideband_item() -> MatrixItem:
    return MatrixItem(
        "5-50 GHz EMX labels are correlated against sampled HFSS cases",
        "PENDING",
        [],
        "No completed 5-50 GHz EMX wideband dataset or sampled HFSS correlation is local yet.",
        "After the wideband 500 pilot, randomly sample cases and compare HFSS/ADS curves against EMX with the same 5% gate.",
    )


def _hfss_emx_strict_compare_contract_item(
    project_root: Path, delivery_audit: dict[str, Any], handoff_verify: dict[str, Any]
) -> MatrixItem:
    repo_root = project_root / "rfic-transformer-inverse-design"
    compare_script = repo_root / "scripts" / "compare_emx_hfss_ads.py"
    root_runbook = project_root / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    handoff_runbook = project_root / "mars_handoff_bundle_20260613" / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    delivery_summary_path = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    handoff_summary_path = project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json"

    script_ok = _file_contains_all(compare_script, COMPARE_SCRIPT_REQUIRED_FLAGS)
    root_runbook_ok = _file_contains_all(root_runbook, COMPARE_GATE_REQUIRED_FRAGMENTS)
    handoff_runbook_ok = _file_contains_all(handoff_runbook, COMPARE_GATE_REQUIRED_FRAGMENTS)
    delivery_checks = {item.get("name"): item.get("status") for item in delivery_audit.get("checks", [])}
    handoff_checks = {item.get("name"): item.get("status") for item in handoff_verify.get("checks", [])}
    delivery_ok = (
        delivery_checks.get("MARS handoff HFSS/EMX compare grid gate") == "PASS"
        and delivery_checks.get("MARS handoff extracted HFSS/EMX compare grid gate") == "PASS"
    )
    handoff_ok = handoff_checks.get("HFSS/EMX compare grid gate") == "PASS"
    ok = script_ok and root_runbook_ok and handoff_runbook_ok and delivery_ok and handoff_ok

    return MatrixItem(
        "Strict HFSS/EMX wideband compare gate is enforced in handoff and delivery audits",
        "PASS" if ok else "PENDING",
        [
            str(compare_script),
            str(root_runbook),
            str(handoff_runbook),
            str(delivery_summary_path),
            str(handoff_summary_path),
        ],
        (
            "Compare script supports strict frequency-grid checks plus explicit ADS no-extrapolation coverage evidence; root/handoff runbooks require 5-50 GHz, 0.1 GHz, 451 points, matching grid, and 5% max error; handoff and delivery audits both PASS the contract."
            if ok
            else "Strict compare contract is missing from the script, root runbook, handoff runbook, handoff verifier, or delivery audit."
        ),
        "After MARS wideband data exists, run the documented compare command on randomly sampled HFSS/ADS reconstructions; this row proves the gate is wired, not that the sampled physics validation is complete.",
    )


def _accepted_emx_hfss_final_runner_contract_item(
    project_root: Path, delivery_audit: dict[str, Any], handoff_verify: dict[str, Any]
) -> MatrixItem:
    repo_root = project_root / "rfic-transformer-inverse-design"
    script = repo_root / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
    tests = repo_root / "tests" / "test_run_accepted_emx_hfss_ads_validation_script.py"
    delivery_summary_path = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    handoff_summary_path = project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json"
    script_ok = _file_contains_all(script, ACCEPTED_EMX_HFSS_RUNNER_REQUIRED_FRAGMENTS)
    tests_ok = _file_contains_all(
        tests,
        (
            "test_rejects_accepted_import_summary_without_verifier_evidence",
            "test_hfss_audit_command_requires_nonzero_coupling_gate",
            "test_compare_checks_reject_metric_error_over_gate_even_when_status_pass",
            "test_compare_checks_reject_relaxed_compare_criterion",
            "test_compare_checks_reject_mismatched_summary_sources",
            "test_rejects_missing_hfss_geometry_summary_for_final_traceability",
            "review-only",
        ),
    )
    delivery_checks = {item.get("name"): item.get("status") for item in delivery_audit.get("checks", [])}
    handoff_checks = {item.get("name"): item.get("status") for item in handoff_verify.get("checks", [])}
    delivery_ok = (
        delivery_checks.get("accepted EMX/HFSS final runner contract") == "PASS"
        and delivery_checks.get("MARS handoff accepted EMX/HFSS final runner contract") == "PASS"
        and delivery_checks.get("MARS handoff extracted accepted EMX/HFSS final runner contract") == "PASS"
    )
    handoff_ok = handoff_checks.get("accepted EMX/HFSS final runner contract") == "PASS"
    ok = script_ok and tests_ok and delivery_ok and handoff_ok
    return MatrixItem(
        "Accepted-EMX HFSS/ADS final runner enforces EMX verifier evidence and nonzero coupling",
        "PASS" if ok else "PENDING",
        [
            str(script),
            str(tests),
            str(delivery_summary_path),
            str(handoff_summary_path),
        ],
        (
            "Final runner source requires accepted EMX post-run verifier evidence and accepted_emx_reference_bundle.status=READY_FOR_HFSS before HFSS comparison, requires PASS HFSS geometry asset audit evidence for model traceability, stops before plotting if EMX is not accepted, passes min/max |K| gates into HFSS Touchstone audit, verifies compare-summary EMX/HFSS source traceability, refuses relaxed compare criteria, enforces numeric K/Qp/Qs/Lp/Ls max errors at or below 5%, records the ADS/Python formula note including the ADS Data Display Zp/Zs/Zm equation template and ADS no-extrapolation coverage, validates finite 451-point ADS-style plot_data before plotting, validates generated ADS-style PNG figures as decodable, sufficiently large, and nonblank, treats skipped HFSS .s4p audit as review-only/DO_NOT_USE, and delivery/handoff audits both enforce the contract."
            if ok
            else "Final runner contract is missing from source, tests, delivery audit, or MARS handoff verifier."
        ),
        "Use this only after verify_target_emx_postrun_package.py returns ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS; this row proves the final runner is wired, not that HFSS/ADS validation has completed.",
    )


def _accepted_final_figure_verifier_contract_item(
    project_root: Path, delivery_audit: dict[str, Any], handoff_verify: dict[str, Any]
) -> MatrixItem:
    repo_root = project_root / "rfic-transformer-inverse-design"
    script = repo_root / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
    tests = repo_root / "tests" / "test_verify_accepted_emx_hfss_ads_figures_script.py"
    root_runbook = project_root / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    handoff_runbook = project_root / "mars_handoff_bundle_20260613" / "project_runbook" / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"
    delivery_summary_path = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    handoff_summary_path = project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json"
    script_ok = _file_contains_all(script, ACCEPTED_FIGURE_VERIFIER_REQUIRED_FRAGMENTS)
    tests_ok = _file_contains_all(
        tests,
        (
            "test_accepts_complete_final_figure_evidence",
            "test_rejects_metric_error_over_gate",
            "test_rejects_missing_plot_data_and_blank_png",
        ),
    )
    root_runbook_ok = _file_contains_all(root_runbook, FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS)
    handoff_runbook_ok = _file_contains_all(handoff_runbook, FINAL_FIGURE_VERIFIER_RUNBOOK_REQUIRED_FRAGMENTS)
    delivery_checks = {item.get("name"): item.get("status") for item in delivery_audit.get("checks", [])}
    handoff_checks = {item.get("name"): item.get("status") for item in handoff_verify.get("checks", [])}
    delivery_ok = (
        delivery_checks.get("accepted final figure verifier contract") == "PASS"
        and delivery_checks.get("MARS handoff accepted final figure verifier contract") == "PASS"
        and delivery_checks.get("MARS handoff extracted accepted final figure verifier contract") == "PASS"
        and delivery_checks.get("MARS handoff accepted final figure verifier runbook contract") == "PASS"
        and delivery_checks.get("MARS handoff extracted accepted final figure verifier runbook contract") == "PASS"
    )
    handoff_ok = (
        handoff_checks.get("accepted final figure verifier contract") == "PASS"
        and handoff_checks.get("accepted final figure verifier runbook contract") == "PASS"
    )
    ok = script_ok and tests_ok and root_runbook_ok and handoff_runbook_ok and delivery_ok and handoff_ok
    return MatrixItem(
        "Accepted final Lp/Ls/Q/K figure verifier enforces plot_data, no-extrapolation, <=5%, PNG sanity, and 15 GHz marker table",
        "PASS" if ok else "PENDING",
        [str(script), str(tests), str(root_runbook), str(handoff_runbook), str(delivery_summary_path), str(handoff_summary_path)],
        (
            "Final figure verifier contract is wired: it only accepts ACCEPT_HFSS_VALIDATION_SAMPLE summaries, verifies 5-50 GHz / 0.1 GHz / 451-point no-extrapolation plot_data for K/Qp/Qs/Lp/Ls, enforces max_percent_error <= 5%, rejects missing or blank EMX/HFSS/overlay PNG figures, verifies the 15 GHz marker CSV/Markdown against the same plot_data, and requires the ADS/Python formula note to preserve Touchstone 2.1/reference-impedance handling, recorded port pairing, the ADS Data Display Zp/Zs/Zm equation template, M=imag(Zdiff[2,1])/omega, and Qp/Qs extraction before final report use; delivery and handoff audits both enforce the contract."
            if ok
            else "Accepted final figure verifier contract or runbook gate is missing from source, tests, delivery audit, or MARS handoff verifier."
        ),
        "Run verify_accepted_emx_hfss_ads_figures.py only after run_accepted_emx_hfss_ads_validation.py returns ACCEPT_HFSS_VALIDATION_SAMPLE; until then final Lp/Ls/Q/K figures remain blocked.",
    )


def _validation_chain_decision_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    script = project_root / "rfic-transformer-inverse-design" / "scripts" / "build_validation_chain_decision_card.py"
    summary_path = project_root / "validation_chain_decision_20260614" / "validation_chain_decision_summary.json"
    report_path = project_root / "validation_chain_decision_20260614" / "validation_chain_decision_report.md"
    stages = {stage.get("name"): stage for stage in summary.get("stages", [])}
    required_stage_names = (
        "EMX-first golden reference",
        "HFSS geometry asset traceability",
        "HFSS physical S4P gate",
        "Accepted EMX-vs-HFSS/ADS comparison",
    )
    stage_names_ok = all(name in stages for name in required_stage_names)
    current_block_ok = (
        summary.get("overall_status") == "BLOCKED_BY_EMX_REFERENCE"
        and summary.get("decision") == "DO_NOT_USE_HFSS_COMPARISON"
        and stages.get("EMX-first golden reference", {}).get("status") == "FAIL"
        and stages.get("HFSS geometry asset traceability", {}).get("status") == "PASS_DIAGNOSTIC_ONLY"
        and stages.get("HFSS physical S4P gate", {}).get("status") == "PASS_DIAGNOSTIC_ONLY"
        and stages.get("Accepted EMX-vs-HFSS/ADS comparison", {}).get("status") == "BLOCKED_BY_EMX_REFERENCE"
    )
    full_pass_ok = (
        summary.get("overall_status") == "PASS"
        and summary.get("decision") == "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN"
        and all(stage.get("status") == "PASS" for stage in stages.values())
    )
    script_ok = _file_contains_all(
        script,
        (
            "BLOCKED_BY_EMX_REFERENCE",
            "PASS_DIAGNOSTIC_ONLY",
            "DO_NOT_USE_HFSS_COMPARISON",
            "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN",
            "--hfss-geometry-summary",
            "HFSS geometry asset traceability",
            "BLOCKED_BY_HFSS_GEOMETRY_GATE",
            "WAIT_FOR_HFSS_GEOMETRY_AUDIT",
            "ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS",
            "A diagnostic HFSS geometry or physical PASS cannot override",
        ),
    )
    ok = script_ok and stage_names_ok and (current_block_ok or full_pass_ok)
    if ok and current_block_ok:
        finding = (
            "Validation-chain decision is generated and conservative: current EMX-first FAIL blocks final comparison, "
            "while HFSS geometry and physical PASS evidence are explicitly diagnostic-only."
        )
        next_action = "Keep citing this gate until a regenerated EMX reference passes; then rerun the same script to unlock final comparison."
    elif ok:
        finding = "Validation-chain decision accepts all four stages only after EMX-first, HFSS geometry, HFSS physical, and final comparison PASS."
        next_action = "Use the accepted validation-chain report as the single-sample EMX/HFSS/ADS evidence boundary."
    else:
        finding = "Validation-chain decision evidence is missing, stale, or no longer records the EMX-first/HFSS-geometry/HFSS-physical/final-comparison stages conservatively."
        next_action = "Run build_validation_chain_decision_card.py and ensure the report blocks HFSS comparison whenever EMX-first, HFSS geometry, or HFSS physical gates fail."
    return MatrixItem(
        "EMX/HFSS/ADS validation-chain decision gate is generated and conservative",
        "PASS" if ok else "PENDING",
        [str(script), str(summary_path), str(report_path)],
        finding,
        next_action,
    )


def _ads_metric_formula_consistency_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    script = project_root / "rfic-transformer-inverse-design" / "scripts" / "audit_ads_metric_formula_consistency.py"
    tests = project_root / "rfic-transformer-inverse-design" / "tests" / "test_audit_ads_metric_formula_consistency_script.py"
    evidence_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "ads_metric_formula_consistency_20260614"
    summary_path = evidence_dir / "ads_metric_formula_consistency_summary.json"
    report_path = evidence_dir / "ads_metric_formula_consistency_report.md"
    plot_path = evidence_dir / "ads_metric_formula_consistency_curves.png"
    template_path = evidence_dir / "ADS_DATA_DISPLAY_LP_LS_Q_K_TEMPLATE.md"
    freq = summary.get("frequency_ghz", {})
    recovery = summary.get("metric_recovery_errors", {})
    worst_metric, worst_error = _worst_percent_error(recovery)
    ok = (
        script.exists()
        and tests.exists()
        and template_path.exists()
        and summary.get("overall_status") == "PASS"
        and summary.get("decision") == "ADS_FORMULA_IMPLEMENTATION_ACCEPTED"
        and float(freq.get("start", 0.0)) == 5.0
        and float(freq.get("stop", 0.0)) == 50.0
        and float(freq.get("step", 0.0)) == 0.1
        and int(freq.get("points", 0)) == 451
        and _summary_checks_pass(
            summary,
            (
                "helper formula equals direct ADS expression",
                "known transformer metric recovery",
                "formula audit frequency grid",
                "ADS Data Display equation template",
            ),
        )
        and worst_error <= 1.0e-6
    )
    return MatrixItem(
        "ADS-style Lp/Ls/M/K/Q extraction formulas are self-checked on a known transformer",
        "PASS" if ok else "PENDING",
        [str(script), str(tests), str(summary_path), str(report_path), str(plot_path), str(template_path)],
        (
            f"Formula audit PASS on a synthetic known coupled transformer over 5-50 GHz / 0.1 GHz / 451 points; direct ADS single-ended expressions match the shared differential helper, the ADS Data Display template is present, and worst metric recovery is {worst_metric}={worst_error:.4g}%."
            if ok
            else "ADS metric formula consistency evidence is missing/failing, lacks the ADS Data Display template, is not on the required 451-point grid, or the known-transformer recovery error is above the numerical tolerance."
        ),
        "Keep this as a formula-only prerequisite; it proves extraction math, not that any EMX or HFSS simulator file is valid.",
    )


def _mars_next_action_packet_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    script = project_root / "rfic-transformer-inverse-design" / "scripts" / "build_mars_next_action_packet.py"
    summary_path = project_root / "mars_next_action_packet_20260614" / "mars_next_action_packet_summary.json"
    report_path = project_root / "mars_next_action_packet_20260614" / "MARS_NEXT_ACTION_PACKET_20260614_CN.md"
    decision = summary.get("decision")
    guardrails = "\n".join(str(item) for item in summary.get("guardrails", []))
    local_import_requirements = "\n".join(str(item) for item in summary.get("local_postrun_import_requirements", []))
    status_counts = summary.get("status_counts", {})
    ok = (
        script.exists()
        and summary.get("overall_status") == "PASS"
        and decision in {"READY_FOR_MARS_TARGET_EMX_RERUN", "VALIDATION_CHAIN_ALREADY_ACCEPTED"}
        and "Do not run HFSS comparison until EMX-first accepts" in guardrails
        and "approved pair 1,2:3,4 PASS" in local_import_requirements
        and "max_percent_error <= 5%" in local_import_requirements
    )
    if ok and decision == "READY_FOR_MARS_TARGET_EMX_RERUN":
        finding = (
            "MARS next-action packet PASS: current EMX remains blocked, the next safe action is the target 5-50 GHz / 0.1 GHz EMX rerun plus post-run validation, and the packet explicitly keeps HFSS comparison blocked until EMX-first accepts while requiring the local approved port-pair CSV gate before import."
        )
        next_action = "Use the packet as the first runbook before MARS work; after the generated EMX .s4p passes post-run validation, rebuild the validation-chain decision."
    elif ok:
        finding = (
            "MARS next-action packet PASS: validation-chain is already accepted, and the packet records this as the only allowed transition beyond rerun preparation."
        )
        next_action = "Keep the packet with the accepted evidence bundle so the MARS rerun/validation provenance remains auditable."
    else:
        finding = (
            f"MARS next-action packet is missing or not conservative enough: overall={summary.get('overall_status')}, "
            f"decision={decision}, counts={status_counts}."
        )
        next_action = "Run build_mars_next_action_packet.py after validation-chain, handoff verifier, report manifest, and acceptance-matrix artifacts are refreshed."
    return MatrixItem(
        "MARS next-action packet is generated and keeps EMX-first as the next gate",
        "PASS" if ok else "PENDING",
        [str(script), str(summary_path), str(report_path)],
        finding,
        next_action,
    )


def _geometry_item(project_root: Path, summary: dict[str, Any], target_summary: dict[str, Any]) -> MatrixItem:
    layout = summary.get("layout_counts", {})
    layout_ok = (
        summary.get("overall_status") == "PASS"
        and layout.get("port_count") == 4
        and layout.get("cadence_pin_purpose") == 51
        and layout.get("signal_labeled_port_count") == 4
        and layout.get("grounded_port_count") == 4
        and layout.get("internal_signal_labeled_port_count") == 4
        and layout.get("internal_ground_labeled_port_count") == 4
    )
    manifest_counts = summary.get("manifest_counts", {})
    manifest_angle_ok = (
        int(manifest_counts.get("angle_checked_count", 0) or 0) > 0
        and int(manifest_counts.get("geometry_check_ok_count", 0) or 0) == int(manifest_counts.get("geometry_check_count", -1) or -1)
    )
    audit_angle_ok = _geometry_audit_angle_ok(summary)
    target_angle_ok = _target_summary_angle_ok(target_summary)
    ok = layout_ok and (manifest_angle_ok or audit_angle_ok or target_angle_ok)
    status = "PASS" if ok else ("PARTIAL" if layout_ok else "PENDING")
    evidence = [
        "/home/researcher/Documents/模拟变压器AI反向建模/hfss_validation/final500_ec6698dfc575950b/geometry_quality_audit_final500_selected_20260613/geometry_quality_audit_summary.json",
        str(project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "summary.json"),
    ]
    if ok and target_angle_ok:
        finding = "Selected final500 layout/clearance audit PASS with P001-P004 signal/ground/internal labels, and target summary geometry_check proves 135 deg octagon internals plus 90 deg terminal interfaces."
    elif ok and audit_angle_ok:
        finding = "Selected final500 layout/clearance audit PASS with P001-P004 signal/ground/internal labels and embedded summary-angle checks for 135 deg internals plus 90 deg terminal interfaces."
    elif ok:
        finding = "Selected final500 layout/clearance audit PASS with complete P001-P004 labels, and manifest geometry_quality includes passing angle evidence."
    elif layout_ok:
        finding = "Selected final500 layout/clearance audit PASS, but no manifest or target-summary angle evidence was available; do not claim manufacturable angles from this row alone."
    else:
        finding = "Geometry audit is missing or not PASS."
    return MatrixItem(
        "Geometry gate enforces grounded shield ports, pin 51, and manufacturable angles",
        status,
        evidence,
        finding,
        "Apply the same geometry audit to the pulled final-500 and wideband pilot manifests.",
    )


def _target_summary_angle_ok(summary: dict[str, Any]) -> bool:
    check = summary.get("geometry_check", {})
    metrics = check.get("metrics", {}) if isinstance(check, dict) else {}
    if not isinstance(metrics, dict):
        return False
    if check.get("ok") is not True and list(check.get("errors") or []):
        return False
    for prefix in ("primary", "secondary"):
        if int(metrics.get(f"{prefix}_winding_centerline_internal_turn_count", 0) or 0) <= 0:
            return False
        if int(metrics.get(f"{prefix}_winding_centerline_terminal_interface_count", 0) or 0) <= 0:
            return False
        if not _angle_summary_exact(metrics, f"{prefix}_winding_centerline_min_internal_angle_deg", 135.0):
            return False
        if not _angle_summary_exact(metrics, f"{prefix}_winding_centerline_max_internal_angle_deg", 135.0):
            return False
        if not _angle_summary_exact(metrics, f"{prefix}_winding_centerline_min_terminal_angle_deg", 90.0):
            return False
        if not _angle_summary_exact(metrics, f"{prefix}_winding_centerline_max_terminal_angle_deg", 90.0):
            return False
    return True


def _geometry_audit_angle_ok(summary: dict[str, Any]) -> bool:
    required = {
        "target summary geometry errors",
        "target summary primary internal winding angles",
        "target summary primary terminal interface angles",
        "target summary primary diagonal segment count",
        "target summary secondary internal winding angles",
        "target summary secondary terminal interface angles",
        "target summary secondary diagonal segment count",
    }
    by_name = {str(item.get("name")): item for item in summary.get("checks", [])}
    return all(by_name.get(name, {}).get("status") == "PASS" for name in required)


def _angle_summary_exact(metrics: dict[str, Any], key: str, expected: float) -> bool:
    try:
        return abs(float(metrics.get(key)) - float(expected)) <= 1.0e-6
    except (TypeError, ValueError):
        return False


def _clearance_item(summary: dict[str, Any]) -> MatrixItem:
    ok = (
        int(summary.get("record_count", 0)) == 500
        and int(summary.get("pass_count", 0)) == 468
        and int(summary.get("reject_count", 0)) == 32
        and str(summary.get("selected_status", "")).startswith("pass")
    )
    return MatrixItem(
        "Signal-to-ground/shield clearance is audited with real reject examples",
        "PASS" if ok else "PENDING",
        ["/home/researcher/Documents/模拟变压器AI反向建模/final500_clearance_audit_visuals_20260613/clearance_audit_visual_summary.json"],
        "500 candidate records audited: 468 pass, 32 reject; selected sample passes clearance." if ok else "Clearance audit counts are missing or unexpected.",
        "Run this audit on future pulled datasets; reject touching/too-close signal-to-ground cases before training.",
    )


def _zin_coverage_item(summary: dict[str, Any]) -> MatrixItem:
    status = summary.get("overall_status")
    evidence = [
        "/home/researcher/Documents/模拟变压器AI反向建模/literature_improvement_create_only_50/zin_coverage_audit_20260613/zin_coverage_audit_summary.json",
        "/home/researcher/Documents/模拟变压器AI反向建模/literature_improvement_create_only_50/zin_coverage_audit_20260613/zin_coverage_audit_report.md",
    ]
    if status == "PASS":
        matrix_status = "PASS"
        finding = "Zin labels exist and pass configured coverage gates, including target-envelope gates if they were configured."
        next_action = "Use the generated Zin scatter/histogram/target-envelope figures in the report and keep the same gates for wideband datasets."
    elif status == "NOT_READY":
        matrix_status = "PENDING"
        finding = "Current create-only evidence has no real Zin labels; Zin coverage cannot be claimed yet; target-envelope Re/Im Zin gates are available but not applicable until real labels and project bounds exist."
        next_action = "Run audit_zin_coverage.py on pulled final-500/wideband 500 response labels; if target Re/Im Zin bounds are defined, require fixed target-envelope bin/area/outside-fraction gates."
    else:
        matrix_status = "PENDING"
        finding = "No passing Zin coverage audit is available locally."
        next_action = "Generate real EM/Zin labels, then run audit_zin_coverage.py with project count/span/bin thresholds and any professor/project target-envelope gates."
    return MatrixItem(
        "Zin coverage spans enough impedance space for inverse training",
        matrix_status,
        evidence,
        finding,
        next_action,
    )


def _response_feature_extractor_item(project_root: Path, summary: dict[str, Any], min500_summary: dict[str, Any]) -> MatrixItem:
    script = project_root / "rfic-transformer-inverse-design" / "scripts" / "extract_touchstone_response_features.py"
    demo_dir = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "response_feature_extraction_package_demo_20260613"
    evidence = [
        str(script),
        str(demo_dir / "response_feature_extraction_summary.json"),
        str(demo_dir / "response_features.csv"),
        str(demo_dir / "zin_coverage_audit_min500_20260613" / "zin_coverage_audit_summary.json"),
    ]
    demo_ok = summary.get("overall_status") == "PASS" and int(summary.get("counts", {}).get("ok_rows", 0)) > 0
    min500_gate_is_conservative = min500_summary.get("overall_status") == "FAIL"
    ok = script.exists() and demo_ok and min500_gate_is_conservative
    return MatrixItem(
        "Touchstone response feature extractor can generate real Zin labels",
        "PASS" if ok else "PENDING",
        evidence,
        (
            "Extractor produced real Zin/K/Q/L labels from existing .s4p files, and the min-500 Zin gate fails on the small demo as expected."
            if ok
            else "Response-feature extractor script or conservative demo evidence is missing."
        ),
        "Run this extractor on the pulled final-500 and wideband 500 datasets, then feed its output to audit_zin_coverage.py.",
    )


def _create_only_distribution_item(project_root: Path) -> MatrixItem:
    dashboard = project_root / "literature_improvement_create_only_50" / "dataset_visualizations_20260613_evidence" / "13_dataset_dashboard.png"
    sampling_summary = project_root / "literature_improvement_create_only_50" / "sampling_distribution_audit_20260613" / "sampling_distribution_audit_summary.json"
    space_plot = project_root / "literature_improvement_create_only_50" / "sampling_distribution_audit_20260613" / "sampling_distribution_space_filling_strata.png"
    evidence = [str(path) for path in (dashboard, sampling_summary, space_plot) if path.exists()]
    status = "PRELIMINARY" if dashboard.exists() and sampling_summary.exists() and space_plot.exists() else "PENDING"
    return MatrixItem(
        "Input sampling has preliminary uniform/LHS and space-filling visual evidence",
        status,
        evidence,
        (
            "Create-only 50 visualization plus sampling audit exist, including uniform-vs-normal, boundary, no-duplicate vector, and strata coverage evidence; it still has no EM/Zin labels and is not training-ready."
            if status == "PRELIMINARY"
            else "Create-only visualization or sampling audit evidence is missing."
        ),
        "Use only as sampling/geometry evidence; generate real EM/Zin label visualizations and space-filling audits after MARS pull/pilot.",
    )


def _final500_pull_item(project_root: Path) -> MatrixItem:
    local_run = project_root / "mars_dataset500_quality_grounded_sync_20260611_0409"
    ready = (local_run / "dataset_manifest.json").exists() and (local_run / "dataset_rows.csv").exists()
    return MatrixItem(
        "Complete final-500 MARS run is pulled locally and ready for strict gates",
        "PASS" if ready else "PENDING",
        [str(local_run)],
        "Local final-500 run manifest and rows are present." if ready else "Known MARS final-500 run is not locally unpacked in the expected location.",
        "Use audit_mars_run_progress.py or watch_mars_run_progress.py on MARS, package with inventory/SHA256, then unpack locally and run run_dataset_quality_gates.py.",
    )


def _wideband_config_item(preflight: dict[str, Any], strict: dict[str, Any]) -> MatrixItem:
    logic_ok = preflight.get("overall_status") == "PASS"
    strict_ok = strict.get("overall_status") == "PASS"
    status = "PASS" if logic_ok and strict_ok else ("PARTIAL" if logic_ok else "PENDING")
    return MatrixItem(
        "Wideband 500 pilot config is ready for MARS execution",
        status,
        [
            "/home/researcher/Documents/模拟变压器AI反向建模/mars_dataset_500_wideband_20260613_preflight.json",
            "/home/researcher/Documents/模拟变压器AI反向建模/mars_dataset_500_wideband_20260613_preflight_strict_paths.json",
        ],
        "Frequency/port/pin/shield logic passes, but strict EMX/Cadence paths still fail." if logic_ok and not strict_ok else ("Strict path preflight passes." if strict_ok else "Wideband config preflight is not PASS."),
        "Run discover_mars_emx_cadence_paths.py on MARS to collect candidate paths, review the generated patch command, patch real paths with patch_mars_config_paths.py, then rerun preflight_dataset_config.py --check-emx-paths.",
    )


def _mars_helpers_item(repo_root: Path) -> MatrixItem:
    scripts = [
        repo_root / "scripts" / "build_mars_handoff_bundle.py",
        repo_root / "scripts" / "verify_mars_handoff_install.py",
        repo_root / "scripts" / "package_mars_dataset_run.py",
        repo_root / "scripts" / "verify_mars_dataset_package.py",
        repo_root / "scripts" / "audit_mars_run_progress.py",
        repo_root / "scripts" / "watch_mars_run_progress.py",
        repo_root / "scripts" / "discover_and_verify_mars_emx_return.py",
        repo_root / "scripts" / "watch_mars_emx_return.py",
        repo_root / "scripts" / "discover_mars_emx_cadence_paths.py",
        repo_root / "scripts" / "patch_mars_config_paths.py",
        repo_root / "scripts" / "preflight_dataset_config.py",
        repo_root / "scripts" / "prepare_mars_wideband_config.py",
        repo_root / "scripts" / "prepare_target_emx_wideband_rerun.py",
        repo_root / "scripts" / "prepare_target_emx_postrun_validation.py",
        repo_root / "scripts" / "backfill_ground_clearance_audit.py",
        repo_root / "scripts" / "run_dataset_quality_gates.py",
        repo_root / "scripts" / "audit_sampling_distribution.py",
        repo_root / "scripts" / "extract_touchstone_response_features.py",
        repo_root / "scripts" / "audit_response_feature_coverage.py",
        repo_root / "scripts" / "audit_zin_coverage.py",
        repo_root / "scripts" / "select_hfss_validation_samples.py",
        repo_root / "scripts" / "run_hfss_emx_validation_batch.py",
        repo_root / "scripts" / "run_accepted_emx_hfss_ads_validation.py",
        repo_root / "scripts" / "verify_accepted_emx_hfss_ads_figures.py",
        repo_root / "scripts" / "audit_hfss_model_geometry_assets.py",
        repo_root / "scripts" / "audit_248k_launch_readiness.py",
        repo_root / "scripts" / "build_mars_next_action_packet.py",
        repo_root / "scripts" / "audit_ads_photo_reference_alignment.py",
        repo_root / "scripts" / "build_emx_first_validation_gate.py",
        repo_root / "scripts" / "audit_photo_matched_vs_target_geometry.py",
        repo_root / "scripts" / "scan_s4p_ads_photo_reference_candidates.py",
        repo_root / "scripts" / "build_photo_matched_hfss_reference_evidence.py",
        repo_root / "scripts" / "plot_emx_hfss_ads_style_metrics.py",
        repo_root / "scripts" / "diagnose_cm_mismatch.py",
        repo_root / "scripts" / "verify_target_emx_postrun_package.py",
    ]
    project_root = repo_root.parent
    handoff_command_path = project_root / "mars_handoff_bundle_20260613" / "project_runbook" / "mars_dataset_500_wideband_20260613.commands.sh"
    handoff_files = [
        project_root / "mars_handoff_bundle_20260613.tar.gz",
        project_root / "mars_handoff_bundle_20260613.tar.gz.sha256",
        project_root / "mars_handoff_bundle_20260613" / "MARS_HANDOFF_INVENTORY_20260613.json",
        project_root / "mars_handoff_bundle_20260613" / "SHA256SUMS.txt",
        project_root / "mars_handoff_bundle_20260613" / "configs" / "mars_dataset_500_wideband_20260613.yaml",
        handoff_command_path,
        project_root
        / "mars_handoff_bundle_20260613"
        / "project_runbook"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_rerun.commands.sh",
        project_root
        / "mars_handoff_bundle_20260613"
        / "project_runbook"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_postrun_validation.commands.sh",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "backfill_ground_clearance_audit.py",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "discover_and_verify_mars_emx_return.py",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "watch_mars_emx_return.py",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "discover_mars_emx_cadence_paths.py",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "prepare_target_emx_wideband_rerun.py",
        project_root / "mars_handoff_bundle_20260613" / "scripts" / "prepare_target_emx_postrun_validation.py",
        project_root / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "dataset.py",
        project_root / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "execution" / "evaluator.py",
        project_root / "mars_handoff_bundle_20260613" / "rfic_transformer_inverse_design" / "layout" / "export.py",
    ]
    portable_ok = _portable_handoff_commands_ok(handoff_command_path)
    discovery_script = repo_root / "scripts" / "discover_mars_emx_cadence_paths.py"
    path_discovery_hint_ok = _file_contains_all(
        discovery_script,
        (
            "--hint-command",
            "DEFAULT_HINT_COMMANDS",
            "target_emx_wideband_rerun.commands.sh",
            "hint_command_files",
            "hint-command:",
        ),
    )
    ok = all(path.exists() for path in scripts) and all(path.exists() for path in handoff_files) and portable_ok and path_discovery_hint_ok
    return MatrixItem(
        "MARS pull, progress, path, local gate, and handoff helpers exist",
        "PASS" if ok else "PENDING",
        [str(path) for path in scripts + handoff_files],
        (
            "All helper scripts plus the MARS handoff tarball, SHA file, inventory, portable wideband config, MARS-relative commands, local returned-EMX discovery/import gate, clearance backfill script, automatic clearance-audit source files, and target-rerun-command path-hint discovery are present."
            if ok
            else "One or more helper scripts/handoff files are missing, the handoff commands still contain non-portable local paths, or path discovery no longer mines target-rerun command hints."
        ),
        "Copy this handoff bundle to MARS, verify SHA256SUMS there, run discover_mars_emx_cadence_paths.py with target-rerun command hints to prepare path patching, run strict preflight, package completed runs, then use discover_and_verify_mars_emx_return.py/verify_target_emx_postrun_package.py on the downloaded target EMX files before local HFSS gates.",
    )


def _check_status_names(summary: dict[str, Any], status: str) -> list[str]:
    return [
        str(item.get("name", "unnamed check"))
        for item in summary.get("checks", [])
        if item.get("status") == status
    ]


def _production_248k_item(project_root: Path, readiness: dict[str, Any]) -> MatrixItem:
    candidates = list(project_root.glob("mars_dataset248k*")) + list(project_root.glob("dataset248k*"))
    readiness_summary = project_root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_summary.json"
    readiness_report = project_root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_report.md"
    evidence = [str(path) for path in candidates]
    if readiness_summary.exists():
        evidence.append(str(readiness_summary))
    if readiness_report.exists():
        evidence.append(str(readiness_report))
    readiness_status = readiness.get("overall_status", "MISSING" if not readiness_summary.exists() else "UNKNOWN")
    blockers = _check_status_names(readiness, "NOT_READY") + _check_status_names(readiness, "FAIL")
    status = "PASS" if candidates and readiness_status == "PASS" else "PENDING"
    if candidates and readiness_status == "PASS":
        finding = "Potential 248k artifacts found locally and launch readiness summary is PASS."
    elif blockers:
        finding = (
            "No completed 248k production dataset is local; launch readiness gate is "
            f"{readiness_status} with blockers: {', '.join(blockers)}."
        )
    else:
        finding = (
            "No completed 248k production dataset is local; launch readiness gate evidence is missing or not PASS."
        )
    return MatrixItem(
        "248k production dataset is generated, audited, and report-ready",
        status,
        evidence,
        finding,
        "Launch production only after audit_248k_launch_readiness.py is PASS, including wideband 500 quality gates and sampled HFSS/ADS-vs-EMX checks with per-record ADS no-extrapolation PASS.",
    )


def _delivery_package_audit_item(project_root: Path, summary: dict[str, Any]) -> MatrixItem:
    summary_path = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    report_path = summary_path.with_name("delivery_package_audit_report.md")
    required_checks = {
        "package SHA manifest",
        "package bytecode/cache hygiene",
        "desktop zip integrity",
        "desktop zip clean metadata",
        "desktop zip bytecode/cache hygiene",
        "desktop zip external SHA",
        "report manifest counts",
        "report assets",
        "report asset usage contract",
        "report local health pytest gate",
        "report html image references",
        "report image nonblank",
        "package selfcheck compare gate",
        "ADS metric formula consistency evidence",
        "validation scripts inventory",
        "validation scripts syntax",
        "local health-check runner contract",
        "target EMX return watcher contract",
        "MARS dataset package helper contract",
        "MARS dataset package verifier contract",
        "HFSS/EMX batch compare runner contract",
        "accepted EMX/HFSS final runner contract",
        "accepted final figure verifier contract",
        "EMX-first gate script contract",
        "target EMX post-run import verifier contract",
        "248k launch readiness contract",
        "MARS handoff package source contract",
            "MARS handoff config contract",
            "MARS handoff target EMX rerun command contract",
            "MARS handoff target EMX post-run validation command contract",
            "MARS handoff path discovery helper contract",
            "MARS handoff target EMX return watcher contract",
            "MARS handoff path patcher smoke",
        "MARS handoff run-progress contract",
        "MARS handoff quality-gate contract",
        "MARS handoff HFSS/EMX compare grid gate",
        "MARS handoff accepted EMX/HFSS final runner contract",
        "MARS handoff accepted final figure verifier contract",
        "MARS handoff accepted final figure verifier runbook contract",
        "MARS handoff EMX-first gate script contract",
        "MARS handoff target EMX post-run import verifier contract",
        "MARS handoff HFSS/EMX batch runner contract",
        "MARS handoff 248k launch readiness contract",
        "MARS handoff tar SHA",
        "MARS handoff tar contents",
        "MARS handoff shape-window gate",
        "MARS handoff extracted package source contract",
            "MARS handoff extracted config contract",
            "MARS handoff extracted target EMX rerun command contract",
            "MARS handoff extracted target EMX post-run validation command contract",
            "MARS handoff extracted path discovery helper contract",
            "MARS handoff extracted target EMX return watcher contract",
            "MARS handoff extracted path patcher smoke",
        "MARS handoff extracted run-progress contract",
        "MARS handoff extracted quality-gate contract",
        "MARS handoff extracted HFSS/EMX compare grid gate",
        "MARS handoff extracted accepted EMX/HFSS final runner contract",
        "MARS handoff extracted accepted final figure verifier contract",
        "MARS handoff extracted accepted final figure verifier runbook contract",
        "MARS handoff extracted EMX-first gate script contract",
        "MARS handoff extracted target EMX post-run import verifier contract",
        "MARS handoff extracted HFSS/EMX batch runner contract",
        "MARS handoff extracted 248k launch readiness contract",
        "MARS handoff extracted shape-window gate",
        "acceptance matrix boundary",
    }
    checks = {item.get("name"): item.get("status") for item in summary.get("checks", [])}
    missing_or_failed = sorted(name for name in required_checks if checks.get(name) != "PASS")
    ok = summary.get("overall_status") == "PASS" and not missing_or_failed
    return MatrixItem(
        "Desktop delivery package passes self-audit",
        "PASS" if ok else "PENDING",
        [str(summary_path), str(report_path)],
        (
            f"Delivery package audit PASS with {len(summary.get('checks', []))} checks, including clean zip metadata, bytecode/cache hygiene, report asset usage contract, report local health full pytest gate, report HTML image references, nonblank report images, narrowband package selfcheck boundary/gate, ADS metric formula consistency evidence, validation scripts inventory/syntax, local health-check watcher contract, returned-EMX watcher contract, MARS dataset package helper/verifier contracts, HFSS/EMX batch runner with source/criterion/core-metric/no-extrapolation records, accepted-EMX final runner contracts with accepted EMX artifact-bundle checks, accepted final-figure script and runbook contracts, EMX-first gate script contracts, target EMX post-run import verifier contracts with finite numeric metrics CSV 5-50 GHz / 451-point grid checks, accepted_emx_reference_bundle.status=READY_FOR_HFSS, approved port-pair CSV gate, and decodable sufficiently large nonblank PNG plot checks, 248k launch readiness contracts with sampled no-extrapolation evidence, MARS handoff package-source/config/target-EMX exact 451-point frequency-list/path-discovery/returned-EMX watcher/path-patcher/run-progress/quality-gate contracts, MARS handoff tar, extracted package-source/config/target-EMX exact 451-point frequency-list/path-discovery/returned-EMX watcher/path-patcher/run-progress/shape-window gates, and acceptance boundary."
            if ok
            else f"Delivery audit is missing, not PASS, or lacks required PASS checks: {missing_or_failed}"
        ),
        "Rerun audit_delivery_package.py after every package/report/handoff update; keep the zip SHA only in the external hash record.",
    )


def _report_asset_usage_item(
    project_root: Path,
    package_dir: Path,
    manifest: dict[str, Any],
    delivery_audit: dict[str, Any],
) -> MatrixItem:
    manifest_path = package_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"
    delivery_summary_path = (
        project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json"
    )
    checks = {item.get("name"): item for item in delivery_audit.get("checks", [])}
    contract_check = checks.get("report asset usage contract", {})
    usage_counts = manifest.get("asset_usage_counts") if isinstance(manifest, dict) else None
    assets = manifest.get("assets", []) if isinstance(manifest, dict) else []
    allowed = {
        "ACCEPTED_FOR_CURRENT_CLAIM",
        "DIAGNOSTIC_ONLY",
        "BLOCKED_AS_FINAL_EVIDENCE",
        "MISSING",
    }
    actual_counts: dict[str, int] = {}
    failures: list[str] = []
    for asset in assets:
        evidence_use = str(asset.get("evidence_use", ""))
        if evidence_use not in allowed:
            failures.append(f"{asset.get('title', 'asset')}: evidence_use={evidence_use or 'missing'}")
            continue
        if not str(asset.get("usage_note", "")).strip():
            failures.append(f"{asset.get('title', 'asset')}: usage_note missing")
        actual_counts[evidence_use] = actual_counts.get(evidence_use, 0) + 1
    normalized_counts = {}
    if isinstance(usage_counts, dict):
        try:
            normalized_counts = {str(key): int(value) for key, value in usage_counts.items()}
        except (TypeError, ValueError):
            failures.append("asset_usage_counts values are not integers")
    else:
        failures.append("asset_usage_counts missing")
    if normalized_counts and normalized_counts != actual_counts:
        failures.append(f"asset_usage_counts mismatch manifest={normalized_counts} actual={actual_counts}")
    blocked_count = actual_counts.get("BLOCKED_AS_FINAL_EVIDENCE", 0)
    diagnostic_count = actual_counts.get("DIAGNOSTIC_ONLY", 0)
    accepted_count = actual_counts.get("ACCEPTED_FOR_CURRENT_CLAIM", 0)
    contract_detail = str(contract_check.get("detail", ""))
    final_boundary_ok = (
        "blocked_final_comparison_assets=" in contract_detail
        and "blocked_final_comparison_assets=0" not in contract_detail
    )
    hfss_boundary_ok = (
        "diagnostic_hfss_standalone_assets=" in contract_detail
        and "diagnostic_hfss_standalone_assets=0" not in contract_detail
    )
    ok = (
        contract_check.get("status") == "PASS"
        and not failures
        and blocked_count > 0
        and diagnostic_count > 0
        and accepted_count > 0
        and final_boundary_ok
        and hfss_boundary_ok
    )
    if ok:
        finding = (
            "Report manifest explicitly classifies visual assets by evidence use: "
            f"{dict(sorted(actual_counts.items()))}. Delivery audit also verifies final-comparison plots remain blocked "
            "while the chain is unaccepted and standalone HFSS plots remain diagnostic."
        )
        next_action = "Keep this contract PASS after every report rebuild; do not remove BLOCKED_AS_FINAL_EVIDENCE labels until EMX-first and final comparison pass."
    else:
        missing_detail = "; ".join(failures[:8]) if failures else (
            f"contract={contract_check.get('status')}, counts={actual_counts}, "
            f"final_boundary_ok={final_boundary_ok}, hfss_boundary_ok={hfss_boundary_ok}"
        )
        finding = f"Report visual asset usage boundaries are missing or incomplete: {missing_detail}."
        next_action = "Rebuild the project validation report and rerun audit_delivery_package.py so each asset has evidence_use and usage_note."
    return MatrixItem(
        "Report visual assets have explicit evidence-use boundaries",
        "PASS" if ok else "PENDING",
        [str(manifest_path), str(delivery_summary_path)],
        finding,
        next_action,
    )


def _package_reproducibility_item(project_root: Path, package_dir: Path, manifest: dict[str, Any]) -> MatrixItem:
    sha_path = package_dir / "SHA256SUMS.txt"
    zip_hash_record = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "FINAL_DESKTOP_PACKAGE_HASH_20260613.txt"
    delivery_audit_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "audit_delivery_package.py"
    clean_zip_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "build_clean_delivery_zip.py"
    health_check_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "run_local_project_health_check.py"
    compare_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "compare_emx_hfss_ads.py"
    accepted_emx_hfss_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "run_accepted_emx_hfss_ads_validation.py"
    accepted_figure_verifier_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "verify_accepted_emx_hfss_ads_figures.py"
    batch_compare_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "run_hfss_emx_validation_batch.py"
    production_readiness_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "audit_248k_launch_readiness.py"
    package_selfcheck_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "run_package_selfcheck_compare.py"
    mars_next_action_script = project_root / "rfic-transformer-inverse-design" / "scripts" / "build_mars_next_action_packet.py"
    evidence = [
        str(sha_path),
        str(zip_hash_record),
        str(package_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613" / "report_manifest.json"),
        str(delivery_audit_script),
        str(clean_zip_script),
        str(health_check_script),
        str(compare_script),
        str(accepted_emx_hfss_script),
        str(accepted_figure_verifier_script),
        str(batch_compare_script),
        str(production_readiness_script),
        str(package_selfcheck_script),
        str(mars_next_action_script),
    ]
    sha_ready = sha_path.exists() and _sha_manifest_has_entries(sha_path)
    asset_count = int(manifest.get("asset_count", 0) or 0)
    status = (
        "PASS"
        if (
            sha_ready
            and asset_count >= 27
            and zip_hash_record.exists()
            and delivery_audit_script.exists()
            and clean_zip_script.exists()
            and health_check_script.exists()
            and compare_script.exists()
            and accepted_emx_hfss_script.exists()
            and accepted_figure_verifier_script.exists()
            and batch_compare_script.exists()
            and production_readiness_script.exists()
            and package_selfcheck_script.exists()
            and mars_next_action_script.exists()
        )
        else "PENDING"
    )
    return MatrixItem(
        "Desktop package is reproducible and hash-tracked",
        status,
        evidence,
        (
            f"Package SHA256SUMS, external zip-hash record, report manifest, delivery audit script, clean zip builder, local health-check runner, single-sample compare script, accepted-EMX HFSS/ADS runner, accepted final-figure verifier, batch HFSS/EMX validation runner, 248k launch readiness gate, narrowband package selfcheck runner, and MARS next-action packet builder exist; report assets={manifest.get('asset_count')}."
            if status == "PASS"
            else "Package SHA manifest, external zip hash, report manifest, delivery audit script, clean zip builder, local health-check runner, compare script, accepted final-figure verifier, batch HFSS/EMX validation runner, 248k launch readiness gate, package selfcheck runner, MARS next-action packet builder, or 27 report assets are missing."
        ),
        "Keep zip hash outside the package to avoid circular hashes; rerun run_local_project_health_check.py --rebuild-delivery-zip after every package update.",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - keep exact evidence parser problem.
        return {"_parse_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"_parse_error": f"top-level JSON is {type(data).__name__}"}


def _worst_percent_error(errors: dict[str, Any]) -> tuple[str, float]:
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


def _sha_manifest_has_entries(sha_path: Path) -> bool:
    if not sha_path.exists():
        return False
    for raw_line in sha_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        digest, _, rel_path = raw_line.partition("  ")
        if digest and rel_path:
            return True
    return False


def _portable_handoff_commands_ok(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return (
        "CONFIG=${1:-configs/mars_dataset_500_wideband_20260613.yaml}" in text
        and "scripts/run_dataset_quality_gates.py" in text
        and "--touchstone-shape-window-start-ghz" in text
        and "--touchstone-shape-window-stop-ghz" in text
        and "--touchstone-max-shape-spike-ratio" in text
        and "--touchstone-max-shape-relative-step" in text
        and "/home/researcher" not in text
    )


def _file_contains_all(path: Path, fragments: tuple[str, ...]) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return all(fragment in text for fragment in fragments)


def _missing_file_fragments(path: Path, fragments: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return [f"missing file: {path}"]
    text = path.read_text(encoding="utf-8")
    return [fragment for fragment in fragments if fragment not in text]


def _summary_checks_pass(summary: dict[str, Any], names: tuple[str, ...]) -> bool:
    by_name = {item.get("name"): item for item in summary.get("checks", [])}
    return all((by_name.get(name) or {}).get("status") == "PASS" for name in names)


def _status_counts(items: list[MatrixItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# RFIC Transformer Acceptance Matrix",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Generated UTC: `{summary['generated_utc']}`",
        f"- Project root: `{summary['project_root']}`",
        "",
        "| Status | Requirement | Finding | Evidence | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in summary["items"]:
        evidence = "<br>".join(f"`{path}`" for path in item["evidence"]) if item["evidence"] else "None local yet"
        lines.append(
            f"| {item['status']} | {item['requirement']} | {item['finding']} | {evidence} | {item['next_action']} |"
        )
    lines.extend(
        [
            "",
            "## Status Meaning",
            "",
            "- `PASS`: current local evidence proves this scoped requirement.",
            "- `PARTIAL` / `PRELIMINARY`: useful evidence exists, but it does not prove the full training-data requirement.",
            "- `PENDING`: required external run, pull, or validation evidence is not present yet.",
            "- `BLOCKED`: progress depends on external access/state, such as MARS SSH/Guacamole.",
            "",
            "Do not present PENDING, PARTIAL, or PRELIMINARY rows as completed work.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
