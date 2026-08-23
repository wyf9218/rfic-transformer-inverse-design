#!/usr/bin/env python3
"""Build a static project-validation report from verified local artifacts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Asset:
    title: str
    source: Path
    filename: str
    caption: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default="/home/researcher/Documents/模拟变压器AI反向建模",
        help="Project evidence root",
    )
    parser.add_argument(
        "--package-dir",
        default="/home/researcher/Desktop/ec6698dfc575950b_s4p_for_ADS_FIXED_20260613",
        help="Desktop package directory with final HFSS/ADS evidence",
    )
    parser.add_argument(
        "--out-dir",
        default="/home/researcher/Documents/模拟变压器AI反向建模/RFIC_TRANSFORMER_VALIDATION_REPORT_20260613",
        help="Output report directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(args.project_root).expanduser().resolve()
    package_dir = Path(args.package_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    assets_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    summaries = _load_summaries(project_root)
    asset_specs = _asset_specs(project_root, package_dir)
    copied_assets = _copy_assets(asset_specs, assets_dir, summaries.get("validation_chain_decision", {}))
    cards = _status_cards(project_root, package_dir, summaries)
    html_text = _render_html(cards, copied_assets, summaries, project_root, package_dir)
    md_text = _render_markdown(cards, copied_assets, summaries, project_root, package_dir)

    html_path = out_dir / "index.html"
    md_path = out_dir / "RFIC_TRANSFORMER_VALIDATION_REPORT_20260613.md"
    html_path.write_text(html_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    manifest = {
        "report_dir": str(out_dir),
        "html": str(html_path),
        "markdown": str(md_path),
        "asset_count": len(copied_assets),
        "asset_usage_counts": _asset_usage_counts(copied_assets),
        "card_count": len(cards),
        "source_summary_count": len(_summary_paths(project_root)),
        "cards": cards,
        "assets": copied_assets,
        "source_summaries": {name: str(path) for name, path in _summary_paths(project_root).items()},
    }
    manifest_path = out_dir / "report_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    checksums_path = out_dir / "SHA256SUMS.txt"
    checksums_path.write_text(_checksums(out_dir), encoding="utf-8")

    print(f"report_dir={out_dir}")
    print(f"html={html_path}")
    print(f"markdown={md_path}")
    print(f"manifest={manifest_path}")
    print(f"assets={len(copied_assets)}")
    return 0


def _summary_paths(project_root: Path) -> dict[str, Path]:
    return {
        "create_only_50_validation": project_root / "literature_improvement_create_only_50" / "validation_summary_strict_wideband_20260613.json",
        "angle_200_validation": project_root / "angle_check_create_only_200" / "validation_summary_strict_wideband_20260613.json",
        "angle_200_default_proc_validation": project_root / "angle_check_create_only_200_default_proc" / "validation_summary_strict_wideband_20260613.json",
        "clearance_500": project_root / "final500_clearance_audit_visuals_20260613" / "clearance_audit_visual_summary.json",
        "template_preflight": project_root / "mars_dataset_248k_template_preflight_20260613.json",
        "template_preflight_strict_paths": project_root / "mars_dataset_248k_template_preflight_strict_paths_20260613.json",
        "hfss_touchstone_preflight": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "touchstone_preflight_hfss_wideband_20260613"
        / "touchstone_transformer_audit_summary.json",
        "package_dataset_touchstone_preflight": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "dataset_touchstone_preflight_package_target_only_20260613"
        / "dataset_touchstone_audit_summary.json",
        "create_only_geometry_audit": project_root
        / "literature_improvement_create_only_50"
        / "geometry_quality_audit_20260613"
        / "geometry_quality_audit_summary.json",
        "final500_geometry_audit": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "geometry_quality_audit_final500_selected_20260613"
        / "geometry_quality_audit_summary.json",
        "port_pairing_sensitivity": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "port_pairing_sensitivity_m9pow1p5_pass_20260613"
        / "port_pairing_sensitivity.json",
        "cm_mismatch": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "cm_mismatch_m9pow1p5_20260613"
        / "cm_mismatch_summary.json",
        "response_feature_coverage": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "response_feature_extraction_package_demo_20260613"
        / "response_feature_coverage_audit_min500_20260613"
        / "response_feature_coverage_summary.json",
        "hfss_sample_selection": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "hfss_validation_sample_selection_demo_20260613"
        / "hfss_validation_sample_selection_summary.json",
        "quality_gates_smoke": project_root
        / "literature_improvement_create_only_50"
        / "dataset_quality_gates_geometry_only_20260613"
        / "dataset_quality_gates_summary.json",
        "wideband_500_config": project_root / "mars_dataset_500_wideband_20260613.summary.json",
        "wideband_500_preflight": project_root / "mars_dataset_500_wideband_20260613_preflight.json",
        "wideband_500_strict_paths": project_root / "mars_dataset_500_wideband_20260613_preflight_strict_paths.json",
        "248k_launch_readiness": project_root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_summary.json",
        "acceptance_matrix": project_root / "acceptance_matrix_20260613.json",
        "delivery_package_audit": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "delivery_package_audit_20260613"
        / "delivery_package_audit_summary.json",
        "local_project_health": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "local_project_health_20260613"
        / "local_project_health_summary.json",
        "validation_chain_decision": project_root
        / "validation_chain_decision_20260614"
        / "validation_chain_decision_summary.json",
        "ads_metric_formula_consistency": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "ads_metric_formula_consistency_20260614"
        / "ads_metric_formula_consistency_summary.json",
        "mars_next_action_packet": project_root
        / "mars_next_action_packet_20260614"
        / "mars_next_action_packet_summary.json",
        "mars_emx_return_discovery": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "mars_emx_return_discovery_20260614"
        / "mars_emx_return_discovery_summary.json",
        "mars_emx_return_watch": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "mars_emx_return_watch_20260614"
        / "mars_emx_return_watch_summary.json",
        "mars_handoff_verify": project_root / "mars_handoff_verify_20260613_latest" / "mars_handoff_verify_summary.json",
        "emx_first_validation_gate": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "emx_first_validation_gate_20260613"
        / "emx_first_validation_gate_summary.json",
        "target_emx_wideband_rerun": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_rerun_summary.json",
        "target_emx_postrun_validation": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "target_emx_wideband_rerun_20260613"
        / "target_emx_wideband_postrun_validation_summary.json",
        "photo_matched_vs_target_geometry": project_root
        / "hfss_validation"
        / "final500_ec6698dfc575950b"
        / "photo_matched_vs_target_geometry_audit_20260613"
        / "photo_matched_vs_target_geometry_audit_summary.json",
    }


def _load_summaries(project_root: Path) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for name, path in _summary_paths(project_root).items():
        summaries[name] = _read_json(path)
    return summaries


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_parse_error": f"{type(exc).__name__}: {exc}", "_path": str(path)}


def _check_detail(summary: dict[str, Any], name: str) -> str | None:
    for item in summary.get("checks", []):
        if item.get("name") == name:
            return str(item.get("detail", ""))
    return None


def _step_detail(summary: dict[str, Any], name: str) -> str | None:
    for item in summary.get("steps", []):
        if item.get("name") == name:
            return str(item.get("detail", ""))
    return None


def _check_status_names(summary: dict[str, Any], status: str) -> list[str]:
    return [
        str(item.get("name", "unnamed check"))
        for item in summary.get("checks", [])
        if item.get("status") == status
    ]


def _readiness_card_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "248k launch readiness summary is missing or unreadable; production launch must stay blocked."
    checks = summary.get("checks", [])
    pass_names = _check_status_names(summary, "PASS")
    not_ready_names = _check_status_names(summary, "NOT_READY")
    fail_names = _check_status_names(summary, "FAIL")
    blockers = not_ready_names + fail_names
    return (
        f"Local readiness gate evaluated {len(checks)} checks: {len(pass_names)} PASS, "
        f"{len(not_ready_names)} NOT_READY, {len(fail_names)} FAIL. "
        f"Passing setup checks: {', '.join(pass_names) or 'none recorded'}. "
        f"Blocking checks: {', '.join(blockers) or 'none recorded'}. "
        "This is a launch guard: 248k must not start until real MARS EMX/Cadence paths, "
        "wideband 500 quality gates, and sampled strict 5% HFSS/EMX evidence are present."
    )


def _validation_chain_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "Validation-chain decision summary is missing or unreadable; do not claim accepted EMX/HFSS/ADS comparison."
    stages = summary.get("stages", [])
    stage_bits = [
        f"{stage.get('name', 'stage')}={stage.get('status', 'UNKNOWN')}/{stage.get('decision', 'UNKNOWN')}"
        for stage in stages
    ]
    return (
        f"build_validation_chain_decision_card.py evaluated the strict EMX-first -> HFSS geometry -> HFSS physical -> ADS sequence: "
        f"overall={summary.get('overall_status', 'UNKNOWN')}, decision={summary.get('decision', 'UNKNOWN')}. "
        f"Stages: {'; '.join(stage_bits) or 'none recorded'}. "
        "A diagnostic HFSS geometry or physical PASS cannot override a failed EMX-first golden-reference gate."
    )


def _mars_next_action_packet_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "MARS next-action packet is missing; rerun build_mars_next_action_packet.py after validation-chain and handoff checks."
    decision = summary.get("decision", "UNKNOWN")
    counts = summary.get("status_counts", {})
    if summary.get("overall_status") == "PASS" and decision == "READY_FOR_MARS_TARGET_EMX_RERUN":
        return (
            "build_mars_next_action_packet.py confirms the current safe transition: keep HFSS comparison blocked, "
            "run the target 5-50 GHz / 0.1 GHz EMX rerun on MARS, then run post-run validation and import verifier. "
            f"Packet checks={counts}."
        )
    if summary.get("overall_status") == "PASS" and decision == "VALIDATION_CHAIN_ALREADY_ACCEPTED":
        return "MARS next-action packet records that the validation chain is already accepted; keep it with the final evidence bundle."
    return (
        f"MARS next-action packet is not ready: overall={summary.get('overall_status', 'UNKNOWN')}, "
        f"decision={decision}, checks={counts}. Do not advance to HFSS-vs-EMX comparison."
    )


def _mars_emx_return_discovery_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return (
            "MARS returned-EMX discovery summary is missing; after pulling MARS files, run "
            "discover_and_verify_mars_emx_return.py before any HFSS-vs-EMX comparison."
        )
    selected = summary.get("selected", {})
    checks = summary.get("status_counts", {})
    if summary.get("overall_status") == "WAITING_FOR_MARS_RETURN":
        return (
            "No accepted local target EMX return is present yet. The discovery gate searched for a real "
            "4-port EMX .s4p on the exact 5-50 GHz / 0.1 GHz / 451-point grid plus the MARS validation tarball "
            f"and SHA record; selected_emx={selected.get('emx_s4p')}, selected_tarball={selected.get('tarball')}, checks={checks}. "
            "This is a protective gate: do not use old narrowband EMX or HFSS-labeled files as the golden reference."
        )
    if summary.get("overall_status") == "PASS" and summary.get("decision") == "ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS":
        return (
            "MARS returned-EMX discovery selected a local wideband EMX .s4p and the strict post-run import verifier "
            "accepted it for downstream HFSS/ADS validation."
        )
    return (
        f"MARS returned-EMX discovery is not accepted: overall={summary.get('overall_status', 'UNKNOWN')}, "
        f"decision={summary.get('decision', 'UNKNOWN')}, selected={selected}, checks={checks}."
    )


def _mars_emx_return_watch_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return (
            "MARS returned-EMX watcher summary is missing; run watch_mars_emx_return.py to preserve repeated "
            "local discovery/import snapshots while waiting for the MARS wideband EMX return."
        )
    latest = summary.get("latest_snapshot") if isinstance(summary.get("latest_snapshot"), dict) else {}
    return (
        "watch_mars_emx_return.py records repeated local discovery/import-gate attempts for the target EMX return. "
        f"overall={summary.get('overall_status', 'UNKNOWN')}, decision={summary.get('decision', 'UNKNOWN')}, "
        f"evidence_use={summary.get('evidence_use', 'UNKNOWN')}, accepted_emx_reference={summary.get('accepted_emx_reference')}, "
        f"iterations={summary.get('iteration_count', 0)}, stop_reason={summary.get('stop_reason', 'UNKNOWN')}, "
        f"s4p_candidates={summary.get('s4p_candidate_count', latest.get('s4p_candidate_count'))}, "
        f"tarball_candidates={summary.get('tarball_candidate_count', latest.get('tarball_candidate_count'))}, "
        f"verifier_decision={summary.get('verifier_decision', latest.get('verifier_decision'))}. "
        "This watcher is traceability only; final HFSS comparison still requires accepted EMX import evidence."
    )


def _ads_metric_formula_consistency_detail(summary: dict[str, Any]) -> str:
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "ADS metric formula consistency audit is missing; rerun audit_ads_metric_formula_consistency.py."
    recovery = summary.get("metric_recovery_errors", {})
    errors: list[tuple[str, float]] = []
    for metric, item in recovery.items():
        if isinstance(item, dict) and "max_percent_error" in item:
            try:
                errors.append((str(metric), float(item["max_percent_error"])))
            except (TypeError, ValueError):
                continue
    worst = "n/a" if not errors else f"{max(errors, key=lambda row: row[1])[0]}={max(errors, key=lambda row: row[1])[1]:.4g}%"
    freq = summary.get("frequency_ghz", {})
    template = summary.get("artifacts", {}).get("ads_data_display_template")
    template_note = " ADS Data Display template is recorded for ADS-side reproduction." if template else ""
    return (
        "audit_ads_metric_formula_consistency.py verifies the ADS-style Lp/Ls/M/K/Qp/Qs extraction implementation on a synthetic known coupled transformer. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; grid={freq.get('start')}-{freq.get('stop')} GHz, "
        f"step={freq.get('step')} GHz, points={freq.get('points')}; worst recovery error={worst}. "
        "This proves formula consistency only; it does not validate any EMX or HFSS simulator file."
        f"{template_note}"
    )


def _ads_style_plot_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "ads_style_metric_curves_20260613" / "ads_style_metric_plot_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "ADS-style EMX/HFSS metric plot summary is missing or unreadable."
    errors = summary.get("metric_max_percent_errors_common_window", {})
    freq = summary.get("common_overlay_frequency_ghz", {})
    hfss_freq = summary.get("hfss_plot_frequency_ghz", {})
    return str(summary.get("overall_status", "UNKNOWN")), (
        "plot_emx_hfss_ads_style_metrics.py generated six-panel curves directly from the current EMX/HFSS .s4p files using ADS-equivalent formulas. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; evidence_use={summary.get('evidence_use', 'UNKNOWN')}. "
        f"Common overlay window={freq.get('start')}-{freq.get('stop')} GHz, points={freq.get('points')}; "
        f"HFSS wideband plot={hfss_freq.get('start')}-{hfss_freq.get('stop')} GHz, points={hfss_freq.get('points')}. "
        f"Max errors in common window: K={_format_percent(errors.get('k'))}, Qp={_format_percent(errors.get('qp'))}, "
        f"Qs={_format_percent(errors.get('qs'))}, Lp={_format_percent(errors.get('lp_nh'))}, "
        f"Ls={_format_percent(errors.get('ls_nh'))}, M={_format_percent(errors.get('m_nh'))}, "
        f"Cm(single-ended)={_format_percent(errors.get('cm_single_primary_y11_plus_y12_ff'))}. "
        "These are not ADS GUI screenshots and do not prove 5-50 GHz EMX validation or final Lp/Ls/Q/K acceptance."
    )


def _ads_photo_alignment_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "ads_photo_reference_alignment_20260613" / "ads_photo_reference_alignment_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "ADS photo reference alignment audit has not been run."
    pieces = []
    for source in summary.get("sources", []):
        failed = [check for check in source.get("checks", []) if check.get("status") == "FAIL"]
        worst = max(source.get("checks", []), key=lambda item: float(item.get("percent_error", 0.0)), default={})
        pieces.append(
            f"{source.get('label')} {len(failed)}/{len(source.get('checks', []))} FAIL"
            + (
                f", worst={worst.get('label')} {float(worst.get('percent_error', 0.0)):.2f}%"
                if worst
                else ""
            )
        )
    return str(summary.get("overall_status", "UNKNOWN")), (
        "audit_ads_photo_reference_alignment.py compares current S4P-derived 15 GHz Lp/Ls/K/Qp/Qs/Cm against the user-provided ADS correct-curve photo markers. "
        f"Result: {'; '.join(pieces) or 'no source checks found'}. "
        "Current EMX/HFSS S4P files must not be treated as the correct ADS-reference curves until this gate is resolved."
    )


def _ads_photo_candidate_scan_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "ads_photo_reference_candidate_scan_20260613" / "ads_photo_reference_candidate_scan_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "ADS photo reference candidate scan has not been run."
    counts = summary.get("counts", {})
    best = summary.get("best") or {}
    best_emx = summary.get("best_emx") or {}
    return str(summary.get("overall_status", "UNKNOWN")), (
        "scan_s4p_ads_photo_reference_candidates.py searched local S4P files for a source matching the user-provided ADS correct-curve photo. "
        f"Scanned {counts.get('candidate_files', 0)} files: EMX PASS={counts.get('pass_emx', 0)}, "
        f"non-EMX PASS={counts.get('pass_non_emx', 0)}, errors={counts.get('errors', 0)}. "
        f"Best overall candidate is {best.get('source_kind', 'n/a')} with max error {_format_percent(best.get('max_percent_error'))}: `{best.get('touchstone', 'n/a')}`. "
        f"Best EMX candidate max error {_format_percent(best_emx.get('max_percent_error'))}: `{best_emx.get('touchstone', 'n/a')}`. "
        "A non-EMX match is only a provenance clue; it does not satisfy the EMX reference-source gate."
    )


def _photo_matched_reference_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "photo_matched_hfss_reference_20260613" / "photo_matched_reference_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "Photo-matched HFSS reference evidence has not been built."
    target = summary.get("target_record", {})
    checks = target.get("checks", [])
    failed = [check for check in checks if check.get("status") == "FAIL"]
    worst = max(checks, key=lambda item: float(item.get("percent_error", 0.0)), default={})
    fields = summary.get("metadata", {}).get("header_fields", {})
    freq = summary.get("frequency_ghz", {})
    return str(summary.get("overall_status", "UNKNOWN")), (
        "build_photo_matched_hfss_reference_evidence.py parses the one S4P that numerically matches the user's ADS correct-curve photo and renders ADS-style Lp/Ls/K/Q/Cm curves. "
        f"Header provenance: File={fields.get('File', 'n/a')}, Design={fields.get('Design', 'n/a')}, Setup={fields.get('Setup', 'n/a')}; "
        f"frequency range={freq.get('start')}-{freq.get('stop')} GHz, step={freq.get('step')} GHz, points={freq.get('points')}. "
        f"15 GHz checks: {len(failed)}/{len(checks)} FAIL"
        + (f", worst={worst.get('label')} {_format_percent(worst.get('percent_error'))}" if worst else "")
        + ". It is an HFSS/provenance clue and still does not satisfy the EMX reference-source gate."
    )


def _emx_first_validation_gate_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "emx_first_validation_gate_20260613" / "emx_first_validation_gate_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "EMX-first validation gate has not been run."
    checks = summary.get("checks", [])
    failed = [check for check in checks if check.get("status") == "FAIL"]
    target = summary.get("target_record", {})
    photo_checks = target.get("checks", [])
    photo_failed = [check for check in photo_checks if check.get("status") == "FAIL"]
    worst = max(photo_checks, key=lambda item: float(item.get("percent_error", 0.0)), default={})
    pair = summary.get("port_pair_sensitivity", {})
    best = pair.get("best") or {}
    default = pair.get("default") or {}
    freq = summary.get("frequency_ghz", {})
    by_name = {str(check.get("name")): check for check in checks}
    physical_status = by_name.get("physical metric window", {}).get("status", "MISSING")
    smooth_status = by_name.get("smooth transformer metric window", {}).get("status", "MISSING")
    return str(summary.get("overall_status", "UNKNOWN")), (
        "build_emx_first_validation_gate.py is the hard first gate before any HFSS-vs-EMX claim. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; failed checks={len(failed)}/{len(checks)}; "
        f"actual sweep={freq.get('start')}-{freq.get('stop')} GHz, step={freq.get('step')} GHz, points={freq.get('points')}. "
        f"Physical metric window status={physical_status}; smooth transformer metric window status={smooth_status}. "
        f"15 GHz ADS-photo anchor: {len(photo_failed)}/{len(photo_checks)} FAIL"
        + (f", worst={worst.get('label')} {_format_percent(worst.get('percent_error'))}" if worst else "")
        + f". Default port pair {default.get('port_pairs', 'n/a')} max error {_format_percent(default.get('max_percent_error'))}; "
        f"best tested pair {best.get('port_pairs', 'n/a')} max error {_format_percent(best.get('max_percent_error'))}. "
        "A FAIL here means the current EMX S4P is blocked as a golden ADS reference, even if generic passivity/reciprocity preflight passes."
    )


def _target_emx_wideband_rerun_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "target_emx_wideband_rerun_20260613" / "target_emx_wideband_rerun_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "Target EMX wideband rerun command has not been prepared."
    original = summary.get("original_frequency_hz", {})
    generated = summary.get("generated_frequency_hz", {})
    status = str(summary.get("overall_status", "UNKNOWN"))
    return status, (
        "prepare_target_emx_wideband_rerun.py reconstructs the target sample's MARS EMX rerun command from its saved summary.json. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; original grid={original.get('start_ghz')}-{original.get('stop_ghz')} GHz, "
        f"points={original.get('points')}; generated grid={generated.get('start_ghz')}-{generated.get('stop_ghz')} GHz, "
        f"step={generated.get('step_ghz')} GHz, points={generated.get('points')}. "
        "It preserves the original EMX binary, GDS, top cell, process file, pin purpose 51, and P001-P004 shield-grounded port flags while changing only the output path and trailing frequency list; delivery and handoff audits now parse that command and require the exact 451 explicit points from 5 GHz to 50 GHz in 0.1 GHz steps. "
        "This is command provenance only; the resulting .s4p still must pass the EMX-first validation gate before ADS/HFSS comparison."
    )


def _target_emx_postrun_validation_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "target_emx_wideband_rerun_20260613" / "target_emx_wideband_postrun_validation_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "Target EMX post-run validation command has not been prepared."
    checks = summary.get("checks", [])
    failed = [check for check in checks if check.get("status") == "FAIL"]
    return str(summary.get("overall_status", "UNKNOWN")), (
        "prepare_target_emx_postrun_validation.py prepares the exact MARS command to run after the regenerated EMX .s4p exists. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; failed checks={len(failed)}/{len(checks)}. "
        "The command first requires a non-empty EMX .s4p, records its SHA256, then runs audit_touchstone_transformer.py for 4-port 5-50 GHz / 0.1 GHz / 451-point physical sanity with every adjacent frequency step checked, "
        "runs build_emx_first_validation_gate.py against the ADS-photo anchor with the 5% threshold plus explicit ADS no-extrapolation and physical/smooth window arguments, and finally packages validation evidence. "
        "The local import verifier also rejects post-run packages whose metrics CSV artifacts are not themselves finite numeric L/Q/K/Cm tables on the 5-50 GHz / 0.1 GHz / 451-point grid, whose PNG plots are not decodable/sufficiently large/nonblank, or whose approved port-pair sensitivity CSV gate does not show 24 ordered pairings with approved pair 1,2:3,4 PASS and <=5% ADS-photo error. "
        "If this command fails on MARS, do not proceed to HFSS or ADS comparison."
    )


def _photo_matched_vs_target_geometry_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "photo_matched_vs_target_geometry_audit_20260613" / "photo_matched_vs_target_geometry_audit_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "Photo-matched HFSS vs target-geometry audit has not been run."
    checks = summary.get("checks", [])
    failed = [check for check in checks if check.get("status") == "FAIL"]
    rows = summary.get("dimension_comparisons", [])
    worst = max(rows, key=lambda item: float(item.get("relative_delta") or 0.0), default={})
    return str(summary.get("overall_status", "UNKNOWN")), (
        "audit_photo_matched_vs_target_geometry.py tests whether the HFSS S4P that matches the user's ADS photo can be used as the reference for target sample ec6698dfc575950b. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; failed checks={len(failed)}/{len(checks)}. "
        f"Comparable geometry dimensions={len(rows)}; worst mismatch={worst.get('name', 'n/a')} "
        f"{_format_percent((float(worst.get('relative_delta')) * 100.0) if worst.get('relative_delta') is not None else None)}. "
        "This FAIL preserves the photo-matched file as a useful correct-curve clue, but blocks using it as target-sample HFSS/ADS validation evidence."
    )


def _hfss_model_geometry_asset_detail(package_dir: Path) -> tuple[str, str]:
    summary_path = package_dir / "hfss_model_geometry_asset_audit_20260614" / "hfss_model_geometry_asset_audit_summary.json"
    summary = _read_json(summary_path)
    if not summary or "_missing" in summary or "_parse_error" in summary:
        return "UNKNOWN", "HFSS model geometry asset audit has not been run."
    failed = [check for check in summary.get("checks", []) if check.get("status") != "PASS"]
    checks_by_name = {str(check.get("name")): str(check.get("detail", "")) for check in summary.get("checks", [])}
    return str(summary.get("overall_status", "UNKNOWN")), (
        "audit_hfss_model_geometry_assets.py verifies the HFSS model-view evidence before it is used in the report. "
        f"Decision={summary.get('decision', 'UNKNOWN')}; failed checks={len(failed)}/{len(summary.get('checks', []))}. "
        f"Top view: {checks_by_name.get('HFSS top-view PNG', 'missing')}. "
        f"Isometric view: {checks_by_name.get('HFSS isometric-view PNG', 'missing')}. "
        f"Quality view: {checks_by_name.get('HFSS geometry-quality PNG', 'missing')}. "
        f"STEP: {checks_by_name.get('HFSS STEP model', 'missing')}. "
        "This proves only geometry asset traceability; EM correctness still requires the EMX-first, HFSS physical, ADS figure, and <=5% comparison gates."
    )


def _asset_specs(project_root: Path, package_dir: Path) -> list[Asset]:
    lit = project_root / "literature_improvement_create_only_50" / "dataset_visualizations_20260613_evidence"
    sampling = project_root / "literature_improvement_create_only_50" / "sampling_distribution_audit_20260613"
    clearance = project_root / "final500_clearance_audit_visuals_20260613"
    model = package_dir / "hfss_model_views"
    rejected = project_root / "hfss_validation" / "final500_01141f3dc413eeb0"
    preflight = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "touchstone_preflight_hfss_wideband_20260613"
    port_pairing = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "port_pairing_sensitivity_m9pow1p5_pass_20260613"
    cm = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "cm_mismatch_m9pow1p5_20260613"
    response = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "response_feature_extraction_package_demo_20260613" / "response_feature_coverage_audit_min500_20260613"
    hfss_selection = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "hfss_validation_sample_selection_demo_20260613"
    package_selfcheck = package_dir / "package_selfcheck_compare_window_20260613"
    photo_matched = package_dir / "photo_matched_hfss_reference_20260613"
    formula = project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "ads_metric_formula_consistency_20260614"
    emx_first = package_dir / "emx_first_validation_gate_20260613"
    photo_vs_target = package_dir / "photo_matched_vs_target_geometry_audit_20260613"
    return [
        Asset("HFSS 宽带 ADS 公式复核", package_dir / "hfss_wideband_ads_formula_metrics.png", "01_hfss_wideband_ads_formula_metrics.png", "真实 HFSS wideband .s4p 读入后按 ADS/Python 公式提取的 L/Q/K/Cm 曲线。"),
        Asset("ADS 物理特征公式一致性", formula / "ads_metric_formula_consistency_curves.png", "01a_ads_formula_consistency_curves.png", "合成已知耦合变压器在 5-50 GHz / 0.1 GHz 网格上 known vs recovered Lp/Ls/M/K/Qp/Qs 重合，用于证明公式实现；不是 EMX/HFSS 仿真证据。"),
        Asset("HFSS vs EMX 窄带对比", package_dir / "hfss_vs_emx_narrowband_reference.png", "02_hfss_vs_emx_narrowband_reference.png", "只覆盖 EMX 原始 13.5-16.5 GHz 窄带参考，不代表 5-50 GHz EMX 宽带已经完成。"),
        Asset("HFSS 模型俯视图", model / "hfss_payload_geometry_top_annotated.png", "03_hfss_model_top_annotated.png", "由同一 HFSS build payload 渲染的模型俯视图，用于解释端口、线圈和 shield 关系。"),
        Asset("HFSS 模型三维视图", model / "hfss_payload_geometry_isometric.png", "04_hfss_model_isometric.png", "用于汇报模型层叠与空间结构；STEP 文件也在桌面交付包中。"),
        Asset("HFSS 几何质量检查", model / "hfss_payload_geometry_quality_checks.png", "05_hfss_geometry_quality_checks.png", "边方向、非端口 overlap 和 clearance 检查图。"),
        Asset("final500 clearance 通过/拒绝数量", clearance / "01_clearance_pass_fail_counts.png", "06_clearance_pass_fail_counts.png", "真实 500 候选 geometry clearance audit：468 pass, 32 reject。"),
        Asset("final500 clearance violation 面积分布", clearance / "02_clearance_violation_area_hist.png", "07_clearance_violation_area_hist.png", "被拒样本的 signal-to-shield violation area 分布。"),
        Asset("final500 bbox 中心覆盖", clearance / "03_clearance_bbox_center_scatter.png", "08_clearance_bbox_center_scatter.png", "500 候选的几何位置覆盖；仅证明 clearance gate，不证明 EM/Zin。"),
        Asset("final500 bbox 尺寸覆盖", clearance / "04_clearance_bbox_size_scatter.png", "09_clearance_bbox_size_scatter.png", "500 候选几何尺寸覆盖；仅证明 clearance gate，不证明 EM/Zin。"),
        Asset("被拒样本 overlap 例子", rejected / "gds_shield_overlap_violation.png", "10_rejected_overlap_example.png", "01141f3dc413eeb0 因 signal body 与 ground ring overlap 被拒，说明 gate 不是摆设。"),
        Asset("50 条 create-only 输入边缘分布", lit / "01_input_marginal_histograms.png", "11_create_only_input_marginals.png", "50 条 create-only 样本的输入采样分布；缺 EM/Zin，所以只能作为 preliminary 几何/采样证据。"),
        Asset("50 条 create-only LHS bin 平衡", lit / "02_input_lhs_bin_balance.png", "12_create_only_lhs_bin_balance.png", "每个输入维度 10 bins 完全均衡，每 bin 5 条。"),
        Asset("50 条 create-only uniform quantile", lit / "03_input_uniform_quantiles.png", "13_create_only_uniform_quantiles.png", "用于证明输入采样接近均匀而不是正态。"),
        Asset("50 条 create-only 相关性热图", lit / "05_input_correlation_heatmap.png", "14_create_only_correlation_heatmap.png", "最大 pairwise abs correlation = 0.3342，小于当前 0.35 gate。"),
        Asset("50 条 create-only 角度检查", lit / "07_geometry_angle_summary.png", "15_create_only_angle_summary.png", "内部角度 135 deg，引线接口 90 deg；仍不是 EM 训练标签证明。"),
        Asset("50 条 create-only dashboard", lit / "13_dataset_dashboard.png", "16_create_only_dashboard.png", "该 dashboard 带 PRELIMINARY 标记，因为 valid S-parameter/Zin count 为 0。"),
        Asset("50 条 create-only 采样边界覆盖", sampling / "sampling_distribution_boundary_coverage.png", "16a_sampling_boundary_coverage.png", "从 dataset_rows.csv 和 bounds 重新计算 normalized min/max，证明每个输入维度触达配置范围两端；仍只是输入采样证据。"),
        Asset("50 条 create-only space-filling strata", sampling / "sampling_distribution_space_filling_strata.png", "16b_sampling_space_filling_strata.png", "每个输入维度按 20 个 strata 检查空桶和 imbalance，防止只看均值/直方图而漏掉输入空间孔洞。"),
        Asset("50 条 create-only 最近邻距离", sampling / "sampling_distribution_nearest_neighbor_distances.png", "16c_sampling_nearest_neighbor_distances.png", "归一化设计向量的最近邻距离分布，用来暴露重复点或异常聚团；不证明输出响应/Zin 覆盖。"),
        Asset("HFSS Touchstone 矩阵预检", preflight / "touchstone_matrix_quality.png", "17_hfss_touchstone_matrix_quality.png", "真实 HFSS wideband .s4p 的互易与无源逐频点检查，用于防止坏 Touchstone 文件进入 ADS。"),
        Asset("HFSS Touchstone ADS 等效指标预检", preflight / "touchstone_ads_equivalent_metrics.png", "18_hfss_touchstone_ads_equivalent_metrics.png", "同一 .s4p 按 ADS/Python 公式提取 Lp/Ls/K/Q 曲线；目标频点和 5-30 GHz 正 L/Q 窗口通过。"),
        Asset("最新 PASS 样本端口配对敏感性", port_pairing / "port_pairing_sensitivity.png", "19_port_pairing_sensitivity_m9pow1p5_pass.png", "最新 refined EMX/HFSS 样本中，物理配对 1,2:3,4 本身 PASS；错误配对会让 K/Q/L 误差大幅失败。"),
        Asset("Cm 单端公式误差诊断", cm / "cm_mismatch_selected_definition.png", "20_cm_mismatch_single_primary_definition.png", "按 ADS 单端 imag(Y11+Y12)/omega 定义，Cm 最大误差约 24.37%；差分定义另列在诊断报告中，不能混用公式。"),
        Asset("响应特征覆盖直方图", response / "response_feature_histograms.png", "21_response_feature_histograms.png", "由真实 .s4p 后处理得到的 K/Q/L/Cm 标签直方图；当前仅 2 个 demo 样本，不代表 final-500 或 248k 覆盖。"),
        Asset("响应特征 K/Q/L 散点", response / "response_k_q_l_scatter.png", "22_response_k_q_l_scatter.png", "K-Qp 和 Lp-Ls 响应空间 sanity 图，用于后续 wideband 500/248k 覆盖审计模板。"),
        Asset("包内自检 K 对比", package_selfcheck / "k_comparison.png", "23_package_selfcheck_k_comparison.png", "同一包内 EMX 窄带参考与 HFSS wideband .s4p 在 13.5-16.5 GHz / 9 点窗口的 K 曲线对比，最大误差低于 5%。"),
        Asset("包内自检 Qp 对比", package_selfcheck / "qp_comparison.png", "24_package_selfcheck_qp_comparison.png", "同一包内 EMX 窄带参考与 HFSS wideband .s4p 的 primary Q 曲线对比；门禁来自 summary JSON，不靠人工读图。"),
        Asset("包内自检 Qs 对比", package_selfcheck / "qs_comparison.png", "25_package_selfcheck_qs_comparison.png", "同一包内 EMX 窄带参考与 HFSS wideband .s4p 的 secondary Q 曲线对比；仅覆盖窄带窗口。"),
        Asset("包内自检 Lp 对比", package_selfcheck / "lp_nh_comparison.png", "26_package_selfcheck_lp_comparison.png", "同一包内 EMX 窄带参考与 HFSS wideband .s4p 的 primary inductance 对比，单位 nH。"),
        Asset("包内自检 Ls 对比", package_selfcheck / "ls_nh_comparison.png", "27_package_selfcheck_ls_comparison.png", "同一包内 EMX 窄带参考与 HFSS wideband .s4p 的 secondary inductance 对比，单位 nH。"),
        Asset("HFSS 复验样本 Zin 选点图", hfss_selection / "hfss_validation_sample_zin_map.png", "28_hfss_validation_sample_zin_map.png", "选择器在 Zin 平面上标出候选点与被选中的 HFSS/ADS 复验点；当前是 2 个 demo 样本，不代表 final-500/wideband 正式样本集。"),
        Asset("照片匹配 HFSS 线索曲线", photo_matched / "photo_matched_reference_ads_style_metrics.png", "29_photo_matched_hfss_reference_metrics.png", "唯一与用户正确 ADS 照片 15 GHz 标记几乎完全一致的 .s4p；文件头为 HFSS export，不是 EMX，因此只能作为溯源线索。"),
        Asset("EMX-first gate 物理曲线", emx_first / "emx_first_validation_gate_ads_style_metrics.png", "30_emx_first_gate_ads_style_metrics.png", "当前 EMX .s4p 按 ADS 等价公式提取的 Lp/Ls/K/Q/Cm；照片锚点和 5-50 GHz 宽带门槛失败，不能作为黄金 EMX 参考源。"),
        Asset("EMX-first gate 核心 L/Q/K 曲线", emx_first / "emx_first_validation_gate_core_metrics.png", "30a_emx_first_gate_core_metrics.png", "当前 EMX .s4p 的 Lp/Ls/Qp/Qs/K 核心物理曲线；这张图用于先验检查 EMX 自身是否像有效耦合变压器，失败时不得进入 HFSS-vs-EMX 对比。"),
        Asset("EMX-first gate 端口穷举", emx_first / "emx_first_validation_gate_port_pair_sensitivity.png", "31_emx_first_gate_port_pair_sensitivity.png", "24 种四端口配对/方向全部不能让当前 EMX 匹配正确 ADS 照片，说明问题不是简单端口顺序或 K 符号。"),
        Asset("照片匹配 HFSS 与目标几何审计", photo_vs_target / "photo_matched_vs_target_geometry_scale.png", "32_photo_matched_vs_target_geometry_scale.png", "照片匹配 HFSS 文件与 ec6698dfc575950b 的项目名、端口名、频率网格和几何尺度均不一致，因此不能用作当前样本的 HFSS/ADS 验证参考。"),
    ]


def _copy_assets(asset_specs: list[Asset], assets_dir: Path, validation_chain: dict[str, Any]) -> list[dict[str, str]]:
    copied = []
    for asset in asset_specs:
        evidence_use, usage_note = _asset_evidence_use(asset, validation_chain)
        if not asset.source.exists():
            copied.append(
                {
                    "title": asset.title,
                    "source": str(asset.source),
                    "file": "",
                    "caption": asset.caption,
                    "status": "MISSING",
                    "evidence_use": "MISSING",
                    "usage_note": f"Source file is missing; expected use was {evidence_use}: {usage_note}",
                }
            )
            continue
        dest = assets_dir / asset.filename
        shutil.copy2(asset.source, dest)
        copied.append(
            {
                "title": asset.title,
                "source": str(asset.source),
                "file": f"assets/{asset.filename}",
                "caption": asset.caption,
                "status": "OK",
                "evidence_use": evidence_use,
                "usage_note": usage_note,
                "sha256": _sha256(dest),
            }
        )
    return copied


def _asset_evidence_use(asset: Asset, validation_chain: dict[str, Any]) -> tuple[str, str]:
    filename = asset.filename.lower()
    title = asset.title.lower()
    final_chain_accepted = (
        validation_chain.get("overall_status") == "PASS"
        and validation_chain.get("decision") == "ACCEPT_FULL_EMX_HFSS_ADS_VALIDATION_CHAIN"
    )
    emx_blocked = validation_chain.get("overall_status") == "BLOCKED_BY_EMX_REFERENCE"

    final_compare_tokens = (
        "hfss_vs_emx",
        "package_selfcheck",
        "emx_first_gate",
    )
    if any(token in filename for token in final_compare_tokens):
        if final_chain_accepted:
            return (
                "ACCEPTED_FOR_CURRENT_CLAIM",
                "Full validation chain is accepted; still cite this with its explicit sweep/window caption.",
            )
        if emx_blocked:
            return (
                "BLOCKED_AS_FINAL_EVIDENCE",
                "EMX-first is not accepted, so this plot may document the failure or narrowband diagnostic only; it must not be cited as final EMX-vs-HFSS validation.",
            )
        return (
            "DIAGNOSTIC_ONLY",
            "Final accepted EMX-vs-HFSS/ADS comparison is not complete; use only as diagnostic context.",
        )

    if "photo_matched" in filename or "photo-matched" in title or "照片匹配" in title:
        return (
            "DIAGNOSTIC_ONLY",
            "This is a provenance/debugging clue or rejection audit, not target-sample accepted EMX/HFSS evidence.",
        )

    if filename.startswith(("01_hfss", "17_hfss", "18_hfss")) or "hfss touchstone" in title:
        return (
            "DIAGNOSTIC_ONLY",
            "Valid for standalone HFSS physical sanity, but not final EMX-vs-HFSS validation until EMX-first and accepted comparison pass.",
        )

    if "cm_" in filename or "cm " in title:
        return (
            "DIAGNOSTIC_ONLY",
            "Formula/definition diagnostic; Cm is intentionally outside the current core 5% L/Q/K pass claim.",
        )

    if "response_" in filename or "zin" in filename or "sample_selection" in filename:
        return (
            "DIAGNOSTIC_ONLY",
            "Demo or selection/coverage diagnostic; not final-500 or 248k response-label evidence yet.",
        )

    return (
        "ACCEPTED_FOR_CURRENT_CLAIM",
        "Reportable only for the limited claim stated in the caption and status cards.",
    )


def _asset_usage_counts(assets: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in assets:
        key = asset.get("evidence_use", "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _status_cards(project_root: Path, package_dir: Path, summaries: dict[str, Any]) -> list[dict[str, str]]:
    create = summaries["create_only_50_validation"]
    angle = summaries["angle_200_validation"]
    angle_default = summaries["angle_200_default_proc_validation"]
    clearance = summaries["clearance_500"]
    template = summaries["template_preflight"]
    strict = summaries["template_preflight_strict_paths"]
    preflight = summaries["hfss_touchstone_preflight"]
    batch_preflight = summaries["package_dataset_touchstone_preflight"]
    create_geom = summaries["create_only_geometry_audit"]
    final500_geom = summaries["final500_geometry_audit"]
    port_pairing = summaries["port_pairing_sensitivity"]
    cm_mismatch = summaries["cm_mismatch"]
    response_coverage = summaries["response_feature_coverage"]
    hfss_selection = summaries["hfss_sample_selection"]
    quality_gates = summaries["quality_gates_smoke"]
    wideband_config = summaries["wideband_500_config"]
    wideband_preflight = summaries["wideband_500_preflight"]
    wideband_strict = summaries["wideband_500_strict_paths"]
    launch_readiness = summaries["248k_launch_readiness"]
    acceptance = summaries["acceptance_matrix"]
    delivery_audit = summaries["delivery_package_audit"]
    local_health = summaries["local_project_health"]
    validation_chain = summaries["validation_chain_decision"]
    formula_consistency = summaries["ads_metric_formula_consistency"]
    mars_next_action = summaries["mars_next_action_packet"]
    mars_emx_return = summaries["mars_emx_return_discovery"]
    mars_emx_return_watch = summaries["mars_emx_return_watch"]
    handoff_verify = summaries["mars_handoff_verify"]
    preflight_target = preflight.get("metric_summary", {}).get("target_point", {})
    preflight_source = _check_detail(preflight, "source identity")
    preflight_z_reciprocity = _check_detail(preflight, "differential Z reciprocity")
    preflight_z_positive_real = _check_detail(preflight, "differential Z positive-realness")
    preflight_shape = _check_detail(preflight, "smooth transformer metric window")
    delivery_checks = delivery_audit.get("checks", [])
    delivery_selfcheck = _check_detail(delivery_audit, "package selfcheck compare gate")
    delivery_shape = _check_detail(delivery_audit, "MARS handoff extracted shape-window gate")
    delivery_touchstone_contract = _check_detail(delivery_audit, "MARS handoff extracted Touchstone transformer audit contract")
    port_pairing_best = port_pairing.get("best", {})
    port_pairing_max_error = _format_percent(port_pairing_best.get("score_max_percent_error"))
    ads_style_status, ads_style_detail = _ads_style_plot_detail(package_dir)
    photo_alignment_status, photo_alignment_detail = _ads_photo_alignment_detail(package_dir)
    candidate_scan_status, candidate_scan_detail = _ads_photo_candidate_scan_detail(package_dir)
    photo_matched_status, photo_matched_detail = _photo_matched_reference_detail(package_dir)
    emx_first_status, emx_first_detail = _emx_first_validation_gate_detail(package_dir)
    target_emx_rerun_status, target_emx_rerun_detail = _target_emx_wideband_rerun_detail(package_dir)
    target_emx_postrun_status, target_emx_postrun_detail = _target_emx_postrun_validation_detail(package_dir)
    photo_vs_target_status, photo_vs_target_detail = _photo_matched_vs_target_geometry_detail(package_dir)
    hfss_geometry_asset_status, hfss_geometry_asset_detail = _hfss_model_geometry_asset_detail(package_dir)
    local_pytest_detail = _step_detail(local_health, "full local pytest suite")
    return [
        {
            "name": "Acceptance matrix",
            "status": acceptance.get("overall_status", "UNKNOWN"),
            "detail": (
                "Requirement-by-requirement evidence matrix generated: "
                f"{acceptance.get('status_counts', {})}. INCOMPLETE is expected until MARS final-500 pull, "
                "wideband 500 pilot, and 248k production are actually done."
            ),
        },
        {
            "name": "EMX/HFSS/ADS validation-chain decision",
            "status": validation_chain.get("overall_status", "UNKNOWN"),
            "detail": _validation_chain_detail(validation_chain),
        },
        {
            "name": "ADS metric formula consistency",
            "status": formula_consistency.get("overall_status", "UNKNOWN"),
            "detail": _ads_metric_formula_consistency_detail(formula_consistency),
        },
        {
            "name": "MARS next-action packet",
            "status": mars_next_action.get("overall_status", "UNKNOWN"),
            "detail": _mars_next_action_packet_detail(mars_next_action),
        },
        {
            "name": "MARS returned target EMX discovery/import gate",
            "status": mars_emx_return.get("overall_status", "UNKNOWN"),
            "detail": _mars_emx_return_discovery_detail(mars_emx_return),
        },
        {
            "name": "MARS returned target EMX watch trace",
            "status": mars_emx_return_watch.get("overall_status", "UNKNOWN"),
            "detail": _mars_emx_return_watch_detail(mars_emx_return_watch),
        },
        {
            "name": "Delivery package audit",
            "status": delivery_audit.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_delivery_package.py verifies the desktop package SHA manifest, zip integrity/clean metadata, "
                "bytecode/cache hygiene, report assets/nonblank images, narrowband package selfcheck boundary/gate, "
                "ADS metric formula consistency summary/report/nonblank plot evidence, "
                "MARS dataset package non-empty inventory-file contracts, target EMX post-run import verifier contracts including finite numeric metrics CSV 5-50 GHz / 451-point grid checks, approved port-pair CSV gate, and decodable sufficiently large nonblank PNG plot checks, "
                "MARS handoff SHA/portable commands/tar extraction, "
                "Python <3.12 tar extraction fallback contracts, and the conservative acceptance-matrix boundary. "
                f"Latest local audit covered {len(delivery_checks)} checks; extracted handoff shape gate: "
                f"{delivery_shape or 'not found'}; extracted Touchstone transformer audit contract: "
                f"{delivery_touchstone_contract or 'not found'}; package selfcheck: {delivery_selfcheck or 'not found'}. "
                "Current zip SHA is recorded only outside the package to avoid circular hashes."
            ),
        },
        {
            "name": "Local project health check",
            "status": local_health.get("overall_status", "UNKNOWN"),
            "detail": (
                "run_local_project_health_check.py orchestrates package selfcheck, MARS handoff bundle rebuild, "
                "ADS metric formula consistency audit, pre-sync acceptance-matrix refresh, clean zip rebuild, delivery package audit, canonical latest MARS handoff verifier, "
                "MARS next-action packet generation, post-audit acceptance-matrix refresh, final clean zip rebuild, final delivery audit, and acceptance-matrix boundary checks. "
                f"Latest local run covered {len(local_health.get('steps', []))} steps. "
                f"Full pytest gate: {local_pytest_detail or 'not run in latest health summary'}. "
                "It is a local reproducibility gate only and does not run MARS/HFSS/ADS/EMX."
            ),
        },
        {
            "name": "MARS handoff verifier",
            "status": handoff_verify.get("overall_status", "UNKNOWN"),
            "detail": (
                "verify_mars_handoff_install.py simulates the first MARS-side unpack/install readiness check. "
                f"Latest smoke covered {len(handoff_verify.get('checks', []))} checks: SHA manifest, required helper files, "
                "dataset package helper/verifier contracts, portable runbook commands, "
                "required clearance-audit gate, Touchstone shape-window gate, EMX-first gate script contract, "
                "Touchstone transformer audit contract with per-adjacent-step frequency-grid checks, "
                "target EMX post-run import verifier contract with finite numeric metrics CSV grid, approved port-pair CSV gate, decodable sufficiently large nonblank PNG plot checks, Python <3.12 tar extraction fallback, and bash syntax. "
                "It does not run EMX/Cadence."
            ),
        },
        {
            "name": "HFSS single-sample ADS export",
            "status": "PASS",
            "detail": "ec6698dfc575950b has real HFSS wideband .s4p from 0.1-50 GHz, 0.1 GHz step, 500 points. Desktop package SHA256 is recorded outside the package to avoid circular hashes.",
        },
        {
            "name": "HFSS model geometry assets",
            "status": hfss_geometry_asset_status,
            "detail": hfss_geometry_asset_detail,
        },
        {
            "name": "HFSS vs EMX narrowband gate",
            "status": "PARTIAL",
            "detail": "Lp/Ls/Qp/Qs/K pass <5% over the explicit EMX 13.5-16.5 GHz / 9-point comparison window. Cm is unresolved at about 24.37% max error.",
        },
        {
            "name": "ADS-style EMX/HFSS curve plots",
            "status": ads_style_status,
            "detail": ads_style_detail,
        },
        {
            "name": "EMX-first golden reference gate",
            "status": emx_first_status,
            "detail": emx_first_detail,
        },
        {
            "name": "Target EMX wideband rerun command",
            "status": target_emx_rerun_status,
            "detail": target_emx_rerun_detail,
        },
        {
            "name": "Target EMX post-run validation command",
            "status": target_emx_postrun_status,
            "detail": target_emx_postrun_detail,
        },
        {
            "name": "ADS photo reference alignment",
            "status": photo_alignment_status,
            "detail": photo_alignment_detail,
        },
        {
            "name": "ADS photo S4P candidate scan",
            "status": candidate_scan_status,
            "detail": candidate_scan_detail,
        },
        {
            "name": "Photo-matched HFSS reference clue",
            "status": photo_matched_status,
            "detail": photo_matched_detail,
        },
        {
            "name": "Photo-matched HFSS target-geometry audit",
            "status": photo_vs_target_status,
            "detail": photo_vs_target_detail,
        },
        {
            "name": "S4P port-pairing sensitivity",
            "status": "PRELIMINARY" if port_pairing.get("best") else "UNKNOWN",
            "detail": (
                "Diagnostic only: this compares a refined HFSS run against the currently blocked narrowband EMX file, "
                "so it can identify the least-bad HFSS port ordering but cannot accept EMX as a golden reference. "
                "The EMX-first gate remains the controlling decision before any final ADS/HFSS comparison. "
                "This diagnostic tests all 24 four-port pair/order choices. "
                f"Best/default pairing is {port_pairing_best.get('hfss_port_pairs', 'n/a')} with max error "
                f"{port_pairing_max_error}; wrong pairings fail by much larger margins."
            ),
        },
        {
            "name": "Cm single-ended formula diagnostic",
            "status": cm_mismatch.get("overall_status", "UNKNOWN"),
            "detail": (
                "diagnose_cm_mismatch.py keeps Cm outside the K/Q/L pass gate. "
                f"Selected single-ended definition max error is "
                f"{_format_percent(cm_mismatch.get('definitions', {}).get('single_primary_y11_plus_y12_ff', {}).get('max_percent_error'))}; "
                "differential definitions are reported separately and must not be mixed with the ADS single-ended formula."
            ),
        },
        {
            "name": "Response feature coverage gate",
            "status": response_coverage.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_response_feature_coverage.py checks K/Q/L/Cm labels for count, finite values, physical sanity, spans, K/Q bin occupancy, and optional fixed K-Qp / Lp-Ls target-envelope coverage. "
                f"Current package demo valid_count={response_coverage.get('label_summary', {}).get('valid_count', 'n/a')}; "
                "the min-500 gate intentionally fails on this 2-file demo."
            ),
        },
        {
            "name": "HFSS validation sample selector",
            "status": hfss_selection.get("overall_status", "UNKNOWN"),
            "detail": (
                "select_hfss_validation_samples.py selects response-labeled samples by Zin extrema/quantiles, K/Q extrema, and sparse Re/Im Zin bins, "
                "then records reason counts, selected feature summary, Zin 2D bin coverage, and a Zin-plane selection map. "
                f"Current demo selected {hfss_selection.get('selected_count', 'n/a')} of {hfss_selection.get('candidate_count', 'n/a')} candidates; "
                "formal final-500/wideband HFSS sampling still requires real completed response labels."
            ),
        },
        {
            "name": "HFSS Touchstone preflight",
            "status": preflight.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_touchstone_transformer.py passes on the wideband HFSS .s4p: 4 ports, "
                "0.1-50 GHz / 500 points, required ADS sweep 5-50 GHz covered, "
                f"source identity: {preflight_source or 'not recorded in source summary'}, "
                f"differential-Z reciprocity: {preflight_z_reciprocity or 'not recorded'}, "
                f"differential-Z positive-realness: {preflight_z_positive_real or 'not recorded'}, "
                f"15 GHz Lp={preflight_target.get('lp_nh', 'n/a')} nH, "
                f"Ls={preflight_target.get('ls_nh', 'n/a')} nH, "
                f"K={preflight_target.get('k', 'n/a')}. "
                f"5-30 GHz smooth-shape gate: {preflight_shape or 'not run in source summary'}."
            ),
        },
        {
            "name": "Dataset Touchstone batch preflight helper",
            "status": batch_preflight.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_dataset_touchstones.py target-only sanity check passed on "
                f"{batch_preflight.get('audited_count', 'n/a')} package Touchstone files. "
                f"Max reciprocity error={batch_preflight.get('matrix_quality_summary', {}).get('reciprocity_error_abs_max', 'n/a')}, "
                f"max passivity sigma={batch_preflight.get('matrix_quality_summary', {}).get('passivity_sigma_max', 'n/a')}. "
                "This proves the batch audit tool works; it is not a full final-500 or 248k validation."
            ),
        },
        {
            "name": "Dataset quality gate orchestrator",
            "status": quality_gates.get("overall_status", "UNKNOWN"),
            "detail": "run_dataset_quality_gates.py geometry-only smoke passes, can require clearance-audit evidence with --require-clearance-audit, and aggregates sub-report JSON status instead of trusting --no-fail-exit return codes. sample-dataset now writes final500_ground_clearance_audit.json automatically from layout sidecar evidence.",
        },
        {
            "name": "Automatic clearance audit generation",
            "status": "PASS",
            "detail": "layout export writes signal_shield_clearance_audit.json with direct-overlap and clearance-violation areas; evaluator rejects failing geometry before EMX; sample-dataset aggregates raw records into final500_ground_clearance_audit.json.",
        },
        {
            "name": "Wideband 500 pilot config generator",
            "status": wideband_config.get("overall_status", "UNKNOWN"),
            "detail": (
                "prepare_mars_wideband_config.py generated a 500-sample pilot config with "
                f"{wideband_config.get('frequency', {}).get('points')} points from "
                f"{wideband_config.get('frequency', {}).get('start_hz')} to "
                f"{wideband_config.get('frequency', {}).get('stop_hz')} Hz. "
                "The generated commands file now includes a post-run audit_mars_run_progress.py raw-clearance and EMX command contract "
                "followed by run_dataset_quality_gates.py with required clearance-audit evidence, sampling uniform-vs-normal KS/entropy/boundary-coverage plus space-filling no-duplicate/strata gates, and Touchstone positive-window and shape-window gates; the MARS handoff copy uses a relative "
                "configs/mars_dataset_500_wideband_20260613.yaml path."
            ),
        },
        {
            "name": "Wideband 500 config preflight",
            "status": wideband_preflight.get("overall_status", "UNKNOWN"),
            "detail": (
                "Generated pilot config passes frequency/port/pin/shield preflight. Strict path preflight remains "
                f"{wideband_strict.get('overall_status', 'UNKNOWN')} until real MARS EMX/Cadence paths replace placeholders."
            ),
        },
        {
            "name": "MARS path patch helper",
            "status": "PASS",
            "detail": "discover_mars_emx_cadence_paths.py can run read-only on MARS to collect candidate EMX/Cadence paths and a reviewable patch command; patch_mars_config_paths.py then explicitly writes reviewed paths into a config and reports INCOMPLETE while required placeholders remain.",
        },
        {
            "name": "final500 ground clearance audit",
            "status": "PASS",
            "detail": f"{clearance.get('pass_count')} pass, {clearance.get('reject_count')} reject from {clearance.get('record_count')} records; selected sample {clearance.get('selected_cache_key')}.",
        },
        {
            "name": "Geometry metadata gate",
            "status": create_geom.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_geometry_quality.py passes on the 50 create-only manifest: grounded shield, pin purpose 51, "
                "50/50 geometry checks, internal 135 deg, terminal interface 90 deg. This is geometry evidence only."
            ),
        },
        {
            "name": "Selected final500 clearance gate",
            "status": final500_geom.get("overall_status", "UNKNOWN"),
            "detail": (
                "audit_geometry_quality.py passes on the final500 clearance package: "
                f"{final500_geom.get('clearance_counts', {}).get('pass_count')} pass / "
                f"{final500_geom.get('clearance_counts', {}).get('reject_count')} reject, selected "
                f"{final500_geom.get('clearance_counts', {}).get('selected_cache_key')} is clearance-pass. "
                "Strict dataset acceptance now uses --require-clearance-audit so missing clearance evidence fails."
            ),
        },
        {
            "name": "create-only 50 geometry/LHS evidence",
            "status": "PRELIMINARY",
            "detail": f"Validation status {create.get('overall_status')}: geometry and LHS pass, but S-parameter/Zin labels and target_frequency are missing.",
        },
        {
            "name": "angle_check_create_only_200",
            "status": "FAIL",
            "detail": f"Validation status {angle.get('overall_status')}: ok_count={angle.get('counts', {}).get('ok_count')}, fail_count={angle.get('counts', {}).get('fail_count')}.",
        },
        {
            "name": "angle_check_create_only_200_default_proc",
            "status": "FAIL",
            "detail": f"Validation status {angle_default.get('overall_status')}: port_mode={angle_default.get('port_mode')}, not grounded shield.",
        },
        {
            "name": "MARS 248k template logic",
            "status": template.get("overall_status", "UNKNOWN"),
            "detail": "Frequency/port/pin/shield checks pass for 5-50 GHz, 0.1 GHz, 451 points; EMX/Cadence paths still have placeholders.",
        },
        {
            "name": "MARS strict path preflight",
            "status": strict.get("overall_status", "UNKNOWN"),
            "detail": "Strict path mode currently fails because real MARS EMX/Cadence paths are not filled in. This must pass before launching wideband 500 or 248k.",
        },
        {
            "name": "248k launch readiness gate",
            "status": launch_readiness.get("overall_status", "UNKNOWN"),
            "detail": _readiness_card_detail(launch_readiness),
        },
        {
            "name": "Local SSH to MARS",
            "status": "BLOCKED",
            "detail": "Noninteractive SSH to mars.example.edu timed out on port 22. Use Guacamole/MARS terminal tar workflow for pulling final-500 data.",
        },
        {
            "name": "MARS transfer helper",
            "status": "PASS",
            "detail": "package_mars_dataset_run.py now refuses empty transfer files before tarball creation, then creates a minimal tarball, SHA256 file, inventory JSON, and inventory Markdown report with category counts for completed MARS dataset runs, including raw clearance audit and progress audit/watch evidence when present. backfill_ground_clearance_audit.py can regenerate raw clearance evidence for older runs from saved geometry without rerunning EMX.",
        },
        {
            "name": "MARS package verifier",
            "status": "PASS",
            "detail": "verify_mars_dataset_package.py verifies downloaded tarball SHA, inventory file count, non-empty packaged files, inventory Markdown report, tar path safety, tar metadata/cache hygiene, duplicate-member hygiene, tar inventory exactness, per-file SHA/size, optional packaged progress/watch evidence, inventory clearance-audit evidence, packaged quality-gate PASS plus required clearance-audit contract and raw clearance file, and can run audit_mars_run_progress.py with raw-clearance and EMX command constraints on a temporary extraction before local quality gates.",
        },
        {
            "name": "MARS handoff source sync",
            "status": "PASS",
            "detail": "build_mars_handoff_bundle.py now includes the package source files required for automatic clearance sidecars, evaluator pre-EMX rejection, dataset audit aggregation, plus the old-run clearance backfill script; handoff and delivery verifiers check those source fragments.",
        },
        {
            "name": "MARS run progress audit helper",
            "status": "PASS",
            "detail": "audit_mars_run_progress.py checks manifest, rows CSV, per-evaluation summary ok/error status, .s4p/4-port Touchstone evidence, layout evidence, sampled frequency grids, optional raw final500_ground_clearance_audit.json count accounting, and optional EMX command semantics for pin 51 plus grounded single-ended ports; watch_mars_run_progress.py repeatedly runs the same audit and stores history plus clearance counts for overnight MARS jobs; watch_mars_emx_return.py repeatedly runs the target EMX return discovery/import gate and stores accepted/waiting snapshots for local pull traceability.",
        },
        {
            "name": "Legacy frequency metadata backfill",
            "status": "PASS",
            "detail": "backfill_dataset_frequency_metadata.py parses existing Touchstone files to add sparam_freq_* columns for older runs without fabricating EM/Zin labels.",
        },
    ]


def _render_html(
    cards: list[dict[str, str]],
    assets: list[dict[str, str]],
    summaries: dict[str, Any],
    project_root: Path,
    package_dir: Path,
) -> str:
    card_html = "\n".join(_card_html(card) for card in cards)
    asset_html = "\n".join(_asset_html(asset) for asset in assets)
    matrix_html = _acceptance_matrix_html(summaries.get("acceptance_matrix", {}))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RFIC Transformer Validation Report 2026-06-13</title>
  <style>
    :root {{
      --ink: #1f2933;
      --muted: #5b6472;
      --line: #d8dee8;
      --bg: #f7f9fc;
      --panel: #ffffff;
      --pass: #166534;
      --fail: #b91c1c;
      --warn: #92400e;
      --partial: #1d4ed8;
    }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; color: var(--ink); background: var(--bg); }}
    header {{ padding: 34px 40px 22px; background: #ffffff; border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 34px 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; }}
    p, li {{ line-height: 1.55; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px 28px 50px; }}
    .sub {{ color: var(--muted); max-width: 900px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; color: white; font-size: 12px; font-weight: 700; margin-bottom: 9px; }}
    .PASS {{ background: var(--pass); }}
    .FAIL, .BLOCKED, .INCOMPLETE, .NO_MATCH, .NO_CANDIDATES {{ background: var(--fail); }}
    .PRELIMINARY, .PARTIAL, .PENDING, .NOT_READY, .REVIEW_REQUIRED, .WAITING_FOR_MARS_RETURN, .READY_TO_VERIFY {{ background: var(--warn); }}
    .UNKNOWN {{ background: var(--muted); }}
    .ACCEPTED_FOR_CURRENT_CLAIM {{ background: var(--pass); }}
    .DIAGNOSTIC_ONLY {{ background: var(--partial); }}
    .BLOCKED_AS_FINAL_EVIDENCE, .MISSING {{ background: var(--fail); }}
    .section-note {{ background: #fff7ed; border-left: 4px solid #f59e0b; padding: 12px 14px; }}
    figure {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 0; }}
    figure img {{ width: 100%; height: auto; display: block; border: 1px solid #eef2f7; }}
    figcaption {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
    .figure-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }}
    code {{ background: #eef2f7; padding: 1px 4px; border-radius: 4px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; }}
    th, td {{ border: 1px solid var(--line); padding: 9px; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    .small {{ font-size: 13px; color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>RFIC Transformer Inverse-Design Validation Report</h1>
    <p class="sub">Generated from local verified artifacts on 2026-06-13. This report separates real HFSS/EMX/geometry evidence from preliminary create-only evidence. It does not fabricate missing EM/Zin labels.</p>
  </header>
  <main>
    <section>
      <h2>Executive Status</h2>
      <div class="grid">{card_html}</div>
    </section>
    <section>
      <h2>What Can Be Claimed Now</h2>
      <ul>
        <li>One selected sample, <code>ec6698dfc575950b</code>, has a real HFSS wideband <code>.s4p</code> export, Touchstone preflight evidence, and model-view asset-audit evidence.</li>
        <li>The old ADS bad curve was caused by sweeping outside a narrowband Touchstone file, not by an ADS formula alone.</li>
        <li>The final500 clearance audit contains real 500-record geometry evidence: 468 pass and 32 reject.</li>
        <li>The reusable geometry audit gate now checks grounded port mode, pin purpose, 135 degree internal angles, 90 degree terminal interfaces, and selected-sample shield clearance evidence; strict acceptance uses <code>--require-clearance-audit</code>.</li>
        <li>The 50 create-only data show uniform/LHS geometry sampling and manufacturable angles, but they are not valid training labels because EM/Zin are absent.</li>
      </ul>
    </section>
    <section>
      <h2>What Cannot Be Claimed Yet</h2>
      <div class="section-note">
        Do not claim that the full 500 dataset is local, that 248k data exist, that 5-50 GHz EMX wideband labels are complete, that sampled HFSS/EMX 5% validation has passed, or that Cm passes the 5% HFSS-vs-EMX gate. The 248k launch readiness gate is intentionally NOT_READY until real MARS paths, wideband 500 quality gates, and sampled strict HFSS/EMX evidence exist.
      </div>
    </section>
    <section>
      <h2>Acceptance Matrix</h2>
      {matrix_html}
    </section>
    <section>
      <h2>Visual Evidence</h2>
      <div class="figure-grid">{asset_html}</div>
    </section>
    <section>
      <h2>Next Required Gates</h2>
      <ol>
        <li>Use <code>audit_mars_run_progress.py --require-clearance-audit</code> on MARS before packaging any final-500, wideband-500, or 248k run; for long jobs, use <code>watch_mars_run_progress.py --require-clearance-audit</code> to preserve interval history, clearance counts, and snapshots.</li>
        <li>For the current target sample <code>ec6698dfc575950b</code>, run <code>target_emx_wideband_rerun.commands.sh</code> on MARS to regenerate its own EMX reference at 5-50 GHz / 0.1 GHz / 451 points, then run <code>target_emx_wideband_postrun_validation.commands.sh</code> on the resulting file before ADS plotting or HFSS comparison; after pulling the tarball and <code>emx.s4p</code> locally, run <code>verify_target_emx_postrun_package.py --require-emx-s4p</code> and require <code>decision=ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS</code> plus <code>accepted_emx_reference_bundle.status=READY_FOR_HFSS</code>, with Touchstone physical gate per-adjacent-step frequency-grid checks PASS, EMX-first <code>ADS no-extrapolation plot grid = PASS</code>, metrics CSV artifacts passing finite numeric L/Q/K/Cm checks on the 5-50 GHz / 0.1 GHz / 451-point grid, PNG plots passing decodable/sufficiently large/nonblank checks, and the approved port-pair sensitivity CSV gate showing 24 ordered pairings with <code>1,2:3,4</code> PASS and <=5% ADS-photo error.</li>
        <li>Use Guacamole/MARS terminal and <code>package_mars_dataset_run.py</code> to create the four-file transfer set: tarball, SHA256, inventory JSON, and inventory Markdown report.</li>
        <li>After download, run <code>verify_mars_dataset_package.py</code> before trusting or unpacking the tarball; it checks the inventory Markdown report, tar metadata/cache hygiene, duplicate-member hygiene, tar inventory exactness, category counts, expected-count evidence, optional inventory raw-clearance evidence, optional progress/watch evidence, optional raw-clearance/EMX command constraints, and optional quality-gate summary.</li>
        <li>After local unpack, run <code>backfill_dataset_frequency_metadata.py</code> on the old narrowband 500 if its CSV lacks <code>sparam_freq_*</code> columns.</li>
        <li>Run strict validation and visualization on the pulled narrowband 500, with 13.5-16.5 GHz / 9-point expected frequency.</li>
        <li>Run <code>audit_touchstone_transformer.py</code> on any sampled <code>.s4p</code> before ADS plotting or HFSS-vs-EMX comparison, with expected start/stop/points/step so every adjacent frequency step is checked, and include a shape window such as <code>--shape-window-start-ghz 5 --shape-window-stop-ghz 30</code> for spike/jump screening.</li>
        <li>Use <code>audit_dataset_touchstones.py</code> to batch-audit sampled Touchstone files before using a pulled dataset for reports, with the same frequency, positive-window, and shape-window gates.</li>
        <li>Use <code>audit_geometry_quality.py --require-clearance-audit</code> on pulled manifests/clearance audits before accepting geometry for training.</li>
        <li>Use <code>run_dataset_quality_gates.py --require-clearance-audit</code> for one-command local acceptance summaries after MARS pulls.</li>
        <li>Run <code>discover_mars_emx_cadence_paths.py</code> on MARS to collect candidate EMX/Cadence paths, review the generated patch suggestion, fill real paths with <code>patch_mars_config_paths.py --check-paths</code>, then run <code>preflight_dataset_config.py --check-emx-paths</code>.</li>
        <li>Run a 500-sample wideband pilot at 5-50 GHz, 0.1 GHz step, 451 points.</li>
        <li>After the corresponding HFSS <code>.s4p</code> and HFSS geometry asset audit summary exist, run <code>run_accepted_emx_hfss_ads_validation.py</code> with the accepted EMX import summary from <code>verify_target_emx_postrun_package.py</code> and <code>--hfss-geometry-summary</code>; the runner must see post-run verifier evidence, local EMX SHA agreement, <code>decision=ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS</code> for model traceability, nonzero HFSS coupling gates, source-matched compare summary, compare criterion not above 5%, <code>ADS no-extrapolation coverage = PASS</code>, finite 451-point ADS-style <code>plot_data</code>, numeric <code>K/Qp/Qs/Lp/Ls</code> max errors at or below 5%, <code>decision=ACCEPT_HFSS_VALIDATION_SAMPLE</code>, decodable, sufficiently large, nonblank EMX, HFSS, and overlay <code>Lp/Ls/Qp/Qs/K</code> figures, and <code>ads_style_target_marker_values_15ghz.csv</code>/<code>ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md</code> marker evidence before they become single-sample evidence. Then run <code>verify_accepted_emx_hfss_ads_figures.py</code> and require <code>decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES</code> so the final figures and 15 GHz marker table are checked against the same <code>plot_data</code>.</li>
        <li>Run <code>run_hfss_emx_validation_batch.py --run-available --require-all-present --require-all-pass --compare-start-ghz 5 --compare-stop-ghz 50 --expected-frequency-step-ghz 0.1 --expected-frequency-points 451 --require-matching-frequency-grid --max-percent-error 5</code> on the selected HFSS rebuilds, and require each compare summary to be source-matched, use a criterion at or below 5%, keep <code>ADS no-extrapolation coverage = PASS</code>, and keep every <code>K/Qp/Qs/Lp/Ls</code> max error at or below 5%.</li>
        <li>Run <code>audit_248k_launch_readiness.py</code>; only a PASS result with sampled HFSS/EMX batch records showing <code>no_extrapolation_status=PASS</code>, source-matched per-sample compare summaries, compare criterion at or below 5%, and every K/Q/L max error at or below 5% may authorize the 248k production launch.</li>
        <li>Only after the wideband pilot passes EM/Zin/frequency/geometry gates should the 248k run start.</li>
      </ol>
    </section>
    <section>
      <h2>Source Pointers</h2>
      <table>
        <tr><th>Artifact</th><th>Path</th></tr>
        <tr><td>Project root</td><td><code>{html.escape(str(project_root))}</code></td></tr>
        <tr><td>Desktop package</td><td><code>{html.escape(str(package_dir))}</code></td></tr>
        <tr><td>Start-here note</td><td><code>{html.escape(str(project_root / "README_START_HERE_20260613_CN.md"))}</code></td></tr>
        <tr><td>Acceptance matrix</td><td><code>{html.escape(str(project_root / "ACCEPTANCE_MATRIX_20260613_CN.md"))}</code></td></tr>
        <tr><td>ADS metric formula consistency</td><td><code>{html.escape(str(project_root / "hfss_validation" / "final500_ec6698dfc575950b" / "ads_metric_formula_consistency_20260614" / "ads_metric_formula_consistency_report.md"))}</code></td></tr>
        <tr><td>EMX/HFSS/ADS physics validation method</td><td><code>{html.escape(str(project_root / "EMX_HFSS_ADS_PHYSICS_VALIDATION_METHOD_20260614_CN.md"))}</code></td></tr>
        <tr><td>MARS next-action packet</td><td><code>{html.escape(str(project_root / "mars_next_action_packet_20260614" / "MARS_NEXT_ACTION_PACKET_20260614_CN.md"))}</code></td></tr>
        <tr><td>Morning status latest</td><td><code>{html.escape(str(project_root / "MORNING_STATUS_20260614_CN.md"))}</code></td></tr>
        <tr><td>Morning status archive</td><td><code>{html.escape(str(project_root / "MORNING_STATUS_20260613_CN.md"))}</code></td></tr>
        <tr><td>Night audit</td><td><code>{html.escape(str(project_root / "OVERNIGHT_DATA_HFSS_AUDIT_20260613_CN.md"))}</code></td></tr>
        <tr><td>MARS next steps</td><td><code>{html.escape(str(project_root / "MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md"))}</code></td></tr>
        <tr><td>248k readiness gate</td><td><code>{html.escape(str(project_root / "248k_launch_readiness_local_20260613" / "248k_launch_readiness_report.md"))}</code></td></tr>
      </table>
      <p class="small">Report-ready status is intentionally conservative. Missing labels or missing frequency metadata keep a dataset preliminary.</p>
    </section>
  </main>
</body>
</html>
"""


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _card_html(card: dict[str, str]) -> str:
    status = html.escape(card["status"])
    return (
        f'<div class="card"><span class="badge {status}">{status}</span>'
        f"<h3>{html.escape(card['name'])}</h3>"
        f"<p>{html.escape(card['detail'])}</p></div>"
    )


def _asset_html(asset: dict[str, str]) -> str:
    title = html.escape(asset["title"])
    caption = html.escape(asset["caption"])
    evidence_use = html.escape(asset.get("evidence_use", "UNKNOWN"))
    usage_note = html.escape(asset.get("usage_note", ""))
    if asset["status"] != "OK":
        return (
            f"<figure><h3>{title}</h3><span class=\"badge {evidence_use}\">{evidence_use}</span>"
            f"<figcaption>MISSING: {html.escape(asset['source'])}. {usage_note}</figcaption></figure>"
        )
    return (
        f'<figure><h3>{title}</h3><span class="badge {evidence_use}">{evidence_use}</span>'
        f'<img src="{html.escape(asset["file"])}" alt="{title}"><figcaption>{caption}<br>{usage_note}</figcaption></figure>'
    )


def _render_markdown(
    cards: list[dict[str, str]],
    assets: list[dict[str, str]],
    summaries: dict[str, Any],
    project_root: Path,
    package_dir: Path,
) -> str:
    lines = [
        "# RFIC Transformer Inverse-Design Validation Report",
        "",
        "Generated from local verified artifacts on 2026-06-13. Missing EM/Zin labels are not inferred.",
        "",
        "## Executive Status",
        "",
    ]
    for card in cards:
        lines.append(f"- **{card['status']}** `{card['name']}`: {card['detail']}")
    lines.extend(
        [
            "",
            "## Claims Allowed Now",
            "",
            "- One selected sample `ec6698dfc575950b` has real HFSS wideband `.s4p` evidence and Touchstone preflight evidence.",
            "- The old ADS bad curve came from sweeping outside a narrowband Touchstone file.",
            "- final500 clearance audit shows 468 pass / 32 reject from 500 geometry records.",
            "- Reusable geometry gates now separately check angles, grounded shield metadata, pin purpose, and selected-sample clearance evidence; strict acceptance uses `--require-clearance-audit`.",
            "- The 50 create-only data prove geometry/LHS sampling only; they do not prove EM/Zin training labels.",
            "",
            "## Claims Not Allowed Yet",
            "",
            "- Do not claim that the full 500 dataset is local, that 248k data exist, that 5-50 GHz EMX wideband labels are complete, that sampled HFSS/EMX 5% validation has passed, or that Cm passes the 5% HFSS-vs-EMX gate.",
            "- The 248k launch readiness gate is intentionally `NOT_READY` until real MARS paths, wideband 500 quality gates, and sampled strict HFSS/EMX evidence exist.",
            "",
            "## Acceptance Matrix",
            "",
            *_acceptance_matrix_markdown_lines(summaries.get("acceptance_matrix", {})),
            "",
            "## Visual Evidence",
            "",
        ]
    )
    for asset in assets:
        lines.append(f"### {asset['title']}")
        lines.append("")
        lines.append(f"- Evidence use: `{asset.get('evidence_use', 'UNKNOWN')}`")
        lines.append(f"- Usage note: {asset.get('usage_note', '')}")
        lines.append("")
        if asset["status"] == "OK":
            lines.append(f"![{asset['title']}]({asset['file']})")
            lines.append("")
            lines.append(asset["caption"])
        else:
            lines.append(f"Missing source: `{asset['source']}`")
        lines.append("")
    lines.extend(
        [
            "## Next Required Gates",
            "",
            "1. Pull the complete final-500 run through Guacamole/MARS terminal tar workflow.",
            "2. For the current target sample `ec6698dfc575950b`, run `target_emx_wideband_rerun.commands.sh` on MARS to regenerate its own EMX reference at 5-50 GHz / 0.1 GHz / 451 points, then run `target_emx_wideband_postrun_validation.commands.sh` on the resulting file before ADS plotting or HFSS comparison; after pulling the tarball and `emx.s4p` locally, run `verify_target_emx_postrun_package.py --require-emx-s4p` and require `decision=ACCEPT_LOCAL_EMX_REFERENCE_FOR_HFSS` plus `accepted_emx_reference_bundle.status=READY_FOR_HFSS`, with Touchstone physical gate per-adjacent-step frequency-grid checks PASS, EMX-first `ADS no-extrapolation plot grid = PASS`, metrics CSV artifacts passing finite numeric L/Q/K/Cm checks on the 5-50 GHz / 0.1 GHz / 451-point grid, PNG plots passing decodable/sufficiently large/nonblank checks, and the approved port-pair sensitivity CSV gate showing 24 ordered pairings with `1,2:3,4` PASS and <=5% ADS-photo error.",
            "3. Run `audit_mars_run_progress.py --require-clearance-audit --require-emx-command --expected-port-mode single_ended_shield_grounded --expected-pin-purpose 51` on MARS before packaging to prove the run is file-complete and the raw clearance plus EMX command evidence is traceable.",
            "4. Use `package_mars_dataset_run.py` on MARS to create the four-file transfer set: tarball, SHA256, inventory JSON, and inventory Markdown report.",
            "5. After download, run `verify_mars_dataset_package.py` before trusting or unpacking the tarball; it checks inventory Markdown report consistency, tar metadata/cache hygiene, duplicate-member hygiene, tar inventory exactness, category counts, expected-count evidence, optional inventory raw-clearance evidence, optional progress/watch evidence, optional raw-clearance/EMX command constraints, and optional quality-gate summary.",
            "6. After local unpack, use `backfill_dataset_frequency_metadata.py` if the old rows CSV lacks `sparam_freq_*`.",
            "7. Validate narrowband 500 with expected 13.5-16.5 GHz / 9 points.",
            "8. Run `audit_touchstone_transformer.py` before ADS plotting or HFSS-vs-EMX comparison for sampled `.s4p` files, with expected start/stop/points/step so every adjacent frequency step is checked, and include a shape window such as `--shape-window-start-ghz 5 --shape-window-stop-ghz 30`.",
            "9. Use `audit_dataset_touchstones.py` to batch-audit sampled `.s4p` files from pulled datasets with the same frequency, positive-window, and shape-window gates.",
            "10. Use `audit_geometry_quality.py --require-clearance-audit` on manifests and clearance audit files.",
            "11. Use `run_dataset_quality_gates.py --require-clearance-audit` to generate a unified local gate summary, including sampling uniform-vs-normal KS, histogram-entropy, normalized boundary-coverage, no-duplicate design-vector, and LHS-style strata-coverage evidence.",
            "12. Run `discover_mars_emx_cadence_paths.py` on MARS to collect candidate EMX/Cadence paths, review the generated patch suggestion, fill real paths with `patch_mars_config_paths.py --check-paths`, and pass `preflight_dataset_config.py --check-emx-paths`.",
            "13. Run a 500-sample wideband pilot at 5-50 GHz / 0.1 GHz / 451 points.",
            "14. After the corresponding HFSS `.s4p` and HFSS geometry asset audit summary exist, run `run_accepted_emx_hfss_ads_validation.py` with the accepted EMX import summary from `verify_target_emx_postrun_package.py` and `--hfss-geometry-summary`; require post-run verifier evidence, local EMX SHA agreement, `decision=ACCEPT_HFSS_MODEL_GEOMETRY_ASSETS` for model traceability, nonzero HFSS coupling gates, source-matched compare summary, compare criterion not above 5%, `ADS no-extrapolation coverage = PASS`, finite 451-point ADS-style `plot_data`, numeric `K/Qp/Qs/Lp/Ls` max errors at or below 5%, `decision=ACCEPT_HFSS_VALIDATION_SAMPLE`, decodable, sufficiently large, nonblank EMX, HFSS, and overlay `Lp/Ls/Qp/Qs/K` figures, and `ads_style_target_marker_values_15ghz.csv` / `ADS_STYLE_TARGET_MARKER_VALUES_15GHZ.md` marker evidence before using them as single-sample evidence. Then run `verify_accepted_emx_hfss_ads_figures.py` and require `decision=ACCEPT_FINAL_LP_LS_Q_K_FIGURES` so the final figures and 15 GHz marker table are checked against the same `plot_data`.",
            "15. For sampled HFSS rebuilds, run `compare_emx_hfss_ads.py --compare-start-ghz 5 --compare-stop-ghz 50 --min-frequency-points 451 --expected-frequency-step-ghz 0.1 --expected-frequency-points 451 --require-matching-frequency-grid --max-percent-error 5`, and require the summary/report to include `ADS no-extrapolation coverage = PASS`.",
            "16. For selected HFSS rebuilds, run `run_hfss_emx_validation_batch.py --run-available --require-all-present --require-all-pass --compare-start-ghz 5 --compare-stop-ghz 50 --expected-frequency-step-ghz 0.1 --expected-frequency-points 451 --require-matching-frequency-grid --max-percent-error 5`, and require every per-sample compare summary to be source-matched, use a criterion at or below 5%, include `ADS no-extrapolation coverage = PASS`, and keep every `K/Qp/Qs/Lp/Ls` max error at or below 5%.",
            "17. Run `audit_248k_launch_readiness.py`; only a `PASS` result with sampled HFSS/EMX batch records showing `no_extrapolation_status=PASS`, source-matched per-sample compare summaries, compare criterion at or below 5%, and every K/Q/L max error at or below 5% may authorize the 248k production launch.",
            "18. Start 248k only after the wideband 500 pilot passes all gates.",
            "",
            "## Source Pointers",
            "",
            f"- Project root: `{project_root}`",
            f"- Desktop package: `{package_dir}`",
            f"- Start here: `{project_root / 'README_START_HERE_20260613_CN.md'}`",
            f"- Acceptance matrix: `{project_root / 'ACCEPTANCE_MATRIX_20260613_CN.md'}`",
            f"- ADS metric formula consistency: `{project_root / 'hfss_validation' / 'final500_ec6698dfc575950b' / 'ads_metric_formula_consistency_20260614' / 'ads_metric_formula_consistency_report.md'}`",
            f"- EMX/HFSS/ADS physics validation method: `{project_root / 'EMX_HFSS_ADS_PHYSICS_VALIDATION_METHOD_20260614_CN.md'}`",
            f"- MARS next-action packet: `{project_root / 'mars_next_action_packet_20260614' / 'MARS_NEXT_ACTION_PACKET_20260614_CN.md'}`",
            f"- Morning status latest: `{project_root / 'MORNING_STATUS_20260614_CN.md'}`",
            f"- Morning status archive: `{project_root / 'MORNING_STATUS_20260613_CN.md'}`",
            f"- Night audit: `{project_root / 'OVERNIGHT_DATA_HFSS_AUDIT_20260613_CN.md'}`",
            f"- MARS next steps: `{project_root / 'MARS_PULL_AND_WIDEBAND_NEXT_STEPS_20260613.md'}`",
            f"- 248k readiness gate: `{project_root / '248k_launch_readiness_local_20260613' / '248k_launch_readiness_report.md'}`",
        ]
    )
    return "\n".join(lines)


def _acceptance_matrix_html(matrix: dict[str, Any]) -> str:
    if not matrix or "_missing" in matrix:
        return "<p class=\"section-note\">Acceptance matrix has not been generated yet.</p>"
    rows = []
    for item in matrix.get("items", []):
        evidence = "<br>".join(html.escape(Path(path).name if path else "") for path in item.get("evidence", [])) or "None local yet"
        status = html.escape(str(item.get("status", "UNKNOWN")))
        rows.append(
            "<tr>"
            f"<td><span class=\"badge {status}\">{status}</span></td>"
            f"<td>{html.escape(str(item.get('requirement', '')))}</td>"
            f"<td>{html.escape(str(item.get('finding', '')))}</td>"
            f"<td>{evidence}</td>"
            "</tr>"
        )
    return (
        f"<p class=\"small\">Overall: <strong>{html.escape(str(matrix.get('overall_status', 'UNKNOWN')))}</strong>; "
        f"counts: {html.escape(str(matrix.get('status_counts', {})))}</p>"
        "<table><tr><th>Status</th><th>Requirement</th><th>Finding</th><th>Evidence files</th></tr>"
        + "\n".join(rows)
        + "</table>"
    )


def _acceptance_matrix_markdown_lines(matrix: dict[str, Any]) -> list[str]:
    if not matrix or "_missing" in matrix:
        return ["Acceptance matrix has not been generated yet."]
    lines = [
        f"- Overall: **{matrix.get('overall_status', 'UNKNOWN')}**",
        f"- Counts: `{matrix.get('status_counts', {})}`",
        "",
        "| Status | Requirement | Finding |",
        "| --- | --- | --- |",
    ]
    for item in matrix.get("items", []):
        lines.append(f"| {item.get('status')} | {item.get('requirement')} | {item.get('finding')} |")
    return lines


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksums(out_dir: Path) -> str:
    lines = []
    for path in sorted(item for item in out_dir.rglob("*") if item.is_file() and item.name != "SHA256SUMS.txt"):
        lines.append(f"{_sha256(path)}  {path.relative_to(out_dir)}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
