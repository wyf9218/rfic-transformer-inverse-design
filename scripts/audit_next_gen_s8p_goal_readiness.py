#!/usr/bin/env python3
"""Audit end-to-end readiness for the next-generation S8P goal.

This is a read-only evidence gate. It maps the current project state to the
post-meeting objective:

1. inverse input must be physical features Lp/Ls/Q/K, not Zin;
2. geometry generation must use the 8-port vertical power-line topology;
3. the 500-sample EMX run must use 8 workers and export .s8p;
4. a random sample must be handed off to HFSS and compared against EMX;
5. unclear scientific assumptions must be called out explicitly.

It does not run EMX, HFSS, ADS, Cadence, or MARS.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfic_transformer_inverse_design.core import load_run_config  # noqa: E402


REQUIRED_SCRIPTS = (
    "build_next_gen_s8p_mars_execution_packet.py",
    "prepare_final_s8p_physical_feature_config.py",
    "build_physical_feature_inverse_training_table.py",
    "predict_geometry_from_physical_features.py",
    "audit_physical_feature_inverse_model_quality.py",
    "train_physical_feature_inverse_model.py",
    "predict_geometry_with_saved_inverse_model.py",
    "derive_scalar_q_feature.py",
    "run_candidate_queue_dataset.py",
    "run_candidate_queue_dataset_parallel.py",
    "audit_s8p_physical_feature_dataset.py",
    "select_physical_feature_validation_samples.py",
    "plan_physical_feature_balanced_acquisition.py",
    "audit_s8p_port_pair_physical_candidates.py",
    "audit_selected_power_line_8port_layout_samples.py",
    "build_selected_s8p_hfss_handoff_packet.py",
    "build_s8p_hfss_aedt_scripts_from_handoff.py",
    "render_hfss_model_views_from_payload.py",
    "run_s8p_hfss_postrun_validation_from_aedt_packet.py",
    "build_s8p_final_report_evidence_packet.py",
)
PHYSICAL_FEATURE_COLUMN_SETS = (
    ("lp_nh_center", "ls_nh_center", "q_center", "k_center"),
    ("lp_nh_center", "ls_nh_center", "qp_center", "qs_center", "k_center"),
)
POWER_LINE_8PORT_GROUND_FRAME_POLICY = (
    "power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame"
)
SCRIPT_CONTRACT_MARKERS = {
    "train_physical_feature_inverse_model.py": (
        "saved inverse-model training records and gates the Lp/Ls/Q/K target feature envelope",
        (
            "input_domain",
            "_target_feature_envelope_checks",
            "target_features_inside_training_envelope",
            "--allow-target-extrapolation",
        ),
    ),
    "predict_geometry_with_saved_inverse_model.py": (
        "saved inverse-model prediction rejects Lp/Ls/Q/K target extrapolation by default",
        (
            "target_feature_envelope",
            "saved_model_target_features_inside_training_envelope",
            "--allow-target-extrapolation",
        ),
    ),
}


@dataclass(frozen=True)
class Evidence:
    status: str
    requirement: str
    evidence: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "requirement": self.requirement,
            "evidence": self.evidence,
            "next_action": self.next_action,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = _resolve_paths(args)
    evidence = []
    evidence.extend(_script_evidence(repo_root))
    evidence.extend(_config_evidence(paths["config"], args))
    evidence.extend(_combined_approval_readiness_evidence(paths["combined_approval_readiness_summary"]))
    launch_summary = _read_json(paths["launch_packet_summary"])
    evidence.extend(_launch_packet_evidence(paths["launch_packet_summary"], launch_summary, args))
    inverse_training_manifest = _default_inverse_training_manifest(
        paths["launch_packet_summary"],
        launch_summary,
        paths["inverse_training_manifest"],
    )
    inverse_prediction_summary = _default_inverse_prediction_summary(
        paths["launch_packet_summary"],
        launch_summary,
        paths["inverse_prediction_summary"],
    )
    evidence.extend(_inverse_geometry_contract_evidence(inverse_training_manifest, inverse_prediction_summary))
    inverse_model_quality_summary = _default_inverse_model_quality_summary(
        paths["launch_packet_summary"],
        launch_summary,
        paths["inverse_model_quality_summary"],
    )
    paths["inverse_model_quality_summary"] = inverse_model_quality_summary
    evidence.extend(_inverse_model_quality_evidence(inverse_model_quality_summary))
    saved_inverse_model_summary = _default_saved_inverse_model_summary(
        paths["launch_packet_summary"],
        launch_summary,
        paths["saved_inverse_model_summary"],
    )
    paths["saved_inverse_model_summary"] = saved_inverse_model_summary
    evidence.extend(_saved_inverse_model_evidence(saved_inverse_model_summary))
    evidence.extend(_candidate_run_evidence(paths["candidate_run_dir"], args))
    quality_summary_path = _default_quality_summary(paths["candidate_run_dir"], paths["dataset_quality_summary"])
    paths["dataset_quality_summary"] = quality_summary_path
    port_pair_candidate_audit_summary = _default_port_pair_candidate_audit_summary(
        paths["candidate_run_dir"],
        paths["port_pair_candidate_audit_summary"],
    )
    paths["port_pair_candidate_audit_summary"] = port_pair_candidate_audit_summary
    evidence.extend(_quality_gate_evidence(quality_summary_path))
    evidence.extend(_port_pair_candidate_audit_evidence(port_pair_candidate_audit_summary))
    evidence.extend(_handoff_evidence(paths["selected_handoff_summary"]))
    evidence.extend(_aedt_packet_evidence(paths["aedt_packet_summary"]))
    evidence.extend(_hfss_payload_render_evidence(paths["hfss_payload_render_summary"]))
    evidence.extend(_postrun_evidence(paths["postrun_validation_summary"]))
    evidence.extend(_explicit_question_evidence(launch_summary, paths, args))

    overall_status = _overall_status(evidence)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": _decision(overall_status),
        "repo_root": str(repo_root),
        "paths": {key: "" if value is None else str(value) for key, value in paths.items()},
        "expected": {
            "sample_count": int(args.expected_sample_count),
            "jobs": int(args.expected_jobs),
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "bridge_width_um": float(args.expected_bridge_width_um),
            "ground_frame_width_um": float(args.expected_ground_frame_width_um),
            "ground_frame_policy": POWER_LINE_8PORT_GROUND_FRAME_POLICY,
            "require_scalar_q": bool(args.require_scalar_q),
        },
        "status_counts": _status_counts(evidence),
        "evidence": [item.as_dict() for item in evidence],
        "limitations": [
            "This audit verifies local files and summaries only; it does not run EMX, HFSS, ADS, Cadence, or MARS.",
            "A PASS requires the supplied summaries to say PASS; it still relies on the provenance of those artifacts being real simulator output.",
            "Missing MARS/HFSS artifacts are reported as WAITING, not as fabricated validation.",
        ],
    }
    summary_path = out_dir / "next_gen_s8p_goal_readiness_summary.json"
    report_path = out_dir / "next_gen_s8p_goal_readiness_report.md"
    csv_path = out_dir / "next_gen_s8p_goal_readiness_evidence.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    _write_csv(csv_path, evidence)

    print(f"overall_status={overall_status}")
    print(f"decision={summary['decision']}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"evidence_csv={csv_path}")
    for item in evidence:
        print(f"{item.status:8s} {item.requirement}: {item.evidence}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root")
    parser.add_argument("--config", help="Final S8P physical-feature run config")
    parser.add_argument("--combined-approval-readiness-summary", help="s8p_combined_approval_readiness_summary.json")
    parser.add_argument("--launch-packet-summary", help="physical_feature_s8p_launch_packet_summary.json")
    parser.add_argument("--inverse-training-manifest", help="physical_feature_inverse_training_manifest.json")
    parser.add_argument("--inverse-prediction-summary", help="physical_feature_inverse_prediction_summary.json")
    parser.add_argument("--inverse-model-quality-summary", help="physical_feature_inverse_model_quality_summary.json")
    parser.add_argument("--saved-inverse-model-summary", help="physical_feature_inverse_model_training_summary.json")
    parser.add_argument("--candidate-run-dir", help="New 500-row EMX candidate run directory")
    parser.add_argument("--dataset-quality-summary", help="dataset_quality_gates_summary.json")
    parser.add_argument("--port-pair-candidate-audit-summary", help="s8p_port_pair_physical_candidate_audit_summary.json")
    parser.add_argument("--selected-handoff-summary", help="selected_s8p_hfss_handoff_summary.json")
    parser.add_argument("--aedt-packet-summary", help="hfss_s8p_aedt_script_packet_summary.json")
    parser.add_argument("--hfss-payload-render-summary", help="hfss_payload_geometry_render_batch_summary.json")
    parser.add_argument("--postrun-validation-summary", help="s8p_hfss_postrun_validation_summary.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-sample-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=1.0)
    parser.add_argument("--expected-frequency-points", type=int, default=56)
    parser.add_argument("--expected-bridge-width-um", type=float, default=10.0)
    parser.add_argument("--bridge-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument("--expected-ground-frame-width-um", type=float, default=100.0)
    parser.add_argument("--ground-frame-width-tolerance-um", type=float, default=1.0e-12)
    parser.add_argument(
        "--require-scalar-q",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require a single scalar Q feature such as q_center for the final Lp/Ls/Q/K objective.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> dict[str, Path | None]:
    fields = (
        "config",
        "combined_approval_readiness_summary",
        "launch_packet_summary",
        "inverse_training_manifest",
        "inverse_prediction_summary",
        "inverse_model_quality_summary",
        "saved_inverse_model_summary",
        "candidate_run_dir",
        "dataset_quality_summary",
        "port_pair_candidate_audit_summary",
        "selected_handoff_summary",
        "aedt_packet_summary",
        "hfss_payload_render_summary",
        "postrun_validation_summary",
    )
    return {field: _path(getattr(args, field)) for field in fields}


def _path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _script_evidence(repo_root: Path) -> list[Evidence]:
    items = []
    for name in REQUIRED_SCRIPTS:
        path = repo_root / "scripts" / name
        exists = path.is_file()
        items.append(
            Evidence(
                "PASS" if exists else "FAIL",
                f"required script exists: {name}",
                str(path),
                "Restore or implement this script before claiming the new flow is automated.",
            )
        )
        if name in SCRIPT_CONTRACT_MARKERS:
            requirement, markers = SCRIPT_CONTRACT_MARKERS[name]
            text = path.read_text(encoding="utf-8") if exists else ""
            missing = [marker for marker in markers if marker not in text]
            items.append(
                Evidence(
                    "PASS" if exists and not missing else "FAIL",
                    requirement,
                    f"script={path}, missing_markers={missing}",
                    "Keep the saved inverse model inside the real training physical-feature envelope unless extrapolation is explicitly approved.",
                )
            )
    return items


def _config_evidence(config_path: Path | None, args: argparse.Namespace) -> list[Evidence]:
    requirement = "8-port vertical power-line run config is finalized"
    if config_path is None:
        return [
            Evidence(
                "QUESTION",
                requirement,
                "No --config supplied.",
                "Provide the final MARS S8P config with confirmed P001-P008 port map, PDK layers, and differential pairs.",
            )
        ]
    if not config_path.is_file():
        return [Evidence("FAIL", requirement, f"Missing config: {config_path}", "Create or pass the final config path.")]
    try:
        raw_text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return [Evidence("FAIL", requirement, f"{type(exc).__name__}: {exc}", "Fix the config file so it can be read.")]
    evidence = []
    placeholders = _placeholder_hits(raw_text)
    evidence.append(
        Evidence(
            "FAIL" if placeholders else "PASS",
            "config has no unresolved TODO/REPLACE placeholders",
            "; ".join(placeholders[:6]) if placeholders else str(config_path),
            "Replace every TODO/REPLACE/TBD marker before running MARS.",
        )
    )
    try:
        cfg = load_run_config(config_path)
    except Exception as exc:  # noqa: BLE001
        evidence.append(Evidence("FAIL", requirement, f"{type(exc).__name__}: {exc}", "Fix the config until load_run_config() succeeds."))
        return evidence
    evidence.append(
        Evidence(
            "PASS",
            requirement,
            f"loaded config: {config_path}",
            "No action.",
        )
    )
    spec = cfg.emx.power_line_8port
    shield = cfg.bounds.shield
    shield_width_um = 0.0 if shield.width_um is None else float(shield.width_um)
    shield_margin_um = 0.0 if shield.margin_um is None else float(shield.margin_um)
    ground_frame_width_um = max(shield_width_um, shield_margin_um)
    config_checks = [
        (
            bool(spec.enabled),
            "power_line_8port.enabled is true",
            str(bool(spec.enabled)),
            "Enable emx.power_line_8port.",
        ),
        (
            str(cfg.emx.port_mode) == "single_ended_shield_grounded",
            "port mode is single_ended_shield_grounded",
            str(cfg.emx.port_mode),
            "Use the grounded shield port mode requested by Henry.",
        ),
        (
            cfg.emx.differential_port_pairs is not None,
            "differential port pairs are explicit",
            str(cfg.emx.differential_port_pairs),
            "Confirm and record the P001-P008 differential port-pair map.",
        ),
        (
            spec.bridge_width_um is not None
            and abs(float(spec.bridge_width_um) - float(args.expected_bridge_width_um)) <= float(args.bridge_width_tolerance_um),
            "bridge width matches vertical power-line width",
            str(spec.bridge_width_um),
            "Set emx.power_line_8port.bridge_width_um to the vertical power-line width.",
        ),
        (
            abs(float(spec.vertical_length_diameter_ratio) - 1.5) <= 1.0e-12,
            "vertical power-line length is 1.5x max coil height",
            str(spec.vertical_length_diameter_ratio),
            "Set vertical_length_diameter_ratio to 1.5.",
        ),
        (
            str(spec.bridge_y_policy) == "center" and str(spec.bridge_motion_axis) == "x_only",
            "bridge is centered horizontally and moves only in x",
            f"bridge_y_policy={spec.bridge_y_policy}, bridge_motion_axis={spec.bridge_motion_axis}",
            "Use bridge_y_policy=center and bridge_motion_axis=x_only.",
        ),
        (
            str(spec.port_ground_reference) == "shield",
            "ports are shield-ground referenced",
            str(spec.port_ground_reference),
            "Set port_ground_reference=shield.",
        ),
        (
            bool(shield.enabled) and str(shield.kind) == "ring",
            "rectangular ground frame shield is enabled",
            str(shield.as_dict()),
            "Enable topology/bounds shield so the white inner window is surrounded by M5 ground.",
        ),
        (
            abs(float(ground_frame_width_um) - float(args.expected_ground_frame_width_um)) <= float(args.ground_frame_width_tolerance_um),
            "ground frame width derives to expected rectangular shield frame",
            f"derived={ground_frame_width_um} um from shield_width={shield_width_um} um and shield_margin={shield_margin_um} um",
            "Set shield margin/width so max(width_um, margin_um) equals the approved ground-frame width.",
        ),
        (
            bool(spec.enabled) and ground_frame_width_um > 0.0,
            "ground frame policy is rectangular shield frame",
            POWER_LINE_8PORT_GROUND_FRAME_POLICY,
            "Keep the derived rectangular shield-frame policy for power_line_8port layouts.",
        ),
        (
            cfg.bounds.primary.vdd_bar is not None
            and cfg.bounds.primary.vdd_bar.bar_layer is not None
            and int(cfg.bounds.primary.vdd_bar.bar_layer) == int(cfg.emx.ap_layer),
            "primary vertical power-line layer matches primary coil layer",
            f"power_line_layer={None if cfg.bounds.primary.vdd_bar is None else cfg.bounds.primary.vdd_bar.bar_layer}, primary_coil_layer={cfg.emx.ap_layer}",
            "Set primary vdd_bar.bar_layer to emx.ap_layer.",
        ),
        (
            cfg.bounds.secondary.vdd_bar is not None
            and cfg.bounds.secondary.vdd_bar.bar_layer is not None
            and int(cfg.bounds.secondary.vdd_bar.bar_layer) == int(cfg.emx.m9_layer),
            "secondary vertical power-line layer matches secondary coil layer",
            f"power_line_layer={None if cfg.bounds.secondary.vdd_bar is None else cfg.bounds.secondary.vdd_bar.bar_layer}, secondary_coil_layer={cfg.emx.m9_layer}",
            "Set secondary vdd_bar.bar_layer to emx.m9_layer.",
        ),
        (
            len(tuple(spec.port_map)) == 8 and len(set(tuple(spec.port_map))) == 8 and not any(_looks_placeholder(x) for x in spec.port_map),
            "P001-P008 port map is explicit and non-placeholder",
            str(tuple(spec.port_map)),
            "Replace placeholder port labels with the approved eight physical port labels.",
        ),
    ]
    for passed, name, detail, action in config_checks:
        evidence.append(Evidence("PASS" if passed else "FAIL", name, detail, action))
    for role, inductor in (("primary", cfg.bounds.primary), ("secondary", cfg.bounds.secondary)):
        vdd = inductor.vdd_bar
        passed = bool(inductor.center_tap) and vdd is not None and bool(vdd.enabled) and vdd.bar_layer is not None
        evidence.append(
            Evidence(
                "PASS" if passed else "FAIL",
                f"{role} center tap and vertical power-line layer are defined",
                f"center_tap={inductor.center_tap}, vdd_bar={vdd}",
                f"Confirm {role} vdd_bar layer against the PDK stack and set center_tap=true.",
            )
        )
    freq = cfg.target.frequency_points_hz()
    if len(freq) >= 2:
        detail = f"{freq[0] / 1e9:.12g}-{freq[-1] / 1e9:.12g} GHz, step={(freq[1] - freq[0]) / 1e9:.12g} GHz, points={len(freq)}"
        passed = (
            abs(float(freq[0]) - float(args.expected_frequency_start_ghz) * 1.0e9) <= 1.0
            and abs(float(freq[-1]) - float(args.expected_frequency_stop_ghz) * 1.0e9) <= 1.0
            and abs(float(freq[1] - freq[0]) - float(args.expected_frequency_step_ghz) * 1.0e9) <= 1.0
            and int(len(freq)) == int(args.expected_frequency_points)
        )
    else:
        detail = f"points={len(freq)}"
        passed = False
    evidence.append(
        Evidence(
            "PASS" if passed else "FAIL",
            "frequency grid is 5-60 GHz with 1.0 GHz step and 56 points",
            detail,
            "Fix target.frequency_start_hz/frequency_stop_hz/frequency_step_hz/band_points.",
        )
    )
    return evidence


def _combined_approval_readiness_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "combined port-map and geometry approval readiness is recorded"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No --combined-approval-readiness-summary supplied.",
                "Generate s8p_combined_approval_readiness_summary.json and review its approval board before running real EMX.",
            )
        ]
    if not summary_path.is_file():
        return [
            Evidence(
                "FAIL",
                requirement,
                f"Missing {summary_path}",
                "Regenerate the combined approval-readiness packet.",
            )
        ]
    summary = _read_json(summary_path)
    state = summary.get("approval_state") or {}
    artifacts = summary.get("artifacts") or {}
    visuals = summary.get("visual_artifacts") or {}
    board_raw = str(visuals.get("approval_board") or artifacts.get("approval_board_png") or "")
    board_path = Path(board_raw).expanduser() if board_raw else None
    if board_path is not None and not board_path.is_absolute():
        board_path = summary_path.parent / board_path
    can_start = bool(summary.get("can_start_real_emx"))
    all_approval_flags = (
        state.get("port_map_approved") is True
        and state.get("geometry_contract_approved") is True
        and state.get("mars_execution_packet_ready") is True
    )
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, decision={summary.get('decision')}",
            "Fix port-map/geometry approval packets until the combined review packet is structurally PASS.",
        ),
        Evidence(
            "PASS" if can_start and all_approval_flags else "WAITING",
            "combined approval gate allows real EMX start",
            f"can_start_real_emx={summary.get('can_start_real_emx')}, approval_state={state}",
            "Get user/advisor approval for both port map and geometry contract, then regenerate approved summaries and strict-path MARS packet.",
        ),
        Evidence(
            "PASS" if board_path is not None and board_path.is_file() else "WAITING",
            "combined approval board PNG exists for advisor review",
            "" if board_path is None else str(board_path),
            "Regenerate the combined approval-readiness packet so the visual board is available in the MARS handoff evidence.",
        ),
    ]


def _launch_packet_evidence(summary_path: Path | None, summary: dict[str, Any], args: argparse.Namespace) -> list[Evidence]:
    requirement = "physical-feature inverse launch packet is ready"
    if summary_path is None:
        return [Evidence("WAITING", requirement, "No --launch-packet-summary supplied.", "Generate the launch packet before MARS execution.")]
    if not summary_path.is_file() or not summary:
        return [Evidence("FAIL", requirement, f"Missing or unreadable: {summary_path}", "Regenerate the launch packet summary.")]
    launch_packet_status = (
        "PASS"
        if summary.get("overall_status") == "PASS"
        else "WAITING"
        if _launch_packet_waits_only_on_approval(summary)
        else "FAIL"
    )
    items = [
        Evidence(
            launch_packet_status,
            requirement,
            f"overall_status={summary.get('overall_status')}, decision={summary.get('decision')}, failed_checks={_failed_launch_check_names(summary)}",
            "Get user/advisor approval for candidate port-map/geometry gates before running generated commands."
            if launch_packet_status == "WAITING"
            else "Fix launch-packet checks before running generated commands.",
        )
    ]
    feature_columns = list(summary.get("feature_columns") or [])
    items.append(_feature_column_evidence(feature_columns, args))
    items.append(_no_zin_input_evidence(feature_columns))
    items.append(_scalar_q_evidence(feature_columns, args))
    items.append(_parallel_launch_contract_evidence(summary, args))
    commands = summary.get("commands") or []
    joined_commands = "\n".join(" ".join(item.get("command") or []) for item in commands if isinstance(item, dict))
    candidate_source_mode = str(summary.get("candidate_source_mode") or "")
    bootstrap_mode = candidate_source_mode == "bootstrap_geometry_queue" or "build_s8p_geometry_bootstrap_candidate_queue.py" in joined_commands
    if args.require_scalar_q:
        scalar_q_passed = "--derive-scalar-q-feature" in joined_commands and "--scalar-q-definition" in joined_commands
        items.append(
            Evidence(
                "PASS" if scalar_q_passed else "FAIL",
                "launch packet explicitly derives scalar Q before inverse training",
                "command contract checked",
                "Regenerate launch packet with --scalar-q-definition so run_dataset_quality_gates.py derives q_center from Qp/Qs.",
            )
        )
    smoke_index = _first_command_index(commands, "run_candidate_queue_dataset.py", "--create-only", "--max-count", "1")
    smoke_audit_index = _first_command_index(commands, "audit_selected_power_line_8port_layout_samples.py", "layout_smoke_8port_audit")
    emx_index = _first_command_index(commands, "run_candidate_queue_dataset_parallel.py")
    quality_index = _first_command_index(commands, "run_dataset_quality_gates.py", "--audit-s8p-physical-feature-dataset")
    coverage_plan_index = _first_command_index(commands, "plan_physical_feature_balanced_acquisition.py")
    post_emx_inverse_training_index = _first_command_index(commands, "build_physical_feature_inverse_training_table.py", "scalar_q_feature_dataset")
    post_emx_inverse_quality_index = _first_command_index(commands, "audit_physical_feature_inverse_model_quality.py")
    post_emx_saved_inverse_model_index = _first_command_index(commands, "train_physical_feature_inverse_model.py")
    postrun_validation_index = _first_command_index(commands, "run_s8p_hfss_postrun_validation_from_aedt_packet.py")
    final_report_packet_index = _first_command_index(commands, "build_s8p_final_report_evidence_packet.py")
    inverse_artifacts = summary.get("inverse_model_artifacts") or {}
    target_predictions_artifact = str(inverse_artifacts.get("post_emx_saved_inverse_target_predictions") or "").strip()
    target_layout_smoke_artifact = str(inverse_artifacts.get("post_emx_saved_inverse_target_layout_smoke_summary") or "").strip()
    target_smoke_index = _first_command_index(
        commands,
        "run_candidate_queue_dataset.py",
        "physical_feature_inverse_model_target_predictions.csv",
        "physical_feature_saved_inverse_target_layout_smoke",
        "--create-only",
    )
    has_inverse_targets = bool(summary.get("targets") or summary.get("inverse_targets"))
    command_checks = [
        (
            smoke_index is not None and "layout_smoke_create_only" in joined_commands,
            "launch packet includes one-sample create-only layout smoke",
            "Insert run_candidate_queue_dataset.py --max-count 1 --create-only before the 500-row EMX command.",
        ),
        (
            smoke_index is not None and smoke_audit_index is not None and emx_index is not None and smoke_index < smoke_audit_index < emx_index,
            "launch packet audits smoke 8-port layout before EMX",
            "Audit layout_smoke_create_only with audit_selected_power_line_8port_layout_samples.py before run_candidate_queue_dataset_parallel.py.",
        ),
        (
            "audit_selected_power_line_8port_layout_samples.py" in joined_commands
            and "--internal-angle-deg" in joined_commands
            and re.search(r"--internal-angle-deg\s+135(?:\.0+)?\b", joined_commands) is not None
            and "--terminal-angle-deg" in joined_commands
            and re.search(r"--terminal-angle-deg\s+90(?:\.0+)?\b", joined_commands) is not None
            and "--angle-tolerance-deg" in joined_commands,
            "launch packet explicitly gates winding 135deg and terminal 90deg geometry",
            "Pass --internal-angle-deg 135 --terminal-angle-deg 90 --angle-tolerance-deg to audit_selected_power_line_8port_layout_samples.py before EMX.",
        ),
        (
            "run_candidate_queue_dataset_parallel.py" in joined_commands,
            "launch packet uses parallel candidate queue EMX runner",
            "Use run_candidate_queue_dataset_parallel.py for the 8-worker 500-row run.",
        ),
        (
            "--resume-completed" in joined_commands,
            "launch packet can resume completed EMX shards",
            "Pass --resume-completed to run_candidate_queue_dataset_parallel.py so interrupted MARS runs only rerun incomplete shards.",
        ),
        (
            "audit_power_line_8port_contract.py" in joined_commands
            and "--expected-ground-frame-width-um" in joined_commands
            and POWER_LINE_8PORT_GROUND_FRAME_POLICY in joined_commands,
            "launch packet audits rectangular ground frame before EMX",
            "Run audit_power_line_8port_contract.py with expected ground-frame width/policy before launching EMX.",
        ),
        (
            re.search(r"--jobs\s+8\b", joined_commands) is not None or str(summary.get("arguments", {}).get("jobs")) == str(args.expected_jobs),
            "launch packet requests 8 workers",
            "Regenerate with --jobs 8.",
        ),
        (
            re.search(r"--expected-jobs\s+8\b", joined_commands) is not None
            or str((summary.get("parallel_emx_contract") or {}).get("expected_jobs")) == str(args.expected_jobs),
            "launch packet requires expected 8-worker audit",
            "Regenerate launch packet so run_candidate_queue_dataset_parallel.py receives --expected-jobs 8.",
        ),
        (
            re.search(r"--max-count\s+500\b", joined_commands) is not None
            or str(summary.get("arguments", {}).get("emx_max_count")) == str(args.expected_sample_count),
            "launch packet requests 500 EMX samples",
            "Regenerate with --emx-max-count 500.",
        ),
        (
            re.search(r"--expected-count\s+500\b", joined_commands) is not None
            or str((summary.get("parallel_emx_contract") or {}).get("expected_emx_count")) == str(args.expected_sample_count),
            "launch packet requires expected 500-row merge audit",
            "Regenerate launch packet so run_candidate_queue_dataset_parallel.py receives --expected-count 500.",
        ),
        (
            "--force-wideband-5-60-1p0" in joined_commands,
            "launch packet forces 5-60 GHz / 1.0 GHz grid",
            "Keep --force-wideband-5-60-1p0 in the candidate runner command.",
        ),
        (
            quality_index is not None
            and coverage_plan_index is not None
            and quality_index < coverage_plan_index
            and "scalar_q_feature_dataset" in joined_commands,
            "launch packet plans Lp/Ls/Q/K coverage after S8P feature extraction",
            "Run plan_physical_feature_balanced_acquisition.py on scalar_q_feature_dataset after run_dataset_quality_gates.py.",
        ),
        (
            quality_index is not None
            and post_emx_inverse_training_index is not None
            and quality_index < post_emx_inverse_training_index
            and "physical_feature_inverse_training_table.py" in joined_commands
            and "--config" in joined_commands,
            "launch packet builds post-EMX Lp/Ls/Q/K inverse training table",
            "Run build_physical_feature_inverse_training_table.py on scalar_q_feature_dataset with --config after S8P feature extraction.",
        ),
        (
            post_emx_inverse_training_index is not None
            and post_emx_inverse_quality_index is not None
            and post_emx_inverse_training_index < post_emx_inverse_quality_index
            and "--training-csv" in joined_commands,
            "launch packet audits post-EMX inverse model quality",
            "Run audit_physical_feature_inverse_model_quality.py after the post-EMX inverse training table is built.",
        ),
        (
            post_emx_inverse_training_index is not None
            and post_emx_inverse_quality_index is not None
            and post_emx_saved_inverse_model_index is not None
            and post_emx_inverse_training_index < post_emx_inverse_quality_index < post_emx_saved_inverse_model_index
            and "--config" in joined_commands,
            "launch packet trains saved post-EMX Lp/Ls/Q/K inverse model artifact",
            "Run train_physical_feature_inverse_model.py after inverse table and quality audit so a reusable model JSON is saved.",
        ),
        (
            post_emx_saved_inverse_model_index is not None
            and "--target-json" in joined_commands
            and (
                "physical_feature_inverse_model_target_predictions.csv" in joined_commands
                or target_predictions_artifact.endswith("physical_feature_inverse_model_target_predictions.csv")
            ),
            "launch packet saves target predictions from Lp/Ls/Q/K inverse model",
            "Keep train_physical_feature_inverse_model.py --target-json and the target-prediction CSV artifact so physical targets can become geometry candidates.",
        ),
        (
            "build_selected_s8p_hfss_handoff_packet.py" in joined_commands,
            "launch packet includes selected-sample HFSS handoff",
            "Include selected-sample handoff generation after quality gates.",
        ),
        (
            "build_s8p_hfss_aedt_scripts_from_handoff.py" in joined_commands,
            "launch packet includes HFSS AEDT script generation",
            "Include AEDT/PyAEDT script generation after handoff.",
        ),
        (
            "render_hfss_model_views_from_payload.py" in joined_commands,
            "launch packet renders HFSS payload geometry views",
            "Render hfss_s8p_build_payload.json views after AEDT script generation.",
        ),
        (
            postrun_validation_index is not None
            and final_report_packet_index is not None
            and postrun_validation_index < final_report_packet_index
            and "--quality-dir" in joined_commands
            and "s8p_final_report_evidence_packet" in joined_commands,
            "launch packet builds final report evidence packet after EMX/HFSS validation",
            "Run build_s8p_final_report_evidence_packet.py after postrun validation so report figures/tables have a manifest and PASS/WAITING/FAIL status.",
        ),
    ]
    if bootstrap_mode:
        command_checks.append(
            (
                "build_s8p_geometry_bootstrap_candidate_queue.py" in joined_commands and "--config" in joined_commands,
                "launch packet uses config-backed bootstrap geometry candidate queue",
                "Bootstrap mode must build the first 500 geometry candidates from the finalized S8P config before any EMX labels exist.",
            )
        )
    else:
        command_checks.append(
            (
                "--inverse-geometry-config" in joined_commands,
                "launch packet validates inverse geometry candidates with config",
                "Pass --inverse-geometry-config to run_dataset_quality_gates.py so training/prediction keep complete adapter field order.",
            )
        )
    if has_inverse_targets:
        command_checks.append(
            (
                post_emx_saved_inverse_model_index is not None
                and target_smoke_index is not None
                and post_emx_saved_inverse_model_index < target_smoke_index
                and target_layout_smoke_artifact.endswith("candidate_queue_dataset_summary.json"),
                "launch packet validates saved-model target geometry with create-only layout smoke",
                "When explicit Lp/Ls/Q/K targets are supplied, run_candidate_queue_dataset.py must create-only rebuild physical_feature_inverse_model_target_predictions.csv after saved-model training.",
            )
        )
    else:
        command_checks.append(
            (
                target_smoke_index is None,
                "launch packet keeps saved-model target layout smoke conditional on explicit Lp/Ls/Q/K targets",
                "Do not create target-geometry layout evidence unless the launch packet has explicit inverse targets.",
            )
        )
    for passed, name, action in command_checks:
        items.append(Evidence("PASS" if passed else "FAIL", name, "command contract checked", action))
    return items


def _launch_packet_waits_only_on_approval(summary: dict[str, Any]) -> bool:
    if summary.get("overall_status") == "PASS":
        return False
    failed = _failed_launch_check_names(summary)
    approval_gate_names = {
        "port_map_approval_summary_approved",
        "geometry_contract_approval_summary_approved",
    }
    return bool(failed) and all(name in approval_gate_names for name in failed)


def _failed_launch_check_names(summary: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for item in summary.get("checks") or []:
        if not isinstance(item, dict):
            continue
        passed = item.get("pass")
        status = item.get("status")
        if passed is False or (isinstance(status, str) and status.upper() == "FAIL"):
            failed.append(str(item.get("name") or "unnamed_check"))
    return failed


def _inverse_geometry_contract_evidence(
    training_manifest_path: Path | None,
    prediction_summary_path: Path | None,
) -> list[Evidence]:
    requirement = "physical-feature inverse candidates rebuild into config geometry"
    if training_manifest_path is None and prediction_summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No inverse training manifest or prediction summary supplied/inferable.",
                "Run the inverse table and prediction steps from the launch packet.",
            )
        ]
    evidence: list[Evidence] = []
    if training_manifest_path is None or not training_manifest_path.is_file():
        evidence.append(
            Evidence(
                "WAITING",
                "inverse training table keeps complete config geometry field order",
                "" if training_manifest_path is None else f"Missing {training_manifest_path}",
                "Run build_physical_feature_inverse_training_table.py with --config.",
            )
        )
    else:
        manifest = _read_json(training_manifest_path)
        geometry_contract = manifest.get("geometry_contract") or {}
        input_contract = manifest.get("input_feature_contract") or {}
        field_order = list(geometry_contract.get("field_order") or [])
        geometry_columns = list(geometry_contract.get("geometry_columns") or manifest.get("geometry_columns") or [])
        no_zin = not list(input_contract.get("zin_columns") or [])
        has_features = all(
            bool(input_contract.get(key))
            for key in ("lp_columns", "ls_columns", "q_columns", "k_columns")
        )
        complete_geometry = bool(field_order) and len(field_order) == len(geometry_columns)
        evidence.extend(
            [
                Evidence(
                    "PASS" if manifest.get("overall_status") == "PASS" else "FAIL",
                    "inverse training table summary passes",
                    f"overall_status={manifest.get('overall_status')}, training_count={manifest.get('training_count')}",
                    "Fix inverse training table rejects before predicting geometry.",
                ),
                Evidence(
                    "PASS" if no_zin and has_features else "FAIL",
                    "inverse training table input contract is Lp/Ls/Q/K without Zin",
                    f"input_feature_contract={input_contract}",
                    "Rebuild inverse training table with Lp/Ls/Q/K columns and no Zin columns.",
                ),
                Evidence(
                    "PASS" if complete_geometry else "FAIL",
                    "inverse training table keeps complete config geometry field order",
                    f"field_order={field_order}, geometry_columns={geometry_columns}",
                    "Rebuild with --config so constant geometry fields are retained.",
                ),
            ]
        )

    if prediction_summary_path is None or not prediction_summary_path.is_file():
        evidence.append(
            Evidence(
                "WAITING",
                requirement,
                "" if prediction_summary_path is None else f"Missing {prediction_summary_path}",
                "Run predict_geometry_from_physical_features.py with --config.",
            )
        )
        return evidence

    summary = _read_json(prediction_summary_path)
    contract = summary.get("candidate_geometry_contract") or {}
    valid_count = _to_int(contract.get("valid_candidate_count"))
    candidate_count = _to_int(contract.get("candidate_count") or summary.get("candidate_count"))
    missing_rows = list(contract.get("missing_field_rows") or [])
    invalid_rows = list(contract.get("invalid_candidate_rows") or [])
    checks = {str(item.get("name")): item for item in summary.get("checks") or [] if isinstance(item, dict)}
    fields_match_check = checks.get("inverse_geometry_candidate_fields_match_config", {})
    rebuild_check = checks.get("inverse_geometry_candidates_rebuild_from_config", {})
    contract_passed = (
        summary.get("overall_status") == "PASS"
        and candidate_count is not None
        and candidate_count > 0
        and valid_count == candidate_count
        and not missing_rows
        and not invalid_rows
        and fields_match_check.get("pass") is True
        and rebuild_check.get("pass") is True
    )
    evidence.extend(
        [
            Evidence(
                "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
                "inverse geometry prediction summary passes",
                f"overall_status={summary.get('overall_status')}, candidate_count={summary.get('candidate_count')}",
                "Fix prediction/target/config checks before launching EMX candidates.",
            ),
            Evidence(
                "PASS" if contract_passed else "FAIL",
                requirement,
                f"candidate_geometry_contract={contract}",
                "Regenerate predictions with --config until every candidate contains full geom__ fields and validates against config bounds.",
            ),
        ]
    )
    return evidence


def _inverse_model_quality_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "post-EMX inverse model quality audit passes"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No inverse model quality summary supplied/inferable.",
                "Run audit_physical_feature_inverse_model_quality.py after building the post-EMX inverse training table.",
            )
        ]
    if not summary_path.is_file():
        return [
            Evidence(
                "WAITING",
                requirement,
                f"Missing {summary_path}",
                "Run audit_physical_feature_inverse_model_quality.py on physical_feature_inverse_training_table.csv.",
            )
        ]
    summary = _read_json(summary_path)
    input_contract = summary.get("input_feature_contract") or {}
    quality_summary = summary.get("quality_summary") or {}
    zin_columns = list(input_contract.get("zin_columns") or [])
    has_features = all(bool(input_contract.get(key)) for key in ("lp_columns", "ls_columns", "q_columns", "k_columns"))
    has_error_metrics = bool(quality_summary.get("per_geometry")) and _to_int(
        quality_summary.get("training_count") or summary.get("training_count")
    ) is not None
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, training_count={summary.get('training_count')}",
            "Do not claim the Lp/Ls/Q/K inverse model is usable until this leave-one-out quality audit passes.",
        ),
        Evidence(
            "PASS" if not zin_columns and has_features else "FAIL",
            "inverse model quality audit input contract is Lp/Ls/Q/K without Zin",
            f"input_feature_contract={input_contract}",
            "Regenerate the inverse training table/audit with Lp/Ls/Q/K columns and no Zin-derived inputs.",
        ),
        Evidence(
            "PASS" if has_error_metrics else "FAIL",
            "inverse model quality audit reports leave-one-out geometry error metrics",
            f"method={quality_summary.get('method')}, per_geometry_count={len(quality_summary.get('per_geometry') or {})}",
            "Keep the CV prediction CSV and geometry-error CSV as traceable model-quality evidence.",
        ),
    ]


def _saved_inverse_model_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "post-EMX saved Lp/Ls/Q/K inverse model artifact is trained"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No saved inverse model training summary supplied/inferable.",
                "Run train_physical_feature_inverse_model.py after building and auditing the post-EMX inverse training table.",
            )
        ]
    if not summary_path.is_file():
        return [
            Evidence(
                "WAITING",
                requirement,
                f"Missing {summary_path}",
                "Run train_physical_feature_inverse_model.py and keep physical_feature_inverse_model.json plus the training summary.",
            )
        ]
    summary = _read_json(summary_path)
    input_contract = summary.get("input_feature_contract") or {}
    quality_summary = summary.get("quality_summary") or {}
    model_json = Path(str(summary.get("model_json") or "")).expanduser()
    if not model_json.is_absolute():
        model_json = summary_path.parent / model_json
    zin_columns = list(input_contract.get("zin_columns") or [])
    has_features = all(bool(input_contract.get(key)) for key in ("lp_columns", "ls_columns", "q_columns", "k_columns"))
    has_error_metrics = bool(quality_summary.get("per_geometry")) and _to_int(
        quality_summary.get("training_count") or summary.get("training_count")
    ) is not None
    method = str(summary.get("method") or "")
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" and model_json.is_file() else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, model_json={model_json}, exists={model_json.is_file()}",
            "Do not claim physical-feature inverse drawing is model-backed until the saved model JSON exists and training passes.",
        ),
        Evidence(
            "PASS" if not zin_columns and has_features else "FAIL",
            "saved inverse model input contract is Lp/Ls/Q/K without Zin",
            f"input_feature_contract={input_contract}",
            "Retrain the saved inverse model with Lp/Ls/Q/K columns and no Zin-derived inputs.",
        ),
        Evidence(
            "PASS" if method == "standardized_polynomial_ridge_regression" and has_error_metrics else "FAIL",
            "saved inverse model reports reproducible geometry-error metrics",
            f"method={method}, per_geometry_count={len(quality_summary.get('per_geometry') or {})}",
            "Keep the model JSON, CV prediction CSV, geometry-error CSV, and training report as model evidence.",
        ),
    ]


def _parallel_launch_contract_evidence(summary: dict[str, Any], args: argparse.Namespace) -> Evidence:
    contract = summary.get("parallel_emx_contract") or {}
    expected_count = contract.get("expected_emx_count")
    emx_count = contract.get("emx_max_count")
    expected_jobs = contract.get("expected_jobs")
    jobs = contract.get("jobs")
    expected_count_int = _to_int(expected_count)
    emx_count_int = _to_int(emx_count)
    expected_jobs_int = _to_int(expected_jobs)
    jobs_int = _to_int(jobs)
    passed = (
        expected_count_int == int(args.expected_sample_count)
        and emx_count_int == int(args.expected_sample_count)
        and expected_jobs_int == int(args.expected_jobs)
        and jobs_int == int(args.expected_jobs)
    )
    return Evidence(
        "PASS" if passed else "FAIL",
        "launch packet parallel EMX contract is 500 samples with 8 workers",
        f"parallel_emx_contract={contract}",
        "Regenerate build_physical_feature_s8p_launch_packet.py output with emx_max_count=500, expected_emx_count=500, jobs=8, expected_jobs=8.",
    )


def _first_command_index(commands: Any, *needles: str) -> int | None:
    for index, item in enumerate(commands if isinstance(commands, list) else []):
        if not isinstance(item, dict):
            continue
        command_text = " ".join(str(part) for part in (item.get("command") or []))
        if all(needle in command_text for needle in needles):
            return index
    return None


def _feature_column_evidence(feature_columns: list[str], args: argparse.Namespace) -> Evidence:
    if not feature_columns:
        return Evidence(
            "FAIL",
            "inverse-design input is physical features",
            "feature_columns missing",
            "Regenerate launch packet with physical-feature columns.",
        )
    columns = tuple(feature_columns)
    if any(all(column in columns for column in required) for required in PHYSICAL_FEATURE_COLUMN_SETS):
        return Evidence("PASS", "inverse-design input is physical features", f"feature_columns={columns}", "No action.")
    return Evidence(
        "FAIL",
        "inverse-design input is physical features",
        f"feature_columns={columns}",
        "Use Lp/Ls/Q/K feature columns instead of Zin columns.",
    )


def _no_zin_input_evidence(feature_columns: list[str]) -> Evidence:
    zin_columns = [column for column in feature_columns if _is_zin_column(column)]
    return Evidence(
        "PASS" if not zin_columns and bool(feature_columns) else "FAIL",
        "inverse-design input does not use Zin columns",
        f"zin_columns={zin_columns}, feature_columns={tuple(feature_columns)}",
        "Remove Zin/Zin-derived columns from the inverse model input; use Lp/Ls/Q/K instead.",
    )


def _scalar_q_evidence(feature_columns: list[str], args: argparse.Namespace) -> Evidence:
    columns = tuple(feature_columns)
    if not args.require_scalar_q:
        return Evidence(
            "PASS",
            "single-Q definition is explicit for Lp/Ls/Q/K input",
            "scalar-Q requirement disabled by argument",
            "No action.",
        )
    if "q_center" in columns:
        return Evidence(
            "PASS",
            "single-Q definition is explicit for Lp/Ls/Q/K input",
            f"feature_columns={columns}",
            "No action.",
        )
    return Evidence(
        "QUESTION",
        "single-Q definition is explicit for Lp/Ls/Q/K input",
        f"feature_columns={columns or 'unknown'}",
        "Confirm Q definition, then regenerate with --scalar-q-definition min/mean/geometric_mean/harmonic_mean/primary/secondary.",
    )


def _candidate_run_evidence(run_dir: Path | None, args: argparse.Namespace) -> list[Evidence]:
    requirement = "500 new S8P EMX samples are generated with 8 workers"
    if run_dir is None:
        return [Evidence("WAITING", requirement, "No --candidate-run-dir supplied.", "Run the launch-packet commands on MARS.")]
    summary_path = run_dir / "parallel_candidate_queue_dataset_summary.json"
    rows_path = run_dir / "dataset_rows.csv"
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Wait for or rerun the parallel EMX candidate generation.")]
    summary = _read_json(summary_path)
    rows = _read_csv(rows_path)
    row_count = len(rows)
    ok_count = sum(1 for row in rows if _truthy(row.get("ok", "true")))
    jobs = summary.get("jobs_requested")
    expected_jobs = summary.get("expected_jobs")
    expected_count = summary.get("expected_count")
    merged_row_count = summary.get("merged_row_count")
    jobs_int = _to_int(jobs)
    expected_jobs_int = _to_int(expected_jobs)
    expected_count_int = _to_int(expected_count)
    merged_row_count_int = _to_int(merged_row_count)
    evidence = [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            "parallel EMX candidate run summary passes",
            f"overall_status={summary.get('overall_status')}",
            "Inspect parallel worker logs and rerun failed shards.",
        ),
        Evidence(
            "PASS"
            if jobs_int == int(args.expected_jobs) and expected_jobs_int == int(args.expected_jobs)
            else "FAIL",
            "parallel EMX candidate run used 8 workers",
            f"jobs_requested={jobs}, expected_jobs={expected_jobs}",
            "Rerun run_candidate_queue_dataset_parallel.py with --jobs 8 --expected-jobs 8.",
        ),
        Evidence(
            "PASS"
            if expected_count_int == int(args.expected_sample_count)
            and merged_row_count_int == int(args.expected_sample_count)
            else "FAIL",
            "parallel EMX merge audit proves 500 rows",
            f"expected_count={expected_count}, merged_row_count={merged_row_count}",
            "Rerun run_candidate_queue_dataset_parallel.py with --expected-count 500 and ensure every shard output merges.",
        ),
        Evidence(
            "PASS" if row_count >= int(args.expected_sample_count) and ok_count >= int(args.expected_sample_count) else "WAITING",
            requirement,
            f"rows={row_count}, ok={ok_count}, expected={args.expected_sample_count}",
            "Continue or rerun MARS until 500 ok .s8p rows exist.",
        ),
    ]
    return evidence


def _quality_gate_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "S8P dataset quality gates passed before training"
    if summary_path is None:
        return [Evidence("WAITING", requirement, "No quality-gate summary path supplied or inferable.", "Run S8P physical-feature dataset quality gates.")]
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Run run_dataset_quality_gates.py for the candidate run.")]
    summary = _read_json(summary_path)
    steps = summary.get("steps") or []
    has_s8p_gate = any("s8p" in str(step.get("name", "")).lower() or "physical-feature" in str(step.get("name", "")).lower() for step in steps)
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, steps={len(steps)}",
            "Fix failed quality-gate steps before using the dataset.",
        ),
        Evidence(
            "PASS" if has_s8p_gate else "FAIL",
            "quality gates include S8P physical-feature audit",
            f"has_s8p_gate={has_s8p_gate}",
            "Run with --audit-s8p-physical-feature-dataset.",
        ),
    ]


def _port_pair_candidate_audit_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "selected S8P sample has candidate port-pair physical-feature diagnostic"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No --port-pair-candidate-audit-summary supplied.",
                "Run audit_s8p_port_pair_physical_candidates.py after selecting the validation sample.",
            )
        ]
    if not summary_path.is_file():
        return [
            Evidence(
                "WAITING",
                requirement,
                f"Missing {summary_path}",
                "Run audit_s8p_port_pair_physical_candidates.py on the selected EMX .s8p before HFSS handoff.",
            )
        ]
    summary = _read_json(summary_path)
    status = str(summary.get("overall_status"))
    expected_pass = bool(summary.get("expected_port_pairs_all_pass"))
    if status == "PASS" and expected_pass:
        evidence_status = "PASS"
        next_action = "Use the expected port pair for HFSS/ADS validation, while retaining the diagnostic record."
    elif status == "REVIEW":
        evidence_status = "QUESTION"
        next_action = "Review candidate port-pair curves with the advisor before accepting the HFSS handoff port convention."
    else:
        evidence_status = "FAIL"
        next_action = "Fix the selected port-pair convention or rerun candidate diagnostics before HFSS handoff."
    return [
        Evidence(
            evidence_status,
            requirement,
            f"overall_status={status}, expected_port_pairs={summary.get('expected_port_pairs')}, expected_all_pass={expected_pass}",
            next_action,
        )
    ]


def _handoff_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "random validation sample is packaged for HFSS rebuild"
    if summary_path is None:
        return [Evidence("WAITING", requirement, "No --selected-handoff-summary supplied.", "Build selected S8P HFSS handoff packet after sample selection.")]
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Run build_selected_s8p_hfss_handoff_packet.py.")]
    summary = _read_json(summary_path)
    formula_trace = _artifact_path(summary, summary_path, "ads_formula_trace")
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, sample_count={summary.get('sample_count')}",
            "Fix selected-sample layout/handoff checks before HFSS rebuild.",
        ),
        Evidence(
            "PASS" if formula_trace is not None and formula_trace.is_file() else "FAIL",
            "HFSS handoff includes ADS/Python formula trace",
            "" if formula_trace is None else str(formula_trace),
            "Regenerate handoff packet so hfss_ads_formula_trace.md records port pairs and Lp/Ls/Q/K formulas.",
        ),
    ]


def _artifact_path(summary: dict[str, Any], summary_path: Path, key: str) -> Path | None:
    artifacts = summary.get("artifacts") or {}
    raw = artifacts.get(key)
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else (summary_path.parent / path)


def _aedt_packet_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "HFSS automatic build/solve scripts are generated for selected sample"
    if summary_path is None:
        return [Evidence("WAITING", requirement, "No --aedt-packet-summary supplied.", "Generate AEDT/PyAEDT scripts from the handoff packet.")]
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Run build_s8p_hfss_aedt_scripts_from_handoff.py.")]
    summary = _read_json(summary_path)
    return [
        Evidence(
            "PASS" if summary.get("overall_status") == "PASS" else "FAIL",
            requirement,
            f"overall_status={summary.get('overall_status')}, sample_count={summary.get('sample_count')}",
            "Fix GDS/proc/port payload extraction before running HFSS.",
        )
    ]


def _hfss_payload_render_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "HFSS payload geometry views are generated for selected sample"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No --hfss-payload-render-summary supplied.",
                "Render payload geometry views from the AEDT packet before using images in a report.",
            )
        ]
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Run render_hfss_model_views_from_payload.py.")]
    summary = _read_json(summary_path)
    status = summary.get("overall_status")
    count = int(summary.get("rendered_count") or 0)
    paths = [Path(str(path)).expanduser() for path in (summary.get("summary_paths") or [])]
    all_exist = bool(paths) and all(path.is_file() for path in paths)
    passed = status == "PASS" and count > 0 and all_exist
    return [
        Evidence(
            "PASS" if passed else "FAIL",
            requirement,
            f"overall_status={status}, rendered_count={count}, summaries_exist={all_exist}",
            "Regenerate HFSS payload geometry views and keep the per-sample render summaries.",
        )
    ]


def _postrun_evidence(summary_path: Path | None) -> list[Evidence]:
    requirement = "EMX and HFSS S8P Lp/Ls/Q/K/Kw curves agree within 10 percent"
    if summary_path is None:
        return [
            Evidence(
                "WAITING",
                requirement,
                "No --postrun-validation-summary supplied.",
                "After HFSS exports .s8p, run run_s8p_hfss_postrun_validation_from_aedt_packet.py.",
            )
        ]
    if not summary_path.is_file():
        return [Evidence("WAITING", requirement, f"Missing {summary_path}", "Run the post-HFSS validation gate after HFSS export.")]
    summary = _read_json(summary_path)
    status = summary.get("overall_status")
    plot_checks_passed, plot_detail = _postrun_plot_checks_passed(summary)
    formula_checks_passed, formula_detail = _postrun_formula_checks_passed(summary)
    metric_checks_passed, metric_detail = _postrun_metric_checks_passed(summary)
    port_manifest_passed, port_manifest_detail = _postrun_port_manifest_checks_passed(summary)
    if status == "PASS":
        return [
            Evidence(
                "PASS" if plot_checks_passed and formula_checks_passed and metric_checks_passed and port_manifest_passed else "FAIL",
                requirement,
                f"overall_status=PASS, status_counts={summary.get('status_counts')}; {plot_detail}; {formula_detail}; {metric_detail}; {port_manifest_detail}",
                "Use the generated EMX/HFSS plots and result CSV as validation evidence only after all plot and metric checks PASS.",
            ),
            Evidence(
                "PASS" if plot_checks_passed else "FAIL",
                "postrun validation generated EMX, HFSS, and overlay physical-feature figures",
                plot_detail,
                "Rerun run_s8p_hfss_postrun_validation_from_aedt_packet.py and keep EMX, HFSS, overlay, and metric CSV artifacts.",
            ),
            Evidence(
                "PASS" if formula_checks_passed else "FAIL",
                "postrun validation proves port-pair formula trace consistency",
                formula_detail,
                "Regenerate handoff/postrun validation so the formula trace records the same port pairs and Lp/Ls/Qp/Qs/K equations used for plots.",
            ),
            Evidence(
                "PASS" if metric_checks_passed else "FAIL",
                "postrun validation proves Lp/Ls/Q/K/Kw metrics within 10 percent",
                metric_detail,
                "Fix EMX/HFSS mismatch or port-pair/formula setup until Lp/Ls/Q/K/Kw all pass the configured 10% gate.",
            ),
            Evidence(
                "PASS" if port_manifest_passed else "FAIL",
                "postrun validation proves HFSS port manifest and integration lines",
                port_manifest_detail,
                "Rerun HFSS build/export and postrun validation so P001-P008 order, P001_G-P008_G grounds, and signal-to-ground integration lines are traceable.",
            ),
        ]
    return [
        Evidence(
            "WAITING" if status == "WAITING_FOR_HFSS" else "FAIL",
            requirement,
            f"overall_status={status}, decision={summary.get('decision')}",
            "Export/fix HFSS .s8p and rerun the postrun validation gate.",
        )
    ]


def _postrun_plot_checks_passed(summary: dict[str, Any]) -> tuple[bool, str]:
    required_names = (
        "ADS-style EMX physical plot exists",
        "ADS-style HFSS physical plot exists",
        "ADS-style EMX/HFSS overlay plot exists",
        "ADS-style metric CSV exists",
        "ADS-style EMX plot source is 8-port",
        "ADS-style HFSS plot source is 8-port",
        "ADS-style EMX/HFSS plot port pairs match",
    )
    statuses = _postrun_check_statuses(summary)
    missing_or_failed = [name for name in required_names if statuses.get(name) != "PASS"]
    return not missing_or_failed, f"plot_checks_missing_or_failed={missing_or_failed}"


def _postrun_metric_checks_passed(summary: dict[str, Any]) -> tuple[bool, str]:
    required_prefixes = (
        "k <=",
        "qp <=",
        "qs <=",
        "lp_nh <=",
        "ls_nh <=",
    )
    checks = list(summary.get("checks") or [])
    missing_or_failed = []
    for prefix in required_prefixes:
        matched = [item for item in checks if str(item.get("name", "")).startswith(prefix)]
        if not matched or not any(item.get("status") == "PASS" for item in matched):
            missing_or_failed.append(prefix)
    return not missing_or_failed, f"metric_checks_missing_or_failed={missing_or_failed}"


def _postrun_formula_checks_passed(summary: dict[str, Any]) -> tuple[bool, str]:
    required_names = (
        "formula trace contains port_pair_syntax",
        "formula trace contains differential_transform",
        "formula trace contains lp_formula",
        "formula trace contains ls_formula",
        "formula trace contains m_formula",
        "formula trace contains qp_formula",
        "formula trace contains qs_formula",
        "formula trace contains k_formula",
    )
    statuses = _postrun_check_statuses(summary)
    missing_or_failed = [name for name in required_names if statuses.get(name) != "PASS"]
    return not missing_or_failed, f"formula_checks_missing_or_failed={missing_or_failed}"


def _postrun_port_manifest_checks_passed(summary: dict[str, Any]) -> tuple[bool, str]:
    required_names = (
        "HFSS build port manifest exists",
        "HFSS build port manifest schema",
        "HFSS build port manifest has 8 ports",
        "HFSS build port manifest port order is P001-P008",
        "HFSS build port manifest ground names are P001_G-P008_G",
        "HFSS build port manifest records integration lines",
    )
    statuses = _postrun_check_statuses(summary)
    missing_or_failed = [name for name in required_names if statuses.get(name) != "PASS"]
    return not missing_or_failed, f"port_manifest_checks_missing_or_failed={missing_or_failed}"


def _postrun_check_statuses(summary: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for item in summary.get("checks") or []:
        name = str(item.get("name", ""))
        status = str(item.get("status", ""))
        if name:
            statuses[name] = status
    return statuses


def _explicit_question_evidence(summary: dict[str, Any], paths: dict[str, Path | None], args: argparse.Namespace) -> list[Evidence]:
    items = []
    feature_columns = list(summary.get("feature_columns") or [])
    if args.require_scalar_q and "q_center" not in feature_columns:
        items.append(
            Evidence(
                "QUESTION",
                "unresolved question: scalar Q definition",
                f"feature_columns={feature_columns or 'unknown'}",
                "User/advisor must confirm whether Q means min(Qp,Qs), mean, geometric mean, harmonic mean, Qp, or Qs.",
            )
        )
    if paths["config"] is None:
        items.append(
            Evidence(
                "QUESTION",
                "unresolved question: final PDK/power-line layer selection",
                "No final config supplied.",
                "Confirm primary/secondary vertical power-line metal layers and bridge layers from the CAE PDK stack.",
            )
        )
    return items


def _default_quality_summary(run_dir: Path | None, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if run_dir is None:
        return None
    return run_dir / "dataset_quality_gates_s8p_physical_feature" / "dataset_quality_gates_summary.json"


def _default_port_pair_candidate_audit_summary(run_dir: Path | None, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if run_dir is None:
        return None
    return (
        run_dir
        / "dataset_quality_gates_s8p_physical_feature"
        / "selected_s8p_port_pair_physical_candidate_audit"
        / "s8p_port_pair_physical_candidate_audit_summary.json"
    )


def _default_inverse_training_manifest(
    launch_summary_path: Path | None,
    launch_summary: dict[str, Any],
    explicit: Path | None,
) -> Path | None:
    if explicit is not None:
        return explicit
    artifacts = launch_summary.get("inverse_model_artifacts") if isinstance(launch_summary, dict) else None
    if isinstance(artifacts, dict):
        raw_path = str(artifacts.get("post_emx_inverse_training_manifest") or "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if not path.is_absolute() and launch_summary_path is not None:
                path = launch_summary_path.parent / path
            return path
    inverse_root = _launch_inverse_quality_root(launch_summary_path, launch_summary)
    if inverse_root is None:
        return None
    return inverse_root / "physical_feature_inverse_training_table" / "physical_feature_inverse_training_manifest.json"


def _default_inverse_prediction_summary(
    launch_summary_path: Path | None,
    launch_summary: dict[str, Any],
    explicit: Path | None,
) -> Path | None:
    if explicit is not None:
        return explicit
    inverse_root = _launch_inverse_quality_root(launch_summary_path, launch_summary)
    if inverse_root is None:
        return None
    return inverse_root / "physical_feature_inverse_geometry_prediction" / "physical_feature_inverse_prediction_summary.json"


def _default_inverse_model_quality_summary(
    launch_summary_path: Path | None,
    launch_summary: dict[str, Any],
    explicit: Path | None,
) -> Path | None:
    if explicit is not None:
        return explicit
    artifacts = launch_summary.get("inverse_model_artifacts") if isinstance(launch_summary, dict) else None
    if isinstance(artifacts, dict):
        raw_path = str(artifacts.get("post_emx_inverse_model_quality_summary") or "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if not path.is_absolute() and launch_summary_path is not None:
                path = launch_summary_path.parent / path
            return path
    raw_run_dir = str(launch_summary.get("run_dir") or "").strip() if isinstance(launch_summary, dict) else ""
    if raw_run_dir:
        run_dir = Path(raw_run_dir).expanduser()
        if not run_dir.is_absolute() and launch_summary_path is not None:
            run_dir = launch_summary_path.parent / run_dir
        return (
            run_dir
            / "dataset_quality_gates_s8p_physical_feature"
            / "physical_feature_inverse_model_quality"
            / "physical_feature_inverse_model_quality_summary.json"
        )
    return None


def _default_saved_inverse_model_summary(
    launch_summary_path: Path | None,
    launch_summary: dict[str, Any],
    explicit: Path | None,
) -> Path | None:
    if explicit is not None:
        return explicit
    artifacts = launch_summary.get("inverse_model_artifacts") if isinstance(launch_summary, dict) else None
    if isinstance(artifacts, dict):
        raw_path = str(artifacts.get("post_emx_saved_inverse_model_summary") or "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if not path.is_absolute() and launch_summary_path is not None:
                path = launch_summary_path.parent / path
            return path
    raw_run_dir = str(launch_summary.get("run_dir") or "").strip() if isinstance(launch_summary, dict) else ""
    if raw_run_dir:
        run_dir = Path(raw_run_dir).expanduser()
        if not run_dir.is_absolute() and launch_summary_path is not None:
            run_dir = launch_summary_path.parent / run_dir
        return (
            run_dir
            / "dataset_quality_gates_s8p_physical_feature"
            / "physical_feature_saved_inverse_model"
            / "physical_feature_inverse_model_training_summary.json"
        )
    return None


def _launch_inverse_quality_root(launch_summary_path: Path | None, launch_summary: dict[str, Any]) -> Path | None:
    raw_out_dir = str(launch_summary.get("out_dir") or "").strip() if isinstance(launch_summary, dict) else ""
    if raw_out_dir:
        base = Path(raw_out_dir).expanduser()
        if not base.is_absolute() and launch_summary_path is not None:
            base = launch_summary_path.parent / base
        return base / "inverse_quality_gates"
    if launch_summary_path is None:
        return None
    return launch_summary_path.parent / "inverse_quality_gates"


def _overall_status(evidence: list[Evidence]) -> str:
    statuses = {item.status for item in evidence}
    if "FAIL" in statuses or "QUESTION" in statuses:
        return "NOT_READY"
    if "WAITING" in statuses:
        return "WAITING_FOR_MARS_OR_HFSS"
    return "PASS"


def _decision(status: str) -> str:
    if status == "PASS":
        return "READY_TO_REPORT_VERIFIED_NEXT_GEN_S8P_SAMPLE"
    if status == "WAITING_FOR_MARS_OR_HFSS":
        return "CONTINUE_EXTERNAL_EMX_HFSS_RUNS"
    return "DO_NOT_CLAIM_NEXT_GEN_S8P_GOAL_COMPLETE"


def _status_counts(evidence: list[Evidence]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.status] = counts.get(item.status, 0) + 1
    return dict(sorted(counts.items()))


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "nan"}


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _placeholder_hits(text: str) -> list[str]:
    hits = []
    for index, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\b(TODO|REPLACE|TBD|PLACEHOLDER)\b", line, flags=re.IGNORECASE):
            hits.append(f"L{index}: {line.strip()}")
    return hits


def _looks_placeholder(value: Any) -> bool:
    text = str(value).strip()
    return not text or bool(re.search(r"\b(TODO|REPLACE|TBD|PLACEHOLDER)\b", text, flags=re.IGNORECASE))


def _is_zin_column(column: str) -> bool:
    name = str(column).strip().lower()
    for prefix in ("input__", "target__", "pred_", "candidate__"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return "zin" in name or name.startswith(("re_z", "im_z", "z_real", "z_imag"))


def _write_csv(path: Path, evidence: list[Evidence]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "requirement", "evidence", "next_action"])
        writer.writeheader()
        for item in evidence:
            writer.writerow(item.as_dict())


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Generation S8P Goal Readiness Audit",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Status counts: `{summary['status_counts']}`",
        "",
        "## Evidence",
        "",
        "| Status | Requirement | Evidence | Next action |",
        "| --- | --- | --- | --- |",
    ]
    for item in summary["evidence"]:
        lines.append(
            f"| {_cell(item['status'])} | {_cell(item['requirement'])} | "
            f"{_cell(item['evidence'])} | {_cell(item['next_action'])} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
