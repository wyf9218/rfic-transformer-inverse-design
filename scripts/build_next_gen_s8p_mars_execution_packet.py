#!/usr/bin/env python3
"""Build a safe MARS execution packet for the next-generation S8P flow.

The generated shell script is intentionally guarded. It prepares path discovery,
final config generation, config contract audit, and the launch packet. It only
runs the configured EMX queue when the operator explicitly sets `RUN_EMX=1`.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POWER_LINE_8PORT_PLACEMENT_POLICY = "coil_opening_fixed_10um_port_ground_overlap"


def _run_emx_guard(args: argparse.Namespace) -> str:
    return (
        "RUN_EMX=1 is required; the launch packet then runs a one-sample "
        f"layout smoke/audit before the {int(args.expected_sample_count)}-sample EMX queue."
    )


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str
    next_action: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "name": self.name,
            "detail": self.detail,
            "next_action": self.next_action,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = _packet_paths(out_dir, args)
    checks = _build_checks(repo_root, args)
    overall_status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    decision = "MARS_S8P_EXECUTION_RUNBOOK_READY" if overall_status == "PASS" else "FILL_REQUIRED_CONFIRMATIONS_BEFORE_MARS_RUN"
    commands_path = out_dir / "next_gen_s8p_mars_execution.commands.sh"
    summary_path = out_dir / "next_gen_s8p_mars_execution_packet_summary.json"
    report_path = out_dir / "NEXT_GEN_S8P_MARS_EXECUTION_PACKET_CN.md"
    inputs_path = out_dir / "next_gen_s8p_required_inputs.json"

    command_text = _render_commands(repo_root, paths, args)
    commands_path.write_text(command_text, encoding="utf-8")
    commands_path.chmod(0o755)
    required_inputs = _required_inputs(args)
    inputs_path.write_text(json.dumps(required_inputs, indent=2, ensure_ascii=False), encoding="utf-8")
    readiness_artifacts = _readiness_artifact_paths(paths)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": decision,
        "repo_root": str(repo_root),
        "out_dir": str(out_dir),
        "commands_path": str(commands_path),
        "required_inputs": str(inputs_path),
        "paths": {key: str(value) for key, value in paths.items()},
        "readiness_artifacts": {key: str(value) for key, value in readiness_artifacts.items()},
        "run_emx_guard": _run_emx_guard(args),
        "checks": [check.as_dict() for check in checks],
        "arguments": vars(args),
        "limitations": [
            "This packet does not run MARS, EMX, Cadence, HFSS, or ADS by itself.",
            "The generated command file defaults to RUN_EMX=0, so it prepares and audits but does not start the launch packet.",
            f"When RUN_EMX=1 is set, the launch packet must pass the one-sample create-only S8P layout smoke/audit before starting the {int(args.expected_sample_count)}-sample EMX queue.",
            "Final acceptance still requires the generated .s8p dataset, S8P quality gates, HFSS export, and <=5% EMX/HFSS physical-curve validation.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"overall_status={overall_status}")
    print(f"decision={decision}")
    print(f"commands={commands_path}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    print(f"required_inputs={inputs_path}")
    return 2 if overall_status != "PASS" and not args.no_fail_exit else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root")
    parser.add_argument("--template", default="configs/mars_s8p_physical_feature_500_template.yaml")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--bootstrap-geometry-candidate-queue",
        action="store_true",
        help="Cold-start mode: generate configured geometry candidates directly from finalized config bounds instead of requiring an existing S8P physical-feature dataset",
    )
    parser.add_argument("--existing-dataset-dir", help="Existing EMX-labeled dataset used to train Lp/Ls/Q/K -> geometry")
    parser.add_argument("--inverse-target-json", help="JSON dict/list of target Lp/Ls/Q/K physical features")
    parser.add_argument("--port-map", help="Comma-separated P001..P008 physical labels")
    parser.add_argument("--role-labels", help="Comma-separated role=port labels that separate physical roles from Touchstone output order")
    parser.add_argument("--differential-port-pairs", help="Explicit S8P differential pairs, e.g. 1,4:5,6")
    parser.add_argument(
        "--port-map-approval-summary",
        help="Approved s8p_port_map_approval_summary.json. Candidate/unapproved summaries keep this MARS packet not ready.",
    )
    parser.add_argument(
        "--geometry-contract-approval-summary",
        help="Approved s8p_geometry_contract_approval_summary.json. Candidate/unapproved summaries keep this MARS packet not ready.",
    )
    parser.add_argument(
        "--combined-approval-summary",
        help="Approved s8p_combined_approval_readiness_summary.json used by readiness and objective acceptance audits.",
    )
    parser.add_argument("--scalar-q-definition", choices=("min", "mean", "geometric_mean", "harmonic_mean", "primary", "secondary"))
    parser.add_argument("--primary-power-line-layer", type=int)
    parser.add_argument("--secondary-power-line-layer", type=int)
    parser.add_argument("--path-discovery-root", action="append", default=[])
    parser.add_argument("--expected-sample-count", type=int, default=500)
    parser.add_argument("--expected-jobs", type=int, default=8)
    parser.add_argument("--bootstrap-sampler", choices=("lhs", "lhs_optimized", "sobol"), default="lhs_optimized")
    parser.add_argument("--bootstrap-seed", type=int, default=20260616)
    parser.add_argument("--run-dir-name")
    parser.add_argument("--launch-packet-dir-name", default="physical_feature_s8p_launch_packet")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args(argv)


def _packet_paths(out_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    return {
        "path_discovery_dir": out_dir / "01_mars_path_discovery",
        "final_config_dir": out_dir / "02_final_s8p_config",
        "final_config": out_dir / "02_final_s8p_config" / "final_s8p_physical_feature_500.yaml",
        "contract_audit_dir": out_dir / "03_power_line_contract_audit",
        "launch_packet_dir": out_dir / str(args.launch_packet_dir_name),
        "run_dir": out_dir / str(args.run_dir_name or f"new_s8p_physical_feature_emx_{int(args.expected_sample_count)}"),
        "readiness_dir": out_dir / "99_next_gen_s8p_goal_readiness",
    }


def _readiness_artifact_paths(paths: dict[str, Path]) -> dict[str, Path]:
    quality_dir = paths["run_dir"] / "dataset_quality_gates_s8p_physical_feature"
    return {
        "dataset_quality_summary": quality_dir / "dataset_quality_gates_summary.json",
        "physical_feature_coverage_plan_summary": quality_dir
        / "physical_feature_balanced_acquisition_plan"
        / "physical_feature_acquisition_plan_summary.json",
        "port_pair_candidate_audit_summary": quality_dir
        / "selected_s8p_port_pair_physical_candidate_audit"
        / "s8p_port_pair_physical_candidate_audit_summary.json",
        "selected_handoff_summary": quality_dir / "selected_s8p_hfss_handoff" / "selected_s8p_hfss_handoff_summary.json",
        "aedt_packet_summary": quality_dir / "selected_s8p_hfss_aedt_scripts" / "hfss_s8p_aedt_script_packet_summary.json",
        "hfss_payload_render_summary": quality_dir / "selected_s8p_hfss_payload_views" / "hfss_payload_geometry_render_batch_summary.json",
        "post_emx_inverse_training_manifest": quality_dir
        / "physical_feature_inverse_training_table"
        / "physical_feature_inverse_training_manifest.json",
        "postrun_validation_summary": quality_dir / "selected_s8p_hfss_postrun_validation" / "s8p_hfss_postrun_validation_summary.json",
    }


def _build_checks(repo_root: Path, args: argparse.Namespace) -> list[Check]:
    approval_check = _port_map_approval_check(
        args.port_map_approval_summary,
        args.port_map,
        args.differential_port_pairs,
    )
    geometry_approval_check = _geometry_contract_approval_check(
        args.geometry_contract_approval_summary,
        args.differential_port_pairs,
    )
    checks = [
        _check(repo_root.is_dir(), "repo root exists", str(repo_root), "Run this from the repository root or pass --repo-root."),
        _check((repo_root / "scripts" / "discover_mars_emx_cadence_paths.py").is_file(), "path discovery script exists", "discover_mars_emx_cadence_paths.py", "Restore path discovery script."),
        _check((repo_root / "scripts" / "prepare_final_s8p_physical_feature_config.py").is_file(), "final config preparer exists", "prepare_final_s8p_physical_feature_config.py", "Restore final config preparer."),
        _check((repo_root / "scripts" / "preflight_dataset_config.py").is_file(), "strict final config preflight exists", "preflight_dataset_config.py", "Restore strict path preflight script."),
        _check((repo_root / "scripts" / "build_physical_feature_s8p_launch_packet.py").is_file(), "launch packet builder exists", "build_physical_feature_s8p_launch_packet.py", "Restore launch packet builder."),
        _check((repo_root / "scripts" / "build_next_gen_s8p_objective_acceptance_audit.py").is_file(), "objective acceptance audit exists", "build_next_gen_s8p_objective_acceptance_audit.py", "Restore objective acceptance audit script."),
        _check((repo_root / "scripts" / "build_s8p_geometry_bootstrap_candidate_queue.py").is_file(), "bootstrap geometry candidate queue builder exists", "build_s8p_geometry_bootstrap_candidate_queue.py", "Restore bootstrap candidate queue builder."),
        _check(
            bool(args.bootstrap_geometry_candidate_queue) or bool(args.existing_dataset_dir),
            "existing dataset specified or bootstrap geometry mode enabled",
            "bootstrap geometry mode"
            if args.bootstrap_geometry_candidate_queue
            else str(args.existing_dataset_dir or ""),
            "Pass --existing-dataset-dir, or use --bootstrap-geometry-candidate-queue for the first S8P batch.",
        ),
        _check(
            bool(args.bootstrap_geometry_candidate_queue) or bool(args.inverse_target_json),
            "inverse target JSON specified or bootstrap geometry mode enabled",
            "bootstrap geometry mode"
            if args.bootstrap_geometry_candidate_queue
            else str(args.inverse_target_json or ""),
            "Pass --inverse-target-json, or use --bootstrap-geometry-candidate-queue for cold-start data generation.",
        ),
        _check(_valid_port_map(args.port_map), "P001-P008 port map is specified", str(args.port_map or ""), "Pass --port-map with eight approved labels."),
        _check(
            _valid_role_labels(args.role_labels, args.port_map),
            "P001-P008 role labels are specified",
            str(args.role_labels or ""),
            "Pass --role-labels with every physical role mapped to the approved P001-P008 order.",
        ),
        _check(_valid_differential_pairs(args.differential_port_pairs), "differential port pairs are specified", str(args.differential_port_pairs or ""), "Pass --differential-port-pairs after port-map approval."),
        approval_check,
        geometry_approval_check,
        _check(bool(args.scalar_q_definition), "scalar Q definition is specified", str(args.scalar_q_definition or ""), "Pass --scalar-q-definition, recommended min."),
        _check(args.primary_power_line_layer is not None, "primary power-line layer is specified", str(args.primary_power_line_layer), "Pass --primary-power-line-layer from the PDK stack."),
        _check(args.secondary_power_line_layer is not None, "secondary power-line layer is specified", str(args.secondary_power_line_layer), "Pass --secondary-power-line-layer from the PDK stack."),
        _check(
            True,
            "power-line placement policy uses fixed 10um port-ground overlap",
            POWER_LINE_8PORT_PLACEMENT_POLICY,
            "Keep the coil opening, 10um port-ground protrusion, synchronized line widths, and opposite-coil clearance contract.",
        ),
        _check(int(args.expected_sample_count) > 0, "expected EMX sample count is positive", str(args.expected_sample_count), "Use a positive --expected-sample-count."),
        _check(int(args.expected_jobs) == 8, "expected worker count is 8", str(args.expected_jobs), "Use --expected-jobs 8."),
    ]
    return checks


def _render_commands(repo_root: Path, paths: dict[str, Path], args: argparse.Namespace) -> str:
    py = '"${PYTHON}"'
    roots: list[str] = []
    for root in args.path_discovery_root:
        roots.extend(["--root", str(root)])
    root_lines = [f"  {shlex.quote(flag)} {shlex.quote(value)} \\" for flag, value in zip(roots[0::2], roots[1::2])]
    port_map = args.port_map or "<FILL_P001_P008_PORT_MAP>"
    role_labels = args.role_labels or ""
    diff_pairs = args.differential_port_pairs or "<FILL_DIFF_PORT_PAIRS>"
    approval_summary = args.port_map_approval_summary or "<FILL_APPROVED_S8P_PORT_MAP_APPROVAL_SUMMARY>"
    geometry_approval_summary = args.geometry_contract_approval_summary or "<FILL_APPROVED_S8P_GEOMETRY_CONTRACT_APPROVAL_SUMMARY>"
    combined_approval_summary = (
        args.combined_approval_summary
        or "${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json"
    )
    scalar_q = args.scalar_q_definition or "<FILL_SCALAR_Q_DEFINITION>"
    primary_layer = "" if args.primary_power_line_layer is None else str(args.primary_power_line_layer)
    secondary_layer = "" if args.secondary_power_line_layer is None else str(args.secondary_power_line_layer)
    existing_dataset = args.existing_dataset_dir or "<FILL_EXISTING_DATASET_DIR>"
    inverse_target = args.inverse_target_json or "<FILL_INVERSE_TARGET_JSON>"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Next-generation S8P physical-feature MARS execution runbook.",
        f"# Default is safe: RUN_EMX=0 prepares/audits only. Set RUN_EMX=1 on MARS to run launch-packet smoke/audit, then the {int(args.expected_sample_count)}-sample EMX queue.",
        "# S8P power-line placement policy: coil_opening_fixed_10um_port_ground_overlap. Every port protrudes 10um past the white shield opening; all coil, bridge, and power-line widths stay synchronized.",
        'REPO_ROOT="${REPO_ROOT:-$(pwd)}"',
        'if [[ -z "${PYTHON:-}" ]]; then',
        '  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then',
        '    PYTHON="${REPO_ROOT}/.venv/bin/python"',
        '  elif command -v python3 >/dev/null 2>&1; then',
        '    PYTHON="$(command -v python3)"',
        "  elif command -v python >/dev/null 2>&1; then",
        '    PYTHON="$(command -v python)"',
        '  else',
        "    echo 'No usable Python found for S8P MARS execution runbook.' >&2",
        "    exit 11",
        "  fi",
        "fi",
        "export PYTHON",
        "",
        "# MARS/CAE defaults verified from the previous clean-smoke run. Operators can override any",
        "# value before launching, but the defaults prevent dbAccess/EMX child processes from",
        "# inheriting an empty license environment or selecting unrelated CAE documentation files.",
        'CADENCE_LICENSE_FILE="${CADENCE_LICENSE_FILE:-27000@example-license-server}"',
        'export CDS_LIC_FILE="${CDS_LIC_FILE:-${CADENCE_LICENSE_FILE}}"',
        'export LM_LICENSE_FILE="${LM_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"',
        'export CDSLMD_LICENSE_FILE="${CDSLMD_LICENSE_FILE:-${CADENCE_LICENSE_FILE}}"',
        'export SINGULARITYENV_CDS_LIC_FILE="${SINGULARITYENV_CDS_LIC_FILE:-${CDS_LIC_FILE}}"',
        'export SINGULARITYENV_LM_LICENSE_FILE="${SINGULARITYENV_LM_LICENSE_FILE:-${LM_LICENSE_FILE}}"',
        'export CADENCE_INSTALL_ROOT="${CADENCE_INSTALL_ROOT:-/cae/apps/data/cadence-2025/installs/IC231}"',
        'export CADENCE_RUNTIME_LD_PATH="${CADENCE_RUNTIME_LD_PATH:-${CADENCE_INSTALL_ROOT}/tools.lnx86/lib/64bit/RHEL/RHEL7:/cae/apps/data/cadence-2025/installs/INNOVUS211/tools.lnx86/lib}"',
        'export LD_LIBRARY_PATH="${CADENCE_RUNTIME_LD_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"',
        'export EMX_PROCESS_FILE="${EMX_PROCESS_FILE:-/path/to/pdk/tsmc65/RC_IRCX_CRN65G+_1P9M+UT-ALRDL_6X1Z1U_MiM_typical.proc}"',
        'export CADENCE_PDK_CDS_LIB="${CADENCE_PDK_CDS_LIB:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/cds.lib}"',
        'export CADENCE_LAYER_MAP="${CADENCE_LAYER_MAP:-/path/to/pdk/tsmc65/MSRF_General_Purpose_Plus/PDK/CadenceOA/tn65cmsp018k3_1_0c/tsmcN65/tsmcN65.layermap}"',
        'export TECH_LIB_NAME="${TECH_LIB_NAME:-tsmcN65}"',
        f"PACKET_ROOT=${{PACKET_ROOT:-{shlex.quote(paths['path_discovery_dir'].parent.name)}}}",
        f"TEMPLATE={shlex.quote(str(Path(args.template)))}",
        f"EXISTING_DATASET_DIR={shlex.quote(existing_dataset)}",
        f"INVERSE_TARGET_JSON={shlex.quote(inverse_target)}",
        f"PORT_MAP=${{PORT_MAP:-{shlex.quote(port_map)}}}",
        f"ROLE_LABELS=${{ROLE_LABELS:-{shlex.quote(role_labels)}}}",
        f"DIFFERENTIAL_PORT_PAIRS=${{DIFFERENTIAL_PORT_PAIRS:-{shlex.quote(diff_pairs)}}}",
        f"PORT_MAP_APPROVAL_SUMMARY=${{PORT_MAP_APPROVAL_SUMMARY:-{shlex.quote(approval_summary)}}}",
        f"GEOMETRY_CONTRACT_APPROVAL_SUMMARY=${{GEOMETRY_CONTRACT_APPROVAL_SUMMARY:-{shlex.quote(geometry_approval_summary)}}}",
        f"COMBINED_APPROVAL_SUMMARY=${{COMBINED_APPROVAL_SUMMARY:-{shlex.quote(combined_approval_summary)}}}",
        'PORT_MAP_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/port_map_approval_user_approved_20260619/s8p_port_map_approval_summary.json"',
        'GEOMETRY_CONTRACT_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/geometry_contract_approval_user_approved_20260619/s8p_geometry_contract_approval_summary.json"',
        'COMBINED_APPROVAL_SUMMARY_FALLBACK="${REPO_ROOT}/outputs/s8p_port_order_from_the_best_20260619/combined_approval_readiness_user_approved_20260619/s8p_combined_approval_readiness_summary.json"',
        'if [[ ! -f "${PORT_MAP_APPROVAL_SUMMARY}" && -f "${PORT_MAP_APPROVAL_SUMMARY_FALLBACK}" ]]; then',
        '  PORT_MAP_APPROVAL_SUMMARY="${PORT_MAP_APPROVAL_SUMMARY_FALLBACK}"',
        'fi',
        'if [[ ! -f "${GEOMETRY_CONTRACT_APPROVAL_SUMMARY}" && -f "${GEOMETRY_CONTRACT_APPROVAL_SUMMARY_FALLBACK}" ]]; then',
        '  GEOMETRY_CONTRACT_APPROVAL_SUMMARY="${GEOMETRY_CONTRACT_APPROVAL_SUMMARY_FALLBACK}"',
        'fi',
        'if [[ ! -f "${COMBINED_APPROVAL_SUMMARY}" && -f "${COMBINED_APPROVAL_SUMMARY_FALLBACK}" ]]; then',
        '  COMBINED_APPROVAL_SUMMARY="${COMBINED_APPROVAL_SUMMARY_FALLBACK}"',
        'fi',
        f"SCALAR_Q_DEFINITION=${{SCALAR_Q_DEFINITION:-{shlex.quote(scalar_q)}}}",
        f"PRIMARY_POWER_LINE_LAYER=${{PRIMARY_POWER_LINE_LAYER:-{shlex.quote(primary_layer or '<FILL_PRIMARY_LAYER>')}}}",
        f"SECONDARY_POWER_LINE_LAYER=${{SECONDARY_POWER_LINE_LAYER:-{shlex.quote(secondary_layer or '<FILL_SECONDARY_LAYER>')}}}",
        f"POWER_LINE_8PORT_PLACEMENT_POLICY={POWER_LINE_8PORT_PLACEMENT_POLICY}",
        "RUN_EMX=${RUN_EMX:-0}",
        "AUTO_INSTALL_PY_DEPS=${AUTO_INSTALL_PY_DEPS:-0}",
        'PATH_DISCOVERY_DIR="${PACKET_ROOT}/01_mars_path_discovery"',
        'FINAL_CONFIG_DIR="${PACKET_ROOT}/02_final_s8p_config"',
        'FINAL_CONFIG="${FINAL_CONFIG_DIR}/final_s8p_physical_feature_500.yaml"',
        'CONTRACT_AUDIT_DIR="${PACKET_ROOT}/03_power_line_contract_audit"',
        f"LAUNCH_PACKET_DIR=\"${{PACKET_ROOT}}/{paths['launch_packet_dir'].name}\"",
        f"RUN_DIR=\"${{PACKET_ROOT}}/{paths['run_dir'].name}\"",
        'QUALITY_DIR="${RUN_DIR}/dataset_quality_gates_s8p_physical_feature"',
        'SELECTED_HANDOFF_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_handoff/selected_s8p_hfss_handoff_summary.json"',
        'AEDT_PACKET_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_aedt_scripts/hfss_s8p_aedt_script_packet_summary.json"',
        'HFSS_PAYLOAD_RENDER_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_payload_views/hfss_payload_geometry_render_batch_summary.json"',
        'INVERSE_TRAINING_MANIFEST="${QUALITY_DIR}/physical_feature_inverse_training_table/physical_feature_inverse_training_manifest.json"',
        'POSTRUN_VALIDATION_SUMMARY="${QUALITY_DIR}/selected_s8p_hfss_postrun_validation/s8p_hfss_postrun_validation_summary.json"',
        'READINESS_DIR="${PACKET_ROOT}/99_next_gen_s8p_goal_readiness"',
        "",
        "mkdir -p \"$PACKET_ROOT\"",
        "",
        "echo '[0/7] Check Python dependencies'",
        *(_python_dependency_check_lines(py)),
        "",
        "echo '[1/7] Discover MARS EMX/Cadence paths (read-only)'",
        f"{py} \"${{REPO_ROOT}}/scripts/discover_mars_emx_cadence_paths.py\" \\",
        "  --config \"${REPO_ROOT}/${TEMPLATE}\" \\",
        "  --out-dir \"${PATH_DISCOVERY_DIR}\" \\",
        *root_lines,
        "  --no-fail-exit",
        "",
        "echo '[2/7] Prepare final S8P physical-feature config'",
        f"{py} \"${{REPO_ROOT}}/scripts/prepare_final_s8p_physical_feature_config.py\" \\",
        "  --template \"${REPO_ROOT}/${TEMPLATE}\" \\",
        "  --path-discovery-summary \"${PATH_DISCOVERY_DIR}/mars_emx_cadence_path_discovery_summary.json\" \\",
        "  --out-config \"${FINAL_CONFIG}\" \\",
        "  --out-dir \"${FINAL_CONFIG_DIR}\" \\",
        "  --port-map \"${PORT_MAP}\" \\",
        "  --role-labels \"${ROLE_LABELS}\" \\",
        "  --differential-port-pairs \"${DIFFERENTIAL_PORT_PAIRS}\" \\",
        "  --scalar-q-definition \"${SCALAR_Q_DEFINITION}\" \\",
        "  --primary-power-line-layer \"${PRIMARY_POWER_LINE_LAYER}\" \\",
        "  --secondary-power-line-layer \"${SECONDARY_POWER_LINE_LAYER}\" \\",
        "  --license-file \"${CDS_LIC_FILE}\" \\",
        "  --cdslmd-license-file \"${CDSLMD_LICENSE_FILE}\" \\",
        "  --check-paths",
        "",
        "echo '[3/7] Strict final config preflight: reject placeholders and dry-run EMX paths'",
        f"{py} \"${{REPO_ROOT}}/scripts/preflight_dataset_config.py\" \"${{FINAL_CONFIG}}\" \\",
        "  --check-emx-paths \\",
        "  --forbid-dry-run-paths \\",
        "  --summary \"${FINAL_CONFIG_DIR}/final_s8p_physical_feature_500.preflight_summary.json\" \\",
        "  --report \"${FINAL_CONFIG_DIR}/final_s8p_physical_feature_500.preflight_report.md\"",
        "",
        "echo '[4/7] Audit final 8-port power-line contract'",
        f"{py} \"${{REPO_ROOT}}/scripts/audit_power_line_8port_contract.py\" \\",
        "  --config \"${FINAL_CONFIG}\" \\",
        "  --out-dir \"${CONTRACT_AUDIT_DIR}\" \\",
        "  --expected-ground-frame-width-um 100 \\",
        "  --expected-ground-frame-policy power_line_8port_uses_max_shield_width_and_margin_as_rectangular_ground_frame \\",
        "  --expected-differential-port-pairs \"${DIFFERENTIAL_PORT_PAIRS}\"",
        "",
        "echo '[5/7] Build physical-feature S8P launch packet'",
        *_launch_packet_command_lines(py, args),
        "",
        "echo '[6/7] Optional expensive EMX run'",
        "if [[ \"${RUN_EMX}\" != \"1\" ]]; then",
        f"  echo 'RUN_EMX is not 1; stopping before the launch packet smoke/audit and {int(args.expected_sample_count)}-sample EMX queue.'",
        "  echo 'Review the launch packet, then rerun with RUN_EMX=1 on MARS.'",
        "  exit 0",
        "fi",
        'bash "${LAUNCH_PACKET_DIR}/physical_feature_s8p_launch.commands.sh"',
        "",
        "echo '[7/7] Audit next-generation S8P goal readiness after EMX run'",
        f"{py} \"${{REPO_ROOT}}/scripts/audit_next_gen_s8p_goal_readiness.py\" \\",
        "  --config \"${FINAL_CONFIG}\" \\",
        "  --combined-approval-readiness-summary \"${COMBINED_APPROVAL_SUMMARY}\" \\",
        "  --launch-packet-summary \"${LAUNCH_PACKET_DIR}/physical_feature_s8p_launch_packet_summary.json\" \\",
        "  --candidate-run-dir \"${RUN_DIR}\" \\",
        "  --dataset-quality-summary \"${QUALITY_DIR}/dataset_quality_gates_summary.json\" \\",
        "  --port-pair-candidate-audit-summary \"${QUALITY_DIR}/selected_s8p_port_pair_physical_candidate_audit/s8p_port_pair_physical_candidate_audit_summary.json\" \\",
        "  --selected-handoff-summary \"${SELECTED_HANDOFF_SUMMARY}\" \\",
        "  --aedt-packet-summary \"${AEDT_PACKET_SUMMARY}\" \\",
        "  --hfss-payload-render-summary \"${HFSS_PAYLOAD_RENDER_SUMMARY}\" \\",
        "  --inverse-training-manifest \"${INVERSE_TRAINING_MANIFEST}\" \\",
        "  --postrun-validation-summary \"${POSTRUN_VALIDATION_SUMMARY}\" \\",
        "  --out-dir \"${READINESS_DIR}\" \\",
        "  --no-fail-exit",
        "",
    ]
    return "\n".join(lines)


def _python_dependency_check_lines(py: str) -> list[str]:
    return [
        'MISSING_PY_DEPS="$(',
        f"{py} - <<'PY'",
        "mods = ['yaml', 'numpy', 'scipy', 'matplotlib', 'gdstk']",
        "missing = []",
        "for mod in mods:",
        "    try:",
        "        __import__(mod)",
        "    except Exception:",
        "        missing.append(mod)",
        "print(' '.join(missing))",
        "PY",
        ')"',
        'if [[ -n "${MISSING_PY_DEPS}" ]]; then',
        '  echo "Missing Python dependencies: ${MISSING_PY_DEPS}" >&2',
        '  if [[ "${AUTO_INSTALL_PY_DEPS}" == "1" ]]; then',
        '    echo "AUTO_INSTALL_PY_DEPS=1, installing project dependencies with pip..."',
        '    "${PYTHON}" -m pip install --user -e "${REPO_ROOT}"',
        '    MISSING_PY_DEPS="$(',
        f"{py} - <<'PY'",
        "mods = ['yaml', 'numpy', 'scipy', 'matplotlib', 'gdstk']",
        "missing = []",
        "for mod in mods:",
        "    try:",
        "        __import__(mod)",
        "    except Exception:",
        "        missing.append(mod)",
        "print(' '.join(missing))",
        "PY",
        ')"',
        '  fi',
        'fi',
        'if [[ -n "${MISSING_PY_DEPS}" ]]; then',
        '  echo "Python environment is not ready; install dependencies before running S8P flow." >&2',
        '  echo "Manual command: cd ${REPO_ROOT} && ${PYTHON} -m pip install --user -e ." >&2',
        "  exit 12",
        "fi",
    ]


def _launch_packet_command_lines(py: str, args: argparse.Namespace) -> list[str]:
    lines = [f"{py} \"${{REPO_ROOT}}/scripts/build_physical_feature_s8p_launch_packet.py\" \\"]
    if args.bootstrap_geometry_candidate_queue:
        lines.extend(
            [
                "  --bootstrap-geometry-candidate-queue \\",
                f"  --bootstrap-sampler {shlex.quote(str(args.bootstrap_sampler))} \\",
                f"  --bootstrap-seed {int(args.bootstrap_seed)} \\",
            ]
        )
    else:
        lines.extend(
            [
                "  --dataset-dir \"${EXISTING_DATASET_DIR}\" \\",
                "  --inverse-target-json \"${INVERSE_TARGET_JSON}\" \\",
            ]
        )
    lines.extend(
        [
            "  --config \"${FINAL_CONFIG}\" \\",
            "  --port-map-approval-summary \"${PORT_MAP_APPROVAL_SUMMARY}\" \\",
            "  --geometry-contract-approval-summary \"${GEOMETRY_CONTRACT_APPROVAL_SUMMARY}\" \\",
            "  --combined-approval-summary \"${COMBINED_APPROVAL_SUMMARY}\" \\",
            "  --out-dir \"${LAUNCH_PACKET_DIR}\" \\",
            "  --run-dir \"${RUN_DIR}\" \\",
            "  --scalar-q-definition \"${SCALAR_Q_DEFINITION}\" \\",
            f"  --inverse-candidate-count {int(args.expected_sample_count)} \\",
            f"  --emx-max-count {int(args.expected_sample_count)} \\",
            f"  --expected-emx-count {int(args.expected_sample_count)} \\",
            f"  --jobs {int(args.expected_jobs)} \\",
            f"  --expected-jobs {int(args.expected_jobs)} \\",
            "  --validation-port-pairs \"${DIFFERENTIAL_PORT_PAIRS}\" \\",
            "  --no-package",
        ]
    )
    return lines


def _required_inputs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "candidate_source_mode": "bootstrap_geometry_queue"
        if args.bootstrap_geometry_candidate_queue
        else "physical_feature_inverse_prediction",
        "existing_dataset_dir": args.existing_dataset_dir or "",
        "inverse_target_json": args.inverse_target_json or "",
        "port_map": _split_csv(args.port_map),
        "role_labels": _split_assignments(args.role_labels),
        "differential_port_pairs": args.differential_port_pairs or "",
        "port_map_approval_summary": args.port_map_approval_summary or "",
        "geometry_contract_approval_summary": args.geometry_contract_approval_summary or "",
        "combined_approval_summary": args.combined_approval_summary or "",
        "scalar_q_definition": args.scalar_q_definition or "",
        "primary_power_line_layer": args.primary_power_line_layer,
        "secondary_power_line_layer": args.secondary_power_line_layer,
        "power_line_8port_placement_policy": POWER_LINE_8PORT_PLACEMENT_POLICY,
        "recommended_scalar_q_definition": "min",
        "port_map_approval_requirement": "approval_status must be APPROVED and decision must be PORT_MAP_APPROVED_FOR_MARS_EMX_RUN",
        "geometry_contract_approval_requirement": "approval_status must be APPROVED and decision must be GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN",
        "run_emx_guard": "Set RUN_EMX=1 only after config and launch packet summaries PASS; the launch packet then runs one-sample layout smoke/audit before EMX.",
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Next-Generation S8P MARS Execution Packet",
        "",
        f"- Overall status: **{summary['overall_status']}**",
        f"- Decision: **{summary['decision']}**",
        f"- Commands: `{summary['commands_path']}`",
        f"- Required inputs: `{summary['required_inputs']}`",
        f"- EMX guard: `{summary['run_emx_guard']}`",
        f"- Power-line placement policy: `{POWER_LINE_8PORT_PLACEMENT_POLICY}`",
        f"- Port-map approval summary: `{summary['arguments'].get('port_map_approval_summary') or ''}`",
        f"- Geometry-contract approval summary: `{summary['arguments'].get('geometry_contract_approval_summary') or ''}`",
        f"- Combined approval summary: `{summary['arguments'].get('combined_approval_summary') or ''}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail | Next action |",
        "| --- | --- | --- | --- |",
    ]
    for check in summary["checks"]:
        lines.append(f"| {_cell(check['status'])} | {_cell(check['name'])} | {_cell(check['detail'])} | {_cell(check['next_action'])} |")
    lines.extend(["", "## Execution Order", ""])
    lines.extend(
        [
            "1. Run read-only MARS path discovery.",
            "2. Generate the final S8P physical-feature config.",
            "3. Run strict final-config preflight with `--check-emx-paths --forbid-dry-run-paths`.",
            "4. Audit the 8-port power-line contract.",
            "5. Build the physical-feature S8P launch packet.",
            "6. Review summaries; set `RUN_EMX=1` only when safe.",
            f"7. The launch packet first runs one-sample create-only layout smoke/audit, then starts the {int(summary['arguments'].get('expected_sample_count', 500))}-sample EMX queue.",
            "8. After EMX finishes, run readiness and then HFSS validation.",
        ]
    )
    lines.extend(["", "## Readiness Artifact Paths", ""])
    artifacts = summary.get("readiness_artifacts") or {}
    if isinstance(artifacts, dict):
        for name, path in artifacts.items():
            lines.append(f"- `{name}`: `{path}`")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def _valid_port_map(text: str | None) -> bool:
    items = _split_csv(text)
    return len(items) == 8 and len(set(items)) == 8 and not any(_placeholder(item) for item in items)


def _valid_role_labels(text: str | None, port_map_text: str | None) -> bool:
    role_labels = _split_assignments(text)
    if not role_labels:
        return False
    expected_roles = {
        "left_power_top",
        "left_power_bottom",
        "primary_top",
        "primary_bottom",
        "secondary_top",
        "secondary_bottom",
        "right_power_top",
        "right_power_bottom",
    }
    port_map = _split_csv(port_map_text)
    return (
        set(role_labels) == expected_roles
        and len(set(role_labels.values())) == 8
        and set(role_labels.values()) == set(port_map)
        and not any(_placeholder(item) for item in role_labels.values())
    )


def _valid_differential_pairs(text: str | None) -> bool:
    if not text:
        return False
    try:
        first, second = str(text).split(":", 1)
        ports = [int(item.strip()) for part in (first, second) for item in part.split(",", 1)]
    except Exception:
        return False
    return len(ports) == 4 and len(set(ports)) == 4 and all(1 <= port <= 8 for port in ports)


def _port_map_approval_check(path_raw: str | None, expected_port_map: str | None, expected_pairs: str | None) -> Check:
    if not path_raw:
        return _check(
            False,
            "approved S8P port map summary is specified",
            "",
            "Pass --port-map-approval-summary from build_s8p_port_map_approval_packet.py --approved.",
        )
    path = Path(path_raw).expanduser().resolve()
    if not path.is_file():
        return _check(
            False,
            "approved S8P port map summary is specified",
            str(path),
            "Create/transfer the approved s8p_port_map_approval_summary.json before launching MARS.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _check(
            False,
            "approved S8P port map summary is specified",
            f"{path}: {type(exc).__name__}: {exc}",
            "Regenerate the port-map approval packet.",
        )
    role_records = data.get("role_records") if isinstance(data, dict) else None
    role_ports = [str(item.get("port", "")) for item in role_records or [] if isinstance(item, dict)]
    touchstone_port_order = [str(item) for item in data.get("touchstone_port_order", [])] if isinstance(data, dict) else []
    if not touchstone_port_order:
        touchstone_port_order = role_ports
    expected_ports = [f"P{idx:03d}" for idx in range(1, 9)]
    expected_map = _split_csv(expected_port_map)
    normalized_expected_pairs = _normalize_pair_text(expected_pairs or "")
    normalized_actual_pairs = _normalize_pair_text(str(data.get("port_pairs", "")))
    checks = {
        "overall_status_is_pass": data.get("overall_status") == "PASS",
        "approval_status_is_approved": data.get("approval_status") == "APPROVED",
        "decision_allows_mars_emx_run": data.get("decision") == "PORT_MAP_APPROVED_FOR_MARS_EMX_RUN",
        "role_ports_are_distinct_p001_to_p008": sorted(role_ports) == expected_ports,
        "touchstone_port_order_is_p001_to_p008": touchstone_port_order == expected_ports,
        "touchstone_port_order_matches_cli_port_map": bool(expected_map) and touchstone_port_order == expected_map,
        "port_pairs_match_cli_pairs": bool(normalized_expected_pairs)
        and normalized_actual_pairs == normalized_expected_pairs,
    }
    return _check(
        all(checks.values()),
        "approved S8P port map summary is specified",
        json.dumps(
            {
                "path": str(path),
                "overall_status": data.get("overall_status"),
                "approval_status": data.get("approval_status"),
                "decision": data.get("decision"),
                "port_pairs": data.get("port_pairs", ""),
                "role_ports": role_ports,
                "touchstone_port_order": touchstone_port_order,
                "checks": checks,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "Use only a user/advisor-approved port-map summary matching --port-map and --differential-port-pairs.",
    )


def _geometry_contract_approval_check(path_raw: str | None, expected_pairs: str | None) -> Check:
    if not path_raw:
        return _check(
            False,
            "approved S8P geometry contract summary is specified",
            "",
            "Pass --geometry-contract-approval-summary from build_s8p_geometry_contract_approval_packet.py --approved.",
        )
    path = Path(path_raw).expanduser().resolve()
    if not path.is_file():
        return _check(
            False,
            "approved S8P geometry contract summary is specified",
            str(path),
            "Create/transfer the approved s8p_geometry_contract_approval_summary.json before launching MARS.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _check(
            False,
            "approved S8P geometry contract summary is specified",
            f"{path}: {type(exc).__name__}: {exc}",
            "Regenerate the geometry-contract approval packet.",
        )
    contract = data.get("approved_geometry_contract") if isinstance(data, dict) else {}
    normalized_expected_pairs = _normalize_pair_text(expected_pairs or "")
    normalized_actual_pairs = _normalize_pair_text(_geometry_pair_text(contract.get("differential_pair_label_map") if isinstance(contract, dict) else []))
    checks = {
        "overall_status_is_pass": data.get("overall_status") == "PASS",
        "approval_status_is_approved": data.get("approval_status") == "APPROVED",
        "decision_allows_mars_emx_run": data.get("decision") == "GEOMETRY_CONTRACT_APPROVED_FOR_MARS_EMX_RUN",
        "bridge_width_is_10um": _float_eq(contract.get("bridge_width_um") if isinstance(contract, dict) else None, 10.0),
        "superseded_10nm_recorded": _float_eq(contract.get("superseded_literal_10nm_bridge_width_um") if isinstance(contract, dict) else None, 0.01),
        "vertical_reference_is_max_height": (contract.get("vertical_length_reference_dimension") if isinstance(contract, dict) else None)
        == "max(primary_outer_height_um, secondary_outer_height_um)",
        "ground_frame_width_is_100um": _float_eq(contract.get("ground_frame_width_um") if isinstance(contract, dict) else None, 100.0),
        "port_pairs_match_cli_pairs": bool(normalized_expected_pairs)
        and normalized_actual_pairs == normalized_expected_pairs,
    }
    return _check(
        all(checks.values()),
        "approved S8P geometry contract summary is specified",
        json.dumps(
            {
                "path": str(path),
                "overall_status": data.get("overall_status"),
                "approval_status": data.get("approval_status"),
                "decision": data.get("decision"),
                "checks": checks,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "Use only a user/advisor-approved geometry-contract summary matching the S8P bridge, ground-frame, vertical-length, and pair conventions.",
    )


def _geometry_pair_text(pair_map: Any) -> str:
    if not isinstance(pair_map, list):
        return ""
    parts: list[str] = []
    for item in pair_map:
        ports = item.get("ports") if isinstance(item, dict) else None
        if not isinstance(ports, list) or len(ports) != 2:
            return ""
        parts.append(f"{int(ports[0])},{int(ports[1])}")
    return ":".join(parts)


def _float_eq(actual: Any, expected: float, tolerance: float = 1.0e-12) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return False


def _normalize_pair_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def _split_csv(text: str | None) -> list[str]:
    if not text:
        return []
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _split_assignments(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    assignments: dict[str, str] = {}
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        assignments[key.strip()] = value.strip()
    return assignments


def _placeholder(value: str) -> bool:
    return bool(re.search(r"\b(TODO|REPLACE|TBD|PLACEHOLDER|FILL)\b", str(value), flags=re.IGNORECASE))


def _check(passed: bool, name: str, detail: Any, next_action: str) -> Check:
    return Check("PASS" if passed else "FAIL", name, str(detail), next_action)


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
